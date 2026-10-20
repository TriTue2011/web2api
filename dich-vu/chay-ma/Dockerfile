# Ảnh dịch vụ chay-ma (NV171): máy chủ stdlib + docker CLI 27.5.1 (nhị phân tĩnh).
#
# - Ảnh nền python:3.13-slim GHIM DIGEST THẬT (tra Docker Hub ngày 20/10/2026,
#   dùng lại giá trị NV161 đã kiểm).
# - docker CLI: tải nhị phân tĩnh từ download.docker.com theo kiến trúc máy
#   dựng, KIỂM SHA-256 (tự tính lại từ tệp tải về ngày 20/10/2026) bằng
#   `sha256sum -c` — nếu sai là dựng hỏng, không bao giờ tin mạng.
# - Server chạy bằng uid không-root; chỉ COPY server.py.
FROM python:3.13-slim@sha256:8fb4cfa1a2616d7b8e0c2175cc6ad68f5729c34ea8488c0b360d2934b7be9024

ARG TARGETARCH
ENV CHAYMA_DOCKER_BIN=/usr/local/bin/docker \
    CHAYMA_ANH="python:3.13-slim@sha256:8fb4cfa1a2616d7b8e0c2175cc6ad68f5729c34ea8488c0b360d2934b7be9024"

# docker CLI tĩnh: phiên bản 27.5.1, SHA-256 ghim theo kiến trúc.
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) DOCKER_SHA=4f798b3ee1e0140eab5bf30b0edc4e84f4cdb53255a429dc3bbae9524845d640 ;; \
      arm64) DOCKER_SHA=e6b53725a73763ab3f988c73f8772eaed429754c1a579db5ff11f21990fd1817 ;; \
      *) echo "kien truc khong ho tro: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    wget -O /tmp/docker.tgz "https://download.docker.com/linux/static/stable/${TARGETARCH}/docker-27.5.1.tgz"; \
    echo "${DOCKER_SHA}  /tmp/docker.tgz" | sha256sum -c -; \
    tar -xz -C /tmp -f /tmp/docker.tgz; \
    install -m 0755 /tmp/docker/docker /usr/local/bin/docker; \
    rm -rf /tmp/docker /tmp/docker.tgz

WORKDIR /ung-dung
COPY server.py ./server.py

RUN groupadd -r chayma 2>/dev/null || true; \
    useradd -r -g chayma chayma 2>/dev/null || true; \
    mkdir -p /data && chown chayma:chayma /data /ung-dung
USER chayma
EXPOSE 8703
ENV CHAYMA_HOST=0.0.0.0 CHAYMA_PORT=8703 CHAYMA_THU_MUC_GOC=/data

ENTRYPOINT ["python3", "/ung-dung/server.py"]
