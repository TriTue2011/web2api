"""NV185 — `lay_khoa_chinh()` không được ghi nhật ký khi còn giữ khoá (tự khoá chết).

Lớp lỗi (đo khi chạy bộ test đầy đủ: treo vô hạn, futex_wait): `lay_khoa_chinh` (chưa có khoá chính) giữ `_KHOA_LUONG` rồi gọi
`nhat_ky.ghi` → nhật ký che bí mật → `xac_thuc.khoa_quan_tri()` → đọc tệp `admin_key` dạng rõ → `lay_khoa_chinh()` (để di trú) → chờ chính
`_KHOA_LUONG` đang giữ. Gặp ở cấu hình mặc định: chưa bật mã hoá khoá + admin_key còn dạng rõ. Nguyên tắc: không gọi mã ngoài khi còn giữ
một khoá không tái nhập.
"""

import threading

import pytest

from app import ham_khoa


@pytest.fixture
def du_lieu_chua_co_khoa_chinh(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "admin_key").write_text("khoa-quan-tri-dang-ro-0000000000000000", encoding="utf-8")
    (data / "admin_key").chmod(0o600)
    monkeypatch.setenv("WEB2API_DATA", str(data))
    monkeypatch.delenv("WEB2API_ADMIN_KEY", raising=False)
    monkeypatch.delenv("WEB2API_KHOA_CHINH", raising=False)
    monkeypatch.delenv("WEB2API_KHOA_CHINH_TEP", raising=False)
    monkeypatch.delenv("WEB2API_BAT_BUOC_MA_HOA", raising=False)
    monkeypatch.setattr(ham_khoa, "_DA_CANH_BAO_KHONG_KHOA", False)
    return data


def test_chua_co_khoa_chinh_va_admin_key_dang_ro_khong_tu_khoa_chet(du_lieu_chua_co_khoa_chinh):
    ket_qua = []
    luong = threading.Thread(target=lambda: ket_qua.append(ham_khoa.lay_khoa_chinh()), daemon=True)
    luong.start()
    luong.join(timeout=10)
    assert not luong.is_alive(), "lay_khoa_chinh() treo: ghi nhật ký khi còn giữ _KHOA_LUONG rồi tái nhập chính nó"
    assert ket_qua == [None]


def test_canh_bao_chi_ghi_mot_lan(du_lieu_chua_co_khoa_chinh, monkeypatch):
    from app import nhat_ky
    da_ghi = []
    goc = nhat_ky.ghi
    monkeypatch.setattr(nhat_ky, "ghi", lambda *a, **k: (da_ghi.append(a), goc(*a, **k))[1])
    for _ in range(3):
        ham_khoa.lay_khoa_chinh()
    assert sum("Chưa cấu hình khoá chính" in str(a) for a in da_ghi) == 1
