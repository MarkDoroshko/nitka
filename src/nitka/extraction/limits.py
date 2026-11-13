"""Bounds for untrusted document packages and parsers.

The byte limit is checked from the input registry before extraction or
snapshotting. Page and workbook limits are enforced separately by the PDF and
XLSX adapters, before the expensive work starts.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from nitka.extraction.registry import RegisteredInput

MAX_PACKAGE_BYTES: Final[int] = 25 * 1024 * 1024
MAX_SUPPORT_PAGES: Final[int] = 20
OCR_PDF_DPI: Final[float] = 200.0
MAX_PREVIEW_PIXELS: Final[int] = 24_000_000
DEFAULT_PDF_TIMEOUT_SECONDS: Final[float] = 30.0

MAX_XLSX_UNCOMPRESSED_BYTES: Final[int] = 64 * 1024 * 1024
MAX_XLSX_ROWS: Final[int] = 100_000
MAX_XLSX_COLUMNS: Final[int] = 256
MAX_XLSX_CELLS: Final[int] = 1_000_000


class PackageLimitError(RuntimeError):
    """The package exceeds a limit and must not be processed."""


@dataclass(frozen=True)
class PackageLimits:
    max_total_bytes: int = MAX_PACKAGE_BYTES
    max_support_pages: int = MAX_SUPPORT_PAGES
    pdf_timeout_seconds: float = DEFAULT_PDF_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if self.max_total_bytes <= 0:
            raise ValueError("max_total_bytes must be positive")
        if self.max_support_pages <= 0:
            raise ValueError("max_support_pages must be positive")
        if self.pdf_timeout_seconds <= 0:
            raise ValueError("pdf_timeout_seconds must be positive")


@dataclass(frozen=True)
class XlsxLimits:
    max_uncompressed_bytes: int = MAX_XLSX_UNCOMPRESSED_BYTES
    max_rows: int = MAX_XLSX_ROWS
    max_columns: int = MAX_XLSX_COLUMNS
    max_cells: int = MAX_XLSX_CELLS

    def __post_init__(self) -> None:
        if self.max_uncompressed_bytes <= 0:
            raise ValueError("max_uncompressed_bytes must be positive")
        if self.max_rows <= 0 or self.max_columns <= 0 or self.max_cells <= 0:
            raise ValueError("XLSX dimensions must be positive")


DEFAULT_PACKAGE_LIMITS: Final[PackageLimits] = PackageLimits()
DEFAULT_XLSX_LIMITS: Final[XlsxLimits] = XlsxLimits()


def enforce_package_size(
    entries: Iterable[RegisteredInput],
    limits: PackageLimits = DEFAULT_PACKAGE_LIMITS,
) -> int:
    """Return the total size and reject a package above the byte limit."""
    total = sum(entry.size_bytes for entry in entries)
    if total > limits.max_total_bytes:
        raise PackageLimitError(
            f"package size {total} bytes exceeds {limits.max_total_bytes} bytes"
        )
    return total
