# ĐỐI CHIẾU ĐĂNG NHẬP: c2a ↔ web2api (NV235)

Nguồn c2a: commit `d879b03` (điều phối đã kiểm: 0 commit đổi sau d879b03 ở các
tệp đăng nhập). Bản sao đọc: `~/workspace/c2a-nv146/`.

Luật PORT: CHÉP/ĐỐI CHIẾU theo c2a, KHÔNG phát minh luồng mới; mọi chỗ lệch
phải nêu lý do. Quy ước trạng thái: **KHỚP** = hành vi tương đương,
**LỆCH** = có nhưng khác hành vi, **THIẾU** = chưa có.

---

## 1. Phân loại tài khoản (bằng DỮ LIỆU, không đoán từ đuôi email)

| Hàm c2a (tệp:dòng) | Chỗ web2api | Trạng thái | Hành động |
|---|---|---|---|
| `_dong_hang_loat` — dòng trong `codex_auto_list` là dấu hiệu tài khoản hàng loạt (`services/account_recovery.py:132`) | Chưa có khái niệm danh sách hàng loạt trong kho web2api | **THIẾU** | web2api chưa có nút «Đăng nhập hàng loạt»; khi có, thêm bảng dữ liệu tương đương — ngoài phạm vi NV235, ghi nhận ở đây |
| `_ho_so_openai(email)` — thư mục `openai-<localpart>` có thật trên đĩa, hoặc chỉ có mật khẩu kho `openai` mà không có kho `google` (`account_recovery.py:251`) | `_ten_ho_so_theo_dich_vu("openai", email)` (`modules/may-dang-nhap/module.py:154`) chỉ sinh TÊN, chưa dùng để phân loại | **LỆCH** | Bước 2: dùng kiểu đăng nhập đã khai (`dang_nhap_bang`) + mặc định nhóm chatgpt = `truc_tiep`; không suy từ đuôi email |
| `_has_google_creds` / `_has_openai_creds` — hỏi két theo KHO (`account_recovery.py:241`) | Két dịch vụ `accounts_db` của web2api đã có cột `loai` (port từ c2a) | **KHỚP** | Giữ nguyên |
| `recover_provider_account`: tắt hẳn hai cờ Google khi tài khoản là OpenAI gốc (`account_recovery.py:1431`) | NV223 chọn đường theo `dang_nhap_bang`; `""` (chưa khai) rơi về đường Google cũ | **LỆCH** | Bước 2: `dang_nhap_bang == ""` + nhóm chatgpt → mặc định `truc_tiep` |

## 2. ChatGPT OpenAI gốc

| Hàm c2a (tệp:dòng) | Chỗ web2api | Trạng thái | Hành động |
|---|---|---|---|
| `_cgf_openai` — `GET /v1/chatgpt/{profile}/refresh-jwt`: quét phiên rẻ trước, hết đường mới `start_openai_login` mật khẩu+TOTP; cấm dùng `/v1/chatgpt/onboard` cho kiểu này (`account_recovery.py:754`) | `modules/may-dang-nhap/module.py::_dang_nhap_theo_bang` với `dich_vu_key="openai"` gọi `POST /v1/openai-native/onboard` NGAY, chưa thử refresh-jwt | **LỆCH** | Bước 3: BƯỚC 1 = `GET /v1/chatgpt/{ho_so}/refresh-jwt` + KIỂM `exp`; phiên chết mới đăng nhập mật khẩu/TOTP |
| LỖI CỦA C2A — KHÔNG chép: refresh-jwt trả `ok=true/method=scrape` với token ĐÃ HẾT HẠN 42h; `openai-native/onboard` trả «Hồ sơ đã có phiên» giả | `_lay_jwt_chatgpt` (`module.py:1580`) nhận token mà không kiểm `exp` | **LỆCH** (cả hai bên) | Bước 3: web2api GIẢI MÃ claim `exp` (không xác thực chữ ký): hết hạn/sắp hết → coi phiên chết → đăng nhập mật khẩu+TOTP → quét lại; vẫn hết hạn → «cần người». Sửa đúng chỗ c2a sai |
| `_cgf_ghi_pool` — chỉ cập nhật dòng free ĐÃ có, không tự thêm tài khoản mới (`account_recovery.py:792`) | Móc `dat_moc_gan_jwt` gắn JWT vào khoá sổ theo email (`module.py:485`) | **KHỚP** (ý tưởng) | Giữ nguyên |

## 3. ChatGPT qua Google

| Hàm c2a (tệp:dòng) | Chỗ web2api | Trạng thái | Hành động |
|---|---|---|---|
| `_cgf_reuse` — ĐÚNG MỘT lượt, `reuse_session=True`; cấm lượt hai (`account_recovery.py:730`) | `DICH_VU_DANG_NHAP["chatgpt"]["bat_dau"]` gửi onboard không kèm `reuse_session` | **LỆCH** | Bước 3: đường `("chatgpt","google")` gửi `reuse_session=True`; thiếu phiên mới đăng nhập Google |
| `ChatGPTOnboardReq.reuse_session` (`captcha-solver` web2api `src/main.py:1923`) | Dịch vụ web2api ĐÃ có tham số | **KHỚP** | Chỉ cần module gửi kèm |

## 4. Đăng nhập Google (một phiên toàn hệ thống)

| Hàm c2a (tệp:dòng) | Chỗ web2api | Trạng thái | Hành động |
|---|---|---|---|
| `_glogin_serial` — MỘT phiên đăng nhập Google toàn hệ thống, giữ khoá suốt lượt poll (`account_recovery.py:64`) | `_dang_nhap_google` (`module.py:1236`) không có khoá liên-lượt | **THIẾU** | Bước 4: khoá toàn cục trong lõi/khôi phục chung |
| `_GLOGIN_GAP_S=25` — giãn cách tối thiểu giữa 2 phiên (`account_recovery.py:66`) | Chưa có | **THIẾU** | Bước 4 |
| `_glogin_captcha_until/_profile` + `_refresh_captcha_hold` — gặp CAPTCHA thì cả hệ thống nghỉ, chỉ bằng chứng của hồ sơ chủ mới gỡ (`account_recovery.py:69`, `:310`) | Chưa có | **THIẾU** | Bước 4 |
| `_GRELOGIN_COOLDOWN_S=1800` — cooldown 1800s/tài khoản (`account_recovery.py:56`) | Trần/giờ + nghỉ luỹ thừa riêng từng lượt (`module.py:374`) | **LỆCH** | Bước 4: cooldown theo tài khoản vào lõi chung, không rải từng module |
| `_ghi_ket_qua` — ghi trạng thái + lý do lượt cuối (`account_recovery.py:293`) | `_nv145b_trang_thai` + `trang_thai_dang_nhap` (`module.py:815`) | **KHỚP** (ý tưởng) | Bước 4: hiện kết quả cuối trên trang Tài khoản |
| Mật khẩu không rời solver | `DongDaTach` + két dịch vụ (`/v1/accounts/saved`) | **KHỚP** | Giữ nguyên |

## 5. Thang khôi phục T0–T3

| Hàm c2a (tệp:dòng) | Chỗ web2api | Trạng thái | Hành động |
|---|---|---|---|
| `_dang_nhap_lai_theo_thang` — T0 kiểm phiên (rẻ, không trình duyệt) → T1 nghỉ+kiểm lại → T2 đăng nhập Google (`_freshen_google`) → T3 kiểm lại; debounce 1800s; ngân sách 1200s không tính giờ xếp hàng (`account_recovery.py:1167`) | `app/khoi_phuc_phien.py`: tầng a (làm mới) → b (đăng nhập tự động) → c (tài khoản kế) → d (cần người) | **KHỚP** (ý tưởng rẻ→đắt) / **LỆCH** (tên + chi tiết: web2api chưa có T1 «nghỉ rồi kiểm lại», chưa debounce theo (provider, profile), chưa trừ giờ xếp hàng khỏi ngân sách) | Bước 5: ánh xạ T0→a, T1→b-phần-nghỉ, T2→b, T3→kiểm-lại; thêm debounce + ngân sách đúng nghĩa |
| `_CAN_NGUOI = (need_captcha, need_code, need_tap)` — cần người thì bỏ NGAY, đừng chờ hết ngân sách (`account_recovery.py:305`) | `_cho_ket_qua_onboard` trả ngay khi gặp need_* (`module.py:1386`) | **KHỚP** | Giữ nguyên |
| Claude: `_claude_session_trang_thai` đọc sessionKey trên đĩa + verify thật; `relogin-via-google` + poll có thời hạn; đo thật FAILED «Chờ claude.ai nhận session» (`account_recovery.py:869`, `api/claude.py:262`) | Dịch vụ web2api đã có `POST /v1/claude-web/{profile}/relogin-via-google` (port); lõi chưa có verify T0 | **LỆCH** | Bước 5: T0 cho claude = đọc session + verify; relogin + poll có thời hạn; hết hạn → «cần người» |
| Grok: `need_manual` (Cloudflare) → «cần người», KHÔNG thử lại vòng | `_cho_ket_qua_onboard` trả ngay `need_manual` | **KHỚP** | Bước 5: kèm hướng dẫn noVNC |
| Codex: có refresh_token → T0 (`codex_refresh_scheduler.py`); không có → T1… (`services/codex_refresh_scheduler.py`) | ? | Chưa khảo sát đủ | Ghi nhận: cần việc riêng nếu web2api có codex |

## 6. 2FA / tiến độ (gộp NV229)

| Hàm c2a (tệp:dòng) | Chỗ web2api | Trạng thái | Hành động |
|---|---|---|---|
| `auto_login.py` — 2FA theo phiên Google | `nhap_ma_2fa`/`tien_do` (`module.py:1705`, `:1742`) chọn đường bằng `_la_luong_chatgpt` → Claude/Grok khai «google» đi SAI đường | **LỆCH** | Bước 6: mở rộng `_DUONG_ONBOARD_THEO_LUONG` thành bảng (nhóm × cách đăng nhập → onboard/status/2fa/token), dùng chung cho đăng nhập/2FA/tiến độ |
| `need_code/need_captcha/need_tap/failed/success` ánh xạ như nhau mọi provider | `_ket_qua_onboard_tu_phien` + `tien_do` đã ánh xạ chung | **KHỚP** | Giữ nguyên |
| Mã 2FA không vào log/phản hồi | `BiMatTam.lay_va_xoa` + ghi đè | **KHỚP** | Giữ nguyên |

## 7. Làm mới định kỳ

| Hàm c2a (tệp:dòng) | Chỗ web2api | Trạng thái | Hành động |
|---|---|---|---|
| `jwt_refresh_scheduler.py` — làm mới khi JWT còn ≤7 ngày, 6h/lượt | `modules/chatgpt-web/lam_moi_jwt.py` (NV207) — ngưỡng 2 ngày, giải mã `exp` | **KHỚP** (ý tưởng; ngưỡng khác theo spec NV207) | Giữ nguyên |
| `web_session_scheduler.py` — quét phiên web định kỳ | Chưa khảo sát | — | Ngoài phạm vi NV235 |
| `openai_hang_loat.py` — đăng nhập hàng loạt | Chưa có ở web2api | **THIẾU** | Ngoài phạm vi (chưa có nút hàng loạt) |

## 8. Gemini web / Muse

| Hàm c2a (tệp:dòng) | Chỗ web2api | Trạng thái | Hành động |
|---|---|---|---|
| `_gma_authenticated` — tín hiệu THẬT: `account_status == AVAILABLE` (`account_recovery.py:913`) | Module gemini-web của web2api | Chưa khảo sát đủ | Ghi nhận: T0 cho gemini nên dùng tín hiệu thật tương đương |
| Muse | Chưa khảo sát | — | Ngoài phạm vi NV235 |

---

*Lệch đã nêu lý do ở cột «Hành động». Mọi chỗ chép từ c2a đều ghi rõ nguồn
hàm + dòng ở bảng trên.*
