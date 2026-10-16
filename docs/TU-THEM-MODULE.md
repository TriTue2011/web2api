# Tự thêm module từ GitHub của bạn trong 5 bước

Tài liệu này dành cho **chủ máy không biết lập trình**. Bạn sẽ tự lấy một
module có sẵn, sửa cho hợp với mình, đặt lên GitHub **của bạn**, rồi tự thêm
vào server **của bạn**. Không cần xin phép ai, và module của bạn không ảnh
hưởng tới ai khác.

> **Làm được hôm nay:** thêm nguồn git bằng lệnh API, cài module từ nguồn
> của bạn, xem bản mới, duyệt quyền khi cập nhật, gỡ module.
> **Sắp có (chưa dùng được):** giao diện bấm nút để thêm nguồn, hộp xác nhận
> hiện ra trước khi cài module mới, công cụ `kiem_module` tự quét mã, nút
> «nhờ AI đọc giúp».

---

## 1. Bạn sẽ làm được gì và cái giá phải trả

1. Module là do **bạn tự chịu trách nhiệm**: bạn chọn cài, bạn chịu hậu quả.
2. Mã của module **chạy trong máy chủ của bạn** — tốn điện, mạng, tài nguyên của bạn.
3. Chỉ thêm module **từ người bạn tin**, như chỉ mời người quen vào nhà.
4. Kẻ xấu có thể **giấu mã độc** trong một module nhìn bề ngoài bình thường.
5. Module hạng A chạy **chung tiến trình với lõi**: nếu nó xấu, nó chạm được
   tới những gì lõi chạm tới.

---

## 2. Năm bước

### Bước 1 — Chọn hoặc fork một module mẫu

Vào GitHub, mở kho `TriTue2011/web2api`, bấm nút **Fork** để có bản sao trong
tài khoản của bạn. Trong thư mục `modules/` có sẵn các module mẫu, dễ học
nhất là `modules/gemini-web` và `modules/chatgpt-web` (hai module bản web,
đăng nhập bằng cookie).

Nếu bạn viết module hoàn toàn mới, chỉ cần giữ đúng cấu trúc: một thư mục
`modules/<ten-module>/` chứa hai tệp `manifest.json` và `module.py`.

### Bước 2 — Sửa `manifest.json` (và `module.py` nếu cần)

`manifest.json` là «chứng minh thư» của module — lõi đọc nó để biết module
là ai và được phép làm gì. Các trường quan trọng:

- `id`: tên định danh, chữ thường, ví dụ `"thoi-tiet-cua-toi"`. Mỗi module
  một `id` riêng, không trùng.
- `ten`: tên hiển thị cho người đọc, ví dụ `"Thời tiết của tôi"`.
- `hang`: chỉ nhận `"A"` hoặc `"B"`. Hạng A chạy chung tiến trình với lõi
  (nhanh, nhưng phải tuyệt đối tin mã). Hạng B chạy cách ly riêng. Người mới
  nên để `"A"` **chỉ khi** mã là của chính bạn hoặc người bạn tin.
- `tai_nguyen.mang_ra`: danh sách tên miền module được phép gọi ra, ví dụ
  `["api.thoi-tiet.example"]`. Đây là «giấy phép ra mạng» — ghi thiếu thì
  module không gọi được, ghi thừa thì module đi được chỗ không cần.
- `truong_cau_hinh`: các ô nhập liệu hiện ra khi bạn liên kết module, ví dụ
  ô nhập API key hay cookie. Mỗi ô có `khoa` (tên), `nhan` (nhãn hiển thị),
  `kieu` (kiểu dữ liệu).
- `cung_cap`: khả năng module cung cấp cho lõi, ví dụ `["chat"]` nghĩa là
  module này biết trò chuyện.

`module.py` chứa mã chạy thật. Nếu bạn chỉ đổi tên, đổi địa chỉ web trong
module mẫu thì thường không cần đụng tới nó. Muốn viết sâu hơn, đọc
[CHUAN-MODULE.md](CHUAN-MODULE.md) (dành cho người viết mã).

### Bước 3 — Đẩy lên GitHub của bạn

Kho GitHub của bạn để ở chế độ **Public** (công khai), nhánh `main`, giữ
nguyên cấu trúc thư mục `modules/<ten-module>/`. Mỗi lần bạn sửa gì, cứ đẩy
(push) lên như bình thường — server sẽ tự thấy bản mới sau vài phút.

### Bước 4 — Khai nguồn git của bạn vào server

Hiện bước này làm bằng **lệnh gọi API** (giao diện bấm nút sắp có). Chạy
lệnh sau trên máy của bạn, thay các giá trị giả bằng của thật:

```bash
curl -s -X POST https://203.0.113.10:3031/api/nguon-git/nguon \
  -H "Authorization: Bearer khoa-quan-tri-cua-ban" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "cua-toi",
    "repo": "ten-cua-ban/web2api",
    "nhanh": "main",
    "thu_muc": "modules",
    "tu_dong": false
  }'
```

Giải thích từng ý:

- `https://203.0.113.10:3031` — địa chỉ server **của bạn** (đây là địa chỉ
  ví dụ, thay bằng địa chỉ thật).
- `khoa-quan-tri-cua-ban` — khoá quản trị của bạn. **Không bao giờ** dùng
  khoá của người khác, không dán khoá lên chỗ công khai.
- `repo` — kho GitHub của bạn, dạng `tên-tài-khoản/tên-kho`.
- `tu_dong: false` — server chỉ **báo** «có bản mới», không tự cài. Giữ
  nguyên như vậy, nhất là với nguồn không phải của bạn.

### Bước 5 — Cài, kiểm tra, và cập nhật sau này

1. **Xem có gì mới:** gọi `GET /api/modules/thong-bao` (kèm khoá quản trị)
   để xem server đã thấy module nào từ nguồn của bạn.
2. **Cài:** gọi `POST /api/nguon-git/cai/<id-module>`. Server tải đúng bản
   commit đã thấy, kiểm băm từng tệp rồi mới cài.
3. **Kiểm tra:** gọi `GET /api/modules` — thấy module của bạn trong danh
   sách là xong. Liên kết (nhập API key/cookie nếu module cần) rồi dùng thử.
4. **Cập nhật sau này:** bạn push bản mới lên GitHub, server báo «có bản
   mới». Bấm cài như cũ.

**Vì sao bản đổi mã phải chờ bạn duyệt?** Mỗi lần cập nhật, server so bản
mới với bản cũ. Nếu bản mới **xin thêm quyền** (gọi thêm tên miền mới, thêm
khả năng mới…) thì server **không tự cài**, mà trả về danh sách «quyền
thêm» để bạn xem trước. Bạn đọc thấy hợp lý thì duyệt đúng bản đó bằng lệnh
kèm mã commit; thấy lạ thì bỏ qua, bản cũ vẫn chạy tiếp. Bớt quyền thì cho
qua tự do, không cần duyệt.

---

## 3. Kiểm tra an toàn trước khi cài

Đánh dấu từng dòng trước khi bấm cài, nhất là với module không phải của bạn:

- [ ] Đọc `manifest.json`: xem `tai_nguyen.mang_ra` — module xin kết nối tới
      những tên miền nào? Có tên miền nào bạn không nhận ra không?
- [ ] Mở các tệp `.py`: mã có gọi ra mạng ngoài danh sách trên không? Có đọc
      tệp lung tung trên máy không? Có chạy lệnh hệ điều hành (`subprocess`,
      `os.system`…) không?
- [ ] So mã commit: mã commit server báo phải trùng với commit mới nhất trên
      GitHub của nguồn đó — lệch là có gì đó không đúng.
- [ ] **Không bật `tu_dong` với nguồn của người lạ**: bật nghĩa là họ push
      cái gì là mã đó chạy ngay trong máy bạn, không ai xem trước.
- [ ] Ba dấu hiệu nguy hiểm: (1) module xin kết nối tới tên miền bạn không
      biết; (2) mã đọc bí mật (biến môi trường, khoá, cookie) rồi gửi đi nơi
      khác; (3) kho mới tạo, không có lịch sử, tác giả ẩn danh mà module lại
      xin nhiều quyền.

---

## 4. Khi có sự cố

- **Gỡ module:** gọi `DELETE /api/modules/<id-module>` (kèm khoá quản trị).
  Module dừng chạy ngay, cấu hình đã lưu của nó vẫn còn để bạn cài lại sau.
- **Xoá nguồn git:** gọi `DELETE /api/nguon-git/nguon/cua-toi`. Module đã
  cài **không** mất theo — gỡ module riêng như dòng trên nếu muốn.
- **Thu hồi:** nếu module từng thấy khoá hay cookie của bạn, đổi ngay khoá
  đó ở nơi đã cấp (ví dụ trang quản lý API key của nhà cung cấp). Đừng chỉ
  gỡ module rồi thôi.
- **Khôi phục:** mất quyền quản trị thì dùng công cụ dòng lệnh
  `python3 -m app.khoi_phuc_quan_tri` chạy trực tiếp trên máy chủ; sao lưu
  và khôi phục toàn bộ qua `POST /api/sao-luu`. Chi tiết từng bước có trong
  [docs/TRIEN-KHAI.md](TRIEN-KHAI.md).

---

## 5. Câu hỏi thường gặp

**1. Tôi không biết lập trình thì làm được không?**
Làm được. Fork module mẫu, đổi `ten`, đổi địa chỉ web trong `module.py`
theo mẫu có sẵn — không cần viết mã mới. Phần khó đã có người viết sẵn.

**2. Module của tôi có ảnh hưởng tới người khác không?**
Không. Mỗi người một server, một GitHub riêng. Bạn cài gì thì chỉ máy bạn
chịu. Chủ kho cũng không can thiệp vào module của bạn.

**3. Chủ kho có phải duyệt module của tôi không?**
Không. Bạn tự thêm vào server của bạn, không ai duyệt hộ. Ngược lại, cũng
đừng mong ai chịu trách nhiệm thay bạn.

**4. Dùng kho GitHub riêng tư (private) được không?**
Được — khi khai nguồn thì kèm thêm `token` của bạn. Nhưng kho public đơn
giản hơn và server không phải giữ thêm bí mật nào.

**5. Sao bản cập nhật của tôi không tự chạy?**
Vì bản mới xin thêm quyền so với bản cũ. Đó là cố ý: bạn phải xem danh sách
quyền thêm và duyệt đúng mã commit đó thì bản mới mới chạy.

**6. Quên hoặc mất khoá quản trị thì sao?**
SSH vào máy chủ, chạy công cụ `python3 -m app.khoi_phuc_quan_tri` theo
hướng dẫn trong [docs/TRIEN-KHAI.md](TRIEN-KHAI.md). Cố ý không có cách nào
làm việc này từ xa qua web.

**7. Xoá nguồn git thì module đã cài có mất không?**
Không. Xoá nguồn chỉ dừng việc theo dõi bản mới. Module đã cài vẫn chạy;
muốn bỏ hẳn thì gỡ module riêng.

**8. Nghi module có mã độc thì làm gì?**
Gỡ module ngay, đổi mọi khoá/cookie mà module từng thấy, rồi mở nhật ký
(`GET /api/nhat-ky`) xem nó đã làm gì trong thời gian chạy.
