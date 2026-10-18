"""Kiểm thử đăng nhập tự động kiểu danh tính (NV101).

Bao quát 12 nhóm yêu cầu:
1. Hợp đồng (kieu hợp lệ / không hợp lệ / truc_tiep).
2. Đường vui danh_tinh (A3).
3. Bốn trạng thái danh tính / hồ sơ (A1, A2, A3, A4) & hai dòng khác email.
4. Thiếu điều kiện -> 400/503 đồng bộ, không đổi dữ liệu.
5. Chặn thử thách (can_nguoi, mã lạ, mã sai khuôn, sai_mat_khau).
6. 2FA (cột 3, cho_2fa, kieu truc_tiep).
7. Bí mật (không lọt phản hồi, trang-thai, log, nhật ký, kho danh tính).
8. Leo thang quyền & IDOR (401, 403, bỏ qua id/ho_so ngoài, 404 tai-dung).
9. Đang có lượt chạy -> 409 da_co_luot_chay.
10. Lõi chạy khi không có module nào, 404 module chưa cài, cờ tai_dung_ho_so.
12. Kiểu danh_tinh với dòng CHỈ CÓ EMAIL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import danh_tinh, khoa_khach, khoi_phuc_phien, so_module
from app.main import app
from app.tai_khoan_hang_loat import BiMatTam, KetQuaDangNhap

KHOA_QT = "AIzaSyTOT-mot-000000000000000000000001"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
HO_SO_CHUAN_1 = "aa" * 16
HO_SO_CHUAN_2 = "bb" * 16
MAT_KHAU_GIA = "mat-khau-gia-001"
COOKIE_GIA = "cookie-gia-nv101-session"


@dataclass
class KetQuaDangNhapGoogleGia:
    thanh_cong: bool
    can_nguoi: bool = False
    cho_2fa: bool = False
    ma_loi: str = ""
    buoc: str = ""


class SoGia:
    def __init__(self):
        self.dang_chay: dict[str, Any] = {}
        self.catalog: dict[str, Any] = {}
        self.cai_goi: list[tuple[str, dict]] = []
        self.tu_xa: list[str] = []
        self.mui_gio = None

    def the_hien_cho_dang_nhap(self, id_: str):
        return self.dang_chay.get(id_)

    def tien_to_cua(self, module_id: str) -> str | None:
        return "gia" if module_id in self.dang_chay or module_id in self.catalog else None

    async def cai(self, id_: str, cau_hinh: dict) -> dict:
        self.cai_goi.append((id_, cau_hinh))
        return {"ok": True, "ket_qua_tung_khoa": [], "loi": None}

    def tinh_nang(self) -> dict:
        return so_module.SoModule.tinh_nang(self)


class DieuKhienGia(khoi_phuc_phien.BoDieuKhienTrinhDuyet):
    def __init__(self, ho_so: str):
        self.ho_so = ho_so
        self.da_dong = False

    async def mo_trang(self, url: str) -> None:
        pass

    async def dien(self, bo_chon: str, gia_tri: str) -> None:
        pass

    async def bam(self, bo_chon: str) -> None:
        pass

    async def doc_cookie(self, mien: str) -> dict:
        return {}

    async def tin_hieu(self, quy_tac: list | None = None) -> set:
        return set()

    async def dong(self):
        self.da_dong = True


class ModTrinhDuyetGia:
    def __init__(self):
        self.manifest = {"id": "mod-trinh-duyet-gia", "cung_cap": ["trinh_duyet"]}
        self.ho_so_tao_lan_luot = [HO_SO_CHUAN_1, HO_SO_CHUAN_2]
        self.da_tao: list[str] = []
        self.da_dong: list[str] = []
        self.nem_khi_tao = False

    async def trinh_duyet_tao(self, ten=None) -> dict:
        if self.nem_khi_tao:
            raise RuntimeError("Lỗi tạo trình duyệt giả")
        hs = self.ho_so_tao_lan_luot.pop(0) if self.ho_so_tao_lan_luot else "cc" * 16
        self.da_tao.append(hs)
        return {"ho_so": hs}

    async def trinh_duyet_dong(self, ho_so: str, giu_phien: bool = False):
        self.da_dong.append(ho_so)

    def trinh_duyet_that(self, ho_so: str):
        return DieuKhienGia(ho_so)


class ModDangNhapDanhTinhGia:
    def __init__(self):
        self.manifest = {"id": "mod-dn-danh-tinh-gia", "cung_cap": ["dang_nhap_danh_tinh"]}
        self.da_nhan: list[dict[str, Any]] = []
        self.tra_ve: Any = KetQuaDangNhapGoogleGia(True)
        self.cho_event: asyncio.Event | None = None

    async def dang_nhap(self, ho_so: str, email: str, mat_khau: Any, trinh_duyet: Any, ma_2fa: Any = None) -> Any:
        mk_str = mat_khau.lay() if isinstance(mat_khau, BiMatTam) else (mat_khau if isinstance(mat_khau, str) else None)
        totp_str = ma_2fa.lay() if isinstance(ma_2fa, BiMatTam) else (ma_2fa if isinstance(ma_2fa, str) else None)
        self.da_nhan.append({
            "ho_so": ho_so,
            "email": email,
            "mat_khau": mk_str,
            "mat_khau_is_none": mat_khau is None,
            "ma_2fa": totp_str,
            "ma_2fa_is_none": ma_2fa is None,
        })
        if self.cho_event is not None:
            await self.cho_event.wait()
        return self.tra_ve


class ModProviderGia:
    def __init__(self, co_tai_dung: bool = True, co_dang_nhap: bool = False, co_truong_bi_mat: bool = True):
        self.manifest: dict[str, Any] = {
            "id": "mod-provider-gia",
            "cung_cap": ["chat"],
            "truong_cau_hinh": [{"khoa": "cookies", "kieu": "danh_sach_bi_mat"}] if co_truong_bi_mat else [],
        }
        self.co_tai_dung = co_tai_dung
        self.co_dang_nhap = co_dang_nhap
        self.tra_ve: KetQuaDangNhap = KetQuaDangNhap(True, cookie=COOKIE_GIA)
        self.da_nhan_tai_dung: list[tuple[str, Any]] = []
        self.da_nhan_dang_nhap: list[Any] = []

    async def tai_dung_ho_so(self, ho_so: str, trinh_duyet: Any) -> KetQuaDangNhap:
        self.da_nhan_tai_dung.append((ho_so, trinh_duyet))
        return self.tra_ve

    async def dang_nhap(self, tk: Any) -> KetQuaDangNhap:
        self.da_nhan_dang_nhap.append(tk)
        return self.tra_ve


@pytest.fixture
def env_gia(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")

    so = SoGia()
    mod_td = ModTrinhDuyetGia()
    mod_dn = ModDangNhapDanhTinhGia()
    mod_pv = ModProviderGia()

    so.dang_chay[mod_td.manifest["id"]] = mod_td
    so.dang_chay[mod_dn.manifest["id"]] = mod_dn
    so.dang_chay[mod_pv.manifest["id"]] = mod_pv

    with TestClient(app) as client:
        monkeypatch.setattr(client.app.state, "so", so)
        yield {
            "client": client,
            "so": so,
            "mod_td": mod_td,
            "mod_dn": mod_dn,
            "mod_pv": mod_pv,
            "tmp_path": tmp_path,
        }


def _cho_luot_chay_xong(client: TestClient, timeout: float = 3.0) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        r = client.get("/api/tai-khoan/dang-nhap-hang-loat/trang-thai", headers=QT)
        assert r.status_code == 200
        j = r.json()
        if j.get("trang_thai_luot") == "hoan_thanh":
            return j
        time.sleep(0.05)
    pytest.fail("Lượt chạy không hoàn thành trong thời gian quy định")


# ── 1. HỢP ĐỒNG ─────────────────────────────────────────────────────────────

def test_hop_dong_kieu_sai_thi_400(env_gia):
    client = env_gia["client"]
    for sai in ["xyz", 123, [], {}]:
        r = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
            "module_id": "mod-provider-gia",
            "kieu": sai,
            "van_ban": f"test@example.com|{MAT_KHAU_GIA}",
        })
        assert r.status_code == 400
        assert r.json().get("error", {}).get("code") == "invalid_value"
    # Không tạo danh tính nào
    assert len(danh_tinh.danh_sach()) == 0


def test_hop_dong_vang_kieu_va_truc_tiep_dung_hanh_vi_cu(env_gia):
    client = env_gia["client"]
    mod_pv = env_gia["mod_pv"]
    mod_pv.co_dang_nhap = True
    mod_pv.dang_nhap = mod_pv.dang_nhap  # gán hàm

    for k in [None, "truc_tiep"]:
        body = {"module_id": "mod-provider-gia", "van_ban": f"u_{k}@example.com|{MAT_KHAU_GIA}"}
        if k is not None:
            body["kieu"] = k
        r = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json=body)
        assert r.status_code == 200
        _cho_luot_chay_xong(client)

    # Provider.dang_nhap được gọi, tai_dung_ho_so KHÔNG được gọi
    assert len(mod_pv.da_nhan_dang_nhap) == 2
    assert len(mod_pv.da_nhan_tai_dung) == 0
    # Không tạo danh tính
    assert len(danh_tinh.danh_sach()) == 0


# ── 2. ĐƯỜNG VUI DANH_TINH (A3) ─────────────────────────────────────────────

def test_duong_vui_danh_tinh_a3(env_gia):
    client = env_gia["client"]
    so = env_gia["so"]
    mod_dn = env_gia["mod_dn"]
    mod_pv = env_gia["mod_pv"]

    r = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia",
        "kieu": "danh_tinh",
        "van_ban": f"happy@example.com|{MAT_KHAU_GIA}",
    })
    assert r.status_code == 200
    thong_tin = r.json()
    assert thong_tin["tong"] == 1

    tt = _cho_luot_chay_xong(client)
    kq0 = tt["ket_qua"][0]
    assert kq0["email"] == "happy@example.com"
    assert kq0["trang_thai"] == "xong"

    # Danh tính được tạo và gắn provider
    dt = danh_tinh.tim_theo_email("happy@example.com")
    assert dt is not None
    assert dt["trang_thai_dang_nhap"] == "dang_nhap"
    assert dt["ho_so_trinh_duyet"] == HO_SO_CHUAN_1
    assert "mod-provider-gia" in dt["gan"]

    # so.cai được gọi ĐÚNG MỘT lần với chứng chỉ vào đúng trường bí mật
    assert len(so.cai_goi) == 1
    mod_id, cau_hinh = so.cai_goi[0]
    assert mod_id == "mod-provider-gia"
    assert cau_hinh == {"cookies": {"them": [COOKIE_GIA]}}

    # Phản hồi / trang-thai không chứa chứng chỉ
    assert COOKIE_GIA not in json.dumps(thong_tin)
    assert COOKIE_GIA not in json.dumps(tt)


# ── 3. BỐN TRẠNG THÁI A1–A4 ────────────────────────────────────────────────

def test_trang_thai_a1_dung_lai_ho_so_co_san(env_gia):
    client = env_gia["client"]
    mod_td = env_gia["mod_td"]
    # Tạo sẵn danh tính và hồ sơ
    dt = danh_tinh.tao("a1@example.com")
    danh_tinh.cap_nhat_trang_thai(dt["id"], ho_so=HO_SO_CHUAN_1, trang_thai="dang_nhap")

    r = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia",
        "kieu": "danh_tinh",
        "van_ban": f"a1@example.com|{MAT_KHAU_GIA}",
    })
    assert r.status_code == 200
    tt = _cho_luot_chay_xong(client)
    assert tt["ket_qua"][0]["trang_thai"] == "xong"

    # Không gọi trinh_duyet_tao
    assert len(mod_td.da_tao) == 0


def test_trang_thai_a2_co_danh_tinh_chua_co_ho_so(env_gia):
    client = env_gia["client"]
    mod_td = env_gia["mod_td"]
    # Tạo sẵn danh tính nhưng ho_so_trinh_duyet=""
    dt = danh_tinh.tao("a2@example.com")
    assert dt["ho_so_trinh_duyet"] == ""

    r = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia",
        "kieu": "danh_tinh",
        "van_ban": f"a2@example.com|{MAT_KHAU_GIA}",
    })
    assert r.status_code == 200
    tt = _cho_luot_chay_xong(client)
    assert tt["ket_qua"][0]["trang_thai"] == "xong"

    # Hồ sơ được tạo và gắn vào danh tính
    assert len(mod_td.da_tao) == 1
    dt_sau = danh_tinh.lay(dt["id"])
    assert dt_sau["ho_so_trinh_duyet"] == HO_SO_CHUAN_1


def test_trang_thai_a4_danh_tinh_co_san_hong_thi_giu_nguyen(env_gia):
    client = env_gia["client"]
    mod_dn = env_gia["mod_dn"]
    mod_dn.tra_ve = KetQuaDangNhapGoogleGia(False, ma_loi="sai_mat_khau")

    dt = danh_tinh.tao("a4_co_san@example.com")
    danh_tinh.cap_nhat_trang_thai(dt["id"], ho_so=HO_SO_CHUAN_1, trang_thai="dang_nhap")

    r = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia",
        "kieu": "danh_tinh",
        "van_ban": f"a4_co_san@example.com|{MAT_KHAU_GIA}",
    })
    assert r.status_code == 200
    tt = _cho_luot_chay_xong(client)
    assert tt["ket_qua"][0]["trang_thai"] == "loi"

    # Danh tính và hồ sơ VẪN NGUYÊN
    dt_sau = danh_tinh.lay(dt["id"])
    assert dt_sau is not None
    assert dt_sau["ho_so_trinh_duyet"] == HO_SO_CHUAN_1


def test_trang_thai_a4_danh_tinh_moi_tao_hong_som_thi_bi_don(env_gia):
    client = env_gia["client"]
    mod_td = env_gia["mod_td"]
    mod_td.nem_khi_tao = True  # lỗi sớm khi tạo hồ sơ

    r = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia",
        "kieu": "danh_tinh",
        "van_ban": f"a4_moi@example.com|{MAT_KHAU_GIA}",
    })
    assert r.status_code == 200
    tt = _cho_luot_chay_xong(client)
    assert tt["ket_qua"][0]["trang_thai"] == "loi"

    # Danh tính bị dọn dẹp, thử lại cùng email không bị lỗi tồn tại
    assert danh_tinh.tim_theo_email("a4_moi@example.com") is None
    # Thử lại cùng email tạo được bình thường
    danh_tinh.tao("a4_moi@example.com")


def test_hai_dong_khac_email_dung_dung_ho_so_rieng(env_gia):
    client = env_gia["client"]
    mod_dn = env_gia["mod_dn"]

    r = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia",
        "kieu": "danh_tinh",
        "van_ban": f"user1@example.com|mk1\nuser2@example.com|mk2",
    })
    assert r.status_code == 200
    tt = _cho_luot_chay_xong(client)
    assert tt["tom_tat"]["xong"] == 2

    assert len(mod_dn.da_nhan) == 2
    assert mod_dn.da_nhan[0]["email"] == "user1@example.com"
    assert mod_dn.da_nhan[0]["ho_so"] == HO_SO_CHUAN_1
    assert mod_dn.da_nhan[1]["email"] == "user2@example.com"
    assert mod_dn.da_nhan[1]["ho_so"] == HO_SO_CHUAN_2


# ── 4. THIẾU ĐIỀU KIỆN ──────────────────────────────────────────────────────

def test_thieu_dieu_kien_400_503(env_gia):
    client = env_gia["client"]
    so = env_gia["so"]
    mod_pv = env_gia["mod_pv"]

    # 1. Thiếu module đăng nhập danh tính -> 400 thieu_module_dang_nhap_danh_tinh
    del so.dang_chay["mod-dn-danh-tinh-gia"]
    r1 = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia", "kieu": "danh_tinh", "van_ban": "u@example.com|p",
    })
    assert r1.status_code == 400
    assert r1.json().get("error", {}).get("code") == "thieu_module_dang_nhap_danh_tinh"

    # Phục hồi mod_dn
    so.dang_chay["mod-dn-danh-tinh-gia"] = env_gia["mod_dn"]

    # 2. Provider không có tai_dung_ho_so -> 400 thieu_module_dang_nhap_danh_tinh
    mod_pv.tai_dung_ho_so = None
    r2 = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia", "kieu": "danh_tinh", "van_ban": "u@example.com|p",
    })
    assert r2.status_code == 400
    assert r2.json().get("error", {}).get("code") == "thieu_module_dang_nhap_danh_tinh"

    # Phục hồi tai_dung_ho_so
    mod_pv.tai_dung_ho_so = ModProviderGia.tai_dung_ho_so.__get__(mod_pv, ModProviderGia)

    # 3. Provider không khai trường bí mật -> 400 module_chua_ho_tro
    mod_pv.manifest["truong_cau_hinh"] = []
    r3 = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia", "kieu": "danh_tinh", "van_ban": "u@example.com|p",
    })
    assert r3.status_code == 400
    assert r3.json().get("error", {}).get("code") == "module_chua_ho_tro"

    # Phục hồi trường bí mật
    mod_pv.manifest["truong_cau_hinh"] = [{"khoa": "cookies", "kieu": "danh_sach_bi_mat"}]

    # 4. Thiếu module trình duyệt -> 503 chua_co_trinh_duyet
    del so.dang_chay["mod-trinh-duyet-gia"]
    r4 = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia", "kieu": "danh_tinh", "van_ban": "u@example.com|p",
    })
    assert r4.status_code == 503
    assert r4.json().get("error", {}).get("code") == "chua_co_trinh_duyet"

    # Snapshot không đổi
    assert len(danh_tinh.danh_sach()) == 0
    assert len(so.cai_goi) == 0


# ── 5. CHẶN THỬ THÁCH ───────────────────────────────────────────────────────

def test_chan_thu_thach_can_nguoi_va_ma_la(env_gia):
    client = env_gia["client"]
    mod_dn = env_gia["mod_dn"]
    mod_pv = env_gia["mod_pv"]

    # Module đăng nhập trả can_nguoi=True
    mod_dn.tra_ve = KetQuaDangNhapGoogleGia(False, can_nguoi=True)
    r = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia", "kieu": "danh_tinh", "van_ban": "cn@example.com|mk",
    })
    assert r.status_code == 200
    tt = _cho_luot_chay_xong(client)
    kq = tt["ket_qua"][0]
    assert kq["trang_thai"] == "loi"
    assert kq["ma_loi"] == "can_nguoi"
    assert len(mod_pv.da_nhan_tai_dung) == 0

    dt = danh_tinh.tim_theo_email("cn@example.com")
    assert dt["trang_thai_dang_nhap"] == "can_nguoi"

    # Provider giả trả can_nguoi ở bước C
    mod_dn.tra_ve = KetQuaDangNhapGoogleGia(True)
    mod_pv.tra_ve = KetQuaDangNhap(False, ma_loi="can_nguoi")
    r2 = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia", "kieu": "danh_tinh", "van_ban": "cn2@example.com|mk",
    })
    assert r2.status_code == 200
    tt2 = _cho_luot_chay_xong(client)
    assert tt2["ket_qua"][0]["ma_loi"] == "can_nguoi"

    # Mã lạ đi nguyên ra trang-thai
    mod_pv.tra_ve = KetQuaDangNhap(False, ma_loi="ma_la_xyz")
    r3 = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia", "kieu": "danh_tinh", "van_ban": "mala@example.com|mk",
    })
    assert r3.status_code == 200
    tt3 = _cho_luot_chay_xong(client)
    assert tt3["ket_qua"][0]["ma_loi"] == "ma_la_xyz"

    # Mã sai khuôn -> loi_module
    mod_pv.tra_ve = KetQuaDangNhap(False, ma_loi="MA HOA CO DAU CACH")
    r4 = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia", "kieu": "danh_tinh", "van_ban": "saikhuon@example.com|mk",
    })
    assert r4.status_code == 200
    tt4 = _cho_luot_chay_xong(client)
    assert tt4["ket_qua"][0]["ma_loi"] == "loi_module"

    # Module trả sai_mat_khau đi thẳng qua
    mod_dn.tra_ve = KetQuaDangNhapGoogleGia(False, ma_loi="sai_mat_khau")
    r5 = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia", "kieu": "danh_tinh", "van_ban": "saimk@example.com|mk",
    })
    assert r5.status_code == 200
    tt5 = _cho_luot_chay_xong(client)
    assert tt5["ket_qua"][0]["ma_loi"] == "sai_mat_khau"


# ── 6. 2FA ──────────────────────────────────────────────────────────────────

def test_2fa_truyen_ma_va_cho_2fa(env_gia):
    client = env_gia["client"]
    mod_dn = env_gia["mod_dn"]

    # Có cột 3 -> ma_2fa đúng giá trị
    r = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia", "kieu": "danh_tinh", "van_ban": "totp@example.com|mk|654321",
    })
    assert r.status_code == 200
    _cho_luot_chay_xong(client)
    assert mod_dn.da_nhan[-1]["ma_2fa"] == "654321"

    # Không có cột 3 -> ma_2fa is None
    r2 = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia", "kieu": "danh_tinh", "van_ban": "nototp@example.com|mk",
    })
    assert r2.status_code == 200
    _cho_luot_chay_xong(client)
    assert mod_dn.da_nhan[-1]["ma_2fa_is_none"] is True

    # Module trả cho_2fa -> dòng loi ma_loi="cho_2fa"
    mod_dn.tra_ve = KetQuaDangNhapGoogleGia(False, cho_2fa=True, ma_loi="cho_2fa")
    r3 = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia", "kieu": "danh_tinh", "van_ban": "wait2fa@example.com|mk",
    })
    assert r3.status_code == 200
    tt3 = _cho_luot_chay_xong(client)
    assert tt3["ket_qua"][0]["ma_loi"] == "cho_2fa"


# ── 7. BẢO VỆ BÍ MẬT ───────────────────────────────────────────────────────

def test_bi_mat_khong_lo_o_moi_noi(env_gia, caplog):
    client = env_gia["client"]
    caplog.set_level(logging.DEBUG)

    van_ban = f"secret@example.com|matkhau_sieu_bi_mat_001|totp_789012"
    r = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia", "kieu": "danh_tinh", "van_ban": van_ban,
    })
    assert r.status_code == 200
    thong_tin = r.text
    assert "matkhau_sieu_bi_mat_001" not in thong_tin
    assert "totp_789012" not in thong_tin

    tt = _cho_luot_chay_xong(client)
    tt_str = json.dumps(tt)
    assert "matkhau_sieu_bi_mat_001" not in tt_str
    assert "totp_789012" not in tt_str
    assert COOKIE_GIA not in tt_str

    # caplog không có mật khẩu / TOTP
    all_logs = " ".join(rec.message for rec in caplog.records)
    assert "matkhau_sieu_bi_mat_001" not in all_logs
    assert "totp_789012" not in all_logs

    # Nhật ký không lộ bí mật
    r_nk = client.get("/api/nhat-ky", headers=QT)
    if r_nk.status_code == 200:
        nk_text = r_nk.text
        assert "matkhau_sieu_bi_mat_001" not in nk_text
        assert "totp_789012" not in nk_text

    # Kho danh tính không lộ mật khẩu/cookie
    r_dt = client.get("/api/danh-tinh", headers=QT)
    assert r_dt.status_code == 200
    dt_text = r_dt.text
    assert "matkhau_sieu_bi_mat_001" not in dt_text
    assert "totp_789012" not in dt_text
    assert COOKIE_GIA not in dt_text


# ── 8. LEO THANG QUYỀN & IDOR ──────────────────────────────────────────────

def test_leo_thang_quyen_va_idor(env_gia):
    client = env_gia["client"]

    # 1. Không khoá -> 401
    r1 = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", json={
        "module_id": "mod-provider-gia", "kieu": "danh_tinh", "van_ban": "idor@example.com|mk",
    })
    assert r1.status_code == 401

    # 2. Khoá khách -> 403
    _, khoa_khach_tho = khoa_khach.tao_khoa(nhan="khach-nv101", pham_vi=["chat"])
    r2 = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau",
                     headers={"Authorization": f"Bearer {khoa_khach_tho}"},
                     json={"module_id": "mod-provider-gia", "kieu": "danh_tinh", "van_ban": "idor@example.com|mk"})
    assert r2.status_code == 403

    # 3. Kèm id / ho_so của người khác trong thân -> bị BỎ QUA
    r3 = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia",
        "kieu": "danh_tinh",
        "van_ban": "legit@example.com|mk",
        "ho_so": "victim_ho_so_12345678",
        "danh_tinh_id": "dt-victim-9999",
    })
    assert r3.status_code == 200
    _cho_luot_chay_xong(client)
    dt = danh_tinh.tim_theo_email("legit@example.com")
    assert dt["ho_so_trinh_duyet"] == HO_SO_CHUAN_1


# ── 9. ĐANG CÓ LƯỢT CHẠY (409) ─────────────────────────────────────────────

def test_dang_co_luot_chay_409(env_gia):
    client = env_gia["client"]
    mod_dn = env_gia["mod_dn"]
    event = asyncio.Event()
    mod_dn.cho_event = event

    # Bắt đầu lượt 1 (sẽ dừng lại ở mod_dn)
    r1 = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia", "kieu": "danh_tinh", "van_ban": "luot1@example.com|mk",
    })
    assert r1.status_code == 200

    # Chờ task nền bắt đầu mod_dn
    han = time.time() + 3.0
    while not mod_dn.da_nhan and time.time() < han:
        time.sleep(0.05)
    assert mod_dn.da_nhan, "Lượt 1 chưa bước vào mod_dn"

    # Bắt đầu lượt 2 trong khi lượt 1 đang chạy -> 409 da_co_luot_chay
    r2 = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia", "kieu": "danh_tinh", "van_ban": "luot2@example.com|mk",
    })
    assert r2.status_code == 409
    assert r2.json().get("error", {}).get("code") == "da_co_luot_chay"

    # Lượt 2 không tạo danh tính
    assert danh_tinh.tim_theo_email("luot2@example.com") is None

    # Mở khoá lượt 1 cho chạy xong
    event.set()
    _cho_luot_chay_xong(client)

    # Sau khi xong thì chạy lại được
    mod_dn.cho_event = None
    r3 = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia", "kieu": "danh_tinh", "van_ban": "luot3@example.com|mk",
    })
    assert r3.status_code == 200
    _cho_luot_chay_xong(client)


# ── 10. LÕI CHẠY KHI KHÔNG CÓ MODULE NÀO ───────────────────────────────────

def test_loi_chay_khi_khong_co_module_nao(tmp_path, monkeypatch):
    d_data = tmp_path / "data"
    monkeypatch.setenv("WEB2API_DATA", str(d_data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    with TestClient(app) as client:
        r_tn = client.get("/api/tinh-nang", headers=QT)
        assert r_tn.status_code == 200

        # bat-dau với module chưa cài -> 404 cho cả hai kiểu
        for k in ["truc_tiep", "danh_tinh"]:
            r = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
                "module_id": "mod-chua-cai-nao", "kieu": k, "van_ban": "test@example.com|mk",
            })
            assert r.status_code == 404


# ── 12. DÒNG CHỈ CÓ EMAIL (A1) ─────────────────────────────────────────────

def test_dong_chi_co_email_dung_lai_danh_tinh_co_san(env_gia):
    client = env_gia["client"]
    mod_dn = env_gia["mod_dn"]
    mod_pv = env_gia["mod_pv"]

    # Đã có danh tính và hồ sơ (A1)
    dt = danh_tinh.tao("only_email@example.com")
    danh_tinh.cap_nhat_trang_thai(dt["id"], ho_so=HO_SO_CHUAN_1, trang_thai="dang_nhap")

    r = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia",
        "kieu": "danh_tinh",
        "van_ban": "only_email@example.com",
    })
    assert r.status_code == 200
    tt = _cho_luot_chay_xong(client)
    assert tt["ket_qua"][0]["trang_thai"] == "xong"

    # Module đăng nhập nhận mat_khau is None
    assert mod_dn.da_nhan[-1]["mat_khau_is_none"] is True
    # Bước C chạy và lưu tài khoản
    assert len(mod_pv.da_nhan_tai_dung) == 1
    # Không tạo danh tính mới
    assert len(danh_tinh.danh_sach()) == 1


def test_dong_chi_co_email_can_nguoi_thi_danh_tinh_con_nguyen(env_gia):
    client = env_gia["client"]
    mod_dn = env_gia["mod_dn"]
    mod_dn.tra_ve = KetQuaDangNhapGoogleGia(False, can_nguoi=True)

    dt = danh_tinh.tao("only_email_cn@example.com")
    danh_tinh.cap_nhat_trang_thai(dt["id"], ho_so=HO_SO_CHUAN_1, trang_thai="dang_nhap")

    r = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json={
        "module_id": "mod-provider-gia",
        "kieu": "danh_tinh",
        "van_ban": "only_email_cn@example.com",
    })
    assert r.status_code == 200
    tt = _cho_luot_chay_xong(client)
    assert tt["ket_qua"][0]["trang_thai"] == "loi"
    assert tt["ket_qua"][0]["ma_loi"] == "can_nguoi"

    # Danh tính còn nguyên
    dt_sau = danh_tinh.lay(dt["id"])
    assert dt_sau is not None
    assert dt_sau["ho_so_trinh_duyet"] == HO_SO_CHUAN_1
