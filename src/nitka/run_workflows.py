"""Application workflows over local run persistence and pure domain rules."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

from nitka.analysis.matching import CaseMatcher, Matcher
from nitka.contracts.analysis import AnalysisResult, Scope
from nitka.contracts.extraction import Issue
from nitka.contracts.inputs import input_manifest_hash
from nitka.contracts.revisions import AmountCorrection
from nitka.contracts.validation import validate_extraction
from nitka.corrections import correct_amount
from nitka.extraction.limits import (
    DEFAULT_PACKAGE_LIMITS,
    PackageLimits,
    enforce_package_size,
)
from nitka.extraction.package import PackageExtractor
from nitka.extraction.ports import Extractor, InputDocument
from nitka.pipeline import evaluate_extraction, require_matching_profiles

from ._storage_inputs import _bind_file_records
from ._storage_models import StorageError, StoredRun
from ._stored_analysis import stored_matcher
from .run_repository import LocalRunRepository


def run_and_save(
    repository: LocalRunRepository,
    documents: Sequence[InputDocument],
    scope: Scope,
    *,
    extractor: Extractor,
    matcher: Matcher,
    run_id: str | None = None,
    revision: int = 1,
    limits: PackageLimits = DEFAULT_PACKAGE_LIMITS,
) -> AnalysisResult:
    """Analyze preserved inputs and publish a complete run."""
    require_matching_profiles(extractor, matcher)
    if isinstance(matcher, CaseMatcher) and type(matcher) is not CaseMatcher:
        raise StorageError("only the built-in case matcher can be saved and replayed")
    run_id = run_id or str(uuid4())
    registry = None
    issues: list[Issue] = []
    if isinstance(extractor, PackageExtractor):
        registry, issues = extractor.register_inputs(documents)
        enforce_package_size(registry.inputs, limits)
    with repository.stage_run(run_id, documents, revision, registry=registry) as staged:
        manifest = staged.manifest
        manifest_hash = input_manifest_hash(manifest.files)
        extraction = (
            extractor.extract_registered(staged.registry, issues=issues).extraction
            if isinstance(extractor, PackageExtractor)
            else extractor(staged.documents)
        )
        _bind_file_records(extraction, manifest)
        validate_extraction(extraction)
        if input_manifest_hash(extraction.files) != manifest_hash:
            raise StorageError(
                "extractor file records do not match the input snapshot manifest"
            )
        analysis = evaluate_extraction(
            extraction,
            matcher,
            scope,
            run_id=run_id,
            revision=revision,
            input_manifest_hash=manifest_hash,
        )
        if analysis.input_manifest_hash != manifest_hash:
            raise StorageError("analysis manifest hash does not match the snapshot")

        repository.publish_run(staged, extraction, analysis)
    return analysis


def correct_run_amount(
    repository: LocalRunRepository,
    run_id: str,
    *,
    expected_revision: int,
    source_id: str,
    value: str,
    comment: str = "",
    rules_version: str,
) -> StoredRun:
    """Publish a value-only revision from the latest saved revision.

    Recompute case matching on the corrected extraction. A competing/stale edit
    raises RevisionConflictError; the caller must reopen the current result.
    """
    stored = repository.load(run_id, expected_revision)
    matcher = stored_matcher(stored.analysis, rules_version)
    repository.require_current_revision(stored)
    extraction = correct_amount(stored.extraction, source_id, value)
    revision = expected_revision + 1
    old_source = next(s for s in stored.extraction.sources if s.source_id == source_id)
    new_source = next(s for s in extraction.sources if s.source_id == source_id)
    assert new_source.value is not None
    correction = AmountCorrection(
        parent_revision=expected_revision,
        revision=revision,
        source_id=source_id,
        old_value=old_source.value,
        new_value=new_source.value,
        comment=comment.strip(),
    )
    analysis = evaluate_extraction(
        extraction,
        matcher,
        stored.analysis.scope,
        run_id=run_id,
        revision=revision,
        input_manifest_hash=stored.manifest_hash,
    )
    return repository.publish_correction(stored, extraction, analysis, correction)
