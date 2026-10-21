# Dịch vụ Đăng nhập & Giải Captcha (may-dang-nhap) cho web2api

Dịch vụ độc lập chạy trong container Docker riêng biệt, cung cấp API tự động hoá các bước đăng nhập hợp lệ của chính chủ tài khoản (Google, OpenAI, Claude, Gemini, Grok, Codex...) và giải Captcha (Cloudflare Turnstile, reCAPTCHA audio...) được port từ thành phần `captcha-solver` của kho `chatgpt2api`.

Lõi `web2api` giao tiếp với dịch vụ này qua giao thức HTTP nội bộ.

---

## 1. Mục đích và Vị trí trong kiến trúc

- **Mục đích**: Tách biệt hoàn toàn các tác vụ nặng và tiềm ẩn rủi ro (chạy Chromium/Patchright/Playwright, điều khiển màn hình ảo Xvfb, xử lý nhận dạng giọng nói giải captcha âm thanh) ra khỏi container chính của `web2api`.
- **Ranh giới an toàn**: Dịch vụ chạy trong container riêng, không phân quyền root (chạy dưới user `appuser` uid 1000:1000), tệp mã nguồn thuộc sở hữu `root` chỉ-đọc (`/app` 0755), container chạy ở chế độ chỉ đọc (`read_only: true`), không mở cổng công khai ra ngoài Internet.
- **Cổng mạng**:
  - `8010`: Cổng FastAPI HTTP API nội bộ (chỉ phục vụ các container cùng mạng Docker riêng hoặc bind `127.0.0.1`).
  - `6080`: Cổng noVNC giao diện web — noVNC mặc định tắt và CHƯA dùng được từ web2api; chỉ truy cập qua SSH tunnel; nhánh cần người chưa hoạt động.

---

## 2. Hướng dẫn triển khai độc lập

### 2.1. Biến thể ảnh và Đối số dựng (Build Arguments)

Ảnh Docker dựng từ `python:3.13-slim` (Python 3.13) hỗ trợ các build-args:
- `BIEN_THE`:
  - `firefox` (mặc định): Chỉ hỗ trợ Firefox (`patchright install --with-deps firefox`), bỏ qua gói Chromium Debian và CloakBrowser (do giấy phép nhị phân CloakBrowser chưa kiểm tra, tuân thủ Luật cứng 4 — nghi ngờ thì dừng). Cần gói `cryptography` để chạy két khoá mật khẩu.
  - `ca-hai`: Hỗ trợ cả hai trình duyệt: Chromium (gói `chromium` của Debian + CloakBrowser) và Firefox (`patchright install --with-deps firefox`). Chỉ sử dụng khi người dùng chủ động bật.
  - `chromium`: Chỉ hỗ trợ Chromium (gói `chromium` của Debian + CloakBrowser), bỏ qua bước cài đặt Firefox.
- `CAI_PHONG_CJK`: `1` (mặc định) hoặc `0` (bật/tắt gói phông `fonts-noto-cjk`).
- `CAI_GIAI_CAPTCHA`: `0` (mặc định) hoặc `1` (cài đặt thêm các gói Python giải captcha âm thanh: `playwright-recaptcha`, `speech_recognition`, `pydub`).

### 2.2. Khởi chạy bằng Docker Compose (Khuyến nghị)

Trong thư mục `dich-vu/may-dang-nhap/`:

```bash
docker compose up -d --build
```

Tệp `docker-compose.yml` đã được cấu hình chuẩn:
- Mạng nội bộ riêng biệt `web2api-may-dang-nhap`.
- Tách biệt hai volume riêng:
  - `may-dang-nhap-data` gắn vào `/data` (lưu trữ hồ sơ trình duyệt, `accounts.db`).
  - `may-dang-nhap-khoa` gắn vào `/khoa` (lưu trữ `khoa-dich-vu`, `vault.key`, `mat-khau-vnc` với quyền 0700).
- Chế độ chỉ đọc: `read_only: true` kèm các gắn tạm `tmpfs`:
  - `/tmp:size=256m,mode=1777,uid=1000,gid=1000`
  - `/run:size=64m,mode=0755,uid=1000,gid=1000`
  - `/home/appuser:size=256m,mode=0700,uid=1000,gid=1000` (đảm bảo Chromium/Firefox ghi cache cấu hình mà không báo lỗi quyền).
- Đường dẫn trình duyệt cố định ngoài `/root`:
  - `PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright` (thuộc root, 0755, người dùng không root đọc trực tiếp nhị phân đã cài sẵn lúc dựng).
  - `CLOAKBROWSER_CACHE_DIR=/opt/cloakbrowser` (thuộc `appuser:appuser` 1000:1000).
- Giới hạn an ninh nghiêm ngặt: `cap_drop: [ALL]`, `no-new-privileges: true`, `pids_limit: 2048`, `mem_limit: 2g`, `shm_size: 1gb`.
- Không công bố cổng trực tiếp ra ngoài (`expose: ["8010"]`). noVNC (6080) mặc định không expose.

### 2.3. Khởi chạy bằng lệnh Docker trực tiếp

```bash
# 1. Dựng ảnh. CẢNH BÁO (đo 21/10/2026): biến thể firefox KHỞI ĐỘNG được nhưng KHÔNG thao tác được trên trang (patchright chỉ vá cho Chromium:
#    evaluate/click/locator lỗi «Cannot read properties of undefined (reading '_client')») nên đăng nhập tự động sẽ hỏng. Muốn đăng nhập chạy thật hãy dựng
#    biến thể chromium (tải CloakBrowser — nhị phân đóng, giấy phép CHƯA kiểm; xem Dockerfile). NV206 đang sửa tận gốc.
docker build -t web2api-may-dang-nhap:latest --build-arg BIEN_THE=chromium .

# 2. Chạy container với quyền hạn tối thiểu và mạng riêng
docker run -d \
  --name web2api-may-dang-nhap \
  -v may-dang-nhap-data:/data \
  -v may-dang-nhap-khoa:/khoa \
  --read-only \
  --tmpfs /tmp:size=256m,mode=1777,uid=1000,gid=1000 \
  --tmpfs /run:size=64m,mode=0755,uid=1000,gid=1000 \
  --tmpfs /home/appuser:size=256m,mode=0700,uid=1000,gid=1000 \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --pids-limit 2048 \
  --memory 2g \
  --shm-size 1g \
  web2api-may-dang-nhap:latest
```

> [!WARNING]
> **Lưu ý sống còn về Volume `/khoa`**: Mất volume `/khoa` = mất khả năng đọc mật khẩu đã lưu; bắt buộc phải sao lưu `/khoa` cẩn thận (giữ riêng quyền 0700).

---

## 3. Quản lý khoá xác thực, Mật khẩu VNC & Két mật khẩu

### 3.1. Khoá API dịch vụ (`CAPTCHA_SOLVER_API_KEY`) & Kiểm tra lúc khởi động (Fail-fast)

Mọi yêu cầu gọi tới API (ngoại trừ `GET /health`) đều bắt buộc phải có tiêu đề xác thực:
`Authorization: Bearer <khoá_dich_vu>`

- **Kiểm tra lúc khởi động (Fail-fast)**: Trong `lifespan` của FastAPI (trước khi khởi động browser pool), dịch vụ gọi `lay_khoa_dich_vu()` một lần:
  - Nếu khai báo biến `CAPTCHA_SOLVER_API_KEY`: Bắt buộc phải có độ dài tối thiểu 24 ký tự, có ít nhất 10 ký tự khác nhau và không được là giá trị mặc định `"change-me"`. Nếu sai khuôn, dịch vụ dừng ngay lập tức khi khởi động với ngoại lệ `RuntimeError` (thông báo tiếng Việt không chứa giá trị khoá).
  - Nếu không khai báo biến: Dịch vụ tự động sinh khoá ngẫu nhiên an toàn bằng `secrets.token_urlsafe(32)` và ghi sẵn vào `/khoa/khoa-dich-vu` với quyền `0600` theo cơ chế nguyên tử (tệp tạm rồi `os.replace`). Người dùng có thể đọc ngay sau khi container lên:
    ```bash
    docker exec web2api-may-dang-nhap cat /khoa/khoa-dich-vu
    ```
- **So sánh chống timing attack**: So sánh khoá bằng `hmac.compare_digest`.
- **Bảo mật log**: Tuyệt đối không in giá trị khoá API ra stdout/stderr hay nhật ký hệ thống.

### 3.2. Mật khẩu VNC cho màn hình ảo (noVNC)

- Giao thức RFB (VNC) chỉ sử dụng 8 ký tự đầu tiên của mật khẩu. Do đó, script `entrypoint.sh` sinh ngẫu nhiên đúng 8 ký tự từ bảng chữ-số (`A-Za-z0-9`, độ mạnh ~47 bit entropy) bằng lệnh:
  `tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 8 > /khoa/mat-khau-vnc`
- Tệp mật khẩu lưu tại `/khoa/mat-khau-vnc` với quyền `0600` và không bao giờ được in ra stdout/log.
- Ghi chú vận hành: noVNC mặc định TẮT. Khi bật qua `CAPTCHA_SOLVER_BAT_NOVNC=1`, noVNC chỉ nghe trên loopback `127.0.0.1` bên trong container.

### 3.3. Két mật khẩu tài khoản (`vault.key`)

- Cơ sở dữ liệu SQLite tại `/data/accounts.db` lưu trữ thông tin tài khoản được mã hoá AES-256-GCM cho cả trường mật khẩu (`password`) và hạt giống TOTP (`totp_secret`) với tiền tố `v1:`.
- Dữ liệu xác thực bổ sung (AAD) được gắn chặt với `email` và tên trường để chống giả mạo hoặc tráo đổi ciphertext giữa các tài khoản.
- Khoá mã hoá két: Đọc từ biến môi trường `VAULT_MASTER_KEY`. Nếu chưa cấu hình, dịch vụ tự sinh khoá 32 byte mã hoá Base64 lưu tại `/khoa/vault.key` với quyền `0600` theo cơ chế nguyên tử (tệp tạm rồi `os.replace`). Nếu tệp rỗng hoặc sai chuẩn, ném lỗi `RuntimeError` tiếng Việt dừng dịch vụ để bảo vệ dữ liệu.
- Mặc định biến `VAULT_REQUIRE_ENCRYPTION=1` được bật trong Docker Compose để đảm bảo mọi bản ghi mới đều bắt buộc mã hoá.

### 3.4. Tách biệt Volume và Quy trình Sao lưu An toàn

- **Tách volume**: Thư mục `/khoa` (chứa các tệp nhạy cảm `khoa-dich-vu`, `vault.key`, `mat-khau-vnc`) được gắn vào volume riêng `web2api_may_dang_nhap_khoa` với quyền `0700`, hoàn toàn tách biệt khỏi volume dữ liệu `/data` (`web2api_may_dang_nhap_du_lieu`).
- **Sao lưu**: Quản trị viên có thể sao lưu volume dữ liệu `/data` (chứa hồ sơ duyệt web và `accounts.db` đã mã hoá) mà không vô tình kéo theo các tệp khoá giải mã trong `/khoa`. Volume `/khoa` cần được bảo vệ với mức độ an ninh cao nhất của máy chủ.

---

## 4. Công tắc tính năng (`CAPTCHA_SOLVER_TINH_NANG_BAT`)

Để thu hẹp bề mặt tấn công, dịch vụ áp dụng nguyên tắc đóng mặc định: chỉ nhóm `dang_nhap` được kích hoạt theo mặc định. Các nhóm tính năng khác chỉ hoạt động khi được khai báo tường minh qua biến môi trường `CAPTCHA_SOLVER_TINH_NANG_BAT` (phân tách bằng dấu phẩy).

Khi một endpoint thuộc nhóm tính năng chưa bật được gọi, dịch vụ sẽ trả về mã lỗi HTTP `403 Forbidden` với cấu trúc JSON:
```json
{
  "detail": "Tính năng '<tên_nhóm>' đang tắt trên máy chủ.",
  "ma_loi": "tinh_nang_tat"
}
```

Các nhóm tính năng hợp lệ:
- `dang_nhap` (BẬT mặc định): Tự động hoá đăng nhập các nhà cung cấp, quản lý profile và lưu trữ danh sách tài khoản cơ bản.
- `lo_bi_mat` (TẮT mặc định): Các tuyến đường tiết lộ hoặc tiếp nhận bí mật ra/vào API (`GET /v1/accounts/saved/{email}/reveal`, `GET /v1/accounts/saved/{email}/totp`, `PUT /v1/accounts/saved/{email}/totp`). Luồng tự động đăng nhập lại chạy nội bộ nên không cần mở nhóm này.
- `giai_captcha` (TẮT mặc định): Các API giải Cloudflare Turnstile, reCAPTCHA v2/v3 và cổng tự giải reCAPTCHA bằng âm thanh trong luồng đăng nhập.
- `luong_anh` (TẮT mặc định): Luồng tạo ảnh/video Google Flow.
- `facebook` (TẮT mặc định): Tác vụ đăng bài nhóm Facebook.
- `tu_dang_ky` (TẮT mặc định): Luồng tự động đăng ký tài khoản (Codex, multi-onboard).
- `chay_tuy_y` (TẮT mặc định): Chạy mã điều khiển trình duyệt tuỳ ý (`POST /v1/browser/run`).
- `ai_qua_trinh_duyet` (TẮT mặc định): Sử dụng AI trực tiếp qua phiên web trình duyệt (Gemini Web, ChatGPT Web).
- `day_token_ngoai` (TẮT mặc định): Đẩy token làm mới sang dịch vụ ngoài.

---

## 5. Nguồn gốc mã nguồn

Mã nguồn trong thư mục `src/` được chép nguyên văn từ:
- **Kho**: `TriTue2011/chatgpt2api`
- **Commit**: `d879b034fa21`
- **Đường dẫn gốc**: `captcha-solver/src/`

**Mục đích**: Tự động hoá các bước đăng nhập hợp lệ của chính chủ tài khoản và giải các thử thách trình duyệt thực tế.

---

## 5. Tận dụng từ c2a (Danh sách tệp đã chép từ TriTue2011/chatgpt2api@d879b03)

Mã nguồn được kế thừa nguyên văn từ thành phần `captcha-solver` của kho `TriTue2011/chatgpt2api` tại commit `d879b034fa21` (đã lọc dữ liệu nhà, thay bằng ví dụ). Bảng dưới đây đối chiếu trạng thái băm so với `nguon-c2a/BANG-BAM-GOC.txt`:

| STT | Đường dẫn tệp c2a | Commit gốc | Tình trạng băm | Chi tiết chỉnh sửa `# W2A:` (nếu có) |
|:---:|---|:---:|:---:|---|
| 1 | `captcha-solver/src/__init__.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 2 | `captcha-solver/src/accounts_db.py` | `d879b03` | Chỉnh `# W2A:` | `chmod 0600` rõ ràng cho `accounts.db` khi tạo/mở kết nối `_conn()` |
| 3 | `captcha-solver/src/auto_login.py` | `d879b03` | Chỉnh `# W2A:` | Thêm cổng kiểm tra nhóm tính năng `giai_captcha` trước khi gọi giải captcha âm thanh |
| 4 | `captcha-solver/src/browser_pool.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 5 | `captcha-solver/src/chatgpt_login.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 6 | `captcha-solver/src/claude_web_login.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 7 | `captcha-solver/src/codex_google_onboard.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 8 | `captcha-solver/src/gemini_web_login.py` | `d879b03` | Chỉnh `# W2A:` | Bỏ ký tự BOM `\ufeff` ở đầu tệp tránh SyntaxError trên Python 3.13 |
| 9 | `captcha-solver/src/github_codex_onboard.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 10 | `captcha-solver/src/grok_web_login.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 11 | `captcha-solver/src/main.py` | `d879b03` | Chỉnh `# W2A:` | Xác thực Bearer `hmac.compare_digest`; kiểm tra fail-fast; kiểm soát nhóm tính năng `can_tinh_nang`; `GET /health` khai danh tính `may-dang-nhap` và cảnh báo thiếu khoá; dùng `_PROFILE_SLUG` + `resolve()` trong `api_delete_profile` |
| 12 | `captcha-solver/src/openai_native_login.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 13 | `captcha-solver/src/settings.py` | `d879b03` | Chỉnh `# W2A:` | Bỏ khoá mặc định `change-me`; tách `key_dir` ra `/khoa`; thêm công tắc `tinh_nang_bat` và `bat_novnc` |
| 14 | `captcha-solver/src/solvers/__init__.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 15 | `captcha-solver/src/solvers/browser_run.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 16 | `captcha-solver/src/solvers/chatgpt_web.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 17 | `captcha-solver/src/solvers/facebook_group.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 18 | `captcha-solver/src/solvers/flow_google.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 19 | `captcha-solver/src/solvers/flow_rest.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 20 | `captcha-solver/src/solvers/flow_rpc.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 21 | `captcha-solver/src/solvers/gemini_web.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 22 | `captcha-solver/src/solvers/model_tracker.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 23 | `captcha-solver/src/solvers/recaptcha.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 24 | `captcha-solver/src/solvers/turnstile.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 25 | `captcha-solver/src/solvers/twocaptcha.py` | `d879b03` | Giữ nguyên băm | Không sửa đổi |
| 26 | `captcha-solver/src/vault.py` | `d879b03` | Chỉnh `# W2A:` | Khoá chính lưu tại `key_dir` `/khoa`; fail-fast khi tệp rỗng/hỏng; kiểm tra có sẵn bản ghi `v1:` thì KHÔNG sinh khoá mới và ném `VaultChuaSanSang`; tạo khoá lần đầu bằng `O_EXCL`/`os.link` |
| 27 | `captcha-solver/src/workspaces.py` | `d879b03` | Chỉnh `# W2A:` | Gắn dependency kiểm tra tính năng `can_tinh_nang` bảo vệ các tuyến `/v1/workspaces` |

---

## 6. Danh sách chỉnh sửa chi tiết so với c2a (Đánh dấu `# W2A:`)

Tất cả các điểm điều chỉnh tối thiểu nhằm thích ứng với kiến trúc `web2api` đều được gắn chú thích `# W2A:`:

1. **`src/settings.py`**:
   - Bỏ giá trị mặc định `"change-me"` cho khoá API để ngăn chặn rủi ro lộ bí mật.
   - Bổ sung thư mục khoá riêng biệt `key_dir` (`/khoa`, nạp từ `CAPTCHA_SOLVER_KEY_DIR`).
   - Bổ sung hàm `lay_khoa_dich_vu()` tự sinh khoá an toàn tại `<key_dir>/khoa-dich-vu` (quyền 0600) theo cơ chế ghi nguyên tử; ném `RuntimeError` tiếng Việt khi tệp rỗng hoặc sai chuẩn, không tự ý ghi đè.
   - Thêm công tắc cấu hình tính năng `tinh_nang_bat` (mặc định chỉ bật `dang_nhap`, `lo_bi_mat` tắt).
   - Thêm công tắc `bat_novnc: bool = False` (mặc định tắt noVNC).
2. **`src/vault.py`**:
   - Bổ sung cơ chế tự sinh khoá mã hoá `vault.key` quyền `0600` trong `key_dir` (thay vì `data_dir`) theo cơ chế ghi nguyên tử (tệp tạm rồi `os.link`/`os.replace` dùng `O_EXCL`).
   - Cơ chế đọc khoá: nếu tệp `vault.key` rỗng hoặc sai chuẩn, ném `RuntimeError` tiếng Việt dừng dịch vụ để bảo vệ dữ liệu đã mã hoá, không ghi đè khoá mới.
   - Nếu `accounts.db` đã có bản ghi tiền tố `v1:` mà không có khoá → KHÔNG sinh khoá mới, ném `VaultChuaSanSang` (bảo vệ mật khẩu cũ không bị ghi đè khoá vô nghĩa).
   - Cơ chế giải mã `giai_ma()` không tự động tạo khoá mới nếu mất khoá (trả về chuỗi rỗng an toàn).
3. **`src/main.py`**:
   - Xác thực `require_api_key` sử dụng `hmac.compare_digest` chống tấn công định thời (timing attacks).
   - Kiểm tra khoá fail-fast trong `lifespan`: dừng khởi động nếu khoá sai định dạng, tự sinh tệp khoá 0600 trong `key_dir` nếu chưa cấu hình.
   - Tắt các endpoint tài liệu (`docs_url=None`, `redoc_url=None`, `openapi_url=None`).
   - Tắt middleware CORS mở rộng toàn bộ (`allow_origins=["*"]`).
   - Endpoint `GET /health` là ngoại lệ mở duy nhất có chú thích lý do rõ ràng; khai danh tính dịch vụ `may-dang-nhap` và báo trạng thái degraded/503 khi két thiếu khoá; chỉ trả URL `novnc` khi bật qua biến `CAPTCHA_SOLVER_BAT_NOVNC=1`.
   - Tuyến đường `DELETE /v1/profiles/{profile}` kiểm tra an toàn bằng `_PROFILE_SLUG` và `resolve()` kiểm tra relative_to để chống path traversal.
   - Tích hợp bộ kiểm soát công tắc tính năng `can_tinh_nang(...)` và `LoiTinhNangTat` trả lỗi 403 chuẩn hoá; xếp các tuyến đường `/reveal` và `/totp` vào nhóm `lo_bi_mat` (tắt mặc định).
   - Vô hiệu hoá mặc định việc tự động đẩy token ra ngoài.
4. **`src/accounts_db.py`**:
   - Đảm bảo quyền `0600` rõ ràng cho `accounts.db` khi tạo mới hoặc mở kết nối `_conn()`.
5. **`src/auto_login.py`**:
   - Thêm cổng kiểm tra nhóm tính năng `giai_captcha` trước khi gọi giải captcha âm thanh `solve_recaptcha_v2_tren_trang`.
6. **`src/workspaces.py`**:
   - Truyền dependency kiểm tra tính năng `can_tinh_nang` vào `create_router` để bảo vệ các tuyến đường `/v1/workspaces`.
7. **`src/gemini_web_login.py`**:
   - Loại bỏ ký tự Byte Order Mark (BOM `\ufeff`) ở đầu tệp để tương thích hoàn toàn với Python 3.13.

---

## 7. Các điểm CHƯA kiểm tra trong môi trường hộp cát

Do các ràng buộc cách ly của môi trường sandbox phát triển:
1. **Chưa dựng ảnh Docker thật**: Môi trường hộp cát không có Docker daemon (`docker build` và `docker compose up` chưa thực hiện trực tiếp).
2. **Chưa chạy trình duyệt thật**: Các gói trình duyệt nhân Chromium, Firefox và màn hình ảo Xvfb/x11vnc/noVNC chưa chạy thật (được mock bằng `_gia_goi.py` phục vụ unit test).
3. **Chưa thực hiện đăng nhập thật**: Chưa thực hiện các luồng đăng nhập thật với các nhà cung cấp bên ngoài (Google, OpenAI, Claude, Gemini, Grok) do môi trường không có kết nối Internet ra ngoài.

---

## 8. Gói bên thứ ba (đã đọc mã/giấy phép 19/10/2026; chỉ ghim bản + hash, không vào requirements của lõi)

| Gói | Giấy phép | Nhị phân | Mạng lúc cài / lúc chạy |
|---|---|---|---|
| patchright 1.63.0 | Apache-2.0 (bản vá của bên thứ ba trên Playwright của Microsoft) | Có sẵn trình điều khiển Node (~143 MB giải nén); JS đã đóng gói, KHÔNG đọc hết được | Chỉ `patchright install` tải trình duyệt từ `cdn.playwright.dev`/`playwright.download.prss.microsoft.com`; grep không thấy máy chủ thu thập số liệu |
| playwright 1.63.0 | Apache-2.0 | như trên (cloakbrowser 0.6.0 gọi gói này thật) | như trên |
| cloakbrowser 0.6.0 | MIT (phần bao Python) | Chromium vá tải về từ `cloakbrowser.dev` hoặc GitHub Releases (nhị phân ĐÓNG, giấy phép riêng của nhị phân CHƯA kiểm); gói tự xác minh chữ ký Ed25519 | Tự cập nhật nền + hỏi PyPI mỗi tiến trình (TẮT bằng `CLOAKBROWSER_AUTO_UPDATE=false`, đã đặt trong Dockerfile); máy chủ cấp phép chỉ gọi khi có khoá Pro; tra IP công cộng chỉ khi `geoip=True` (c2a không bật) |
| pyotp 2.10.0 | MIT | không | không gọi mạng |
| pydantic-settings 2.15.0 | MIT | không | không gọi mạng (đọc `.env` tại thư mục chạy nếu có) |
| playwright-recaptcha 0.5.1 (chỉ khi `CAI_GIAI_CAPTCHA=1`) | MIT | không; kéo SpeechRecognition 3.10.4 (có `flac` dựng sẵn, 44,7 MB), pydub, tenacity, requests | `recognize_google` GỬI ÂM THANH thử thách tới `www.google.com/speech-api` — TẮT ở BA lớp: build-arg `CAI_GIAI_CAPTCHA`, nhóm `giai_captcha`, và cổng trong luồng đăng nhập, chủ máy phải duyệt trước khi bật |

---

## 9. Tệp c2a CỐ Ý không chép

Các tệp sau từ kho `chatgpt2api` (thư mục `captcha-solver/` và liên quan) cố ý không được chép vào dịch vụ `may-dang-nhap`:

- **Các kịch bản dò Flow một lần**: `capture_body.py`, `capture_real_body.py`, `capture_real_v2.py`, `capture_route.py`, `debug_fetch.py`, `debug_fetch2.py`, `find_api_fields.py`, `log_requests.py`, `probe_api.py`, `probe_api_v2.py`, `test_flow.py`
  - *Lý do*: Kịch bản dò dùng một lần trong quá trình phân tích API ban đầu của tác giả c2a, chứa dữ liệu/thông tin thăm dò tạm thời của nhà, không thuộc thành phần chạy của dịch vụ web.
- **Công cụ dòng lệnh CLI**: `src/cli.py`, `bin/cs-cli`
  - *Lý do*: Giao diện dòng lệnh cục bộ không phục vụ dịch vụ API container của web2api.
- **Kịch bản phân tích & thử nghiệm khác**: `sniff_flow.py`, `test_onboard.py`
  - *Lý do*: Kịch bản thử nghiệm cục bộ một lần, chứa dữ liệu thử nghiệm nhà.
- **README gốc của c2a**:
  - *Lý do*: Tài liệu riêng của c2a, được thay thế bằng tài liệu chuẩn hoá riêng của dịch vụ trong web2api (`README.md` này).
- **Giao diện frontend c2a**: `web/src`
  - *Lý do*: Giao diện web riêng của c2a không thuộc phạm vi dịch vụ backend API; web2api sử dụng hệ thống giao diện tích hợp riêng.
