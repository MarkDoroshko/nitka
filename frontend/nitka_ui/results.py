from __future__ import annotations

from typing import Sequence

import streamlit as st

from .actions import Action, Navigate, ResetKit, ViewCheck
from .demo_models import CheckView, RunSnapshot
from .display import (
    CHECK_STATE_LABELS,
    CHECK_STATE_TONES,
    DECISION_LABELS,
    DECISION_TONES,
    EXECUTION_LABELS,
    FILE_ROLE_LABELS,
    FILE_STATUS_LABELS,
    short_run_id,
)
from .presentation import (
    _badge,
    _decision_counts,
    _delta,
    _delta_class,
    _esc,
    _money,
    _operand_value,
    _page_head,
    _short_reason,
)


def _render_check_row(snapshot: RunSnapshot, view: CheckView) -> Action | None:
    result = snapshot.result
    currency = result.scope.currency
    calculation = view.check.calculation
    reported_value = (
        calculation.actual if calculation else _operand_value(view.check, "reported")
    )
    supported_value = (
        calculation.expected if calculation else _operand_value(view.check, "supported")
    )
    tone = CHECK_STATE_TONES.get(view.check.state, "neutral")
    columns = st.columns([3.2, 1.25, 1.4, 1.4, 1.35, 1.1], gap="small")
    with columns[0]:
        st.markdown(
            f'<div class="nk-row-title">{_esc(view.row_label)}</div>'
            f'<div class="nk-row-sub">{_esc(view.report_row_id)} · '
            f"{_esc(view.check.check_id)}</div>",
            unsafe_allow_html=True,
        )
    with columns[1]:
        st.markdown(
            _badge(tone, CHECK_STATE_LABELS.get(view.check.state, view.check.state))
            + f'<div class="nk-row-reason">'
            f"{_esc(_short_reason(view.check.reason_code))}</div>",
            unsafe_allow_html=True,
        )
    with columns[2]:
        st.markdown(
            '<div class="nk-cell-label">Заявлено</div>'
            f'<div class="nk-money">'
            f"{_esc(_money(reported_value, currency))}"
            "</div>",
            unsafe_allow_html=True,
        )
    with columns[3]:
        st.markdown(
            '<div class="nk-cell-label">Подтверждено</div>'
            f'<div class="nk-money">'
            f"{_esc(_money(supported_value, currency))}"
            "</div>",
            unsafe_allow_html=True,
        )
    with columns[4]:
        delta = calculation.delta if calculation else None
        st.markdown(
            '<div class="nk-cell-label">Отклонение</div>'
            f'<div class="nk-money {_delta_class(delta)}">'
            f"{_esc(_delta(delta, currency))}</div>",
            unsafe_allow_html=True,
        )
    with columns[5]:
        if st.button(
            "Разбор",
            key=f"check-{view.check.check_id}",
            use_container_width=True,
        ):
            return ViewCheck(view.check.check_id)
    return None


def screen_results(
    snapshot: RunSnapshot,
    views: Sequence[CheckView],
) -> Action | None:
    result = snapshot.result
    kit = snapshot.kit.info
    scope = result.scope
    passes, fails, not_run = _decision_counts(snapshot)
    tone = DECISION_TONES.get(result.decision, "neutral")
    action: Action | None = None

    _page_head(
        f"Результат · редакция {result.revision}",
        DECISION_LABELS.get(result.decision, result.decision),
        f"{kit.title}. {kit.description}",
    )

    execution = EXECUTION_LABELS.get(result.execution_status, result.execution_status)
    st.markdown(
        (
            f'<div class="nk-decision {tone}">'
            '<div class="nk-decision-copy">'
            f'<div class="nk-status-dot {tone}"></div><div>'
            f'<div class="nk-decision-title">'
            f"{_esc(DECISION_LABELS.get(result.decision, result.decision))}</div>"
            f'<div class="nk-decision-sub">'
            f"{_esc(execution)} · период {_esc(scope.reporting_period)} · "
            f"{_esc(scope.currency)} / {_esc(scope.amount_basis)}</div>"
            "</div></div>"
            '<div class="nk-decision-meta">'
            f"run_id<br/><b>{_esc(short_run_id(result.run_id))}</b><br/>"
            f"правила {_esc(result.rules_version)}"
            "</div></div>"
        ),
        unsafe_allow_html=True,
    )

    metrics = st.columns(5, gap="small")
    metrics[0].metric("Строк в отчёте", result.coverage.report_rows)
    metrics[1].metric(
        "Проверено строк",
        f"{result.coverage.checked_rows}/{result.coverage.report_rows}",
    )
    metrics[2].metric("Расхождений", fails)
    metrics[3].metric("Не проверено", not_run)
    metrics[4].metric("Редакция", result.revision)

    required = result.coverage.required_checks
    completed = result.coverage.completed_checks
    coverage_ratio = completed / required if required else 0.0
    st.progress(
        max(0.0, min(1.0, coverage_ratio)),
        text=(
            f"Обязательные проверки: {completed} из {required}. "
            f"Пройдено: {passes}. Замечаний: {fails}."
        ),
    )

    if result.execution_status != "COMPLETED" or result.issues:
        st.warning(
            "Проверка неполная: часть файлов или страниц не обработана. "
            "Зелёный статус в таком запуске запрещён."
        )

    st.markdown(
        '<div class="nk-section-label">Проверки по строкам</div>',
        unsafe_allow_html=True,
    )
    header = st.columns([3.2, 1.25, 1.4, 1.4, 1.35, 1.1], gap="small")
    for column, label in zip(
        header,
        ("Строка", "Статус", "Заявлено", "Подтверждено", "Отклонение", ""),
    ):
        column.markdown(f'<div class="nk-th">{label}</div>', unsafe_allow_html=True)

    for view in views:
        with st.container(border=True):
            row_action = _render_check_row(snapshot, view)
        if row_action is not None:
            action = row_action

    if result.issues:
        st.markdown(
            '<div class="nk-section-label">Ошибки обработки</div>',
            unsafe_allow_html=True,
        )
        for issue in result.issues:
            with st.container(border=True):
                st.markdown(
                    _badge("warning", issue.code)
                    + (
                        f'<div class="nk-issue-file">{_esc(issue.file_id)}</div>'
                        if issue.file_id
                        else ""
                    ),
                    unsafe_allow_html=True,
                )
                st.write(issue.detail)

    with st.expander(f"Реестр файлов · {len(result.files)}"):
        for file in result.files:
            columns = st.columns([3.2, 1.2, 1.2, 1.5], gap="small")
            columns[0].markdown(f"**{_esc(file.name)}**")
            columns[1].caption(FILE_ROLE_LABELS.get(file.role, file.role))
            columns[2].caption(FILE_STATUS_LABELS.get(file.status, file.status))
            columns[3].caption(f"sha256: {file.sha256[:10]}…")

    if snapshot.history:
        with st.expander(f"История редакций · {len(snapshot.history)}", expanded=False):
            for item in reversed(snapshot.history):
                st.markdown(
                    (
                        '<div class="nk-history">'
                        f"<b>Редакция {item.revision} · {_esc(item.row_label)}</b><br/>"
                        f"{_esc(_money(item.old_value, scope.currency))} → "
                        f"{_esc(_money(item.new_value, scope.currency))}"
                        f"<span>{_esc(item.comment or 'Без комментария')}</span>"
                        "</div>"
                    ),
                    unsafe_allow_html=True,
                )

    st.markdown("<div style='height: 8px'></div>", unsafe_allow_html=True)
    buttons = st.columns([1.5, 1.2, 1.3], gap="small")
    with buttons[0]:
        if st.button(
            "Перейти к экспорту",
            key="results-export",
            use_container_width=True,
            type="primary",
        ):
            action = Navigate("export")
    with buttons[1]:
        if st.button(
            "Сменить комплект",
            key="results-catalog",
            use_container_width=True,
        ):
            action = Navigate("catalog")
    with buttons[2]:
        if snapshot.history and st.button(
            "Вернуть редакцию 1",
            key="results-reset",
            use_container_width=True,
        ):
            action = ResetKit()
    return action
