"""Async job store and request-id middleware."""

from __future__ import annotations

import pytest

from core import jobs as jobs_mod
from core.jobs import JobStatus, create_job, get_job, job_public_dict, run_job, update_job
from core.request_id import get_request_id, new_request_id, reset_request_id, set_request_id


@pytest.fixture(autouse=True)
def _reset_jobs():
    jobs_mod.reset_jobs()
    yield
    jobs_mod.reset_jobs()


def test_request_id_context():
    token = set_request_id("abc123")
    try:
        assert get_request_id() == "abc123"
    finally:
        reset_request_id(token)
    assert get_request_id() is None
    assert len(new_request_id()) == 16


@pytest.mark.asyncio
async def test_job_lifecycle():
    job = await create_job("pdf2word", download_name="a.docx")
    assert job.status == JobStatus.queued
    got = await get_job(job.id)
    assert got is not None
    assert got.id == job.id

    def work():
        return {"pages": 1}

    await run_job(job.id, work)
    done = await get_job(job.id)
    assert done is not None
    assert done.status == JobStatus.done
    assert done.result == {"pages": 1}
    pub = job_public_dict(done)
    assert pub["id"] == job.id
    assert pub["status"] == "done"
    assert "output_path" not in pub


@pytest.mark.asyncio
async def test_job_error_status():
    job = await create_job("word2pdf")

    def boom():
        raise RuntimeError("fail")

    await run_job(job.id, boom)
    got = await get_job(job.id)
    assert got is not None
    assert got.status == JobStatus.error
    assert "fail" in (got.error or "")


@pytest.mark.asyncio
async def test_update_job_fields():
    job = await create_job("pdf-merge")
    await update_job(job.id, progress=0.5, message="halfway")
    got = await get_job(job.id)
    assert got is not None
    assert got.progress == 0.5
    assert got.message == "halfway"


def test_api_request_id_header_and_jobs_404():
    from fastapi.testclient import TestClient
    from app import app

    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.headers.get("X-Request-ID")

    r2 = client.get("/health", headers={"X-Request-ID": "client-rid-01"})
    assert r2.headers.get("X-Request-ID") == "client-rid-01"

    missing = client.get("/api/jobs/does-not-exist")
    assert missing.status_code == 404
    detail = missing.json().get("detail") or ""
    assert "workers" in detail.lower() or "过期" in detail or "不存在" in detail

    health = client.get("/health").json()
    assert health.get("jobs", {}).get("single_worker_required") is True


@pytest.mark.asyncio
async def test_mark_downloaded_clears_files(tmp_path):
    work = tmp_path / "jobw"
    work.mkdir()
    out = work / "out.docx"
    out.write_bytes(b"PK mock")
    job = await create_job(
        "pdf2word",
        work_dir=str(work),
        output_path=str(out),
        download_name="out.docx",
    )
    await update_job(job.id, status=JobStatus.done)
    from core.jobs import mark_downloaded

    await mark_downloaded(job.id)
    got = await get_job(job.id)
    assert got is not None
    assert got.output_path is None
    assert got.work_dir is None
    assert got.downloaded_at is not None
    assert not work.exists()
    pub = job_public_dict(got)
    assert pub["has_result"] is False
