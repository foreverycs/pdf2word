from __future__ import annotations

from typing import List, Optional

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_ROW_HEIGHT_RULE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

from .pdf_reader import PageContent, TextBlock, TableBlock, Cell

# ----- style defaults -------------------------------------------------------
DEFAULT_FONT = "宋体"
DEFAULT_SIZE = 10.5

_HALIGN = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
}
_VALIGN = {
    "top": WD_ALIGN_VERTICAL.TOP,
    "center": WD_ALIGN_VERTICAL.CENTER,
    "bottom": WD_ALIGN_VERTICAL.BOTTOM,
}

# Map common embedded PDF font names to the Word font that renders them.
_FONT_MAP = {
    "simsun": "宋体",
    "simhei": "黑体",
    "microsoftyahei": "微软雅黑",
    "stsong": "华文宋体",
    "stxihei": "华文细黑",
    "stkaiti": "华文楷体",
    "kaiti": "楷体",
    "fangsong": "仿宋",
}


def _normalize_font(name: Optional[str]) -> str:
    if not name:
        return DEFAULT_FONT
    # strip embedded-subset prefix, e.g. "ABCDEF+SimSun" -> "SimSun"
    if "+" in name:
        name = name.split("+", 1)[1]
    lower = name.lower()
    for key, value in _FONT_MAP.items():
        if key in lower:
            return value
    return name


def _is_bold(font_name: Optional[str]) -> bool:
    return bool(font_name) and "bold" in font_name.lower()


def _set_run_font(run, font_name: Optional[str], font_size: Optional[float],
                  bold: bool) -> None:
    name = _normalize_font(font_name)
    run.font.name = name
    run.font.size = Pt(font_size) if font_size else Pt(DEFAULT_SIZE)
    run.font.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia"):
        rfonts.set(qn(attr), name)


def _set_cell_text(cell, text: str, font_name: Optional[str],
                   font_size: Optional[float], bold: bool) -> None:
    lines = (text or "").split("\n")
    # first line via cell.text (creates the primary run); remaining lines are
    # appended after explicit line breaks so intra-cell newlines are preserved.
    cell.text = lines[0] if lines else ""
    paragraph = cell.paragraphs[0]
    for run in paragraph.runs:
        _set_run_font(run, font_name, font_size, bold)
    for line in lines[1:]:
        br = paragraph.add_run("")
        br.add_break()
        run = paragraph.add_run(line)
        _set_run_font(run, font_name, font_size, bold)


def _set_cell_borders(cell, borders: Optional[dict]) -> None:
    if not borders:
        return
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_b = tc_pr.find(qn("w:tcBorders"))
    if tc_b is None:
        tc_b = OxmlElement("w:tcBorders")
        tc_pr.append(tc_b)
    else:
        tc_b.clear()
    for edge in ("top", "left", "bottom", "right"):
        val = borders.get(edge)
        if not val:
            continue
        w_pt, color, dashed = val
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "dashed" if dashed else "single")
        el.set(qn("w:sz"), str(int(round(w_pt * 8))))
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color)
        tc_b.append(el)


def _set_table_borders(table, outer_pt: float, inner_pt: float,
                       color: str, dashed: bool) -> None:
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    else:
        borders.clear()
    val = "dashed" if dashed else "single"
    edges = {
        "top": outer_pt, "left": outer_pt, "bottom": outer_pt, "right": outer_pt,
        "insideH": inner_pt, "insideV": inner_pt,
    }
    for name, pt in edges.items():
        el = OxmlElement(f"w:{name}")
        el.set(qn("w:val"), val)
        el.set(qn("w:sz"), str(int(round(pt * 8))))
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color)
        borders.append(el)


def _set_cell_shading(cell, hex_color: str) -> None:
    if not hex_color:
        return
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)


def _set_fixed_layout(table) -> None:
    tbl_pr = table._tbl.tblPr
    layout = tbl_pr.find(qn("w:tblLayout"))
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        tbl_pr.append(layout)
    layout.set(qn("w:type"), "fixed")


def _set_table_dims(table, col_widths: List[float], row_heights: List[float]) -> None:
    if col_widths:
        table.width = Pt(sum(col_widths))
        for i, col in enumerate(table.columns):
            if i < len(col_widths):
                col.width = Pt(col_widths[i])
    if row_heights:
        for i, row in enumerate(table.rows):
            if i < len(row_heights):
                row.height = Pt(row_heights[i])
                row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
    _set_fixed_layout(table)


def _write_table(doc: Document, table: TableBlock) -> None:
    grid = doc.add_table(rows=table.rows, cols=table.cols)
    try:
        grid.style = "Table Grid"
    except KeyError:
        pass

    has_cell_borders = False
    for r in range(table.rows):
        for c in range(table.cols):
            anchor = table.owner[r][c]
            if anchor != (r, c):
                continue  # covered cell of a merge; absorbed by the anchor
            cell_obj: Cell = table.cells[r][c]
            target = grid.cell(r, c)
            if cell_obj.rowspan > 1 or cell_obj.colspan > 1:
                end_r = min(r + cell_obj.rowspan - 1, table.rows - 1)
                end_c = min(c + cell_obj.colspan - 1, table.cols - 1)
                target = target.merge(grid.cell(end_r, end_c))
            _set_cell_text(
                target, cell_obj.text,
                font_name=cell_obj.font_name, font_size=cell_obj.font_size,
                bold=_is_bold(cell_obj.font_name),
            )
            target.vertical_alignment = _VALIGN.get(cell_obj.valign, WD_ALIGN_VERTICAL.TOP)
            for paragraph in target.paragraphs:
                paragraph.alignment = _HALIGN.get(cell_obj.align, WD_ALIGN_PARAGRAPH.LEFT)
            _set_cell_shading(target, cell_obj.bg_color or "")
            if cell_obj.borders:
                has_cell_borders = True
                _set_cell_borders(target, cell_obj.borders)

    # Per-cell borders win when present (they already cover every drawn edge,
    # including merges). Otherwise fall back to a uniform grid so borderless /
    # text-strategy tables still render as a table.
    if not has_cell_borders:
        _set_table_borders(
            grid, table.border_outer, table.border_inner,
            table.border_color, table.border_dashed,
        )
    _set_table_dims(grid, table.col_widths, table.row_heights)


def write_document(
    pages: List[PageContent],
    output_path: str,
    *,
    page_breaks: bool = True,
) -> None:
    """Write extracted pages to a .docx file.

    When ``page_breaks`` is True (default), a page break is inserted between
    consecutive PDF pages so the Word document mirrors the source pagination.
    """
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = DEFAULT_FONT
    style.font.size = Pt(DEFAULT_SIZE)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), DEFAULT_FONT)

    for i, page in enumerate(pages):
        if page_breaks and i > 0:
            doc.add_page_break()
        for block in page.blocks:
            if isinstance(block, TextBlock):
                p = doc.add_paragraph()
                p.alignment = _HALIGN.get(block.align, WD_ALIGN_PARAGRAPH.LEFT)
                run = p.add_run(block.text)
                _set_run_font(run, block.font_name, block.font_size,
                              _is_bold(block.font_name))
            elif isinstance(block, TableBlock):
                _write_table(doc, block)

    doc.save(output_path)
