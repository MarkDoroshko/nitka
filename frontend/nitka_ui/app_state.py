from __future__ import annotations

import streamlit as st

from .actions import (
    Action,
    Navigate,
    OpenKit,
    ProcessingComplete,
    ResetAll,
    ResetKit,
    SaveCorrection,
    ViewCheck,
)
from .demo_models import AdapterError, FrontendAdapter, KitInfo, RunSnapshot
from .display import DECISION_LABELS

APP_STATE_KEY = "nitka_app_state"


def _default_state() -> dict[str, object]:
    return {
        "screen": "catalog",
        "kit_id": None,
        "snapshot": None,
        "selected_check_id": None,
        "flash": None,
        "artifacts": {},
    }


def _state() -> dict[str, object]:
    if APP_STATE_KEY not in st.session_state:
        st.session_state[APP_STATE_KEY] = _default_state()
    state = st.session_state[APP_STATE_KEY]
    for key, value in _default_state().items():
        state.setdefault(key, value)
    return state


def _kit_info(adapter: FrontendAdapter, kit_id: object) -> KitInfo | None:
    return next((kit for kit in adapter.list_kits() if kit.kit_id == kit_id), None)


class AppSession:
    def __init__(self, adapter: FrontendAdapter, state: dict[str, object]) -> None:
        self.adapter = adapter
        self.state = state

    def artifacts(
        self,
        snapshot: RunSnapshot,
    ) -> tuple[bytes, bytes | None, str | None]:
        adapter = self.adapter
        state = self.state
        key = f"{snapshot.result.run_id}:{snapshot.result.revision}"
        cache = state["artifacts"]
        assert isinstance(cache, dict)
        if key not in cache:
            json_bytes = adapter.export_json(snapshot)
            try:
                pdf_bytes: bytes | None = adapter.export_pdf(snapshot)
                pdf_error = None
            except Exception as exc:  # keep JSON available if PDF rendering fails
                pdf_bytes = None
                pdf_error = str(exc)
            cache[key] = (json_bytes, pdf_bytes, pdf_error)
            for stale in list(cache)[:-6]:
                cache.pop(stale, None)
        return cache[key]

    def handle(self, action: Action) -> None:
        adapter = self.adapter
        state = self.state
        if isinstance(action, OpenKit):
            state["kit_id"] = action.kit_id
            state["screen"] = "processing"
            state["snapshot"] = None
            state["selected_check_id"] = None
            state["flash"] = None
            return

        if isinstance(action, ProcessingComplete):
            kit_id = state.get("kit_id")
            try:
                snapshot = adapter.open_kit(str(kit_id))
            except Exception as exc:
                state["screen"] = "catalog"
                state["kit_id"] = None
                state["flash"] = (
                    "error",
                    f"Не удалось открыть комплект: {exc}",
                )
            else:
                state["snapshot"] = snapshot
                state["screen"] = "results"
                state["selected_check_id"] = None
                state["flash"] = (
                    "success",
                    "Комплект обработан. Источники прикреплены к каждой проверке.",
                )
            return

        if isinstance(action, ViewCheck):
            state["selected_check_id"] = action.check_id
            state["screen"] = "evidence"
            return

        if isinstance(action, Navigate):
            target = action.screen
            if target not in {"catalog", "results", "export"}:
                target = "catalog"
            if target in {"results", "export"} and not isinstance(
                state.get("snapshot"), RunSnapshot
            ):
                target = "catalog"
            state["screen"] = target
            return

        if isinstance(action, SaveCorrection):
            snapshot = state.get("snapshot")
            if not isinstance(snapshot, RunSnapshot):
                state["screen"] = "catalog"
                return
            try:
                corrected = adapter.apply_correction(
                    snapshot, action.source_id, action.value, action.comment
                )
            except AdapterError as exc:
                state["flash"] = ("error", str(exc))
            except Exception as exc:
                state["flash"] = ("error", f"Не удалось пересчитать: {exc}")
            else:
                state["snapshot"] = corrected
                state["flash"] = (
                    "success",
                    f"Сохранена редакция {corrected.result.revision}. "
                    f"{DECISION_LABELS.get(corrected.result.decision, '')}.",
                )
            return

        if isinstance(action, ResetKit):
            snapshot = state.get("snapshot")
            if not isinstance(snapshot, RunSnapshot):
                return
            try:
                fresh = adapter.open_kit(snapshot.kit.info.kit_id)
            except Exception as exc:
                state["flash"] = ("error", f"Не удалось сбросить редакции: {exc}")
            else:
                state["snapshot"] = fresh
                state["selected_check_id"] = None
                state["screen"] = "results"
                state["flash"] = (
                    "info",
                    "Демонстрационный комплект открыт заново с редакции 1.",
                )
            return

        if isinstance(action, ResetAll):
            state.clear()
            state.update(_default_state())
