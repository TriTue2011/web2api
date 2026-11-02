# Module mikrotik-routeros — Kiểm soát mạng LAN từ router MikroTik (CHỈ ĐỌC)

Nguồn thiết bị và cảm biến mạng cho web2api qua RouterOS API (giao thức câu-từ nhị phân, TLS cổng `8729` mặc định):
- Bảng DHCP lease (`/ip/dhcp-server/lease/print`) và bảng host (`/interface/bridge/host/print`) gộp theo MAC thành thực thể chuẩn thiết bị C1 (`loai="may_mang"`, `thuoc_tinh.mac`) cho sổ thiết bị của lõi. Tự động dọn máy vắng mặt quá N ngày (mặc định 14).
- Cổng mạng (`/interface/print`): trạng thái lên/xuống/tắt, tốc độ nhận/gửi Mbit/s tính từ bộ đếm byte.
- Tài nguyên router (`/system/resource/print`): CPU %, RAM %, thời gian chạy router (uptime), nhiệt độ nếu có.
- WireGuard peers (`/interface/wireguard/peers/print`): trạng thái kết nối và thời gian bắt tay gần nhất.
- Kid Control (`/ip/kid-control/device/print`): tốc độ nhận/gửi của từng máy nếu bật.

## Chỉ đọc theo cấu tạo

- Lớp client (`routeros.py`) giới hạn tập lệnh trong danh sách cho phép CỐ ĐỊNH gồm 7 đường:
  `/ip/dhcp-server/lease/print`, `/interface/bridge/host/print`, `/interface/print`,
  `/system/resource/print`, `/interface/wireguard/peers/print`, `/ip/kid-control/device/print`,
  và `/interface/monitor-traffic` (chỉ qua hàm đo một lần với tên cổng kiểm cấu trúc).
- Mọi lệnh ngoài danh sách hoặc lệnh ghi (`add`, `set`, `remove`, `/system/reboot`…) bị từ chối **trước khi gửi** (không byte nào tới router).
- Proplist chỉ nhận các trường được phép của từng lệnh, chặn mọi trường nhạy cảm (`private-key`, `preshared-key`, `password`…).
- `thuc_hien` của module luôn từ chối — module không có hành động nào.
- **Khuyến nghị:** tạo tài khoản RouterOS chỉ có quyền đọc (nhóm `read`) trong `/user`.

## Tính tốc độ từ bộ đếm

- Hàm tính tốc độ thuần `tinh_toc_do_mbit`: tính từ hiệu bộ đếm byte và thời gian giữa hai lần đo (`delta * 8 / dt / 1e6`).
- Xử lý tràn bộ đếm 64-bit (hoặc 32-bit): `delta = (2**bit - truoc) + nay`. Giới hạn trần vật lý `TRAN_VAT_LY = 400_000 Mbit/s` (400 Gbit/s); vượt trần coi là đặt lại bộ đếm và trả `None`.
- Nhận biết router khởi động lại khi uptime giảm so với mẫu trước -> trả `None` để đặt lại mẫu gốc, không trả số âm hay số khổng lồ.

## Dọn máy đã mất

- Máy đang thấy: có mặt trong bảng host hiện tại hoặc lease có `status="bound"`.
- Máy không đang thấy: dùng `last-seen` từ lease nếu có, hoặc ghi mốc lần đầu quan sát thấy vắng mặt vào sổ bền `du_lieu/mikrotik-routeros/thay_lan_cuoi.json` (quyền 0600, thư mục 0700).
- Máy vắng mặt liên tục quá `ngay_giu_may_vang` (mặc định 14 ngày, kẹp 1–365) sẽ ngừng báo trong sổ thiết bị. Máy quay lại mạng sẽ được báo lại và xoá mốc khỏi sổ.

## Nhịp đọc và cô lập lỗi

- Nhóm nhanh (`chu_ky_nhanh_giay`, mặc định 15s, kẹp 10–3600): cổng mạng và Kid Control.
- Nhóm chậm (`chu_ky_cham_giay`, mặc định 120s, kẹp 30–3600): tài nguyên router, WireGuard và hai bảng máy.
- Lỗi cô lập từng nguồn: lỗi một lệnh (`LoiRouterTuChoi`) không làm hỏng nguồn khác trong cùng lượt. Router không có WireGuard hoặc Kid Control được đánh dấu `khong_co` (không lỗi, không sinh thực thể).
- `thuc_the` không chặn quá ~2.5s: tác vụ làm mới chạy nền và trả dữ liệu đệm nếu hết giờ.
- `suc_khoe()` trả thêm chi tiết từng nguồn: `nguon: {ten: {trang_thai, loi, luc}}`.

## Cấu hình

| Trường | Bắt buộc | Mặc định | Ý nghĩa |
|---|---|---|---|
| `dia_chi` | Có | — | Địa chỉ LAN của router, dạng `host[:cổng]` (cổng mặc định 8729 TLS). |
| `tai_khoan` | Có | — | Tài khoản RouterOS (nhóm `read`). |
| `mat_khau` | Có | — | Mật khẩu tài khoản (kiểu `bi_mat`, lưu riêng 0600). |
| `kiem_chung_chi_tls` | Không | `"true"` | Kiểm tra chứng chỉ TLS theo hệ thống khi không ghim vân tay. |
| `van_tay_tls` | Không | `""` | Vân tay SHA-256 của chứng chỉ router (ghim chứng chỉ tự ký). |
| `cho_phep_khong_ma_hoa` | Không | `"false"` | Cho phép nối trần cổng 8728 (không khuyến nghị). |
| `chu_ky_nhanh_giay` | Không | `"15"` | Chu kỳ đọc nhanh (giây, kẹp 10–3600). |
| `chu_ky_cham_giay` | Không | `"120"` | Chu kỳ đọc chậm (giây, kẹp 30–3600, >= chu kỳ nhanh). |
| `ngay_giu_may_vang` | Không | `"14"` | Số ngày giữ máy vắng mặt khỏi mạng (kẹp 1–365). |
| `bat_kid_control` | Không | `"true"` | Bật đọc Kid Control nếu router có tính năng. |

## Giới hạn

- CHỈ ĐỌC: `thuc_hien` luôn từ chối; muốn điều khiển router thì dùng module khác.
- Chưa kiểm với router MikroTik thật (chỉ kiểm với máy chủ RouterOS giả dựng bằng thư viện chuẩn trong test).
