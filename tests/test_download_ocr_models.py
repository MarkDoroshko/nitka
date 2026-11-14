import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from scripts import download_ocr_models as downloader


@pytest.fixture
def package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "config/ocr"
    config.mkdir(parents=True)
    content = b"test model bytes"
    manifest = {
        "model_sha256": {"detector/inference.onnx": hashlib.sha256(content).hexdigest()}
    }
    (config / "ppocr-v5.json").write_text(json.dumps(manifest))
    archive = tmp_path / "models.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("ppocr-v5/detector/inference.onnx", content)
        bundle.writestr("ppocr-v5/ppocr-v5.json", json.dumps(manifest))
        bundle.writestr("ppocr-v5/MODEL-SOURCES.md", "source")
        bundle.writestr("ppocr-v5/LICENSE-PaddleOCR.txt", "license")
    monkeypatch.setattr(downloader, "ROOT", tmp_path)
    monkeypatch.setattr(downloader, "ARCHIVE_SHA256", downloader.sha256(archive))
    return archive


def test_install_and_reuse_without_archive(package: Path, tmp_path: Path) -> None:
    destination = tmp_path / "installed"
    downloader.install(destination, package)
    assert (destination / "detector/inference.onnx").read_bytes() == b"test model bytes"
    assert (destination / "LICENSE-PaddleOCR.txt").is_file()
    package.unlink()
    downloader.install(destination, package)


def test_corrupt_archive_does_not_install(package: Path, tmp_path: Path) -> None:
    package.write_bytes(b"corrupt")
    destination = tmp_path / "installed"
    with pytest.raises(ValueError, match="Archive SHA-256 mismatch"):
        downloader.install(destination, package)
    assert not destination.exists()
    assert not list(tmp_path.glob(".nitka-ocr-*"))


def test_existing_damaged_model_is_preserved(package: Path, tmp_path: Path) -> None:
    destination = tmp_path / "installed"
    downloader.install(destination, package)
    model = destination / "detector/inference.onnx"
    model.write_bytes(b"local modification")
    with pytest.raises(ValueError, match="Existing folder was not changed"):
        downloader.install(destination, package)
    assert model.read_bytes() == b"local modification"


def test_wrong_model_hash_does_not_install(package: Path, tmp_path: Path) -> None:
    (tmp_path / "config/ocr/ppocr-v5.json").write_text(
        json.dumps({"model_sha256": {"detector/inference.onnx": "0" * 64}})
    )
    destination = tmp_path / "installed"
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        downloader.install(destination, package)
    assert not destination.exists()


def test_unexpected_archive_paths_are_rejected(
    package: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with zipfile.ZipFile(package, "a") as bundle:
        bundle.writestr("../escaped", "unexpected")
    monkeypatch.setattr(downloader, "ARCHIVE_SHA256", downloader.sha256(package))
    with pytest.raises(ValueError, match="Unexpected archive contents"):
        downloader.install(tmp_path / "installed", package)
    assert not (tmp_path / "escaped").exists()
    assert not (tmp_path / "installed").exists()
