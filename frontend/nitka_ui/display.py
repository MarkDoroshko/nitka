from __future__ import annotations

from decimal import Decimal, InvalidOperation

from nitka.contracts.analysis import Check

from .demo_models import SourceView

CURRENCY_SYMBOLS = {"RUB": "₽", "USD": "$", "EUR": "€"}

DECISION_LABELS = {
    "NO_DISCREPANCIES": "Расхождения не выявлены в проверенном объёме",
    "DISCREPANCIES_FOUND": "Выявлены расхождения",
    "REVIEW_REQUIRED": "Требуется ручная проверка",
    "NOT_ASSESSED": "Проверка не выполнена",
}
DECISION_TONES = {
    "NO_DISCREPANCIES": "success",
    "DISCREPANCIES_FOUND": "danger",
    "REVIEW_REQUIRED": "warning",
    "NOT_ASSESSED": "neutral",
}
EXECUTION_LABELS = {
    "COMPLETED": "Обработка завершена",
    "PARTIAL": "Обработка частичная",
    "FAILED": "Обработка не удалась",
}
CHECK_STATE_LABELS = {
    "PASS": "Пройдено",
    "FAIL": "Расхождение",
    "NOT_RUN": "Не проверено",
}
CHECK_STATE_TONES = {
    "PASS": "success",
    "FAIL": "danger",
    "NOT_RUN": "warning",
}
REASON_LABELS = {
    "AMOUNTS_EQUAL": "Суммы совпадают",
    "AMOUNT_EXCEEDS_SUPPORT": "Заявлено больше подтверждённого",
    "AMOUNT_BELOW_SUPPORT": "Заявлено меньше подтверждённого",
    "MISSING_SUPPORT": "Нет подтверждающего документа",
    "AMBIGUOUS_MATCH": "Неоднозначное соответствие",
    "AMOUNT_MISSING": "Сумма не извлечена",
    "AMOUNT_INVALID": "Сумма не распознана",
    "AMOUNT_NEEDS_REVIEW": "Сумма требует ручной проверки",
    "UNKNOWN_ROW_KIND": "Не определён тип строки",
    "SCOPE_CURRENCY_MISSING": "Не задана валюта проверки",
    "TOTAL_EXCEEDS_ITEMS": "Итог больше суммы позиций",
    "TOTAL_BELOW_ITEMS": "Итог меньше суммы позиций",
    "TOTAL_MATCHES_ITEMS": "Итог совпадает с суммой позиций",
    "TOTAL_MISSING": "Итог не извлечён",
    "TOTAL_INVALID": "Итог не распознан",
    "TOTAL_NEEDS_REVIEW": "Итог требует ручной проверки",
    "NO_ITEMS": "Нет позиций для проверки итога",
    "ITEM_MISSING": "У позиции нет суммы",
    "ITEM_INVALID": "Сумма позиции не распознана",
    "ITEM_NEEDS_REVIEW": "Сумма позиции требует проверки",
    "FORMULA_UNVERIFIED": "Формула без кешированного значения",
    "PAGE_BOUNDARY_UNKNOWN": "Граница страницы не определена",
}
REVIEW_STATUS_LABELS = {
    "UNREVIEWED": "Не проверено",
    "NEEDS_REVIEW": "Нужна проверка",
    "CONFIRMED": "Подтверждено",
    "CORRECTED": "Исправлено вручную",
}
METHOD_LABELS = {
    "TABLE": "Таблица XLSX/CSV",
    "PDF_TEXT": "Текстовый слой PDF",
    "VISION": "Распознавание скана",
    "MANUAL": "Ручной ввод",
}
FILE_ROLE_LABELS = {
    "REPORT": "Отчёт",
    "SUPPORT": "Первичка",
}
FILE_STATUS_LABELS = {
    "COMPLETED": "Обработан",
    "PARTIAL": "Частично",
    "FAILED": "Ошибка",
}
MATCH_STATE_LABELS = {
    "ACCEPTED": "Принято",
    "AMBIGUOUS": "Неоднозначно",
    "REJECTED": "Отклонено",
}


def format_money(
    value: str | None, currency: str = "RUB", *, with_currency: bool = True
) -> str:
    if value is None or not str(value).strip():
        return "—"
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return str(value)
    decimals = format(number, "f").partition(".")[2].rstrip("0")
    precision = max(2, len(decimals))
    text = f"{number:,.{precision}f}".replace(",", "\u00a0").replace(".", ",")
    if with_currency and currency:
        return f"{text}\u00a0{CURRENCY_SYMBOLS.get(currency, currency)}"
    return text


def format_delta(value: str | None, currency: str = "RUB") -> str:
    if value is None or not str(value).strip():
        return "—"
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return str(value)
    text = format_money(value, currency, with_currency=False)
    if number > 0:
        text = f"+{text}"
    return f"{text}\u00a0{CURRENCY_SYMBOLS.get(currency, currency)}"


def source_location_text(source: SourceView) -> str:
    if source.location_kind == "PAGE":
        return f"Страница {source.page}" if source.page else "Страница"
    if source.location_kind == "CELL":
        sheet = f"Лист {source.sheet} · " if source.sheet else ""
        return f"{sheet}ячейка {source.cell}"
    return "Источник"


def short_run_id(run_id: str) -> str:
    return run_id if len(run_id) <= 12 else f"{run_id[:8]}…"


def operand_value(check: Check, name: str) -> str | None:
    for operand in check.operands:
        if operand.name == name:
            return operand.value
    return None
