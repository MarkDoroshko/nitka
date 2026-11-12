import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook

from nitka.contracts.extraction import ExtractionResult
from nitka.extraction.limits import XlsxLimits
from nitka.extraction.ports import InputDocument
from nitka.extraction.registry import InputRegistry
from nitka.extraction.xlsx_issues import XLSX_LIMITS_EXCEEDED
from nitka.extraction.xlsx_report import XlsxReportExtractor


def _write_report(path: Path, *, last_cell: str | None = None) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Report"
    sheet["A1"] = "Договор"
    sheet["B1"] = "Наименование"
    sheet["D1"] = "Сумма"
    sheet["A2"] = "0012"
    sheet["B2"] = "Услуги"
    sheet["D2"] = "150 000,00"
    if last_cell is not None:
        sheet[last_cell] = 2000
    workbook.save(path)
    workbook.close()


def _set_dimensions(path: Path, dimensions: str | None) -> None:
    with ZipFile(path) as source:
        entries = [(info, source.read(info)) for info in source.infolist()]
    with ZipFile(path, "w", ZIP_DEFLATED) as target:
        for info, content in entries:
            if info.filename == "xl/worksheets/sheet1.xml":
                replacement = (
                    f'<dimension ref="{dimensions}"/>'.encode() if dimensions else b""
                )
                content, replaced = re.subn(
                    rb"<dimension\b[^>]*/>", replacement, content
                )
                assert replaced == 1
            target.writestr(info, content)


def _extract(path: Path, limits: XlsxLimits) -> ExtractionResult:
    registry = InputRegistry.register([InputDocument(path, "REPORT")])
    return XlsxReportExtractor(registry, limits=limits).extract("F-1")


def test_uncompressed_workbook_limit_is_checked_before_parsing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path)

    result = _extract(path, XlsxLimits(max_uncompressed_bytes=1))

    assert result.files[0].status == "FAILED"
    assert [issue.code for issue in result.issues] == [XLSX_LIMITS_EXCEEDED]
    assert result.rows == []


def test_sheet_row_limit_rejects_large_sheet(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path)

    result = _extract(path, XlsxLimits(max_rows=1))

    assert result.files[0].status == "FAILED"
    assert XLSX_LIMITS_EXCEEDED in [issue.code for issue in result.issues]


def test_sheet_column_limit_rejects_large_sheet(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path)

    result = _extract(path, XlsxLimits(max_columns=1))

    assert result.files[0].status == "FAILED"
    assert XLSX_LIMITS_EXCEEDED in [issue.code for issue in result.issues]


def test_sheet_cell_limit_rejects_large_sheet(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path)

    result = _extract(path, XlsxLimits(max_cells=1))

    assert result.files[0].status == "FAILED"
    assert XLSX_LIMITS_EXCEEDED in [issue.code for issue in result.issues]


@pytest.mark.parametrize(
    "dimensions",
    [None, "A1", "A1:B2", "A1:XFD1048576"],
    ids=["missing", "single-cell", "undersized", "oversized"],
)
def test_reads_actual_rows_with_unreliable_dimensions(
    tmp_path: Path, dimensions: str | None
) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path, last_cell="D5")
    _set_dimensions(path, dimensions)

    result = _extract(path, XlsxLimits(max_rows=5, max_columns=4, max_cells=20))

    assert result.files[0].status == "PARTIAL"  # Row 5 has only an amount.
    assert [row.row_id for row in result.rows] == ["R-F-1-Report-2", "R-F-1-Report-5"]
    amounts = {source.source_id: source.value for source in result.sources}
    assert amounts[result.rows[1].amount_source_id] == "2000"


@pytest.mark.parametrize("dimensions", [None, "A1:D2"], ids=["missing", "undersized"])
@pytest.mark.parametrize(
    "limits",
    [XlsxLimits(max_rows=4), XlsxLimits(max_columns=25), XlsxLimits(max_cells=129)],
    ids=["row", "column", "cell"],
)
def test_actual_sheet_limits_apply_with_unreliable_dimensions(
    tmp_path: Path, dimensions: str | None, limits: XlsxLimits
) -> None:
    path = tmp_path / "report.xlsx"
    _write_report(path, last_cell="Z5")
    _set_dimensions(path, dimensions)

    result = _extract(path, limits)

    assert result.files[0].status == "FAILED"
    assert [issue.code for issue in result.issues] == [XLSX_LIMITS_EXCEEDED]
    assert result.rows == []
    assert result.sources == []
