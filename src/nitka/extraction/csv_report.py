"""Read the agreed report layout from a CSV file.

The same ``ReportTemplate`` describes headers, the amount column and requisite
columns as for XLSX. CSV has no worksheet, so each source uses a cell location
with ``sheet=None`` and an A1-style address (row and column), for example
``D2``.
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Sequence
from typing import Literal

from nitka.amounts import parse_amount
from nitka.contracts.extraction import (
    CellLocation,
    ExtractedRow,
    ExtractionResult,
    FileStatus,
    Issue,
    Source,
)
from nitka.contracts.validation import validate_extraction
from nitka.extraction.registry import InputRegistry, RegisteredInput

from .xlsx_cells import _at, _is_blank, _text
from .xlsx_issues import (
    AMOUNT_INVALID,
    AMOUNT_MISSING,
    FIELD_MISSING,
    INPUT_CHANGED,
    INPUT_UNREADABLE,
    ROW_KIND_UNKNOWN,
    TEMPLATE_HEADER_MISMATCH,
    TEMPLATE_HEADER_MISSING,
)
from .xlsx_template import (
    _KINDS,
    _column_index,
    _normalize_label,
)
from .xlsx_template import DEFAULT_LABELS as DEFAULT_LABELS
from .xlsx_template import REPORT_TEMPLATE as REPORT_TEMPLATE
from .xlsx_template import FieldSpec as FieldSpec
from .xlsx_template import ReportTemplate as ReportTemplate
from .xlsx_template import RowKind as RowKind

CSV_UNREADABLE = "CSV_UNREADABLE"
EXTRACTION_VERSION = "csv-report-1"
ReviewStatus = Literal["UNREVIEWED", "NEEDS_REVIEW"]


class CsvReportExtractor:
    """Extract a report from a CSV file using an explicit template."""

    def __init__(
        self,
        registry: InputRegistry,
        template: ReportTemplate = REPORT_TEMPLATE,
        *,
        delimiter: str = ",",
        encoding: str = "utf-8-sig",
    ) -> None:
        if len(delimiter) != 1 or delimiter in {'"', "\r", "\n"}:
            raise ValueError("delimiter must be one non-quote character")
        if not encoding:
            raise ValueError("encoding must not be empty")
        self.registry = registry
        self.template = template
        self.delimiter = delimiter
        self.encoding = encoding

    def extract(self, file_id: str) -> ExtractionResult:
        entry = self._entry(file_id)
        if entry.role != "REPORT":
            raise ValueError(
                f"CSV report adapter requires a REPORT file, got {entry.role}"
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
        try:
            text = data.decode(self.encoding)
            rows = list(
                csv.reader(io.StringIO(text, newline=""), delimiter=self.delimiter)
            )
        except (UnicodeDecodeError, LookupError, csv.Error, ValueError) as exc:
            return self._failed(entry, CSV_UNREADABLE, str(exc))

        template = self.template
        header_index = template.header_row - 1
        if header_index < 0 or header_index >= len(rows):
            return self._failed(
                entry,
                TEMPLATE_HEADER_MISSING,
                f"header row {template.header_row} is outside the file",
            )
        header_issues = self._header_issues(entry, rows[header_index])
        if header_issues:
            return self._result(entry, "FAILED", [], [], header_issues)

        amount_index = _column_index(template.amount_column)
        field_indexes = [
            (field, _column_index(field.column)) for field in template.fields
        ]
        label_index = _column_index(
            template.label_column
            if template.label_column is not None
            else template.fields[0].column
        )
        kind_index = (
            _column_index(template.kind_column)
            if template.kind_column is not None
            else None
        )
        label_kinds = {_normalize_label(label): kind for label, kind in template.labels}

        sources: list[Source] = []
        extracted_rows: list[ExtractedRow] = []
        issues: list[Issue] = []
        data_start = max(template.first_data_row, template.header_row + 1) - 1
        for offset, csv_row in enumerate(rows[data_start:]):
            row_number = template.first_data_row + offset
            values = tuple(csv_row)
            field_raw = {
                field.name: _cell_text(_at(values, index))
                for field, index in field_indexes
            }
            amount_raw = _cell_text(_at(values, amount_index))
            label_raw = _cell_text(_at(values, label_index))
            kind_raw = (
                _cell_text(_at(values, kind_index)) if kind_index is not None else None
            )

            if all(
                _is_blank(value)
                for value in (amount_raw, label_raw, kind_raw, *field_raw.values())
            ):
                continue

            detected = _row_kind(
                kind_raw, (label_raw, *field_raw.values()), label_kinds
            )
            if detected is None:
                has_content = not _is_blank(amount_raw) or any(
                    not _is_blank(value) for value in field_raw.values()
                )
                if has_content:
                    kind: RowKind = "ITEM"
                else:
                    kind = "UNKNOWN"
                    issues.append(
                        _issue(
                            entry,
                            row_number,
                            ROW_KIND_UNKNOWN,
                            "row has no recognized kind, label or data",
                        )
                    )
            else:
                kind = detected
                if kind == "UNKNOWN":
                    issues.append(
                        _issue(
                            entry, row_number, ROW_KIND_UNKNOWN, "row kind is UNKNOWN"
                        )
                    )

            field_ids: dict[str, str] = {}
            for field, index in field_indexes:
                cell = f"{field.column.strip().upper()}{row_number}"
                raw = field_raw[field.name]
                value = raw if not _is_blank(raw) else None
                review: ReviewStatus = "UNREVIEWED"
                if field.required and kind == "ITEM" and value is None:
                    review = "NEEDS_REVIEW"
                    issues.append(
                        _issue(
                            entry,
                            row_number,
                            FIELD_MISSING,
                            f"required field {field.name!r} is empty",
                            column=field.column,
                        )
                    )
                source = _source(entry, row_number, cell, raw, value, review)
                sources.append(source)
                field_ids[field.name] = source.source_id

            amount_cell = f"{template.amount_column.strip().upper()}{row_number}"
            amount_source, amount_issues = _amount_source(
                entry, row_number, amount_cell, amount_raw
            )
            sources.append(amount_source)
            issues.extend(amount_issues)
            extracted_rows.append(
                ExtractedRow(
                    row_id=f"R-{entry.file_id}-CSV-{row_number}",
                    kind=kind,
                    amount_source_id=amount_source.source_id,
                    fields=field_ids,
                )
            )

        status: FileStatus = "PARTIAL" if issues else "COMPLETED"
        return self._result(entry, status, sources, extracted_rows, issues)

    def _header_issues(
        self, entry: RegisteredInput, header: Sequence[str]
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
            actual = _text(_at(tuple(header), _column_index(column)))
            if actual is None or not actual.strip():
                issues.append(
                    Issue(
                        code=TEMPLATE_HEADER_MISSING,
                        file_id=entry.file_id,
                        detail=(
                            f"CSV!{column.strip().upper()}{template.header_row}: "
                            f"expected header {expected!r}"
                        ),
                    )
                )
            elif actual.strip().casefold() != expected.strip().casefold():
                issues.append(
                    Issue(
                        code=TEMPLATE_HEADER_MISMATCH,
                        file_id=entry.file_id,
                        detail=(
                            f"CSV!{column.strip().upper()}{template.header_row}: "
                            f"expected {expected!r}, got {actual!r}"
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
            [Issue(code=code, file_id=entry.file_id, detail=f"CSV: {detail}")],
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

    def _entry(self, file_id: str) -> RegisteredInput:
        for entry in self.registry.inputs:
            if entry.file_id == file_id:
                return entry
        raise KeyError(f"Unknown file ID: {file_id}")


def extract_csv_report(
    registry: InputRegistry,
    file_id: str,
    *,
    template: ReportTemplate = REPORT_TEMPLATE,
    delimiter: str = ",",
    encoding: str = "utf-8-sig",
) -> ExtractionResult:
    return CsvReportExtractor(
        registry, template, delimiter=delimiter, encoding=encoding
    ).extract(file_id)


def _row_kind(
    kind_raw: str | None,
    labels: Sequence[str | None],
    label_kinds: dict[str, RowKind],
) -> RowKind | None:
    kind_text = (kind_raw or "").strip()
    if kind_text:
        kind = _KINDS.get(kind_text.upper())
        if kind is None:
            return "UNKNOWN"
        return kind
    for value in labels:
        label = _normalize_label(value)
        if label in label_kinds:
            return label_kinds[label]
    return None


def _amount_source(
    entry: RegisteredInput,
    row_number: int,
    cell: str,
    raw: str | None,
) -> tuple[Source, list[Issue]]:
    column = cell.rstrip("0123456789")
    if _is_blank(raw):
        return (
            _source(entry, row_number, cell, raw, None, "NEEDS_REVIEW"),
            [
                _issue(
                    entry, row_number, AMOUNT_MISSING, "amount is empty", column=column
                )
            ],
        )
    parsed = parse_amount(raw)
    if parsed.value is None:
        return (
            _source(entry, row_number, cell, raw, None, "NEEDS_REVIEW"),
            [
                _issue(
                    entry,
                    row_number,
                    AMOUNT_INVALID,
                    parsed.error or "not an amount",
                    column=column,
                )
            ],
        )
    return _source(entry, row_number, cell, raw, parsed.value, "UNREVIEWED"), []


def _cell_text(value: object) -> str | None:
    text = _text(value)
    return None if text == "" else text


def _source(
    entry: RegisteredInput,
    row_number: int,
    cell: str,
    raw: str | None,
    value: str | None,
    review: ReviewStatus,
) -> Source:
    return Source(
        source_id=f"S-{entry.file_id}-CSV-{cell}",
        file_id=entry.file_id,
        document_id=f"D-{entry.file_id}",
        location=CellLocation(kind="CELL", sheet=None, cell=cell),
        method="TABLE",
        raw_text=raw,
        value=value,
        review_status=review,
    )


def _issue(
    entry: RegisteredInput,
    row_number: int,
    code: str,
    detail: str,
    *,
    column: str | None = None,
) -> Issue:
    cell = f"{column.strip().upper()}{row_number}" if column else str(row_number)
    return Issue(
        code=code,
        file_id=entry.file_id,
        detail=f"CSV!{cell}: {detail}",
    )
