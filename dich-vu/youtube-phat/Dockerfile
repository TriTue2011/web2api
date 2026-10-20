# Dockerfile cho dịch vụ youtube-phat — máy phát YouTube/Zing cho web2api (NV153)
# Tệp MỚI trong dich-vu/youtube-phat/ — KHÔNG sửa Dockerfile/docker-compose.yml gốc của repo.
# Chưa dựng thử trong môi trường hộp cát (không có Docker daemon) — ghi «chưa đo» ở README.
#
# Ảnh nền: GHIM DIGEST, lấy từ Dockerfile gốc của kho (dòng 7):
#   python:3.13-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6

FROM python:3.13-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Người dùng không root (uid 1000). /data (playlists.json, history.json,
# integration_token 0600, hmac_key 0600) thuộc youtube-phat, 0700;
# /app (mã) thuộc root, youtube-phat chỉ đọc.
RUN groupadd -g 1000 youtube-phat && \
    useradd -u 1000 -g youtube-phat -m -s /bin/false youtube-phat && \
    mkdir -p /app /data && \
    chown youtube-phat:youtube-phat /data && chmod 0700 /data && chmod 0755 /app

WORKDIR /app
COPY requirements.txt /app/requirements.txt
# Cài có kiểm hash (pip --require-hashes): mọi gói trong requirements.txt
# đều có --hash=sha256. Không mạng lúc chạy — chỉ lúc dựng.
RUN pip install --require-hashes -r /app/requirements.txt

COPY server.py cau_hinh.py xac_thuc.py dich_vu.py ro_le.py \
     search.py streaming.py session.py playlists.py hang_cho.py /app/
RUN chmod 0644 /app/*.py && \
    python3 -c "import server, dich_vu, ro_le; print('import OK')"

USER 1000:1000

EXPOSE 8713

# Mặc định CHỈ nghe nội bộ (luật cứng số 5). Muốn Home Assistant và loa tới
# được thì đặt YT_HOST=0.0.0.0 (hoặc IP LAN cụ thể) + YT_PUBLISH_IP=<IP LAN>
# lúc chạy — xem README mục «Bật mạng LAN» và rủi ro kèm theo.
ENV YT_DATA_DIR=/data \
    YT_HOST=127.0.0.1 \
    YT_PORT=8713

# YT_PUBLISH_IP và integration_token: truyền lúc chạy, không ghi vào ảnh.
# deno/yt-dlp cần ghi bộ nhớ đệm: chạy với tmpfs /tmp và $HOME (xem
# docker-compose.yml); không ghi vào /app (chỉ đọc).

CMD ["python3", "/app/server.py"]
