"""Test NV147 cho module tuvanhanh: bộ tự vận hành + kênh báo/hỏi một đường (port từ c2a).

Hợp đồng (từ viec-nv147.txt):
- Module `tuvanhanh` độc lập: bộ lập lịch chạy nền (khởi động trong nap/cai, dừng sạch
  trong dung(), không luồng mồ côi, hai lần nạp không hai lịch).
- (a) làm mới phiên/token định kỳ; (b) tự đăng nhập lại khi hết hạn qua hợp đồng đăng nhập
  chung, giãn cách WEB2API_DANG_NHAP_GIAN_CACH_GIAY, dừng sau 2 hỏng liên tiếp;
  (c) theo dõi sức khoẻ nguồn/model; (d) tự khôi phục module lỗi (lùi dần, tối đa N lần,
  báo một tin); (e) cảnh báo đĩa > 85% + dọn dữ liệu tạm của chính module.
- Kênh báo/hỏi một đường: gửi tin đánh số qua kenh_ra của lõi, nhận trả lời `cl <số>`
  (vai quản trị theo NGƯỜI GỬI). Đích nêu đích danh. Hàng đợi có trần, hết hạn.
  Không trả lời = mặc định AN TOÀN (không làm). Im lặng khi ổn.
- Công cụ C2 chỉ-đọc `tu_van_hanh_trang_thai`.

Quy tắc port: chép mã c2a đang chạy (services/hoi_giam_sat.py, web_session_scheduler.py,
solver_selfheal.py), đổi lớp kết nối + khuôn module, không dữ liệu nhà.

Không mạng thật, không sleep thật (đồng hồ giả). Chuỗi bí mật trong test đều GIẢ rõ ràng.
"""

from __future__ import annotations

import asyncio
import asyncio
import importlib.util
import json
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "tuvanhanh"
TEN_GOI = "tu_van_hanh_nv147_mod_test"


def _tai_goi():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC)]
    sys.modules[TEN_GOI] = goi
    for ten_file in ("hop_thu", "lich", "module"):
        spec = importlib.util.spec_from_file_location(
            f"{TEN_GOI}.{ten_file}", THU_MUC / f"{ten_file}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{TEN_GOI}.{ten_file}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{TEN_GOI}.module"]


mod_module = _tai_goi()
Module = mod_module.Module
hop_thu_mod = sys.modules[f"{TEN_GOI}.hop_thu"]
lich_mod = sys.modules[f"{TEN_GOI}.lich"]
HopThu = hop_thu_mod.HopThu
LichTrinh = lich_mod.LichTrinh

MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


class DongHoGia:
    """Đồng hồ giả cho test: không sleep thật."""
    def __init__(self, moc=1_000_000.0):
        self.moc = moc

    def __call__(self):
        return self.moc

    def tien(self, giay):
        self.moc += giay


# ───────────── hộp thư hỏi/đáp (port hoi_giam_sat.py) ─────────────

def test_hop_thu_dat_va_tra_loi():
    ht = HopThu(han_gio=6.0, lay_gio=DongHoGia())
    ma, text = ht.dat_cau_hoi("Có cài module X?", ["Có", "Không"])
    assert ma
    assert "1. Có" in text and "2. Không" in text
    ma2, tb = ht.tra_loi("cl 1")
    assert ma2 == ma
    assert "ghi nhận" in tb.lower() or "đã" in tb.lower()
    kq = ht.doc_tra_loi(ma)
    assert kq["tra_loi"] == "Có"


def test_hop_thu_khong_tien_to_thi_bo():
    ht = HopThu(lay_gio=DongHoGia())
    ht.dat_cau_hoi("Hỏi?", ["A"])
    ma, tb = ht.tra_loi("xin chào")
    assert ma is None  # không phải trả lời — tin đi tiếp như thường


def test_hop_thu_so_la_thi_bao_khong_hieu():
    ht = HopThu(lay_gio=DongHoGia())
    ht.dat_cau_hoi("Hỏi?", ["A", "B"])
    ma, tb = ht.tra_loi("cl 9")
    assert ma is None
    assert tb  # báo lại danh sách để chủ máy chọn lại


def test_hop_thu_het_han_thi_bo():
    dh = DongHoGia()
    ht = HopThu(han_gio=6.0, lay_gio=dh)
    ma, _ = ht.dat_cau_hoi("Hỏi?", ["A"])
    dh.tien(7 * 3600)
    assert ht.doc_tra_loi(ma) == {}
    ma2, tb = ht.tra_loi("cl 1")
    assert ma2 is None
    assert "quá hạn" in tb


def test_hop_thu_khong_tra_loi_mac_dinh_an_toan():
    ht = HopThu(lay_gio=DongHoGia())
    ma, _ = ht.dat_cau_hoi("Có xoá dữ liệu?", ["Có", "Không"])
    assert ht.doc_tra_loi(ma) == {}  # rỗng = chưa trả lời = KHÔNG làm gì


def test_cong_tac_rui_ro_mac_dinh_tat():
    """Spec: tự động BẬT; thứ rủi ro (tự cài module, giải CAPTCHA, tự đăng ký) TẮT."""
    async def _chay():
        mod = Module(MANIFEST)
        mod.nap({})
        assert mod._bat_tu_dang_nhap_lai is True   # tự động: bật
        assert mod._bat_lam_moi is True
        assert mod._bat_tu_cai_module is False     # rủi ro: tắt
        assert mod._bat_giai_captcha is False       # rủi ro: tắt
        assert mod._bat_tu_dang_ky is False         # rủi ro: tắt
        mod2 = Module(MANIFEST)
        mod2.nap({"bat_tu_cai_module": "1"})
        assert mod2._bat_tu_cai_module is True     # bật tường minh vẫn được
        await mod.dung(); await mod2.dung()
    asyncio.run(_chay())


def test_hop_thu_tran_hang_doi():
    ht = HopThu(tran_hang_doi=2, lay_gio=DongHoGia())
    ht.dat_cau_hoi("Q1?", ["A"])
    ht.dat_cau_hoi("Q2?", ["A"])
    ma3, _ = ht.dat_cau_hoi("Q3?", ["A"])
    assert ma3  # vẫn nhận nhưng hàng đợi không vượt trần
    assert ht.so_cau_hoi_cho() <= 2


# ───────────── bộ lập lịch (port web_session_scheduler.py) ─────────────

def test_lich_hai_lan_bat_dau_chi_mot():
    dh = DongHoGia()
    lich = LichTrinh(lay_gio=dh)
    dem = {"n": 0}

    async def nv():
        dem["n"] += 1

    lich.them("dem", 60.0, nv)
    lich.bat_dau()
    lich.bat_dau()
    assert lich.so_tac_vu_chay() <= 1


def test_lich_dung_sach():
    async def _chay():
        lich = LichTrinh(lay_gio=DongHoGia())
        lich.them("x", 60.0, lambda: asyncio.sleep(0))
        lich.bat_dau()
        await lich.dung()
        await lich.dung()  # idempotent
        assert lich.so_tac_vu_chay() == 0
    asyncio.run(_chay())


def test_lich_chay_mot_vong_theo_dong_ho_gia():
    async def _chay():
        dh = DongHoGia()
        lich = LichTrinh(lay_gio=dh)
        dem = {"n": 0}

        async def nv():
            dem["n"] += 1

        lich.them("dem", 60.0, nv)
        await lich.chay_mot_vong()
        assert dem["n"] == 1
        await lich.chay_mot_vong()  # chưa tới chu kỳ
        assert dem["n"] == 1
        dh.tien(61)
        await lich.chay_mot_vong()
        assert dem["n"] == 2
        await lich.dung()
    asyncio.run(_chay())


# ───────────── tự đăng nhập lại (hợp đồng đăng nhập chung) ─────────────

class NhaCungCapGia:
    """Giả module provider có hợp đồng tai_dung_ho_so."""
    def __init__(self, che_do="het_han_roi_ok"):
        self.che_do = che_do  # het_han_roi_ok | hong_mai
        self.so_lan_dang_nhap = 0
        self.phien_het_han = True

    async def tai_dung_ho_so(self, ho_so, trinh_duyet=None):
        self.so_lan_dang_nhap += 1
        if self.che_do == "hong_mai":
            return {"ok": False, "ma": "sai_mat_khau"}
        self.phien_het_han = False
        return {"ok": True}


def test_dang_nhap_lai_khi_het_han():
    async def _chay():
        mod = Module(MANIFEST, lay_gio=DongHoGia())
        mod.nap({"kenh_module": "kenh-gia", "kenh_id": "d1"})
        ncc = NhaCungCapGia("het_han_roi_ok")
        kq = await mod._dang_nhap_lai_mot_nha(ncc, "ho-so-1")
        assert kq["ok"] is True
        assert ncc.so_lan_dang_nhap == 1
        await mod.dung()
    asyncio.run(_chay())


def test_dang_nhap_hong_2_lan_lien_tiep_thi_dung_va_bao_mot_tin():
    async def _chay():
        dh = DongHoGia()
        mod = Module(MANIFEST, lay_gio=dh)
        mod.nap({"kenh_module": "kenh-gia", "kenh_id": "d1"})
        ncc = NhaCungCapGia("hong_mai")
        bao = []

        async def _bao(t):
            bao.append(t)
            return True
        mod._gui_canh_bao_noi_bo = _bao
        r1 = await mod._dang_nhap_lai_mot_nha(ncc, "ho-so-1")
        dh.tien(301)
        r2 = await mod._dang_nhap_lai_mot_nha(ncc, "ho-so-1")
        dh.tien(301)
        r3 = await mod._dang_nhap_lai_mot_nha(ncc, "ho-so-1")
        assert r1["ok"] is False and r2["ok"] is False
        assert r3.get("bo_qua") is True  # dừng cả hàng sau 2 hỏng liên tiếp
        assert ncc.so_lan_dang_nhap == 2
        assert len(bao) == 1  # báo đúng MỘT tin
        await mod.dung()
    asyncio.run(_chay())


# ───────────── lệnh cl (vai quản trị theo người gửi) ─────────────

def test_lenh_cl_quan_tri_tra_loi_duoc():
    async def _chay():
        mod = Module(MANIFEST)
        mod.nap({"kenh_module": "kenh-gia", "kenh_id": "d1"})
        ma, _ = mod._hop_thu.dat_cau_hoi("Cài?", ["Có", "Không"])
        ngu_canh = {"nguoi_gui": "admin-1", "vai": "quan_tri", "noi_gui": "d1", "app": None}
        kq = await mod._xu_ly_cl(ngu_canh, "1")
        assert "ghi nhận" in kq.lower() or "đã" in kq.lower()
        assert mod._hop_thu.doc_tra_loi(ma)["tra_loi"] == "Có"
        await mod.dung()
    asyncio.run(_chay())


def test_lenh_cl_nguoi_la_bi_bo():
    async def _chay():
        mod = Module(MANIFEST)
        mod.nap({"kenh_module": "kenh-gia", "kenh_id": "d1"})
        ma, _ = mod._hop_thu.dat_cau_hoi("Cài?", ["Có", "Không"])
        ngu_canh = {"nguoi_gui": "la-9", "vai": "la", "noi_gui": "d1", "app": None}
        kq = await mod._xu_ly_cl(ngu_canh, "1")
        assert mod._hop_thu.doc_tra_loi(ma) == {}  # không ghi nhận
        assert kq == "" or "quyền" in kq.lower()
        await mod.dung()
    asyncio.run(_chay())


# ───────────── hợp đồng module ─────────────

def test_manifest_hop_le():
    from app.kham_pha import kiem_manifest
    assert kiem_manifest(MANIFEST) is None
    assert MANIFEST["id"] == "tuvanhanh"


def test_suc_khoe_va_dung_la_async():
    async def _chay():
        mod = Module(MANIFEST)
        mod.nap({})
        assert asyncio.iscoroutinefunction(mod.suc_khoe)
        assert asyncio.iscoroutinefunction(mod.dung)
        sk = await mod.suc_khoe()
        assert sk["ok"] is True
        assert "bi_mat" not in json.dumps(sk, ensure_ascii=False).lower()
        await mod.dung()
    asyncio.run(_chay())


def test_cai_tu_choi_cau_hinh_sai():
    async def _chay():
        mod = Module(MANIFEST)
        kq = await mod.cai({"kenh_module": 123})
        assert kq["ok"] is False
        await mod.dung()
    asyncio.run(_chay())


def test_cong_cu_chi_doc():
    mod = Module(MANIFEST)
    mod.nap({})
    tt = mod.tu_van_hanh_trang_thai()
    assert isinstance(tt, dict)
    assert "lich_chay" in tt


def test_bi_mat_khong_vao_log(caplog):
    import logging
    mod = Module(MANIFEST)
    with caplog.at_level(logging.INFO):
        mod.nap({"kenh_module": "kenh-gia", "kenh_id": "d1",
                 "khoa_bi_mat_gia": "MAT-KHAU-GIA-123"})
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "MAT-KHAU-GIA-123" not in text


def test_duong_kenh_vao_lenh_cl():
    """Đi qua kenh_vao.nhan_tin thật với lệnh cl đã đăng ký (không chỉ gọi thẳng hàm)."""
    async def _chay():
        from app import kenh_vao
        from app.main import app as app_obj
        mod = Module(MANIFEST)
        mod.nap({"kenh_module": "kenh-gia", "kenh_id": "d1"})
        ma, _ = mod._hop_thu.dat_cau_hoi("Cài?", ["Có", "Không"])
        await kenh_vao.cap_quyen("tuvanhanh", "admin-1", "quan_tri", "Admin")
        tin = {
            "id_tin": "nv147-t1",
            "nguoi_gui": "admin-1",
            "noi_gui": "d1",
            "la_nhom": False,
            "ten_tu_khai": "Admin",
            "van_ban": "cl 2",
            "luc": None,
        }
        kq = await kenh_vao.nhan_tin(app_obj, "tuvanhanh", tin)
        assert kq.get("ok") is True
        assert mod._hop_thu.doc_tra_loi(ma)["tra_loi"] == "Không"
        await mod.dung()
    asyncio.run(_chay())
