from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal

from nitka.amounts import parse_amount
from nitka.analysis.aggregation import (
    CaseAggregator,
    article_code_for_row,
    check_article_totals,
    check_overall,
)
from nitka.analysis.amounts import check_amount
from nitka.analysis.totals import check_total
from nitka.contracts.analysis import Check, Match, Scope
from nitka.contracts.extraction import ExtractionResult, Source
from nitka.contracts.validation import validate_extraction


def _require_unique(ids: Iterable[str], label: str) -> None:
    values = list(ids)
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label} IDs")


def check_report(
    extraction: ExtractionResult,
    matches: Sequence[Match],
    currency: Scope | str,
    *,
    blocked_articles: Mapping[str, str] | None = None,
    required_articles: tuple[str, ...] = (),
) -> list[Check]:
    validate_extraction(extraction)
    _require_unique((match.match_id for match in matches), "match")
    files = {file.file_id: file for file in extraction.files}
    sources = {source.source_id: source for source in extraction.sources}

    report_items = {}
    support_rows = {}
    support_codes = {}
    for row in extraction.rows:
        source = sources[row.amount_source_id]
        role = files[source.file_id].role
        if role == "REPORT" and row.kind in {"ITEM", "UNKNOWN"}:
            report_items[row.row_id] = row
        if role == "SUPPORT" and row.kind == "ITEM":
            support_rows[row.row_id] = source
            support_codes[row.row_id] = article_code_for_row(row, sources)

    accepted: dict[str, Match] = {}
    ambiguous: dict[str, Match] = {}
    used_support: set[str] = set()
    for match in matches:
        if match.report_row_id not in report_items or any(
            row_id not in support_rows for row_id in match.support_row_ids
        ):
            raise ValueError(f"Invalid row reference in match {match.match_id}")
        _require_unique(match.support_row_ids, "support row")
        if match.state == "ACCEPTED":
            if report_items[match.report_row_id].kind != "ITEM":
                raise ValueError(
                    f"Accepted match {match.match_id} requires a report item"
                )
            if match.report_row_id in accepted or used_support.intersection(
                match.support_row_ids
            ):
                raise ValueError(f"Accepted match {match.match_id} reuses a row")
            accepted[match.report_row_id] = match
            used_support.update(match.support_row_ids)
        elif match.state == "AMBIGUOUS":
            ambiguous.setdefault(match.report_row_id, match)

    items_by_document: dict[tuple[str, str], list[Source]] = defaultdict(list)
    unknown_by_document: set[tuple[str, str]] = set()
    articles_by_document: dict[tuple[str, str], set[str]] = defaultdict(set)
    seen_item_sources: set[str] = set()
    for row in extraction.rows:
        source = sources[row.amount_source_id]
        if files[source.file_id].role != "REPORT":
            continue
        key = (source.file_id, source.document_id)
        if row.kind == "ITEM":
            if (code := article_code_for_row(row, sources)) is not None:
                articles_by_document[key].add(code)
            if source.source_id in seen_item_sources:
                # One amount source reused by several row IDs is one item.
                continue
            seen_item_sources.add(source.source_id)
            items_by_document[key].append(source)
        elif row.kind == "UNKNOWN":
            unknown_by_document.add(key)

    checks: list[Check] = []
    blocked = dict(blocked_articles or {})
    overall_block = None
    accepted_report_sources = {
        (article_code_for_row(report_items[row_id], sources), report_items[row_id].amount_source_id)
        for row_id in accepted
    }
    for row in extraction.rows:
        source = sources[row.amount_source_id]
        if files[source.file_id].role != "REPORT":
            continue
        key = (source.file_id, source.document_id)
        if row.kind in {"ITEM", "UNKNOWN"}:
            row_match = accepted.get(row.row_id) or ambiguous.get(row.row_id)
            article = article_code_for_row(row, sources) if row.kind == "ITEM" else None
            if article is not None:
                if row_match is not None and row_match.state == "AMBIGUOUS":
                    blocked.setdefault(article, "AMBIGUOUS_MATCH")
                elif row_match is None and (article, row.amount_source_id) not in accepted_report_sources:
                    blocked.setdefault(article, "ARTICLE_SUPPORT_MISSING")
                continue
            support = (
                [support_rows[row_id] for row_id in row_match.support_row_ids]
                if row_match
                else []
            )
            checks.append(check_amount(row, source, row_match, support, currency))
        elif row.kind == "PAGE_SUBTOTAL":
            # Report rows carry no page/section boundary, so summing "items
            # since the previous subtotal" would guess and can create a false
            # discrepancy. Refuse the calculation until the boundary exists.
            checks.append(
                check_total(
                    row,
                    source,
                    items_by_document[key],
                    currency,
                    blocked_reason="PAGE_BOUNDARY_UNKNOWN",
                )
            )
        elif row.kind == "DOCUMENT_TOTAL":
            items = items_by_document[key]
            material_id = row.fields.get("materials")
            if material_id is not None and all(item.source_id != material_id for item in items):
                material = sources[material_id]
                items = [*items, material]
                value = parse_amount(material.value).value
                if material.review_status == "NEEDS_REVIEW" or value is None or Decimal(value) != 0:
                    overall_block = "MATERIAL_SUPPORT_UNVERIFIED"
            checks.append(
                check_total(
                    row,
                    source,
                    items,
                    currency,
                    blocked_reason=(
                        "UNKNOWN_ROW_KIND" if key in unknown_by_document
                        else "REPORT_ARTICLE_MISSING" if set(required_articles) - articles_by_document[key]
                        else None
                    ),
                )
            )

    accepted_support_by_article: dict[str, set[str]] = defaultdict(set)
    for match in accepted.values():
        report_row = report_items[match.report_row_id]
        article = article_code_for_row(report_row, sources)
        if article is not None:
            if any(
                support_codes[row_id] not in (None, article)
                for row_id in match.support_row_ids
            ):
                blocked[article] = "SUPPORT_ARTICLE_CONFLICT"
            accepted_support_by_article[article].update(match.support_row_ids)

    totals = CaseAggregator(
        extraction,
        accepted_support_by_article=accepted_support_by_article,
        blocked=blocked,
        required_articles=required_articles,
    ).totals()
    checks.extend(check_article_totals(totals, currency))
    if unknown_by_document and overall_block is None:
        overall_block = "UNKNOWN_ROW_KIND"
    overall = check_overall(totals, currency, blocked_reason=overall_block)
    if overall is not None:
        checks.append(overall)
    return checks
