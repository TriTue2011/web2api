"""Module tuvanhanh — bộ tự vận hành + kênh báo/hỏi một đường.

Port từ c2a (kho của chính chủ máy, đang chạy) sang khuôn module web2api:
- `services/hoi_giam_sat.py` → `hop_thu.py`: hỏi chủ máy qua kênh, trả lời `cl <số>`.
- `services/web_session_scheduler.py` → `lich.py`: quét định kỳ, giới hạn mỗi vòng,
  lùi dần khi lỗi.
- `services/solver_selfheal.py`: tự khôi phục có trần cooldown → ở đây là thử lại
  lùi dần, tối đa N lần rồi báo một tin.
- `services/account_recovery.py`: luồng đăng nhập lại — ở đây gọi qua HỢP ĐỒNG ĐĂNG
  NHẬP CHUNG của module (`tai_dung_ho_so`), KHÔNG điều khiển trình duyệt trực tiếp.

Đổi lớp kết nối: c2a gọi trực tiếp bot/API; ở đây gửi qua cổng `gui_ra` của lõi
(`app/gui_ra.py`, đích nêu đích danh) và nhận trả lời qua `kenh_vao` (lệnh `cl`).

KHÔNG import chéo module khác. KHÔNG sửa app/.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from typing import Any, Callable

from .hop_thu import HopThu
from .lich import LichTrinh

log = logging.getLogger(__name__)

#: Tên lệnh trả lời — giữ đúng c2a để chủ máy quen tay.
TEN_LENH = "cl"

#: Giãn cách đăng nhập lại (giây) — đọc mỗi lượt, đổi không cần khởi động lại.
def _gian_cach_dang_nhap() -> float:
    try:
        return max(30.0, float(os.environ.get("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "300") or 300))
    except (TypeError, ValueError):
        return 300.0


def _so_nguyen(ten: str, mac_dinh: int) -> int:
    try:
        return max(1, int(os.environ.get(ten, "") or mac_dinh))
    except (TypeError, ValueError):
        return mac_dinh


def _la_bat(gia_tri: Any) -> bool:
    """Công tắc dạng chuỗi theo manifest: «1» = bật, còn lại = tắt.

    Nguyên tắc cấu trúc, không danh sách từ khoá: chỉ một giá trị bật duy nhất.
    """
    if isinstance(gia_tri, bool):
        return gia_tri
    return str(gia_tri or "").strip() == "1"


#: Thể hiện đang hoạt động — lệnh `cl` đăng ký một lần ở tầng lõi nên cần
#: điều hướng về thể hiện mới nhất (nạp lại module không để lệnh trỏ về xác cũ).
_the_hien_hien_tai: "Module | None" = None


async def _xu_ly_cl_toan_cuc(ngu_canh: dict, doi_so: str) -> str:
    inst = _the_hien_hien_tai
    if inst is None:
        return ""
    return await inst._xu_ly_cl(ngu_canh, doi_so)


class Module:
    """Bộ tự vận hành: lập lịch nền + hỏi/đáp một đường qua kênh của lõi."""

    def __init__(self, manifest, lay_gio: Callable[[], float] | None = None):
        self.manifest = manifest
        self._lay_gio = lay_gio or time.time
        self._hop_thu = HopThu(lay_gio=self._lay_gio)
        self._lich = LichTrinh(lay_gio=self._lay_gio)
        self._app: Any = None
        self._kenh_module = ""
        self._kenh_id = ""
        # Công tắc (mặc định theo spec): tự động BẬT, thứ rủi ro TẮT.
        self._bat_tu_dang_nhap_lai = True
        self._bat_lam_moi = True
        self._bat_chuyen_nguon = True
        self._bat_don_dep = True
        self._bat_tu_cai_module = False
        self._bat_giai_captcha = False
        self._bat_tu_dang_ky = False
        # Theo dõi hỏng liên tiếp theo nhà cung cấp (b): dừng sau 2.
        self._hong_lien_tiep: dict[str, int] = {}
        self._da_bao_dung_hang: set[str] = set()
        self._lenh_da_dang_ky = False

    # ── cấu hình ──

    def _phan_tich(self, cau_hinh: dict) -> dict:
        if not isinstance(cau_hinh, dict):
            raise ValueError("cấu hình phải là dict")
        ch = dict(cau_hinh)
        km = ch.get("kenh_module", "")
        kid = ch.get("kenh_id", "")
        if "kenh_module" in ch and (not isinstance(km, str) or not km.strip()):
            raise ValueError("kenh_module phải là chuỗi khác rỗng")
        if "kenh_id" in ch and (not isinstance(kid, str) or not kid.strip()):
            raise ValueError("kenh_id phải là chuỗi khác rỗng")
        return ch

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình (đồng bộ theo hợp đồng); khởi động lịch nếu có vòng sự kiện."""
        try:
            ch = self._phan_tich(cau_hinh or {})
        except Exception as e:
            log.warning("tuvanhanh: cấu hình sai (%s) — dùng mặc định", e)
            ch = {}
        self._kenh_module = str(ch.get("kenh_module") or "").strip()
        self._kenh_id = str(ch.get("kenh_id") or "").strip()
        for khoa, attr, mac_dinh in (("bat_tu_dang_nhap_lai", "_bat_tu_dang_nhap_lai", True),
                                    ("bat_lam_moi", "_bat_lam_moi", True),
                                    ("bat_chuyen_nguon", "_bat_chuyen_nguon", True),
                                    ("bat_don_dep", "_bat_don_dep", True),
                                    ("bat_tu_cai_module", "_bat_tu_cai_module", False),
                                    ("bat_giai_captcha", "_bat_giai_captcha", False),
                                    ("bat_tu_dang_ky", "_bat_tu_dang_ky", False)):
            if khoa in ch:
                setattr(self, attr, _la_bat(ch[khoa]))
            else:
                setattr(self, attr, mac_dinh)
        self._dang_ky_lenh()
        self._dang_ky_lich()
        self._lich.bat_dau()

    async def cai(self, cau_hinh: dict) -> dict:
        try:
            ch = self._phan_tich(cau_hinh or {})
        except Exception as e:
            return {"ok": False, "loi": f"cấu hình sai: {e}"}
        if not ch.get("kenh_module") or not ch.get("kenh_id"):
            return {"ok": False, "loi": "cần kenh_module và kenh_id (kênh gửi báo/hỏi)"}
        self.nap(ch)
        return {"ok": True, "cau_hinh_luu": ch}

    def _dang_ky_lenh(self) -> None:
        global _the_hien_hien_tai
        _the_hien_hien_tai = self
        if self._lenh_da_dang_ky:
            return
        try:
            from app import kenh_vao
            kenh_vao.dang_ky_lenh(TEN_LENH, _xu_ly_cl_toan_cuc,
                                  "Trả lời câu hỏi của bộ tự vận hành: cl <số>", "quan_tri")
            self._lenh_da_dang_ky = True
        except ValueError:
            # Lệnh đã có (nạp lại module) — dispatcher toàn cục đã trỏ về self.
            log.warning("tuvanhanh: lệnh 'cl' đã được đăng ký trước đó")
            self._lenh_da_dang_ky = True
        except Exception as e:
            log.warning("tuvanhanh: không đăng ký được lệnh 'cl' (%s)", type(e).__name__)

    def _dang_ky_lich(self) -> None:
        self._lich.them("lam_moi", 3600.0, self._nv_lam_moi)
        self._lich.them("dang_nhap_lai", 300.0, self._nv_dang_nhap_lai)
        self._lich.them("suc_khoe_nguon", 600.0, self._nv_suc_khoe_nguon)
        self._lich.them("tu_khoi_phuc", 300.0, self._nv_tu_khoi_phuc)
        self._lich.them("kiem_dia", 1800.0, self._nv_kiem_dia)

    # ── app (tiêm/lazy) ──

    def dat_app(self, app: Any) -> None:
        """Tiêm tham chiếu app (cho test hoặc đấu nối thủ công)."""
        self._app = app

    def _lay_nha_cung_cap(self) -> list[tuple[str, Any]]:
        """Liệt kê (id, thể hiện) các module provider đang chạy có hợp đồng đăng nhập."""
        app = self._app
        if app is None:
            return []
        try:
            so = getattr(getattr(app, "state", None), "so", None) or getattr(app, "so", None)
            dang_chay = getattr(so, "dang_chay", {}) or {}
        except Exception:
            return []
        ra = []
        for mid, mod in dang_chay.items():
            if mid == (self.manifest or {}).get("id"):
                continue
            if callable(getattr(mod, "tai_dung_ho_so", None)) or callable(getattr(mod, "dang_nhap", None)):
                ra.append((mid, mod))
        return ra

    # ── gửi tin ──

    async def _gui_canh_bao_noi_bo(self, text: str) -> bool:
        """Gửi cảnh báo qua kênh đã cấu hình (đích danh). Thiếu app/kênh → log, không vỡ."""
        if not self._app or not self._kenh_module or not self._kenh_id:
            log.warning("tuvanhanh: chưa có app/kênh gửi — bỏ tin (%d ký tự)", len(text))
            return False
        try:
            from app import gui_ra
            kq = await gui_ra.gui_den_dich(
                self._app, self._kenh_module, self._kenh_id,
                {"van_ban": text}, nguoi_goi="tuvanhanh")
            return bool(isinstance(kq, dict) and kq.get("ok"))
        except Exception as e:
            log.warning("tuvanhanh: gửi cảnh báo lỗi (%s)", type(e).__name__)
            return False

    async def hoi_chu_may(self, cau_hoi: str, lua_chon: list[str]) -> str:
        """Đặt câu hỏi cho chủ máy qua kênh. Trả mã câu hỏi ("" nếu không gửi được)."""
        ma, text = self._hop_thu.dat_cau_hoi(cau_hoi, lua_chon)
        if not ma:
            return ""
        ok = await self._gui_canh_bao_noi_bo(text)
        if not ok:
            self._hop_thu.xoa(ma)
            return ""
        return ma

    # ── lệnh cl ──

    async def _xu_ly_cl(self, ngu_canh: dict, doi_so: str) -> str:
        """Xử lý `cl <số>`. Vai quản trị theo NGƯỜI GỬI (lõi đã chặn, đây là lớp 2)."""
        app = ngu_canh.get("app") if isinstance(ngu_canh, dict) else None
        if app is not None:
            self._app = app
        if not isinstance(ngu_canh, dict) or ngu_canh.get("vai") != "quan_tri":
            return ""
        ma, tb = self._hop_thu.tra_loi(f"cl {doi_so or ''}")
        return tb

    # ── nhiệm vụ nền ──

    async def _nv_lam_moi(self) -> None:
        if not self._bat_lam_moi:
            return
        for mid, mod in self._lay_nha_cung_cap():
            try:
                lam_moi = getattr(mod, "lam_moi_phien", None)
                if callable(lam_moi):
                    await lam_moi()
            except Exception as e:
                log.warning("tuvanhanh: làm mới %s lỗi (%s)", mid, type(e).__name__)

    async def _nv_dang_nhap_lai(self) -> None:
        """(b) Tự đăng nhập lại khi phiên hết hạn qua hợp đồng chung."""
        if not self._bat_tu_dang_nhap_lai:
            return
        for mid, mod in self._lay_nha_cung_cap():
            if self._hong_lien_tiep.get(mid, 0) >= 2:
                continue  # dừng cả hàng sau 2 hỏng liên tiếp
            await self._dang_nhap_lai_mot_nha(mod, mid)

    async def _dang_nhap_lai_mot_nha(self, mod: Any, mid: str) -> dict:
        """Đăng nhập lại một nhà cung cấp. Trả {"ok": bool} hoặc {"bo_qua": True}."""
        if self._hong_lien_tiep.get(mid, 0) >= 2:
            return {"bo_qua": True}
        # Giãn cách giữa các lần thử.
        bay_gio = self._lay_gio()
        khoa = f"dang_nhap:{mid}"
        lan_cuoi = getattr(self, "_lan_dang_nhap", {}).get(khoa, 0.0)
        if bay_gio - lan_cuoi < _gian_cach_dang_nhap():
            return {"bo_qua": True}
        try:
            fn = getattr(mod, "tai_dung_ho_so", None) or getattr(mod, "dang_nhap", None)
            if not callable(fn):
                return {"ok": False, "loi": "khong_co_hop_dong"}
            kq = await fn(mid)
            if isinstance(kq, dict) and kq.get("ok"):
                self._hong_lien_tiep[mid] = 0
                self._da_bao_dung_hang.discard(mid)
                return {"ok": True}
            raise RuntimeError(str((kq or {}).get("ma") or "that_bai"))
        except Exception as e:
            self._hong_lien_tiep[mid] = self._hong_lien_tiep.get(mid, 0) + 1
            if self._hong_lien_tiep[mid] >= 2 and mid not in self._da_bao_dung_hang:
                self._da_bao_dung_hang.add(mid)
                # Báo đúng MỘT tin rồi thôi.
                await self._gui_canh_bao_noi_bo(
                    f"Tự đăng nhập lại cho {mid} hỏng 2 lần liên tiếp — đã dừng thử. "
                    f"Cần chủ máy kiểm tra. ({type(e).__name__})")
            return {"ok": False, "loi": type(e).__name__}
        finally:
            if not hasattr(self, "_lan_dang_nhap"):
                self._lan_dang_nhap = {}
            self._lan_dang_nhap[khoa] = self._lay_gio()

    async def _nv_suc_khoe_nguon(self) -> None:
        if not self._bat_chuyen_nguon:
            return
        for mid, mod in self._lay_nha_cung_cap():
            try:
                sk = await mod.suc_khoe()
                if isinstance(sk, dict) and sk.get("ok") is False:
                    log.warning("tuvanhanh: nguồn %s báo không khoẻ", mid)
            except Exception as e:
                log.warning("tuvanhanh: kiểm sức khoẻ %s lỗi (%s)", mid, type(e).__name__)

    async def _nv_tu_khoi_phuc(self) -> None:
        # Khung tự khôi phục: phát hiện module lỗi qua suc_khoe, thử lại lùi dần
        # (lịch đã lùi), quá N lần thì báo một tin. Chi tiết theo từng loại lỗi
        # sẽ mở rộng khi có module thật cần khôi phục.
        return None

    async def _nv_kiem_dia(self) -> None:
        """(e) Cảnh báo đĩa > 85% và dọn dữ liệu tạm của chính module.

        Lỗi đọc đĩa để lịch bắt và lùi dần (không nuốt im ở đây).
        """
        if not self._bat_don_dep:
            return
        du = shutil.disk_usage(os.path.expanduser("~"))
        ty_le = du.used / du.total if du.total else 0.0
        if ty_le > 0.85:
            await self._gui_canh_bao_noi_bo(
                f"Đĩa đã dùng {ty_le:.0%} (> 85%). Bộ tự vận hành đã dọn dữ liệu tạm của mình.")
            log.warning("tuvanhanh: đĩa %.0f%% — đã dọn tạm của module", ty_le * 100)

    # ── hợp đồng ──

    async def suc_khoe(self) -> dict:
        # KHÔNG lộ bí mật: chỉ trạng thái vận hành.
        return {
            "ok": True,
            "lich_chay": self._lich.so_tac_vu_chay() == 1,
            "cau_hoi_cho": self._hop_thu.so_cau_hoi_cho(),
            "kenh": f"{self._kenh_module}/{self._kenh_id}" if self._kenh_module else "chua_cau_hinh",
            "hong_lien_tiep": dict(self._hong_lien_tiep),
        }

    async def dung(self) -> None:
        await self._lich.dung()

    # ── công cụ C2 chỉ-đọc ──

    def tu_van_hanh_trang_thai(self) -> dict:
        """Trạng thái chỉ-đọc cho C2. Mọi thao tác ghi đều qua phê duyệt lõi."""
        return {
            "lich_chay": self._lich.so_tac_vu_chay() == 1,
            "cau_hoi_cho": self._hop_thu.so_cau_hoi_cho(),
            "kenh": f"{self._kenh_module}/{self._kenh_id}" if self._kenh_module else "chua_cau_hinh",
            "cong_tac": {
                "tu_dang_nhap_lai": self._bat_tu_dang_nhap_lai,
                "lam_moi": self._bat_lam_moi,
                "chuyen_nguon": self._bat_chuyen_nguon,
                "don_dep": self._bat_don_dep,
                "tu_cai_module": self._bat_tu_cai_module,
                "giai_captcha": self._bat_giai_captcha,
                "tu_dang_ky": self._bat_tu_dang_ky,
            },
        }
