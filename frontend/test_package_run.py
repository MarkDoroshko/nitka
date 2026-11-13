from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from pypdf import PdfWriter

from frontend.nitka_ui import package_run
from frontend.nitka_ui.upload_state import RunAction
from nitka.extraction.limits import PackageLimitError
from nitka.ocr import OcrPage
from nitka.storage import load_run, replay_run


@dataclass
class Upload:
    name: str
    data: bytes
    size: int = 1  # Deliberately untrusted client metadata.
    type: str = "application/octet-stream"

    def getvalue(self) -> bytes:
        return self.data


class UnusedOcr:
    def recognize(self, image_path: Path) -> OcrPage:
        pytest.fail(f"Corrupt PDF must fail before OCR: {image_path}")


def test_stages_every_occurrence_under_a_generated_path(tmp_path: Path) -> None:
    action: RunAction = {
        "action": "run",
        "report": Upload("../../КФЗ.PDF", b"report"),
        "documents": [
            Upload(r"..\same.DOCX", b"same"),
            Upload("same.DOCX", b"same"),
            Upload("payroll.xlsx", b"workbook"),
            Upload("scan.jpeg", b"image"),
        ],
    }
    documents = package_run._stage_uploads(action, tmp_path)
    assert [document.role for document in documents] == ["REPORT"] + ["SUPPORT"] * 4
    assert [document.name for document in documents] == [
        "КФЗ.PDF",
        "same.DOCX",
        "same.DOCX",
        "payroll.xlsx",
        "scan.jpeg",
    ]
    assert [document.path.name for document in documents] == [
        "1.pdf",
        "2.docx",
        "3.docx",
        "4.xlsx",
        "5.jpeg",
    ]
    assert all(document.path.parent == tmp_path for document in documents)
    assert documents[1].path.read_bytes() == documents[2].path.read_bytes() == b"same"


@pytest.mark.parametrize(
    ("report", "documents", "message"),
    [
        (Upload("report.xlsx", b"report"), [Upload("act.pdf", b"act")], "Формат файла"),
        (Upload("report.pdf", b"report"), [Upload("act.csv", b"act")], "Формат файла"),
        (Upload("report.pdf", b""), [Upload("act.pdf", b"act")], "пуст"),
        (Upload("report.pdf", b"report"), [Upload("act.pdf", b"")], "пуст"),
        (Upload("report.pdf", b"report"), [], "первичный документ"),
        (Upload("report.pdf", b"report"), [Upload("act.pdf", b"longer")], "25 МБ"),
    ],
)
def test_rejects_invalid_uploads_and_cleans_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report: Upload,
    documents: list[Upload],
    message: str,
) -> None:
    monkeypatch.setattr(package_run, "MAX_PACKAGE_BYTES", 10)
    action: RunAction = {
        "action": "run",
        "report": report,
        "documents": list(documents),
    }
    with pytest.raises(package_run.PackageUploadError, match=message):
        package_run.run_uploaded_package(
            action,
            package_run.load_profile(),
            tmp_path,
            ocr=UnusedOcr(),
            show_stage=lambda _: None,
        )
    assert list(tmp_path.iterdir()) == []


def test_broken_package_is_saved_as_failed_result(tmp_path: Path) -> None:
    action: RunAction = {
        "action": "run",
        "report": Upload("КФЗ.pdf", b"bad PDF"),
        "documents": [Upload("акт.docx", b"bad Word")],
    }
    profile = package_run.load_profile()
    stages: list[str] = []
    snapshot = package_run.run_uploaded_package(
        action,
        profile,
        tmp_path,
        ocr=UnusedOcr(),
        show_stage=stages.append,
    )
    result = snapshot.analysis
    assert len(stages) == 2
    assert result.execution_status == "FAILED"
    assert result.decision == "REVIEW_REQUIRED"
    assert {file.name: file.status for file in result.files} == {
        "КФЗ.pdf": "FAILED",
        "акт.docx": "FAILED",
    }
    assert any(issue.code == "REPORT_SCOPE_UNVERIFIED" for issue in result.issues)
    assert list(tmp_path.iterdir()) == [tmp_path / result.run_id]
    stored = load_run(tmp_path, result.run_id)
    assert {item.name for item in stored.manifest.files} == {"КФЗ.pdf", "акт.docx"}
    assert stored.analysis == result == replay_run(tmp_path, result.run_id)


def test_support_page_limit_survives_storage_boundary(tmp_path: Path) -> None:
    pdf = tmp_path / "support.pdf"
    writer = PdfWriter()
    for _ in range(21):
        writer.add_blank_page(width=100, height=100)
    writer.write(pdf)
    action: RunAction = {
        "action": "run",
        "report": Upload("КФЗ.pdf", b"bad PDF"),
        "documents": [Upload(pdf.name, pdf.read_bytes())],
    }
    root = tmp_path / "runs"
    with pytest.raises(PackageLimitError, match="support page count 21 exceeds 20"):
        package_run.run_uploaded_package(
            action,
            package_run.load_profile(),
            root,
            ocr=UnusedOcr(),
            show_stage=lambda _: None,
        )
    assert list(root.iterdir()) == []


def test_missing_ocr_weights_are_reported_on_the_kfz(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = tmp_path / "report.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(pdf)
    monkeypatch.setenv("NITKA_OCR_MODELS", str(tmp_path / "missing-models"))
    action: RunAction = {
        "action": "run",
        "report": Upload(pdf.name, pdf.read_bytes()),
        "documents": [Upload("act.docx", b"bad Word")],
    }
    result = package_run.run_uploaded_package(
        action,
        package_run.load_profile(),
        tmp_path / "runs",
        ocr=package_run.local_ocr(),
        show_stage=lambda _: None,
    ).analysis
    report = next(file for file in result.files if file.role == "REPORT")
    assert report.status == "FAILED"
    assert result.decision == "REVIEW_REQUIRED"
    assert any(
        issue.file_id == report.file_id and "OCR_WEIGHTS_MISSING" in issue.detail
        for issue in result.issues
    )
    assert all(check.state == "NOT_RUN" for check in result.checks)
