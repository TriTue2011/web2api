# muse2api — Module web2api cho muse.ai (direct protocol)

Trò chuyện với Muse (muse.ai) qua **giao thức trực tiếp** của web app:
cookie → access_token → hatch token → WebSocket Noise XX tới máy ảo.
**Miễn phí, không cần API key, không cần browser.**

Tiền tố model: `muse2api` (model duy nhất: `muse2api/muse`).

## Khả năng

| Khả năng | Phương thức | Ghi chú |
|---|---|---|
| Trò chuyện | `chat` | Stream (SSE) + không stream |

## Cần gì

- Gói Python `curl_cffi`, `noiseprotocol`, `protobuf` phải có trong ảnh (đã nằm trong `requirements.txt`).
- Một hoặc nhiều bộ cookie từ tab muse.ai đã đăng nhập (DevTools F12 → Application → Cookies), dạng `k1=v1; k2=v2`.
- Mạng ra tới `muse.ai` và `hatch.metaaivm.com`.

## Cấu hình

| Trường | Bắt buộc | Ý nghĩa |
|---|---|---|
| `cookies` | Có | Một hoặc nhiều bộ cookie từ tab muse.ai đã đăng nhập, dạng `k1=v1; k2=v2`. Nhiều bộ thì xoay vòng. |

**Lấy cookie:** mở tab muse.ai đã đăng nhập → DevTools (F12) →
Application → Cookies → copy các giá trị (`hatch_sess`, `hatch_gw`,
`datr`, …) ghép thành chuỗi `k1=v1; k2=v2`.

Hoặc **đăng nhập ngay trên server** bằng module `vnc-xa` (tab «Trình duyệt»): tạo workspace,
đăng nhập muse.ai trong Firefox chạy trên server, «Đóng · giữ phiên», rồi «Lấy cookie sang module»
vào trường `cookies` — không phải chép cookie bằng tay (xem `dich-vu/trinh-duyet/README.md`). Manifest khai
`"nhan_cookie": {"mien": "muse.ai"}` trên trường `cookies`: chỉ trường này nhận cookie, và chỉ cookie của `muse.ai`.

Lúc **Cài đặt**, module kiểm từng bộ cookie bằng HTTPS bootstrap
(`auth/check` + `session`) — không tốn tiền, không cần chat thật.

## Ví dụ dùng

```bash
curl -s -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"model": "muse2api/muse", "messages": [{"role": "user", "content": "Xin chào"}]}' \
  $W/v1/chat/completions
```

## Sự cố thường gặp

- `401` «Cookie không hợp lệ/hết hạn»: đăng nhập lại muse.ai trên trình
  duyệt rồi export lại cookie. Có nhiều bộ cookie thì module tự chuyển sang
  bộ kế khi lời nhắn chưa gửi; bộ bị từ chối vẫn được thử lại ở lượt sau và
  khoẻ lại ngay khi trả lời được.
- `502` «Lỗi máy chủ hoặc kết nối: …»: máy ảo chưa sẵn sàng hoặc WebSocket
  hỏng — đợi chút rồi thử lại. Thông báo chỉ nêu kiểu lỗi và mã trạng thái;
  nội dung phản hồi của muse.ai chỉ ghi vào log máy chủ. Kết nối cũ chết
  TRƯỚC khi gửi lời nhắn thì module tự nối lại một lần; chết SAU khi đã gửi
  thì trả `502`, không nối lại để khỏi hỏi Muse hai lần.
- `504` «Hết hạn chờ muse.ai»: bắt tay Noise quá 20 giây, cổng im quá 30
  giây lúc đọc mốc lịch sử (trước khi gửi), hoặc không có câu trả lời trong
  300 giây (`wait_s`) — trợ lý có thể vẫn đang làm, xem lại lịch sử chat sau.

## Giới hạn

- Giao thức nội bộ đã reverse-engineer, không chính thức — Meta đổi web
  app có thể làm module ngừng hoạt động.
- Độ trễ tối thiểu ≈ 12 giây mỗi lượt (2 giây poll + 10 giây im lặng để chắc đã hết), tin cách quãng quá 10 giây có thể bị cắt.
- Lỗi giữa stream kết thúc bằng sự kiện `error` không có `[DONE]`.
- Các request trên cùng một bộ cookie xếp hàng tuần tự và dùng chung
  main chat — thêm cookie để song song.
- Điều khoản của Muse cấm reverse engineering — dùng cá nhân, tự chịu rủi ro.
- Cookie chính là tài khoản của bạn: không commit, không chia sẻ.
