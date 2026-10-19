# Module zte-e2633 — nguồn máy mạng WiFi (chỉ đọc)

## ⚠️ VAI TRÒ DỰ PHÒNG — ĐỌC TRƯỚC KHI BẬT

Theo quyết định đã chốt của chủ máy: **chỉ MỘT bên được đăng nhập bộ phát
ZTE E2633**. Tích hợp Home Assistant `mikrotik_snmp` của chủ máy đang giữ đăng
nhập liên tục — web2api đọc danh sách máy qua module `home-assistant` (thực thể
do `mikrotik_snmp` tạo), KHÔNG đăng nhập ZTE trực tiếp.

**Chỉ bật module này khi KHÔNG có Home Assistant đọc ZTE.** Bộ phát có thể chỉ
cho **một phiên quản trị**: đừng chạy module này đồng thời với tích hợp khác
đang đọc ZTE — đăng nhập song song có thể bị từ chối («trang đã quá hạn») hoặc
đá phiên của bên kia.

Đăng nhập bộ phát WiFi **ZTE E2633**, đọc danh sách máy WiFi đang nối, trả thành
thực thể chuẩn C1 cho sổ thiết bị của lõi (`app/so_thiet_bi.py`).

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

## Quy tắc an toàn (phiên bản 0.2.0)

- `cai()`: kiểm biên (SSRF qua `app.tu_xa.kiem_url_lan`, trường bắt buộc) rồi **lưu
  cấu hình ngay** — đăng nhập thử lỗi không còn là lý do bỏ lưu. Đăng nhập thử chỉ
  sinh cảnh báo có cấu trúc (`ma_loi` tiếng Việt + `canh_bao` kèm hướng dẫn).
- Mật khẩu không vào log/URL/phản hồi; lưu riêng ở `bi_mat/` quyền 0600.
- **MỘT PHIÊN**: giữ phiên giữa các lần đọc; hết phiên đăng nhập lại đúng một lần;
  `dung()` chỉ quên phiên phía client, không gửi yêu cầu đăng xuất.
- `cai()` đăng nhập thử thành công thì **để cookie phiên lại** cho bản phục vụ
  (thể hiện mới do lõi dựng sau khi cài) dùng luôn — không đăng nhập lần nữa,
  không giữ phiên mồ côi (sổ trong tiến trình, dùng một lần rồi xoá; cookie
  lạ/hết hạn tự hồi phục ở lần đọc).
- `suc_khoe()` không đăng nhập lại mỗi lần gọi: dùng kết quả lần đọc gần nhất
  (trong 5 phút) hoặc đăng nhập tối đa 1 lần mỗi `khoang_cach_dang_nhap_phut`
  (mặc định 10).
- Bị từ chối (sai mật khẩu/khoá) → **tạm ngừng 30 phút rồi tự hồi phục**; trang
  lạ/token rỗng KHÔNG phải từ chối → không tạm ngừng dài (phân loại bằng cấu
  trúc: loại ngoại lệ, mã HTTP, có/không JSON đúng khoá — không dò chữ).
- Đọc hỏng (JSON sai khuôn) là **lỗi**: giữ dữ liệu cũ, không trả «0 máy».
- `dia_chi` qua đúng `app.tu_xa.kiem_url_lan` (chống SSRF của lõi).
- TLS mặc định kiểm chứng chỉ; đặt `kiem_chung_chi_tls=false` hoặc ghim `van_tay_tls`.
- Không dùng proxy môi trường (`trust_env=False`); trần phản hồi 1 MB; hết giờ 10 s.

## Giới hạn

- Ánh xạ trường JSON (`MACAddress`, `AccessType`…) suy từ tóm tắt giao thức của chủ kho,
  **chưa kiểm với bộ phát thật** — thiết bị thật có thể đặt tên trường khác.
- Tín hiệu «khoá tài khoản» của thiết bị thật chưa thấy tận mắt; hiện nhận theo cấu
  trúc JSON cờ khoá + quy tắc dừng sau lỗi lặp. **Chưa xác nhận trên bộ phát thật**
  — nhánh này có test với bộ phát giả (`test_l6_bi_khoa_co_test_va_tam_ngung_dai`)
