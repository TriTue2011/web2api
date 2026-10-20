# Module `maytram` — Máy trạm (điều khiển từ xa qua agent)

**Cấp độ rủi ro: CAO.** Module điều khiển được máy tính ở xa (đọc/ghi tệp, tắt máy,
chụp màn hình). Vì vậy mọi thực thể đều `rui_ro_mac_dinh: "nguy_hiem"`: quản trị
gọi phải kèm `xac_nhan: true` (thiếu → `409`), khách gọi thì yêu cầu vào hàng chờ
phê duyệt (`202`). Module mặc định TẮT (trường cấu hình `bat`).

## Mô hình

Agent nhỏ chạy trên máy trạm (máy khác, sau NAT/wifi/4G) **tự kết nối RA** mở kênh
hai chiều tới lõi và ở đó chờ lệnh. Nhờ chiều kết nối đảo, máy trạm không cần mở
cổng nào — không cổng nào mở ra Internet từ phía agent.

**Điểm gắn truyền tải (cần lõi, CHƯA có):** module nhận bất kỳ đối tượng «kenh»
nào có `async gui(obj)` / `async nhan() -> dict` (ví dụ một WebSocket). Lõi sẽ
gọi `QuanLyPhien.chay_kenh(kenh)` cho mỗi kết nối tới. Giao thức bắt tay và khung
được ghi đầy đủ trong `phien_agent.py`; agent GIẢ trong `agent_gia.py` là tài
liệu sống của phía agent. Nói thẳng: **bản này CHƯA dùng được với máy thật** —
mọi kiểm chứng đều qua agent giả trong tiến trình.

**Module tự kiểm vai người gọi:** cả hai thực thể (quản lý + từng máy) chỉ
nhận người gọi `quan_tri`/`noi_bo`, đóng khi thiếu ngữ cảnh — KHÔNG phụ
thuộc mức rủi ro lõi gán cho thực thể (quản trị có hạ rủi ro xuống
«ghi»/«doc» thì khách vẫn bị từ chối ở module).

## Bắt tay hai chiều (HMAC, chống phát lại)

Mỗi máy có MỘT khoá 256 bit riêng (`khoa_agent`), chỉ trả đúng **một lần** lúc
đăng ký/xoay khoá, không đọc lại được ở đâu khác (trong sổ chỉ còn dấu vân
sha256 rút gọn để đối chiếu):

1. agent→lõi: `chao {ten, nonce_agent, thoi_gian}`
2. lõi kiểm: máy tồn tại, đang bật, chưa thu hồi; `thoi_gian` là số HỮU HẠN
   (NaN/vô cực/bool bị từ chối) và `|giờ − thoi_gian| ≤ cửa sổ` (mặc định 120
   giây) — kiểm giờ TRƯỚC nonce nên khung chào sai giờ không đốt được nonce;
   `nonce_agent` chưa dùng (nhớ 10 phút, tối đa 10.000).
3. lõi→agent: `thach_thuc {nonce_loi, xac_thuc_loi}` — **lõi chứng minh BIẾT khoá
   trước**: `xac_thuc_loi = HMAC-SHA256(khoa, "loi:"+nonce_agent+":"+nonce_loi)`.
4. agent→lõi: `dap {xac_thuc_agent}` — **agent chứng minh BIẾT khoá sau**:
   `xac_thuc_agent = HMAC-SHA256(khoa, "agent:"+nonce_agent+":"+nonce_loi)`.
5. lõi **nạp lại sổ trong khoá** ngay trước khi lập phiên: khoá đã bị xoay,
   máy đã bị thu hồi/xoá/tắt TRONG LÚC bắt tay đang dở → bắt tay chết, phiên
   không lập. Phiên giữ dấu vân của khoá đã lập nó; mỗi lệnh module đối chiếu
   lại với khoá hiện hành (khoá xoay là phiên cũ chết hẳn).
6. lõi→agent: `san_sang {duong_dan, quyen}` — khoá-phiên được suy ra bằng HMAC,
   không bao giờ truyền đi.

Mọi khung lệnh/kết quả sau đó mang `mac = HMAC-SHA256(khoá-phiên, nội dung)`:
khung sai MAC bị bỏ lặng. Agent giả mạo không nói được tiếng lõi; lõi giả mạo
không nói được tiếng agent.

## Tập lệnh ĐÓNG

Chỉ các hành động liệt kê trong `tinh_nang` của thực thể, không có «chạy lệnh
tuỳ ý» (c2a có `exec`/`kill` nhưng module này **KHÔNG port** theo spec NV170):

- **Đọc** (không cần quyền riêng): `liet_ke`, `doc_tep`, `thong_ke`, `tim_tep`,
  `thong_tin`, `tai_nguyen`, `tien_trinh`, `dich_vu`.
- **Ghi** (`co_quyen_ghi`): `ghi_tep`, `tao_thu_muc`, `xoa`, `noi_tep`.
- **Nguồn** (`co_quyen_nguon`): `khoa_man_hinh`, `ngu`, `dang_xuat`, `tat_may`,
  `khoi_dong_lai`, `mo_khoa`.
- **Ghi màn hình** (`co_quyen_ghi_man_hinh`): `chup_man_hinh`, `chup_webcam`.

Quyền được kiểm **hai lớp trùng nhau** (như c2a): lõi kiểm trước khi gửi; agent
trên máy cũng tự kiểm allowlist của chính nó và từ chối lệnh lạ. Allowlist rỗng
= không cho gì cả (fail-closed); chặn `..` trước khi so sánh.

## Quản lý máy (thực thể `maytram.dang_ky`)

| Hành động | Ý nghĩa |
|---|---|
| `dang_ky` | Sinh khoá agent (trả MỘT lần). Bắt buộc `duong_dan` (≥1 thư mục). |
| `thu_hoi` | Khoá chết vĩnh viễn, ngắt phiên đang mở; máy khác không ảnh hưởng. |
| `xoa` | Gỡ trên máy TRƯỚC (gửi `go_cai_dat` khi còn online), rồi xoá ở sổ. |
| `xoay_khoa` | Khoá mới (trả MỘT lần); khoá cũ chết, phiên cũ bị ngắt. |
| `doi_quyen` | Đổi nhãn/thư mục/quyền/bật-tắt; có hiệu lực ngay cả với phiên đang mở; `bat=false` NGẮT phiên đang sống ngay. |

Tên máy không được trùng không gian tên của thực thể quản lý (`dang_ky`,
`thu_hoi`, `xoa`, `xoay_khoa`, `doi_quyen`) — đăng ký bằng các tên đó bị từ
chối để không sinh hai thực thể cùng id.

Sổ máy lưu ở `<WEB2API_DATA>/du_lieu/maytram/thiet_bi.json`, quyền `0600`,
ghi nguyên tử qua `app.kho.ghi_bi_mat_tep` (mã hoá khi nghỉ nếu lõi bật).
Khoá không bao giờ vào log/phản hồi/`suc_khoe` — chỉ dấu vân 12 ký tự hex.

## Kết quả là dữ liệu KHÔNG TIN

Mọi kết quả từ agent được cắt ở `tran_dau_ra` (mặc định 20.000 ký tự) tính
theo TỔNG ký tự của toàn cấu trúc (không phải từng chuỗi), cộng trần độ sâu
6 tầng và trần tổng số phần tử; phần bị cắt được đánh dấu `bi_cat`. Kết quả
đóng dấu `nguon_ngoai: true, khong_tin: true`; cấu trúc lạ làm module trả
`ok: false` chứ không ném ngoại lệ. Lõi KHÔNG làm theo chỉ dẫn nằm trong
kết quả này.

## Tận dụng từ c2a (`~/workspace/c2a-nv146` @ `d879b03`, CHỈ ĐỌC)

- `services/device_agents.py` → `phien_agent.py`: phiên theo id, trần lệnh chờ
  (32), trần hết giờ (60s), fail_all khi nối lại, fail-closed allowlist, hai lớp
  kiểm, ba cờ quyền mặc định TẮT. **KHÔNG port**: hello token thô (bị phát lại),
  `call_sync`, `exec`/`kill` (lệnh tuỳ ý — spec cấm).
- `api/devices.py` → đăng ký (tên khuôn `[a-z0-9][a-z0-9_-]{1,30}`), xoay khoá
  ngắt phiên cũ, đổi quyền có hiệu lực ngay, xoá gỡ-trên-máy-trước (thứ tự bắt
  buộc). **KHÔNG port**: đường REST/WebSocket (module không tự mở đường) và
  kiểm token «rỗng/quá ngắn/không khớp» (thay bằng bắt tay HMAC).

## CHƯA kiểm

- Máy trạm thật (toàn bộ kiểm bằng agent GIẢ trong tiến trình).
- Điểm gắn truyền tải trong lõi (kênh WebSocket cho agent) chưa có — hiện agent
  thật chưa có chỗ nối vào; mọi kiểm kết nối qua kênh nối cặp.
