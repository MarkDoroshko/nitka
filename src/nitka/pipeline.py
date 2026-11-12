from collections.abc import Sequence
from typing import Final
from uuid import uuid4

from nitka.analysis.completeness import CompletenessPolicy, assess_completeness
from nitka.analysis.matching import Matcher
from nitka.analysis.service import check_report
from nitka.contracts.analysis import AnalysisResult, Match, Scope
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
    completeness: CompletenessPolicy | None = None,
) -> AnalysisResult:
    checks = check_report(extraction, matches, scope)
    assessment = assess_completeness(extraction, checks, completeness)
    manifest_hash = (
        input_manifest_hash
        if input_manifest_hash is not None
        else compute_input_manifest_hash(extraction.files)
    )
    analysis_issues = [*extraction.issues, *assessment.issues]

    return AnalysisResult(
        run_id=run_id or str(uuid4()),
        revision=revision,
        input_manifest_hash=manifest_hash,
        rules_version=RULES_VERSION,
        extraction_version=extraction.extraction_version,
        scope=scope,
        execution_status=assessment.execution_status,
        decision=assessment.decision,
        coverage=assessment.coverage,
        files=extraction.files,
        sources=extraction.sources,
        matches=list(matches),
        checks=list(assessment.checks),
        issues=analysis_issues,
    )


def run_pipeline(
    documents: Sequence[InputDocument],
    scope: Scope,
    *,
    extractor: Extractor,
    matcher: Matcher,
    completeness: CompletenessPolicy | None = None,
) -> AnalysisResult:
    extraction = extractor(documents)
    return analyze_extraction(
        extraction,
        matcher(extraction),
        scope,
        completeness=completeness,
    )
