"""Kiểm thử giao diện Chrome cho tính năng nhóm tài khoản theo provider và ẩn provider 0 khoá (Hệ thống → AI).

8 ca kiểm bắt buộc:
(a) tab Tài khoản có đúng 2 khối provider, mỗi khối có tiêu đề kèm số khoá, KHÔNG khối cho nguồn 0 khoá;
(b) lúc mở trang mọi khối ĐÓNG (không thấy dòng khoá nào, aria-expanded=false), bấm tiêu đề ⇒ thấy đúng các khoá của provider ĐÓ (không lẫn provider khác), bấm lại ⇒ đóng;
(c) neo #tk-<id> mở đúng khối;
(d) tab Provider: danh sách chọn có đúng 2 provider, KHÔNG có nguồn 0 khoá, vẫn thu gọn lúc đầu (menu chọn đóng, bấm mới xổ);
(e) xoá khoá cuối của một provider ⇒ provider biến khỏi danh sách chọn và khối biến khỏi tab Tài khoản, provider đang chọn tự chuyển;
(f) không provider nào có khoá ⇒ trạng thái rỗng có nút «Mở tab Tài khoản» và biểu mẫu Thêm API vẫn liệt kê cả nguồn 0 khoá;
(g) thêm khoá đầu tiên cho nguồn 0 khoá ⇒ khối provider xuất hiện;
(h) vai/phiên: mọi thứ trên chỉ sau đăng nhập (dùng dang_nhap_quan_tri); khoá hiển thị chỉ dạng che.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import httpx
import pytest

from app import so_module
from app.main import app
from gia import MayChuThat, ghi_thu_muc
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture
from ui_mau import module_ai

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

KHOA_QT = "khoa-quan-tri-giao-dien-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

# Các khoá API giả nằm trong allowlist của .gitleaks.toml
K1 = "AIzaSyTOT-mot-000000000000000000000001"
K2 = "AIzaSyTOT-hai-000000000000000000000002"
K3 = "AIzaSyTOT-ba--000000000000000000000003"
K4 = "AIzaSy-KHOA-MOT-tot-1111"


def module_ai_ba_nguon(id_: str, ten: str, tien_to: str, cung_cap: tuple[str, ...]) -> dict:
    """Nguồn AI giả cho 3 nguồn: trường api_keys không bắt buộc (bat_buoc: False) để kiểm thử việc xoá khoá cuối."""
    m = {
        "id": id_, "ten": ten, "mo_ta": "Nguồn AI giả cho kiểm thử giao diện nhóm provider.",
        "phien_ban": "1.0.0", "hang": "A", "loai": "ai", "loai_ten": "AI", "loai_thu_tu": 10, "giao_thuc": "openai", "tien_to": tien_to,
        "cung_cap": list(cung_cap),
        "truong_cau_hinh": [
            {"khoa": "api_keys", "nhan": "Khoá API", "kieu": "danh_sach_bi_mat", "bat_buoc": False, "mac_dinh": None,
             "mo_ta": "Một hoặc nhiều khoá giả."},
            {"khoa": "base_url", "nhan": "Địa chỉ gốc", "kieu": "chuoi", "bat_buoc": False,
             "mac_dinh": "https://gia.example/v1", "mo_ta": "Để trống là gọi thẳng."}
        ],
        "tai_nguyen": {"ram_them_mb": 64, "dia_mb": None, "gpu": False, "mang_ra": ["gia.example"], "ghi_chu": ""},
    }
    ma_py = '''
import json

class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self.khoa = []
        self.base = ""

    def nap(self, cau_hinh):
        self.khoa = list(cau_hinh.get("api_keys") or [])
        self.base = cau_hinh.get("base_url") or ""

    async def cai(self, cau_hinh):
        khoa = list(cau_hinh.get("api_keys") or [])
        ds = [{"khoa": "…" + k[-4:], "ok": "hong" not in k, "do_tre_ms": 7, "so_model": 3 if "hong" not in k else 0,
               "loi": None if "hong" not in k else "HTTP 401: khoá không hợp lệ (giả)"} for k in khoa]
        tot = [k for k in khoa if "hong" not in k]
        luu = {"api_keys": tot}
        if cau_hinh.get("base_url"):
            luu["base_url"] = cau_hinh["base_url"]
        return {"ok": True, "ket_qua_tung_khoa": ds, "loi": None, "cau_hinh_luu": luu}

    async def chat(self, body, stream):
        return 200, {"content-type": "application/json"}, b'{"ok":true}'

    async def models(self):
        t = self.tien_to
        return [{"id": t + "/nhanh", "object": "model"}]

    async def suc_khoe(self):
        return {"ok": True, "base_url": self.base or "https://gia.example/v1", "khoa": [
            {"khoa": "…" + k[-4:], "ok": "hong" not in k, "do_tre_ms": 5, "so_model": 3,
             "loi": None if "hong" not in k else "HTTP 401: khoá hỏng (giả)", "dang_nghi_den": None,
             "thu_tu": i + 1, "dang_dung": i == 0, "dung_cuoi": "2026-10-12T07:55:00+00:00",
             "so_429": 0, "lan_429_cuoi": None,
             "han_muc": [{"ten": "chat", "da_dung": 12, "toi_da": 100, "hoi_luc": None, "trang_thai": "dung_duoc", "ma": None}],
             "kha_nang": {}} for i, k in enumerate(self.khoa)
        ]}

    async def dung(self):
        pass
'''
    return {
        "manifest.json": json.dumps(m, ensure_ascii=False).encode(),
        "module.py": ma_py.encode(),
    }


@contextmanager
def moi_truong_ba_nguon(tmp_path, monkeypatch):
    """Khởi tạo môi trường với 3 nguồn AI: ai-mot (>=2 khoá), ai-hai (>=2 khoá), ai-ba (0 khoá)."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")

    mods = {
        "ai-mot": module_ai_ba_nguon("ai-mot", "AI Thứ Nhất", "mot", ("chat",)),
        "ai-hai": module_ai_ba_nguon("ai-hai", "AI Thứ Hai", "hai", ("chat",)),
        "ai-ba": module_ai_ba_nguon("ai-ba", "AI Thứ Ba", "ba", ("chat",)),
    }
    for id_, tep in mods.items():
        ghi_thu_muc(tmp_path / "modules" / id_, tep)

    with MayChuThat(app, lifespan="on") as w:
        yield w


def lien_ket_khoa(w, id_: str, keys: list[str]):
    r = httpx.post(f"{w.url}/api/modules/{id_}/cai", json={"cau_hinh": {"api_keys": keys}}, headers=QT)
    assert r.status_code == 200 and r.json()["ok"], r.text


def test_a_tab_tai_khoan_dung_hai_khoi_kem_so_khoa_khong_nguon_khong_khoa(trang, tmp_path, monkeypatch):
    """(a) tab Tài khoản có đúng 2 khối provider, mỗi khối có tiêu đề kèm số khoá, KHÔNG khối cho nguồn 0 khoá."""
    with moi_truong_ba_nguon(tmp_path, monkeypatch) as w:
        lien_ket_khoa(w, "ai-mot", [K1, K2])
        lien_ket_khoa(w, "ai-hai", [K3, K4])
        # ai-ba có 0 khoá

        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html#tai-khoan")
        trang.cho("document.querySelectorAll('[data-khoi-pv]').length === 2", han=15, mo_ta="đúng 2 khối provider")

        # Có khối cho ai-hai và ai-mot (theo tên hiển thị: 'AI Thứ Hai' < 'AI Thứ Nhất')
        khoi_ids = trang.js("[...document.querySelectorAll('[data-khoi-pv]')].map(s => s.dataset.khoiPv)")
        assert khoi_ids == ["ai-hai", "ai-mot"]

        # Tiêu đề kèm số khoá
        tieu_de_mot = trang.chu("[data-khoi-pv='ai-mot'] [data-mo-khoi-pv='ai-mot']")
        assert "AI Thứ Nhất" in tieu_de_mot
        assert "2" in tieu_de_mot

        tieu_de_hai = trang.chu("[data-khoi-pv='ai-hai'] [data-mo-khoi-pv='ai-hai']")
        assert "AI Thứ Hai" in tieu_de_hai
        assert "2" in tieu_de_hai

        # KHÔNG có khối cho nguồn 0 khoá (ai-ba)
        assert trang.js("document.querySelector('[data-khoi-pv=\"ai-ba\"]')") is None


def test_b_khoi_thu_gon_luc_mo_bam_tieu_de_xo_va_dong(trang, tmp_path, monkeypatch):
    """(b) lúc mở trang mọi khối ĐÓNG (không thấy dòng khoá nào, aria-expanded=false),
    bấm tiêu đề ⇒ thấy đúng các khoá của provider ĐÓ (không lẫn provider khác), bấm lại ⇒ đóng."""
    with moi_truong_ba_nguon(tmp_path, monkeypatch) as w:
        lien_ket_khoa(w, "ai-mot", [K1, K2])
        lien_ket_khoa(w, "ai-hai", [K3, K4])

        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html#tai-khoan")
        trang.cho("document.querySelectorAll('[data-khoi-pv]').length === 2", han=15, mo_ta="2 khối provider")

        # Lúc mở trang: mọi khối ĐÓNG, aria-expanded=false, không thấy dòng khoá nào trong DOM
        trang.khung(1900, 900)
        trang.chup("tab-tai-khoan-nhieu-provider-dong-1900px")
        assert trang.js("document.querySelectorAll('[data-tai-khoan]').length") == 0
        assert trang.js("document.querySelector('[data-mo-khoi-pv=\"ai-mot\"]').getAttribute('aria-expanded')") == "false"
        assert trang.js("document.querySelector('[data-mo-khoi-pv=\"ai-hai\"]').getAttribute('aria-expanded')") == "false"

        # Bấm tiêu đề khối ai-mot ⇒ mở ra, thấy đúng các khoá của ai-mot
        trang.bam("document.querySelector('[data-mo-khoi-pv=\"ai-mot\"]')")
        trang.cho("document.querySelectorAll('[data-tai-khoan]').length === 2", mo_ta="mở khối ai-mot")
        trang.chup("tab-tai-khoan-mot-khoi-mo-1900px")
        assert trang.js("document.querySelector('[data-mo-khoi-pv=\"ai-mot\"]').getAttribute('aria-expanded')") == "true"
        assert trang.js("document.querySelector('[data-mo-khoi-pv=\"ai-hai\"]').getAttribute('aria-expanded')") == "false"

        tk_mot = trang.js("[...document.querySelectorAll('[data-tai-khoan]')].map(d => d.dataset.taiKhoan)")
        assert tk_mot == ["ai-mot", "ai-mot"]  # Không lẫn khoá của ai-hai

        # Bấm lại tiêu đề khối ai-mot ⇒ đóng lại
        trang.bam("document.querySelector('[data-mo-khoi-pv=\"ai-mot\"]')")
        trang.cho("document.querySelectorAll('[data-tai-khoan]').length === 0", mo_ta="đóng khối ai-mot")
        assert trang.js("document.querySelector('[data-mo-khoi-pv=\"ai-mot\"]').getAttribute('aria-expanded')") == "false"


def test_c_neo_tk_mo_dung_khoi(trang, tmp_path, monkeypatch):
    """(c) neo #tk-<id> mở đúng khối và cuộn tới."""
    with moi_truong_ba_nguon(tmp_path, monkeypatch) as w:
        lien_ket_khoa(w, "ai-mot", [K1, K2])
        lien_ket_khoa(w, "ai-hai", [K3, K4])

        dang_nhap_quan_tri(trang, w.url)
        # Mở trang bằng neo #tk-ai-hai
        trang.di(w.url + "/He-thong-AI.dc.html#tk-ai-hai")
        trang.cho("document.getElementById('bang-ai')?.dataset.muc === 'tai_khoan'", han=15, mo_ta="vào tab Tài khoản qua neo")

        # Khối ai-hai TỰ MỞ và hiện 2 dòng khoá của ai-hai
        trang.cho("document.querySelector('[data-mo-khoi-pv=\"ai-hai\"]')?.getAttribute('aria-expanded') === 'true'", han=15, mo_ta="khối ai-hai tự mở")
        assert trang.js("document.querySelector('[data-mo-khoi-pv=\"ai-mot\"]').getAttribute('aria-expanded')") == "false"

        tk_hien_tai = trang.js("[...document.querySelectorAll('[data-tai-khoan]')].map(d => d.dataset.taiKhoan)")
        assert tk_hien_tai == ["ai-hai", "ai-hai"]

        # Form thêm API cũng mở sẵn cho ai-hai
        assert trang.js("document.querySelector('#form-them')?.dataset.module") == "ai-hai"


def test_d_tab_provider_danh_sach_chon_chi_co_nguon_co_khoa_thu_gon_luc_dau(trang, tmp_path, monkeypatch):
    """(d) tab Provider: danh sách chọn có đúng 2 provider, KHÔNG có nguồn 0 khoá, vẫn thu gọn lúc đầu (menu chọn đóng, bấm mới xổ)."""
    with moi_truong_ba_nguon(tmp_path, monkeypatch) as w:
        lien_ket_khoa(w, "ai-mot", [K1, K2])
        lien_ket_khoa(w, "ai-hai", [K3, K4])
        # ai-ba 0 khoá

        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        trang.cho("document.getElementById('bang-ai')?.dataset.muc === 'provider'", han=15, mo_ta="vào tab Provider")
        trang.cho("!!document.getElementById('chon-pv')", han=15, mo_ta="nút chọn provider")

        # Danh sách chọn ban đầu THU GỌN (menu listbox chưa mở, aria-expanded=false)
        assert trang.js("document.getElementById('chon-pv').getAttribute('aria-expanded')") == "false"
        assert trang.js("document.querySelector('[role=listbox]')") is None

        # Bấm nút chọn provider ⇒ menu xổ ra
        trang.bam("document.getElementById('chon-pv')")
        trang.cho("!!document.querySelector('[role=listbox]')", mo_ta="menu provider xổ ra")
        assert trang.js("document.getElementById('chon-pv').getAttribute('aria-expanded')") == "true"

        # Danh sách chọn có đúng 2 provider theo tên hiển thị (ai-hai < ai-mot), KHÔNG có nguồn 0 khoá (ai-ba)
        ds_pv = trang.js("[...document.querySelectorAll('[data-pv]')].map(b => b.dataset.pv)")
        assert ds_pv == ["ai-hai", "ai-mot"]
        assert "ai-ba" not in ds_pv


def test_e_xoa_khoa_cuoi_provider_bien_khoi_danh_sach_va_tu_chuyen_provider(trang, tmp_path, monkeypatch):
    """(e) xoá khoá cuối của một provider ⇒ provider biến khỏi danh sách chọn và khối biến khỏi tab Tài khoản, provider đang chọn tự chuyển."""
    with moi_truong_ba_nguon(tmp_path, monkeypatch) as w:
        # ai-hai chỉ có 1 khoá K3; ai-mot có 2 khoá K1, K2
        lien_ket_khoa(w, "ai-hai", [K3])
        lien_ket_khoa(w, "ai-mot", [K1, K2])

        dang_nhap_quan_tri(trang, w.url)
        # Tại tab Provider ban đầu chọn ai-hai (do 'AI Thứ Hai' < 'AI Thứ Nhất' theo tên hiển thị nên đứng đầu)
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        trang.cho("document.querySelector('[data-provider]')?.dataset.provider === 'ai-hai'", han=15, mo_ta="provider ban đầu ai-hai")

        # Chuyển sang tab Tài khoản để xoá khoá duy nhất của ai-hai
        trang.bam("document.querySelector('[data-nav=\"ai\"]')")
        trang.cho("!!document.querySelector('[data-nav-con=\"tai_khoan\"]')", han=5, mo_ta="menu con tai_khoan")
        trang.bam("document.querySelector('[data-nav-con=\"tai_khoan\"]')")
        trang.cho("document.querySelectorAll('[data-khoi-pv]').length === 2", han=15, mo_ta="2 khối tài khoản")

        # Mở khối ai-hai
        trang.cho("!!document.querySelector('[data-mo-khoi-pv=\"ai-hai\"]')", han=15, mo_ta="khối ai-hai")
        trang.bam("document.querySelector('[data-mo-khoi-pv=\"ai-hai\"]')")
        trang.cho("document.querySelectorAll('[data-tai-khoan=\"ai-hai\"]').length === 1", mo_ta="1 dòng khoá ai-hai")

        # Mở chi tiết dòng khoá ai-hai và bấm xoá (2 bước)
        trang.bam("document.querySelector('[data-tai-khoan=\"ai-hai\"] button')")
        trang.cho("!!document.querySelector('[data-tai-khoan=\"ai-hai\"] [data-hd=xoa]')", mo_ta="nút xoá")
        trang.bam("document.querySelector('[data-tai-khoan=\"ai-hai\"] [data-hd=xoa]')")
        trang.bam("document.querySelector('[data-tai-khoan=\"ai-hai\"] [data-hd=xoa]')")

        # Sau khi xoá: khối ai-hai BIẾN MẤT khỏi tab Tài khoản
        trang.cho("document.querySelectorAll('[data-khoi-pv]').length === 1", han=15, mo_ta="chỉ còn 1 khối provider")
        assert trang.js("document.querySelector('[data-khoi-pv=\"ai-hai\"]')") is None
        assert trang.js("document.querySelector('[data-khoi-pv=\"ai-mot\"]')") is not None

        # Chuyển sang tab Provider: ai-hai biến khỏi danh sách chọn và provider đang chọn tự chuyển sang ai-mot
        trang.bam("document.querySelector('[data-nav=\"ai\"]')")
        trang.cho("!!document.querySelector('[data-nav-con=\"provider\"]')", han=5, mo_ta="menu con provider")
        trang.bam("document.querySelector('[data-nav-con=\"provider\"]')")
        trang.cho("document.querySelector('[data-provider]')?.dataset.provider === 'ai-mot'", han=15, mo_ta="tự chuyển sang ai-mot")

        trang.cho("!!document.getElementById('chon-pv')", han=15, mo_ta="nút chọn provider")
        trang.bam("document.getElementById('chon-pv')")
        trang.cho("!!document.querySelector('[role=listbox]')", mo_ta="menu provider")
        ds_pv = trang.js("[...document.querySelectorAll('[data-pv]')].map(b => b.dataset.pv)")
        assert ds_pv == ["ai-mot"]
        assert "ai-hai" not in ds_pv


def test_f_khong_provider_nao_co_khoa_trang_thai_rong_va_form_them_api_du_nguon(trang, tmp_path, monkeypatch):
    """(f) không provider nào có khoá ⇒ trạng thái rỗng có nút «Mở tab Tài khoản» và biểu mẫu Thêm API vẫn liệt kê cả nguồn 0 khoá."""
    with moi_truong_ba_nguon(tmp_path, monkeypatch) as w:
        # Cả 3 nguồn đều 0 khoá

        dang_nhap_quan_tri(trang, w.url)
        # Tab Provider có trạng thái rỗng và nút «Mở tab Tài khoản»
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        trang.cho("!!document.getElementById('pv-trong')", han=15, mo_ta="trạng thái rỗng ở provider")
        assert trang.js("!!document.getElementById('nut-mo-tk')")
        trang.chup("trang-thai-rong-tab-provider-chua-co-khoa")

        # Bấm nút «Mở tab Tài khoản»
        trang.bam("document.getElementById('nut-mo-tk')")
        trang.cho("document.getElementById('bang-ai')?.dataset.muc === 'tai_khoan'", han=15, mo_ta="chuyển sang tab Tài khoản")

        # Tab Tài khoản có thông báo rỗng, không khối nào
        assert trang.js("!!document.getElementById('tk-trong')")
        assert trang.js("document.querySelectorAll('[data-khoi-pv]').length") == 0
        trang.chup("trang-thai-rong-tab-tai-khoan-chua-co-khoa")

        # Biểu mẫu «＋ Thêm API» mở ra và liệt kê ĐỦ cả 3 nguồn (kể cả 0 khoá)
        trang.cho("!!document.getElementById('form-them')", han=15, mo_ta="form thêm API mở ra")
        danh_sach_module = trang.js("[...document.querySelectorAll('[data-chon-module]')].map(b => b.dataset.chonModule)")
        assert sorted(danh_sach_module) == ["ai-ba", "ai-hai", "ai-mot"]


def test_g_them_khoa_dau_tien_cho_nguon_0_khoa_khoi_provider_xuat_hien(trang, tmp_path, monkeypatch):
    """(g) thêm khoá đầu tiên cho nguồn 0 khoá ⇒ khối provider xuất hiện."""
    with moi_truong_ba_nguon(tmp_path, monkeypatch) as w:
        # ai-mot có 2 khoá, ai-hai có 2 khoá, ai-ba 0 khoá
        lien_ket_khoa(w, "ai-mot", [K1, K2])
        lien_ket_khoa(w, "ai-hai", [K3, K4])

        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html#tai-khoan")
        trang.cho("document.querySelectorAll('[data-khoi-pv]').length === 2", han=15, mo_ta="ban đầu 2 khối")
        assert trang.js("document.querySelector('[data-khoi-pv=\"ai-ba\"]')") is None

        # Bấm nút «＋ Thêm API»
        trang.bam("document.getElementById('nut-them')")
        trang.cho("!!document.getElementById('form-them')", mo_ta="mở form thêm")

        # Chọn nguồn ai-ba
        trang.bam("document.querySelector('[data-chon-module=\"ai-ba\"]')")
        trang.cho("document.querySelector('#form-them')?.dataset.module === 'ai-ba'", mo_ta="chọn ai-ba")

        # Nhập khoá K1 vào form và bấm lưu
        trang.go("#form-them [data-truong='api_keys'] input", K1)
        trang.bam("document.getElementById('nut-them-luu')")

        # Sau khi lưu: khối provider ai-ba XUẤT HIỆN trong tab Tài khoản
        trang.cho("!!document.querySelector('[data-khoi-pv=\"ai-ba\"]')", han=15, mo_ta="khối ai-ba xuất hiện")
        assert trang.js("document.querySelectorAll('[data-khoi-pv]').length") == 3

        # Thứ tự các khối đúng quy tắc tên hiển thị ('AI Thứ Ba' < 'AI Thứ Hai' < 'AI Thứ Nhất')
        khoi_ids_sau = trang.js("[...document.querySelectorAll('[data-khoi-pv]')].map(s => s.dataset.khoiPv)")
        assert khoi_ids_sau == ["ai-ba", "ai-hai", "ai-mot"]

        # Khối ai-ba mới thêm tự động mở và hiển thị khoá vừa thêm
        assert trang.js("document.querySelector('[data-mo-khoi-pv=\"ai-ba\"]').getAttribute('aria-expanded')") == "true"
        assert trang.js("document.querySelectorAll('[data-tai-khoan=\"ai-ba\"]').length") == 1


def test_h_vai_phien_dang_nhap_va_khoa_hien_thi_che_an_toan(trang, tmp_path, monkeypatch):
    """(h) vai/phiên: mọi thứ trên chỉ sau đăng nhập (dùng dang_nhap_quan_tri); khoá hiển thị chỉ dạng che."""
    import asyncio
    from app import nguoi_dung

    with moi_truong_ba_nguon(tmp_path, monkeypatch) as w:
        lien_ket_khoa(w, "ai-mot", [K1, K2])

        # Tạo tài khoản quản trị trước để kho có admin (tránh bị chuyển sang Thiet-lap.dc.html)
        asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin_kiem_thu", mat_khau="MatKhauAdminBaoMat123456"))

        # 1. Chưa đăng nhập: mở trang He-thong-AI.dc.html bị chuyển hướng về Dang-nhap.dc.html
        trang.di(w.url + "/He-thong-AI.dc.html")
        trang.cho("location.pathname.endsWith('/Dang-nhap.dc.html')", han=15, mo_ta="chưa đăng nhập chuyển về trang đăng nhập")

        # 2. Đăng nhập quản trị viên
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html#tai-khoan")
        trang.cho("document.querySelectorAll('[data-khoi-pv]').length === 1", han=15, mo_ta="vào trang sau đăng nhập")

        # Mở khối ai-mot
        trang.bam("document.querySelector('[data-mo-khoi-pv=\"ai-mot\"]')")
        trang.cho("document.querySelectorAll('[data-tai-khoan=\"ai-mot\"]').length === 2", mo_ta="mở dòng khoá")

        # Mở chi tiết dòng khoá
        trang.bam("document.querySelector('[data-tai-khoan=\"ai-mot\"][data-khoa-so=\"0\"] button')")
        trang.cho("!!document.querySelector('[data-kv-tk]')", mo_ta="chi tiết khoá")

        # Toàn bộ nội dung hiển thị trong DOM chỉ có khoá dạng che (…abcd), không chứa chuỗi khoá đầy đủ
        toan_bo_chu = trang.js("document.body.innerText")
        assert K1 not in toan_bo_chu
        assert K2 not in toan_bo_chu
        assert f"…{K1[-4:]}" in toan_bo_chu


def test_thu_tu_on_dinh_khong_phu_thuoc_thu_tu_them_khoa(trang, tmp_path, monkeypatch):
    """Chứng minh thứ tự hiển thị không phụ thuộc thứ tự thêm khoá/khám phá:
    tạo khoá theo thứ tự ngược (ai-mot trước, rồi ai-hai) ⇒ cùng thứ tự hiển thị (ai-hai trước ai-mot theo tên hiển thị)."""
    with moi_truong_ba_nguon(tmp_path, monkeypatch) as w:
        # Thêm khoá theo thứ tự ngược: ai-mot trước, rồi mới tới ai-hai
        lien_ket_khoa(w, "ai-mot", [K1, K2])
        lien_ket_khoa(w, "ai-hai", [K3, K4])

        dang_nhap_quan_tri(trang, w.url)
        # 1. Kiểm tra ở tab Tài khoản: thứ tự khối luôn là ai-hai rồi ai-mot (tên 'AI Thứ Hai' < 'AI Thứ Nhất')
        trang.di(w.url + "/He-thong-AI.dc.html#tai-khoan")
        trang.cho("document.querySelectorAll('[data-khoi-pv]').length === 2", han=15, mo_ta="2 khối provider")
        khoi_ids = trang.js("[...document.querySelectorAll('[data-khoi-pv]')].map(s => s.dataset.khoiPv)")
        assert khoi_ids == ["ai-hai", "ai-mot"]

        # 2. Kiểm tra ở tab Provider: danh sách chọn provider cũng luôn là ai-hai rồi ai-mot
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        trang.cho("document.getElementById('bang-ai')?.dataset.muc === 'provider'", han=15, mo_ta="vào tab Provider")
        trang.cho("!!document.getElementById('chon-pv')", han=15, mo_ta="nút chọn provider")
        trang.bam("document.getElementById('chon-pv')")
        trang.cho("!!document.querySelector('[role=listbox]')", mo_ta="menu provider")
        ds_pv = trang.js("[...document.querySelectorAll('[data-pv]')].map(b => b.dataset.pv)")
        assert ds_pv == ["ai-hai", "ai-mot"]


# ─────────────────────────────────────────────────────────────────────────────
# LƯỢT 2: BỐ CỤC DÀY, GỌN, DỄ KIỂM SOÁT (7 TEST ĐO THẬT BẰNG getBoundingClientRect)
# ─────────────────────────────────────────────────────────────────────────────

def module_ai_nhieu_model(id_: str = "ai-haieu", ten: str = "AI Nhiều Model", tien_to: str = "nhieu", so_model: int = 52) -> dict:
    """Nguồn AI giả có nhiều model để kiểm thử bố cục lưới và hiệu năng cuộn."""
    m = {
        "id": id_, "ten": ten, "mo_ta": "Nguồn AI giả nhiều model cho test bố cục.",
        "phien_ban": "1.0.0", "hang": "A", "loai": "ai", "loai_ten": "AI", "loai_thu_tu": 10, "giao_thuc": "openai", "tien_to": tien_to,
        "cung_cap": ["chat"],
        "truong_cau_hinh": [
            {"khoa": "api_keys", "nhan": "Khoá API", "kieu": "danh_sach_bi_mat", "bat_buoc": True, "mac_dinh": None,
             "mo_ta": "Một hoặc nhiều khoá giả."}
        ],
        "tai_nguyen": {"ram_them_mb": 64, "dia_mb": None, "gpu": False, "mang_ra": [], "ghi_chu": ""},
    }
    ma_py = f'''
class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self.khoa = []

    def nap(self, cau_hinh):
        self.khoa = list(cau_hinh.get("api_keys") or [])

    async def cai(self, cau_hinh):
        khoa = list(cau_hinh.get("api_keys") or [])
        ds = [{{"khoa": "…" + k[-4:], "ok": "hong" not in k, "do_tre_ms": 7, "so_model": {so_model} if "hong" not in k else 0,
               "loi": None if "hong" not in k else "HTTP 401: khoá không hợp lệ (giả)"}} for k in khoa]
        tot = [k for k in khoa if "hong" not in k]
        if not tot:
            return {{"ok": False, "ket_qua_tung_khoa": ds, "loi": "Không khoá nào dùng được (giả)."}}
        return {{"ok": True, "ket_qua_tung_khoa": ds, "loi": None, "cau_hinh_luu": {{"api_keys": khoa}}}}

    async def chat(self, body, stream):
        return 200, {{"content-type": "application/json"}}, b'{{"ok":true}}'

    async def models(self):
        t = self.tien_to
        return [{{"id": f"{{t}}/model-tro-chuyen-{{i:02d}}", "object": "model"}} for i in range(1, {so_model} + 1)]

    async def suc_khoe(self):
        return {{"ok": True, "khoa": [
            {{"khoa": "…" + k[-4:], "ok": "hong" not in k, "do_tre_ms": 5, "so_model": {so_model} if "hong" not in k else 0,
             "loi": None if "hong" not in k else "HTTP 401: khoá hỏng (giả)"}} for k in self.khoa
        ]}}

    async def dung(self):
        pass
'''
    return {
        "manifest.json": json.dumps(m, ensure_ascii=False).encode(),
        "module.py": ma_py.encode(),
    }


@contextmanager
def moi_truong_nhieu_model(tmp_path, monkeypatch, so_model: int = 52):
    """Môi trường kiểm thử có provider giả với nhiều model và nhiều khoá."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")

    mods = {
        "ai-haieu": module_ai_nhieu_model("ai-haieu", "AI Nhiều Model", "nhieu", so_model=so_model),
    }
    for id_, tep in mods.items():
        ghi_thu_muc(tmp_path / "modules" / id_, tep)

    with MayChuThat(app, lifespan="on") as w:
        yield w


def lien_ket_khoa_nhieu(w, id_: str, keys: list[str]):
    r = httpx.post(f"{w.url}/api/modules/{id_}/cai", json={"cau_hinh": {"api_keys": keys}}, headers=QT)
    assert r.status_code == 200 and r.json()["ok"], r.text


def mo_nhom_model(trang, nhom: str = "chat", so_model: int = 52):
    """Chờ nhóm model xuất hiện trên tab Provider, bấm mở và chờ đủ số model dựng xong."""
    trang.cho(f"!!document.querySelector('[data-mo-nhom=\"{nhom}\"]')", han=15, mo_ta=f"nhóm {nhom} xuất hiện")
    trang.bam(f"document.querySelector('[data-mo-nhom=\"{nhom}\"]')")
    trang.cho(f"document.querySelectorAll('[data-model]').length === {so_model}", han=15, mo_ta=f"đủ {so_model} model")


def test_bo_cuc_a_so_cot_dung_nguong_cac_kho(trang, tmp_path, monkeypatch):
    """(a) số cột đúng ngưỡng ở mỗi khổ: >= 4 ở 1900px, >= 3 ở 1280px, == 1 ở 390px (không cuộn ngang)."""
    with moi_truong_nhieu_model(tmp_path, monkeypatch, so_model=52) as w:
        lien_ket_khoa_nhieu(w, "ai-haieu", [K1, K2])
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        trang.cho("document.querySelector('[data-provider]')?.dataset.provider === 'ai-haieu'", han=15)
        mo_nhom_model(trang, "chat", 52)

        # 1900x900: >= 4 cột
        trang.khung(1900, 900)
        trang.chup("tab-provider-52-model-1900px")
        so_cot_1900 = trang.js("""(() => {
            const els = [...document.querySelectorAll('[data-model]')];
            if (!els.length) return 0;
            const top0 = els[0].getBoundingClientRect().top;
            return els.filter(e => Math.abs(e.getBoundingClientRect().top - top0) < 6).length;
        })()""")
        assert so_cot_1900 >= 4, f"Khổ 1900px phải >= 4 cột, thực tế {so_cot_1900}"

        # 1280x800: >= 3 cột
        trang.khung(1280, 800)
        so_cot_1280 = trang.js("""(() => {
            const els = [...document.querySelectorAll('[data-model]')];
            if (!els.length) return 0;
            const top0 = els[0].getBoundingClientRect().top;
            return els.filter(e => Math.abs(e.getBoundingClientRect().top - top0) < 6).length;
        })()""")
        assert so_cot_1280 >= 3, f"Khổ 1280px phải >= 3 cột, thực tế {so_cot_1280}"

        # 390x844: đúng 1 cột
        trang.khung(390, 844, True)
        trang.chup("tab-provider-52-model-390px")
        so_cot_390 = trang.js("""(() => {
            const els = [...document.querySelectorAll('[data-model]')];
            if (!els.length) return 0;
            const top0 = els[0].getBoundingClientRect().top;
            return els.filter(e => Math.abs(e.getBoundingClientRect().top - top0) < 6).length;
        })()""")
        assert so_cot_390 == 1, f"Khổ 390px phải đúng 1 cột, thực tế {so_cot_390}"


def test_bo_cuc_b_chieu_cao_o_model_gon(trang, tmp_path, monkeypatch):
    """(b) cao mỗi ô model <= 44 px."""
    with moi_truong_nhieu_model(tmp_path, monkeypatch, so_model=52) as w:
        lien_ket_khoa_nhieu(w, "ai-haieu", [K1, K2])
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        mo_nhom_model(trang, "chat", 52)

        trang.khung(1900, 900)
        trang.cho("""(() => {
            const els = [...document.querySelectorAll('[data-model]')];
            if (els.length !== 52) return false;
            const hs = els.map(e => e.getBoundingClientRect().height);
            return Math.min(...hs) >= 24 && Math.max(...hs) <= 44;
        })()""", han=5, mo_ta="kích thước ô model ổn định trong khoảng 24-44px")
        cao_max = trang.js("""(() => {
            const els = [...document.querySelectorAll('[data-model]')];
            return Math.max(...els.map(e => e.getBoundingClientRect().height));
        })()""")
        cao_min = trang.js("""(() => {
            const els = [...document.querySelectorAll('[data-model]')];
            return Math.min(...els.map(e => e.getBoundingClientRect().height));
        })()""")
        assert cao_max <= 44, f"Cao mỗi ô model phải <= 44px, thực tế max={cao_max}"
        assert cao_min >= 24, f"Ô model phải có chiều cao thực tế, min={cao_min}"


def test_bo_cuc_c_khoang_cach_ten_va_cong_tac_trong_o(trang, tmp_path, monkeypatch):
    """(c) khoảng cách ngang từ mép phải tên model tới công tắc <= bề rộng ô, và công tắc nằm TRONG ô (rect.right <= rect.right của ô)."""
    with moi_truong_nhieu_model(tmp_path, monkeypatch, so_model=52) as w:
        lien_ket_khoa_nhieu(w, "ai-haieu", [K1, K2])
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        mo_nhom_model(trang, "chat", 52)

        trang.khung(1900, 900)
        kq = trang.js("""(() => {
            const o = document.querySelector('[data-model]');
            const ten = o.querySelector('span');
            const sw = o.querySelector('[role="switch"]');
            const rO = o.getBoundingClientRect();
            const rTen = ten.getBoundingClientRect();
            const rSw = sw.getBoundingClientRect();
            return {
                cong_tac_trong_o: rSw.right <= rO.right + 0.5 && rSw.left >= rO.left,
                khoang_cach: rSw.left - rTen.right,
                be_rong_o: rO.width,
                be_rong_ten: rTen.width
            };
        })()""")
        assert kq["cong_tac_trong_o"] is True, "Công tắc phải nằm trong ô (rect.right <= rect.right của ô)"
        assert 0 <= kq["khoang_cach"] <= kq["be_rong_o"], f"Khoảng cách tên <-> công tắc ({kq['khoang_cach']}) phải <= bề rộng ô ({kq['be_rong_o']})"
        assert kq["be_rong_o"] > 0


def test_bo_cuc_d_dien_thoai_390px_khong_cuon_ngang(trang, tmp_path, monkeypatch):
    """(d) 390 px: document.documentElement.scrollWidth <= innerWidth (không cuộn ngang)."""
    with moi_truong_nhieu_model(tmp_path, monkeypatch, so_model=52) as w:
        lien_ket_khoa_nhieu(w, "ai-haieu", [K1, K2])
        dang_nhap_quan_tri(trang, w.url)

        trang.khung(390, 844, True)
        # Tab Provider có danh sách 52 model mở
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        mo_nhom_model(trang, "chat", 52)
        trang.cho("document.documentElement.scrollWidth <= window.innerWidth", han=5, mo_ta="bố cục 390px không tràn ngang ở Provider")
        scroll_pv = trang.js("document.documentElement.scrollWidth")
        inner_w = trang.js("window.innerWidth")
        assert scroll_pv <= inner_w, f"Tab Provider bị tràn ngang ở 390px: scrollWidth={scroll_pv} > innerWidth={inner_w}"

        # Tab Tài khoản có khối mở
        trang.di(w.url + "/He-thong-AI.dc.html#tai-khoan")
        trang.cho("!!document.querySelector('[data-mo-khoi-pv=\"ai-haieu\"]')", han=15, mo_ta="khối ai-haieu xuất hiện")
        trang.bam("document.querySelector('[data-mo-khoi-pv=\"ai-haieu\"]')")
        trang.cho("document.querySelectorAll('[data-tai-khoan=\"ai-haieu\"]').length === 2", han=15)
        trang.cho("document.documentElement.scrollWidth <= window.innerWidth", han=5, mo_ta="bố cục 390px không tràn ngang ở Tài khoản")
        scroll_tk = trang.js("document.documentElement.scrollWidth")
        assert scroll_tk <= inner_w, f"Tab Tài khoản bị tràn ngang ở 390px: scrollWidth={scroll_tk} > innerWidth={inner_w}"


def test_bo_cuc_e_o_loc_model_va_chon_tat_ca_khi_loc(trang, tmp_path, monkeypatch):
    """(e) ô lọc: gõ chuỗi => chỉ còn model khớp, đếm «n/m» đúng, xoá => đủ,
    model ẩn KHÔNG đổi trạng thái bật/tắt (đọc lại trạng thái sau lọc),
    «Chọn tất cả» khi đang lọc chỉ bật các model đang hiển thị."""
    with moi_truong_nhieu_model(tmp_path, monkeypatch, so_model=52) as w:
        lien_ket_khoa_nhieu(w, "ai-haieu", [K1, K2])
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        mo_nhom_model(trang, "chat", 52)

        # 1. Ban đầu có 52 model
        trang.cho("document.getElementById('dem-loc-md')?.innerText?.includes('52')", han=5, mo_ta="bộ đếm lọc ban đầu hiện 52")
        dem_dau = trang.chu("#dem-loc-md")
        assert "52" in dem_dau

        # 2. Gõ "05" vào ô lọc
        trang.cho("!!document.getElementById('loc-md')", han=5, mo_ta="ô lọc sẵn sàng")
        trang.go("#loc-md", "05")
        trang.cho("document.querySelectorAll('[data-model]').length === 1", han=15, mo_ta="lọc chỉ còn model 05")
        trang.cho("document.getElementById('dem-loc-md')?.innerText?.includes('1')", han=5, mo_ta="bộ đếm cập nhật sau khi lọc")
        dem_sau = trang.chu("#dem-loc-md")
        assert "1/52" in dem_sau or ("1" in dem_sau and "52" in dem_sau)

        # 3. Tắt model 05 đang hiển thị
        trang.cho("!!document.querySelector('[data-cong-tac=\"nhieu/model-tro-chuyen-05\"]')", han=5)
        trang.bam("document.querySelector('[data-cong-tac=\"nhieu/model-tro-chuyen-05\"]')")
        trang.cho("document.querySelector('[data-cong-tac=\"nhieu/model-tro-chuyen-05\"]').getAttribute('aria-checked') === 'false'")

        # 4. Xoá lọc
        trang.js("(() => { const inp = document.getElementById('loc-md'); inp.value = ''; inp.dispatchEvent(new Event('input', {bubbles: true})); })()")
        trang.cho("document.querySelectorAll('[data-model]').length === 52", han=15, mo_ta="xoá lọc trở lại 52 model")
        trang.cho("document.getElementById('dem-loc-md')?.innerText?.includes('52')", han=5, mo_ta="bộ đếm cập nhật lại 52")
        dem_xoa = trang.chu("#dem-loc-md")
        assert "52" in dem_xoa

        # 5. Model 05 vẫn tắt, các model khác (từng bị ẩn trong lúc lọc) vẫn bật
        assert trang.js("document.querySelector('[data-cong-tac=\"nhieu/model-tro-chuyen-05\"]').getAttribute('aria-checked')") == "false"
        assert trang.js("document.querySelector('[data-cong-tac=\"nhieu/model-tro-chuyen-01\"]').getAttribute('aria-checked')") == "true"

        # 6. Thử tắt tất cả bằng «Bỏ chọn tất cả»
        trang.cho("!!document.querySelector('[data-bo-chon-tat-ca=\"chat\"]')", han=5)
        trang.bam("document.querySelector('[data-bo-chon-tat-ca=\"chat\"]')")
        trang.cho("document.querySelectorAll('[data-cong-tac][aria-checked=\"false\"]').length === 52")

        # 7. Lọc "model-tro-chuyen-0" (khớp 01 đến 09: đúng 9 model)
        trang.go("#loc-md", "model-tro-chuyen-0")
        trang.cho("document.querySelectorAll('[data-model]').length === 9", han=15)

        # 8. Bấm «Chọn tất cả» trong khi đang lọc
        trang.cho("!!document.querySelector('[data-chon-tat-ca=\"chat\"]')", han=5)
        trang.bam("document.querySelector('[data-chon-tat-ca=\"chat\"]')")
        trang.cho("document.querySelectorAll('[data-cong-tac][aria-checked=\"true\"]').length === 9")

        # 9. Xoá lọc để xem toàn bộ 52 model
        trang.js("(() => { const inp = document.getElementById('loc-md'); inp.value = ''; inp.dispatchEvent(new Event('input', {bubbles: true})); })()")
        trang.cho("document.querySelectorAll('[data-model]').length === 52", han=15)

        # Chỉ đúng 9 model (01..09) được bật, 43 model còn lại VẪN TẮT
        so_bat = trang.js("document.querySelectorAll('[data-cong-tac][aria-checked=\"true\"]').length")
        assert so_bat == 9, f"Chỉ 9 model hiển thị sau lọc được bật, thực tế {so_bat} model bật"


def test_bo_cuc_f_tab_tai_khoan_so_cot_va_chi_tiet_full_hang_va_tom_tat(trang, tmp_path, monkeypatch):
    """(f) tab Tài khoản: >= 2/3 cột đúng khổ, thẻ mở chi tiết chiếm cả hàng, tiêu đề khối có tóm tắt đúng số «dùng được/lỗi»."""
    K_HONG = "AIzaSy-KHOA-HAI-hong-2222"
    with moi_truong_nhieu_model(tmp_path, monkeypatch, so_model=52) as w:
        # Cài 3 khoá: K1 (tốt), K2 (tốt), K_HONG (hỏng)
        lien_ket_khoa_nhieu(w, "ai-haieu", [K1, K2, K_HONG])
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html#tai-khoan")
        trang.cho("document.querySelectorAll('[data-khoi-pv]').length === 1", han=15)

        # 1. Tiêu đề khối có tóm tắt đúng số «dùng được/lỗi» ngay ở tiêu đề
        tieu_de = trang.chu("[data-khoi-pv='ai-haieu'] [data-mo-khoi-pv='ai-haieu']")
        assert "3" in tieu_de, f"Phải có số lượng 3 khoá, thực tế: {tieu_de}"
        assert "2 dùng được" in tieu_de, f"Phải có '2 dùng được', thực tế: {tieu_de}"
        assert "1 lỗi" in tieu_de, f"Phải có '1 lỗi', thực tế: {tieu_de}"

        # Mở khối provider
        trang.cho("!!document.querySelector('[data-mo-khoi-pv=\"ai-haieu\"]')", han=15)
        trang.bam("document.querySelector('[data-mo-khoi-pv=\"ai-haieu\"]')")
        trang.cho("document.querySelectorAll('[data-tai-khoan=\"ai-haieu\"]').length === 3", han=15)

        # 2. Số cột thẻ ở 1900px (>= 3) và 1280px (>= 2)
        trang.khung(1900, 900)
        so_cot_1900 = trang.js("""(() => {
            const els = [...document.querySelectorAll('[data-tai-khoan]')];
            if (!els.length) return 0;
            const top0 = els[0].getBoundingClientRect().top;
            return els.filter(e => Math.abs(e.getBoundingClientRect().top - top0) < 6).length;
        })()""")
        assert so_cot_1900 >= 3, f"Tab Tài khoản ở 1900px phải >= 3 cột thẻ, thực tế {so_cot_1900}"

        trang.khung(1280, 800)
        so_cot_1280 = trang.js("""(() => {
            const els = [...document.querySelectorAll('[data-tai-khoan]')];
            if (!els.length) return 0;
            const top0 = els[0].getBoundingClientRect().top;
            return els.filter(e => Math.abs(e.getBoundingClientRect().top - top0) < 6).length;
        })()""")
        assert so_cot_1280 >= 2, f"Tab Tài khoản ở 1280px phải >= 2 cột thẻ, thực tế {so_cot_1280}"

        # 3. Thẻ mở chi tiết chiếm cả hàng (grid-column: 1 / -1, tỷ lệ chiều rộng > 90% so với lưới)
        trang.bam("document.querySelector('[data-tai-khoan=\"ai-haieu\"][data-khoa-so=\"0\"] button')")
        trang.cho("!!document.querySelector('[data-tai-khoan=\"ai-haieu\"][data-khoa-so=\"0\"] [data-kv-tk]')", han=15)
        ty_le = trang.js("""(() => {
            const the = document.querySelector('[data-tai-khoan=\"ai-haieu\"][data-khoa-so=\"0\"]');
            const luoi = the.parentElement;
            return the.getBoundingClientRect().width / luoi.getBoundingClientRect().width;
        })()""")
        assert ty_le > 0.9, f"Thẻ mở chi tiết phải chiếm cả hàng (tỷ lệ {ty_le} phải > 0.9)"


def test_bo_cuc_g_giam_chieu_cao_52_model_o_1900px(trang, tmp_path, monkeypatch):
    """(g) tổng chiều cao 52 model ở 1900 px giảm rõ: scrollHeight của danh sách <= 25% so với bố cục cũ (<= 850 px)."""
    with moi_truong_nhieu_model(tmp_path, monkeypatch, so_model=52) as w:
        lien_ket_khoa_nhieu(w, "ai-haieu", [K1, K2])
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        mo_nhom_model(trang, "chat", 52)

        trang.khung(1900, 900)
        trang.cho("""(() => {
            const el = document.querySelector('[data-danh-sach-md="chat"]');
            if (!el || window.innerWidth !== 1900) return false;
            const h = el.scrollHeight;
            return h >= 300 && h <= 850;
        })()""", han=5, mo_ta="chiều cao danh sách 52 model ở 1900px ổn định trong khoảng 300-850px")
        scroll_height = trang.js("document.querySelector('[data-danh-sach-md=\"chat\"]').scrollHeight")
        # Bố cục cũ: 52 x 65 px = 3380 px. Yêu cầu <= 25% là <= 850 px.
        assert scroll_height <= 850, f"Chiều cao danh sách 52 model ({scroll_height}px) phải <= 850px (25% của 3380px)"
        assert scroll_height >= 300, f"Chiều cao danh sách phải là kích thước thực, min={scroll_height}px"

