"""NV119 — thêm tài khoản BẰNG ĐĂNG NHẬP khi module CHƯA có tài khoản nào (module thật khai trường tài khoản BẮT BUỘC).

Lỗi thật (Claude, 18/10): các module web (chatgpt, grok, claude, gemini) khai trường tài khoản/cookie `bat_buoc`, nên chưa có tài khoản
thì module KHÔNG chạy (không vào `dang_chay`). Nhưng cờ «tự đăng nhập» (`/api/tinh-nang`) và các đường đăng nhập đều đòi module ĐANG
CHẠY → muốn thêm tài khoản ĐẦU TIÊN bằng đăng nhập thì phải có sẵn một tài khoản. Test cũ dùng module giả KHÔNG có trường bắt buộc nên
không bắt được. Thêm nữa: đường «Trực tiếp» không lưu kết quả vào cấu hình (chỉ vào bộ nhớ module) nên khởi động lại là mất.
Yêu cầu: (1) cờ suy từ CẤU TRÚC lớp module (không cần đang chạy); (2) đăng nhập chạy được trên module chưa cấu hình; (3) đăng nhập thành công thì
chứng chỉ được LƯU qua đường cấu hình chung (cùng đường `/tai-dung`) → module chạy, và còn sau khi khởi động lại; (4) thất bại thì không lưu gì;
(5) mật khẩu/chứng chỉ không lọt log/phản hồi.
"""
import json
import logging
import textwrap
import time

import pytest
from fastapi.testclient import TestClient

from app import so_module
from app.main import app

KHOA_QT = "khoa-quan-tri-nv119-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
MK_GIA = "MatKhauGia-NV119-xyz"
CC_GIA = "chung-chi-gia-nv119-0001"
ID = "web-gia-bat-buoc"

MA = textwrap.dedent('''
    from app.tai_khoan_hang_loat import KetQuaDangNhap

    class Module:
        def __init__(self, manifest):
            self.manifest = manifest
            self.kho = None
            self._tk = []
        def nap(self, cau_hinh):
            self._tk = list((cau_hinh or {}).get("tai_khoan") or [])
        async def cai(self, cau_hinh):
            ds = list((cau_hinh or {}).get("tai_khoan") or [])
            return {"ok": bool(ds), "ket_qua_tung_khoa": [], "loi": None if ds else "thieu", "cau_hinh_luu": cau_hinh}
        async def models(self):
            return [{"id": "webgia/m1", "object": "model", "owned_by": "gia"}] if self._tk else []
        async def suc_khoe(self): return {"ok": True}
        async def dung(self): pass
        async def dang_nhap(self, tai_khoan):
            try:
                mk = tai_khoan.mat_khau.lay() if tai_khoan.mat_khau else ""
                if mk == "''' + MK_GIA + '''":
                    return KetQuaDangNhap(True, cookie="''' + CC_GIA + '''")
                return KetQuaDangNhap(False, ma_loi="sai_mat_khau")
            finally:
                tai_khoan.xoa_bi_mat()
''')

MANIFEST = {
    "id": ID, "ten": "Web giả bắt buộc", "mo_ta": "x", "phien_ban": "1.0.0", "loai": "ai", "hang": "A", "giao_thuc": "openai",
    "tien_to": "webgia", "cung_cap": ["chat"],
    "truong_cau_hinh": [{"khoa": "tai_khoan", "nhan": "Tài khoản", "kieu": "danh_sach_bi_mat", "bat_buoc": True}],
    "tai_nguyen": {"mang_ra": []},
}


@pytest.fixture
def moi(tmp_path, monkeypatch):
    data = tmp_path / "data"
    thu_muc = data / "modules" / ID
    thu_muc.mkdir(parents=True)
    (thu_muc / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (thu_muc / "module.py").write_text(MA, encoding="utf-8")
    monkeypatch.setenv("WEB2API_DATA", str(data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return data


def _mo():
    return TestClient(app, raise_server_exceptions=False)


def _doi_luot(c, toi_da=10.0):
    cuoi = time.monotonic() + toi_da
    while time.monotonic() < cuoi:
        r = c.get("/api/tai-khoan/dang-nhap-hang-loat/trang-thai", headers=QT)
        if r.status_code == 200 and r.json().get("trang_thai_luot") == "hoan_thanh":
            return r.json()
        time.sleep(0.05)
    raise AssertionError("lượt đăng nhập không xong")


def _module(c):
    return next(m for m in c.get("/api/tinh-nang", headers=QT).json()["modules"] if m["id"] == ID)


def test_co_dang_nhap_suy_tu_lop_khi_module_chua_chay(moi):
    with _mo() as c:
        m = _module(c)
        assert m["da_lien_ket"] is False, "không có tài khoản nào → module chưa chạy"
        assert m["dang_nhap_tu_dong"] is True, "cờ phải suy từ lớp module, không cần đang chạy"


def test_dang_nhap_thanh_cong_luu_tai_khoan_va_module_chay(moi, caplog):
    caplog.set_level(logging.DEBUG)
    with _mo() as c:
        r = c.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT,
                   json={"module_id": ID, "van_ban": f"nguoi@example.com|{MK_GIA}"})
        assert r.status_code == 200, r.text
        luot = _doi_luot(c)
        assert luot["ket_qua"][0]["trang_thai"] == "xong", luot
        assert _module(c)["da_lien_ket"] is True, "đăng nhập xong module phải chạy (chứng chỉ đã lưu qua cấu hình)"
        ids = [x["id"] for x in c.get("/v1/models", headers=QT).json()["data"]]
        assert "webgia/m1" in ids
        for nguon in (r.text, json.dumps(luot), caplog.text):
            assert MK_GIA not in nguon and CC_GIA not in nguon, "bí mật lọt ra log/phản hồi"
    # Khởi động lại (đối tượng ứng dụng mới trên cùng thư mục dữ liệu): tài khoản còn.
    with _mo() as c2:
        assert _module(c2)["da_lien_ket"] is True
        assert "webgia/m1" in [x["id"] for x in c2.get("/v1/models", headers=QT).json()["data"]]


def test_dang_nhap_that_bai_khong_luu_gi(moi):
    with _mo() as c:
        r = c.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT,
                   json={"module_id": ID, "van_ban": "nguoi@example.com|MatKhauSai123456"})
        assert r.status_code == 200, r.text
        luot = _doi_luot(c)
        assert luot["ket_qua"][0]["trang_thai"] == "loi"
        assert _module(c)["da_lien_ket"] is False
        assert not (moi / "bi_mat").exists() or CC_GIA not in "".join(p.read_text(errors="ignore") for p in (moi / "bi_mat").rglob("*") if p.is_file())


def test_khong_khoa_va_khoa_khach_van_bi_chan(moi):
    with _mo() as c:
        r = c.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", json={"module_id": ID, "van_ban": f"a@example.com|{MK_GIA}"})
        assert r.status_code == 401


# ───────────── nap() bất đồng bộ (lỗi thật của grok-web): lõi gọi đồng bộ nên hàm KHÔNG BAO GIỜ chạy ─────────────

MA_NAP_ASYNC = textwrap.dedent('''
    import asyncio

    class Module:
        def __init__(self, manifest):
            self.manifest = manifest; self.kho = None; self._tk = []
        async def nap(self, cau_hinh):
            self._tk = list((cau_hinh or {}).get("tai_khoan") or [])
            if (cau_hinh or {}).get("cho_that"):
                await asyncio.sleep(0.01)
        async def cai(self, cau_hinh):
            return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": cau_hinh}
        async def models(self):
            return [{"id": "napgia/m1", "object": "model", "owned_by": "gia"}] if self._tk else []
        async def suc_khoe(self): return {"ok": True}
        async def dung(self): pass
''')


def _cai_module_nap(tmp_path, monkeypatch, ma, id_="nap-async-gia", cau_hinh_them=None):
    data = tmp_path / "data"
    thu_muc = data / "modules" / id_
    thu_muc.mkdir(parents=True)
    man = {"id": id_, "ten": "Nap async", "mo_ta": "x", "phien_ban": "1.0.0", "loai": "ai", "hang": "A", "giao_thuc": "openai",
           "tien_to": "napgia", "cung_cap": ["chat"], "tai_nguyen": {"mang_ra": []},
           "truong_cau_hinh": [{"khoa": "tai_khoan", "nhan": "Tài khoản", "kieu": "danh_sach_bi_mat", "bat_buoc": True}]}
    (thu_muc / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    (thu_muc / "module.py").write_text(ma, encoding="utf-8")
    monkeypatch.setenv("WEB2API_DATA", str(data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return id_


def test_nap_async_khong_cho_gi_van_duoc_nap(tmp_path, monkeypatch):
    id_ = _cai_module_nap(tmp_path, monkeypatch, MA_NAP_ASYNC)
    with _mo() as c:
        r = c.post(f"/api/modules/{id_}/cai", headers=QT, json={"cau_hinh": {"tai_khoan": ["tk-gia-nap-001"]}})
        assert r.status_code == 200 and r.json()["ok"], r.text
        assert "napgia/m1" in [x["id"] for x in c.get("/v1/models", headers=QT).json()["data"]], \
            "nap() async không chờ gì phải được chạy xong — cấu hình đã lưu phải nạp vào thể hiện phục vụ"
    with _mo() as c2:   # khởi động lại: nạp từ cấu hình đã lưu
        assert "napgia/m1" in [x["id"] for x in c2.get("/v1/models", headers=QT).json()["data"]]


def test_nap_async_cho_that_bi_tu_choi_to_tieng(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    id_ = _cai_module_nap(tmp_path, monkeypatch, MA_NAP_ASYNC.replace('"cho_that"', '"tai_khoan_x"').replace("if (cau_hinh or {}).get(\"tai_khoan_x\"):", "if True:"))
    with _mo() as c:
        c.post(f"/api/modules/{id_}/cai", headers=QT, json={"cau_hinh": {"tai_khoan": ["tk-gia-nap-002"]}})
        assert "napgia/m1" not in [x["id"] for x in c.get("/v1/models", headers=QT).json()["data"]], \
            "nap() thật sự chờ (await) không được chấp nhận: module không được coi là đã nạp"
        assert "nap" in caplog.text.lower(), "phải có log nói rõ lý do (không im lặng)"
