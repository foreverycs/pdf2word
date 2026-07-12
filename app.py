from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from storage import (
    RETENTION_DAYS,
    cleanup_expired,
    ensure_file_dir,
    list_records,
    resolve_stored,
)
from tools import TOOL_REGISTRY, pdf2word_router, word2pdf_router

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_file_dir()
    try:
        cleanup_expired()
    except Exception:
        pass
    yield


app = FastAPI(title="工具箱", version="0.5.0", lifespan=lifespan)

# Register tool routers
app.include_router(pdf2word_router)
app.include_router(word2pdf_router)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"tools": TOOL_REGISTRY},
    )


@app.get("/health")
async def health():
    from word2pdf import engine_info
    from converter import ocr_info

    w2p = engine_info()
    ocr = ocr_info()
    return JSONResponse(
        {
            "status": "ok",
            "version": app.version,
            "tools": len(TOOL_REGISTRY),
            "word2pdf": {
                "ready": w2p.get("ready", False),
                "engines": w2p.get("engines") or [],
                "preferred": w2p.get("preferred"),
            },
            "ocr": {
                "available": ocr.get("available", False),
                "lang": ocr.get("lang"),
            },
            "upload_history": {
                "retention_days": RETENTION_DAYS,
                "count": len(list_records(limit=200)),
            },
        }
    )


@app.get("/api/uploads")
async def api_uploads(limit: int = Query(50, ge=1, le=200)):
    """JSON list of recent uploads (last ``RETENTION_DAYS`` days)."""
    return JSONResponse(
        {
            "retention_days": RETENTION_DAYS,
            "items": list_records(limit=limit),
        }
    )


@app.get("/api/uploads/{record_id}/download")
async def download_upload(record_id: str):
    """Download the archived input file for a history record."""
    items = list_records(limit=200)
    rec = next((r for r in items if r.get("id") == record_id), None)
    if not rec:
        raise HTTPException(status_code=404, detail="Record not found")
    rel = rec.get("input_rel")
    if not rel:
        raise HTTPException(status_code=404, detail="No input file stored")
    path = resolve_stored(str(rel))
    if path is None:
        raise HTTPException(status_code=404, detail="File missing on disk")
    name = rec.get("original_name") or path.name
    return FileResponse(path, filename=str(name))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
