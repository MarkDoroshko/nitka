from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from nitka.contracts.extraction import FileRecord, FileRole, FileStatus
from nitka.contracts.inputs import InputManifest, ManifestFile
from nitka.extraction.ports import InputDocument


class InputReadError(OSError):
    """Raised when an input document cannot be read as bytes."""

    def __init__(self, filename: str, reason: str) -> None:
        super().__init__(f"Cannot read input file {filename!r}: {reason}")
        self.filename = filename

    def __str__(self) -> str:
        return str(self.args[0])


@dataclass(frozen=True)
class RegisteredInput:
    """Accepted file metadata plus the in-memory path the extractor can open."""

    file: ManifestFile
    path: Path

    @property
    def file_id(self) -> str:
        return self.file.file_id

    @property
    def name(self) -> str:
        return self.file.name

    @property
    def role(self) -> FileRole:
        return self.file.role

    @property
    def size_bytes(self) -> int:
        return self.file.size_bytes

    @property
    def sha256(self) -> str:
        return self.file.sha256

    def to_file_record(self, status: FileStatus) -> FileRecord:
        """Build the extraction-facing record once parsing has a result."""
        return FileRecord(
            file_id=self.file.file_id,
            name=self.file.name,
            sha256=self.file.sha256,
            role=self.file.role,
            status=status,
            size_bytes=self.file.size_bytes,
        )

    def snapshot(self, destination: Path) -> RegisteredInput:
        """Make a private working copy and verify the bytes written to it."""
        digest = hashlib.sha256()
        remaining = self.size_bytes
        try:
            with self.path.open("rb") as source, destination.open("xb") as target:
                while chunk := source.read(min(1024 * 1024, remaining + 1)):
                    remaining -= len(chunk)
                    if remaining < 0:
                        break
                    target.write(chunk)
                    digest.update(chunk)
        except OSError as exc:
            raise InputReadError(self.name, exc.strerror or type(exc).__name__) from exc
        if remaining != 0 or digest.hexdigest() != self.sha256:
            raise InputReadError(self.name, "contents changed after registration")
        return RegisteredInput(self.file, destination)


class InputRegistry:
    """Per-run registry of accepted inputs and their portable manifest."""

    def __init__(self, entries: Iterable[RegisteredInput]) -> None:
        self._entries = tuple(entries)
        self._by_id = {entry.file_id: entry for entry in self._entries}
        if len(self._by_id) != len(self._entries):
            raise ValueError("Duplicate file IDs in input registry")
        self.manifest = InputManifest(files=[entry.file for entry in self._entries])

    @classmethod
    def register(cls, documents: Sequence[InputDocument]) -> Self:
        """Read every document, hash its bytes, and assign a per-run file ID."""
        return cls(
            _register(document, f"F-{index}")
            for index, document in enumerate(documents, start=1)
        )

    @classmethod
    def register_package(
        cls, documents: Sequence[InputDocument]
    ) -> tuple[Self, tuple[InputReadError, ...]]:
        """Register readable inputs with content IDs and report unreadable inputs.

        Copies with the same bytes and role share an identity stem. Their set of
        occurrence IDs is stable even when individual copies are renamed.
        """
        groups: dict[str, list[RegisteredInput]] = defaultdict(list)
        errors: list[InputReadError] = []
        for document in documents:
            try:
                entry = _register(document)
            except InputReadError as exc:
                errors.append(exc)
            else:
                groups[entry.file_id].append(entry)

        entries = []
        for stem, group in sorted(groups.items()):
            ordered = sorted(
                group, key=lambda item: (item.path.suffix.lower(), item.name, item.path)
            )
            for occurrence, entry in enumerate(ordered, start=1):
                file_id = stem if occurrence == 1 else f"{stem}-{occurrence}"
                entries.append(
                    RegisteredInput(
                        entry.file.model_copy(update={"file_id": file_id}), entry.path
                    )
                )
        return cls(entries), tuple(sorted(errors, key=str))

    @classmethod
    def restore(cls, manifest: InputManifest, paths: Mapping[str, Path]) -> Self:
        """Re-attach a portable manifest to local paths, verifying bytes."""
        entries = []
        for file in manifest.files:
            path = paths.get(file.file_id)
            if path is None:
                raise ValueError(f"Missing path for input file {file.file_id}")
            resolved, size, digest = _fingerprint(path)
            if size != file.size_bytes or digest != file.sha256:
                raise ValueError(
                    f"Input file {file.name!r} does not match the manifest"
                )
            entries.append(RegisteredInput(file=file, path=resolved))
        return cls(entries)

    @property
    def inputs(self) -> tuple[RegisteredInput, ...]:
        return self._entries

    def path_for(self, file_id: str) -> Path:
        try:
            return self._by_id[file_id].path
        except KeyError:
            raise KeyError(f"Unknown file ID: {file_id}") from None

    def file_records(self, statuses: Mapping[str, FileStatus]) -> list[FileRecord]:
        records = []
        for entry in self._entries:
            status = statuses.get(entry.file_id)
            if status is None:
                raise ValueError(f"Missing extraction status for {entry.file_id}")
            records.append(entry.to_file_record(status))
        return records

    def duplicate_groups(self) -> list[list[str]]:
        """File IDs grouped by identical content; only groups of two or more."""
        by_hash: dict[str, list[str]] = defaultdict(list)
        for entry in self._entries:
            by_hash[entry.sha256].append(entry.file_id)
        return [ids for ids in by_hash.values() if len(ids) > 1]


def _register(document: InputDocument, file_id: str | None = None) -> RegisteredInput:
    path, size, digest = _fingerprint(document.path)
    return RegisteredInput(
        ManifestFile(
            file_id=file_id or f"F-{document.role}-{digest}",
            name=document.name or document.path.name,
            role=document.role,
            size_bytes=size,
            sha256=digest,
        ),
        path,
    )


def _fingerprint(path: Path) -> tuple[Path, int, str]:
    try:
        resolved = path.resolve(strict=True)
        with resolved.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
            size = stream.tell()
    except (OSError, RuntimeError) as exc:
        # RuntimeError covers symlink loops on Python 3.12.
        reason = exc.strerror if isinstance(exc, OSError) else None
        raise InputReadError(path.name, reason or type(exc).__name__) from exc
    return resolved, size, digest
