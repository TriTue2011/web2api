"""NV122 — lõi điều phối «mã xác minh qua email» khi đăng nhập (tích «Lấy mã từ Gmail»).

Kiểm bằng module đọc-hộp-thư GIẢ và module đăng nhập GIẢ (không chờ NV121),
theo đúng hợp đồng: module đọc khai `cung_cap: ["ma_xac_minh"]`,
`hop_thu_ra() -> [{"id","email_che"}]`,
`async lay_ma(yeu_cau) -> {"ok","ma"|"ma_loi"}`.

Mã giả: "481516" — chuỗi GIẢ rõ ràng, không dữ liệu thật.
"""
import asyncio
import json
import logging
import textwrap
import time

import pytest
from fastapi.testclient import TestClient

from app import nhat_ky as nhat_ky_mod
from app import so_module
from app.main import app
from app import ma_xac_minh as ma_xac_minh_mod
from app import tai_khoan_hang_loat as tkh_mod

KHOA_QT = "khoa-quan-tri-nv122-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
MA_GIA = "481516"  # mã xác minh GIẢ dùng cho mọi test
MK_GIA = "MatKhauGia-NV122-xyz"
ID_DOC = "doc-ma-gia"
ID_DN = "dn-ma-gia"
ID_DN_KHONG_KHAI = "dn-khong-khai"

MA_DOC = textwrap.dedent('''\
    import asyncio

    class Module:
        che_do = "ok"          # ok | treo | loi | ma_loi:<ma>
        ma_gia = "''' + MA_GIA + '''"
        da_goi = []            # các yeu_cau đã nhận
        def __init__(self, manifest):
            self.manifest = manifest
        def nap(self, cau_hinh):
            pass
        async def cai(self, cau_hinh):
            return {"ok": True, "ket_qua_tung_khoa": [], "loi": None}
        async def suc_khoe(self):
            return {"ok": True}
        async def dung(self):
            pass
        async def models(self):
            return []
        def hop_thu_ra(self):
            return [
                {"id": "nguoi@example.com", "email_che": "n***@example.com"},
                {"id": "rieng@example.com", "email_che": "r***@example.com"},
            ]
        async def lay_ma(self, yeu_cau):
            type(self).da_goi.append(dict(yeu_cau))
            che = self.che_do
            if che == "treo":
                await asyncio.sleep(3600)
            if che == "loi":
                raise RuntimeError("doc hong gia")
            if che.startswith("ma_loi:"):
                return {"ok": False, "ma_loi": che.split(":", 1)[1]}
            return {"ok": True, "ma": self.ma_gia}
    ''')

MA_DN = textwrap.dedent('''\
    from app.tai_khoan_hang_loat import BiMatTam, KetQuaDangNhap

    class Module:
        nhan_ma = []   # các mã (str) mà nhap_ma_xac_minh đã nhận
        da_luu = []    # cau_hinh mà cai() đã nhận
        che_do_nhap = "ok"   # ok | that_bai | can_ma_email_lai
        def __init__(self, manifest):
            self.manifest = manifest
        def nap(self, cau_hinh):
            pass
        async def cai(self, cau_hinh):
            type(self).da_luu.append(cau_hinh)
            return {"ok": True, "ket_qua_tung_khoa": [], "loi": None}
        async def suc_khoe(self):
            return {"ok": True}
        async def dung(self):
            pass
        async def models(self):
            return []
        async def dang_nhap(self, tai_khoan):
            return KetQuaDangNhap(False, ma_loi="can_ma_email")
        async def nhap_ma_xac_minh(self, ho_so_hoac_phien, ma, trinh_duyet=None):
            assert isinstance(ma, BiMatTam), "mã phải bọc trong BiMatTam"
            type(self).nhan_ma.append(ma.lay())
            che = self.che_do_nhap
            if che == "that_bai":
                return KetQuaDangNhap(False, ma_loi="sai_ma")
            if che == "can_ma_email_lai":
                return KetQuaDangNhap(False, ma_loi="can_ma_email")
            return KetQuaDangNhap(True, cookie="chung-chi-gia")
    ''')


def _manifest(id_, tien_to, cung_cap, them=None):
    m = {
        "id": id_, "ten": "Giả " + id_, "mo_ta": "module giả cho NV122",
        "phien_ban": "1.0.0", "loai": "ai", "hang": "A", "giao_thuc": "openai",
        "tien_to": tien_to, "cung_cap": cung_cap,
        "truong_cau_hinh": [{"khoa": "tai_khoan", "nhan": "Tài khoản",
                             "kieu": "danh_sach_bi_mat"}],
        "tai_nguyen": {"mang_ra": []},
    }
    if them:
        m.update(them)
    return m


MANIFEST_DOC = _manifest(ID_DOC, "docgia", ["ma_xac_minh"],
                         {"truong_cau_hinh": []})
MANIFEST_DN = _manifest(ID_DN, "dngia", ["chat"],
                        {"xac_minh_email": {"tu_mien": ["example.com"], "do_dai": [6, 6]}})
MANIFEST_DN_KHONG_KHAI = _manifest(ID_DN_KHONG_KHAI, "dnkkgia", ["chat"])


@pytest.fixture
def moi(tmp_path, monkeypatch):
    data = tmp_path / "data"
    for mid, ma, manifest in ((ID_DOC, MA_DOC, MANIFEST_DOC),
                              (ID_DN, MA_DN, MANIFEST_DN),
                              (ID_DN_KHONG_KHAI, MA_DN, MANIFEST_DN_KHONG_KHAI)):
        thu_muc = data / "modules" / mid
        thu_muc.mkdir(parents=True)
        (thu_muc / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        (thu_muc / "module.py").write_text(ma, encoding="utf-8")
    monkeypatch.setenv("WEB2API_DATA", str(data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    (data / "modules.json").write_text(json.dumps({"modules": [
        {"id": ID_DOC, "cai_luc": "2026-10-17T00:00:00", "cau_hinh_cong_khai": {}},
        {"id": ID_DN, "cai_luc": "2026-10-17T00:00:00", "cau_hinh_cong_khai": {}},
        {"id": ID_DN_KHONG_KHAI, "cai_luc": "2026-10-17T00:00:00", "cau_hinh_cong_khai": {}},
    ]}, ensure_ascii=False), encoding="utf-8")
    ma_xac_minh_mod.dat_lai_han_dong()
    with TestClient(app, raise_server_exceptions=False) as client:
        assert ID_DOC in client.app.state.so.dang_chay
        assert ID_DN in client.app.state.so.dang_chay
        yield client


def _doi_luot_xong(client, timeout=8.0):
    han = time.monotonic() + timeout
    while time.monotonic() < han:
        r = client.get("/api/tai-khoan/dang-nhap-hang-loat/trang-thai", headers=QT)
        assert r.status_code == 200, r.text
        if r.json()["trang_thai_luot"] == "hoan_thanh":
            return r.json()
        time.sleep(0.05)
    raise AssertionError("lượt chạy nền không kết thúc kịp")


def _doc(client):
    return client.app.state.so.dang_chay[ID_DOC]


def _dn(client):
    return client.app.state.so.dang_chay[ID_DN]


# ───────────── tach_dong: cột gmail thứ tư ─────────────

def test_tach_dong_cot_gmail():
    ds, loi, _ = tkh_mod.tach_dong(
        "a@example.com|mk||true\n"
        "b@example.com|mk||\n"
        "c@example.com|mk||false\n"
        "d@example.com|mk||rieng@example.com\n"
        "e@example.com|mk||khong-phai-email\n")
    assert [t.ma_tu_gmail for t in ds] == [True, None, None, "rieng@example.com"]
    assert [(l.dong, l.loi) for l in loi] == [(5, "gmail sai khuôn")]
    # mặc định: không có cột gmail
    ds2, _, _ = tkh_mod.tach_dong("a@example.com|mk\n")
    assert ds2[0].ma_tu_gmail is None


# ───────────── luồng chính: cờ bật → lấy mã → nhập mã → lưu ─────────────

def test_co_bat_lay_ma_nhap_ma_va_luu_chung_chi(moi):
    r = moi.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT,
                 json={"module_id": ID_DN,
                       "van_ban": f"nguoi@example.com|{MK_GIA}||true"})
    assert r.status_code == 200, r.text
    tt = _doi_luot_xong(moi)
    assert tt["tom_tat"]["xong"] == 1, tt
    doc = _doc(moi)
    assert len(doc.da_goi) == 1
    yc = doc.da_goi[0]
    # tu_mien/do_dai lấy từ MANIFEST module đăng nhập, đúng khuôn hợp đồng
    assert yc["tu_mien"] == ["example.com"]
    assert yc["do_dai"] == [6, 6]
    assert yc["hop_thu"] == "nguoi@example.com"
    assert "sau_luc" in yc and "cho_giay" in yc
    dn = _dn(moi)
    assert dn.nhan_ma == [MA_GIA], "nhap_ma_xac_minh phải nhận ĐÚNG mã"
    assert any("chung-chi-gia" in json.dumps(c, ensure_ascii=False) for c in dn.da_luu), \
        "đăng nhập thành công phải lưu chứng chỉ qua cai()"


def test_hop_thu_chi_dinh_rieng(moi):
    r = moi.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT,
                 json={"module_id": ID_DN,
                       "van_ban": f"nguoi@example.com|{MK_GIA}||rieng@example.com"})
    assert r.status_code == 200, r.text
    _doi_luot_xong(moi)
    doc = _doc(moi)
    assert doc.da_goi[0]["hop_thu"] == "rieng@example.com"


def test_co_tat_khong_goi_lay_ma(moi, caplog):
    with caplog.at_level(logging.DEBUG, logger="web2api"):
        r = moi.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT,
                     json={"module_id": ID_DN,
                           "van_ban": f"nguoi@example.com|{MK_GIA}"})
    assert r.status_code == 200, r.text
    tt = _doi_luot_xong(moi)
    # không tích: hành vi cũ — module báo can_ma_email thì giữ nguyên mã lỗi đó
    kq = tt["ket_qua"]
    assert kq[0]["ma_loi"] == "can_ma_email"
    assert _doc(moi).da_goi == [], "cờ TẮT thì KHÔNG được gọi lay_ma"
    assert MA_GIA not in caplog.text


def test_khong_co_hop_thu_400_dong_bo(moi):
    r = moi.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT,
                 json={"module_id": ID_DN,
                       "van_ban": f"nguoi@example.com|{MK_GIA}||la@example.com"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "khong_co_hop_thu"


def test_flag_bat_nhung_module_khong_khai_400(moi):
    r = moi.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT,
                 json={"module_id": ID_DN_KHONG_KHAI,
                       "van_ban": f"nguoi@example.com|{MK_GIA}||true"})
    assert r.status_code == 400, r.text


def test_tu_mien_khong_lay_tu_http(moi):
    r = moi.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT,
                 json={"module_id": ID_DN,
                       "van_ban": f"nguoi@example.com|{MK_GIA}||true",
                       "tu_mien": ["evil.example"], "do_dai": [1, 1],
                       "ma_tu_gmail": True})
    assert r.status_code == 200, r.text
    _doi_luot_xong(moi)
    yc = _doc(moi).da_goi[0]
    assert yc["tu_mien"] == ["example.com"], "phải bỏ trường lạ trong HTTP"
    assert yc["do_dai"] == [6, 6]


def test_ma_khong_lo_vao_log_phan_hoi_nhat_ky(moi, caplog):
    with caplog.at_level(logging.DEBUG, logger="web2api"):
        r = moi.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT,
                     json={"module_id": ID_DN,
                           "van_ban": f"nguoi@example.com|{MK_GIA}||true"})
        assert r.status_code == 200, r.text
        tt = _doi_luot_xong(moi)
    assert MA_GIA not in r.text
    assert MA_GIA not in json.dumps(tt, ensure_ascii=False)
    assert MA_GIA not in caplog.text, "mã không được vào log"
    dong = nhat_ky_mod.doc(kenh="ma_xac_minh", gioi_han=50)
    for rec in dong:
        assert MA_GIA not in json.dumps(rec, ensure_ascii=False), \
            "nhật ký kênh ma_xac_minh không được chứa mã"
        kq = (rec.get("them") or {}).get("ket_qua")
        assert kq in ("ok",) or str(kq or "").startswith("khong_") \
            or kq in ("het_gio", "loi_module", "gioi_han"), rec


def test_het_gio(moi, monkeypatch):
    _doc(moi).che_do = "treo"
    monkeypatch.setattr(ma_xac_minh_mod, "CHO_GIAY_MAC_DINH", 1)
    r = moi.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT,
                 json={"module_id": ID_DN,
                       "van_ban": f"nguoi@example.com|{MK_GIA}||true"})
    assert r.status_code == 200, r.text
    tt = _doi_luot_xong(moi, timeout=10.0)
    assert tt["ket_qua"][0]["ma_loi"] == "het_gio"
    assert _dn(moi).nhan_ma == []


def test_doc_hong_khong_hong_luot_khac(moi):
    _doc(moi).che_do = "loi"
    r = moi.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT,
                 json={"module_id": ID_DN,
                       "van_ban": f"nguoi@example.com|{MK_GIA}||true\n"
                                  f"hai@example.com|{MK_GIA}"})
    assert r.status_code == 200, r.text
    tt = _doi_luot_xong(moi)
    kq = {k["email"]: k for k in tt["ket_qua"]}
    assert kq["nguoi@example.com"]["ma_loi"] == "loi_module"
    # tài khoản khác vẫn có kết quả riêng của nó (không bị hỏng theo)
    assert kq["hai@example.com"]["ma_loi"] == "can_ma_email"


def test_moi_luot_toi_da_mot_lan_goi(moi):
    _dn(moi).che_do_nhap = "can_ma_email_lai"  # module đòi mã lần nữa
    r = moi.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT,
                 json={"module_id": ID_DN,
                       "van_ban": f"nguoi@example.com|{MK_GIA}||true"})
    assert r.status_code == 200, r.text
    _doi_luot_xong(moi)
    assert len(_doc(moi).da_goi) == 1, "mỗi lượt tối đa một lần gọi lay_ma"


# ───────────── đơn vị: app/ma_xac_minh ─────────────

def test_loi_khong_co_module_doc(moi):
    so = moi.app.state.so
    doc = _doc(moi)
    so.dang_chay.pop(ID_DOC, None)
    try:
        ma, ma_loi = asyncio.run(
            ma_xac_minh_mod.lay_ma_xac_minh(
                moi.app, "nguoi@example.com", ["example.com"], (6, 6),
                "2026-10-18T00:00:00+00:00", 5))
    finally:
        so.dang_chay[ID_DOC] = doc
    assert (ma, ma_loi) == (None, "khong_co_hop_thu")


def test_han_dong_toan_cuc_6_phut(moi):
    async def goi():
        return await ma_xac_minh_mod.lay_ma_xac_minh(
            moi.app, "nguoi@example.com", ["example.com"], (6, 6),
            "2026-10-18T00:00:00+00:00", 5)
    ket_qua = [asyncio.run(goi()) for _ in range(7)]
    assert [k[1] for k in ket_qua[:6]] == [""] * 6
    assert ket_qua[6] == (None, "gioi_han")


def test_hop_thu_ra_hong_bo_qua(moi, monkeypatch):
    doc = _doc(moi)
    monkeypatch.setattr(doc, "hop_thu_ra", lambda: (_ for _ in ()).throw(RuntimeError("hong")))
    ma, ma_loi = asyncio.run(
        ma_xac_minh_mod.lay_ma_xac_minh(
            moi.app, "nguoi@example.com", ["example.com"], (6, 6),
            "2026-10-18T00:00:00+00:00", 5))
    assert (ma, ma_loi) == (None, "khong_co_hop_thu")


# ───────────── GET /api/ma-xac-minh/hop-thu ─────────────

def test_get_hop_thu_ok(moi):
    r = moi.get("/api/ma-xac-minh/hop-thu", headers=QT)
    assert r.status_code == 200, r.text
    ds = r.json()["hop_thu"]
    muc = [m for m in ds if m["module"] == ID_DOC]
    assert muc and muc[0]["email_che"] == "n***@example.com"
    assert all(set(m) == {"id", "email_che", "module"} for m in ds)
    assert MA_GIA not in r.text


def test_get_hop_thu_401(moi):
    assert moi.get("/api/ma-xac-minh/hop-thu").status_code == 401


def test_get_hop_thu_403_khoa_khach(moi, monkeypatch):
    monkeypatch.setenv("WEB2API_KHOA_KHACH", "khoa-khach-gia")
    r = moi.get("/api/ma-xac-minh/hop-thu",
                headers={"Authorization": "Bearer <redacted>"})
    assert r.status_code in (401, 403)


def test_get_hop_thu_rong_khi_khong_co_module_doc(moi):
    doc = _doc(moi)
    moi.app.state.so.dang_chay.pop(ID_DOC, None)
    try:
        r = moi.get("/api/ma-xac-minh/hop-thu", headers=QT)
        assert r.status_code == 200, r.text
        assert r.json()["hop_thu"] == []
    finally:
        moi.app.state.so.dang_chay[ID_DOC] = doc


# ───────────── manifest: xac_minh_email sai khuôn → bỏ trường, vẫn nạp ─────────────

def test_manifest_xac_minh_email_sai_khuon_van_nap(tmp_path, monkeypatch, caplog):
    from app import kham_pha
    data = tmp_path / "data"
    thu_muc = data / "modules" / "xau-gia"
    thu_muc.mkdir(parents=True)
    manifest = _manifest("xau-gia", "xaugia", ["chat"],
                         {"xac_minh_email": {"tu_mien": "khong-phai-danh-sach",
                                             "do_dai": [6]}})
    (thu_muc / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (thu_muc / "module.py").write_text(MA_DN, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        m, ly_do = kham_pha.doc_manifest(thu_muc)
    assert ly_do is None, f"module phải vẫn nạp được: {ly_do}"
    assert "xac_minh_email" not in m, "trường sai khuôn phải bị bỏ"
    assert any("xac_minh_email" in rec.message for rec in caplog.records)
