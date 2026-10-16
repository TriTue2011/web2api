"""Nguồn module là git: thăm dò có điều kiện (ETag), thông báo, cài chốt sha + kiểm băm, tự cài có điều kiện.

GitHub GIẢ chạy cục bộ (gia.GitHubGia); WEB2API_GITHUB_API trỏ vào nó — biến này chỉ dành cho test.
Phần lớn test gọi thẳng `mot_lan`/`cai` trong một asyncio.run để xác định; vòng nền được kiểm riêng.
"""

import asyncio
import json
import logging
import stat
import time

import pytest
from fastapi.testclient import TestClient

from app import nguon_git, so_module
from app.main import app
from app.nguon_git import LoiGit, NguonGit
from app.so_module import LoiSo, SoModule
from gia import GitHubGia, MayChuThat, bam_blob, tep_echo, tep_kho

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
REPO = "chu-may/c2a"
TOKEN = "github_pat_BIMAT0000000000000000000000000000abcd"
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
    # NV16: NguonGit giờ dùng TransportGhimIp — cho phép loopback cho máy chủ giả trong test
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
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


def dem(gh, loai):
    return sum(1 for x in gh.nhat_ky if x["loai"] == loai)


async def tra_loi(so, id_="echo"):
    ma, _, than = await so.dang_chay[id_].chat({"model": "echo/x", "messages": [{"role": "user", "content": "a"}]}, False)
    assert ma == 200
    return json.loads(than)["choices"][0]["message"]["content"]


def khong_rac(du_lieu):
    return [p.name for p in (du_lieu / "modules").iterdir() if p.name.startswith(".")] if (du_lieu / "modules").exists() else []


# ───────────── thông báo & cài ─────────────

def test_thong_bao_moi_cai_roi_ban_moi(gh, du_lieu):
    gh.day(tep_kho({"echo": tep_echo("echo v1", "1.0.0")}), thong_diep="thêm echo")

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        tb = ng.thong_bao()
        assert tb["so_moi"] == 1
        x = tb["danh_sach"][0]
        assert (x["id"], x["trang_thai"], x["phien_ban_moi"], x["phien_ban_cu"]) == ("echo", "moi", "1.0.0", None)
        assert x["commit"]["thong_diep"] == "thêm echo" and x["commit"]["tac_gia"] == "Chủ máy"
        kq = await ng.cai("echo")
        assert kq["ok"] and kq["cai"]["ok"]  # không cần cấu hình → tự cài cấu hình rỗng
        assert await tra_loi(so) == "echo v1: a"
        nguon = json.loads((du_lieu / "modules" / "echo" / ".nguon.json").read_text())
        assert nguon["sha_commit"] == gh.nhanh["main"] and nguon["repo"] == REPO
        assert ng.thong_bao() == {"so_moi": 0, "danh_sach": [], "moi_nhat": ["echo"],   # đã cài đúng bản → «mới nhất»
                                  "dong_bo": ng.thong_bao()["dong_bo"]}
        gh.day(tep_kho({"echo": tep_echo("echo v2", "2.0.0")}))
        await ng.mot_lan()
        x = ng.thong_bao()["danh_sach"][0]
        assert (x["trang_thai"], x["phien_ban_cu"], x["phien_ban_moi"]) == ("co_ban_moi", "1.0.0", "2.0.0")
        await ng.cai("echo")
        assert await tra_loi(so) == "echo v2: a"
        assert khong_rac(du_lieu) == []
    chay(ham)


def test_304_va_commit_khong_cham_module_khong_tai_lai(gh, du_lieu):
    gh.day(tep_kho({"echo": tep_echo()}))

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        n_cay, n_blob = dem(gh, "tree"), dem(gh, "blob")
        gh.nhat_ky.clear()
        await ng.mot_lan()
        # Một vòng thăm dò bình thường = ĐÚNG 1 yêu cầu, có điều kiện, nhận 304.
        assert len(gh.nhat_ky) == 1 and gh.nhat_ky[0]["if_none_match"] == f'W/"{gh.nhanh["main"]}"'
        assert n_cay == 1 and n_blob == 1
        # Commit đổi tệp KHÁC → đọc cây, nhưng không tải lại manifest của module không đổi.
        gh.day(tep_kho({"echo": tep_echo()}, them={"docs/x.md": b"khac"}))
        gh.nhat_ky.clear()
        await ng.mot_lan()
        assert dem(gh, "tree") == 1 and dem(gh, "blob") == 0
    chay(ham)


def test_cai_chot_dung_sha_da_thay(gh, du_lieu):
    gh.day(tep_kho({"echo": tep_echo("echo v1")}))

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        gh.day(tep_kho({"echo": tep_echo("echo v2")}))  # đẩy bản mới nhưng CHƯA thăm dò
        await ng.cai("echo")
        assert await tra_loi(so) == "echo v1: a"
    chay(ham)


def test_blob_bi_sua_trai_bam_tu_choi_ca_module(gh, du_lieu):
    tep = tep_echo()
    gh.day(tep_kho({"echo": tep}))
    gh.blob_sai[bam_blob(tep["module.py"])] = tep["module.py"].replace(b"LOI_CHAO", b"LOI_CHA0")

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        with pytest.raises(LoiGit, match="SAI BĂM"):
            await ng.cai("echo")
        assert "echo" not in so.catalog and not (du_lieu / "modules" / "echo").exists()
        assert khong_rac(du_lieu) == []
    chay(ham)


@pytest.mark.parametrize("them,mau", [
    ({"web/lien-ket": ("symlink", b"../../../../etc/passwd")}, "symlink"),
    ({"phu": ("submodule", "a" * 40)}, "submodule"),
    ({"web/../../thoat.py": b"x"}, "không an toàn"),
    ({"./a.py": b"x"}, "không an toàn"),
    ({"Web/echo.dc.html": b"x"}, "trùng"),
])
def test_tu_choi_symlink_submodule_duong_thoat(gh, du_lieu, them, mau):
    gh.day(tep_kho({"echo": {**tep_echo(), **them}}))

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        with pytest.raises(LoiGit, match=mau):
            await ng.cai("echo")
        assert not (du_lieu / "modules" / "echo").exists() and khong_rac(du_lieu) == []
        assert dem(gh, "blob") == 1  # chỉ manifest lúc thăm dò; KHÔNG tải tệp nào của bản bị từ chối
    chay(ham)


@pytest.mark.parametrize("hang,gia_tri,mau", [("TRAN_TEP", 2, "vượt trần"), ("TRAN_BLOB", 600, "vượt trần"),
                                               ("TRAN_TONG", 900, "vượt trần")])
def test_tran_so_tep_va_dung_luong(gh, du_lieu, monkeypatch, hang, gia_tri, mau):
    gh.day(tep_kho({"echo": tep_echo()}))
    monkeypatch.setattr(nguon_git, hang, gia_tri)

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        with pytest.raises(LoiGit, match=mau):
            await ng.cai("echo")
        assert khong_rac(du_lieu) == [] and not (du_lieu / "modules" / "echo").exists()
    chay(ham)


def test_cai_de_ban_hong_hoan_ve_ban_cu(gh, du_lieu):
    gh.day(tep_kho({"echo": tep_echo("echo v1")}))

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        await ng.cai("echo")
        the = so.dang_chay["echo"]
        gh.day(tep_kho({"echo": tep_echo("echo v3", "3.0.0", hong=True)}))
        await ng.mot_lan()
        with pytest.raises(LoiSo, match="hoàn về bản cũ") as e:
            await ng.cai("echo")
        assert "SyntaxError" in str(e.value)
        assert so.dang_chay["echo"] is the and await tra_loi(so) == "echo v1: a"
        assert b"echo v1" in (du_lieu / "modules" / "echo" / "module.py").read_bytes()
        assert khong_rac(du_lieu) == []
        assert ng.thong_bao()["danh_sach"][0]["trang_thai"] == "co_ban_moi"
    chay(ham)


def test_tu_dong_chi_khi_co_chu_ky_va_khong_doi_quyen(gh, du_lieu):
    async def ham(so, ng):
        ng.luu_cau_hinh({**CAU_HINH, "tu_dong": True, "yeu_cau_chu_ky": True})

        async def vong():
            await ng.mot_lan()
            await ng._tu_dong()

        gh.day(tep_kho({"echo": tep_echo("echo v1")}), xac_minh=False)
        await vong()
        assert "echo" not in so.dang_chay
        assert "chữ ký" in ng.thong_bao()["danh_sach"][0]["ghi_chu"]
        gh.day(tep_kho({"echo": tep_echo("echo v1b")}), xac_minh=True)
        await vong()
        assert await tra_loi(so) == "echo v1b: a"
        # Bản mới xin thêm gói / mạng ra → cổng giữ lại chờ duyệt, KHÔNG tự cài dù đã ký.
        gh.day(tep_kho({"echo": tep_echo("echo v2", yeu_cau_goi=["json"], mang_ra=["vi-du.com"])}), xac_minh=True)
        await vong()
        assert await tra_loi(so) == "echo v1b: a"
        x = ng.thong_bao()["danh_sach"][0]
        assert x["trang_thai"] == "co_ban_moi" and "THÊM QUYỀN" in x["ghi_chu"] and "2 mục" in x["ghi_chu"]
        gh.day(tep_kho({"echo": tep_echo("echo v3")}), xac_minh=True)
        await vong()
        assert await tra_loi(so) == "echo v3: a"
    chay(ham)


def test_tu_dong_mac_dinh_tat(gh, du_lieu):
    gh.day(tep_kho({"echo": tep_echo()}), xac_minh=True)

    async def ham(so, ng):
        cfg = ng.luu_cau_hinh(CAU_HINH)["cau_hinh"]
        assert cfg["tu_dong"] is False and cfg["yeu_cau_chu_ky"] is False
        await ng.mot_lan()
        await ng._tu_dong()
        assert "echo" not in so.dang_chay and ng.so_thong_bao() == 1
    chay(ham)


# ───────────── token, hạn mức, lỗi ─────────────

def test_an_danh_khong_gui_authorization(gh, du_lieu):
    gh.day(tep_kho({"echo": tep_echo()}))

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        await ng.cai("echo")
    chay(ham)
    assert gh.nhat_ky and all(x["auth"] is None for x in gh.nhat_ky)


def test_token_gui_dung_header_va_khong_ro(gh, du_lieu, caplog):
    gh.token = TOKEN
    gh.day(tep_kho({"echo": tep_echo()}))

    async def ham(so, ng):
        cong = ng.luu_cau_hinh({**CAU_HINH, "token": TOKEN})
        await ng.mot_lan()
        await ng.cai("echo")
        gh.loi_commit.append((500, {}, f"máy chủ nhắc lại token {TOKEN}"))
        gh.day(tep_kho({"echo": tep_echo("v2")}))
        with pytest.raises(LoiGit) as e:
            await ng.mot_lan()
        return cong, str(e.value), ng.cong_khai(), ng.thong_bao()

    with caplog.at_level(logging.DEBUG):
        cong, loi, cong_khai, tb = chay(ham)
    assert all(x["auth"] == f"Bearer {TOKEN}" for x in gh.nhat_ky)
    assert cong["cau_hinh"]["token"] == "…abcd"
    p = du_lieu / "bi_mat" / "_nguon_git.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    for vb in (json.dumps(cong), loi, json.dumps(cong_khai), json.dumps(tb), caplog.text,
               (du_lieu / "nguon_git.json").read_text()):
        assert TOKEN not in vb
    assert "…abcd" in loi


def test_token_sai_bao_ro(gh, du_lieu):
    gh.token = TOKEN
    gh.day(tep_kho({"echo": tep_echo()}))

    async def ham(so, ng):
        ng.luu_cau_hinh({**CAU_HINH, "token": "sai-token-0000000000000"})
        with pytest.raises(LoiGit, match="token sai"):
            await ng.mot_lan()
    chay(ham)


def test_han_muc_thap_gian_nhip_giu_etag(gh, du_lieu):
    gh.day(tep_kho({"echo": tep_echo()}))
    reset = int(time.time()) + 600
    gh.han_muc = (5, reset)

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        etag = ng.trang_thai["etag"]
        nhip = ng.nhip_ke_tiep(1, bay_gio=reset - 600)
        assert 590 <= nhip <= 602
        assert "còn 5" in ng.trang_thai["canh_bao"] and ng.trang_thai["han_muc_con"] == 5
        await ng.mot_lan()
        assert ng.trang_thai["etag"] == etag  # không mất ETag/điều kiện
        gh.han_muc = (50, reset)
        await ng.mot_lan()
        assert ng.nhip_ke_tiep(1) == 1 and ng.trang_thai["canh_bao"] is None
    chay(ham)


def test_kho_rong_409_khong_phai_loi(gh, du_lieu):
    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        assert ng.thong_bao()["so_moi"] == 0 and ng.trang_thai["loi"] is None
    chay(ham)


def test_cay_bi_cat_bot_la_loi(gh, du_lieu):
    gh.day(tep_kho({"echo": tep_echo()}))
    gh.cat_cay = True

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)
        with pytest.raises(LoiGit, match="truncated"):
            await ng.mot_lan()
        assert ng.trang_thai.get("etag") is None  # lần sau đọc lại, không bị 304 che
    chay(ham)


def test_manifest_loi_tren_git(gh, du_lieu):
    hong = {**tep_echo(), "manifest.json": json.dumps({"id": "khac", "ten": "x"}).encode()}
    gh.day(tep_kho({"echo": tep_echo(), "xau": hong}))

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        ds = {x["id"]: x for x in ng.thong_bao()["danh_sach"]}
        assert ds["xau"]["trang_thai"] == "loi_manifest" and ds["xau"]["ly_do"]
        assert ng.thong_bao()["so_moi"] == 1
        with pytest.raises(LoiGit, match="manifest"):
            await ng.cai("xau")
    chay(ham)


@pytest.mark.parametrize("than", [
    {"repo": "../x"}, {"repo": "a/.."}, {"repo": "a/b/c"}, {"repo": ".."}, {"repo": "a/b", "nhanh": "../x"},
    {"repo": "a/b", "thu_muc": "/abs"}, {"repo": "a/b", "thu_muc": "a//b"}, {"repo": "a/b", "chu_ky_giay": 0},
    {"repo": "a/b", "tu_dong": "co"}, {"repo": "a/b", "token": 123},
])
def test_cau_hinh_sai_bi_tu_choi(du_lieu, than):
    with TestClient(app) as c:
        r = c.put("/api/nguon-git", json=than, headers=QT)
    assert r.status_code == 400 and r.json()["error"]["message"]
    assert not (du_lieu / "nguon_git.json").exists()


def test_bien_moi_truong_dien_san(du_lieu, gh, monkeypatch):
    monkeypatch.setenv("WEB2API_GIT_REPO", "TriTue2011/web2api")
    with TestClient(app) as c:
        cfg = c.get("/api/nguon-git", headers=QT).json()["cau_hinh"]
    assert cfg["repo"] == "TriTue2011/web2api" and cfg["nhanh"] == "main"
    assert cfg["thu_muc"] == "modules" and cfg["nguon"] == "bien_moi_truong" and cfg["token"] is None


# ───────────── vòng nền ─────────────

def test_vong_nen_lui_nhip_khi_loi_roi_hoi_phuc(gh, du_lieu):
    gh.day(tep_kho({"echo": tep_echo()}))
    gh.loi_commit += [(500, {}), (503, {}), (502, {})]

    async def ham(so, ng):
        ng.HE_SO_CHO = 0.01
        ng.luu_cau_hinh(CAU_HINH)
        ng.bat_dau()
        han = time.monotonic() + 10
        while ng.so_thong_bao() == 0 and time.monotonic() < han:
            await asyncio.sleep(0.05)
        assert not ng._tac_vu.done()
        return ng.so_thong_bao(), dict(ng.trang_thai)

    so_moi, tt = chay(ham)
    assert so_moi == 1 and tt["loi"] is None
    assert dem(gh, "commit") >= 4  # 3 lần lỗi rồi lần đạt (sau đó vòng nền vẫn thăm dò tiếp — 304)
    tep = json.loads((du_lieu / "nguon_git_trang_thai.json").read_text())
    assert tep["danh_sach"][0]["trang_thai"] == "moi"


def test_het_han_muc_an_danh_khong_dot_vong_lap(gh, du_lieu):
    gh.day(tep_kho({"echo": tep_echo()}))
    reset = int(time.time()) + 3600
    gh.loi_commit += [(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)},
                       "API rate limit exceeded for 1.2.3.4.")] * 5

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)  # chu_ky_giay=1, HE_SO_CHO thật
        ng.bat_dau()
        await asyncio.sleep(2.5)
        assert not ng._tac_vu.done()
        return dict(ng.trang_thai)

    tt = chay(ham)
    assert dem(gh, "commit") == 1  # chờ tới giờ reset, không nã lại mỗi giây
    assert "hạn mức" in tt["loi"] and "ẩn danh" in tt["loi"]
    assert tt["han_muc_con"] == 0 and tt["reset_luc"] and tt["thu_lai_luc"] >= tt["reset_luc"]


def test_get_mo_khong_lo_repo_va_vong_nen_that(gh, du_lieu):
    gh.day(tep_kho({"echo": tep_echo()}), thong_diep="bí mật nội bộ: thêm echo")
    with TestClient(app) as c:
        assert c.put("/api/nguon-git", json=CAU_HINH, headers=QT).status_code == 200
        han = time.monotonic() + 10
        while c.get("/api/modules").json()["thong_bao_so"] == 0 and time.monotonic() < han:
            time.sleep(0.1)
        mo = c.get("/api/modules").text + c.get("/api/modules/catalog").text
        tb = c.get("/api/modules/thong-bao", headers=QT).json()
        r = c.post("/api/nguon-git/cai/echo", headers=QT)
        assert r.status_code == 200, r.text
        assert c.get("/api/modules").json()["thong_bao_so"] == 0
        assert c.get("/api/modules").json()["modules"][0]["tab_web"]["duong"] == "/m/echo/echo.dc.html"
        assert c.post("/api/nguon-git/cai/khong-co", headers=QT).status_code == 404
    assert '"thong_bao_so":1' in mo
    for bi_mat in (REPO, "bí mật nội bộ", gh.nhanh["main"]):
        assert bi_mat not in mo
    assert tb["so_moi"] == 1 and tb["danh_sach"][0]["commit"]["thong_diep"].startswith("bí mật")


def test_go_khi_bat_tu_dong_khong_bi_cai_lai(gh, du_lieu):
    # Gỡ xong mà vòng tự cài thấy «moi» lại cài ngay bản đó thì lệnh gỡ vô nghĩa.
    gh.day(tep_kho({"echo": tep_echo("echo v1")}), xac_minh=True)
    with TestClient(app) as c:
        assert c.put("/api/nguon-git", json={**CAU_HINH, "tu_dong": True}, headers=QT).status_code == 200
        han = time.monotonic() + 10
        while "echo" not in c.app.state.so.dang_chay and time.monotonic() < han:
            time.sleep(0.1)
        assert "echo" in c.app.state.so.dang_chay
        assert c.delete("/api/modules/echo", headers=QT).status_code == 200
        time.sleep(2.5)  # ≥ 2 vòng thăm dò (chu_ky_giay=1)
        assert "echo" not in c.app.state.so.dang_chay
        x = c.get("/api/modules/thong-bao", headers=QT).json()["danh_sach"][0]
        assert x["trang_thai"] == "moi" and "đã gỡ" in x["ghi_chu"]
        gh.day(tep_kho({"echo": tep_echo("echo v2")}), xac_minh=True)  # bản MỚI thì tự cài như thường
        han = time.monotonic() + 10
        while "echo" not in c.app.state.so.dang_chay and time.monotonic() < han:
            time.sleep(0.1)
        assert "echo" in c.app.state.so.dang_chay


def test_hop_dong_giao_dien_chua_cau_hinh_va_xoa_token(du_lieu):
    # Hợp đồng trang Module dựa vào: chưa cấu hình git → thong-bao 200 rỗng, GET /api/nguon-git cau_hinh=null;
    # PUT với "token": "" là XOÁ token đã lưu; thiếu khoá "token" là GIỮ nguyên.
    with TestClient(app) as c:
        tb = c.get("/api/modules/thong-bao", headers=QT)
        assert tb.status_code == 200 and tb.json()["so_moi"] == 0 and tb.json()["danh_sach"] == []
        assert c.get("/api/nguon-git", headers=QT).json()["cau_hinh"] is None
        c.put("/api/nguon-git", json={**CAU_HINH, "token": TOKEN}, headers=QT)
        assert c.get("/api/nguon-git", headers=QT).json()["cau_hinh"]["token"] == "…abcd"
        c.put("/api/nguon-git", json={**CAU_HINH, "chu_ky_giay": 600}, headers=QT)
        assert c.get("/api/nguon-git", headers=QT).json()["cau_hinh"]["token"] == "…abcd"
        c.put("/api/nguon-git", json={**CAU_HINH, "token": ""}, headers=QT)
        assert c.get("/api/nguon-git", headers=QT).json()["cau_hinh"]["token"] is None
        assert not (du_lieu / "bi_mat" / "_nguon_git.json").exists()


def _noi_suy_compose(gia_tri: str, moi_truong: dict) -> str:
    """Nội suy ${X-mặc định} / ${X:-mặc định} như docker compose (đủ cho các dòng ta dùng)."""
    import re as _re

    def thay(m):
        ten, toan_tu, mac_dinh = m.group(1), m.group(2), m.group(3)
        if toan_tu == ":-":
            return moi_truong.get(ten) or mac_dinh
        return moi_truong[ten] if ten in moi_truong else mac_dinh
    return _re.sub(r"\$\{(\w+)(:-|-)([^}]*)\}", thay, gia_tri)


def _env_compose() -> dict:
    import re as _re
    from pathlib import Path as _P

    txt = (_P(__file__).resolve().parents[1] / "docker-compose.yml").read_text(encoding="utf-8")
    khoi = txt.split("    environment:\n", 1)[1].split("\n    env_file:", 1)[0]
    return dict(_re.findall(r"^      (WEB2API_\w+): (.+)$", khoi, flags=_re.M))


@pytest.mark.parametrize("moi_truong,mong", [
    ({}, {"repo": "TriTue2011/web2api", "nhanh": "main", "thu_muc": "modules"}),
    ({"WEB2API_GIT_REPO": "toi/kho", "WEB2API_GIT_NHANH": "thu"}, {"repo": "toi/kho", "nhanh": "thu", "thu_muc": "modules"}),
    ({"WEB2API_GIT_REPO": ""}, None),  # đặt rỗng trong .env = tắt nguồn git
])
def test_compose_dat_san_nguon_git_va_ma_doc_dung(du_lieu, monkeypatch, moi_truong, mong):
    env = _env_compose()
    assert set(env) >= {"WEB2API_GIT_REPO", "WEB2API_GIT_NHANH", "WEB2API_GIT_THU_MUC"}
    assert "WEB2API_GIT_TOKEN" not in env  # kho public: không cần token, và token không nằm trong compose
    for ten, gia_tri in env.items():
        if ten.startswith("WEB2API_GIT_"):
            monkeypatch.setenv(ten, _noi_suy_compose(gia_tri, moi_truong))

    async def ham(so, ng):
        return ng.cau_hinh()

    cfg = chay(ham)
    if mong is None:
        assert cfg is None
    else:
        assert {k: cfg[k] for k in mong} == mong
        assert cfg["tu_dong"] is False and cfg["yeu_cau_chu_ky"] is False and cfg["nguon"] == "bien_moi_truong"
