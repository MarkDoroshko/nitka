from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from openpyxl.utils import column_index_from_string

RowKind = Literal["ITEM", "PAGE_SUBTOTAL", "DOCUMENT_TOTAL", "TAX_SUMMARY", "UNKNOWN"]
ReviewStatus = Literal["UNREVIEWED", "NEEDS_REVIEW"]


_KINDS: Final[dict[str, RowKind]] = {
    "ITEM": "ITEM",
    "PAGE_SUBTOTAL": "PAGE_SUBTOTAL",
    "DOCUMENT_TOTAL": "DOCUMENT_TOTAL",
    "TAX_SUMMARY": "TAX_SUMMARY",
    "UNKNOWN": "UNKNOWN",
}


def _column_index(column: str) -> int:
    try:
        return int(column_index_from_string(column.strip().upper()))
    except ValueError as exc:
        raise ValueError(f"Invalid column: {column!r}") from exc


DEFAULT_LABELS: Final[tuple[tuple[str, RowKind], ...]] = (
    ("итого", "DOCUMENT_TOTAL"),
    ("всего", "DOCUMENT_TOTAL"),
    ("total", "DOCUMENT_TOTAL"),
    ("подытог", "PAGE_SUBTOTAL"),
    ("промежуточный итог", "PAGE_SUBTOTAL"),
    ("subtotal", "PAGE_SUBTOTAL"),
    ("ндс", "TAX_SUMMARY"),
    ("налог", "TAX_SUMMARY"),
)


@dataclass(frozen=True)
class FieldSpec:
    """A requisite column and the header expected above it."""

    name: str
    column: str
    header: str
    required: bool = True


@dataclass(frozen=True)
class ReportTemplate:
    """Explicit description of the supported XLSX report."""

    sheet: str
    header_row: int
    first_data_row: int
    amount_column: str
    amount_header: str
    fields: tuple[FieldSpec, ...]
    label_column: str | None = None
    kind_column: str | None = None
    kind_header: str | None = None
    labels: tuple[tuple[str, RowKind], ...] = DEFAULT_LABELS

    def __post_init__(self) -> None:
        if not self.sheet.strip():
            raise ValueError("Report template requires a sheet name")
        if self.header_row < 1:
            raise ValueError("header_row must be >= 1")
        if self.first_data_row <= self.header_row:
            raise ValueError("first_data_row must be after header_row")
        if not self.amount_header.strip():
            raise ValueError("amount_header must not be empty")
        if not self.fields:
            raise ValueError("Report template requires at least one field")
        if not self.labels:
            raise ValueError("Report template requires at least one total label")

        amount_index = _column_index(self.amount_column)
        names: set[str] = set()
        columns: set[int] = set()
        for field in self.fields:
            if not field.name.strip() or not field.header.strip():
                raise ValueError("Field name and header must not be empty")
            if field.name in names:
                raise ValueError(f"Duplicate field name: {field.name}")
            index = _column_index(field.column)
            if index == amount_index:
                raise ValueError("Amount column cannot also be a field column")
            if index in columns:
                raise ValueError(f"Duplicate field column: {field.column}")
            names.add(field.name)
            columns.add(index)

        label_index = _column_index(
            self.label_column
            if self.label_column is not None
            else self.fields[0].column
        )
        if (self.kind_column is None) != (self.kind_header is None):
            raise ValueError("kind_column and kind_header must be set together")
        if self.kind_column is not None:
            kind_index = _column_index(self.kind_column)
            if kind_index in columns or kind_index == amount_index:
                raise ValueError("Kind column cannot be an amount or field column")
            if not self.kind_header or not self.kind_header.strip():
                raise ValueError("kind_header must not be empty")
            if kind_index == label_index:
                raise ValueError("Kind column cannot also be the label column")
        for label, _ in self.labels:
            if not label.strip():
                raise ValueError("Total labels must not be empty")


REPORT_TEMPLATE: Final[ReportTemplate] = ReportTemplate(
    sheet="Report",
    header_row=1,
    first_data_row=2,
    amount_column="D",
    amount_header="Сумма",
    fields=(
        FieldSpec(name="contract", column="A", header="Договор"),
        FieldSpec(
            name="description", column="B", header="Наименование", required=False
        ),
    ),
    label_column="B",
)
DEFAULT_REPORT_TEMPLATE: Final[ReportTemplate] = REPORT_TEMPLATE


def _normalize_label(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.split()).casefold().rstrip(":").strip()
