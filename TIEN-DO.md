# NV144 — TIẾN ĐỘ (subagent NV144, 2026-10-19)

## Đã xong
- `app/kho_tai_khoan.py` (mới): kho tài khoản đã lưu — email/nhãn/mật khẩu/TOTP secret;
  tệp `bi_mat/_tai_khoan_da_luu.json` qua `app.kho` (0600, mã hoá khi nghỉ nếu có
  `WEB2API_KHOA_CHINH`); id `tk-`+sha256; `hmac.compare_digest`; kiểm biên chặt
  (email khuôn hẹp NV120, mật khẩu ≤256, TOTP chỉ nhận khoá Base32 qua
  `kiem_tra_va_tao_otp`+ép `BiMatTotp`, từ chối mã 6 số; ký tự điều khiển/surrogate
  → `invalid_value`); kho hỏng → `kho_hong` (đóng cửa).
- 6 routes trong `app/tuyen.py` (router `/api/tai-khoan`, QT): `GET/POST /da-luu`,
  `DELETE /da-luu/{id}`, `POST /da-luu/{id}/dang-nhap`, `GET/PUT /tu-dang-nhap-lai`.
- Hook tự đăng nhập lại trong `app/tu_lam_moi._chay_khoi_phuc` (nhánh `can_nguoi`):
  gọi `kho_tai_khoan.thu_dang_nhap_lai()` (dict API), chỉ gọi `module.dang_nhap`.
- UI `web/He-thong-AI.dc.html`: khối «Tài khoản đã lưu (n)» — dropdown, các ô
  Email/Mật khẩu (Hiện/Ẩn)/TOTP secret/Nhãn, «Cách lấy secret» 5 bước, 4 nút
  (Lưu tài khoản vô hiệu khi thiếu email+mk / Lưu và đăng nhập chính /
  Chỉ đăng nhập / Mở noVNC), công tắc tự đăng nhập lại (mặc định BẬT),
  mỗi tài khoản một dòng (email che · Đăng nhập · Xoá bấm hai lần), «Lưu tất cả».
  Bí mật rời state+DOM ngay sau gửi; guard chống gửi đôi.
- `docs/THAM-CHIEU.md`: mục NV144 (API + kho + giao diện + sao lưu).
- Test: `test/test_web2api_nv144_tai_khoan_da_luu.py` (23 test) +
  `test/test_web2api_nv144_giao_dien.py` (3 test Chrome).

## Kết quả kiểm (2026-10-19, sau khi sandbox mở lại ghi file cho Python)
- Test mới: 23/23 xanh, exit 0. UI: 3 skipped (không có Chrome — thành thật).
- readme + doc_lap + nv45a: 14/14 xanh, exit 0.
- Đột biến 4/4 đỏ đúng: liệt kê lộ bí mật; bỏ kiểm công tắc; bỏ chốt dừng 2 hỏng;
  bỏ QT trên router.
- Quét: không `eval(/exec(/subprocess/os.system/pickle` trên diff; không tên
  provider mới trong app//web/.
- Logic `thu_dang_nhap_lai` đã kiểm 5 nhánh bằng stub (không cần FS).
- Full suite (bỏ test_v1_*.py): ĐANG CHẠY nền (proc_a81a89211e99).

## Còn lại
1. Chờ full suite xong → ghi số liệu.
2. `git add` + commit; `git format-patch <nền>..HEAD --stdout` → nv144.patch;
   `git am --check` trên clone sạch.
3. Viết nv144-bao-cao.md; PUT cả hai lên outbox; xoá viec-nv144.txt khỏi inbox;
   append da_thay; xoá claim khỏi dang-lam.

## Ghi chú quan trọng
- Sandbox từng chặn Python ghi file (~02:38–03:2xZ, EPERM) — đã tự hết lúc ~03:25Z.
  Mọi kiểm trên chạy SAU khi mở lại.
- TOTP secret lưu là KHOÁ Base32 đã chuẩn hoá (đọc từ `_buf` của BiMatTotp —
  `lay()` của nó tính mã 6 số, không phải khoá).
- `che_email` → `…***@example.com` (test yêu cầu bắt đầu bằng `…`; UI cần `***`).
- Mật khẩu trần 256 ký tự (test: 257 → 400).
- `nhan` được `html.escape` ở biên ra (test XSS yêu cầu `&lt;script&gt;`).
- Sao lưu: `bi_mat/` đã bị loại khỏi bản sao lưu không mật khẩu từ trước (không sửa).
