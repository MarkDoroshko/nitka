from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfWriter

from frontend.nitka_ui.display import format_delta
from frontend.nitka_ui.package_evidence import page_preview
from nitka.contracts.extraction import OcrLocation, PageLocation


def test_nonzero_subkopeck_delta_is_visible_in_the_report() -> None:
    assert format_delta("-0.000001") == "-0,000001\u00a0₽"
    assert format_delta("12500.000000") == "+12\u00a0500,00\u00a0₽"


def test_preview_renders_the_selected_pdf_page_at_extraction_scale(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "document.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=36, height=36)
    writer.add_blank_page(width=72, height=36)
    writer.write(pdf)
    page, highlighted = page_preview(pdf, PageLocation(kind="PAGE", page=2))
    assert not highlighted
    with Image.open(BytesIO(page)) as image:
        assert image.size == (150, 75)
    page, highlighted = page_preview(
        pdf, OcrLocation(kind="OCR", page=2, bbox=(15, 20, 40, 35))
    )
    assert highlighted
    with Image.open(BytesIO(page)) as image:
        assert image.size == (200, 100)
        assert image.getpixel((15, 20)) == (197, 40, 32)
        assert image.getpixel((0, 0)) == (255, 255, 255)
    with pytest.raises(ValueError, match="PDF"):
        page_preview(pdf, PageLocation(kind="PAGE", page=3))


@pytest.mark.parametrize("bbox", [None, (1, 2, 1000, 30), (float("nan"), 1, 20, 30)])
def test_unusable_ocr_box_leaves_original_page_available(
    tmp_path: Path,
    bbox: tuple[float, float, float, float] | None,
) -> None:
    path = tmp_path / "scan.png"
    with Image.new("RGB", (40, 50), (24, 50, 90)) as image:
        image.save(path)
    preview, highlighted = page_preview(
        path, OcrLocation(kind="OCR", page=1, bbox=bbox)
    )
    assert not highlighted
    with Image.open(BytesIO(preview)) as image:
        assert image.size == (40, 50)
        assert image.getpixel((1, 2)) == (24, 50, 90)
