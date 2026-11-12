from pathlib import Path

import pytest

from nitka.contracts.extraction import ExtractionResult
from nitka.corrections import AmountCorrectionError, correct_amount


def _extraction() -> ExtractionResult:
    return ExtractionResult.model_validate_json(
        Path("examples/extraction.json").read_bytes()
    )


def test_correct_amount_preserves_evidence_and_original() -> None:
    extraction = _extraction()
    before = extraction.model_dump_json()
    source_id = extraction.rows[0].amount_source_id
    original = next(s for s in extraction.sources if s.source_id == source_id)
    corrected = correct_amount(extraction, source_id, "120 000,00")
    source = next(s for s in corrected.sources if s.source_id == source_id)
    assert source.value == "120000.00"
    assert source.review_status == "CORRECTED"
    assert source.model_dump(exclude={"value", "review_status"}) == original.model_dump(
        exclude={"value", "review_status"}
    )
    assert extraction.model_dump_json() == before
    corrected.files[0].name = "independent copy"
    assert extraction.model_dump_json() == before


@pytest.mark.parametrize("value", ["", "NaN", "1e3", "12,34.00", "150000.000"])
def test_invalid_or_numerically_unchanged_amount(value: str) -> None:
    extraction = _extraction()
    with pytest.raises(AmountCorrectionError):
        correct_amount(extraction, extraction.rows[0].amount_source_id, value)


def test_unknown_and_non_amount_sources() -> None:
    extraction = _extraction()
    with pytest.raises(AmountCorrectionError, match="Unknown source"):
        correct_amount(extraction, "missing", "1")
    non_amount = extraction.sources[0].model_copy(update={"source_id": "text"})
    extraction.sources.append(non_amount)
    with pytest.raises(AmountCorrectionError, match="not a row amount"):
        correct_amount(extraction, "text", "1")


def test_missing_support_amount_can_be_corrected() -> None:
    extraction = _extraction()
    source_id = extraction.rows[-1].amount_source_id
    source = next(s for s in extraction.sources if s.source_id == source_id)
    source.value = None
    corrected = correct_amount(extraction, source_id, "0")
    assert next(s for s in corrected.sources if s.source_id == source_id).value == "0"
    assert source.value is None
