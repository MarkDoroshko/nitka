"""Opt-in smoke using genuine model artifacts and local scans."""

import json
import os
import re
from pathlib import Path

import pytest

from nitka.ocr import OcrEngine

MANIFEST = Path(__file__).resolve().parents[2] / "config" / "ocr" / "ppocr-v5.json"


def test_local_models_recognize_real_scans() -> None:
    models = os.environ.get("NITKA_OCR_MODELS")
    images = os.environ.get("NITKA_OCR_SMOKE_IMAGES")
    if models is None and images is None:
        pytest.skip(
            "Set NITKA_OCR_MODELS and NITKA_OCR_SMOKE_IMAGES for the real-model smoke"
        )
    assert models and images, "Set both NITKA_OCR_MODELS and NITKA_OCR_SMOKE_IMAGES"

    engine = OcrEngine(Path(models), MANIFEST)
    expected_provenance = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for filename in images.split(os.pathsep):
        page = engine.recognize(Path(filename))
        assert re.search(r"[А-Яа-яЁё]", page.text), filename
        assert page.provenance.model_dump() == expected_provenance
        assert sorted(page.reading_order) == list(range(len(page.blocks)))
        for block in page.blocks:
            left, top, right, bottom = block.bbox
            assert 0 <= left <= right <= page.width
            assert 0 <= top <= bottom <= page.height
            assert 0 <= block.confidence <= 1
