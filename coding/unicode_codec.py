"""中文 / Unicode 转义还原与编码。

支持常见形态：
- ``\\uXXXX`` / ``\\UXXXXXXXX``（JSON / Python / JS）
- ``\\u{XXXX}``（ES6）
- ``%uXXXX``（部分旧系统 / JS escape）
- ``U+XXXX``（Unicode 码位记号）
- HTML 实体 ``&#xXXXX;`` / ``&#DDDD;``
"""

from __future__ import annotations

import re
from typing import Any, Dict, Literal, Optional

Mode = Literal["auto", "backslash_u", "percent_u", "u_plus", "html_entity"]

# \uXXXX and \UXXXXXXXX (Python long form); case-insensitive hex.
_RE_BACKSLASH_U = re.compile(
    r"\\u([0-9a-fA-F]{4})|\\U([0-9a-fA-F]{8})",
    re.UNICODE,
)
# ES6: \u{1F600}
_RE_ES6_U = re.compile(r"\\u\{([0-9a-fA-F]{1,8})\}", re.UNICODE)
# %uXXXX (legacy JS escape style)
_RE_PERCENT_U = re.compile(r"%u([0-9a-fA-F]{4})", re.UNICODE)
# U+XXXX or U+XXXXX (1–6 hex digits)
_RE_U_PLUS = re.compile(r"\bU\+([0-9a-fA-F]{1,6})\b", re.UNICODE)
# HTML numeric entities
_RE_HTML_HEX = re.compile(r"&#x([0-9a-fA-F]{1,6});", re.IGNORECASE)
_RE_HTML_DEC = re.compile(r"&#([0-9]{1,7});")

# Double-escaped: \\uXXXX → treat as \uXXXX after one unescape pass of the slash.
_RE_DOUBLE_BACKSLASH_U = re.compile(
    r"\\\\u([0-9a-fA-F]{4})|\\\\U([0-9a-fA-F]{8})",
    re.UNICODE,
)
_RE_DOUBLE_ES6 = re.compile(r"\\\\u\{([0-9a-fA-F]{1,8})\}", re.UNICODE)

_MAX_CODE_POINT = 0x10FFFF


class UnicodeCodecError(ValueError):
    """Raised when Unicode escape input cannot be processed."""


def _chr_safe(cp: int) -> str:
    if cp < 0 or cp > _MAX_CODE_POINT:
        raise UnicodeCodecError(f"非法码位: U+{cp:X}")
    try:
        return chr(cp)
    except ValueError as exc:
        raise UnicodeCodecError(f"非法码位: U+{cp:X}") from exc


def _replace_backslash_u(match: re.Match[str]) -> str:
    hex4, hex8 = match.group(1), match.group(2)
    if hex4 is not None:
        return _chr_safe(int(hex4, 16))
    return _chr_safe(int(hex8, 16))


def _replace_es6(match: re.Match[str]) -> str:
    return _chr_safe(int(match.group(1), 16))


def _replace_percent_u(match: re.Match[str]) -> str:
    return _chr_safe(int(match.group(1), 16))


def _replace_u_plus(match: re.Match[str]) -> str:
    return _chr_safe(int(match.group(1), 16))


def _replace_html_hex(match: re.Match[str]) -> str:
    return _chr_safe(int(match.group(1), 16))


def _replace_html_dec(match: re.Match[str]) -> str:
    return _chr_safe(int(match.group(1), 10))


def _count_matches(pattern: re.Pattern[str], text: str) -> int:
    return sum(1 for _ in pattern.finditer(text))


def _decode_once(text: str, mode: str) -> tuple[str, int]:
    """Apply one decode pass. Returns (result, replacements)."""
    if mode == "backslash_u":
        # Prefer ES6 form first so \u{4e2d} is not partially eaten.
        n_es6 = _count_matches(_RE_ES6_U, text)
        out = _RE_ES6_U.sub(_replace_es6, text)
        n_u = _count_matches(_RE_BACKSLASH_U, out)
        out = _RE_BACKSLASH_U.sub(_replace_backslash_u, out)
        return out, n_es6 + n_u

    if mode == "percent_u":
        n = _count_matches(_RE_PERCENT_U, text)
        return _RE_PERCENT_U.sub(_replace_percent_u, text), n

    if mode == "u_plus":
        n = _count_matches(_RE_U_PLUS, text)
        return _RE_U_PLUS.sub(_replace_u_plus, text), n

    if mode == "html_entity":
        n_hex = _count_matches(_RE_HTML_HEX, text)
        out = _RE_HTML_HEX.sub(_replace_html_hex, text)
        n_dec = _count_matches(_RE_HTML_DEC, out)
        out = _RE_HTML_DEC.sub(_replace_html_dec, out)
        return out, n_hex + n_dec

    # auto: all forms; also peel one layer of double-backslash escapes.
    total = 0
    out = text

    n_dbl_es6 = _count_matches(_RE_DOUBLE_ES6, out)
    if n_dbl_es6:
        out = _RE_DOUBLE_ES6.sub(lambda m: "\\u{" + m.group(1) + "}", out)
        total += n_dbl_es6

    n_dbl = _count_matches(_RE_DOUBLE_BACKSLASH_U, out)
    if n_dbl:

        def _peel_double(m: re.Match[str]) -> str:
            if m.group(1) is not None:
                return "\\u" + m.group(1)
            return "\\U" + m.group(2)

        out = _RE_DOUBLE_BACKSLASH_U.sub(_peel_double, out)
        total += n_dbl

    for pattern, repl in (
        (_RE_ES6_U, _replace_es6),
        (_RE_BACKSLASH_U, _replace_backslash_u),
        (_RE_PERCENT_U, _replace_percent_u),
        (_RE_U_PLUS, _replace_u_plus),
        (_RE_HTML_HEX, _replace_html_hex),
        (_RE_HTML_DEC, _replace_html_dec),
    ):
        n = _count_matches(pattern, out)
        if n:
            out = pattern.sub(repl, out)
            total += n
    return out, total


def decode_unicode(
    text: str,
    *,
    mode: str = "auto",
    max_passes: int = 3,
) -> Dict[str, Any]:
    """还原文本中的 Unicode 转义为真实字符。

    Parameters
    ----------
    text:
        含 ``\\uXXXX`` 等转义的字符串。
    mode:
        ``auto`` 尝试全部格式；也可指定单一格式。
    max_passes:
        多层嵌套转义时的最大还原轮数（例如 ``\\\\\\\\u4e2d``）。
    """
    if text is None:
        raise UnicodeCodecError("输入不能为空")
    if mode not in ("auto", "backslash_u", "percent_u", "u_plus", "html_entity"):
        raise UnicodeCodecError(
            "mode 必须是 auto / backslash_u / percent_u / u_plus / html_entity"
        )
    if max_passes < 1 or max_passes > 10:
        raise UnicodeCodecError("max_passes 须在 1–10 之间")

    original = text
    out = text
    total_replacements = 0
    passes = 0
    for _ in range(max_passes):
        passes += 1
        try:
            nxt, n = _decode_once(out, mode)
        except UnicodeCodecError:
            raise
        total_replacements += n
        if n == 0 or nxt == out:
            out = nxt
            break
        out = nxt

    return {
        "result": out,
        "mode": mode,
        "replacements": total_replacements,
        "passes": passes,
        "input_chars": len(original),
        "output_chars": len(out),
        "changed": out != original,
    }


def encode_unicode(
    text: str,
    *,
    style: str = "backslash_u",
    ascii_only: bool = False,
    uppercase: bool = False,
) -> Dict[str, Any]:
    """将文本编码为 Unicode 转义。

    Parameters
    ----------
    text:
        明文。
    style:
        ``backslash_u`` → ``\\uXXXX``；
        ``u_plus`` → ``U+XXXX``；
        ``html_entity`` → ``&#xXXXX;``；
        ``percent_u`` → ``%uXXXX``。
    ascii_only:
        True 时仅转义非 ASCII；False 时转义全部非 ASCII（默认），ASCII 原样保留。
        （参数名保留兼容：始终保留可打印 ASCII 原样。）
    uppercase:
        十六进制是否大写。
    """
    if text is None:
        raise UnicodeCodecError("输入不能为空")
    if style not in ("backslash_u", "u_plus", "html_entity", "percent_u"):
        raise UnicodeCodecError(
            "style 必须是 backslash_u / u_plus / html_entity / percent_u"
        )

    # ascii_only currently means "escape non-ASCII only" which is the only
    # sensible default; keep flag for API symmetry / future "escape all".
    _ = ascii_only

    parts: list[str] = []
    escaped = 0
    for ch in text:
        cp = ord(ch)
        if cp < 128:
            parts.append(ch)
            continue
        escaped += 1
        if style == "backslash_u":
            if cp <= 0xFFFF:
                hx = f"{cp:04X}" if uppercase else f"{cp:04x}"
                parts.append(f"\\u{hx}")
            else:
                # Surrogate pair as two \uXXXX (JSON-compatible) or \UXXXXXXXX
                hx = f"{cp:08X}" if uppercase else f"{cp:08x}"
                parts.append(f"\\U{hx}")
        elif style == "u_plus":
            hx = f"{cp:X}" if uppercase else f"{cp:x}"
            # pad common BMP to 4 digits for readability
            if len(hx) < 4:
                hx = hx.zfill(4)
                if not uppercase:
                    hx = hx.lower()
            parts.append(f"U+{hx}")
        elif style == "html_entity":
            hx = f"{cp:X}" if uppercase else f"{cp:x}"
            parts.append(f"&#x{hx};")
        else:  # percent_u — only BMP; fall back to \u for astral
            if cp <= 0xFFFF:
                hx = f"{cp:04X}" if uppercase else f"{cp:04x}"
                parts.append(f"%u{hx}")
            else:
                hx = f"{cp:08X}" if uppercase else f"{cp:08x}"
                parts.append(f"\\U{hx}")

    result = "".join(parts)
    return {
        "result": result,
        "style": style,
        "uppercase": uppercase,
        "escaped_chars": escaped,
        "input_chars": len(text),
        "output_chars": len(result),
    }


def probe_unicode(text: str) -> Dict[str, Any]:
    """探测文本中可能含有的 Unicode 转义形态。"""
    if text is None:
        text = ""
    counts = {
        "backslash_u": _count_matches(_RE_BACKSLASH_U, text)
        + _count_matches(_RE_ES6_U, text),
        "double_backslash_u": _count_matches(_RE_DOUBLE_BACKSLASH_U, text)
        + _count_matches(_RE_DOUBLE_ES6, text),
        "percent_u": _count_matches(_RE_PERCENT_U, text),
        "u_plus": _count_matches(_RE_U_PLUS, text),
        "html_entity": _count_matches(_RE_HTML_HEX, text)
        + _count_matches(_RE_HTML_DEC, text),
    }
    detected = [k for k, v in counts.items() if v > 0]
    return {
        "counts": counts,
        "detected": detected,
        "likely": bool(detected),
        "input_chars": len(text),
    }


__all__ = [
    "UnicodeCodecError",
    "decode_unicode",
    "encode_unicode",
    "probe_unicode",
]
