from __future__ import annotations

import asyncio
import os
import zipfile
from typing import List, Optional, Tuple
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.requests import Request

from converter import content_warnings, count_blocks, extract_document, ocr_info, write_document
from core.concurrency import run_heavy, should_use_process_pool
from core.errors import ConversionError, PDFParseError
from core.jobs import create_job, job_public_dict, schedule_job
from tools.common import (
    DOCX_MEDIA,
    ZIP_MEDIA,
    check_upload_size_header,
    max_batch_files,
    safe_stem,
    save_upload,
    templates,
)
from tools.pipeline import TempWorkspace, archive_input, map_conversion_error

router = APIRouter(prefix="/tools/pdf2word", tags=["pdf2word"])


@router.get("", response_class=HTMLResponse)
async def tool_page(request: Request):
    return templates.TemplateResponse(
        request,
        "tools/pdf2word.html",
        {"ocr": ocr_info()},
    )


@router.get("/ocr-status")
async def ocr_status():
    """Return whether optional OCR (Tesseract) is available."""
    return JSONResponse(ocr_info())


def _convert_one(
    pdf_path: str,
    docx_path: str,
    page_range: Optional[str],
    page_breaks: bool,
    ocr: bool = False,
) -> dict:
    pages = extract_document(pdf_path, page_range=page_range, ocr=ocr)
    if not pages:
        raise PDFParseError("No content extracted from PDF")
    write_document(pages, docx_path, page_breaks=page_breaks)
    stats = count_blocks(pages)
    stats["warnings"] = content_warnings(pages)
    if "empty" in stats["warnings"]:
        raise ConversionError(
            "No extractable content (empty or unsupported PDF). "
            "Scanned PDFs without a renderable image cannot be converted. "
            "Enable OCR if Tesseract is installed."
        )
    return stats


def _stats_headers(stats: dict) -> dict:
    headers = {
        "X-Pages": str(stats.get("pages", 0)),
        "X-Tables": str(stats.get("tables", 0)),
        "X-Text-Blocks": str(stats.get("text_blocks", 0)),
        "X-Images": str(stats.get("images", 0)),
        "X-Lines": str(stats.get("lines", 0)),
    }
    warns = stats.get("warnings") or []
    if warns:
        headers["X-Warnings"] = ",".join(warns)
        if "ocr_applied" in warns:
            headers["X-Warning-Message"] = quote(
                "OCR applied on scanned page(s); text is editable but may contain errors"
            )
        elif "image_only" in warns:
            headers["X-Warning-Message"] = quote(
                "Detected image-only/scanned pages; embedded as images "
                "(enable OCR for editable text)"
            )
    return headers


def _parse_ocr_flag(ocr: Optional[str]) -> bool:
    if ocr is None:
        return False
    return str(ocr).strip().lower() in ("1", "true", "yes", "on")


def _require_ocr_if_requested(use_ocr: bool) -> None:
    if not use_ocr:
        return
    from converter import ocr_available

    if not ocr_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "OCR requested but Tesseract is not available. "
                "Install Tesseract and pytesseract, or set TESSERACT_CMD."
            ),
        )


def _job_urls(job_id: str) -> dict:
    return {
        "poll_url": f"/api/jobs/{job_id}",
        "download_url": f"/api/jobs/{job_id}/download",
    }


async def _run_single_async_job(
    *,
    job_id: str,
    pdf_path: str,
    docx_path: str,
    range_spec: Optional[str],
    page_breaks: bool,
    use_ocr: bool,
    original_name: str,
    file_size: int,
) -> dict:
    stats = await run_heavy(
        _convert_one,
        pdf_path,
        docx_path,
        range_spec,
        page_breaks,
        use_ocr,
        file_size=file_size,
    )
    await archive_input(
        tool="pdf2word",
        original_name=original_name,
        input_path=pdf_path,
        extra={
            "pages": stats.get("pages"),
            "tables": stats.get("tables"),
            "images": stats.get("images"),
            "async": True,
        },
    )
    # Drop input after archive to free space; keep output for download.
    try:
        os.remove(pdf_path)
    except OSError:
        pass
    return {
        "result": {
            "pages": stats.get("pages"),
            "tables": stats.get("tables"),
            "text_blocks": stats.get("text_blocks"),
            "images": stats.get("images"),
            "lines": stats.get("lines"),
            "warnings": stats.get("warnings") or [],
        },
        "response_headers": _stats_headers(stats),
        "progress": 1.0,
        "message": "done",
    }


async def _run_batch_async_job(
    *,
    job_id: str,
    items: List[Tuple[int, str, str, str]],
    zip_path: str,
    range_spec: Optional[str],
    page_breaks: bool,
    use_ocr: bool,
) -> dict:
    async def _one(
        idx: int, name: str, pdf_path: str, docx_path: str
    ) -> Tuple[int, str, str, str, dict]:
        file_size = os.path.getsize(pdf_path)
        stats = await run_heavy(
            _convert_one,
            pdf_path,
            docx_path,
            range_spec,
            page_breaks,
            use_ocr,
            file_size=file_size,
        )
        return idx, name, pdf_path, docx_path, stats

    results = await asyncio.gather(
        *[_one(idx, name, pdf, docx) for idx, name, pdf, docx in items]
    )
    results = sorted(results, key=lambda r: r[0])

    used_names: set = set()
    total_pages = total_tables = total_texts = total_images = 0
    converted = 0
    all_warns: set = set()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx, name, pdf_path, docx_path, stats in results:
            stem = safe_stem(name)
            out_name = f"{stem}.docx"
            if out_name in used_names:
                out_name = f"{stem}_{idx + 1}.docx"
            used_names.add(out_name)
            zf.write(docx_path, out_name)

            await archive_input(
                tool="pdf2word",
                original_name=name,
                input_path=pdf_path,
                extra={
                    "pages": stats.get("pages"),
                    "tables": stats.get("tables"),
                    "images": stats.get("images"),
                    "batch": True,
                    "async": True,
                },
            )

            total_pages += stats["pages"]
            total_tables += stats["tables"]
            total_texts += stats["text_blocks"]
            total_images += stats.get("images", 0)
            all_warns.update(stats.get("warnings") or [])
            converted += 1
            for p in (pdf_path, docx_path):
                try:
                    os.remove(p)
                except OSError:
                    pass

    headers = _stats_headers({
        "pages": total_pages,
        "tables": total_tables,
        "text_blocks": total_texts,
        "images": total_images,
        "warnings": sorted(all_warns),
    })
    headers["X-Files"] = str(converted)
    return {
        "result": {
            "pages": total_pages,
            "tables": total_tables,
            "text_blocks": total_texts,
            "images": total_images,
            "warnings": sorted(all_warns),
            "files": converted,
            "batch": True,
        },
        "response_headers": headers,
        "progress": 1.0,
        "message": "done",
    }


@router.post("/convert")
async def convert(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    page_range: Optional[str] = Form(None),
    page_breaks: bool = Form(True),
    ocr: Optional[str] = Form(None),
):
    """Convert a single PDF to Word (synchronous response with file body)."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    check_upload_size_header(file)
    use_ocr = _parse_ocr_flag(ocr)
    _require_ocr_if_requested(use_ocr)

    ws = TempWorkspace("pdf2word_")
    ws.create()
    pdf_path = ws.join("input.pdf")
    docx_path = ws.join("output.docx")
    range_spec = (page_range or "").strip() or None

    try:
        await save_upload(file, pdf_path)
        file_size = os.path.getsize(pdf_path)
        stats = await run_heavy(
            _convert_one,
            pdf_path,
            docx_path,
            range_spec,
            page_breaks,
            use_ocr,
            file_size=file_size,
        )
    except Exception as exc:
        ws.cleanup_now()
        raise map_conversion_error(exc) from exc

    out_name = safe_stem(file.filename) + ".docx"
    await archive_input(
        tool="pdf2word",
        original_name=file.filename or "input.pdf",
        input_path=pdf_path,
        extra={
            "pages": stats.get("pages"),
            "tables": stats.get("tables"),
            "images": stats.get("images"),
        },
    )
    ws.schedule_cleanup(background_tasks)
    return FileResponse(
        docx_path,
        media_type=DOCX_MEDIA,
        filename=out_name,
        headers=_stats_headers(stats),
    )


@router.post("/convert-async")
async def convert_async(
    file: UploadFile = File(...),
    page_range: Optional[str] = Form(None),
    page_breaks: bool = Form(True),
    ocr: Optional[str] = Form(None),
):
    """Queue a single PDF→Word job; poll ``/api/jobs/{id}`` then download."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    check_upload_size_header(file)
    use_ocr = _parse_ocr_flag(ocr)
    _require_ocr_if_requested(use_ocr)

    ws = TempWorkspace("pdf2word_async_")
    work_dir = ws.create()
    pdf_path = ws.join("input.pdf")
    docx_path = ws.join("output.docx")
    range_spec = (page_range or "").strip() or None
    original = file.filename or "input.pdf"
    out_name = safe_stem(original) + ".docx"

    try:
        await save_upload(file, pdf_path)
        file_size = os.path.getsize(pdf_path)
    except Exception as exc:
        ws.cleanup_now()
        raise map_conversion_error(exc) from exc

    job = await create_job(
        "pdf2word",
        work_dir=work_dir,
        output_path=docx_path,
        download_name=out_name,
        media_type=DOCX_MEDIA,
        message="queued",
        progress=0.0,
    )

    async def _factory():
        return await _run_single_async_job(
            job_id=job.id,
            pdf_path=pdf_path,
            docx_path=docx_path,
            range_spec=range_spec,
            page_breaks=page_breaks,
            use_ocr=use_ocr,
            original_name=original,
            file_size=file_size,
        )

    schedule_job(job.id, _factory)
    body = job_public_dict(job)
    body.update(_job_urls(job.id))
    body["mode"] = "async"
    body["prefer_process_pool"] = should_use_process_pool(file_size) or use_ocr
    return JSONResponse(body, status_code=202)


@router.post("/convert-batch")
async def convert_batch(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    page_range: Optional[str] = Form(None),
    page_breaks: bool = Form(True),
    ocr: Optional[str] = Form(None),
):
    """Convert multiple PDFs; returns a ZIP of .docx files (synchronous)."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    batch_limit = max_batch_files()
    if len(files) > batch_limit:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files (max {batch_limit})",
        )

    use_ocr = _parse_ocr_flag(ocr)
    _require_ocr_if_requested(use_ocr)

    range_spec = (page_range or "").strip() or None
    ws = TempWorkspace("pdf2word_batch_")
    ws.create()
    zip_path = ws.join("output.zip")

    jobs: List[Tuple[int, str, str, str]] = []

    try:
        for idx, file in enumerate(files):
            if not file.filename or not file.filename.lower().endswith(".pdf"):
                raise HTTPException(
                    status_code=400,
                    detail=f"File {idx + 1}: only PDF files are supported",
                )
            check_upload_size_header(file)

            pdf_path = ws.join(f"in_{idx}.pdf")
            docx_path = ws.join(f"out_{idx}.docx")
            await save_upload(file, pdf_path)
            jobs.append((idx, file.filename or f"input_{idx}.pdf", pdf_path, docx_path))

        async def _run_job(
            idx: int, name: str, pdf_path: str, docx_path: str
        ) -> Tuple[int, str, str, str, dict]:
            try:
                file_size = os.path.getsize(pdf_path)
                stats = await run_heavy(
                    _convert_one,
                    pdf_path,
                    docx_path,
                    range_spec,
                    page_breaks,
                    use_ocr,
                    file_size=file_size,
                )
            except Exception as exc:
                raise map_conversion_error(exc, name_prefix=name) from exc
            return idx, name, pdf_path, docx_path, stats

        results = await asyncio.gather(
            *[_run_job(idx, name, pdf, docx) for idx, name, pdf, docx in jobs]
        )
        results = sorted(results, key=lambda r: r[0])

        used_names: set = set()
        total_pages = total_tables = total_texts = total_images = 0
        converted = 0
        all_warns: set = set()

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, name, pdf_path, docx_path, stats in results:
                stem = safe_stem(name)
                out_name = f"{stem}.docx"
                if out_name in used_names:
                    out_name = f"{stem}_{idx + 1}.docx"
                used_names.add(out_name)
                zf.write(docx_path, out_name)

                await archive_input(
                    tool="pdf2word",
                    original_name=name,
                    input_path=pdf_path,
                    extra={
                        "pages": stats.get("pages"),
                        "tables": stats.get("tables"),
                        "images": stats.get("images"),
                        "batch": True,
                    },
                )

                total_pages += stats["pages"]
                total_tables += stats["tables"]
                total_texts += stats["text_blocks"]
                total_images += stats.get("images", 0)
                all_warns.update(stats.get("warnings") or [])
                converted += 1
                for p in (pdf_path, docx_path):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
    except Exception as exc:
        ws.cleanup_now()
        raise map_conversion_error(
            exc, label="Batch conversion failed"
        ) from exc

    ws.schedule_cleanup(background_tasks)
    headers = _stats_headers({
        "pages": total_pages,
        "tables": total_tables,
        "text_blocks": total_texts,
        "images": total_images,
        "warnings": sorted(all_warns),
    })
    headers["X-Files"] = str(converted)
    return FileResponse(
        zip_path,
        media_type=ZIP_MEDIA,
        filename="pdf2word_batch.zip",
        headers=headers,
    )


@router.post("/convert-batch-async")
async def convert_batch_async(
    files: List[UploadFile] = File(...),
    page_range: Optional[str] = Form(None),
    page_breaks: bool = Form(True),
    ocr: Optional[str] = Form(None),
):
    """Queue a batch PDF→Word job; poll then download a ZIP."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    batch_limit = max_batch_files()
    if len(files) > batch_limit:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files (max {batch_limit})",
        )

    use_ocr = _parse_ocr_flag(ocr)
    _require_ocr_if_requested(use_ocr)
    range_spec = (page_range or "").strip() or None

    ws = TempWorkspace("pdf2word_batch_async_")
    work_dir = ws.create()
    zip_path = ws.join("output.zip")
    items: List[Tuple[int, str, str, str]] = []

    try:
        for idx, file in enumerate(files):
            if not file.filename or not file.filename.lower().endswith(".pdf"):
                raise HTTPException(
                    status_code=400,
                    detail=f"File {idx + 1}: only PDF files are supported",
                )
            check_upload_size_header(file)
            pdf_path = ws.join(f"in_{idx}.pdf")
            docx_path = ws.join(f"out_{idx}.docx")
            await save_upload(file, pdf_path)
            items.append(
                (idx, file.filename or f"input_{idx}.pdf", pdf_path, docx_path)
            )
    except Exception as exc:
        ws.cleanup_now()
        raise map_conversion_error(exc, label="Batch upload failed") from exc

    job = await create_job(
        "pdf2word",
        work_dir=work_dir,
        output_path=zip_path,
        download_name="pdf2word_batch.zip",
        media_type=ZIP_MEDIA,
        message="queued",
        progress=0.0,
    )

    async def _factory():
        return await _run_batch_async_job(
            job_id=job.id,
            items=items,
            zip_path=zip_path,
            range_spec=range_spec,
            page_breaks=page_breaks,
            use_ocr=use_ocr,
        )

    schedule_job(job.id, _factory)
    body = job_public_dict(job)
    body.update(_job_urls(job.id))
    body["mode"] = "async"
    body["files"] = len(items)
    return JSONResponse(body, status_code=202)
