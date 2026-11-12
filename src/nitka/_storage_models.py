from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from nitka.contracts.analysis import AnalysisResult
from nitka.contracts.extraction import ExtractionResult
from nitka.contracts.inputs import InputManifest
from nitka.contracts.revisions import AmountCorrection

MANIFEST_NAME: Final[str] = "input_manifest.json"
EXTRACTION_NAME: Final[str] = "extraction.json"
ANALYSIS_NAME: Final[str] = "analysis.json"
CORRECTION_NAME: Final[str] = "correction.json"
INPUTS_DIR: Final[str] = "inputs"
REVISIONS_DIR: Final[str] = "revisions"


class StorageError(RuntimeError):
    """Base error for local run storage."""


class RunExistsError(StorageError):
    """The requested run_id is already present."""


class RunNotFoundError(StorageError):
    """The requested run or revision is absent."""


class RevisionConflictError(StorageError):
    """A correction was based on a revision that is no longer current."""


class CorruptRunError(StorageError):
    """A stored run is incomplete or internally inconsistent."""


class IncompatibleRulesError(StorageError):
    """The stored rules version cannot be reproduced locally."""


class ReplayMismatchError(CorruptRunError):
    """Recomputing a stored run did not reproduce the stored analysis."""


@dataclass(frozen=True)
class StoredRun:
    root: Path
    run_id: str
    revision: int
    manifest: InputManifest
    manifest_hash: str
    extraction: ExtractionResult
    analysis: AnalysisResult
    inputs: Mapping[str, Path]
    history: tuple[AmountCorrection, ...] = ()

    @property
    def run_dir(self) -> Path:
        return self.root / self.run_id

    @property
    def revision_dir(self) -> Path:
        return self.run_dir / REVISIONS_DIR / str(self.revision)
