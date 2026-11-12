"""Construct evidence from literal cells and cached formula values."""

from __future__ import annotations

from typing import Any

from nitka.amounts import parse_amount
from nitka.contracts.extraction import CellLocation, Issue, Source
from nitka.extraction.registry import RegisteredInput

from .xlsx_cells import _document_id, _is_blank, _issue, _source_id, _text
from .xlsx_issues import (
    AMOUNT_CELL_ERROR,
    AMOUNT_INVALID,
    AMOUNT_MISSING,
    FIELD_MISSING,
    FORMULA_CACHED,
    FORMULA_UNVERIFIED,
)
from .xlsx_template import FieldSpec, ReportTemplate, ReviewStatus, RowKind


class XlsxSources:
    def __init__(self, template: ReportTemplate) -> None:
        self.template = template

    def field_source(
        self,
        entry: RegisteredInput,
        row_number: int,
        cell: str,
        field: FieldSpec,
        raw: str | None,
        cached: Any,
        kind: RowKind,
    ) -> tuple[Source, list[Issue]]:
        template = self.template
        formula = raw is not None and raw.startswith("=")
        issues: list[Issue] = []
        review: ReviewStatus = "UNREVIEWED"

        if formula:
            if cached is None:
                value = None
                review = "NEEDS_REVIEW"
                issues.append(
                    _issue(
                        entry,
                        template,
                        row_number,
                        FORMULA_UNVERIFIED,
                        f"field {field.name!r} has a formula and no cached value",
                        column=field.column,
                    )
                )
            else:
                value = _text(cached)
                if value is not None and not value.strip():
                    value = None
        else:
            value = _text(cached) if cached is not None else raw
            if value is not None and not value.strip():
                value = None

        if field.required and kind == "ITEM" and value is None:
            review = "NEEDS_REVIEW"
            issues.append(
                _issue(
                    entry,
                    template,
                    row_number,
                    FIELD_MISSING,
                    f"required field {field.name!r} is empty",
                    column=field.column,
                )
            )

        source = Source(
            source_id=_source_id(entry.file_id, template.sheet, cell),
            file_id=entry.file_id,
            document_id=_document_id(entry.file_id),
            location=CellLocation(kind="CELL", sheet=template.sheet, cell=cell),
            method="TABLE",
            raw_text=raw,
            value=value,
            review_status=review,
        )
        return source, issues

    def amount_source(
        self,
        entry: RegisteredInput,
        row_number: int,
        cell: str,
        raw: str | None,
        cached: Any,
    ) -> tuple[Source, list[Issue]]:
        template = self.template
        formula = raw is not None and raw.startswith("=")
        issues: list[Issue] = []
        review: ReviewStatus = "UNREVIEWED"
        value: str | None = None

        if formula:
            review = "NEEDS_REVIEW"
            if cached is None:
                issues.append(
                    _issue(
                        entry,
                        template,
                        row_number,
                        FORMULA_UNVERIFIED,
                        "formula in amount column has no cached value",
                        column=template.amount_column,
                    )
                )
            else:
                parsed = parse_amount(cached)
                value = parsed.value
                if value is None:
                    issues.append(
                        _issue(
                            entry,
                            template,
                            row_number,
                            AMOUNT_INVALID,
                            "cached formula value is invalid: "
                            f"{parsed.error or 'not an amount'}",
                            column=template.amount_column,
                        )
                    )
                else:
                    issues.append(
                        _issue(
                            entry,
                            template,
                            row_number,
                            FORMULA_CACHED,
                            "formula cached value is not recalculated; verify it",
                            column=template.amount_column,
                        )
                    )
        elif _is_blank(raw) and _is_blank(cached):
            review = "NEEDS_REVIEW"
            issues.append(
                _issue(
                    entry,
                    template,
                    row_number,
                    AMOUNT_MISSING,
                    "amount is empty",
                    column=template.amount_column,
                )
            )
        else:
            candidate = cached if not _is_blank(cached) else raw
            parsed = parse_amount(candidate)
            value = parsed.value
            if value is None:
                review = "NEEDS_REVIEW"
                code = (
                    AMOUNT_CELL_ERROR
                    if isinstance(candidate, str) and candidate.startswith("#")
                    else AMOUNT_INVALID
                )
                issues.append(
                    _issue(
                        entry,
                        template,
                        row_number,
                        code,
                        parsed.error or "not an amount",
                        column=template.amount_column,
                    )
                )

        source = Source(
            source_id=_source_id(entry.file_id, template.sheet, cell),
            file_id=entry.file_id,
            document_id=_document_id(entry.file_id),
            location=CellLocation(kind="CELL", sheet=template.sheet, cell=cell),
            method="TABLE",
            raw_text=raw,
            value=value,
            review_status=review,
        )
        return source, issues
