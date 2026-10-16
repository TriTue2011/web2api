# web2api

<p>
  <a href="https://github.com/TriTue2011/web2api/actions/workflows/docker-build.yml"><img src="https://github.com/TriTue2011/web2api/actions/workflows/docker-build.yml/badge.svg" alt="Docker Build"></a>
  <a href="https://github.com/TriTue2011/web2api/actions/workflows/security.yml"><img src="https://github.com/TriTue2011/web2api/actions/workflows/security.yml/badge.svg" alt="Security Scan"></a>
</p>

Cổng AI chuẩn OpenAI (`/v1/models`, `/v1/chat/completions`, stream, tạo ảnh, video, âm thanh, nhúng...) chạy trong một container Docker nhỏ gọn. Dự án dành cho cá nhân và gia đình muốn hợp nhất các nguồn AI (Google Gemini, Meta Muse...) vào các ứng dụng chuẩn OpenAI như OpenAI SDK, Home Assistant, Open WebUI hay LibreChat. Điểm khác biệt cốt lõi: lõi đóng vai trò **«phiên dịch viên»** trung lập, không chứa sẵn mã của nhà cung cấp nào; mọi nguồn AI là các **module** độc lập cài thêm lúc chạy từ git, không cần dựng lại ảnh Docker hay khởi động lại dịch vụ.

[English](README.en.md) | [Tiếng Việt](README.md)

```mermaid
flowchart LR
  subgraph NGUOIDUNG["Người dùng & Ứng dụng"]
    KHACH["Ứng dụng khách<br/>(OpenAI SDK, Home Assistant, curl)"]
    QUANTRI["Quản trị viên<br/>(Trình duyệt web)"]
  end
  subgraph W2A["web2api (Container :3031 / 127.0.0.1)"]
    LOI["Lõi phiên dịch viên<br/>(FastAPI /v1 & /api)"]
    MOD["Module độc lập<br/>(modules/<id>/)"]
  end
  NCC["Dịch vụ AI ngược dòng<br/>(Google AI Studio, Meta...)"]

  KHACH -->|"Bearer khoá khách (w2a_...)"| LOI
  QUANTRI -->|"Phiên web (Cookie HttpOnly + CSRF)"| LOI
  LOI -->|"Định tuyến theo tiền tố model"| MOD
  MOD -->|"API / giao thức riêng"| NCC
```

## Tính năng

- **Cổng chuẩn OpenAI toàn diện**:
  - Trò chuyện: `POST /v1/chat/completions` (hỗ trợ stream SSE, keep-alive, chuẩn hoá `finish_reason` khi gọi công cụ).
  - Danh mục mô hình: `GET /v1/models` và `GET /v1/models/{model}` (tự động gom mô hình từ các module đã liên kết và combo).
  - Hình ảnh: Tạo ảnh `POST /v1/images/generations`, sửa ảnh `POST /v1/images/edits`, Responses API `POST /v1/responses` với công cụ `image_generation` (hỗ trợ stream SSE).
  - Video: Tạo video bất đồng bộ `POST /v1/videos`, kiểm tra trạng thái `GET /v1/videos/{id}`, tải tệp `GET /v1/videos/{id}/content`.
  - Âm thanh: Đọc văn bản thành giọng nói `POST /v1/audio/speech` (stream nhị phân), nhận dạng giọng nói `POST /v1/audio/transcriptions`.
  - Nhúng & xếp hạng: Tạo vector nhúng `POST /v1/embeddings`, xếp hạng tài liệu `POST /v1/rerank`.
- **Kiến trúc Module nạp nóng**: Cài đặt, cập nhật hoặc gỡ bỏ module từ git ngay lúc chạy qua giao diện mà không cần dựng lại ảnh Docker hay tắt dịch vụ. Lõi hoàn toàn trung lập, không chứa tên nhà cung cấp cụ thể nào.
- **Giao diện quản trị tích hợp**:
  - **Studio**: Trò chuyện trực tiếp với AI, xem trước định dạng markdown, tạo ảnh, lưu lịch sử cục bộ trong trình duyệt.
  - **Hệ thống → Module**: Bàn cờ quản lý module, cài đặt từ git, kiểm tra quyền và tài nguyên yêu cầu.
  - **Hệ thống → AI**: Quản lý tài khoản, thêm/bớt nhiều khoá API cho từng nhà cung cấp, cấu hình model và provider.
  - **Hệ thống → Bảo mật & người dùng**: Quản lý người dùng, cấp phát khoá khách, kiểm tra chuỗi băm nhật ký hệ thống.
  - **Hệ thống → Thiết bị**: Quản lý thực thể thiết bị, kiểm soát quyền và hàng chờ phê duyệt hành động rủi ro.
- **Đa người dùng & Phân quyền**: Hỗ trợ hai vai trò `admin` và `user`. Mật khẩu băm an toàn bằng scrypt ($N=2^{14}$), khoá đăng nhập hai tầng chống brute-force phân tán, bảo vệ bất biến còn ít nhất một admin. Phiên web qua cookie `HttpOnly`, `SameSite`, tự động bật `Secure` và tiền tố `__Host-` khi chạy qua HTTPS, chống CSRF.
- **Khoá khách có phạm vi & Xoay khoá**: Cấp khoá khách (tiền tố `w2a_`) theo phạm vi cụ thể (`chat`, `anh`, `video`, `noi`, `nghe`, `nhung`, `xep_hang`, `tim_kiem`, `thiet_bi`, `trinh_duyet`, `*`). Xoay khoá không gián đoạn với thời gian ân hạn cho khoá cũ (mặc định 7 ngày). Kiểm soát trần yêu cầu đồng thời cho từng khoá và toàn cục.
- **Chuyển khoá & Dự phòng thông minh**: Tự động xoay vòng danh sách khoá API trong module, tạm nghỉ khoá gặp lỗi 429 hoặc lỗi hạn mức. Tính năng Combo (`/api/combo`) ghép chuỗi nhiều nguồn AI với cơ chế cầu chì và tự động hạ bậc khi gặp lỗi.
- **Sao lưu & Khôi phục mã hoá**: Xuất bản sao lưu toàn bộ cấu hình mã hoá AES-256-GCM với mật khẩu (`POST /api/sao-luu`). Khôi phục kiểm chứng toàn vẹn, bảo vệ quyền chủ sở hữu khoá, tự động hoàn tác nếu lỗi ghi (`POST /api/khoi-phuc`).
- **Nhật ký chống sửa lén (Tamper-evident)**: Từng dòng nhật ký được liên kết bằng chuỗi băm SHA-256 (`truoc` và `bam`), tự động phát hiện can thiệp hoặc đứt chuỗi (`GET /api/nhat-ky/chuoi`). Tự động che giấu khoá và bí mật trong mọi thông báo lỗi và nhật ký.
- **Trình duyệt từ xa noVNC (`vnc-xa`)**: Mở workspace trình duyệt trên máy chủ qua noVNC để đăng nhập thủ công các dịch vụ có CAPTCHA/OTP, duy trì phiên và tự động đưa cookie sang module khác mà không lộ ra ngoài.
- **Tìm kiếm tri thức & RAG (`/api/tim-kiem`)**: Tìm kiếm kết hợp 5 sơ đồ (công tắc nguồn, lọc nhiều mặt, cây phân cấp, mục lục tài liệu, phễu nối tầng), đánh dấu dữ liệu nguồn ngoài không tin cậy.
- **Công cụ AI từ thiết bị (`/api/cong-cu`)**: Tự động chuyển đổi thiết bị và hành động thành công cụ function-calling chuẩn OpenAI; hàng chờ phê duyệt cho các tác vụ nguy hiểm (`/api/phe-duyet`).

## Hình ảnh giao diện

![Màn hình Thiết lập ban đầu](docs/anh/thiet-lap-may-tinh.png)
*Màn hình thiết lập tài khoản quản trị viên lần đầu bằng mã thiết lập.*

![Màn hình Đăng nhập](docs/anh/dang-nhap-may-tinh.png)
*Màn hình đăng nhập quản trị viên và người dùng.*

![Lưới quản lý Module](docs/anh/nhieu-o-may-tinh.png)
*Giao diện quản lý module dạng lưới ô với trạng thái nạp nóng.*

![Studio trò chuyện](docs/anh/studio-may-tinh.png)
*Giao diện Studio trò chuyện trực tiếp qua luồng stream chuẩn OpenAI.*

![Bảo mật và người dùng](docs/anh/bao-mat-may-tinh.png)
*Quản lý tài khoản người dùng, cấp phát khoá khách có phạm vi và kiểm tra nhật ký.*

## Bắt đầu nhanh

Tệp `docker-compose.yml` chuẩn của dự án:

```yaml
services:
  web2api:
    image: ghcr.io/tritue2011/web2api:latest
    build: .
    container_name: web2api
    restart: unless-stopped
    ports:
      # Mặc định CHỈ nghe trên máy chủ (127.0.0.1): không công bố cổng ra mạng nhà/Internet.
      # Muốn truy cập từ máy khác trong mạng nhà: đặt WEB2API_BIND=<IP LAN của máy chủ> trong .env.
      - "${WEB2API_BIND:-127.0.0.1}:${WEB2API_PORT:-3031}:8000"
    environment:
      WEB2API_DATA: /data
      WEB2API_GIT_REPO: ${WEB2API_GIT_REPO-TriTue2011/web2api}
      WEB2API_GIT_NHANH: ${WEB2API_GIT_NHANH:-main}
      WEB2API_GIT_THU_MUC: ${WEB2API_GIT_THU_MUC:-modules}
    env_file:
      - path: .env
        required: false
    volumes:
      - ${WEB2API_DATA_DIR:-web2api-data}:/data
    user: "1000:1000"
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    mem_limit: 256m
    cpus: 0.5
    pids_limit: 100
    labels:
      com.centurylinklabs.watchtower.enable: "false"

volumes:
  web2api-data:
    name: web2api-data
```

### 3 bước khởi chạy

1. **Khởi động dịch vụ:**
   ```bash
   docker compose up -d
   ```

2. **Lấy mã thiết lập ban đầu:**
   Khi khởi động lần đầu (chưa có tài khoản admin), hệ thống tự sinh mã thiết lập ngẫu nhiên lưu tại tệp `/data/ma_thiet_lap` (và ghi một dòng thông báo đường dẫn tệp trong nhật ký container):
   ```bash
   docker exec web2api cat /data/ma_thiet_lap
   # Hoặc xem dòng thông báo trong nhật ký:
   docker compose logs web2api
   ```
   *(Nếu có đặt sẵn biến `WEB2API_ADMIN_KEY`, giá trị khoá đó cũng có thể dùng làm mã thiết lập ban đầu).*

3. **Thiết lập tài khoản & kiểm tra gọi API:**
   - Mở trình duyệt truy cập `http://127.0.0.1:3031` (hoặc `http://<IP-máy-chủ>:3031` nếu đã đặt `WEB2API_BIND`).
   - Màn hình **Thiết lập** xuất hiện: dán mã thiết lập vừa lấy ở bước 2, đặt Tên đăng nhập và Mật khẩu cho tài khoản admin đầu tiên, bấm **Hoàn tất thiết lập**. Tệp `ma_thiet_lap` sẽ tự động bị xoá.
   - Hệ thống tự động tạo phiên đăng nhập. Vào **Hệ thống → Bảo mật & người dùng** để tạo khoá khách (Client Key) hoặc vào trang cá nhân để tạo khoá API riêng.
   - Thử nghiệm gọi API bằng lệnh `curl`:
     ```bash
     # Liệt kê danh sách model sẵn sàng:
     curl -s -H "Authorization: Bearer <khoá-khách>" http://127.0.0.1:3031/v1/models
     # Gửi thử một yêu cầu trò chuyện:
     curl -s -X POST http://127.0.0.1:3031/v1/chat/completions \
       -H "Authorization: Bearer <khoá-khách>" \
       -H "Content-Type: application/json" \
       -d '{"model": "gemini/gemini-2.5-flash", "messages": [{"role": "user", "content": "Xin chào web2api!"}]}'
     ```
   > [!IMPORTANT]
   > Mọi tuyến API (ngoại trừ kiểm tra sức khoẻ `/health`, trang tĩnh và màn hình đăng nhập/thiết lập ban đầu) đều bắt buộc phải có xác thực (Bearer khoá khách/khoá quản trị hoặc cookie phiên đăng nhập).

## Module có sẵn

| Module | Khả năng | Giao thức | Trạng thái |
|---|---|---|---|
| `gemini-api` | Chat stream, tạo ảnh, video Veo | OpenAI HTTP | Có sẵn (chat đã kiểm; ảnh/video cần khoá trả phí Google AI Studio) |
| `muse` | Trò chuyện | Noise XX WebSocket | Thử nghiệm, chưa kiểm với dịch vụ thật (cần khoá thiết bị X25519) |
| `muse2api` | Trò chuyện miễn phí bằng cookie | WebSocket + Noise XX | Có sẵn (giao thức trực tiếp muse.ai, không cần API key) |
| `antigravity` | Trò chuyện đa mô hình qua CLI | HTTP Bridge | Có khung, chưa kiểm với `agy` thật (cần dịch vụ cầu nối ngoài container) |
| `vnc-xa` | Workspace trình duyệt từ xa noVNC | HTTP / WebSocket | Có sẵn (đăng nhập tay, trích xuất cookie sang module khác) |

- **Cách cài đặt module**: Vào tab **Hệ thống → Module**, duyệt danh sách module tìm thấy từ nguồn git, bấm **Cài đặt**.
- **Cấu hình nguồn git**: Qua biến `WEB2API_GIT_REPO` (mặc định `TriTue2011/web2api`, nhánh `main`, thư mục `modules`). Tính năng tự cài ngầm mặc định TẮT (`tu_dong: false`) — hệ thống chỉ báo khi có bản mới để quản trị viên duyệt quyền trước khi nạp.
- **Dành cho nhà phát triển viết module mới**: Tham khảo chi tiết tại [docs/CHUAN-MODULE.md](docs/CHUAN-MODULE.md) và [docs/CHUAN-BAO-MAT-MODULE.md](docs/CHUAN-BAO-MAT-MODULE.md).

## Cấu hình

Các biến môi trường thường dùng nhất (cấu hình trong `.env` hoặc Docker Compose):

| Biến môi trường | Mặc định | Tác dụng |
|---|---|---|
| `WEB2API_DATA` | `/data` | Đường dẫn thư mục dữ liệu (khoá, cấu hình, phiên, module cài lúc chạy) |
| `WEB2API_BIND` | `127.0.0.1` | Địa chỉ IP máy chủ để container lắng nghe (chỉ trong compose; đặt IP LAN để mở mạng nhà) |
| `WEB2API_PORT` | `3031` | Cổng dịch vụ lắng nghe trên máy chủ (chỉ trong compose) |
| `WEB2API_ADMIN_KEY` | Tự sinh | Khoá quản trị dự phòng (Bearer); để trống tự sinh lưu vào `<WEB2API_DATA>/admin_key` |
| `WEB2API_GIT_REPO` | `TriTue2011/web2api` | Kho GitHub chứa module (`chủ/kho`); đặt rỗng để tắt nguồn git |
| `WEB2API_GIT_NHANH` | `main` | Nhánh git theo dõi module |
| `WEB2API_GIT_THU_MUC` | `modules` | Thư mục chứa module trong kho git |
| `WEB2API_GITHUB_TOKEN` | Không | Token GitHub PAT (quyền `Contents: Read`) cho kho riêng tư hoặc tránh hạn mức 60 req/h (`WEB2API_GIT_TOKEN` là bí danh) |
| `WEB2API_TIN_PROXY` | Không | Đặt `1` để tin `X-Forwarded-Proto` từ proxy ngược, bật cookie `Secure` và tiền tố `__Host-` |
| `WEB2API_TRUST_PROXY` | Không | Danh sách CIDR của proxy ngược/Cloudflare để bóc tách IP khách từ `X-Forwarded-For` |
| `WEB2API_PUBLIC_URL` | Không | Địa chỉ URL công khai chính thức (ví dụ `https://ai.example.com`) |
| `WEB2API_QT_IP_CHO_PHEP` | Không | Danh sách CIDR (ví dụ `192.0.2.0/24`) giới hạn mạng được gọi API quản trị và thiết lập |
| `WEB2API_KHACH_DONG_THOI` | `4` | Số yêu cầu đồng thời tối đa cho mỗi khoá khách (`0` = không giới hạn) |
| `WEB2API_V1_DONG_THOI_TOAN_CUC` | `32` | Số yêu cầu đồng thời tối đa toàn cục cho cổng `/v1` (`0` = không giới hạn) |
| `WEB2API_THAN_TOI_DA_MB` | `25` | Trần dung lượng thân yêu cầu JSON (MB) cho `/v1` và `/api/*` |
| `WEB2API_TEP_TOI_DA_MB` | `25` | Trần dung lượng yêu cầu multipart/form-data tải tệp (MB) |
| `WEB2API_RL_LOI_XAC_THUC` | `30` | Ngưỡng sai khoá tối đa trước khi tạm khoá IP trong `WEB2API_RL_KHOA_GIAY` (mặc định 60s) |
| `WEB2API_DANG_NHAP_SAI_TOI_DA` | `5` | Số lần đăng nhập sai mật khẩu liên tiếp tối đa của một tài khoản trước khi tạm khoá 15 phút |
| `TZ` | Không | Múi giờ IANA (ví dụ `Asia/Ho_Chi_Minh`) để hiển thị đúng thời gian trên Studio |

Bảng tra cứu đầy đủ tất cả các biến hệ thống có tại [docs/THAM-CHIEU.md mục 8](docs/THAM-CHIEU.md#8-tra-cứu-api-và-biến-môi-trường).

## Bảo mật

- **Mặc định an toàn**:
  - Container chỉ lắng nghe loopback `127.0.0.1`, chạy bằng người dùng không đặc quyền `uid 1000:1000`.
  - Tước bỏ toàn bộ đặc quyền Linux (`cap_drop: [ALL]`) và cấm leo quyền (`no-new-privileges:true`).
  - Nguồn git mặc định tắt tự động cài (`tu_dong: false`), chỉ thông báo cập nhật.
  - Khoá khách mới tạo có phạm vi mặc định thu hẹp `["chat"]`, không cấp toàn quyền `*`.
  - Mật khẩu băm an toàn bằng scrypt; khoá đăng nhập hai tầng chống brute-force phân tán.
  - Cookie phiên mang cờ `HttpOnly`, `SameSite=Strict` (hoặc Lax), tự động bật `Secure` và tiền tố `__Host-` khi có HTTPS, chống CSRF.
  - Chống SSRF và bảo vệ đường dẫn: kiểm duyệt chặt chẽ mọi URL từ xa qua `app/tu_xa.py`, ghim IP lúc kết nối và chuẩn hoá đường dẫn tệp.
  - Tự động che giấu bí mật/khoá trong lỗi và nhật ký; nhật ký hệ thống ghi chuỗi băm SHA-256 chống sửa lén.
- **Khuyến nghị triển khai ra Internet**:
  - Triển khai sau Cloudflare Tunnel hoặc VPN riêng (WireGuard, Tailscale) thay vì mở cổng router gia đình.
  - Bắt buộc khai báo `WEB2API_TRUST_PROXY` và đặt `WEB2API_TIN_PROXY=1` khi chạy sau reverse proxy.
  - Quản trị máy chủ chỉ dùng khoá SSH (SSH Key), tắt đăng nhập bằng mật khẩu, tắt UPnP trên router.
  - Hướng dẫn cấu hình chi tiết: xem [docs/TRIEN-KHAI.md](docs/TRIEN-KHAI.md).
- **Báo cáo lỗ hổng**: Xem hướng dẫn báo cáo riêng tư tại [SECURITY.md](SECURITY.md).
- **Giới hạn đã biết**:
  - Module cài đặt chạy trong cùng tiến trình Python với lõi (in-process), chia sẻ bộ nhớ và đặc quyền của container.
  - Chưa áp dụng Content Security Policy (CSP) chặt chẽ cho tất cả các trang giao diện (chỉ có sandbox CSP cho noVNC).
  - Bản sao lưu có mật khẩu chứa khoá API của các module — cần cất giữ cẩn mật như mật khẩu.

## Portainer và NAS

Cổng dịch vụ mặc định là `3031` (ánh xạ `3031:8000`), dữ liệu gắn ngoài container vào `/data`. Để truy cập từ máy khác trong mạng nhà, đặt `WEB2API_BIND=<IP LAN của máy chủ>` trong `.env`.

### Portainer
1. Vào **Stacks** → **Add stack**, đặt tên stack (ví dụ `web2api`).
2. Dán khối compose ở mục [Bắt đầu nhanh](#bắt-đầu-nhanh) vào trình soạn thảo.
3. Thêm biến môi trường cần thiết ở mục **Environment variables** (như `WEB2API_BIND`).
4. Bấm **Deploy the stack**. Để cập nhật bản mới sau này, bấm **Pull and redeploy**. Lấy mã thiết lập bằng `docker exec web2api cat /data/ma_thiet_lap` trong console container để kích hoạt tài khoản admin lần đầu.

### Synology NAS (Container Manager)
1. Mở **Container Manager** → **Project** → **Create**.
2. Đặt tên dự án, chọn thư mục lưu trữ dữ liệu `/volume1/docker/web2api`.
3. Dán nội dung compose, đổi volume thành `/volume1/docker/web2api:/data`.
4. Hoàn tất khởi chạy dự án, mở Terminal container để lấy mã thiết lập từ `/data/ma_thiet_lap`.

### QNAP NAS (Container Station)
1. Mở **Container Station** → **Applications** → **Create**.
2. Đặt tên ứng dụng và dán nội dung docker-compose.
3. Đảm bảo volume gắn thư mục dữ liệu trên NAS vào `/data` của container và cấu hình user `1000:1000`.
4. Bấm **Create** để hệ thống khởi chạy, sau đó đọc mã thiết lập từ container logs.

### Unraid
1. Mở tab **Docker** (hoặc dùng plugin Compose Manager).
2. Dán nội dung compose hoặc cấu hình container từ ảnh `ghcr.io/tritue2011/web2api:latest`.
3. Ánh xạ cổng `3031:8000`, đường dẫn dữ liệu `/mnt/user/appdata/web2api` vào `/data`, user `1000:1000`.

## Kho riêng tư (Private Repository)

Có hai loại token cho hai mục đích hoàn toàn khác nhau — không nhầm lẫn:

### 1. Tải ảnh Docker từ ghcr.io khi gói riêng tư
Gói GHCR mặc định riêng tư (chủ kho có thể chuyển sang Public ở trang Packages của GitHub):
- Tạo GitHub Personal Access Token (classic) có quyền `read:packages`.
- Đăng nhập trên máy Docker:
  ```bash
  echo $TOKEN | docker login ghcr.io -u <tên-github> --password-stdin
  ```
  Thông tin lưu vào `~/.docker/config.json`, không đặt token vào compose.
- **Portainer**: Vào **Registries** → **Add registry** → chọn Custom/GitHub với URL `ghcr.io`, điền username + token, chọn registry này khi deploy stack.
- **Synology / QNAP / Unraid**: SSH vào thiết bị và chạy lệnh `docker login ghcr.io` như trên.

### 2. Nguồn module từ kho GitHub riêng tư
Khi lấy module từ kho GitHub riêng tư hoặc tránh hạn mức 60 yêu cầu/giờ/IP của GitHub:
- Tạo GitHub Fine-grained PAT có quyền **Repository permissions → Contents: Read-only** cho đúng kho chứa module.
- Khai báo biến môi trường `WEB2API_GITHUB_TOKEN` (hoặc `WEB2API_GIT_TOKEN`) trong compose hoặc `.env`. Hệ thống sẽ đọc `WEB2API_GITHUB_TOKEN` trước rồi đến `WEB2API_GIT_TOKEN`.

## Cập nhật, sao lưu, gỡ lỗi

### Cập nhật ảnh Docker
```bash
docker compose pull && docker compose up -d
```

### Sao lưu và khôi phục
- **Sao lưu qua API**: Gửi yêu cầu `POST /api/sao-luu` (kèm tham số `mat_khau` để mã hoá toàn bộ bí mật bằng AES-256-GCM).
- **Khôi phục qua API**: Tải tệp sao lưu lên qua `POST /api/khoi-phuc`. Hệ thống sẽ kiểm tra tính toàn vẹn và xung đột trước khi áp dụng.
- **Sao lưu volume Docker trực tiếp**:
  ```bash
  docker run --rm -v web2api-data:/data -v $(pwd):/backup alpine tar czf /backup/web2api-data-backup.tar.gz -C /data .
  ```

### Xử lý sự cố thường gặp

1. **Cổng 3031 bị chiếm:** Đổi cổng lắng nghe của máy chủ bằng biến `WEB2API_PORT=3032` trong tệp `.env`.
2. **Lỗi 429 Too Many Requests (bị khoá cả nhà):** Khi chạy sau Cloudflare/proxy ngược, thiếu biến `WEB2API_TRUST_PROXY` khiến hệ thống coi mọi yêu cầu đến từ một IP duy nhất và khoá IP khi có người gửi sai khoá. Khai báo dải IP của proxy vào `WEB2API_TRUST_PROXY` theo hướng dẫn tại [docs/TRIEN-KHAI.md](docs/TRIEN-KHAI.md).
3. **Lỗi 401 Unauthorized / 403 Forbidden khi gọi API:** Kiểm tra header `Authorization: Bearer <khoá>`. Đảm bảo khoá khách đang ở trạng thái bật và có phạm vi quyền phù hợp với tuyến API đang gọi (ví dụ cần quyền `anh` để tạo ảnh).
4. **Quên mật khẩu tài khoản admin:**
   - *Cách 1*: Dùng khoá quản trị Bearer (`WEB2API_ADMIN_KEY` hoặc đọc từ `/data/admin_key`) gọi `POST /api/nguoi-dung/{id}/dat-lai-mat-khau` (tra cứu `id` qua `GET /api/nguoi-dung`).
   - *Cách 2*: Dừng container, xoá tệp `nguoi_dung.json` trong volume `/data` (lưu ý sao lưu tệp trước), rồi khởi động lại container. Hệ thống nhận diện chưa có tài khoản admin và sẽ tự động sinh tệp mã thiết lập mới `/data/ma_thiet_lap` để bạn thiết lập lại từ đầu.
5. **Module không nạp hoặc báo lỗi:** Xem log container bằng `docker compose logs web2api` hoặc mở tab **Hệ thống → Module** để xem lỗi chi tiết. Nếu module đòi hỏi gói ngoài mà container không có, hãy chuyển sang hình thức module từ xa.
6. **Cookie phiên không lưu hoặc mất cờ Secure:** Khi chạy qua HTTPS sau reverse proxy, bắt buộc phải thêm `WEB2API_TIN_PROXY=1` vào `.env` để web2api nhận diện giao thức HTTPS và cấp cookie có cờ `Secure` (`__Host-w2a_phien`).
7. **GitHub báo hết hạn mức (Rate limit exceeded):** Tạo GitHub PAT chỉ-đọc và thêm vào biến `WEB2API_GITHUB_TOKEN`.
8. **Lỗi 502 Bad Gateway khi gọi model:** Dịch vụ AI phía sau bị ngắt kết nối hoặc nhà cung cấp chặn vùng (ví dụ Gemini tại Việt Nam báo lỗi không hỗ trợ vị trí, cần đặt `base_url` trỏ qua Cloudflare Worker proxy trong `deploy/`).

## Phát triển & đóng góp

Cần môi trường Python 3.13 (xem `.python-version`):
```bash
uv venv && uv pip install -r requirements.txt -r requirements-dev.txt
# hoặc dùng venv chuẩn:
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

### Chạy kiểm tra chất lượng
Trước khi mở Pull Request, chạy đủ các lệnh kiểm tra:
```bash
.venv/bin/python -m pytest test -q -p no:cacheprovider    # Kiểm thử tự động (máy chủ giả lập offline)
.venv/bin/python -m bandit -r app modules -lll -q          # Quét bảo mật mã nguồn
docker build -t web2api:latest .                          # Dựng thử ảnh Docker
```
*(Kiểm thử giao diện với Chrome thật và node là tuỳ chọn; tự động bỏ qua nếu môi trường không có Chrome).*

### Bản đồ thư mục

| Thư mục / Tệp | Vai trò |
|---|---|
| `app/` | Lõi FastAPI: `main.py` (khởi động), `tuyen.py` (định tuyến API), `kham_pha.py` (khám phá module), `so_module.py` (quản lý module), `nguoi_dung.py` (tài khoản & scrypt), `khoa_khach.py` (khoá khách), `nhat_ky.py` (nhật ký chuỗi băm), `sao_luu.py` (sao lưu/khôi phục), `chuyen_tiep.py` (chuyển tiếp OpenAI dùng chung) |
| `modules/<id>/` | Các module cài sẵn: `manifest.json`, `module.py` |
| `web/` | Giao diện web tĩnh (DC-Lite): Studio, Module, AI, Bảo mật, Thiết bị, Đăng nhập, Thiết lập |
| `test/` | Bộ kiểm thử tự động pytest với toàn bộ dịch vụ giả lập offline (Google, GitHub, OpenAI) |
| `docs/` | Tài liệu kỹ thuật: `THAM-CHIEU.md`, `TRIEN-KHAI.md`, `CHUAN-MODULE.md`, `CHUAN-BAO-MAT-MODULE.md`, ảnh chụp |
| `deploy/` | Tệp triển khai proxy phụ trợ (Cloudflare Worker) |
| `dich-vu/` | Dịch vụ phụ trợ (cầu nối Antigravity CLI, dịch vụ trình duyệt noVNC) |
| `scripts/` | Tiện ích hỗ trợ: `chup-man-hinh.sh`, `chan-lan-trinh-duyet.sh` |

Quy định đóng góp chi tiết có tại [CONTRIBUTING.md](CONTRIBUTING.md).

### Giấy phép
Bản quyền thuộc `TriTue2011`. **Giữ toàn quyền** (*All rights reserved* / Proprietary, xem [LICENSE](LICENSE)). Mã nguồn công khai trên GitHub nhằm mục đích minh bạch.

## Đọc tiếp

- [docs/THAM-CHIEU.md](docs/THAM-CHIEU.md): Tài liệu tham chiếu kỹ thuật chi tiết toàn diện.
- [docs/TRIEN-KHAI.md](docs/TRIEN-KHAI.md): Hướng dẫn triển khai an toàn ra Internet (Cloudflare Tunnel, SSH, cổng mạng).
- [docs/CHUAN-MODULE.md](docs/CHUAN-MODULE.md): Chuẩn hợp đồng và thông số kỹ thuật viết module mới.
- [docs/CHUAN-BAO-MAT-MODULE.md](docs/CHUAN-BAO-MAT-MODULE.md): Quy chuẩn bảo mật khi xây dựng module.
- [SECURITY.md](SECURITY.md): Chính sách bảo mật và kênh báo cáo lỗ hổng riêng tư.
- [CONTRIBUTING.md](CONTRIBUTING.md): Hướng dẫn đóng góp cho dự án.
