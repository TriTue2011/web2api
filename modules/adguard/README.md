# Module adguard — AdGuard Home trong LAN (NV278)

Đọc thống kê DNS chặn quảng cáo của cả nhà và bật/tắt bảo vệ, thay cho việc
chỉ xem được qua Home Assistant. Chỉ thư viện chuẩn (`urllib`), không thêm gói.

## Thực thể (chuẩn `thiet_bi`, không có route riêng)

| Thực thể | Loại | Rủi ro | Nội dung |
|---|---|---|---|
| `adguard.tong_truy_van` | cảm biến | `doc` | Tổng truy vấn DNS (cửa sổ thống kê của máy chủ) |
| `adguard.so_bi_chan` | cảm biến | `doc` | Số truy vấn bị chặn bởi bộ lọc |
| `adguard.ti_le_chan` | cảm biến | `doc` | Tỉ lệ chặn, % (làm tròn 2 chữ số) |
| `adguard.thoi_gian_xu_ly_trung_binh` | cảm biến | `doc` | Thời gian xử lý trung bình, giây |
| `adguard.so_bo_loc_dang_bat` | cảm biến | `doc` | Số bộ lọc đang bật |
| `adguard.bao_ve` | công tắc | `nguy_hiem` | Trạng thái bảo vệ + top miền bị chặn / top máy khách trong `thuoc_tinh` (giới hạn theo `gioi_han_top`, chuỗi cắt 64 ký tự) |

## Hành động duy nhất: `bao_ve`

Tham số: `bat` (bool, bắt buộc), `thoi_gian_giay` (int 0–86400, chỉ dùng
khi `bat=false`; bỏ trống = thời gian tắt mặc định đã cấu hình, mặc định
300 giây = 5 phút; `0` = vô thời hạn, không tự bật lại; trần 86400 giây).

Vì `rui_ro_mac_dinh` là `nguy_hiem`, hành động đi đúng cơ chế của lõi
`app/thiet_bi.py`: khách thiếu quyền `thiet_bi:ghi` -> 403; khách gọi
nguy hiểm -> 202 vào hàng chờ phê duyệt của quản trị; quản trị gọi thiếu
`xac_nhan: true` -> 409 `can_xac_nhan`; nhật ký kiểm toán của lõi ghi ai
làm, lúc nào. Module chỉ ghi thêm một dòng log trạng thái + thời hạn (không
mật khẩu, không header, không địa chỉ). Không có hành động ghi nào khác:
không sửa bộ lọc, không xoá nhật ký.

## Địa chỉ và bí mật

- `dia_chi` dạng `host[:cổng]`, cổng mặc định 3000. Chỉ LAN: IP công cộng,
  loopback (trừ test với `WEB2API_TEST_CHO_PHEP_LOOPBACK=1`), link-local /
  metadata, địa chỉ không định tuyến đều bị từ chối. Tên miền được phân giải
  một lần và ghim IP đã kiểm (chống DNS rebinding); client tắt proxy.
- Mật khẩu nằm trong cấu hình đã lưu của lõi (kho bí mật mã hoá), không vào
  manifest, log, ngoại lệ hay phản hồi.

## API AdGuard Home dùng

`GET /control/status`, `GET /control/stats`, `GET /control/filtering/status`,
`GET /control/clients` (chỉ để lấy tên máy khách, hỏng thì giữ IP),
`POST /control/protection` (`{"enabled": bool, "duration": ms}`).
Cố tình KHÔNG dùng `GET /control/querylog`: top list đã có dạng tổng hợp
trong `/control/stats`, đọc nhật ký thô là không cần thiết và trái yêu cầu
không trả nguyên nhật ký truy vấn. Chưa kiểm với AdGuard thật (NV278).

## Lỗi

`AdGuardError.ma_loi`: `sai_dang_nhap` (401/403), `mang_loi` (không nối
được), `het_gio` (timeout 10 giây), `loi_may_chu` (HTTP khác), `du_lieu_sai`
(JSON/hình dạng sai), `tham_so_sai` (nội bộ). Lỗi không nuốt: `cai()` trả
thông báo rõ, `thuc_the`/`trang_thai` giữ số cũ và gắn mã lỗi khi chưa có
số nào.
