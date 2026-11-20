"""NV305-bao-mat — Quy tắc xác thực lại + hiển thị sau xác thực + công tắc noVNC
cho tab «Bảo mật & người dùng» (phương án mới 19:19 ngày 03/11 + chốt 19:26).

Quy tắc thi hành (test khóa):
(a) Xác thực lại cho thao tác tạo/xoá trong tab: chưa bật 2FA → nhập lại mật khẩu
    admin; đã bật 2FA → mã 2FA THAY mật khẩu (mật khẩu bị từ chối khi đã bật 2FA,
    kể cả đổi mật khẩu). Khoá quản trị Bearer vẫn được miễn như mọi đường chạm bí
    mật khác (đó là bí mật gốc, không phải mật khẩu) — ghi rõ, không giấu.
(b) Hiển thị sau xác thực lại: mật khẩu noVNC sau khi đặt thì ẩn; chỉ hiển thị lại
    qua endpoint hiển thị với bằng chứng TƯƠI trong body (mật khẩu khi chưa bật 2FA,
    CHỈ mã 2FA khi đã bật 2FA — không miễn Bearer, không dùng cửa sổ step-up).
    Thay đổi này thay thiết kế never-return của NV305 (đã chốt 19:26, không ngoại lệ).
    Khoá khách + mã khôi phục 2FA vẫn hash-only / hiện một lần nên KHÔNG hiển thị
    lại được theo thiết kế — test khẳng định danh sách không lộ chúng.
(c) Công tắc noVNC trong web UI (tab Bảo mật) chịu cùng quy tắc xác thực lại ở (a).
(d) Cách ly: endpoint chỉ quản trị, kiểm tra phía server, không tin client.

Test dùng mật khẩu/mã 2FA tự tạo, module giả, không gọi dịch vụ thật.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import gioi_han, nguoi_dung, novnc, xac_thuc_2fa
from app.main import app
from app.so_module import SoModule

GOC = Path(__file__).resolve().parents[1]
HTML_BAO_MAT = (GOC / "web" / "He-thong-Bao-mat.dc.html").read_text(encoding="utf-8")
KHUNG_JS = (GOC / "web" / "khung.js").read_text(encoding="utf-8")
SUPPORT_JS = (GOC / "web" / "support.js").read_text(encoding="utf-8")
KHOA_QT = "khoa-quan-tri-nv305-bao-mat-123456"
MODULE = "may-dang-nhap"
MK_ADMIN = "MatKhauAdminBm000000000000"
MK_MOI = "MatKhauMoiBm000000000000"
MK_VNC_TEST = "VncBm01"  # 7 ký tự, chỉ để test, không phải mật khẩu thật


class ModBaoMatGia:
    """Module may-dang-nhap giả: ghi lại lời gọi, không gọi mạng, không lưu gì."""

    def __init__(self, mat_khau_vnc=MK_VNC_TEST, da_dat=True):
        self.mat_khau_vnc = mat_khau_vnc
        self.da_dat = da_dat
        self.hien_goi = 0
        self.bat_tat_goi = []

    async def dat_mat_khau_vnc(self, mat_khau: str) -> dict:
        return {"ok": True, "da_nap_lai": True}

    async def trang_thai_mat_khau_vnc(self) -> dict:
        return {"ok": True, "da_dat": self.da_dat}

    async def hien_mat_khau_vnc(self) -> dict:
        self.hien_goi += 1
        return {"ok": True, "mat_khau": self.mat_khau_vnc}

    async def bat_tat_novnc(self, bat: bool) -> dict:
        self.bat_tat_goi.append(bat)
        return {"ok": True, "bat": bat, "dang_chay": bat, "da_ap_dung": True}

    async def trang_thai_novnc(self) -> dict:
        return {"ok": True, "bat": False, "dang_chay": False,
                "x11vnc": False, "websockify": False, "da_dat": self.da_dat}


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "1000")
    monkeypatch.setenv("WEB2API_RL_KHOA_GIAY", "60")
    monkeypatch.setenv("WEB2API_DANG_NHAP_SAI_TOI_DA", "100")
    monkeypatch.setenv("WEB2API_DANG_NHAP_KHOA_PHUT", "15")
    gioi_han.khoi_dong()
    novnc._GIOI_HAN_MK_VNC.clear()
    with TestClient(app) as client:
        client.app.state.so = SoModule()
        mod = ModBaoMatGia()
        client.app.state.so.dang_chay[MODULE] = mod
        yield client, mod
    novnc._GIOI_HAN_MK_VNC.clear()


@pytest.fixture
def dong_ho(monkeypatch):
    """Đồng hồ giả cho TOTP: ma() sinh mã ở bước hiện tại, ma_moi() nhảy +31 giây
    rồi sinh mã mới (mỗi lần xác minh 2FA thành công đều cần bước mới vì chống phát lại)."""
    t = [1700000000.0]
    monkeypatch.setattr(time, "time", lambda: t[0])

    class DongHo:
        def ma(self, bi_mat):
            return xac_thuc_2fa.sinh_totp(bi_mat, moc_thoi_gian=t[0])

        def ma_moi(self, bi_mat):
            t[0] += 31.0
            return xac_thuc_2fa.sinh_totp(bi_mat, moc_thoi_gian=t[0])

    return DongHo()


def _tao_admin(client, ten="admin-bm", mk=MK_ADMIN):
    ban_ghi, _, _ = asyncio.run(
        nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap=ten, mat_khau=mk)
    )
    return ban_ghi


def _dang_nhap(client, ten, mk):
    """Đăng nhập web (chưa bật 2FA) → (cookies, csrf headers)."""
    r = client.post("/api/dang-nhap", json={"ten_dang_nhap": ten, "mat_khau": mk})
    assert r.status_code == 200, f"đăng nhập phải 200, nhận {r.status_code}: {r.text}"
    return {"w2a_phien": r.cookies.get("w2a_phien")}, {"X-CSRF-Token": r.json()["csrf"]}


def _bat_2fa_admin(admin, dh):
    """Bật 2FA cho admin qua API nội bộ, trả về bí mật TOTP.

    Đồng hồ giả: mã đăng ký sinh ở bước hiện tại; mọi mã dùng sau đó phải qua
    dh.ma_moi (nhảy +31 giây trước khi sinh) vì xác minh chống phát lại theo bước.
    """
    bi_mat, _uri = xac_thuc_2fa.bat_dau_dang_ky(admin["id"], admin["ten_dang_nhap"])
    ma = dh.ma(bi_mat)
    ok, _codes = xac_thuc_2fa.xac_nhan_dang_ky(admin["id"], ma)
    assert ok, "xác nhận đăng ký 2FA phải thành công"
    assert xac_thuc_2fa.da_bat_2fa(admin["id"]) is True
    return bi_mat


def _dang_nhap_2fa(client, ten, mk, dh, bi_mat):
    """Đăng nhập web khi đã bật 2FA (2 bước) → (cookies, csrf headers)."""
    r = client.post("/api/dang-nhap", json={"ten_dang_nhap": ten, "mat_khau": mk})
    assert r.status_code == 401, f"bước 1 phải 401 can_ma_2fa, nhận {r.status_code}: {r.text}"
    err = r.json().get("error", {})
    ve = err.get("ve") or r.json().get("ve")
    assert ve, f"phải có vé tạm 2FA: {r.text}"
    r2 = client.post("/api/dang-nhap/2fa", json={"ve": ve, "ma": dh.ma_moi(bi_mat)})
    assert r2.status_code == 200, f"bước 2 phải 200, nhận {r2.status_code}: {r2.text}"
    return {"w2a_phien": r2.cookies.get("w2a_phien")}, {"X-CSRF-Token": r2.json()["csrf"]}


def _stepup(client, cookies, csrf, than):
    return client.post("/api/toi/xac-nhan-lai", json=than,
                       cookies=cookies, headers=csrf)


def _qt():
    return {"Authorization": f"Bearer {KHOA_QT}"}


# ─────────────────────────────────────────────────────────────────────────────
# (a) Quy tắc xác thực lại: mật khẩu vs mã 2FA
# ─────────────────────────────────────────────────────────────────────────────


def test_stepup_mat_khau_duoc_khi_chua_bat_2fa(moi_truong):
    client, _ = moi_truong
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    r = _stepup(client, cookies, csrf, {"mat_khau": MK_ADMIN})
    assert r.status_code == 200, r.text


def test_stepup_mat_khau_bi_tu_choi_khi_da_bat_2fa(moi_truong, dong_ho):
    client, _ = moi_truong
    admin = _tao_admin(client)
    bi_mat = _bat_2fa_admin(admin, dong_ho)
    cookies, csrf = _dang_nhap_2fa(client, admin["ten_dang_nhap"], MK_ADMIN, dong_ho, bi_mat)
    r = _stepup(client, cookies, csrf, {"mat_khau": MK_ADMIN})
    assert r.status_code == 401, f"mật khẩu khi đã bật 2FA phải 401, nhận {r.status_code}: {r.text}"
    assert "2FA" in r.text, "thông báo phải hướng sang mã 2FA"
    # Chưa được xác nhận lại: thao tác tạo khoá vẫn bị chặn step-up
    r2 = client.post("/api/khoa-khach", json={"nhan": "thu-sau-stepup-hong"},
                     cookies=cookies, headers=csrf)
    assert r2.status_code == 403, r2.text
    assert r2.json()["error"]["code"] == "step_up_required"


def test_stepup_ma_2fa_duoc_khi_da_bat_2fa(moi_truong, dong_ho):
    client, _ = moi_truong
    admin = _tao_admin(client)
    bi_mat = _bat_2fa_admin(admin, dong_ho)
    cookies, csrf = _dang_nhap_2fa(client, admin["ten_dang_nhap"], MK_ADMIN, dong_ho, bi_mat)
    r = _stepup(client, cookies, csrf, {"ma_2fa": dong_ho.ma_moi(bi_mat)})
    assert r.status_code == 200, r.text
    r2 = client.post("/api/khoa-khach", json={"nhan": "thu-sau-stepup-2fa"},
                     cookies=cookies, headers=csrf)
    assert r2.status_code == 200, r2.text


def test_stepup_ma_2fa_sai_bi_401(moi_truong, dong_ho):
    client, _ = moi_truong
    admin = _tao_admin(client)
    bi_mat = _bat_2fa_admin(admin, dong_ho)
    cookies, csrf = _dang_nhap_2fa(client, admin["ten_dang_nhap"], MK_ADMIN, dong_ho, bi_mat)
    r = _stepup(client, cookies, csrf, {"ma_2fa": "000000"})
    assert r.status_code == 401, r.text


def test_stepup_khoa_quan_tri_van_duoc_khi_da_bat_2fa(moi_truong, dong_ho):
    """Khoá quản trị là bí mật gốc (không phải mật khẩu) nên vẫn miễn như cũ — ghi rõ."""
    client, _ = moi_truong
    admin = _tao_admin(client)
    bi_mat = _bat_2fa_admin(admin, dong_ho)
    cookies, csrf = _dang_nhap_2fa(client, admin["ten_dang_nhap"], MK_ADMIN, dong_ho, bi_mat)
    r = _stepup(client, cookies, csrf, {"khoa_quan_tri": KHOA_QT})
    assert r.status_code == 200, r.text


# ─────────────────────────────────────────────────────────────────────────────
# (a) Gating: tạo/xoá trong tab phải có step-up
# ─────────────────────────────────────────────────────────────────────────────


def test_tao_khoa_khach_doi_stepup(moi_truong):
    client, _ = moi_truong
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    r = client.post("/api/khoa-khach", json={"nhan": "khoa-moi"},
                    cookies=cookies, headers=csrf)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "step_up_required"
    assert _stepup(client, cookies, csrf, {"mat_khau": MK_ADMIN}).status_code == 200
    r2 = client.post("/api/khoa-khach", json={"nhan": "khoa-moi"},
                     cookies=cookies, headers=csrf)
    assert r2.status_code == 200, r2.text
    # Hiện một lần lúc tạo (trong lời gọi đã xác thực lại), sau đó danh sách không lộ
    assert "khoa" in r2.json() and r2.json()["khoa"]["khoa"].startswith("w2a_")
    r3 = client.get("/api/khoa-khach", cookies=cookies, headers=csrf)
    assert "w2a_" not in r3.text, "danh sách khoá không được lộ khoá thô"


def test_xoa_khoa_khach_doi_stepup(moi_truong):
    client, _ = moi_truong
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    assert _stepup(client, cookies, csrf, {"mat_khau": MK_ADMIN}).status_code == 200
    kid = client.post("/api/khoa-khach", json={"nhan": "khoa-xoa"},
                      cookies=cookies, headers=csrf).json()["khoa"]["id"]
    # Phiên mới (chưa xác nhận lại) thì xoá phải bị chặn
    cookies2, csrf2 = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    r = client.delete(f"/api/khoa-khach/{kid}", cookies=cookies2, headers=csrf2)
    assert r.status_code == 403, r.text
    assert _stepup(client, cookies2, csrf2, {"mat_khau": MK_ADMIN}).status_code == 200
    r2 = client.delete(f"/api/khoa-khach/{kid}", cookies=cookies2, headers=csrf2)
    assert r2.status_code == 200, r2.text


def test_tao_nguoi_dung_thuong_doi_stepup(moi_truong):
    """Trước đây chỉ tạo admin mới cần step-up; phương án mới: MỌI tạo trong tab đều cần."""
    client, _ = moi_truong
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    r = client.post("/api/nguoi-dung",
                    json={"ten_dang_nhap": "user-moi-bm", "mat_khau": "MatKhauUserBm0000000000", "vai": "user"},
                    cookies=cookies, headers=csrf)
    assert r.status_code == 403, r.text
    assert _stepup(client, cookies, csrf, {"mat_khau": MK_ADMIN}).status_code == 200
    r2 = client.post("/api/nguoi-dung",
                     json={"ten_dang_nhap": "user-moi-bm", "mat_khau": "MatKhauUserBm0000000000", "vai": "user"},
                     cookies=cookies, headers=csrf)
    assert r2.status_code == 200, r2.text


def test_xoa_nguoi_dung_doi_stepup(moi_truong):
    client, _ = moi_truong
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    assert _stepup(client, cookies, csrf, {"mat_khau": MK_ADMIN}).status_code == 200
    uid = client.post("/api/nguoi-dung",
                      json={"ten_dang_nhap": "user-xoa-bm", "mat_khau": "MatKhauUserBm0000000000", "vai": "user"},
                      cookies=cookies, headers=csrf).json()["nguoi_dung"]["id"]
    cookies2, csrf2 = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    r = client.delete(f"/api/nguoi-dung/{uid}", cookies=cookies2, headers=csrf2)
    assert r.status_code == 403, r.text
    assert _stepup(client, cookies2, csrf2, {"mat_khau": MK_ADMIN}).status_code == 200
    assert client.delete(f"/api/nguoi-dung/{uid}", cookies=cookies2, headers=csrf2).status_code == 200


def test_quan_tri_tat_2fa_ho_khong_doi_stepup(moi_truong):
    """Tắt 2FA hộ KHÔNG nằm trong «tạo/xoá» của phương án mới và là đường khôi phục khi
    kho 2FA hỏng — gắn step-up sẽ khoá chết đường này (xác nhận lại cũng đọc kho 2FA).
    Giữ nguyên: chỉ cần admin + CSRF là tắt được, không cần step-up."""
    client, _ = moi_truong
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    assert _stepup(client, cookies, csrf, {"mat_khau": MK_ADMIN}).status_code == 200
    uid = client.post("/api/nguoi-dung",
                      json={"ten_dang_nhap": "user-2fa-bm", "mat_khau": "MatKhauUserBm0000000000", "vai": "user"},
                      cookies=cookies, headers=csrf).json()["nguoi_dung"]["id"]
    cookies2, csrf2 = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    r = client.post(f"/api/nguoi-dung/{uid}/tat-2fa", cookies=cookies2, headers=csrf2)
    assert r.status_code == 200, r.text


def test_bearer_admin_van_mien_stepup(moi_truong):
    """Bearer quản trị trực tiếp vẫn được miễn step-up như mọi đường chạm bí mật khác."""
    client, _ = moi_truong
    r = client.post("/api/khoa-khach", json={"nhan": "khoa-bearer"}, headers=_qt())
    assert r.status_code == 200, r.text


# ─────────────────────────────────────────────────────────────────────────────
# (a) Đổi mật khẩu: 2FA bật thì chỉ cần mã 2FA, không cần mật khẩu cũ
# ─────────────────────────────────────────────────────────────────────────────


def test_doi_mat_khau_2fa_on_chi_can_ma(moi_truong, dong_ho):
    client, _ = moi_truong
    admin = _tao_admin(client)
    bi_mat = _bat_2fa_admin(admin, dong_ho)
    cookies, csrf = _dang_nhap_2fa(client, admin["ten_dang_nhap"], MK_ADMIN, dong_ho, bi_mat)
    r = client.post("/api/toi/mat-khau",
                    json={"mat_khau_moi": MK_MOI, "ma_2fa": dong_ho.ma_moi(bi_mat)},
                    cookies=cookies, headers=csrf)
    assert r.status_code == 200, r.text
    # Mật khẩu mới có hiệu lực (đăng nhập lại + 2FA)
    cookies2, csrf2 = _dang_nhap_2fa(client, admin["ten_dang_nhap"], MK_MOI, dong_ho, bi_mat)
    assert cookies2.get("w2a_phien"), "đăng nhập bằng mật khẩu mới phải có phiên"


def test_doi_mat_khau_2fa_on_thieu_ma_bi_400(moi_truong, dong_ho):
    client, _ = moi_truong
    admin = _tao_admin(client)
    bi_mat = _bat_2fa_admin(admin, dong_ho)
    cookies, csrf = _dang_nhap_2fa(client, admin["ten_dang_nhap"], MK_ADMIN, dong_ho, bi_mat)
    r = client.post("/api/toi/mat-khau", json={"mat_khau_moi": MK_MOI},
                    cookies=cookies, headers=csrf)
    assert r.status_code == 400, r.text


def test_doi_mat_khau_2fa_on_mat_khau_cu_khong_du(moi_truong, dong_ho):
    """Khi đã bật 2FA, chỉ mật khẩu cũ (không mã) KHÔNG được đổi mật khẩu."""
    client, _ = moi_truong
    admin = _tao_admin(client)
    bi_mat = _bat_2fa_admin(admin, dong_ho)
    cookies, csrf = _dang_nhap_2fa(client, admin["ten_dang_nhap"], MK_ADMIN, dong_ho, bi_mat)
    r = client.post("/api/toi/mat-khau",
                    json={"mat_khau_cu": MK_ADMIN, "mat_khau_moi": MK_MOI},
                    cookies=cookies, headers=csrf)
    assert r.status_code in (400, 401), r.text
    # Mật khẩu cũ vẫn còn hiệu lực
    _dang_nhap_2fa(client, admin["ten_dang_nhap"], MK_ADMIN, dong_ho, bi_mat)


def test_doi_mat_khau_2fa_off_van_can_mat_khau_cu(moi_truong):
    client, _ = moi_truong
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    r = client.post("/api/toi/mat-khau", json={"mat_khau_moi": MK_MOI},
                    cookies=cookies, headers=csrf)
    assert r.status_code == 400, r.text
    r2 = client.post("/api/toi/mat-khau",
                     json={"mat_khau_cu": MK_ADMIN, "mat_khau_moi": MK_MOI},
                     cookies=cookies, headers=csrf)
    assert r2.status_code == 200, r2.text


# ─────────────────────────────────────────────────────────────────────────────
# (b) Hiển thị sau xác thực lại: mật khẩu noVNC (thay never-return của NV305)
# ─────────────────────────────────────────────────────────────────────────────


def _hien(client, cookies, csrf, than, headers=None):
    return client.post("/api/trinh-duyet/mat-khau-vnc/hien", json=than,
                       cookies=cookies, headers=headers if headers is not None else csrf)


def test_hien_mat_khau_vnc_khong_bang_chung_bi_401(moi_truong):
    client, mod = moi_truong
    # Chỉ Bearer quản trị, không bằng chứng tươi trong body → vẫn 401 (không miễn)
    r = client.post("/api/trinh-duyet/mat-khau-vnc/hien", json={}, headers=_qt())
    assert r.status_code == 401, r.text
    assert mod.hien_goi == 0, "không được gọi module khi chưa có bằng chứng"


def test_hien_mat_khau_vnc_mat_khau_dung_khi_chua_bat_2fa(moi_truong):
    client, mod = moi_truong
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    r = _hien(client, cookies, csrf, {"mat_khau": MK_ADMIN})
    assert r.status_code == 200, r.text
    assert r.json()["mat_khau"] == MK_VNC_TEST
    assert mod.hien_goi == 1


def test_hien_mat_khau_vnc_mat_khau_sai_bi_401(moi_truong):
    client, mod = moi_truong
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    r = _hien(client, cookies, csrf, {"mat_khau": "SaiMatKhau000000000000"})
    assert r.status_code == 401, r.text
    assert mod.hien_goi == 0


def test_hien_mat_khau_vnc_mat_khau_bi_tu_choi_khi_da_bat_2fa(moi_truong, dong_ho):
    """Chốt 19:26: khi đã bật 2FA thì CHỈ mã 2FA mở hiển thị, không nhận mật khẩu thay thế."""
    client, mod = moi_truong
    admin = _tao_admin(client)
    bi_mat = _bat_2fa_admin(admin, dong_ho)
    cookies, csrf = _dang_nhap_2fa(client, admin["ten_dang_nhap"], MK_ADMIN, dong_ho, bi_mat)
    r = _hien(client, cookies, csrf, {"mat_khau": MK_ADMIN})
    assert r.status_code == 401, r.text
    assert "2FA" in r.text
    assert mod.hien_goi == 0


def test_hien_mat_khau_vnc_ma_2fa_dung(moi_truong, dong_ho):
    client, mod = moi_truong
    admin = _tao_admin(client)
    bi_mat = _bat_2fa_admin(admin, dong_ho)
    cookies, csrf = _dang_nhap_2fa(client, admin["ten_dang_nhap"], MK_ADMIN, dong_ho, bi_mat)
    r = _hien(client, cookies, csrf, {"ma_2fa": dong_ho.ma_moi(bi_mat)})
    assert r.status_code == 200, r.text
    assert r.json()["mat_khau"] == MK_VNC_TEST
    assert mod.hien_goi == 1


def test_hien_mat_khau_vnc_ma_2fa_sai_bi_401(moi_truong, dong_ho):
    client, mod = moi_truong
    admin = _tao_admin(client)
    bi_mat = _bat_2fa_admin(admin, dong_ho)
    cookies, csrf = _dang_nhap_2fa(client, admin["ten_dang_nhap"], MK_ADMIN, dong_ho, bi_mat)
    r = _hien(client, cookies, csrf, {"ma_2fa": "000000"})
    assert r.status_code == 401, r.text
    assert mod.hien_goi == 0


def test_hien_mat_khau_vnc_gioi_han_tan_suat(moi_truong):
    """Cùng một người gọi hiển thị quá 10 lần/60 giây → 429 (chống dò mật khẩu)."""
    client, mod = moi_truong
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    for _ in range(10):
        r = _hien(client, cookies, csrf, {"mat_khau": MK_ADMIN})
        assert r.status_code == 200, r.text
    r = _hien(client, cookies, csrf, {"mat_khau": MK_ADMIN})
    assert r.status_code == 429, r.text


def test_hien_mat_khau_vnc_khong_phai_admin_bi_403(moi_truong):
    client, mod = moi_truong
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    assert _stepup(client, cookies, csrf, {"mat_khau": MK_ADMIN}).status_code == 200
    client.post("/api/nguoi-dung",
                json={"ten_dang_nhap": "user-thuong-bm", "mat_khau": "MatKhauUserBm0000000000", "vai": "user"},
                cookies=cookies, headers=csrf)
    cookies_u, csrf_u = _dang_nhap(client, "user-thuong-bm", "MatKhauUserBm0000000000")
    r = _hien(client, cookies_u, csrf_u, {"mat_khau": "MatKhauUserBm0000000000"})
    assert r.status_code == 403, r.text
    assert mod.hien_goi == 0


# ─────────────────────────────────────────────────────────────────────────────
# (c) Công tắc noVNC trong web UI
# ─────────────────────────────────────────────────────────────────────────────


def test_bat_tat_novnc_khong_bool_bi_400(moi_truong):
    """Không tin client: bat phải là bool thật, chuỗi 'true' hay số 1 đều bị từ chối."""
    client, mod = moi_truong
    for bat_xau in ["true", 1, 0, None]:
        r = client.post("/api/trinh-duyet/man-hinh/bat-tat", json={"bat": bat_xau}, headers=_qt())
        assert r.status_code == 400, f"bat={bat_xau!r} phải 400, nhận {r.status_code}: {r.text}"
    assert mod.bat_tat_goi == []


def test_bat_tat_novnc_doi_stepup(moi_truong):
    client, mod = moi_truong
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    r = client.post("/api/trinh-duyet/man-hinh/bat-tat", json={"bat": True},
                    cookies=cookies, headers=csrf)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "step_up_required"
    assert mod.bat_tat_goi == []
    assert _stepup(client, cookies, csrf, {"mat_khau": MK_ADMIN}).status_code == 200
    r2 = client.post("/api/trinh-duyet/man-hinh/bat-tat", json={"bat": True},
                     cookies=cookies, headers=csrf)
    assert r2.status_code == 200, r2.text
    assert r2.json()["bat"] is True and r2.json()["dang_chay"] is True
    assert mod.bat_tat_goi == [True]


def test_trang_thai_novnc_chi_bool_khong_bi_mat(moi_truong):
    client, _ = moi_truong
    r = client.get("/api/trinh-duyet/man-hinh/trang-thai", headers=_qt())
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j) <= {"ok", "bat", "dang_chay", "x11vnc", "websockify", "da_dat"}
    assert "mat_khau" not in r.text


# ─────────────────────────────────────────────────────────────────────────────
# (d) Kiểm tra rò rỉ: không GET nào trả bí mật, không giá trị điền sẵn
# ─────────────────────────────────────────────────────────────────────────────


def test_get_khong_tra_bi_mat(moi_truong):
    client, _ = moi_truong
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    assert _stepup(client, cookies, csrf, {"mat_khau": MK_ADMIN}).status_code == 200
    client.post("/api/khoa-khach", json={"nhan": "khoa-kiem-ro-ri"},
                cookies=cookies, headers=csrf)
    r_khoa = client.get("/api/khoa-khach", cookies=cookies, headers=csrf)
    assert r_khoa.status_code == 200
    assert "w2a_" not in r_khoa.text and "bam" not in r_khoa.text
    r_nd = client.get("/api/nguoi-dung", cookies=cookies, headers=csrf)
    assert r_nd.status_code == 200
    assert "bam_mat_khau" not in r_nd.text
    r_tt = client.get("/api/trinh-duyet/mat-khau-vnc/trang-thai", headers=_qt())
    assert r_tt.status_code == 200
    assert set(r_tt.json()) <= {"ok", "da_dat"}
    r_toi = client.get("/api/toi", cookies=cookies, headers=csrf)
    assert r_toi.status_code == 200
    assert "bam_mat_khau" not in r_toi.text


def test_trang_khong_dien_san_bi_mat():
    """Ô mật khẩu VNC chỉ nhận từ tay người gõ; bí mật hiện lại nằm ở khoá riêng vncHien."""
    assert "vncMk: ''" in HTML_BAO_MAT or 'vncMk: ""' in HTML_BAO_MAT
    import re
    for m in re.finditer(r"vncMk\s*:\s*([^,}\n]+)", HTML_BAO_MAT):
        gia_tri = m.group(1).strip()
        # s.vncMk ở renderVals chỉ là chuyển tiếp state ra khuôn mẫu (state này chỉ nhận
        # '' hoặc tay người gõ, xem hai trường hợp dưới) — không phải điền sẵn từ máy chủ.
        assert gia_tri in ("''", '""', "e.target.value", "s.vncMk"), (
            f"vncMk chỉ được gán '' hoặc từ tay gõ, thấy: {gia_tri}")
    assert "mat-khau-vnc/hien" in HTML_BAO_MAT, "trang phải có đường hiển thị lại"
    assert "man-hinh/bat-tat" in HTML_BAO_MAT, "trang phải có công tắc màn hình xem"
    # Hai câu cũ ở MỤC VNC đã lỗi thời phải hết (cụm «không xem lại được» vẫn đúng cho
    # mã khôi phục 2FA ở mục khác nên không cấm toàn trang, chỉ cấm đúng câu của mục VNC):
    assert "Mật khẩu chỉ ghi đè, không xem lại được." not in HTML_BAO_MAT
    assert "màn hình xem đang dùng mật khẩu do máy tự sinh" not in HTML_BAO_MAT
    # Mục VNC mới phải nói rõ quy tắc hiển thị lại sau xác thực
    assert "xác thực lại bằng mật khẩu admin" in HTML_BAO_MAT


def test_khung_js_stepup_hoi_ma_2fa_khi_da_bat():
    """Hộp thoại xác nhận lại của khung.js phải hỏi mã 2FA (không phải mật khẩu)
    khi người dùng hiện tại đã bật 2FA, để gửi đúng ma_2fa lên /xac-nhan-lai."""
    assert "ma_2fa" in KHUNG_JS
    assert "bat_2fa" in KHUNG_JS


def test_template_thoat_xss():
    """Mọi giá trị chèn qua {{ }} trong support.js đều qua thoat/thoatTT (chống XSS
    từ chuỗi do module/thiết bị cung cấp, vd nhãn khoá khách chứa thẻ script)."""
    import re
    for m in re.finditer(r"chu\(lay\(x, p\)\)", SUPPORT_JS):
        truoc = SUPPORT_JS[max(0, m.start() - 40):m.start()]
        assert "thoat" in truoc, f"chèn giá trị không qua thoat: ...{truoc[-30:]}"
    assert "thoat(chu(lay(x, p)))" in SUPPORT_JS
    assert "thoatTT(val.replace" in SUPPORT_JS
