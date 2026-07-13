"""JSON 格式化 / 压缩工具 — 页面与 API。"""

from __future__ import annotations

import os

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from coding import JsonError, format_json, validate_json
from coding.json_format import MAX_INPUT_CHARS

router = APIRouter(prefix="/tools/json", tags=["json"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


@router.get("", response_class=HTMLResponse)
async def tool_page(request: Request):
    return templates.TemplateResponse(
        request,
        "tools/json.html",
        {
            "tool": {
                "name": "JSON 格式化",
                "slug": "json",
                "category": "coding",
            }
        },
    )


def _bool_form(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


@router.post("/format")
async def api_format(
    text: str = Form(...),
    mode: str = Form("pretty"),
    indent: int = Form(2),
    sort_keys: str = Form("false"),
    ensure_ascii: str = Form("false"),
):
    """Pretty-print or minify JSON text."""
    if text is not None and len(text) > MAX_INPUT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"输入过长（最多 {MAX_INPUT_CHARS} 字符）",
        )
    try:
        result = format_json(
            text,
            mode=mode,
            indent=int(indent),
            sort_keys=_bool_form(sort_keys),
            ensure_ascii=_bool_form(ensure_ascii),
        )
    except JsonError as exc:
        detail: dict = {"message": str(exc)}
        if exc.line is not None:
            detail["line"] = exc.line
            detail["column"] = exc.column
            detail["pos"] = exc.pos
        raise HTTPException(status_code=400, detail=detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


@router.post("/validate")
async def api_validate(text: str = Form(...)):
    if text is not None and len(text) > MAX_INPUT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"输入过长（最多 {MAX_INPUT_CHARS} 字符）",
        )
    return JSONResponse(validate_json(text))
