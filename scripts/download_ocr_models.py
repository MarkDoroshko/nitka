#!/usr/bin/env python3
"""Install the pinned OCR release using only the Python standard library."""

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = (
    "https://github.com/MarkDoroshko/nitka/releases/download/ocr-models-v1/ppocr-v5.zip"
)
ARCHIVE_SHA256 = "e08c60e6e7bab9f6f22d72994b4f3cbc5da69e5afa10b36a9eedb815d12ee20c"
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(directory: Path, hashes: dict[str, str]) -> None:
    for name, expected in hashes.items():
        path = directory / name
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f"Missing file or SHA-256 mismatch: {path}")


def install(destination: Path, archive: Path | None = None) -> None:
    manifest = ROOT / "config/ocr/ppocr-v5.json"
    hashes = json.loads(manifest.read_text(encoding="utf-8"))["model_sha256"]
    if destination.exists():
        try:
            verify(destination, hashes)
        except ValueError as error:
            raise ValueError(
                f"{error}. Existing folder was not changed. Move it aside and retry."
            ) from error
        print(f"OCR models already installed and verified: {destination}")
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    # Stage beside the destination, so the final directory rename stays on one disk.
    with tempfile.TemporaryDirectory(
        prefix=".nitka-ocr-", dir=destination.parent
    ) as tmp:
        staging = Path(tmp)
        package = staging / "models.zip"
        if archive is not None:
            if archive.stat().st_size > MAX_ARCHIVE_BYTES:
                raise ValueError("Archive exceeds the 32 MiB limit")
            shutil.copyfile(archive, package)
        else:
            print(f"Downloading {URL}", flush=True)
            request = urllib.request.Request(
                URL, headers={"User-Agent": "Nitka-OCR-setup/1"}
            )
            with (
                urllib.request.urlopen(request, timeout=60) as response,
                package.open("wb") as out,
            ):
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_ARCHIVE_BYTES:
                        raise ValueError("Download exceeds the 32 MiB limit")
                    out.write(chunk)
        if sha256(package) != ARCHIVE_SHA256:
            raise ValueError("Archive SHA-256 mismatch; no models installed")

        names = list(hashes) + [
            "ppocr-v5.json",
            "MODEL-SOURCES.md",
            "LICENSE-PaddleOCR.txt",
        ]
        prepared = staging / "ppocr-v5"
        with zipfile.ZipFile(package) as bundle:
            expected = {f"ppocr-v5/{name}" for name in names}
            if set(bundle.namelist()) != expected or len(bundle.infolist()) != len(
                expected
            ):
                raise ValueError("Unexpected archive contents")
            # Explicit paths only; never extract arbitrary ZIP paths.
            for name in names:
                target = prepared / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.read(f"ppocr-v5/{name}"))
        verify(prepared, hashes)
        prepared.rename(destination)
    print(f"Installed and verified all four OCR files: {destination}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT / "models/ppocr-v5")
    parser.add_argument(
        "--archive", type=Path, help="Use a previously downloaded ppocr-v5.zip"
    )
    args = parser.parse_args()
    try:
        install(args.destination.resolve(), args.archive)
    except (OSError, ValueError, urllib.error.URLError, zipfile.BadZipFile) as error:
        print(f"OCR setup failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
