# Lớp cổng nút GPU — chỉ thư viện chuẩn, không thêm gói.
FROM python:3.13-slim-bookworm

RUN useradd -r -u 1000 -m nut \
    && mkdir -p /srv /data && chown nut:nut /srv /data

COPY cong.py /srv/cong.py
RUN chmod 644 /srv/cong.py

WORKDIR /srv
USER 1000

ENV GPU_NODE_LISTEN=0.0.0.0 \
    GPU_NODE_PORT=8091 \
    GPU_NODE_DATA=/data

EXPOSE 8091

CMD ["python3", "/srv/cong.py"]
