"""Test NV110 — sửa hai phát hiện mức thấp của lượt soát NV109.

NV109-01: loc_danh_sach_model / cap_nhat_so nhận id mang tiền tố của module khác
  → lọc theo nguyên tắc không gian tên: dấu '/' trong id là tuyên bố không gian tên,
  chỉ chấp nhận khi khớp đúng tien_to của chính module đang làm mới.

NV109-02: PUT /api/modules/{id}/model không giới hạn số mục mac_dinh
  → trần = số nhóm model thật của module + biên cố định; vượt → 400 invalid_value;
  nhóm lạ bị lọc như tat (vào bo_qua).

Chuỗi khoá trong test là chuỗi GIẢ.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import kho, khoa_khach, so_module
from app.main import app

KHOA_QT = "khoa-quan-tri-test-nv110-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


def _tao_module_gia(goc: Path, id_: str, models: list[dict], tien_to: str | None = None) -> Path:
    """Module giả tối thiểu: nap/cai/chat/models/suc_khoe/dung."""
    thu_muc = goc / id_
    thu_muc.mkdir(parents=True, exist_ok=True)
    tt = tien_to if tien_to is not None else id_
    manifest = {
        "id": id_, "ten": f"Module {id_}", "mo_ta": "Module gia NV110",
        "phien_ban": "1.0.0", "hang": "A", "loai": "ai", "giao_thuc": "openai",
        "tien_to": tt, "cung_cap": ["chat"], "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []},
    }
    (thu_muc / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (thu_muc / "module.py").write_text(
        "class Module:\n"
        "    def __init__(self, m):\n"
        "        self.manifest = m\n"
        "        self.tien_to = m['tien_to']\n"
        f"        self.raw_models = {models!r}\n"
        "    def nap(self, c): pass\n"
        "    async def cai(self, c): return {'ok': True, 'cau_hinh_luu': c}\n"
        "    async def chat(self, b, s): return 200, {}, b'{}'\n"
        "    async def models(self): return [dict(x) for x in self.raw_models]\n"
        "    async def suc_khoe(self): return {'ok': True}\n"
        "    async def dung(self): pass\n",
        encoding="utf-8",
    )
    return thu_muc


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    monkeypatch.delenv("WEB2API_QT_IP_CHO_PHEP", raising=False)
    monkeypatch.delenv("WEB2API_TRUST_PROXY", raising=False)
    modules_dir = data_dir / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    khoa_khach.lam_moi_cache()
    return data_dir


def _cai_module(c, id_: str):
    r = c.post(f"/api/modules/{id_}/cai", headers=QT, json={"cau_hinh": {}})
    assert r.status_code == 200, r.text
    return r


# ── NV109-01: kiểm tiền tố không gian tên ────────────────────────────────────


def test_nv110_01_tien_to_khac_bi_loai_khong_ngu_canh():
    """Không có tien_to để đối chiếu: id 'mod-b/m1' tuyên bố không gian tên
    không kiểm chứng được → bị loại (kịch bản đúng của test xfail NV109-01)."""
    from app import lam_moi_model as lmm
    ds = lmm.loc_danh_sach_model([{"id": "mod-b/m1", "nhan": "Gia"}])
    assert ds == []


def test_nv110_01_tien_to_dung_duoc_giu():
    """Có tien_to: id mang đúng tiền tố của chính module được giữ."""
    from app import lam_moi_model as lmm
    ds = lmm.loc_danh_sach_model(
        [{"id": "mod-a/m1", "nhan": "A"}, {"id": "mod-a/m2", "nhan": "B"}],
        tien_to="mod-a",
    )
    assert [m["id"] for m in ds] == ["mod-a/m1", "mod-a/m2"]


def test_nv110_01_tien_to_sai_bi_loai_co_ngu_canh():
    """Có tien_to: id mang tiền tố module KHÁC bị loại; id không tiền tố
    (dữ liệu cũ, không tuyên bố không gian tên) vẫn giữ — không làm hỏng."""
    from app import lam_moi_model as lmm
    ds = lmm.loc_danh_sach_model(
        [
            {"id": "mod-b/m1", "nhan": "Gia"},
            {"id": "mod-a/m1", "nhan": "That"},
            {"id": "m1", "nhan": "Cu"},
        ],
        tien_to="mod-a",
    )
    assert [m["id"] for m in ds] == ["mod-a/m1", "m1"]


def test_nv110_01_khong_in_id_tho_vao_log(caplog):
    """Id bị loại chỉ ghi mã ngắn (tiền tố tuyên bố), không in id thô vào log."""
    from app import lam_moi_model as lmm
    with caplog.at_level(logging.WARNING, logger="web2api.lam_moi_model"):
        ds = lmm.loc_danh_sach_model([{"id": "mod-b/m1", "nhan": "Gia"}])
    assert ds == []
    ban_ghi = [r for r in caplog.records if "mod-b" in r.getMessage()]
    assert ban_ghi, "phải có log khi loại id tiền tố lạ"
    for r in caplog.records:
        assert "mod-b/m1" not in r.getMessage(), "lộ id thô trong log"


def test_nv110_01_lam_moi_mot_truyen_tien_to_manifest(moi_truong):
    """Luồng thật lam_moi_mot: id tiền tố lạ không vào sổ của module."""
    from app import lam_moi_model as lmm

    class ModGia110:
        def __init__(self):
            self.manifest = {"id": "mod-110", "tien_to": "mod-110"}

        async def lam_moi_model(self):
            pass

        async def models(self):
            return [
                {"id": "mod-110/a", "nhan": "A"},
                {"id": "mod-khac/b", "nhan": "B"},
            ]

    vong = lmm.VongLamMoiModel(lambda: {"mod-110": ModGia110()})
    kq = asyncio.run(vong.lam_moi_mot("mod-110"))
    assert kq["ket_qua"] == "ok"
    assert kq["moi"] == ["mod-110/a"]
    so = lmm._doc_so()["modules"]["mod-110"]
    assert set(so) == {"mod-110/a"}


# ── NV109-02: trần số mục mac_dinh ───────────────────────────────────────────


def _dung_module_2_nhom(moi_truong):
    _tao_module_gia(
        moi_truong / "modules", "mod-110-tran",
        models=[
            {"id": "m1", "kha_nang": ["chat"]},
            {"id": "m2", "kha_nang": ["anh"]},
        ],
    )


def test_nv110_02_mac_dinh_vuot_tran_400(moi_truong):
    """Module có 2 nhóm thật, trần = 2 + biên: 8 mục → 400 invalid_value,
    và model.json không bị ghi đè."""
    from app import tuyen
    _dung_module_2_nhom(moi_truong)
    tran = 2 + tuyen.TRAN_THEM_MAC_DINH
    with TestClient(app) as c:
        _cai_module(c, "mod-110-tran")
        md = {f"nhom-gia-{i}": "m1" for i in range(tran + 1)}
        r = c.put("/api/modules/mod-110-tran/model", headers=QT,
                  json={"tat": [], "mac_dinh": md})
        assert r.status_code == 400, r.status_code
        assert r.json()["error"]["code"] == "invalid_value"
        # sổ không bị phình
        assert kho.doc_cai_dat_model_module("mod-110-tran")["mac_dinh"] == {}


def test_nv110_02_mac_dinh_dung_bien_200(moi_truong):
    """Đúng trần (2 nhóm thật + biên): 200; nhóm lạ bị lọc vào bo_qua như tat,
    không lưu vào sổ."""
    from app import tuyen
    _dung_module_2_nhom(moi_truong)
    tran = 2 + tuyen.TRAN_THEM_MAC_DINH
    md = {"chat": "m1", "anh": "m2"}
    md.update({f"nhom-la-{i}": "m1" for i in range(tran - 2)})
    assert len(md) == tran
    with TestClient(app) as c:
        _cai_module(c, "mod-110-tran")
        r = c.put("/api/modules/mod-110-tran/model", headers=QT,
                  json={"tat": [], "mac_dinh": md})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["mac_dinh"] == {"chat": "m1", "anh": "m2"}
        for i in range(tran - 2):
            assert f"nhom-la-{i}" in j["bo_qua"]
        doc = kho.doc_cai_dat_model_module("mod-110-tran")
        assert doc["mac_dinh"] == {"chat": "m1", "anh": "m2"}


def test_nv110_02_du_lieu_hop_le_giu_nguyen(moi_truong):
    """Dữ liệu hợp lệ: hành vi cũ giữ nguyên (200, lưu đủ)."""
    _dung_module_2_nhom(moi_truong)
    with TestClient(app) as c:
        _cai_module(c, "mod-110-tran")
        r = c.put("/api/modules/mod-110-tran/model", headers=QT,
                  json={"tat": [], "mac_dinh": {"chat": "m1", "anh": "m2"}})
        assert r.status_code == 200, r.text
        assert r.json()["mac_dinh"] == {"chat": "m1", "anh": "m2"}
        # GET lại thấy đủ
        j = c.get("/api/modules/mod-110-tran/model", headers=QT).json()
        assert j["mac_dinh"] == {"chat": "m1", "anh": "m2"}
