# Module `hoa-don` — Đối chiếu hóa đơn điện tử với bảng danh mục (NV162)

Đọc hoá đơn điện tử XML (bản gốc pháp lý, Nghị định 123/2020 + Thông tư 78/2021)
và đối chiếu với bảng danh mục Excel: báo dòng khớp/lệch (số lượng, đơn giá, thành
tiền), dòng chỉ có một bên, và tự kiểm phép tính trên hoá đơn (số lượng × đơn giá −
chiết khấu = thành tiền; tổng dòng = tổng chưa thuế; chưa thuế + thuế = thanh toán).

Port từ `services/hoa_don.py` của c2a (commit `d879b03`) theo LUẬT PORT; nguồn gốc
và các khác biệt có chủ đích ghi ở đầu `module.py`. Khác với c2a: thuần tính toán
trong lõi (không pandas, không đọc tệp theo đường dẫn, không lưu hồ sơ chờ tệp),
số tiền xử lý bằng **Decimal** (không float).

Mã nguồn module: `modules/hoa-don/`. Tiền tố: `hoa_don`.

## Cần gì

- **Không khoá, không tài khoản, không dịch vụ phụ, không mạng ra ngoài.** Cài là
  dùng. Trường cấu hình: không có.
- Dữ liệu đầu vào người dùng/trợ lý cung cấp lúc gọi công cụ:
  - Hoá đơn: nội dung XML gốc (xin từ nhà phát hành hoá đơn điện tử — file `.xml`
    đi kèm hoá đơn, chắc hơn bản in PDF), hoặc văn bản Markdown đã trích từ PDF.
  - Danh mục: mảng các dòng đã đọc (dòng đầu là tiêu đề: tên hàng, số lượng, đơn
    giá, thành tiền — mỗi ô dạng chuỗi hoặc số), hoặc tệp `.xlsx` dạng base64.
- Tài nguyên thêm: chưa đo (xem `tai_nguyen` trong manifest).

## Hai công cụ (rủi ro «doc», qua thực thể `hoa_don.cong_cu`)

| Công cụ | Tham số | Trả về |
|---|---|---|
| `doc_hoa_don` | `xml` hoặc `van_ban_pdf`, `ten_hd` (tuỳ chọn) | `du_lieu`: mẫu/số/ngày, người bán/mua (MST dạng chuỗi), từng dòng hàng, tổng tiền — số tiền dạng chuỗi Decimal |
| `doi_chieu_hoa_don` | hoá đơn (như trên) + `danh_muc` hoặc `xlsx_base64`, `ten_dm` | `bao_cao`: báo cáo Markdown đối chiếu |

Kết quả mang `nguon_ngoai: true` vì đầu vào là dữ liệu không tin.

## An toàn đầu vào

- XML chống nổ thực thể: từ chối mọi tài liệu có `<!DOCTYPE`/khai báo ENTITY;
  trần kích thước 2.000.000 byte, độ sâu 64 tầng, 200.000 phần tử. Mẫu hoá đơn lạ
  (không có `DSHHDVu/HHDVu`) → báo «không đọc được», không đoán.
- `.xlsx` đọc bằng zipfile với trần: nén 10 MB, tổng giải nén 20 MB, một tệp con
  5 MB, tỉ lệ nén 100× (chống zip bomb), 2.000 mục, 5.000 dòng, 64 cột. Chỉ đọc
  sheet đầu tiên (`xl/worksheets/sheet1.xml` hoặc sheet đầu theo thứ tự tên).
  Công thức không bao giờ thực thi: chỉ dùng giá trị đã lưu; ô công thức không
  có giá trị lưu thì bỏ qua.
- Tiền/số lượng phân tích theo ba khuôn số Việt/Anh (nhóm nghìn/thập phân) của
  c2a và giữ bằng Decimal; MST giữ nguyên chuỗi (không mất số 0 đầu).
- Báo cáo bị cắt ở 80.000 ký tự; mỗi nhóm «chỉ một bên» liệt kê tối đa 200 tên.

## Điểm nối (không phụ thuộc — module khác chưa gộp vẫn dùng được)

- **PDF**: trích xuất văn bản từ PDF thuộc module `van-phong` (NV156, chưa gộp).
  Module này chỉ nhận Markdown đã trích qua `van_ban_pdf`; báo cáo nói rõ nguồn
  là PDF (bản in), kém chắc hơn XML. Khi bảng trích lệch cột, module tự neo theo
  tên hàng của danh mục (hàm của c2a).
- **Danh mục đã đọc**: nếu `van-phong` sau này cung cấp phần đọc bảng, nó đưa mảng
  dòng qua tham số `danh_muc` — hợp đồng không đổi.

## Giới hạn

- Chỉ đọc sheet đầu tiên của .xlsx; bảng nhiều sheet thì đưa mảng dòng đã đọc.
- Ghép gần đúng tên hàng theo ngưỡng tương tự 0,85 (của c2a) — tên quá khác nhau
  sẽ rơi vào nhóm «chỉ có một bên», không ghép bừa.
- Chưa kiểm với hoá đơn XML thật của nhà phát hành (Viettel/VNPT/MISA…) — test
  dùng hoá đơn giả đúng khuôn TT78.
