# Base image: default uses a China-friendly mirror of Docker Hub library/python.
# Override if needed, e.g.:
#   docker compose build --build-arg PYTHON_IMAGE=python:3.12-slim
#   docker compose build --build-arg PYTHON_IMAGE=registry.cn-hangzhou.aliyuncs.com/library/python:3.12-slim
ARG PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
FROM ${PYTHON_IMAGE}

WORKDIR /app

# Debian apt → Aliyun (faster on mainland / Alibaba Cloud ECS).
# bookworm = current python:3.12-slim base; adjust if the base tag changes.
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i \
        -e 's|deb.debian.org|mirrors.aliyun.com|g' \
        -e 's|security.debian.org|mirrors.aliyun.com|g' \
        /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
      sed -i \
        -e 's|deb.debian.org|mirrors.aliyun.com|g' \
        -e 's|security.debian.org|mirrors.aliyun.com|g' \
        /etc/apt/sources.list; \
    fi

# Headless LibreOffice for Word → PDF (+ CJK fonts for Chinese docs).
# Optional OCR: tesseract + chi_sim/eng language packs for scanned PDFs.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        libreoffice-writer \
        libreoffice-java-common \
        default-jre-headless \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
        tesseract-ocr-eng \
        fonts-dejavu-core \
        fonts-liberation \
        fonts-noto-cjk \
        fonts-wqy-zenhei \
        fontconfig \
        ca-certificates; \
    fc-cache -f; \
    rm -rf /var/lib/apt/lists/*; \
    if [ -x /usr/bin/soffice ]; then LO=/usr/bin/soffice; \
    elif [ -x /usr/bin/libreoffice ]; then LO=/usr/bin/libreoffice; \
    else echo "LibreOffice binary not found" >&2; exit 1; fi; \
    echo "$LO" > /etc/libreoffice-path; \
    "$LO" --version; \
    tesseract --version

ENV HOME=/tmp \
    SAL_USE_VCLPLUGIN=svp \
    PYTHONUNBUFFERED=1 \
    LIBREOFFICE_PATH=/usr/bin/soffice \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
