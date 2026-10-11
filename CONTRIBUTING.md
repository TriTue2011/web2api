# Đóng góp cho web2api

Cảm ơn bạn muốn góp sức. Tài liệu này nói cách dựng môi trường, chạy kiểm tra và thêm một module mới.
Hướng dẫn dùng web2api (cài, cấu hình, API) ở `README.md`; luật làm việc chi tiết cho người sửa mã ở `CLAUDE.md`.

**Kho này công khai.** Đừng đưa khoá API, token, khoá quản trị, địa chỉ nội bộ hay dữ liệu thật của nhà vào
mã, test, ảnh chụp, issue hay pull request. Lỗ hổng bảo mật thì báo riêng tư theo `SECURITY.md`.

## Chạy môi trường phát triển

Cần Python 3.13 (xem `.python-version`). Từ gốc kho:

```bash
uv venv && uv pip install -r requirements.txt -r requirements-dev.txt
# hoặc không có uv:
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

`requirements.txt` là gói của lõi (cũng là thứ ảnh Docker cài); `requirements-dev.txt` thêm gói kiểm thử
và chụp ảnh. Chạy lõi tại chỗ, thư mục dữ liệu riêng để khỏi đụng dữ liệu thật:

```bash
WEB2API_DATA=/tmp/web2api-dev WEB2API_ADMIN_KEY=khoa-dev-tam .venv/bin/python -m uvicorn app.main:app --port 8000
```

Hoặc chạy bằng Docker: `docker compose up -d` (xem README mục 5).

## Chạy kiểm tra

Chạy đủ ba lệnh dưới đây trước khi mở pull request; **một lệnh hỏng là dừng và sửa**, không đẩy lên.

```bash
.venv/bin/python -m pytest test -q -p no:cacheprovider
bandit -r app modules -lll
docker build -t web2api:latest .
```

Mọi test dùng máy chủ giả cục bộ (Google, GitHub, dịch vụ OpenAI) và không gọi Internet. Test giao diện cần
Chrome (`/usr/bin/google-chrome`, đổi bằng `WEB2API_CHROME`) và `node`; thiếu thì tự bỏ qua. CI
(`.github/workflows/`) chạy cùng các kiểm tra này cộng quét bí mật bằng gitleaks và quét gói bằng pip-audit.

Cài hook chặn bí mật ngay trên máy (tuỳ chọn nhưng nên làm): `pipx install pre-commit && pre-commit install`.

## Thêm một module mới

Module là một thư mục `modules/<id>/` trong kho; lõi tự khám phá, không cần đăng ký ở đâu. Làm lần lượt:

1. **Chọn id và tiền tố.** `id` gồm chữ thường, số, `-`, `_` (2–41 ký tự); `tien_to` là phần đứng trước dấu `/` trong tên model
   (`gemini/gemini-2.5-flash`) và không được trùng module khác. Dịch vụ đã nói chuẩn OpenAI sẵn thì có thể
   chỉ cần «module từ xa» (README mục 3), không cần viết module.
2. **Viết `manifest.json`** theo mô tả ở README mục 3, tiểu mục «Viết một module» (gồm `loai`, `loai_ten`,
   `loai_thu_tu`, `cung_cap`, `muc` với `can` — giao diện hiện tab/chế độ theo lời khai này, không theo tên module). Trường nhập khoá phải khai
   `kieu` là `bi_mat` hoặc `danh_sach_bi_mat` để lõi tự lưu riêng và che; khai `tai_nguyen.mang_ra` đúng các máy module gọi tới.
3. **Viết `module.py`** với lớp `Module`. Hợp đồng ghi ở đầu `app/kham_pha.py`; module mẫu thật là
   `modules/gemini-api/`. Nguồn nói chuẩn OpenAI thì dùng lại `ChuyenTiepOpenAI` trong `app/chuyen_tiep.py`
   (xoay khoá, nghỉ khoá, stream, chuẩn hoá lỗi) thay vì tự viết lại. Module cần gói Python mà ảnh lõi không có sẽ
   bị từ chối nạp: nêu gói trong `yeu_cau_goi`, và nếu ảnh không có thì làm module từ xa.
4. **Viết test** `test/test_web2api_<id>.py` theo mẫu `test/test_web2api_gemini.py`: dựng máy chủ giả của dịch vụ, kiểm
   đường thành công, khoá sai, hết hạn mức, và **kiểm khoá không lọt ra phản hồi hay nhật ký**. Khoá trong test
   phải là chuỗi giả rõ ràng; chuỗi giả mới cần được thêm vào allowlist của `.gitleaks.toml` — việc này chỉ chủ kho làm, hãy nêu trong pull request.
5. **Cập nhật README** — chép `docs/MAU-TIEU-MUC-MODULE.md` thành tiểu mục mới ở mục 4 «Module có sẵn», thêm dòng vào bảng
   «Trạng thái hiện tại» (mục 1, ghi id module). Làm đủ các bước ở README mục 9 «Cách bổ sung README khi thêm module»
   (README không giữ nhật ký thay đổi — lịch sử nằm trong git); test
   `test/test_web2api_readme.py` báo đỏ nếu còn thiếu tiểu mục, dòng trạng thái, đường API hay biến `WEB2API_*`.
   Nên thêm `modules/<id>/README.md` tóm tắt cạnh mã, như `modules/gemini-api/README.md`.
6. **Chạy ba lệnh kiểm tra** ở trên.
7. **Đẩy lên git.** Sau khi vào `main`, web2api đang theo dõi kho sẽ báo «có module mới» và quản trị bấm cài.
   Lưu ý: web2api nhận biết bản mới theo nội dung thư mục module, nên sửa **bất kỳ** tệp nào trong `modules/<id>/`
   (kể cả README của module) cũng có thể hiện thành thông báo «có bản mới».

## Quy ước commit

- Dòng đầu viết tiếng Việt, nêu **kết quả** (việc gì giờ đã khác), gọn trong khoảng 72 ký tự.
- **Sửa lỗi thì thân commit trả lời ba câu**: lỗi thuộc *lớp* nào; còn chỗ nào cùng lớp (đo bằng `grep`, ghi lệnh
  và số kết quả); sửa theo *nguyên tắc* hay theo *danh sách*. Thêm danh sách từ khoá là thất bại, không phải giải pháp.
- Một commit một việc; không trộn sửa lỗi với dọn dẹp ngoài yêu cầu.
- Không dùng `git push --force`, `git reset --hard` trên nhánh dùng chung, và không dùng `--no-verify` để
  lách hook: hook chặn thì sửa nguyên nhân.

## Ảnh chụp màn hình trong README

Ảnh phải là ảnh **thật** của web2api, chụp lại được bằng script thay vì chụp tay:

```bash
scripts/chup-man-hinh.sh
```

Script dựng một lõi tạm (thư mục dữ liệu tạm, không khoá API nào), chụp từng trang ở khổ máy tính 1280×800
và điện thoại 390×844 bằng Chrome, lưu vào `docs/anh/` rồi dọn sạch. Quy tắc:

- Trang mới có ảnh thì thêm vào danh sách `TRANG` trong script, đừng chụp tay.
- Mỗi ảnh nằm trong `docs/anh/`, tối đa 250 KB (script tự giảm màu nếu vượt), và có một dòng chú thích nghiêng
  ngay dưới ảnh trong README — test README kiểm cả ba điều này.
- Không để khoá, token hay địa chỉ nội bộ nào lên ảnh. Thấy lọt thì chụp lại, không che bằng tay.
