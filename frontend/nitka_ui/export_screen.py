from __future__ import annotations

import streamlit as st

from .actions import Action, Navigate
from .demo_models import RunSnapshot
from .display import DECISION_LABELS, DECISION_TONES, short_run_id
from .presentation import _esc, _file_name, _money, _page_head


def screen_export(
    snapshot: RunSnapshot,
    json_bytes: bytes,
    pdf_bytes: bytes | None,
    pdf_error: str | None,
) -> Action | None:
    result = snapshot.result
    kit = snapshot.kit.info
    action: Action | None = None
    _page_head(
        f"Экспорт · редакция {result.revision}",
        "Заключение и выгрузка",
        "JSON содержит AnalysisResult текущей редакции. PDF — образец "
        "заключения с расчётами, покрытием и историей изменений.",
    )

    tone = DECISION_TONES.get(result.decision, "neutral")
    st.markdown(
        (
            f'<div class="nk-decision {tone}">'
            '<div class="nk-decision-copy">'
            f'<div class="nk-status-dot {tone}"></div><div>'
            f'<div class="nk-decision-title">'
            f"{_esc(DECISION_LABELS.get(result.decision, result.decision))}</div>"
            f'<div class="nk-decision-sub">'
            f"{_esc(kit.title)} · run_id {_esc(short_run_id(result.run_id))}"
            f" · revision {result.revision}</div>"
            "</div></div>"
            '<div class="nk-decision-meta">'
            f"правила<br/><b>{_esc(result.rules_version)}</b><br/>"
            f"извлечение {_esc(result.extraction_version)}"
            "</div></div>"
        ),
        unsafe_allow_html=True,
    )

    left, right = st.columns(2, gap="large")
    with left:
        with st.container(border=True):
            st.markdown(
                '<div class="nk-export-title">JSON · AnalysisResult</div>',
                unsafe_allow_html=True,
            )
            st.caption(
                "Текущая редакция в контракте Nitka. Суммы остаются строками, "
                "источники и проверки сохраняются."
            )
            st.markdown(
                (
                    '<div class="nk-export-meta">'
                    f"<span>Редакция</span><b>{result.revision}</b>"
                    f"<span>Проверок</span><b>{len(result.checks)}</b>"
                    f"<span>Размер</span><b>{len(json_bytes) / 1024:.1f} КБ</b>"
                    "</div>"
                ),
                unsafe_allow_html=True,
            )
            st.download_button(
                "Скачать JSON текущей редакции",
                data=json_bytes,
                file_name=_file_name(snapshot, "json"),
                mime="application/json",
                use_container_width=True,
            )
    with right:
        with st.container(border=True):
            st.markdown(
                '<div class="nk-export-title">PDF · образец заключения</div>',
                unsafe_allow_html=True,
            )
            st.caption(
                "Печатная версия текущей редакции: статус, покрытие, таблица "
                "проверок, ошибки обработки и история изменений."
            )
            if pdf_bytes is not None:
                st.markdown(
                    (
                        '<div class="nk-export-meta">'
                        f"<span>Редакция</span><b>{result.revision}</b>"
                        f"<span>Страниц</span><b>≈ 1–3</b>"
                        f"<span>Размер</span><b>{len(pdf_bytes) / 1024:.1f} КБ</b>"
                        "</div>"
                    ),
                    unsafe_allow_html=True,
                )
                st.download_button(
                    "Скачать образец PDF",
                    data=pdf_bytes,
                    file_name=_file_name(snapshot, "pdf"),
                    mime="application/pdf",
                    use_container_width=True,
                )
            else:
                st.error(
                    "PDF сейчас недоступен, JSON сохранён и доступен слева. "
                    f"Причина: {pdf_error or 'неизвестная ошибка'}"
                )

    with st.expander("Предпросмотр JSON"):
        text = json_bytes.decode("utf-8", errors="replace")
        st.code(
            text if len(text) <= 12000 else text[:12000] + "\n…",
            language="json",
        )

    if snapshot.history:
        with st.expander(f"История редакций · {len(snapshot.history)}"):
            for item in reversed(snapshot.history):
                st.markdown(
                    (
                        '<div class="nk-history">'
                        f"<b>Редакция {item.revision} · {_esc(item.row_label)}</b><br/>"
                        f"{_esc(_money(item.old_value, result.scope.currency))} → "
                        f"{_esc(_money(item.new_value, result.scope.currency))}"
                        f"<span>{_esc(item.comment or 'Без комментария')}</span>"
                        "</div>"
                    ),
                    unsafe_allow_html=True,
                )

    st.caption(
        "Контрольная пара: UI и PDF используют один run_id + revision. "
        "После нового исправления оба файла будут относиться к новой редакции."
    )
    buttons = st.columns([1.2, 1.2], gap="small")
    with buttons[0]:
        if st.button(
            "← К результату",
            key="export-back",
            use_container_width=True,
        ):
            action = Navigate("results")
    with buttons[1]:
        if st.button(
            "Сменить комплект",
            key="export-catalog",
            use_container_width=True,
        ):
            action = Navigate("catalog")
    return action
