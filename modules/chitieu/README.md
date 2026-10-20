# Sổ chi tiêu cá nhân (JARS)

Module `chitieu` cung cấp sổ chi tiêu theo hũ (JARS) cho trợ lý qua công cụ chuẩn `thiet_bi`
(C2): ghi khoản chi, xem ngân sách theo kỳ lương, tự bù khi một hũ vượt hạn mức, cảnh báo
khi chạm ngưỡng, ghi thu nhập thêm / chi phí đặc biệt, và sổ tạm ứng công ty. Mã nguồn port
từ `services/chi_tieu/` + `api/chi_tieu.py` của c2a (@ d879b03, bản gốc ở
`~/workspace/c2a-nv146` — kho c2a là của chính chủ máy, chép theo luật port) và
`chi-tieu-mcp` (Quiz99, MIT) ở các lớp kho/ngân sách.

> Tên module: spec gọi là `chi-tieu`, nhưng id đăng ký là `chitieu` (không gạch) vì mảnh
> «tieu» của id gạch làm test độc lập của lõi (`test_web2api_doc_lap.py`) suy nhầm thành
> tên nhà cung cấp và đỏ 124 dòng lõi (chuỗi `tieu_de`…) — theo tiền lệ NV147
> (`tu-van-hanh` → `tuvanhanh`). Tiền tố công cụ vẫn là `chi_tieu`; dữ liệu ở thư mục
> `du_lieu/chi-tieu/`.

## Cách tổ chức tiền

- **Mỗi danh tính một sổ.** Công cụ không nhận `so_id` từ tham số: lõi xác định người gọi
  (phạm vi `thiet_bi:ghi` tại `POST /api/thiet-bi/{id}/hanh-dong`) và module ràng buộc sổ
  theo danh tính đó. Người này không đọc/sửa/xoá được dữ liệu của người kia — kể cả quản trị
  cũng chỉ chạm sổ của chính mình khi dùng công cụ. Không có ngữ cảnh danh tính (gọi nội bộ)
  thì ghi vào sổ dùng chung của nhà.
- **Hũ là dữ liệu** (không phải 6 mã cố định): tạo sổ có 6 hũ mẫu 55/5/10/10/10/10
  (Thiết Yếu, Gia Đình & Cho Đi, Học Tập, Dự Phòng & Tiết Kiệm, Hưởng Thụ, Tự Do Tài Chính);
  thêm / sửa / xoá tự do. Hũ có hai thuộc tính thứ tự: `thu_tu_bu` (hũ nào bị rút bù trước
  khi hũ khác vượt) và `thu_tu_dac_biet` (>0 = hứng chi phí đặc biệt, nhỏ hơn hứng trước).
- **Kỳ** đóng băng theo ngày bắt đầu kỳ (mặc định 1, đặt 1–28) và múi giờ nhà
  `Asia/Ho_Chi_Minh`. Số tiền là **số nguyên VNĐ** — không bao giờ dùng float cho tiền.
- **Cổng vượt tổng**: khoản ghi mới (hoặc sửa tăng) làm vượt tổng ngân sách kỳ thì công cụ
  trả `can_xac_nhan: true` và CHƯA ghi; chỉ ghi khi gọi lại với `xac_nhan_vuot_tong: true`
  sau khi người dùng đồng ý rõ ràng.

## Dữ liệu

- SQLite ở `<WEB2API_DATA>/du_lieu/chi-tieu/chi_tieu.db` (0600, thư mục 0700), ghi theo
  giao dịch. Tệp hỏng → công cụ trả lỗi `du_lieu_hong`, KHÔNG xoá và KHÔNG ghi đè tệp —
  khôi phục từ sao lưu của chính anh (chưa nằm trong bản sao lưu của lõi: cần nối sao lưu).
- Trần: ghi chú/mô tả ≤ 500 ký tự, tên sổ ≤ 80, tên hũ ≤ 60, số tiền dương ≤ 1.000 tỷ,
  đọc tối đa 10.000 bản ghi một lần.
- Ghi chú/mô tả là DỮ LIỆU người dùng (không tin): module lưu nguyên văn và không thực thi
  gì từ đó; việc hiển thị escape HTML thuộc tầng giao diện.

## Công cụ

Hai thực thể công cụ: `chi_tieu.doc` (rủi ro `doc`) và `chi_tieu.ghi` (rủi ro `ghi`).

- Chỉ đọc: `xem_ngan_sach`, `xem_lich_su`, `thong_ke`, `de_xuat`, `xem_canh_bao`,
  `xem_cong_ty`, `xem_hu`.
- Ghi: `ghi_chi`, `sua_chi`, `xoa_chi`, `tach_chi`, `thu_nhap_them`, `chi_phi_dac_biet`,
  `sua_ky`, `xoa_ky`, `them_hu`, `sua_hu`, `xoa_hu`, `luu_cau_hinh`, `cong_ty_tam_ung`,
  `cong_ty_chi`, `cong_ty_sua`, `cong_ty_xoa`, `giai_chi`.

Ví dụ (qua API thiết bị của lõi, sau khi thực thể đã được xác nhận theo SEC-004f):

```bash
curl -s -X POST -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"hanh_dong": "ghi_chi", "tham_so": {"hu": "Thiết Yếu", "so_tien": 50000, "ghi_chu": "cơm trưa"}}' \
  $W/api/thiet-bi/chi_tieu.ghi/hanh-dong
```

## Cần gì

- Không cần khoá, tài khoản bên ngoài hay mạng. Thuần Python thư viện chuẩn (sqlite3).
- RAM/đĩa: chưa đo (ghi `null` trong manifest theo quy ước — không bịa số).

## Khác biệt với c2a (không port, có lý do)

- **`email_chi.py` — KHÔNG port.** Phần này đọc hộp thư IMAP (mật khẩu ứng dụng mã hoá),
  quét nền 180 giây và dùng model hiểu thư ngân hàng để tự ghi chi: nó kéo theo cả một kênh
  email. Module `email` của web2api (NV125/NV154) chưa gộp vào main — khi module đó có mặt,
  sẽ nối: hộp thư thuộc sổ, chỉ-đọc (EXAMINE/BODY.PEEK), người-gửi-được-đọc theo danh sách,
  một thư ghi một lần. Cho tới lúc đó **chưa gộp** phần đọc thư, không có mã IMAP nào trong
  module này. Đây là điểm nối duy nhất còn thiếu so với c2a.
- **Gửi cảnh báo qua kênh chat** (Zalo/Telegram của c2a) và vòng lặp kiểm 30 phút — chỉ port
  logic tính mốc (mỗi mốc một lần mỗi kỳ); trong web2api xem cảnh báo qua `xem_canh_bao`.
- **Liên kết sổ bằng mã 6 số** cho người chỉ dùng bot — web2api cách ly theo danh tính
  đăng nhập nên không cần; sổ cho người không có tài khoản web chưa hỗ trợ (xem báo cáo).
- **Trang web tab riêng** của c2a (`/chi-tieu`) — chưa làm `tab_web`; thao tác qua công cụ.

## Giới hạn

- Chưa kiểm với dữ liệu thật của nhà (chỉ dữ liệu giả trong test).
- `suc_khoe()` không kèm số tiền — dữ liệu tài chính không đi vào đường sức khoẻ.
