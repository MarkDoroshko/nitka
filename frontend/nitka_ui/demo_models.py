from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from nitka.contracts.analysis import AnalysisResult, Check, Match, Scope
from nitka.contracts.extraction import ExtractionResult


class AdapterError(Exception):
    """Base error for operations exposed to the UI."""


class CorrectionError(AdapterError):
    """The requested manual correction cannot be applied."""


class ExportError(AdapterError):
    """An export could not be rendered."""


@dataclass(frozen=True)
class KitInfo:
    kit_id: str
    title: str
    subtitle: str
    description: str
    expected_label: str
    expected_decision: str
    tone: str
    tags: tuple[str, ...]
    report_name: str
    file_count: int


@dataclass(frozen=True)
class ProcessingStage:
    title: str
    detail: str


@dataclass(frozen=True)
class DocumentPreview:
    title: str
    kind: str
    file_id: str
    pages: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class DemoKit:
    info: KitInfo
    scope: Scope
    extraction: ExtractionResult
    matches: tuple[Match, ...]
    row_labels: dict[str, str]
    documents: dict[str, DocumentPreview]


@dataclass(frozen=True)
class CorrectionRecord:
    revision: int
    source_id: str
    row_label: str
    old_value: str | None
    new_value: str
    comment: str


@dataclass(frozen=True)
class RunSnapshot:
    kit: DemoKit
    extraction: ExtractionResult
    matches: tuple[Match, ...]
    result: AnalysisResult
    history: tuple[CorrectionRecord, ...] = ()


@dataclass(frozen=True)
class SourceView:
    source_id: str
    role: str
    row_id: str
    row_label: str
    file_id: str
    file_name: str
    document_id: str
    document_title: str
    document_kind: str
    location_kind: str
    page: int | None
    sheet: str | None
    cell: str | None
    method: str
    raw_text: str | None
    value: str | None
    review_status: str
    preview_lines: tuple[str, ...]


@dataclass(frozen=True)
class CheckView:
    check: Check
    report_row_id: str
    row_label: str
    reported: SourceView | None
    supported: tuple[SourceView, ...]
    match_state: str | None


class FrontendAdapter(Protocol):
    """Operations the UI needs from a demo or real backend."""

    def list_kits(self) -> Sequence[KitInfo]: ...

    def processing_stages(self) -> Sequence[ProcessingStage]: ...

    def open_kit(self, kit_id: str) -> RunSnapshot: ...

    def get_check_views(self, snapshot: RunSnapshot) -> Sequence[CheckView]: ...

    def get_check_view(
        self, snapshot: RunSnapshot, check_id: str
    ) -> CheckView | None: ...

    def apply_correction(
        self, snapshot: RunSnapshot, source_id: str, value: str, comment: str = ""
    ) -> RunSnapshot: ...

    def export_json(self, snapshot: RunSnapshot) -> bytes: ...

    def export_pdf(self, snapshot: RunSnapshot) -> bytes: ...
