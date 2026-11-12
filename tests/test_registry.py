import hashlib
from pathlib import Path
from typing import Literal

import pytest

from nitka.contracts.extraction import (
    CellLocation,
    ExtractedRow,
    ExtractionResult,
    PageLocation,
    Source,
)
from nitka.contracts.inputs import InputManifest
from nitka.contracts.validation import validate_extraction
from nitka.extraction.ports import InputDocument
from nitka.extraction.registry import InputReadError, InputRegistry


def _doc(path: Path, role: Literal["REPORT", "SUPPORT"] = "REPORT") -> InputDocument:
    return InputDocument(path=path, role=role)


def test_registers_real_digest_size_name_and_role(tmp_path: Path) -> None:
    report = tmp_path / "report.xlsx"
    report.write_bytes(b"report-bytes")
    act = tmp_path / "act.pdf"
    act.write_bytes(b"act-bytes")

    registry = InputRegistry.register(
        [_doc(report, "REPORT"), _doc(act, "SUPPORT")]
    )

    report_file, act_file = registry.manifest.files
    assert report_file.file_id == "F-1"
    assert report_file.name == "report.xlsx"
    assert report_file.role == "REPORT"
    assert report_file.size_bytes == len(b"report-bytes")
    assert report_file.sha256 == hashlib.sha256(b"report-bytes").hexdigest()
    assert act_file.file_id == "F-2"
    assert act_file.role == "SUPPORT"
    assert registry.path_for("F-1") == report.resolve()
    assert registry.path_for("F-2") == act.resolve()

    registered = registry.inputs[0]
    assert registered.name == "report.xlsx"
    assert registered.role == "REPORT"
    assert registered.size_bytes == len(b"report-bytes")
    assert registered.sha256 == report_file.sha256


def test_one_changed_byte_changes_the_hash(tmp_path: Path) -> None:
    first = tmp_path / "first.bin"
    first.write_bytes(b"same-prefix-A")
    second = tmp_path / "second.bin"
    second.write_bytes(b"same-prefix-B")

    registry = InputRegistry.register([_doc(first), _doc(second)])
    left, right = registry.manifest.files
    assert left.sha256 != right.sha256


def test_same_bytes_under_different_names_share_a_hash(tmp_path: Path) -> None:
    original = tmp_path / "original.pdf"
    original.write_bytes(b"identical-content")
    copy = tmp_path / "copy.pdf"
    copy.write_bytes(b"identical-content")

    registry = InputRegistry.register([_doc(original), _doc(copy)])

    left, right = registry.manifest.files
    assert left.file_id != right.file_id
    assert left.sha256 == right.sha256
    assert registry.duplicate_groups() == [["F-1", "F-2"]]


def test_same_name_from_different_directories_gets_distinct_ids(tmp_path: Path) -> None:
    first = tmp_path / "one" / "same.pdf"
    first.parent.mkdir()
    first.write_bytes(b"first")
    second = tmp_path / "two" / "same.pdf"
    second.parent.mkdir()
    second.write_bytes(b"second")

    registry = InputRegistry.register([_doc(first), _doc(second)])

    left, right = registry.manifest.files
    assert left.name == right.name == "same.pdf"
    assert left.file_id != right.file_id
    assert left.sha256 != right.sha256


def test_missing_file_raises_a_filename_aware_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pdf"

    with pytest.raises(InputReadError, match="missing.pdf") as excinfo:
        InputRegistry.register([_doc(missing)])

    assert excinfo.value.filename == "missing.pdf"


def test_directory_input_raises_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(InputReadError, match="Cannot read input file"):
        InputRegistry.register([_doc(tmp_path)])


def test_manifest_is_portable_and_round_trips(tmp_path: Path) -> None:
    report = tmp_path / "report.xlsx"
    report.write_bytes(b"report-bytes")
    registry = InputRegistry.register([_doc(report)])

    payload = registry.manifest.model_dump_json()
    assert str(tmp_path) not in payload
    assert "path" not in registry.manifest.model_dump()

    restored = InputManifest.model_validate_json(payload)
    assert restored == registry.manifest


def test_restore_reattaches_paths_and_verifies_bytes(tmp_path: Path) -> None:
    report = tmp_path / "report.xlsx"
    report.write_bytes(b"report-bytes")
    registry = InputRegistry.register([_doc(report)])
    renamed = tmp_path / "renamed-on-disk.xlsx"
    report.rename(renamed)

    restored = InputRegistry.restore(registry.manifest, {"F-1": renamed})

    assert restored.path_for("F-1") == renamed.resolve()
    assert restored.manifest.files[0].name == "report.xlsx"


def test_restore_rejects_changed_bytes(tmp_path: Path) -> None:
    report = tmp_path / "report.xlsx"
    report.write_bytes(b"report-bytes")
    registry = InputRegistry.register([_doc(report)])
    report.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="does not match the manifest"):
        InputRegistry.restore(registry.manifest, {"F-1": report})


def test_restore_requires_a_path_for_every_file(tmp_path: Path) -> None:
    report = tmp_path / "report.xlsx"
    report.write_bytes(b"report-bytes")
    registry = InputRegistry.register([_doc(report)])

    with pytest.raises(ValueError, match="Missing path for input file F-1"):
        InputRegistry.restore(registry.manifest, {})


def test_duplicate_registry_ids_are_rejected(tmp_path: Path) -> None:
    report = tmp_path / "report.xlsx"
    report.write_bytes(b"report-bytes")
    registry = InputRegistry.register([_doc(report)])

    with pytest.raises(ValueError, match="Duplicate file IDs"):
        InputRegistry([registry.inputs[0], registry.inputs[0]])


def test_file_records_use_the_extraction_status(tmp_path: Path) -> None:
    report = tmp_path / "report.xlsx"
    report.write_bytes(b"report-bytes")
    act = tmp_path / "act.pdf"
    act.write_bytes(b"act-bytes")
    registry = InputRegistry.register([_doc(report), _doc(act, "SUPPORT")])

    records = registry.file_records({"F-1": "COMPLETED", "F-2": "FAILED"})

    assert [record.file_id for record in records] == ["F-1", "F-2"]
    assert [record.status for record in records] == ["COMPLETED", "FAILED"]
    assert records[0].sha256 == registry.inputs[0].sha256
    assert records[1].role == "SUPPORT"


def test_failed_parse_keeps_the_real_hash(tmp_path: Path) -> None:
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"not-a-valid-xlsx")
    registry = InputRegistry.register([_doc(broken)])

    record = registry.inputs[0].to_file_record("FAILED")

    assert record.status == "FAILED"
    assert record.sha256 == hashlib.sha256(b"not-a-valid-xlsx").hexdigest()


def test_unknown_file_id_is_reported(tmp_path: Path) -> None:
    report = tmp_path / "report.xlsx"
    report.write_bytes(b"report-bytes")
    registry = InputRegistry.register([_doc(report)])

    with pytest.raises(KeyError, match="Unknown file ID"):
        registry.path_for("F-404")


def test_file_records_require_a_status_for_every_file(tmp_path: Path) -> None:
    report = tmp_path / "report.xlsx"
    report.write_bytes(b"report-bytes")
    registry = InputRegistry.register([_doc(report)])

    with pytest.raises(ValueError, match="Missing extraction status for F-1"):
        registry.file_records({})


def test_registry_records_can_back_an_extraction(tmp_path: Path) -> None:
    report = tmp_path / "report.xlsx"
    report.write_bytes(b"report-bytes")
    act = tmp_path / "act.pdf"
    act.write_bytes(b"act-bytes")
    registry = InputRegistry.register(
        [_doc(report, "REPORT"), _doc(act, "SUPPORT")]
    )

    extraction = ExtractionResult(
        extraction_version="registry-1",
        files=registry.file_records({"F-1": "COMPLETED", "F-2": "COMPLETED"}),
        sources=[
            Source(
                source_id="S-R",
                file_id="F-1",
                document_id="D-R",
                location=CellLocation(kind="CELL", sheet="Report", cell="D2"),
                method="TABLE",
                raw_text="150 000,00",
                value="150000.00",
                review_status="UNREVIEWED",
            ),
            Source(
                source_id="S-A",
                file_id="F-2",
                document_id="D-A",
                location=PageLocation(kind="PAGE", page=2),
                method="VISION",
                raw_text="120 000,00",
                value="120000.00",
                review_status="UNREVIEWED",
            ),
        ],
        rows=[
            ExtractedRow(row_id="R-1", kind="ITEM", amount_source_id="S-R"),
            ExtractedRow(row_id="A-1", kind="ITEM", amount_source_id="S-A"),
        ],
    )

    validate_extraction(extraction)
    assert {source.file_id for source in extraction.sources} == {"F-1", "F-2"}


def test_empty_registry_has_an_empty_manifest() -> None:
    registry = InputRegistry.register([])
    assert registry.manifest.files == []
    assert registry.duplicate_groups() == []
