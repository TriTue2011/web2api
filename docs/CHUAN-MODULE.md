# Quy chuẩn Module của web2api

Tài liệu này quy định hợp đồng kỹ thuật giữa **lõi web2api** («phiên dịch viên») và các **module**.
Nguồn gốc hợp đồng được định nghĩa tại phần HỢP ĐỒNG trong [app/kham_pha.py](file:///opt/claude-c2a/agy-dieu-phoi/wt/c1-thiet-bi/app/kham_pha.py).

---

## 1. Nguyên tắc cốt lõi: Lõi đặt chuẩn, module đổi theo

1. **Lõi là phiên dịch viên trung lập**: Lõi web2api cung cấp các chuẩn giao tiếp chung (chuẩn OpenAI `/v1/*`, chuẩn thiết bị `/api/thiet-bi`, chuẩn khám phá module). Lõi không chứa tên nhà cung cấp, dịch vụ hoặc phần cứng cụ thể nào.
2. **Module thích ứng theo lõi**: Mọi chuyển đổi giao thức, định dạng dữ liệu, xử lý lỗi đặc thù của bên thứ ba đều phải nằm hoàn toàn bên trong thư mục `modules/<id>/`. Nếu dịch vụ bên ngoài thay đổi API, chỉ sửa module tương ứng, lõi không thay đổi.
3. **Mã mở (Open Extension)**: Các định danh loại (`loai`), khả năng (`cung_cap`), rủi ro hoặc tính năng đều tuân theo nguyên tắc mã mở: những mã lạ chưa có trong danh mục của lõi vẫn được chấp nhận nguyên văn, không bị chặn và không làm vỡ hệ thống.
4. **Kiểm chuẩn lúc nạp**: Khi nạp một module, nếu module khai một khả năng đã có quy chuẩn mà thiếu phương thức bắt buộc, lõi vẫn nạp module vào catalog nhưng đánh dấu «không đúng chuẩn», ghi cảnh báo vào nhật ký và hiển thị trên giao diện Hệ thống → Module. Đường gọi tương ứng sẽ từ chối phục vụ khả năng thiếu chuẩn đó thay vì gây lỗi sập tiến trình.
5. **Cập nhật không lén thêm quyền**: Các trường mang quyền của manifest (`cung_cap`, `can_module`, `nhan_cookie`, `yeu_cau_goi`, `tai_nguyen.mang_ra`, và mọi trường lạ chưa biết) được so sánh mỗi khi module đã cài có bản mới từ git — bản mới xin **thêm** quyền thì không tự kích hoạt, chờ quản trị duyệt đúng phần thêm (409 `can_duyet_quyen`). Riêng `tai_nguyen`: `ghi_chu` chỉ là chữ mô tả (không phải quyền); số tài nguyên (`dia_mb`, `ram_them_mb`…) chỉ **tăng** mới cần duyệt, giảm/giữ nguyên thì qua tự do. Người viết module lưu ý: thêm quyền vào bản cập nhật đồng nghĩa quản trị phải duyệt tay; bớt quyền thì qua tự do.

---

## 2. Bảng hợp đồng khả năng và phương thức

Mọi module triển khai lớp `Module` trong `module.py` với các phương thức nền tảng:
- `__init__(manifest: dict)`: Khởi tạo nhẹ, không gọi mạng.
- `nap(cau_hinh: dict) -> None`: Nạp cấu hình đã lưu.
- `async cai(cau_hinh: dict) -> dict`: Kiểm tra cấu hình và trả về `{ok, ket_qua_tung_khoa, loi, cau_hinh_luu}`.
- `async models() -> list[dict]`: Liệt kê các model dạng `<tien_to>/<tên>`. Bắt buộc khi `cung_cap` có khả năng định tuyến theo `model` (`chat`, `anh`, `anh_sua`, `video`, `noi`, `nghe`, `nhung`, `xep_hang` — `KHA_NANG_CO_MODEL` ở `app/kham_pha.py`); module chỉ làm thiết bị/tri thức không cần. Thiếu thì module không có model trong `GET /v1/models`.

`chat` không còn bắt buộc chung: chỉ bắt buộc khi `cung_cap` có `chat` (bảng dưới) — module chỉ làm giọng nói, sửa ảnh, thiết bị hay trình duyệt không cần khai hàm `chat` giả. Lõi định tuyến theo phương thức CÓ THẬT — module không có `chat` gọi `POST /v1/chat/completions` nhận `501 capability_not_supported`.
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
| `trinh_duyet` | `trinh_duyet_mo`<br>`trinh_duyet_dong`<br>`trinh_duyet_ds` | `trinh_duyet_tao`<br>`trinh_duyet_cookie` | `ho_so, loai, url?` (mở)<br>`ho_so, giu_phien` (đóng)<br>`ten` (tạo)<br>`ho_so, mien` (cookie) | `dict` `{ho_so, dich, loai}` (mở)<br>`dict` `{ok}` (đóng)<br>`list[dict]` (danh sách)<br>`{ho_so, ten}` (tạo)<br>`{cookie}` (cookie) | 400, 404, 501, 502 |
| `kenh_ra` | `gui_tin`<br>`kenh_ra` | — | `dich: {kenh_id, ten}`, `noi_dung: dict` (kèm `_tep` khi có tệp) | `dict` có `ok` là bool thật (`ok` thiếu/khác bool → lõi coi là thất bại `502`) | 400 (`kenh_required` khi nhiều đích mà không nêu `kenh_id`), 404 (đích lạ), 413, 429, 502, 503 (chưa có đích) |
| `ma_xac_minh` | `lay_ma`<br>`hop_thu_ra` | — | `yeu_cau: dict` gồm `hop_thu`, `tu_mien`, `sau_luc` (ISO UTC), `do_dai` `[min, max]`, `cho_giay` (kẹp 5..180) | `dict` `{ok, ma?}` khi thấy mã mới hơn `sau_luc`; `{ok: False, ma_loi}` khi chưa cấu hình/hết giờ/lỗi | `khong_co_hop_thu`, `het_gio`, `sai_dang_nhap`, `mang_loi`, `khong_thay_ma`, `yeu_cau_sai` |
| *(Mã lạ)* | Không bắt buộc | Tự do | Tuỳ ý | Tuỳ ý | — |

> **Lưu ý**: Phương thức `anh` và `anh_sua` cũng được gọi từ `POST /v1/responses` khi client chỉ định công cụ `image_generation` (không có ảnh đầu vào → gọi `anh` với thân kiểu `/v1/images/generations`, `response_format: "b64_json"`; có `input_image` dạng data URL base64 → dựng `YeuCauDaPhan` gọi `anh_sua`). Module nhận y như từ đường chuẩn: `model` đầy đủ `<tiền tố>/<tên>`, các trường `prompt`, `n`, `size`, `quality`, `background`, `output_format` (nếu khách gửi). Đường này cần phản hồi `b64_json` (trả `url` → `502`); nên nêu `size`/`quality`/`background`/`output_format` ở cấp đầu phản hồi để lõi báo đúng thuộc tính của kết quả.

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
    "mac": "aa:bb:cc:dd:ee:ff",
    "ip": "192.168.1.50",
    "ten_may_tu_khai": "iphone-guest",
    "bang_tan": "5GHz"
  },
  "tinh_nang": ["bat", "tat", "do_sang"],
  "rui_ro_mac_dinh": "doc | ghi | nguy_hiem"
}
```
- **Thuộc tính chuẩn cho thiết bị mạng (`thuoc_tinh`)**:
  - `mac`: Địa chỉ MAC của thiết bị (chuỗi bất kỳ định dạng chuẩn, lõi tự chuẩn hoá về dạng `aa:bb:cc:dd:ee:ff`). Đây là điều kiện nhận diện thực thể là máy mạng trong sổ thiết bị của lõi.
  - `ip`: Địa chỉ IP hiện tại của thiết bị (tuỳ chọn).
  - `ten_may_tu_khai`: Tên máy tự khai qua DHCP/mDNS (tuỳ chọn, dữ liệu KHÔNG TIN — lõi làm sạch ký tự điều khiển/ngắt dòng và cắt 40 ký tự).
  - `bang_tan`: Băng tần WiFi/kết nối (ví dụ `"2.4GHz"`, `"5GHz"`, `"6GHz"` — tuỳ chọn).
- **Quy tắc tiền tố**: `id` BẮT BUỘC bắt đầu bằng `<tien_to>.` khớp với `tien_to` khai báo trong `manifest.json`. Lõi sẽ bỏ qua các thực thể sai tiền tố hoặc trùng lặp `id` mà không làm hỏng toàn bộ danh sách.
- **Mức rủi ro**:
  - `"doc"`: Chỉ đọc dữ liệu, không làm thay đổi trạng thái thiết bị.
  - `"ghi"`: Thao tác thông thường (bật, tắt, đổi trạng thái công tắc/đèn).
  - `"nguy_hiem"`: Thao tác có tính chất rủi ro cao (mở khoá cửa, khởi động lại hệ thống mạng, xoá dữ liệu).
  - **Nguyên tắc ĐÓNG KHI LỖI**: Nếu module không khai `rui_ro_mac_dinh` hoặc khai mức lạ/không hợp lệ, lõi tự động coi là `"nguy_hiem"` (yêu cầu phê duyệt khi khách gọi). Module muốn hạ mức rủi ro phải chủ động khai báo tường minh `"doc"` hoặc `"ghi"`.
- **Tính năng (`tinh_nang`) và Lược đồ tham số**:
  - Mỗi phần tử có thể là chuỗi tên hành động (vd `"bat"`) hoặc đối tượng khai báo chi tiết kèm lược đồ tham số:
    ```json
    {
      "hanh_dong": "chinh_sang",
      "mo_ta": "Điều chỉnh mức độ sáng của đèn",
      "tham_so": {
        "type": "object",
        "required": ["muc_sang"],
        "additionalProperties": false,
        "properties": {
          "muc_sang": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Mức sáng từ 0 đến 100"},
          "khoa_bi_mat": {"type": "string", "bi_mat": true, "description": "Khoá bí mật cho thiết bị"},
          "che_do": {"type": "string", "enum": ["doc_sach", "ngu", "lam_viec"]}
        }
      }
    }
    ```
  - **Kiểm tra tham số tại lõi (SEC-004a)**: Trước khi gọi `thuc_hien`, lõi tự động thẩm định `tham_so` theo lược đồ đã khai: kiểm tra trường bắt buộc (`required`), cấm khoá lạ nếu `additionalProperties: false`, kiểm tra đúng kiểu dữ liệu (`integer`, `number`, `string`, `boolean`), giới hạn khoảng giá trị (`minimum`, `maximum`, `minLength`, `maxLength`), và tập giá trị cho phép (`enum`). Tham số không đạt chuẩn bị từ chối ngay với mã `400` kèm thông báo lỗi cụ thể.
  - **Tham số bí mật theo nguyên tắc (SEC-004b)**: Lõi tuyệt đối không dùng danh sách từ khoá để đoán thuộc tính nhạy cảm. Thay vào đó:
    - Thuộc tính khai báo `"bi_mat": true` trong lược đồ sẽ được che thành `…che` khi ghi vào sổ nhật ký kiểm toán.
    - Mọi tham số KHÔNG được khai báo trong lược đồ (hoặc khi hành động không có lược đồ) đều được ghi nhận ở dạng an toàn `…che(độ dài N)`, chỉ phản ánh tên và độ dài chuỗi mà không lộ dữ liệu thô.
  - **Xác nhận tính năng và cách ly thực thể mới (SEC-004f)**: Lõi tính băm SHA-256 ổn định của `tinh_nang` và lưu trong kho `thiet_bi.json`. Mọi thực thể mới xuất hiện sau khi cài đặt hoặc bị thay đổi danh sách `tinh_nang` đều tự động bị áp mức rủi ro hiệu lực `"nguy_hiem"` cho tới khi quản trị viên phê duyệt xác nhận qua `POST /api/thiet-bi/{id}/xac-nhan` hoặc `PUT /api/thiet-bi/{id}/rui-ro`.

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

---

## 5. Chi tiết chuẩn `trinh_duyet` (Chuẩn C6: noVNC và hồ sơ trình duyệt)

Dành cho module điều khiển dịch vụ trình duyệt riêng (đăng nhập tay OTP/captcha, giữ phiên trong hồ sơ bền vững):
- `async trinh_duyet_mo(ho_so: str, loai: str) -> dict`: Mở hoặc khôi phục hồ sơ, trả về `{"ho_so": str, "dich": "http://[w2a:<bí-mật>@]host:port", "loai": str}`. `dich` có thể mang userinfo (bí mật riêng mỗi hồ sơ mà websockify của dịch vụ đòi bằng Basic auth): lõi tách nó ra, kiểm SSRF theo HOSTNAME (mọi địa chỉ phân giải được), GHIM IP lúc mở và proxy giao diện noVNC + WebSocket tới đúng IP đó kèm Basic auth; `dich` chỉ nằm trong RAM, không vào log/nhật ký/phản hồi. Mở lại cùng hồ sơ mà máy/cổng/đường của `dich` đổi → `502`.
- `async trinh_duyet_dong(ho_so: str, giu_phien: bool) -> dict`: Đóng hồ sơ; `giu_phien=True` (mặc định của API khi quản trị không gửi thân) giữ thư mục dữ liệu hồ sơ (cookie còn), `False` xoá hồ sơ. Lõi THU HỒI TRƯỚC khi gọi phương thức này (module ném lỗi thì quyền truy cập vẫn đã bị cắt, API trả `502` kèm `da_thu_hoi: true`, không nhúng nguyên văn lỗi) — lõi xoá toàn bộ vé/phiên và đóng kết nối WebSocket liên quan.
- `async trinh_duyet_ds() -> list[dict]`: Liệt kê workspace: `[{"ho_so": str, "ten"?: str, "loai": str, "trang_thai": str, "dang_mo"?: bool, "thu_cong"?: bool, "luc_mo"?: str}]`. Lõi chỉ trả đúng các trường này (trường khác như `dich` bị bỏ).
- `trinh_duyet_mo` nhận thêm từ khoá `url` (tuỳ chọn): trang đầu khi trình duyệt KHỞI ĐỘNG. Lõi kiểm trước (http/https, có tên máy, không userinfo, ≤ 2048 ký tự ASCII in được, sai → `400` không phản chiếu) và chỉ truyền khi quản trị gửi — module cũ chỉ nhận `(ho_so, loai)` vẫn chạy.
- TUỲ CHỌN `async trinh_duyet_tao(ten: str) -> dict`: tạo workspace `{"ho_so": str, "ten": str}` — tên hiển thị tuỳ ý (lõi kiểm 1–100 ký tự in được), thư mục hồ sơ ID NGẪU NHIÊN do dịch vụ sinh, không suy từ tên/email (mô hình workspace của c2a). Không mở trình duyệt. Thiếu → `501`.
- TUỲ CHỌN `async trinh_duyet_cookie(ho_so: str, mien: str) -> dict`: `{"cookie": "tên=giá trị; …"}` — cookie CÒN HẠN của `mien` và tên miền con trong hồ sơ (lõi đã kiểm `mien`: chữ thường, ≥ 2 nhãn). Thiếu → `501`. Lỗi có cấu trúc qua thuộc tính `ma` của ngoại lệ: `400`/`404`/`409`/`501` được lõi chuyển tiếp kèm câu của LÕI; lỗi khác → `502` chỉ nêu tên kiểu lỗi. Module không log giá trị cookie.
- Lõi dùng `trinh_duyet_cookie` ở `POST /api/trinh-duyet/{module}/{ho_so}/cookie-sang-module` thân `{"module_dich", "truong", "mien"?}` (chỉ quản trị): `truong` phải là trường mà CHÍNH module đích khai nhận cookie trong `truong_cau_hinh` — `"nhan_cookie": {"mien": "<tên miền chặt, ≥ 2 nhãn>"}`, chỉ đặt được trên trường `kieu` `bi_mat` (thay) hoặc `danh_sach_bi_mat` (THÊM vào cuối, khử trùng); `kiem_manifest` từ chối hình dạng sai. Trường bí mật không khai (khoá API…) → `400`, giá trị cũ giữ nguyên. Tên miền là tên miền ĐÃ KHAI: `mien` trong thân bỏ trống hoặc phải bằng nó, khác → `400` (người gọi không chọn tên miền, nên cũng không chạm được đuôi tên miền công cộng như `co.uk`). Cookie đi vào cấu hình QUA ĐÚNG đường cài có sẵn (`cai()` của module đích kiểm rồi lõi mới lưu, bí mật vào kho 0600). Phản hồi chỉ `{"ok", "ket_qua_tung_khoa" ([{khoa (đúng dạng che «…abcd», sai dạng → «…»), ok, loi (câu của LÕI)}]), "da_them", "loi"? (câu của LÕI)}`: lõi KHÔNG chuyển tiếp chuỗi tự do nào của module đích (có thể chứa cookie mới/cũ ở dạng mã hoá bất kỳ).
- Nội dung `dich` phục vụ (noVNC) bị coi là không tin được: lõi phục vụ nó dưới `/novnc/s/<phien>/` với CSP `sandbox` (origin mờ) và header theo danh sách cho phép; module không cần và không được tự proxy.
- TUỲ CHỌN `trinh_duyet_that(ho_so: str) -> BoDieuKhienTrinhDuyet` (đồng bộ, NV92): trả bộ điều khiển
  trình duyệt THẬT cho hồ sơ (điều khiển qua các đường `/ho-so/{id}/dieu-khien/*`… của dịch vụ).
  Lõi tìm module có khả năng `trinh_duyet` qua `app/khoi_phuc_phien.lay_trinh_duyet_that(ho_so,
  dang_chay)` — KHÔNG gọi tên module nào; module ném lỗi/trả kiểu lạ/`ho_so` sai khuôn → lõi
  nhận `None` (đóng cửa). Thể hiện phải thực hiện đúng giao diện `BoDieuKhienTrinhDuyet`
  (`mo_trang`, `dien`, `bam`, `cho`, `doc_cookie`, `tin_hieu`, `gianh`/`nha`/`dong`); lỗi dịch
  vụ chỉ mang mã trạng thái (không chép nội dung phản hồi — có thể chứa cookie); giá trị điền
  không vào log; mọi lời gọi mang Bearer <redacted> vụ và qua kiểm IP lúc kết nối (chống DNS rebinding).
- TUỲ CHỌN (provider web) `async tai_dung_ho_so(ho_so: str, trinh_duyet: BoDieuKhienTrinhDuyet | None)
  -> KetQuaDangNhap` (NV92): «Đăng nhập bằng Google» trong hồ sơ đã đăng nhập sẵn, lấy phiên và
  thêm tài khoản của CHÍNH module (qua `cai()` của lõi ở tầng API, module chỉ trả `KetQuaDangNhap`).
  Quy tắc: có phiên sẵn (`tin_hieu()` có `da_dang_nhap`) thì ĐI TẮT (không bấm/điền thêm); chưa thì
  bấm nút «Đăng nhập bằng Google» của provider; gặp CAPTCHA/Cloudflare/2FA không có mã/xác minh
  thiết bị → `can_nguoi` (KHÔNG tự giải, KHÔNG che dấu); KHÔNG BAO GIỜ điền email/mật khẩu
  (mật khẩu thuộc về module `ho-so-google`); `trinh_duyet is None` → `can_nguoi`. Kết quả và mọi
  phản hồi/log không chứa bí mật thô.

---

## 6. Hợp đồng chuyển tiếp nhị phân và tệp tải lên (Audio, Images)

### 6.1. Luồng nhị phân (TTS `POST /v1/audio/speech`)
- Phương thức module: `async noi(body: dict) -> (mã, headers, bytes | async-iterator[bytes])`.
- **Stream nhị phân**: Dữ liệu âm thanh thô chứa các byte đặc biệt `\n`, `\r`, `\x00` và toàn bộ 256 giá trị byte. Bộ chuyển tiếp stream bằng `aiter_bytes()`, tuyệt đối không cắt dòng theo text (tránh làm biến dạng hoặc hỏng byte). Luồng của dịch vụ xa đứt giữa chừng (hay gzip hỏng) thì lỗi đi lên: kết nối tới khách bị cắt, nhật ký cuộc gọi ghi `dut` — không kết thúc êm như thể tệp đã trọn.
- **Header của dịch vụ xa** (bộ chuyển tiếp dùng chung `ChuyenTiepOpenAI`): mặc định từ chối — chỉ `content-type` tới khách (`tieu_de_cho_khach` trong `app/chuyen_tiep.py`, chỗ duy nhất cho chat, ảnh/video, giọng nói, multipart). Set-Cookie, Access-Control-*, Server, X-Powered-By, WWW-Authenticate, Connection, Keep-Alive, Content-Disposition, CSP… của dịch vụ xa không tới khách; `content-length`/`content-encoding` cũng không (httpx đã giải nén). Khi trả stream, lõi bỏ `content-length`/`content-encoding`/`transfer-encoding` mà module tự đặt (lõi quyết khung truyền).
- **Vòng đời stream (`aclose`)**: Lõi bảo đảm luôn gọi `aclose()` trên iterator của module đúng một lần cả khi luồng kết thúc bình thường lẫn khi khách ngắt kết nối giữa chừng. Iterator của module nên là đối tượng có `aclose()` đóng tài nguyên kể cả khi CHƯA đọc khúc nào (async generator chưa chạy thì `aclose()` không chạy `finally` của nó).
- **Xoay khoá** (bộ chuyển tiếp dùng chung): `noi`, `nghe`, `anh_sua` nhớ trạng thái theo (khoá, việc) như ảnh/video (`goi_kha_nang`) — khoá bị từ chối giọng nói không làm chat nghỉ, và ngược lại.

### 6.2. Yêu cầu đa phần (`YeuCauDaPhan`, STT `POST /v1/audio/transcriptions` và sửa ảnh `POST /v1/images/edits`)
- Phương thức module: `async nghe(yc: YeuCauDaPhan) -> (mã, headers, bytes)` và `async anh_sua(yc: YeuCauDaPhan) -> (mã, headers, bytes)`.
- **Dựng multipart an toàn**:
  - Tên tệp (`filename`) do khách khai là đầu vào không tin cậy: lõi giải mã unquote, loại bỏ đường dẫn (`../`, `/`), bỏ ký tự điều khiển ASCII trước khi chuyển tiếp.
  - MIME type (`content_type`) chỉ chấp nhận định dạng chuẩn `type/subtype` hợp lệ, mọi chuỗi lạ/rác được fallback về `application/octet-stream`.
  - Giữ nguyên toàn bộ các trường chữ lạ (không lọc bỏ) và đổi tên model bỏ tiền tố `<tien_to>/`.
  - Nội dung tệp nhị phân được truyền trực tiếp dạng bytes vào multipart payload mà không nhân đôi RAM.


---

## 7. Cửa webhook chung của lõi (`POST /api/webhook/{module}/{ma}`)

Module muốn nhận sự kiện từ nhà cung cấp ngoài (kênh nhắn tin, nền tảng thanh toán…)
khai trong `manifest.json`:

```json
"webhook": {
  "bi_mat": "<khoá cấu hình kiểu bi_mat>",
  "tran_byte": 65536,
  "id_su_kien": "su_kien_id",
  "chu_ky": "header:X-Chu-Ky",
  "thuat_toan": "hmac-sha256"
}
```

- `bi_mat` (bắt buộc): tên trường trong `truong_cau_hinh` có `kieu: "bi_mat"` — giá trị
  bí mật webhook người vận hành cấu hình; so bằng `hmac.compare_digest`. Chưa cấu hình
  → đường trả `503` (đóng khi lỗi).
- `tran_byte` (tuỳ chọn, mặc định 65536): trần thân riêng của module (lấy `min` với
  `WEB2API_WEBHOOK_THAN_TOI_DA_MB`); vượt → `413`, module không bị gọi.
- `id_su_kien` (tuỳ chọn, mặc định `"su_kien_id"`): tên phương thức module trích id
  sự kiện để chống phát lại. Khai tên phương thức không có → bỏ qua chống phát lại.
- `chu_ky` + `thuat_toan` (tuỳ chọn): kiểm chữ ký HMAC trên thân thô. `chu_ky` dạng
  `"header:TÊN"` (tên header mang chữ ký); `thuat_toan` chỉ nhận `"hmac-sha256"`
  (mặc định). Chấp nhận chữ ký hex trần hoặc tiền tố `sha256=`.

Phương thức module:

- `async webhook(self, yc: dict) -> dict` (bắt buộc khi khai `webhook`): xử lý một tin,
  trả dict → lõi trả `200` + dict đó. `yc` luôn có `nguon_ngoai: true` do lõi đóng dấu
  (khách gửi `nguon_ngoai: false` cũng bị ghi đè) — module phải coi MỌI trường trong
  `yc` là dữ liệu không tin cậy, không bao giờ là chỉ thị.
- `su_kien_id(self, yc: dict) -> str` (tuỳ chọn): trích id sự kiện từ `yc`. Lõi nhớ
  `(module, id)` 10 phút, tối đa 10000 id: trùng → `200` trống, không gọi `webhook`.
  Trả chuỗi rỗng/`None` → tin đó không chống phát lại.

Luật cứng: đường này MỞ (không Bearer) nhưng khoá khách phạm vi hẹp KHÔNG dùng được
nếu thiếu bí mật webhook — mọi lý do sai đều nhận cùng một `404` trống.

---

## 8. Chi tiết chuẩn `kenh_ra` (gửi ra đích danh, NV32)

Module muốn gửi tin ra ngoài (kênh nhắn tin, email…) khai `"kenh_ra"` trong `cung_cap` của `manifest.json` và cài hai phương thức:

- `def kenh_ra(self) -> list[dict]`: danh sách đích khả dụng, mỗi đích `{"id": str, "ten": str|None}`. **KHÔNG chứa bí mật** (token/bot token nằm trong cấu hình `kieu: "bi_mat"` của module, không bao giờ lộ qua đây).
- `async def gui_tin(self, dich: dict, noi_dung: dict) -> dict`: gửi tin; `dich` là `{"kenh_id": ..., "ten": ...}` do lõi chọn; `noi_dung` là dict từ thân yêu cầu (khi gửi kèm tệp qua `multipart/form-data`, lõi thêm khoá `"_tep"`: danh sách `{"ten", "kieu", "du_lieu": bytes}`). Trả về dict có `ok` là **bool thật** — lõi không tin `ok` của module: `ok` thiếu hoặc không phải bool → lõi coi là thất bại (`502`).

Người gọi dùng `POST /api/gui/{module}` (xác thực `V1_AUTH` + phạm vi riêng `gui:<module>`; id module có `-` thì mã phạm vi thay bằng `_`, vd module `mod-a` dùng phạm vi `gui:mod_a`):

- Thân JSON `{"kenh_id": "...", "noi_dung": {...}}`, hoặc `multipart/form-data` với trường chữ `kenh_id`, `noi_dung` (JSON) và các tệp ở trường `tep`.
- `kenh_id` **bắt buộc** khi module có hơn một đích — lõi không đoán «đích đầu danh sách» (`400` `kenh_required`); module chỉ có một đích thì được bỏ qua.
- Trần: 20 tin/phút theo (khoá, module), 8 lượt đồng thời toàn cục (`429` + `Retry-After`); nội dung ≤ 16 KiB, tệp ≤ 25 MB (`413`); gọi module quá 60 giây → `504`.
- Phản hồi thành công `{"ok": true, "kenh": "<id>"}` — luôn nêu kênh thực sự đã gửi để người gọi tự xác minh không bị gửi nhầm. Lỗi thô của nhà cung cấp không bao giờ lộ ra phản hồi.
- Nhật ký kênh `gui_ra`: module, `kenh_id` băm SHA-256 rút gọn (16 ký tự hex), mã kết quả, độ dài — không nội dung tin, không token.

---

## 9. Khai báo manifest.json và trường bắt buộc `hang`

Mỗi module bắt buộc có tệp `manifest.json` ở thư mục gốc của module. Các trường chuỗi bắt buộc gồm: `id`, `ten`, `mo_ta`, `loai`, `giao_thuc`, `tien_to` và `hang`.

Trường `hang` là **bắt buộc** trong `manifest.json`, chỉ nhận giá trị `"A"` hoặc `"B"` (`kiem_manifest` từ chối manifest thiếu `hang` hoặc mang giá trị khác):
- `"A"`: Chủ máy đã đọc toàn bộ mã nguồn, ghim SHA của bản đã đọc, tự chịu trách nhiệm; chạy trong tiến trình lõi.
- `"B"`: Mã của bên thứ ba, chạy ngoài tiến trình lõi.

> **Lưu ý nguyên tắc bảo mật**: `hang` là lời khai của tác giả; máy chủ có quyền coi module bên thứ ba là B bất kể lời khai.

### Trường tuỳ chọn `nhom` (gom nhóm nhà cung cấp trên giao diện)

Module có thể khai báo thêm trường tuỳ chọn `nhom` dưới dạng object `{"id": "...", "ten": "..."}`:
- `id`: Định danh nhóm, bắt buộc khớp biểu thức chính quy `^[a-z0-9-]{1,32}$`.
- `ten`: Tên hiển thị của nhóm (ví dụ: `"Google"`, `"OpenAI"`, `"Anthropic"`), độ dài tối đa 40 ký tự.

Trường `nhom` dùng để gom các module thuộc cùng một hệ sinh thái/nhà cung cấp trên giao diện người dùng (ví dụ: tab Hệ thống → AI → Tài khoản gom theo nhóm). Khi module không khai báo trường này hoặc khai báo sai khuôn (không phải object, thiếu khoá, id/ten không đúng quy cách), lõi sẽ bỏ qua trường `nhom`, ghi một cảnh báo `log.warning` (chỉ chứa id module và lý do ngắn), module vẫn nạp bình thường và tự động xếp vào nhóm mặc định là `"Khác"` (`id: "khac"`, `ten: "Khác"`).

Khai báo nhóm của các module mẫu trong kho:
- `gemini-api`, `gemini-web`, `antigravity`, `ho-so-google`: `{"id": "google", "ten": "Google"}`
- `chatgpt-web`: `{"id": "chatgpt", "ten": "ChatGPT"}`
- `grok-web`, `grok-api`: `{"id": "grok", "ten": "Grok"}`
- `claude-web`: `{"id": "claude", "ten": "Claude"}`
- `muse`, `muse2api`: `{"id": "muse", "ten": "Muse"}`
- `vnc-xa`: không khai báo (xếp vào nhóm «Khác»).

> **Lưu ý kiến trúc**: Không bao giờ coi `nhom` là định danh duy nhất của module hay của nhà cung cấp ngoài đời. Nhiều module có thể dùng chung một `nhom` (ví dụ một module cổng API/provider và một module hồ sơ/đăng nhập trình duyệt của cùng một hãng); một module không khai báo thì thuộc nhóm Khác.

### Trường tuỳ chọn `xac_minh_email` (mã xác minh qua email, NV122)

Module đăng nhập muốn dùng nút tích «Lấy mã từ hộp thư» khai trong `manifest.json`:

```json
"xac_minh_email": {"tu_mien": ["example.com"], "do_dai": [6, 6]}
```

- `tu_mien`: danh sách tên miền không trùng (mỗi tên ít nhất hai nhãn); `do_dai`: `[min, max]` số nguyên, `1 <= min <= max <= 32`.
- Đây là **nguồn sự thật duy nhất** cho `tu_mien`/`do_dai` mà lõi truyền vào module đọc hộp thư — lõi KHÔNG bao giờ lấy hai giá trị này từ yêu cầu HTTP (nếu không đường này thành công cụ đọc thư tuỳ ý).
- Sai khuôn → `app/kham_pha.py` bỏ trường + `log.warning`, module vẫn nạp bình thường.
- Module còn phải trả `KetQuaDangNhap(False, ma_loi="can_ma_email")` khi trang đòi mã gửi qua email, và cài `async def nhap_ma_xac_minh(self, ho_so_hoac_phien, ma: BiMatTam, trinh_duyet=None) -> KetQuaDangNhap` để lõi điền mã (mã bọc trong `BiMatTam`, xoá ngay sau khi điền).
- Lõi: cờ tích BẬT + module khai `xac_minh_email` → đọc `sau_luc` lúc bắt đầu bước đăng nhập, gọi `lay_ma_xac_minh`, truyền mã cho `nhap_ma_xac_minh`; thành công → lưu chứng chỉ bằng `_luu_chung_chi_vao_module`. Không lấy được mã → `ma_loi` rõ (`khong_co_hop_thu`/`het_gio`/…) để giao diện báo «Không đọc được mã từ hộp thư — nhập tay hoặc dùng ứng dụng xác thực». Cờ TẮT: hành vi hiện có (chờ mã 2FA / khoá TOTP / cần người), KHÔNG bao giờ tự chuyển sang đọc hộp thư.
- Nhật ký kênh `ma_xac_minh` chỉ ghi module, hộp thư BĂM rút gọn, kết quả (`ok`/mã lỗi), độ dài chờ — không mã, không tiêu đề thư.

---

*Xem thêm: [Chuẩn Bảo Mật cho Module](CHUAN-BAO-MAT-MODULE.md) (luật bảo mật: hạng module, khai báo manifest, dịch vụ lõi dùng chung, test bắt buộc).*

## 10. Công cụ cho tác giả module (`scripts/kiem_module.py`)

Trước khi chia sẻ module (fork GitHub riêng — chủ kho không duyệt), tác giả tự soát bằng:

```bash
python3 -m scripts.kiem_module modules/<id>        # văn bản tiếng Việt
python3 -m scripts.kiem_module modules/<id> --json  # dữ kiện có cấu trúc
```

Công cụ **không chạy mã module**: kiểm `manifest.json` bằng đúng bộ kiểm của lõi (`kiem_manifest` trong
`app/kham_pha.py`) rồi quét tĩnh cây cú pháp (`ast`) các tệp `.py` để liệt kê import, lời gọi mạng và tên
miền/URL cố định, mở tệp, tiến trình con, `exec`/`eval`/`pickle`, đọc biến môi trường, `verify=False`; đối
chiếu với quyền đã khai (`tai_nguyen.mang_ra`, `yeu_cau_goi`) và báo mọi chỗ làm nhiều hơn khai; cảnh báo chuỗi
giống bí mật nhúng cứng. Dạng `--json` là «bản rút gọn do mã trích» để đưa cho AI đọc ở bước sau — mọi chuỗi lấy
từ mã đều bị cắt ngắn và làm sạch vì nội dung mã là dữ liệu không tin được.

Mã thoát: `0` = đạt; `1` = lỗi manifest hoặc vi phạm; `2` = không đọc được.

---

## 11. Chuẩn Sổ thiết bị mạng và báo máy lạ (NV107)

Lõi web2api cung cấp tính năng quản lý sổ thiết bị mạng bền vững và cảnh báo máy lạ tự động mà không phụ thuộc vào bất kỳ nhà cung cấp phần cứng cụ thể nào:

### 11.1. Nguồn dữ liệu từ module
- Bất kỳ module nào khai báo khả năng `"thiet_bi"` và trả về thực thể có thuộc tính `thuoc_tinh.mac` hợp lệ đều được coi là nguồn dữ liệu mạng.
- Module nguồn cần cung cấp:
  - `thuoc_tinh.mac`: Địa chỉ MAC (dạng bất kỳ).
  - `thuoc_tinh.ip`: Địa chỉ IP (tuỳ chọn).
  - `thuoc_tinh.ten_may_tu_khai`: Tên máy tự khai qua DHCP/mDNS (tuỳ chọn, dữ liệu không tin).
- Sổ chỉ thêm/cập nhật khi thấy máy; một nguồn lỗi hoặc trả rỗng không làm mất máy cũ và không biến máy cũ thành mới khi nguồn hoạt động trở lại.

### 11.2. Chuẩn hoá MAC và nhận diện MAC ngẫu nhiên
- Lõi tự động chuẩn hoá mọi định dạng MAC về một định dạng chuẩn duy nhất `aa:bb:cc:dd:ee:ff`.
- Tự động lọc bỏ các địa chỉ sai khuôn, toàn 0 (`00:00:00:00:00:00`), broadcast (`ff:ff:ff:ff:ff:ff`), và multicast (bit 0 của octet đầu tiên = 1).
- Tự động nhận diện MAC ngẫu nhiên / riêng tư của điện thoại (bit 0x02 của octet đầu bật).

### 11.3. Báo máy lạ qua chuẩn `kenh_ra`
- Báo máy lạ được gửi qua bất kỳ module nào có khả năng `"kenh_ra"` thông qua hàm gửi chuẩn của lõi (`app/gui_ra.py`).
- Cấu hình qua `PUT /api/so-thiet-bi/bao`:
  - `bat`: bật/tắt báo.
  - `dich`: danh sách `[{"module": "...", "kenh_id": "..."}]`.
  - `bao_mac_ngau_nhien`: `"gop"` (mặc định — gom vào 1 tin tóm tắt), `"co"` (báo như máy thường), `"khong"` (chỉ ghi sổ, không gửi tin).
- Cơ chế theo dõi từng (máy, đích):
  - Đích nào gửi thành công thì không gửi lại.
  - Đích nào lỗi thì chỉ thử lại đích đó ở các chu kỳ sau (tối đa 5 lần).
  - Chống bão: máy mới xuất hiện đột biến (> max(5, nửa số máy thấy được)) sẽ gửi 1 tin cảnh báo và tạm dừng báo cho tới khi quản trị gọi tiếp tục.

---

## 12. Chuẩn Tin vào, phân quyền người gửi và lệnh (NV115)

Lõi web2api cung cấp cổng nhận tin chung (`app/kenh_vao.py`) cho mọi module kênh nhắn tin (Telegram, Zalo Bot...), phân quyền chặt chẽ theo người gửi và cho phép chủ máy ra lệnh trực tiếp trong chat:

### 12.1. Hợp đồng tin vào (`nhan_tin`)
Module đẩy tin vào qua `await kenh_vao.nhan_tin(app, module_id, tin) -> dict` với cấu trúc:
```python
tin = {
    "id_tin": str,            # Định danh tin do nhà cung cấp cấp
    "nguoi_gui": str,         # ID người gửi (^[0-9A-Za-z._:@-]{1,64}$)
    "noi_gui": str,           # ID đích gửi trả lời (phải thuộc kenh_ra() của module)
    "la_nhom": bool,          # Có phải tin trong nhóm/kênh đông người
    "ten_tu_khai": str | None,# Tên người gửi do nền tảng báo (dữ liệu không tin cậy)
    "van_ban": str,           # Nội dung tin (<= 4000 ký tự, lọc ký tự điều khiển)
    "luc": str | None,        # Thời điểm ISO
}
```
- **Chống phát lại**: Lõi tự động nhớ `(module, id_tin)` trong 10 phút (tối đa 10.000 id); tin trùng bị bỏ qua an toàn.
- **Hạn dòng**: Mặc định 20 lệnh/phút theo `(module, nguoi_gui)` và 200 tin/phút theo `module`.

### 12.2. Phân quyền theo NGƯỜI GỬI (chống lỗ hổng c2a)
- Quyền hạn gắn liền với `nguoi_gui`, **tuyệt đối không theo nơi gửi (`noi_gui`)**: Trong nhóm chat chung của quản trị viên, thành viên chưa được cấp quyền riêng vẫn được coi là `la` (người lạ).
- Thứ bậc vai trò: `quan_tri` (3) > `nguoi_dung` (2) > `chi_xem` (1). Mã vai lạ có thứ bậc 0 (như `la`).
- **Người lạ (`la`)**: Không được thực thi lệnh, không nhận phản hồi (tránh kẻ lạ dò bot). Lõi tự động ghi nhận vào sổ «người lạ gần đây» (trần 200 người) và gửi 1 tin cảnh báo duy nhất tới đích báo của sổ thiết bị.
- Quyền chỉ được cấp qua API quản trị (`/api/kenh-vao/nguoi`). Không có lệnh chat nào có quyền thay đổi vai trò.

### 12.3. Khai báo hỏi vòng (`hoi_vong`) trong manifest
Module cần kéo tin định kỳ (ví dụ Telegram khi không có HTTPS công khai) khai trong `manifest.json`:
```json
"hoi_vong": {
  "chu_ky_giay": 5
}
```
- `chu_ky_giay`: Số nguyên từ 2 đến 300 giây.
- Module cài phương thức: `async def hoi_vong(self) -> list[dict]` (danh sách `tin` đúng chuẩn ở mục 12.1).
- Vòng lặp do lõi duy trì: nghỉ theo chu kỳ, lùi dần khi lỗi (tối đa 5 phút), tự dừng khi gỡ module, không chạy trùng lượt, mỗi module có timeout riêng biệt và lỗi của module này không làm ảnh hưởng module khác.

### 12.3b. Đẩy tin vào qua cửa webhook (`tin_vao`)
Cửa webhook (mục 7) chỉ đưa THÂN JSON cho `module.webhook(yc)` — module không có `app` để gọi `nhan_tin`. Vì vậy `webhook()` trả dict có thêm khoá
`tin_vao`: danh sách tin chuẩn hoá (đúng khuôn 12.1). Lõi, SAU khi cửa đã xác thực (bí mật + chữ ký + chống phát lại + hạn dòng), chuyển từng tin
(tối đa 20 phần tử dạng dict mỗi lần, phần tử khác bị bỏ) vào `kenh_vao.nhan_tin(app, module, tin)` rồi BỎ khoá `tin_vao` khỏi phản hồi 200 —
nội dung tin không bao giờ được phản chiếu ra ngoài. Một tin lỗi chỉ ghi log mã ngắn, không làm hỏng webhook (nhà cung cấp gửi lại nếu không nhận 200).
Bí mật nằm trong đường dẫn `/api/webhook/<module>/<bí mật>` (so sánh thời gian hằng); header riêng của nhà cung cấp không tới module, nên không dựa vào nó.

### 12.4. Bảng lệnh và trả lời
- Phân tích câu lệnh theo cấu trúc: bỏ dấu tiếng Việt (kể cả `đ` -> `d`), chuyển chữ thường, bỏ tiền tố `/` và hậu tố `@bot` ở token đầu tiên. Token đầu là tên lệnh, phần còn lại là đối số thô.
- Hàm xử lý: `async def ham(ngu_canh: dict, doi_so: str) -> str`. Trả lời qua hàm gửi chung `gui_den_dich(..., nguoi_goi="kenh_vao")` về đúng `noi_gui`.
- Ngoại lệ trong lệnh luôn được bọc thành `"Lệnh lỗi."` (không lộ chi tiết lỗi hệ thống).
- Sổ thiết bị kết nối sẵn 3 lệnh:
  - `may-la` (vai >= `nguoi_dung`): Liệt kê tối đa 10 máy lạ đang chờ duyệt.
  - `quen <mac>` (vai `quan_tri`): Đánh dấu thiết bị quen.
  - `bo-qua <mac>` (vai `quan_tri`): Bỏ qua thiết bị.



---

## 13. Chuẩn `ma_xac_minh` — Đọc mã xác minh từ hộp thư (NV121)

Nhiều dịch vụ gửi mã xác minh (thường 6 số) về Gmail khi đăng nhập bằng email +
mật khẩu. Chuẩn `ma_xac_minh` để module đọc **CHỈ ĐỌC** hộp thư (IMAP) tìm mã
mới gửi về; lõi (NV122) gọi khi chủ máy tích «Lấy mã từ Gmail» trong form thêm
tài khoản. Mã KHÔNG bao giờ đi qua đường HTTP nào của lõi: module không có route.

### 13.1. Hợp đồng phương thức

- `def hop_thu_ra(self) -> list[dict]`: danh sách hộp thư module đọc được —
  `[{"id": "<email chuẩn hoá>", "email_che": "n***@gmail.com"}]`. KHÔNG chứa
  mật khẩu/token.
- `async def lay_ma(self, yeu_cau: dict) -> dict` với
  `yeu_cau = {"hop_thu": "<email>", "tu_mien": ["openai.com", ...],
  "sau_luc": "<ISO UTC>", "do_dai": [4, 8], "cho_giay": 90}` →
  `{"ok": True, "ma": "123456"}` hoặc `{"ok": False, "ma_loi": "<mã ngắn>"}`
  (mã mở: `khong_co_hop_thu`, `het_gio`, `sai_dang_nhap`, `mang_loi`,
  `khong_thay_ma`, `yeu_cau_sai`…). Hỏi vòng (mỗi ~3 giây, tối đa `cho_giay`,
  kẹp 5..180) cho tới khi thấy thư MỚI hơn `sau_luc`.

### 13.2. An toàn cứng (bắt buộc, có test)

1. **CHỈ ĐỌC**: dùng IMAP `SELECT … readonly` (EXAMINE), `SEARCH`/`FETCH` với
   `BODY.PEEK[]` (không đánh dấu đã đọc); không bao giờ STORE/EXPUNGE/COPY/
   APPEND/DELETE — lớp trộn chặn các phương thức ghi theo cấu trúc.
2. Chỉ xét thư đến **TỪ miền trong `tu_mien`** (so sánh miền của địa chỉ gửi theo
   đúng ranh giới nhãn: `x.openai.com` khớp `openai.com`, `evilopenai.com`
   KHÔNG khớp) và đến **SAU `sau_luc`**, trong hộp thư Đến/«Tất cả thư» — không
   đọc nhãn khác.
3. Trích mã bằng regex chặt: chuỗi số liền dài đúng trong `do_dai`, đứng độc
   lập (không dính số điện thoại/ngày dài hơn); lấy mã của thư **MỚI NHẤT** khớp.
4. KHÔNG lưu nội dung thư, KHÔNG log thư/tiêu đề/người gửi/mã, KHÔNG trả bất cứ
   thứ gì ngoài mã; mã không bao giờ lộ qua đường HTTP nào của lõi (module
   không có route).
5. Kết nối máy chủ thư CỐ ĐỊNH qua SSL có kiểm chứng chỉ (cho phép ghi đè gốc
   CHỈ cho test bằng tham số nội bộ, không đọc từ cấu hình người dùng — chống
   SSRF).
6. Hết giờ kết nối/đọc, trần kích thước thư đọc (256 KB/thư), trần số thư xét
   (20 thư mới nhất).

### 13.3. Cấu hình (khuôn modules/zalo-bot)

Danh sách hộp thư do chủ máy khai: mỗi hộp thư `email` + `mat_khau_ung_dung`
(kieu `danh_sach_bi_mat` — «Mật khẩu ứng dụng» 16 ký tự của Google, bỏ khoảng
trắng). Vì form chung chưa hỗ trợ danh sách có cấu trúc, dùng khuôn
danh-sách-bí-mật + cặp theo thứ tự như modules/telegram-bot và zalo-bot:
`mat_khau_ung_dung` là danh sách bí mật (mỗi dòng một mật khẩu, lõi lưu riêng,
không API nào trả lại) và `email` là chuỗi danh sách CÙNG THỨ TỰ; số lượng phải
khớp nhau. `cai()` KIỂM bằng cách đăng nhập IMAP thử một lần (LOGIN rồi LOGOUT
ngay, không đọc thư) và trả `ok` + `so_hop_thu`; sai → `ok False` + mã ngắn,
không in mật khẩu.

**Khuyến nghị giảm rủi ro** (nói rõ trong README module): dùng một Gmail PHỤ chỉ
để nhận mã (đặt bộ lọc Gmail tự chuyển tiếp các thư mã xác minh sang Gmail phụ)
thay vì Gmail chính, vì mật khẩu ứng dụng cho quyền đọc TOÀN BỘ hộp thư.

## 14. Hợp đồng tự làm mới JWT (NV207, tuỳ chọn)

Module lưu khoá là JWT (vd `chatgpt-web`) có thể khai hợp đồng này để vòng nền
lõi (`app/lam_moi_jwt.py`) tự làm mới token khi gần hết hạn mà không gọi mạng
thừa:

- `async lam_moi_jwt(khoa: str) -> dict`: MỘT lượt làm mới cho một khoá (khoá =
  định danh mờ do module đặt, không phải token). KHÔNG ném — lỗi trả trong dict
  (`{"ok": False, "ma": ..., "can_nguoi": ...}`). Vòng nền gọi, KHÔNG phải người dùng.
- `jwt_trang_thai() -> dict`: trạng thái JWT từng khoá cho giao diện
  (`GET /api/modules/{id}/jwt-trang-thai`, yêu cầu quyền quản trị):
  `{"khoa": [{"khoa", "email", "het_han" (ISO từ claim `exp`), "con_lai_ngay",
  "lam_moi": {"trang_thai", "lan_lam_moi", "ma_loi"}, "canh_bao"}], "nguong_ngay"}`.
  Không chứa bí mật (khoá đã mờ).

Móc nối được bảo vệ (được vòng gọi qua `getattr`, mẫu triển khai ở
`modules/chatgpt-web/lam_moi_jwt.py` + `module.py`):

| Móc | Việc |
|---|---|
| `_tim__tai_khoan_obj(khoa)` | trả đối tượng tài khoản có khoá mờ khớp, hoặc None |
| `_thay_token_jwt(khoa_cu, token_moi)` | thay token BẰNG ĐƯỜNG `cai {giu, them}` trong khoá sổ: đọc-ghép-lưu cấu hình đã lưu bằng `kho.ghep_cau_hinh` + `kho.luu_cai_dat` TRONG KHOÁ MODULE, rồi cập nhật bộ nhớ; không để hai lượt đè nhau |
| `_doc_trang_thai_jwt()` / `_luu_trang_thai_jwt(tt)` | sổ trạng thái làm mới (kho riêng, không bí mật) |
| `_sau_lam_moi_jwt(khoa_moi, email_moi, ho_so)` | làm giàu bản ghi sau khi thay (email từ claim token mới) |

Nguyên tắc bắt buộc:

1. Kiểm hạn bằng GIẢI MÃ claim `exp` (không gọi mạng); chỉ dưới ngưỡng
   (`jwt_nguong_ngay`, mặc định 2 ngày, cấu hình được) mới gọi dịch vụ làm mới
   cho đúng hồ sơ trình duyệt.
2. Ghép khoá → hồ sơ bằng thông tin CHẮC CHẮN (`ho_so` đã lưu còn tồn tại trong
   `GET /v1/profiles` của dịch vụ). KHÔNG đoán chuỗi (vd không khớp email→tên hồ
   sơ); không ghép được → đánh dấu `can_nguoi`, KHÔNG làm mới bừa.
3. Token KHÔNG vào log/phản hồi/nhật ký; nhật ký chỉ ghi «đã làm mới khoá …XXXX»
   (4 ký tự cuối).
4. Trần thử N lần/ngày/khoá + nghỉ luỹ thừa; 401 (hồ sơ đăng xuất) là lỗi VĨNH
   VIỄN → dừng, đánh dấu cần người theo đường NV145b («cần đăng nhập lại»),
   KHÔNG thử lại mãi.
5. URL dịch vụ qua cùng bộ kiểm SSRF; chỉ quản trị gọi/đọc; step-up giữ nguyên
   cho thao tác ghi bí mật.

Vòng nền: `WEB2API_JWT_LAM_MOI_BAT` (mặc định bật), `WEB2API_JWT_LAM_MOI_KHOANG_GIAY`
(mặc định 21600 = 6 giờ, tối thiểu 300). Vòng quét mọi module đang chạy có
`lam_moi_jwt` callable, một lần mỗi khoảng, bắt đầu ngay khi máy chạy.
