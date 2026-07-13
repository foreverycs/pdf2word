"""JSON 格式化 / 压缩 / 校验。"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

MAX_INPUT_CHARS = 2 * 1024 * 1024  # 2M chars


class JsonError(ValueError):
    """Raised when JSON input is invalid or options are wrong."""

    def __init__(
        self,
        message: str,
        *,
        line: Optional[int] = None,
        column: Optional[int] = None,
        pos: Optional[int] = None,
    ):
        super().__init__(message)
        self.line = line
        self.column = column
        self.pos = pos


def _parse_json(text: str) -> Any:
    if text is None:
        raise JsonError("请输入 JSON")
    s = text.strip()
    if not s:
        raise JsonError("请输入 JSON")
    if len(text) > MAX_INPUT_CHARS:
        raise JsonError(f"输入过长（最多 {MAX_INPUT_CHARS} 字符）")
    try:
        return json.loads(s)
    except json.JSONDecodeError as exc:
        raise JsonError(
            f"JSON 解析失败：{exc.msg}",
            line=exc.lineno,
            column=exc.colno,
            pos=exc.pos,
        ) from exc


def format_json(
    text: str,
    *,
    mode: str = "pretty",
    indent: int = 2,
    sort_keys: bool = False,
    ensure_ascii: bool = False,
) -> Dict[str, Any]:
    """Parse JSON text and re-serialize as pretty or minified form.

    Parameters
    ----------
    text:
        Raw JSON string (object, array, or primitive).
    mode:
        ``pretty`` (indented) or ``minify`` (compact).
    indent:
        Spaces per level when pretty (2 or 4 recommended; 0–8 allowed).
    sort_keys:
        Sort object keys alphabetically.
    ensure_ascii:
        If True, escape non-ASCII as ``\\uXXXX`` (default False keeps 中文).
    """
    if mode not in ("pretty", "minify"):
        raise JsonError("mode 必须是 pretty 或 minify")
    if not isinstance(indent, int) or indent < 0 or indent > 8:
        raise JsonError("indent 须在 0–8 之间")

    data = _parse_json(text)

    if mode == "minify":
        result = json.dumps(
            data,
            ensure_ascii=ensure_ascii,
            sort_keys=sort_keys,
            separators=(",", ":"),
        )
    else:
        result = json.dumps(
            data,
            ensure_ascii=ensure_ascii,
            sort_keys=sort_keys,
            indent=indent if indent > 0 else None,
            separators=(",", ": ") if indent > 0 else (",", ":"),
        )
        if indent == 0 and mode == "pretty":
            # indent 0 ≈ compact one-liner with spaces after : and ,
            result = json.dumps(
                data,
                ensure_ascii=ensure_ascii,
                sort_keys=sort_keys,
                separators=(", ", ": "),
            )

    kind = type(data).__name__
    if isinstance(data, dict):
        kind = "object"
    elif isinstance(data, list):
        kind = "array"
    elif data is None:
        kind = "null"
    elif isinstance(data, bool):
        kind = "boolean"
    elif isinstance(data, (int, float)):
        kind = "number"
    elif isinstance(data, str):
        kind = "string"

    return {
        "result": result,
        "mode": mode,
        "indent": indent if mode == "pretty" else 0,
        "sort_keys": bool(sort_keys),
        "ensure_ascii": bool(ensure_ascii),
        "type": kind,
        "input_chars": len(text),
        "output_chars": len(result),
        "valid": True,
    }


def validate_json(text: str) -> Dict[str, Any]:
    """Validate JSON without reformatting; returns ok or error location."""
    try:
        data = _parse_json(text)
    except JsonError as exc:
        return {
            "valid": False,
            "error": str(exc),
            "line": exc.line,
            "column": exc.column,
            "pos": exc.pos,
        }
    kind = "value"
    if isinstance(data, dict):
        kind = "object"
    elif isinstance(data, list):
        kind = "array"
    return {"valid": True, "type": kind, "input_chars": len(text or "")}
