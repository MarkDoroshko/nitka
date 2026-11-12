"""Read primary accounting documents and retain evidence for each field."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Protocol
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile
from zlib import error as ZipDataError

from nitka.amounts import normalize_amount
from nitka.contracts.extraction import FileRecord, Issue
from nitka.extraction.docx_support import DocxLocation, read_docx
from nitka.extraction.limits import (
    DEFAULT_PACKAGE_LIMITS,
    PackageLimitError,
    PackageLimits,
    enforce_package_size,
)
from nitka.extraction.pdf import (
    PDF_ENCRYPTED,
    PDF_IMAGE_SUFFIXES,
    PDF_RENDER_FAILED,
    PDF_SUFFIX,
    PDF_TIMEOUT,
    PDF_UNREADABLE,
    PdfInspection,
    PdfInspector,
)
from nitka.extraction.registry import InputRegistry, RegisteredInput
from nitka.ocr.engine import BoundingBox, OcrError, OcrPage, OcrProvenance

_MIN_CONFIDENCE = 0.80
_DOCX_SUFFIX = ".docx"
_IDENTIFIER = r"(?=[\w./-]*[0-9])[\w]+(?:[./-][\w]+)*"
_DATE = r"[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4}"
_MARKER = r"(?:№\s*|(?:No\.?|N)\s+)"
_TITLE = (
    r"сч[её]т[-– ]акт|(?:акт|akt)(?: оказанных услуг)?|упд|"
    r"универсальный передаточный документ|служебная записка|"
    r"справка о (?:расходах|затратах) ИТ"
)
_HEADING = re.compile(
    rf"^(?:{_TITLE})\s+(?:{_MARKER})?(?P<number>{_IDENTIFIER})"
    rf"(?:\s+(?:от|ot)\s+(?P<date>{_DATE}))?[\s.]*$",
    re.IGNORECASE,
)
_NUMBER = re.compile(
    rf"^(?:номер(?: документа)?\s*[: ]\s*(?:{_MARKER})?|{_MARKER})"
    rf"(?P<number>{_IDENTIFIER})[\s.]*$",
    re.IGNORECASE,
)
_DATED = re.compile(rf"^дата(?: документа)?\s*[: ]\s*({_DATE})[\s.]*$", re.IGNORECASE)
_CONTRACT = re.compile(
    rf"^(?:основание[\s:]+)?(?:по\s+)?договор(?:у)?[\s:]+"
    rf"(?:{_MARKER})?(?P<number>{_IDENTIFIER})(?=[\s.,;]|$)",
    re.IGNORECASE,
)
_CONTRACT_BASIS = re.compile(
    rf"^основание[\s:]+(?:{_MARKER})?(?P<number>(?=[\w./-]*/[\w./-]*/){_IDENTIFIER})[\s.]*$",
    re.IGNORECASE,
)
_PERIOD = re.compile(r"^(?:(?:отч[её]тный )?период)\s*:\s*(\S.*)$", re.IGNORECASE)
_PURPOSE = re.compile(
    r"^(?:содержание|описание|назначение)(?: работ| услуг)?\s*:\s*(.*)$", re.IGNORECASE
)
_METADATA = re.compile(
    rf"^(?:{_TITLE}|номер|дата|основание|(?:по )?договор(?:у)?|этап|"
    r"исполнитель|поставщик|покупатель|получатель|заказчик|подразделение|период)"
    r"(?=\W|$)",
    re.IGNORECASE,
)
_AMOUNT_LABEL = re.compile(
    r"^(?P<label>итого(?: к оплате| по документу)?|всего(?: к оплате)?|"
    r"к оплате(?: по документу)?|сумма(?: по акту| по документу| к оплате)?|"
    r"стоимость(?: (?:оказанных )?(?:услуг|работ|товаров))?(?: за расчетный период)?)"
    r"(?:\s*:\s*|\s+(?=-?\d))(?P<amount>.*)$",
    re.IGNORECASE,
)
_MONEY = re.compile(r"(.+?)(?:\s*(?:руб(?:\.|лей|ля)?|₽|RUB))?", re.IGNORECASE)
_AMOUNT_COLUMN = re.compile(
    r"^(?:сумма|стоимость)(?:\s*,?\s*(?:руб\.?|₽|RUB))?$", re.IGNORECASE
)
_PURPOSE_COLUMNS = {"основание", "описание", "назначение", "наименование"}
_TOTAL_LABEL = re.compile(
    r"(?:итого|всего)(?: к оплате| по документу)?|к оплате(?: по документу)?|сумма к оплате",
    re.IGNORECASE,
)
_SUMMARY_LABEL = re.compile(
    r"^(?:(?:итого|всего|сумма)\s+)?(?:ндс|страниц|листов)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class SupportPageLocation:
    page: int
    line: int | None = None
    bbox: BoundingBox | None = None
    image_path: Path | None = None


@dataclass(frozen=True)
class SupportFragment:
    text: str
    location: DocxLocation | SupportPageLocation
    confidence: float | None = None


@dataclass(frozen=True)
class SupportField:
    value: str | None
    fragments: tuple[SupportFragment, ...]
    needs_review: bool = False


@dataclass(frozen=True)
class SupportDocument:
    file: FileRecord
    fragments: tuple[SupportFragment, ...]
    fields: dict[str, SupportField]
    issues: tuple[Issue, ...]
    ocr_provenance: dict[int, OcrProvenance]


class ImageRecognizer(Protocol):
    def recognize(self, image_path: Path) -> OcrPage: ...


@dataclass
class _ReadDocument:
    fragments: list[SupportFragment] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    ocr_provenance: dict[int, OcrProvenance] = field(default_factory=dict)


@dataclass(frozen=True)
class _Candidate:
    value: str
    fragments: tuple[SupportFragment, ...]
    total: bool = False
    line_item: bool = False


class SupportDocumentsExtractor:
    """Extract supported primary documents from the caller's input registry.

    Use a preview directory specific to this run. OCR coordinates for a PDF
    refer to the rendered page stored there. Image coordinates refer to the
    original image.
    """

    def __init__(
        self,
        ocr: ImageRecognizer,
        *,
        preview_dir: Path,
        limits: PackageLimits = DEFAULT_PACKAGE_LIMITS,
        inspector: PdfInspector | None = None,
    ) -> None:
        self.ocr = ocr
        self.preview_dir = preview_dir
        self.limits = limits
        self.inspector = inspector or PdfInspector(limits.pdf_timeout_seconds)

    def extract(self, registry: InputRegistry) -> tuple[SupportDocument, ...]:
        enforce_package_size(registry.inputs, self.limits)
        entries = [
            entry
            for entry in registry.inputs
            if entry.role == "SUPPORT"
            and entry.path.suffix.lower()
            in {PDF_SUFFIX, _DOCX_SUFFIX, *PDF_IMAGE_SUFFIXES}
        ]
        inspections: dict[str, PdfInspection] = {}
        pages = 0
        for entry in entries:
            suffix = entry.path.suffix.lower()
            if suffix == PDF_SUFFIX:
                inspection = self.inspector.inspect(entry.path)
                inspections[entry.file_id] = inspection
                pages += inspection.page_count or 0
            elif suffix in PDF_IMAGE_SUFFIXES:
                pages += 1
            if pages > self.limits.max_support_pages:
                raise PackageLimitError(
                    f"support page count {pages} exceeds {self.limits.max_support_pages}"
                )
        return tuple(
            self._extract(entry, inspections.get(entry.file_id)) for entry in entries
        )

    def _extract(
        self, entry: RegisteredInput, inspection: PdfInspection | None
    ) -> SupportDocument:
        read = _ReadDocument()
        if inspection is not None:
            self._read_pdf(entry, inspection, read)
        elif entry.path.suffix.lower() == _DOCX_SUFFIX:
            try:
                read.fragments.extend(
                    SupportFragment(paragraph.text, paragraph.location)
                    for paragraph in read_docx(entry.path)
                    if paragraph.text.strip()
                )
            except (
                BadZipFile,
                OSError,
                ParseError,
                RuntimeError,
                ValueError,
                ZipDataError,
            ) as exc:
                read.issues.append(_issue(entry, "DOCX_UNREADABLE", str(exc)))
        else:
            self._read_image(entry, entry.path, 1, read)

        if not read.fragments:
            if not read.issues:
                read.issues.append(
                    _issue(entry, "SUPPORT_TEXT_MISSING", "No readable text")
                )
            return SupportDocument(
                entry.to_file_record("FAILED"),
                (),
                {},
                tuple(read.issues),
                read.ocr_provenance,
            )
        fields = _extract_fields(read.fragments)
        required = ("number", "contract", "purpose", "amount")
        missing = [name for name in required if name not in fields]
        if "date" not in fields and "period" not in fields:
            missing.append("date or period")
        for name in missing:
            read.issues.append(_issue(entry, "SUPPORT_FIELD_MISSING", name))
        for name, item in fields.items():
            if item.value is None:
                read.issues.append(_issue(entry, "SUPPORT_FIELD_CONFLICT", name))
            elif item.needs_review:
                read.issues.append(_issue(entry, "SUPPORT_FIELD_REVIEW", name))
        return SupportDocument(
            entry.to_file_record("PARTIAL" if read.issues else "COMPLETED"),
            tuple(read.fragments),
            fields,
            tuple(read.issues),
            read.ocr_provenance,
        )

    def _read_pdf(
        self, entry: RegisteredInput, inspection: PdfInspection, read: _ReadDocument
    ) -> None:
        if inspection.timed_out:
            read.issues.append(_issue(entry, PDF_TIMEOUT, "PDF inspection timed out"))
            return
        if inspection.encrypted:
            read.issues.append(
                _issue(entry, PDF_ENCRYPTED, "PDF is password-protected")
            )
            return
        if inspection.error is not None or not inspection.page_count:
            read.issues.append(
                _issue(entry, PDF_UNREADABLE, inspection.error or "PDF has no pages")
            )
            return

        content = self.inspector.extract(entry.path)
        text_by_page = (
            {page.page: page.text for page in content.pages} if content.ok else {}
        )
        page_numbers = range(1, inspection.page_count + 1)
        images: Sequence[Path] = ()
        if any(not _has_text(text_by_page.get(number, "")) for number in page_numbers):
            rendered = self.inspector.render(
                entry.path,
                self.preview_dir / entry.file_id,
                dpi=200,
                max_pages=self.limits.max_support_pages,
            )
            if rendered.ok and len(rendered.pages) == inspection.page_count:
                images = rendered.pages
            else:
                code = PDF_TIMEOUT if rendered.timed_out else PDF_RENDER_FAILED
                read.issues.append(
                    _issue(entry, code, rendered.error or "Incomplete PDF render")
                )

        for number in page_numbers:
            text = text_by_page.get(number, "")
            if _has_text(text):
                read.fragments.extend(
                    SupportFragment(line, SupportPageLocation(number, line=line_number))
                    for line_number, line in enumerate(text.splitlines(), 1)
                    if line.strip()
                )
            elif images:
                self._read_image(entry, images[number - 1], number, read)

    def _read_image(
        self, entry: RegisteredInput, image: Path, number: int, read: _ReadDocument
    ) -> None:
        try:
            page = self.ocr.recognize(image)
        except OcrError as exc:
            read.issues.append(_issue(entry, exc.code, f"page {number}: {exc.detail}"))
            return
        if sorted(page.reading_order) != list(range(len(page.blocks))):
            read.issues.append(
                _issue(
                    entry, "OCR_WORKER_FAILED", f"page {number}: Invalid block order"
                )
            )
            return
        read.ocr_provenance[number] = page.provenance
        blocks = [
            page.blocks[index]
            for index in page.reading_order
            if page.blocks[index].text.strip()
        ]
        if not blocks:
            read.issues.append(
                _issue(entry, "SUPPORT_TEXT_MISSING", f"page {number}: No OCR text")
            )
        for block in blocks:
            read.fragments.append(
                SupportFragment(
                    block.text,
                    SupportPageLocation(number, bbox=block.bbox, image_path=image),
                    block.confidence,
                )
            )


def _has_text(text: str) -> bool:
    return sum(char.isalnum() for char in text) >= 20 or any(
        _AMOUNT_LABEL.fullmatch(line.strip()) or _DATED.fullmatch(line.strip())
        for line in text.splitlines()
    )


def _issue(entry: RegisteredInput, code: str, detail: str) -> Issue:
    return Issue(code=code, file_id=entry.file_id, detail=detail)


def _extract_fields(fragments: Sequence[SupportFragment]) -> dict[str, SupportField]:
    candidates: dict[str, list[_Candidate]] = {}
    purposes: list[_Candidate] = []
    seen_header = seen_amount = False
    for parts in _body_lines(fragments):
        text = " ".join(" ".join(part.text.split()) for part in parts)
        heading = _HEADING.fullmatch(text) or _NUMBER.fullmatch(text)
        if heading:
            candidates.setdefault("number", []).append(
                _Candidate(heading["number"], parts)
            )
        date_label = _DATED.fullmatch(text)
        printed_date = (
            date_label[1] if date_label else heading and heading.groupdict().get("date")
        )
        if printed_date and (value := _date_value(printed_date)):
            candidates.setdefault("date", []).append(_Candidate(value, parts))
        contract = _CONTRACT.match(text) or _CONTRACT_BASIS.fullmatch(text)
        if contract and not re.fullmatch(_DATE, contract["number"]):
            candidates.setdefault("contract", []).append(
                _Candidate(contract["number"], parts)
            )
        if period := _PERIOD.fullmatch(text):
            candidates.setdefault("period", []).append(_Candidate(period[1], parts))
        if amount := _AMOUNT_LABEL.fullmatch(text):
            seen_amount = True
            if value := _money(amount["amount"]):
                candidates.setdefault("amount", []).append(
                    _Candidate(
                        value, parts, bool(_TOTAL_LABEL.fullmatch(amount["label"]))
                    )
                )
        elif purpose := _PURPOSE.fullmatch(text):
            if purpose[1]:
                purposes.append(_Candidate(purpose[1], parts))
            seen_header, seen_amount = True, False
        elif heading or _METADATA.match(text):
            seen_header = True
        elif _SUMMARY_LABEL.match(text):
            continue
        elif (
            seen_header
            and not seen_amount
            and (
                any(char.isalpha() for char in text)
                or (
                    purposes
                    and "/" in text
                    and re.fullmatch(_IDENTIFIER, text.rstrip("."))
                )
            )
        ):
            purposes.append(_Candidate(text, parts))

    table_amounts, table_purposes = _docx_table_fields(fragments)
    if table_amounts:
        candidates.setdefault("amount", []).extend(table_amounts)
    fields = {name: _select(name, values) for name, values in candidates.items()}
    if purpose_parts := purposes or table_purposes:
        fields["purpose"] = SupportField(
            " ".join(part.value for part in purpose_parts),
            tuple(fragment for part in purpose_parts for fragment in part.fragments),
            any(_low_confidence(part.fragments) for part in purpose_parts),
        )
    return fields


def _body_lines(
    fragments: Sequence[SupportFragment],
) -> list[tuple[SupportFragment, ...]]:
    lines: list[tuple[SupportFragment, ...]] = []
    for fragment in fragments:
        location = fragment.location
        if isinstance(location, DocxLocation) and location.table is not None:
            continue
        if lines and _same_ocr_line(lines[-1][-1], fragment):
            lines[-1] += (fragment,)
        else:
            lines.append((fragment,))
    return lines


def _same_ocr_line(left: SupportFragment, right: SupportFragment) -> bool:
    a, b = left.location, right.location
    if (
        not isinstance(a, SupportPageLocation)
        or not isinstance(b, SupportPageLocation)
        or a.page != b.page
        or a.bbox is None
        or b.bbox is None
    ):
        return False
    _, top, end, bottom = a.bbox
    start, next_top, _, next_bottom = b.bbox
    height = min(bottom - top, next_bottom - next_top)
    overlap = min(bottom, next_bottom) - max(top, next_top)
    return height > 0 and overlap >= height / 2 and 0 <= start - end <= 4 * height


def _docx_table_fields(
    fragments: Sequence[SupportFragment],
) -> tuple[list[_Candidate], list[_Candidate]]:
    tables: dict[int, dict[int, dict[int, list[SupportFragment]]]] = {}
    for fragment in fragments:
        loc = fragment.location
        if isinstance(loc, DocxLocation) and loc.table and loc.row and loc.cell:
            cells = tables.setdefault(loc.table, {}).setdefault(loc.row, {})
            cells.setdefault(loc.cell, []).append(fragment)

    amounts: list[_Candidate] = []
    purposes: list[_Candidate] = []
    for rows in tables.values():
        amount_column = purpose_column = None
        for cells in rows.values():
            labels = {
                index: " ".join(
                    " ".join(part.text.split()) for part in parts
                ).casefold()
                for index, parts in cells.items()
            }
            headers = [
                index
                for index, text in labels.items()
                if _AMOUNT_COLUMN.fullmatch(text)
            ]
            if headers:
                amount_column = headers[0] if len(headers) == 1 else None
                purpose_column = next(
                    (
                        index
                        for index, text in labels.items()
                        if text in _PURPOSE_COLUMNS
                    ),
                    None,
                )
                continue
            if any(_SUMMARY_LABEL.match(text) for text in labels.values()):
                continue
            total = any(
                _TOTAL_LABEL.fullmatch(text.rstrip(":")) for text in labels.values()
            )
            amount_parts = tuple(cells.get(amount_column, [])) if amount_column else ()
            if amount_parts and (
                value := _money(" ".join(part.text for part in amount_parts))
            ):
                amounts.append(
                    _Candidate(value, amount_parts, total, line_item=not total)
                )
            if (
                not total
                and purpose_column
                and (parts := tuple(cells.get(purpose_column, [])))
            ):
                purposes.append(
                    _Candidate(" ".join(part.text for part in parts), parts)
                )
    return amounts, purposes


def _money(text: str) -> str | None:
    match = _MONEY.fullmatch(text.strip())
    return normalize_amount(match[1]) if match else None


def _date_value(text: str) -> str | None:
    day, month, year = map(int, text.split("."))
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _select(name: str, values: Sequence[_Candidate]) -> SupportField:
    selected = [item for item in values if item.total] or values
    distinct = {
        Decimal(item.value) if name == "amount" else item.value for item in selected
    }
    fragments = tuple(part for item in selected for part in item.fragments)
    if len(distinct) > 1 or sum(item.line_item for item in selected) > 1:
        return SupportField(None, fragments, needs_review=True)
    # Identifier glyphs can be ambiguous even at high OCR confidence.
    ocr_identifier = name in {"number", "contract"} and any(
        fragment.confidence is not None for fragment in fragments
    )
    return SupportField(
        selected[0].value, fragments, ocr_identifier or _low_confidence(fragments)
    )


def _low_confidence(fragments: Sequence[SupportFragment]) -> bool:
    return any(
        part.confidence is not None and part.confidence < _MIN_CONFIDENCE
        for part in fragments
    )
