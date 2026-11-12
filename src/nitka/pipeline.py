from collections.abc import Sequence
from typing import Final, Literal
from uuid import uuid4

from nitka.analysis.completeness import calculate_coverage, unverified_breakdowns
from nitka.analysis.matching import CaseMatcher, Matcher
from nitka.analysis.service import check_report
from nitka.contracts.analysis import (
    AnalysisResult,
    CaseMatching,
    Match,
    MatchingContext,
    MatchOutcome,
    Scope,
    StaticMatching,
)
from nitka.contracts.extraction import ExtractionResult
from nitka.contracts.inputs import input_manifest_hash as compute_input_manifest_hash
from nitka.contracts.report_scope import REPORT_SCOPE_UNVERIFIED
from nitka.extraction.package import PackageExtractor
from nitka.extraction.ports import Extractor, InputDocument

RULES_VERSION: Final[str] = "amount-and-totals-4"
CASE_MATCHER_VERSION: Final[str] = "case-matcher-1"


def analyze_extraction(
    extraction: ExtractionResult,
    matches: Sequence[Match] | MatchOutcome,
    scope: Scope,
    *,
    run_id: str | None = None,
    revision: int = 1,
    input_manifest_hash: str | None = None,
    matching_context: MatchingContext | None = None,
) -> AnalysisResult:
    outcome = matches if isinstance(matches, MatchOutcome) else None
    links = outcome.matches if outcome is not None else matches
    assert not isinstance(links, MatchOutcome)
    issues = [*extraction.issues, *(outcome.issues if outcome is not None else ())]
    scope_blocked = any(issue.code == REPORT_SCOPE_UNVERIFIED for issue in issues)
    if scope_blocked:
        links = ()
    checks = check_report(
        extraction, links, scope,
        blocked_articles=outcome.blocked_articles if outcome is not None else None,
        required_articles=outcome.required_articles if outcome is not None else (),
    )
    checks.extend(unverified_breakdowns(extraction))
    coverage = calculate_coverage(extraction, checks)
    completed = coverage.completed_checks
    manifest_hash = (
        input_manifest_hash
        if input_manifest_hash is not None
        else compute_input_manifest_hash(extraction.files)
    )

    execution: Literal["COMPLETED", "PARTIAL", "FAILED"]
    if not extraction.files or all(
        file.status == "FAILED" for file in extraction.files
    ):
        execution = "FAILED"
    elif extraction.issues or scope_blocked or any(
        file.status != "COMPLETED" for file in extraction.files
    ):
        execution = "PARTIAL"
    else:
        execution = "COMPLETED"

    decision: Literal[
        "NO_DISCREPANCIES", "DISCREPANCIES_FOUND", "REVIEW_REQUIRED", "NOT_ASSESSED"
    ]
    if scope_blocked:
        decision = "REVIEW_REQUIRED"
    elif not completed:
        decision = "NOT_ASSESSED"
    elif any(check.state == "FAIL" for check in checks):
        decision = "DISCREPANCIES_FOUND"
    elif execution != "COMPLETED" or completed < len(checks) or coverage.checked_rows < coverage.report_rows:
        decision = "REVIEW_REQUIRED"
    else:
        decision = "NO_DISCREPANCIES"

    return AnalysisResult(
        run_id=run_id or str(uuid4()),
        revision=revision,
        input_manifest_hash=manifest_hash,
        rules_version=RULES_VERSION,
        extraction_version=extraction.extraction_version,
        scope=scope,
        execution_status=execution,
        decision=decision,
        coverage=coverage,
        files=extraction.files,
        sources=extraction.sources,
        matches=list(links),
        checks=checks,
        issues=issues,
        excluded_documents=list(outcome.excluded_documents) if outcome is not None else [],
        matching_context=(
            StaticMatching() if outcome is None and matching_context is None
            else matching_context
        ),
    )


def evaluate_extraction(
    extraction: ExtractionResult,
    matcher: Matcher | Sequence[Match],
    scope: Scope,
    *,
    run_id: str | None = None,
    revision: int = 1,
    input_manifest_hash: str | None = None,
) -> AnalysisResult:
    context = None
    matches: MatchOutcome | Sequence[Match]
    if type(matcher) is CaseMatcher:
        profile = matcher.profile.model_copy(deep=True)
        matches = CaseMatcher(profile).match(extraction)
        context = CaseMatching(
            profile=profile, matcher_version=CASE_MATCHER_VERSION, outcome=matches
        )
    elif isinstance(matcher, CaseMatcher):
        matches = matcher.match(extraction)
    else:
        matches = matcher(extraction) if callable(matcher) else matcher
    return analyze_extraction(
        extraction, matches, scope, run_id=run_id, revision=revision,
        input_manifest_hash=input_manifest_hash, matching_context=context,
    )


def require_matching_profiles(extractor: Extractor, matcher: Matcher) -> None:
    if isinstance(extractor, PackageExtractor) and isinstance(matcher, CaseMatcher):
        if extractor.profile != matcher.profile:
            raise ValueError("extractor and matcher must use the same case profile")


def run_pipeline(
    documents: Sequence[InputDocument],
    scope: Scope,
    *,
    extractor: Extractor,
    matcher: Matcher,
) -> AnalysisResult:
    require_matching_profiles(extractor, matcher)
    return evaluate_extraction(extractor(documents), matcher, scope)
