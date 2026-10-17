---
name: api-design
description: Thiết kế đường API cho web2api — hợp đồng chuẩn OpenAI ở /v1, mã trạng thái ngữ nghĩa, lỗi kiểu OpenAI, phân trang con trỏ. Dùng khi thiết kế đường API mới hoặc soát lại hợp đồng đường cũ.
---

# Thiết kế đường API cho web2api

Kho có hai họ đường: `/v1/*` (chuẩn OpenAI, nghiêm ngặt) và `/api/*` (quản trị
của cổng). Thiết kế đường nào thì theo họ đó.

## /v1/* — nói y hệt OpenAI, không phát minh

- Đã có: `GET /v1/models`, `GET /v1/models/{model}`, `POST /v1/chat/completions`
  (`stream: true` → SSE), `/v1/images/*`, `/v1/audio/*`, `/v1/videos/*`,
  `/v1/embeddings`, `/v1/responses` (router ở `app/tuyen.py`, đều qua `V1_AUTH`).
- Thêm đường `/v1` mới = khớp y hệt ngữ nghĩa OpenAI (tên tham số, dạng trả về).
  Riêng của một nhà cung cấp thì nằm ở **module**, không ở `/v1` (lõi là phiên
  dịch viên trung lập — CLAUDE.md).
- Sai lệch ở `/v1` làm mọi client chuẩn OpenAI hỏng — kiểm bằng `curl` thử thật
  sau khi thêm.

## Đặt tên và phương thức

- Tài nguyên là danh từ, số nhiều, viết thường: `/api/modules`, `/api/nguoi-dung`.
- `GET` đọc; `POST` tạo/kích hoạt; `PUT` thay thế toàn bộ; `PATCH` sửa từng phần;
  `DELETE` xoá. Không có động từ trong URL.
- Bộ sưu tập con cho quan hệ sở hữu: `/api/modules/{id_}/suc-khoe`.
- Đường `/api/*` mới theo quy ước tiếng Việt của kho (xem các route có sẵn ở
  `app/tuyen.py`), không trộn Anh–Việt trong một họ đường.

## Mã trạng thái ngữ nghĩa

| Mã | Dùng cho |
|---|---|
| 200 | GET/PUT/PATCH thành công |
| 201 | POST tạo tài nguyên, kèm `Location` |
| 204 | DELETE thành công, không thân trả về |
| 400 | tham số sai định dạng |
| 401 | thiếu/sai khoá xác thực |
| 403 | đã xác thực nhưng không đủ quyền |
| 404 | tài nguyên không có |
| 409 | trùng hoặc cấn trạng thái |
| 422 | JSON đúng nhưng dữ liệu vô nghĩa |
| 429 | vượt giới hạn gọi (kho có `app/gioi_han.py` giới hạn sai khoá) |
| 500 | lỗi nội bộ — không bao giờ lộ chi tiết cho khách |

- Không trả `200` kèm thân báo lỗi cho lỗi. `401`/`403`/`404`/`403` tách bạch theo
  đúng luật leo thang quyền ở CLAUDE.md (không khoá → 401; vai thấp gọi đường
  quản trị → 403; IDOR → 403/404).

## Định dạng lỗi — một mẫu duy nhất kiểu OpenAI

```json
{ "error": { "message": "Model ... không có (xem GET /v1/models).",
             "type": "invalid_request_error", "code": "model_not_found" } }
```

- Mọi lỗi (cả `/api/*`) dùng `app/loi_openai.py`: `LoiOpenAI(ma, thong_diep, code=...)`.
- Thông báo đi qua `lam_sach_thong_diep`: cắt token liền mạch dài (khỏi lộ khoá),
  bỏ ký tự điều khiển, giới hạn 400 ký tự.
- 500 luôn trả thông điệp chung; chi tiết chỉ ở log máy chủ.

## Phân trang con trỏ

- Danh sách dài dùng **con trỏ, không số trang**: `GET /api/nhat-ky` dùng tham
  số `truoc` = thời điểm ISO 8601 lấy từ trường `luc` của dòng cuối trang vừa đọc.
- Sắp xếp phải ổn định theo cùng một khoá mỗi lần để con trỏ không bỏ sót bản ghi.
- Số trang (`page`) chỉ dùng khi dữ liệu nhỏ, cố định và người dùng thật sự cần
  nhảy trang — không phải mặc định.

## Xác thực

- Bearer/khoá **luôn bắt buộc** cho mọi đường mới, dù chỉ một người dùng.
- Đường mở không xác thực là ngoại lệ hiếm (hiện chỉ sức khoẻ và đổi vé): ghi lý
  do ở đầu mã + test giới hạn dùng một lần/hết hạn.

## Checklist trước khi ship đường mới

- [ ] Thuộc đúng họ (`/v1` chuẩn OpenAI hay `/api` tiếng Việt của kho)
- [ ] Đúng phương thức và mã trạng thái ngữ nghĩa (không 200 cho lỗi)
- [ ] Lỗi qua `LoiOpenAI`, không lộ chi tiết nội bộ hay bí mật
- [ ] Xác thực bắt buộc (hoặc ghi lý do + test giới hạn nếu mở)
- [ ] Danh sách dài có phân trang con trỏ, sắp xếp ổn định
- [ ] Đã ghi vào `docs/THAM-CHIEU.md` (kẻo `test_web2api_readme.py` báo đỏ)
- [ ] Phản hồi không lộ khoá, stack trace hay lỗi SQL nội bộ
