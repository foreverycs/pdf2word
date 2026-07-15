from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import Response

from core.errors import ToolkitError
from core.logging_setup import configure_logging, get_logger
from core.request_id import (
    REQUEST_ID_HEADER,
    get_request_id,
    new_request_id,
    reset_request_id,
    set_request_id,
)
from core.settings import get_settings, load_dotenv, validate_security_settings
from core.version import __version__
from storage import (
    ensure_file_dir,
    get_record,
    list_records,
    record_count,
    resolve_stored,
    retention_days,
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
from tools.common import templates

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load .env early so ROOT_PATH is available without forcing full security load.
load_dotenv()
configure_logging()
logger = get_logger("toolkit.app")

# Pre-compute static data that never changes at runtime
_nav_items = nav_categories()
_tool_count = len(TOOL_REGISTRY)
_categories_cache = tools_by_category()


def _import_root_path() -> str:
    """Read ROOT_PATH without full credential validation (import-time safe)."""
    from core.settings import _normalize_root_path

    return _normalize_root_path(os.environ.get("ROOT_PATH") or "")


def _page_ctx(
    *,
    active_nav: str = "home",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Common template context for pages that share the top menu."""
    ctx: Dict[str, Any] = {
        "nav_items": _nav_items,
        "active_nav": active_nav,
        "tool_count": _tool_count,
    }
    if extra:
        ctx.update(extra)
    return ctx


@asynccontextmanager
async def lifespan(app: FastAPI):
    from core.concurrency import shutdown_pools
    from core.jobs import reclaim_expired
    from storage.history import _do_cleanup

    # Project-root .env for local runs (does not override real process env).
    load_dotenv()
    configure_logging()
    # Fail fast on weak/missing admin credentials (unless ALLOW_INSECURE_ADMIN=1).
    validate_security_settings()
    ensure_file_dir()
    try:
        _do_cleanup()
    except Exception:
        pass
    try:
        await reclaim_expired()
    except Exception:
        pass
    logger.info("toolkit started version=%s tools=%s", __version__, _tool_count)
    try:
        yield
    finally:
        # Release ProcessPoolExecutor workers if any were started.
        shutdown_pools(wait=False)
        logger.info("toolkit stopped")


app = FastAPI(
    title="工具集",
    version=__version__,
    lifespan=lifespan,
    root_path=_import_root_path(),
)

# Trust X-Forwarded-* from reverse proxies (Baota/Nginx). Safe when only the
# proxy can reach uvicorn; restrict via network, not by disabling this.
try:
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
except Exception:
    pass

app.add_middleware(GZipMiddleware, minimum_size=500)


@app.exception_handler(ToolkitError)
async def toolkit_error_handler(request: Request, exc: ToolkitError):
    """Map unified ToolkitError hierarchy to HTTP responses."""
    rid = get_request_id()
    body: dict = {"detail": exc.detail}
    if rid:
        body["request_id"] = rid
    headers = {REQUEST_ID_HEADER: rid} if rid else {}
    return JSONResponse(
        status_code=exc.status_code,
        content=body,
        headers=headers,
    )


@app.middleware("http")
async def request_context_and_security_headers(request: Request, call_next):
    """Attach request id, security headers, and static cache policy."""
    incoming = request.headers.get(REQUEST_ID_HEADER) or request.headers.get(
        "X-Request-Id"
    )
    rid = (incoming or "").strip() or new_request_id()
    token = set_request_id(rid)
    request.state.request_id = rid
    try:
        response: Response = await call_next(request)
    finally:
        reset_request_id(token)

    response.headers.setdefault(REQUEST_ID_HEADER, rid)
    path = request.url.path
    # Baseline hardening for HTML/API responses (static files get a lighter set).
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    # Templates use static_url(...?v=mtime); allow long cache for versioned assets.
    if "/static/" in path:
        if request.url.query and "v=" in request.url.query:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            # Unversioned /static requests: short cache so reverse-proxy mistakes heal faster
            response.headers["Cache-Control"] = "public, max-age=300, must-revalidate"
    return response


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
async def documents_alias(request: Request):
    from tools.common import url_path

    return RedirectResponse(url=url_path("/c/document", request), status_code=307)


@app.get("/coding", response_class=HTMLResponse)
async def coding_alias(request: Request):
    from tools.common import url_path

    return RedirectResponse(url=url_path("/c/coding", request), status_code=307)


@app.get("/office", response_class=HTMLResponse)
async def office_alias(request: Request):
    from tools.common import url_path

    return RedirectResponse(url=url_path("/c/office", request), status_code=307)


@app.get("/api/tools")
async def api_tools():
    """Machine-readable tool catalog (for future clients)."""
    return JSONResponse(
        {
            "version": app.version,
            "categories": _categories_cache,
            "nav": _nav_items,
            "tools": TOOL_REGISTRY,
        }
    )


# Lightweight cache for expensive /health details (engines + storage).
_health_detail_cache: dict = {}
_health_detail_ts: float = 0.0
_HEALTH_DETAIL_TTL: float = 60.0


def _health_details(*, force: bool = False) -> dict:
    """Engine/OCR/storage snapshot; cached so probes stay cheap."""
    global _health_detail_cache, _health_detail_ts
    import time

    now = time.monotonic()
    if (
        not force
        and _health_detail_cache
        and now - _health_detail_ts < _HEALTH_DETAIL_TTL
    ):
        return _health_detail_cache

    from word2pdf import engine_info
    from converter import ocr_info

    w2p = engine_info()
    ocr = ocr_info()
    _health_detail_cache = {
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
            "retention_days": retention_days(),
            "count": record_count(),
        },
        "convert_concurrency": get_settings().convert_concurrency,
        "root_path": get_settings().root_path or "",
    }
    _health_detail_ts = now
    return _health_detail_cache


@app.get("/health")
async def health(detail: int = Query(0, ge=0, le=1)):
    """Liveness probe. Pass ``?detail=1`` for engines, OCR, and storage stats."""
    body: dict = {
        "status": "ok",
        "version": app.version,
        "tools": _tool_count,
    }
    if detail:
        body["categories"] = [
            {
                "id": c["id"],
                "name": c["name"],
                "count": len(c["tools"]),
                "route": c.get("route"),
            }
            for c in _categories_cache
        ]
        body.update(_health_details())
    return JSONResponse(body)


@app.get("/api/jobs/{job_id}")
async def api_job_status(job_id: str):
    """Poll an async conversion job (in-process store; lost on restart)."""
    from core.jobs import get_job, job_public_dict

    job = await get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(job_public_dict(job))


@app.get("/api/jobs/{job_id}/download")
async def api_job_download(job_id: str):
    """Download the result file for a completed job (if still on disk)."""
    from core.jobs import JobStatus, get_job

    job = await get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.done or not job.output_path:
        raise HTTPException(status_code=409, detail="Job has no downloadable result yet")
    if not os.path.isfile(job.output_path):
        raise HTTPException(status_code=410, detail="Job result expired or missing")
    headers = dict(job.response_headers or {})
    return FileResponse(
        job.output_path,
        filename=job.download_name or os.path.basename(job.output_path),
        media_type=job.media_type or "application/octet-stream",
        headers=headers,
    )


@app.get("/api/uploads")
async def api_uploads(request: Request, limit: int = Query(50, ge=1, le=200)):
    """JSON list of recent uploads (admin only; last retention window)."""
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return JSONResponse(
        {
            "retention_days": retention_days(),
            "items": list_records(limit=limit),
        }
    )


@app.get("/api/uploads/{record_id}/download")
async def download_upload(request: Request, record_id: str):
    """Download the archived input file for a history record (admin only)."""
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    rec = get_record(record_id)
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
