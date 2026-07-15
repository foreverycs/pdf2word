"""Global limit for heavy conversion jobs (PDF/Word/LibreOffice, invoice merge).

Provides two execution strategies:
- ``run_conversion`` — thread-based (default, low overhead, good for I/O-bound)
- ``run_conversion_process`` — process-based (bypasses GIL for CPU-bound PDF parsing)

The semaphore limits total concurrent jobs regardless of strategy.
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Optional, Tuple

from .settings import get_settings

_sem: Optional[asyncio.Semaphore] = None
_sem_limit: int = 0

# Reusable process pool (lazy init). Size matches concurrency limit.
_proc_pool: Optional[ProcessPoolExecutor] = None
_proc_pool_size: int = 0


def _get_semaphore() -> asyncio.Semaphore:
    global _sem, _sem_limit
    limit = get_settings().convert_concurrency
    if _sem is None or _sem_limit != limit:
        _sem = asyncio.Semaphore(limit)
        _sem_limit = limit
    return _sem


def _get_proc_pool() -> ProcessPoolExecutor:
    global _proc_pool, _proc_pool_size
    limit = get_settings().convert_concurrency
    if _proc_pool is None or _proc_pool_size != limit:
        if _proc_pool is not None:
            _proc_pool.shutdown(wait=False)
        _proc_pool = ProcessPoolExecutor(max_workers=limit)
        _proc_pool_size = limit
    return _proc_pool


def reset_semaphore() -> None:
    """Reset after settings reload (tests)."""
    global _sem, _sem_limit, _proc_pool, _proc_pool_size
    _sem = None
    _sem_limit = 0
    if _proc_pool is not None:
        _proc_pool.shutdown(wait=False)
        _proc_pool = None
        _proc_pool_size = 0


def shutdown_pools(*, wait: bool = False) -> None:
    """Release process-pool workers (call from app lifespan teardown)."""
    global _proc_pool, _proc_pool_size
    if _proc_pool is not None:
        _proc_pool.shutdown(wait=wait)
        _proc_pool = None
        _proc_pool_size = 0


@asynccontextmanager
async def conversion_slot() -> AsyncIterator[None]:
    """Acquire a global conversion slot; wait if the pool is full.

    Use around ``asyncio.to_thread`` (or other heavy work) so concurrent
    LibreOffice / PDF jobs cannot exhaust memory unboundedly.
    """
    sem = _get_semaphore()
    await sem.acquire()
    try:
        yield
    finally:
        sem.release()


async def run_conversion(func, /, *args, **kwargs):
    """Run ``func`` in a worker thread while holding a conversion slot."""
    async with conversion_slot():
        return await asyncio.to_thread(func, *args, **kwargs)


def _call_in_process(
    func: Callable[..., Any],
    args: Tuple[Any, ...],
    kwargs: dict,
) -> Any:
    """Top-level picklable entry for ProcessPoolExecutor (no lambdas)."""
    return func(*args, **kwargs)


async def run_conversion_process(func, /, *args, **kwargs):
    """Run ``func`` in a worker process while holding a conversion slot.

    Use for CPU-bound work (PDF parsing, image processing) where the GIL
    limits thread-based parallelism. The function and its arguments must
    be picklable (module-level callable; no lambdas, no open file handles).
    """
    pool = _get_proc_pool()
    async with conversion_slot():
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            pool, _call_in_process, func, args, kwargs
        )


# Threshold in bytes: files larger than this use process pool for PDF conversion.
_PROCESS_POOL_THRESHOLD = int(
    os.environ.get("PDF_PROCESS_POOL_THRESHOLD", str(2 * 1024 * 1024))
)


def should_use_process_pool(file_size: int) -> bool:
    """True when the file is large enough to benefit from process-based parallelism."""
    return file_size >= _PROCESS_POOL_THRESHOLD


async def run_heavy(
    func,
    /,
    *args,
    file_size: Optional[int] = None,
    force_process: bool = False,
    **kwargs,
):
    """Run ``func`` under the conversion slot; use process pool when appropriate.

    Process pool is selected when ``force_process`` is true or ``file_size``
    meets :func:`should_use_process_pool`. Otherwise uses a worker thread.
    """
    use_proc = force_process or (
        file_size is not None and should_use_process_pool(file_size)
    )
    if use_proc:
        return await run_conversion_process(func, *args, **kwargs)
    return await run_conversion(func, *args, **kwargs)
