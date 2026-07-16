"""Tests for file express (取件码) storage and HTTP API."""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def express_env(tmp_path, monkeypatch):
    d = tmp_path / "file"
    d.mkdir()
    monkeypatch.setenv("UPLOAD_FILE_DIR", str(d))
    monkeypatch.setenv("UPLOAD_RETENTION_DAYS", "5")
    monkeypatch.setenv("ALLOW_INSECURE_ADMIN", "1")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-pass")
    monkeypatch.setenv("ADMIN_SECRET", "test-secret-for-unit-tests-only")
    monkeypatch.setenv("DOTENV_OVERRIDE", "0")
    monkeypatch.setenv("EXPRESS_DEFAULT_TTL_HOURS", "24")
    monkeypatch.setenv("EXPRESS_MAX_TTL_HOURS", "168")

    import core.settings as settings_mod
    import storage.express as ex

    settings_mod.clear_settings_cache()
    ex._last_cleanup_ts = 0.0
    yield ex, d
    settings_mod.clear_settings_cache()
    ex._last_cleanup_ts = 0.0


@pytest.fixture()
def express_client(express_env, monkeypatch):
    ex, d = express_env
    import core.api_rate_limit as rl
    import core.concurrency as concurrency_mod
    import core.settings as settings_mod
    import core.tool_flags as flags_mod
    import tools.common as common
    import app as app_mod

    settings_mod.clear_settings_cache()
    concurrency_mod.reset_semaphore()
    rl.reset_all()
    common.refresh_limits()
    flags_mod.clear_tool_flags_cache()
    importlib.reload(app_mod)

    client = TestClient(app_mod.app)
    yield client, ex, d
    rl.reset_all()
    flags_mod.clear_tool_flags_cache()
    settings_mod.clear_settings_cache()


def _touch(path: Path, content: bytes = b"hello express") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_create_and_lookup_package(express_env, tmp_path):
    ex, root = express_env
    src = _touch(tmp_path / "note.txt", b"secret payload")
    pkg = ex.create_package(
        src,
        "笔记.txt",
        content_type="text/plain",
        ttl_hours=2,
        max_downloads=3,
        note="给同事",
    )
    assert pkg["code"] and len(pkg["code"]) == 6 and pkg["code"].isdigit()
    assert pkg["available"] is True
    assert pkg["max_downloads"] == 3
    assert pkg["downloads_left"] == 3
    assert pkg["note"] == "给同事"
    assert "stored_rel" not in pkg

    info = ex.get_package_by_code(pkg["code"])
    assert info is not None
    assert info["original_name"] == "笔记.txt"
    assert info["size_bytes"] == len(b"secret payload")
    path = ex.resolve_package_file(info)
    assert path is not None and path.is_file()
    assert path.read_bytes() == b"secret payload"
    # Stored under file/express/
    assert (root / "express").is_dir()


def test_claim_download_and_exhaust(express_env, tmp_path):
    ex, _ = express_env
    src = _touch(tmp_path / "once.bin", b"x" * 20)
    pkg = ex.create_package(src, "once.bin", max_downloads=1)
    code = pkg["code"]

    info1, err1 = ex.claim_download(code)
    assert err1 is None and info1 is not None
    assert info1["download_count"] == 1
    assert Path(info1["_abs_path"]).is_file()

    info2, err2 = ex.claim_download(code)
    assert err2 == "exhausted"
    assert info2 is not None and info2["exhausted"] is True


def test_invalid_code_format(express_env):
    ex, _ = express_env
    assert ex.is_valid_code_format("12345") is False
    assert ex.is_valid_code_format("1234567") is False
    # Spaces are stripped for paste-friendly codes ("12 3456" → "123456")
    assert ex.is_valid_code_format("12 3456") is True
    assert ex.is_valid_code_format("123456") is True
    assert ex.get_package_by_code("abcdef") is None
    info, err = ex.claim_download("000000")
    assert err == "invalid" and info is None


def test_cleanup_expired(express_env, tmp_path):
    ex, _ = express_env
    src = _touch(tmp_path / "old.txt", b"old")
    pkg = ex.create_package(src, "old.txt", ttl_hours=1)
    code = pkg["code"]

    import sqlite3

    db_path = ex.express_root() / "express.db"
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE packages SET expires_at = ? WHERE code = ?", (past, code)
    )
    conn.commit()
    conn.close()

    ex._last_cleanup_ts = 0.0
    removed = ex.cleanup_express(force=True)
    assert removed >= 1
    assert ex.get_package_by_code(code) is None


def test_resolve_blocks_traversal(express_env):
    ex, _ = express_env
    assert ex.resolve_package_file({"stored_rel": "../secrets"}) is None
    assert ex.resolve_package_file({"stored_rel": "..\\secrets"}) is None
    assert ex.resolve_package_file({"stored_rel": ""}) is None


def test_registry_has_express():
    from tools import TOOL_REGISTRY, TOOL_ROUTERS, express_router

    slugs = {t["slug"] for t in TOOL_REGISTRY}
    assert "express" in slugs
    tool = next(t for t in TOOL_REGISTRY if t["slug"] == "express")
    assert tool["category"] == "office"
    assert tool["route"] == "/tools/express"
    assert express_router in TOOL_ROUTERS


def test_api_send_lookup_pickup(express_client):
    client, ex, _ = express_client

    page = client.get("/tools/express")
    assert page.status_code == 200
    assert "文件快递" in page.text or "取件码" in page.text

    send = client.post(
        "/tools/express/send",
        files={"file": ("hello.txt", b"hello world", "text/plain")},
        data={"ttl_hours": "24", "max_downloads": "2", "note": "test"},
    )
    assert send.status_code == 200, send.text
    body = send.json()
    assert body["ok"] is True
    code = body["code"]
    assert len(code) == 6 and code.isdigit()
    assert body["original_name"] == "hello.txt"
    assert body["max_downloads"] == 2
    assert "pickup_url" in body

    lookup = client.post("/tools/express/lookup", data={"code": code})
    assert lookup.status_code == 200
    meta = lookup.json()
    assert meta["ok"] is True
    assert meta["code"] == code
    assert meta["size_bytes"] == len(b"hello world")
    assert "stored_rel" not in meta

    dl = client.get(f"/tools/express/pickup/{code}")
    assert dl.status_code == 200
    assert dl.content == b"hello world"
    assert "attachment" in (dl.headers.get("content-disposition") or "").lower()

    dl2 = client.post("/tools/express/pickup", data={"code": code})
    assert dl2.status_code == 200
    assert dl2.content == b"hello world"

    # max_downloads=2 exhausted
    dl3 = client.get(f"/tools/express/pickup/{code}")
    assert dl3.status_code == 410


def test_api_bad_code_and_ttl(express_client):
    client, _, _ = express_client

    bad = client.post("/tools/express/lookup", data={"code": "12"})
    assert bad.status_code == 400

    missing = client.post("/tools/express/lookup", data={"code": "999999"})
    assert missing.status_code == 404

    ttl = client.post(
        "/tools/express/send",
        files={"file": ("a.txt", b"x", "text/plain")},
        data={"ttl_hours": "99999"},
    )
    assert ttl.status_code == 400


def test_list_delete_packages_admin_api(express_env, tmp_path):
    ex, _ = express_env
    src1 = _touch(tmp_path / "a.txt", b"aaa")
    src2 = _touch(tmp_path / "b.txt", b"bbb")
    p1 = ex.create_package(src1, "a.txt", note="alpha", max_downloads=1)
    p2 = ex.create_package(src2, "b.txt", note="beta")

    listed = ex.list_packages(limit=50)
    ids = {p["id"] for p in listed}
    assert p1["id"] in ids and p2["id"] in ids
    assert all("file_exists" in p for p in listed)
    assert all(p.get("file_exists") for p in listed if p["id"] in ids)

    by_q = ex.list_packages(q="alpha")
    assert len(by_q) == 1 and by_q[0]["id"] == p1["id"]

    got = ex.get_package_by_id(p1["id"])
    assert got is not None and got["code"] == p1["code"]
    assert got["file_exists"] is True

    assert ex.delete_package(p1["id"]) is True
    assert ex.get_package_by_id(p1["id"]) is None
    assert ex.delete_packages([p2["id"], "missing"]) == 1
    assert ex.get_package_by_id(p2["id"]) is None
    assert ex.delete_packages([]) == 0


def test_list_delete_packages_admin_api(express_env, tmp_path):
    ex, _ = express_env
    src1 = _touch(tmp_path / "a.txt", b"aaa")
    src2 = _touch(tmp_path / "b.txt", b"bbb")
    p1 = ex.create_package(src1, "a.txt", note="alpha", max_downloads=1)
    p2 = ex.create_package(src2, "b.txt", note="beta")

    listed = ex.list_packages(limit=50)
    ids = {p["id"] for p in listed}
    assert p1["id"] in ids and p2["id"] in ids
    assert all("file_exists" in p for p in listed)
    assert all(p.get("file_exists") for p in listed if p["id"] in ids)

    by_q = ex.list_packages(q="alpha")
    assert len(by_q) == 1 and by_q[0]["id"] == p1["id"]

    got = ex.get_package_by_id(p1["id"])
    assert got is not None and got["code"] == p1["code"]
    assert got["file_exists"] is True

    assert ex.delete_package(p1["id"]) is True
    assert ex.get_package_by_id(p1["id"]) is None
    assert ex.delete_packages([p2["id"], "missing"]) == 1
    assert ex.get_package_by_id(p2["id"]) is None
    assert ex.delete_packages([]) == 0
