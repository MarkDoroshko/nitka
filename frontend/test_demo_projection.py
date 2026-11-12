"""Public adapter behavior across repository and evidence projection boundaries."""

from pathlib import Path

import pytest

from frontend.nitka_ui.demo_backend import DemoAdapter


@pytest.mark.parametrize("kit_id", ["clean", "discrepancy", "incomplete"])
def test_evidence_preserves_cell_and_page_provenance(kit_id: str) -> None:
    adapter = DemoAdapter(Path(__file__).parent / "demo_data")
    snapshot = adapter.open_kit(kit_id)
    views = adapter.get_check_views(snapshot)
    assert len(views) == snapshot.result.coverage.report_rows
    for view in views:
        assert view.reported is not None
        reported = view.reported
        assert reported.sheet == "Report"
        assert reported.cell is not None and reported.cell.startswith("D")
        for source_view in (reported, *view.supported):
            source = next(
                source
                for source in snapshot.extraction.sources
                if source.source_id == source_view.source_id
            )
            assert source_view.raw_text == source.raw_text
            assert source_view.value == source.value
            assert source_view.file_id == source.file_id
            document = snapshot.kit.documents[source.document_id]
            page = source_view.page or 1
            assert source_view.preview_lines == document.pages[page - 1]
        for supported in view.supported:
            assert supported.location_kind == "PAGE"
            assert supported.page is not None
    if kit_id == "incomplete":
        missing = next(
            view for view in views if view.check.reason_code == "MISSING_SUPPORT"
        )
        assert missing.supported == ()
        assert missing.check.calculation is None


def test_corrected_projection_preserves_original_and_reopening_is_fresh() -> None:
    adapter = DemoAdapter()
    original = adapter.open_kit("discrepancy")
    before = next(
        view for view in adapter.get_check_views(original) if view.check.state == "FAIL"
    )
    assert before.reported is not None
    corrected = adapter.apply_correction(
        original, before.reported.source_id, "120000.00"
    )
    after = adapter.get_check_view(corrected, before.check.check_id)
    assert after is not None and after.reported is not None
    assert after.reported.source_id == before.reported.source_id
    assert after.reported.raw_text == before.reported.raw_text
    assert after.reported.cell == before.reported.cell
    assert after.reported.preview_lines == before.reported.preview_lines
    assert after.reported.value == "120000.00"
    assert before.reported.value == "150000.00"
    reopened = adapter.open_kit("discrepancy")
    assert reopened.extraction == original.extraction
    assert reopened.result.revision == 1
    assert reopened.history == ()
