"""Application facade: one profile, one extractor, one matcher, one run store."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from nitka.analysis.matching import Matcher
from nitka.contracts.analysis import AnalysisResult, Scope
from nitka.contracts.profile import CaseProfile
from nitka.extraction.limits import DEFAULT_PACKAGE_LIMITS, PackageLimits
from nitka.extraction.ports import Extractor, InputDocument, ProfileExtractor
from nitka.run_workflows import ProgressSink
from nitka.storage import (
    StoredRun,
    correct_run_amount,
    load_run,
    replay_run,
    run_and_save,
)


class CaseApplication:
    """Compose the effective profile with extraction, matching and persistence.

    ``from_factories`` is the preferred construction path: both adapters receive
    the same profile in their constructors. The profile is also written next to
    the run and returned by ``load``/``replay``.
    """

    def __init__(
        self,
        root: Path | str,
        profile: CaseProfile,
        scope: Scope,
        *,
        extractor: Extractor,
        matcher: Matcher,
        limits: PackageLimits = DEFAULT_PACKAGE_LIMITS,
        progress: ProgressSink | None = None,
    ) -> None:
        self.root = Path(root)
        self.profile = profile
        self.scope = scope
        self.extractor = extractor
        self.matcher = matcher
        self.limits = limits
        self.progress = progress

    @classmethod
    def from_factories(
        cls,
        root: Path | str,
        profile: CaseProfile,
        scope: Scope,
        *,
        extractor_factory: Callable[[CaseProfile], ProfileExtractor],
        matcher_factory: Callable[[CaseProfile], Matcher],
        limits: PackageLimits = DEFAULT_PACKAGE_LIMITS,
        progress: ProgressSink | None = None,
    ) -> CaseApplication:
        return cls(
            root,
            profile,
            scope,
            extractor=extractor_factory(profile),
            matcher=matcher_factory(profile),
            limits=limits,
            progress=progress,
        )

    def run(
        self,
        documents: Sequence[InputDocument],
        *,
        run_id: str | None = None,
    ) -> AnalysisResult:
        return run_and_save(
            self.root,
            documents,
            self.scope,
            extractor=self.extractor,
            matcher=self.matcher,
            run_id=run_id,
            revision=1,
            limits=self.limits,
            profile=self.profile,
            progress=self.progress,
        )

    def load(self, run_id: str, revision: int = 1) -> StoredRun:
        return load_run(self.root, run_id, revision)

    def replay(self, run_id: str, revision: int = 1) -> AnalysisResult:
        # The effective profile is persisted with the run; replay uses the saved
        # matches and the stored profile, not a newly supplied global/default.
        stored = self.load(run_id, revision)
        if stored.profile is None:
            raise ValueError("stored run has no effective case profile")
        return replay_run(self.root, run_id, revision)

    def correct(
        self,
        run_id: str,
        *,
        expected_revision: int,
        source_id: str,
        value: str,
        comment: str = "",
    ) -> StoredRun:
        return correct_run_amount(
            self.root,
            run_id,
            expected_revision=expected_revision,
            source_id=source_id,
            value=value,
            comment=comment,
        )
