"""PDF inspection and rendering behind a subprocess boundary.

PDF libraries can hang, crash or request a password on hostile input. This
module runs them in a short-lived child process with a timeout. Rendering is
sequential inside that child; a bad document is reported per file and does not
stop the rest of the package.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, Protocol, Sequence

from nitka.contracts.extraction import ExtractionResult, Issue
from nitka.extraction.limits import (
    DEFAULT_PACKAGE_LIMITS,
    PackageLimitError,
    PackageLimits,
    enforce_package_size,
)
from nitka.extraction.registry import InputRegistry, RegisteredInput

PDF_TIMEOUT: Final[str] = "PDF_TIMEOUT"
PDF_ENCRYPTED: Final[str] = "PDF_ENCRYPTED"
PDF_UNREADABLE: Final[str] = "PDF_UNREADABLE"
PDF_RENDER_FAILED: Final[str] = "PDF_RENDER_FAILED"
PDF_IMAGE_SUFFIXES: Final[frozenset[str]] = frozenset({".png", ".jpg", ".jpeg"})
PDF_SUFFIX: Final[str] = ".pdf"


@dataclass(frozen=True)
class PdfInspection:
    page_count: int | None
    encrypted: bool = False
    error: str | None = None
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return not self.timed_out and not self.encrypted and self.error is None


@dataclass(frozen=True)
class PdfRenderResult:
    pages: tuple[Path, ...]
    error: str | None = None
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.error is None


@dataclass(frozen=True)
class PdfTable:
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class PdfPageContent:
    page: int
    text: str
    tables: tuple[PdfTable, ...]
    width: float = 0.0
    height: float = 0.0
    rotation: int = 0


@dataclass(frozen=True)
class PdfContentResult:
    pages: tuple[PdfPageContent, ...]
    error: str | None = None
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.error is None


@dataclass(frozen=True)
class PdfFileReport:
    file_id: str
    name: str
    page_count: int | None
    status: Literal["COMPLETED", "FAILED"]
    code: str | None
    detail: str


class _WorkerTimeout(RuntimeError):
    pass


class _WorkerFailure(RuntimeError):
    pass


class PdfInspectionPort(Protocol):
    def inspect(self, path: Path) -> PdfInspection: ...


class PdfInspector:
    """Run PDF work in a child process with a hard timeout."""

    def __init__(
        self,
        timeout_seconds: float = DEFAULT_PACKAGE_LIMITS.pdf_timeout_seconds,
        *,
        worker_command: Sequence[str] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds
        self.worker_command = tuple(
            worker_command or (sys.executable, "-m", "nitka.extraction.pdf_worker")
        )
        if not self.worker_command:
            raise ValueError("worker_command must not be empty")

    def inspect(self, path: Path) -> PdfInspection:
        try:
            completed = self._run(("inspect", str(path)))
        except _WorkerTimeout:
            return PdfInspection(None, error=PDF_TIMEOUT, timed_out=True)
        except _WorkerFailure as exc:
            return PdfInspection(None, error=f"PDF_WORKER_FAILED: {exc}")

        payload = _payload(completed)
        if completed.returncode != 0:
            return PdfInspection(
                None,
                error=f"PDF_WORKER_FAILED: exit {completed.returncode}"
                f" {completed.stderr.strip()}",
            )
        if payload is None:
            return PdfInspection(None, error=PDF_UNREADABLE)
        if not payload.get("ok"):
            return PdfInspection(
                None, error=str(payload.get("error") or PDF_UNREADABLE)
            )
        if payload.get("encrypted"):
            return PdfInspection(None, encrypted=True)
        pages = payload.get("pages")
        if not isinstance(pages, int) or pages < 0:
            return PdfInspection(None, error=PDF_UNREADABLE)
        return PdfInspection(pages)

    def render(
        self,
        path: Path,
        output_dir: Path,
        *,
        dpi: float = 150.0,
        max_pages: int = DEFAULT_PACKAGE_LIMITS.max_support_pages,
        page: int | None = None,
    ) -> PdfRenderResult:
        try:
            completed = self._run(
                (
                    "render",
                    str(path),
                    str(output_dir),
                    "--dpi",
                    str(dpi),
                    "--max-pages",
                    str(max_pages),
                    *(("--page", str(page)) if page is not None else ()),
                )
            )
        except _WorkerTimeout:
            return PdfRenderResult((), PDF_TIMEOUT, timed_out=True)
        except _WorkerFailure as exc:
            return PdfRenderResult((), f"PDF_WORKER_FAILED: {exc}")

        payload = _payload(completed)
        if completed.returncode != 0:
            return PdfRenderResult(
                (),
                f"PDF_WORKER_FAILED: exit {completed.returncode}"
                f" {completed.stderr.strip()}",
            )
        if payload is None or not payload.get("ok"):
            detail = (
                str(payload.get("error") or PDF_RENDER_FAILED)
                if payload is not None
                else PDF_RENDER_FAILED
            )
            return PdfRenderResult((), detail)
        files = payload.get("files")
        if not isinstance(files, list):
            return PdfRenderResult((), PDF_RENDER_FAILED)
        return PdfRenderResult(tuple(Path(str(item)) for item in files))

    def extract(self, path: Path) -> PdfContentResult:
        try:
            completed = self._run(("extract", str(path)))
        except _WorkerTimeout:
            return PdfContentResult((), PDF_TIMEOUT, timed_out=True)
        except _WorkerFailure as exc:
            return PdfContentResult((), f"PDF_WORKER_FAILED: {exc}")

        payload = _payload(completed)
        if completed.returncode != 0:
            return PdfContentResult(
                (),
                f"PDF_WORKER_FAILED: exit {completed.returncode}"
                f" {completed.stderr.strip()}",
            )
        if payload is None or not payload.get("ok"):
            return PdfContentResult(
                (),
                str(payload.get("error") or PDF_UNREADABLE)
                if payload is not None
                else PDF_UNREADABLE,
            )
        raw_pages = payload.get("pages")
        if not isinstance(raw_pages, list):
            return PdfContentResult((), PDF_UNREADABLE)
        pages: list[PdfPageContent] = []
        for raw_page in raw_pages:
            if not isinstance(raw_page, dict):
                return PdfContentResult((), PDF_UNREADABLE)
            page_number = raw_page.get("page")
            text = raw_page.get("text")
            raw_tables = raw_page.get("tables")
            if (
                not isinstance(page_number, int)
                or not isinstance(text, str)
                or not isinstance(raw_tables, list)
            ):
                return PdfContentResult((), PDF_UNREADABLE)
            tables: list[PdfTable] = []
            for raw_table in raw_tables:
                if not isinstance(raw_table, list):
                    return PdfContentResult((), PDF_UNREADABLE)
                rows: list[tuple[str, ...]] = []
                for raw_row in raw_table:
                    if not isinstance(raw_row, list):
                        return PdfContentResult((), PDF_UNREADABLE)
                    rows.append(tuple(str(cell) for cell in raw_row))
                tables.append(PdfTable(tuple(rows)))
            try:
                width = float(raw_page.get("width") or 0.0)
                height = float(raw_page.get("height") or 0.0)
                rotation = int(raw_page.get("rotation") or 0)
            except (TypeError, ValueError, OverflowError):
                return PdfContentResult((), PDF_UNREADABLE)
            pages.append(
                PdfPageContent(
                    page=page_number,
                    text=text,
                    tables=tuple(tables),
                    width=width,
                    height=height,
                    rotation=rotation,
                )
            )
        return PdfContentResult(tuple(pages))

    def _run(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [*self.worker_command, *arguments],
                capture_output=True,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise _WorkerTimeout(str(exc)) from exc
        except OSError as exc:
            raise _WorkerFailure(str(exc)) from exc


def _payload(completed: subprocess.CompletedProcess[str]) -> dict[str, Any] | None:
    lines = [line for line in completed.stdout.split("\n") if line.strip()]
    if not lines:
        return None
    try:
        parsed = json.loads(lines[-1])
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


class PdfSupportExtractor:
    """Inspect support PDFs/images and return isolated file diagnostics.

    It intentionally produces no text sources yet: this task only establishes
    the bounded, isolated PDF path. A collector can merge these ``files`` and
    ``issues`` with the report extraction and continue with the good files.
    """

    def __init__(
        self,
        registry: InputRegistry,
        limits: PackageLimits = DEFAULT_PACKAGE_LIMITS,
        *,
        inspector: PdfInspectionPort | None = None,
    ) -> None:
        self.registry = registry
        self.limits = limits
        self.inspector = inspector or PdfInspector(limits.pdf_timeout_seconds)

    def inspect(self) -> list[PdfFileReport]:
        enforce_package_size(self.registry.inputs, self.limits)
        reports: list[PdfFileReport] = []
        total_pages = 0
        for entry in self.registry.inputs:
            if entry.role != "SUPPORT":
                continue
            suffix = entry.path.suffix.lower()
            if suffix == PDF_SUFFIX:
                report = self._inspect_pdf(entry)
            elif suffix in PDF_IMAGE_SUFFIXES:
                report = PdfFileReport(
                    file_id=entry.file_id,
                    name=entry.name,
                    page_count=1,
                    status="COMPLETED",
                    code=None,
                    detail="image counted as one page",
                )
            else:
                continue
            if report.page_count is not None:
                total_pages += report.page_count
                if total_pages > self.limits.max_support_pages:
                    raise PackageLimitError(
                        f"support page count {total_pages} exceeds "
                        f"{self.limits.max_support_pages}"
                    )
            reports.append(report)
        return reports

    def extract(self) -> ExtractionResult:
        reports = self.inspect()
        files = []
        issues: list[Issue] = []
        for report in reports:
            entry = self._entry(report.file_id)
            files.append(entry.to_file_record(report.status))
            if report.code is not None:
                issues.append(
                    Issue(
                        code=report.code,
                        file_id=report.file_id,
                        detail=report.detail,
                    )
                )
        return ExtractionResult(
            extraction_version="pdf-support-1",
            files=files,
            sources=[],
            rows=[],
            issues=issues,
        )

    def _inspect_pdf(self, entry: RegisteredInput) -> PdfFileReport:
        inspection = self.inspector.inspect(entry.path)
        if inspection.timed_out:
            return PdfFileReport(
                entry.file_id,
                entry.name,
                None,
                "FAILED",
                PDF_TIMEOUT,
                f"PDF parsing exceeded {self.limits.pdf_timeout_seconds} s",
            )
        if inspection.encrypted:
            return PdfFileReport(
                entry.file_id,
                entry.name,
                None,
                "FAILED",
                PDF_ENCRYPTED,
                "PDF is password-protected",
            )
        if inspection.error is not None:
            return PdfFileReport(
                entry.file_id,
                entry.name,
                None,
                "FAILED",
                PDF_UNREADABLE,
                inspection.error,
            )
        if inspection.page_count is None:
            return PdfFileReport(
                entry.file_id,
                entry.name,
                None,
                "FAILED",
                PDF_UNREADABLE,
                "page count is unavailable",
            )
        return PdfFileReport(
            entry.file_id,
            entry.name,
            inspection.page_count,
            "COMPLETED",
            None,
            f"{inspection.page_count} page(s)",
        )

    def _entry(self, file_id: str) -> RegisteredInput:
        for entry in self.registry.inputs:
            if entry.file_id == file_id:
                return entry
        raise KeyError(f"Unknown file ID: {file_id}")


def inspect_support_pdfs(
    registry: InputRegistry,
    limits: PackageLimits = DEFAULT_PACKAGE_LIMITS,
    *,
    inspector: PdfInspectionPort | None = None,
) -> list[PdfFileReport]:
    return PdfSupportExtractor(registry, limits, inspector=inspector).inspect()
