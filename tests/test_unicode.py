"""Tests for Chinese Unicode restore / encode tool."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from coding import decode_unicode, encode_unicode, probe_unicode
from tools import TOOL_REGISTRY, get_tool_by_slug, tools_by_category


def test_decode_backslash_u_cjk():
    r = decode_unicode(r"\u4f60\u597d")
    assert r["result"] == "你好"
    assert r["replacements"] == 2
    assert r["changed"] is True


def test_decode_mixed_json():
    raw = r'{"name":"\u5f20\u4e09","city":"\u5317\u4eac"}'
    r = decode_unicode(raw)
    assert r["result"] == '{"name":"张三","city":"北京"}'


def test_decode_double_escape():
    # String content is \\u4e2d (two backslashes + u4e2d)
    r = decode_unicode(r"\\u4e2d\\u6587", mode="auto", max_passes=3)
    assert r["result"] == "中文"
    assert r["changed"] is True


def test_decode_es6_and_long_form():
    r = decode_unicode(r"\u{4e2d}\U00004e2d")
    assert r["result"] == "中中"


def test_decode_percent_u():
    r = decode_unicode("%u4F60%u597D", mode="percent_u")
    assert r["result"] == "你好"


def test_decode_u_plus():
    r = decode_unicode("U+4E2D U+6587", mode="u_plus")
    assert r["result"] == "中 文"


def test_decode_html_entity():
    r = decode_unicode("&#x4f60;&#x597d;", mode="html_entity")
    assert r["result"] == "你好"
    r2 = decode_unicode("&#20013;&#25991;", mode="html_entity")
    assert r2["result"] == "中文"


def test_decode_no_escape_unchanged():
    r = decode_unicode("已经是中文")
    assert r["result"] == "已经是中文"
    assert r["changed"] is False
    assert r["replacements"] == 0


def test_decode_invalid_mode():
    with pytest.raises(Exception):
        decode_unicode(r"\u4e2d", mode="nope")


def test_encode_roundtrip():
    plain = "你好，世界"
    enc = encode_unicode(plain, style="backslash_u")
    assert "\\u" in enc["result"]
    dec = decode_unicode(enc["result"])
    assert dec["result"] == plain


def test_encode_styles():
    plain = "中"
    assert encode_unicode(plain, style="backslash_u")["result"] == r"\u4e2d"
    assert encode_unicode(plain, style="u_plus", uppercase=True)["result"] == "U+4E2D"
    assert encode_unicode(plain, style="html_entity")["result"] == "&#x4e2d;"
    assert encode_unicode(plain, style="percent_u", uppercase=True)["result"] == "%u4E2D"


def test_encode_keeps_ascii():
    r = encode_unicode("A中B", style="backslash_u")
    assert r["result"] == r"A\u4e2dB"
    assert r["escaped_chars"] == 1


def test_probe_detects():
    p = probe_unicode(r"hello \u4e2d %u4E2D U+4E2D &#x4e2d;")
    assert p["likely"] is True
    assert "backslash_u" in p["detected"]
    assert "percent_u" in p["detected"]
    assert "u_plus" in p["detected"]
    assert "html_entity" in p["detected"]


def test_registry_has_unicode():
    tool = get_tool_by_slug("unicode")
    assert tool is not None
    assert tool["category"] == "coding"
    assert tool["route"] == "/tools/unicode"
    assert any(t["slug"] == "unicode" for t in TOOL_REGISTRY)

    cats = tools_by_category()
    coding = next(c for c in cats if c["id"] == "coding")
    assert any(t["slug"] == "unicode" for t in coding["tools"])


def test_unicode_page_and_decode_api():
    from app import app

    client = TestClient(app)
    page = client.get("/tools/unicode")
    assert page.status_code == 200
    assert "Unicode" in page.text or "unicode" in page.text.lower()

    r = client.post(
        "/tools/unicode/decode",
        data={"text": r"\u4f60\u597d", "mode": "auto"},
    )
    assert r.status_code == 200
    assert r.json()["result"] == "你好"


def test_unicode_encode_api():
    from app import app

    client = TestClient(app)
    r = client.post(
        "/tools/unicode/encode",
        data={"text": "中", "style": "backslash_u", "uppercase": "false"},
    )
    assert r.status_code == 200
    assert r.json()["result"] == r"\u4e2d"


def test_unicode_samples_api():
    from app import app

    client = TestClient(app)
    r = client.get("/tools/unicode/samples")
    assert r.status_code == 200
    body = r.json()
    assert body["samples"]
    assert any("你好" in (s.get("result") or "") for s in body["samples"])


def test_coding_category_lists_unicode():
    from app import app

    client = TestClient(app)
    page = client.get("/c/coding")
    assert page.status_code == 200
    assert "/tools/unicode" in page.text
