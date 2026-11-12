"""Adapter from the B04 application/storage to the existing UI screens."""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from nitka.application import CaseApplication
from nitka.contracts.extraction import ExtractionResult
from nitka.extraction.ports import InputDocument
from nitka.storage import StorageError, StoredRun

from .check_views import build_check_views
from .demo_backend import DemoAdapter
from .demo_models import (
    AdapterError,
    CheckView,
    CorrectionError,
    CorrectionRecord,
    DemoKit,
    DocumentPreview,
    KitInfo,
    ProcessingStage,
    RunSnapshot,
)
from .display import DECISION_LABELS, DECISION_TONES
from .pdf_report import _render_pdf


class RealAdapter:
    """Real runs from the application plus explicitly marked demo kits."""

    DEMO_PREFIX = "demo:"

    def __init__(
        self,
        application: CaseApplication,
        *,
        demo: DemoAdapter | None = None,
    ) -> None:
        self.application = application
        self.demo = demo

    # -- catalog ---------------------------------------------------------

    def list_kits(self) -> Sequence[KitInfo]:
        kits: list[KitInfo] = []
        if self.application.root.is_dir():
            for run_dir in sorted(self.application.root.iterdir()):
                if not run_dir.is_dir() or run_dir.name.startswith("."):
                    continue
                try:
                    kits.append(self._kit_info(run_dir.name))
                except Exception:
                    continue
        if self.demo is not None:
            kits.extend(
                replace(
                    kit,
                    kit_id=f"{self.DEMO_PREFIX}{kit.kit_id}",
                    title=f"[ДЕМО] {kit.title}",
                    tags=(*kit.tags, "демо", "синтетика"),
                )
                for kit in self.demo.list_kits()
            )
        return kits

    def processing_stages(self) -> Sequence[ProcessingStage]:
        return (
            ProcessingStage(
                "Снимок входов", "Проверяем роли, лимиты и сохраняем файлы"
            ),
            ProcessingStage("Извлечение", "Читаем отчёт и первичные документы"),
            ProcessingStage("Сопоставление", "Связываем операции по реквизитам"),
            ProcessingStage("Правила", "Считаем суммы и покрытие"),
            ProcessingStage("Публикация", "Сохраняем редакцию и доказательства"),
        )

    def open_kit(self, kit_id: str) -> RunSnapshot:
        if kit_id.startswith(self.DEMO_PREFIX):
            if self.demo is None:
                raise AdapterError("Демонстрационный адаптер не подключён")
            snapshot = self.demo.open_kit(kit_id[len(self.DEMO_PREFIX) :])
            info = replace(
                snapshot.kit.info,
                kit_id=kit_id,
                title=f"[ДЕМО] {snapshot.kit.info.title}",
                tags=(*snapshot.kit.info.tags, "демо", "синтетика"),
            )
            return replace(snapshot, kit=replace(snapshot.kit, info=info))
        return self._snapshot(self.application.load(kit_id, self._latest(kit_id)))

    # -- real upload -----------------------------------------------------

    def run_upload(
        self,
        report_name: str,
        report_bytes: bytes,
        documents: Sequence[tuple[str, bytes]],
        *,
        run_id: str | None = None,
    ) -> RunSnapshot:
        """Persist an uploaded package through the real B04 application."""
        if not report_bytes:
            raise AdapterError("Файл отчёта пуст")
        if not documents:
            raise AdapterError("Добавьте хотя бы один первичный документ")

        base = Path(tempfile.mkdtemp(prefix="nitka-upload-"))
        report = base / _safe_name(report_name)
        report.write_bytes(report_bytes)
        paths = [InputDocument(report, "REPORT")]
        for name, data in documents:
            path = base / _safe_name(name)
            if path == report:
                raise AdapterError("Отчёт и первичный документ не должны совпадать")
            path.write_bytes(data)
            paths.append(InputDocument(path, "SUPPORT"))
        try:
            analysis = self.application.run(paths, run_id=run_id)
        except Exception as exc:
            raise AdapterError(f"Не удалось выполнить запуск: {exc}") from exc
        return self._snapshot(self.application.load(analysis.run_id))

    # -- views / corrections / export -----------------------------------

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

    def apply_correction(
        self, snapshot: RunSnapshot, source_id: str, value: str, comment: str = ""
    ) -> RunSnapshot:
        try:
            stored = self.application.correct(
                snapshot.result.run_id,
                expected_revision=snapshot.result.revision,
                source_id=source_id,
                value=value,
                comment=comment,
            )
        except (ValueError, StorageError) as exc:
            raise CorrectionError(f"Сумма не изменена: {exc}") from exc
        return self._snapshot(stored)

    def export_json(self, snapshot: RunSnapshot) -> bytes:
        return snapshot.result.model_dump_json(indent=2).encode("utf-8")

    def export_pdf(self, snapshot: RunSnapshot) -> bytes:
        return _render_pdf(snapshot, self.get_check_views(snapshot))

    # -- conversion ------------------------------------------------------

    def _latest(self, run_id: str) -> int:
        revisions = self.application.root / run_id / "revisions"
        numbers = [
            int(path.name)
            for path in revisions.iterdir()
            if path.is_dir() and path.name.isdecimal()
        ]
        return max(numbers, default=1)

    def _kit_info(self, run_id: str) -> KitInfo:
        stored = self.application.load(run_id, self._latest(run_id))
        result = stored.analysis
        report = next(
            (file for file in stored.extraction.files if file.role == "REPORT"),
            None,
        )
        return KitInfo(
            kit_id=run_id,
            title=f"Запуск {run_id}",
            subtitle=f"Редакция {result.revision} · {result.scope.reporting_period}",
            description=(
                f"Сохранённый запуск, {len(stored.extraction.files)} файлов. "
                f"Профиль: {stored.profile.profile_id if stored.profile else 'не сохранён'}."
            ),
            expected_label=DECISION_LABELS.get(result.decision, result.decision),
            expected_decision=result.decision,
            tone=DECISION_TONES.get(result.decision, "neutral"),
            tags=(
                f"{result.coverage.checked_rows}/{result.coverage.report_rows} строк",
                f"редакция {result.revision}",
            ),
            report_name=report.name if report else "—",
            file_count=len(stored.extraction.files),
        )

    def _snapshot(self, stored: StoredRun) -> RunSnapshot:
        extraction = stored.extraction
        analysis = stored.analysis
        info = self._kit_info(stored.run_id)
        row_labels = _row_labels(extraction)
        documents = _documents(extraction)
        kit = DemoKit(
            info=info,
            scope=analysis.scope,
            extraction=extraction,
            matches=tuple(analysis.matches),
            row_labels=row_labels,
            documents=documents,
        )
        history = tuple(
            CorrectionRecord(
                revision=item.revision,
                source_id=item.source_id,
                row_label=row_labels.get(
                    _row_for_source(extraction, item.source_id), item.source_id
                ),
                old_value=item.old_value,
                new_value=item.new_value,
                comment=item.comment,
            )
            for item in stored.history
        )
        return RunSnapshot(
            kit=kit,
            extraction=extraction,
            matches=tuple(analysis.matches),
            result=analysis,
            history=history,
        )


def _safe_name(name: str) -> str:
    clean = Path(name).name.strip().replace("\x00", "")
    return clean or "document"


def _row_labels(extraction: ExtractionResult) -> dict[str, str]:
    sources = {source.source_id: source for source in extraction.sources}
    labels: dict[str, str] = {}
    for row in extraction.rows:
        for key in ("description", "name", "article_code"):
            source = sources.get(row.fields.get(key, ""))
            if source and (source.value or source.raw_text):
                labels[row.row_id] = str(source.value or source.raw_text)
                break
        labels.setdefault(row.row_id, row.row_id)
    return labels


def _documents(extraction: ExtractionResult) -> dict[str, DocumentPreview]:
    files = {file.file_id: file for file in extraction.files}
    pages: dict[str, list[list[str]]] = {}
    for source in extraction.sources:
        file = files.get(source.file_id)
        if file is None:
            continue
        page = getattr(source.location, "page", None)
        index = max(0, (page or 1) - 1)
        while len(pages.setdefault(file.file_id, [])) <= index:
            pages[file.file_id].append([])
        if source.raw_text:
            pages[file.file_id][index].append(source.raw_text)
    return {
        file_id: DocumentPreview(
            title=files[file_id].name,
            kind=Path(files[file_id].name).suffix.lstrip(".").upper() or "FILE",
            file_id=file_id,
            pages=tuple(tuple(page) for page in file_pages),
        )
        for file_id, file_pages in pages.items()
    }


def _row_for_source(extraction: ExtractionResult, source_id: str) -> str:
    for row in extraction.rows:
        if row.amount_source_id == source_id:
            return row.row_id
    return source_id
