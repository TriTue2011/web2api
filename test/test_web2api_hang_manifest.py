"""Kiểm tra bắt buộc khai trường `hang` trong manifest.json (NV60).

Theo docs/CHUAN-BAO-MAT-MODULE.md mục 2.1:
- `hang` bắt buộc là "A" hoặc "B"
- `kiem_manifest` từ chối thiếu hoặc giá trị lạ
- Module cũ đang chạy thiếu `hang` không bị biến mất khi cập nhật
- Bản mới thiếu `hang` bị từ chối khi cài (giữ bản cũ đang chạy, báo lỗi rõ)
- 5 module có sẵn trong kho đều hợp lệ với hang="A"
- Lõi chạy đúng khi không có module nào
- Module lạ mang hang="B" không làm lỗi (mã mở)
"""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app import so_module
from app.kham_pha import kiem_manifest, doc_manifest
from app.main import app
from app.nguon_git import LoiGit, NguonGit
from app.so_module import LoiSo, SoModule
from gia import GitHubGia, MayChuThat, ghi_thu_muc, tep_echo, tep_kho

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
REPO = "chu-may/c2a"
CAU_HINH = {"repo": REPO, "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 1}
GOC = Path(__file__).resolve().parents[1]


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
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    anh = tmp_path / "anh"
    anh.mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "modules").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh)
    return tmp_path


def chay(ham):
    import asyncio
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


def tin(model="echo/lap-lai", noi_dung="chào"):
    return {"model": model, "messages": [{"role": "user", "content": noi_dung}]}


async def tra_loi_so(so, model="echo/lap-lai"):
    ma, _, b = await so.dang_chay["echo"].chat(tin(model, "a"), False)
    assert ma == 200
    return json.loads(b)["choices"][0]["message"]["content"]


def test_kiem_manifest_thieu_hang_bao_loi():
    m = {
        "id": "test-mod",
        "ten": "Test Mod",
        "mo_ta": "Mô tả test",
        "loai": "ai",
        "giao_thuc": "openai",
        "tien_to": "test",
        "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []},
    }
    loi = kiem_manifest(m)
    assert loi is not None
    assert "hang" in loi


def test_kiem_manifest_hang_la_bao_loi():
    m = {
        "id": "test-mod",
        "ten": "Test Mod",
        "mo_ta": "Mô tả test",
        "loai": "ai",
        "giao_thuc": "openai",
        "tien_to": "test",
        "hang": "C",
        "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []},
    }
    loi = kiem_manifest(m)
    assert loi is not None
    assert "hang" in loi and "'C'" in loi

    for gia_tri_la in ["", "a", "b", "AB", 1, True, None]:
        m["hang"] = gia_tri_la
        loi = kiem_manifest(m)
        assert loi is not None, f"hang={gia_tri_la!r} phải bị từ chối"
        assert "hang" in loi


def test_kiem_manifest_hang_a_va_b_hop_le():
    m = {
        "id": "test-mod",
        "ten": "Test Mod",
        "mo_ta": "Mô tả test",
        "loai": "ai",
        "giao_thuc": "openai",
        "tien_to": "test",
        "hang": "A",
        "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []},
    }
    assert kiem_manifest(m) is None
    m["hang"] = "B"
    assert kiem_manifest(m) is None


def test_nam_module_co_san_deu_hop_le_hang_a():
    danh_sach = ["antigravity", "gemini-api", "muse", "muse2api", "vnc-xa"]
    for id_ in danh_sach:
        tm = GOC / "modules" / id_
        assert tm.is_dir(), f"thiếu thư mục modules/{id_}"
        manifest, loi = doc_manifest(tm)
        assert loi is None, f"module {id_} lỗi manifest: {loi}"
        assert manifest is not None
        assert manifest.get("hang") == "A", f"module {id_} phải khai hang='A'"


def test_loi_chay_dung_khi_khong_co_module_nao(du_lieu):
    with TestClient(app) as c:
        r_health = c.get("/health")
        assert r_health.status_code == 200

        r_cat = c.get("/api/modules/catalog", headers=QT)
        assert r_cat.status_code == 200
        assert r_cat.json()["modules"] == []

        r_models = c.get("/v1/models", headers=QT)
        assert r_models.status_code == 200
        assert r_models.json()["data"] == []


def test_module_la_mang_hang_b_khong_lam_loi(du_lieu):
    tm = du_lieu / "data" / "modules" / "mod-la"
    manifest = {
        "id": "mod-la",
        "ten": "Module Lạ B",
        "mo_ta": "Module bên thứ ba hạng B với khả năng lạ",
        "hang": "B",
        "loai": "loai_la_x",
        "giao_thuc": "giao_thuc_la_y",
        "tien_to": "modla",
        "cung_cap": ["kha_nang_la_z"],
        "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": ["ngoai.example.com"]},
    }
    ma = '''
class Module:
    def __init__(self, manifest):
        self.manifest = manifest
    def nap(self, c): pass
    async def cai(self, c): return {"ok": True}
    async def suc_khoe(self): return {"ok": True}
    async def dung(self): pass
'''
    ghi_thu_muc(tm, {
        "manifest.json": json.dumps(manifest).encode("utf-8"),
        "module.py": ma.encode("utf-8"),
    })

    with TestClient(app) as c:
        r_cat = c.get("/api/modules/catalog", headers=QT)
        assert r_cat.status_code == 200
        j = r_cat.json()
        ids = [m["id"] for m in j["modules"]]
        assert "mod-la" in ids
        # Khả năng lạ và hạng B không làm lỗi hệ thống
        assert c.get("/health").status_code == 200


def test_cap_nhat_git_ban_moi_thieu_hang_bi_tu_choi_giu_ban_cu_dang_chay(gh, du_lieu):
    gh.day(tep_kho({"echo": tep_echo("echo v1", hang="A")}))

    async def ham(so, ng):
        from conftest import cai_ng_hai_luot
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()
        await cai_ng_hai_luot(ng, "echo")
        the = so.dang_chay["echo"]
        assert await tra_loi_so(so) == "echo v1: a"

        # Bản mới trên git thiếu hang
        tep_v2 = tep_echo("echo v2", "2.0.0")
        m_v2 = json.loads(tep_v2["manifest.json"].decode("utf-8"))
        del m_v2["hang"]
        tep_v2["manifest.json"] = json.dumps(m_v2).encode("utf-8")
        gh.day(tep_kho({"echo": tep_v2}))
        await ng.mot_lan()

        # Cài bản mới bị từ chối vì thiếu hang
        with pytest.raises(LoiGit, match="thiếu «hang»") as e:
            await ng.cai("echo")
        assert "hang" in str(e.value)

        # Bản cũ vẫn đang chạy và phục vụ bình thường
        assert so.dang_chay["echo"] is the
        assert await tra_loi_so(so) == "echo v1: a"
    chay(ham)


def test_cap_nhat_git_ban_dang_chay_thieu_hang_khong_bi_bien_mat(gh, du_lieu):
    # Giả lập bản cũ đang chạy được cài từ trước khi có quy định hang
    tep_v1 = tep_echo("echo v1", "1.0.0")
    m_v1 = json.loads(tep_v1["manifest.json"].decode("utf-8"))
    del m_v1["hang"]
    tep_v1["manifest.json"] = json.dumps(m_v1).encode("utf-8")

    tm_echo = du_lieu / "data" / "modules" / "echo"
    ghi_thu_muc(tm_echo, tep_v1)

    async def ham(so, ng):
        from app.kham_pha import nap_ma, van_tay
        from app.quyen_module import diff_quyen
        vt = van_tay(tm_echo)
        lop, ten = nap_ma(tm_echo, m_v1, vt)
        km = so_module.ModuleKhamPha(
            id="echo", nguon="du_lieu", thu_muc=tm_echo, manifest=m_v1,
            lop=lop, van_tay=vt, ten_ma=ten
        )
        so.catalog["echo"] = km
        the = km.tao()
        the.nap({})
        so.dang_chay["echo"] = the

        assert await tra_loi_so(so) == "echo v1: a"

        # Bản mới trên git thiếu hang -> bị từ chối, bản cũ thiếu hang vẫn đang chạy và không bị biến mất
        tep_v2_thieu = tep_echo("echo v2", "2.0.0")
        m_v2_thieu = json.loads(tep_v2_thieu["manifest.json"].decode("utf-8"))
        del m_v2_thieu["hang"]
        tep_v2_thieu["manifest.json"] = json.dumps(m_v2_thieu).encode("utf-8")
        gh.day(tep_kho({"echo": tep_v2_thieu}))
        ng.luu_cau_hinh(CAU_HINH)
        await ng.mot_lan()

        with pytest.raises(LoiGit, match="thiếu «hang»"):
            await ng.cai("echo")

        assert "echo" in so.catalog
        assert "echo" in so.dang_chay
        assert await tra_loi_so(so) == "echo v1: a"

        # Khi nạp lại hệ thống: manifest thiếu hang ở bản đang chạy không làm module biến mất
        await so.nap_lai()
        assert "echo" in so.catalog
        assert "echo" in so.dang_chay
        assert await tra_loi_so(so) == "echo v1: a"
    chay(ham)
