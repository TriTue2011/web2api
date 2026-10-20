# Dockerfile cho Dịch vụ Camera Cô lập (NV167) — go2rtc/ffmpeg + kênh nói camera.
# Tệp MỚI trong dich-vu/camera/ — KHÔNG sửa Dockerfile/docker-compose.yml gốc của repo.
# Chưa dựng thử trong môi trường soạn bài (không có Docker daemon) — xem README.
#
# Ảnh nền: ghim digest THẬT tra từ Docker Hub ngày 2026-10-20:
#   python:3.13-slim = sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ── ffmpeg tĩnh (BtbN FFmpeg-Builds, bản «latest» công bố 2026-10-19) ──
# GHIM SHA-256 từng kiến trúc, đối chiếu checksums.sha256 chính thức của bản
# phát hành và TỰ TÍNH LẠI ngày 2026-10-20 khi soạn tệp này:
#   amd64 000602625cbed17ccd1e6c1835592557e9eae2c61ad98e2262c7e85a77483fe7
#   arm64 56b5a549e24381216706d438861aa8afa8663c302092a59feed776600504e9a0
# Tệp «latest» có thể được nhà phát hành thay khi ra bản mới — khi đó dựng ảnh
# HỎNG vì lệch hàm băm (đóng khi lỗi, đúng chủ đích): lấy checksums.sha256 mới
# rồi cập nhật hai giá trị này.
ARG TARGETARCH
RUN set -e; \
    if [ "$TARGETARCH" = "arm64" ]; then \
      TEP=ffmpeg-master-latest-linuxarm64-gpl.tar.xz; \
      SHA256_GHIM=56b5a549e24381216706d438861aa8afa8663c302092a59feed776600504e9a0; \
    else \
      TEP=ffmpeg-master-latest-linux64-gpl.tar.xz; \
      SHA256_GHIM=000602625cbed17ccd1e6c1835592557e9eae2c61ad98e2262c7e85a77483fe7; \
    fi; \
    python3 -c "import urllib.request; \
r = urllib.request.Request('https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/' + '$TEP', headers={'User-Agent': 'curl/8.5.0'}); \
open('/tmp/$TEP', 'wb').write(urllib.request.urlopen(r, timeout=600).read())"; \
    echo "$SHA256_GHIM  /tmp/$TEP" | sha256sum -c -; \
    mkdir -p /opt/ffmpeg && tar -xJf /tmp/$TEP -C /opt/ffmpeg --strip-components=1; \
    ln -s /opt/ffmpeg/bin/ffmpeg /usr/local/bin/ffmpeg; \
    ln -s /opt/ffmpeg/bin/ffprobe /usr/local/bin/ffprobe; \
    rm -f /tmp/$TEP

# Người dùng không root (uid 1000). /app (mã) chỉ đọc.
RUN groupadd -g 1000 camera && \
    useradd -u 1000 -g camera -m -s /bin/false camera && \
    mkdir -p /app && chmod 0755 /app

COPY server.py kenh.py chup.py /app/
RUN chmod 0644 /app/server.py /app/kenh.py /app/chup.py

USER 1000:1000
WORKDIR /app

# Chỉ nghe loopback theo mặc định; khi chạy cạnh web2api trong mạng Docker nội
# bộ thì đặt CAM_HOST=0.0.0.0 và CHỈ công bố cổng trong mạng nội bộ đó (không
# ra Internet). CAM_KHOA bắt buộc ≥ 24 ký tự (dịch vụ từ chối khởi động nếu yếu).
ENV CAM_HOST=127.0.0.1 \
    CAM_CONG=8714

EXPOSE 8714

CMD ["python3", "server.py"]
