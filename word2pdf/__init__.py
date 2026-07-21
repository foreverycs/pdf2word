"""Word (.docx / .doc) → PDF conversion."""

from core.errors import (
    ConversionError,
    EngineNotFoundError,
    UnsupportedFormatError,
    ValidationError,
)

from .converter import (
    available_engines,
    convert_to_pdf,
    engine_info,
    clear_engine_cache,
)

__all__ = [
    "convert_to_pdf",
    "engine_info",
    "clear_engine_cache",
    "available_engines",
    "ConversionError",
    "EngineNotFoundError",
    "UnsupportedFormatError",
    "ValidationError",
]
