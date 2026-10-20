# Tiện ích đời sống — bản tin, chỉ đường, vùng radar, dấu câu

Module chuẩn C2 (`thiet_bi`), port từ c2a `d879b03` (NV165). Bốn nhóm công cụ
OpenAI (tiền tố `tienich`), cho trợ lý gọi qua hội thoại:

- **tin_tuc**: xếp hàng mục tin theo nguồn (chống trùng theo mã mục), bản tổng hợp
  theo mốc giờ (mỗi mốc bắn 1 lần/ngày), tóm tắt cắt gọn xác định.
- **chi_duong**: tra địa điểm, chỉ đường có bước rẽ tiếng Việt, giải link ghim
  Google Maps — qua Nominatim/OpenStreetMap + OSRM (miễn phí, không khoá).
- **vung**: vùng khoảng cách tự học ngưỡng «trong/ngoài» từ dữ liệu lịch sử của
  radar đo khoảng cách (do người gọi thu thập); chưa học đủ → luôn báo có mặt.
- **dau_cau**: kiểm tra văn bản thiếu dấu câu + chia lô cho phục hồi dấu câu.

## Quyền riêng tư

Địa chỉ/vị trí là dữ liệu NHẠY CẢM: không ghi vào log; chỉ gửi đúng chuỗi truy
vấn cho Nominatim/OSRM khi dùng công cụ chỉ đường. Cấu hình `bat_tim_duong=0`
tắt toàn bộ gọi mạng ngoài. Trạng thái lưu tại
`WEB2API_DATA/du_lieu/tien-ich-doi-song/` (thư mục 0700, tệp 0600).

## Không port (ghi rõ)

- Lớp GỬI bản tin tới kênh Zalo/Telegram của c2a `digest.py` — công cụ trả văn
  bản, tác nhân/tro-ly tự gửi.
- Đường cào endpoint Google nội bộ của c2a `chi_duong.py` (không chính thức) —
  ghi «cần chủ máy quyết», không bật.
- `cap_radar()`/`hoc()` tự đọc Home Assistant của c2a `vung_khoang_cach.py` —
  thay bằng điểm nối `hoc_tu_du_lieu`.
- `trong_xe.py` của c2a (nhắc giữ xe/ghi xe trong bãi) — phụ thuộc camera/
  nhin-nha, thuộc danh sách chờ chủ máy trong quy ước chung.

Chi tiết: `docs/THAM-CHIEU.md`, tiểu mục «Module tien-ich-doi-song».
