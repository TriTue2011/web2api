# Quy chuẩn Module của web2api

Tài liệu này quy định hợp đồng kỹ thuật giữa **lõi web2api** («phiên dịch viên») và các **module**.
Nguồn gốc hợp đồng được định nghĩa tại phần HỢP ĐỒNG trong [app/kham_pha.py](file:///opt/claude-c2a/agy-dieu-phoi/wt/c1-thiet-bi/app/kham_pha.py).

---

## 1. Nguyên tắc cốt lõi: Lõi đặt chuẩn, module đổi theo

1. **Lõi là phiên dịch viên trung lập**: Lõi web2api cung cấp các chuẩn giao tiếp chung (chuẩn OpenAI `/v1/*`, chuẩn thiết bị `/api/thiet-bi`, chuẩn khám phá module). Lõi không chứa tên nhà cung cấp, dịch vụ hoặc phần cứng cụ thể nào.
2. **Module thích ứng theo lõi**: Mọi chuyển đổi giao thức, định dạng dữ liệu, xử lý lỗi đặc thù của bên thứ ba đều phải nằm hoàn toàn bên trong thư mục `modules/<id>/`. Nếu dịch vụ bên ngoài thay đổi API, chỉ sửa module tương ứng, lõi không thay đổi.
3. **Mã mở (Open Extension)**: Các định danh loại (`loai`), khả năng (`cung_cap`), rủi ro hoặc tính năng đều tuân theo nguyên tắc mã mở: những mã lạ chưa có trong danh mục của lõi vẫn được chấp nhận nguyên văn, không bị chặn và không làm vỡ hệ thống.
4. **Kiểm chuẩn lúc nạp**: Khi nạp một module, nếu module khai một khả năng đã có quy chuẩn mà thiếu phương thức bắt buộc, lõi vẫn nạp module vào catalog nhưng đánh dấu «không đúng chuẩn», ghi cảnh báo vào nhật ký và hiển thị trên giao diện Hệ thống → Module. Đường gọi tương ứng sẽ từ chối phục vụ khả năng thiếu chuẩn đó thay vì gây lỗi sập tiến trình.

---

## 2. Bảng hợp đồng khả năng và phương thức

Mọi module triển khai lớp `Module` trong `module.py` với các phương thức nền tảng:
- `__init__(manifest: dict)`: Khởi tạo nhẹ, không gọi mạng.
- `nap(cau_hinh: dict) -> None`: Nạp cấu hình đã lưu.
- `async cai(cau_hinh: dict) -> dict`: Kiểm tra cấu hình và trả về `{ok, ket_qua_tung_khoa, loi, cau_hinh_luu}`.
- `async models() -> list[dict]`: Liệt kê các model dạng `<tien_to>/<tên>`.
- `async suc_khoe() -> dict`: Kiểm tra trạng thái sức khoẻ, hạn mức.
- `async dung() -> None`: Dọn dẹp kết nối khi dừng.

Đối với từng mã khả năng khai trong trường `cung_cap` của `manifest.json`:

| Mã khả năng | Phương thức bắt buộc | Phương thức tuỳ chọn | Đầu vào | Đầu ra chuẩn | Mã lỗi thường gặp |
|---|---|---|---|---|---|
| `chat` | `chat` | — | `body: dict, stream: bool` | `(mã, headers, bytes \| iterator)` | 400 (thiếu model), 429 (hạn mức), 502 (lỗi dịch vụ) |
| `anh` | `anh` | — | `body: dict` | `(mã, headers, bytes)` | 400 (thiếu prompt), 502 |
| `video` | `video_tao`<br>`video_trang_thai`<br>`video_noi_dung` | — | `body: dict` (tạo)<br>`ma: str` (trạng thái/nội dung) | `(mã, headers, bytes)` (tạo/trạng thái)<br>`(mã, headers, bytes \| iterator)` (nội dung) | 400, 404 (mã video), 415 (gửi tệp chưa hỗ trợ), 501 |
| `noi` | `noi` | — | `body: dict` | `(mã, headers, bytes \| iterator)` | 400, 501, 502 |
| `nghe` | `nghe` | — | `yc: YeuCauDaPhan` | `(mã, headers, bytes)` | 400 (thiếu tệp audio), 501, 502 |
| `anh_sua` | `anh_sua` | — | `yc: YeuCauDaPhan` | `(mã, headers, bytes)` | 400 (thiếu ảnh/prompt), 501, 502 |
| `thiet_bi` | `thuc_the`<br>`trang_thai`<br>`thuc_hien` | `su_kien` | `loc: dict \| None` (thực thể)<br>`ids: list[str]` (trạng thái)<br>`id, hanh_dong, tham_so` (thực hiện) | `list[dict]` (thực thể)<br>`dict[str, dict]` (trạng thái)<br>`dict` `{ok, trang_thai_moi?, loi?}` (thực hiện)<br>`async iterator[dict]` (sự kiện) | 400 (hành động không hỗ trợ), 404 (không thấy thực thể), 409 (cần xác nhận), 202 (vào hàng chờ), 502 |
| *(Mã lạ)* | Không bắt buộc | Tự do | Tuỳ ý | Tuỳ ý | — |

---

## 3. Chi tiết chuẩn `thiet_bi`

### 3.1. Cấu trúc thực thể (`thuc_the`)
Mỗi thực thể do module trả về phải có cấu trúc từ điển:
```json
{
  "id": "<tien_to>.<tên_thuc_the>",
  "ten": "Tên hiển thị thân thiện",
  "loai": "den | cong_tac | cam_bien | camera | may_chu | cong_mang | ... (mã mở)",
  "khu": "Phòng khách | Tầng 1 | ... (tuỳ chọn nhóm/vị trí)",
  "thuoc_tinh": {
    "ip": "192.168.1.50"
  },
  "tinh_nang": ["bat", "tat", "do_sang"],
  "rui_ro_mac_dinh": "doc | ghi | nguy_hiem"
}
```
- **Quy tắc tiền tố**: `id` BẮT BUỘC bắt đầu bằng `<tien_to>.` khớp với `tien_to` khai báo trong `manifest.json`. Lõi sẽ bỏ qua các thực thể sai tiền tố hoặc trùng lặp `id` mà không làm hỏng toàn bộ danh sách.
- **Mức rủi ro**:
  - `"doc"`: Chỉ đọc dữ liệu, không làm thay đổi trạng thái thiết bị.
  - `"ghi"`: Thao tác thông thường (bật, tắt, đổi trạng thái công tắc/đèn).
  - `"nguy_hiem"`: Thao tác có tính chất rủi ro cao (mở khoá cửa, khởi động lại hệ thống mạng, xoá dữ liệu).

### 3.2. Trạng thái (`trang_thai`)
Nhận danh sách `ids: list[str]` và trả về từ điển:
```json
{
  "<tien_to>.<tên>": {
    "gia_tri": "on",
    "don_vi": "W",
    "luc": "2026-10-12T15:00:00Z",
    "hop_le": true
  }
}
```

### 3.3. Thực hiện hành động (`thuc_hien`)
- Gọi khi khách hoặc quản trị kích hoạt một hành động nằm trong `tinh_nang` của thực thể.
- Nhận `id: str`, `hanh_dong: str`, `tham_so: dict`.
- Trả về: `{"ok": bool, "trang_thai_moi": {...}, "loi": "thông điệp nếu thất bại"}`.

### 3.4. Sự kiện thời gian thực (`su_kien`)
- Tuỳ chọn cho module có khả năng đẩy sự kiện thời gian thực (Server-Sent Events).
- Trả về async iterator sinh các đối tượng `{id, loai, gia_tri, luc}`.
