# 工具集（Toolkit Suite）

基于 **FastAPI** 的可扩展自托管工具平台。工具按**分类**注册，提供 Web 界面、REST API、管理后台，以及 PDF / Word 命令行入口。

| 项目 | 说明 |
|------|------|
| 版本 | `0.9.0` |
| 运行时 | Python 3.11+（推荐 3.12） |
| 默认端口 | `8000`（Docker Compose 映射 `8002→8000`） |

---

## 功能一览

### 文档处理 · `/c/document`

| 工具 | 路径 | 说明 |
|------|------|------|
| **PDF 转 Word** | `/tools/pdf2word` | 纯文本 / 表格 PDF → 高保真 `.docx`（合并单元格、嵌套样式、图片、可选 OCR、批量 ZIP） |
| **Word 转 PDF** | `/tools/word2pdf` | `.docx` / `.doc` → PDF（LibreOffice 优先，Windows 可回退 Microsoft Word） |

### 办公工具 · `/c/office`

| 工具 | 路径 | 说明 |
|------|------|------|
| **发票合并** | `/tools/pdf-merge` | 两张发票合并到一张 A4：上下半页、中间分割线、页内预览打印 |
| **人民币大写** | `/tools/rmb` | 阿拉伯数字金额 → 财务规范中文大写（角分、千分位） |
| **图片压缩** | `/tools/image-compress` | JPEG / PNG / GIF / SVG 高观感压缩，尽量保持清晰 |


### 特色功能 · 首页

| 工具 | 路径 | 说明 |
|------|------|------|
| **文件快递** | `/tools/express` | 首页特色入口：上传生成 6 位取件码，对方输入即可下载；不出现在业务栏目模块列表中 |

### 编码工具 · `/c/coding`

| 工具 | 路径 | 说明 |
|------|------|------|
| **Base64 编解码** | `/tools/base64` | 文本 / 文件 Base64（标准 / URL-safe、多字符集、换行折叠） |
| **代码格式化** | `/tools/json` | 多语言美化 / 压缩（JSON、JS/TS、Python、HTML/CSS/XML、SQL、YAML 等） |
| **Markdown 编辑** | `/tools/markdown` | 左右分栏编辑与实时 HTML 预览，XSS 过滤，可导出 HTML |

### 平台能力

- **异步转换**：PDF ↔ Word 支持「提交 → 轮询 → 下载」，降低反代读超时风险
- **批量转换**：多文件上传，结果打 ZIP；并发受 `CONVERT_CONCURRENCY` 限制
- **上传归档**：成功后仅保存**输入文件**到 `file/`（默认保留 5 天），管理后台可查看
- **功能开关**：后台按工具启用 / 关闭；关闭后首页隐藏，页面与 API 返回 403
- **管理后台**：仪表盘、上传记录、文件快递、功能开关、系统状态；登录限流 + CSRF
- **可观测性**：`X-Request-ID`、`/health`、公开接口 IP 限流

---

## 特性说明

### PDF → Word

- 合并单元格（`rowspan` / `colspan`）、表格边框、字号、对齐、背景色
- 文本与表格按页面纵向顺序交错输出
- 图片嵌入；纯图片 / 扫描页可整页栅格化，或启用 **OCR**（Tesseract）生成可编辑文字
- 页边距按 PDF 内容边界推断，坐标 1:1 映射；同行元素用制表位布局
- 横线还原为段落底边框；支持页码范围（如 `1-3,5`）与 Word 分页符

### Word → PDF

- LibreOffice 无头模式（服务器 / Docker 推荐）
- Windows 可回退 Microsoft Word COM（需本机 Word + `pywin32` / `docx2pdf`）
- 非 ASCII 路径暂存、超时随体积缩放、引擎失败自动回退
- 无可用引擎时返回 HTTP `503`

### 图片压缩

- 保持原格式：JPEG / PNG / GIF / SVG
- 预设：高质量 / 均衡（推荐）/ 强压缩；默认去除 EXIF 等元数据（方向先烘焙进像素）
- 压缩后若更大则保留原文件；强压缩可限制最长边（默认 2560px）

### Markdown 编辑

- 左源码、右实时预览（服务端渲染 + bleach 过滤 XSS）
- 支持标题、列表、表格、代码块、引用等
- 复制 MD / HTML、导出独立 HTML；内容可保存在浏览器 `localStorage`

---

## 安装

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
# source .venv/bin/activate

pip install -r requirements.txt

# 开发 / 跑测试
pip install -r requirements-dev.txt
```

可选系统依赖：

| 能力 | 依赖 |
|------|------|
| Word → PDF（推荐） | LibreOffice Writer |
| Word → PDF（Windows 回退） | Microsoft Word + `pywin32` / `docx2pdf` |
| PDF OCR | Tesseract + 语言包（如 `chi_sim`、`eng`）+ `pytesseract` |

---

## 运行

```bash
# 本地开发：复制环境文件
copy .env.example .env          # Windows
# cp .env.example .env          # Linux / macOS

python app.py
# 或
uvicorn app:app --host 127.0.0.1 --port 8000 --workers 1
```

浏览器打开：

| 地址 | 说明 |
|------|------|
| http://127.0.0.1:8000/ | 首页 |
| http://127.0.0.1:8000/admin | 管理后台 |
| http://127.0.0.1:8000/c/document | 文档处理 |
| http://127.0.0.1:8000/c/office | 办公工具 |
| http://127.0.0.1:8000/c/coding | 编码工具 |

### 单 worker 约束

异步任务（`GET /api/jobs/{id}`）默认存在**当前进程内存**中。

- 必须使用 **`--workers 1`**
- 多 worker / 多副本会导致提交与轮询打到不同进程 → 任务 404
- `JOBS_BACKEND=redis` 为多实例预留；当前构建仍会回退内存并打日志

### 宝塔 / Nginx 反代

若 **IP:端口正常、域名访问布局错乱**，多为静态资源未反代到本应用。

1. F12 → Network，确认 `/static/css/tokens.css` 等为 **200**
2. 整站 `/` 反代到 `http://127.0.0.1:你的端口`，不要把 `/static` 指到其它站点
3. 参考 `deploy/nginx-baota.conf.example`：`Host`、`X-Forwarded-*`、`client_max_body_size`、`proxy_read_timeout ≥ 600s`
4. 仅子路径部署时设置 `ROOT_PATH`（如 `/toolkit`）；域名根目录反代**不要**设置
5. HTTPS 下 Cookie 需 Secure 时可设 `ADMIN_COOKIE_SECURE=1`

---

## 管理后台

地址：http://127.0.0.1:8000/admin

生产环境**必须**配置强密钥，启动时会校验弱口令：

```bash
# Windows
set ADMIN_PASSWORD=Str0ng-Passw0rd!
set ADMIN_SECRET=please-use-a-long-random-string-here

# Linux / macOS
export ADMIN_PASSWORD='Str0ng-Passw0rd!'
export ADMIN_SECRET='please-use-a-long-random-string-here'
```

本地开发可直接使用 `.env.example` 中的 `ALLOW_INSECURE_ADMIN=1`。

### 文件快递记录

地址：http://127.0.0.1:8000/admin/express（需登录）

与「上传记录」相互独立：列出取件码包裹，支持按状态/关键词筛选、下载原文件、单条/批量删除。有效期仅限制用户取件；管理端长期保留记录与文件，仅管理员手动删除或「清理过期项」才会物理删除。数据存放在 `file/express/`。

### 功能开关

地址：http://127.0.0.1:8000/admin/tools（需登录）

在后台按工具勾选「启用 / 关闭」，保存后**立即生效**（无需重启进程）。

| 行为 | 说明 |
|------|------|
| 默认 | 全部启用（无配置文件时） |
| 关闭某工具 | 首页、分类页、`GET /api/tools` 不再展示该工具 |
| 直接访问 | 页面或转换 API 返回 **403**（HTML 提示「功能已关闭」） |
| 持久化 | 写入归档目录下的 `tool_flags.json`（默认 `file/tool_flags.json`） |
| Docker | 与上传归档共用 `./file` 挂载，重启后状态保留 |

示例配置文件：

```json
{
  "version": 1,
  "disabled": ["markdown", "image-compress"]
}
```

- 仅接受已注册的工具 `slug`；未知项忽略
- 也可手写 `{"tools": {"markdown": false}}` 形式，程序会合并解析
- 仪表盘与系统状态页会显示「前台启用数 / 已关闭列表」

相关接口（均需管理员会话 + CSRF）：

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/admin/tools` | 功能开关页面 |
| `POST` | `/admin/tools` | 批量保存（表单字段 `enabled` 多选） |
| `POST` | `/admin/tools/{slug}/toggle` | 单个工具开 / 关 |

### 环境变量

| 变量 | 说明 | 默认 |
|------|------|------|
| `ADMIN_PASSWORD` | 后台登录密码（≥12 位，非常见弱口令） | **必填** |
| `ADMIN_SECRET` | 会话签名密钥（≥24 位，与密码独立） | **必填** |
| `ALLOW_INSECURE_ADMIN` | `1` 允许弱口令 / 缺省（仅本地） | `0` |
| `ADMIN_SESSION_TTL` | 会话有效期（秒） | `43200`（12h） |
| `ADMIN_COOKIE_SECURE` | Cookie 仅 HTTPS | `0` |
| `CONVERT_CONCURRENCY` | 全局转换并发上限 | `2` |
| `MAX_UPLOAD_BYTES` | 单文件上传上限（字节） | `52428800`（50MB） |
| `MAX_BATCH_FILES` | 批量最多文件数 | `20` |
| `UPLOAD_RETENTION_DAYS` | 上传归档保留天数 | `5` |
| `UPLOAD_FILE_DIR` | 归档目录（默认项目下 `file/`） | 空 |
| `ROOT_PATH` | 反代子路径前缀 | 空 |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `PDF_PROCESS_POOL_THRESHOLD` | 大于该字节的 PDF 走进程池 | `2097152`（2MB） |
| `API_RATE_LIMIT` | 公开转换/下载每窗口最大请求数（`0` 关闭） | `30` |
| `API_RATE_WINDOW_SEC` | 限流窗口（秒） | `60` |
| `JOBS_BACKEND` | 任务存储：`memory` / `redis`（预留） | `memory` |
| `REDIS_URL` | Redis 连接（`JOBS_BACKEND=redis` 时） | 空 |
| `JOB_TTL_SEC` | 完成/失败任务元数据保留秒数 | `3600` |
| `DOTENV_OVERRIDE` | `1` 时 `.env` 覆盖进程环境变量 | `0` |
| `LIBREOFFICE_PATH` | `soffice` 可执行文件路径 | 系统探测 |
| `EXPRESS_MAX_BYTES` | 文件快递单文件上限（字节） | 同 `MAX_UPLOAD_BYTES` |
| `EXPRESS_DEFAULT_TTL_HOURS` | 取件码默认有效期（小时） | `24` |
| `EXPRESS_MAX_TTL_HOURS` | 取件码最长有效期（小时） | `168` |
| `EXPRESS_DIR` | 快递包存储目录（可选） | `file/express/` |

### 密码改了不生效？

1. **改完未重启**进程（uvicorn / Docker / 宝塔必须重启）
2. **进程环境优先**：Docker `environment:`、宝塔环境变量、systemd 中若已有同名项，默认会忽略 `.env`
   - 解决：在 `.env` 加 `DOTENV_OVERRIDE=1` 后重启，或改掉进程环境中的旧值
3. **Docker 未挂载 `.env`**：compose 已支持 `env_file` 与挂载；纯 Docker 请用 `--env-file` 或 `-e`
4. **文件位置**：必须是项目根目录（与 `app.py` 同级）的 `.env`
5. 改密码后旧 Cookie 可能仍有效：清 Cookie 或更换 `ADMIN_SECRET`

管理后台 → **系统状态** 可查看 `.env` 路径，以及 `ADMIN_PASSWORD` 是 `set` 还是 `skipped_existing`。

---

## 异步转换 API

| 工具 | 提交 | 轮询 | 下载 |
|------|------|------|------|
| PDF→Word 单文件 | `POST /tools/pdf2word/convert-async` → 202 | `GET /api/jobs/{id}` | `GET /api/jobs/{id}/download` |
| PDF→Word 批量 | `POST /tools/pdf2word/convert-batch-async` | 同上 | 同上（ZIP） |
| Word→PDF 单文件 | `POST /tools/word2pdf/convert-async` → 202 | 同上 | 同上 |
| Word→PDF 批量 | `POST /tools/word2pdf/convert-batch-async` | 同上 | 同上（ZIP） |

流程：`multipart` 上传 → JSON（`id` / `poll_url` / `download_url`）→ 轮询至 `status=done` → 下载。  
下载成功后服务端清理临时文件；同步 `/convert` 仍可用作兼容回退。

前端仅在**网络错误或 404/405** 时回退同步；业务错误（坏文件、引擎未就绪、任务 `error`）不会重复请求同步接口。

长转换（尤其 OCR）可能需数分钟：优先异步 API；Nginx 建议 `proxy_read_timeout ≥ 600s`。

---

## 命令行

```bash
# PDF → Word
python -m converter input.pdf
python -m converter input.pdf -o out.docx --pages 1-3,5
python -m converter input.pdf --no-page-breaks
python -m converter input.pdf --ocr
python -m converter --ocr-info

# Word → PDF（需 LibreOffice 或 Windows + Word）
python -m word2pdf input.docx
python -m word2pdf input.docx -o out.pdf
python -m word2pdf --info
```

---

## Docker

镜像内置 LibreOffice Writer（`writer-nogui` 无头包，体积小于完整 GUI 版）、Tesseract（中/英）与中文字体，本机无需再装办公套件。

```bash
# 准备 .env（生产务必改掉弱口令）
copy .env.example .env

docker compose up --build -d

# 默认映射 8002 → 容器 8000
curl http://127.0.0.1:8002/health
# 浏览器：http://127.0.0.1:8002
```

国内拉不到官方 Python 镜像时，Dockerfile 默认使用 DaoCloud 镜像；也可覆盖：

```bash
docker compose build --build-arg PYTHON_IMAGE=docker.1ms.run/library/python:3.12-slim
docker compose up -d
```

```bash
docker compose exec toolbox python -m word2pdf --info
docker compose down
```

> 异步任务为进程内存存储，compose 请保持单副本、单 worker（镜像 CMD 已指定 `--workers 1`）。

---

## 健康检查与目录 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 轻量探活；`tools` 为**前台启用**数量，`tools_registered` 为注册总数 |
| `GET` | `/health?detail=1` | 含引擎 / OCR / 归档 / 任务后端；分类统计仅含已启用工具 |
| `GET` | `/api/tools` | 工具目录 JSON（**仅已启用**的分类与列表） |

响应带 `X-Request-ID`（客户端也可传入以便串联日志）。

---

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/tools/pdf2word` | PDF→Word 页面 |
| `GET` | `/tools/pdf2word/ocr-status` | OCR 状态 |
| `POST` | `/tools/pdf2word/convert` | 单 PDF → `.docx` |
| `POST` | `/tools/pdf2word/convert-batch` | 多 PDF → `.zip` |
| `POST` | `/tools/pdf2word/convert-async` | 异步单文件 |
| `POST` | `/tools/pdf2word/convert-batch-async` | 异步批量 |
| `GET` | `/tools/word2pdf` | Word→PDF 页面 |
| `GET` | `/tools/word2pdf/status` | 引擎状态 |
| `POST` | `/tools/word2pdf/convert` | 单 Word → `.pdf` |
| `POST` | `/tools/word2pdf/convert-batch` | 多 Word → `.zip` |
| `POST` | `/tools/word2pdf/convert-async` | 异步单文件 |
| `POST` | `/tools/word2pdf/convert-batch-async` | 异步批量 |
| `GET` | `/api/jobs/{id}` | 任务状态 |
| `GET` | `/api/jobs/{id}/download` | 任务结果下载 |
| `POST` | `/tools/pdf-merge/merge` | 发票合并 |
| `POST` | `/tools/image-compress/compress` | 图片压缩（返回文件） |
| `POST` | `/tools/image-compress/compress-info` | 图片压缩统计 JSON |
| `POST` | `/tools/base64/encode` | Base64 编码 |
| `POST` | `/tools/base64/decode` | Base64 解码 |
| `POST` | `/tools/json/format` | 多语言代码美化 / 压缩（`language` 参数） |
| `POST` | `/tools/json/validate` | 代码 / JSON 校验（`language` 参数） |
| `GET` | `/tools/json/languages` | 支持的语言列表 |
| `GET` | `/tools/json/sample` | 各语言示例代码 |
| `POST` | `/tools/markdown/render` | Markdown → HTML JSON |
| `POST` | `/tools/markdown/export-html` | 导出独立 HTML |
| `POST` | `/tools/rmb/convert` | 金额大写 |
| `GET` | `/tools/express` | 文件快递页面（`?code=` 可预填取件码） |
| `POST` | `/tools/express/send` | 上传文件 → 返回 6 位取件码 |
| `POST` | `/tools/express/lookup` | 查询取件码元数据（不消耗下载次数） |
| `POST` | `/tools/express/pickup` | 按取件码下载（表单） |
| `GET` | `/tools/express/pickup/{code}` | 按取件码下载（路径，可收藏） |
| `GET` | `/admin` | 管理后台（需登录） |
| `GET` | `/admin/uploads` | 通用上传记录（需管理员） |
| `GET` | `/admin/express` | 文件快递记录（需管理员） |
| `POST` | `/admin/express/batch-delete` | 批量删除快递包裹（需管理员 + CSRF） |
| `POST` | `/admin/express/{id}/delete` | 删除快递包裹（需管理员 + CSRF） |
| `GET` | `/admin/express/{id}/download` | 下载快递文件（需管理员） |
| `POST` | `/admin/express/cleanup` | 手动清理已过期快递（需管理员 + CSRF；非自动） |
| `GET` | `/admin/tools` | 功能开关页面（需管理员） |
| `POST` | `/admin/tools` | 批量保存功能开关（需管理员 + CSRF） |
| `POST` | `/admin/tools/{slug}/toggle` | 切换单个工具（需管理员 + CSRF） |
| `GET` | `/api/uploads` | 上传记录（需管理员） |

> 被管理员关闭的工具：其 `/tools/{slug}/...` 页面与 API 均返回 **403**，不会进入业务逻辑。

### PDF → Word 表单字段

| 字段 | 说明 |
|------|------|
| `file` / `files` | PDF（单文件 / 批量） |
| `page_range` | 可选，如 `1-3,5`（1 起始） |
| `page_breaks` | 可选，默认 `true` |
| `ocr` | 可选，`true` / `1` 启用 OCR |

响应头：`X-Pages`、`X-Tables`、`X-Text-Blocks`、`X-Images`、`X-Lines`；可选 `X-Warnings`、`X-Warning-Message`。

### Word → PDF 表单字段

| 字段 | 说明 |
|------|------|
| `file` / `files` | `.docx` / `.doc` |

响应头：`X-Engine`、`X-Bytes`；无引擎时 `503`。

### 图片压缩表单字段

| 字段 | 说明 |
|------|------|
| `file` | 图片文件 |
| `quality` | `high` / `balanced` / `strong` |
| `strip_meta` | 默认 `true` |
| `max_side` | 可选最长边（像素），`0` = 不缩放 |

响应头：`X-Original-Bytes`、`X-Compressed-Bytes`、`X-Percent-Saved` 等。

---

## 测试

```bash
pip install -r requirements-dev.txt
pytest tests -q
```

---

## 扩展新工具

1. 在 `tools/` 下新增路由模块（如 `tools/my_tool.py`）
2. 核心逻辑放在对应包（如 `coding/`、`office/`、`media/`、`converter/`）
3. 在 `tools/__init__.py` 的 `TOOL_REGISTRY` 增加条目，设置 `category`
4. 将 router 加入 `TOOL_ROUTERS`；新分类先写 `TOOL_CATEGORIES`
5. 页面模板放在 `templates/tools/`；共享样式见 `static/css/tokens.css`、`layout.css`

---

## 目录结构（简要）

```
app.py                 # FastAPI 入口
core/                  # 配置、任务、并发、限流、日志、功能开关
admin/                 # 管理后台
tools/                 # 各工具 HTTP 路由与注册表
converter/             # PDF → Word 算法
word2pdf/              # Word → PDF 引擎
coding/                # Base64 / 代码格式化 / Markdown 逻辑
office/                # 人民币大写等
media/                 # 图片压缩
storage/               # 上传归档 + 文件快递（SQLite + file/）
templates/             # Jinja2 页面
static/                # CSS / JS
deploy/                # Nginx 示例
tests/                 # pytest
```

功能开关状态文件：`file/tool_flags.json`（或 `UPLOAD_FILE_DIR/tool_flags.json`）。

---

## 安全提示

- 生产务必设置强 `ADMIN_PASSWORD` 与 `ADMIN_SECRET`，关闭 `ALLOW_INSECURE_ADMIN`
- 公开转换接口有进程内 IP 限流；生产建议在 Nginx 再加一层限流
- Markdown 预览默认 XSS 过滤；勿关闭 sanitize 用于不可信输入
- 上传归档目录 `file/` 含用户原始文件与 `tool_flags.json`，注意备份与访问权限
- 功能开关仅控制前台开放范围，**不能替代**管理员密码与反代鉴权
