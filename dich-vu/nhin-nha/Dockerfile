# Dịch vụ ML phụ «nhìn nhà» (YOLO + SCRFD + ArcFace qua ONNX).
# Digest ghim: python:3.12-slim (Docker Hub manifest list, lấy 2026-10-20).
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY yolo.py khuon_mat.py server.py tai_model.py ./

# Model KHÔNG nằm trong ảnh: tải riêng bằng tai_model.py vào volume /model,
# kiểm sha256 trước khi dùng. Dịch vụ không tải mặc định.

RUN useradd --create-home --uid 10001 nhinnha && mkdir -p /model && chown nhinnha:nhinnha /model
USER nhinnha
EXPOSE 8703
ENV PORT=8703
CMD ["python", "server.py"]
