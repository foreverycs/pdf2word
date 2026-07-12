from .pdf2word import router as pdf2word_router

TOOL_REGISTRY = [
    {
        "name": "PDF 转 Word",
        "slug": "pdf2word",
        "description": "纯文本 / 表格 PDF 转 Word：合并单元格、图片嵌入、页码范围、批量 ZIP。",
        "icon": "📄",
        "route": "/tools/pdf2word",
    },
]

__all__ = ["TOOL_REGISTRY", "pdf2word_router"]
