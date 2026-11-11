"""Audit metadata stored alongside each corrected revision."""

from typing import Literal

from pydantic import Field

from nitka.contracts.extraction import Amount, Contract


class AmountCorrection(Contract):
    schema_version: Literal["1"] = "1"
    parent_revision: int = Field(ge=1)
    revision: int = Field(ge=2)
    source_id: str
    old_value: str | None
    new_value: Amount
    comment: str = ""
