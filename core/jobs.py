"""In-process async conversion job store (foundation for long-running work).

Jobs are process-local and lost on restart. Suitable for a single uvicorn
worker; multi-worker deployments need a shared backend later.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import shutil
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger("toolkit.jobs")


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    error = "error"


@dataclass
class Job:
    id: str
    tool: str
    status: JobStatus = JobStatus.queued
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    progress: float = 0.0
    message: str = ""
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    # Absolute paths owned by the job (cleaned by reclaim / error paths).
    work_dir: Optional[str] = None
    output_path: Optional[str] = None
    download_name: Optional[str] = None
    media_type: Optional[str] = None
    # Extra response headers for the download (e.g. X-Pages).
    response_headers: Optional[Dict[str, str]] = None


_jobs: Dict[str, Job] = {}
_lock = asyncio.Lock()
# Drop finished jobs after this many seconds.
_JOB_TTL_SEC = 3600.0
# Track background tasks so they are not GC'd mid-flight.
_bg_tasks: set[asyncio.Task] = set()


def _now() -> float:
    return time.time()


async def create_job(tool: str, **extra: Any) -> Job:
    jid = secrets.token_hex(12)
    job = Job(id=jid, tool=tool)
    for k, v in extra.items():
        if hasattr(job, k):
            setattr(job, k, v)
    async with _lock:
        _reclaim_unlocked()
        _jobs[jid] = job
    return job


async def get_job(job_id: str) -> Optional[Job]:
    async with _lock:
        return _jobs.get(job_id)


async def update_job(job_id: str, **fields: Any) -> Optional[Job]:
    async with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        for k, v in fields.items():
            if hasattr(job, k):
                setattr(job, k, v)
        job.updated_at = _now()
        return job


def _cleanup_work_dir(work_dir: Optional[str]) -> None:
    if work_dir:
        shutil.rmtree(work_dir, ignore_errors=True)


def _reclaim_unlocked() -> int:
    cutoff = _now() - _JOB_TTL_SEC
    dead = [
        jid
        for jid, j in _jobs.items()
        if j.status in (JobStatus.done, JobStatus.error) and j.updated_at < cutoff
    ]
    for jid in dead:
        job = _jobs.pop(jid, None)
        if job:
            _cleanup_work_dir(job.work_dir)
    return len(dead)


async def reclaim_expired() -> int:
    async with _lock:
        return _reclaim_unlocked()


async def run_job(
    job_id: str,
    func: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> None:
    """Execute ``func`` in a worker thread; update job status around it.

    ``func`` should return a dict suitable for ``Job.result`` (or None).
    """
    await update_job(job_id, status=JobStatus.running, progress=0.05, message="running")
    try:
        result = await asyncio.to_thread(func, *args, **kwargs)
        await update_job(
            job_id,
            status=JobStatus.done,
            progress=1.0,
            message="done",
            result=result if isinstance(result, dict) else {"value": result},
        )
    except Exception as exc:
        job = await get_job(job_id)
        if job and job.work_dir:
            _cleanup_work_dir(job.work_dir)
            await update_job(job_id, work_dir=None, output_path=None)
        await update_job(
            job_id,
            status=JobStatus.error,
            progress=1.0,
            message="error",
            error=str(exc) or type(exc).__name__,
        )


async def run_job_async(
    job_id: str,
    coro_factory: Callable[[], Awaitable[Optional[Dict[str, Any]]]],
) -> None:
    """Run an async coroutine for a job; apply result fields when done.

    ``coro_factory`` should return a dict of optional job field updates
    (e.g. ``result``, ``response_headers``, ``output_path``) or None.
    On failure the job ``work_dir`` is deleted.
    """
    await update_job(
        job_id, status=JobStatus.running, progress=0.05, message="running"
    )
    try:
        updates = await coro_factory()
        fields: Dict[str, Any] = {
            "status": JobStatus.done,
            "progress": 1.0,
            "message": "done",
        }
        if isinstance(updates, dict):
            fields.update(updates)
            if "result" in updates and not isinstance(updates.get("result"), dict):
                fields["result"] = {"value": updates["result"]}
        await update_job(job_id, **fields)
    except Exception as exc:
        logger.exception("job_failed id=%s", job_id)
        job = await get_job(job_id)
        if job and job.work_dir:
            _cleanup_work_dir(job.work_dir)
            await update_job(job_id, work_dir=None, output_path=None)
        detail = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
        await update_job(
            job_id,
            status=JobStatus.error,
            progress=1.0,
            message="error",
            error=str(detail),
        )


def schedule_job(
    job_id: str,
    coro_factory: Callable[[], Awaitable[Optional[Dict[str, Any]]]],
) -> None:
    """Fire-and-forget ``run_job_async`` on the running event loop."""

    async def _runner() -> None:
        await run_job_async(job_id, coro_factory)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop (sync tests): run inline is not possible for async factory.
        raise RuntimeError("schedule_job requires a running event loop")

    task = loop.create_task(_runner())
    _bg_tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _bg_tasks.discard(t)
        try:
            exc = t.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            logger.error("background job task crashed id=%s: %s", job_id, exc)

    task.add_done_callback(_done)


def job_public_dict(job: Job) -> Dict[str, Any]:
    """JSON-safe view for clients (no absolute paths)."""
    body: Dict[str, Any] = {
        "id": job.id,
        "tool": job.tool,
        "status": job.status.value,
        "progress": job.progress,
        "message": job.message,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "has_result": job.status == JobStatus.done and bool(job.output_path),
        "download_name": job.download_name,
        "media_type": job.media_type,
    }
    if job.result:
        # Expose safe stats for UI (pages, tables, warnings, …).
        safe = {
            k: v
            for k, v in job.result.items()
            if k
            in (
                "pages",
                "tables",
                "text_blocks",
                "images",
                "lines",
                "warnings",
                "files",
                "batch",
            )
        }
        if safe:
            body["result"] = safe
    return body


def reset_jobs() -> None:
    """Clear all jobs (tests)."""
    for job in list(_jobs.values()):
        _cleanup_work_dir(job.work_dir)
    _jobs.clear()
    for t in list(_bg_tasks):
        t.cancel()
    _bg_tasks.clear()
