"""Tests for Base64 coding tools and toolkit catalog."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from coding import DecodeError, decode_base64, encode_base64, probe_base64
from tools import TOOL_REGISTRY, tools_by_category


def test_encode_decode_roundtrip_cjk():
    text = "Hello, 工具集!"
    enc = encode_base64(text, charset="utf-8")
    assert enc["input_kind"] == "text"
    assert enc["result"]
    dec = decode_base64(enc["result"], charset="utf-8")
    assert dec["text_ok"] is True
    assert dec["result"] == text


def test_encode_urlsafe_and_wrap():
    text = "a" * 100
    enc = encode_base64(text, variant="urlsafe", wrap=64)
    assert "\n" in enc["result"]
    assert "-" in enc["result"] or "_" in enc["result"] or enc["result"].isalnum() or "=" in enc["result"]
    dec = decode_base64(enc["result"], variant="urlsafe")
    assert dec["result"] == text


def test_decode_auto_variant_and_padding():
    # Standard alphabet without padding
    raw = base64.b64encode(b"hi").decode("ascii").rstrip("=")
    dec = decode_base64(raw, variant="standard")
    assert dec["result"] == "hi"
    assert dec["padding_added"] >= 0


def test_decode_invalid_raises():
    with pytest.raises(DecodeError):
        decode_base64("@@@not-base64@@@", strict=True)


def test_encode_bytes():
    enc = encode_base64(b"\x00\xff\xfe", charset="utf-8")
    assert enc["input_kind"] == "bytes"
    assert enc["input_bytes"] == 3
    dec = decode_base64(enc["result"], charset=None)
    assert dec["raw_bytes"] == 3
    assert dec["raw_hex"] == "00fffe"


def test_probe_base64():
    assert probe_base64("")["looks_like"] is False
    assert probe_base64("SGVsbG8=")["looks_like"] is True
    assert probe_base64("hello world!!!")["looks_like"] is False


def test_registry_has_categories():
    cats = tools_by_category()
    ids = {c["id"] for c in cats}
    assert "document" in ids
    assert "coding" in ids
    slugs = {t["slug"] for t in TOOL_REGISTRY}
    assert "pdf2word" in slugs
    assert "word2pdf" in slugs
    assert "base64" in slugs
    for t in TOOL_REGISTRY:
        assert "category" in t
        assert "route" in t


def test_api_tools_catalog():
    from app import app

    client = TestClient(app)
    r = client.get("/api/tools")
    assert r.status_code == 200
    body = r.json()
    assert body["tools"]
    assert any(c["id"] == "coding" for c in body["categories"])


def test_home_lists_categories_and_base64():
    from app import app

    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "文档处理" in r.text
    assert "编码工具" in r.text
    assert "/c/document" in r.text
    assert "/c/coding" in r.text
    assert "工具集" in r.text


def test_category_pages():
    from app import app

    client = TestClient(app)
    doc = client.get("/c/document")
    assert doc.status_code == 200
    assert "文档处理" in doc.text
    assert "/tools/pdf2word" in doc.text
    assert "/tools/word2pdf" in doc.text
    # Category page should highlight its nav item
    assert 'aria-current="page"' in doc.text

    coding = client.get("/c/coding")
    assert coding.status_code == 200
    assert "编码工具" in coding.text
    assert "/tools/base64" in coding.text

    missing = client.get("/c/not-a-category")
    assert missing.status_code == 404


def test_category_aliases():
    from app import app

    client = TestClient(app)
    r = client.get("/documents", follow_redirects=False)
    assert r.status_code in (307, 302)
    assert r.headers["location"] == "/c/document"
    r2 = client.get("/coding", follow_redirects=False)
    assert r2.status_code in (307, 302)
    assert r2.headers["location"] == "/c/coding"


def test_base64_page_and_encode_api():
    from app import app

    client = TestClient(app)
    page = client.get("/tools/base64")
    assert page.status_code == 200
    assert "Base64" in page.text

    r = client.post(
        "/tools/base64/encode",
        data={"text": "abc", "charset": "utf-8", "variant": "standard", "wrap": "0"},
    )
    assert r.status_code == 200
    assert r.json()["result"] == base64.b64encode(b"abc").decode("ascii")


def test_base64_decode_api():
    from app import app

    client = TestClient(app)
    b64 = base64.b64encode("你好".encode("utf-8")).decode("ascii")
    r = client.post(
        "/tools/base64/decode",
        data={"text": b64, "charset": "utf-8", "variant": "standard"},
    )
    assert r.status_code == 200
    assert r.json()["result"] == "你好"


def test_base64_encode_file():
    from app import app

    client = TestClient(app)
    r = client.post(
        "/tools/base64/encode",
        data={"variant": "standard", "wrap": "0"},
        files={"file": ("t.bin", b"\x01\x02\x03", "application/octet-stream")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == base64.b64encode(b"\x01\x02\x03").decode("ascii")
    assert body["filename"] == "t.bin"


def test_health_includes_categories():
    from app import app

    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["tools"] >= 3
    assert any(c["id"] == "document" for c in body["categories"])
    assert any(c["id"] == "coding" for c in body["categories"])
