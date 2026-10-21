"""NV222 — phụ thuộc module: kế hoạch cài topo + đồ thị + cài theo kế hoạch.

ĐO (bước a): chỉ 4 module AI web khai `can_module` (đều ["vnc-xa"]); chatgpt-web
PHỤ THUỘC THẬT vào may-dang-nhap (modules/chatgpt-web/lam_moi_jwt.py nạp cứng
đường dẫn modules/may-dang-nhap/module.py — thiếu là vỡ vòng làm mới JWT) nhưng
chưa khai; giao diện không hề dùng `can_module_thieu` (không đề nghị cài 1 bấm,
không sơ đồ).

Test chạy trên thư mục dữ liệu tạm, không mạng ngoài.
"""

import asyncio
import json
import shutil

import pytest
from fastapi.testclient import TestClient

from app import kho, so_module
from app.main import app
from app.so_module import LoiSo, SoModule
from gia import ghi_thu_muc

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

MODULE_PY = """class Module:
    def __init__(self, manifest):
        self.manifest = manifest

    def nap(self, cau_hinh):
        self.cau_hinh = cau_hinh

    async def cai(self, cau_hinh):
        return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": {}}

    async def suc_khoe(self):
        return {"ok": True}

    async def dung(self):
        pass
"""


def manifest_gia(id_, can_module="khong_khai", **them):
    m = {
        "id": id_, "ten": f"Module {id_}", "mo_ta": "module giả cho test NV222",
        "hang": "A", "loai": "ai", "giao_thuc": "openai", "tien_to": id_,
        "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []},
    }
    if can_module != "khong_khai":
        m["can_module"] = can_module
    m.update(them)
    return m


def tep_module(id_, can_module="khong_khai", **them):
    return {"manifest.json": json.dumps(manifest_gia(id_, can_module, **them),
                                        ensure_ascii=False).encode(),
            "module.py": MODULE_PY.encode()}


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
        try:
            return await ham(so)
        finally:
            await so.dung_het()
    return asyncio.run(_())


async def cai_tu_thu_muc(so, du_lieu, id_, **kw):
    tam = du_lieu / f".tam-{id_}"
    if tam.exists():
        shutil.rmtree(tam)
    ghi_thu_muc(tam, tep_module(id_, **kw))
    kho.thu_muc_modules().mkdir(parents=True, exist_ok=True)
    return await so.cai_thu_muc(id_, tam)


# ───────────── ke_hoach_cai: thứ tự topo, khử trùng, đủ thì rỗng ─────────────

def test_ke_hoach_cai_thu_tu_topo(du_lieu):
    """A cần [B, C]; B cần [D] (biết qua bản đồ ngoài); C đã cài
    → kế hoạch [D, B] (cái cần đứng trước)."""
    async def ham(so):
        await cai_tu_thu_muc(so, du_lieu, "mod-c")
        await cai_tu_thu_muc(so, du_lieu, "mod-a", can_module=["mod-b", "mod-c"])
        kh = so.ke_hoach_cai("mod-a", ban_do_ngoai={"mod-b": ["mod-d"]})
        assert kh["id"] == "mod-a"
        assert kh["ke_hoach"] == ["mod-d", "mod-b"]
        assert kh["da_du"] is False
    chay(ham)


def test_ke_hoach_cai_kim_cuong_chi_mot_lan(du_lieu):
    """A cần [B, C]; B, C cùng cần D → D chỉ một lần, đứng trước cả hai."""
    async def ham(so):
        await cai_tu_thu_muc(so, du_lieu, "mod-a", can_module=["mod-b", "mod-c"])
        kh = so.ke_hoach_cai("mod-a", ban_do_ngoai={"mod-b": ["mod-d"], "mod-c": ["mod-d"]})
        assert kh["ke_hoach"].count("mod-d") == 1
        assert kh["ke_hoach"].index("mod-d") < kh["ke_hoach"].index("mod-b")
        assert kh["ke_hoach"].index("mod-d") < kh["ke_hoach"].index("mod-c")
    chay(ham)


def test_ke_hoach_cai_da_du(du_lieu):
    async def ham(so):
        await cai_tu_thu_muc(so, du_lieu, "vnc-xa")
        await cai_tu_thu_muc(so, du_lieu, "provider-a", can_module=["vnc-xa"])
        kh = so.ke_hoach_cai("provider-a")
        assert kh == {"id": "provider-a", "ke_hoach": [], "da_du": True}
    chay(ham)


def test_ke_hoach_cai_khong_khai(du_lieu):
    async def ham(so):
        await cai_tu_thu_muc(so, du_lieu, "doc-lap")
        assert so.ke_hoach_cai("doc-lap")["ke_hoach"] == []
    chay(ham)


def test_ke_hoach_cai_id_la_404(du_lieu):
    async def ham(so):
        with pytest.raises(LoiSo) as e:
            so.ke_hoach_cai("khong-co")
        assert e.value.ma == 404
    chay(ham)


def test_ke_hoach_cai_vong_409(du_lieu):
    """X cần Y, Y cần X → 409 mã vong_phu_thuoc, không treo."""
    async def ham(so):
        await cai_tu_thu_muc(so, du_lieu, "mod-x", can_module=["mod-y"])
        await cai_tu_thu_muc(so, du_lieu, "mod-y", can_module=["mod-x"])
        with pytest.raises(LoiSo) as e:
            so.ke_hoach_cai("mod-x")
        assert e.value.ma == 409
        assert e.value.code == "vong_phu_thuoc"
    chay(ham)


def test_ke_hoach_cai_ban_do_ngoai(du_lieu):
    """Phụ thuộc của module CHƯA cài lấy từ bản đồ ngoài (nguồn git)."""
    async def ham(so):
        await cai_tu_thu_muc(so, du_lieu, "mod-a", can_module=["mod-b"])
        # b chưa cài nhưng nguồn git biết b cần d
        kh = so.ke_hoach_cai("mod-a", ban_do_ngoai={"mod-b": ["mod-d"]})
        assert kh["ke_hoach"] == ["mod-d", "mod-b"]
    chay(ham)


# ───────────── cai_theo_ke_hoach: đúng thứ tự, dừng khi hỏng, chống giả ─────────────

def test_cai_theo_ke_hoach_dung_thu_tu(du_lieu):
    async def ham(so):
        await cai_tu_thu_muc(so, du_lieu, "mod-b", can_module=["mod-d"])
        await cai_tu_thu_muc(so, du_lieu, "mod-a", can_module=["mod-b", "mod-c"])
        goi = []

        async def ham_cai(dep):
            goi.append(dep)
            return {"ok": True, "id": dep}

        kq = await so.cai_theo_ke_hoach("mod-a", ["mod-d", "mod-c"], ham_cai)
        assert kq["ok"] is True
        assert goi == ["mod-d", "mod-c", "mod-a"]  # phụ thuộc trước, chính module cuối
    chay(ham)


def test_cai_theo_ke_hoach_ke_hoach_cu_bi_409(du_lieu):
    """Thân giả kế hoạch khác với tính lại → 409, chưa cài gì."""
    async def ham(so):
        await cai_tu_thu_muc(so, du_lieu, "mod-a", can_module=["mod-b"])
        goi = []

        async def ham_cai(dep):
            goi.append(dep)
            return {"ok": True}

        with pytest.raises(LoiSo) as e:
            await so.cai_theo_ke_hoach("mod-a", ["mod-b", "c-gia"], ham_cai)
        assert e.value.ma == 409
        assert e.value.code == "ke_hoach_cu"
        assert goi == []
    chay(ham)


def test_cai_theo_ke_hoach_dung_o_cai_hong(du_lieu):
    """Cài d hỏng → DỪNG, không cài lén tiếp; trả tiến độ đã cài."""
    async def ham(so):
        await cai_tu_thu_muc(so, du_lieu, "mod-b", can_module=["mod-d"])
        await cai_tu_thu_muc(so, du_lieu, "mod-a", can_module=["mod-b"])
        goi = []

        async def ham_cai(dep):
            goi.append(dep)
            if dep == "mod-d":
                raise LoiSo(400, "git không có module d")
            return {"ok": True}

        kq = await so.cai_theo_ke_hoach("mod-a", ["mod-d"], ham_cai)
        assert kq["ok"] is False
        assert kq["loi_o"] == "mod-d"
        assert "mod-b" not in goi and "mod-a" not in goi  # dừng hẳn, không cài tiếp
        assert kq["da_cai"] == []
    chay(ham)


def test_cai_theo_ke_hoach_bo_qua_da_cai(du_lieu):
    """Phụ thuộc đã có người cài trước lúc chạy → bỏ qua, không cài đè."""
    async def ham(so):
        await cai_tu_thu_muc(so, du_lieu, "mod-b")
        await cai_tu_thu_muc(so, du_lieu, "mod-a", can_module=["mod-b"])
        goi = []

        async def ham_cai(dep):
            goi.append(dep)
            return {"ok": True}

        kq = await so.cai_theo_ke_hoach("mod-a", [], ham_cai)
        assert kq["ok"] is True
        assert goi == ["mod-a"]
    chay(ham)


# ───────────── do_thi_phu_thuoc ─────────────

def test_do_thi_phu_thuoc_nut_canh_thu_tu(du_lieu):
    async def ham(so):
        await cai_tu_thu_muc(so, du_lieu, "vnc-xa")
        await cai_tu_thu_muc(so, du_lieu, "provider-a", can_module=["vnc-xa"])
        g = so.do_thi_phu_thuoc()
        nut = {n["id"]: n for n in g["nut"]}
        assert nut["provider-a"]["da_cai"] is True
        assert nut["vnc-xa"]["da_cai"] is True
        assert {"tu": "provider-a", "den": "vnc-xa"} in g["canh"]
        # thứ tự gợi ý: cái được cần đứng trước
        assert g["thu_tu_goi_y"].index("vnc-xa") < g["thu_tu_goi_y"].index("provider-a")
    chay(ham)


def test_do_thi_phu_thuoc_gom_id_la(du_lieu):
    """Id lạ trong can_module vẫn hiện nút (chưa cài) — không vỡ."""
    async def ham(so):
        await cai_tu_thu_muc(so, du_lieu, "provider-a", can_module=["id-la-xyz"])
        g = so.do_thi_phu_thuoc()
        nut = {n["id"]: n for n in g["nut"]}
        assert nut["id-la-xyz"]["da_cai"] is False
    chay(ham)


# ───────────── manifest thật: chatgpt-web khai may-dang-nhap ─────────────

def test_manifest_chatgpt_web_khai_may_dang_nhap():
    import pathlib
    goc = pathlib.Path(__file__).resolve().parent.parent
    m = json.loads((goc / "modules" / "chatgpt-web" / "manifest.json").read_text(encoding="utf-8"))
    assert "may-dang-nhap" in (m.get("can_module") or [])


# ───────────── HTTP ─────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    tep = {}
    for id_, cm in (("vnc-xa", "khong_khai"), ("mod-b", ["mod-d"]), ("mod-a", ["mod-b", "vnc-xa"])):
        for ten, noi_dung in tep_module(id_, can_module=cm).items():
            tep[f"{id_}/{ten}"] = noi_dung
    ghi_thu_muc(tmp_path / "modules", tep)
    with TestClient(app) as c:
        yield c


def test_http_ke_hoach_cai(client):
    r = client.get("/api/modules/mod-a/ke-hoach-cai", headers=QT)
    assert r.status_code == 200
    j = r.json()
    assert j["ke_hoach"] == ["mod-d"]
    assert j["da_du"] is False


def test_http_ke_hoach_cai_404(client):
    r = client.get("/api/modules/khong-co/ke-hoach-cai", headers=QT)
    assert r.status_code == 404


def test_http_ke_hoach_cai_401(client):
    r = client.get("/api/modules/mod-a/ke-hoach-cai")
    assert r.status_code == 401


def test_http_do_thi_phu_thuoc(client):
    r = client.get("/api/modules/do-thi-phu-thuoc", headers=QT)
    assert r.status_code == 200
    j = r.json()
    ids = {n["id"] for n in j["nut"]}
    assert {"mod-a", "mod-b", "vnc-xa", "mod-d"} <= ids
    assert {"tu": "mod-a", "den": "mod-b"} in j["canh"]
    assert j["thu_tu_goi_y"].index("mod-b") < j["thu_tu_goi_y"].index("mod-a")


def test_http_cai_theo_ke_hoach_ke_hoach_cu_409(client):
    r = client.post("/api/modules/cai-theo-ke-hoach", headers=QT,
                    json={"id_": "mod-a", "ke_hoach": ["sai"]})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ke_hoach_cu"


def test_http_cai_theo_ke_hoach_401(client):
    r = client.post("/api/modules/cai-theo-ke-hoach", json={"id_": "mod-a", "ke_hoach": []})
    assert r.status_code == 401
