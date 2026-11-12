import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from nitka.ocr import OcrEngine, OcrError, OcrLimits
from nitka.ocr import _worker as worker

MANIFEST = Path(__file__).resolve().parents[2] / "config" / "ocr" / "ppocr-v5.json"


@pytest.fixture
def image_path(tmp_path: Path) -> Path:
    path = tmp_path / "Скан.png"
    Image.new("RGB", (120, 160), "white").save(path)
    return path


@pytest.fixture
def local_models(tmp_path: Path) -> tuple[Path, Path]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    models = tmp_path / "models"
    for filename in manifest["model_sha256"]:
        path = models / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(filename.encode())
        manifest["model_sha256"][filename] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return models, manifest_path


@pytest.mark.parametrize(("tokens", "expected_order"), [(99, (0, 1)), (100, (1, 0))])
def test_reading_order_preserves_raw_blocks(
    local_models: tuple[Path, Path],
    image_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tokens: int,
    expected_order: tuple[int, int],
) -> None:
    texts = ["число " * (tokens - 2), "АБC-001!"]
    result = {
        "rec_texts": texts,
        "rec_scores": [0.8, 0.95],
        "rec_polys": [
            [(3, 112), (30, 110), (32, 122), (2, 120)],
            [(1, 1), (21, 1), (21, 11), (1, 11)],
        ],
    }
    monkeypatch.setattr(worker, "_recognize", lambda image, model_dir: result)

    page = worker._main(image_path, *local_models, OcrLimits())

    assert (page.width, page.height) == (120, 160)
    assert [(block.bbox, block.text, block.confidence) for block in page.blocks] == [
        ((2, 110, 32, 122), texts[0], 0.8),
        ((1, 1, 21, 11), texts[1], 0.95),
    ]
    assert page.reading_order == expected_order
    assert page.text == "\n".join(texts[index] for index in expected_order)
    assert page.provenance.model_dump() == json.loads(
        local_models[1].read_text(encoding="utf-8")
    )


def test_missing_weights_report_the_expected_local_file(
    tmp_path: Path, image_path: Path
) -> None:
    with pytest.raises(OcrError, match="OCR_WEIGHTS_MISSING.*inference.onnx"):
        OcrEngine(tmp_path / "missing", MANIFEST).recognize(image_path)


def test_mismatched_weights_are_rejected(
    local_models: tuple[Path, Path], image_path: Path
) -> None:
    models, manifest = local_models
    (models / worker.MODEL_FILES[0]).write_bytes(b"wrong")
    with pytest.raises(OcrError, match="OCR_WEIGHTS_MISMATCH.*inference.onnx"):
        OcrEngine(models, manifest).recognize(image_path)


@pytest.mark.parametrize(
    "limits", [OcrLimits(max_image_bytes=64), OcrLimits(max_image_pixels=1000)]
)
def test_image_limits_are_enforced_before_inference(
    local_models: tuple[Path, Path], image_path: Path, limits: OcrLimits
) -> None:
    with pytest.raises(OcrError, match="OCR_IMAGE_LIMIT"):
        OcrEngine(*local_models, limits=limits).recognize(image_path)


def test_mismatched_result_arrays_are_rejected() -> None:
    with pytest.raises(OcrError, match="OCR_RESULT_INVALID"):
        worker._blocks({"rec_texts": ["строка"], "rec_scores": [], "rec_polys": []})


def test_worker_timeout_is_reported_and_cache_is_cleaned(
    tmp_path: Path, image_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    caches: list[Path] = []

    def timeout(args: list[str], **kwargs: Any) -> None:
        assert kwargs["timeout"] == 0.5
        assert kwargs["env"]["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] == "True"
        cache = Path(kwargs["env"]["PADDLE_PDX_CACHE_HOME"])
        assert cache.is_dir()
        caches.append(cache)
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(OcrError, match="OCR_TIMEOUT"):
        OcrEngine(tmp_path, MANIFEST, limits=OcrLimits(timeout_seconds=0.5)).recognize(
            image_path
        )
    assert len(caches) == 1
    assert not caches[0].exists()
