# Module camera (NV167)

Xem ảnh chụp/luồng camera trong nhà (go2rtc, URL RTSP khai thẳng) và phát tiếng
qua loa camera khi cần. Mã giao thức kênh nói chép chạy thật từ c2a (`xem báo cáo
nv167-bao-cao.md «Tận dụng từ c2a»).

## Điểm nổi bật

- **URL luồng chứa mật khẩu → bí mật**: chỉ ở trường cấu hình `danh_sach_camera`
  (kiểu `bi_mat`), không bao giờ vào log/phản hồi/ảnh chụp/trạng thái.
- **Loa/bộ đàm mặc định TẮT**: chỉ có thực thể ghi `cam.phat_<tên>` khi cấu hình
  `loa_bat=true`; phát bị giới hạn thời lượng (mặc định 10 s, module + trần 30 s
  ở dịch vụ phụ) và giãn cách 2 s.
- **Không lưu ảnh mặc định**; ảnh cho AI chỉ khi bật `cho_ai_xem` (mặc định tắt);
  ảnh qua dịch vụ phụ luôn thu nhỏ (người xem ≤ 1600 px, AI ≤ 768 px).

## Cấu hình

| Khoá | Kiểu | Lấy ở đâu |
|---|---|---|
| `danh_sach_camera` | bí mật (JSON) | Chép từ go2rtc (phần «Luồng») hoặc thông tin camera: mỗi phần tử `{"name":"Cửa","kind":"go2rtc","base":"http://10.0.0.6:1984","src":"cua"}` hoặc `{"name":"Phòng khách","kind":"rtsp","url":"rtsp://admin:MK@10.0.0.6/stream"}`. Thêm `src_ai`/`url_ai` cho luồng phụ của AI. |
| `dich_vu_url` | chuỗi | URL nội bộ của `dich-vu/camera` đã dựng (mặc định `http://127.0.0.1:8714`) |
| `dich_vu_khoa` | bí mật | TỰ ĐẶT (≥ 24 ký tự) — chính là `CAM_KHOA` đã đặt khi chạy dịch vụ phụ |
| `loa_bat` | chuỗi | `true`/`false` (mặc định `false`) |
| `cho_ai_xem` | chuỗi | `true`/`false` (mặc định `false`) |
| `phat_toi_da_giay` | chuỗi | Số giây, mặc định `10` |
| `ma_webhook` | bí mật | TỰ ĐẶT (chuỗi ngẫu nhiên) — cấu hình vào Frigate (`webhook: http://web2api/api/webhook/camera/<mã>`) |

## Hành động

Đọc (thực thể `cam.<tên>`): `chup`, `chup_cho_ai`, `canh_bao_moi_nhat`.
Ghi (thực thể `cam.phat_<tên>`, chỉ khi `loa_bat=true`): `phat`
(`am_thanh_b64` WAV/MP3), `bo_dam_mo` → `bo_dam_them` (`alaw_b64`) → `bo_dam_dong`.

## Lưu ý khi gộp

- Dịch vụ phụ cần ảnh Docker dựng thật (VM soạn bài không có Docker daemon).
- Chưa kiểm với camera thật (chỉ go2rtc giả + camera giả qua loopback).
- Chi tiết trong `docs/THAM-CHIEU.md` và báo cáo `nv167-bao-cao.md` (outbox).
