"""媒体处理工具核心逻辑。"""

from .image_compress import (
    CompressError,
    compress_image,
    detect_format,
    supported_formats,
)

__all__ = [
    "compress_image",
    "detect_format",
    "supported_formats",
    "CompressError",
]
