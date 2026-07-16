"""工具注册表：按分类组织，供首页与路由挂载。"""

from __future__ import annotations

from typing import Any, Dict, List

from .base64_tool import router as base64_router
from .image_compress_tool import router as image_compress_router
from .json_tool import router as json_router
from .pdf2word import router as pdf2word_router
from .pdf_merge import router as pdf_merge_router
from .rmb_tool import router as rmb_router
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
        "id": "office",
        "name": "办公工具",
        "name_en": "Office",
        "description": "发票合并、金额大写、图片压缩等日常办公小工具",
        "icon": "💼",
        "accent": "emerald",
        "route": "/c/office",
        "lead": "财务与办公场景常用的小工具：发票合并、人民币大写、图片压缩等。",
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
        "name": "发票合并",
        "slug": "pdf-merge",
        "category": "office",
        "description": "两张发票合并到一张 A4 纸：上下半页、中间分割线；页内预览并直接打印。",
        "icon": "🧾",
        "route": "/tools/pdf-merge",
        "badge": "2→1 A4",
        "features": ["A4 排版", "页内预览", "一键打印", "中间分割线"],
        "cta": "开始合并",
        "accent": "violet",
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
    {
        "name": "JSON 格式化",
        "slug": "json",
        "category": "coding",
        "description": "JSON 美化缩进、压缩最小化、键排序与语法校验，默认保留中文。",
        "icon": "{ }",
        "route": "/tools/json",
        "badge": "Pretty · Minify",
        "features": ["美化 / 压缩", "键排序", "中文不转义", "错误定位"],
        "cta": "打开工具",
        "accent": "amber",
    },
    {
        "name": "人民币大写",
        "slug": "rmb",
        "category": "office",
        "description": "阿拉伯数字金额转财务规范中文大写，支持角分、千分位与货币符号。",
        "icon": "¥",
        "route": "/tools/rmb",
        "badge": "数字 → 大写",
        "features": ["角分规范", "千分位清洗", "一键复制", "即时转换"],
        "cta": "打开工具",
        "accent": "emerald",
    },
    {
        "name": "图片压缩",
        "slug": "image-compress",
        "category": "office",
        "description": "高观感压缩 JPEG / PNG / GIF / SVG：显著减小体积，尽量保持清晰与细节。",
        "icon": "🖼️",
        "route": "/tools/image-compress",
        "badge": "JPEG · PNG · GIF · SVG",
        "features": ["近无损观感", "多格式", "去元数据", "压缩对比"],
        "cta": "开始压缩",
        "accent": "violet",
    },
]

# Routers to mount on the FastAPI app (order does not matter).
TOOL_ROUTERS = (
    pdf2word_router,
    word2pdf_router,
    pdf_merge_router,
    base64_router,
    json_router,
    rmb_router,
    image_compress_router,
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
    "get_category",
    "nav_categories",
    "pdf2word_router",
    "word2pdf_router",
    "pdf_merge_router",
    "base64_router",
    "json_router",
    "rmb_router",
    "image_compress_router",
]
