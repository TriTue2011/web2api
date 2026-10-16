"""NV50 — TEST TRƯỚC cho phân hạng module (hạng A / hạng B) và lưu hạng vào catalog.

Bối cảnh: báo cáo NV48 §3.1 chốt hai hạng module — hạng A (an toàn mặc định) và hạng B
(nguy hiểm, cần cấp đích danh). Phân hạng theo CẤU TRÚC manifest (quyền khai báo),
KHÔNG liệt kê tên module.

Hàm `tinh_hang(manifest)` (thuần, đặt ở app/quyen_module.py theo NV48 mục 4) CHƯA có
trên main, catalog cũng chưa lưu hạng — nên mọi test hành vi mới trong tệp này đều
đánh dấu xfail(strict=True): đỏ bây giờ, xanh khi mã được viết.

Quy tắc suy hạng (NV48 §3.1):
- Hạng A khi TẤT CẢ đúng: `cung_cap` ⊆ {chat, anh, nhung, xep_hang, nghe, noi}
  (khả năng «chỉ nói chuyện qua giao diện lõi»); `tai_nguyen` không có `mang_ra`;
  không `webhook`; không `nhan_cookie`; không `yeu_cau_goi` ngoài allowlist của ảnh.
- Hạng B khi BẤT KỲ: `tai_nguyen.mang_ra` khác rỗng; khai `webhook`; khai
  `nhan_cookie`; `cung_cap` có khả năng tự chạy mã / SSH / trình duyệt / đọc tệp /
  điều khiển thiết bị; `yeu_cau_goi` có gói ngoài allowlist. `cung_cap` là trường mở
  nên hạng B suy theo nguyên tắc mặc định-đóng: khả năng không thuộc tập an toàn đã
  biết → B (không liệt kê tên khả năng nguy hiểm).
"""

from __future__ import annotations

import pytest

try:
    from app.quyen_module import tinh_hang
except ImportError:  # chưa có trên main → các test dưới đỏ đúng chỗ
    tinh_hang = None


def mf(**kw):
    """Manifest tối thiểu cho test phân hạng thuần."""
    m = {"id": "mod-test", "ten": "Mod test", "tien_to": "modtest", "phien_ban": "1.0.0"}
    m.update(kw)
    return m


def _hang(manifest):
    assert tinh_hang is not None, "NV50 PH1: chưa có hàm tinh_hang trên main"
    return tinh_hang(manifest)


XFAIL_PH1 = pytest.mark.xfail(strict=True, reason="NV50 PH1: tinh_hang chưa có trên main")


# ── Hạng A ────────────────────────────────────────────────────────────────

@XFAIL_PH1
def test_tinh_hang_chat_don_gian_la_a():
    assert _hang(mf(cung_cap=["chat"], truong_cau_hinh=[])) == "A"


@XFAIL_PH1
def test_tinh_hang_nhom_kha_nang_loi_la_a():
    assert _hang(mf(cung_cap=["chat", "anh", "nhung", "xep_hang", "nghe", "noi"])) == "A"


@XFAIL_PH1
def test_tinh_hang_manifest_rong_la_a():
    # Không khai quyền nào → không có gì nguy hiểm → hạng A (mặc định an toàn).
    assert _hang({}) == "A"


@XFAIL_PH1
def test_tinh_hang_none_la_a():
    assert _hang(None) == "A"


@XFAIL_PH1
def test_tinh_hang_mang_ra_rong_khong_doi_hang():
    m = mf(cung_cap=["chat"], tai_nguyen={"mang_ra": []})
    assert _hang(m) == "A"


@XFAIL_PH1
def test_tinh_hang_yeu_cau_goi_rong_la_a():
    assert _hang(mf(cung_cap=["chat"], yeu_cau_goi=[])) == "A"


# ── Hạng B ────────────────────────────────────────────────────────────────

@XFAIL_PH1
def test_tinh_hang_mang_ra_la_b():
    m = mf(cung_cap=["chat"], tai_nguyen={"mang_ra": ["api.vi-du-test.example"]})
    assert _hang(m) == "B"


@XFAIL_PH1
def test_tinh_hang_webhook_la_b():
    m = mf(cung_cap=["chat"], webhook={
        "bi_mat": "khoa_bi_mat", "tran_byte": 1024,
        "id_su_kien": "lay_id", "chu_ky": "header:X-Chu-Ky", "thuat_toan": "hmac-sha256"})
    assert _hang(m) == "B"


@XFAIL_PH1
def test_tinh_hang_nhan_cookie_la_b():
    m = mf(truong_cau_hinh=[{"khoa": "cookie_dn", "nhan": "Cookie đăng nhập",
                             "kieu": "danh_sach_bi_mat",
                             "nhan_cookie": {"mien": "vi-du-test.example"}}])
    assert _hang(m) == "B"


@XFAIL_PH1
def test_tinh_hang_yeu_cau_goi_la_la_b():
    # Gói rõ ràng không thuộc allowlist của ảnh → hạng B.
    m = mf(cung_cap=["chat"], yeu_cau_goi=["goi-la-nv50-khong-co-that-xyz"])
    assert _hang(m) == "B"


@XFAIL_PH1
def test_tinh_hang_trinh_duyet_la_b():
    # Tự động hoá trình duyệt (NV48 §3.1) → hạng B.
    assert _hang(mf(cung_cap=["trinh_duyet"])) == "B"


@XFAIL_PH1
def test_tinh_hang_thiet_bi_la_b():
    # Điều khiển thiết bị (NV48 §3.1) → hạng B.
    assert _hang(mf(cung_cap=["thiet_bi"])) == "B"


@XFAIL_PH1
def test_tinh_hang_kha_nang_la_mac_dinh_dong():
    # cung_cap là trường mở: khả năng chưa biết → B (không liệt kê tên nguy hiểm).
    assert _hang(mf(cung_cap=["kha_nang_moi_chua_biet_xyz"])) == "B"


# ── Theo cấu trúc, không theo tên ─────────────────────────────────────────

@XFAIL_PH1
def test_tinh_hang_khong_phu_thuoc_ten_module():
    # Tên «nguy hiểm» nhưng cấu trúc an toàn → A; tên «lành» nhưng cấu trúc B → B.
    assert _hang(mf(id="mod-nguy-hiem", tien_to="nguyhiem", cung_cap=["chat"])) == "A"
    assert _hang(mf(id="mod-lanh", tien_to="lanh",
                    tai_nguyen={"mang_ra": ["api.vi-du-test.example"]})) == "B"


# ── Lưu hạng vào catalog ──────────────────────────────────────────────────
# Dùng lại GitHub giả + module echo giả (không viết máy chủ mới).

import asyncio
import json

from app import so_module
from app.nguon_git import NguonGit
from app.so_module import SoModule
from gia import GitHubGia, MayChuThat, tep_echo, tep_kho

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
REPO = "chu-may/c2a"
CAU_HINH = {"repo": REPO, "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 1}


def tep_echo_hang(loi_chao="echo v1", **patch_manifest):
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


@pytest.mark.xfail(strict=True, reason="NV50 PH1: catalog chưa lưu hạng module")
def test_catalog_luu_hang_a_sau_khi_cai(gh, du_lieu):
    async def ham(so, ng):
        gh.day(tep_kho({"echo": tep_echo_hang("echo v1", cung_cap=["chat"])}))
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        await ng.cai("echo")
        assert getattr(so.catalog["echo"], "hang", None) == "A"
    chay(ham)


@pytest.mark.xfail(strict=True, reason="NV50 PH1: catalog chưa lưu hạng module")
def test_catalog_luu_hang_b_sau_khi_cai(gh, du_lieu):
    # Cài MỚI module hạng B không bị chặn (chỉ cập nhật A→B mới chờ duyệt) —
    # nhưng hạng phải được ghi vào catalog.
    async def ham(so, ng):
        gh.day(tep_kho({"echo": tep_echo_hang(
            "echo v1", cung_cap=["chat"],
            tai_nguyen={"mang_ra": ["api.vi-du-test.example"]})}))
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        await ng.cai("echo")
        assert getattr(so.catalog["echo"], "hang", None) == "B"
    chay(ham)
