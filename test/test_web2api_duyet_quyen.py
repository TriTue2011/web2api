"""Chống «rug-pull» khi module cập nhật từ git (cẩm nang §23): bản mới xin thêm quyền
thì KHÔNG tự kích hoạt, chờ quản trị duyệt đúng phần thêm.

Dùng lại GitHub giả (gia.GitHubGia) và module echo giả — KHÔNG viết máy chủ GitHub mới.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import kho, nguon_git, so_module
from app.main import app
from app.nguon_git import LoiGit, NguonGit
from app.quyen_module import bam_diff, cac_muc_chuan, diff_quyen, tap_quyen
from app.so_module import LoiCanDuyetQuyen, LoiSo, SoModule
from gia import GitHubGia, MayChuThat, tep_echo, tep_kho

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
REPO = "chu-may/c2a"
CAU_HINH = {"repo": REPO, "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 1}

TRUONG_COOKIE = [{"khoa": "cookie_dn", "nhan": "Cookie đăng nhập", "kieu": "danh_sach_bi_mat",
                  "nhan_cookie": {"mien": "vi-du.example"}}]


def tep_echo_quyen(loi_chao="echo v1", **patch_manifest):
    """Module echo giả với manifest đã vá thêm quyền."""
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


async def tra_loi(so, id_="echo"):
    ma, _, than = await so.dang_chay[id_].chat({"model": "echo/x", "messages": [{"role": "user", "content": "a"}]}, False)
    assert ma == 200
    return json.loads(than)["choices"][0]["message"]["content"]


async def cai_v1(so, ng, gh, **quyen_v1):
    gh.day(tep_kho({"echo": tep_echo_quyen("echo v1", **quyen_v1)}))
    ng.luu_cau_hinh(CAU_HINH)
    await ng.mot_lan()
    await ng.cai("echo")
    assert await tra_loi(so) == "echo v1: a"


# ── Đơn vị: diff_quyen ───────────────────────────────────────────────────────

def test_diff_quyen_them_bot():
    cu = {"cung_cap": ["chat"], "yeu_cau_goi": ["a"]}
    moi = {"cung_cap": ["chat", "anh"], "yeu_cau_goi": []}
    d = diff_quyen(cu, moi)
    assert {"loai": "cung_cap", "ten": "anh"} in d["them"]
    assert {"loai": "yeu_cau_goi", "ten": "a"} in d["bot"]
    assert len(d["them"]) == 1 and len(d["bot"]) == 1


def test_diff_quyen_truong_la_mac_dinh_dong():
    d = diff_quyen({"id": "x"}, {"id": "x", "quyen_sieu_cap": True})
    assert len(d["them"]) == 1 and d["them"][0]["loai"] == "truong_la"
    # đổi giá trị của trường lạ cũng thành «thêm»
    d2 = diff_quyen({"quyen_sieu_cap": True}, {"quyen_sieu_cap": {"mo_rong": True}})
    assert len(d2["them"]) == 1 and len(d2["bot"]) == 1
    # trường mô tả/hiển thị không phải quyền
    d3 = diff_quyen({"mo_ta": "a", "phien_ban": "1"}, {"mo_ta": "b", "phien_ban": "2"})
    assert d3 == {"them": [], "bot": []}


def test_diff_quyen_nhan_cookie_mang_ra():
    cu = {"tai_nguyen": {"mang_ra": []}, "truong_cau_hinh": []}
    moi = {"tai_nguyen": {"mang_ra": ["vi-du.com"]},
           "truong_cau_hinh": [{"khoa": "c", "nhan_cookie": {"mien": "vi-du.example"}}]}
    d = diff_quyen(cu, moi)
    assert {"loai": "mang_ra", "ten": "vi-du.com"} in d["them"]
    assert {"loai": "nhan_cookie", "ten": "vi-du.example"} in d["them"]


def test_bam_diff_on_dinh_va_cac_muc_chuan():
    d = diff_quyen({"cung_cap": []}, {"cung_cap": ["chat"]})
    assert bam_diff(d) == bam_diff(diff_quyen({"cung_cap": []}, {"cung_cap": ["chat"]}))
    assert bam_diff(d) != bam_diff({"them": [], "bot": []})
    # chuẩn hoá: thứ tự khác nhau vẫn khớp
    assert cac_muc_chuan([{"ten": "b", "loai": "x"}, {"loai": "x", "ten": "a"}]) == \
        cac_muc_chuan([{"loai": "x", "ten": "a"}, {"loai": "x", "ten": "b"}])


# ── Tích hợp: cổng cài ───────────────────────────────────────────────────────

def test_cap_nhat_them_nhan_cookie_409_ban_cu_van_chay(gh, du_lieu):
    async def ham(so, ng):
        await cai_v1(so, ng, gh)
        gh.day(tep_kho({"echo": tep_echo_quyen("echo v2", truong_cau_hinh=TRUONG_COOKIE)}))
        await ng.mot_lan()
        with pytest.raises(LoiCanDuyetQuyen) as ei:
            await ng.cai("echo")
        assert ei.value.ma == 409
        assert {"loai": "nhan_cookie", "ten": "vi-du.example"} in ei.value.diff["them"]
        assert await tra_loi(so) == "echo v1: a"  # bản cũ tiếp tục chạy nguyên trạng
        assert kho.doc_cho_duyet_quyen("echo") is not None  # yêu cầu chờ duyệt đã lưu
    chay(ham)


@pytest.mark.parametrize("quyen_moi", [
    {"cung_cap": ["chat"]},
    {"yeu_cau_goi": ["requests"]},
    {"quyen_la_chua_biet": "bat_ngo"},
])
def test_them_quyen_moi_loai_deu_409(gh, du_lieu, quyen_moi):
    async def ham(so, ng):
        await cai_v1(so, ng, gh)
        gh.day(tep_kho({"echo": tep_echo_quyen("echo v2", **quyen_moi)}))
        await ng.mot_lan()
        with pytest.raises(LoiCanDuyetQuyen):
            await ng.cai("echo")
        assert await tra_loi(so) == "echo v1: a"
    chay(ham)


def test_bot_quyen_qua_tu_do(gh, du_lieu):
    async def ham(so, ng):
        await cai_v1(so, ng, gh, cung_cap=["chat"], yeu_cau_goi=["json"])  # json có sẵn nên cài được
        gh.day(tep_kho({"echo": tep_echo_quyen("echo v2")}))
        await ng.mot_lan()
        kq = await ng.cai("echo")  # bớt quyền: không cần duyệt
        assert kq["ok"] is True
        assert kq["diff_quyen"]["bot"]  # ghi nhận phần bớt
        assert await tra_loi(so) == "echo v2: a"
    chay(ham)


def test_duyet_dung_diff_duoc_ap_dung(gh, du_lieu):
    async def ham(so, ng):
        await cai_v1(so, ng, gh)
        gh.day(tep_kho({"echo": tep_echo_quyen("echo v2", truong_cau_hinh=TRUONG_COOKIE)}))
        await ng.mot_lan()
        with pytest.raises(LoiCanDuyetQuyen) as ei:
            await ng.cai("echo")
        diff, sha = ei.value.diff, ei.value.sha_commit
        kq = await ng.cai("echo", chap_nhan_quyen_them=diff["them"], sha_commit=sha)
        assert kq["ok"] is True
        assert await tra_loi(so) == "echo v2: a"
        assert kho.doc_cho_duyet_quyen("echo") is None  # duyệt xong xoá yêu cầu
    chay(ham)


def test_duong_thu_muc_cho_duyet_suy_tu_id_khong_tin_json(gh, du_lieu, tmp_path):
    """Sửa `thu_muc` trong cho_duyet_quyen.json thành thư mục LẠ (chứa bản khác) không làm đổi nơi cài:
    bản kích hoạt vẫn là bản đã xem diff (suy từ id), và thư mục lạ không bị xoá."""
    la = tmp_path / "thu-muc-la"
    la.mkdir()
    (la / "dau-vet.txt").write_text("không được đụng", encoding="utf-8")

    async def ham(so, ng):
        await cai_v1(so, ng, gh)
        gh.day(tep_kho({"echo": tep_echo_quyen("echo v2", truong_cau_hinh=TRUONG_COOKIE)}))
        await ng.mot_lan()
        with pytest.raises(LoiCanDuyetQuyen) as ei:
            await ng.cai("echo")
        diff, sha = ei.value.diff, ei.value.sha_commit
        cho = kho.doc_cho_duyet_quyen("echo")
        cho["thu_muc"] = str(la)  # giả sửa tệp: trỏ sang thư mục lạ
        kho.luu_cho_duyet_quyen("echo", cho)
        # yêu cầu mới ghi đè: không được rmtree thư mục lạ
        gh.day(tep_kho({"echo": tep_echo_quyen("echo v3", truong_cau_hinh=TRUONG_COOKIE)}))
        await ng.mot_lan()
        with pytest.raises(LoiCanDuyetQuyen) as ei2:
            await ng.cai("echo")
        assert (la / "dau-vet.txt").exists(), "thư mục lạ trong JSON bị xoá"
        kq = await ng.cai("echo", chap_nhan_quyen_them=ei2.value.diff["them"], sha_commit=ei2.value.sha_commit)
        assert kq["ok"] is True
        assert await tra_loi(so) == "echo v3: a"
        assert (la / "dau-vet.txt").exists()
    chay(ham)


def test_duyet_sai_thieu_409(gh, du_lieu):
    async def ham(so, ng):
        await cai_v1(so, ng, gh)
        gh.day(tep_kho({"echo": tep_echo_quyen("echo v2", truong_cau_hinh=TRUONG_COOKIE,
                                                cung_cap=["chat"])}))
        await ng.mot_lan()
        with pytest.raises(LoiCanDuyetQuyen) as ei:
            await ng.cai("echo")
        diff, sha = ei.value.diff, ei.value.sha_commit
        assert len(diff["them"]) == 2
        thieu = diff["them"][:-1]
        with pytest.raises((LoiSo, LoiGit), match="409|khớp"):
            await ng.cai("echo", chap_nhan_quyen_them=thieu, sha_commit=sha)
        assert await tra_loi(so) == "echo v1: a"
        thua = diff["them"] + [{"loai": "cung_cap", "ten": "gia"}]
        with pytest.raises((LoiSo, LoiGit)):
            await ng.cai("echo", chap_nhan_quyen_them=thua, sha_commit=sha)
        assert await tra_loi(so) == "echo v1: a"
    chay(ham)


def test_commit_doi_duyet_cu_mat_hieu_luc(gh, du_lieu):
    async def ham(so, ng):
        await cai_v1(so, ng, gh)
        gh.day(tep_kho({"echo": tep_echo_quyen("echo v2", cung_cap=["chat"])}))
        await ng.mot_lan()
        with pytest.raises(LoiCanDuyetQuyen) as ei_a:
            await ng.cai("echo")
        diff_a, sha_a = ei_a.value.diff, ei_a.value.sha_commit
        # kho đẩy tiếp commit mới sau khi quản trị đã xem diff của v2
        gh.day(tep_kho({"echo": tep_echo_quyen("echo v3", cung_cap=["chat", "anh"])}))
        await ng.mot_lan()
        with pytest.raises(LoiCanDuyetQuyen):
            await ng.cai("echo")
        # duyệt bằng diff+sha CŨ → mất hiệu lực
        with pytest.raises((LoiSo, LoiGit)):
            await ng.cai("echo", chap_nhan_quyen_them=diff_a["them"], sha_commit=sha_a)
        assert await tra_loi(so) == "echo v1: a"
    chay(ham)


def test_tu_dong_giu_ban_xin_them_quyen(gh, du_lieu):
    async def ham(so, ng):
        ng.luu_cau_hinh({**CAU_HINH, "tu_dong": True})

        async def vong():
            await ng.mot_lan()
            await ng._tu_dong()

        gh.day(tep_kho({"echo": tep_echo("echo v1")}))
        await vong()
        assert await tra_loi(so) == "echo v1: a"
        gh.day(tep_kho({"echo": tep_echo_quyen("echo v2", truong_cau_hinh=TRUONG_COOKIE)}))
        await vong()
        assert await tra_loi(so) == "echo v1: a"  # KHÔNG tự cài bản xin thêm quyền
        x = ng.thong_bao()["danh_sach"][0]
        assert x["trang_thai"] == "co_ban_moi" and "THÊM QUYỀN" in x["ghi_chu"]
        assert kho.doc_cho_duyet_quyen("echo") is not None  # giữ lại chờ duyệt
    chay(ham)


def test_cai_moi_khong_bi_chan_nhung_neu_quyen(gh, du_lieu):
    async def ham(so, ng):
        gh.day(tep_kho({"echo": tep_echo_quyen("echo v1", truong_cau_hinh=TRUONG_COOKIE,
                                                cung_cap=["chat"])}))
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        kq = await ng.cai("echo")  # cài mới: không chặn
        assert kq["ok"] is True
        quyen = (kq.get("diff_quyen") or {}).get("them") or []
        loai_ten = {(m["loai"], m["ten"]) for m in quyen}
        assert ("nhan_cookie", "vi-du.example") in loai_ten
        assert ("cung_cap", "chat") in loai_ten
    chay(ham)


# ── HTTP: đường duyệt ───────────────────────────────────────────────────────

def test_duong_cai_http_409_kem_diff_va_duyet(gh, du_lieu):
    async def chuan_bi(so, ng):
        await cai_v1(so, ng, gh)
        gh.day(tep_kho({"echo": tep_echo_quyen("echo v2", cung_cap=["chat"])}))
        await ng.mot_lan()

    async def _():
        so = SoModule()
        await so.khoi_dong()
        ng = NguonGit(so)
        try:
            await chuan_bi(so, ng)
        finally:
            await ng.dung()
            await so.dung_het()
    asyncio.run(_())

    with TestClient(app) as c:
        r = c.post("/api/nguon-git/cai/echo", headers=QT)
        assert r.status_code == 409, r.text
        than = r.json()
        assert than["ma"] == "can_duyet_quyen"
        assert {"loai": "cung_cap", "ten": "chat"} in than["diff"]["them"]
        # duyệt qua HTTP (NV50: cần xác nhận lần 2)
        r2 = c.post("/api/nguon-git/cai/echo", headers=QT,
                    json={"chap_nhan_quyen_them": than["diff"]["them"], "sha_commit": than["sha_commit"],
                          "xac_nhan_lan_2": True})
        assert r2.status_code == 200, r2.text
        assert r2.json()["ok"] is True


def test_duong_duyet_khach_403_khong_khoa_401(du_lieu):
    from app import khoa_khach
    ban_ghi, khoa_tho = khoa_khach.tao_khoa(nhan="khách test", pham_vi=["x"])
    try:
        with TestClient(app) as c:
            r = c.post("/api/nguon-git/cai/echo")
            assert r.status_code == 401, r.text
            r = c.post("/api/nguon-git/cai/echo",
                       headers={"Authorization": f"Bearer {khoa_tho}"})
            assert r.status_code == 403, r.text
    finally:
        khoa_khach.xoa_khoa(ban_ghi["id"])


def test_get_modules_mo_chi_boolean(gh, du_lieu):
    async def ham(so, ng):
        await cai_v1(so, ng, gh)
        gh.day(tep_kho({"echo": tep_echo_quyen("echo v2", cung_cap=["chat"])}))
        await ng.mot_lan()
        with pytest.raises(LoiCanDuyetQuyen):
            await ng.cai("echo")
    chay(ham)
    with TestClient(app) as c:
        r = c.get("/api/modules", headers=QT)
        assert r.status_code == 200
        echo = next(m for m in r.json()["modules"] if m["id"] == "echo")
        assert echo["cho_duyet_quyen"] is True
        assert "diff" not in echo and "them" not in echo


def test_cho_duyet_quyen_khi_tep_cho_duyet_hong_khong_mo_coi_thu_muc(du_lieu, tmp_path):
    """T3: cho_duyet_quyen.json hỏng → ném LoiSo có code='store_unreadable', thư mục chờ duyệt bị dọn (không mồ côi)."""
    from app.so_module import SoModule, LoiSo
    from pathlib import Path
    import os
    p_data = Path(os.environ["WEB2API_DATA"])
    (p_data / "modules").mkdir(parents=True, exist_ok=True)
    (p_data / "cho_duyet_quyen.json").write_text("{hong json", encoding="utf-8")

    so = SoModule()
    tam = tmp_path / "tam_mod"
    tam.mkdir()
    (tam / "manifest.json").write_text('{"id": "echo", "ten": "Echo"}')

    cho = so.thu_muc_cho_duyet("echo")
    with pytest.raises(LoiSo) as exc_info:
        so._cho_duyet_quyen("echo", tam, {"phien_ban": "1"}, {"sha_commit": "abc"}, {"them": [{"loai": "x"}]})

    assert exc_info.value.ma == 500
    assert exc_info.value.code == "store_unreadable"
    assert not cho.exists(), f"Thư mục chờ duyệt {cho} bị mồ côi trên đĩa!"

