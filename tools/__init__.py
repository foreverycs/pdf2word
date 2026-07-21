"""工具注册表：按分类组织，供首页与路由挂载。"""

from __future__ import annotations

from typing import Any, Dict, List

from .base64_tool import router as base64_router
from .code_format_tool import router as code_format_router
from .express_tool import router as express_router
from .image_compress_tool import router as image_compress_router
from .image_convert_tool import router as image_convert_router
from .json_tool import router as json_legacy_router
from .markdown_tool import router as markdown_router
from .pdf2word import router as pdf2word_router
from .pdf_merge import router as pdf_merge_router
from .rmb_tool import router as rmb_router
from .unicode_tool import router as unicode_router
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
        "description": "发票合并、金额大写、图片压缩与格式转换等日常办公小工具",
        "icon": "💼",
        "accent": "emerald",
        "route": "/c/office",
        "lead": "财务与办公场景常用的小工具：发票合并、人民币大写、图片压缩与格式转换等。",
    },
    {
        "id": "coding",
        "name": "编码工具",
        "name_en": "Encoding",
        "description": "Base64、Unicode、JSON、Markdown 等编解码与文本处理",
        "icon": "🔐",
        "accent": "amber",
        "route": "/c/coding",
        "lead": "开发调试常用的编解码与文本预览能力，浏览器内即时处理。",
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
        "name": "中文 Unicode 还原",
        "slug": "unicode",
        "category": "coding",
        "description": "将 \\uXXXX、U+XXXX、HTML 实体等 Unicode 转义还原为中文，也可反向编码。",
        "icon": "文",
        "route": "/tools/unicode",
        "badge": "\\u → 中文",
        "features": ["\\uXXXX 还原", "双重转义", "U+ / HTML", "反向编码"],
        "cta": "打开工具",
        "accent": "amber",
    },
    {
        "name": "代码格式化",
        "slug": "code-format",
        "category": "coding",
        "description": "多语言代码美化 / 压缩（JSON、JS/TS、Python、HTML/CSS/XML、SQL、YAML 等），选项卡切换。",
        "icon": "{ }",
        "route": "/tools/code-format",
        "badge": "Multi-lang",
        "features": ["多语言选项卡", "美化 / 压缩", "JSON 键排序", "错误定位"],
        "cta": "打开工具",
        "accent": "amber",
    },
    {
        "name": "Markdown 编辑",
        "slug": "markdown",
        "category": "coding",
        "description": "Markdown 左右分栏编辑与实时 HTML 预览，支持表格、代码块，可导出 HTML。",
        "icon": "MD",
        "route": "/tools/markdown",
        "badge": "Edit · Preview",
        "features": ["实时预览", "表格 / 代码块", "XSS 过滤", "导出 HTML"],
        "cta": "打开编辑器",
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
    {
        "name": "图片格式转换",
        "slug": "image-convert",
        "category": "office",
        "description": "JPEG / PNG / WebP / GIF / BMP / TIFF / ICO 互转：透明铺底、动图保留、质量可调。",
        "icon": "🔄",
        "route": "/tools/image-convert",
        "badge": "JPEG · PNG · WebP · …",
        "features": ["七种格式", "保留透明", "动图支持", "质量可调"],
        "cta": "开始转换",
        "accent": "sky",
    },
    {
        "name": "文件快递",
        "slug": "express",
        "category": "office",
        # Featured: shown as a homepage highlight, not listed under module grids.
        "featured": True,
        "description": "上传文件生成 6 位取件码，对方输入取件码即可下载；可设有效期与下载次数。",
        "icon": "📦",
        "route": "/tools/express",
        "badge": "特色 · 取件码分享",
        "features": ["6 位取件码", "有效期", "下载次数", "一键复制"],
        "cta": "开始寄送",
        "accent": "indigo",
        "lead": "临时传文件无需账号：生成取件码，对方输入即可下载。",
    },
]

# Routers to mount on the FastAPI app (order does not matter).
# code_format first; json_legacy only provides 308 redirects for old URLs.
TOOL_ROUTERS = (
    pdf2word_router,
    word2pdf_router,
    pdf_merge_router,
    base64_router,
    code_format_router,
    json_legacy_router,
    markdown_router,
    unicode_router,
    rmb_router,
    image_compress_router,
    image_convert_router,
    express_router,
)


def is_featured_tool(tool: Dict[str, Any] | None) -> bool:
    """True when a registry entry is a homepage feature (not a module card)."""
    if not tool:
        return False
    return bool(tool.get("featured"))


def enabled_tools(*, include_featured: bool = False) -> List[Dict[str, Any]]:
    """Public catalog: tools not disabled in admin flags.

    Featured tools (e.g. 文件快递) are omitted by default so module grids and
    category pages only show regular tools. Pass ``include_featured=True`` for
    counts / APIs that need the full public set.
    """
    from core.tool_flags import get_disabled_slugs

    disabled = get_disabled_slugs()
    out = []
    for t in TOOL_REGISTRY:
        if str(t.get("slug") or "") in disabled:
            continue
        if is_featured_tool(t) and not include_featured:
            continue
        out.append(t)
    return out


def featured_tools() -> List[Dict[str, Any]]:
    """Enabled tools marked ``featured=True`` (homepage highlight strip)."""
    from core.tool_flags import get_disabled_slugs

    disabled = get_disabled_slugs()
    return [
        t
        for t in TOOL_REGISTRY
        if is_featured_tool(t) and str(t.get("slug") or "") not in disabled
    ]


def tools_by_category(
    *, include_disabled: bool = False, include_featured: bool = False
) -> List[Dict[str, Any]]:
    """Return categories each with a ``tools`` list (only non-empty categories).

    By default disabled and featured tools are omitted (homepage / public API).
    Pass ``include_disabled=True`` for the admin console (includes featured).
    Pass ``include_featured=True`` to place featured tools back under categories.
    """
    if include_disabled:
        # Full registry for admin (featured tools stay under their category).
        source = list(TOOL_REGISTRY)
    else:
        source = enabled_tools(include_featured=include_featured)
    by_id = {c["id"]: {**c, "tools": []} for c in TOOL_CATEGORIES}
    for tool in source:
        # Public category grids never list featured tools unless asked.
        if is_featured_tool(tool) and not include_disabled and not include_featured:
            continue
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


def get_category(
    category_id: str, *, include_disabled: bool = False
) -> Dict[str, Any] | None:
    """Return one category dict with its ``tools`` list, or None."""
    for cat in tools_by_category(include_disabled=include_disabled):
        if cat["id"] == category_id:
            return cat
    # Empty-but-defined category (no tools yet / all disabled)
    for c in TOOL_CATEGORIES:
        if c["id"] == category_id:
            return {**c, "tools": []}
    return None


def nav_categories(*, include_disabled: bool = False) -> List[Dict[str, Any]]:
    """Top-nav menu items (all registered categories, including empty)."""
    by_id = {c["id"]: c for c in tools_by_category(include_disabled=include_disabled)}
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


def get_tool_by_slug(slug: str) -> Dict[str, Any] | None:
    """Lookup a registry entry by slug (ignores enable flags)."""
    s = (slug or "").strip()
    if not s:
        return None
    for tool in TOOL_REGISTRY:
        if tool.get("slug") == s:
            return tool
    return None


TOOLS = TOOL_REGISTRY  # alias

__all__ = [
    "TOOL_CATEGORIES",
    "TOOL_REGISTRY",
    "TOOLS",
    "TOOL_ROUTERS",
    "is_featured_tool",
    "enabled_tools",
    "featured_tools",
    "tools_by_category",
    "get_category",
    "nav_categories",
    "get_tool_by_slug",
    "pdf2word_router",
    "word2pdf_router",
    "pdf_merge_router",
    "base64_router",
    "unicode_router",
    "json_router",
    "markdown_router",
    "rmb_router",
    "image_compress_router",
    "image_convert_router",
    "express_router",
]
