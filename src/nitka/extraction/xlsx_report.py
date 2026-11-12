"""Read a report from XLSX according to an explicit, validated template.

Supported template (default in ``REPORT_TEMPLATE``)
----------------------------------------------------
* one named worksheet (``sheet``), never the active sheet;
* a header row (``header_row``) with exact headers for the amount column,
  every requisite column and the optional row-kind column;
* data starts at ``first_data_row``;
* the amount is a cell in ``amount_column``;
* requisites are cells in the configured field columns;
* a row is empty when every mapped cell is blank; empty rows are skipped;
* a row is a total when the explicit kind cell says so or when a mapped text
  cell matches ``labels`` (``Итого``/``Всего``/``Подытог``/``НДС`` …); the
  ``label_column`` is the primary place for that label;
* an unrecognized explicit kind becomes ``UNKNOWN`` with a
  ``ROW_KIND_UNKNOWN`` issue;
* otherwise a row with any mapped data is an item, even if its amount is
  missing. Unknown rows and items are preserved for manual review.

Template problems (missing sheet, missing or wrong header, unreadable
workbook, changed bytes) are returned as a ``FAILED`` file with an ``Issue``
whose code names the concrete reason. Invalid template *configuration* (for
example the amount column reused as a field) raises ``ValueError``.

Numeric cells
-------------
``raw_text`` for a numeric cell is ``str(value)`` as returned by openpyxl.
This is the stored numeric value, not a formatted Excel display: openpyxl does
not expose the number format here. A text cell keeps its text exactly, so
``0012`` stays ``0012`` for a requisite.

Formulas
--------
The workbook is opened twice: once with ``data_only=False`` (formulas) and once
with ``data_only=True`` (cached results). A formula in the amount column is
always sent to manual review. Without a cached value its ``value`` is ``None``
and an issue ``FORMULA_UNVERIFIED`` is emitted; with a cached value the cached
number is normalized, but the source still has ``review_status="NEEDS_REVIEW"``.

Collector usage
---------------
``InputRegistry.register`` is called once for the whole package. The collector
then calls ``XlsxReportExtractor(registry).extract(report_file_id)`` for the
report, obtains the support extraction separately, and merges the ``files``,
``sources``, ``rows`` and ``issues`` lists into the final ``ExtractionResult``
before passing it to the matcher/analysis. The ``Extractor`` protocol in
``extraction/ports.py`` remains the package-level pipeline hook.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from typing import Any
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from nitka.contracts.extraction import (
    ExtractedRow,
    ExtractionResult,
    FileStatus,
    Issue,
    Source,
)
from nitka.contracts.validation import validate_extraction
from nitka.extraction.limits import DEFAULT_XLSX_LIMITS, XlsxLimits
from nitka.extraction.registry import InputRegistry, RegisteredInput

from .xlsx_cells import _at, _text
from .xlsx_issues import AMOUNT_CELL_ERROR as AMOUNT_CELL_ERROR
from .xlsx_issues import AMOUNT_INVALID as AMOUNT_INVALID
from .xlsx_issues import AMOUNT_MISSING as AMOUNT_MISSING
from .xlsx_issues import EXTRACTION_VERSION as EXTRACTION_VERSION
from .xlsx_issues import FIELD_MISSING as FIELD_MISSING
from .xlsx_issues import FORMULA_CACHED as FORMULA_CACHED
from .xlsx_issues import FORMULA_UNVERIFIED as FORMULA_UNVERIFIED
from .xlsx_issues import INPUT_CHANGED as INPUT_CHANGED
from .xlsx_issues import INPUT_UNREADABLE as INPUT_UNREADABLE
from .xlsx_issues import ROW_KIND_UNKNOWN as ROW_KIND_UNKNOWN
from .xlsx_issues import TEMPLATE_HEADER_MISMATCH as TEMPLATE_HEADER_MISMATCH
from .xlsx_issues import TEMPLATE_HEADER_MISSING as TEMPLATE_HEADER_MISSING
from .xlsx_issues import TEMPLATE_SHEET_MISSING as TEMPLATE_SHEET_MISSING
from .xlsx_issues import WORKBOOK_UNREADABLE as WORKBOOK_UNREADABLE
from .xlsx_issues import XLSX_LIMITS_EXCEEDED
from .xlsx_rows import ReportRowParser
from .xlsx_template import DEFAULT_LABELS as DEFAULT_LABELS
from .xlsx_template import DEFAULT_REPORT_TEMPLATE as DEFAULT_REPORT_TEMPLATE
from .xlsx_template import REPORT_TEMPLATE as REPORT_TEMPLATE
from .xlsx_template import FieldSpec as FieldSpec
from .xlsx_template import ReportTemplate as ReportTemplate
from .xlsx_template import ReviewStatus as ReviewStatus
from .xlsx_template import RowKind as RowKind
from .xlsx_template import _column_index


class XlsxReportExtractor:
    """Extract one REPORT file from an input registry."""

    def __init__(
        self,
        registry: InputRegistry,
        template: ReportTemplate = REPORT_TEMPLATE,
        limits: XlsxLimits = DEFAULT_XLSX_LIMITS,
    ) -> None:
        self.registry = registry
        self.template = template
        self.limits = limits

    def extract(self, file_id: str) -> ExtractionResult:
        entry = _entry(self.registry, file_id)
        if entry.role != "REPORT":
            raise ValueError(
                f"XLSX report adapter requires a REPORT file, got {entry.role}"
            )
        try:
            data = entry.path.read_bytes()
        except OSError as exc:
            return self._failed(entry, INPUT_UNREADABLE, str(exc))
        if (
            len(data) != entry.size_bytes
            or hashlib.sha256(data).hexdigest() != entry.sha256
        ):
            return self._failed(
                entry,
                INPUT_CHANGED,
                "file bytes no longer match the input registry",
            )

        archive_size = _uncompressed_size(data)
        if (
            archive_size is not None
            and archive_size > self.limits.max_uncompressed_bytes
        ):
            return self._failed(
                entry,
                XLSX_LIMITS_EXCEEDED,
                f"uncompressed workbook is {archive_size} bytes, "
                f"limit is {self.limits.max_uncompressed_bytes}",
            )

        wb_formula: Any = None
        wb_values: Any = None
        try:
            try:
                wb_formula = load_workbook(
                    BytesIO(data), data_only=False, read_only=True
                )
                wb_values = load_workbook(BytesIO(data), data_only=True, read_only=True)
            except (
                InvalidFileException,
                BadZipFile,
                OSError,
                ValueError,
                KeyError,
            ) as exc:
                return self._failed(entry, WORKBOOK_UNREADABLE, str(exc))
            return self._extract(entry, wb_formula, wb_values)
        except ParseError as exc:
            # Read-only worksheets parse XML lazily as rows are consumed.
            return self._failed(entry, WORKBOOK_UNREADABLE, str(exc))
        finally:
            if wb_formula is not None:
                wb_formula.close()
            if wb_values is not None:
                wb_values.close()

    def _extract(
        self, entry: RegisteredInput, wb_formula: Any, wb_values: Any
    ) -> ExtractionResult:
        template = self.template
        row_parser = ReportRowParser(template)
        if template.sheet not in wb_formula.sheetnames:
            return self._failed(
                entry,
                TEMPLATE_SHEET_MISSING,
                f"sheet {template.sheet!r} not found; available: "
                f"{', '.join(wb_formula.sheetnames)}",
            )

        ws_formula = wb_formula[template.sheet]
        ws_values = wb_values[template.sheet]
        # Worksheet dimensions can be missing or stale. Check the used
        # rectangle as openpyxl reads rows, including gaps in sparse sheets.
        ws_formula.reset_dimensions()
        ws_values.reset_dimensions()
        formula_rows = ws_formula.iter_rows(min_row=1, values_only=True)
        value_rows = ws_values.iter_rows(min_row=1, values_only=True)

        sources: list[Source] = []
        rows: list[ExtractedRow] = []
        issues: list[Issue] = []
        row_number = 0
        max_column = 0
        for row_number, (formula_row, value_row) in enumerate(
            zip(formula_rows, value_rows, strict=True), start=1
        ):
            max_column = max(max_column, len(formula_row))
            if (
                row_number > self.limits.max_rows
                or max_column > self.limits.max_columns
                or row_number * max_column > self.limits.max_cells
            ):
                return self._failed(
                    entry,
                    XLSX_LIMITS_EXCEEDED,
                    f"sheet {template.sheet!r} reaches row {row_number} and "
                    f"column {max_column}; limits are {self.limits.max_rows} rows, "
                    f"{self.limits.max_columns} columns, "
                    f"{self.limits.max_cells} cells",
                )
            if row_number == template.header_row:
                header_issues = self._header_issues(entry, formula_row)
                if header_issues:
                    return self._result(entry, "FAILED", [], [], header_issues)
            if row_number < template.first_data_row:
                continue

            parsed = row_parser.parse(entry, row_number, formula_row, value_row)
            if parsed is not None:
                rows.append(parsed.row)
                sources.extend(parsed.sources)
                issues.extend(parsed.issues)

        if row_number < template.header_row:
            return self._failed(
                entry,
                TEMPLATE_HEADER_MISSING,
                f"{template.sheet}: header row {template.header_row} "
                "is outside the used range",
            )
        status: FileStatus = "PARTIAL" if issues else "COMPLETED"
        return self._result(entry, status, sources, rows, issues)

    def _header_issues(
        self, entry: RegisteredInput, header: tuple[Any, ...]
    ) -> list[Issue]:
        template = self.template
        checks: list[tuple[str, str]] = [
            (template.amount_column, template.amount_header),
            *[(field.column, field.header) for field in template.fields],
        ]
        if template.kind_column is not None and template.kind_header is not None:
            checks.append((template.kind_column, template.kind_header))

        issues: list[Issue] = []
        for column, expected in checks:
            actual = _text(_at(header, _column_index(column)))
            if actual is None or not actual.strip():
                issues.append(
                    Issue(
                        code=TEMPLATE_HEADER_MISSING,
                        file_id=entry.file_id,
                        detail=(
                            f"{template.sheet}!{column.strip().upper()}"
                            f"{template.header_row}: expected header {expected!r}"
                        ),
                    )
                )
            elif actual.strip().casefold() != expected.strip().casefold():
                issues.append(
                    Issue(
                        code=TEMPLATE_HEADER_MISMATCH,
                        file_id=entry.file_id,
                        detail=(
                            f"{template.sheet}!{column.strip().upper()}"
                            f"{template.header_row}: expected {expected!r}, "
                            f"got {actual!r}"
                        ),
                    )
                )
        return issues

    def _failed(
        self, entry: RegisteredInput, code: str, detail: str
    ) -> ExtractionResult:
        return self._result(
            entry,
            "FAILED",
            [],
            [],
            [Issue(code=code, file_id=entry.file_id, detail=detail)],
        )

    def _result(
        self,
        entry: RegisteredInput,
        status: FileStatus,
        sources: list[Source],
        rows: list[ExtractedRow],
        issues: list[Issue],
    ) -> ExtractionResult:
        result = ExtractionResult(
            extraction_version=EXTRACTION_VERSION,
            files=[entry.to_file_record(status)],
            sources=sources,
            rows=rows,
            issues=issues,
        )
        validate_extraction(result)
        return result


def extract_xlsx_report(
    registry: InputRegistry,
    file_id: str,
    *,
    template: ReportTemplate = REPORT_TEMPLATE,
) -> ExtractionResult:
    """Convenience wrapper for one report file."""
    return XlsxReportExtractor(registry, template).extract(file_id)


def _entry(registry: InputRegistry, file_id: str) -> RegisteredInput:
    for entry in registry.inputs:
        if entry.file_id == file_id:
            return entry
    raise KeyError(f"Unknown file ID: {file_id}")


def _uncompressed_size(data: bytes) -> int | None:
    try:
        with ZipFile(BytesIO(data)) as archive:
            return sum(info.file_size for info in archive.infolist())
    except (BadZipFile, OSError, ValueError):
        return None
