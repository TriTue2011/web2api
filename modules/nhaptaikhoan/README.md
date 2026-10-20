# Module nhaptaikhoan — Nhập tài khoản hàng loạt (xem trước)

Xem trước danh sách tài khoản từ dịch vụ khác ở dạng **CHE** (không bí mật thô):
tệp sao lưu 9router (JSON / JSON.gz), máy chủ sub2api (đăng nhập JWT hoặc
`x-api-key`), và CLIProxyAPI/CPA (chỉ liệt kê tệp auth, không tải token).

> **Phạm vi NV164 (chưa nối kho):** module này chỉ đọc + xem trước. Kho tài
> khoản/danh tính của lõi (NV144, NV72) chưa gộp — hành động `ap_dung` là giao
> diện nhận danh sách đã duyệt, hiện luôn từ chối rõ ràng
> `chua_noi_kho_tai_khoan` và không ghi vào đâu.

## Hành động

Thực thể `nhaptaikhoan.nguon` (rủi ro `nguy_hiem` — quản trị duyệt; **chỉ quản
trị gọi được**):

| Hành động | Tham số | Kết quả |
|---|---|---|
| `xem_truoc_tep` | `dang: "ninerouter"`, `noi_dung_base64` (nội dung tệp 9router) | Danh sách kết nối ở dạng che: tên, nhà cung cấp, email `a***@mien`, khoá `…abcd`, số combo |
| `xem_truoc_may_chu` | `loai: "sub2api"\|"cpa"`, `may_chu_url`, `khoa_api` (hoặc `email`+`mat_khau` cho sub2api) | Danh sách tài khoản/tệp auth dạng che |
| `ap_dung` | `danh_sach` (mục `{nguon, dinh_danh}`) | Luôn từ chối `chua_noi_kho_tai_khoan` (chờ NV144/NV72) |

Khoá/mật khẩu khai **theo từng lần xem trước**, không lưu. Địa chỉ máy chủ qua
đúng `app/tu_xa.kiem_url_lan` (chống SSRF: chặn loopback/link-local/không định
tuyến; LAN riêng được phép) trước khi nối.

## Định dạng hỗ trợ (NV164)

| Nguồn | Hỗ trợ | Bỏ / lý do |
|---|---|---|
| 9router exportDb (`.json`, `.json.gz`) | Có — nhận diện `providerConnections` (kể cả lồng trong `data`), trích token `codex/cursor/openai` (bắt đầu `eyJ`) và mọi kết nối OAuth khác | YAML/PDF không nhận (không có định dạng chuẩn cho xuất 9router; tránh kéo `yaml`) |
| sub2api admin API | Có — `x-api-key` hoặc đăng nhập `/api/v1/auth/login` (JWT), liệt kê `/api/v1/admin/accounts` phân trang | Ghi tài khoản vào kho — chờ kho lõi (NV144/NV72) |
| CPA `/v0/management/auth-files` | Có — chỉ **liệt kê** tệp | Tải token từng tệp (`/download`) — thuộc phía «áp dụng», chưa làm |

## Nguồn chép từ c2a (chủ máy, commit `d879b03`, chỉ đọc)

`services/ninerouter_backup_import.py` (nhận diện/trích token/combo — không chép
phần ghi), `services/sub2api_service.py` (bóc phong bì, trích trang, đăng nhập
JWT — `curl_cffi` đổi sang `httpx`), `services/cpa_service.py` (liệt kê tệp —
không chép tải token). Kho đăng ký máy chủ và mọi đường ghi của c2a **không
chép**: module không giữ trạng thái, kho đích chưa có.
