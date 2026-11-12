from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .optional_streamlit import st
from .review_html import _cell_html, _ledger_html, _page_html
from .review_sources import _as_mapping, _field, _file_name, _short_location, _source_id
from .review_view import _as_decimal, _build_view

_STYLES_PATH = Path(__file__).resolve().parents[1] / "assets" / "review" / "styles.css"


def _back_action() -> dict[str, str]:
    return {"action": "back"}


def _save_action(issue_id: str, source_id: str, value: str) -> dict[str, str]:
    return {
        "action": "save_review",
        "issue_id": issue_id,
        "source_id": source_id,
        "value": value,
    }


def _confirm_action(issue_id: str, source_id: str) -> dict[str, str]:
    return {
        "action": "confirm_match",
        "issue_id": issue_id,
        "source_id": source_id,
    }


def _key(issue_id: str, suffix: str) -> str:
    return f"nitka_review__{issue_id or 'current'}__{suffix}"


# ---------------------------------------------------------------------------
# Streamlit
# ---------------------------------------------------------------------------


def _inject_styles() -> None:
    if st is None:  # pragma: no cover - защита от запуска без Streamlit
        return
    try:
        css = _STYLES_PATH.read_text(encoding="utf-8")
    except OSError:
        return
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def _render_correction(view: Mapping[str, Any]) -> dict[str, str] | None:
    """Ручная правка одного из источников замечания."""

    issue_id = view["issue_id"]
    available: list[object] = []
    for source in [*view["reported_sources"], *view["support_sources"]]:
        if _source_id(source) and not _as_mapping(source).get("_missing"):
            if all(_source_id(source) != _source_id(item) for item in available):
                available.append(source)

    st.markdown("### Ручная правка")
    if not available:
        st.caption("Нет источника, значение которого можно исправить.")
        return None

    source_ids = [_source_id(source) for source in available]
    selected_id = st.selectbox(
        "Источник",
        options=source_ids,
        format_func=lambda source_id: next(
            (
                f"{_short_location(source)} · {_file_name(source) or source_id}"
                for source in available
                if _source_id(source) == source_id
            ),
            source_id,
        ),
        key=_key(issue_id, "edit_source"),
    )
    selected = next(source for source in available if _source_id(source) == selected_id)
    current_value = _field(selected, "value")
    value = st.text_input(
        "Новое значение",
        value=str(current_value) if current_value is not None else "",
        key=_key(issue_id, f"edit_value__{selected_id}"),
        help="Введите сумму так, как она должна учитываться в сверке.",
    )
    if st.button(
        "Сохранить исправление",
        key=_key(issue_id, f"save__{selected_id}"),
        use_container_width=True,
    ):
        clean_value = value.strip()
        if not clean_value:
            st.error("Значение не может быть пустым.")
        elif _as_decimal(clean_value) is None:
            st.error("Не удалось прочитать сумму. Используйте, например, 120000.00.")
        else:
            return _save_action(issue_id, selected_id, clean_value)
    return None


def _render_confirmation(view: Mapping[str, Any]) -> dict[str, str] | None:
    issue_id = view["issue_id"]
    supports = [
        source
        for source in view["support_sources"]
        if _source_id(source) and not _as_mapping(source).get("_missing")
    ]

    st.markdown("### Подтверждение документа")
    if not supports:
        st.caption("Подтверждающий документ не найден.")
        return None

    st.caption(
        "Подтвердите документ, если он действительно относится к этой строке отчёта."
    )
    for source in supports:
        source_id = _source_id(source)
        label = f"Подтвердить {_short_location(source)}"
        if st.button(
            label,
            key=_key(issue_id, f"confirm__{source_id}"),
            type="primary",
            use_container_width=True,
        ):
            return _confirm_action(issue_id, source_id)
    return None


def render_review(issue: object, sources: object) -> dict[str, str] | None:
    """Показать замечание и вернуть выбранное пользователем действие."""

    if st is None:  # pragma: no cover - зависит от окружения
        raise RuntimeError("Для экрана проверки требуется Streamlit.")

    view = _build_view(issue, sources)
    issue_id = view["issue_id"]
    _inject_styles()

    top_left, top_right = st.columns([1, 5], vertical_alignment="center")
    with top_left:
        if st.button("← Назад", key=_key(issue_id, "back"), use_container_width=True):
            return _back_action()
    with top_right:
        st.caption(
            "Сверка сумм: отчёт → подтверждающий документ. "
            "Проверьте обе стороны, затем подтвердите документ или исправьте значение."
        )

    st.markdown(_ledger_html(view), unsafe_allow_html=True)

    left, right = st.columns([1.55, 1], gap="large")

    with left:
        pane = st.radio(
            "Что показать",
            options=("Ячейка отчёта", "Страница акта"),
            horizontal=True,
            label_visibility="collapsed",
            key=_key(issue_id, "pane"),
        )
        st.markdown(
            '<p class="nitka-review nr-pane-label">Источник выбранного значения</p>',
            unsafe_allow_html=True,
        )

        if pane == "Ячейка отчёта":
            if view["reported_sources"]:
                for source in view["reported_sources"]:
                    st.markdown(_cell_html(source), unsafe_allow_html=True)
            else:
                st.markdown(_cell_html(None), unsafe_allow_html=True)
        else:
            if view["support_sources"]:
                for source in view["support_sources"]:
                    st.markdown(_page_html(source), unsafe_allow_html=True)
            else:
                st.markdown(_page_html(None), unsafe_allow_html=True)

    with right:
        action = _render_confirmation(view)
        if action is not None:
            return action

        st.divider()
        action = _render_correction(view)
        if action is not None:
            return action

    return None
