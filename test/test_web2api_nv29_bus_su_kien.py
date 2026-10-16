"""NV29/SEC-003 — Bảo vệ bus sự kiện thiết bị.

Dùng MayChuThat (uvicorn thật) + httpx vì TestClient của starlette 1.7 chạy app tới khi
xong mới trả response — không đọc được luồng SSE vô hạn từ luồng khác.

Mỗi test ghi rõ đột biến chứng minh (bỏ dòng bảo vệ tương ứng → test ĐỎ).
Các đột biến đã chạy lại thủ công, xem nv29-bao-cao.md:
- DB1: bỏ kiểm tồn tại thực thể ở POST /api/thiet-bi/su-kien → test_1 ĐỎ
- DB2: bỏ trần 4KB gia_tri trong chuan_hoa_su_kien_thiet_bi → test_2 ĐỎ
- DB3: bỏ dòng ghi đè `nguon` trong chuan_hoa_su_kien_thiet_bi → test_3 ĐỎ
- DB4: bỏ lọc phe_duyet-chỉ-quản-trị trong BoPhatSuKienThietBi._duoc_nhan → test_4 ĐỎ
- DB5: bỏ vòng kiểm lại khoá trong sse_su_kien_thiet_bi → test_5 ĐỎ
- DB6: bỏ trần theo danh tính trong BoPhatSuKienThietBi.dang_ky → test_6a ĐỎ
- DB6b: bỏ trần toàn cục trong BoPhatSuKienThietBi.dang_ky → test_6b ĐỎ
- DB7: siết nghe CHỈ còn thiet_bi:su_kien_doc (bỏ tương thích) → test_7 ĐỎ
"""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

from app import thiet_bi
from app.main import app
from app.so_module import SoModule
from gia import MayChuThat, ghi_thu_muc

KHOA_QT = "khoa-quan-tri-test-nv29-bus-su-kien-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

#: Mọi DocLuong đang mở của test hiện tại — fixture đóng hết trước khi máy chủ dừng.
_CAC_LUONG: list = []


def _module_gia() -> dict[str, bytes]:
    manifest = {
        "id": "sk-gia",
        "ten": "Module sự kiện giả",
        "mo_ta": "Module giả cho test bus sự kiện NV29",
        "loai": "ung_dung",
        "hang": "A",
        "giao_thuc": "cuc_bo",
        "tien_to": "skgia",
        "cung_cap": ["thiet_bi"],
        "truong_cau_hinh": [],
        "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False, "mang_ra": [], "ghi_chu": ""},
    }
    ma = '''
class Module:
    def __init__(self, manifest):
        self.manifest = manifest

    def nap(self, cau_hinh):
        pass

    async def cai(self, cau_hinh):
        return {"ok": True}

    async def thuc_the(self, loc=None):
        return [
            {"id": "skgia.den", "ten": "Đèn giả", "loai": "den", "khu": "phong",
             "thuoc_tinh": {}, "tinh_nang": ["bat", "tat"], "rui_ro_mac_dinh": "doc"},
            {"id": "skgia.khoa", "ten": "Khoá giả", "loai": "khoa", "khu": "cua",
             "thuoc_tinh": {}, "tinh_nang": ["mo_khoa"], "rui_ro_mac_dinh": "nguy_hiem"},
        ]

    async def trang_thai(self, ids):
        return {i: {"gia_tri": "on", "hop_le": True} for i in ids}

    async def thuc_hien(self, id, hanh_dong, tham_so):
        return {"ok": True}

    async def suc_khoe(self):
        return {"ok": True}

    async def dung(self):
        pass
'''
    return {"manifest.json": json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
            "module.py": ma.encode("utf-8")}


@pytest.fixture
def may_chu(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    # Chu kỳ kiểm lại khoá nhỏ để test đóng luồng nhanh (không sleep dài)
    monkeypatch.setattr(thiet_bi, "chu_ky_kiem_lai_khoa_giay", lambda: 0.2)
    app.state.so = SoModule()
    with MayChuThat(app, lifespan="on") as w:
        with httpx.Client(base_url=w.url, timeout=15) as h:
            ghi_thu_muc(data_dir / "modules" / "sk-gia", _module_gia())
            assert h.post("/api/modules/nap-lai", headers=QT).status_code == 200
            assert h.post("/api/modules/sk-gia/cai", headers=QT, json={"cau_hinh": {}}).status_code == 200
            try:
                yield h, w.url
            finally:
                for doc in list(_CAC_LUONG):
                    doc.dong()
                for doc in list(_CAC_LUONG):
                    t = getattr(doc, "_t", None)
                    if t is not None:
                        t.join(timeout=5)
                _CAC_LUONG.clear()
                thiet_bi.BO_PHAT._nghe.clear()


def _tao_khoa(h, nhan, pham_vi):
    r = h.post("/api/khoa-khach", headers=QT, json={"nhan": nhan, "pham_vi": pham_vi})
    assert r.status_code == 200, r.text
    khoa = r.json()["khoa"]
    return {"Authorization": f"Bearer {khoa['khoa']}"}, khoa["id"]


class DocLuong:
    """Đọc luồng SSE trong luồng riêng, thu các sự kiện data: đã parse."""

    def __init__(self, base_url, headers):
        self.base_url = base_url
        self.headers = headers
        self.su_kien: list = []
        self.status: int | None = None
        self.da_dong = threading.Event()
        self.loi: Exception | None = None
        self._resp = None
        _CAC_LUONG.append(self)

    def bat_dau(self):
        self._t = threading.Thread(target=self._chay, daemon=True)
        self._t.start()
        return self

    def _chay(self):
        try:
            with httpx.Client(base_url=self.base_url, timeout=30) as client:
                with client.stream("GET", "/api/thiet-bi/su-kien",
                                   headers=self.headers) as r:
                    self._resp = r
                    self.status = r.status_code
                    if r.status_code != 200:
                        return
                    for dong in r.iter_lines():
                        if dong.startswith("data:"):
                            try:
                                self.su_kien.append(json.loads(dong[5:].strip()))
                            except ValueError:
                                pass
        except Exception as e:  # noqa: BLE001
            self.loi = e
        finally:
            self.da_dong.set()

    def dong(self):
        r = self._resp
        if r is not None:
            try:
                r.close()
            except Exception:  # noqa: BLE001
                pass

    def doi(self, dieu_kien, han=10.0):
        het = time.time() + han
        while time.time() < het:
            if dieu_kien():
                return True
            time.sleep(0.05)
        return False

    def doi_san_sang(self, han=10.0):
        """Đợi tới khi luồng của CHÍNH reader này nhận header 200 (đăng ký xong ở server)."""
        return self.doi(lambda: self.status == 200, han)


# ───────── POST /api/thiet-bi/su-kien ─────────

def test_1_id_khong_ton_tai_bi_tu_choi(may_chu):
    """Khách A gửi sự kiện giả id không tồn tại → 404. Đột biến DB1 (bỏ kiểm tồn tại) → ĐỎ."""
    h, _ = may_chu
    hd_a, _ = _tao_khoa(h, "App A", ["thiet_bi:su_kien"])
    r = h.post("/api/thiet-bi/su-kien", headers=hd_a,
               json={"id": "khong-ton-tai.cam-bien", "loai": "trang_thai", "gia_tri": "mo"})
    assert r.status_code == 404
    r2 = h.post("/api/thiet-bi/su-kien", headers=hd_a,
                json={"id": "khong_co_dau_cham", "loai": "trang_thai"})
    assert r2.status_code == 404


def test_2_gia_tri_qua_lon_bi_tu_choi(may_chu):
    """gia_tri > 4KB sau serialize → 413; lồng sâu > 4 → 400; loai sai mẫu → 400.
    Đột biến DB2 (bỏ trần kích thước) → ĐỎ."""
    h, _ = may_chu
    hd_a, _ = _tao_khoa(h, "App A", ["thiet_bi:su_kien"])
    r = h.post("/api/thiet-bi/su-kien", headers=hd_a,
               json={"id": "skgia.den", "loai": "trang_thai", "gia_tri": "x" * 5000})
    assert r.status_code == 413
    r2 = h.post("/api/thiet-bi/su-kien", headers=hd_a,
                json={"id": "skgia.den", "loai": "trang_thai",
                      "gia_tri": {"a": {"b": {"c": {"d": {"e": 1}}}}}})
    assert r2.status_code == 400
    r3 = h.post("/api/thiet-bi/su-kien", headers=hd_a,
                json={"id": "skgia.den", "loai": "loai khong hop le!!"})
    assert r3.status_code == 400


def test_3_khong_gia_duoc_truong_nguon(may_chu):
    """Người gửi đặt `nguon` giả → lõi ghi đè bằng danh tính thật. Đột biến DB3 → ĐỎ."""
    h, url = may_chu
    hd_a, id_a = _tao_khoa(h, "App A", ["thiet_bi:su_kien"])
    doc = DocLuong(url, QT).bat_dau()
    assert doc.doi_san_sang()
    r = h.post("/api/thiet-bi/su-kien", headers=hd_a,
               json={"id": "skgia.den", "loai": "trang_thai", "gia_tri": "on",
                     "nguon": "quan_tri"})
    assert r.status_code == 200
    assert doc.doi(lambda: len(doc.su_kien) >= 1)
    sk = doc.su_kien[0]
    assert sk["nguon"] == f"khach:{id_a}"
    assert sk["nguon"] != "quan_tri"


def test_8_khong_khoa_401_vai_thap_403(may_chu):
    """Không khoá → 401; khoá thiếu phạm vi → 403 (cả gửi và nghe)."""
    h, _ = may_chu
    hd_yeu, _ = _tao_khoa(h, "App yếu", ["thiet_bi:doc"])
    assert h.get("/api/thiet-bi/su-kien").status_code == 401
    assert h.post("/api/thiet-bi/su-kien", json={"id": "skgia.den"}).status_code == 401
    assert h.get("/api/thiet-bi/su-kien", headers=hd_yeu).status_code == 403
    r = h.post("/api/thiet-bi/su-kien", headers=hd_yeu, json={"id": "skgia.den"})
    assert r.status_code == 403


# ───────── Luồng SSE: lọc phát, kiểm lại khoá, trần ─────────

def test_4_khach_b_khong_nhan_su_kien_phe_duyet_cua_a(may_chu):
    """Đọc luồng thật: B nhận sự kiện thường của A, KHÔNG nhận sự kiện phê duyệt.
    Đột biến DB4 (bỏ lọc phe_duyet chỉ-quản-trị) → ĐỎ."""
    h, url = may_chu
    hd_a, _ = _tao_khoa(h, "App A", ["thiet_bi:ghi", "thiet_bi:su_kien"])
    hd_b, _ = _tao_khoa(h, "App B", ["thiet_bi:su_kien"])
    doc_b = DocLuong(url, hd_b).bat_dau()
    assert doc_b.doi_san_sang()

    # A gửi sự kiện thường → B phải nhận
    r = h.post("/api/thiet-bi/su-kien", headers=hd_a,
               json={"id": "skgia.den", "loai": "trang_thai", "gia_tri": "on"})
    assert r.status_code == 200
    assert doc_b.doi(lambda: any(sk.get("loai") == "trang_thai" for sk in doc_b.su_kien))

    # A đề xuất hành động nguy hiểm → vào hàng chờ phê duyệt, lõi phát sự kiện phe_duyet_moi
    r2 = h.post("/api/thiet-bi/skgia.khoa/hanh-dong", headers=hd_a,
                json={"hanh_dong": "mo_khoa", "tham_so": {"ma_pin": "9999"}, "ly_do": "kiem tra"})
    assert r2.status_code == 202

    # B không được thấy sự kiện phê duyệt trong ~2s (keep-alive 0.2s đánh thức vòng đọc)
    time.sleep(2.0)
    cac_loai_b = [sk.get("loai") for sk in doc_b.su_kien]
    assert not any(str(loai).startswith("phe_duyet") for loai in cac_loai_b), cac_loai_b
    # và không lộ tham số nhạy cảm của A cho B
    assert "9999" not in json.dumps(doc_b.su_kien, ensure_ascii=False)


def test_4b_quan_tri_nhan_su_kien_phe_duyet(may_chu):
    """Quản trị vẫn nhận sự kiện phê duyệt (kênh duyệt của quản trị không bị chặn nhầm)."""
    h, url = may_chu
    hd_a, _ = _tao_khoa(h, "App A", ["thiet_bi:ghi", "thiet_bi:su_kien"])
    doc_qt = DocLuong(url, QT).bat_dau()
    assert doc_qt.doi_san_sang()
    r = h.post("/api/thiet-bi/skgia.khoa/hanh-dong", headers=hd_a,
               json={"hanh_dong": "mo_khoa", "tham_so": {}, "ly_do": "kiem tra"})
    assert r.status_code == 202
    assert doc_qt.doi(lambda: any(sk.get("loai") == "phe_duyet_moi" for sk in doc_qt.su_kien))


def test_5_khoa_bi_tat_giua_chung_luong_dong(may_chu):
    """Khoá bị tắt giữa chừng → luồng đóng (kiểm lại khoá mỗi chu kỳ). Đột biến DB5 → ĐỎ."""
    h, url = may_chu
    hd_a, id_a = _tao_khoa(h, "App A", ["thiet_bi:su_kien"])
    doc = DocLuong(url, hd_a).bat_dau()
    assert doc.doi_san_sang()
    bat_dau = time.time()
    r = h.patch(f"/api/khoa-khach/{id_a}", headers=QT, json={"bat": False})
    assert r.status_code == 200
    assert doc.doi(lambda: doc.da_dong.is_set(), han=10.0)
    assert time.time() - bat_dau <= 20.0


def test_6a_tran_nguoi_nghe_theo_danh_tinh_429(may_chu, monkeypatch):
    """Vượt trần theo danh tính → 429 + Retry-After. Đột biến DB6 → ĐỎ."""
    h, url = may_chu
    monkeypatch.setenv("WEB2API_SSE_NGHE_TOI_DA_MOI_DANH_TINH", "1")
    hd_a, _ = _tao_khoa(h, "App A", ["thiet_bi:su_kien"])
    doc = DocLuong(url, hd_a).bat_dau()
    assert doc.doi_san_sang()
    # dùng h.stream (không đọc thân vô hạn): đột biến DB6 mở luồng thứ hai thành công → status 200 → ĐỎ
    with h.stream("GET", "/api/thiet-bi/su-kien", headers=hd_a, timeout=10) as r:
        assert r.status_code == 429
        assert r.headers.get("Retry-After") == "30"


def test_6b_tran_nguoi_nghe_toan_cuc_429(may_chu, monkeypatch):
    """Vượt trần toàn cục → 429 + Retry-After. Đột biến DB6b → ĐỎ."""
    h, url = may_chu
    monkeypatch.setenv("WEB2API_SSE_NGHE_TOI_DA_TOAN_CUC", "1")
    hd_a, _ = _tao_khoa(h, "App A", ["thiet_bi:su_kien"])
    hd_b, _ = _tao_khoa(h, "App B", ["thiet_bi:su_kien"])
    doc = DocLuong(url, hd_a).bat_dau()
    assert doc.doi_san_sang()
    with h.stream("GET", "/api/thiet-bi/su-kien", headers=hd_b, timeout=10) as r:
        assert r.status_code == 429
        assert r.headers.get("Retry-After") == "30"


def test_7_khoa_cu_sao_va_su_kien_van_nghe_duoc(may_chu):
    """Hồi quy tương thích: khoá `*` và khoá `thiet_bi:su_kien` (cũ) vẫn nghe được.
    Đột biến DB7 (chỉ nhận su_kien_doc) → ĐỎ."""
    h, url = may_chu
    hd_sao, _ = _tao_khoa(h, "App sao", ["*"])
    hd_cu, _ = _tao_khoa(h, "App cũ", ["thiet_bi:su_kien"])
    hd_moi, _ = _tao_khoa(h, "App mới", ["thiet_bi:su_kien_doc"])
    doc_sao = DocLuong(url, hd_sao).bat_dau()
    doc_cu = DocLuong(url, hd_cu).bat_dau()
    doc_moi = DocLuong(url, hd_moi).bat_dau()
    # Chờ cả 3 đăng ký phía server (tránh race: _nghe>=1 xảy ra trước khi client
    # nhận header, nên phải chờ riêng từng status được gán).
    assert doc_sao.doi(lambda: len(thiet_bi.BO_PHAT._nghe) >= 3)
    for doc in (doc_sao, doc_cu, doc_moi):
        assert doc.doi(lambda: doc.status is not None), "luồng SSE phải nhận header phản hồi"
    assert doc_sao.status == 200 and doc_cu.status == 200 and doc_moi.status == 200
    r = h.post("/api/thiet-bi/su-kien", headers=QT,
               json={"id": "skgia.den", "loai": "trang_thai", "gia_tri": "on"})
    assert r.status_code == 200
    for ten, doc in (("sao", doc_sao), ("cu", doc_cu), ("moi", doc_moi)):
        ok = doc.doi(lambda: len(doc.su_kien) >= 1)
        print(f"\nDEBUG test_7: {ten} status={doc.status} nhan={len(doc.su_kien)} loi={doc.loi} nghe={len(thiet_bi.BO_PHAT._nghe)}")
        assert ok, "người nghe tương thích phải nhận được sự kiện"


def test_9_khoa_chi_nghe_khong_gui_duoc(may_chu):
    """Tách phạm vi: khoá chỉ có `thiet_bi:su_kien_doc` nghe được nhưng KHÔNG gửi được."""
    h, url = may_chu
    hd_nghe, _ = _tao_khoa(h, "App nghe", ["thiet_bi:su_kien_doc"])
    doc = DocLuong(url, hd_nghe).bat_dau()
    assert doc.doi_san_sang()
    assert doc.status == 200
    r = h.post("/api/thiet-bi/su-kien", headers=hd_nghe, json={"id": "skgia.den"})
    assert r.status_code == 403
