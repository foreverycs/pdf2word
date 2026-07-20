"""Image format conversion (JPEG / PNG / WebP / GIF / BMP / TIFF / ICO).

Converts between common raster formats with sensible defaults for alpha,
animation, and quality. Designed to mirror the style of ``image_compress``.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageChops, ImageOps, ImageSequence

# ---------------------------------------------------------------------------
# Public constants / errors
# ---------------------------------------------------------------------------

# Formats we accept as *input* (magic / extension).
INPUT_FORMATS = ("jpeg", "png", "gif", "webp", "bmp", "tiff", "ico")

# Formats we can *emit*.
OUTPUT_FORMATS = ("jpeg", "png", "webp", "gif", "bmp", "tiff", "ico")

_MEDIA_TYPES = {
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
    "ico": "image/x-icon",
}

_EXTENSIONS = {
    "jpeg": ".jpg",
    "png": ".png",
    "gif": ".gif",
    "webp": ".webp",
    "bmp": ".bmp",
    "tiff": ".tiff",
    "ico": ".ico",
}

# Pillow ``format`` kwarg / reported format names.
_PIL_SAVE = {
    "jpeg": "JPEG",
    "png": "PNG",
    "gif": "GIF",
    "webp": "WEBP",
    "bmp": "BMP",
    "tiff": "TIFF",
    "ico": "ICO",
}


class ConvertError(ValueError):
    """Raised when input cannot be converted (bad format / corrupt data)."""


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

_SVG_RE = re.compile(
    rb"^\s*(?:<\?xml\b[^>]*>\s*)?(?:<!--.*?-->\s*)*<svg\b",
    re.IGNORECASE | re.DOTALL,
)


def input_formats() -> List[str]:
    return list(INPUT_FORMATS)


def output_formats() -> List[str]:
    return list(OUTPUT_FORMATS)


def detect_format(data: bytes, filename: Optional[str] = None) -> str:
    """Return one of INPUT_FORMATS or raise ``ConvertError``."""
    if not data:
        raise ConvertError("Empty file")

    name = (filename or "").lower()
    head = data[:32]

    # Magic numbers first.
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    # WebP: RIFF....WEBP
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:2] == b"BM":
        return "bmp"
    # TIFF little/big endian
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    # ICO / CUR
    if head[:4] in (b"\x00\x00\x01\x00", b"\x00\x00\x02\x00"):
        return "ico"

    # Reject SVG early with a clear message (no native rasterizer here).
    if _SVG_RE.match(data[:4096] if len(data) > 4096 else data):
        raise ConvertError(
            "SVG is not supported for format conversion. "
            "Export to PNG/JPEG first, or use a vector editor."
        )

    # Extension fallback.
    if name.endswith((".jpg", ".jpeg", ".jpe", ".jfif")):
        return "jpeg"
    if name.endswith(".png"):
        return "png"
    if name.endswith(".gif"):
        return "gif"
    if name.endswith(".webp"):
        return "webp"
    if name.endswith(".bmp"):
        return "bmp"
    if name.endswith((".tif", ".tiff")):
        return "tiff"
    if name.endswith((".ico", ".cur")):
        return "ico"
    if name.endswith((".svg", ".svgz")):
        raise ConvertError(
            "SVG is not supported for format conversion. "
            "Export to PNG/JPEG first, or use a vector editor."
        )

    raise ConvertError(
        "Unsupported image format. Use JPEG, PNG, GIF, WebP, BMP, TIFF, or ICO."
    )


def _normalize_target(target: str) -> str:
    t = (target or "").strip().lower()
    aliases = {
        "jpg": "jpeg",
        "jpe": "jpeg",
        "jfif": "jpeg",
        "tif": "tiff",
        "icon": "ico",
    }
    t = aliases.get(t, t)
    if t not in OUTPUT_FORMATS:
        raise ConvertError(
            f"Unknown target format: {target!r}. "
            f"Use one of: {', '.join(OUTPUT_FORMATS)}"
        )
    return t


def _parse_bg(color: Optional[str]) -> Tuple[int, int, int]:
    """Parse ``#rgb`` / ``#rrggbb`` / ``rgb(r,g,b)`` / named white/black."""
    raw = (color or "#ffffff").strip()
    low = raw.lower()
    if low in ("white", "#fff", "#ffffff"):
        return (255, 255, 255)
    if low in ("black", "#000", "#000000"):
        return (0, 0, 0)
    if low.startswith("rgb(") and low.endswith(")"):
        parts = low[4:-1].split(",")
        if len(parts) == 3:
            try:
                r, g, b = (int(p.strip()) for p in parts)
                return (
                    max(0, min(255, r)),
                    max(0, min(255, g)),
                    max(0, min(255, b)),
                )
            except ValueError:
                pass
    if raw.startswith("#"):
        h = raw[1:]
        if len(h) == 3 and all(c in "0123456789abcdefABCDEF" for c in h):
            return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16))
        if len(h) == 6 and all(c in "0123456789abcdefABCDEF" for c in h):
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    raise ConvertError(
        f"Invalid background color: {color!r}. Use #rrggbb or white/black."
    )


def _apply_orientation(img: Image.Image) -> Image.Image:
    try:
        return ImageOps.exif_transpose(img)
    except Exception:
        return img


def _flatten_alpha(
    img: Image.Image, bg: Tuple[int, int, int]
) -> Image.Image:
    """Composite alpha onto a solid background → RGB."""
    if img.mode in ("RGBA", "LA"):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, bg)
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    if img.mode == "P" and "transparency" in img.info:
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, bg)
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def _has_alpha(img: Image.Image) -> bool:
    if img.mode in ("RGBA", "LA"):
        return True
    if img.mode == "P" and "transparency" in img.info:
        return True
    return False


def _clamp_quality(quality: int) -> int:
    try:
        q = int(quality)
    except (TypeError, ValueError) as exc:
        raise ConvertError("quality must be an integer 1–100") from exc
    if q < 1 or q > 100:
        raise ConvertError("quality must be between 1 and 100")
    return q


def _clamp_tolerance(raw: int) -> int:
    """Tolerance 0–100: how close a pixel must be to the key color to vanish."""
    try:
        t = int(raw)
    except (TypeError, ValueError) as exc:
        raise ConvertError("tolerance must be an integer 0–100") from exc
    if t < 0 or t > 100:
        raise ConvertError("tolerance must be between 0 and 100")
    return t


# Max Euclidean distance in RGB cube (≈ 441.67).
_RGB_MAX_DIST = (3 * 255 * 255) ** 0.5


def _sample_corner_bg(img: Image.Image) -> Tuple[int, int, int]:
    """Estimate solid background from corner / edge samples (icon-friendly)."""
    rgba = img.convert("RGBA")
    w, h = rgba.size
    pts = [
        (0, 0),
        (w - 1, 0),
        (0, h - 1),
        (w - 1, h - 1),
        (w // 2, 0),
        (w // 2, h - 1),
        (0, h // 2),
        (w - 1, h // 2),
    ]
    samples: List[Tuple[int, int, int]] = []
    for x, y in pts:
        px = rgba.getpixel((x, y))
        # Skip already-transparent corner pixels.
        if len(px) >= 4 and px[3] < 16:
            continue
        samples.append((px[0], px[1], px[2]))
    if not samples:
        return (255, 255, 255)
    # Median per channel is more robust than mean for mixed edges.
    rs = sorted(s[0] for s in samples)
    gs = sorted(s[1] for s in samples)
    bs = sorted(s[2] for s in samples)
    mid = len(samples) // 2
    return (rs[mid], gs[mid], bs[mid])


def _rgb_dist(a: Tuple[int, int, int], b: Tuple[int, int, int]) -> float:
    return (
        (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
    ) ** 0.5


def make_background_transparent(
    img: Image.Image,
    *,
    key_color: Optional[Tuple[int, int, int]] = None,
    tolerance: int = 28,
    soft: bool = True,
) -> Tuple[Image.Image, Tuple[int, int, int]]:
    """Remove a solid background → RGBA with transparent backdrop.

    Designed for icons / logos on a flat color (white, black, green screen).
    Pixels near ``key_color`` become transparent; others keep original RGB.

    Uses ``ImageChops`` (C-level) instead of a Python pixel loop so large
    icons stay responsive.

    Parameters
    ----------
    key_color:
        RGB to punch out. ``None`` samples corners automatically.
    tolerance:
        0–100. Higher removes more near-key fringe (JPEG compression noise).
    soft:
        When True, alpha ramps near the threshold (cleaner anti-aliased edges).

    Returns
    -------
    (rgba_image, key_color_used)
    """
    tol = _clamp_tolerance(tolerance)
    rgba = img.convert("RGBA")
    key = key_color if key_color is not None else _sample_corner_bg(rgba)

    rgb = rgba.convert("RGB")
    solid = Image.new("RGB", rgb.size, key)
    # Per-channel abs difference; take max channel as a cheap distance proxy.
    diff = ImageChops.difference(rgb, solid)
    r_ch, g_ch, b_ch = diff.split()
    dist_l = ImageChops.lighter(ImageChops.lighter(r_ch, g_ch), b_ch)

    # Map tolerance 0–100 → 0–255 on max-channel distance.
    # Default 28 ≈ ~71/255, enough for mild JPEG fringe on white.
    hard = max(0, min(255, int(round(255 * tol / 100.0))))
    if soft and hard > 0:
        feather = max(4, int(round(hard * 0.45)))
    else:
        feather = 0

    # Build alpha mask: 0 near key, 255 far from key.
    # point() LUT is fast and avoids Python per-pixel loops.
    lo = max(0, hard - feather)
    hi = min(255, hard + feather)
    lut = [0] * 256
    for i in range(256):
        if i <= lo:
            lut[i] = 0
        elif feather > 0 and i < hi:
            # Soft ramp from 0 → 255 across [lo, hi].
            lut[i] = int(round(255 * (i - lo) / max(1, hi - lo)))
        else:
            lut[i] = 255
    mask = dist_l.point(lut)

    # Combine with any existing alpha (already-transparent stays transparent).
    _, _, _, existing_a = rgba.split()
    final_a = ImageChops.multiply(existing_a, mask)

    r, g, b, _ = rgba.split()
    out = Image.merge("RGBA", (r, g, b, final_a))
    return out, key


# Formats that can store a real alpha channel after background removal.
_ALPHA_TARGETS = frozenset({"png", "webp", "gif", "tiff", "ico"})


@dataclass
class _Result:
    data: bytes
    source_format: str
    target_format: str
    media_type: str
    extension: str
    width: Optional[int] = None
    height: Optional[int] = None
    frames: int = 1
    notes: Tuple[str, ...] = ()


def _load_frames(
    data: bytes,
) -> Tuple[List[Image.Image], List[int], int, str]:
    """Return (frames_rgb_or_rgba, durations_ms, loop, detected_pil_format)."""
    frames: List[Image.Image] = []
    durations: List[int] = []
    loop = 0
    pil_fmt = ""

    with Image.open(io.BytesIO(data)) as im:
        pil_fmt = (im.format or "").upper()
        n = getattr(im, "n_frames", 1) or 1
        loop = int(im.info.get("loop", 0) or 0)

        if n <= 1:
            fr = _apply_orientation(im.copy())
            frames.append(fr)
            durations.append(int(im.info.get("duration", 100) or 100))
        else:
            for frame in ImageSequence.Iterator(im):
                fr = _apply_orientation(frame.copy())
                duration = int(
                    fr.info.get("duration", im.info.get("duration", 100)) or 100
                )
                frames.append(fr)
                durations.append(duration)

    if not frames:
        raise ConvertError("Image has no frames")
    return frames, durations, loop, pil_fmt


def _save_static(
    img: Image.Image,
    target: str,
    *,
    quality: int,
    strip_meta: bool,
    bg: Tuple[int, int, int],
    notes: List[str],
) -> bytes:
    work = img
    save_kwargs: Dict[str, Any] = {"format": _PIL_SAVE[target]}

    if target == "jpeg":
        if _has_alpha(work) or work.mode not in ("RGB", "L"):
            if _has_alpha(work):
                notes.append("alpha_flattened")
            work = _flatten_alpha(work, bg)
        elif work.mode == "L":
            pass  # grayscale JPEG ok
        else:
            work = work.convert("RGB")
        save_kwargs.update(
            {
                "quality": quality,
                "optimize": True,
                "progressive": True,
            }
        )
        if not strip_meta and work.info.get("icc_profile"):
            save_kwargs["icc_profile"] = work.info["icc_profile"]
        else:
            notes.append("metadata_stripped" if strip_meta else "no_icc")

    elif target == "png":
        if work.mode not in ("1", "L", "LA", "P", "RGB", "RGBA"):
            work = work.convert("RGBA" if _has_alpha(work) else "RGB")
        save_kwargs.update({"optimize": True, "compress_level": 9})
        if not strip_meta and work.info.get("icc_profile"):
            save_kwargs["icc_profile"] = work.info["icc_profile"]

    elif target == "webp":
        # Keep alpha when present.
        if work.mode not in ("RGB", "RGBA"):
            work = work.convert("RGBA" if _has_alpha(work) else "RGB")
        save_kwargs.update(
            {
                "quality": quality,
                "method": 4,
            }
        )

    elif target == "gif":
        if work.mode != "P":
            if _has_alpha(work):
                # Best-effort palette quantize (alpha may be reduced).
                rgba = work.convert("RGBA")
                work = rgba.convert(
                    "P", palette=Image.Palette.ADAPTIVE, colors=255
                )
                notes.append("gif_palette")
            else:
                work = work.convert("RGB").convert(
                    "P", palette=Image.Palette.ADAPTIVE, colors=256
                )
        save_kwargs.update({"optimize": True})

    elif target == "bmp":
        if _has_alpha(work):
            notes.append("alpha_flattened")
            work = _flatten_alpha(work, bg)
        elif work.mode not in ("RGB", "L", "P", "1"):
            work = work.convert("RGB")

    elif target == "tiff":
        if work.mode not in (
            "1",
            "L",
            "LA",
            "P",
            "RGB",
            "RGBA",
            "CMYK",
        ):
            work = work.convert("RGBA" if _has_alpha(work) else "RGB")
        save_kwargs.update({"compression": "tiff_deflate"})

    elif target == "ico":
        # ICO works best with RGBA; cap very large sources.
        if work.mode not in ("RGB", "RGBA", "P", "L"):
            work = work.convert("RGBA" if _has_alpha(work) else "RGB")
        w, h = work.size
        max_ico = 256
        if max(w, h) > max_ico:
            scale = max_ico / float(max(w, h))
            nw = max(1, int(round(w * scale)))
            nh = max(1, int(round(h * scale)))
            work = work.resize((nw, nh), Image.Resampling.LANCZOS)
            notes.append(f"resized_for_ico_{nw}x{nh}")
        # Provide a couple of common sizes when source is large enough.
        sizes = []
        for s in (16, 32, 48, 64, 128, 256):
            if min(work.size) >= s or s == min(16, min(work.size)):
                sizes.append((s, s))
        if not sizes:
            sizes = [work.size]
        # Deduplicate and keep only sizes <= source.
        uniq = []
        for s in sizes:
            if s[0] <= work.size[0] and s[1] <= work.size[1] and s not in uniq:
                uniq.append(s)
        if not uniq:
            uniq = [work.size]
        save_kwargs["sizes"] = uniq[:6]

    else:
        raise ConvertError(f"Unsupported target: {target}")

    if strip_meta and target == "jpeg":
        # Ensure no EXIF leaked via info.
        pass

    buf = io.BytesIO()
    work.save(buf, **save_kwargs)
    return buf.getvalue()


def _save_animated(
    frames: List[Image.Image],
    durations: List[int],
    loop: int,
    target: str,
    *,
    quality: int,
    bg: Tuple[int, int, int],
    notes: List[str],
) -> bytes:
    """Save multi-frame GIF or WebP. Other targets use first frame only."""
    if target not in ("gif", "webp"):
        notes.append("animation_first_frame_only")
        return _save_static(
            frames[0],
            target,
            quality=quality,
            strip_meta=True,
            bg=bg,
            notes=notes,
        )

    prepared: List[Image.Image] = []
    for fr in frames:
        if target == "webp":
            if fr.mode not in ("RGB", "RGBA"):
                fr = fr.convert("RGBA" if _has_alpha(fr) else "RGB")
            prepared.append(fr)
        else:  # gif
            if fr.mode != "P":
                if _has_alpha(fr):
                    fr = fr.convert("RGBA").convert(
                        "P", palette=Image.Palette.ADAPTIVE, colors=255
                    )
                else:
                    fr = fr.convert("RGB").convert(
                        "P", palette=Image.Palette.ADAPTIVE, colors=256
                    )
            prepared.append(fr)

    buf = io.BytesIO()
    save_kwargs: Dict[str, Any] = {
        "format": _PIL_SAVE[target],
        "save_all": True,
        "append_images": prepared[1:] if len(prepared) > 1 else [],
        "duration": durations if len(durations) > 1 else durations[0],
        "loop": loop,
    }
    if target == "webp":
        save_kwargs["quality"] = quality
        save_kwargs["method"] = 4
    else:
        save_kwargs["optimize"] = True

    prepared[0].save(buf, **save_kwargs)
    notes.append(f"frames_{len(prepared)}")
    return buf.getvalue()


def convert_image(
    data: bytes,
    target_format: str,
    *,
    filename: Optional[str] = None,
    quality: int = 85,
    strip_meta: bool = True,
    background: str = "#ffffff",
    make_transparent: bool = False,
    chroma_key: str = "auto",
    tolerance: int = 28,
    soft_edge: bool = True,
) -> Dict[str, Any]:
    """Convert image bytes to ``target_format``.

    Parameters
    ----------
    data:
        Raw file bytes.
    target_format:
        One of OUTPUT_FORMATS (aliases: jpg→jpeg, tif→tiff).
    filename:
        Optional name (helps format detection / output naming).
    quality:
        1–100 for JPEG / WebP (ignored for lossless targets).
    strip_meta:
        Drop EXIF when saving JPEG (orientation is applied first).
    background:
        Solid color used when flattening alpha (e.g. PNG→JPEG).
    make_transparent:
        Punch out a solid backdrop so only the icon/subject remains
        (exports need an alpha-capable format: PNG / WebP / …).
    chroma_key:
        ``auto`` (sample corners) or a color like ``#ffffff`` / ``white``.
    tolerance:
        0–100; higher removes more near-key fringe (good for JPEG icons).
    soft_edge:
        Soften the cutout edge (recommended for anti-aliased logos).

    Returns
    -------
    dict with keys: data, source_format, target_format, media_type, extension,
    width, height, frames, quality, notes, original_bytes, output_bytes.
    """
    if not data:
        raise ConvertError("Empty file")

    source = detect_format(data, filename)
    target = _normalize_target(target_format)
    q = _clamp_quality(quality)
    bg = _parse_bg(background)
    notes: List[str] = []
    key_used: Optional[str] = None
    tol = _clamp_tolerance(tolerance)

    if make_transparent and target not in _ALPHA_TARGETS:
        # JPEG/BMP cannot store transparency — force PNG so the cutout is kept.
        notes.append(f"target_coerced_{target}_to_png")
        target = "png"

    if source == target and not make_transparent:
        notes.append("same_format_reencoded")

    try:
        frames, durations, loop, _pil_fmt = _load_frames(data)
    except ConvertError:
        raise
    except OSError as exc:
        raise ConvertError(f"Cannot read image: {exc}") from exc
    except Exception as exc:
        raise ConvertError(f"Cannot read image: {exc}") from exc

    if make_transparent:
        key_rgb: Optional[Tuple[int, int, int]]
        ck = (chroma_key or "auto").strip().lower()
        if ck in ("", "auto", "corner", "sample"):
            key_rgb = None
            notes.append("chroma_auto")
        else:
            key_rgb = _parse_bg(chroma_key)
            notes.append("chroma_manual")

        new_frames: List[Image.Image] = []
        used_key: Optional[Tuple[int, int, int]] = None
        for fr in frames:
            cut, used = make_background_transparent(
                fr,
                key_color=key_rgb,
                tolerance=tol,
                soft=bool(soft_edge),
            )
            new_frames.append(cut)
            used_key = used
        frames = new_frames
        if used_key is not None:
            key_used = "#{:02x}{:02x}{:02x}".format(*used_key)
            notes.append(f"key_{key_used}")
        notes.append(f"tolerance_{tol}")
        if soft_edge:
            notes.append("soft_edge")
        notes.append("background_removed")

    width, height = frames[0].size
    multi = len(frames) > 1

    try:
        if multi and target in ("gif", "webp"):
            out = _save_animated(
                frames,
                durations,
                loop,
                target,
                quality=q,
                bg=bg,
                notes=notes,
            )
            frame_count = len(frames)
        elif multi:
            notes.append("animation_first_frame_only")
            out = _save_static(
                frames[0],
                target,
                quality=q,
                strip_meta=strip_meta,
                bg=bg,
                notes=notes,
            )
            frame_count = 1
        else:
            out = _save_static(
                frames[0],
                target,
                quality=q,
                strip_meta=strip_meta,
                bg=bg,
                notes=notes,
            )
            frame_count = 1
    except ConvertError:
        raise
    except OSError as exc:
        raise ConvertError(f"Conversion failed: {exc}") from exc
    except Exception as exc:
        raise ConvertError(f"Conversion failed: {exc}") from exc

    if strip_meta and "metadata_stripped" not in notes and target == "jpeg":
        notes.append("metadata_stripped")

    # Re-read dimensions after possible ICO resize.
    try:
        with Image.open(io.BytesIO(out)) as check:
            width, height = check.size
    except Exception:
        pass

    return {
        "data": out,
        "source_format": source,
        "target_format": target,
        "format": target,  # alias for symmetry with compress tool
        "media_type": _MEDIA_TYPES[target],
        "extension": _EXTENSIONS[target],
        "width": width,
        "height": height,
        "frames": frame_count,
        "quality": q,
        "strip_meta": bool(strip_meta),
        "background": background if isinstance(background, str) else "#ffffff",
        "make_transparent": bool(make_transparent),
        "chroma_key": key_used or (chroma_key if make_transparent else None),
        "tolerance": tol if make_transparent else None,
        "soft_edge": bool(soft_edge) if make_transparent else None,
        "notes": list(notes),
        "original_bytes": len(data),
        "output_bytes": len(out),
    }
