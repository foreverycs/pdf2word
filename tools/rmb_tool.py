"""人民币金额转大写 — 页面与 API。"""

from __future__ import annotations

import os

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from office import AmountError, to_rmb_upper

router = APIRouter(prefix="/tools/rmb", tags=["rmb"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

MAX_TEXT_CHARS = 64


@router.get("", response_class=HTMLResponse)
async def tool_page(request: Request):
    return templates.TemplateResponse(
        request,
        "tools/rmb.html",
        {
            "tool": {
                "name": "人民币大写",
                "slug": "rmb",
                "category": "office",
            }
        },
    )


@router.post("/convert")
async def api_convert(
    amount: str = Form(...),
    prefix: str = Form("true"),
):
    """Convert Arabic-numeral amount to Chinese RMB uppercase."""
    if amount is None or str(amount).strip() == "":
        raise HTTPException(status_code=400, detail="请输入金额")
    if len(amount) > MAX_TEXT_CHARS:
        raise HTTPException(status_code=413, detail="输入过长")

    use_prefix = str(prefix).strip().lower() in ("1", "true", "yes", "on")
    try:
        result = to_rmb_upper(amount, prefix=use_prefix)
    except AmountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


@router.get("/samples")
async def api_samples():
    samples = [
        {"amount": "0", "label": "零元"},
        {"amount": "0.05", "label": "五分"},
        {"amount": "0.10", "label": "一角"},
        {"amount": "1234.56", "label": "常见金额"},
        {"amount": "10000", "label": "一万"},
        {"amount": "100000000", "label": "一亿"},
    ]
    out = []
    for s in samples:
        try:
            r = to_rmb_upper(s["amount"], prefix=True)
            out.append({**s, "result": r["result"]})
        except AmountError:
            continue
    return JSONResponse({"samples": out})
