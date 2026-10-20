"""Lớp gọi Tuya Open API (đám mây) — PORT từ c2a, services/tuya_nha.py
(commit d879b03, CHỈ ĐỌC, không sửa c2a).

Phần CHÉP NGUYÊN từ mã chạy thật của c2a (đối chiếu từng dòng lúc port):
- Bảng máy chủ theo vùng (MAY_CHU) và bản đồ ngành→loại (_NGANH).
- Công thức ký HMAC-SHA256: access_id + [access_token] + t + method + "\n" +
  sha256(body) + "\n\n" + đường_dẫn_gồm_cả_truy_vấn_đã_sắp_xếp — sai một dấu
  xuống dòng là Tuya trả «sign invalid», nên test đo bằng vector độc lập.
- Quy trình: token /v1.0/token?grant_type=1 (hiệu lực 7200s, xin lại trước
  60s), danh sách thiết bị /v2.0/cloud/thing/device (tối đa 20/trang, lật
  trang bằng last_row_key, trần 25 trang), thuộc tính
  /v2.0/cloud/thing/{id}/shadow/properties, lệnh
  POST /v1.0/iot-03/devices/{id}/commands, local_key qua /v1.0/devices/{id}.
- Thông điệp lỗi dịch có ý: «subscription expired» / «sign invalid» /
  «cross-region» — lần lượt gợi ý gia hạn, kiểm Secret, kiểm vùng tài khoản.

Phần THAY ĐỔI so với c2a (khác biệt có chủ ý, ghi rõ):
- c2a đồng bộ (requests, khoá distill.db). Bản này bất đồng bộ bằng httpx
  qua TransportGhimIp của lõi (ghim IP đã kiểm, chống DNS rebinding; IP
  không hợp lệ bị chặn trước khi nối). Miền máy chủ CHỈ được phép từ đúng
  sáu host trong MAY_CHU (kiem_mien_tuya kiểm cấu trúc qua urlsplit) —
  không có đường nào gửi khoá tới máy chủ ngoài Tuya.
- c2a trả chi tiết thiết bị ra bot chat (người dùng của bot xem là đúng
  quyền). Bản module KHÔNG: chi tiết thô chỉ dùng nội bộ, bí mật luôn qua
  che_bi_mat trước khi bất cứ thứ gì rời khỏi lớp này.

Khoá (access_secret/access_token/local_key) chỉ nằm trong RAM tiến trình,
không ghi log, không vào thông báo lỗi của API.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.chuyen_tiep import (
    PhanHoiQuaLon,
    TransportGhimIp,
    doc_phan_hoi_co_tran,
)
from app.tu_xa import _dia_chi_bi_chan

#: Vùng của TÀI KHOẢN Tuya lúc đăng ký (không phải nơi ở) → máy chủ Open API.
MAY_CHU: dict[str, str] = {
    "america": "https://openapi.tuyaus.com",
    "china": "https://openapi.tuyacn.com",
    "europe": "https://openapi.tuyaeu.com",
    "india": "https://openapi.tuyain.com",
    "west-america": "https://openapi-ueaz.tuyaus.com",
    "west-europe": "https://openapi-weaz.tuyaeu.com",
}

_HOSTS_DUOC_PHEP = {urlsplit(u).hostname for u in MAY_CHU.values()}

#: Ngành (category) → (tên tiếng Việt, nhóm). CHÉP từ _NGANH của c2a.
_NGANH: dict[str, tuple[str, str]] = {
    "videolock": ("Khoá cửa video", "khoá"), "jtmspro": ("Khoá cửa", "khoá"),
    "ms": ("Khoá cửa", "khoá"), "mk": ("Ổ khoá", "khoá"), "bxx": ("Hộp an toàn", "khoá"),
    "kg": ("Công tắc", "ghi"), "cz": ("Ổ cắm", "ghi"), "pc": ("Ổ cắm dài", "ghi"),
    "dj": ("Đèn", "ghi"), "dd": ("Dây đèn", "ghi"), "fwd": ("Đèn không khí", "ghi"),
    "xdd": ("Đèn trần", "ghi"), "dc": ("Đèn bàn", "ghi"),
    "wk": ("Điều nhiệt", "ghi"), "ktkzq": ("Điều hoà", "ghi"),
    "ks": ("Quạt", "ghi"), "fs": ("Quạt", "ghi"),
    "cl": ("Rèm cửa", "ghi"), "clkg": ("Công tắc rèm", "ghi"),
    "rs": ("Bình nóng lạnh", "doc"),  # luật nhà: chỉ đọc
    "wsdcg": ("Cảm biến nhiệt ẩm", "doc"), "ldcg": ("Cảm biến ánh sáng", "doc"),
    "pir": ("Cảm biến chuyển động", "doc"), "mcs": ("Cảm biến cửa", "doc"),
    "sj": ("Cảm biến nước", "doc"), "ywbj": ("Báo khói", "doc"), "rqbj": ("Báo gas", "doc"),
    "sp": ("Camera", "doc"), "ipc": ("Camera", "doc"),
    "qt": ("Cảm biến khác", "doc"), "wg2": ("Cổng kết nối", "doc"),
}

#: Ngành KHÔNG BAO GIỜ điều khiển (luật nhà — cấu trúc, không theo tên):
#: mọi khoá/ổ khoá + bình nóng lạnh + bếp nhiệt. Ngành lạ cũng mặc định doc.
_NGANH_KHONG_DIEU_KHIEN = {
    "videolock", "jtmspro", "ms", "mk", "bxx",   # khoá / ổ khoá / hộp an toàn
    "rs",                                        # bình nóng lạnh
    "dcl", "kx",                                 # bếp điện / bếp từ
}

GIay_XIN_TRUOC_HET_HAN = 60
TRANG_TOI_DA = 25
TRAN_PHAN_HOI = 2 * 1024 * 1024


class LoiDamMay(Exception):
    """Lỗi đám mây có lời giải thích sẵn; không chứa bí mật."""


def kiem_mien_tuya(url: Any) -> bool:
    """True chỉ khi url là https và host nằm trong đúng sáu máy chủ Tuya."""
    if not isinstance(url, str) or not url:
        return False
    try:
        phan = urlsplit(url)
    except ValueError:
        return False
    if phan.scheme != "https":
        return False
    chu = (phan.hostname or "").lower()
    return chu in _HOSTS_DUOC_PHEP


def che_bi_mat(obj: Any) -> Any:
    """Trả bản sao đã che: access_secret/password/local_key → «***»;
    access_id → 4 ký tự đầu + «…». CHÉP nguyên ý từ c2a (che_bi_mat)."""
    if isinstance(obj, dict):
        kq = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in ("access_secret", "secret", "password", "local_key", "token"):
                kq[k] = "***"
            elif kl == "access_id" and isinstance(v, str) and len(v) > 4:
                kq[k] = v[:4] + "…"
            else:
                kq[k] = che_bi_mat(v)
        return kq
    if isinstance(obj, list):
        return [che_bi_mat(x) for x in obj]
    return obj


def _ky(secret: str, chuoi: str) -> str:
    return hmac.new(secret.encode("utf-8"), chuoi.encode("utf-8"),
                    hashlib.sha256).hexdigest().upper()


def _dich_loi(msg: str, code: Any) -> str:
    """Dịch thông điệp lỗi của Tuya thành lời khuyên — CHÉP ý từ c2a."""
    m = (msg or "").lower()
    if "cross" in m:
        return ("Tuya từ chối vì SAI VÙNG tài khoản (cross-region). Kiểm mục "
                "«vùng» trong cấu hình: phải đúng vùng lúc đăng ký app Tuya.")
    if "sign invalid" in m or "sign" in m and "invalid" in m:
        return ("Chữ ký không hợp lệ (sign invalid): thường do Access Secret "
                "sai (không khớp dự án) hoặc đồng hồ hệ thống lệch giờ.")
    if "subscription" in m or "permission" in m or "expired" in m:
        return ("Thuê bao IoT Core đã hết hạn hoặc dự án chưa kích hoạt dịch "
                "vụ. Gia hạn ở iot.tuya.com → Cloud → dự án → Service API.")
    if code:
        return f"Tuya báo lỗi (mã {code}): {msg or 'không rõ'}."
    return f"Tuya báo lỗi: {msg or 'không rõ'}."


class DamMay:
    """Khách gọi Open API cho MỘT tài khoản Tuya (một vùng)."""

    def __init__(self, access_id: str, access_secret: str, vung: str) -> None:
        co_so = MAY_CHU.get(str(vung or "america").strip().lower())
        if not co_so:
            raise LoiDamMay("Vùng không hợp lệ — chọn: "
                            + ", ".join(sorted(MAY_CHU)) + ".")
        if not kiem_mien_tuya(co_so):  # bất biến, kiểm lại cho chắc
            raise LoiDamMay("Máy chủ vùng không thuộc miền Tuya Open API.")
        self._access_id = str(access_id or "")
        self._secret = str(access_secret or "")
        self._co_so = co_so
        self.vung = str(vung or "america").strip().lower()
        self._token: str = ""
        self._token_den: float = 0.0
        self._khoa_token = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None

    # ── hạ tầng ──

    def _lay_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                transport=TransportGhimIp(_dia_chi_bi_chan),
                timeout=httpx.Timeout(20.0),
                follow_redirects=False,
            )
        return self._client

    async def dong(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _chuoi_ky(self, method: str, duong: str, body: bytes, token: str) -> str:
        """CHÉP NGUYÊN công thức c2a: id + [token] + t do nơi gọi gắn sau;
        ở đây dựng phần method/body/đường, t chèn giữa lúc ký thật."""
        return (f"{method}\n{hashlib.sha256(body).hexdigest()}\n\n{duong}")

    async def _token_hien_tai(self) -> str:
        if self._token and time.time() < self._token_den - GIay_XIN_TRUOC_HET_HAN:
            return self._token
        async with self._khoa_token:
            if self._token and time.time() < self._token_den - GIay_XIN_TRUOC_HET_HAN:
                return self._token
            du_lieu = await self._gui("/v1.0/token?grant_type=1", "GET", b"", "")
            kq = du_lieu.get("result") or {}
            token = str(kq.get("access_token") or "")
            if not token:
                raise LoiDamMay("Tuya không cấp access token — kiểm Access ID/Secret.")
            self._token = token
            song = int(kq.get("expire_time") or 7200)
            self._token_den = time.time() + song
            return token

    async def _gui(self, duong: str, method: str, body: bytes,
                   token: str) -> dict:
        t = str(int(time.time() * 1000))
        if "?" in duong:  # tham số truy vấn PHẢI sắp xếp trước khi ký và khi gửi
            path, qs = duong.split("?", 1)
            cac = sorted(p for p in qs.split("&") if p)
            duong_ky = path + "?" + "&".join(cac)
        else:
            duong_ky = duong
        chuoi = (f"{self._access_id}{token}{t}"
                 f"{self._chuoi_ky(method, duong_ky, body, token)}")
        dau = {
            "client_id": self._access_id,
            "sign": _ky(self._secret, chuoi),
            "t": t,
            "sign_method": "HMAC-SHA256",
        }
        if token:
            dau["access_token"] = token
        if body:
            dau["Content-Type"] = "application/json"
        try:
            phan_hoi = await self._lay_client().request(
                method, self._co_so + duong_ky, content=body or None,
                headers=dau)
        except httpx.TimeoutException as exc:
            raise LoiDamMay("Hết giờ chờ Tuya trả lời (20 giây).") from exc
        except httpx.HTTPError as exc:
            raise LoiDamMay(f"Không nối được Tuya: {type(exc).__name__}.") from exc
        try:
            than = await doc_phan_hoi_co_tran(phan_hoi, TRAN_PHAN_HOI)
        except PhanHoiQuaLon as exc:
            raise LoiDamMay("Phản hồi của Tuya quá lớn — bỏ qua an toàn.") from exc
        import json as _json
        try:
            du_lieu = _json.loads(than.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise LoiDamMay("Tuya trả dữ liệu không phải JSON — bỏ qua an toàn.") from exc
        if not isinstance(du_lieu, dict):
            raise LoiDamMay("Tuya trả dữ liệu lạ — bỏ qua an toàn.")
        if du_lieu.get("success") is not True:
            raise LoiDamMay(_dich_loi(str(du_lieu.get("msg") or ""),
                                      du_lieu.get("code")))
        return du_lieu

    async def goi_api(self, duong: str, method: str = "GET",
                      than: dict | None = None) -> dict:
        """Gọi API có token. Trả toàn bộ phong bì đã kiểm success."""
        token = await self._token_hien_tai()
        import json as _json
        body = _json.dumps(than).encode("utf-8") if than is not None else b""
        return await self._gui(duong, method, body, token)

    # ── nghiệp vụ (port từ c2a) ──

    async def danh_sach_thiet_bi(self) -> list[dict]:
        """Mọi thiết bị của dự án; lật trang bằng last_row_key (tối đa 25 trang)."""
        tat_ca: list[dict] = []
        moc = ""
        for _ in range(TRANG_TOI_DA):
            duong = "/v2.0/cloud/thing/device?page_size=20"
            if moc:
                duong += f"&last_row_key={moc}"
            du_lieu = await self.goi_api(duong)
            kq = du_lieu.get("result") or {}
            for tb in kq.get("list") or []:
                if not isinstance(tb, dict) or not tb.get("id"):
                    continue
                nganh = str(tb.get("category") or "")
                ten_loai, _nhom = _NGANH.get(nganh, ("Thiết bị Tuya", "doc"))
                tat_ca.append({
                    "id": str(tb["id"]),
                    "ten": str(tb.get("name") or tb["id"]),
                    "nganh": nganh,
                    "loai": ten_loai,
                    "model": str(tb.get("model") or ""),
                    "truc_tuyen": bool(tb.get("is_online")),
                    "pham_vi": "cloud",
                })
            moc = str(kq.get("last_row_key") or "")
            if not moc:
                break
        return tat_ca

    async def thuoc_tinh(self, ma_thiet_bi: str) -> dict[str, Any]:
        """Thuộc tính hiện tại (shadow): {code: value}."""
        du_lieu = await self.goi_api(
            f"/v2.0/cloud/thing/{ma_thiet_bi}/shadow/properties")
        kq = du_lieu.get("result") or {}
        tra: dict[str, Any] = {}
        for muc in kq.get("properties") or []:
            if isinstance(muc, dict) and muc.get("code"):
                tra[str(muc["code"])] = muc.get("value")
        return tra

    async def dac_ta(self, ma_thiet_bi: str) -> dict:
        """Đặc tả: functions (ghi được) + status (đọc) kèm dp_id."""
        du_lieu = await self.goi_api(f"/v1.0/devices/{ma_thiet_bi}/specifications")
        kq = du_lieu.get("result") or {}
        return kq if isinstance(kq, dict) else {}

    async def gui_lenh(self, ma_thiet_bi: str, lenh: list[dict]) -> bool:
        du_lieu = await self.goi_api(
            f"/v1.0/iot-03/devices/{ma_thiet_bi}/commands", "POST",
            {"commands": lenh})
        return du_lieu.get("result") is True or du_lieu.get("success") is True

    async def lay_local_key(self, ma_thiet_bi: str) -> str:
        """local_key của thiết bị qua Open API — CHÉP từ c2a."""
        du_lieu = await self.goi_api(f"/v1.0/devices/{ma_thiet_bi}")
        kq = du_lieu.get("result") or {}
        khoa = str(kq.get("local_key") or "")
        if not khoa:
            raise LoiDamMay("Tuya không trả local_key cho thiết bị này.")
        return khoa
