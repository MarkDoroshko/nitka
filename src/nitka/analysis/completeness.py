"""Completeness checks for the report fields and coverage of report rows."""

from collections.abc import Sequence

from nitka.analysis.aggregation import article_code_for_row
from nitka.contracts.analysis import Check, Coverage, Operand
from nitka.contracts.extraction import ExtractionResult


def unverified_breakdowns(extraction: ExtractionResult) -> list[Check]:
    reports = {file.file_id for file in extraction.files if file.role == "REPORT"}
    sources = {source.source_id: source for source in extraction.sources}
    checks = []
    seen: set[str] = set()
    rows = sorted(extraction.rows, key=lambda row: row.kind != "DOCUMENT_TOTAL")
    for row in rows:
        if sources[row.amount_source_id].file_id not in reports:
            continue
        for field, source_id in row.fields.items():
            if source_id in seen or not (
                field == "labor" or field.startswith(("0200_", "0300_"))
            ):
                continue
            seen.add(source_id)
            checks.append(Check(
                check_id=f"REPORT_FIELD:{row.row_id}:{field}",
                target_id=row.row_id, match_id=None, state="NOT_RUN",
                reason_code="BREAKDOWN_SUPPORT_MISSING",
                operands=[
                    Operand(name="reported", value=sources[source_id].value, source_ids=[source_id]),
                    Operand(name="supported", value=None, source_ids=[]),
                ],
                calculation=None, evidence_ids=[source_id],
            ))
    return checks


def calculate_coverage(extraction: ExtractionResult, checks: Sequence[Check]) -> Coverage:
    reports = {file.file_id for file in extraction.files if file.role == "REPORT"}
    sources = {source.source_id: source for source in extraction.sources}
    by_target: dict[str, list[Check]] = {}
    for check in checks:
        by_target.setdefault(check.target_id, []).append(check)
    groups: dict[str, list[Check]] = {}
    for row in extraction.rows:
        if sources[row.amount_source_id].file_id not in reports:
            continue
        targets = [row.row_id]
        article = article_code_for_row(row, sources) if row.kind == "ITEM" else None
        if article is not None:
            targets.append(f"ARTICLE:{article}")
        if row.kind == "DOCUMENT_TOTAL":
            targets.append("OVERALL")
        group = groups.setdefault(row.amount_source_id, [])
        for target in targets:
            group.extend(by_target.get(target, ()))
    return Coverage(
        report_rows=len(groups),
        checked_rows=sum(bool(group) and all(c.state != "NOT_RUN" for c in group) for group in groups.values()),
        required_checks=len(checks),
        completed_checks=sum(check.state != "NOT_RUN" for check in checks),
    )
