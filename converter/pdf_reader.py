"""PDF content extraction (facade).

Implementation is split across focused modules under ``converter``:

- ``word_index`` — spatial index over words
- ``text_utils`` — CJK join / spacing helpers
- ``text_blocks`` — non-table text extraction
- ``tables`` — table detect / build / accept
- ``images`` — embedded images and rasterisation
- ``hlines`` — standalone horizontal rules
- ``extract`` — page orchestration and public API

This module re-exports the public surface (and private symbols used by tests)
so existing ``from converter.pdf_reader import ...`` keeps working.
"""

from __future__ import annotations

from .constants import IMAGE_RENDER_DPI, IMAGE_RENDER_MAX_PX, OCR_RENDER_DPI
from .extract import (
    content_warnings,
    count_blocks,
    extract_document,
    parse_page_range,
    _extract_page,
    _friendly_open_error,
    _ocr_page_to_text_blocks,
)
from .hlines import _extract_hlines
from .images import (
    _bbox_overlap_ratio,
    _clamp_render_dpi,
    _colorspace_kind,
    _embedded_stream_png,
    _extract_images,
    _image_h_align,
    _native_image_is_sharp_enough,
    _pdf_name_str,
    _pil_to_png_bytes,
    _render_full_page_image,
    _render_region_png,
    _stream_filter_names,
)
from .models import (
    Cell,
    ImageBlock,
    LineBlock,
    PageContent,
    TableBlock,
    TextBlock,
    TextRun,
)
from .tables import (
    _accept_table,
    _build_table,
    _cell_borders,
    _count_grid_lines_in_bbox,
    _estimate_aligned_columns,
    _find_tables,
    _has_drawn_grid,
    _index_of,
    _inter_column_text_gaps,
    _is_plausible_borderless_table,
    _iter_page_strokes,
    _paragraphs_to_text,
    _refine_merges_from_words,
    _region_paragraphs,
    _rgb_to_hex,
    _table_anchor_stats,
    _table_bbox_from_block,
    _table_bbox_overlap_ratio,
    _table_border,
    _words_in_bbox,
)
from .text_blocks import (
    _extract_text_blocks,
    _text_h_align,
    _word_mid_y,
    _words_same_visual_line,
)
from .text_utils import (
    _has_cjk,
    _join_words,
    _normalize_newlines,
    _normalize_spacing,
    _word_line_sort_key,
)
from .word_index import WordIndex

# Re-export so ``from converter.pdf_reader import TableBlock`` keeps working.
__all__ = [
    "Cell",
    "ImageBlock",
    "LineBlock",
    "PageContent",
    "TableBlock",
    "TextBlock",
    "TextRun",
    "IMAGE_RENDER_DPI",
    "IMAGE_RENDER_MAX_PX",
    "OCR_RENDER_DPI",
    "extract_document",
    "parse_page_range",
    "count_blocks",
    "content_warnings",
    "WordIndex",
    # private symbols imported by tests
    "_join_words",
    "_image_h_align",
    "_refine_merges_from_words",
    "_region_paragraphs",
]
