from __future__ import annotations

import streamlit as st

from .actions import Action, Navigate, ResetAll, ResetKit
from .demo_models import RunSnapshot
from .display import DECISION_LABELS, DECISION_TONES, short_run_id
from .presentation import DECISION_SHORT_LABELS, _badge, _esc


def _render_sidebar(state: dict[str, object]) -> Action | None:
    action: Action | None = None
    snapshot = state.get("snapshot")
    with st.sidebar:
        st.markdown(
            (
                '<div class="nk-brand">'
                '<div class="nk-brand-mark">Н</div>'
                "<div><b>Нитка</b><span>доказательная сверка</span></div>"
                "</div>"
            ),
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="nk-side-label">Демонстрационный контур</div>',
            unsafe_allow_html=True,
        )

        if isinstance(snapshot, RunSnapshot):
            result = snapshot.result
            tone = DECISION_TONES.get(result.decision, "neutral")
            label = DECISION_SHORT_LABELS.get(
                result.decision,
                DECISION_LABELS.get(result.decision, result.decision),
            )
            st.markdown(
                (
                    '<div class="nk-side-run">'
                    f'<div class="nk-side-kit">{_esc(snapshot.kit.info.title)}</div>'
                    f"{_badge(tone, label)}"
                    f'<div class="nk-side-meta">Редакция {result.revision}'
                    f" · {_esc(short_run_id(result.run_id))}</div>"
                    "</div>"
                ),
                unsafe_allow_html=True,
            )

        nav = (
            ("catalog", "01 · Комплект"),
            ("results", "02 · Результат"),
            ("export", "03 · Экспорт"),
        )
        for screen_id, label in nav:
            disabled = screen_id != "catalog" and not isinstance(snapshot, RunSnapshot)
            if st.button(
                label,
                key=f"nav-{screen_id}",
                use_container_width=True,
                type="primary" if state.get("screen") == screen_id else "secondary",
                disabled=disabled,
            ):
                action = Navigate(screen_id)

        st.markdown('<div class="nk-side-rule"></div>', unsafe_allow_html=True)
        if isinstance(snapshot, RunSnapshot):
            if st.button(
                "Вернуть редакцию 1",
                key="nav-reset-kit",
                use_container_width=True,
            ):
                action = ResetKit()
        if st.button(
            "Начать заново",
            key="nav-reset-all",
            use_container_width=True,
        ):
            action = ResetAll()

        st.markdown(
            (
                '<div class="nk-side-foot">'
                "Адаптер: <b>demo_backend.py</b><br/>"
                "Данные синтетические. Прогресс обработки имитируется."
                "</div>"
            ),
            unsafe_allow_html=True,
        )
    return action


def _render_flash(state: dict[str, object]) -> None:
    flash = state.get("flash")
    if not flash:
        return
    kind, text = flash
    state["flash"] = None
    renderer = {
        "success": st.success,
        "error": st.error,
        "warning": st.warning,
        "info": st.info,
    }.get(kind, st.info)
    renderer(str(text))
