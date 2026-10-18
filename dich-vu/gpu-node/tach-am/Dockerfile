# Dịch vụ tách âm: audio-separator (model ONNX MDX nhẹ mặc định) trên CUDA.
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir \
        audio-separator==0.35.1 fastapi==0.118.0 uvicorn==0.37.0 "python-multipart==0.0.20"

RUN useradd -r -u 1000 -m nut \
    && mkdir -p /srv /data && chown nut:nut /srv /data

COPY tach_am.py /srv/tach_am.py
RUN chmod 644 /srv/tach_am.py

WORKDIR /srv
USER 1000

ENV TACH_AM_MODEL=MDX23C.onnx \
    TACH_AM_MODEL_DIR=/data \
    TACH_AM_RA=/data/tach-am

EXPOSE 5000

CMD ["python3", "-m", "uvicorn", "tach_am:app", "--host", "0.0.0.0", "--port", "5000"]
