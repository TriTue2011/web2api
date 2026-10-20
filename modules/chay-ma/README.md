# Module chay-ma — chạy mã Python trong container cách ly (NV171)

Module lõi hạng B cầu nối tới **dịch vụ phụ** `dich-vu/chay-ma`
(cổng 8703): mỗi lượt chạy mã là một container docker dùng-một-lần
cách ly, **không bao giờ chạy trong tiến trình lõi**.

## Hành động

| Thực thể | Hành động | Rủi ro | Mô tả |
|---|---|---|---|
| `chay_ma.kiem` | `kiem_tinh` | `doc` | Soi tĩnh không chạy: lỗi cú pháp, tên chưa định nghĩa, gợi ý sớm về mã đáng chạy thử (KHÔNG phải bằng chứng an toàn) |
| `chay_ma.chay` | `chay_python` | `nguy_hiem` | Chạy đoạn mã Python trong container cách ly, trả stdout/stderr/tệp ra — lõi bắt phê duyệt (SEC-004f) |

## Cách bật

1. Chạy dịch vụ `dich-vu/chay-ma` với `CHAYMA_KHOA` sinh bằng
   `openssl rand -hex 32`.
2. Trong Cài đặt module, bật `chay_bat`, đặt `dich_vu_url`
   (mặc định `http://127.0.0.1:8703`) và `khoa_dich_vu` = đúng
   `CHAYMA_KHOA` đó.

## An toàn

- Tắt mặc định; chưa bật hoặc không nối được dịch vụ → báo «chưa bật»,
  không gọi mạng, không lộ chi tiết.
- Địa chỉ dịch vụ qua ĐÚNG bộ kiểm của lõi (`app/tu_xa.py`): phân giải
  host rồi kiểm TỪNG IP sau phân giải — host dạng số nguyên/hex/bát phân
  trỏ vào `169.254.169.254` bị chặn như IP chữ; lúc gọi ghim IP bằng
  `TransportGhimIp` chống DNS rebinding. Ngoại lệ có chủ đích duy nhất:
  loopback được phép vì dịch vụ là tiến trình cục bộ do quản trị cấu
  hình; ngoài loopback chỉ nhận mạng riêng RFC1918/ULA. Kiểm ở cả
  `cai()` lẫn `nap()` (NV171-SUA C1).
- Lược đồ `chay_python` khớp trần của lõi: tổng tham số một lần gọi
  ≤ 8 KB (NV171-SUA C8) — mã dài thì chia nhỏ hoặc gọi thẳng dịch vụ.
- Dịch vụ cách ly: `--network none`, rootfs chỉ đọc, user không-root
  `65534:65534`, `--cap-drop ALL`, `no-new-privileges`, trần
  CPU/RAM (512MB)/tiến trình (64)/thời gian (60s)/đầu ra; bind-mount duy
  nhất là thư mục tạm của lần chạy; không mount dữ liệu lõi, không
  docker.sock. Chi tiết: `dich-vu/chay-ma/README.md`.
- Mã và đầu ra là dữ liệu KHÔNG TIN: module cắt ở trần 20.000 ký tự và
  gắn `nguon_ngoai=True`.
- Khoá dịch vụ không vào log (logging.Filter theo khuôn van-phong).

## Mã nguồn

Soi tĩnh và chẩn đoán của dịch vụ chép từ c2a **CỦA CHÍNH CHỦ MÁY**
(`services/code_runner.py`: `boc_code_python`, `co_the_chay` — đã đổi
tên `goi_y_som` và hạ xuống vai trò GỢI Ý SỚM ở NV171-SUA C3,
`kiem_tinh`, `chan_doan_loi`, bootstrap rlimit). Phần thực thi trực
tiếp của c2a KHÔNG được port — thay bằng container cách ly.
