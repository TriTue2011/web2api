"""Lớp gọi Tuya Open API (đám mây) cho khoá cửa.

TẬN DỤNG từ c2a: công thức ký HMAC-SHA256 và quy trình xin token của c2a
`services/tuya_nha.py` (commit d879b03, CHỈ ĐỌC — bản sao
~/workspace/c2a-nv146). CHÉP NGUYÊN từ mã chạy thật:
- Công thức ký: access_id + [access_token] + t + method + "\\n" +
  sha256(body) + "\\n\\n" + đường_dẫn_gồm_cả_truy_vấn_đã_sắp_xếp.
- Quy trình: token /v1.0/token?grant_type=1 (xin lại trước 60 giây).
Đường ĐẶC THÙ khoá cửa CHÉP từ c2a `services/khoa_cua_nha.py` (d879b03):
- Nhật ký mở/đóng: GET /v1.0/devices/{id}/door-lock/open-logs.
ĐIỂM KHÁC so với c2a (khác biệt có chủ ý, ghi rõ):
- Bất đồng bộ bằng httpx qua TransportGhimIp của lõi (ghim IP đã kiểm, chống
  DNS rebinding); miền máy chủ CHỈ từ đúng sáu host trong MAY_CHU — không có
  đường nào gửi khoá tới máy chủ ngoài Tuya (theo khuôn NV157 dam_may.py).
- Không gọi HA (c2a đọc thêm từ lịch sử Home Assistant — web2api không có
  dịch vụ này); chỉ dùng đám mây Tuya.
- Mã lệnh mở/đóng KHÔNG đoán DP của khoá thật: lấy từ cấu hình (lenh_mo,
  lenh_dong) do chủ máy khai theo mã lệnh của khoá mình; chưa khai thì hành
  động từ chối an toàn thay vì gửi lệnh sai.

Khoá (access_secret/access_token) chỉ nằm trong RAM tiến trình, không vào log,
không vào thông điệp lỗi của API.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.chuyen_tiep import TransportGhimIp, doc_phan_hoi_co_tran, PhanHoiQuaLon
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

GIay_XIN_TRUOC_HET_HAN = 60
TRAN_PHAN_HOI = 2 * 1024 * 1024


class LoiKhoaCua(Exception):
    """Lỗi gọi đám mây — thông điệp đã làm sạch, không chứa bí mật."""


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


def _ky(secret: str, chuoi: str) -> str:
    return hmac.new(secret.encode("utf-8"), chuoi.encode("utf-8"),
                    hashlib.sha256).hexdigest().upper()


class KhoaDamMay:
    """Backend khoá cửa qua Tuya Open API.

    Backend cho test (KhoaGia trong test) cài cùng giao diện:
    trang_thai_khoa(), nhat_ky_mo(so_ngay), mo_khoa(), dong_khoa(), dong().
    """

    def __init__(self, access_id: str, access_secret: str, vung: str,
                 ma_thiet_bi: str, ma_dp_trang_thai: str = "lock_motor_state",
                 lenh_mo: list | None = None, lenh_dong: list | None = None) -> None:
        co_so = MAY_CHU.get(str(vung or "america").strip().lower())
        if not co_so or not kiem_mien_tuya(co_so):
            raise LoiKhoaCua("Vùng không hợp lệ — chọn: "
                             + ", ".join(sorted(MAY_CHU)) + ".")
        self._access_id = str(access_id or "")
        self._secret = str(access_secret or "")
        self._co_so = co_so
        self._ma_thiet_bi = str(ma_thiet_bi or "")
        self._ma_dp = str(ma_dp_trang_thai or "lock_motor_state")
        self._lenh_mo = lenh_mo
        self._lenh_dong = lenh_dong
        self._token = ""
        self._token_den: float = 0.0
        self._client: httpx.AsyncClient | None = None

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

    async def _token_hien_tai(self) -> str:
        if self._token and time.time() < self._token_den - GIay_XIN_TRUOC_HET_HAN:
            return self._token
        du_lieu = await self._gui("/v1.0/token?grant_type=1", "GET", b"", "")
        kq = du_lieu.get("result") or {}
        token = str(kq.get("access_token") or "")
        if not token:
            raise LoiKhoaCua("Tuya không cấp access token — kiểm Access ID/Secret.")
        self._token = token
        self._token_den = time.time() + int(kq.get("expire_time") or 7200)
        return token

    async def _gui(self, duong: str, method: str, body: bytes, token: str) -> dict:
        t = str(int(time.time() * 1000))
        if "?" in duong:
            path, qs = duong.split("?", 1)
            duong_ky = path + "?" + "&".join(sorted(p for p in qs.split("&") if p))
        else:
            duong_ky = duong
        chuoi = (f"{self._access_id}{token}{t}"
                 f"{method}\n{hashlib.sha256(body).hexdigest()}\n\n{duong_ky}")
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
                method, self._co_so + duong_ky, content=body or None, headers=dau)
        except httpx.TimeoutException as exc:
            raise LoiKhoaCua("Hết giờ chờ Tuya trả lời (20 giây).") from exc
        except httpx.HTTPError as exc:
            raise LoiKhoaCua(f"Không nối được Tuya: {type(exc).__name__}.") from exc
        try:
            than = await doc_phan_hoi_co_tran(phan_hoi, TRAN_PHAN_HOI)
        except PhanHoiQuaLon as exc:
            raise LoiKhoaCua("Phản hồi của Tuya quá lớn — bỏ qua an toàn.") from exc
        import json as _json
        try:
            du_lieu = _json.loads(than.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise LoiKhoaCua("Tuya trả dữ liệu không phải JSON — bỏ qua an toàn.") from exc
        if not isinstance(du_lieu, dict):
            raise LoiKhoaCua("Tuya trả dữ liệu lạ — bỏ qua an toàn.")
        if du_lieu.get("success") is not True:
            msg = str(du_lieu.get("msg") or "").lower()
            if "cross" in msg:
                chi = "Tuya từ chối vì SAI VÙNG tài khoản (cross-region)."
            elif "sign invalid" in msg:
                chi = "Chữ ký không hợp lệ: kiểm Access Secret hoặc đồng hồ hệ thống."
            else:
                chi = f"Tuya báo lỗi (mã {du_lieu.get('code')}): {du_lieu.get('msg') or 'không rõ'}."
            raise LoiKhoaCua(chi)
        return du_lieu

    async def goi_api(self, duong: str, method: str = "GET",
                      than: dict | None = None) -> dict:
        token = await self._token_hien_tai()
        import json as _json
        body = _json.dumps(than).encode("utf-8") if than is not None else b""
        return await self._gui(duong, method, body, token)

    # ── nghiệp vụ ──

    async def trang_thai_khoa(self) -> str:
        """«khoa» / «mo» / «chua_ro».

        Quy ước: giá trị DP mã `ma_dp_trang_thai` đúng (truthy) = đã khoá — theo
        quy ước khoá Tuya; CHƯA kiểm trên khoá thật, chủ máy đổi được mã DP.
        """
        try:
            du_lieu = await self.goi_api(
                f"/v2.0/cloud/thing/{self._ma_thiet_bi}/shadow/properties")
        except LoiKhoaCua:
            return "chua_ro"
        for muc in (du_lieu.get("result") or {}).get("properties") or []:
            if isinstance(muc, dict) and str(muc.get("code")) == self._ma_dp:
                return "khoa" if muc.get("value") else "mo"
        return "chua_ro"

    async def nhat_ky_mo(self, so_ngay: int) -> list[dict[str, Any]]:
        """Nhật ký mở/đóng — CHÉP đường open-logs từ c2a khoa_cua_nha.py.

        Mỗi mục: {"ma": "cach#so", "ten_tuya": str, "ts": float, "thiet_bi": str}.
        """
        nay = int(time.time() * 1000)
        tu = nay - max(1, min(30, int(so_ngay))) * 86400 * 1000
        try:
            du_lieu = await self.goi_api(
                f"/v1.0/devices/{self._ma_thiet_bi}/door-lock/open-logs"
                f"?start_time={tu}&end_time={nay}&page_no=1&page_size=50")
        except LoiKhoaCua:
            return []
        ra: list[dict[str, Any]] = []
        for l in (du_lieu.get("result") or {}).get("logs") or []:
            if not isinstance(l, dict):
                continue
            st = l.get("status") or {}
            cach = str(st.get("code") or "").replace("unlock_", "")
            so = str(st.get("value") or "")
            ma = f"{cach}#{so}" if cach and so else ""
            if not ma:
                continue
            ra.append({
                "ma": ma,
                "ten_tuya": str(l.get("unlock_name") or l.get("nick_name") or ""),
                "ts": float(l.get("time") or l.get("update_time") or 0) / 1000,
                "thiet_bi": "khoá cửa",
            })
        ra.sort(key=lambda x: -x["ts"])
        return ra

    async def _lenh(self, lenh: list | None, ten: str) -> None:
        if not lenh:
            raise LoiKhoaCua(f"Chưa khai lệnh {ten} (lenh_mo/lenh_dong) — "
                             "khai theo mã lệnh của khoá trong cấu hình.")
        await self.goi_api(f"/v1.0/iot-03/devices/{self._ma_thiet_bi}/commands",
                           "POST", {"commands": lenh})

    async def mo_khoa(self) -> None:
        await self._lenh(self._lenh_mo, "mở")

    async def dong_khoa(self) -> None:
        await self._lenh(self._lenh_dong, "đóng")
