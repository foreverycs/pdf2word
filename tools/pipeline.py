"""Shared conversion HTTP helpers: temp workspace, error mapping, archive."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from typing import Any, Dict, Optional, Tuple, Type

from fastapi import BackgroundTasks, HTTPException

from core.errors import ToolkitError
from storage import archive_conversion

_WORD_ERR: Tuple[Type[BaseException], ...]
try:
    from word2pdf import ConversionError as _WordConversionError

    _WORD_ERR = (_WordConversionError,)
except Exception:  # pragma: no cover
    _WORD_ERR = ()


class TempWorkspace:
    """Create a temp dir, clean on failure, or schedule cleanup after response."""

    __slots__ = ("prefix", "path")

    def __init__(self, prefix: str = "toolkit_"):
        self.prefix = prefix
        self.path: Optional[str] = None

    def create(self) -> str:
        self.path = tempfile.mkdtemp(prefix=self.prefix)
        return self.path

    def join(self, *parts: str) -> str:
        if not self.path:
            raise RuntimeError("TempWorkspace not created")
        return os.path.join(self.path, *parts)

    def cleanup_now(self) -> None:
        if self.path:
            shutil.rmtree(self.path, ignore_errors=True)
            self.path = None

    def schedule_cleanup(self, background_tasks: BackgroundTasks) -> None:
        if self.path:
            background_tasks.add_task(shutil.rmtree, self.path, ignore_errors=True)


def map_conversion_error(
    exc: BaseException,
    *,
    label: str = "Conversion failed",
    name_prefix: Optional[str] = None,
) -> HTTPException:
    """Map conversion-layer exceptions to HTTPException."""
    if isinstance(exc, HTTPException):
        return exc

    if isinstance(exc, ToolkitError):
        status = exc.status_code
        detail = exc.detail
    elif _WORD_ERR and isinstance(exc, _WORD_ERR):
        # Match prior word2pdf routes: engine/layout failures → 400.
        status = 400
        detail = str(exc)
    elif isinstance(exc, ValueError):
        status = 400
        detail = str(exc)
    else:
        status = 500
        detail = f"{label}: {exc}"

    if name_prefix:
        detail = f"{name_prefix}: {detail}"
    return HTTPException(status_code=status, detail=detail)


def raise_as_http(
    exc: BaseException,
    *,
    label: str = "Conversion failed",
    name_prefix: Optional[str] = None,
) -> None:
    """Raise mapped HTTPException from *exc* (always raises)."""
    raise map_conversion_error(exc, label=label, name_prefix=name_prefix) from exc


async def archive_input(
    *,
    tool: str,
    original_name: str,
    input_path: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Archive upload on a worker thread (never raises to the caller)."""
    await asyncio.to_thread(
        archive_conversion,
        tool=tool,
        original_name=original_name,
        input_path=input_path,
        extra=extra,
    )


__all__ = [
    "TempWorkspace",
    "map_conversion_error",
    "raise_as_http",
    "archive_input",
]
