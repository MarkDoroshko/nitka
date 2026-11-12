"""Local, portable storage for a run and its evidence.

Layout::

    <root>/<run_id>/
        input_manifest.json
        inputs/<file_id>.<suffix>
        revisions/<revision>/extraction.json
        revisions/<revision>/analysis.json

A run is assembled in a temporary directory next to the target and renamed only
after every file has been written. The manifest hash covers file identity
(``file_id``, role, original name, size, SHA-256) and deliberately excludes
parsing status, paths and manifest ordering.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from nitka.analysis.matching import Matcher
from nitka.contracts.analysis import AnalysisResult, Scope
from nitka.contracts.profile import CaseProfile
from nitka.extraction.limits import (
    DEFAULT_PACKAGE_LIMITS,
    PackageLimitError,
    PackageLimits,
)
from nitka.extraction.ports import Extractor, InputDocument
from nitka.pipeline import RULES_VERSION

from . import run_workflows
from ._storage_files import (
    _write_json,
)
from ._storage_inputs import (
    _verify_inputs as _verify_inputs,
)
from ._storage_models import ANALYSIS_NAME as ANALYSIS_NAME
from ._storage_models import CORRECTION_NAME as CORRECTION_NAME
from ._storage_models import EXTRACTION_NAME as EXTRACTION_NAME
from ._storage_models import INPUTS_DIR as INPUTS_DIR
from ._storage_models import MANIFEST_NAME as MANIFEST_NAME
from ._storage_models import REVISIONS_DIR as REVISIONS_DIR
from ._storage_models import CorruptRunError as CorruptRunError
from ._storage_models import IncompatibleRulesError as IncompatibleRulesError
from ._storage_models import ReplayMismatchError as ReplayMismatchError
from ._storage_models import RevisionConflictError as RevisionConflictError
from ._storage_models import RunExistsError as RunExistsError
from ._storage_models import RunNotFoundError as RunNotFoundError
from ._storage_models import StorageError as StorageError
from ._storage_models import StoredRun as StoredRun
from .run_repository import LocalRunRepository
from .run_workflows import ProgressSink


class LegacyReplayPolicy:
    """Keep read-only access to runs produced by superseded rule engines.

    The old engine is not available, so its stored analysis is returned exactly
    as published. It is never silently recomputed with the current rules.
    """

    READ_ONLY_RULES: frozenset[str] = frozenset({"amount-only-1"})

    def replay(self, stored: StoredRun, rules_version: str) -> AnalysisResult:
        if stored.analysis.rules_version in self.READ_ONLY_RULES:
            return stored.analysis
        return run_workflows.replay_stored(stored, rules_version)


class PackageSizeGuard:
    """Reject an oversized working package before the repository copies it."""

    def __init__(self, limits: PackageLimits = DEFAULT_PACKAGE_LIMITS) -> None:
        self.limits = limits

    def check(self, documents: Sequence[InputDocument]) -> int:
        total = 0
        for document in documents:
            try:
                total += document.path.stat().st_size
            except OSError:
                # The repository reports a precise unreadable-input error later.
                continue
        if total > self.limits.max_total_bytes:
            raise PackageLimitError(
                f"package size {total} bytes exceeds "
                f"{self.limits.max_total_bytes} bytes"
            )
        return total


def _repository(root: Path | str) -> LocalRunRepository:
    return LocalRunRepository(
        root, write_json=_write_json, verify_inputs=_verify_inputs
    )


def run_and_save(
    root: Path | str,
    documents: Sequence[InputDocument],
    scope: Scope,
    *,
    extractor: Extractor,
    matcher: Matcher,
    run_id: str | None = None,
    revision: int = 1,
    limits: PackageLimits = DEFAULT_PACKAGE_LIMITS,
    profile: CaseProfile | None = None,
    progress: ProgressSink | None = None,
) -> AnalysisResult:
    """Snapshot inputs, run extraction/matching/analysis, publish the run."""
    PackageSizeGuard(limits).check(documents)
    return run_workflows.run_and_save(
        _repository(root),
        documents,
        scope,
        extractor=extractor,
        matcher=matcher,
        run_id=run_id,
        revision=revision,
        profile=profile,
        limits=limits,
        progress=progress,
    )


def load_run(root: Path | str, run_id: str, revision: int = 1) -> StoredRun:
    """Load a revision and its validated audit chain."""
    return _repository(root).load(run_id, revision)


def correct_run_amount(
    root: Path | str,
    run_id: str,
    *,
    expected_revision: int,
    source_id: str,
    value: str,
    comment: str = "",
) -> StoredRun:
    """Publish a value-only revision from the latest saved revision."""
    return run_workflows.correct_run_amount(
        _repository(root),
        run_id,
        expected_revision=expected_revision,
        source_id=source_id,
        value=value,
        comment=comment,
        rules_version=RULES_VERSION,
    )


def replay_run(root: Path | str, run_id: str, revision: int = 1) -> AnalysisResult:
    """Recompute a current run; old rule engines are read-only."""
    return LegacyReplayPolicy().replay(load_run(root, run_id, revision), RULES_VERSION)
