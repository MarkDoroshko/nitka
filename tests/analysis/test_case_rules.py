from __future__ import annotations

import hashlib
from decimal import Decimal

from nitka.analysis.service import check_report
from nitka.contracts.analysis import Calculation, Check, Match, Scope
from nitka.contracts.extraction import (
    CellLocation,
    ExtractedRow,
    ExtractionResult,
    FileRecord,
    PageLocation,
    Source,
)

SCOPE = Scope(currency="RUB", amount_basis="GROSS", reporting_period="2026-05")

REPORT_AMOUNTS = {
    "0200": "5558474.83",
    "0300": "1671359.96",
    "0800": "1133298.10",
    "0900": "3204744.51",
}
SUPPORT_AMOUNTS = {
    "0200": "5558474.83",
    "0300": "1671359.96",
    "0800": "1145798.10",
    "0900": "3204744.51",
}
REPORT_TOTAL = "11567877.40"
SUPPORT_TOTAL = "11580377.40"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class _Builder:
    def __init__(self) -> None:
        self.files: list[FileRecord] = []
        self.sources: list[Source] = []
        self.rows: list[ExtractedRow] = []
        self._amount_sources: dict[str, str] = {}

    def add_file(self, file_id: str, role: str, name: str) -> None:
        self.files.append(
            FileRecord(
                file_id=file_id,
                name=name,
                sha256=_sha(file_id),
                role=role,  # type: ignore[arg-type]
                status="COMPLETED",
            )
        )

    def add_report(
        self,
        row_id: str,
        amount: str,
        *,
        article: str | None = None,
        kind: str = "ITEM",
        amount_source_id: str | None = None,
    ) -> str:
        file_id = "F-REPORT"
        amount_id = amount_source_id or f"S-{row_id}-AMOUNT"
        if amount_source_id is None:
            self._add_source(
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
            self._add_source(
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

    def add_support(
        self,
        row_id: str,
        article: str,
        amount: str,
        *,
        file_id: str,
        amount_source_id: str | None = None,
        document_id: str | None = None,
    ) -> str:
        document_id = document_id or f"D-{row_id}"
        amount_id = amount_source_id or f"S-{row_id}-AMOUNT"
        if amount_source_id is None:
            self._add_source(
                amount_id,
                file_id,
                document_id,
                amount,
                PageLocation(kind="PAGE", page=1),
                "PDF_TEXT",
            )
        article_id = f"S-{row_id}-ARTICLE"
        self._add_source(
            article_id,
            file_id,
            document_id,
            article,
            PageLocation(kind="PAGE", page=1),
            "PDF_TEXT",
        )
        self.rows.append(
            ExtractedRow(
                row_id=row_id,
                kind="ITEM",
                amount_source_id=amount_id,
                fields={"article_code": article_id},
            )
        )
        return row_id

    def build(self) -> ExtractionResult:
        return ExtractionResult(
            extraction_version="case-rules-1",
            files=self.files,
            sources=self.sources,
            rows=self.rows,
        )

    def _add_source(
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


def _case() -> tuple[ExtractionResult, list[Match]]:
    builder = _Builder()
    builder.add_file("F-REPORT", "REPORT", "kfz.xlsx")
    for article, amount in REPORT_AMOUNTS.items():
        builder.add_report(f"R-{article}", amount, article=article)
    builder.add_report("R-TOTAL", REPORT_TOTAL, kind="DOCUMENT_TOTAL")

    payroll = [
        "1000000.00",
        "1100000.00",
        "1158474.83",
        "1150000.00",
        "1150000.00",
    ]
    contributions = [
        "333000.00",
        "335100.00",
        "330540.00",
        "337225.00",
        "335494.96",
    ]
    support_ids: dict[str, list[str]] = {"0200": [], "0300": [], "0800": [], "0900": []}
    for index, amount in enumerate(payroll, start=1):
        file_id = f"F-SAL-{index}"
        builder.add_file(file_id, "SUPPORT", f"payroll-{index}.xlsx")
        support_ids["0200"].append(
            builder.add_support(f"SAL-{index}", "0200", amount, file_id=file_id)
        )
    for index, amount in enumerate(contributions, start=1):
        file_id = f"F-CON-{index}"
        builder.add_file(file_id, "SUPPORT", f"contrib-{index}.xlsx")
        support_ids["0300"].append(
            builder.add_support(f"CON-{index}", "0300", amount, file_id=file_id)
        )
    for index, amount in enumerate(("500000.00", "645798.10"), start=1):
        file_id = f"F-PROD-{index}"
        builder.add_file(file_id, "SUPPORT", f"prod-{index}.pdf")
        support_ids["0800"].append(
            builder.add_support(f"PROD-{index}", "0800", amount, file_id=file_id)
        )
    for index, amount in enumerate(("1500000.00", "1704744.51"), start=1):
        file_id = f"F-ADM-{index}"
        builder.add_file(file_id, "SUPPORT", f"admin-{index}.pdf")
        support_ids["0900"].append(
            builder.add_support(f"ADM-{index}", "0900", amount, file_id=file_id)
        )

    # Same amount source reused by another row id must count once.
    builder.add_support(
        "SAL-1-DUPLICATE",
        "0200",
        "1000000.00",
        file_id="F-SAL-1",
        amount_source_id="S-SAL-1-AMOUNT",
        document_id="D-SAL-1",
    )
    builder.add_report(
        "R-0200-DUPLICATE",
        "5558474.83",
        article="0200",
        amount_source_id="S-R-0200-AMOUNT",
    )

    extraction = builder.build()
    matches = [
        Match(
            match_id="M-0800",
            report_row_id="R-0800",
            support_row_ids=support_ids["0800"],
            state="ACCEPTED",
        ),
        Match(
            match_id="M-0900",
            report_row_id="R-0900",
            support_row_ids=support_ids["0900"],
            state="ACCEPTED",
        ),
    ]
    return extraction, matches


def _by_id(checks: list[Check]) -> dict[str, Check]:
    return {check.check_id: check for check in checks}


def _calc(check: Check) -> Calculation:
    assert check.calculation is not None
    return check.calculation


def test_case_articles_are_aggregated_in_decimal_and_rub() -> None:
    extraction, matches = _case()
    checks = _by_id(check_report(extraction, matches, SCOPE))

    for code in ("0200", "0300", "0900"):
        check = checks[f"ARTICLE_AMOUNT:{code}"]
        assert check.state == "PASS"
        assert _calc(check).unit == "RUB"
        assert _calc(check).delta == "0.00"

    assert _calc(checks["ARTICLE_AMOUNT:0200"]).actual == "5558474.83"
    assert _calc(checks["ARTICLE_AMOUNT:0300"]).actual == "1671359.96"
    assert _calc(checks["ARTICLE_AMOUNT:0300"]).expected == "1671359.96"
    assert _calc(checks["ARTICLE_AMOUNT:0900"]).actual == "3204744.51"


def test_only_0800_and_overall_have_positive_12500_delta() -> None:
    extraction, matches = _case()
    checks = _by_id(check_report(extraction, matches, SCOPE))

    production = checks["ARTICLE_AMOUNT:0800"]
    assert production.state == "FAIL"
    assert production.reason_code == "ARTICLE_SUPPORT_EXCEEDS_REPORT"
    assert _calc(production).actual == "1145798.10"
    assert _calc(production).expected == "1133298.10"
    assert _calc(production).delta == "12500.00"

    overall = checks["SUPPORT_TOTAL:OVERALL"]
    assert overall.state == "FAIL"
    assert overall.reason_code == "OVERALL_SUPPORT_EXCEEDS_REPORT"
    assert _calc(overall).actual == "11580377.40"
    assert _calc(overall).expected == "11567877.40"
    assert _calc(overall).delta == "12500.00"

    for code in ("0200", "0300", "0900"):
        assert checks[f"ARTICLE_AMOUNT:{code}"].state == "PASS"
    assert checks["REPORT_TOTAL:R-TOTAL"].state == "PASS"


def test_reused_amount_source_is_counted_once_without_losing_operations() -> None:
    extraction, matches = _case()
    checks = _by_id(check_report(extraction, matches, SCOPE))

    payroll = checks["ARTICLE_AMOUNT:0200"]
    assert _calc(payroll).actual == "5558474.83"
    # The duplicate report row and support row did not double the article.
    assert _calc(payroll).expected == "5558474.83"
    assert checks["REPORT_TOTAL:R-TOTAL"].state == "PASS"
    assert payroll.evidence_ids.count("S-SAL-1-AMOUNT") == 1


def test_contributions_are_summed_not_derived_from_a_rate() -> None:
    extraction, matches = _case()
    checks = _by_id(check_report(extraction, matches, SCOPE))

    contributions = checks["ARTICLE_AMOUNT:0300"]
    assert _calc(contributions).actual == "1671359.96"
    assert _calc(contributions).expected == "1671359.96"
    assert Decimal(_calc(contributions).actual) != Decimal("5558474.83") * Decimal(
        "0.30"
    )


def test_internal_total_and_support_overall_are_separate_checks() -> None:
    extraction, matches = _case()
    checks = _by_id(check_report(extraction, matches, SCOPE))

    internal = checks["REPORT_TOTAL:R-TOTAL"]
    overall = checks["SUPPORT_TOTAL:OVERALL"]
    assert internal.check_id != overall.check_id
    assert internal.rule_id == "REPORT_TOTAL"
    assert overall.rule_id == "REPORT_TOTAL"
    assert internal.state == "PASS"
    assert overall.state == "FAIL"
    assert internal.evidence_ids != overall.evidence_ids
