import os
import shutil
from pathlib import Path

import pytest

from nitka.extraction.kfz import extract_kfz, parse_kfz
from nitka.ocr import OcrBlock, OcrEngine, OcrPage
from nitka.ocr.engine import OcrProvenance

_PROVENANCE = OcrProvenance(settings_version="test", model_sha256={})
_TARGET = (
    "4,25", "0,00",
    "2175 200,50", "2 100 000,50", "2 000 000,00", "100 000,50", "75 200,00",
    "42 000,00", "40 000,00", "39 000,00", "1 000,00", "2 000,00",
    "1 250,00", "-200,00", "2 218250,50",
)


def _block(text: str, x: int, y: int, *, confidence: float = 0.99) -> OcrBlock:
    width = min(len(text) * 2, 40)
    return OcrBlock(bbox=(x - width, y - 7, x + width, y + 7), text=text, confidence=confidence)


def _page(
    *,
    target: tuple[str, ...] = _TARGET,
    label: str = "4 этап, 2028 г.:",
    low_confidence: tuple[int, ...] = (),
    omitted: tuple[int, ...] = (),
    swap_headings: bool = False,
    split_label: bool = False,
) -> OcrPage:
    blocks = [
        _block("Февраль 2027 г. - Май 2028 г.", 350, 20),
        _block("Начало 4 этапа", 50, 41),
        _block('"05" февраля 2027 г.', 150, 42),
        _block('Окончание 4 этапа "30" мая 2028 г.', 50, 61),
        _block("Договор № 44-19 от 01.02.2027 г.", 1000, 41),
        _block("Трудоёмкость", 300, 175),
        _block("Материальные затраты", 400, 175),
        _block("Оплата труда (статья 0200):", 700, 106),
        _block("0300):", 1200, 127),
        _block("Статья 0900" if swap_headings else "Статья 0800", 1500, 140),
        _block("Статья 0800" if swap_headings else "Статья 0900", 1600, 140),
        _block("Итого", 1700, 172),
    ]
    blocks += [_block(str(i), i * 100, 200 + i % 4) for i in range(1, 18)]
    for row_label, y, amounts in (
        ("4 этап, 2027 г.:", 220, ("1,00",) * len(_TARGET)),
        (label, 235, target),
        ("ВСЕГО 4 этап, 2028 г.:", 250, ("2,00",) * len(_TARGET)),
    ):
        if y == 235 and split_label:
            blocks.extend([_block("4 этап,", 85, y - 2), _block("2028 г.:", 185, y)])
        else:
            blocks.append(_block(row_label, 100, y))
        for column, text in enumerate(amounts, start=3):
            if y != 235 or column not in omitted:
                confidence = 0.70 if y == 235 and column in low_confidence else 0.99
                blocks.append(_block(text, column * 100, y + column % 3, confidence=confidence))
    return OcrPage(
        width=1800, height=350, blocks=tuple(reversed(blocks)),
        reading_order=tuple(range(len(blocks))), provenance=_PROVENANCE,
    )


def test_extracts_requested_stage_year_from_geometric_columns() -> None:
    result = parse_kfz([_page(split_label=True)], stage=4, year=2028)
    assert result.issues == ()
    assert result.provenance == (_PROVENANCE,)
    row = result.row
    assert row is not None
    assert [fragment.block.text for fragment in row.label] == ["4 этап,", "2028 г.:"]
    assert {key: cell.value for key, cell in row.cells.items()} == {
        "labor": "4.25",
        "materials": "0.00",
        "0200": "2175200.50",
        "0200_payroll_total": "2100000.50",
        "0200_base_pay": "2000000.00",
        "0200_additional_pay": "100000.50",
        "0200_reserve": "75200.00",
        "0300": "42000.00",
        "0300_contributions_total": "40000.00",
        "0300_base_pay": "39000.00",
        "0300_additional_pay": "1000.00",
        "0300_reserve": "2000.00",
        "0800": "1250.00",
        "0900": "-200.00",
        "total": "2218250.50",
    }
    payroll = row.cells["0200"]
    assert payroll.review_status == "UNREVIEWED"
    assert payroll.fragments[0].page == 1
    assert payroll.fragments[0].block.text == "2175 200,50"
    assert payroll.fragments[0].block.bbox == (478, 230, 522, 244)
    assert [fragment.block.text for fragment in payroll.headings] == [
        "Оплата труда (статья 0200):", "5",
    ]
    assert [fragment.block.text for fragment in result.header["stage_start"]] == [
        "Начало 4 этапа", '"05" февраля 2027 г.',
    ]
    assert result.header["stage_end"][0].block.text == 'Окончание 4 этапа "30" мая 2028 г.'
    assert result.header["contract"][0].block.text == "Договор № 44-19 от 01.02.2027 г."
    assert result.header["printed_period"][0].block.text == "Февраль 2027 г. - Май 2028 г."


def test_marks_low_confidence_missing_and_malformed_cells_for_review() -> None:
    values = list(_TARGET)
    values[3] = "2 10,50"
    result = parse_kfz(
        [_page(target=tuple(values), low_confidence=(5,), omitted=(15,))], stage=4, year=2028
    )
    row = result.row
    assert row is not None
    for key in ("0200", "0200_payroll_total", "0800"):
        assert row.cells[key].value is None
        assert row.cells[key].review_status == "NEEDS_REVIEW"
    assert row.cells["0200"].fragments[0].block.confidence == 0.70
    assert row.cells["0200_payroll_total"].fragments[0].block.text == "2 10,50"
    assert row.cells["0800"].fragments == ()
    assert row.cells["0900"].value == "-200.00"


def test_requires_matching_article_headings() -> None:
    row = parse_kfz([_page(swap_headings=True)], stage=4, year=2028).row
    assert row is not None
    assert row.cells["0800"].review_status == row.cells["0900"].review_status == "NEEDS_REVIEW"
    assert row.cells["0200"].value == "2175200.50"


def test_preserves_printed_amounts_when_totals_disagree() -> None:
    values = list(_TARGET)
    values[4] = "2 001 000,00"
    values[-1] = "2 218350,50"
    row = parse_kfz([_page(target=tuple(values))], stage=4, year=2028).row
    assert row is not None
    assert row.cells["0200_base_pay"].review_status == "UNREVIEWED"
    assert row.cells["0200_base_pay"].value == "2001000.00"
    assert row.cells["0200_payroll_total"].value == "2100000.50"
    assert row.cells["0200"].value == "2175200.50"
    assert row.cells["0800"].value == "1250.00"
    assert row.cells["total"].value == "2218350.50"
    assert row.arithmetic_issues == (
        "0200_payroll_total: printed 2100000.50, component sum 2101000.50, difference -1000.00",
        "total: printed 2218350.50, component sum 2218250.50, difference 100.00",
    )


def test_reports_missing_and_ambiguous_target_rows() -> None:
    missing = parse_kfz([_page(label="ВСЕГО 4 этап:")], stage=4, year=2028)
    assert missing.row is None
    assert "was not located" in missing.issues[0]
    duplicate = parse_kfz([_page(), _page()], stage=4, year=2028)
    assert duplicate.row is None
    assert "Multiple KFZ rows" in duplicate.issues[0]


def test_incomplete_column_number_band_cannot_shift_values() -> None:
    page = _page()
    blocks = tuple(block for block in page.blocks if block.text != "5")
    incomplete = page.model_copy(update={"blocks": blocks, "reading_order": tuple(range(len(blocks)))})
    result = parse_kfz([incomplete], stage=4, year=2028)
    assert result.row is None
    assert "was not located in a numbered table" in result.issues[0]


def test_scanned_case_with_real_ocr(tmp_path: Path) -> None:
    source = os.environ.get("NITKA_KFZ_SMOKE_PDF")
    if source is None:
        pytest.skip("Set NITKA_KFZ_SMOKE_PDF and NITKA_OCR_MODELS for the real-model smoke")
    manifest = Path(__file__).resolve().parents[2] / "config" / "ocr" / "ppocr-v5.json"
    engine = OcrEngine(Path(os.environ["NITKA_OCR_MODELS"]), manifest)
    renamed = tmp_path / "renamed-report.pdf"
    shutil.copyfile(source, renamed)
    result = extract_kfz(renamed, engine, stage=3, year=2026)
    assert result.issues == ()
    row = result.row
    assert row is not None
    expected = {
        "0200": "5558474.83", "0300": "1671359.96", "0800": "1145798.10",
        "0900": "3204744.51", "total": "11580377.40", "labor": "34.14",
    }
    assert {key: row.cells[key].value for key in expected} == expected
    assert all(cell.fragments[0].page == 1 for cell in row.cells.values())
    assert "24/03/15-01" in result.header["contract"][0].block.text
    assert "2025" in result.header["stage_start"][-1].block.text
    assert "2026" in result.header["stage_end"][-1].block.text
