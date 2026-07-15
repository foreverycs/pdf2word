"""Page orchestration and public document extraction API."""

from __future__ import annotations

import os
from typing import List, Optional

import pdfplumber

from .constants import OCR_RENDER_DPI
from .hlines import _extract_hlines
from .images import _extract_images, _render_full_page_image, _render_region_png
from .models import ImageBlock, LineBlock, PageContent, TableBlock, TextBlock
from .tables import _accept_table, _build_table, _find_tables
from .text_blocks import _extract_text_blocks
from .word_index import WordIndex


def _extract_page(page, *, ocr: bool = False, ocr_lang: Optional[str] = None) -> PageContent:
    # Extract words (with font info) and lines once for the whole page and reuse
    # them for every table and the text blocks, instead of re-parsing per table.
    words = page.extract_words(
        use_text_flow=False, keep_blank_chars=False, extra_attrs=["fontname", "size"]
    )
    widx = WordIndex(words)
    page_w = float(getattr(page, "width", 0) or 0)
    page_h = float(getattr(page, "height", 0) or 0)
    raw_tables = _find_tables(page)
    tables = []          # list of (top, TableBlock)
    bboxes = []
    for t in raw_tables:
        tb = _build_table(t, page, widx)
        if tb is None:
            continue
        # Drop text-strategy false positives (plain prose / multi-col layout
        # misread as a grid) so content stays as TextBlock / ImageBlock.
        if not _accept_table(tb, page, widx):
            continue
        tables.append((tb.top, tb))
        bboxes.append(t.bbox)

    text_blocks = _extract_text_blocks(page, bboxes, widx)
    image_blocks = _extract_images(page, bboxes)
    line_blocks = _extract_hlines(page, bboxes)

    # interleave text, tables, images and rules by vertical position
    ordered = (
        [(top, tb) for top, tb in tables]
        + [(b.top, b) for b in text_blocks]
        + [(b.top, b) for b in image_blocks]
        + [(b.top, b) for b in line_blocks]
    )
    ordered.sort(key=lambda item: item[0])
    blocks: List = [tb for _, tb in ordered]

    # Scanned / image-only page: no extractable text or tables.
    has_text_or_table = any(
        isinstance(b, (TextBlock, TableBlock)) for b in blocks
    )
    if not has_text_or_table:
        ocr_blocks: List[TextBlock] = []
        if ocr:
            ocr_blocks = _ocr_page_to_text_blocks(page, lang=ocr_lang)
        if ocr_blocks:
            # Prefer editable OCR text; keep a light full-page image behind? No —
            # OCR text alone is the editable output; caller can re-run without OCR
            # for image-only. Still attach page image only when OCR found nothing.
            blocks = sorted(ocr_blocks, key=lambda b: b.top)
        elif not any(isinstance(b, ImageBlock) for b in blocks):
            # Only fall back to a full-page raster when nothing was extracted.
            # Replacing already-decoded native XObjects with a page re-render
            # was dropping sharpness (e.g. a lone photo with no caption text).
            full = _render_full_page_image(page)
            if full is not None:
                blocks = [full]

    return PageContent(blocks=blocks, width=page_w, height=page_h)


def _ocr_page_to_text_blocks(page, *, lang: Optional[str] = None) -> List[TextBlock]:
    """Rasterise the page and OCR into TextBlocks (empty list if OCR unavailable)."""
    from .ocr import ocr_available, ocr_image_to_blocks

    if not ocr_available():
        return []
    try:
        w = float(getattr(page, "width", 0) or 0)
        h = float(getattr(page, "height", 0) or 0)
        if w <= 0 or h <= 0:
            return []
        png = _render_region_png(page, (0, 0, w, h), resolution=OCR_RENDER_DPI)
        if not png:
            return []
        return ocr_image_to_blocks(png, page_width=w, page_height=h, lang=lang)
    except Exception:
        return []


def parse_page_range(spec: Optional[str], total_pages: int) -> List[int]:
    """Parse a 1-based page range like ``1-3,5,7-9`` into 0-based indices.

    Empty / whitespace ``spec`` means all pages. Raises ``ValueError`` on
    malformed input or out-of-range numbers.
    """
    if total_pages < 1:
        raise ValueError("PDF has no pages")
    if not spec or not str(spec).strip():
        return list(range(total_pages))

    indices: List[int] = []
    seen = set()
    for part in str(spec).split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            ends = token.split("-", 1)
            if len(ends) != 2 or not ends[0].strip() or not ends[1].strip():
                raise ValueError(f"Invalid page range: {token!r}")
            try:
                start = int(ends[0].strip())
                end = int(ends[1].strip())
            except ValueError as exc:
                raise ValueError(f"Invalid page range: {token!r}") from exc
            if start < 1 or end < 1 or start > end:
                raise ValueError(f"Invalid page range: {token!r}")
            for n in range(start, end + 1):
                if n > total_pages:
                    raise ValueError(
                        f"Page {n} out of range (PDF has {total_pages} pages)"
                    )
                idx = n - 1
                if idx not in seen:
                    seen.add(idx)
                    indices.append(idx)
        else:
            try:
                n = int(token)
            except ValueError as exc:
                raise ValueError(f"Invalid page number: {token!r}") from exc
            if n < 1 or n > total_pages:
                raise ValueError(
                    f"Page {n} out of range (PDF has {total_pages} pages)"
                )
            idx = n - 1
            if idx not in seen:
                seen.add(idx)
                indices.append(idx)

    if not indices:
        raise ValueError("No pages selected")
    return indices


def count_blocks(pages: List[PageContent]) -> dict:
    """Return simple conversion stats for response headers / UI."""
    tables = sum(
        1 for p in pages for b in p.blocks if isinstance(b, TableBlock)
    )
    texts = sum(
        1 for p in pages for b in p.blocks if isinstance(b, TextBlock)
    )
    images = sum(
        1 for p in pages for b in p.blocks if isinstance(b, ImageBlock)
    )
    lines = sum(
        1 for p in pages for b in p.blocks if isinstance(b, LineBlock)
    )
    return {
        "pages": len(pages),
        "tables": tables,
        "text_blocks": texts,
        "images": images,
        "lines": lines,
    }


def content_warnings(pages: List[PageContent]) -> List[str]:
    """Heuristic warnings for the UI (scanned PDF, empty extract, …)."""
    stats = count_blocks(pages)
    warnings: List[str] = []
    if stats["pages"] == 0:
        warnings.append("empty")
        return warnings
    if (
        stats["tables"] == 0
        and stats["text_blocks"] == 0
        and stats["images"] == 0
    ):
        warnings.append("empty")
    elif stats["tables"] == 0 and stats["text_blocks"] == 0 and stats["images"] > 0:
        warnings.append("image_only")
    # OCR produced text from a scan (no native PDF text layer was present for
    # those pages) — still useful for UI messaging when only OCR text exists
    # without tables and the source was image-heavy. Detected via flag on blocks.
    if any(getattr(b, "from_ocr", False) for p in pages for b in p.blocks):
        warnings.append("ocr_applied")
    return warnings


def _friendly_open_error(exc: BaseException) -> str:
    msg = str(exc).lower()
    if any(k in msg for k in ("password", "encrypt", "crypt")):
        return "PDF is password-protected; please decrypt it first"
    return f"Cannot open PDF: {exc}"


def extract_document(
    pdf_path: str,
    page_range: Optional[str] = None,
    *,
    ocr: bool = False,
    ocr_lang: Optional[str] = None,
) -> List[PageContent]:
    """Extract structured content from a PDF.

    ``page_range`` is an optional 1-based spec (e.g. ``"1-3,5"``). When
    omitted, every page is processed.

    Image-only / scanned pages are embedded as full-page rasters by default.
    Pass ``ocr=True`` to run optional Tesseract OCR (requires ``pytesseract``
    and a system Tesseract install) so scanned text becomes editable.
    """
    # Env override: PDF2WORD_OCR=1 enables OCR even if the caller omitted it.
    if not ocr:
        env = (os.environ.get("PDF2WORD_OCR") or "").strip().lower()
        ocr = env in ("1", "true", "yes", "on")
    if ocr_lang is None:
        ocr_lang = os.environ.get("PDF2WORD_OCR_LANG") or None

    pages: List[PageContent] = []
    try:
        pdf = pdfplumber.open(pdf_path)
    except Exception as exc:
        raise ValueError(_friendly_open_error(exc)) from exc

    try:
        total = len(pdf.pages)
        if total == 0:
            raise ValueError("PDF has no pages")
        indices = parse_page_range(page_range, total)
        for i in indices:
            try:
                pages.append(
                    _extract_page(pdf.pages[i], ocr=ocr, ocr_lang=ocr_lang)
                )
            except Exception as exc:
                raise ValueError(
                    _friendly_open_error(exc)
                    if any(k in str(exc).lower() for k in ("password", "encrypt", "crypt"))
                    else f"Failed to read page {i + 1}: {exc}"
                ) from exc
    finally:
        pdf.close()
    return pages


__all__ = [
    "extract_document",
    "parse_page_range",
    "count_blocks",
    "content_warnings",
    "_extract_page",
    "_ocr_page_to_text_blocks",
    "_friendly_open_error",
]
