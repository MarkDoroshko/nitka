from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pypdf import PdfWriter

from nitka.extraction.limits import PackageLimitError, PackageLimits
from nitka.extraction.pdf import (
    PDF_ENCRYPTED,
    PDF_TIMEOUT,
    PDF_UNREADABLE,
    PdfInspection,
    PdfInspector,
    PdfSupportExtractor,
)
from nitka.extraction.ports import InputDocument
from nitka.extraction.registry import InputRegistry


def _write_pdf(path: Path, *, pages: int = 2, password: str | None = None) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    if password is not None:
        writer.encrypt(password)
    with path.open("wb") as handle:
        writer.write(handle)


def _registry_files(base: Path) -> tuple[Path, Path, Path]:
    good = base / "good.pdf"
    broken = base / "broken.pdf"
    encrypted = base / "encrypted.pdf"
    _write_pdf(good, pages=2)
    broken.write_bytes(b"this is not a PDF")
    _write_pdf(encrypted, pages=1, password="secret")
    return good, broken, encrypted


def test_inspector_reports_page_count_and_isolates_bad_files(
    tmp_path: Path,
) -> None:
    good, broken, encrypted = _registry_files(tmp_path)
    inspector = PdfInspector(timeout_seconds=5)

    good_result = inspector.inspect(good)
    broken_result = inspector.inspect(broken)
    encrypted_result = inspector.inspect(encrypted)

    assert good_result.ok
    assert good_result.page_count == 2
    assert not broken_result.ok
    assert broken_result.error is not None
    assert encrypted_result.encrypted
    assert not encrypted_result.ok


def test_inspector_times_out_and_kills_the_child(tmp_path: Path) -> None:
    good, _, _ = _registry_files(tmp_path)
    inspector = PdfInspector(
        timeout_seconds=0.2,
        worker_command=(sys.executable, "-c", "import time; time.sleep(5)"),
    )

    result = inspector.inspect(good)

    assert result.timed_out
    assert result.error == PDF_TIMEOUT


def test_pdf_worker_text_round_trips_as_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "cp1251")
    inspector = PdfInspector(
        worker_command=(
            sys.executable,
            "-c",
            "import json; print(json.dumps({'ok': True, 'pages': [{'page': 1,"
            " 'text': 'Сумма к оплате: 10 ₽', 'tables': []}]}, ensure_ascii=False))",
        )
    )

    result = inspector.extract(tmp_path / "имя.pdf")

    assert result.ok
    assert result.pages[0].text == "Сумма к оплате: 10 ₽"


def test_renderer_renders_pages_sequentially(tmp_path: Path) -> None:
    good, _, _ = _registry_files(tmp_path)
    output = tmp_path / "pages"

    result = PdfInspector(timeout_seconds=10).render(good, output)

    assert result.ok
    assert [path.name for path in result.pages] == [
        "page-0001.png",
        "page-0002.png",
    ]
    assert all(path.is_file() and path.stat().st_size > 0 for path in result.pages)


def test_support_extractor_isolates_bad_pdfs_and_keeps_good_one(
    tmp_path: Path,
) -> None:
    good, broken, encrypted = _registry_files(tmp_path)
    registry = InputRegistry.register(
        [
            InputDocument(good, "SUPPORT"),
            InputDocument(broken, "SUPPORT"),
            InputDocument(encrypted, "SUPPORT"),
        ]
    )

    result = PdfSupportExtractor(registry, PackageLimits(max_support_pages=10)).extract()

    statuses = {file.file_id: file.status for file in result.files}
    assert statuses == {"F-1": "COMPLETED", "F-2": "FAILED", "F-3": "FAILED"}
    codes = {issue.file_id: issue.code for issue in result.issues}
    assert codes == {"F-2": PDF_UNREADABLE, "F-3": PDF_ENCRYPTED}


def test_package_page_limit_is_rejected(tmp_path: Path) -> None:
    good, _, _ = _registry_files(tmp_path)
    registry = InputRegistry.register([InputDocument(good, "SUPPORT")])

    with pytest.raises(PackageLimitError, match="page count"):
        PdfSupportExtractor(
            registry, PackageLimits(max_support_pages=1)
        ).extract()


def test_default_page_limit_is_twenty(tmp_path: Path) -> None:
    too_long = tmp_path / "too-long.pdf"
    _write_pdf(too_long, pages=21)
    registry = InputRegistry.register([InputDocument(too_long, "SUPPORT")])

    with pytest.raises(PackageLimitError, match="20"):
        PdfSupportExtractor(registry).extract()


class _HangingInspector:
    def inspect(self, path: Path) -> PdfInspection:
        if path.name == "hang.pdf":
            return PdfInspection(None, error=PDF_TIMEOUT, timed_out=True)
        return PdfInspection(1)


def test_timeout_diagnostic_does_not_stop_the_next_document(
    tmp_path: Path,
) -> None:
    hang = tmp_path / "hang.pdf"
    good = tmp_path / "good.pdf"
    hang.write_bytes(b"not parsed by the stub")
    good.write_bytes(b"not parsed by the stub")
    registry = InputRegistry.register(
        [InputDocument(hang, "SUPPORT"), InputDocument(good, "SUPPORT")]
    )

    result = PdfSupportExtractor(
        registry,
        PackageLimits(max_support_pages=10),
        inspector=_HangingInspector(),
    ).extract()

    assert [file.status for file in result.files] == ["FAILED", "COMPLETED"]
    assert result.issues[0].code == PDF_TIMEOUT
