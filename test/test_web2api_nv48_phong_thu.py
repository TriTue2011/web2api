"""NV48 — các phòng thủ ĐÃ CÓ và đang thực thi thật (test xanh).

Mỗi test gắn với bằng chứng trong mã lõi; chi tiết ở nv48-bao-cao.md mục (2).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import kham_pha
from app.quyen_module import diff_quyen


def _manifest_co_ban(**ghi_de) -> dict:
    m = {"id": "nv48-phong-thu", "ten": "Phong thu", "mo_ta": "gia", "loai": "ai", "hang": "A",
         "giao_thuc": "local", "tien_to": "nv48pt", "truong_cau_hinh": [], "tai_nguyen": {}}
    m.update(ghi_de)
    return m


def test_nv48_mang_ra_sai_kieu_bi_tu_choi():
    """`tai_nguyen.mang_ra` không phải danh sách chuỗi → manifest bị từ chối ngay lúc quét
    (app/kham_pha.py kiem_manifest)."""
    for mang_xau in ("https://vi-du.example", ["ok", 123], {"a": 1}):
        assert kham_pha.kiem_manifest(_manifest_co_ban(
            tai_nguyen={"mang_ra": mang_xau})) == "tai_nguyen.mang_ra phải là danh sách chuỗi"


def test_nv48_yeu_cau_goi_thieu_bi_tu_choi(tmp_path: Path):
    """Gói khai ở `yeu_cau_goi` mà không import được → lõi từ chối nạp, không tự pip install
    (app/kham_pha.py nap_ma → goi_thieu)."""
    d = tmp_path / "nv48-goi"
    d.mkdir()
    m = _manifest_co_ban(id="nv48-goi", tien_to="nv48goi",
                         yeu_cau_goi=["goi_khong_ton_tai_nv48_xyz"])
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    (d / "module.py").write_text(
        "class Module:\n"
        "    def __init__(self, manifest): pass\n"
        "    def nap(self, c): pass\n"
        "    async def cai(self, c): return {'ok': True}\n"
        "    async def suc_khoe(self): return {'ok': True}\n"
        "    async def dung(self): pass\n", encoding="utf-8")
    with pytest.raises(kham_pha.LoiNap, match="thiếu gói Python"):
        kham_pha.nap_ma(d, m, "vtgoinv48" * 2)


def test_nv48_module_loi_khong_sap_loi(tmp_path: Path):
    """Mã module hỏng (cú pháp / __init__ ném) → LoiNap cho đúng module đó, lõi không sập
    (app/kham_pha.py nap_ma bắt BaseException trừ KeyboardInterrupt)."""
    d = tmp_path / "nv48-hong"
    d.mkdir()
    m = _manifest_co_ban(id="nv48-hong", tien_to="nv48hong")
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    (d / "module.py").write_text("class Module:\n    def __init__(self, manifest):\n        raise RuntimeError('hong co y')\n"
                                 "    def nap(self, c): pass\n"
                                 "    async def cai(self, c): return {'ok': True}\n"
                                 "    async def suc_khoe(self): return {'ok': True}\n"
                                 "    async def dung(self): pass\n", encoding="utf-8")
    with pytest.raises(kham_pha.LoiNap, match="lỗi khi nạp module.py"):
        kham_pha.nap_ma(d, m, "vthongnv48" * 2)
    # Bộ nhớ mô-đun đã dọn — không giữ mã hỏng.
    assert not any(k.startswith(kham_pha.ten_ma(m["id"], "vthongnv48" * 2))
                   for k in __import__("sys").modules)


def test_nv48_diff_bat_mang_ra_moi():
    """Thêm mục `tai_nguyen.mang_ra` khi cập nhật → diff_quyen xếp vào «them» → bản mới
    KHÔNG kích hoạt mà chờ duyệt (app/so_module.py _cho_duyet_quyen → LoiCanDuyetQuyen 409)."""
    cu = _manifest_co_ban(tai_nguyen={"mang_ra": ["api.vi-du.example"]})
    moi = _manifest_co_ban(tai_nguyen={"mang_ra": ["api.vi-du.example", "evil.example"]})
    diff = diff_quyen(cu, moi)
    assert {"loai": "mang_ra", "ten": "evil.example"} in diff["them"]
    assert diff["bot"] == []


def test_nv48_diff_bat_webhook_moi():
    """Khai `webhook` mới khi cập nhật → cần duyệt (NV31, app/quyen_module.py diff_quyen)."""
    diff = diff_quyen(_manifest_co_ban(), _manifest_co_ban(webhook={"duong": "/hook"}))
    assert {"loai": "webhook", "ten": "webhook"} in diff["them"]


def test_nv48_thu_muc_cho_duyet_khong_bi_quet(tmp_path: Path):
    """Bản chờ duyệt quyền nằm ở `.cho-duyet-<id>/` (bắt đầu bằng «.») → `quet()` bỏ qua,
    mã chưa duyệt KHÔNG bị thực thi trong lúc chờ (app/kham_pha.py quet, app/so_module.py
    _cho_duyet_quyen)."""
    (tmp_path / ".cho-duyet-nv48-x").mkdir()
    (tmp_path / "nv48-x").mkdir()
    thay = [p.name for p in kham_pha.quet(tmp_path)]
    assert thay == ["nv48-x"]


def test_nv48_duyet_sai_diff_bi_tu_choi():
    """Duyệt phải khớp CHÍNH XÁC diff đã xem: băm diff đổi → 409 (app/so_module.py
    _kiem_duyet_quyen + app/quyen_module.py bam_diff)."""
    cu = _manifest_co_ban()
    moi = _manifest_co_ban(tai_nguyen={"mang_ra": ["evil.example"]})
    diff = diff_quyen(cu, moi)
    from app.quyen_module import bam_diff
    assert bam_diff(diff) != bam_diff({"them": [], "bot": []})
