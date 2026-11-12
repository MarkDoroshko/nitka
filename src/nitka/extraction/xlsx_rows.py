from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from nitka.contracts.extraction import ExtractedRow, Issue, Source

from .registry import RegisteredInput
from .xlsx_cells import _at, _is_blank, _issue, _text
from .xlsx_issues import ROW_KIND_UNKNOWN
from .xlsx_sources import XlsxSources
from .xlsx_template import (
    _KINDS,
    ReportTemplate,
    RowKind,
    _column_index,
    _normalize_label,
)


@dataclass(frozen=True)
class ParsedRow:
    row: ExtractedRow
    sources: tuple[Source, ...]
    issues: tuple[Issue, ...]


class ReportRowParser:
    """Interpret a row using a fixed template and precomputed column indexes."""

    def __init__(self, template: ReportTemplate) -> None:
        self.template = template
        self.sources_reader = XlsxSources(template)
        self.amount_index = _column_index(self.template.amount_column)
        self.field_indexes = [
            (field, _column_index(field.column)) for field in self.template.fields
        ]
        self.label_index = _column_index(
            self.template.label_column
            if self.template.label_column is not None
            else self.template.fields[0].column
        )
        self.kind_index = (
            _column_index(self.template.kind_column)
            if self.template.kind_column is not None
            else None
        )
        self.label_kinds = {
            _normalize_label(label): kind for label, kind in self.template.labels
        }

    def parse(
        self,
        entry: RegisteredInput,
        row_number: int,
        formula_row: tuple[Any, ...],
        value_row: tuple[Any, ...],
    ) -> ParsedRow | None:
        template = self.template
        sources: list[Source] = []
        issues: list[Issue] = []
        field_raw = {
            field.name: _text(_at(formula_row, index))
            for field, index in self.field_indexes
        }
        field_cached = {
            field.name: _at(value_row, index) for field, index in self.field_indexes
        }
        amount_raw = _text(_at(formula_row, self.amount_index))
        amount_cached = _at(value_row, self.amount_index)
        label_raw = _text(_at(formula_row, self.label_index))
        kind_raw = (
            _text(_at(formula_row, self.kind_index))
            if self.kind_index is not None
            else None
        )

        if all(
            _is_blank(value)
            for value in (
                amount_raw,
                label_raw,
                kind_raw,
                *field_raw.values(),
            )
        ):
            return None

        detected = _row_kind(
            kind_raw, (label_raw, *field_raw.values()), self.label_kinds
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
                        template,
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
                        entry,
                        template,
                        row_number,
                        ROW_KIND_UNKNOWN,
                        "row kind is UNKNOWN",
                    )
                )

        field_ids: dict[str, str] = {}
        for field, index in self.field_indexes:
            cell = f"{field.column.strip().upper()}{row_number}"
            source, field_issues = self.sources_reader.field_source(
                entry,
                row_number,
                cell,
                field,
                field_raw[field.name],
                field_cached[field.name],
                kind,
            )
            sources.append(source)
            field_ids[field.name] = source.source_id
            issues.extend(field_issues)

        amount_cell = f"{template.amount_column.strip().upper()}{row_number}"
        amount_source, amount_issues = self.sources_reader.amount_source(
            entry,
            row_number,
            amount_cell,
            amount_raw,
            amount_cached,
        )
        sources.append(amount_source)
        issues.extend(amount_issues)
        row = ExtractedRow(
            row_id=f"R-{entry.file_id}-{template.sheet}-{row_number}",
            kind=kind,
            amount_source_id=amount_source.source_id,
            fields=field_ids,
        )
        return ParsedRow(row, tuple(sources), tuple(issues))


def _row_kind(
    kind_raw: str | None,
    labels: Iterable[str | None],
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
