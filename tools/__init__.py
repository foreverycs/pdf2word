from .pdf2word import router as pdf2word_router

TOOL_REGISTRY = [
    {
        "name": "PDF 转 Word",
        "slug": "pdf2word",
        "description": "纯文本 / 表格类 PDF 转换为 Word，高保真还原合并单元格、字号、边框与样式。",
        "icon": "📄",
        "route": "/tools/pdf2word",
    },
]

__all__ = ["TOOL_REGISTRY", "pdf2word_router"]
