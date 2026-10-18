"""NV50 — TEST TRƯỚC cho cổng duyệt hạng B.

Bối cảnh: báo cáo NV48 §3.3 — duyệt hạng B cần XÁC NHẬN LẠI lần 2 (chống bấm nhầm),
quyền duyệt có KỲ HẠN (hết hạn → chờ duyệt lại), mỗi lần duyệt GHI NHẬT KÝ quản trị.

Trên main hiện tại: duyệt một lần là kích hoạt ngay (không có xác nhận lần 2, không
kỳ hạn, lần duyệt chỉ ghi log.warning của Python chứ không vào nhật ký) — nên các
test hành vi mới đều xfail(strict=True): đỏ bây giờ, xanh khi mã được viết.

Các test phòng thủ (đã đúng trên main, xanh):
- Cập nhật A→B (thêm tai_nguyen.mang_ra) → 409 chờ duyệt (cổng NV7 hiện có).
- Không khoá → 401; khoá khách / phiên user thường → 403 trên đường duyệt.
- Duyệt nhầm id khác → 409, không lộ/kích hoạt gì.
- Tài khoản mới tạo không tự có quyền cao.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import khoa_khach, nguoi_dung, nhat_ky, so_module
from app.main import app
from app.nguon_git import NguonGit
from app.so_module import LoiCanDuyetQuyen, SoModule
from conftest import cai_ng_hai_luot
from gia import GitHubGia, MayChuThat, tep_echo, tep_kho

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
REPO = "chu-may/c2a"
CAU_HINH = {"repo": REPO, "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 1}
MANG_RA_TEST = ["api.vi-du-test.example"]


def tep_echo_quyen(loi_chao="echo v1", **patch_manifest):
    tep = tep_echo(loi_chao)
    m = json.loads(tep["manifest.json"])
    m.update(patch_manifest)
    tep["manifest.json"] = json.dumps(m, ensure_ascii=False).encode()
    return tep


@pytest.fixture(scope="module")
def _gh():
    g = GitHubGia(REPO)
    with MayChuThat(g.app) as s:
        g.url = s.url
        yield g


@pytest.fixture
def gh(_gh, monkeypatch):
    for k in ("nhanh", "commits", "trees", "blobs", "blob_sai", "nhat_ky", "loi_commit"):
        getattr(_gh, k).clear()
    _gh.token = None
    _gh.han_muc = None
    _gh.cat_cay = False
    monkeypatch.setenv("WEB2API_GITHUB_API", _gh.url)
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    return _gh


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return tmp_path


def chay(ham):
    async def _():
        so = SoModule()
        await so.khoi_dong()
        ng = NguonGit(so)
        try:
            return await ham(so, ng)
        finally:
            await ng.dung()
            await so.dung_het()
    return asyncio.run(_())


async def cai_v1_hang_a(so, ng, gh):
    """Cài echo v1 hạng A (chỉ chat)."""
    gh.day(tep_kho({"echo": tep_echo_quyen("echo v1", cung_cap=["chat"])}))
    ng.luu_cau_hinh(CAU_HINH)
    await ng.mot_lan()
    kq = await cai_ng_hai_luot(ng, "echo")
    assert kq["ok"] is True


async def day_v2_hang_b(gh):
    """Đẩy bản v2 hạng B (thêm tai_nguyen.mang_ra) lên git giả + đồng bộ."""
    gh.day(tep_kho({"echo": tep_echo_quyen(
        "echo v2", cung_cap=["chat"], tai_nguyen={"mang_ra": MANG_RA_TEST})}))
    return gh


def chuan_bi_cho_duyet_hang_b(gh, du_lieu):
    """Cài v1 (A), đẩy v2 (B), đồng bộ, rồi tạo yêu cầu chờ duyệt qua HTTP (409).

    Trả về (sha_commit, diff_them) của yêu cầu chờ duyệt.
    """
    async def _():
        so = SoModule()
        await so.khoi_dong()
        ng = NguonGit(so)
        try:
            await cai_v1_hang_a(so, ng, gh)
            await day_v2_hang_b(gh)
            await ng.mot_lan()
        finally:
            await ng.dung()
            await so.dung_het()
    asyncio.run(_())
    with TestClient(app) as c:
        r = c.post("/api/nguon-git/cai/echo", headers=QT)
        assert r.status_code == 409, r.text
        than = r.json()
        assert than["ma"] == "can_duyet_quyen"
        assert {"loai": "mang_ra", "ten": MANG_RA_TEST[0]} in than["diff"]["them"]
        return than["sha_commit"], than["diff"]["them"]


# ── Phòng thủ: đã đúng trên main ──────────────────────────────────────────

def test_cap_nhat_a_len_b_409_cho_duyet(gh, du_lieu):
    """Cập nhật từ hạng A lên hạng B (thêm mang_ra) → 409 chờ duyệt (cổng NV7)."""
    async def ham(so, ng):
        await cai_v1_hang_a(so, ng, gh)
        await day_v2_hang_b(gh)
        await ng.mot_lan()
        with pytest.raises(LoiCanDuyetQuyen):
            await ng.cai("echo")
    chay(ham)


def test_duong_duyet_khong_khoa_401_khoa_khach_403(du_lieu):
    ban_ghi, khoa_tho = khoa_khach.tao_khoa(nhan="khách test nv50", pham_vi=["chat"])
    try:
        with TestClient(app) as c:
            r = c.post("/api/nguon-git/cai/echo")
            assert r.status_code == 401, r.text
            r = c.post("/api/nguon-git/cai/echo",
                       headers={"Authorization": f"Bearer {khoa_tho}"})
            assert r.status_code == 403, r.text
    finally:
        khoa_khach.xoa_khoa(ban_ghi["id"])


def test_duong_duyet_phien_user_thuong_403(du_lieu):
    """Tài khoản vai=user đăng nhập web rồi gọi đường duyệt → 403; vai vẫn là user."""
    async def tao():
        ban_ghi, ten, mat_khau = await nguoi_dung.tao_nguoi_dung(
            vai="user", ten_dang_nhap="user-nv50", mat_khau="MatKhau-Test-NV50-000")
        assert ban_ghi["vai"] == "user"  # tài khoản mới không tự có quyền cao
        return ten, mat_khau
    ten, mat_khau = asyncio.run(tao())
    with TestClient(app) as c:
        r = c.post("/api/dang-nhap", json={"ten_dang_nhap": ten, "mat_khau": mat_khau})
        assert r.status_code == 200, r.text
        r2 = c.post("/api/nguon-git/cai/echo")  # cookie phiên user tự đi theo
        assert r2.status_code == 403, r2.text


def test_duyet_nham_id_khac_409_khong_lo_du_lieu(gh, du_lieu):
    """Yêu cầu chờ duyệt của echo, nhưng duyệt qua id khác → 409, không kích hoạt gì."""
    sha_commit, them = chuan_bi_cho_duyet_hang_b(gh, du_lieu)
    with TestClient(app) as c:
        r = c.post("/api/nguon-git/cai/echo-khac", headers=QT,
                   json={"chap_nhan_quyen_them": them, "sha_commit": sha_commit})
        assert r.status_code == 409, r.text
        # Yêu cầu của echo vẫn còn nguyên, chưa bị ai duyệt hộ.
        r2 = c.post("/api/nguon-git/cai/echo", headers=QT)
        assert r2.status_code == 409
        assert r2.json()["ma"] == "can_duyet_quyen"


def test_duyet_hang_b_co_xac_nhan_lan_2_thi_kich_hoat(gh, du_lieu):
    """Đủ hai xác nhận → kích hoạt bản hạng B (trạng thái cuối phải đạt được)."""
    sha_commit, them = chuan_bi_cho_duyet_hang_b(gh, du_lieu)
    with TestClient(app) as c:
        r = c.post("/api/nguon-git/cai/echo", headers=QT,
                   json={"chap_nhan_quyen_them": them, "sha_commit": sha_commit,
                         "xac_nhan_lan_2": True})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True


# ── Hành vi mới: đỏ trên main ─────────────────────────────────────────────

def test_duyet_hang_b_thieu_xac_nhan_lan_2_409(gh, du_lieu):
    """Duyệt hạng B chỉ một lần (không có xac_nhan_lan_2) → 409, chưa kích hoạt."""
    sha_commit, them = chuan_bi_cho_duyet_hang_b(gh, du_lieu)
    with TestClient(app) as c:
        r = c.post("/api/nguon-git/cai/echo", headers=QT,
                   json={"chap_nhan_quyen_them": them, "sha_commit": sha_commit})
        assert r.status_code == 409, r.text
        # Chưa kích hoạt: yêu cầu chờ duyệt vẫn còn.
        r2 = c.post("/api/nguon-git/cai/echo", headers=QT)
        assert r2.status_code == 409
        assert r2.json()["ma"] == "can_duyet_quyen"


def test_duyet_hang_b_ghi_nhat_ky(gh, du_lieu):
    """Duyệt xong hạng B → có bản ghi mới ở kênh nhật ký quan_tri."""
    sha_commit, them = chuan_bi_cho_duyet_hang_b(gh, du_lieu)
    truoc = nhat_ky.doc(kenh="quan_tri", q="echo")
    with TestClient(app) as c:
        r = c.post("/api/nguon-git/cai/echo", headers=QT,
                   json={"chap_nhan_quyen_them": them, "sha_commit": sha_commit,
                         "xac_nhan_lan_2": True})
        assert r.status_code == 200, r.text
    sau = nhat_ky.doc(kenh="quan_tri", q="echo")
    assert len(sau) > len(truoc), "duyệt hạng B phải ghi nhật ký quản trị"


def test_quyen_duyet_hang_b_co_ky_han(gh, du_lieu):
    """Duyệt xong → quyền duyệt hạng B được cấp có kỳ hạn (het_han trong tương lai)."""
    sha_commit, them = chuan_bi_cho_duyet_hang_b(gh, du_lieu)
    with TestClient(app) as c:
        r = c.post("/api/nguon-git/cai/echo", headers=QT,
                   json={"chap_nhan_quyen_them": them, "sha_commit": sha_commit,
                         "xac_nhan_lan_2": True})
        assert r.status_code == 200, r.text
        than = r.json()
        grant = than.get("duyet_hang_b") or {}
        assert grant.get("het_han"), "quyền duyệt hạng B phải có kỳ hạn"
