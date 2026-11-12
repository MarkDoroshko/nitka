"""Load the verified PP-OCRv5 profile and recognize a single raster image."""

from __future__ import annotations

import hashlib
import io
import sys
from contextlib import redirect_stdout
from importlib import import_module
from pathlib import Path
from typing import Any, Mapping

from PIL import Image
from pydantic import ValidationError

from nitka.ocr._reading_order import reading_order
from nitka.ocr.engine import (
    CPU_THREADS,
    OcrBlock,
    OcrError,
    OcrLimits,
    OcrPage,
    OcrProvenance,
    _WorkerError,
)

DETECTOR = "PP-OCRv5_mobile_det"
RECOGNIZER = "cyrillic_PP-OCRv5_mobile_rec"
SETTINGS_VERSION = "ppocr-v5-mobile-cyrillic-max2400-v1"
MODEL_FILES = tuple(
    f"{model}/{filename}"
    for model in (DETECTOR, RECOGNIZER)
    for filename in ("inference.onnx", "inference.yml")
)
MAX_IMAGE_EDGE = 16_384
MAX_BLOCKS = 1000


def _verify_models(model_dir: Path, manifest_path: Path) -> OcrProvenance:
    try:
        manifest = OcrProvenance.model_validate_json(manifest_path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise OcrError(
            "OCR_MANIFEST_INVALID", f"Cannot read OCR manifest: {manifest_path}"
        ) from exc
    if manifest.settings_version != SETTINGS_VERSION or set(
        manifest.model_sha256
    ) != set(MODEL_FILES):
        raise OcrError(
            "OCR_MANIFEST_INVALID", "Expected the PP-OCRv5 mobile Cyrillic manifest"
        )
    hashes = {}
    for name in MODEL_FILES:
        model_file = model_dir / name
        try:
            with model_file.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError as exc:
            raise OcrError(
                "OCR_WEIGHTS_MISSING",
                f"Install readable local PP-OCRv5 weights at {model_file}",
            ) from exc
        if digest != manifest.model_sha256[name]:
            raise OcrError(
                "OCR_WEIGHTS_MISMATCH", f"SHA-256 differs from manifest: {model_file}"
            )
        hashes[name] = digest
    return OcrProvenance(settings_version=SETTINGS_VERSION, model_sha256=hashes)


def _load_image(path: Path, limits: OcrLimits) -> Image.Image:
    try:
        if not path.is_file():
            raise OSError("Expected a local image file")
        with path.open("rb") as stream:
            data = stream.read(limits.max_image_bytes + 1)
        if len(data) > limits.max_image_bytes:
            raise OcrError("OCR_IMAGE_LIMIT", "Encoded image exceeds the byte limit")
        with Image.open(io.BytesIO(data), formats=("PNG", "JPEG", "WEBP")) as image:
            if (
                max(image.size) > MAX_IMAGE_EDGE
                or image.width * image.height > limits.max_image_pixels
            ):
                raise OcrError(
                    "OCR_IMAGE_LIMIT", "Image dimensions exceed the pixel limit"
                )
            if getattr(image, "is_animated", False):
                raise OcrError("OCR_IMAGE_INVALID", "Expected a single image frame")
            return image.convert("RGB")
    except Image.DecompressionBombError as exc:
        raise OcrError(
            "OCR_IMAGE_LIMIT", "Image dimensions exceed the pixel limit"
        ) from exc
    except (OSError, ValueError) as exc:
        raise OcrError(
            "OCR_IMAGE_INVALID", f"Cannot read a PNG, JPEG or WebP image: {path}"
        ) from exc


def _recognize(image: Image.Image, model_dir: Path) -> Mapping[str, Any]:
    try:
        cv2 = import_module("cv2")
        numpy = import_module("numpy")
        import_module("onnxruntime")
        paddle = import_module("paddleocr")
    except ImportError as exc:
        raise OcrError(
            "OCR_RUNTIME_MISSING",
            f"Install the PaddleOCR/ONNX Runtime CPU environment: {exc}",
        ) from exc

    cv2.setNumThreads(1)
    engine = paddle.PaddleOCR(
        engine="onnxruntime",
        engine_config={
            "providers": ["CPUExecutionProvider"],
            "intra_op_num_threads": CPU_THREADS,
            "inter_op_num_threads": 1,
            "log_severity_level": 3,
        },
        device="cpu",
        text_detection_model_name=DETECTOR,
        text_detection_model_dir=str(model_dir / DETECTOR),
        text_recognition_model_name=RECOGNIZER,
        text_recognition_model_dir=str(model_dir / RECOGNIZER),
        text_det_limit_side_len=2400,
        text_det_limit_type="max",
        text_det_thresh=0.3,
        text_det_box_thresh=0.6,
        text_det_unclip_ratio=1.5,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    # PaddleOCR expects BGR for ndarray inputs. This preserves original pixel coordinates.
    pages = engine.predict(numpy.asarray(image)[:, :, ::-1].copy())
    if len(pages) != 1:
        raise OcrError("OCR_RESULT_INVALID", "Expected one OCR result for one image")
    result: Mapping[str, Any] = pages[0]
    return result


def _blocks(result: Mapping[str, Any]) -> tuple[OcrBlock, ...]:
    try:
        texts, scores, polygons = (
            result[key] for key in ("rec_texts", "rec_scores", "rec_polys")
        )
        if len(texts) > MAX_BLOCKS:
            raise OcrError("OCR_RESULT_LIMIT", "Recognition exceeded the block limit")
        blocks = []
        for text, score, polygon in zip(texts, scores, polygons, strict=True):
            xs, ys = zip(*polygon, strict=True)
            blocks.append(
                OcrBlock(
                    bbox=(int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))),
                    text=text,
                    confidence=float(score),
                )
            )
        return tuple(blocks)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise OcrError(
            "OCR_RESULT_INVALID", "Invalid text, score or polygon from PaddleOCR"
        ) from exc


def _main(
    image_path: Path, model_dir: Path, manifest_path: Path, limits: OcrLimits
) -> OcrPage:
    provenance = _verify_models(model_dir, manifest_path)
    with _load_image(image_path, limits) as image:
        blocks = _blocks(_recognize(image, model_dir))
        return OcrPage(
            width=image.width,
            height=image.height,
            blocks=blocks,
            reading_order=reading_order(blocks),
            provenance=provenance,
        )


def _run() -> int:
    image, models, manifest, raw_limits = sys.argv[1:]
    try:
        with redirect_stdout(sys.stderr):
            page = _main(
                Path(image),
                Path(models),
                Path(manifest),
                OcrLimits.model_validate_json(raw_limits),
            )
    except OcrError as exc:
        print(_WorkerError(code=exc.code, detail=exc.detail).model_dump_json())
        return 2
    print(page.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(_run())
