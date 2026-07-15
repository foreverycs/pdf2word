"""Table detection, cell building, merge refinement, and acceptance filters."""

from __future__ import annotations

from collections import Counter
from typing import List, Optional, Tuple

from .constants import (
    HLINE_MAX_THICKNESS,
    HLINE_MIN_WIDTH,
    SNAP_TOLERANCE,
    SPAN_COVER_RATIO,
    TABLE_OVERLAP_REJECT,
    TEXT_COL_CLUSTER_TOL,
    TEXT_TABLE_MAX_CELLS,
    TEXT_TABLE_MAX_COLS,
    TEXT_TABLE_MAX_ROWS,
    TEXT_TABLE_MIN_FILLED,
)
from .models import Cell, TableBlock, TextRun
from .text_blocks import _words_same_visual_line
from .text_utils import (
    _join_words,
    _merge_soft_wrap_paragraphs,
    _normalize_newlines,
    _normalize_spacing,
    _word_line_sort_key,
)
from .word_index import WordIndex

def _index_of(value: float, bounds: List[float]) -> Optional[int]:
    for i in range(len(bounds) - 1):
        if bounds[i] <= value < bounds[i + 1]:
            return i
    if bounds and value >= bounds[-1]:
        return len(bounds) - 2
    return None


def _build_table(table, page, widx: WordIndex) -> Optional[TableBlock]:
    # In pdfplumber >=0.11 `table.cells` is a flat list of (x0, top, x1, bottom)
    # rects describing the detected grid. A merged region is rendered as a
    # single rect that spans multiple column/row bands, so the rect geometry is
    # the most reliable source for span (rowspan/colspan) information.
    rects = table.cells
    vx = sorted({round(r[0], 1) for r in rects} | {round(r[2], 1) for r in rects})
    hy = sorted({round(r[1], 1) for r in rects} | {round(r[3], 1) for r in rects})
    ncols = len(vx) - 1
    nrows = len(hy) - 1
    if ncols < 1 or nrows < 1:
        return None

    # `extract()` gives the text of every (non-covered) cell.
    logical = table.extract()

    # Use spatial index for words inside this table's bbox.
    x0, top, x1, bottom = table.bbox
    table_words = widx.query_rect(x0, top, x1, bottom, pad=1.0)
    # Lines that lie within this table, used for per-edge border detection.
    table_lines = [
        ln for ln in page.lines
        if not (ln["x0"] < x0 - 1 or ln["x1"] > x1 + 1
                or ln["top"] < top - 1 or ln["bottom"] > bottom + 1)
    ]
    word_font: dict = {}
    word_box: dict = {}  # (r, c) -> (x0, x1, top, bottom) of its text
    # (r, c) -> {rounded_line_top: [min_x0, max_x1]} per text line, used to infer
    # alignment line-by-line (a wrapped paragraph reveals its alignment on every
    # line, not on the union bounding box).
    word_lines: dict = {}
    for w in table_words:
        cx = (w["x0"] + w["x1"]) / 2
        cy = (w["top"] + w["bottom"]) / 2
        ci = _index_of(cx, vx)
        ri = _index_of(cy, hy)
        if ci is None or ri is None:
            continue
        size = w.get("size") or 0.0
        fname = w.get("fontname") or ""
        word_font.setdefault((ri, ci), Counter())[(round(size, 1), fname)] += 1
        b = word_box.get((ri, ci))
        if b is None:
            word_box[(ri, ci)] = [w["x0"], w["x1"], w["top"], w["bottom"]]
        else:
            b[0] = min(b[0], w["x0"]); b[1] = max(b[1], w["x1"])
            b[2] = min(b[2], w["top"]); b[3] = max(b[3], w["bottom"])
        lm = word_lines.setdefault((ri, ci), {})
        key = round(w["top"])
        entry = lm.get(key)
        if entry is None:
            lm[key] = [w["x0"], w["x1"]]
        else:
            entry[0] = min(entry[0], w["x0"])
            entry[1] = max(entry[1], w["x1"])

    def _region_font(r0: int, c0: int, r1: int, c1: int):
        merged: Counter = Counter()
        for rr in range(r0, r1 + 1):
            for cc in range(c0, c1 + 1):
                merged.update(word_font.get((rr, cc), Counter()))
        if not merged:
            return None, None
        (size, fname), _ = merged.most_common(1)[0]
        return (size or None), (fname or None)

    def _region_align(r0: int, c0: int, r1: int, c1: int):
        box = None
        votes = []
        for rr in range(r0, r1 + 1):
            for cc in range(c0, c1 + 1):
                b = word_box.get((rr, cc))
                if b is None:
                    continue
                if box is None:
                    box = list(b)
                else:
                    box[0] = min(box[0], b[0]); box[1] = max(box[1], b[1])
                    box[2] = min(box[2], b[2]); box[3] = max(box[3], b[3])
                for line in word_lines.get((rr, cc), {}).values():
                    lx0, lx1 = line
                    lpad = lx0 - vx[c0]
                    rpad = vx[c1 + 1] - lx1
                    if rpad > lpad * 2.5:
                        votes.append("left")
                    elif lpad > rpad * 2.5:
                        votes.append("right")
                    else:
                        votes.append("center")

        if box is None:
            return "left", "top"
        cell_l, cell_r = vx[c0], vx[c1 + 1]
        cell_t, cell_b = hy[r0], hy[r1 + 1]
        cell_h = cell_b - cell_t
        bx0, bx1, bt, bb = box

        # horizontal: take the majority vote across the cell's text lines, so a
        # wrapped (left/centre/right) paragraph is classified by each line rather
        # than by its union bounding box (which would otherwise fill the width
        # and look "centred").
        align = "left"
        if votes:
            align = Counter(votes).most_common(1)[0][0]

        # vertical: use the union text box (single-line cells dominate).
        tpad = bt - cell_t
        bpad = cell_b - bb
        if bpad > tpad * 2.5:
            valign = "top"
        elif tpad > bpad * 2.5:
            valign = "bottom"
        else:
            valign = "center"
        return align, valign

    # Filled rectangles (cell background fills) that belong to this table.
    fill_rects = []
    for rct in page.rects:
        if not rct.get("fill"):
            continue
        if (rct["x0"] < x0 - 1 or rct["x1"] > x1 + 1
                or rct["top"] < top - 1 or rct["bottom"] > bottom + 1):
            continue
        fill_rects.append((rct["x0"], rct["top"], rct["x1"], rct["bottom"],
                           _rgb_to_hex(rct.get("non_stroking_color"))))

    def _region_bg(r0: int, c0: int, r1: int, c1: int):
        cell_l, cell_r = vx[c0], vx[c1 + 1]
        cell_t, cell_b = hy[r0], hy[r1 + 1]
        cell_area = max((cell_r - cell_l) * (cell_b - cell_t), 1e-6)
        best, best_area = None, 0.0
        for (fx0, ftop, fx1, fbottom, color) in fill_rects:
            ix0, ix1 = max(fx0, cell_l), min(fx1, cell_r)
            it0, it1 = max(ftop, cell_t), min(fbottom, cell_b)
            if ix1 <= ix0 or it1 <= it0:
                continue
            area = (ix1 - ix0) * (it1 - it0)
            if area / cell_area >= 0.5 and area > best_area:
                best, best_area = color, area
        return best

    cells: List[List[Optional[Cell]]] = [[None] * ncols for _ in range(nrows)]
    owner: List[List[Tuple[int, int]]] = [
        [(r, c) for c in range(ncols)] for r in range(nrows)
    ]

    for (rx0, rtop, rx1, rbottom) in rects:
        c_start = _index_of(rx0 + 0.5, vx)
        c_end = _index_of(rx1 - 0.5, vx)
        r_start = _index_of(rtop + 0.5, hy)
        r_end = _index_of(rbottom - 0.5, hy)
        if None in (c_start, c_end, r_start, r_end):
            continue
        colspan = c_end - c_start + 1
        # A rect whose bottom edge touches the table boundary actually spans
        # from r_start to the very last row; _index_of clamps to the last row
        # index so we must extend the span manually.
        if abs(rbottom - hy[-1]) < 1.0:
            rowspan = nrows - r_start
        else:
            rowspan = r_end - r_start + 1
        text = ""
        if r_start < len(logical) and c_start < len(logical[r_start]) \
                and logical[r_start][c_start] not in (None, ""):
            text = _normalize_spacing(
                _normalize_newlines(str(logical[r_start][c_start]))
            )
        font_size, font_name = _region_font(r_start, c_start, r_end, c_end)
        align, valign = _region_align(r_start, c_start, r_end, c_end)
        bg_color = _region_bg(r_start, c_start, r_end, c_end)
        borders = _cell_borders(table_lines, vx[c_start], vx[c_end + 1],
                                hy[r_start], hy[r_end + 1])
        paragraphs = _region_paragraphs(
            widx, vx, hy, r_start, c_start, r_end, c_end
        )
        if paragraphs and not text:
            text = _paragraphs_to_text(paragraphs)

        if cells[r_start][c_start] is not None:
            continue
        for rr in range(r_start, r_end + 1):
            for cc in range(c_start, c_end + 1):
                owner[rr][cc] = (r_start, c_start)
        cells[r_start][c_start] = Cell(
            text=text,
            rowspan=rowspan,
            colspan=colspan,
            font_size=font_size,
            font_name=font_name,
            align=align,
            valign=valign,
            bg_color=bg_color,
            borders=borders or None,
            paragraphs=paragraphs or None,
        )

    # Text-strategy / partial grids: grow merges when a word bbox spans
    # multiple empty neighbour cells (common for borderless tables).
    _refine_merges_from_words(cells, owner, vx, hy, widx, x0, top, x1, bottom)

    # Fill still-empty anchors with word text when extract() left them blank.
    for r in range(nrows):
        for c in range(ncols):
            if owner[r][c] != (r, c):
                continue
            cell = cells[r][c]
            if cell is None:
                continue
            if cell.text.strip() and cell.paragraphs:
                continue
            r1 = r + cell.rowspan - 1
            c1 = c + cell.colspan - 1
            paragraphs = _region_paragraphs(widx, vx, hy, r, c, r1, c1)
            if not paragraphs:
                continue
            cell.paragraphs = paragraphs
            if not cell.text.strip():
                cell.text = _paragraphs_to_text(paragraphs)
            if cell.font_size is None or cell.font_name is None:
                fs, fn = _region_font(r, c, r1, c1)
                cell.font_size = cell.font_size or fs
                cell.font_name = cell.font_name or fn

    # Any grid cell still unclaimed (no rect covers it) is part of a merge; it
    # is already marked via `owner`, so leave cells[r][c] as None.
    border = _table_border(table, page)
    col_widths = [round(vx[i + 1] - vx[i], 1) for i in range(ncols)]
    row_heights = [round(hy[i + 1] - hy[i], 1) for i in range(nrows)]
    tx0, ttop, tx1, tbottom = table.bbox
    return TableBlock(
        rows=nrows, cols=ncols, cells=cells, owner=owner,
        col_widths=col_widths, row_heights=row_heights,
        top=float(ttop), bottom=float(tbottom), x0=float(tx0),
        **border,
    )


def _paragraphs_to_text(paragraphs: List[List[TextRun]]) -> str:
    lines = []
    for para in paragraphs:
        lines.append("".join(run.text for run in para))
    return "\n".join(lines)


def _region_paragraphs(
    widx: WordIndex,
    vx: List[float],
    hy: List[float],
    r0: int,
    c0: int,
    r1: int,
    c1: int,
) -> List[List[TextRun]]:
    """Build nested paragraphs/runs for a cell region from page words."""
    cell_l, cell_r = vx[c0], vx[c1 + 1]
    cell_t, cell_b = hy[r0], hy[r1 + 1]
    region_words = widx.query_rect(cell_l, cell_t, cell_r, cell_b, pad=1.0)
    if not region_words:
        return []

    region_words.sort(key=lambda w: (round((w["top"] + w["bottom"]) / 2.0, 1), w["x0"]))
    lines: List[List[dict]] = []
    for w in region_words:
        if lines and _words_same_visual_line(lines[-1], w):
            lines[-1].append(w)
        else:
            lines.append([w])

    paragraphs: List[List[TextRun]] = []
    line_boxes: List[Tuple[float, float, float]] = []
    for line in lines:
        ordered = sorted(line, key=_word_line_sort_key)
        runs: List[TextRun] = []
        cur_key: Optional[Tuple[float, str]] = None
        buf: List[dict] = []
        for w in ordered:
            size = round(float(w.get("size") or 0.0), 1)
            fname = w.get("fontname") or ""
            key = (size, fname)
            if cur_key is None:
                cur_key = key
                buf = [w]
            elif key == cur_key:
                buf.append(w)
            else:
                text = _normalize_spacing(_join_words(buf))
                if text:
                    runs.append(TextRun(
                        text=text,
                        font_size=cur_key[0] or None,
                        font_name=cur_key[1] or None,
                    ))
                cur_key = key
                buf = [w]
        if buf and cur_key is not None:
            text = _normalize_spacing(_join_words(buf))
            if text:
                runs.append(TextRun(
                    text=text,
                    font_size=cur_key[0] or None,
                    font_name=cur_key[1] or None,
                ))
        if runs:
            paragraphs.append(runs)
            line_boxes.append((
                min(float(w["top"]) for w in line),
                max(float(w["bottom"]) for w in line),
                min(float(w["x0"]) for w in line),
                max(float(w["x1"]) for w in line),
            ))
    # Soft word-wrap inside a cell → one Word paragraph (not hard breaks).
    # Pass cell right edge so full-width wraps (e.g. "…地面、踢" / "脚线为…")
    # merge even when line leading is ~1× glyph-box height.
    return _merge_soft_wrap_paragraphs(
        paragraphs, line_boxes=line_boxes, cell_right=cell_r
    )


def _refine_merges_from_words(
    cells: List[List[Optional[Cell]]],
    owner: List[List[Tuple[int, int]]],
    vx: List[float],
    hy: List[float],
    widx: WordIndex,
    table_x0: float,
    table_top: float,
    table_x1: float,
    table_bottom: float,
) -> None:
    """Grow merged regions when word boxes span multiple grid cells.

    Borderless (text-strategy) tables often report a full grid of 1×1 cells
    even when a heading visually spans columns. If a word's horizontal extent
    covers several columns of the same row (and those cells are empty or share
    the same anchor), merge them under the left-most anchor.
    """
    nrows = len(cells)
    ncols = len(cells[0]) if cells else 0
    if nrows < 1 or ncols < 2:
        return

    # Use spatial index instead of full word list scan.
    table_words = widx.query_rect(table_x0, table_top, table_x1, table_bottom, pad=1.0)

    # Collect candidate horizontal spans per row from words.
    for w in table_words:
        if (w["x1"] < table_x0 - 1 or w["x0"] > table_x1 + 1
                or w["bottom"] < table_top - 1 or w["top"] > table_bottom + 1):
            continue
        cy = (w["top"] + w["bottom"]) / 2
        ri = _index_of(cy, hy)
        if ri is None:
            continue
        c_start = _index_of(w["x0"] + 0.5, vx)
        c_end = _index_of(w["x1"] - 0.5, vx)
        if c_start is None or c_end is None or c_end <= c_start:
            continue

        # Require the word to cover a meaningful portion of each intermediate
        # column so we do not merge on a single overflowing glyph.
        covers_all = True
        for cc in range(c_start, c_end + 1):
            col_l, col_r = vx[cc], vx[cc + 1]
            col_w = max(col_r - col_l, 1e-6)
            overlap = min(w["x1"], col_r) - max(w["x0"], col_l)
            if overlap / col_w < SPAN_COVER_RATIO * 0.5 and cc not in (c_start, c_end):
                covers_all = False
                break
            if cc in (c_start, c_end) and overlap / col_w < 0.15:
                covers_all = False
                break
        if not covers_all:
            continue

        # Anchor = left-most cell owner in this row span that already has content,
        # else the left-most grid cell.
        anchor = owner[ri][c_start]
        ar, ac = anchor
        # Only expand within the same row for horizontal word spans.
        if ar != ri:
            continue
        cell = cells[ar][ac]
        if cell is None:
            # Create a minimal anchor if the grid left a hole.
            cells[ar][ac] = Cell(text="")
            cell = cells[ar][ac]
            owner[ar][ac] = (ar, ac)

        new_c_end = max(ac + cell.colspan - 1, c_end)
        # Refuse merge if an intermediate cell already has different text.
        conflict = False
        for cc in range(ac, new_c_end + 1):
            or_, oc = owner[ri][cc]
            other = cells[or_][oc] if or_ == ri else None
            if other is None or (or_, oc) == (ar, ac):
                continue
            if other.text.strip() and other.text.strip() != (cell.text or "").strip():
                # Different content — not a merge.
                conflict = True
                break
            if other.rowspan > 1:
                conflict = True
                break
        if conflict:
            continue

        # Absorb intermediate 1×1 cells into the anchor.
        for cc in range(ac, new_c_end + 1):
            or_, oc = owner[ri][cc]
            if (or_, oc) == (ar, ac):
                continue
            # Clear absorbed anchor cells (same row only).
            if or_ == ri and cells[or_][oc] is not None and (or_, oc) != (ar, ac):
                cells[or_][oc] = None
            owner[ri][cc] = (ar, ac)
        cell.colspan = new_c_end - ac + 1


def _rgb_to_hex(stroke) -> str:
    """Convert a pdfplumber stroke colour (tuple of 0-1 floats/ints) to RRGGBB."""
    if not isinstance(stroke, (tuple, list)) or len(stroke) < 3:
        return "000000"
    parts = []
    for ch in stroke[:3]:
        try:
            parts.append(f"{int(round(float(ch) * 255)):02X}")
        except (TypeError, ValueError):
            parts.append("00")
    return "".join(parts)


def _cell_borders(lines, rx0: float, rx1: float, rtop: float, rbottom: float,
                  tol: float = 1.0) -> dict:
    """Per-edge border info for one cell rectangle.

    Returns a dict with some of the keys top/left/bottom/right; each value is a
    (width_pt, color_hex, dashed) tuple for the line covering that edge.
    """
    best: dict = {}  # kind -> (value_tuple, overlap)

    def consider(kind: str, ln: dict, overlap: float) -> None:
        cur = best.get(kind)
        width = ln.get("linewidth") or 0.5
        color_src = ln.get("stroking_color")
        if not isinstance(color_src, (tuple, list)):
            color_src = ln.get("stroke")
        if cur is None or overlap > cur[1] or (overlap == cur[1] and width > cur[0][0]):
            val = (width, _rgb_to_hex(color_src), bool(ln.get("dash")))
            best[kind] = (val, overlap)

    for ln in lines:
        if ln.get("linewidth") is None:
            continue
        is_vertical = abs(ln["x0"] - ln["x1"]) < 0.5
        if is_vertical:
            x = ln["x0"]
            y0, y1 = min(ln["top"], ln["bottom"]), max(ln["top"], ln["bottom"])
            ov = min(y1, rbottom) - max(y0, rtop)
            if ov <= 0:
                continue
            if abs(x - rx0) <= tol:
                consider("left", ln, ov)
            if abs(x - rx1) <= tol:
                consider("right", ln, ov)
        else:
            y = ln["top"]
            x0, x1 = min(ln["x0"], ln["x1"]), max(ln["x0"], ln["x1"])
            ov = min(x1, rx1) - max(x0, rx0)
            if ov <= 0:
                continue
            if abs(y - rtop) <= tol:
                consider("top", ln, ov)
            if abs(y - rbottom) <= tol:
                consider("bottom", ln, ov)

    return {k: v[0] for k, v in best.items()}


def _table_border(table, page) -> dict:
    x0, top, x1, bottom = table.bbox
    tol = 1.0
    outer_w, inner_w = [], []
    color = "000000"
    dashed = False

    for line in page.lines:
        # keep only lines that lie within the table area
        if (line["x0"] < x0 - tol or line["x1"] > x1 + tol
                or line["top"] < top - tol or line["bottom"] > bottom + tol):
            continue
        lw = line.get("linewidth") or 0.5
        is_vertical = abs(line["x0"] - line["x1"]) < 0.5
        on_outer = False
        if is_vertical:
            if abs(line["x0"] - x0) <= tol or abs(line["x0"] - x1) <= tol:
                on_outer = True
        else:
            if abs(line["top"] - top) <= tol or abs(line["bottom"] - bottom) <= tol:
                on_outer = True
        (outer_w if on_outer else inner_w).append(lw)
        csrc = line.get("stroking_color")
        if not isinstance(csrc, (tuple, list)):
            csrc = line.get("stroke")
        if isinstance(csrc, (tuple, list)):
            color = _rgb_to_hex(csrc)
        if line.get("dash"):
            dashed = True

    if not outer_w and not inner_w:
        return {"border_outer": 0.5, "border_inner": 0.5,
                "border_color": color, "border_dashed": dashed}
    outer = max(outer_w) if outer_w else (max(inner_w) if inner_w else 0.5)
    inner = max(inner_w) if inner_w else outer
    return {"border_outer": outer, "border_inner": inner,
            "border_color": color, "border_dashed": dashed}


def _table_bbox_overlap_ratio(a, b) -> float:
    """Intersection over smaller-area ratio for two (x0, top, x1, bottom) boxes."""
    ax0, atop, ax1, abottom = a
    bx0, btop, bx1, bbottom = b
    ix0, ix1 = max(ax0, bx0), min(ax1, bx1)
    it0, it1 = max(atop, btop), min(abottom, bbottom)
    if ix1 <= ix0 or it1 <= it0:
        return 0.0
    inter = (ix1 - ix0) * (it1 - it0)
    area_a = max((ax1 - ax0) * (abottom - atop), 1e-6)
    area_b = max((bx1 - bx0) * (bbottom - btop), 1e-6)
    return inter / min(area_a, area_b)


def _iter_page_strokes(page):
    """Yield line-like strokes as (x0, top, x1, bottom) from lines / edges / thin rects."""
    for ln in page.lines or []:
        yield (
            float(ln["x0"]), float(ln["top"]),
            float(ln["x1"]), float(ln["bottom"]),
        )
    for edge in getattr(page, "edges", None) or []:
        try:
            yield (
                float(edge["x0"]), float(edge["top"]),
                float(edge["x1"]), float(edge["bottom"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
    # Thin filled/stroked rectangles often act as grid lines in forms.
    for rct in page.rects or []:
        try:
            x0, top = float(rct["x0"]), float(rct["top"])
            x1, bottom = float(rct["x1"]), float(rct["bottom"])
        except (KeyError, TypeError, ValueError):
            continue
        w = abs(x1 - x0)
        h = abs(bottom - top)
        if w >= HLINE_MIN_WIDTH and h <= HLINE_MAX_THICKNESS * 1.5:
            yield (x0, top, x1, bottom)
        elif h >= HLINE_MIN_WIDTH and w <= HLINE_MAX_THICKNESS * 1.5:
            yield (x0, top, x1, bottom)


def _count_grid_lines_in_bbox(
    page, bbox: Tuple[float, float, float, float], tol: float = 2.0
) -> Tuple[int, int]:
    """Count distinct vertical / horizontal strokes that intersect *bbox*."""
    x0, top, x1, bottom = bbox
    v_xs: List[float] = []
    h_ys: List[float] = []
    for sx0, stop, sx1, sbottom in _iter_page_strokes(page):
        is_v = abs(sx0 - sx1) < 0.75
        is_h = abs(stop - sbottom) < 0.75
        if is_v:
            x = (sx0 + sx1) / 2.0
            y0, y1 = min(stop, sbottom), max(stop, sbottom)
            # Line must run inside the table band and cross it meaningfully.
            if x < x0 - tol or x > x1 + tol:
                continue
            ov = min(y1, bottom) - max(y0, top)
            if ov < max(8.0, (bottom - top) * 0.15):
                continue
            if not any(abs(x - ex) <= tol for ex in v_xs):
                v_xs.append(x)
        elif is_h:
            y = (stop + sbottom) / 2.0
            lx0, lx1 = min(sx0, sx1), max(sx0, sx1)
            if y < top - tol or y > bottom + tol:
                continue
            ov = min(lx1, x1) - max(lx0, x0)
            if ov < max(12.0, (x1 - x0) * 0.15):
                continue
            if not any(abs(y - ey) <= tol for ey in h_ys):
                h_ys.append(y)
    return len(v_xs), len(h_ys)


def _has_drawn_grid(page, bbox: Tuple[float, float, float, float]) -> bool:
    """True when strokes form a real table grid (not just a header underline)."""
    v, h = _count_grid_lines_in_bbox(page, bbox)
    # Minimal grid: 2 vertical + 2 horizontal (one cell), or richer on one axis.
    return (v >= 2 and h >= 2) or (v >= 3 and h >= 1) or (h >= 3 and v >= 1)


def _table_bbox_from_block(tb: TableBlock) -> Tuple[float, float, float, float]:
    x1 = tb.x0 + (sum(tb.col_widths) if tb.col_widths else 0.0)
    if x1 <= tb.x0:
        x1 = tb.x0 + 1.0
    return (tb.x0, tb.top, x1, tb.bottom)


def _words_in_bbox(widx: WordIndex, bbox: Tuple[float, float, float, float], pad: float = 1.0):
    x0, top, x1, bottom = bbox
    return widx.query_rect(x0, top, x1, bottom, pad=pad)


def _estimate_aligned_columns(words, x_tol: float = TEXT_COL_CLUSTER_TOL) -> int:
    """How many distinct left-edge columns the words form (alignment clusters)."""
    if not words:
        return 0
    xs = sorted(float(w["x0"]) for w in words)
    clusters = 0
    prev = None
    for x in xs:
        if prev is None or x - prev > x_tol:
            clusters += 1
            prev = x
        else:
            prev = prev  # keep cluster anchor (first x)
    return clusters


def _table_anchor_stats(tb: TableBlock) -> Tuple[List[Cell], List[str], List[int], List[int]]:
    """Return (anchor cells, filled texts, per-col fill counts, per-row fill counts)."""
    anchors: List[Cell] = []
    col_fill = [0] * tb.cols
    row_fill = [0] * tb.rows
    for r in range(tb.rows):
        for c in range(tb.cols):
            if tb.owner[r][c] != (r, c):
                continue
            cell = tb.cells[r][c]
            if cell is None:
                continue
            anchors.append(cell)
            text = (cell.text or "").strip()
            if text:
                col_fill[c] += 1
                row_fill[r] += 1
    filled_texts = [(c.text or "").strip() for c in anchors if (c.text or "").strip()]
    return anchors, filled_texts, col_fill, row_fill


def _inter_column_text_gaps(tb: TableBlock, widx: WordIndex) -> List[float]:
    """Horizontal gaps between consecutive non-empty cell texts on the same row.

    Large gaps (label …… value) are typical of forms; small gaps mean the
    detector merely split a prose line into adjacent word chips.
    """
    if not tb.col_widths or tb.cols < 2:
        return []
    vx = [tb.x0]
    for w in tb.col_widths:
        vx.append(vx[-1] + w)
    hy = [tb.top]
    for h in tb.row_heights or []:
        hy.append(hy[-1] + h)
    if len(hy) != tb.rows + 1:
        # Heights missing / inconsistent — fall back to word clustering only.
        return []

    gaps: List[float] = []
    for r in range(tb.rows):
        # Collect (col_index, text_x0, text_x1) for non-empty anchors on this row.
        pieces = []
        for c in range(tb.cols):
            if tb.owner[r][c] != (r, c):
                continue
            cell = tb.cells[r][c]
            if cell is None or not (cell.text or "").strip():
                continue
            # Words whose centre falls in this cell's band.
            cx0, cx1 = vx[c], vx[c + cell.colspan]
            cy0, cy1 = hy[r], hy[min(r + cell.rowspan, tb.rows)]
            xs0, xs1 = [], []
            for w in widx.query_rect(cx0, cy0, cx1, cy1, pad=1.0):
                xs0.append(w["x0"])
                xs1.append(w["x1"])
            if not xs0:
                continue
            pieces.append((c, min(xs0), max(xs1)))
        pieces.sort(key=lambda p: p[0])
        for i in range(len(pieces) - 1):
            # Gap from end of left text to start of right text.
            gap = pieces[i + 1][1] - pieces[i][2]
            gaps.append(gap)
    return gaps


def _is_plausible_borderless_table(tb: TableBlock, widx: WordIndex) -> bool:
    """Heuristic gate for tables found without a drawn grid (text strategy).

    pdfplumber's text/text strategy eagerly treats multi-column prose and even
    single-column paragraphs as tables, splitting words across micro-columns.
    Real borderless forms look like compact label/value grids instead.
    """
    if tb.rows < 2 or tb.cols < 2:
        return False
    if tb.cols > TEXT_TABLE_MAX_COLS or tb.rows > TEXT_TABLE_MAX_ROWS:
        return False
    if tb.rows * tb.cols > TEXT_TABLE_MAX_CELLS:
        return False

    anchors, filled_texts, col_fill, row_fill = _table_anchor_stats(tb)
    n_filled = len(filled_texts)
    if n_filled < TEXT_TABLE_MIN_FILLED:
        return False

    # Need stable columns *and* rows (forms), not a single row of word chips.
    strong_cols = sum(1 for n in col_fill if n >= 2)
    strong_rows = sum(1 for n in row_fill if n >= 2)
    if strong_cols < 2 or strong_rows < 2:
        return False

    # Sparse grids from prose alignment (many empty slots) are unreliable.
    n_anchors = max(len(anchors), 1)
    empty_ratio = (n_anchors - n_filled) / n_anchors
    if empty_ratio > 0.55 and tb.cols >= 3:
        return False
    if empty_ratio > 0.65:
        return False

    # Tiny fragments mean the detector split glyphs/words into fake cells.
    tiny = sum(1 for t in filled_texts if len(t) <= 1)
    if tiny / max(n_filled, 1) > 0.2:
        return False
    short = sum(1 for t in filled_texts if len(t) <= 2)
    if short / max(n_filled, 1) > 0.45 and tb.cols >= 3:
        return False

    bbox = _table_bbox_from_block(tb)
    in_words = _words_in_bbox(widx, bbox)
    n_words = len(in_words)
    if n_words < TEXT_TABLE_MIN_FILLED:
        return False

    # Over-segmentation: more non-empty cells than source words.
    if n_filled > n_words + 1:
        return False

    # Detected column count should match how text is actually aligned.
    est_cols = _estimate_aligned_columns(in_words)
    if est_cols <= 1:
        return False
    if tb.cols > max(est_cols + 1, int(est_cols * 1.5) + 1):
        return False

    # Adjacent cells with only word-spacing gaps → prose line, not form columns.
    # Real borderless forms leave a clear gutter (often 30–80+ pt) between fields.
    gaps = _inter_column_text_gaps(tb, widx)
    gap_mid = None
    if gaps:
        gaps_sorted = sorted(gaps)
        gap_mid = gaps_sorted[len(gaps_sorted) // 2]
        # Most inter-cell gaps look like spaces between words on one line.
        if gap_mid < 18.0:
            return False
        tight = sum(1 for g in gaps if g < 12.0)
        if tight / len(gaps) >= 0.4:
            return False

    # Alternating empty grid rows + tight columns ⇒ line-spacing over-segmentation
    # of prose. Real forms may also insert spacer bands, but keep large gutters.
    empty_rows = sum(1 for n in row_fill if n == 0)
    if empty_rows / max(tb.rows, 1) > 0.35 and (gap_mid is None or gap_mid < 40.0):
        return False

    # Very wide "cells" that are really full prose lines: few columns but long text.
    avg_len = sum(len(t) for t in filled_texts) / max(n_filled, 1)
    if tb.cols == 2 and avg_len > 48 and strong_rows < 3:
        # Two long prose columns (article layout) — keep as text, not a table.
        # Short label/value pairs stay (avg_len small).
        longish = sum(1 for t in filled_texts if len(t) > 36)
        if longish / max(n_filled, 1) >= 0.5:
            return False

    return True


def _accept_table(tb: TableBlock, page, widx: WordIndex) -> bool:
    """Keep line-grid tables; only accept borderless ones that look like forms."""
    bbox = _table_bbox_from_block(tb)
    _, filled_texts, _, _ = _table_anchor_stats(tb)
    if not filled_texts:
        return False
    if _has_drawn_grid(page, bbox):
        # Real ruled table: still require a minimal grid shape.
        return tb.rows >= 1 and tb.cols >= 1
    return _is_plausible_borderless_table(tb, widx)


def _page_has_table_strokes(page) -> bool:
    """True when the page has line/edge/thin-rect strokes that may form a grid.

    Used to skip useless line-based ``find_tables`` passes on plain-text pages.
    """
    if page.lines or getattr(page, "edges", None):
        return True
    for rct in page.rects or []:
        try:
            x0, top = float(rct["x0"]), float(rct["top"])
            x1, bottom = float(rct["x1"]), float(rct["bottom"])
        except (KeyError, TypeError, ValueError):
            continue
        w = abs(x1 - x0)
        h = abs(bottom - top)
        if w >= HLINE_MIN_WIDTH and h <= HLINE_MAX_THICKNESS * 1.5:
            return True
        if h >= HLINE_MIN_WIDTH and w <= HLINE_MAX_THICKNESS * 1.5:
            return True
    return False


def _find_tables(page):
    """Detect tables with a hybrid strategy.

    1. Line-based (best for ruled forms).
    2. Mixed lines/text (vertical rules + horizontal text alignment).
    3. Pure text strategy for borderless grids, only when the region is not
       already covered by a line-based table.

    On pages with **no** grid-like strokes, only the text/text strategy runs
    (plain-text fast path: skip three empty line/mixed passes).

    Candidates are de-duplicated here; plausibility filtering (to drop prose
    mis-detected as tables) happens after :func:`_build_table` via
    :func:`_accept_table`.
    """
    line_settings = {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "snap_tolerance": SNAP_TOLERANCE,
        "intersection_tolerance": SNAP_TOLERANCE,
    }
    mixed_vh = {
        "vertical_strategy": "lines",
        "horizontal_strategy": "text",
        "snap_tolerance": SNAP_TOLERANCE,
        "intersection_tolerance": SNAP_TOLERANCE,
        "text_tolerance": 3,
        "text_x_tolerance": 3,
        "text_y_tolerance": 3,
    }
    mixed_hv = {
        "vertical_strategy": "text",
        "horizontal_strategy": "lines",
        "snap_tolerance": SNAP_TOLERANCE,
        "intersection_tolerance": SNAP_TOLERANCE,
        "text_tolerance": 3,
        "text_x_tolerance": 3,
        "text_y_tolerance": 3,
    }
    text_settings = {
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
        "snap_tolerance": SNAP_TOLERANCE,
        "intersection_tolerance": SNAP_TOLERANCE,
        "text_tolerance": 3,
        "text_x_tolerance": 3,
        "text_y_tolerance": 3,
        # Slightly stricter than pdfplumber defaults so single-column prose
        # is less likely to form a micro-grid of word chips.
        "min_words_vertical": 3,
        "min_words_horizontal": 2,
    }
    if _page_has_table_strokes(page):
        settings_list = [line_settings, mixed_vh, mixed_hv, text_settings]
    else:
        # Plain / borderless: one text strategy only (no empty line passes).
        settings_list = [text_settings]
    kept = []
    page_area = max(
        (float(getattr(page, "width", 0) or 0))
        * (float(getattr(page, "height", 0) or 0)),
        1.0,
    )
    for settings in settings_list:
        try:
            found = page.find_tables(settings) or []
        except Exception:
            found = []
        for t in found:
            bbox = t.bbox
            # Need at least a 2×1 or 1×2 structure after build; skip tiny noise.
            if (bbox[2] - bbox[0]) < 20 or (bbox[3] - bbox[1]) < 10:
                continue
            if any(
                _table_bbox_overlap_ratio(bbox, existing.bbox) >= TABLE_OVERLAP_REJECT
                for existing in kept
            ):
                continue
            kept.append(t)
        # Short-circuit: if line-based strategies already cover most of the page,
        # skip the expensive text/text strategy (which often mis-detects prose).
        if kept:
            covered = sum(
                (t.bbox[2] - t.bbox[0]) * (t.bbox[3] - t.bbox[1]) for t in kept
            )
            if covered / page_area > 0.5:
                break
    return kept


__all__ = [
    "_index_of",
    "_build_table",
    "_paragraphs_to_text",
    "_region_paragraphs",
    "_refine_merges_from_words",
    "_rgb_to_hex",
    "_cell_borders",
    "_table_border",
    "_table_bbox_overlap_ratio",
    "_iter_page_strokes",
    "_count_grid_lines_in_bbox",
    "_has_drawn_grid",
    "_table_bbox_from_block",
    "_words_in_bbox",
    "_estimate_aligned_columns",
    "_table_anchor_stats",
    "_inter_column_text_gaps",
    "_is_plausible_borderless_table",
    "_accept_table",
    "_find_tables",
]
