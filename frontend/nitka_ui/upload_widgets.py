from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .optional_streamlit import st
from .upload_display import (
    _display_name,
    _file_icon,
    _file_type_label,
    _files_word,
    _format_bytes,
)
from .upload_state import (
    _MAX_PACKAGE_BYTES,
    _REPORT_TYPES,
    _SCENARIO_KEY,
    _SUPPORT_TYPES,
    DemoAction,
    DemoScenario,
    RunAction,
    UploadAction,
    UploadedFileLike,
    _file_name,
    _file_size,
    _get_state,
    _input_problems,
    _merge_documents,
    _normalize_scenarios,
    _store,
    _total_size,
    _UploadState,
)


def _show_notice(state: _UploadState) -> None:
    if state.notice is None:
        return

    level, message = state.notice
    state.notice = None
    if level == "warning":
        st.warning(message)
    else:
        st.success(message)


def _render_file_row(
    file: UploadedFileLike,
    *,
    caption: str,
    delete_key: str,
) -> bool:
    """Рисует строку файла и возвращает True, если нажато удаление."""

    with st.container(border=True):
        icon_column, info_column, action_column = st.columns(
            [0.4, 5.0, 1.15],
            vertical_alignment="center",
        )
        with icon_column:
            st.markdown(_file_icon(_file_name(file)))
        with info_column:
            st.markdown(f"**{_display_name(_file_name(file))}**")
            st.caption(
                f"{caption} · {_format_bytes(_file_size(file))} · "
                f"{_file_type_label(_file_name(file))}"
            )
        with action_column:
            return st.button(
                "Удалить",
                key=delete_key,
                help=f"Удалить «{_file_name(file)}»",
                use_container_width=True,
            )

    return False


def _render_report_section(state: _UploadState) -> bool:
    st.markdown("#### Отчёт")
    st.caption("Один итоговый отчёт в формате XLSX, CSV или PDF.")

    uploaded = st.file_uploader(
        "Выберите отчёт",
        type=list(_REPORT_TYPES),
        accept_multiple_files=False,
        key=f"nitka_upload_report_{state.report_revision}",
        label_visibility="collapsed",
    )

    changed = False
    if uploaded is not None:
        previous = state.report
        state.report = _store(uploaded)
        state.report_revision += 1
        changed = True
        uploaded_name = _display_name(_file_name(uploaded))
        if previous is not None and previous.digest == state.report.digest:
            state.notice = (
                "success",
                f"Отчёт «{uploaded_name}» уже выбран.",
            )
        else:
            state.notice = (
                "success",
                f"Отчёт «{uploaded_name}» добавлен.",
            )

    if state.report is None:
        st.caption("Отчёт пока не добавлен.")
    elif _render_file_row(
        state.report.file,
        caption="Итоговый отчёт",
        delete_key=f"nitka_upload_delete_report_{state.report.digest[:12]}",
    ):
        state.report = None
        state.report_revision += 1
        state.notice = ("success", "Отчёт удалён.")
        st.rerun()

    return changed


def _render_documents_section(state: _UploadState) -> bool:
    header_column, count_column = st.columns([4.0, 1.0])
    with header_column:
        st.markdown("#### Первичные документы")
    with count_column:
        st.caption(f"{len(state.documents)} {_files_word(len(state.documents))}")

    st.caption("Можно добавить несколько файлов: PDF, PNG/JPG, XLSX или CSV.")

    uploaded = st.file_uploader(
        "Выберите первичные документы",
        type=list(_SUPPORT_TYPES),
        accept_multiple_files=True,
        key=f"nitka_upload_documents_{state.documents_revision}",
        label_visibility="collapsed",
    )

    changed = False
    if uploaded:
        before = len(state.documents)
        state.documents, skipped = _merge_documents(state.documents, uploaded)
        added = len(state.documents) - before
        state.documents_revision += 1
        changed = True
        if skipped:
            state.notice = ("warning", " ".join(skipped))
        else:
            state.notice = (
                "success",
                f"Добавлено: {added} {_files_word(added)}.",
            )

    if not state.documents:
        st.caption("Документы пока не добавлены.")
        return changed

    for index, document in enumerate(state.documents, start=1):
        if _render_file_row(
            document.file,
            caption=f"Первичный документ {index}",
            delete_key=(f"nitka_upload_delete_document_{document.digest[:12]}_{index}"),
        ):
            removed_name = _display_name(_file_name(document.file))
            del state.documents[index - 1]
            state.documents_revision += 1
            state.notice = ("success", f"Документ «{removed_name}» удалён.")
            st.rerun()

    return changed


def _render_demo(
    scenarios: Sequence[DemoScenario],
) -> DemoAction | None:
    st.markdown("#### Демонстрационный комплект")
    st.caption("Готовый синтетический набор файлов. Его можно запустить без загрузки.")

    scenario_ids = [scenario.id for scenario in scenarios]
    if st.session_state.get(_SCENARIO_KEY) not in scenario_ids:
        st.session_state[_SCENARIO_KEY] = scenario_ids[0]

    titles = {scenario.id: scenario.title for scenario in scenarios}
    selected_id = st.radio(
        "Сценарий",
        options=scenario_ids,
        format_func=lambda scenario_id: titles[scenario_id],
        key=_SCENARIO_KEY,
    )
    selected = next(scenario for scenario in scenarios if scenario.id == selected_id)

    with st.container(border=True):
        st.markdown(f"**{_display_name(selected.title)}**")
        if selected.description:
            st.write(selected.description)
        if selected.expected:
            st.caption(f"Ожидаемый результат: {selected.expected}")

    if st.button(
        "Запустить демонстрацию",
        key="nitka_upload_demo_submit",
        type="primary",
        use_container_width=True,
    ):
        return {"action": "demo", "scenario": selected_id}

    return None


def _render_own_files(state: _UploadState) -> RunAction | None:
    st.markdown("#### Свои файлы")
    st.caption("Один отчёт и любое количество документов-оснований.")
    st.warning(
        "Свои файлы обрабатываются в демонстрационном режиме. "
        "Результат не является юридически значимым заключением."
    )

    changed = _render_report_section(state)
    st.divider()
    changed = _render_documents_section(state) or changed
    if changed:
        st.rerun()

    total_size = _total_size(state)
    file_count = (1 if state.report is not None else 0) + len(state.documents)
    summary_column, clear_column = st.columns([4.0, 1.0])
    with summary_column:
        st.caption(
            f"В комплекте: {file_count} {_files_word(file_count)} · "
            f"{_format_bytes(total_size)} из {_format_bytes(_MAX_PACKAGE_BYTES)}"
        )
    with clear_column:
        if file_count and st.button(
            "Очистить",
            key="nitka_upload_clear",
            use_container_width=True,
        ):
            state.report = None
            state.documents.clear()
            state.report_revision += 1
            state.documents_revision += 1
            state.notice = ("success", "Комплект очищен.")
            st.rerun()

    if state.report is not None and any(
        document.digest == state.report.digest for document in state.documents
    ):
        st.warning(
            "Один из первичных документов совпадает с отчётом по содержимому. "
            "Проверьте комплект."
        )

    problems = _input_problems(state)
    if state.report is None and not state.documents:
        st.info("Добавьте отчёт и первичные документы, чтобы запустить проверку.")
    else:
        for problem in problems:
            st.warning(problem)

    if st.button(
        "Проверить свои файлы",
        key="nitka_upload_run_submit",
        type="primary",
        use_container_width=True,
        disabled=bool(problems),
    ):
        if state.report is not None and state.documents and not problems:
            return {
                "action": "run",
                "report": state.report.file,
                "documents": [document.file for document in state.documents],
            }

    return None


def render_upload(
    report: UploadedFileLike | None = None,
    documents: Sequence[UploadedFileLike] = (),
    *,
    demo_scenarios: Sequence[Any] | None = None,
) -> UploadAction | None:
    """Показывает экран загрузки и возвращает выбранное пользователем действие.

    Параметры ``report`` и ``documents`` позволяют восстановить ранее выбранный
    комплект. Если они не переданы, экран использует только виджеты загрузки.
    """

    if st is None:  # pragma: no cover
        raise RuntimeError(
            "Для экрана загрузки требуется Streamlit. "
            'Установите его командой: pip install "streamlit>=1.57".'
        )

    scenarios = _normalize_scenarios(demo_scenarios)
    state = _get_state(report, documents)

    st.subheader("Новая проверка")
    st.caption(
        "Один отчёт и документы-основания. Система свяжет суммы отчёта "
        "с первичными документами и покажет проверяемую цепочку доказательств."
    )

    _show_notice(state)

    own_column, demo_column = st.columns([1.65, 1.0], gap="large")
    with own_column:
        run_action = _render_own_files(state)
        if run_action is not None:
            return run_action

    with demo_column:
        demo_action = _render_demo(scenarios)
        if demo_action is not None:
            return demo_action

    return None
