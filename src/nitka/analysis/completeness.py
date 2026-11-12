"""Coverage, missing-evidence and decision policy for a case run.

The module keeps three ideas separate:

* report rows vs checks;
* confirmed arithmetic discrepancies vs missing evidence;
* the KFZ's internal arithmetic vs the independent support total.

Missing months or amounts become NOT_RUN/REVIEW_REQUIRED, never a zero or a
proven overstatement. A confirmed discrepancy can coexist with PARTIAL.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from nitka.analysis.aggregation import article_code_for_row
from nitka.contracts.analysis import Check, Coverage
from nitka.contracts.extraction import ExtractionResult, Issue, Source
from nitka.contracts.profile import CaseProfile

CheckCategory = Literal[
    "AMOUNT", "PERIOD", "CONTRACT", "DUPLICATE", "MISSING_DATA", "OTHER"
]
Decision = Literal[
    "NO_DISCREPANCIES", "DISCREPANCIES_FOUND", "REVIEW_REQUIRED", "NOT_ASSESSED"
]
Execution = Literal["COMPLETED", "PARTIAL", "FAILED"]

_MONTHS: Final[dict[str, int]] = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}


@dataclass(frozen=True)
class CompletenessPolicy:
    """Configurable evidence expected for a case.

    Empty ``required_articles`` means "derive from report article rows". The
    monthly requirement applies only to listed articles; other articles need
    ``minimum_support_rows_per_article``.
    """

    required_articles: tuple[str, ...] = ()
    monthly_articles: tuple[str, ...] = ()
    required_months: tuple[str, ...] = ()
    minimum_support_rows_per_article: int = 1

    def __post_init__(self) -> None:
        if self.minimum_support_rows_per_article < 1:
            raise ValueError("minimum_support_rows_per_article must be >= 1")
        if self.monthly_articles and not self.required_months:
            raise ValueError("monthly_articles require required_months")

    @classmethod
    def from_profile(cls, profile: CaseProfile) -> CompletenessPolicy:
        return cls(
            required_articles=tuple(profile.article_codes),
            monthly_articles=("0200", "0300"),
            required_months=tuple(profile.month_labels()),
        )


@dataclass(frozen=True)
class CompletenessAssessment:
    checks: tuple[Check, ...]
    issues: tuple[Issue, ...]
    coverage: Coverage
    execution_status: Execution
    decision: Decision
    categories: dict[str, int]
    incomplete_articles: tuple[str, ...]
    missing_months: tuple[tuple[str, str], ...]
    report_row_ids: tuple[str, ...]
    checked_row_ids: tuple[str, ...]


def assess_completeness(
    extraction: ExtractionResult,
    checks: list[Check],
    policy: CompletenessPolicy | None = None,
) -> CompletenessAssessment:
    policy = policy or CompletenessPolicy()
    sources = {source.source_id: source for source in extraction.sources}
    files = {file.file_id: file for file in extraction.files}
    report_rows = [
        row
        for row in extraction.rows
        if row.kind in {"ITEM", "UNKNOWN"}
        and row.amount_source_id in sources
        and files.get(sources[row.amount_source_id].file_id) is not None
        and files[sources[row.amount_source_id].file_id].role == "REPORT"
    ]
    report_article = {
        row.row_id: article_code_for_row(row, sources) for row in report_rows
    }
    required = policy.required_articles or tuple(
        sorted({code for code in report_article.values() if code is not None})
    )
    if policy.required_articles:
        required = tuple(sorted(set(required)))
    support = _support_coverage(extraction, sources, files, required, policy)
    issues: list[Issue] = []
    missing_months: list[tuple[str, str]] = []
    incomplete: set[str] = set()

    for code in required:
        support_coverage = support.get(code)
        if (
            support_coverage is None
            or support_coverage.rows < policy.minimum_support_rows_per_article
        ):
            incomplete.add(code)
            issues.append(
                Issue(
                    code="MISSING_ARTICLE",
                    detail=f"article {code}: insufficient support rows",
                )
            )
            continue
        if code in policy.monthly_articles:
            missing = [
                month
                for month in policy.required_months
                if month not in support_coverage.months
            ]
            if missing:
                incomplete.add(code)
                for month in missing:
                    missing_months.append((code, month))
                    issues.append(
                        Issue(
                            code="MISSING_MONTH",
                            detail=f"article {code}: no support for {month}",
                        )
                    )
        if support_coverage.missing_amount:
            incomplete.add(code)
            issues.append(
                Issue(
                    code="MISSING_AMOUNT",
                    detail=f"article {code}: a support amount is missing",
                )
            )
    if support:
        for code, support_coverage in support.items():
            if (
                code in required
                and support_coverage.missing_amount
                and code not in incomplete
            ):
                incomplete.add(code)

    adjusted = [_downgrade(check, report_article, incomplete) for check in checks]
    checked_rows = _checked_rows(adjusted, report_article)
    completed_checks = sum(check.state != "NOT_RUN" for check in adjusted)
    coverage_summary = Coverage(
        report_rows=len(report_rows),
        checked_rows=len(checked_rows),
        required_checks=len(adjusted),
        completed_checks=completed_checks,
    )
    execution = _execution_status(extraction, issues, adjusted)
    decision = _decision(adjusted, execution, issues, completed_checks)
    categories = _categories(adjusted, issues)
    return CompletenessAssessment(
        checks=tuple(adjusted),
        issues=tuple(issues),
        coverage=coverage_summary,
        execution_status=execution,
        decision=decision,
        categories=categories,
        incomplete_articles=tuple(sorted(incomplete)),
        missing_months=tuple(missing_months),
        report_row_ids=tuple(row.row_id for row in report_rows),
        checked_row_ids=checked_rows,
    )


@dataclass
class _SupportCoverage:
    rows: int = 0
    months: set[str] = field(default_factory=set)
    missing_amount: bool = False


def _support_coverage(
    extraction: ExtractionResult,
    sources: Mapping[str, Source],
    files: Mapping[str, object],
    required: tuple[str, ...],
    policy: CompletenessPolicy,
) -> dict[str, _SupportCoverage]:
    result: dict[str, _SupportCoverage] = defaultdict(_SupportCoverage)
    seen_sources: set[str] = set()
    for row in extraction.rows:
        if row.kind != "ITEM":
            continue
        source = sources.get(row.amount_source_id)
        if source is None:
            continue
        file_record = files.get(source.file_id)
        if file_record is None or getattr(file_record, "role", None) != "SUPPORT":
            continue
        code = article_code_for_row(row, sources)
        if code is None or (required and code not in required):
            continue
        if source.source_id in seen_sources:
            continue
        seen_sources.add(source.source_id)
        coverage = result[code]
        coverage.rows += 1
        if source.value is None or not source.value.strip():
            coverage.missing_amount = True
        period = _field_value(row, sources, "service_period", "period")
        if period:
            month = _parse_month(period)
            if month is not None:
                coverage.months.add(month)
    return dict(result)


def _downgrade(
    check: Check,
    report_article: Mapping[str, str | None],
    incomplete: set[str],
) -> Check:
    code = _check_article(check, report_article)
    if code is None:
        if check.target_id != "OVERALL" or not incomplete:
            return check
    elif code not in incomplete:
        return check
    return check.model_copy(
        update={
            "state": "NOT_RUN",
            "reason_code": "INSUFFICIENT_DOCUMENTS",
            "calculation": None,
        }
    )


def _check_article(
    check: Check, report_article: Mapping[str, str | None]
) -> str | None:
    if check.target_id.startswith("ARTICLE:"):
        return check.target_id.split(":", 1)[1]
    return report_article.get(check.target_id)


def _checked_rows(
    checks: list[Check], report_article: Mapping[str, str | None]
) -> tuple[str, ...]:
    result: list[str] = []
    for check in checks:
        if check.rule_id != "REPORT_AMOUNT":
            continue
        if check.target_id not in report_article:
            continue
        if check.state == "NOT_RUN":
            continue
        result.append(check.target_id)
    return tuple(dict.fromkeys(result))


def _execution_status(
    extraction: ExtractionResult,
    issues: list[Issue],
    checks: list[Check],
) -> Execution:
    if not extraction.files or all(
        file.status == "FAILED" for file in extraction.files
    ):
        return "FAILED"
    if (
        issues
        or extraction.issues
        or any(file.status != "COMPLETED" for file in extraction.files)
        or any(check.state == "NOT_RUN" for check in checks)
    ):
        return "PARTIAL"
    return "COMPLETED"


def _decision(
    checks: list[Check],
    execution: Execution,
    issues: list[Issue],
    completed_checks: int,
) -> Decision:
    if not completed_checks:
        return "NOT_ASSESSED"
    if any(check.state == "FAIL" for check in checks):
        return "DISCREPANCIES_FOUND"
    if (
        execution != "COMPLETED"
        or issues
        or any(check.state == "NOT_RUN" for check in checks)
    ):
        return "REVIEW_REQUIRED"
    return "NO_DISCREPANCIES"


def _categories(checks: list[Check], issues: list[Issue]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for check in checks:
        counts[_category(check.reason_code)] += 1
    for issue in issues:
        counts[_category(issue.code)] += 1
    return dict(counts)


def _category(code: str) -> CheckCategory:
    upper = code.upper()
    if "PERIOD" in upper or "MONTH" in upper:
        return "PERIOD"
    if "CONTRACT" in upper or upper in {"MISSING_SUPPORT", "AMBIGUOUS_MATCH"}:
        return "CONTRACT"
    if "DUPLICATE" in upper:
        return "DUPLICATE"
    if (
        upper.startswith("MISSING")
        or upper.startswith("INSUFFICIENT")
        or upper in {"NO_ITEMS", "UNKNOWN_ROW_KIND", "PAGE_BOUNDARY_UNKNOWN"}
    ):
        return "MISSING_DATA"
    if any(
        token in upper for token in ("AMOUNT", "ARTICLE", "TOTAL", "OVERALL", "FORMULA")
    ):
        return "AMOUNT"
    return "OTHER"


def _field_value(row: object, sources: Mapping[str, Source], *names: str) -> str | None:
    fields = getattr(row, "fields", {})
    for name in names:
        source = sources.get(fields.get(name, ""))
        if source is None:
            continue
        value = source.value if source.value is not None else source.raw_text
        if value:
            return value
    return None


def _parse_month(value: str) -> str | None:
    text = value.casefold()
    match = re.search(r"(?P<year>20\d{2})\s*[-./]\s*(?P<month>\d{1,2})", text)
    if match is None:
        match = re.search(r"(?P<month>\d{1,2})\s*[-./]\s*(?P<year>20\d{2})", text)
    if match is not None:
        month = int(match.group("month"))
        if 1 <= month <= 12:
            return f"{int(match.group('year')):04d}-{month:02d}"
        return None
    for stem, month in _MONTHS.items():
        if stem in text:
            year_match = re.search(r"\b(20\d{2})\b", text)
            if year_match:
                return f"{int(year_match.group(1)):04d}-{month:02d}"
            return None
    return None
