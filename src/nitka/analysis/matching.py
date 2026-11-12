"""Match support operations to KFZ article rows without using amounts.

The matcher is deliberately conservative: an operation is linked only when its
article classification, contract and service period are unambiguous. Duplicate
operations are counted once by business identity (not file hash), while
excluded documents carry their own diagnostics separate from accepted Match.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, Sequence

from nitka.amounts import parse_amount
from nitka.analysis.classification import (
    ArticleClassification,
    DocumentClassifier,
    ServicePeriodGate,
    contract_matches,
    operation_key,
)
from nitka.contracts.analysis import ExcludedDocument, Match
from nitka.contracts.extraction import ExtractionResult, Issue, Source
from nitka.contracts.profile import CaseProfile, normalize_contract
from nitka.contracts.report_scope import REPORT_SCOPE_UNVERIFIED, report_scope_issues


class Matcher(Protocol):
    def __call__(self, extraction: ExtractionResult, /) -> Sequence[Match]: ...


@dataclass(frozen=True)
class SupportOperation:
    row_id: str
    file_id: str
    document_id: str
    amount_source_id: str
    source_ids: tuple[str, ...]
    amount: str | None
    contract: str | None
    service_period: str | None
    document_date: str | None
    document_number: str | None
    operation_id: str | None
    description: str | None
    article_code: str | None
    classification_basis: str | None
    fragment: str | None
    period_present: bool = False


@dataclass(frozen=True)
class ReportArticleRow:
    row_id: str
    file_id: str
    document_id: str
    article_code: str | None
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class MatchOutcome:
    matches: tuple[Match, ...]
    excluded_documents: tuple[ExcludedDocument, ...]
    ambiguous_operations: tuple[SupportOperation, ...]
    required_articles: tuple[str, ...] = ()
    blocked_articles: dict[str, str] = field(default_factory=dict)
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class _ClassifiedOperation:
    operation: SupportOperation
    classification: ArticleClassification


@dataclass(frozen=True)
class _AmbiguousGroup:
    operations: tuple[SupportOperation, ...]
    classification: ArticleClassification


class _FieldReader:
    def __init__(self, sources: dict[str, Source]) -> None:
        self.sources = sources

    def value(self, fields: dict[str, str], *names: str) -> str | None:
        for name in names:
            source_id = fields.get(name)
            if source_id is None:
                continue
            source = self.sources.get(source_id)
            if source is None or source.review_status == "NEEDS_REVIEW":
                continue
            value = source.value
            if value is not None and value.strip():
                return value.strip()
        return None


def _contract(
    reader: _FieldReader,
    fields: dict[str, str],
    profile: CaseProfile | None,
    description: str | None,
) -> str | None:
    value = reader.value(fields, "contract")
    if value is not None or profile is None:
        return value
    source = reader.sources.get(fields.get("contract", ""))
    if (
        source is None or source.method != "OCR"
        or source.confidence is None or source.confidence < 0.95
        or source.value is None
    ):
        return None
    token = normalize_contract(source.value)
    if not re.fullmatch(r"\d+(?:/\d+)+-\d+", token):
        return None
    if re.search(rf"(?<![\w/-]){re.escape(token)}(?![\w/-])", description or ""):
        return token
    if token == normalize_contract(profile.contract):
        return token
    return None


class SupportOperationReader:
    """Build support operations from field-to-source references."""

    def read(
        self, extraction: ExtractionResult, profile: CaseProfile | None = None
    ) -> list[SupportOperation]:
        files = {file.file_id: file for file in extraction.files}
        sources = {source.source_id: source for source in extraction.sources}
        reader = _FieldReader(sources)
        operations: list[SupportOperation] = []
        for row in extraction.rows:
            if row.kind != "ITEM":
                continue
            amount_source = sources.get(row.amount_source_id)
            if amount_source is None:
                continue
            file_record = files.get(amount_source.file_id)
            if file_record is None or file_record.role != "SUPPORT":
                continue
            document_number = reader.value(
                row.fields,
                "document_number",
                "document_no",
                "doc_number",
                "number",
            )
            operation_id = reader.value(row.fields, "operation_id")
            description = reader.value(
                row.fields, "description", "name", "purpose", "fragment"
            )
            field_ids = tuple(
                source_id for source_id in row.fields.values() if source_id in sources
            )
            operations.append(
                SupportOperation(
                    row_id=row.row_id,
                    file_id=amount_source.file_id,
                    document_id=amount_source.document_id,
                    amount_source_id=amount_source.source_id,
                    source_ids=_unique((amount_source.source_id, *field_ids)),
                    amount=amount_source.value,
                    contract=_contract(reader, row.fields, profile, description),
                    service_period=reader.value(row.fields, "service_period", "period"),
                    document_date=reader.value(row.fields, "document_date"),
                    document_number=document_number,
                    operation_id=operation_id,
                    description=description,
                    article_code=reader.value(row.fields, "article_code"),
                    classification_basis=reader.value(
                        row.fields, "classification_basis"
                    ),
                    fragment=reader.value(row.fields, "fragment"),
                    period_present=any(
                        key in row.fields for key in ("service_period", "period", "evidence_period")
                    ),
                )
            )
        return operations


class ReportArticleReader:
    def read(self, extraction: ExtractionResult) -> list[ReportArticleRow]:
        files = {file.file_id: file for file in extraction.files}
        sources = {source.source_id: source for source in extraction.sources}
        reader = _FieldReader(sources)
        rows: list[ReportArticleRow] = []
        for row in extraction.rows:
            if row.kind != "ITEM":
                continue
            amount_source = sources.get(row.amount_source_id)
            if amount_source is None:
                continue
            file_record = files.get(amount_source.file_id)
            if file_record is None or file_record.role != "REPORT":
                continue
            article = reader.value(row.fields, "article_code", "article", "code")
            rows.append(
                ReportArticleRow(
                    row_id=row.row_id,
                    file_id=amount_source.file_id,
                    document_id=amount_source.document_id,
                    article_code=_article_code(article),
                    source_ids=_unique((amount_source.source_id, *row.fields.values())),
                )
            )
        return rows


class CaseMatcher:
    """Profile-aware matcher; the profile is constructor state, not a global."""

    def __init__(self, profile: CaseProfile) -> None:
        self.profile = profile
        self.classifier = DocumentClassifier(profile)
        self.periods = ServicePeriodGate(profile)

    def __call__(self, extraction: ExtractionResult, /) -> Sequence[Match]:
        outcome = self.match(extraction)
        by_row = {row.row_id: row.article_code for row in ReportArticleReader().read(extraction)}
        # Callers of the legacy links-only interface must not turn incomplete
        # evidence into an accepted monetary comparison.
        return [
            link.model_copy(update={"state": "AMBIGUOUS"})
            if link.state == "ACCEPTED" and by_row.get(link.report_row_id) in outcome.blocked_articles
            else link
            for link in outcome.matches
        ]

    def match(self, extraction: ExtractionResult) -> MatchOutcome:
        scope_already_blocked = any(
            issue.code == REPORT_SCOPE_UNVERIFIED for issue in extraction.issues
        )
        scope_issues = () if scope_already_blocked else report_scope_issues(extraction, self.profile)
        if scope_already_blocked or scope_issues:
            return MatchOutcome(
                (), (), (), tuple(self.profile.article_codes),
                dict.fromkeys(self.profile.article_codes, REPORT_SCOPE_UNVERIFIED),
                scope_issues,
            )
        support = SupportOperationReader().read(extraction, self.profile)
        report_rows = ReportArticleReader().read(extraction)
        excluded: list[ExcludedDocument] = []
        issues: list[Issue] = []
        remaining: list[SupportOperation] = []
        ambiguous_groups: list[_AmbiguousGroup] = []

        for operation in support:
            classification = self.classifier.classify(
                operation.document_number, operation.description, operation.article_code
            )
            contract_ok = contract_matches(self.profile, operation.contract)
            if contract_ok is False:
                excluded.append(
                    _excluded(operation, "OTHER_CONTRACT", f"support contract {operation.contract!r}")
                )
                continue
            if contract_ok is None:
                ambiguous_groups.append(_AmbiguousGroup((operation,), classification))
                issues.append(Issue(
                    code="CONTRACT_UNVERIFIED", file_id=operation.file_id,
                    detail="No reliable support contract",
                ))
                continue
            period = self.periods.evaluate(operation.service_period)
            used_document_date = (
                operation.service_period is None and not operation.period_present
                and classification.article_code in {"0800", "0900"}
            )
            if used_document_date:
                period = self.periods.document_date(operation.document_date)
            if period.inside is False:
                excluded.append(_excluded(operation, "OTHER_PERIOD", period.detail))
                continue
            if period.inside is None:
                ambiguous_groups.append(_AmbiguousGroup((operation,), classification))
                issues.append(Issue(
                    code="PERIOD_UNVERIFIED", file_id=operation.file_id, detail=period.detail,
                ))
                continue
            if used_document_date:
                issues.append(Issue(
                    code="DOCUMENT_DATE_APPLICABILITY", file_id=operation.file_id,
                    detail=period.detail,
                ))
            remaining.append(operation)

        valid: list[_ClassifiedOperation] = []
        hashes = {file.file_id: file.sha256 for file in extraction.files}
        for group in _group_by_identity(remaining, hashes):
            resolved = self._resolve_group(group, excluded)
            if isinstance(resolved, _ClassifiedOperation):
                valid.append(resolved)
            else:
                ambiguous_groups.append(resolved)

        matches = self._matches(report_rows, valid, ambiguous_groups)
        report_codes = {row.article_code for row in report_rows}
        for item in valid:
            if item.classification.article_code not in report_codes:
                ambiguous_groups.append(_AmbiguousGroup((item.operation,), item.classification))
        ambiguous_operations = _unique_operations(
            [operation for group in ambiguous_groups for operation in group.operations]
        )
        blocked = {}
        for ambiguous_group in ambiguous_groups:
            code = ambiguous_group.classification.article_code
            if code not in report_codes:
                blocked.update(dict.fromkeys(self.profile.article_codes, "UNASSIGNED_SUPPORT"))
            elif code is not None:
                blocked[code] = "AMBIGUOUS_MATCH"
        issues.extend(
            Issue(code="SUPPORT_UNRESOLVED", file_id=operation.file_id, detail=operation.row_id)
            for operation in ambiguous_operations
        )
        matched_ids = {
            support_id
            for match in matches if match.state == "ACCEPTED"
            for support_id in match.support_row_ids
        }
        for code in ("0200", "0300"):
            if code not in self.profile.article_codes:
                continue
            found = {
                self.periods.evaluate(item.operation.service_period).month
                for item in valid
                if item.classification.article_code == code and item.operation.row_id in matched_ids
            }
            missing = sorted(set(self.profile.month_labels()) - found)
            if missing:
                blocked.setdefault(code, "SUPPORT_MONTH_MISSING")
                issues.append(Issue(
                    code="SUPPORT_MONTH_MISSING", detail=f"{code}: {', '.join(missing)}",
                ))
        operation_files = {operation.file_id for operation in support}
        if any(
            file.role == "SUPPORT" and file.status != "COMPLETED" and file.file_id not in operation_files
            for file in extraction.files
        ) or any(
            issue.code in {"INPUT_UNREADABLE", "INPUT_CHANGED"} for issue in extraction.issues
        ):
            for code in self.profile.article_codes:
                blocked.setdefault(code, "SUPPORT_INPUT_UNREADABLE")
        return MatchOutcome(
            tuple(matches), tuple(excluded), ambiguous_operations,
            tuple(self.profile.article_codes), blocked, tuple(issues),
        )

    def _resolve_group(
        self,
        group: tuple[SupportOperation, ...],
        excluded: list[ExcludedDocument],
    ) -> _ClassifiedOperation | _AmbiguousGroup:
        classifications = [
            _ClassifiedOperation(
                operation,
                self.classifier.classify(
                    operation.document_number,
                    operation.description,
                    operation.article_code,
                ),
            )
            for operation in group
        ]
        known = [item for item in classifications if item.classification.article_code]
        if not known:
            return _AmbiguousGroup(
                group,
                ArticleClassification(
                    None,
                    "no unambiguous article evidence",
                    ambiguous=True,
                    detail="all duplicate/group rows are ambiguous",
                ),
            )
        codes = {item.classification.article_code for item in known}
        if len(codes) != 1:
            return _AmbiguousGroup(
                group,
                ArticleClassification(
                    None,
                    "conflicting article classifications",
                    ambiguous=True,
                    detail="same operation has different article codes",
                ),
            )
        amounts = {
            Decimal(value) if (value := parse_amount(operation.amount).value) is not None else None
            for operation in group
        }
        if len(group) > 1 and (len(amounts) != 1 or None in amounts):
            return _AmbiguousGroup(
                group,
                known[0].classification,
            )
        canonical = known[0]
        for operation in group:
            if operation.row_id == canonical.operation.row_id:
                continue
            excluded.append(
                _excluded(
                    operation,
                    "DUPLICATE",
                    "same operation identity as "
                    f"{canonical.operation.row_id}; counted once",
                )
            )
        return canonical

    def _matches(
        self,
        report_rows: list[ReportArticleRow],
        valid: list[_ClassifiedOperation],
        ambiguous_groups: list[_AmbiguousGroup],
    ) -> list[Match]:
        by_article: dict[str, list[str]] = defaultdict(list)
        for item in valid:
            code = item.classification.article_code
            if code is not None:
                by_article[code].append(item.operation.row_id)

        report_counts = Counter(row.article_code for row in report_rows)
        uncertain: dict[str, set[str]] = defaultdict(set)
        for group in ambiguous_groups:
            code = group.classification.article_code
            for article in (code,) if code else self.profile.article_codes:
                uncertain[article].update(operation.row_id for operation in group.operations)

        matches = []
        for row in report_rows:
            row_article = row.article_code
            if row_article is None:
                continue
            candidates = sorted(set(by_article[row_article]) | uncertain[row_article])
            if candidates:
                needs_review = bool(uncertain[row_article]) or report_counts[row_article] != 1
                matches.append(Match(
                    match_id=f"M-{row_article}-{row.row_id}" + ("-REVIEW" if needs_review else ""),
                    report_row_id=row.row_id,
                    support_row_ids=candidates,
                    state="AMBIGUOUS" if needs_review else "ACCEPTED",
                ))
        return matches


def _group_by_identity(
    operations: list[SupportOperation], hashes: dict[str, str]
) -> list[tuple[SupportOperation, ...]]:
    groups: dict[tuple[str, ...], list[SupportOperation]] = defaultdict(list)
    counts = Counter(operation.file_id for operation in operations)
    for operation in sorted(operations, key=lambda item: (item.file_id, item.row_id)):
        key = operation_key(
            operation.operation_id, operation.contract, operation.document_number,
            operation.document_date,
        )
        if operation.article_code in {"0200", "0300"} and key is not None:
            key = (*key, operation.service_period or "")
        if key is None and counts[operation.file_id] == 1:
            key = ("file", hashes[operation.file_id])
        groups[key or ("row", operation.row_id)].append(operation)
    return [tuple(group) for group in groups.values()]


def _excluded(
    operation: SupportOperation, reason: str, detail: str
) -> ExcludedDocument:
    return ExcludedDocument(
        file_id=operation.file_id,
        document_id=operation.document_id,
        reason=reason,  # type: ignore[arg-type]
        detail=detail,
        source_ids=list(operation.source_ids),
    )


def _article_code(value: str | None) -> str | None:
    if value is None:
        return None
    match = re.search(r"\b(0[2-9]00)\b", value)
    return match.group(1) if match else None


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _unique_operations(
    operations: Sequence[SupportOperation],
) -> tuple[SupportOperation, ...]:
    seen: set[str] = set()
    result: list[SupportOperation] = []
    for operation in operations:
        if operation.row_id in seen:
            continue
        seen.add(operation.row_id)
        result.append(operation)
    return tuple(result)
