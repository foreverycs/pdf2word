"""代码格式化工具 — 页面与 API（多语言，含 JSON）。"""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.requests import Request

from coding import (
    FormatError,
    JsonError,
    format_code,
    format_json,
    list_languages,
    sample_for,
    validate_code,
    validate_json,
)
from coding.code_format import MAX_INPUT_CHARS
from tools.common import templates

router = APIRouter(prefix="/tools/json", tags=["code-format"])

TOOL_META = {
    "name": "代码格式化",
    "slug": "json",
    "category": "coding",
}


@router.get("", response_class=HTMLResponse)
async def tool_page(request: Request):
    return templates.TemplateResponse(
        request,
        "tools/json.html",
        {
            "tool": TOOL_META,
            "languages": list_languages(),
        },
    )


@router.get("/languages")
async def api_languages():
    """Return supported language catalog for the UI."""
    return JSONResponse({"languages": list_languages()})


def _bool_form(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


@router.post("/format")
async def api_format(
    text: str = Form(...),
    language: str = Form("json"),
    mode: str = Form("pretty"),
    indent: int = Form(2),
    sort_keys: str = Form("false"),
    ensure_ascii: str = Form("false"),
):
    """Pretty-print or minify source for the selected language."""
    if text is not None and len(text) > MAX_INPUT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"输入过长（最多 {MAX_INPUT_CHARS} 字符）",
        )
    try:
        result = format_code(
            text,
            language=language,
            mode=mode,
            indent=int(indent),
            sort_keys=_bool_form(sort_keys),
            ensure_ascii=_bool_form(ensure_ascii),
        )
    except (FormatError, JsonError) as exc:
        detail: dict = {"message": str(exc)}
        if getattr(exc, "line", None) is not None:
            detail["line"] = exc.line
            detail["column"] = exc.column
            detail["pos"] = exc.pos
        raise HTTPException(status_code=400, detail=detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


@router.post("/validate")
async def api_validate(
    text: str = Form(...),
    language: str = Form("json"),
):
    if text is not None and len(text) > MAX_INPUT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"输入过长（最多 {MAX_INPUT_CHARS} 字符）",
        )
    return JSONResponse(validate_code(text, language=language))


@router.get("/sample")
async def api_sample(language: str = "json"):
    try:
        sample = sample_for(language)
    except (FormatError, JsonError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"language": language, "sample": sample})


# Re-export for tests that may import helpers
__all__ = [
    "router",
    "format_json",
    "validate_json",
]
