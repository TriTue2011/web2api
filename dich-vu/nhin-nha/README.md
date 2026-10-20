# Dịch vụ ML phụ «nhìn nhà»

Container nhỏ (stdlib HTTP + onnxruntime) chạy phần ML nặng của module
`nhin-nha`: YOLO phát hiện vật thể, SCRFD dò mặt, ArcFace lấy vector. Module
trong lõi web2api gọi dịch vụ này qua HTTP nội bộ kèm Bearer.

## Hợp đồng API

Mọi đường đều cần `Authorization: Bearer <NHINNHA_KHOA>`:

- `GET /suc-khoe` → `{"ok": true, "yolo": {"model": "yolo26n", "da_tai": false},
  "khuon_mat": {"bo": "buffalo_s", "da_tai": false}}`
- `POST /quet` ← `{"anh_b64": "...", "nhan_mat": true}`
  → `{"vat_the": [{"nhan", "ten", "diem", "hop", "hop_ti_le", "vi_tri"}],
  "mat": [{"hop", "diem_do", "moc", "vector_b64", "net", "chuan"}]}`

Dịch vụ **không biết tên ai**: ánh xạ vector↔tên chỉ nằm trong sổ khuôn mặt
của module (mã hoá khi nghỉ / 0600). Trong container dịch vụ nghe mọi giao
diện (`NHINNHA_HOST=0.0.0.0` do Dockerfile đặt) nhưng phía máy chủ chỉ
publish `127.0.0.1:8703` nên không lộ ra LAN; chạy `server.py` trực tiếp
ngoài container thì mặc định chỉ nghe 127.0.0.1. Muốn chạy trên máy/GPU
khác trong nhà thì đặt proxy/firewall của bạn và trỏ `dich_vu_url` —
module chỉ chấp nhận loopback + mạng nhà riêng.

Hàng rào đầu vào (NV168-SUA N5): khoá so trên bytes (đầu `Authorization`
ngoài ASCII → 401 sạch, không sập luồng); `Content-Length` âm/lạ → 400
ngay (không treo); ảnh bị chặn theo **số điểm ảnh đọc từ tiêu đề** PNG/JPEG
trước khi giải mã (trần 25 triệu điểm — chống bom giải nén); sha256 của
model được **kiểm lại lúc nạp** (lệch là từ chối), không chỉ lúc tải.

## Chạy

```bash
# 1) Khoá dịch vụ — TỰ ĐẶT, dùng chung với trường khoa_dich_vu của module:
head -c 32 /dev/urandom | base64   # ghi lại giá trị này

# 2) Tải model vào thư mục model/ (kiểm sha256, lệch là huỷ):
python3 tai_model.py --dich model

# 3) Dựng + chạy (model gắn qua volume, KHÔNG nằm trong ảnh):
docker build -t nhin-nha-ml .
docker run -d --name nhin-nha-ml -p 127.0.0.1:8703:8703 \
  -e NHINNHA_KHOA='<khoá ở bước 1>' \
  -v "$PWD/model:/model:ro" nhin-nha-ml
```

Biến môi trường: `NHINNHA_KHOA` (bắt buộc, ≥24 ký tự), `NHINNHA_YOLO`
(mặc định `yolo26n`), `NHINNHA_BO_MAT` (mặc định `buffalo_s`),
`NHINNHA_LUONG` (luồng CPU, mặc định 2), `PORT` (mặc định 8703),
`NHINNHA_HOST` (địa chỉ nghe, mặc định 127.0.0.1; Dockerfile đặt 0.0.0.0).

## Model và giấy phép

- YOLO26 (Ultralytics, AGPL-3.0): release `ultralytics/assets` v8.4.0,
  sha256 trong `yolo.py` (MODELS) — từ trường `digest` GitHub.
- SCRFD + ArcFace (insightface `buffalo_s`, chỉ dùng nghiên cứu phi thương
  mại theo giấy phép của insightface): release v0.7, sha256 trong
  `khuon_mat.py` (BO).
- Model không tải mặc định: `tai_model.py` là bước thủ công duy nhất.

## Nguồn

Port từ c2a commit `d879b03`: `yolo.py` ← `services/yolo_nha.py`,
`khuon_mat.py` ← `services/khuon_mat_nha.py`, `server.py` ← phần orchestrate
của `services/nhin_nha.py`. Xem `test_server.py` để chạy test với detector giả.
