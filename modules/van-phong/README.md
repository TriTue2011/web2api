# Module `van-phong` — Công cụ văn phòng qua container phụ (NV138)

Đọc/sửa tài liệu văn phòng (docx/xlsx/pptx) bằng công cụ `office_*` gọi dịch vụ
`dich-vu/van-phong-office` — OfficeCLI chạy trong container riêng, tập lệnh đóng,
hết giờ, không có mạng ra ngoài. Mã nguồn module: `modules/van-phong/`.

## Từng bước cài đặt

1. **Dựng dịch vụ container phụ** (máy có Docker), xem chi tiết từng bước trong
   `dich-vu/van-phong-office/README.md`:
   ```bash
   cd dich-vu/van-phong-office
   docker build -t van-phong-office:1.0 .
   docker network create --internal mang-office-noi-bo || true
   OFFICE_KHOA='<khoa-dai-ngau-nhien>' docker run -d --name office \
     --network mang-office-noi-bo \
     --memory 512m --pids-limit 64 \
     -p 127.0.0.1:8701:8701 \
     -e OFFICE_KHOA -v office-data:/data van-phong-office:1.0
   ```
   Kiểm còn sống: `curl http://127.0.0.1:8701/suc-khoe` → `{"ok": true}`.

2. **Cài module vào web2api**: Hệ thống → Module → tìm `van-phong` → Cài đặt
   (hoặc `POST /api/nguon-git/cai/van-phong`).

3. **Bật và cấu hình** (`POST /api/modules/van-phong/cai`,
   thân `{"cau_hinh": {...}}`):
   - `office_bat`: `true` — bật công cụ `office_*` (mặc định tắt).
   - `dich_vu_url`: `http://127.0.0.1:8701` (hoặc `http://office:8701` trong mạng Docker).
     Module tự kiểm tra SSRF (`_kiem_url_dich_vu`): cho phép loopback (127.0.0.1) và mạng
     LAN nội bộ/Docker host, chặn link-local 169.254.169.254, multicast và dải cấm.
   - `khoa_dich_vu`: khoá Bearer của dịch vụ (biến `OFFICE_KHOA` lúc chạy
     container). Lưu an toàn trong thư mục bí mật, không bao giờ lộ ra log hay API.

4. **Dùng**: hai thực thể `van_phong.office_doc` (công cụ đọc: `office_view`,
   `office_get`, `office_query`, `office_validate`, `office_dump`, `office_help` —
   rủi ro `doc`) và `van_phong.office_ghi` (công cụ sửa: `office_create`,
   `office_set`, `office_add`, `office_remove`, `office_move`, `office_swap`,
   `office_merge`, `office_save`, `office_import`, `office_batch` — rủi ro `ghi`).
   Tham số: `{"tep": "bao-cao.docx", "doi_so": [...]}` — `tep` là tên tệp trong
   thư mục làm việc của dịch vụ (đưa tệp lên trước qua `POST /tai-len` của dịch
   vụ nếu cần).

Khi `office_bat` tắt hoặc không nối được dịch vụ, mọi công cụ `office_*` trả
`{"ok": false, "loi": "chưa bật"}` (đóng khi lỗi, không lộ chi tiết) và không
gọi mạng. Đầu ra dịch vụ là **dữ liệu không tin**: module cắt ở 80.000 ký tự và
gắn `nguon_ngoai: true`.

## Sự cố thường gặp

- `{"ok": false, "loi": "chưa bật"}` — chưa bật `office_bat`, sai
  `dich_vu_url`/`khoa_dich_vu`, hoặc container dịch vụ chưa chạy.
- `dia_chi_dich_vu_khong_hop_le` lúc cài — địa chỉ trỏ vào dải bị chặn (SSRF);
  dùng địa chỉ nội bộ hợp lệ.
- Dịch vụ báo `lenh_khong_ho_tro` — hành động ngoài tập đóng (kể cả `mcp`).

## Giới hạn

- Chưa kiểm với OfficeCLI thật (chỉ nhị phân giả trong test); chưa đo RAM/đĩa
  của container phụ (điền `null` theo khuôn).
- Đường đọc/sửa zip+XML trực tiếp trong web2api là phần NV132 (việc khác làm
  song song, gộp sau) — module này chỉ thêm công cụ `office_*` qua container phụ.
