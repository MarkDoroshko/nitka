"""Subprocess worker for PDF inspection and sequential page rendering.

Invoked as ``python -m nitka.extraction.pdf_worker ...``. It prints one JSON
object to stdout. Errors are reported as JSON instead of tracebacks so the
parent process can isolate a bad document.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pdf-worker")
    parser.add_argument("command", choices=("inspect", "render", "extract"))
    parser.add_argument("path")
    parser.add_argument("output", nargs="?")
    parser.add_argument("--dpi", type=float, default=150.0)
    parser.add_argument("--max-pages", type=int, default=20)
    args = parser.parse_args(argv)

    if args.command == "inspect":
        _inspect(Path(args.path))
    elif args.command == "extract":
        _extract(Path(args.path))
    else:
        if not args.output:
            _emit({"ok": False, "error": "render requires an output directory"})
        else:
            _render(Path(args.path), Path(args.output), args.dpi, args.max_pages)
    return 0


def _inspect(path: Path) -> None:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            _emit({"ok": True, "encrypted": True, "pages": None})
            return
        _emit({"ok": True, "encrypted": False, "pages": len(reader.pages)})
    except Exception as exc:  # noqa: BLE001 - isolate any parser failure
        _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def _extract(path: Path) -> None:
    try:
        import pdfplumber

        pages: list[dict[str, Any]] = []
        with pdfplumber.open(str(path)) as document:
            for index, page in enumerate(document.pages, start=1):
                tables = page.extract_tables() or []
                pages.append(
                    {
                        "page": index,
                        "width": float(page.width),
                        "height": float(page.height),
                        "text": page.extract_text() or "",
                        "rotation": int(getattr(page, "rotation", 0) or 0),
                        "tables": [
                            [
                                ["" if cell is None else str(cell) for cell in row]
                                for row in table
                            ]
                            for table in tables
                        ],
                    }
                )
        _emit({"ok": True, "pages": pages})
    except Exception as exc:  # noqa: BLE001 - isolate any parser failure
        _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def _render(path: Path, output: Path, dpi: float, max_pages: int) -> None:
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(str(path))
        try:
            page_count = len(document)
            if page_count > max_pages:
                _emit(
                    {
                        "ok": False,
                        "error": f"page count {page_count} exceeds {max_pages}",
                    }
                )
                return
            output.mkdir(parents=True, exist_ok=True)
            files: list[str] = []
            for index in range(page_count):
                page = document[index]
                bitmap = page.render(scale=dpi / 72.0)
                image = bitmap.to_pil()
                target = output / f"page-{index + 1:04d}.png"
                image.save(target, format="PNG")
                image.close()
                files.append(str(target))
            _emit({"ok": True, "pages": page_count, "files": files})
        finally:
            document.close()
    except Exception as exc:  # noqa: BLE001 - isolate any renderer failure
        _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
