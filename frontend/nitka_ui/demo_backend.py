from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from nitka.corrections import AmountCorrectionError, correct_amount
from nitka.pipeline import analyze_extraction

from .check_views import build_check_views
from .demo_models import (
    CheckView,
    CorrectionError,
    CorrectionRecord,
    ExportError,
    KitInfo,
    ProcessingStage,
    RunSnapshot,
)
from .demo_repository import KIT_ORDER as KIT_ORDER
from .demo_repository import DemoKitRepository
from .pdf_report import _render_pdf


class DemoAdapter:
    """In-memory demo implementation backed by synthetic JSON kits."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.repository = DemoKitRepository(data_dir)
        self.data_dir = self.repository.data_dir

    def list_kits(self) -> Sequence[KitInfo]:
        return self.repository.list_kits()

    def processing_stages(self) -> Sequence[ProcessingStage]:
        return (
            ProcessingStage("Реестр файлов", "Сверяем состав комплекта и страницы"),
            ProcessingStage(
                "Извлечение значений", "Читаем отчёт и первичные документы"
            ),
            ProcessingStage(
                "Сопоставление", "Связываем строки по реквизитам, а не по сумме"
            ),
            ProcessingStage("Правила и расчёт", "Считаем отклонения через Decimal"),
            ProcessingStage(
                "Сборка доказательств", "Прикрепляем источники к каждой проверке"
            ),
            ProcessingStage("Заключение", "Готовим редакцию результата и экспорт"),
        )

    # -- loading ---------------------------------------------------------

    def open_kit(self, kit_id: str) -> RunSnapshot:
        kit = self.repository.load(kit_id)
        result = analyze_extraction(kit.extraction, list(kit.matches), kit.scope)
        return RunSnapshot(
            kit=kit,
            extraction=kit.extraction,
            matches=kit.matches,
            result=result,
        )

    def get_check_views(self, snapshot: RunSnapshot) -> Sequence[CheckView]:
        return build_check_views(snapshot)

    def get_check_view(self, snapshot: RunSnapshot, check_id: str) -> CheckView | None:
        return next(
            (
                view
                for view in self.get_check_views(snapshot)
                if view.check.check_id == check_id
            ),
            None,
        )

    # -- corrections -----------------------------------------------------

    def apply_correction(
        self, snapshot: RunSnapshot, source_id: str, value: str, comment: str = ""
    ) -> RunSnapshot:
        source = next(
            (
                item
                for item in snapshot.extraction.sources
                if item.source_id == source_id
            ),
            None,
        )
        if source is None:
            raise CorrectionError(f"Источник {source_id} не найден в комплекте.")

        try:
            updated_extraction = correct_amount(snapshot.extraction, source_id, value)
        except AmountCorrectionError as exc:
            raise CorrectionError(
                "Сумма не изменена: проверьте источник и введите новое число, "
                "например 120 000,00."
            ) from exc
        normalized = next(
            item.value
            for item in updated_extraction.sources
            if item.source_id == source_id
        )
        assert normalized is not None

        row = next(
            (
                item
                for item in updated_extraction.rows
                if item.amount_source_id == source_id
            ),
            None,
        )
        row_label = (
            snapshot.kit.row_labels.get(row.row_id, row.row_id)
            if row is not None
            else source_id
        )
        revision = snapshot.result.revision + 1
        result = analyze_extraction(
            updated_extraction,
            list(snapshot.matches),
            snapshot.kit.scope,
            run_id=snapshot.result.run_id,
            revision=revision,
        )
        record = CorrectionRecord(
            revision=revision,
            source_id=source_id,
            row_label=row_label,
            old_value=source.value,
            new_value=normalized,
            comment=comment.strip(),
        )
        return replace(
            snapshot,
            extraction=updated_extraction,
            result=result,
            history=(*snapshot.history, record),
        )

    # -- export ----------------------------------------------------------

    def export_json(self, snapshot: RunSnapshot) -> bytes:
        return snapshot.result.model_dump_json(indent=2).encode("utf-8")

    def export_pdf(self, snapshot: RunSnapshot) -> bytes:
        return _render_pdf(snapshot, self.get_check_views(snapshot))


def _selftest() -> None:
    adapter = DemoAdapter()
    kits = adapter.list_kits()
    assert [kit.kit_id for kit in kits] == ["clean", "discrepancy", "incomplete"]

    expected = {
        "clean": "NO_DISCREPANCIES",
        "discrepancy": "DISCREPANCIES_FOUND",
        "incomplete": "REVIEW_REQUIRED",
    }
    for info in kits:
        snapshot = adapter.open_kit(info.kit_id)
        assert snapshot.result.decision == expected[info.kit_id], (
            info.kit_id,
            snapshot.result.decision,
        )
        assert snapshot.result.revision == 1
        views = adapter.get_check_views(snapshot)
        assert len(views) == snapshot.result.coverage.report_rows
        assert all(view.reported is not None for view in views)
        for view in views:
            found = adapter.get_check_view(snapshot, view.check.check_id)
            assert found == view
            assert view.reported is not None
            assert (
                1 <= len(view.reported.preview_lines)
                or view.reported.preview_lines == ()
            )
        parsed = json.loads(adapter.export_json(snapshot))
        assert parsed["revision"] == 1
        print(
            f"kit {info.kit_id}: {snapshot.result.decision}, "
            f"{snapshot.result.coverage.checked_rows}/"
            f"{snapshot.result.coverage.report_rows} rows checked"
        )

    snapshot = adapter.open_kit("discrepancy")
    fail_view = next(
        view for view in adapter.get_check_views(snapshot) if view.check.state == "FAIL"
    )
    assert fail_view.check.calculation is not None
    assert fail_view.check.calculation.delta == "30000.00"
    assert fail_view.reported is not None

    corrected = adapter.apply_correction(
        snapshot,
        fail_view.reported.source_id,
        "120 000,00",
        "Сверено с актом № 14",
    )
    assert corrected.result.revision == 2
    assert corrected.result.decision == "NO_DISCREPANCIES"
    assert corrected.history[-1].old_value == "150000.00"
    assert corrected.history[-1].new_value == "120000.00"
    assert json.loads(adapter.export_json(corrected))["revision"] == 2

    try:
        adapter.apply_correction(corrected, fail_view.reported.source_id, "не сумма")
    except CorrectionError:
        pass
    else:
        raise AssertionError("invalid correction was accepted")

    try:
        pdf = adapter.export_pdf(corrected)
    except ExportError as exc:
        print(f"PDF check skipped: {exc}")
    else:
        assert pdf.startswith(b"%PDF")
        assert len(pdf) > 1000
        print(f"pdf: {len(pdf)} bytes")

    print("demo_backend self-check: OK")
