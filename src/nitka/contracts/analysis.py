from typing import Literal

from pydantic import Field

from nitka.contracts.extraction import Amount, Contract, FileRecord, Issue, Source


class Scope(Contract):
    currency: str
    amount_basis: Literal["GROSS", "NET"]
    reporting_period: str


class Match(Contract):
    match_id: str
    report_row_id: str
    support_row_ids: list[str] = Field(min_length=1)
    state: Literal["ACCEPTED", "AMBIGUOUS", "REJECTED"]


class Operand(Contract):
    name: Literal["reported", "supported"]
    value: str | None
    source_ids: list[str]


class Calculation(Contract):
    actual: str
    expected: str
    delta: str
    tolerance: Amount
    unit: str


class Check(Contract):
    check_id: str
    rule_id: Literal["REPORT_AMOUNT", "REPORT_TOTAL"] = "REPORT_AMOUNT"
    target_id: str
    match_id: str | None
    state: Literal["PASS", "FAIL", "NOT_RUN"]
    reason_code: str
    operands: list[Operand]
    calculation: Calculation | None
    evidence_ids: list[str]


class ExcludedDocument(Contract):
    """A document excluded from matching, with its own visible reason.

    This is deliberately separate from ``Match``: an excluded duplicate, a
    foreign contract or a foreign period must not turn a correct article into a
    false amount FAIL.
    """

    file_id: str
    document_id: str
    reason: Literal[
        "DUPLICATE",
        "OTHER_CONTRACT",
        "OTHER_PERIOD",
        "TECHNICAL",
        "ORACLE",
        "UNRELATED",
    ]
    detail: str
    source_ids: list[str] = Field(default_factory=list)


class Coverage(Contract):
    report_rows: int
    checked_rows: int
    required_checks: int
    completed_checks: int


class AnalysisResult(Contract):
    schema_version: Literal["0.1.0"] = "0.1.0"
    run_id: str
    revision: int = Field(ge=1)
    input_manifest_hash: str
    rules_version: str
    extraction_version: str
    scope: Scope
    execution_status: Literal["COMPLETED", "PARTIAL", "FAILED"]
    decision: Literal[
        "NO_DISCREPANCIES", "DISCREPANCIES_FOUND", "REVIEW_REQUIRED", "NOT_ASSESSED"
    ]
    coverage: Coverage
    files: list[FileRecord]
    sources: list[Source]
    matches: list[Match]
    checks: list[Check]
    issues: list[Issue]
    excluded_documents: list[ExcludedDocument] = Field(default_factory=list)
