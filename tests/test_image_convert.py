"""Tests for image format conversion (core + HTTP)."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from media.image_convert import (
    ConvertError,
    convert_image,
    detect_format,
    input_formats,
    output_formats,
)


def _jpeg_bytes(size=(120, 80), color=(40, 120, 200), quality=92) -> bytes:
    img = Image.new("RGB", size, color)
    for x in range(0, size[0], 11):
        img.putpixel((x, size[1] // 2), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _png_bytes(size=(80, 60), *, alpha: bool = True) -> bytes:
    if alpha:
        img = Image.new("RGBA", size, (255, 0, 0, 128))
        for i in range(size[0]):
            img.putpixel((i, size[1] // 2), (0, 255, 0, 255))
    else:
        img = Image.new("RGB", size, (10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _gif_bytes(size=(40, 30), frames=3) -> bytes:
    frames_img = []
    for i in range(frames):
        frames_img.append(
            Image.new("RGB", size, (i * 40, 80, 160)).convert(
                "P", palette=Image.Palette.ADAPTIVE, colors=64
            )
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


def _webp_bytes(size=(60, 40)) -> bytes:
    img = Image.new("RGB", size, (20, 40, 60))
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=80)
    return buf.getvalue()


def _bmp_bytes(size=(50, 40)) -> bytes:
    img = Image.new("RGB", size, (100, 50, 0))
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


def test_format_lists():
    assert "jpeg" in input_formats()
    assert "webp" in output_formats()
    assert set(input_formats()) == set(output_formats())


def test_detect_format_magic():
    assert detect_format(_jpeg_bytes(), "x.bin") == "jpeg"
    assert detect_format(_png_bytes(), "x.bin") == "png"
    assert detect_format(_gif_bytes(), "x.bin") == "gif"
    assert detect_format(_webp_bytes(), "x.bin") == "webp"
    assert detect_format(_bmp_bytes(), "x.bin") == "bmp"


def test_detect_format_rejects_unknown():
    with pytest.raises(ConvertError):
        detect_format(b"not-an-image", "file.txt")


def test_detect_format_rejects_svg():
    svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'
    with pytest.raises(ConvertError, match="SVG"):
        detect_format(svg, "a.svg")


def test_png_to_jpeg_flattens_alpha():
    raw = _png_bytes(alpha=True)
    out = convert_image(raw, "jpeg", filename="a.png", quality=85)
    assert out["source_format"] == "png"
    assert out["target_format"] == "jpeg"
    assert out["extension"] == ".jpg"
    assert out["data"][:3] == b"\xff\xd8\xff"
    assert "alpha_flattened" in out["notes"]
    with Image.open(io.BytesIO(out["data"])) as im:
        assert im.format == "JPEG"
        assert im.size == (80, 60)


def test_jpeg_to_png():
    raw = _jpeg_bytes()
    out = convert_image(raw, "png", filename="photo.jpg")
    assert out["target_format"] == "png"
    assert out["data"][:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(io.BytesIO(out["data"])) as im:
        assert im.format == "PNG"


def test_png_to_webp():
    raw = _png_bytes(alpha=True)
    out = convert_image(raw, "webp", filename="a.png", quality=80)
    assert out["target_format"] == "webp"
    assert out["extension"] == ".webp"
    with Image.open(io.BytesIO(out["data"])) as im:
        assert im.format == "WEBP"


def test_jpg_alias():
    raw = _png_bytes(alpha=False)
    out = convert_image(raw, "jpg", filename="a.png")
    assert out["target_format"] == "jpeg"


def test_gif_to_png_first_frame():
    raw = _gif_bytes(frames=3)
    out = convert_image(raw, "png", filename="anim.gif")
    assert out["target_format"] == "png"
    assert out["frames"] == 1
    assert "animation_first_frame_only" in out["notes"]


def test_gif_to_webp_keeps_frames():
    raw = _gif_bytes(frames=3)
    out = convert_image(raw, "webp", filename="anim.gif", quality=75)
    assert out["target_format"] == "webp"
    assert out["frames"] == 3
    with Image.open(io.BytesIO(out["data"])) as im:
        assert getattr(im, "n_frames", 1) >= 2


def test_to_ico_resizes_large():
    raw = _png_bytes(size=(400, 300), alpha=False)
    out = convert_image(raw, "ico", filename="big.png")
    assert out["target_format"] == "ico"
    assert out["extension"] == ".ico"
    assert any("resized_for_ico" in n for n in out["notes"])
    with Image.open(io.BytesIO(out["data"])) as im:
        assert max(im.size) <= 256


def test_to_bmp_and_tiff():
    raw = _jpeg_bytes()
    bmp = convert_image(raw, "bmp", filename="a.jpg")
    assert bmp["data"][:2] == b"BM"
    tiff = convert_image(raw, "tiff", filename="a.jpg")
    assert tiff["extension"] == ".tiff"
    with Image.open(io.BytesIO(tiff["data"])) as im:
        assert im.format == "TIFF"


def test_bad_quality_raises():
    with pytest.raises(ConvertError):
        convert_image(_jpeg_bytes(), "png", quality=0)
    with pytest.raises(ConvertError):
        convert_image(_jpeg_bytes(), "png", quality=101)


def test_bad_target_raises():
    with pytest.raises(ConvertError):
        convert_image(_jpeg_bytes(), "avif")


def test_empty_raises():
    with pytest.raises(ConvertError):
        convert_image(b"", "png")


def test_http_page_and_formats():
    from app import app

    client = TestClient(app)
    r = client.get("/tools/image-convert")
    assert r.status_code == 200
    assert "图片格式转换" in r.text
    assert "WebP" in r.text

    p = client.get("/tools/image-convert/formats")
    assert p.status_code == 200
    body = p.json()
    assert "jpeg" in body["input"]
    assert "webp" in body["output"]
    assert body["defaults"]["target_format"] == "png"
    assert any(t["id"] == "png" for t in body["targets"])


def test_http_convert_download():
    from app import app

    client = TestClient(app)
    raw = _png_bytes(alpha=True)
    r = client.post(
        "/tools/image-convert/convert",
        files={"file": ("logo.png", raw, "image/png")},
        data={
            "target_format": "jpeg",
            "quality": "80",
            "strip_meta": "true",
            "background": "#ffffff",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("image/jpeg")
    assert r.headers.get("X-Source-Format") == "png"
    assert r.headers.get("X-Target-Format") == "jpeg"
    assert int(r.headers.get("X-Original-Bytes", "0")) == len(raw)
    assert int(r.headers.get("X-Output-Bytes", "0")) > 0
    assert r.content[:3] == b"\xff\xd8\xff"


def test_http_convert_info_json():
    from app import app

    client = TestClient(app)
    raw = _jpeg_bytes()
    r = client.post(
        "/tools/image-convert/convert-info",
        files={"file": ("a.jpg", raw, "image/jpeg")},
        data={"target_format": "webp", "quality": "70"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["source_format"] == "jpeg"
    assert body["target_format"] == "webp"
    assert "data" not in body
    assert body["original_bytes"] == len(raw)
    assert body["output_bytes"] > 0
    assert body["output_name"].endswith(".webp")


def test_http_reject_bad_format():
    from app import app

    client = TestClient(app)
    r = client.post(
        "/tools/image-convert/convert",
        files={"file": ("x.txt", b"hello world", "text/plain")},
        data={"target_format": "png"},
    )
    assert r.status_code == 400


def test_http_reject_bad_target():
    from app import app

    client = TestClient(app)
    raw = _jpeg_bytes()
    r = client.post(
        "/tools/image-convert/convert",
        files={"file": ("a.jpg", raw, "image/jpeg")},
        data={"target_format": "avif"},
    )
    assert r.status_code == 400


def test_registry_lists_image_convert():
    from tools import TOOL_REGISTRY

    slugs = {t["slug"] for t in TOOL_REGISTRY}
    assert "image-convert" in slugs
    tool = next(t for t in TOOL_REGISTRY if t["slug"] == "image-convert")
    assert tool["category"] == "office"
    assert tool["route"] == "/tools/image-convert"


def test_office_category_includes_tool():
    from app import app

    client = TestClient(app)
    r = client.get("/c/office")
    assert r.status_code == 200
    assert "图片格式转换" in r.text
    assert "/tools/image-convert" in r.text


def _icon_on_white(size=(80, 80)) -> bytes:
    """Solid white canvas with a blue square icon in the center."""
    img = Image.new("RGB", size, (255, 255, 255))
    for x in range(20, 60):
        for y in range(20, 60):
            img.putpixel((x, y), (30, 90, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_make_transparent_removes_white_bg():
    raw = _icon_on_white()
    out = convert_image(
        raw,
        "png",
        filename="icon.png",
        make_transparent=True,
        chroma_key="auto",
        tolerance=20,
        soft_edge=False,
    )
    assert out["make_transparent"] is True
    assert "background_removed" in out["notes"]
    assert out["target_format"] == "png"
    with Image.open(io.BytesIO(out["data"])) as im:
        rgba = im.convert("RGBA")
        # Corner should be fully transparent.
        assert rgba.getpixel((0, 0))[3] == 0
        assert rgba.getpixel((79, 79))[3] == 0
        # Icon center should stay opaque blue-ish.
        center = rgba.getpixel((40, 40))
        assert center[3] > 200
        assert center[2] > 150  # blue channel


def test_make_transparent_manual_key():
    raw = _icon_on_white()
    out = convert_image(
        raw,
        "webp",
        filename="icon.png",
        make_transparent=True,
        chroma_key="#ffffff",
        tolerance=15,
        soft_edge=True,
    )
    assert out["target_format"] == "webp"
    assert "chroma_manual" in out["notes"]
    with Image.open(io.BytesIO(out["data"])) as im:
        rgba = im.convert("RGBA")
        assert rgba.getpixel((1, 1))[3] < 30


def test_make_transparent_coerces_jpeg_to_png():
    raw = _icon_on_white()
    out = convert_image(
        raw,
        "jpeg",
        filename="icon.png",
        make_transparent=True,
        chroma_key="white",
        tolerance=20,
    )
    assert out["target_format"] == "png"
    assert any("coerced" in n for n in out["notes"])
    assert out["data"][:8] == b"\x89PNG\r\n\x1a\n"


def test_http_make_transparent():
    from app import app

    client = TestClient(app)
    raw = _icon_on_white()
    r = client.post(
        "/tools/image-convert/convert",
        files={"file": ("icon.png", raw, "image/png")},
        data={
            "target_format": "png",
            "make_transparent": "true",
            "chroma_key": "auto",
            "tolerance": "25",
            "soft_edge": "true",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("X-Make-Transparent") == "1"
    assert r.headers.get("X-Target-Format") == "png"
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.convert("RGBA").getpixel((0, 0))[3] == 0


def test_page_has_cutout_option():
    from app import app

    client = TestClient(app)
    r = client.get("/tools/image-convert")
    assert r.status_code == 200
    assert "makeTransparent" in r.text
    assert "去底色" in r.text


def _icon_on_white(size=(80, 80)) -> bytes:
    """Solid white canvas with a blue square icon in the center."""
    img = Image.new("RGB", size, (255, 255, 255))
    for x in range(20, 60):
        for y in range(20, 60):
            img.putpixel((x, y), (30, 90, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_make_transparent_removes_white_bg():
    raw = _icon_on_white()
    out = convert_image(
        raw,
        "png",
        filename="icon.png",
        make_transparent=True,
        chroma_key="auto",
        tolerance=20,
        soft_edge=False,
    )
    assert out["make_transparent"] is True
    assert "background_removed" in out["notes"]
    assert out["target_format"] == "png"
    with Image.open(io.BytesIO(out["data"])) as im:
        rgba = im.convert("RGBA")
        # Corner should be fully transparent.
        assert rgba.getpixel((0, 0))[3] == 0
        assert rgba.getpixel((79, 79))[3] == 0
        # Icon center should stay opaque blue-ish.
        center = rgba.getpixel((40, 40))
        assert center[3] > 200
        assert center[2] > 150  # blue channel


def test_make_transparent_manual_key():
    raw = _icon_on_white()
    out = convert_image(
        raw,
        "webp",
        filename="icon.png",
        make_transparent=True,
        chroma_key="#ffffff",
        tolerance=15,
        soft_edge=True,
    )
    assert out["target_format"] == "webp"
    assert "chroma_manual" in out["notes"]
    with Image.open(io.BytesIO(out["data"])) as im:
        rgba = im.convert("RGBA")
        assert rgba.getpixel((1, 1))[3] < 30


def test_make_transparent_coerces_jpeg_to_png():
    raw = _icon_on_white()
    out = convert_image(
        raw,
        "jpeg",
        filename="icon.png",
        make_transparent=True,
        chroma_key="white",
        tolerance=20,
    )
    assert out["target_format"] == "png"
    assert any("coerced" in n for n in out["notes"])
    assert out["data"][:8] == b"\x89PNG\r\n\x1a\n"


def test_http_make_transparent():
    from app import app

    client = TestClient(app)
    raw = _icon_on_white()
    r = client.post(
        "/tools/image-convert/convert",
        files={"file": ("icon.png", raw, "image/png")},
        data={
            "target_format": "png",
            "make_transparent": "true",
            "chroma_key": "auto",
            "tolerance": "25",
            "soft_edge": "true",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("X-Make-Transparent") == "1"
    assert r.headers.get("X-Target-Format") == "png"
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.convert("RGBA").getpixel((0, 0))[3] == 0


def test_page_has_cutout_option():
    from app import app

    client = TestClient(app)
    r = client.get("/tools/image-convert")
    assert r.status_code == 200
    assert "makeTransparent" in r.text
    assert "去底色" in r.text
