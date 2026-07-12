from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import zipfile
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from converter import count_blocks, extract_document, write_document

router = APIRouter(prefix="/tools/pdf2word", tags=["pdf2word"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB per file
MAX_BATCH_FILES = 20
_CHUNK_SIZE = 1024 * 1024  # 1 MB
_DOCX_MEDIA = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_SAFE_NAME_RE = re.compile(r"[^\w\u4e00-\u9fff.\-]+", re.UNICODE)


@router.get("", response_class=HTMLResponse)
async def tool_page(request: Request):
    return templates.TemplateResponse(request, "tools/pdf2word.html")


def _safe_stem(filename: str) -> str:
    stem = os.path.splitext(os.path.basename(filename or "output"))[0]
    stem = _SAFE_NAME_RE.sub("_", stem).strip("._") or "output"
    return stem[:80]


async def _save_upload(file: UploadFile, dest: str) -> None:
    """Stream the upload to disk, enforcing the size limit without loading all bytes."""
    total = 0
    with open(dest, "wb") as out:
        while True:
            chunk = await file.read(_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413, detail="File too large (max 50 MB)"
                )
            out.write(chunk)
    if total == 0:
        raise HTTPException(status_code=400, detail="Empty file")


def _convert_one(
    pdf_path: str,
    docx_path: str,
    page_range: Optional[str],
    page_breaks: bool,
) -> dict:
    pages = extract_document(pdf_path, page_range=page_range)
    if not pages:
        raise ValueError("No content extracted from PDF")
    write_document(pages, docx_path, page_breaks=page_breaks)
    return count_blocks(pages)


def _stats_headers(stats: dict) -> dict:
    return {
        "X-Pages": str(stats.get("pages", 0)),
        "X-Tables": str(stats.get("tables", 0)),
        "X-Text-Blocks": str(stats.get("text_blocks", 0)),
    }


@router.post("/convert")
async def convert(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    page_range: Optional[str] = Form(None),
    page_breaks: bool = Form(True),
):
    """Convert a single PDF to Word.

    Optional form fields:
    - ``page_range``: 1-based range like ``1-3,5`` (default: all pages)
    - ``page_breaks``: insert Word page breaks between PDF pages (default: true)
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 50 MB)")

    tmp_dir = tempfile.mkdtemp(prefix="pdf2word_")
    pdf_path = os.path.join(tmp_dir, "input.pdf")
    docx_path = os.path.join(tmp_dir, "output.docx")
    range_spec = (page_range or "").strip() or None

    try:
        await _save_upload(file, pdf_path)
        stats = await asyncio.to_thread(
            _convert_one, pdf_path, docx_path, range_spec, page_breaks
        )
    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except ValueError as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(
            status_code=500, detail=f"Conversion failed: {exc}"
        ) from exc

    background_tasks.add_task(shutil.rmtree, tmp_dir, ignore_errors=True)
    out_name = _safe_stem(file.filename) + ".docx"
    return FileResponse(
        docx_path,
        media_type=_DOCX_MEDIA,
        filename=out_name,
        headers=_stats_headers(stats),
    )


@router.post("/convert-batch")
async def convert_batch(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    page_range: Optional[str] = Form(None),
    page_breaks: bool = Form(True),
):
    """Convert multiple PDFs; returns a ZIP of .docx files."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files (max {MAX_BATCH_FILES})",
        )

    range_spec = (page_range or "").strip() or None
    tmp_dir = tempfile.mkdtemp(prefix="pdf2word_batch_")
    zip_path = os.path.join(tmp_dir, "output.zip")
    used_names: set = set()
    total_pages = total_tables = total_texts = 0
    converted = 0

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, file in enumerate(files):
                if not file.filename or not file.filename.lower().endswith(".pdf"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"File {idx + 1}: only PDF files are supported",
                    )
                if file.size is not None and file.size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"{file.filename}: file too large (max 50 MB)",
                    )

                pdf_path = os.path.join(tmp_dir, f"in_{idx}.pdf")
                docx_path = os.path.join(tmp_dir, f"out_{idx}.docx")
                await _save_upload(file, pdf_path)

                try:
                    stats = await asyncio.to_thread(
                        _convert_one, pdf_path, docx_path, range_spec, page_breaks
                    )
                except ValueError as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{file.filename}: {exc}",
                    ) from exc

                stem = _safe_stem(file.filename)
                out_name = f"{stem}.docx"
                if out_name in used_names:
                    out_name = f"{stem}_{idx + 1}.docx"
                used_names.add(out_name)
                zf.write(docx_path, out_name)

                total_pages += stats["pages"]
                total_tables += stats["tables"]
                total_texts += stats["text_blocks"]
                converted += 1
                # free per-file intermediates early
                for p in (pdf_path, docx_path):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(
            status_code=500, detail=f"Batch conversion failed: {exc}"
        ) from exc

    background_tasks.add_task(shutil.rmtree, tmp_dir, ignore_errors=True)
    headers = {
        "X-Pages": str(total_pages),
        "X-Tables": str(total_tables),
        "X-Text-Blocks": str(total_texts),
        "X-Files": str(converted),
    }
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename="pdf2word_batch.zip",
        headers=headers,
    )
