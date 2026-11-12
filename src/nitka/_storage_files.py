from __future__ import annotations

import re
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from nitka.contracts.inputs import ManifestFile

from ._storage_models import CorruptRunError, StorageError

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_SUFFIX_RE = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


def _read_model(path: Path, model: type[_ModelT]) -> _ModelT:
    try:
        return model.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise CorruptRunError(f"cannot read {path.name}: {exc}") from exc


def _write_json(path: Path, text: str) -> None:
    try:
        path.write_text(f"{text}\n", encoding="utf-8")
    except OSError as exc:
        raise StorageError(f"cannot write {path.name}: {exc}") from exc


def _run_dir(root: Path | str, run_id: str) -> Path:
    _validate_run_id(run_id)
    return Path(root) / run_id


def _validate_run_id(run_id: str) -> None:
    if (
        not run_id
        or run_id in {".", ".."}
        or any(character in run_id for character in ("/", "\\", "\x00"))
    ):
        raise StorageError(f"invalid run_id: {run_id!r}")


def _validate_component(value: str, label: str) -> None:
    if (
        not value
        or value in {".", ".."}
        or any(character in value for character in ("/", "\\", "\x00"))
    ):
        raise StorageError(f"invalid {label}: {value!r}")


def _safe_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    return suffix if _SUFFIX_RE.fullmatch(suffix) else ""


def _input_path(run_dir: Path, file: ManifestFile) -> Path:
    if file.relative_path is None:
        raise StorageError(f"input {file.file_id!r} has no relative path")
    return _resolve_relative(run_dir, file.relative_path)


def _resolve_relative(run_dir: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute():
        raise CorruptRunError(f"absolute input path: {relative_path!r}")
    root = run_dir.resolve()
    try:
        resolved = (run_dir / relative).resolve()
    except (OSError, ValueError) as exc:
        raise CorruptRunError(f"invalid input path {relative_path!r}: {exc}") from exc
    if not resolved.is_relative_to(root):
        raise CorruptRunError(
            f"input path escapes the run directory: {relative_path!r}"
        )
    return resolved
