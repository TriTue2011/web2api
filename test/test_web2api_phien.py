"""Kiểm thử kho phiên tài khoản và bộ quét định kỳ ở lõi (Việc C7).

Các yêu cầu nghiệm thu:
1. Lưu -> nạp lại từ đĩa -> lấy lại đúng; tệp quyền 0600, thư mục 0700.
2. liet_ke và GET /api/phien KHÔNG chứa du_lieu hay chuỗi bí mật (chuỗi giả không lọt vào phản hồi/log).
3. module / tai_khoan có ../ hoặc ký tự lạ -> 400.
4. Khoá khách gọi /api/phien* -> 403.
5. Bộ quét: phien_con_song False -> lam_moi_phien trả du_lieu mới -> con_song;
   không làm mới được -> can_dang_nhap và ghi sổ cảnh báo ĐÚNG MỘT LẦN dù quét nhiều vòng;
   luu phiên mới -> về con_song.
6. Module không có phien_con_song -> «chua_ro», không lỗi.
7. Hết hạn (het_han trong quá khứ) -> can_dang_nhap.
8. Gỡ module thì phiên còn trên đĩa nhưng không bị quét.
9. Lõi chạy bình thường khi không có phiên nào (không lỗi/cảnh báo).
10. Test giao diện Chrome: hiện cảnh báo «n tài khoản cần đăng nhập lại» khi can_dang_nhap_tong > 0.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import khoa_khach, nhat_ky, phien, so_module
from app.main import app
from gia import MayChuThat
from ui_chung import CO_CHROME, trang, trinh_duyet  # noqa: F401

KHOA_QT = "khoa-quan-tri-phien-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
CHUOI_BI_MAT_GIA = "phien-gia-bi-mat-000"


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return tmp_path


def _tha_module_mock(du_lieu: Path, id_: str, ma_python: str) -> None:
    thu_muc = du_lieu / "modules" / id_
    thu_muc.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": id_,
        "ten": f"Mock {id_}",
        "mo_ta": "Module giả để kiểm tra phiên",
        "phien_ban": "0.1.0",
        "loai": "ai",
        "giao_thuc": "openai",
        "tien_to": id_.replace("-", ""),
        "cung_cap": ["chat"],
        "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []},
    }
    (thu_muc / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (thu_muc / "module.py").write_text(ma_python, encoding="utf-8")


# ───────────── Ca 1: Lưu -> nạp lại từ đĩa -> lấy lại đúng; tệp 0600, thư mục 0700 ─────────────

def test_luu_nap_lai_quyen_tep_va_thu_muc(du_lieu):
    du_lieu_phien = {"token": "gia-tri-token-123", "cookie": "phien=abc"}
    luu_kq = phien.luu("mod-a", "tai-khoan-1", du_lieu_phien, het_han=None)
    assert luu_kq["tai_khoan"] == "tai-khoan-1"
    assert luu_kq["trang_thai"] == "con_song"
    assert luu_kq["du_lieu"] == du_lieu_phien

    tep = du_lieu / "bi_mat" / "phien" / "mod-a" / "tai-khoan-1.json"
    assert tep.is_file(), "Tệp phiên phải tồn tại"

    # Kiểm tra quyền 0600 cho tệp và 0700 cho thư mục
    quyen_tep = tep.stat().st_mode & 0o777
    assert quyen_tep == 0o600, f"Quyền tệp phiên phải là 0600, nhận được: {oct(quyen_tep)}"

    quyen_tm = tep.parent.stat().st_mode & 0o777
    assert quyen_tm == 0o700, f"Quyền thư mục phiên phải là 0700, nhận được: {oct(quyen_tm)}"

    quyen_goc_phien = tep.parent.parent.stat().st_mode & 0o777
    assert quyen_goc_phien == 0o700, f"Quyền thư mục bi_mat/phien phải là 0700, nhận được: {oct(quyen_goc_phien)}"

    # Nạp lại từ đĩa (mô phỏng khởi động lại)
    lay_kq = phien.lay("mod-a", "tai-khoan-1")
    assert lay_kq is not None
    assert lay_kq["tai_khoan"] == "tai-khoan-1"
    assert lay_kq["du_lieu"] == du_lieu_phien
    assert lay_kq["trang_thai"] == "con_song"
    assert lay_kq["so_lan_loi"] == 0

    # Trả bản sao (không ảnh hưởng bản lưu khi sửa)
    lay_kq["du_lieu"]["token"] = "da-doi"
    assert phien.lay("mod-a", "tai-khoan-1")["du_lieu"]["token"] == "gia-tri-token-123"


# ───────────── Ca 2: liet_ke / GET /api/phien KHÔNG chứa du_lieu hay chuỗi bí mật ─────────────

def test_liet_ke_va_api_khong_lo_bi_mat(du_lieu):
    phien.luu("mod-bao-mat", "tk-bi-mat", {"bi_mat_rieng": CHUOI_BI_MAT_GIA})

    # Kiểm tra liet_ke() trong tiến trình
    ds = phien.liet_ke()
    assert len(ds) == 1
    muc = ds[0]
    assert "du_lieu" not in muc, "liet_ke() không được chứa trường du_lieu"
    assert CHUOI_BI_MAT_GIA not in json.dumps(muc), "Chuỗi bí mật không được xuất hiện trong liet_ke()"

    # Kiểm tra GET /api/phien
    with TestClient(app) as c:
        r = c.get("/api/phien", headers=QT)
        assert r.status_code == 200
        assert CHUOI_BI_MAT_GIA not in r.text, "Chuỗi bí mật không được xuất hiện trong phản hồi API /api/phien"
        j = r.json()
        assert j["tong"] == 1
        assert j["can_dang_nhap_tong"] == 0
        assert len(j["phien"]) == 1
        assert "du_lieu" not in j["phien"][0]

    # Kiểm tra sổ nhật ký kênh phien
    nhat_ky_phien = nhat_ky.doc(kenh="phien", gioi_han=100)
    for dong in nhat_ky_phien:
        assert CHUOI_BI_MAT_GIA not in json.dumps(dong), "Chuỗi bí mật không được lọt vào sổ nhật ký kênh phien"


# ───────────── Ca 3: module / tai_khoan có ../ hoặc ký tự lạ -> 400 ─────────────

def test_id_khong_hop_le_tra_400(du_lieu):
    with TestClient(app) as c:
        # module có .. hoặc ký tự lạ
        r1 = c.delete("/api/phien/mod..bad/acc", headers=QT)
        assert r1.status_code == 400

        # tai_khoan có .. hoặc ký tự lạ
        r2 = c.delete("/api/phien/mod-a/tk..bad", headers=QT)
        assert r2.status_code == 400

        # Ký tự lạ ngoài [a-z0-9_-]
        r3 = c.delete("/api/phien/mod$a/acc", headers=QT)
        assert r3.status_code == 400

        r4 = c.post("/api/phien/mod-a/acc!/kiem", headers=QT)
        assert r4.status_code == 400

    # Kiểm tra hàm nội bộ ném ValueError
    with pytest.raises(ValueError):
        phien.luu("../mod", "acc", {"a": 1})

    with pytest.raises(ValueError):
        phien.lay("mod", "../acc")

    with pytest.raises(ValueError):
        phien.xoa("mod/con", "acc")


# ───────────── Ca 4: Khoá khách gọi /api/phien* -> 403 ─────────────

def test_khoa_khach_bi_chan_403(du_lieu):
    # Tạo khoá khách có phạm vi đầy đủ
    ban_ghi, khoa_tho = khoa_khach.tao_khoa("Khách kiểm tra", ["*"])
    headers_khach = {"Authorization": f"Bearer {khoa_tho}"}

    with TestClient(app) as c:
        # GET /api/phien
        r1 = c.get("/api/phien", headers=headers_khach)
        assert r1.status_code == 403

        # DELETE /api/phien/{mod}/{tk}
        r2 = c.delete("/api/phien/mod-a/tk-1", headers=headers_khach)
        assert r2.status_code == 403

        # POST /api/phien/{mod}/{tk}/kiem
        r3 = c.post("/api/phien/mod-a/tk-1/kiem", headers=headers_khach)
        assert r3.status_code == 403


# ───────────── Ca 5: Bộ quét thang xử lý lỗi & cảnh báo ĐÚNG 1 LẦN ─────────────

MA_MODULE_QUET = """
class Module:
    def __init__(self, m):
        self.manifest = m
        self.tien_to = m["tien_to"]
        self.song = True
        self.du_lieu_lam_moi = None

    def nap(self, cfg): pass
    async def cai(self, cfg): return {"ok": True, "cau_hinh_luu": {}}
    async def chat(self, b, s): return 200, {}, b"{}"
    async def models(self): return [{"id": self.tien_to + "/test", "object": "model"}]
    async def suc_khoe(self): return {"ok": True}
    async def dung(self): pass

    async def phien_con_song(self, du_lieu: dict) -> bool:
        if du_lieu.get("token") == "token-moi":
            return True
        return self.song

    async def lam_moi_phien(self, du_lieu: dict) -> dict | None:
        return self.du_lieu_lam_moi
"""


def test_bo_quet_thang_xu_ly_va_canh_bao_mot_lan(du_lieu):
    _tha_module_mock(du_lieu, "mod-quet", MA_MODULE_QUET)

    with TestClient(app) as c:
        # Nạp module vào sổ
        r_nap = c.post("/api/modules/nap-lai", headers=QT)
        assert r_nap.status_code == 200
        # Liên kết module
        r_cai = c.post("/api/modules/mod-quet/cai", json={"cau_hinh": {}}, headers=QT)
        assert r_cai.status_code == 200

        so = c.app.state.so
        the_hien = so.dang_chay["mod-quet"]

        # Lưu phiên ban đầu
        phien.luu("mod-quet", "tk-1", {"token": "token-cu"})

        # Trường hợp A: phien_con_song trả False nhưng lam_moi_phien trả du_lieu mới
        the_hien.song = False
        the_hien.du_lieu_lam_moi = {"token": "token-moi"}

        # Chạy kiểm ngay một phiên
        r_kiem = c.post("/api/phien/mod-quet/tk-1/kiem", headers=QT)
        assert r_kiem.status_code == 200
        assert r_kiem.json()["trang_thai"] == "con_song"

        # Phiên trên đĩa đã được cập nhật du_lieu mới
        p_luu = phien.lay("mod-quet", "tk-1")
        assert p_luu["du_lieu"]["token"] == "token-moi"
        assert p_luu["trang_thai"] == "con_song"

        # Trường hợp B: phien_con_song trả False và lam_moi_phien thất bại (None)
        the_hien.song = False
        the_hien.du_lieu_lam_moi = None
        # Đổi lại dữ liệu thành token cũ
        phien.luu("mod-quet", "tk-1", {"token": "token-hong"})

        # Vòng 1: quét thất bại -> can_dang_nhap và ghi sổ cảnh báo
        asyncio.run(phien.quet_mot_vong(so))
        p_sau1 = phien.lay("mod-quet", "tk-1")
        assert p_sau1["trang_thai"] == "can_dang_nhap"

        # Đếm số dòng cảnh báo kênh phien
        nk1 = [d for d in nhat_ky.doc(kenh="phien", gioi_han=100) if d.get("muc") == "canh_bao"]
        assert len(nk1) == 1, "Phải có đúng 1 dòng cảnh báo ở lần đầu phát hiện can_dang_nhap"

        # Vòng 2 & 3: quét tiếp nhiều vòng (phiên đang can_dang_nhap bị bỏ qua, không gọi lại module nhiều lần)
        asyncio.run(phien.quet_mot_vong(so))
        asyncio.run(phien.quet_mot_vong(so))

        nk2 = [d for d in nhat_ky.doc(kenh="phien", gioi_han=100) if d.get("muc") == "canh_bao"]
        assert len(nk2) == 1, "Cảnh báo không được lặp lại ở các vòng quét sau"

        # Người dùng đăng nhập lại và lưu phiên mới -> tự về con_song
        phien.luu("mod-quet", "tk-1", {"token": "token-moi"})
        p_moi = phien.lay("mod-quet", "tk-1")
        assert p_moi["trang_thai"] == "con_song"


# ───────────── Ca 6: Module không có phien_con_song -> «chua_ro», không lỗi ─────────────

MA_MODULE_KHONG_PHIEN = """
class Module:
    def __init__(self, m):
        self.manifest = m
        self.tien_to = m["tien_to"]
    def nap(self, cfg): pass
    async def cai(self, cfg): return {"ok": True, "cau_hinh_luu": {}}
    async def chat(self, b, s): return 200, {}, b"{}"
    async def models(self): return [{"id": self.tien_to + "/test", "object": "model"}]
    async def suc_khoe(self): return {"ok": True}
    async def dung(self): pass
"""


def test_module_khong_co_phien_con_song(du_lieu):
    _tha_module_mock(du_lieu, "mod-khong-phien", MA_MODULE_KHONG_PHIEN)

    with TestClient(app) as c:
        c.post("/api/modules/nap-lai", headers=QT)
        c.post("/api/modules/mod-khong-phien/cai", json={"cau_hinh": {}}, headers=QT)

        so = c.app.state.so
        phien.luu("mod-khong-phien", "tk-1", {"data": 123})

        # Kiểm tra phiên
        r = c.post("/api/phien/mod-khong-phien/tk-1/kiem", headers=QT)
        assert r.status_code == 200
        assert r.json()["trang_thai"] == "chua_ro"

        # Quét một vòng: không lỗi và bỏ qua module không có phien_con_song
        kq_quet = asyncio.run(phien.quet_mot_vong(so))
        assert kq_quet["da_quet"] == 0


# ───────────── Ca 7: Hết hạn (het_han đã qua) -> can_dang_nhap ─────────────

def test_phien_het_han_thanh_can_dang_nhap(du_lieu):
    qua_khu = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    p = phien.luu("mod-hh", "tk-hh", {"token": "x"}, het_han=qua_khu)
    assert p["trang_thai"] == "can_dang_nhap"

    lay_p = phien.lay("mod-hh", "tk-hh")
    assert lay_p["trang_thai"] == "can_dang_nhap"

    # Kiểm tra qua API
    with TestClient(app) as c:
        r = c.get("/api/phien", headers=QT)
        assert r.status_code == 200
        j = r.json()
        assert j["can_dang_nhap_tong"] == 1


# ───────────── Ca 8: Gỡ module thì phiên còn trên đĩa nhưng không bị quét ─────────────

def test_go_module_giu_phien_khong_quet(du_lieu):
    _tha_module_mock(du_lieu, "mod-go", MA_MODULE_QUET)

    with TestClient(app) as c:
        c.post("/api/modules/nap-lai", headers=QT)
        c.post("/api/modules/mod-go/cai", json={"cau_hinh": {}}, headers=QT)

        phien.luu("mod-go", "tk-go", {"token": "token-1"})
        tep_phien = du_lieu / "bi_mat" / "phien" / "mod-go" / "tk-go.json"
        assert tep_phien.is_file()

        # Gỡ module
        r_go = c.delete("/api/modules/mod-go", headers=QT)
        assert r_go.status_code == 200

        # Phiên trên đĩa vẫn còn nguyên
        assert tep_phien.is_file()
        assert phien.lay("mod-go", "tk-go") is not None

        # Bộ quét bỏ qua vì module không còn chạy
        so = c.app.state.so
        kq_quet = asyncio.run(phien.quet_mot_vong(so))
        assert kq_quet["da_quet"] == 0


# ───────────── Ca 9: Lõi chạy bình thường khi không có phiên nào ─────────────

def test_loi_chay_binh_thuong_khi_khong_co_phien(du_lieu):
    with TestClient(app) as c:
        # GET /api/tinh-nang
        r_tn = c.get("/api/tinh-nang", headers=QT)
        assert r_tn.status_code == 200
        j_tn = r_tn.json()
        assert j_tn["phien"] == {"tong": 0, "can_dang_nhap": 0}

        # GET /api/phien
        r_phien = c.get("/api/phien", headers=QT)
        assert r_phien.status_code == 200
        j_p = r_phien.json()
        assert j_p == {"phien": [], "tong": 0, "can_dang_nhap_tong": 0}

        # Quét vòng: không lỗi
        so = c.app.state.so
        kq = asyncio.run(phien.quet_mot_vong(so))
        assert kq["da_quet"] == 0


# ───────────── Ca 10: Test giao diện Chrome (cảnh báo khi can_dang_nhap_tong > 0) ─────────────

@pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")
def test_giao_dien_canh_bao_can_dang_nhap(du_lieu, trang):
    """Chrome thật: phiên hết hạn thì trang Module hiện «n tài khoản cần đăng nhập lại»; không có phiên thì dòng này KHÔNG có."""
    with MayChuThat(app, lifespan="on") as w:
        trang.di(f"{w.url}/He-thong.dc.html")
        trang.cho("document.querySelector('x-dc')?.dataset.dc === 'san-sang'", mo_ta="trang dựng xong")
        assert trang.js("document.getElementById('can-dang-nhap-canh-bao') === null"), "chưa có phiên nào: không được hiện cảnh báo"
        qua_khu = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        phien.luu("mod-ui", "tk-ui", {"token": "x"}, het_han=qua_khu)
        trang.di(f"{w.url}/He-thong.dc.html")
        trang.cho("(document.getElementById('can-dang-nhap-canh-bao')?.innerText || '').includes('1 tài khoản cần đăng nhập lại')",
                  mo_ta="cảnh báo cần đăng nhập lại")
