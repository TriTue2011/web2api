# Module mikrotik-routeros — Nguồn máy mạng từ router MikroTik (CHỈ ĐỌC)

Đọc bảng DHCP lease (`/ip/dhcp-server/lease/print`) và bảng host
(`/interface/bridge/host/print`) của router MikroTik qua RouterOS API (giao thức
câu-từ nhị phân, TLS cổng `8729` mặc định), gộp theo địa chỉ MAC thành thực thể
chuẩn thiết bị C1 (`loai="may_mang"`, `thuoc_tinh.mac`) cho sổ thiết bị của lõi.
Lõi dùng `thuoc_tinh.mac` để nhận diện máy mạng.

## Chỉ đọc theo cấu tạo

- Lớp client (`routeros.py`) từ chối mọi lệnh KHÔNG phải dạng «`/…/print`» **trước
  khi gửi** (không byte lệnh nào tới router): `add`, `set`, `remove`,
  `/system/reboot`, lệnh giả dạng có dấu cách/`;`/`=`/`?`/xuống dòng đều bị chặn.
  Lọc theo cấu trúc lệnh, không liệt kê tên lệnh.
- `thuc_hien` của module luôn từ chối — module không có hành động nào.
- **Khuyến nghị:** tạo tài khoản RouterOS chỉ có quyền đọc (nhóm `read`) trong
  `/user`, vì module chỉ gọi các lệnh `.../print`.

## Bảo mật

- Địa chỉ router (`dia_chi`, dạng `host[:cổng]`) được kiểm đúng luật
  `app/tu_xa.py`: chặn loopback (trừ test), link-local/metadata đám mây
  (`169.254.0.0/16`), địa chỉ không định tuyến; tên miền được phân giải và ghim
  IP đã kiểm (chống DNS rebinding).
- Mặc định **chỉ TLS** (cổng `8729`). Cờ `cho_phep_khong_ma_hoa` (mặc định
  `false`) cho phép nối trần (cổng `8728`) — **cảnh báo:** mật khẩu đi dạng thô
  trên mạng; chỉ dùng trong LAN tin cậy và nên tránh.
- Vân tay SHA-256 của chứng chỉ (`van_tay_tls`) được kiểm **trước khi gửi mật
  khẩu** (sai → đóng kết nối, chưa hề đăng nhập). Nếu router dùng chứng chỉ tự
  ký, ghim vân tay thay vì tắt `kiem_chung_chi_tls`.
- Mật khẩu không bao giờ vào log, phản hồi lỗi, ngoại lệ hay nhật ký.
- Hết giờ kết nối + đọc; trần độ dài mỗi câu (1 MiB) và trần 50 câu mỗi lượt đọc
  chống bộ nhớ phình; câu sai khuôn/ngắt giữa chừng → bỏ, không sập lõi.
- Mọi giá trị từ router là không tin: tên máy tự khai bị bỏ ký tự điều khiển và
  cắt 40 ký tự; MAC sai khuôn/broadcast/zero bị bỏ.

## Quy ước thực thể

- `id = "<tien_to>.<mac viết thường, bỏ dấu hai chấm>"`, ví dụ
  `mikrotikrouteros.aabbcc000001`.
- `ten` = tên máy tự khai (host-name) đã làm sạch; máy chỉ có trong bảng host thì
  `ten` rỗng.
- `thuoc_tinh`: `mac` (dạng `aa:bb:cc:00:00:01`), `ip` (từ DHCP; máy chỉ có
  trong bảng host thì `null`), `ten_may_tu_khai`, `cong` (on-interface từ bảng
  host), `nguon`: `"dhcp"` | `"host"`.
- `rui_ro_mac_dinh = "doc"`, `tinh_nang = []`.

## Giới hạn

- Chưa kiểm với router MikroTik thật (chỉ kiểm với máy chủ RouterOS giả dựng
  bằng thư viện chuẩn trong test).
- Không hỗ trợ WireGuard/Kid Control/bridge port (có trong mã tham khảo của chủ
  kho nhưng ngoài phạm vi CHỈ ĐỌC máy mạng của nhiệm vụ).
