from __future__ import annotations

from dataclasses import dataclass

Action = dict[str, str]

DECISION_LABELS = {
    "NO_DISCREPANCIES": "Расхождения не выявлены",
    "DISCREPANCIES_FOUND": "Выявлены расхождения",
    "REVIEW_REQUIRED": "Требуется ручная проверка",
    "NOT_ASSESSED": "Проверка не выполнена",
}

DECISION_DETAILS = {
    "NO_DISCREPANCIES": "Расхождения не выявлены в проверенном объеме.",
    "DISCREPANCIES_FOUND": (
        "Расхождения найдены по завершенным проверкам. "
        "Неполнота проверки не отменяет найденные расхождения."
    ),
    "REVIEW_REQUIRED": (
        "Расхождений не найдено, но остаются обязательные непроверенные части."
    ),
    "NOT_ASSESSED": "Нет завершенных проверок, поэтому результат не оценен.",
}

EXECUTION_LABELS = {
    "COMPLETED": "Обработка завершена",
    "PARTIAL": "Обработка частичная",
    "FAILED": "Обработка не удалась",
}

STATE_LABELS = {
    "PASS": "Пройдено",
    "FAIL": "Расхождение",
    "NOT_RUN": "Не проверено",
    "PROCESSING": "Ошибка обработки",
}

REASON_LABELS = {
    "AMOUNT_EXCEEDS_SUPPORT": "Заявленная сумма превышает подтвержденную",
    "AMOUNT_BELOW_SUPPORT": "Заявленная сумма ниже подтвержденной",
    "AMOUNTS_EQUAL": "Суммы совпадают",
    "MISSING_SUPPORT": "Нет подтверждающей первички",
    "AMBIGUOUS_MATCH": "Неоднозначное соответствие",
    "AMOUNT_MISSING": "Сумма отсутствует",
    "AMOUNT_INVALID": "Сумма не распознана",
    "AMOUNT_NEEDS_REVIEW": "Сумма требует ручной проверки",
    "UNKNOWN_ROW_KIND": "Неизвестен тип строки",
    "SCOPE_CURRENCY_MISSING": "Не задана валюта",
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
    "FORMULA_UNVERIFIED": "Формула без кеша",
    "PAGE_BOUNDARY_UNKNOWN": "Граница страницы не определена",
}

PROCESSING_REASON_LABELS = {
    "PARSE_FAILED": "Не удалось прочитать файл",
    "PAGE_UNREADABLE": "Страница не распознана",
    "FILE_ACCESS_DENIED": "Нет доступа к файлу",
    "UNSUPPORTED_FORMAT": "Формат файла не поддерживается",
    "PDF_UNREADABLE": "PDF не читается",
    "PDF_ENCRYPTED": "PDF защищён паролем",
    "PDF_TIMEOUT": "PDF не ответил вовремя",
    "IMAGE_UNREADABLE": "Изображение не открывается",
    "VISION_REQUIRED": "Нужна Vision-обработка",
    "PREVIEW_FAILED": "Не удалось подготовить превью",
    "TEXT_LAYER_UNUSABLE": "Текстовый слой непригоден",
    "NO_POSITIONS": "Позиции не найдены",
    "TABLE_LAYOUT_UNKNOWN": "Не удалось определить колонки таблицы",
    "AMOUNT_MISSING": "Сумма отсутствует",
}

FILTER_STATES: dict[str, frozenset[str] | None] = {
    "Все": None,
    "Требуют внимания": frozenset({"FAIL", "NOT_RUN", "PROCESSING"}),
    "Расхождения": frozenset({"FAIL"}),
    "Не проверено": frozenset({"NOT_RUN"}),
    "Ошибки обработки": frozenset({"PROCESSING"}),
    "Пройдено": frozenset({"PASS"}),
}

AMOUNT_BASIS_LABELS = {
    "GROSS": "сумма с НДС",
    "NET": "сумма без НДС",
}


@dataclass(frozen=True)
class CoverageView:
    report_rows: int
    checked_rows: int
    required_checks: int
    completed_checks: int

    @property
    def row_ratio(self) -> float:
        if self.report_rows <= 0:
            return 0.0
        return self.checked_rows / self.report_rows

    @property
    def check_ratio(self) -> float:
        if self.required_checks <= 0:
            return 0.0
        return self.completed_checks / self.required_checks


@dataclass(frozen=True)
class ResultRow:
    """Одна строка таблицы: проверка или ошибка обработки."""

    issue_id: str | None
    target_id: str
    state: str
    state_label: str
    reason_code: str
    reason: str
    reported: str
    supported: str
    delta: str
    unit: str
    evidence_ids: tuple[str, ...]
    detail: str

    @property
    def is_actionable(self) -> bool:
        return self.issue_id is not None and self.state in {
            "FAIL",
            "NOT_RUN",
            "PROCESSING",
        }

    @property
    def table_row(self) -> dict[str, str]:
        return {
            "Строка": self.target_id,
            "Статус": self.state_label,
            "Причина": self.reason,
            "Заявлено": self.reported,
            "Подтверждено": self.supported,
            "Отклонение": self.delta,
            "Валюта": self.unit or "—",
            "Доказательств": str(len(self.evidence_ids)),
        }

    @property
    def search_text(self) -> str:
        return " ".join(
            (
                self.issue_id or "",
                self.target_id,
                self.state,
                self.state_label,
                self.reason_code,
                self.reason,
                self.reported,
                self.supported,
                self.delta,
                self.unit,
                self.detail,
            )
        )


@dataclass(frozen=True)
class ResultsView:
    decision: str
    decision_label: str
    decision_detail: str
    execution_status: str
    execution_label: str
    coverage: CoverageView
    rows: tuple[ResultRow, ...]
    warning: str | None
    discrepancies_count: int
    not_checked_count: int
    processing_count: int
    actionable_count: int
    scope_text: str
    run_text: str

    @property
    def incomplete(self) -> bool:
        return self.warning is not None
