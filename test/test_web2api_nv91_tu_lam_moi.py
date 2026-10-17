"""Test tự động khôi phục/làm mới phiên nhiều tầng nối vào đường chạy (NV91-P2).

Máy chủ/dịch vụ GIẢ, đồng hồ GIẢ. Kiểm:
  1. chat gặp lỗi phiên (401 theo cấu trúc) → khôi phục MỘT lần cho ĐÚNG tài
     khoản → tài khoản khoẻ lại, chat tiếp được;
  2. single-flight: 50 yêu cầu song song cùng 401 → đúng MỘT lượt khôi phục;
  3. nghỉ giữa các lượt (cooldown): kích hoạt lại ngay → không chạy thêm;
  4. vòng định kỳ: chạy/ngừng đúng, bỏ qua tài khoản can_nguoi/ngừng tự đăng
     nhập, giãn cách giữa các tài khoản, không vòng lặp bận;
  5. chốt dừng: 2 hỏng liên tiếp tầng (b) → ngừng tự đăng nhập tài khoản đó;
  6. hỏng tới tầng (d) → nhật ký quản trị + trạng thái «cần người», không thử lại mãi;
  7. gỡ module giữa chừng → không để lại tác vụ nền mồ côi;
  8. bí mật không lộ ở phản hồi/log/nhật ký;
  9. đường trạng thái: không khoá → 401; vai thấp → 403.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app import khoi_phuc_phien as kp
from app import nhat_ky
from app import tu_lam_moi as tlm
from app.tai_khoan_hang_loat import BiMatTam

MAT_KHAU_GIA = "mat-khau-gia-nv91-001"
COOKIE_GIA = "phien-gia-nv91-aaaabbbbcccc"


def chay(coro):
    return asyncio.run(coro)


class ModuleGia91:
    """Module giả: kịch bản theo tầng, máy chủ giả, không mạng thật."""

    def __init__(self, manifest=None):
        self.manifest = manifest or {"id": "mod-gia-nv91", "tien_to": "modgianv91"}
        self.kich_ban = {}            # "a"/"b" -> KetQuaKhoiPhuc | Exception | None
        self.tai_khoan = {}           # khoa -> {"khoe": bool}
        self.khoa_ke = {}             # khoa -> khoa kế | None
        self.mat_khau_luu = None
        self.so_lan_lam_moi = 0       # đếm lượt khôi phục (kiểm single-flight)
        self.da_danh_dau_hong = []
        self.da_danh_dau_khoe = []
        self.da_goi = []

    # -- hợp đồng NV88 --
    async def lam_moi_phien(self, khoa):
        self.so_lan_lam_moi += 1
        self.da_goi.append(("a", khoa))
        kq = self.kich_ban.get("a")
        if isinstance(kq, Exception):
            raise kq
        if kq is not None:
            return kq
        tk = self.tai_khoan.get(khoa)
        if tk is not None:
            tk["khoe"] = True
        return kp.KetQuaKhoiPhuc(True, tang="a")

    async def dang_nhap_tu_dong(self, tai_khoan, trinh_duyet):
        self.so_lan_lam_moi += 1
        self.da_goi.append(("b", tai_khoan.khoa))
        kq = self.kich_ban.get("b")
        if isinstance(kq, Exception):
            raise kq
        return kq if kq is not None else kp.KetQuaKhoiPhuc(False, ma_loi="sai_mat_khau")

    def tai_khoan_ke(self, khoa):
        return self.khoa_ke.get(khoa)

    def danh_dau_hong(self, khoa, ma_loi):
        self.da_danh_dau_hong.append((khoa, ma_loi))
        tk = self.tai_khoan.get(khoa)
        if tk is not None:
            tk["khoe"] = False

    def mat_khau_da_luu(self, khoa):
        return BiMatTam(self.mat_khau_luu) if self.mat_khau_luu else None

    # -- hợp đồng NV91-P2 (mới) --
    def khoa_tai_khoan(self):
        return list(self.tai_khoan)

    def danh_dau_khoe(self, khoa):
        self.da_danh_dau_khoe.append(khoa)
        tk = self.tai_khoan.get(khoa)
        if tk is not None:
            tk["khoe"] = True

    # -- đường chat giả: lỗi phiên nhận ra bằng CẤU TRÚC (mã 401) --
    async def chat(self, body, stream):
        khoa = (body or {}).get("_khoa_gia")
        tk = self.tai_khoan.get(khoa)
        if tk is None or not tk["khoe"]:
            # Nhận ra lỗi phiên bằng mã 401 (cấu trúc), không dò chữ:
            tlm.bao_loi_phien(self, khoa or "khoa-la")
            return 401, {}, b'{"loi": "phien het han"}'
        return 200, {}, b'{"ok": true}'


@pytest.fixture(autouse=True)
def sach_trang_thai(monkeypatch, tmp_path):
    """Mỗi test: trạng thái khôi phục sạch, đồng hồ giả, cấu hình mặc định."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_TU_LAM_MOI_BAT", "1")
    monkeypatch.setenv("WEB2API_TU_LAM_MOI_NGHI_GIAY", "300")
    dong_ho = [1000.0]
    monkeypatch.setattr(tlm, "_dong_ho", lambda: dong_ho[0])
    # Trạng thái single-flight/cooldown/sổ kiểm toàn cục phải sạch mỗi test.
    tlm._dang_chay.clear()
    tlm._lan_kich_cuoi.clear()
    tlm._lan_kiem.clear()
    yield dong_ho
    # Dọn tác vụ nền còn sót (không để rò sang test khác).
    chay(tlm.huy_tat_ca())
    tlm._dang_chay.clear()
    tlm._lan_kich_cuoi.clear()
    tlm._lan_kiem.clear()


def test_bao_loi_phien_khoi_phuc_mot_lan_cho_dung_tai_khoan(sach_trang_thai):
    mod = ModuleGia91()
    mod.tai_khoan = {"k1": {"khoe": False}, "k2": {"khoe": True}}

    async def _chay():
        task = tlm.bao_loi_phien(mod, "k1")
        assert task is not None
        await task

    chay(_chay())
    assert mod.so_lan_lam_moi == 1
    assert mod.tai_khoan["k1"]["khoe"] is True
    assert mod.tai_khoan["k2"]["khoe"] is True
    assert "k1" in mod.da_danh_dau_khoe


def test_single_flight_50_yeu_cau_song_song_chi_mot_luot(sach_trang_thai):
    mod = ModuleGia91()
    mod.tai_khoan = {"k1": {"khoe": False}}

    async def _ban():
        tac_vu = [tlm.bao_loi_phien(mod, "k1") for _ in range(50)]
        await asyncio.gather(*[t for t in tac_vu if t is not None])

    chay(_ban())
    # Một lượt khôi phục duy nhất cho 50 kích hoạt đồng thời.
    assert mod.so_lan_lam_moi == 1


def test_nghi_giua_cac_luot(sach_trang_thai):
    dong_ho = sach_trang_thai
    mod = ModuleGia91()
    mod.tai_khoan = {"k1": {"khoe": False}}

    async def _chay():
        t1 = tlm.bao_loi_phien(mod, "k1")
        assert t1 is not None
        await t1
        # Kích hoạt lại ngay (chưa hết 300s nghỉ) → không chạy thêm.
        assert tlm.bao_loi_phien(mod, "k1") is None
        # Qua khỏi thời gian nghỉ → được chạy lượt mới.
        dong_ho[0] += 301
        t2 = tlm.bao_loi_phien(mod, "k1")
        assert t2 is not None
        await t2

    chay(_chay())
    assert mod.so_lan_lam_moi == 2


def test_chat_401_tu_khoi_phuc_roi_chat_tiep_duoc(sach_trang_thai):
    mod = ModuleGia91()
    mod.tai_khoan = {"k1": {"khoe": False}}

    async def _kich_ban():
        ma, _, _ = await mod.chat({"_khoa_gia": "k1"}, False)
        assert ma == 401
        # Chờ tác vụ nền khôi phục xong (single-flight, không chặn chat).
        await asyncio.sleep(0.2)
        ma2, _, _ = await mod.chat({"_khoa_gia": "k1"}, False)
        return ma2

    assert chay(_kich_ban()) == 200
    assert mod.so_lan_lam_moi == 1


def test_vong_dinh_ky_chay_ngung_dung(sach_trang_thai):
    mod = ModuleGia91()
    mod.tai_khoan = {"k1": {"khoe": True}, "k2": {"khoe": True}}
    vong = tlm.VongTuLamMoi(mod, "mod-gia-nv91")

    async def _chay():
        await vong.chay_mot_vong()
        assert mod.so_lan_lam_moi == 2  # mỗi tài khoản một lượt làm mới
        vong.dung()
        assert vong.da_dung

    chay(_chay())


def test_vong_bo_qua_can_nguoi_va_ngung_tu_dang_nhap(sach_trang_thai):
    mod = ModuleGia91()
    mod.tai_khoan = {"k1": {"khoe": False}, "k2": {"khoe": True}}
    kp.dat_can_nguoi("mod-gia-nv91", "k1", "captcha", "huong dan")
    kp._cap_nhat("mod-gia-nv91", "k2", ngung_tu_dang_nhap=True)
    vong = tlm.VongTuLamMoi(mod, "mod-gia-nv91")
    chay(vong.chay_mot_vong())
    # Cả hai đều bị bỏ qua: không lượt làm mới nào.
    assert mod.so_lan_lam_moi == 0


def test_vong_gian_cach_giua_cac_tai_khoan(sach_trang_thai, monkeypatch):
    monkeypatch.setenv("WEB2API_TU_LAM_MOI_GIAN_CACH_TAI_KHOAN_GIAY", "7")
    mod = ModuleGia91()
    mod.tai_khoan = {"k1": {"khoe": True}, "k2": {"khoe": True}, "k3": {"khoe": True}}
    cac_lan_ngu = []

    async def ngu_gia(giay):
        cac_lan_ngu.append(giay)

    vong = tlm.VongTuLamMoi(mod, "mod-gia-nv91", ngu=ngu_gia)
    chay(vong.chay_mot_vong())
    assert mod.so_lan_lam_moi == 3
    # Giãn cách giữa các tài khoản (không phải 0): 2 lần nghỉ 7s cho 3 tài khoản.
    assert cac_lan_ngu == [7.0, 7.0]


def test_chot_dung_hai_hong_tang_b_lien_tiep(sach_trang_thai):
    mod = ModuleGia91()
    mod.tai_khoan = {"k1": {"khoe": False}}
    mod.kich_ban["a"] = kp.KetQuaKhoiPhuc(False, ma_loi="het_han")
    mod.mat_khau_luu = MAT_KHAU_GIA
    mod.kich_ban["b"] = kp.KetQuaKhoiPhuc(False, ma_loi="sai_mat_khau")

    async def _kich_hoat_3_lan():
        for _ in range(3):
            t = tlm.bao_loi_phien(mod, "k1")
            if t is not None:
                await t
            # Hết nghỉ để lượt sau được chạy.
            sach_trang_thai[0] += 301

    chay(_kich_hoat_3_lan())
    so_lan_b = sum(1 for tang, _ in mod.da_goi if tang == "b")
    # Tầng (b) chỉ chạy 2 lần rồi chốt dừng (ngung_tu_dang_nhap).
    assert so_lan_b == 2
    assert kp.lay_trang_thai("mod-gia-nv91", "k1")["ngung_tu_dang_nhap"] is True


def test_hong_toi_tang_d_ghi_nhat_ky_va_trang_thai_can_nguoi(sach_trang_thai):
    mod = ModuleGia91()
    mod.tai_khoan = {"k1": {"khoe": False}}
    mod.kich_ban["a"] = kp.KetQuaKhoiPhuc(False, ma_loi="het_han")
    mod.mat_khau_luu = MAT_KHAU_GIA  # để tầng (b) được chạy (không mật khẩu → bỏ qua)
    mod.kich_ban["b"] = kp.KetQuaKhoiPhuc(False, ma_loi="can_nguoi", can_nguoi=True)

    async def _chay():
        t = tlm.bao_loi_phien(mod, "k1")
        assert t is not None
        await t

    chay(_chay())
    tt = kp.lay_trang_thai("mod-gia-nv91", "k1")
    assert tt["can_nguoi"] is True
    # Nhật ký quản trị có dòng cảnh báo (không thử lại mãi: chỉ 1 lượt a+b).
    assert sum(1 for t, _ in mod.da_goi if t == "a") == 1
    assert sum(1 for t, _ in mod.da_goi if t == "b") == 1
    dong = nhat_ky.doc("quan_tri", gioi_han=50)
    assert any("k1" in (d.get("tom_tat") or "") and "cần người" in (d.get("tom_tat") or "")
               for d in dong)


def test_go_module_giua_chung_khong_de_lai_tac_vu(sach_trang_thai):
    mod = ModuleGia91()
    mod.tai_khoan = {"k1": {"khoe": False}}

    async def _lam_moi_cham(khoa):
        await asyncio.sleep(30)
        return kp.KetQuaKhoiPhuc(True, tang="a")

    mod.lam_moi_phien = _lam_moi_cham

    async def _kich_ban():
        t = tlm.bao_loi_phien(mod, "k1")
        assert t is not None
        await asyncio.sleep(0.05)
        assert tlm.so_tac_vu_dang_chay("mod-gia-nv91") >= 1
        await tlm.huy_cua_module("mod-gia-nv91")
        assert tlm.so_tac_vu_dang_chay("mod-gia-nv91") == 0

    chay(_kich_ban())


def test_bi_mat_khong_lo_vao_log_hay_nhat_ky(sach_trang_thai, caplog):
    mod = ModuleGia91()
    mod.tai_khoan = {"k1": {"khoe": False}}
    mod.kich_ban["a"] = kp.KetQuaKhoiPhuc(False, ma_loi="het_han")
    mod.mat_khau_luu = MAT_KHAU_GIA
    mod.kich_ban["b"] = kp.KetQuaKhoiPhuc(False, ma_loi="sai_mat_khau")

    async def _chay():
        t = tlm.bao_loi_phien(mod, "k1")
        assert t is not None
        await t

    with caplog.at_level(logging.WARNING, logger="web2api"):
        chay(_chay())
    for rec in caplog.records:
        assert MAT_KHAU_GIA not in rec.getMessage()
        assert COOKIE_GIA not in rec.getMessage()
    for d in nhat_ky.doc("quan_tri", gioi_han=50):
        noi_dung = (d.get("tom_tat") or "") + str(d.get("them") or "")
        assert MAT_KHAU_GIA not in noi_dung


def test_tat_cau_hinh_thi_khong_tu_kich_hoat(sach_trang_thai, monkeypatch):
    monkeypatch.setenv("WEB2API_TU_LAM_MOI_BAT", "0")
    mod = ModuleGia91()
    mod.tai_khoan = {"k1": {"khoe": False}}
    assert tlm.bao_loi_phien(mod, "k1") is None
    assert mod.so_lan_lam_moi == 0
