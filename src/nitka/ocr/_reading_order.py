"""The laboratory's whitespace cuts over complete OCR blocks."""

import re
from statistics import median

from nitka.ocr.engine import BoundingBox, OcrBlock

MINIMUM_TOKENS = 100
TOKEN = re.compile(r"[^\W_]+")


def reading_order(blocks: tuple[OcrBlock, ...]) -> tuple[int, ...]:
    indices = list(range(len(blocks)))
    if sum(len(TOKEN.findall(block.text)) for block in blocks) < MINIMUM_TOKENS:
        return tuple(indices)

    boxes = tuple(block.bbox for block in blocks)
    height = median(bottom - top for _, top, _, bottom in boxes)
    cuts = ((1, max(8.0, height * 0.6)), (0, max(20.0, height * 1.5)))

    def visit(group: list[int], depth: int = 0) -> list[int]:
        if len(group) < 2 or depth > 30:
            return group
        for axis, gap in cuts:
            parts = _split(boxes, group, axis, gap)
            if len(parts) > 1:
                return [index for part in parts for index in visit(part, depth + 1)]
        return sorted(group)

    return tuple(visit(indices))


def _split(
    boxes: tuple[BoundingBox, ...], indices: list[int], axis: int, gap: float
) -> list[list[int]]:
    groups: list[list[int]] = []
    end = float("-inf")
    for index in sorted(indices, key=lambda index: boxes[index][axis]):
        start = boxes[index][axis]
        if not groups or start - end > gap:
            groups.append([])
        groups[-1].append(index)
        end = max(end, boxes[index][axis + 2])
    return groups
