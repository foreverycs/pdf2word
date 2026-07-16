"""Near-visually-lossless image compression for JPEG / PNG / GIF / SVG.

Goals
-----
- Significantly shrink file size while keeping perceived quality high.
- Keep the original container format (JPEG stays JPEG, etc.).
- Work with common real-world files (EXIF orientation, alpha, animation).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageOps, ImageSequence

# ---------------------------------------------------------------------------
# Public constants / errors
# ---------------------------------------------------------------------------

SUPPORTED_FORMATS = ("jpeg", "png", "gif", "svg")

# Quality presets → encoder knobs. Tuned for "looks the same, smaller file".
_PRESETS: Dict[str, Dict[str, Any]] = {
    "high": {
        "jpeg_quality": 90,
        "jpeg_subsampling": 0,  # 4:4:4
        "png_quantize": False,
        "png_colors": 256,
        "gif_colors": 256,
        "max_side": 0,
    },
    "balanced": {
        "jpeg_quality": 82,
        "jpeg_subsampling": 2,  # 4:2:0
        "png_quantize": True,  # only when it helps (few colors / photos)
        "png_colors": 256,
        "gif_colors": 192,
        "max_side": 0,
    },
    "strong": {
        "jpeg_quality": 72,
        "jpeg_subsampling": 2,
        "png_quantize": True,
        "png_colors": 192,
        "gif_colors": 128,
        "max_side": 2560,
    },
}


class CompressError(ValueError):
    """Raised when input cannot be compressed (bad format / corrupt data)."""


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

_SVG_RE = re.compile(
    rb"^\s*(?:<\?xml\b[^>]*>\s*)?(?:<!--.*?-->\s*)*<svg\b",
    re.IGNORECASE | re.DOTALL,
)


def supported_formats() -> List[str]:
    return list(SUPPORTED_FORMATS)


def detect_format(data: bytes, filename: Optional[str] = None) -> str:
    """Return one of ``jpeg|png|gif|svg`` or raise ``CompressError``."""
    if not data:
        raise CompressError("Empty file")

    name = (filename or "").lower()
    head = data[:32]

    # Magic numbers first (more reliable than extension).
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if _SVG_RE.match(data[:4096] if len(data) > 4096 else data):
        return "svg"

    # Extension fallback for edge-case SVG without standard header.
    if name.endswith((".jpg", ".jpeg", ".jpe", ".jfif")):
        return "jpeg"
    if name.endswith(".png"):
        return "png"
    if name.endswith(".gif"):
        return "gif"
    if name.endswith(".svg") or name.endswith(".svgz"):
        # svgz is gzip-compressed SVG — not supported as text minify target.
        if name.endswith(".svgz") or data[:2] == b"\x1f\x8b":
            raise CompressError("Compressed SVG (.svgz) is not supported")
        return "svg"

    raise CompressError(
        "Unsupported image format. Use JPEG, PNG, GIF, or SVG."
    )


def _preset(quality: str) -> Dict[str, Any]:
    key = (quality or "balanced").strip().lower()
    if key not in _PRESETS:
        raise CompressError(
            f"Unknown quality preset: {quality!r}. "
            f"Use one of: {', '.join(_PRESETS)}"
        )
    return _PRESETS[key]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _apply_orientation(img: Image.Image) -> Image.Image:
    """Bake EXIF orientation into pixels so stripping metadata is safe."""
    try:
        return ImageOps.exif_transpose(img)
    except Exception:
        return img


def _maybe_resize(img: Image.Image, max_side: int) -> Image.Image:
    if not max_side or max_side <= 0:
        return img
    w, h = img.size
    longest = max(w, h)
    if longest <= max_side:
        return img
    scale = max_side / float(longest)
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    return img.resize((nw, nh), Image.Resampling.LANCZOS)


def _size_stats(original: int, compressed: int) -> Dict[str, Any]:
    saved = max(0, original - compressed)
    ratio = (1.0 - compressed / original) if original > 0 else 0.0
    return {
        "original_bytes": original,
        "compressed_bytes": compressed,
        "saved_bytes": saved,
        "ratio": round(ratio, 4),  # 0.35 = 35% smaller
        "percent_saved": round(ratio * 100, 1),
    }


@dataclass
class _Result:
    data: bytes
    fmt: str
    media_type: str
    extension: str
    width: Optional[int] = None
    height: Optional[int] = None
    frames: int = 1
    notes: Tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# JPEG
# ---------------------------------------------------------------------------


def _compress_jpeg(
    data: bytes, cfg: Dict[str, Any], *, strip_meta: bool
) -> _Result:
    notes: List[str] = []
    with Image.open(io.BytesIO(data)) as im:
        im = _apply_orientation(im)
        im = _maybe_resize(im, int(cfg["max_side"] or 0))
        if im.mode in ("RGBA", "LA") or (
            im.mode == "P" and "transparency" in im.info
        ):
            # JPEG has no alpha — composite on white to avoid black matte.
            rgba = im.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            im = background
            notes.append("alpha_flattened")
        elif im.mode != "RGB":
            im = im.convert("RGB")

        width, height = im.size
        buf = io.BytesIO()
        save_kwargs: Dict[str, Any] = {
            "format": "JPEG",
            "quality": int(cfg["jpeg_quality"]),
            "optimize": True,
            "progressive": True,
            "subsampling": int(cfg["jpeg_subsampling"]),
        }
        # Keep ICC when present and not stripping hard — helps color accuracy.
        icc = im.info.get("icc_profile") if not strip_meta else None
        if icc:
            save_kwargs["icc_profile"] = icc
        im.save(buf, **save_kwargs)
        out = buf.getvalue()

    # Never return a larger file when original is already efficient.
    if len(out) >= len(data) and data[:3] == b"\xff\xd8\xff":
        notes.append("kept_original")
        return _Result(
            data=data,
            fmt="jpeg",
            media_type="image/jpeg",
            extension=".jpg",
            width=width,
            height=height,
            notes=tuple(notes),
        )

    if strip_meta:
        notes.append("metadata_stripped")
    notes.append(f"quality_{cfg['jpeg_quality']}")
    return _Result(
        data=out,
        fmt="jpeg",
        media_type="image/jpeg",
        extension=".jpg",
        width=width,
        height=height,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# PNG
# ---------------------------------------------------------------------------


def _png_unique_colors(img: Image.Image, sample_limit: int = 257) -> int:
    """Count unique colors up to ``sample_limit`` (cheap early-exit)."""
    # Convert to a countable mode.
    if img.mode not in ("RGB", "RGBA", "L", "P"):
        work = img.convert("RGBA")
    else:
        work = img
    try:
        colors = work.getcolors(maxcolors=sample_limit)
    except Exception:
        return sample_limit
    if colors is None:
        return sample_limit
    return len(colors)


def _compress_png(
    data: bytes, cfg: Dict[str, Any], *, strip_meta: bool
) -> _Result:
    notes: List[str] = []
    with Image.open(io.BytesIO(data)) as im:
        im = _apply_orientation(im)
        im = _maybe_resize(im, int(cfg["max_side"] or 0))
        width, height = im.size

        candidates: List[bytes] = []

        # Candidate A: lossless re-encode (optimize + max zlib).
        buf_a = io.BytesIO()
        save_a = im.copy()
        # Drop useless high-bit modes.
        if save_a.mode not in (
            "1",
            "L",
            "LA",
            "P",
            "RGB",
            "RGBA",
        ):
            save_a = save_a.convert("RGBA" if "A" in save_a.getbands() else "RGB")
        save_kwargs: Dict[str, Any] = {
            "format": "PNG",
            "optimize": True,
            "compress_level": 9,
        }
        if not strip_meta and save_a.info.get("icc_profile"):
            save_kwargs["icc_profile"] = save_a.info["icc_profile"]
        save_a.save(buf_a, **save_kwargs)
        candidates.append(buf_a.getvalue())
        notes.append("lossless_optimize")

        # Candidate B: palette quantize when it won't hurt much.
        if cfg.get("png_quantize"):
            ncolors = _png_unique_colors(im)
            # Photos (many colors): light quantize still often wins on size.
            # Icons / UI (few colors): palette is ideal.
            try:
                has_alpha = (
                    im.mode in ("RGBA", "LA")
                    or (im.mode == "P" and "transparency" in im.info)
                )
                src = im.convert("RGBA") if has_alpha else im.convert("RGB")
                # Pillow quantize: method=2 = libimagequant if available, else 0.
                method = 2
                try:
                    q = src.quantize(
                        colors=int(cfg["png_colors"]),
                        method=method,
                        dither=Image.Dither.FLOYDSTEINBERG,
                    )
                except Exception:
                    q = src.quantize(
                        colors=int(cfg["png_colors"]),
                        method=0,
                        dither=Image.Dither.FLOYDSTEINBERG,
                    )
                buf_b = io.BytesIO()
                q.save(
                    buf_b,
                    format="PNG",
                    optimize=True,
                    compress_level=9,
                )
                candidates.append(buf_b.getvalue())
                notes.append(
                    f"palette_{cfg['png_colors']}"
                    + (f"_src_colors~{ncolors}" if ncolors < 257 else "")
                )
            except Exception:
                pass

        # Prefer the smallest candidate that is not larger than original.
        candidates.append(data)  # original as last-resort baseline
        best = min(candidates, key=len)
        if best is data or best == data:
            notes.append("kept_original")
        elif len(best) == len(candidates[0]) and best == candidates[0]:
            notes.append("chose_lossless")
        else:
            notes.append("chose_palette")

        if strip_meta:
            notes.append("metadata_stripped")

        return _Result(
            data=best,
            fmt="png",
            media_type="image/png",
            extension=".png",
            width=width,
            height=height,
            notes=tuple(notes),
        )


# ---------------------------------------------------------------------------
# GIF (static + animated)
# ---------------------------------------------------------------------------


def _compress_gif(
    data: bytes, cfg: Dict[str, Any], *, strip_meta: bool
) -> _Result:
    notes: List[str] = []
    del strip_meta  # GIF has little EXIF; palette is the payload.

    with Image.open(io.BytesIO(data)) as im:
        frames_in = getattr(im, "n_frames", 1) or 1
        width, height = im.size
        max_side = int(cfg["max_side"] or 0)
        target_colors = int(cfg["gif_colors"])

        frames: List[Image.Image] = []
        durations: List[int] = []
        disposals: List[int] = []
        loop = im.info.get("loop", 0)

        for frame in ImageSequence.Iterator(im):
            fr = frame.copy()
            duration = int(fr.info.get("duration", im.info.get("duration", 100)) or 100)
            disposal = int(fr.info.get("disposal", im.info.get("disposal", 0)) or 0)
            durations.append(duration)
            disposals.append(disposal)

            fr = _apply_orientation(fr)
            fr = _maybe_resize(fr, max_side)

            # Normalize to palette for GIF.
            if fr.mode != "P":
                # Preserve transparency when present.
                if fr.mode in ("RGBA", "LA"):
                    fr = fr.convert("RGBA")
                    alpha = fr.split()[-1]
                    # Composite semi-transparent on white then restore mask via quantize.
                    bg = Image.new("RGB", fr.size, (255, 255, 255))
                    bg.paste(fr, mask=alpha)
                    fr = bg.convert("P", palette=Image.Palette.ADAPTIVE, colors=target_colors)
                else:
                    fr = fr.convert(
                        "P",
                        palette=Image.Palette.ADAPTIVE,
                        colors=target_colors,
                    )
            else:
                # Re-quantize existing palette if stronger preset.
                if target_colors < 256:
                    rgb = fr.convert("RGB")
                    fr = rgb.convert(
                        "P",
                        palette=Image.Palette.ADAPTIVE,
                        colors=target_colors,
                    )
            frames.append(fr)

        if not frames:
            raise CompressError("GIF has no frames")

        buf = io.BytesIO()
        save_kwargs: Dict[str, Any] = {
            "format": "GIF",
            "save_all": True,
            "append_images": frames[1:] if len(frames) > 1 else [],
            "optimize": True,
            "duration": durations if len(durations) > 1 else durations[0],
            "loop": loop,
        }
        # disposal is supported on recent Pillow via disposal= list
        try:
            if any(d for d in disposals):
                save_kwargs["disposal"] = (
                    disposals if len(disposals) > 1 else disposals[0]
                )
        except Exception:
            pass

        frames[0].save(buf, **save_kwargs)
        out = buf.getvalue()

        if len(out) >= len(data):
            notes.append("kept_original")
            out = data
        else:
            notes.append(f"colors_{target_colors}")
            if frames_in > 1:
                notes.append(f"frames_{frames_in}")

        return _Result(
            data=out,
            fmt="gif",
            media_type="image/gif",
            extension=".gif",
            width=width,
            height=height,
            frames=frames_in,
            notes=tuple(notes),
        )


# ---------------------------------------------------------------------------
# SVG minify (text-level; preserves structure)
# ---------------------------------------------------------------------------

_SVG_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# Collapse whitespace between tags; keep single spaces inside text lightly.
_SVG_BETWEEN_TAGS_RE = re.compile(r">\s+<")
_SVG_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_SVG_NEWLINE_RE = re.compile(r"[\r\n]+")
# Remove spaces around = in attributes:  foo = "bar" → foo="bar"
_SVG_ATTR_EQ_RE = re.compile(r"\s*=\s*")
# Self-closing space normalize:  <path  /> → <path/>
_SVG_SELF_CLOSE_RE = re.compile(r"\s+/>")


def _compress_svg(data: bytes, cfg: Dict[str, Any], *, strip_meta: bool) -> _Result:
    del cfg  # SVG has no quality ladder beyond minify aggressiveness.
    notes: List[str] = ["svg_minify"]

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise CompressError(f"SVG is not valid UTF-8: {exc}") from exc

    original_text = text

    # Strip BOM / leading junk whitespace.
    text = text.lstrip("\ufeff").strip()

    # Remove comments (metadata often lives here).
    if strip_meta:
        text = _SVG_COMMENT_RE.sub("", text)
        notes.append("comments_removed")
    else:
        # Still remove empty / editor boilerplate comments? Keep if not stripping.
        pass

    # Remove XML prolog optional whitespace noise but keep the declaration.
    # Collapse newlines and multi-spaces between tags.
    text = _SVG_BETWEEN_TAGS_RE.sub("><", text)
    text = _SVG_NEWLINE_RE.sub(" ", text)
    text = _SVG_MULTI_SPACE_RE.sub(" ", text)
    text = _SVG_ATTR_EQ_RE.sub("=", text)
    text = _SVG_SELF_CLOSE_RE.sub("/>", text)
    # Trim spaces after < and before >
    text = re.sub(r"<\s+", "<", text)
    text = re.sub(r"\s+>", ">", text)
    text = text.strip()

    out = text.encode("utf-8")
    if len(out) >= len(data):
        notes.append("kept_original")
        out = data if isinstance(data, (bytes, bytearray)) else original_text.encode("utf-8")

    # Best-effort width/height from root attributes (not required).
    width = height = None
    m = re.search(
        r"<svg\b[^>]*\bwidth\s*=\s*[\"']([\d.]+)",
        text,
        re.IGNORECASE,
    )
    if m:
        try:
            width = int(float(m.group(1)))
        except ValueError:
            pass
    m = re.search(
        r"<svg\b[^>]*\bheight\s*=\s*[\"']([\d.]+)",
        text,
        re.IGNORECASE,
    )
    if m:
        try:
            height = int(float(m.group(1)))
        except ValueError:
            pass

    return _Result(
        data=bytes(out),
        fmt="svg",
        media_type="image/svg+xml",
        extension=".svg",
        width=width,
        height=height,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compress_image(
    data: bytes,
    *,
    filename: Optional[str] = None,
    quality: str = "balanced",
    strip_meta: bool = True,
    max_side: Optional[int] = None,
) -> Dict[str, Any]:
    """Compress image bytes.

    Parameters
    ----------
    data:
        Raw file bytes.
    filename:
        Optional name (helps format detection / output naming).
    quality:
        ``high`` | ``balanced`` | ``strong``.
    strip_meta:
        Drop EXIF / comments when safe (orientation is applied first).
    max_side:
        Optional override for longest-edge resize (0 = no resize).
        ``None`` uses the preset default.

    Returns
    -------
    dict with keys: data, format, media_type, extension, width, height,
    frames, quality, notes, and size statistics.
    """
    if not data:
        raise CompressError("Empty file")

    fmt = detect_format(data, filename)
    cfg = dict(_preset(quality))
    if max_side is not None:
        if max_side < 0:
            raise CompressError("max_side must be >= 0")
        cfg["max_side"] = int(max_side)

    try:
        if fmt == "jpeg":
            result = _compress_jpeg(data, cfg, strip_meta=strip_meta)
        elif fmt == "png":
            result = _compress_png(data, cfg, strip_meta=strip_meta)
        elif fmt == "gif":
            result = _compress_gif(data, cfg, strip_meta=strip_meta)
        elif fmt == "svg":
            result = _compress_svg(data, cfg, strip_meta=strip_meta)
        else:
            raise CompressError(f"Unsupported format: {fmt}")
    except CompressError:
        raise
    except OSError as exc:
        raise CompressError(f"Cannot read image: {exc}") from exc
    except Exception as exc:
        raise CompressError(f"Compression failed: {exc}") from exc

    stats = _size_stats(len(data), len(result.data))
    return {
        "data": result.data,
        "format": result.fmt,
        "media_type": result.media_type,
        "extension": result.extension,
        "width": result.width,
        "height": result.height,
        "frames": result.frames,
        "quality": (quality or "balanced").strip().lower(),
        "strip_meta": bool(strip_meta),
        "notes": list(result.notes),
        **stats,
    }
