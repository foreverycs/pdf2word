"""人民币金额（阿拉伯数字）→ 中文大写。"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Union

_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_UNITS_SMALL = ("", "拾", "佰", "仟")
_SECTION_UNITS = ("", "万", "亿", "兆")  # 每 4 位一节

_CLEAN_RE = re.compile(r"[\s,，￥¥元]")
_FULLWIDTH_TRANS = str.maketrans("０１２３４５６７８９．", "0123456789.")

# Practical upper bound: within 兆 section (16 integer digits)
_MAX_YUAN = Decimal("9999999999999999.99")


class AmountError(ValueError):
    """Raised when the amount cannot be converted."""


def _normalize_input(raw: str) -> str:
    s = (raw or "").strip().translate(_FULLWIDTH_TRANS)
    s = _CLEAN_RE.sub("", s)
    if s.startswith("+"):
        s = s[1:]
    return s


def _section_to_cn(n: int) -> str:
    """Convert 0..9999 to Chinese uppercase (no section unit)."""
    if n <= 0:
        return ""
    parts: list[str] = []
    zero_pending = False
    for power in (3, 2, 1, 0):
        d = (n // (10 ** power)) % 10
        unit = _UNITS_SMALL[power]
        if d == 0:
            if parts:
                zero_pending = True
            continue
        if zero_pending:
            parts.append("零")
            zero_pending = False
        parts.append(_DIGITS[d] + unit)
    return "".join(parts)


def _integer_to_cn(n: int) -> str:
    """Convert non-negative integer yuan part to Chinese uppercase (without 元)."""
    if n == 0:
        return "零"

    sections: list[int] = []
    x = n
    while x > 0:
        sections.append(x % 10000)
        x //= 10000

    # sections[0]=个, [1]=万, [2]=亿, [3]=兆 — walk high → low
    out: list[str] = []
    prev_idx: int | None = None
    for i in range(len(sections) - 1, -1, -1):
        sec = sections[i]
        if sec == 0:
            continue
        unit = _SECTION_UNITS[i] if i < len(_SECTION_UNITS) else ""
        cn = _section_to_cn(sec)
        if prev_idx is not None:
            gap = prev_idx - i - 1
            # Gap of whole zero sections, or current section missing 仟 → insert 零
            if gap > 0 or sec < 1000:
                if not (out and out[-1].endswith("零")):
                    out.append("零")
        out.append(cn + unit)
        prev_idx = i
    return "".join(out)


def _fraction_to_cn(jiao: int, fen: int) -> str:
    """jiao/fen digits 0-9 → suffix after 元 (empty means caller should add 整)."""
    if jiao == 0 and fen == 0:
        return ""
    if jiao == 0:
        return f"零{_DIGITS[fen]}分"
    if fen == 0:
        return f"{_DIGITS[jiao]}角"
    return f"{_DIGITS[jiao]}角{_DIGITS[fen]}分"


def to_rmb_upper(
    amount: Union[str, int, float, Decimal],
    *,
    prefix: bool = True,
) -> Dict[str, Any]:
    """Convert a numeric amount to Chinese RMB uppercase form.

    Parameters
    ----------
    amount:
        Number or string (supports ``1,234.56``, ``￥100``, full-width digits).
    prefix:
        If True, prepend ``人民币``.

    Returns
    -------
    dict with keys: result, amount, input, prefix, yuan, jiao, fen
    """
    if isinstance(amount, bool):
        raise AmountError("金额格式无效")

    if isinstance(amount, (int, float, Decimal)):
        if isinstance(amount, float):
            try:
                dec = Decimal(str(amount))
            except InvalidOperation as exc:
                raise AmountError("金额格式无效") from exc
            raw_input = str(amount)
        elif isinstance(amount, Decimal):
            dec = amount
            raw_input = format(amount, "f")
        else:
            dec = Decimal(amount)
            raw_input = str(amount)
    else:
        raw_input = str(amount)
        cleaned = _normalize_input(raw_input)
        if not cleaned:
            raise AmountError("请输入金额")
        if cleaned.startswith("-"):
            raise AmountError("暂不支持负数金额")
        if cleaned.count(".") > 1:
            raise AmountError("金额格式无效")
        try:
            dec = Decimal(cleaned)
        except InvalidOperation as exc:
            raise AmountError("金额格式无效") from exc

    if dec < 0:
        raise AmountError("暂不支持负数金额")
    if dec > _MAX_YUAN:
        raise AmountError("金额过大（最大支持到千万亿级）")

    quantized = dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    cents = int(quantized * 100)
    yuan = cents // 100
    rem = cents % 100
    jiao = rem // 10
    fen = rem % 10

    int_cn = _integer_to_cn(yuan)
    frac_cn = _fraction_to_cn(jiao, fen)
    if frac_cn:
        body = f"{int_cn}元{frac_cn}"
    else:
        body = f"{int_cn}元整"

    result = ("人民币" if prefix else "") + body
    amount_str = f"{yuan}.{jiao}{fen}"

    return {
        "result": result,
        "amount": amount_str,
        "input": raw_input if isinstance(amount, str) else amount_str,
        "prefix": bool(prefix),
        "yuan": yuan,
        "jiao": jiao,
        "fen": fen,
    }
