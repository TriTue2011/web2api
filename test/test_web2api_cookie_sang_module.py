"""Kiểm thử «lấy cookie từ hồ sơ trình duyệt → cấu hình module đích» (Chuẩn C6, bước sau đăng nhập trên server).

Tuyến: `POST /api/trinh-duyet/{module}/{ho_so}/cookie-sang-module` thân `{"mien", "module_dich", "truong"}`.
- Chỉ quản trị: không khoá 401, khoá khách 403.
- Lõi gọi phương thức TUỲ CHỌN `trinh_duyet_cookie(ho_so, mien)` của module nguồn (thiếu → 501), rồi đưa cookie vào
  cấu hình module đích QUA ĐÚNG đường cài có sẵn (`SoModule.cai` — module đích tự kiểm bằng `cai()` rồi mới lưu).
- `truong` phải là trường bí mật khai trong manifest của module đích (nhận diện bằng `kieu`, không bằng tên).
  Trường danh sách → THÊM vào cuối (khử trùng); trường đơn → thay.
- Phản hồi, log và nhật ký KHÔNG BAO GIỜ chứa cookie.

Module đích là module THẬT trên đĩa (<WEB2API_DATA>/modules/dich-gia) để đi đúng đường `catalog` → `cai()`;
module nguồn là đối tượng giả gắn vào `dang_chay`.
"""

from __future__ import annotations

import json
import logging
import textwrap

import pytest
from fastapi.testclient import TestClient

from app import kho, nhat_ky, novnc, so_module
from app.main import app

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
HO_SO = "abcdef0123456789abcdef01"
#: Cookie GIẢ, entropy thấp, đặc trưng để quét rò rỉ.
COOKIE_1 = "phien=GIA-cookie-mot-0001; phu=GIA-phu-mot-0001"
COOKIE_2 = "phien=GIA-cookie-hai-0002; phu=GIA-phu-hai-0002"
DUONG = f"/api/trinh-duyet/nguon-gia/{HO_SO}/cookie-sang-module"
THAN = {"mien": "vi-du.example", "module_dich": "dich-gia", "truong": "cookies"}

MANIFEST_DICH = {
    "id": "dich-gia", "ten": "Đích giả", "mo_ta": "Module đích giả nhận cookie", "loai": "ai", "giao_thuc": "gia",
    "tien_to": "dichgia", "cung_cap": ["chat"], "tai_nguyen": {"mang_ra": []},
    "tab_web": {"nhan": "Đích", "trang": "web/index.html"},
    "truong_cau_hinh": [
        {"khoa": "cookies", "nhan": "Danh sách cookie", "kieu": "danh_sach_bi_mat", "bat_buoc": False,
         "nhan_cookie": {"mien": "vi-du.example"}},
        {"khoa": "mot_cookie", "nhan": "Một cookie", "kieu": "bi_mat", "bat_buoc": False,
         "nhan_cookie": {"mien": "vi-du.example"}},
        {"khoa": "khoa_api", "nhan": "Khoá API (không nhận cookie)", "kieu": "bi_mat", "bat_buoc": False},
        {"khoa": "ghi_chu", "nhan": "Ghi chú", "kieu": "chuoi", "bat_buoc": False},
    ],
}

#: Module đích: cookie chứa «TU-CHOI» bị từ chối (và không được giữ); «VONG-LAI» thì module VỤNG VỀ chép nguyên cookie
#: vào thông báo lỗi (lõi phải tự chặn); «NEM-LOI» thì cai() ném ngoại lệ mang nguyên cookie.
MA_DICH = textwrap.dedent('''
    import base64
    import json
    import urllib.parse


    def phan_chieu(c, tat_ca):
        return ([c, json.dumps(c), urllib.parse.quote(c), base64.b64encode(c.encode()).decode(), repr(c), c[::-1],
                 c[:7]] + [x for x in tat_ca if x != c])


    class Module:
        def __init__(self, manifest):
            self.manifest = manifest

        def nap(self, cau_hinh):
            self.cau_hinh = cau_hinh

        async def cai(self, cau_hinh):
            ds = list(cau_hinh.get("cookies") or [])
            mot = cau_hinh.get("mot_cookie")
            tat_ca = ds + ([mot] if mot else [])
            for c in tat_ca:
                if "NEM-LOI" in c:
                    raise RuntimeError("no tung: " + c)
            kq = []
            for c in tat_ca:
                ok = "TU-CHOI" not in c
                loi = None if ok else "bi tu choi"
                if "VONG-LAI" in c:
                    loi = "loi voi cookie " + c
                if "XSS" in c:
                    loi = '<img src=x onerror="window.__xss=4">'
                khoa = "…" + c[-4:]
                if "PHAN-CHIEU" in c:  # module độc/vụng: phản chiếu cookie mới dưới mọi dạng + cookie CŨ
                    loi = " | ".join(phan_chieu(c, tat_ca))
                    khoa = c
                kq.append({"khoa": khoa, "ok": ok, "loi": loi, "thua": c})
            if kq and not any(x["ok"] for x in kq):
                vong = next((c for c in tat_ca if "VONG-LAI" in c), "")
                pc = next((" | ".join(phan_chieu(c, tat_ca)) for c in tat_ca if "PHAN-CHIEU" in c), "")
                return {"ok": False, "ket_qua_tung_khoa": kq, "loi": "khong cookie nao dung duoc " + vong + pc}
            luu = {"cookies": [c for c in ds if "TU-CHOI" not in c]}
            if mot is not None:
                luu["mot_cookie"] = mot
            for k in ("ghi_chu", "khoa_api"):
                if k in cau_hinh:
                    luu[k] = cau_hinh[k]
            return {"ok": True, "ket_qua_tung_khoa": kq, "loi": None, "cau_hinh_luu": luu}

        async def chat(self, body, stream):
            return 200, {}, b"{}"

        async def models(self):
            return []

        async def suc_khoe(self):
            return {"ok": True, "khoa": []}

        async def dung(self):
            pass
''')


class LoiNguon(Exception):
    """Lỗi có mã HTTP dạng cấu trúc (`ma`) — như module điều khiển trình duyệt thật ném khi dịch vụ trả 404/501."""

    def __init__(self, ma: int, thong_diep: str = ""):
        super().__init__(thong_diep)
        self.ma = ma


class ModuleNguonGia:
    def __init__(self):
        self.manifest = {"id": "nguon-gia", "loai": "tien_ich", "cung_cap": ["trinh_duyet"]}
        self.cookie: str = COOKIE_1
        self.loi: Exception | None = None
        self.goi: list[dict] = []

    async def trinh_duyet_mo(self, ho_so, loai, url=None):
        self.goi.append({"mo": ho_so, "loai": loai, "url": url})
        return {"ho_so": ho_so, "loai": loai, "dich": "http://127.0.0.1:9"}

    async def trinh_duyet_dong(self, ho_so, giu_phien):
        return {"ok": True}

    async def trinh_duyet_tao(self, ten):
        self.goi.append({"tao": ten})
        return {"ho_so": "0123456789abcdef01234567", "ten": ten}

    async def trinh_duyet_ds(self):
        return [{"ho_so": HO_SO, "ten": "<img src=x>", "loai": "firefox", "trang_thai": "dong", "dang_mo": False,
                 "thu_cong": False, "luc_mo": None, "dich": "http://w2a:bimat-GIA@may:1"}]

    async def trinh_duyet_cookie(self, ho_so: str, mien: str) -> dict:
        self.goi.append({"ho_so": ho_so, "mien": mien})
        if self.loi is not None:
            raise self.loi
        return {"cookie": self.cookie, "so_cookie": self.cookie.count(";") + 1}

    async def models(self):
        return []

    async def suc_khoe(self):
        return {"ok": True}

    async def dung(self):
        pass


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    data = tmp_path / "data"
    thu_muc = data / "modules" / "dich-gia"
    thu_muc.mkdir(parents=True)
    (thu_muc / "manifest.json").write_text(json.dumps(MANIFEST_DICH, ensure_ascii=False), encoding="utf-8")
    (thu_muc / "module.py").write_text(MA_DICH, encoding="utf-8")
    (thu_muc / "web").mkdir()
    (thu_muc / "web" / "index.html").write_text("<!doctype html><title>dich</title><p>tab</p>", encoding="utf-8")
    monkeypatch.setenv("WEB2API_DATA", str(data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    for so in (novnc._VE, novnc._PHIEN, novnc._HO_SO, novnc._WS):
        so.clear()
    yield data


@pytest.fixture
def client(du_lieu):
    with TestClient(app) as c:
        assert "dich-gia" in c.app.state.so.catalog, c.app.state.so.loi
        c.nguon = ModuleNguonGia()
        c.app.state.so.dang_chay["nguon-gia"] = c.nguon
        yield c


def _bi_mat_dich() -> dict:
    return kho.doc_bi_mat("dich-gia")


def _khong_chua_cookie(chu: str, *cookie: str) -> None:
    for c in cookie or (COOKIE_1, COOKIE_2):
        for manh in [c] + [p.split("=", 1)[1] for p in c.split("; ")]:
            assert manh not in chu, f"rò cookie: {manh!r} trong {chu[:300]!r}"


# ───────────── quyền ─────────────

def test_khong_khoa_401_khach_403(client):
    assert client.post(DUONG, json=THAN).status_code == 401
    assert client.post(DUONG, json=THAN, headers={"Authorization": "Bearer sai-hoan-toan-0000"}).status_code == 401
    rk = client.post("/api/khoa-khach", json={"nhan": "Khách", "pham_vi": ["chat"]}, headers=QT)
    hk = {"Authorization": f"Bearer {rk.json()['khoa']['khoa']}"}
    r = client.post(DUONG, json=THAN, headers=hk)
    assert r.status_code == 403
    assert client.nguon.goi == [], "khách/không khoá không được chạm tới module nguồn"
    assert _bi_mat_dich() == {}


# ───────────── kiểm đầu vào (không phản chiếu) ─────────────

@pytest.mark.parametrize("than, ma", [
    ({**THAN, "mien": "KHONG..HOP-LE"}, 400),
    ({**THAN, "mien": "<img src=x>.example"}, 400),
    ({**THAN, "mien": "dondoc"}, 400),
    ({**THAN, "mien": 123}, 400),
    ({**THAN, "module_dich": "khong-co-module-nay"}, 404),
    ({**THAN, "module_dich": "../../etc"}, 404),
    ({**THAN, "module_dich": None}, 404),
    ({**THAN, "truong": "ghi_chu"}, 400),            # trường thường, không phải bí mật
    ({**THAN, "truong": "truong-bia-ra-XYZ"}, 400),  # không khai trong manifest
    ({**THAN, "truong": ["cookies"]}, 400),
    ({**THAN, "truong": "khoa_dich_vu"}, 400),       # tên trường bí mật của module KHÁC — không đoán theo tên
])
def test_dau_vao_sai_khong_goi_nguon_khong_phan_chieu(client, than, ma):
    r = client.post(DUONG, json=than, headers=QT)
    assert r.status_code == ma, r.text
    thong_diep = r.json()["error"]["message"]
    for k in ("mien", "module_dich", "truong"):
        v = than.get(k)
        if isinstance(v, str) and len(v) >= 4 and v != THAN.get(k):
            assert v not in thong_diep, f"phản chiếu {k}"
    assert client.nguon.goi == [], "đầu vào sai phải bị chặn TRƯỚC khi lấy cookie"
    assert _bi_mat_dich() == {}


def test_ho_so_sai_va_than_hong(client):
    assert client.post("/api/trinh-duyet/nguon-gia/KHONG-HEX/cookie-sang-module", json=THAN,
                       headers=QT).status_code == 400
    assert client.post(DUONG, content=b"{hong", headers={**QT, "Content-Type": "application/json"}).status_code == 400
    assert client.post(DUONG, json=["mang"], headers=QT).status_code == 400
    assert client.nguon.goi == []


def test_module_dich_tu_xa_khong_phai_catalog_404(client):
    """Module từ xa (chỉ URL, không manifest trường) không phải đích hợp lệ."""
    client.app.state.so.tu_xa["xa-gia"] = {"id": "xa-gia", "ten": "x", "tien_to": "xagia", "base_url": "http://x"}
    r = client.post(DUONG, json={**THAN, "module_dich": "xa-gia"}, headers=QT)
    assert r.status_code == 404


def test_module_nguon_khong_co_hoac_thieu_phuong_thuc(client):
    r = client.post(f"/api/trinh-duyet/khong-co/{HO_SO}/cookie-sang-module", json=THAN, headers=QT)
    assert r.status_code == 404

    class KhongCookie:
        manifest = {"id": "k", "cung_cap": ["trinh_duyet"]}

        async def dung(self):
            pass

    client.app.state.so.dang_chay["khong-cookie"] = KhongCookie()
    r = client.post(f"/api/trinh-duyet/khong-cookie/{HO_SO}/cookie-sang-module", json=THAN, headers=QT)
    assert r.status_code == 501


# ───────────── đường chính ─────────────

def test_them_vao_danh_sach_khu_trung_va_khong_tra_cookie(client, caplog):
    caplog.set_level(logging.DEBUG)
    r1 = client.post(DUONG, json=THAN, headers=QT)
    assert r1.status_code == 200, r1.text
    assert r1.json()["ok"] is True and r1.json()["da_them"] == 1
    assert client.nguon.goi == [{"ho_so": HO_SO, "mien": "vi-du.example"}]
    assert _bi_mat_dich()["cookies"] == [COOKIE_1]

    client.nguon.cookie = COOKIE_2
    r2 = client.post(DUONG, json=THAN, headers=QT)
    assert r2.json()["da_them"] == 1
    assert _bi_mat_dich()["cookies"] == [COOKIE_1, COOKIE_2], "THÊM vào cuối, giữ cookie cũ"

    client.nguon.cookie = COOKIE_1
    r3 = client.post(DUONG, json=THAN, headers=QT)
    assert r3.status_code == 200 and r3.json()["ok"] is True
    assert r3.json()["da_them"] == 0, "chuỗi y hệt đã có → không thêm bản thứ hai"
    assert _bi_mat_dich()["cookies"] == [COOKIE_1, COOKIE_2]

    for r in (r1, r2, r3):
        assert set(r.json()) <= {"ok", "ket_qua_tung_khoa", "da_them", "loi"}
        _khong_chua_cookie(r.text)
        for muc in r.json()["ket_qua_tung_khoa"]:
            assert set(muc) <= {"khoa", "ok", "loi"}, "chỉ khoa/ok/loi — trường thừa của module bị bỏ"
    # Cấu hình công khai (GET mở) chỉ có dạng che
    _khong_chua_cookie(json.dumps(client.get("/api/modules").json(), ensure_ascii=False))
    _khong_chua_cookie(json.dumps(client.get("/api/modules/dich-gia/cau-hinh", headers=QT).json(), ensure_ascii=False))
    _khong_chua_cookie("\n".join(rec.getMessage() + repr(rec.args) for rec in caplog.records))
    so_nk = nhat_ky.doc(kenh="trinh_duyet", gioi_han=100)
    _khong_chua_cookie(json.dumps(so_nk, ensure_ascii=False))


def test_truong_don_thay_the(client):
    than = {**THAN, "truong": "mot_cookie"}
    assert client.post(DUONG, json=than, headers=QT).json()["da_them"] == 1
    assert _bi_mat_dich()["mot_cookie"] == COOKIE_1
    client.nguon.cookie = COOKIE_2
    r = client.post(DUONG, json=than, headers=QT)
    assert r.status_code == 200 and r.json()["da_them"] == 1
    assert _bi_mat_dich()["mot_cookie"] == COOKIE_2
    _khong_chua_cookie(r.text)


def test_giu_cau_hinh_khac_da_luu(client):
    """Đường cài có sẵn: trường vắng mặt giữ giá trị đã lưu (ghi chú, cookie đơn) — không đè mất."""
    r0 = client.post("/api/modules/dich-gia/cai", json={"cau_hinh": {"ghi_chu": "giu-nguyen",
                                                                     "mot_cookie": COOKIE_2}}, headers=QT)
    assert r0.status_code == 200, r0.text
    assert client.post(DUONG, json=THAN, headers=QT).status_code == 200
    assert _bi_mat_dich() == {"cookies": [COOKIE_1], "mot_cookie": COOKIE_2}
    assert client.get("/api/modules/dich-gia/cau-hinh", headers=QT).json()["gia_tri"]["ghi_chu"] == "giu-nguyen"


def test_cai_that_bai_khong_luu(client):
    client.post(DUONG, json=THAN, headers=QT)
    truoc = _bi_mat_dich()
    client.nguon.cookie = "phien=GIA-TU-CHOI-0003"
    # Cookie cũ (COOKIE_1) vẫn đạt nên cai() ok=True nhưng cookie mới bị loại → da_them 0.
    r = client.post(DUONG, json=THAN, headers=QT)
    assert r.status_code == 200 and r.json()["da_them"] == 0
    assert _bi_mat_dich() == truoc


def test_cai_that_bai_moi_cookie_400_khong_luu(client):
    client.nguon.cookie = "phien=GIA-TU-CHOI-0004"
    r = client.post(DUONG, json=THAN, headers=QT)
    assert r.status_code == 400
    assert r.json()["ok"] is False and r.json()["da_them"] == 0
    assert r.json()["ket_qua_tung_khoa"][0]["ok"] is False
    assert _bi_mat_dich() == {}
    assert "dich-gia" not in client.app.state.so.dang_chay
    _khong_chua_cookie(r.text, "phien=GIA-TU-CHOI-0004")


def test_module_dich_vung_ve_chep_cookie_vao_loi_van_bi_chan(client, caplog):
    """Lõi không tin module đích: thông báo lỗi chứa cookie (hay một mảnh giá trị của nó) bị thay bằng câu chung."""
    caplog.set_level(logging.DEBUG)
    c_vong = "phien=GIA-VONG-LAI-TU-CHOI-0005"
    client.nguon.cookie = c_vong
    r = client.post(DUONG, json=THAN, headers=QT)
    assert r.status_code == 400
    _khong_chua_cookie(r.text, c_vong)
    _khong_chua_cookie(json.dumps(nhat_ky.doc(kenh="trinh_duyet", gioi_han=100), ensure_ascii=False), c_vong)
    _khong_chua_cookie("\n".join(rec.getMessage() + repr(rec.args) for rec in caplog.records), c_vong)


def test_module_dich_nem_loi_502_khong_nhung_nguyen_van(client, caplog):
    caplog.set_level(logging.DEBUG)
    c_no = "phien=GIA-NEM-LOI-0006"
    client.nguon.cookie = c_no
    r = client.post(DUONG, json=THAN, headers=QT)
    assert r.status_code == 502
    assert "RuntimeError" in r.json()["error"]["message"]
    _khong_chua_cookie(r.text, c_no)
    _khong_chua_cookie("\n".join(rec.getMessage() + repr(rec.args) for rec in caplog.records), c_no)
    assert _bi_mat_dich() == {}


# ───────────── lỗi từ module nguồn ─────────────

@pytest.mark.parametrize("ma_nguon, ma_ra", [(404, 404), (501, 501), (400, 400), (409, 409), (401, 502), (413, 502),
                                             (500, 502)])
def test_loi_nguon_co_ma_cau_truc(client, ma_nguon, ma_ra):
    client.nguon.loi = LoiNguon(ma_nguon, "chi tiet " + COOKIE_1)
    r = client.post(DUONG, json=THAN, headers=QT)
    assert r.status_code == ma_ra, r.text
    _khong_chua_cookie(r.text)
    assert "chi tiet" not in r.text, "không nhúng nguyên văn lỗi của module"
    assert _bi_mat_dich() == {}


def test_loi_nguon_bat_ky_502_chi_ten_kieu(client):
    client.nguon.loi = ValueError("bi mat " + COOKIE_1)
    r = client.post(DUONG, json=THAN, headers=QT)
    assert r.status_code == 502 and "ValueError" in r.json()["error"]["message"]
    _khong_chua_cookie(r.text)


@pytest.mark.parametrize("cookie", ["", "   ", "a=b\r\nX-Chen: 1", "a=\x00b", "a=ạ", "x" * (novnc.TRAN_COOKIE + 1)])
def test_cookie_rong_hoac_sai_hinh_khong_cai(client, cookie):
    client.nguon.cookie = cookie
    r = client.post(DUONG, json=THAN, headers=QT)
    assert r.status_code in (404, 502), r.text
    assert _bi_mat_dich() == {}, "cookie rỗng/sai hình KHÔNG được đưa vào cai()"


@pytest.mark.parametrize("kq", [None, [], {"khong_co_cookie": 1}, {"cookie": 5}])
def test_ket_qua_nguon_sai_hinh_502(client, kq, monkeypatch):
    async def tra(ho_so, mien):
        return kq

    monkeypatch.setattr(client.nguon, "trinh_duyet_cookie", tra)
    assert client.post(DUONG, json=THAN, headers=QT).status_code == 502
    assert _bi_mat_dich() == {}


# ───────────── nhật ký ─────────────

def test_ghi_so_kenh_trinh_duyet(client):
    client.post(DUONG, json=THAN, headers=QT)
    client.nguon.loi = LoiNguon(404)
    client.post(DUONG, json={**THAN, "truong": "mot_cookie"}, headers=QT)
    client.nguon.loi = None
    client.nguon.cookie = "phien=GIA-TU-CHOI-0007"
    client.post(DUONG, json={**THAN, "module_dich": "dich-gia"}, headers=QT)
    ds = nhat_ky.doc(kenh="trinh_duyet", gioi_han=100)
    muc = [m for m in ds if m["them"].get("hanh_dong") == "cookie_sang_module"]
    assert len(muc) == 3, ds
    vb = json.dumps(muc, ensure_ascii=False)
    # Mã hồ sơ (hex dài) bị nhật ký tự che như mọi chuỗi trông giống bí mật — không đòi có nguyên văn.
    assert "dich-gia" in vb and "nguon-gia" in vb and "cookies" in vb and "mot_cookie" in vb and "vi-du.example" in vb
    assert "thanh_cong" in vb and "that_bai" in vb
    _khong_chua_cookie(json.dumps(ds, ensure_ascii=False), COOKIE_1, "phien=GIA-TU-CHOI-0007")


# ───────────── module vnc-xa: trinh_duyet_cookie gọi dịch vụ trình duyệt ─────────────

import asyncio  # noqa: E402
import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

from starlette.applications import Starlette  # noqa: E402
from starlette.responses import JSONResponse, Response  # noqa: E402
from starlette.routing import Route  # noqa: E402

from gia import MayChuThat  # noqa: E402

KHOA_DV = "khoa-dich-vu-GIA-0000000000000000"
THU_MUC_VNC = Path(__file__).resolve().parents[1] / "modules" / "vnc-xa"


class DichVuCookieGia:
    """Dịch vụ trình duyệt giả: chỉ đường cookie (+ /ho-so cho cai()). `tra` = (mã, thân) để ép phản hồi."""

    def __init__(self):
        self.yeu_cau: list[dict] = []
        self.tra: tuple[int, object] | None = None

    async def cookie(self, request):
        self.yeu_cau.append({"path": request.url.path, "query": dict(request.query_params),
                             "auth": request.headers.get("authorization")})
        if request.headers.get("authorization") != f"Bearer {KHOA_DV}":
            return JSONResponse({"detail": "sai khoa"}, status_code=401)
        if self.tra is not None:
            ma, than = self.tra
            if ma == 302:
                return Response("", status_code=302, headers={"Location": "http://127.0.0.1:9/ho-so"})
            return JSONResponse(than, status_code=ma)
        return JSONResponse({"cookie": COOKIE_1, "so_cookie": 2})

    async def ds(self, request):
        if request.headers.get("authorization") != f"Bearer {KHOA_DV}":
            return JSONResponse({"detail": "sai khoa"}, status_code=401)
        if request.method == "POST":
            than = await request.json()
            self.yeu_cau.append({"tao": than})
            if self.tra is not None:
                return JSONResponse(self.tra[1], status_code=self.tra[0])
            return JSONResponse({"ho_so": "00112233445566778899aabb", "ten": than.get("ten")})
        return JSONResponse([])

    async def mo(self, request):
        than = await request.json()
        self.yeu_cau.append({"mo": than})
        return JSONResponse({"ho_so": than["ho_so"], "loai": than["loai"], "dich": "http://127.0.0.1:9"})

    def app(self):
        return Starlette(routes=[Route("/ho-so/{id}/cookie", self.cookie), Route("/ho-so", self.ds, methods=["GET", "POST"]),
                                 Route("/ho-so/mo", self.mo, methods=["POST"])])


@pytest.fixture
def dich_vu_cookie():
    dv = DichVuCookieGia()
    with MayChuThat(dv.app()) as s:
        dv.url = s.url
        yield dv


def _vnc_xa(url: str, khoa: str = KHOA_DV):
    spec = importlib.util.spec_from_file_location("vnc_xa_cookie_test", THU_MUC_VNC / "module.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    m = mod.Module(json.loads((THU_MUC_VNC / "manifest.json").read_text(encoding="utf-8")))
    # Máy chủ giả chạy trên loopback: mở kiểm IP của thể hiện này (mỗi test một thể
    # hiện mới nên gán trực tiếp tương đương monkeypatch, không rò sang test khác).
    m._kiem_ip = lambda ip: None
    m.nap({"dich_vu_url": url, "khoa_dich_vu": khoa})
    return m


def _loi_dv(m):
    """Lớp lỗi có cấu trúc `LoiDichVu` của CHÍNH module vnc-xa vừa nạp (để kiểm đúng kiểu, không bắt Exception chung)."""
    return m.trinh_duyet_cookie.__globals__["LoiDichVu"]


def test_vnc_xa_goi_dung_duong_dich_vu(dich_vu_cookie):
    m = _vnc_xa(dich_vu_cookie.url)
    kq = asyncio.run(m.trinh_duyet_cookie(HO_SO, "vi-du.example"))
    assert kq == {"cookie": COOKIE_1, "so_cookie": 2}
    assert dich_vu_cookie.yeu_cau == [{"path": f"/ho-so/{HO_SO}/cookie", "query": {"mien": "vi-du.example"},
                                       "auth": f"Bearer {KHOA_DV}"}]


@pytest.mark.parametrize("ma_dv, ma_loi", [(400, 400), (404, 404), (409, 409), (501, 501), (413, None), (500, None),
                                           (302, None)])
def test_vnc_xa_anh_xa_loi_theo_ma(dich_vu_cookie, ma_dv, ma_loi):
    dich_vu_cookie.tra = (ma_dv, {"detail": "noi dung thuong nguon " + COOKIE_2})
    m = _vnc_xa(dich_vu_cookie.url)
    with pytest.raises(_loi_dv(m)) as e:
        asyncio.run(m.trinh_duyet_cookie(HO_SO, "vi-du.example"))
    assert e.value.ma == ma_loi
    assert "noi dung thuong nguon" not in str(e.value), "lỗi dựng từ mã trạng thái, không chép nội dung thượng nguồn"
    _khong_chua_cookie(str(e.value), COOKIE_2)
    assert len(dich_vu_cookie.yeu_cau) == 1, "không theo chuyển hướng"


def test_vnc_xa_khoa_sai_va_phan_hoi_sai_hinh(dich_vu_cookie):
    m_sai = _vnc_xa(dich_vu_cookie.url, khoa="khoa-sai-GIA-0000")
    with pytest.raises(_loi_dv(m_sai)) as e:
        asyncio.run(m_sai.trinh_duyet_cookie(HO_SO, "vi-du.example"))
    assert e.value.ma is None and "401" in str(e.value) and KHOA_DV not in str(e.value)
    for than in ([], {"cookie": 5}, {"khac": "x"}):
        dich_vu_cookie.tra = (200, than)
        m = _vnc_xa(dich_vu_cookie.url)
        with pytest.raises(_loi_dv(m)) as e:
            asyncio.run(m.trinh_duyet_cookie(HO_SO, "vi-du.example"))
        assert e.value.ma is None and "cấu trúc" in str(e.value)


def test_vnc_xa_ho_so_sai_khong_goi_dich_vu(dich_vu_cookie):
    m = _vnc_xa(dich_vu_cookie.url)
    for ho_so in ("../ho-so", "ABCDEF0123456789", "abc", HO_SO + "/x"):
        with pytest.raises(_loi_dv(m)) as e:
            asyncio.run(m.trinh_duyet_cookie(ho_so, "vi-du.example"))
        assert e.value.ma == 400
    assert dich_vu_cookie.yeu_cau == []
    chua_cau_hinh = _vnc_xa("")
    with pytest.raises(RuntimeError):
        asyncio.run(chua_cau_hinh.trinh_duyet_cookie(HO_SO, "vi-du.example"))


# ───────────── workspace qua lõi: tạo, liệt kê, mở với trang bắt đầu ─────────────

TAO = "/api/trinh-duyet/nguon-gia"


def test_tao_workspace_quyen_va_hop_dong(client):
    assert client.post(TAO, json={"ten": "A"}).status_code == 401
    rk = client.post("/api/khoa-khach", json={"nhan": "Khách", "pham_vi": ["chat"]}, headers=QT)
    hk = {"Authorization": f"Bearer {rk.json()['khoa']['khoa']}"}
    assert client.post(TAO, json={"ten": "A"}, headers=hk).status_code == 403
    assert client.get(TAO, headers=hk).status_code == 403
    assert client.nguon.goi == []
    r = client.post(TAO, json={"ten": "  Muse cá nhân "}, headers=QT)
    assert r.status_code == 200, r.text
    assert r.json() == {"ho_so": "0123456789abcdef01234567", "ten": "Muse cá nhân"}
    assert client.nguon.goi == [{"tao": "Muse cá nhân"}]


@pytest.mark.parametrize("ten", ["", "   ", "x" * 101, "a\nb", None, 5, ["a"]])
def test_tao_workspace_ten_sai_400(client, ten):
    r = client.post(TAO, json={"ten": ten}, headers=QT)
    assert r.status_code == 400
    assert client.nguon.goi == []


def test_tao_workspace_module_thieu_hoac_tra_sai(client, monkeypatch):
    class KhongTao:
        manifest = {"id": "k", "cung_cap": ["trinh_duyet"]}

        async def dung(self):
            pass

    client.app.state.so.dang_chay["khong-tao"] = KhongTao()
    assert client.post("/api/trinh-duyet/khong-tao", json={"ten": "a"}, headers=QT).status_code == 501
    for kq in ({"ho_so": "../x", "ten": "a"}, {"ten": "a"}, None):
        async def tra(ten, kq=kq):
            return kq
        monkeypatch.setattr(client.nguon, "trinh_duyet_tao", tra)
        assert client.post(TAO, json={"ten": "a"}, headers=QT).status_code == 502

    async def no(ten):
        raise RuntimeError("chi tiet noi bo")
    monkeypatch.setattr(client.nguon, "trinh_duyet_tao", no)
    r = client.post(TAO, json={"ten": "a"}, headers=QT)
    assert r.status_code == 502 and "chi tiet noi bo" not in r.text


def test_danh_sach_co_ten_dang_mo_khong_lo_dich(client):
    r = client.get(TAO, headers=QT)
    assert r.status_code == 200
    assert r.json() == [{"ho_so": HO_SO, "ten": "<img src=x>", "loai": "firefox", "trang_thai": "dong",
                         "dang_mo": False, "thu_cong": False, "luc_mo": None}]
    assert "bimat-GIA" not in r.text


def test_mo_chuyen_url_bat_dau_cho_module(client):
    r = client.post("/api/trinh-duyet/nguon-gia/mo", json={"loai": "firefox", "ho_so": HO_SO,
                                                           "url": "https://muse.ai/"}, headers=QT)
    # Đích giả 127.0.0.1:9 bị chặn SSRF (không bật cờ loopback) → 502 SAU khi module đã được gọi đúng tham số.
    assert client.nguon.goi[0] == {"mo": HO_SO, "loai": "firefox", "url": "https://muse.ai/"}
    assert r.status_code in (200, 502)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "http://u:p@vi-du.example/",
                                 "https://", "https://a b.example/", "x" * 3000, 5, "--profile=/tmp"])
def test_mo_url_sai_400_khong_goi_module(client, url):
    r = client.post("/api/trinh-duyet/nguon-gia/mo", json={"loai": "firefox", "url": url}, headers=QT)
    assert r.status_code == 400
    assert client.nguon.goi == []
    if isinstance(url, str) and 8 < len(url) < 100:
        assert url not in r.text


def test_mo_khong_url_khong_truyen_tham_so_url(client):
    """Module cũ chỉ nhận (ho_so, loai) vẫn chạy: lõi chỉ truyền `url` khi quản trị gửi."""
    cu: list = []

    async def mo_cu(ho_so, loai):
        cu.append((ho_so, loai))
        return {"ho_so": ho_so, "loai": loai, "dich": "http://127.0.0.1:9"}

    client.nguon.trinh_duyet_mo = mo_cu
    client.post("/api/trinh-duyet/nguon-gia/mo", json={"loai": "firefox", "ho_so": HO_SO}, headers=QT)
    client.post("/api/trinh-duyet/nguon-gia/mo", json={"loai": "firefox", "ho_so": HO_SO, "url": ""}, headers=QT)
    assert cu == [(HO_SO, "firefox"), (HO_SO, "firefox")]


def test_vnc_xa_tao_workspace_va_mo_voi_url(dich_vu_cookie):
    m = _vnc_xa(dich_vu_cookie.url)
    assert asyncio.run(m.trinh_duyet_tao("Muse cá nhân")) == {"ho_so": "00112233445566778899aabb", "ten": "Muse cá nhân"}
    asyncio.run(m.trinh_duyet_mo(HO_SO, "firefox", url="https://muse.ai/"))
    asyncio.run(m.trinh_duyet_mo(HO_SO, "firefox"))
    assert dich_vu_cookie.yeu_cau == [
        {"tao": {"ten": "Muse cá nhân", "loai": "firefox"}},
        {"mo": {"ho_so": HO_SO, "loai": "firefox", "url": "https://muse.ai/"}},
        {"mo": {"ho_so": HO_SO, "loai": "firefox"}},
    ]
    dich_vu_cookie.tra = (429, {"detail": "noi dung thuong nguon"})
    with pytest.raises(_loi_dv(m)) as e:
        asyncio.run(m.trinh_duyet_tao("B"))
    assert "429" in str(e.value) and "noi dung thuong nguon" not in str(e.value)


def test_trang_module_m_co_csp_va_header_bao_ve(client):
    """Luật cứng 3: trang module (giao diện chạy với khoá quản trị trong localStorage) có CSP chặt + header bảo vệ —
    cả khi 404."""
    for duong, ma in (("/m/dich-gia/index.html", 200), ("/m/dich-gia/khong-co.html", 404)):
        r = client.get(duong)
        assert r.status_code == ma
        csp = r.headers.get("content-security-policy", "")
        for chi_thi in ("default-src 'self'", "script-src 'self'", "object-src 'none'", "base-uri 'none'",
                        "frame-ancestors 'none'"):
            assert chi_thi in csp, (duong, csp)
        assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert r.headers.get("x-frame-options") == "DENY"
        assert r.headers.get("referrer-policy") == "no-referrer"


def test_vnc_xa_nap_duoc_bang_kham_pha_cua_loi():
    """Lõi nạp module bằng `nap_ma` (đòi đủ PHUONG_THUC, kể cả `chat`) — module vnc-xa phải qua được, và đúng chuẩn
    khả năng `trinh_duyet` (bản cũ thiếu `chat` nên không bao giờ được nạp)."""
    from app.kham_pha import doc_manifest, go_ma, kiem_chuan_module, nap_ma, van_tay

    manifest, ly_do = doc_manifest(THU_MUC_VNC)
    assert ly_do is None, ly_do
    lop, ten = nap_ma(THU_MUC_VNC, manifest, van_tay(THU_MUC_VNC))
    try:
        assert kiem_chuan_module(manifest["cung_cap"], lop) == {}
        ma, _, than = asyncio.run(lop(manifest).chat({"model": "vncxa/x", "messages": []}, False))
        assert ma == 501 and b"error" in than
    finally:
        go_ma(ten)


@pytest.mark.parametrize("thu_muc", sorted(p.parent for p in (THU_MUC_VNC.parent).glob("*/module.py")),
                         ids=lambda p: p.name)
def test_moi_module_trong_kho_du_phuong_thuc_bat_buoc(thu_muc):
    """Lớp lỗi «module không bao giờ nạp được» (vnc-xa từng thiếu `chat`; test cũ nạp bằng spec_from_file_location nên
    không thấy): đọc AST — không import, không cần gói của module — và đối chiếu PHUONG_THUC của lõi."""
    import ast

    from app.kham_pha import PHUONG_THUC

    cay = ast.parse((thu_muc / "module.py").read_text(encoding="utf-8"))
    lop = next(n for n in cay.body if isinstance(n, ast.ClassDef) and n.name == "Module")
    co = {n.name for n in lop.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert [x for x in PHUONG_THUC if x not in co] == [], f"{thu_muc.name} thiếu: {[x for x in PHUONG_THUC if x not in co]}"


def _cac_dang(c: str) -> list[str]:
    import base64
    import urllib.parse
    return [c, json.dumps(c)[1:-1], urllib.parse.quote(c), base64.b64encode(c.encode()).decode(), repr(c)[1:-1],
            c[::-1], c[:7]]


def test_module_dich_phan_chieu_cookie_moi_va_cu_moi_dang_khong_lot(client):
    """Lõi KHÔNG chuyển tiếp chuỗi tự do của module đích (`loi` tổng và từng khoá, `khoa` sai dạng che): module độc
    phản chiếu cookie mới (thô, JSON, URL, base64, repr, đảo ngược, 7 ký tự đầu) và cookie CŨ đã lưu — không lọt dạng nào."""
    assert client.post(DUONG, json=THAN, headers=QT).status_code == 200  # COOKIE_1 thành cookie CŨ đã lưu
    c_moi = "phien=GIA-PHAN-CHIEU-0010"
    client.nguon.cookie = c_moi
    r = client.post(DUONG, json=THAN, headers=QT)
    assert r.status_code == 200
    for dang in _cac_dang(c_moi) + _cac_dang(COOKIE_1)[:6]:
        assert dang not in r.text, dang
    for muc in r.json()["ket_qua_tung_khoa"]:
        assert muc["khoa"] == "…" or (muc["khoa"].startswith("…") and len(muc["khoa"]) <= 5), muc
    # Mọi khoá bị từ chối → lời tổng của module (cũng phản chiếu) không lọt
    c_tc = "phien=GIA-PHAN-CHIEU-TU-CHOI-0011"
    client.nguon.cookie = c_tc
    r2 = client.post(DUONG, json={**THAN, "truong": "mot_cookie"}, headers=QT)
    for dang in _cac_dang(c_tc) + _cac_dang(COOKIE_1)[:6]:
        assert dang not in r2.text, dang


def test_loi_tra_ve_la_cau_cua_loi_khong_phai_cua_module(client):
    client.nguon.cookie = "phien=GIA-TU-CHOI-0012"
    r = client.post(DUONG, json=THAN, headers=QT)
    assert r.status_code == 400
    assert "khong cookie nao dung duoc" not in r.text and "bi tu choi" not in r.text
    assert r.json()["loi"] and r.json()["ket_qua_tung_khoa"][0]["loi"]


# ───────────── nhan_cookie: module đích TỰ KHAI trường nào nhận cookie của tên miền nào ─────────────

def test_truong_bi_mat_khong_khai_nhan_cookie_400_va_khong_doi(client):
    """Leo thang ngang: không được ghi cookie vào khoá API (trường bí mật bất kỳ) của module khác."""
    r0 = client.post("/api/modules/dich-gia/cai", json={"cau_hinh": {"khoa_api": "khoa-api-GIA-that-0001"}}, headers=QT)
    assert r0.status_code == 200, r0.text
    r = client.post(DUONG, json={**THAN, "truong": "khoa_api"}, headers=QT)
    assert r.status_code == 400
    assert "khoa_api" not in r.json()["error"]["message"]
    assert _bi_mat_dich()["khoa_api"] == "khoa-api-GIA-that-0001"
    assert client.nguon.goi == [], "chặn TRƯỚC khi lấy cookie"


@pytest.mark.parametrize("mien", ["khac.example", "sub.vi-du.example", "example"])
def test_mien_khac_ban_khai_400(client, mien):
    r = client.post(DUONG, json={**THAN, "mien": mien}, headers=QT)
    assert r.status_code == 400
    assert client.nguon.goi == [] and _bi_mat_dich() == {}


def test_khong_gui_mien_thi_dung_mien_da_khai(client):
    r = client.post(DUONG, json={"module_dich": "dich-gia", "truong": "cookies"}, headers=QT)
    assert r.status_code == 200, r.text
    assert client.nguon.goi == [{"ho_so": HO_SO, "mien": "vi-du.example"}]


@pytest.mark.parametrize("truong, ly_do", [
    ({"khoa": "c", "nhan": "C", "kieu": "chuoi", "nhan_cookie": {"mien": "vi-du.example"}}, "bí mật"),
    ({"khoa": "c", "nhan": "C", "kieu": "bi_mat", "nhan_cookie": {"mien": "ai"}}, "nhan_cookie"),
    ({"khoa": "c", "nhan": "C", "kieu": "bi_mat", "nhan_cookie": {"mien": "Vi-Du.Example"}}, "nhan_cookie"),
    ({"khoa": "c", "nhan": "C", "kieu": "bi_mat", "nhan_cookie": "vi-du.example"}, "nhan_cookie"),
    ({"khoa": "c", "nhan": "C", "kieu": "bi_mat", "nhan_cookie": {"mien": "vi-du.example", "them": 1}}, "nhan_cookie"),
])
def test_kiem_manifest_nhan_cookie_sai_hinh(truong, ly_do):
    from app.kham_pha import kiem_manifest
    m = dict(MANIFEST_DICH, truong_cau_hinh=[truong])
    loi = kiem_manifest(m)
    assert loi and ly_do in loi, loi


def test_kiem_manifest_nhan_cookie_hop_le_va_muse2api_khai():
    from app.kham_pha import kiem_manifest
    assert kiem_manifest(MANIFEST_DICH) is None
    man = json.loads((THU_MUC_VNC.parent / "muse2api" / "manifest.json").read_text(encoding="utf-8"))
    assert kiem_manifest(man) is None
    assert [t.get("nhan_cookie") for t in man["truong_cau_hinh"] if t["khoa"] == "cookies"] == [{"mien": "muse.ai"}]


def test_loi_404_goi_y_cookie_phien_va_dan_tay(client):
    client.nguon.loi = LoiNguon(404)
    thong_diep = client.post(DUONG, json=THAN, headers=QT).json()["error"]["message"]
    assert "cookie phiên" in thong_diep and "dán" in thong_diep
    client.nguon.loi = None
    client.nguon.cookie = ""
    assert "cookie phiên" in client.post(DUONG, json=THAN, headers=QT).json()["error"]["message"]
