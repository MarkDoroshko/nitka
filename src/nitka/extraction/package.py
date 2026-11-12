"""Compose the local KFZ, payroll and primary-document adapters."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from typing import Literal
from zlib import error as ZipDataError

from nitka.contracts.extraction import (
    CellLocation,
    DocxLocation,
    ExtractedRow,
    ExtractionResult,
    FileRole,
    FileStatus,
    Issue,
    OcrLocation,
    PageLocation,
    Source,
)
from nitka.contracts.inputs import InputManifest
from nitka.contracts.profile import CaseProfile
from nitka.contracts.report_scope import (
    PACKAGE_EXTRACTION_VERSION,
    REPORT_SCOPE_UNVERIFIED,
    report_scope_issues,
)
from nitka.contracts.validation import validate_extraction
from nitka.extraction.docx_support import DocxLocation as NativeDocxLocation
from nitka.extraction.kfz import KfzFragment, KfzResult, kfz_header_value, parse_kfz
from nitka.extraction.limits import (
    DEFAULT_PACKAGE_LIMITS,
    PackageLimits,
    enforce_package_size,
)
from nitka.extraction.payroll import PayrollResult, extract_payroll
from nitka.extraction.pdf import PDF_IMAGE_SUFFIXES, PDF_SUFFIX, PdfInspector
from nitka.extraction.ports import InputDocument
from nitka.extraction.registry import InputReadError, InputRegistry, RegisteredInput
from nitka.extraction.support_documents import (
    ImageRecognizer,
    SupportDocument,
    SupportDocumentsExtractor,
    SupportField,
    SupportPageLocation,
)
from nitka.ocr import OcrError
from nitka.ocr.engine import OcrProvenance

DocumentEvidence = KfzResult | PayrollResult | SupportDocument
SourceMethod = Literal["TABLE", "PDF_TEXT", "OCR", "DOCX"]
SourceLocation = CellLocation | PageLocation | OcrLocation | DocxLocation
RowKind = Literal["ITEM", "DOCUMENT_TOTAL"]


@dataclass(frozen=True)
class PackageExtraction:
    """Portable manifest, contract result and native evidence by file ID.

    Native evidence also keeps model provenance, all OCR fragments and PDF line
    numbers. The contract result includes the source anchors used by consumers.
    Raster evidence lives in ``preview_dir / file_id``. The caller owns the
    preview directory lifecycle.
    """

    extraction: ExtractionResult
    manifest: InputManifest
    evidence: Mapping[str, DocumentEvidence]
    preview_dir: Path | None = None


class PackageExtractor:
    """Run the case adapters on local files with a caller-supplied local OCR engine."""

    def __init__(
        self,
        profile: CaseProfile,
        *,
        ocr: ImageRecognizer,
        preview_dir: Path,
        limits: PackageLimits = DEFAULT_PACKAGE_LIMITS,
    ) -> None:
        self._ocr = ocr
        self._profile = profile
        self._preview_dir = _preview_path(preview_dir)
        self._limits = limits
        self._pdf = PdfInspector(limits.pdf_timeout_seconds)

    def __call__(self, documents: Sequence[InputDocument], /) -> ExtractionResult:
        return self.extract_package(documents).extraction

    def extract_package(self, documents: Sequence[InputDocument]) -> PackageExtraction:
        """Register the working inputs once and keep failures local to each file."""
        accepted = [
            document for document in documents if self._is_working(document.role)
        ]
        registry, read_errors = InputRegistry.register_package(accepted)
        issues = [Issue(code="INPUT_UNREADABLE", detail=str(error)) for error in read_errors]
        submitted_reports = sum(document.role == "REPORT" for document in accepted)
        readable_reports = sum(entry.role == "REPORT" for entry in registry.inputs)
        if readable_reports != submitted_reports:
            issues.append(Issue(
                code=REPORT_SCOPE_UNVERIFIED,
                detail=f"Could read only {readable_reports} of {submitted_reports} submitted reports",
            ))
        return self._extract_registered(registry, issues)

    def extract_registered(self, registry: InputRegistry) -> PackageExtraction:
        """Use a caller-owned registry, preserving its file IDs and manifest."""
        working = [entry for entry in registry.inputs if self._is_working(entry.role)]
        if len(working) != len(registry.inputs):
            registry = InputRegistry(working)
        return self._extract_registered(registry, [])

    def _is_working(self, role: FileRole) -> bool:
        return role in ("REPORT", "SUPPORT") and self._profile.is_working_role(role)

    def _extract_registered(
        self, registry: InputRegistry, issues: list[Issue]
    ) -> PackageExtraction:
        enforce_package_size(registry.inputs, self._limits)
        if not registry.inputs:
            return PackageExtraction(
                ExtractionResult(
                    extraction_version=PACKAGE_EXTRACTION_VERSION,
                    files=[],
                    sources=[],
                    rows=[],
                    issues=issues,
                ),
                registry.manifest,
                {},
            )

        if any(
            not re.fullmatch(r"[A-Za-z0-9_-]+", entry.file_id)
            for entry in registry.inputs
        ):
            raise ValueError("Unsafe file ID in input registry")
        self._preview_dir.mkdir(parents=True, exist_ok=True)
        previews = Path(mkdtemp(prefix="package-", dir=self._preview_dir))
        statuses: dict[str, FileStatus] = {}
        with TemporaryDirectory(prefix="inputs-", dir=previews) as temporary:
            snapshots = []
            for entry in registry.inputs:
                suffix = entry.path.suffix.lower()
                if entry.role == "SUPPORT" and suffix in PDF_IMAGE_SUFFIXES:
                    image_dir = previews / entry.file_id
                    image_dir.mkdir()
                    destination = image_dir / f"source{suffix}"
                else:
                    destination = Path(temporary) / f"{entry.file_id}{suffix}"
                try:
                    snapshots.append(entry.snapshot(destination))
                except InputReadError as exc:
                    destination.unlink(missing_ok=True)
                    statuses[entry.file_id] = "FAILED"
                    issues.append(
                        Issue(
                            code="INPUT_CHANGED", file_id=entry.file_id, detail=str(exc)
                        )
                    )
            return self._compose(
                registry, InputRegistry(snapshots), previews, statuses, issues
            )

    def _compose(
        self,
        original: InputRegistry,
        snapshots: InputRegistry,
        previews: Path,
        statuses: dict[str, FileStatus],
        issues: list[Issue],
    ) -> PackageExtraction:
        support = SupportDocumentsExtractor(
            self._ocr, preview_dir=previews, limits=self._limits, inspector=self._pdf
        ).extract(snapshots)
        evidence: dict[str, DocumentEvidence] = {
            item.file.file_id: item for item in support
        }
        sources: list[Source] = []
        rows: list[ExtractedRow] = []
        for entry in snapshots.inputs:
            projected = _Projection(entry.file_id)
            native = evidence.get(entry.file_id)
            suffix = entry.path.suffix.lower()
            if isinstance(native, SupportDocument):
                status = _project_support(native, projected)
            elif entry.role == "SUPPORT" and suffix == ".xlsx":
                payroll = _read_payroll(entry.path)
                evidence[entry.file_id] = payroll
                status = _project_payroll(payroll, projected, self._profile.contract)
            elif entry.role == "REPORT" and suffix == PDF_SUFFIX:
                kfz = self._read_kfz(entry, previews / entry.file_id)
                evidence[entry.file_id] = kfz
                status = _project_kfz(kfz, projected, self._profile.article_codes)
            else:
                projected.issue(
                    "UNSUPPORTED_INPUT", f"Unsupported {entry.role} format: {suffix}"
                )
                status = "FAILED"

            statuses[entry.file_id] = status
            sources.extend(projected.sources)
            rows.extend(projected.rows)
            issues.extend(projected.issues)

        for first, *copies in snapshots.duplicate_groups():
            issues.extend(
                Issue(
                    code="DUPLICATE_INPUT",
                    file_id=copy,
                    detail=f"Same bytes as {first}",
                )
                for copy in copies
            )

        extraction = ExtractionResult(
            extraction_version=PACKAGE_EXTRACTION_VERSION,
            files=original.file_records(statuses),
            sources=sources,
            rows=rows,
            issues=issues,
        )
        validate_extraction(extraction)
        if any(file.role == "REPORT" for file in extraction.files):
            extraction.issues.extend(report_scope_issues(extraction, self._profile))
        return PackageExtraction(extraction, original.manifest, evidence, previews)

    def _read_kfz(self, entry: RegisteredInput, previews: Path) -> KfzResult:
        rendered = self._pdf.render(
            entry.path, previews, dpi=200, max_pages=self._limits.max_support_pages
        )
        if not rendered.ok or not rendered.pages:
            return KfzResult(
                None, {}, (), (rendered.error or "KFZ PDF has no rendered pages",)
            )
        try:
            pages = [self._ocr.recognize(path) for path in rendered.pages]
        except OcrError as exc:
            return KfzResult(None, {}, (), (str(exc),))
        return parse_kfz(
            pages, stage=self._profile.target_stage, year=self._profile.target_year
        )


def _preview_path(path: Path) -> Path:
    """Allow digest-based subdirectories beneath long Windows workspace paths."""
    path = path.resolve()
    if os.name != "nt":
        return path
    absolute = str(path)
    if absolute.startswith("\\\\?\\"):
        return path
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


@dataclass
class _Projection:
    file_id: str
    sources: list[Source] = field(default_factory=list)
    rows: list[ExtractedRow] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    def source(
        self,
        key: str,
        location: SourceLocation,
        method: SourceMethod,
        raw: str | None,
        value: str | None,
        *,
        needs_review: bool = False,
        confidence: float | None = None,
    ) -> str:
        source_id = f"S-{self.file_id}-{key}"
        self.sources.append(
            Source(
                source_id=source_id,
                file_id=self.file_id,
                document_id=f"D-{self.file_id}",
                location=location,
                method=method,
                raw_text=raw,
                value=value,
                confidence=confidence,
                review_status="NEEDS_REVIEW" if needs_review else "UNREVIEWED",
            )
        )
        return source_id

    def row(self, key: str, kind: RowKind, amount: str, fields: dict[str, str]) -> None:
        self.rows.append(
            ExtractedRow(
                row_id=f"R-{self.file_id}-{key}",
                kind=kind,
                amount_source_id=amount,
                fields=fields,
            )
        )

    def issue(self, code: str, detail: str) -> None:
        self.issues.append(Issue(code=code, file_id=self.file_id, detail=detail))

    def status(self) -> FileStatus:
        if not self.sources:
            return "FAILED"
        return "PARTIAL" if self.issues else "COMPLETED"

    def ocr_evidence(
        self, fields: dict[str, str], page: int, provenance: OcrProvenance
    ) -> None:
        key = f"evidence_ocr_profile_{page}"
        fields[key] = self.source(
            key, OcrLocation(kind="OCR", page=page), "OCR",
            None, provenance.model_dump_json(),
        )


def _project_kfz(
    result: KfzResult, projected: _Projection, article_codes: Sequence[str]
) -> FileStatus:
    for detail in result.issues:
        projected.issue("KFZ_EXTRACTION", detail)
    fields: dict[str, str] = {}
    for key, fragments in result.header.items():
        if fragments:
            value, issue = (
                kfz_header_value(key, fragments)
                if key in {"contract", "printed_period"} else (None, None)
            )
            if issue is not None:
                projected.issue("KFZ_HEADER_REVIEW", f"{key}: {issue}")
            fields[key] = _kfz_source(
                projected, fields, key, fragments, value, needs_review=issue is not None
            )
    for page, provenance in enumerate(result.provenance, start=1):
        projected.ocr_evidence(fields, page, provenance)
    if result.row is not None:
        for detail in result.row.arithmetic_issues:
            projected.issue("KFZ_ARITHMETIC_MISMATCH", detail)
        fields["selected_row"] = _kfz_source(
            projected,
            fields,
            "selected_row",
            result.row.label,
            " ".join(fragment.block.text for fragment in result.row.label),
            needs_review=bool(result.issues),
        )
        fields["fragment"] = fields["selected_row"]
        for key, cell in result.row.cells.items():
            if cell.issue:
                projected.issue("KFZ_CELL_REVIEW", f"{key}: {cell.issue}")
            # Missing cells have no literal evidence. The native result retains
            # the issue and headers for locating the cell in the selected row.
            if cell.fragments:
                fields[key] = _kfz_source(
                    projected, fields, key, cell.fragments, cell.value, bool(cell.issue)
                )
            _kfz_fragments(projected, fields, f"{key}_heading", cell.headings)
        total = fields.pop("total", None)
        if total is not None:
            projected.row("kfz-total", "DOCUMENT_TOTAL", total, fields)
        for configured_code in article_codes:
            code = configured_code.strip()
            article_cell = result.row.cells.get(code)
            if article_cell is None or not article_cell.headings:
                continue
            article = _kfz_source(
                projected, fields, f"article-{code}", article_cell.headings, code
            )
            amount = fields.get(code)
            if amount is None:
                amount = projected.source(
                    code,
                    OcrLocation(kind="OCR", page=result.row.label[0].page),
                    "OCR",
                    None,
                    None,
                    needs_review=True,
                )
            projected.row(code, "ITEM", amount, {**fields, "article_code": article})
    return projected.status()


def _kfz_source(
    projected: _Projection,
    fields: dict[str, str],
    key: str,
    fragments: tuple[KfzFragment, ...],
    value: str | None,
    needs_review: bool = False,
) -> str:
    page = fragments[0].page
    if len(fragments) > 1:
        _kfz_fragments(projected, fields, key, fragments)
    bbox = None
    if all(fragment.page == page for fragment in fragments):
        boxes = [fragment.block.bbox for fragment in fragments]
        bbox = (
            min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes),
        )
    return projected.source(
        key,
        OcrLocation(kind="OCR", page=page, bbox=bbox),
        "OCR",
        " ".join(fragment.block.text for fragment in fragments),
        value,
        needs_review=needs_review,
        confidence=min(fragment.block.confidence for fragment in fragments),
    )


def _kfz_fragments(
    projected: _Projection,
    fields: dict[str, str],
    key: str,
    fragments: Sequence[KfzFragment],
) -> None:
    for index, fragment in enumerate(fragments, start=1):
        part = f"evidence_{key}_{index}"
        fields[part] = projected.source(
            part,
            OcrLocation(kind="OCR", page=fragment.page, bbox=fragment.block.bbox),
            "OCR", fragment.block.text, None, confidence=fragment.block.confidence,
        )


def _project_payroll(
    result: PayrollResult, projected: _Projection, configured_contract: str
) -> FileStatus:
    for detail in result.issues:
        projected.issue("PAYROLL_EXTRACTION", detail)
    fields = {
        "service_period" if key == "period" else key: projected.source(
            key, cell.location, "TABLE", cell.raw_text, cell.value
        )
        for key, cell in result.requisites.items()
    }
    project = result.requisites.get("project")
    if project is not None and project.value is not None:
        # The same-form Project field carries the agreement number in this
        # form. Free-text project names do not establish an agreement.
        value = project.value
        if re.fullmatch(r"\d+(?:/\d+)+-\d+", value) or (
            value.casefold() == configured_contract.casefold()
        ):
            fields["contract"] = fields["project"]
    if result.amounts and "contract" not in fields:
        projected.issue(
            "PAYROLL_CONTRACT_UNCONFIRMED",
            "No contract identifier in the Project field",
        )
    if (base := result.contribution_base) is not None:
        fields["contribution_base"] = projected.source(
            "contribution_base",
            base.location,
            "TABLE",
            base.raw_text,
            base.value,
            needs_review=base.value is None,
        )
    for code, cell in result.amounts.items():
        amount = projected.source(
            code,
            cell.location,
            "TABLE",
            cell.raw_text,
            cell.value,
            needs_review=cell.value is None,
        )
        # The adapter verifies the printed code in the form before adding an
        # amount. The workbook's original code formatting is not exposed.
        column = "C" if result.kind == "CONTRIBUTIONS" else "A"
        code_location = CellLocation(
            kind="CELL",
            sheet=cell.location.sheet,
            cell=f"{column}{cell.location.cell[1:]}",
        )
        code_source = projected.source(
            f"article-{code}", code_location, "TABLE", None, code
        )
        fields[f"component_{code}"] = amount
        fields[f"component_code_{code}"] = code_source
    if result.kind == "CONTRIBUTIONS" and "0300" in result.amounts:
        projected.row(
            "0300",
            "ITEM",
            fields["component_0300"],
            {
                **fields,
                "article_code": fields["component_code_0300"],
            },
        )
    elif result.kind == "PAYROLL" and "0201" in result.amounts:
        anchor = result.amounts["0201"].location
        other = result.amounts.get("0202")
        if other is not None and other.location.sheet == anchor.sheet:
            anchor = CellLocation(
                kind="CELL", sheet=anchor.sheet, cell=f"{anchor.cell}:{other.location.cell}"
            )
        amount = projected.source(
            "monthly_payroll",
            anchor,
            "TABLE",
            None,
            format(result.monthly_amount, "f")
            if result.monthly_amount is not None
            else None,
            needs_review=result.monthly_amount is None,
        )
        article = projected.source("article-0200", anchor, "TABLE", None, "0200")
        projected.row("0200", "ITEM", amount, {**fields, "article_code": article})
    if (total := result.reported_total) is not None:
        amount = projected.source(
            "reported_total",
            total.location,
            "TABLE",
            total.raw_text,
            total.value,
            needs_review=total.value is None
            or result.monthly_amount != Decimal(total.value),
        )
        projected.row("reported_total", "DOCUMENT_TOTAL", amount, fields)
    return projected.status()


def _read_payroll(path: Path) -> PayrollResult:
    try:
        return extract_payroll(path)
    except (RuntimeError, ZipDataError) as exc:
        # Encrypted ZIP members and invalid compression data can fail while
        # openpyxl opens the workbook, before the adapter can inspect a form.
        return PayrollResult(issues=(f"Unreadable XLSX: {exc}",))


def _project_support(result: SupportDocument, projected: _Projection) -> FileStatus:
    projected.issues.extend(result.issues)
    aliases = {
        "number": "document_number",
        "date": "document_date",
        "period": "service_period",
    }
    fields: dict[str, str] = {}
    amount = None
    for page, provenance in result.ocr_provenance.items():
        if result.fields:
            projected.ocr_evidence(fields, page, provenance)
    for key, item in result.fields.items():
        if not item.fragments:
            continue
        source_id = _support_source(projected, key, item)
        if len(item.fragments) > 1:
            for index, fragment in enumerate(item.fragments, start=1):
                part = f"evidence_{key}_{index}"
                fields[part] = _support_source(
                    projected, part, SupportField(None, (fragment,), item.needs_review)
                )
        if key == "amount":
            amount = source_id
        elif item.value is not None:
            fields[aliases.get(key, key)] = source_id
        else:
            fields[f"evidence_{key}"] = source_id
    if amount is not None:
        fields["fragment"] = fields.get("purpose", amount)
        projected.row("primary", "ITEM", amount, fields)
    return result.file.status


def _support_source(projected: _Projection, key: str, item: SupportField) -> str:
    anchor = item.fragments[0].location
    location: SourceLocation
    method: SourceMethod
    if isinstance(anchor, NativeDocxLocation):
        location = DocxLocation(
            kind="DOCX",
            paragraph=anchor.paragraph,
            table=anchor.table,
            row=anchor.row,
            cell=anchor.cell,
        )
        method = "DOCX"
    elif anchor.bbox is not None:
        boxes = [
            part.location.bbox for part in item.fragments
            if isinstance(part.location, SupportPageLocation)
            and part.location.page == anchor.page and part.location.bbox is not None
        ]
        bbox = None
        if len(boxes) == len(item.fragments):
            bbox = (
                min(box[0] for box in boxes), min(box[1] for box in boxes),
                max(box[2] for box in boxes), max(box[3] for box in boxes),
            )
        location = OcrLocation(kind="OCR", page=anchor.page, bbox=bbox)
        method = "OCR"
    else:
        location = PageLocation(kind="PAGE", page=anchor.page)
        method = "PDF_TEXT"
    return projected.source(
        key,
        location,
        method,
        "\n".join(fragment.text for fragment in item.fragments),
        item.value,
        needs_review=item.needs_review or item.value is None,
        confidence=min(
            (part.confidence for part in item.fragments if part.confidence is not None),
            default=None,
        ),
    )
