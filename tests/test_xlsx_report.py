from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook

from nitka.contracts.analysis import Scope
from nitka.contracts.extraction import CellLocation, ExtractionResult
from nitka.contracts.validation import validate_extraction
from nitka.extraction.ports import InputDocument
from nitka.extraction.registry import InputRegistry
from nitka.extraction.xlsx_report import (
    AMOUNT_CELL_ERROR,
    AMOUNT_INVALID,
    AMOUNT_MISSING,
    FIELD_MISSING,
    FORMULA_CACHED,
    FORMULA_UNVERIFIED,
    INPUT_CHANGED,
    REPORT_TEMPLATE,
    ROW_KIND_UNKNOWN,
    TEMPLATE_HEADER_MISMATCH,
    TEMPLATE_HEADER_MISSING,
    TEMPLATE_SHEET_MISSING,
    WORKBOOK_UNREADABLE,
    FieldSpec,
    ReportTemplate,
    XlsxReportExtractor,
    extract_xlsx_report,
)
from nitka.pipeline import analyze_extraction

SCOPE = Scope(currency="RUB", amount_basis="GROSS", reporting_period="2026-10")
HEADERS = {"A1": "Договор", "B1": "Наименование", "D1": "Сумма"}
ROWS: list[dict[str, Any]] = [
    {"A": "0012", "B": "Услуги", "D": "150 000,00"},
    {"A": "0013", "B": "Материалы", "D": 2000},
    {"B": "Итого", "D": 152000},
]


def _write_report(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    headers: Mapping[str, Any] | None = None,
    sheet: str = "Report",
    extra_active_sheet: bool = False,
) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    for cell, value in (HEADERS if headers is None else headers).items():
        worksheet[cell] = value
    for row_number, values in enumerate(rows, start=2):
        for column, value in values.items():
            worksheet[f"{column}{row_number}"] = value
    if extra_active_sheet:
        other = workbook.create_sheet("Other")
        other["A1"] = "not the report"
        workbook.active = 1
    workbook.save(path)
    workbook.close()


def _registry(path: Path) -> InputRegistry:
    return InputRegistry.register([InputDocument(path, "REPORT")])


def _inject_cached_value(path: Path, cell: str, cached: str) -> None:
    """Add a cached result to a formula cell, as a real Excel file would."""
    with ZipFile(path) as source:
        entries = [
            (info, source.read(info.filename)) for info in source.infolist()
        ]
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as target:
        for info, content in entries:
            if info.filename == "xl/worksheets/sheet1.xml":
                text = content.decode()
                start = text.index(f'<c r="{cell}">')
                end = text.index("</c>", start) + len("</c>")
                cell_xml = text[start:end]
                if re.search(r"<v\s*/>", cell_xml):
                    cell_xml = re.sub(
                        r"<v\s*/>", f"<v>{cached}</v>", cell_xml, count=1
                    )
                else:
                    cell_xml = cell_xml.replace(
                        "</f>", f"</f><v>{cached}</v>", 1
                    )
                text = text[:start] + cell_xml + text[end:]
                content = text.encode()
            target.writestr(info, content)
    path.write_bytes(output.getvalue())


def _extract(
    path: Path, template: ReportTemplate = REPORT_TEMPLATE
) -> ExtractionResult:
    return XlsxReportExtractor(_registry(path), template).extract("F-1")


def test_reads_amount_and_requisites_from_cells(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path, ROWS)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    result = extract_xlsx_report(_registry(path), "F-1")

    assert result.files[0].file_id == "F-1"
    assert result.files[0].status == "COMPLETED"
    assert result.files[0].sha256 == digest
    assert result.issues == []
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest

    amount = next(
        source
        for source in result.sources
        if source.location == CellLocation(kind="CELL", sheet="Report", cell="D2")
    )
    assert amount.method == "TABLE"
    assert amount.raw_text == "150 000,00"
    assert amount.value == "150000.00"
    assert amount.review_status == "UNREVIEWED"

    contract_id = result.rows[0].fields["contract"]
    contract = next(
        source for source in result.sources if source.source_id == contract_id
    )
    assert contract.value == "0012"
    assert contract.raw_text == "0012"

    numeric = next(
        source
        for source in result.sources
        if source.location == CellLocation(kind="CELL", sheet="Report", cell="D3")
    )
    assert numeric.raw_text == "2000"
    assert numeric.value == "2000"

    assert [row.kind for row in result.rows] == [
        "ITEM",
        "ITEM",
        "DOCUMENT_TOTAL",
    ]
    validate_extraction(result)

    analysis = analyze_extraction(result, [], SCOPE)
    assert len(analysis.checks) == 3
    total = next(check for check in analysis.checks if check.rule_id == "REPORT_TOTAL")
    assert total.state == "PASS"
    assert total.calculation is not None
    assert total.calculation.delta == "0.00"
    assert total.evidence_ids == [
        "S-F-1-Report-D4",
        "S-F-1-Report-D2",
        "S-F-1-Report-D3",
    ]
    assert analysis.decision == "REVIEW_REQUIRED"


def test_source_and_row_ids_are_stable_across_reads(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path, ROWS)

    first = _extract(path)
    second = _extract(path)

    assert [source.source_id for source in first.sources] == [
        source.source_id for source in second.sources
    ]
    assert [row.row_id for row in first.rows] == [row.row_id for row in second.rows]


def test_active_sheet_and_unmapped_columns_do_not_define_the_report(
    tmp_path: Path,
) -> None:
    path = tmp_path / "report.xlsx"
    rows = [
        {"A": "0012", "B": "Услуги", "C": 999, "D": "100,00"},
    ]
    _write_report(path, rows, extra_active_sheet=True)

    result = _extract(path)

    assert result.files[0].status == "COMPLETED"
    amount = next(
        source
        for source in result.sources
        if source.location == CellLocation(kind="CELL", sheet="Report", cell="D2")
    )
    assert amount.value == "100.00"
    assert all(
        source.location.cell.startswith(("A", "B", "D"))
        for source in result.sources
        if isinstance(source.location, CellLocation)
    )


@pytest.mark.parametrize(
    ("row", "code", "raw"),
    [
        ({"A": "0012", "B": "Услуги"}, AMOUNT_MISSING, None),
        ({"A": "0012", "B": "Услуги", "D": "abc"}, AMOUNT_INVALID, "abc"),
        (
            {"A": "0012", "B": "Услуги", "D": "12 34,56"},
            AMOUNT_INVALID,
            "12 34,56",
        ),
        (
            {"A": "0012", "B": "Услуги", "D": "1" * 19},
            AMOUNT_INVALID,
            "1" * 19,
        ),
        (
            {"A": "0012", "B": "Услуги", "D": "=1+1"},
            FORMULA_UNVERIFIED,
            "=1+1",
        ),
        (
            {"A": "0012", "B": "Услуги", "D": "#VALUE!"},
            AMOUNT_CELL_ERROR,
            "#VALUE!",
        ),
    ],
)
def test_unreadable_amount_is_kept_for_manual_review(
    tmp_path: Path, row: dict[str, Any], code: str, raw: str | None
) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path, [row])

    result = _extract(path)

    assert result.files[0].status == "PARTIAL"
    assert len(result.rows) == 1
    assert code in [issue.code for issue in result.issues]
    amount = next(
        source
        for source in result.sources
        if source.location == CellLocation(kind="CELL", sheet="Report", cell="D2")
    )
    assert amount.value is None
    assert amount.review_status == "NEEDS_REVIEW"
    assert amount.raw_text == raw


def test_formula_with_cached_value_is_still_manual_review(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path, [{"A": "0012", "B": "Услуги", "D": "=1+1"}])
    _inject_cached_value(path, "D2", "2")

    result = _extract(path)

    assert result.files[0].status == "PARTIAL"
    assert FORMULA_CACHED in [issue.code for issue in result.issues]
    amount = next(
        source
        for source in result.sources
        if source.location == CellLocation(kind="CELL", sheet="Report", cell="D2")
    )
    assert amount.raw_text == "=1+1"
    assert amount.value == "2"
    assert amount.review_status == "NEEDS_REVIEW"


def test_empty_rows_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(
        path,
        [
            {"A": "0012", "B": "Услуги", "D": "100,00"},
            {},
            {"A": "0013", "B": "Материалы", "D": "200,00"},
        ],
    )

    result = _extract(path)

    assert len(result.rows) == 2
    assert [row.row_id for row in result.rows] == [
        "R-F-1-Report-2",
        "R-F-1-Report-4",
    ]


def test_missing_required_field_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path, [{"B": "Услуги", "D": "100,00"}])

    result = _extract(path)

    assert result.files[0].status == "PARTIAL"
    assert FIELD_MISSING in [issue.code for issue in result.issues]
    contract = next(
        source
        for source in result.sources
        if source.location == CellLocation(kind="CELL", sheet="Report", cell="A2")
    )
    assert contract.value is None
    assert contract.review_status == "NEEDS_REVIEW"


def test_explicit_kind_column_marks_unknown_row(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    template = replace(
        REPORT_TEMPLATE,
        kind_column="E",
        kind_header="Тип",
    )
    headers = {**HEADERS, "E1": "Тип"}
    _write_report(
        path,
        [
            {"A": "0012", "B": "Услуги", "D": 100, "E": "ITEM"},
            {"A": "0013", "B": "Материалы", "D": 200, "E": "???"},
            {"B": "Итого", "D": 300, "E": "DOCUMENT_TOTAL"},
        ],
        headers=headers,
    )

    result = _extract(path, template)

    assert [row.kind for row in result.rows] == [
        "ITEM",
        "UNKNOWN",
        "DOCUMENT_TOTAL",
    ]
    assert ROW_KIND_UNKNOWN in [issue.code for issue in result.issues]


def test_total_labels_choose_the_row_kind(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(
        path,
        [
            {"A": "0012", "B": "Услуги", "D": 100},
            {"B": "Подытог", "D": 100},
            {"B": "НДС", "D": 20},
            {"B": "Итого", "D": 120},
        ],
    )

    result = _extract(path)

    assert [row.kind for row in result.rows] == [
        "ITEM",
        "PAGE_SUBTOTAL",
        "TAX_SUMMARY",
        "DOCUMENT_TOTAL",
    ]
    assert result.files[0].status == "COMPLETED"


def test_header_only_report_is_complete_but_empty(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path, [])

    result = _extract(path)

    assert result.files[0].status == "COMPLETED"
    assert result.rows == []
    assert result.sources == []
    assert result.issues == []


def test_missing_sheet_is_a_template_error(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path, ROWS, sheet="Other")

    result = _extract(path)

    assert result.files[0].status == "FAILED"
    assert [issue.code for issue in result.issues] == [TEMPLATE_SHEET_MISSING]
    assert result.sources == []
    assert result.rows == []


def test_wrong_header_is_a_template_error(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path, ROWS, headers={**HEADERS, "D1": "Итого"})

    result = _extract(path)

    assert result.files[0].status == "FAILED"
    assert [issue.code for issue in result.issues] == [TEMPLATE_HEADER_MISMATCH]
    assert "D1" in result.issues[0].detail


def test_missing_header_is_a_template_error(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path, ROWS, headers={"A1": "Договор", "B1": "Наименование"})

    result = _extract(path)

    assert result.files[0].status == "FAILED"
    assert [issue.code for issue in result.issues] == [TEMPLATE_HEADER_MISSING]


def test_corrupt_workbook_reports_a_readable_reason(tmp_path: Path) -> None:
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"this is not a workbook")
    registry = _registry(path)

    result = extract_xlsx_report(registry, "F-1")

    assert result.files[0].status == "FAILED"
    assert [issue.code for issue in result.issues] == [WORKBOOK_UNREADABLE]
    assert result.files[0].sha256 == registry.inputs[0].sha256


@pytest.mark.parametrize("lazy", [False, True], ids=["opening", "reading-rows"])
def test_malformed_worksheet_xml_returns_failed(tmp_path: Path, lazy: bool) -> None:
    path = tmp_path / "broken-xml.xlsx"
    _write_report(path, ROWS)
    with ZipFile(path) as source:
        entries = [(info, source.read(info.filename)) for info in source.infolist()]
    with ZipFile(path, "w", ZIP_DEFLATED) as target:
        for info, content in entries:
            if info.filename == "xl/worksheets/sheet1.xml":
                content = (
                    content.replace(b'<row r="3">', b'<row r="3"><broken></mismatch>')
                    if lazy else b"<broken"
                )
            target.writestr(info, content)
    original = path.read_bytes()
    registry = _registry(path)

    result = extract_xlsx_report(registry, "F-1")

    assert result.files[0].status == "FAILED"
    assert [issue.code for issue in result.issues] == [WORKBOOK_UNREADABLE]
    assert result.rows == []
    assert result.sources == []
    assert result.files[0].sha256 == registry.inputs[0].sha256
    assert path.read_bytes() == original


def test_changed_file_bytes_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path, ROWS)
    registry = _registry(path)
    path.write_bytes(b"changed after registration")

    result = extract_xlsx_report(registry, "F-1")

    assert result.files[0].status == "FAILED"
    assert [issue.code for issue in result.issues] == [INPUT_CHANGED]


def test_template_rejects_amount_column_reused_as_field() -> None:
    with pytest.raises(ValueError, match="Amount column"):
        ReportTemplate(
            sheet="Report",
            header_row=1,
            first_data_row=2,
            amount_column="A",
            amount_header="Сумма",
            fields=(FieldSpec(name="contract", column="A", header="Договор"),),
        )
