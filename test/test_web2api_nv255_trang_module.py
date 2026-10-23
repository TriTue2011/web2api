"""NV255 — trang Module kiểu làn (theo mẫu chủ máy đã duyệt).

Kiểm:
(a) backend: /api/modules/catalog trả nhom_con; 14 module đã chuyển loai sang ai;
    _chuan_hoa_nhom_con loại bỏ giá trị sai (không chặn nạp module).
(b) Chrome thật: số ô = số module từ API; số bước/chữ cái đúng và TỰ ĐỔI khi thêm
    module mới hoặc module cũ khai thêm can_module; khoá/mở khoá/chặn gỡ; dung lượng;
    lọc nhóm; hộp xác nhận cài vẫn chạy.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from app import kham_pha, so_module
from app.main import app
from conftest import cai_git_hai_luot
from gia import GitHubGia, MayChuThat, ghi_thu_muc, tep_kho
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture
from ui_mau import module_khong_khoa

can_chrome = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

KHOA_QT = "khoa-quan-tri-nv255-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
REPO = "TriTue2011/web2api"


def module_nen(id_="nen-gia", ten="Nền giả"):
    """Module nền AI đơn giản, không phụ thuộc gì."""
    tep = module_khong_khoa(id_, ten, "ai", "nengia", loai_ten="AI", loai_thu_tu=10)
    m = json.loads(tep["manifest.json"])
    m["nhom_con"] = {"id": "nen-tai-khoan", "ten": "Nền và tài khoản", "thu_tu": 1}
    tep["manifest.json"] = json.dumps(m, ensure_ascii=False).encode()
    return tep


def module_con(id_="con-gia", ten="Con giả", can=("nen-gia",)):
    """Module AI phụ thuộc module nền (can_module)."""
    tep = module_khong_khoa(id_, ten, "ai", "congia", loai_ten="AI", loai_thu_tu=10)
    m = json.loads(tep["manifest.json"])
    m["can_module"] = list(can)
    m["nhom_con"] = {"id": "cong-cu-ai", "ten": "Trợ lý và công cụ dùng AI", "thu_tu": 3}
    tep["manifest.json"] = json.dumps(m, ensure_ascii=False).encode()
    return tep


@contextmanager
def loi_that(tmp_path, monkeypatch, modules=None, git=None):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    for id_, tep in (modules or {}).items():
        ghi_thu_muc(tmp_path / "modules" / id_, tep)
    if git:
        gh = GitHubGia(REPO)
        gh.day(tep_kho(git), thong_diep="thêm module")
        with MayChuThat(gh.app) as s_gh:
            monkeypatch.setenv("WEB2API_GITHUB_API", s_gh.url)
            monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
            monkeypatch.setenv("WEB2API_GIT_REPO", REPO)
            with MayChuThat(app, lifespan="on") as w:
                yield w
    else:
        with MayChuThat(app, lifespan="on") as w:
            yield w


def o(id_):
    return f"document.querySelector('[data-o={json.dumps(id_)}]')"


def ds_module(w):
    r = httpx.get(f"{w.url}/api/modules", headers=QT)
    assert r.status_code == 200, r.text
    j = r.json()
    ds = j["modules"] if isinstance(j, dict) else j
    assert isinstance(ds, list), r.text
    return ds


def ds_catalog(w):
    r = httpx.get(f"{w.url}/api/modules/catalog", headers=QT)
    assert r.status_code == 200, r.text
    j = r.json()
    ds = j["modules"] if isinstance(j, dict) else j
    assert isinstance(ds, list), r.text
    return ds


# ───────────── backend: nhom_con ─────────────

def test_catalog_tra_nhom_con(tmp_path, monkeypatch):
    """GET /api/modules/catalog trả trường nhom_con cho mọi module (null khi không khai)."""
    with loi_that(tmp_path, monkeypatch, modules={"nen-gia": module_nen()}) as w:
        r = httpx.get(f"{w.url}/api/modules/catalog", headers=QT)
        assert r.status_code == 200, r.text
        j = r.json()
        ds = j["modules"] if isinstance(j, dict) else j
        assert isinstance(ds, list) and ds
        for m in ds:
            assert "nhom_con" in m, m.get("id")
        nen = next(m for m in ds if m["id"] == "nen-gia")
        assert nen["nhom_con"] == {"id": "nen-tai-khoan", "ten": "Nền và tài khoản", "thu_tu": 1}


def test_chuan_hoa_nhom_con_loai_bo_sai_khong_chan():
    """nhom_con sai định dạng thì warn + bỏ trường, không chặn nạp module (chuẩn hoá tại chỗ)."""
    def chuan(manifest):
        m = dict(manifest)
        kham_pha._chuan_hoa_nhom_con(m)
        return m.get("nhom_con")

    assert chuan({"id": "x", "nhom_con": {"id": "a", "ten": "A", "thu_tu": 2}}) == {"id": "a", "ten": "A", "thu_tu": 2}
    assert chuan({"id": "x", "nhom_con": {"id": "a", "ten": "A"}}) == {"id": "a", "ten": "A", "thu_tu": None}
    assert chuan({"id": "x", "nhom_con": "chuoi-sai"}) is None
    assert chuan({"id": "x", "nhom_con": {"ten": "thiếu id"}}) is None
    assert chuan({"id": "x", "nhom_con": {"id": "ID_HOA", "ten": "A"}}) is None
    assert chuan({"id": "x", "nhom_con": {"id": "a"}}) is None  # thiếu ten → bỏ
    assert chuan({"id": "x"}) is None


def test_14_module_ai_da_chuyen_loai():
    """14 module liên quan AI đã chuyển loai=tien_ich → ai trong manifest (không viết cứng id trong mã lõi)."""
    goc = Path(__file__).resolve().parent.parent / "modules"
    for mid in ("vnc-xa", "may-dang-nhap", "nhaptaikhoan", "register", "ho-so-google",
                "doc-ma-hop-thu", "thuthach", "tuvanhanh",
                "tro-ly", "tro-ly-giao-vien", "anh-ai", "dich", "chay-ma", "van-phong"):
        p = goc / mid / "manifest.json"
        assert p.exists(), f"thiếu manifest {mid}"
        m = json.loads(p.read_text(encoding="utf-8"))
        assert m["loai"] == "ai", f"{mid}: loai={m['loai']}"
        nc = m.get("nhom_con")
        assert isinstance(nc, dict) and nc.get("id") and nc.get("ten"), f"{mid} thiếu nhom_con hợp lệ"


# ───────────── Chrome: làn, số, chữ, khoá ─────────────

@can_chrome
def test_so_o_bang_so_module_va_lan_dung_nhom(trang, tmp_path, monkeypatch):
    """Ô (pill) hiện đủ module từ kho git; làn chia đúng nhóm từ manifest (không cứng id)."""
    git = {"nen-gia": module_nen(), "con-gia": module_con()}
    with loi_that(tmp_path, monkeypatch, git=git) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong.dc.html")
        trang.cho(o("nen-gia"), han=20, mo_ta="ô nen-gia")
        trang.cho(o("con-gia"), han=20, mo_ta="ô con-gia")
        # Cả hai module test đều ở làn AI, nhóm con đúng.
        assert trang.js(o("nen-gia") + ".closest('[data-lane]').dataset.lane") == "ai"
        assert trang.js(o("con-gia") + ".closest('[data-lane]').dataset.lane") == "ai"
        # Lọc nhóm: bấm tên làn AI thì chỉ còn làn AI.
        trang.bam("document.querySelector('[data-loc-lan=\"ai\"]')")
        trang.cho("document.querySelectorAll('[data-lane]').length === 1", mo_ta="chỉ làn AI")


@can_chrome
def test_so_buoc_va_chu_cai_dung(trang, tmp_path, monkeypatch):
    """Số bước cài + chữ cái nền đúng quy ước mẫu: nen-gia (nền) có chữ A, bước 1;
    con-gia (cần nen-gia) bước 2 và ghi «cần A»."""
    git = {"nen-gia": module_nen(), "con-gia": module_con()}
    with loi_that(tmp_path, monkeypatch, git=git) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong.dc.html")
        trang.cho(o("con-gia"), han=20, mo_ta="ô con-gia")
        # nen-gia là nền duy nhất → chữ A, bước 1 (buoc 0 + 1).
        assert trang.js(o("nen-gia") + ".querySelector(':scope > .chu').textContent") == "A"
        assert trang.js(o("nen-gia") + ".querySelector('.b').textContent") == "1"
        # con-gia cần nen-gia → bước 2, «cần A».
        assert trang.js(o("con-gia") + ".querySelector('.b').textContent") == "2"
        can = trang.js(o("con-gia") + ".querySelector('.can').textContent")
        assert "cần" in can and "A" in can, can
        # Chú thích dưới làn giải thích chữ A.
        chu_thich = trang.js("document.getElementById('chu-thich-dep').textContent")
        assert "A = Nền giả" in chu_thich, chu_thich


@can_chrome
def test_khoa_khi_thieu_nen_va_mo_khoa_sau_khi_cai_nen(trang, tmp_path, monkeypatch):
    """Ô chưa cài mà thiếu nền chưa cài thì bị khoá (disabled + ổ khoá + chú giải);
    cài nền xong thì tự mở khoá."""
    git = {"nen-gia": module_nen(), "con-gia": module_con()}
    with loi_that(tmp_path, monkeypatch, git=git) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong.dc.html")
        trang.cho(o("con-gia"), han=20, mo_ta="ô con-gia")
        # Khoá: disabled, có ổ khoá, chú giải nêu nền cần cài trước.
        assert trang.js(o("con-gia") + ".disabled") is True
        assert trang.js("!!" + o("con-gia") + ".querySelector('svg')") is True
        title = trang.js(o("con-gia") + ".title")
        assert "Cần cài trước" in title and "Nền giả" in title, title
        # Cài nền qua API → tải lại → ô con tự mở khoá.
        assert cai_git_hai_luot(httpx, "nen-gia", headers=QT,
                                duong_dan=f"{w.url}/api/nguon-git/cai/nen-gia").status_code == 200
        trang.di(w.url + "/He-thong.dc.html")
        trang.cho(o("con-gia") + " && !" + o("con-gia") + ".disabled", han=20, mo_ta="con-gia mở khoá")
        assert trang.js(o("con-gia") + ".disabled") is False


@can_chrome
def test_chan_go_khi_dang_duoc_can(trang, tmp_path, monkeypatch):
    """Không gỡ được module nền khi còn module ĐÃ CÀI đang cần nó: nút Gỡ disabled + dòng cảnh báo."""
    git = {"nen-gia": module_nen(), "con-gia": module_con()}
    with loi_that(tmp_path, monkeypatch, git=git) as w:
        for mid in ("nen-gia", "con-gia"):
            assert cai_git_hai_luot(httpx, mid, headers=QT,
                                    duong_dan=f"{w.url}/api/nguon-git/cai/{mid}").status_code == 200
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong.dc.html")
        trang.cho(o("nen-gia"), han=20, mo_ta="ô nen-gia")
        trang.bam(o("nen-gia"))
        trang.cho("document.getElementById('chan-go')", han=15, mo_ta="cảnh báo chặn gỡ")
        assert "Con giả" in trang.chu("#chan-go")
        assert trang.js("document.getElementById('nut-go').disabled") is True


@can_chrome
def test_dung_luong_trong_bang_chi_tiet(trang, tmp_path, monkeypatch):
    """Bảng chi tiết hiện «Dung lượng cài thêm» (chưa cài) / «Đang chiếm» (đã cài); thiếu số thì «chưa đo»."""
    git = {"nen-gia": module_nen()}
    with loi_that(tmp_path, monkeypatch, git=git) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong.dc.html")
        trang.cho(o("nen-gia"), han=20, mo_ta="ô nen-gia")
        trang.bam(o("nen-gia"))
        trang.cho("document.getElementById('dung-luong')", han=15, mo_ta="dòng dung lượng")
        assert "Dung lượng cài thêm" in trang.chu("#dung-luong")


@can_chrome
def test_so_chu_tu_doi_theo_du_lieu(trang, tmp_path, monkeypatch):
    """Số/chữ TỰ ĐỔI theo dữ liệu (không viết cứng): với 2 module (nen-gia ← con-gia) thì
    con-gia bước 2 chưa có chữ; với 3 module (thêm chau-gia ← con-gia) thì con-gia có thêm
    chữ B, chau-gia bước 3. Chứng minh phuThuoc() tính lại từ can_module mỗi lần vẽ."""
    # Bộ 2: nen-gia (nền), con-gia (cần nen-gia).
    git2 = {"nen-gia": module_nen(), "con-gia": module_con()}
    with loi_that(tmp_path, monkeypatch, git=git2) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong.dc.html")
        trang.cho(o("con-gia"), han=20, mo_ta="ô con-gia")
        assert trang.js(o("con-gia") + ".querySelector('.b').textContent") == "2"
        # NV255: chữ của CHÍNH ô (con trực tiếp) — chưa là nền → chưa có; chữ trong .can là của nền nen-gia.
        assert trang.js("!" + o("con-gia") + ".querySelector(':scope > .chu')") is True


@can_chrome
def test_so_chu_voi_ba_module(trang, tmp_path, monkeypatch):
    """Với 3 module (nen-gia ← con-gia ← chau-gia): nen-gia giữ chữ A, con-gia có thêm chữ B,
    chau-gia bước 3 «cần B» — số/chữ đổi theo dữ liệu, không cứng."""
    git3 = {"nen-gia": module_nen(), "con-gia": module_con(),
            "chau-gia": module_con("chau-gia", "Cháu giả", ("con-gia",))}
    with loi_that(tmp_path, monkeypatch, git=git3) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong.dc.html")
        trang.cho(o("chau-gia"), han=20, mo_ta="ô chau-gia")
        # nen-gia giữ chữ A (bước thấp hơn), con-gia có thêm chữ B (giờ là nền).
        assert trang.js(o("nen-gia") + ".querySelector(':scope > .chu').textContent") == "A"
        assert trang.js(o("con-gia") + ".querySelector(':scope > .chu').textContent") == "B"
        # chau-gia bước 3, cần B.
        assert trang.js(o("chau-gia") + ".querySelector('.b').textContent") == "3"
        can = trang.js(o("chau-gia") + ".querySelector('.can').textContent")
        assert "B" in can, can


@can_chrome
def test_hop_xac_nhan_cai_van_chay(trang, tmp_path, monkeypatch):
    """Hộp xác nhận cài (NV61) vẫn chạy từ nút Cài trong bảng chi tiết."""
    git = {"nen-gia": module_nen()}
    with loi_that(tmp_path, monkeypatch, git=git) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong.dc.html")
        trang.cho(o("nen-gia"), han=20, mo_ta="ô nen-gia")
        trang.bam(o("nen-gia"))
        trang.cho("document.getElementById('nut-cai')", mo_ta="nút Cài")
        trang.bam("document.getElementById('nut-cai')")
        trang.cho("!!document.getElementById('xac-nhan-cai')", han=15, mo_ta="hộp xác nhận cài")
        assert "Cài module" in trang.chu("#xac-nhan-cai")
