"""Test NV220 — đo hợp đồng «lam_moi_model» qua lõi thật.

Máy chủ giả, không Internet. Ghi nhận hành vi ĐÃ CÓ của lõi NV102
(app/lam_moi_model.py) làm hồi quy cho quyết định thiết kế:
  - lỗi lấy model là lỗi TẠM: lõi chỉ ghi log, KHÔNG đánh dấu khoá/tài khoản hỏng;
  - module không có lam_moi_model (danh sách tĩnh) bị vòng lặp bỏ qua, không hỏng;
  - lượt làm mới lỗi không xoá model đã ghi trong sổ.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import kho, khoa_khach, lam_moi_model, so_module

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"


class ModuleGia:
    """Module giả: trạng thái khoá mô phỏng tài khoản thật."""

    def __init__(self, tien_to: str, models: list[dict], loi: Exception | None = None,
                 co_lam_moi: bool = True):
        self.manifest = {"id": "gia", "tien_to": tien_to}
        self.tien_to = tien_to
        self._models = [dict(m) for m in models]
        self._loi = loi
        self._co_lam_moi = co_lam_moi
        # Trạng thái «khoá» mô phỏng: lõi KHÔNG được đụng vào khi làm mới lỗi.
        self.trang_thai_khoa = {"k1": "hoat_dong", "k2": "hoat_dong"}

    async def models(self):
        return [dict(m) for m in self._models]

    async def lam_moi_model(self):
        if not self._co_lam_moi:
            raise AttributeError("không hỗ trợ")
        if self._loi is not None:
            raise self._loi


class ModuleTinh:
    """Module danh sách tĩnh: KHÔNG có khả năng lam_moi_model (lõi bỏ qua)."""

    def __init__(self, tien_to: str, models: list[dict]):
        self.manifest = {"id": "tinh", "tien_to": tien_to}
        self.tien_to = tien_to
        self._models = [dict(m) for m in models]

    async def models(self):
        return [dict(m) for m in self._models]


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    khoa_khach.lam_moi_cache()
    return data_dir


def _doc_so() -> dict:
    p = kho.thu_muc_du_lieu() / "model_so.json"
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _vong(mods: dict):
    return lam_moi_model.VongLamMoiModel(lambda: mods)


def test_1_loi_lam_moi_khong_danh_dau_khoa_hong(moi_truong):
    """Lỗi lấy model là lỗi TẠM: ket_qua='loi', khoá/tài khoản giữ nguyên."""
    mod = ModuleGia("gia", [{"id": "gia/m1", "nhan": "M1"}],
                    loi=RuntimeError("nguồn sập"))
    vong = _vong({"gia": mod})
    khoa_truoc = json.dumps(mod.trang_thai_khoa, sort_keys=True)

    import asyncio
    kq = asyncio.run(vong.lam_moi_mot("gia"))

    assert kq["ket_qua"] == "loi"
    # Trạng thái khoá không bị đụng: không đánh dấu hỏng.
    assert json.dumps(mod.trang_thai_khoa, sort_keys=True) == khoa_truoc
    # Sổ không ghi model nào cho module lỗi.
    assert "gia" not in _doc_so().get("modules", {})


def test_2_module_tinh_bi_bo_qua(moi_truong):
    """Module không có lam_moi_model: vòng lặp bỏ qua, sổ không có mục."""
    mod = ModuleTinh("tinh", [{"id": "tinh/m1", "nhan": "M1"}])
    vong = _vong({"tinh": mod})

    import asyncio
    kq = asyncio.run(vong.chay_mot_chu_ky())

    assert kq == {}
    assert "tinh" not in _doc_so().get("modules", {})


def test_3_luot_loi_khong_xoa_model_da_ghi(moi_truong):
    """Sổ đã có model; lượt làm mới lỗi KHÔNG xoá model cũ (rỗng = không thông tin)."""
    mod = ModuleGia("giu", [{"id": "giu/m1", "nhan": "M1"}])
    vong = _vong({"giu": mod})

    import asyncio
    kq1 = asyncio.run(vong.lam_moi_mot("giu"))
    assert kq1["ket_qua"] == "ok"
    assert _doc_so()["modules"]["giu"]["giu/m1"]["con"] is True

    mod._loi = RuntimeError("nguồn sập")
    kq2 = asyncio.run(vong.lam_moi_mot("giu"))
    assert kq2["ket_qua"] == "loi"
    # Model cũ vẫn còn, không bị đánh «không còn».
    muc = _doc_so()["modules"]["giu"]["giu/m1"]
    assert muc["con"] is True
    assert muc["vang"] == 0
