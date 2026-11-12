from __future__ import annotations

import html
from decimal import Decimal, InvalidOperation
from typing import Sequence

import streamlit as st

from .demo_models import CheckView, RunSnapshot
from .display import REASON_LABELS, format_delta, format_money

DECISION_SHORT_LABELS = {
    "NO_DISCREPANCIES": "Расхождений нет",
    "DISCREPANCIES_FOUND": "Есть расхождения",
    "REVIEW_REQUIRED": "Нужна проверка",
    "NOT_ASSESSED": "Не проверено",
}


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _plural(count: int, one: str, few: str, many: str) -> str:
    last_two = count % 100
    last = count % 10
    if last == 1 and last_two != 11:
        return one
    if 2 <= last <= 4 and not 12 <= last_two <= 14:
        return few
    return many


def _badge(tone: str, text: str) -> str:
    return f'<span class="nk-badge {tone}">{_esc(text)}</span>'


def _money(value: str | None, currency: str = "RUB") -> str:
    return format_money(value, currency)


def _delta(value: str | None, currency: str = "RUB") -> str:
    return format_delta(value, currency)


def _delta_class(value: str | None) -> str:
    if value is None:
        return "muted"
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return "muted"
    if number == 0:
        return "success"
    return "danger"


SHORT_REASON_LABELS = {
    "AMOUNTS_EQUAL": "Суммы совпадают",
    "AMOUNT_EXCEEDS_SUPPORT": "Больше подтверждённого",
    "AMOUNT_BELOW_SUPPORT": "Меньше подтверждённого",
    "MISSING_SUPPORT": "Нет первички",
    "AMBIGUOUS_MATCH": "Неоднозначно",
    "AMOUNT_MISSING": "Сумма не извлечена",
    "AMOUNT_INVALID": "Сумма не распознана",
    "AMOUNT_NEEDS_REVIEW": "Нужна проверка",
    "UNKNOWN_ROW_KIND": "Тип не определён",
    "SCOPE_CURRENCY_MISSING": "Нет валюты",
    "TOTAL_EXCEEDS_ITEMS": "Итог больше позиций",
    "TOTAL_BELOW_ITEMS": "Итог меньше позиций",
    "TOTAL_MATCHES_ITEMS": "Итог совпадает",
    "TOTAL_MISSING": "Итог не извлечён",
    "TOTAL_INVALID": "Итог не распознан",
    "TOTAL_NEEDS_REVIEW": "Итог требует проверки",
    "NO_ITEMS": "Нет позиций",
    "ITEM_MISSING": "Нет суммы позиции",
    "ITEM_INVALID": "Сумма позиции не распознана",
    "ITEM_NEEDS_REVIEW": "Позиция требует проверки",
    "FORMULA_UNVERIFIED": "Формула без кеша",
    "PAGE_BOUNDARY_UNKNOWN": "Граница страницы неизвестна",
}


def _reason_title(code: str) -> str:
    return REASON_LABELS.get(code, code)


def _short_reason(code: str) -> str:
    return SHORT_REASON_LABELS.get(code, _reason_title(code))


def _operand_value(check: object, name: str) -> str | None:
    for operand in getattr(check, "operands", ()):
        if getattr(operand, "name", None) == name:
            return getattr(operand, "value", None)
    return None


def _reason_explanation(view: CheckView) -> str:
    code = view.check.reason_code
    explanations = {
        "AMOUNTS_EQUAL": (
            "Заявленная сумма совпадает с суммой принятых подтверждающих "
            "позиций. Проверка пройдена."
        ),
        "AMOUNT_EXCEEDS_SUPPORT": (
            "Заявленная сумма больше подтверждённой. Положительная дельта "
            "показывает, насколько отчёт превышает основание."
        ),
        "AMOUNT_BELOW_SUPPORT": (
            "Заявленная сумма меньше подтверждённой. Отрицательная дельта "
            "показывает разницу с основанием."
        ),
        "MISSING_SUPPORT": (
            "Подтверждающий документ не найден. Сумма остаётся неизвестной "
            "и не превращается в ноль."
        ),
        "AMBIGUOUS_MATCH": (
            "Найдено несколько равнозначных кандидатов. Автоматический выбор "
            "запрещён: решение принимает человек."
        ),
        "AMOUNT_NEEDS_REVIEW": (
            "Извлечённое значение помечено как требующее проверки. Расчёт "
            "блокируется до подтверждения источника."
        ),
        "AMOUNT_INVALID": (
            "Напечатанное значение не удалось разобрать как сумму. "
            "Подстановка предположения запрещена."
        ),
        "AMOUNT_MISSING": (
            "Сумма отсутствует в источнике или не была извлечена. "
            "Проверка не выполнена."
        ),
        "UNKNOWN_ROW_KIND": (
            "Тип строки не определён, поэтому она не может участвовать в расчёте."
        ),
        "SCOPE_CURRENCY_MISSING": (
            "В запуске не задана валюта. Сравнение сумм без валюты небезопасно."
        ),
        "TOTAL_EXCEEDS_ITEMS": (
            "Итог больше суммы позиций отчёта. Положительная дельта показывает, "
            "насколько итог превышает независимо сложенные позиции."
        ),
        "TOTAL_BELOW_ITEMS": (
            "Итог меньше суммы позиций отчёта. Отрицательная дельта показывает, "
            "насколько итог занижен относительно позиций."
        ),
        "TOTAL_MATCHES_ITEMS": (
            "Итог совпадает с независимой суммой позиций отчёта. Проверка пройдена."
        ),
        "TOTAL_MISSING": (
            "Итог отсутствует или не был извлечён. Проверка не выполнена."
        ),
        "TOTAL_INVALID": (
            "Значение итога не удалось разобрать как сумму. Подстановка запрещена."
        ),
        "TOTAL_NEEDS_REVIEW": (
            "Итог требует проверки: например, формула имеет кеш, но не подтверждена."
        ),
        "FORMULA_UNVERIFIED": (
            "Формула не имеет кешированного значения. Итог нельзя проверить без "
            "пересчёта или ручного подтверждения."
        ),
        "PAGE_BOUNDARY_UNKNOWN": (
            "Граница страницы не передана в извлечении. Суммировать позиции "
            "только по порядку строк нельзя: это может дать ложное расхождение."
        ),
        "NO_ITEMS": ("В документе нет позиций, которые можно независимо сложить."),
        "ITEM_MISSING": (
            "У одной из позиций отсутствует сумма, поэтому итог нельзя сложить."
        ),
        "ITEM_INVALID": (
            "Сумма одной из позиций не распознана, поэтому итог нельзя сложить."
        ),
        "ITEM_NEEDS_REVIEW": (
            "Сумма позиции требует проверки; независимый итог заблокирован."
        ),
    }
    return explanations.get(
        code, "Правило не смогло завершить проверку для этой строки."
    )


def _decision_counts(snapshot: RunSnapshot) -> tuple[int, int, int]:
    checks = snapshot.result.checks
    fails = sum(check.state == "FAIL" for check in checks)
    not_run = sum(check.state == "NOT_RUN" for check in checks)
    passes = sum(check.state == "PASS" for check in checks)
    return passes, fails, not_run


def _file_name(snapshot: RunSnapshot, extension: str) -> str:
    return f"nitka_{snapshot.kit.info.kit_id}_r{snapshot.result.revision}.{extension}"


def _preview_html(lines: Sequence[str], needle: str | None) -> str:
    parts: list[str] = []
    for line in lines:
        safe = _esc(line)
        if needle:
            safe_needle = _esc(needle)
            if safe_needle in safe:
                safe = safe.replace(safe_needle, f"<mark>{safe_needle}</mark>")
        parts.append(safe)
    body = "<br/>".join(parts) if parts else "Превью недоступно."
    return f'<div class="nk-paper">{body}</div>'


def _page_head(eyebrow: str, title: str, subtitle: str = "") -> None:
    paragraph = f"<p>{_esc(subtitle)}</p>" if subtitle else ""
    st.markdown(
        (
            '<div class="nk-page-head">'
            f'<div class="nk-kicker">{_esc(eyebrow)}</div>'
            f"<h2>{_esc(title)}</h2>"
            f"{paragraph}"
            "</div>"
        ),
        unsafe_allow_html=True,
    )
