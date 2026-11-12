from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from .review_sources import (
    _as_mapping,
    _field,
    _operand,
    _select_sources,
    _short_location,
)

_REASON_TITLES = {
    "AMOUNT_EXCEEDS_SUPPORT": "Заявленная сумма превышает подтверждение",
    "AMOUNT_BELOW_SUPPORT": "Заявленная сумма ниже подтверждения",
    "AMOUNTS_EQUAL": "Заявленная и подтверждённая суммы совпадают",
    "AMOUNT_MISSING": "Сумма не распознана",
    "AMOUNT_INVALID": "Сумму не удалось прочитать",
    "AMOUNT_NEEDS_REVIEW": "Значение требует ручной проверки",
    "MISSING_SUPPORT": "Не найден подтверждающий документ",
    "AMBIGUOUS_MATCH": "Неоднозначное соответствие документа",
    "SCOPE_CURRENCY_MISSING": "Не указана валюта сверки",
    "UNKNOWN_ROW_KIND": "Не определён тип строки отчёта",
}
_CURRENCY_SYMBOLS = {
    "RUB": "₽",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "KZT": "₸",
    "BYN": "Br",
    "UAH": "₴",
}


def _as_decimal(value: object) -> Decimal | None:
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = Decimal(str(value))
        return result if result.is_finite() else None
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for space in ("\u00a0", "\u202f", " "):
        text = text.replace(space, "")
    text = text.replace(",", ".")
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _currency_symbol(currency: object) -> str:
    code = str(currency or "").strip()
    if not code:
        return ""
    return _CURRENCY_SYMBOLS.get(code.upper(), code)


def _format_amount(value: object, currency: object = "") -> str:
    amount = _as_decimal(value)
    if amount is None:
        return "—"

    text = f"{abs(amount):,.2f}"
    integer, fraction = text.split(".")
    formatted = f"{integer.replace(',', chr(0x202F))},{fraction}"
    if amount < 0:
        formatted = "−" + formatted

    symbol = _currency_symbol(currency)
    return f"{formatted} {symbol}" if symbol else formatted


def _format_delta(value: object, currency: object = "") -> str:
    amount = _as_decimal(value)
    if amount is None:
        return "—"
    if amount > 0:
        return "+" + _format_amount(amount, currency)
    if amount < 0:
        return "−" + _format_amount(abs(amount), currency)
    return _format_amount(amount, currency)


def _delta_kind(delta: object, tolerance: object = "0.00") -> str:
    amount = _as_decimal(delta)
    if amount is None:
        return "unknown"
    limit = _as_decimal(tolerance) or Decimal(0)
    limit = abs(limit)
    if abs(amount) <= limit:
        return "zero"
    return "over" if amount > 0 else "under"


def _difference(reported: object, supported: object) -> str | None:
    left = _as_decimal(reported)
    right = _as_decimal(supported)
    if left is None or right is None:
        return None
    return format(left - right, "f")


def _plural(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def _support_note(support_sources: list[object]) -> str:
    actual = [
        source for source in support_sources if not _as_mapping(source).get("_missing")
    ]
    if not actual:
        return "подтверждающие документы не найдены"

    count = len(actual)
    noun = _plural(count, "документ", "документа", "документов")
    places = ", ".join(_short_location(source) for source in actual[:3])
    if count > 3:
        places += f" и ещё {count - 3}"
    return f"{count} {noun} · {places}"


# ---------------------------------------------------------------------------
# Представление
# ---------------------------------------------------------------------------


def _build_view(issue: object, sources: object) -> dict[str, Any]:
    reported_sources, support_sources = _select_sources(issue, sources)
    calculation = _field(issue, "calculation", default={}) or {}
    reported_operand = _operand(issue, "reported")
    supported_operand = _operand(issue, "supported")

    reported = _field(
        issue,
        "reported_amount",
        "reported_value",
        "amount_reported",
        "actual",
    )
    if reported is None and reported_operand is not None:
        reported = _field(reported_operand, "value")
    if reported is None:
        reported = _field(calculation, "actual")

    supported = _field(
        issue,
        "supported_amount",
        "confirmed_amount",
        "expected_amount",
        "amount_supported",
        "expected",
    )
    if supported is None and supported_operand is not None:
        supported = _field(supported_operand, "value")
    if supported is None:
        supported = _field(calculation, "expected")

    delta = _field(issue, "delta", "difference")
    if delta is None:
        delta = _field(calculation, "delta")
    if delta is None:
        delta = _difference(reported, supported)

    tolerance = _field(issue, "tolerance")
    if tolerance is None:
        tolerance = _field(calculation, "tolerance")
    if tolerance is None:
        tolerance = "0.00"

    currency = _field(issue, "currency")
    if currency is None:
        currency = _field(calculation, "unit", default="")
    if currency is None:
        currency = ""

    issue_id = str(
        _field(issue, "issue_id", "check_id", "id", default="")
        or _field(issue, "target_id", default="")
        or ""
    )
    reason_code = str(_field(issue, "reason_code", "code", default="") or "")
    state = str(_field(issue, "state", default="") or "").upper()
    detail = str(_field(issue, "detail", "message", default="") or "")
    mapped_title = _REASON_TITLES.get(reason_code, "")
    explicit_title = str(_field(issue, "title", default="") or "")
    title = explicit_title or mapped_title or detail or "Расхождение по сумме"

    return {
        "issue_id": issue_id,
        "reason_code": reason_code,
        "state": state,
        "title": title,
        "detail": detail,
        "reported": reported,
        "supported": supported,
        "delta": delta,
        "tolerance": tolerance,
        "delta_kind": _delta_kind(delta, tolerance),
        "currency": str(currency or ""),
        "reported_sources": reported_sources,
        "support_sources": support_sources,
    }


def _state_label(view: Mapping[str, Any]) -> tuple[str, str]:
    state = view["state"]
    if state == "FAIL" or view["delta_kind"] in {"over", "under"}:
        return "Расхождение", "alert"
    if state == "PASS" or view["delta_kind"] == "zero":
        return "Сверено", "ok"
    return "Нужна проверка", "wait"


def _finding(view: Mapping[str, Any]) -> str:
    kind = view["delta_kind"]
    currency = view["currency"]
    delta = view["delta"]
    if kind == "unknown":
        return "Разницу рассчитать нельзя: не хватает читаемого значения."
    if kind == "zero":
        return "Суммы совпадают в пределах допуска."
    amount = _format_amount(abs(_as_decimal(delta) or Decimal(0)), currency)
    if kind == "over":
        return f"Заявленная сумма выше подтверждённой на {amount}."
    return f"Заявленная сумма ниже подтверждённой на {amount}."
