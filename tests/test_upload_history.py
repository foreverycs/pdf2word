"""Tests for upload history under file/."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture()
def hist_dir(tmp_path, monkeypatch):
    d = tmp_path / "file"
    d.mkdir()
    monkeypatch.setenv("UPLOAD_FILE_DIR", str(d))
    monkeypatch.setenv("UPLOAD_RETENTION_DAYS", "5")
    # Reload module paths after env change
    import importlib
    import storage.history as h
    import storage as s

    importlib.reload(h)
    importlib.reload(s)
    yield h
    # leave tmp_path to pytest cleanup


def _touch(path: Path, content: bytes = b"hello") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_archive_and_list(hist_dir, tmp_path):
    h = hist_dir
    src = _touch(tmp_path / "a.pdf", b"%PDF-in")
    out = _touch(tmp_path / "a.docx", b"PK-out")

    rec = h.archive_conversion(
        tool="pdf2word",
        original_name="报告.pdf",
        input_path=str(src),
        # legacy kwargs must be ignored (input-only archive)
        output_path=str(out),
        output_name="报告.docx",
        extra={"pages": 1},
    )
    assert rec is not None
    assert rec["tool"] == "pdf2word"
    assert rec["original_name"] == "报告.pdf"
    assert (h.FILE_DIR / rec["input_rel"]).is_file()
    assert rec.get("output_rel") in (None, "")
    # no *_out* files written
    outs = list(h.FILE_DIR.rglob("*_out*"))
    assert outs == []

    items = h.list_records()
    assert len(items) >= 1
    assert items[0]["id"] == rec["id"]
    assert items[0]["input_exists"] is True


def test_cleanup_expired(hist_dir, tmp_path):
    h = hist_dir
    src = _touch(tmp_path / "old.pdf", b"old")
    rec = h.archive_conversion(
        tool="pdf2word",
        original_name="old.pdf",
        input_path=str(src),
    )
    assert rec is not None

    # Backdate the record past retention
    path = h.FILE_DIR / "records.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    old_ts = (datetime.now(timezone.utc) - timedelta(days=6)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    data[0]["created_at"] = old_ts
    # also put files under an old day folder name is fine; cleanup uses created_at
    path.write_text(json.dumps(data), encoding="utf-8")

    removed = h.cleanup_expired()
    assert removed >= 1
    items = h.list_records()
    assert all(r["id"] != rec["id"] for r in items)


def test_resolve_blocks_traversal(hist_dir):
    h = hist_dir
    assert h.resolve_stored("../secrets") is None
    assert h.resolve_stored("..\\secrets") is None


def test_api_uploads_requires_admin(hist_dir, tmp_path, monkeypatch):
    h = hist_dir
    src = _touch(tmp_path / "x.pdf", b"data")
    rec = h.archive_conversion(
        tool="pdf2word",
        original_name="x.pdf",
        input_path=str(src),
    )
    assert rec is not None

    monkeypatch.setenv("ADMIN_PASSWORD", "test-pass")
    monkeypatch.setenv("ADMIN_SECRET", "test-secret")

    from fastapi.testclient import TestClient
    import app as app_mod
    import admin.auth as auth
    import admin.routes as routes
    import importlib

    importlib.reload(auth)
    importlib.reload(routes)
    importlib.reload(app_mod)
    client = TestClient(app_mod.app)

    # Unauthenticated: list and download must not leak history/files
    r = client.get("/api/uploads")
    assert r.status_code == 401
    dl = client.get(f"/api/uploads/{rec['id']}/download")
    assert dl.status_code == 401

    # Authenticated admin may list and download
    login = client.post(
        "/admin/login",
        data={"password": "test-pass", "next": "/admin"},
        follow_redirects=False,
    )
    assert login.status_code in (303, 307, 302)

    r = client.get("/api/uploads")
    assert r.status_code == 200
    body = r.json()
    assert body["retention_days"] == 5
    assert any(i["original_name"] == "x.pdf" for i in body["items"])

    dl = client.get(f"/api/uploads/{rec['id']}/download")
    assert dl.status_code == 200
    assert dl.content == b"data"

    home = client.get("/")
    assert home.status_code == 200
    assert "工具集" in home.text
    assert "最近上传" not in home.text
