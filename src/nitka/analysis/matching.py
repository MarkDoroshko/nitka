"""Match support operations to KFZ article rows without using amounts.

The matcher is deliberately conservative: an operation is linked only when its
article classification, contract and service period are unambiguous. Duplicate
operations are counted once by business identity (not file hash), while
excluded documents carry their own diagnostics separate from accepted Match.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol, Sequence

from nitka.analysis.classification import (
    ArticleClassification,
    DocumentClassifier,
    ServicePeriodGate,
    contract_matches,
    operation_key,
)
from nitka.contracts.analysis import ExcludedDocument, Match
from nitka.contracts.extraction import ExtractionResult, Source
from nitka.contracts.profile import CaseProfile


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
            if source is None:
                continue
            value = source.value if source.value is not None else source.raw_text
            if value is not None and value.strip():
                return value.strip()
        return None


class SupportOperationReader:
    """Build support operations from field-to-source references."""

    def read(self, extraction: ExtractionResult) -> list[SupportOperation]:
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
            if document_number is None:
                document_number = _number_from_operation_id(operation_id)
            if document_number is None:
                document_number = _number_from_text(description)
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
                    contract=reader.value(row.fields, "contract"),
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
        return list(self.match(extraction).matches)

    def match(self, extraction: ExtractionResult) -> MatchOutcome:
        support = SupportOperationReader().read(extraction)
        report_rows = ReportArticleReader().read(extraction)
        excluded: list[ExcludedDocument] = []
        remaining: list[SupportOperation] = []
        incomplete: list[SupportOperation] = []

        for operation in support:
            contract_ok = contract_matches(self.profile, operation.contract)
            if contract_ok is False:
                excluded.append(
                    _excluded(
                        operation,
                        "OTHER_CONTRACT",
                        f"support contract {operation.contract!r} does not match "
                        f"profile {self.profile.contract!r}",
                    )
                )
                continue
            if contract_ok is None:
                incomplete.append(operation)
                continue
            period = self.periods.evaluate(operation.service_period)
            if period.inside is False:
                excluded.append(_excluded(operation, "OTHER_PERIOD", period.detail))
                continue
            if period.inside is None:
                incomplete.append(operation)
                continue
            remaining.append(operation)

        valid: list[_ClassifiedOperation] = []
        ambiguous_groups: list[_AmbiguousGroup] = [
            _AmbiguousGroup(
                (operation,),
                self.classifier.classify(
                    operation.document_number,
                    operation.description,
                    operation.article_code,
                ),
            )
            for operation in incomplete
        ]
        for group in _group_by_identity(remaining):
            resolved = self._resolve_group(group, excluded)
            if isinstance(resolved, _ClassifiedOperation):
                valid.append(resolved)
            else:
                ambiguous_groups.append(resolved)

        matches = self._matches(report_rows, valid, ambiguous_groups)
        ambiguous_operations = tuple(
            operation for group in ambiguous_groups for operation in group.operations
        )
        return MatchOutcome(
            matches=tuple(matches),
            excluded_documents=tuple(excluded),
            ambiguous_operations=_unique_operations(ambiguous_operations),
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
        amounts = {_decimal_text(operation.amount) for operation in group}
        if len(group) > 1 and len(amounts) > 1:
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

        report_by_article: dict[str, list[ReportArticleRow]] = defaultdict(list)
        for row in report_rows:
            if row.article_code is not None:
                report_by_article[row.article_code].append(row)

        ambiguous_by_article: dict[str, set[str]] = defaultdict(set)
        for group in ambiguous_groups:
            code = group.classification.article_code
            if code is not None:
                ambiguous_by_article[code].update(
                    operation.row_id for operation in group.operations
                )

        matches: list[Match] = []
        handled_ambiguous: set[str] = set()
        for article, support_ids in sorted(by_article.items()):
            rows = report_by_article.get(article, [])
            ambiguous_ids = ambiguous_by_article.get(article)
            if ambiguous_ids is not None:
                # If any operation for this article needs review, the whole
                # article row needs review; never let the accepted subset hide it.
                all_ids = sorted(set(support_ids) | ambiguous_ids)
                for row in rows:
                    matches.append(
                        Match(
                            match_id=f"M-{article}-{row.row_id}-REVIEW",
                            report_row_id=row.row_id,
                            support_row_ids=all_ids,
                            state="AMBIGUOUS",
                        )
                    )
                handled_ambiguous.add(article)
                continue
            unique_ids = sorted(set(support_ids))
            if len(rows) == 1:
                matches.append(
                    Match(
                        match_id=f"M-{article}-{rows[0].row_id}",
                        report_row_id=rows[0].row_id,
                        support_row_ids=unique_ids,
                        state="ACCEPTED",
                    )
                )
            elif len(rows) > 1:
                for row in rows:
                    matches.append(
                        Match(
                            match_id=f"M-{article}-{row.row_id}-REVIEW",
                            report_row_id=row.row_id,
                            support_row_ids=unique_ids,
                            state="AMBIGUOUS",
                        )
                    )

        for article, ambiguous_ids in sorted(ambiguous_by_article.items()):
            if article in handled_ambiguous:
                continue
            rows = report_by_article.get(article, [])
            for row in rows:
                matches.append(
                    Match(
                        match_id=f"M-{article}-{row.row_id}-REVIEW",
                        report_row_id=row.row_id,
                        support_row_ids=sorted(ambiguous_ids),
                        state="AMBIGUOUS",
                    )
                )
        return matches


def _group_by_identity(
    operations: list[SupportOperation],
) -> list[tuple[SupportOperation, ...]]:
    groups: dict[tuple[str, ...], list[SupportOperation]] = defaultdict(list)
    unique: list[SupportOperation] = []
    for operation in operations:
        key = operation_key(
            operation.operation_id,
            operation.contract,
            operation.document_number,
            operation.document_date,
        )
        if key is None:
            unique.append(operation)
        else:
            groups[key].append(operation)
    return [
        *[tuple(group) for group in groups.values()],
        *[(operation,) for operation in unique],
    ]


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


def _number_from_operation_id(value: str | None) -> str | None:
    if value is None:
        return None
    for token in re.split(r"[|;/,\s]+", value):
        if re.fullmatch(r"\d{1,2}", token):
            return token
    return None


def _number_from_text(value: str | None) -> str | None:
    if value is None:
        return None
    match = re.search(r"(?:№|N|документ(?:а)?)\s*(\d{1,3})", value, re.IGNORECASE)
    return match.group(1) if match else None


def _article_code(value: str | None) -> str | None:
    if value is None:
        return None
    match = re.search(r"\b(0[2-9]00)\b", value)
    return match.group(1) if match else None


def _decimal_text(value: str | None) -> str:
    return value.strip() if value is not None else ""


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
