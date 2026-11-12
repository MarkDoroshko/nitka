"""Extract a selected stage/year row from the numbered, scanned KFZ form."""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from typing import Literal

from nitka.amounts import normalize_amount
from nitka.extraction.pdf import PdfInspector
from nitka.ocr import OcrBlock, OcrEngine, OcrError, OcrPage
from nitka.ocr.engine import OcrProvenance

_COLUMNS = (
    "stage", "month", "labor", "materials",
    "0200", "0200_payroll_total", "0200_base_pay", "0200_additional_pay", "0200_reserve",
    "0300", "0300_contributions_total", "0300_base_pay", "0300_additional_pay", "0300_reserve",
    "0800", "0900", "total",
)
_HEADINGS = (
    (2, 3, r"\bтрудоемкость\b"),
    (3, 4, r"\bматериальные\b"),
    (4, 9, r"\b0200\b"),
    (9, 14, r"\b0300\b"),
    (14, 15, r"\b0800\b"),
    (15, 16, r"\b0900\b"),
    (16, 17, r"^итого\s*:?$"),
)
_ROW = re.compile(r"([0-9]+)\s*этап[\s,]+([0-9]{4})(?:\s*г\.?)?\s*:?")
_COLUMN_NUMBER = re.compile(r"(?:[1-9]|1[0-7])")
_OCR_GROUPED = re.compile(r"-?[0-9]+(?: (?:[0-9]{3})+)+[.,][0-9]{2}")
_DATE = re.compile(r'["«]?[0-9]{1,2}["»]?\s+[а-я]+\s+[0-9]{4}\b')
_PERIOD = re.compile(r"^[а-я]+\s+[0-9]{4}.*[-–—]\s*[а-я]+\s+[0-9]{4}\b")
_CONTRACT = re.compile(r"\b(?:контракт\w*|договор\w*)\s*(?:№|n[оo°]?)?\s*[0-9]")
_VALUE_CONFIDENCE = 0.80
_HEADING_CONFIDENCE = 0.65


@dataclass(frozen=True)
class KfzFragment:
    page: int
    block: OcrBlock


@dataclass(frozen=True)
class KfzCell:
    value: str | None
    fragments: tuple[KfzFragment, ...]
    headings: tuple[KfzFragment, ...]
    issue: str | None = None

    @property
    def review_status(self) -> Literal["UNREVIEWED", "NEEDS_REVIEW"]:
        return "NEEDS_REVIEW" if self.issue else "UNREVIEWED"


@dataclass(frozen=True)
class KfzRow:
    stage: int
    year: int
    label: tuple[KfzFragment, ...]
    cells: dict[str, KfzCell]


@dataclass(frozen=True)
class KfzResult:
    row: KfzRow | None
    header: dict[str, tuple[KfzFragment, ...]]
    provenance: tuple[OcrProvenance, ...]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Table:
    numbers: tuple[OcrBlock, ...]
    boundaries: tuple[float, ...]
    bottom: int

    def column(self, x: float) -> int:
        return bisect_right(self.boundaries, x)


def extract_kfz(path: Path, engine: OcrEngine, *, stage: int, year: int) -> KfzResult:
    """Render a local PDF and run the offline OCR engine on each page."""
    with TemporaryDirectory(prefix="nitka-kfz-") as temporary:
        rendered = PdfInspector().render(path, Path(temporary), dpi=200)
        if not rendered.ok:
            return KfzResult(None, {}, (), (rendered.error or "KFZ PDF render failed",))
        try:
            pages = [engine.recognize(image) for image in rendered.pages]
        except OcrError as exc:
            return KfzResult(None, {}, (), (str(exc),))
    return parse_kfz(pages, stage=stage, year=year)


def parse_kfz(pages: Sequence[OcrPage], *, stage: int, year: int) -> KfzResult:
    """Use the form's printed column numbers and headings to locate OCR cells.

    The selector comes from the caller. All fragments retain the OCR text,
    confidence and bounds in rendered-page pixels. Page numbers are one-based.
    """
    candidates: list[KfzRow] = []
    header: dict[str, tuple[KfzFragment, ...]] = {}
    issues: list[str] = []
    for number, page in enumerate(pages, start=1):
        blocks = tuple(block for block in page.blocks if block.text.strip())
        tables = _find_tables(blocks)
        header_limit = min((table.numbers[0].bbox[1] for table in tables), default=page.height)
        for key, fragments in _header(blocks, number, header_limit).items():
            header[key] = header.get(key, ()) + fragments
        for table in tables:
            candidates.extend(_read_rows(blocks, table, number, stage, year))

    row = candidates[0] if len(candidates) == 1 else None
    if not candidates:
        issues.append(f"KFZ row for stage {stage}, year {year} was not located in a numbered table")
    elif row is None:
        issues.append(f"Multiple KFZ rows match stage {stage}, year {year}")
    elif any(fragment.block.confidence < _VALUE_CONFIDENCE for fragment in row.label):
        reason = "KFZ row label requires review"
        issues.append(reason)
        row = KfzRow(
            stage, year, row.label,
            {key: _require_review(cell, reason) for key, cell in row.cells.items()},
        )
    return KfzResult(row, header, tuple(page.provenance for page in pages), tuple(issues))


def _x(block: OcrBlock) -> float:
    return (block.bbox[0] + block.bbox[2]) / 2


def _y(block: OcrBlock) -> float:
    return (block.bbox[1] + block.bbox[3]) / 2


def _height(block: OcrBlock) -> int:
    return block.bbox[3] - block.bbox[1]


def _lines(blocks: Sequence[OcrBlock], tolerance: float) -> list[list[OcrBlock]]:
    lines: list[list[OcrBlock]] = []
    for block in sorted(blocks, key=_y):
        if lines and _y(block) - _y(lines[-1][0]) <= tolerance:
            lines[-1].append(block)
        else:
            lines.append([block])
    return [sorted(line, key=_x) for line in lines]


def _find_tables(blocks: Sequence[OcrBlock]) -> tuple[_Table, ...]:
    numbers = [block for block in blocks if _COLUMN_NUMBER.fullmatch(block.text.strip())]
    if not numbers:
        return ()
    tolerance = max(1.0, median(_height(block) for block in numbers) * 0.45)
    bands: list[tuple[OcrBlock, ...]] = []
    for line in _lines(numbers, tolerance):
        # The number band is a printed ruler. Require all its markers so a
        # skipped or misread number cannot silently shift the monetary columns.
        if tuple(int(block.text) for block in line) == tuple(range(1, len(_COLUMNS) + 1)):
            bands.append(tuple(line))
    tables = []
    for index, band in enumerate(bands):
        boundaries = tuple((_x(left) + _x(right)) / 2 for left, right in zip(band, band[1:]))
        bottom = min(block.bbox[1] for block in bands[index + 1]) if index + 1 < len(bands) else 0
        tables.append(_Table(band, boundaries, bottom))
    return tuple(tables)


def _header(
    blocks: Sequence[OcrBlock], page: int, limit: int
) -> dict[str, tuple[KfzFragment, ...]]:
    fields: dict[str, tuple[KfzFragment, ...]] = {}
    above = sorted((block for block in blocks if _y(block) < limit), key=_y)
    for block in above:
        text = block.text.strip().casefold()
        fragments: tuple[OcrBlock, ...] = (block,)
        if _CONTRACT.search(text):
            key = "contract"
        elif _PERIOD.match(text):
            key = "printed_period"
        elif text.startswith(("начало ", "окончание ")):
            key = "stage_start" if text.startswith("начало ") else "stage_end"
            if not _DATE.search(text):
                dates = [
                    candidate for candidate in above
                    if 0 <= candidate.bbox[0] - block.bbox[2] <= 3 * _height(block)
                    and abs(_y(candidate) - _y(block)) <= 0.4 * _height(block)
                    and _DATE.match(candidate.text.strip().casefold())
                ]
                if dates:
                    fragments += (min(dates, key=_x),)
        else:
            continue
        fields[key] = fields.get(key, ()) + _fragments(page, fragments)
    return fields


def _fragments(page: int, blocks: Sequence[OcrBlock]) -> tuple[KfzFragment, ...]:
    return tuple(KfzFragment(page, block) for block in blocks)


def _headings(
    blocks: Sequence[OcrBlock], table: _Table
) -> dict[int, tuple[OcrBlock, ...]]:
    top = min(block.bbox[1] for block in table.numbers)
    # The header sits immediately above the number band. Bound its height by
    # the scale of the printed ruler so an earlier table cannot supply headings.
    height = median(_height(block) for block in table.numbers)
    above = [block for block in blocks if top - 15 * height <= _y(block) < top]
    headings = {}
    for start, stop, pattern in _HEADINGS:
        matches = tuple(
            block for block in above
            if start <= table.column(_x(block)) < stop
            and block.confidence >= _HEADING_CONFIDENCE
            and re.search(pattern, block.text.strip().casefold().replace("ё", "е"))
        )
        for column in range(start, stop):
            headings[column] = matches
    return headings


def _read_rows(
    blocks: Sequence[OcrBlock], table: _Table, page: int, stage: int, year: int
) -> list[KfzRow]:
    bottom = max(block.bbox[3] for block in table.numbers)
    body = [
        block for block in blocks
        if _y(block) > bottom and (not table.bottom or _y(block) < table.bottom)
    ]
    labels = [block for block in body if table.column(_x(block)) < 2]
    if not labels:
        return []
    height = median(_height(block) for block in labels)
    lines = _lines(labels, height * 0.4)
    centers = [median(_y(block) for block in line) for line in lines]
    headings = _headings(blocks, table)
    rows = []
    for index, line in enumerate(lines):
        # A full match excludes cumulative rows and rows for other years.
        label = " ".join(block.text.strip().casefold() for block in line)
        match = _ROW.fullmatch(label)
        if match is None or (int(match[1]), int(match[2])) != (stage, year):
            continue
        center = centers[index]
        start = max(bottom, center - 0.65 * height)
        stop = center + 0.65 * height
        if index:
            start = max(start, (centers[index - 1] + center) / 2)
        if index + 1 < len(lines):
            stop = min(stop, (centers[index + 1] + center) / 2)

        cells: dict[str, KfzCell] = {}
        for column, name in enumerate(_COLUMNS[2:], start=2):
            values = sorted(
                (block for block in body if start < _y(block) < stop
                 and table.column(_x(block)) == column),
                key=_x,
            )
            cells[name] = _read_cell(values, headings[column], table, column, page)
        rows.append(KfzRow(stage, year, _fragments(page, line), _check_totals(cells)))
    return rows


def _read_cell(
    blocks: Sequence[OcrBlock],
    heading: tuple[OcrBlock, ...],
    table: _Table,
    column: int,
    page: int,
) -> KfzCell:
    marker = table.numbers[column]
    cell = KfzCell(None, _fragments(page, blocks), _fragments(page, (*heading, marker)))
    if not heading or marker.confidence < _VALUE_CONFIDENCE:
        return _require_review(cell, "Column heading or number could not be verified")
    if not blocks:
        return _require_review(cell, "Cell is blank or was not recognized")
    if any(
        block.confidence < _VALUE_CONFIDENCE
        or table.column(block.bbox[0]) != column
        or table.column(block.bbox[2] - 1) != column
        for block in blocks
    ):
        return _require_review(cell, "Low OCR confidence or ambiguous column bounds")
    text = " ".join(block.text.strip() for block in blocks)
    value = normalize_amount(text)
    # OCR sometimes retains only some of the printed thousands spaces.
    # Accept omitted spaces only between complete three-digit groups.
    spacing = text.replace("\u00a0", " ").replace("\u202f", " ")
    if value is None and _OCR_GROUPED.fullmatch(spacing):
        value = normalize_amount(spacing.replace(" ", ""))
    if value is None:
        return _require_review(cell, "Cell does not contain a single recognizable number")
    return replace(cell, value=value)


def _require_review(cell: KfzCell, issue: str) -> KfzCell:
    return replace(cell, value=None, issue=issue)


def _check_totals(cells: dict[str, KfzCell]) -> dict[str, KfzCell]:
    checks = (
        ("0200_payroll_total", "0200_base_pay", "0200_additional_pay"),
        ("0200", "0200_payroll_total", "0200_reserve"),
        ("0300_contributions_total", "0300_base_pay", "0300_additional_pay"),
        ("0300", "0300_contributions_total", "0300_reserve"),
        ("total", "materials", "0200", "0300", "0800", "0900"),
    )
    checked = cells.copy()
    for total, *parts in checks:
        values = [cells[key].value for key in (total, *parts)]
        if any(value is None for value in values):
            continue
        printed, *components = (Decimal(value) for value in values if value is not None)
        if printed != sum(components):
            for key in (total, *parts):
                checked[key] = _require_review(cells[key], f"Printed values do not reconcile for {total}")
    return checked
