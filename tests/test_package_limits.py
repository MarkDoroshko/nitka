from collections.abc import Sequence
from pathlib import Path

import pytest

from nitka.contracts.analysis import Match, Scope
from nitka.contracts.extraction import ExtractionResult
from nitka.extraction.limits import PackageLimitError, PackageLimits
from nitka.extraction.ports import InputDocument
from nitka.storage import run_and_save

SCOPE = Scope(currency="RUB", amount_basis="GROSS", reporting_period="2026-10")


def test_oversize_package_is_rejected_before_extraction(tmp_path: Path) -> None:
    report = tmp_path / "report.xlsx"
    report.write_bytes(b"x" * 100)
    called = False

    def extractor(documents: Sequence[InputDocument]) -> ExtractionResult:
        nonlocal called
        called = True
        return ExtractionResult(
            extraction_version="never",
            files=[],
            sources=[],
            rows=[],
        )

    def matcher(extraction: ExtractionResult) -> list[Match]:
        return []

    with pytest.raises(PackageLimitError, match="package size"):
        run_and_save(
            tmp_path / "runs",
            [InputDocument(report, "REPORT")],
            SCOPE,
            extractor=extractor,
            matcher=matcher,
            run_id="oversize",
            limits=PackageLimits(max_total_bytes=10),
        )

    assert called is False
    assert not (tmp_path / "runs" / "oversize").exists()
