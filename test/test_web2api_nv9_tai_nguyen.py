"""NV9 — tinh chỉnh duyệt quyền NV7 cho `tai_nguyen`: bớt báo động giả, giữ mặc định đóng.

Ba loại giá trị của `tai_nguyen` (ngoài `mang_ra` vốn đã là quyền từng mục):
- (1) ghi chú cho người đọc (`ghi_chu`): không phải quyền, không so sánh;
- (2) số tài nguyên không âm (RAM/đĩa…): chỉ TĂNG so với bản cũ mới là quyền thêm;
- (3) mọi khoá khác chưa biết: vẫn là quyền (mặc định đóng), đổi giá trị cũng tính.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app import kho, nguon_git, so_module
from app.nguon_git import NguonGit
from app.quyen_module import bam_diff, cac_muc_chuan, diff_quyen, tap_quyen
from app.so_module import LoiCanDuyetQuyen, SoModule
from gia import GitHubGia, MayChuThat, tep_echo, tep_kho

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
REPO = "chu-may/c2a"
CAU_HINH = {"repo": REPO, "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 1}


def _mf(tai_nguyen, **them):
    m = {"id": "echo", "tai_nguyen": tai_nguyen, "truong_cau_hinh": []}
    m.update(them)
    return m


def tep_echo_tn(loi_chao="echo v1", tai_nguyen=None, **patch):
    tep = tep_echo(loi_chao)
    m = json.loads(tep["manifest.json"])
    if tai_nguyen is not None:
        m["tai_nguyen"] = tai_nguyen
    m.update(patch)
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
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")  # NV16: NguonGit ghim IP, máy chủ giả ở loopback
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


TN_GOC = {"mang_ra": [], "dia_mb": 100, "ram_them_mb": 200, "ghi_chu": "ghi chú v1"}


# ── Đơn vị: diff_quyen ───────────────────────────────────────────────────────

def test_doi_chu_ghi_chu_khong_can_duyet():
    cu = _mf(dict(TN_GOC))
    moi = _mf({**TN_GOC, "ghi_chu": "đã sửa toàn bộ chữ mô tả"})
    assert diff_quyen(cu, moi) == {"them": [], "bot": []}


def test_giam_so_tai_nguyen_khong_can_duyet():
    cu = _mf(dict(TN_GOC))
    moi = _mf({**TN_GOC, "dia_mb": 50, "ram_them_mb": 100})
    assert diff_quyen(cu, moi) == {"them": [], "bot": []}


def test_giu_nguyen_so_khong_can_duyet():
    assert diff_quyen(_mf(dict(TN_GOC)), _mf(dict(TN_GOC))) == {"them": [], "bot": []}


def test_tang_so_tai_nguyen_can_duyet_va_diff_neu_muc_tang():
    cu = _mf(dict(TN_GOC))
    moi = _mf({**TN_GOC, "ram_them_mb": 300})
    d = diff_quyen(cu, moi)
    assert d["them"] == [{"loai": "tai_nguyen", "ten": "ram_them_mb",
                          "tang_tu": 200, "tang_len": 300}]
    assert d["bot"] == []


def test_doi_ghi_chu_va_tang_so_409_chi_muc_tang():
    cu = _mf(dict(TN_GOC))
    moi = _mf({**TN_GOC, "ghi_chu": "chữ khác hẳn", "dia_mb": 150})
    d = diff_quyen(cu, moi)
    assert len(d["them"]) == 1
    assert d["them"][0]["ten"] == "dia_mb"
    assert d["them"][0]["tang_tu"] == 100 and d["them"][0]["tang_len"] == 150
    assert all(m["ten"] != "ghi_chu" for m in d["them"])


def test_khoa_la_moi_trong_tai_nguyen_409():
    cu = _mf(dict(TN_GOC))
    moi = _mf({**TN_GOC, "quyen_bi_mat": "x"})
    d = diff_quyen(cu, moi)
    assert len(d["them"]) == 1 and d["them"][0] == {
        "loai": "tai_nguyen", "ten": "quyen_bi_mat",
        "bam_gia_tri": d["them"][0]["bam_gia_tri"]}


def test_kieu_bat_thuong_la_loai_3_dong_khi_loi():
    for gia_tri in ["100", -5, True, [1], {"a": 1}, 10 ** 15]:
        cu = _mf({"mang_ra": []})
        moi = _mf({"mang_ra": [], "dia_mb": gia_tri})
        d = diff_quyen(cu, moi)  # không ném
        assert len(d["them"]) == 1 and d["them"][0]["loai"] == "tai_nguyen"
        assert "tang_tu" not in d["them"][0]  # không phải loại (2)


def test_cai_moi_neu_so_tai_nguyen_xin():
    d = diff_quyen(None, _mf(dict(TN_GOC)))
    muc = {m["ten"]: m for m in d["them"]}
    assert muc["dia_mb"] == {"loai": "tai_nguyen", "ten": "dia_mb",
                             "tang_tu": 0, "tang_len": 100}
    assert muc["ram_them_mb"]["tang_len"] == 200
    assert "ghi_chu" not in muc  # ghi chú không nêu


def test_null_sang_so_la_tang_tu_0():
    cu = _mf({"mang_ra": [], "dia_mb": None})
    moi = _mf({"mang_ra": [], "dia_mb": 100})
    d = diff_quyen(cu, moi)
    assert d["them"] == [{"loai": "tai_nguyen", "ten": "dia_mb",
                          "tang_tu": 0, "tang_len": 100}]


def test_bam_diff_on_dinh_voi_muc_tang():
    d1 = diff_quyen(_mf(dict(TN_GOC)), _mf({**TN_GOC, "dia_mb": 150}))
    d2 = diff_quyen(_mf(dict(TN_GOC)), _mf({**TN_GOC, "dia_mb": 150}))
    assert bam_diff(d1) == bam_diff(d2)
    # thứ tự mục trong diff ổn định (sắp xếp xác định)
    assert d1["them"] == sorted(d1["them"],
                               key=lambda m: json.dumps(m, sort_keys=True, ensure_ascii=False))


def test_cac_muc_chuan_giu_tang_tu_tang_len():
    muc = [{"loai": "tai_nguyen", "ten": "dia_mb", "tang_tu": 100, "tang_len": 150}]
    assert cac_muc_chuan(muc) == muc
    # mục tăng thiếu trường → không khớp
    assert cac_muc_chuan([{"loai": "tai_nguyen", "ten": "dia_mb"}]) != muc


# ── Giữ nguyên các trường hợp nhạy cảm của NV7 ───────────────────────────────

def test_nv7_cac_quyen_that_van_409():
    assert diff_quyen({"truong_cau_hinh": []}, {"truong_cau_hinh": [
        {"khoa": "c", "nhan_cookie": {"mien": "vi-du.example"}}]})["them"]
    assert diff_quyen({"cung_cap": []}, {"cung_cap": ["anh"]})["them"]
    assert diff_quyen({"yeu_cau_goi": []}, {"yeu_cau_goi": ["goi_la"]})["them"]
    assert diff_quyen({"tai_nguyen": {"mang_ra": []}},
                      {"tai_nguyen": {"mang_ra": ["x.example"]}})["them"]
    assert diff_quyen({"id": "x"}, {"id": "x", "quyen_la": 1})["them"]
    assert diff_quyen({"tai_nguyen": {"mang_ra": []}},
                      {"tai_nguyen": {"mang_ra": [], "khoa_bi_mat": 1}})["them"]
    assert diff_quyen({"truong_cau_hinh": [{"khoa": "a"}]},
                      {"truong_cau_hinh": [{"khoa": "a", "khoa_la": 1}]})["them"]


# ── Tích hợp: cổng cài ───────────────────────────────────────────────────────

async def cai_v1(so, ng, gh, tai_nguyen=None):
    gh.day(tep_kho({"echo": tep_echo_tn("echo v1", tai_nguyen)}))
    ng.luu_cau_hinh(CAU_HINH)
    await ng.mot_lan()
    await ng.cai("echo")


def test_tich_hop_giam_tai_nguyen_qua_tu_do(gh, du_lieu):
    async def ham(so, ng):
        await cai_v1(so, ng, gh, dict(TN_GOC))
        gh.day(tep_kho({"echo": tep_echo_tn("echo v2", {**TN_GOC, "dia_mb": 50,
                                                         "ghi_chu": "đổi chữ" })}))
        await ng.mot_lan()
        kq = await ng.cai("echo")  # giảm số + đổi ghi chú: không cần duyệt
        assert kq["ok"] is True
    chay(ham)


def test_tich_hop_tang_tai_nguyen_409_va_duyet(gh, du_lieu):
    async def ham(so, ng):
        await cai_v1(so, ng, gh, dict(TN_GOC))
        gh.day(tep_kho({"echo": tep_echo_tn("echo v2", {**TN_GOC, "dia_mb": 150})}))
        await ng.mot_lan()
        with pytest.raises(LoiCanDuyetQuyen) as ei:
            await ng.cai("echo")
        diff, sha = ei.value.diff, ei.value.sha_commit
        assert diff["them"] == [{"loai": "tai_nguyen", "ten": "dia_mb",
                                 "tang_tu": 100, "tang_len": 150}]
        # duyệt đúng danh sách mới → áp dụng
        kq = await ng.cai("echo", chap_nhan_quyen_them=diff["them"], sha_commit=sha,
                          xac_nhan_lan_2=True)  # NV50: duyệt hạng B cần xác nhận lần 2
        assert kq["ok"] is True
        assert kho.doc_cho_duyet_quyen("echo") is None
    chay(ham)


def test_tich_hop_duyet_sai_danh_sach_cu_409(gh, du_lieu):
    async def ham(so, ng):
        await cai_v1(so, ng, gh, dict(TN_GOC))
        gh.day(tep_kho({"echo": tep_echo_tn("echo v2", {**TN_GOC, "dia_mb": 150})}))
        await ng.mot_lan()
        with pytest.raises(LoiCanDuyetQuyen) as ei:
            await ng.cai("echo")
        diff, sha = ei.value.diff, ei.value.sha_commit
        # duyệt bằng danh sách kiểu cũ (bam_gia_tri thay vì tang_tu/tang_len) → 409
        from app.so_module import LoiSo
        with pytest.raises(LoiSo):
            await ng.cai("echo", chap_nhan_quyen_them=[
                {"loai": "tai_nguyen", "ten": "dia_mb", "bam_gia_tri": "00" * 32}],
                sha_commit=sha, xac_nhan_lan_2=True)  # NV50: vẫn 409 vì danh sách sai
        assert kho.doc_cho_duyet_quyen("echo") is not None  # yêu cầu còn đó
    chay(ham)
