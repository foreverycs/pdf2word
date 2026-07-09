from __future__ import annotations

import os
import shutil
import tempfile

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from converter import extract_document, write_document

router = APIRouter(prefix="/tools/pdf2word", tags=["pdf2word"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


@router.get("", response_class=HTMLResponse)
async def tool_page(request: Request):
    return templates.TemplateResponse(request, "tools/pdf2word.html")


@router.post("/convert")
async def convert(file: UploadFile = File(...),
                  background_tasks: BackgroundTasks = None):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 50 MB)")

    tmp_dir = tempfile.mkdtemp(prefix="pdf2word_")
    pdf_path = os.path.join(tmp_dir, "input.pdf")
    docx_path = os.path.join(tmp_dir, "output.docx")
    try:
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="File too large (max 50 MB)")
        with open(pdf_path, "wb") as f:
            f.write(data)
        pages = extract_document(pdf_path)
        write_document(pages, docx_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Conversion failed: {exc}")
    finally:
        if background_tasks is not None:
            background_tasks.add_task(shutil.rmtree, tmp_dir, ignore_errors=True)

    out_name = os.path.splitext(os.path.basename(file.filename))[0] + ".docx"
    return FileResponse(
        docx_path,
        media_type=("application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"),
        filename=out_name,
    )
