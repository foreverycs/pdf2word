# PDF to Word (纯文本 / 表格类)

将**纯文本、带表格**的 PDF 转换为高保真 Word（`.docx`），还原合并单元格与基本样式。
基于 **FastAPI** + **pdfplumber**（表格提取）+ **python-docx**（Word 生成）。

## 特性

- 高保真还原：合并单元格（`rowspan` / `colspan`）、表格边框、首行加粗。
- 文本与表格混排：按页面纵向顺序交错输出。
- Web 界面：拖拽上传 PDF，转换后直接下载 `.docx`。
- 单文件处理（每次一个 PDF）。

## 安装

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

## 运行

```bash
python app.py
# 或
uvicorn app:app --host 127.0.0.1 --port 8000
```

浏览器打开 http://127.0.0.1:8000 ，上传 PDF 即可。

## 测试

```bash
pytest tests -q
```

## 目录结构

```
app.py                 FastAPI 入口（上传 / 转换 / 下载）
converter/
  pdf_reader.py        用 pdfplumber 提取页面文本与表格（含合并单元格检测）
  docx_writer.py       用 python-docx 生成 Word，重建合并单元格与样式
templates/index.html   上传前端页面
tests/                 单元 / 样例 PDF 转换测试
```

## 实现说明

- `pdf_reader` 通过 `table.cells` 的矩形几何推断单元格跨度：合并区域在
  pdfplumber 中表现为**跨越多行/多列带**的单一矩形，据此得到
  `rowspan` / `colspan`。
- `docx_writer` 先建 `nrows × ncols` 网格，再对锚点单元格调用
  `cell.merge(...)` 重建合并。

## 已知限制

- 主要针对**有框线**的表格（lines 策略）。无框线表格会回退到 text 策略，
  合并单元格检测精度可能下降。
- 暂不处理扫描件 / 图片型 PDF（无 OCR）。
- 单元格内多行文本、复杂嵌套样式为高保真目标的后续迭代项。
