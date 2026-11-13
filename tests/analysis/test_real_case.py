import os
from decimal import Decimal
from pathlib import Path

import pytest

from nitka.analysis.matching import CaseMatcher
from nitka.contracts.analysis import CaseMatching, Scope
from nitka.contracts.extraction import FileRole, OcrProvenance
from nitka.contracts.profile import CaseProfile
from nitka.extraction.package import PackageExtractor
from nitka.extraction.ports import InputDocument
from nitka.ocr import OcrEngine
from nitka.storage import StoredRun, load_run, replay_run, run_and_save

_MANIFEST = Path(__file__).resolve().parents[2] / "config" / "ocr" / "ppocr-v5.json"


def _run_organizer_case(
    tmp_path: Path, *, omit_january_payroll: bool = False
) -> tuple[StoredRun, CaseProfile]:
    case_dir = os.environ.get("NITKA_PACKAGE_SMOKE_DIR")
    if case_dir is None:
        pytest.skip("Set NITKA_PACKAGE_SMOKE_DIR for the real organizer case")
    profile = CaseProfile.model_validate_json(
        Path(os.environ["NITKA_PACKAGE_SMOKE_PROFILE"]).read_bytes()
    )
    engine = OcrEngine(Path(os.environ["NITKA_OCR_MODELS"]), _MANIFEST)
    roles: tuple[FileRole, ...] = ("REPORT", "SUPPORT", "TECHNICAL")
    documents = [
        InputDocument(path, role)
        for role in roles
        for path in sorted((Path(case_dir) / role.lower()).iterdir())
        if not (
            omit_january_payroll and role == "SUPPORT" and path.name.startswith("02_01_")
        )
    ]
    report = next(document.path for document in documents if document.role == "REPORT")
    documents.extend([InputDocument(report, "TECHNICAL"), InputDocument(report, "ORACLE")])
    root = tmp_path / "runs"
    result = run_and_save(
        root,
        documents,
        Scope(currency="RUB", amount_basis="GROSS", reporting_period="2026-01..2026-05"),
        extractor=PackageExtractor(profile, ocr=engine, preview_dir=tmp_path),
        matcher=CaseMatcher(profile),
    )
    stored = load_run(root, result.run_id)
    assert stored.analysis == result == replay_run(root, result.run_id)
    return stored, profile


def test_reconciles_organizer_case_with_real_ocr(tmp_path: Path) -> None:
    stored, profile = _run_organizer_case(tmp_path)
    loaded = stored.analysis
    context = loaded.matching_context
    assert isinstance(context, CaseMatching) and context.profile == profile
    assert context.outcome == CaseMatcher(profile).match(stored.extraction)
    assert len(stored.inputs) == 23
    files = {file.file_id: file for file in loaded.files}
    ocr_pages = stored.extraction.ocr_provenance
    assert {files[file_id].name[:2] for file_id in ocr_pages} == {"01", "04", "08", "12", "15"}
    expected_provenance = OcrProvenance.model_validate_json(_MANIFEST.read_bytes())
    assert all(pages == {1: expected_provenance} for pages in ocr_pages.values())
    assert len(files) == 23
    assert all(file.role in {"REPORT", "SUPPORT"} and file.status != "FAILED" for file in files.values())
    assert loaded.decision == "DISCREPANCIES_FOUND"
    assert loaded.coverage.report_rows == 5
    assert loaded.coverage.checked_rows == 4
    checks = {check.check_id: check for check in loaded.checks}
    sources = {source.source_id: source for source in loaded.sources}
    support_prefixes: set[str] = set()
    for check_id, actual, expected, delta, prefixes in (
        ("ARTICLE_AMOUNT:0200", "5558474.83", "5558474.83", "0.00", ["02"] * 5),
        ("ARTICLE_AMOUNT:0300", "1671359.96", "1671359.96", "0.00", ["03"] * 5),
        ("ARTICLE_AMOUNT:0800", "1145798.10", "1133298.10", "12500.00", ["04", "05", "06", "07"]),
        ("ARTICLE_AMOUNT:0900", "3204744.51", "3204744.51", "0.00", ["08", "09", "11", "12", "13"]),
        ("SUPPORT_TOTAL:OVERALL", "11580377.40", "11567877.40", "12500.00",
         ["02"] * 5 + ["03"] * 5 + ["04", "05", "06", "07", "08", "09", "11", "12", "13"]),
    ):
        check = checks[check_id]
        assert check.state == ("FAIL" if delta == "12500.00" else "PASS")
        assert check.calculation is not None
        assert Decimal(check.calculation.actual) == Decimal(actual)
        assert Decimal(check.calculation.expected) == Decimal(expected)
        assert Decimal(check.calculation.delta) == Decimal(delta)
        support_ids = next(operand.source_ids for operand in check.operands if operand.name == "supported")
        assert sorted(files[sources[item].file_id].name[:2] for item in support_ids) == prefixes
        support_prefixes.update(prefixes)
    assert support_prefixes.isdisjoint({"10", "14", "15"})
    (internal,) = [check for check in loaded.checks if check.check_id.startswith("REPORT_TOTAL:")]
    assert internal.state == "PASS"
    assert internal.calculation is not None and Decimal(internal.calculation.delta) == 0
    assert {check.check_id for check in loaded.checks if check.state == "FAIL"} == {
        "ARTICLE_AMOUNT:0800", "SUPPORT_TOTAL:OVERALL",
    }
    unverified = [check for check in loaded.checks if check.reason_code == "BREAKDOWN_SUPPORT_MISSING"]
    assert len(unverified) == 9 and all(check.state == "NOT_RUN" for check in unverified)
    assert {(files[item.file_id].name[:2], item.reason) for item in loaded.excluded_documents} == {
        ("10", "DUPLICATE"), ("14", "OTHER_PERIOD"), ("15", "OTHER_CONTRACT"),
    }
    assert not any(issue.code == "REPORT_SCOPE_UNVERIFIED" for issue in loaded.issues)
    for prefix, expected_fields in {
        "01": {"contract": "24/03/15-01", "printed_period": "2025-08..2026-05"},
        "04": {"contract": "24/03/15-01", "date": "2026-01-28", "amount": "245000.00"},
        "08": {"contract": "24/03/15-01", "date": "2026-01-31", "amount": "620000.00"},
        "12": {"contract": "24/03/15-01", "date": "2026-04-29", "amount": "650000.00"},
        "15": {"contract": "24/03/16-02", "date": "2026-05-15", "amount": "98000.00"},
    }.items():
        file_id = next(file.file_id for file in loaded.files if file.name.startswith(prefix))
        for field, expected in expected_fields.items():
            source = sources[f"S-{file_id}-{field}"]
            assert source.value == expected
            assert source.raw_text and source.confidence is not None
            assert source.location.kind == "OCR"
            assert source.location.page == 1 and source.location.bbox is not None


def test_missing_payroll_preserves_independent_discrepancy(tmp_path: Path) -> None:
    stored, _ = _run_organizer_case(tmp_path, omit_january_payroll=True)
    result = stored.analysis
    assert len(stored.inputs) == 22
    assert result.execution_status == "PARTIAL"
    assert result.decision == "DISCREPANCIES_FOUND"
    checks = {check.check_id: check for check in result.checks}
    for check_id, reason in (
        ("ARTICLE_AMOUNT:0200", "SUPPORT_MONTH_MISSING"),
        ("SUPPORT_TOTAL:OVERALL", "OVERALL_SUPPORT_MISSING"),
    ):
        check = checks[check_id]
        assert check.state == "NOT_RUN"
        assert check.reason_code == reason
        assert check.calculation is None
    assert any(
        issue.code == "SUPPORT_MONTH_MISSING" and issue.detail == "0200: 2026-01"
        for issue in result.issues
    )
    discrepancy = checks["ARTICLE_AMOUNT:0800"]
    assert discrepancy.state == "FAIL"
    assert discrepancy.calculation is not None
    assert Decimal(discrepancy.calculation.delta) == Decimal("12500.00")
    assert checks["ARTICLE_AMOUNT:0300"].state == checks["ARTICLE_AMOUNT:0900"].state == "PASS"
