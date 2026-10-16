"""NV49 R-1 — cập nhật MÃ của module đã cài, giữ nguyên quyền, tự cài lặng.

Cổng duyệt trong `SoModule.cai_thu_muc` chỉ chặn khi `diff["them"]` (quyền xin thêm) khác rỗng;
bản mới đổi mã mà không đổi quyền đi thẳng tới `os.rename` + nạp lại. Khi `tu_dong` bật và
`yeu_cau_chu_ky` tắt (mặc định), `NguonGit._tu_dong` gọi `_cai` cho trạng thái `co_ban_moi`
nên mã mới chạy ngay, không ai duyệt. Fixture bám test_web2api_nguon_git.py (GitHub giả cục bộ).
"""

import asyncio
import json

import pytest

from app import so_module
from app.nguon_git import NguonGit
from app.so_module import SoModule
from gia import GitHubGia, MayChuThat, tep_echo, tep_kho

REPO = "chu-may/c2a"
CAU_HINH = {"repo": REPO, "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 1}


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
    monkeypatch.setenv("WEB2API_ADMIN_KEY", "khoa-quan-tri-test-0123456789abcdef")
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


async def tra_loi(so, id_="echo"):
    ma, _, than = await so.dang_chay[id_].chat({"model": "echo/x", "messages": [{"role": "user", "content": "a"}]}, False)
    assert ma == 200
    return json.loads(than)["choices"][0]["message"]["content"]


def test_cap_nhat_ma_giu_nguyen_quyen_khong_duoc_tu_cai(gh, du_lieu):
    """Module đã cài; bản mới ĐỔI MÃ, quyền y hệt; tu_dong bật, yeu_cau_chu_ky tắt, commit không ký
    → mã mới KHÔNG được chạy khi chưa ai duyệt."""
    async def ham(so, ng):
        cfg = ng.luu_cau_hinh({**CAU_HINH, "tu_dong": True})["cau_hinh"]
        assert cfg["tu_dong"] is True and cfg["yeu_cau_chu_ky"] is False

        gh.day(tep_kho({"echo": tep_echo("echo v1")}), xac_minh=False)
        await ng.mot_lan()
        await ng._tu_dong()  # cài mới do tu_dong (không bị cổng chặn) — chủ máy đã bật tu_dong
        assert await tra_loi(so) == "echo v1: a"

        # Bản mới: chỉ đổi mã (lời chào), cùng tham số quyền → diff["them"] rỗng.
        gh.day(tep_kho({"echo": tep_echo("echo MA-DOC-v2", "2.0.0")}), xac_minh=False)
        await ng.mot_lan()
        assert ng.thong_bao()["danh_sach"][0]["trang_thai"] == "co_ban_moi"  # tiền đề: thấy bản mới
        await ng._tu_dong()

        assert await tra_loi(so) == "echo v1: a", "mã mới đã chạy mà không ai duyệt"
        assert b"MA-DOC-v2" not in (du_lieu / "modules" / "echo" / "module.py").read_bytes()
    chay(ham)
