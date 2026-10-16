# Chuẩn Bảo Mật cho Module của web2api

Tài liệu này là **luật** cho người viết module: module phải khai gì trong manifest, lõi cung cấp dịch vụ an toàn dùng chung nào (module **không tự chế**), và test bắt buộc trước khi vào kho.

Hợp đồng kỹ thuật chung giữa lõi và module nằm ở [CHUAN-MODULE.md](CHUAN-MODULE.md) — đọc tài liệu đó trước; tài liệu này chỉ nói về bảo mật và tham chiếu chéo, không chép lại.

---

## 1. Ranh giới tin cậy

### 1.1. Module chạy trong tiến trình lõi, với quyền lõi

Một module đã nạp chạy **trong** tiến trình của lõi và thấy được mọi thứ lõi thấy: khoá, bí mật, nhật ký, dữ liệu của module khác. Không có biên cách ly nào giữa module và lõi ở tầng tiến trình.

- **Vì sao**: Hiểu sai ranh giới này là nguồn gốc của mọi quyết định sai — module không phải «ứng dụng khách» của lõi mà là một phần của lõi.
- **Cách kiểm**: Đọc mã module, tìm chỗ chạm bí mật lõi (khoá, cookie, tệp cấu hình của module khác); nếu có mà không qua dịch vụ lõi thì vi phạm.

### 1.2. Dịch vụ dùng chung chống đầu vào độc và tác giả cẩu thả — không chống module độc

Mọi dịch vụ bảo mật của lõi (cổng phạm vi, webhook chung, bộ tải URL không tin…) được thiết kế để chống **đầu vào độc từ bên ngoài** (webhook giả, đầu ra model bị đầu độc, văn bản RAG độc, khoá khách bị lộ) và **tác giả module cẩu thả** (quên kiểm, kiểm sai). Chúng **không** chống được module cố tình độc hại — chống module độc là việc của hạng module (mục 1.3) và duyệt mã.

- **Vì sao**: Một mô hình đe doạ rõ ràng giúp người viết module biết mình được bảo vệ khỏi cái gì và phải tự lo cái gì.
- **Cách kiểm**: Với mỗi dịch vụ lõi, hỏi «kẻ tấn công ngồi ở đâu?» — nếu câu trả lời là «chính tác giả module» thì dịch vụ đó không thuộc phạm vi.

### 1.3. Hai hạng module

| Hạng | Điều kiện | Chạy ở đâu | Được chạm gì |
|---|---|---|---|
| **A** | Chủ máy đã **đọc toàn bộ mã**, ghim SHA của bản đã đọc, và tự chịu trách nhiệm | Trong tiến trình lõi | Mọi dịch vụ lõi, qua hợp đồng khả năng |
| **B** | Mã của người khác, chưa/không đọc hết | **Ngoài tiến trình lõi** (dịch vụ riêng, container riêng), nói với lõi bằng hợp đồng HTTP | Chỉ những gì hợp đồng cho phép; **không chạm bí mật lõi** |

- **Vì sao**: Không thể vừa cho mã lạ chạy trong lõi vừa giữ an toàn; phân hạng biến «có tin được module này không» thành quyết định triển khai rõ ràng.
- **Cách kiểm**: Manifest khai `hang`; lõi từ chối nạp hạng B vào trong lõi và từ chối cho hạng B đường đọc bí mật lõi.

### 1.4. Chạy mã, SSH, trình duyệt tự động chỉ hạng B

Các chức năng **chạy mã tuỳ ý**, **điều khiển máy chủ qua SSH**, và **tự động hoá trình duyệt** chỉ được triển khai ở hạng B (ngoài tiến trình). Hạng A không được chứa chúng.

- **Vì sao**: Đây là ba chức năng biến «mã chạy được» thành «máy bị chiếm» nhanh nhất; đặt chúng ngoài tiến trình thì một module độc chỉ chiếm được container của chính nó.
- **Cách kiểm**: Quét mã hạng A tìm `exec`/`eval`/`subprocess`/SSH client/driver trình duyệt — có là vi phạm.

---

## 2. Manifest phải khai gì

Mọi trường mới dưới đây đều là **«quyền»** theo `app/quyen_module.py`: khi module cập nhật mà tăng quyền (thêm trường, mở rộng giá trị), lõi giữ bản cũ chạy tiếp và chờ quản trị duyệt đúng phần thêm (409 `can_duyet_quyen`) — xem mục 5 của [CHUAN-MODULE.md](CHUAN-MODULE.md).

### 2.1. `hang`: `"A"` hoặc `"B"`

- **Vì sao**: Hạng quyết định module được chạy ở đâu và được chạm gì (mục 1.3); không khai thì lõi không biết cách đối xử.
- **Cách kiểm**: `kiem_manifest` từ chối manifest thiếu `hang` hoặc giá trị lạ; cập nhật đổi `B`→`A` là tăng quyền, chờ duyệt.

### 2.2. `cung_cap` kèm nhóm nguy hiểm của từng khả năng

Mỗi khả năng khai trong `cung_cap` phải kèm nhóm nguy hiểm: `doc` (chỉ đọc), `ghi` (đổi trạng thái thông thường), `nguy_hiem` (mở khoá, xoá dữ liệu, chạy mã, gửi ra ngoài…). Nhóm lạ → lõi coi là `nguy_hiem` (đóng khi lỗi).

- **Vì sao**: Nhóm nguy hiểm là đầu vào của hàng chờ phê duyệt và trần tần suất; khai thấp hơn thực tế là cách lén lút qua mặt kiểm soát.
- **Cách kiểm**: Test gọi khả năng với dữ liệu thật, đối chiếu tác dụng phụ với nhóm đã khai; sai nhóm là vi phạm.

### 2.3. `mang_ra`: mẫu tên miền, lõi cưỡng chế

`mang_ra` khai **mẫu tên miền** mà module được gọi ra (ví dụ `*.nha-cung-cap-a.example`). Lõi cưỡng chế: hạng B bị chặn ở **tầng mạng của container** (không tới được tên miền ngoài mẫu dù mã có cố); hạng A chỉ được gọi ra **qua bộ tải URL của lõi** (mục 3.6), không dùng thư viện HTTP trần.

- **Vì sao**: Danh sách cho phép ở tầng mạng là hàng rào cuối cùng khi mã module bị chiếm hoặc bị đầu độc đầu vào.
- **Cách kiểm**: Từ trong container hạng B `curl` một tên miền ngoài mẫu → phải bị từ chối ở tầng mạng; quét mã hạng A tìm `httpx`/`requests` gọi URL từ dữ liệu → vi phạm.

### 2.4. `bi_mat`, `danh_sach_bi_mat` và tham số bí mật trong lược đồ hành động

Hai trường đã có trong chuẩn module. Bổ sung: mọi tham số trong lược đồ hành động mang giá trị bí mật (khoá, token, mật khẩu) phải được đánh dấu bí mật ngay trong lược đồ — lõi che chúng trong nhật ký, phản hồi lỗi và giao diện.

- **Vì sao**: Bí mật lọt vào log là lỗi kinh điển và khó phát hiện sau khi đã lọt.
- **Cách kiểm**: Gọi hành động với tham số bí mật, đọc nhật ký/phản hồi trên đĩa — không được thấy giá trị thô (xem thêm quy tắc 4.2).

### 2.5. `du_lieu`: `{rieng, co_chu, nhan}`

Khai dữ liệu module xử lý: `rieng` (chỉ của chủ máy), `co_chu` (của người khác — tin nhắn, ảnh người khác gửi), `nhan` (nhãn dữ liệu: `pii`, `anh_camera`, …).

- **Vì sao**: Dữ liệu có chủ và dữ liệu nhạy cảm cần luật riêng (mã hoá, không log, không đưa vào RAG chung); không khai thì lõi mặc định xử lý như dữ liệu nhạy cảm nhất — tốn kém và dễ sai.
- **Cách kiểm**: Đối chiếu luồng dữ liệu thật của module với khai báo; thiếu nhãn `pii`/`anh_camera` cho dữ liệu nhạy cảm là vi phạm.

### 2.6. `webhook`: `{bi_mat, tran_byte, id_su_kien}`

Module nhận webhook từ ngoài phải khai: `bi_mat` (khoá ký webhook), `tran_byte` (trần kích thước thân), `id_su_kien` (tên trường chứa id chống phát lại).

- **Vì sao**: Webhook là đường vào không qua xác thực người dùng; không có ký + trần + chống phát lại thì bất kỳ ai cũng gọi được.
- **Cách kiểm**: Gửi webhook không ký/sai ký/trùng id/vượt trần → đều bị từ chối với cùng một mã, không rò rỉ lý do (xem mục 3.2).

### 2.7. `oauth`: `{phat_hanh, pham_vi_xin, mien}`

Module dùng OAuth phải khai: `phat_hanh` (nhà phát hành, ví dụ «nhà cung cấp A»), `pham_vi_xin` (danh sách scope xin), `mien` (tên miền redirect đã đăng ký).

- **Vì sao**: Scope xin thừa và redirect mở là hai cách phổ biến để chiếm tài khoản qua OAuth.
- **Cách kiểm**: Luồng OAuth chỉ xin đúng scope đã khai; redirect tới tên miền khác `mien` → từ chối.

### 2.8. `tai_nguyen` (đã có trong chuẩn module)

Giữ nguyên quy tắc ở [CHUAN-MODULE.md](CHUAN-MODULE.md) mục 5: số tài nguyên chỉ **tăng** mới cần duyệt.

- **Vì sao**: Tài nguyên là quyền gián tiếp — module xin RAM/CPU/disk lớn có thể làm chết máy chủ (DoS tài nguyên).
- **Cách kiểm**: Cập nhật tăng `dia_mb`/`ram_them_mb` → 409 chờ duyệt; giảm → qua tự do.

---

## 3. Dịch vụ lõi dùng chung (module không tự chế)

Trạng thái mỗi dịch vụ: **có** (đã chạy), **đang làm** (một phần chạy), **kế hoạch** (chưa có — xem «Việc còn lại của lõi» ở cuối).

### 3.1. Cổng phạm vi + danh tính người gọi — kế hoạch

Mọi đường vào khả năng (kể cả gọi nội bộ giữa các module) phải đi qua **một hàm gọi duy nhất** của lõi, mang theo **ngữ cảnh gọi**: danh tính người gọi (ai: quản trị/khách/tác nhân nào) và phạm vi cho phép. Thiếu danh tính → từ chối.

- **Vì sao**: Không có danh tính ở điểm gọi thì mọi kiểm tra phạm vi sau đó đều là đoán.
- **Cách kiểm**: Gọi khả năng qua đường vòng (không qua hàm duy nhất) → không tới được module; gọi không danh tính → bị từ chối.

### 3.2. Webhook chung — kế hoạch

Lõi nhận webhook thay module: đọc thân tới **trần byte trước khi phân tích** (chống bom), **so sánh chữ ký hằng thời gian**, **chống phát lại theo id sự kiện**, **hạn dòng theo module** (429 khi vượt), và trả **một mã duy nhất cho mọi lý do sai** (không cho kẻ gửi biết mình sai ở đâu).

- **Vì sao**: Mỗi module tự làm webhook là mỗi module tự tạo một lỗ hổng khác nhau.
- **Cách kiểm**: Gửi webhook sai ký/sai id/trùng id/vượt trần/vượt hạn dòng → cùng một mã, cùng thời gian phản hồi tương đương.

### 3.3. Gửi ra đích danh — kế hoạch

Mọi tin gửi ra ngoài (kênh nhắn tin, email, webhook đi…) phải **nêu rõ kênh** khi có nhiều kênh, tuân **phạm vi riêng cho gửi ra** (khác phạm vi đọc), chịu **trần tin/phút**, và **sổ nhật ký không ghi nội dung tin**.

- **Vì sao**: Kênh gửi ra là đường rò rỉ dữ liệu và đường spam; nội dung tin trong sổ là mỏ vàng cho kẻ đọc được đĩa.
- **Cách kiểm**: Gửi vượt trần → 429; đọc sổ → chỉ thấy «đã gửi tới kênh X lúc Y», không thấy nội dung.

### 3.4. Kho phiên/bí mật mã hoá khi nghỉ + tay cầm kho riêng theo module — kế hoạch

Bí mật và phiên của module được lõi **mã hoá khi nằm trên đĩa**; mỗi module chỉ cầm **tay cầm kho riêng** của mình — không đọc được kho của module khác hay của lõi.

- **Vì sao**: Đĩa bị đọc (sao lưu lọt, máy bị chiếm) không đồng nghĩa bí mật bị lộ; tay cầm riêng biến «module A đọc trộm module B» thành bất khả thi ở tầng lõi.
- **Cách kiểm**: Đọc tệp kho trên đĩa → chỉ thấy dữ liệu mã hoá; module A gọi tay cầm của module B → bị từ chối.

### 3.5. OAuth chung — kế hoạch

Lõi làm OAuth thay module: `state` **một lần** (chống CSRF), **PKCE** cho luồng công khai, redirect **chỉ về địa chỉ đã khai** trong manifest, token **làm mới theo hạn** do lõi giữ.

- **Vì sao**: OAuth tự chế sai một chi tiết là mất tài khoản; làm một lần ở lõi thì mọi module được hưởng.
- **Cách kiểm**: Đổi `redirect_uri`/dùng lại `state`/bỏ PKCE → luồng bị từ chối.

### 3.6. Bộ tải URL không tin — kế hoạch

Bộ tải duy nhất hạng A được dùng: **chặn dải riêng/loopback/link-local**, **kiểm lại tên miền sau mỗi chặng chuyển hướng** (chống redirect sang LAN — SSRF), **trần byte và trần thời gian**. Khác với chính sách URL do **quản trị khai** (được phép chạm LAN — ví dụ thiết bị nhà thông minh), áp cho URL của module theo `mang_ra`.

- **Vì sao**: SSRF qua chuyển hướng là cách chiếm mạng nội bộ phổ biến nhất từ module gọi URL ngoài.
- **Cách kiểm**: URL chuyển hướng sang địa chỉ LAN → bị chặn ở chặng đó; URL vượt trần byte/thời gian → bị cắt.

### 3.7. Bộ kiểm tệp tải lên — kế hoạch

Mọi tệp module nhận từ ngoài đi qua bộ kiểm: **nhận loại theo nội dung** (không tin phần mở rộng/MIME do khách khai), **trần kích thước giải nén** (chống bom zip), **chống zip-slip** (đường dẫn trong archive không thoát được thư mục đích).

- **Vì sao**: Tệp tải lên là đường đưa mã độc vào máy chủ; kiểm ở một chỗ thì không module nào quên.
- **Cách kiểm**: Gửi tệp đội lốt loại khác/zip bom/zip-slip → bị từ chối trước khi module thấy nội dung.

### 3.8. URL ký cho media — kế hoạch

Media phục vụ qua **URL có chữ ký**: HMAC phủ **phương thức + đường dẫn chuẩn hoá + thời hạn**; đường dẫn đi qua `resolve()` + `is_relative_to` (không `../` ra ngoài); hết hạn → từ chối.

- **Vì sao**: URL media không ký là đường đọc trộm ảnh camera/tài liệu của người khác bằng cách đoán đường dẫn.
- **Cách kiểm**: Sửa một ký tự trong URL/đổi phương thức/dùng URL hết hạn/`../` → 403.

### 3.9. Hàng chờ phê duyệt + mức rủi ro + mức tự chủ — đang làm

Mức rủi ro `doc`/`ghi`/`nguy_hiem` đã có ở chuẩn thực thể (thiếu/lạ → `nguy_hiem`, đóng khi lỗi). Còn lại: **hàng chờ phê duyệt** cho hành động `nguy_hiem`, **mức tự chủ** `hoi_lai` (hỏi trước) / `tu_lam` (tự làm, trần tần suất) — và luật sắt: hành động `nguy_hiem` **không bao giờ** `tu_lam`.

- **Vì sao**: Tác nhân tự làm hành động nguy hiểm mà không hỏi là kịch bản mất kiểm soát điển hình.
- **Cách kiểm**: Hành động `nguy_hiem` ở mức `tu_lam` → bị chặn; ở mức `hoi_lai` → vào hàng chờ, chưa duyệt thì không chạy.

### 3.10. Kho riêng có chủ — có

Mỗi module có kho cấu hình/dữ liệu riêng do lõi quản (ghi qua `cai()`, đọc qua `nap()`), tệp 0600, module khác không đọc được.

- **Vì sao**: Không có kho riêng thì module phải tự cất bí mật — và sẽ cất sai.
- **Cách kiểm**: Module A liệt kê/ghi tệp kho của module B → bị từ chối; kiểm quyền tệp kho trên đĩa là 0600.

### 3.11. Sổ kiểm toán do lõi đóng dấu — có

Mọi hành động quan trọng được lõi ghi vào sổ kiểm toán **chuỗi băm** (chống sửa/xoá lén), do **lõi** ghi kênh và danh tính người gọi — module không tự ghi, không sửa được.

- **Vì sao**: Sổ do chính kẻ hành động ghi thì không còn là bằng chứng.
- **Cách kiểm**: Sửa/xoá một dòng sổ → kiểm chuỗi băm phát hiện; dòng sổ thiếu kênh/danh tính → lỗi của lõi.

### 3.12. Trần chi phí — kế hoạch

Mỗi khả năng tốn tiền (gọi model, gửi tin, chạy tác vụ) chịu **trần chi phí** theo module theo ngày/tháng; vượt trần → từ chối cho tới kỳ mới.

- **Vì sao**: Vòng lặp tác nhân bị kẹt có thể đốt hết tiền trong một đêm.
- **Cách kiểm**: Đặt trần thấp, cho tác nhân chạy vòng lặp → tới trần thì dừng với mã rõ ràng.

### 3.13. Chạy cách ly cho mã và trình duyệt — kế hoạch

Hạng B chạy trong container/sandbox riêng: không thấy tiến trình khác, không thấy mạng LAN của máy chủ (trừ danh sách cho phép), hết hạn tự huỷ.

- **Vì sao**: Đây là hàng rào cuối cùng khi mọi lớp trên đều bị qua mặt.
- **Cách kiểm**: Từ trong sandbox `curl` địa chỉ LAN → bị chặn ở tầng mạng; liệt kê tiến trình → chỉ thấy chính mình.

---

## 4. Cam kết của module (test bắt buộc trước khi vào kho)

Mỗi cam kết dưới đây phải có **ít nhất một test** chứng minh module giữ lời — test đỏ khi module vi phạm, xanh khi tuân thủ.

### 4.1. Không dùng thư viện HTTP trần với URL từ dữ liệu

Mọi URL lấy từ dữ liệu ngoài (tin nhắn, kết quả tìm kiếm, trang web, tham số khách) chỉ được tải qua **bộ tải URL của lõi** (mục 3.6); hạng B tuân `mang_ra` ở tầng mạng.

- **Vì sao**: Đây là cửa SSRF — một URL `http://169.254.169.254/...` trong tin nhắn là đủ để đọc bí mật đám mây.
- **Cách kiểm**: Quét mã module tìm `httpx`/`requests`/`urllib` nhận URL từ biến dữ liệu → có là vi phạm; test đưa URL LAN vào dữ liệu → phải bị chặn.

### 4.2. Không log cookie, khoá, bí mật

Không một giá trị bí mật nào được xuất hiện trong `print`, nhật ký, ngoại lệ, hay phản hồi lỗi — kể cả ở dạng «một phần» nếu phần đó đủ để dùng.

- **Vì sao**: Log là nơi bí mật sống lâu nhất và được đọc bởi nhiều người nhất.
- **Cách kiểm**: Chạy luồng có bí mật, đọc toàn bộ log/nhật ký/phản hồi trên đĩa → không được chứa giá trị bí mật (dùng chuỗi giả trong allowlist để test).

### 4.3. ID và đường dẫn do khách đưa phải kiểm trước khi dùng

Mọi `id`, tên tệp, đường dẫn từ khách phải qua kiểm: đúng định dạng, thuộc quyền của người gọi, nằm trong thư mục cho phép (`resolve()` + `is_relative_to`).

- **Vì sao**: ID không kiểm là đường đọc/ghi tài nguyên của người khác (IDOR); đường dẫn không kiểm là đường đọc tệp hệ thống.
- **Cách kiểm**: Test đổi ID sang tài nguyên của người khác → 403/404; test `../` trong đường dẫn → bị từ chối.

### 4.4. Không `getattr`/gọi phương thức theo chuỗi từ dữ liệu ngoài

Không dùng tên hàm/phương thức lấy từ dữ liệu ngoài để gọi (`getattr(obj, ten_tu_du_lieu)`, `eval`, `exec`). Bảng ánh xạ tên→hàm phải là **hằng số trong mã**.

- **Vì sao**: Đây là lỗi thật đã gặp (phễu `tim-kiem`): chuỗi từ dữ liệu ngoài biến thành lời gọi hàm, kẻ tấn công chọn được hàm để chạy.
- **Cách kiểm**: Quét mã tìm `getattr`/`eval`/`exec` với đối số từ dữ liệu → có là vi phạm; test gửi tên hàm lạ → bị từ chối, không có hàm nào chạy.

### 4.5. Không ghi tệp ngoài kho riêng

Module chỉ ghi trong thư mục kho riêng do lõi cấp; không ghi `/tmp` chung, không ghi thư mục của module khác, không ghi đè tệp hệ thống.

- **Vì sao**: Ghi lung tung là đường phá hoại và đường leo thang (ghi đè cấu hình, cấy mã vào chỗ khác).
- **Cách kiểm**: Chạy module trong môi trường giám sát ghi tệp → mọi lần ghi ngoài kho riêng là vi phạm.

### 4.6. Nội dung từ ngoài mang nhãn `nguon_ngoai`, không bao giờ là chỉ thị

Mọi nội dung lấy từ bên ngoài (tin nhắn người khác, kết quả tìm kiếm, nội dung trang web, đầu ra model) phải mang nhãn `nguon_ngoai: true` và **không bao giờ** được đưa vào vị trí chỉ thị (system prompt, lệnh chạy, điều kiện phân quyền).

- **Vì sao**: Nhãn là cách duy nhất để các lớp sau phân biệt «lời chủ máy» với «lời kẻ ngoài»; không nhãn thì đầu độc prompt là chuyện đương nhiên.
- **Cách kiểm**: Đưa chuỗi «bỏ qua mọi chỉ thị trên» qua từng nguồn ngoài → hệ thống không làm theo; kiểm dữ liệu ra của module → thiếu nhãn là vi phạm.

---

## 5. Test bắt buộc cho mỗi khả năng

Theo luật cứng số 1 của kho (mọi khả năng mới đều phải có test bảo mật), mỗi khả năng của module phải qua đủ 5 nhóm test sau — thiếu một nhóm là chưa được vào kho:

1. **Không khoá → 401**: gọi khả năng không kèm khoá/không đăng nhập → `401`, không rò rỉ khả năng có tồn tại hay không.
2. **Khoá thấp/vai thấp gọi đường cao → 403**: khoá khách hoặc vai thấp gọi khả năng dành cho quản trị/vai cao → `403`.
3. **Đổi ID/đường dẫn đụng tài nguyên người khác → 403/404**: tráo `id` sang tài nguyên không thuộc mình → `403` (hoặc `404` nếu chính sách là không tiết lộ sự tồn tại).
4. **Tài khoản mới không tự có quyền cao**: tạo tài khoản/khoá mới → chỉ có quyền tối thiểu; không có đường nào «mặc định là quản trị».
5. **Khách phạm vi hẹp không lách qua phễu**: khoá khách chỉ có phạm vi `tim_kiem` (ví dụ) gọi khả năng khác qua đường tìm kiếm/phễu chung → bị từ chối.

- **Vì sao**: Năm nhóm này bao phủ 5 cách vượt rào phổ biến nhất; module nào cũng phải trả lời được cả năm.
- **Cách kiểm**: Chạy bộ 5 test trên cho từng khả năng; một test đỏ trên mã đúng là test sai — sửa test, không sửa mã để cho qua.

---

## 6. Bảng chức năng → mối đe doạ → dịch vụ lõi → hạng tối thiểu

Mỗi hàng ≤ 3 câu. «Dịch vụ lõi» lấy từ mục 3; «hạng tối thiểu» lấy từ mục 1.3.

| Chức năng | Mối đe doạ chính | Dịch vụ lõi bắt buộc | Hạng tối thiểu |
|---|---|---|---|
| Nhà thông minh | Lệnh lạ bật/tắt thiết bị người khác; thiết bị độc gửi trạng thái giả | Cổng phạm vi + danh tính (3.1); hàng chờ phê duyệt cho `nguy_hiem` (3.9); sổ kiểm toán (3.11) | A |
| Kênh vào — webhook | Webhook giả mạo kích hoạt hành động; phát lại bản tin cũ; bom thân | Webhook chung (3.2): ký hằng thời gian, chống phát lại, trần byte, hạn dòng | A |
| Kênh vào — polling | Token polling lộ; vòng lặp polling bị đầu độc nội dung | Kho bí mật mã hoá (3.4); nhãn `nguon_ngoai` cho mọi nội dung poll về (4.6) | A |
| Kênh ra | Rò rỉ dữ liệu qua tin nhắn; spam; nội dung tin lọt vào sổ | Gửi ra đích danh (3.3): nêu kênh, phạm vi riêng, trần tin/phút, sổ không ghi nội dung | A |
| Tác nhân / MCP | Công cụ bị gọi sai ngữ cảnh; chuỗi công cụ vượt quyền; chi phí bùng nổ | Cổng phạm vi + danh tính (3.1); hàng chờ phê duyệt (3.9); trần chi phí (3.12) | A |
| OAuth / phiên / cookie | Scope xin thừa; redirect mở; phiên không hết hạn; CSRF | OAuth chung (3.5): state một lần, PKCE, redirect đã khai; kho phiên mã hoá (3.4) | A |
| Trình duyệt tự động / noVNC | Chiếm trình duyệt → chiếm máy; cookie phiên lộ; URL khởi động độc | Chạy cách ly (3.13) + hạng B bắt buộc; bộ tải URL (3.6) cho URL khởi động; không log cookie (4.2) | **B** |
| Chạy mã | Mã độc chiếm máy chủ, đọc bí mật, tấn công mạng nội bộ | Chạy cách ly (3.13) + hạng B bắt buộc; `mang_ra` cưỡng chế tầng mạng (2.3); trần chi phí (3.12) | **B** |
| Tải lên ảnh / tài liệu | Tệp đội lốt loại; bom giải nén; zip-slip ghi đè tệp hệ thống | Bộ kiểm tệp tải lên (3.7): nhận loại theo nội dung, trần giải nén, chống zip-slip | A |
| Tải URL không tin | SSRF qua chuyển hướng sang LAN; bom byte; treo kết nối | Bộ tải URL không tin (3.6): chặn dải riêng, kiểm lại từng chặng redirect, trần byte + thời gian | A |
| Phục vụ media qua URL | Đoán đường dẫn đọc trộm ảnh camera/tài liệu người khác | URL ký cho media (3.8): HMAC phủ phương thức + đường dẫn chuẩn hoá + hạn | A |
| Tìm kiếm tri thức / RAG | Văn bản RAG đầu độc prompt; kết quả của người khác lọt sang người này | Nhãn `nguon_ngoai` (4.6); cổng phạm vi + danh tính lọc theo người gọi (3.1) | A |
| Bộ nhớ nhiều người dùng / PII / camera | Dữ liệu người này lọt sang người khác; PII vào log/RAG chung | Khai `du_lieu` (2.5) + kho riêng có chủ (3.10); mã hoá khi nghỉ (3.4); không log PII (4.2) | A |
| Tự học / tự động hoá | Học từ dữ liệu độc → hành vi độc; tự động hoá chạy việc nguy hiểm không hỏi | Nguyên liệu lõi: nhãn `nguon_hanh_dong` (`nguoi`/`ai`/`tu_dong`) do lõi đóng dấu, sổ chấm đúng/sai; hàng chờ phê duyệt (3.9) — thuật toán học là việc của module | A |
| Sao lưu dữ liệu riêng của module | Bản sao lưu lọt bí mật; khôi phục ghi đè dữ liệu người khác | Kho riêng có chủ (3.10); bí mật mã hoá khi nghỉ (3.4); khôi phục kiểm chủ sở hữu trước khi ghi | A |

---

## 7. Việc còn lại của lõi

Các dịch vụ ở trạng thái «kế hoạch», xếp theo thứ tự ưu tiên (số 1 làm trước):

1. **Hợp đồng gọi khả năng + ngữ cảnh danh tính** (3.1): một hàm gọi duy nhất cho mọi đường vào, mang danh tính người gọi; thiếu danh tính thì từ chối.
2. **Webhook chung** (3.2): trần thân trước khi phân tích, ký hằng thời gian, chống phát lại theo id sự kiện, hạn dòng theo module, một mã cho mọi lý do sai.
3. **Bus sự kiện thiết bị**: lõi đóng dấu nguồn sự kiện, lọc phát theo danh tính người nghe, kiểm lại khoá trong SSE, trần số người nghe.
4. **Bộ tải URL không tin** (3.6): chặn dải riêng/loopback/link-local, kiểm lại tên miền từng chặng chuyển hướng, trần byte + thời gian.
5. **Cách ly rủi ro cho thực thể mới + lược đồ tham số có min/max + trần tần suất**: thực thể lạ mặc định `nguy_hiem`; tham số hành động khai min/max; trần tần suất theo (người gọi, hành động).
6. **Cổng gửi ra + URL ký** (3.3, 3.8): gửi ra nêu kênh, phạm vi riêng, trần tin/phút; URL media ký HMAC có hạn.
7. **Mã hoá khi nghỉ + kho riêng** (3.4): bí mật mã hoá trên đĩa, tay cầm kho riêng theo module.
8. **Chạy cách ly** (3.13): sandbox/container cho hạng B — chặn LAN ở tầng mạng, hết hạn tự huỷ.

---

*Xem thêm: [Quy chuẩn Module](CHUAN-MODULE.md) (hợp đồng kỹ thuật), `app/quyen_module.py` (cơ chế duyệt quyền).*
