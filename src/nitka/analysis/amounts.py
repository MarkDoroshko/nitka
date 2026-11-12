from decimal import Decimal, localcontext
from typing import Literal

from nitka.amounts import normalize_amount
from nitka.contracts.analysis import Calculation, Check, Match, Operand, Scope
from nitka.contracts.extraction import ExtractedRow, Source

TOLERANCE = "0.00"


def check_amount(
    row: ExtractedRow,
    reported: Source,
    match: Match | None,
    support: list[Source],
    currency: Scope | str,
    *,
    blocked_reason: str | None = None,
) -> Check:
    """Compare one report row with its accepted support positions.

    All operands are interpreted in the single scope supplied by the
    analysis run: sources do not carry their own currency or amount basis in
    the current contract. A plain string is accepted for compatibility with
    older callers and is treated as the scope currency.
    """
    scope = (
        currency
        if isinstance(currency, Scope)
        else Scope(currency=currency, amount_basis="GROSS", reporting_period="")
    )

    support_ids = [source.source_id for source in support]
    normalized_reported = normalize_amount(reported.value)
    normalized_support = [normalize_amount(source.value) for source in support]
    calculation = None
    supported = None
    state: Literal["PASS", "FAIL", "NOT_RUN"] = "NOT_RUN"

    if blocked_reason is not None:
        reason = blocked_reason
    elif not scope.currency.strip():
        reason = "SCOPE_CURRENCY_MISSING"
    elif row.kind == "UNKNOWN":
        reason = "UNKNOWN_ROW_KIND"
    elif match is None:
        reason = "MISSING_SUPPORT"
    elif match.state == "AMBIGUOUS":
        reason = "AMBIGUOUS_MATCH"
    elif _is_blank(reported.value) or any(
        _is_blank(source.value) for source in support
    ):
        reason = "AMOUNT_MISSING"
    elif normalized_reported is None or any(
        value is None for value in normalized_support
    ):
        reason = "AMOUNT_INVALID"
    elif any(
        source.review_status == "NEEDS_REVIEW" for source in [reported, *support]
    ):
        reason = "AMOUNT_NEEDS_REVIEW"
    else:
        with localcontext() as context:
            # Amounts have at most 18 integer and 6 fractional digits; a wide
            # context keeps addition exact for any realistic number of rows.
            context.prec = 50
            actual = Decimal(normalized_reported)
            expected = sum(
                (
                    Decimal(value)
                    for value in normalized_support
                    if value is not None
                ),
                start=Decimal(0),
            )
            delta = actual - expected
        supported = format(expected, "f")
        calculation = Calculation(
            actual=normalized_reported,
            expected=supported,
            delta=format(delta, "f"),
            tolerance=TOLERANCE,
            unit=scope.currency,
        )
        if delta > 0:
            state, reason = "FAIL", "AMOUNT_EXCEEDS_SUPPORT"
        elif delta < 0:
            state, reason = "FAIL", "AMOUNT_BELOW_SUPPORT"
        else:
            state, reason = "PASS", "AMOUNTS_EQUAL"

    return Check(
        check_id=f"REPORT_AMOUNT:{row.row_id}",
        target_id=row.row_id,
        match_id=match.match_id if match else None,
        state=state,
        reason_code=reason,
        operands=[
            Operand(
                name="reported",
                value=normalized_reported,
                source_ids=[reported.source_id],
            ),
            Operand(name="supported", value=supported, source_ids=support_ids),
        ],
        calculation=calculation,
        evidence_ids=[reported.source_id, *support_ids],
    )


def _is_blank(value: str | None) -> bool:
    return value is None or not value.strip()
