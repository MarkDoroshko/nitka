from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from nitka.contracts.analysis import Match
from nitka.contracts.extraction import ExtractionResult, FileRole
from nitka.contracts.profile import CaseProfile


@dataclass(frozen=True)
class InputDocument:
    path: Path
    role: FileRole
    name: str | None = None


class Extractor(Protocol):
    def __call__(self, documents: Sequence[InputDocument], /) -> ExtractionResult: ...


class ProfileExtractor(Protocol):
    """An extractor constructed with the single configurable case profile."""

    def __init__(self, profile: CaseProfile) -> None: ...

    def __call__(self, documents: Sequence[InputDocument], /) -> ExtractionResult: ...


class ProfileMatcher(Protocol):
    """A matcher constructed with the profile, never a hidden global."""

    def __init__(self, profile: CaseProfile) -> None: ...

    def __call__(self, extraction: ExtractionResult, /) -> Sequence[Match]: ...
