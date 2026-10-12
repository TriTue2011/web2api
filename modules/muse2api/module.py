"""Module muse2api — Muse Spark qua Meta Model API chính thức.

Gọi https://api.meta.ai/v1 (tương thích OpenAI). Hai cách lấy key:

1. **API key tĩnh**: dán key từ Meta for Developers Console → Model API
   dashboard vào trường `api_keys`.
2. **OAuth device flow** (không cần dashboard): để trống `api_keys`, bấm
   Cài đặt → mở URL hiện ra, nhập mã, phê duyệt trên trình duyệt của bạn →
   bấm Cài đặt lại để đúc key. Key tự đúc lại mỗi ngày bằng identity token
   đã lưu (không cần bạn làm gì thêm).

Không mật khẩu, không cookie phiên web, không scrape.

Khả năng: chat (stream / không stream), anh (tạo ảnh).
Video: official API không có endpoint nên không khai.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from app.chuyen_tiep import ChuyenTiepOpenAI, ds_khoa

log = logging.getLogger("web2api.module.muse2api")

BASE_URL_MAC_DINH = "https://api.meta.ai/v1"

# ── OAuth device flow (RFC 8628) — hằng số đã xác minh từ tooling chính chủ
# của Meta (Muse Code CLI / pi-meta-oauth). Endpoint đúc key chưa nằm trong
# docs public dev.meta.ai.
_OAUTH_AUTHORIZE_URL = "https://auth.meta.com/oidc/device/authorization/"
_OAUTH_TOKEN_URL = "https://auth.meta.com/oidc/device/token/"
_MINT_URL = "https://api.meta.ai/muse-code/key"
_CLIENT_ID = "1031625952748946"
_API_VERSION = "1.0.0"
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_KEY_TTL = 24 * 3600  # key đúc lại mỗi ngày
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class OAuthDenied(Exception):
    """Người dùng từ chối phê duyệt."""


class OAuthExpired(Exception):
    """Mã thiết bị hết hạn."""


class OAuthError(Exception):
    """Lỗi khác của luồng OAuth."""


def _oauth_file() -> Path:
    # Lõi tạo instance Module MỚI cho mỗi lần cai() nên trạng thái device flow
    # phải nằm trên đĩa, cạnh module (thư mục cài lúc chạy, ghi được).
    return Path(__file__).resolve().parent / ".oauth.json"


def _doc_oauth() -> dict:
    try:
        data = json.loads(_oauth_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _ghi_oauth(data: dict) -> None:
    p = _oauth_file()
    p.write_text(json.dumps(data), encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass


def _xoa_oauth() -> None:
    try:
        _oauth_file().unlink()
    except OSError:
        pass


async def _oauth_bat_dau(client: httpx.AsyncClient) -> dict:
    """Bắt đầu device flow. Trả về {device_code, user_code, verification_uri, expires_at}."""
    try:
        r = await client.post(_OAUTH_AUTHORIZE_URL, data={"client_id": _CLIENT_ID},
                              timeout=_TIMEOUT)
    except httpx.HTTPError as e:
        raise OAuthError(f"không gọi được {_OAUTH_AUTHORIZE_URL}: {e}") from e
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400 or not body.get("device_code") or not body.get("user_code"):
        raise OAuthError(f"Meta từ chối khởi tạo OAuth (HTTP {r.status_code})")
    uri = body.get("verification_uri_complete") or body.get("verification_uri") or ""
    if not uri.startswith("http"):
        raise OAuthError("Meta trả verification_uri không hợp lệ")
    try:
        expires_in = max(60.0, float(body.get("expires_in", 900)))
    except (TypeError, ValueError):
        expires_in = 900.0
    return {"device_code": str(body["device_code"]), "user_code": str(body["user_code"]),
            "verification_uri": uri, "expires_at": time.time() + expires_in}


async def _oauth_poll(client: httpx.AsyncClient, device_code: str) -> str | None:
    """Hỏi một lần: trả về identity token, None nếu người dùng chưa duyệt."""
    try:
        r = await client.post(_OAUTH_TOKEN_URL,
                              data={"grant_type": _DEVICE_GRANT,
                                    "device_code": device_code,
                                    "client_id": _CLIENT_ID},
                              timeout=_TIMEOUT)
    except httpx.HTTPError as e:
        raise OAuthError(f"không hỏi được trạng thái OAuth: {e}") from e
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code < 400 and body.get("access_token"):
        return str(body["access_token"])
    err = str(body.get("error", ""))
    if err == "authorization_pending":
        return None
    if err == "slow_down":
        return None
    if err == "access_denied":
        raise OAuthDenied("bạn đã từ chối phê duyệt trên trình duyệt")
    if err == "expired_token":
        raise OAuthExpired("mã thiết bị đã hết hạn, bấm Cài đặt để lấy mã mới")
    raise OAuthError(f"OAuth thất bại: {err or r.status_code}")


async def _oauth_mint(client: httpx.AsyncClient, identity_token: str) -> str:
    """Đổi identity token lấy Model API key."""
    try:
        r = await client.post(
            _MINT_URL,
            headers={"Accept": "application/json",
                     "Authorization": f"Bearer {identity_token}",
                     "Content-Type": "application/json",
                     "x-api-version": _API_VERSION},
            content=b"{}",
            timeout=_TIMEOUT)
    except httpx.HTTPError as e:
        raise OAuthError(f"không đúc được key: {e}") from e
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400:
        raise OAuthError(f"Meta không cấp key (HTTP {r.status_code})")
    key = (body.get("api_key") or "").strip()
    if not key:
        raise OAuthError("Meta không trả về api_key")
    return key


def phan_loai_loi(ma: int, loi: dict, boi_canh: dict) -> str | None:
    """Phân loại lỗi Meta Model API về mã trung lập của lõi."""
    if ma in (401, 403):
        return "khoa_hong"  # key sai / hết hạn / bị thu hồi
    if ma == 429:
        return "het_han_muc"  # rate limit tính theo team
    if ma == 400:
        return "yeu_cau_sai"
    if ma is not None and ma >= 500:
        return "may_chu_loi"
    return None


def chuan_hoa_base_url(gia_tri: Any) -> str:
    base = (gia_tri or "").strip() or BASE_URL_MAC_DINH
    return base.rstrip("/")


class Module:
    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self._ct: ChuyenTiepOpenAI | None = None
        self._base = BASE_URL_MAC_DINH

    def phan_loai_loi(self, ma: int, loi: dict, boi_canh: dict) -> str | None:
        return phan_loai_loi(ma, loi, boi_canh)

    def _tao(self, khoa: list[str], base: str) -> ChuyenTiepOpenAI:
        # Khả năng tốn tiền suc_khoe báo theo từng khoá = mọi mã đã khai ở `cung_cap` trừ chat.
        kn = tuple(x for x in self.manifest.get("cung_cap", []) if x != "chat")
        return ChuyenTiepOpenAI(ten="Meta", tien_to=self.tien_to, base_url=base,
                                khoa=khoa, phan_loai_loi=self.phan_loai_loi,
                                kha_nang=kn)

    # ── Vòng đời ────────────────────────────────────────────────────────

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM (lúc khởi động/cài xong) — không gọi mạng."""
        self._base = chuan_hoa_base_url(cau_hinh.get("base_url"))
        khoa = ds_khoa(cau_hinh.get("api_keys"))
        oauth = _doc_oauth()
        # Người dùng đổi key tay (key trong kho khác key OAuth đã đúc) → bỏ trạng thái OAuth cũ.
        if oauth.get("api_key") and khoa and oauth["api_key"] not in khoa:
            _xoa_oauth()
            oauth = {}
        khoa_dung = [oauth["api_key"]] if oauth.get("api_key") else khoa
        self._ct = self._tao(khoa_dung or khoa, self._base)

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm cấu hình. Hai đường:

        - Có `api_keys`: kiểm từng khoá bằng GET /models (không tốn token).
        - Trống `api_keys`: OAuth device flow 2 pha (pha 1 trả mã + URL,
          pha 2 — sau khi bạn phê duyệt — đúc key và lưu).
        """
        khoa = ds_khoa(cau_hinh.get("api_keys"))
        base = chuan_hoa_base_url(cau_hinh.get("base_url"))
        if khoa:
            return await self._cai_khoa_tinh(khoa, base)
        return await self._cai_oauth(base)

    async def _cai_khoa_tinh(self, khoa: list[str], base: str) -> dict:
        ct = self._tao(khoa, base)
        try:
            ds = (await ct.suc_khoe())["khoa"]
        finally:
            await ct.dung()
        tot = [k for k, muc in zip(khoa, ds) if muc["ok"]]
        if not tot:
            return {"ok": False, "ket_qua_tung_khoa": ds,
                    "loi": f"Không khoá nào gọi được {base}/models."}
        _xoa_oauth()  # dùng key tay → không cần trạng thái OAuth nữa
        return {"ok": True, "ket_qua_tung_khoa": ds, "loi": None,
                "cau_hinh_luu": {"api_keys": tot, "base_url": base}}

    async def _cai_oauth(self, base: str) -> dict:
        khong = {"ok": False, "ket_qua_tung_khoa": []}
        async with httpx.AsyncClient() as client:
            oauth = _doc_oauth()
            bay_gio = time.time()
            if oauth.get("device_code") and oauth.get("expires_at", 0) > bay_gio:
                # Pha 2: hỏi xem bạn đã phê duyệt chưa.
                try:
                    identity = await _oauth_poll(client, oauth["device_code"])
                except (OAuthDenied, OAuthExpired) as e:
                    _xoa_oauth()
                    return {**khong, "loi": f"OAuth: {e}."}
                except OAuthError as e:
                    return {**khong, "loi": f"OAuth: {e}."}
                if identity is None:
                    return {**khong, "loi": (
                        f"Chưa thấy bạn phê duyệt. Mở {oauth['verification_uri']}, "
                        f"nhập mã {oauth['user_code']}, rồi bấm Cài đặt lại.")}
                try:
                    api_key = await _oauth_mint(client, identity)
                except OAuthError as e:
                    _xoa_oauth()
                    return {**khong, "loi": f"Đúc key thất bại: {e}."}
                _ghi_oauth({"identity_token": identity, "api_key": api_key,
                            "key_expires_at": bay_gio + _KEY_TTL})
                ct = self._tao([api_key], base)
                try:
                    ds = (await ct.suc_khoe())["khoa"]
                finally:
                    await ct.dung()
                if not ds or not ds[0]["ok"]:
                    return {"ok": False, "ket_qua_tung_khoa": ds,
                            "loi": "Key vừa đúc không gọi được /v1/models."}
                return {"ok": True, "ket_qua_tung_khoa": ds, "loi": None,
                        "cau_hinh_luu": {"api_keys": [api_key], "base_url": base}}
            # Pha 1 (hoặc mã cũ hết hạn): xin mã mới.
            if oauth.get("device_code"):
                _xoa_oauth()
            try:
                yc = await _oauth_bat_dau(client)
            except OAuthError as e:
                return {**khong, "loi": f"Không khởi động được OAuth: {e}."}
            _ghi_oauth({"device_code": yc["device_code"], "user_code": yc["user_code"],
                        "verification_uri": yc["verification_uri"],
                        "expires_at": yc["expires_at"]})
            return {**khong, "loi": (
                f"Mở {yc['verification_uri']} trên trình duyệt, nhập mã "
                f"{yc['user_code']}, phê duyệt cho Meta, rồi bấm Cài đặt lại.")}

    # ── Đúc lại key OAuth khi sắp hết hạn (trong suốt, không cần bạn) ──────

    async def _khoa_tuoi(self) -> None:
        oauth = _doc_oauth()
        ident = oauth.get("identity_token")
        if not ident:
            return
        if oauth.get("key_expires_at", 0) > time.time() + 300:
            return
        try:
            async with httpx.AsyncClient() as client:
                key = await _oauth_mint(client, ident)
        except Exception as e:  # noqa: BLE001 — lỗi đúc lại không được làm sập yêu cầu
            log.warning("muse2api: đúc lại key OAuth thất bại: %s", e)
            return
        oauth.update({"api_key": key, "key_expires_at": time.time() + _KEY_TTL})
        _ghi_oauth(oauth)
        self._ct = self._tao([key], self._base)
        log.info("muse2api: đã tự đúc lại key OAuth")

    # ── Khả năng ─────────────────────────────────────────────────────────

    async def chat(self, body: dict, stream: bool):
        await self._khoa_tuoi()
        return await self._ct.chat(body, stream)

    async def anh(self, body: dict):
        await self._khoa_tuoi()
        _, kq = await self._ct.goi_kha_nang(
            "anh", "tạo ảnh", "POST", f"{self._ct.base_url}/images/generations",
            json_than=self._ct.doi_model(body), tieu_de=self._ct._tieu_de)
        return kq

    async def models(self) -> list[dict]:
        return await self._ct.models()

    async def suc_khoe(self) -> dict:
        await self._khoa_tuoi()
        return await self._ct.suc_khoe()

    async def dung(self) -> None:
        if self._ct is not None:
            await self._ct.dung()
