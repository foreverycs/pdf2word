"""代码格式化 / JSON 兼容 API 测试。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from coding import (
    FormatError,
    format_code,
    format_json,
    list_languages,
    validate_code,
    validate_json,
)


def _client() -> TestClient:
    from app import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# Unit: language catalog
# ---------------------------------------------------------------------------

def test_list_languages_includes_json_and_js():
    langs = list_languages()
    ids = {item["id"] for item in langs}
    assert "json" in ids
    assert "javascript" in ids
    assert "python" in ids
    assert "html" in ids
    json_lang = next(x for x in langs if x["id"] == "json")
    assert "pretty" in json_lang["modes"]
    assert "minify" in json_lang["modes"]
    assert json_lang["validate"] is True


# ---------------------------------------------------------------------------
# Unit: JSON (backward compatible)
# ---------------------------------------------------------------------------

def test_format_json_pretty_preserves_chinese():
    src = '{"name":"工具集","n":1}'
    out = format_json(src, mode="pretty", indent=2, ensure_ascii=False)
    assert "工具集" in out["result"]
    assert out["type"] == "object"
    assert out["valid"] is True
    assert out["language"] == "json"
    parsed = json.loads(out["result"])
    assert parsed["name"] == "工具集"


def test_format_json_minify_and_sort_keys():
    src = '{"b":2,"a":1}'
    out = format_json(src, mode="minify", sort_keys=True)
    assert out["result"] == '{"a":1,"b":2}'
    assert out["mode"] == "minify"


def test_format_json_invalid_raises():
    with pytest.raises(FormatError) as ei:
        format_json("{bad")
    assert ei.value.line is not None


def test_validate_json_ok_and_bad():
    ok = validate_json('{"x": true}')
    assert ok["valid"] is True
    assert ok["type"] == "object"
    bad = validate_json("{")
    assert bad["valid"] is False
    assert "error" in bad


# ---------------------------------------------------------------------------
# Unit: multi-language format_code
# ---------------------------------------------------------------------------

def test_format_javascript_pretty():
    out = format_code(
        "function x(){return 1;}",
        language="javascript",
        mode="pretty",
        indent=2,
    )
    assert "function" in out["result"]
    assert out["language"] == "javascript"
    assert "{" in out["result"]


def test_format_python_indent():
    out = format_code(
        "def f():\n  return 1\n",
        language="python",
        mode="pretty",
        indent=4,
    )
    assert "def f():" in out["result"]
    assert "    return 1" in out["result"]


def test_format_html_pretty():
    out = format_code(
        "<div><p>hi</p></div>",
        language="html",
        mode="pretty",
        indent=2,
    )
    assert "<div>" in out["result"]
    assert "<p>" in out["result"]


def test_format_css_pretty():
    out = format_code("body{color:red}", language="css", mode="pretty", indent=2)
    assert "body" in out["result"]
    assert "color" in out["result"]


def test_format_xml_pretty():
    out = format_code(
        "<root><item>1</item></root>",
        language="xml",
        mode="pretty",
        indent=2,
    )
    assert "root" in out["result"]
    assert out["type"].startswith("xml")


def test_format_sql_pretty():
    out = format_code(
        "select a from t where x=1",
        language="sql",
        mode="pretty",
        indent=2,
    )
    assert "SELECT" in out["result"].upper()
    assert "FROM" in out["result"].upper()


def test_format_yaml_pretty():
    out = format_code(
        "a: 1\nb:\n- x\n",
        language="yaml",
        mode="pretty",
        indent=2,
    )
    assert "a:" in out["result"]


def test_format_unknown_language():
    with pytest.raises(FormatError):
        format_code("x", language="brainfuck")


def test_format_empty_raises():
    with pytest.raises(FormatError):
        format_code("   ", language="json")


def test_js_alias():
    out = format_code("var a=1;", language="js", mode="pretty")
    assert out["language"] == "javascript"


def test_validate_code_xml():
    ok = validate_code("<a/>", language="xml")
    assert ok["valid"] is True
    bad = validate_code("<a>", language="xml")
    assert bad["valid"] is False


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

def test_page_renders_language_tabs():
    res = _client().get("/tools/json")
    assert res.status_code == 200
    assert "代码格式化" in res.text
    assert "lang-tab" in res.text
    assert "JavaScript" in res.text
    assert "Python" in res.text


def test_api_languages():
    res = _client().get("/tools/json/languages")
    assert res.status_code == 200
    data = res.json()
    assert "languages" in data
    ids = {x["id"] for x in data["languages"]}
    assert "json" in ids
    assert "typescript" in ids


def test_api_format_json():
    res = _client().post(
        "/tools/json/format",
        data={
            "text": '{"z":1,"a":2}',
            "language": "json",
            "mode": "pretty",
            "indent": "2",
            "sort_keys": "true",
            "ensure_ascii": "false",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["language"] == "json"
    assert '"a"' in body["result"]
    # sorted: a before z
    assert body["result"].index('"a"') < body["result"].index('"z"')


def test_api_format_javascript():
    res = _client().post(
        "/tools/json/format",
        data={
            "text": "function f(){return 1;}",
            "language": "javascript",
            "mode": "pretty",
            "indent": "2",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["language"] == "javascript"
    assert "function" in body["result"]


def test_api_format_invalid_json():
    res = _client().post(
        "/tools/json/format",
        data={"text": "{", "language": "json", "mode": "pretty"},
    )
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "message" in detail or isinstance(detail, str)


def test_api_validate():
    res = _client().post(
        "/tools/json/validate",
        data={"text": '{"ok": true}', "language": "json"},
    )
    assert res.status_code == 200
    assert res.json()["valid"] is True


def test_api_sample_per_language():
    client = _client()
    for lang in ("json", "python", "sql", "html"):
        res = client.get(f"/tools/json/sample?language={lang}")
        assert res.status_code == 200
        assert res.json()["sample"]


def test_registry_lists_code_format_tool():
    from tools import TOOLS

    tool = next(t for t in TOOLS if t["slug"] == "json")
    assert "代码" in tool["name"] or "格式化" in tool["name"]
    assert tool["route"] == "/tools/json"
