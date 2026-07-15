"""Embedded image extraction and page-region rasterisation."""

from __future__ import annotations

import io
from typing import List, Optional

from .constants import (
    IMAGE_NATIVE_MIN_PX_PER_PT,
    IMAGE_RENDER_DPI,
    IMAGE_RENDER_MAX_PX,
    MAX_IMAGES_PER_PAGE,
    MIN_IMAGE_AREA,
)
from .models import ImageBlock

def _bbox_overlap_ratio(a, b) -> float:
    """Intersection area of ``a`` over area of ``a`` (both x0,top,x1,bottom)."""
    ax0, atop, ax1, abottom = a
    bx0, btop, bx1, bbottom = b
    ix0, ix1 = max(ax0, bx0), min(ax1, bx1)
    it0, it1 = max(atop, btop), min(abottom, bbottom)
    if ix1 <= ix0 or it1 <= it0:
        return 0.0
    inter = (ix1 - ix0) * (it1 - it0)
    area = max((ax1 - ax0) * (abottom - atop), 1e-6)
    return inter / area


def _clamp_render_dpi(width_pt: float, height_pt: float, base_dpi: int) -> int:
    """Lower DPI when the region would exceed IMAGE_RENDER_MAX_PX on a side."""
    dpi = max(72, int(base_dpi))
    w_px = width_pt * dpi / 72.0
    h_px = height_pt * dpi / 72.0
    long_edge = max(w_px, h_px, 1.0)
    if long_edge <= IMAGE_RENDER_MAX_PX:
        return dpi
    scale = IMAGE_RENDER_MAX_PX / long_edge
    return max(96, int(dpi * scale))


def _pil_to_png_bytes(pil) -> Optional[bytes]:
    try:
        if pil.mode not in ("RGB", "RGBA", "L", "LA", "P"):
            pil = pil.convert("RGB")
        elif pil.mode == "P":
            pil = pil.convert("RGBA" if "transparency" in pil.info else "RGB")
        buf = io.BytesIO()
        # optimize=True on huge images is slow and rarely shrinks photos much.
        pil.save(buf, format="PNG", optimize=False, compress_level=6)
        return buf.getvalue()
    except Exception:
        return None


def _pdf_name_str(value) -> str:
    if value is None:
        return ""
    text = str(value)
    if text.startswith("/"):
        text = text[1:]
    # pdfminer may yield "PSLiteral(DeviceRGB)"-like forms
    if "Device" in text or "ICC" in text or "Indexed" in text or "Cal" in text:
        for token in (
            "DeviceRGB", "DeviceGray", "DeviceCMYK", "ICCBased",
            "Indexed", "CalRGB", "CalGray",
        ):
            if token in text:
                return token
    return text.strip()


def _colorspace_kind(colorspace) -> str:
    """Map pdfplumber / pdfminer colorspace to a coarse kind."""
    if colorspace is None:
        return ""
    if isinstance(colorspace, (list, tuple)) and colorspace:
        return _colorspace_kind(colorspace[0])
    name = _pdf_name_str(colorspace).lower()
    if "cmyk" in name:
        return "cmyk"
    if "gray" in name or "grey" in name:
        return "gray"
    if "rgb" in name or "icc" in name or "calrgb" in name:
        return "rgb"
    if "index" in name:
        return "indexed"
    return name


def _stream_filter_names(stream) -> List[str]:
    attrs = getattr(stream, "attrs", None) or {}
    filt = attrs.get("Filter")
    if filt is None:
        return []
    if isinstance(filt, (list, tuple)):
        return [_pdf_name_str(f) for f in filt]
    return [_pdf_name_str(filt)]


def _embedded_stream_png(img: dict) -> Optional[bytes]:
    """Decode the PDF image XObject to PNG without re-rasterising the page.

    Prefer native streams (JPEG / raw RGB / Gray / CMYK) so Word keeps the
    source pixel resolution. Returns None when the stream is unsupported
    (masks, exotic filters, JBIG2, JPX without codec, etc.).
    """
    stream = img.get("stream")
    if stream is None or not hasattr(stream, "get_data"):
        return None
    try:
        data = stream.get_data()
    except Exception:
        return None
    if not data:
        return None

    filters = [f.lower() for f in _stream_filter_names(stream)]
    # Encoded image formats that Pillow can open directly after decode.
    if data[:3] == b"\xff\xd8\xff" or any("dct" in f for f in filters):
        try:
            from PIL import Image

            pil = Image.open(io.BytesIO(data))
            pil.load()
            return _pil_to_png_bytes(pil)
        except Exception:
            pass
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return bytes(data)
    if data[:2] == b"BM":
        try:
            from PIL import Image

            pil = Image.open(io.BytesIO(data))
            pil.load()
            return _pil_to_png_bytes(pil)
        except Exception:
            pass

    # Raw samples (typically FlateDecode → uncompressed pixels).
    src = img.get("srcsize") or (0, 0)
    try:
        width, height = int(src[0]), int(src[1])
    except (TypeError, ValueError, IndexError):
        attrs = getattr(stream, "attrs", None) or {}
        try:
            width = int(attrs.get("Width") or 0)
            height = int(attrs.get("Height") or 0)
        except (TypeError, ValueError):
            width = height = 0
    if width < 2 or height < 2:
        return None

    bits = int(img.get("bits") or (getattr(stream, "attrs", None) or {}).get("BitsPerComponent") or 8)
    if bits != 8:
        return None
    if img.get("imagemask"):
        return None

    kind = _colorspace_kind(img.get("colorspace"))
    attrs = getattr(stream, "attrs", None) or {}
    if not kind:
        kind = _colorspace_kind(attrs.get("ColorSpace"))

    try:
        from PIL import Image
    except Exception:
        return None

    expected = {
        "rgb": width * height * 3,
        "gray": width * height,
        "cmyk": width * height * 4,
    }
    try:
        if kind == "rgb" and len(data) >= expected["rgb"]:
            pil = Image.frombytes("RGB", (width, height), data[: expected["rgb"]])
        elif kind == "gray" and len(data) >= expected["gray"]:
            pil = Image.frombytes("L", (width, height), data[: expected["gray"]])
        elif kind == "cmyk" and len(data) >= expected["cmyk"]:
            pil = Image.frombytes("CMYK", (width, height), data[: expected["cmyk"]])
            pil = pil.convert("RGB")
        else:
            # Last resort: try Pillow on the raw buffer (may work for some filters).
            try:
                pil = Image.open(io.BytesIO(data))
                pil.load()
            except Exception:
                return None
        return _pil_to_png_bytes(pil)
    except Exception:
        return None


def _native_image_is_sharp_enough(img: dict, width_pt: float, height_pt: float) -> bool:
    """True when embedded pixel size is dense enough for the on-page box.

    Also accept the stream when it already has at least as many pixels as a
    default high-DPI re-render would produce (avoids needlessly re-rasterising
    large photos that are slightly under the px/pt threshold on one axis).
    """
    src = img.get("srcsize")
    if not src:
        return False
    try:
        pw, ph = float(src[0]), float(src[1])
    except (TypeError, ValueError, IndexError):
        return False
    if width_pt <= 1 or height_pt <= 1 or pw < 2 or ph < 2:
        return False
    if (
        pw / width_pt >= IMAGE_NATIVE_MIN_PX_PER_PT
        and ph / height_pt >= IMAGE_NATIVE_MIN_PX_PER_PT
    ):
        return True
    # Compare to what IMAGE_RENDER_DPI would yield for this box.
    dpi = _clamp_render_dpi(width_pt, height_pt, IMAGE_RENDER_DPI)
    target_w = width_pt * dpi / 72.0
    target_h = height_pt * dpi / 72.0
    return pw >= target_w * 0.9 and ph >= target_h * 0.9


def _render_region_png(
    page,
    bbox,
    resolution: Optional[int] = None,
) -> Optional[bytes]:
    """Rasterise a page region to PNG bytes. Returns None on failure."""
    try:
        x0, top, x1, bottom = bbox
        width_pt = max(float(x1) - float(x0), 1.0)
        height_pt = max(float(bottom) - float(top), 1.0)
        base = IMAGE_RENDER_DPI if resolution is None else int(resolution)
        dpi = _clamp_render_dpi(width_pt, height_pt, base)
        cropped = page.crop(bbox, strict=False)
        pil = cropped.to_image(resolution=dpi).original
        if pil is None:
            return None
        # Drop nearly-blank crops (e.g. failed extract of vector-only art).
        extrema = pil.convert("L").getextrema()
        if extrema is not None and extrema[0] == extrema[1]:
            return None
        return _pil_to_png_bytes(pil)
    except Exception:
        return None


def _image_h_align(x0: float, width: float, page_w: float) -> str:
    """Infer horizontal placement of an image relative to the page content box."""
    if page_w <= 0 or width <= 0:
        return "left"
    # Near full width → treat as centered full-bleed content.
    if width / page_w >= 0.85:
        return "center"
    left_pad = max(x0, 0.0)
    right_pad = max(page_w - (x0 + width), 0.0)
    # Balanced side margins → centre; otherwise keep flush to the denser side.
    if abs(left_pad - right_pad) <= max(page_w * 0.08, 12.0):
        return "center"
    if left_pad > right_pad * 2.0 and left_pad / page_w > 0.2:
        return "right"
    return "left"


def _extract_images(page, table_bboxes) -> List[ImageBlock]:
    """Pull embedded image regions that sit outside tables.

    Prefer the native PDF image stream (full source resolution). Only fall
    back to page-region rasterisation when the stream cannot be decoded or is
    too low-density for the on-page display size.
    """
    raw = getattr(page, "images", None) or []
    if not raw:
        return []

    page_w = float(getattr(page, "width", 0) or 0)
    page_h = float(getattr(page, "height", 0) or 0)
    page_area = max(page_w * page_h, 1.0)
    blocks: List[ImageBlock] = []

    # Sort top-to-bottom, left-to-right for stable ordering.
    ordered_imgs = sorted(
        raw,
        key=lambda im: (round(im.get("top", 0), 1), round(im.get("x0", 0), 1)),
    )
    for img in ordered_imgs:
        if len(blocks) >= MAX_IMAGES_PER_PAGE:
            break
        try:
            x0 = float(img["x0"])
            top = float(img["top"])
            x1 = float(img["x1"])
            bottom = float(img["bottom"])
        except (KeyError, TypeError, ValueError):
            continue
        w, h = x1 - x0, bottom - top
        if w <= 1 or h <= 1 or w * h < MIN_IMAGE_AREA:
            continue
        # Skip near-full-page images here; empty-page fallback handles scans.
        if page_area > 0 and (w * h) / page_area > 0.85:
            continue
        bbox = (x0, top, x1, bottom)
        if any(_bbox_overlap_ratio(bbox, tb) > 0.5 for tb in table_bboxes):
            continue

        png: Optional[bytes] = None
        native = _embedded_stream_png(img)
        if native and _native_image_is_sharp_enough(img, w, h):
            png = native
        if not png:
            # High-DPI region render (or soft native stream as last resort).
            png = _render_region_png(page, bbox)
        if not png and native:
            png = native
        if not png:
            continue
        blocks.append(ImageBlock(
            image_bytes=png,
            top=top,
            bottom=bottom,
            x0=x0,
            width_pt=w,
            height_pt=h,
            page_width=page_w,
            align=_image_h_align(x0, w, page_w),
        ))
    return blocks


def _render_full_page_image(page) -> Optional[ImageBlock]:
    """Fallback for scanned / image-only pages: embed a full-page raster."""
    try:
        w = float(getattr(page, "width", 0) or 0)
        h = float(getattr(page, "height", 0) or 0)
        if w <= 0 or h <= 0:
            return None
        # Prefer the largest embedded full-page stream when present (true scan DPI).
        for img in getattr(page, "images", None) or []:
            try:
                ix0, itop = float(img["x0"]), float(img["top"])
                ix1, ibot = float(img["x1"]), float(img["bottom"])
            except (KeyError, TypeError, ValueError):
                continue
            iw, ih = ix1 - ix0, ibot - itop
            if iw * ih / max(w * h, 1.0) < 0.85:
                continue
            native = _embedded_stream_png(img)
            if native and _native_image_is_sharp_enough(img, iw, ih):
                return ImageBlock(
                    image_bytes=native,
                    top=0.0,
                    bottom=h,
                    x0=0.0,
                    width_pt=w,
                    height_pt=h,
                    page_width=w,
                    align="center",
                )
        png = _render_region_png(page, (0, 0, w, h), resolution=IMAGE_RENDER_DPI)
        if not png:
            return None
        return ImageBlock(
            image_bytes=png,
            top=0.0,
            bottom=h,
            x0=0.0,
            width_pt=w,
            height_pt=h,
            page_width=w,
            align="center",
        )
    except Exception:
        return None


__all__ = [
    "_bbox_overlap_ratio",
    "_clamp_render_dpi",
    "_pil_to_png_bytes",
    "_pdf_name_str",
    "_colorspace_kind",
    "_stream_filter_names",
    "_embedded_stream_png",
    "_native_image_is_sharp_enough",
    "_render_region_png",
    "_image_h_align",
    "_extract_images",
    "_render_full_page_image",
]
