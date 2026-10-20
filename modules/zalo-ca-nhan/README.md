# Module `zalo-ca-nhan` — kênh Zalo cá nhân qua dịch vụ phụ Node

Module gửi/nhận tin Zalo bằng **tài khoản cá nhân** thông qua dịch vụ phụ
`dich-vu/zalo-ca-nhan` (Node + zca-js, chạy trong container riêng, giao tiếp
HTTP loopback `127.0.0.1:8703` với Bearer xác thực). Không phải module
`zalo-bot` (bot chính thức qua webhook) — đây là luồng tài khoản cá nhân.

> ⚠️ **CẢNH BÁO RỦI RO**: API **không chính thức** của Zalo. Tài khoản có thể
> bị khoá bất cứ lúc nào, không báo trước; chủ máy tự chịu. Chỉ dùng tài
> khoản phụ, không dùng tài khoản chính/có tiền/banking. Dịch vụ mặc định
> không tự làm gì: phải đặt `ZALO_KHOA`, dựng container, bật `bat=1`, quét QR.

## Hoạt động

- **Gửi ra** (chuẩn `kenh_ra`): `kenh_ra()` liệt kê hội thoại (bạn bè + nhóm)
  từ **bộ đệm** của module — hàm này đồng bộ theo hợp đồng lõi nên không
  chạm mạng; bộ đệm được làm mới ở các đường async (`cai`, `hoi_vong`,
  `gui_tin`, TTL 30 s). `gui_tin(dich, noi_dung)` gửi văn bản (`van_ban`)
  hoặc kèm tệp (`_tep`: `[{ten, kieu, du_lieu (base64)}]`). Gửi tin là
  **GHI ra ngoài**: lõi chỉ cho khoá khách có phạm vi `gui:zalo-ca-nhan`
  gọi đường gửi (kèm hạn tần suất của lõi) — **không có bước quản trị xác
  nhận từng tin**; ai giữ khoá có phạm vi đó gửi được tới mọi đích của tài
  khoản, nên chỉ cấp phạm vi này cho ứng dụng thật sự cần gửi.
- **Nhận vào** (chuẩn `hoi_vong`, chu kỳ 5 s): module hỏi dịch vụ
  `/tin-moi?tu=<seq>`, lọc theo `danh_sach_cho_phep` → tin chuẩn cho lõi
  (`id_tin = "<thread_id>:<nhãn phiên>:<seq>"`, `noi_gui = thread_id`,
  `la_nhom` khi nhóm). Dịch vụ khởi động lại (seq về 0) thì module tự đặt
  lại con trỏ theo nhãn phiên dịch vụ trả kèm — không mất tin lặng lẽ.
  Chỉ nhận người trong danh sách cho phép (**mặc định rỗng = không nhận ai**);
  tin của người lạ bị bỏ lặng lẽ (đếm `bo_qua_nguoi_la` trong `suc_khoe`).

## Cấu hình

| khoá | mặc định | nghĩa |
|---|---|---|
| `bat` | `0` | `1` = bật; tắt thì không gọi mạng, `gui_tin` báo `chua_bat` |
| `dich_vu_url` | `http://127.0.0.1:8703` | Địa chỉ dịch vụ; **chỉ loopback** (chống SSRF) |
| `khoa_dich_vu` (`bi_mat`) | — | Khóa Bearer của dịch vụ (`ZALO_KHOA` lúc chạy container). **Tự đặt**: `openssl rand -hex 32`, ≥24 ký tự; bắt buộc khi `bat=1`. Không vào log (bộ lọc log), không có trong `suc_khoe` |
| `danh_sach_cho_phep` | `[]` | Chuỗi JSON mảng id người gửi được nhận tin. Lấy id từ trang hợp đồng/QR của dịch vụ. Tin ngoài danh sách bị bỏ |

## Đăng nhập Zalo (ở dịch vụ, không ở module)

```bash
curl -X POST -H "Authorization: Bearer $ZALO_KHOA" http://127.0.0.1:8703/tai-khoan/dang-nhap-qr
# → {"url_qr": "/qr/<token>"} — mở MỘT lần bằng trình duyệt nội bộ, quét bằng
# app Zalo trong 120 s (trang QR có CSP, không script, token chỉ xem được 1 lần)
```

Chứng chỉ phiên (`cred_<ownId>.json`) do **dịch vụ** lưu, quyền 0600 —
module không bao giờ thấy cookie/imei/userAgent, không log.

## Thiết kế an toàn (tóm tắt)

- Mặc định TẮT; khoá Bearer ≥24 ký tự, dịch vụ từ chối khởi động nếu thiếu/yếu.
- Tin nhận là DỮ LIỆU KHÔNG TIN: kiểm khuôn (`MAU_NGUOI_GUI`), cắt ở
  `TRAN_VAN_BAN` (4000), bỏ ký tự điều khiển, không đưa nội dung vào log.
- Dịch vụ hạn tốc độ gửi (≥2 s/lần, ≤20/phút) chống tràn tin/bị khoá —
  giữ chỗ nguyên tử ở phía dịch vụ nên gửi đồng thời hay gửi lỗi đều
  không qua được hạn.
- Hạn dòng + chống trùng của lõi vẫn áp dụng cho tin nhận.
- **Chưa kiểm với Zalo thật** trong môi trường nộp bài; xem báo cáo NV172
  («Phần KHÔNG port và vì sao»).
