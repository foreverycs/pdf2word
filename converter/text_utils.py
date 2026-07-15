"""CJK-aware word joining and spacing/newline normalization for PDF text."""

from __future__ import annotations

import re
from typing import Tuple

# ----- low level helpers ----------------------------------------------------
_CJK_RE = re.compile(
    r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]"
)


def _has_cjk(s: str) -> bool:
    return bool(_CJK_RE.search(s or ""))


def _word_line_sort_key(w: dict) -> Tuple[float, float]:
    """Reading order within one visual line: left-to-right, then top.

    Primary key must be ``x0``. Sorting by ``top`` first breaks Chinese list
    lines such as ``10、进入安装过程`` when the numeric marker sits a fraction
    of a point lower/higher than the CJK run — the marker was appended after
    the body (``、进入安装过程 10``).
    """
    return (float(w["x0"]), float(w.get("top") or 0.0))


def _join_words(words: list) -> str:
    """Join pdfplumber words into a line, keeping CJK characters tight (no
    space between adjacent CJK glyphs) while preserving spaces between Latin
    words and at CJK/Latin boundaries.

    Words are ordered left-to-right (not by vertical baseline) so slightly
    misaligned list markers stay before their text.
    """
    out = []
    prev = None
    for w in sorted(words, key=_word_line_sort_key):
        txt = w["text"]
        if prev is None:
            out.append(txt)
        else:
            prev_cjk = _has_cjk(prev["text"])
            cur_cjk = _has_cjk(txt)
            gap = w["x0"] - prev["x1"]
            if prev_cjk and cur_cjk:
                out.append(txt)
            elif not prev_cjk and not cur_cjk:
                out.append((" " + txt) if gap > 1.0 else txt)
            elif prev_cjk and not cur_cjk:
                # Number / Latin after CJK on the same line (e.g. "项 10") —
                # keep a space. Pure list markers should already be first by x0.
                out.append((" " + txt) if gap > 0.5 else txt)
            else:  # Latin followed by CJK: keep them tight ("10、进入…")
                out.append(txt)
        prev = w
    return "".join(out)


_SP_RE = re.compile(
    r"(?<=[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef])"
    r" +"
    r"(?=[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef])"
)


def _normalize_spacing(text: str) -> str:
    """Remove spaces that sit between two CJK characters (some extractors
    insert one between every glyph)."""
    return _SP_RE.sub("", text)


# Matches a \n that is a soft word-wrap (NOT a real paragraph break).
# A real break is one preceded by sentence-ending punctuation or followed by a
# numbered list marker; everything else is a soft wrap from PDF auto-layout.
_SOFT_NL_RE = re.compile(r"(?<![。！？；：])\n(?!\d[、.])")


def _normalize_newlines(text: str) -> str:
    """Replace soft word-wrap \\n from PDF auto-layout with spaces, while
    preserving real paragraph/list breaks (e.g. after 。 or before 1、)."""
    return _SOFT_NL_RE.sub(" ", text)


__all__ = [
    "_has_cjk",
    "_word_line_sort_key",
    "_join_words",
    "_normalize_spacing",
    "_normalize_newlines",
]
