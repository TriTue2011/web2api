# Dockerfile cho dịch vụ c2a-dang-nhap (web2api - Chuẩn C6)
# Lưu ý: Tệp mới trong dich-vu/c2a-dang-nhap/, không sửa Dockerfile gốc của repo.
# W2A: Ghim digest đầy đủ lấy từ Dockerfile gốc của repo (đồng bộ khi Dockerfile gốc đổi).
FROM python:3.13-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6

ARG BIEN_THE=firefox
# Gói giải captcha âm thanh tắt ở BA lớp: build-arg CAI_GIAI_CAPTCHA, nhóm giai_captcha, và cổng trong luồng đăng nhập
ARG CAI_GIAI_CAPTCHA=0
ARG CAI_PHONG_CJK=1

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=vi_VN.UTF-8 \
    LC_ALL=vi_VN.UTF-8 \
    TZ=Asia/Ho_Chi_Minh \
    DISPLAY=:99 \
    HOME=/home/appuser \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    CLOAKBROWSER_CACHE_DIR=/opt/cloakbrowser \
    CLOAKBROWSER_AUTO_UPDATE=false \
    # Port từ c2a (nguon-c2a/Dockerfile.c2a.txt dòng 80-81): Firefox cần tắt content sandbox
    # và gmp sandbox trong môi trường container không root có cap_drop: ALL để khởi động được.
    MOZ_DISABLE_CONTENT_SANDBOX=1 \
    MOZ_DISABLE_GMP_SANDBOX=1

# Cài đặt gói hệ thống tối thiểu cho X11 ảo, VNC, noVNC, phông chữ và ngôn ngữ tiếng Việt
RUN apt-get update && apt-get install -y --no-install-recommends \
        locales tzdata \
        xvfb x11vnc novnc websockify supervisor fluxbox \
        fonts-noto-core procps \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
        libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
        libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
        libatspi2.0-0 libxshmfence1 \
    && echo "vi_VN.UTF-8 UTF-8" > /etc/locale.gen && locale-gen vi_VN.UTF-8 \
    && ln -fs /usr/share/zoneinfo/Asia/Ho_Chi_Minh /etc/localtime && dpkg-reconfigure --frontend noninteractive tzdata \
    && if [ "$CAI_PHONG_CJK" = "1" ]; then apt-get install -y --no-install-recommends fonts-noto-cjk; fi \
    && if [ "$BIEN_THE" = "chromium" ] || [ "$BIEN_THE" = "ca-hai" ]; then apt-get install -y --no-install-recommends chromium; fi \
    && rm -rf /var/lib/apt/lists/*

# Cài đặt các gói Python đã ghim bằng hash sha256
COPY requirements-nen.txt requirements-chromium.txt requirements-captcha.txt /tmp/
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements-nen.txt \
    && if [ "$BIEN_THE" != "firefox" ]; then pip install --no-cache-dir --require-hashes -r /tmp/requirements-chromium.txt; fi \
    && if [ "$CAI_GIAI_CAPTCHA" = "1" ]; then pip install --no-cache-dir --require-hashes -r /tmp/requirements-captcha.txt; fi \
    && rm -rf /tmp/requirements-*.txt

# Trình duyệt tự động hoá:
# - CloakBrowser: nhị phân đóng, tải từ máy chủ nhà phát hành lúc dựng, có xác minh chữ ký Ed25519 do gói tự thực hiện (tắt cập nhật lúc chạy bằng CLOAKBROWSER_AUTO_UPDATE=false).
# - Patchright: cài đặt nhị phân trình duyệt Firefox cùng thư viện hệ thống (--with-deps) cho biến thể tương ứng lúc dựng.
RUN mkdir -p /opt/ms-playwright /opt/cloakbrowser \
    && if [ "$BIEN_THE" != "firefox" ]; then python -m cloakbrowser install; fi \
    && if [ "$BIEN_THE" != "chromium" ]; then \
        python -m patchright install --with-deps firefox \
        && rm -rf /var/lib/apt/lists/*; \
    fi \
    && chown -R 1000:1000 /opt/cloakbrowser \
    && chmod 0755 /opt/ms-playwright

# Tạo người dùng không phải root (appuser: 1000:1000)
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -m -s /bin/bash appuser

# /app thuộc root (appuser chỉ đọc), /data và /khoa (volumes) thuộc appuser (0700)
RUN mkdir -p /app /data /khoa /run/supervisor /tmp /home/appuser && \
    chown appuser:appuser /data /khoa /run/supervisor /home/appuser && \
    chmod 0700 /data /khoa /home/appuser && \
    chmod 0755 /app

WORKDIR /app

# Sao chép mã nguồn dịch vụ và cấu hình
COPY supervisord.conf supervisord-novnc.conf /etc/supervisor/
COPY entrypoint.sh /app/entrypoint.sh
COPY src/ /app/src/

RUN chmod 0755 /app/entrypoint.sh && chmod 0644 /etc/supervisor/supervisord.conf /etc/supervisor/supervisord-novnc.conf

USER 1000:1000

VOLUME ["/data", "/khoa"]

EXPOSE 8010

ENTRYPOINT ["/app/entrypoint.sh"]

