# muse2api — Module web2api cho Meta Model API

Gọi các model **Muse Spark** của Meta qua **Meta Model API chính thức**
(`https://api.meta.ai/v1`, tương thích OpenAI) bằng API key. Không mật khẩu,
không cookie phiên web, không scrape.

Tiền tố model: `muse2api` (ví dụ `muse2api/muse-spark-1.3`).

## Khả năng

| Khả năng | Phương thức | Ghi chú |
|---|---|---|
| Trò chuyện | `chat` | Stream + không stream, qua `POST /v1/chat/completions` |
| Tạo ảnh | `anh` | Qua `POST /v1/images/generations` |

Video: official API không có endpoint nên module không khai.

## Cấu hình

| Trường | Bắt buộc | Ý nghĩa |
|---|---|---|
| `api_keys` | Có | Một hoặc nhiều key từ Meta for Developers Console → Model API dashboard (biến `MODEL_API_KEY`). Nhiều khoá thì xoay vòng, khoá 429 được cho nghỉ. |
| `base_url` | Không | Mặc định `https://api.meta.ai/v1`. |

## Lấy key

Theo tài liệu `dev.meta.ai/docs/authentication`: đăng nhập Meta for Developers
Console → tạo team → vào Model API dashboard → Generate key.

## Ví dụ dùng

```bash
curl -s -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"model": "muse2api/muse-spark-1.3", "messages": [{"role": "user", "content": "Xin chào"}]}' \
  $W/v1/chat/completions
```

## Giới hạn

- Module gọi Model API (Muse Spark), không phải app Muse (muse.ai) — Meta không
  công bố public API cho threads/VM/connectors của Muse agent.
- Giá theo docs Meta: Standard $1.25/$4.25, Contributor $0.10/$0.20 mỗi 1M
  token (input/output). Bản Contributor cho phép Meta dùng dữ liệu cải thiện
  sản phẩm.
