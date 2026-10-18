# Chuẩn hoá chữ để đọc tiếng Việt (TTS)

Module này cung cấp công cụ chuẩn hoá văn bản tiếng Việt sang dạng chữ phát âm (chuẩn C2 `thiet_bi`) dành cho các hệ thống tổng hợp giọng nói (TTS) hoặc các trợ lý AI. Module giải quyết triệt để 8 lớp lỗi đọc tiếng Việt phổ biến bằng các quy tắc cấu trúc ngôn ngữ và dữ liệu tập đóng, hoàn toàn thuần Python và không gọi mạng ra ngoài.

## Các lớp lỗi được xử lý

1. **Gạch đầu dòng & số âm**: Phân biệt dấu gạch đầu dòng danh sách `- 60%` (không đọc là âm) với số âm thực tế (`-5°C` -> "âm 5 độ C").
2. **Số thập phân & phân tách hàng nghìn**: Chuyển đổi số thập phân dấu phẩy hoặc chấm (`7,3` hoặc `7.3 km/h` -> "bảy phẩy ba...") và mở rộng số phân tách hàng nghìn (`1.500.000 đ` -> "một triệu năm trăm nghìn đồng").
3. **Đơn vị đo tập đóng**: Tập đóng các đơn vị SI và thông dụng (km/h, kg, mg, ml, kWh, kcal, Ah, V, W, Hz, °C, %, Nm, TB, GB, MB, KB, ha...). Phân biệt chữ hoa/thường theo ngữ cảnh (`Mg` trong hoá học vs `mg` miligam).
4. **Số La Mã, ngày tháng, khoảng số, tỉ số, số điện thoại, tiền tệ**:
   - Số La Mã (quý IV -> quý bốn, thế kỷ XXI -> thế kỷ hai mươi mốt, II/III -> hai phần ba).
   - Khoảng số ("2-3 ngày" -> "2 đến 3 ngày").
   - Tỉ số trận đấu ("kết quả trận đấu 3-1" -> "kết quả trận đấu 3 1").
   - Số điện thoại (đọc từng chữ số).
   - Tiền tệ (50 USD -> 50 đô la Mỹ, 10 BTC -> 10 bít côi).
5. **Từ viết tắt**:
   - Bảng viết tắt một nghĩa tập đóng (≥160 từ hành chính, giáo dục, đời sống nguồn CC0).
   - Viết tắt nhiều nghĩa: thu thập danh sách mơ hồ `mo_ho` (vị trí `vi_tri` tính theo chuỗi gốc của bước chuẩn hoá viết tắt, dùng thuật toán thay thế một lượt để đảm bảo không lệch chỉ số khi từ viết tắt lặp lại nhiều lần) và mặc định đánh vần theo chữ cái tiếng Việt.
   - Viết tắt lạ: tự động đánh vần từng chữ cái (API, SDK, CPU...).
6. **Công thức hoá học & phản ứng (nguyên tắc cấu trúc)**:
   - Đọc 118 nguyên tố hoá học, chỉ số, ngoặc (`Al(OH)3`), điện tích (`Fe3+`, `SO4^2-`), hợp chất IUPAC (`NaCl`, `NaOH`, `HCl`, `BaO`), phương trình phản ứng.
   - «Ngữ cảnh hoá học» được xác định thuần cấu trúc: trong cùng câu có công thức cấu trúc (chỉ số, điện tích, ngoặc hoặc hợp chất IUPAC) hoặc ký hiệu phản ứng. Ký hiệu nguyên tố đơn (như `Ba`) chỉ đọc theo hoá học khi trong cùng câu có công thức cấu trúc và không đứng cạnh đại từ xưng hô tập đóng `XUNG_HO` (đối chứng tên người "cô Na", "anh Ba", "Ba mẹ đi làm" giữ nguyên).
   - Ký hiệu nguyên tố đứng đơn lẻ trong câu không có công thức cấu trúc (như `Cu`, `Fe`, `Mg`, `K`) sẽ không kích hoạt đọc hoá học nhằm tuân thủ luật cứng không dùng danh sách từ khoá suy đoán ngữ cảnh.
7. **Tiêu đề IN HOA, tên viết tắt có chấm, URL, Email**:
   - Hạ chữ thường các tiêu đề IN HOA có dấu tiếng Việt, giữ nguyên từ viết tắt đã biết.
   - Đánh vần tên viết tắt có chấm (`L.T.M.P`).
   - Đọc URL và email sang tiếng Việt chuẩn ("a còng", "chấm", "gạch chéo").
8. **Hậu xử lý TTS**: Lọc bỏ emoji, Variation Selectors, cú pháp Markdown (bảng biểu, in đậm, in nghiêng, trích dẫn, gạch ngang) và định dạng LaTeX (giữ nguyên biểu thức).

## Cần chuẩn bị gì

- Không cần cấu hình tài khoản hay khoá API.
- Thuần Python, không phụ thuộc thư viện bên ngoài, không cần kết nối mạng.
- Bộ nhớ tiêu thụ thấp, thời gian xử lý nhanh (chống ReDoS, trần 20.000 ký tự).

## Cách cài đặt

1. Mở giao diện quản trị web2api, vào tab **Hệ thống → Module**.
2. Tìm module **«Chuẩn hoá chữ để đọc tiếng Việt (TTS)»** (`doc-viettat`).
3. Bấm **Cài đặt**. Module sẽ tự động nạp và đăng ký công cụ `doc_viettat.chuan_hoa` vào danh mục thiết bị của web2api.

## Cách sử dụng

### 1. Gọi trực tiếp qua API thiết bị (REST API)

Yêu cầu xác nhận thực thể theo cơ chế an toàn mặc định (SEC-004f), sau đó gửi yêu cầu chuẩn hoá:

```bash
# Xác nhận thực thể công cụ (chỉ cần làm một lần với quyền quản trị)
curl -s -X POST -H "Authorization: Bearer $WEB2API_ADMIN_KEY" \
  http://192.0.2.1:8000/api/thiet-bi/doc_viettat.chuan_hoa/xac-nhan

# Thực hiện chuẩn hoá văn bản
curl -s -X POST -H "Authorization: Bearer $WEB2API_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "hanh_dong": "chuan_hoa_doc",
    "tham_so": {
      "van_ban": "UBND tỉnh họp lúc 14h30 về việc nâng cấp 15 km đường quốc lộ."
    }
  }' \
  http://192.0.2.1:8000/api/thiet-bi/doc_viettat.chuan_hoa/hanh-dong
```

Kết quả trả về:

```json
{
  "ok": true,
  "ket_qua": {
    "van_ban_doc": "Ủy ban nhân dân tỉnh họp lúc 14 giờ 30 về việc nâng cấp 15 ki lô mét đường quốc lộ.",
    "mo_ho": [],
    "da_doi": 3
  }
}
```

### 2. Dùng qua Function Calling (OpenAI Tools)

Khi AI gọi công cụ `chuan_hoa_doc`:

```json
{
  "name": "chuan_hoa_doc",
  "arguments": {
    "van_ban": "Chi phí xét nghiệm là 1.500.000 đ tại BV đa khoa."
  }
}
```

## Giới hạn và lưu ý

- **Trần ký tự**: Giới hạn tối đa 20.000 ký tự mỗi lượt gọi để bảo đảm tài nguyên hệ thống và ngăn ngừa tấn công làm nghẽn dịch vụ. Chuỗi dài hơn sẽ trả về lỗi `ValueError`.
- **Tính xác định**: Cùng một chuỗi đầu vào luôn cho ra cùng một kết quả chuẩn hoá duy nhất.
- **Không suy đoán chủ quan**: Những từ viết tắt nhiều nghĩa mà không có đủ ngữ cảnh sẽ được đánh vần theo chữ cái tiếng Việt và ghi nhận vào danh sách `mo_ho` để ứng dụng phía trên tuỳ nghi xử lý.
