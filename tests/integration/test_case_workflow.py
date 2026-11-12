from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from nitka.application import CaseApplication
from nitka.contracts.analysis import Match, Scope
from nitka.contracts.extraction import (
    CellLocation,
    ExtractedRow,
    ExtractionResult,
    PageLocation,
    Source,
)
from nitka.contracts.profile import CaseProfile
from nitka.extraction.limits import PackageLimitError, PackageLimits
from nitka.extraction.ports import InputDocument
from nitka.extraction.registry import InputRegistry
from nitka.run_workflows import ProgressEvent
from nitka.storage import RevisionConflictError, StorageError

SCOPE = Scope(currency="RUB", amount_basis="GROSS", reporting_period="2026-05")
PROFILE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "case_contract.json"


def _profile() -> CaseProfile:
    return CaseProfile.model_validate_json(PROFILE_PATH.read_text(encoding="utf-8"))


def _documents(base: Path) -> list[InputDocument]:
    report = base / "report.xlsx"
    support = base / "act.pdf"
    report.write_bytes(b"report-bytes")
    support.write_bytes(b"support-bytes")
    return [
        InputDocument(report, "REPORT"),
        InputDocument(support, "SUPPORT"),
    ]


class _Extractor:
    def __init__(self, profile: CaseProfile) -> None:
        self.profile = profile
        self.calls = 0

    def __call__(self, documents: Sequence[InputDocument]) -> ExtractionResult:
        self.calls += 1
        registry = InputRegistry.register(documents)
        files = [entry.to_file_record("COMPLETED") for entry in registry.inputs]
        report = next(entry for entry in registry.inputs if entry.role == "REPORT")
        support = next(entry for entry in registry.inputs if entry.role == "SUPPORT")
        report_source = Source(
            source_id="S-REPORT-AMOUNT",
            file_id=report.file_id,
            document_id="D-REPORT",
            location=CellLocation(kind="CELL", sheet="Report", cell="D2"),
            method="TABLE",
            raw_text="100.00",
            value="100.00",
            review_status="UNREVIEWED",
        )
        support_source = Source(
            source_id="S-SUPPORT-AMOUNT",
            file_id=support.file_id,
            document_id="D-SUPPORT",
            location=PageLocation(kind="PAGE", page=1),
            method="PDF_TEXT",
            raw_text="100.00",
            value="100.00",
            review_status="UNREVIEWED",
        )
        return ExtractionResult(
            extraction_version="fake-1",
            files=files,
            sources=[report_source, support_source],
            rows=[
                ExtractedRow(
                    row_id="R-1",
                    kind="ITEM",
                    amount_source_id=report_source.source_id,
                ),
                ExtractedRow(
                    row_id="A-1",
                    kind="ITEM",
                    amount_source_id=support_source.source_id,
                ),
            ],
        )


class _Matcher:
    def __init__(self, profile: CaseProfile) -> None:
        self.profile = profile
        self.calls = 0

    def __call__(self, extraction: ExtractionResult) -> list[Match]:
        self.calls += 1
        return [
            Match(
                match_id="M-1",
                report_row_id="R-1",
                support_row_ids=["A-1"],
                state="ACCEPTED",
            )
        ]


def _app(
    root: Path,
    *,
    events: list[ProgressEvent] | None = None,
    limits: PackageLimits | None = None,
) -> tuple[CaseApplication, _Extractor, _Matcher]:
    profile = _profile()
    extractor = _Extractor(profile)
    matcher = _Matcher(profile)
    application = CaseApplication.from_factories(
        root,
        profile,
        SCOPE,
        extractor_factory=lambda _: extractor,
        matcher_factory=lambda _: matcher,
        limits=limits or PackageLimits(max_total_bytes=1024),
        progress=events.append if events is not None else None,
    )
    return application, extractor, matcher


def test_run_saves_profile_and_replays_without_originals(tmp_path: Path) -> None:
    documents = _documents(tmp_path)
    events: list[ProgressEvent] = []
    application, extractor, matcher = _app(tmp_path / "runs", events=events)

    analysis = application.run(documents, run_id="case-run")

    assert analysis.run_id == "case-run"
    assert extractor.calls == 1
    assert matcher.calls == 1
    assert extractor.profile is application.profile
    assert matcher.profile is application.profile
    assert (tmp_path / "runs" / "case-run" / "case_profile.json").is_file()
    assert [event.stage for event in events] == [
        "SNAPSHOT",
        "EXTRACT",
        "EXTRACT",
        "MATCH",
        "MATCH",
        "ANALYZE",
        "ANALYZE",
        "PUBLISH",
        "PUBLISH",
    ]

    stored = application.load("case-run")
    assert stored.profile == application.profile
    for document in documents:
        document.path.unlink()
    assert application.replay("case-run") == analysis
    assert extractor.calls == 1
    assert matcher.calls == 1


def test_correction_adds_revision_and_keeps_old_revision(tmp_path: Path) -> None:
    application, _, _ = _app(tmp_path / "runs")
    documents = _documents(tmp_path)
    application.run(documents, run_id="case-run")
    original = application.load("case-run")

    corrected = application.correct(
        "case-run",
        expected_revision=1,
        source_id="S-REPORT-AMOUNT",
        value="120.00",
        comment="act checked",
    )

    assert corrected.revision == 2
    assert corrected.extraction.sources[0].value == "120.00"
    assert corrected.extraction.sources[0].review_status == "CORRECTED"
    assert corrected.history[0].old_value == "100.00"
    assert corrected.history[0].new_value == "120.00"
    assert corrected.profile == original.profile
    assert application.load("case-run", 1) == original
    with pytest.raises(RevisionConflictError):
        application.correct(
            "case-run",
            expected_revision=1,
            source_id="S-REPORT-AMOUNT",
            value="130.00",
        )


def test_technical_role_is_rejected_before_extraction(tmp_path: Path) -> None:
    application, extractor, _ = _app(tmp_path / "runs")
    documents = _documents(tmp_path)
    documents[1] = InputDocument(documents[1].path, "TECHNICAL")

    with pytest.raises(StorageError, match="non-working input roles"):
        application.run(documents, run_id="case-run")
    assert extractor.calls == 0
    assert not (tmp_path / "runs" / "case-run").exists()


def test_user_limits_are_checked_before_extraction(tmp_path: Path) -> None:
    application, extractor, _ = _app(
        tmp_path / "runs",
        limits=PackageLimits(max_total_bytes=1),
    )
    documents = _documents(tmp_path)

    with pytest.raises(PackageLimitError, match="package size"):
        application.run(documents, run_id="case-run")
    assert extractor.calls == 0
    assert not (tmp_path / "runs" / "case-run").exists()
