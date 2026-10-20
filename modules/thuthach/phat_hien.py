"""Nhận diện thử thách chống bot bằng CẤU TRÚC — phân loại, không giải.

Nguồn (LUẬT PORT — c2a là kho của chính chủ máy, commit d879b03, bản sao
``~/workspace/c2a-nv146`` vì ``/opt/claude-c2a`` không còn trên máy này,
CHỈ ĐỌC):
- ``captcha-solver/src/auto_login.py``: hàm ``_detect_state`` và các hằng
  ``_CAPTCHA_URL_PATHS`` / ``_2FA_CODE_SELECTORS`` — c2a dùng chúng để BIẾT
  trang đang ở trạng thái thử thách nào (URL ``/challenge/recaptcha``,
  ``/challenge/botguard``, ô nhập mã ``totpPin``…). Ở đây chỉ chép PHẦN
  NHẬN DIỆN: dấu hiệu đường dẫn + dấu hiệu cấu trúc trang.
- ``captcha-solver/src/solvers/recaptcha.py`` / ``turnstile.py``: chỉ lấy
  DẤU HIỆU cấu trúc của widget (iframe ``recaptcha/api2/anchor``,
  ``grecaptcha.execute``, iframe ``challenges.cloudflare.com``). Phần
  GIẢI của hai tệp đó thuộc nhóm (B) — KHÔNG chép (xem báo cáo NV175).
- Bổ sung theo quyết chủ máy 20/10: loại ``chon_hinh`` (khung chọn ảnh
  ``recaptcha/api2/bframe`` / ``rc-imageselect`` — tách khỏi
  recaptcha_v2/captcha_chu) và loại ``ma_otp`` (ô nhập mã một lần:
  selector ``autocomplete="one-time-code"`` của ``_2FA_CODE_SELECTORS``,
  tên trường mã xác minh, ô số maxlength 4–8). Cả hai vẫn CHỈ NHẬN DIỆN.

CÁCH NHẬN DIỆN (vá F1 sau soát độc lập B mục 7 — NV175-SUA):
- HTML được PHÂN TÍCH CẤU TRÚC bằng ``html.parser``: chỉ xét THẺ THẬT và
  THUỘC TÍNH của chúng — ``iframe[src]``/``script[src]`` (host + đường
  dẫn), ``class``/``data-sitekey``, và từng thẻ ``input`` với
  ``name``/``id``/``autocomplete``/``inputmode``/``maxlength`` — cộng nội
  dung MÃ của riêng thẻ ``<script>`` thật (mã thực thi, không phải văn
  bản hiển thị). Văn bản trang, chú thích HTML và mã trích dẫn trong bài
  viết KHÔNG BAO GIỜ là dấu hiệu: trang chỉ nhắc tên «recaptcha» /
  ``challenges.cloudflare.com`` trong đoạn văn không bị báo thử thách.
- Mọi thuộc tính của MỘT dấu hiệu phải nằm trên CÙNG MỘT thẻ (bản cũ dò
  chuỗi con trên cả văn bản trang, cộng dồn thuộc tính của các ô khác
  nhau và báo nhầm — vd ô mã bưu điện bị báo là ô OTP).
- URL chỉ khớp theo ĐOẠN đường dẫn có ngữ cảnh (cặp đoạn
  ``challenge/recaptcha``…), không khớp đoạn ``/recaptcha/`` đứng một
  mình (đường dẫn tài liệu chứa chữ đó từng bị báo nhầm).
- Tín hiệu mơ hồ (ô số + ``maxlength`` 4–8 không tên đặc thù) vẫn nhận
  diện nhưng hạ ``tin_cay`` xuống «thap».

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

from html.parser import HTMLParser
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


# ── Phân tích cấu trúc HTML ──────────────────────────────────────────────
# Chỉ thu THẺ + THUỘC TÍNH và nội dung mã của thẻ <script> thật. Văn bản
# hiển thị, chú thích (<!-- ... -->) và mã trích dẫn (đã thoát &lt;) đều
# bị bỏ qua — chúng không phải cấu trúc trang.

class _BoPhanTichHTML(HTMLParser):
    """Thu cấu trúc DOM tối thiểu cần cho nhận diện thử thách."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cac_the: list[tuple[str, dict[str, str]]] = []
        self.than_script: list[str] = []
        self._trong_script = False
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        tag = tag.lower()
        thuoc_tinh: dict[str, str] = {}
        for khoa, gia_tri in attrs:
            thuoc_tinh[khoa.lower()] = gia_tri or ""
        self.cac_the.append((tag, thuoc_tinh))
        if tag == "script":
            self._trong_script = True
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._trong_script:
            self.than_script.append("".join(self._buf))
            self._trong_script = False
            self._buf = []

    def handle_data(self, data: str) -> None:
        # Chỉ giữ MÃ trong thẻ <script> thật; mọi văn bản khác bỏ qua.
        if self._trong_script:
            self._buf.append(data)

    def hoan_tat(self) -> None:
        if self._trong_script and self._buf:
            self.than_script.append("".join(self._buf))
            self._buf = []
        self._trong_script = False


def _phan_tich(html: str) -> _BoPhanTichHTML:
    bo = _BoPhanTichHTML()
    try:
        bo.feed(html)
        bo.close()
    except Exception:
        # HTML hỏng nặng: dùng phần cấu trúc đã thu được (parser vốn khoan
        # dung; không để đầu vào không tin làm sập việc phân loại).
        pass
    bo.hoan_tat()
    return bo


def _host_va_duong(src: str) -> tuple[str, str]:
    """(host, đường dẫn) hạ chữ thường của một địa chỉ src; lỗi → rỗng."""
    if not src:
        return "", ""
    try:
        phan = urlsplit(src.strip())
    except ValueError:
        return "", ""
    return (phan.hostname or "").lower(), (phan.path or "").lower()


#: Tên trường tự khai là mã OTP một lần (thuộc tính name/id của thẻ input).
_TEN_TRUONG_OTP = frozenset({
    "otp", "otp_code", "otp-code", "ma_otp", "sms_code", "email_code",
    "verification_code", "verification-code",
})

#: Mảnh tên trường thư tín/địa chỉ — ô mang tên này là mã bưu điện/bưu
#: chính, không phải ô OTP (chỉ dùng để loại trừ ở phương án yếu dưới đây).
_MANH_TEN_THU_TIN = ("zip", "postal", "buu_dien", "buudien",
                     "post_code", "postcode")


def _dau_hieu_tu_cau_truc(bo: _BoPhanTichHTML) -> tuple[set[str], set[str]]:
    """Trả (loại khớp dấu hiệu MẠNH, loại chỉ khớp dấu hiệu YẾU).

    Dấu hiệu yếu duy nhất hiện nay: ô nhập số + maxlength 4–8 không tên
    đặc thù (phương án 3 của ma_otp) — mơ hồ với ô mã bưu điện nên kết
    quả hạ tin cậy, và ô tự khai là trường thư tín thì không tính.
    """
    manh: set[str] = set()
    yeu: set[str] = set()

    cac_lop: set[str] = set()
    for _, tt in bo.cac_the:
        cac_lop.update(tt.get("class", "").lower().split())

    src_iframe = [tt.get("src", "") for ten, tt in bo.cac_the
                  if ten == "iframe"]
    src_script = [tt.get("src", "") for ten, tt in bo.cac_the
                  if ten == "script"]

    # Turnstile / trang kiểm tra Cloudflare: host widget trên iframe hay
    # script THẬT, hoặc khối cf-turnstile của widget.
    for src in src_iframe + src_script:
        if _host_va_duong(src)[0] == "challenges.cloudflare.com":
            manh.add("turnstile")
    if "cf-turnstile" in cac_lop:
        manh.add("turnstile")

    # Widget reCAPTCHA: đường dẫn iframe (anchor = v2, bframe = khung
    # chọn hình) hoặc khối neo/mẫu của widget trong DOM.
    for src in src_iframe:
        _, duong = _host_va_duong(src)
        if ("/recaptcha/api2/bframe" in duong
                or "/recaptcha/enterprise/bframe" in duong):
            manh.add("chon_hinh")
        if ("/recaptcha/api2/anchor" in duong
                or "/recaptcha/enterprise/anchor" in duong):
            manh.add("recaptcha_v2")
    if any(tok.startswith("rc-imageselect") for tok in cac_lop):
        manh.add("chon_hinh")
    if "g-recaptcha" in cac_lop:
        manh.add("recaptcha_v2")
    if any(tt.get("id", "").lower() == "recaptcha-anchor"
           for _, tt in bo.cac_the):
        manh.add("recaptcha_v2")

    # reCAPTCHA v3: script api.js có render=…, hoặc MÃ trong thẻ <script>
    # thật gọi grecaptcha.execute (chữ trong đoạn văn/bài viết không tính).
    for src in src_script:
        try:
            phan = urlsplit(src.strip())
        except ValueError:
            continue
        if ((phan.path or "").lower().endswith("/recaptcha/api.js")
                and "render=" in (phan.query or "").lower()):
            manh.add("recaptcha_v3")
    if any("grecaptcha.execute" in ma.lower() for ma in bo.than_script):
        manh.add("recaptcha_v3")

    # Các ô nhập: MỌI thuộc tính của một dấu hiệu phải ở CÙNG một thẻ.
    co_img = False
    co_o = False
    img_captcha = False
    o_captcha = False
    for ten_the, tt in bo.cac_the:
        if ten_the == "img":
            co_img = True
            if ("captcha" in tt.get("src", "").lower()
                    or "captcha" in tt.get("alt", "").lower()
                    or "captcha" in tt.get("id", "").lower()
                    or "captcha" in tt.get("class", "").lower()):
                img_captcha = True
        if ten_the in ("input", "textarea"):
            co_o = True
            if ("captcha" in tt.get("name", "").lower()
                    or "captcha" in tt.get("id", "").lower()):
                o_captcha = True
        if ten_the != "input":
            continue
        ten_o = tt.get("name", "").lower()
        id_o = tt.get("id", "").lower()
        tu_dien = tt.get("autocomplete", "").lower().split()
        # TOTP của luồng đăng nhập (selector totpPin trong
        # _2FA_CODE_SELECTORS của c2a) — dấu hiệu HẸP và đặc thù.
        if ten_o in ("totppin", "totp_pin") or id_o in ("totppin", "totp_pin"):
            manh.add("xac_thuc_hai_lop")
        # Mã OTP — phương án mạnh: thuộc tính chuẩn one-time-code hoặc
        # tên trường mã xác minh (selector của _2FA_CODE_SELECTORS).
        if ("one-time-code" in tu_dien or ten_o in _TEN_TRUONG_OTP
                or id_o in _TEN_TRUONG_OTP):
            manh.add("ma_otp")
        # Mã OTP — phương án YẾU: ô nhập số (bàn phím số) giới hạn 4–8 ký
        # tự trên cùng một thẻ; ô tự khai là trường thư tín (tên kiểu mã
        # bưu điện hoặc autocomplete postal-code) thì KHÔNG tính.
        if (tt.get("inputmode", "").lower() == "numeric"
                and "postal-code" not in tu_dien
                and not any(m in ten_o or m in id_o
                            for m in _MANH_TEN_THU_TIN)):
            ml = tt.get("maxlength", "").strip()
            if ml.isdigit() and 4 <= int(ml) <= 8:
                yeu.add("ma_otp")

    # CAPTCHA chữ/hình cổ điển: ẢNH mã (img tự khai captcha ở src/alt/id/
    # class) kèm ô nhập, hoặc ô nhập tự khai captcha kèm một ảnh bất kỳ —
    # chữ «captcha» trong VĂN BẢN trang không tính.
    if (img_captcha and co_o) or (o_captcha and co_img):
        manh.add("captcha_chu")

    return manh, yeu


def _loai_tu_url(url: str) -> str | None:
    """Loại theo cấu trúc URL: host widget hoặc ĐOẠN đường dẫn có ngữ
    cảnh. Query/fragment bị bỏ trước (nơi chứa token). Đoạn
    ``/recaptcha/`` đứng một mình (trang tài liệu) KHÔNG còn là dấu hiệu.
    """
    if not url:
        return None
    try:
        phan = urlsplit(url.strip())
    except ValueError:
        return None
    host = (phan.hostname or "").lower()
    doan = [d for d in (phan.path or "").lower().split("/") if d]

    if host == "challenges.cloudflare.com":
        return "turnstile"

    def co_cap(a: str, b: str) -> bool:
        return any(doan[i] == a and doan[i + 1] == b
                   for i in range(len(doan) - 1))

    if co_cap("challenge", "recaptcha"):
        return "recaptcha_v2"
    if co_cap("challenge", "botguard"):
        return "botguard"
    if doan[:2] == ["cdn-cgi", "challenge-platform"]:
        return "turnstile"
    if co_cap("signin", "challenge"):
        return "thu_thach_khac"
    if "challenge" in doan:
        return "thu_thach_khac"
    return None


#: Thứ tự ưu tiên phân loại (cao → thấp): thử thách NHÌN THẤY được thắng
#: thử thách vô hình; ô nhập mã (TOTP/OTP) thắng CAPTCHA chữ cổ điển vì ô
#: mã là bước người dùng đang bị chặn trực tiếp (trang vừa có CAPTCHA chữ
#: vừa có ô OTP → báo loại OTP).
_THU_TU_UU_TIEN = ("turnstile", "chon_hinh", "recaptcha_v2", "recaptcha_v3",
                   "xac_thuc_hai_lop", "ma_otp", "captcha_chu")


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

    manh, yeu = _dau_hieu_tu_cau_truc(_phan_tich(html))

    # Ưu tiên: dấu hiệu HTML theo thứ tự cố định, rồi mới tới URL.
    for loai in _THU_TU_UU_TIEN:
        if loai in manh:
            return {"co_thu_thach": True, "loai": loai, "can_nguoi": True,
                    "tin_cay": "cao", "dau_hieu": [f"html:{loai}"]}
        if loai in yeu:
            return {"co_thu_thach": True, "loai": loai, "can_nguoi": True,
                    "tin_cay": "thap", "dau_hieu": [f"html:{loai}"]}
    loai_theo_url = _loai_tu_url(url)
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
