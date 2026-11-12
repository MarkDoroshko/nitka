"""Value-only corrections that preserve the original extraction evidence."""

from decimal import Decimal

from nitka.amounts import normalize_amount
from nitka.contracts.extraction import ExtractionResult
from nitka.contracts.validation import validate_extraction


class AmountCorrectionError(ValueError):
    """A correction does not identify a changed, valid monetary value."""


def correct_amount(
    extraction: ExtractionResult, source_id: str, value: str
) -> ExtractionResult:
    """Return an independent extraction; never rewrite printed text/provenance."""
    validate_extraction(extraction)
    source = next(
        (source for source in extraction.sources if source.source_id == source_id),
        None,
    )
    if source is None:
        raise AmountCorrectionError(f"Unknown source: {source_id}")
    if not any(row.amount_source_id == source_id for row in extraction.rows):
        raise AmountCorrectionError(f"Source is not a row amount: {source_id}")
    normalized = normalize_amount(value)
    if normalized is None:
        raise AmountCorrectionError("Enter a valid amount, for example 120 000,00")
    previous = normalize_amount(source.value)
    if previous is not None and Decimal(previous) == Decimal(normalized):
        raise AmountCorrectionError("The new amount equals the current amount")
    updated = extraction.model_copy(deep=True)
    target = next(source for source in updated.sources if source.source_id == source_id)
    target.value = normalized
    target.review_status = "CORRECTED"
    return updated
