"""Extract positions from digital PDFs and route unusable pages to Vision.

The parser uses pdfplumber's text and table output, but keeps every value tied
to its file, logical document and page. A page with no usable text layer is not
parsed speculatively: it gets a Vision request and a rendered preview instead.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from nitka.amounts import parse_amount
from nitka.contracts.extraction import (
    ExtractedRow,
    ExtractionResult,
    FileStatus,
    Issue,
    PageLocation,
    Source,
)
from nitka.contracts.validation import validate_extraction
from nitka.extraction.limits import (
    DEFAULT_PACKAGE_LIMITS,
    PackageLimitError,
    PackageLimits,
    enforce_package_size,
)
from nitka.extraction.pdf import (
    PDF_ENCRYPTED,
    PDF_TIMEOUT,
    PDF_UNREADABLE,
    PdfInspection,
    PdfInspector,
    PdfPageContent,
    PdfTable,
)
from nitka.extraction.pdf_preview import PdfPreview, PreviewService
from nitka.extraction.ports import InputDocument
from nitka.extraction.registry import InputRegistry, RegisteredInput
from nitka.extraction.xlsx_issues import AMOUNT_MISSING, ROW_KIND_UNKNOWN
from nitka.extraction.xlsx_template import ReviewStatus, RowKind

PDF_TEXT_VERSION: Final[str] = "pdf-text-1"
VISION_REQUIRED: Final[str] = "VISION_REQUIRED"
IMAGE_UNREADABLE: Final[str] = "IMAGE_UNREADABLE"
PREVIEW_FAILED: Final[str] = "PREVIEW_FAILED"
TEXT_LAYER_UNUSABLE: Final[str] = "TEXT_LAYER_UNUSABLE"
TABLE_LAYOUT_UNKNOWN: Final[str] = "TABLE_LAYOUT_UNKNOWN"
PDF_SUFFIX: Final[str] = ".pdf"
IMAGE_SUFFIXES: Final[frozenset[str]] = frozenset({".png", ".jpg", ".jpeg"})
MIN_TEXT_CHARS: Final[int] = 20


@dataclass(frozen=True)
class VisionRequest:
    file_id: str
    document_id: str
    page: int | None
    image_path: Path | None
    reason: str


@dataclass(frozen=True)
class PdfExtractionPackage:
    extraction: ExtractionResult
    vision_requests: tuple[VisionRequest, ...]
    previews: tuple[PdfPreview, ...]


@dataclass
class _FileResult:
    page_count: int
    status: FileStatus
    sources: list[Source] = field(default_factory=list)
    rows: list[ExtractedRow] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    requests: list[VisionRequest] = field(default_factory=list)
    previews: list[PdfPreview] = field(default_factory=list)


@dataclass(frozen=True)
class _TableLayout:
    start_row: int
    description: int | None
    amount: int | None
    quantity: int | None
    price: int | None
    number: int | None


class PdfTextExtractor:
    def __init__(
        self,
        registry: InputRegistry,
        limits: PackageLimits = DEFAULT_PACKAGE_LIMITS,
        *,
        inspector: PdfInspector | None = None,
        preview_dir: Path,
        min_text_chars: int = MIN_TEXT_CHARS,
    ) -> None:
        if min_text_chars < 0:
            raise ValueError("min_text_chars must be >= 0")
        self.registry = registry
        self.limits = limits
        self.inspector = inspector or PdfInspector(limits.pdf_timeout_seconds)
        self.preview_dir = Path(preview_dir)
        self.previews = PreviewService(self.inspector, self.preview_dir)
        self.min_text_chars = min_text_chars

    def extract(self) -> PdfExtractionPackage:
        enforce_package_size(self.registry.inputs, self.limits)
        entries = [
            entry
            for entry in self.registry.inputs
            if entry.role == "SUPPORT"
            and entry.path.suffix.lower() in {PDF_SUFFIX, *IMAGE_SUFFIXES}
        ]
        inspections: dict[str, PdfInspection] = {}
        total_pages = 0
        for entry in entries:
            if entry.path.suffix.lower() == PDF_SUFFIX:
                inspection = self.inspector.inspect(entry.path)
                inspections[entry.file_id] = inspection
                total_pages += inspection.page_count or 0
            else:
                total_pages += 1
            if total_pages > self.limits.max_support_pages:
                raise PackageLimitError(
                    f"support page count {total_pages} exceeds "
                    f"{self.limits.max_support_pages}"
                )

        files = []
        sources: list[Source] = []
        rows: list[ExtractedRow] = []
        issues: list[Issue] = []
        requests: list[VisionRequest] = []
        previews: list[PdfPreview] = []

        for entry in entries:
            if entry.path.suffix.lower() == PDF_SUFFIX:
                result = self._pdf(entry, inspections[entry.file_id])
            else:
                result = self._image(entry)
            files.append(entry.to_file_record(result.status))
            sources.extend(result.sources)
            rows.extend(result.rows)
            issues.extend(result.issues)
            requests.extend(result.requests)
            previews.extend(result.previews)

        extraction = ExtractionResult(
            extraction_version=PDF_TEXT_VERSION,
            files=files,
            sources=sources,
            rows=rows,
            issues=issues,
        )
        validate_extraction(extraction)
        return PdfExtractionPackage(
            extraction=extraction,
            vision_requests=tuple(requests),
            previews=tuple(previews),
        )

    def _pdf(
        self, entry: RegisteredInput, inspection: PdfInspection | None = None
    ) -> _FileResult:
        inspection = inspection or self.inspector.inspect(entry.path)
        if inspection.timed_out:
            return self._failed(entry, PDF_TIMEOUT, "PDF parsing timed out")
        if inspection.encrypted:
            return self._failed(entry, PDF_ENCRYPTED, "PDF is password-protected")
        if inspection.error is not None:
            return self._failed(entry, PDF_UNREADABLE, inspection.error)
        page_count = inspection.page_count or 0
        if page_count > self.limits.max_support_pages:
            raise PackageLimitError(
                f"support page count {page_count} exceeds "
                f"{self.limits.max_support_pages}"
            )
        result = _FileResult(page_count=page_count, status="COMPLETED")
        result.previews = list(
            self.previews.render_pdf(entry, max_pages=self.limits.max_support_pages)
        )
        if not result.previews:
            result.issues.append(
                Issue(
                    code=PREVIEW_FAILED,
                    file_id=entry.file_id,
                    detail="could not render page previews",
                )
            )

        content = self.inspector.extract(entry.path)
        if not content.ok:
            return self._route_all_pages(
                entry,
                result,
                TEXT_LAYER_UNUSABLE,
                content.error or TEXT_LAYER_UNUSABLE,
            )
        pages_by_number = {page.page: page for page in content.pages}
        preview_by_page = {preview.page: preview for preview in result.previews}

        for page_number in range(1, page_count + 1):
            page = pages_by_number.get(page_number)
            if page is None:
                self._route_page(
                    entry,
                    result,
                    page_number,
                    "PAGE_MISSING",
                    preview_by_page,
                    "page content is missing",
                )
                continue
            if not self._text_usable(page):
                self._route_page(
                    entry,
                    result,
                    page_number,
                    TEXT_LAYER_UNUSABLE,
                    preview_by_page,
                    "text layer is empty, too short and has no table",
                )
                continue
            parsed = _parse_page(entry, page)
            if not parsed.rows:
                self._route_page(
                    entry,
                    result,
                    page_number,
                    "NO_POSITIONS",
                    preview_by_page,
                    "no positions were found in the text layer",
                )
                continue
            result.sources.extend(parsed.sources)
            result.rows.extend(parsed.rows)
            result.issues.extend(parsed.issues)

        if result.requests or result.issues:
            result.status = "PARTIAL"
        return result

    def _image(self, entry: RegisteredInput) -> _FileResult:
        result = _FileResult(page_count=1, status="PARTIAL")
        preview = self.previews.image_preview(entry)
        if preview is None:
            return self._failed(entry, IMAGE_UNREADABLE, "image cannot be opened")
        result.previews.append(preview)
        result.requests.append(
            VisionRequest(
                file_id=entry.file_id,
                document_id=_document_id(entry.file_id),
                page=1,
                image_path=preview.image_path,
                reason="IMAGE_SCAN",
            )
        )
        result.issues.append(
            Issue(
                code=VISION_REQUIRED,
                file_id=entry.file_id,
                detail="page 1: image scan routed to Vision",
            )
        )
        return result

    def _route_all_pages(
        self,
        entry: RegisteredInput,
        result: _FileResult,
        reason: str,
        detail: str,
    ) -> _FileResult:
        preview_by_page = {preview.page: preview for preview in result.previews}
        for page_number in range(1, result.page_count + 1):
            self._route_page(
                entry,
                result,
                page_number,
                reason,
                preview_by_page,
                detail,
            )
        result.status = "PARTIAL"
        return result

    def _route_page(
        self,
        entry: RegisteredInput,
        result: _FileResult,
        page_number: int,
        reason: str,
        preview_by_page: dict[int, PdfPreview],
        detail: str,
    ) -> None:
        preview = preview_by_page.get(page_number)
        result.requests.append(
            VisionRequest(
                file_id=entry.file_id,
                document_id=_document_id(entry.file_id),
                page=page_number,
                image_path=preview.image_path if preview else None,
                reason=reason,
            )
        )
        result.issues.append(
            Issue(
                code=VISION_REQUIRED,
                file_id=entry.file_id,
                detail=f"page {page_number}: {detail}",
            )
        )

    def _failed(self, entry: RegisteredInput, code: str, detail: str) -> _FileResult:
        return _FileResult(
            page_count=0,
            status="FAILED",
            issues=[Issue(code=code, file_id=entry.file_id, detail=detail)],
        )

    def _text_usable(self, page: PdfPageContent) -> bool:
        if any(
            cell.strip() for table in page.tables for row in table.rows for cell in row
        ):
            return True
        compact = re.sub(r"\s+", "", page.text)
        return len(compact) >= self.min_text_chars and any(
            character.isalnum() for character in compact
        )


@dataclass
class _ParsedPage:
    sources: list[Source] = field(default_factory=list)
    rows: list[ExtractedRow] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


def _parse_page(entry: RegisteredInput, page: PdfPageContent) -> _ParsedPage:
    parsed = _ParsedPage()
    for table_number, table in enumerate(page.tables, start=1):
        table_result = _parse_table(entry, page.page, table_number, table)
        parsed.sources.extend(table_result.sources)
        parsed.rows.extend(table_result.rows)
        parsed.issues.extend(table_result.issues)
    if parsed.rows:
        return parsed
    return _parse_text(entry, page)


def _parse_table(
    entry: RegisteredInput,
    page_number: int,
    table_number: int,
    table: PdfTable,
) -> _ParsedPage:
    parsed = _ParsedPage()
    rows = [tuple(_cell(cell) for cell in row) for row in table.rows]
    if not rows:
        return parsed
    layout = _layout(rows)
    if layout is None:
        layout = _infer_layout(rows)
    if layout is None:
        parsed.issues.append(
            Issue(
                code=TABLE_LAYOUT_UNKNOWN,
                file_id=entry.file_id,
                detail=f"page {page_number}, table {table_number}: "
                "no amount column could be identified",
            )
        )
        return parsed
    for row_index in range(layout.start_row, len(rows)):
        cells = rows[row_index]
        if all(not cell for cell in cells):
            continue
        if _is_header_repeat(cells):
            continue
        description = _cell_at(cells, layout.description)
        amount_value, amount_text = _amount_from_cells(cells, layout.amount)
        kind = _classify(cells, description, amount_value)
        row_id = f"R-{entry.file_id}-P{page_number}-T{table_number}-R{row_index + 1}"
        fields: dict[str, str] = {}
        for field_name, index in (
            ("description", layout.description),
            ("quantity", layout.quantity),
            ("price", layout.price),
            ("number", layout.number),
        ):
            raw = _cell_at(cells, index)
            if raw:
                source = _make_source(
                    entry,
                    page_number,
                    row_index,
                    table_number,
                    field_name,
                    raw,
                    raw,
                )
                parsed.sources.append(source)
                fields[field_name] = source.source_id
        amount_source = _make_source(
            entry,
            page_number,
            row_index,
            table_number,
            "amount",
            amount_text,
            amount_value,
            "NEEDS_REVIEW" if amount_value is None else "UNREVIEWED",
        )
        parsed.sources.append(amount_source)
        if amount_value is None and description:
            parsed.issues.append(
                Issue(
                    code=AMOUNT_MISSING,
                    file_id=entry.file_id,
                    detail=f"page {page_number}, table {table_number}, "
                    f"row {row_index + 1}: amount is empty",
                )
            )
        parsed.rows.append(
            ExtractedRow(
                row_id=row_id,
                kind=kind,
                amount_source_id=amount_source.source_id,
                fields=fields,
            )
        )
        if kind == "UNKNOWN":
            parsed.issues.append(
                Issue(
                    code=ROW_KIND_UNKNOWN,
                    file_id=entry.file_id,
                    detail=f"page {page_number}, table {table_number}, "
                    f"row {row_index + 1}: row kind is not recognized",
                )
            )
    return parsed


def _parse_text(entry: RegisteredInput, page: PdfPageContent) -> _ParsedPage:
    parsed = _ParsedPage()
    for line_number, raw_line in enumerate(page.text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        amount_match = re.search(r"(-?\d[\d\s\u00a0\u202f]*[.,]\d{2})\s*$", line)
        if amount_match is None:
            continue
        amount_text = amount_match.group(1).strip()
        parsed_amount = parse_amount(amount_text)
        if parsed_amount.value is None:
            continue
        prefix = line[: amount_match.start()].strip(" |;:-\t")
        if not prefix and "итого" not in line.casefold():
            continue
        cells = (prefix, amount_text)
        kind = _classify(cells, prefix, parsed_amount.value)
        row_id = f"R-{entry.file_id}-P{page.page}-L{line_number}"
        fields: dict[str, str] = {}
        if prefix:
            source = _make_source(
                entry,
                page.page,
                line_number,
                0,
                "description",
                prefix,
                prefix,
            )
            parsed.sources.append(source)
            fields["description"] = source.source_id
        amount_source = _make_source(
            entry,
            page.page,
            line_number,
            0,
            "amount",
            amount_text,
            parsed_amount.value,
        )
        parsed.sources.append(amount_source)
        parsed.rows.append(
            ExtractedRow(
                row_id=row_id,
                kind=kind,
                amount_source_id=amount_source.source_id,
                fields=fields,
            )
        )
        if kind == "UNKNOWN":
            parsed.issues.append(
                Issue(
                    code=ROW_KIND_UNKNOWN,
                    file_id=entry.file_id,
                    detail=f"page {page.page}, line {line_number}: row kind is unknown",
                )
            )
    return parsed


def _layout(rows: list[tuple[str, ...]]) -> _TableLayout | None:
    for header_index, header in enumerate(rows[:4]):
        cells = [cell.casefold() for cell in header]
        description = _find_column(cells, ("наимен", "описан", "услуг", "товар"))
        amount = _find_column(cells, ("сумм", "стоим"))
        quantity = _find_column(cells, ("кол", "колич"))
        price = _find_column(cells, ("цена", "тариф"))
        number = _find_column(cells, ("№", "номер"))
        if amount is not None or (
            description is not None
            and any(index is not None for index in (quantity, price, number))
        ):
            return _TableLayout(
                start_row=header_index + 1,
                description=description,
                amount=amount,
                quantity=quantity,
                price=price,
                number=number,
            )
    return None


def _infer_layout(rows: list[tuple[str, ...]]) -> _TableLayout | None:
    width = max((len(row) for row in rows), default=0)
    if width == 0:
        return None
    counts = [
        sum(1 for row in rows if index < len(row) and _is_amount(row[index]))
        for index in range(width)
    ]
    maximum = max(counts)
    if maximum == 0:
        return None
    amount = max(index for index, count in enumerate(counts) if count == maximum)
    description = next(
        (
            index
            for index in range(width)
            if any(
                index < len(row) and row[index] and not _is_amount(row[index])
                for row in rows
            )
        ),
        None,
    )
    return _TableLayout(
        start_row=0,
        description=description,
        amount=amount,
        quantity=None,
        price=None,
        number=None,
    )


def _is_header_repeat(cells: tuple[str, ...]) -> bool:
    text = " ".join(cells).casefold()
    return (
        "наимен" in text
        and "сумм" in text
        and not any(_is_amount(cell) for cell in cells)
    )


def _classify(
    cells: tuple[str, ...],
    description: str | None,
    amount: str | None,
) -> RowKind:
    label = _label_text(description)
    if not label:
        label = next(
            (_label_text(cell) for cell in cells if cell and not _is_amount(cell)),
            "",
        )
    if label.startswith(("итого", "всего")):
        return "DOCUMENT_TOTAL"
    if label.startswith(("подытог", "промежуточный итог")):
        return "PAGE_SUBTOTAL"
    if label.startswith(("ндс", "налог")):
        return "TAX_SUMMARY"
    if description and amount is not None:
        return "ITEM"
    return "UNKNOWN"


def _label_text(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", value.casefold()).strip(" .:;,-")


def _amount_from_cells(
    cells: tuple[str, ...], preferred: int | None
) -> tuple[str | None, str | None]:
    if preferred is None or not 0 <= preferred < len(cells):
        return None, None
    raw = cells[preferred]
    parsed = parse_amount(raw)
    return (parsed.value, raw) if parsed.value is not None else (None, None)


def _find_column(cells: list[str], needles: tuple[str, ...]) -> int | None:
    for index, cell in enumerate(cells):
        if any(needle in cell for needle in needles):
            return index
    return None


def _cell(value: str | None) -> str:
    return "" if value is None else str(value).strip()


def _cell_at(cells: tuple[str, ...], index: int | None) -> str | None:
    if index is None or index < 0 or index >= len(cells):
        return None
    return cells[index] or None


def _is_amount(value: str) -> bool:
    return parse_amount(value).value is not None


def _document_id(file_id: str) -> str:
    return f"D-{file_id}"


def _make_source(
    entry: RegisteredInput,
    page_number: int,
    row_index: int,
    table_number: int,
    field_name: str,
    raw: str | None,
    value: str | None,
    review_status: ReviewStatus = "UNREVIEWED",
) -> Source:
    suffix = f"T{table_number}-" if table_number else ""
    return Source(
        source_id=(
            f"S-{entry.file_id}-P{page_number}-{suffix}R{row_index}-{field_name.upper()}"
        ),
        file_id=entry.file_id,
        document_id=_document_id(entry.file_id),
        location=PageLocation(kind="PAGE", page=page_number),
        method="PDF_TEXT",
        raw_text=raw,
        value=value,
        review_status=review_status,
    )


class PdfTextPackageExtractor:
    """Extractor-protocol adapter for the support-PDF stage.

    It returns only SUPPORT file records/sources; a package collector merges
    them with the report extraction. Vision requests and previews are available
    through :meth:`extract_package`.
    """

    def __init__(
        self,
        limits: PackageLimits = DEFAULT_PACKAGE_LIMITS,
        *,
        inspector: PdfInspector | None = None,
        preview_dir: Path,
        min_text_chars: int = MIN_TEXT_CHARS,
    ) -> None:
        self.limits = limits
        self.inspector = inspector
        self.preview_dir = preview_dir
        self.min_text_chars = min_text_chars

    def __call__(self, documents: Sequence[InputDocument]) -> ExtractionResult:
        return self.extract_package(documents).extraction

    def extract_package(
        self, documents: Sequence[InputDocument]
    ) -> PdfExtractionPackage:
        if any(document.role != "SUPPORT" for document in documents):
            raise ValueError(
                "PdfTextPackageExtractor is a support-stage adapter; "
                "merge the REPORT extraction separately"
            )
        registry = InputRegistry.register(documents)
        return PdfTextExtractor(
            registry,
            self.limits,
            inspector=self.inspector,
            preview_dir=self.preview_dir,
            min_text_chars=self.min_text_chars,
        ).extract()
