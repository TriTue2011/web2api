---
name: verification-loop
description: Vòng kiểm chuẩn của web2api sau mỗi việc — pytest (xét mã thoát), bandit -r app modules -lll, dựng ảnh Docker, curl thử thật; một lệnh hỏng là dừng. Dùng sau khi xong một việc, trước khi commit, hoặc khi thấy chất lượng đáng ngờ.
---

# Vòng kiểm web2api

Lệnh ở `CLAUDE.md` mục «Cách kiểm». **Một lệnh hỏng là DỪNG**: không cam kết,
không đẩy, không tuyên bố xong. Chạy đủ cả bốn, theo đúng thứ tự này.

## Bước 1 — pytest (xét MÃ THOÁT, không xét cảm giác)

```bash
.venv/bin/python -m pytest test -q -p no:cacheprovider
echo $?    # 0 là xanh, khác 0 là đỏ
```

- Hỏng một test là dừng ở đây, không chạy tiếp để «xem còn hỏng gì».
- `test/test_web2api_readme.py` bắt lệch tài liệu ↔ mã: thêm đường API hay biến
  `WEB2API_*` thì ghi vào `docs/THAM-CHIEU.md` trước khi chạy lại.
- Sửa lỗi theo lớp: lỗi do khớp chuỗi bắt nhầm hay đọc sai kho dữ liệu thì sửa
  ở gốc, rồi `grep` mọi chỗ cùng lớp — không vá triệu chứng.

## Bước 2 — bandit (mức HIGH phải sạch)

```bash
bandit -r app modules -lll
```

- `app`, `modules` là mã kho; CI chặn nếu còn HIGH. Báo cáo khoan cắt: mức thấp
  (thiếu assert trong test chẳng hạn) là chuyện thường, chỉ HIGH là phải xử lý.
- Chạm xác thực/phiên/proxy/giao diện/cổng mạng/mã bên thứ ba thì song song chạy
  gitleaks và thêm đoạn «Bảo mật» vào thông điệp commit.

## Bước 3 — dựng ảnh Docker

```bash
docker build -t web2api:latest .
```

- Ảnh phải dựng được từ đầu; chỉ sửa `*.md` hoặc `docs/` mới được bỏ qua bước này
  (CI cũng không dựng lại trong trường hợp đó).

## Bước 4 — chạy thật và curl

```bash
docker compose up -d                     # chạy ở cổng 3031
curl -s http://127.0.0.1:3031/v1/models -H "Authorization: Bearer $KHOA" | head -c 400
```

- **Đo trên chạy thật trước khi tin bản sửa.** Test chỉ chứng minh mã chạy đúng
  như mình nghĩ, không chứng minh mình nghĩ đúng.
- Chi tiết smoke test ở `docs/THAM-CHIEU.md` mục 5. Chưa kiểm được thì nói
  «chưa kiểm», đừng tuyên bố xong.

## Mẫu báo cáo kiểm

```
KIỂM TRA <tên việc>
==================
pytest:    PASS (1 failed, 3052 passed, ...) — ghi đủ con số và mã thoát
bandit:    PASS (không HIGH) / FAIL (liệt kê)
docker build: PASS / FAIL
curl thật: PASS (GET /v1/models 200) / CHƯA KIỂM
→ ĐẠT / CHƯA ĐẠT
```

- Ghi nguyên con số test (passed/failed/skipped) và `echo $?` của từng lệnh.
- Không viết «đại khái xanh» hay «không có lỗi» mà không có mã thoát kèm theo.

## Trước khi commit — ba câu hỏi (ghi vào nội dung commit)

1. Lỗi này thuộc **lớp** nào?
2. Còn chỗ nào cùng lớp? **Đo bằng `grep`, đừng đoán** — ghi lệnh và số kết quả.
3. Sửa theo **nguyên tắc** hay theo **danh sách**?

Cấm: `git push --force`, `git reset --hard`, `--no-verify` để lách hook.
