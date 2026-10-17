---
name: fastapi-patterns
description: Các mẫu FastAPI dùng trong web2api — hợp đồng chuẩn OpenAI (/v1/*), lỗi kiểu OpenAI, mã trạng thái, phân trang, route mỏng và xác thực bằng dependency. Dùng khi thêm/sửa đường API trong kho.
---

# Mẫu FastAPI cho web2api

web2api là cổng nói **chuẩn OpenAI**: toàn bộ đường `/v1/*` phải đi theo hợp đồng
OpenAI, không phát sinh hợp đồng riêng cho phần `/v1`.

## Hợp đồng OpenAI ở /v1/*

- Router: `app/tuyen.py` dùng `APIRouter(prefix="/v1", dependencies=V1_AUTH)` — mọi
  đường `/v1/*` bắt buộc qua xác thực khoá.
- Đường chính: `GET /v1/models`, `GET /v1/models/{model}`, `POST /v1/chat/completions`
  (hỗ trợ `stream: true` → SSE `text/event-stream`), thêm `/v1/images/*`, `/v1/audio/*`,
  `/v1/videos/*`, `/v1/embeddings`, `/v1/responses` theo cùng cách.
- Thêm đường `/v1` mới phải khớp y hệt ngữ nghĩa OpenAI (tên tham số, dạng trả về);
  nhà cung cấp đổi API thì sửa **module**, không sửa lõi `/v1` (xem CLAUDE.md).

## Định dạng lỗi — mẫu OpenAI, không phát minh lại

```python
from app.loi_openai import LoiOpenAI

# Trả về: {"error": {"message": "...", "type": "...", "code": ...}}
raise LoiOpenAI(400, "Model ... không có trong danh sách (xem GET /v1/models).",
                code="model_not_found")
```

- `app/loi_openai.py`: `than_loi(ma, thong_diep)` sinh `{"error": {"message", "type", "code"}}`;
  `xu_ly_loi_openai` trả JSON đúng mã trạng thái.
- `lam_sach_thong_diep(...)`: **làm sạch mọi thông báo lỗi** trước khi trả khách
  hoặc ghi log — cắt token liền mạch dài (khỏi lộ khoá), giới hạn 400 ký tự.
- Lỗi hệ thống 500 luôn trả thông điệp chung, không lộ chi tiết nội bộ.

## Mã trạng thái

- `200` GET/PUT/PATCH thành công; `201` POST tạo tài nguyên (kèm `Location`);
  `204` DELETE không thân trả về.
- `400` tham số sai; `401` thiếu/sai khoá; `403` khoá/vai không đủ quyền;
  `404` tài nguyên không có; `409` trùng/cấn; `422` JSON đúng nhưng dữ liệu vô nghĩa;
  `429` vượt giới hạn (xem `app/gioi_han.py`); `500` lỗi nội bộ.
- 401 và 403 phải tách bạch: xác thực (ai gọi) khác phân quyền (được làm gì).
- Không bao giờ trả `200` kèm thân báo lỗi cho lỗi — dùng đúng mã HTTP.

## Phân trang

- Danh sách dài dùng **con trỏ** (cursor), không số trang: `GET /api/nhat-ky`
  dùng tham số `truoc` = thời điểm ISO 8601 lấy từ trường `luc` của dòng cuối trang.
- Sắp xếp phải ổn định (theo cùng một khoá mỗi lần) để con trỏ không nhảy bản ghi.

## Route mỏng, xác thực bằng dependency

```python
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/v1", dependencies=[Depends(xac_thuc_v1)])

@router.post("/chat/completions")
async def chat(yeu_cau: YeuCauChat, nguoi: NguoiGoi = Depends(nguoi_goi)):
    return await dich_vu.chat(yeu_cau, nguoi)
```

- Route chỉ kiểm hình thức (Pydantic) và định tuyến; logic nằm ở lớp dịch vụ riêng
  (xem `app/chuyen_tiep.py` là bộ chuyển tiếp OpenAI dùng chung).
- Xác thực/ủy quyền bằng `Depends(...)` tập trung, không lặp kiểm trong từng route.
- Khai báo `response_model` để ngăn rò dữ liệu ngoài ý muốn.

## Những thứ KHÔNG dùng trong kho (gặp ở tài liệu ngoài thì bỏ)

- SQLAlchemy / Alembic: kho dùng kho dữ liệu trên đĩa (`app/kho.py`), không ORM.
- JWT mẫu: xác thực khoá/vé theo `app/xac_thuc.py`, không tự dựng OAuth2/JWT mới.
- Cấu trúc project mẫu: giữ đúng bản đồ thư mục ở CLAUDE.md.
