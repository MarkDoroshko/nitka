import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from openpyxl.writer import excel
from PIL import ImageFont
from pydantic import ValidationError

from examples.generalization_corpus import CASES, REPORTS, SUPPORT, build_corpus
from examples.run_generalization import (
    _EXPECTED,
    Counts,
    Expectations,
    _score,
    evaluate,
)
from nitka.contracts.analysis import (
    Calculation,
    Check,
    Match,
    Operand,
    Scope,
)
from nitka.contracts.extraction import (
    CellLocation,
    ExtractedRow,
    ExtractionResult,
    FileRecord,
    Source,
)
from nitka.pipeline import analyze_extraction

_SCOPE = Scope(currency="RUB", amount_basis="GROSS", reporting_period="2031-03")


def _expectations() -> Expectations:
    return Expectations.model_validate_json(_EXPECTED.read_bytes())


def _file(file_id: str, name: str) -> FileRecord:
    return FileRecord(
        file_id=file_id, name=name, sha256="0" * 64,
        role="REPORT" if file_id == "report" else "SUPPORT", status="COMPLETED",
    )


def _source(file_id: str, field: str, value: str, *, review: bool = False) -> Source:
    return Source(
        source_id=f"S-{file_id}-{field}", file_id=file_id, document_id=f"D-{file_id}",
        location=CellLocation(kind="CELL", cell="A1"), method="TABLE",
        raw_text=value, value=value, review_status="NEEDS_REVIEW" if review else "UNREVIEWED",
    )


def test_empty_extraction_cannot_improve_fixed_denominators() -> None:
    extraction = ExtractionResult(extraction_version="test", files=[], sources=[], rows=[])
    analysis = analyze_extraction(extraction, [], _SCOPE)

    numbered, _ = _score("numbered", _expectations(), extraction, analysis)
    compact, _ = _score("compact", _expectations(), extraction, analysis)

    assert numbered == Counts(0, 21, 0, 0, 5, 5, 5, 5)
    assert compact == Counts(0, 13, 0, 0, 5, 5, 5, 5)


def test_amounts_use_exact_numeric_equality_and_review_status() -> None:
    extraction = ExtractionResult(
        extraction_version="test", files=[_file("report", "numbered.pdf")], rows=[],
        sources=[
            _source("report", "0200", "52 000,250"),
            _source("report", "0300", "14000.13", review=True),
            _source("report", "0800", "48700.51"),
            _source("report", "0900", "NaN"),
        ],
    )
    analysis = analyze_extraction(extraction, [], _SCOPE)
    counts, _ = _score("numbered", _expectations(), extraction, analysis)

    assert counts.exact_amounts == 1
    assert counts.expected_amounts == 21


def test_counts_duplicate_wrong_and_missing_links_even_when_no_rule_completed() -> None:
    report_amount = _source("report", "0800", "48700.50")
    article = _source("report", "article-0800", "0800")
    support_amounts = [
        _source("valid", "amount", "48600.50"),
        _source("valid", "extra", "48600.50"),
        _source("foreign", "amount", "48700.50"),
    ]
    extraction = ExtractionResult(
        extraction_version="test",
        files=[_file("report", "numbered.pdf"), _file("valid", "work-order.pdf"),
               _file("foreign", "foreign.pdf")],
        sources=[report_amount, article, *support_amounts],
        rows=[
            ExtractedRow(
                row_id="report", kind="ITEM", amount_source_id=report_amount.source_id,
                fields={"article_code": article.source_id},
            ),
            *(ExtractedRow(row_id=f"support-{index}", kind="ITEM", amount_source_id=source.source_id)
              for index, source in enumerate(support_amounts)),
        ],
    )
    analysis = analyze_extraction(extraction, [], _SCOPE).model_copy(update={
        "matches": [Match(
            match_id="accepted", report_row_id="report",
            support_row_ids=["support-0", "support-1", "support-2"], state="ACCEPTED",
        )],
    })

    counts, observations = _score("numbered", _expectations(), extraction, analysis)

    assert (counts.accepted_links, counts.wrong_links, counts.missing_links) == (3, 2, 4)
    assert observations["wrong_links"] == [("0800", "work-order.pdf"), ("0800", "foreign.pdf")]
    assert counts.unverified_rows == 5


@pytest.mark.parametrize("printed_total", [False, True])
def test_overall_completion_requires_printed_evidence(printed_total: bool) -> None:
    source = _source("report", "total", "144201.50")
    extraction = ExtractionResult(
        extraction_version="test", files=[_file("report", "numbered.pdf")], rows=[],
        sources=[source] if printed_total else [],
    )
    analysis = analyze_extraction(extraction, [], _SCOPE).model_copy(update={
        "checks": [Check(
            check_id="SUPPORT_TOTAL:OVERALL", target_id="OVERALL", match_id=None,
            state="FAIL", reason_code="OVERALL_REPORT_EXCEEDS_SUPPORT",
            evidence_ids=[],
            calculation=Calculation(
                actual="144201.50", expected="144101.50", delta="100", tolerance="0", unit="RUB",
            ),
            operands=[Operand(
                name="reported", value="144201.50", source_ids=[source.source_id] if printed_total else [],
            )],
        )],
    })

    counts, _ = _score("numbered", _expectations(), extraction, analysis)

    assert counts.unverified_rows == (4 if printed_total else 5)


@pytest.mark.parametrize("missing", ["field", "file", "article"])
def test_expectations_reject_incomplete_inputs(missing: str) -> None:
    data: dict[str, Any] = json.loads(_EXPECTED.read_bytes())
    if missing == "field":
        del data["report_amounts"]["numbered"]["total"]
    elif missing == "file":
        del data["support_amounts"]["subscription.png"]
    else:
        data["links"]["insurance.xlsx"] = "9999"
    with pytest.raises(ValidationError):
        Expectations.model_validate(data)


@pytest.fixture
def cyrillic_font() -> Path:
    try:
        filename = ImageFont.truetype("DejaVuSans.ttf", 12).path
    except OSError:
        pytest.skip("DejaVu Sans is required to generate the Cyrillic corpus")
    assert isinstance(filename, str)
    return Path(filename)


def test_corpus_is_reproducible_across_workbook_save_dates(
    tmp_path: Path, cyrillic_font: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FixedDate:
        year_to_return = 2035

        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return datetime(cls.year_to_return, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(excel, "datetime", SimpleNamespace(
        datetime=FixedDate, timezone=SimpleNamespace(utc=UTC),
    ))
    first, second = tmp_path / "first", tmp_path / "second"
    build_corpus(first, cyrillic_font)
    FixedDate.year_to_return = 2036
    build_corpus(second, cyrillic_font)
    filenames = {*REPORTS.values(), *SUPPORT}

    assert {path.name for path in first.iterdir()} == filenames
    assert {path.name for path in second.iterdir()} == filenames
    assert all((first / name).read_bytes() == (second / name).read_bytes() for name in filenames)


def test_missing_models_produce_incomplete_report(tmp_path: Path, cyrillic_font: Path) -> None:
    models = tmp_path / "models"
    models.mkdir()
    output = tmp_path / "report"
    assert not evaluate(output, cyrillic_font, models)

    result = json.loads((output / "report.json").read_bytes())
    assert not result["complete"]
    assert result["cases"].keys() == CASES.keys()
    assert result["counts"] == {
        "exact_amounts": 14, "expected_amounts": 55,
        "wrong_links": 0, "accepted_links": 0, "missing_links": 15, "expected_links": 15,
        "unverified_rows": 15, "expected_rows": 15,
    }
    for case, layout in CASES.items():
        assert set(result["cases"][case]["missing_ocr"]) == {REPORTS[layout], "subscription.png"}


def test_cpu_ocr_generalization(tmp_path: Path, cyrillic_font: Path) -> None:
    models = os.environ.get("NITKA_OCR_MODELS")
    if not models:
        pytest.skip("Set NITKA_OCR_MODELS to run the evaluation with CPU OCR")
    output = tmp_path / "report"

    assert evaluate(output, cyrillic_font, Path(models))
    cases = json.loads((output / "report.json").read_bytes())["cases"]
    assert all(not case["missing_ocr"] for case in cases.values())
    assert cases["compact"]["counts"]["accepted_links"] == 0
    assert cases["compact"]["rows"]["ARTICLE_AMOUNT:0800"]["check"]["reason_code"] == (
        "REPORT_SCOPE_UNVERIFIED"
    )
    assert cases["numbered"]["rows"]["ARTICLE_AMOUNT:0800"]["check"]["reason_code"] == (
        "SUPPORT_INPUT_UNREADABLE"
    )
    control = cases["numbered-control"]
    assert control["counts"]["wrong_links"] == control["counts"]["unverified_rows"] == 0
    assert control["exclusions"] == [{"file": "foreign.pdf", "reason": "OTHER_CONTRACT"}]
    assert control["rows"]["ARTICLE_AMOUNT:0800"]["check"]["calculation"]["expected"] == "48600.50"
    for check_id in ("ARTICLE_AMOUNT:0800", "SUPPORT_TOTAL:OVERALL"):
        row = control["rows"][check_id]
        assert row["verified"]
        assert row["check"]["state"] == "FAIL"
        assert row["check"]["calculation"]["delta"] == "100.00"
