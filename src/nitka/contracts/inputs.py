import json
from collections.abc import Iterable
from hashlib import sha256
from typing import Literal

from pydantic import Field

from nitka.contracts.extraction import (
    Contract,
    FileRecord,
    FileRole,
    Sha256,
)


class ManifestFile(Contract):
    """Portable description of an accepted input file."""

    file_id: str
    name: str
    role: FileRole
    size_bytes: int = Field(ge=0)
    sha256: Sha256
    relative_path: str | None = None


class InputManifest(Contract):
    """Inputs accepted for one run, without machine-specific paths."""

    manifest_version: Literal["0.1.0"] = "0.1.0"
    files: list[ManifestFile]


def input_manifest_hash(files: Iterable[FileRecord | ManifestFile]) -> str:
    """Hash stable input identity independently of order and paths.

    Included: ``file_id``, role, original name, size and SHA-256. Excluded:
    parsing status, relative/absolute paths and manifest ordering.
    """
    entries = sorted(files, key=lambda file: file.file_id)
    payload = [
        {
            "file_id": entry.file_id,
            "name": entry.name,
            "role": entry.role,
            "sha256": entry.sha256,
            "size_bytes": entry.size_bytes,
        }
        for entry in entries
    ]
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
