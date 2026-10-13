---
name: muse-web
description: Giao nhiệm vụ phức tạp cho agent Muse (muse.ai) có máy ảo riêng, connectors và tác vụ chạy ngầm. KHÔNG dùng cho hỏi đáp ngắn hoặc chat thông thường.
---

# Kỹ năng giao việc cho agent Muse qua `muse-web`

## Khi nào nên dùng
- Cần giao việc lớn, nghiên cứu đa bước hoặc tác vụ chạy ngầm trên máy ảo riêng của Muse.
- **KHÔNG dùng** cho các câu hỏi đáp ngắn, dịch thuật đơn giản hay hội thoại thông thường (hãy dùng các model chat tiêu chuẩn).

## Cách gọi lệnh

Chạy từ **gốc kho web2api** bằng Python của môi trường kiểm (`./.venv/bin/python`). Môi trường đó cần `curl_cffi`, `noiseprotocol`, `protobuf` (ghim trong `requirements.txt`).

### 1. Giao việc cơ bản (nhận JSON đầy đủ)
```bash
./.venv/bin/python dich-vu/muse-web/muse_web.py gui "Nội dung việc cần giao cho Muse"
```
Đầu ra stdout là JSON:
```json
{
  "ok": true,
  "van_ban": "Kết quả do agent Muse trả lời...",
  "tai_khoan": "acc1",
  "giay": 15.4
}
```

### 2. Giao việc có stream (nhận JSONL)
```bash
./.venv/bin/python dich-vu/muse-web/muse_web.py gui --dong "Nội dung việc"
```
Mỗi dòng delta trả về dạng `{"delta": "..."}` và dòng kết thúc là `{"ok": true, "xong": true, "tai_khoan": "...", "giay": ...}`.

### 3. Giao việc qua tệp hoặc stdin
```bash
cat task.txt | ./.venv/bin/python dich-vu/muse-web/muse_web.py gui -
./.venv/bin/python dich-vu/muse-web/muse_web.py gui --tep task.txt
```
**Cảnh báo:** `gui --tep FILE` gửi **NỘI DUNG** tệp đó cho bên thứ ba (muse.ai). Đừng đưa tệp chứa bí mật (khoá, cookie, `.env`, dữ liệu riêng).

### 4. Đọc lịch sử
```bash
./.venv/bin/python dich-vu/muse-web/muse_web.py lich-su --gioi-han 15
```

## Bảng mã thoát
- `0`: Thành công.
- `2`: Tham số sai / cú pháp sai / kho chưa có tài khoản (chạy `cookie them`) / kho cookie không an toàn hoặc hỏng.
- `3`: Hết giờ chờ trả lời (quá thời hạn `--cho`, mặc định 300s).
- `4`: Mọi cookie bị từ chối hoặc hết hạn (cần xuất cookie mới).
- `5`: Lỗi kết nối mạng hoặc lỗi máy chủ `muse.ai`.
- `6`: Không có tài khoản rảnh (đang bị khóa bởi lượt khác).
- `130` / `143`: Bị ngắt bằng `SIGINT` / `SIGTERM` (không in JSON). Việc có thể đã gửi — xem bằng `lich-su`.
- `141`: stdout bị đóng giữa chừng.

Khi có lỗi, stdout trả về JSON:
```json
{"ok": false, "ma": "het_gio|khoa_hong|loi_ket_noi|yeu_cau_sai|khong_co_tai_khoan_ranh", "loi": "..."}
```

### Mã 3/4/5 của `gui` CÓ THỂ nghĩa là việc ĐÃ gửi
JSON lỗi của `gui` có trường `"viec_da_gui"`:
- `true`: việc đã tới Muse và có thể vẫn đang chạy. **ĐỪNG gửi lại** (sẽ giao việc hai lần) — chờ rồi dùng `lich-su` để lấy kết quả.
- `false`: việc chưa tới Muse, gửi lại an toàn.

Nếu đã nhận được một phần chữ trước khi đứt, JSON lỗi kèm `"mot_phan": "..."`.

## Lưu ý và Cảnh báo quan trọng
1. **Độ trễ tối thiểu**: Mỗi lượt hoàn tất mất tối thiểu ≈ 12 giây do cơ chế poll lịch sử web app.
2. **Cắt đoạn khi im lặng**: Agent Muse nếu im lặng quá 10 giây giữa hai thông điệp thì lượt trả lời được coi là kết thúc.
3. **Cuộc trò chuyện chung**: Mỗi tài khoản chỉ có một luồng trò chuyện duy nhất; mọi việc giao đều lưu vào lịch sử thật của tài khoản đó trên giao diện web.
4. **Bảo mật tuyệt đối**: **KHÔNG BAO GIỜ** dán cookie, token hay email vào lệnh, thông báo, nhật ký commit hay tài liệu.
5. **Rủi ro giao thức**: Giao thức nội bộ được reverse-engineer từ web app, không chính thức; điều khoản của Muse cấm reverse engineering — tự chịu rủi ro khi sử dụng.
