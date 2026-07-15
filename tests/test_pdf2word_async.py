"""pdf2word async job path: submit → poll → download."""

from __future__ import annotations

import io
import os
import time
import zipfile
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from core import jobs as jobs_mod


def _make_sample_pdf(path: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

    doc = SimpleDocTemplate(path, pagesize=A4)
    data = [
        ["Name", "Info", "Score"],
        ["Alice", "Math", "90"],
        ["Bob", "Physics", "85"],
    ]
    table = Table(data, colWidths=[80, 80, 80])
    table.setStyle(
        TableStyle([
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ])
    )
    doc.build([table])


@pytest.fixture
def client(tmp_path, monkeypatch):
    """App client with isolated storage + clean job store."""
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("ALLOW_INSECURE_ADMIN", "1")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-pass")
    monkeypatch.setenv("ADMIN_SECRET", "test-secret-for-unit-tests-only")

    jobs_mod.reset_jobs()
    from app import app

    with TestClient(app) as c:
        yield c
    jobs_mod.reset_jobs()


def _wait_job(client: TestClient, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200, r.text
        last = r.json()
        if last["status"] in ("done", "error"):
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {last}")


def test_convert_async_lifecycle(client, tmp_path):
    pdf = tmp_path / "sample.pdf"
    _make_sample_pdf(str(pdf))

    with open(pdf, "rb") as f:
        resp = client.post(
            "/tools/pdf2word/convert-async",
            files={"file": ("sample.pdf", f, "application/pdf")},
            data={"page_breaks": "true"},
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["id"]
    assert body["status"] in ("queued", "running", "done")
    assert body["mode"] == "async"
    assert body["poll_url"] == f"/api/jobs/{body['id']}"
    assert body["download_url"] == f"/api/jobs/{body['id']}/download"
    assert body.get("download_name") == "sample.docx"

    # Mid-flight download should be 409 (or already done).
    early = client.get(body["download_url"])
    assert early.status_code in (200, 409)

    job = _wait_job(client, body["id"])
    assert job["status"] == "done"
    assert job["has_result"] is True
    assert job.get("result", {}).get("pages", 0) >= 1

    dl = client.get(body["download_url"])
    assert dl.status_code == 200
    assert "officedocument" in (dl.headers.get("content-type") or "")
    assert dl.headers.get("X-Pages")
    assert dl.content[:2] == b"PK"  # zip/docx magic
    assert "sample" in (dl.headers.get("content-disposition") or "").lower()


def test_convert_async_rejects_non_pdf(client, tmp_path):
    bad = tmp_path / "x.txt"
    bad.write_text("not a pdf")
    with open(bad, "rb") as f:
        resp = client.post(
            "/tools/pdf2word/convert-async",
            files={"file": ("x.txt", f, "text/plain")},
        )
    assert resp.status_code == 400


def test_convert_async_error_status(client, tmp_path):
    """Conversion failures surface as job status=error (not HTTP 500 on poll)."""
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(b"%PDF-1.4 not really a valid document content")

    with open(pdf, "rb") as f:
        resp = client.post(
            "/tools/pdf2word/convert-async",
            files={"file": ("bad.pdf", f, "application/pdf")},
        )
    # Submit may succeed (file saved) and fail in background.
    if resp.status_code == 202:
        job = _wait_job(client, resp.json()["id"], timeout=30.0)
        assert job["status"] == "error"
        assert job.get("error")
        dl = client.get(f"/api/jobs/{resp.json()['id']}/download")
        assert dl.status_code in (409, 410)
    else:
        # Or fail at save/parse boundary with 4xx/5xx — also acceptable.
        assert resp.status_code >= 400


def test_convert_batch_async_zip(client, tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    _make_sample_pdf(str(a))
    _make_sample_pdf(str(b))

    with open(a, "rb") as fa, open(b, "rb") as fb:
        resp = client.post(
            "/tools/pdf2word/convert-batch-async",
            files=[
                ("files", ("a.pdf", fa, "application/pdf")),
                ("files", ("b.pdf", fb, "application/pdf")),
            ],
            data={"page_breaks": "true"},
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["files"] == 2
    assert body["download_name"] == "pdf2word_batch.zip"

    job = _wait_job(client, body["id"])
    assert job["status"] == "done"
    assert job.get("result", {}).get("files") == 2

    dl = client.get(body["download_url"])
    assert dl.status_code == 200
    assert dl.headers.get("X-Files") == "2"
    with zipfile.ZipFile(io.BytesIO(dl.content)) as zf:
        names = zf.namelist()
        assert any(n.endswith(".docx") for n in names)
        assert len(names) == 2


def test_download_missing_job(client):
    r = client.get("/api/jobs/nope-nope/download")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_run_job_async_and_schedule():
    jobs_mod.reset_jobs()
    try:
        job = await jobs_mod.create_job(
            "pdf2word",
            download_name="x.docx",
            output_path="/tmp/fake.docx",
        )

        async def ok():
            return {
                "result": {"pages": 3, "tables": 1},
                "response_headers": {"X-Pages": "3"},
                "progress": 1.0,
            }

        await jobs_mod.run_job_async(job.id, ok)
        done = await jobs_mod.get_job(job.id)
        assert done is not None
        assert done.status == jobs_mod.JobStatus.done
        assert done.result["pages"] == 3
        assert done.response_headers["X-Pages"] == "3"
        pub = jobs_mod.job_public_dict(done)
        assert pub["has_result"] is True
        assert pub["result"]["pages"] == 3
        assert "output_path" not in pub

        # schedule_job needs a running loop (we're in asyncio test).
        job2 = await jobs_mod.create_job("pdf2word")

        async def boom():
            raise RuntimeError("scheduled fail")

        jobs_mod.schedule_job(job2.id, boom)
        # Allow the background task to run.
        for _ in range(50):
            j = await jobs_mod.get_job(job2.id)
            if j and j.status in (jobs_mod.JobStatus.done, jobs_mod.JobStatus.error):
                break
            await __import__("asyncio").sleep(0.02)
        j2 = await jobs_mod.get_job(job2.id)
        assert j2 is not None
        assert j2.status == jobs_mod.JobStatus.error
        assert "scheduled fail" in (j2.error or "")
    finally:
        jobs_mod.reset_jobs()
