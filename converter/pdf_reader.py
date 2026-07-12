from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pdfplumber

# ----- tuning constants -----------------------------------------------------
SNAP_TOLERANCE = 3.0        # grid line snapping tolerance (pt)
LINE_GAP = 3.0              # max vertical gap (pt) to group words into one text line
# max horizontal gap (pt) to keep words in the same text segment; larger gaps
# split one visual line into multiple blocks (e.g. "检验：" … "审核：").
TEXT_COL_GAP = 40.0
MIN_IMAGE_AREA = 40.0 * 40.0  # skip decorative icons smaller than this (pt²)
MAX_IMAGES_PER_PAGE = 15
IMAGE_RENDER_DPI = 144
# Thin filled rectangles / strokes treated as horizontal rules (pt).
HLINE_MAX_THICKNESS = 2.5
HLINE_MIN_WIDTH = 40.0



@dataclass
class Cell:
    text: str
    rowspan: int = 1
    colspan: int = 1
    font_size: Optional[float] = None   # dominant font size (pt) in the cell
    font_name: Optional[str] = None     # dominant PDF font name in the cell
    align: str = "left"                 # horizontal alignment: left/center/right
    valign: str = "top"                 # vertical alignment: top/center/bottom
    bg_color: Optional[str] = None        # cell fill colour as RRGGBB hex
    # per-edge borders: dict with keys top/left/bottom/right, each
    # (width_pt, color_hex, dashed) or omitted when no line exists on that edge.
    borders: Optional[dict] = None


@dataclass
class TableBlock:
    rows: int
    cols: int
    # cells[r][c] holds a Cell only at the top-left (anchor) of a (possibly merged)
    # region. Covered cells are None. owner[r][c] points to the anchor (r, c).
    cells: List[List[Optional[Cell]]]
    owner: List[List[Tuple[int, int]]]
    col_widths: List[float] = field(default_factory=list)   # column widths (pt)
    row_heights: List[float] = field(default_factory=list)  # row heights (pt)
    border_outer: float = 0.5           # outer border width (pt)
    border_inner: float = 0.5           # inner grid line width (pt)
    border_color: str = "000000"        # border colour as RRGGBB hex
    border_dashed: bool = False         # whether borders are dashed
    top: float = 0.0                    # vertical position on page (pt)
    bottom: float = 0.0
    x0: float = 0.0                     # left edge of table bbox (pt)


@dataclass
class TextBlock:
    text: str
    top: float = 0.0
    bottom: float = 0.0
    x0: float = 0.0                     # left edge on the PDF page (pt)
    x1: float = 0.0                     # right edge on the PDF page (pt)
    font_size: Optional[float] = None
    font_name: Optional[str] = None
    align: str = "left"                 # horizontal alignment: left/center/right



@dataclass
class ImageBlock:
    """Raster image extracted (or rendered) from the PDF page."""
    image_bytes: bytes                  # PNG bytes
    top: float = 0.0
    bottom: float = 0.0
    x0: float = 0.0                     # left edge on the PDF page (pt)
    width_pt: float = 0.0               # display width in PDF points
    height_pt: float = 0.0
    page_width: float = 0.0             # source page width (pt), for placement
    align: str = "left"                 # left/center/right relative to page


@dataclass
class LineBlock:
    """Standalone horizontal rule (header underline, separator, …)."""
    top: float = 0.0
    bottom: float = 0.0
    x0: float = 0.0
    x1: float = 0.0
    thickness: float = 0.5              # stroke / fill height (pt)
    color: str = "000000"


@dataclass
class PageContent:
    blocks: List  # ordered TextBlock | TableBlock | ImageBlock | LineBlock
    width: float = 0.0                  # page width (pt)
    height: float = 0.0                 # page height (pt)


# ----- low level helpers ----------------------------------------------------
_CJK_RE = re.compile(
    r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]"
)


def _has_cjk(s: str) -> bool:
    return bool(_CJK_RE.search(s or ""))


def _join_words(words: list) -> str:
    """Join pdfplumber words into a line, keeping CJK characters tight (no
    space between adjacent CJK glyphs) while preserving spaces between Latin
    words and at CJK/Latin boundaries."""
    out = []
    prev = None
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        txt = w["text"]
        if prev is None:
            out.append(txt)
        else:
            prev_cjk = _has_cjk(prev["text"])
            cur_cjk = _has_cjk(txt)
            gap = w["x0"] - prev["x1"]
            if prev_cjk and cur_cjk:
                out.append(txt)
            elif not prev_cjk and not cur_cjk:
                out.append((" " + txt) if gap > 1.0 else txt)
            elif prev_cjk and not cur_cjk:
                out.append(" " + txt)
            else:  # Latin followed by CJK: keep them tight
                out.append(txt)
        prev = w
    return "".join(out)


_SP_RE = re.compile(
    r"(?<=[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef])"
    r" +"
    r"(?=[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef])"
)


def _normalize_spacing(text: str) -> str:
    """Remove spaces that sit between two CJK characters (some extractors
    insert one between every glyph)."""
    return _SP_RE.sub("", text)


# Matches a \n that is a soft word-wrap (NOT a real paragraph break).
# A real break is one preceded by sentence-ending punctuation or followed by a
# numbered list marker; everything else is a soft wrap from PDF auto-layout.
_SOFT_NL_RE = re.compile(r"(?<![。！？；：])\n(?!\d[、.])")


def _normalize_newlines(text: str) -> str:
    """Replace soft word-wrap \\n from PDF auto-layout with spaces, while
    preserving real paragraph/list breaks (e.g. after 。 or before 1、)."""
    return _SOFT_NL_RE.sub(" ", text)


def _index_of(value: float, bounds: List[float]) -> Optional[int]:
    for i in range(len(bounds) - 1):
        if bounds[i] <= value < bounds[i + 1]:
            return i
    if bounds and value >= bounds[-1]:
        return len(bounds) - 2
    return None


def _build_table(table, page, words) -> Optional[TableBlock]:
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

    # `words` is the page-level word list (extracted once by the caller) carrying
    # font name/size; we only keep the ones inside this table's bbox.
    x0, top, x1, bottom = table.bbox
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
    for w in words:
        if w["x1"] < x0 - 1 or w["x0"] > x1 + 1 or w["bottom"] < top - 1 or w["top"] > bottom + 1:
            continue
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

        if cells[r_start][c_start] is not None:
            continue
        for rr in range(r_start, r_end + 1):
            for cc in range(c_start, c_end + 1):
                owner[rr][cc] = (r_start, c_start)
        cells[r_start][c_start] = Cell(text=text, rowspan=rowspan, colspan=colspan,
                                       font_size=font_size, font_name=font_name,
                                       align=align, valign=valign, bg_color=bg_color,
                                       borders=borders or None)

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


def _find_tables(page):
    settings_lines = {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "snap_tolerance": SNAP_TOLERANCE,
    }
    settings_text = {
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
        "snap_tolerance": SNAP_TOLERANCE,
    }
    tables = page.find_tables(settings_lines)
    if not tables:
        tables = page.find_tables(settings_text)
    return tables


def _in_any_bbox(word: dict, bboxes: List[Tuple[float, float, float, float]]) -> bool:
    cx = (word["x0"] + word["x1"]) / 2
    cy = (word["top"] + word["bottom"]) / 2
    for (bx0, btop, bx1, bbottom) in bboxes:
        if bx0 - 1 <= cx <= bx1 + 1 and btop - 1 <= cy <= bbottom + 1:
            return True
    return False


def _text_h_align(x0: float, x1: float, page_w: float) -> str:
    """Infer paragraph alignment from the text bbox relative to the page."""
    if page_w <= 0:
        return "left"
    width = max(x1 - x0, 1.0)
    left_pad = max(x0, 0.0)
    right_pad = max(page_w - x1, 0.0)
    mid = (x0 + x1) / 2.0 / page_w
    # Full-ish width lines stay left.
    if width / page_w >= 0.7:
        return "left"
    # Balanced side margins or midpoint near page centre → centre.
    if abs(left_pad - right_pad) <= max(page_w * 0.12, 18.0) or 0.38 < mid < 0.62:
        # Prefer centre only when not clearly flush-left (logo-adjacent labels
        # often start past 0.25 of the page but are not titles).
        if left_pad > page_w * 0.18 or abs(left_pad - right_pad) <= page_w * 0.12:
            return "center"
    if left_pad > right_pad * 2.0 and left_pad / page_w > 0.45:
        return "right"
    return "left"


def _extract_text_blocks(page, table_bboxes, words) -> List[TextBlock]:
    outside = [w for w in words if not _in_any_bbox(w, table_bboxes)]
    if not outside:
        return []

    # group words into lines by vertical proximity
    outside.sort(key=lambda w: (round(w["top"]), w["x0"]))
    lines: List[List[dict]] = []
    for w in outside:
        if lines and (w["top"] - lines[-1][-1]["bottom"]) <= LINE_GAP:
            lines[-1].append(w)
        else:
            lines.append([w])
    page_w = float(getattr(page, "width", 0) or 0)
    blocks: List[TextBlock] = []
    for line in lines:
        ordered = sorted(line, key=lambda w: w["x0"])
        # Split a visual line into horizontal segments when words sit far apart
        # (form labels on opposite sides, header title next to logo, etc.).
        segments: List[List[dict]] = []
        current: List[dict] = []
        prev_x1 = None
        for w in ordered:
            if current and prev_x1 is not None and (w["x0"] - prev_x1) > TEXT_COL_GAP:
                segments.append(current)
                current = []
            current.append(w)
            prev_x1 = w["x1"]
        if current:
            segments.append(current)

        for seg in segments:
            text = _normalize_spacing(_join_words(seg))
            if not text.strip():
                continue
            top = min(w["top"] for w in seg)
            bottom = max(w["bottom"] for w in seg)
            x0 = min(w["x0"] for w in seg)
            x1 = max(w["x1"] for w in seg)
            counter: Counter = Counter()
            for w in seg:
                counter[(round(w.get("size") or 0.0, 1), w.get("fontname") or "")] += 1
            (size, fname), _ = counter.most_common(1)[0]
            align = _text_h_align(x0, x1, page_w)
            blocks.append(TextBlock(
                text=text.strip(),
                top=top, bottom=bottom, x0=x0, x1=x1,
                font_size=size or None, font_name=fname or None,
                align=align,
            ))
    return blocks



def _bbox_overlap_ratio(a, b) -> float:
    """Intersection area of ``a`` over area of ``a`` (both x0,top,x1,bottom)."""
    ax0, atop, ax1, abottom = a
    bx0, btop, bx1, bbottom = b
    ix0, ix1 = max(ax0, bx0), min(ax1, bx1)
    it0, it1 = max(atop, btop), min(abottom, bbottom)
    if ix1 <= ix0 or it1 <= it0:
        return 0.0
    inter = (ix1 - ix0) * (it1 - it0)
    area = max((ax1 - ax0) * (abottom - atop), 1e-6)
    return inter / area


def _render_region_png(page, bbox, resolution: int = IMAGE_RENDER_DPI) -> Optional[bytes]:
    """Rasterise a page region to PNG bytes. Returns None on failure."""
    try:
        cropped = page.crop(bbox, strict=False)
        pil = cropped.to_image(resolution=resolution).original
        if pil is None:
            return None
        # Drop nearly-blank crops (e.g. failed extract of vector-only art).
        extrema = pil.convert("L").getextrema()
        if extrema is not None and extrema[0] == extrema[1]:
            return None
        buf = io.BytesIO()
        pil.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        return None


def _image_h_align(x0: float, width: float, page_w: float) -> str:
    """Infer horizontal placement of an image relative to the page content box."""
    if page_w <= 0 or width <= 0:
        return "left"
    # Near full width → treat as centered full-bleed content.
    if width / page_w >= 0.85:
        return "center"
    left_pad = max(x0, 0.0)
    right_pad = max(page_w - (x0 + width), 0.0)
    # Balanced side margins → centre; otherwise keep flush to the denser side.
    if abs(left_pad - right_pad) <= max(page_w * 0.08, 12.0):
        return "center"
    if left_pad > right_pad * 2.0 and left_pad / page_w > 0.2:
        return "right"
    return "left"


def _extract_images(page, table_bboxes) -> List[ImageBlock]:
    """Pull embedded image regions that sit outside tables."""
    raw = getattr(page, "images", None) or []
    if not raw:
        return []

    page_w = float(getattr(page, "width", 0) or 0)
    page_h = float(getattr(page, "height", 0) or 0)
    page_area = max(page_w * page_h, 1.0)
    blocks: List[ImageBlock] = []

    # Sort top-to-bottom, left-to-right for stable ordering.
    ordered_imgs = sorted(
        raw,
        key=lambda im: (round(im.get("top", 0), 1), round(im.get("x0", 0), 1)),
    )
    for img in ordered_imgs:
        if len(blocks) >= MAX_IMAGES_PER_PAGE:
            break
        try:
            x0 = float(img["x0"])
            top = float(img["top"])
            x1 = float(img["x1"])
            bottom = float(img["bottom"])
        except (KeyError, TypeError, ValueError):
            continue
        w, h = x1 - x0, bottom - top
        if w <= 1 or h <= 1 or w * h < MIN_IMAGE_AREA:
            continue
        # Skip near-full-page images here; empty-page fallback handles scans.
        if page_area > 0 and (w * h) / page_area > 0.85:
            continue
        bbox = (x0, top, x1, bottom)
        if any(_bbox_overlap_ratio(bbox, tb) > 0.5 for tb in table_bboxes):
            continue
        png = _render_region_png(page, bbox)
        if not png:
            continue
        blocks.append(ImageBlock(
            image_bytes=png,
            top=top,
            bottom=bottom,
            x0=x0,
            width_pt=w,
            height_pt=h,
            page_width=page_w,
            align=_image_h_align(x0, w, page_w),
        ))
    return blocks


def _render_full_page_image(page) -> Optional[ImageBlock]:
    """Fallback for scanned / image-only pages: embed a full-page raster."""
    try:
        w = float(getattr(page, "width", 0) or 0)
        h = float(getattr(page, "height", 0) or 0)
        if w <= 0 or h <= 0:
            return None
        png = _render_region_png(page, (0, 0, w, h), resolution=IMAGE_RENDER_DPI)
        if not png:
            return None
        return ImageBlock(
            image_bytes=png,
            top=0.0,
            bottom=h,
            x0=0.0,
            width_pt=w,
            height_pt=h,
            page_width=w,
            align="center",
        )
    except Exception:
        return None


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


def _extract_page(page) -> PageContent:
    # Extract words (with font info) and lines once for the whole page and reuse
    # them for every table and the text blocks, instead of re-parsing per table.
    words = page.extract_words(
        use_text_flow=False, keep_blank_chars=False, extra_attrs=["fontname", "size"]
    )
    page_w = float(getattr(page, "width", 0) or 0)
    page_h = float(getattr(page, "height", 0) or 0)
    raw_tables = _find_tables(page)
    tables = []          # list of (top, TableBlock)
    bboxes = []
    for t in raw_tables:
        tb = _build_table(t, page, words)
        if tb is not None:
            tables.append((tb.top, tb))
            bboxes.append(t.bbox)

    text_blocks = _extract_text_blocks(page, bboxes, words)
    image_blocks = _extract_images(page, bboxes)
    line_blocks = _extract_hlines(page, bboxes)

    # interleave text, tables, images and rules by vertical position
    ordered = (
        [(top, tb) for top, tb in tables]
        + [(b.top, b) for b in text_blocks]
        + [(b.top, b) for b in image_blocks]
        + [(b.top, b) for b in line_blocks]
    )
    ordered.sort(key=lambda item: item[0])
    blocks: List = [tb for _, tb in ordered]

    # Scanned / image-only page: no extractable text or tables → embed page image.
    has_text_or_table = any(
        isinstance(b, (TextBlock, TableBlock)) for b in blocks
    )
    if not has_text_or_table:
        full = _render_full_page_image(page)
        if full is not None:
            # keep any pure lines if present, but full-page image is primary
            blocks = [full]

    return PageContent(blocks=blocks, width=page_w, height=page_h)


def parse_page_range(spec: Optional[str], total_pages: int) -> List[int]:
    """Parse a 1-based page range like ``1-3,5,7-9`` into 0-based indices.

    Empty / whitespace ``spec`` means all pages. Raises ``ValueError`` on
    malformed input or out-of-range numbers.
    """
    if total_pages < 1:
        raise ValueError("PDF has no pages")
    if not spec or not str(spec).strip():
        return list(range(total_pages))

    indices: List[int] = []
    seen = set()
    for part in str(spec).split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            ends = token.split("-", 1)
            if len(ends) != 2 or not ends[0].strip() or not ends[1].strip():
                raise ValueError(f"Invalid page range: {token!r}")
            try:
                start = int(ends[0].strip())
                end = int(ends[1].strip())
            except ValueError as exc:
                raise ValueError(f"Invalid page range: {token!r}") from exc
            if start < 1 or end < 1 or start > end:
                raise ValueError(f"Invalid page range: {token!r}")
            for n in range(start, end + 1):
                if n > total_pages:
                    raise ValueError(
                        f"Page {n} out of range (PDF has {total_pages} pages)"
                    )
                idx = n - 1
                if idx not in seen:
                    seen.add(idx)
                    indices.append(idx)
        else:
            try:
                n = int(token)
            except ValueError as exc:
                raise ValueError(f"Invalid page number: {token!r}") from exc
            if n < 1 or n > total_pages:
                raise ValueError(
                    f"Page {n} out of range (PDF has {total_pages} pages)"
                )
            idx = n - 1
            if idx not in seen:
                seen.add(idx)
                indices.append(idx)

    if not indices:
        raise ValueError("No pages selected")
    return indices


def count_blocks(pages: List[PageContent]) -> dict:
    """Return simple conversion stats for response headers / UI."""
    tables = sum(
        1 for p in pages for b in p.blocks if isinstance(b, TableBlock)
    )
    texts = sum(
        1 for p in pages for b in p.blocks if isinstance(b, TextBlock)
    )
    images = sum(
        1 for p in pages for b in p.blocks if isinstance(b, ImageBlock)
    )
    lines = sum(
        1 for p in pages for b in p.blocks if isinstance(b, LineBlock)
    )
    return {
        "pages": len(pages),
        "tables": tables,
        "text_blocks": texts,
        "images": images,
        "lines": lines,
    }


def content_warnings(pages: List[PageContent]) -> List[str]:
    """Heuristic warnings for the UI (scanned PDF, empty extract, …)."""
    stats = count_blocks(pages)
    warnings: List[str] = []
    if stats["pages"] == 0:
        warnings.append("empty")
        return warnings
    if (
        stats["tables"] == 0
        and stats["text_blocks"] == 0
        and stats["images"] == 0
    ):
        warnings.append("empty")
    elif stats["tables"] == 0 and stats["text_blocks"] == 0 and stats["images"] > 0:
        warnings.append("image_only")
    return warnings


def _friendly_open_error(exc: BaseException) -> str:
    msg = str(exc).lower()
    if any(k in msg for k in ("password", "encrypt", "crypt")):
        return "PDF is password-protected; please decrypt it first"
    return f"Cannot open PDF: {exc}"


def extract_document(
    pdf_path: str,
    page_range: Optional[str] = None,
) -> List[PageContent]:
    """Extract structured content from a PDF.

    ``page_range`` is an optional 1-based spec (e.g. ``"1-3,5"``). When
    omitted, every page is processed.

    Image-only / scanned pages are embedded as full-page rasters so content
    is not silently lost (OCR is not applied).
    """
    pages: List[PageContent] = []
    try:
        pdf = pdfplumber.open(pdf_path)
    except Exception as exc:
        raise ValueError(_friendly_open_error(exc)) from exc

    try:
        total = len(pdf.pages)
        if total == 0:
            raise ValueError("PDF has no pages")
        indices = parse_page_range(page_range, total)
        for i in indices:
            try:
                pages.append(_extract_page(pdf.pages[i]))
            except Exception as exc:
                raise ValueError(
                    _friendly_open_error(exc)
                    if any(k in str(exc).lower() for k in ("password", "encrypt", "crypt"))
                    else f"Failed to read page {i + 1}: {exc}"
                ) from exc
    finally:
        pdf.close()
    return pages
