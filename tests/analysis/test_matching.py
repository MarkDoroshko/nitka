from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from nitka.analysis.classification import ArticleClassification, DocumentClassifier
from nitka.analysis.matching import CaseMatcher
from nitka.contracts.analysis import Scope
from nitka.contracts.extraction import (
    ExtractedRow,
    ExtractionResult,
    FileRecord,
    PageLocation,
    Source,
)
from nitka.contracts.profile import CaseProfile
from nitka.pipeline import run_pipeline

PROFILE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "case_contract.json"


def _profile() -> CaseProfile:
    return CaseProfile.model_validate_json(PROFILE_PATH.read_text(encoding="utf-8"))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class _Operation:
    row_id: str
    document_number: str
    amount: str = "100.00"
    contract: str = field(default_factory=lambda: _profile().contract)
    period: str = "2026-01"
    document_date: str = "2026-01-15"
    operation_id: str | None = None
    description: str = "производственная услуга"
    article_code: str | None = None
    fields: dict[str, str] = field(default_factory=dict)


class _ExtractionBuilder:
    def __init__(self) -> None:
        self.files: list[FileRecord] = []
        self.sources: list[Source] = []
        self.rows: list[ExtractedRow] = []

    def add_report(
        self, row_id: str, article_code: str, amount: str = "9999.00"
    ) -> None:
        file_id = "F-REPORT"
        document_id = "D-REPORT"
        if not any(file.file_id == file_id for file in self.files):
            self.files.append(
                FileRecord(
                    file_id=file_id,
                    name="kfz.xlsx",
                    sha256=_sha(file_id),
                    role="REPORT",
                    status="COMPLETED",
                )
            )
        article = self._field(file_id, document_id, row_id, "ARTICLE", article_code)
        amount_source = self._field(file_id, document_id, row_id, "AMOUNT", amount)
        self.rows.append(
            ExtractedRow(
                row_id=row_id,
                kind="ITEM",
                amount_source_id=amount_source,
                fields={"article_code": article},
            )
        )

    def add_operation(self, operation: _Operation, *, role: str = "SUPPORT") -> None:
        file_id = f"F-{operation.row_id}"
        document_id = f"D-{operation.row_id}"
        self.files.append(
            FileRecord(
                file_id=file_id,
                name=f"notes-{operation.row_id}.pdf",
                sha256=_sha(file_id),
                role=role,  # type: ignore[arg-type]
                status="COMPLETED",
            )
        )
        fields: dict[str, str] = {}
        values = {
            "contract": operation.contract,
            "service_period": operation.period,
            "document_date": operation.document_date,
            "document_number": operation.document_number,
            "description": operation.description,
            "classification_basis": "requisites and content",
            "fragment": operation.description,
        }
        if operation.operation_id is not None:
            values["operation_id"] = operation.operation_id
        if operation.article_code is not None:
            values["article_code"] = operation.article_code
        for name, value in values.items():
            fields[name] = self._field(
                file_id, document_id, operation.row_id, name, value
            )
        amount_source = self._field(
            file_id, document_id, operation.row_id, "amount", operation.amount
        )
        self.rows.append(
            ExtractedRow(
                row_id=operation.row_id,
                kind="ITEM",
                amount_source_id=amount_source,
                fields=fields,
            )
        )

    def build(self) -> ExtractionResult:
        return ExtractionResult(
            extraction_version="matching-test-1",
            files=self.files,
            sources=self.sources,
            rows=self.rows,
        )

    def _field(
        self, file_id: str, document_id: str, row_id: str, name: str, value: str
    ) -> str:
        source_id = f"S-{row_id}-{name.upper()}"
        self.sources.append(
            Source(
                source_id=source_id,
                file_id=file_id,
                document_id=document_id,
                location=PageLocation(kind="PAGE", page=1),
                method="PDF_TEXT",
                raw_text=value,
                value=value,
                review_status="UNREVIEWED",
            )
        )
        return source_id


@pytest.mark.parametrize("scope_problem", ["article_contract", "total_contract", "second_report"])
def test_pipeline_blocks_matching_amounts_when_report_scope_is_unverified(
    scope_problem: str,
) -> None:
    profile = _profile().model_copy(update={"article_codes": ["0800"]})
    builder = _ExtractionBuilder()
    builder.add_report("R-0800", "0800", "100.00")
    builder.add_report("R-TOTAL", "0800", "150.00")
    item, total = builder.rows
    total.kind = "DOCUMENT_TOTAL"
    total.fields.clear()
    for row, problem in ((item, "article_contract"), (total, "total_contract")):
        contract = "99/99/99-99" if scope_problem == problem else profile.contract
        row.fields["contract"] = builder._field(
            "F-REPORT", "D-REPORT", row.row_id, "contract", contract
        )
    if scope_problem == "second_report":
        builder.files.append(FileRecord(
            file_id="F-SECOND", name="second.pdf", sha256=_sha("second"),
            role="REPORT", status="FAILED",
        ))
    builder.add_operation(_Operation("S-1", "149"))

    result = run_pipeline(
        [],
        Scope(currency="RUB", amount_basis="GROSS", reporting_period="2026-01..2026-05"),
        extractor=lambda _: builder.build(),
        matcher=CaseMatcher(profile),
    )
    assert result.decision == "REVIEW_REQUIRED"
    assert not result.matches
    assert {check.check_id for check in result.checks} == {
        "ARTICLE_AMOUNT:0800", "REPORT_TOTAL:R-TOTAL", "SUPPORT_TOTAL:OVERALL",
    }
    assert all(
        check.reason_code == "REPORT_SCOPE_UNVERIFIED"
        and check.state == "NOT_RUN" and check.calculation is None
        for check in result.checks
    )


def _standard_operations() -> list[_Operation]:
    operations = [
        _Operation("R-04", "04", "100.00", operation_id="OP-04"),
        _Operation("R-05", "05", "200.00", operation_id="OP-05"),
        _Operation("R-06", "06", "300.00", operation_id="OP-06"),
        _Operation("R-07", "07", "400.00", operation_id="OP-07"),
        _Operation(
            "R-08",
            "08",
            "500.00",
            operation_id="OP-08",
            description="административный офис",
        ),
        _Operation(
            "R-09",
            "09",
            "600.00",
            operation_id="OP-09",
            description="административная связь",
        ),
        _Operation(
            "R-11",
            "11",
            "700.00",
            operation_id="OP-11",
            description="административная аренда",
        ),
        _Operation(
            "R-12",
            "12",
            "800.00",
            operation_id="OP-12",
            description="административный банк",
        ),
        _Operation(
            "R-13",
            "13",
            "900.00",
            operation_id="OP-13",
            description="административный консультант",
        ),
    ]
    return operations


def _standard_extraction(
    *, different_amounts: bool = False
) -> tuple[ExtractionResult, CaseProfile]:
    profile = _profile()
    builder = _ExtractionBuilder()
    builder.add_report("R-0800", "0800")
    builder.add_report("R-0900", "0900")
    for operation in _standard_operations():
        builder.add_operation(operation)
    duplicate = _Operation(
        "R-10",
        "10",
        "150.00" if different_amounts else "400.00",
        operation_id="OP-07",
    )
    builder.add_operation(duplicate)
    builder.add_operation(
        _Operation("R-14", "14", "140.00", period="2026-06", operation_id="OP-14")
    )
    builder.add_operation(
        _Operation(
            "R-15",
            "15",
            "150.00",
            contract="OTHER-DEMO-CONTRACT",
            operation_id="OP-15",
        )
    )
    return builder.build(), profile


def test_matches_allowed_articles_and_excludes_three_documents() -> None:
    extraction, profile = _standard_extraction()

    matcher = CaseMatcher(profile)
    outcome = matcher.match(extraction)
    assert list(matcher(extraction)) == list(outcome.matches)

    accepted = [match for match in outcome.matches if match.state == "ACCEPTED"]
    assert len(accepted) == 2
    by_article = {match.report_row_id: match.support_row_ids for match in accepted}
    assert by_article == {
        "R-0800": ["R-04", "R-05", "R-06", "R-07"],
        "R-0900": ["R-08", "R-09", "R-11", "R-12", "R-13"],
    }
    assert {item.reason for item in outcome.excluded_documents} == {
        "DUPLICATE",
        "OTHER_PERIOD",
        "OTHER_CONTRACT",
    }
    assert len(outcome.excluded_documents) == 3
    assert all(item.source_ids for item in outcome.excluded_documents)
    assert outcome.ambiguous_operations == ()


def test_duplicate_is_counted_once_by_operation_not_hash_or_amount() -> None:
    extraction, profile = _standard_extraction()

    outcome = CaseMatcher(profile).match(extraction)

    accepted = [match for match in outcome.matches if match.state == "ACCEPTED"]
    production = next(match for match in accepted if match.report_row_id == "R-0800")
    assert production.support_row_ids == ["R-04", "R-05", "R-06", "R-07"]
    assert "R-10" not in production.support_row_ids
    duplicate = next(
        item for item in outcome.excluded_documents if item.reason == "DUPLICATE"
    )
    assert duplicate.source_ids
    assert duplicate.detail


def test_same_identity_with_different_amounts_needs_manual_review() -> None:
    extraction, profile = _standard_extraction(different_amounts=True)

    outcome = CaseMatcher(profile).match(extraction)

    accepted = [match for match in outcome.matches if match.state == "ACCEPTED"]
    assert not any(match.report_row_id == "R-0800" for match in accepted)
    assert {operation.row_id for operation in outcome.ambiguous_operations} == {
        "R-07",
        "R-10",
    }
    review = [match for match in outcome.matches if match.state == "AMBIGUOUS"]
    review_row = next(match for match in review if match.report_row_id == "R-0800")
    assert {"R-07", "R-10"} <= set(review_row.support_row_ids)
    assert not any(item.reason == "DUPLICATE" for item in outcome.excluded_documents)


def test_partially_matching_document_numbers_do_not_merge() -> None:
    profile = _profile()
    builder = _ExtractionBuilder()
    builder.add_report("R-0900", "0900")
    builder.add_operation(_Operation("R-12", "12", "100.00", description="административная услуга"))
    builder.add_operation(_Operation("R-12P", "12-П", "200.00", description="административная услуга"))
    extraction = builder.build()

    outcome = CaseMatcher(profile).match(extraction)

    accepted = [match for match in outcome.matches if match.state == "ACCEPTED"]
    assert len(accepted) == 1
    assert accepted[0].support_row_ids == ["R-12", "R-12P"]
    assert not [
        item for item in outcome.excluded_documents if item.reason == "DUPLICATE"
    ]


def test_amounts_and_filenames_are_not_used_for_matching() -> None:
    extraction, profile = _standard_extraction()
    report = next(row for row in extraction.rows if row.row_id == "R-0800")
    report_source = next(
        source
        for source in extraction.sources
        if source.source_id == report.amount_source_id
    )
    report_source.value = "1.00"
    report_source.raw_text = "1.00"

    outcome = CaseMatcher(profile).match(extraction)

    assert any(
        match.report_row_id == "R-0800" and match.state == "ACCEPTED"
        for match in outcome.matches
    )


def test_oracle_is_never_a_linked_source_document() -> None:
    profile = _profile()
    builder = _ExtractionBuilder()
    builder.add_report("R-0800", "0800")
    builder.add_operation(_Operation("R-04", "04", "100.00", operation_id="OP-04"))
    builder.add_operation(
        _Operation("R-ORACLE", "04", "100.00", operation_id="OP-04"),
        role="ORACLE",
    )

    outcome = CaseMatcher(profile).match(builder.build())

    accepted = [match for match in outcome.matches if match.state == "ACCEPTED"]
    assert accepted
    assert all("R-ORACLE" not in match.support_row_ids for match in accepted)
    assert all("R-ORACLE" not in item.file_id for item in outcome.excluded_documents)


def test_ambiguous_classification_is_not_turned_into_an_accepted_article() -> None:
    profile = _profile()
    classifier = DocumentClassifier(profile)
    result = classifier.classify("99", "неизвестный расход", None)
    assert result.ambiguous
    assert isinstance(result, ArticleClassification)

    builder = _ExtractionBuilder()
    builder.add_report("R-0800", "0800")
    builder.add_operation(
        _Operation(
            "R-99",
            "99",
            "100.00",
            operation_id="OP-99",
            description="неизвестный расход",
        )
    )
    outcome = CaseMatcher(profile).match(builder.build())

    assert outcome.ambiguous_operations
    assert not [match for match in outcome.matches if match.state == "ACCEPTED"]


def test_classification_ignores_document_numbers() -> None:
    classifier = DocumentClassifier(_profile())
    assert classifier.classify("04", "Административное сопровождение", None).article_code == "0900"
    assert classifier.classify("77", "Техническое обслуживание испытательного стенда", None).article_code == "0800"
    assert classifier.classify("07", "Неназванная услуга", None).ambiguous


@pytest.mark.parametrize(
    ("period", "document_date", "accepted", "excluded"),
    [
        ("", "2026-01-31", True, False),
        ("2026-06", "2026-01-31", False, True),
        ("2026-01", "2026-06-01", True, False),
        ("2026-01..2026-06", "2026-01-31", False, False),
        ("", "2026-02-30", False, False),
    ],
)
def test_period_evidence_precedes_document_date(
    period: str, document_date: str, accepted: bool, excluded: bool
) -> None:
    builder = _ExtractionBuilder()
    builder.add_report("R-0800", "0800")
    builder.add_operation(_Operation("S-1", "149", period=period, document_date=document_date))
    extraction = builder.build()
    if not period:
        extraction.rows[-1].fields.pop("service_period")
    result = CaseMatcher(_profile()).match(extraction)
    assert any(m.state == "ACCEPTED" for m in result.matches) is accepted
    assert any(item.reason == "OTHER_PERIOD" for item in result.excluded_documents) is excluded


def test_same_operation_with_equivalent_amount_format_is_duplicate() -> None:
    builder = _ExtractionBuilder()
    builder.add_report("R-0800", "0800")
    builder.add_operation(_Operation("S-1", "123", "100.00"))
    builder.add_operation(_Operation("S-2", "123", "100"))
    outcome = CaseMatcher(_profile()).match(builder.build())
    (match,) = outcome.matches
    assert match.state == "ACCEPTED" and match.support_row_ids == ["S-1"]
    (excluded,) = outcome.excluded_documents
    assert excluded.reason == "DUPLICATE"


def test_unknown_or_unreadable_support_blocks_an_otherwise_accepted_article() -> None:
    builder = _ExtractionBuilder()
    builder.add_report("R-0800", "0800")
    builder.add_operation(_Operation("S-1", "1"))
    builder.add_operation(_Operation("S-2", "2", description="Неназванная услуга"))
    extraction = builder.build()
    matcher = CaseMatcher(_profile())
    assert matcher.match(extraction).blocked_articles["0800"] == "UNASSIGNED_SUPPORT"
    extraction.rows.pop()
    extraction.files[-1].status = "PARTIAL"
    assert matcher.match(extraction).blocked_articles["0800"] == "SUPPORT_INPUT_UNREADABLE"
    assert all(link.state == "AMBIGUOUS" for link in matcher(extraction))


def test_missing_payroll_month_is_not_a_shortfall() -> None:
    profile = _profile()
    builder = _ExtractionBuilder()
    builder.add_report("R-0200", "0200", "500.00")
    builder.add_report("R-0800", "0800", "150.00")
    for month in range(1, 5):
        builder.add_operation(_Operation(
            f"PAY-{month}", "30", period=f"2026-{month:02}", article_code="0200",
            operation_id="PAY",
        ))
    builder.add_operation(_Operation("ACT", "45", "100.00"))
    extraction = builder.build()
    result = run_pipeline(
        [], Scope(currency="RUB", amount_basis="GROSS", reporting_period="2026-01..2026-05"),
        extractor=lambda _: extraction, matcher=CaseMatcher(profile),
    )
    checks = {check.check_id: check for check in result.checks}
    payroll = checks["ARTICLE_AMOUNT:0200"]
    assert payroll.state == "NOT_RUN" and payroll.reason_code == "SUPPORT_MONTH_MISSING"
    assert payroll.operands[1].value is None and payroll.calculation is None
    assert checks["SUPPORT_TOTAL:OVERALL"].state == "NOT_RUN"
    assert checks["ARTICLE_AMOUNT:0800"].state == "FAIL"
    assert result.decision == "DISCREPANCIES_FOUND"
    assert result.coverage.report_rows == 2 and result.coverage.checked_rows == 1
    assert result.coverage.required_checks > result.coverage.report_rows
