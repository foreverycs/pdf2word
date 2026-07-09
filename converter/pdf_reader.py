from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pdfplumber

# ----- tuning constants -----------------------------------------------------
SNAP_TOLERANCE = 3.0        # grid line snapping tolerance (pt)
LINE_GAP = 3.0              # max vertical gap (pt) to group words into one text line


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


@dataclass
class TextBlock:
    text: str
    top: float = 0.0
    font_size: Optional[float] = None
    font_name: Optional[str] = None
    align: str = "left"                 # horizontal alignment: left/center/right


@dataclass
class PageContent:
    blocks: List  # ordered list of TextBlock | TableBlock (top-to-bottom)


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
    return TableBlock(rows=nrows, cols=ncols, cells=cells, owner=owner,
                      col_widths=col_widths, row_heights=row_heights, **border)


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


def _extract_text_blocks(page, table_bboxes, words) -> List[TextBlock]:
    outside = [w for w in words if not _in_any_bbox(w, table_bboxes)]
    if not outside:
        return []

    # group words into lines by vertical proximity, then glue into paragraphs
    outside.sort(key=lambda w: (round(w["top"]), w["x0"]))
    lines: List[List[dict]] = []
    for w in outside:
        if lines and (w["top"] - lines[-1][-1]["bottom"]) <= LINE_GAP:
            lines[-1].append(w)
        else:
            lines.append([w])
    page_w = getattr(page, "width", None)
    blocks = []
    for line in lines:
        ordered = sorted(line, key=lambda w: w["x0"])
        text = _normalize_spacing(_join_words(ordered))
        top = min(w["top"] for w in line)
        if text.strip():
            counter: Counter = Counter()
            for w in line:
                counter[(round(w.get("size") or 0.0, 1), w.get("fontname") or "")] += 1
            (size, fname), _ = counter.most_common(1)[0]
            # horizontal alignment: determined by where the first line starts.
            # A centred title starts near the page centre; a left-aligned body
            # paragraph starts near the left margin.
            align = "left"
            if page_w:
                left_edge = ordered[0]["x0"] / page_w
                if 0.25 < left_edge < 0.55:
                    align = "center"
                elif left_edge > 0.6:
                    align = "right"
            blocks.append(TextBlock(
                text=text.strip(), top=top,
                font_size=size or None, font_name=fname or None,
                align=align,
            ))
    return blocks


def _extract_page(page) -> PageContent:
    # Extract words (with font info) and lines once for the whole page and reuse
    # them for every table and the text blocks, instead of re-parsing per table.
    words = page.extract_words(
        use_text_flow=False, keep_blank_chars=False, extra_attrs=["fontname", "size"]
    )
    raw_tables = _find_tables(page)
    tables = []          # list of (top, TableBlock)
    bboxes = []
    for t in raw_tables:
        tb = _build_table(t, page, words)
        if tb is not None:
            tables.append((t.bbox[1], tb))
            bboxes.append(t.bbox)

    text_blocks = _extract_text_blocks(page, bboxes, words)

    # interleave text and tables by vertical position (top edge, top-to-bottom)
    ordered = [(top, tb) for top, tb in tables] + [(b.top, b) for b in text_blocks]
    ordered.sort(key=lambda item: item[0])
    blocks: List = [tb for _, tb in ordered]
    return PageContent(blocks=blocks)


def extract_document(pdf_path: str) -> List[PageContent]:
    pages: List[PageContent] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages.append(_extract_page(page))
    return pages
