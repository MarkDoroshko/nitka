"""Upload and inspect a real case package in the Streamlit session."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import streamlit as st

from nitka.contracts.analysis import AnalysisResult, CaseMatching, Check
from nitka.contracts.profile import CaseProfile
from nitka.extraction.limits import PackageLimitError

from .display import (
    CHECK_STATE_LABELS,
    DECISION_LABELS,
    EXECUTION_LABELS,
    FILE_ROLE_LABELS,
    FILE_STATUS_LABELS,
    REASON_LABELS,
    format_delta,
    format_money,
    operand_value,
)
from .package_run import (
    PackageUploadError,
    load_profile,
    local_ocr,
    run_uploaded_package,
    runs_root,
)
from .upload_state import reset_upload_state
from .upload_widgets import render_package_upload

_LOGGER = logging.getLogger(__name__)
_RUN_KEY = "nitka_package_run"
_ARTICLES = {
    "0200": "Оплата труда",
    "0300": "Страховые взносы",
    "0800": "Общепроизводственные расходы",
    "0900": "Общехозяйственные расходы",
}
_BREAKDOWNS = {
    "labor": "Трудоёмкость",
    "0200_payroll_total": "0200 · Итого по оплате труда",
    "0300_contributions_total": "0300 · Итого по взносам",
    "reserve": "Резерв",
}
_CHECK_REASONS = {
    "REPORT_SCOPE_UNVERIFIED": "Не подтверждена область проверки КФЗ",
    "BREAKDOWN_SUPPORT_MISSING": "Нет подтверждения детализации",
    "SUPPORT_MONTH_MISSING": "Нет подтверждения за все месяцы",
    "SUPPORT_INPUT_UNREADABLE": "Есть нечитаемые документы первички",
    "UNASSIGNED_SUPPORT": "Не удалось отнести документы к статье",
    "SUPPORT_SOURCE_REUSED": "Источник связан с несколькими статьями",
    "ARTICLE_SUPPORT_MISSING": "Нет подтверждения по статье",
    "OVERALL_SUPPORT_MISSING": "Нет подтверждения по всем статьям",
    "ARTICLE_REPORT_MISSING": "В КФЗ нет суммы статьи",
    "OVERALL_REPORT_MISSING": "В КФЗ нет итоговой суммы",
    "ARTICLE_AMOUNT_INVALID": "Сумма статьи требует проверки",
    "ARTICLE_MATCHES_SUPPORT": "Суммы совпадают",
    "OVERALL_MATCHES_SUPPORT": "Суммы совпадают",
    "ARTICLE_REPORT_EXCEEDS_SUPPORT": "Представленная первичка подтверждает меньшую сумму",
    "OVERALL_REPORT_EXCEEDS_SUPPORT": "Представленная первичка подтверждает меньшую сумму",
    "ARTICLE_SUPPORT_EXCEEDS_REPORT": "Сумма первички больше суммы КФЗ",
    "OVERALL_SUPPORT_EXCEEDS_REPORT": "Сумма первички больше суммы КФЗ",
}
_EXCLUSIONS = {
    "DUPLICATE": "Дублирующая операция",
    "OTHER_CONTRACT": "Другой договор",
    "OTHER_PERIOD": "Другой период",
    "TECHNICAL": "Технический документ",
    "ORACLE": "Эталон",
    "UNRELATED": "Документ вне области проверки",
}


@dataclass
class _PackageRun:
    running: bool = False
    result: AnalysisResult | None = None
    error: str | None = None


def _show_scope(profile: CaseProfile) -> None:
    period = profile.service_period
    st.subheader("Область проверки")
    st.text(f"Кейс {profile.case_number} · договор {profile.contract}")
    st.text(
        f"Строка КФЗ «{profile.target_row_label}» · период "
        f"{period.start:%d.%m.%Y}–{period.end:%d.%m.%Y}"
    )
    st.text("Статьи " + ", ".join(profile.article_codes))


def _check_label(check: Check) -> str:
    kind, _, key = check.check_id.partition(":")
    if kind == "ARTICLE_AMOUNT":
        return f"{key} · {_ARTICLES[key]}" if key in _ARTICLES else key
    if kind == "SUPPORT_TOTAL":
        return "Итого по первичным документам"
    if kind == "REPORT_TOTAL":
        return "Внутренний итог КФЗ"
    if kind == "REPORT_FIELD":
        field = key.rsplit(":", 1)[-1]
        if field in _BREAKDOWNS:
            return _BREAKDOWNS[field]
        article, _, part = field.partition("_")
        if part in {"base_pay", "additional_pay"}:
            basis = "Основная" if part == "base_pay" else "Дополнительная"
            return f"{article} · {basis} {'часть взносов' if article == '0300' else 'оплата труда'}"
        return f"{article} · {_BREAKDOWNS.get(part, part)}"
    return check.target_id


def _check_rows(result: AnalysisResult) -> list[dict[str, str]]:
    currency = result.scope.currency
    rows = []
    for check in result.checks:
        calculation = check.calculation
        reported = (
            calculation.actual if calculation else operand_value(check, "reported")
        )
        supported = (
            calculation.expected if calculation else operand_value(check, "supported")
        )
        labor = check.check_id.startswith("REPORT_FIELD:") and check.check_id.endswith(
            ":labor"
        )
        rows.append(
            {
                "Показатель": _check_label(check),
                "Статус": CHECK_STATE_LABELS[check.state],
                "КФЗ": (reported or "—") if labor else format_money(reported, currency),
                "По документам / расчёту": format_money(supported, currency),
                "Разница": format_delta(
                    calculation.delta if calculation else None, currency
                ),
                "Основание": _CHECK_REASONS.get(
                    check.reason_code,
                    REASON_LABELS.get(check.reason_code, check.reason_code),
                ),
            }
        )
    return rows


def _show_result(result: AnalysisResult) -> None:
    if isinstance(result.matching_context, CaseMatching):
        _show_scope(result.matching_context.profile)
    st.subheader(DECISION_LABELS[result.decision])
    st.caption(f"Запуск {result.run_id} · редакция {result.revision}")
    st.write(EXECUTION_LABELS[result.execution_status])
    coverage = result.coverage
    left, right = st.columns(2)
    left.metric(
        "Полностью проверено строк КФЗ",
        f"{coverage.checked_rows} / {coverage.report_rows}",
    )
    right.metric(
        "Выполнено проверок",
        f"{coverage.completed_checks} / {coverage.required_checks}",
    )
    if coverage.completed_checks < coverage.required_checks:
        st.warning(
            "Часть показателей требует дополнительных документов или ручной проверки."
        )

    if result.checks:
        st.subheader("Показатели КФЗ")
        st.dataframe(_check_rows(result), hide_index=True)

    names = {file.file_id: file.name for file in result.files}
    if result.excluded_documents:
        st.subheader("Исключено из расчёта")
        st.dataframe(
            [
                {
                    "Файл": names.get(item.file_id, item.file_id),
                    "Причина": _EXCLUSIONS[item.reason],
                    "Подробности": item.detail,
                }
                for item in result.excluded_documents
            ],
            hide_index=True,
        )

    with st.expander(f"Статусы файлов · {len(result.files)}"):
        st.dataframe(
            [
                {
                    "Файл": file.name,
                    "Роль": FILE_ROLE_LABELS.get(file.role, file.role),
                    "Обработка": FILE_STATUS_LABELS[file.status],
                }
                for file in result.files
            ],
            hide_index=True,
        )

    if result.issues:
        with st.expander(
            f"Замечания обработки и сверки · {len(result.issues)}", expanded=True
        ):
            st.dataframe(
                [
                    {
                        "Файл": names.get(issue.file_id, issue.file_id)
                        if issue.file_id
                        else "Комплект",
                        "Код": issue.code,
                        "Подробности": issue.detail,
                    }
                    for issue in result.issues
                ],
                hide_index=True,
            )


def screen_package() -> None:
    st.title("Проверка комплекта")
    run = st.session_state.setdefault(_RUN_KEY, _PackageRun())
    if run.result is not None:
        if st.button("Загрузить новый комплект", key="nitka_package_new"):
            st.session_state.pop(_RUN_KEY)
            reset_upload_state()
            st.rerun()
        _show_result(run.result)
        return
    if run.running:
        st.info("Комплект обрабатывается. Обновите статус, чтобы увидеть результат.")
        st.button("Обновить статус")
        return

    try:
        profile = load_profile()
    except (OSError, ValueError):
        _LOGGER.exception("Cannot load the case profile")
        st.error(
            "Не удалось загрузить профиль проверки. Проверьте JSON и NITKA_CASE_PROFILE."
        )
        return
    _show_scope(profile)
    if run.error:
        st.error(run.error)
    action = render_package_upload()
    if action is None:
        return

    run.running = True
    run.error = None
    try:
        with st.status("Проверяем комплект", expanded=True):
            # Store the result before the next Streamlit call can interrupt this run.
            run.result = run_uploaded_package(
                action, profile, runs_root(), ocr=local_ocr(), show_stage=st.write
            )
    except PackageUploadError as exc:
        run.error = str(exc)
    except PackageLimitError as exc:
        run.error = f"Уменьшите объём комплекта. {exc}"
    except (OSError, RuntimeError, ValueError):
        _LOGGER.exception("Cannot process the uploaded package")
        run.error = "Не удалось обработать комплект. Проверьте доступ к хранилищу и журнал приложения."
    finally:
        run.running = False
    st.rerun()
