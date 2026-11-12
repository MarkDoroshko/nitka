"""Configurable case profile shared by extraction, matching and rules.

The profile is input, not a source of truth for document text. In particular,
case metadata identifies the target row/period but never replaces a source's
``raw_text``.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Final, Literal

from pydantic import Field, model_validator

from nitka.contracts.extraction import Contract, FileRole

CaseField = Literal[
    "contract",
    "service_period",
    "document_date",
    "article_code",
    "operation_id",
    "classification_basis",
    "fragment",
]

WORKING_ROLES: Final[list[FileRole]] = ["REPORT", "SUPPORT"]
TECHNICAL_ROLES: Final[list[FileRole]] = ["TECHNICAL", "ORACLE"]

_KFZ_ROW = re.compile(r"([0-9]+)\s*этап[\s,]+([0-9]{4})(?:\s*г\.?)?\s*:?")


def kfz_row_identity(text: str) -> tuple[int, int] | None:
    """Parse a complete stage/year label from a numbered KFZ table."""
    match = _KFZ_ROW.fullmatch(text.strip().casefold())
    if match is None:
        return None
    try:
        return int(match[1]), int(match[2])
    except ValueError:
        return None


def normalize_contract(value: str | None) -> str:
    """Preserve contract punctuation and suffixes when comparing identifiers."""
    return re.sub(r"\s+", "", value or "").casefold()

CASE_FIELD_NAMES: Final[tuple[CaseField, ...]] = (
    "contract",
    "service_period",
    "document_date",
    "article_code",
    "operation_id",
    "classification_basis",
    "fragment",
)


class ServicePeriod(Contract):
    """Inclusive service period; a document date is a separate field."""

    start: date
    end: date
    months: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _valid_period(self) -> ServicePeriod:
        if self.start > self.end:
            raise ValueError("service period start must not be after end")
        if any(not month.strip() for month in self.months):
            raise ValueError("service period months must not be blank")
        return self

    def includes(self, value: date) -> bool:
        return self.start <= value <= self.end


class ProfileConflict(Contract):
    """An observed value that differs from the configured interpretation."""

    field: str = Field(min_length=1)
    raw_value: str
    interpreted_value: str | None = None
    source: str = Field(min_length=1)
    detail: str = ""


class CaseProfile(Contract):
    """Explicit user/organizer profile for one case.

    ``raw_header_text`` and ``conflicts`` preserve observations exactly. The
    profile can select "3 этап 2026" and January–May 2026, but consumers must
    keep the original source fragments and expose a mismatch instead of
    rewriting them.
    """

    profile_id: str = Field(min_length=1)
    case_number: int = Field(ge=1)
    observed_case_numbers: dict[str, int] = Field(default_factory=dict)
    contract: str = Field(min_length=1)
    service_period: ServicePeriod
    target_stage: int = Field(ge=1)
    target_year: int = Field(ge=2000)
    target_row_label: str = Field(min_length=1)
    document_date: date | None = None
    article_codes: list[str] = Field(min_length=1)
    operation_id_rule: str = Field(min_length=1)
    classification_basis: str = Field(min_length=1)
    fragment: str = Field(min_length=1)
    required_fields: list[CaseField] = Field(
        default_factory=lambda: list(CASE_FIELD_NAMES)
    )
    working_roles: list[FileRole] = Field(default_factory=lambda: list(WORKING_ROLES))
    technical_roles: list[FileRole] = Field(
        default_factory=lambda: list(TECHNICAL_ROLES)
    )
    profile_source: str = Field(default="docs/contracts/case-profile.md")
    raw_header_text: str | None = None
    conflicts: list[ProfileConflict] = Field(default_factory=list)

    @model_validator(mode="after")
    def _coherent(self) -> CaseProfile:
        if set(self.working_roles) & set(self.technical_roles):
            raise ValueError("working and technical roles must not overlap")
        if len(set(self.required_fields)) != len(self.required_fields):
            raise ValueError("required_fields must not contain duplicates")
        if len({code.strip() for code in self.article_codes}) != len(
            self.article_codes
        ):
            raise ValueError("article_codes must be unique")
        return self

    def selects_target_row(self, raw_text: str) -> bool:
        """Return whether literal text contains the target, not its total row.

        A label such as ``3 этап 2026`` must not be selected when it is the tail
        of ``ВСЕГО 3 этап 2026``; that is a different KFZ row. The source text
        itself is never changed.
        """
        normalized = " ".join(raw_text.casefold().split())
        target = " ".join(self.target_row_label.casefold().split())
        if not target or target not in normalized:
            return False
        total_prefix = re.search(
            r"(?:^|\s)(?:всего|итого)\s+" + re.escape(target), normalized
        )
        return total_prefix is None

    def is_working_role(self, role: FileRole) -> bool:
        return role in self.working_roles

    def is_excluded_role(self, role: FileRole) -> bool:
        return role in self.technical_roles

    def month_labels(self) -> list[str]:
        """Configured month labels, or a month list derived from the period."""
        if self.service_period.months:
            return list(self.service_period.months)
        labels: list[str] = []
        year, month = self.service_period.start.year, self.service_period.start.month
        while (year, month) <= (
            self.service_period.end.year,
            self.service_period.end.month,
        ):
            labels.append(f"{year:04d}-{month:02d}")
            month += 1
            if month == 13:
                year, month = year + 1, 1
        return labels
