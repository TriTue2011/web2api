"""NV283 — trạng thái đăng nhập thật + loại suy từ module (backend).

Kiểm với module GIẢ (conftest chặn Internet), tài khoản GIẢ dạng
``nv283-...@example.com`` — không đăng nhập thật, không bí mật thật.
Mỗi đường đăng nhập phải ghi kết quả đúng một lần qua hàm bọc chung
``kho_tai_khoan.ghi_ket_qua_dang_nhap_tu_ket_qua`` (sổ phụ một mục/tài
khoản nên «một lần» nghĩa là mục cuối đúng, không ghi chéo tài khoản).
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

_KHOA_QT = "khoa-qt-nv283-0123456789abcdef"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}
_MODULE = "chatgpt-web"  # id module giả trùng tên module thật để đo loai_hien_thi=Web


class _KetQua:
    """Kết quả hợp đồng đăng nhập (object, như module thật trả)."""

    def __init__(self, thanh_cong, ma_loi="", can_nguoi=False, cho_2fa=False):
        self.thanh_cong = thanh_cong
        self.ma_loi = ma_loi
        self.can_nguoi = can_nguoi
        self.cho_2fa = cho_2fa
        self.cookie = None
        self.ma_trang_thai = None


class _ModuleGia:
    """Module giả: dang_nhap theo bảng email → kết quả; không gọi mạng."""

    def __init__(self, bang):
        self._bang = bang  # email lower → _KetQua
        self.goi = []

    async def dang_nhap(self, dong):
        email = str(dong.email or "").strip().lower()
        self.goi.append(email)
        return self._bang.get(email, _KetQua(False, ma_loi="loi_module"))

    def liet_ke_tai_khoan(self):
        return []


@pytest.fixture()
def moi_truong(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.so_module import SoModule

    data_dir = tmp_path / "data-loi"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", _KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        yield client, so


def _cai_module_gia(so, fake):
    fake.manifest = {"id": _MODULE, "nhom": {"id": "chatgpt", "ten": "ChatGPT"}}
    so.catalog[_MODULE] = SimpleNamespace(tien_to=_MODULE, manifest=fake.manifest)
    so.dang_chay[_MODULE] = fake


def _them(client, email, module_id=None):
    body = {"email": email, "mat_khau": "MatKhauGia123!"}
    if module_id:
        body["module_id"] = module_id
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json=body)
    assert r.status_code == 200, r.text
    return r.json()["tai_khoan"]


def _da_luu_theo_id(client, tk_id):
    r = client.get("/api/tai-khoan/da-luu", headers=_QT)
    assert r.status_code == 200, r.text
    for b in r.json()["tai_khoan"]:
        if b["id"] == tk_id:
            return b
    raise AssertionError(f"không thấy {tk_id} trong da-luu")


def _cho_luot_xong(client, timeout=15.0):
    bat_dau = time.time()
    while time.time() - bat_dau < timeout:
        r = client.get("/api/tai-khoan/dang-nhap-hang-loat/trang-thai", headers=_QT)
        assert r.status_code == 200, r.text
        if r.json()["trang_thai_luot"] == "hoan_thanh":
            return r.json()
        time.sleep(0.05)
    raise AssertionError("lượt hàng loạt không xong trong hạn")


# ── Một tài khoản ────────────────────────────────────────────────────────────

def test_dang_nhap_mot_thanh_cong_ghi_ok_va_loai_web(moi_truong):
    client, so = moi_truong
    _cai_module_gia(so, _ModuleGia({"ok-nv283@example.com": _KetQua(True)}))
    bk = _them(client, "ok-nv283@example.com")
    r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/dang-nhap", headers=_QT,
                    json={"module_id": _MODULE})
    assert r.status_code == 200, r.text
    assert r.json()["thanh_cong"] is True
    sau = _da_luu_theo_id(client, bk["id"])
    assert sau["dang_nhap"]["trang_thai"] == "ok"
    assert sau["dang_nhap"]["co_phien"] is True
    assert sau["dang_nhap"]["luc"] > 0
    assert sau["loai_hien_thi"] == "Web"


def test_dang_nhap_mot_can_nguoi_va_2fa_ghi_can_xu_ly(moi_truong):
    client, so = moi_truong
    _cai_module_gia(so, _ModuleGia({
        "captcha-nv283@example.com": _KetQua(False, ma_loi="thu_thach_truoc_khi_dien"),
        "hai-fa-nv283@example.com": _KetQua(False, cho_2fa=True),
    }))
    for email in ("captcha-nv283@example.com", "hai-fa-nv283@example.com"):
        bk = _them(client, email)
        r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/dang-nhap", headers=_QT,
                        json={"module_id": _MODULE})
        assert r.status_code == 200, r.text
        assert _da_luu_theo_id(client, bk["id"])["dang_nhap"]["trang_thai"] == "can_xu_ly"


def test_dang_nhap_mot_sai_mat_khau_ghi_loi(moi_truong):
    client, so = moi_truong
    _cai_module_gia(so, _ModuleGia({"sai-nv283@example.com": _KetQua(False, ma_loi="sai_mat_khau")}))
    bk = _them(client, "sai-nv283@example.com")
    r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/dang-nhap", headers=_QT,
                    json={"module_id": _MODULE})
    assert r.status_code == 200, r.text
    sau = _da_luu_theo_id(client, bk["id"])["dang_nhap"]
    assert sau["trang_thai"] == "loi"
    assert sau["ma_loi"] == "sai_mat_khau"
    assert sau["co_phien"] is False


def test_module_khong_ho_tro_khong_ghi_ket_qua(moi_truong):
    client, so = moi_truong
    fake = SimpleNamespace()  # không có dang_nhap
    _cai_module_gia(so, fake)
    bk = _them(client, "khong-ho-tro-nv283@example.com")
    r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/dang-nhap", headers=_QT,
                    json={"module_id": _MODULE})
    assert r.status_code == 502, r.text
    # Đăng nhập chưa từng chạy → vẫn «chua», không bị ghi thành lỗi.
    assert _da_luu_theo_id(client, bk["id"])["dang_nhap"]["trang_thai"] == "chua"


def test_tam_dung_khong_dang_nhap_khong_ghi(moi_truong):
    client, so = moi_truong
    _cai_module_gia(so, _ModuleGia({"tam-nv283@example.com": _KetQua(True)}))
    bk = _them(client, "tam-nv283@example.com")
    r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/tam-dung", headers=_QT,
                    json={"tam_dung": True})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/dang-nhap", headers=_QT,
                    json={"module_id": _MODULE})
    assert r.status_code == 403, r.text
    sau = _da_luu_theo_id(client, bk["id"])
    assert sau["trang_thai"] == "tam_dung"
    assert sau["dang_nhap"]["trang_thai"] == "chua"


def test_loi_ghi_so_khong_hong_ket_qua_dang_nhap(moi_truong, monkeypatch, caplog):
    """nv45: lỗi ghi sổ chỉ log.warning có kiểu lỗi, kết quả vẫn thành công."""
    import logging

    from app import kho_tai_khoan as ktk

    client, so = moi_truong
    _cai_module_gia(so, _ModuleGia({"ok2-nv283@example.com": _KetQua(True)}))
    bk = _them(client, "ok2-nv283@example.com")

    def _hong(*_a, **_k):
        raise OSError("kho phu gia hong")

    monkeypatch.setattr(ktk, "_ghi_ket_qua", _hong)
    with caplog.at_level(logging.WARNING, logger="web2api.kho_tai_khoan"):
        r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/dang-nhap", headers=_QT,
                        json={"module_id": _MODULE})
    assert r.status_code == 200, r.text
    assert r.json()["thanh_cong"] is True
    assert any("ghi kết quả đăng nhập hỏng" in rec.message for rec in caplog.records)


# ── Hàng loạt ────────────────────────────────────────────────────────────────

# Thứ tự xen kẽ ok/hỏng: LuotChay dừng cả hàng sau 2 hỏng LIÊN TIẾP
# (mặc định) nên b3/b4 không được đứng cạnh nhau — nếu không b5 bị bo_qua
# đúng luật, không phải lỗi ghi sổ.
_BANG_5 = {
    "b1-nv283@example.com": _KetQua(True),
    "b3-nv283@example.com": _KetQua(False, can_nguoi=True, ma_loi="can_nguoi"),
    "b2-nv283@example.com": _KetQua(True),
    "b4-nv283@example.com": _KetQua(False, ma_loi="sai_mat_khau"),
    "b5-nv283@example.com": _KetQua(True),
}
_MONG_5 = {
    "b1-nv283@example.com": "ok",
    "b2-nv283@example.com": "ok",
    "b3-nv283@example.com": "can_xu_ly",
    "b4-nv283@example.com": "loi",
    "b5-nv283@example.com": "ok",
}


def _kiem_5(client):
    r = client.get("/api/tai-khoan/da-luu", headers=_QT)
    assert r.status_code == 200, r.text
    ds = {b["id"]: b for b in r.json()["tai_khoan"]}
    assert r.json()["can_xu_ly"] == 2  # b3 can_xu_ly + b4 loi
    return ds


def test_hang_loat_theo_module_5_tai_khoan_khong_ghi_cheo(moi_truong):
    client, so = moi_truong
    fake = _ModuleGia(_BANG_5)
    _cai_module_gia(so, fake)
    ids = {}
    for email in _BANG_5:
        ids[email] = _them(client, email, module_id=_MODULE)["id"]
    r = client.post("/api/tai-khoan/da-luu/dang-nhap-theo-module", headers=_QT,
                    json={"module_id": _MODULE, "module_so_huu": _MODULE,
                          "chi_chua_co_ho_so": True})
    assert r.status_code == 200, r.text
    _cho_luot_xong(client)
    ds = _kiem_5(client)
    for email, mong in _MONG_5.items():
        assert ds[ids[email]]["dang_nhap"]["trang_thai"] == mong, email
    assert fake.goi and len(fake.goi) == 5


def test_hang_loat_theo_module_chay_lai_khong_ghi_trung_sai(moi_truong):
    client, so = moi_truong
    _cai_module_gia(so, _ModuleGia(_BANG_5))
    ids = {email: _them(client, email, module_id=_MODULE)["id"] for email in _BANG_5}
    for _lan in range(2):
        r = client.post("/api/tai-khoan/da-luu/dang-nhap-theo-module", headers=_QT,
                        json={"module_id": _MODULE, "module_so_huu": _MODULE,
                              "chi_chua_co_ho_so": True})
        assert r.status_code == 200, r.text
        _cho_luot_xong(client)
    ds = _kiem_5(client)
    for email, mong in _MONG_5.items():
        assert ds[ids[email]]["dang_nhap"]["trang_thai"] == mong, email


def test_hang_loat_dan_van_ban_ghi_qua_quan_ly_luot_chay(moi_truong):
    client, so = moi_truong
    _cai_module_gia(so, _ModuleGia(_BANG_5))
    ids = {email: _them(client, email)["id"] for email in _BANG_5}
    van_ban = "\n".join(f"{email}|MatKhauGia123!" for email in _BANG_5)
    r = client.post("/api/tai-khoan/dang-nhap-hang-loat/bat-dau", headers=_QT,
                    json={"module_id": _MODULE, "van_ban": van_ban})
    assert r.status_code == 200, r.text
    _cho_luot_xong(client)
    ds = _kiem_5(client)
    for email, mong in _MONG_5.items():
        assert ds[ids[email]]["dang_nhap"]["trang_thai"] == mong, email


def test_dang_nhap_tu_dong_ghi_qua_quan_ly_luot_chay(moi_truong):
    client, so = moi_truong
    _cai_module_gia(so, _ModuleGia({
        "td1-nv283@example.com": _KetQua(True),
        "td2-nv283@example.com": _KetQua(False, ma_loi="sai_mat_khau"),
    }))
    id1 = _them(client, "td1-nv283@example.com")["id"]
    id2 = _them(client, "td2-nv283@example.com")["id"]
    r = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=_QT,
                    json={"module_id": _MODULE, "kieu": "truc_tiep",
                          "tai_khoan": [
                              {"email": "td1-nv283@example.com", "mat_khau": "MatKhauGia123!"},
                              {"email": "td2-nv283@example.com", "mat_khau": "MatKhauGia123!"},
                          ]})
    assert r.status_code == 200, r.text
    _cho_luot_xong(client)
    assert _da_luu_theo_id(client, id1)["dang_nhap"]["trang_thai"] == "ok"
    assert _da_luu_theo_id(client, id2)["dang_nhap"]["trang_thai"] == "loi"


def test_luot_chay_tuan_thu_gian_cach(monkeypatch):
    """LuotChay giãn cách giữa các dòng (đồng hồ/ngủ giả, không chờ thật)."""
    from app.tai_khoan_hang_loat import DongDaTach, LuotChay
    from app.tai_khoan_hang_loat import BiMatTam

    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "5")
    ngu = []
    dong_ho = [1000.0]

    async def _ngu(giay):
        ngu.append(giay)

    async def _ham(_dong):
        from app.tai_khoan_hang_loat import KetQuaDangNhap
        return KetQuaDangNhap(True)

    dong = []
    for email in ("g1-nv283@example.com", "g2-nv283@example.com", "g3-nv283@example.com"):
        dong.append(DongDaTach(0, email, "mat_khau", mat_khau=BiMatTam("MatKhauGia123!")))
    luot = LuotChay(dong, _ham, dong_ho=lambda: dong_ho[0], ngu=_ngu)
    asyncio.run(luot.chay())
    assert ngu == [5.0, 5.0]


# ── Tự đăng nhập lại + 401 + MCP ─────────────────────────────────────────────

def test_tu_dang_nhap_lai_ghi_ok(moi_truong):
    import asyncio

    from app import kho_tai_khoan as ktk

    client, so = moi_truong
    fake = _ModuleGia({"tu-nv283@example.com": _KetQua(True)})
    bk = _them(client, "tu-nv283@example.com")
    kq = asyncio.run(ktk.thu_dang_nhap_lai(module=fake, module_id=_MODULE,
                                          dinh_danh="tu-nv283@example.com"))
    assert kq == {"thu": True, "thanh_cong": True}
    assert _da_luu_theo_id(client, bk["id"])["dang_nhap"]["trang_thai"] == "ok"


def test_luot_dung_thay_401_danh_dau_phien_het_han(moi_truong):
    from app import kho_tai_khoan as ktk
    from app import luong_dung_tai_khoan as ldtk

    client, so = moi_truong
    _cai_module_gia(so, _ModuleGia({"het-nv283@example.com": _KetQua(True)}))
    bk = _them(client, "het-nv283@example.com")
    ktk.ghi_ket_qua_dang_nhap(bk["id"], "thanh_cong", co_phien=True)
    assert _da_luu_theo_id(client, bk["id"])["dang_nhap"]["trang_thai"] == "ok"
    assert ldtk.ghi_luot(bk["id"], ma=401) is True
    sau = _da_luu_theo_id(client, bk["id"])["dang_nhap"]
    assert sau["trang_thai"] == "can_xu_ly"
    # Ghi lại thành công thì hết cờ hết hạn.
    ktk.ghi_ket_qua_dang_nhap(bk["id"], "thanh_cong", co_phien=True)
    assert _da_luu_theo_id(client, bk["id"])["dang_nhap"]["trang_thai"] == "ok"


def test_mcp_dang_nhap_qua_khoi_phuc_ghi_dung_mot_lan(moi_truong):
    import asyncio

    from app import cong_mcp_cong_cu as mcp

    client, so = moi_truong

    class _KhoiPhuc:
        async def khoi_phuc_tu_dong(self, tk_id, email, nghi_t1_giay=0.0):
            return {"thanh_cong": True}

    bk = _them(client, "mcp-nv283@example.com")
    so_gia = SimpleNamespace(dang_chay={_MODULE: _KhoiPhuc()})
    phien = {"phien_id": "phien-gia-nv283", "tai_khoan_id": bk["id"],
             "trang_thai": "dang_chay", "ma_loi": "", "huong_dan": "",
             "bat_dau_luc": "2026-11-02T00:00:00Z", "ket_thuc_luc": None}
    asyncio.run(mcp._chay_dang_nhap(phien, so_gia, bk["id"], _MODULE))
    assert phien["trang_thai"] == "thanh_cong"
    assert _da_luu_theo_id(client, bk["id"])["dang_nhap"]["trang_thai"] == "ok"


# ── Loại hiển thị + hợp đồng ─────────────────────────────────────────────────

def test_30_tai_khoan_chatgpt_web_loai_web_khong_chua_phan_loai(moi_truong):
    client, so = moi_truong
    _cai_module_gia(so, _ModuleGia({}))
    for i in range(30):
        _them(client, f"loai-{i:02d}-nv283@example.com", module_id=_MODULE)
    r = client.get("/api/tai-khoan/da-luu", headers=_QT)
    assert r.status_code == 200, r.text
    ds = r.json()["tai_khoan"]
    assert len(ds) == 30
    assert all(b["loai_hien_thi"] == "Web" for b in ds)
    assert "Chưa phân loại" not in r.text
    assert "can_xu_ly" in r.json()


def test_loai_hien_thi_theo_kieu_bi_mat_khi_khong_module(moi_truong):
    from app import kho_tai_khoan as ktk

    client, _so = moi_truong
    bk = _them(client, "api-nv283@example.com")
    # Đổi kiểu bí mật qua kho trực tiếp (đường HTTP chưa cho đổi kiểu).
    ktk.luu_tai_khoan(email="api-nv283@example.com", mat_khau=None,
                      bi_mat="KhoaGiaNv2830123456789", kieu_bi_mat="khoa_api",
                      id=bk["id"])
    sau = _da_luu_theo_id(client, bk["id"])
    assert sau["loai_hien_thi"] == "API"
    assert sau["dang_nhap"]["trang_thai"] == "chua"


def test_ghi_ket_qua_sai_hinh_va_id_la(moi_truong):
    from app import kho_tai_khoan as ktk

    _client, _so = moi_truong
    with pytest.raises(ktk.LoiKhoTaiKhoan):
        ktk.ghi_ket_qua_dang_nhap("tk-khong-co", "khong_hop_le")
    assert ktk.ghi_ket_qua_dang_nhap("tk-khong-co", "thanh_cong") is None
