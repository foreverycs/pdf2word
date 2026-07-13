"""Tests for JSON format tool and coding catalog entry."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from coding import JsonError, format_json, validate_json
from tools import TOOL_REGISTRY, tools_by_category


def test_pretty_preserves_cjk():
    raw = '{"name":"工具集","n":1}'
    r = format_json(raw, mode="pretty", indent=2)
    assert r["valid"] is True
    assert "工具集" in r["result"]
    assert "\n" in r["result"]
    assert json.loads(r["result"]) == {"name": "工具集", "n": 1}


def test_minify_and_sort_keys():
    raw = '{"b":2,"a":1}'
    r = format_json(raw, mode="minify", sort_keys=True)
    assert r["result"] == '{"a":1,"b":2}'
    assert r["mode"] == "minify"


def test_ensure_ascii():
    r = format_json('{"x":"中"}', mode="minify", ensure_ascii=True)
    assert "\\u" in r["result"]
    assert "中" not in r["result"]


def test_array_and_primitives():
    assert format_json("[1,2]")["type"] == "array"
    assert format_json("true")["type"] == "boolean"
    assert format_json("null")["type"] == "null"
    assert format_json('"hi"')["type"] == "string"


def test_invalid_json():
    with pytest.raises(JsonError) as ei:
        format_json("{bad")
    assert ei.value.line is not None


def test_validate_json():
    ok = validate_json('{"a":1}')
    assert ok["valid"] is True
    bad = validate_json("{")
    assert bad["valid"] is False
    assert bad["line"] is not None


def test_registry_has_json_in_coding():
    slugs = {t["slug"] for t in TOOL_REGISTRY}
    assert "json" in slugs
    tool = next(t for t in TOOL_REGISTRY if t["slug"] == "json")
    assert tool["category"] == "coding"
    assert tool["route"] == "/tools/json"
    cats = tools_by_category()
    coding = next(c for c in cats if c["id"] == "coding")
    coding_slugs = {t["slug"] for t in coding["tools"]}
    assert "json" in coding_slugs
    assert "base64" in coding_slugs


def test_json_page_and_apis():
    from app import app

    client = TestClient(app)
    page = client.get("/tools/json")
    assert page.status_code == 200
    assert "JSON" in page.text

    r = client.post(
        "/tools/json/format",
        data={
            "text": '{"z":1,"a":2}',
            "mode": "pretty",
            "indent": "2",
            "sort_keys": "true",
            "ensure_ascii": "false",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "object"
    parsed = json.loads(body["result"])
    assert list(parsed.keys()) == ["a", "z"]

    mini = client.post(
        "/tools/json/format",
        data={"text": '[1, 2, 3]', "mode": "minify"},
    )
    assert mini.status_code == 200
    assert mini.json()["result"] == "[1,2,3]"

    bad = client.post("/tools/json/format", data={"text": "{nope"})
    assert bad.status_code == 400

    v = client.post("/tools/json/validate", data={"text": '{"ok":true}'})
    assert v.status_code == 200
    assert v.json()["valid"] is True


def test_coding_category_lists_json():
    from app import app

    client = TestClient(app)
    coding = client.get("/c/coding")
    assert coding.status_code == 200
    assert "/tools/json" in coding.text
    assert "JSON" in coding.text
