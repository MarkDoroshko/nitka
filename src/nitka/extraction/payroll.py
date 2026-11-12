"""Extract one payroll statement or insurance contribution workbook."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Context, Decimal
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from nitka.amounts import parse_amount
from nitka.contracts.extraction import CellLocation
from nitka.extraction.limits import DEFAULT_XLSX_LIMITS

PayrollKind = Literal["PAYROLL", "CONTRIBUTIONS"]

_SHEETS: dict[PayrollKind, str] = {
    "PAYROLL": "Ведомость",
    "CONTRIBUTIONS": "Расчет взносов",
}
_PAYROLL_FORM = {"A5": "Код статьи", "C5": "Сумма, руб.", "A6": "0201", "A7": "0202"}
_CONTRIBUTIONS_FORM = {
    "B5": "Начислено взносов, руб.",
    "C5": "Код статьи",
    "C6": "0300",
}
_DETAIL_SUM = re.compile(r"=\s*SUM\s*\(\s*\$?C\$?6\s*:\s*\$?C\$?7\s*\)", re.I)
_MONTHS = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)
_WORKBOOK_ERRORS = (
    OSError,
    BadZipFile,
    InvalidFileException,
    ValueError,
    LookupError,
    ParseError,
    SyntaxError,
)


@dataclass(frozen=True)
class PayrollCell:
    location: CellLocation
    raw_text: str | None
    value: str | None


@dataclass(frozen=True)
class PayrollResult:
    kind: PayrollKind | None = None
    # Requisite names are document_number, project and period (YYYY-MM).
    requisites: dict[str, PayrollCell] = field(default_factory=dict)
    # Only these cells contribute to monthly_amount, keyed by article code.
    amounts: dict[str, PayrollCell] = field(default_factory=dict)
    monthly_amount: Decimal | None = None
    reported_total: PayrollCell | None = None
    contribution_base: PayrollCell | None = None
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class _WorkbookCell:
    value: object = None
    formula: bool = False

    @property
    def text(self) -> str:
        return "" if self.value is None else str(self.value)


def extract_payroll(
    path: Path, *, kind: PayrollKind | None = None, sheet: str | None = None
) -> PayrollResult:
    """Read fixed forms by worksheet name. An explicit sheet requires a form kind.

    Required amounts come from literal cells. A printed total or its formula
    cache is comparison evidence. Contract and month come from the workbook.
    """
    if sheet is not None and kind is None:
        raise ValueError("Selecting a worksheet requires a payroll form kind")

    try:
        data = path.read_bytes()
        with ZipFile(BytesIO(data)) as archive:
            size = sum(entry.file_size for entry in archive.infolist())
        if size > DEFAULT_XLSX_LIMITS.max_uncompressed_bytes:
            return PayrollResult(
                kind=kind, issues=("XLSX expanded size exceeds limit",)
            )

        workbook = load_workbook(BytesIO(data), read_only=True, keep_links=False)
        try:
            worksheets = {
                worksheet.title: worksheet for worksheet in workbook.worksheets
            }
            if kind is None:
                matches = [form for form, name in _SHEETS.items() if name in worksheets]
                if len(matches) != 1:
                    return PayrollResult(
                        issues=("Expected exactly one payroll form worksheet",)
                    )
                kind = matches[0]
            name = _SHEETS[kind] if sheet is None else sheet
            if name not in worksheets:
                return PayrollResult(
                    kind=kind, issues=(f"Worksheet {name!r} is missing",)
                )
            max_row = 8 if kind == "PAYROLL" else 6
            cells = _read_cells(worksheets[name], max_row)
        finally:
            workbook.close()

        return _parse_form(data, kind, name, cells)
    except _WORKBOOK_ERRORS as exc:
        return PayrollResult(kind=kind, issues=(f"Unreadable XLSX: {exc}",))


def _read_cells(worksheet: Any, max_row: int) -> dict[str, _WorkbookCell]:
    # The read-only iterator may omit empty rows at the end of a worksheet.
    cells = dict.fromkeys(
        (f"{column}{row}" for row in range(1, max_row + 1) for column in "ABC"),
        _WorkbookCell(),
    )
    rows = worksheet.iter_rows(min_row=1, max_row=max_row, min_col=1, max_col=3)
    for row_number, row in enumerate(rows, 1):
        for column, cell in zip("ABC", row, strict=True):
            value = cell.value
            formula = cell.data_type == "f"
            if formula and not isinstance(value, str):
                value = getattr(value, "text", None)
            cells[f"{column}{row_number}"] = _WorkbookCell(value, formula)
    return cells


def _parse_form(
    data: bytes, kind: PayrollKind, sheet: str, cells: dict[str, _WorkbookCell]
) -> PayrollResult:
    issues: list[str] = []
    requisites = _read_requisites(sheet, cells, issues)
    form = _PAYROLL_FORM if kind == "PAYROLL" else _CONTRIBUTIONS_FORM
    mismatches = [
        f"{sheet}!{address}: expected {label!r}"
        for address, label in form.items()
        if not _matches_form_label(cells[address], label)
    ]
    if mismatches:
        return PayrollResult(
            kind=kind, requisites=requisites, issues=tuple(issues + mismatches)
        )

    if kind == "CONTRIBUTIONS":
        assessed = _read_amount(sheet, "B6", cells["B6"], issues)
        base = None
        if _matches_form_label(cells["A5"], "База начислений, руб."):
            base = _read_amount(sheet, "A6", cells["A6"], [])
        return PayrollResult(
            kind=kind,
            requisites=requisites,
            amounts={"0300": assessed},
            monthly_amount=Decimal(assessed.value)
            if assessed.value is not None
            else None,
            contribution_base=base,
            issues=tuple(issues),
        )

    basic = _read_amount(sheet, "C6", cells["C6"], issues)
    additional = _read_amount(sheet, "C7", cells["C7"], issues)
    total = None
    if basic.value is not None and additional.value is not None:
        # Two amounts with at most 18 integer and 6 fractional digits fit in 28 digits.
        total = Context(prec=28).add(Decimal(basic.value), Decimal(additional.value))
    reported = _read_reported_total(data, sheet, cells["C8"], issues)
    if (
        total is not None
        and reported.value is not None
        and total != Decimal(reported.value)
    ):
        issues.append(f"{sheet}!C8: reported total differs from C6 + C7")
    return PayrollResult(
        kind=kind,
        requisites=requisites,
        amounts={"0201": basic, "0202": additional},
        monthly_amount=total,
        reported_total=reported,
        issues=tuple(issues),
    )


def _matches_form_label(cell: _WorkbookCell, expected: str) -> bool:
    if expected.isdecimal() and isinstance(cell.value, (int, float)):
        # Excel can store a zero-padded article code as an ordinary number.
        return cell.value == int(expected)
    return cell.text.strip().casefold() == expected.casefold()


def _read_requisites(
    sheet: str, cells: dict[str, _WorkbookCell], issues: list[str]
) -> dict[str, PayrollCell]:
    a2, a3 = cells["A2"].text, cells["A3"].text
    project, _, period = a3.partition("|")
    values = {
        "document_number": ("A2", a2, _labelled(a2, "Документ")),
        "project": ("A3", a3, _labelled(project, "Проект")),
        "period": ("A3", a3, _iso_month(_labelled(period, "Период"))),
    }
    result: dict[str, PayrollCell] = {}
    for key, (address, raw, value) in values.items():
        if value is None:
            issues.append(f"{sheet}!{address}: missing or invalid {key}")
        result[key] = _evidence(sheet, address, raw, value)
    return result


def _labelled(text: str, label: str) -> str | None:
    name, separator, value = text.partition(":")
    if separator and name.strip().casefold() == label.casefold():
        return value.strip() or None
    return None


def _iso_month(text: str | None) -> str | None:
    match = re.fullmatch(r"([а-я]+)\s+([0-9]{4})(?:\s+г\.?)?", (text or "").casefold())
    if match is None:
        return None
    month, year = match.groups()
    if month not in _MONTHS or year == "0000":
        return None
    return f"{year}-{_MONTHS.index(month) + 1:02}"


def _read_amount(
    sheet: str, address: str, cell: _WorkbookCell, issues: list[str]
) -> PayrollCell:
    if cell.formula:
        issues.append(f"{sheet}!{address}: expected a literal amount")
        return _evidence(sheet, address, cell.text, None)
    parsed = parse_amount(cell.value)
    if parsed.value is None:
        issues.append(f"{sheet}!{address}: {parsed.error or 'amount is empty'}")
    return _evidence(sheet, address, cell.text, parsed.value)


def _read_reported_total(
    data: bytes, sheet: str, cell: _WorkbookCell, issues: list[str]
) -> PayrollCell:
    raw = cell.text
    if not cell.formula:
        return _read_amount(sheet, "C8", cell, issues)
    if not _DETAIL_SUM.fullmatch(raw.strip()):
        issues.append(f"{sheet}!C8: expected SUM(C6:C7)")

    try:
        cached_book = load_workbook(
            BytesIO(data), data_only=True, read_only=True, keep_links=False
        )
        try:
            cached = cached_book[sheet]["C8"].value
        finally:
            cached_book.close()
    except _WORKBOOK_ERRORS as exc:
        issues.append(f"{sheet}!C8: unreadable cached total ({exc})")
        return _evidence(sheet, "C8", raw, None)
    parsed = parse_amount(cached)
    if cached is not None and parsed.value is None:
        issues.append(f"{sheet}!C8: invalid cached total ({parsed.error or 'empty'})")
    return _evidence(sheet, "C8", raw, parsed.value)


def _evidence(sheet: str, address: str, raw: str, value: str | None) -> PayrollCell:
    return PayrollCell(
        CellLocation(kind="CELL", sheet=sheet, cell=address), raw or None, value
    )
