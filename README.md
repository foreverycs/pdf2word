# 工具集（Toolkit Suite）

基于 **FastAPI** 的可扩展工具集。工具按**集合（category）**注册，当前包含：

### 文档处理

| 工具 | 说明 |
|------|------|
| **PDF 转 Word** | 纯文本 / 表格 PDF → 高保真 `.docx`（合并单元格、嵌套样式、图片、可选 OCR） |
| **Word 转 PDF** | `.docx` / `.doc` → PDF（LibreOffice 优先，Windows 可回退 Microsoft Word） |

### 编码工具

| 工具 | 说明 |
|------|------|
| **Base64 编解码** | 文本 / 文件 Base64 编码与解码（标准 / URL-safe、多字符集、换行折叠） |

## 特性

### PDF → Word

- 高保真还原：合并单元格（`rowspan` / `colspan`）、表格边框、字号、对齐、背景色。
- 文本与表格混排：按页面纵向顺序交错输出。
- **图片嵌入**：提取页面内图片；纯图片/扫描页默认整页栅格化写入 Word。
- **可选 OCR**：扫描页可启用 Tesseract（`ocr=true` / `--ocr`），输出可编辑文字（需系统 Tesseract + `pytesseract`）。
- **表格算法**：线框 + 混合 + 无框线 text 策略融合；按词框推断跨列合并。
- **单元格嵌套样式**：单元格内多段落、多字体/字号 run 级还原。
- **水平定位**：按 PDF 内容边界推断 Word 页边距，文本/图片/表格 x 坐标 1:1 映射，避免整体偏左。
- **同行布局**：Logo+公司名、签名栏等同一行元素用制表位写入同一段落（不用布局表格）。
- **横线还原**：页眉下划线等细矩形/线段转为段落底边框（`pBdr`）。
- **页码范围**：如 `1-3,5`，只转换指定页。
- **分页保留**：可选在 Word 中按 PDF 页插入分页符。

### Word → PDF

- 支持 `.docx` / `.doc`；单文件或批量 ZIP。
- **LibreOffice** 无头模式（服务器 / Docker 推荐）；可设 `LIBREOFFICE_PATH`。
- **Microsoft Word** COM 回退（仅 Windows，需本机安装 Word + `pywin32` 或 `docx2pdf`）。
- 非 ASCII 路径暂存、超时按文件体积缩放、引擎失败自动回退；宏/ActiveX 禁用并给出明确错误提示。
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

浏览器打开 http://127.0.0.1:8000 。顶部菜单进入 **文档处理** / **编码工具** 栏目页，再打开具体工具。

| 栏目 | 路径 |
|------|------|
| 首页 | `/` |
| 文档处理 | `/c/document`（别名 `/documents`） |
| 编码工具 | `/c/coding`（别名 `/coding`） |

### 命令行

```bash
# PDF → Word
python -m converter input.pdf
python -m converter input.pdf -o out.docx --pages 1-3,5
python -m converter input.pdf --no-page-breaks
python -m converter input.pdf --ocr                 # 扫描页 OCR
python -m converter --ocr-info                      # 查看 Tesseract 状态

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
| `GET` | `/health` | 健康检查（含工具分类） |
| `GET` | `/api/tools` | 工具目录 JSON（分类 + 列表） |
| `GET` | `/api/uploads` | 最近上传记录 JSON |
| `GET` | `/api/uploads/{id}/download` | 下载归档的输入文件 |
| `GET` | `/tools/pdf2word` | PDF→Word 页面 |
| `GET` | `/tools/pdf2word/ocr-status` | OCR（Tesseract）状态 |
| `POST` | `/tools/pdf2word/convert` | 单 PDF → `.docx` |
| `POST` | `/tools/pdf2word/convert-batch` | 多 PDF → `.zip` |
| `GET` | `/tools/word2pdf` | Word→PDF 页面 |
| `GET` | `/tools/word2pdf/status` | 引擎状态 JSON |
| `POST` | `/tools/word2pdf/convert` | 单 Word → `.pdf` |
| `POST` | `/tools/word2pdf/convert-batch` | 多 Word → `.zip` |
| `GET` | `/tools/base64` | Base64 编解码页面 |
| `POST` | `/tools/base64/encode` | 文本/文件 → Base64 |
| `POST` | `/tools/base64/decode` | Base64 → 文本/hex |
| `POST` | `/tools/base64/probe` | 粗检是否像 Base64 |
| `GET` | `/tools/base64/presets` | 选项与示例 |

### PDF → Word 表单字段

| 字段 | 说明 |
|------|------|
| `file` / `files` | PDF 文件（单文件 / 批量） |
| `page_range` | 可选，如 `1-3,5`（1 起始） |
| `page_breaks` | 可选，默认 `true`，是否插入 Word 分页符 |
| `ocr` | 可选，`true`/`1` 对扫描页启用 OCR（需 Tesseract） |

响应头：`X-Pages`、`X-Tables`、`X-Text-Blocks`、`X-Images`、`X-Lines`；  
可选警告：`X-Warnings`（如 `image_only`、`ocr_applied`）、`X-Warning-Message`；批量另有 `X-Files`。

### Word → PDF 表单字段

| 字段 | 说明 |
|------|------|
| `file` / `files` | `.docx` / `.doc`（单文件 / 批量） |

响应头：`X-Engine`、`X-Bytes`；批量另有 `X-Files`。无引擎时 `503`。

## 测试

```bash
pytest tests -q
```

### Base64 表单字段

| 字段 | 说明 |
|------|------|
| `text` | 明文（encode）或 Base64 串（decode） |
| `file` | 可选，encode 时上传文件（≤ 5 MB） |
| `charset` | `utf-8` / `utf-16` / `latin-1` / `ascii`（decode 可 `none`） |
| `variant` | `standard` 或 `urlsafe` |
| `wrap` | encode 换行宽度，`0` / `64` / `76` |
| `strict` | decode 严格校验 |

## 扩展新工具

1. 在 `tools/` 下新增路由模块（如 `tools/my_tool.py`）。
2. 在 `tools/__init__.py` 的 `TOOL_REGISTRY` 增加条目，并设置 `category`（`document` / `coding` 或新分类）。
3. 将 router 加入 `TOOL_ROUTERS`；如需新集合，先在 `TOOL_CATEGORIES` 注册。
4. 页面模板放 `templates/tools/`；共享样式见 `static/css/tokens.css`。

## 目录结构

```
app.py                      FastAPI 入口（工具集首页）
tools/
  __init__.py               分类 + TOOL_REGISTRY + TOOL_ROUTERS
  pdf2word.py               文档：PDF→Word
  word2pdf.py               文档：Word→PDF
  base64_tool.py            编码：Base64
coding/
  base64_codec.py           Base64 核心逻辑
converter/                  PDF→Word 核心
word2pdf/                   Word→PDF 引擎
storage/                    上传归档
static/css/tokens.css       共享设计 token
templates/
  index.html                分类首页
  tools/*.html              各工具页
tests/
Dockerfile / docker-compose.yml
```

## 实现说明

### PDF → Word

- `pdf_reader` 通过 `table.cells` 的矩形几何推断单元格跨度：合并区域在
  pdfplumber 中表现为**跨越多行/多列带**的单一矩形，据此得到
  `rowspan` / `colspan`；无框线表再用词框跨列启发式 `_refine_merges_from_words` 补全。
- 表格检测为 **hybrid**：lines → lines/text 混合 → text，去重重叠区域。
- 单元格内按字体/字号拆分为 `TextRun` 段落列表，由 `docx_writer._set_cell_rich` 写出。
- 页面图片通过 pdfplumber 区域栅格化为 PNG 后写入 Word；无文本/表格的页面
  默认回退为整页图片；`ocr=True` 时走 Tesseract 生成可编辑 `TextBlock`。
- 细长填充矩形 / 水平线段提取为 `LineBlock`（页眉下划线等）。
- `docx_writer`：
  - 由内容 bbox 推断页边距，页尺寸取自 PDF，使水平位置与源文件一致；
  - 同行块用 tab stops 合并为一段（非布局表格）；
  - 横线写成段落 `pBdr`；标题与表格之间用 1pt 紧凑 spacer；
  - 表格网格按 `nrows × ncols` 建立，锚点单元格 `merge` 还原合并。

### Word → PDF

- `word2pdf.converter` 依次尝试 LibreOffice（`soffice --headless --convert-to pdf`）
  与 Microsoft Word COM；独立用户配置目录避免并发冲突。
- 非 ASCII / 过长路径会复制到 ASCII 工作目录；超时随文件体积增长。
- 检测 VBA/ActiveX/OLE 时优先 MS Word，并禁用宏执行；失败信息聚合多引擎错误。
- Docker 镜像预装 `libreoffice-writer`、CJK 字体与 Tesseract（`chi_sim`+`eng`）。

### 通用

- 转换失败时立即清理临时目录；成功时在响应发送完毕后异步删除。
- 环境变量：`PDF2WORD_OCR`、`PDF2WORD_OCR_LANG`、`TESSERACT_CMD`、`LIBREOFFICE_PATH`。

## 已知限制

- 无框线表格与跨行合并仍依赖启发式，复杂表头可能需要人工校对。
- OCR 为可选能力：识别率依赖扫描质量与语言包；默认不开启。
- Word→PDF 版式仍依赖引擎渲染；复杂宏 / ActiveX / 嵌入控件无法完整还原。
- 单元格内图片、嵌套表格尚未支持。
