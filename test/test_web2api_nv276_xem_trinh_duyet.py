"""NV276: lõi cấp vé noVNC để XEM trình duyệt dịch vụ đang đăng nhập tự động (như c2a mở noVNC khi bấm «Đăng nhập tự động»).
Module khai đích bằng `trinh_duyet_xem()`; lõi ghim đích, chặn dải mạng, cấp vé có hạn; quyền quản trị; không rò đích ra phản hồi."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import gioi_han, novnc
from app.main import app

KHOA_QT = "khoa-quan-tri-nv276-xem-123456"
MODULE = "mod-xem-nv276"


class ModGia:
    def __init__(self, dich="http://10.77.0.9:6080", loi=None):
        self.dich, self.loi, self.so_lan = dich, loi, 0

    async def trinh_duyet_xem(self):
        self.so_lan += 1
        if self.loi:
            raise self.loi
        return {"dich": self.dich, "loai": "novnc"}


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "1000")
    monkeypatch.delenv("WEB2API_TIN_PROXY", raising=False)
    gioi_han.khoi_dong()
    yield
    novnc._VE.clear(); novnc._PHIEN.clear(); novnc._HO_SO.clear()


def _qt():
    return {"Authorization": f"Bearer {KHOA_QT}"}


def test_cap_ve_xem_cho_dich_noi_bo_va_khong_lo_dich(moi_truong):
    with TestClient(app) as c:
        c.app.state.so.dang_chay[MODULE] = ModGia()
        r = c.post(f"/api/trinh-duyet/{MODULE}/xem", headers=_qt())
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["url"].startswith("/novnc/dang-nhap?ve=") and j["ho_so"] == novnc.ho_so_xem(MODULE)
        assert "10.77.0.9" not in r.text and "6080" not in r.text          # đích nội bộ không ra ngoài
        assert (MODULE, j["ho_so"]) in novnc._HO_SO and novnc._bam(j["ve"]) in novnc._VE


def test_ho_so_xem_khop_mau_ho_so_va_on_dinh():
    novnc.kiem_tra_ho_so_id(novnc.ho_so_xem(MODULE))
    assert novnc.ho_so_xem(MODULE) == novnc.ho_so_xem(MODULE) != novnc.ho_so_xem("mod-khac")


def test_khong_khoa_va_khoa_khach_bi_chan(moi_truong):
    with TestClient(app) as c:
        c.app.state.so.dang_chay[MODULE] = ModGia()
        assert c.post(f"/api/trinh-duyet/{MODULE}/xem").status_code == 401
        rk = c.post("/api/khoa-khach", json={"nhan": "nv276-khach"}, headers=_qt())
        khach = rk.json()["khoa"]["khoa"]
        assert c.post(f"/api/trinh-duyet/{MODULE}/xem", headers={"Authorization": f"Bearer {khach}"}).status_code == 403


def test_module_khong_ho_tro_thi_501_va_khong_co_thi_404(moi_truong):
    with TestClient(app) as c:
        c.app.state.so.dang_chay["mod-khong-xem"] = object()
        assert c.post("/api/trinh-duyet/mod-khong-xem/xem", headers=_qt()).status_code == 501
        assert c.post("/api/trinh-duyet/mod-khong-co/xem", headers=_qt()).status_code == 404


@pytest.mark.parametrize("dich", ["http://127.0.0.1:6080", "http://169.254.169.254/", "ftp://10.77.0.9:6080", "http://10.77.0.9:6080/?x=1"])
def test_dich_bi_chan_hoac_sai_dang_thi_502(moi_truong, dich):
    with TestClient(app) as c:
        c.app.state.so.dang_chay[MODULE] = ModGia(dich=dich)
        r = c.post(f"/api/trinh-duyet/{MODULE}/xem", headers=_qt())
        assert r.status_code == 502, (dich, r.status_code, r.text)


def test_module_loi_thi_502_khong_lo_chi_tiet(moi_truong):
    with TestClient(app) as c:
        c.app.state.so.dang_chay[MODULE] = ModGia(loi=RuntimeError("khoá bí mật abc123"))
        r = c.post(f"/api/trinh-duyet/{MODULE}/xem", headers=_qt())
        assert r.status_code == 502 and "abc123" not in r.text


def test_dich_vu_khoi_dong_lai_doi_dia_chi_thi_thu_hoi_phien_cu_va_ghim_dich_moi(moi_truong):
    with TestClient(app) as c:
        mod = ModGia(dich="http://10.77.0.9:6080")
        c.app.state.so.dang_chay[MODULE] = mod
        v1 = c.post(f"/api/trinh-duyet/{MODULE}/xem", headers=_qt()).json()["ve"]
        mod.dich = "http://10.77.0.10:6080"                       # container khởi động lại, IP đổi
        r2 = c.post(f"/api/trinh-duyet/{MODULE}/xem", headers=_qt())
        assert r2.status_code == 200
        assert novnc._HO_SO[(MODULE, novnc.ho_so_xem(MODULE))]["ip"] == "10.77.0.10"


def _tai_module_may_dang_nhap():
    import importlib.util
    from pathlib import Path

    goc = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("may_dang_nhap_nv276", str(goc / "modules" / "may-dang-nhap" / "module.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_module_may_dang_nhap_noi_dich_xem_tu_dia_chi_dich_vu_cong_6080():
    import asyncio

    m = _tai_module_may_dang_nhap()
    mod = [getattr(m, n) for n in dir(m) if n.startswith("Module") or n.endswith("Module")]
    cls = next(c for c in mod if isinstance(c, type) and hasattr(c, "trinh_duyet_xem"))
    doi_tuong = cls.__new__(cls)
    doi_tuong._url = "http://web2api-may-dang-nhap:8010"
    assert asyncio.run(doi_tuong.trinh_duyet_xem()) == {"dich": "http://web2api-may-dang-nhap:6080", "loai": "novnc"}
    doi_tuong._url = "ftp://x"
    with pytest.raises(RuntimeError):
        asyncio.run(doi_tuong.trinh_duyet_xem())


def test_cau_hinh_novnc_cua_dich_vu_mac_dinh_van_an_toan_va_co_cong_tac_cuc_bo():
    """Mặc định x11vnc vẫn đòi mật khẩu và noVNC chỉ nghe 127.0.0.1; chỉ đổi khi tệp ghi đè CỤC BỘ đặt biến."""
    from pathlib import Path

    conf = (Path(__file__).resolve().parents[1] / "dich-vu" / "may-dang-nhap" / "supervisord-novnc.conf").read_text(encoding="utf-8")
    assert "-passwdfile /khoa/mat-khau-vnc" in conf and "-listen 127.0.0.1" in conf
    assert '${CAPTCHA_SOLVER_NOVNC_NGHE:-127.0.0.1}:6080' in conf              # mặc định loopback
    assert 'CAPTCHA_SOLVER_NOVNC_MAT_KHAU" = "0"' in conf                        # chỉ "0" mới bỏ mật khẩu
    assert "0.0.0.0:6080" not in conf                                            # không cứng 0.0.0.0 trong repo
