from decimal import Decimal
from pathlib import Path

import pytest

from nitka.contracts.analysis import Match, Scope
from nitka.contracts.extraction import (
    ExtractedRow,
    ExtractionResult,
    PageLocation,
    Source,
)
from nitka.extraction.loading import load_extraction
from nitka.pipeline import analyze_extraction

SCOPE = Scope(currency="RUB", amount_basis="GROSS", reporting_period="2026-10")


@pytest.fixture
def extraction() -> ExtractionResult:
    fixture = Path(__file__).resolve().parents[1] / "examples" / "extraction.json"
    return load_extraction(fixture)


@pytest.fixture
def match() -> Match:
    return Match(
        match_id="M-1", report_row_id="R-1", support_row_ids=["A-1"], state="ACCEPTED"
    )


def test_partial_run_keeps_discrepancy_and_evidence(
    extraction: ExtractionResult, match: Match
) -> None:
    extraction.files[1].status = "PARTIAL"
    result = analyze_extraction(extraction, [match], SCOPE)
    (check,) = result.checks
    assert result.execution_status == "PARTIAL"
    assert result.decision == "DISCREPANCIES_FOUND"
    assert check.calculation is not None
    assert check.calculation.delta == "30000.00"
    assert check.evidence_ids == ["S-R-AMOUNT", "S-A-AMOUNT"]


@pytest.mark.parametrize("missing_support", [True, False])
def test_unknown_support_does_not_become_zero(
    extraction: ExtractionResult, match: Match, missing_support: bool
) -> None:
    extraction.sources[1].value = None
    result = analyze_extraction(extraction, [] if missing_support else [match], SCOPE)
    (check,) = result.checks
    assert result.decision == "NOT_ASSESSED"
    assert check.state == "NOT_RUN"
    assert check.operands[1].value is None
    assert check.calculation is None


def test_accepted_match_cannot_count_a_support_row_twice(
    extraction: ExtractionResult, match: Match
) -> None:
    match.support_row_ids.append("A-1")
    with pytest.raises(ValueError, match="Duplicate support row IDs"):
        analyze_extraction(extraction, [match], SCOPE)


def test_empty_report_cannot_pass(extraction: ExtractionResult) -> None:
    extraction.rows.clear()
    result = analyze_extraction(extraction, [], SCOPE)
    assert result.decision == "NOT_ASSESSED"


def test_noncanonical_imported_extraction_does_not_crash(
    tmp_path: Path, extraction: ExtractionResult, match: Match
) -> None:
    extraction.sources[0].value = "150 000,00"
    extraction.sources[1].value = "120 000,00"
    imported_path = tmp_path / "imported.json"
    imported_path.write_text(extraction.model_dump_json())
    imported = load_extraction(imported_path)

    result = analyze_extraction(imported, [match], SCOPE)

    (check,) = result.checks
    assert check.state == "FAIL"
    assert check.reason_code == "AMOUNT_EXCEEDS_SUPPORT"
    assert check.calculation is not None
    assert check.calculation.actual == "150000.00"
    assert check.calculation.expected == "120000.00"
    assert check.calculation.delta == "30000.00"
    assert check.calculation.tolerance == "0.00"
    assert check.calculation.unit == "RUB"
    assert check.evidence_ids == ["S-R-AMOUNT", "S-A-AMOUNT"]


def test_report_below_support_gives_negative_delta(
    extraction: ExtractionResult, match: Match
) -> None:
    extraction.sources[0].value = "120000.00"
    extraction.sources[1].value = "150000.00"

    result = analyze_extraction(extraction, [match], SCOPE)

    (check,) = result.checks
    assert check.state == "FAIL"
    assert check.reason_code == "AMOUNT_BELOW_SUPPORT"
    assert check.calculation is not None
    assert check.calculation.delta == "-30000.00"


def test_equal_amounts_with_different_precision_pass(
    extraction: ExtractionResult, match: Match
) -> None:
    extraction.sources[0].value = "100.0"
    extraction.sources[1].value = "100.00"

    result = analyze_extraction(extraction, [match], SCOPE)

    (check,) = result.checks
    assert check.state == "PASS"
    assert check.reason_code == "AMOUNTS_EQUAL"
    assert check.calculation is not None
    assert Decimal(check.calculation.delta) == 0


def test_accepted_n_to_one_support_is_summed_exactly(
    extraction: ExtractionResult,
) -> None:
    extraction.sources[0].value = "0.30"
    extraction.sources[1].value = "0.10"
    extraction.sources.append(
        Source(
            source_id="S-A-AMOUNT-2",
            file_id="F-ACT",
            document_id="D-ACT",
            location=PageLocation(kind="PAGE", page=3),
            method="VISION",
            raw_text="0,20",
            value="0.20",
            review_status="UNREVIEWED",
        )
    )
    extraction.rows.append(
        ExtractedRow(
            row_id="A-2", kind="ITEM", amount_source_id="S-A-AMOUNT-2"
        )
    )
    accepted = Match(
        match_id="M-2",
        report_row_id="R-1",
        support_row_ids=["A-1", "A-2"],
        state="ACCEPTED",
    )

    result = analyze_extraction(extraction, [accepted], SCOPE)

    (check,) = result.checks
    assert check.state == "PASS"
    assert check.calculation is not None
    assert check.calculation.expected == "0.30"
    assert check.calculation.delta == "0.00"
    assert check.evidence_ids == [
        "S-R-AMOUNT",
        "S-A-AMOUNT",
        "S-A-AMOUNT-2",
    ]


def test_invalid_amount_becomes_not_run_without_crashing(
    tmp_path: Path, extraction: ExtractionResult, match: Match
) -> None:
    extraction.sources[0].value = "12 34,56"
    imported_path = tmp_path / "invalid.json"
    imported_path.write_text(extraction.model_dump_json())
    imported = load_extraction(imported_path)

    result = analyze_extraction(imported, [match], SCOPE)

    (check,) = result.checks
    assert check.state == "NOT_RUN"
    assert check.reason_code == "AMOUNT_INVALID"
    assert check.calculation is None
    assert check.operands[0].value is None
    assert result.decision == "NOT_ASSESSED"


def test_missing_amount_becomes_not_run(
    extraction: ExtractionResult, match: Match
) -> None:
    extraction.sources[0].value = ""

    result = analyze_extraction(extraction, [match], SCOPE)

    (check,) = result.checks
    assert check.state == "NOT_RUN"
    assert check.reason_code == "AMOUNT_MISSING"
    assert check.calculation is None


def test_ambiguous_match_becomes_not_run(
    extraction: ExtractionResult,
) -> None:
    ambiguous = Match(
        match_id="M-A",
        report_row_id="R-1",
        support_row_ids=["A-1"],
        state="AMBIGUOUS",
    )

    result = analyze_extraction(extraction, [ambiguous], SCOPE)

    (check,) = result.checks
    assert check.state == "NOT_RUN"
    assert check.reason_code == "AMBIGUOUS_MATCH"
    assert check.calculation is None


def test_missing_support_becomes_not_run(
    extraction: ExtractionResult,
) -> None:
    result = analyze_extraction(extraction, [], SCOPE)

    (check,) = result.checks
    assert check.state == "NOT_RUN"
    assert check.reason_code == "MISSING_SUPPORT"
    assert check.calculation is None
    assert result.decision == "NOT_ASSESSED"


def test_manual_review_blocks_calculation(
    extraction: ExtractionResult, match: Match
) -> None:
    extraction.sources[1].review_status = "NEEDS_REVIEW"

    result = analyze_extraction(extraction, [match], SCOPE)

    (check,) = result.checks
    assert check.state == "NOT_RUN"
    assert check.reason_code == "AMOUNT_NEEDS_REVIEW"
    assert check.calculation is None


def test_blank_scope_currency_blocks_calculation(
    extraction: ExtractionResult, match: Match
) -> None:
    scope = Scope(currency="", amount_basis="GROSS", reporting_period="2026-10")

    result = analyze_extraction(extraction, [match], scope)

    (check,) = result.checks
    assert check.state == "NOT_RUN"
    assert check.reason_code == "SCOPE_CURRENCY_MISSING"
    assert check.calculation is None
