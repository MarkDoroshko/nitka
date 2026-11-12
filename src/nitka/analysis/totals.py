from decimal import Decimal, localcontext
from typing import Literal

from nitka.amounts import normalize_amount
from nitka.contracts.analysis import Calculation, Check, Operand, Scope
from nitka.contracts.extraction import ExtractedRow, Source

TOLERANCE = "0.00"


def check_total(
    total_row: ExtractedRow,
    total_source: Source,
    item_sources: list[Source],
    scope: Scope | str,
    *,
    blocked_reason: str | None = None,
) -> Check:
    """Verify a report total against an independent sum of its item rows."""
    if isinstance(scope, str):
        scope = Scope(currency=scope, amount_basis="GROSS", reporting_period="")

    normalized_total = normalize_amount(total_source.value)
    normalized_items = [normalize_amount(source.value) for source in item_sources]
    calculation = None
    supported = None
    state: Literal["PASS", "FAIL", "NOT_RUN"] = "NOT_RUN"

    if not scope.currency.strip():
        reason = "SCOPE_CURRENCY_MISSING"
    elif blocked_reason is not None:
        reason = blocked_reason
    elif _is_blank(total_source.value) and _is_blank(total_source.raw_text):
        reason = "TOTAL_MISSING"
    elif _is_formula(total_source) and total_source.value is None:
        reason = "FORMULA_UNVERIFIED"
    elif normalized_total is None:
        reason = "TOTAL_INVALID"
    elif total_source.review_status == "NEEDS_REVIEW":
        reason = "TOTAL_NEEDS_REVIEW"
    elif not item_sources:
        reason = "NO_ITEMS"
    elif any(
        _is_blank(source.value) and _is_blank(source.raw_text)
        for source in item_sources
    ):
        reason = "ITEM_MISSING"
    elif any(_is_formula(source) and source.value is None for source in item_sources):
        reason = "ITEM_NEEDS_REVIEW"
    elif any(value is None for value in normalized_items):
        reason = "ITEM_INVALID"
    elif any(source.review_status == "NEEDS_REVIEW" for source in item_sources):
        reason = "ITEM_NEEDS_REVIEW"
    else:
        with localcontext() as context:
            context.prec = 50
            actual = Decimal(normalized_total)
            expected = sum(
                (Decimal(value) for value in normalized_items if value is not None),
                start=Decimal(0),
            )
            delta = actual - expected
        supported = format(expected, "f")
        calculation = Calculation(
            actual=normalized_total,
            expected=supported,
            delta=format(delta, "f"),
            tolerance=TOLERANCE,
            unit=scope.currency,
        )
        if delta > 0:
            state, reason = "FAIL", "TOTAL_EXCEEDS_ITEMS"
        elif delta < 0:
            state, reason = "FAIL", "TOTAL_BELOW_ITEMS"
        else:
            state, reason = "PASS", "TOTAL_MATCHES_ITEMS"

    return Check(
        check_id=f"REPORT_TOTAL:{total_row.row_id}",
        rule_id="REPORT_TOTAL",
        target_id=total_row.row_id,
        match_id=None,
        state=state,
        reason_code=reason,
        operands=[
            Operand(
                name="reported",
                value=normalized_total,
                source_ids=[total_source.source_id],
            ),
            Operand(
                name="supported",
                value=supported,
                source_ids=[source.source_id for source in item_sources],
            ),
        ],
        calculation=calculation,
        evidence_ids=[
            total_source.source_id,
            *[source.source_id for source in item_sources],
        ],
    )


def _is_blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _is_formula(source: Source) -> bool:
    return bool(source.raw_text and source.raw_text.startswith("="))
