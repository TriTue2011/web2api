# Backlog skill cho MCP sau này

Danh sách chờ, xếp theo ưu tiên. Chưa có nội dung chi tiết của các skill này
(chỉ có mô tả ngoài) nên mục rủi ro ghi rõ **«chưa đọc mã»** — không bịa chi tiết bên trong.

## Ưu tiên cao

- **docs-seeker** — Dùng khi: tra cứu tài liệu thư viện/API lúc viết module mới.
  Rủi ro: truy vấn ra mạng, có thể nhận tài liệu sai/thiếu; chưa đọc mã.
  Trước khi cài: xác minh nguồn tài liệu là chính chủ.
- **web-testing** — Dùng khi: kiểm thử trình duyệt/tải (Playwright, k6); kho đã có test Chrome.
  Rủi ro: cài trình duyệt và gói ngoài, chạy mã trên máy; chưa đọc mã.
  Trước khi cài: đọc mã, ghim đúng phiên bản.
- **chrome-devtools** — Dùng khi: điều khiển Chrome thật (Puppeteer) phục vụ test giao diện.
  Rủi ro: mã chạy với quyền trình duyệt, có thể chạm dữ liệu duyệt web; chưa đọc mã.
  Trước khi cài: đọc mã, ghim đúng phiên bản.

## Ưu tiên trung bình

- **devops** — Dùng khi: thao tác Docker/Cloudflare; kho có Dockerfile và Cloudflare Worker proxy.
  Rủi ro: đổi nhầm hạ tầng, lộ secret triển khai; chưa đọc mã.
  Trước khi cài: đọc mã, chạy khô trước khi chạm hạ tầng thật.
- **databases** — Dùng khi: vượt quá khả năng JSON/SQLite (kho đang dùng JSON/SQLite, có ý định FTS5).
  Rủi ro: di trú dữ liệu sai, thêm phụ thuộc nặng (Mongo/Postgres); chưa đọc mã.
  Trước khi cài: đánh giá xem SQLite/FTS5 có thật sự không đủ không.
- **ai-multimodal** — Dùng khi: xử lý ảnh/âm thanh qua Gemini; kho đã có module gemini.
  Rủi ro: khoá API đi ra mạng, chi phí gọi. ĐÃ ĐỌC MỘT TỆP (17/10): `scripts/media_optimizer.py:107` gọi `eval()` trên
  giá trị `r_frame_rate` do ffprobe đọc từ tệp media — tệp media độc có thể chạy mã. KHÔNG cài khi chưa thay bằng phân tích
  an toàn (tách `a/b` rồi chia); các tệp khác chưa đọc.
  Trước khi cài: đọc mã, kiểm tra phạm vi quyền của khoá.

## Ưu tiên thấp

- **mcp-builder** — Dùng khi: tự đóng gói máy chủ MCP cho nhu cầu riêng.
  Rủi ro: mã máy chủ chạy với quyền của máy; chưa đọc mã. Trước khi cài: đọc mã.
- **mcp-management** — Dùng khi: quản trị các MCP đã cài.
  Rủi ro: đổi sai cấu hình làm hỏng kênh đang chạy; chưa đọc mã. Trước khi cài: đọc mã.
- **skill-creator** — Dùng khi: soạn skill mới đúng chuẩn cho kho (như đợt NV95 này).
  Rủi ro thấp (sinh văn bản); chưa đọc mã. Trước khi cài: đọc mã.
- **repomix** — Dùng khi: đóng gói cả repo thành một tệp cho AI đọc (hữu ích khi giao việc cho Muse).
  Rủi ro chuỗi cung ứng: cần npm trong khi kho không dùng npm; chưa đọc mã.
  Trước khi cài: đọc mã, ghim đúng phiên bản, cân nhắc viết script nội bộ thay thế.

## Không hợp — bỏ

**shopify, threejs, payment-integration, better-auth, bunny, ui-styling, aesthetic,
web-frameworks, frontend-\*:** kho là cổng API/proxy đa mô hình, không bán hàng,
không làm giao diện web riêng hay đồ hoạ 3D, xác thực đã có cơ chế riêng —
các skill này không khớp phạm vi nên bỏ.

## Đã chọn và nhập (NV97, từ nguồn ecc)

**fastapi-patterns, python-testing, security-review, verification-loop, api-design**
(đã nhập vào `.claude/skills/`, Việt hoá + rút gọn, xem `THIRD_PARTY_NOTICES.md`):
chỉ 5 skill này khớp kho — FastAPI chuẩn OpenAI, pytest với máy chủ giả chặn mạng,
luật cứng bảo mật ở CLAUDE.md, vòng kiểm pytest/bandit/docker/curl — còn lại của
nguồn ecc (**613 skill, 71 agent, 26 hook**) KHÔNG nhập: phần lớn viết cho ngăn xếp
khác (Next.js/npm/Supabase/Vercel, bán hàng, đồ hoạ, 3D), gây thêm bề mặt rủi ro
chuỗi cung ứng mà kho không dùng đến; ràng buộc NV97 cũng cấm thêm hook, agent,
lệnh và cấu hình MCP.
