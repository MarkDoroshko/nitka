"""Public OCR results and the isolated worker entry point."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

CPU_THREADS = 2
BoundingBox = tuple[int, int, int, int]


class OcrBlock(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    bbox: BoundingBox
    text: str
    confidence: Annotated[float, Field(ge=0, le=1)]


class OcrProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    settings_version: str
    model_sha256: dict[str, str]


class OcrPage(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    width: int
    height: int
    blocks: tuple[OcrBlock, ...]
    reading_order: tuple[int, ...]
    provenance: OcrProvenance

    @property
    def text(self) -> str:
        return "\n".join(self.blocks[index].text for index in self.reading_order)


class OcrLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 60.0
    max_image_bytes: Annotated[int, Field(gt=0)] = 32 * 1024 * 1024
    max_image_pixels: Annotated[int, Field(gt=0)] = 24_000_000


class OcrError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class _WorkerError(BaseModel):
    code: str
    detail: str


class OcrEngine:
    """Recognize one local image per CPU worker with a wall-clock timeout."""

    def __init__(
        self, model_dir: Path, manifest_path: Path, *, limits: OcrLimits | None = None
    ) -> None:
        self.model_dir = model_dir.absolute()
        self.manifest_path = manifest_path.absolute()
        self.limits = limits or OcrLimits()

    def recognize(self, image_path: Path) -> OcrPage:
        env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
            "OMP_NUM_THREADS": str(CPU_THREADS),
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
        }
        try:
            with TemporaryDirectory(prefix="nitka-ocr-") as cache:
                process = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "nitka.ocr._worker",
                        str(image_path.absolute()),
                        str(self.model_dir),
                        str(self.manifest_path),
                        self.limits.model_dump_json(),
                    ],
                    env={**env, "PADDLE_PDX_CACHE_HOME": cache},
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.limits.timeout_seconds,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise OcrError(
                "OCR_TIMEOUT",
                f"Recognition exceeded {self.limits.timeout_seconds:g} seconds",
            ) from exc
        except OSError as exc:
            raise OcrError("OCR_WORKER_FAILED", str(exc)) from exc

        # Native libraries may write diagnostics to stdout before the worker's response.
        payload = process.stdout.strip().rpartition("\n")[2]
        try:
            if process.returncode == 0:
                return OcrPage.model_validate_json(payload)
            if process.returncode == 2:
                failure = _WorkerError.model_validate_json(payload)
                raise OcrError(failure.code, failure.detail)
        except ValidationError as exc:
            raise OcrError("OCR_WORKER_FAILED", "Invalid OCR worker response") from exc
        raise OcrError(
            "OCR_WORKER_FAILED",
            f"Worker exited with status {process.returncode}: {process.stderr.strip()[-2000:]}",
        )
