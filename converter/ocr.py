"""Optional OCR for scanned / image-only PDF pages.

Uses Tesseract via ``pytesseract`` when both the Python package and a system
Tesseract binary are available. Callers should treat OCR as best-effort: if
unavailable, the pipeline falls back to embedding full-page images.
"""

from __future__ import annotations

import io
import os
import shutil
from functools import lru_cache
from typing import List, Optional

from .models import TextBlock
from .text_utils import _merge_soft_wrap_text_blocks

# Default languages: simplified Chinese + English (common for this toolbox).
DEFAULT_OCR_LANG = "chi_sim+eng"
# Drop OCR boxes with very low confidence (-1 means Tesseract withheld a score).
MIN_OCR_CONFIDENCE = 35.0


@lru_cache(maxsize=1)
def ocr_available() -> bool:
    """Return True when pytesseract + a Tesseract binary can be used."""
    try:
        import pytesseract  # type: ignore
    except ImportError:
        return False

    cmd = os.environ.get("TESSERACT_CMD") or os.environ.get("TESSERACT_PATH")
    if cmd and os.path.isfile(cmd):
        pytesseract.pytesseract.tesseract_cmd = cmd
    elif shutil.which("tesseract"):
        pass
    else:
        # Common Windows install path.
        win = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if os.path.isfile(win):
            pytesseract.pytesseract.tesseract_cmd = win
        else:
            return False

    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def ocr_info() -> dict:
    """Diagnostic info for health / UI (cached; binary probe is expensive)."""
    available = ocr_available()
    lang = os.environ.get("PDF2WORD_OCR_LANG") or DEFAULT_OCR_LANG
    cmd = None
    version = None
    if available:
        try:
            import pytesseract  # type: ignore

            cmd = getattr(pytesseract.pytesseract, "tesseract_cmd", None) or shutil.which(
                "tesseract"
            )
            version = str(pytesseract.get_tesseract_version())
        except Exception:
            pass
    return {
        "available": available,
        "lang": lang,
        "tesseract_cmd": cmd,
        "version": version,
    }


def ocr_image_to_blocks(
    png_bytes: bytes,
    *,
    page_width: float,
    page_height: float,
    lang: Optional[str] = None,
) -> List[TextBlock]:
    """Run Tesseract on a page PNG and return positioned TextBlocks.

    Coordinates from Tesseract are in image pixels; they are scaled back to
    PDF points using ``page_width`` / ``page_height``.
    """
    if not ocr_available() or not png_bytes:
        return []
    if page_width <= 0 or page_height <= 0:
        return []

    try:
        import pytesseract  # type: ignore
        from PIL import Image
    except ImportError:
        return []

    try:
        img = Image.open(io.BytesIO(png_bytes))
        # Grayscale + slight contrast helps forms / scans.
        gray = img.convert("L")
        img_w, img_h = gray.size
        if img_w < 8 or img_h < 8:
            return []

        ocr_lang = (lang or os.environ.get("PDF2WORD_OCR_LANG") or DEFAULT_OCR_LANG).strip()
        data = pytesseract.image_to_data(
            gray,
            lang=ocr_lang,
            output_type=pytesseract.Output.DICT,
            config="--psm 6",
        )
    except Exception:
        return []

    n = len(data.get("text") or [])
    if n == 0:
        return []

    sx = page_width / float(img_w)
    sy = page_height / float(img_h)

    # Group words into lines by (block_num, par_num, line_num).
    lines: dict = {}
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf >= 0 and conf < MIN_OCR_CONFIDENCE:
            continue
        try:
            left = float(data["left"][i])
            top = float(data["top"][i])
            width = float(data["width"][i])
            height = float(data["height"][i])
        except (TypeError, ValueError, KeyError):
            continue
        if width <= 0 or height <= 0:
            continue
        key = (
            int(data.get("block_num", [0])[i] or 0),
            int(data.get("par_num", [0])[i] or 0),
            int(data.get("line_num", [0])[i] or 0),
        )
        lines.setdefault(key, []).append(
            {
                "text": text,
                "left": left,
                "top": top,
                "right": left + width,
                "bottom": top + height,
                "conf": conf,
            }
        )

    blocks: List[TextBlock] = []
    for key in sorted(lines.keys()):
        words = sorted(lines[key], key=lambda w: w["left"])
        # CJK-aware join: no space between CJK tokens.
        parts: List[str] = []
        for j, w in enumerate(words):
            t = w["text"]
            if j == 0:
                parts.append(t)
                continue
            prev = words[j - 1]["text"]
            if _is_cjk_char(prev[-1:]) and _is_cjk_char(t[:1]):
                parts.append(t)
            else:
                parts.append(" " + t)
        line_text = "".join(parts).strip()
        if not line_text:
            continue
        x0 = min(w["left"] for w in words) * sx
        x1 = max(w["right"] for w in words) * sx
        top = min(w["top"] for w in words) * sy
        bottom = max(w["bottom"] for w in words) * sy
        # Estimate font size from box height in points.
        font_size = max(round((bottom - top) * 0.75, 1), 8.0)
        align = _guess_align(x0, x1, page_width)
        blocks.append(
            TextBlock(
                text=line_text,
                top=top,
                bottom=bottom,
                x0=x0,
                x1=x1,
                font_size=font_size,
                font_name="宋体",
                align=align,
                from_ocr=True,
            )
        )
    return _merge_soft_wrap_text_blocks(blocks)


def _is_cjk_char(s: str) -> bool:
    if not s:
        return False
    code = ord(s[0])
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x3000 <= code <= 0x303F
        or 0xFF00 <= code <= 0xFFEF
    )


def _guess_align(x0: float, x1: float, page_w: float) -> str:
    if page_w <= 0:
        return "left"
    width = max(x1 - x0, 1.0)
    if width / page_w >= 0.7:
        return "left"
    left_pad = max(x0, 0.0)
    right_pad = max(page_w - x1, 0.0)
    mid = (x0 + x1) / 2.0 / page_w
    if abs(left_pad - right_pad) <= max(page_w * 0.12, 18.0) or 0.38 < mid < 0.62:
        if left_pad > page_w * 0.18 or abs(left_pad - right_pad) <= page_w * 0.12:
            return "center"
    if left_pad > right_pad * 2.0 and left_pad / page_w > 0.45:
        return "right"
    return "left"
