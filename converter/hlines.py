"""Standalone horizontal rules outside tables (header underlines, etc.)."""

from __future__ import annotations

from typing import List

from .constants import HLINE_MAX_THICKNESS, HLINE_MIN_WIDTH
from .models import LineBlock
from .tables import _rgb_to_hex

def _extract_hlines(page, table_bboxes) -> List[LineBlock]:
    """Standalone horizontal rules outside tables (header underlines, etc.).

    Many forms draw the header bar as a very thin filled rectangle rather than
    a stroked line; both sources are considered.
    """
    page_w = float(getattr(page, "width", 0) or 0)
    candidates: List[tuple] = []  # (top, x0, x1, thickness, color)

    for ln in page.lines or []:
        try:
            x0, x1 = float(ln["x0"]), float(ln["x1"])
            top, bottom = float(ln["top"]), float(ln["bottom"])
        except (KeyError, TypeError, ValueError):
            continue
        width = abs(x1 - x0)
        height = abs(bottom - top)
        # horizontal stroke: wide and nearly zero height
        if width < HLINE_MIN_WIDTH or height > HLINE_MAX_THICKNESS:
            continue
        if height < 1e-3 and width >= HLINE_MIN_WIDTH:
            height = float(ln.get("linewidth") or 0.5)
        color = _rgb_to_hex(ln.get("stroking_color") or ln.get("stroke"))
        candidates.append((min(top, bottom), min(x0, x1), max(x0, x1),
                           max(height, 0.3), color))

    for rct in page.rects or []:
        try:
            x0, x1 = float(rct["x0"]), float(rct["x1"])
            top, bottom = float(rct["top"]), float(rct["bottom"])
        except (KeyError, TypeError, ValueError):
            continue
        width = abs(x1 - x0)
        height = abs(bottom - top)
        if width < HLINE_MIN_WIDTH or height <= 0 or height > HLINE_MAX_THICKNESS:
            continue
        # Prefer filled thin rects (common for header bars).
        if not rct.get("fill") and not rct.get("stroke"):
            continue
        color_src = rct.get("non_stroking_color") if rct.get("fill") else (
            rct.get("stroking_color") or rct.get("stroke")
        )
        color = _rgb_to_hex(color_src)
        candidates.append((min(top, bottom), min(x0, x1), max(x0, x1),
                           height, color))

    blocks: List[LineBlock] = []
    for top, x0, x1, thick, color in sorted(candidates, key=lambda c: c[0]):
        # Skip lines that sit on / inside a table (grid lines).
        mid_y = top + thick / 2.0
        mid_x = (x0 + x1) / 2.0
        if any(
            bx0 - 1 <= mid_x <= bx1 + 1 and btop - 1 <= mid_y <= bbottom + 1
            for (bx0, btop, bx1, bbottom) in table_bboxes
        ):
            continue
        # Deduplicate near-identical rules.
        if any(
            abs(b.top - top) < 1.5 and abs(b.x0 - x0) < 2 and abs(b.x1 - x1) < 2
            for b in blocks
        ):
            continue
        blocks.append(LineBlock(
            top=top,
            bottom=top + thick,
            x0=x0,
            x1=x1,
            thickness=thick,
            color=color or "000000",
        ))
    return blocks


__all__ = ["_extract_hlines"]
