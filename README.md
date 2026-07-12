# PDF to Word (纯文本 / 表格类)

将**纯文本、带表格**的 PDF 转换为高保真 Word（`.docx`），还原合并单元格与基本样式。
基于 **FastAPI** + **pdfplumber**（表格提取）+ **python-docx**（Word 生成）。

应用以「工具箱」形式组织，当前内置 **PDF 转 Word**；可按同样方式扩展更多小工具。

## 特性

- 高保真还原：合并单元格（`rowspan` / `colspan`）、表格边框、字号、对齐、背景色。
- 文本与表格混排：按页面纵向顺序交错输出。
- **图片嵌入**：提取页面内图片；纯图片/扫描页整页栅格化写入 Word（**不做 OCR**）。
- **水平定位**：按 PDF 内容边界推断 Word 页边距，文本/图片/表格 x 坐标 1:1 映射，避免整体偏左。
- **同行布局**：Logo+公司名、签名栏等同一行元素用制表位写入同一段落（不用布局表格）。
- **横线还原**：页眉下划线等细矩形/线段转为段落底边框（`pBdr`）。
- **页码范围**：如 `1-3,5`，只转换指定页。
- **分页保留**：可选在 Word 中按 PDF 页插入分页符。
- **批量转换**：多文件一次上传，打包为 ZIP 下载。
- Web 界面：拖拽上传、上传进度条、选项记忆、统计与警告提示。
- 命令行：`python -m converter input.pdf`。
- 单文件最大 50 MB，批量最多 20 个；上传分块写盘，转换在线程池执行。

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

浏览器打开 http://127.0.0.1:8000 ，进入「PDF 转 Word」上传即可。

### 命令行

```bash
python -m converter input.pdf
python -m converter input.pdf -o out.docx --pages 1-3,5
python -m converter input.pdf --no-page-breaks
```

### Docker

```bash
docker compose up --build
# 映射端口 8002 -> 8000
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `GET` | `/tools/pdf2word` | 工具页面 |
| `POST` | `/tools/pdf2word/convert` | 单文件 → `.docx` |
| `POST` | `/tools/pdf2word/convert-batch` | 多文件 → `.zip` |

表单字段：

| 字段 | 说明 |
|------|------|
| `file` / `files` | PDF 文件（单文件 / 批量） |
| `page_range` | 可选，如 `1-3,5`（1 起始） |
| `page_breaks` | 可选，默认 `true`，是否插入 Word 分页符 |

响应头（统计）：`X-Pages`、`X-Tables`、`X-Text-Blocks`、`X-Images`、`X-Lines`；  
可选警告：`X-Warnings`（如 `image_only`）、`X-Warning-Message`；批量另有 `X-Files`。

## 测试

```bash
pytest tests -q
```

## 目录结构

```
app.py                      FastAPI 入口（工具箱首页）
tools/
  __init__.py               工具注册表 TOOL_REGISTRY
  pdf2word.py               /tools/pdf2word 页面与转换 API
converter/
  __main__.py               CLI 入口
  pdf_reader.py             提取文本 / 表格 / 图片 / 横线（含页码范围）
  docx_writer.py            生成 Word（分页、图片、同行制表位、页边距推断）
templates/
  index.html                工具箱首页
  tools/pdf2word.html       PDF 转 Word 上传页
tests/                      单元 / 样例 PDF 转换测试
Dockerfile / docker-compose.yml
```

## 实现说明

- `pdf_reader` 通过 `table.cells` 的矩形几何推断单元格跨度：合并区域在
  pdfplumber 中表现为**跨越多行/多列带**的单一矩形，据此得到
  `rowspan` / `colspan`。
- 页面图片通过 pdfplumber 区域栅格化为 PNG 后写入 Word；无文本/表格的页面
  回退为整页图片，避免扫描件输出空白文档。
- 细长填充矩形 / 水平线段提取为 `LineBlock`（页眉下划线等）。
- `docx_writer`：
  - 由内容 bbox 推断页边距，页尺寸取自 PDF，使水平位置与源文件一致；
  - 同行块用 tab stops 合并为一段（非布局表格）；
  - 横线写成段落 `pBdr`；标题与表格之间用 1pt 紧凑 spacer；
  - 表格网格按 `nrows × ncols` 建立，锚点单元格 `merge` 还原合并。
- 转换失败时立即清理临时目录；成功时在响应发送完毕后异步删除。

## 已知限制

- 主要针对**有框线**的表格（lines 策略）。无框线表格会回退到 text 策略，
  合并单元格检测精度可能下降。
- **无 OCR**：扫描件以图片形式保留版面，文字不可编辑。
- 单元格内复杂嵌套样式为后续迭代项。
