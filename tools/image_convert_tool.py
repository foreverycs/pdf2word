"""图片格式转换 — 页面与 API（JPEG / PNG / WebP / GIF / BMP / TIFF / ICO）。"""

from __future__ import annotations

import os
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.requests import Request

from core.concurrency import run_heavy
from media.image_convert import (
    ConvertError,
    convert_image,
    input_formats,
    output_formats,
)
from tools.common import (
    check_upload_size_header,
    safe_stem,
    save_upload,
    templates,
)
from tools.pipeline import TempWorkspace, archive_input, map_conversion_error

router = APIRouter(prefix="/tools/image-convert", tags=["image-convert"])


@router.get("", response_class=HTMLResponse)
async def tool_page(request: Request):
    return templates.TemplateResponse(
        request,
        "tools/image-convert.html",
        {
            "tool": {
                "name": "图片格式转换",
                "slug": "image-convert",
                "category": "office",
            },
            "input_formats": input_formats(),
            "output_formats": output_formats(),
        },
    )


def _parse_bool(raw: Optional[str], default: bool = True) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _parse_quality(raw: Optional[str]) -> int:
    if raw is None or str(raw).strip() == "":
        return 85
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="quality must be an integer 1–100"
        ) from exc
    if value < 1 or value > 100:
        raise HTTPException(
            status_code=400, detail="quality must be between 1 and 100"
        )
    return value


def _parse_target(raw: Optional[str]) -> str:
    t = (raw or "").strip().lower()
    if not t:
        raise HTTPException(status_code=400, detail="target_format is required")
    # Normalize aliases before ConvertError for clearer HTTP message.
    aliases = {"jpg": "jpeg", "jpe": "jpeg", "jfif": "jpeg", "tif": "tiff"}
    t = aliases.get(t, t)
    if t not in output_formats():
        raise HTTPException(
            status_code=400,
            detail=f"target_format must be one of: {', '.join(output_formats())}",
        )
    return t


def _parse_tolerance(raw: Optional[str]) -> int:
    if raw is None or str(raw).strip() == "":
        return 28
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="tolerance must be an integer 0–100"
        ) from exc
    if value < 0 or value > 100:
        raise HTTPException(
            status_code=400, detail="tolerance must be between 0 and 100"
        )
    return value


def _convert_file(
    path: str,
    *,
    filename: str,
    target_format: str,
    quality: int,
    strip_meta: bool,
    background: str,
    make_transparent: bool,
    chroma_key: str,
    tolerance: int,
    soft_edge: bool,
) -> dict:
    with open(path, "rb") as f:
        raw = f.read()
    return convert_image(
        raw,
        target_format,
        filename=filename,
        quality=quality,
        strip_meta=strip_meta,
        background=background,
        make_transparent=make_transparent,
        chroma_key=chroma_key,
        tolerance=tolerance,
        soft_edge=soft_edge,
    )


def _stats_headers(result: dict) -> dict:
    headers = {
        "X-Original-Bytes": str(result.get("original_bytes", 0)),
        "X-Output-Bytes": str(result.get("output_bytes", 0)),
        "X-Source-Format": str(result.get("source_format") or ""),
        "X-Target-Format": str(result.get("target_format") or ""),
        "X-Image-Width": str(result.get("width") or 0),
        "X-Image-Height": str(result.get("height") or 0),
    }
    if result.get("make_transparent"):
        headers["X-Make-Transparent"] = "1"
        if result.get("chroma_key"):
            headers["X-Chroma-Key"] = quote(str(result["chroma_key"])[:32])
    notes = result.get("notes") or []
    if notes:
        headers["X-Convert-Notes"] = quote(",".join(str(n) for n in notes)[:500])
    return headers


@router.get("/formats")
async def api_formats():
    return JSONResponse(
        {
            "input": input_formats(),
            "output": output_formats(),
            "defaults": {
                "target_format": "png",
                "quality": 85,
                "strip_meta": True,
                "background": "#ffffff",
                "make_transparent": False,
                "chroma_key": "auto",
                "tolerance": 28,
                "soft_edge": True,
            },
            "alpha_targets": ["png", "webp", "gif", "tiff", "ico"],
            "targets": [
                {
                    "id": "jpeg",
                    "label": "JPEG",
                    "hint": "照片常用；不支持透明，会铺底色",
                    "lossy": True,
                },
                {
                    "id": "png",
                    "label": "PNG",
                    "hint": "无损，保留透明通道（图标推荐）",
                    "lossy": False,
                },
                {
                    "id": "webp",
                    "label": "WebP",
                    "hint": "体积小，支持透明与动图",
                    "lossy": True,
                },
                {
                    "id": "gif",
                    "label": "GIF",
                    "hint": "动图 / 简单图；调色板有限",
                    "lossy": True,
                },
                {
                    "id": "bmp",
                    "label": "BMP",
                    "hint": "无压缩位图，体积较大",
                    "lossy": False,
                },
                {
                    "id": "tiff",
                    "label": "TIFF",
                    "hint": "印刷 / 扫描常用容器",
                    "lossy": False,
                },
                {
                    "id": "ico",
                    "label": "ICO",
                    "hint": "网站 / 应用图标（大图会缩到 256）",
                    "lossy": False,
                },
            ],
        }
    )


@router.post("/convert")
async def api_convert(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    target_format: str = Form(...),
    quality: str = Form("85"),
    strip_meta: str = Form("true"),
    background: str = Form("#ffffff"),
    make_transparent: str = Form("false"),
    chroma_key: str = Form("auto"),
    tolerance: str = Form("28"),
    soft_edge: str = Form("true"),
):
    """Convert one image; returns the converted file as a download."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    target = _parse_target(target_format)
    q = _parse_quality(quality)
    strip = _parse_bool(strip_meta, True)
    bg = (background or "#ffffff").strip() or "#ffffff"
    punch = _parse_bool(make_transparent, False)
    key = (chroma_key or "auto").strip() or "auto"
    tol = _parse_tolerance(tolerance)
    soft = _parse_bool(soft_edge, True)
    check_upload_size_header(file)

    ws = TempWorkspace(prefix="imgx_")
    try:
        work = ws.create()
        in_path = os.path.join(work, "input.bin")
        await save_upload(file, in_path)

        result = await run_heavy(
            _convert_file,
            in_path,
            filename=file.filename,
            target_format=target,
            quality=q,
            strip_meta=strip,
            background=bg,
            make_transparent=punch,
            chroma_key=key,
            tolerance=tol,
            soft_edge=soft,
        )
    except HTTPException:
        ws.cleanup_now()
        raise
    except ConvertError as exc:
        ws.cleanup_now()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        ws.cleanup_now()
        raise map_conversion_error(exc, label="Image conversion failed") from exc

    out_name = f"{safe_stem(file.filename)}{result['extension']}"
    out_path = ws.join(out_name)
    try:
        with open(out_path, "wb") as out:
            out.write(result["data"])
    except Exception as exc:
        ws.cleanup_now()
        raise HTTPException(status_code=500, detail=f"Write failed: {exc}") from exc

    try:
        await archive_input(
            tool="image-convert",
            original_name=file.filename or out_name,
            input_path=in_path,
            extra={
                "source_format": result.get("source_format"),
                "target_format": result.get("target_format"),
                "quality": q,
                "make_transparent": punch,
                "original_bytes": result.get("original_bytes"),
                "output_bytes": result.get("output_bytes"),
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


@router.post("/convert-info")
async def api_convert_info(
    file: UploadFile = File(...),
    target_format: str = Form(...),
    quality: str = Form("85"),
    strip_meta: str = Form("true"),
    background: str = Form("#ffffff"),
    make_transparent: str = Form("false"),
    chroma_key: str = Form("auto"),
    tolerance: str = Form("28"),
    soft_edge: str = Form("true"),
):
    """Convert and return JSON stats (no binary payload)."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    target = _parse_target(target_format)
    q = _parse_quality(quality)
    strip = _parse_bool(strip_meta, True)
    bg = (background or "#ffffff").strip() or "#ffffff"
    punch = _parse_bool(make_transparent, False)
    key = (chroma_key or "auto").strip() or "auto"
    tol = _parse_tolerance(tolerance)
    soft = _parse_bool(soft_edge, True)
    check_upload_size_header(file)

    raw = await file.read()
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
            convert_image,
            raw,
            target,
            filename=file.filename,
            quality=q,
            strip_meta=strip,
            background=bg,
            make_transparent=punch,
            chroma_key=key,
            tolerance=tol,
            soft_edge=soft,
        )
    except ConvertError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise map_conversion_error(exc, label="Image conversion failed") from exc

    body = {k: v for k, v in result.items() if k != "data"}
    body["filename"] = file.filename
    body["output_name"] = f"{safe_stem(file.filename)}{result['extension']}"
    return JSONResponse(body)
