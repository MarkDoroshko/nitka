from collections.abc import Sequence
from typing import Final, Literal
from uuid import uuid4

from nitka.analysis.completeness import calculate_coverage, unverified_breakdowns
from nitka.analysis.matching import CaseMatcher, Matcher, MatchOutcome
from nitka.analysis.service import check_report
from nitka.contracts.analysis import AnalysisResult, Match, Scope
from nitka.contracts.extraction import ExtractionResult
from nitka.contracts.inputs import input_manifest_hash as compute_input_manifest_hash
from nitka.extraction.ports import Extractor, InputDocument

RULES_VERSION: Final[str] = "amount-and-totals-3"


def analyze_extraction(
    extraction: ExtractionResult,
    matches: Sequence[Match] | MatchOutcome,
    scope: Scope,
    *,
    run_id: str | None = None,
    revision: int = 1,
    input_manifest_hash: str | None = None,
) -> AnalysisResult:
    outcome = matches if isinstance(matches, MatchOutcome) else None
    links = outcome.matches if outcome is not None else matches
    assert not isinstance(links, MatchOutcome)
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
    elif extraction.issues or any(
        file.status != "COMPLETED" for file in extraction.files
    ):
        execution = "PARTIAL"
    else:
        execution = "COMPLETED"

    decision: Literal[
        "NO_DISCREPANCIES", "DISCREPANCIES_FOUND", "REVIEW_REQUIRED", "NOT_ASSESSED"
    ]
    if not completed:
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
        issues=[*extraction.issues, *(outcome.issues if outcome is not None else ())],
        excluded_documents=list(outcome.excluded_documents) if outcome is not None else [],
    )


def run_pipeline(
    documents: Sequence[InputDocument],
    scope: Scope,
    *,
    extractor: Extractor,
    matcher: Matcher,
) -> AnalysisResult:
    extraction = extractor(documents)
    matches = matcher.match(extraction) if isinstance(matcher, CaseMatcher) else matcher(extraction)
    return analyze_extraction(extraction, matches, scope)
