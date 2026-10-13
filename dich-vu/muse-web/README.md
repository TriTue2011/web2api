# `muse-web`: Công cụ dòng lệnh giao việc cho agent Muse (muse.ai)

Công cụ dòng lệnh mỏng cho phép bất kỳ agent nào (Claude Code, agy, tập lệnh shell) giao việc trực tiếp cho agent Muse (muse.ai) tương tự `agy -p`, **KHÔNG ép Muse thành model chat OpenAI thông thường**.

Muse bản thân là một agent có máy ảo riêng, connectors và tác vụ chạy ngầm. `muse-web` đứng trên giao thức trực tiếp của `modules/muse2api/` (`protocol.py` và `chat.py`) để giao việc, chờ kết quả và lấy lịch sử.

> [!NOTE]
> `muse-web` là công cụ chạy **TRONG bản kiểm của kho web2api** (tự động nhận `modules/muse2api/` qua đường dẫn của kho), không phải gói cài đặt độc lập trên PyPI.
>
> Chạy từ **gốc kho** bằng Python của môi trường kiểm: `./.venv/bin/python dich-vu/muse-web/muse_web.py …`. Môi trường đó phải có `curl_cffi`, `noiseprotocol` và `protobuf` (đã ghim trong `requirements.txt`; cài bằng `uv pip install -r requirements.txt`). Thiếu gói thì lệnh trả mã `5` kèm tên gói thiếu.

---

## 1. Lưu trữ và Quản lý Cookie An toàn

- **Thư mục kho cookie**: Đọc từ biến môi trường `$MUSE_WEB_DIR` (mặc định là `~/.config/muse-web`).
- **Phân quyền nghiêm ngặt** (sai bất kỳ điều nào → mã `2`, kèm cách sửa):
  - Thư mục kho `$MUSE_WEB_DIR` và thư mục `$MUSE_WEB_DIR/khoa/` phải là **thư mục thật** (không phải liên kết tượng trưng), **thuộc người dùng đang chạy**, và được đặt quyền `0700` (`drwx------`). Không đặt được quyền thì báo lỗi, không lặng lẽ bỏ qua.
  - Tệp dữ liệu `$MUSE_WEB_DIR/cookies.json` phải là tệp thường (không phải liên kết tượng trưng), thuộc người dùng đang chạy, quyền `0600` (`-rw-------`). Quyền rộng hơn thì lệnh từ chối đọc và chỉ cách sửa (`chmod 600 ...`). Mọi phép kiểm làm trên chính tệp đã mở (`O_NOFOLLOW` + `fstat`) nên không bị tráo giữa lúc kiểm và lúc đọc.
  - Nội dung `cookies.json` phải đúng cấu trúc `{"tai_khoan": [{"ten": chuỗi, "cookies": chuỗi}, …]}`; sai cấu trúc hay không phải JSON → mã `2`.
- **Ghi nguyên tử và có khoá**: `cookie them`/`cookie xoa` giữ khoá `flock` độc quyền trên `$MUSE_WEB_DIR/.kho.lock` suốt chu trình đọc-sửa-ghi (hai lệnh chạy cùng lúc không ghi đè mất tài khoản của nhau). Bản ghi ra tệp tạm cùng thư mục, `fsync` rồi đổi tên nguyên tử bằng `os.replace`, không bao giờ để lại tệp hỏng hoặc tệp tạm.
- **Bảo mật dòng lệnh**: **TUYỆT ĐỐI không nhận cookie qua tham số dòng lệnh** (để tránh lộ cookie trong danh sách tiến trình `ps` và lịch sử shell). Cookie chỉ được nạp qua `stdin` (gõ tương tác thì không hiện chữ) hoặc qua tệp `--tep`.

---

## 2. Các Lệnh

Mọi lệnh đều xuất **MỘT đối tượng JSON ra stdout** (tiếng Việt có dấu, `ensure_ascii=False`). Khi có cờ `--dong`, xuất từng dòng JSON (JSONL).

### 2.1. Quản lý cookie (`cookie`)

- **Thêm/cập nhật cookie** (trùng tên thì cập nhật, không thêm dòng mới):
  ```bash
  # Nhập tương tác (khuyên dùng): dán cookie khi được hỏi — không hiện chữ, không vào lịch sử shell
  ./.venv/bin/python dich-vu/muse-web/muse_web.py cookie them --ten acc1

  # Hoặc đọc từ tệp riêng quyền 0600 (soạn bằng trình soạn thảo, ĐỪNG dùng `echo "cookie…"`
  # vì chuỗi sẽ vào lịch sử shell):
  install -m 600 /dev/null ~/cookie-acc1.txt && "${EDITOR:-nano}" ~/cookie-acc1.txt
  ./.venv/bin/python dich-vu/muse-web/muse_web.py cookie them --ten acc1 --tep ~/cookie-acc1.txt
  ```
  Tên tài khoản phải khớp định dạng `^[a-z0-9][a-z0-9_-]{0,31}$`. Cookie rỗng, chứa ký tự xuống dòng giữa chừng, ký tự điều khiển hoặc không phải UTF-8 sẽ bị từ chối (mã `2`).

- **Xem danh sách tài khoản**:
  ```bash
  ./.venv/bin/python dich-vu/muse-web/muse_web.py cookie ds
  ```
  Kết quả: danh sách `[{"ten": "acc1", "khoa": "…abcd"}]` — chỉ hiển thị 4 ký tự cuối của giá trị cookie đầu tiên theo quy ước che bí mật của kho.

- **Xóa cookie** (tên không có trong kho → mã `2`):
  ```bash
  ./.venv/bin/python dich-vu/muse-web/muse_web.py cookie xoa --ten acc1
  ```

### 2.2. Kiểm tra tài khoản (`kiem`)

Chạy thử `fetch_access_token` và `fetch_session_info` (rẻ, không tốn lượt chat):
```bash
./.venv/bin/python dich-vu/muse-web/muse_web.py kiem [--tai-khoan TÊN]
```
Kết quả: `[{"ten": "...", "ok": true|false, "loi": null|"..."}]`. Mã thoát `0` nếu có ít nhất một tài khoản hoạt động; `4` nếu mọi tài khoản bị từ chối xác thực; `5` nếu không tài khoản nào ổn và có ít nhất một lỗi mạng/máy chủ.

### 2.3. Giao việc cho agent (`gui`)

Gửi nội dung việc **NGUYÊN VĂN** tới Muse (không bọc transcript dạng `User:` / `Assistant:`):
```bash
# Gửi trực tiếp qua đối số:
./.venv/bin/python dich-vu/muse-web/muse_web.py gui "Viết báo cáo phân tích hiệu năng hệ thống"

# Hoặc đọc từ tệp / stdin (UTF-8):
cat task.txt | ./.venv/bin/python dich-vu/muse-web/muse_web.py gui -
./.venv/bin/python dich-vu/muse-web/muse_web.py gui --tep /duong/dan/task.txt

# Chế độ stream từng dòng delta (JSONL):
./.venv/bin/python dich-vu/muse-web/muse_web.py gui --dong "Viết kịch bản kiểm thử"
```

> [!WARNING]
> `gui --tep FILE` (và `gui -`) gửi **NỘI DUNG** tệp đó cho bên thứ ba (muse.ai). Đừng đưa tệp chứa bí mật (khoá, cookie, `.env`, dữ liệu riêng).

Các tùy chọn:
- `--tai-khoan TÊN`: Chỉ định đích danh tài khoản. Nếu không truyền, hệ thống tự động chọn tài khoản rảnh đầu tiên.
- `--cho GIÂY`: Thời gian chờ tối đa cho câu trả lời — số hữu hạn `> 0`, mặc định 300; vượt 1800 thì kẹp về 1800. `nan`, `inf`, `0`, số âm → mã `2` và **không gửi gì**.
- `--cho-ranh GIÂY`: Thời gian chờ tài khoản rảnh nếu đang bị khóa flock — số hữu hạn `≥ 0`, mặc định 0; vượt 3600 thì kẹp về 3600. `nan`, `inf`, số âm → mã `2`.
- `--dong`: Xuất từng đoạn chữ `{"delta": "…"}` ngay khi nhận được, dòng cuối cùng là `{"ok": true, "xong": true, "tai_khoan": "…", "giay": …}`.

**Lỗi sau khi việc đã gửi.** JSON lỗi của `gui` với mã `3`, `4`, `5` luôn có trường `"viec_da_gui": true|false`:
- `true` nghĩa là việc **ĐÃ** tới Muse và có thể vẫn đang chạy. **Đừng gửi lại** (sẽ giao việc hai lần); dùng `lich-su` để lấy kết quả.
- Ở chế độ thường, nếu đã nhận được một phần chữ trước khi đứt, JSON lỗi kèm `"mot_phan": "<chữ đã nhận>"`. Ở chế độ `--dong` phần chữ đã được in thành các dòng `delta` trước dòng lỗi.

### 2.4. Đọc lịch sử cuộc trò chuyện (`lich-su`)

```bash
./.venv/bin/python dich-vu/muse-web/muse_web.py lich-su [--gioi-han 15] [--tai-khoan TÊN] [--cho-ranh GIÂY]
```
Kết quả: `[{"seq": 1, "vai": "user"|"assistant", "van_ban": "…"}]` (sắp xếp theo `seq`, tự động bỏ qua các sự kiện hệ thống hoặc sự kiện không có văn bản). `--gioi-han` từ 1 đến 100.

---

## 3. Điều Phối Nhiều Tài Khoản và Khóa Tệp

- **Khóa tệp điều phối (`flock`)**: Mỗi tài khoản được bảo vệ bởi một khóa tệp `fcntl.flock(fd, LOCK_EX | LOCK_NB)` tại `$MUSE_WEB_DIR/khoa/<tên>.lock`. Khóa được giữ suốt thời gian thực thi của lượt và được hệ điều hành tự động giải phóng nếu tiến trình bị hủy đột ngột.
- **Xoay vòng tài khoản rảnh**: Khi không chỉ định `--tai-khoan`, lệnh tự động thử các tài khoản rảnh. Nếu tất cả đều bận, lệnh trả về mã thoát `6` (hoặc chờ theo `--cho-ranh`).
- **Chuyển tài khoản khi gặp `AuthError`**: `gui` gặp lỗi xác thực **trước khi gửi việc** thì tự chuyển sang tài khoản kế tiếp. `lich-su` (chỉ đọc) gặp lỗi xác thực thì cũng chuyển sang tài khoản kế tiếp.
- **Bảo vệ không giao việc hai lần**: Một khi việc đã được gửi (`sess.da_gui` là True), hệ thống **tuyệt đối không chuyển sang tài khoản khác và không nối lại để gửi lần nữa**.
- **Nối lại khi đứt kết nối**: Nếu kết nối WebSocket bị đóng **trước khi** gửi việc, hệ thống đóng kết nối cũ và tạo kết nối mới thử lại đúng một lần.
- **Bắt tín hiệu an toàn**: Nhận `SIGINT` (Ctrl+C) hoặc `SIGTERM` sẽ kích hoạt cờ hủy (`co_huy`), đóng kết nối WebSocket, nhả khóa và thoát với mã `130` hoặc `143`. Nếu việc đã gửi trước khi bị ngắt, Muse vẫn có thể đang chạy — xem bằng `lich-su`.

---

## 4. Bảng Mã Thoát và Định Dạng Lỗi

| Mã thoát | Ý nghĩa | Định dạng JSON phản hồi |
|---|---|---|
| **0** | Thành công | Tuỳ theo lệnh (`ok: true`, danh sách, v.v.) |
| **2** | Dùng sai cú pháp / đầu vào sai / kho chưa có tài khoản / kho sai quyền, sai chủ, là liên kết tượng trưng hoặc sai cấu trúc | `{"ok": false, "ma": "yeu_cau_sai", "loi": "…"}` |
| **3** | Hết giờ chờ trả lời (quá thời hạn `--cho`) | `{"ok": false, "ma": "het_gio", "loi": "…", "viec_da_gui": …}` |
| **4** | Mọi cookie bị từ chối / hết hạn (cần xuất lại cookie) | `{"ok": false, "ma": "khoa_hong", "loi": "…", "viec_da_gui": …}` |
| **5** | Lỗi kết nối / lỗi máy chủ muse.ai / thiếu gói import | `{"ok": false, "ma": "loi_ket_noi", "loi": "…", "viec_da_gui": …, "mot_phan": "…"}` |
| **6** | Không có tài khoản rảnh (tất cả tài khoản đều bận) | `{"ok": false, "ma": "khong_co_tai_khoan_ranh", "loi": "…"}` |
| **130** / **143** | Bị ngắt bởi `SIGINT` / `SIGTERM` | (không in JSON) |
| **141** | Người đọc đóng stdout giữa chừng (ống dẫn vỡ) | (không in được gì thêm) |

Trường `viec_da_gui` chỉ có ở lỗi của `gui`; `mot_phan` chỉ có khi đã nhận được chữ (chế độ thường). Với `gui`, mã `3`/`4`/`5` **không** có nghĩa là việc chưa chạy — xem `viec_da_gui`.

> [!IMPORTANT]
> Mọi thông báo lỗi đều được dựng từ **CẤU TRÚC** (kiểu ngoại lệ, mã trạng thái HTTP, tên tham số). Nội dung phản hồi thô thượng nguồn, cookie, và **mọi giá trị người gọi đưa vào** (đối số, tên tài khoản, đường dẫn `--tep`, nội dung stdin) không bao giờ được chép vào thông báo lỗi — gõ nhầm cookie vào sai chỗ cũng không bị in ra.

---

## 5. Lưu Ý và Cảnh Báo

1. **Cuộc trò chuyện dùng chung**: Mỗi tài khoản Muse có **MỘT cuộc trò chuyện chính dùng chung**. Mọi nhiệm vụ giao qua CLI sẽ xuất hiện trong lịch sử trò chuyện trực tiếp của tài khoản Muse đó trên giao diện web.
2. **Độ trễ phản hồi**: Do cơ chế polling định kỳ của giao thức web, độ trễ tối thiểu cho mỗi lượt hoàn tất khoảng 12 giây.
3. **Cắt đoạn khi im lặng**: Nếu agent im lặng quá 10 giây giữa hai tin nhắn liên tiếp, lượt nhận kết quả sẽ được coi là đã hoàn thành.
4. **Dữ liệu ra bên thứ ba**: Nội dung việc (kể cả nội dung tệp `--tep`) được gửi tới muse.ai.
5. **Giao thức không chính thức**: Giao thức nội bộ của web app muse.ai được đảo ngược (reverse-engineered) và không chính thức. Điều khoản sử dụng của dịch vụ có thể cấm reverse engineering — công cụ phục vụ mục đích nghiên cứu và sử dụng cá nhân, người dùng tự chịu rủi ro.
