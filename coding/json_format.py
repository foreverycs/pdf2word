"""JSON 格式化（兼容层）。

实际实现见 :mod:`coding.code_format`。保留本模块路径，避免旧导入失效。
"""

from coding.code_format import (  # noqa: F401
    MAX_INPUT_CHARS,
    FormatError,
    JsonError,
    format_code,
    format_json,
    list_languages,
    sample_for,
    validate_code,
    validate_json,
)

__all__ = [
    "MAX_INPUT_CHARS",
    "FormatError",
    "JsonError",
    "format_code",
    "format_json",
    "list_languages",
    "sample_for",
    "validate_code",
    "validate_json",
]
