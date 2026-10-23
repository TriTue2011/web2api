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


# NV230 — TIẾN ĐỘ (nền lại nv230.patch lên ebdfcc6, tiếp quản worker cron chết, 2026-10-23 ~23:10 +07)

Patch gốc: 1 commit, 5 tệp, nền 0b7435c (NV223). Nền mới ebdfcc6 = main 7726157 + NV262 + NV263 + sửa chú thích.
Áp sạch 4/5 tệp (`git apply --exclude=TIEN-DO.md`); TIEN-DO.md giữ CẢ HAI bên, nối mục này.

## Yêu cầu (spec viec-nv230)
- Khi cookie muse2api hỏng (401/AuthError) hoặc sắp hết hạn: tầng (a) MỚI tìm hồ sơ Firefox GẮN CHẮC CHẮN với khoá (trường ho_so trong kho riêng như NV93/NV207, không đoán từ tên), đọc cookie muse.ai qua đường SẴN CÓ của lõi (không viết bộ đọc thứ hai), kiểm bằng bootstrap, qua thì THAY cookie của khoá (đường cai {giu, them} như NV207), ghi trạng thái, KHÔNG lộ cookie.
- Không có hồ sơ gắn / hồ sơ đang mở (409) / không có hatch_sess → «cần người» rõ lý do, không thử lại mãi (trần/ngày + nghỉ luỹ thừa như NV207). Vòng nền dùng chung cơ chế khôi phục hiện có. Không đóng cưỡng bức hồ sơ đang mở.
- Đo qua lõi thật + dịch vụ trình duyệt GIẢ: (a) cookie cũ hỏng + hồ sơ có cookie mới → thay, chat 200; (b) 409 → cần người, không thử lại; (c) không hatch_sess → cần người; (d) cookie mới cũng hỏng → không thay, nghỉ luỹ thừa; (e) không lộ cookie; (f) khoá không gắn hồ sơ → không làm mới bừa. ≥8 đột biến.
## Bước a — ĐO hiện trạng (2026-10-23)
- `modules/muse2api/module.py::lam_moi_phien` (tầng a hiện tại): CHỈ kiểm lại đúng cookie đã có bằng bootstrap (`_kiem_cookie`); AuthError → `het_han` 401. KHÔNG có đường kéo cookie mới từ workspace → đúng như điều phối đã đo: chat báo 401 «cookies expired» dù workspace còn đăng nhập.
- `dang_nhap_tu_dong` (tầng b): trả can_nguoi (SSO Meta, không form mật khẩu) — giữ nguyên.
- Ghép khoá → hồ sơ: tài khoản NV93 có trường `ho_so` trong bản ghi kho riêng (`tai_khoan_nv93` / `_TaiKhoanNV93.ho_so`, khôi phục ở `nap()`), ghép chắc chắn qua giá trị hatch_sess. Khoá dán tay (ms-) không có bản ghi → không gắn hồ sơ.
- Đường đọc cookie sẵn có: module `vnc-xa` (cung cấp khả năng trình duyệt) có `trinh_duyet_ds()` + `trinh_duyet_cookie(ho_so, mien)` → GET /ho-so/{id}/cookie của dịch vụ trình duyệt; lỗi mang `.ma` (409 hồ sơ đang mở, 404 chưa có cookie, 501 Chromium). KHÔNG viết bộ đọc thứ hai: nạp vnc-xa qua importlib như NV207 nạp may-dang-nhap.
- Thay cookie: đường cai {giu, them} qua app/kho (doc_modules/doc_cau_hinh/ghep_cau_hinh/luu_cai_dat) như `_thay_token_jwt` của chatgpt-web, trường `cookies`.
- Vòng nền: `app/tu_lam_moi.VongTuLamMoi` đã gọi `lam_moi_phien` cho module có `khoa_tai_khoan()` — muse2api CHƯA có `khoa_tai_khoan()` nên vòng bỏ qua. Thêm `khoa_tai_khoan()` + `danh_dau_khoe()` = dùng cơ chế hiện có, không thêm vòng mới.
- hatch_sess là chuỗi mờ (không giải mã được exp) → «sắp hết hạn» không đo được từ cookie; tín hiệu đo được là bootstrap từ chối (401/AuthError). Ghi rõ trong báo cáo.
- Số test nền trước khi sửa: cụm muse2api (6 file) = **253 passed, exit 0**.
## Thiết kế (theo mẫu NV207)
- Mới: `modules/muse2api/lam_moi_cookie.py` — `BoLamMoiCookie` (bản sao cấu trúc BoLamMoiJWT): ghép hồ sơ chắc chắn, trần 5 lần/ngày/khoá, nghỉ luỹ thừa 300·2^n trần 6h, single-flight theo khoá, trạng thái ở kho riêng (khoá `lam_moi_cookie`), không lộ cookie.
- `module.py`: `lam_moi_phien` — bootstrap từ chối (AuthError) → thử `BoLamMoiCookie.lam_moi(khoa)`: thành công → tầng (a) thành công; can_nguoi (409/không hatch_sess/hồ sơ biến mất) → KetQuaKhoiPhuc can_nguoi + hướng dẫn; lỗi tạm (cookie mới cũng hỏng/dịch vụ sập/giống cũ) → giữ nguyên `het_han` như cũ; không gắn hồ sơ → KHÔNG gọi dịch vụ, giữ nguyên `het_han` (ca f).
- `module.py`: `_thay_cookie(khoa_cu, cookie_moi)` (đường cai {giu, them} + cập nhật bộ nhớ + bản ghi NV93), `_doc/_luu_trang_thai_cookie`, `khoa_tai_khoan()`, `danh_dau_khoe()`, cấu hình dịch vụ trình duyệt đọc ở `nap()` (`dich_vu_trinh_duyet_url`, `khoa_dich_vu_trinh_duyet`, `cookie_tran_thu_ngay`) như NV207 đọc cấu hình may-dang-nhap.

## Trạng thái
- [x] Bước a ĐO: đọc code + ghi hiện trạng ở trên.
- [x] Test đỏ tái hiện (a)-(f): 15 test mới, lượt đầu **14 failed / 1 passed** (đúng kỳ vọng).
- [x] Triển khai tới xanh: `lam_moi_cookie.py` mới + tích hợp `module.py` + manifest thêm 3 trường cấu hình (phien_ban 0.2.2).
- [x] Cụm test: NV230 **15 passed**; cụm muse2api + cookie-sang-module **402 passed, exit 0** (không hồi quy so với nền 253 ở 6 file gốc). Cụm rộng liên quan (khoi_phuc/tu_lam_moi/kham_pha/manifest…): 147 passed, 2 failed — cả 2 KHÔNG phải hồi quy: `test_danh_sach_nuot_loi_khong_tang` đỏ sẵn trên nền sạch (điểm bị gắn cờ duy nhất ở app/kho_tai_khoan.py, code NV230 cộng 0 điểm mới); `test_hieu_nang_doc_phien_khong_doi_bac` là test đo thời gian trên app/phien.py, flaky khi máy tải nặng (load 7.59, qua khi chạy lại lúc rảnh).
- [x] Đột biến: **12/12 chết** bởi assert thật (M5 làm lại sạch sau khi bản đầu chết vì lỗi cú pháp của chính đột biến).
- [ ] Commit + patch + apply --check clone sạch.
- [ ] Báo cáo + upload outbox + xoá inbox + da_thay + dang-lam.

## Ghi chú nền lại (NV265)
- Trạng thái trong mục gốc đã chốt trên nền cũ; lượt này kiểm lại trên nền mới theo spec viec-nv265.
- CHƯA kiểm: chạy với workspace Firefox thật; đo đường cookie hết hạn ngoài đời.
