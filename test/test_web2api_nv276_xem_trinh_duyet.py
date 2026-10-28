"""NV276: XEM trình duyệt đang đăng nhập tự động bằng ảnh chụp (như c2a mở trình duyệt cho chủ máy theo dõi), KHÔNG mở thêm dịch vụ nào.
Dịch vụ chụp trang qua API có khoá sẵn; module chuyển ảnh JPEG; lõi chỉ cho quản trị, kiểm định dạng và kích thước, không lưu, không đệm."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import gioi_han, novnc
from app.main import app

GOC = Path(__file__).resolve().parents[1]
KHOA_QT = "khoa-quan-tri-nv276-xem-123456"
MODULE = "mod-xem-nv276"
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 200


class ModGia:
    def __init__(self, anh=JPEG, loi=None):
        self.anh, self.loi, self.so_lan = anh, loi, 0

    async def man_hinh(self):
        self.so_lan += 1
        if self.loi:
            raise self.loi
        return self.anh


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "1000")
    monkeypatch.delenv("WEB2API_TIN_PROXY", raising=False)
    gioi_han.khoi_dong()
    yield


def _qt():
    return {"Authorization": f"Bearer {KHOA_QT}"}


def test_tra_anh_jpeg_khong_dem_va_khong_doan_noi_dung(moi_truong):
    with TestClient(app) as c:
        c.app.state.so.dang_chay[MODULE] = ModGia()
        r = c.get(f"/api/trinh-duyet/{MODULE}/man-hinh", headers=_qt())
        assert r.status_code == 200 and r.content == JPEG
        assert r.headers["content-type"] == "image/jpeg" and r.headers["cache-control"] == "no-store"
        assert r.headers["x-content-type-options"] == "nosniff"


def test_chua_co_trinh_duyet_thi_404(moi_truong):
    with TestClient(app) as c:
        c.app.state.so.dang_chay[MODULE] = ModGia(anh=None)
        assert c.get(f"/api/trinh-duyet/{MODULE}/man-hinh", headers=_qt()).status_code == 404


def test_khong_khoa_va_khoa_khach_bi_chan(moi_truong):
    with TestClient(app) as c:
        c.app.state.so.dang_chay[MODULE] = ModGia()
        assert c.get(f"/api/trinh-duyet/{MODULE}/man-hinh").status_code == 401
        khach = c.post("/api/khoa-khach", json={"nhan": "nv276-khach"}, headers=_qt()).json()["khoa"]["khoa"]
        assert c.get(f"/api/trinh-duyet/{MODULE}/man-hinh", headers={"Authorization": f"Bearer {khach}"}).status_code == 403


def test_module_khong_ho_tro_501_khong_co_404(moi_truong):
    with TestClient(app) as c:
        c.app.state.so.dang_chay["mod-khong-xem"] = object()
        assert c.get("/api/trinh-duyet/mod-khong-xem/man-hinh", headers=_qt()).status_code == 501
        assert c.get("/api/trinh-duyet/mod-khong-co/man-hinh", headers=_qt()).status_code == 404


@pytest.mark.parametrize("anh", [b"<html>khong phai anh</html>", b"\xff\xd8" + b"x" * (novnc.TRAN_ANH_MAN_HINH + 10), "chuoi"])
def test_anh_sai_dinh_dang_hoac_qua_lon_bi_chan_502(moi_truong, anh):
    with TestClient(app) as c:
        c.app.state.so.dang_chay[MODULE] = ModGia(anh=anh)
        assert c.get(f"/api/trinh-duyet/{MODULE}/man-hinh", headers=_qt()).status_code == 502


def test_module_loi_502_khong_lo_chi_tiet(moi_truong):
    with TestClient(app) as c:
        c.app.state.so.dang_chay[MODULE] = ModGia(loi=RuntimeError("khoá bí mật abc123"))
        r = c.get(f"/api/trinh-duyet/{MODULE}/man-hinh", headers=_qt())
        assert r.status_code == 502 and "abc123" not in r.text


def test_so_module_cong_bo_man_hinh_theo_cau_truc(moi_truong):
    with TestClient(app) as c:
        c.app.state.so.dang_chay[MODULE] = ModGia()
        # cờ suy từ cấu trúc (module có hàm man_hinh), không từ tên nhà cung cấp
        assert callable(getattr(c.app.state.so.dang_chay[MODULE], "man_hinh", None))


# ── dịch vụ máy đăng nhập: chụp trang đang đăng nhập ─────────────────────────────────────────
def _tai_pool():
    """Nạp ``src.browser_pool`` của dịch vụ (gói nội bộ, import tương đối). Trình điều khiển trình duyệt (patchright) không cài trong
    môi trường kiểm thử của lõi nên được thay bằng bản rỗng; chỉ phần chụp ảnh/hồ sơ được kiểm, KHÔNG khởi chạy trình duyệt."""
    import types

    goc = str(GOC / "dich-vu" / "may-dang-nhap")
    truoc = {k: sys.modules.get(k) for k in ("src", "src.browser_pool", "src.settings", "patchright", "patchright.async_api")}
    for k in ("src", "src.browser_pool", "src.settings"):
        sys.modules.pop(k, None)
    pk = types.ModuleType("patchright"); api = types.ModuleType("patchright.async_api")
    api.__getattr__ = lambda ten: object                                      # mọi tên import đều là lớp rỗng
    pk.async_api = api
    sys.modules["patchright"], sys.modules["patchright.async_api"] = pk, api
    sys.path.insert(0, goc)
    try:
        return importlib.import_module("src.browser_pool")
    finally:
        sys.path.remove(goc)
        for k, v in truoc.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


class _Trang:
    def __init__(self, dong=False, anh=JPEG, cham=0.0, loi=None):
        self._dong, self.anh, self.cham, self.loi, self.so_lan = dong, anh, cham, loi, 0

    def is_closed(self):
        return self._dong

    async def screenshot(self, **kw):
        self.so_lan += 1
        assert kw["type"] == "jpeg" and kw["timeout"] <= 3000          # có hạn thời gian, JPEG nhỏ
        await asyncio.sleep(self.cham)
        if self.loi:
            raise self.loi
        return self.anh


class _Ctx:
    def __init__(self, *trang):
        self.pages = list(trang)


def _pool_gia(**ho_so):
    """BrowserPool thật nhưng không khởi chạy trình duyệt: gắn thẳng hồ sơ giả."""
    from types import SimpleNamespace
    try:
        mod = _tai_pool()
    except ImportError as e:
        pytest.skip(f"không nạp được browser_pool: {e}")
    p = mod.BrowserPool()
    for ten, (ctx, dung) in ho_so.items():
        p._contexts[ten] = SimpleNamespace(ctx=ctx, page=None, headless=False, last_used=dung)
    return p


def test_chup_uu_tien_ho_so_dang_dang_nhap_va_trang_moi_nhat():
    a = _Trang(anh=b"\xff\xd8A"); b = _Trang(anh=b"\xff\xd8B"); c = _Trang(anh=b"\xff\xd8C")
    p = _pool_gia(x=(_Ctx(a), 100.0), y=(_Ctx(b, c), 50.0))
    p.dau_dang_nhap("y")
    assert asyncio.run(p.chup_man_hinh()) == b"\xff\xd8C"           # hồ sơ đang đăng nhập (y), trang mở sau cùng (c)
    assert b.so_lan == 0 and a.so_lan == 0


def test_chup_khong_co_trinh_duyet_hoac_trang_dong_thi_none_khong_nem_loi():
    assert asyncio.run(_pool_gia().chup_man_hinh()) is None
    assert asyncio.run(_pool_gia(x=(_Ctx(_Trang(dong=True)), 1.0)).chup_man_hinh()) is None
    assert asyncio.run(_pool_gia(x=(_Ctx(_Trang(loi=RuntimeError("đóng giữa chừng"))), 1.0)).chup_man_hinh()) is None


def test_hoi_don_thi_dung_lai_anh_vua_chup_khong_chup_chong():
    t = _Trang(cham=0.05)
    p = _pool_gia(x=(_Ctx(t), 1.0))

    async def dua():
        return await asyncio.gather(*(p.chup_man_hinh() for _ in range(6)))

    kq = asyncio.run(dua())
    assert t.so_lan == 1 and kq.count(JPEG) >= 1                        # sáu lời hỏi, một lần chụp
    assert asyncio.run(p.chup_man_hinh()) == JPEG and t.so_lan == 1      # trong 0,8 giây dùng lại ảnh cũ


# ── module may-dang-nhap: chuyển ảnh, chỉ nhận JPEG ───────────────────────────────────────────
def _lop_module():
    spec = importlib.util.spec_from_file_location("may_dang_nhap_nv276", str(GOC / "modules" / "may-dang-nhap" / "module.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_module_goi_man_hinh_cua_dich_vu_va_chi_nhan_jpeg():
    from unittest import mock

    m = _lop_module()
    cls = next(c for c in vars(m).values() if isinstance(c, type) and hasattr(c, "man_hinh"))
    doi_tuong = cls.__new__(cls)
    doi_tuong._url, doi_tuong._khoa = "http://web2api-may-dang-nhap:8010", "khoa-gia"

    class Res:
        def __init__(self, ma, than):
            self.status_code, self.than = ma, than

    async def chay(ma, than):
        class Client:
            def __init__(s, **kw): pass
            async def __aenter__(s): return s
            async def __aexit__(s, *a): return False
            def build_request(s, phuong_thuc, url, headers=None):
                assert phuong_thuc == "GET" and url.endswith("/v1/man-hinh") and headers["Authorization"] == "Bearer khoa-gia"
                return object()
            async def send(s, req, stream=True): return Res(ma, than)
        with mock.patch.object(m, "_kiem_url_dich_vu", new=mock.AsyncMock(return_value="http://web2api-may-dang-nhap:8010")), \
                mock.patch.object(m.httpx, "AsyncClient", Client), \
                mock.patch.object(m, "doc_phan_hoi_co_tran", new=mock.AsyncMock(side_effect=lambda r, tran_bytes: r.than)):
            return await doi_tuong.man_hinh()

    assert asyncio.run(chay(200, JPEG)) == JPEG
    assert asyncio.run(chay(404, b"")) is None
    for ma, than in ((200, b"<html>"), (500, b"loi")):
        with pytest.raises(RuntimeError):
            asyncio.run(chay(ma, than))
