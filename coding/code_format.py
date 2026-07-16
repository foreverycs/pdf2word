"""多语言代码格式化 / 压缩 / 校验。

支持 JSON、JS/TS、Python、HTML/CSS/XML、SQL、YAML 以及 C 系等语言。
不依赖外部格式化引擎：用标准库 + 轻量缩进/结构规则，适合在线小工具场景。
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Callable, Dict, List, Optional
from xml.dom import minidom
from xml.parsers.expat import ExpatError

MAX_INPUT_CHARS = 2 * 1024 * 1024  # 2M chars


class FormatError(ValueError):
    """Raised when input is invalid or options are wrong."""

    def __init__(
        self,
        message: str,
        *,
        line: Optional[int] = None,
        column: Optional[int] = None,
        pos: Optional[int] = None,
    ):
        super().__init__(message)
        self.line = line
        self.column = column
        self.pos = pos


# Backward-compatible alias used by older imports / tests.
JsonError = FormatError


# ---------------------------------------------------------------------------
# Language catalog
# ---------------------------------------------------------------------------

LanguageDef = Dict[str, Any]

LANGUAGES: List[LanguageDef] = [
    {
        "id": "json",
        "label": "JSON",
        "group": "data",
        "modes": ["pretty", "minify"],
        "options": ["indent", "sort_keys", "ensure_ascii"],
        "validate": True,
        "description": "美化 / 压缩，键排序，中文可保留",
    },
    {
        "id": "javascript",
        "label": "JavaScript",
        "group": "web",
        "modes": ["pretty", "minify"],
        "options": ["indent"],
        "validate": False,
        "description": "括号缩进格式化",
    },
    {
        "id": "typescript",
        "label": "TypeScript",
        "group": "web",
        "modes": ["pretty", "minify"],
        "options": ["indent"],
        "validate": False,
        "description": "括号缩进格式化",
    },
    {
        "id": "python",
        "label": "Python",
        "group": "script",
        "modes": ["pretty"],
        "options": ["indent"],
        "validate": False,
        "description": "缩进规范化（不改变语义）",
    },
    {
        "id": "html",
        "label": "HTML",
        "group": "web",
        "modes": ["pretty", "minify"],
        "options": ["indent"],
        "validate": False,
        "description": "标签缩进 / 去空白",
    },
    {
        "id": "css",
        "label": "CSS",
        "group": "web",
        "modes": ["pretty", "minify"],
        "options": ["indent"],
        "validate": False,
        "description": "规则块缩进 / 压缩",
    },
    {
        "id": "xml",
        "label": "XML",
        "group": "data",
        "modes": ["pretty", "minify"],
        "options": ["indent"],
        "validate": True,
        "description": "DOM 美化 / 压缩",
    },
    {
        "id": "sql",
        "label": "SQL",
        "group": "data",
        "modes": ["pretty", "minify"],
        "options": ["indent"],
        "validate": False,
        "description": "关键字分行与缩进",
    },
    {
        "id": "yaml",
        "label": "YAML",
        "group": "data",
        "modes": ["pretty"],
        "options": ["indent"],
        "validate": False,
        "description": "缩进与空行整理",
    },
    {
        "id": "java",
        "label": "Java",
        "group": "backend",
        "modes": ["pretty", "minify"],
        "options": ["indent"],
        "validate": False,
        "description": "括号缩进格式化",
    },
    {
        "id": "go",
        "label": "Go",
        "group": "backend",
        "modes": ["pretty", "minify"],
        "options": ["indent"],
        "validate": False,
        "description": "括号缩进格式化",
    },
    {
        "id": "rust",
        "label": "Rust",
        "group": "backend",
        "modes": ["pretty", "minify"],
        "options": ["indent"],
        "validate": False,
        "description": "括号缩进格式化",
    },
    {
        "id": "php",
        "label": "PHP",
        "group": "web",
        "modes": ["pretty", "minify"],
        "options": ["indent"],
        "validate": False,
        "description": "括号缩进格式化",
    },
    {
        "id": "shell",
        "label": "Shell",
        "group": "script",
        "modes": ["pretty"],
        "options": ["indent"],
        "validate": False,
        "description": "缩进与空行整理",
    },
    {
        "id": "cpp",
        "label": "C / C++",
        "group": "backend",
        "modes": ["pretty", "minify"],
        "options": ["indent"],
        "validate": False,
        "description": "括号缩进格式化",
    },
    {
        "id": "csharp",
        "label": "C#",
        "group": "backend",
        "modes": ["pretty", "minify"],
        "options": ["indent"],
        "validate": False,
        "description": "括号缩进格式化",
    },
]

_LANG_BY_ID = {str(item["id"]): item for item in LANGUAGES}


def list_languages() -> List[Dict[str, Any]]:
    """Public catalog for UI / API."""
    return [
        {
            "id": item["id"],
            "label": item["label"],
            "group": item["group"],
            "modes": list(item["modes"]),
            "options": list(item["options"]),
            "validate": bool(item["validate"]),
            "description": item["description"],
        }
        for item in LANGUAGES
    ]


def _get_lang(language: str) -> LanguageDef:
    key = (language or "json").strip().lower()
    aliases = {
        "js": "javascript",
        "ts": "typescript",
        "py": "python",
        "yml": "yaml",
        "c": "cpp",
        "c++": "cpp",
        "cplusplus": "cpp",
        "cs": "csharp",
        "bash": "shell",
        "sh": "shell",
        "zsh": "shell",
    }
    key = aliases.get(key, key)
    if key not in _LANG_BY_ID:
        raise FormatError(f"不支持的语言：{language}")
    return _LANG_BY_ID[key]


def _check_input(text: Optional[str]) -> str:
    if text is None or not str(text).strip():
        raise FormatError("请输入代码")
    if len(text) > MAX_INPUT_CHARS:
        raise FormatError(f"输入过长（最多 {MAX_INPUT_CHARS} 字符）")
    return text


def _check_indent(indent: int) -> int:
    if not isinstance(indent, int) or indent < 0 or indent > 8:
        raise FormatError("indent 须在 0–8 之间")
    return indent


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def _json_kind(data: Any) -> str:
    if isinstance(data, dict):
        return "object"
    if isinstance(data, list):
        return "array"
    if data is None:
        return "null"
    if isinstance(data, bool):
        return "boolean"
    if isinstance(data, (int, float)):
        return "number"
    if isinstance(data, str):
        return "string"
    return type(data).__name__


def _parse_json(text: str) -> Any:
    s = text.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError as exc:
        raise FormatError(
            f"JSON 解析失败：{exc.msg}",
            line=exc.lineno,
            column=exc.colno,
            pos=exc.pos,
        ) from exc


def _format_json(
    text: str,
    *,
    mode: str,
    indent: int,
    sort_keys: bool,
    ensure_ascii: bool,
) -> Dict[str, Any]:
    data = _parse_json(text)
    if mode == "minify":
        result = json.dumps(
            data,
            ensure_ascii=ensure_ascii,
            sort_keys=sort_keys,
            separators=(",", ":"),
        )
    else:
        if indent == 0:
            result = json.dumps(
                data,
                ensure_ascii=ensure_ascii,
                sort_keys=sort_keys,
                separators=(", ", ": "),
            )
        else:
            result = json.dumps(
                data,
                ensure_ascii=ensure_ascii,
                sort_keys=sort_keys,
                indent=indent,
                separators=(",", ": "),
            )
    return {
        "result": result,
        "type": _json_kind(data),
        "sort_keys": bool(sort_keys),
        "ensure_ascii": bool(ensure_ascii),
    }


# ---------------------------------------------------------------------------
# Generic brace / bracket formatter (JS, TS, Java, Go, Rust, PHP, C/C++, C#)
# ---------------------------------------------------------------------------

_STRING_OR_COMMENT = re.compile(
    r'(?P<dqs>"(?:\\.|[^"\\])*")'
    r"|(?P<sqs>'(?:\\.|[^'\\])*')"
    r"|(?P<bts>`(?:\\.|[^`\\])*`)"
    r"|(?P<lc>//[^\n]*)"
    r"|(?P<bc>/\*.*?\*/)"
    r"|(?P<php>#[^\n]*)",
    re.DOTALL,
)


def _protect_literals(src: str) -> tuple[str, List[str]]:
    """Replace strings/comments with placeholders so braces inside are ignored."""
    store: List[str] = []

    def repl(m: re.Match[str]) -> str:
        store.append(m.group(0))
        return f"\x00{len(store) - 1}\x00"

    return _STRING_OR_COMMENT.sub(repl, src), store


def _restore_literals(src: str, store: List[str]) -> str:
    def repl(m: re.Match[str]) -> str:
        return store[int(m.group(1))]

    return re.sub(r"\x00(\d+)\x00", repl, src)


def _format_braces(text: str, *, mode: str, indent: int) -> str:
    """Brace-aware pretty / compact formatter for C-like languages."""
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    protected, store = _protect_literals(raw)

    if mode == "minify":
        # Collapse whitespace outside literals; keep single spaces where needed.
        out: List[str] = []
        i = 0
        n = len(protected)
        while i < n:
            ch = protected[i]
            if ch == "\x00":
                j = protected.find("\x00", i + 1)
                if j < 0:
                    out.append(ch)
                    i += 1
                    continue
                out.append(protected[i : j + 1])
                i = j + 1
                continue
            if ch.isspace():
                # keep one space between word chars / operators when useful
                if out and out[-1] and not out[-1][-1].isspace():
                    # peek next non-space
                    k = i
                    while k < n and protected[k].isspace():
                        k += 1
                    if k < n:
                        prev = out[-1][-1]
                        nxt = protected[k]
                        if (prev.isalnum() or prev in "_$") and (
                            nxt.isalnum() or nxt in "_$"
                        ):
                            out.append(" ")
                i += 1
                continue
            out.append(ch)
            i += 1
        compact = "".join(out)
        # Tighten common patterns
        compact = re.sub(r"\s*([{};,])\s*", r"\1", compact)
        compact = re.sub(r"\s*([{}])\s*", r"\1", compact)
        return _restore_literals(compact, store)

    # Pretty: insert newlines around braces / semicolons, then indent.
    s = protected
    s = re.sub(r"[ \t]+\n", "\n", s)
    # Normalize: ensure `{` / `}` / `;` get line breaks where helpful
    buf: List[str] = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "\x00":
            j = s.find("\x00", i + 1)
            if j < 0:
                buf.append(s[i])
                i += 1
                continue
            buf.append(s[i : j + 1])
            i = j + 1
            continue
        ch = s[i]
        if ch == "{":
            buf.append(" {\n")
            i += 1
            continue
        if ch == "}":
            buf.append("\n}\n")
            i += 1
            continue
        if ch == ";":
            buf.append(";\n")
            i += 1
            continue
        if ch == "\n":
            buf.append("\n")
            i += 1
            continue
        buf.append(ch)
        i += 1

    rough = "".join(buf)
    rough = re.sub(r"\n{3,}", "\n\n", rough)
    pad = " " * max(indent, 0) if indent > 0 else ""
    level = 0
    lines_out: List[str] = []
    for line in rough.split("\n"):
        stripped = line.strip()
        if not stripped:
            if lines_out and lines_out[-1] != "":
                lines_out.append("")
            continue
        # decrease before closing braces
        close_only = stripped.startswith("}")
        if close_only:
            level = max(level - stripped.count("}") + stripped.count("{"), 0)
            # recount: if line starts with }, drop level first
            closes = 0
            for c in stripped:
                if c == "}":
                    closes += 1
                elif c == "{":
                    break
            level = max(level - closes, 0)

        lines_out.append((pad * level if pad else "") + stripped)

        # adjust level after line
        opens = stripped.count("{")
        closes = stripped.count("}")
        if close_only:
            # already applied leading closes; apply remaining net
            # recompute net for non-leading
            net = 0
            seen_open = False
            leading_done = False
            for c in stripped:
                if not leading_done and c == "}":
                    continue
                leading_done = True
                if c == "{":
                    net += 1
                    seen_open = True
                elif c == "}":
                    net -= 1
            level = max(level + net, 0)
        else:
            level = max(level + opens - closes, 0)

    # Clean double spaces and " }" artifacts
    cleaned: List[str] = []
    for line in lines_out:
        line = re.sub(r" +\n", "\n", line)
        line = re.sub(r"  +", " ", line) if not pad else line
        # fix "foo {" already handled
        cleaned.append(line.rstrip())
    # drop trailing empty
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    pretty = "\n".join(cleaned)
    if pretty and not pretty.endswith("\n"):
        pretty += "\n"
    return _restore_literals(pretty, store)


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

def _format_python(text: str, *, indent: int) -> str:
    """Normalize indentation levels without parsing AST (keeps comments)."""
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")
    pad = " " * (indent if indent > 0 else 4)
    # Detect existing indent width from first indented non-empty line
    detected = 0
    for line in lines:
        if not line.strip():
            continue
        leading = len(line) - len(line.lstrip(" \t"))
        if leading > 0:
            # expand tabs as 4
            expanded = line.expandtabs(4)
            leading = len(expanded) - len(expanded.lstrip(" "))
            detected = leading
            break
    unit = detected if detected > 0 else (indent if indent > 0 else 4)

    out: List[str] = []
    for line in lines:
        if not line.strip():
            out.append("")
            continue
        expanded = line.expandtabs(4)
        lead = len(expanded) - len(expanded.lstrip(" "))
        level = lead // unit if unit else 0
        # preserve relative remainder roughly
        out.append(pad * level + expanded.lstrip(" "))
    # collapse 3+ blank lines
    text_out = "\n".join(out)
    text_out = re.sub(r"\n{3,}", "\n\n", text_out)
    if text_out and not text_out.endswith("\n"):
        text_out += "\n"
    return text_out


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
_RAW_TAGS = {"script", "style", "pre", "textarea", "code"}


class _HTMLPretty(HTMLParser):
    def __init__(self, indent: int) -> None:
        super().__init__(convert_charrefs=False)
        self.indent = max(indent, 0)
        self.level = 0
        self.parts: List[str] = []
        self._raw_stack: List[str] = []

    def _pad(self) -> str:
        return " " * (self.indent * self.level)

    def handle_starttag(self, tag: str, attrs) -> None:
        attr_s = "".join(
            f' {k}' if v is None else f' {k}="{v}"' for k, v in attrs
        )
        void = tag.lower() in _VOID_TAGS
        self.parts.append(f"{self._pad()}<{tag}{attr_s}{' /' if void else ''}>\n")
        if not void:
            if tag.lower() in _RAW_TAGS:
                self._raw_stack.append(tag.lower())
            self.level += 1

    def handle_startendtag(self, tag: str, attrs) -> None:
        attr_s = "".join(
            f' {k}' if v is None else f' {k}="{v}"' for k, v in attrs
        )
        self.parts.append(f"{self._pad()}<{tag}{attr_s} />\n")

    def handle_endtag(self, tag: str) -> None:
        if self._raw_stack and self._raw_stack[-1] == tag.lower():
            self._raw_stack.pop()
        self.level = max(self.level - 1, 0)
        self.parts.append(f"{self._pad()}</{tag}>\n")

    def handle_data(self, data: str) -> None:
        if self._raw_stack:
            # keep raw content, re-indent first line only lightly
            text = data
            if text.strip():
                for line in text.split("\n"):
                    if line.strip():
                        self.parts.append(f"{self._pad()}{line.rstrip()}\n")
                    elif self.parts and not self.parts[-1].endswith("\n\n"):
                        self.parts.append("\n")
            return
        text = data.strip()
        if text:
            self.parts.append(f"{self._pad()}{text}\n")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"{self._pad()}<!--{data}-->\n")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"{self._pad()}<!{decl}>\n")

    def handle_pi(self, data: str) -> None:
        self.parts.append(f"{self._pad()}<?{data}>\n")

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"{self._pad()}&{name};\n")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"{self._pad()}&#{name};\n")


def _format_html(text: str, *, mode: str, indent: int) -> str:
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    if mode == "minify":
        # crude minify: strip between tags, keep one space in text nodes lightly
        s = re.sub(r">\s+<", "><", raw.strip())
        s = re.sub(r"\s{2,}", " ", s)
        return s
    parser = _HTMLPretty(indent if indent > 0 else 2)
    try:
        parser.feed(raw)
        parser.close()
    except Exception as exc:  # noqa: BLE001 — surface as format error
        raise FormatError(f"HTML 解析失败：{exc}") from exc
    out = "".join(parser.parts).rstrip() + "\n"
    return out


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

def _format_css(text: str, *, mode: str, indent: int) -> str:
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    protected, store = _protect_literals(raw)
    if mode == "minify":
        s = re.sub(r"\s+", " ", protected)
        s = re.sub(r"\s*([{}:;,])\s*", r"\1", s)
        s = s.replace(";}", "}")
        return _restore_literals(s.strip(), store)

    pad = " " * (indent if indent > 0 else 2)
    s = protected
    s = re.sub(r"\s*\{\s*", " {\n", s)
    s = re.sub(r"\s*;\s*", ";\n", s)
    s = re.sub(r"\s*\}\s*", "\n}\n", s)
    lines: List[str] = []
    level = 0
    for line in s.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "}" or stripped.startswith("}"):
            level = max(level - 1, 0)
            lines.append(pad * level + stripped)
            continue
        lines.append(pad * level + stripped)
        if stripped.endswith("{"):
            level += 1
    out = "\n".join(lines)
    if out and not out.endswith("\n"):
        out += "\n"
    return _restore_literals(out, store)


# ---------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------

def _format_xml(text: str, *, mode: str, indent: int) -> Dict[str, Any]:
    raw = text.strip()
    try:
        dom = minidom.parseString(raw.encode("utf-8"))
    except ExpatError as exc:
        raise FormatError(
            f"XML 解析失败：{exc}",
            line=getattr(exc, "lineno", None),
            column=getattr(exc, "offset", None),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise FormatError(f"XML 解析失败：{exc}") from exc

    if mode == "minify":
        # collapse pretty whitespace between tags
        rough = dom.toxml(encoding=None)
        result = re.sub(r">\s+<", "><", rough)
    else:
        pad = " " * (indent if indent > 0 else 2)
        rough = dom.toprettyxml(indent=pad)
        # minidom adds extra blank lines — clean
        lines = [ln for ln in rough.split("\n") if ln.strip()]
        result = "\n".join(lines) + "\n"
    root = dom.documentElement.tagName if dom.documentElement else "document"
    return {"result": result, "type": f"xml:{root}"}


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_SQL_BREAK_BEFORE = re.compile(
    r"\b(SELECT|FROM|WHERE|AND|OR|JOIN|LEFT JOIN|RIGHT JOIN|INNER JOIN|"
    r"OUTER JOIN|GROUP BY|ORDER BY|HAVING|LIMIT|OFFSET|UNION|INSERT INTO|"
    r"VALUES|UPDATE|SET|DELETE FROM|CREATE TABLE|ALTER TABLE|DROP TABLE|"
    r"ON|AS)\b",
    re.IGNORECASE,
)


def _format_sql(text: str, *, mode: str, indent: int) -> str:
    raw = " ".join(text.replace("\r", "\n").split())
    if mode == "minify":
        return raw
    # Insert newlines before major keywords
    s = _SQL_BREAK_BEFORE.sub(lambda m: "\n" + m.group(0).upper(), raw)
    s = s.strip()
    pad = " " * (indent if indent > 0 else 2)
    lines: List[str] = []
    for line in s.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper.startswith(("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "WITH")):
            lines.append(stripped)
        elif upper.startswith(("FROM", "WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "OFFSET", "UNION", "SET", "VALUES")):
            lines.append(stripped)
        elif upper.startswith(("AND", "OR", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "ON")):
            lines.append(pad + stripped)
        else:
            lines.append(pad + stripped)
    out = "\n".join(lines)
    if out and not out.endswith("\n"):
        out += "\n"
    return out


# ---------------------------------------------------------------------------
# YAML / Shell — line-oriented indent normalize
# ---------------------------------------------------------------------------

def _format_indent_lines(text: str, *, indent: int) -> str:
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")
    pad = " " * (indent if indent > 0 else 2)
    detected = 0
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            # still count indent on comments
            pass
        expanded = line.expandtabs(2)
        lead = len(expanded) - len(expanded.lstrip(" "))
        if lead > 0 and not detected:
            detected = lead
            break
    unit = detected if detected > 0 else (indent if indent > 0 else 2)
    out: List[str] = []
    for line in lines:
        if not line.strip():
            out.append("")
            continue
        expanded = line.expandtabs(2)
        lead = len(expanded) - len(expanded.lstrip(" "))
        level = lead // unit if unit else 0
        out.append(pad * level + expanded.lstrip(" "))
    text_out = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    if text_out and not text_out.endswith("\n"):
        text_out += "\n"
    return text_out


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------

SAMPLES: Dict[str, str] = {
    "json": '{"name":"工具集","version":1,"features":["json","base64"],"nested":{"ok":true,"count":2},"empty":null}',
    "javascript": "function greet(name){const msg=`Hello, ${name}!`;if(!name){return null;}return msg;}\nconst user={id:1,active:true};console.log(greet('Alltools'));",
    "typescript": "interface User{id:number;name:string;}\nfunction greet(u:User):string{return `Hi ${u.name}`;}const u:User={id:1,name:'Ada'};",
    "python": "def greet(name: str) -> str:\n  if not name:\n    return ''\n  return f'Hello, {name}!'\n\nprint(greet('工具集'))\n",
    "html": "<!DOCTYPE html><html><head><title>Demo</title></head><body><h1>工具集</h1><p class=\"lead\">Hello</p></body></html>",
    "css": "body{margin:0;font-family:system-ui}.card{padding:16px;border-radius:12px}.card h1{color:#d97706}",
    "xml": '<?xml version="1.0"?><root><item id="1"><name>工具集</name><ok>true</ok></item></root>',
    "sql": "select u.id,u.name from users u join orders o on u.id=o.user_id where o.total>100 and u.active=1 order by o.created_at desc limit 20",
    "yaml": "name: 工具集\nversion: 1\nfeatures:\n- json\n- base64\nnested:\n  ok: true\n  count: 2\n",
    "java": "public class Main{public static void main(String[] args){System.out.println(\"Hello\");for(int i=0;i<3;i++){System.out.println(i);}}}",
    "go": "package main\nimport \"fmt\"\nfunc main(){x:=1;if x>0{fmt.Println(\"ok\")}}",
    "rust": "fn main(){let mut n=0;for i in 0..3{n+=i;}println!(\"{}\",n);}",
    "php": "<?php\nfunction greet($name){if(!$name){return null;}return \"Hello, $name\";}\necho greet('工具集');",
    "shell": "#!/usr/bin/env bash\nset -euo pipefail\nif [ -n \"$1\" ]; then\necho \"Hello $1\"\nelse\necho \"usage: $0 name\"\nfi\n",
    "cpp": "#include <iostream>\nint main(){int n=0;for(int i=0;i<3;++i){n+=i;}std::cout<<n<<std::endl;return 0;}",
    "csharp": "using System;class Program{static void Main(){var n=0;for(var i=0;i<3;i++){n+=i;}Console.WriteLine(n);}}",
}


def sample_for(language: str) -> str:
    lang = _get_lang(language)
    return SAMPLES.get(str(lang["id"]), SAMPLES["json"])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def format_code(
    text: str,
    *,
    language: str = "json",
    mode: str = "pretty",
    indent: int = 2,
    sort_keys: bool = False,
    ensure_ascii: bool = False,
) -> Dict[str, Any]:
    """Format source text for the given language.

    Returns a dict with at least ``result``, ``language``, ``mode``,
    ``indent``, ``input_chars``, ``output_chars``, ``valid``.
    """
    lang = _get_lang(language)
    lang_id = str(lang["id"])
    modes = list(lang["modes"])
    if mode not in ("pretty", "minify"):
        raise FormatError("mode 必须是 pretty 或 minify")
    if mode not in modes:
        # graceful fallback: languages without minify → pretty
        if mode == "minify" and "pretty" in modes:
            mode = "pretty"
        else:
            raise FormatError(f"{lang['label']} 不支持 mode={mode}")
    indent = _check_indent(indent)
    raw = _check_input(text)

    extra: Dict[str, Any] = {}
    if lang_id == "json":
        extra = _format_json(
            raw,
            mode=mode,
            indent=indent,
            sort_keys=sort_keys,
            ensure_ascii=ensure_ascii,
        )
        result = extra.pop("result")
    elif lang_id in {
        "javascript",
        "typescript",
        "java",
        "go",
        "rust",
        "php",
        "cpp",
        "csharp",
    }:
        result = _format_braces(raw, mode=mode, indent=indent)
        extra["type"] = lang_id
    elif lang_id == "python":
        result = _format_python(raw, indent=indent)
        extra["type"] = "python"
    elif lang_id == "html":
        result = _format_html(raw, mode=mode, indent=indent)
        extra["type"] = "html"
    elif lang_id == "css":
        result = _format_css(raw, mode=mode, indent=indent)
        extra["type"] = "css"
    elif lang_id == "xml":
        extra = _format_xml(raw, mode=mode, indent=indent)
        result = extra.pop("result")
    elif lang_id == "sql":
        result = _format_sql(raw, mode=mode, indent=indent)
        extra["type"] = "sql"
    elif lang_id in {"yaml", "shell"}:
        result = _format_indent_lines(raw, indent=indent)
        extra["type"] = lang_id
    else:
        raise FormatError(f"未实现的语言：{lang_id}")

    body: Dict[str, Any] = {
        "result": result,
        "language": lang_id,
        "label": lang["label"],
        "mode": mode,
        "indent": indent if mode == "pretty" else 0,
        "input_chars": len(raw),
        "output_chars": len(result),
        "valid": True,
    }
    body.update(extra)
    return body


def format_json(
    text: str,
    *,
    mode: str = "pretty",
    indent: int = 2,
    sort_keys: bool = False,
    ensure_ascii: bool = False,
) -> Dict[str, Any]:
    """Backward-compatible JSON entry point."""
    return format_code(
        text,
        language="json",
        mode=mode,
        indent=indent,
        sort_keys=sort_keys,
        ensure_ascii=ensure_ascii,
    )


def validate_code(text: str, *, language: str = "json") -> Dict[str, Any]:
    """Validate when the language supports structural parsing."""
    lang = _get_lang(language)
    lang_id = str(lang["id"])
    if text is not None and len(text) > MAX_INPUT_CHARS:
        return {
            "valid": False,
            "error": f"输入过长（最多 {MAX_INPUT_CHARS} 字符）",
            "language": lang_id,
        }
    if not (text or "").strip():
        return {
            "valid": False,
            "error": "请输入代码",
            "language": lang_id,
        }

    if lang_id == "json":
        try:
            data = _parse_json(text)
        except FormatError as exc:
            return {
                "valid": False,
                "error": str(exc),
                "line": exc.line,
                "column": exc.column,
                "pos": exc.pos,
                "language": lang_id,
            }
        return {
            "valid": True,
            "type": _json_kind(data),
            "input_chars": len(text or ""),
            "language": lang_id,
        }

    if lang_id == "xml":
        try:
            _format_xml(text, mode="pretty", indent=2)
        except FormatError as exc:
            return {
                "valid": False,
                "error": str(exc),
                "line": exc.line,
                "column": exc.column,
                "language": lang_id,
            }
        return {
            "valid": True,
            "type": "xml",
            "input_chars": len(text or ""),
            "language": lang_id,
        }

    # Soft validate: try formatting; success ⇒ "ok enough"
    try:
        format_code(text, language=lang_id, mode="pretty", indent=2)
    except FormatError as exc:
        return {
            "valid": False,
            "error": str(exc),
            "line": exc.line,
            "column": exc.column,
            "language": lang_id,
        }
    return {
        "valid": True,
        "type": lang_id,
        "input_chars": len(text or ""),
        "language": lang_id,
        "note": "已通过基础格式化检查（非严格语法校验）",
    }


def validate_json(text: str) -> Dict[str, Any]:
    """Backward-compatible JSON validator."""
    return validate_code(text, language="json")
