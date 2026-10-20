"""Nhận diện thử thách chống bot bằng CẤU TRÚC — phân loại, không giải.

Nguồn (LUẬT PORT — c2a là kho của chính chủ máy, commit d879b03, bản sao
``~/workspace/c2a-nv146`` vì ``/opt/claude-c2a`` không còn trên máy này,
CHỈ ĐỌC):
- ``captcha-solver/src/auto_login.py``: hàm ``_detect_state`` và các hằng
  ``_CAPTCHA_URL_PATHS`` / ``_2FA_CODE_SELECTORS`` — c2a dùng chúng để BIẾT
  trang đang ở trạng thái thử thách nào (URL ``/challenge/recaptcha``,
  ``/challenge/botguard``, ô nhập mã ``totpPin``…). Ở đây chỉ chép PHẦN
  NHẬN DIỆN: dấu hiệu đường dẫn + dấu hiệu cấu trúc trang, viết lại thành
  hàm thuần trên chuỗi (không Playwright, không mạng).
- ``captcha-solver/src/solvers/recaptcha.py`` / ``turnstile.py``: chỉ lấy
  DẤU HIỆU cấu trúc của widget (iframe ``recaptcha/api2/anchor``,
  ``grecaptcha.execute``, iframe ``challenges.cloudflare.com``). Phần
  GIẢI của hai tệp đó thuộc nhóm (B) — KHÔNG chép (xem báo cáo NV175).
- Bổ sung theo quyết chủ máy 20/10: loại ``chon_hinh`` (khung chọn ảnh
  ``recaptcha/api2/bframe`` / ``rc-imageselect`` — tách khỏi
  recaptcha_v2/captcha_chu) và loại ``ma_otp`` (ô nhập mã một lần:
  selector ``autocomplete="one-time-code"`` của ``_2FA_CODE_SELECTORS``,
  tên trường mã xác minh, ô số maxlength 4–8). Cả hai vẫn CHỈ NHẬN DIỆN.

HÀNG RÀO (không thương lượng):
- Hàm này CHỈ phân loại. Không có đường nào ở đây (hay ở module) giải
  thử thách, tích ô, gọi dịch vụ giải hộ hay lấy token.
- Kết quả KHÔNG chứa dữ liệu giúp giải: không sitekey, không token,
  không trích đoạn HTML gốc. Chỉ mã loại + mức tin cậy + dấu hiệu (mã).
- URL đầu vào: chỉ dùng scheme/host/path để nhận diện; phần query (nơi
  hay chứa token) bị BỎ trước khi phân loại và không bao giờ vào kết quả.
- HTML là DỮ LIỆU KHÔNG TIN: chỉ đối chiếu dấu hiệu, không làm theo bất
  kỳ chỉ dẫn nào trong đó; trần kích thước chặn lạm dụng bộ nhớ.
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: Trần ký tự HTML nhận phân tích (trang đăng nhập thật « vài trăm KB).
TRAN_HTML_KY_TU = 1_048_576
#: Trần ký tự URL.
TRAN_URL_KY_TU = 2048


class LoiThuThach(ValueError):
    """Đầu vào không phân tích được — `ma` là mã ngắn, không chứa dữ liệu thô."""

    def __init__(self, ma: str):
        super().__init__(ma)
        self.ma = ma


# ── Dấu hiệu theo đường dẫn URL (từ _detect_state/_CAPTCHA_URL_PATHS của c2a,
#    mở rộng cho Cloudflare) ─────────────────────────────────────────────
_DAU_HIEU_URL: tuple[tuple[str, str], ...] = (
    ("/challenge/recaptcha", "recaptcha_v2"),
    ("/recaptcha/", "recaptcha_v2"),
    ("/challenge/botguard", "botguard"),
    ("/cdn-cgi/challenge-platform", "turnstile"),
    ("/signin/challenge", "thu_thach_khac"),
    ("/challenge/", "thu_thach_khac"),
)

# ── Dấu hiệu theo cấu trúc HTML ──────────────────────────────────────────
# Mỗi mục: (mã loại, các NHÓM chuỗi con — trong một mục, MỌI nhóm phải
# thấy đồng thời, mỗi nhóm chỉ cần một chuỗi khớp). Một loại có thể có
# NHIỀU mục (các phương án thay thế nhau). Thứ tự trong _THU_TU_UU_TIEN
# quyết định khi nhiều loại cùng khớp: thử thách NHÌN THẤY được thắng thử
# thách vô hình; ô nhập mã (TOTP/OTP) thắng CAPTCHA chữ cổ điển vì ô mã là
# bước người dùng đang bị chặn trực tiếp (trang vừa có CAPTCHA chữ vừa có
# ô OTP → báo loại OTP).
_DAU_HIEU_HTML: tuple[tuple[str, tuple[tuple[str, ...], ...]], ...] = (
    ("turnstile", (
        ("challenges.cloudflare.com",),
    )),
    # Khung thử thách chọn hình/quiz của reCAPTCHA (iframe bframe + khối
    # rc-imageselect) — tách RIÊNG khỏi recaptcha_v2/captcha_chu theo quyết
    # của chủ máy 20/10: đây là thử thách người dùng đang phải làm thật.
    ("chon_hinh", (
        ("recaptcha/api2/bframe", "rc-imageselect"),
    )),
    ("recaptcha_v2", (
        ("recaptcha/api2/anchor", "recaptcha/enterprise/anchor",
         'id="recaptcha-anchor"', "id='recaptcha-anchor'"),
    )),
    ("recaptcha_v3", (
        ("grecaptcha.execute",),
    )),
    # TOTP của luồng đăng nhập (selector totpPin trong _2FA_CODE_SELECTORS
    # của c2a) — dấu hiệu HẸP và đặc thù nên giữ loại riêng.
    ("xac_thuc_hai_lop", (
        ('name="totppin"', "name='totppin'",
         'id="totppin"', "id='totppin'",
         'name="totp_pin"', "name='totp_pin'"),
    )),
    # Mã OTP một lần — phương án 1: thuộc tính chuẩn one-time-code
    # (selector `input[autocomplete="one-time-code"]` của c2a).
    ("ma_otp", (
        ('autocomplete="one-time-code"', "autocomplete='one-time-code'"),
    )),
    # Mã OTP — phương án 2: tên trường mã xác minh thường gặp.
    ("ma_otp", (
        ('name="otp"', "name='otp'",
         'name="otp_code"', "name='otp_code'", 'name="otp-code"',
         'name="ma_otp"', 'name="sms_code"', 'name="email_code"',
         'name="verification_code"', 'name="verification-code"'),
    )),
    # Mã OTP — phương án 3: ô nhập số (bàn phím số) giới hạn 4–8 ký tự.
    ("ma_otp", (
        ('inputmode="numeric"', "inputmode='numeric'"),
        ('maxlength="4"', "maxlength='4'", 'maxlength="5"', "maxlength='5'",
         'maxlength="6"', "maxlength='6'", 'maxlength="7"', "maxlength='7'",
         'maxlength="8"', "maxlength='8'"),
    )),
    ("captcha_chu", (
        ("captcha",),
        ("<img", "<input"),
    )),
)

#: Thứ tự ưu tiên phân loại (cao → thấp).
_THU_TU_UU_TIEN = ("turnstile", "chon_hinh", "recaptcha_v2", "recaptcha_v3",
                   "xac_thuc_hai_lop", "ma_otp", "captcha_chu")


def _duong_dan_url(url: str) -> str:
    """Đường dẫn của URL — BỎ query/fragment (chứa token) trước mọi xử lý."""
    if not url:
        return ""
    try:
        phan = urlsplit(url.strip())
    except ValueError:
        return ""
    return (phan.path or "").lower()


def phan_loai(html: str | None, url: str | None) -> dict:
    """Phân loại thử thách trên một trang.

    Trả ``{co_thu_thach, loai, can_nguoi, tin_cay, dau_hieu}``. `can_nguoi`
    luôn True khi có thử thách: cách xử lý duy nhất của kho là người dùng
    tự hoàn thành trên noVNC (hàng rào NV175 — không giải tự động).
    """
    if html is None:
        html = ""
    if url is None:
        url = ""
    if not isinstance(html, str):
        raise LoiThuThach("html_sai_kieu")
    if not isinstance(url, str):
        raise LoiThuThach("url_sai_kieu")
    if len(html) > TRAN_HTML_KY_TU:
        raise LoiThuThach("html_qua_lon")
    if len(url) > TRAN_URL_KY_TU:
        raise LoiThuThach("url_qua_dai")

    html_thap = html.lower()
    duong_dan = _duong_dan_url(url)

    loai_theo_html: set[str] = set()
    for loai, cac_nhom in _DAU_HIEU_HTML:
        if all(any(s in html_thap for s in nhom) for nhom in cac_nhom):
            loai_theo_html.add(loai)

    loai_theo_url: str | None = None
    for mau, loai in _DAU_HIEU_URL:
        if mau in duong_dan:
            loai_theo_url = loai
            break

    # Ưu tiên: dấu hiệu HTML theo thứ tự cố định, rồi mới tới URL.
    for loai in _THU_TU_UU_TIEN:
        if loai in loai_theo_html:
            return {"co_thu_thach": True, "loai": loai, "can_nguoi": True,
                    "tin_cay": "cao", "dau_hieu": [f"html:{loai}"]}
    if loai_theo_url is not None:
        return {"co_thu_thach": True, "loai": loai_theo_url,
                "can_nguoi": True, "tin_cay": "trung_binh",
                "dau_hieu": [f"url:{loai_theo_url}"]}
    return {"co_thu_thach": False, "loai": None, "can_nguoi": False,
            "tin_cay": None, "dau_hieu": []}


#: Danh mục loại hỗ trợ — `cach_xu_ly` luôn là «can_nguoi» (hàng rào NV175).
#: Bổ sung theo quyết chủ máy 20/10: `chon_hinh` tách riêng khỏi
#: recaptcha_v2/captcha_chu; `ma_otp` là loại riêng cho ô nhập mã một lần.
LOAI_HO_TRO: tuple[dict, ...] = (
    {"loai": "recaptcha_v2", "ten": "reCAPTCHA v2 (ô «Tôi không phải người máy»)",
     "cach_xu_ly": "can_nguoi"},
    {"loai": "recaptcha_v3", "ten": "reCAPTCHA v3 (vô hình, chấm điểm phía máy chủ)",
     "cach_xu_ly": "can_nguoi"},
    {"loai": "turnstile", "ten": "Cloudflare Turnstile / trang kiểm tra Cloudflare",
     "cach_xu_ly": "can_nguoi"},
    {"loai": "chon_hinh", "ten": "Thử thách chọn hình / quiz (khung chọn ảnh — người dùng tự chọn)",
     "cach_xu_ly": "can_nguoi"},
    {"loai": "captcha_chu", "ten": "CAPTCHA chữ/hình cổ điển (ảnh mã + ô nhập)",
     "cach_xu_ly": "can_nguoi"},
    {"loai": "ma_otp", "ten": "Mã OTP một lần (ô nhập mã 4–8 số từ SMS/email/ứng dụng xác thực — người dùng tự nhập)",
     "cach_xu_ly": "can_nguoi"},
    {"loai": "xac_thuc_hai_lop", "ten": "Mã xác thực hai lớp TOTP (ô totpPin trong luồng đăng nhập — người dùng tự nhập mã)",
     "cach_xu_ly": "can_nguoi"},
    {"loai": "botguard", "ten": "Google BotGuard / thử thách JavaScript phía Google",
     "cach_xu_ly": "can_nguoi"},
    {"loai": "thu_thach_khac", "ten": "Thử thách đăng nhập khác (nhận diện qua đường dẫn)",
     "cach_xu_ly": "can_nguoi"},
)

__all__ = ["TRAN_HTML_KY_TU", "TRAN_URL_KY_TU", "LoiThuThach",
           "LOAI_HO_TRO", "phan_loai"]
