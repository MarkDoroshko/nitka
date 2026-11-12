"""Article aggregation and independent overall reconciliation.

Support totals are summed from item rows by article; printed subtotals and the
overall printed total are never added again. Decimal arithmetic uses the run
currency, so contribution totals are explicit evidence (never a universal rate).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from typing import Literal

from nitka.amounts import normalize_amount
from nitka.contracts.analysis import Calculation, Check, Operand, Scope
from nitka.contracts.extraction import ExtractedRow, ExtractionResult, Source

TOLERANCE = "0.00"
_ARTICLE_RE = re.compile(r"\b(0[2-9]00)\b")


@dataclass(frozen=True)
class ArticleAggregate:
    article_code: str
    amount: str | None
    source_ids: tuple[str, ...]
    row_ids: tuple[str, ...]
    missing: bool = False
    invalid: bool = False


@dataclass(frozen=True)
class CaseTotals:
    report: dict[str, ArticleAggregate]
    support: dict[str, ArticleAggregate]
    report_total: ArticleAggregate
    support_total: ArticleAggregate

    @property
    def article_codes(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.report) | set(self.support)))


@dataclass
class _AggregateBuilder:
    article_code: str
    total: Decimal = Decimal(0)
    source_ids: list[str] = field(default_factory=list)
    row_ids: list[str] = field(default_factory=list)
    missing: bool = False
    invalid: bool = False
    seen_sources: set[str] = field(default_factory=set)

    def add(self, row_id: str, source: Source) -> None:
        if source.source_id in self.seen_sources:
            return
        self.seen_sources.add(source.source_id)
        self.source_ids.append(source.source_id)
        self.row_ids.append(row_id)
        if source.value is None or not source.value.strip():
            self.missing = True
            return
        normalized = normalize_amount(source.value)
        if normalized is None:
            self.invalid = True
            return
        with localcontext() as context:
            context.prec = 50
            self.total += Decimal(normalized)

    def build(self) -> ArticleAggregate:
        amount = None if self.missing or self.invalid else format(self.total, "f")
        return ArticleAggregate(
            article_code=self.article_code,
            amount=amount,
            source_ids=tuple(self.source_ids),
            row_ids=tuple(self.row_ids),
            missing=self.missing,
            invalid=self.invalid,
        )


class CaseAggregator:
    """Aggregate report/support items by article without using subtotals."""

    def __init__(
        self,
        extraction: ExtractionResult,
        *,
        accepted_support_by_article: Mapping[str, set[str]] | None = None,
    ) -> None:
        self.extraction = extraction
        self.sources = {source.source_id: source for source in extraction.sources}
        self.files = {file.file_id: file for file in extraction.files}
        self.accepted_support_by_article = accepted_support_by_article or {}

    def totals(self) -> CaseTotals:
        report = self._collect("REPORT")
        support = self._collect("SUPPORT")
        report_total = self._report_total(report)
        support_total = _sum_aggregates("TOTAL", support.values())
        return CaseTotals(
            report=report,
            support=support,
            report_total=report_total,
            support_total=support_total,
        )

    def _collect(
        self, role: Literal["REPORT", "SUPPORT"]
    ) -> dict[str, ArticleAggregate]:
        builders: dict[str, _AggregateBuilder] = {}
        seen_sources: set[str] = set()
        accepted = self.accepted_support_by_article
        for row in self.extraction.rows:
            if row.kind != "ITEM":
                continue
            amount_source = self.sources.get(row.amount_source_id)
            if amount_source is None:
                continue
            file_record = self.files.get(amount_source.file_id)
            if file_record is None or file_record.role != role:
                continue
            article = article_code_for_row(row, self.sources)
            if article is None:
                continue
            if role == "SUPPORT" and article in accepted:
                if row.row_id not in accepted[article]:
                    continue
            if amount_source.source_id in seen_sources:
                # A reused amount source is one operation, not two.
                continue
            seen_sources.add(amount_source.source_id)
            builder = builders.get(article)
            if builder is None:
                builder = _AggregateBuilder(article)
                builders[article] = builder
            builder.add(row.row_id, amount_source)
        return {code: builder.build() for code, builder in sorted(builders.items())}

    def _report_total(self, report: Mapping[str, ArticleAggregate]) -> ArticleAggregate:
        for row in self.extraction.rows:
            if row.kind != "DOCUMENT_TOTAL":
                continue
            source = self.sources.get(row.amount_source_id)
            if source is None:
                continue
            file_record = self.files.get(source.file_id)
            if file_record is None or file_record.role != "REPORT":
                continue
            value = _source_amount(source)
            return ArticleAggregate(
                article_code="TOTAL",
                amount=value[0],
                source_ids=(source.source_id,),
                row_ids=(row.row_id,),
                missing=value[1] == "missing",
                invalid=value[1] == "invalid",
            )
        return _sum_aggregates("TOTAL", report.values())


def check_article_totals(totals: CaseTotals, scope: Scope | str) -> list[Check]:
    """Compare each report article with the independent support aggregate."""
    scope = _scope(scope)
    checks: list[Check] = []
    for code in totals.article_codes:
        reported = totals.report.get(code)
        supported = totals.support.get(code)
        checks.append(_article_check(code, reported, supported, scope))
    return checks


def check_overall(totals: CaseTotals, scope: Scope | str) -> Check | None:
    """Compare the printed/report total with the independent support total."""
    if not totals.article_codes:
        return None
    scope = _scope(scope)
    return _overall_check(totals.report_total, totals.support_total, scope)


def article_code_for_row(
    row: ExtractedRow, sources: Mapping[str, Source]
) -> str | None:
    for name in ("article_code", "article", "code"):
        source_id = row.fields.get(name)
        if source_id is None:
            continue
        source = sources.get(source_id)
        if source is None:
            continue
        value = source.value if source.value is not None else source.raw_text
        if value:
            match = _ARTICLE_RE.search(value)
            if match is not None:
                return match.group(1)
    return None


def _article_check(
    code: str,
    reported: ArticleAggregate | None,
    supported: ArticleAggregate | None,
    scope: Scope,
) -> Check:
    if reported is None or reported.amount is None:
        return _not_run(
            "ARTICLE_REPORT_MISSING" if reported is None else "ARTICLE_AMOUNT_INVALID",
            f"ARTICLE:{code}",
            f"ARTICLE_AMOUNT:{code}",
            reported,
            supported,
        )
    if supported is None or supported.amount is None:
        return _not_run(
            "ARTICLE_SUPPORT_MISSING"
            if supported is None
            else "ARTICLE_AMOUNT_INVALID",
            f"ARTICLE:{code}",
            f"ARTICLE_AMOUNT:{code}",
            reported,
            supported,
        )
    return _compare(
        check_id=f"ARTICLE_AMOUNT:{code}",
        target_id=f"ARTICLE:{code}",
        reported=reported,
        supported=supported,
        scope=scope,
        equal_reason="ARTICLE_MATCHES_SUPPORT",
        excess_reason="ARTICLE_SUPPORT_EXCEEDS_REPORT",
        below_reason="ARTICLE_REPORT_EXCEEDS_SUPPORT",
    )


def _overall_check(
    reported: ArticleAggregate,
    supported: ArticleAggregate,
    scope: Scope,
) -> Check:
    if reported.amount is None:
        return _not_run(
            "OVERALL_REPORT_MISSING",
            "OVERALL",
            "SUPPORT_TOTAL:OVERALL",
            reported,
            supported,
        )
    if supported.amount is None:
        return _not_run(
            "OVERALL_SUPPORT_MISSING",
            "OVERALL",
            "SUPPORT_TOTAL:OVERALL",
            reported,
            supported,
        )
    return _compare(
        check_id="SUPPORT_TOTAL:OVERALL",
        target_id="OVERALL",
        reported=reported,
        supported=supported,
        scope=scope,
        equal_reason="OVERALL_MATCHES_SUPPORT",
        excess_reason="OVERALL_SUPPORT_EXCEEDS_REPORT",
        below_reason="OVERALL_REPORT_EXCEEDS_SUPPORT",
    )


def _compare(
    *,
    check_id: str,
    target_id: str,
    reported: ArticleAggregate,
    supported: ArticleAggregate,
    scope: Scope,
    equal_reason: str,
    excess_reason: str,
    below_reason: str,
) -> Check:
    assert reported.amount is not None and supported.amount is not None
    with localcontext() as context:
        context.prec = 50
        actual = Decimal(supported.amount)
        expected = Decimal(reported.amount)
        delta = actual - expected
    if delta > 0:
        state: Literal["PASS", "FAIL", "NOT_RUN"] = "FAIL"
        reason = excess_reason
    elif delta < 0:
        state, reason = "FAIL", below_reason
    else:
        state, reason = "PASS", equal_reason
    return Check(
        check_id=check_id,
        rule_id="REPORT_TOTAL"
        if check_id.startswith("SUPPORT_TOTAL")
        else "REPORT_AMOUNT",
        target_id=target_id,
        match_id=None,
        state=state,
        reason_code=reason,
        operands=[
            Operand(
                name="reported",
                value=reported.amount,
                source_ids=list(reported.source_ids),
            ),
            Operand(
                name="supported",
                value=supported.amount,
                source_ids=list(supported.source_ids),
            ),
        ],
        calculation=Calculation(
            actual=supported.amount,
            expected=reported.amount,
            delta=format(delta, "f"),
            tolerance=TOLERANCE,
            unit=scope.currency,
        ),
        evidence_ids=[*reported.source_ids, *supported.source_ids],
    )


def _not_run(
    reason: str,
    target_id: str,
    check_id: str,
    reported: ArticleAggregate | None,
    supported: ArticleAggregate | None,
) -> Check:
    return Check(
        check_id=check_id,
        rule_id="REPORT_TOTAL"
        if check_id.startswith("SUPPORT_TOTAL")
        else "REPORT_AMOUNT",
        target_id=target_id,
        match_id=None,
        state="NOT_RUN",
        reason_code=reason,
        operands=[
            Operand(
                name="reported",
                value=reported.amount if reported else None,
                source_ids=list(reported.source_ids) if reported else [],
            ),
            Operand(
                name="supported",
                value=supported.amount if supported else None,
                source_ids=list(supported.source_ids) if supported else [],
            ),
        ],
        calculation=None,
        evidence_ids=[
            *(reported.source_ids if reported else ()),
            *(supported.source_ids if supported else ()),
        ],
    )


def _sum_aggregates(
    code: str, aggregates: Iterable[ArticleAggregate]
) -> ArticleAggregate:
    values = list(aggregates)
    sources: list[str] = []
    rows: list[str] = []
    total = Decimal(0)
    missing = False
    invalid = False
    for aggregate in values:
        sources.extend(aggregate.source_ids)
        rows.extend(aggregate.row_ids)
        if aggregate.amount is None:
            missing = missing or aggregate.missing
            invalid = invalid or aggregate.invalid
            continue
        with localcontext() as context:
            context.prec = 50
            total += Decimal(aggregate.amount)
    return ArticleAggregate(
        article_code=code,
        amount=None if missing or invalid else format(total, "f"),
        source_ids=tuple(dict.fromkeys(sources)),
        row_ids=tuple(dict.fromkeys(rows)),
        missing=missing,
        invalid=invalid,
    )


def _source_amount(source: Source) -> tuple[str | None, str | None]:
    if source.value is None or not source.value.strip():
        return None, "missing"
    normalized = normalize_amount(source.value)
    if normalized is None:
        return None, "invalid"
    return normalized, None


def _scope(value: Scope | str) -> Scope:
    if isinstance(value, Scope):
        return value
    return Scope(currency=value, amount_basis="GROSS", reporting_period="")
