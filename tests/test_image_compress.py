"""Tests for image compression (core + HTTP)."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from media import CompressError, compress_image, detect_format, supported_formats


def _jpeg_bytes(size=(400, 300), color=(40, 120, 200), quality=95) -> bytes:
    img = Image.new("RGB", size, color)
    # Add some detail so compression has something to do.
    for x in range(0, size[0], 17):
        for y in range(0, size[1], 13):
            img.putpixel((x, y), ((x * 3) % 256, (y * 5) % 256, 180))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _png_bytes(size=(120, 80), mode="RGBA") -> bytes:
    if mode == "RGBA":
        img = Image.new("RGBA", size, (255, 0, 0, 128))
        for i in range(size[0]):
            img.putpixel((i, size[1] // 2), (0, 255, 0, 255))
    else:
        img = Image.new("RGB", size, (10, 20, 30))
        for i in range(min(size)):
            img.putpixel((i, i), (200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _gif_bytes(size=(60, 40), frames=3) -> bytes:
    frames_img = []
    for i in range(frames):
        frames_img.append(
            Image.new("P", size, color=i * 40)
            .convert("RGB")
            .convert("P", palette=Image.Palette.ADAPTIVE, colors=32)
        )
        # tint
        frames_img[-1] = Image.new("RGB", size, (i * 40, 80, 160)).convert(
            "P", palette=Image.Palette.ADAPTIVE, colors=64
        )
    buf = io.BytesIO()
    frames_img[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames_img[1:],
        duration=80,
        loop=0,
        optimize=False,
    )
    return buf.getvalue()


def _svg_bytes() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="100" height="80" viewBox="0 0 100 80">
  <!-- logo comment -->
  <rect x="10" y="10" width="80" height="60" fill="#4f46e5" />
  <text x="50" y="45" text-anchor="middle" fill="white">Hi</text>
</svg>
"""


def test_supported_formats():
    assert set(supported_formats()) == {"jpeg", "png", "gif", "svg"}


def test_detect_format_magic():
    assert detect_format(_jpeg_bytes(), "x.bin") == "jpeg"
    assert detect_format(_png_bytes(), "x.bin") == "png"
    assert detect_format(_gif_bytes(), "x.bin") == "gif"
    assert detect_format(_svg_bytes(), "x.bin") == "svg"


def test_detect_format_rejects_unknown():
    with pytest.raises(CompressError):
        detect_format(b"not-an-image", "file.txt")


def test_compress_jpeg_shrinks():
    raw = _jpeg_bytes(size=(800, 600), quality=98)
    out = compress_image(raw, filename="photo.jpg", quality="balanced")
    assert out["format"] == "jpeg"
    assert out["compressed_bytes"] <= out["original_bytes"]
    assert out["data"][:3] == b"\xff\xd8\xff"
    assert out["width"] == 800
    assert out["height"] == 600
    # Re-open to ensure valid JPEG
    with Image.open(io.BytesIO(out["data"])) as im:
        assert im.format == "JPEG"
        assert im.size == (800, 600)


def test_compress_jpeg_strong_resize():
    raw = _jpeg_bytes(size=(3000, 2000), quality=95)
    out = compress_image(raw, filename="big.jpg", quality="strong")
    assert out["format"] == "jpeg"
    assert max(out["width"], out["height"]) <= 2560
    assert out["compressed_bytes"] < out["original_bytes"]


def test_compress_png_and_keep_readable():
    raw = _png_bytes(size=(200, 150), mode="RGB")
    out = compress_image(raw, filename="shot.png", quality="balanced")
    assert out["format"] == "png"
    assert out["data"][:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(io.BytesIO(out["data"])) as im:
        assert im.format == "PNG"


def test_compress_png_alpha():
    raw = _png_bytes(size=(80, 60), mode="RGBA")
    out = compress_image(raw, filename="a.png", quality="high")
    assert out["format"] == "png"
    with Image.open(io.BytesIO(out["data"])) as im:
        assert im.size == (80, 60)


def test_compress_gif_static_or_anim():
    raw = _gif_bytes(frames=4)
    out = compress_image(raw, filename="anim.gif", quality="balanced")
    assert out["format"] == "gif"
    assert out["frames"] == 4
    with Image.open(io.BytesIO(out["data"])) as im:
        assert im.format == "GIF"


def test_compress_svg_minify():
    raw = _svg_bytes()
    out = compress_image(raw, filename="icon.svg", quality="balanced", strip_meta=True)
    assert out["format"] == "svg"
    assert out["compressed_bytes"] < out["original_bytes"]
    text = out["data"].decode("utf-8")
    assert "<!--" not in text
    assert "<svg" in text
    assert "rect" in text


def test_compress_empty_raises():
    with pytest.raises(CompressError):
        compress_image(b"", filename="x.jpg")


def test_bad_quality_raises():
    with pytest.raises(CompressError):
        compress_image(_jpeg_bytes(), quality="ultra")


def test_http_page_and_presets():
    from app import app

    client = TestClient(app)
    r = client.get("/tools/image-compress")
    assert r.status_code == 200
    assert "图片压缩" in r.text
    assert "JPEG" in r.text

    p = client.get("/tools/image-compress/presets")
    assert p.status_code == 200
    body = p.json()
    assert "jpeg" in body["formats"]
    assert any(q["id"] == "balanced" for q in body["qualities"])


def test_http_compress_download():
    from app import app

    client = TestClient(app)
    raw = _jpeg_bytes(size=(320, 240), quality=96)
    r = client.post(
        "/tools/image-compress/compress",
        files={"file": ("demo.jpg", raw, "image/jpeg")},
        data={"quality": "balanced", "strip_meta": "true"},
    )
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("image/jpeg")
    assert int(r.headers.get("X-Original-Bytes", "0")) == len(raw)
    assert int(r.headers.get("X-Compressed-Bytes", "0")) > 0
    assert r.content[:3] == b"\xff\xd8\xff"
    assert "compressed" in (r.headers.get("content-disposition") or "").lower()


def test_http_compress_info_json():
    from app import app

    client = TestClient(app)
    raw = _png_bytes(size=(100, 80), mode="RGB")
    r = client.post(
        "/tools/image-compress/compress-info",
        files={"file": ("a.png", raw, "image/png")},
        data={"quality": "high"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["format"] == "png"
    assert "data" not in body
    assert body["original_bytes"] == len(raw)
    assert body["compressed_bytes"] > 0


def test_http_reject_bad_format():
    from app import app

    client = TestClient(app)
    r = client.post(
        "/tools/image-compress/compress",
        files={"file": ("x.txt", b"hello world", "text/plain")},
        data={"quality": "balanced"},
    )
    assert r.status_code == 400


def test_registry_lists_image_compress():
    from tools import TOOL_REGISTRY

    slugs = {t["slug"] for t in TOOL_REGISTRY}
    assert "image-compress" in slugs
    tool = next(t for t in TOOL_REGISTRY if t["slug"] == "image-compress")
    assert tool["category"] == "office"
    assert tool["route"] == "/tools/image-compress"


def test_office_category_includes_tool():
    from app import app

    client = TestClient(app)
    r = client.get("/c/office")
    assert r.status_code == 200
    assert "图片压缩" in r.text
    assert "/tools/image-compress" in r.text
