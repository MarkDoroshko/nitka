from collections.abc import Sequence
from typing import Final, Literal
from uuid import uuid4

from nitka.analysis.matching import Matcher
from nitka.analysis.service import check_report
from nitka.contracts.analysis import AnalysisResult, Coverage, Match, Scope
from nitka.contracts.extraction import ExtractionResult
from nitka.contracts.inputs import input_manifest_hash as compute_input_manifest_hash
from nitka.extraction.ports import Extractor, InputDocument

RULES_VERSION: Final[str] = "amount-and-totals-2"


def analyze_extraction(
    extraction: ExtractionResult,
    matches: Sequence[Match],
    scope: Scope,
    *,
    run_id: str | None = None,
    revision: int = 1,
    input_manifest_hash: str | None = None,
) -> AnalysisResult:
    checks = check_report(extraction, matches, scope)
    completed = sum(check.state != "NOT_RUN" for check in checks)
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
    elif execution != "COMPLETED" or completed < len(checks):
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
        coverage=Coverage(
            report_rows=len(checks),
            checked_rows=completed,
            required_checks=len(checks),
            completed_checks=completed,
        ),
        files=extraction.files,
        sources=extraction.sources,
        matches=list(matches),
        checks=checks,
        issues=extraction.issues,
    )


def run_pipeline(
    documents: Sequence[InputDocument],
    scope: Scope,
    *,
    extractor: Extractor,
    matcher: Matcher,
) -> AnalysisResult:
    extraction = extractor(documents)
    return analyze_extraction(extraction, matcher(extraction), scope)
