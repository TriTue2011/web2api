# muse2api — Module web2api cho muse.ai (direct protocol)

Trò chuyện với Muse (muse.ai) qua **giao thức trực tiếp** của web app:
cookie → access_token → hatch token → WebSocket Noise XX tới máy ảo.
**Miễn phí, không cần API key, không cần browser.**

Tiền tố model: `muse2api` (model duy nhất: `muse2api/muse`).

## Khả năng

| Khả năng | Phương thức | Ghi chú |
|---|---|---|
| Trò chuyện | `chat` | Stream (SSE) + không stream |

## Cấu hình

| Trường | Bắt buộc | Ý nghĩa |
|---|---|---|
| `cookies` | Có | Một hoặc nhiều bộ cookie từ tab muse.ai đã đăng nhập, dạng `k1=v1; k2=v2`. Nhiều bộ thì xoay vòng. |

**Lấy cookie:** mở tab muse.ai đã đăng nhập → DevTools (F12) →
Application → Cookies → copy các giá trị (`hatch_sess`, `hatch_gw`,
`datr`, …) ghép thành chuỗi `k1=v1; k2=v2`.

Lúc **Cài đặt**, module kiểm từng bộ cookie bằng HTTPS bootstrap
(`auth/check` + `session`) — không tốn tiền, không cần chat thật.

## Ví dụ dùng

```bash
curl -s -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"model": "muse2api/muse", "messages": [{"role": "user", "content": "Xin chào"}]}' \
  $W/v1/chat/completions
```

## Giới hạn

- Giao thức nội bộ đã reverse-engineer, không chính thức — Meta đổi web
  app có thể làm module ngừng hoạt động.
- Stream là "giả lập" theo kiểu poll lịch sử mỗi ~2s (không phải token-by-token).
- Các request trên cùng một bộ cookie xếp hàng tuần tự và dùng chung
  main chat — thêm cookie để song song.
- Điều khoản của Muse cấm reverse engineering — dùng cá nhân, tự chịu rủi ro.
- Cookie chính là tài khoản của bạn: không commit, không chia sẻ.
