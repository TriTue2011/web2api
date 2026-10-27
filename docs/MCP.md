# Cổng MCP chỉ-kiểm-tra — hướng dẫn cho chủ máy

Cổng này cho một dịch vụ bên ngoài (gọi tắt «khách MCP») tự thao tác kiểm tra xem **đăng nhập tự động** của web2api có chạy được không,
rồi báo lại. Khách MCP gọi VÀO web2api bằng giao thức MCP (Model Context Protocol) qua HTTP «streamable», thân JSON-RPC 2.0.
Mã nằm ở `app/cong_mcp.py` (giao thức, xác thực, giới hạn, kiểm toán), `app/cong_mcp_cong_cu.py` (sáu công cụ, bộ che đầu ra) và
`app/cong_mcp_tu_kiem.py` (bộ tự kiểm). **Mặc định TẮT.** Bảng biến và đường API cũng có ở `docs/THAM-CHIEU.md`.

## 1. Cổng này là gì và KHÔNG làm gì

Sáu công cụ, không hơn:

| Công cụ | Việc làm | Chạm tài khoản thật? |
|---|---|---|
| `tong_quan` | Phiên bản, module đang có/đang chạy, tình trạng kho tài khoản | Không |
| `tai_khoan` | Danh sách tài khoản ĐÃ CHE: id, nhóm, loại, đích dùng, bậc gói, module, trạng thái, lượng dùng, hạn mức, cờ `co_mat_khau`/`co_totp`, cờ «được cho phép đăng nhập qua cổng» | Không |
| `tu_kiem_dang_nhap` | Chạy bộ kiểm LOGIC điều phối đăng nhập của lõi với module và dịch vụ GIẢ trong một tiến trình con riêng (thư mục dữ liệu tạm, không mạng); trả đạt/hỏng từng ca kèm lý do | Không |
| `dang_nhap_bat_dau` | Chạy MỘT lần đăng nhập thật cho một tài khoản chủ máy đã cho phép đích danh | **Có** |
| `dang_nhap_trang_thai` | Trạng thái phiên vừa xin: `dang_chay`, `thanh_cong`, `can_nguoi`, `that_bai` + mã lý do | Không |
| `nhat_ky_gan_day` | Vài dòng nhật ký gần đây của các kênh được phép, đã lọc (chỉ thời điểm, kênh, mức, tóm tắt) | Không |

Cổng KHÔNG có: công cụ ghi/xoá/sửa nào khác, đọc bí mật (mật khẩu, khoá, OTP/TOTP, cookie, JWT), chạy lệnh, đọc tệp, nhận mã 2FA, giải thử thách.
Gặp thử thách (CAPTCHA, xác minh thiết bị, 2FA…) thì trạng thái là **«cần người»** — cổng không cố vượt.

`tu_kiem_dang_nhap` chỉ chứng minh logic điều phối của lõi chạy đúng (phân loại lỗi theo mã, dừng sau hai lần hỏng, dừng ngay khi cần người, xoá
bí mật tạm, không để bí mật lọt ra kết quả…). Nó **không** chứng minh đăng nhập với nhà cung cấp thật được — việc đó là `dang_nhap_bat_dau`.

## 2. Mô hình đe doạ và các lớp phòng thủ

Khách MCP và phía Internet là bên KHÔNG TIN CẬY; dữ liệu nó gửi là đầu vào độc hại; dữ liệu ta trả có thể chứa chuỗi dụ lệnh (prompt injection).

- **Khoá riêng, hạn quyền**: khoá khách có phạm vi đúng `["mcp"]` (sinh ngẫu nhiên, lưu băm SHA-256, so sánh hằng-thời-gian, thu hồi/xoay được). Phạm vi
  `mcp` không khớp khả năng nào của `/v1` hay `/api/*`. Cổng kiểm phạm vi ĐÃ LƯU của khoá (không dựa phạm vi hiệu lực, vì khách tự thu hẹp được bằng
  header `X-Web2api-Scope`). **Khoá quản trị bị từ chối `403`** — đừng đưa nó cho ai.
- **Không nhận cookie phiên**: bắt buộc header `Authorization: Bearer`. Kiểm `Origin` nếu có (mặc định từ chối mọi `Origin`).
- **Đầu ra theo danh sách trắng + che theo hình chuỗi**: `tai_khoan` và `nhat_ky_gan_day` chỉ chép những trường nêu đích danh (trường mới của kho không tự lọt ra);
  mọi chuỗi qua bộ che **theo hình chuỗi**: token/khoá dài ≥ 20 ký tự che HOÀN TOÀN (không chừa ký tự cuối), JWT, `Cookie:`/`Authorization:`/`Bearer`, cặp `tên=giá-trị` dài,
  cặp `tên: giá trị` / JSON `"tên": "giá trị"` mà tên chứa gốc từ bí mật (`password`, `token`, `secret`, `key`, `otp`…), email, địa chỉ IPv4/IPv6, ký tự điều khiển; rồi cắt độ dài, giới hạn độ sâu.
  Bộ che KHÔNG bắt hết: bí mật ngắn không có tên trường nhạy, hạt TOTP base32 trần, đường dẫn, traceback, số điện thoại, email viết dạng lạ (`a@b`, `%40`, `@` toàn chiều rộng) có thể lọt —
  vì vậy lớp chắn thật là danh sách trắng trường và danh sách kênh nhật ký (mặc định chỉ `trinh_duyet,mcp`; `quan_tri` và `cuoc_goi` bị loại cứng trong mã). Mô tả công cụ và mọi kết quả đều nói rõ: đây là
  DỮ LIỆU, không chứa chỉ dẫn.
- **Giới hạn**: tốc độ theo khoá (`WEB2API_MCP_TOC_DO_PHUT`), đồng thời toàn cổng, trần thân 64 KB; sai khoá nhiều lần bị khoá IP bởi bộ đếm **riêng của cổng** (cùng cơ chế với lõi nhưng không miễn peer loopback không có `X-Forwarded-For`;
  thiếu `Authorization` và sai `Origin` cũng được đếm). Yêu cầu bị từ chối KHÔNG ghi từng dòng nhật ký (nhật ký xoay vòng dùng chung, một làn sóng yêu cầu lạ sẽ đẩy mất dòng `quan_tri`): chỉ ghi gộp tối đa một dòng mỗi 60 giây.
- **Đăng nhập thật có hãm**: chỉ tài khoản trong danh sách cho phép (mặc định RỖNG); tối đa MỘT phiên một lúc; nghỉ tối thiểu giữa hai lần bắt đầu; hai lần không
  thành công liên tiếp ở một tài khoản thì cổng từ chối tài khoản đó cho tới khi khởi động lại; trần thời gian một phiên; không đường nhận mã 2FA.
- **Nhật ký kiểm toán**: mỗi lượt gọi một dòng kênh `mcp` (id khoá, phương thức, tên công cụ, kết quả, thời gian; IP ở phần chi tiết chỉ quản trị đọc được). Không ghi khoá thô, tham số hay nội dung kết quả.

## 3. Bật cổng

Đặt biến môi trường của web2api (khởi động lại tiến trình để nhận):

```
WEB2API_MCP_BAT=1
```

Các biến khác (đều có mặc định an toàn; đọc mỗi lượt gọi):

| Biến | Mặc định | Tác dụng |
|---|---|---|
| `WEB2API_MCP_BAT` | không | `1` để bật; khác thì `/mcp` trả `404` trống |
| `WEB2API_MCP_ORIGIN_CHO_PHEP` | rỗng | Danh sách `Origin` được phép (cách nhau dấu phẩy) |
| `WEB2API_MCP_TOC_DO_PHUT` | `30` | Lượt gọi mỗi khoá mỗi 60 giây |
| `WEB2API_MCP_DONG_THOI` | `2` | Yêu cầu công cụ đồng thời toàn cổng |
| `WEB2API_MCP_THAN_TOI_DA_MB` | `0.0625` | Trần thân yêu cầu |
| `WEB2API_MCP_NHAT_KY_KENH` | `trinh_duyet,mcp` | Kênh nhật ký được đọc. `quan_tri` và `cuoc_goi` bị loại cứng dù cấu hình thêm; `he_thong` (có dòng chứa chuỗi do khách điều khiển) và `phien` (định danh module/tài khoản) không nằm trong mặc định |
| `WEB2API_MCP_DANG_NHAP_CHO_PHEP` | rỗng | Tài khoản được đăng nhập thật, dạng `id_tai_khoan@id_module` |
| `WEB2API_MCP_DANG_NHAP_NGHI_GIAY` | `3600` | Nghỉ tối thiểu giữa hai lần bắt đầu đăng nhập thật |
| `WEB2API_MCP_DANG_NHAP_THANH_CONG_NGAY` | `3` | Trần số lần đăng nhập THÀNH CÔNG của một tài khoản trong 24 giờ; vượt → `het_luot_ngay` (mỗi lần thành công ghi đè token/cookie của module và là một lần đăng nhập thật tới nhà cung cấp) |
| `WEB2API_MCP_DANG_NHAP_HONG_TOI_DA` | `2` | Số lần không thành công liên tiếp trước khi chặn tài khoản đó |
| `WEB2API_MCP_DANG_NHAP_HOI_GIAY` | `5` | Chu kỳ hỏi tiến độ lượt nền |
| `WEB2API_MCP_DANG_NHAP_TOI_DA_GIAY` | `300` | Trần thời gian một phiên |
| `WEB2API_MCP_TU_KIEM_NGHI_GIAY` | `10` | Nghỉ giữa hai lượt tự kiểm |
| `WEB2API_MCP_TU_KIEM_TOI_DA_GIAY` | `60` | Hết giờ thì giết tiến trình con tự kiểm |

## 4. Tạo khoá cho khách MCP

Dùng khoá quản trị (một lần, trên máy của bạn), tạo khoá khách chỉ có phạm vi `mcp`:

```bash
curl -s -X POST http://127.0.0.1:3031/api/khoa-khach \
  -H "Authorization: Bearer <khoá quản trị>" -H "Content-Type: application/json" \
  -d '{"nhan":"khach-mcp","pham_vi":["mcp"]}'
```

(hoặc ở trang Khoá khách của giao diện). Khoá (dạng `w2a_…`) hiện **MỘT lần duy nhất** trong phản hồi — chép ngay vào chỗ cất an toàn. Thu hồi: `DELETE /api/khoa-khach/{id}`;
xoay: `POST /api/khoa-khach/{id}/xoay`. Đừng gửi khoá qua kênh không mã hoá; đừng ghi vào tệp trong kho git.

## 5. Cho phép đăng nhập thật cho một tài khoản (tuỳ chọn)

Mặc định không tài khoản nào được phép; `dang_nhap_bat_dau` luôn trả `khong_cho_phep`. Muốn thử đăng nhập thật cho một tài khoản chính chủ:

1. Lấy **id mờ** của tài khoản (dạng `tk-…`) ở trang Tài khoản hoặc `GET /api/tai-khoan/da-luu` (trường `id`) — không dùng email.
2. Xác định **id module thực thi** (nguồn đăng nhập mà bạn vẫn chọn khi bấm «Đăng nhập» trên giao diện).
3. Đặt `WEB2API_MCP_DANG_NHAP_CHO_PHEP=tk-xxxxxxxxxxxxxxxx@id-module` (nhiều mục cách nhau dấu phẩy) rồi khởi động lại.

Nếu tài khoản đó ở chế độ «đăng nhập qua một tài khoản lưu khác» (đăng nhập hộ), lõi sẽ dùng bí mật của tài khoản HỘ: khi đó cổng đòi cả tài khoản hộ cũng nằm trong danh sách, không thì từ chối (`khong_cho_phep`).

Khách MCP chỉ nêu `tai_khoan_id`; module thực thi do danh sách này quyết, khách không chọn được. Cùng một mã lỗi cho «không có» lẫn «không được phép» để khách không dò được id nào tồn tại.
Lưu ý: đây là đăng nhập THẬT vào dịch vụ của nhà cung cấp — có rủi ro bị nhà cung cấp hạn chế tài khoản nếu thử dồn dập. Chỉ bật cho tài khoản bạn chấp nhận rủi ro đó; các hãm (một phiên, nghỉ 1 giờ,
dừng sau hai lần không thành công, tối đa 3 lần thành công mỗi ngày) chỉ giảm rủi ro, không xoá. Bộ đếm nằm trong bộ nhớ: khởi động lại tiến trình (kể cả Watchtower) xoá mốc nghỉ và các trần.

## 6. Trỏ tunnel (việc của chủ máy — cổng không tự mở ra Internet)

Đường `/mcp` nằm **cùng ứng dụng** với `/api/*` (giao diện và API quản trị). Vì vậy **không được** trỏ tunnel vào toàn bộ cổng của web2api. Chỉ cho đi đúng đường `/mcp`.
Ví dụ cloudflared (`config.yml`), đường quản trị và giao diện KHÔNG được công bố:

```yaml
ingress:
  - hostname: mcp.example.com
    path: ^/mcp$
    service: http://127.0.0.1:3031        # cổng nội bộ của web2api trên máy bạn
  - service: http_status:404
```

- **BẮT BUỘC có một lớp chặn ở rìa** (Cloudflare Access với service token, hoặc luật giới hạn tốc độ ở rìa cho đường `/mcp`). Lý do: bộ đếm và khoá IP của web2api nằm trong bộ nhớ tiến trình
  (chạy `--workers N` thì nhân giới hạn lên N lần, khởi động lại xoá sạch) và không ngăn được kẻ lạ gửi hàng loạt yêu cầu tới tận web2api. Khoá `mcp` vẫn là lớp bắt buộc bên trong, không thay cho lớp này.
- **BẮT BUỘC đặt `WEB2API_TRUST_PROXY`** thành CIDR của proxy/cổng cầu nối (cloudflared chạy cùng máy: loopback đã được lõi hiểu và dùng `X-Forwarded-For`; cloudflared là container hoặc đi qua cổng cầu nối Docker:
  đặt địa chỉ cổng cầu nối, xem `docs/TRIEN-KHAI.md`). Không đặt thì mọi khách Internet đi qua cùng một địa chỉ và dùng chung một thùng đếm: kẻ dò khoá làm khoá IP đó, còn khoá hợp lệ chưa từng xác thực
  từ IP này bị 429. **Cách kiểm**: sau khi bật, gọi `/mcp` bằng khoá sai từ bên ngoài rồi xem nhật ký kênh `quan_tri` (khoá IP/cặp ghi `Khoá cặp (IP, khoá) <ip>`) — `<ip>` phải là IP công khai của máy gọi,
  KHÔNG phải `172.x`/`127.0.0.1`/`khong_xac_dinh`. Nếu thấy nhật ký kênh `mcp` cảnh báo «đặt WEB2API_TRUST_PROXY» nghĩa là cổng nhận `X-Forwarded-For` từ peer chưa khai tin cậy.
- Bộ kiểm Host của lõi: nếu đã đặt `WEB2API_PUBLIC_URL` (hoặc địa chỉ công khai qua API) thì yêu cầu mang `Host` khác tên đó bị `421`; hãy đặt đúng tên miền tunnel hoặc để trống
  (chưa đặt thì lõi cho qua và ghi một cảnh báo nhật ký).
- Cổng `/mcp` dùng chung cổng với ứng dụng nên thừa hưởng địa chỉ nghe của nó: theo `docker-compose.yml`, mặc định cổng chỉ công bố ở `127.0.0.1` (`WEB2API_BIND`). Nếu bạn đã đổi `WEB2API_BIND` sang IP LAN/`0.0.0.0` thì khi bật `WEB2API_MCP_BAT=1`, ai trong LAN có khoá `mcp` cũng gọi được `/mcp` (khoá vẫn là lớp bắt buộc).
- Phản hồi 404 thân trống khi cổng TẮT khác với `{"detail":"Not Found"}` của đường lạ, nên kẻ dò đường biết bản này có MCP (đang tắt); `401`/`403` phân biệt được loại khoá sai. Chấp nhận được vì đường không lộ dữ liệu.
- Muốn dừng khẩn cấp: bỏ `WEB2API_MCP_BAT` rồi khởi động lại, hoặc thu hồi khoá khách.

## 7. Cách khách MCP kết nối

- URL: `https://mcp.example.com/mcp` — Streamable HTTP, **không phiên**, mỗi yêu cầu là một `POST` JSON (không có luồng SSE; `GET` trả `405`).
- Header: `Authorization: Bearer <khoá>` và `Content-Type: application/json`.
- Giao thức hỗ trợ: `2025-06-18` (mặc định), `2025-03-26`, `2024-11-05`. Không hỗ trợ batch.

Thử bằng `curl` (thay `<khoá>`; đổi địa chỉ sang `http://127.0.0.1:<cổng>/mcp` để thử cục bộ):

```bash
U=https://mcp.example.com/mcp
H=(-H "Authorization: Bearer <khoá>" -H "Content-Type: application/json")
curl -s -X POST $U "${H[@]}" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"thu","version":"0"}}}'
curl -s -X POST $U "${H[@]}" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'
curl -s -X POST $U "${H[@]}" -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
curl -s -X POST $U "${H[@]}" -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"tong_quan","arguments":{}}}'
```

Kết quả công cụ: `result.content[0].text` (chuỗi JSON) và `result.structuredContent` (cùng đối tượng), `result.isError`. Lỗi nghiệp vụ (`khong_cho_phep`, `dang_ban`, `phai_nghi`,
`da_that_bai_lien_tiep`, `kenh_khong_duoc_phep`…) là `isError: true` kèm `loi` và `thong_diep`.

## 8. Rủi ro còn lại và chỗ chưa kiểm

- **Chưa kiểm với nền tảng bên ngoài thật**: chưa biết dịch vụ của bạn có nối được MCP từ xa (URL + header Bearer) hay không; cổng đã được thử bằng client JSON-RPC tối giản (`curl`) và bằng test, không phải bằng nền tảng thật.
- `tu_kiem_dang_nhap` không chứng minh đăng nhập với nhà cung cấp thật; `dang_nhap_bat_dau` mới chạm thật, và kết quả phụ thuộc module thực thi.
- Chuỗi dụ lệnh trong dữ liệu (nhật ký, nhãn) vẫn đến tay khách MCP dưới dạng DỮ LIỆU đã cắt/che; việc khách có nghe theo hay không nằm ngoài tầm kiểm soát của web2api. Cổng không có công cụ nào đủ quyền để một chỉ dẫn độc hại gây hại ngoài `dang_nhap_bat_dau`, vốn bị hãm bởi danh sách cho phép.
- Bộ che là che theo HÌNH chuỗi (độ dài, lớp ký tự, cấu trúc), không phải danh sách từ: một bí mật có hình lạ (ngắn, toàn chữ thường) có thể không bị che — vì vậy các trường nhạy cảm bị LOẠI khỏi đầu ra theo danh sách trắng chứ không chỉ che.
- Id tài khoản (`tk-…`) là 16 ký tự đầu của băm SHA-256 của email: ai đoán được email có thể kiểm chứng đoán đó khi thấy id. Đây là thông tin duy nhất về tài khoản mà khách MCP thấy ngoài nhóm/loại/trạng thái.
- Bộ đếm hãm (một phiên, nghỉ, hai lần hỏng, trần thành công/ngày) nằm trong bộ nhớ: khởi động lại tiến trình xoá nó; `--workers N` nhân giới hạn lên N lần. Đặt cổng tắt khi không dùng.
- Phiên trình duyệt nền: nếu trần 300 giây huỷ lượt chờ phía web2api, phiên đang chạy trong dịch vụ đăng nhập không tự bị huỷ; tối đa một đến hai phiên tồn dư, không chồng vô hạn (một phiên một lúc).
- `nhat_ky_gan_day` đọc nhật ký lớn (14 MB mất khoảng 1 giây): chấp nhận được với trần 30 lượt/phút và 2 yêu cầu đồng thời.
- Đường `/mcp` cùng cổng với giao diện quản trị: một cấu hình tunnel sai (trỏ cả cổng) sẽ công bố cả quản trị ra Internet — đúng như mọi dịch vụ khác của web2api; giữ rule `path: ^/mcp$`.
