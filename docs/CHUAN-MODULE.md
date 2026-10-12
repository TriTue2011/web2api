# Quy chuẩn Module của web2api

Tài liệu này quy định hợp đồng kỹ thuật giữa **lõi web2api** («phiên dịch viên») và các **module**.
Nguồn gốc hợp đồng được định nghĩa tại phần HỢP ĐỒNG trong [app/kham_pha.py](file:///opt/claude-c2a/agy-dieu-phoi/wt/c1-thiet-bi/app/kham_pha.py).

---

## 1. Nguyên tắc cốt lõi: Lõi đặt chuẩn, module đổi theo

1. **Lõi là phiên dịch viên trung lập**: Lõi web2api cung cấp các chuẩn giao tiếp chung (chuẩn OpenAI `/v1/*`, chuẩn thiết bị `/api/thiet-bi`, chuẩn khám phá module). Lõi không chứa tên nhà cung cấp, dịch vụ hoặc phần cứng cụ thể nào.
2. **Module thích ứng theo lõi**: Mọi chuyển đổi giao thức, định dạng dữ liệu, xử lý lỗi đặc thù của bên thứ ba đều phải nằm hoàn toàn bên trong thư mục `modules/<id>/`. Nếu dịch vụ bên ngoài thay đổi API, chỉ sửa module tương ứng, lõi không thay đổi.
3. **Mã mở (Open Extension)**: Các định danh loại (`loai`), khả năng (`cung_cap`), rủi ro hoặc tính năng đều tuân theo nguyên tắc mã mở: những mã lạ chưa có trong danh mục của lõi vẫn được chấp nhận nguyên văn, không bị chặn và không làm vỡ hệ thống.
4. **Kiểm chuẩn lúc nạp**: Khi nạp một module, nếu module khai một khả năng đã có quy chuẩn mà thiếu phương thức bắt buộc, lõi vẫn nạp module vào catalog nhưng đánh dấu «không đúng chuẩn», ghi cảnh báo vào nhật ký và hiển thị trên giao diện Hệ thống → Module. Đường gọi tương ứng sẽ từ chối phục vụ khả năng thiếu chuẩn đó thay vì gây lỗi sập tiến trình.

---

## 2. Bảng hợp đồng khả năng và phương thức

Mọi module triển khai lớp `Module` trong `module.py` với các phương thức nền tảng:
- `__init__(manifest: dict)`: Khởi tạo nhẹ, không gọi mạng.
- `nap(cau_hinh: dict) -> None`: Nạp cấu hình đã lưu.
- `async cai(cau_hinh: dict) -> dict`: Kiểm tra cấu hình và trả về `{ok, ket_qua_tung_khoa, loi, cau_hinh_luu}`.
- `async models() -> list[dict]`: Liệt kê các model dạng `<tien_to>/<tên>`.
- `async suc_khoe() -> dict`: Kiểm tra trạng thái sức khoẻ, hạn mức.
- `async dung() -> None`: Dọn dẹp kết nối khi dừng.

Đối với từng mã khả năng khai trong trường `cung_cap` của `manifest.json`:

| Mã khả năng | Phương thức bắt buộc | Phương thức tuỳ chọn | Đầu vào | Đầu ra chuẩn | Mã lỗi thường gặp |
|---|---|---|---|---|---|
| `chat` | `chat` | — | `body: dict, stream: bool` | `(mã, headers, bytes \| iterator)` | 400 (thiếu model), 429 (hạn mức), 502 (lỗi dịch vụ) |
| `anh` | `anh` | — | `body: dict` | `(mã, headers, bytes)` | 400 (thiếu prompt), 502 |
| `video` | `video_tao`<br>`video_trang_thai`<br>`video_noi_dung` | — | `body: dict` (tạo)<br>`ma: str` (trạng thái/nội dung) | `(mã, headers, bytes)` (tạo/trạng thái)<br>`(mã, headers, bytes \| iterator)` (nội dung) | 400, 404 (mã video), 415 (gửi tệp chưa hỗ trợ), 501 |
| `noi` | `noi` | — | `body: dict` | `(mã, headers, bytes \| iterator)` | 400, 501, 502 |
| `nghe` | `nghe` | — | `yc: YeuCauDaPhan` | `(mã, headers, bytes)` | 400 (thiếu tệp audio), 501, 502 |
| `anh_sua` | `anh_sua` | — | `yc: YeuCauDaPhan` | `(mã, headers, bytes)` | 400 (thiếu ảnh/prompt), 501, 502 |
| `thiet_bi` | `thuc_the`<br>`trang_thai`<br>`thuc_hien` | `su_kien` | `loc: dict \| None` (thực thể)<br>`ids: list[str]` (trạng thái)<br>`id, hanh_dong, tham_so` (thực hiện) | `list[dict]` (thực thể)<br>`dict[str, dict]` (trạng thái)<br>`dict` `{ok, trang_thai_moi?, loi?}` (thực hiện)<br>`async iterator[dict]` (sự kiện) | 400 (hành động không hỗ trợ), 404 (không thấy thực thể), 409 (cần xác nhận), 202 (vào hàng chờ), 502 |
| `nhung` | `nhung` | — | `body: dict` | `(mã, headers, bytes)` | 400 (thiếu input/model), 404 (model), 501, 502 |
| `xep_hang` | `xep_hang` | — | `body: dict` | `(mã, headers, bytes)` | 400 (sai hình dạng documents/query/top_n), 404 (model), 501, 502 |
| `tim_kiem` | `tim_kiem` | — | `yc: dict` | `dict` `{muc: [...], tong?}` | 400, 502 |
| *(Mã lạ)* | Không bắt buộc | Tự do | Tuỳ ý | Tuỳ ý | — |

---

## 3. Chi tiết chuẩn `thiet_bi`

### 3.1. Cấu trúc thực thể (`thuc_the`)
Mỗi thực thể do module trả về phải có cấu trúc từ điển:
```json
{
  "id": "<tien_to>.<tên_thuc_the>",
  "ten": "Tên hiển thị thân thiện",
  "loai": "den | cong_tac | cam_bien | camera | may_chu | cong_mang | ... (mã mở)",
  "khu": "Phòng khách | Tầng 1 | ... (tuỳ chọn nhóm/vị trí)",
  "thuoc_tinh": {
    "ip": "192.168.1.50"
  },
  "tinh_nang": ["bat", "tat", "do_sang"],
  "rui_ro_mac_dinh": "doc | ghi | nguy_hiem"
}
```
- **Quy tắc tiền tố**: `id` BẮT BUỘC bắt đầu bằng `<tien_to>.` khớp với `tien_to` khai báo trong `manifest.json`. Lõi sẽ bỏ qua các thực thể sai tiền tố hoặc trùng lặp `id` mà không làm hỏng toàn bộ danh sách.
- **Mức rủi ro**:
  - `"doc"`: Chỉ đọc dữ liệu, không làm thay đổi trạng thái thiết bị.
  - `"ghi"`: Thao tác thông thường (bật, tắt, đổi trạng thái công tắc/đèn).
  - `"nguy_hiem"`: Thao tác có tính chất rủi ro cao (mở khoá cửa, khởi động lại hệ thống mạng, xoá dữ liệu).

### 3.2. Trạng thái (`trang_thai`)
Nhận danh sách `ids: list[str]` và trả về từ điển:
```json
{
  "<tien_to>.<tên>": {
    "gia_tri": "on",
    "don_vi": "W",
    "luc": "2026-10-12T15:00:00Z",
    "hop_le": true
  }
}
```

### 3.3. Thực hiện hành động (`thuc_hien`)
- Gọi khi khách hoặc quản trị kích hoạt một hành động nằm trong `tinh_nang` của thực thể.
- Nhận `id: str`, `hanh_dong: str`, `tham_so: dict`.
- Trả về: `{"ok": bool, "trang_thai_moi": {...}, "loi": "thông điệp nếu thất bại"}`.

### 3.4. Sự kiện thời gian thực (`su_kien`)
- Tuỳ chọn cho module có khả năng đẩy sự kiện thời gian thực (Server-Sent Events).
- Trả về async iterator sinh các đối tượng `{id, loai, gia_tri, luc}`.

---

## 4. Chi tiết chuẩn `tri_thuc` (nhung, xep_hang, tim_kiem)

### 4.1. Nhúng vector (`nhung`)
- Phương thức: `async nhung(body: dict) -> (mã, headers, bytes)` phục vụ `POST /v1/embeddings`.
- Nhận `body`: `{"model": "<tien_to>/<tên>", "input": "chuỗi" | ["chuỗi 1", "chuỗi 2"]}`.
- Trả về phản hồi chuẩn: `(200, {"content-type": "application/json"}, bytes_json)` với `data: [{"object": "embedding", "embedding": [...], "index": 0}]`.

### 4.2. Xếp hạng lại (`xep_hang`)
- Phương thức: `async xep_hang(body: dict) -> (mã, headers, bytes)` phục vụ `POST /v1/rerank`.
- Nhận `body`: `{"model": "<tien_to>/<tên>", "query": "chuỗi tìm", "documents": ["đoạn 1", "đoạn 2"] | [{"text": "..."}, ...], "top_n": 5}`.
- Lõi kiểm hình dạng trước khi chuyển tới module: `documents` phải là danh sách, `query` là chuỗi, `top_n` là số nguyên dương ≤ số tài liệu.
- Trả về phản hồi chuẩn: `(200, {"content-type": "application/json"}, bytes_json)` với `results: [{"index": 0, "relevance_score": 0.95}, ...]`.

### 4.3. Tìm kiếm (`tim_kiem`)
- Phương thức: `async tim_kiem(yc: dict) -> dict` phục vụ `GET /api/tim-kiem/nguon` và `POST /api/tim-kiem`.
- Trường manifest `so_do_tim_kiem` (mở, tuỳ chọn): danh sách mã sơ đồ module hỗ trợ (ví dụ `["toan_van", "ngu_nghia"]`). `GET /api/tim-kiem/nguon` liệt kê danh sách này theo từng nguồn để giao diện hiển thị các công tắc tương ứng.
- Đầu vào `yc`: `{chuoi?, loc?, bat_tat?, cay?, mat?, muc_luc?, chi_muc?, so_luong?, ngu_canh?}` (mọi trường là mã mở):
  - **Công tắc (`bat_tat`)**:
    - `bat_tat.nguon = {<id module>: bool}`: lõi tự động loại trừ nguồn bị tắt (`false`) khỏi tầng truy hồi; nguồn không tồn tại hoặc tắt được ghi nhận vào `bao_cao` là bỏ qua, không gây lỗi.
    - `bat_tat.so_do = {<mã sơ đồ>: bool}`: lõi chuyển nguyên vào `yc` của module. Sơ đồ lạ module bỏ qua, phản hồi ghi nhận `so_do_bo_qua` theo nguồn (không lỗi).
  - **Lọc nhiều mặt (`loc`)**: hỗ trợ giá trị đơn (bằng), danh sách (thuộc một trong), khoảng `{tu, den}` cho số và chuỗi ISO ngày giờ, loại trừ `{khac: ...}`, và trường lồng bằng đường chấm (`truong.kenh`). Kiểu dữ liệu không khớp thì mục không qua lọc (không lỗi).
  - **Đếm theo mặt (`mat`)**: `mat: ["truong.kenh", ...]`, phản hồi trả về `mat: {"truong.kenh": {"<giá trị>": số mục}}` được tính trên kết quả trước khi cắt `so_luong`.
  - **Cây phân cấp (`cay`)**: `{truong, goc, do_sau?}` lọc theo tiền tố đường dẫn phân cấp (phân tách «/»), độ sâu tính từ `goc`. Nếu `cay.dem: true`, phản hồi trả về `cay_nut: [{duong, so_muc}]` các nút con trực tiếp dưới `goc` kèm số mục tính trước khi cắt `so_luong`.
  - **Nhảy tới đề mục (`muc_luc`)**: `{tai_lieu, muc_id}` giới hạn tìm kiếm trong một đề mục cụ thể. Module không hỗ trợ thì lõi tự lọc theo `vi_tri`/`truong.muc_id` của mục nếu có; nếu không có thì ghi `so_do_bo_qua`.
  - **Chỉ mục A–Z (`chi_muc`)**: `{truong, kieu: "chu_cai", chon?}`. Phản hồi trả về `chi_muc: {"A": n, "B": n, …, "#": n}` đếm theo chữ cái đầu (bỏ dấu tiếng Việt khi xếp: «Đ» xếp cùng «D» nhưng giữ nguyên chữ ở mục) tính trước `cat`. Nếu có `chon` (ví dụ `"B"`), kết quả chỉ giữ các mục bắt đầu bằng chữ đó.
- Đầu ra: `{muc: [{id, diem, doan, tai_lieu?, vi_tri?, truong?: dict}], tong?, so_do_bo_qua?: dict, mat?: dict, cay_nut?: list, chi_muc?: dict}`.
- **An toàn nội dung**: Nội dung tìm kiếm là đầu vào không tin cậy. Kết quả mỗi mục do lõi trả ra luôn gắn `nguon` (id module) và `nguon_ngoai: true`. Lõi không tự động chuyển nội dung này thành chỉ thị hệ thống.

### 4.4. Mục lục tài liệu (`muc_luc` — tuỳ chọn)
- Phương thức: `async muc_luc(yc: dict) -> dict` phục vụ `POST /api/tim-kiem/muc-luc`.
- Đầu vào `yc`: `{"tai_lieu": str, "do_sau": int (tuỳ chọn)}`.
- Đầu ra: `{"tai_lieu": str, "tieu_de": str (tuỳ chọn), "muc": [{"id": str, "tieu_de": str, "cap": int, "vi_tri": any, "con": [...]}]}`.
- Lõi kiểm hình dạng: bỏ nút sai không vỡ cây, áp trần độ sâu (mặc định 6, cấu hình qua `WEB2API_MUC_LUC_TRAN_DO_SAU`) và trần số nút (mặc định 2000, cấu hình qua `WEB2API_MUC_LUC_TRAN_SO_NUT`). Module không có phương thức → lõi trả mã `501` rõ ràng. Yêu cầu quyền/phạm vi `tim_kiem`.

