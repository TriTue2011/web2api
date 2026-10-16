<!--
Mẫu tiểu mục cho MỘT module trong docs/THAM-CHIEU.md, mục «4. Module có sẵn»
(và thêm một dòng vào bảng «Module có sẵn» của README.md).
Cách dùng: chép phần dưới đường kẻ vào docs/THAM-CHIEU.md (sau tiểu mục module cuối cùng), thay mọi <…>,
xoá dòng nào không áp dụng. Viết câu hoàn chỉnh, không giả định người đọc biết dự án.
Xong thì làm đủ các bước ở docs/THAM-CHIEU.md, mục 9 «Cách bổ sung tài liệu khi thêm module».
-->

---

### Module <id> — <Tên hiển thị>

**Mô tả.** <Module làm gì, gọi tới đâu, khác gì so với gọi thẳng dịch vụ gốc.> Mã nguồn:
`modules/<id>/`. Tiền tố model: `<tien_to>`. Hạng: `<"A" hoặc "B">` (trường `hang` là bắt buộc trong `manifest.json`: "A" = chủ máy đã đọc hết mã + ghim SHA; "B" = mã của người khác, chạy ngoài lõi; hang là lời khai của tác giả; máy chủ có quyền coi module bên thứ ba là B bất kể lời khai).

**Cần gì.**
- <Khoá/tài khoản cần có và lấy ở đâu (đường dẫn trang lấy khoá).>
- <Mạng ra tới máy nào (khớp `tai_nguyen.mang_ra` trong manifest).>
- <RAM/đĩa/GPU thêm — số ĐO THẬT; chưa đo thì ghi «chưa đo».>

**Cách cài.**

1. Xem thông báo: `curl -s -H "Authorization: Bearer $K" $W/api/modules/thong-bao` — có `"id": "<id>"`.
2. Tải về: `curl -s -X POST -H "Authorization: Bearer $K" $W/api/nguon-git/cai/<id>`
3. <Nếu có trường bắt buộc: lệnh `POST /api/modules/<id>/cai` với thân `{"cau_hinh": {...}}` mẫu.>

**Cấu hình.**

| Trường | Bắt buộc | Ý nghĩa |
|---|---|---|
| `<khoa>` | <Có/Không> | <Ý nghĩa; trường bí mật thì nói rõ được lưu riêng, không trả lại.> |

**Ví dụ dùng.**

```bash
curl -s -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"model": "<tien_to>/<tên model>", "messages": [{"role": "user", "content": "Xin chào"}]}' \
  $W/v1/chat/completions
```

**Sự cố thường gặp.**
- <Mã lỗi hoặc thông báo> — <nguyên nhân> — <cách xử lý>.

**Giới hạn.**
- <Cái module CHƯA làm được; cái chưa kiểm với dịch vụ thật.>

<!-- Có ảnh giao diện riêng (tab_web) thì thêm một bước chụp vào scripts/chup-man-hinh.sh,
chạy script, rồi nhúng ảnh: ![<mô tả>](anh/<trang>-may-tinh.png) kèm một dòng chú thích nghiêng
nói ảnh thể hiện bước nào. -->
