from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from frontend.nitka_ui.demo_backend import DemoAdapter
from frontend.nitka_ui.real_adapter import RealAdapter
from nitka.application import CaseApplication
from nitka.contracts.analysis import Match, Scope
from nitka.contracts.extraction import (
    CellLocation,
    ExtractedRow,
    ExtractionResult,
    PageLocation,
    Source,
)
from nitka.contracts.profile import CaseProfile
from nitka.extraction.limits import PackageLimits
from nitka.extraction.ports import InputDocument
from nitka.extraction.registry import InputRegistry

SCOPE = Scope(currency="RUB", amount_basis="GROSS", reporting_period="2026-05")
PROFILE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "case_contract.json"


class _Extractor:
    def __init__(self, profile: CaseProfile) -> None:
        self.profile = profile

    def __call__(self, documents: Sequence[InputDocument]) -> ExtractionResult:
        registry = InputRegistry.register(documents)
        files = [entry.to_file_record("COMPLETED") for entry in registry.inputs]
        report = next(entry for entry in registry.inputs if entry.role == "REPORT")
        support = next(entry for entry in registry.inputs if entry.role == "SUPPORT")
        report_source = Source(
            source_id="S-REPORT",
            file_id=report.file_id,
            document_id="D-REPORT",
            location=CellLocation(kind="CELL", sheet="Report", cell="D2"),
            method="TABLE",
            raw_text="100.00",
            value="100.00",
            review_status="UNREVIEWED",
        )
        support_source = Source(
            source_id="S-SUPPORT",
            file_id=support.file_id,
            document_id="D-SUPPORT",
            location=PageLocation(kind="PAGE", page=1),
            method="PDF_TEXT",
            raw_text="100.00",
            value="100.00",
            review_status="UNREVIEWED",
        )
        return ExtractionResult(
            extraction_version="fake-ui-1",
            files=files,
            sources=[report_source, support_source],
            rows=[
                ExtractedRow(
                    row_id="R-1",
                    kind="ITEM",
                    amount_source_id=report_source.source_id,
                ),
                ExtractedRow(
                    row_id="A-1",
                    kind="ITEM",
                    amount_source_id=support_source.source_id,
                ),
            ],
        )


class _Matcher:
    def __init__(self, profile: CaseProfile) -> None:
        self.profile = profile

    def __call__(self, extraction: ExtractionResult) -> list[Match]:
        return [
            Match(
                match_id="M-1",
                report_row_id="R-1",
                support_row_ids=["A-1"],
                state="ACCEPTED",
            )
        ]


def _adapter(tmp_path: Path) -> RealAdapter:
    profile = CaseProfile.model_validate_json(PROFILE_PATH.read_text(encoding="utf-8"))
    app = CaseApplication.from_factories(
        tmp_path / "runs",
        profile,
        SCOPE,
        extractor_factory=_Extractor,
        matcher_factory=_Matcher,
        limits=PackageLimits(max_total_bytes=1024),
    )
    return RealAdapter(app, demo=DemoAdapter())


def test_upload_evidence_correction_and_current_revision_export(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)

    snapshot = adapter.run_upload(
        "report.xlsx",
        b"report",
        [("act.pdf", b"act")],
        run_id="ui-run",
    )

    assert snapshot.result.run_id == "ui-run"
    assert snapshot.result.revision == 1
    assert snapshot.kit.info.kit_id == "ui-run"
    assert snapshot.kit.info.title == "Запуск ui-run"
    views = adapter.get_check_views(snapshot)
    assert views
    view = views[0]
    assert view.reported is not None
    assert view.reported.location_kind == "CELL"
    assert view.supported[0].location_kind == "PAGE"
    assert view.reported.preview_lines
    assert view.supported[0].preview_lines

    corrected = adapter.apply_correction(
        snapshot, "S-REPORT", "120.00", "сверено с актом"
    )
    assert corrected.result.revision == 2
    assert corrected.history[-1].old_value == "100.00"
    assert corrected.history[-1].new_value == "120.00"
    assert adapter.open_kit("ui-run").result.revision == 2

    payload = json.loads(adapter.export_json(corrected))
    assert payload["revision"] == 2
    assert payload["run_id"] == "ui-run"
    pdf = adapter.export_pdf(corrected)
    assert pdf.startswith(b"%PDF")


def test_catalog_keeps_demo_explicitly_marked(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    adapter.run_upload("report.xlsx", b"report", [("act.pdf", b"act")], run_id="ui-run")

    kits = list(adapter.list_kits())
    real = [kit for kit in kits if kit.kit_id == "ui-run"]
    demos = [kit for kit in kits if kit.kit_id.startswith("demo:")]
    assert real and real[0].title == "Запуск ui-run"
    assert demos
    assert all(kit.title.startswith("[ДЕМО]") for kit in demos)
    assert all("демо" in kit.tags for kit in demos)
    demo = adapter.open_kit(demos[0].kit_id)
    assert demo.kit.info.kit_id.startswith("demo:")
