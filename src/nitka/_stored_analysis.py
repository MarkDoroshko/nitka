from collections.abc import Sequence

from nitka.analysis.matching import CaseMatcher
from nitka.contracts.analysis import AnalysisResult, CaseMatching, Match, StaticMatching
from nitka.pipeline import CASE_MATCHER_VERSION, evaluate_extraction

from ._storage_models import (
    CorruptRunError,
    IncompatibleRulesError,
    ReplayMismatchError,
    StoredRun,
)


def stored_matcher(analysis: AnalysisResult, rules_version: str) -> CaseMatcher | Sequence[Match]:
    if analysis.rules_version != rules_version:
        raise IncompatibleRulesError(
            f"stored rules_version {analysis.rules_version!r} cannot be reproduced with "
            f"local {rules_version!r}; run a new verification before editing"
        )
    context = analysis.matching_context
    if isinstance(context, CaseMatching):
        if context.matcher_version != CASE_MATCHER_VERSION:
            raise IncompatibleRulesError(
                f"stored matcher version {context.matcher_version!r} cannot be reproduced; "
                "run a new verification before editing"
            )
        return CaseMatcher(context.profile)
    if isinstance(context, StaticMatching):
        return analysis.matches
    raise CorruptRunError("current analysis is missing matching context")


def replay_stored(stored: StoredRun, rules_version: str) -> AnalysisResult:
    matcher = stored_matcher(stored.analysis, rules_version)
    try:
        recomputed = evaluate_extraction(
            stored.extraction,
            matcher,
            stored.analysis.scope,
            run_id=stored.run_id,
            revision=stored.revision,
            input_manifest_hash=stored.manifest_hash,
        )
    except ValueError as exc:
        raise ReplayMismatchError(f"cannot recompute stored analysis: {exc}") from exc
    if recomputed != stored.analysis:
        raise ReplayMismatchError("recomputed analysis differs from the stored analysis")
    return recomputed
