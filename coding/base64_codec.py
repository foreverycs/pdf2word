"""Base64 编码 / 解码（文本与二进制安全）。"""

from __future__ import annotations

import base64
import re
from typing import Any, Dict, Literal, Optional, Union

Charset = Literal["utf-8", "utf-16", "latin-1", "ascii"]
Variant = Literal["standard", "urlsafe"]

_SUPPORTED_CHARSETS = ("utf-8", "utf-16", "latin-1", "ascii")
_WS_RE = re.compile(r"\s+")


class DecodeError(ValueError):
    """Raised when Base64 input cannot be decoded."""


def _b64_module(variant: str):
    if variant == "urlsafe":
        return base64.urlsafe_b64encode, base64.urlsafe_b64decode
    return base64.b64encode, base64.b64decode


def encode_base64(
    data: Union[str, bytes],
    *,
    charset: str = "utf-8",
    variant: str = "standard",
    wrap: int = 0,
) -> Dict[str, Any]:
    """Encode text or bytes to Base64.

    Parameters
    ----------
    data:
        Plain text (str) or raw bytes.
    charset:
        Used only when ``data`` is str.
    variant:
        ``standard`` or ``urlsafe`` (RFC 4648 §5).
    wrap:
        Insert newlines every N characters (0 = no wrap). Common: 76.
    """
    if charset not in _SUPPORTED_CHARSETS:
        raise ValueError(f"Unsupported charset: {charset}")
    if variant not in ("standard", "urlsafe"):
        raise ValueError("variant must be 'standard' or 'urlsafe'")
    if wrap < 0:
        raise ValueError("wrap must be >= 0")

    if isinstance(data, str):
        try:
            raw = data.encode(charset)
        except UnicodeEncodeError as exc:
            raise ValueError(f"Cannot encode text as {charset}: {exc}") from exc
        input_kind = "text"
        input_chars = len(data)
    else:
        raw = bytes(data)
        input_kind = "bytes"
        input_chars = None

    enc, _ = _b64_module(variant)
    encoded = enc(raw).decode("ascii")
    if wrap and wrap > 0:
        encoded = "\n".join(
            encoded[i : i + wrap] for i in range(0, len(encoded), wrap)
        )

    return {
        "result": encoded,
        "variant": variant,
        "charset": charset if input_kind == "text" else None,
        "input_kind": input_kind,
        "input_bytes": len(raw),
        "input_chars": input_chars,
        "output_chars": len(encoded.replace("\n", "")),
        "wrap": wrap or 0,
    }


def decode_base64(
    text: str,
    *,
    charset: Optional[str] = "utf-8",
    variant: str = "standard",
    strict: bool = False,
) -> Dict[str, Any]:
    """Decode a Base64 string.

    If ``charset`` is set, also attempt to decode bytes as text.
    Pass ``charset=None`` to return only hex of raw bytes (binary-safe path
    still returns ``raw_b64`` of the bytes re-encoded for transport).
    """
    if variant not in ("standard", "urlsafe"):
        raise ValueError("variant must be 'standard' or 'urlsafe'")
    if charset is not None and charset not in _SUPPORTED_CHARSETS:
        raise ValueError(f"Unsupported charset: {charset}")

    cleaned = _WS_RE.sub("", (text or "").strip())
    if not cleaned:
        raise DecodeError("Empty Base64 input")

    # Normalize URL-safe / standard alphabet mismatches when not strict.
    if not strict:
        if variant == "standard" and ("-" in cleaned or "_" in cleaned):
            variant = "urlsafe"
        elif variant == "urlsafe" and ("+" in cleaned or "/" in cleaned):
            variant = "standard"

    # Fix missing padding.
    pad = (-len(cleaned)) % 4
    if pad:
        cleaned_padded = cleaned + ("=" * pad)
    else:
        cleaned_padded = cleaned

    _, dec = _b64_module(variant)
    try:
        # ``validate`` is only supported by ``b64decode``, not urlsafe variants.
        if variant == "standard":
            raw = dec(cleaned_padded, validate=strict)
        else:
            if strict and not re.fullmatch(r"[A-Za-z0-9_\-]+={0,2}", cleaned):
                raise DecodeError("Invalid Base64 alphabet for urlsafe variant")
            raw = dec(cleaned_padded)
    except DecodeError:
        raise
    except Exception as exc:
        raise DecodeError(f"Invalid Base64: {exc}") from exc

    text_out: Optional[str] = None
    text_error: Optional[str] = None
    if charset:
        try:
            text_out = raw.decode(charset)
        except UnicodeDecodeError as exc:
            text_error = str(exc)
            if strict:
                raise DecodeError(
                    f"Decoded bytes are not valid {charset}: {exc}"
                ) from exc

    return {
        "result": text_out if text_out is not None else "",
        "raw_hex": raw.hex(),
        "raw_bytes": len(raw),
        "variant": variant,
        "charset": charset,
        "text_ok": text_out is not None,
        "text_error": text_error,
        "padding_added": pad,
        "input_chars": len(cleaned),
    }


def probe_base64(text: str) -> Dict[str, Any]:
    """Lightweight check: does this look like Base64?"""
    cleaned = _WS_RE.sub("", (text or "").strip())
    if not cleaned:
        return {"looks_like": False, "reason": "empty"}
    if not re.fullmatch(r"[A-Za-z0-9+/_\-]+={0,2}", cleaned):
        return {"looks_like": False, "reason": "invalid_alphabet"}
    if len(cleaned) % 4 not in (0, 2, 3):  # after optional padding fix: 0 ok
        # length % 4 == 1 is never valid
        if len(cleaned) % 4 == 1:
            return {"looks_like": False, "reason": "bad_length"}
    return {
        "looks_like": True,
        "length": len(cleaned),
        "urlsafe_chars": ("-" in cleaned or "_" in cleaned),
        "standard_chars": ("+" in cleaned or "/" in cleaned),
    }
