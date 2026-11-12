from __future__ import annotations

import os
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest
from openpyxl import Workbook, load_workbook

from nitka.extraction.payroll import PayrollKind, extract_payroll


def _make_workbook(
    path: Path,
    kind: PayrollKind = "PAYROLL",
    *,
    changes: dict[str, object] | None = None,
    sheet: str | None = None,
) -> Path:
    book = Workbook()
    book.active.title = "Unrelated active sheet"
    if kind == "PAYROLL":
        name = "Ведомость"
        cells: dict[str, object] = {
            "A5": "Код статьи",
            "C5": "Сумма, руб.",
            "A6": "0201",
            "A7": "0202",
            "C6": 1240.75,
            "C7": "-40,50",
            "C8": "=SUM(C6:C7)",
        }
    else:
        name = "Расчет взносов"
        cells = {
            "A5": "База начислений, руб.",
            "B5": "Начислено взносов, руб.",
            "C5": "Код статьи",
            "A6": 400,
            "B6": 122.54,
            "C6": "0300",
        }
    cells.update(
        {"A2": " Документ : НОВ-11 ", "A3": "Проект: 51/44-07 | Период: Ноябрь 2029"}
    )
    cells.update(changes or {})
    page = book.create_sheet(sheet or name)
    for address, value in cells.items():
        if value is not None:
            page[address] = value
    book.save(path)
    book.close()
    return path


def test_payroll_uses_details_and_preserves_requisite_evidence(tmp_path: Path) -> None:
    path = _make_workbook(tmp_path / "renamed.xlsx")
    result = extract_payroll(path)

    assert result.kind == "PAYROLL"
    assert not result.issues
    assert result.monthly_amount == Decimal("1200.25")
    assert {key: cell.value for key, cell in result.requisites.items()} == {
        "document_number": "НОВ-11",
        "project": "51/44-07",
        "period": "2029-11",
    }
    assert result.requisites["document_number"].raw_text == " Документ : НОВ-11 "
    assert result.requisites["document_number"].location.cell == "A2"
    assert (
        result.requisites["project"].raw_text
        == "Проект: 51/44-07 | Период: Ноябрь 2029"
    )
    assert result.requisites["period"].location.cell == "A3"
    assert {
        code: (cell.location.sheet, cell.location.cell)
        for code, cell in result.amounts.items()
    } == {
        "0201": ("Ведомость", "C6"),
        "0202": ("Ведомость", "C7"),
    }
    assert result.amounts["0202"].raw_text == "-40,50"
    assert result.reported_total is not None
    assert result.reported_total.location.cell == "C8"
    assert result.reported_total.raw_text == "=SUM(C6:C7)"
    assert result.reported_total.value is None


def test_contributions_use_printed_b6_and_keep_base_as_evidence(tmp_path: Path) -> None:
    path = _make_workbook(tmp_path / "renamed.xlsx", "CONTRIBUTIONS")
    result = extract_payroll(path)

    assert result.kind == "CONTRIBUTIONS"
    assert not result.issues
    assert result.monthly_amount == Decimal("122.54")
    assert list(result.amounts) == ["0300"]
    assert result.amounts["0300"].location.cell == "B6"
    assert result.contribution_base is not None
    assert result.contribution_base.value == "400"
    assert result.contribution_base.location.cell == "A6"


@pytest.mark.parametrize("kind", ["PAYROLL", "CONTRIBUTIONS"])
def test_numeric_article_codes_with_excel_padding(
    tmp_path: Path, kind: PayrollKind
) -> None:
    if kind == "PAYROLL":
        name, codes, total = "Ведомость", {"A6": 201, "A7": 202}, "1200.25"
    else:
        name, codes, total = "Расчет взносов", {"C6": 300}, "122.54"
    path = _make_workbook(tmp_path / "numeric-codes.xlsx", kind)
    book = load_workbook(path)
    for address, code in codes.items():
        cell = book[name][address]
        cell.value = code
        cell.number_format = "0000"
    book.save(path)
    book.close()

    result = extract_payroll(path)
    assert not result.issues
    assert result.monthly_amount == Decimal(total)


@pytest.mark.parametrize("cached", [False, True], ids=["literal", "formula-cache"])
def test_conflicting_reported_total_does_not_replace_details(
    tmp_path: Path, cached: bool
) -> None:
    path = _make_workbook(tmp_path / "pay.xlsx", changes={} if cached else {"C8": 9999})
    if cached:
        _set_total_cache(path, "9999")

    result = extract_payroll(path)

    assert result.monthly_amount == Decimal("1200.25")
    assert result.reported_total is not None
    assert result.reported_total.value == "9999"
    assert any("C8" in issue and "differs" in issue for issue in result.issues)


def _set_total_cache(path: Path, value: str) -> None:
    def update_cache(sheet: ElementTree.Element) -> None:
        cache = sheet.find(".//{*}c[@r='C8']/{*}v")
        assert cache is not None and cache.text is None
        cache.text = value

    _edit_worksheet_xml(path, update_cache)


def _edit_worksheet_xml(
    path: Path, edit: Callable[[ElementTree.Element], None]
) -> None:
    with ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    worksheet = "xl/worksheets/sheet2.xml"
    sheet_xml = ElementTree.fromstring(members[worksheet])
    edit(sheet_xml)
    members[worksheet] = ElementTree.tostring(sheet_xml)
    with ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)


@pytest.mark.parametrize("missing", [True, False], ids=["absent-row", "invalid-cache"])
def test_unreadable_or_missing_total_preserves_details(
    tmp_path: Path, missing: bool
) -> None:
    path = _make_workbook(
        tmp_path / "pay.xlsx", changes={"C8": None} if missing else {}
    )
    if not missing:
        _set_total_cache(path, "invalid")

    result = extract_payroll(path)
    assert result.monthly_amount == Decimal("1200.25")
    assert result.reported_total is not None
    assert result.reported_total.value is None
    assert any("C8" in issue for issue in result.issues)


@pytest.mark.parametrize("bad_amount", [None, True, "#DIV/0!", "=5+10", "unknown"])
def test_invalid_payroll_detail_prevents_partial_sum(
    tmp_path: Path, bad_amount: object
) -> None:
    path = _make_workbook(tmp_path / "pay.xlsx", changes={"C7": bad_amount, "C8": 900})
    result = extract_payroll(path)

    assert result.monthly_amount is None
    assert result.amounts["0201"].value == "1240.75"
    assert result.amounts["0202"].value is None
    assert any("C7" in issue for issue in result.issues)


def test_missing_assessed_contributions_cannot_be_inferred(tmp_path: Path) -> None:
    path = _make_workbook(
        tmp_path / "contributions.xlsx", "CONTRIBUTIONS", changes={"B6": None}
    )
    result = extract_payroll(path)

    assert result.monthly_amount is None
    assert result.amounts["0300"].value is None
    assert any("B6" in issue for issue in result.issues)


@pytest.mark.parametrize("address", ["A5", "A6", "A7", "C5"])
def test_mismatched_payroll_form_does_not_publish_amounts(
    tmp_path: Path, address: str
) -> None:
    path = _make_workbook(tmp_path / "pay.xlsx", changes={address: "different"})
    result = extract_payroll(path)

    assert result.monthly_amount is None
    assert not result.amounts
    assert any(address in issue for issue in result.issues)


def test_preserves_partial_requisites_and_zero_amount(tmp_path: Path) -> None:
    path = _make_workbook(
        tmp_path / "pay.xlsx",
        changes={"A2": None, "A3": "Проект: 88 | Период: неверный", "C6": 0, "C7": 0},
    )
    result = extract_payroll(path)

    assert result.monthly_amount == Decimal(0)
    assert result.requisites["document_number"].value is None
    assert result.requisites["project"].value == "88"
    assert result.requisites["period"].value is None
    assert result.requisites["period"].raw_text == "Проект: 88 | Период: неверный"
    assert len(result.issues) == 2


def test_can_select_a_renamed_form_sheet_explicitly(tmp_path: Path) -> None:
    path = _make_workbook(tmp_path / "pay.xlsx", sheet="Custom")

    assert extract_payroll(path).issues
    result = extract_payroll(path, kind="PAYROLL", sheet="Custom")
    assert result.monthly_amount == Decimal("1200.25")
    assert result.amounts["0201"].location.sheet == "Custom"


def test_ambiguous_form_names_require_explicit_selection(tmp_path: Path) -> None:
    path = _make_workbook(tmp_path / "pay.xlsx")
    book = load_workbook(path)
    book.create_sheet("Расчет взносов")
    book.save(path)
    book.close()

    assert extract_payroll(path).monthly_amount is None
    assert extract_payroll(path, kind="PAYROLL").monthly_amount == Decimal("1200.25")


def test_unreadable_workbook_produces_diagnostic(tmp_path: Path) -> None:
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"not an XLSX")

    result = extract_payroll(path)
    assert result.monthly_amount is None
    assert any("Unreadable XLSX" in issue for issue in result.issues)


def test_invalid_shared_string_reference_produces_diagnostic(tmp_path: Path) -> None:
    path = _make_workbook(tmp_path / "broken.xlsx")

    def corrupt_reference(sheet: ElementTree.Element) -> None:
        cell = sheet.find(".//{*}c[@r='C6']")
        assert cell is not None
        cell.set("t", "s")
        index = cell.find("{*}v")
        assert index is not None
        index.text = "400"

    _edit_worksheet_xml(path, corrupt_reference)

    result = extract_payroll(path)
    assert result.monthly_amount is None
    assert any("Unreadable XLSX" in issue for issue in result.issues)


def test_real_case_workbooks() -> None:
    root = os.environ.get("NITKA_PAYROLL_SMOKE_DIR")
    if not root:
        pytest.skip(
            "Set NITKA_PAYROLL_SMOKE_DIR to the directory with the ten real workbooks"
        )

    expected = {
        "02": ("1110000", "1117000", "1101800", "1120750", "1108924.83"),
        "03": ("333000", "335100", "330540", "337225", "335494.96"),
    }
    for prefix, values in expected.items():
        results = [
            extract_payroll(path)
            for path in sorted(Path(root).glob(f"{prefix}_*.xlsx"))
        ]
        assert [result.monthly_amount for result in results] == [
            Decimal(value) for value in values
        ]
        for month, result in enumerate(results, 1):
            assert not result.issues
            assert result.requisites["project"].value == "24/03/15-01"
            assert result.requisites["period"].value == f"2026-{month:02}"
            assert [cell.location.cell for cell in result.amounts.values()] == (
                ["C6", "C7"] if prefix == "02" else ["B6"]
            )
