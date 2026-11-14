"""Build the synthetic, fixed layouts used by run_generalization.py."""

from datetime import datetime
from io import BytesIO
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from nitka.contracts.profile import CaseProfile
from nitka.extraction.ports import InputDocument

REPORTS = {"numbered": "numbered.pdf", "compact": "compact.pdf"}
CASES = {"numbered": "numbered", "compact": "compact", "numbered-control": "numbered"}
SUPPORT = {
    "work-order.pdf": "digital-act",
    "subscription.png": "scanned-act",
    "records.docx": "docx-table",
    "payroll.xlsx": "shifted-payroll",
    "payroll-control.xlsx": "payroll-with-cover",
    "insurance.xlsx": "insurance-with-cover",
    "foreign.pdf": "digital-act",
}
OCR_FILES = frozenset((*REPORTS.values(), "subscription.png"))
PROFILE = CaseProfile.model_validate({
    "profile_id": "synthetic-generalization-v1",
    "case_number": 8,
    "contract": "31/07/41-08",
    "service_period": {"start": "2031-03-01", "end": "2031-03-31"},
    "target_stage": 2,
    "target_year": 2031,
    "target_row_label": "2 этап, 2031 г.",
    "article_codes": ["0200", "0300", "0800", "0900"],
    "operation_id_rule": "Договор, номер и дата операции",
    "classification_basis": "Назначение расходов и код статьи",
    "fragment": "Годовая строка по этапу",
    "profile_source": "Синтетический набор для проверки обобщения",
})

_FONT = "Generalization"
_DOCX = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def build_corpus(directory: Path, font: Path) -> None:
    directory.mkdir()
    pdfmetrics.registerFont(TTFont(_FONT, str(font)))
    _numbered_report(directory / REPORTS["numbered"])
    _compact_report(directory / REPORTS["compact"])
    _act(directory / "work-order.pdf", "ПК-381", "31/07/41-08", "48 600,50")
    _act(directory / "foreign.pdf", "ПК-399", "31/07/51-08", "48 700,50")
    _scan(directory / "subscription.png", font)
    _docx(directory / "records.docx")
    _workbook(directory / "payroll.xlsx", payroll=True)
    _workbook(directory / "payroll-control.xlsx", payroll=True, shifted=False)
    _workbook(directory / "insurance.xlsx", payroll=False)


def support_names(case: str) -> list[str]:
    other_payroll = "payroll.xlsx" if case == "numbered-control" else "payroll-control.xlsx"
    return [name for name in SUPPORT if name != other_payroll]


def documents(directory: Path, case: str) -> list[InputDocument]:
    return [
        InputDocument(directory / REPORTS[CASES[case]], "REPORT"),
        *(InputDocument(directory / name, "SUPPORT") for name in support_names(case)),
    ]


def _numbered_report(path: Path) -> None:
    pdf = Canvas(str(path), pagesize=(1540, 510), invariant=1)
    pdf.setFont(_FONT, 23)
    pdf.drawString(48, 452, "Карточка фактических затрат")
    pdf.setFont(_FONT, 16)
    pdf.drawString(48, 413, "Март 2031 г. - Март 2031 г.")
    pdf.drawRightString(1490, 413, "Договор № 31/07/41-08")
    centers = (100, 198, *(269 + i * 84 for i in range(15)))
    headings = {
        0: "Этап и год", 1: "Месяц", 2: "Трудоемкость",
        6: "0200 Оплата труда", 11: "0300 Взносы",
        14: "0800", 15: "0900", 16: "Итого",
    }
    pdf.setFillColorRGB(0.91, 0.94, 0.97)
    pdf.rect(46, 306, 1454, 55, fill=1, stroke=0)
    pdf.setFillColorRGB(0, 0, 0)
    pdf.setFont(_FONT, 11)
    for column, heading in headings.items():
        pdf.drawCentredString(centers[column], 335, heading)
    pdf.drawCentredString(centers[3], 318, "Материальные")
    for number, x in enumerate(centers, start=1):
        pdf.drawCentredString(x, 285, str(number))
    pdf.setStrokeColorRGB(0.73, 0.77, 0.82)
    for y in (302, 268, 219, 173, 127):
        pdf.line(46, y, 1500, y)
    pdf.setFont(_FONT, 13)
    rows = (
        ("2 этап, 2030 г.", ("1,00", "0,00", "10,00", "10,00", "9,00", "1,00", "0,00",
                            "2,00", "2,00", "1,50", "0,50", "0,00", "7,00", "3,00", "22,00")),
        ("2 этап, 2031 г.", ("4,75", "0,00", "52000,25", "52000,25", "50300,50", "1699,75",
                            "0,00", "14000,13", "14000,13", "12500,10", "1500,03", "0,00",
                            "48700,50", "29500,62", "144201,50")),
        ("Всего 2 этап", ("5,75", "0,00", "52010,25", "52010,25", "50309,50", "1700,75",
                          "0,00", "14002,13", "14002,13", "12501,60", "1500,53", "0,00",
                          "48707,50", "29503,62", "144223,50")),
    )
    for y, (label, amounts) in zip((237, 191, 145), rows, strict=True):
        pdf.drawString(52, y, label)
        for x, amount in zip(centers[2:], amounts, strict=True):
            pdf.drawCentredString(x, y, amount)
    pdf.setFont(_FONT, 12)
    pdf.drawString(48, 76, "Суммы указаны в рублях. Синтетическая форма.")
    pdf.save()


def _compact_report(path: Path) -> None:
    pdf = Canvas(str(path), pagesize=(595, 630), invariant=1)
    pdf.setFont(_FONT, 24)
    pdf.drawString(45, 560, "Карточка фактических затрат")
    pdf.setFont(_FONT, 14)
    for y, line in (
        (523, "2 этап, 2031 г."),
        (495, "Договор № 31/07/41-08"),
        (467, "Март 2031 г. - Март 2031 г."),
        (407, "Статья расходов"),
    ):
        pdf.drawString(45, y, line)
    pdf.drawRightString(550, 407, "Сумма, руб.")
    for index, (label, amount) in enumerate((
        ("0900  Общехозяйственные расходы", "29 500,62"),
        ("0800  Общепроизводственные расходы", "48 700,50"),
        ("Материалы", "0,00"),
        ("0300  Страховые взносы", "14 000,13"),
        ("0200  Оплата труда", "52 000,25"),
        ("Итого", "144 201,50"),
    )):
        y = 367 - index * 39
        pdf.setStrokeColorRGB(0.73, 0.77, 0.82)
        pdf.line(45, y - 10, 550, y - 10)
        pdf.drawString(45, y, label)
        pdf.drawRightString(550, y, amount)
    pdf.setFont(_FONT, 12)
    pdf.drawString(45, 99, "Синтетическая форма.")
    pdf.save()


def _act(path: Path, number: str, contract: str, amount: str) -> None:
    pdf = Canvas(str(path), pagesize=(595, 560), invariant=1)
    pdf.setFont(_FONT, 21)
    pdf.drawString(45, 503, f"Акт оказанных услуг № {number}")
    pdf.setFont(_FONT, 14)
    pdf.drawString(45, 459, "Дата документа: 18.03.2031")
    pdf.drawString(45, 369, "Описание работ: Ремонт вентиляции цеха")
    pdf.drawString(45, 301, f"Стоимость работ: {amount} руб.")
    pdf.drawString(45, 240, f"Основание: договор {contract}")
    pdf.drawString(45, 209, "Период: март 2031")
    pdf.setFont(_FONT, 12)
    pdf.drawString(45, 70, "Синтетическая форма.")
    pdf.save()


def _scan(path: Path, font: Path) -> None:
    page = Image.new("RGB", (1240, 1120), "#f8f7ef")
    draw = ImageDraw.Draw(page)
    title = ImageFont.truetype(str(font), 45)
    body = ImageFont.truetype(str(font), 33)
    draw.text((90, 95), "УПД № ПС-628", font=title, fill="#181e24")
    draw.line((90, 172, 1140, 172), fill="#536577", width=3)
    for y, line in (
        (215, "Дата: 21.03.2031"),
        (296, "Договор: 31/07/41-08"),
        (378, "Период: март 2031"),
        (526, "Назначение: Консультации для бухгалтерии"),
        (687, "Сумма по документу: 19 000,43 руб."),
        (935, "Синтетическая форма."),
    ):
        draw.text((90, y), line, font=body, fill="#181e24")
    page.rotate(0.7, fillcolor="#f8f7ef").save(path)


def _zip_member(archive: ZipFile, name: str, data: bytes) -> None:
    archive.writestr(ZipInfo(name), data, compress_type=ZIP_DEFLATED)


def _docx(path: Path) -> None:
    def paragraph(parent: Element, text: str) -> None:
        node = SubElement(parent, _DOCX + "p")
        SubElement(SubElement(node, _DOCX + "r"), _DOCX + "t").text = text

    document = Element(_DOCX + "document")
    body = SubElement(document, _DOCX + "body")
    paragraph(body, "Служебная записка ДЛ-593")
    paragraph(body, "Дата документа: 25.03.2031")
    paragraph(body, "Период: март 2031")
    paragraph(body, "Договор: 31/07/41-08")
    table = SubElement(body, _DOCX + "tbl")
    SubElement(table, _DOCX + "tblPr")
    grid = SubElement(table, _DOCX + "tblGrid")
    for width in ("2200", "6300"):
        SubElement(grid, _DOCX + "gridCol", {_DOCX + "w": width})
    for cells in (("Сумма, руб.", "Описание"), ("10 500,19", "Ведение документооборота офиса")):
        row = SubElement(table, _DOCX + "tr")
        for cell in cells:
            paragraph(SubElement(row, _DOCX + "tc"), cell)
    with ZipFile(path, "w") as archive:
        _zip_member(archive, "word/document.xml", tostring(document, encoding="utf-8"))
        _zip_member(archive, "[Content_Types].xml", b"""<Types
 xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Override PartName="/word/document.xml"
 ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""")
        _zip_member(archive, "_rels/.rels", b"""<Relationships
 xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Target="word/document.xml"
 Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"/>
</Relationships>""")


def _workbook(path: Path, *, payroll: bool, shifted: bool = True) -> None:
    book = Workbook()
    cover = book.create_sheet("Сведения", 0)
    cover["A1"] = "Синтетическая форма"
    book.active = 0
    sheet = book.worksheets[1]
    sheet.title = "Ведомость" if payroll else "Расчет взносов"
    sheet["A2"] = "Документ: ОТ-831" if payroll else "Документ: ВЗ-831"
    sheet["A3"] = "Проект: 31/07/41-08 | Период: Март 2031"
    if payroll:
        start = 8 if shifted else 5
        if shifted:
            sheet["A5"] = "Расходы подразделения"
        values = (
            ("Код статьи", "Вид", "Сумма, руб."),
            ("0201", "Основная оплата", "50 300,50"),
            ("0202", "Дополнительная оплата", "1 699,75"),
        )
        for row, cells in enumerate(values, start=start):
            for column, value in enumerate(cells, start=1):
                sheet.cell(row, column, value)
        sheet.cell(start + 3, 3, f"=SUM(C{start + 1}:C{start + 2})")
    else:
        sheet["A5"] = "База начислений, руб."
        sheet["B5"] = "Начислено взносов, руб."
        sheet["C5"] = "Код статьи"
        sheet["A6"] = 51000.75
        sheet["B6"] = 14000.13
        sheet["C6"] = "0300"
    # openpyxl supplies a modified timestamp at save time. Normalize that
    # metadata and the archive timestamps so repeated generation is comparable.
    buffer = BytesIO()
    book.properties.created = datetime(2000, 1, 1)
    book.save(buffer)
    book.close()
    with ZipFile(buffer) as original, ZipFile(path, "w") as archive:
        for name in original.namelist():
            data = original.read(name)
            if name == "docProps/core.xml":
                data = data.replace(
                    book.properties.modified.isoformat(timespec="seconds").encode(),
                    b"2000-01-01T00:00:00",
                )
            _zip_member(archive, name, data)
