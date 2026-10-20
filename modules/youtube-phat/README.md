# Module youtube-phat — máy phát YouTube/Zing

Module điều khiển máy phát YouTube/Zing **qua dịch vụ** `dich-vu/youtube-phat`
(HTTP nội bộ/LAN). Bản thân module không phát nhạc và không gọi ra
youtube.com — dịch vụ mới là nơi gọi ra ngoài.

## Cài đặt (4 bước)

**Bước 1 — Cài và chạy dịch vụ** (đợt A của NV153, worker khác làm song song).
Xem `dich-vu/youtube-phat/README.md`: dựng ảnh, chạy container, mở cổng cho
Home Assistant và loa.

**Bước 2 — Lấy khoá Integration API.** Đọc tệp `integration_token` trong
volume dữ liệu của dịch vụ (`/data/youtube_phat/integration_token`).
Đây là khoá Bearer cho mọi lời gọi tới dịch vụ. Giữ bí mật — module chỉ hiện
dạng che `…abcd`.

**Bước 3 — Cài module.** Vào **Hệ thống → Module**, tìm `youtube-phat`, bấm
**Cài đặt**, nhập:

| Trường | Giá trị |
|---|---|
| Địa chỉ dịch vụ youtube-phat | `http://<địa-chỉ-lan-của-dịch-vụ>:8713` — **bắt buộc, không có mặc định** |
| Khoá Integration API | nội dung tệp `integration_token` ở bước 2 |

Địa chỉ được kiểm chống SSRF bằng bộ kiểm chung của lõi
(`app/tu_xa.py`): LAN nội bộ được chấp nhận, link-local
(169.254.x.x), địa chỉ không định tuyến và scheme lạ bị từ chối.
**Loopback (`127.0.0.1`/`localhost`) BỊ CHẶN** (NV181): lõi chạy trong
container nên loopback trỏ vào chính container lõi, không tới được dịch
vụ — các bản trước có mặc định `http://127.0.0.1:8713` khiến module không
bao giờ cài được. Nếu dịch vụ chạy cùng mạng Docker với lõi, nhập tên
container (ví dụ `http://youtube-phat:8713`).

**Bước 4 — Kiểm tra.** Bấm nút **«Kiểm tra»** trên ô module (gọi
`GET /api/modules/youtube-phat/suc-khoe`): `{"ok": true, ...}` nghĩa là nối
được dịch vụ (`GET /api/integration/health` trả 200). Lỗi hiện dạng
«chưa kết nối được: \<lý do\>» — kiểm tra địa chỉ/khoá và container dịch vụ.

## Trỏ Home Assistant sang dịch vụ mới

Trong cấu hình tích hợp phát nhạc của Home Assistant, đổi **URL gốc** thành
`http://<địa-chỉ-lan-của-dịch-vụ>:8713` (dịch vụ phục vụ ở gốc
`/api/integration/*`, không có tiền tố `/yt` như c2a) và dán **token mới**
(lấy ở bước 2, không phải token cũ của c2a).

## Tab «YouTube phát» (đợt C)

Module khai `tab_web` trong `manifest.json` nên khi đã cài sẽ có tab riêng
**«YouTube phát»** trên thanh trên (phục vụ ở `/m/youtube-phat/`); gỡ module
thì tab biến mất. Tab là HTML tĩnh theo khung trang Hệ thống, một cột,
điện thoại là chính.

**Có:** (1) «Kết nối Home Assistant» — URL dịch vụ + nút chép; token hiện
dạng che `…abcd`, bấm **«Chép token»** mới lấy đầy đủ (một lần, chép ngay,
không lưu ở trình duyệt); (2) tìm YouTube/Zing, mỗi kết quả có nút «Chép mã»
và «Phát lên loa»; (3) phát lên một hay nhiều loa/tivi (danh sách lấy từ
module `home-assistant` qua `GET /api/thiet-bi`, không gọi thẳng HA);
(4) playlist trong kho (chép id) + nhập mã chia sẻ `TTPL1.`; (5) «Đang phát»:
trạng thái thật của dịch vụ, «Dừng hẳn», «Tạm dừng/Tiếp tục loa» (qua thực
thể media_player của module home-assistant), «Bài kế»/«Bài trước» trong hàng
đợi + thanh tua tới giây (đợt D4); (6) «Hàng đợi» của phiên.

Giao diện gọi 10 hành động của module qua đường lõi
`POST /api/thiet-bi/<thực thể>/hanh-dong` (chuẩn C1 — lõi đã có
cơ chế, đợt C không sửa lõi). NV181 tách HAI thực thể theo rủi ro (lõi
tính rủi ro theo thực thể): hành động ĐỌC `tim`, `trang_thai`,
`danh_sach_playlist` đi qua `youtube_phat.xem` (rủi ro `doc`); hành động
điều khiển `phat_len_loa`, `dung_phien`, `nhap_ma_chia_se`, `lay_khoa`,
`bai_ke`, `bai_truoc`, `tua` (đợt D4) đi qua `youtube_phat.dieu_khien`
(rủi ro `ghi`). Mọi tham số
được lõi kiểm theo lược đồ (SEC-004a); vì đợt C thêm `tinh_nang`, lần đầu dùng
quản trị phải **xác nhận thực thể một lần** ở Hệ thống → Thiết bị (SEC-004f) —
giao diện báo rõ khi gặp 409.

**Ẩn (không nút chết), chờ lõi:** «Nghe trên máy này» — CSP của trang module
do lõi đặt (`media-src` về `default-src 'self'`) chặn thẻ `<audio>` tải luồng
từ dịch vụ (khác origin); «Video nhúng» — CSP (`frame-src` về `'self'`) chặn
khung youtube.com. Muốn bật: lõi nới CSP cho trang module hoặc thêm proxy.
**Đợt D4 đã bật «Bài kế»/«Bài trước» + thanh tua** ở mục «Đang phát»: dịch vụ
có `POST /api/integration/next`, `/prev`, `/seek` với khoá tuần tự (bấm liên
tiếp không sinh phiên đôi; «Dừng hẳn» vẫn thắng — phiên đã dừng thì bài kế
báo lỗi, không hồi sinh), thời lượng thật điền sẵn ở `prepare_stream`, và tua
tính byte bắt đầu ngay ở dịch vụ (`GET /api/stream/{token}?t=<giây>`, theo
mục lục `sidx` của tệp MP4 hoặc tỉ lệ thời lượng).

## Bảo mật

- Khoá lưu riêng ở kho bí mật của lõi (quyền 0600 theo trường `bi_mat`);
  không vào log (bộ lọc chặn mọi dòng httpx/httpcore chứa
  `Authorization … Bearer` hoặc khoá đã nạp); lỗi dịch vụ trả về không chứa khoá.
- Mọi nơi chỉ thấy khoá che dạng `…abcd`. Hai đường lấy đầy đủ (đều cần
  phiên quản trị): đọc tệp `integration_token` trong volume dịch vụ, hoặc bấm
  **«Chép token»** trên tab (gọi hành động `lay_khoa` — **chỉ quản trị**, danh
  tính `quan_tri`/`noi_bo` theo định nghĩa `la_quan_tri` của lõi; khoá khách
  giữ phạm vi `thiet_bi:ghi` bị từ chối (trả `ok: False`); khoá
  không vào nhật ký kiểm toán vì lõi chỉ ghi tham số). Vá bảo mật 2026-10-20
  (test: `test/test_nv153_va_laykhoa_log.py`).
- Bộ lọc log giữ filter của MỌI bản module đã nạp (mỗi bản chỉ gắn filter của
  chính mình) — nạp lại module dưới tên gói khác không làm «cướp» filter và
  lọt khoá vào log (mẫu vá nv116 cho telegram-bot).
- Thêm `tab_web` vào manifest là thêm quyền (NV15b): khi cập nhật module, quản
  trị phải duyệt quyền mới một lần (409 `can_duyet_quyen`).

## CHƯA kiểm

- **CHƯA kiểm với dịch vụ thật** (dịch vụ do đợt A viết song song; test ở
  đây dùng máy chủ giả).
- Chưa đo RAM/đĩa thêm của module (module chỉ gọi HTTP nội bộ/LAN).
- Chưa bấm tab trên trình duyệt thật (chỉ test Chrome headless 360px);
  chưa thử với Home Assistant thật và loa thật.
