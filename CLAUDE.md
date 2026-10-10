# web2api — hướng dẫn cho phiên Claude làm việc trong kho này

## Dự án là gì

web2api là một **cổng AI nói chuẩn OpenAI** (`/v1/models`, `/v1/chat/completions`, có stream) chạy trong
một container Docker nhỏ. **Lõi không chứa sẵn nguồn AI nào**: mỗi nguồn là một *module* nằm ở
`modules/<id>/` của kho git này, cài lúc chạy qua git, không dựng lại ảnh, không khởi động lại.
Kho: `TriTue2011/web2api`, **PUBLIC**. README.md là tài liệu cho người dùng; file này là luật cho người sửa mã.

## Bản đồ thư mục

| Đường | Nội dung |
|---|---|
| `app/` | Lõi FastAPI. `main.py` khởi động; `tuyen.py` các đường API; `kham_pha.py` khám phá module (hợp đồng module ghi ở đầu file); `so_module.py` sổ module đang chạy; `nguon_git.py` nguồn module từ git; `tu_xa.py` module từ xa; `xac_thuc.py` khoá quản trị; `kho.py` kho dữ liệu trên đĩa; `chuyen_tiep.py` bộ chuyển tiếp OpenAI dùng chung; `he_thong.py` trang «Máy này» |
| `modules/<id>/` | Một module: `manifest.json` + `module.py` (+ `web/` tuỳ chọn). Hiện có `gemini-api` |
| `web/` | Trang tĩnh của lõi (`Khoi-dau`, `Cai-module`) |
| `test/` | pytest; máy chủ Google/GitHub/OpenAI đều là **giả** cục bộ, `conftest.py` chặn mọi đường ra Internet |
| `scripts/` | `chup-man-hinh.sh` chụp lại ảnh README vào `docs/anh/` |
| `docs/` | `MAU-TIEU-MUC-MODULE.md` (mẫu tiểu mục README cho module mới), `anh/` |
| `deploy/` | Tệp triển khai phụ (Cloudflare Worker làm proxy vượt chặn vùng của Gemini) |

## Cách kiểm — chạy ĐỦ trước khi cam kết

Môi trường: Python 3.13 (`.python-version`). Lần đầu: `uv venv && uv pip install -r requirements.txt -r requirements-dev.txt`
(hoặc `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt`).

```bash
.venv/bin/python -m pytest test -q -p no:cacheprovider    # test; xét MÃ THOÁT, không xét cảm giác
bandit -r app modules -lll                                # mức HIGH phải sạch (CI chặn)
docker build -t web2api:latest .                          # ảnh phải dựng được
docker compose up -d                                      # chạy thật ở cổng 3031 (xem README mục 4)
```

Một lệnh hỏng là **DỪNG**: không cam kết, không đẩy. Test giao diện cần `/usr/bin/google-chrome` và `node`
(thiếu thì tự bỏ qua); test so với trang demo thiết kế chỉ chạy khi đặt `WEB2API_DEMO_DIR`.

## Quy tắc làm việc

- **Sửa LỚP LỖI, không vá triệu chứng.** Lỗi do khớp chuỗi bắt nhầm, đọc sai kho dữ liệu, hay không kiểm
  chứng kết quả thì phải sửa ở gốc rồi tìm mọi chỗ cùng lớp.
- **Không thêm danh sách từ khoá.** Danh sách luôn thiếu, và mỗi lần thiếu là một lần chủ kho phải làm
  người soát lỗi. Chỉ chấp nhận khi đã nêu rõ vì sao không nguyên tắc nào thay được.
- **Module khám phá từ cấu trúc**, không đăng ký tay: lõi thấy `modules/<id>/manifest.json`, trường bí mật
  do `kieu` trong manifest quyết (`bi_mat`, `danh_sach_bi_mat`). Đừng thêm bảng tên module hay tên trường bí mật vào lõi.
- **Khoá API không bao giờ vào log hay phản hồi.** Chỉ hiện dạng che `…abcd`; bí mật lưu ở
  `<WEB2API_DATA>/bi_mat/` quyền 0600. Thêm đường API hay dòng log mới thì tự hỏi: có thể lộ khoá không?
- **Mặc định an toàn:** tự cài module tắt, chỉ-báo-không-cài; bắt chữ ký commit là tuỳ chọn; chạy bằng uid 1000.
  Đừng nới mặc định để cho tiện.
- **Đo trên chạy thật trước khi tin bản sửa.** Test chỉ chứng minh mã chạy đúng như mình nghĩ, không chứng
  minh mình nghĩ đúng. Chưa kiểm được thì nói «chưa kiểm», đừng tuyên bố xong.
- **Mỗi module mới PHẢI có** tiểu mục ở README mục 6 **và** dòng ở bảng «Trạng thái hiện tại» (mục 2); thêm
  đường API hay biến `WEB2API_*` cũng phải có trong README. `test/test_web2api_readme.py` bắt lệch và báo đỏ.
  Làm đủ 5 bước ở README mục 15.
- Việc dùng một lần không cần helper; không refactor ngoài yêu cầu.

## Quy trình cam kết

Trước khi sửa lỗi, trả lời ba câu và **viết vào nội dung commit**:

1. Lỗi này thuộc **lớp** nào?
2. Còn chỗ nào cùng lớp? **Đo bằng `grep`, đừng đoán** — ghi lệnh và số kết quả.
3. Sửa theo **nguyên tắc** hay theo **danh sách**?

Cấm tuyệt đối: `git push --force`, `git reset --hard`, `--no-verify` để lách hook. Hook gitleaks chặn
`git commit` thì sửa nguyên nhân, đừng bỏ qua.

## Kho public — không đưa bí mật hay dữ liệu thật của nhà vào mã

Mọi người đọc được cả lịch sử commit; khoá đã commit là đã lộ dù xoá ở commit sau (phải thu hồi). Không
đưa vào mã, ảnh chụp, test hay nhật ký: khoá API, khoá quản trị, token GitHub, địa chỉ IP nội bộ, tên
thiết bị hay dữ liệu thật của nhà chủ kho. Test dùng chuỗi **giả** rõ ràng (dạng `AIzaSy…-000…`); chuỗi
giả mới phải thêm vào allowlist của `.gitleaks.toml` (chỉ chủ kho được sửa — xem dưới). Ảnh README chụp
bằng `scripts/chup-man-hinh.sh` trên lõi tạm không có khoá.

## Phát hành

- Đẩy lên `main` → GitHub Actions (`.github/workflows/docker-build.yml`): chạy test, dựng ảnh amd64 và
  arm64, đẩy `ghcr.io/tritue2011/web2api` với thẻ `latest`, `sha-<7 ký tự>` và thẻ phiên bản đọc từ `VERSION`.
  Chỉ sửa `*.md` hoặc `docs/` thì không dựng lại.
- Phát hành bản có số: sửa `VERSION` (ví dụ `0.2.0`), cam kết, rồi gắn thẻ `v0.2.0`. Thẻ phải khớp `VERSION`,
  không khớp thì CI dừng.
- Gói GHCR mặc định **riêng tư**: chủ kho phải đặt Public ở trang Packages sau lần chạy đầu.

## File KHÔNG được tự sửa — phải hỏi chủ kho

`Dockerfile`, `docker-compose.yml`, `.github/workflows/*`, `.gitleaks.toml` (cổng chặn của chính bạn và hạ
tầng dựng/chạy ảnh). Cần đổi một trong số đó thì dừng, nói rõ đổi gì và vì sao, đợi chủ kho đồng ý.
