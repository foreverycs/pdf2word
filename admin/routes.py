"""Admin console: login, dashboard, uploads, system."""

from __future__ import annotations

import html
import os
import time
from pathlib import Path
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

# Inline Word preview without LibreOffice / MS Word conversion.
# .docx → HTML via python-docx; legacy .doc is not supported this way.
_WORD_PREVIEW_EXTS = {".docx", ".doc"}
_DOCX_PREVIEW_MAX_PARAS = 4000
_DOCX_PREVIEW_MAX_TABLES = 200


def _docx_run_html(run) -> str:
    """Escape a python-docx run and wrap basic emphasis."""
    text = html.escape(run.text or "")
    if not text:
        return ""
    if getattr(run, "bold", None):
        text = f"<strong>{text}</strong>"
    if getattr(run, "italic", None):
        text = f"<em>{text}</em>"
    if getattr(run, "underline", None):
        text = f"<u>{text}</u>"
    return text


def _docx_paragraph_html(paragraph) -> str:
    style_name = ""
    try:
        style_name = (paragraph.style.name or "").lower() if paragraph.style else ""
    except Exception:
        style_name = ""

    parts = [_docx_run_html(run) for run in paragraph.runs]
    inner = "".join(parts).strip()
    # python-docx sometimes leaves empty runs when text is only on the paragraph
    if not inner:
        inner = html.escape(paragraph.text or "").strip()
    if not inner:
        return "<p class='empty'>&nbsp;</p>"

    align = ""
    try:
        raw = paragraph.alignment
        if raw is not None:
            # WD_ALIGN_PARAGRAPH: 0 left, 1 center, 2 right, 3 justify
            mapping = {1: "center", 2: "right", 3: "justify"}
            if int(raw) in mapping:
                align = f' style="text-align:{mapping[int(raw)]}"'
    except Exception:
        align = ""

    if style_name.startswith("heading 1") or style_name == "title":
        return f"<h1{align}>{inner}</h1>"
    if style_name.startswith("heading 2"):
        return f"<h2{align}>{inner}</h2>"
    if style_name.startswith("heading 3"):
        return f"<h3{align}>{inner}</h3>"
    if style_name.startswith("heading"):
        return f"<h4{align}>{inner}</h4>"
    if "list" in style_name:
        return f"<li{align}>{inner}</li>"
    return f"<p{align}>{inner}</p>"


def _docx_table_html(table) -> str:
    rows_html: list[str] = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            cell_text = html.escape((cell.text or "").strip()) or "&nbsp;"
            cells.append(f"<td>{cell_text}</td>")
        rows_html.append("<tr>" + "".join(cells) + "</tr>")
    return "<table>" + "".join(rows_html) + "</table>"


def _docx_to_preview_html(path: Path, *, title: str = "") -> str:
    """Render a .docx file to a self-contained HTML preview page.

    Uses python-docx only (no LibreOffice / MS Word). Layout fidelity is
    approximate: paragraphs, headings, basic emphasis, and tables.
    """
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    body_parts: list[str] = []
    para_count = 0
    table_count = 0
    truncated = False

    # Walk body in document order so tables sit between surrounding paragraphs.
    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag
        if tag == qn("w:p"):
            if para_count >= _DOCX_PREVIEW_MAX_PARAS:
                truncated = True
                break
            body_parts.append(_docx_paragraph_html(Paragraph(child, doc)))
            para_count += 1
        elif tag == qn("w:tbl"):
            if table_count >= _DOCX_PREVIEW_MAX_TABLES:
                truncated = True
                break
            body_parts.append(_docx_table_html(Table(child, doc)))
            table_count += 1

    if truncated:
        body_parts.append(
            "<p class='note'>文档较长，预览已截断（仅展示前若干段落/表格）。</p>"
        )

    # Collapse consecutive list items into <ul>
    merged: list[str] = []
    list_buf: list[str] = []
    for part in body_parts:
        if part.startswith("<li"):
            list_buf.append(part)
        else:
            if list_buf:
                merged.append("<ul>" + "".join(list_buf) + "</ul>")
                list_buf = []
            merged.append(part)
    if list_buf:
        merged.append("<ul>" + "".join(list_buf) + "</ul>")

    safe_title = html.escape(title or path.name)
    content = "\n".join(merged) if merged else "<p class='note'>（文档无可见文本内容）</p>"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{safe_title}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 28px 20px 48px;
    font-family: "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif;
    font-size: 15px;
    line-height: 1.65;
    color: #1e293b;
    background: #eef1f8;
  }}
  .sheet {{
    max-width: 820px;
    margin: 0 auto;
    background: #fff;
    border-radius: 12px;
    box-shadow: 0 8px 28px rgba(15, 23, 42, 0.08);
    padding: 40px 48px;
    min-height: 60vh;
  }}
  h1, h2, h3, h4 {{
    color: #0f172a;
    line-height: 1.3;
    margin: 1.2em 0 0.5em;
  }}
  h1 {{ font-size: 1.55rem; }}
  h2 {{ font-size: 1.3rem; }}
  h3 {{ font-size: 1.12rem; }}
  h4 {{ font-size: 1.02rem; }}
  p {{ margin: 0.55em 0; white-space: pre-wrap; word-break: break-word; }}
  p.empty {{ margin: 0.35em 0; }}
  ul {{ margin: 0.5em 0 0.5em 1.2em; padding: 0; }}
  li {{ margin: 0.25em 0; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 1em 0;
    font-size: 13.5px;
  }}
  td {{
    border: 1px solid #cbd5e1;
    padding: 8px 10px;
    vertical-align: top;
    white-space: pre-wrap;
    word-break: break-word;
  }}
  .note {{
    color: #64748b;
    font-size: 13px;
    text-align: center;
    margin-top: 1.5em;
  }}
  @media (max-width: 640px) {{
    body {{ padding: 12px; }}
    .sheet {{ padding: 22px 18px; border-radius: 10px; }}
  }}
</style>
</head>
<body>
  <article class="sheet">
    {content}
  </article>
</body>
</html>
"""


def _word_preview_response(path: Path, *, original_name: str) -> HTMLResponse:
    """Build an HTML preview response for Word uploads (no PDF conversion)."""
    ext = path.suffix.lower()
    if ext == ".doc":
        raise HTTPException(
            status_code=415,
            detail=(
                "旧版 .doc 无法在不转换的情况下预览。"
                "请使用 .docx，或下载后用 Word 打开。"
            ),
        )
    if ext != ".docx":
        raise HTTPException(status_code=415, detail="Unsupported Word format")
    try:
        html_doc = _docx_to_preview_html(path, title=original_name or path.name)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"无法解析 Word 文档: {exc}",
        ) from exc
    safe_name = (original_name or path.name).replace('"', "")
    return HTMLResponse(
        content=html_doc,
        headers={
            "Content-Disposition": f'inline; filename="{safe_name}.html"',
            "X-Preview-Source": "docx-html",
            "Cache-Control": "private, max-age=120",
            "X-Content-Type-Options": "nosniff",
        },
    )


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

    # Word: render as HTML (no LibreOffice / MS Word conversion).
    if ext in _WORD_PREVIEW_EXTS:
        return _word_preview_response(
            path, original_name=str(rec.get("original_name") or path.name)
        )

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
