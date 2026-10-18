"""Kiểm thử nguồn git: cài bằng MỘT yêu cầu tarball và hỗ trợ kho private.

Bao gồm:
1. Cài module 8 tệp = ĐÚNG 1 yêu cầu tarball (+ commit/cây của thăm dò), 0 yêu cầu blob.
2. Tarball có mục ../x, symlink, tệp quá lớn → từ chối cả lần cài, không để lại rác trong <DATA>/modules/.
3. 302 sang máy chủ khác KHÔNG mang Authorization (chỉ gửi token tới đúng máy chủ api.github.com đã cấu hình).
4. Token từ biến môi trường: ưu tiên hơn token lưu, dạng che, không lộ ra phản hồi/log/lỗi.
5. Kho private giả (trả 404 khi thiếu auth): cài được khi có token qua env; không token/sai token báo lỗi rõ.
6. Cài lại module đã cài vẫn cập nhật đúng (bản mới thay bản cũ, .nguon.json ghi đúng commit).
7. Cảnh báo chu kỳ dưới 10 phút khi chưa có token.
"""

import asyncio
import io
import json
import logging
import tarfile
import time

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response

from app import kho, nguon_git, so_module
from app.nguon_git import TEN_BI_MAT, LoiGit, NguonGit
from app.so_module import SoModule
from conftest import cai_ng_hai_luot
from test.gia import GitHubGia, MayChuThat, tep_echo, tep_kho

REPO = "chu-may/c2a"
TOKEN_GIA = "ghp_TOKENRIENG1234567890"  # Có trong allowlist .gitleaks.toml
CAU_HINH = {"repo": REPO, "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 300}


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
    _gh.kho_rieng_tu = False
    monkeypatch.setenv("WEB2API_GITHUB_API", _gh.url)
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")  # NV16: NguonGit ghim IP, máy chủ giả ở loopback
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


def dem(gh, loai):
    return sum(1 for x in gh.nhat_ky if x["loai"] == loai)


async def tra_loi(so, id_="echo"):
    ma, _, than = await so.dang_chay[id_].chat({"model": f"{id_}/x", "messages": [{"role": "user", "content": "a"}]}, False)
    assert ma == 200
    return json.loads(than)["choices"][0]["message"]["content"]


def khong_rac(du_lieu):
    return [p.name for p in (du_lieu / "modules").iterdir() if p.name.startswith(".")] if (du_lieu / "modules").exists() else []


# ───────────── (1) Cài 8 tệp = ĐÚNG 1 yêu cầu tarball ─────────────

def test_cai_module_8_tep_dung_1_yeu_cau_tarball(gh, du_lieu):
    # Module gồm 8 tệp: manifest.json, module.py, web/echo.dc.html, và 5 tệp phụ
    tep_8 = tep_echo("echo v1", "1.0.0", id_="mod8", tien_to="mod8")
    for i in range(1, 6):
        tep_8[f"phu_{i}.py"] = f"# tệp phụ {i}\nx = {i}\n".encode()
    assert len(tep_8) == 8

    gh.day(tep_kho({"mod8": tep_8}), thong_diep="thêm mod8 8 tệp")

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        assert dem(gh, "commit") == 1
        assert dem(gh, "tree") == 1
        assert dem(gh, "blob") == 1  # Chỉ tải manifest.json

        gh.nhat_ky.clear()
        # Cài module 8 tệp: ĐÚNG 1 yêu cầu tarball, không gọi blob hay tree
        kq = await cai_ng_hai_luot(ng, "mod8")
        assert kq["ok"]
        assert dem(gh, "tarball") == 1
        assert dem(gh, "blob") == 0
        assert dem(gh, "tree") == 0
        assert await tra_loi(so, "mod8") == "echo v1: a"

        # Kiểm tra đủ 8 tệp trong thư mục module
        tm_mod = du_lieu / "modules" / "mod8"
        for ten in tep_8:
            assert (tm_mod / ten).is_file()
        assert (tm_mod / ".nguon.json").is_file()
        assert khong_rac(du_lieu) == []

    chay(ham)


# ───────────── (2) Tarball độc hại bị từ chối, không để lại rác ─────────────

def test_tu_choi_tarball_chua_duong_thoat(gh, du_lieu):
    gh.day(tep_kho({"echo": tep_echo()}))

    # Dựng tarball chứa mục "../thoat.py"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        du_lieu_tep = b"doc hai"
        ti = tarfile.TarInfo(name="chu-may-c2a-1234567/modules/echo/../thoat.py")
        ti.size = len(du_lieu_tep)
        tf.addfile(ti, io.BytesIO(du_lieu_tep))
    gh.tarball_tuy_chinh = buf.getvalue()

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        with pytest.raises(LoiGit, match="không an toàn"):
            await cai_ng_hai_luot(ng, "echo")
        assert not (du_lieu / "modules" / "echo").exists()
        assert khong_rac(du_lieu) == []

    chay(ham)


def test_tu_choi_tarball_chua_symlink(gh, du_lieu):
    gh.day(tep_kho({"echo": tep_echo()}))

    # Dựng tarball chứa symlink trong module
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        goc = "chu-may-c2a-1234567/modules/echo"
        for k, v in tep_echo().items():
            ti = tarfile.TarInfo(name=f"{goc}/{k}")
            ti.size = len(v)
            tf.addfile(ti, io.BytesIO(v))
        ti_sym = tarfile.TarInfo(name=f"{goc}/lien_ket")
        ti_sym.type = tarfile.SYMTYPE
        ti_sym.linkname = "/etc/passwd"
        tf.addfile(ti_sym)
    gh.tarball_tuy_chinh = buf.getvalue()

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        with pytest.raises(LoiGit, match="symlink"):
            await cai_ng_hai_luot(ng, "echo")
        assert not (du_lieu / "modules" / "echo").exists()
        assert khong_rac(du_lieu) == []

    chay(ham)


def test_tu_choi_tarball_chua_tep_qua_lon(gh, du_lieu, monkeypatch):
    gh.day(tep_kho({"echo": tep_echo()}))

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        monkeypatch.setattr(nguon_git, "TRAN_BLOB", 100)
        with pytest.raises(LoiGit, match="vượt trần"):
            await cai_ng_hai_luot(ng, "echo")
        assert not (du_lieu / "modules" / "echo").exists()
        assert khong_rac(du_lieu) == []

    chay(ham)


# ───────────── (3) 302 sang máy chủ khác KHÔNG mang Authorization ─────────────

def test_302_sang_may_chu_khac_khong_mang_authorization(gh, du_lieu):
    gh.day(tep_kho({"echo": tep_echo()}))

    # Dựng máy chủ đích thứ hai giả lập codeload.github.com
    nhat_ky_dich = []

    async def tai_legacy(req: Request):
        nhat_ky_dich.append({
            "path": req.url.path,
            "auth": req.headers.get("authorization"),
            "host": req.headers.get("host"),
        })
        sha = gh.nhanh["main"]
        du_lieu_tb = gh._dung_tarball(sha)
        return Response(du_lieu_tb, media_type="application/x-gzip")

    from starlette.routing import Route as SRoute
    app_dich = Starlette(routes=[SRoute("/tai-tarball-legacy/main.tar.gz", tai_legacy)])
    with MayChuThat(app_dich) as s2:
        gh.tarball_chuyen_huong = f"{s2.url}/tai-tarball-legacy/main.tar.gz"

        async def ham(so, ng):
            ng.luu_cau_hinh({**CAU_HINH, "token": TOKEN_GIA})
            await ng.mot_lan()

            # Gọi cài: gh trả 302 tới s2.url
            kq = await cai_ng_hai_luot(ng, "echo")
            assert kq["ok"]

            # Kiểm tra tại máy chủ gh: có gửi Authorization
            tb_gh = next(x for x in gh.nhat_ky if x["loai"] == "tarball")
            assert tb_gh["auth"] == f"Bearer {TOKEN_GIA}"

            # Kiểm tra tại máy chủ đích s2: KHÔNG mang Authorization
            assert len(nhat_ky_dich) == 1
            assert nhat_ky_dich[0]["auth"] is None

        chay(ham)


# ───────────── (4) Token từ env: ưu tiên hơn token lưu, dạng che, không lộ ─────────────

def test_token_env_uu_tien_hon_token_luu_va_khong_lo(gh, du_lieu, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    gh.day(tep_kho({"echo": tep_echo()}))

    # Lưu một token cũ qua API
    kho.ghi_bi_mat(TEN_BI_MAT, {"token": "token-cu-da-luu-9999"})

    # Đặt token qua biến môi trường WEB2API_GITHUB_TOKEN
    monkeypatch.setenv("WEB2API_GITHUB_TOKEN", TOKEN_GIA)

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)

        # 1. Ưu tiên biến môi trường
        assert ng.token() == TOKEN_GIA
        tk, nguon = ng.token_va_nguon()
        assert tk == TOKEN_GIA
        assert nguon == "bien_moi_truong"

        # 2. cong_khai() trả token che và nguồn biến môi trường
        ck = ng.cong_khai()
        assert ck["cau_hinh"]["token"] == kho.che(TOKEN_GIA)
        assert ck["cau_hinh"]["token_nguon"] == "bien_moi_truong"
        assert ck["cau_hinh"]["nguon_token"] == "bien_moi_truong"

        # 3. Chuỗi token thô KHÔNG BAO GIỜ xuất hiện trong JSON công khai
        ck_str = json.dumps(ck)
        assert TOKEN_GIA not in ck_str

        # 4. Khi lỗi, chuỗi token thô không lọt vào thông điệp lỗi hay trang thai
        gh.token = "token-khac-khong-khop"
        with pytest.raises(LoiGit) as exc_info:
            await ng.mot_lan()
        assert TOKEN_GIA not in str(exc_info.value)
        assert TOKEN_GIA not in str(ng.trang_thai.get("loi"))

        # 5. Token thô không lọt vào log
        assert TOKEN_GIA not in caplog.text

    chay(ham)


# ───────────── (5) Kho private giả: 404 khi thiếu token, cài được khi có token ─────────────

def test_kho_private_gia_bat_buoc_token(gh, du_lieu, monkeypatch):
    gh.day(tep_kho({"echo": tep_echo()}))
    gh.kho_rieng_tu = True
    gh.token = TOKEN_GIA

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)

        # Chưa có token: báo lỗi rõ ràng về kho riêng tư và quyền Contents: Read
        with pytest.raises(LoiGit, match=r"kho riêng tư cần token có quyền đọc \(Contents: Read\)"):
            await ng.mot_lan()

        # Token sai: báo lỗi rõ ràng token sai hoặc hết hạn
        monkeypatch.setenv("WEB2API_GITHUB_TOKEN", "ghp_TOKEN_SAI_000000000000000")
        with pytest.raises(LoiGit, match="token sai hoặc hết hạn"):
            await ng.mot_lan()

        # Token đúng qua env: thăm dò và cài thành công
        monkeypatch.setenv("WEB2API_GITHUB_TOKEN", TOKEN_GIA)
        await ng.mot_lan()
        kq = await cai_ng_hai_luot(ng, "echo")
        assert kq["ok"]
        assert await tra_loi(so) == "echo v1: a"

    chay(ham)


# ───────────── (6) Cài lại module đã cài vẫn cập nhật đúng ─────────────

def test_cai_lai_cap_nhat_dung_va_ghi_nguon_commit(gh, du_lieu):
    gh.day(tep_kho({"echo": tep_echo("echo v1", "1.0.0")}), thong_diep="commit ban dau")

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        c1 = gh.nhanh["main"]

        # Cài lần đầu
        kq1 = await cai_ng_hai_luot(ng, "echo")
        assert kq1["ok"]
        assert await tra_loi(so) == "echo v1: a"
        nguon1 = json.loads((du_lieu / "modules" / "echo" / ".nguon.json").read_text(encoding="utf-8"))
        assert nguon1["sha_commit"] == c1
        assert nguon1["phien_ban"] == "1.0.0"

        # Đẩy bản mới v2.0.0
        gh.day(tep_kho({"echo": tep_echo("echo v2", "2.0.0")}), thong_diep="commit ban moi")
        await ng.mot_lan()
        c2 = gh.nhanh["main"]
        assert c1 != c2

        # Cài cập nhật
        kq2 = await ng.cai("echo")
        assert kq2["ok"]
        assert await tra_loi(so) == "echo v2: a"
        nguon2 = json.loads((du_lieu / "modules" / "echo" / ".nguon.json").read_text(encoding="utf-8"))
        assert nguon2["sha_commit"] == c2
        assert nguon2["phien_ban"] == "2.0.0"

    chay(ham)


# ───────────── (7) Cảnh báo chu kỳ dưới 10 phút khi chưa có token ─────────────

def test_canh_bao_chu_ky_ngan_khi_khong_co_token(gh, du_lieu, monkeypatch):
    monkeypatch.delenv("WEB2API_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("WEB2API_GIT_TOKEN", raising=False)

    async def ham(so, ng):
        # 1. Chưa có token, chu_ky_giay = 300 (< 600) → có cảnh báo
        ng.luu_cau_hinh({**CAU_HINH, "chu_ky_giay": 300})
        ck = ng.cong_khai()
        assert "chu kỳ dưới 10 phút khi chưa có token dễ hết hạn mức (60 yêu cầu/giờ)" in (ck["trang_thai"]["canh_bao"] or "")

        # 2. Có token qua env → không có cảnh báo chu kỳ
        monkeypatch.setenv("WEB2API_GITHUB_TOKEN", TOKEN_GIA)
        ck2 = ng.cong_khai()
        assert ck2["trang_thai"]["canh_bao"] is None

        # 3. Không token nhưng chu_ky_giay = 600 (>= 600) → không cảnh báo
        monkeypatch.delenv("WEB2API_GITHUB_TOKEN", raising=False)
        ng.luu_cau_hinh({**CAU_HINH, "chu_ky_giay": 600})
        ck3 = ng.cong_khai()
        assert ck3["trang_thai"]["canh_bao"] is None

    chay(ham)
