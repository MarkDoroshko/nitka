from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from openpyxl import Workbook

import nitka.storage as storage
from nitka.analysis.matching import Matcher
from nitka.contracts.analysis import AnalysisResult, Match, Scope
from nitka.contracts.extraction import (
    ExtractedRow,
    ExtractionResult,
    Issue,
    PageLocation,
    Source,
)
from nitka.contracts.inputs import InputManifest, input_manifest_hash
from nitka.contracts.revisions import AmountCorrection
from nitka.corrections import correct_amount
from nitka.extraction.limits import PackageLimitError, PackageLimits
from nitka.extraction.ports import Extractor, InputDocument
from nitka.extraction.registry import InputRegistry
from nitka.extraction.xlsx_report import XlsxReportExtractor
from nitka.pipeline import analyze_extraction
from nitka.storage import (
    CorruptRunError,
    IncompatibleRulesError,
    RevisionConflictError,
    RunExistsError,
    StorageError,
    correct_run_amount,
    load_run,
    replay_run,
    run_and_save,
)

SCOPE = Scope(currency="RUB", amount_basis="GROSS", reporting_period="2026-10")


def test_replays_run_written_before_repository_refactor() -> None:
    root = Path(__file__).parent / "fixtures" / "pre_refactor_runs"
    stored = load_run(root, "legacy-run")
    replayed = replay_run(root, "legacy-run")

    assert replayed == stored.analysis
    assert replayed.decision == "DISCREPANCIES_FOUND"
    assert replayed.revision == 1
    assert replayed.checks[0].calculation is not None
    assert replayed.checks[0].calculation.delta == "30000.00"
    assert set(stored.inputs) == {"F-1", "F-2"}


def test_matcher_failure_cleans_staging_and_allows_retry(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    documents = _documents(tmp_path)
    originals = {document.path: document.path.read_bytes() for document in documents}

    def failed_matcher(extraction: ExtractionResult) -> list[Match]:
        assert extraction.rows
        raise ValueError("matcher unavailable")

    with pytest.raises(StorageError, match="matcher unavailable") as error:
        _run(root, documents, matcher=failed_matcher)

    assert isinstance(error.value.__cause__, ValueError)
    assert list(root.iterdir()) == []
    assert all(path.read_bytes() == data for path, data in originals.items())
    result = _run(root, documents)
    assert replay_run(root, result.run_id) == result


def _write_report(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Report"
    for cell, value in {
        "A1": "Договор",
        "B1": "Наименование",
        "D1": "Сумма",
    }.items():
        sheet[cell] = value
    sheet["A2"] = "0012"
    sheet["B2"] = "Услуги"
    sheet["D2"] = "150 000,00"
    sheet["B3"] = "Итого"
    sheet["D3"] = 120000
    workbook.save(path)
    workbook.close()


def _documents(base: Path) -> list[InputDocument]:
    report = base / "report.xlsx"
    support = base / "act.pdf"
    _write_report(report)
    support.write_bytes(b"%PDF-1.4\nsupport document")
    return [
        InputDocument(path=report, role="REPORT"),
        InputDocument(path=support, role="SUPPORT"),
    ]


def _package_extractor(documents: Sequence[InputDocument]) -> ExtractionResult:
    registry = InputRegistry.register(documents)
    report = next(entry for entry in registry.inputs if entry.role == "REPORT")
    support = next(entry for entry in registry.inputs if entry.role == "SUPPORT")
    report_result = XlsxReportExtractor(registry).extract(report.file_id)
    support_source = Source(
        source_id=f"S-{support.file_id}-PAGE-1",
        file_id=support.file_id,
        document_id=f"D-{support.file_id}",
        location=PageLocation(kind="PAGE", page=1),
        method="VISION",
        raw_text="120 000,00",
        value="120000.00",
        review_status="UNREVIEWED",
    )
    support_row = ExtractedRow(
        row_id="A-1",
        kind="ITEM",
        amount_source_id=support_source.source_id,
    )
    return ExtractionResult(
        extraction_version="test-package-1",
        files=[*report_result.files, support.to_file_record("COMPLETED")],
        sources=[*report_result.sources, support_source],
        rows=[*report_result.rows, support_row],
        issues=[*report_result.issues],
    )


def _package_matcher(extraction: ExtractionResult) -> list[Match]:
    report_row = next(
        row for row in extraction.rows if row.kind == "ITEM" and row.fields
    )
    return [
        Match(
            match_id="M-1",
            report_row_id=report_row.row_id,
            support_row_ids=["A-1"],
            state="ACCEPTED",
        )
    ]


def _run(
    root: Path,
    documents: list[InputDocument],
    *,
    run_id: str = "run-1",
    extractor: Extractor = _package_extractor,
    matcher: Matcher = _package_matcher,
) -> AnalysisResult:
    return run_and_save(
        root,
        documents,
        SCOPE,
        extractor=extractor,
        matcher=matcher,
        run_id=run_id,
    )


def test_saves_evidence_and_replays_without_originals(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    documents = _documents(tmp_path)

    analysis = _run(root, documents)
    run_dir = root / "run-1"

    assert (run_dir / "input_manifest.json").is_file()
    assert (run_dir / "inputs" / "F-1.xlsx").is_file()
    assert (run_dir / "inputs" / "F-2.pdf").is_file()
    assert (run_dir / "revisions" / "1" / "extraction.json").is_file()
    assert (run_dir / "revisions" / "1" / "analysis.json").is_file()

    manifest = InputManifest.model_validate_json(
        (run_dir / "input_manifest.json").read_text(encoding="utf-8")
    )
    assert [file.role for file in manifest.files] == ["REPORT", "SUPPORT"]
    assert all(file.relative_path is not None for file in manifest.files)
    assert input_manifest_hash(manifest.files) == analysis.input_manifest_hash
    assert input_manifest_hash(list(reversed(manifest.files))) == (
        analysis.input_manifest_hash
    )

    saved_analysis = AnalysisResult.model_validate_json(
        (run_dir / "revisions" / "1" / "analysis.json").read_text(encoding="utf-8")
    )
    assert saved_analysis.decision == "DISCREPANCIES_FOUND"
    item_check = next(
        check for check in saved_analysis.checks if check.rule_id == "REPORT_AMOUNT"
    )
    total_check = next(
        check for check in saved_analysis.checks if check.rule_id == "REPORT_TOTAL"
    )
    assert item_check.calculation is not None
    assert item_check.calculation.delta == "30000.00"
    assert total_check.state == "FAIL"
    assert total_check.calculation is not None
    assert total_check.calculation.delta == "-30000.00"

    for source_id in item_check.evidence_ids:
        source = next(
            item for item in saved_analysis.sources if item.source_id == source_id
        )
        manifest_file = next(
            item for item in manifest.files if item.file_id == source.file_id
        )
        assert manifest_file.relative_path is not None
        saved_input = run_dir / manifest_file.relative_path
        assert saved_input.is_file()
        assert (
            hashlib.sha256(saved_input.read_bytes()).hexdigest() == manifest_file.sha256
        )
        assert source.location.kind in {"CELL", "PAGE"}

    for document in documents:
        document.path.unlink()

    stored = load_run(root, "run-1")
    assert stored.analysis == analysis
    replayed = replay_run(root, "run-1")
    assert replayed == stored.analysis
    assert replayed.checks == stored.analysis.checks


def test_same_original_names_do_not_overwrite(tmp_path: Path) -> None:
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    report = first / "same.xlsx"
    support = second / "same.xlsx"
    _write_report(report)
    support.write_bytes(b"support bytes")
    documents = [
        InputDocument(path=report, role="REPORT"),
        InputDocument(path=support, role="SUPPORT"),
    ]
    root = tmp_path / "runs"

    _run(root, documents)
    manifest = InputManifest.model_validate_json(
        (root / "run-1" / "input_manifest.json").read_text(encoding="utf-8")
    )

    assert [file.name for file in manifest.files] == ["same.xlsx", "same.xlsx"]
    assert len({file.relative_path for file in manifest.files}) == 2
    for file in manifest.files:
        assert file.relative_path is not None
        assert (root / "run-1" / file.relative_path).is_file()


def test_duplicate_run_id_keeps_previous_run(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    documents = _documents(tmp_path)
    first = _run(root, documents)

    with pytest.raises(RunExistsError):
        _run(root, documents)

    assert load_run(root, "run-1").analysis == first


def test_write_failure_does_not_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "runs"
    documents = _documents(tmp_path)

    def fail(path: Path, text: str) -> None:
        raise StorageError("simulated disk failure")

    monkeypatch.setattr(storage, "_write_json", fail)

    with pytest.raises(StorageError, match="simulated disk failure"):
        _run(root, documents, run_id="failed-run")

    assert not (root / "failed-run").exists()
    assert not any(path.name.startswith(".failed-run") for path in root.iterdir())


def test_modified_saved_input_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    documents = _documents(tmp_path)
    _run(root, documents)
    saved = root / "run-1" / "inputs" / "F-1.xlsx"
    saved.write_bytes(b"tampered")

    with pytest.raises(CorruptRunError, match="does not match"):
        load_run(root, "run-1")


def test_corrupt_json_is_diagnosed(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    documents = _documents(tmp_path)
    _run(root, documents)
    (root / "run-1" / "revisions" / "1" / "analysis.json").write_text("{")

    with pytest.raises(CorruptRunError, match="cannot read analysis.json"):
        load_run(root, "run-1")


def test_rules_version_mismatch_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "runs"
    documents = _documents(tmp_path)
    _run(root, documents)
    monkeypatch.setattr(storage, "RULES_VERSION", "amount-only-999")

    with pytest.raises(IncompatibleRulesError, match="amount-only-999"):
        replay_run(root, "run-1")


def test_manifest_path_outside_run_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    documents = _documents(tmp_path)
    _run(root, documents)
    manifest_path = root / "run-1" / "input_manifest.json"
    manifest = InputManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    manifest.files[0].relative_path = "../../outside.bin"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    with pytest.raises(CorruptRunError, match="escapes the run directory"):
        load_run(root, "run-1")


def test_missing_saved_input_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    documents = _documents(tmp_path)
    _run(root, documents)
    (root / "run-1" / "inputs" / "F-2.pdf").unlink()

    with pytest.raises(CorruptRunError, match="is missing"):
        load_run(root, "run-1")


def _no_matches(_: ExtractionResult) -> list[Match]:
    return []


def test_partial_result_is_saved_and_replayed(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    documents = _documents(tmp_path)

    def partial_extractor(documents: Sequence[InputDocument]) -> ExtractionResult:
        registry = InputRegistry.register(documents)
        files = registry.file_records({"F-1": "PARTIAL", "F-2": "COMPLETED"})
        return ExtractionResult(
            extraction_version="partial-1",
            files=files,
            sources=[],
            rows=[],
            issues=[
                Issue(
                    code="PAGE_UNREADABLE",
                    file_id="F-1",
                    detail="page 2 could not be read",
                )
            ],
        )

    analysis = _run(root, documents, extractor=partial_extractor, matcher=_no_matches)

    assert analysis.execution_status == "PARTIAL"
    stored = load_run(root, "run-1")
    assert stored.analysis.execution_status == "PARTIAL"
    assert stored.analysis.issues[0].code == "PAGE_UNREADABLE"
    assert replay_run(root, "run-1") == stored.analysis


def test_extractor_receives_snapshot_paths(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    documents = _documents(tmp_path)
    originals = {document.path.resolve() for document in documents}
    seen: list[Path] = []

    def extractor(items: Sequence[InputDocument]) -> ExtractionResult:
        seen.extend(item.path for item in items)
        return _package_extractor(items)

    _run(root, documents, extractor=extractor)

    assert seen
    assert all(path.resolve().is_relative_to(root.resolve()) for path in seen)
    assert all(path.resolve() not in originals for path in seen)


def _correct(
    root: Path, revision: int = 1, value: str = "120 000,00"
) -> storage.StoredRun:
    stored = load_run(root, "run-1", revision)
    source_id = next(
        row.amount_source_id
        for row in stored.extraction.rows
        if row.kind == "ITEM" and row.fields
    )
    return correct_run_amount(
        root,
        "run-1",
        expected_revision=revision,
        source_id=source_id,
        value=value,
        comment="  checked against act  ",
    )


def test_correction_revisions_are_immutable_and_replay_offline(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    documents = _documents(tmp_path)
    original = _run(root, documents)
    original_bytes = {
        path: path.read_bytes()
        for path in (root / "run-1").rglob("*")
        if path.is_file()
    }
    for document in documents:
        document.path.unlink()
    corrected = _correct(root)
    assert corrected.revision == 2
    assert corrected.analysis.decision == "NO_DISCREPANCIES"
    assert corrected.manifest_hash == original.input_manifest_hash
    assert corrected.analysis.run_id == original.run_id
    assert corrected.analysis.scope == original.scope
    assert corrected.analysis.matches == original.matches
    assert len(corrected.history) == 1
    event = corrected.history[0]
    assert (event.parent_revision, event.revision) == (1, 2)
    assert (event.old_value, event.new_value) == ("150000.00", "120000.00")
    assert event.comment == "checked against act"
    assert all(path.read_bytes() == data for path, data in original_bytes.items())
    assert replay_run(root, "run-1") == original
    assert replay_run(root, "run-1", 2) == corrected.analysis
    third = _correct(root, 2, "110000")
    assert [event.revision for event in third.history] == [2, 3]
    assert third.history[-1].old_value == "120000.00"
    assert replay_run(root, "run-1", 3) == third.analysis


def test_stale_correction_cannot_overwrite_a_revision(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    _run(root, _documents(tmp_path))
    saved = _correct(root)
    with pytest.raises(RevisionConflictError):
        _correct(root, 1, "100000")
    assert load_run(root, "run-1", 2) == saved
    assert not (root / "run-1/revisions/3").exists()


@pytest.mark.parametrize("failure", ["write", "rename"])
def test_failed_correction_leaves_original_usable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    root = tmp_path / "runs"
    original = _run(root, _documents(tmp_path))

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("injected publication failure")

    if failure == "write":
        monkeypatch.setattr(storage, "_write_json", fail)
    else:
        monkeypatch.setattr(Path, "rename", fail)
    with pytest.raises(StorageError, match="injected"):
        _correct(root)
    assert list((root / "run-1/revisions").iterdir()) == [root / "run-1/revisions/1"]
    assert replay_run(root, "run-1") == original


def test_competing_publisher_keeps_winning_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runs"
    _run(root, _documents(tmp_path))
    rename = Path.rename
    winning_bytes: dict[Path, bytes] = {}

    def competing_rename(path: Path, target: Path) -> Path:
        # Publish another complete revision after the existence check but before
        # this writer's rename, simulating the final publication race.
        monkeypatch.setattr(Path, "rename", rename)
        winner = _correct(root, value="100000")
        winning_bytes.update(
            {p: p.read_bytes() for p in winner.revision_dir.iterdir() if p.is_file()}
        )
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", competing_rename)
    with pytest.raises(RevisionConflictError):
        _correct(root)
    assert all(path.read_bytes() == value for path, value in winning_bytes.items())
    assert replay_run(root, "run-1", 2).decision == "DISCREPANCIES_FOUND"
    assert sorted(p.name for p in (root / "run-1/revisions").iterdir()) == ["1", "2"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("old_value", "999"),
        ("new_value", "1"),
        ("source_id", "missing"),
        ("parent_revision", 2),
        ("revision", 3),
    ],
)
def test_corrupt_correction_history_is_rejected(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    root = tmp_path / "runs"
    _run(root, _documents(tmp_path))
    corrected = _correct(root)
    path = corrected.revision_dir / storage.CORRECTION_NAME
    data = json.loads(path.read_bytes())
    data[field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CorruptRunError):
        load_run(root, "run-1", 2)
    assert replay_run(root, "run-1").revision == 1


def test_correction_preserves_partial_processing(tmp_path: Path) -> None:
    root = tmp_path / "runs"

    def extractor(documents: Sequence[InputDocument]) -> ExtractionResult:
        extraction = _package_extractor(documents)
        extraction.files[-1].status = "PARTIAL"
        extraction.issues.append(Issue(code="PAGE_UNREADABLE", detail="page 2 failed"))
        return extraction

    original = _run(root, _documents(tmp_path), extractor=extractor)
    corrected = _correct(root)
    assert corrected.analysis.execution_status == "PARTIAL"
    assert corrected.analysis.decision == "REVIEW_REQUIRED"
    assert corrected.analysis.issues == original.issues


def test_invalid_correction_and_incompatible_rules_publish_nothing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    _run(root, _documents(tmp_path))
    for value in ("NaN", "150000.000"):
        with pytest.raises(ValueError):
            _correct(root, value=value)
    path = root / "run-1/revisions/1/analysis.json"
    data = json.loads(path.read_bytes())
    data["rules_version"] = "future"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(IncompatibleRulesError):
        _correct(root)
    assert not (root / "run-1/revisions/2").exists()


def test_legacy_initial_revision_need_not_be_one(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_and_save(
        root,
        _documents(tmp_path),
        SCOPE,
        extractor=_package_extractor,
        matcher=_package_matcher,
        run_id="run-1",
        revision=5,
    )
    assert load_run(root, "run-1", 5).history == ()
    corrected = _correct(root, 5)
    assert corrected.revision == 6
    assert replay_run(root, "run-1", 6) == corrected.analysis


@pytest.mark.parametrize("missing_revision", [2, 3])
def test_missing_audit_metadata_is_not_treated_as_legacy(
    tmp_path: Path,
    missing_revision: int,
) -> None:
    root = tmp_path / "runs"
    _run(root, _documents(tmp_path))
    _correct(root)
    _correct(root, 2, "100000")
    (root / f"run-1/revisions/{missing_revision}/correction.json").unlink()
    with pytest.raises(CorruptRunError, match="missing correction"):
        load_run(root, "run-1", 3)
    with pytest.raises(CorruptRunError, match="missing correction"):
        replay_run(root, "run-1", 3)
    assert replay_run(root, "run-1").revision == 1


def test_long_history_is_iterative_and_verifies_shared_inputs_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runs"
    _run(root, _documents(tmp_path))
    stored = load_run(root, "run-1")
    source_id = stored.extraction.rows[0].amount_source_id
    extraction = stored.extraction
    # Build a chain exceeding the usual recursion limit without invoking the
    # public append operation O(N) times for test setup.
    for revision in range(2, 1051):
        old_value = next(
            s.value for s in extraction.sources if s.source_id == source_id
        )
        new_value = "120000.00" if revision % 2 == 0 else "150000.00"
        extraction = correct_amount(extraction, source_id, new_value)
        analysis = analyze_extraction(
            extraction,
            stored.analysis.matches,
            stored.analysis.scope,
            run_id="run-1",
            revision=revision,
            input_manifest_hash=stored.manifest_hash,
        )
        event = AmountCorrection(
            parent_revision=revision - 1,
            revision=revision,
            source_id=source_id,
            old_value=old_value,
            new_value=new_value,
        )
        revision_dir = root / f"run-1/revisions/{revision}"
        revision_dir.mkdir()
        for name, model in (
            (storage.EXTRACTION_NAME, extraction),
            (storage.ANALYSIS_NAME, analysis),
            (storage.CORRECTION_NAME, event),
        ):
            (revision_dir / name).write_text(model.model_dump_json(), encoding="utf-8")
    verify = storage._verify_inputs
    calls = 0

    def counted(run_dir: Path, manifest: InputManifest) -> dict[str, Path]:
        nonlocal calls
        calls += 1
        return verify(run_dir, manifest)

    monkeypatch.setattr(storage, "_verify_inputs", counted)
    result = load_run(root, "run-1", 1050)
    assert len(result.history) == 1049
    assert calls == 1
    calls = 0
    assert replay_run(root, "run-1", 1050) == result.analysis
    assert calls == 1
    calls = 0
    corrected = correct_run_amount(
        root,
        "run-1",
        expected_revision=1050,
        source_id=source_id,
        value="110000",
    )
    assert corrected.revision == 1051
    assert calls == 1


def test_oversize_package_is_rejected_before_staging(tmp_path: Path) -> None:
    report = tmp_path / "report.xlsx"
    report.write_bytes(b"x" * 100)
    called = False

    def extractor(documents: Sequence[InputDocument]) -> ExtractionResult:
        nonlocal called
        called = True
        return ExtractionResult(
            extraction_version="never",
            files=[],
            sources=[],
            rows=[],
        )

    def matcher(extraction: ExtractionResult) -> list[Match]:
        return []

    with pytest.raises(PackageLimitError, match="package size"):
        run_and_save(
            tmp_path / "runs",
            [InputDocument(report, "REPORT")],
            SCOPE,
            extractor=extractor,
            matcher=matcher,
            run_id="too-big",
            limits=PackageLimits(max_total_bytes=10),
        )

    assert called is False
    assert not (tmp_path / "runs" / "too-big").exists()
