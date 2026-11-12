from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OpenKit:
    kit_id: str


@dataclass(frozen=True)
class ProcessingComplete:
    pass


@dataclass(frozen=True)
class ViewCheck:
    check_id: str


@dataclass(frozen=True)
class Navigate:
    screen: str


@dataclass(frozen=True)
class SaveCorrection:
    source_id: str
    value: str
    comment: str


@dataclass(frozen=True)
class ResetKit:
    pass


@dataclass(frozen=True)
class ResetAll:
    pass


Action = (
    OpenKit
    | ProcessingComplete
    | ViewCheck
    | Navigate
    | SaveCorrection
    | ResetKit
    | ResetAll
)
