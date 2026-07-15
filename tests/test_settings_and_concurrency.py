"""Settings security policy and conversion concurrency."""

from __future__ import annotations

import asyncio
import time

import pytest

import core.concurrency as concurrency_mod
import core.settings as settings_mod


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("ADMIN_SECRET", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_ADMIN", raising=False)
    monkeypatch.delenv("CONVERT_CONCURRENCY", raising=False)
    settings_mod.clear_settings_cache()
    concurrency_mod.reset_semaphore()
    yield
    settings_mod.clear_settings_cache()
    concurrency_mod.reset_semaphore()


def test_missing_password_rejected():
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        settings_mod.validate_security_settings()


def test_weak_password_rejected(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "admin123")
    monkeypatch.setenv("ADMIN_SECRET", "a" * 32)
    with pytest.raises(RuntimeError, match="too weak|ADMIN_PASSWORD"):
        settings_mod.validate_security_settings()


def test_strong_credentials_accepted(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "Str0ng-Passw0rd!")
    monkeypatch.setenv("ADMIN_SECRET", "a" * 32)
    s = settings_mod.validate_security_settings()
    assert s.admin_password == "Str0ng-Passw0rd!"
    assert s.convert_concurrency >= 1


def test_insecure_mode_allows_defaults(monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_ADMIN", "1")
    s = settings_mod.validate_security_settings()
    assert s.allow_insecure_admin is True
    assert s.admin_password


def test_convert_concurrency_env(monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_ADMIN", "1")
    monkeypatch.setenv("CONVERT_CONCURRENCY", "3")
    s = settings_mod.validate_security_settings()
    assert s.convert_concurrency == 3


def test_storage_reads_settings(monkeypatch, tmp_path):
    """Upload dir / retention come from get_settings(), not import-time env."""
    d = tmp_path / "uploads"
    d.mkdir()
    monkeypatch.setenv("ALLOW_INSECURE_ADMIN", "1")
    monkeypatch.setenv("UPLOAD_FILE_DIR", str(d))
    monkeypatch.setenv("UPLOAD_RETENTION_DAYS", "9")
    settings_mod.clear_settings_cache()

    import storage.history as h

    assert h.file_dir() == d
    assert h.retention_days() == 9
    assert h.FILE_DIR == d
    assert h.RETENTION_DAYS == 9


@pytest.mark.asyncio
async def test_conversion_slot_limits_parallelism(monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_ADMIN", "1")
    monkeypatch.setenv("CONVERT_CONCURRENCY", "1")
    settings_mod.clear_settings_cache()
    concurrency_mod.reset_semaphore()

    active = 0
    max_active = 0
    lock = asyncio.Lock()

    async def job():
        nonlocal active, max_active
        async with concurrency_mod.conversion_slot():
            async with lock:
                active += 1
                max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            async with lock:
                active -= 1

    await asyncio.gather(job(), job(), job())
    assert max_active == 1


@pytest.mark.asyncio
async def test_run_conversion_runs_callable(monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_ADMIN", "1")
    monkeypatch.setenv("CONVERT_CONCURRENCY", "2")
    settings_mod.clear_settings_cache()
    concurrency_mod.reset_semaphore()

    def add(a, b):
        return a + b

    assert await concurrency_mod.run_conversion(add, 2, 3) == 5


def test_process_worker_entry_is_picklable():
    """ProcessPoolExecutor requires a top-level callable (no lambdas)."""
    import pickle

    # Round-trip the worker entry + its call signature (spawn-safe).
    payload = pickle.dumps(
        (concurrency_mod._call_in_process, pow, (2, 10), {})
    )
    fn, target, args, kwargs = pickle.loads(payload)
    assert fn(target, args, kwargs) == 1024
    # Lambdas are not picklable — that was the old bug class.
    with pytest.raises(Exception):
        pickle.dumps(lambda: 1)


@pytest.mark.asyncio
async def test_run_conversion_process_picklable(monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_ADMIN", "1")
    monkeypatch.setenv("CONVERT_CONCURRENCY", "1")
    settings_mod.clear_settings_cache()
    concurrency_mod.reset_semaphore()
    try:
        # Builtins pickle cleanly across spawn/fork.
        assert await concurrency_mod.run_conversion_process(pow, 2, 8) == 256
    finally:
        concurrency_mod.shutdown_pools(wait=False)


def test_should_use_process_pool_threshold(monkeypatch):
    monkeypatch.setenv("PDF_PROCESS_POOL_THRESHOLD", "100")
    # Re-read threshold is fixed at import; use the module constant check via API.
    assert concurrency_mod.should_use_process_pool(0) is False
    assert concurrency_mod.should_use_process_pool(
        concurrency_mod._PROCESS_POOL_THRESHOLD
    ) is True


@pytest.mark.asyncio
async def test_run_heavy_chooses_thread_for_small(monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_ADMIN", "1")
    monkeypatch.setenv("CONVERT_CONCURRENCY", "1")
    settings_mod.clear_settings_cache()
    concurrency_mod.reset_semaphore()

    calls = []

    async def fake_thread(func, /, *args, **kwargs):
        calls.append("thread")
        return func(*args, **kwargs)

    async def fake_proc(func, /, *args, **kwargs):
        calls.append("proc")
        return func(*args, **kwargs)

    monkeypatch.setattr(concurrency_mod, "run_conversion", fake_thread)
    monkeypatch.setattr(concurrency_mod, "run_conversion_process", fake_proc)

    def add(a, b):
        return a + b

    assert await concurrency_mod.run_heavy(add, 1, 2, file_size=1) == 3
    assert calls == ["thread"]

    calls.clear()
    assert await concurrency_mod.run_heavy(
        add, 1, 2, file_size=concurrency_mod._PROCESS_POOL_THRESHOLD
    ) == 3
    assert calls == ["proc"]
