from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from nitka.contracts.extraction import PageLocation
from nitka.extraction.pdf import PdfInspector
from nitka.extraction.pdf_preview import PreviewService, rotate_image
from nitka.extraction.pdf_text import (
    VISION_REQUIRED,
    PdfTextExtractor,
    PdfTextPackageExtractor,
)
from nitka.extraction.ports import InputDocument
from nitka.extraction.registry import InputRegistry
from nitka.extraction.xlsx_issues import AMOUNT_MISSING, ROW_KIND_UNKNOWN

FONT = Path("/usr/share/fonts/liberation/LiberationSans-Regular.ttf")
FONT_BOLD = Path("/usr/share/fonts/liberation/LiberationSans-Bold.ttf")


@pytest.fixture(scope="module", autouse=True)
def _fonts() -> None:
    if not FONT.is_file() or not FONT_BOLD.is_file():
        pytest.skip("Liberation fonts are required to build a Cyrillic PDF")
    pdfmetrics.registerFont(TTFont("NitkaText", str(FONT)))
    pdfmetrics.registerFont(TTFont("NitkaTextBold", str(FONT_BOLD)))


def _build_act(path: Path) -> None:
    styles = getSampleStyleSheet()
    title = ParagraphStyle("title", parent=styles["Title"], fontName="NitkaText")
    body = ParagraphStyle("body", parent=styles["Normal"], fontName="NitkaText")
    page_one = [
        ["№", "Наименование", "Кол-во", "Цена", "Сумма"],
        ["1", "Услуги", "1", "150000.00", "150000.00"],
        ["", "Подытог", "", "", "150000.00"],
    ]
    page_two = [
        ["№", "Наименование", "Кол-во", "Цена", "Сумма"],
        ["2", "Материалы", "1", "50000.00", "50000.00"],
        ["", "Итого", "", "", "200000.00"],
        ["", "НДС", "", "", "33333.33"],
        ["", "Всего", "", "", "233333.33"],
    ]

    def table(rows: list[list[str]]) -> Table:
        value = Table(rows, colWidths=[25, 200, 60, 70, 80])
        value.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                    ("FONTNAME", (0, 0), (-1, -1), "NitkaText"),
                    ("FONTNAME", (0, 0), (-1, 0), "NitkaTextBold"),
                ]
            )
        )
        return value

    story = [
        Paragraph("Акт № 14 от 05.10.2026", title),
        Paragraph("Договор № 0012", body),
        Spacer(1, 12),
        table(page_one),
        PageBreak(),
        Paragraph("Продолжение акта № 14", body),
        Spacer(1, 12),
        table(page_two),
    ]
    SimpleDocTemplate(str(path), pagesize=A4).build(story)


def _build_one_page(path: Path, rows: list[list[str]]) -> None:
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["Normal"], fontName="NitkaText")
    table = Table(rows, colWidths=[25, 200, 60, 70, 80])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("FONTNAME", (0, 0), (-1, -1), "NitkaText"),
            ]
        )
    )
    SimpleDocTemplate(str(path), pagesize=A4).build(
        [Paragraph("Акт № 15", body), Spacer(1, 12), table]
    )


def _build_two_tables(path: Path) -> None:
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["Normal"], fontName="NitkaText")

    def table(rows: list[list[str]]) -> Table:
        value = Table(rows, colWidths=[25, 200, 60, 70, 80])
        value.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                    ("FONTNAME", (0, 0), (-1, -1), "NitkaText"),
                ]
            )
        )
        return value

    first = [
        ["№", "Наименование", "Кол-во", "Цена", "Сумма"],
        ["1", "Услуга A", "1", "100.00", "100.00"],
    ]
    second = [["2", "Услуга B", "1", "200.00", "200.00"]]
    SimpleDocTemplate(str(path), pagesize=A4).build(
        [Paragraph("Акт № 15", body), table(first), Spacer(1, 30), table(second)]
    )


def _registry(*documents: tuple[Path, str]) -> InputRegistry:
    return InputRegistry.register(
        [InputDocument(path, role) for path, role in documents]  # type: ignore[arg-type]
    )


def _amount_source(package: object, row: object):  # type: ignore[no-untyped-def]
    extraction = package.extraction  # type: ignore[attr-defined]
    return next(
        source
        for source in extraction.sources
        if source.source_id == row.amount_source_id  # type: ignore[attr-defined]
    )


def test_two_page_act_has_sources_pages_previews_and_total_kinds(
    tmp_path: Path,
) -> None:
    path = tmp_path / "act.pdf"
    _build_act(path)
    package = PdfTextExtractor(
        _registry((path, "SUPPORT")), preview_dir=tmp_path / "previews"
    ).extract()

    assert [file.status for file in package.extraction.files] == ["COMPLETED"]
    assert [row.kind for row in package.extraction.rows] == [
        "ITEM",
        "PAGE_SUBTOTAL",
        "ITEM",
        "DOCUMENT_TOTAL",
        "TAX_SUMMARY",
        "DOCUMENT_TOTAL",
    ]
    assert [row.kind for row in package.extraction.rows].count("ITEM") == 2
    assert not package.vision_requests

    amounts = [_amount_source(package, row) for row in package.extraction.rows]
    assert [source.value for source in amounts] == [
        "150000.00",
        "150000.00",
        "50000.00",
        "200000.00",
        "33333.33",
        "233333.33",
    ]
    assert [source.location for source in amounts] == [
        PageLocation(kind="PAGE", page=1),
        PageLocation(kind="PAGE", page=1),
        PageLocation(kind="PAGE", page=2),
        PageLocation(kind="PAGE", page=2),
        PageLocation(kind="PAGE", page=2),
        PageLocation(kind="PAGE", page=2),
    ]
    assert all(source.document_id == "D-F-1" for source in amounts)
    assert all(source.method == "PDF_TEXT" for source in amounts)

    assert [preview.page for preview in package.previews] == [1, 2]
    assert all(preview.image_path.is_file() for preview in package.previews)


def test_broken_pdf_does_not_block_the_good_neighbour(tmp_path: Path) -> None:
    good = tmp_path / "good.pdf"
    broken = tmp_path / "broken.pdf"
    _build_act(good)
    broken.write_bytes(b"not a PDF at all")
    registry = _registry((broken, "SUPPORT"), (good, "SUPPORT"))

    package = PdfTextExtractor(registry, preview_dir=tmp_path / "previews").extract()

    statuses = {file.file_id: file.status for file in package.extraction.files}
    assert statuses == {"F-1": "FAILED", "F-2": "COMPLETED"}
    assert [row.kind for row in package.extraction.rows].count("ITEM") == 2
    assert any(issue.code == "PDF_UNREADABLE" for issue in package.extraction.issues)
    assert {preview.file_id for preview in package.previews} == {"F-2"}


def test_unknown_table_row_is_preserved_and_diagnosed(tmp_path: Path) -> None:
    path = tmp_path / "unknown.pdf"
    _build_one_page(
        path,
        [
            ["№", "Наименование", "Кол-во", "Цена", "Сумма"],
            ["", "???", "", "", ""],
            ["1", "Услуга", "1", "100.00", "100.00"],
        ],
    )

    package = PdfTextExtractor(
        _registry((path, "SUPPORT")), preview_dir=tmp_path / "previews"
    ).extract()

    assert package.extraction.rows[0].kind == "UNKNOWN"
    assert package.extraction.rows[1].kind == "ITEM"
    assert any(issue.code == ROW_KIND_UNKNOWN for issue in package.extraction.issues)


def test_text_only_pdf_uses_line_fallback(tmp_path: Path) -> None:
    path = tmp_path / "text-only.pdf"
    document = canvas.Canvas(str(path), pagesize=A4)
    document.setFont("NitkaText", 12)
    document.drawString(72, 760, "Услуги по договору 100.00")
    document.drawString(72, 740, "Копия верна 05.10.2026")
    document.drawString(72, 720, "Итого 100.00")
    document.save()

    package = PdfTextExtractor(
        _registry((path, "SUPPORT")), preview_dir=tmp_path / "previews"
    ).extract()

    assert [row.kind for row in package.extraction.rows] == [
        "ITEM",
        "DOCUMENT_TOTAL",
    ]
    amounts = [_amount_source(package, row) for row in package.extraction.rows]
    assert [source.value for source in amounts] == ["100.00", "100.00"]
    assert all(source.location.page == 1 for source in amounts)


def test_label_column_is_not_misclassified_by_tax_words(tmp_path: Path) -> None:
    path = tmp_path / "labels.pdf"
    _build_one_page(
        path,
        [
            ["№", "Наименование", "Кол-во", "Цена", "Сумма"],
            ["1", "Консультация по НДС", "1", "1000.00", "1000.00"],
            ["", "Итого с НДС", "", "", "1000.00"],
            ["", "НДС", "", "", "166.67"],
        ],
    )

    package = PdfTextExtractor(
        _registry((path, "SUPPORT")), preview_dir=tmp_path / "previews"
    ).extract()

    assert [row.kind for row in package.extraction.rows] == [
        "ITEM",
        "DOCUMENT_TOTAL",
        "TAX_SUMMARY",
    ]


def test_empty_amount_cell_is_not_replaced_by_price(tmp_path: Path) -> None:
    path = tmp_path / "missing-amount.pdf"
    _build_one_page(
        path,
        [
            ["№", "Наименование", "Кол-во", "Цена", "Сумма"],
            ["1", "Услуга", "2", "100.00", ""],
        ],
    )

    package = PdfTextExtractor(
        _registry((path, "SUPPORT")), preview_dir=tmp_path / "previews"
    ).extract()

    row = package.extraction.rows[0]
    amount = _amount_source(package, row)
    assert row.kind == "UNKNOWN"
    assert amount.value is None
    assert AMOUNT_MISSING in [issue.code for issue in package.extraction.issues]


def test_watermark_only_page_routes_to_vision(tmp_path: Path) -> None:
    path = tmp_path / "watermark.pdf"
    document = canvas.Canvas(str(path), pagesize=A4)
    document.setFont("NitkaText", 12)
    document.drawString(72, 760, "Сканировано компанией Nitka")
    document.save()

    package = PdfTextExtractor(
        _registry((path, "SUPPORT")), preview_dir=tmp_path / "previews"
    ).extract()

    assert package.extraction.files[0].status == "PARTIAL"
    assert package.vision_requests[0].page == 1
    assert package.vision_requests[0].reason == "NO_POSITIONS"
    assert package.vision_requests[0].image_path is not None


def test_all_tables_on_a_page_are_parsed(tmp_path: Path) -> None:
    path = tmp_path / "two-tables.pdf"
    _build_two_tables(path)

    package = PdfTextExtractor(
        _registry((path, "SUPPORT")), preview_dir=tmp_path / "previews"
    ).extract()

    item_rows = [row for row in package.extraction.rows if row.kind == "ITEM"]
    assert len(item_rows) == 2
    values = [_amount_source(package, row).value for row in item_rows]
    assert values == ["100.00", "200.00"]


def test_blank_text_layer_routes_page_to_vision_with_preview(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    document = canvas.Canvas(str(path), pagesize=A4)
    document.showPage()
    document.save()
    package = PdfTextExtractor(
        _registry((path, "SUPPORT")), preview_dir=tmp_path / "previews"
    ).extract()

    assert package.extraction.files[0].status == "PARTIAL"
    assert [issue.code for issue in package.extraction.issues] == [VISION_REQUIRED]
    assert len(package.vision_requests) == 1
    request = package.vision_requests[0]
    assert request.page == 1
    assert request.image_path is not None and request.image_path.is_file()
    assert package.previews[0].image_path.is_file()


def test_png_image_routes_to_vision_and_has_preview(tmp_path: Path) -> None:
    path = tmp_path / "scan.png"
    Image.new("RGB", (80, 40), "white").save(path)
    package = PdfTextExtractor(
        _registry((path, "SUPPORT")), preview_dir=tmp_path / "previews"
    ).extract()

    assert package.extraction.files[0].status == "PARTIAL"
    assert [issue.code for issue in package.extraction.issues] == [VISION_REQUIRED]
    assert package.vision_requests[0].page == 1
    assert package.vision_requests[0].reason == "IMAGE_SCAN"
    assert package.vision_requests[0].image_path is not None
    assert package.vision_requests[0].image_path.is_file()


def test_preview_orientation_correction(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    Image.new("RGB", (40, 20), "white").save(path)
    rotated = rotate_image(path, 90)
    with Image.open(rotated) as image:
        assert image.size == (20, 40)

    preview = PreviewService(
        PdfInspector(timeout_seconds=10), tmp_path / "previews"
    ).image_preview(InputRegistry.register([InputDocument(path, "SUPPORT")]).inputs[0])
    assert preview is not None
    corrected = PreviewService(
        PdfInspector(timeout_seconds=10), tmp_path / "previews"
    ).rotate_preview(preview, 90)
    assert corrected.rotation == 90
    with Image.open(corrected.image_path) as image:
        assert image.size == (20, 40)


def test_extractor_protocol_adapter_returns_support_extraction(
    tmp_path: Path,
) -> None:
    path = tmp_path / "act.pdf"
    _build_act(path)
    extractor = PdfTextPackageExtractor(preview_dir=tmp_path / "previews")
    result = extractor([InputDocument(path, "SUPPORT")])
    assert result.files[0].status == "COMPLETED"
    assert [row.kind for row in result.rows].count("ITEM") == 2


def test_extractor_protocol_adapter_rejects_a_report_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "act.pdf"
    _build_act(path)
    extractor = PdfTextPackageExtractor(preview_dir=tmp_path / "previews")
    with pytest.raises(ValueError, match="support-stage"):
        extractor(
            [
                InputDocument(path, "SUPPORT"),
                InputDocument(path, "REPORT"),
            ]
        )
