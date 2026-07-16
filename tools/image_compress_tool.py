"""图片压缩 — 页面与 API（JPEG / PNG / GIF / SVG）。"""

from __future__ import annotations

import os
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.requests import Request

from core.concurrency import run_heavy
from media import CompressError, compress_image, supported_formats
from tools.common import (
    check_upload_size_header,
    safe_stem,
    save_upload,
    templates,
)
from tools.pipeline import TempWorkspace, archive_input, map_conversion_error

router = APIRouter(prefix="/tools/image-compress", tags=["image-compress"])

_QUALITY_PRESETS = ("high", "balanced", "strong")


@router.get("", response_class=HTMLResponse)
async def tool_page(request: Request):
    return templates.TemplateResponse(
        request,
        "tools/image-compress.html",
        {
            "tool": {
                "name": "图片压缩",
                "slug": "image-compress",
                "category": "office",
            },
            "formats": supported_formats(),
            "qualities": list(_QUALITY_PRESETS),
        },
    )


def _parse_bool(raw: Optional[str], default: bool = True) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _parse_quality(raw: Optional[str]) -> str:
    q = (raw or "balanced").strip().lower()
    if q not in _QUALITY_PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"quality must be one of: {', '.join(_QUALITY_PRESETS)}",
        )
    return q


def _parse_max_side(raw: Optional[str]) -> Optional[int]:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="max_side must be an integer") from exc
    if value < 0:
        raise HTTPException(status_code=400, detail="max_side must be >= 0")
    if value > 20000:
        raise HTTPException(status_code=400, detail="max_side too large")
    return value


def _compress_file(
    path: str,
    *,
    filename: str,
    quality: str,
    strip_meta: bool,
    max_side: Optional[int],
) -> dict:
    with open(path, "rb") as f:
        raw = f.read()
    return compress_image(
        raw,
        filename=filename,
        quality=quality,
        strip_meta=strip_meta,
        max_side=max_side,
    )


def _stats_headers(result: dict) -> dict:
    headers = {
        "X-Original-Bytes": str(result.get("original_bytes", 0)),
        "X-Compressed-Bytes": str(result.get("compressed_bytes", 0)),
        "X-Saved-Bytes": str(result.get("saved_bytes", 0)),
        "X-Percent-Saved": str(result.get("percent_saved", 0)),
        "X-Image-Format": str(result.get("format") or ""),
        "X-Image-Width": str(result.get("width") or 0),
        "X-Image-Height": str(result.get("height") or 0),
    }
    notes = result.get("notes") or []
    if notes:
        headers["X-Compress-Notes"] = quote(",".join(str(n) for n in notes)[:500])
    return headers


@router.get("/presets")
async def api_presets():
    return JSONResponse(
        {
            "formats": supported_formats(),
            "qualities": [
                {
                    "id": "high",
                    "label": "高质量",
                    "hint": "几乎无损观感，体积仍会下降",
                },
                {
                    "id": "balanced",
                    "label": "均衡（推荐）",
                    "hint": "体积显著减小，肉眼几乎无感",
                },
                {
                    "id": "strong",
                    "label": "强压缩",
                    "hint": "更小体积；超大图会限制最长边",
                },
            ],
            "defaults": {
                "quality": "balanced",
                "strip_meta": True,
            },
        }
    )


@router.post("/compress")
async def api_compress(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    quality: str = Form("balanced"),
    strip_meta: str = Form("true"),
    max_side: Optional[str] = Form(None),
):
    """Compress one image; returns the compressed file as a download."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    q = _parse_quality(quality)
    strip = _parse_bool(strip_meta, True)
    side = _parse_max_side(max_side)
    check_upload_size_header(file)

    ws = TempWorkspace(prefix="imgc_")
    try:
        work = ws.create()
        in_path = os.path.join(work, "input.bin")
        await save_upload(file, in_path)

        result = await run_heavy(
            _compress_file,
            in_path,
            filename=file.filename,
            quality=q,
            strip_meta=strip,
            max_side=side,
        )
    except HTTPException:
        ws.cleanup_now()
        raise
    except CompressError as exc:
        ws.cleanup_now()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        ws.cleanup_now()
        raise map_conversion_error(exc, label="Image compression failed") from exc

    out_name = f"{safe_stem(file.filename)}_compressed{result['extension']}"
    out_path = ws.join(out_name)
    try:
        with open(out_path, "wb") as out:
            out.write(result["data"])
    except Exception as exc:
        ws.cleanup_now()
        raise HTTPException(status_code=500, detail=f"Write failed: {exc}") from exc

    # Archive original input only (consistent with other tools).
    try:
        await archive_input(
            tool="image-compress",
            original_name=file.filename or out_name,
            input_path=in_path,
            extra={
                "format": result.get("format"),
                "quality": q,
                "original_bytes": result.get("original_bytes"),
                "compressed_bytes": result.get("compressed_bytes"),
                "percent_saved": result.get("percent_saved"),
            },
        )
    except Exception:
        pass

    ws.schedule_cleanup(background_tasks)
    headers = _stats_headers(result)
    return FileResponse(
        out_path,
        media_type=result["media_type"],
        filename=out_name,
        headers=headers,
        background=None,
    )


@router.post("/compress-info")
async def api_compress_info(
    file: UploadFile = File(...),
    quality: str = Form("balanced"),
    strip_meta: str = Form("true"),
    max_side: Optional[str] = Form(None),
):
    """Compress and return JSON stats + base64 preview is intentionally omitted
    (files may be large). Use ``/compress`` to download the result.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    q = _parse_quality(quality)
    strip = _parse_bool(strip_meta, True)
    side = _parse_max_side(max_side)
    check_upload_size_header(file)

    raw = await file.read()
    limit = None
    try:
        from tools.common import max_upload_bytes

        limit = max_upload_bytes()
        if len(raw) > limit:
            raise HTTPException(
                status_code=413,
                detail=f"File too large (max {limit // (1024 * 1024)} MB)",
            )
    except HTTPException:
        raise

    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")

    try:
        result = await run_heavy(
            compress_image,
            raw,
            filename=file.filename,
            quality=q,
            strip_meta=strip,
            max_side=side,
        )
    except CompressError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise map_conversion_error(exc, label="Image compression failed") from exc

    # Do not echo binary in JSON.
    body = {k: v for k, v in result.items() if k != "data"}
    body["filename"] = file.filename
    body["output_name"] = (
        f"{safe_stem(file.filename)}_compressed{result['extension']}"
    )
    return JSONResponse(body)
