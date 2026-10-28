"""Cập nhật module đã cài mà bản mới xin THÊM quyền: máy chủ trả 409 `can_duyet_quyen` kèm diff; giao diện phải hiện hộp duyệt
hai bước (xem quyền mới → xác nhận lần 2) rồi cập nhật. Trước 28/10/2026 giao diện chỉ hiện «Máy chủ trả HTTP 409» và không có
đường duyệt nào (chủ máy gặp ở ChatGPT Web). Cần Chrome thật; không có thì bỏ qua."""

from __future__ import annotations

import json

import pytest

from gia import GitHubGia, MayChuThat, tep_kho
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixtures
from ui_mau import module_ai
from app import so_module
from app.main import app

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

KHOA_QT = "khoa-quan-tri-duyet-quyen-123456"
REPO = "TriTue2011/web2api"
ID = "mod-duyet-quyen"


def _ban(phien_ban: str, mang_ra: list[str]) -> dict:
    mau = module_ai(ID, ten="Module duyệt quyền")
    mf = json.loads(mau["manifest.json"])
    mf["phien_ban"] = phien_ban
    mf["tai_nguyen"]["mang_ra"] = mang_ra
    return {**mau, "manifest.json": json.dumps(mf, ensure_ascii=False).encode()}


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    gh = GitHubGia(REPO)
    gh.day(tep_kho({ID: _ban("1.0.0", ["gia.example"])}), thong_diep="bản 1")
    with MayChuThat(gh.app) as s_gh:
        monkeypatch.setenv("WEB2API_GITHUB_API", s_gh.url)
        monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
        # Nguồn git quét mỗi 1 giây (mặc định 300 giây) để «có bản mới» hiện kịp trong test
        (tmp_path / "nguon_git.json").write_text(json.dumps({"repo": REPO, "chu_ky_giay": 1}), encoding="utf-8")
        with MayChuThat(app, lifespan="on") as w:
            w.gh = gh
            yield w


def _cai_ban_dau(trang, url):
    """Cài bản 1 qua giao diện (đường thật: Cài → Đồng ý cài), rồi chờ module có mặt trong /api/modules."""
    trang.di(f"{url}/He-thong.dc.html")
    trang.cho(f"document.querySelector('[data-o=\"{ID}\"]')", mo_ta="ô module chưa cài xuất hiện")
    trang.js(f"document.querySelector('[data-o=\"{ID}\"]').click()")
    trang.cho("document.getElementById('nut-cai')", mo_ta="nút Cài hiện")
    trang.js("document.getElementById('nut-cai').click()")
    trang.cho("document.getElementById('xac-nhan-cai') !== null", mo_ta="hộp xác nhận cài mới")
    trang.js("document.getElementById('nut-dong-y-cai').click()")
    trang.cho("fetch('/api/modules').then(r => r.json()).then(d => (d.modules || []).some(m => m.id === '%s'))" % ID,
              han=20, mo_ta="module đã cài")


def _cho_ban_moi(trang):
    trang.cho("fetch('/api/modules/thong-bao').then(r => r.json()).then(d => JSON.stringify(d).includes('co_ban_moi'))",
              han=25, mo_ta="máy chủ thấy bản mới trên git")


def test_cap_nhat_xin_them_quyen_hien_hop_duyet_hai_buoc_roi_cap_nhat(moi_truong, trang):
    w = moi_truong
    dang_nhap_quan_tri(trang, w.url)
    _cai_ban_dau(trang, w.url)
    w.gh.day(tep_kho({ID: _ban("1.0.1", ["gia.example", "moi.example"])}), thong_diep="bản 2 xin thêm quyền mạng")
    _cho_ban_moi(trang)
    trang.di(f"{w.url}/He-thong.dc.html")
    trang.cho(f"document.querySelector('[data-o=\"{ID}\"]')", mo_ta="ô module xuất hiện")
    trang.js(f"document.querySelector('[data-o=\"{ID}\"]').click()")
    trang.cho("document.getElementById('nut-cai')", mo_ta="nút Cập nhật hiện")
    trang.js("document.getElementById('nut-cai').click()")

    # Hộp duyệt hiện ra (không phải dòng «Máy chủ trả HTTP 409») và liệt kê quyền MỚI
    trang.cho("document.getElementById('xac-nhan-cai') !== null", mo_ta="hộp duyệt quyền hiện ra")
    hop = trang.js("document.getElementById('xac-nhan-cai').innerText")
    assert "Duyệt quyền mới" in hop and "moi.example" in hop and "HTTP 409" not in hop, hop
    assert trang.js("document.getElementById('nut-dong-y-cai').innerText").startswith("Đã xem")

    # Bước 1 chỉ chuyển sang bước 2, CHƯA cập nhật gì
    trang.js("document.getElementById('nut-dong-y-cai').click()")
    trang.cho("document.getElementById('nut-dong-y-cai').innerText.startsWith('Xác nhận lần 2')", mo_ta="sang bước 2")
    assert trang.js("fetch('/api/modules').then(r => r.json()).then(d => (d.modules || []).find(m => m.id === '%s')?.phien_ban)" % ID) == "1.0.0"

    # Bước 2 mới cập nhật
    trang.js("document.getElementById('nut-dong-y-cai').click()")
    trang.cho("fetch('/api/modules').then(r => r.json()).then(d => (d.modules || []).find(m => m.id === '%s')?.phien_ban === '1.0.1')" % ID,
              han=20, mo_ta="module lên bản 1.0.1 sau khi duyệt")
    # Cập nhật xong vẫn ở trang Module (không bị đẩy sang trang chính của module như lúc cài mới) và có dòng báo «Đã cập nhật»
    assert trang.js("location.pathname").endswith("/He-thong.dc.html")
    trang.cho("(document.getElementById('cai-tin')?.innerText || '').startsWith('Đã cập nhật')", mo_ta="dòng báo đã cập nhật")


def test_huy_o_hop_duyet_khong_cap_nhat(moi_truong, trang):
    w = moi_truong
    dang_nhap_quan_tri(trang, w.url)
    _cai_ban_dau(trang, w.url)
    w.gh.day(tep_kho({ID: _ban("1.0.1", ["gia.example", "moi.example"])}), thong_diep="bản 2")
    _cho_ban_moi(trang)
    trang.di(f"{w.url}/He-thong.dc.html")
    trang.cho(f"document.querySelector('[data-o=\"{ID}\"]')", mo_ta="ô module xuất hiện")
    trang.js(f"document.querySelector('[data-o=\"{ID}\"]').click()")
    trang.cho("document.getElementById('nut-cai')", mo_ta="nút Cập nhật hiện")
    trang.js("document.getElementById('nut-cai').click()")
    trang.cho("document.getElementById('xac-nhan-cai') !== null", mo_ta="hộp duyệt quyền hiện ra")
    trang.js("document.getElementById('nut-huy-cai').click()")
    trang.cho("document.getElementById('xac-nhan-cai') === null", mo_ta="hộp đóng")
    assert trang.js("fetch('/api/modules').then(r => r.json()).then(d => (d.modules || []).find(m => m.id === '%s')?.phien_ban)" % ID) == "1.0.0"
