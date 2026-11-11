from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Amount = Annotated[str, StringConstraints(pattern=r"^-?[0-9]{1,18}(\.[0-9]{1,6})?$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
FileRole = Literal["REPORT", "SUPPORT", "TECHNICAL", "ORACLE"]
FileStatus = Literal["COMPLETED", "PARTIAL", "FAILED"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileRecord(Contract):
    file_id: str
    name: str
    sha256: Sha256
    role: FileRole
    status: FileStatus
    size_bytes: int | None = Field(default=None, ge=0)


class PageLocation(Contract):
    kind: Literal["PAGE"]
    page: int = Field(ge=1)


class CellLocation(Contract):
    kind: Literal["CELL"]
    sheet: str | None = None
    cell: str


class OcrLocation(Contract):
    """OCR provenance: page plus optional pixel bounding box."""

    kind: Literal["OCR"]
    page: int = Field(ge=1)
    bbox: tuple[float, float, float, float] | None = None


class DocxLocation(Contract):
    """DOCX provenance: paragraph or table/row/cell address."""

    kind: Literal["DOCX"]
    paragraph: int | None = Field(default=None, ge=0)
    table: int | None = Field(default=None, ge=0)
    row: int | None = Field(default=None, ge=0)
    cell: int | None = Field(default=None, ge=0)


class Source(Contract):
    source_id: str
    file_id: str
    document_id: str
    location: Annotated[
        PageLocation | CellLocation | OcrLocation | DocxLocation,
        Field(discriminator="kind"),
    ]
    method: Literal["TABLE", "PDF_TEXT", "VISION", "MANUAL", "OCR", "DOCX"]
    raw_text: str | None
    value: str | None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    review_status: Literal["UNREVIEWED", "NEEDS_REVIEW", "CONFIRMED", "CORRECTED"]


class ExtractedRow(Contract):
    row_id: str
    kind: Literal["ITEM", "PAGE_SUBTOTAL", "DOCUMENT_TOTAL", "TAX_SUMMARY", "UNKNOWN"]
    amount_source_id: str
    fields: dict[str, str] = Field(default_factory=dict)


class Issue(Contract):
    code: str
    file_id: str | None = None
    detail: str


class ExtractionResult(Contract):
    extraction_version: str
    files: list[FileRecord]
    sources: list[Source]
    rows: list[ExtractedRow]
    issues: list[Issue] = Field(default_factory=list)

    @classmethod
    def model_validate_json(
        cls, json_data: str | bytes | bytearray, **kwargs: Any
    ) -> Self:
        """Parse JSON and check referential integrity on the way in."""
        result = super().model_validate_json(json_data, **kwargs)
        from nitka.contracts.validation import validate_extraction

        validate_extraction(result)
        return result
