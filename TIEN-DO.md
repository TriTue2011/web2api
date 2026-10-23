# NV231 — TIẾN ĐỘ (tiếp quản worker cron chết, 2026-10-23 ~13:30 +07)

## Đã xong
- Tiếp quản /home/hatch/workspace/web2api-nv231 (KHÔNG tạo thư mục mới).
- Triển khai xong trong `dich-vu/may-dang-nhap/src/auto_login.py`:
  - `_co_phien_google(ctx)`: kiểm phiên Google NGHIÊM — cookie trong bảng hằng số
    duy nhất `_GOOGLE_LOGIN_COOKIES = ("__Secure-1PSID", "__Secure-3PSID", "SID")`,
    đồng thời: miền chứa google.com, value khác rỗng, chưa hết hạn
    (expires < 0 = cookie phiên, hoặc > hiện tại). Cookie hết hạn/rỗng/sai miền
    → False (fail closed). Không log tên/giá trị cookie hay URL.
  - Trong `_vong_2fa`, ĐẶT TRƯỚC kiểm «đã đăng nhập» chung: trang xác minh danh
    tính (mọi mã «xac_minh_*» từ `_phan_loai_thu_thach`) + có phiên
    → state success, message «bỏ qua trang xác minh danh tính khi phiên đã
    đăng nhập», KHÔNG bấm/điền gì. Không phiên → luồng NV209 (need_human).
    Lỗi vĩnh viễn (dang_nhap_bi_tu_choi/tai_khoan_bi_khoa) không thuộc họ
    «xac_minh_*» → không biến thành success.
  - Sửa kiểm chung `_already_logged_in` trong `_vong_2fa`: giờ cũng ghi
    state=success + completed_at (trước đây chỉ ghi message).
- Test: `dich-vu/may-dang-nhap/test_nv231_phien_da_dang_nhap.py` (20 test, mới)
  + cập nhật CÓ CHỦ Ý `test_nv209_xac_minh_danh_tinh.py` (cookie giả mang đủ
  domain/expires như Playwright thật).
- Đo thật: **44 passed / 0 failed, exit 0** (67s) — gồm (1) selfie+phiên→success,
  (2) selfie không phiên→need_human, (3) rejected/disabled+cookie→lỗi vĩnh viễn,
  (4) /verification/ lạ +phiên→success / không phiên→vài vòng rồi need_human
  (<60s), (5) cookie hết hạn/rỗng/sai miền→không success, (6) không log TL=/giá
  trị cookie, hồi quy dp không phiên vẫn need_tap.
- Sửa lỗi tiếp quản: nhánh NV231 ban đầu đặt SAU kiểm `_already_logged_in`
  nên trang /verification/ + phiên hợp lệ bị trả True mà state vẫn 'pending'
  (2 test đỏ) — đã chuyển nhánh NV231 lên TRƯỚC kiểm chung, xóa nhánh trùng
  ở cuối vòng.

## Tiếp quản 2 (worker mới, 2026-10-23 ~14:37 +07)
- Chạy lại toàn bộ test dịch vụ: nv231+nv209+c2a_tinh_nang+c2a_nguyen_van =
  61 passed / 1 failed; các file còn lại (vault, xac_thuc, dockerfile,
  loc_du_lieu_nha, trinh_duyet_that) = 56 passed / 1 skipped, exit 0.
  Test đỏ duy nhất `test_c2a_tinh_nang::test_lo_bi_mat_mac_dinh_tat_tra_403`
  (assert 503 == 200 ở bước reveal khi bật lo_bi_mat) — đã kiểm bằng git stash:
  ĐỎ SẴN TRÊN NỀN SẠCH, không liên quan NV231 → không sửa, ghi vào báo cáo.

## Đột biến + số cuối (tiếp quản 2, 2026-10-23)
- [x] Đột biến: 11 đột biến trên code NV231 (script /tmp/mut_nv231.py, sao
  lưu/khôi phục bằng cp). Lần đầu 10/11 CHẾT, M5 (rút bảng cookie còn mỗi
  "SID") SỐNG SÓT → thêm 3 test `test_co_phien_google_tung_ten_trong_bang`
  (từng tên trong bảng đứng một mình) vào test_nv231 → chạy lại M5: CHẾT.
  Kết quả cuối: 11/11 đột biến chết.
- [x] Test toàn dịch vụ may-dang-nhap (pytest .): **120 passed / 1 skipped /
  1 failed**, exit 1 — failed duy nhất là
  `test_c2a_tinh_nang::test_lo_bi_mat_mac_dinh_tat_tra_403`, đã verify ĐỎ SẴN
  trên nền sạch (git stash), không liên quan NV231.
  Riêng nv231+nv209: 47 passed / 0 failed, exit 0.

## Còn lại
2. [ĐANG LÀM] `git add` + commit; `git format-patch -1 --stdout` →
   nv231.patch; `git apply --check` sạch.
3. Viết nv231-bao-cao.md (tiếng Việt); lưu cả hai vào
   /home/hatch/workspace/patches-web2api/.
4. PUT cả hai lên outbox (token trong script từ
   /home/hatch/workspace/drop-server/.token, UA curl/8.5.0).
5. DELETE viec-nv231-google-trang-xac-minh-nhung-da-dang-nhap.txt khỏi inbox.
6. Append tên file vào /home/hatch/hooks/state/drop-inbox/da_thay.
7. Xóa dòng viec-nv231 khỏi /home/hatch/workspace/drop-inbox/dang-lam (rút claim).

## Ghi chú quan trọng
- CHƯA kiểm với Google thật (sandbox không có tài khoản/đăng nhập thật).
- Quy tắc cứng giữ nguyên: KHÔNG push GitHub; không dùng secret của Việt.
- `pydantic-settings` đã cài thêm vào system python (pip --break-system-packages)
  để chạy pytest — không ảnh hưởng patch.
- Diff hiện tại còn ở working tree (chưa commit): auto_login.py +
  test_nv209 (modified), test_nv231 (untracked).
