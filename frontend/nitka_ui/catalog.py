from __future__ import annotations

from typing import Sequence

import streamlit as st

from .actions import Action, OpenKit
from .demo_models import KitInfo
from .presentation import _badge, _esc, _plural


def screen_catalog(kits: Sequence[KitInfo]) -> Action | None:
    if not kits:
        st.error(
            "Демонстрационные комплекты не найдены. Проверьте каталог "
            "frontend/demo_data."
        )
        return None
    st.markdown(
        (
            '<div class="nk-hero">'
            '<div class="nk-kicker">Нитка · доказательная сверка отчётности</div>'
            "<h1>Каждая сумма —<br/><span>с основанием.</span></h1>"
            "<p>Связываем строку отчёта с первичными документами, проверяем "
            "расчёт и показываем путь к доказательству. Модель читает, код "
            "считает, решение остаётся за человеком.</p>"
            '<div class="nk-flow">'
            "<span>отчёт</span><i>→</i><span>первичка</span><i>→</i>"
            "<span>правило</span><i>→</i><span>расчёт</span><i>→</i>"
            "<span>заключение</span>"
            "</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="nk-section-label">Выберите демонстрационный комплект</div>',
        unsafe_allow_html=True,
    )
    action: Action | None = None
    columns = st.columns(len(kits), gap="large")
    for index, (column, kit) in enumerate(zip(columns, kits), start=1):
        tone = kit.tone if kit.tone in {"success", "warning", "danger"} else "neutral"
        with column:
            with st.container(border=True):
                st.markdown(
                    (
                        '<div class="nk-kit-top">'
                        f'<span class="nk-num">{index:02d}</span>'
                        f"{_badge(tone, kit.expected_label)}"
                        "</div>"
                        f'<div class="nk-kit-title">{_esc(kit.title)}</div>'
                        f'<div class="nk-kit-sub">{_esc(kit.subtitle)}</div>'
                        f'<div class="nk-expected">{_esc(kit.expected_label)}</div>'
                        '<div class="nk-chip-row">'
                        + "".join(
                            f'<span class="nk-chip">{_esc(tag)}</span>'
                            for tag in kit.tags
                        )
                        + "</div>"
                    ),
                    unsafe_allow_html=True,
                )
                st.markdown(
                    (
                        '<div class="nk-kit-file">'
                        f"{kit.file_count} "
                        f"{_plural(kit.file_count, 'файл', 'файла', 'файлов')} · "
                        f"{_esc(kit.report_name)}"
                        "</div>"
                    ),
                    unsafe_allow_html=True,
                )
                if st.button(
                    "Открыть комплект",
                    key=f"open-{kit.kit_id}",
                    use_container_width=True,
                    type="primary",
                ):
                    action = OpenKit(kit.kit_id)

    st.markdown("<div style='height: 14px'></div>", unsafe_allow_html=True)
    st.markdown(
        '<div class="nk-section-label">Как читать результат</div>',
        unsafe_allow_html=True,
    )
    method_columns = st.columns(3, gap="large")
    methods = (
        (
            "01",
            "Сначала операция, потом сумма",
            "Сумма не доказывает принадлежность. Строки связываются по "
            "реквизитам, а неоднозначность уходит на ручную проверку.",
        ),
        (
            "02",
            "Каждое число с адресом",
            "Для значения сохраняются файл, страница или ячейка, исходный "
            "текст, способ извлечения и состояние проверки.",
        ),
        (
            "03",
            "Одна редакция для UI и PDF",
            "Любое исправление создаёт новую редакцию и пересчёт. JSON и PDF "
            "всегда относятся к одному run_id + revision.",
        ),
    )
    for column, (number, title, text) in zip(method_columns, methods):
        with column:
            st.markdown(
                (
                    '<div class="nk-method">'
                    f'<div class="nk-method-num">{number}</div>'
                    f"<h4>{_esc(title)}</h4>"
                    f"<p>{_esc(text)}</p>"
                    "</div>"
                ),
                unsafe_allow_html=True,
            )

    st.caption(
        "Демонстрационный контур: данные синтетические, прогресс обработки "
        "имитируется. Реальные парсеры, сверка и сохранение подключаются "
        "через FrontendAdapter в demo_backend.py."
    )
    return action
