"""Read body paragraphs and table cells from bounded WordprocessingML."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

_DOCUMENT_XML = "word/document.xml"
_MAX_DOCUMENT_XML_BYTES = 8 * 1024 * 1024
_WORD_NAMESPACES = {
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://purl.oclc.org/ooxml/wordprocessingml/main",
}


@dataclass(frozen=True)
class DocxLocation:
    """One-based body paragraph, or table/row/cell and paragraph within it."""

    paragraph: int
    table: int | None = None
    row: int | None = None
    cell: int | None = None


@dataclass(frozen=True)
class DocxParagraph:
    text: str
    location: DocxLocation


def read_docx(path: Path) -> tuple[DocxParagraph, ...]:
    with ZipFile(path) as archive:
        members = [
            item for item in archive.infolist() if item.filename == _DOCUMENT_XML
        ]
        if len(members) != 1:
            raise ValueError("DOCX must contain exactly one document body")
        if members[0].file_size > _MAX_DOCUMENT_XML_BYTES:
            raise ValueError("DOCX body exceeds the XML size limit")
        with archive.open(members[0]) as stream:
            xml = stream.read(_MAX_DOCUMENT_XML_BYTES + 1)
        if len(xml) > _MAX_DOCUMENT_XML_BYTES:
            raise ValueError("DOCX body exceeds the XML size limit")

    # OOXML forbids DTDs. Check before parsing to avoid expanding custom entities.
    if b"<!DOCTYPE" in xml.replace(b"\x00", b""):
        raise ValueError("DOCX body contains a DTD")
    try:
        root = ET.fromstring(xml)
    except LookupError as exc:
        raise ValueError("Unsupported DOCX XML encoding") from exc
    namespace, _, tag = root.tag.removeprefix("{").partition("}")
    if namespace not in _WORD_NAMESPACES or tag != "document":
        raise ValueError("Invalid WordprocessingML document")
    w = f"{{{namespace}}}"
    body = root.find(f"{w}body")
    if body is None:
        raise ValueError("DOCX document body is missing")

    paragraphs: list[DocxParagraph] = []
    paragraph_number = table_number = 0
    for element in body:
        if element.tag == f"{w}p":
            paragraph_number += 1
            paragraphs.append(
                DocxParagraph(
                    _paragraph_text(element, w), DocxLocation(paragraph_number)
                )
            )
        elif element.tag == f"{w}tbl":
            table_number += 1
            for row_number, row in enumerate(element.findall(f"{w}tr"), 1):
                for cell_number, cell in enumerate(row.findall(f"{w}tc"), 1):
                    for index, paragraph in enumerate(cell.findall(f"{w}p"), 1):
                        paragraphs.append(
                            DocxParagraph(
                                _paragraph_text(paragraph, w),
                                DocxLocation(
                                    index, table_number, row_number, cell_number
                                ),
                            )
                        )
    return tuple(paragraphs)


def _paragraph_text(paragraph: ET.Element, w: str) -> str:
    pieces = []
    for element in paragraph.iter():
        if element.tag == f"{w}t":
            pieces.append(element.text or "")
        elif element.tag == f"{w}tab":
            pieces.append("\t")
        elif element.tag in (f"{w}br", f"{w}cr"):
            pieces.append("\n")
    return "".join(pieces)
