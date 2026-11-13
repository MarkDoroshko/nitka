"""Inspect the saved sources of a real check."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from PIL import Image, ImageDraw

from nitka.contracts.analysis import Check
from nitka.contracts.extraction import (
    CellLocation,
    DocxLocation,
    OcrLocation,
    PageLocation,
    Source,
)
from nitka.extraction.docx_support import read_docx
from nitka.extraction.limits import MAX_PREVIEW_PIXELS, OCR_PDF_DPI
from nitka.extraction.pdf import PDF_IMAGE_SUFFIXES, PdfInspector
from nitka.storage import StoredRun


@dataclass(frozen=True)
class CheckEvidence:
    reported: tuple[Source, ...]
    supported: tuple[Source, ...]
    other: tuple[Source, ...]


def check_evidence(check: Check, sources: dict[str, Source]) -> CheckEvidence:
    def resolve(ids: list[str]) -> tuple[Source, ...]:
        return tuple(sources[key] for key in dict.fromkeys(ids) if key in sources)

    reported = resolve(
        [key for op in check.operands if op.name == "reported" for key in op.source_ids]
    )
    supported = resolve(
        [
            key
            for op in check.operands
            if op.name == "supported"
            for key in op.source_ids
        ]
    )
    used = {source.source_id for source in (*reported, *supported)}
    return CheckEvidence(
        reported,
        supported,
        resolve([key for key in check.evidence_ids if key not in used]),
    )


def source_address(source: Source) -> str:
    loc = source.location
    if isinstance(loc, CellLocation):
        return f"{loc.sheet}!{loc.cell}" if loc.sheet else loc.cell
    if isinstance(loc, DocxLocation):
        parts = []
        for label, value in (
            ("таблица", loc.table),
            ("строка", loc.row),
            ("ячейка", loc.cell),
            ("абзац", loc.paragraph),
        ):
            if value is not None:
                parts.append(f"{label} {value}")
        return ", ".join(parts) or "Фрагмент DOCX"
    return f"Страница {loc.page}" + (" · OCR" if isinstance(loc, OcrLocation) else "")


def source_texts(stored: StoredRun, source: Source) -> list[tuple[str, str]]:
    """Show persisted original text and, for a computed payroll amount, its cells."""
    if isinstance(source.location, DocxLocation):
        path = stored.inputs.get(source.file_id)
        if path is not None:
            loc = source.location
            try:
                for paragraph in read_docx(path):
                    anchor = paragraph.location
                    if (anchor.table, anchor.row, anchor.cell, anchor.paragraph) == (
                        loc.table,
                        loc.row,
                        loc.cell,
                        loc.paragraph,
                    ):
                        texts = [(source_address(source), paragraph.text)]
                        if (
                            source.raw_text
                            and source.raw_text.strip() != paragraph.text.strip()
                        ):
                            texts.append(("Извлечённый фрагмент", source.raw_text))
                        return texts
            except (OSError, ValueError, RuntimeError, BadZipFile, ParseError):
                pass
    if source.raw_text is not None:
        return [("Извлечённый фрагмент", source.raw_text)]
    if isinstance(source.location, CellLocation):
        fields = next(
            (
                row.fields
                for row in stored.extraction.rows
                if row.amount_source_id == source.source_id
            ),
            {},
        )
        component_ids = {fields.get("component_0201"), fields.get("component_0202")}
        return [
            (source_address(item), item.raw_text)
            for item in stored.extraction.sources
            if item.source_id in component_ids and item.raw_text is not None
        ]
    return []


def page_preview(
    path: Path, location: PageLocation | OcrLocation
) -> tuple[bytes, bool]:
    """Render only the recorded page and preserve OCR pixel coordinates."""
    with TemporaryDirectory(prefix="nitka-evidence-") as directory:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            rendered = PdfInspector().render(
                path,
                Path(directory),
                dpi=OCR_PDF_DPI if isinstance(location, OcrLocation) else 150.0,
                page=location.page,
            )
            if not rendered.ok or len(rendered.pages) != 1:
                raise ValueError("Не удалось отобразить страницу PDF.")
            path = rendered.pages[0]
        elif suffix not in PDF_IMAGE_SUFFIXES or location.page != 1:
            raise ValueError("Страница отсутствует в сохранённом документе.")

        with Image.open(path, formats=("PNG", "JPEG")) as image:
            if image.width * image.height > MAX_PREVIEW_PIXELS:
                raise ValueError("Изображение превышает допустимый размер.")
            with image.convert("RGB") as preview:
                bbox = location.bbox if isinstance(location, OcrLocation) else None
                highlighted = False
                if bbox and all(isfinite(value) for value in bbox):
                    left, top, right, bottom = bbox
                    if (
                        0 <= left < right <= image.width
                        and 0 <= top < bottom <= image.height
                    ):
                        ImageDraw.Draw(preview).rectangle(
                            bbox, outline="#c52820", width=4
                        )
                        highlighted = True
                output = BytesIO()
                preview.save(output, format="PNG")
                return output.getvalue(), highlighted
