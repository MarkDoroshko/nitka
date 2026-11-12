"""Render page previews and apply an explicit orientation correction."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image

from nitka.extraction.pdf import PdfInspector
from nitka.extraction.registry import RegisteredInput


@dataclass(frozen=True)
class PdfPreview:
    file_id: str
    page: int
    image_path: Path
    rotation: int = 0


class PreviewService:
    def __init__(self, inspector: PdfInspector, output_dir: Path) -> None:
        self.inspector = inspector
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def render_pdf(
        self, entry: RegisteredInput, *, max_pages: int = 20
    ) -> tuple[PdfPreview, ...]:
        target = self.output_dir / entry.file_id
        result = self.inspector.render(entry.path, target, max_pages=max_pages)
        if not result.ok:
            return ()
        return tuple(
            PdfPreview(entry.file_id, index + 1, path)
            for index, path in enumerate(result.pages)
        )

    def image_preview(self, entry: RegisteredInput) -> PdfPreview | None:
        try:
            with Image.open(entry.path) as image:
                image.verify()
        except (OSError, ValueError):
            return None
        target_dir = self.output_dir / entry.file_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"page-0001{entry.path.suffix.lower() or '.png'}"
        try:
            shutil.copy2(entry.path, target)
        except OSError:
            return None
        return PdfPreview(entry.file_id, 1, target)

    def rotate_preview(self, preview: PdfPreview, degrees: int) -> PdfPreview:
        if degrees not in {0, 90, 180, 270}:
            raise ValueError("degrees must be one of 0, 90, 180, 270")
        if degrees == 0:
            return preview
        target = preview.image_path.with_name(
            f"{preview.image_path.stem}-rot{degrees}.png"
        )
        rotated = rotate_image(preview.image_path, degrees, target)
        return replace(
            preview,
            image_path=rotated,
            rotation=(preview.rotation + degrees) % 360,
        )


def rotate_image(path: Path, degrees: int, output_path: Path | None = None) -> Path:
    """Rotate a preview image clockwise by 90/180/270 degrees."""
    if degrees not in {0, 90, 180, 270}:
        raise ValueError("degrees must be one of 0, 90, 180, 270")
    if degrees == 0:
        return path
    target = output_path or path.with_name(f"{path.stem}-rot{degrees}.png")
    with Image.open(path) as image:
        rotated = image.rotate(-degrees, expand=True)
        if target.suffix.lower() == ".png":
            rotated.save(target, format="PNG")
        else:
            rotated.save(target)
    return target
