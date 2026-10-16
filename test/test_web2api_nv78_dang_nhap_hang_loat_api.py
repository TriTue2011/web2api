"""Kiểm thử API dán hàng loạt + tiến độ đăng nhập lần lượt (NV67 nhóm 3, việc 7 — NV78).

Dùng `app/tai_khoan_hang_loat.py` đã có (NV74): tách dòng, `LuotChay`, hợp đồng
`KetQuaDangNhap`. Việc 7 thêm 4 đường API quản trị trong `app/tuyen.py`:

- `POST /api/tai-khoan/dan-hang-loat` — tách văn bản dán theo provider, trả
  dòng đúng/sai kèm số dòng, KHÔNG trả mật khẩu thô.
- `POST /api/tai-khoan/dang-nhap-hang-loat/bat-dau` — khởi động lượt chạy nền;
  lượt thứ hai trong lúc đang chạy → 409.
- `GET /api/tai-khoan/dang-nhap-hang-loat/trang-thai` — tiến độ từng dòng.
- `POST /api/tai-khoan/dang-nhap-hang-loat/dung` — dừng lượt đang chạy.

Module chưa triển khai `dang_nhap` → dòng đó `bo_qua` với mã
`module_chua_ho_tro_dang_nhap_tu_dong` (KHÔNG phải lỗi, không tính hỏng liên tiếp).
«Tiếp tục sau lỗi» = gọi `bat-dau` lại kèm `bo_qua_email` (email đã xong, lấy từ
`trang-thai`) — bí mật không lưu giữa các lượt nên client gửi lại văn bản.

Test chạy với module provider giả (không mạng ngoài), nhanh, không chờ vòng nền.
"""

from __future__ import annotations

import asyncio
import json
import textwrap
import time

import pytest
from fastapi.testclient import TestClient

from app import khoa_khach, nhat_ky as nhat_ky_mod, so_module, tai_khoan_hang_loat
from app.main import app

KHOA_QT = "<redacted>"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
MODULE_ID = "provider-gia-nv78"

MANIFEST_PROVIDER = {
    "id": MODULE_ID, "ten": "Provider giả NV78", "mo_ta": "Module provider giả cho test API đăng nhập hàng loạt",
    "loai": "ai", "hang": "A", "giao_thuc": "gia", "tien_to": "providergianv78",
    "cung_cap": ["chat"], "tai_nguyen": {"mang_ra": []},
    "tab_web": {"nhan": "Giả", "trang": "web/index.html"},
    "truong_cau_hinh": [],
}

# Module giả: `dang_nhap` đọc kịch bản từ `self.kich_ban` {email: (thanh_cong, ma_loi)};
# vắng mặt trong kịch bản → thành công. `treo` là asyncio.Event để test treo lượt chạy.
MA_PROVIDER = textwrap.dedent('''
    from app.tai_khoan_hang_loat import KetQuaDangNhap

    class Module:
        def __init__(self, manifest):
            self.manifest = manifest
            self.kich_ban = {}
            self.treo = None
            self.da_goi = []
        def nap(self, cau_hinh):
            pass
        async def cai(self, cau_hinh):
            return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": {}}
        async def models(self):
            return []
        async def suc_khoe(self):
            return {"ok": True}
        async def dung(self):
            pass
        async def dang_nhap(self, tai_khoan):
            self.da_goi.append(tai_khoan.email)
            if self.treo is not None:
                await self.treo.wait()
            thanh_cong, ma_loi = self.kich_ban.get(tai_khoan.email, (True, ""))
            return KetQuaDangNhap(thanh_cong, ma_loi=ma_loi)
''')

# Biến thể KHÔNG có `dang_nhap` — module chưa hỗ trợ đăng nhập tự động.
MA_PROVIDER_KHONG_HO_TRO = textwrap.dedent('''
    class Module:
        def __init__(self, manifest):
            self.manifest = manifest
        def nap(self, cau_hinh):
            pass
        async def cai(self, cau_hinh):
            return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": {}}
        async def models(self):
            return []
        async def suc_khoe(self):
            return {"ok": True}
        async def dung(self):
            pass
''')

VAN_BAN_MAU = """\
ban1@example.com|MatKhauBiMat_1|JBSWY3DPEHPK3PXP
ban2@example.com|cookie=SID=abc123; HSID=xyz
# dòng ghi chú bỏ qua
dong-sai-khong-email|matkhau
ban1@example.com|MatKhauTrung
"""

MODULE_ID_KHONG_HO_TRO = "provider-gia-nv78-kht"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data = tmp_path / "data"
    for mid, ma in ((MODULE_ID, MA_PROVIDER), (MODULE_ID_KHONG_HO_TRO, MA_PROVIDER_KHONG_HO_TRO)):
        thu_muc = data / "modules" / mid
        thu_muc.mkdir(parents=True)
        manifest = dict(MANIFEST_PROVIDER)
        manifest["id"] = mid
        manifest["tien_to"] = "providergianv78" if mid == MODULE_ID else "providergianv78kht"
        (thu_muc / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        (thu_muc / "module.py").write_text(ma, encoding="utf-8")
        (thu_muc / "web").mkdir()
        (thu_muc / "web" / "index.html").write_text("<!doctype html><title>gia</title>", encoding="utf-8")
    monkeypatch.setenv("WEB2API_DATA", str(data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    # Module chỉ vào so.dang_chay khi có mục trong modules.json (đã «cài»).
    (data / "modules.json").write_text(json.dumps({"modules": [
        {"id": MODULE_ID, "cai_luc": "2026-10-17T00:00:00", "cau_hinh_cong_khai": {}},
        {"id": MODULE_ID_KHONG_HO_TRO, "cai_luc": "2026-10-17T00:00:00", "cau_hinh_cong_khai": {}},
    ]}, ensure_ascii=False), encoding="utf-8")
    with TestClient(app) as client:
        assert MODULE_ID in client.app.state.so.catalog
        assert MODULE_ID_KHONG_HO_TRO in client.app.state.so.catalog
        assert MODULE_ID in client.app.state.so.dang_chay
        assert MODULE_ID_KHONG_HO_TRO in client.app.state.so.dang_chay
        yield client


def _module_gia(client):
    return client.app.state.so.dang_chay[MODULE_ID]


def _doi_trang_thai_xong(client, timeout=5.0):
    """Chờ lượt chạy nền kết thúc (poll nhẹ, không sleep dài)."""
    han = time.monotonic() + timeout
    while time.monotonic() < han:
        r = client.get("/api/tai-khoan/dang-nhap-hang-loat/trang-thai", headers=QT)
        assert r.status_code == 200, r.text
        if r.json()["trang_thai_luot"] == "hoan_thanh":
            return r.json()
        time.sleep(0.05)
    raise AssertionError("lượt chạy nền không kết thúc kịp")


# ───────────── POST /dan-hang-loat ─────────────

def test_dan_hang_loat_ok_va_khong_lo_mat_khau(moi_truong):
    """Tách đúng 2 dòng (kèm số dòng), 1 dòng lỗi đúng số dòng, 1 dòng trùng bị bỏ;
    phản hồi KHÔNG chứa mật khẩu thô."""
    r = moi_truong.post("/api/tai-khoan/dan-hang-loat", headers=QT,
                        json={"module_id": MODULE_ID, "van_ban": VAN_BAN_MAU})
    assert r.status_code == 200, r.text
    assert "MatKhauBiMat_1" not in r.text
    assert "JBSWY3DPEHPK3PXP" not in r.text
    assert "SID=abc123" not in r.text
    du_lieu = r.json()
    assert [(t["dong"], t["email"], t["loai"]) for t in du_lieu["tai_khoan"]] == [
        (1, "ban1@example.com", "mat_khau"),
        (2, "ban2@example.com", "cookie"),
    ]
    assert [(l["dong"]) for l in du_lieu["loi"]] == [4]
    assert [(b["dong"], b["email"]) for b in du_lieu["bo_trung"]] == [(5, "ban1@example.com")]


def test_dan_hang_loat_401(moi_truong):
    """Không khoá → 401."""
    r = moi_truong.post("/api/tai-khoan/dan-hang-loat",
                        json={"module_id": MODULE_ID, "van_ban": "a@b.co|x"})
    assert r.status_code == 401


def test_dan_hang_loat_403_khoa_khach(moi_truong):
    """Khoá khách phạm vi hẹp → 403."""
    _, khoa_tho = khoa_khach.tao_khoa("Khoá hẹp NV78", pham_vi=["chat"])
    khach = {"Authorization": f"Bearer {khoa_tho}"}
    assert moi_truong.post("/api/tai-khoan/dan-hang-loat", headers=khach,
                           json={"module_id": MODULE_ID, "van_ban": "a@b.co|x"}).status_code == 403
    assert moi_truong.post("/api/tai-khoan/dang-nhap-hang-loat/bat-dau", headers=khach,
                           json={"module_id": MODULE_ID, "van_ban": "a@b.co|x"}).status_code == 403
    assert moi_truong.get("/api/tai-khoan/dang-nhap-hang-loat/trang-thai", headers=khach).status_code == 403
    assert moi_truong.post("/api/tai-khoan/dang-nhap-hang-loat/dung", headers=khach).status_code == 403


def test_dan_hang_loat_module_chua_cai_404(moi_truong):
    """module_id chưa cài → 404 module_chua_cai (không lộ gì thêm)."""
    r = moi_truong.post("/api/tai-khoan/dan-hang-loat", headers=QT,
                        json={"module_id": "khong-co-module-nay", "van_ban": "a@b.co|x"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "module_chua_cai"


def test_dan_hang_loat_thieu_truong_400(moi_truong):
    """Thiếu module_id hoặc van_ban không phải chuỗi → 400."""
    assert moi_truong.post("/api/tai-khoan/dan-hang-loat", headers=QT,
                           json={"van_ban": "a@b.co|x"}).status_code == 400
    assert moi_truong.post("/api/tai-khoan/dan-hang-loat", headers=QT,
                           json={"module_id": MODULE_ID, "van_ban": 123}).status_code == 400


# ───────────── POST /bat-dau + GET /trang-thai ─────────────

def test_bat_dau_va_trang_thai_thanh_cong(moi_truong, monkeypatch):
    """Lượt chạy nền xong: tiến độ từng dòng, tóm tắt đúng; mật khẩu không lọt vào tiến độ."""
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    van_ban = "ok1@example.com|MatKhauKhongLo_9\nok2@example.com|cookie=SID=z9z"
    r = moi_truong.post("/api/tai-khoan/dang-nhap-hang-loat/bat-dau", headers=QT,
                        json={"module_id": MODULE_ID, "van_ban": van_ban})
    assert r.status_code == 200, r.text
    assert r.json()["tong"] == 2
    assert r.json()["luot_id"]
    assert "MatKhauKhongLo_9" not in r.text

    tt = _doi_trang_thai_xong(moi_truong)
    assert tt["trang_thai_luot"] == "hoan_thanh"
    assert tt["tom_tat"] == {"tong": 2, "xong": 2, "loi": 0, "bo_qua": 0}
    assert [(k["email"], k["trang_thai"]) for k in tt["ket_qua"]] == [
        ("ok1@example.com", "xong"), ("ok2@example.com", "xong")]
    assert "MatKhauKhongLo_9" not in json.dumps(tt)
    # Module giả đã được gọi đúng 2 email, theo thứ tự.
    assert _module_gia(moi_truong).da_goi == ["ok1@example.com", "ok2@example.com"]


def test_bat_dau_module_khong_ho_tro_bo_qua_khong_phai_loi(moi_truong):
    """Module chưa triển khai `dang_nhap` → mọi dòng `bo_qua` với mã riêng, KHÔNG tính lỗi."""
    r = moi_truong.post("/api/tai-khoan/dang-nhap-hang-loat/bat-dau", headers=QT,
                        json={"module_id": MODULE_ID_KHONG_HO_TRO,
                              "van_ban": "c1@example.com|x\nc2@example.com|cookie=k"})
    assert r.status_code == 200, r.text
    tt = _doi_trang_thai_xong(moi_truong)
    assert tt["trang_thai_luot"] == "hoan_thanh"
    assert tt["tom_tat"] == {"tong": 2, "xong": 0, "loi": 0, "bo_qua": 2}
    for k in tt["ket_qua"]:
        assert k["trang_thai"] == "bo_qua"
        assert k["ma_loi"] == "module_chua_ho_tro_dang_nhap_tu_dong"


def test_chi_mot_luot_mot_luc_409(moi_truong, monkeypatch):
    """Lượt thứ hai trong lúc lượt đầu đang chạy → 409 da_co_luot_chay; dừng được."""
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    mod = _module_gia(moi_truong)
    mod.treo = asyncio.Event()  # treo tài khoản đầu để lượt đầu còn «đang chạy»
    r1 = moi_truong.post("/api/tai-khoan/dang-nhap-hang-loat/bat-dau", headers=QT,
                         json={"module_id": MODULE_ID, "van_ban": "t1@example.com|x\nt2@example.com|y"})
    assert r1.status_code == 200, r1.text
    # Đợi task nền bắt đầu tài khoản đầu (đã gọi dang_nhap).
    han = time.monotonic() + 5
    while not mod.da_goi and time.monotonic() < han:
        time.sleep(0.05)
    assert mod.da_goi, "task nền chưa bắt đầu"

    r2 = moi_truong.post("/api/tai-khoan/dang-nhap-hang-loat/bat-dau", headers=QT,
                         json={"module_id": MODULE_ID, "van_ban": "khac@example.com|z"})
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "da_co_luot_chay"

    assert moi_truong.post("/api/tai-khoan/dang-nhap-hang-loat/dung", headers=QT).status_code == 200
    mod.treo.set()  # thả tài khoản đang treo ra để task nền kết thúc
    tt = _doi_trang_thai_xong(moi_truong)
    assert tt["trang_thai_luot"] == "hoan_thanh"
    # Dừng giữa chừng: tài khoản đã xong giữ nguyên, còn lại bo_qua (không chạy tiếp).
    trang_thais = {k["email"]: k["trang_thai"] for k in tt["ket_qua"]}
    assert trang_thais["t2@example.com"] == "bo_qua"


def test_tiep_tuc_sau_loi_bang_bo_qua_email(moi_truong, monkeypatch):
    """«Chạy tiếp» = bat-dau lại với bo_qua_email (email đã xong từ trang-thai)."""
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    van_ban = "n1@example.com|a\nn2@example.com|b"
    r1 = moi_truong.post("/api/tai-khoan/dang-nhap-hang-loat/bat-dau", headers=QT,
                         json={"module_id": MODULE_ID, "van_ban": van_ban})
    assert r1.status_code == 200, r1.text
    tt1 = _doi_trang_thai_xong(moi_truong)
    assert tt1["tom_tat"]["xong"] == 2

    # Giả vờ n2 chưa xong: chạy tiếp chỉ còn n2.
    r2 = moi_truong.post("/api/tai-khoan/dang-nhap-hang-loat/bat-dau", headers=QT,
                         json={"module_id": MODULE_ID, "van_ban": van_ban,
                               "bo_qua_email": ["n1@example.com"]})
    assert r2.status_code == 200, r2.text
    assert r2.json()["tong"] == 1
    tt2 = _doi_trang_thai_xong(moi_truong)
    assert [k["email"] for k in tt2["ket_qua"]] == ["n2@example.com"]


def test_gian_cach_do_bang_dong_ho_gia(moi_truong, monkeypatch):
    """Giãn cách giữa hai lần đăng nhập được đo bằng đồng hồ giả (không chờ thật)."""
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "30")
    moc = [0.0]
    cac_lan_ngu = []

    async def ngu_gia(giay):
        cac_lan_ngu.append(giay)
        moc[0] += giay

    quan_ly = moi_truong.app.state.quan_ly_luot_chay
    that = quan_ly._tao_luot
    monkeypatch.setattr(quan_ly, "_tao_luot",
                        lambda tk, ham: tai_khoan_hang_loat.LuotChay(
                            tk, ham, dong_ho=lambda: moc[0], ngu=ngu_gia))
    try:
        r = moi_truong.post("/api/tai-khoan/dang-nhap-hang-loat/bat-dau", headers=QT,
                            json={"module_id": MODULE_ID,
                                  "van_ban": "g1@example.com|a\ng2@example.com|b\ng3@example.com|c"})
        assert r.status_code == 200, r.text
        tt = _doi_trang_thai_xong(moi_truong)
    finally:
        monkeypatch.setattr(quan_ly, "_tao_luot", that)
    assert tt["tom_tat"]["xong"] == 3
    # 3 tài khoản → 2 khoảng giãn cách, mỗi khoảng ≥ 30s theo đồng hồ giả.
    assert len(cac_lan_ngu) == 2
    assert all(g >= 30.0 for g in cac_lan_ngu), cac_lan_ngu


def test_bat_dau_khong_co_dong_dung_400(moi_truong):
    """Văn bản không có dòng đúng nào → 400, không tạo lượt chạy."""
    r = moi_truong.post("/api/tai-khoan/dang-nhap-hang-loat/bat-dau", headers=QT,
                        json={"module_id": MODULE_ID, "van_ban": "# chỉ ghi chú\n"})
    assert r.status_code == 400
    r2 = moi_truong.get("/api/tai-khoan/dang-nhap-hang-loat/trang-thai", headers=QT)
    assert r2.status_code == 404  # chưa hề có lượt nào được tạo


def test_dung_khi_khong_co_luot_chay_404(moi_truong):
    """Dừng khi không có lượt nào → 404 luot_khong_chay."""
    r = moi_truong.post("/api/tai-khoan/dang-nhap-hang-loat/dung", headers=QT)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "luot_khong_chay"


def test_trang_thai_401(moi_truong):
    """GET trang-thai không khoá → 401."""
    assert moi_truong.get("/api/tai-khoan/dang-nhap-hang-loat/trang-thai").status_code == 401


def test_mat_khau_khong_vao_nhat_ky(moi_truong):
    """Mật khẩu thô không lọt vào nhật ký quản trị sau dan-hang-loat và cả lượt chạy."""
    danh_dau = "MatKhauNhatKy_9f8e7d6c"
    van_ban = f"nk1@example.com|{danh_dau}|JBSWY3DPEHPK3PXP"
    r = moi_truong.post("/api/tai-khoan/dan-hang-loat", headers=QT,
                        json={"module_id": MODULE_ID, "van_ban": van_ban})
    assert r.status_code == 200, r.text
    r = moi_truong.post("/api/tai-khoan/dang-nhap-hang-loat/bat-dau", headers=QT,
                        json={"module_id": MODULE_ID, "van_ban": van_ban})
    assert r.status_code == 200, r.text
    _doi_trang_thai_xong(moi_truong)
    ban_ghi = nhat_ky_mod.doc("quan_tri", gioi_han=500)
    noi_dung = json.dumps(ban_ghi, ensure_ascii=False)
    assert danh_dau not in noi_dung
    assert "JBSWY3DPEHPK3PXP" not in noi_dung
