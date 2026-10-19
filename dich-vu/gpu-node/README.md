# Nút GPU cho web2api (`dich-vu/gpu-node/`)

Gói dựng **phía máy có card NVIDIA** (máy khác, cùng mạng với web2api).
Máy GPU chạy các tác vụ nặng (nghe, thị giác, tách âm); web2api ở máy chính
nối vào bằng giao diện «＋ Thêm máy GPU» (NV140) hoặc «Thêm module → Từ xa» —
**không đổi biến môi trường của docker compose web2api**.

Học ý từ c2a (`fw-nghe/`, `deploy/gpu-box/`, `docs/NGHE_GPU.md` — kho công khai
TriTue2011/chatgpt2api, đã đọc 18/10/2026), tự viết lại theo «hợp đồng nút GPU».

## Chuẩn bị máy GPU

1. Card NVIDIA + driver + `nvidia-container-toolkit`. Kiểm nhanh:
   ```bash
   docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
   ```
   Ra bảng là đủ điều kiện. Card cần compute capability ≥ 7.0 để chạy
   `float16` (RTX 2060 Super = 7.5); card cũ hơn (GTX 10xx, CC 6.1) thì trong
   `.env` đổi `FW_COMPUTE=int8`.
2. Docker Engine + plugin compose (`docker compose version`).

## Chạy

```bash
cd dich-vu/gpu-node
cp .env.mau .env          # rồi sửa .env cho đúng mạng nhà (xem dưới)
docker compose up -d
```

Dịch vụ chạy bằng uid không root (`1000`). Dịch vụ `khoi-tao` trong `docker-compose.yml`
sẽ tự động tạo và chown thư mục `./du-lieu` cho uid `1000`. Nếu chuẩn bị thủ công:
```bash
mkdir -p du-lieu/nghe du-lieu/thi-giac du-lieu/tach-am && chown -R 1000:1000 du-lieu
```

Lần đầu `nghe` tải model faster-whisper large-v3 (~3 GB) vào `./du-lieu/nghe`
rồi mới mở cổng — `/health` có thể im vài phút, là bình thường.
Bật thêm dịch vụ trong `.env` (`COMPOSE_PROFILES=nghe,thi-giac,tach-am`)
rồi `docker compose up -d` lại.

**Lấy khoá nối web2api** — khoá sinh ngẫu nhiên lần đầu, ghi file `0600`,
chỉ in ra console **đúng một lần**:

```bash
docker logs gpu-node-cong 2>&1 | grep "KHOA NUT GPU"
```

Lần sau khởi động lại sẽ không in nữa (khoá nằm trong `./du-lieu/khoa`).

## Kiểm bằng curl

Thay `KHOA` bằng khoá vừa lấy, `LAN` bằng địa chỉ đã đặt ở `GPU_NODE_BIND`:

```bash
curl -s http://LAN:8091/health -H "Authorization: Bearer KHOA" | python3 -m json.tool
# {"ok": true, "phien_ban": "1.0.0",
#  "gpu": [{"ten": "...", "vram_tong_mb": 8192, "vram_dung_mb": 1200,
#            "nhiet_c": 55, "tai_pct": 12}],
#  "mo_hinh_da_nap": ["large-v3"], "hang_doi": {"dang_chay": 0, "cho": 0}}

curl -s http://LAN:8091/.well-known/w2a-node -H "Authorization: Bearer KHOA"
# {"id": "gpu-node", "ten": "Máy GPU", "kha_nang": ["nghe", ...],
#  "mo_hinh": [...], "gioi_han_dong_thoi": 1}
```

Không khoá → 401; sai khoá → 401; IP ngoài danh sách cho phép → 403;
nút đang bận (vượt giới hạn đồng thời) → 429 kèm `Retry-After`.

## Nối vào web2api

Trang Hệ thống → Module → «＋ Thêm máy GPU»: nhập địa chỉ `http://LAN:8091`
rồi dán khoá → nút «Kiểm tra» đọc manifest, hiện tên card/VRAM/khả năng/model.
(NV140 làm giao diện này; nếu chưa có thì dùng «Thêm module → Từ xa».)

## An toàn mạng

- Cổng `cong` chỉ công bố trên địa chỉ LAN đặt ở `GPU_NODE_BIND`
  (mặc định `127.0.0.1`) — **không mở ra Internet**.
- Khuyên đặt `GPU_NODE_IP_CHO_PHEP` bằng IP của máy chạy web2api.
- Khác mạng nhà: dùng Tailscale / WireGuard / Cloudflare Tunnel, không forward
  cổng 8091 ra Internet.
- Không telemetry gửi ra ngoài, không tự cập nhật; ảnh ghim phiên bản;
  container chạy uid không root, `--cap-drop ALL`, `no-new-privileges`.

## Dịch vụ

| Dịch vụ | Profile | Việc | Đường |
|---|---|---|---|
| `cong` | luôn bật | Lớp cổng: xác thực, `/health`, manifest, hàng đợi, chuyển tiếp | `:8091` |
| `nghe` | `nghe` | faster-whisper large-v3, đúng đường OpenAI `POST /v1/audio/transcriptions` (`json`/`text`/`verbose_json` có mốc từng từ + `avg_logprob`) | nội bộ `:5000` |
| `thi-giac` | `thi-giac` | llama-server + Qwen3-VL-2B Q4_K_M + mmproj F16 (~2 GB VRAM), đường OpenAI `POST /v1/chat/completions` | nội bộ `:8080` |
| `tach-am` | `tach-am` | audio-separator (MDX23C.onnx), đường riêng `POST /v1/audio/tach-am` | nội bộ `:5000` |

**Chọn model thị giác:** Qwen3-VL-2B-Instruct-GGUF `Q4_K_M` + `mmproj` F16 —
2B tham số lượng tử hoá 4-bit vừa ~2 GB VRAM, còn chỗ cho faster-whisper
int8 (~2.9 GB) trên card 8 GB; llama.cpp hỗ trợ đa phương thức qua
`/v1/chat/completions` và tự unload khi rảnh. Giấy phép model: Apache 2.0
(theo công bố của Qwen cho dòng Qwen3-VL — tự kiểm lại trên trang
Hugging Face của model khi tải).

**Ảnh thị giác:** `ghcr.io/ggml-org/llama.cpp:server-cuda` (tag nổi). Khi dựng
thật nên ghim digest: `docker pull` ảnh rồi chép `@sha256:...` vào
`docker-compose.yml` (release mới nhất của llama.cpp lúc viết: v0.7.0).

## CHƯA kiểm

- Chưa chạy Docker thật, chưa dựng ảnh thật, chưa chạy với card NVIDIA, chưa đo VRAM/tốc độ thật.
- Chưa kiểm digest ghim của ảnh llama.cpp (ghi ở trên).
- Chưa kiểm giấy phép model trực tiếp trên trang Hugging Face lúc tải.

## Cập nhật

Máy này không có watchtower: `docker compose pull && docker compose up -d`.
