"""Module openaicodex — đăng nhập OAuth PKCE, lưu/làm mới token, hạn mức.

Nguồn (chỉ đọc, chép theo LUẬT PORT): ~/workspace/c2a-nv146 (commit d879b03)
- services/oauth_service.py → phần Codex trong `oauth.py` (PKCE, đổi mã).
- services/codex_token_refresh.py → phần làm mới trong `oauth.py`.
- services/codex_callback_listener.py → `oauth.BoNgheCallback`.
- services/codex_usage.py → `han_muc.py`.
- services/codex_deactivated.py → `hoi_phuc.py`.
- services/providers/openai_oauth.py → đường chat ở đây: đổi chat/responses,
  401 → làm mới rồi thử lại, đếm 401 (tắt sau 3 lần liên tiếp), 403 → vô
  hiệu, 429 → giới hạn + mốc hồi phục, ghi hạn mức từ header. BỎ các helper
  riêng cho Home Assistant của bản gốc (không thuộc Codex).
- services/account_recovery.py → chỉ `_codex_exchange_from_redirect` (đổi mã
  từ URL callback người dùng dán — chính là hành động «dan_callback»); các
  tầng tự đăng nhập lại qua captcha-solver KHÔNG port (xem báo cáo).

Hàng rào an toàn của việc này:
- access/refresh token là `bi_mat`: lưu qua kho riêng của lõi (mã hoá khi
  nghỉ, 0600); KHÔNG vào log/phản hồi/suc_khoe/thuc_the.
- Mọi hành động ghi mặc định TẮT cho tới khi cài: module không tự chạy gì
  khi chưa có tài khoản; vòng tự làm mới của lõi chỉ gọi lam_moi_phien cho
  tài khoản ĐÃ có.
- Lỗi phân loại bằng CẤU TRÚC (mã trạng thái + mã lỗi có cấu trúc của nhà
  cung cấp), không dò chữ; lỗi mạng tạm không đánh dấu tài khoản hỏng.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.khoi_phuc_phien import KetQuaKhoiPhuc
from app.loi_openai import than_loi
from app.tu_xa import kiem_url_lan

from . import han_muc, hoi_phuc, oauth

log = logging.getLogger("web2api.openai_codex")

TIEN_TO = "openaicodex"
MAY_CHU_MAC_DINH = "https://chatgpt.com"
DUONG_CODEX = "/backend-api/codex/responses"

#: Model lấy từ bản c2a (CODEX_DEFAULT_MODEL / CODEX_AUTO_FALLBACK).
DANH_SACH_MODEL = ["gpt-5.5", "gpt-5"]

#: Ngưỡng chủ động làm mới: access token sống ~8 giờ, làm mới khi còn ≤ 6 giờ
#: (theo codex_refresh_scheduler của c2a).
NGUONG_LAM_MOI_GIAY = 6 * 3600

#: Số 401 liên tiếp mới vô hiệu tài khoản (một 401 đơn lẻ thường là tạm).
NGUONG_401_VO_HIEU = 3
# TRẦN CỨNG số lần làm mới token do 401 trong MỘT lượt chat (tổng mọi tài
# khoản), độc lập với mọi khoá/tập đã thấy: bảo đảm vòng lặp trong chat() luôn
# dừng dù định danh tài khoản có xoay thế nào (NV198b). c2a cũng chỉ làm mới
# một lần rồi trả 401 (openai_oauth.py: 401_after_refresh).
TRAN_LAM_MOI_MOI_LUOT = 1

TIEU_DE_CODEX = {
    "originator": "codex-cli",
    "User-Agent": "codex-cli/1.0.18 (Windows; x64)",
    "Content-Type": "application/json",
    "Accept": "text/event-stream",
}


def _gio_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _che_email(email: str) -> str:
    email = str(email or "")
    if "@" not in email:
        return ""
    ten, mien = email.rsplit("@", 1)
    return (ten[:1] + "***@" + mien) if ten else "***@" + mien


_DEM_MA_ON_DINH = itertools.count(1)


def _khoa_tu_token(token: str) -> str:
    """Khoá mờ ổn định của một token: «tk-» + 12 ký tự hex đầu của SHA-256."""
    return "tk-" + hashlib.sha256(str(token).encode("utf-8")).hexdigest()[:12]


def _la_token_api_only(token: str) -> bool:
    """Token CHỈ dùng được api.openai.com (không phải Codex/chatgpt.com):
    phân biệt bằng claim ĐẶC THÙ ChatGPT trong JWT — token không có claim
    `chatgpt_account_id`/`chatgpt_plan_type` thì không gọi được Codex.
    Không decode được → False (không loại nhầm). Port từ c2a."""
    tt = han_muc.thong_tin_token(token)
    if not tt["la_jwt"]:
        return False
    return not (tt["account_id"] or tt["plan"])


class _TaiKhoan:
    __slots__ = ("access_token", "refresh_token", "email", "plan",
                 "expires_at", "trang_thai", "device_id", "so_401",
                 "restore_at", "lan_dung_cuoi", "ma_on_dinh", "ma_tai_khoan")

    def __init__(self, access_token: str, refresh_token: str = ""):
        # Định danh ỔN ĐỊNH của tài khoản trong tiến trình: KHÔNG xoay theo
        # access_token/refresh_token như `khoa`. Chỉ dùng làm khoá «đã thử /
        # đã làm mới trong lượt» ở chat(); không lưu, không ra ngoài.
        self.ma_on_dinh = f"tkid-{next(_DEM_MA_ON_DINH)}"
        # Mã tài khoản BỀN QUA LÀM MỚI (NV198c): khoá công khai `khoa` của
        # tài khoản BẮT NGUỒN từ đây, không từ access_token xoay theo mỗi
        # lần làm mới. Cấp một lần khi thêm tài khoản, lưu bền trong kho.
        self.ma_tai_khoan = f"tk-{uuid.uuid4().hex[:16]}"
        self.access_token = access_token
        self.refresh_token = refresh_token
        tt = han_muc.thong_tin_token(access_token)
        self.email = tt["email"] or ""
        self.plan = tt["plan"] or ""
        self.expires_at: float = 0.0
        # Suy hạn từ claim exp nếu chưa có expires_at được ghi riêng.
        claims = han_muc.claims_tu_token(access_token)
        if claims.get("exp"):
            self.expires_at = float(claims["exp"])
        self.trang_thai = "hoat_dong"  # hoat_dong|gioi_han|vo_hieu|deactivated
        self.device_id = ""
        self.so_401 = 0
        self.restore_at: str | None = None
        self.lan_dung_cuoi: str = ""

    @property
    def khoa(self) -> str:
        # Khoá công khai ỔN ĐỊNH qua làm mới token (NV198c): bắt nguồn từ
        # mã bền `ma_tai_khoan`, KHÔNG từ access_token. Lõi giữ khoá này
        # trong lam_moi_phien/tai_khoan_ke/danh_dau_hong và trạng thái
        # khôi phục — sau làm mới nó vẫn trỏ đúng tài khoản.
        return self.ma_tai_khoan

    def _cap_ma_moi(self) -> None:
        """Cấp lại mã bền (dùng khi phát hiện trùng với tài khoản khác)."""
        self.ma_tai_khoan = f"tk-{uuid.uuid4().hex[:16]}"

    @property
    def id_thuc_the(self) -> str:
        return f"{TIEN_TO}.{self.khoa}"

    def sang_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "email": self.email,
            "plan": self.plan,
            "expires_at": self.expires_at,
            "trang_thai": self.trang_thai,
            "device_id": self.device_id,
            "so_401": self.so_401,
            "restore_at": self.restore_at,
            "ma_tai_khoan": self.ma_tai_khoan,
        }

    def ap_dung_dict(self, d: dict) -> None:
        if not isinstance(d, dict):
            return
        ma = d.get("ma_tai_khoan")
        if isinstance(ma, str) and ma and len(ma) <= 64:
            self.ma_tai_khoan = ma
        else:
            # Tài khoản CŨ chưa có mã bền: lấy khoá hiện tại (sha256 của
            # access_token lúc này) MỘT lần rồi giữ nguyên từ đây về sau.
            self.ma_tai_khoan = _khoa_tu_token(self.access_token)
        if d.get("email"):
            self.email = str(d["email"])
        if d.get("plan"):
            self.plan = str(d["plan"])
        try:
            self.expires_at = float(d.get("expires_at") or self.expires_at)
        except (TypeError, ValueError):
            pass
        if d.get("trang_thai") in ("hoat_dong", "gioi_han", "vo_hieu",
                                   "deactivated"):
            self.trang_thai = str(d["trang_thai"])
        self.device_id = str(d.get("device_id") or "")
        try:
            self.so_401 = int(d.get("so_401") or 0)
        except (TypeError, ValueError):
            self.so_401 = 0
        ra = d.get("restore_at")
        self.restore_at = str(ra) if ra else None


class Module:
    """Lớp Module theo hợp đồng app/kham_pha.py."""

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to", TIEN_TO)
        self.kho = None  # lõi gán sau khi tạo (kho riêng của module)
        self._khoa = threading.Lock()
        self._tai_khoan: list[_TaiKhoan] = []
        self._co_so_url: str | None = None
        self._cho_oauth = oauth.ChoOAuth()
        self._bo_nghe: oauth.BoNgheCallback | None = None
        # Mối nối test: transport httpx giả (MockTransport).
        self._transport_gia = None

    # -- mối nối HTTP --------------------------------------------------------

    def _gan_transport_gia(self, transport) -> None:
        """Test chích transport giả; triển khai thật để None."""
        self._transport_gia = transport

    def _client(self, timeout: float = 20.0):
        import httpx
        return httpx.AsyncClient(transport=self._transport_gia,
                                 timeout=timeout)

    async def _goi_http(self, method: str, url: str, **kw):
        """HTTP một-lượt cho oauth/han_muc (đúng chữ ký HttpGoi của oauth.py)."""
        async with self._client(timeout=float(kw.get("timeout") or 20)) as cli:
            if method == "POST":
                phan_hoi = await cli.post(url, data=kw.get("data"),
                                          headers=kw.get("headers") or {})
            else:
                phan_hoi = await cli.get(url, headers=kw.get("headers") or {})
            _ = phan_hoi.content
        return phan_hoi

    # -- kho tài khoản ---------------------------------------------------------

    def _doc_tai_khoan_kho(self) -> list[dict]:
        if self.kho is None:
            return []
        try:
            du_lieu = self.kho.doc("tai_khoan") or {}
        except Exception as e:
            log.warning("openaicodex: không đọc được kho tài khoản: %s",
                        type(e).__name__)
            return []
        ds = du_lieu.get("tai_khoan") if isinstance(du_lieu, dict) else None
        return [d for d in ds if isinstance(d, dict)] if isinstance(ds, list) else []

    def _luu_tai_khoan_kho(self) -> None:
        if self.kho is None:
            return
        try:
            with self._khoa:
                ds = [tk.sang_dict() for tk in self._tai_khoan]
            self.kho.luu("tai_khoan", {"tai_khoan": ds})
        except Exception as e:
            log.warning("openaicodex: không lưu được kho tài khoản: %s",
                        type(e).__name__)

    def _tim_tai_khoan(self, khoa: str) -> _TaiKhoan | None:
        with self._khoa:
            for tk in self._tai_khoan:
                if tk.khoa == khoa:
                    return tk
        return None

    def _tim_theo_refresh(self, refresh_token: str) -> dict | None:
        """Hook cho oauth.lam_moi_token: tránh POST trùng khi đã có bản mới."""
        with self._khoa:
            for tk in self._tai_khoan:
                if tk.refresh_token == refresh_token and tk.access_token:
                    return {"access_token": tk.access_token,
                            "expires_at": tk.expires_at,
                            "device_id": tk.device_id}
        return None

    def _them_hoac_cap_nhat(self, access: str, refresh: str,
                            expires_at: float = 0.0) -> _TaiKhoan:
        """Thêm tài khoản mới hoặc cập nhật theo email (chống trùng khi đăng
        nhập lại cùng một tài khoản — port ý dedupe của c2a)."""
        tt = han_muc.thong_tin_token(access)
        email = tt["email"]
        with self._khoa:
            dich = None
            for tk in self._tai_khoan:
                if (email and tk.email == email) or tk.access_token == access:
                    dich = tk
                    break
            if dich is None:
                dich = _TaiKhoan(access, refresh)
                # Hai tài khoản KHÁC nhau không bao giờ được trùng mã bền
                # (phòng va chạm ngẫu nhiên cực hiếm của uuid; trần 32 lần
                # để không treo nếu nguồn ngẫu nhiên suy biến).
                da_co = {u.ma_tai_khoan for u in self._tai_khoan}
                lan = 0
                while dich.ma_tai_khoan in da_co and lan < 32:
                    dich._cap_ma_moi()
                    lan += 1
                if dich.ma_tai_khoan in da_co:
                    goc, i = dich.ma_tai_khoan, 0
                    while f"{goc}-{i}" in da_co:
                        i += 1
                    dich.ma_tai_khoan = f"{goc}-{i}"
                self._tai_khoan.append(dich)
            else:
                dich.access_token = access
                if refresh:
                    dich.refresh_token = refresh
                dich.trang_thai = "hoat_dong"
                dich.so_401 = 0
            if email:
                dich.email = email
            if tt["plan"]:
                dich.plan = tt["plan"]
            if expires_at:
                dich.expires_at = expires_at
            elif han_muc.claims_tu_token(access).get("exp"):
                dich.expires_at = float(
                    han_muc.claims_tu_token(access)["exp"])
        self._luu_tai_khoan_kho()
        return dich

    # -- hợp đồng vòng đời ----------------------------------------------------

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM — không gọi mạng; dựng lại tài khoản từ kho."""
        cau_hinh = cau_hinh or {}
        tai_khoan = []
        for d in self._doc_tai_khoan_kho():
            access = str(d.get("access_token") or "")
            if not access:
                continue
            tk = _TaiKhoan(access, str(d.get("refresh_token") or ""))
            tk.ap_dung_dict(d)
            tai_khoan.append(tk)
        co_so = str(cau_hinh.get("co_so_url") or "").strip().rstrip("/") or None
        with self._khoa:
            self._tai_khoan = tai_khoan
            self._co_so_url = co_so
        log.info("openaicodex: đã nạp %d tài khoản", len(tai_khoan))

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm cấu hình; mỗi refresh_token dán vào được đổi lấy access_token
        và thêm vào bể tài khoản (lưu trong kho riêng, không ở cấu hình)."""
        self.nap(cau_hinh)
        if self._co_so_url:
            try:
                self._co_so_url = await kiem_url_lan(self._co_so_url)
            except ValueError as e:
                return {"ok": False, "ket_qua_tung_khoa": [],
                        "loi": f"co_so_url không hợp lệ: {e}"}
        danh_sach = (cau_hinh or {}).get("refresh_token") or []
        if isinstance(danh_sach, str):
            danh_sach = [danh_sach]
        ket_qua = []
        ok_het = True
        for rt in danh_sach:
            rt = str(rt or "").strip()
            if not rt:
                continue
            khoa_che = "…" + rt[-4:]
            try:
                kq = await oauth.lam_moi_token(
                    rt, http=self._goi_http,
                    tim_theo_refresh=self._tim_theo_refresh)
            except Exception:
                kq = None
            if kq and "error" not in kq:
                self._them_hoac_cap_nhat(kq["access_token"],
                                         kq.get("refresh_token") or rt,
                                         float(kq.get("expires_at") or 0))
                ket_qua.append({"khoa": khoa_che, "ok": True, "loi": None})
            elif kq and kq.get("error") == "unrecoverable":
                ok_het = False
                ket_qua.append({"khoa": khoa_che, "ok": False,
                                "loi": "refresh_token không còn dùng được "
                                       f"({kq.get('code', 'không rõ')})"})
            else:
                ok_het = False
                ket_qua.append({"khoa": khoa_che, "ok": False,
                                "loi": "không đổi được token (lỗi tạm — thử "
                                       "lại sau)"})
        return {
            "ok": ok_het,
            "ket_qua_tung_khoa": ket_qua,
            "loi": None if ok_het else "một số refresh_token không dùng được",
            "cau_hinh_luu": {"co_so_url": self._co_so_url or ""},
        }

    async def dung(self) -> None:
        if self._bo_nghe is not None:
            self._bo_nghe.dung()
            self._bo_nghe = None

    # -- models / suc_khoe -----------------------------------------------------

    async def models(self) -> list[dict]:
        with self._khoa:
            co_tai_khoan = any(tk.trang_thai == "hoat_dong"
                               for tk in self._tai_khoan)
        if not co_tai_khoan:
            return []
        return [{
            "id": f"{self.tien_to}/{ten}",
            "object": "model",
            "created": 1760000000,
            "owned_by": self.tien_to,
            "nhan": ten,
        } for ten in DANH_SACH_MODEL]

    async def suc_khoe(self) -> dict:
        with self._khoa:
            ds = list(self._tai_khoan)
        cho_duyet = self.danh_sach_cho_duyet()
        muc = []
        for tk in ds:
            ban = han_muc.kho_han_muc.lay(tk.access_token)
            muc.append({
                "tai_khoan_id": tk.id_thuc_the,
                "email_che": _che_email(tk.email),
                "plan": tk.plan,
                "trang_thai": tk.trang_thai,
                "het_han_sau_giay": (int(tk.expires_at - time.time())
                                     if tk.expires_at else None),
                "co_refresh_token": bool(tk.refresh_token),
                "han_muc": ban,
            })
        return {
            "ok": True,
            "so_tai_khoan": len(ds),
            "tai_khoan": muc,
            "cho_duyet_deactivated": len(cho_duyet),
        }

    # -- chuẩn thiet_bi (C1): thực thể = tài khoản + luồng đăng nhập ----------

    async def thuc_the(self, loc: dict | None = None) -> list[dict]:
        ra = [{
            "id": f"{self.tien_to}.dang-nhap",
            "ten": "Đăng nhập Codex (OAuth)",
            "loai": "dang_nhap",
            "thuoc_tinh": {},
            "tinh_nang": [
                {"hanh_dong": "bat_dau_dang_nhap",
                 "mo_ta": "Bắt đầu luồng OAuth PKCE: trả URL đăng nhập để mở "
                          "trong trình duyệt; bộ nghe callback loopback tự mở.",
                 "tham_so": {"type": "object", "additionalProperties": False,
                             "properties": {}}},
                {"hanh_dong": "dan_callback",
                 "mo_ta": "Đổi mã lấy token: dán URL callback đầy đủ "
                          "(http://localhost:1455/auth/callback?code=…&state=…) "
                          "vào url_callback; hoặc để trống nếu trình duyệt đã "
                          "gọi về bộ nghe 1455 trên cùng máy.",
                 "tham_so": {"type": "object",
                             "additionalProperties": False,
                             "properties": {
                                 "url_callback": {"type": "string",
                                                  "maxLength": 4096}}}},
            ],
            "rui_ro_mac_dinh": "ghi",
        }]
        with self._khoa:
            ds = list(self._tai_khoan)
        for tk in ds:
            tinh_nang = [
                {"hanh_dong": "lam_moi",
                 "mo_ta": "Làm mới access token ngay bằng refresh token.",
                 "tham_so": {"type": "object", "additionalProperties": False,
                             "properties": {}}},
                {"hanh_dong": "kiem_han_muc",
                 "mo_ta": "Hỏi /wham/usage để cập nhật hạn mức của tài khoản.",
                 "tham_so": {"type": "object", "additionalProperties": False,
                             "properties": {}}},
                {"hanh_dong": "xoa_tai_khoan",
                 "mo_ta": "Xoá tài khoản và token của nó khỏi kho của module.",
                 "tham_so": {"type": "object", "required": ["xac_nhan"],
                             "additionalProperties": False,
                             "properties": {
                                 "xac_nhan": {"type": "string",
                                              "enum": ["xoa"]}}}},
            ]
            if tk.trang_thai == "deactivated":
                tinh_nang.append(
                    {"hanh_dong": "giu_tai_khoan",
                     "mo_ta": "Giữ tài khoản đã bị vô hiệu hoá (bỏ chờ duyệt, "
                              "vẫn giữ trạng thái deactivated).",
                     "tham_so": {"type": "object",
                                 "additionalProperties": False,
                                 "properties": {}}})
            ra.append({
                "id": tk.id_thuc_the,
                "ten": _che_email(tk.email) or f"Tài khoản {tk.khoa}",
                "loai": "tai_khoan",
                "thuoc_tinh": {"plan": tk.plan},
                "tinh_nang": tinh_nang,
                "rui_ro_mac_dinh": "ghi",
            })
        return ra

    async def trang_thai(self, ids: list[str]) -> dict[str, dict]:
        with self._khoa:
            ban_do = {tk.id_thuc_the: tk for tk in self._tai_khoan}
        ra: dict[str, dict] = {}
        for id_ in ids or []:
            if id_ == f"{self.tien_to}.dang-nhap":
                ra[id_] = {"gia_tri": "san_sang", "don_vi": "",
                           "luc": _gio_utc(), "hop_le": True}
                continue
            tk = ban_do.get(id_)
            if tk is None:
                ra[id_] = {"gia_tri": "", "don_vi": "", "luc": _gio_utc(),
                           "hop_le": False}
            else:
                ra[id_] = {"gia_tri": tk.trang_thai, "don_vi": "",
                           "luc": _gio_utc(), "hop_le": True}
        return ra

    async def thuc_hien(self, id: str, hanh_dong: str,
                        tham_so: dict) -> dict:
        tham_so = tham_so or {}
        if id == f"{self.tien_to}.dang-nhap":
            if hanh_dong == "bat_dau_dang_nhap":
                return self._bat_dau_dang_nhap()
            if hanh_dong == "dan_callback":
                return await self._dan_callback(
                    str(tham_so.get("url_callback") or ""))
            return {"ok": False, "loi": f"hành động không hỗ trợ: {hanh_dong}"}
        tk = None
        with self._khoa:
            for u in self._tai_khoan:
                if u.id_thuc_the == id:
                    tk = u
                    break
        if tk is None:
            return {"ok": False, "loi": "không thấy tài khoản"}
        if hanh_dong == "lam_moi":
            return await self._hanh_dong_lam_moi(tk)
        if hanh_dong == "kiem_han_muc":
            return await self._hanh_dong_kiem_han_muc(tk)
        if hanh_dong == "giu_tai_khoan":
            return self._hanh_dong_giu(tk)
        if hanh_dong == "xoa_tai_khoan":
            if str(tham_so.get("xac_nhan") or "") != "xoa":
                return {"ok": False,
                        "loi": "cần tham số xac_nhan=\"xoa\" để xoá tài khoản"}
            return self._hanh_dong_xoa(tk)
        return {"ok": False, "loi": f"hành động không hỗ trợ: {hanh_dong}"}

    # -- hành động OAuth ------------------------------------------------------

    def _bat_dau_dang_nhap(self) -> dict:
        kq = self._cho_oauth.bat_dau()
        # Mở bộ nghe loopback để trình duyệt trên cùng máy gọi về được; tự
        # đóng sau khi xong/hết hạn. Máy ở xa thì dùng «dan_callback».
        if self._bo_nghe is not None:
            self._bo_nghe.dung()
        nghe = oauth.BoNgheCallback(self._cho_oauth, cong=1455)
        nghe.bat_dau()
        self._bo_nghe = nghe
        log.info("openaicodex: bắt đầu luồng OAuth (state đã cấp)")
        return {"ok": True, "trang_thai_moi": {
            "auth_url": kq["auth_url"], "state": kq["state"],
            "huong_dan": "Mở auth_url trong trình duyệt, đăng nhập; nếu trình "
                         "duyệt không gọi về được cổng 1455, chép URL callback "
                         "rồi dùng hành động dan_callback."}}

    async def _dan_callback(self, url_callback: str) -> dict:
        if not str(url_callback or "").strip():
            # Không dán tay: lấy (code, state) bộ nghe loopback đã ghi nhận.
            kq_nghe = (self._bo_nghe.lay_ket_qua(0)
                       if self._bo_nghe is not None else None)
            if not kq_nghe or not kq_nghe.get("ok"):
                return {"ok": False,
                        "loi": "chưa có callback nào về cổng 1455 — dán "
                               "url_callback thay thế"}
            code, state = kq_nghe["code"], kq_nghe["state"]
        else:
            try:
                code, state = oauth.trich_code_state(url_callback)
            except oauth.LoiOAuth as e:
                return {"ok": False,
                        "loi": f"URL callback không hợp lệ ({e.ma_loi})"}
        try:
            token = await self._cho_oauth.doi_ma(code, state, http=self._goi_http)
        except oauth.LoiOAuth as e:
            return {"ok": False,
                    "loi": f"đổi mã thất bại ({e.ma_loi}"
                           + (f", http {e.ma}" if e.ma else "") + ")"}
        tk = self._them_hoac_cap_nhat(token["access_token"],
                                      token["refresh_token"],
                                      float(token.get("expires_at") or 0))
        if self._bo_nghe is not None:
            self._bo_nghe.dung()
            self._bo_nghe = None
        log.info("openaicodex: thêm tài khoản %s qua OAuth", tk.khoa)
        return {"ok": True, "trang_thai_moi": {
            "tai_khoan_id": tk.id_thuc_the,
            "email_che": _che_email(tk.email),
            "co_refresh_token": bool(token["refresh_token"])}}

    async def _hanh_dong_lam_moi(self, tk: _TaiKhoan) -> dict:
        if not tk.refresh_token:
            return {"ok": False, "loi": "tài khoản không có refresh token"}
        # Hành động tường minh của quản trị: hỏi thẳng máy chủ, không cache.
        kq = await oauth.lam_moi_token(tk.refresh_token, tk.device_id or None,
                                       http=self._goi_http,
                                       tim_theo_refresh=self._tim_theo_refresh,
                                       bo_qua_cache=True)
        if kq and "error" not in kq:
            self._ap_dung_lam_moi(tk, kq)
            return {"ok": True, "trang_thai_moi": {"gia_tri": "hoat_dong"}}
        if kq and kq.get("error") == "unrecoverable":
            self._xu_ly_refresh_chet(tk, str(kq.get("code") or ""))
            return {"ok": False,
                    "loi": f"refresh token đã hết hiệu lực ({kq.get('code')})"}
        return {"ok": False, "loi": "làm mới lỗi tạm — thử lại sau"}

    async def _hanh_dong_kiem_han_muc(self, tk: _TaiKhoan) -> dict:
        ban = await han_muc.lam_moi(tk.access_token, http=self._goi_http,
                                     co_so=self._co_so_url or "")
        if ban is None:
            return {"ok": False, "loi": "chưa đọc được hạn mức"}
        return {"ok": True, "trang_thai_moi": {"han_muc": ban}}

    def _hanh_dong_giu(self, tk: _TaiKhoan) -> dict:
        if self.kho is not None:
            hoi_phuc.giai_quyet_cho_duyet(self.kho, tk.email, giu=True)
        return {"ok": True, "trang_thai_moi": {"gia_tri": tk.trang_thai}}

    def _hanh_dong_xoa(self, tk: _TaiKhoan) -> dict:
        with self._khoa:
            self._tai_khoan = [u for u in self._tai_khoan if u is not tk]
        if self.kho is not None:
            hoi_phuc.giai_quyet_cho_duyet(self.kho, tk.email, giu=False)
        self._luu_tai_khoan_kho()
        log.info("openaicodex: đã xoá tài khoản %s", tk.khoa)
        return {"ok": True, "trang_thai_moi": {"gia_tri": "da_xoa"}}

    # -- deactivated ------------------------------------------------------------

    def danh_dau_deactivated(self, tai_khoan_id: str, ly_do: str = "") -> None:
        """Đánh dấu tài khoản bị OpenAI vô hiệu hoá: ngừng thử lại + chờ duyệt."""
        tk = None
        with self._khoa:
            for u in self._tai_khoan:
                if u.id_thuc_the == tai_khoan_id:
                    tk = u
                    break
        if tk is None:
            return
        tk.trang_thai = "deactivated"
        self._luu_tai_khoan_kho()
        if self.kho is not None:
            hoi_phuc.danh_dau_deactivated(
                self.kho, tk.email, ly_do or "account_deactivated")

    def danh_sach_cho_duyet(self) -> list[dict]:
        if self.kho is None:
            return []
        return hoi_phuc.doc_cho_duyet(self.kho)

    # -- làm mới chủ động + hợp đồng khôi phục của lõi -------------------------

    def _can_lam_moi(self, tk: _TaiKhoan) -> bool:
        if not tk.refresh_token:
            return False
        if tk.expires_at:
            return tk.expires_at - time.time() <= NGUONG_LAM_MOI_GIAY
        claims = han_muc.claims_tu_token(tk.access_token)
        exp = int(claims.get("exp") or 0)
        if not exp:
            return False
        return exp - time.time() <= NGUONG_LAM_MOI_GIAY

    def _ap_dung_lam_moi(self, tk: _TaiKhoan, kq: dict,
                         giu_so_401: bool = False) -> None:
        """Áp token mới. `giu_so_401=True` (đường 401 của chat): làm mới KHÔNG
        chứng minh token chạy được nên không xoá bộ đếm 401 — chỉ lần gọi 200
        mới xoá. Nếu xoá ở đây thì 401 lặp qua các lượt chat không bao giờ chạm
        ngưỡng vô hiệu."""
        with self._khoa:
            tk.access_token = str(kq.get("access_token") or tk.access_token)
            moi = str(kq.get("refresh_token") or "")
            if moi:
                tk.refresh_token = moi
            if kq.get("expires_at"):
                tk.expires_at = float(kq["expires_at"])
            tk.trang_thai = "hoat_dong"
            if not giu_so_401:
                tk.so_401 = 0
            tk.restore_at = None
        self._luu_tai_khoan_kho()

    def _xu_ly_refresh_chet(self, tk: _TaiKhoan, code: str) -> None:
        """refresh_token hết hiệu lực: account_deactivated → deactivated +
        chờ duyệt; mã khác → vo_hieu."""
        if code == "account_deactivated":
            self.danh_dau_deactivated(tk.id_thuc_the, code)
            return
        tk.trang_thai = "vo_hieu"
        self._luu_tai_khoan_kho()
        log.warning("openaicodex: tài khoản %s vô hiệu (refresh: %s)",
                    tk.khoa, code or "không rõ")

    async def _lam_moi_tai_khoan(self, tk: _TaiKhoan, ep: bool = False,
                                 giu_so_401: bool = False) -> dict | None:
        """Làm mới một tài khoản; phân loại kết quả để người gọi quyết định:
        {"ok": True} | {"loai": "tam"|"chet", "code": ...}.
        `ep=True` (đường 401): bỏ qua cache — token vừa bị từ chối."""
        if not tk.refresh_token:
            return {"loai": "chet", "code": "thieu_refresh_token"}
        kq = await oauth.lam_moi_token(tk.refresh_token, tk.device_id or None,
                                       http=self._goi_http,
                                       tim_theo_refresh=self._tim_theo_refresh,
                                       bo_qua_cache=ep)
        if kq and "error" not in kq:
            self._ap_dung_lam_moi(tk, kq, giu_so_401=giu_so_401)
            return {"ok": True}
        if kq and kq.get("error") == "unrecoverable":
            return {"loai": "chet", "code": str(kq.get("code") or "")}
        return {"loai": "tam", "code": ""}

    async def lam_moi_phien(self, khoa: str) -> KetQuaKhoiPhuc:
        """Tầng (a) của lõi: làm mới token của tài khoản theo khoá mờ.

        Lõi mạng tạm → KHÔNG đánh dấu hỏng (trả hỏng để lõi thử tầng khác/
        lượt sau); refresh chết → đánh dấu vo_hieu/deactivated.
        """
        tk = self._tim_tai_khoan(str(khoa))
        if tk is None:
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="khoa_khong_co")
        if tk.trang_thai == "deactivated":
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="deactivated")
        if not self._can_lam_moi(tk):
            return KetQuaKhoiPhuc(True, tang="a")
        kq = await self._lam_moi_tai_khoan(tk)
        if kq.get("ok"):
            return KetQuaKhoiPhuc(True, tang="a")
        if kq.get("loai") == "chet":
            self._xu_ly_refresh_chet(tk, str(kq.get("code") or ""))
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="refresh_het_han",
                                  ma_trang_thai=401)
        return KetQuaKhoiPhuc(False, tang="a", ma_loi="loi_mang")

    def khoa_tai_khoan(self) -> list[str]:
        """Khoá mờ mọi tài khoản (vòng làm mới định kỳ của lõi liệt kê)."""
        with self._khoa:
            return [tk.khoa for tk in self._tai_khoan]

    def danh_dau_khoe(self, khoa: str) -> None:
        tk = self._tim_tai_khoan(str(khoa))
        if tk is not None and tk.trang_thai != "deactivated":
            tk.so_401 = 0

    def danh_dau_hong(self, khoa: str, ma_loi: str = "") -> None:
        tk = self._tim_tai_khoan(str(khoa))
        if tk is not None:
            tk.trang_thai = "vo_hieu"
            self._luu_tai_khoan_kho()

    def tai_khoan_ke(self, khoa: str) -> str | None:
        """Khoá tài khoản kế còn dùng được (tầng chuyển tài khoản của lõi)."""
        with self._khoa:
            for tk in self._tai_khoan:
                if tk.khoa != str(khoa) and tk.trang_thai == "hoat_dong":
                    return tk.khoa
        return None

    def phan_loai_loi(self, ma: int, loi: dict, boi_canh: dict) -> str | None:
        """Phân loại lỗi thượng nguồn theo CẤU TRÚC mã trạng thái."""
        if ma == 429:
            return "het_han_muc"
        if ma in (401, 403):
            return "khoa_hong"
        if 500 <= ma <= 599:
            return "may_chu_loi"
        return None

    # -- chat qua Codex Responses API (port openai_oauth.py) ------------------

    def _chon_tai_khoan(self, da_thu: set[str]) -> _TaiKhoan | None:
        """Chọn tài khoản cho một lượt gọi: bỏ qua tài khoản (theo MÃ ỔN ĐỊNH,
        không theo `khoa` vốn xoay khi làm mới) đã thử trong lượt này, đã vô hiệu/deactivated, đang giới hạn chưa tới
        mốc hồi phục, và token API-only (không có claim ChatGPT)."""
        bay_gio = time.time()
        with self._khoa:
            ds = list(self._tai_khoan)
        for tk in ds:
            if tk.ma_on_dinh in da_thu:
                continue
            if tk.trang_thai in ("vo_hieu", "deactivated"):
                continue
            if tk.trang_thai == "gioi_han":
                if tk.restore_at:
                    try:
                        moc = datetime.fromisoformat(
                            tk.restore_at.replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        moc = 0
                    if moc > bay_gio:
                        continue
                    tk.trang_thai = "hoat_dong"
                else:
                    continue
            if _la_token_api_only(tk.access_token):
                continue
            return tk
        return None

    def _dem_401(self, tk: _TaiKhoan, ly_do: str) -> bool:
        """Đếm 401 liên tiếp: đạt ngưỡng thì vô hiệu. Trả True nếu vừa vô hiệu."""
        tk.so_401 += 1
        if tk.so_401 >= NGUONG_401_VO_HIEU:
            tk.trang_thai = "vo_hieu"
            tk.so_401 = 0
            self._luu_tai_khoan_kho()
            log.warning("openaicodex: vô hiệu tài khoản %s sau %d lần 401 "
                        "(%s)", tk.khoa, NGUONG_401_VO_HIEU, ly_do)
            return True
        self._luu_tai_khoan_kho()
        return False

    def _danh_dau_gioi_han(self, tk: _TaiKhoan, headers: dict,
                           than_json: dict | None) -> None:
        """429 hạn mức gói: KHÔNG làm mới token (vô ích — port nhận xét đo thật
        của c2a); ghi mốc hồi phục từ header/body và hạ xuống cuối hàng."""
        restore_iso = None
        reset_at = str((headers or {}).get("x-codex-primary-reset-at") or "")
        if reset_at.strip().isdigit():
            restore_iso = datetime.fromtimestamp(
                int(reset_at), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not restore_iso and isinstance(than_json, dict):
            err = than_json.get("error") or {}
            resets = int(err.get("resets_at") or 0) if isinstance(err, dict) else 0
            if resets > 0:
                restore_iso = datetime.fromtimestamp(
                    resets, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        tk.trang_thai = "gioi_han"
        tk.restore_at = restore_iso
        with self._khoa:
            if tk in self._tai_khoan:
                self._tai_khoan.remove(tk)
                self._tai_khoan.append(tk)
        self._luu_tai_khoan_kho()
        log.warning("openaicodex: tài khoản %s chạm hạn mức (hồi phục: %s)",
                    tk.khoa, restore_iso or "chưa rõ")

    async def chat(self, body: dict, stream: bool):
        body = body or {}
        ten_model = str(body.get("model") or "")
        if "/" in ten_model:
            ten_model = ten_model.split("/", 1)[1]
        if not ten_model or ten_model == "auto":
            ten_model = DANH_SACH_MODEL[0]
        if ten_model not in DANH_SACH_MODEL:
            return (400, {"content-type": "application/json"},
                    json.dumps(than_loi(
                        400, f"model không hỗ trợ: {ten_model}",
                        code="model_not_found")).encode())
        tin_nhan = body.get("messages")
        if not isinstance(tin_nhan, list) or not tin_nhan:
            return (400, {"content-type": "application/json"},
                    json.dumps(than_loi(400, "thiếu messages")).encode())

        than_responses = _chat_sang_responses(tin_nhan, body)
        than_responses["model"] = ten_model
        # Khoá của hai tập dưới là `ma_on_dinh` (KHÔNG phải `khoa`: `khoa` =
        # sha256(access_token) đổi sau mỗi lần làm mới thành công).
        da_thu: set[str] = set()       # tài khoản đã thử trong lượt gọi này
        da_lam_moi: set[str] = set()   # tài khoản đã làm mới trong lượt này
        so_lam_moi = 0                 # tổng lần làm mới trong lượt (trần cứng)
        loi_cuoi: tuple[int, bytes] | None = None

        while True:
            tk = self._chon_tai_khoan(da_thu)
            if tk is None:
                break
            ma, su_kien, headers = await self._goi_codex(tk, than_responses)
            han_muc.ghi_nhan_tu_header(tk.access_token, headers)
            if ma == 200:
                tk.so_401 = 0
                tk.lan_dung_cuoi = _gio_utc()
                if stream:
                    return (200, {"content-type": "text/event-stream"},
                            _luong_chat_sse(su_kien, ten_model))
                return (200, {"content-type": "application/json"},
                        _gop_chat_json(su_kien, ten_model, tin_nhan))
            than_loi_bytes = su_kien if isinstance(su_kien, bytes) else b""
            if ma == 401:
                # Làm mới tối đa TRAN_LAM_MOI_MOI_LUOT lần cho CẢ lượt, rồi thử
                # lại đúng tài khoản đó (chưa vào da_thu).
                if tk.ma_on_dinh in da_lam_moi:
                    # Vừa làm mới trong lượt này mà vẫn 401: đếm ĐÚNG MỘT lần
                    # vào ngưỡng vô hiệu rồi trả 401 (không làm mới nữa).
                    self._dem_401(tk, "401_sau_lam_moi")
                elif so_lam_moi < TRAN_LAM_MOI_MOI_LUOT:
                    so_lam_moi += 1
                    da_lam_moi.add(tk.ma_on_dinh)
                    kq = await self._lam_moi_tai_khoan(
                        tk, ep=True, giu_so_401=True)
                    if kq.get("ok"):
                        continue
                    self._dem_401(tk, "401_khong_lam_moi_duoc")
                # else: hết trần làm mới của lượt (tài khoản này chưa được
                # làm mới): token có thể chỉ hết hạn → KHÔNG đếm 401, chuyển
                # tài khoản kế; lượt sau tự làm mới.
                loi_cuoi = (401, than_loi_bytes)
            elif ma == 403:
                tk.trang_thai = "vo_hieu"
                self._luu_tai_khoan_kho()
                log.warning("openaicodex: vô hiệu tài khoản %s (403)",
                            tk.khoa)
                loi_cuoi = (403, than_loi_bytes)
            elif ma == 429:
                than_json = None
                try:
                    than_json = json.loads(than_loi_bytes.decode("utf-8"))
                except Exception:
                    than_json = None
                self._danh_dau_gioi_han(tk, headers, than_json)
                loi_cuoi = (429, than_loi_bytes)
            else:
                # 5xx: thử tài khoản kế; 4xx khác (yêu cầu sai): trả ngay.
                loi_cuoi = (ma, than_loi_bytes)
                if ma < 500:
                    da_thu.add(tk.ma_on_dinh)
                    break
            da_thu.add(tk.ma_on_dinh)

        if loi_cuoi is not None:
            ma, than = loi_cuoi
            return (ma, {"content-type": "application/json"},
                    than or json.dumps(than_loi(ma, "lỗi Codex")).encode())
        return (503, {"content-type": "application/json"},
                json.dumps(than_loi(
                    503, "chưa có tài khoản Codex dùng được — đăng nhập qua "
                         "hành động thiết bị «bat_dau_dang_nhap»",
                    code="chua_co_tai_khoan")).encode())

    async def _goi_codex(self, tk: _TaiKhoan, than: dict):
        """Một lượt gọi Codex Responses API. Trả (mã, các dòng SSE | bytes lỗi,
        headers). Thân lỗi KHÔNG đi tiếp ra ngoài nguyên văn nếu chứa bí mật —
        lỗi trả khách được chuẩn hoá ở chat()."""
        url = f"{self._co_so_url or MAY_CHU_MAC_DINH}{DUONG_CODEX}"
        headers = dict(TIEU_DE_CODEX)
        headers["Authorization"] = f"Bearer {tk.access_token}"
        tt = han_muc.thong_tin_token(tk.access_token)
        if tt["account_id"]:
            headers["ChatGPT-Account-Id"] = tt["account_id"]
        try:
            async with self._client(timeout=300) as cli:
                async with cli.stream("POST", url, headers=headers,
                                      json=than) as phan_hoi:
                    if phan_hoi.status_code != 200:
                        than_loi = await phan_hoi.aread()
                        return (phan_hoi.status_code,
                                _than_loi_chuan(phan_hoi.status_code, than_loi),
                                dict(phan_hoi.headers))
                    cac_dong = []
                    async for dong in phan_hoi.aiter_lines():
                        cac_dong.append(dong)
                    return (200, cac_dong, dict(phan_hoi.headers))
        except Exception as exc:
            log.warning("openaicodex: gọi Codex lỗi mạng: %s",
                        type(exc).__name__)
            return (502, json.dumps(than_loi(
                502, "không nối được Codex")).encode(), {})


def _than_loi_chuan(ma: int, than: bytes) -> bytes:
    """Chuẩn hoá thân lỗi thượng nguồn: chỉ giữ mã + thông điệp ngắn đã làm
    sạch của lõi (không chuyển tiếp nguyên văn — có thể chứa dữ liệu lạ)."""
    thong_diep = "lỗi từ Codex"
    try:
        du_lieu = json.loads((than or b"").decode("utf-8", "ignore"))
        err = du_lieu.get("error") if isinstance(du_lieu, dict) else None
        if isinstance(err, dict) and err.get("message"):
            thong_diep = str(err["message"])[:200]
    except Exception:
        pass
    return json.dumps(than_loi(ma, thong_diep)).encode()


# ── Đổi định dạng chat ↔ Responses (port _chat_to_responses_input và
#    _responses_to_chat_chunk của c2a; bỏ các helper riêng cho Home Assistant).

def _chat_sang_responses(tin_nhan: list[dict], body: dict) -> dict:
    ra: dict[str, Any] = {"stream": True, "store": False}
    muc: list[dict] = []
    chi_dan = str(body.get("instructions") or "")
    for tn in tin_nhan:
        if not isinstance(tn, dict):
            continue
        vai = str(tn.get("role") or "user")
        noi_dung = tn.get("content", "")
        if isinstance(noi_dung, list):
            text = " ".join(str(p.get("text", "")) for p in noi_dung
                            if isinstance(p, dict) and p.get("type") == "text")
            anh = [p for p in noi_dung
                   if isinstance(p, dict)
                   and p.get("type") in ("image_url", "input_image")]
            if anh:
                cac_muc = []
                if text:
                    cac_muc.append({"type": "input_text", "text": text})
                for p in anh:
                    url = (p.get("image_url") or {}).get("url", "") \
                        if p.get("type") == "image_url" else p.get("image_url", "")
                    if url:
                        cac_muc.append({"type": "input_image", "image_url": url})
                muc.append({"role": "user", "content": cac_muc})
                continue
            noi_dung = text
        noi_dung = str(noi_dung or "")
        if vai == "system":
            chi_dan = (chi_dan + "\n" + noi_dung) if chi_dan else noi_dung
            continue
        if vai == "tool":
            muc.append({"type": "function_call_output",
                        "call_id": str(tn.get("tool_call_id") or ""),
                        "output": noi_dung})
            continue
        if vai == "assistant":
            cac_goi = tn.get("tool_calls")
            if isinstance(cac_goi, list) and cac_goi:
                if noi_dung.strip():
                    muc.append({"role": "assistant", "content": noi_dung})
                for g in cac_goi:
                    if isinstance(g, dict):
                        fn = g.get("function") or {}
                        muc.append({"type": "function_call",
                                    "call_id": str(g.get("id") or ""),
                                    "name": str(fn.get("name") or ""),
                                    "arguments": str(fn.get("arguments") or "")})
                continue
            muc.append({"role": "assistant", "content": noi_dung})
            continue
        muc.append({"role": "user", "content": noi_dung})
    ra["input"] = muc
    ra["instructions"] = chi_dan.strip() or "You are a helpful assistant."
    cong_cu = body.get("tools")
    if isinstance(cong_cu, list) and cong_cu:
        ra["tools"] = [{
            "type": "function",
            "name": (t.get("function") or {}).get("name", ""),
            "description": (t.get("function") or {}).get("description", ""),
            "parameters": (t.get("function") or {}).get("parameters", {}),
        } for t in cong_cu if isinstance(t, dict)]
    if body.get("tool_choice"):
        ra["tool_choice"] = body["tool_choice"]
    return ra


def _su_kien_sang_chunk(su_kien: dict, model: str, ma_hoi: str,
                        luc_tao: int) -> dict | None:
    loai = su_kien.get("type", "")
    if loai == "response.output_text.delta":
        return {"id": ma_hoi, "object": "chat.completion.chunk",
                "created": luc_tao, "model": model,
                "choices": [{"index": 0,
                             "delta": {"content": su_kien.get("delta", "")},
                             "finish_reason": None}]}
    if loai == "response.output_item.added":
        muc = su_kien.get("item") or {}
        if muc.get("type") == "function_call":
            return {"id": ma_hoi, "object": "chat.completion.chunk",
                    "created": luc_tao, "model": model,
                    "choices": [{"index": 0, "delta": {"tool_calls": [{
                        "index": 0, "id": muc.get("call_id", ""),
                        "type": "function",
                        "function": {"name": muc.get("name", ""),
                                     "arguments": ""}}]},
                        "finish_reason": None}]}
        return None
    if loai == "response.output_item.done":
        muc = su_kien.get("item") or {}
        if muc.get("type") == "function_call":
            # Sự kiện done mang ĐỦ arguments (delta bị lệch tên ở thượng nguồn
            # từng làm mất args — port nhận xét đo thật của c2a).
            return {"id": ma_hoi, "object": "chat.completion.chunk",
                    "created": luc_tao, "model": model,
                    "choices": [{"index": 0, "delta": {"tool_calls": [{
                        "index": 0, "id": muc.get("call_id", ""),
                        "type": "function",
                        "function": {"arguments": muc.get("arguments", "")}}]},
                        "finish_reason": None}]}
        return None
    if loai == "response.completed":
        return {"id": ma_hoi, "object": "chat.completion.chunk",
                "created": luc_tao, "model": model,
                "choices": [{"index": 0, "delta": {},
                             "finish_reason": "stop"}]}
    if loai == "error":
        return {"id": ma_hoi, "object": "chat.completion.chunk",
                "created": luc_tao, "model": model,
                "choices": [{"index": 0, "delta": {
                    "content": f"Codex error: {su_kien.get('message', 'unknown')}"},
                    "finish_reason": "stop"}]}
    return None


def _doc_cac_su_kien(cac_dong) -> list[dict]:
    ra = []
    for dong in cac_dong or []:
        dong = str(dong).strip()
        if not dong.startswith("data:"):
            continue
        tai = dong[5:].strip()
        if not tai or tai == "[DONE]":
            continue
        try:
            sk = json.loads(tai)
        except Exception:
            continue
        if isinstance(sk, dict):
            ra.append(sk)
    return ra


async def _luong_chat_sse(cac_dong, model: str):
    """Phát SSE chat chunks từ các dòng Responses đã nhận."""
    ma_hoi = f"chatcmpl-{uuid.uuid4().hex}"
    luc_tao = int(time.time())
    da_gui_vai = False
    co_goi_cong_cu = False
    for sk in _doc_cac_su_kien(cac_dong):
        chunk = _su_kien_sang_chunk(sk, model, ma_hoi, luc_tao)
        if not chunk:
            continue
        delta = chunk["choices"][0]["delta"]
        if not da_gui_vai and (delta.get("content") is not None
                               or delta.get("tool_calls") is not None):
            delta["role"] = "assistant"
            da_gui_vai = True
        if delta.get("tool_calls"):
            co_goi_cong_cu = True
        if chunk["choices"][0].get("finish_reason") == "stop" and co_goi_cong_cu:
            chunk["choices"][0]["finish_reason"] = "tool_calls"
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
    yield b"data: [DONE]\n\n"


def _gop_chat_json(cac_dong, model: str, tin_nhan: list) -> bytes:
    """Gộp SSE thành một chat.completion (đường không stream)."""
    ma_hoi = f"chatcmpl-{uuid.uuid4().hex}"
    luc_tao = int(time.time())
    text = ""
    cac_goi: list[dict] = []
    for sk in _doc_cac_su_kien(cac_dong):
        chunk = _su_kien_sang_chunk(sk, model, ma_hoi, luc_tao)
        if not chunk:
            continue
        delta = chunk["choices"][0]["delta"]
        text += delta.get("content", "") or ""
        for phan in (delta.get("tool_calls") or []):
            fn = phan.get("function") or {}
            if fn.get("name"):
                cac_goi.append({"id": phan.get("id", ""), "type": "function",
                                "function": {"name": fn.get("name", ""),
                                             "arguments": fn.get("arguments", "") or ""}})
            elif cac_goi:
                if fn.get("arguments"):
                    cac_goi[-1]["function"]["arguments"] += fn["arguments"]
                if phan.get("id") and not cac_goi[-1].get("id"):
                    cac_goi[-1]["id"] = phan["id"]
    tin = {"role": "assistant", "content": text}
    if cac_goi:
        tin["tool_calls"] = cac_goi
    return json.dumps({
        "id": ma_hoi, "object": "chat.completion", "created": luc_tao,
        "model": model,
        "choices": [{"index": 0, "message": tin,
                     "finish_reason": "tool_calls" if cac_goi else "stop"}],
    }, ensure_ascii=False).encode()
