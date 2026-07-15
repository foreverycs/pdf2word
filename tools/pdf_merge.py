from __future__ import annotations

import io
import os
from typing import List, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from starlette.requests import Request

from core.concurrency import run_conversion
from core.errors import PDFParseError, ValidationError
from tools.common import (
    PDF_MEDIA,
    check_upload_size_header,
    safe_stem,
    save_upload,
    templates,
)
from tools.pipeline import TempWorkspace, archive_input, map_conversion_error

router = APIRouter(prefix="/tools/pdf-merge", tags=["pdf-merge"])

# A4 dimensions in points
A4_W = 595.28
A4_H = 841.89
HALF_H = A4_H / 2
MARGIN = 18


@router.get("", response_class=HTMLResponse)
async def tool_page(request: Request):
    return templates.TemplateResponse(request, "tools/pdf-merge.html", {})


def _make_divider() -> bytes:
    """Create a tiny PDF containing a single horizontal dashed line."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as rl_canvas

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)
    y = HALF_H
    c.setStrokeColorRGB(0.6, 0.6, 0.6)
    c.setLineWidth(0.5)
    c.setDash(6, 3)
    c.line(MARGIN, y, A4_W - MARGIN, y)
    c.save()
    return buf.getvalue()


def _scale_to_fit(src_w: float, src_h: float, dst_w: float, dst_h: float) -> float:
    """Return uniform scale that fits src into dst preserving aspect ratio."""
    if src_w <= 0 or src_h <= 0:
        raise ValidationError("Invalid page dimensions")
    return min(dst_w / src_w, dst_h / src_h)


def _load_invoice_page(path: str) -> Tuple[PdfWriter, PageObject]:
    """Load first page into an owned PdfWriter so objects stay valid after merge.

    Transfers ``/Rotate`` into content when possible, so scale/place math can use
    the visual page box (important for many e-invoice PDFs).
    """
    reader = PdfReader(path)
    if not reader.pages:
        raise PDFParseError("PDF has no pages")

    owner = PdfWriter()
    owner.add_page(reader.pages[0])
    page = owner.pages[0]

    rotate = int(page.get("/Rotate") or 0) % 360
    if rotate and hasattr(page, "transfer_rotation_to_content"):
        try:
            page.transfer_rotation_to_content()
        except Exception:
            # Leave /Rotate as-is; placement still uses mediabox/cropbox.
            pass

    return owner, page


def _page_box(page: PageObject):
    """Prefer cropbox (visible area); fall back to mediabox."""
    try:
        box = page.cropbox
        if box is not None and float(box.width) > 0 and float(box.height) > 0:
            return box
    except Exception:
        pass
    return page.mediabox


def _place_transform(page: PageObject, *, top: bool) -> Transformation:
    """Scale a source page into the top or bottom half of A4 and center it.

    Accounts for non-zero cropbox/mediabox origins so content is not shifted
    off the half-page (a common cause of “blank” merges for scanned invoices).
    """
    box = _page_box(page)
    src_w = float(box.width)
    src_h = float(box.height)
    ox = float(box.left)
    oy = float(box.bottom)
    if src_w <= 0 or src_h <= 0:
        raise ValidationError("Invalid page dimensions")

    usable_w = A4_W - 2 * MARGIN
    usable_h = HALF_H - MARGIN
    scale = _scale_to_fit(src_w, src_h, usable_w, usable_h)
    scaled_w = src_w * scale
    scaled_h = src_h * scale
    tx = MARGIN + (usable_w - scaled_w) / 2
    if top:
        ty = HALF_H + (usable_h - scaled_h) / 2
    else:
        ty = (usable_h - scaled_h) / 2

    # x' = scale * (x - ox) + tx  =>  e = tx - scale*ox
    # y' = scale * (y - oy) + ty  =>  f = ty - scale*oy
    return Transformation(
        (scale, 0, 0, scale, tx - scale * ox, ty - scale * oy)
    )


def _merge_pair(
    top_page: Optional[PageObject],
    bottom_page: Optional[PageObject],
    divider_page: Optional[PageObject],
) -> PageObject:
    """Place up to two invoice pages onto one A4 sheet (top / bottom halves).

    A lone invoice is placed only on the upper half; the lower half stays empty.
    """
    if top_page is None and bottom_page is None:
        raise ValidationError("At least one page is required")

    # Use PdfWriter blank page so the result is part of a document graph from
    # the start (more reliable resource cloning than a free-standing PageObject).
    holder = PdfWriter()
    out = holder.add_blank_page(width=A4_W, height=A4_H)

    if top_page is not None:
        out.merge_transformed_page(
            top_page, _place_transform(top_page, top=True), over=True
        )
    if bottom_page is not None:
        out.merge_transformed_page(
            bottom_page, _place_transform(bottom_page, top=False), over=True
        )
    # Divider is drawn at the A4 mid-line for both one- and two-invoice layouts.
    if divider_page is not None:
        out.merge_page(divider_page)

    # Detach for callers; holder is kept only for the duration of this call via
    # the page's internal pdf reference until written by the outer writer.
    out.pdf = holder  # type: ignore[attr-defined]
    return out


def merge_invoices(
    pdf1_path: str,
    out_path: str,
    pdf2_path: Optional[str] = None,
    add_divider: bool = True,
) -> dict:
    """Merge one or two single-invoice PDFs onto one A4 page.

    - Two files: first → upper half, second → lower half.
    - One file: invoice only on the upper half (lower half empty).
    - Optional mid-page divider for both one- and two-invoice layouts.
    Only the first page of each file is used (one invoice per file).
    """
    # Keep owner writers alive until after ``writer.write`` so page resources
    # (fonts, images, XObjects) are not garbage-collected mid-merge.
    owners: List[PdfWriter] = []

    top_owner, top = _load_invoice_page(pdf1_path)
    owners.append(top_owner)

    bottom: Optional[PageObject] = None
    if pdf2_path:
        bottom_owner, bottom = _load_invoice_page(pdf2_path)
        owners.append(bottom_owner)

    divider_page = None
    divider_owner: Optional[PdfWriter] = None
    if add_divider:
        divider_owner = PdfWriter()
        divider_owner.add_page(PdfReader(io.BytesIO(_make_divider())).pages[0])
        divider_page = divider_owner.pages[0]
        owners.append(divider_owner)

    merged = _merge_pair(top, bottom, divider_page)
    # Keep the blank-page holder alive as well
    if getattr(merged, "pdf", None) is not None:
        owners.append(merged.pdf)  # type: ignore[arg-type]

    writer = PdfWriter()
    writer.add_page(merged)
    with open(out_path, "wb") as f:
        writer.write(f)

    # Explicitly retain owners until write finishes
    del owners

    input_pages = 1 + (1 if bottom is not None else 0)
    return {"input_pages": input_pages, "output_pages": 1}


def _merge_two_files(
    pdf1_path: str, pdf2_path: str, out_path: str, add_divider: bool
) -> dict:
    return merge_invoices(
        pdf1_path, out_path, pdf2_path=pdf2_path, add_divider=add_divider
    )


def _merge_single(pdf_path: str, out_path: str, add_divider: bool = True) -> dict:
    """Place a single invoice on the upper half of one A4 page."""
    return merge_invoices(pdf_path, out_path, pdf2_path=None, add_divider=add_divider)


@router.post("/convert")
async def convert(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    file2: Optional[UploadFile] = File(None),
    divider: Optional[str] = Form(None),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    check_upload_size_header(file)

    use_divider = str(divider or "").strip().lower() in ("1", "true", "yes", "on")

    ws = TempWorkspace("pdf_merge_")
    ws.create()
    pdf1_path = ws.join("input1.pdf")
    out_path = ws.join("merged.pdf")

    try:
        await save_upload(file, pdf1_path)

        pdf2_path: Optional[str] = None
        if file2 and file2.filename:
            if not file2.filename.lower().endswith(".pdf"):
                raise HTTPException(
                    status_code=400, detail="Second file must be a PDF"
                )
            check_upload_size_header(file2, label="Second file")
            pdf2_path = ws.join("input2.pdf")
            await save_upload(file2, pdf2_path)

        stats = await run_conversion(
            merge_invoices, pdf1_path, out_path, pdf2_path, use_divider
        )
        archive_name = file.filename
    except Exception as exc:
        ws.cleanup_now()
        raise map_conversion_error(exc, label="Merge failed") from exc

    out_name = safe_stem(file.filename) + "_merged.pdf"
    await archive_input(
        tool="pdf-merge",
        original_name=archive_name or "input.pdf",
        input_path=pdf1_path,
        extra={
            "pages": stats.get("input_pages"),
            "output_pages": stats.get("output_pages"),
        },
    )
    ws.schedule_cleanup(background_tasks)
    # inline so the browser can embed / print instead of forcing a download
    return FileResponse(
        out_path,
        media_type=PDF_MEDIA,
        filename=out_name,
        content_disposition_type="inline",
        headers={
            "X-Input-Pages": str(stats.get("input_pages", 0)),
            "X-Output-Pages": str(stats.get("output_pages", 0)),
            "Cache-Control": "no-store",
        },
    )
