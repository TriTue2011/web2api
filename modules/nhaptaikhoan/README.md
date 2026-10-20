# Module nhaptaikhoan — Nhập tài khoản hàng loạt

Xem trước danh sách tài khoản từ dịch vụ khác ở dạng **CHE** (không bí mật thô):
tệp sao lưu 9router (JSON / JSON.gz), máy chủ sub2api (đăng nhập JWT hoặc
`x-api-key`), và CLIProxyAPI/CPA (chỉ liệt kê tệp auth, không tải token).

> **NV197e (đã nối kho):** hành động `ap_dung` ghi danh sách ĐÃ DUYỆT vào kho
> tài khoản của lõi (`app/kho_tai_khoan.nhap_nhieu_tai_khoan`) — cùng kho,
> không kho thứ hai; khử trùng theo (nguồn, định danh); ghi nguyên tử một lần;
> báo cáo từng dòng (`da_them`/`trung`/`bo_qua`/`loi`) không lộ bí mật.

## Hành động (NV164-SUA: hai thực thể tách theo mức rủi ro)

| Thực thể | Rủi ro | Hành động |
|---|---|---|
| `nhaptaikhoan.xem_truoc` — xem trước, chỉ đọc | `doc` | `xem_truoc_tep`, `xem_truoc_may_chu` |
| `nhaptaikhoan.ap_dung` — áp dụng vào kho | `nguy_hiem` | `ap_dung` |

**Chỉ quản trị gọi được** (cổng quản trị nội bộ của module kiểm mọi hành động —
hành động chỉ chạy dưới đúng thực thể của nó):

| Hành động | Tham số | Kết quả |
|---|---|---|
| `xem_truoc_tep` | `dang: "ninerouter"`, `noi_dung_base64` (tối đa **8000 ký tự** — trần tham số 8 KB của lõi) | Danh sách kết nối ở dạng che: tên, nhà cung cấp, email `a***@mien`, khoá `…abcd`, số combo |
| `xem_truoc_may_chu` | `loai: "sub2api"\|"cpa"`, `may_chu_url`, `khoa_api` (hoặc `email`+`mat_khau` cho sub2api) | Danh sách tài khoản/tệp auth dạng che |
| `ap_dung` | `danh_sach` (tối đa **20 mục**/lần; mục `{nguon, dinh_danh, bi_mat, email?, nhan?}` — `bi_mat` là bí mật, xem mô tả trường để biết lấy ở đâu) | Ghi vào kho lõi: `da_them`/`trung` (khử trùng theo nguồn+định danh, không ghi đè)/`bo_qua` (thiếu bí mật)/`loi` — nguyên tử một lần, không lộ bí mật. Thực thể `nguy_hiem`: lõi đòi step-up + `xac_nhan` |

> Giới hạn tệp nhập: vì dữ liệu gửi qua tham số hành động của lõi, lõi cắt toàn
> bộ tham số ở 8 KB → tệp base64 tối đa ~8000 ký tự (≈6 KB tệp thô, ≈60 kết nối
> 9router). Tệp lớn hơn nhận 400 rõ ràng. Lỗi này là trần của lõi, không nâng
> được ở phía module.

Khoá/mật khẩu khai **theo từng lần xem trước**, không lưu. Địa chỉ máy chủ qua
đúng `app/tu_xa.kiem_url_lan` (chống SSRF: chặn loopback/link-local/không định
tuyến; LAN riêng được phép) trước khi nối, rồi nối qua client **ghim IP đã
kiểm** (`TransportGhimIp` của lõi — chống DNS rebinding); thân phản hồi máy chủ
nguồn đọc có trần 2 MB.

## Định dạng hỗ trợ (NV164)

| Nguồn | Hỗ trợ | Bỏ / lý do |
|---|---|---|
| 9router exportDb (`.json`, `.json.gz`) | Có — nhận diện `providerConnections` (kể cả lồng trong `data`), trích token `codex/cursor/openai` (bắt đầu `eyJ`) và mọi kết nối OAuth khác | YAML/PDF không nhận (không có định dạng chuẩn cho xuất 9router; tránh kéo `yaml`) |
| sub2api admin API | Có — `x-api-key` hoặc đăng nhập `/api/v1/auth/login` (JWT), liệt kê `/api/v1/admin/accounts` phân trang | Ghi vào kho qua `ap_dung` (NV197e) |
| CPA `/v0/management/auth-files` | Có — chỉ **liệt kê** tệp | Tải token từng tệp (`/download`) — thuộc phía «áp dụng», chưa làm |

## Nguồn chép từ c2a (chủ máy, commit `d879b03`, chỉ đọc)

`services/ninerouter_backup_import.py` (nhận diện/trích token/combo — không chép
phần ghi), `services/sub2api_service.py` (bóc phong bì, trích trang, đăng nhập
JWT — `curl_cffi` đổi sang `httpx`), `services/cpa_service.py` (liệt kê tệp —
không chép tải token). Kho đăng ký máy chủ và mọi đường ghi của c2a **không
chép**: module không giữ trạng thái, kho đích chưa có.
