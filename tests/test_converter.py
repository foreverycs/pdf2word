import os
import tempfile

import pytest

from converter import extract_document, write_document
from converter.pdf_reader import TableBlock, TextBlock


def _make_sample_pdf(path: str) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    from reportlab.lib import colors

    doc = SimpleDocTemplate(path, pagesize=A4)
    data = [
        ["Name", "Info", "Score"],
        ["Alice", "Math", "90"],
        ["Bob", "Physics", "85"],
    ]
    table = Table(data, colWidths=[80, 80, 80])
    style = TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        # merge "Name" with the empty area below by spanning first col rows 0-1
        ("SPAN", (0, 0), (0, 1)),
    ])
    table.setStyle(style)
    doc.build([table])


def test_roundtrip_preserves_grid_and_merge():
    tmp = tempfile.mkdtemp(prefix="pdf2word_test_")
    pdf_path = os.path.join(tmp, "sample.pdf")
    docx_path = os.path.join(tmp, "out.docx")
    _make_sample_pdf(pdf_path)

    pages = extract_document(pdf_path)
    assert pages, "no pages extracted"
    # find the table block among page blocks
    table: TableBlock = next(
        (b for b in pages[0].blocks if isinstance(b, TableBlock)), None
    )
    assert table is not None, "table not detected"
    assert table.rows == 3, table.rows
    assert table.cols == 3, table.cols

    # the merged cell (0,0) should report rowspan 2
    anchor = table.cells[0][0]
    assert anchor is not None
    assert anchor.rowspan == 2, anchor.rowspan
    assert anchor.text == "Name", anchor.text

    write_document(pages, docx_path)
    assert os.path.exists(docx_path)

    # read back and verify the merge produced a single cell object
    from docx import Document
    doc = Document(docx_path)
    assert doc.tables, "no table in docx"
    doc_table = doc.tables[0]
    assert len(doc_table.rows) == 3
    assert len(doc_table.columns) == 3
    # covered cell (1,0) must share the same underlying XML cell as (0,0)
    assert doc_table.cell(1, 0)._tc is doc_table.cell(0, 0)._tc


def test_text_block_extracted():
    tmp = tempfile.mkdtemp(prefix="pdf2word_test_")
    pdf_path = os.path.join(tmp, "t.pdf")
    docx_path = os.path.join(tmp, "t.docx")
    _make_sample_pdf(pdf_path)
    pages = extract_document(pdf_path)
    assert any(isinstance(b, TextBlock) or isinstance(b, TableBlock)
               for b in pages[0].blocks)


def _make_styled_pdf(path: str) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet

    doc = SimpleDocTemplate(path, pagesize=A4)
    style = getSampleStyleSheet()["Normal"]
    style.fontSize = 14
    style.fontName = "Helvetica"
    tbl = Table([[Paragraph("Big", style), "Small"],
                 ["X", "Y"]], colWidths=[90, 90])
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 2.5, colors.black),
        ("BOX", (0, 0), (-1, -1), 2.5, colors.black),
    ]))
    doc.build([tbl])


def _border_sz(doc_table, edge: str) -> int:
    from docx.oxml.ns import qn
    borders = doc_table._tbl.tblPr.find(qn("w:tblBorders"))
    el = borders.find(qn(f"w:{edge}"))
    return int(el.get(qn("w:sz")))


def _cell_border_sz(doc_table, row: int, col: int, edge: str) -> int:
    from docx.oxml.ns import qn
    tc_pr = doc_table.cell(row, col)._tc.tcPr
    tc_b = tc_pr.find(qn("w:tcBorders"))
    el = tc_b.find(qn(f"w:{edge}"))
    return int(el.get(qn("w:sz")))


def test_fidelity_font_size_and_border():
    tmp = tempfile.mkdtemp(prefix="pdf2word_test_")
    pdf_path = os.path.join(tmp, "styled.pdf")
    docx_path = os.path.join(tmp, "styled.docx")
    _make_styled_pdf(pdf_path)

    pages = extract_document(pdf_path)
    table = next(b for b in pages[0].blocks if isinstance(b, TableBlock))
    assert table.border_outer == 2.5

    write_document(pages, docx_path)
    from docx import Document
    from docx.shared import Pt
    doc = Document(docx_path)
    doc_table = doc.tables[0]

    # per-cell border width is preserved (sz is in eighths of a point)
    assert _cell_border_sz(doc_table, 0, 0, "top") == 20  # 2.5pt * 8
    assert _cell_border_sz(doc_table, 0, 0, "right") == 20

    # cell font size is preserved
    size = doc_table.cell(0, 0).paragraphs[0].runs[0].font.size
    assert size == Pt(14)


def test_fidelity_per_cell_border_color():
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    from reportlab.lib import colors

    tmp = tempfile.mkdtemp(prefix="pdf2word_test_")
    pdf_path = os.path.join(tmp, "pc.pdf")
    docx_path = os.path.join(tmp, "pc.docx")
    doc = SimpleDocTemplate(pdf_path, pagesize=A4)
    tbl = Table([["A", "B"], ["X", "Y"]], colWidths=[90, 90])
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("LINEABOVE", (0, 0), (-1, 0), 3, colors.Color(1, 0, 0)),  # red top
        ("LINEBEFORE", (0, 0), (0, -1), 3, colors.Color(0, 0, 1)),  # blue left
    ]))
    doc.build([tbl])

    pages = extract_document(pdf_path)
    table = next(b for b in pages[0].blocks if isinstance(b, TableBlock))
    # top edge of row 0 and left edge of col 0 should carry their colours
    assert table.cells[0][0].borders["top"][1] == "FF0000"
    assert table.cells[0][0].borders["left"][1] == "0000FF"

    write_document(pages, docx_path)
    from docx import Document
    from docx.oxml.ns import qn
    doc = Document(docx_path)
    doc_table = doc.tables[0]
    tc_b = doc_table.cell(0, 0)._tc.tcPr.find(qn("w:tcBorders"))
    assert tc_b.find(qn("w:top")).get(qn("w:color")) == "FF0000"
    assert tc_b.find(qn("w:left")).get(qn("w:color")) == "0000FF"


def test_fidelity_alignment_variants():
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    from reportlab.lib import colors

    tmp = tempfile.mkdtemp(prefix="pdf2word_test_")
    pdf_path = os.path.join(tmp, "align.pdf")
    docx_path = os.path.join(tmp, "align.docx")
    # one row, three columns: left / centre / right; plus a full-width
    # left-aligned long text that must NOT be detected as centred.
    doc = SimpleDocTemplate(pdf_path, pagesize=A4)
    tbl = Table([["Left text", "Centre text", "Right text"],
                 ["Left aligned sentence here", "X", "Y"]],
                colWidths=[140, 140, 140])
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("ALIGN", (2, 0), (2, 0), "RIGHT"),
        ("ALIGN", (0, 1), (0, 1), "LEFT"),
    ]))
    doc.build([tbl])

    pages = extract_document(pdf_path)
    table = next(b for b in pages[0].blocks if isinstance(b, TableBlock))
    # row 0: left / centre / right
    assert table.cells[0][0].align == "left"
    assert table.cells[0][1].align == "center"
    assert table.cells[0][2].align == "right"
    # row 1: left-aligned text stays 'left' (not misread as centred)
    assert table.cells[1][0].align == "left"

    write_document(pages, docx_path)
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    doc = Document(docx_path)
    doc_table = doc.tables[0]
    assert doc_table.cell(0, 0).paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.LEFT
    assert doc_table.cell(0, 1).paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert doc_table.cell(0, 2).paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.RIGHT
    assert doc_table.cell(1, 0).paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.LEFT


def test_fidelity_soft_newline_normalized():
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors

    tmp = tempfile.mkdtemp(prefix="pdf2word_test_")
    pdf_path = os.path.join(tmp, "br.pdf")
    docx_path = os.path.join(tmp, "br.docx")
    doc = SimpleDocTemplate(pdf_path, pagesize=A4)
    st = getSampleStyleSheet()["Normal"]
    st.fontSize = 10
    st.leading = 12
    tbl = Table([[Paragraph("第一行<br/>第二行", st), "B"], ["C", "D"]],
                colWidths=[120, 80])
    tbl.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
    doc.build([tbl])

    pages = extract_document(pdf_path)
    table = next(b for b in pages[0].blocks if isinstance(b, TableBlock))
    # PDF soft word-wrap \n are replaced with spaces so Word can reflow naturally
    assert " " in table.cells[0][0].text

    write_document(pages, docx_path)
    from docx import Document
    doc = Document(docx_path)
    doc_table = doc.tables[0]
    # the cell should not contain hard line breaks
    cell_text = doc_table.cell(0, 0).text
    assert "\n" not in cell_text


def test_fidelity_text_strategy_fallback_border():
    # When no drawn lines exist (e.g. text-strategy tables), the writer must
    # still emit a uniform grid so the table is visible.
    from converter.pdf_reader import Cell, TableBlock
    from docx import Document
    from docx.oxml.ns import qn

    tmp = tempfile.mkdtemp(prefix="pdf2word_test_")
    docx_path = os.path.join(tmp, "fallback.docx")
    cells = [[Cell(text="A"), Cell(text="B")], [Cell(text="C"), Cell(text="D")]]
    owner = [[(r, c) for c in range(2)] for r in range(2)]
    table = TableBlock(rows=2, cols=2, cells=cells, owner=owner,
                       col_widths=[90, 90], row_heights=[20, 20],
                       border_outer=1.0, border_inner=1.0)
    write_document([__import__("converter.pdf_reader", fromlist=["PageContent"]).PageContent(blocks=[table])],
                  docx_path)
    doc = Document(docx_path)
    doc_table = doc.tables[0]
    borders = doc_table._tbl.tblPr.find(qn("w:tblBorders"))
    assert borders is not None
    assert int(borders.find(qn("w:top")).get(qn("w:sz"))) == 8  # 1pt * 8


def _make_dims_pdf(path: str) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    from reportlab.lib import colors

    doc = SimpleDocTemplate(path, pagesize=A4)
    # first column is wider; second row is taller
    tbl = Table([["A", "B"], ["X", "Y"]], colWidths=[160, 80], rowHeights=[20, 50])
    tbl.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black),
                             ("ALIGN", (1, 0), (1, -1), "CENTER"),
                             ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    doc.build([tbl])


def test_fidelity_dimensions_and_alignment():
    tmp = tempfile.mkdtemp(prefix="pdf2word_test_")
    pdf_path = os.path.join(tmp, "dims.pdf")
    docx_path = os.path.join(tmp, "dims.docx")
    _make_dims_pdf(pdf_path)

    pages = extract_document(pdf_path)
    table = next(b for b in pages[0].blocks if isinstance(b, TableBlock))
    # column widths reflect the 160/80 pt source layout
    assert abs(table.col_widths[0] - 160) < 2
    assert abs(table.col_widths[1] - 80) < 2
    assert abs(table.row_heights[1] - 50) < 2

    write_document(pages, docx_path)
    from docx import Document
    doc = Document(docx_path)
    doc_table = doc.tables[0]

    # fixed layout + column widths applied
    from docx.oxml.ns import qn
    layout = doc_table._tbl.tblPr.find(qn("w:tblLayout"))
    assert layout is not None and layout.get(qn("w:type")) == "fixed"
    from docx.shared import Pt
    assert abs(doc_table.columns[0].width.pt - 160) < 2
    assert abs(doc_table.rows[1].height.pt - 50) < 2

    # alignment: column 1 is centred, vertical middle
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_ALIGN_VERTICAL
    p = doc_table.cell(0, 1).paragraphs[0]
    assert p.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert doc_table.cell(0, 0).vertical_alignment == WD_ALIGN_VERTICAL.CENTER


def _make_bg_pdf(path: str) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    from reportlab.lib import colors

    doc = SimpleDocTemplate(path, pagesize=A4)
    tbl = Table([["A", "B"], ["X", "Y"]], colWidths=[90, 90])
    # yellow-ish background on the top-left cell only
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("BACKGROUND", (0, 0), (0, 0), colors.Color(0.9, 0.8, 0.2)),
    ]))
    doc.build([tbl])


def test_fidelity_cell_background():
    tmp = tempfile.mkdtemp(prefix="pdf2word_test_")
    pdf_path = os.path.join(tmp, "bg.pdf")
    docx_path = os.path.join(tmp, "bg.docx")
    _make_bg_pdf(pdf_path)

    pages = extract_document(pdf_path)
    table = next(b for b in pages[0].blocks if isinstance(b, TableBlock))
    assert table.cells[0][0].bg_color == "E6CC33"  # (0.9,0.8,0.2) -> E6CC33

    write_document(pages, docx_path)
    from docx import Document
    from docx.oxml.ns import qn
    doc = Document(docx_path)
    doc_table = doc.tables[0]
    shd = doc_table.cell(0, 0)._tc.tcPr.find(qn("w:shd"))
    assert shd is not None and shd.get(qn("w:fill")) == "E6CC33"
    # other cells should not be shaded
    assert doc_table.cell(0, 1)._tc.tcPr.find(qn("w:shd")) is None


def test_fidelity_cjk_spacing_and_title_center():
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

    tmp = tempfile.mkdtemp(prefix="pdf2word_test_")
    pdf_path = os.path.join(tmp, "cjk.pdf")
    docx_path = os.path.join(tmp, "cjk.docx")

    from reportlab.lib.enums import TA_CENTER
    st = getSampleStyleSheet()["Normal"]
    st.fontName = "STSong-Light"
    st.fontSize = 12
    st.alignment = TA_CENTER
    title = Paragraph("\u6708\u5ea6\u9500\u552e\u60c5\u51b5\u62a5\u544a", st)
    tbl = Table([["\u59d3\u540d", "\u6210\u7ee9"],
                 ["\u5f20\u4e09", "\u4e5d\u5341\u4e94"]], colWidths=[90, 90])
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    doc = SimpleDocTemplate(pdf_path, pagesize=A4)
    doc.build([title, tbl])

    pages = extract_document(pdf_path)

    # title: CJK characters must not be separated by spaces
    title_block = next(b for b in pages[0].blocks
                       if isinstance(b, TextBlock) and "\u6708\u5ea6" in b.text)
    assert "\u5ea6 " not in title_block.text, f"unexpected space in: {title_block.text!r}"
    assert title_block.align == "center"

    # table cells: CJK characters must not be separated by spaces
    table = next(b for b in pages[0].blocks if isinstance(b, TableBlock))
    assert " " not in table.cells[0][0].text, f"unexpected space in cell: {table.cells[0][0].text!r}"
    assert " " not in table.cells[1][0].text, f"unexpected space in cell: {table.cells[1][0].text!r}"
    assert table.cells[0][0].align == "center"

    write_document(pages, docx_path)
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    doc = Document(docx_path)
    # title paragraph should be centred and have no spaces between CJK chars
    title_para = doc.paragraphs[0]
    assert title_para.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert " " not in title_para.text, f"unexpected space in docx title: {title_para.text!r}"


# ----- page range / pagination / stats --------------------------------------

def test_parse_page_range():
    from converter import parse_page_range

    assert parse_page_range(None, 5) == [0, 1, 2, 3, 4]
    assert parse_page_range("  ", 3) == [0, 1, 2]
    assert parse_page_range("1,3", 5) == [0, 2]
    assert parse_page_range("2-4", 5) == [1, 2, 3]
    assert parse_page_range("1-2,5", 5) == [0, 1, 4]
    # duplicates are collapsed, order preserved
    assert parse_page_range("3,1,3", 5) == [2, 0]

    with pytest.raises(ValueError):
        parse_page_range("0", 5)
    with pytest.raises(ValueError):
        parse_page_range("6", 5)
    with pytest.raises(ValueError):
        parse_page_range("3-1", 5)
    with pytest.raises(ValueError):
        parse_page_range("abc", 5)
    with pytest.raises(ValueError):
        parse_page_range("1-", 5)


def _make_multipage_pdf(path: str) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(path, pagesize=A4)
    for i in range(1, 4):
        c.drawString(100, 750, f"PageMarker{i}")
        c.showPage()
    c.save()


def test_extract_page_range_and_stats():
    from converter import count_blocks

    tmp = tempfile.mkdtemp(prefix="pdf2word_test_")
    pdf_path = os.path.join(tmp, "multi.pdf")
    _make_multipage_pdf(pdf_path)

    pages = extract_document(pdf_path, page_range="1,3")
    assert len(pages) == 2
    texts = [
        b.text for p in pages for b in p.blocks if isinstance(b, TextBlock)
    ]
    joined = " ".join(texts)
    assert "PageMarker1" in joined
    assert "PageMarker3" in joined
    assert "PageMarker2" not in joined

    stats = count_blocks(pages)
    assert stats["pages"] == 2
    assert stats["text_blocks"] >= 2


def test_write_document_page_breaks():
    from converter.pdf_reader import PageContent, TextBlock
    from docx import Document
    from docx.oxml.ns import qn

    tmp = tempfile.mkdtemp(prefix="pdf2word_test_")
    docx_path = os.path.join(tmp, "pages.docx")
    pages = [
        PageContent(blocks=[TextBlock(text="First page", top=0)]),
        PageContent(blocks=[TextBlock(text="Second page", top=0)]),
    ]
    write_document(pages, docx_path, page_breaks=True)
    doc = Document(docx_path)
    # at least one paragraph should contain a page break run
    has_break = False
    for p in doc.paragraphs:
        for run in p.runs:
            brs = run._element.findall(qn("w:br"))
            if any(br.get(qn("w:type")) == "page" for br in brs):
                has_break = True
    assert has_break, "expected a page break between PDF pages"

    # page_breaks=False should not insert one
    docx2 = os.path.join(tmp, "nobreak.docx")
    write_document(pages, docx2, page_breaks=False)
    doc2 = Document(docx2)
    for p in doc2.paragraphs:
        for run in p.runs:
            brs = run._element.findall(qn("w:br"))
            assert not any(br.get(qn("w:type")) == "page" for br in brs)
