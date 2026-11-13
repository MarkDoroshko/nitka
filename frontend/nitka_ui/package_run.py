"""Run a reconciliation from browser uploads and publish it to local storage."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory

from nitka.analysis.matching import CaseMatcher
from nitka.contracts.analysis import AnalysisResult, Scope
from nitka.contracts.extraction import FileRole
from nitka.contracts.profile import CaseProfile
from nitka.extraction.limits import MAX_PACKAGE_BYTES
from nitka.extraction.package import (
    KFZ_SUFFIX,
    PACKAGE_SUPPORT_SUFFIXES,
    PackageExtractor,
)
from nitka.extraction.ports import InputDocument
from nitka.extraction.support_documents import ImageRecognizer
from nitka.ocr import OcrEngine
from nitka.storage import load_run, run_and_save

from .upload_state import RunAction, UploadedFileLike

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PackageUploadError(ValueError):
    """User-facing validation or result loading error."""


def load_profile() -> CaseProfile:
    path = Path(
        os.environ.get("NITKA_CASE_PROFILE", _PROJECT_ROOT / "config/case5.json")
    )
    profile = CaseProfile.model_validate_json(path.read_bytes())
    if not {"REPORT", "SUPPORT"} <= set(profile.working_roles):
        raise ValueError("В профиле должны быть включены роли REPORT и SUPPORT.")
    return profile


def runs_root() -> Path:
    return Path(os.environ.get("NITKA_RUNS_DIR", _PROJECT_ROOT / "runs/ui"))


def local_ocr() -> OcrEngine:
    models = Path(os.environ.get("NITKA_OCR_MODELS", _PROJECT_ROOT / "models/ppocr-v5"))
    return OcrEngine(models, _PROJECT_ROOT / "config/ocr/ppocr-v5.json")


def _stage_uploads(action: RunAction, directory: Path) -> list[InputDocument]:
    if not action["documents"]:
        raise PackageUploadError("Добавьте хотя бы один первичный документ.")
    files: list[tuple[UploadedFileLike, FileRole]] = [(action["report"], "REPORT")]
    files.extend((document, "SUPPORT") for document in action["documents"])

    documents: list[InputDocument] = []
    total = 0
    for index, (upload, role) in enumerate(files, start=1):
        name = PureWindowsPath(upload.name).name
        suffix = Path(name).suffix.lower()
        allowed = {KFZ_SUFFIX} if role == "REPORT" else PACKAGE_SUPPORT_SUFFIXES
        if suffix not in allowed:
            raise PackageUploadError(
                f"Формат файла «{name}» не подходит для этой роли."
            )
        data = upload.getvalue()
        if not data:
            raise PackageUploadError(f"Файл «{name}» пуст.")
        total += len(data)
        if total > MAX_PACKAGE_BYTES:
            raise PackageUploadError("Комплект превышает 25 МБ.")
        path = directory / f"{index}{suffix}"
        path.write_bytes(data)
        documents.append(InputDocument(path, role, name))
    return documents


def run_uploaded_package(
    action: RunAction,
    profile: CaseProfile,
    root: Path,
    *,
    ocr: ImageRecognizer,
    show_stage: Callable[[str], None],
) -> AnalysisResult:
    period = profile.service_period
    scope = Scope(
        currency="RUB",
        amount_basis="GROSS",
        reporting_period=f"{period.start.isoformat()} — {period.end.isoformat()}",
    )
    show_stage("Подготовка загруженных файлов")
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="upload-", dir=root) as directory:
        workspace = Path(directory)
        documents = _stage_uploads(action, workspace)
        show_stage("Извлечение данных, сверка и сохранение")
        result = run_and_save(
            root,
            documents,
            scope,
            extractor=PackageExtractor(
                profile, ocr=ocr, preview_dir=workspace / "previews"
            ),
            matcher=CaseMatcher(profile),
        )
    try:
        return load_run(root, result.run_id, result.revision).analysis
    except (OSError, ValueError, RuntimeError) as exc:
        raise PackageUploadError(
            f"Запуск {result.run_id} сохранён, но результат не удалось открыть."
        ) from exc
