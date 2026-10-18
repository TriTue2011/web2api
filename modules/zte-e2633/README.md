# Module zte-e2633 — nguồn máy mạng WiFi (chỉ đọc)

Đăng nhập bộ phát WiFi **ZTE E2633**, đọc danh sách máy WiFi đang nối, trả thành
thực thể chuẩn C1 cho sổ thiết bị của lõi (`app/so_thiet_bi.py`). Module đầu tiên
khai khả năng `thiet_bi` có `thuoc_tinh.mac`.

## Giao thức (đo từ trang đăng nhập của thiết bị)

1. `GET /` → lấy số `_sessionTOKEN` trong form.
2. `GET /function_module/login_module/login_page/logintoken_lua.lua` → token đăng nhập.
3. `POST /` với **đúng thứ tự trường** `Username`, `Password` (= sha256(mật khẩu + token)),
   `action=login`, `Frm_Logintoken`, `captchaCode` (rỗng), `_sessionTOKEN`, kèm header
   `check` = SHA-256 của đúng chuỗi thân → cookie phiên. Thiếu `check` hoặc sai thứ
   tự → thiết bị trả trang «đã quá hạn».
4. `GET /getpage.lua?pid=1005&nextpage=topo_lua.lua&Action=GetALLClients` → JSON máy WiFi.

Bộ phát dùng HTTPS với chứng chỉ tự ký.

## Thực thể trả về

| Trường | Giá trị |
|---|---|
| `id` | `<tien_to>.<mac không dấu hai chấm>` (vd `zte-e2633.aabbcc000001`) |
| `ten` | tên tự khai đã làm sạch (bỏ ký tự điều khiển, ≤ 40 ký tự) |
| `loai` | `may_mang` |
| `thuoc_tinh.mac` | MAC chuẩn `aa:bb:cc:dd:ee:ff` |
| `thuoc_tinh.ip` | IP hiện tại (nếu hợp lệ) |
| `thuoc_tinh.ten_may_tu_khai` | tên đã làm sạch |
| `thuoc_tinh.bang_tan` | `"2.4G"` (AccessType 1), `"5G"` (AccessType 2), `null` nếu lạ |
| `thuoc_tinh.rssi` | số nguyên hoặc `null` |
| `thuoc_tinh.nguon` | `"wifi"` |
| `rui_ro_mac_dinh` | `"doc"` |
| `tinh_nang` | `[]` — `thuc_hien` luôn từ chối |

## Quy tắc an toàn

- Mật khẩu không vào log/URL/phản hồi; mỗi chu kỳ đăng nhập tối đa 1 lần.
- Gặp tín hiệu khoá của thiết bị hoặc ≥ 3 lần đăng nhập lỗi liên tiếp → **dừng hẳn**,
  không thử lại (thiết bị khoá sau vài lần sai).
- `dia_chi` qua đúng `app.tu_xa.kiem_url_lan` (chống SSRF của lõi).
- TLS mặc định kiểm chứng chỉ; đặt `kiem_chung_chi_tls=false` hoặc ghim `van_tay_tls`.
- Không dùng proxy môi trường (`trust_env=False`); trần phản hồi 1 MB; hết giờ 10 s.

## Giới hạn

- Ánh xạ trường JSON (`MACAddress`, `AccessType`…) suy từ tóm tắt giao thức của chủ kho,
  **chưa kiểm với bộ phát thật** — thiết bị thật có thể đặt tên trường khác.
- Tín hiệu «khoá tài khoản» của thiết bị thật chưa thấy tận mắt; hiện nhận theo cấu
  trúc JSON cờ khoá + quy tắc dừng sau lỗi lặp.
