from collections.abc import Sequence
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZipFile

import pytest
from reportlab.pdfgen import canvas

from nitka.extraction.docx_support import DocxLocation
from nitka.extraction.limits import PackageLimitError, PackageLimits
from nitka.extraction.pdf import (
    PdfContentResult,
    PdfInspection,
    PdfInspector,
    PdfPageContent,
)
from nitka.extraction.ports import InputDocument
from nitka.extraction.registry import InputRegistry
from nitka.extraction.support_documents import (
    SupportDocument,
    SupportDocumentsExtractor,
    SupportPageLocation,
)
from nitka.ocr.engine import OcrBlock, OcrError, OcrPage, OcrProvenance

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_PROVENANCE = OcrProvenance(settings_version="test", model_sha256={})


def _paragraph(parent: Element, *runs: str) -> None:
    p = SubElement(parent, _W + "p")
    for text in runs:
        SubElement(SubElement(p, _W + "r"), _W + "t").text = text


def _docx(
    path: Path,
    heading: str,
    period: str,
    header: str,
    amount: str,
    *,
    additional_items: Sequence[tuple[str, str]] = (),
) -> Path:
    doc = Element(_W + "document")
    body = SubElement(doc, _W + "body")
    _paragraph(body, heading)
    _paragraph(body, period)
    _paragraph(body, "Договор: ", "26/03/03-02")
    _paragraph(body, "Обслуживание оборудования ", "для отдела контроля.")
    table = SubElement(body, _W + "tbl")
    for label, value in [
        (header, "Сумма, руб."),
        ("Контроль качества", amount),
        *additional_items,
        ("Итого НДС", "123,45"),
    ]:
        row = SubElement(table, _W + "tr")
        for text in (label, value):
            _paragraph(SubElement(row, _W + "tc"), text)
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", tostring(doc, encoding="utf-8"))
    return path


def _registry(*paths: Path) -> InputRegistry:
    return InputRegistry.register([InputDocument(path, "SUPPORT") for path in paths])


def _page(*texts: str) -> OcrPage:
    return OcrPage(
        width=400,
        height=400,
        blocks=tuple(
            OcrBlock(
                text=text, bbox=(10, index * 20, 300, index * 20 + 12), confidence=0.98
            )
            for index, text in enumerate(texts)
        ),
        reading_order=tuple(range(len(texts))),
        provenance=_PROVENANCE,
    )


class _Ocr:
    def __init__(self, page: OcrPage | None = None) -> None:
        self.page = page
        self.calls: list[Path] = []

    def recognize(self, image_path: Path) -> OcrPage:
        self.calls.append(image_path)
        if image_path.name == "corrupt.png":
            raise OcrError("OCR_INPUT_INVALID", "invalid raster")
        assert self.page is not None
        return self.page


def _values(document: SupportDocument) -> dict[str, str | None]:
    return {name: item.value for name, item in document.fields.items()}


@pytest.mark.parametrize(
    ("heading", "period", "header", "printed_amount", "expected"),
    [
        (
            "Служебная записка КЛ-43",
            "Дата: 11.04.2027",
            "Основание",
            "89 456,70",
            "89456.70",
        ),
        (
            "Справка о расходах ИТ 04-27",
            "Период: апрель 2027",
            "Описание",
            "95 121,27",
            "95121.27",
        ),
    ],
)
def test_docx_table_amount_and_paragraph_evidence(
    tmp_path: Path,
    heading: str,
    period: str,
    header: str,
    printed_amount: str,
    expected: str,
) -> None:
    path = _docx(tmp_path / "arbitrary.docx", heading, period, header, printed_amount)
    extractor = SupportDocumentsExtractor(_Ocr(), preview_dir=tmp_path)
    (document,) = extractor.extract(_registry(path))
    assert document.file.status == "COMPLETED"
    assert document.fields["number"].value == heading.split()[-1]
    assert document.fields["contract"].value == "26/03/03-02"
    assert (
        document.fields["purpose"].value
        == "Обслуживание оборудования для отдела контроля."
    )
    assert document.fields["purpose"].fragments[0].location == DocxLocation(paragraph=4)
    assert document.fields["amount"].value == expected
    (evidence,) = document.fields["amount"].fragments
    assert evidence.text == printed_amount
    assert evidence.location == DocxLocation(paragraph=1, table=1, row=2, cell=2)


class _PdfInspector(PdfInspector):
    def __init__(self, pages: Sequence[PdfPageContent]) -> None:
        super().__init__()
        self.pages = tuple(pages)

    def inspect(self, path: Path) -> PdfInspection:
        return PdfInspection(len(self.pages))

    def extract(self, path: Path) -> PdfContentResult:
        return PdfContentResult(self.pages)


def _pdf(path: Path, pages: int) -> Path:
    pdf = canvas.Canvas(str(path))
    for _ in range(pages):
        pdf.showPage()
    pdf.save()
    return path


def test_digital_date_from_content_and_scan_page_bbox(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "07_УПД_0426-77.pdf", 2)
    ocr = _Ocr(_page("Итого к оплате: 66 501,30 руб."))
    inspector = _PdfInspector(
        (
            PdfPageContent(
                1,
                "УНИВЕРСАЛЬНЫЙ ПЕРЕДАТОЧНЫЙ ДОКУМЕНТ 0426-77\nДата 22.05.2026\n"
                "Договор 24/03/15-01\nЛицензирование средств связи\n"
                "для административной группы.\nВсего страниц: 2",
                (),
            ),
            PdfPageContent(2, "2", ()),
        )
    )
    extractor = SupportDocumentsExtractor(
        ocr, preview_dir=tmp_path, inspector=inspector
    )

    (document,) = extractor.extract(_registry(pdf))

    assert document.file.status == "COMPLETED"
    assert _values(document) == {
        "number": "0426-77",
        "date": "2026-05-22",
        "contract": "24/03/15-01",
        "purpose": "Лицензирование средств связи для административной группы.",
        "amount": "66501.30",
    }
    assert document.fields["date"].fragments[0].location == SupportPageLocation(
        1, line=2
    )
    (preview,) = ocr.calls
    assert preview.name == "page-0002.png" and preview.is_file()
    assert document.fields["amount"].fragments[0].location == SupportPageLocation(
        2, bbox=(10, 0, 300, 12), image_path=preview
    )
    assert document.ocr_provenance == {2: _PROVENANCE}


def test_png_uses_explicit_amount_and_preserves_ambiguous_ocr_number(
    tmp_path: Path,
) -> None:
    path = tmp_path / "no-requisites.png"
    path.write_bytes(b"mock OCR input")
    ocr = _Ocr(
        _page(
            "АКТ ОКАЗАННЫХ УСЛУГ № СВ-О426",
            "Дата: 29.04.2026",
            "По договору 24/03/15-01",
            "Услуги связи и защищенного доступа.",
            "Стоимость услуг: 650 000,00 руб.",
            "Итого НДС: 100 000,00 руб.",
            "Всего страниц: 1",
        )
    )
    (document,) = SupportDocumentsExtractor(ocr, preview_dir=tmp_path).extract(
        _registry(path)
    )
    assert document.fields["amount"].value == "650000.00"
    assert document.fields["amount"].fragments[0].location == SupportPageLocation(
        1, bbox=(10, 80, 300, 92), image_path=path
    )
    assert document.fields["number"].value == "СВ-О426"
    assert document.fields["number"].needs_review


@pytest.mark.parametrize(
    ("lines", "expected", "needs_review"),
    [
        (("Сопровождение за июнь 2026 года.",), "2026-06", False),
        (("Период: июнь 2026", "Работы за июня 2026 г."), "2026-06", False),
        (("Период: май 2026", "Работы за июнь 2026 года."), None, True),
        (("Услуги за январь 2026 — март 2026.",), None, True),
        (("Период: май 2026; см. акт за июнь 2026.",), "2026-05", False),
        (("Работы по договору от 01.06.2026.",), None, False),
    ],
)
def test_service_period_uses_literal_context_and_not_document_date(
    tmp_path: Path, lines: tuple[str, ...], expected: str | None, needs_review: bool
) -> None:
    path = tmp_path / "arbitrary.png"
    path.write_bytes(b"synthetic image")
    ocr = _Ocr(_page(
        "Акт № 124 от 03.07.2026",
        "Договор 24/03/15-01",
        "Административное сопровождение.",
        "К оплате: 100,00",
        *lines,
    ))
    (document,) = SupportDocumentsExtractor(
        ocr, preview_dir=tmp_path
    ).extract(_registry(path))
    assert document.fields["date"].value == "2026-07-03"
    if expected is None and not needs_review:
        assert "period" not in document.fields
    else:
        period = document.fields["period"]
        assert period.value == expected
        assert period.needs_review == needs_review
        assert tuple(part.text for part in period.fragments) == lines


def test_conflicting_totals_keep_both_fragments(tmp_path: Path) -> None:
    path = tmp_path / "document.pdf"
    path.touch()
    inspector = _PdfInspector(
        (
            PdfPageContent(
                1, "Акт № NORD-88\nИтого к оплате: 10 руб.\nИтого 11 руб.", ()
            ),
        )
    )
    extractor = SupportDocumentsExtractor(
        _Ocr(), preview_dir=tmp_path, inspector=inspector
    )
    (document,) = extractor.extract(_registry(path))
    assert document.fields["number"].value == "NORD-88"
    assert document.fields["amount"].value is None
    assert len(document.fields["amount"].fragments) == 2
    assert any(issue.code == "SUPPORT_FIELD_CONFLICT" for issue in document.issues)


def test_numbered_basis_alone_is_not_a_contract(tmp_path: Path) -> None:
    path = tmp_path / "document.pdf"
    path.touch()
    inspector = _PdfInspector(
        (
            PdfPageContent(
                1,
                "Акт № ИА-11\nОснование: 123\nНазначение: Поддержка\nСумма: 10 руб.",
                (),
            ),
        )
    )
    extractor = SupportDocumentsExtractor(
        _Ocr(), preview_dir=tmp_path, inspector=inspector
    )
    (document,) = extractor.extract(_registry(path))
    assert "contract" not in document.fields
    assert document.fields["number"].value == "ИА-11"
    assert document.fields["amount"].value == "10"


def test_corrupt_inputs_do_not_hide_valid_documents(tmp_path: Path) -> None:
    invalid = [tmp_path / f"corrupt.{ext}" for ext in ("docx", "png", "pdf")]
    for path in invalid:
        path.write_bytes(b"corrupt data")
    unknown_encoding = tmp_path / "bad-encoding.docx"
    with ZipFile(unknown_encoding, "w") as archive:
        archive.writestr(
            "word/document.xml", b'<?xml version="1.0" encoding="BAD-ENCODING"?><x/>'
        )
    invalid.append(unknown_encoding)
    valid = _docx(
        tmp_path / "valid.docx",
        "Служебная записка С-11",
        "Дата: 15.09.2026",
        "Основание",
        "101,17",
    )
    results = SupportDocumentsExtractor(_Ocr(), preview_dir=tmp_path).extract(
        _registry(*invalid, valid)
    )
    assert [result.file.status for result in results] == ["FAILED"] * 4 + ["COMPLETED"]
    assert [result.issues[0].code for result in results[:-1]] == [
        "DOCX_UNREADABLE",
        "OCR_INPUT_INVALID",
        "PDF_UNREADABLE",
        "DOCX_UNREADABLE",
    ]
    assert results[-1].fields["amount"].value == "101.17"


def test_equal_docx_item_amounts_do_not_imply_a_document_total(tmp_path: Path) -> None:
    path = _docx(
        tmp_path / "multiple-items.docx",
        "Служебная записка ИА-11",
        "Дата: 11.04.2027",
        "Описание",
        "100,00",
        additional_items=[("Обслуживание", "100,00")],
    )
    (document,) = SupportDocumentsExtractor(_Ocr(), preview_dir=tmp_path).extract(
        _registry(path)
    )
    amount = document.fields["amount"]
    assert amount.value is None and amount.needs_review
    assert [fragment.location for fragment in amount.fragments] == [
        DocxLocation(1, table=1, row=2, cell=2),
        DocxLocation(1, table=1, row=3, cell=2),
    ]
    assert any(issue.code == "SUPPORT_FIELD_CONFLICT" for issue in document.issues)


def test_page_limit_applies_before_ocr(tmp_path: Path) -> None:
    png = tmp_path / "too-many.png"
    png.touch()
    pdf = tmp_path / "digital.pdf"
    pdf.touch()
    inspector = _PdfInspector((PdfPageContent(1, "", ()),))
    ocr = _Ocr()
    extractor = SupportDocumentsExtractor(
        ocr,
        preview_dir=tmp_path,
        limits=PackageLimits(max_support_pages=1),
        inspector=inspector,
    )
    with pytest.raises(PackageLimitError, match="page count"):
        extractor.extract(_registry(png, pdf))
    assert not ocr.calls
