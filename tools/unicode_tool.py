"""中文 Unicode 还原 / 编码 — 页面与 API。"""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.requests import Request

from coding import (
    UnicodeCodecError,
    decode_unicode,
    encode_unicode,
    probe_unicode,
)
from tools.common import templates

router = APIRouter(prefix="/tools/unicode", tags=["unicode"])

MAX_TEXT_CHARS = 2 * 1024 * 1024  # 2M chars


@router.get("", response_class=HTMLResponse)
async def tool_page(request: Request):
    return templates.TemplateResponse(
        request,
        "tools/unicode.html",
        {
            "tool": {
                "name": "中文 Unicode 还原",
                "slug": "unicode",
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


@router.post("/decode")
async def api_decode(
    text: str = Form(...),
    mode: str = Form("auto"),
    max_passes: int = Form(3),
):
    """还原 Unicode 转义为中文等真实字符。"""
    _check_text_size(text, "text")
    try:
        result = decode_unicode(
            text, mode=(mode or "auto").strip(), max_passes=int(max_passes)
        )
    except UnicodeCodecError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


@router.post("/encode")
async def api_encode(
    text: str = Form(...),
    style: str = Form("backslash_u"),
    uppercase: str = Form("false"),
):
    """将文本编码为 Unicode 转义。"""
    _check_text_size(text, "text")
    use_upper = str(uppercase).strip().lower() in ("1", "true", "yes", "on")
    try:
        result = encode_unicode(
            text, style=(style or "backslash_u").strip(), uppercase=use_upper
        )
    except UnicodeCodecError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


@router.post("/probe")
async def api_probe(text: str = Form(...)):
    _check_text_size(text, "text")
    return JSONResponse(probe_unicode(text))


@router.get("/samples")
async def api_samples():
    samples = [
        {
            "label": "JSON 中文",
            "text": r"\u4f60\u597d\uff0c\u4e16\u754c",
        },
        {
            "label": "混合文本",
            "text": r'{"name":"\u5f20\u4e09","city":"\u5317\u4eac"}',
        },
        {
            "label": "双重转义",
            "text": r"\\u4e2d\\u6587",
        },
        {
            "label": "U+ 码位",
            "text": "U+4E2D U+6587",
        },
        {
            "label": "HTML 实体",
            "text": "&#x4f60;&#x597d;",
        },
        {
            "label": "%u 旧式",
            "text": "%u4F60%u597D",
        },
    ]
    out = []
    for s in samples:
        try:
            r = decode_unicode(s["text"], mode="auto")
            out.append({**s, "result": r["result"]})
        except UnicodeCodecError:
            out.append({**s, "result": ""})
    return JSONResponse({"samples": out})
