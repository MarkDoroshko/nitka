"""Regression coverage for demo navigation, corrections and export revisions."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("streamlit")

from frontend.nitka_ui.actions import (
    Navigate,
    OpenKit,
    ProcessingComplete,
    ResetKit,
    SaveCorrection,
    ViewCheck,
)
from frontend.nitka_ui.app_state import AppSession, _default_state
from frontend.nitka_ui.demo_backend import DemoAdapter
from frontend.nitka_ui.demo_models import ExportError, RunSnapshot


class JsonOnlyDemoAdapter(DemoAdapter):
    def export_pdf(self, snapshot: RunSnapshot) -> bytes:
        raise ExportError("PDF unavailable")


def test_demo_session_tracks_correction_and_export_revision() -> None:
    adapter = JsonOnlyDemoAdapter()
    state = _default_state()
    session = AppSession(adapter, state)

    session.handle(OpenKit("discrepancy"))
    assert state["screen"] == "processing"
    session.handle(ProcessingComplete())
    assert state["screen"] == "results"

    original = state["snapshot"]
    assert isinstance(original, RunSnapshot)
    discrepancy = next(
        view for view in adapter.get_check_views(original) if view.check.state == "FAIL"
    )
    assert discrepancy.reported is not None
    session.handle(ViewCheck(discrepancy.check.check_id))
    assert state["screen"] == "evidence"

    original_json, pdf, error = session.artifacts(original)
    assert json.loads(original_json)["revision"] == 1
    assert pdf is None
    assert error == "PDF unavailable"

    session.handle(
        SaveCorrection(discrepancy.reported.source_id, "120000.00", "Verified")
    )
    corrected = state["snapshot"]
    assert isinstance(corrected, RunSnapshot)
    assert corrected.result.decision == "NO_DISCREPANCIES"
    assert len(corrected.history) == 1

    session.handle(Navigate("export"))
    assert state["screen"] == "export"
    corrected_json, _, _ = session.artifacts(corrected)
    assert json.loads(corrected_json)["revision"] == 2
    assert session.artifacts(original)[0] == original_json

    session.handle(ResetKit())
    reset = state["snapshot"]
    assert isinstance(reset, RunSnapshot)
    assert reset.result.revision == 1
    assert reset.result.decision == "DISCREPANCIES_FOUND"
    assert state["screen"] == "results"
