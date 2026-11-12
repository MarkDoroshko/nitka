from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .demo_models import ExportError


@dataclass(frozen=True)
class _PdfFonts:
    regular: str
    bold: str


_PDF_FONTS: _PdfFonts | None = None

_REGULAR_FONT_CANDIDATES = (
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)
_BOLD_FONT_CANDIDATES = (
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/seguisb.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)


def _pdf_fonts() -> _PdfFonts:
    global _PDF_FONTS
    if _PDF_FONTS is not None:
        return _PDF_FONTS
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError as exc:
        raise ExportError(
            "Для PDF установите reportlab: pip install -r frontend/requirements.txt"
        ) from exc

    regular_path = next(
        (path for path in _REGULAR_FONT_CANDIDATES if Path(path).is_file()), None
    )
    if regular_path is None:
        raise ExportError(
            "Не найден TTF-шрифт с кириллицей. Установите Arial или DejaVu Sans."
        )
    pdfmetrics.registerFont(TTFont("NitkaSans", regular_path))

    bold_path = next(
        (path for path in _BOLD_FONT_CANDIDATES if Path(path).is_file()), None
    )
    if bold_path is None:
        bold = "NitkaSans"
    else:
        pdfmetrics.registerFont(TTFont("NitkaSans-Bold", bold_path))
        bold = "NitkaSans-Bold"

    _PDF_FONTS = _PdfFonts(regular="NitkaSans", bold=bold)
    return _PDF_FONTS
