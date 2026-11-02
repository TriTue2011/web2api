"""NV276 — THEO DÕI trình duyệt đang đăng nhập tự động bằng noVNC thật (khác hướng ảnh chụp của test_nv276_xem_trinh_duyet.py).

Đường mới: POST /api/trinh-duyet/{module}/xem — chỉ quản trị, có step-up (phiên web phải xác nhận lại;
Bearer quản trị được miễn như mọi đường chạm bí mật khác), module khai khả năng «trinh_duyet_xem» trả đích
noVNC nội bộ (màn hình dùng chung của dịch vụ, KHÔNG tạo hồ sơ trình duyệt mới), lõi ghim đích qua kiểm SSRF
rồi cấp vé một lần 60 giây như proxy /novnc hiện có. Mật khẩu VNC không bao giờ xuất hiện trong phản hồi:
x11vnc vẫn bắt mật khẩu, người xem tự nhập một lần ở hộp nhập của noVNC (mật khẩu chỉ chủ máy biết qua
docker exec vào container dịch vụ — ngoài phạm vi API này).

Test qua dịch vụ GIẢ, không đăng nhập thật, không trình duyệt thật.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import gioi_han, novnc
from app.main import app

GOC = Path(__file__).resolve().parents[1]
HTML = (GOC / "web" / "He-thong-AI.dc.html").read_text(encoding="utf-8")
KHOA_QT = "khoa-quan-tri-nv276-novnc-123456"
MODULE = "mod-xem-novnc-nv276"
DICH_GIA = "http://127.0.0.1:6080"


class ModXemGia:
    """Module giả có khả năng trinh_duyet_xem: trả đích noVNC nội bộ, không gọi mạng."""

    def __init__(self, dich=DICH_GIA, loi=None):
        self.dich, self.loi, self.so_lan = dich, loi, 0

    async def trinh_duyet_xem_mo(self):
        self.so_lan += 1
        if self.loi:
            raise self.loi
        return {"dich": self.dich}


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "1000")
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.delenv("WEB2API_TIN_PROXY", raising=False)
    gioi_han.khoi_dong()
    yield
    with TestClient(app) as c:
        c.app.state.so.dang_chay.pop(MODULE, None)
        c.app.state.so.dang_chay.pop("mod-khong-xem-novnc", None)
    novnc._VE.clear()
    novnc._PHIEN.clear()
    novnc._HO_SO.clear()


def _qt():
    return {"Authorization": f"Bearer {KHOA_QT}"}


def _xem(c):
    r = c.post(f"/api/trinh-duyet/{MODULE}/xem", json={}, headers=_qt())
    assert r.status_code == 200, r.text
    return r.json()


def _ve_tu_url(url: str) -> str:
    assert url.startswith("/novnc/dang-nhap?ve="), url
    return url.split("ve=", 1)[1]


def test_xem_cap_ve_url_khong_lo_mat_khau(moi_truong):
    with TestClient(app) as c:
        c.app.state.so.dang_chay[MODULE] = ModXemGia()
        j = _xem(c)
        assert j["url"].startswith("/novnc/dang-nhap?ve=")
        assert j["ho_so"] and len(j["ho_so"]) >= 16
        assert "mat_khau" not in r_text(j) and "password" not in r_text(j)
        assert "6080" not in j["url"]  # đích nội bộ chỉ nằm trong sổ ghim của lõi, không ra phản hồi


def r_text(j):
    import json

    return json.dumps(j, ensure_ascii=False).lower()


def test_xem_ve_mot_lan_doi_thanh_phien(moi_truong):
    with TestClient(app, follow_redirects=False) as c:
        c.app.state.so.dang_chay[MODULE] = ModXemGia()
        ve = _ve_tu_url(_xem(c)["url"])
        r1 = c.post("/novnc/dang-nhap", data={"ve": ve},
                    headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": "http://testserver"})
        assert r1.status_code == 303, r1.text
        r2 = c.post("/novnc/dang-nhap", data={"ve": ve},
                    headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": "http://testserver"})
        assert r2.status_code == 401, f"dùng lại vé phải 401, được {r2.status_code}"


def test_xem_ve_het_han_401(moi_truong):
    with TestClient(app, follow_redirects=False) as c:
        c.app.state.so.dang_chay[MODULE] = ModXemGia()
        ve = _ve_tu_url(_xem(c)["url"])
        novnc._VE[novnc._bam(ve)]["het_han"] = time.time() - 1
        r = c.post("/novnc/dang-nhap", data={"ve": ve},
                   headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": "http://testserver"})
        assert r.status_code == 401, f"vé hết hạn phải 401, được {r.status_code}"


def test_xem_thu_hoi_khi_dong_lam_ve_mat_hieu_luc(moi_truong):
    with TestClient(app, follow_redirects=False) as c:
        c.app.state.so.dang_chay[MODULE] = ModXemGia()
        j = _xem(c)
        ve = _ve_tu_url(j["url"])
        novnc.thu_hoi_ho_so(MODULE, j["ho_so"])
        r = c.post("/novnc/dang-nhap", data={"ve": ve},
                   headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": "http://testserver"})
        assert r.status_code == 401, f"vé đã thu hồi phải 401, được {r.status_code}"


def test_xem_idor_ve_module_khac_khong_mo_duoc_ho_so_nay(moi_truong):
    """Vé gắn (module, hồ sơ) của nó: xoá hồ sơ của module khác không ảnh hưởng, và vé không
    mở được hồ sơ thuộc module khác (doi_ve tra cứu đúng cặp đã ghim)."""
    with TestClient(app, follow_redirects=False) as c:
        c.app.state.so.dang_chay[MODULE] = ModXemGia()
        j = _xem(c)
        ve = _ve_tu_url(j["url"])
        novnc.thu_hoi_ho_so("mod-khac-khong-lien-quan", j["ho_so"])
        r = c.post("/novnc/dang-nhap", data={"ve": ve},
                   headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": "http://testserver"})
        assert r.status_code == 303, r.text


def test_xem_khong_khoa_401_khach_403(moi_truong):
    with TestClient(app) as c:
        c.app.state.so.dang_chay[MODULE] = ModXemGia()
        assert c.post(f"/api/trinh-duyet/{MODULE}/xem", json={}).status_code == 401
        rk = c.post("/api/khoa-khach", json={"nhan": "nv276-novnc-khach"}, headers=_qt())
        assert rk.status_code == 200, rk.text
        khoa_khach = rk.json()["khoa"]["khoa"]
        r = c.post(f"/api/trinh-duyet/{MODULE}/xem", json={},
                   headers={"Authorization": f"Bearer {khoa_khach}"})
        assert r.status_code == 403, f"khoá khách phải 403, được {r.status_code}"


def test_xem_module_khong_ho_tro_501_khong_co_404(moi_truong):
    with TestClient(app) as c:
        c.app.state.so.dang_chay["mod-khong-xem-novnc"] = object()
        assert c.post("/api/trinh-duyet/mod-khong-xem-novnc/xem", json={}, headers=_qt()).status_code == 501
        assert c.post("/api/trinh-duyet/mod-khong-co-novnc/xem", json={}, headers=_qt()).status_code == 404


def test_xem_module_loi_502_khong_lo_chi_tiet(moi_truong):
    with TestClient(app) as c:
        c.app.state.so.dang_chay[MODULE] = ModXemGia(loi=RuntimeError("bi mat abc123xyz"))
        r = c.post(f"/api/trinh-duyet/{MODULE}/xem", json={}, headers=_qt())
        assert r.status_code == 502 and "abc123xyz" not in r.text


def test_xem_dich_ssrf_bi_chan_502(moi_truong):
    with TestClient(app) as c:
        c.app.state.so.dang_chay[MODULE] = ModXemGia(dich="http://169.254.169.254:6080")
        r = c.post(f"/api/trinh-duyet/{MODULE}/xem", json={}, headers=_qt())
        assert r.status_code == 502, f"đích metadata phải 502, được {r.status_code}"
        assert "169.254.169.254" not in r.text  # địa chỉ đích cụ thể không được lộ ra phản hồi


def test_module_that_may_dang_nhap_tra_dich_novnc_tu_dich_vu_url():
    """Module thật: đích noVNC suy từ host của dich_vu_url đã cấu hình, cổng 6080, không dùng
    khoá dịch vụ, không gọi mạng."""
    import asyncio
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "mod_may_dang_nhap_nv276", GOC / "modules" / "may-dang-nhap" / "module.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mod_may_dang_nhap_nv276"] = mod
    spec.loader.exec_module(mod)
    m = mod.Module({})
    m.nap({"dich_vu_url": "http://192.0.2.10:8010", "khoa_dich_vu": ""})
    kq = asyncio.run(m.trinh_duyet_xem_mo())
    assert kq["dich"] == "http://192.0.2.10:6080"
    assert "khoa" not in str(kq).lower()


# ── giao diện: nút noVNC riêng, giữ nguyên nút ảnh chụp cũ ─────────────────────────
def test_giao_dien_co_nut_theo_doi_novnc_rieng():
    assert 'id="k268-theo-doi-truc-tiep"' in HTML
    assert "khuTheoDoiTrucTiep" in HTML
    assert "Xem trình duyệt đang đăng nhập; gặp CAPTCHA/xác minh thì tự xử lý rồi bấm Đăng nhập tự động lần nữa" in HTML
    # nút ảnh chụp cũ vẫn còn nguyên, không bị đổi hành vi
    assert 'id="k268-theo-doi"' in HTML and 'id="k268-xem-anh"' in HTML


def test_giao_dien_nut_xuong_dong_khong_tran_ngang():
    """Hàng nút .k282-hd (trước NV282: .k268-hd) dùng flex-wrap:wrap nên nút mới xuống dòng ở 390 px thay vì tràn ngang
    (kiểm tĩnh; chưa đo bằng Chrome thật — xem báo cáo)."""
    import re

    m = re.search(r"\.k282-hd\{([^}]*)\}", HTML)
    assert m and "flex-wrap:wrap" in m.group(1)


def test_giao_dien_mo_cua_so_ngay_trong_cu_bam_va_dung_xem():
    i = HTML.index("khuTheoDoiTrucTiep")
    ham = HTML[i:i + 1800]
    assert 'window.open("about:blank", "_blank")' in ham
    assert "/xem" in ham
    assert "trinh_duyet_xem" in HTML  # giao diện tìm module theo khả năng, không nêu tên module
    assert "may-dang-nhap" not in ham


# ── bổ sung khi gộp: step-up cho phiên web, mở lại thì thu hồi hồ sơ xem cũ ──────────
def test_xem_phien_web_chua_xac_nhan_lai_bi_403_step_up(moi_truong):
    """Phiên web (cookie) chưa xác nhận lại phải bị 403 step_up_required; chỉ Bearer quản trị được miễn."""
    import asyncio

    from app import nguoi_dung

    with TestClient(app) as c:
        c.app.state.so.dang_chay[MODULE] = ModXemGia()
        asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="qt-nv276", mat_khau="MatKhauGia-nv276-000"))
        r = c.post("/api/dang-nhap", json={"ten_dang_nhap": "qt-nv276", "mat_khau": "MatKhauGia-nv276-000"})
        assert r.status_code == 200, r.text
        r2 = c.post(f"/api/trinh-duyet/{MODULE}/xem", json={},
                    cookies={"w2a_phien": r.cookies.get("w2a_phien")}, headers={"X-CSRF-Token": r.json()["csrf"]})
        assert r2.status_code == 403, f"phiên web chưa step-up phải 403, được {r2.status_code}: {r2.text}"
        assert r2.json()["error"]["code"] == "step_up_required"
        assert c.app.state.so.dang_chay[MODULE].so_lan == 0, "chưa qua step-up thì không được gọi module"


def test_xem_mo_lai_thu_hoi_ho_so_xem_cu_va_so_khong_phinh(moi_truong):
    with TestClient(app, follow_redirects=False) as c:
        c.app.state.so.dang_chay[MODULE] = ModXemGia()
        j1 = _xem(c)
        ve1 = _ve_tu_url(j1["url"])
        for _ in range(6):  # vượt xa trần 4 phiên mỗi hồ sơ: mỗi lần mở lại phải thay hồ sơ cũ, không cộng dồn
            j = _xem(c)
        xem = [k for k, v in novnc._HO_SO.items() if k[0] == MODULE and v.get("chi_xem")]
        assert xem == [(MODULE, j["ho_so"])], f"mỗi module chỉ giữ một hồ sơ xem, thấy {len(xem)}"
        r = c.post("/novnc/dang-nhap", data={"ve": ve1},
                   headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": "http://testserver"})
        assert r.status_code == 401, f"vé của hồ sơ xem cũ phải mất hiệu lực, được {r.status_code}"
        r = c.post("/novnc/dang-nhap", data={"ve": _ve_tu_url(j["url"])},
                   headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": "http://testserver"})
        assert r.status_code == 303, r.text


def test_xin_ve_lai_ho_so_xem_cung_doi_step_up(moi_truong):
    """Không né step-up bằng đường /ve: phiên web chưa xác nhận lại xin vé cho hồ sơ xem phải bị 403;
    Bearer quản trị vẫn xin được."""
    import asyncio

    from app import nguoi_dung

    with TestClient(app) as c:
        c.app.state.so.dang_chay[MODULE] = ModXemGia()
        j = _xem(c)
        r_bearer = c.post(f"/api/trinh-duyet/{MODULE}/{j['ho_so']}/ve", headers=_qt())
        assert r_bearer.status_code == 200, r_bearer.text
        asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="qt2-nv276", mat_khau="MatKhauGia-nv276-111"))
        r = c.post("/api/dang-nhap", json={"ten_dang_nhap": "qt2-nv276", "mat_khau": "MatKhauGia-nv276-111"})
        assert r.status_code == 200, r.text
        r2 = c.post(f"/api/trinh-duyet/{MODULE}/{j['ho_so']}/ve",
                    cookies={"w2a_phien": r.cookies.get("w2a_phien")}, headers={"X-CSRF-Token": r.json()["csrf"]})
        assert r2.status_code == 403, f"/ve của hồ sơ xem mà chưa step-up phải 403, được {r2.status_code}: {r2.text}"
