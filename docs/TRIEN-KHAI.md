# Triển khai web2api an toàn sau Cloudflare, cổng mạng và SSH

Tài liệu hướng dẫn từng bước triển khai web2api ra Internet an toàn. Dành cho người vận hành và người dùng phổ thông làm theo được.

## 1. Sơ đồ kiến trúc tổng quan

Mặc định, web2api chỉ lắng nghe nội bộ trên máy chủ hoặc trong mạng Docker. Mọi truy cập từ Internet đi qua lớp bảo vệ của Cloudflare trước khi vào web2api.

```mermaid
flowchart LR
  Internet["Internet (Khách truy cập)"] --> CF["Cloudflare (WAF / TLS / DDoS)"]
  CF --> TUN["Cloudflare Tunnel (cloudflared)<br/>hoặc Proxy ngược"]
  TUN --> W2A["web2api (chỉ nghe nội bộ :8000 / 127.0.0.1:3031)"]
```

---

## 2. Hai kiểu đặt sau Cloudflare

Có hai mô hình triển khai phổ biến khi đưa web2api ra Internet qua Cloudflare.

### Kiểu A: Cloudflare Tunnel (khuyên dùng — không cần mở cổng router)

Cloudflare Tunnel sử dụng dịch vụ `cloudflared` chạy trên cùng máy chủ hoặc trong cùng mạng Docker với web2api.

1. **Nguyên lý hoạt động:**
   - Tiến trình `cloudflared` chủ động tạo kết nối ra ngoài (outbound) tới mạng Cloudflare.
   - Bạn **không cần mở cổng** nào trên router gia đình hay tường lửa máy chủ (không cần mở cổng 80, 443 hay 3031).
   - web2api được cấu hình chỉ nghe trên địa chỉ loopback `127.0.0.1` của máy chủ hoặc chỉ nằm trong mạng nội bộ Docker.

2. **Cổng lắng nghe an toàn:**
   - Trong `docker-compose.yml`, không dùng `ports: - "3031:8000"` (vì Docker sẽ mở cổng ra mọi giao diện mạng `0.0.0.0:3031`).
   - Đổi thành chỉ lắng nghe loopback trên máy chủ:
     ```yaml
     ports:
       - "127.0.0.1:3031:8000"
     ```
   - Hoặc nếu chạy `cloudflared` chung một tệp compose với web2api trong cùng mạng Docker riêng, bạn có thể bỏ hoàn toàn mục `ports:`. `cloudflared` kết nối trực tiếp tới `http://web2api:8000`.

3. **Tác động tới giới hạn sai khoá (`WEB2API_TRUST_PROXY`) — đọc kỹ, đây là chỗ dễ sai nhất:** web2api biết khách là ai nhờ địa chỉ **nguồn** của kết nối, mà địa chỉ này phụ thuộc `cloudflared` nối vào bằng đường nào. Đã đo trên một máy chạy thật (kết nối từ máy chủ vào cổng công bố của Docker, rồi đọc bảng kết nối trong container):

   | `cloudflared` nối vào web2api qua | Container thấy nguồn là |
   |---|---|
   | `127.0.0.1:<cổng>` | địa chỉ **cổng (gateway) của mạng Docker**, KHÔNG phải loopback |
   | địa chỉ **LAN** của máy chủ (`<IP-LAN-máy-chủ>:<cổng>`) | địa chỉ LAN của máy chạy `cloudflared` (nếu nó cùng máy chủ thì chính là LAN của máy chủ; từ máy LAN khác thì là IP máy đó — chưa đo từ máy LAN khác) |

   - **Cách bạn đang dùng (cloudflared nối qua địa chỉ LAN):** khai đúng **địa chỉ LAN của máy chạy `cloudflared`, dạng `/32`**, ví dụ (địa chỉ MINH HOẠ thuộc dải tài liệu, thay bằng IP LAN thật của bạn) `WEB2API_TRUST_PROXY=192.0.2.10/32`. **Không khai cả dải `/24`**: khi đó mọi thiết bị trong nhà (TV, camera, điện thoại…) đều được coi là proxy và có thể tự gõ `X-Forwarded-For` giả để né giới hạn của chính nó hoặc vu cho người khác bị khoá.
   - Nếu `cloudflared` nối qua `127.0.0.1` (cổng công bố ở loopback): khai **địa chỉ cổng mạng Docker** (lấy bằng lệnh dưới), cũng dạng `/32`. Nếu `cloudflared` là container trong cùng mạng compose: khai dải mạng của compose (đổi `.Gateway` thành `.Subnet`).
     ```bash
     docker network inspect <tên-mạng> --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}'
     ```
     (`<tên-mạng>` xem bằng `docker network ls`).
   - **Không khai** → web2api coi nguồn là không tin cậy, bỏ qua `X-Forwarded-For`, dồn mọi khách Internet vào MỘT IP; một người gõ sai khoá 30 lần sẽ chặn tất cả (mục 3).
   - Nguồn là loopback THẬT chỉ xảy ra khi tiến trình `cloudflared` nằm **trong cùng container** với web2api (ít gặp); khi đó web2api tự nhận proxy nếu có `X-Forwarded-For`.
   - Sau khi khai xong, **kiểm tra bằng mục 3** trước khi mở cho người ngoài.

### Kiểu B: Proxy ngược công bố cổng ra Internet

Áp dụng khi bạn sử dụng Nginx, Caddy, Traefik hoặc mở cổng trực tiếp cho Cloudflare trỏ về IP công khai của máy chủ.

1. **Nguyên lý hoạt động:**
   - Bạn mở cổng máy chủ ra Internet (thường là 443 cho HTTPS).
   - Tên miền được bật đám mây màu cam (Proxied) trên Cloudflare DNS.
   - Cloudflare nhận yêu cầu từ khách rồi chuyển tiếp về máy chủ của bạn.

2. **Khai báo dải IP của Cloudflare:**
   - Kết nối tới máy chủ sẽ có địa chỉ nguồn (peer IP) là các địa chỉ của Cloudflare.
   - Bạn **bắt buộc phải khai dải IP của Cloudflare** vào biến môi trường `WEB2API_TRUST_PROXY`.
   - **Lưu ý quan trọng:** Không chép danh sách cố định vào tài liệu vì Cloudflare có thể cập nhật dải IP. Hãy lấy danh sách dải IP chính thức mới nhất tại trang IP của Cloudflare: `https://www.cloudflare.com/ips/`.
   - Cấu hình trong tệp `.env`:
     ```bash
     # Ví dụ MINH HOẠ (dải tài liệu RFC 5737, KHÔNG dùng thật): thay bằng các dải IPv4 và IPv6 chính thức của Cloudflare, cách nhau bằng dấu phẩy
     WEB2API_TRUST_PROXY=192.0.2.0/24,198.51.100.0/24
     ```
   - Ngoài ra, cấu hình tường lửa máy chủ (như ufw hoặc iptables) để **chỉ cho phép** các dải IP của Cloudflare kết nối tới cổng dịch vụ, chặn mọi IP lạ quét trực tiếp vào máy chủ.

---

## 3. Hậu quả khi thiếu WEB2API_TRUST_PROXY và cách kiểm tra

### Hậu quả: Một người gõ sai khoá, cả nhà bị chặn

Lõi web2api tích hợp sẵn cơ chế chống dò đoán khoá (brute-force):
- Mỗi lần khách gửi sai khoá xác thực (HTTP 401), hệ thống đếm số lần sai theo IP của khách.
- Vượt ngưỡng `WEB2API_RL_LOI_XAC_THUC` (mặc định 30 lần trong cửa sổ `WEB2API_RL_CUA_SO_GIAY` 60 giây), IP đó sẽ bị khoá tạm thời trong `WEB2API_RL_KHOA_GIAY` (mặc định 60 giây).
- Mọi yêu cầu từ IP bị khoá sẽ nhận mã lỗi `429 Too Many Requests` kèm tiêu đề `Retry-After`.
- **Lưu ý cấu hình:** Đặt `WEB2API_RL_LOI_XAC_THUC=0` sẽ TẮT toàn bộ cơ chế giới hạn lỗi xác thực (không đếm và không khoá IP ở mọi đường Bearer, đăng nhập hay noVNC) — **tuyệt đối KHÔNG đặt khi máy chủ mở ra Internet** vì sẽ vô hiệu hoá hàng rào chống đoán khoá, dò vé và dò quét mật khẩu.

**Nếu KHÔNG khai báo `WEB2API_TRUST_PROXY`:**
- web2api coi kết nối từ proxy là không tin cậy và bỏ qua tiêu đề `X-Forwarded-For`.
- Mọi yêu cầu của mọi khách hàng đi qua proxy đều bị web2api tính chung vào một địa chỉ IP duy nhất (IP của Cloudflare hoặc proxy).
- **Ví dụ thực tế:** Khách A gửi sai khoá 30 lần. web2api khoá IP của proxy trong 60 giây. Khách B, khách C và chính bạn dù gửi đúng khoá quản trị cũng bị từ chối với mã 429 («chặn cả nhà»).

### Cách kiểm tra cấu hình IP đã đúng chưa

Kiểm tra bằng chính cơ chế ghi nhật ký hệ thống của web2api:

1. Dùng máy khách ngoài Internet gửi một yêu cầu sai khoá qua proxy tới web2api:
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer khoa-sai-thu-nghiem" https://example.com/v1/models
   ```
   Lệnh trả về mã `401`.

2. Kiểm tra nhật ký kênh quản trị (`quan_tri`) của web2api từ máy chủ nội bộ:
   ```bash
   curl -s -H "Authorization: Bearer <khoá-quản-trị>" "http://127.0.0.1:3031/api/nhat-ky?kenh=quan_tri"
   ```
   Hoặc gửi cố ý vượt ngưỡng 30 lần để kích hoạt khoá tạm thời và xem dòng cảnh báo khoá IP:
   - Nếu nhật ký ghi nhận đúng địa chỉ IP công khai của máy khách (ví dụ `198.51.100.10`), hệ thống đã nhận diện IP khách thành công.
   - Nếu nhật ký ghi nhận địa chỉ của Cloudflare hoặc proxy, bạn cần kiểm tra lại giá trị biến `WEB2API_TRUST_PROXY`.

---

## 4. Chỉ cho mạng nhà gọi đường quản trị (`WEB2API_QT_IP_CHO_PHEP`)

Kể cả khi khoá quản trị bị lộ, đường quản trị (`/api/*` qua `can_quan_tri`) vẫn chỉ dùng được từ mạng bạn cho phép:

```bash
WEB2API_QT_IP_CHO_PHEP=192.0.2.0/24
```

(địa chỉ minh hoạ thuộc dải tài liệu RFC 5737 — thay bằng dải LAN thật của nhà bạn, ví dụ dải mà router cấp). Không đặt biến (mặc định) = không hạn chế. Có giá trị: IP hiệu lực ngoài danh sách → `403 ip_not_allowed`, ghi cảnh báo ở kênh nhật ký `quan_tri` (tối đa một dòng mỗi IP mỗi phút), không tính vào bộ đếm sai khoá. Đổi biến có hiệu lực ngay, không cần khởi động lại. Đường `/v1/*` (khoá API của khách) KHÔNG bị ảnh hưởng.

**Hai bẫy phải nhớ:**

1. **Đi qua Cloudflare thì IP khách là IP Internet, không phải IP LAN.** web2api xác định IP khách đúng một cách duy nhất (duyệt `X-Forwarded-For` từ phải sang trái như mục 2), nên admin ở nhà mở tên miền qua Cloudflare sẽ bị tính là IP Internet và bị chặn nếu danh sách chỉ chứa dải LAN. Muốn quản trị từ nhà: mở thẳng địa chỉ LAN của máy chủ (ví dụ `http://192.0.2.10:3031` — địa chỉ minh hoạ, thay bằng IP LAN thật), đừng đi qua tên miền Cloudflare.
2. **Tự khoá mình thì khôi phục bằng loopback trực tiếp.** Kết nối loopback trực tiếp không kèm `X-Forwarded-For` luôn được phép, kể cả khi danh sách đặt sai hết (tất cả CIDR hỏng → chặn mọi IP khác, trừ loopback). Container không có `curl` nên dùng `python3`, chạy ngay trên máy chủ:
   ```bash
   docker exec <tên-container> python3 -c "import urllib.request as u; print(u.urlopen(u.Request('http://127.0.0.1:8000/api/nhat-ky?kenh=quan_tri', headers={'Authorization': 'Bearer <khoá-quản-trị>'})).read().decode())"
   ```
   Sửa lại `.env` rồi `docker compose up -d` (hoặc bỏ biến) là hết khoá.

---

## 5. HSTS và TLS (HTTPS)

- **Nguyên tắc:** Việc mã hoá TLS/HTTPS và thiết lập HSTS (`Strict-Transport-Security`) được thực hiện tại Cloudflare hoặc reverse proxy, **không làm ở web2api**.
- **Lý do:** Lõi web2api (`app/tieu_de_bao_mat.py`) chỉ gắn các tiêu đề bảo mật cơ bản (`x-content-type-options: nosniff`, `x-frame-options: DENY`, `referrer-policy: no-referrer`) và chủ động không thêm HSTS toàn cục. Điều này giúp người vận hành không bị khoá cứng vào giao thức HTTPS khi cần truy cập nội bộ bằng HTTP qua mạng LAN.
- **Cấu hình trên Cloudflare:**
  - Bật chế độ SSL/TLS là **Full** hoặc **Full (strict)**.
  - Bật tính năng **Always Use HTTPS** để tự động chuyển hướng từ cổng 80 sang 443.
  - Bật **HTTP Strict Transport Security (HSTS)** trong mục SSL/TLS → Edge Certificates của Cloudflare nếu tên miền chỉ phục vụ HTTPS.
- **Xác thực:** Tuyệt đối không bao giờ tắt xác thực. Mọi đường `/v1/*` và toàn bộ các đường API quản trị `/api/*` (bao gồm cả `/api/he-thong`, `/api/modules`, `/api/modules/catalog`, `/api/tinh-nang`) đều yêu cầu xác thực bằng khoá quản trị (Bearer) hoặc phiên đăng nhập của quản trị viên. **Ngoại lệ có chủ đích** chỉ còn: `/health`, `/`, `/m/<id>/…` (tệp tĩnh giao diện module), các trang `/novnc/…` (đổi vé một lần), `GET/POST /api/thiet-lap` (thiết lập tài khoản admin lần đầu) và `POST /api/dang-nhap` (đăng nhập giao diện bằng mật khẩu). Danh sách đầy đủ và lý do nằm ở đầu `test/test_web2api_ma_tran_quyen.py` (`BANG_NGOAI_LE_MO`); đường mở mới chưa ghi vào đó sẽ làm test đỏ.

---

## 6. Bảo mật SSH và cổng mạng

Máy chủ đặt dịch vụ web2api cần được khoá chặt các cổng quản trị:

1. **Chỉ dùng tệp khoá SSH (SSH Key):**
   - Tạo cặp khoá SSH trên máy cá nhân và đưa khoá công khai lên máy chủ.
   - Tắt hoàn toàn đăng nhập bằng mật khẩu trong tệp cấu hình SSH (`/etc/ssh/sshd_config` hoặc `/etc/ssh/sshd_config.d/`):
     ```text
     PasswordAuthentication no
     PubkeyAuthentication yes
     KbdInteractiveAuthentication no
     ```
   - Khởi động lại dịch vụ SSH sau khi đổi cấu hình: `sudo systemctl restart ssh`.

2. **Không công bố cổng SSH ra Internet:**
   - Không mở cổng 22 trên router hay tường lửa công cộng.
   - Khi cần quản trị từ xa, truy cập SSH thông qua Cloudflare Access (Cloudflare Tunnel hỗ trợ bọc SSH) hoặc VPN nội bộ (như WireGuard, Tailscale).

3. **Tắt UPnP trên Router:**
   - Tính năng UPnP (Universal Plug and Play) trên router có thể cho phép các phần mềm hoặc thiết bị tự động mở cổng ra Internet mà bạn không biết.
   - Hãy truy cập trang quản trị router gia đình/văn phòng và tắt hoàn toàn tính năng UPnP.

---

## 7. Danh sách kiểm trước khi mở ra Internet

Trước khi mở tên miền cho người ngoài truy cập, hãy kiểm tra từng mục sau:

- [ ] **Khoá quản trị đã đổi:** Không dùng khoá mặc định hay ngắn. Đã đặt khoá mạnh ngẫu nhiên trong biến `WEB2API_ADMIN_KEY` tại `.env`, hoặc kiểm tra tệp `/data/admin_key` do hệ thống tự sinh với quyền 0600.
- [ ] **Cổng web2api không công bố toàn cục:** Cổng container chỉ lắng nghe trên `127.0.0.1:3031` hoặc kết nối qua mạng nội bộ Docker với `cloudflared`, không mở `0.0.0.0:3031`.
- [ ] **`WEB2API_TRUST_PROXY` cấu hình đúng và đã thử:** Đã khai báo địa chỉ cổng/dải mạng Docker (kiểu A) hoặc dải IP Cloudflare (kiểu B), rồi làm đúng bước kiểm tra ở mục 3: nhật ký ghi IP công khai của máy thử, không phải địa chỉ nội bộ của Docker hay của Cloudflare.
- [ ] **Bật `WEB2API_TIN_PROXY=1`:** Nhận diện đúng giao thức HTTPS từ tiêu đề `X-Forwarded-Proto` của proxy ngược; bắt buộc để cookie phiên có cờ bảo mật `Secure` và tiền tố `__Host-w2a_phien` (nếu thiếu, cookie bị coi là HTTP thường, dùng tên `w2a_phien` không có cờ `Secure`), đồng thời tạo liên kết kết nối chuẩn xác trên giao diện.
- [ ] **SSH an toàn:** Đã tắt đăng nhập bằng mật khẩu (`PasswordAuthentication no`), chỉ cho phép đăng nhập bằng SSH Key.
- [ ] **Cổng SSH không mở thẳng ra Internet:** Chỉ kết nối SSH qua Tunnel, VPN hoặc mạng nội bộ.
- [ ] **Quản trị chỉ từ mạng nhà:** Đã đặt `WEB2API_QT_IP_CHO_PHEP` bằng dải LAN (mục 4) và đã thử: từ ngoài Internet gọi đường quản trị nhận `403`, từ mạng nhà vẫn vào được.
- [ ] **Trình duyệt từ xa không chạm được mạng LAN:** Đã cài luật bằng
  `sudo ./scripts/chan-lan-trinh-duyet.sh` và kiểm bằng curl từ trong container
  (xem mục 9).
- [ ] **Tắt UPnP:** Đã tắt tính năng UPnP trên router mạng.
- [ ] **Tắt tự cài module ngầm:** Giữ nguyên mặc định `WEB2API_GIT_REPO` chỉ báo module mới, không bật tự động cài (`tu_dong: false`).
- [ ] **Sao lưu định kỳ:** Đã có phương án sao lưu volume dữ liệu `web2api-data` thường xuyên.

---

## 8. Các lỗi hay gặp và cách xử lý

### 1. Bị lỗi 429 Too Many Requests (bị khoá cả nhà)
- **Triệu chứng:** Người dùng dùng đúng khoá nhưng vẫn bị trả về mã HTTP `429` kèm thông báo sai khoá quá nhiều lần.
- **Nguyên nhân:** Thiếu cấu hình `WEB2API_TRUST_PROXY`, khiến hệ thống tính mọi yêu cầu qua Cloudflare vào chung một địa chỉ IP.
- **Cách sửa:** Khai báo dải IP của Cloudflare (lấy từ `https://www.cloudflare.com/ips/`) vào `WEB2API_TRUST_PROXY` trong tệp `.env`, sau đó chạy lại `docker compose up -d`.

### 2. Cloudflare báo lỗi 502 Bad Gateway hoặc 504 Gateway Timeout
- **Triệu chứng:** Trình duyệt hiện màn hình lỗi mã 502 hoặc 504 của Cloudflare khi truy cập tên miền.
- **Nguyên nhân:** Container web2api chưa chạy, bị dừng đột ngột (thiếu RAM, lỗi phân quyền thư mục dữ liệu), hoặc `cloudflared` trỏ sai cổng nội bộ (ví dụ trỏ cổng 80 thay vì 8000 hay 3031).
- **Cách sửa:** Kiểm tra trạng thái container bằng `docker compose ps` và xem log bằng `docker compose logs web2api`. Kiểm tra lại cấu hình ingress của Tunnel để đảm bảo trỏ đúng cổng dịch vụ đang lắng nghe.

### 3. Vòng lặp chuyển hướng vô tận (ERR_TOO_MANY_REDIRECTS)
- **Triệu chứng:** Trình duyệt báo lỗi trang web chuyển hướng quá nhiều lần.
- **Nguyên nhân:** Chế độ mã hoá SSL/TLS trên Cloudflare đang đặt là **Flexible**. Cloudflare gửi yêu cầu HTTP tới máy chủ, trong khi proxy trên máy chủ lại ép chuyển hướng ngược về HTTPS.
- **Cách sửa:** Đổi chế độ SSL/TLS trên trang quản trị Cloudflare thành **Full** hoặc **Full (strict)**.

### 4. Giao diện hiển thị liên kết HTTP thay vì HTTPS hoặc cookie phiên thiếu cờ Secure
- **Triệu chứng:** Hướng dẫn kết nối và địa chỉ mẫu trên giao diện web2api hiển thị dạng `http://example.com` thay vì `https://example.com`, hoặc cookie phiên không có cờ `Secure` và không mang tiền tố `__Host-` (`w2a_phien` thay vì `__Host-w2a_phien`) dù đã chạy qua HTTPS.
- **Nguyên nhân:** web2api chạy sau proxy nhận kết nối nội bộ là HTTP (`request.url.scheme == "http"`). Khi chưa bật `WEB2API_TIN_PROXY=1`, hệ thống không tin tiêu đề `X-Forwarded-Proto: https` từ proxy nên coi yêu cầu là HTTP thường (`la_https(request)` trả về `False`), khiến cookie phiên bị phát hành ở chế độ không `Secure`.
- **Cách sửa:** Thêm biến `WEB2API_TIN_PROXY=1` vào tệp `.env` và khởi động lại container (chỉ bật khi mọi đường vào web2api đều đi qua proxy).
- **Cũng ảnh hưởng đăng nhập noVNC (NV98):** trang đổi vé noVNC kiểm `Origin` của trình duyệt có KHỚP CHÍNH XÁC với địa chỉ của máy chủ (scheme + tên máy + cổng). Chưa bật `WEB2API_TIN_PROXY=1` mà truy cập qua HTTPS công khai thì trình duyệt gửi `https://…` còn máy chủ thấy `http://…` → **403** khi đổi vé. Truy cập thẳng bằng địa chỉ LAN (`http://IP:cổng`) không bị ảnh hưởng.

### 5. Kết nối ra ngoài (Outbound) và biến môi trường HTTP_PROXY / HTTPS_PROXY
- **Triệu chứng / Hành vi:** Khi máy chủ đặt trong môi trường mạng chỉ cho phép ra ngoài qua proxy HTTP/HTTPS (khai báo `HTTP_PROXY`, `HTTPS_PROXY` hoặc `ALL_PROXY`), các kết nối ngược dòng của lõi (chuyển tiếp tới nhà cung cấp AI, tải tarball/thăm dò GitHub) không đi qua proxy đó mà kết nối trực tiếp.
- **Nguyên nhân:** Để chống tấn công SSRF và DNS-rebinding («kiểm lúc đăng ký, dùng lúc kết nối»), web2api sử dụng cơ chế vận chuyển tuỳ biến `TransportGhimIp` ở tầng socket (phân giải và ghim IP ngay trước khi mở kết nối). Cơ chế này tạo socket trực tiếp tới IP đã kiểm và bỏ qua việc tự động nạp cấu hình proxy từ các biến môi trường của thư viện `httpx`.
- **Lưu ý triển khai:** Nếu môi trường triển khai bắt buộc mọi kết nối ra ngoài phải qua proxy, cần mở trực tiếp tuyến mạng (routing / firewall) cho container web2api kết nối ra các đích cần thiết (API GitHub, dịch vụ AI), hoặc thiết lập mạng ở tầng hệ điều hành (chẳng hạn qua VPN / iptables REDIRECT / transparent proxy) thay vì dựa vào biến môi trường `HTTP_PROXY`/`HTTPS_PROXY`.

### 6. Mất mật khẩu quản trị hoặc mất 2FA (khôi phục bằng dòng lệnh trên máy chủ)

- **Triệu chứng / Hành vi:** Không còn tài khoản quản trị nào đăng nhập được — quên mật khẩu, mất thiết bị TOTP, hoặc tài khoản bị tắt khoá.
- **Giải pháp:** Dùng công cụ dòng lệnh `app.khoi_phuc_quan_tri` chạy TRỰC TIẾP trên máy chủ (SSH vào máy chủ, chạy trong thư mục mã nguồn web2api). CỐ Ý không có đường web tương đương cho việc này (đó sẽ là cửa hậu):
  ```bash
  cd /opt/web2api   # thư mục chứa app/

  # Đặt lại mật khẩu admin (đồng thời bật lại tài khoản, tắt 2FA, xoá khoá đăng nhập sai).
  # Không truyền --mat-khau → công cụ sinh mật khẩu ngẫu nhiên và in RA MÀN HÌNH MỘT LẦN.
  python3 -m app.khoi_phuc_quan_tri dat-lai-mat-khau --ten admin

  # Chỉ tắt 2FA mà không đổi mật khẩu (mất thiết bị TOTP):
  python3 -m app.khoi_phuc_quan_tri tat-2fa --ten admin
  ```
- **Yêu cầu và dấu vết:** tiến trình chạy lệnh phải đọc được thư mục dữ liệu `WEB2API_DATA` (không đọc được → lỗi `khong_doc_du_lieu`, không chạm gì); mỗi lần dùng ghi một dòng nhật ký kênh `quan_tri` (không nêu mật khẩu); mật khẩu sinh ngẫu nhiên chỉ hiện trên stdout của lệnh, không vào log/nhật ký/ngoại lệ. Mã thoát: `0` = xong; `2` = lỗi dự kiến (không admin, nhiều admin mà không chỉ `--ten`, mật khẩu yếu); `1` = lỗi bất ngờ.

## 9. Chặn trình duyệt từ xa chạm mạng nhà

**Vì sao:** container `web2api-trinh-duyet` (`dich-vu/trinh-duyet/`) chạy Chromium/Firefox,
giữ cookie các tài khoản đã đăng nhập và CẦN ra Internet để đăng nhập dịch vụ ngoài —
compose cố ý không đặt mạng `internal`. Một trang độc/quảng cáo chạy JS trong trình
duyệt có thể gọi `http://<IP-LAN>:3030/` (Home Assistant, router, gateway Docker...)
ngay từ TRONG mạng nhà. Chính sách Chromium chỉ chặn `file://`, không chặn theo IP.

**Giải pháp:** luật iptables/ip6tables của MÁY CHỦ trong HAI chuỗi, chặn theo
DẢI ĐỊA CHỈ / GIAO DIỆN CẦU (không theo tên miền — không có danh sách trang nào
để duy trì):

- **Chuỗi `DOCKER-USER`** — lưu lượng container → mạng KHÁC (đi qua FORWARD).
  Chặn (REJECT) các dải riêng tư (RFC 1918), link-local (RFC 3927), CGNAT
  (RFC 6598) và loopback — danh sách đầy đủ trong `DAI_CHAN_V4`/`DAI_CHAN_V6`
  của `scripts/chan-lan-trinh-duyet.sh`, không ghi lại ở đây để tránh lệch.
  Ngoại lệ (đứng TRƯỚC mọi luật chặn): lưu lượng trong chính mạng của dịch vụ
  (lõi web2api được nối vào mạng này bằng `docker network connect`;
  docker-compose.yml của dịch vụ chỉ có MỘT service nên không còn dịch vụ phụ
  nào khác cần ngoại lệ) và DNS cổng 53 tới gateway của mạng Docker.
- **Chuỗi `INPUT`** (bổ sung NV30) — lưu lượng container → CHÍNH MÁY CHỦ.
  Đường này KHÔNG đi qua `DOCKER-USER`: các cổng 3030/3031/22/8099… nghe
  `0.0.0.0` và IP gateway của mạng Docker đều tới được từ container nếu chỉ
  chặn ở `DOCKER-USER`. Luật gắn theo giao diện cầu `-i <cầu>` của mạng
  `web2api-trinh-duyet` (lấy tên cầu từ `docker network inspect`: Options
  `com.docker.network.bridge.name`, nếu trống thì `br-`<12 ký tự đầu của Id
  mạng>; không hard-code):
  1. `ACCEPT` lưu lượng `ESTABLISHED,RELATED` từ cầu (trả về của kết nối do
     máy chủ khởi tạo) — đứng trước mọi luật chặn;
  2. `ACCEPT` UDP/TCP cổng 53 tới gateway của mạng Docker;
  3. `REJECT` mọi còn lại từ cầu tới máy chủ.
  Luật chèn đầu chuỗi (`-I`) để đứng trước mọi luật cho phép khác có sẵn.
- Vì sao container vẫn nói chuyện được với lõi web2api: lõi được nối vào CHÍNH
  mạng này nên container↔container đi qua cầu ở tầng L2, KHÔNG vào chuỗi INPUT
  của máy chủ. Luật INPUT chỉ gắn `-i <cầu>` nên không chạm lưu lượng trên các
  giao diện khác của máy chủ.
- Mọi luật gắn nhãn comment `web2api-trinh-duyet`; dải mạng Docker lấy từ
  `docker network inspect web2api-trinh-duyet`, không hard-code.

**Cài (một lần, idempotent — chạy hai lần không nhân đôi luật):**

```bash
sudo ./scripts/chan-lan-trinh-duyet.sh
```

**Chỉ kiểm tra, không đổi gì:** `sudo ./scripts/chan-lan-trinh-duyet.sh --kiem`
(thoát 0 nếu đủ luật, 1 nếu thiếu). **Xem trước lệnh sẽ chạy, không cần quyền:**
`./scripts/chan-lan-trinh-duyet.sh --dry-run`.

**Kiểm bằng một lệnh curl từ trong container** (phải BỊ TỪ CHỐI, curl thoát mã lỗi
khác 0, không phải HTTP 200) — gồm cả đường tới mạng LAN và đường tới CHÍNH
MÁY CHỦ (qua IP gateway của mạng Docker; `<IP-gateway>` lấy từ
`docker network inspect web2api-trinh-duyet`, ví dụ `192.0.2.1` chỉ là ví dụ
minh hoạ, KHÔNG phải IP thật):

```bash
docker exec web2api-trinh-duyet curl -s -m 5 -o /dev/null http://192.0.2.1:3030/ ; echo "curl exit=$?"
docker exec web2api-trinh-duyet curl -s -m 5 -o /dev/null http://198.51.100.1/ ; echo "curl exit=$?"
GW=$(docker network inspect web2api-trinh-duyet -f '{{(index .IPAM.Config 0).Gateway}}')
docker exec web2api-trinh-duyet curl -s -m 5 -o /dev/null http://$GW:3031/ ; echo "curl exit=$? (tới chính máy chủ qua gateway)"
```
(Địa chỉ `192.0.2.1`, `198.51.100.1` thuộc dải tài liệu RFC 5737, chỉ là ví dụ
minh hoạ — thay bằng IP LAN thật khi kiểm trên máy của bạn.)

**Gỡ:** `sudo ./scripts/chan-lan-trinh-duyet.sh --go` — chỉ gỡ đúng các luật có nhãn
`web2api-trinh-duyet` (cả hai chuỗi DOCKER-USER và INPUT), không đụng luật khác.

**CẢNH BÁO — luật mất khi khởi động lại:** luật iptables không lưu trữ; khởi động lại
máy hoặc `systemctl restart docker` là mất. Gắn vào systemd để tự cài lại sau khi
Docker lên (chuỗi `DOCKER-USER` chỉ tồn tại khi Docker đã chạy):

```ini
# /etc/systemd/system/chan-lan-trinh-duyet.service
[Unit]
Description=Chặn container trình duyệt chạm mạng LAN
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/opt/web2api/scripts/chan-lan-trinh-duyet.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now chan-lan-trinh-duyet.service
```

**Trạng thái kiểm chứng:**
- Test tĩnh: `test/test_web2api_nv27_chan_lan.py` (16 test) và
  `test/test_web2api_nv30_chan_lan_input.py` (13 test) — chạy bằng lệnh
  `docker`/`iptables` GIẢ, không cần root. Đột biến xóa luật REJECT INPUT đã
  được test bắt (đỏ).
- **CHƯA kiểm thật trên máy chủ**: cần chạy trên máy chủ thật — cài luật bằng
  `sudo`, kiểm `curl` từ trong container tới IP LAN và tới `$GW:3031` đều phải
  bị từ chối, và gắn systemd để luật tự cài lại sau khi Docker lên.

**Giới hạn đã biết:**
- Container dùng DNS `127.0.0.11` của Docker không đi qua `DOCKER-USER` (Docker chặn
  ngay trong netns của container); script vẫn dự phòng ngoại lệ DNS cổng 53 tới
  gateway đề phòng container dùng gateway làm trình phân giải.

---

## 8. Mã hoá bí mật và phiên khi nghỉ (Encryption at Rest) và quay vòng khoá

Mặc định, nếu không cấu hình khoá chính, các tệp bí mật (`bi_mat/<id>.json`, `bi_mat/mo-dun/<id>/...`) và phiên tài khoản (`bi_mat/phien/<module>/<tai_khoan>.json`) được lưu dưới dạng JSON thuần với quyền 0600. Tuy nhiên, nếu máy chủ bị xâm nhập, ổ đĩa bị tháo trộm hoặc snapshot volume bị lộ, toàn bộ khoá API và cookie sẽ bị lộ dạng rõ.

Từ phiên bản F5, web2api hỗ trợ mã hoá AES-256-GCM khi nghỉ với tiêu đề `W2A1` và xác thực liên kết đường dẫn (AAD) chống tráo đổi tệp.

### 8.1. Cấu hình khoá chính 32 byte an toàn

Khoá chính là một chuỗi 32 byte (256 bit). Có thể sinh ngẫu nhiên bằng lệnh:

```bash
# Sinh khoá chính dạng hex 64 ký tự:
openssl rand -hex 32

# Hoặc dạng base64:
openssl rand -base64 32
```

web2api lấy khoá chính theo thứ tự ưu tiên:
1. **Biến môi trường `WEB2API_KHOA_CHINH`**: Chuỗi hex 64 ký tự hoặc base64. Thích hợp cho môi trường thử nghiệm:
   ```yaml
   environment:
     - WEB2API_KHOA_CHINH=<kết quả của: openssl rand -hex 32>
   ```
   > [!WARNING]
   > Khoá truyền qua biến môi trường dễ bị lộ qua lệnh `docker inspect` và tệp `/proc/<pid>/environ` trên máy chủ máy chủ. Trong môi trường sản xuất, **khuyến nghị dùng tệp ngoài volume hoặc Docker Secret** (`WEB2API_KHOA_CHINH_TEP`).

2. **Tệp khoá `WEB2API_KHOA_CHINH_TEP` (Khuyến nghị cho sản xuất / Docker Secret)**:
   - Tệp phải có quyền nghiêm ngặt **0600** (`chmod 0600`).
   - **BẮT BUỘC NGOÀI VOLUME DỮ LIỆU**: Tệp khoá phải nằm ngoài thư mục `<WEB2API_DATA>` (ví dụ không nằm trong `/data`).
   > [!CAUTION]
   > **Tuyệt đối KHÔNG lưu tệp khoá chính vào cùng thư mục dữ liệu `/data`**. Nếu khoá nằm cạnh dữ liệu, kẻ tấn công chụp snapshot ổ đĩa hoặc sao lưu volume sẽ lấy được cả khoá lẫn dữ liệu, biến mã hoá thành vô nghĩa. Hãy dùng Docker secret hoặc bind-mount riêng một tệp từ hệ thống tệp của máy chủ:
   ```yaml
   services:
     web2api:
       volumes:
         - web2api_data:/data
         - /etc/web2api/khoa_chinh.key:/run/secrets/khoa_chinh.key:ro
       environment:
         - WEB2API_KHOA_CHINH_TEP=/run/secrets/khoa_chinh.key
         - WEB2API_BAT_BUOC_MA_HOA=1
   ```

3. **Cờ bắt buộc mã hoá `WEB2API_BAT_BUOC_MA_HOA=1`**:
   Bật cờ này để đảm bảo máy chủ dừng khởi động nếu thiếu khoá chính hoặc khoá bị sai định dạng/sai quyền/entropy quá thấp. Giúp ngăn ngừa sự cố người vận hành quên nạp khoá khiến dữ liệu mới ghi dạng rõ.

> [!NOTE]
> **Phạm vi mã hoá và mô hình an ninh**:
> - Hiện tại, hàm khoá mã hoá các tệp bí mật (`bi_mat/<id>.json`, `bi_mat/mo-dun/<id>/...`) và phiên tài khoản (`bi_mat/phien/...`). Các tệp quản trị hệ thống khác (`bi_mat/khoa_van_tay`, `admin_key`, `khoa_khach.json`, `nguoi_dung`, `phien_web`) chưa qua hàm khoá.
> - Tiêu đề `W2A1` chưa mang mã khoá (key ID) nên việc xoay khoá dần trên đĩa chưa hỗ trợ; xoay khoá thực hiện qua quy trình sao lưu và khôi phục ở mục 8.2.
> - Module hạng A chạy chung tiến trình với lõi có toàn quyền trong RAM (đọc được biến môi trường và khoá chính). Cô lập an ninh toàn diện chỉ có khi chạy module hạng B (tiến trình riêng / container riêng như module từ xa).

### 8.2. Quy trình quay vòng khoá (Key Rotation)

Bản sao lưu (`POST /api/sao-luu`) của web2api có mã hoá độc lập bằng mật khẩu người dùng (dùng scrypt + AES-256-GCM), các tệp bí mật bên trong gói tar được giải mã sang dạng chuẩn trước khi đóng gói. Nhờ vậy, bản sao lưu **không phụ thuộc** vào khoá chính của máy chủ.

Để quay vòng sang khoá chính mới mà không làm mất bí mật và phiên:
1. Tạo một bản sao lưu có mật khẩu:
   ```bash
   curl -X POST -H "Authorization: Bearer $ADMIN_KEY" \
     -H "Content-Type: application/json" \
     -d '{"mat_khau": "MatKhauSaoLuuAnToan123456", "kem_phien": true}' \
     http://127.0.0.1:3031/api/sao-luu -o ban_sao_luu.tar.enc
   ```
2. Sinh khoá chính mới và cập nhật `WEB2API_KHOA_CHINH` hoặc tệp `WEB2API_KHOA_CHINH_TEP`.
3. Khởi động lại container web2api với khoá chính mới.
4. Kiểm tra và khôi phục bản sao lưu:
   ```bash
   # Bước 1: Kiểm tra tệp sao lưu và lấy sha256
   SHA=$(curl -s -X POST -H "Authorization: Bearer $ADMIN_KEY" \
     -F "tep=@ban_sao_luu.tar.enc" \
     -F "mat_khau=MatKhauSaoLuuAnToan123456" \
     -F "che_do=kiem" \
     http://127.0.0.1:3031/api/khoi-phuc | jq -r .sha256)

   # Bước 2: Áp dụng khôi phục
   curl -X POST -H "Authorization: Bearer $ADMIN_KEY" \
     -F "tep=@ban_sao_luu.tar.enc" \
     -F "mat_khau=MatKhauSaoLuuAnToan123456" \
     -F "che_do=ap_dung" \
     -F "sha256=$SHA" \
     http://127.0.0.1:3031/api/khoi-phuc
   ```
5. Toàn bộ bí mật và phiên sẽ được tự động giải mã từ bản sao lưu và ghi lại lên đĩa với mã hoá bằng **khoá chính mới**.

### 8.3. Di trú dữ liệu dạng rõ sang mã hoá (nâng cấp triển khai đã chạy)

Khi đã có khoá chính **và** bật `WEB2API_BAT_BUOC_MA_HOA=1`, lõi **từ chối đọc** tệp bí mật dạng rõ (chống hạ cấp), và việc đọc không tự mã hoá lại tệp. Triển khai cũ có dữ liệu dạng rõ mà bật cờ thẳng thì các module không đọc được khoá/cookie của chúng. Phải di trú **trước**, khi web2api đang dừng (tránh ghi đồng thời). Quy trình đã chạy thật ngày 20/10/2026 (5 tệp, đọc lại giải mã đúng):

```bash
# 0. Sao lưu thư mục dữ liệu (đường lùi) — tệp chứa bí mật nên giữ quyền 0600
umask 077
tar czf sao-luu-truoc-ma-hoa.tar.gz -C /duong/dan/den/cha-cua data

# 1. Sinh khoá chính NGOÀI thư mục dữ liệu rồi chép bản dự phòng ra ngoài máy
openssl rand -base64 32 > /duong/ngoai/data/khoa-chinh
sudo chown 1000:1000 /duong/ngoai/data/khoa-chinh && chmod 600 /duong/ngoai/data/khoa-chinh

# 2. Dừng web2api rồi di trú bằng chính ảnh đang dùng (không mạng, user 1000)
docker stop web2api
docker run --rm -i --user 1000:1000 --network none --cap-drop ALL --security-opt no-new-privileges:true \
  --entrypoint python -v /duong/den/data:/data -v /duong/ngoai/data/khoa-chinh:/run/web2api/khoa-chinh:ro \
  -e WEB2API_DATA=/data -e WEB2API_KHOA_CHINH_TEP=/run/web2api/khoa-chinh -e WEB2API_CHO_DOC_RO=1 \
  web2api:latest - <<'PY'
import os
from pathlib import Path
from app import ham_khoa
goc = Path('/data/bi_mat')
ds = sorted(p for p in goc.rglob('*.json') if p.is_file())
for p in ds:
    if p.read_bytes().startswith(ham_khoa.TIEN_TO_MA_HOA):
        continue                                   # đã mã hoá sẵn
    ham_khoa.ghi_bi_mat_tep(p, ham_khoa.doc_bi_mat_tep(p), 0o600)   # đọc bản rõ (CHO_DOC_RO=1) → ghi mã hoá
os.environ.pop("WEB2API_CHO_DOC_RO", None)         # kiểm lại: tắt cờ đọc rõ, phải giải mã được
for p in ds:
    assert p.read_bytes().startswith(ham_khoa.TIEN_TO_MA_HOA), p
    assert isinstance(ham_khoa.doc_bi_mat_tep(p), dict), p
    print("ok", p.relative_to(goc))
PY

# 3. Bật cờ + gắn tệp khoá trong compose (xem README, «Bước 0»), rồi khởi động lại
docker compose up -d
```

- Chỉ các tệp `bi_mat/**/*.json` được mã hoá (đúng phạm vi mục 8.1). `bi_mat/khoa_van_tay` và `admin_key` ở bản lõi này vẫn là tệp thường.
- Lỗi giữa chừng: dừng, giải nén bản sao lưu bước 0 đè lại thư mục dữ liệu, bỏ hai dòng cờ khỏi compose, khởi động lại.
- Không bao giờ giữ khoá chính trong cùng thư mục dữ liệu hay commit lên git.
