"""NV73 — phụ thuộc module: trường manifest `can_module` + đếm tham chiếu ở lõi.

Kịch bản đúng thiết kế NV67 đã chốt (việc 4, nhóm 2):
- cài provider mà cái cần (vd vnc-xa) chưa có → phản hồi ĐỀ NGHỊ cài (`can_module_thieu`),
  KHÔNG tự cài lén;
- gỡ module đang được module khác cần → 409 `module_dang_duoc_can` kèm danh sách;
- gỡ provider cuối cùng còn cần một module chung → phản hồi có cờ `hoi_giu_phu_thuoc`
  để hỏi quản trị giữ hay gỡ theo (lõi không tự quyết);
- lõi không nhắc tên module cụ thể: `can_module` là dữ liệu khai báo mở — id lạ hiện nguyên mã;
- thêm id vào `can_module` là thêm quyền → bản cập nhật phải qua cổng duyệt quyền.

Test chạy trên thư mục dữ liệu tạm, không mạng ngoài.
"""

import asyncio
import json
import shutil

import pytest
from fastapi.testclient import TestClient

from app import kho, so_module
from app.kham_pha import kiem_manifest
from app.main import app
from app.quyen_module import diff_quyen
from app.so_module import LoiModuleDangDuocCan, LoiSo, SoModule
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


def manifest_gia(id_, tien_to=None, can_module="khong_khai", **them):
    """Module giả tối thiểu đúng chuẩn. `can_module="khong_khai"` = không có trường này."""
    m = {
        "id": id_, "ten": f"Module {id_}", "mo_ta": "module giả cho test can_module (NV73)",
        "hang": "A", "loai": "ai", "giao_thuc": "openai", "tien_to": tien_to or id_,
        "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []},
    }
    if can_module != "khong_khai":
        m["can_module"] = can_module
    m.update(them)
    return m


def tep_module(id_, tien_to=None, can_module="khong_khai", **them):
    """Các tệp của MỘT module (ghi thẳng vào thư mục module)."""
    return {"manifest.json": json.dumps(manifest_gia(id_, tien_to, can_module, **them),
                                        ensure_ascii=False).encode(),
            "module.py": MODULE_PY.encode()}


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return tmp_path


def chay(ham):
    """Chạy `ham(so)` trong MỘT vòng sự kiện với sổ module mới."""
    async def _():
        so = SoModule()
        await so.khoi_dong()
        try:
            return await ham(so)
        finally:
            await so.dung_het()
    return asyncio.run(_())


async def cai_tu_thu_muc(so, du_lieu, id_, **kw):
    """Giả lập «cài từ git»: đặt thư mục tạm rồi gọi SoModule.cai_thu_muc."""
    tam = du_lieu / f".tam-{id_}"
    if tam.exists():
        shutil.rmtree(tam)
    ghi_thu_muc(tam, tep_module(id_, **kw))
    kho.thu_muc_modules().mkdir(parents=True, exist_ok=True)  # luồng thật tạo khi dựng thư mục tạm
    return await so.cai_thu_muc(id_, tam)


# ───────────── kiểm manifest ─────────────

def test_kiem_manifest_can_module_hop_le_va_tu_choi_sai():
    tot = manifest_gia("provider-a", can_module=["vnc-xa"])
    assert kiem_manifest(tot) is None
    assert kiem_manifest(manifest_gia("provider-a")) is None  # tuỳ chọn: vắng vẫn được
    assert kiem_manifest(manifest_gia("provider-a", can_module=[])) is None
    for xau in (["vnc-xa", "vnc-xa"],          # trùng
                "vnc-xa",                       # không phải danh sách
                ["VNC-XA"],                     # id phải chữ thường
                ["vnc xa"],                     # khoảng trắng
                [123],                          # không phải chuỗi
                [None],
                ["provider-a"]):                # tự tham chiếu chính mình
        m = manifest_gia("provider-a", can_module=xau)
        ly_do = kiem_manifest(m)
        assert ly_do and "can_module" in ly_do, xau


def test_nap_lai_tu_choi_module_can_module_sai(du_lieu):
    async def _():
        so = SoModule()
        await so.khoi_dong()
        try:
            ghi_thu_muc(kho.thu_muc_modules(), {"m-xau/manifest.json": json.dumps(
                manifest_gia("m-xau", can_module=["m-xau"]), ensure_ascii=False).encode(),
                "m-xau/module.py": MODULE_PY.encode()})
            kq = await so.nap_lai()
            assert "m-xau" not in so.catalog
            assert any("can_module" in x["ly_do"] for x in kq["loi"])
        finally:
            await so.dung_het()
    asyncio.run(_())


# ───────────── cài: đề nghị, không tự cài lén ─────────────

def test_cai_provider_thieu_dep_de_nghi_khong_tu_cai_len(du_lieu):
    async def ham(so):
        kq = await cai_tu_thu_muc(so, du_lieu, "provider-a", can_module=["vnc-xa"])
        assert kq["can_module_thieu"] == ["vnc-xa"]
        assert "provider-a" in so.catalog           # cài thành công
        assert "vnc-xa" not in so.catalog           # KHÔNG tự cài lén cái thiếu
        assert so.dang_duoc_can_boi("vnc-xa") == ["provider-a"]  # vẫn đếm được dù chưa cài
    chay(ham)


def test_cai_du_dep_thi_khong_thieu(du_lieu):
    async def ham(so):
        await cai_tu_thu_muc(so, du_lieu, "vnc-xa")
        kq = await cai_tu_thu_muc(so, du_lieu, "provider-a", can_module=["vnc-xa"])
        assert kq["can_module_thieu"] == []
        assert so.dang_duoc_can_boi("vnc-xa") == ["provider-a"]
    chay(ham)


def test_can_module_la_hien_nguyen_ma(du_lieu):
    """Module lạ khai `can_module` lạ: lõi không vỡ, hiện nguyên mã, đề nghị đúng id lạ."""
    async def ham(so):
        kq = await cai_tu_thu_muc(so, du_lieu, "provider-a", can_module=["id-la-xyz"])
        assert kq["can_module_thieu"] == ["id-la-xyz"]
        assert so.catalog["provider-a"].manifest["can_module"] == ["id-la-xyz"]
        assert so.dang_duoc_can_boi("id-la-xyz") == ["provider-a"]
    chay(ham)


def test_loi_chay_khong_module_nao(du_lieu):
    async def ham(so):
        assert so.catalog == {}
        assert so.dang_duoc_can_boi("vnc-xa") == []
        assert so.can_module_thieu("khong-co") == []
    chay(ham)


# ───────────── gỡ: 409 khi còn người cần; hỏi khi mồ côi ─────────────

def test_go_dep_khi_con_provider_can_bi_409(du_lieu):
    async def ham(so):
        await cai_tu_thu_muc(so, du_lieu, "vnc-xa")
        await cai_tu_thu_muc(so, du_lieu, "provider-a", can_module=["vnc-xa"])
        with pytest.raises(LoiModuleDangDuocCan) as e:
            await so.go("vnc-xa")
        assert e.value.ma == 409
        assert e.value.code == "module_dang_duoc_can"
        assert e.value.dung == ["provider-a"]
        assert "vnc-xa" in so.catalog  # chưa gỡ gì cả
    chay(ham)


def test_go_dep_voi_hai_provider_liet_ke_du(du_lieu):
    async def ham(so):
        await cai_tu_thu_muc(so, du_lieu, "vnc-xa")
        await cai_tu_thu_muc(so, du_lieu, "provider-a", can_module=["vnc-xa"])
        await cai_tu_thu_muc(so, du_lieu, "provider-b", can_module=["vnc-xa"])
        with pytest.raises(LoiModuleDangDuocCan) as e:
            await so.go("vnc-xa")
        assert e.value.dung == ["provider-a", "provider-b"]
    chay(ham)


def test_go_provider_cuoi_hoi_giu_dep_mo_coi(du_lieu):
    """Gỡ provider cuối cùng: lõi trả cờ hỏi giữ hay gỡ vnc-xa — không tự quyết."""
    async def ham(so):
        await cai_tu_thu_muc(so, du_lieu, "vnc-xa")
        await cai_tu_thu_muc(so, du_lieu, "provider-a", can_module=["vnc-xa"])
        kq = await so.go("provider-a")
        assert kq["ok"] and kq["hoi_giu_phu_thuoc"] == ["vnc-xa"]
        assert "vnc-xa" in so.catalog  # lõi KHÔNG tự gỡ theo
        # Quản trị chọn gỡ theo → lúc này không còn ai cần nên được gỡ.
        kq2 = await so.go("vnc-xa")
        assert kq2["ok"] and kq2["hoi_giu_phu_thuoc"] == []
        assert "vnc-xa" not in so.catalog
    chay(ham)


def test_go_provider_con_lai_khong_hoi(du_lieu):
    """Còn provider khác đang cần thì gỡ một provider không sinh cờ hỏi."""
    async def ham(so):
        await cai_tu_thu_muc(so, du_lieu, "vnc-xa")
        await cai_tu_thu_muc(so, du_lieu, "provider-a", can_module=["vnc-xa"])
        await cai_tu_thu_muc(so, du_lieu, "provider-b", can_module=["vnc-xa"])
        kq = await so.go("provider-a")
        assert kq["ok"] and kq["hoi_giu_phu_thuoc"] == []
        assert "vnc-xa" in so.catalog
    chay(ham)


def test_go_module_khong_lien_quan_binh_thuong(du_lieu):
    async def ham(so):
        await cai_tu_thu_muc(so, du_lieu, "vnc-xa")
        await cai_tu_thu_muc(so, du_lieu, "doc-lap")
        kq = await so.go("doc-lap")
        assert kq["ok"] and kq["hoi_giu_phu_thuoc"] == []
        with pytest.raises(LoiSo) as e:
            await so.go("khong-co")
        assert e.value.ma == 404  # đổi id lạ → 404, không lộ gì
    chay(ham)


# ───────────── duyệt quyền: thêm can_module là thêm quyền ─────────────

def test_diff_quyen_can_module_them_can_duyet_bot_thi_tu_do():
    them = diff_quyen(None, {"can_module": ["vnc-xa"]})
    assert {"loai": "can_module", "ten": "vnc-xa"} in them["them"]
    bot = diff_quyen({"can_module": ["vnc-xa"]}, {})
    assert bot["them"] == []  # bớt phụ thuộc = bớt quyền → qua tự do
    assert {"loai": "can_module", "ten": "vnc-xa"} in bot["bot"]
    giu = diff_quyen({"can_module": ["vnc-xa"]}, {"can_module": ["vnc-xa"]})
    assert giu == {"them": [], "bot": []}


# ───────────── HTTP: đường đã có, hành vi mới ─────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    tep = {}
    for id_, cm in (("vnc-xa", "khong_khai"), ("provider-a", ["vnc-xa"])):
        for ten, noi_dung in tep_module(id_, can_module=cm).items():
            tep[f"{id_}/{ten}"] = noi_dung
    ghi_thu_muc(tmp_path / "modules", tep)
    # `with` để chạy lifespan: sổ module (app.state.so) dựng lúc khởi động, quét sẵn 2 module.
    with TestClient(app) as c:
        yield c


def test_http_delete_dep_bi_409_co_cau_truc(client):
    r = client.delete("/api/modules/vnc-xa", headers=QT)
    assert r.status_code == 409
    j = r.json()
    assert j["ma"] == "module_dang_duoc_can" and j["dang_duoc_can_boi"] == ["provider-a"]


def test_http_delete_provider_cuoi_tra_co_hoi_giu(client):
    r = client.delete("/api/modules/provider-a", headers=QT)
    assert r.status_code == 200
    assert r.json()["hoi_giu_phu_thuoc"] == ["vnc-xa"]
    r = client.delete("/api/modules/vnc-xa", headers=QT)
    assert r.status_code == 200
    assert r.json()["hoi_giu_phu_thuoc"] == []


def test_http_delete_id_la_404_khong_lo(client):
    r = client.delete("/api/modules/khong-co-module-nay", headers=QT)
    assert r.status_code == 404
    assert "vnc-xa" not in r.text and "provider-a" not in r.text


def test_http_delete_khong_khoa_401(client):
    r = client.delete("/api/modules/vnc-xa")
    assert r.status_code == 401


def test_http_catalog_va_ds_hien_can_module(client):
    cat = {m["id"]: m for m in client.get("/api/modules/catalog", headers=QT).json()["modules"]}
    assert cat["provider-a"]["can_module"] == ["vnc-xa"]
    assert cat["vnc-xa"]["can_module"] is None  # không khai → None như các trường tuỳ chọn khác
    ds = {m["id"]: m for m in client.get("/api/modules", headers=QT).json()["modules"]}
    assert ds["provider-a"]["can_module"] == ["vnc-xa"]
    assert ds["vnc-xa"]["can_module"] == []
