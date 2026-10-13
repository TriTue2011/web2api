# Dịch vụ Trình duyệt Cô lập (noVNC) cho web2api — Chuẩn C6

Tiến trình chạy trong một **container Docker riêng** (chạy cạnh web2api), cung cấp môi trường trình duyệt thực
(Chromium / Firefox) trên màn hình ảo Xvfb với x11vnc và websockify (noVNC), cho phép người dùng đăng nhập tay
(OTP, captcha, passkey) vào các dịch vụ web trên máy chủ không có màn hình (headless server).

Phiên làm việc nằm lại trong một **hồ sơ trình duyệt bền vững** (volume lưu trữ profile) để các lần sau hoặc các
tác vụ tự động dùng lại phiên này mà không phải đăng nhập lại từ đầu.

Dịch vụ được điều khiển qua HTTP nội bộ bởi module `trinh-duyet` (chạy trong web2api); lõi web2api proxy giao diện
noVNC tới cổng tương ứng của hồ sơ.

> **Ranh giới an ninh là CONTAINER, không phải từng hồ sơ.** Các hồ sơ trong cùng container KHÔNG cô lập với nhau
> (xem «Hạn chế còn lại»). Đừng dùng chung một container cho những người không tin nhau.

---

## Biến môi trường

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `TRINH_DUYET_KHOA` | *(bắt buộc)* | Khoá dịch vụ cho header `Authorization: Bearer <khoá>`. **Thiếu, ngắn hơn 24 ký tự hoặc ít hơn 10 ký tự khác nhau thì dịch vụ từ chối khởi động** (nên sinh bằng `openssl rand -hex 32`). So sánh bằng `hmac.compare_digest`. Đọc MỘT lần lúc khởi động rồi xoá khỏi môi trường tiến trình (kể cả `/proc/<pid>/environ`). |
| `TRINH_DUYET_HOST` | `0.0.0.0` | Địa chỉ IP dịch vụ lắng nghe bên trong container. |
| `TRINH_DUYET_PORT` | `8700` | Cổng API điều khiển nội bộ. |
| `TRINH_DUYET_THU_MUC_GOC` | `/hoso` | Thư mục gốc chứa các thư mục profile trình duyệt (gắn volume bền vững). |
| `TRINH_DUYET_CONG_TU` | `6100` | Cổng noVNC bắt đầu của dải cấp phát cho các hồ sơ đang mở. |
| `TRINH_DUYET_CONG_DEN` | `6199` | Cổng noVNC kết thúc của dải cấp phát. Cổng cấp **theo vòng**: cổng vừa giải phóng không bị cấp lại ngay. |
| `TRINH_DUYET_TOI_DA` | `3` | Trần số hồ sơ mở đồng thời (vượt quá trả `429`). Hồ sơ có tiến trình đã chết không giữ chỗ. |
| `TRINH_DUYET_TOI_DA_HO_SO` | `20` | Trần số thư mục hồ sơ **trên đĩa**. Mở một ID **mới** khi đã đủ trần trả `429`; ID đã có vẫn mở lại được. |
| `TRINH_DUYET_IDLE_PHUT` | `120` | Thời gian không thao tác tối đa (phút). Nếu quá thời gian này và không còn ai kết nối WebSocket noVNC thì tự động đóng hồ sơ (`giu_phien=True`). |
| `TRINH_DUYET_TOI_DA_PHUT` | `480` | Hạn **tuyệt đối** (phút) kể từ lúc mở: quá hạn thì hồ sơ tự đóng (`giu_phien=True`) **dù còn kết nối** (chặn kết nối treo giữ hồ sơ sống mãi). |
| `TRINH_DUYET_NOVNC_DIR` | `/usr/share/novnc` | Thư mục chứa giao diện web tĩnh của noVNC. |
| `TRINH_DUYET_HOST_NOI_BO` | `trinh-duyet` | Tên host nội bộ trả về trong trường `dich` để lõi web2api kết nối proxy. |
| `TRINH_DUYET_CHROMIUM_BIN` | `chromium` | Tệp thực thi Chromium trong container. |
| `TRINH_DUYET_FIREFOX_BIN` | `firefox-esr` | Tệp thực thi Firefox ESR trong container. |
| `TRINH_DUYET_XVFB_BIN` | `Xvfb` | Tệp thực thi Xvfb màn hình ảo. |
| `TRINH_DUYET_X11VNC_BIN` | `x11vnc` | Tệp thực thi x11vnc. |
| `TRINH_DUYET_WEBSOCKIFY_BIN` | `websockify` | Tệp thực thi websockify chuyển tiếp WebSocket -> VNC. |

---

## Hợp đồng HTTP

Mọi đường dẫn (trừ `/suc-khoe`) đều yêu cầu tiêu đề `Authorization: Bearer <TRINH_DUYET_KHOA>`. Xác thực chạy ở
tầng ASGI **trước** định tuyến và **trước** phân tích thân: không khoá thì luôn `401`, kể cả thân JSON hỏng hay
đường không tồn tại. `/docs`, `/redoc`, `/openapi.json` đã tắt. Không bao giờ ghi khoá vào log; access log của
uvicorn tắt (dòng truy cập chứa ID hồ sơ).

### 1. `GET /suc-khoe`
Kiểm tra dịch vụ còn sống. Ngoại lệ có chủ đích duy nhất không cần khoá; không đọc hay lộ trạng thái nào.
- **Phản hồi 200:** `{"ok": true}`

### 2. `GET /ho-so`
Lấy danh sách mọi hồ sơ (đang mở và đang đóng trên đĩa). **Không** trả `dich` hay bí mật nào.
- **Phản hồi 200:**
  ```json
  [
    {"ho_so": "0123456789abcdef0123456789abcdef", "loai": "chromium", "trang_thai": "dang_mo", "luc_mo": "2026-10-13T01:00:00+00:00"},
    {"ho_so": "fedcba9876543210fedcba9876543210", "loai": "firefox", "trang_thai": "dong", "luc_mo": null}
  ]
  ```

### 3. `POST /ho-so/mo`
Mở một hồ sơ trình duyệt trên màn hình ảo và khởi động bộ chuyển tiếp noVNC.
- **Thân yêu cầu (JSON):** `{"ho_so": "0123456789abcdef0123456789abcdef", "loai": "chromium"}`
- **Quy tắc:**
  - `ho_so`: Chỉ chấp nhận chuỗi hex 16–64 ký tự (`^[a-f0-9]{16,64}$`), do lõi sinh (không suy ra từ email).
  - `loai`: Chỉ chấp nhận `"chromium"` hoặc `"firefox"`. Loại lạ trả về mã `400`.
  - Mở lại hồ sơ đang mở **và cả bộ tiến trình còn sống**: trả lại `dich` (không mở trùng). Có tiến trình đã chết:
    dọn cả bộ rồi dựng lại.
  - Sau khi dựng, dịch vụ chờ ngắn (1 giây) và kiểm cả bộ tiến trình còn sống; chết ngay thì trả `502`, dọn sạch
    (giết cả nhóm tiến trình, giải phóng cổng/display), không để hồ sơ «ma». Thư mục hồ sơ do **chính** lần mở
    hỏng đó tạo ra cũng bị xoá (không chiếm trần trên đĩa); thư mục đã có từ trước thì giữ nguyên.
  - Vượt `TRINH_DUYET_TOI_DA` hoặc (với ID mới) `TRINH_DUYET_TOI_DA_HO_SO`: `429`.
- **Phản hồi 200:**
  ```json
  {
    "ho_so": "0123456789abcdef0123456789abcdef",
    "loai": "chromium",
    "dich": "http://w2a:<bí-mật-hồ-sơ>@trinh-duyet:6100"
  }
  ```

#### Hợp đồng với lõi: `dich` chứa userinfo — lõi phải giữ nó như BÍ MẬT

- `dich` có dạng `http://w2a:<bí mật>@<host-nội-bộ>:<cổng>`, trong đó bí mật là 256 bit **ngẫu nhiên** (64 ký tự
  hex) sinh mới **mỗi lần dựng** hồ sơ: gọi `mo` lại khi hồ sơ đang mở trả đúng bí mật hiện tại; đóng hồ sơ thì bí
  mật bị huỷ (xoá tệp); mở lại sinh bí mật mới — `dich` cũ lỡ lộ hết giá trị khi hồ sơ đóng. websockify của hồ sơ
  đòi `Authorization: Basic base64("w2a:<bí mật>")` cho **cả** WebSocket `/websockify` **lẫn** trang tĩnh noVNC.
- httpx và thư viện `websockets` tự gửi Basic auth khi URL có userinfo, nên lõi chỉ cần **giữ nguyên** `netloc`
  (gồm userinfo) khi ghép `…/vnc.html` hay `ws://…/websockify`.
- Lõi **không được** ghi `dich` (hay URL ghép từ nó) vào log, nhật ký, thông báo lỗi, phản hồi gửi trình duyệt
  khách; không echo nó ra ngoài. Nếu bộ kiểm URL của lõi từ chối userinfo thì phải tách userinfo ra **trước** khi
  kiểm SSRF và gắn lại (hoặc gửi tiêu đề `Authorization` tường minh) khi gọi đích.
- `dich` chỉ có trong phản hồi `POST /ho-so/mo`; `GET /ho-so` không trả.

### 4. `POST /ho-so/{id}/dong`
Đóng hồ sơ: giết **cả nhóm** tiến trình của từng thành phần (Xvfb, trình duyệt, x11vnc, websockify — SIGTERM, chờ
2 giây, rồi SIGKILL cả nhóm), xoá tệp bí mật websockify, rồi mới giải phóng cổng.
- **Thân yêu cầu (JSON):** `{"giu_phien": true}`
- `giu_phien = true`: Giữ thư mục profile để lần sau mở lại vẫn còn cookie / session.
- `giu_phien = false`: Xoá thư mục profile của hồ sơ — chạy trong luồng riêng (không chặn event loop) và chỉ
  sau khi cả nhóm tiến trình đã chết. Chống thoát thư mục bảo đảm chỉ xoá đúng thư mục hồ sơ con.
- **Phản hồi 200:** `{"ok": true}`

---

## Cách kết nối với web2api

1. **Mạng riêng của dịch vụ:** `docker-compose.yml` tạo mạng **riêng** `web2api-trinh-duyet` (không `external`,
   không dùng chung). Cách DUY NHẤT để nối lõi vào là gắn container web2api vào mạng này:
   ```bash
   docker network connect web2api-trinh-duyet <tên container web2api>
   ```
   **Không gắn dịch vụ nào khác** vào mạng này: trình duyệt chạy trang web tuỳ ý, mọi thứ cùng mạng đều trong tầm
   với của nó.
2. **Không công bố cổng:** compose chỉ `expose` 8700 và 6100–6199 trong mạng Docker; không có `ports`.
3. **Module điều khiển:** Module `trinh-duyet` gọi `http://trinh-duyet:8700` kèm `Authorization: Bearer <TRINH_DUYET_KHOA>`.
4. **Proxy noVNC:** Lõi cấp vé phiên có hạn cho người dùng đã xác thực, rồi proxy HTTP / WebSocket tới `dich`
   (gồm userinfo — xem «Hợp đồng với lõi»).

---

## Mô hình Đe doạ & An toàn

- **Ai nối được vào noVNC?** websockify của mỗi hồ sơ đòi Basic auth bằng bí mật ngẫu nhiên của lần mở đó
  (`--auth-plugin xac_thuc_ws.BasicTuTep --auth-source <tệp> --web-auth`). Container khác cùng mạng, trang web mở
  trong hồ sơ khác, hay ai đoán được cổng đều không nối được nếu không có bí mật — bí mật chỉ đi ra qua `dich` cho
  lõi. Bí mật nằm trong tệp 0600 (`<gốc hồ sơ>/.bi-mat-ws/<id>`, xoá khi đóng), **không** nằm trên dòng lệnh
  (dòng lệnh lộ qua `ps`/`docker top` cho mọi người dùng máy chủ), và được so hằng thời gian (`BasicHTTPAuth` có
  sẵn của websockify so bằng `==` nên không dùng).
- **x11vnc không mật khẩu:** chỉ nghe `127.0.0.1` trong container; lối vào từ ngoài là websockify (có xác thực).
  `-nevershared -dontdisconnect -forever`: một khách tại một thời điểm, khách thứ hai bị **từ chối** (không đá người
  đang dùng); khách thoát thì x11vnc chờ khách kế (tải lại trang vẫn nối lại được). Hệ quả: một kết nối treo chiếm
  chỗ cho tới khi TCP đóng; hạn tuyệt đối `TRINH_DUYET_TOI_DA_PHUT` bảo đảm hồ sơ không bị giữ sống mãi.
- **Chỉ phiên ĐÃ XÁC THỰC giữ hồ sơ sống.** websockify xử lý mỗi kết nối trong một tiến trình con riêng; khi xác
  thực thành công, plugin ghi dấu `<tệp bí mật>.ket-noi/<pid>`. Vòng dọn idle chỉ tính dấu của tiến trình còn sống
  (kèm thời điểm bắt đầu để chống pid bị tái dùng) — kết nối TCP treo chưa xác thực (ví dụ gửi 1 byte rồi giữ
  socket) KHÔNG giữ hồ sơ. Khách hợp lệ quên đóng tab thì bị cắt ở hạn tuyệt đối `TRINH_DUYET_TOI_DA_PHUT`.
- **websockify KHÔNG giới hạn số kết nối và không có hạn chờ đọc yêu cầu** (bản 0.10: chờ byte đầu 3 giây, sau
  đó đọc dòng yêu cầu không hạn; không có tham số số kết nối tối đa). Mỗi kết nối, kể cả chưa xác thực, là một
  tiến trình con: ai **nối được tới cổng** (tức đang ở TRONG mạng riêng `web2api-trinh-duyet`, hoặc mã chạy trong
  container) vẫn làm cạn `pids_limit` được bằng cách mở hàng nghìn kết nối treo. Vì vậy mạng đó **chỉ được gắn
  container lõi**; một tiến trình trong container (renderer bị chiếm) cũng làm được việc này.
- **Tiến trình con và biến môi trường:** chạy bằng mảng đối số (không `shell=True`), môi trường lọc theo danh sách
  cho phép **khớp tên đúng** (`PATH`, `LANG`, `LANGUAGE`, `LC_ALL`, `TZ`, `USER`, `LOGNAME`, `TERM`, `HOME`,
  `XAUTHORITY`, `DISPLAY`) — biến mang bí mật dù tên gì cũng không đi qua.
- **Khoá dịch vụ trong tiến trình API:** đọc một lần vào bộ nhớ rồi ghi đè trong vùng nhớ môi trường gốc và gỡ khỏi
  `os.environ`. Lý do phải ghi đè: `/proc/<pid>/environ` đọc vùng nhớ dựng lúc exec, `os.environ.pop` không xoá
  được (đã đo). Giới hạn: `docker inspect` trên máy chủ vẫn thấy biến (chỉ người có quyền Docker).
- **Mã chỉ đọc:** `/app` (server.py, xac_thuc_ws.py) thuộc root; `appuser` (uid 1000) không sửa được mã.
- **Chống thoát thư mục:** ID hồ sơ khớp `^[a-f0-9]{16,64}$`; đường dẫn sau `resolve()` phải nằm dưới thư mục gốc,
  symlink trỏ ra ngoài bị chặn `403`.
- **Cổng cấp theo vòng:** vé/khách cũ của hồ sơ A không nối nhầm vào hồ sơ B vừa nhận lại cổng của A (và bí mật
  ngẫu nhiên khác nhau giữa hai lần mở chặn thêm một lớp).
- **Chặn `file://`** bằng chính sách trình duyệt cài trong ảnh (`chinh-sach/`): Chromium `URLBlocklist: ["file://*"]`
  tại `/etc/chromium/policies/managed/w2a-chan-file.json`, Firefox ESR `WebsiteFilter.Block: ["file:///*"]` tại
  `/usr/lib/firefox-esr/distribution/policies.json`. **Chưa kiểm với trình duyệt thật** trong ảnh (đường chính sách
  là đường của gói Debian theo hiểu biết, chưa mở thử `file:///` để xác nhận bị chặn).
- **Giới hạn container** (compose): `cap_drop: [ALL]`, `no-new-privileges`, `pids_limit: 2048`, `mem_limit: 2g`.
  **Không** dùng `init: true`: tini sẽ là PID 1 và giữ nguyên khoá trong `/proc/1/environ`. Thay vào đó server.py
  (PID 1) tự thu dọn tiến trình xác sống mồ côi sau `killpg` (sau mỗi lần đóng và mỗi vòng dọn idle 30 giây).
  **Chưa kiểm với Docker thật** rằng Chromium/Xvfb khởi động được dưới `cap_drop: ALL`.

### Hạn chế còn lại (chưa giải quyết — nêu thẳng)

- **Các hồ sơ KHÔNG cô lập với nhau cho người không tin nhau; ranh giới an ninh là container.** Mọi hồ sơ cùng
  uid 1000 và cùng hệ tệp. **Không cần RCE**: người dùng noVNC của hồ sơ A chỉ việc gõ `file:///hoso/<id B>/` (cookie,
  phiên của B) hay `file:///hoso/.bi-mat-ws/<id B>` (bí mật websockify của B) ở thanh địa chỉ. Chặn `file://` bằng
  chính sách (ở trên) chỉ **giảm bớt đường dễ nhất**, chưa phải ranh giới an ninh: hộp thoại chọn tệp của một form
  tải lên (đưa `Cookies` của B lên trang ngoài), lưu tải về vào thư mục hồ sơ khác, hay chính sách không áp được
  vẫn là đường đi. Thêm vào đó Chromium chạy `--no-sandbox`: một lỗi thực thi mã trong renderer (RCE) đọc được mọi
  thứ trên, kết nối được x11vnc không mật khẩu và display Xvfb của hồ sơ khác qua `127.0.0.1` / socket X, đọc bộ
  nhớ môi trường của tiến trình khác cùng uid, và mở hàng loạt kết nối làm cạn `pids_limit`. Việc xoá khoá khỏi
  `/proc/1/environ` chỉ thu hẹp một đường; không biến container thành nhiều vùng cô lập. Người không tin nhau thì
  mỗi người một container.
- **Trình duyệt có đường ra Internet** nên vẫn tới được mạng LAN của máy chủ từ trong container nếu router cho phép.
  Nên chặn đầu ra tới dải riêng tư bằng iptables/bộ lọc của Docker. Gợi ý (CHƯA chạy; thay `br-XXXX` bằng tên cầu
  của mạng `web2api-trinh-duyet`, lấy từ `docker network inspect web2api-trinh-duyet -f '{{.Id}}'` — tên cầu là
  `br-` + 12 ký tự đầu của Id):
  ```bash
  # Chặn kết nối MỚI từ mạng trình duyệt đi tới dải riêng tư, trừ lưu lượng trong chính cầu đó (lõi ↔ trình duyệt).
  for dai in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10; do
    iptables -I DOCKER-USER -i br-XXXX ! -o br-XXXX -d "$dai" -m conntrack --ctstate NEW -j REJECT
  done
  ```
  Giới hạn của gợi ý này: lưu lượng từ container tới **chính máy chủ** đi qua chuỗi `INPUT`, không qua `DOCKER-USER`
  — cần thêm luật `INPUT -i br-XXXX` cho các cổng dịch vụ trên máy chủ; IPv6 cần luật `ip6tables` tương ứng.
- **Đề xuất theo sau:** (1) bật sandbox của Chromium đúng cách (hồ sơ seccomp cho phép `clone`/userns của Chromium,
  hoặc userns không đặc quyền trong container) để bỏ `--no-sandbox`; (2) mỗi hồ sơ một uid riêng (tiến trình trình
  duyệt hạ quyền sang uid của hồ sơ, thư mục hồ sơ thuộc uid đó) hoặc mỗi hồ sơ một container; (3) x11vnc đặt mật
  khẩu (`-rfbauth`) và Xvfb dùng `-auth` để hồ sơ không nối chéo display/VNC của nhau.

---

## Ước lượng Tài nguyên (RAM & CPU)

- **Ước lượng RAM:** *(chưa đo trên môi trường sản xuất thực tế)*
  - Xvfb + x11vnc + websockify: chiếm khoảng 50–100 MB mỗi hồ sơ.
  - Chromium / Firefox: ban đầu khoảng 150–250 MB; khi duyệt các trang đăng nhập nặng hoặc xử lý JavaScript phức tạp có thể tăng lên 400–800 MB mỗi hồ sơ.
  - Với giới hạn mặc định `TRINH_DUYET_TOI_DA = 3`, dung lượng RAM dự kiến cần khoảng 1.5–2.0 GB. File compose đặt `mem_limit: 2g` và `shm_size: 1gb`.
