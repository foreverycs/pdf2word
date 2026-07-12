"""工具注册表：按分类组织，供首页与路由挂载。"""

from __future__ import annotations

from typing import Any, Dict, List

from .base64_tool import router as base64_router
from .pdf2word import router as pdf2word_router
from .word2pdf import router as word2pdf_router

# ---------------------------------------------------------------------------
# Categories (order = homepage display order)
# ---------------------------------------------------------------------------
TOOL_CATEGORIES: List[Dict[str, Any]] = [
    {
        "id": "document",
        "name": "文档处理",
        "name_en": "Documents",
        "description": "PDF / Word 转换与版式还原",
        "icon": "📄",
        "accent": "indigo",
        "route": "/c/document",
        "lead": "表格高保真、批量转换、可选 OCR，适合日常办公文档互转。",
    },
    {
        "id": "coding",
        "name": "编码工具",
        "name_en": "Encoding",
        "description": "Base64 等编解码与文本处理",
        "icon": "🔐",
        "accent": "amber",
        "route": "/c/coding",
        "lead": "开发调试常用的编解码能力，浏览器内即时处理。",
    },
]

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
TOOL_REGISTRY: List[Dict[str, Any]] = [
    {
        "name": "PDF 转 Word",
        "slug": "pdf2word",
        "category": "document",
        "description": "纯文本 / 表格 PDF 转 Word：合并单元格、嵌套样式、图片嵌入、可选 OCR、批量 ZIP。",
        "icon": "📄",
        "route": "/tools/pdf2word",
        "badge": "PDF → Word",
        "features": ["合并单元格", "嵌套样式", "可选 OCR", "批量 ZIP"],
        "cta": "开始转换",
        "accent": "indigo",
    },
    {
        "name": "Word 转 PDF",
        "slug": "word2pdf",
        "category": "document",
        "description": "Word（.docx / .doc）转 PDF：LibreOffice 优先，Windows 可回退 Microsoft Word。",
        "icon": "📝",
        "route": "/tools/word2pdf",
        "badge": "Word → PDF",
        "features": ["LibreOffice", ".docx / .doc", "批量 ZIP", "引擎回退"],
        "cta": "开始转换",
        "accent": "emerald",
    },
    {
        "name": "Base64 编解码",
        "slug": "base64",
        "category": "coding",
        "description": "文本 / 文件 Base64 编码与解码，支持标准与 URL-safe、换行折叠、多字符集。",
        "icon": "🔑",
        "route": "/tools/base64",
        "badge": "Encode · Decode",
        "features": ["标准 / URL-safe", "UTF-8 等", "文件编码", "一键复制"],
        "cta": "打开工具",
        "accent": "amber",
    },
]

# Routers to mount on the FastAPI app (order does not matter).
TOOL_ROUTERS = (
    pdf2word_router,
    word2pdf_router,
    base64_router,
)


def tools_by_category() -> List[Dict[str, Any]]:
    """Return categories each with a ``tools`` list (only non-empty categories)."""
    by_id = {c["id"]: {**c, "tools": []} for c in TOOL_CATEGORIES}
    for tool in TOOL_REGISTRY:
        cat = by_id.get(tool.get("category") or "")
        if cat is not None:
            cat["tools"].append(tool)
        else:
            # Unknown category → attach under a synthetic bucket.
            other = by_id.setdefault(
                "_other",
                {
                    "id": "_other",
                    "name": "其他",
                    "name_en": "Other",
                    "description": "",
                    "icon": "🧩",
                    "accent": "slate",
                    "tools": [],
                },
            )
            other["tools"].append(tool)
    return [c for c in by_id.values() if c["tools"]]


def get_tool(slug: str) -> Dict[str, Any] | None:
    for t in TOOL_REGISTRY:
        if t["slug"] == slug:
            return t
    return None


def get_category(category_id: str) -> Dict[str, Any] | None:
    """Return one category dict with its ``tools`` list, or None."""
    for cat in tools_by_category():
        if cat["id"] == category_id:
            return cat
    # Empty-but-defined category (no tools yet)
    for c in TOOL_CATEGORIES:
        if c["id"] == category_id:
            return {**c, "tools": []}
    return None


def nav_categories() -> List[Dict[str, Any]]:
    """Top-nav menu items (all registered categories, including empty)."""
    by_id = {c["id"]: c for c in tools_by_category()}
    items = []
    for c in TOOL_CATEGORIES:
        filled = by_id.get(c["id"])
        tools = list((filled or {}).get("tools") or [])
        items.append(
            {
                **c,
                "tool_count": len(tools),
                "tool_names": [t.get("name") for t in tools if t.get("name")],
                "tools": tools,
            }
        )
    return items


__all__ = [
    "TOOL_CATEGORIES",
    "TOOL_REGISTRY",
    "TOOL_ROUTERS",
    "tools_by_category",
    "get_tool",
    "get_category",
    "nav_categories",
    "pdf2word_router",
    "word2pdf_router",
    "base64_router",
]
