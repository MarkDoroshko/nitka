import os
from decimal import Decimal
from pathlib import Path

import pytest

from nitka.analysis.matching import CaseMatcher
from nitka.contracts.analysis import AnalysisResult, Scope
from nitka.contracts.extraction import FileRole
from nitka.contracts.profile import CaseProfile
from nitka.extraction.package import PackageExtractor
from nitka.extraction.ports import InputDocument
from nitka.ocr import OcrEngine
from nitka.pipeline import run_pipeline


def test_reconciles_organizer_case_with_real_ocr(tmp_path: Path) -> None:
    case_dir = os.environ.get("NITKA_PACKAGE_SMOKE_DIR")
    if case_dir is None:
        pytest.skip("Set NITKA_PACKAGE_SMOKE_DIR for the real organizer case")
    manifest = Path(__file__).resolve().parents[2] / "config" / "ocr" / "ppocr-v5.json"
    profile = CaseProfile.model_validate_json(
        Path(os.environ["NITKA_PACKAGE_SMOKE_PROFILE"]).read_bytes()
    )
    engine = OcrEngine(Path(os.environ["NITKA_OCR_MODELS"]), manifest)
    roles: tuple[FileRole, ...] = ("REPORT", "SUPPORT", "TECHNICAL")
    documents = [
        InputDocument(path, role)
        for role in roles
        for path in sorted((Path(case_dir) / role.lower()).iterdir())
    ]
    result = run_pipeline(
        documents,
        Scope(currency="RUB", amount_basis="GROSS", reporting_period="2026-01..2026-05"),
        extractor=PackageExtractor(profile, ocr=engine, preview_dir=tmp_path),
        matcher=CaseMatcher(profile),
    )
    loaded = AnalysisResult.model_validate_json(result.model_dump_json())
    files = {file.file_id: file for file in loaded.files}
    assert len(files) == 23
    assert loaded.decision == "DISCREPANCIES_FOUND"
    assert loaded.coverage.report_rows == 5
    assert loaded.coverage.checked_rows == 4
    checks = {check.check_id: check for check in loaded.checks}
    for code in ("0200", "0300", "0900"):
        assert checks[f"ARTICLE_AMOUNT:{code}"].state == "PASS"
    for check_id, actual, expected in (
        ("ARTICLE_AMOUNT:0800", "1145798.10", "1133298.10"),
        ("SUPPORT_TOTAL:OVERALL", "11580377.40", "11567877.40"),
    ):
        check = checks[check_id]
        assert check.state == "FAIL" and check.calculation is not None
        assert Decimal(check.calculation.actual) == Decimal(actual)
        assert Decimal(check.calculation.expected) == Decimal(expected)
        assert Decimal(check.calculation.delta) == Decimal("12500.00")
    assert {check.check_id for check in loaded.checks if check.state == "FAIL"} == {
        "ARTICLE_AMOUNT:0800", "SUPPORT_TOTAL:OVERALL",
    }
    assert any(check.reason_code == "BREAKDOWN_SUPPORT_MISSING" for check in loaded.checks)
    assert {(files[item.file_id].name[:2], item.reason) for item in loaded.excluded_documents} == {
        ("10", "DUPLICATE"), ("14", "OTHER_PERIOD"), ("15", "OTHER_CONTRACT"),
    }
