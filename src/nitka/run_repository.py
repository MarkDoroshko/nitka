"""Local, portable storage for a run and its evidence.

Layout::

    <root>/<run_id>/
        input_manifest.json
        inputs/<file_id>.<suffix>
        revisions/<revision>/extraction.json
        revisions/<revision>/analysis.json

A run is assembled in a temporary directory next to the target and renamed only
after every file has been written. The manifest hash covers file identity
(``file_id``, role, original name, size, SHA-256) and deliberately excludes
parsing status, paths and manifest ordering.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from nitka.contracts.analysis import AnalysisResult
from nitka.contracts.extraction import ExtractionResult
from nitka.contracts.inputs import InputManifest, input_manifest_hash
from nitka.contracts.profile import CaseProfile
from nitka.contracts.revisions import AmountCorrection
from nitka.corrections import correct_amount
from nitka.extraction.limits import (
    DEFAULT_PACKAGE_LIMITS,
    PackageLimitError,
    PackageLimits,
)
from nitka.extraction.ports import InputDocument
from nitka.extraction.registry import InputRegistry

from ._storage_files import (
    _input_path,
    _read_model,
    _run_dir,
    _validate_run_id,
    _write_json,
)
from ._storage_inputs import (
    _snapshot_inputs,
    _verify_file_records,
    _verify_inputs,
)
from ._storage_models import (
    ANALYSIS_NAME,
    CORRECTION_NAME,
    EXTRACTION_NAME,
    MANIFEST_NAME,
    PROFILE_NAME,
    REVISIONS_DIR,
    CorruptRunError,
    RevisionConflictError,
    RunExistsError,
    RunNotFoundError,
    StorageError,
    StoredRun,
)


@dataclass(frozen=True)
class StagedRun:
    """Input snapshots available only inside a repository staging context."""

    path: Path
    manifest: InputManifest
    documents: tuple[InputDocument, ...]
    profile: CaseProfile | None


class LocalRunRepository:
    """Own local snapshots, integrity checks and atomic publication."""

    def __init__(
        self,
        root: Path | str,
        *,
        write_json: Callable[[Path, str], None] = _write_json,
        verify_inputs: Callable[
            [Path, InputManifest], dict[str, Path]
        ] = _verify_inputs,
    ) -> None:
        self.root = Path(root)
        self.write_json = write_json
        self.verify_inputs = verify_inputs

    @contextmanager
    def stage_run(
        self,
        run_id: str,
        documents: Sequence[InputDocument],
        revision: int,
        *,
        profile: CaseProfile | None = None,
        limits: PackageLimits = DEFAULT_PACKAGE_LIMITS,
    ) -> Iterator[StagedRun]:
        _validate_run_id(run_id)
        if revision < 1:
            raise StorageError("revision must be >= 1")
        self._check_package_size(documents, limits)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(f"cannot create runs root {self.root}: {exc}") from exc
        if (self.root / run_id).exists():
            raise RunExistsError(f"run {run_id!r} already exists")
        try:
            temporary = Path(tempfile.mkdtemp(prefix=f".{run_id}-", dir=self.root))
        except OSError as exc:
            raise StorageError(f"cannot create temporary run directory: {exc}") from exc
        try:
            originals = InputRegistry.register(documents)
            manifest = _snapshot_inputs(originals, temporary)
            copies = {
                file.file_id: _input_path(temporary, file)
                for file in manifest.files
                if file.relative_path is not None
            }
            registry = InputRegistry.restore(manifest, copies)
            yield StagedRun(
                temporary,
                manifest,
                tuple(
                    InputDocument(path=entry.path, role=entry.role, name=entry.name)
                    for entry in registry.inputs
                ),
                profile,
            )
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"run {run_id!r} failed: {exc}") from exc
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def publish_run(
        self,
        staged: StagedRun,
        extraction: ExtractionResult,
        analysis: AnalysisResult,
    ) -> None:
        run_id = analysis.run_id
        target = _run_dir(self.root, run_id)
        revision_dir = staged.path / REVISIONS_DIR / str(analysis.revision)
        revision_dir.mkdir(parents=True)
        self.write_json(
            staged.path / MANIFEST_NAME, staged.manifest.model_dump_json(indent=2)
        )
        if staged.profile is not None:
            self.write_json(
                staged.path / PROFILE_NAME,
                staged.profile.model_dump_json(indent=2),
            )
        self.write_json(
            revision_dir / EXTRACTION_NAME, extraction.model_dump_json(indent=2)
        )
        self.write_json(
            revision_dir / ANALYSIS_NAME, analysis.model_dump_json(indent=2)
        )
        if target.exists():
            raise RunExistsError(f"run {run_id!r} already exists")
        try:
            staged.path.rename(target)
        except OSError as exc:
            raise StorageError(f"cannot publish run {run_id!r}: {exc}") from exc

    def _load_run_snapshot(
        self,
        run_id: str,
        revision: int,
        *,
        verify_inputs: bool = True,
    ) -> StoredRun:
        """Validate one revision; ancestors share the already verified inputs."""
        if revision < 1:
            raise RunNotFoundError("revision must be >= 1")
        run_dir = _run_dir(self.root, run_id)
        if not run_dir.is_dir():
            raise RunNotFoundError(f"run {run_id!r} not found")
        revision_dir = run_dir / REVISIONS_DIR / str(revision)
        if not revision_dir.is_dir():
            raise RunNotFoundError(f"revision {revision} of run {run_id!r} not found")

        manifest = _read_model(run_dir / MANIFEST_NAME, InputManifest)
        extraction = _read_model(revision_dir / EXTRACTION_NAME, ExtractionResult)
        analysis = _read_model(revision_dir / ANALYSIS_NAME, AnalysisResult)
        profile_path = run_dir / PROFILE_NAME
        profile = (
            _read_model(profile_path, CaseProfile) if profile_path.is_file() else None
        )

        manifest_hash = input_manifest_hash(manifest.files)
        if analysis.input_manifest_hash != manifest_hash:
            raise CorruptRunError(
                "analysis.input_manifest_hash does not match input_manifest.json"
            )
        if analysis.run_id != run_id:
            raise CorruptRunError(
                f"analysis.run_id {analysis.run_id!r} does not match {run_id!r}"
            )
        if analysis.revision != revision:
            raise CorruptRunError(
                f"analysis.revision {analysis.revision} does not match {revision}"
            )
        if analysis.extraction_version != extraction.extraction_version:
            raise CorruptRunError(
                "analysis.extraction_version does not match extraction.json"
            )
        if analysis.files != extraction.files:
            raise CorruptRunError("analysis.files does not match extraction.json")
        if analysis.sources != extraction.sources:
            raise CorruptRunError("analysis.sources does not match extraction.json")
        if analysis.issues[: len(extraction.issues)] != extraction.issues:
            raise CorruptRunError(
                "analysis.issues does not preserve extraction.json issues"
            )

        inputs = self.verify_inputs(run_dir, manifest) if verify_inputs else {}
        _verify_file_records(extraction.files, manifest, "extraction")
        _verify_file_records(analysis.files, manifest, "analysis")

        return StoredRun(
            root=self.root,
            run_id=run_id,
            revision=revision,
            manifest=manifest,
            manifest_hash=manifest_hash,
            extraction=extraction,
            analysis=analysis,
            inputs=inputs,
            profile=profile,
        )

    def load(self, run_id: str, revision: int = 1) -> StoredRun:
        """Load a revision and its validated audit chain, hashing shared inputs once."""
        stored = self._load_run_snapshot(run_id, revision)
        cursor = stored
        history: list[AmountCorrection] = []
        revision_numbers = {
            int(path.name)
            for path in (stored.run_dir / REVISIONS_DIR).iterdir()
            if path.is_dir() and path.name.isdecimal()
        }
        while True:
            correction_path = cursor.revision_dir / CORRECTION_NAME
            if not correction_path.exists():
                # Legacy runs have one initial revision, which may be greater than 1.
                # A descendant cannot silently become a legacy base after audit loss.
                if any(0 < number < cursor.revision for number in revision_numbers):
                    raise CorruptRunError("derived revision is missing correction.json")
                break
            correction = _read_model(correction_path, AmountCorrection)
            if (
                correction.revision != cursor.revision
                or correction.parent_revision != cursor.revision - 1
            ):
                raise CorruptRunError("correction revision identities do not match")
            try:
                parent = self._load_run_snapshot(
                    run_id, correction.parent_revision, verify_inputs=False
                )
            except RunNotFoundError as exc:
                raise CorruptRunError("correction parent revision is missing") from exc
            try:
                corrected = correct_amount(
                    parent.extraction, correction.source_id, correction.new_value
                )
            except ValueError as exc:
                raise CorruptRunError(f"invalid correction: {exc}") from exc
            old_source = next(
                source
                for source in parent.extraction.sources
                if source.source_id == correction.source_id
            )
            if (
                old_source.value != correction.old_value
                or corrected != cursor.extraction
                or cursor.analysis.matches != parent.analysis.matches
                or cursor.analysis.scope != parent.analysis.scope
                or cursor.analysis.rules_version != parent.analysis.rules_version
            ):
                raise CorruptRunError(
                    "correction does not match its parent or extraction"
                )
            history.append(correction)
            cursor = parent
        return replace(stored, history=tuple(reversed(history)))

    @staticmethod
    def _check_package_size(
        documents: Sequence[InputDocument], limits: PackageLimits
    ) -> int:
        total = 0
        for document in documents:
            try:
                total += document.path.stat().st_size
            except OSError:
                continue
        if total > limits.max_total_bytes:
            raise PackageLimitError(
                f"package size {total} bytes exceeds "
                f"{limits.max_total_bytes} bytes"
            )
        return total

    def require_current_revision(self, stored: StoredRun) -> None:
        current = max(
            int(path.name)
            for path in (stored.run_dir / REVISIONS_DIR).iterdir()
            if path.is_dir() and path.name.isdecimal()
        )
        if current != stored.revision:
            raise RevisionConflictError(
                f"expected revision {stored.revision}, current {current}"
            )

    def publish_correction(
        self,
        stored: StoredRun,
        extraction: ExtractionResult,
        analysis: AnalysisResult,
        correction: AmountCorrection,
    ) -> StoredRun:
        revision = analysis.revision
        run_id = stored.run_id
        revisions_dir = stored.run_dir / REVISIONS_DIR
        target = revisions_dir / str(revision)
        temporary: Path | None = None
        try:
            temporary = Path(
                tempfile.mkdtemp(prefix=f".{revision}-", dir=revisions_dir)
            )
            for name, model in (
                (EXTRACTION_NAME, extraction),
                (ANALYSIS_NAME, analysis),
                (CORRECTION_NAME, correction),
            ):
                self.write_json(temporary / name, model.model_dump_json(indent=2))
            if target.exists():
                raise RevisionConflictError(f"revision {revision} already exists")
            try:
                # Published revision directories are nonempty; rename cannot replace
                # one, including when another writer wins after the existence check.
                temporary.rename(target)
            except OSError as exc:
                if target.exists():
                    raise RevisionConflictError(
                        f"revision {revision} already exists"
                    ) from exc
                raise
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError(f"cannot publish correction: {exc}") from exc
        finally:
            if temporary is not None and temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)
        return StoredRun(
            root=stored.root,
            run_id=run_id,
            revision=revision,
            manifest=stored.manifest,
            manifest_hash=stored.manifest_hash,
            extraction=extraction,
            analysis=analysis,
            inputs=stored.inputs,
            history=(*stored.history, correction),
            profile=stored.profile,
        )
