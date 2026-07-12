"""编码 / 编解码工具核心逻辑。"""

from .base64_codec import (
    DecodeError,
    decode_base64,
    encode_base64,
    probe_base64,
)

__all__ = [
    "encode_base64",
    "decode_base64",
    "probe_base64",
    "DecodeError",
]
