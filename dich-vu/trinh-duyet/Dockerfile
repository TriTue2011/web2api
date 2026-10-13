# Dockerfile cho Dịch vụ Trình duyệt Cô lập (web2api - Chuẩn C6)
# Lưu ý: Tệp mới trong dich-vu/trinh-duyet/, không sửa Dockerfile gốc của repo.
# Chưa dựng thử trực tiếp trong môi trường hộp cát do không có Docker daemon/mạng.

# Ảnh nền: NÊN GHIM DIGEST khi phát hành (dạng `python:3.13-slim@sha256:<digest>`, lấy bằng
# `docker buildx imagetools inspect python:3.13-slim`). Chưa ghim ở đây vì phiên soạn tệp không có mạng
# để lấy digest thật — không ghi digest bịa.
FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Cài đặt các gói hệ thống cần thiết cho X11 ảo, trình duyệt và noVNC
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    firefox-esr \
    xvfb \
    x11vnc \
    novnc \
    websockify \
    fonts-noto-core \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Gói Python cho API điều khiển — GHIM đúng bản đã chạy test:
# fastapi, uvicorn: đúng bản trong requirements.txt gốc của repo (đã kiểm cùng nhau trên Python 3.13);
# pydantic: đúng bản trong .venv của repo nơi test_server.py chạy xanh (requirements.txt gốc không ghim
# pydantic vì nó đi kèm fastapi). Phụ thuộc bắc cầu (starlette, pydantic-core…) do pip giải theo các bản này.
RUN pip install --no-cache-dir fastapi==0.142.4 uvicorn==0.54.0 pydantic==2.14.1

# Tạo người dùng không phải root (UID 1000) để chạy trình duyệt an toàn
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -m -s /bin/bash appuser

# /hoso (hồ sơ trình duyệt) thuộc appuser, 0700. /app (mã) thuộc ROOT, appuser chỉ đọc: tiến trình bị chiếm
# quyền (ví dụ renderer chạy --no-sandbox) không thay được server.py/xac_thuc_ws.py.
RUN mkdir -p /hoso /app && \
    chown appuser:appuser /hoso && \
    chmod 0700 /hoso && \
    chmod 0755 /app

WORKDIR /app

# Chính sách trình duyệt (root:root, chỉ đọc): chặn file:// để người dùng noVNC của hồ sơ A không gõ
# file:///hoso/<id B>/… (cookie B) hay file:///hoso/.bi-mat-ws/… ở thanh địa chỉ. CHƯA KIỂM với trình duyệt
# thật trong ảnh: đường chính sách quản lý của gói Debian chromium là /etc/chromium/policies/managed/, của
# firefox-esr Debian là /usr/lib/firefox-esr/distribution/policies.json. Chỉ giảm đường dễ nhất — xem README.
RUN mkdir -p /etc/chromium/policies/managed /usr/lib/firefox-esr/distribution
COPY chinh-sach/chromium.json /etc/chromium/policies/managed/w2a-chan-file.json
COPY chinh-sach/firefox-policies.json /usr/lib/firefox-esr/distribution/policies.json
RUN chmod 0644 /etc/chromium/policies/managed/w2a-chan-file.json /usr/lib/firefox-esr/distribution/policies.json

# Sao chép mã nguồn dịch vụ (root:root) — xac_thuc_ws.py là plugin xác thực websockify nạp qua PYTHONPATH=/app
COPY server.py xac_thuc_ws.py /app/
RUN chmod 0644 /app/server.py /app/xac_thuc_ws.py

# Đổi quyền sang người dùng không phải root
USER 1000:1000

# Khai báo volume lưu trữ hồ sơ bền vững
VOLUME ["/hoso"]

# Cổng 8700: API điều khiển nội bộ
# Dải 6100-6199: Cổng noVNC phục vụ phiên người dùng (websockify đòi Basic auth bí mật riêng từng hồ sơ)
EXPOSE 8700 6100-6199

CMD ["python", "server.py"]
