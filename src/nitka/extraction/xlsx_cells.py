from __future__ import annotations

from typing import Any

from nitka.contracts.extraction import Issue
from nitka.extraction.registry import RegisteredInput

from .xlsx_template import ReportTemplate


def _source_id(file_id: str, sheet: str, cell: str) -> str:
    return f"S-{file_id}-{sheet}-{cell}"


def _document_id(file_id: str) -> str:
    return f"D-{file_id}"


def _row_values(worksheet: Any, row: int) -> tuple[Any, ...]:
    for values in worksheet.iter_rows(min_row=row, max_row=row, values_only=True):
        return tuple(values)
    return ()


def _at(row: tuple[Any, ...], index: int) -> Any:
    return row[index - 1] if 0 < index <= len(row) else None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float, str)):
        return str(value)
    return str(value)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _issue(
    entry: RegisteredInput,
    template: ReportTemplate,
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
        detail=f"{template.sheet}!{cell}: {detail}",
    )
