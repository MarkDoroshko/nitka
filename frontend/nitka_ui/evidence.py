from __future__ import annotations

import streamlit as st

from .actions import Action, Navigate, SaveCorrection
from .demo_models import CheckView, RunSnapshot, SourceView
from .display import (
    CHECK_STATE_LABELS,
    CHECK_STATE_TONES,
    MATCH_STATE_LABELS,
    METHOD_LABELS,
    REVIEW_STATUS_LABELS,
    source_location_text,
)
from .presentation import (
    _badge,
    _delta,
    _delta_class,
    _esc,
    _money,
    _page_head,
    _preview_html,
    _reason_explanation,
    _reason_title,
)


def _render_source_card(source: SourceView, currency: str) -> None:
    review_tone = {
        "CORRECTED": "warning",
        "CONFIRMED": "success",
        "NEEDS_REVIEW": "warning",
    }.get(source.review_status, "neutral")
    st.markdown(
        (
            '<div class="nk-source">'
            '<div class="nk-source-top">'
            f'<span class="nk-source-value">'
            f"{_esc(_money(source.value, currency))}</span>"
            f"{_badge(review_tone, REVIEW_STATUS_LABELS.get(source.review_status, source.review_status))}"
            "</div>"
            f'<div class="nk-source-doc">{_esc(source.document_title)}</div>'
            f'<div class="nk-source-meta">{_esc(source_location_text(source))}'
            f" · {_esc(source.file_name)}</div>"
            '<div class="nk-source-raw">Напечатано: '
            f"<code>{_esc(source.raw_text or '—')}</code></div>"
            f'<div class="nk-source-meta">'
            f"{METHOD_LABELS.get(source.method, source.method)}"
            f" · {_esc(source.row_label)}</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def screen_evidence(
    snapshot: RunSnapshot,
    view: CheckView,
) -> Action | None:
    result = snapshot.result
    scope = result.scope
    check = view.check
    tone = CHECK_STATE_TONES.get(check.state, "neutral")

    back_columns = st.columns([1, 4], gap="small")
    with back_columns[0]:
        if st.button(
            "← К результату",
            key="evidence-back",
            use_container_width=True,
        ):
            return Navigate("results")

    _page_head(
        f"Паспорт проверки · {view.report_row_id}",
        view.row_label,
        f"{CHECK_STATE_LABELS.get(check.state, check.state)} · "
        f"{_reason_title(check.reason_code)}",
    )

    st.markdown(
        (
            f'<div class="nk-rule {tone}">'
            '<div class="nk-rule-head">'
            f"{_badge(tone, CHECK_STATE_LABELS.get(check.state, check.state))}"
            f'<span class="nk-rule-id">{_esc(check.check_id)}</span></div>'
            f"<p>{_esc(_reason_explanation(view))}</p>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )

    calculation = check.calculation
    if calculation is not None:
        st.markdown(
            (
                '<div class="nk-formula">'
                "<div><span>Заявлено</span><b>"
                f"{_esc(_money(calculation.actual, scope.currency))}</b></div>"
                '<div class="op">−</div>'
                "<div><span>Подтверждено</span><b>"
                f"{_esc(_money(calculation.expected, scope.currency))}</b></div>"
                '<div class="op">=</div>'
                f'<div class="res {_delta_class(calculation.delta)}">'
                "<span>Отклонение</span><b>"
                f"{_esc(_delta(calculation.delta, scope.currency))}</b></div>"
                "<div><span>Допуск</span><b>"
                f"{_esc(_money(calculation.tolerance, scope.currency))}</b></div>"
                "</div>"
            ),
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            (
                '<div class="nk-empty">Расчёт не сформирован: '
                f"{_esc(_reason_title(check.reason_code))}. "
                "Неподтверждённая сумма не заменяется нулём.</div>"
            ),
            unsafe_allow_html=True,
        )

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown(
            '<div class="nk-section-label">В отчёте</div>',
            unsafe_allow_html=True,
        )
        if view.reported is not None:
            _render_source_card(view.reported, scope.currency)
        else:
            st.markdown(
                '<div class="nk-empty">Источник строки отчёта не найден.</div>',
                unsafe_allow_html=True,
            )
    with right:
        st.markdown(
            '<div class="nk-section-label">Подтверждение</div>',
            unsafe_allow_html=True,
        )
        if view.supported:
            for source in view.supported:
                _render_source_card(source, scope.currency)
                st.markdown("<div style='height: 8px'></div>", unsafe_allow_html=True)
            if view.match_state:
                st.caption(
                    "Соответствие: "
                    f"{MATCH_STATE_LABELS.get(view.match_state, view.match_state)}"
                )
        else:
            st.markdown(
                (
                    '<div class="nk-empty">Подтверждающих источников нет. '
                    "Сумма остаётся неподтверждённой, пока документ не найден "
                    "или не введён вручную.</div>"
                ),
                unsafe_allow_html=True,
            )

    preview_sources = [
        source for source in (view.reported, *view.supported) if source is not None
    ]
    if preview_sources:
        st.markdown(
            '<div class="nk-section-label">Факсимиле источника</div>',
            unsafe_allow_html=True,
        )
        tabs = st.tabs(
            [
                f"{source.document_kind} · {source.document_title}"
                for source in preview_sources
            ]
        )
        for tab, source in zip(tabs, preview_sources):
            with tab:
                st.caption(
                    f"{source_location_text(source)} · {source.file_name} · "
                    "демонстрационное превью, не оригинал документа"
                )
                st.markdown(
                    _preview_html(source.preview_lines, source.raw_text),
                    unsafe_allow_html=True,
                )

    st.markdown(
        '<div class="nk-section-label">Исправить значение</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Исправление создаёт новую редакцию, сохраняет старое значение "
        "в истории и пересчитывает проверки. Новое значение проходит "
        "нормализацию суммы."
    )
    if view.match_state == "AMBIGUOUS":
        st.warning(
            "Исправление суммы не снимет неоднозначность соответствия: "
            "автоматический выбор между документами запрещён."
        )

    correction_sources = [
        source for source in (view.reported, *view.supported) if source is not None
    ]
    if not correction_sources:
        st.info("В этой проверке нет источника, который можно исправить.")
    else:
        source_by_id = {source.source_id: source for source in correction_sources}

        def source_label(source_id: str) -> str:
            source = source_by_id[source_id]
            role = "Отчёт" if source.role == "reported" else "Первичка"
            return (
                f"{role} · {source.document_title} · "
                f"{_money(source.value, scope.currency)}"
            )

        with st.form("correction-form", clear_on_submit=False):
            selected_id = st.selectbox(
                "Что исправить",
                list(source_by_id),
                format_func=source_label,
            )
            selected = source_by_id[selected_id]
            new_value = st.text_input(
                "Новое значение",
                value=selected.value or "",
                help="Например: 120 000,00. Можно ввести и канонический вид 120000.00.",
            )
            comment = st.text_input(
                "Комментарий",
                placeholder="Что проверили и почему меняем значение",
            )
            submitted = st.form_submit_button(
                "Сохранить и пересчитать",
                type="primary",
                use_container_width=True,
            )
        if submitted:
            return SaveCorrection(selected_id, new_value, comment)

    if snapshot.history:
        st.markdown(
            '<div class="nk-section-label">История редакций</div>',
            unsafe_allow_html=True,
        )
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
    return None
