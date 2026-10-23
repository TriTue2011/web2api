# web2api — khung lõi RỖNG (chưa có module nào). Dựng với ngữ cảnh = thư mục này:
#   docker build -t web2api-moi web2api
#
# Vì sao một tầng, không đa tầng: chỉ có hai gói Python thuần (fastapi, uvicorn) và các tệp
# tĩnh, không có bước biên dịch nào để tách. Nền trùng DIGEST với Dockerfile gốc của c2a để
# hai ảnh chung lớp nền, không kéo thêm một bản Python nữa.
FROM python:3.13-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    WEB2API_DATA=/data

WORKDIR /app

# requirements trước mã: sửa mã không làm cài lại gói.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY VERSION ./
COPY app ./app
COPY web ./web
COPY du_lieu ./du_lieu

# Không chạy bằng root: container này chưa có xác thực, nên càng không nên có quyền thừa.
RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin web2api \
    && mkdir -p /data && chown 1000:1000 /data
USER 1000

EXPOSE 8000

# Không cần curl trong ảnh: dùng chính python để hỏi /health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
