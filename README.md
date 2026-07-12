# 工具箱（PDF ↔ Word）

基于 **FastAPI** 的小工具集合，当前内置：

| 工具 | 说明 |
|------|------|
| **PDF 转 Word** | 纯文本 / 表格 PDF → 高保真 `.docx`（合并单元格、图片、页边距推断） |
| **Word 转 PDF** | `.docx` / `.doc` → PDF（LibreOffice 优先，Windows 可回退 Microsoft Word） |

## 特性

### PDF → Word

- 高保真还原：合并单元格（`rowspan` / `colspan`）、表格边框、字号、对齐、背景色。
- 文本与表格混排：按页面纵向顺序交错输出。
- **图片嵌入**：提取页面内图片；纯图片/扫描页整页栅格化写入 Word（**不做 OCR**）。
- **水平定位**：按 PDF 内容边界推断 Word 页边距，文本/图片/表格 x 坐标 1:1 映射，避免整体偏左。
- **同行布局**：Logo+公司名、签名栏等同一行元素用制表位写入同一段落（不用布局表格）。
- **横线还原**：页眉下划线等细矩形/线段转为段落底边框（`pBdr`）。
- **页码范围**：如 `1-3,5`，只转换指定页。
- **分页保留**：可选在 Word 中按 PDF 页插入分页符。

### Word → PDF

- 支持 `.docx` / `.doc`；单文件或批量 ZIP。
- **LibreOffice** 无头模式（服务器 / Docker 推荐）；可设 `LIBREOFFICE_PATH`。
- **Microsoft Word** COM 回退（仅 Windows，需本机安装 Word + `pywin32` 或 `docx2pdf`）。
- 页面展示引擎状态；无引擎时返回 HTTP 503。

### 通用

- **批量转换**：多文件一次上传，打包为 ZIP 下载。
- **上传归档**：转换成功后仅将**输入文件**写入后台 `file/` 目录（前端不展示）；**仅保留最近 5 天**（可配 `UPLOAD_RETENTION_DAYS`）。
- Web 界面：拖拽上传、上传进度条、统计与警告提示。
- 命令行：`python -m converter input.pdf` / `python -m word2pdf input.docx`。
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
# PDF → Word
python -m converter input.pdf
python -m converter input.pdf -o out.docx --pages 1-3,5
python -m converter input.pdf --no-page-breaks

# Word → PDF（需 LibreOffice 或 Windows + Word）
python -m word2pdf input.docx
python -m word2pdf input.docx -o out.pdf
python -m word2pdf --info          # 查看可用引擎
```

### Word → PDF 引擎依赖

| 环境 | 推荐 |
|------|------|
| Docker | 镜像已安装 LibreOffice Writer |
| Linux 本机 | `sudo apt install libreoffice-writer`（或发行版等价包） |
| Windows 本机 | 安装 [LibreOffice](https://www.libreoffice.org/)；或安装 Microsoft Word 并 `pip install pywin32` / `docx2pdf` |

可选环境变量：`LIBREOFFICE_PATH` / `SOFFICE_PATH` 指向 `soffice` 可执行文件。

### Docker（推荐：Word → PDF 用 LibreOffice，本机无需安装）

镜像已内置 **LibreOffice Writer** + 中文字体，本机只要有 Docker 即可。

```bash
# 构建并启动（端口 8002 → 容器 8000）
docker compose up --build -d

# 查看状态（应含 word2pdf.ready=true）
curl http://127.0.0.1:8002/health

# 浏览器
# http://127.0.0.1:8002          工具箱首页
# http://127.0.0.1:8002/tools/word2pdf
```

**国内服务器拉不到 `python:3.12-slim`（docker.io 超时）时：**

本仓库 Dockerfile 默认基础镜像为  
`docker.m.daocloud.io/library/python:3.12-slim`，一般可直接构建。

若该镜像站也失败，可换源再构建：

```bash
# 备选 1
docker compose build --build-arg PYTHON_IMAGE=docker.1ms.run/library/python:3.12-slim
docker compose up -d

# 备选 2：给 Docker 配 registry-mirror 后仍用官方名
# 编辑 /etc/docker/daemon.json 后 systemctl restart docker，例如：
# {
#   "registry-mirrors": [
#     "https://docker.m.daocloud.io",
#     "https://docker.1ms.run"
#   ]
# }
docker compose build --build-arg PYTHON_IMAGE=python:3.12-slim
docker compose up -d
```

首次构建会安装 LibreOffice，体积与时间较大，属正常现象。

```bash
# 容器内查看引擎
docker compose exec toolbox python -m word2pdf --info

# 停止
docker compose down
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `GET` | `/api/uploads` | 最近上传记录 JSON |
| `GET` | `/api/uploads/{id}/download` | 下载归档的输入文件 |
| `GET` | `/tools/pdf2word` | PDF→Word 页面 |
| `POST` | `/tools/pdf2word/convert` | 单 PDF → `.docx` |
| `POST` | `/tools/pdf2word/convert-batch` | 多 PDF → `.zip` |
| `GET` | `/tools/word2pdf` | Word→PDF 页面 |
| `GET` | `/tools/word2pdf/status` | 引擎状态 JSON |
| `POST` | `/tools/word2pdf/convert` | 单 Word → `.pdf` |
| `POST` | `/tools/word2pdf/convert-batch` | 多 Word → `.zip` |

### PDF → Word 表单字段

| 字段 | 说明 |
|------|------|
| `file` / `files` | PDF 文件（单文件 / 批量） |
| `page_range` | 可选，如 `1-3,5`（1 起始） |
| `page_breaks` | 可选，默认 `true`，是否插入 Word 分页符 |

响应头：`X-Pages`、`X-Tables`、`X-Text-Blocks`、`X-Images`、`X-Lines`；  
可选警告：`X-Warnings`（如 `image_only`）、`X-Warning-Message`；批量另有 `X-Files`。

### Word → PDF 表单字段

| 字段 | 说明 |
|------|------|
| `file` / `files` | `.docx` / `.doc`（单文件 / 批量） |

响应头：`X-Engine`、`X-Bytes`；批量另有 `X-Files`。无引擎时 `503`。

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
  word2pdf.py               /tools/word2pdf 页面与转换 API
converter/
  __main__.py               PDF→Word CLI
  pdf_reader.py             提取文本 / 表格 / 图片 / 横线
  docx_writer.py            生成 Word
word2pdf/
  __main__.py               Word→PDF CLI
  converter.py              LibreOffice / MS Word 引擎
storage/
  history.py                上传归档与 5 天清理
file/                       上传记录目录（挂载持久化，gitignore）
templates/
  index.html                工具箱首页
  tools/pdf2word.html       PDF 转 Word 上传页
  tools/word2pdf.html       Word 转 PDF 上传页
tests/                      单元测试
Dockerfile / docker-compose.yml
```

## 实现说明

### PDF → Word

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

### Word → PDF

- `word2pdf.converter` 依次尝试 LibreOffice（`soffice --headless --convert-to pdf`）
  与 Microsoft Word COM；独立用户配置目录避免并发冲突。
- Docker 镜像预装 `libreoffice-writer` 与 CJK 字体。

### 通用

- 转换失败时立即清理临时目录；成功时在响应发送完毕后异步删除。

## 已知限制

- PDF→Word 主要针对**有框线**的表格（lines 策略）。无框线表格会回退到 text 策略，
  合并单元格检测精度可能下降。
- **无 OCR**：扫描件以图片形式保留版面，文字不可编辑。
- Word→PDF 版式依赖 LibreOffice / Word 渲染，复杂宏 / ActiveX 可能无法还原。
- 单元格内复杂嵌套样式为后续迭代项。
