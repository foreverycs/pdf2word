"""Tests for the PDF merge (invoice merge) tool."""
from __future__ import annotations

import importlib
import io
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject


def _make_pdf(path: str, pages: int = 2) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as rl_canvas

    c = rl_canvas.Canvas(path, pagesize=A4)
    for i in range(1, pages + 1):
        c.setFont("Helvetica", 24)
        c.drawString(100, 700, f"Page {i}")
        c.showPage()
    c.save()


def _make_single_page_pdf(path: str, text: str = "invoice") -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as rl_canvas

    c = rl_canvas.Canvas(path, pagesize=A4)
    c.setFont("Helvetica", 24)
    c.drawString(100, 700, text)
    c.save()


@pytest.fixture()
def merge_client(tmp_path, monkeypatch):
    d = tmp_path / "file"
    d.mkdir()
    monkeypatch.setenv("UPLOAD_FILE_DIR", str(d))
    monkeypatch.setenv("UPLOAD_RETENTION_DAYS", "5")

    import storage.history as h
    import storage as s
    import admin.auth as auth
    import admin.routes as routes
    import app as app_mod

    importlib.reload(h)
    importlib.reload(s)
    importlib.reload(auth)
    importlib.reload(routes)
    importlib.reload(app_mod)

    client = TestClient(app_mod.app)
    yield client, tmp_path


def test_merge_single_upper_half_only(tmp_path):
    """One invoice → one A4 page, content on upper half only."""
    from tools.pdf_merge import _merge_single

    src = tmp_path / "1page.pdf"
    _make_single_page_pdf(str(src), "ONLY-TOP")
    out = tmp_path / "merged.pdf"

    stats = _merge_single(str(src), str(out), add_divider=True)
    assert stats["input_pages"] == 1
    assert stats["output_pages"] == 1
    assert out.exists()

    reader = PdfReader(str(out))
    assert len(reader.pages) == 1
    page = reader.pages[0]
    assert float(page.mediabox.width) == pytest.approx(595.28, abs=1)
    assert float(page.mediabox.height) == pytest.approx(841.89, abs=1)
    text = page.extract_text() or ""
    assert "ONLY-TOP" in text
    # Mid-page divider is drawn even for a single invoice (content stream present).
    contents = page.get_contents()
    assert contents is not None
    data = contents.get_data() if hasattr(contents, "get_data") else b""
    assert len(data) > 0


def test_merge_single_ignores_extra_pages(tmp_path):
    """Multi-page PDF uses first page only (one invoice per file)."""
    from tools.pdf_merge import _merge_single

    src = tmp_path / "3pages.pdf"
    _make_pdf(str(src), pages=3)
    out = tmp_path / "merged.pdf"

    stats = _merge_single(str(src), str(out), add_divider=False)
    assert stats["input_pages"] == 1
    assert stats["output_pages"] == 1

    reader = PdfReader(str(out))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text() or ""
    assert "Page 1" in text
    assert "Page 2" not in text
    assert "Page 3" not in text


def test_merge_two_files(tmp_path):
    from tools.pdf_merge import _merge_two_files

    src1 = tmp_path / "a.pdf"
    src2 = tmp_path / "b.pdf"
    _make_single_page_pdf(str(src1), "Invoice A")
    _make_single_page_pdf(str(src2), "Invoice B")
    out = tmp_path / "merged.pdf"

    stats = _merge_two_files(str(src1), str(src2), str(out), add_divider=True)
    assert stats["input_pages"] == 2
    assert stats["output_pages"] == 1

    reader = PdfReader(str(out))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text() or ""
    assert "Invoice A" in text
    assert "Invoice B" in text


def test_merge_offset_cropbox_not_blank(tmp_path):
    """Non-zero cropbox origin must still place visible content (not blank)."""
    from tools.pdf_merge import merge_invoices

    src = tmp_path / "offset.pdf"
    _make_single_page_pdf(str(src), "OFFSET-OK")

    # Rewrite with non-zero mediabox origin (common for cropped scans)
    r = PdfReader(str(src))
    w = PdfWriter()
    page = r.pages[0]
    page.mediabox = RectangleObject([80, 120, 595.28, 841.89])
    page.cropbox = RectangleObject([80, 120, 595.28, 841.89])
    w.add_page(page)
    fixed = tmp_path / "offset_fixed.pdf"
    with open(fixed, "wb") as f:
        w.write(f)

    out = tmp_path / "merged.pdf"
    stats = merge_invoices(str(fixed), str(out), pdf2_path=None, add_divider=False)
    assert stats["output_pages"] == 1
    text = PdfReader(str(out)).pages[0].extract_text() or ""
    assert "OFFSET-OK" in text


def test_api_merge_single(merge_client):
    client, tmp_path = merge_client
    src = tmp_path / "test.pdf"
    _make_single_page_pdf(str(src), "API-ONE")

    with open(str(src), "rb") as f:
        resp = client.post(
            "/tools/pdf-merge/convert",
            files={"file": ("test.pdf", f, "application/pdf")},
            data={"divider": "true"},
        )

    assert resp.status_code == 200
    assert resp.headers.get("content-type") == "application/pdf"
    assert resp.headers.get("X-Input-Pages") == "1"
    assert resp.headers.get("X-Output-Pages") == "1"

    reader = PdfReader(io.BytesIO(resp.content))
    assert len(reader.pages) == 1
    assert "API-ONE" in (reader.pages[0].extract_text() or "")


def test_api_merge_two_files(merge_client):
    client, tmp_path = merge_client
    src1 = tmp_path / "a.pdf"
    src2 = tmp_path / "b.pdf"
    _make_single_page_pdf(str(src1), "A")
    _make_single_page_pdf(str(src2), "B")

    with open(str(src1), "rb") as f1, open(str(src2), "rb") as f2:
        resp = client.post(
            "/tools/pdf-merge/convert",
            files=[
                ("file", ("a.pdf", f1, "application/pdf")),
                ("file2", ("b.pdf", f2, "application/pdf")),
            ],
            data={"divider": "true"},
        )

    assert resp.status_code == 200
    assert resp.headers.get("X-Input-Pages") == "2"
    assert resp.headers.get("X-Output-Pages") == "1"
    text = PdfReader(io.BytesIO(resp.content)).pages[0].extract_text() or ""
    assert "A" in text
    assert "B" in text


def test_api_rejects_non_pdf(merge_client):
    client, tmp_path = merge_client
    txt = tmp_path / "bad.txt"
    txt.write_text("not a pdf")

    with open(str(txt), "rb") as f:
        resp = client.post(
            "/tools/pdf-merge/convert",
            files={"file": ("bad.txt", f, "text/plain")},
        )

    assert resp.status_code == 400


def test_page_registered_under_office():
    from tools import TOOL_REGISTRY

    tool = next(t for t in TOOL_REGISTRY if t["slug"] == "pdf-merge")
    assert tool["category"] == "office"
    assert tool["route"] == "/tools/pdf-merge"


def test_route_accessible(merge_client):
    client, _ = merge_client
    resp = client.get("/tools/pdf-merge")
    assert resp.status_code == 200
    assert "发票合并" in resp.text
    assert "/c/office" in resp.text
    assert "文档处理" not in resp.text or "办公工具" in resp.text
    # multi-page pairing mode removed
    assert "单文件配对" not in resp.text
    assert "双文件合并" not in resp.text
    # in-page preview + print (no auto-download flow)
    assert "previewFrame" in resp.text
    assert "打印" in resp.text
    assert "btnPrint" in resp.text


def test_office_category_lists_invoice_merge(merge_client):
    client, _ = merge_client
    office = client.get("/c/office")
    assert office.status_code == 200
    assert "发票合并" in office.text
    assert "/tools/pdf-merge" in office.text
