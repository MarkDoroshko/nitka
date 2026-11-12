from __future__ import annotations

import hashlib
import shutil
from collections.abc import Sequence
from pathlib import Path

from nitka.contracts.extraction import ExtractionResult, FileRecord
from nitka.contracts.inputs import InputManifest, ManifestFile
from nitka.extraction.registry import InputRegistry

from ._storage_files import _resolve_relative, _safe_suffix, _validate_component
from ._storage_models import INPUTS_DIR, CorruptRunError, StorageError


def _snapshot_inputs(originals: InputRegistry, temporary: Path) -> InputManifest:
    inputs_dir = temporary / INPUTS_DIR
    inputs_dir.mkdir()
    files: list[ManifestFile] = []
    for entry in originals.inputs:
        _validate_component(entry.file_id, "file_id")
        filename = f"{entry.file_id}{_safe_suffix(entry.path)}"
        destination = inputs_dir / filename
        _copy_verified(entry.path, destination, entry.size_bytes, entry.sha256)
        files.append(
            ManifestFile(
                file_id=entry.file_id,
                name=entry.name,
                role=entry.role,
                size_bytes=entry.size_bytes,
                sha256=entry.sha256,
                relative_path=Path(INPUTS_DIR, filename).as_posix(),
            )
        )
    files.sort(key=lambda file: file.file_id)
    return InputManifest(files=files)


def _bind_file_records(extraction: ExtractionResult, manifest: InputManifest) -> None:
    """Bind extractor file IDs to the authoritative snapshot metadata."""
    expected = {file.file_id: file for file in manifest.files}
    actual = {record.file_id: record for record in extraction.files}
    if len(actual) != len(extraction.files) or set(actual) != set(expected):
        raise StorageError(
            "extractor file IDs do not match the input snapshot manifest"
        )
    for file_id, record in actual.items():
        file = expected[file_id]
        if record.sha256 != file.sha256:
            raise StorageError(
                f"extractor file {file_id!r} hash does not match the snapshot"
            )
        record.name = file.name
        record.role = file.role
        record.size_bytes = file.size_bytes


def _copy_verified(
    source: Path, destination: Path, size_bytes: int, sha256: str
) -> None:
    try:
        shutil.copyfile(source, destination)
        data = destination.read_bytes()
    except OSError as exc:
        raise StorageError(f"cannot snapshot input {source.name!r}: {exc}") from exc
    if len(data) != size_bytes or hashlib.sha256(data).hexdigest() != sha256:
        raise StorageError(
            f"input {source.name!r} changed while it was being snapshotted"
        )


def _verify_inputs(run_dir: Path, manifest: InputManifest) -> dict[str, Path]:
    by_id: dict[str, Path] = {}
    for file in manifest.files:
        if file.file_id in by_id:
            raise CorruptRunError(f"duplicate file_id {file.file_id!r} in manifest")
        if file.relative_path is None:
            raise CorruptRunError(
                f"manifest entry {file.file_id!r} has no relative path"
            )
        path = _resolve_relative(run_dir, file.relative_path)
        if not path.is_file():
            raise CorruptRunError(f"input file {file.relative_path!r} is missing")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise CorruptRunError(
                f"cannot read input file {file.relative_path!r}: {exc}"
            ) from exc
        if (
            len(data) != file.size_bytes
            or hashlib.sha256(data).hexdigest() != file.sha256
        ):
            raise CorruptRunError(
                f"input file {file.relative_path!r} does not match the manifest"
            )
        by_id[file.file_id] = path
    return by_id


def _verify_file_records(
    records: Sequence[FileRecord],
    manifest: InputManifest,
    label: str,
) -> None:
    expected = {file.file_id: file for file in manifest.files}
    actual = {record.file_id: record for record in records}
    if set(expected) != set(actual):
        raise CorruptRunError(f"{label} file IDs do not match input_manifest.json")
    for file_id, record in actual.items():
        file = expected[file_id]
        if (
            record.name != file.name
            or record.role != file.role
            or record.sha256 != file.sha256
            or record.size_bytes != file.size_bytes
        ):
            raise CorruptRunError(
                f"{label} file record {file_id!r} does not match the manifest"
            )
