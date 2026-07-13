from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from storage import (
    RETENTION_DAYS,
    cleanup_expired,
    ensure_file_dir,
    list_records,
    resolve_stored,
)
from admin import admin_router
from admin.auth import is_admin
from tools import (
    TOOL_REGISTRY,
    TOOL_ROUTERS,
    get_category,
    nav_categories,
    tools_by_category,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def _page_ctx(
    *,
    active_nav: str = "home",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Common template context for pages that share the top menu."""
    ctx: Dict[str, Any] = {
        "nav_items": nav_categories(),
        "active_nav": active_nav,
        "tool_count": len(TOOL_REGISTRY),
    }
    if extra:
        ctx.update(extra)
    return ctx


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_file_dir()
    try:
        cleanup_expired()
    except Exception:
        pass
    yield


app = FastAPI(title="工具集", version="0.7.0", lifespan=lifespan)

# Static assets (shared CSS, etc.)
static_dir = os.path.join(BASE_DIR, "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Register all tool routers
for router in TOOL_ROUTERS:
    app.include_router(router)

# Admin console (password-protected)
app.include_router(admin_router)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        _page_ctx(active_nav="home"),
    )


@app.get("/c/{category_id}", response_class=HTMLResponse)
async def category_page(request: Request, category_id: str):
    """Dedicated page for one menu column (文档处理 / 编码工具 / …)."""
    cat = get_category(category_id)
    if cat is None:
        raise HTTPException(status_code=404, detail="栏目不存在")
    return templates.TemplateResponse(
        request,
        "category.html",
        _page_ctx(
            active_nav=category_id,
            extra={"category": cat},
        ),
    )


# Friendly aliases (optional bookmarks)
@app.get("/documents", response_class=HTMLResponse)
async def documents_alias():
    return RedirectResponse(url="/c/document", status_code=307)


@app.get("/coding", response_class=HTMLResponse)
async def coding_alias():
    return RedirectResponse(url="/c/coding", status_code=307)


@app.get("/api/tools")
async def api_tools():
    """Machine-readable tool catalog (for future clients)."""
    return JSONResponse(
        {
            "version": app.version,
            "categories": tools_by_category(),
            "nav": nav_categories(),
            "tools": TOOL_REGISTRY,
        }
    )


@app.get("/health")
async def health():
    from word2pdf import engine_info
    from converter import ocr_info

    w2p = engine_info()
    ocr = ocr_info()
    cats = tools_by_category()
    return JSONResponse(
        {
            "status": "ok",
            "version": app.version,
            "tools": len(TOOL_REGISTRY),
            "categories": [
                {
                    "id": c["id"],
                    "name": c["name"],
                    "count": len(c["tools"]),
                    "route": c.get("route"),
                }
                for c in cats
            ],
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
async def api_uploads(request: Request, limit: int = Query(50, ge=1, le=200)):
    """JSON list of recent uploads (admin only; last ``RETENTION_DAYS`` days)."""
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return JSONResponse(
        {
            "retention_days": RETENTION_DAYS,
            "items": list_records(limit=limit),
        }
    )


@app.get("/api/uploads/{record_id}/download")
async def download_upload(request: Request, record_id: str):
    """Download the archived input file for a history record (admin only)."""
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
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
