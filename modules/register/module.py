"""Module register — điều phối ĐĂNG KÝ TÀI KHOẢN có NGƯỜI TRONG VÒNG LẶP (NV174).

Mô hình: module chuẩn bị hộp thư + mật khẩu sinh ngẫu nhiên, tải trang đăng ký
để NHẬN DIỆN BƯỚC theo cấu trúc, rồi DỪNG ĐÚNG ĐIỂM CẦN NGƯỜI. Mọi tương tác tay
(điền biểu mẫu, vượt thử thách chống bot, nhập mã, nhập số điện thoại) do CHÍNH
CHỦ MÁY làm trên trình duyệt từ xa (noVNC); module không bao giờ tự điền, không
tự vượt CAPTCHA/thử thách, không tự gửi mã đi đâu, không tạo tài khoản hàng loạt.

NGUỒN c2a (theo LUẬT PORT — ghi rõ, không che giấu):
  Mã thật đọc từ bản sao ~/workspace/c2a-nv146 của /opt/claude-c2a/chatgpt2api
  (commit d879b03 — /opt không còn trên máy này nên dùng bản sao):
  - services/register/openai_register.py: hàm _random_password  →  sinh_mat_khau
  - services/register/mail_provider.py:
      BaseMailProvider (vòng lặp hỏi tới hạn giờ)         →  _cho_ma
      InbucketMailProvider (liệt kê + đọc chi tiết thư)   →  HopThuInbucket
      _extract_code (regex trích mã 6 số, bỏ qua mã mẫu)  →  trich_ma_xac_minh
      _message_matches_email (lọc thư đúng địa chỉ)       →  thu_khop_email
      _message_tracking_ref (chống đọc trùng một thư)     →  _ref_theo_doi_thu
      _parse_received_at (phân tích thời điểm nhận thư)   →  _phan_tich_thoi_diem
      _config / _random_mailbox_name                      →  _cau_hinh_hop_thu / _ten_hop_thu_ngau_nhien
  Chỉ đổi: lớp kết nối (httpx + bộ kiểm URL của lõi app/tai_url_khong_tin.py và
  app/tu_xa.py), khuôn module web2api (nap/cai/suc_khoe/dung + chuẩn thiet_bi),
  và xác thực (cổng quản trị của lõi — mọi hành động nguy hiểm qua /api/thiet-bi).
  c2a dùng requests; bản port dùng httpx bất đồng bộ.

PHẦN KHÔNG PORT từ c2a (hàng rào cứng của NV174 — xem thêm báo cáo):
  - SentinelTokenGenerator (bộ giải Proof-of-Work của thử thách chống bot) — là
    hành vi VƯỢT thử thách chống bot, spec cấm tuyệt đối.
  - register_service.py (vòng lặp hàng loạt: nhiều luồng, chỉ tiêu số lượng tài
    khoản, chế độ total/quota/available) — tạo tài khoản hàng loạt, spec cấm.
  - Luồng xác thực riêng của OpenAI (endpoint auth.openai.com, client_id,
    đổi mã OAuth/PKCE) — module này nhà-cung-cấp-trung-tính, không bóc auth riêng.
  - Header giả mạo dấu vân tay trình duyệt (sec-ch-ua…, impersonate=chrome) —
    giả mạo trình duyệt, spec cấm.
  - _handle_email_verification_link (tự dò và tự bấm liên kết xác minh) — trong
    mô hình human-in-loop, NGƯỜI tự bấm liên kết trên trình duyệt từ xa.

MỚI viết (c2a không có): phan_loai_trang — nhận diện bước đăng ký theo CẤU TRÚC
HTML (iframe/script thử thách + data-sitekey, input tel, one-time-code,
form email+mật khẩu). Trang lạ → «khong_ro» → dừng fail-closed ở «can_nguoi».

VÒNG SỬA NV174-SUA (soát độc lập C2, R1–R5 — sửa theo LỚP, không danh sách):
  - R1 (đua giữ chỗ): _bat_dau kiểm + đặt «dang_khoi_tao» + tăng bộ đếm trong
    CÙNG một khối khoá, không await ở giữa; thất bại sau giữ chỗ → _nha_cho
    hoàn cả trạng thái lẫn bộ đếm.
  - R2 (bí mật sống dai): MỌI đường kết thúc lượt (hoàn tất / huỷ / hết hạn)
    đi qua _ket_thuc_luot → XOÁ bí mật khỏi kho ngay; lượt DỞ có hạn giữ tối
    đa 30 phút kể từ lúc bắt đầu rồi tự hết hạn + xoá (quyết điều phối 20/10:
    hiện một lần rồi xoá; chưa từng được hiện thì tự xoá sau 30 phút);
    nap() dọn tệp bí mật mồ côi không thuộc lượt hiện tại.
  - R5 (cổng người ghi nhận bị bỏ qua): phan_loai_trang ĐẢO MẶC ĐỊNH — form
    đăng ký chỉ «sẵn sàng» khi trang KHÔNG tải script/iframe từ gốc khác;
    có mã/khung ngoài (dù không rõ của nhà cung cấp nào) → «thu_thach» →
    can_nguoi. doi_ma còn bị cổng bởi cờ trang-đầu-có-mã-ngoài +
    người-đã-ghi-nhận. Ô điện thoại nhận cả inputmode="tel".
  - R3 (DNS rebinding hộp thư): HopThuInbucket gọi qua TransportGhimIp của
    lõi với ĐÚNG hàm kiểm mà kiem_url_lan dùng — ghim IP lúc gọi thật.
  - R4 (trích mã bừa): trich_ma_xac_minh lọc theo miền người gửi đã khai
    (cấu hình mien_gui_cho_phep, so hậu tố theo nhãn) và chỉ trích số 6 chữ
    số khi có NGỮ CẢNH mã (tiêu đề/nội dung cạnh từ khoá mã, hoặc thẻ bọc).
"""

from __future__ import annotations

import asyncio
import html.parser
import json
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from app import kho as kho_loi
from app.chuyen_tiep import TransportGhimIp
from app.tai_url_khong_tin import (
    LoiTaiUrl,
    kiem_dns_va_ip,
    kiem_ip_khong_tin,
    kiem_url_co_ban,
)
from app.tu_xa import _dia_chi_bi_chan, kiem_url_lan

log = logging.getLogger("web2api.register")

# ── Hằng số ────────────────────────────────────────────────────────────────────

TIEN_TO_MAC_DINH = "dang_ky"
TRAN_BYTE_TRANG = 512_000          # trần nội dung một trang đăng ký tải về
TRAN_BYTE_HOP_THU = 1_048_576      # trần nội dung phản hồi API hộp thư
TRAN_GIAY_TAI = 20.0
TRAN_GIAY_HOP_THU = 10.0
TRAN_LUOT_NGAY_CUNG = 5            # trần cứng số lượt/ngày dù cấu hình ra sao
DO_DAI_MAT_KHAU = 20
# Hạn giữ bí mật của lượt DỞ: quá hạn này (kể từ lúc bắt đầu) lượt tự đóng +
# xoá bí mật (quyết điều phối đợt rủi ro cao 20/10 — R2).
THOI_HAN_GIU_BI_MAT_GIAY = 30 * 60
BUOC_DANG_CHAY = ("cho_nguoi_dien", "doi_ma_xac_minh", "can_nguoi")
# «dang_khoi_tao» = đã GIỮ CHỖ (R1) nhưng chưa tải xong trang: vẫn tính là
# đang chạy cho cổng một-lượt-một-lúc và vòng dọn hết hạn.
BUOC_GIU_CHO = BUOC_DANG_CHAY + ("dang_khoi_tao",)
MAU_MIEN = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,253}[a-z0-9])?\.[a-z]{2,}$")

# Host của nhà cung cấp widget thử thách chống bot (tập đóng theo ĐIỂM TÍCH HỢP
# CẤU TRÚC của trình duyệt — iframe/script tải từ các host này: không đổi theo
# thời gian, không phải danh sách từ khoá nội dung trang).
_HOST_THU_THACH = frozenset({
    "challenges.cloudflare.com",      # Cloudflare Turnstile
    "hcaptcha.com", "js.hcaptcha.com", "imgs.hcaptcha.com",
    "recaptcha.net", "www.recaptcha.net",
})


def _host_cua(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


# ── Sinh mật khẩu (port từ openai_register._random_password) ──────────────────


def sinh_mat_khau(do_dai: int = DO_DAI_MAT_KHAU) -> str:
    """Sinh mật khẩu ngẫu nhiên mạnh (port từ c2a — đổi tên + Việt hoá)."""
    do_dai = max(16, int(do_dai or 0))
    nhom = [
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "abcdefghijklmnopqrstuvwxyz",
        "0123456789",
        "!@#$%^&*()-_=+[]{}",
    ]
    tat_ca = "".join(nhom)
    mat_khau = [secrets.choice(n) for n in nhom]
    mat_khau += [secrets.choice(tat_ca) for _ in range(do_dai - len(nhom))]
    secrets.SystemRandom().shuffle(mat_khau)
    return "".join(mat_khau)


# ── Phân loại trang đăng ký theo cấu trúc (mới viết) ───────────────────────────


class _TrichTinHieu(html.parser.HTMLParser):
    """Gom tín hiệu CẤU TRÚC của trang: không khớp theo nội dung chữ."""

    def __init__(self) -> None:
        super().__init__()
        self.nguon_iframe: list[str] = []
        self.nguon_script: list[str] = []
        self.co_sitekey = False          # data-sitekey: widget recaptcha/hcaptcha/turnstile
        self.loai_o_nhap: list[str] = []  # các type="…" của <input>
        self.autocomplete: list[str] = []  # các autocomplete="…" của <input>
        self.inputmode: list[str] = []    # các inputmode="…" của <input>
        self.form: list[dict] = []        # mỗi form: {"loai": [...], "autocomplete": [...], "inputmode": [...]}
        self._trong_form: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ds = {k.lower(): (v or "") for k, v in attrs}
        if tag == "iframe":
            src = ds.get("src", "")
            if src:
                self.nguon_iframe.append(src)
        elif tag == "script":
            src = ds.get("src", "")
            if src:
                self.nguon_script.append(src)
        elif tag == "form":
            self._trong_form = {"loai": [], "autocomplete": [], "inputmode": []}
            self.form.append(self._trong_form)
        elif tag == "input":
            loai = ds.get("type", "text").lower()
            ac = ds.get("autocomplete", "").lower()
            im = ds.get("inputmode", "").lower()
            self.loai_o_nhap.append(loai)
            if ac:
                self.autocomplete.append(ac)
            if im:
                self.inputmode.append(im)
            if self._trong_form is not None:
                self._trong_form["loai"].append(loai)
                if ac:
                    self._trong_form["autocomplete"].append(ac)
                if im:
                    self._trong_form["inputmode"].append(im)
        if ds.get("data-sitekey"):
            self.co_sitekey = True


def _khac_goc(src: str, goc: str) -> bool:
    """src của script/iframe có trỏ RA NGOÀI gốc của trang không (cấu trúc).

    Đường tương đối / data: / không host → cùng gốc. Không biết gốc trang
    (url_trang trống) thì mọi host tuyệt đối đều coi là ngoài — đóng lại (R5).
    """
    host = _host_cua(src)
    if not host:
        return False
    if not goc:
        return True
    return host != goc


def phan_loai_trang(html: str, url_trang: str = "") -> dict[str, Any]:
    """Nhận diện bước đăng ký theo cấu trúc HTML. Không khớp nội dung chữ.

    R5 — đảo mặc định: form đăng ký CHỈ được coi là «sẵn sàng cho người điền»
    khi trang không tải script/iframe từ gốc khác. Bất kỳ mã/khung ngoài nào
    (dù không rõ thuộc nhà cung cấp thử thách nào — Arkose, GeeTest, Friendly
    Captcha…) cũng đẩy về «thu_thach» → dừng «can_nguoi», thay vì liệt kê
    từng nhà cung cấp. `chi_tiet.co_ma_ngoai` cho biết trang đầu có tải mã
    ngoài hay không (cổng ghi-nhận cho doi_ma — xem _doi_ma).
    """
    trich = _TrichTinHieu()
    try:
        trich.feed(html or "")
    except Exception:
        pass
    goc = _host_cua(url_trang)
    co_ma_ngoai = any(
        _khac_goc(s, goc) for s in trich.nguon_iframe + trich.nguon_script
    )
    for src in trich.nguon_iframe + trich.nguon_script:
        host = _host_cua(src)
        if not host:
            continue
        if host in _HOST_THU_THACH or host.endswith(".hcaptcha.com") or (
            "google.com" in host or "gstatic.com" in host
        ) and "recaptcha" in src.lower():
            return {"loai": "thu_thach",
                    "chi_tiet": {"nguon": host, "co_ma_ngoai": True}}
    if trich.co_sitekey:
        return {"loai": "thu_thach",
                "chi_tiet": {"widget": "data-sitekey", "co_ma_ngoai": co_ma_ngoai}}
    if "tel" in trich.loai_o_nhap or "tel" in trich.inputmode:
        return {"loai": "xac_minh_dien_thoai", "chi_tiet": {"co_ma_ngoai": co_ma_ngoai}}
    if "one-time-code" in trich.autocomplete:
        return {"loai": "doi_ma", "chi_tiet": {"co_ma_ngoai": co_ma_ngoai}}
    for f in trich.form:
        if "email" in f["loai"] and "password" in f["loai"]:
            if co_ma_ngoai:
                return {"loai": "thu_thach",
                        "chi_tiet": {"nguon": "ma_ngoai_khong_ro", "co_ma_ngoai": True}}
            return {"loai": "form_dang_ky", "chi_tiet": {"co_ma_ngoai": False}}
    return {"loai": "khong_ro", "chi_tiet": {"co_ma_ngoai": co_ma_ngoai}}


# ── Trích mã xác minh (port từ mail_provider._extract_code) ────────────────────


_MAU_BO_QUA = "177010"  # mã mẫu trong tài liệu của c2a — không bao giờ coi là mã thật


def _hop_le(ma: str | None) -> bool:
    return bool(ma) and ma.isdigit() and len(ma) == 6 and ma != _MAU_BO_QUA


def _rut_gon(html: str) -> str:
    return re.sub(r"\s+", " ", html or "").strip()


def _mien_nguoi_gui(thu: dict[str, Any]) -> str:
    """Miền của người gửi theo CẤU TRÚC địa chỉ (phần sau @ cuối), chữ thường."""
    gui = str(thu.get("sender") or thu.get("from") or "")
    m = re.search(r"@([A-Za-z0-9.-]+)", gui)
    return m.group(1).lower().rstrip(".") if m else ""


def trich_ma_xac_minh(thu: dict[str, Any],
                      mien_gui_cho_phep: str = "") -> str | None:
    """Trích mã xác minh 6 số từ một bức thư (port từ c2a, thêm mẫu tiếng Việt).

    R4 — hai lớp lọc theo nguyên tắc, không theo danh sách:
    (1) Khi cấu hình khai `mien_gui_cho_phep`: chỉ xét thư mà miền người gửi
        BẰNG miền đó hoặc là TIỂU MIỀN của nó (so hậu tố theo nhãn «.» — thư
        từ miền lạ, kể cả miền chứa miền khai ở giữa, bị bỏ qua hẳn).
    (2) Số 6 chữ số chỉ được trích khi có NGỮ CẢNH mã: tiêu đề/nội dung nằm
        cạnh từ khoá chỉ mã, khuôn XXX-XXX ở đầu tiêu đề, hoặc số nằm trong
        thẻ HTML (mã hiển thị lớn). KHÔNG còn đường «số 6 chữ số bất kỳ trong
        thân» — số đơn hàng/mã bưu điện không bị trích nhầm thành mã.
    """
    ten_mien = (mien_gui_cho_phep or "").strip().lower().lstrip("@")
    if ten_mien:
        mien = _mien_nguoi_gui(thu)
        if not mien or not (mien == ten_mien or mien.endswith("." + ten_mien)):
            return None
    tieu_de = _rut_gon(str(thu.get("subject") or ""))
    van_ban = _rut_gon(str(thu.get("text_content") or ""))
    html_nd = str(thu.get("html_content") or "")

    m = re.search(r"^([A-Z0-9]{3}-[A-Z0-9]{3})\s", tieu_de)
    if m and _hop_le(m.group(1)):
        return m.group(1)

    mau_ngu_canh = [
        r"(?:verification code|code is|code:|your code|Mã xác minh|mã của bạn|mã xác thực)\s*[:：]?\s*(\d{6})",
        # Từ khoá chỉ mã + cầu ngắn không chứa số («Mã xác minh của bạn là …»).
        r"(?:mã xác minh|mã xác thực|verification code|your code)[^0-9]{0,24}?(\d{6})",
        r">\s*(\d{6})\s*<",
    ]
    for nd in (tieu_de, html_nd, van_ban):
        for mau in mau_ngu_canh:
            m = re.search(mau, nd, re.IGNORECASE)
            if m and _hop_le(m.group(1)):
                return m.group(1)
    return None


def thu_khop_email(thu: dict[str, Any], email: str) -> bool:
    """Thư có đúng gửi tới hộp thư này không (port từ _message_matches_email)."""
    muc_tieu = (email or "").strip().lower()
    ung_vien: list[str] = []
    for khoa in ("recipients", "to", "cc"):
        gia_tri = thu.get(khoa)
        if isinstance(gia_tri, str):
            ung_vien.append(gia_tri)
        elif isinstance(gia_tri, list):
            ung_vien.extend(str(x) for x in gia_tri)
    tho = thu.get("raw") or {}
    if isinstance(tho, dict):
        for nguon in (tho.get("header"), tho.get("headers"), tho):
            if isinstance(nguon, dict):
                for khoa in ("To", "to", "Cc", "cc"):
                    gia_tri = nguon.get(khoa)
                    if isinstance(gia_tri, str):
                        ung_vien.append(gia_tri)
                    elif isinstance(gia_tri, list):
                        ung_vien.extend(str(x) for x in gia_tri)
    ung_vien = [u.strip().lower() for u in ung_vien if u and u.strip()]
    if not ung_vien:
        return True  # không có thông tin người nhận: hộp thư Inbucket đã theo tên
    return any(muc_tieu in u or u == muc_tieu for u in ung_vien)


def _ref_theo_doi_thu(thu: dict[str, Any]) -> str:
    """Tham chiếu chống đọc trùng một thư (port từ _message_tracking_ref)."""
    if thu.get("message_id"):
        return f"id:{thu['message_id']}"
    return f"subj:{thu.get('subject', '')}|from:{thu.get('sender', '')}|at:{thu.get('received_at', '')}"


def _phan_tich_thoi_diem(text: str | None) -> str:
    """Phân tích thời điểm nhận thư về ISO (port từ _parse_received_at)."""
    if not text:
        return ""
    try:
        return parsedate_to_datetime(str(text)).isoformat()
    except (ValueError, TypeError):
        return ""


def _ten_hop_thu_ngau_nhien() -> str:
    """Tên hộp thư ngẫu nhiên (port từ _random_mailbox_name)."""
    return "nv" + secrets.token_hex(6)


# ── Hộp thư Inbucket (port từ InbucketMailProvider — chỉ phần chạy được) ──────


class HopThuInbucket:
    """Lớp hộp thư Inbucket do chính chủ máy tự host.

    Port từ services/register/mail_provider.py của c2a (InbucketMailProvider +
    vòng lặp wait_for/wait_for_code của BaseMailProvider); chỉ đổi requests →
    httpx bất đồng bộ và chèn Authorization khi có khoá.
    """

    def __init__(self, api: str, khoa: str = "") -> None:
        self.api = api.rstrip("/")
        self.khoa = khoa or ""

    def _tieu_de(self) -> dict[str, str]:
        if self.khoa:
            return {"Authorization": f"Bearer {self.khoa}"}
        return {}

    async def _goi(self, duong: str) -> Any:
        url = f"{self.api}{duong}"
        # R3: ghim IP LÚC GỌI qua TransportGhimIp của lõi với ĐÚNG hàm kiểm
        # mà kiem_url_lan dùng lúc cai() — tên máy bị tái gắn DNS về loopback/
        # metadata giữa kiểm và gọi thì kết nối bị chặn tại tầng vận chuyển,
        # khoá hộp thư không tới được địa chỉ cấm.
        van_chuyen = TransportGhimIp(kiem_ip=_dia_chi_bi_chan)
        async with httpx.AsyncClient(transport=van_chuyen,
                                     timeout=TRAN_GIAY_HOP_THU) as kh:
            async with kh.stream("GET", url, headers=self._tieu_de()) as res:
                if res.status_code != 200:
                    raise RuntimeError(f"hộp thư trả mã {res.status_code}")
                than = bytearray()
                async for khuc in res.aiter_raw():
                    than += khuc
                    if len(than) > TRAN_BYTE_HOP_THU:
                        raise RuntimeError("phản hồi hộp thư vượt trần dung lượng")
                return json.loads(bytes(than).decode("utf-8", "replace") or "null")

    async def kiem_tra(self) -> bool:
        try:
            kq = await self._goi("/api/v1/mailbox/web2api-kiem-tra")
            return isinstance(kq, list)
        except Exception:
            return False

    def _chuan_hoa_thu(self, chi_tiet: dict, email: str) -> dict[str, Any]:
        than = chi_tiet.get("body") or {}
        tieu_de_tho = chi_tiet.get("header") or {}
        nguoi_nhan: list[str] = []
        for khoa in ("To", "to", "Cc", "cc"):
            gia_tri = tieu_de_tho.get(khoa)
            if isinstance(gia_tri, str):
                nguoi_nhan.append(gia_tri)
            elif isinstance(gia_tri, list):
                nguoi_nhan.extend(str(x) for x in gia_tri)
        if not nguoi_nhan:
            nguoi_nhan = [email]
        return {
            "message_id": str(chi_tiet.get("id") or ""),
            "subject": str(chi_tiet.get("subject") or ""),
            "sender": str(chi_tiet.get("from") or chi_tiet.get("sender") or ""),
            "text_content": str(than.get("text") or ""),
            "html_content": str(than.get("html") or ""),
            "received_at": _phan_tich_thoi_diem(chi_tiet.get("date")),
            "recipients": nguoi_nhan,
            "raw": chi_tiet,
        }

    async def _liet_ke(self, ten_hop_thu: str) -> list[dict[str, Any]]:
        ds = await self._goi(f"/api/v1/mailbox/{ten_hop_thu}")
        return ds if isinstance(ds, list) else []

    async def _doc_chi_tiet(self, ten_hop_thu: str, ma_thu: str, email: str) -> dict[str, Any] | None:
        try:
            chi_tiet = await self._goi(f"/api/v1/mailbox/{ten_hop_thu}/{ma_thu}")
        except Exception:
            return None
        if not isinstance(chi_tiet, dict):
            return None
        return self._chuan_hoa_thu(chi_tiet, email)

    async def cho_ma(self, ten_hop_thu: str, email: str, han_giay: float,
                     da_xem: set[str],
                     mien_gui_cho_phep: str = "") -> str | None:
        """Hỏi vòng tới hạn giờ, bỏ qua thư đã xem, chỉ lấy thư đúng hộp thư.

        Port vòng lặp wait_for/wait_for_code của BaseMailProvider.
        `mien_gui_cho_phep` (R4): miền người gửi đã khai — thư ngoài miền
        không được trích mã.
        """
        het_han = time.monotonic() + max(1.0, han_giay)
        while True:
            try:
                ds = await self._liet_ke(ten_hop_thu)
            except Exception:
                ds = []
            for muc in reversed(ds):  # thư mới nhất trước (như c2a)
                ma_thu = str(muc.get("id") or "")
                if not ma_thu:
                    continue
                ref = f"id:{ma_thu}"
                if ref in da_xem:
                    continue
                da_xem.add(ref)
                thu = await self._doc_chi_tiet(ten_hop_thu, ma_thu, email)
                if thu is None or not thu_khop_email(thu, email):
                    continue
                ma = trich_ma_xac_minh(thu, mien_gui_cho_phep)
                if ma:
                    return ma
            con_lai = het_han - time.monotonic()
            if con_lai <= 0:
                return None
            await asyncio.sleep(min(2.0, con_lai))


# ── Tải trang đăng ký (dùng đúng bộ kiểm SSRF của lõi) ────────────────────────


async def _tai_trang_dang_ky(url: str) -> tuple[int, str]:
    """Tải trang đăng ký về để NHẬN DIỆN cấu trúc.

    Dùng đúng bộ kiểm của lõi: kiem_url_co_ban + kiem_dns_va_ip +
    TransportGhimIp (chống DNS rebinding) — cùng bộ mà tai_url_khong_tin dùng.
    Khác tai_url ở chỗ CHẤP NHẬN thân phản hồi cả khi mã HTTP không 2xx (trang
    thử thách chống bot thường trả 403 kèm HTML) để phân loại và DỪNG đúng điểm
    «can_nguoi» thay vì vỡ. Không theo chuyển hướng: chuyển hướng → trang lạ →
    dừng fail-closed (người dùng khai URL cuối sau chuyển hướng nếu muốn).
    """
    phan = kiem_url_co_ban(url)  # ném LoiTaiUrl nếu URL không đạt
    cong = phan.port or (443 if phan.scheme.lower() == "https" else 80)
    await kiem_dns_va_ip(phan.hostname, cong)  # chặn IP nội bộ/metadata/link-local…
    van_chuyen = TransportGhimIp(kiem_ip=kiem_ip_khong_tin)
    try:
        async with httpx.AsyncClient(transport=van_chuyen, follow_redirects=False,
                                     timeout=TRAN_GIAY_TAI) as kh:
            async with kh.stream("GET", url, headers={
                "user-agent": "web2api-register/0.1 (nhan-dien-cau-truc-trang)",
                "accept": "text/html,*/*",
                "accept-encoding": "identity",
            }) as res:
                ma = res.status_code
                dem = bytearray()
                async for khuc in res.aiter_raw():
                    dem += khuc
                    if len(dem) > TRAN_BYTE_TRANG:
                        break
    except httpx.HTTPError as e:
        raise LoiTaiUrl("loi_mang", f"lỗi mạng khi tải trang: {type(e).__name__}") from e
    return ma, bytes(dem).decode("utf-8", "replace")


# ── Module ────────────────────────────────────────────────────────────────────


class Module:
    """Điều phối đăng ký tài khoản: chuẩn bị → nhận diện → dừng đúng điểm cần người."""

    def __init__(self, manifest: dict[str, Any]) -> None:
        self.manifest = manifest
        self.tien_to = str(manifest.get("tien_to") or TIEN_TO_MAC_DINH)
        self._cau_hinh: dict[str, Any] = {}
        self._tt: dict[str, Any] = {}
        self._khoa = threading.RLock()
        self._id_luot = f"{self.tien_to}.luot"
        self._id_tt = f"{self.tien_to}.trang_thai"

    # ── Vòng đời ──

    def nap(self, cau_hinh: dict[str, Any]) -> None:
        self._cau_hinh = dict(cau_hinh or {})
        self._tai_trang_thai()
        self._don_bi_mat_mo_coi()

    def _duong_tt(self):
        thu_muc = kho_loi.thu_muc_du_lieu() / "du_lieu" / "register"
        thu_muc.mkdir(parents=True, exist_ok=True)
        return thu_muc / "trang_thai.json"

    def _tai_trang_thai(self) -> None:
        mac_dinh = {"buoc": "chua_bat_dau", "luot_id": None, "ly_do": None,
                    "huong_dan": None, "email_che": None,
                    "ngay_dem": "", "so_luot_hom_nay": 0, "thu_da_xem": [],
                    "bat_dau_luc": None, 
                    "trang_dau_co_ma_ngoai": False, "nguoi_da_ghi_nhan": False}
        try:
            du_lieu = json.loads(self._duong_tt().read_text(encoding="utf-8"))
            if isinstance(du_lieu, dict):
                mac_dinh.update(du_lieu)
        except (OSError, ValueError):
            pass
        self._tt = mac_dinh

    def _luu_trang_thai(self) -> None:
        kho_loi.ghi_nguyen_tu(self._duong_tt(), json.dumps(self._tt, ensure_ascii=False),
                              quyen=0o600)

    # ── Chốt vòng đời bí mật của lượt (R1/R2) ──

    @staticmethod
    def _bay_gio_ts() -> float:
        return time.time()

    def _xoa_bi_mat_luot(self) -> None:
        luot_id = self._tt.get("luot_id") or ""
        if luot_id:
            kho_loi.xoa_bi_mat(self._ten_bi_mat(luot_id))

    def _ket_thuc_luot(self, buoc: str, huong_dan: str) -> None:
        """Đóng lượt — MỌI đường kết thúc đều qua đây để XOÁ bí mật khỏi kho
        NGAY (R2: trước đây chỉ huỷ mới xoá, hoàn tất thì bí mật nằm vĩnh
        viễn). Gọi khi đã giữ khoá."""
        self._xoa_bi_mat_luot()
        self._tt.update({
            "buoc": buoc, "luot_id": None, "ly_do": None,
            "huong_dan": huong_dan, "thu_da_xem": [],
            "bat_dau_luc": None, 
            "trang_dau_co_ma_ngoai": False, "nguoi_da_ghi_nhan": False,
        })
        self._luu_trang_thai()

    def _nha_cho(self) -> None:
        """Hoàn chỗ đã giữ của _bat_dau khi phần sau giữ chỗ THẤT BẠI (R1):
        nhả trạng thái «đang chạy» VÀ hoàn bộ đếm — lượt chưa từng bắt đầu
        thành công thì không tính vào hạn ngày. Bí mật (nếu đã kịp ghi) bị
        xoá bởi _ket_thuc_luot."""
        with self._khoa:
            self._tt["so_luot_hom_nay"] = max(
                0, int(self._tt.get("so_luot_hom_nay", 0) or 0) - 1)
            self._ket_thuc_luot(
                "chua_bat_dau",
                "Lượt giữ chỗ đã được hoàn lại sau lỗi — chưa có gì được tạo.")

    def _don_luot_het_han(self) -> None:
        """HẠN GIỮ TỐI ĐA cho lượt dở (R2 + quyết điều phối 20/10): lượt chưa
        kết thúc quá THOI_HAN_GIU_BI_MAT_GIAY kể từ lúc bắt đầu → tự đóng +
        XOÁ bí mật, dù thông tin đã hiển thị ở kết quả bắt đầu hay chưa (hiển
        thị MỘT lần ở phản hồi bắt đầu; quá 30 phút mà chưa hoàn tất thì coi
        như lượt bỏ — bí mật không nằm lại trong kho)."""
        with self._khoa:
            if self._tt.get("buoc") not in BUOC_GIU_CHO:
                return
            bat_dau = self._tt.get("bat_dau_luc")
            if not isinstance(bat_dau, (int, float)):
                return
            if self._bay_gio_ts() - float(bat_dau) < THOI_HAN_GIU_BI_MAT_GIAY:
                return
            self._ket_thuc_luot(
                "het_han",
                "Lượt tự hết hạn: quá 30 phút kể từ lúc bắt đầu mà chưa hoàn "
                "tất — bí mật của lượt đã XOÁ khỏi kho. Muốn đăng ký thì bắt "
                "đầu lượt mới (thông tin chỉ hiện một lần ở kết quả bắt đầu).")
            self._tt["email_che"] = None
            self._luu_trang_thai()
            log.info("register: lượt hết hạn 30 phút chưa hiển thị — đã xoá bí mật")

    def _don_bi_mat_mo_coi(self) -> None:
        """Xoá tệp bí mật register-* KHÔNG thuộc lượt hiện tại (R2: mồ côi do
        đua R1 cũ hoặc tiến trình chết giữa chừng để lại)."""
        try:
            thu_muc = kho_loi.thu_muc_du_lieu() / "bi_mat"
            tep_ds = list(thu_muc.glob("register-*.json")) if thu_muc.is_dir() else []
        except OSError:
            return
        luot_id = self._tt.get("luot_id")
        giu = self._ten_bi_mat(luot_id) if luot_id else None
        for tep in tep_ds:
            if tep.stem != giu:
                kho_loi.xoa_bi_mat(tep.stem)

    def _bat(self) -> bool:
        return str(self._cau_hinh.get("bat", "false")).strip().lower() == "true"

    def _han_moi_ngay(self) -> int:
        try:
            han = int(str(self._cau_hinh.get("gioi_han_moi_ngay", "1")).strip())
        except (TypeError, ValueError):
            han = 1
        return max(1, min(TRAN_LUOT_NGAY_CUNG, han))

    def _hop_thu(self) -> HopThuInbucket:
        return HopThuInbucket(str(self._cau_hinh.get("hop_thu_api") or ""),
                              str(self._cau_hinh.get("hop_thu_khoa") or ""))

    @staticmethod
    def _hom_nay() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def _kiem_ngay(self) -> None:
        if self._tt.get("ngay_dem") != self._hom_nay():
            self._tt["ngay_dem"] = self._hom_nay()
            self._tt["so_luot_hom_nay"] = 0

    @staticmethod
    def _che_email(email: str) -> str:
        cuc_bo, _, mien = (email or "").partition("@")
        dau = (cuc_bo[:1] + "…") if cuc_bo else "…"
        return f"{dau}@{mien}" if mien else "…"

    def _tt_cong_khai(self) -> dict[str, Any]:
        return {
            "buoc": self._tt.get("buoc"),
            "ly_do": self._tt.get("ly_do"),
            "huong_dan": self._tt.get("huong_dan"),
            "email_che": self._tt.get("email_che"),
        }

    # ── Cài đặt ──

    async def cai(self, cau_hinh: dict[str, Any]) -> dict[str, Any]:
        ch = dict(cau_hinh or {})
        bat = str(ch.get("bat", "false")).strip().lower() == "true"
        ket: dict[str, Any] = {"hop_thu_khoa": {"ok": True, "che": ""}}
        khoa = str(ch.get("hop_thu_khoa") or "")
        if khoa:
            ket["hop_thu_khoa"] = {"ok": True, "che": kho_loi.che(khoa)}
        url_dang_ky = str(ch.get("url_dang_ky") or "").strip()
        hop_thu_api = str(ch.get("hop_thu_api") or "").strip()
        hop_thu_mien = str(ch.get("hop_thu_mien") or "").strip().lower()

        mien_gui = str(ch.get("mien_gui_cho_phep") or "").strip().lower().lstrip("@")
        luu = {
            "bat": "true" if bat else "false",
            "url_dang_ky": url_dang_ky,
            "hop_thu_api": hop_thu_api,
            "hop_thu_mien": hop_thu_mien,
            "hop_thu_khoa": khoa,
            "gioi_han_moi_ngay": str(ch.get("gioi_han_moi_ngay", "1")).strip() or "1",
            "mien_gui_cho_phep": mien_gui,
        }
        if not bat:
            return {"ok": True, "ket_qua_tung_khoa": ket, "loi": None, "cau_hinh_luu": luu}

        def loi(khoa_che: str, thong_bao: str) -> dict[str, Any]:
            muc = dict(ket.get(khoa_che) or {})
            muc.update({"ok": False, "loi": thong_bao})
            ket[khoa_che] = muc
            return {"ok": False, "ket_qua_tung_khoa": ket, "loi": thong_bao,
                    "cau_hinh_luu": {}}

        if not url_dang_ky:
            return loi("url_dang_ky", "Chưa khai «URL trang đăng ký» (bat=true).")
        try:
            phan = kiem_url_co_ban(url_dang_ky)
            cong = phan.port or (443 if phan.scheme.lower() == "https" else 80)
            await kiem_dns_va_ip(phan.hostname, cong)
        except LoiTaiUrl as e:
            return loi("url_dang_ky", f"«URL trang đăng ký» bị từ chối: {e}")
        if not hop_thu_api:
            return loi("hop_thu_api", "Chưa khai «Địa chỉ API hộp thư».")
        try:
            api_sach = await kiem_url_lan(hop_thu_api)  # lõi: cho LAN, chặn loopback/metadata
        except ValueError as e:
            return loi("hop_thu_api", f"«Địa chỉ API hộp thư» bị từ chối: {e}")
        luu["hop_thu_api"] = api_sach.rstrip("/")
        if not hop_thu_mien or not MAU_MIEN.match(hop_thu_mien):
            return loi("hop_thu_mien", "«Miền hộp thư» phải là tên miền hợp lệ (ví dụ hop-thu.example).")
        if mien_gui and not MAU_MIEN.match(mien_gui):
            return loi("mien_gui_cho_phep",
                       "«Miền người gửi được phép trích mã» phải là tên miền hợp lệ "
                       "(ví dụ nha-cung-cap.example) hoặc để trống.")
        hop_thu = HopThuInbucket(luu["hop_thu_api"], khoa)
        if not await hop_thu.kiem_tra():
            return loi("hop_thu_api", "Không gọi được API hộp thư (kiểm lại địa chỉ/khoá).")
        return {"ok": True, "ket_qua_tung_khoa": ket, "loi": None, "cau_hinh_luu": luu}

    async def suc_khoe(self) -> dict[str, Any]:
        # Tách «đang tắt» khỏi «chưa cấu hình» (NV189): module tắt theo thiết
        # kế (bat=false mặc định) là trạng thái chủ đích, không phải mâu thuẫn
        # «cai ok nhưng chưa cấu hình» — thông điệp cũ gộp hai trường hợp làm
        # smoke (d) dương tính giả.
        if not self._bat():
            return {"ok": False, "trang_thai": "đang tắt",
                    "loi": "Module đang tắt (bat=false) — đây là trạng thái chủ đích.",
                    "chi_tiet": {"buoc": self._tt.get("buoc", "chua_bat_dau")}}
        if not self._cau_hinh.get("url_dang_ky"):
            return {"ok": False, "loi": "Module chưa cấu hình xong (thiếu «URL trang đăng ký»).",
                    "chi_tiet": {"buoc": self._tt.get("buoc", "chua_bat_dau")}}
        return {"ok": True, "chi_tiet": {
            "buoc": self._tt.get("buoc", "chua_bat_dau"),
            "so_luot_hom_nay": self._tt.get("so_luot_hom_nay", 0),
            "han_moi_ngay": self._han_moi_ngay(),
        }}

    async def dung(self) -> None:
        return None

    # ── Chuẩn thiet_bi ──

    def _tham_so_rong(self) -> dict[str, Any]:
        return {"type": "object", "additionalProperties": False, "properties": {}}

    async def thuc_the(self, loc: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if loc and loc.get("module") and loc.get("module") != self.manifest.get("id"):
            return []
        return [
            {
                "id": self._id_luot,
                "ten": "Lượt đăng ký tài khoản",
                "loai": "cong_cu",
                "rui_ro_mac_dinh": "nguy_hiem",
                "tinh_nang": [
                    {
                        "hanh_dong": "bat_dau",
                        "mo_ta": "Bắt đầu một lượt đăng ký: tạo hộp thư, sinh mật khẩu, "
                                 "tải trang đăng ký để nhận diện bước. Bí mật chỉ hiện "
                                 "MỘT lần trong kết quả lần này.",
                        "tham_so": self._tham_so_rong(),
                    },
                    {
                        "hanh_dong": "doi_ma",
                        "mo_ta": "Hỏi hộp thư lấy mã xác minh vừa gửi tới; trả mã để "
                                 "NGƯỜI tự nhập vào trình duyệt từ xa. Module không tự nhập mã.",
                        "tham_so": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "cho_giay": {
                                    "type": "integer",
                                    "description": "Số giây chờ mã tối đa (1–120, mặc định 60)",
                                },
                            },
                        },
                    },
                    {
                        "hanh_dong": "ghi_nhan_nguoi_xu_ly",
                        "mo_ta": "Quản trị xác nhận NGƯỜI đã tự xử lý bước cần người "
                                 "(thử thách/xác minh) trên trình duyệt từ xa — lượt tiếp tục.",
                        "tham_so": self._tham_so_rong(),
                    },
                    {
                        "hanh_dong": "xac_nhan_hoan_tat",
                        "mo_ta": "Quản trị xác nhận tài khoản đã được tạo xong bởi người trên "
                                 "trình duyệt từ xa — đóng lượt.",
                        "tham_so": self._tham_so_rong(),
                    },
                    {
                        "hanh_dong": "huy",
                        "mo_ta": "Huỷ lượt đang chạy và XOÁ bí mật của lượt khỏi kho bí mật.",
                        "tham_so": self._tham_so_rong(),
                    },
                ],
            },
            {
                "id": self._id_tt,
                "ten": "Trạng thái lượt đăng ký",
                "loai": "cam_bien",
                "rui_ro_mac_dinh": "doc",
                "tinh_nang": [
                    {
                        "hanh_dong": "xem",
                        "mo_ta": "Xem trạng thái lượt (bước hiện tại, lý do cần người, "
                                 "email dạng che). Không bao giờ trả mật khẩu hay khoá.",
                        "tham_so": self._tham_so_rong(),
                    },
                ],
            },
        ]

    async def trang_thai(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        kq: dict[str, dict[str, Any]] = {}
        muc = {
            "gia_tri": self._tt.get("buoc", "chua_bat_dau"),
            "don_vi": None,
            "luc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hop_le": True,
        }
        for id_ in (self._id_luot, self._id_tt):
            if not ids or id_ in ids:
                kq[id_] = dict(muc)
        return kq

    async def thuc_hien(self, id: str, hanh_dong: str,
                        tham_so: dict[str, Any] | None) -> dict[str, Any]:
        tham_so = tham_so or {}
        if id == self._id_tt:
            if hanh_dong == "xem":
                return {"ok": True, "ket_qua": self._xem()}
            return self._tu_choi("hanh_dong_khong_ho_tro",
                                 f"Hành động «{hanh_dong}» không hỗ trợ")
        if id != self._id_luot:
            return self._tu_choi("khong_thay_thuc_the", f"Không tìm thấy thực thể {id!r}")
        if hanh_dong == "bat_dau":
            return await self._bat_dau()
        if hanh_dong == "doi_ma":
            return await self._doi_ma(tham_so)
        if hanh_dong == "ghi_nhan_nguoi_xu_ly":
            return self._ghi_nhan_nguoi()
        if hanh_dong == "xac_nhan_hoan_tat":
            return self._hoan_tat()
        if hanh_dong == "huy":
            return self._huy()
        return self._tu_choi("hanh_dong_khong_ho_tro", f"Hành động «{hanh_dong}» không hỗ trợ")

    @staticmethod
    def _tu_choi(ma_loi: str, loi: str) -> dict[str, Any]:
        return {"ok": False, "ma_loi": ma_loi, "loi": loi}

    def _xem(self) -> dict[str, Any]:
        self._don_luot_het_han()
        cong_khai = self._tt_cong_khai()
        cong_khai.update({
            "so_luot_hom_nay": self._tt.get("so_luot_hom_nay", 0),
            "han_moi_ngay": self._han_moi_ngay(),
        })
        return cong_khai

    # ── Lượt đăng ký ──

    def _ten_bi_mat(self, luot_id: str) -> str:
        return f"register-{luot_id}"

    async def _bat_dau(self) -> dict[str, Any]:
        if not self._bat():
            return self._tu_choi("module_dang_tat",
                                 "Module đang TẮT (cấu hình «Bật luồng đăng ký» = false) — không bắt đầu lượt.")
        url = str(self._cau_hinh.get("url_dang_ky") or "").strip()
        if not url or not self._cau_hinh.get("hop_thu_api"):
            return self._tu_choi("chua_cau_hinh", "Chưa cấu hình xong URL đăng ký / hộp thư.")

        self._don_luot_het_han()
        luot_id = secrets.token_hex(4)
        # GIỮ CHỖ NGUYÊN TỬ (R1): kiểm cổng + đặt trạng thái «đang chạy» +
        # tăng bộ đếm NGAY trong cùng một khối khoá, KHÔNG await ở giữa.
        # Trước đây kiểm ở một khối, tải trang (await) rồi mới ghi ở khối
        # khác → N lượt đồng thời cùng qua cổng. Mọi đường THẤT BẠI sau khi
        # đã giữ chỗ đều qua _nha_cho() để hoàn cả trạng thái lẫn bộ đếm.
        with self._khoa:
            self._kiem_ngay()
            if self._tt.get("buoc") in BUOC_GIU_CHO:
                return self._tu_choi("da_co_luot_dang_chay",
                                     "Đang có một lượt đăng ký chạy — mỗi lần chỉ MỘT tài khoản.")
            if self._tt.get("so_luot_hom_nay", 0) >= self._han_moi_ngay():
                return self._tu_choi("vuot_han_moi_ngay",
                                     f"Đã tới giới hạn {self._han_moi_ngay()} lượt/ngày.")
            self._tt.update({
                "buoc": "dang_khoi_tao", "luot_id": luot_id, "ly_do": None,
                "huong_dan": None, "email_che": None,
                "so_luot_hom_nay": int(self._tt.get("so_luot_hom_nay", 0) or 0) + 1,
                "thu_da_xem": [], "bat_dau_luc": self._bay_gio_ts(),
                "trang_dau_co_ma_ngoai": False, "nguoi_da_ghi_nhan": False,
            })
            self._luu_trang_thai()

        try:
            ma_http, html = await _tai_trang_dang_ky(url)
        except LoiTaiUrl as e:
            self._nha_cho()
            return self._tu_choi("khong_tai_duoc_trang", f"Không tải được trang đăng ký: {e}")
        except Exception as e:  # không lộ chi tiết nội bộ
            log.warning("Tải trang đăng ký lỗi: %s", type(e).__name__)
            self._nha_cho()
            return self._tu_choi("khong_tai_duoc_trang", "Không tải được trang đăng ký (lỗi mạng).")

        phan_loai = phan_loai_trang(html, url)
        loai = phan_loai["loai"]
        co_ma_ngoai = bool(phan_loai.get("chi_tiet", {}).get("co_ma_ngoai"))

        ten_hop_thu = _ten_hop_thu_ngau_nhien()
        email = f"{ten_hop_thu}@{self._cau_hinh.get('hop_thu_mien')}"
        mat_khau = sinh_mat_khau()
        try:
            kho_loi.ghi_bi_mat(self._ten_bi_mat(luot_id), {
                "email": email, "mat_khau": mat_khau, "hop_thu": ten_hop_thu,
                "tao_luc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
        except Exception as e:
            log.warning("Ghi bí mật lượt lỗi: %s", type(e).__name__)
            self._nha_cho()
            return self._tu_choi("khong_luu_duoc_bi_mat",
                                 "Không lưu được thông tin lượt vào kho bí mật — lượt đã huỷ.")

        if loai == "form_dang_ky":
            buoc, ly_do = "cho_nguoi_dien", None
            huong_dan = ("Trang đăng ký đã sẵn sàng. MỞ TRÌNH DUYỆT TỪ XA (noVNC), "
                         "chính bạn điền email và mật khẩu dưới đây vào biểu mẫu — "
                         "module không tự điền.")
        elif loai == "doi_ma":
            buoc, ly_do = "doi_ma_xac_minh", None
            huong_dan = ("Trang đang chờ mã xác minh. Dùng hành động «doi_ma» để lấy "
                         "mã từ hộp thư, rồi chính bạn nhập mã vào trình duyệt từ xa.")
        elif loai == "thu_thach" and phan_loai["chi_tiet"].get("nguon") == "ma_ngoai_khong_ro":
            buoc, ly_do = "can_nguoi", "thu_thach_chua_ro"
            huong_dan = ("Trang đăng ký có tải MÃ/KHUNG TỪ NGUỒN NGOÀI mà module "
                         "không nhận diện được (có thể là thử thách chống bot). "
                         "Module KHÔNG tự giải/vượt và không đi tiếp khi chưa rõ. "
                         "Mở noVNC, chính bạn xem và xử lý trên trình duyệt từ xa, "
                         "xong gọi «ghi_nhan_nguoi_xu_ly» để lượt tiếp tục.")
        elif loai == "thu_thach":
            buoc, ly_do = "can_nguoi", "thu_thach_chong_bot"
            huong_dan = ("Trang có THỬ THÁCH CHỐNG BOT. Module KHÔNG tự giải/vượt. "
                         "Mở noVNC, chính bạn vượt qua thử thách trên trình duyệt từ "
                         "xa, xong gọi «ghi_nhan_nguoi_xu_ly» để lượt tiếp tục.")
        elif loai == "xac_minh_dien_thoai":
            buoc, ly_do = "can_nguoi", "xac_minh_dien_thoai"
            huong_dan = ("Trang yêu cầu XÁC MINH SỐ ĐIỆN THOẠI. Mở noVNC, chính bạn "
                         "nhập số điện thoại của mình, xong gọi «ghi_nhan_nguoi_xu_ly».")
        else:
            buoc, ly_do = "can_nguoi", "khong_nhan_dien_duoc_trang"
            huong_dan = ("Không nhận diện được cấu trúc trang đăng ký — DỪNG an toàn. "
                         "Mở noVNC tự xem trang, xử lý tay, rồi gọi «ghi_nhan_nguoi_xu_ly».")

        with self._khoa:
            if self._tt.get("luot_id") != luot_id:
                # Lượt giữ chỗ đã bị đóng giữa chừng (huỷ/hết hạn bởi đường
                # khác trong lúc tải trang): dọn bí mật vừa ghi, KHÔNG hồi sinh.
                kho_loi.xoa_bi_mat(self._ten_bi_mat(luot_id))
                return self._tu_choi("luot_da_bi_dong",
                                     "Lượt vừa bị đóng giữa chừng — hãy bắt đầu lại.")
            self._tt.update({
                "buoc": buoc, "ly_do": ly_do, "huong_dan": huong_dan,
                "email_che": self._che_email(email),
                "trang_dau_co_ma_ngoai": co_ma_ngoai,
            })
            self._luu_trang_thai()
        log.info("register: bắt đầu lượt %s, bước %s", luot_id, buoc)
        return {
            "ok": True,
            "trang_thai_moi": self._tt_cong_khai(),
            "ket_qua": {
                "luot_id": luot_id,
                # LẦN DUY NHẤT module hiện thông tin bí mật: chép ngay, che ngay.
                "thong_tin_tai_khoan": {"email": email, "mat_khau": mat_khau},
                "huong_dan_bao_mat": ("Đây là lần duy nhất module hiện mật khẩu. "
                                      "Chép ngay rồi che — mọi lần xem sau chỉ hiện dạng che."),
            },
        }

    async def _doi_ma(self, tham_so: dict[str, Any]) -> dict[str, Any]:
        self._don_luot_het_han()
        buoc = self._tt.get("buoc")
        if buoc == "can_nguoi":
            return self._tu_choi("dang_can_nguoi",
                                 "Lượt đang DỪNG ở «can_nguoi» — không đi tiếp cho tới khi "
                                 "người ghi nhận đã xử lý.")
        if buoc not in ("cho_nguoi_dien", "doi_ma_xac_minh"):
            return self._tu_choi("khong_co_luot_dang_chay", "Chưa có lượt đăng ký nào đang chạy.")
        # R5: trang đầu lượt có tải mã/khung từ nguồn ngoài (chưa rõ có phải
        # thử thách hay không) → PHẢI có ghi nhận người xử lý rồi mới được
        # đợi mã; đóng khi thiếu ghi nhận (fail-closed).
        if self._tt.get("trang_dau_co_ma_ngoai") and not self._tt.get("nguoi_da_ghi_nhan"):
            return self._tu_choi("dang_can_nguoi",
                                 "Trang đăng ký đầu lượt có tải mã/khung từ nguồn ngoài "
                                 "chưa rõ — phải có «ghi_nhan_nguoi_xu_ly» của người "
                                 "trước khi đợi mã.")
        luot_id = self._tt.get("luot_id") or ""
        bi_mat = kho_loi.doc_bi_mat(self._ten_bi_mat(luot_id))
        if not bi_mat.get("email"):
            return self._tu_choi("mat_thong_tin_luot", "Không còn thông tin lượt (có thể đã bị huỷ).")
        email = bi_mat["email"]
        ten_hop_thu = bi_mat.get("hop_thu") or ""
        try:
            cho_giay = int(tham_so.get("cho_giay", 60))
        except (TypeError, ValueError):
            cho_giay = 60
        cho_giay = max(1, min(120, cho_giay))
        da_xem = set(self._tt.get("thu_da_xem") or [])
        ma = await self._hop_thu().cho_ma(
            ten_hop_thu, email, float(cho_giay), da_xem,
            str(self._cau_hinh.get("mien_gui_cho_phep") or ""))
        with self._khoa:
            self._tt["thu_da_xem"] = sorted(da_xem)
            self._luu_trang_thai()
        if ma is None:
            return self._tu_choi("chua_thay_ma",
                                 f"Chưa thấy mã xác minh nào trong hộp thư sau {cho_giay}s — "
                                 "hãy gọi lại «doi_ma» sau khi trang đã gửi mã.")
        with self._khoa:
            if self._tt.get("buoc") == "doi_ma_xac_minh":
                self._tt["buoc"] = "cho_nguoi_dien"
                self._tt["huong_dan"] = ("Đã có mã xác minh. Chính bạn nhập mã vào "
                                         "trình duyệt từ xa — module không tự nhập.")
            self._luu_trang_thai()
        log.info("register: đã lấy mã xác minh cho lượt %s (không ghi mã vào log)", luot_id)
        return {
            "ok": True,
            "trang_thai_moi": self._tt_cong_khai(),
            "ket_qua": {
                "ma_xac_minh": ma,
                "huong_dan": ("Nhập mã này vào TRÌNH DUYỆT TỪ XA của bạn. "
                              "Module không tự nhập mã và không gửi mã đi đâu."),
            },
        }

    def _ghi_nhan_nguoi(self) -> dict[str, Any]:
        self._don_luot_het_han()
        buoc = self._tt.get("buoc")
        # Nhánh riêng (R5): lượt đang ở bước đợi mã nhưng trang đầu có mã ngoài
        # chưa rõ → ghi nhận chỉ mở cổng đợi mã, KHÔNG đổi bước.
        if (buoc == "doi_ma_xac_minh" and self._tt.get("trang_dau_co_ma_ngoai")
                and not self._tt.get("nguoi_da_ghi_nhan")):
            with self._khoa:
                self._tt.update({
                    "nguoi_da_ghi_nhan": True,
                    "huong_dan": ("Người đã ghi nhận phần mã ngoài trên trang. "
                                   "Tiếp tục: gọi «doi_ma» để lấy mã từ hộp thư, "
                                   "rồi chính bạn nhập mã vào trình duyệt từ xa."),
                })
                self._luu_trang_thai()
            log.info("register: người đã ghi nhận mã ngoài, lượt %s mở cổng đợi mã",
                     self._tt.get("luot_id"))
            return {"ok": True, "trang_thai_moi": self._tt_cong_khai()}
        if buoc != "can_nguoi":
            return self._tu_choi("khong_o_trang_thai_can_nguoi",
                                 "Lượt không ở trạng thái «can_nguoi» — không có gì để ghi nhận.")
        with self._khoa:
            self._tt.update({
                "buoc": "cho_nguoi_dien", "ly_do": None,
                "nguoi_da_ghi_nhan": True,
                "huong_dan": ("Người đã xử lý xong bước cần người. Tiếp tục trên "
                               "trình duyệt từ xa; cần mã thì gọi «doi_ma»."),
            })
            self._luu_trang_thai()
        log.info("register: người đã xử lý bước cần người, lượt %s tiếp tục",
                 self._tt.get("luot_id"))
        return {"ok": True, "trang_thai_moi": self._tt_cong_khai()}

    def _hoan_tat(self) -> dict[str, Any]:
        self._don_luot_het_han()
        if self._tt.get("buoc") not in BUOC_DANG_CHAY:
            return self._tu_choi("khong_co_luot_dang_chay", "Chưa có lượt đăng ký nào đang chạy.")
        with self._khoa:
            luot_id = self._tt.get("luot_id")
            # R2 + quyết điều phối: hiện MỘT lần (ở kết quả bắt đầu) rồi XOÁ
            # NGAY khi hoàn tất — bí mật không nằm lại trong kho.
            self._ket_thuc_luot(
                "hoan_tat",
                "Tài khoản đã được tạo xong bởi người. Bí mật của lượt đã XOÁ "
                "khỏi kho ngay khi hoàn tất (chỉ hiện đúng một lần ở kết quả "
                "bắt đầu lượt) — không còn cách đọc lại, hãy dùng mật khẩu đã chép.")
        log.info("register: lượt %s hoàn tất, đã xoá bí mật", luot_id)
        return {"ok": True, "trang_thai_moi": self._tt_cong_khai()}

    def _huy(self) -> dict[str, Any]:
        with self._khoa:
            luot_id = self._tt.get("luot_id")
            self._ket_thuc_luot(
                "da_huy", "Lượt đã huỷ; bí mật của lượt đã xoá khỏi kho bí mật.")
            self._tt["email_che"] = None
            self._luu_trang_thai()
        log.info("register: đã huỷ lượt %s và xoá bí mật", luot_id)
        return {"ok": True, "trang_thai_moi": self._tt_cong_khai()}
