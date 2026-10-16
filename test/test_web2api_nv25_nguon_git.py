"""NV25 — lấp 2 lỗ hổng test do NV22 tìm ra ở nhóm nguồn git (CHỈ THÊM TEST, không sửa mã).

NG2 (app/nguon_git.py ~333): khi theo redirect lúc tải tarball, header Authorization
    CHỈ được gửi tiếp khi host đích trùng host API đã cấu hình. Dựng máy chủ «xấu»
    ở host khác (cổng khác → netloc khác) và khẳng định yêu cầu thứ hai KHÔNG mang
    Authorization (rò token GitHub là rủi ro cao nhất).
NG1 (app/nguon_git.py ~375): _doc_blob phát hiện nội dung blob không khớp sha trong
    cây git → LoiGit 422 «SAI BĂM», từ chối cả module.
"""

from __future__ import annotations

import asyncio

import pytest
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route

from app import nguon_git, so_module
from app.nguon_git import LoiGit, NguonGit
from app.so_module import SoModule
from gia import GitHubGia, MayChuThat, bam_blob, tep_echo, tep_kho


@pytest.fixture(autouse=True)
def _cho_loopback(monkeypatch):
    # NV16: NguonGit ghim IP và chặn loopback; máy chủ giả của test chạy ở loopback.
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")

KHOA_QT = "khoa-quan-tri-nv25-nguon-git-0123456789abcdef"
REPO = "chu-may/c2a"
TOKEN_GITHUB_GIA = "ghp_github_token_gia_nv25_khong_that"
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
    _gh.tarball_chuyen_huong = None
    _gh.tarball_tuy_chinh = None
    monkeypatch.setenv("WEB2API_GITHUB_API", _gh.url)
    return _gh


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return tmp_path


def chay(ham):
    """Chạy `ham(so, ng)` trong MỘT vòng sự kiện với sổ module + nguồn git mới."""
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


def _may_chu_xau(ho_so: dict) -> Starlette:
    """Máy chủ «xấu» ở host khác: ghi lại header Authorization rồi trả tarball giả."""

    async def tra(req):
        ho_so["so_lan"] = ho_so.get("so_lan", 0) + 1
        ho_so["authorization"] = req.headers.get("authorization")
        return Response(b"tarball-gia-nv25", media_type="application/x-gzip")

    return Starlette(routes=[Route("/tai-xau/{duong:path}", tra)])


def test_ng2_redirect_tarball_khong_ro_token_sang_host_la(gh, du_lieu, monkeypatch):
    """NG2: 302 sang host khác → yêu cầu thứ hai KHÔNG được mang Authorization.

    Đột biến NV22 (gửi Authorization sang host lạ khi redirect) phải làm test này ĐỎ.
    """
    monkeypatch.setenv("WEB2API_GITHUB_TOKEN", TOKEN_GITHUB_GIA)
    gh.token = TOKEN_GITHUB_GIA  # máy chủ API thật kiểm tra token ở yêu cầu đầu
    sha = gh.day(tep_kho({"echo": tep_echo("echo v1")}))
    ho_so_xau: dict = {}
    with MayChuThat(_may_chu_xau(ho_so_xau)) as xau:
        assert xau.url != gh.url  # cổng khác → netloc khác → «host lạ»
        gh.tarball_chuyen_huong = f"{xau.url}/tai-xau/du-lieu"

        async def ham(so, ng):
            return await ng._tai_tarball(REPO, sha)

        du_lieu_tai = chay(ham)

    assert du_lieu_tai == b"tarball-gia-nv25", "luồng tải qua redirect không hoàn tất"
    # Đối chứng: yêu cầu ĐẦU tới đúng host API có mang token (test không rỗng).
    yeu_cau_dau = [x for x in gh.nhat_ky if x["loai"] == "tarball"]
    assert yeu_cau_dau and yeu_cau_dau[0]["auth"] == f"Bearer {TOKEN_GITHUB_GIA}", \
        f"yêu cầu đầu phải mang token tới host API: {yeu_cau_dau}"
    # Khẳng định chính: yêu cầu THỨ HAI sang host lạ không mang Authorization.
    assert ho_so_xau.get("so_lan") == 1, f"máy chủ xấu phải nhận đúng 1 yêu cầu: {ho_so_xau}"
    assert ho_so_xau.get("authorization") is None, \
        f"RÒ TOKEN GitHub sang host lạ qua redirect: {ho_so_xau.get('authorization')!r}"


def test_ng1_doc_blob_sai_bam_tu_choi_422(gh, du_lieu):
    """NG1: blob có nội dung không khớp sha → LoiGit 422 «SAI BĂM» (từ chối cả module).

    Đột biến NV22 (bỏ kiểm băm blob trong _doc_blob) phải làm test này ĐỎ.
    """
    tep = tep_echo()
    gh.day(tep_kho({"echo": tep}))
    sha_dung = bam_blob(tep["module.py"])
    sha_manifest = bam_blob(tep["manifest.json"])
    # Kẻ tấn công/MITM tráo nội dung tệp module nhưng sha trong cây giữ nguyên.
    gh.blob_sai[sha_dung] = tep["module.py"].replace(b"LOI_CHAO", b"LOI_CHA0")

    async def ham(so, ng):
        # Đối chứng: blob đúng băm vẫn đọc được (test không rỗng).
        assert await ng._doc_blob(REPO, sha_manifest) == tep["manifest.json"]
        with pytest.raises(LoiGit) as exc:
            await ng._doc_blob(REPO, sha_dung)
        assert exc.value.ma == 422, f"muốn mã 422, được {exc.value.ma}"
        assert "SAI BĂM" in str(exc.value), f"thiếu dấu hiệu SAI BĂM: {exc.value}"

    chay(ham)
