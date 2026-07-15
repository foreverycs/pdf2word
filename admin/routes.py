"""Admin console: login, dashboard, uploads, system."""

from __future__ import annotations

import os
import time
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from admin.auth import (
    check_password,
    clear_session_cookie,
    create_session_token,
    is_admin,
    require_admin,
    set_session_cookie,
)
from admin.csrf import (
    FIELD_NAME as CSRF_FIELD,
    get_or_create_csrf_token,
    set_csrf_cookie,
    verify_csrf,
)
from admin.rate_limit import clear_failures, client_key, is_locked, register_failure
from core.settings import dotenv_status, get_settings
from core.version import __version__
from storage import (
    cleanup_expired,
    delete_record,
    delete_records,
    file_dir,
    get_record,
    list_records,
    resolve_stored,
    retention_days,
    storage_stats,
)
from tools import TOOL_REGISTRY, tools_by_category
from tools.common import templates

# NOTE: tags list closes with ], then APIRouter call closes with )
router = APIRouter(prefix="/admin", tags=["admin"])

# Pre-compute static data
_categories_cache = tools_by_category()

# Cached health info — engines don't change at runtime
_health_cache: dict = {}
_health_cache_ts: float = 0.0
_HEALTH_TTL: float = 60.0


def _tpl(request: Request, name: str, **ctx):
    csrf = get_or_create_csrf_token(request)
    data = {
        "request": request,
        "is_admin": is_admin(request),
        "app_version": __version__,
        "csrf_token": csrf,
        "csrf_field": CSRF_FIELD,
        **ctx,
    }
    resp = templates.TemplateResponse(request, name, data)
    set_csrf_cookie(resp, csrf)
    return resp


def _safe_next(next_url: Optional[str], request: Optional[Request] = None) -> str:
    from tools.common import effective_root_path, url_path

    root = effective_root_path(request)
    admin_home = url_path("/admin", request)
    if not next_url:
        return admin_home
    # Allow both app-absolute and root-prefixed paths.
    allowed_prefixes = ("/admin",)
    if root:
        allowed_prefixes = (f"{root}/admin", "/admin")
    if (
        any(next_url.startswith(p) for p in allowed_prefixes)
        and "://" not in next_url
        and "\\" not in next_url
    ):
        return next_url
    return admin_home


def _admin_url(path: str, request: Optional[Request] = None) -> str:
    from tools.common import url_path

    return url_path(path, request)


def _build_health() -> dict:
    global _health_cache, _health_cache_ts
    now = time.monotonic()
    if _health_cache and now - _health_cache_ts < _HEALTH_TTL:
        return _health_cache
    from word2pdf import engine_info
    from converter import ocr_info

    w2p = engine_info()
    ocr = ocr_info()
    _health_cache = {
        "word2pdf": w2p,
        "ocr": ocr,
        "tools": len(TOOL_REGISTRY),
        "categories": len(_categories_cache),
    }
    _health_cache_ts = now
    return _health_cache


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    next: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    if is_admin(request):
        return _redirect(_safe_next(next, request))
    return _tpl(
        request,
        "admin/login.html",
        next_url=_safe_next(next, request),
        error=error,
    )


@router.post("/login")
async def login_submit(
    request: Request,
    password: str = Form(...),
    next: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
):
    if not verify_csrf(request, csrf_token):
        dest = (
            _admin_url("/admin/login", request)
            + "?error="
            + quote("invalid session token; refresh and try again")
            + "&next="
            + quote(_safe_next(next, request))
        )
        return _redirect(dest)

    key = client_key(request)
    locked, retry_after = is_locked(key)
    if locked:
        dest = (
            _admin_url("/admin/login", request)
            + "?error="
            + quote(f"too many attempts; retry in {retry_after}s")
            + "&next="
            + quote(_safe_next(next, request))
        )
        return _redirect(dest)

    if not check_password(password):
        locked, retry_after = register_failure(key)
        err = (
            f"too many attempts; retry in {retry_after}s"
            if locked
            else "password error"
        )
        dest = (
            _admin_url("/admin/login", request)
            + "?error="
            + quote(err)
            + "&next="
            + quote(_safe_next(next, request))
        )
        return _redirect(dest)

    clear_failures(key)
    resp = _redirect(_safe_next(next, request))
    set_session_cookie(resp, create_session_token())
    set_csrf_cookie(resp, get_or_create_csrf_token(request))
    return resp


@router.post("/logout")
async def logout(
    request: Request,
    csrf_token: Optional[str] = Form(None),
):
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    resp = _redirect(_admin_url("/admin/login", request))
    clear_session_cookie(resp)
    return resp


@router.get("/logout")
async def logout_get(request: Request):
    """GET logout kept for bookmarks; prefer POST with CSRF."""
    resp = _redirect(_admin_url("/admin/login", request))
    clear_session_cookie(resp)
    return resp


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    redir = require_admin(request)
    if redir:
        return redir
    return _tpl(
        request,
        "admin/dashboard.html",
        active="dashboard",
        stats=storage_stats(),
        health=_build_health(),
        recent=list_records(limit=8),
        tools=TOOL_REGISTRY,
        categories=_categories_cache,
    )


@router.get("/uploads", response_class=HTMLResponse)
async def uploads_page(
    request: Request,
    tool: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=200),
):
    redir = require_admin(request)
    if redir:
        return redir

    all_items = list_records(limit=max(limit, 200))
    tool_f = (tool or "").strip()
    q_f = (q or "").strip().lower()

    tools_used = sorted(
        {str(r.get("tool") or "") for r in all_items if r.get("tool")}
    )

    items = all_items[:limit]
    if tool_f:
        items = [r for r in items if r.get("tool") == tool_f]
    if q_f:
        items = [
            r
            for r in items
            if q_f in str(r.get("original_name") or "").lower()
            or q_f in str(r.get("id") or "").lower()
        ]

    return _tpl(
        request,
        "admin/uploads.html",
        active="uploads",
        items=items,
        tool_filter=tool_f,
        q=q or "",
        tools_used=tools_used,
        retention_days=retention_days(),
        flash=request.query_params.get("msg"),
    )


@router.post("/uploads/batch-delete")
async def uploads_batch_delete(
    request: Request,
    csrf_token: Optional[str] = Form(None),
):
    """Delete multiple upload records selected in the admin table."""
    redir = require_admin(request)
    if redir:
        return redir
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="CSRF validation failed")

    form = await request.form()
    raw_ids = form.getlist("ids")
    ids = [str(v).strip() for v in raw_ids if str(v).strip()]
    # Cap batch size to avoid accidental huge deletes / DoS via form spam.
    max_batch = 200
    if len(ids) > max_batch:
        ids = ids[:max_batch]

    if not ids:
        return _redirect(
            _admin_url("/admin/uploads", request) + "?msg=" + quote("no selection")
        )

    removed = delete_records(ids)
    msg = f"deleted {removed}" if removed else "not found"
    return _redirect(_admin_url("/admin/uploads", request) + "?msg=" + quote(msg))


@router.post("/uploads/{record_id}/delete")
async def uploads_delete(
    request: Request,
    record_id: str,
    csrf_token: Optional[str] = Form(None),
):
    redir = require_admin(request)
    if redir:
        return redir
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    ok = delete_record(record_id)
    msg = "deleted" if ok else "not found"
    return _redirect(_admin_url("/admin/uploads", request) + "?msg=" + quote(msg))


@router.get("/uploads/{record_id}/download")
async def uploads_download(request: Request, record_id: str):
    redir = require_admin(request)
    if redir:
        return redir
    rec = get_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Record not found")
    rel = rec.get("input_rel")
    if not rel:
        raise HTTPException(status_code=404, detail="No file")
    path = resolve_stored(str(rel))
    if path is None:
        raise HTTPException(status_code=404, detail="File missing")
    name = rec.get("original_name") or path.name
    return FileResponse(path, filename=str(name))


_PREVIEW_MIME = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".html": "text/html",
    ".htm": "text/html",
}


@router.get("/uploads/{record_id}/preview")
async def uploads_preview(request: Request, record_id: str):
    redir = require_admin(request)
    if redir:
        return redir
    rec = get_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Record not found")
    rel = rec.get("input_rel")
    if not rel:
        raise HTTPException(status_code=404, detail="No file")
    path = resolve_stored(str(rel))
    if path is None:
        raise HTTPException(status_code=404, detail="File missing")
    ext = path.suffix.lower()
    media_type = _PREVIEW_MIME.get(ext, "application/octet-stream")
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{path.name}"'},
    )


@router.post("/cleanup")
async def run_cleanup(
    request: Request,
    csrf_token: Optional[str] = Form(None),
):
    redir = require_admin(request)
    if redir:
        return redir
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    removed = cleanup_expired()
    return _redirect(
        _admin_url("/admin/uploads", request) + "?msg=" + quote("cleaned %d" % removed)
    )


@router.get("/system", response_class=HTMLResponse)
async def system_page(request: Request):
    redir = require_admin(request)
    if redir:
        return redir
    return _tpl(
        request,
        "admin/system.html",
        active="system",
        health=_build_health(),
        stats=storage_stats(),
        tools=TOOL_REGISTRY,
        categories=_categories_cache,
        env_hints={
            **get_settings().admin_security_summary(),
            "UPLOAD_RETENTION_DAYS": str(retention_days()),
            "UPLOAD_FILE_DIR": str(file_dir()),
            "LIBREOFFICE_PATH": os.environ.get("LIBREOFFICE_PATH") or "(auto)",
            "PDF2WORD_OCR": os.environ.get("PDF2WORD_OCR") or "0",
            "MAX_UPLOAD_BYTES": str(get_settings().max_upload_bytes),
            **{f".env {k}": v for k, v in dotenv_status().items()},
        },
    )


@router.get("/api/stats")
async def api_stats(request: Request):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return JSONResponse(
        {
            "storage": storage_stats(),
            "health": _build_health(),
        }
    )
