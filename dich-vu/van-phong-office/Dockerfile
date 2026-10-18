# Dockerfile cho Dịch vụ Văn phòng Cô lập — OfficeCLI trong container phụ (NV138)
# Tệp MỚI trong dich-vu/van-phong-office/ — KHÔNG sửa Dockerfile/docker-compose.yml gốc của repo.
# Chưa dựng thử trong môi trường hộp cát (không có Docker daemon).
#
# Ảnh nền: NÊN GHIM DIGEST khi phát hành (dạng `python:3.13-slim@sha256:<digest>`,
# lấy bằng `docker buildx imagetools inspect python:3.13-slim`). Chưa ghim ở đây vì
# phiên soạn tệp không lấy được digest thật — không ghi digest bịa.

FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OFFICECLI_SKIP_UPDATE=1

# ── OfficeCLI v1.0.140: tải từ GitHub Releases, GHIM SHA-256 từng kiến trúc ──
# Đối chiếu tệp SHA256SUMS của bản phát hành v1.0.140
# (https://github.com/iOfficeAI/OfficeCLI/releases/tag/v1.0.140);
# bản x64 đã tự tải về và tính lại SHA-256 ngày 18/10/2026: KHỚP.
# amd64 → officecli-linux-x64 ; arm64 → officecli-linux-arm64
ARG TARGETARCH
RUN set -e; \
    if [ "$TARGETARCH" = "arm64" ]; then \
      TEP=officecli-linux-arm64; \
      SHA256_GHIM=924dd58f57891d1b3fe8cf77f06990f58f8f0c0ddf6fe22e1d91c7f10d3e7576; \
    else \
      TEP=officecli-linux-x64; \
      SHA256_GHIM=cee68cc2108074e5ae5ad114e1cd5cab4514da8ead4983d5d94aa0acba4f41e8; \
    fi; \
    python3 -c "import urllib.request; \
r = urllib.request.Request('https://github.com/iOfficeAI/OfficeCLI/releases/download/v1.0.140/' + '$TEP', headers={'User-Agent': 'curl/8.5.0'}); \
open('/tmp/$TEP', 'wb').write(urllib.request.urlopen(r, timeout=300).read())"; \
    echo "$SHA256_GHIM  /tmp/$TEP" | sha256sum -c -; \
    install -m 0755 /tmp/$TEP /usr/local/bin/officecli; \
    rm -f /tmp/$TEP

# Người dùng không root (uid 1000). /data (thư mục làm việc) thuộc office, 0700;
# /app (mã) thuộc root, office chỉ đọc.
RUN groupadd -g 1000 office && \
    useradd -u 1000 -g office -m -s /bin/false office && \
    mkdir -p /app /data && \
    chown office:office /data && chmod 0700 /data && chmod 0755 /app

COPY server.py /app/server.py
RUN chmod 0644 /app/server.py

USER 1000:1000

# TẮT kiểm tra cập nhật nền của OfficeCLI (README chính thức:
# "Updates are checked automatically in the background. Disable with
# `officecli config autoUpdate false`"). Ghi vào ~/.officecli/config.json của
# office; grep bên dưới làm dựng HỎNG nếu không tắt được (đóng khi lỗi).
RUN officecli config autoUpdate false && \
    grep -qi 'autoupdate[^}]*false' /home/office/.officecli/config.json

WORKDIR /data

EXPOSE 8701

ENV OFFICE_THU_MUC_GOC=/data \
    OFFICE_PORT=8701 \
    OFFICE_HOST=127.0.0.1

# OFFICE_KHOA (khoá dịch vụ, xem README) truyền lúc chạy, không ghi vào ảnh.
CMD ["python3", "/app/server.py"]
