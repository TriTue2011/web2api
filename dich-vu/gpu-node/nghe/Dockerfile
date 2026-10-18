# Dịch vụ nghe: faster-whisper large-v3 trên CUDA (học từ fw-nghe của c2a).
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Ghim phiên bản gói chính (không thêm gói lạ ngoài nhu cầu dịch vụ).
RUN pip3 install --no-cache-dir \
        faster-whisper==1.2.1 fastapi==0.118.0 uvicorn==0.37.0 "python-multipart==0.0.20"

RUN useradd -r -u 1000 -m nut \
    && mkdir -p /srv /data && chown nut:nut /srv /data

COPY nghe.py /srv/nghe.py
RUN chmod 644 /srv/nghe.py

WORKDIR /srv
USER 1000

ENV HF_HOME=/data \
    FW_MODEL=large-v3 \
    FW_COMPUTE=int8_float16

EXPOSE 5000

CMD ["python3", "-m", "uvicorn", "nghe:app", "--host", "0.0.0.0", "--port", "5000"]
