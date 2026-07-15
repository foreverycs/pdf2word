"""CJK-aware word joining and spacing/newline normalization for PDF text."""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .models import TextBlock, TextRun

# ----- low level helpers ----------------------------------------------------
_CJK_RE = re.compile(
    r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]"
)

# Sentence-ending punctuation: soft wraps never follow these.
_SENT_END_CHARS = frozenset("。！？；：.!?;…")
# Numbered / bullet list markers at the start of a visual line.
_LIST_START_RE = re.compile(
    r"^\s*(?:"
    r"\d{1,3}[、.．)）]"  # 1、 1. 1)
    r"|[（(]\d{1,3}[)）]"  # (1) （1）
    r"|[一二三四五六七八九十百]+[、.．]"
    r"|[•·▪◦●○◆◇■□]"
    r")"
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


# Boundary helpers for CJK / Latin / digit joining.
_CJK_CHAR_CLASS = (
    r"\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef"
)
# Fullwidth + common Chinese punctuation (no spaces around these).
_CJK_PUNCT_CLASS = (
    r"，。！？；：、…—·「」『』【】《》（）""''［］｛｝～"
    r"\u2014\u2018\u2019\u201c\u201d"  # em-dash, curly quotes
)


def _is_cjk_char(ch: str) -> bool:
    return bool(ch) and bool(_CJK_RE.match(ch))


def _is_cjk_punct(ch: str) -> bool:
    return bool(ch) and ch in (
        "，。！？；：、…—·「」『』【】《》（）""''［］｛｝～"
        "\u2014\u2018\u2019\u201c\u201d"
        ".,;:!?)]}"  # ASCII closers next to CJK also lose the space
    )


def _is_ascii_opener(ch: str) -> bool:
    return ch in "([{<"


def _is_ascii_closer(ch: str) -> bool:
    return ch in ")]}>.,;:!?"


def _join_words(words: list) -> str:
    """Join pdfplumber words into a line, keeping CJK characters tight (no
    space between adjacent CJK glyphs) while preserving spaces between Latin
    words.

    CJK next to digits / Latin stays tight for typical Chinese technical
    prose (``第1章``, ``版本V2``). A large horizontal gap still inserts a
    space so form labels like ``姓名    张三`` stay separated.

    Words are ordered left-to-right (not by vertical baseline) so slightly
    misaligned list markers stay before their text.
    """
    out: List[str] = []
    prev = None
    for w in sorted(words, key=_word_line_sort_key):
        # Clean spaces inside a single pdfplumber token first.
        txt = _normalize_spacing(w.get("text") or "")
        if not txt:
            continue
        if prev is None:
            out.append(txt)
            prev = {**w, "text": txt}
            continue

        prev_txt = prev["text"]
        gap = float(w["x0"]) - float(prev["x1"])
        prev_last = prev_txt[-1]
        cur_first = txt[0]
        prev_cjk = _is_cjk_char(prev_last) or _is_cjk_punct(prev_last)
        cur_cjk = _is_cjk_char(cur_first) or _is_cjk_punct(cur_first)
        prev_alnum = prev_last.isalnum() and not _is_cjk_char(prev_last)
        cur_alnum = cur_first.isalnum() and not _is_cjk_char(cur_first)

        if prev_cjk and cur_cjk:
            # CJK / CJK-punct abut.
            out.append(txt)
        elif prev_cjk and cur_alnum:
            # 「第」+「1」 / 「版本」+「V2」: tight unless a wide form gap.
            out.append((" " + txt) if gap > 8.0 else txt)
        elif prev_alnum and cur_cjk:
            # 「10」+「、进入」 / 「V2」+「版本」: always tight for list markers.
            out.append(txt)
        elif not prev_cjk and not cur_cjk:
            # Latin / Latin (or digit / digit): space when words are separated.
            if _is_ascii_opener(cur_first) or _is_ascii_closer(prev_last):
                out.append(txt)
            elif prev_last.isspace() or cur_first.isspace():
                out.append(txt)
            else:
                out.append((" " + txt) if gap > 1.0 else txt)
        else:
            # Mixed punctuation / symbols: keep tight next to CJK.
            if prev_cjk or cur_cjk:
                out.append(txt if gap <= 8.0 else (" " + txt))
            else:
                out.append((" " + txt) if gap > 1.0 else txt)

        prev = {**w, "text": txt}
    return _normalize_spacing("".join(out))


# Spaces between two CJK (incl. CJK punctuation range) glyphs.
_SP_BETWEEN_CJK_RE = re.compile(
    rf"(?<=[{_CJK_CHAR_CLASS}]) +(?=[{_CJK_CHAR_CLASS}])"
)
# Spaces before/after dedicated Chinese punctuation.
_SP_BEFORE_CJK_PUNCT_RE = re.compile(rf" +(?=[{_CJK_PUNCT_CLASS}])")
_SP_AFTER_CJK_PUNCT_RE = re.compile(rf"(?<=[{_CJK_PUNCT_CLASS}]) +")
# Spaces between CJK and ASCII digits (第 1 章 → 第1章).
_SP_CJK_DIGIT_RE = re.compile(
    rf"(?<=[{_CJK_CHAR_CLASS}]) +(?=\d)|(?<=\d) +(?=[{_CJK_CHAR_CLASS}])"
)
# CJK + space + short Latin (V2 / mm / kg / API) and the reverse.
# Keep longer words like "Python" spaced so "使用 Python" stays readable.
_SP_CJK_SHORT_LATIN_FWD_RE = re.compile(
    rf"(?<=[{_CJK_CHAR_CLASS}]) +([A-Za-z]{{1,4}})(?![A-Za-z])"
)
_SP_CJK_SHORT_LATIN_REV_RE = re.compile(
    rf"(?<![A-Za-z])([A-Za-z]{{1,4}}) +(?=[{_CJK_CHAR_CLASS}])"
)
# Fullwidth / NBSP → ASCII space before other passes.
_ODD_SPACE_RE = re.compile(r"[\u00a0\u2000-\u200b\u202f\u205f\u3000]+")
_MULTI_SPACE_RE = re.compile(r" {2,}")


def _normalize_spacing(text: str) -> str:
    """Clean spurious spaces from PDF extraction (esp. Chinese prose).

    * NBSP / fullwidth spaces → regular space
    * Spaces between CJK glyphs removed
    * Spaces around Chinese punctuation removed
    * Spaces between CJK and digits removed (``第 1 章`` → ``第1章``)
    * Spaces between CJK and short Latin codes removed (``版本 V2`` → ``版本V2``)
    * Runs of spaces collapsed to one (keeps real Latin word gaps)
    """
    if not text:
        return text or ""
    s = _ODD_SPACE_RE.sub(" ", text)
    # Iterate a few times so chained patterns settle (CJK space punct space CJK).
    for _ in range(3):
        prev = s
        s = _SP_BETWEEN_CJK_RE.sub("", s)
        s = _SP_BEFORE_CJK_PUNCT_RE.sub("", s)
        s = _SP_AFTER_CJK_PUNCT_RE.sub("", s)
        s = _SP_CJK_DIGIT_RE.sub("", s)
        s = _SP_CJK_SHORT_LATIN_FWD_RE.sub(r"\1", s)
        s = _SP_CJK_SHORT_LATIN_REV_RE.sub(r"\1", s)
        if s == prev:
            break
    s = _MULTI_SPACE_RE.sub(" ", s)
    return s


def _ends_sentence(text: str) -> bool:
    s = (text or "").rstrip()
    if not s:
        return False
    return s[-1] in _SENT_END_CHARS


def _starts_list_item(text: str) -> bool:
    return bool(_LIST_START_RE.match(text or ""))


def _looks_full_line(text: str) -> bool:
    """True when a line is long enough to be a PDF wrap (not a short title/label)."""
    t = (text or "").strip()
    if not t:
        return False
    # Hyphenated Latin wrap is always a continuation candidate.
    if t.endswith("-") and len(t) >= 4:
        return True
    if _has_cjk(t):
        # Count CJK glyphs; short headings like "安装说明" must not glue to body.
        cjk_n = sum(1 for ch in t if _has_cjk(ch))
        return cjk_n >= 12 or len(t) >= 18
    return len(t) >= 36


def _soft_join_text(left: str, right: str) -> str:
    """Join two soft-wrapped fragments without inventing a paragraph break.

    CJK stays tight; Latin gets a single space; hyphenated line-ends are
    re-joined (``exam-`` + ``ple`` → ``example``). Result is spacing-normalised.
    """
    a = _normalize_spacing(left or "").rstrip()
    b = _normalize_spacing(right or "").lstrip()
    if not a:
        return b
    if not b:
        return a
    # PDF hyphenation at line end.
    if a.endswith("-") and b[:1].isalpha() and not _is_cjk_char(b[0]):
        return _normalize_spacing(a[:-1] + b)
    a_last, b_first = a[-1], b[0]
    if _is_cjk_char(a_last) or _is_cjk_punct(a_last):
        if _is_cjk_char(b_first) or _is_cjk_punct(b_first) or b_first.isalnum():
            return _normalize_spacing(a + b)
    if _is_cjk_char(b_first) or _is_cjk_punct(b_first):
        # Latin/digit followed by CJK.
        return _normalize_spacing(a + b)
    # Latin / numeric: insert one space unless left already ends with space-ish.
    if a_last.isspace() or _is_ascii_opener(b_first) or _is_ascii_closer(a_last):
        return _normalize_spacing(a + b)
    return _normalize_spacing(a + " " + b)


def _is_soft_wrap_break(
    prev_text: str,
    next_text: str,
    *,
    v_gap: float = 0.0,
    prev_height: float = 0.0,
    next_height: float = 0.0,
    prev_x0: float = 0.0,
    next_x0: float = 0.0,
    prev_x1: float = 0.0,
    next_x1: float = 0.0,
    cell_right: Optional[float] = None,
    prev_font: Optional[float] = None,
    next_font: Optional[float] = None,
    prev_align: str = "left",
    next_align: str = "left",
) -> bool:
    """True when a visual line break is PDF word-wrap, not a new paragraph.

    Table cells often use ~1.5–2× font-size leading, so the empty gap between
    glyph boxes can approach one full line height even for soft wraps. Prefer
    right-edge fullness (line runs near the cell/page margin) plus list /
    sentence cues over a tight gap threshold.
    """
    prev_text = (prev_text or "").strip()
    next_text = (next_text or "").strip()
    if not prev_text or not next_text:
        return False
    if _ends_sentence(prev_text):
        return False
    if _starts_list_item(next_text):
        return False
    if prev_align != next_align and {prev_align, next_align} & {"center", "right"}:
        # Centre/right titles next to body are real breaks.
        return False

    line_h = max(
        prev_height or 0.0,
        next_height or 0.0,
        float(prev_font or 0.0) or 0.0,
        float(next_font or 0.0) or 0.0,
        10.0,
    )
    # How close the previous line ends to the cell/page right edge.
    near_right = False
    if cell_right is not None and prev_x1 > 0:
        room = float(cell_right) - float(prev_x1)
        # Within ~1.5 CJK glyphs (or 18pt) of the right edge → almost certainly wrap.
        near_right = room <= max(line_h * 1.6, 18.0)

    # Short labels only block soft-wrap when the line is clearly not full-width.
    if not _looks_full_line(prev_text) and not near_right:
        return False

    # Large vertical gap → real paragraph spacing.
    # Soft wraps in Chinese tables often sit at ~0.8–1.2× line box height.
    # Only reject when the gap is clearly a blank line (≥ ~1.6×).
    max_soft_gap = max(line_h * 1.55, 14.0)
    if near_right:
        # Full-width wrap can tolerate slightly looser leading.
        max_soft_gap = max(line_h * 2.1, 18.0)
    if v_gap > max_soft_gap:
        return False

    # Font size jump (heading → body) is not a soft wrap.
    if prev_font and next_font and abs(float(prev_font) - float(next_font)) >= 2.5:
        return False

    # Column jump: allow first-line indent (prev further right) and mild drift.
    # Reject when the next line starts much further right (nested block).
    dx = float(next_x0) - float(prev_x0)
    if dx > 40.0:
        return False
    # Continuation lines usually share the same left edge (or hang slightly left).
    # A large leftward jump is OK; a large rightward jump was handled above.

    return True


def _normalize_newlines(text: str) -> str:
    """Replace soft word-wrap \\n from PDF auto-layout with natural joins.

    Real paragraph/list breaks (after 。！？ or before ``1、``) are kept as ``\\n``.
    CJK soft wraps join with no space; Latin soft wraps join with a space.

    String-only path (no geometry): any non-sentence / non-list break is treated
    as a soft wrap — matching pdfplumber cell text which often embeds wrap ``\\n``
    without length cues. Spatial TextBlock merging uses ``_is_soft_wrap_break``
    with the full-line heuristic instead.
    """
    if not text:
        return text or ""
    # Normalise Windows newlines first.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if "\n" not in text:
        return _normalize_spacing(text)
    parts = text.split("\n")
    if len(parts) == 1:
        return _normalize_spacing(parts[0])
    out = parts[0]
    for nxt in parts[1:]:
        prev_s = (out or "").rstrip()
        next_s = (nxt or "").lstrip()
        if (
            prev_s
            and next_s
            and not _ends_sentence(prev_s)
            and not _starts_list_item(next_s)
        ):
            out = _soft_join_text(out, nxt)
        else:
            out = _normalize_spacing(prev_s) + "\n" + _normalize_spacing(next_s)
    return out


def _text_of_runs(runs: Sequence["TextRun"]) -> str:
    return "".join(r.text for r in runs if r and r.text)


def _merge_run_lists(
    left: Sequence["TextRun"], right: Sequence["TextRun"]
) -> List["TextRun"]:
    """Concatenate two run lists, soft-joining at the boundary."""
    from .models import TextRun

    left_l = [r for r in left if r and r.text]
    right_l = [r for r in right if r and r.text]
    if not left_l:
        return list(right_l)
    if not right_l:
        return list(left_l)

    # Soft-join only the abutting runs; keep earlier/later runs intact.
    boundary = _soft_join_text(left_l[-1].text, right_l[0].text)
    out = list(left_l[:-1])
    out.append(
        TextRun(
            text=boundary,
            font_size=left_l[-1].font_size or right_l[0].font_size,
            font_name=left_l[-1].font_name or right_l[0].font_name,
        )
    )
    out.extend(right_l[1:])
    return out


def _merge_soft_wrap_paragraphs(
    paragraphs: List[List["TextRun"]],
    *,
    line_boxes: Optional[Sequence[Tuple[float, float, float, float]]] = None,
    cell_right: Optional[float] = None,
) -> List[List["TextRun"]]:
    """Merge consecutive cell visual lines that are PDF soft wraps.

    ``line_boxes`` optional per-line ``(top, bottom, x0, x1)`` for gap / edge checks.
    ``cell_right`` is the cell's right edge (pt) used to detect full-width wraps.
    """
    if not paragraphs:
        return []
    if len(paragraphs) == 1:
        return paragraphs

    merged: List[List["TextRun"]] = [list(paragraphs[0])]
    # Index of the last original paragraph absorbed into ``merged[-1]``.
    last_src_idx = 0
    boxes = list(line_boxes) if line_boxes is not None else None

    for i in range(1, len(paragraphs)):
        prev_runs = merged[-1]
        next_runs = paragraphs[i]
        prev_text = _text_of_runs(prev_runs)
        next_text = _text_of_runs(next_runs)
        v_gap = 0.0
        prev_h = 0.0
        next_h = 0.0
        prev_x0 = 0.0
        next_x0 = 0.0
        prev_x1 = 0.0
        next_x1 = 0.0
        if boxes is not None and last_src_idx < len(boxes) and i < len(boxes):
            box_prev = boxes[last_src_idx]
            box_next = boxes[i]
            pt, pb, px = box_prev[0], box_prev[1], box_prev[2]
            nt, nb, nx = box_next[0], box_next[1], box_next[2]
            prev_x1 = float(box_prev[3]) if len(box_prev) > 3 else 0.0
            next_x1 = float(box_next[3]) if len(box_next) > 3 else 0.0
            v_gap = max(0.0, float(nt) - float(pb))
            prev_h = max(0.0, float(pb) - float(pt))
            next_h = max(0.0, float(nb) - float(nt))
            prev_x0 = float(px)
            next_x0 = float(nx)
        prev_font = next(
            (r.font_size for r in reversed(prev_runs) if r.font_size), None
        )
        next_font = next((r.font_size for r in next_runs if r.font_size), None)
        if _is_soft_wrap_break(
            prev_text,
            next_text,
            v_gap=v_gap,
            prev_height=prev_h,
            next_height=next_h,
            prev_x0=prev_x0,
            next_x0=next_x0,
            prev_x1=prev_x1,
            next_x1=next_x1,
            cell_right=cell_right,
            prev_font=prev_font,
            next_font=next_font,
        ):
            merged[-1] = _merge_run_lists(prev_runs, next_runs)
            last_src_idx = i
        else:
            merged.append(list(next_runs))
            last_src_idx = i
    return merged


def _merge_soft_wrap_text_blocks(
    blocks: List["TextBlock"],
    *,
    page_right: Optional[float] = None,
) -> List["TextBlock"]:
    """Merge consecutive page TextBlocks that are PDF soft word-wraps.

    Each visual line from pdfplumber becomes one TextBlock; body paragraphs
    that wrap in the PDF would otherwise appear as hard line breaks in Word.
    """
    from .models import TextBlock

    if not blocks:
        return []
    out: List[TextBlock] = []
    cur = blocks[0]
    # Track the last visual line's geometry separately so multi-line merges
    # don't inflate prev_height (which would loosen the gap threshold).
    last_top = float(cur.top)
    last_bot = float(cur.bottom)
    last_x0 = float(cur.x0)
    last_x1 = float(cur.x1)
    for nxt in blocks[1:]:
        prev_h = max(0.0, last_bot - last_top)
        next_h = max(0.0, float(nxt.bottom) - float(nxt.top))
        v_gap = max(0.0, float(nxt.top) - last_bot)
        # Only use a real page width for right-edge fullness; do not infer from
        # block x1 (that would mark every line "full" relative to itself).
        if _is_soft_wrap_break(
            cur.text,
            nxt.text,
            v_gap=v_gap,
            prev_height=prev_h,
            next_height=next_h,
            prev_x0=last_x0,
            next_x0=float(nxt.x0),
            prev_x1=last_x1,
            next_x1=float(nxt.x1),
            cell_right=page_right,
            prev_font=cur.font_size,
            next_font=nxt.font_size,
            prev_align=cur.align or "left",
            next_align=nxt.align or "left",
        ):
            cur = TextBlock(
                text=_soft_join_text(cur.text, nxt.text),
                top=cur.top,
                bottom=nxt.bottom,
                x0=min(cur.x0, nxt.x0),
                x1=max(cur.x1, nxt.x1),
                font_size=cur.font_size or nxt.font_size,
                font_name=cur.font_name or nxt.font_name,
                align=cur.align,
                from_ocr=cur.from_ocr or nxt.from_ocr,
            )
            last_top = float(nxt.top)
            last_bot = float(nxt.bottom)
            last_x0 = float(nxt.x0)
            last_x1 = float(nxt.x1)
        else:
            out.append(
                TextBlock(
                    text=_normalize_spacing(cur.text),
                    top=cur.top,
                    bottom=cur.bottom,
                    x0=cur.x0,
                    x1=cur.x1,
                    font_size=cur.font_size,
                    font_name=cur.font_name,
                    align=cur.align,
                    from_ocr=cur.from_ocr,
                )
            )
            cur = nxt
            last_top = float(cur.top)
            last_bot = float(cur.bottom)
            last_x0 = float(cur.x0)
            last_x1 = float(cur.x1)
    out.append(
        TextBlock(
            text=_normalize_spacing(cur.text),
            top=cur.top,
            bottom=cur.bottom,
            x0=cur.x0,
            x1=cur.x1,
            font_size=cur.font_size,
            font_name=cur.font_name,
            align=cur.align,
            from_ocr=cur.from_ocr,
        )
    )
    return out


__all__ = [
    "_has_cjk",
    "_is_cjk_char",
    "_word_line_sort_key",
    "_join_words",
    "_normalize_spacing",
    "_normalize_newlines",
    "_soft_join_text",
    "_is_soft_wrap_break",
    "_ends_sentence",
    "_starts_list_item",
    "_merge_soft_wrap_text_blocks",
    "_merge_soft_wrap_paragraphs",
    "_merge_run_lists",
]
