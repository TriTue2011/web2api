# Dockerfile cho Dịch vụ rclone — nhị phân rclone trong container phụ (NV161)
# Tệp MỚI trong dich-vu/rclone/ — KHÔNG sửa Dockerfile/docker-compose.yml gốc của repo.
# Chưa dựng thử trong môi trường hộp cát (không có Docker daemon).
#
# Ảnh nền ghim DIGEST THẬT: python:3.13-slim@sha256:8fb4cfa1… (tra Docker Hub
# ngày 20/10/2026: tag cập nhật lần cuối 2026-10-09).
# rclone v1.75.2: SHA-256 TỰ TÍNH LẠI từ tệp tải về (downloads.rclone.org) ngày
# 20/10/2026 và ĐỐI CHIẾU khớp tệp SHA256SUMS chính thức của nhà phát hành —
# amd64: 349ac8fb… ; arm64: 7e1e8d69…

FROM python:3.13-slim@sha256:8fb4cfa1a2616d7b8e0c2175cc6ad68f5729c34ea8488c0b360d2934b7be9024

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ── rclone v1.75.2: tải từ downloads.rclone.org, GHIM SHA-256 từng kiến trúc ──
ARG TARGETARCH
RUN set -e; \
    if [ "$TARGETARCH" = "arm64" ]; then \
      TEP=rclone-v1.75.2-linux-arm64.zip; \
      SHA256_GHIM=7e1e8d69654941b7b7df84ee74c5f7fb09cce7d5496947fb7bf16b55ff427d10; \
    else \
      TEP=rclone-v1.75.2-linux-amd64.zip; \
      SHA256_GHIM=349ac8fba6ff65d6247043f1750cdcb518ec5d500ef91463a10d37c0ccdf3702; \
    fi; \
    python3 -c "import urllib.request; \
r = urllib.request.Request('https://downloads.rclone.org/v1.75.2/' + '$TEP', headers={'User-Agent': 'curl/8.5.0'}); \
open('/tmp/rclone.zip', 'wb').write(urllib.request.urlopen(r, timeout=300).read())"; \
    echo "$SHA256_GHIM  /tmp/rclone.zip" | sha256sum -c -; \
    python3 -c "import zipfile; z = zipfile.ZipFile('/tmp/rclone.zip'); \
z.extractall('/tmp/rclone-x')"; \
    install -m 0755 /tmp/rclone-x/rclone-*/rclone /usr/local/bin/rclone; \
    rm -rf /tmp/rclone.zip /tmp/rclone-x

# Người dùng không root (uid 1000). /data (thư mục làm việc + cấu hình) thuộc
# rclone, 0700; /app (mã) thuộc root, rclone chỉ đọc.
RUN groupadd -g 1000 rclone && \
    useradd -u 1000 -g rclone -m -s /bin/false rclone && \
    mkdir -p /app /data && \
    chown rclone:rclone /data && chmod 0700 /data && chmod 0755 /app

COPY server.py /app/server.py
RUN chmod 0644 /app/server.py

USER 1000:1000

WORKDIR /data

EXPOSE 8702

ENV RCLONE_THU_MUC_GOC=/data \
    RCLONE_PORT=8702 \
    RCLONE_HOST=0.0.0.0 \
    RCLONE_BIN=/usr/local/bin/rclone

# RCLONE_KHOA (khoá dịch vụ, xem README) truyền lúc chạy, không ghi vào ảnh.
CMD ["python3", "/app/server.py"]
