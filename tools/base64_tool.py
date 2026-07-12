"""Base64 编码 / 解码工具 — 页面与 API。"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from coding import DecodeError, decode_base64, encode_base64, probe_base64

router = APIRouter(prefix="/tools/base64", tags=["base64"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

MAX_TEXT_CHARS = 2 * 1024 * 1024  # 2M chars
MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB


@router.get("", response_class=HTMLResponse)
async def tool_page(request: Request):
    return templates.TemplateResponse(
        request,
        "tools/base64.html",
        {
            "tool": {
                "name": "Base64 编解码",
                "slug": "base64",
                "category": "coding",
            }
        },
    )


def _check_text_size(text: str, label: str = "input") -> None:
    if text is None:
        raise HTTPException(status_code=400, detail=f"Missing {label}")
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"{label} too large (max {MAX_TEXT_CHARS} characters)",
        )


@router.post("/encode")
async def api_encode(
    text: Optional[str] = Form(None),
    charset: str = Form("utf-8"),
    variant: str = Form("standard"),
    wrap: int = Form(0),
    file: Optional[UploadFile] = File(None),
):
    """Encode plain text or an uploaded file to Base64."""
    try:
        if file is not None and file.filename:
            raw = await file.read()
            if len(raw) > MAX_FILE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large (max {MAX_FILE_BYTES // (1024 * 1024)} MB)",
                )
            if not raw:
                raise HTTPException(status_code=400, detail="Empty file")
            result = encode_base64(
                raw, charset=charset, variant=variant, wrap=wrap
            )
            result["filename"] = file.filename
        else:
            _check_text_size(text or "", "text")
            if text is None or text == "":
                # Allow empty string encode → empty base64
                result = encode_base64(
                    text or "", charset=charset, variant=variant, wrap=wrap
                )
            else:
                result = encode_base64(
                    text, charset=charset, variant=variant, wrap=wrap
                )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


@router.post("/decode")
async def api_decode(
    text: str = Form(...),
    charset: Optional[str] = Form("utf-8"),
    variant: str = Form("standard"),
    strict: bool = Form(False),
):
    """Decode a Base64 string to text (and hex of raw bytes)."""
    _check_text_size(text, "text")
    # Form may send empty charset to mean binary-only.
    cs: Optional[str]
    if charset is None or str(charset).strip() == "" or str(charset).lower() == "none":
        cs = None
    else:
        cs = str(charset).strip()
    try:
        result = decode_base64(
            text, charset=cs, variant=variant, strict=bool(strict)
        )
    except DecodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


@router.post("/probe")
async def api_probe(text: str = Form(...)):
    _check_text_size(text, "text")
    return JSONResponse(probe_base64(text))


@router.get("/presets")
async def api_presets():
    """Return UI option metadata."""
    samples = {
        "hello": {
            "plain": "Hello, 工具箱!",
            "base64": encode_base64("Hello, 工具箱!")["result"],
        }
    }
    return JSONResponse(
        {
            "charsets": ["utf-8", "utf-16", "latin-1", "ascii"],
            "variants": ["standard", "urlsafe"],
            "wrap_options": [0, 64, 76],
            "limits": {
                "max_text_chars": MAX_TEXT_CHARS,
                "max_file_bytes": MAX_FILE_BYTES,
            },
            "samples": samples,
        }
    )
