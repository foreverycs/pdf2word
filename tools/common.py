"""Shared helpers for tool HTTP routes: templates, uploads, naming."""

from __future__ import annotations

import os
import re
from typing import Any, Optional

from fastapi import HTTPException, UploadFile
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from starlette.requests import Request

from core.settings import get_settings

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)

_SAFE_NAME_RE = re.compile(r"[^\w\u4e00-\u9fff.\-]+", re.UNICODE)

# Bump when shipping CSS/JS that must invalidate CDN/browser caches.
# Also mixed with file mtime so local edits bust cache without code changes.
_ASSET_BUILD = os.environ.get("STATIC_ASSET_VERSION") or "20260715b"


def effective_root_path(request: Optional[Request] = None) -> str:
    """App mount prefix for reverse proxies (ROOT_PATH or ASGI root_path)."""
    if request is not None:
        scoped = (request.scope.get("root_path") or "").rstrip("/")
        if scoped:
            return scoped if scoped.startswith("/") else f"/{scoped}"
    return get_settings().root_path


def join_url(root: str, path: str) -> str:
    """Join root prefix with an absolute app path (``/static/...``)."""
    if not path:
        return root or "/"
    if path.startswith(("http://", "https://", "//")):
        return path
    if not path.startswith("/"):
        path = "/" + path
    root = (root or "").rstrip("/")
    return f"{root}{path}" if root else path


def url_path(path: str, request: Optional[Request] = None) -> str:
    """Build a browser path that respects reverse-proxy subpath mounts."""
    return join_url(effective_root_path(request), path)


def _static_file_version(rel_path: str) -> str:
    """Return a short cache-buster for a static file under /static/."""
    rel = rel_path.lstrip("/").removeprefix("static/").lstrip("/")
    full = os.path.join(STATIC_DIR, rel.replace("/", os.sep))
    try:
        mtime = int(os.path.getmtime(full))
    except OSError:
        mtime = 0
    return f"{_ASSET_BUILD}.{mtime}" if mtime else _ASSET_BUILD


def static_url(path: str, request: Optional[Request] = None) -> str:
    """URL for a static asset with ``?v=`` cache buster.

    ``path`` may be ``/static/css/layout.css`` or ``css/layout.css``.
    """
    p = path.strip()
    if not p.startswith("/"):
        p = "/" + p
    if not p.startswith("/static/"):
        p = "/static" + p
    base = url_path(p, request)
    ver = _static_file_version(p)
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}v={ver}"


@pass_context
def _jinja_url_path(ctx: Any, path: str) -> str:
    request = ctx.get("request")
    if request is not None and not isinstance(request, Request):
        request = None
    return url_path(path, request)


@pass_context
def _jinja_static_url(ctx: Any, path: str) -> str:
    request = ctx.get("request")
    if request is not None and not isinstance(request, Request):
        request = None
    return static_url(path, request)


@pass_context
def _jinja_root_path(ctx: Any) -> str:
    request = ctx.get("request")
    if request is not None and not isinstance(request, Request):
        request = None
    return effective_root_path(request)


# Available in all templates:
#   {{ url_path('/tools/pdf2word') }}
#   {{ static_url('/static/css/layout.css') }}
templates.env.globals["url_path"] = _jinja_url_path
templates.env.globals["static_url"] = _jinja_static_url
templates.env.globals["root_path"] = _jinja_root_path

# Media types
DOCX_MEDIA = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
PDF_MEDIA = "application/pdf"
ZIP_MEDIA = "application/zip"

# Sensible defaults for modules that still read constants (updated via refresh_limits).
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_BATCH_FILES = 20


def max_upload_bytes() -> int:
    return get_settings().max_upload_bytes


def max_batch_files() -> int:
    return get_settings().max_batch_files


def upload_chunk_size() -> int:
    return get_settings().upload_chunk_size


def refresh_limits() -> None:
    """Refresh module-level limit constants after settings cache clear (tests)."""
    global MAX_UPLOAD_BYTES, MAX_BATCH_FILES
    s = get_settings()
    MAX_UPLOAD_BYTES = s.max_upload_bytes
    MAX_BATCH_FILES = s.max_batch_files


def safe_stem(filename: Optional[str], default: str = "output") -> str:
    stem = os.path.splitext(os.path.basename(filename or default))[0]
    stem = _SAFE_NAME_RE.sub("_", stem).strip("._") or default
    return stem[:80]


async def save_upload(
    file: UploadFile,
    dest: str,
    *,
    max_bytes: Optional[int] = None,
) -> int:
    """Stream an upload to ``dest``, enforcing size limit. Returns byte count."""
    limit = max_bytes if max_bytes is not None else max_upload_bytes()
    chunk_size = upload_chunk_size()
    total = 0
    with open(dest, "wb") as out:
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large (max {limit // (1024 * 1024)} MB)",
                )
            out.write(chunk)
    if total == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    return total


def check_upload_size_header(file: UploadFile, *, label: Optional[str] = None) -> None:
    """Reject early when Content-Length / starlette size exceeds the limit."""
    limit = max_upload_bytes()
    if file.size is not None and file.size > limit:
        name = label or file.filename or "file"
        raise HTTPException(
            status_code=413,
            detail=f"{name}: file too large (max {limit // (1024 * 1024)} MB)",
        )


__all__ = [
    "BASE_DIR",
    "TEMPLATES_DIR",
    "templates",
    "DOCX_MEDIA",
    "PDF_MEDIA",
    "ZIP_MEDIA",
    "MAX_UPLOAD_BYTES",
    "MAX_BATCH_FILES",
    "max_upload_bytes",
    "max_batch_files",
    "upload_chunk_size",
    "refresh_limits",
    "safe_stem",
    "save_upload",
    "check_upload_size_header",
    "effective_root_path",
    "join_url",
    "url_path",
    "static_url",
]
