# Module `anh-ai` — tạo/sửa ảnh AI (NV192)

Port từ c2a (commit `d879b03`, CHỈ ĐỌC — không sửa c2a): nhóm G (ảnh/video AI).
Nền NEN190, nhánh `muse/nv192`. Lõi `app/` KHÔNG sửa.

## Chức năng

- `POST /v1/images/generations` → `anh(body)` — tạo ảnh, model dạng
  `anh_ai/<nhà>/<tên>` (vd `anh_ai/gemini/imagen-3.0-generate-001`).
- `POST /v1/images/edits` → `anh_sua(yc)` — sửa/inpaint ảnh.
- Thực thể công cụ `anh_ai.hang_doi_anh` (chuẩn C2): hàng đợi tác vụ tạo/sửa
  (queued/running/done/failed, huỷ, dọn tác vụ cũ, phân tách theo chủ).

## Cấu trúc

| Tệp | Nguồn c2a | Chỉnh khi port |
|---|---|---|
| `bo_chuyen_doi/` (10 adapter: `_base`, `bfl`, `cloudflare`, `custom_openai_image`, `fal_ai`, `flow_google`, `gemini_image`, `huggingface`, `nvidia_nim_image`, `sdwebui`, `stability`) | `services/image_providers/*.py` | bỏ `veo_video.py`; bỏ lời gọi `gemini_watermark` trong `normalize()` |
| `hang_doi.py` | `image_task_service.py` | đổi lớp kết nối/khuôn module |
| `kiem_anh.py` | `image_guard.py` | nguyên logic (trần byte/điểm ảnh/khung, magic bytes) |
| `tien_ich_anh.py` | `image_utils.py` | nguyên logic |
| `nhan_anh.py` | `image_tags_service.py` | đổi khuôn |
| `module.py` | `protocol/openai_v1_image_generations.py` + `openai_v1_image_edit.py` | gộp 2 hàm xử lý thành `_chay`; hàng đợi ngay trong module |

Không port: `gemini_watermark.py` (xem `hoi-nv192-gemini-watermark.md` —
khuyến nghị **không làm**), `image_service.py` (thư viện ảnh/cloud, ngoài phạm vi).

## Bảo mật

- Mọi khoá `api_key`/`api_keys` kiểu `bi_mat`: không vào log/phản hồi/`suc_khoe`/
  thông báo lỗi. Bộ lọc log chặn mọi dòng `Authorization … Bearer` và mọi khoá
  đã nạp (`_KHOA_DANG_DUNG`).
- `base_url`/`solver_url` do người dùng khai đi qua **đúng** `app.tu_xa.kiem_url_lan`
  lúc `cai()` (SSRF) — không viết bộ kiểm thứ hai.
- Ảnh trả về là DỮ LIỆU KHÔNG TIN: qua `kiem_anh` (chống bom giải nén) trước khi
  lưu; tên tệp do module sinh (uuid), không lấy từ nhà cung cấp.
- Xoay khoá khi nhà cung cấp trả 400/401/403/429.

## Cấu hình (`cai`)

`nha_cung_cap` (gemini | fal_ai | stability_ai | black_forest_labs | cloudflare_ai |
huggingface | nvidia_nim_image | sdwebui | flow | custom:<id>), `api_key`/`api_keys`,
`base_url` (bắt buộc với `custom:`; tuỳ chọn với sdwebui/gemini), `solver_url` +
`flow_accounts` (bắt buộc với flow), các trần hàng đợi
(`dong_thoi_toi_da`, `dong_thoi_moi_nguoi`, `giu_tac_vu_ngay`).

Mỗi trường khoá có `mo_ta` ghi LẤY Ở ĐÂU — xem bảng trong `manifest.json`.

## Bug phát hiện khi port (đã sửa)

1. Tác vụ sửa qua hàng đợi bị **mất ảnh đầu vào**: `hang_doi` gọi
   `xu_ly_sua(payload)` với 1 tham số, ảnh nằm ở `payload["anh"]` nhưng
   `_sua_dong_bo` không đọc → `_sua_dong_bo` đọc fallback `body["anh"]`
   khi `images is None`.
2. Adapter (vd gemini) **bỏ qua** mục ảnh dạng dict `{"b64_json","mime"}`
   (chỉ hiểu bytes/str-data/tuple) → `_chay` chuẩn hoá thành tuple
   `(bytes, mime)` trước khi gọi `build_body`.
3. `manifest.json` thiếu trường `nhan` → module bị lõi từ chối lúc nạp.

Test: `test/test_web2api_nv192_anh_ai.py` — 39 test, 8/8 đột biến bị giết.
