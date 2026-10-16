# NV15(1): bool trong `tai_nguyen` (vd `gpu`) — chỉ false→true mới là quyền thêm.
# Khuôn theo NV9 (số tài nguyên chỉ tăng mới báo): hạ quyền (true→false) được qua tự do.
from __future__ import annotations

from app.quyen_module import diff_quyen


def _mf(tai_nguyen: dict) -> dict:
    return {"id": "m", "ten": "M", "tai_nguyen": tai_nguyen}


def test_gpu_true_sang_false_khong_can_duyet():
    """Hạ quyền gpu true→false: không vào «them», không cần duyệt."""
    cu = _mf({"gpu": True})
    moi = _mf({"gpu": False})
    assert diff_quyen(cu, moi) == {"them": [], "bot": []}


def test_gpu_false_sang_true_can_duyet():
    """Tăng quyền gpu false→true: vào «them», cần duyệt."""
    cu = _mf({"gpu": False})
    moi = _mf({"gpu": True})
    d = diff_quyen(cu, moi)
    assert d["them"] == [{"loai": "tai_nguyen", "ten": "gpu"}]
    assert d["bot"] == []


def test_gpu_thieu_sang_true_can_duyet():
    """Thêm mới gpu=true (trước đó không có): vào «them»."""
    cu = _mf({})
    moi = _mf({"gpu": True})
    d = diff_quyen(cu, moi)
    assert d["them"] == [{"loai": "tai_nguyen", "ten": "gpu"}]


def test_gpu_true_sang_thieu_khong_can_duyet():
    """Gỡ gpu (true→thiếu): không cần duyệt."""
    cu = _mf({"gpu": True})
    moi = _mf({})
    assert diff_quyen(cu, moi) == {"them": [], "bot": []}


def test_gpu_giu_nguyen_true_khong_can_duyet():
    cu = _mf({"gpu": True})
    moi = _mf({"gpu": True})
    assert diff_quyen(cu, moi) == {"them": [], "bot": []}


def test_bool_khac_cung_quy_tac():
    """Bool khác trong tai_nguyen cũng chỉ false→true mới báo."""
    cu = _mf({"tpm": True, "usb": False})
    moi = _mf({"tpm": False, "usb": True})
    d = diff_quyen(cu, moi)
    assert d["them"] == [{"loai": "tai_nguyen", "ten": "usb"}]
    assert d["bot"] == []
