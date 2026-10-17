"""Kiểm thử điều khiển tự động qua Marionette — NV92 P1 (Cách B).

Máy khách Marionette giả (kịch bản) + bộ chạy tiến trình giả: không cần
Firefox thật. Kiểm chứng:
- Mở Firefox: cờ ``--marionette`` + ``user.js`` mang cổng riêng trên
  127.0.0.1; mỗi hồ sơ một cổng; đóng thì trả cổng.
- Giành/nhả quyền điều khiển máy: giành lần hai → 409; giành khi có người
  dùng noVNC → 409; nhả lặp lại vẫn ok (luỹ đẳng).
- Hồ sơ chromium → 501; chưa giành mà gọi nguyên thuỷ → 409;
  hồ sơ chưa mở → 404.
- Các nguyên thuỷ: điều hướng / điền / bấm / chờ / tín hiệu / cookie trực
  tiếp / chụp trạng thái; giá trị điền KHÔNG vào log.
- Lỗi Marionette → 502 chỉ mang mã lỗi, không mang nội dung trang;
  lỗi lạ → đóng cửa (nhả quyền).
- Mở noVNC khi máy đang giữ → 409 (người và máy không cùng lúc).
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

# Đưa thư mục dịch vụ vào sys.path
GOC_DICH_VU = Path(__file__).resolve().parent
import sys

if str(GOC_DICH_VU) not in sys.path:
    sys.path.insert(0, str(GOC_DICH_VU))

import server
from server import QuanLyHoSo, app

# Lấy lại các lớp giả từ test_server để không viết hai lần.
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "nv92_test_server_goc", GOC_DICH_VU / "test_server.py")
_mod_goc = importlib.util.module_from_spec(_spec)
sys.modules["nv92_test_server_goc"] = _mod_goc
_spec.loader.exec_module(_mod_goc)

BoChayGia = _mod_goc.BoChayGia
BoKiemTraKetNoiGia = _mod_goc.BoKiemTraKetNoiGia
KHOA_TEST = _mod_goc.KHOA_TEST
HEADER_AUTH = _mod_goc.HEADER_AUTH
TienTrinhGia = _mod_goc.TienTrinhGia

from marionette import LoiMarionette


class MarionetteGia:
    """Máy khách Marionette giả: kịch bản theo URL hiện tại.

    ``kich_ban``: {url: {"tin_hieu_script": [...], "cookie": {ten: gia_tri}, ...}}.
    Ghi lại ``hanh_dong`` — với ``dien`` chỉ ghi TÊN hành động + bộ chọn
    (không ghi giá trị — như hợp đồng an toàn).
    """

    def __init__(self, kich_ban: dict | None = None):
        self.kich_ban = dict(kich_ban or {})
        self.hanh_dong: list[tuple] = []
        self.url = "about:blank"
        self.da_noi = False
        self.loi_tiep_theo: str | None = None
        self.loi_la_tiep_theo: bool = False

    # ── nối/phiên ──
    def ket_noi(self) -> None:
        if self.loi_tiep_theo:
            raise LoiMarionette(self.loi_tiep_theo)
        self.da_noi = True

    def dong(self) -> None:
        self.da_noi = False

    def phien_moi(self) -> str:
        return "phien-gia"

    def xoa_phien(self) -> None:
        pass

    # ── điều hướng/đọc ──
    def dieu_huong(self, url: str) -> None:
        self._co_the_loi()
        self.url = url
        self.hanh_dong.append(("dieu_huong", url))

    def url_hien_tai(self) -> str:
        return self.url

    def tieu_de(self) -> str:
        if "google.com" in self.url:
            return "Đăng nhập - Tài khoản Google"
        return "Trang giả"

    def cookie(self) -> list[dict]:
        cfg = self.kich_ban.get(self.url, {})
        return [
            {"name": ten, "value": gt, "domain": ".google.com"}
            for ten, gt in (cfg.get("cookie") or {}).items()
        ]

    def thuc_thi(self, script: str, doi_so: list | None = None) -> Any:
        self._co_the_loi()
        cfg = self.kich_ban.get(self.url, {})
        return list(cfg.get("tin_hieu_script") or [])

    # ── phần tử ──
    def cho_phan_tu(self, bo_chon: str, timeout: float = 10.0) -> bool:
        self._co_the_loi()
        return bo_chon in (self.kich_ban.get(self.url, {}).get("phan_tu") or [])

    def bam(self, bo_chon: str, timeout: float = 10.0) -> None:
        self._co_the_loi()
        if not self.cho_phan_tu(bo_chon, 0):
            raise LoiMarionette("no such element")
        self.hanh_dong.append(("bam", bo_chon))

    def dien(self, bo_chon: str, van_ban: str, timeout: float = 10.0,
             xoa_truoc: bool = True) -> None:
        self._co_the_loi()
        if not self.cho_phan_tu(bo_chon, 0):
            raise LoiMarionette("no such element")
        self.hanh_dong.append(("dien", bo_chon))

    def _co_the_loi(self) -> None:
        if self.loi_la_tiep_theo:
            self.loi_la_tiep_theo = False
            raise RuntimeError("lỗi lạ giả lập")
        if self.loi_tiep_theo:
            ma = self.loi_tiep_theo
            self.loi_tiep_theo = None
            raise LoiMarionette(ma)


@pytest.fixture
def mt_dk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    thu_muc_hoso = tmp_path / "hoso"
    thu_muc_hoso.mkdir()
    (tmp_path / "tam").mkdir()
    monkeypatch.setenv("TRINH_DUYET_KHOA", KHOA_TEST)
    monkeypatch.setenv("TRINH_DUYET_THU_MUC_GOC", str(thu_muc_hoso))
    monkeypatch.setattr(__import__("tempfile"), "tempdir", str(tmp_path / "tam"))
    monkeypatch.setenv("TRINH_DUYET_CONG_TU", "6200")
    monkeypatch.setenv("TRINH_DUYET_CONG_DEN", "6205")
    monkeypatch.setenv("TRINH_DUYET_MARIONETTE_TU", "32100")
    monkeypatch.setenv("TRINH_DUYET_MARIONETTE_DEN", "32105")
    monkeypatch.setenv("TRINH_DUYET_TOI_DA", "3")
    monkeypatch.setenv("TRINH_DUYET_IDLE_PHUT", "120")
    monkeypatch.setenv("TRINH_DUYET_HOST_NOI_BO", "trinh-duyet")
    monkeypatch.setenv("TRINH_DUYET_TOI_DA_HO_SO", "20")
    monkeypatch.setenv("TRINH_DUYET_TOI_DA_PHUT", "480")
    monkeypatch.setattr(server, "_khoa_dich_vu", KHOA_TEST, raising=False)

    gia: MarionetteGia | None = None

    def tao_gia(cong: int) -> MarionetteGia:
        nonlocal gia
        assert 32100 <= cong <= 32105, f"cổng ngoài dải cấp phát: {cong}"
        kich_ban = {
            "https://accounts.google.com/": {
                "tin_hieu_script": [],
                "phan_tu": ["#identifierId", "#identifierNext"],
                "cookie": {},
            },
        }
        gia = MarionetteGia(kich_ban)
        return gia

    monkeypatch.setattr(server, "_tao_marionette", tao_gia)

    bo_chay = BoChayGia()
    bo_kiem_tra = BoKiemTraKetNoiGia()
    cau_hinh = server.lay_cau_hinh()
    cau_hinh["cho_khoi_dong_giay"] = 0
    quan_ly = QuanLyHoSo(cau_hinh=cau_hinh, bo_chay=bo_chay, bo_kiem_tra=bo_kiem_tra)
    monkeypatch.setattr(server, "quan_ly_toan_cuc", quan_ly)
    return {
        "thu_muc_hoso": thu_muc_hoso,
        "bo_chay": bo_chay,
        "bo_kiem_tra": bo_kiem_tra,
        "quan_ly": quan_ly,
        "lay_gia": lambda: gia,
        "monkeypatch": monkeypatch,
    }


def _mo_firefox(mt_dk, client: TestClient, ten: str = "gg") -> str:
    res = client.post("/ho-so", headers=HEADER_AUTH, json={"ten": ten, "loai": "firefox"})
    assert res.status_code == 200, res.text
    ho_so = res.json()["ho_so"]
    res = client.post("/ho-so/mo", headers=HEADER_AUTH,
                      json={"ho_so": ho_so, "loai": "firefox"})
    assert res.status_code == 200, res.text
    return ho_so


# ── Mở hồ sơ: cờ --marionette + user.js + cấp cổng ─────────────────────────────

def test_mo_firefox_co_co_marionette_va_user_js(mt_dk):
    with TestClient(app) as client:
        ho_so = _mo_firefox(mt_dk, client)
        lenh_ff = [l for l in mt_dk["bo_chay"].lich_su_lenh if "firefox" in (l[0] if l else "")]
        assert lenh_ff, "không thấy lệnh khởi động firefox"
        assert "--marionette" in lenh_ff[0]
        user_js = mt_dk["thu_muc_hoso"] / ho_so / "user.js"
        noi_dung = user_js.read_text(encoding="utf-8")
        m = re.search(r'user_pref\("marionette\.port", (\d+)\);', noi_dung)
        assert m, f"user.js thiếu marionette.port:\n{noi_dung}"
        cong = int(m.group(1))
        assert 32100 <= cong <= 32105
        assert cong in mt_dk["quan_ly"]._cong_marionette_dang_dung


def test_hai_ho_so_cong_khac_nhau_dong_thi_tra(mt_dk):
    with TestClient(app) as client:
        hs1 = _mo_firefox(mt_dk, client, "a")
        hs2 = _mo_firefox(mt_dk, client, "b")
        ql = mt_dk["quan_ly"]
        c1 = ql.danh_sach_mo[hs1].cong_marionette
        c2 = ql.danh_sach_mo[hs2].cong_marionette
        assert c1 != c2
        client.post(f"/ho-so/{hs1}/dong", headers=HEADER_AUTH, json={"giu_phien": True})
        assert c1 not in ql._cong_marionette_dang_dung
        assert c2 in ql._cong_marionette_dang_dung


def test_mo_lai_giu_cong_cu(mt_dk):
    with TestClient(app) as client:
        ho_so = _mo_firefox(mt_dk, client)
        ql = mt_dk["quan_ly"]
        cong_cu = ql.danh_sach_mo[ho_so].cong_marionette
        res = client.post("/ho-so/mo", headers=HEADER_AUTH,
                          json={"ho_so": ho_so, "loai": "firefox"})
        assert res.status_code == 200
        assert ql.danh_sach_mo[ho_so].cong_marionette == cong_cu


# ── Giành / nhả ───────────────────────────────────────────────────────────────

def test_gianh_nha_luy_dang(mt_dk):
    with TestClient(app) as client:
        ho_so = _mo_firefox(mt_dk, client)
        res = client.post(f"/ho-so/{ho_so}/dieu-khien/gianh", headers=HEADER_AUTH)
        assert res.status_code == 200, res.text
        assert res.json() == {"ok": True}
        assert mt_dk["quan_ly"].danh_sach_mo[ho_so].dang_ban is True
        # Giành lần hai → 409
        res2 = client.post(f"/ho-so/{ho_so}/dieu-khien/gianh", headers=HEADER_AUTH)
        assert res2.status_code == 409
        # Nhả hai lần vẫn ok
        assert client.post(f"/ho-so/{ho_so}/dieu-khien/nha", headers=HEADER_AUTH).status_code == 200
        assert client.post(f"/ho-so/{ho_so}/dieu-khien/nha", headers=HEADER_AUTH).status_code == 200
        assert mt_dk["quan_ly"].danh_sach_mo[ho_so].dang_ban is False
        # Giành lại được sau khi nhả
        assert client.post(f"/ho-so/{ho_so}/dieu-khien/gianh", headers=HEADER_AUTH).status_code == 200


def test_gianh_khi_co_nguoi_dung_novnc_thi_409(mt_dk):
    with TestClient(app) as client:
        ho_so = _mo_firefox(mt_dk, client)
        ho_so_mo = mt_dk["quan_ly"].danh_sach_mo[ho_so]
        mt_dk["bo_kiem_tra"].dat_ket_noi(ho_so_mo.cong, 1)
        res = client.post(f"/ho-so/{ho_so}/dieu-khien/gianh", headers=HEADER_AUTH)
        assert res.status_code == 409
        assert "người" in res.json()["detail"] or "noVNC" in res.json()["detail"]


def test_gianh_ho_so_chua_mo_thi_404(mt_dk):
    with TestClient(app) as client:
        res = client.post("/ho-so", headers=HEADER_AUTH, json={"ten": "x", "loai": "firefox"})
        ho_so = res.json()["ho_so"]
        res = client.post(f"/ho-so/{ho_so}/dieu-khien/gianh", headers=HEADER_AUTH)
        assert res.status_code == 404


def test_gianh_chromium_thi_501(mt_dk):
    with TestClient(app) as client:
        res = client.post("/ho-so", headers=HEADER_AUTH, json={"ten": "c", "loai": "chromium"})
        ho_so = res.json()["ho_so"]
        res = client.post("/ho-so/mo", headers=HEADER_AUTH,
                          json={"ho_so": ho_so, "loai": "chromium"})
        assert res.status_code == 200
        res = client.post(f"/ho-so/{ho_so}/dieu-khien/gianh", headers=HEADER_AUTH)
        assert res.status_code == 501


def test_mo_novnc_khi_may_dang_giu_thi_409(mt_dk):
    with TestClient(app) as client:
        ho_so = _mo_firefox(mt_dk, client)
        client.post(f"/ho-so/{ho_so}/dieu-khien/gianh", headers=HEADER_AUTH)
        res = client.post("/ho-so/mo", headers=HEADER_AUTH,
                          json={"ho_so": ho_so, "loai": "firefox"})
        assert res.status_code == 409


def test_dong_nha_quyen_va_dong_marionette(mt_dk):
    with TestClient(app) as client:
        ho_so = _mo_firefox(mt_dk, client)
        client.post(f"/ho-so/{ho_so}/dieu-khien/gianh", headers=HEADER_AUTH)
        gia = mt_dk["lay_gia"]()
        assert gia is not None and gia.da_noi
        client.post(f"/ho-so/{ho_so}/dieu-khien/nha", headers=HEADER_AUTH)
        assert gia.da_noi is False


# ── Nguyên thuỷ ────────────────────────────────────────────────────────────────

def _gianh(mt_dk, client: TestClient, ho_so: str):
    res = client.post(f"/ho-so/{ho_so}/dieu-khien/gianh", headers=HEADER_AUTH)
    assert res.status_code == 200, res.text


def test_nguyen_thuy_chua_gianh_thi_409(mt_dk):
    with TestClient(app) as client:
        ho_so = _mo_firefox(mt_dk, client)
        for duong, than in [
            ("/dieu-huong", {"url": "https://example.com/"}),
            ("/dien", {"bo_chon": "#a", "van_ban": "x"}),
            ("/bam", {"bo_chon": "#a"}),
            ("/cho", {"bo_chon": "#a", "giay": 1}),
            ("/tin-hieu", {"quy_tac": []}),
            ("/cookie-truc-tiep", {"mien": "example.com"}),
            ("/chup-trang-thai", {}),
        ]:
            res = client.post(f"/ho-so/{ho_so}{duong}", headers=HEADER_AUTH, json=than)
            assert res.status_code == 409, (duong, res.status_code, res.text)


def test_dieu_huong_va_url_sai_400(mt_dk):
    with TestClient(app) as client:
        ho_so = _mo_firefox(mt_dk, client)
        _gianh(mt_dk, client, ho_so)
        res = client.post(f"/ho-so/{ho_so}/dieu-huong", headers=HEADER_AUTH,
                          json={"url": "https://accounts.google.com/"})
        assert res.status_code == 200
        assert res.json() == {"ok": True}
        res = client.post(f"/ho-so/{ho_so}/dieu-huong", headers=HEADER_AUTH,
                          json={"url": "javascript:alert(1)"})
        assert res.status_code == 400
        res = client.post(f"/ho-so/{ho_so}/dieu-huong", headers=HEADER_AUTH,
                          json={"url": "http://169.254.169.254/"})
        assert res.status_code == 400


def test_dien_bam_khong_ghi_gia_tri_vao_log(mt_dk, caplog):
    with TestClient(app) as client:
        ho_so = _mo_firefox(mt_dk, client)
        _gianh(mt_dk, client, ho_so)
        client.post(f"/ho-so/{ho_so}/dieu-huong", headers=HEADER_AUTH,
                    json={"url": "https://accounts.google.com/"})
        bi_mat = "mat-khau-sieu-bi-mat-123"
        with caplog.at_level(logging.INFO, logger="dich_vu.trinh_duyet"):
            res = client.post(f"/ho-so/{ho_so}/dien", headers=HEADER_AUTH,
                              json={"bo_chon": "#identifierId", "van_ban": bi_mat})
        assert res.status_code == 200
        assert bi_mat not in caplog.text
        gia = mt_dk["lay_gia"]()
        assert ("dien", "#identifierId") in gia.hanh_dong
        assert not any(bi_mat in str(h) for h in gia.hanh_dong)
        res = client.post(f"/ho-so/{ho_so}/bam", headers=HEADER_AUTH,
                          json={"bo_chon": "#identifierNext"})
        assert res.status_code == 200
        assert ("bam", "#identifierNext") in gia.hanh_dong


def test_cho_phan_tu(mt_dk):
    with TestClient(app) as client:
        ho_so = _mo_firefox(mt_dk, client)
        _gianh(mt_dk, client, ho_so)
        client.post(f"/ho-so/{ho_so}/dieu-huong", headers=HEADER_AUTH,
                    json={"url": "https://accounts.google.com/"})
        res = client.post(f"/ho-so/{ho_so}/cho", headers=HEADER_AUTH,
                          json={"bo_chon": "#identifierId", "giay": 2})
        assert res.json() == {"thay": True}
        res = client.post(f"/ho-so/{ho_so}/cho", headers=HEADER_AUTH,
                          json={"bo_chon": "#khong-co", "giay": 1})
        assert res.json() == {"thay": False}


def test_tin_hieu_quy_tac(mt_dk):
    with TestClient(app) as client:
        ho_so = _mo_firefox(mt_dk, client)
        _gianh(mt_dk, client, ho_so)
        client.post(f"/ho-so/{ho_so}/dieu-huong", headers=HEADER_AUTH,
                    json={"url": "https://accounts.google.com/"})
        quy_tac = [
            {"loai": "url_chua", "tin_hieu": "trang_google", "gia_tri": "accounts.google"},
            {"loai": "tieu_de_chua", "tin_hieu": "tieu_de_dang_nhap", "gia_tri": "Tài khoản Google"},
            {"loai": "bo_chon", "tin_hieu": "co_o_email", "bo_chon": "#identifierId"},
            {"loai": "url_chua", "tin_hieu": "khong_khop", "gia_tri": "chatgpt.com"},
        ]
        res = client.post(f"/ho-so/{ho_so}/tin-hieu", headers=HEADER_AUTH,
                          json={"quy_tac": quy_tac})
        assert res.status_code == 200, res.text
        tin_hieu = set(res.json()["tin_hieu"])
        assert {"trang_google", "tieu_de_dang_nhap", "co_o_email"} <= tin_hieu
        assert "khong_khop" not in tin_hieu


def test_tin_hieu_quy_tac_sai_400(mt_dk):
    with TestClient(app) as client:
        ho_so = _mo_firefox(mt_dk, client)
        _gianh(mt_dk, client, ho_so)
        res = client.post(f"/ho-so/{ho_so}/tin-hieu", headers=HEADER_AUTH,
                          json={"quy_tac": [{"loai": "doan_mo", "tin_hieu": "x"}]})
        assert res.status_code == 400
        res = client.post(f"/ho-so/{ho_so}/tin-hieu", headers=HEADER_AUTH,
                          json={"quy_tac": [{"loai": "url_chua"}] * 11})
        assert res.status_code == 400


def test_cookie_truc_tiep_theo_mien(mt_dk):
    with TestClient(app) as client:
        ho_so = _mo_firefox(mt_dk, client)
        _gianh(mt_dk, client, ho_so)
        gia = mt_dk["lay_gia"]()
        gia.kich_ban["https://accounts.google.com/"]["cookie"] = {"SID": "bi-mat-sid"}
        client.post(f"/ho-so/{ho_so}/dieu-huong", headers=HEADER_AUTH,
                    json={"url": "https://accounts.google.com/"})
        res = client.post(f"/ho-so/{ho_so}/cookie-truc-tiep", headers=HEADER_AUTH,
                          json={"mien": "google.com"})
        assert res.status_code == 200, res.text
        assert "SID=bi-mat-sid" in res.json()["cookie"]
        res = client.post(f"/ho-so/{ho_so}/cookie-truc-tiep", headers=HEADER_AUTH,
                          json={"mien": "example.com"})
        assert res.json()["so_cookie"] == 0


def test_chup_trang_thai(mt_dk):
    with TestClient(app) as client:
        ho_so = _mo_firefox(mt_dk, client)
        _gianh(mt_dk, client, ho_so)
        gia = mt_dk["lay_gia"]()
        gia.kich_ban["https://accounts.google.com/"]["tin_hieu_script"] = ["2fa"]
        client.post(f"/ho-so/{ho_so}/dieu-huong", headers=HEADER_AUTH,
                    json={"url": "https://accounts.google.com/"})
        res = client.post(f"/ho-so/{ho_so}/chup-trang-thai", headers=HEADER_AUTH, json={})
        assert res.status_code == 200, res.text
        du_lieu = res.json()
        assert du_lieu["url"] == "https://accounts.google.com/"
        assert "Google" in du_lieu["tieu_de"]
        assert "2fa" in du_lieu["tin_hieu"]
        assert "anh" not in du_lieu  # không trả ảnh chụp (tránh lưu bí mật)


# ── Lỗi: 502 chỉ mã, lỗi lạ đóng cửa ───────────────────────────────────────────

def test_loi_marionette_thanh_502_chi_ma(mt_dk):
    with TestClient(app) as client:
        ho_so = _mo_firefox(mt_dk, client)
        _gianh(mt_dk, client, ho_so)
        gia = mt_dk["lay_gia"]()
        gia.loi_tiep_theo = "no such element"
        res = client.post(f"/ho-so/{ho_so}/bam", headers=HEADER_AUTH,
                          json={"bo_chon": "#a"})
        assert res.status_code == 502
        assert res.json()["detail"] == "no such element"
        # quyền điều khiển vẫn giữ (lỗi giao thức không đánh sập phiên)
        assert mt_dk["quan_ly"].danh_sach_mo[ho_so].dang_ban is True


def test_loi_la_dong_cua_nha_quyen(mt_dk):
    with TestClient(app) as client:
        ho_so = _mo_firefox(mt_dk, client)
        _gianh(mt_dk, client, ho_so)
        gia = mt_dk["lay_gia"]()
        gia.loi_la_tiep_theo = True
        res = client.post(f"/ho-so/{ho_so}/bam", headers=HEADER_AUTH,
                          json={"bo_chon": "#a"})
        assert res.status_code == 500
        # Đóng cửa: quyền đã nhả, nguyên thuỷ kế cần giành lại
        assert mt_dk["quan_ly"].danh_sach_mo[ho_so].dang_ban is False
        res = client.post(f"/ho-so/{ho_so}/bam", headers=HEADER_AUTH,
                          json={"bo_chon": "#a"})
        assert res.status_code == 409


def test_gianh_marionette_hong_thi_502(mt_dk):
    with TestClient(app) as client:
        ho_so = _mo_firefox(mt_dk, client)

        class Hong:
            def ket_noi(self):
                raise LoiMarionette("ket_noi_that_bai")

        mt_dk["monkeypatch"].setattr(server, "_tao_marionette", lambda cong: Hong())
        res = client.post(f"/ho-so/{ho_so}/dieu-khien/gianh", headers=HEADER_AUTH)
        assert res.status_code == 502
        assert mt_dk["quan_ly"].danh_sach_mo[ho_so].dang_ban is False


def test_xac_thuc_duong_dieu_khien(mt_dk):
    with TestClient(app) as client:
        res = client.post("/ho-so/0123456789abcdef/dieu-khien/gianh")
        assert res.status_code == 401
