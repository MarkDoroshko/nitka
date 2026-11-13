from __future__ import annotations

from pathlib import Path

import streamlit as st

from .actions import Action, Navigate
from .app_state import AppSession, _kit_info, _state
from .catalog import screen_catalog
from .demo_backend import DemoAdapter
from .demo_models import RunSnapshot
from .evidence import screen_evidence
from .export_screen import screen_export
from .package_screen import screen_package
from .processing import screen_processing
from .results import screen_results
from .sidebar import _render_flash, _render_sidebar


class AppController:
    def __init__(self, session: AppSession) -> None:
        self.session = session

    def render(self) -> None:
        adapter = self.session.adapter
        state = self.session.state
        sidebar_action = _render_sidebar(state)
        _render_flash(state)

        screen = str(state.get("screen", "catalog"))
        action: Action | None = sidebar_action

        if screen == "catalog":
            screen_action = screen_catalog(adapter.list_kits())
        elif screen == "processing":
            kit_id = state.get("kit_id")
            kit = _kit_info(adapter, kit_id)
            if kit is None:
                screen_action = Navigate("catalog")
            else:
                screen_action = screen_processing(kit, adapter.processing_stages())
        elif screen == "results":
            snapshot = state.get("snapshot")
            if not isinstance(snapshot, RunSnapshot):
                screen_action = Navigate("catalog")
            else:
                screen_action = screen_results(
                    snapshot, adapter.get_check_views(snapshot)
                )
        elif screen == "evidence":
            snapshot = state.get("snapshot")
            check_id = state.get("selected_check_id")
            if not isinstance(snapshot, RunSnapshot) or not check_id:
                screen_action = Navigate("results")
            else:
                view = adapter.get_check_view(snapshot, str(check_id))
                if view is None:
                    screen_action = Navigate("results")
                else:
                    screen_action = screen_evidence(snapshot, view)
        elif screen == "export":
            snapshot = state.get("snapshot")
            if not isinstance(snapshot, RunSnapshot):
                screen_action = Navigate("catalog")
            else:
                json_bytes, pdf_bytes, pdf_error = self.session.artifacts(snapshot)
                screen_action = screen_export(
                    snapshot, json_bytes, pdf_bytes, pdf_error
                )
        else:
            screen_action = Navigate("catalog")

        if action is None:
            action = screen_action

        if action is not None:
            self.session.handle(action)
            st.rerun()


def main() -> None:
    st.set_page_config(
        page_title="Нитка — доказательная сверка",
        page_icon="🧾",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    css = (Path(__file__).resolve().parents[1] / "assets" / "app.css").read_text(
        encoding="utf-8"
    )
    st.markdown(f"<style>\n{css}</style>", unsafe_allow_html=True)
    with st.sidebar:
        mode = st.radio("Режим работы", ("Свои документы", "Демонстрация"), key="nitka_mode")
    if mode == "Свои документы":
        screen_package()
    else:
        AppController(AppSession(DemoAdapter(), _state())).render()
