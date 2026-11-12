from __future__ import annotations

from typing import Any

from .results_models import FILTER_STATES, Action, CoverageView, ResultRow, ResultsView
from .results_view import _get, _items, _text, build_results_view, filter_rows


def render_results(result: object, *, key_prefix: str = "results") -> Action | None:
    """Рисует экран результатов и возвращает действие пользователя или ``None``."""

    st = _streamlit()
    if result is None:
        st.info("Результат сверки еще не готов.")
        return None

    view = build_results_view(result)
    _render_summary(st, view)
    _render_incomplete_warning(st, view)
    _render_coverage(st, view.coverage)

    # Ключи привязаны к запуску и редакции: при новом результате выбор строки
    # из предыдущей редакции не должен открывать чужое замечание.
    run_id = _text(_get(result, "run_id"))
    revision = _text(_get(result, "revision"))
    identity = "_".join(part for part in (run_id, revision) if part)
    widget_prefix = f"{key_prefix}_{identity}" if identity else key_prefix
    return _render_checks(st, view, widget_prefix)


def _render_summary(st: Any, view: ResultsView) -> None:
    st.subheader("Итог проверки")
    columns = st.columns(3)
    columns[0].metric("Результат сверки", view.decision_label)
    columns[1].metric("Техническая обработка", view.execution_label)
    columns[2].metric("Замечаний", str(view.actionable_count))

    if view.decision_detail:
        st.caption(view.decision_detail)

    if view.discrepancies_count:
        st.error(
            f"Расхождений: {view.discrepancies_count}. "
            "Подробности приведены в таблице ниже."
        )
    elif view.decision == "NO_DISCREPANCIES" and not view.incomplete:
        st.success("Расхождения не выявлены в проверенном объеме.")

    meta = " ".join(part for part in (view.run_text, view.scope_text) if part)
    if meta:
        st.caption(meta)


def _render_incomplete_warning(st: Any, view: ResultsView) -> None:
    # Предупреждение рисуется отдельно от итога: найденное расхождение и
    # неполнота проверки должны быть видны одновременно.
    if view.warning:
        st.warning(view.warning)


def _render_coverage(st: Any, coverage: CoverageView) -> None:
    st.subheader("Покрытие")
    left, right = st.columns(2)

    left.metric(
        "Проверено строк отчета",
        f"{coverage.checked_rows} из {coverage.report_rows}",
    )
    left.progress(_progress(coverage.row_ratio))
    if coverage.report_rows:
        left.caption("Полностью проверенные строки / все строки отчета.")
    else:
        left.caption("Строки отчета не определены.")

    right.metric(
        "Выполнено обязательных проверок",
        f"{coverage.completed_checks} из {coverage.required_checks}",
    )
    right.progress(_progress(coverage.check_ratio))
    if coverage.required_checks:
        right.caption("Выполненные обязательные проверки / все обязательные.")
    else:
        right.caption("Обязательные проверки не определены.")


def _render_checks(st: Any, view: ResultsView, key_prefix: str) -> Action | None:
    st.subheader("Проверки")
    left, right = st.columns([1, 2])
    status_filter = left.selectbox(
        "Фильтр по статусу",
        list(FILTER_STATES),
        index=0,
        key=f"{key_prefix}_status",
    )
    query = right.text_input(
        "Поиск по строке, причине, сумме или ID",
        key=f"{key_prefix}_query",
    )

    rows = filter_rows(view.rows, status_filter, query)
    st.caption(f"Показано {len(rows)} из {len(view.rows)}.")

    if not rows:
        st.info("Нет проверок, удовлетворяющих выбранным фильтрам.")
        return None

    table_key = f"{key_prefix}_table"
    session_state = getattr(st, "session_state", None)
    filter_key = f"{key_prefix}_filter"
    filter_signature = (status_filter, query)
    if session_state is not None:
        previous_filter = session_state.get(filter_key)
        if previous_filter is not None and previous_filter != filter_signature:
            # Смена фильтра не должна открывать строку, выбранную в старом
            # наборе данных.
            session_state[table_key] = {"selection": {"rows": []}}
        session_state[filter_key] = filter_signature

    selected = _render_table(
        st,
        [row.table_row for row in rows],
        key=table_key,
    )
    selected_row = (
        rows[selected] if selected is not None and 0 <= selected < len(rows) else None
    )
    selected_action = _open_action(selected_row) if selected_row else None

    # Выбор строки сохраняется между rerun. Запоминаем уже обработанный
    # выбор, чтобы не открывать одно и то же замечание повторно.
    selection_key = f"{key_prefix}_selected_issue"
    session_state = getattr(st, "session_state", None)
    if selected_row is not None and selected_action is not None:
        signature = (status_filter, query, selected, selected_row.issue_id)
        if session_state is None:
            return selected_action
        if session_state.get(selection_key) != signature:
            session_state[selection_key] = signature
            return selected_action
    elif session_state is not None:
        session_state[selection_key] = None

    actionable = [row for row in rows if row.is_actionable]
    if actionable:
        st.markdown("#### Замечания")
        st.caption("Нажмите на замечание, чтобы открыть его доказательства.")
        for index, row in enumerate(actionable):
            label = f"Открыть: {row.target_id} — {row.reason}"
            if st.button(
                label,
                key=_button_key(key_prefix, index, row),
                help=row.detail,
            ):
                return _open_action(row)

    return None


def _render_table(st: Any, table_rows: list[dict[str, str]], *, key: str) -> int | None:
    """Рисует таблицу и возвращает индекс выбранной строки, если он есть."""

    try:
        event = st.dataframe(
            table_rows,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key=key,
        )
    except TypeError:
        # Совместимость со старыми версиями Streamlit без выбора строк.
        try:
            st.dataframe(table_rows, hide_index=True)
        except TypeError:
            st.dataframe(table_rows)
        return None
    return _selected_index(event)


def _open_action(row: ResultRow) -> Action | None:
    if row.issue_id is None:
        return None
    return {"action": "open_issue", "issue_id": row.issue_id}


def _progress(ratio: float) -> float:
    return min(1.0, max(0.0, ratio))


def _button_key(key_prefix: str, index: int, row: ResultRow) -> str:
    safe_id = "".join(
        character if character.isalnum() else "_" for character in (row.issue_id or "")
    )
    return f"{key_prefix}_open_{index}_{safe_id}"


def _selected_index(event: object) -> int | None:
    selection = _get(event, "selection")
    rows = _get(selection, "rows") if selection is not None else None
    selected = _items(rows)
    if not selected:
        return None
    try:
        return int(selected[0])
    except (TypeError, ValueError):
        return None


def _streamlit() -> Any:
    try:
        import streamlit as st
    except ModuleNotFoundError as error:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "Для экрана результатов установите streamlit: pip install streamlit"
        ) from error
    return st
