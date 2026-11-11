from collections.abc import Iterable

from nitka.contracts.extraction import ExtractionResult


def _require_unique(ids: Iterable[str], label: str) -> None:
    values = list(ids)
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label} IDs")


def validate_extraction(extraction: ExtractionResult) -> None:
    """Check referential integrity of an extraction result.

    Amount syntax is deliberately not checked here: the analysis layer parses
    the printed value and returns ``NOT_RUN`` for an invalid one. This keeps an
    imported but non-canonical amount from crashing the whole run.
    Match-level checks stay in the analysis layer.
    """
    _require_unique((file.file_id for file in extraction.files), "file")
    _require_unique((source.source_id for source in extraction.sources), "source")
    _require_unique((row.row_id for row in extraction.rows), "row")

    files = {file.file_id: file for file in extraction.files}
    sources = {source.source_id: source for source in extraction.sources}

    for source in extraction.sources:
        if source.file_id not in files:
            raise ValueError(
                "Source references an unknown file: "
                f"{source.source_id} -> {source.file_id}"
            )
    if any(files[source.file_id].status == "FAILED" for source in extraction.sources):
        raise ValueError(
            "Sources with usable extraction must belong to completed or partial files"
        )

    for row in extraction.rows:
        amount_source = sources.get(row.amount_source_id)
        if amount_source is None:
            raise ValueError(f"Unknown source referenced by row {row.row_id}")

        for field, source_id in row.fields.items():
            field_source = sources.get(source_id)
            if field_source is None:
                raise ValueError(f"Unknown source referenced by row {row.row_id}")
            if (
                field_source.file_id != amount_source.file_id
                or field_source.document_id != amount_source.document_id
            ):
                raise ValueError(
                    f"Row {row.row_id} field {field!r} references a source in "
                    "another document"
                )
