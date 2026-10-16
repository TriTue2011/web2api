"""Cổng gửi ra đích danh của lõi (NV32): POST /api/gui/{module}.

Mỗi test kèm đột biến (bảng trong nv32-bao-cao.md): đột biến tương ứng phải làm
test ĐỎ, mã thật XANH.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import gui_ra as gui_ra_mod  # noqa: E402
from app import khoa_khach, nhat_ky  # noqa: E402
from app.main import app  # noqa: E402

KHOA_QT = "<redacted>"

MOD_A = "mod-a"      # hai đích
MOD_MOT = "mod-mot"  # một đích
MOD_LA = "mod-la"    # khai kenh_ra nhưng module giả thiếu phương thức gui_tin

DS_HAI = [{"id": "zalo-1", "ten": "Zalo bot 1"}, {"id": "zalo-2", "ten": "Zalo bot 2"}]
DS_MOT = [{"id": "kenh-duy-nhat", "ten": "Kênh duy nhất"}]


def _manifest(mod):
    return {"id": mod, "tien_to": mod, "cung_cap": ["kenh_ra"]}


class ModGia:
    """Module giả có kênh ra."""

    def __init__(self, ds_kenh):
        self._ds = ds_kenh
        self.da_gui: list[tuple[dict, dict]] = []
        self.ket_qua: dict = {"ok": True}
        self.loi: Exception | None = None

    def kenh_ra(self):
        return self._ds

    async def gui_tin(self, dich: dict, noi_dung: dict) -> dict:
        self.da_gui.append((dich, noi_dung))
        if self.loi is not None:
            raise self.loi
        return self.ket_qua

    async def dung(self) -> None:
        pass


class ModThieuPhuongThuc:
    """Khai cung_cap kenh_ra nhưng không cài gui_tin (lỗi cấu hình)."""

    def kenh_ra(self):
        return DS_MOT

    async def dung(self) -> None:
        pass


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")  # tắt khoá IP do sai khoá
    for v in ("WEB2API_GUI_TIN_PHUT", "WEB2API_GUI_DONG_THOI",
              "WEB2API_GUI_NOI_DUNG_TOI_DA_BYTE", "WEB2API_GUI_TEP_TOI_DA_MB",
              "WEB2API_GUI_THOI_GIAN_GIAY"):
        monkeypatch.delenv(v, raising=False)
    gui_ra_mod._dat_lai_trang_thai()

    _, khoa_chat = khoa_khach.tao_khoa(nhan="khach chat", pham_vi=["chat"])
    _, khoa_gui_a = khoa_khach.tao_khoa(nhan="khach gui a", pham_vi=["gui:mod_a"])
    _, khoa_sao = khoa_khach.tao_khoa(nhan="khach sao", pham_vi=["*"])

    mod_a = ModGia(DS_HAI)
    mod_mot = ModGia(DS_MOT)
    mod_la = ModThieuPhuongThuc()
    with TestClient(app) as c:
        so = c.app.state.so
        so.dang_chay[MOD_A] = mod_a
        so.dang_chay[MOD_MOT] = mod_mot
        so.dang_chay[MOD_LA] = mod_la
        so.catalog[MOD_A] = SimpleNamespace(manifest=_manifest(MOD_A))
        so.catalog[MOD_MOT] = SimpleNamespace(manifest=_manifest(MOD_MOT))
        so.catalog[MOD_LA] = SimpleNamespace(manifest=_manifest(MOD_LA))
        yield c, mod_a, mod_mot, {"qt": KHOA_QT, "chat": khoa_chat,
                                  "gui_a": khoa_gui_a, "sao": khoa_sao}
    for m in (MOD_A, MOD_MOT, MOD_LA):
        so.dang_chay.pop(m, None)
        so.catalog.pop(m, None)
    gui_ra_mod._dat_lai_trang_thai()


def _gui(c, module, khoa=None, json=None, data=None, files=None, headers=None):
    kw = {"headers": dict(headers or {})}
    if khoa:
        kw["headers"]["Authorization"] = f"Bearer {khoa}"
    if json is not None:
        kw["json"] = json
    if data is not None:
        kw["data"] = data
    if files is not None:
        kw["files"] = files
    return c.post(f"/api/gui/{module}", **kw)


# ── xác thực + phạm vi ──

def test_khong_khoa_401(moi_truong):
    c, *_ = moi_truong
    r = _gui(c, MOD_A, json={"kenh_id": "zalo-1", "noi_dung": {}})
    assert r.status_code == 401, r.text


def test_khoa_sai_401(moi_truong):
    c, *_ = moi_truong
    r = _gui(c, MOD_A, khoa="sai-hoan-toan", json={"kenh_id": "zalo-1", "noi_dung": {}})
    assert r.status_code == 401, r.text


def test_khoa_chat_403(moi_truong):
    # Hiện tại middleware MiddlewareChanKhoaKhachQuanTri (app/main.py, cấm sửa)
    # chặn MỌI khoá khách ở /api/* → 403 trước khi tới logic phạm vi của lõi.
    # Xem test_co_pham_vi_gui_* (mức đơn vị) cho ma trận phạm vi chi tiết và
    # nv32-bao-cao.md cho đề xuất mở ngoại lệ 1 dòng trong main.py.
    c, _, _, khoa = moi_truong
    r = _gui(c, MOD_A, khoa=khoa["chat"], json={"kenh_id": "zalo-1", "noi_dung": {}})
    assert r.status_code == 403, r.text


def test_khoa_gui_mod_a_goi_mod_b_403(moi_truong):
    # Khoá khách đi QUA middleware (đường /api/gui/ được phép) và bị router từ chối vì thiếu phạm vi gui:mod_b.
    c, _, _, khoa = moi_truong
    r = _gui(c, MOD_MOT, khoa=khoa["gui_a"], json={"noi_dung": {}})
    assert r.status_code == 403, r.text


def test_khoa_khach_co_pham_vi_gui_dung_module_thanh_cong(moi_truong):
    # Chứng minh khoá khách có `gui:mod_a` thật sự GỬI ĐƯỢC tới mod-a (nếu middleware chặn, test này đỏ):
    # không có test này thì 403 ở test bên trên có thể đúng vì SAI lý do.
    c, mod_a, _, khoa = moi_truong
    r = _gui(c, MOD_A, khoa=khoa["gui_a"], json={"kenh_id": "zalo-2", "noi_dung": {"text": "xin chao"}})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "kenh": "zalo-2"}
    assert mod_a.da_gui[-1][1] == {"text": "xin chao"}


def test_khoa_qt_gui_thanh_cong(moi_truong):
    c, mod_a, _, khoa = moi_truong
    r = _gui(c, MOD_A, khoa=khoa["qt"],
             json={"kenh_id": "zalo-2", "noi_dung": {"text": "chao"}})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "kenh": "zalo-2"}
    dich, noi_dung = mod_a.da_gui[-1]
    assert dich["kenh_id"] == "zalo-2"
    assert noi_dung == {"text": "chao"}


def test_co_pham_vi_gui_don_vi():
    # Ma trận phạm vi gui:<module> ở mức đơn vị (không qua middleware).
    f = gui_ra_mod._co_pham_vi_gui
    qt = {"loai": "quan_tri", "id": "quan_tri", "pham_vi": ["*"]}
    assert f(qt, "mod-a") is True
    khach_sao = {"loai": "khach", "id": "kk1", "pham_vi": ["*"]}
    assert f(khach_sao, "mod-a") is True
    khach_chat = {"loai": "khach", "id": "kk2", "pham_vi": ["chat"]}
    assert f(khach_chat, "mod-a") is False  # phạm vi hẹp không gọi được
    khach_gui_a = {"loai": "khach", "id": "kk3", "pham_vi": ["gui:mod_a"]}
    assert f(khach_gui_a, "mod-a") is True   # id «mod-a» dùng phạm vi «gui:mod_a»
    assert f(khach_gui_a, "mod-b") is False  # gui:mod_a không gọi được mod-b
    assert f(khach_gui_a, "mod_a") is True
    khach_rong = {"loai": "khach", "id": "kk4", "pham_vi": []}
    assert f(khach_rong, "mod-a") is False


# ── đích danh ──

def test_hai_dich_khong_neu_kenh_id_400_va_module_khong_bi_goi(moi_truong):
    c, mod_a, _, khoa = moi_truong
    truoc = len(mod_a.da_gui)
    r = _gui(c, MOD_A, khoa=khoa["qt"], json={"noi_dung": {"text": "x"}})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "kenh_required"
    assert len(mod_a.da_gui) == truoc  # module KHÔNG bị gọi


def test_kenh_id_la_404(moi_truong):
    c, mod_a, _, khoa = moi_truong
    truoc = len(mod_a.da_gui)
    r = _gui(c, MOD_A, khoa=khoa["qt"],
             json={"kenh_id": "kenh-khong-co", "noi_dung": {}})
    assert r.status_code == 404, r.text
    assert len(mod_a.da_gui) == truoc


def test_mot_dich_khong_can_neu_kenh_id(moi_truong):
    c, _, mod_mot, khoa = moi_truong
    r = _gui(c, MOD_MOT, khoa=khoa["qt"], json={"noi_dung": {"text": "mot"}})
    assert r.status_code == 200, r.text
    assert r.json()["kenh"] == "kenh-duy-nhat"
    dich, _ = mod_mot.da_gui[-1]
    assert dich["kenh_id"] == "kenh-duy-nhat"


def test_module_la_404(moi_truong):
    c, *_ , khoa = moi_truong
    r = _gui(c, "khong-ton-tai", khoa=khoa["qt"], json={"kenh_id": "x", "noi_dung": {}})
    assert r.status_code == 404, r.text


def test_module_thieu_phuong_thuc_404(moi_truong):
    # Khai cung_cap kenh_ra nhưng không cài gui_tin → coi như không có cổng gửi ra.
    c, *_ , khoa = moi_truong
    r = _gui(c, MOD_LA, khoa=khoa["qt"], json={"noi_dung": {}})
    assert r.status_code == 404, r.text


# ── trần ──

def test_vuot_tran_tin_phut_429(moi_truong, monkeypatch):
    c, _, mod_mot, khoa = moi_truong
    monkeypatch.setenv("WEB2API_GUI_TIN_PHUT", "2")
    for _ in range(2):
        r = _gui(c, MOD_MOT, khoa=khoa["qt"], json={"noi_dung": {}})
        assert r.status_code == 200, r.text
    r = _gui(c, MOD_MOT, khoa=khoa["qt"], json={"noi_dung": {}})
    assert r.status_code == 429, r.text
    assert "Retry-After" in r.headers


def test_tran_tinh_theo_khoa_rieng_don_vi(monkeypatch):
    # Trần tính theo (khoá, module): khoá khác vẫn gửi được (mức đơn vị).
    monkeypatch.setenv("WEB2API_GUI_TIN_PHUT", "1")
    gui_ra_mod._dat_lai_trang_thai()
    gui_ra_mod._kiem_han_dong("khoa-1", "mod-x")
    with pytest.raises(Exception) as e:
        gui_ra_mod._kiem_han_dong("khoa-1", "mod-x")
    assert getattr(e.value, "ma", None) == 429
    gui_ra_mod._kiem_han_dong("khoa-2", "mod-x")  # khoá khác: không bị chặn
    gui_ra_mod._kiem_han_dong("khoa-1", "mod-y")  # module khác: không bị chặn
    gui_ra_mod._dat_lai_trang_thai()


def test_noi_dung_qua_tran_413_va_module_khong_bi_goi(moi_truong, monkeypatch):
    c, _, mod_mot, khoa = moi_truong
    monkeypatch.setenv("WEB2API_GUI_NOI_DUNG_TOI_DA_BYTE", "100")
    truoc = len(mod_mot.da_gui)
    r = _gui(c, MOD_MOT, khoa=khoa["qt"], json={"noi_dung": {"text": "x" * 500}})
    assert r.status_code == 413, r.text
    assert len(mod_mot.da_gui) == truoc


def test_tep_qua_tran_413(moi_truong, monkeypatch):
    c, _, mod_mot, khoa = moi_truong
    monkeypatch.setenv("WEB2API_GUI_TEP_TOI_DA_MB", "0.00001")  # ~10 byte
    r = _gui(c, MOD_MOT, khoa=khoa["qt"],
             data={"kenh_id": "kenh-duy-nhat", "noi_dung": "{}"},
             files={"tep": ("a.bin", b"x" * 100, "application/octet-stream")})
    assert r.status_code == 413, r.text
    assert len(mod_mot.da_gui) == 0


def test_tep_vua_tran_di_qua(moi_truong):
    c, _, mod_mot, khoa = moi_truong
    r = _gui(c, MOD_MOT, khoa=khoa["qt"],
             data={"kenh_id": "kenh-duy-nhat", "noi_dung": '{"text": "kem tep"}'},
             files={"tep": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 200, r.text
    _, noi_dung = mod_mot.da_gui[-1]
    assert noi_dung["text"] == "kem tep"
    assert noi_dung["_tep"][0]["ten"] == "a.txt"
    assert noi_dung["_tep"][0]["du_lieu"] == b"hello"


def test_vuot_dong_thoi_429(moi_truong, monkeypatch):
    c, _, _, khoa = moi_truong
    monkeypatch.setenv("WEB2API_GUI_DONG_THOI", "0")
    r = _gui(c, MOD_MOT, khoa=khoa["qt"], json={"noi_dung": {}})
    assert r.status_code == 429, r.text
    assert "Retry-After" in r.headers


# ── tin cậy kết quả ──

def test_ok_khong_phai_bool_502(moi_truong):
    c, _, mod_mot, khoa = moi_truong
    for gia_tri in ("true", 1, 0, None):
        mod_mot.ket_qua = {"ok": gia_tri}
        r = _gui(c, MOD_MOT, khoa=khoa["qt"], json={"noi_dung": {}})
        assert r.status_code == 502, (gia_tri, r.text)
    mod_mot.ket_qua = {}
    r = _gui(c, MOD_MOT, khoa=khoa["qt"], json={"noi_dung": {}})
    assert r.status_code == 502, r.text
    mod_mot.ket_qua = {"ok": True}


def test_module_bao_that_bai_502(moi_truong):
    c, _, mod_mot, khoa = moi_truong
    mod_mot.ket_qua = {"ok": False, "loi": "het han muc"}
    r = _gui(c, MOD_MOT, khoa=khoa["qt"], json={"noi_dung": {}})
    assert r.status_code == 502, r.text
    assert "het han muc" not in r.text  # chi tiết của module không lộ
    mod_mot.ket_qua = {"ok": True}


def test_loi_tho_nha_cung_cap_khong_lo(moi_truong):
    c, _, mod_mot, khoa = moi_truong
    mod_mot.loi = RuntimeError("nha cung cap bao: token-bi-mat-xyz het han")
    r = _gui(c, MOD_MOT, khoa=khoa["qt"], json={"noi_dung": {}})
    assert r.status_code == 502, r.text
    assert "token-bi-mat-xyz" not in r.text
    assert r.json()["error"]["code"] == "gui_that_bai"
    mod_mot.loi = None


# ── nhật ký ──

def test_nhat_ky_khong_ghi_noi_dung_hay_token(moi_truong):
    c, _, mod_mot, khoa = moi_truong
    noi_dung_bi_mat = "noi-dung-tin-toi-mat-gia-xyz"
    _gui(c, MOD_MOT, khoa=khoa["qt"], json={"noi_dung": {"text": noi_dung_bi_mat}})
    _gui(c, MOD_MOT, khoa=khoa["qt"], json={"kenh_id": "kenh-la", "noi_dung": {}})
    dong = nhat_ky.doc(kenh="gui_ra", gioi_han=50)
    assert dong, "phải có dòng nhật ký kênh gui_ra"
    tat_ca = " ".join(str(d) for d in dong)
    assert noi_dung_bi_mat not in tat_ca
    assert khoa["qt"] not in tat_ca  # token quản trị cũng không được ghi
    assert "kenh-duy-nhat" not in tat_ca  # kenh_id thô không ghi, chỉ băm
    assert any(d.get("them", {}).get("ket_qua") == "ok" for d in dong)


def test_kenh_bam_co_mat_trong_nhat_ky(moi_truong):
    import hashlib
    c, _, _, khoa = moi_truong
    _gui(c, MOD_MOT, khoa=khoa["qt"], json={"noi_dung": {}})
    bam = hashlib.sha256(f"{MOD_MOT}:kenh-duy-nhat".encode()).hexdigest()[:16]
    dong = nhat_ky.doc(kenh="gui_ra", gioi_han=10)
    assert any(d.get("them", {}).get("kenh_bam") == bam for d in dong)


# ── hợp đồng module ──

def test_hop_dong_kenh_ra_bat_phuong_thuc():
    from app.kham_pha import kiem_chuan_module

    class ThieuGuiTin:
        def kenh_ra(self):
            return []

    class ThieuKenhRa:
        async def gui_tin(self, dich, noi_dung):
            return {"ok": True}

    class Du:
        def kenh_ra(self):
            return []

        async def gui_tin(self, dich, noi_dung):
            return {"ok": True}

    assert kiem_chuan_module(["kenh_ra"], ThieuGuiTin) == {"kenh_ra": ["gui_tin"]}
    assert kiem_chuan_module(["kenh_ra"], ThieuKenhRa) == {"kenh_ra": ["kenh_ra"]}
    assert kiem_chuan_module(["kenh_ra"], Du) == {}
