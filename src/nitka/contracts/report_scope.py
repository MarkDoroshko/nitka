"""Validate report evidence against the selected case before reconciliation."""

import re
from calendar import monthrange
from datetime import date
from typing import Final

from nitka.contracts.extraction import ExtractionResult, Issue, Source
from nitka.contracts.profile import CaseProfile, kfz_row_identity, normalize_contract

PACKAGE_EXTRACTION_VERSION: Final = "package-3"
REPORT_SCOPE_UNVERIFIED: Final = "REPORT_SCOPE_UNVERIFIED"
REPORT_SCOPE_MIN_OCR_CONFIDENCE: Final = 0.80


def report_scope_issues(extraction: ExtractionResult, profile: CaseProfile) -> tuple[Issue, ...]:
    strict = extraction.extraction_version in {"package-2", PACKAGE_EXTRACTION_VERSION}
    reports = {file.file_id for file in extraction.files if file.role == "REPORT"}
    sources = {source.source_id: source for source in extraction.sources}
    rows = [row for row in extraction.rows if sources[row.amount_source_id].file_id in reports]
    keys = ("contract", "selected_row", "printed_period", "service_period")
    scoped = strict or any(any(key in row.fields for key in keys) for row in rows)
    if not scoped:
        return ()
    if len(reports) != 1:
        return (Issue(
            code=REPORT_SCOPE_UNVERIFIED,
            detail=f"Expected one report, found {len(reports)}",
        ),)

    file_id = next(iter(reports))
    details = set()
    if not rows:
        details.add("No selected report row was extracted")
    expected_row = (profile.target_stage, profile.target_year)
    if (strict or any("selected_row" in row.fields for row in rows)) and (
        kfz_row_identity(profile.target_row_label) != expected_row
    ):
        details.add("The profile target row label conflicts with its stage or year")

    expected_period = (profile.service_period.start, profile.service_period.end)
    # Article rows and the total share the same KFZ scope sources.
    for contract_id, row_id, printed_id, period_id in {
        tuple(row.fields.get(key, "") for key in keys) for row in rows
    }:
        if strict or contract_id:
            contract = normalize_contract(_usable(sources.get(contract_id)))
            if not contract:
                details.add("Report contract is missing or requires review")
            elif contract != normalize_contract(profile.contract):
                details.add(f"Report contract {contract!r} differs from {profile.contract!r}")
        label = _usable(sources.get(row_id))
        identity = kfz_row_identity(label) if label is not None else None
        if strict or row_id:
            if label is None:
                details.add("Report target row is missing or requires review")
            elif identity != expected_row:
                details.add(f"Report row {label!r} differs from {profile.target_row_label!r}")
        if strict or printed_id:
            printed = _usable(sources.get(printed_id))
            period = _parse_period(printed)
            if period is not None and identity is not None and 1 <= identity[1] <= 9999:
                year = identity[1]
                period = (max(period[0], date(year, 1, 1)), min(period[1], date(year, 12, 31)))
            else:
                period = None
            _check_period(period, expected_period, details)
        if period_id:
            _check_period(_parse_period(_usable(sources.get(period_id))), expected_period, details)
    return tuple(
        Issue(code=REPORT_SCOPE_UNVERIFIED, file_id=file_id, detail=detail)
        for detail in sorted(details)
    )


def _usable(source: Source | None) -> str | None:
    if source is None or source.review_status == "NEEDS_REVIEW":
        return None
    if source.method == "OCR" and source.review_status == "UNREVIEWED" and (
        source.confidence is None or source.confidence < REPORT_SCOPE_MIN_OCR_CONFIDENCE
    ):
        return None
    return source.value


def _parse_period(value: str | None) -> tuple[date, date] | None:
    if value is None:
        return None
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}\.\.[0-9]{4}-[0-9]{2}", value):
        start, end = value.split("..")
        try:
            last = date.fromisoformat(f"{end}-01")
            return date.fromisoformat(f"{start}-01"), last.replace(
                day=monthrange(last.year, last.month)[1]
            )
        except ValueError:
            return None
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\.\.[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        start, end = value.split("..")
        try:
            return date.fromisoformat(start), date.fromisoformat(end)
        except ValueError:
            return None
    return None


def _check_period(
    period: tuple[date, date] | None, expected: tuple[date, date], details: set[str]
) -> None:
    if period is None or period[0] > period[1]:
        details.add("Report period is missing, invalid or requires review")
    elif period != expected:
        details.add(
            f"Report period {period[0]}..{period[1]} differs from {expected[0]}..{expected[1]}"
        )
