"""Tự lấy cookie muse.ai MỚI từ workspace Firefox đã đăng nhập (NV230).

Vấn đề: `lam_moi_phien` cũ chỉ kiểm lại đúng cookie đã dán — hết hạn là
`het_han`, dù workspace Firefox của chính tài khoản đó vẫn đang đăng nhập
muse.ai (chủ máy nói 21/10: «muse2api dùng firefox đăng nhập tự động rồi
lưu workspace mà, đâu cần đăng nhập lại»).

Cơ chế (theo MẪU NV207 `modules/chatgpt-web/lam_moi_jwt.py`):
  1. Ghép khoá → hồ sơ CHẮC CHẮN: trường `ho_so` trong bản ghi kho riêng
     NV93 của module, ghép qua chính giá trị hatch_sess của cookie hiện tại
     — KHÔNG đoán từ tên hồ sơ/email. Không có gắn → không làm mới bừa.
  2. Đọc cookie muse.ai qua ĐƯỜNG SẴN CÓ của dịch vụ trình duyệt (client
     vnc-xa: `trinh_duyet_ds` + `trinh_duyet_cookie` → GET
     /ho-so/{id}/cookie?mien=muse.ai) — không viết bộ đọc cookie thứ hai.
     Chỉ hồ sơ Firefox ĐÃ ĐÓNG mới đọc được; đang mở → 409.
  3. Kiểm cookie mới bằng bootstrap của module (`_kiem_cookie`); qua thì
     THAY cookie của khoá qua đường cai {giu, them} (`Module._thay_cookie`).

Cần người (dừng, không thử lại mãi): hồ sơ đang mở (409), hồ sơ không có
cookie hatch_sess, hồ sơ đã gắn nhưng không còn trong dịch vụ. Lỗi tạm
(cookie mới cũng bị từ chối, dịch vụ sập, cookie mới giống cũ): KHÔNG thay,
ghi nghỉ luỹ thừa 300·2^n (trần 6 giờ) + trần thử mỗi ngày (mặc định 5).

An toàn bí mật: cookie/token không vào log, phản hồi hay sổ trạng thái;
nhật ký chỉ ghi «đã làm mới khoá …XXXX» (4 ký tự cuối của khoá mờ).
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("web2api.module.muse2api.lam_moi_cookie")

#: Tên miền cookie của module này (khai trong manifest `nhan_cookie`).
MIEN_COOKIE = "muse.ai"
#: Trần thử mặc định: tối đa 5 lần/ngày/khoá (như NV207).
TRAN_THU_NGAY_MAC_DINH = 5
#: Nghỉ luỹ thừa sau lỗi tạm thời: 300 * 2^loi_lien_tiep giây, trần 6 giờ.
NGHI_LUY_THUA_CO_SO_GIAY = 300.0
NGHI_LUY_THUA_TRAN_GIAY = 6 * 3600.0

_HUONG_DAN = {
    "ho_so_dang_mo": (
        "Hồ sơ Firefox của tài khoản đang MỞ nên không đọc được cookie "
        "(dịch vụ trả 409). Hãy đóng hồ sơ (giữ phiên) rồi bấm «Đã xử lý "
        "xong» để hệ thống tự lấy cookie mới."),
    "khong_co_hatch_sess": (
        "Workspace Firefox đã gắn chưa đăng nhập muse.ai (không có cookie "
        "hatch_sess). Hãy mở noVNC của hồ sơ, đăng nhập muse.ai, đóng hồ sơ "
        "(giữ phiên) rồi bấm «Đã xử lý xong»."),
    "ho_so_khong_con": (
        "Hồ sơ Firefox đã gắn với tài khoản không còn trong dịch vụ trình "
        "duyệt. Hãy tái dùng hồ sơ cho tài khoản hoặc đăng nhập lại trong "
        "workspace rồi bấm «Đã xử lý xong»."),
}


def _hatch_sess_tu_cookie(cookies: str) -> str | None:
    """Trích giá trị cookie ``hatch_sess`` khỏi chuỗi cookie header."""
    for phan in str(cookies or "").split(";"):
        ten, _, gia_tri = phan.partition("=")
        if ten.strip() == "hatch_sess" and gia_tri.strip():
            return gia_tri.strip()
    return None


def _iso(bay_gio: float) -> str:
    return datetime.fromtimestamp(bay_gio, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Client dịch vụ trình duyệt (nạp lười qua importlib — module web2api không
# phải package nên không import tuyệt đối được; chỉ dùng phương thức CÔNG
# KHAI của module vnc-xa, như NV207 dùng module may-dang-nhap).
# ---------------------------------------------------------------------------

_VNC_MOD: Any = None


def _tai_vnc_xa() -> Any:
    """Nạp modules/vnc-xa/module.py (cache)."""
    global _VNC_MOD
    if _VNC_MOD is None:
        p = Path(__file__).resolve().parent.parent / "vnc-xa" / "module.py"
        spec = importlib.util.spec_from_file_location(
            "web2api_vnc_xa_client", str(p))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _VNC_MOD = mod
    return _VNC_MOD


def _tao_client_dich_vu_mac_dinh(url: str, khoa: str) -> Any:
    """Thể hiện client vnc-xa đã nap() — dùng đúng đường gọi HTTP có kiểm
    SSRF của module đó (không viết bộ gọi thứ hai)."""
    mod = _tai_vnc_xa()
    client = mod.Module({"tien_to": "vnc-xa"})
    client.nap({"dich_vu_url": url, "khoa_dich_vu": khoa})
    return client


# ---------------------------------------------------------------------------
# Bộ làm mới cookie gắn với một thể hiện Module muse2api.
# ---------------------------------------------------------------------------

class BoLamMoiCookie:
    """Thực hiện một lượt tự lấy cookie mới cho một khoá muse2api.

    `tao_client_dich_vu`: mối nối để test chích — () -> client có hai
    phương thức công khai `trinh_duyet_ds()` và `trinh_duyet_cookie(ho_so,
    mien)` (như module vnc-xa). Mặc định: tạo từ cấu hình dịch vụ trình
    duyệt của module.
    """

    def __init__(self, module: Any, *,
                 tao_client_dich_vu: Any | None = None) -> None:
        self._module = module
        self._tao_client_dich_vu = tao_client_dich_vu
        self._locks: dict[str, asyncio.Lock] = {}

    # -- cấu hình ------------------------------------------------------

    def _tran_thu_ngay(self) -> int:
        try:
            v = int(float(getattr(self._module, "_cookie_tran_thu_ngay",
                                  TRAN_THU_NGAY_MAC_DINH)))
        except (TypeError, ValueError):
            v = TRAN_THU_NGAY_MAC_DINH
        return v if v > 0 else TRAN_THU_NGAY_MAC_DINH

    def _lay_client(self) -> Any | None:
        if self._tao_client_dich_vu is not None:
            return self._tao_client_dich_vu()
        url = str(getattr(self._module, "_dich_vu_trinh_duyet_url", "")
                  or "").strip()
        if not url:
            return None
        khoa = str(getattr(self._module, "_khoa_dich_vu_trinh_duyet", "")
                   or "")
        return _tao_client_dich_vu_mac_dinh(url, khoa)

    # -- sổ trạng thái (kho riêng của module, không bí mật) --------------

    def _doc_trang_thai(self) -> dict:
        doc = getattr(self._module, "_doc_trang_thai_cookie", None)
        if callable(doc):
            try:
                tt = doc()
            except Exception:
                return {}
            return tt if isinstance(tt, dict) else {}
        return {}

    def _luu_trang_thai(self, tt: dict) -> None:
        luu = getattr(self._module, "_luu_trang_thai_cookie", None)
        if callable(luu):
            try:
                luu(tt)
            except Exception as e:
                log.warning("lam-moi-cookie: không lưu được trạng thái: %s",
                            type(e).__name__)

    def _cap_nhat_trang_thai(self, khoa: str, **truong) -> dict:
        tt = self._doc_trang_thai()
        muc = tt.get(khoa) if isinstance(tt.get(khoa), dict) else {}
        muc = dict(muc)
        muc.update(truong)
        tt[khoa] = muc
        self._luu_trang_thai(tt)
        return muc

    # -- ghi nhận kết quả ------------------------------------------------

    def _nghi_luy_thua(self, tt: dict) -> float:
        lien_tiep = int(tt.get("loi_lien_tiep") or 0) + 1
        return min(NGHI_LUY_THUA_CO_SO_GIAY * (2 ** (lien_tiep - 1)),
                   NGHI_LUY_THUA_TRAN_GIAY), lien_tiep

    def _ghi_loi_tam_thoi(self, khoa: str, hom_nay: str, so_lan_thu: int,
                          bay_gio: float, tt: dict, ma_loi: str) -> None:
        nghi, lien_tiep = self._nghi_luy_thua(tt)
        self._cap_nhat_trang_thai(
            khoa, trang_thai="loi_tam_thoi", ma_loi=ma_loi,
            so_lan_thu=so_lan_thu + 1, ngay_thu=hom_nay,
            nghi_den=bay_gio + nghi, loi_lien_tiep=lien_tiep)

    def _danh_dau_can_nguoi(self, khoa: str, hom_nay: str, so_lan_thu: int,
                            bay_gio: float, tt: dict, ma_loi: str) -> dict:
        """Ghi trạng thái cần người (kho riêng + trạng thái khôi phục của
        lõi) và trả kết quả can_nguoi cho tầng (a)."""
        huong_dan = _HUONG_DAN[ma_loi]
        nghi, lien_tiep = self._nghi_luy_thua(tt)
        self._cap_nhat_trang_thai(
            khoa, trang_thai="can_nguoi", ma_loi=ma_loi,
            so_lan_thu=so_lan_thu + 1, ngay_thu=hom_nay,
            nghi_den=bay_gio + nghi, loi_lien_tiep=lien_tiep)
        try:
            from app import khoi_phuc_phien as kp
            kp.dat_can_nguoi("muse2api", khoa, "lam_moi_cookie", huong_dan)
        except Exception as e:
            log.warning("lam-moi-cookie: không đánh dấu can_nguoi được: %s",
                        type(e).__name__)
        try:
            from app import nhat_ky
            nhat_ky.ghi("quan_tri", "canh_bao",
                        f"muse2api: khoá {khoa} cần người xử lý "
                        f"(tự lấy cookie mới: {ma_loi}).",
                        module="muse2api")
        except Exception as e:
            log.warning("lam-moi-cookie: không ghi nhật ký được: %s",
                        type(e).__name__)
        log.warning("lam-moi-cookie: khoá …%s can_nguoi (%s)",
                    khoa[-4:], ma_loi)
        return {"ok": False, "khoa": khoa, "ma": ma_loi,
                "can_nguoi": True, "huong_dan": huong_dan}

    # -- một lượt làm mới -------------------------------------------------

    async def lam_moi(self, khoa: str) -> dict:
        """Thử lấy cookie mới cho một khoá. Không bao giờ ném.

        Mã (`ma`): khoa_khong_co | khong_co_ho_so_gan | chua_cau_hinh_dich_vu
        | dang_chay | het_tran_ngay | dang_nghi_luy_thua | da_lam_moi
        | cookie_da_doi | cookie_moi_giong_cu | cookie_moi_cung_hong
        | loi_dich_vu | ho_so_dang_mo | khong_co_hatch_sess | ho_so_khong_con
        (ba mã cuối kèm `can_nguoi=True` + `huong_dan`).
        """
        try:
            return await self._lam_moi(khoa)
        except Exception as e:  # biên ngoài cùng: không để tầng (a) vỡ
            log.warning("lam-moi-cookie: lỗi không ngờ: %s",
                        type(e).__name__)
            return {"ok": False, "khoa": str(khoa or ""), "ma": "loi_noi_bo"}

    async def _lam_moi(self, khoa: str) -> dict:
        module = self._module
        khoa = str(khoa or "")
        acc = module._tim_acc(khoa)
        if acc is None:
            return {"ok": False, "khoa": khoa, "ma": "khoa_khong_co"}

        # 1. Ghép hồ sơ CHẮC CHẮN (trường ho_so trong kho riêng NV93).
        ho_so = module._ho_so_gan_voi_khoa(khoa)
        if not ho_so:
            # (f) khoá không gắn hồ sơ → không làm mới bừa, không gọi dịch vụ.
            return {"ok": False, "khoa": khoa, "ma": "khong_co_ho_so_gan"}

        bay_gio = time.time()
        hom_nay = datetime.fromtimestamp(
            bay_gio, tz=timezone.utc).strftime("%Y-%m-%d")
        tt = self._doc_trang_thai().get(khoa) or {}
        so_lan_thu = int(tt.get("so_lan_thu") or 0) \
            if tt.get("ngay_thu") == hom_nay else 0
        # Đã kết luận cần người và còn trong nhịp nghỉ → giữ nguyên kết luận,
        # KHÔNG gọi lại dịch vụ (không thử lại mãi).
        if tt.get("trang_thai") == "can_nguoi" \
                and float(tt.get("nghi_den") or 0) > bay_gio \
                and tt.get("ma_loi") in _HUONG_DAN:
            return {"ok": False, "khoa": khoa, "ma": tt["ma_loi"],
                    "can_nguoi": True,
                    "huong_dan": _HUONG_DAN[tt["ma_loi"]]}
        if so_lan_thu >= self._tran_thu_ngay():
            self._cap_nhat_trang_thai(khoa, trang_thai="het_tran_ngay")
            return {"ok": False, "khoa": khoa, "ma": "het_tran_ngay"}
        if float(tt.get("nghi_den") or 0) > bay_gio:
            return {"ok": False, "khoa": khoa, "ma": "dang_nghi_luy_thua"}

        client = self._lay_client()
        if client is None:
            self._cap_nhat_trang_thai(khoa, trang_thai="chua_cau_hinh_dich_vu")
            return {"ok": False, "khoa": khoa,
                    "ma": "chua_cau_hinh_dich_vu"}

        # Single-flight trong thể hiện: hai lượt cùng khoá không đè nhau.
        lock = self._locks.get(khoa)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[khoa] = lock
        if lock.locked():
            return {"ok": False, "khoa": khoa, "ma": "dang_chay"}
        async with lock:
            return await self._lam_moi_co_khoa(
                client, acc, khoa, ho_so, hom_nay, so_lan_thu, bay_gio, tt)

    async def _lam_moi_co_khoa(self, client: Any, acc: Any, khoa: str,
                               ho_so: str, hom_nay: str, so_lan_thu: int,
                               bay_gio: float, tt: dict) -> dict:
        module = self._module

        # 2. Ghép chắc chắn với danh sách hồ sơ của dịch vụ + trạng thái mở.
        try:
            ds = await client.trinh_duyet_ds()
        except Exception as e:
            log.warning("lam-moi-cookie: trinh_duyet_ds lỗi: %s",
                        type(e).__name__)
            ds = None
        if not isinstance(ds, list):
            self._ghi_loi_tam_thoi(khoa, hom_nay, so_lan_thu, bay_gio, tt,
                                   "loi_dich_vu")
            return {"ok": False, "khoa": khoa, "ma": "loi_dich_vu",
                    "buoc": "trinh_duyet_ds"}
        muc = next((x for x in ds if isinstance(x, dict)
                    and str(x.get("ho_so") or "") == ho_so), None)
        if muc is None:
            return self._danh_dau_can_nguoi(
                khoa, hom_nay, so_lan_thu, bay_gio, tt, "ho_so_khong_con")
        if muc.get("dang_mo"):
            # Đang mở → dịch vụ sẽ trả 409; không đóng cưỡng bức, cần người.
            return self._danh_dau_can_nguoi(
                khoa, hom_nay, so_lan_thu, bay_gio, tt, "ho_so_dang_mo")

        # 3. Đọc cookie muse.ai của hồ sơ ĐÃ ĐÓNG (đường sẵn có của dịch vụ).
        try:
            kq_cookie = await client.trinh_duyet_cookie(ho_so, MIEN_COOKIE)
        except Exception as e:
            ma = getattr(e, "ma", None)
            log.warning("lam-moi-cookie: trinh_duyet_cookie lỗi (ma=%s)", ma)
            if ma == 409:
                return self._danh_dau_can_nguoi(
                    khoa, hom_nay, so_lan_thu, bay_gio, tt, "ho_so_dang_mo")
            if ma == 404:
                return self._danh_dau_can_nguoi(
                    khoa, hom_nay, so_lan_thu, bay_gio, tt,
                    "khong_co_hatch_sess")
            self._ghi_loi_tam_thoi(khoa, hom_nay, so_lan_thu, bay_gio, tt,
                                   "loi_dich_vu")
            return {"ok": False, "khoa": khoa, "ma": "loi_dich_vu",
                    "buoc": "trinh_duyet_cookie"}
        cookie_moi = kq_cookie.get("cookie") if isinstance(kq_cookie, dict) \
            else None
        if not isinstance(cookie_moi, str) or not cookie_moi.strip():
            return self._danh_dau_can_nguoi(
                khoa, hom_nay, so_lan_thu, bay_gio, tt, "khong_co_hatch_sess")
        cookie_moi = cookie_moi.strip()
        if _hatch_sess_tu_cookie(cookie_moi) is None:
            return self._danh_dau_can_nguoi(
                khoa, hom_nay, so_lan_thu, bay_gio, tt, "khong_co_hatch_sess")
        if cookie_moi == acc.cookies:
            # Workspace vẫn giữ đúng phiên đã chết → thay cũng như không.
            self._ghi_loi_tam_thoi(khoa, hom_nay, so_lan_thu, bay_gio, tt,
                                   "cookie_moi_giong_cu")
            log.warning("lam-moi-cookie: khoá …%s: cookie hồ sơ giống cookie "
                        "hiện tại", khoa[-4:])
            return {"ok": False, "khoa": khoa, "ma": "cookie_moi_giong_cu",
                    "ho_so": ho_so}

        # 4. Kiểm cookie mới bằng bootstrap của module (không tốn tiền).
        ok, _loi, bi_tu_choi = await asyncio.to_thread(
            module._kiem_cookie, cookie_moi)
        if bi_tu_choi:
            # (d) cookie mới CŨNG hỏng → không thay, nghỉ luỹ thừa.
            self._ghi_loi_tam_thoi(khoa, hom_nay, so_lan_thu, bay_gio, tt,
                                   "cookie_moi_cung_hong")
            log.warning("lam-moi-cookie: khoá …%s: cookie mới cũng bị từ "
                        "chối", khoa[-4:])
            return {"ok": False, "khoa": khoa, "ma": "cookie_moi_cung_hong",
                    "ho_so": ho_so}
        if not ok:
            self._ghi_loi_tam_thoi(khoa, hom_nay, so_lan_thu, bay_gio, tt,
                                   "loi_kiem_cookie_moi")
            return {"ok": False, "khoa": khoa, "ma": "loi_dich_vu",
                    "buoc": "kiem_cookie_moi"}

        # 5. Thay cookie của khoá qua đường cai {giu, them}.
        kq_thay = module._thay_cookie(khoa, cookie_moi)
        if not kq_thay.get("ok"):
            ma_thay = kq_thay.get("ma_loi") or "loi_thay_cookie"
            if ma_thay == "cookie_da_doi":
                return {"ok": True, "khoa": khoa, "ma": "cookie_da_doi"}
            self._ghi_loi_tam_thoi(khoa, hom_nay, so_lan_thu, bay_gio, tt,
                                   ma_thay)
            return {"ok": False, "khoa": khoa, "ma": "loi_dich_vu",
                    "chi_tiet": ma_thay}
        khoa_moi = kq_thay.get("khoa_moi") or khoa

        # Xoá sổ lỗi của khoá cũ, ghi thành công dưới khoá mới.
        tt_moi = self._doc_trang_thai()
        tt_moi.pop(khoa, None)
        self._luu_trang_thai(tt_moi)
        self._cap_nhat_trang_thai(
            khoa_moi, trang_thai="da_lam_moi", ma_loi=None,
            lan_lam_moi=_iso(time.time()), ho_so=ho_so,
            so_lan_thu=0, ngay_thu=hom_nay, nghi_den=0, loi_lien_tiep=0)
        try:
            from app import nhat_ky
            nhat_ky.ghi("quan_tri", "thong_tin",
                        f"muse2api: đã tự lấy cookie mới cho khoá …{khoa[-4:]} "
                        f"(hồ sơ {ho_so})",
                        module="muse2api")
        except Exception as e:
            log.warning("lam-moi-cookie: không ghi nhật ký được: %s",
                        type(e).__name__)
        log.info("lam-moi-cookie: đã làm mới khoá …%s", khoa[-4:])
        return {"ok": True, "khoa": khoa, "khoa_moi": khoa_moi,
                "ma": "da_lam_moi", "ho_so": ho_so}
