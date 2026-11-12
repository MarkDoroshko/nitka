from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from nitka.contracts.analysis import Match, Scope
from nitka.contracts.extraction import ExtractionResult
from nitka.contracts.validation import validate_extraction

from .demo_models import AdapterError, DemoKit, DocumentPreview, KitInfo

KIT_ORDER = ("clean", "discrepancy", "incomplete")


class DemoKitRepository:
    """Own the fixture directory and raw cache; load fresh validated models."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or Path(__file__).resolve().parents[1] / "demo_data"
        self._raw_cache: dict[str, dict[str, Any]] = {}
        self._files = {path.stem: path for path in sorted(self.data_dir.glob("*.json"))}

    # -- catalog ---------------------------------------------------------

    def list_kits(self) -> Sequence[KitInfo]:
        order = {kit_id: index for index, kit_id in enumerate(KIT_ORDER)}
        kits = [self._kit_info(kit_id) for kit_id in self._files]
        return tuple(sorted(kits, key=lambda kit: order.get(kit.kit_id, 99)))

    def _kit_info(self, kit_id: str) -> KitInfo:
        raw = self._raw(kit_id)
        extraction = raw["extraction"]
        return KitInfo(
            kit_id=str(raw["kit_id"]),
            title=str(raw["title"]),
            subtitle=str(raw["subtitle"]),
            description=str(raw["description"]),
            expected_label=str(raw["expected_label"]),
            expected_decision=str(raw["expected_decision"]),
            tone=str(raw.get("tone", "neutral")),
            tags=tuple(str(tag) for tag in raw.get("tags", [])),
            report_name=str(raw.get("report_name", "")),
            file_count=len(extraction.get("files", [])),
        )

    def load(self, kit_id: str) -> DemoKit:
        raw = self._raw(kit_id)
        extraction = ExtractionResult.model_validate(raw["extraction"])
        validate_extraction(extraction)
        matches = tuple(Match.model_validate(item) for item in raw["matches"])
        documents = {
            document_id: DocumentPreview(
                title=str(document["title"]),
                kind=str(document["kind"]),
                file_id=str(document["file_id"]),
                pages=tuple(
                    tuple(str(line) for line in page)
                    for page in document.get("pages", [])
                ),
            )
            for document_id, document in raw.get("documents", {}).items()
        }
        return DemoKit(
            info=self._kit_info(kit_id),
            scope=Scope.model_validate(raw["scope"]),
            extraction=extraction,
            matches=matches,
            row_labels={
                str(key): str(value) for key, value in raw.get("row_labels", {}).items()
            },
            documents=documents,
        )

    def _raw(self, kit_id: str) -> dict[str, Any]:
        if kit_id not in self._files:
            raise AdapterError(f"Неизвестный демонстрационный комплект: {kit_id}")
        if kit_id not in self._raw_cache:
            self._raw_cache[kit_id] = json.loads(
                self._files[kit_id].read_text(encoding="utf-8")
            )
        return self._raw_cache[kit_id]
