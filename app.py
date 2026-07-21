from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
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
    enabled_tools,
    featured_tools,
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


def _import_root_path() -> str:
    """Read ROOT_PATH without full credential validation (import-time safe)."""
    from core.settings import _normalize_root_path

    return _normalize_root_path(os.environ.get("ROOT_PATH") or "")


def _page_ctx(
    *,
    active_nav: str = "home",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Common template context for pages that share the top menu.

    Nav and tool counts are resolved per-request so admin enable/disable
    flags take effect without restart.
    """
    public = enabled_tools()
    featured = featured_tools()
    module_count = len(public)
    featured_count = len(featured)
    # Flat catalog for homepage recent-tools hydration (modules + featured).
    catalog: list = []
    seen: set = set()
    for t in list(public) + list(featured):
        slug = str(t.get("slug") or "")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        catalog.append(
            {
                "slug": slug,
                "name": t.get("name"),
                "route": t.get("route"),
                "icon": t.get("icon"),
                "description": t.get("description"),
                "accent": t.get("accent") or "indigo",
            }
        )
    ctx: Dict[str, Any] = {
        "nav_items": nav_categories(),
        "active_nav": active_nav,
        # Homepage stats: modules + featured (featured is outside module grids).
        "module_count": module_count,
        "featured_count": featured_count,
        "tool_count": module_count + featured_count,
        "featured_tools": featured,
        "tools_catalog": catalog,
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
    # File express: expiry only blocks user pickup; packages are retained for
    # admin indefinitely (no automatic purge on startup).
    try:
        await reclaim_expired()
    except Exception:
        pass
    logger.info(
        "toolkit started version=%s tools=%s",
        __version__,
        len(TOOL_REGISTRY),
    )
    # Probe LibreOffice / OCR off the request path so admin dashboard is snappy.
    try:
        from admin.routes import schedule_health_warm

        schedule_health_warm()
    except Exception:
        pass
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


def _is_public_convert_path(path: str) -> bool:
    """Paths that accept heavy uploads / create jobs (rate-limited)."""
    if not path:
        return False
    # Strip ROOT_PATH if present is handled by ASGI; path is app-relative.
    if path.startswith("/api/jobs") and path.rstrip("/").endswith("/download"):
        return True  # download is lighter but still abuse-sensitive
    markers = (
        "/convert-async",
        "/convert-batch-async",
        "/convert-batch",
        "/convert",
        "/compress",  # image-compress (and future media tools)
        "/send",  # file express upload
        "/pickup",  # file express download
    )
    return any(m in path for m in markers)


@app.middleware("http")
async def request_context_and_security_headers(request: Request, call_next):
    """Attach request id, security headers, rate limit, and static cache policy."""
    incoming = request.headers.get(REQUEST_ID_HEADER) or request.headers.get(
        "X-Request-Id"
    )
    rid = (incoming or "").strip() or new_request_id()
    token = set_request_id(rid)
    request.state.request_id = rid
    try:
        path = request.url.path

        # Admin-controlled tool enable flags (hide + block disabled tools).
        if path.startswith("/tools/") or "/tools/" in path:
            from core.tool_flags import is_tool_path_enabled, tool_slug_from_path

            # path may include ROOT_PATH when not stripped; slug helper is resilient.
            check_path = path
            root = (get_settings().root_path or "").rstrip("/")
            if root and check_path.startswith(root + "/"):
                check_path = check_path[len(root) :]
            if not is_tool_path_enabled(check_path):
                slug = tool_slug_from_path(check_path) or "tool"
                accept = (request.headers.get("accept") or "").lower()
                wants_html = "text/html" in accept and "application/json" not in accept
                if request.method == "GET" and wants_html:
                    return HTMLResponse(
                        content=(
                            "<!DOCTYPE html><html lang='zh-CN'><head>"
                            "<meta charset='utf-8'/><title>功能已关闭</title></head>"
                            "<body style='font-family:system-ui;padding:48px;text-align:center'>"
                            f"<h1>功能已关闭</h1><p>「{slug}」已被管理员停用。</p>"
                            f"<p><a href='{root or ''}/'>返回首页</a></p>"
                            "</body></html>"
                        ),
                        status_code=403,
                        headers={REQUEST_ID_HEADER: rid},
                    )
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": f"Tool '{slug}' is disabled by administrator",
                        "slug": slug,
                        "request_id": rid,
                    },
                    headers={REQUEST_ID_HEADER: rid},
                )

        # Public convert / job download rate limit (process-local).
        if request.method in ("POST", "GET") and _is_public_convert_path(path):
            # Only rate-limit write-ish convert POSTs and job downloads.
            if request.method == "POST" or path.rstrip("/").endswith("/download"):
                from core.api_rate_limit import check_rate, client_key_from_request

                s = get_settings()
                if s.api_rate_limit > 0:
                    key = f"api:{client_key_from_request(request)}"
                    allowed, retry_after, remaining = check_rate(
                        key,
                        limit=s.api_rate_limit,
                        window_sec=float(s.api_rate_window_sec),
                    )
                    if not allowed:
                        return JSONResponse(
                            status_code=429,
                            content={
                                "detail": (
                                    f"Too many requests. Retry after {retry_after}s"
                                ),
                                "request_id": rid,
                            },
                            headers={
                                REQUEST_ID_HEADER: rid,
                                "Retry-After": str(retry_after),
                                "X-RateLimit-Limit": str(s.api_rate_limit),
                                "X-RateLimit-Remaining": "0",
                            },
                        )
                    request.state.rate_limit_remaining = remaining

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
    elif response.status_code == 200 and (
        path == "/" or path.startswith("/c/")
    ):
        # Catalog pages: short private cache so top-nav switches feel warm after first visit.
        response.headers.setdefault(
            "Cache-Control", "private, max-age=30, stale-while-revalidate=120"
        )
    remaining = getattr(request.state, "rate_limit_remaining", None)
    if remaining is not None:
        response.headers.setdefault(
            "X-RateLimit-Limit", str(get_settings().api_rate_limit)
        )
        response.headers.setdefault("X-RateLimit-Remaining", str(remaining))
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


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    """Service worker at site root so scope covers the whole app."""
    from fastapi.responses import FileResponse

    path = os.path.join(BASE_DIR, "static", "sw.js")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="sw missing")
    return FileResponse(
        path,
        media_type="application/javascript; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Service-Worker-Allowed": "/",
        },
    )


@app.get("/manifest.webmanifest", include_in_schema=False)
async def web_manifest(request: Request):
    """PWA manifest with start_url respecting reverse-proxy root_path."""
    from fastapi.responses import JSONResponse
    from tools.common import url_path

    root = url_path("/", request)
    if not root.endswith("/"):
        # start_url should be a path the browser can open
        start = root if root else "/"
    else:
        start = root
    body = {
        "name": "工具集",
        "short_name": "工具集",
        "description": "本地部署的办公与开发工具台：PDF/Word、发票合并、编码工具与文件快递",
        "start_url": start,
        "scope": start if start.endswith("/") else (start + "/" if start != "/" else "/"),
        "display": "standalone",
        "orientation": "any",
        "background_color": "#0b1220",
        "theme_color": "#4f46e5",
        "lang": "zh-CN",
        "categories": ["productivity", "utilities"],
        "icons": [
            {
                "src": url_path("/static/icons/icon-192.png", request),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": url_path("/static/icons/icon-512.png", request),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": url_path("/static/icons/icon-maskable-512.png", request),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
    }
    return JSONResponse(
        body,
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


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
    """Machine-readable tool catalog (enabled tools only)."""
    public = enabled_tools()
    featured = featured_tools()
    return JSONResponse(
        {
            "version": app.version,
            "categories": tools_by_category(),
            "nav": nav_categories(),
            "tools": public,
            "featured": featured,
            "counts": {
                "module": len(public),
                "featured": len(featured),
                "total": len(public) + len(featured),
            },
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
    from core.jobs import jobs_backend_name

    settings = get_settings()
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
        "convert_concurrency": settings.convert_concurrency,
        "root_path": settings.root_path or "",
        "jobs": {
            "backend": jobs_backend_name(),
            "note": "process-local; use a single uvicorn worker",
        },
        "api_rate_limit": settings.api_rate_limit,
        "api_rate_window_sec": settings.api_rate_window_sec,
    }
    _health_detail_ts = now
    return _health_detail_cache


# Shown when async job is missing (restart, TTL, or multi-worker routing).
_JOB_MISSING_DETAIL = (
    "任务不存在或已过期。可能因服务重启、任务清理，或使用了多个 uvicorn worker。"
    "异步 PDF/Word 任务为进程内存存储，请使用 --workers 1 后重新提交。"
)


@app.get("/health")
async def health(detail: int = Query(0, ge=0, le=1)):
    """Liveness probe. Pass ``?detail=1`` for engines, OCR, and storage stats."""
    from core.jobs import jobs_backend_name

    public = enabled_tools()
    featured = featured_tools()
    body: dict = {
        "status": "ok",
        "version": app.version,
        # Public inventory = module catalog + homepage featured tools.
        "tools": len(public) + len(featured),
        "tools_module": len(public),
        "tools_featured": len(featured),
        "tools_registered": len(TOOL_REGISTRY),
        # Ops hint: async convert jobs are process-local.
        "jobs": {
            "backend": jobs_backend_name(),
            "single_worker_required": True,
            "note": (
                "Async conversion jobs are in-process; run uvicorn with --workers 1 "
                "(multi-worker causes job 404 on poll/download)."
            ),
            "note_zh": "异步任务为进程内存存储，请使用 --workers 1。",
        },
    }
    if detail:
        body["categories"] = [
            {
                "id": c["id"],
                "name": c["name"],
                "count": len(c["tools"]),
                "route": c.get("route"),
            }
            for c in tools_by_category()
        ]
        body.update(_health_details())
    return JSONResponse(body)


@app.get("/api/jobs/{job_id}")
async def api_job_status(job_id: str):
    """Poll an async conversion job (in-process store; lost on restart)."""
    from core.jobs import get_job, job_public_dict

    job = await get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=_JOB_MISSING_DETAIL)
    return JSONResponse(job_public_dict(job))


@app.get("/api/jobs/{job_id}/download")
async def api_job_download(job_id: str, background_tasks: BackgroundTasks):
    """Download the result file for a completed job (if still on disk).

    After the response is sent, temporary files are deleted (mark_downloaded).
    """
    from core.jobs import JobStatus, get_job, mark_downloaded

    job = await get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=_JOB_MISSING_DETAIL)
    if job.status != JobStatus.done or not job.output_path:
        raise HTTPException(
            status_code=409,
            detail="任务尚未完成，暂无可下载结果。请稍候再试或重新提交。",
        )
    if not os.path.isfile(job.output_path):
        raise HTTPException(
            status_code=410,
            detail="结果文件已过期或已清理，请重新转换。",
        )
    headers = dict(job.response_headers or {})
    path = job.output_path
    filename = job.download_name or os.path.basename(path)
    media = job.media_type or "application/octet-stream"

    async def _cleanup() -> None:
        try:
            await mark_downloaded(job_id)
        except Exception:
            logger.exception("job download cleanup failed id=%s", job_id)

    background_tasks.add_task(_cleanup)
    return FileResponse(
        path,
        filename=filename,
        media_type=media,
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
