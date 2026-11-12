from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook

from nitka.contracts.analysis import Scope
from nitka.contracts.extraction import CellLocation
from nitka.contracts.validation import validate_extraction
from nitka.extraction.csv_report import CsvReportExtractor
from nitka.extraction.ports import InputDocument
from nitka.extraction.registry import InputRegistry
from nitka.extraction.xlsx_issues import (
    AMOUNT_INVALID,
    AMOUNT_MISSING,
    TEMPLATE_HEADER_MISMATCH,
    TEMPLATE_HEADER_MISSING,
)
from nitka.extraction.xlsx_report import XlsxReportExtractor
from nitka.extraction.xlsx_template import FieldSpec, ReportTemplate
from nitka.pipeline import analyze_extraction

SCOPE = Scope(currency="RUB", amount_basis="GROSS", reporting_period="2026-10")
HEADERS = ["Договор", "Наименование", "", "Сумма"]
ROWS = [
    ["0012", "Услуги", "", "150 000,00"],
    ["0013", "Материалы", "", "2000"],
    ["", "Итого", "", "152000"],
]


def _write_csv(
    path: Path,
    rows: list[list[str]] = ROWS,
    *,
    headers: list[str] = HEADERS,
    delimiter: str = ",",
    encoding: str = "utf-8-sig",
) -> None:
    with path.open("w", encoding=encoding, newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter)
        writer.writerow(headers)
        writer.writerows(rows)


def _extract_csv(path: Path, delimiter: str = ","):  # type: ignore[no-untyped-def]
    registry = InputRegistry.register([InputDocument(path, "REPORT")])
    return CsvReportExtractor(registry, delimiter=delimiter).extract("F-1")


def _write_xlsx(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Report"
    for column, value in zip("ABD", ["Договор", "Наименование", "Сумма"]):
        sheet[f"{column}1"] = value
    for row, values in enumerate(ROWS, start=2):
        sheet[f"A{row}"] = values[0] or None
        sheet[f"B{row}"] = values[1] or None
        sheet[f"D{row}"] = values[3] or None
    workbook.save(path)
    workbook.close()


def test_csv_and_xlsx_produce_equivalent_positions(tmp_path: Path) -> None:
    csv_path = tmp_path / "report.csv"
    xlsx_path = tmp_path / "report.xlsx"
    _write_csv(csv_path)
    _write_xlsx(xlsx_path)

    csv_result = _extract_csv(csv_path)
    xlsx_result = XlsxReportExtractor(
        InputRegistry.register([InputDocument(xlsx_path, "REPORT")])
    ).extract("F-1")

    validate_extraction(csv_result)
    assert [row.kind for row in csv_result.rows] == [
        "ITEM",
        "ITEM",
        "DOCUMENT_TOTAL",
    ]
    assert [row.kind for row in xlsx_result.rows] == [
        "ITEM",
        "ITEM",
        "DOCUMENT_TOTAL",
    ]

    for csv_row, xlsx_row in zip(csv_result.rows, xlsx_result.rows):
        csv_amount = next(
            source
            for source in csv_result.sources
            if source.source_id == csv_row.amount_source_id
        )
        xlsx_amount = next(
            source
            for source in xlsx_result.sources
            if source.source_id == xlsx_row.amount_source_id
        )
        assert csv_amount.value == xlsx_amount.value
        assert csv_amount.raw_text == xlsx_amount.raw_text
        assert csv_amount.location.kind == "CELL"
        assert xlsx_amount.location.kind == "CELL"
        assert isinstance(csv_amount.location, CellLocation)
        assert isinstance(xlsx_amount.location, CellLocation)
        assert csv_amount.location.cell == xlsx_amount.location.cell
        assert csv_amount.location.sheet is None
        for field, csv_source_id in csv_row.fields.items():
            csv_field = next(
                source
                for source in csv_result.sources
                if source.source_id == csv_source_id
            )
            xlsx_field = next(
                source
                for source in xlsx_result.sources
                if source.source_id == xlsx_row.fields[field]
            )
            assert csv_field.value == xlsx_field.value
            assert isinstance(csv_field.location, CellLocation)
            assert isinstance(xlsx_field.location, CellLocation)
            assert csv_field.location.cell == xlsx_field.location.cell


def test_csv_semicolon_and_bom(tmp_path: Path) -> None:
    path = tmp_path / "report.csv"
    _write_csv(path, delimiter=";")

    result = _extract_csv(path, delimiter=";")

    assert result.files[0].status == "COMPLETED"
    assert len(result.rows) == 3


def test_csv_invalid_amount_is_kept_for_review(tmp_path: Path) -> None:
    path = tmp_path / "report.csv"
    _write_csv(
        path,
        [["0012", "Услуги", "", "abc"]],
    )

    result = _extract_csv(path)

    assert result.files[0].status == "PARTIAL"
    assert AMOUNT_INVALID in [issue.code for issue in result.issues]
    amount = next(
        source
        for source in result.sources
        if source.location == CellLocation(kind="CELL", sheet=None, cell="D2")
    )
    assert amount.value is None
    assert amount.review_status == "NEEDS_REVIEW"


def test_csv_empty_amount_is_kept_for_review(tmp_path: Path) -> None:
    path = tmp_path / "report.csv"
    _write_csv(path, [["0012", "Услуги", "", ""]])

    result = _extract_csv(path)

    assert result.files[0].status == "PARTIAL"
    assert AMOUNT_MISSING in [issue.code for issue in result.issues]


def test_csv_wrong_header_is_a_template_error(tmp_path: Path) -> None:
    path = tmp_path / "report.csv"
    _write_csv(path, headers=["Договор", "Наименование", "", "Итого"])

    result = _extract_csv(path)

    assert result.files[0].status == "FAILED"
    assert [issue.code for issue in result.issues] == [TEMPLATE_HEADER_MISMATCH]


def test_csv_missing_header_is_a_template_error(tmp_path: Path) -> None:
    path = tmp_path / "report.csv"
    _write_csv(path, headers=["Договор", "Наименование"])

    result = _extract_csv(path)

    assert result.files[0].status == "FAILED"
    assert [issue.code for issue in result.issues] == [TEMPLATE_HEADER_MISSING]


def test_csv_unreadable_encoding_is_diagnosed(tmp_path: Path) -> None:
    path = tmp_path / "report.csv"
    path.write_bytes(b"\xff\xfe\x00not utf-8")
    registry = InputRegistry.register([InputDocument(path, "REPORT")])

    result = CsvReportExtractor(registry, encoding="utf-8").extract("F-1")

    assert result.files[0].status == "FAILED"
    assert [issue.code for issue in result.issues] == ["CSV_UNREADABLE"]


def test_csv_total_is_checked_independently(tmp_path: Path) -> None:
    path = tmp_path / "report.csv"
    _write_csv(
        path,
        [
            ["0012", "Услуги", "", "100,00"],
            ["0013", "Материалы", "", "200,00"],
            ["", "Итого", "", "400,00"],
        ],
    )

    extraction = _extract_csv(path)
    analysis = analyze_extraction(extraction, [], SCOPE)

    total = next(check for check in analysis.checks if check.rule_id == "REPORT_TOTAL")
    assert total.state == "FAIL"
    assert total.reason_code == "TOTAL_EXCEEDS_ITEMS"
    assert total.calculation is not None
    assert total.calculation.delta == "100.00"
    assert analysis.decision == "DISCREPANCIES_FOUND"


def test_custom_real_style_template_is_shared_by_xlsx_and_csv(tmp_path: Path) -> None:
    template = ReportTemplate(
        sheet="Отчёт",
        header_row=2,
        first_data_row=3,
        amount_column="F",
        amount_header="Сумма с НДС",
        fields=(
            FieldSpec("contract", "B", "Договор"),
            FieldSpec("name", "C", "Контрагент"),
        ),
        label_column="C",
    )
    rows = [
        ["Отчёт за октябрь"],
        ["", "Договор", "Контрагент", "", "", "Сумма с НДС"],
        ["", "0012", "ООО Ромашка", "", "", "150 000,00"],
        ["", "", "Итого", "", "", "150 000,00"],
    ]
    csv_path = tmp_path / "report.csv"
    _write_csv(csv_path, rows, headers=[""], delimiter=",")
    # Rewrite the header/data rows because the helper writes one header row.
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)

    xlsx_path = tmp_path / "report.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Отчёт"
    for row_number, values in enumerate(rows, start=1):
        padded = [*values, *[""] * (6 - len(values))]
        for column, value in zip("ABCF", [padded[0], padded[1], padded[2], padded[5]]):
            if value:
                sheet[f"{column}{row_number}"] = value
    workbook.save(xlsx_path)
    workbook.close()

    csv_registry = InputRegistry.register([InputDocument(csv_path, "REPORT")])
    csv_result = CsvReportExtractor(csv_registry, template).extract("F-1")
    xlsx_registry = InputRegistry.register([InputDocument(xlsx_path, "REPORT")])
    xlsx_result = XlsxReportExtractor(xlsx_registry, template).extract("F-1")

    assert [row.kind for row in csv_result.rows] == [
        row.kind for row in xlsx_result.rows
    ]
    csv_amount = next(
        source
        for source in csv_result.sources
        if source.source_id == csv_result.rows[0].amount_source_id
    )
    xlsx_amount = next(
        source
        for source in xlsx_result.sources
        if source.source_id == xlsx_result.rows[0].amount_source_id
    )
    assert isinstance(csv_amount.location, CellLocation)
    assert isinstance(xlsx_amount.location, CellLocation)
    assert csv_amount.location.cell == "F3"
    assert xlsx_amount.location.cell == "F3"
    assert csv_amount.value == xlsx_amount.value == "150000.00"


def test_csv_blank_lines_and_multiline_fields_keep_logical_row_addresses(
    tmp_path: Path,
) -> None:
    path = tmp_path / "report.csv"
    _write_csv(
        path,
        [
            ["0012", "Услуги\nс доставкой", "", "100,00"],
            [],
            ["0013", "Материалы", "", "200,00"],
            ["", "Итого", "", "300,00"],
        ],
    )

    result = _extract_csv(path)

    assert [row.kind for row in result.rows] == [
        "ITEM",
        "ITEM",
        "DOCUMENT_TOTAL",
    ]
    addresses = [
        source.location.cell
        for source in result.sources
        if isinstance(source.location, CellLocation)
        and source.location.cell.startswith("D")
    ]
    assert addresses == ["D2", "D4", "D5"]
