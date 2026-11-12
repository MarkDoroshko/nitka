import os
import shutil
from collections import Counter
from pathlib import Path
from typing import assert_type

import pytest
from openpyxl import Workbook

from nitka.analysis.aggregation import CaseAggregator
from nitka.analysis.matching import CaseMatcher
from nitka.contracts.extraction import (
    CellLocation,
    DocxLocation,
    ExtractionResult,
    FileRole,
    OcrLocation,
)
from nitka.contracts.profile import CaseProfile
from nitka.contracts.validation import validate_extraction
from nitka.extraction import package
from nitka.extraction.kfz import KfzCell, KfzFragment, KfzResult, KfzRow
from nitka.extraction.limits import PackageLimitError, PackageLimits
from nitka.extraction.package import PackageExtractor
from nitka.extraction.payroll import PayrollResult, extract_payroll
from nitka.extraction.ports import Extractor, InputDocument
from nitka.extraction.registry import InputRegistry
from nitka.extraction.support_documents import SupportDocument, SupportPageLocation
from nitka.ocr import OcrBlock, OcrEngine, OcrError, OcrPage


class _UnavailableOcr:
    def recognize(self, image_path: Path) -> OcrPage:
        raise OcrError("OCR_INPUT_INVALID", "Test image is unreadable")


def _extractor(root: Path, limits: PackageLimits = PackageLimits()) -> PackageExtractor:
    return PackageExtractor(
        _case_profile(),
        ocr=_UnavailableOcr(),
        preview_dir=root,
        limits=limits,
    )


def _case_profile() -> CaseProfile:
    path = Path(__file__).resolve().parents[1] / "fixtures" / "case_contract.json"
    return CaseProfile.model_validate_json(path.read_text(encoding="utf-8"))


def _payroll(
    path: Path,
    base: int = 40,
    *,
    project_and_period: str = "51/44-07 | Период: Ноябрь 2029",
) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Ведомость"
    for cell, value in {
        "A2": "Документ: НОВ-11",
        "A3": f"Проект: {project_and_period}",
        "A5": "Код статьи",
        "C5": "Сумма, руб.",
        "A6": "0201",
        "A7": "0202",
        "C6": base,
        "C7": 2,
        "C8": base + 2,
    }.items():
        sheet[cell] = value
    workbook.save(path)
    workbook.close()
    return path


def test_shared_profile_drives_article_matching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "report.pdf"
    report.write_bytes(b"synthetic report")
    payroll = _payroll(
        tmp_path / "payroll.xlsx",
        project_and_period="24/03/15-01 | Период: Январь 2026",
    )

    def fragment(text: str) -> KfzFragment:
        return KfzFragment(
            1, OcrBlock(text=text, bbox=(10, 10, 30, 30), confidence=0.99)
        )

    profile = _case_profile()
    cells = {
        "0200": KfzCell("42.00", (fragment("42,00"),), (fragment("Статья 0200"),)),
        "total": KfzCell("42.00", (fragment("42,00"),), (fragment("Итого"),)),
    }
    native = KfzResult(
        KfzRow(
            profile.target_stage, profile.target_year, (fragment("3 этап 2026"),), cells
        ),
        {},
        (),
    )
    monkeypatch.setattr(PackageExtractor, "_read_kfz", lambda *_: native)
    result = _extractor(tmp_path / "previews")(
        [
            InputDocument(report, "REPORT"),
            InputDocument(payroll, "SUPPORT"),
        ]
    )

    outcome = CaseMatcher(profile).match(result)
    assert len(outcome.matches) == 1 and outcome.matches[0].state == "ACCEPTED"
    assert len(outcome.matches[0].support_row_ids) == 1
    totals = CaseAggregator(result).totals()
    assert totals.report["0200"].amount == "42.00"
    assert totals.support["0200"].amount == "42"
    assert totals.report_total.amount == "42.00"


def test_port_uses_stable_content_ids_and_explicit_roles(tmp_path: Path) -> None:
    first = _payroll(tmp_path / "one.xlsx")
    copy = Path(shutil.copyfile(first, tmp_path / "two.xlsx"))
    second = _payroll(tmp_path / "00_actual_support.xlsx", base=50)
    reference = _payroll(tmp_path / "legitimate-name.xlsx", base=90)
    extractor = _extractor(tmp_path / "previews")
    port: Extractor = extractor
    assert_type(port([InputDocument(first, "SUPPORT")]), ExtractionResult)

    before = extractor.extract_package(
        [
            InputDocument(first, "SUPPORT"),
            InputDocument(copy, "SUPPORT"),
            InputDocument(second, "SUPPORT"),
            InputDocument(reference, "ORACLE"),
        ]
    )
    renamed = [
        path.rename(tmp_path / f"renamed-{index}.xlsx")
        for index, path in enumerate((reference, second, copy, first))
    ]
    after = extractor.extract_package(
        [
            InputDocument(renamed[0], "ORACLE"),
            *(InputDocument(path, "SUPPORT") for path in renamed[1:]),
        ]
    )

    assert before.extraction.rows == after.extraction.rows
    assert before.extraction.sources == after.extraction.sources
    assert before.extraction.issues == after.extraction.issues
    assert [issue.code for issue in after.extraction.issues] == ["DUPLICATE_INPUT"]
    assert len(after.manifest.files) == 3
    assert after.manifest.files[0].name != before.manifest.files[0].name
    assert {item.file_id: item.sha256 for item in before.manifest.files} == {
        item.file_id: item.sha256 for item in after.manifest.files
    }
    assert str(tmp_path) not in after.manifest.model_dump_json()
    by_id = {source.source_id: source for source in after.extraction.sources}
    item_rows = [row for row in after.extraction.rows if row.kind == "ITEM"]
    assert Counter(by_id[row.amount_source_id].value for row in item_rows) == {
        "42": 2,
        "52": 1,
    }
    for row in item_rows:
        code = by_id[row.fields["article_code"]]
        assert code.value == "0200"
        for component, cell in (("0201", "A6"), ("0202", "A7")):
            printed = by_id[row.fields[f"component_code_{component}"]]
            assert printed.value == component
            assert printed.location == CellLocation(
                kind="CELL", sheet="Ведомость", cell=cell
            )
    validate_extraction(after.extraction)


def test_malformed_and_missing_inputs_preserve_successful_siblings(
    tmp_path: Path,
) -> None:
    good = _payroll(tmp_path / "payroll.xlsx")
    encrypted = tmp_path / "encrypted.xlsx"
    data = bytearray(good.read_bytes())
    member = data.index(b"[Content_Types].xml", data.index(b"PK\x01\x02"))
    header = data.rindex(b"PK\x01\x02", 0, member)
    data[header + 8] |= 1
    encrypted.write_bytes(data)
    corrupt_image = tmp_path / "corrupt.png"
    corrupt_image.write_bytes(b"unreadable image")
    unknown = tmp_path / "extra.bin"
    unknown.write_bytes(b"other format")

    result = _extractor(tmp_path / "previews")(
        [
            InputDocument(path, "SUPPORT")
            for path in (
                good,
                encrypted,
                corrupt_image,
                unknown,
                tmp_path / "missing.docx",
            )
        ]
    )
    assert {file.name: file.status for file in result.files} == {
        good.name: "COMPLETED",
        encrypted.name: "FAILED",
        corrupt_image.name: "FAILED",
        unknown.name: "FAILED",
    }
    assert {issue.code for issue in result.issues} == {
        "INPUT_UNREADABLE",
        "PAYROLL_EXTRACTION",
        "OCR_INPUT_INVALID",
        "UNSUPPORTED_INPUT",
    }
    assert all(str(tmp_path) not in issue.detail for issue in result.issues)
    assert len(result.rows) == 2
    validate_extraction(result)


def test_changed_registered_bytes_cannot_supply_evidence(tmp_path: Path) -> None:
    path = _payroll(tmp_path / "payroll.xlsx")
    registry, errors = InputRegistry.register_package([InputDocument(path, "SUPPORT")])
    assert not errors
    _payroll(path, base=50)

    result = _extractor(tmp_path / "previews").extract_registered(registry)
    assert result.extraction.files[0].status == "FAILED"
    assert result.extraction.issues[0].code == "INPUT_CHANGED"
    assert not result.extraction.sources and not result.extraction.rows
    assert not result.evidence


def test_adapters_read_verified_bytes_when_original_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _payroll(tmp_path / "payroll.xlsx")
    initial_bytes = original.read_bytes()

    def replace_original_and_read(path: Path) -> PayrollResult:
        _payroll(original, base=50)
        try:
            return extract_payroll(path)
        finally:
            original.write_bytes(initial_bytes)

    monkeypatch.setattr(package, "extract_payroll", replace_original_and_read)
    result = _extractor(tmp_path / "previews").extract_package(
        [
            InputDocument(original, "SUPPORT"),
        ]
    )
    document = next(iter(result.evidence.values()))
    assert isinstance(document, PayrollResult)
    assert document.amounts["0201"].value == "40"
    assert result.extraction.files[0].status == "COMPLETED"
    assert result.preview_dir is not None and not list(result.preview_dir.iterdir())


def test_duplicate_occurrences_keep_their_routing_when_renamed(tmp_path: Path) -> None:
    good = _payroll(tmp_path / "z.xlsx")
    unknown = Path(shutil.copyfile(good, tmp_path / "a.bin"))
    extractor = _extractor(tmp_path / "previews")
    before = extractor(
        [InputDocument(good, "SUPPORT"), InputDocument(unknown, "SUPPORT")]
    )
    after = extractor(
        [
            InputDocument(unknown.rename(tmp_path / "z.bin"), "SUPPORT"),
            InputDocument(good.rename(tmp_path / "a.xlsx"), "SUPPORT"),
        ]
    )
    assert [(file.file_id, file.status) for file in before.files] == [
        (file.file_id, file.status) for file in after.files
    ]
    assert before.rows == after.rows


def test_empty_or_technical_only_package_has_no_inputs(tmp_path: Path) -> None:
    result = _extractor(tmp_path / "previews").extract_package(
        [
            InputDocument(tmp_path / "missing.xlsx", "TECHNICAL"),
        ]
    )
    assert not result.manifest.files
    assert not result.extraction.issues
    assert not (tmp_path / "previews").exists()


def test_package_limits_are_applied_to_working_inputs(tmp_path: Path) -> None:
    technical = tmp_path / "instructions.docx"
    technical.write_bytes(b"X" * 100)
    scan = tmp_path / "scan.png"
    scan.write_bytes(b"Y" * 10)
    preview_dir = tmp_path / "previews"
    extractor = _extractor(preview_dir, PackageLimits(max_total_bytes=9))
    with pytest.raises(PackageLimitError, match="package size"):
        extractor(
            [InputDocument(technical, "TECHNICAL"), InputDocument(scan, "SUPPORT")]
        )
    assert not preview_dir.exists()

    extractor = _extractor(
        preview_dir, PackageLimits(max_total_bytes=10, max_support_pages=1)
    )
    assert (
        len(
            extractor(
                [
                    InputDocument(technical, "TECHNICAL"),
                    InputDocument(scan, "SUPPORT"),
                ]
            ).files
        )
        == 1
    )
    with pytest.raises(PackageLimitError, match="support page count"):
        _extractor(preview_dir, PackageLimits(max_support_pages=1))(
            [
                InputDocument(scan, "SUPPORT"),
                InputDocument(scan, "SUPPORT"),
            ]
        )


def test_local_case_with_real_ocr(tmp_path: Path) -> None:
    case_dir = os.environ.get("NITKA_PACKAGE_SMOKE_DIR")
    if case_dir is None:
        pytest.skip(
            "Set NITKA_PACKAGE_SMOKE_DIR with report/, support/ and technical/ inputs"
        )
    model_manifest = (
        Path(__file__).resolve().parents[2] / "config" / "ocr" / "ppocr-v5.json"
    )
    engine = OcrEngine(Path(os.environ["NITKA_OCR_MODELS"]), model_manifest)
    profile = _case_profile()
    extractor = PackageExtractor(profile, ocr=engine, preview_dir=tmp_path)
    roles: tuple[FileRole, ...] = ("REPORT", "SUPPORT", "TECHNICAL")
    documents = [
        InputDocument(path, role)
        for role in roles
        for path in (Path(case_dir) / role.lower()).iterdir()
    ]

    original = extractor.extract_package(documents)
    assert len(original.manifest.files) == len(original.extraction.files) == 23
    assert all(file.status != "FAILED" for file in original.extraction.files)
    assert Counter(type(item) for item in original.evidence.values()) == {
        KfzResult: 1,
        PayrollResult: 10,
        SupportDocument: 12,
    }
    kfz = next(
        item for item in original.evidence.values() if isinstance(item, KfzResult)
    )
    assert kfz.row is not None and kfz.row.cells["total"].value == "11580377.40"
    assert kfz.row.cells["total"].fragments[0].block.bbox
    assert kfz.provenance[0].model_sha256
    sources = original.extraction.sources
    assert any(
        source.method == "OCR"
        and isinstance(source.location, OcrLocation)
        and source.location.bbox
        and source.confidence is not None
        for source in sources
    )
    assert any(
        source.method == "DOCX"
        and isinstance(source.location, DocxLocation)
        and source.location.table
        for source in sources
    )
    totals = CaseAggregator(original.extraction).totals()
    assert set(totals.report) == set(profile.article_codes)
    assert totals.report_total.amount == "11580377.40"
    matches = CaseMatcher(profile).match(original.extraction)
    assert any(
        match.state == "ACCEPTED" and len(match.support_row_ids) == 5
        for match in matches.matches
    )
    support = [
        item for item in original.evidence.values() if isinstance(item, SupportDocument)
    ]
    assert {"270000.00", "703244.51", "650000.00"} <= {
        item.fields["amount"].value for item in support
    }
    assert any(
        isinstance(fragment.location, SupportPageLocation)
        and fragment.location.bbox
        and fragment.location.image_path is not None
        and fragment.location.image_path.is_file()
        for item in support
        for fragment in item.fragments
    )

    renamed: list[InputDocument] = []
    for index, document in enumerate(reversed(documents)):
        path = tmp_path / f"input-{index}{document.path.suffix}"
        shutil.copyfile(document.path, path)
        renamed.append(InputDocument(path, document.role))
    repeated = extractor(renamed)
    assert repeated.rows == original.extraction.rows
    assert repeated.sources == original.extraction.sources
    assert repeated.issues == original.extraction.issues
