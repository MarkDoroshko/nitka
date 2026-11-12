from __future__ import annotations

import time
from typing import Sequence

import streamlit as st

from .actions import Action, ProcessingComplete
from .demo_models import KitInfo, ProcessingStage
from .presentation import _esc, _page_head


def screen_processing(
    kit: KitInfo,
    stages: Sequence[ProcessingStage],
    delay: float = 0.34,
) -> Action | None:
    _page_head(
        f"Комплект · {kit.title}",
        "Проверяем комплект",
        "Показываем этапы обработки. В демо они выполняются локально, "
        "после подключения адаптера здесь будет фактический прогресс.",
    )
    bar = st.progress(0, text="Подготовка…")
    stage_box = st.empty()
    for index, stage in enumerate(stages):
        progress = (index + 1) / len(stages)
        bar.progress(
            progress,
            text=f"{stage.title} · {int(progress * 100)}%",
        )
        rows = []
        for stage_index, item in enumerate(stages):
            if stage_index < index:
                marker, state_class = "✓", "done"
            elif stage_index == index:
                marker, state_class = "●", "active"
            else:
                marker, state_class = "○", "todo"
            rows.append(
                (
                    f'<div class="nk-stage {state_class}">'
                    f"<span>{marker}</span><div>"
                    f"<b>{_esc(item.title)}</b>"
                    f"<small>{_esc(item.detail)}</small>"
                    "</div></div>"
                )
            )
        stage_box.markdown(
            f'<div class="nk-stage-list">{"".join(rows)}</div>',
            unsafe_allow_html=True,
        )
        time.sleep(delay)
    bar.progress(1.0, text="Готово · результат готов к проверке")
    return ProcessingComplete()
