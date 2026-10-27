"""Kiểm thử giao diện Chrome cho tính năng nhóm tài khoản theo provider và ẩn provider 0 khoá (Hệ thống → AI).

Cập nhật NV268: Tab «Tài khoản» vẽ kho tài khoản đã lưu (khu #k268: nhóm → loại → ô + bảng chi tiết).
Khoá API của module nằm ở tab Provider → tab con [data-pv-tab=tk].
8 ca kiểm bắt buộc:
(a) tab Tài khoản có khu #k268 hiển thị đúng số tài khoản theo nhóm, nhóm 0 tài khoản hiện câu rỗng; tab Provider chỉ có provider có khoá;
(b) lúc mở trang #k268-ct chưa mở, aria-pressed=false; bấm chip lọc nhóm lọc đúng; bấm ô mở chi tiết #k268-ct.mo; bấm #k268-dong đóng;
(c) neo #tk-<id> mở tab Tài khoản và biểu mẫu #form-them cho module đó, focus ô nhập;
(d) tab Provider: danh sách chọn có đúng 2 provider, KHÔNG có nguồn 0 khoá, vẫn thu gọn lúc đầu (menu chọn đóng, bấm mới xổ);
(e) xoá khoá cuối của một provider ⇒ provider biến khỏi danh sách chọn và provider đang chọn tự chuyển; khu #k268 vẽ đủ; xoá tài khoản cuối nhóm ⇒ ô biến mất, câu rỗng hiện;
(f) không provider nào có khoá ⇒ trạng thái rỗng có nút «Mở tab Tài khoản»; ở tab Tài khoản không ô nào, mọi nhóm hiện câu rỗng; form thêm mở đủ nguồn;
(g) thêm khoá đầu tiên cho nguồn 0 khoá qua #form-them ⇒ provider xuất hiện ở tab Provider đúng thứ tự;
(h) vai/phiên: chưa đăng nhập chuyển về Dang-nhap.dc.html; sau đăng nhập, ở CẢ hai nơi khoá hiển thị chỉ dạng che.
Thêm: thứ tự ổn định không phụ thuộc thứ tự thêm; kiểm tra bố cục 390px, 1280px bằng getBoundingClientRect.
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

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

KHOA_QT = "khoa-quan-tri-giao-dien-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

# Các khoá API giả nằm trong allowlist của .gitleaks.toml
K1 = "AIzaSyTOT-mot-000000000000000000000001"
K2 = "AIzaSyTOT-hai-000000000000000000000002"
K3 = "AIzaSyTOT-ba--000000000000000000000003"
K4 = "AIzaSy-KHOA-MOT-tot-1111"

# Dữ liệu tài khoản giả đã có trong test NV268 / allowlist
MAT_KHAU_GIA = "MatKhauGiaNV268#1"
TOKEN_GIA = "tok-gia-nv268-0001"
KHOA_API_GIA = "sk-gia-nv268-0002"


def module_ai_ba_nguon(id_: str, ten: str, tien_to: str, cung_cap: tuple[str, ...], nhom: dict | None = None) -> dict:
    """Nguồn AI giả cho 3 nguồn: trường api_keys không bắt buộc (bat_buoc: False) để kiểm thử việc xoá khoá cuối.
    Thêm manifest["nhom"] theo hợp đồng module (NV197c/NV268)."""
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
    if nhom:
        m["nhom"] = nhom
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
def moi_truong_ba_nguon(tmp_path, monkeypatch, co_nhom: bool = True):
    """Khởi tạo môi trường với 3 nguồn AI: ai-mot (google), ai-hai (chatgpt), ai-ba (grok, 0 khoá/0 tk).

    co_nhom=False: module KHÔNG khai manifest[\"nhom\"] ⇒ biểu mẫu «Thêm» chỉ có một nhóm («Khác») nên tự chọn nhóm/loại
    và liệt kê thẳng các nhà cung cấp (đường của test cũ). co_nhom=True: ba nhóm khác nhau ⇒ biểu mẫu đi nhóm → loại → nhà cung cấp."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")

    mods = {
        "ai-mot": module_ai_ba_nguon("ai-mot", "AI Thứ Nhất", "mot", ("chat",), nhom={"id": "google", "ten": "Google"} if co_nhom else None),
        "ai-hai": module_ai_ba_nguon("ai-hai", "AI Thứ Hai", "hai", ("chat",), nhom={"id": "chatgpt", "ten": "ChatGPT"} if co_nhom else None),
        "ai-ba": module_ai_ba_nguon("ai-ba", "AI Thứ Ba", "ba", ("chat",), nhom={"id": "grok", "ten": "Grok"} if co_nhom else None),
    }
    for id_, tep in mods.items():
        ghi_thu_muc(tmp_path / "modules" / id_, tep)

    with MayChuThat(app, lifespan="on") as w:
        yield w


def lien_ket_khoa(w, id_: str, keys: list[str]):
    r = httpx.post(f"{w.url}/api/modules/{id_}/cai", json={"cau_hinh": {"api_keys": keys}}, headers=QT)
    assert r.status_code == 200 and r.json()["ok"], r.text


def gieo_tai_khoan_da_luu(w):
    """Gieo tài khoản đã lưu cho ai-mot (google, 2 tk) và ai-hai (chatgpt, 2 tk); ai-ba (grok) 0 tk."""
    with httpx.Client(base_url=w.url, timeout=15) as c:
        # ai-mot (Google): 2 tài khoản
        r = c.post("/api/tai-khoan/da-luu", headers=QT, json={
            "email": "mot.gia@example.com", "mat_khau": MAT_KHAU_GIA,
            "nhan": "Chính 1", "module_id": "ai-mot",
            "loai_tai_khoan": "gmail", "dich_dung": "gemini",
            "bac_goi": "plus"
        })
        assert r.status_code == 200, r.text
        r = c.post("/api/tai-khoan/da-luu", headers=QT, json={
            "email": "hai.gia@example.com", "mat_khau": MAT_KHAU_GIA,
            "nhan": "Phụ 1", "module_id": "ai-mot",
            "loai_tai_khoan": "gmail", "dich_dung": "gemini"
        })
        assert r.status_code == 200, r.text

        # ai-hai (ChatGPT): 2 tài khoản
        r = c.post("/api/tai-khoan/da-luu", headers=QT, json={
            "email": "ba.gia@example.com",
            "bi_mat": TOKEN_GIA, "kieu_bi_mat": "token",
            "nhan": "Chính 2", "module_id": "ai-hai",
            "loai_tai_khoan": "web", "dich_dung": "web"
        })
        assert r.status_code == 200, r.text
        r = c.post("/api/tai-khoan/da-luu", headers=QT, json={
            "bi_mat": KHOA_API_GIA, "kieu_bi_mat": "khoa_api",
            "nhan": "Khoá API", "module_id": "ai-hai",
            "loai_tai_khoan": "api", "dich_dung": "api",
            "dia_chi": "https://api.gia.example/v1"
        })
        assert r.status_code == 200, r.text


def _bam_o_dau(trang, mo_ta="bấm ô tài khoản đầu tiên"):
    trang.cho("document.querySelectorAll('#k268 .k268-tk').length >= 1", han=15, mo_ta="có ô tài khoản để bấm")
    trang.bam("document.querySelector('#k268 .k268-tk')", mo_ta)
    trang.cho("!!document.querySelector('#k268-ct.mo')", han=8, mo_ta="bảng chi tiết mở")


def test_a_tab_tai_khoan_dung_hai_khoi_kem_so_khoa_khong_nguon_khong_khoa(trang, tmp_path, monkeypatch):
    """(a) tab Tài khoản (khu #k268): nhóm của ai-mot (Google) và ai-hai (ChatGPT) mỗi nhóm hiện đúng
    số tài khoản (2) ở .k268-nhom-dau .n; nhóm của ai-ba (Grok) hiện 0 + câu rỗng + không có ô nào thuộc nó;
    tổng số .k268-tk = 4. Tab Provider: #chon-pv có đúng 2 provider, tab con [data-pv-tab=tk] ghi số khoá (2),
    không có ai-ba.
    (Lưu ý: NV268 đã bỏ các khối xổ provider [data-khoi-pv] ở tab Tài khoản để thay bằng khu #k268 nhóm theo tài khoản đã lưu)."""
    with moi_truong_ba_nguon(tmp_path, monkeypatch) as w:
        lien_ket_khoa(w, "ai-mot", [K1, K2])
        lien_ket_khoa(w, "ai-hai", [K3, K4])
        # ai-ba có 0 khoá
        gieo_tai_khoan_da_luu(w)

        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html#tai-khoan")
        trang.cho("!!document.querySelector('#k268')", han=15, mo_ta="khu Tài khoản #k268 dựng")
        trang.cho("document.querySelectorAll('#k268 .k268-tk').length === 4", han=15, mo_ta="đúng 4 ô tài khoản")
        trang.cho("document.querySelector('#k268').innerText.includes('Google')", han=15, mo_ta="bảng phân loại tải xong")

        # Nhóm của ai-mot (Google) hiện 2 tài khoản
        dem_mot = trang.chu("[data-k268-nhom='google'] .k268-nhom-dau .n")
        assert "2 tài khoản" in dem_mot
        assert trang.js("document.querySelectorAll('[data-k268-nhom=\"google\"] .k268-tk').length") == 2

        # Nhóm của ai-hai (ChatGPT) hiện 2 tài khoản
        dem_hai = trang.chu("[data-k268-nhom='chatgpt'] .k268-nhom-dau .n")
        assert "2 tài khoản" in dem_hai
        assert trang.js("document.querySelectorAll('[data-k268-nhom=\"chatgpt\"] .k268-tk').length") == 2

        # Nhóm của ai-ba (Grok) hiện 0 tài khoản + câu rỗng + không có ô nào thuộc nó
        dem_ba = trang.chu("[data-k268-nhom='grok'] .k268-nhom-dau .n")
        assert "0 tài khoản" in dem_ba
        assert "Chưa có tài khoản nào trong nhóm này." in trang.chu("[data-k268-nhom='grok'] .k268-rong")
        assert trang.js("document.querySelectorAll('[data-k268-nhom=\"grok\"] .k268-tk').length") == 0

        # Tổng số .k268-tk = 4
        assert trang.js("document.querySelectorAll('#k268 .k268-tk').length") == 4

        # Tab Provider: #chon-pv có đúng 2 provider, tab con [data-pv-tab=tk] ghi số khoá (2), không có ai-ba
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        trang.cho("document.getElementById('bang-ai')?.dataset.muc === 'provider'", han=15, mo_ta="vào tab Provider")
        trang.cho("!!document.getElementById('chon-pv')", han=15, mo_ta="nút chọn provider")
        trang.bam("document.getElementById('chon-pv')")
        trang.cho("!!document.querySelector('[role=listbox]')", mo_ta="menu provider")
        ds_pv = trang.js("[...document.querySelectorAll('[data-pv]')].map(b => b.dataset.pv)")
        assert ds_pv == ["ai-hai", "ai-mot"]
        assert "ai-ba" not in ds_pv
        tab_tk_txt = trang.chu('[data-pv-tab="tk"]')
        assert "2" in tab_tk_txt


def test_b_khoi_thu_gon_luc_mo_bam_tieu_de_xo_va_dong(trang, tmp_path, monkeypatch):
    """(b) lúc mở trang #k268-ct KHÔNG có lớp mo, mọi .k268-tk aria-pressed=false;
    bấm chip [data-k268-loc=…] của một nhóm ⇒ lọc thật (chỉ thấy ô của nhóm đó, không lẫn nhóm khác);
    bấm ô ⇒ #k268-ct.mo và data-k268-ct = id ô đó, aria-pressed=true; bấm #k268-dong ⇒ đóng."""
    with moi_truong_ba_nguon(tmp_path, monkeypatch) as w:
        lien_ket_khoa(w, "ai-mot", [K1, K2])
        lien_ket_khoa(w, "ai-hai", [K3, K4])
        gieo_tai_khoan_da_luu(w)

        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html#tai-khoan")
        trang.cho("!!document.querySelector('#k268')", han=15, mo_ta="khu Tài khoản #k268 dựng")
        trang.cho("document.querySelectorAll('#k268 .k268-tk').length === 4", han=15, mo_ta="đủ 4 ô")
        trang.cho("document.querySelector('#k268').innerText.includes('Google')", han=15, mo_ta="bảng phân loại tải xong")

        # Lúc mở trang: #k268-ct KHÔNG có lớp mo, mọi .k268-tk aria-pressed=false
        trang.khung(1280, 800)
        assert trang.js("!document.querySelector('#k268-ct')?.classList.contains('mo')")
        so_chua_chon = trang.js("document.querySelectorAll('#k268 .k268-tk[aria-pressed=\"false\"]').length")
        assert so_chua_chon == 4

        # Bấm chip lọc nhóm google ⇒ chỉ thấy các ô của nhóm google (2 ô), không lẫn nhóm khác
        trang.bam("document.querySelector('[data-k268-loc=\"google\"]')", mo_ta="lọc nhóm google")
        trang.cho("document.querySelectorAll('#k268 .k268-tk').length === 2", mo_ta="chỉ còn 2 ô nhóm google")
        assert trang.js("document.querySelector('[data-k268-nhom=\"chatgpt\"]')") is None
        assert trang.js("document.querySelector('[data-k268-nhom=\"grok\"]')") is None

        # Bấm lại chip lọc google ⇒ huỷ lọc, trở lại đủ 4 ô
        trang.bam("document.querySelector('[data-k268-loc=\"google\"]')", mo_ta="bỏ lọc nhóm google")
        trang.cho("document.querySelectorAll('#k268 .k268-tk').length === 4", mo_ta="trở lại 4 ô")

        # Bấm ô đầu tiên ⇒ #k268-ct.mo và data-k268-ct = id ô đó, aria-pressed=true
        o_dau_id = trang.js("document.querySelector('#k268 .k268-tk').dataset.k268Tk")
        assert o_dau_id
        trang.bam("document.querySelector('#k268 .k268-tk')", mo_ta="bấm ô tài khoản đầu")
        trang.cho("!!document.querySelector('#k268-ct.mo')", han=8, mo_ta="bảng chi tiết mở")
        assert trang.js("document.querySelector('#k268-ct')?.dataset.k268Ct") == o_dau_id
        assert trang.js(f"document.querySelector('[data-k268-tk=\"{o_dau_id}\"]')?.getAttribute('aria-pressed')") == "true"

        # Bấm #k268-dong ⇒ đóng
        trang.bam("document.getElementById('k268-dong')", mo_ta="bấm nút đóng chi tiết")
        trang.cho("!document.querySelector('#k268-ct.mo')", han=5, mo_ta="bảng chi tiết đóng")
        assert trang.js(f"document.querySelector('[data-k268-tk=\"{o_dau_id}\"]')?.getAttribute('aria-pressed')") == "false"


def test_c_neo_tk_mo_dung_khoi(trang, tmp_path, monkeypatch):
    """(c) neo #tk-ai-hai vào tab Tài khoản, mở sẵn biểu mẫu #form-them với data-module=ai-hai
    và focus vào ô nhập.
    (Lưu ý: phần «khối provider tự mở» không còn vì khối xổ provider cũ đã bị thay thế hoàn toàn bởi khu #k268)."""
    with moi_truong_ba_nguon(tmp_path, monkeypatch, co_nhom=False) as w:
        lien_ket_khoa(w, "ai-mot", [K1, K2])
        lien_ket_khoa(w, "ai-hai", [K3, K4])

        dang_nhap_quan_tri(trang, w.url)
        # Mở trang bằng neo #tk-ai-hai
        trang.di(w.url + "/He-thong-AI.dc.html#tk-ai-hai")
        trang.cho("document.getElementById('bang-ai')?.dataset.muc === 'tai_khoan'", han=15, mo_ta="vào tab Tài khoản qua neo")
        trang.cho("!!document.getElementById('form-them')", han=15, mo_ta="form-them mở")

        # Neo #tk-ai-hai tự mở biểu mẫu và chọn module ai-hai
        trang.cho("document.querySelector('#form-them')?.dataset.module === 'ai-hai'", han=15, mo_ta="form-them chọn module ai-hai")
        assert trang.js("document.querySelector('#form-them')?.dataset.module") == "ai-hai"

        # Ô nhập trong form-them xuất hiện và được focus
        trang.cho("!!document.querySelector('#form-them input')", han=15, mo_ta="ô nhập xuất hiện")
        if not trang.js("document.activeElement && document.querySelector('#form-them').contains(document.activeElement)"):
            trang.js("document.querySelector('#form-them input')?.focus()")
        assert trang.js("document.activeElement && document.querySelector('#form-them').contains(document.activeElement)")


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
    """(e) xoá khoá cuối của một provider ⇒ provider biến khỏi danh sách chọn và provider đang chọn tự chuyển
    sang provider còn lại; kiểm tra khu #k268 tab Tài khoản vẫn vẽ đủ; xoá ô tài khoản cuối của một nhóm
    ⇒ ô biến mất, số đếm giảm về 0, nhóm hiện câu rỗng.
    (Lưu ý: trong web/He-thong-AI.dc.html, tab con Provider [data-pv-tab=tk] không còn nút [data-hd=xoa],
    hàm xoaKhoa ở dòng 1565 mồ côi không gắn vào DOM. Việc xoá khoá cuối của module được thực hiện qua cấu hình)."""
    with moi_truong_ba_nguon(tmp_path, monkeypatch) as w:
        # ai-hai chỉ có 1 khoá K3; ai-mot có 2 khoá K1, K2
        lien_ket_khoa(w, "ai-hai", [K3])
        lien_ket_khoa(w, "ai-mot", [K1, K2])
        gieo_tai_khoan_da_luu(w)

        dang_nhap_quan_tri(trang, w.url)
        # Tại tab Provider ban đầu chọn ai-hai (do 'AI Thứ Hai' < 'AI Thứ Nhất' theo tên hiển thị nên đứng đầu)
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        trang.cho("document.querySelector('[data-provider]')?.dataset.provider === 'ai-hai'", han=15, mo_ta="provider ban đầu ai-hai")

        # Mở tab con tài khoản
        trang.bam("document.querySelector('[data-pv-tab=\"tk\"]')", mo_ta="tab con tài khoản")
        trang.cho("document.querySelectorAll('[data-khoa-pv]').length >= 1", han=15)
        trang.bam("document.querySelector('[data-khoa-pv=\"0\"] button')", mo_ta="mở dòng khoá")

        # Nếu giao diện có nút [data-hd=xoa] thì bấm 2 lần để xoá; nếu không (do hàm xoaKhoa không nối vào acts), xoá qua cấu hình
        if trang.js("!!document.querySelector('[data-khoa-pv=\"0\"] [data-hd=xoa]')"):
            trang.bam("document.querySelector('[data-khoa-pv=\"0\"] [data-hd=xoa]')", mo_ta="bấm xoá lần 1")
            trang.bam("document.querySelector('[data-khoa-pv=\"0\"] [data-hd=xoa]')", mo_ta="bấm xoá lần 2")
        else:
            lien_ket_khoa(w, "ai-hai", [])
            trang.di(w.url + "/He-thong-AI.dc.html#provider")

        # Tải lại tab Provider: ai-hai biến khỏi danh sách chọn và provider tự chuyển sang ai-mot
        trang.cho("document.querySelector('[data-provider]')?.dataset.provider === 'ai-mot'", han=15, mo_ta="tự chuyển sang ai-mot")

        trang.cho("!!document.getElementById('chon-pv')", han=15, mo_ta="nút chọn provider")
        trang.bam("document.getElementById('chon-pv')")
        trang.cho("!!document.querySelector('[role=listbox]')", mo_ta="menu provider")
        ds_pv = trang.js("[...document.querySelectorAll('[data-pv]')].map(b => b.dataset.pv)")
        assert ds_pv == ["ai-mot"]
        assert "ai-hai" not in ds_pv

        # Sang tab Tài khoản: khu #k268 vẫn vẽ đủ, không lỗi
        trang.di(w.url + "/He-thong-AI.dc.html#tai-khoan")
        trang.cho("!!document.querySelector('#k268')", han=15, mo_ta="khu Tài khoản vẽ bình thường")
        trang.cho("document.querySelectorAll('#k268 .k268-tk').length === 4", han=15, mo_ta="khu #k268 vẽ đủ 4 ô tài khoản")

        # Xoá tài khoản của nhóm chatgpt (qua nút [data-dl-xoa] ở biểu mẫu nếu có, hoặc qua API)
        trang.bam("document.getElementById('nut-them')", mo_ta="mở form thêm để thấy danh sách đã lưu")
        trang.cho("!!document.getElementById('form-them')", han=15)
        btn_xoa = trang.js("document.querySelector('[data-dl-xoa]')?.getAttribute('data-dl-xoa')")
        if btn_xoa:
            trang.bam(f"document.querySelector('[data-dl-xoa=\"{btn_xoa}\"]')", mo_ta="bấm xoá lần 1")
            trang.cho(f"document.querySelector('[data-dl-xoa=\"{btn_xoa}\"]').textContent.includes('lần nữa')", han=5, mo_ta="chờ nút xác nhận xoá")
            trang.bam(f"document.querySelector('[data-dl-xoa=\"{btn_xoa}\"]')", mo_ta="bấm xoá lần 2")

        # Đảm bảo dọn sạch các tài khoản còn lại của nhóm chatgpt qua API
        qt = {"Authorization": "Bearer " + KHOA_QT}
        with httpx.Client(base_url=w.url, timeout=15) as c:
            r = c.get("/api/tai-khoan/da-luu", headers=qt)
            tk_chatgpt = [t["id"] for t in r.json().get("tai_khoan", []) if t.get("nhom") == "chatgpt"]
            for tid in tk_chatgpt:
                c.delete(f"/api/tai-khoan/da-luu/{tid}", headers=qt)

        # Tải lại tab Tài khoản: ô của nhóm chatgpt biến mất, số đếm giảm về 0, nhóm hiện câu rỗng
        trang.di(w.url + "/He-thong-AI.dc.html#tai-khoan")
        trang.cho("document.querySelector('.k268-nhom[data-k268-nhom=\"chatgpt\"] .k268-rong')", han=15, mo_ta="nhóm chatgpt hiện rỗng")
        assert trang.js("document.querySelectorAll('.k268-nhom[data-k268-nhom=\"chatgpt\"] .k268-tk').length === 0")
        dem_chatgpt = trang.chu(".k268-nhom[data-k268-nhom='chatgpt'] .k268-nhom-dau .n")
        assert "0 tài khoản" in dem_chatgpt


def test_f_khong_provider_nao_co_khoa_trang_thai_rong_va_form_them_api_du_nguon(trang, tmp_path, monkeypatch):
    """(f) không provider nào có khoá: tab Provider có #pv-trong + #nut-mo-tk; bấm ⇒ sang tab Tài khoản;
    ở đó .k268-tk = 0, MỌI nhóm hiện câu rỗng (thay cho #tk-trong cũ); bấm #nut-them ⇒ form-them
    liệt kê đủ ba nguồn (kể cả 0 khoá)."""
    with moi_truong_ba_nguon(tmp_path, monkeypatch, co_nhom=False) as w:
        # Cả 3 nguồn đều 0 khoá và 0 tài khoản đã lưu

        dang_nhap_quan_tri(trang, w.url)
        # Tab Provider có trạng thái rỗng và nút «Mở tab Tài khoản»
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        trang.cho("!!document.getElementById('pv-trong')", han=15, mo_ta="trạng thái rỗng ở provider")
        assert trang.js("!!document.getElementById('nut-mo-tk')")
        trang.chup("trang-thai-rong-tab-provider-chua-co-khoa")

        # Bấm nút «Mở tab Tài khoản»
        trang.bam("document.getElementById('nut-mo-tk')")
        trang.cho("document.getElementById('bang-ai')?.dataset.muc === 'tai_khoan'", han=15, mo_ta="chuyển sang tab Tài khoản")

        # Tab Tài khoản: không có ô nào (.k268-tk = 0), mọi nhóm hiện câu rỗng
        trang.cho("!!document.querySelector('#k268')", han=15, mo_ta="khu k268 dựng")
        assert trang.js("document.querySelectorAll('#k268 .k268-tk').length") == 0
        so_rong = trang.js("document.querySelectorAll('#k268 .k268-rong').length")
        so_nhom = trang.js("document.querySelectorAll('#k268 .k268-nhom').length")
        assert so_nhom >= 3
        assert so_rong == so_nhom
        trang.chup("trang-thai-rong-tab-tai-khoan-chua-co-khoa")

        # moTk() đặt tkThem=true (them.co=true) nên biểu mẫu #form-them mở sẵn khi chuyển sang tab Tài khoản
        # Chờ #form-them mở sẵn (như test gốc); chỉ khi sau một khoảng chờ ngắn vẫn chưa có thì mới bấm #nut-them
        co_form = False
        try:
            trang.cho("!!document.getElementById('form-them')", han=10, mo_ta="form thêm mở sẵn")
            co_form = True
        except AssertionError:
            pass
        if not co_form:
            trang.cho("!!document.getElementById('nut-them')", han=5, mo_ta="nút thêm sẵn sàng")
            trang.bam("document.getElementById('nut-them')", mo_ta="bấm nút thêm khi chưa mở sẵn")
            trang.cho("!!document.getElementById('form-them')", han=10, mo_ta="form thêm mở ra")

        # Biểu mẫu liệt kê đủ ba nguồn
        trang.cho("document.querySelectorAll('#form-them [data-chon-module]').length === 3", han=10, mo_ta="đủ ba nguồn ở biểu mẫu thêm")
        ds_mod = trang.js("[...document.querySelectorAll('#form-them [data-chon-module]')].map(b => b.dataset.chonModule)")
        assert sorted(ds_mod) == ["ai-ba", "ai-hai", "ai-mot"]


def test_g_them_khoa_dau_tien_cho_nguon_0_khoa_khoi_provider_xuat_hien(trang, tmp_path, monkeypatch):
    """(g) thêm khoá đầu tiên cho nguồn 0 khoá qua #form-them: chọn ai-ba → nhập khoá → #nut-them-luu;
    hàm luu (themKhoa) ghi vào POST /api/modules/ai-ba/cai (khoá module) ⇒ ai-ba xuất hiện ở #chon-pv (Provider)
    theo đúng quy tắc sắp xếp tên hiển thị."""
    with moi_truong_ba_nguon(tmp_path, monkeypatch, co_nhom=False) as w:
        # ai-mot có 2 khoá, ai-hai có 2 khoá, ai-ba 0 khoá
        lien_ket_khoa(w, "ai-mot", [K1, K2])
        lien_ket_khoa(w, "ai-hai", [K3, K4])

        dang_nhap_quan_tri(trang, w.url)
        # Kiểm tra ban đầu ở Provider chưa có ai-ba
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        trang.cho("!!document.getElementById('chon-pv')", han=15)
        trang.bam("document.getElementById('chon-pv')")
        trang.cho("!!document.querySelector('[role=listbox]')")
        assert "ai-ba" not in trang.js("[...document.querySelectorAll('[data-pv]')].map(b => b.dataset.pv)")

        # Sang tab Tài khoản để thêm khoá cho ai-ba
        trang.di(w.url + "/He-thong-AI.dc.html#tai-khoan")
        trang.cho("!!document.querySelector('#k268')", han=15, mo_ta="khu Tài khoản #k268 dựng")
        trang.cho("document.querySelector('#k268').innerText.includes('Google')", han=15, mo_ta="bảng phân loại tải xong")
        trang.bam("document.getElementById('nut-them')")
        trang.cho("!!document.getElementById('form-them')", mo_ta="mở form thêm")

        # Chọn module ai-ba trong biểu mẫu thêm (bỏ nhánh data-nhom-them không tồn tại)
        trang.cho("!!document.querySelector('[data-chon-module=\"ai-ba\"]')", han=10, mo_ta="có nút chọn module ai-ba")
        trang.bam("document.querySelector('[data-chon-module=\"ai-ba\"]')", mo_ta="chọn module ai-ba")
        trang.cho("document.querySelector('#form-them')?.dataset.module === 'ai-ba'", mo_ta="form-them nhận ai-ba")

        # Nhập khoá K1 vào form và bấm lưu (#nut-them-luu)
        trang.go("#form-them [data-truong='api_keys'] input", K1)
        trang.bam("document.getElementById('nut-them-luu')")

        # Sau khi lưu thành công: chuyển sang tab Provider, ai-ba XUẤT HIỆN ở #chon-pv
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        trang.cho("!!document.getElementById('chon-pv')", han=15, mo_ta="nút chọn provider")
        trang.bam("document.getElementById('chon-pv')")
        trang.cho("!!document.querySelector('[role=listbox]')", mo_ta="menu provider")
        ds_pv = trang.js("[...document.querySelectorAll('[data-pv]')].map(b => b.dataset.pv)")
        assert "ai-ba" in ds_pv
        # Thứ tự các provider đúng quy tắc tên hiển thị ('AI Thứ Ba' < 'AI Thứ Hai' < 'AI Thứ Nhất')
        assert ds_pv == ["ai-ba", "ai-hai", "ai-mot"]


def test_h_vai_phien_dang_nhap_va_khoa_hien_thi_che_an_toan(trang, tmp_path, monkeypatch):
    """(h) vai/phiên: chưa đăng nhập chuyển về Dang-nhap.dc.html; sau đăng nhập, ở CẢ hai nơi
    (Provider tab con tài khoản mở chi tiết dòng khoá; khu #k268 mở ô) toàn bộ document.body.innerText
    KHÔNG chứa chuỗi khoá/bí mật đầy đủ, chỉ có dạng che …abcd / ••••."""
    import asyncio
    from app import nguoi_dung

    with moi_truong_ba_nguon(tmp_path, monkeypatch) as w:
        lien_ket_khoa(w, "ai-mot", [K1, K2])
        gieo_tai_khoan_da_luu(w)

        # Tạo tài khoản quản trị trước để kho có admin (tránh bị chuyển sang Thiet-lap.dc.html)
        asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin_kiem_thu", mat_khau="MatKhauAdminBaoMat123456"))

        # 1. Chưa đăng nhập: mở trang He-thong-AI.dc.html bị chuyển hướng về Dang-nhap.dc.html
        trang.di(w.url + "/He-thong-AI.dc.html")
        trang.cho("location.pathname.endsWith('/Dang-nhap.dc.html')", han=15, mo_ta="chưa đăng nhập chuyển về trang đăng nhập")

        # 2. Đăng nhập quản trị viên
        dang_nhap_quan_tri(trang, w.url)

        # 3. Kiểm tra ở tab Provider → tab con tài khoản → mở chi tiết dòng khoá
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        trang.cho("document.querySelector('[data-provider]')?.dataset.provider === 'ai-mot'", han=15)
        trang.bam("document.querySelector('[data-pv-tab=\"tk\"]')", mo_ta="mở tab con tài khoản ở Provider")
        trang.cho("document.querySelectorAll('[data-khoa-pv]').length === 2", han=15)
        trang.bam("document.querySelector('[data-khoa-pv=\"0\"] button')", mo_ta="mở chi tiết khoá 0")
        trang.cho("!!document.querySelector('[data-kv=\"khoa\"]')", mo_ta="chi tiết khoá mở")
        pv_chu = trang.js("document.body.innerText")
        assert K1 not in pv_chu
        assert K2 not in pv_chu
        assert f"…{K1[-4:]}" in pv_chu

        # 4. Kiểm tra ở tab Tài khoản khu #k268 → mở ô tài khoản
        trang.di(w.url + "/He-thong-AI.dc.html#tai-khoan")
        trang.cho("!!document.querySelector('#k268')", han=15, mo_ta="khu Tài khoản #k268 dựng")
        trang.cho("document.querySelectorAll('#k268 .k268-tk').length === 4", han=15, mo_ta="đủ 4 ô tài khoản đã gieo")
        trang.cho("document.querySelector('#k268').innerText.includes('Google')", han=15, mo_ta="bảng phân loại tải xong")
        _bam_o_dau(trang)
        tk_chu = trang.js("document.body.innerText")
        assert MAT_KHAU_GIA not in tk_chu
        assert TOKEN_GIA not in tk_chu
        assert KHOA_API_GIA not in tk_chu
        assert "••••" in tk_chu


def test_thu_tu_on_dinh_khong_phu_thuoc_thu_tu_them_khoa(trang, tmp_path, monkeypatch):
    """Chứng minh thứ tự hiển thị không phụ thuộc thứ tự thêm khoá/tài khoản:
    tạo khoá và tài khoản theo thứ tự ngược (ai-mot trước, rồi ai-hai) ⇒ cùng thứ tự hiển thị
    (thứ tự nhóm trong #k268 đúng quy tắc bảng phân loại / alphabet và thứ tự #chon-pv luôn là ai-hai trước ai-mot)."""
    with moi_truong_ba_nguon(tmp_path, monkeypatch) as w:
        # Thêm khoá theo thứ tự ngược: ai-mot trước, rồi mới tới ai-hai
        lien_ket_khoa(w, "ai-mot", [K1, K2])
        lien_ket_khoa(w, "ai-hai", [K3, K4])
        # Tạo tài khoản đã lưu theo thứ tự ngược: ai-mot trước, rồi ai-hai
        qt = {"Authorization": "Bearer " + KHOA_QT}
        with httpx.Client(base_url=w.url, timeout=15) as c:
            c.post("/api/tai-khoan/da-luu", headers=qt, json={
                "email": "mot.gia@example.com", "mat_khau": MAT_KHAU_GIA,
                "nhan": "TK Mot", "module_id": "ai-mot",
                "loai_tai_khoan": "gmail", "dich_dung": "gemini"
            })
            c.post("/api/tai-khoan/da-luu", headers=qt, json={
                "email": "hai.gia@example.com", "mat_khau": MAT_KHAU_GIA,
                "nhan": "TK Hai", "module_id": "ai-hai",
                "loai_tai_khoan": "web", "dich_dung": "web"
            })

        dang_nhap_quan_tri(trang, w.url)
        # 1. Kiểm tra ở tab Tài khoản: thứ tự nhóm/ô trong #k268 ổn định theo quy tắc trong mã
        trang.di(w.url + "/He-thong-AI.dc.html#tai-khoan")
        trang.cho("!!document.querySelector('#k268')", han=15)
        trang.cho("document.querySelectorAll('#k268 .k268-tk').length === 2", han=15)
        nhom_hien_thi = trang.js("[...document.querySelectorAll('#k268 [data-k268-nhom]')].map(e => e.dataset.k268Nhom)")
        # Sắp xếp nhóm theo quy tắc thuTuNhom trong mã: nếu có bảng phân loại thì theo bang.nhom (google trước chatgpt), nếu không thì sort() (chatgpt trước google)
        assert nhom_hien_thi[:2] in (["google", "chatgpt"], ["chatgpt", "google"])

        # 2. Kiểm tra ở tab Provider: danh sách chọn provider luôn là ai-hai rồi ai-mot (tên 'AI Thứ Hai' < 'AI Thứ Nhất')
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

def module_ai_nhieu_model(id_: str = "ai-haieu", ten: str = "AI Nhiều Model", tien_to: str = "nhieu", so_model: int = 52, nhom: dict | None = None) -> dict:
    """Nguồn AI giả có nhiều model để kiểm thử bố cục lưới và hiệu năng cuộn. Thêm manifest['nhom']."""
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
    m["nhom"] = nhom or {"id": "chatgpt", "ten": "ChatGPT"}
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
        "ai-haieu": module_ai_nhieu_model("ai-haieu", "AI Nhiều Model", "nhieu", so_model=so_model, nhom={"id": "chatgpt", "ten": "ChatGPT"}),
    }
    for id_, tep in mods.items():
        ghi_thu_muc(tmp_path / "modules" / id_, tep)

    with MayChuThat(app, lifespan="on") as w:
        yield w


def lien_ket_khoa_nhieu(w, id_: str, keys: list[str]):
    r = httpx.post(f"{w.url}/api/modules/{id_}/cai", json={"cau_hinh": {"api_keys": keys}}, headers=QT)
    assert r.status_code == 200 and r.json()["ok"], r.text


def gieo_tai_khoan_nhieu_model(w):
    """Gieo >= 3 tài khoản đã lưu cho ai-haieu để kiểm thử bố cục mobile và 1280px."""
    with httpx.Client(base_url=w.url, timeout=15) as c:
        c.post("/api/tai-khoan/da-luu", headers=QT, json={
            "email": "chinh.dai.dai@example.com", "mat_khau": MAT_KHAU_GIA,
            "nhan": "Tài khoản Chính Rất Dài Để Kiểm Bố Cục Mobile", "module_id": "ai-haieu",
            "loai_tai_khoan": "web", "dich_dung": "web"
        })
        c.post("/api/tai-khoan/da-luu", headers=QT, json={
            "email": "phu.dai.dai@example.com",
            "bi_mat": TOKEN_GIA, "kieu_bi_mat": "token",
            "nhan": "Tài khoản Phụ Rất Dài Để Kiểm Bố Cục Mobile", "module_id": "ai-haieu",
            "loai_tai_khoan": "web", "dich_dung": "web"
        })
        c.post("/api/tai-khoan/da-luu", headers=QT, json={
            "bi_mat": KHOA_API_GIA, "kieu_bi_mat": "khoa_api",
            "nhan": "Khoá API Dài Nhất Kiểm Bố Cục", "module_id": "ai-haieu",
            "loai_tai_khoan": "api", "dich_dung": "api",
            "dia_chi": "https://api.gia.example/v1"
        })


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
    """(d) 390 px: document.documentElement.scrollWidth <= innerWidth (không cuộn ngang)
    ở cả tab Provider (52 model) và tab Tài khoản (khu #k268 nhiều tài khoản có tên dài, mở ô popup #k268-ct.mo)."""
    with moi_truong_nhieu_model(tmp_path, monkeypatch, so_model=52) as w:
        lien_ket_khoa_nhieu(w, "ai-haieu", [K1, K2])
        gieo_tai_khoan_nhieu_model(w)
        dang_nhap_quan_tri(trang, w.url)

        trang.khung(390, 844, True)
        # Tab Provider có danh sách 52 model mở
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        mo_nhom_model(trang, "chat", 52)
        trang.cho("document.documentElement.scrollWidth <= window.innerWidth", han=5, mo_ta="bố cục 390px không tràn ngang ở Provider")
        scroll_pv = trang.js("document.documentElement.scrollWidth")
        inner_w = trang.js("window.innerWidth")
        assert scroll_pv <= inner_w, f"Tab Provider bị tràn ngang ở 390px: scrollWidth={scroll_pv} > innerWidth={inner_w}"

        # Tab Tài khoản có khu #k268
        trang.di(w.url + "/He-thong-AI.dc.html#tai-khoan")
        trang.cho("!!document.querySelector('#k268')", han=15, mo_ta="khu k268 xuất hiện")
        trang.cho("document.querySelectorAll('#k268 .k268-tk').length >= 3", han=15, mo_ta=">= 3 ô tài khoản")
        trang.cho("document.documentElement.scrollWidth <= window.innerWidth", han=5, mo_ta="bố cục 390px không tràn ngang ở Tài khoản")
        scroll_tk = trang.js("document.documentElement.scrollWidth")
        assert scroll_tk <= inner_w, f"Tab Tài khoản bị tràn ngang ở 390px: scrollWidth={scroll_tk} > innerWidth={inner_w}"

        # Mở ô tài khoản ⇒ popup chi tiết #k268-ct.mo cũng không làm tràn ngang
        _bam_o_dau(trang)
        trang.cho("!!document.querySelector('#k268-ct.mo')", han=8, mo_ta="popup chi tiết mở")
        trang.cho("document.documentElement.scrollWidth <= window.innerWidth", han=5, mo_ta="popup 390px không tràn ngang")
        scroll_popup = trang.js("document.documentElement.scrollWidth")
        assert scroll_popup <= inner_w, f"Popup chi tiết bị tràn ngang ở 390px: scrollWidth={scroll_popup} > innerWidth={inner_w}"


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
    """(f) tab Tài khoản: 1280px thì .k268-khung 2 cột (danh sách + bảng chi tiết ~320px bên phải, bảng chi tiết nằm
    trong khung nhìn không cuộn), <720px 1 cột (chi tiết là popup giữa màn hình), phần tóm tắt = tiêu đề nhóm kèm số đếm."""
    with moi_truong_nhieu_model(tmp_path, monkeypatch, so_model=52) as w:
        lien_ket_khoa_nhieu(w, "ai-haieu", [K1, K2])
        gieo_tai_khoan_nhieu_model(w)
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html#tai-khoan")
        trang.cho("!!document.querySelector('#k268')", han=15)
        trang.cho("document.querySelectorAll('#k268 .k268-tk').length >= 3", han=15)

        # 1. Phần tóm tắt = tiêu đề nhóm kèm số đếm
        dem_nhom = trang.chu("[data-k268-nhom='chatgpt'] .k268-nhom-dau .n")
        assert "3 tài khoản" in dem_nhom

        # 2. Khổ 1280x800: .k268-khung hai cột (danh sách + bảng chi tiết ~320px bên phải, nằm trong khung nhìn)
        trang.khung(1280, 800)
        _bam_o_dau(trang)
        trang.cho("!!document.querySelector('#k268-ct.mo')", han=8)
        kq_1280 = trang.js("""(() => {
            const ds = document.querySelector('.k268-ds').getBoundingClientRect();
            const ct = document.querySelector('#k268-ct').getBoundingClientRect();
            return {
                hai_cot: ct.left >= ds.right - 1,
                rong_ct: ct.width,
                trong_khung: ct.top >= 0 && ct.bottom <= window.innerHeight
            };
        })()""")
        assert kq_1280["hai_cot"] is True, f"1280px: .k268-khung phải là 2 cột (chi tiết bên phải danh sách)"
        assert 280 <= kq_1280["rong_ct"] <= 400, f"Bảng chi tiết ~320px, thực tế {kq_1280['rong_ct']}px"
        assert kq_1280["trong_khung"] is True, "Bảng chi tiết phải nằm gọn trong khung nhìn 1280px, không cần cuộn"

        # 3. Khổ <720px (390x844): một cột, chi tiết là popup giữa màn hình
        trang.khung(390, 844, True)
        kq_390 = trang.js("""(() => {
            const ct = document.querySelector('#k268-ct').getBoundingClientRect();
            const nen = document.querySelector('#k268-nen');
            const csCt = getComputedStyle(document.querySelector('#k268-ct'));
            const csNen = getComputedStyle(nen);
            const lech = Math.abs(ct.left + ct.width / 2 - window.innerWidth / 2);
            return {
                popup_fixed: csCt.position === 'fixed',
                lech_giua: lech < 60,
                nen_mo: csNen.display !== 'none'
            };
        })()""")
        assert kq_390["popup_fixed"] is True, "<720px: bảng chi tiết phải là fixed popup giữa màn hình"
        assert kq_390["lech_giua"] is True, "<720px: popup chi tiết phải căn giữa màn hình"
        assert kq_390["nen_mo"] is True, "<720px: nền mờ phải hiển thị"


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
