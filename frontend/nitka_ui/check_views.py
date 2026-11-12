from __future__ import annotations

from pathlib import Path

from nitka.contracts.extraction import ExtractedRow, FileRecord, Source

from .demo_models import CheckView, RunSnapshot, SourceView


def build_check_views(snapshot: RunSnapshot) -> tuple[CheckView, ...]:
    rows_by_id = {row.row_id: row for row in snapshot.extraction.rows}
    source_to_row = {row.amount_source_id: row for row in snapshot.extraction.rows}
    sources_by_id = {source.source_id: source for source in snapshot.extraction.sources}
    matches_by_id = {match.match_id: match for match in snapshot.result.matches}
    files_by_id = {file.file_id: file for file in snapshot.extraction.files}
    views: list[CheckView] = []

    for check in snapshot.result.checks:
        row = rows_by_id.get(check.target_id)
        if row is None:
            if check.target_id.startswith("ARTICLE:"):
                row_id = check.target_id
                row_label = f"Статья {check.target_id.split(':', 1)[1]}"
            elif check.target_id == "OVERALL":
                row_id = check.target_id
                row_label = "Общий итог"
            else:
                continue
        else:
            row_id = row.row_id
            row_label = snapshot.kit.row_labels.get(row.row_id, f"Строка {row.row_id}")
        reported: SourceView | None = None
        supported: list[SourceView] = []

        for operand in check.operands:
            if operand.name == "reported" and operand.source_ids:
                source = sources_by_id.get(operand.source_ids[0])
                if source is not None:
                    reported = _source_view(
                        snapshot, source, "reported", source_to_row, files_by_id
                    )
            elif operand.name == "supported":
                for source_id in operand.source_ids:
                    source = sources_by_id.get(source_id)
                    if source is not None:
                        supported.append(
                            _source_view(
                                snapshot,
                                source,
                                "supported",
                                source_to_row,
                                files_by_id,
                            )
                        )

        match = matches_by_id.get(check.match_id) if check.match_id else None
        views.append(
            CheckView(
                check=check,
                report_row_id=row_id,
                row_label=row_label,
                reported=reported,
                supported=tuple(supported),
                match_state=match.state if match else None,
            )
        )
    return tuple(views)


def _source_view(
    snapshot: RunSnapshot,
    source: Source,
    role: str,
    source_to_row: dict[str, ExtractedRow],
    files_by_id: dict[str, FileRecord],
) -> SourceView:
    file = files_by_id[source.file_id]
    row = source_to_row.get(source.source_id)
    row_id = row.row_id if row is not None else source.source_id
    row_label = snapshot.kit.row_labels.get(row_id, row_id)
    document = snapshot.kit.documents.get(source.document_id)
    page = getattr(source.location, "page", None)
    sheet = getattr(source.location, "sheet", None)
    cell = getattr(source.location, "cell", None)

    preview_lines: tuple[str, ...] = ()
    if document is not None and document.pages:
        index = (page - 1) if page else 0
        index = min(max(index, 0), len(document.pages) - 1)
        preview_lines = document.pages[index]
    if not preview_lines and source.raw_text:
        preview_lines = (source.raw_text,)

    return SourceView(
        source_id=source.source_id,
        role=role,
        row_id=row_id,
        row_label=row_label,
        file_id=source.file_id,
        file_name=file.name,
        document_id=source.document_id,
        document_title=document.title if document else file.name,
        document_kind=(
            document.kind if document else Path(file.name).suffix.lstrip(".").upper()
        ),
        location_kind=source.location.kind,
        page=page,
        sheet=sheet,
        cell=cell,
        method=source.method,
        raw_text=source.raw_text,
        value=source.value,
        review_status=source.review_status,
        preview_lines=preview_lines,
    )
