from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .results_models import (
    AMOUNT_BASIS_LABELS,
    DECISION_DETAILS,
    DECISION_LABELS,
    EXECUTION_LABELS,
    FILTER_STATES,
    PROCESSING_REASON_LABELS,
    REASON_LABELS,
    STATE_LABELS,
    CoverageView,
    ResultRow,
    ResultsView,
)


def build_results_view(result: object) -> ResultsView:
    """Превращает ``AnalysisResult`` (или совместимый словарь) в данные экрана."""

    decision = _text(_get(result, "decision"), "NOT_ASSESSED")
    execution_status = _text(_get(result, "execution_status"), "FAILED")
    scope = _get(result, "scope")
    currency = _text(_get(scope, "currency")) if scope is not None else ""

    coverage_data = _get(result, "coverage")
    coverage = CoverageView(
        report_rows=_integer(_get(coverage_data, "report_rows")),
        checked_rows=_integer(_get(coverage_data, "checked_rows")),
        required_checks=_integer(_get(coverage_data, "required_checks")),
        completed_checks=_integer(_get(coverage_data, "completed_checks")),
    )

    checks = _items(_get(result, "checks"))
    rows: list[ResultRow] = [
        _check_row(check, index=index, currency=currency)
        for index, check in enumerate(checks)
    ]

    files = _items(_get(result, "files"))
    file_names = {
        _text(_get(file, "file_id")): _text(_get(file, "name"))
        for file in files
        if _get(file, "file_id") is not None
    }
    issues = _items(_get(result, "issues"))
    for index, issue in enumerate(issues):
        rows.append(_processing_row(issue, index=index, file_names=file_names))

    bad_files = [
        _text(_get(file, "name")) or _text(_get(file, "file_id"))
        for file in files
        if _text(_get(file, "status")) not in {"", "COMPLETED"}
    ]
    discrepancies_count = sum(row.state == "FAIL" for row in rows)
    not_checked_count = sum(row.state == "NOT_RUN" for row in rows)
    processing_count = sum(row.state == "PROCESSING" for row in rows)

    warning = _incomplete_warning(
        decision=decision,
        execution_status=execution_status,
        coverage=coverage,
        bad_files=bad_files,
        processing_count=processing_count,
    )

    return ResultsView(
        decision=decision,
        decision_label=DECISION_LABELS.get(decision, decision),
        decision_detail=DECISION_DETAILS.get(decision, ""),
        execution_status=execution_status,
        execution_label=EXECUTION_LABELS.get(execution_status, execution_status),
        coverage=coverage,
        rows=tuple(rows),
        warning=warning,
        discrepancies_count=discrepancies_count,
        not_checked_count=not_checked_count,
        processing_count=processing_count,
        actionable_count=discrepancies_count + not_checked_count + processing_count,
        scope_text=_scope_text(scope),
        run_text=_run_text(result),
    )


def filter_rows(rows: Sequence[ResultRow], status: str, query: str) -> list[ResultRow]:
    """Фильтрует строки по статусу и подстроке без учета регистра."""

    allowed = FILTER_STATES.get(status) if isinstance(status, str) else None
    needle = query.strip().casefold() if isinstance(query, str) else ""
    filtered: list[ResultRow] = []
    for row in rows:
        if allowed is not None and row.state not in allowed:
            continue
        if needle and needle not in row.search_text.casefold():
            continue
        filtered.append(row)
    return filtered


def _incomplete_warning(
    *,
    decision: str,
    execution_status: str,
    coverage: CoverageView,
    bad_files: Sequence[str],
    processing_count: int,
) -> str | None:
    problems: list[str] = []

    if execution_status != "COMPLETED":
        execution_label = EXECUTION_LABELS.get(execution_status, execution_status)
        problems.append(f"техническая обработка — {execution_label.lower()}")
    if coverage.completed_checks < coverage.required_checks:
        problems.append(
            f"выполнено {coverage.completed_checks} из "
            f"{coverage.required_checks} обязательных проверок"
        )
    if coverage.checked_rows < coverage.report_rows:
        problems.append(
            f"проверено {coverage.checked_rows} из {coverage.report_rows} строк отчета"
        )
    if bad_files:
        problems.append("не полностью обработаны файлы: " + ", ".join(bad_files))
    if processing_count:
        problems.append(f"ошибок обработки: {processing_count}")
    if decision == "NOT_ASSESSED" and not problems:
        problems.append("нет ни одной оцененной проверки")
    if decision == "REVIEW_REQUIRED" and not problems:
        problems.append("есть обязательные части, требующие ручной проверки")

    if not problems:
        return None

    message = "**Проверка неполная.** " + "; ".join(problems) + "."
    if decision == "DISCREPANCIES_FOUND":
        message += (
            " Найденные расхождения показаны ниже; неполнота проверки не отменяет их."
        )
    return message


def _check_row(check: object, *, index: int, currency: str) -> ResultRow:
    check_id = _text(_get(check, "check_id"), f"CHECK-{index + 1}")
    state = _text(_get(check, "state"), "NOT_RUN").upper()
    reason_code = _text(_get(check, "reason_code"))
    reason = REASON_LABELS.get(reason_code, reason_code or "Без пояснения")

    operands = _items(_get(check, "operands"))
    calculation = _get(check, "calculation")

    reported = _operand_value(operands, "reported")
    supported = _operand_value(operands, "supported")
    if reported is None and calculation is not None:
        reported = _text(_get(calculation, "actual"))
    if supported is None and calculation is not None:
        supported = _text(_get(calculation, "expected"))

    delta = _text(_get(calculation, "delta")) if calculation is not None else ""
    unit = _text(_get(calculation, "unit")) if calculation is not None else ""
    unit = unit or currency
    evidence_ids = tuple(
        _text(source_id)
        for source_id in _items(_get(check, "evidence_ids"))
        if _text(source_id)
    )

    detail = _check_detail(
        target_id=_text(_get(check, "target_id")),
        reported=reported,
        supported=supported,
        delta=delta,
        unit=unit,
        reason=reason,
    )

    return ResultRow(
        issue_id=check_id if state in {"FAIL", "NOT_RUN"} else None,
        target_id=_text(_get(check, "target_id")) or "—",
        state=state,
        state_label=STATE_LABELS.get(state, state or "Неизвестно"),
        reason_code=reason_code,
        reason=reason,
        reported=_display(reported),
        supported=_display(supported),
        delta=_display(delta),
        unit=unit,
        evidence_ids=evidence_ids,
        detail=detail,
    )


def _processing_row(
    issue: object,
    *,
    index: int,
    file_names: Mapping[str, str],
) -> ResultRow:
    code = _text(_get(issue, "code"))
    file_id = _text(_get(issue, "file_id"))
    detail = _text(_get(issue, "detail")) or "Ошибка обработки без пояснения"
    file_name = file_names.get(file_id, "")
    reason = PROCESSING_REASON_LABELS.get(code, code or "Ошибка обработки")
    target = file_name or file_id or "—"

    return ResultRow(
        issue_id=_processing_issue_id(issue, index),
        target_id=target,
        state="PROCESSING",
        state_label=STATE_LABELS["PROCESSING"],
        reason_code=code,
        reason=reason,
        reported="—",
        supported="—",
        delta="—",
        unit="",
        evidence_ids=(),
        detail=detail,
    )


def _processing_issue_id(issue: object, index: int) -> str:
    explicit = _get(issue, "issue_id")
    if explicit is not None and _text(explicit):
        return _text(explicit)

    code = _text(_get(issue, "code")) or "ISSUE"
    file_id = _text(_get(issue, "file_id"))
    suffix = f":{file_id}" if file_id else ""
    return f"{code}{suffix}:{index + 1}"


def _check_detail(
    *,
    target_id: str,
    reported: str | None,
    supported: str | None,
    delta: str,
    unit: str,
    reason: str,
) -> str:
    prefix = f"Строка {target_id}: " if target_id else ""
    if reported is not None and supported is not None and delta:
        suffix = f" {unit}" if unit else ""
        return (
            f"{prefix}заявлено {reported}{suffix}, подтверждено "
            f"{supported}{suffix}, отклонение {delta}{suffix}."
        )
    if reported is not None and supported is not None:
        suffix = f" {unit}" if unit else ""
        return f"{prefix}заявлено {reported}{suffix}, подтверждено {supported}{suffix}."
    return f"{prefix}{reason}."


def _scope_text(scope: object | None) -> str:
    if scope is None:
        return ""

    parts: list[str] = []
    currency = _text(_get(scope, "currency"))
    basis = _text(_get(scope, "amount_basis"))
    period = _text(_get(scope, "reporting_period"))

    if currency:
        parts.append(currency)
    if basis:
        parts.append(AMOUNT_BASIS_LABELS.get(basis, basis))
    if period:
        parts.append(f"период {period}")
    return ", ".join(parts)


def _run_text(result: object) -> str:
    run_id = _text(_get(result, "run_id"))
    revision = _get(result, "revision")
    parts: list[str] = []
    if run_id:
        parts.append(f"Запуск {run_id}")
    if revision is not None:
        parts.append(f"редакция {revision}")
    return ", ".join(parts) + "." if parts else ""


def _operand_value(operands: Sequence[object], name: str) -> str | None:
    for operand in operands:
        if _text(_get(operand, "name")) != name:
            continue
        value = _get(operand, "value")
        if value is None:
            return None
        text = _text(value)
        return text if text else None
    return None


def _get(obj: object, name: str, default: object = None) -> object | None:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _items(value: object | None) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _text(value: object | None, default: str = "") -> str:
    if value is None:
        return default
    text = str(value)
    return text if text else default


def _display(value: str | None) -> str:
    if value is None or not value.strip():
        return "—"
    return value


def _integer(value: object | None, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
