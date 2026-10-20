"""Test module khoa-cua (NV169) — khoá cửa thông minh.

Luật cứng của việc: mở khoá LUÔN «nguy_hiem» theo cấu trúc, không bao giờ chạy
từ mô hình/khoá khách/kênh vào; chỉ quản trị sau cửa sổ xác nhận lại (step-up)
của module mới mở được; mọi lần mở ghi nhật ký bất biến (ai, từ đâu, lúc nào).
Chống phát lại: mã xác nhận dùng một lần, hết hiệu lực sau 120 giây, gắn với
quản trị đã lấy nó. Điều khiển mặc định TẮT.

Mọi dịch vụ đều GIẢ: backend khoá là đối tượng KhoaGia tiêm vào module
(`mod._be = KhoaGia()`); chữ ký Tuya được đo bằng httpx.MockTransport. Không
mạng thật. Khoá/IP trong test là GIẢ rõ ràng.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import stat
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import ngu_canh
from app.kham_pha import doc_manifest, kiem_manifest, nap_ma

GOC = Path(__file__).resolve().parents[1]
THU_MUC_MODULE = GOC / "modules" / "khoa-cua"

AID = "access-id-gia-0123"
SECRET = "secret-gia-0123456789abcdef"  # GIẢ, chỉ cho test
DEVICE = "khoa-gia-001"

KHOA_QT = "khoa-quan-tri-test-khoa-cua-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


# ── tải module ─────────────────────────────────────────────────────────────

_goi = None
_ten_goi = ""


def _tai():
    global _goi, _ten_goi
    if _goi is None:
        manifest, loi = doc_manifest(THU_MUC_MODULE)
        assert loi is None, loi
        assert kiem_manifest(manifest) is None, kiem_manifest(manifest)
        lop, ten_goi = nap_ma(THU_MUC_MODULE, manifest, "test169")
        _goi = (lop, sys.modules[ten_goi])
        _ten_goi = ten_goi
    return _goi


def _manifest():
    manifest, loi = doc_manifest(THU_MUC_MODULE)
    assert loi is None, loi
    return manifest


def _cau_hinh(**them):
    cfg = {
        "access_id": AID,
        "access_secret": SECRET,
        "vung": "america",
        "ma_thiet_bi": DEVICE,
        "cho_phep_dieu_khien": "bat",
    }
    cfg.update(them)
    return cfg


def _module_moi(**cfg_over):
    lop, _ = _tai()
    mod = lop(_manifest())
    mod.nap(_cau_hinh(**cfg_over))
    return mod


@contextmanager
def ngu_canh_cua(danh_tinh, nguon="nguoi"):
    tok = ngu_canh.dat(danh_tinh, nguon=nguon)
    try:
        yield
    finally:
        ngu_canh.khoi_phuc(tok)


QT_DT = {"loai": "quan_tri", "id": "admin-1"}
QT2_DT = {"loai": "quan_tri", "id": "admin-2"}
KHACH_DT = {"loai": "khach", "id": "khach-1", "pham_vi": ["thiet_bi:ghi"]}
AI_DT = {"loai": "khach", "id": "model-1", "pham_vi": ["thiet_bi:ghi"]}


def chay(coro):
    return asyncio.run(coro)


# ── backend giả ────────────────────────────────────────────────────────────

class KhoaGia:
    """Khoá giả: giữ trạng thái đóng/mở + nhật ký mở cửa."""

    def __init__(self):
        self.tt = "khoa"
        self.so_mo = 0
        self.so_dong = 0
        self.nhat_ky = []          # [{"ma", "ten_tuya", "ts", "thiet_bi"}]
        self.loi = None           # gán Exception để giả lỗi thiết bị

    async def trang_thai_khoa(self):
        if self.loi:
            raise self.loi
        return self.tt

    async def nhat_ky_mo(self, so_ngay):
        return [dict(m) for m in self.nhat_ky]

    async def mo_khoa(self):
        if self.loi:
            raise self.loi
        self.so_mo += 1
        self.tt = "mo"
        self.nhat_ky.append({"ma": "app#0", "ten_tuya": "", "ts": time.time(),
                             "thiet_bi": "khoá giả"})

    async def dong_khoa(self):
        if self.loi:
            raise self.loi
        self.so_dong += 1
        self.tt = "khoa"


def _gia(mod, **cfg_over):
    mod._be = KhoaGia()
    return mod


# ── 1. cấu trúc nguy_hiem ──────────────────────────────────────────────────

def test_manifest_va_thuc_the_khai_nguy_hiem_theo_cau_truc(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    _gia(mod)
    thuc_the = chay(mod.thuc_the(None))
    khoa = next(e for e in thuc_the if e["id"] == "khoa_cua.khoa_chinh")
    assert khoa["rui_ro_mac_dinh"] == "nguy_hiem"
    ten_hd = {h["hanh_dong"] if isinstance(h, dict) else h
              for h in khoa["tinh_nang"]}
    assert {"chuan_bi_mo", "mo_khoa", "dong_khoa"} <= ten_hd
    assert "mo_khoa" in [h for h in ten_hd]
    # Thực thể đọc riêng, không nguy hiểm
    nk = next(e for e in thuc_the if e["id"] == "khoa_cua.nhat_ky")
    assert nk["rui_ro_mac_dinh"] == "doc"
    assert all(e["id"].startswith("khoa_cua.") for e in thuc_the)


def test_chua_cau_hinh_khong_co_thuc_the(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    lop, _ = _tai()
    mod = lop(_manifest())  # chưa nap
    assert chay(mod.thuc_the(None)) == []
    sk = chay(mod.suc_khoe())
    assert sk["ok"] is False


# ── 2. mở khoá: cổng quản trị + step-up ────────────────────────────────────

def test_mo_khoa_thieu_ngu_canh_tu_choi(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    be = KhoaGia()
    mod._be = be
    assert ngu_canh.lay() is None
    kq = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "mo_khoa", {}))
    assert kq["ok"] is False
    assert be.so_mo == 0


def test_mo_khoa_khach_tu_choi(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    be = KhoaGia()
    mod._be = be
    with ngu_canh_cua(KHACH_DT, nguon="kenh"):
        kq = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "mo_khoa", {}))
    assert kq["ok"] is False
    assert "quản trị" in kq["loi"]
    assert be.so_mo == 0


def test_mo_khoa_mo_hinh_tu_choi(tmp_path, monkeypatch):
    """Mô hình (nguồn ai, danh tính khách) không bao giờ mở được trực tiếp."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    be = KhoaGia()
    mod._be = be
    with ngu_canh_cua(AI_DT, nguon="ai"):
        kq = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "mo_khoa", {}))
    assert kq["ok"] is False
    assert be.so_mo == 0


def test_mo_khoa_quan_tri_thieu_ma_xac_nhan_tu_choi(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    be = KhoaGia()
    mod._be = be
    with ngu_canh_cua(QT_DT):
        kq = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "mo_khoa", {}))
    assert kq["ok"] is False
    assert "chuan_bi_mo" in kq["loi"]
    assert be.so_mo == 0


def test_chuan_bi_mo_khach_tu_choi(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    _gia(mod)
    with ngu_canh_cua(KHACH_DT):
        kq = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "chuan_bi_mo", {}))
    assert kq["ok"] is False


def test_mo_khoa_dung_quy_trinh_va_nhat_ky_bat_bien(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    be = KhoaGia()
    mod._be = be
    with ngu_canh_cua(QT_DT):
        cb = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "chuan_bi_mo", {}))
    assert cb["ok"] is True
    ma = cb["ma_xac_nhan"]
    assert isinstance(ma, str) and len(ma) >= 16
    assert cb["het_han_giay"] == 120

    with ngu_canh_cua(QT_DT):
        kq = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "mo_khoa",
                                {"ma_xac_nhan": ma}))
    assert kq["ok"] is True
    assert be.so_mo == 1

    tt = chay(mod.trang_thai(["khoa_cua.khoa_chinh"]))
    assert tt["khoa_cua.khoa_chinh"]["gia_tri"] == "mo"

    # Nhật ký bất biến: ai, từ đâu, lúc nào
    p = tmp_path / "du_lieu" / "khoa-cua" / "nhat-ky-bat-bien.jsonl"
    assert p.is_file()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    dong = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    mo = [d for d in dong if d["su_kien"] == "mo_khoa_thanh_cong"]
    assert len(mo) == 1
    assert mo[0]["ai"] == "admin-1"
    assert mo[0]["nguon"] == "nguoi"
    assert "luc" in mo[0]
    assert SECRET not in p.read_text(encoding="utf-8")


def test_phat_lai_ma_xac_nhan_tu_choi(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    be = KhoaGia()
    mod._be = be
    with ngu_canh_cua(QT_DT):
        ma = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "chuan_bi_mo", {}))["ma_xac_nhan"]
        assert chay(mod.thuc_hien("khoa_cua.khoa_chinh", "mo_khoa",
                                  {"ma_xac_nhan": ma}))["ok"] is True
        # Gửi lại lệnh cũ → từ chối (đơn dùng)
        kq2 = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "mo_khoa",
                                 {"ma_xac_nhan": ma}))
    assert kq2["ok"] is False
    assert be.so_mo == 1


def test_ma_xac_nhan_het_han_tu_choi(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    be = KhoaGia()
    mod._be = be
    with ngu_canh_cua(QT_DT):
        ma = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "chuan_bi_mo", {}))["ma_xac_nhan"]
    mod._step_up["admin-1"]["het"] = time.time() - 1  # hết hạn
    with ngu_canh_cua(QT_DT):
        kq = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "mo_khoa",
                                {"ma_xac_nhan": ma}))
    assert kq["ok"] is False
    assert be.so_mo == 0


def test_ma_xac_nhan_gan_dung_quan_tri(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    be = KhoaGia()
    mod._be = be
    with ngu_canh_cua(QT_DT):
        ma = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "chuan_bi_mo", {}))["ma_xac_nhan"]
    with ngu_canh_cua(QT2_DT):  # quản trị khác dùng mã của admin-1
        kq = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "mo_khoa",
                                {"ma_xac_nhan": ma}))
    assert kq["ok"] is False
    assert be.so_mo == 0


def test_hai_lenh_mo_song_song_chi_mot_thanh_cong(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    be = KhoaGia()
    mod._be = be

    async def hai_lenh():
        with ngu_canh_cua(QT_DT):
            ma = (await mod.thuc_hien("khoa_cua.khoa_chinh", "chuan_bi_mo",
                                      {}))["ma_xac_nhan"]
            return await asyncio.gather(
                mod.thuc_hien("khoa_cua.khoa_chinh", "mo_khoa",
                              {"ma_xac_nhan": ma}),
                mod.thuc_hien("khoa_cua.khoa_chinh", "mo_khoa",
                              {"ma_xac_nhan": ma}),
            )

    kq1, kq2 = asyncio.run(hai_lenh())
    assert [kq1["ok"], kq2["ok"]].count(True) == 1
    assert be.so_mo == 1


def test_dieu_khien_tat_mac_dinh(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi(cho_phep_dieu_khien="tat")
    be = KhoaGia()
    mod._be = be
    with ngu_canh_cua(QT_DT):
        assert chay(mod.thuc_hien("khoa_cua.khoa_chinh", "chuan_bi_mo",
                                  {}))["ok"] is False
        assert chay(mod.thuc_hien("khoa_cua.khoa_chinh", "dong_khoa",
                                  {}))["ok"] is False
        # Đọc vẫn được
        kq = chay(mod.thuc_hien("khoa_cua.nhat_ky", "xem_nhat_ky", {}))
        assert kq["ok"] is True
    assert be.so_mo == 0 and be.so_dong == 0


def test_dong_khoa_quan_tri_va_nhat_ky(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    be = KhoaGia()
    be.tt = "mo"
    mod._be = be
    with ngu_canh_cua(QT_DT):
        kq = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "dong_khoa", {}))
    assert kq["ok"] is True
    assert be.so_dong == 1
    p = tmp_path / "du_lieu" / "khoa-cua" / "nhat-ky-bat-bien.jsonl"
    dong = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    assert any(d["su_kien"] == "dong_khoa_thanh_cong" and d["ai"] == "admin-1"
               for d in dong)


def test_dong_khoa_khach_tu_choi(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    be = KhoaGia()
    mod._be = be
    with ngu_canh_cua(KHACH_DT):
        kq = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "dong_khoa", {}))
    assert kq["ok"] is False
    assert be.so_dong == 0


# ── 3. nhật ký mở/đóng + học tên ───────────────────────────────────────────

def test_xem_nhat_ky_giai_ten(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    be = KhoaGia()
    ts = time.time() - 3600
    be.nhat_ky = [
        {"ma": "fingerprint#11", "ten_tuya": "", "ts": ts, "thiet_bi": "Khoá cửa"},
        {"ma": "password#3", "ten_tuya": "Chủ nhà", "ts": ts + 10, "thiet_bi": "Khoá cửa"},
    ]
    mod._be = be
    with ngu_canh_cua(QT_DT):
        chay(mod.thuc_hien("khoa_cua.nhat_ky", "dat_ten_ma",
                           {"ma": "fingerprint#11", "ten": "Con trai"}))
        kq = chay(mod.thuc_hien("khoa_cua.nhat_ky", "xem_nhat_ky", {"so_ngay": 1}))
    assert kq["ok"] is True
    muc = {m["ma"]: m for m in kq["muc"]}
    assert muc["fingerprint#11"]["ten"] == "Con trai"
    assert muc["fingerprint#11"]["hien_thi"] == "vân tay số 11"
    assert muc["password#3"]["ten"] == "Chủ nhà"  # tên Tuya ưu tiên khi có
    assert SECRET not in json.dumps(kq, ensure_ascii=False)


def test_dat_ten_khach_tu_choi_va_xoa_ten(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    _gia(mod)
    with ngu_canh_cua(KHACH_DT):
        kq = chay(mod.thuc_hien("khoa_cua.nhat_ky", "dat_ten_ma",
                                {"ma": "face#17", "ten": "Kẻ lạ"}))
    assert kq["ok"] is False
    with ngu_canh_cua(QT_DT):
        assert chay(mod.thuc_hien("khoa_cua.nhat_ky", "dat_ten_ma",
                                  {"ma": "face#17", "ten": "Em gái"}))["ok"] is True
        assert chay(mod.thuc_hien("khoa_cua.nhat_ky", "xoa_ten_ma",
                                  {"ma": "face#17"}))["ok"] is True
    p = tmp_path / "du_lieu" / "khoa-cua" / "so-ten.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    du_lieu = json.loads(p.read_text(encoding="utf-8"))
    assert "face#17" not in du_lieu


def test_ten_khong_vao_suc_khoe_va_khong_dung_de_mo(tmp_path, monkeypatch):
    """Học tên là dữ liệu cá nhân: không lộ qua suc_khoe, không phải bằng
    chứng để mở — mo_khoa không đọc sổ tên."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    be = KhoaGia()
    mod._be = be
    with ngu_canh_cua(QT_DT):
        chay(mod.thuc_hien("khoa_cua.nhat_ky", "dat_ten_ma",
                           {"ma": "fingerprint#11", "ten": "Con trai"}))
    sk = chay(mod.suc_khoe())
    assert "Con trai" not in json.dumps(sk, ensure_ascii=False)


# ── 4. bí mật ──────────────────────────────────────────────────────────────

def test_bi_mat_khong_vao_log_phanh_hoi_suc_khoe(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    be = KhoaGia()
    mod._be = be
    assert _manifest()["truong_cau_hinh"][1]["kieu"] == "bi_mat"
    assert "iot.tuya.com" in _manifest()["truong_cau_hinh"][1]["mo_ta"]
    import logging
    with caplog.at_level(logging.DEBUG):
        chay(mod.cai(_cau_hinh()))
        sk = chay(mod.suc_khoe())
        with ngu_canh_cua(QT_DT):
            cb = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "chuan_bi_mo", {}))
            chay(mod.thuc_hien("khoa_cua.khoa_chinh", "mo_khoa",
                               {"ma_xac_nhan": cb["ma_xac_nhan"]}))
    for cho in (json.dumps(sk, ensure_ascii=False), caplog.text):
        assert SECRET not in cho


def test_cai_thieu_cap_bi_mat_tu_choi(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    lop, _ = _tai()
    mod = lop(_manifest())
    r = chay(mod.cai(_cau_hinh(access_secret="")))
    assert r["ok"] is False
    r = chay(mod.cai(_cau_hinh(vung="sai-vung")))
    assert r["ok"] is False
    r = chay(mod.cai(_cau_hinh(cho_phep_dieu_khien="cho_phep")))
    assert r["ok"] is False
    r = chay(mod.cai(_cau_hinh()))
    assert r["ok"] is True


# ── 5. sự kiện ─────────────────────────────────────────────────────────────

def test_su_kien_chi_day_su_kien_moi(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    be = KhoaGia()
    be.nhat_ky = [{"ma": "app#0", "ten_tuya": "", "ts": 1000.0, "thiet_bi": "khoá giả"}]
    mod._be = be
    mod._nhip_su_kien = 0.01

    async def lay():
        gen = mod.su_kien()
        task = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0.3)  # lần kiểm đầu chỉ đặt mốc (lịch sử cũ không phát lại)
        be.nhat_ky.append({"ma": "fingerprint#5", "ten_tuya": "",
                           "ts": time.time(), "thiet_bi": "khoá giả"})
        sk = await asyncio.wait_for(task, timeout=5)
        await gen.aclose()
        return sk

    sk = asyncio.run(lay())
    assert sk["id"] == "khoa_cua.khoa_chinh"
    assert sk["loai"] == "mo_khoa"
    assert sk["gia_tri"] == "fingerprint#5"


# ── 6. chữ ký đám mây (MockTransport) ──────────────────────────────────────

def test_chu_ky_tuya_dung_vector_doc_lap(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    _module_moi()  # nạp → dam_may được import như anh em của gói module
    import importlib
    dam_may = importlib.import_module(f"{_ten_goi}.dam_may")
    chuoi = "GET\n" + hashlib.sha256(b"").hexdigest() + "\n\n/v1.0/token?grant_type=1"
    ky = dam_may._ky("khoa-bi-mat", chuoi)
    mong_doi = hmac.new(b"khoa-bi-mat", chuoi.encode(), hashlib.sha256).hexdigest().upper()
    assert ky == mong_doi
    assert dam_may.MAY_CHU["america"] == "https://openapi.tuyaus.com"
    assert not dam_may.kiem_mien_tuya("https://api.evil.example/")
    assert dam_may.kiem_mien_tuya("https://openapi.tuyaus.com/v1.0/token")


# ── 7. API thật qua TestClient ─────────────────────────────────────────────

@pytest.fixture
def moi_truong_api(tmp_path, monkeypatch):
    from app import kho, so_module
    from app.main import app
    from app.so_module import SoModule
    from gia import ghi_thu_muc

    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    anh_dir = tmp_path / "anh"
    anh_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh_dir)

    tep = {}
    for p in THU_MUC_MODULE.iterdir():
        if p.is_file():
            tep[p.name] = p.read_bytes()
    ghi_thu_muc(data_dir / "modules" / "khoa-cua", tep)

    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        r = client.post("/api/modules/nap-lai", headers=QT)
        assert r.status_code == 200
        r = client.post("/api/modules/khoa-cua/cai", headers=QT,
                        json={"cau_hinh": _cau_hinh()})
        assert r.status_code == 200, r.text
        mod = so.dang_chay["khoa-cua"]
        be = KhoaGia()
        mod._be = be
        yield client, so, mod, be


def _khoa_khach(client, pham_vi):
    r = client.post("/api/khoa-khach", headers=QT,
                    json={"nhan": "test169", "pham_vi": pham_vi})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['khoa']['khoa']}"}


MA_GIA = "ma-gia-dung-do-dai-0123"  # ≥16 ký tự để qua kiểm tham số của lõi


def test_api_401_khong_xac_thuc(moi_truong_api):
    client, so, mod, be = moi_truong_api
    r = client.post("/api/thiet-bi/khoa_cua.khoa_chinh/hanh-dong",
                    json={"hanh_dong": "mo_khoa", "xac_nhan": True})
    assert r.status_code == 401


def test_api_khach_mo_khoa_vao_hang_cho_khong_chay(moi_truong_api):
    client, so, mod, be = moi_truong_api
    hd = _khoa_khach(client, ["thiet_bi:ghi", "thiet_bi:doc"])
    r = client.post("/api/thiet-bi/khoa_cua.khoa_chinh/hanh-dong", headers=hd,
                    json={"hanh_dong": "mo_khoa", "tham_so": {"ma_xac_nhan": MA_GIA}})
    assert r.status_code == 202
    assert r.json()["cho_phe_duyet"] is True
    assert be.so_mo == 0  # không bao giờ chạy từ khoá khách


def test_api_khach_thieu_pham_vi_403(moi_truong_api):
    client, so, mod, be = moi_truong_api
    hd = _khoa_khach(client, ["thiet_bi:doc"])
    r = client.post("/api/thiet-bi/khoa_cua.khoa_chinh/hanh-dong", headers=hd,
                    json={"hanh_dong": "dong_khoa", "xac_nhan": True})
    assert r.status_code == 403


def test_api_quan_tri_thieu_xac_nhan_409(moi_truong_api):
    client, so, mod, be = moi_truong_api
    r = client.post("/api/thiet-bi/khoa_cua.khoa_chinh/hanh-dong", headers=QT,
                    json={"hanh_dong": "mo_khoa", "tham_so": {"ma_xac_nhan": MA_GIA}})
    assert r.status_code == 409


def test_api_quan_tri_chua_step_up_module_tu_choi(moi_truong_api):
    client, so, mod, be = moi_truong_api
    r = client.post("/api/thiet-bi/khoa_cua.khoa_chinh/hanh-dong", headers=QT,
                    json={"hanh_dong": "mo_khoa", "xac_nhan": True,
                          "tham_so": {"ma_xac_nhan": MA_GIA}})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert be.so_mo == 0


def test_api_quy_trinh_mo_day_du(moi_truong_api):
    client, so, mod, be = moi_truong_api
    r1 = client.post("/api/thiet-bi/khoa_cua.khoa_chinh/hanh-dong", headers=QT,
                     json={"hanh_dong": "chuan_bi_mo", "xac_nhan": True})
    assert r1.status_code == 200
    ma = r1.json()["ma_xac_nhan"]
    # Mã xác nhận không lộ bí mật và không lộ qua lỗi
    assert SECRET not in r1.text
    r2 = client.post("/api/thiet-bi/khoa_cua.khoa_chinh/hanh-dong", headers=QT,
                     json={"hanh_dong": "mo_khoa", "xac_nhan": True,
                           "tham_so": {"ma_xac_nhan": ma}})
    assert r2.status_code == 200
    assert r2.json()["ok"] is True
    assert be.so_mo == 1


def test_api_duyet_hang_cho_thieu_ma_xac_nhan_module_tu_choi(moi_truong_api):
    """Đường «phê duyệt người» của lõi: quản trị duyệt (đã qua step-up của lõi)
    nhưng module vẫn bắt mã xác nhận gắn quản trị → từ chối an toàn. Quản trị
    dùng đường trực tiếp (chuan_bi_mo → mo_khoa) thay vì hàng chờ cho mo_khoa."""
    client, so, mod, be = moi_truong_api
    hd = _khoa_khach(client, ["thiet_bi:ghi", "thiet_bi:doc"])
    r = client.post("/api/thiet-bi/khoa_cua.khoa_chinh/hanh-dong", headers=hd,
                    json={"hanh_dong": "mo_khoa", "tham_so": {"ma_xac_nhan": MA_GIA}})
    pd_id = r.json()["phe_duyet_id"]
    r_duyet = client.post(f"/api/phe-duyet/{pd_id}/duyet", headers=QT)
    assert r_duyet.status_code == 200
    assert r_duyet.json()["ket_qua"]["ok"] is False
    assert be.so_mo == 0
