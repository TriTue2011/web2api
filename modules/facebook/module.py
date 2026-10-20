"""Module facebook — đăng bài lên Trang (Page) qua Graph API CHÍNH THỨC.

Nguồn port (chỉ đổi lớp kết nối/khuôn module/xác thực): đường
`services/facebook_page.py` @ commit d879b03 của c2a (kho của chính chủ máy).
Không port: phần NHÓM (`services/facebook_group.py` — Meta gỡ Groups API từ
04/2024, c2a đăng nhóm qua trình duyệt + captcha-solver là đường KHÔNG chính
thức), và máy trạng thái menu/bắt input chat (`menu_ask`, `bat_dau_flow`,
`tiep_flow`, `don_bai`, `tach_bai`… — gắn vào vòng agent của c2a, lõi web2api
không có) — thay bằng chuẩn thiết bị C1 của lõi.

Thiết kế theo chuẩn thiết bị C1 (`thiet_bi`), KHÔNG dùng `kenh_ra`:
- Mỗi Trang là một thực thể `facebook.trang_<page_id>` với
  `rui_ro_mac_dinh: "nguy_hiem"` — đăng ra CÔNG CHÚNG là hành động rủi ro
  cao: lõi bắt quản trị xác nhận (SEC-004f) trước khi bất cứ bài nào được
  đăng. `kenh_ra` không có cổng xác nhận cho hành động ghi như vậy.
- `thuc_hien` có lược đồ tham số: lõi tự thẩm định theo SEC-004a; tham số
  bí mật (nếu có) được che trong nhật ký kiểm toán theo SEC-004b.
- Thực thể `facebook.ket_noi` rủi ro «doc» cho liệt kê Trang / kiểm tra kết nối.

Bảo mật:
- Địa chỉ gọi CỐ ĐỊNH `https://graph.facebook.com/v23.0` trong mã — không đọc
  từ cấu hình người dùng (chống SSRF); ghi đè gốc chỉ tồn tại dưới dạng
  thuộc tính nội bộ `_goc_test` và chỉ có hiệu lực khi
  `WEB2API_TEST_CHO_PHEP_LOOPBACK=1` và giá trị là loopback.
- Page access token, user token, app_secret: không bao giờ vào log/phản hồi/
  nhật ký — bộ lọc `_ChanRoToken` bỏ dòng log thư viện HTTP chứa token (token
  đi trong thân POST và query GET; httpx ghi URL ở INFO/DEBUG); KHÔNG bao giờ
  `str()` ngoại lệ httpx (chúng có thể in URL chứa token) — chỉ tên kiểu.
- Ảnh tải từ URL ngoài: chỉ qua `app.tai_url_khong_tin.tai_url` (chặn IP riêng/
  loopback/link-local/metadata đám mây, ghim IP chống DNS rebinding, chuyển
  hướng thủ công ≤ 3 chặng, trần 4 MB) và `app.tep_an_toan.kiem_anh` (đúng PNG/
  JPEG/GIF/WebP, không vỡ ảnh) — không chuyển URL thẳng cho Facebook, không
  tự viết bộ kiểm URL thứ hai.
- Video: Graph nhận `file_url` để Facebook tự kéo — URL phải qua kiểm tra
  `kiem_url_co_ban` + `kiem_dns_va_ip` (host CÔNG KHAI, không LAN/loopback).
- Lỗi Graph dịch sang tiếng Việt bằng bảng trong c2a; thử lại tối đa 2 lần lỗi
  thoáng qua (429, is_transient, mã 4/17/32/613, 5xx): lỗi token hỏng hay nội
  dung vi phạm thì thử lại vô ích → ném ngay. Kết quả chỉ mang thông điệp đã
  dịch + mã ngắn, không bao giờ lộ thân lỗi thô chứa token.

Mã này chạy TRONG tiến trình lõi.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac as _hmac
import json
import logging
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.tai_url_khong_tin import (
    LoiTaiUrl,
    kiem_dns_va_ip,
    kiem_url_co_ban,
    tai_url,
)
from app.tep_an_toan import LoiTep, kiem_anh
from app.tu_xa import cho_phep_loopback_cho_test

#: Nguồn: đường services/facebook_page.py @ commit d879b03 của c2a.
NGUON_C2A = "services/facebook_page.py@d879b03"

#: Địa chỉ gọi CỐ ĐỊNH — không đọc từ cấu hình người dùng (chống SSRF).
GOC_MAC_DINH = "https://graph.facebook.com/v23.0"
#: Trần ảnh Graph (theo subcode 1366046 của c2a: ảnh phải nhỏ hơn 4 MB).
TRAN_BYTE_ANH = 4 * 1024 * 1024
#: Hết giờ tải ảnh.
HET_GIO_TAI = 30.0
#: Số ảnh tối đa một bài.
ANH_TOI_DA = 10
#: Khoảng cách tối thiểu giữa hai bài đăng trên cùng một Trang (trần tự chọn —
#  chống đăng quá nhanh ăn lỗi 1390008 của Graph; lõi còn có trần 30 lệnh/phút
#  theo (khoá, thực thể) riêng).
KHOANG_CACH_BAI_GIAY = 30.0
_HET_GIO = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
#: Thời gian nghỉ cơ sở giữa các lần thử lại (test có thể đặt về nhỏ).
_THOI_GIAN_NGHI = 3.0

log = logging.getLogger("web2api.module.facebook")

# ── Dịch lỗi Graph API ra tiếng Việt ─────────────────────────────────────────
# Nguồn: services/facebook_page.py @ d879b03 (bảng rút từ facebook.provider.ts
# của Postiz, đối chiếu vận hành thật của họ). Meta khuyến nghị kèm
# appsecret_proof (HMAC-SHA256(app_secret, token)) vào mọi lời gọi server.
_LOI_THEO_SUBCODE: dict[int, str] = {
    1366046: "Ảnh phải nhỏ hơn 4 MB và là JPG/PNG",
    1390008: "Đăng quá nhanh — chờ một lát rồi thử lại",
    1346003: "Nội dung bị Facebook gắn cờ lạm dụng",
    1404102: "Nội dung vi phạm Tiêu chuẩn cộng đồng của Facebook",
    1404078: "Page chưa cấp quyền đăng — cần nối lại tài khoản Facebook",
    1366051: "Những ảnh này đã được đăng trước đó",
    1609008: "Facebook không cho đăng link facebook.com",
    2061006: "URL trong bài không đúng định dạng",
    4854002: "Cần mở app Facebook trên điện thoại xác minh danh tính "
             "trước khi đăng dưới danh nghĩa Page này",
    2069019: "Tệp không hợp lệ với Facebook",
}
# Theo code (khi không có subcode khớp). 190 = token hỏng/thu hồi/hết hạn.
_LOI_THEO_CODE: dict[int, str] = {
    190: "Token Facebook hết hạn hoặc bị thu hồi — vào Cài đặt ▸ Facebook nối lại",
    200: "Thiếu quyền đăng lên Page này (cần pages_manage_posts)",
    100: "Tham số gửi lên Facebook không hợp lệ",
}
# Mã thuộc nhóm rate-limit — đáng thử lại sau khi chờ.
_CODE_THU_LAI = frozenset({4, 17, 32, 613})


class LoiFacebook(Exception):
    """Một lời gọi Graph API thất bại, đã dịch sang thông điệp người đọc được.

    `can_noi_lai=True` nghĩa là token hỏng — sửa bằng cách nối lại tài khoản,
    thử lại vô ích. `ma` là mã ngắn duy nhất đi ra kết quả/log (thân lỗi thô
    của Graph KHÔNG bao giờ lộ — có thể chứa token).
    """

    def __init__(self, thong_diep: str, *, ma: str = "", can_noi_lai: bool = False):
        super().__init__(thong_diep)
        self.ma = ma
        self.can_noi_lai = can_noi_lai


def _dich_loi(err: dict[str, Any]) -> LoiFacebook:
    ma = int(err.get("code") or 0)
    ma_phu = int(err.get("error_subcode") or 0)
    user_msg = str(err.get("error_user_msg") or "")
    thong_diep = (_LOI_THEO_SUBCODE.get(ma_phu)
                  or _LOI_THEO_CODE.get(ma)
                  or user_msg
                  or f"Facebook báo lỗi (mã {ma or 'không rõ'})")
    return LoiFacebook(thong_diep, ma=f"fb_{ma}" if ma else "fb_loi",
                       can_noi_lai=(ma == 190 or ma_phu == 1404078))


# ── Lọc token trong log thư viện HTTP ─────────────────────────────────────────

#: Token đang dùng (chỉ thêm) — bộ lọc log bỏ mọi dòng của thư viện HTTP có chứa
#  token (token đi trong query GET / thân POST; httpx ghi URL ở mức INFO/DEBUG).
_TOKEN_DANG_DUNG: set[str] = set()
_LOGGER_HTTP = ("httpx", "httpcore", "httpcore.connection",
                "httpcore.http11", "httpcore.http2", "httpcore.proxy")


class _ChanRoToken(logging.Filter):
    """Bỏ dòng log chứa token. Cài MỘT lần lên logger của thư viện HTTP — KHÔNG
    đổi mức log toàn cục (nâng mức quanh mỗi lời gọi rồi khôi phục sẽ kẹt
    logger ở mức cao khi hai lượt gửi chạy xen kẽ)."""
    _facebook_loc = True

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            noi_dung = record.getMessage()
        except Exception:
            return False  # không định dạng được → bỏ dòng (đóng khi lỗi)
        return not any(t in noi_dung for t in _TOKEN_DANG_DUNG)


def _cai_bo_loc_log() -> None:
    for ten in _LOGGER_HTTP:
        lg = logging.getLogger(ten)
        for f in [f for f in lg.filters if getattr(f, "_facebook_loc", False)]:
            lg.removeFilter(f)   # nạp lại module: bỏ bộ lọc của bản cũ
        lg.addFilter(_ChanRoToken())


_cai_bo_loc_log()


def _sach_ten(ten: str, dai_toi_da: int = 100) -> str:
    """Tên Trang từ Facebook là dữ liệu không tin — bỏ ký tự điều khiển, cắt."""
    s = "".join(ch for ch in str(ten or "") if ch.isprintable())
    s = " ".join(s.split())
    return s[:dai_toi_da] if s else ""


def _che_token(gia_tri: str) -> str:
    v = str(gia_tri or "")
    return f"…{v[-4:]}" if len(v) > 4 else "…"


class Module:
    """Lớp Module theo hợp đồng app/kham_pha.py (khả năng thiet_bi)."""

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to", "facebook")
        self._app_id = ""
        self._app_secret = ""
        self._token_dai = ""
        self._trang: list[dict] = []
        # Tham số NỘI BỘ chỉ cho test: gốc máy chủ giả. Không đọc từ cấu hình
        # người dùng; chỉ có hiệu lực khi WEB2API_TEST_CHO_PHEP_LOOPBACK=1.
        self._goc_test: str | None = None
        self._lan_dang_cuoi: dict[str, float] = {}   # page_id -> monotonic
        # Trần tự chọn (test có thể hạ để chạy nhanh).
        self._khoang_cach_bai = KHOANG_CACH_BAI_GIAY

    def _goc(self) -> str:
        goc_test = self._goc_test
        if goc_test and cho_phep_loopback_cho_test():
            phan = urlsplit(str(goc_test))
            if (phan.scheme == "http" and phan.hostname in ("127.0.0.1", "localhost")
                    and not phan.username and not phan.password):
                return str(goc_test).rstrip("/")
            log.warning("facebook: bỏ qua gốc test không hợp lệ (chỉ nhận http loopback)")
        return GOC_MAC_DINH

    def _ghi_nhan_token(self, *tokens: str) -> None:
        for t in tokens:
            if t and len(str(t)) > 4:
                _TOKEN_DANG_DUNG.add(str(t))

    def _appsecret_proof(self, token: str) -> str:
        secret = str(self._app_secret or "")
        if not secret:
            return ""
        return _hmac.new(secret.encode("utf-8"), token.encode("utf-8"),
                         hashlib.sha256).hexdigest()

    # ── nạp cấu hình ──────────────────────────────────────────────────────

    def _nap_trang(self, gia_tri: Any) -> list[dict]:
        if gia_tri is None or gia_tri == "":
            return []
        if isinstance(gia_tri, str):
            try:
                gia_tri = json.loads(gia_tri)
            except (json.JSONDecodeError, ValueError):
                log.warning("facebook: cấu hình Trang không phải JSON hợp lệ")
                return []
        if not isinstance(gia_tri, list):
            log.warning("facebook: cấu hình Trang phải là danh sách")
            return []
        ra = []
        for p in gia_tri:
            if not isinstance(p, dict):
                continue
            pid = str(p.get("id") or "").strip()
            token = str(p.get("access_token") or "")
            if not pid or not token:
                continue
            ra.append({"id": pid, "name": _sach_ten(p.get("name") or pid),
                       "access_token": token})
        return ra

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM (lúc khởi động/cài xong) — không gọi mạng."""
        cau_hinh = cau_hinh or {}
        self._app_id = str(cau_hinh.get("app_id") or "").strip()
        self._app_secret = str(cau_hinh.get("app_secret") or "").strip()
        self._token_dai = str(cau_hinh.get("user_token_long") or "").strip()
        self._trang = self._nap_trang(cau_hinh.get("pages"))
        self._ghi_nhan_token(self._app_secret, self._token_dai,
                             *[p["access_token"] for p in self._trang])

    # ── gọi Graph API (port từ goi_graph của c2a, bất đồng bộ hoá) ────────

    async def _goi_graph(self, phuong_thuc: str, duong: str,
                         tham_so: dict | None = None, *,
                         token: str, timeout: float = 30.0,
                         form: bool = False,
                         tep: dict | None = None) -> dict:
        """Một lời gọi Graph API, tự thử lại lỗi thoáng qua, tự dịch lỗi.

        Thử lại (tối đa 2 lần, nghỉ 3s/6s): HTTP 429, `is_transient` trong
        thân, mã rate-limit (4/17/32/613), hoặc 5xx không đọc được lỗi. Các lỗi
        còn lại ném LoiFacebook ngay — token hỏng hay nội dung vi phạm thì
        thử lại vô ích.
        """
        url = f"{self._goc()}/{duong.lstrip('/')}"
        proof = self._appsecret_proof(token)
        loi_cuoi: LoiFacebook | None = None
        for lan in range(3):
            try:
                async with httpx.AsyncClient(timeout=_HET_GIO) as client:
                    if phuong_thuc.upper() == "GET":
                        q = dict(tham_so or {})
                        q["access_token"] = token      # GET: Meta đọc từ query
                        if proof:
                            q["appsecret_proof"] = proof
                        dap = await client.get(url, params=q)
                    else:
                        # POST: token đi trong THÂN (không trong URL) — log httpx
                        # ghi URL đầy đủ, tránh token lọt vào dòng log.
                        than: dict[str, Any] = dict(tham_so or {})
                        than["access_token"] = token
                        if proof:
                            than["appsecret_proof"] = proof
                        if tep:
                            dap = await client.post(url, data=than, files=tep)
                        else:
                            dap = await client.post(url, json=than,
                                                    headers={"Content-Type": "application/json"})
            except httpx.HTTPError as exc:
                # KHÔNG str(exc): ngoại lệ httpx có thể in URL chứa token.
                loi_cuoi = LoiFacebook(f"Không gọi được Facebook: {type(exc).__name__}",
                                       ma="mang_loi")
                await asyncio.sleep(_THOI_GIAN_NGHI * (lan + 1))
                continue

            try:
                than_tra = dap.json()
            except ValueError:
                than_tra = {}
            if dap.status_code < 400 and "error" not in (than_tra if isinstance(than_tra, dict) else {}):
                return than_tra if isinstance(than_tra, dict) else {}

            err = than_tra.get("error") if isinstance(than_tra, dict) else None
            if not isinstance(err, dict):
                err = {"message": "", "code": 0}
            loi = _dich_loi(err)
            thoang_qua = (dap.status_code == 429
                          or bool(err.get("is_transient"))
                          or int(err.get("code") or 0) in _CODE_THU_LAI
                          or (dap.status_code >= 500 and int(err.get("code") or 0) == 0))
            if not thoang_qua:
                raise loi
            loi_cuoi = loi
            await asyncio.sleep(_THOI_GIAN_NGHI * (lan + 1))

        assert loi_cuoi is not None
        raise loi_cuoi

    # ── kết nối: đổi token dài hạn + nạp Trang ────────────────────────────

    def _token_de_doi(self, cau_hinh: dict) -> str:
        return str(cau_hinh.get("user_token") or "").strip() or self._token_dai

    async def _doi_token_va_nap_trang(self, user_token: str) -> tuple[str, list[dict]]:
        doi = await self._goi_graph("GET", "oauth/access_token", {
            "grant_type": "fb_exchange_token",
            "client_id": self._app_id,
            "client_secret": self._app_secret,
            "fb_exchange_token": user_token,
        }, token=user_token)
        token_dai = str(doi.get("access_token") or "")
        if not token_dai:
            raise LoiFacebook("Facebook không trả token dài hạn", ma="doi_token_loi")

        trang: list[dict] = []
        tham_so: dict[str, Any] = {
            "fields": "id,name,access_token,picture.type(large)", "limit": 100}
        # /me/accounts phân trang bằng paging.cursors.after — Page nhà thường
        # < 100 nhưng vòng while rẻ và tránh sót trang hai (nguồn: c2a).
        while True:
            phan = await self._goi_graph("GET", "me/accounts", tham_so, token=token_dai)
            for p in phan.get("data") or []:
                if not isinstance(p, dict) or not p.get("id"):
                    continue
                trang.append({
                    "id": str(p["id"]),
                    "name": _sach_ten(p.get("name") or ""),
                    "access_token": str(p.get("access_token") or ""),
                })
            sau = (((phan.get("paging") or {}).get("cursors") or {}).get("after") or "")
            if not sau or not (phan.get("paging") or {}).get("next"):
                break
            tham_so["after"] = sau
        return token_dai, trang

    async def cai(self, cau_hinh: dict) -> dict:
        """Đổi user token → dài hạn, nạp danh sách Trang, LƯU vào cấu hình.

        Token ngắn hạn đã dùng thì XOÁ khỏi cấu hình lưu (không còn sống);
        Trang kèm Page access token lưu dạng bí mật (không API nào trả lại).
        """
        cau_hinh = cau_hinh or {}
        app_id = str(cau_hinh.get("app_id") or "").strip()
        app_secret = str(cau_hinh.get("app_secret") or "").strip()
        user_token = str(cau_hinh.get("user_token") or "").strip() \
            or str(cau_hinh.get("user_token_long") or "").strip()
        if not (app_id and app_secret):
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": "Chưa đủ app_id / app_secret (xem hướng dẫn lấy ở mô tả trường).",
                    "cau_hinh_luu": {}}
        if not user_token:
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": "Chưa có user token — dán ở trường «User token» rồi bấm Cài "
                           "(mục «làm tươi» dùng lại token dài hạn đã lưu).",
                    "cau_hinh_luu": {}}
        self._app_id, self._app_secret = app_id, app_secret
        self._ghi_nhan_token(app_secret, user_token)
        try:
            token_dai, trang = await self._doi_token_va_nap_trang(user_token)
        except LoiFacebook as e:
            return {"ok": False,
                    "ket_qua_tung_khoa": [{"khoa": _che_token(user_token), "ok": False}],
                    "loi": str(e), "cau_hinh_luu": {}}
        except Exception as e:
            # KHÔNG str(e): chỉ tên kiểu (ngoại lệ mạng có thể in URL chứa token).
            return {"ok": False,
                    "ket_qua_tung_khoa": [{"khoa": _che_token(user_token), "ok": False}],
                    "loi": f"Không kết nối được Facebook ({type(e).__name__}).",
                    "cau_hinh_luu": {}}
        if not trang:
            return {"ok": False,
                    "ket_qua_tung_khoa": [{"khoa": _che_token(user_token), "ok": True}],
                    "loi": "Đã đổi được token dài hạn nhưng tài khoản chưa quản trị Trang nào.",
                    "cau_hinh_luu": {}}
        luu = {"app_id": app_id, "app_secret": app_secret, "user_token": "",
               "user_token_long": token_dai, "pages": json.dumps(trang, ensure_ascii=False)}
        self.nap(luu)
        log.info("facebook: kết nối xong, %d trang", len(trang))
        return {"ok": True,
                "ket_qua_tung_khoa": [{"khoa": _che_token(user_token), "ok": True}],
                "loi": None,
                "cau_hinh_luu": luu}

    async def suc_khoe(self) -> dict:
        """Sức khoẻ theo khuôn chung — KHÔNG gọi mạng."""
        if not self._app_id or not self._app_secret:
            return {"ok": False, "loi": "chua_cau_hinh"}
        if not self._trang:
            return {"ok": False, "loi": "chua_ket_noi_trang"}
        return {"ok": True, "so_trang": len(self._trang)}

    async def dung(self) -> None:
        pass

    # ── thực thể / trạng thái ─────────────────────────────────────────────

    def _tinh_nang_dang(self) -> list[dict]:
        return [
            {"hanh_dong": "dang_bai",
             "mo_ta": "Đăng bài chữ (kèm link nếu có) lên Trang. Đăng ra công chúng — lõi bắt quản trị xác nhận trước.",
             "tham_so": {
                 "type": "object", "additionalProperties": False,
                 "required": ["noi_dung"],
                 "properties": {
                     "noi_dung": {"type": "string", "minLength": 1, "maxLength": 5000,
                                   "description": "Nội dung bài đăng (văn bản thuần, ≤ 5000 ký tự)"},
                     "link": {"type": "string", "maxLength": 2048,
                              "description": "Link kèm bài (tuỳ chọn)"},
                 },
             }},
            {"hanh_dong": "dang_anh",
             "mo_ta": "Đăng bài kèm 1–10 ảnh lên Trang. Ảnh được module tải an toàn qua URL rồi upload lên Facebook (không chuyển URL thẳng cho Facebook).",
             "tham_so": {
                 "type": "object", "additionalProperties": False,
                 "required": ["media_urls"],
                 "properties": {
                     "media_urls": {"type": "array", "minItems": 1, "maxItems": ANH_TOI_DA,
                                    "items": {"type": "string", "maxLength": 2048},
                                    "description": "URL ảnh (công khai, module tự tải và kiểm)"},
                     "caption": {"type": "string", "maxLength": 5000,
                                  "description": "Chú thích kèm bài"},
                 },
             }},
            {"hanh_dong": "dang_video",
             "mo_ta": "Đăng video lên Trang: Facebook tự kéo file từ URL và xử lý (hiển thị dạng reel).",
             "tham_so": {
                 "type": "object", "additionalProperties": False,
                 "required": ["url"],
                 "properties": {
                     "url": {"type": "string", "maxLength": 2048,
                             "description": "URL video công khai (Facebook tự kéo — host phải công khai, không LAN/loopback)"},
                     "mo_ta": {"type": "string", "maxLength": 5000,
                                "description": "Mô tả kèm video"},
                 },
             }},
        ]

    async def thuc_the(self, loc=None) -> list[dict]:
        ds = [{
            "id": f"{self.tien_to}.ket_noi",
            "ten": "Kết nối Facebook",
            "loai": "cong_cu",
            "thuoc_tinh": {},
            "tinh_nang": [
                {"hanh_dong": "liet_ke_trang",
                 "mo_ta": "Liệt kê các Trang đã kết nối (chỉ id và tên — không bao giờ lộ token).",
                 "tham_so": {"type": "object", "additionalProperties": False,
                             "properties": {}}},
                {"hanh_dong": "kiem_tra",
                 "mo_ta": "Gọi Graph API kiểm tra token từng Trang còn sống không.",
                 "tham_so": {"type": "object", "additionalProperties": False,
                             "properties": {}}},
            ],
            "rui_ro_mac_dinh": "doc",
        }]
        for p in self._trang:
            ds.append({
                "id": f"{self.tien_to}.trang_{p['id']}",
                "ten": f"Trang Facebook — {p['name']}",
                "loai": "cong_cu",
                "thuoc_tinh": {"page_id": p["id"]},
                "tinh_nang": self._tinh_nang_dang(),
                "rui_ro_mac_dinh": "nguy_hiem",
            })
        return ds

    async def trang_thai(self, ids: list[str]) -> dict:
        ra = {}
        for eid in ids or []:
            if eid == f"{self.tien_to}.ket_noi":
                ra[eid] = {"gia_tri": "da_ket_noi" if self._trang else "chua_ket_noi",
                           "hop_le": True}
            else:
                tim = next((p for p in self._trang
                            if eid == f"{self.tien_to}.trang_{p['id']}"), None)
                ra[eid] = {"gia_tri": "da_ket_noi" if tim else "khong_co",
                           "hop_le": bool(tim)}
        return ra

    # ── thực hiện hành động ───────────────────────────────────────────────

    def _trang_theo_id(self, id_thuc_the: str) -> dict | None:
        for p in self._trang:
            if id_thuc_the == f"{self.tien_to}.trang_{p['id']}":
                return p
        return None

    def _kiem_tan_suat(self, page_id: str) -> str | None:
        """Giới hạn tần suất theo Trang (chống đăng quá nhanh ăn lỗi 1390008
        của Graph). Trần tự chọn 30s — lõi còn có trần 30 lệnh/phút riêng."""
        luc = self._lan_dang_cuoi.get(page_id)
        if luc is None:
            return None
        con = self._khoang_cach_bai - (time.monotonic() - luc)
        if con > 0:
            return f"Đăng quá nhanh — chờ {con:.0f} giây nữa rồi đăng lại"
        return None

    def _ghi_dang(self, page_id: str) -> None:
        self._lan_dang_cuoi[page_id] = time.monotonic()

    @staticmethod
    def _url_bai(than: dict) -> str:
        lien_ket = str(than.get("permalink_url") or "")
        if lien_ket.startswith("/"):
            return f"https://www.facebook.com{lien_ket}"
        if lien_ket:
            return lien_ket
        bai_id = str(than.get("post_id") or than.get("id") or "")
        return f"https://www.facebook.com/{bai_id}" if bai_id else ""

    async def _dang_bai_chu(self, trang: dict, tham_so: dict) -> dict:
        noi_dung = str((tham_so or {}).get("noi_dung") or "").strip()
        link = str((tham_so or {}).get("link") or "").strip()
        if not noi_dung:
            return {"ok": False, "loi": "Thiếu nội dung bài đăng (noi_dung)."}
        than: dict[str, Any] = {"message": noi_dung, "published": True,
                                "fields": "id,permalink_url"}
        if link:
            than["link"] = link
        try:
            ket = await self._goi_graph("POST", f"{trang['id']}/feed", than,
                                        token=trang["access_token"])
        except LoiFacebook as e:
            return {"ok": False, "loi": str(e)}
        return {"ok": True, "trang_thai_moi": {
            "id_bai": str(ket.get("id") or ""), "url": self._url_bai(ket)}}

    async def _tai_anh(self, url: str) -> tuple[bytes, str]:
        """Tải ảnh an toàn: qua app.tai_url_khong_tin (chặn SSRF/DNS rebinding,
        trần 4 MB) rồi app.tep_an_toan.kiem_anh (đúng PNG/JPEG/GIF/WebP)."""
        try:
            du_lieu, _kieu = await tai_url(url, tran_byte=TRAN_BYTE_ANH,
                                           tran_giay=HET_GIO_TAI)
        except LoiTaiUrl as e:
            raise LoiFacebook(f"Không tải được ảnh ({e.ma}) — kiểm tra URL có "
                              "công khai và nhỏ hơn 4 MB không.", ma="tai_anh_loi") from None
        try:
            thong_tin = kiem_anh(du_lieu)
        except LoiTep as e:
            raise LoiFacebook(f"Ảnh không an toàn/hợp lệ ({e.ma}) — Facebook "
                              "chỉ nhận JPG/PNG.", ma="anh_loi") from None
        return du_lieu, thong_tin["mime"]

    async def _dang_anh(self, trang: dict, tham_so: dict) -> dict:
        urls = (tham_so or {}).get("media_urls")
        if not isinstance(urls, list) or not 1 <= len(urls) <= ANH_TOI_DA:
            return {"ok": False, "loi": "media_urls phải là danh sách 1–10 URL ảnh."}
        caption = str((tham_so or {}).get("caption") or "")
        fbids = []
        for u in urls:
            if not isinstance(u, str) or not u.strip():
                return {"ok": False, "loi": "media_urls chứa URL không hợp lệ."}
            try:
                du_lieu, mime = await self._tai_anh(u.strip())
            except LoiFacebook as e:
                return {"ok": False, "loi": str(e)}
            try:
                anh = await self._goi_graph("POST", f"{trang['id']}/photos",
                                            {"published": "false"},
                                            token=trang["access_token"],
                                            tep={"source": ("anh",
                                                            du_lieu, mime)})
            except LoiFacebook as e:
                return {"ok": False, "loi": str(e)}
            fbids.append({"media_fbid": str(anh.get("id") or "")})
        try:
            ket = await self._goi_graph("POST", f"{trang['id']}/feed", {
                "message": caption, "attached_media": fbids, "published": True,
                "fields": "id,permalink_url",
            }, token=trang["access_token"])
        except LoiFacebook as e:
            return {"ok": False, "loi": str(e)}
        return {"ok": True, "trang_thai_moi": {
            "id_bai": str(ket.get("id") or ""), "url": self._url_bai(ket)}}

    async def _kiem_url_video(self, url: str) -> None:
        """Video cho Graph bằng file_url — Facebook tự kéo, nên URL phải qua
        kiểm tra host CÔNG KHAI (chặn LAN/loopback/link-local/metadata) để
        module không thành công cụ dò mạng nhà qua tài khoản Facebook."""
        try:
            phan = kiem_url_co_ban(url)
        except ValueError as e:
            raise LoiFacebook(f"URL video không hợp lệ: {e}") from None
        try:
            ds = await kiem_dns_va_ip(phan.hostname or "", phan.port or 0)
        except Exception as e:
            raise LoiFacebook(f"Không kiểm được host của URL video ({type(e).__name__}).") from None
        if not ds:
            raise LoiFacebook("Không phân giải được host của URL video.")

    async def _dang_video(self, trang: dict, tham_so: dict) -> dict:
        url_video = str((tham_so or {}).get("url") or "").strip()
        mo_ta = str((tham_so or {}).get("mo_ta") or "")
        if not url_video:
            return {"ok": False, "loi": "Thiếu URL video (url)."}
        try:
            await self._kiem_url_video(url_video)
        except LoiFacebook as e:
            return {"ok": False, "loi": str(e)}
        try:
            ket = await self._goi_graph("POST", f"{trang['id']}/videos", {
                "file_url": url_video, "description": mo_ta, "published": True,
            }, token=trang["access_token"], timeout=90.0)
        except LoiFacebook as e:
            return {"ok": False, "loi": str(e)}
        vid = str(ket.get("id") or "")
        return {"ok": True, "trang_thai_moi": {
            "id_bai": vid,
            "url": f"https://www.facebook.com/reel/{vid}" if vid else ""}}

    async def _liet_ke_trang(self) -> dict:
        # KHÔNG bao giờ trả token — chỉ id + tên.
        return {"ok": True, "trang_thai_moi": {
            "trang": [{"id": p["id"], "name": p["name"]} for p in self._trang]}}

    async def _kiem_tra(self) -> dict:
        song = 0
        for p in self._trang:
            try:
                await self._goi_graph("GET", str(p["id"]),
                                      {"fields": "id,name"}, token=p["access_token"])
                song += 1
            except LoiFacebook:
                continue
        return {"ok": song > 0, "trang_thai_moi": {
            "so_trang_song": song, "tong_trang": len(self._trang)}}

    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict | None) -> dict:
        tham_so = tham_so or {}
        if id == f"{self.tien_to}.ket_noi":
            if hanh_dong == "liet_ke_trang":
                return await self._liet_ke_trang()
            if hanh_dong == "kiem_tra":
                return await self._kiem_tra()
            return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}
        trang = self._trang_theo_id(id)
        if trang is None:
            return {"ok": False, "loi": "Trang không có trong cấu hình (kiểm tra lại danh sách Trang đã kết nối)."}
        # Giới hạn tần suất theo Trang: chỉ kiểm cho hành động đăng ra công chúng.
        if hanh_dong in ("dang_bai", "dang_anh", "dang_video"):
            tu_choi = self._kiem_tan_suat(trang["id"])
            if tu_choi:
                return {"ok": False, "loi": tu_choi}
        try:
            if hanh_dong == "dang_bai":
                kq = await self._dang_bai_chu(trang, tham_so)
            elif hanh_dong == "dang_anh":
                kq = await self._dang_anh(trang, tham_so)
            elif hanh_dong == "dang_video":
                kq = await self._dang_video(trang, tham_so)
            else:
                return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}
        except Exception as e:
            # Lưới an toàn cuối: không bao giờ lộ thân ngoại lệ (có thể chứa token trong URL).
            log.warning("facebook: lỗi không xác định (%s)", type(e).__name__)
            return {"ok": False, "loi": "loi_noi_bo"}
        if kq.get("ok") is True:
            self._ghi_dang(trang["id"])
        return kq
