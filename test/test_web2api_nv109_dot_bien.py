"""NV109-B — test lấp khoảng hở phát hiện bằng đột biến.

Mỗi test ở đây khẳng định một tính chất an toàn của mã NV102 / NV105 / NV103
mà bộ test hiện có chưa bao phủ đủ chặt: test XANH trên mã thật, ĐỎ khi áp
đột biến tương ứng (đã kiểm từng đột biến bằng tay — xem nv109-bao-cao.md).

KHÔNG sửa mã sản phẩm. Chuỗi trong test là chuỗi GIẢ rõ ràng.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


# ── app/lam_moi_model.py ──────────────────────────────────────────────


def test_nv109b_chu_ky_gio_kep_trong_1_24(monkeypatch):
    """Đột biến: bỏ kẹp min/max ở chu_ky_gio → chu kỳ 0.1h/100h lọt (DoS ngược/
    treo). Mã thật kẹp trong [1, 24]."""
    from app import lam_moi_model as lmm
    monkeypatch.setenv("WEB2API_CHU_KY_MODEL_GIO", "0.1")
    assert lmm.chu_ky_gio() == 1.0
    monkeypatch.setenv("WEB2API_CHU_KY_MODEL_GIO", "100")
    assert lmm.chu_ky_gio() == 24.0
    monkeypatch.setenv("WEB2API_CHU_KY_MODEL_GIO", "3")
    assert lmm.chu_ky_gio() == 3.0


def test_nv109b_chu_ky_gio_so_xau_ve_mac_dinh(monkeypatch):
    """Đột biến: bỏ nhánh NaN/inf/không-phải-số → chu kỳ thành NaN (vòng ngủ
    hỏng). Mã thật trả về mặc định 3.0."""
    from app import lam_moi_model as lmm
    for xau in ("NaN", "inf", "-inf", "abc", "1,5"):
        monkeypatch.setenv("WEB2API_CHU_KY_MODEL_GIO", xau)
        assert lmm.chu_ky_gio() == 3.0, xau
    monkeypatch.delenv("WEB2API_CHU_KY_MODEL_GIO", raising=False)
    assert lmm.chu_ky_gio() == 3.0


def test_nv109b_loc_danh_sach_cat_500():
    """Đột biến: bỏ cắt TOI_DA_MODEL → upstream trả 10k model làm phình sổ.
    Mã thật cắt còn 500."""
    from app import lam_moi_model as lmm
    ds = lmm.loc_danh_sach_model([{"id": f"m{i}", "nhan": "X"} for i in range(600)])
    assert len(ds) == 500
    assert ds[0]["id"] == "m0" and ds[-1]["id"] == "m499"


def test_nv109b_chuan_hoa_ma_tu_choi_khong_phai_chuoi():
    """Đột biến: bỏ kiểm isinstance str → số/None lọt vào sổ gây lỗi sau này."""
    from app import lam_moi_model as lmm
    assert lmm.chuan_hoa_ma_model(None) is None
    assert lmm.chuan_hoa_ma_model(123) is None
    assert lmm.chuan_hoa_ma_model(["m1"]) is None
    assert lmm.chuan_hoa_nhan(None) is None
    assert lmm.chuan_hoa_nhan(123) is None


def test_nv109b_loc_danh_sach_bo_muc_khong_phai_dict():
    """Đột biến: bỏ kiểm isinstance dict trong loc_danh_sach_model → mục số/
    chuỗi gây AttributeError khi .get. Mã thật bỏ qua êm."""
    from app import lam_moi_model as lmm
    ds = lmm.loc_danh_sach_model(["m1", 42, None, {"id": "ok"}])
    assert [m["id"] for m in ds] == ["ok"]
    assert lmm.loc_danh_sach_model("khong-phai-list") == []
    assert lmm.loc_danh_sach_model(None) == []


# ── app/model_bo_sung.py ─────────────────────────────────────────────


def test_nv109b_ghi_tep_quyen_0600(tmp_path, monkeypatch):
    """Đột biến: bỏ quyen=0o600 ở ghi() → tệp model bổ sung thành 0644 (lộ
    cho user khác trên máy dùng chung)."""
    from app import kho, model_bo_sung
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    model_bo_sung.ghi("mod-q", [{"id": "m1"}])
    p = model_bo_sung.duong_tep("mod-q")
    assert p.is_file()
    assert oct(p.stat().st_mode & 0o777) == "0o600", oct(p.stat().st_mode & 0o777)


def test_nv109b_thu_muc_bo_sung_0700(tmp_path, monkeypatch):
    """Đột biến: bỏ chmod 0700 thư mục model_bo_sung."""
    from app import model_bo_sung
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    model_bo_sung.ghi("mod-q2", [{"id": "m1"}])
    d = (tmp_path / "data" / "model_bo_sung")
    assert d.is_dir()
    assert oct(d.stat().st_mode & 0o777) == "0o700", oct(d.stat().st_mode & 0o777)


def test_nv109b_kiem_tu_choi_id_khoang_trang_ven():
    """Đột biến: bỏ kiểm `id_chuan != id_raw` → id ' m1 ' (kèm trắng) được
    chấp nhận, gây lệch khi so khớp/tắt model."""
    from app import model_bo_sung
    with pytest.raises(ValueError):
        model_bo_sung.kiem({"models": [{"id": " m1 "}]})
    with pytest.raises(ValueError):
        model_bo_sung.kiem({"models": [{"id": "m1\n"}]})
    # id đã chuẩn thì qua
    assert model_bo_sung.kiem({"models": [{"id": "m1"}]}) == [{"id": "m1"}]


def test_nv109b_kiem_tu_choi_khoa_thua_va_thieu_id():
    """Đột biến: nới kiểm khoá (cho thêm khoá lạ / thiếu id) → dữ liệu bẩn
    vào tệp."""
    from app import model_bo_sung
    with pytest.raises(ValueError):
        model_bo_sung.kiem({"models": [{"id": "m1", "gia": 1}]})
    with pytest.raises(ValueError):
        model_bo_sung.kiem({"models": [{"nhan": "X"}]})
    with pytest.raises(ValueError):
        model_bo_sung.kiem({"models": [{"id": "m1"}], "thua": 1})
    with pytest.raises(ValueError):
        model_bo_sung.kiem([{"id": "m1"}])


def test_nv109b_kiem_nhan_khong_phai_chuoi_bi_chan():
    """Đột biến: bỏ kiểm isinstance str của nhan → số/dict vào tệp."""
    from app import model_bo_sung
    with pytest.raises(ValueError):
        model_bo_sung.kiem({"models": [{"id": "m1", "nhan": 42}]})
    with pytest.raises(ValueError):
        model_bo_sung.kiem({"models": [{"id": "m1", "nhan": {"x": 1}}]})


# ── app/quyen_module.py :: tinh_hang (NV105) ──────────────────────────


def test_nv109b_tinh_hang_kha_nang_la_thi_b():
    """Đột biến: bỏ kiểm `kn not in _KHA_NANG_AN_TOAN` (mặc-định-mở) → khả năng
    lạ được hạng A. Mã thật: lạ → B."""
    from app.quyen_module import tinh_hang
    assert tinh_hang({"cung_cap": ["kha-nang-hoan-toan-moi-xyz"]}) == "B"
    assert tinh_hang({"cung_cap": ["chat"]}) == "A"


def test_nv109b_tinh_hang_webhook_va_nhan_cookie_thi_b():
    """Đột biến: bỏ kiểm webhook / nhan_cookie."""
    from app.quyen_module import tinh_hang
    assert tinh_hang({"cung_cap": [], "webhook": {"duong": "/x"}}) == "B"
    assert tinh_hang({"cung_cap": [],
                      "truong_cau_hinh": [{"ten": "t", "nhan_cookie": True}]}) == "B"


def test_nv109b_tinh_hang_sai_kieu_dong_khi_loi():
    """Đột biến: manifest sai kiểu → "A" (mở khi lỗi). Mã thật: "B"."""
    from app.quyen_module import tinh_hang
    assert tinh_hang("chuoi") == "B"
    assert tinh_hang(["list"]) == "B"
    assert tinh_hang(42) == "B"
    assert tinh_hang(None) == "A"
    assert tinh_hang({}) == "A"


# ── modules/muse2api/module.py :: luật hatch_sess (NV103) ─────────────


def test_nv109b_luat_hatch_sess_nguyen_ven():
    """Đột biến: xoá/sửa luật cookie hatch_sess → tín hiệu da_dang_nhap mất,
    tai_dung_ho_so liệt. Khẳng định toàn bộ trường của luật."""
    from modules.muse2api.module import _QUY_TAC_TIN_HIEU
    luat = [q for q in _QUY_TAC_TIN_HIEU
            if q.get("loai") == "cookie" and q.get("ten") == "hatch_sess"]
    assert len(luat) == 1, _QUY_TAC_TIN_HIEU
    q = luat[0]
    assert q["mien"] == "muse.ai", q
    assert q["tin_hieu"] == "da_dang_nhap", q


def test_nv109b_lmm8_nhan_loc_ky_tu_dieu_khien():
    """Giết đột biến LMM8 (bỏ lọc ký tự điều khiển trong chuan_hoa_nhan):
    nhãn chứa \\n/\\x00 phải được lọc, không lọt vào sổ/log."""
    from app import lam_moi_model as lmm
    assert lmm.chuan_hoa_nhan("a\nb") == "ab"
    assert lmm.chuan_hoa_nhan("x\x00y") == "xy"
    assert "\n" not in (lmm.chuan_hoa_nhan("m\n") or "")
