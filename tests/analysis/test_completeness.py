from __future__ import annotations

import hashlib
from pathlib import Path

from nitka.analysis.completeness import CompletenessPolicy, assess_completeness
from nitka.analysis.service import check_report
from nitka.contracts.analysis import Match, Scope
from nitka.contracts.extraction import (
    CellLocation,
    ExtractedRow,
    ExtractionResult,
    FileRecord,
    PageLocation,
    Source,
)
from nitka.contracts.profile import CaseProfile
from nitka.pipeline import analyze_extraction

SCOPE = Scope(currency="RUB", amount_basis="GROSS", reporting_period="2026-05")
PROFILE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "case_contract.json"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class _Builder:
    def __init__(self) -> None:
        self.files: list[FileRecord] = []
        self.sources: list[Source] = []
        self.rows: list[ExtractedRow] = []
        self._amounts: dict[str, str] = {}

    def file(self, file_id: str, role: str, name: str) -> None:
        self.files.append(
            FileRecord(
                file_id=file_id,
                name=name,
                sha256=_sha(file_id),
                role=role,  # type: ignore[arg-type]
                status="COMPLETED",
            )
        )

    def report(
        self, row_id: str, amount: str, article: str | None = None, kind: str = "ITEM"
    ) -> str:
        file_id = "F-REPORT"
        amount_id = f"S-{row_id}-AMOUNT"
        self._source(
            amount_id,
            file_id,
            "D-REPORT",
            amount,
            CellLocation(kind="CELL", sheet="Kfz", cell=f"D{len(self.rows) + 2}"),
            "TABLE",
        )
        fields: dict[str, str] = {}
        if article is not None:
            article_id = f"S-{row_id}-ARTICLE"
            self._source(
                article_id,
                file_id,
                "D-REPORT",
                article,
                CellLocation(kind="CELL", sheet="Kfz", cell=f"B{len(self.rows) + 2}"),
                "TABLE",
            )
            fields["article_code"] = article_id
        self.rows.append(
            ExtractedRow(
                row_id=row_id,
                kind=kind,  # type: ignore[arg-type]
                amount_source_id=amount_id,
                fields=fields,
            )
        )
        return row_id

    def support(
        self,
        row_id: str,
        article: str,
        amount: str,
        month: str,
        *,
        file_id: str,
    ) -> str:
        document_id = f"D-{row_id}"
        amount_id = f"S-{row_id}-AMOUNT"
        self._source(
            amount_id,
            file_id,
            document_id,
            amount,
            PageLocation(kind="PAGE", page=1),
            "PDF_TEXT",
        )
        article_id = f"S-{row_id}-ARTICLE"
        self._source(
            article_id,
            file_id,
            document_id,
            article,
            PageLocation(kind="PAGE", page=1),
            "PDF_TEXT",
        )
        period_id = f"S-{row_id}-PERIOD"
        self._source(
            period_id,
            file_id,
            document_id,
            month,
            PageLocation(kind="PAGE", page=1),
            "PDF_TEXT",
        )
        self.rows.append(
            ExtractedRow(
                row_id=row_id,
                kind="ITEM",
                amount_source_id=amount_id,
                fields={"article_code": article_id, "service_period": period_id},
            )
        )
        return row_id

    def build(self) -> ExtractionResult:
        return ExtractionResult(
            extraction_version="completeness-1",
            files=self.files,
            sources=self.sources,
            rows=self.rows,
        )

    def _source(
        self,
        source_id: str,
        file_id: str,
        document_id: str,
        value: str,
        location: object,
        method: str,
    ) -> None:
        self.sources.append(
            Source(
                source_id=source_id,
                file_id=file_id,
                document_id=document_id,
                location=location,  # type: ignore[arg-type]
                method=method,  # type: ignore[arg-type]
                raw_text=value,
                value=value,
                review_status="UNREVIEWED",
            )
        )


def _case(*, include_may: bool = True) -> tuple[ExtractionResult, list[Match]]:
    builder = _Builder()
    builder.file("F-REPORT", "REPORT", "kfz.xlsx")
    builder.report("R-0200", "100.00", "0200")
    builder.report("R-0300", "50.00", "0300")
    builder.report("R-0800", "100.00", "0800")
    builder.report("R-0900", "100.00", "0900")
    builder.report("R-TOTAL", "350.00", kind="DOCUMENT_TOTAL")

    support_ids: dict[str, list[str]] = {"0200": [], "0300": [], "0800": [], "0900": []}
    months = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05"]
    for index, month in enumerate(months, start=1):
        if month == "2026-05" and not include_may:
            continue
        file_id = f"F-SAL-{index}"
        builder.file(file_id, "SUPPORT", f"payroll-{index}.xlsx")
        support_ids["0200"].append(
            builder.support(f"SAL-{index}", "0200", "20.00", month, file_id=file_id)
        )
    for index, month in enumerate(months, start=1):
        file_id = f"F-CON-{index}"
        builder.file(file_id, "SUPPORT", f"contrib-{index}.xlsx")
        support_ids["0300"].append(
            builder.support(f"CON-{index}", "0300", "10.00", month, file_id=file_id)
        )
    builder.file("F-PROD", "SUPPORT", "prod.pdf")
    support_ids["0800"].append(
        builder.support("PROD", "0800", "112.50", "2026-05", file_id="F-PROD")
    )
    builder.file("F-ADM", "SUPPORT", "admin.pdf")
    support_ids["0900"].append(
        builder.support("ADM", "0900", "100.00", "2026-05", file_id="F-ADM")
    )

    matches = [
        Match(
            match_id=f"M-{article}",
            report_row_id=f"R-{article}",
            support_row_ids=support_ids[article],
            state="ACCEPTED",
        )
        for article in ("0200", "0300", "0800", "0900")
    ]
    return builder.build(), matches


def _policy() -> CompletenessPolicy:
    profile = CaseProfile.model_validate_json(PROFILE_PATH.read_text(encoding="utf-8"))
    return CompletenessPolicy.from_profile(profile)


def test_complete_package_separates_rows_from_checks_and_is_not_partial() -> None:
    extraction, matches = _case()
    result = analyze_extraction(extraction, matches, SCOPE, completeness=_policy())

    assert result.execution_status == "COMPLETED"
    assert result.decision == "DISCREPANCIES_FOUND"
    assert result.coverage.report_rows == 4
    assert result.coverage.checked_rows == 4
    assert result.coverage.required_checks == 10
    assert result.coverage.completed_checks == 10
    assert result.coverage.report_rows != result.coverage.required_checks


def test_missing_month_is_not_zero_and_not_an_arithmetic_fail() -> None:
    extraction, matches = _case(include_may=False)
    result = analyze_extraction(extraction, matches, SCOPE, completeness=_policy())
    checks = {check.check_id: check for check in result.checks}

    assert result.execution_status == "PARTIAL"
    assert result.decision == "DISCREPANCIES_FOUND"
    assert checks["ARTICLE_AMOUNT:0200"].state == "NOT_RUN"
    assert checks["ARTICLE_AMOUNT:0200"].reason_code == "INSUFFICIENT_DOCUMENTS"
    assert checks["REPORT_AMOUNT:R-0200"].state == "NOT_RUN"
    assert checks["SUPPORT_TOTAL:OVERALL"].state == "NOT_RUN"
    assert not any(
        check.state == "FAIL" and check.target_id in {"ARTICLE:0200", "R-0200"}
        for check in result.checks
    )
    assert any(issue.code == "MISSING_MONTH" for issue in result.issues)
    assert result.coverage.report_rows == 4
    assert result.coverage.checked_rows == 3


def test_confirmed_0800_discrepancy_is_compatible_with_partial() -> None:
    extraction, matches = _case(include_may=False)
    result = analyze_extraction(extraction, matches, SCOPE, completeness=_policy())
    checks = {check.check_id: check for check in result.checks}

    assert result.execution_status == "PARTIAL"
    assert result.decision == "DISCREPANCIES_FOUND"
    assert checks["ARTICLE_AMOUNT:0800"].state == "FAIL"
    assert checks["ARTICLE_AMOUNT:0800"].calculation is not None
    assert checks["ARTICLE_AMOUNT:0800"].calculation.delta == "12.50"
    assert checks["REPORT_TOTAL:R-TOTAL"].state == "PASS"
    assert result.coverage.checked_rows < result.coverage.report_rows


def test_categories_show_amount_period_and_missing_data() -> None:
    extraction, matches = _case(include_may=False)
    result = analyze_extraction(extraction, matches, SCOPE, completeness=_policy())
    # Category counts are available from the explicit assessment API.
    assessment = assess_completeness(
        extraction, check_report(extraction, matches, SCOPE), _policy()
    )
    assert assessment.categories["AMOUNT"] > 0
    # The downgraded 0200/overall checks and missing-month issue are missing data.
    assert assessment.categories["MISSING_DATA"] > 0
    assert assessment.categories["PERIOD"] > 0
    assert result.issues
