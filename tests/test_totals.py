from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook, load_workbook

from nitka.contracts.analysis import Scope
from nitka.contracts.extraction import CellLocation, ExtractionResult
from nitka.extraction.ports import InputDocument
from nitka.extraction.registry import InputRegistry
from nitka.extraction.xlsx_issues import FORMULA_CACHED, FORMULA_UNVERIFIED
from nitka.extraction.xlsx_report import XlsxReportExtractor
from nitka.extraction.xlsx_template import FieldSpec, ReportTemplate
from nitka.pipeline import analyze_extraction

SCOPE = Scope(currency="RUB", amount_basis="GROSS", reporting_period="2026-10")


def _write_report(path: Path, total: object) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Report"
    for cell, value in {
        "A1": "Договор",
        "B1": "Наименование",
        "D1": "Сумма",
    }.items():
        sheet[cell] = value
    sheet["A2"] = "0012"
    sheet["B2"] = "Услуги"
    sheet["D2"] = "100,00"
    sheet["A3"] = "0013"
    sheet["B3"] = "Материалы"
    sheet["D3"] = "200,00"
    sheet["B4"] = "Итого"
    sheet["D4"] = total
    workbook.save(path)
    workbook.close()


def _extract(path: Path) -> ExtractionResult:
    registry = InputRegistry.register([InputDocument(path, "REPORT")])
    return XlsxReportExtractor(registry).extract("F-1")


def test_wrong_total_is_detected_by_summing_items(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path, 400)

    extraction = _extract(path)
    analysis = analyze_extraction(extraction, [], SCOPE)

    total = next(check for check in analysis.checks if check.rule_id == "REPORT_TOTAL")
    assert total.state == "FAIL"
    assert total.reason_code == "TOTAL_EXCEEDS_ITEMS"
    assert total.calculation is not None
    assert total.calculation.actual == "400"
    assert total.calculation.expected == "300.00"
    assert total.calculation.delta == "100.00"
    assert total.evidence_ids == [
        "S-F-1-Report-D4",
        "S-F-1-Report-D2",
        "S-F-1-Report-D3",
    ]
    assert analysis.decision == "DISCREPANCIES_FOUND"


def test_correct_total_is_independent_of_matches(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path, 300)

    extraction = _extract(path)
    analysis = analyze_extraction(extraction, [], SCOPE)

    total = next(check for check in analysis.checks if check.rule_id == "REPORT_TOTAL")
    assert total.state == "PASS"
    assert total.reason_code == "TOTAL_MATCHES_ITEMS"
    assert total.calculation is not None
    assert total.calculation.delta == "0.00"
    # Item checks have no accepted matches, so the package still needs review.
    assert analysis.decision == "REVIEW_REQUIRED"


def test_page_subtotal_is_not_guessed_without_page_boundary(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Report"
    sheet["A1"] = "Договор"
    sheet["B1"] = "Наименование"
    sheet["D1"] = "Сумма"
    sheet["A2"] = "0012"
    sheet["B2"] = "Услуги"
    sheet["D2"] = "100,00"
    sheet["A3"] = "0013"
    sheet["B3"] = "Материалы"
    sheet["D3"] = "200,00"
    sheet["B4"] = "Подытог"
    sheet["D4"] = "250,00"
    sheet["A5"] = "0014"
    sheet["B5"] = "Доставка"
    sheet["D5"] = "50,00"
    sheet["B6"] = "Итого"
    sheet["D6"] = "350,00"
    workbook.save(path)
    workbook.close()

    extraction = _extract(path)
    analysis = analyze_extraction(extraction, [], SCOPE)

    total_checks = [
        check for check in analysis.checks if check.rule_id == "REPORT_TOTAL"
    ]
    assert [check.state for check in total_checks] == ["NOT_RUN", "PASS"]
    assert total_checks[0].reason_code == "PAGE_BOUNDARY_UNKNOWN"
    assert total_checks[0].calculation is None
    assert total_checks[1].calculation is not None
    assert total_checks[1].calculation.delta == "0.00"


def test_formula_total_without_cache_is_unverified(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path, "=SUM(D2:D3)")

    extraction = _extract(path)

    assert FORMULA_UNVERIFIED in [issue.code for issue in extraction.issues]
    total_source = next(
        source
        for source in extraction.sources
        if source.location == CellLocation(kind="CELL", sheet="Report", cell="D4")
    )
    assert total_source.raw_text == "=SUM(D2:D3)"
    assert total_source.value is None
    assert total_source.review_status == "NEEDS_REVIEW"

    analysis = analyze_extraction(extraction, [], SCOPE)
    total = next(check for check in analysis.checks if check.rule_id == "REPORT_TOTAL")
    assert total.state == "NOT_RUN"
    assert total.reason_code == "FORMULA_UNVERIFIED"
    assert total.calculation is None


def test_invalid_total_is_not_reported_as_missing(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path, "abc")

    extraction = _extract(path)
    analysis = analyze_extraction(extraction, [], SCOPE)

    total = next(check for check in analysis.checks if check.rule_id == "REPORT_TOTAL")
    assert total.state == "NOT_RUN"
    assert total.reason_code == "TOTAL_INVALID"
    assert total.calculation is None


def test_invalid_item_makes_total_unverifiable(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path, 300)
    # Re-open and corrupt one item while keeping the total valid.
    workbook = load_workbook(path)
    workbook["Report"]["D3"] = "abc"
    workbook.save(path)
    workbook.close()

    extraction = _extract(path)
    analysis = analyze_extraction(extraction, [], SCOPE)

    total = next(check for check in analysis.checks if check.rule_id == "REPORT_TOTAL")
    assert total.state == "NOT_RUN"
    assert total.reason_code == "ITEM_INVALID"
    assert total.calculation is None


def test_unknown_row_blocks_the_document_total(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Report"
    for cell, value in {
        "A1": "Договор",
        "B1": "Наименование",
        "D1": "Сумма",
        "E1": "Тип",
    }.items():
        sheet[cell] = value
    sheet["A2"] = "0012"
    sheet["B2"] = "Услуги"
    sheet["D2"] = "100,00"
    sheet["E2"] = "ITEM"
    sheet["A3"] = "0012"
    sheet["B3"] = "Неясная строка"
    sheet["D3"] = "50,00"
    sheet["E3"] = "???"
    sheet["B4"] = "Итого"
    sheet["D4"] = "100,00"
    sheet["E4"] = "DOCUMENT_TOTAL"
    workbook.save(path)
    workbook.close()
    template = ReportTemplate(
        sheet="Report",
        header_row=1,
        first_data_row=2,
        amount_column="D",
        amount_header="Сумма",
        fields=(
            FieldSpec("contract", "A", "Договор"),
            FieldSpec("name", "B", "Наименование"),
        ),
        label_column="B",
        kind_column="E",
        kind_header="Тип",
    )
    registry = InputRegistry.register([InputDocument(path, "REPORT")])
    extraction = XlsxReportExtractor(registry, template).extract("F-1")

    analysis = analyze_extraction(extraction, [], SCOPE)

    total = next(check for check in analysis.checks if check.rule_id == "REPORT_TOTAL")
    assert total.state == "NOT_RUN"
    assert total.reason_code == "UNKNOWN_ROW_KIND"
    assert total.calculation is None


def test_blank_currency_blocks_the_total(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path, 300)
    extraction = _extract(path)

    analysis = analyze_extraction(
        extraction,
        [],
        Scope(currency="", amount_basis="GROSS", reporting_period="2026-10"),
    )

    total = next(check for check in analysis.checks if check.rule_id == "REPORT_TOTAL")
    assert total.state == "NOT_RUN"
    assert total.reason_code == "SCOPE_CURRENCY_MISSING"
    assert total.calculation is None


def test_cached_formula_total_is_still_sent_to_review(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path, "=SUM(D2:D3)")
    _inject_cached_value(path, "D4", "300")

    extraction = _extract(path)

    assert FORMULA_CACHED in [issue.code for issue in extraction.issues]
    total_source = next(
        source
        for source in extraction.sources
        if source.location == CellLocation(kind="CELL", sheet="Report", cell="D4")
    )
    assert total_source.value == "300"
    assert total_source.review_status == "NEEDS_REVIEW"

    analysis = analyze_extraction(extraction, [], SCOPE)
    total = next(check for check in analysis.checks if check.rule_id == "REPORT_TOTAL")
    assert total.state == "NOT_RUN"
    assert total.reason_code == "TOTAL_NEEDS_REVIEW"
    assert total.calculation is None


def _inject_cached_value(path: Path, cell: str, cached: str) -> None:
    with ZipFile(path) as source:
        entries = [(info, source.read(info.filename)) for info in source.infolist()]
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as target:
        for info, content in entries:
            if info.filename == "xl/worksheets/sheet1.xml":
                text = content.decode()
                start = text.index(f'<c r="{cell}">')
                end = text.index("</c>", start) + len("</c>")
                cell_xml = text[start:end]
                if re.search(r"<v\s*/>", cell_xml):
                    cell_xml = re.sub(r"<v\s*/>", f"<v>{cached}</v>", cell_xml, count=1)
                else:
                    cell_xml = cell_xml.replace("</f>", f"</f><v>{cached}</v>", 1)
                text = text[:start] + cell_xml + text[end:]
                content = text.encode()
            target.writestr(info, content)
    path.write_bytes(output.getvalue())
