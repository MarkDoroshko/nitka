from pathlib import Path

import pytest
from pydantic import ValidationError

from nitka.contracts.analysis import AnalysisResult, ExcludedDocument
from nitka.contracts.extraction import (
    CellLocation,
    DocxLocation,
    ExtractedRow,
    ExtractionResult,
    FileRecord,
    Issue,
    OcrLocation,
    PageLocation,
    Source,
)
from nitka.contracts.profile import CaseProfile
from nitka.contracts.validation import validate_extraction
from nitka.extraction.loading import load_extraction

REPORT_SHA = "a" * 64
ACT_SHA = "b" * 64


def make_extraction() -> ExtractionResult:
    return ExtractionResult(
        extraction_version="test-1",
        files=[
            FileRecord(
                file_id="F-1",
                name="report.xlsx",
                sha256=REPORT_SHA,
                role="REPORT",
                status="COMPLETED",
            ),
            FileRecord(
                file_id="F-2",
                name="act.pdf",
                sha256=ACT_SHA,
                role="SUPPORT",
                status="COMPLETED",
            ),
        ],
        sources=[
            Source(
                source_id="S-R",
                file_id="F-1",
                document_id="D-R",
                location=CellLocation(kind="CELL", sheet="Report", cell="D2"),
                method="TABLE",
                raw_text="150 000,00",
                value="150000.00",
                review_status="UNREVIEWED",
            ),
            Source(
                source_id="S-A",
                file_id="F-2",
                document_id="D-A",
                location=PageLocation(kind="PAGE", page=2),
                method="VISION",
                raw_text="120 000,00",
                value="120000.00",
                review_status="UNREVIEWED",
            ),
            Source(
                source_id="S-CONTRACT",
                file_id="F-1",
                document_id="D-R",
                location=CellLocation(kind="CELL", sheet="Report", cell="B1"),
                method="TABLE",
                raw_text="12-П",
                value="12-П",
                review_status="UNREVIEWED",
            ),
        ],
        rows=[
            ExtractedRow(
                row_id="R-1",
                kind="ITEM",
                amount_source_id="S-R",
                fields={"contract": "S-CONTRACT"},
            ),
            ExtractedRow(row_id="A-1", kind="ITEM", amount_source_id="S-A"),
        ],
    )


def test_valid_extraction_passes() -> None:
    validate_extraction(make_extraction())


def test_duplicate_file_ids_are_rejected() -> None:
    extraction = make_extraction()
    extraction.files.append(extraction.files[0].model_copy())
    with pytest.raises(ValueError, match="Duplicate file IDs"):
        validate_extraction(extraction)


def test_duplicate_source_ids_are_rejected() -> None:
    extraction = make_extraction()
    extraction.sources.append(extraction.sources[0].model_copy())
    with pytest.raises(ValueError, match="Duplicate source IDs"):
        validate_extraction(extraction)


def test_duplicate_row_ids_are_rejected() -> None:
    extraction = make_extraction()
    extraction.rows.append(extraction.rows[0].model_copy())
    with pytest.raises(ValueError, match="Duplicate row IDs"):
        validate_extraction(extraction)


def test_unknown_file_reference_is_rejected() -> None:
    extraction = make_extraction()
    extraction.sources[0].file_id = "F-404"
    with pytest.raises(ValueError, match="references an unknown file"):
        validate_extraction(extraction)


def test_unknown_amount_source_is_rejected() -> None:
    extraction = make_extraction()
    extraction.rows[0].amount_source_id = "S-404"
    with pytest.raises(ValueError, match="Unknown source referenced by row R-1"):
        validate_extraction(extraction)


def test_unknown_field_source_is_rejected() -> None:
    extraction = make_extraction()
    extraction.rows[0].fields["contract"] = "S-404"
    with pytest.raises(ValueError, match="Unknown source referenced by row R-1"):
        validate_extraction(extraction)


def test_field_source_must_be_in_the_same_document_as_the_amount() -> None:
    extraction = make_extraction()
    extraction.sources[2].document_id = "D-OTHER"
    with pytest.raises(ValueError, match="references a source in another document"):
        validate_extraction(extraction)


def test_field_source_must_be_in_the_same_file_as_the_amount() -> None:
    extraction = make_extraction()
    extraction.sources[2].file_id = "F-2"
    extraction.sources[2].document_id = "D-A"
    with pytest.raises(ValueError, match="references a source in another document"):
        validate_extraction(extraction)


def test_source_from_a_failed_file_is_rejected() -> None:
    extraction = make_extraction()
    extraction.files[1].status = "FAILED"
    with pytest.raises(ValueError, match="completed or partial"):
        validate_extraction(extraction)


def test_failed_file_without_sources_is_allowed() -> None:
    extraction = make_extraction()
    extraction.files.append(
        FileRecord(
            file_id="F-3",
            name="broken.xlsx",
            sha256="c" * 64,
            role="SUPPORT",
            status="FAILED",
        )
    )
    extraction.issues.append(
        Issue(code="PARSE_FAILED", file_id="F-3", detail="corrupt workbook")
    )

    validate_extraction(extraction)


def test_page_zero_is_rejected_by_the_contract() -> None:
    with pytest.raises(ValidationError):
        PageLocation(kind="PAGE", page=0)


def test_amount_syntax_is_left_to_the_rule() -> None:
    extraction = make_extraction()
    extraction.sources[0].value = "150 000,00"
    extraction.sources[1].value = "12 34,56"
    validate_extraction(extraction)


def test_null_amount_source_is_unknown_not_zero() -> None:
    extraction = make_extraction()
    extraction.sources[0].value = None
    validate_extraction(extraction)


def test_text_requisite_value_is_not_treated_as_money() -> None:
    extraction = make_extraction()
    assert extraction.sources[2].value == "12-П"
    validate_extraction(extraction)


def test_extraction_round_trips_through_json() -> None:
    extraction = make_extraction()
    extraction.sources[0].value = None
    extraction.sources[2].raw_text = None

    loaded = ExtractionResult.model_validate_json(extraction.model_dump_json())

    assert loaded == extraction
    assert loaded.sources[0].value is None
    assert loaded.sources[0].raw_text == "150 000,00"
    assert loaded.sources[2].raw_text is None
    assert loaded.sources[1].location == PageLocation(kind="PAGE", page=2)


def test_example_extraction_still_loads() -> None:
    fixture = Path(__file__).resolve().parents[1] / "examples" / "extraction.json"
    extraction = load_extraction(fixture)
    assert extraction.rows[0].row_id == "R-1"
    assert extraction.sources[0].raw_text == "150 000,00"


def test_model_validate_json_checks_references() -> None:
    extraction = make_extraction()
    extraction.rows[0].amount_source_id = "S-404"

    with pytest.raises(ValueError, match="Unknown source referenced by row R-1"):
        ExtractionResult.model_validate_json(extraction.model_dump_json())


def test_load_extraction_checks_references(tmp_path: Path) -> None:
    extraction = make_extraction()
    extraction.rows[0].amount_source_id = "S-404"
    broken = tmp_path / "broken.json"
    broken.write_text(extraction.model_dump_json(), encoding="utf-8")

    with pytest.raises(ValueError, match="Unknown source referenced by row R-1"):
        load_extraction(broken)


def test_case_contract_fixture_is_the_shared_configurable_profile() -> None:
    fixture = Path(__file__).parent / "fixtures" / "case_contract.json"
    profile = CaseProfile.model_validate_json(fixture.read_text(encoding="utf-8"))

    assert profile.case_number == 5
    assert profile.observed_case_numbers["README_TXT"] == 19
    assert profile.observed_case_numbers["ANSWER_WORKBOOK"] == 19
    assert profile.contract == "DEMO-CONTRACT-001"
    assert profile.target_stage == 3
    assert profile.target_year == 2026
    assert profile.target_row_label == "3 этап 2026"
    assert profile.month_labels() == [
        "2026-01",
        "2026-02",
        "2026-03",
        "2026-04",
        "2026-05",
    ]
    assert profile.raw_header_text == "Договор № DEMO-CONTRACT-001; период 2025"
    assert any(conflict.field == "case_number" for conflict in profile.conflicts)
    assert any(conflict.field == "service_period" for conflict in profile.conflicts)
    assert profile.is_working_role("REPORT")
    assert profile.is_working_role("SUPPORT")
    assert profile.is_excluded_role("TECHNICAL")
    assert profile.is_excluded_role("ORACLE")
    assert not profile.is_working_role("TECHNICAL")


def test_profile_selects_but_does_not_rewrite_raw_text() -> None:
    fixture = Path(__file__).parent / "fixtures" / "case_contract.json"
    profile = CaseProfile.model_validate_json(fixture.read_text(encoding="utf-8"))
    raw = "3 этап 2026; историческая шапка: 2025"

    assert profile.selects_target_row(raw)
    assert not profile.selects_target_row("ВСЕГО 3 этап 2026")
    assert "3 этап 2026" in raw
    assert profile.raw_header_text != profile.target_row_label


def test_ocr_and_docx_provenance_are_additive() -> None:
    extraction = make_extraction()
    extraction.sources.extend(
        [
            Source(
                source_id="S-OCR",
                file_id="F-2",
                document_id="D-A",
                location=OcrLocation(
                    kind="OCR", page=2, bbox=(10.0, 20.0, 110.0, 40.0)
                ),
                method="OCR",
                raw_text="120 000,00",
                value="120000.00",
                confidence=0.93,
                review_status="NEEDS_REVIEW",
            ),
            Source(
                source_id="S-DOCX",
                file_id="F-2",
                document_id="D-A",
                location=DocxLocation(
                    kind="DOCX", table=1, row=2, cell=3, paragraph=None
                ),
                method="DOCX",
                raw_text="120 000,00",
                value="120000.00",
                review_status="UNREVIEWED",
            ),
        ]
    )

    validate_extraction(extraction)
    loaded = ExtractionResult.model_validate_json(extraction.model_dump_json())
    assert isinstance(loaded.sources[3].location, OcrLocation)
    assert loaded.sources[3].confidence == 0.93
    assert isinstance(loaded.sources[4].location, DocxLocation)
    assert loaded.sources[4].location.table == 1


def test_legacy_analysis_without_exclusions_still_parses() -> None:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "pre_refactor_runs"
        / "legacy-run"
        / "revisions"
        / "1"
        / "analysis.json"
    )
    legacy = AnalysisResult.model_validate_json(fixture.read_text(encoding="utf-8"))
    assert legacy.excluded_documents == []
    assert legacy.rules_version == "amount-only-1"


def test_excluded_documents_are_separate_from_matches_and_round_trip() -> None:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "pre_refactor_runs"
        / "legacy-run"
        / "revisions"
        / "1"
        / "analysis.json"
    )
    legacy = AnalysisResult.model_validate_json(fixture.read_text(encoding="utf-8"))
    excluded = ExcludedDocument(
        file_id="F-9",
        document_id="D-9",
        reason="OTHER_PERIOD",
        detail="June is outside January-May 2026",
        source_ids=["S-9"],
    )
    updated = legacy.model_copy(update={"excluded_documents": [excluded]})
    loaded = AnalysisResult.model_validate_json(updated.model_dump_json())

    assert loaded.matches == legacy.matches
    assert loaded.excluded_documents == [excluded]
