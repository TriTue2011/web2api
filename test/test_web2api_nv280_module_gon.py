"""NV280: bảng chi tiết Hệ thống → Module gọn theo trang Tiện ích của demo.

Hai nhóm:
- đọc nguồn trang (không cần Chrome): không còn chuỗi «Sau khi cài sẽ tốn», nút «Thêm khoá», chữ «chờ khoá»;
- Chrome THẬT, lõi thật + GitHub giả: ba trạng thái (chưa cài / đã cài chạy được / đã cài cần khoá) — không «sau khi cài»,
  mô tả một câu ≤ 110 ký tự, «Đang chiếm» chỉ ở module đã cài.
NV296: chỗ nhập khoá/kết nối của module cần khoá là tab «Kết nối» NGAY TRONG bảng này (không còn liên kết sang Bảo mật &
người dùng); chi tiết tab ở test_web2api_nv296_tab_ket_noi.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from conftest import cai_git_hai_luot
from test_web2api_giao_dien import QT, loi_that, o
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture
from ui_mau import module_ai, module_khong_khoa

TRANG = Path(__file__).resolve().parents[1] / "web" / "He-thong.dc.html"
chrome = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

MO_TA_DAI = (
    "Module giả có một câu mở đầu cố tình viết dài thật dài để vượt quá một trăm mười ký tự hiển thị trong bảng chi tiết. "
    "Câu thứ hai nói thêm nhiều chuyện khác mà bảng gọn không được hiện ra mặc định."
)


def test_nguon_trang_khong_con_chu_cu():
    """Không cần Chrome: mã trang không còn tiêu đề «Sau khi cài sẽ tốn», nút «Thêm khoá» trong bảng, nhãn «chờ khoá»;
    NV296: khoá/kết nối nằm trong tab «Kết nối» của bảng, không liên kết sang Bảo mật."""
    ma = TRANG.read_text(encoding="utf-8")
    assert "Sau khi cài sẽ tốn" not in ma
    assert 'id="nut-them-khoa"' not in ma and 'id="lk-them-khoa"' not in ma
    assert "Đã cài — chờ khoá" not in ma
    assert 'id="tab-ket-noi"' in ma and 'id="lien-ket"' in ma
    assert 'id="lk-khoa-bao-mat"' not in ma and "He-thong-Bao-mat.dc.html#khoa-module-" not in ma


def mo_trang(trang, w, id_):
    dang_nhap_quan_tri(trang, w.url)
    trang.di(f"{w.url}/He-thong.dc.html")
    trang.cho(o(id_), han=20, mo_ta=f"ô {id_}")
    trang.bam(o(id_))
    trang.cho("document.getElementById('ct-trang-thai')", han=15, mo_ta="bảng chi tiết")


@chrome
def test_chua_cai_co_bang_cai_vao_se_ton_gon(trang, tmp_path, monkeypatch):
    ai = module_ai("ai-gia", "AI giả", "gia")
    m = json.loads(ai["manifest.json"])
    m["mo_ta"] = MO_TA_DAI
    ai["manifest.json"] = json.dumps(m, ensure_ascii=False).encode()
    with loi_that(tmp_path, monkeypatch, git={"ai-gia": ai}) as w:
        mo_trang(trang, w, "ai-gia")
        chu = trang.chu("#chi-tiet").lower()
        assert "sau khi cài" not in chu and "chưa đo" not in chu
        assert trang.chu("#ct-trang-thai") == "Chưa cài"
        assert trang.js("document.getElementById('dung-luong')") is None
        assert trang.chu("#h-can-gi").lower() == "cần có" and trang.chu("#ct-id") == "ai-gia"
        assert "Đĩa" in trang.chu("#can-gi") and "RAM" in trang.chu("#can-gi")
        assert "không cần module nào khác" in trang.chu("#phu-thuoc").lower()
        assert trang.js("document.getElementById('tab-ket-noi')") is None   # chưa cài: chưa nói chuyện khoá/kết nối
        # Mô tả: MỘT câu, ≤ 110 ký tự hiển thị (đo trên chữ đã vẽ); phần đầy đủ gập sau «Xem đầy đủ».
        mo_ta = trang.chu("#mo-ta")
        assert 0 < len(mo_ta) <= 110 and mo_ta.endswith("…") and "Câu thứ hai" not in mo_ta
        assert trang.js("document.getElementById('mo-ta-day').open") is False
        assert trang.chu("#mo-ta-day summary") == "Xem đầy đủ"


@chrome
def test_da_cai_chay_duoc_chi_mot_dong_dang_chiem(trang, tmp_path, monkeypatch):
    kk = module_khong_khoa("mang-gia", "Mạng giả", "mang", "mang", loai_ten="Mạng", loai_thu_tu=30, mo_ta="Một câu gọn.")
    with loi_that(tmp_path, monkeypatch, git={"mang-gia": kk}) as w:
        assert cai_git_hai_luot(httpx, "mang-gia", headers=QT, duong_dan=f"{w.url}/api/nguon-git/cai/mang-gia").status_code == 200
        mo_trang(trang, w, "mang-gia")
        trang.cho("document.getElementById('ct-trang-thai').textContent.trim() === 'Đang chạy'", han=15, mo_ta="Đang chạy ngay sau khi cài")
        assert "sau khi cài" not in trang.chu("#chi-tiet").lower()
        assert trang.js("document.getElementById('can-gi')") is None
        assert "đang chiếm trên đĩa" in trang.chu("#dung-luong")
        assert trang.chu("#h-can-gi").lower() == "đang dùng" and "chưa module nào cần module này" in trang.chu("#phu-thuoc").lower()
        assert trang.chu("#nut-do-lai-mod") == "Đo ngay" and "chưa đo" in trang.chu("#dang-dung").lower()
        trang.bam("document.getElementById('nut-do-lai-mod')")   # chỉ đo khi bấm (API đo thật của lõi), như «Đo lại» của artifact
        trang.cho("(document.getElementById('do-luc-mod')?.textContent || '').startsWith('Đo lúc')", han=20, mo_ta="Đo lúc …")
        assert trang.chu("#nut-do-lai-mod") == "Đo lại" and "bấm đo ngay" not in trang.chu("#dang-dung").lower()
        assert trang.js("document.getElementById('tab-ket-noi')") is None          # không cần khoá: không có tab Kết nối
        assert trang.js("document.getElementById('lien-ket')") is None             # đang chạy, không trường nào: không khối «Cấu hình»
        assert trang.chu("#mo-ta") == "Một câu gọn." and trang.js("document.getElementById('mo-ta-day')") is None
        assert trang.js("document.querySelectorAll('#chi-tiet input[type=password], #chi-tiet textarea').length") == 0


@chrome
def test_da_cai_can_khoa_mo_san_tab_ket_noi_trong_bang(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, git={"ai-gia": module_ai()}) as w:
        assert cai_git_hai_luot(httpx, "ai-gia", headers=QT, duong_dan=f"{w.url}/api/nguon-git/cai/ai-gia").status_code == 200
        mo_trang(trang, w, "ai-gia")
        assert trang.chu("#ct-trang-thai") == "Đã cài"
        assert trang.js(o("ai-gia") + ".dataset.tt") == "cho-khoa"
        assert "sau khi cài" not in trang.chu("#chi-tiet").lower()
        assert trang.js("document.getElementById('can-gi')") is None
        # Chờ khoá → tab «Kết nối» mở sẵn, ô dán khoá nằm NGAY trong bảng; không nút «Thêm khoá», không liên kết sang Bảo mật.
        trang.cho("document.querySelector('#chi-tiet #lien-ket textarea')", han=15, mo_ta="ô dán khoá trong tab Kết nối")
        assert trang.js("document.getElementById('tab-ket-noi').getAttribute('aria-selected')") == "true"
        assert trang.js("document.getElementById('nut-them-khoa')") is None
        assert trang.js("document.getElementById('lk-khoa-bao-mat')") is None
        assert len(trang.chu("#mo-ta")) <= 110
        # Không tràn ngang ở 390 px.
        trang.khung(390, 844, True)
        assert trang.js("document.documentElement.scrollWidth <= window.innerWidth + 1")
        trang.khung(1280, 800)
