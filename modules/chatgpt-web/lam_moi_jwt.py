"""Tự làm mới JWT chatgpt-web khi gần hết hạn, qua dịch vụ may-dang-nhap (NV207).

Ý tưởng học từ c2a `services/jwt_refresh_scheduler.py` (chỉ đọc lấy hiểu, tự
viết lại cho kiến trúc web2api): vòng nền kiểm hạn JWT của từng khoá bằng GIẢI
MÃ CLAIM `exp` (không gọi mạng); chỉ khi còn dưới ngưỡng (mặc định 2 ngày) mới
gọi dịch vụ `GET /v1/chatgpt/{profile}/refresh-jwt` cho đúng hồ sơ rồi thay
token cũ bằng token mới qua đường `cai` {giu, them} trong khoá sổ.

Ghép khoá → hồ sơ KHÔNG đoán chuỗi:
  1. `ho_so` đã lưu trong kho riêng (NV93 tái dùng hồ sơ) và còn trong
     `GET /v1/profiles` → dùng ngay (chắc chắn);
  2. ngược lại → đánh dấu «cần người», KHÔNG làm mới bừa.

An toàn bí mật: token không vào log/phản hồi/nhật ký; nhật ký chỉ ghi
«đã làm mới khoá …XXXX» (4 ký tự cuối của khoá mờ).
"""

from __future__ import annotations

import base64
import importlib.util
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("web2api.module.chatgpt-web.lam-moi-jwt")

#: Ngưỡng mặc định: còn dưới 2 ngày thì làm mới (giống spec NV207).
NGUONG_NGAY_MAC_DINH = 2.0
#: Trần thử mặc định: tối đa 5 lần/ngày/khoá.
TRAN_THU_NGAY_MAC_DINH = 5
#: Nghỉ luỹ thừa sau lỗi tạm thời: 300 * 2^loi_lien_tiep giây, trần 6 giờ.
NGHI_LUY_THUA_CO_SO_GIAY = 300.0
NGHI_LUY_THUA_TRAN_GIAY = 6 * 3600.0

_CLAIM_HO_SO = "https://api.openai.com/profile"


# ---------------------------------------------------------------------------
# Giải mã JWT (không xác thực chữ ký — chỉ đọc claim exp/email của token mà
# chính chủ máy đã dán vào; không gọi mạng).
# ---------------------------------------------------------------------------

def giai_ma_payload(token: str) -> dict[str, Any] | None:
    """Payload của JWT (đoạn giữa, base64url); hỏng → None."""
    try:
        _, payload_b64, _ = str(token).split(".", 2)
    except ValueError:
        return None
    pad = "=" * (-len(payload_b64) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload_b64 + pad)
        du_lieu = json.loads(raw)
    except Exception:
        return None
    return du_lieu if isinstance(du_lieu, dict) else None


def phan_loai_exp(token: str) -> tuple[str, int | None]:
    """Phân loại claim exp theo cấu trúc (luật thống nhất 23/10/2026):
      - 'hong': payload không giải mã được hoặc có exp nhưng sai kiểu (bool, str, <=0)
      - 'thieu': payload JSON hợp lệ nhưng không có khoá 'exp' (dùng được)
      - 'co': có exp là số nguyên dương -> ('co', exp)
    """
    payload = giai_ma_payload(token)
    if payload is None:
        return ("hong", None)
    if "exp" not in payload:
        return ("thieu", None)
    exp = payload.get("exp")
    if isinstance(exp, bool):
        return ("hong", None)
    try:
        exp_int = int(exp)
    except (TypeError, ValueError):
        return ("hong", None)
    if exp_int <= 0:
        return ("hong", None)
    return ("co", exp_int)


def exp_cua(token: str) -> int | None:
    """Claim `exp` (giây epoch); thiếu/không phải số nguyên dương → None."""
    loai, exp = phan_loai_exp(token)
    return exp if loai == "co" else None


def email_cua(token: str) -> str:
    """Email trong claim `https://api.openai.com/profile`; không có → ""."""
    payload = giai_ma_payload(token)
    if payload is None:
        return ""
    ho_so = payload.get(_CLAIM_HO_SO)
    if isinstance(ho_so, dict):
        email = ho_so.get("email")
        if isinstance(email, str):
            return email.strip()
    email = payload.get("email")
    return email.strip() if isinstance(email, str) else ""


def het_han_iso(exp: int | None) -> str | None:
    if exp is None:
        return None
    return datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def con_lai_ngay(exp: int | None, bay_gio: float | None = None) -> float | None:
    if exp is None:
        return None
    return (exp - (bay_gio if bay_gio is not None else time.time())) / 86400.0


# ---------------------------------------------------------------------------
# Client dịch vụ may-dang-nhap (nạp lười qua importlib — module web2api không
# phải package nên không import tuyệt đối được; chỉ dùng phương thức CÔNG KHAI).
# ---------------------------------------------------------------------------

_MDN_MOD: Any = None


def _tai_may_dang_nhap() -> Any:
    """Nạp modules/may-dang-nhap/module.py (cache)."""
    global _MDN_MOD
    if _MDN_MOD is None:
        p = Path(__file__).resolve().parent.parent / "may-dang-nhap" / "module.py"
        spec = importlib.util.spec_from_file_location(
            "web2api_may_dang_nhap_client", str(p))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MDN_MOD = mod
    return _MDN_MOD


def _tao_client_dich_vu_mac_dinh(url: str, khoa: str) -> Any:
    """Thể hiện client may-dang-nhap đã nap() — dùng đúng đường gọi HTTP có kiểm
    SSRF của module đó (không viết bộ kiểm thứ hai)."""
    mod = _tai_may_dang_nhap()
    client = mod.Module({"tien_to": "may-dang-nhap"})
    client.nap({"dich_vu_url": url, "khoa_dich_vu": khoa})
    return client


# ---------------------------------------------------------------------------
# Bộ làm mới JWT gắn với một thể hiện Module chatgpt-web.
# ---------------------------------------------------------------------------

class BoLamMoiJWT:
    """Thực hiện `lam_moi(khoa)` cho từng tài khoản chatgpt-web.

    `tao_client_dich_vu`: mối nối để test chích — () -> client có hai phương
    thức công khai `danh_sach_ho_so()` và `lam_moi_jwt(ho_so)` (như module
    may-dang-nhap). Mặc định: tạo từ cấu hình dịch vụ của module.
    """

    def __init__(self, module: Any, *,
                 tao_client_dich_vu: Any | None = None) -> None:
        self._module = module
        self._tao_client_dich_vu = tao_client_dich_vu

    # -- cấu hình ------------------------------------------------------

    def _nguong_ngay(self) -> float:
        try:
            v = float(getattr(self._module, "_jwt_nguong_ngay", NGUONG_NGAY_MAC_DINH))
        except (TypeError, ValueError):
            v = NGUONG_NGAY_MAC_DINH
        return v if v > 0 else NGUONG_NGAY_MAC_DINH

    def _tran_thu_ngay(self) -> int:
        try:
            v = int(float(getattr(self._module, "_jwt_tran_thu_ngay",
                                  TRAN_THU_NGAY_MAC_DINH)))
        except (TypeError, ValueError):
            v = TRAN_THU_NGAY_MAC_DINH
        return v if v > 0 else TRAN_THU_NGAY_MAC_DINH

    def _lay_client(self) -> Any | None:
        if self._tao_client_dich_vu is not None:
            return self._tao_client_dich_vu()
        url = str(getattr(self._module, "_dich_vu_dang_nhap_url", "") or "").strip()
        if not url:
            return None
        khoa = str(getattr(self._module, "_khoa_dich_vu_dang_nhap", "") or "")
        return _tao_client_dich_vu_mac_dinh(url, khoa)

    # -- sổ trạng thái (kho riêng, không bí mật) ------------------------

    def _doc_trang_thai(self) -> dict:
        doc = getattr(self._module, "_doc_trang_thai_jwt", None)
        if callable(doc):
            try:
                tt = doc()
            except Exception:
                return {}
            return tt if isinstance(tt, dict) else {}
        return {}

    def _luu_trang_thai(self, tt: dict) -> None:
        luu = getattr(self._module, "_luu_trang_thai_jwt", None)
        if callable(luu):
            try:
                luu(tt)
            except Exception as e:
                log.warning("lam-moi-jwt: không lưu được trạng thái: %s",
                            type(e).__name__)

    def _cap_nhat_trang_thai(self, khoa: str, **truong) -> dict:
        tt = self._doc_trang_thai()
        muc = tt.get(khoa) if isinstance(tt.get(khoa), dict) else {}
        muc = dict(muc)
        muc.update(truong)
        tt[khoa] = muc
        self._luu_trang_thai(tt)
        return muc

    # -- đánh dấu cần người ----------------------------------------------

    def _danh_dau_can_nguoi(self, khoa: str, ma_loi: str, chi_tiet: str) -> None:
        try:
            from app import khoi_phuc_phien as kp
            kp.dat_can_nguoi("chatgpt-web", khoa, "lam_moi_jwt", chi_tiet)
        except Exception as e:
            log.warning("lam-moi-jwt: không đánh dấu can_nguoi được: %s",
                        type(e).__name__)
        try:
            from app import nhat_ky
            nhat_ky.ghi("quan_tri", "canh_bao",
                        f"chatgpt-web: khoá {khoa} cần người xử lý "
                        f"(làm mới JWT: {chi_tiet}).",
                        module="chatgpt-web")
        except Exception as e:
            log.warning("lam-moi-jwt: không ghi nhật ký được: %s",
                        type(e).__name__)
        self._cap_nhat_trang_thai(khoa, trang_thai="can_nguoi", ma_loi=ma_loi)

    # -- ghép khoá → hồ sơ (KHÔNG đoán chuỗi) -----------------------------

    async def _tim_ho_so(self, client: Any, tk: Any,
                         ho_so_da_luu: list[str]) -> str | None:
        """Hồ sơ chắc chắn cho tài khoản, hoặc None.

        Chỉ dùng `ho_so` đã lưu trong kho riêng (NV93) và còn tồn tại trong
        `GET /v1/profiles`. Không suy từ email, không khớp chuỗi.
        """
        hs = str(getattr(tk, "ho_so", "") or "").strip()
        if hs and hs in ho_so_da_luu:
            return hs
        return None

    # -- thay token qua đường cai {giu, them} -----------------------------

    def _thay_token(self, khoa_cu: str, token_moi: str) -> dict:
        thay = getattr(self._module, "_thay_token_jwt", None)
        if not callable(thay):
            return {"ok": False, "ma_loi": "thieu_ho_tro_thay_token"}
        try:
            return thay(khoa_cu, token_moi)
        except Exception as e:
            log.warning("lam-moi-jwt: thay token lỗi: %s", type(e).__name__)
            return {"ok": False, "ma_loi": "loi_thay_token"}

    # -- một lượt làm mới -------------------------------------------------

    async def lam_moi(self, khoa: str) -> dict:
        """Làm mới JWT cho một khoá. Không bao giờ ném (lỗi → mã trong kết quả).

        Mã (`ma`): khoa_khong_co | exp_khong_doc_duoc | khong_co_exp | chua_toi_nguong |
        chua_cau_hinh_dich_vu | dang_chay | khong_ghep_duoc_ho_so |
        het_tran_ngay | dang_nghi_luy_thua | da_lam_moi | token_moi_giong_cu |
        ho_so_dang_xuat | can_nguoi (khoá đã dừng vĩnh viễn vì 401) |
        loi_dich_vu | token_da_doi
        """
        module = self._module
        khoa = str(khoa or "")
        tim = getattr(module, "_tim__tai_khoan_obj", None)
        tk = tim(khoa) if callable(tim) else None
        if tk is None:
            return {"ok": False, "khoa": khoa, "ma": "khoa_khong_co"}

        bay_gio = time.time()
        loai_exp, exp = phan_loai_exp(tk.token)
        con_lai = con_lai_ngay(exp, bay_gio)
        self._cap_nhat_trang_thai(
            khoa, het_han=het_han_iso(exp), con_lai_ngay=con_lai,
            lan_kiem=datetime.fromtimestamp(bay_gio, tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"))
        # NV207: 401 hồ sơ đăng xuất là VĨNH VIỄN — không gọi lại dịch vụ cho
        # khoá này nữa («KHÔNG thử lại mãi»). Các can_nguoi khác
        # (khong_ghep_duoc_ho_so) vẫn thử lại vòng sau.
        tt_truoc = self._doc_trang_thai().get(khoa)
        if isinstance(tt_truoc, dict) \
                and tt_truoc.get("trang_thai") == "can_nguoi" \
                and tt_truoc.get("ma_loi") == "ho_so_dang_xuat":
            return {"ok": False, "khoa": khoa, "ma": "can_nguoi",
                    "can_nguoi": True}
        if loai_exp == "thieu":
            # NV271: Token hợp lệ nhưng không có claim exp -> dùng được,
            # không biết hạn, không cần làm mới, không gọi mạng, không cần người.
            self._cap_nhat_trang_thai(khoa, trang_thai="khong_co_exp", ma_loi=None)
            return {"ok": True, "khoa": khoa, "ma": "khong_co_exp"}
        if loai_exp == "hong":
            # Không đọc được exp hoặc sai kiểu → không dám làm mới bừa.
            self._danh_dau_can_nguoi(
                khoa, "exp_khong_doc_duoc",
                "không giải mã được claim exp của JWT — dán lại token")
            log.warning("lam-moi-jwt: khoá %s không đọc được exp", khoa)
            return {"ok": False, "khoa": khoa, "ma": "exp_khong_doc_duoc",
                    "can_nguoi": True}
        if con_lai is not None and con_lai > self._nguong_ngay():
            # Chưa tới ngưỡng: KHÔNG gọi mạng (đúng nguyên tắc 1).
            self._cap_nhat_trang_thai(khoa, trang_thai="chua_toi_nguong",
                                      ma_loi=None)
            return {"ok": True, "khoa": khoa, "ma": "chua_toi_nguong",
                    "con_lai_ngay": con_lai, "het_han": het_han_iso(exp)}

        client = self._lay_client()
        if client is None:
            self._cap_nhat_trang_thai(khoa, trang_thai="chua_cau_hinh_dich_vu")
            return {"ok": True, "khoa": khoa, "ma": "chua_cau_hinh_dich_vu"}

        # Single-flight trong thể hiện: hai lượt cùng khoá không đè nhau.
        khoa_chay = getattr(module, "_khoa_lam_moi_jwt", None)
        if not isinstance(khoa_chay, dict):
            return {"ok": False, "khoa": khoa, "ma": "loi_noi_bo"}
        import asyncio as _asyncio
        with module._khoa:
            lock = khoa_chay.get(khoa)
            if lock is None:
                lock = _asyncio.Lock()
                khoa_chay[khoa] = lock
        if lock.locked():
            return {"ok": True, "khoa": khoa, "ma": "dang_chay"}
        async with lock:
            return await self._lam_moi_co_khoa(client, tk, khoa, exp,
                                               con_lai, bay_gio)

    async def _lam_moi_co_khoa(self, client: Any, tk: Any, khoa: str,
                               exp: int, con_lai: float | None,
                               bay_gio: float) -> dict:
        # Trần thử mỗi ngày + nghỉ luỹ thừa.
        tt = self._doc_trang_thai().get(khoa) or {}
        hom_nay = datetime.fromtimestamp(bay_gio, tz=timezone.utc).strftime("%Y-%m-%d")
        so_lan_thu = int(tt.get("so_lan_thu") or 0) if tt.get("ngay_thu") == hom_nay else 0
        if so_lan_thu >= self._tran_thu_ngay():
            self._cap_nhat_trang_thai(khoa, trang_thai="het_tran_ngay")
            return {"ok": True, "khoa": khoa, "ma": "het_tran_ngay"}
        nghi_den = float(tt.get("nghi_den") or 0)
        if nghi_den and bay_gio < nghi_den:
            return {"ok": True, "khoa": khoa, "ma": "dang_nghi_luy_thua"}

        # 1. Lấy danh sách hồ sơ rồi ghép chắc chắn.
        try:
            ds = await client.danh_sach_ho_so()
        except Exception as e:
            log.warning("lam-moi-jwt: danh_sach_ho_so lỗi: %s", type(e).__name__)
            ds = {"ok": False, "ma_loi": "loi_goi"}
        if not isinstance(ds, dict) or not ds.get("ok"):
            self._ghi_loi_tam_thoi(khoa, hom_nay, so_lan_thu, bay_gio,
                                   tt, "loi_danh_sach_ho_so")
            return {"ok": False, "khoa": khoa, "ma": "loi_dich_vu",
                    "buoc": "danh_sach_ho_so"}
        ho_so_ds = ds.get("profiles")
        ho_so_ds = [str(h) for h in ho_so_ds] if isinstance(ho_so_ds, list) else []
        ho_so = await self._tim_ho_so(client, tk, ho_so_ds)
        if ho_so is None:
            self._danh_dau_can_nguoi(
                khoa, "khong_ghep_duoc_ho_so",
                "không ghép được hồ sơ trình duyệt cho khoá này "
                "(tái dùng hồ sơ cho tài khoản rồi vòng sau tự làm mới)")
            log.warning("lam-moi-jwt: khoá %s không ghép được hồ sơ", khoa)
            return {"ok": False, "khoa": khoa, "ma": "khong_ghep_duoc_ho_so",
                    "can_nguoi": True}

        # 2. Gọi làm mới.
        try:
            kq = await client.lam_moi_jwt(ho_so)
        except Exception as e:
            log.warning("lam-moi-jwt: lam_moi_jwt lỗi: %s", type(e).__name__)
            kq = {"ok": False, "ma_loi": "loi_goi"}
        if not isinstance(kq, dict):
            kq = {"ok": False, "ma_loi": "phan_hoi_la"}

        # 401 từ dịch vụ = hồ sơ đã đăng xuất → VĨNH VIỄN: dừng, báo cần
        # đăng nhập lại (không thử lại mãi).
        if kq.get("ma_loi") == "sai_khoa_dich_vu" or kq.get("ma_trang_thai") == 401:
            self._danh_dau_can_nguoi(
                khoa, "ho_so_dang_xuat",
                f"hồ sơ trình duyệt {ho_so} đã đăng xuất — "
                "cần đăng nhập lại hồ sơ trong máy đăng nhập")
            log.warning("lam-moi-jwt: khoá %s: hồ sơ %s đã đăng xuất",
                        khoa, ho_so)
            return {"ok": False, "khoa": khoa, "ma": "ho_so_dang_xuat",
                    "can_nguoi": True, "ho_so": ho_so}

        token_moi = kq.get("access_token") if kq.get("ok") else None
        if not token_moi or not isinstance(token_moi, str):
            self._ghi_loi_tam_thoi(khoa, hom_nay, so_lan_thu, bay_gio, tt,
                                   "lam_moi_that_bai")
            return {"ok": False, "khoa": khoa, "ma": "loi_dich_vu",
                    "ho_so": ho_so}
        if token_moi == tk.token:
            # Dịch vụ trả lại đúng token cũ = không xoay thật → không thay,
            # coi như lỗi tạm thời (có thể phiên chưa kịp xoay).
            self._ghi_loi_tam_thoi(khoa, hom_nay, so_lan_thu, bay_gio, tt,
                                   "token_moi_giong_cu")
            log.warning("lam-moi-jwt: khoá %s: token mới giống token cũ", khoa)
            return {"ok": False, "khoa": khoa, "ma": "token_moi_giong_cu",
                    "ho_so": ho_so}

        # 3. Thay token qua đường cai {giu, them}.
        kq_thay = self._thay_token(khoa, token_moi)
        if not kq_thay.get("ok"):
            ma = kq_thay.get("ma_loi") or "loi_thay_token"
            if ma == "token_da_doi":
                return {"ok": True, "khoa": khoa, "ma": "token_da_doi"}
            self._ghi_loi_tam_thoi(khoa, hom_nay, so_lan_thu, bay_gio, tt, ma)
            return {"ok": False, "khoa": khoa, "ma": "loi_dich_vu",
                    "chi_tiet": ma}
        khoa_moi = kq_thay.get("khoa_moi") or khoa

        # Làm giàu email từ token mới (không đoán — đọc claim).
        email_moi = email_cua(token_moi)
        try:
            self._module._sau_lam_moi_jwt(khoa_moi, email_moi, ho_so)
        except Exception as e:
            log.warning("lam-moi-jwt: cập nhật sau làm mới lỗi: %s",
                        type(e).__name__)

        # Xoá sổ lỗi cũ, ghi thành công (nhật ký chỉ nêu 4 ký tự cuối).
        tt_moi = self._doc_trang_thai()
        tt_moi.pop(khoa, None)
        self._luu_trang_thai(tt_moi)
        self._cap_nhat_trang_thai(
            khoa_moi, trang_thai="da_lam_moi", ma_loi=None,
            lan_lam_moi=datetime.fromtimestamp(time.time(), tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
            het_han=het_han_iso(exp_cua(token_moi)),
            con_lai_ngay=con_lai_ngay(exp_cua(token_moi)),
            ho_so=ho_so, so_lan_thu=0, ngay_thu=hom_nay, nghi_den=0,
            loi_lien_tiep=0)
        try:
            from app import nhat_ky
            nhat_ky.ghi("quan_tri", "thong_tin",
                        f"chatgpt-web: đã làm mới khoá …{khoa[-4:]} "
                        f"(hồ sơ {ho_so})",
                        module="chatgpt-web")
        except Exception as e:
            log.warning("lam-moi-jwt: không ghi nhật ký được: %s",
                        type(e).__name__)
        log.info("lam-moi-jwt: đã làm mới khoá …%s", khoa[-4:])
        return {"ok": True, "khoa": khoa, "khoa_moi": khoa_moi,
                "ma": "da_lam_moi", "ho_so": ho_so}

    def _ghi_loi_tam_thoi(self, khoa: str, hom_nay: str, so_lan_thu: int,
                          bay_gio: float, tt: dict, ma_loi: str) -> None:
        lien_tiep = int(tt.get("loi_lien_tiep") or 0) + 1
        nghi = min(NGHI_LUY_THUA_CO_SO_GIAY * (2 ** (lien_tiep - 1)),
                   NGHI_LUY_THUA_TRAN_GIAY)
        self._cap_nhat_trang_thai(
            khoa, trang_thai="loi_tam_thoi", ma_loi=ma_loi,
            so_lan_thu=so_lan_thu + 1, ngay_thu=hom_nay,
            nghi_den=bay_gio + nghi, loi_lien_tiep=lien_tiep)

    # -- một vòng qua mọi tài khoản ------------------------------------

    async def chay_mot_vong(self) -> dict:
        """Quét mọi khoá của module; trả thống kê {ma: dem}."""
        lay = getattr(self._module, "khoa_tai_khoan", None)
        if not callable(lay):
            return {}
        try:
            cac_khoa = lay()
        except Exception as e:
            log.warning("lam-moi-jwt: khoa_tai_khoan lỗi: %s", type(e).__name__)
            return {}
        thong_ke: dict[str, int] = {}
        import asyncio as _asyncio
        for khoa in cac_khoa or []:
            try:
                kq = await self.lam_moi(khoa)
            except Exception as e:  # không để một khoá vỡ cả vòng
                log.warning("lam-moi-jwt: khoá %s lỗi không ngờ: %s",
                            khoa, type(e).__name__)
                kq = {"ok": False, "ma": "loi_noi_bo"}
            thong_ke[kq.get("ma") or "khong_ro"] = \
                thong_ke.get(kq.get("ma") or "khong_ro", 0) + 1
            await _asyncio.sleep(5)  # giãn cách giữa các tài khoản
        return thong_ke
