from .pdf2word import router as pdf2word_router

TOOL_REGISTRY = [
    {
        "name": "PDF 转 Word",
        "slug": "pdf2word",
        "description": "纯文本 / 表格类 PDF 转 Word：合并单元格、页码范围、批量 ZIP、分页保留。",
        "icon": "📄",
        "route": "/tools/pdf2word",
    },
]

__all__ = ["TOOL_REGISTRY", "pdf2word_router"]
