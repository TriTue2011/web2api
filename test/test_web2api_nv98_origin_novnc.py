"""NV98 — test bảo mật cho vé noVNC: kiểm Origin và thu hồi phiên/vé theo tài khoản.

Đo trên môi trường chạy thực:
- Kiểm Origin ở GET và POST /novnc/dang-nhap: so khớp chuẩn hoá authority, scheme và port.
- Tôn trọng cờ WEB2API_TIN_PROXY đối với X-Forwarded-*.
- Referrer-Policy ở trang vé là same-origin; ở đường khác dưới /novnc/ là no-referrer.
- Không để lộ vé hoặc Origin thô trong nhật ký và phản hồi 403.
- Thu hồi vé và phiên noVNC cấp bằng Bearer (nguoi_dung_id rỗng) khi thu hồi bất kỳ tài khoản nào;
  vé của tài khoản khác không bị thu hồi lan sai.
- Đổi mật khẩu qua HTTP đầu-cuối (POST /api/toi/mat-khau) thu hồi vé noVNC của chính tài khoản.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re

import pytest
from fastapi.testclient import TestClient

from app import dia_chi, gioi_han, nguoi_dung, nhat_ky, novnc, phien_web
from app.main import app

KHOA_QT_GIA = "khoa-quan-tri-gia-nv98"
MK_TOT = "MatKhauTot123456"
MK_MOI = "MatKhauMoi654321"
HO_SO_GIA = "abcdef1234567890"
MODULE_GIA = "mod-gia-nv98"
ORIGIN_DUNG = "http://testserver"
ORIGIN_LA = "http://ke-la.example"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT_GIA)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "1000")
    monkeypatch.setenv("WEB2API_RL_CUA_SO_GIAY", "60")
    monkeypatch.setenv("WEB2API_RL_KHOA_GIAY", "60")
    monkeypatch.setenv("WEB2API_DANG_NHAP_SAI_TOI_DA", "100")
    monkeypatch.delenv("WEB2API_TIN_PROXY", raising=False)
    nguoi_dung._THEO_DOI_SAI.clear()
    gioi_han.khoi_dong()
    novnc._VE.clear()
    novnc._PHIEN.clear()
    novnc._HO_SO.clear()
    yield data_dir
    novnc._VE.clear()
    novnc._PHIEN.clear()
    novnc._HO_SO.clear()


def _tao_tai_khoan(vai: str, ten: str, mk: str = MK_TOT) -> dict:
    ban_ghi, _ten_chuan, _mk = asyncio.run(
        nguoi_dung.tao_nguoi_dung(vai=vai, ten_dang_nhap=ten, mat_khau=mk)
    )
    return ban_ghi


def _ve_con(ve: str) -> bool:
    return novnc._bam(ve) in novnc._VE


def _chen_ho_so_gia() -> None:
    novnc._HO_SO[(MODULE_GIA, HO_SO_GIA)] = {
        "scheme": "http",
        "host": "127.0.0.1",
        "port": 6080,
        "duong": "",
        "ip": "127.0.0.1",
        "host_hdr": "127.0.0.1:6080",
        "auth": None,
        "mod": None,
        "loai": "chromium",
    }


def _qt() -> dict:
    return {"Authorization": f"Bearer {KHOA_QT_GIA}"}


# ───────────────────────────── 1. Kiểm Origin khi POST đổi vé ─────────────────────────────

def test_post_doi_ve_origin_dung(moi_truong):
    """POST đổi vé với Origin đúng → 303."""
    _chen_ho_so_gia()
    ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id="uid-1")
    with TestClient(app, follow_redirects=False) as c:
        r = c.post(
            "/novnc/dang-nhap",
            data={"ve": ve},
            headers={"Origin": ORIGIN_DUNG, "Content-Type": "application/x-www-form-urlencoded"},
        )
    assert r.status_code == 303
    assert not _ve_con(ve)


@pytest.mark.parametrize(
    ("host", "origin"),
    [
        ("testserver:80", "http://testserver"),    # Host ghi cổng mặc định, Origin của trình duyệt thì bỏ
        ("testserver", "http://testserver:80"),    # ngược lại
    ],
)
def test_post_doi_ve_chuan_hoa_cong_mac_dinh_hai_ve(moi_truong, host, origin):
    """Cổng mặc định (:80 với http) được chuẩn hoá ở CẢ HAI vế trước khi so bằng ==."""
    _chen_ho_so_gia()
    ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id="uid-1")
    with TestClient(app, follow_redirects=False) as c:
        r = c.post(
            "/novnc/dang-nhap",
            data={"ve": ve},
            headers={"Host": host, "Origin": origin, "Content-Type": "application/x-www-form-urlencoded"},
        )
    assert r.status_code == 303, f"Host {host} + Origin {origin}: {r.status_code}"
    assert not _ve_con(ve)


@pytest.mark.parametrize(
    "origin_sai",
    [
        "https://testserver",                # khác scheme
        "http://testserver:8080",            # khác port
        "http://testserver.ke-la.example",   # kẻ tấn công nối đuôi tên miền
        "http://ke-la.example",              # domain lạ hoàn toàn
        "null",                              # chuỗi null
    ],
)
def test_post_doi_ve_origin_sai_bi_tu_choi_va_ve_con_nguyen(moi_truong, origin_sai):
    """POST đổi vé với Origin sai khác nhau → 403, vé không bị tiêu thụ."""
    _chen_ho_so_gia()
    ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id="uid-1")
    with TestClient(app, follow_redirects=False) as c:
        r = c.post(
            "/novnc/dang-nhap",
            data={"ve": ve},
            headers={"Origin": origin_sai, "Content-Type": "application/x-www-form-urlencoded"},
        )
    assert r.status_code == 403, f"Origin {origin_sai} phải nhận 403, nhận {r.status_code}"
    assert _ve_con(ve), f"Vé phải còn nguyên sau 403 với origin {origin_sai}"


def test_post_doi_ve_thieu_origin_khi_co_ve_bi_tu_choi(moi_truong):
    """POST đổi vé có gửi vé mà thiếu header Origin → 403, vé còn nguyên."""
    _chen_ho_so_gia()
    ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id="uid-1")
    with TestClient(app, follow_redirects=False) as c:
        r = c.post(
            "/novnc/dang-nhap",
            content=f"ve={ve}".encode("ascii"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if r.request.headers.get("origin"):
        pytest.skip("TestClient tự thêm Origin nên không đo được ca thiếu Origin thuần tuý")
    assert r.status_code == 403
    assert _ve_con(ve)


def test_post_doi_ve_thieu_origin_va_khong_ve_la_401(moi_truong):
    """POST đổi vé không gửi vé và không có Origin → 401 như cũ."""
    with TestClient(app, follow_redirects=False) as c:
        r = c.post(
            "/novnc/dang-nhap",
            content=b"",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    assert r.status_code == 401


# ───────────────────────────── 2. X-Forwarded-* và WEB2API_TIN_PROXY ─────────────────────────────

def test_gia_mao_forwarded_khi_chua_tin_proxy_bi_tu_choi(moi_truong, monkeypatch):
    """Giả mạo X-Forwarded-Host/Proto khi CHƯA bật WEB2API_TIN_PROXY → 403."""
    monkeypatch.delenv("WEB2API_TIN_PROXY", raising=False)
    _chen_ho_so_gia()
    ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id="uid-1")
    headers = {
        "X-Forwarded-Host": "ke-la.example",
        "X-Forwarded-Proto": "https",
        "Origin": "https://ke-la.example",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    with TestClient(app, follow_redirects=False) as c:
        r = c.post("/novnc/dang-nhap", data={"ve": ve}, headers=headers)
    assert r.status_code == 403
    assert _ve_con(ve)


def test_forwarded_hop_le_khi_tin_proxy_duoc_chap_nhan(moi_truong, monkeypatch):
    """Bộ header X-Forwarded-Host/Proto khi WEB2API_TIN_PROXY=1 → được chấp nhận (303)."""
    monkeypatch.setenv("WEB2API_TIN_PROXY", "1")
    _chen_ho_so_gia()
    ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id="uid-1")
    headers = {
        "X-Forwarded-Host": "ke-la.example",
        "X-Forwarded-Proto": "https",
        "Origin": "https://ke-la.example",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    with TestClient(app, follow_redirects=False) as c:
        r = c.post("/novnc/dang-nhap", data={"ve": ve}, headers=headers)
    assert r.status_code == 303
    assert not _ve_con(ve)


# ───────────────────────────── 3. GET trang vé ─────────────────────────────

def test_get_trang_ve_origin_la_bi_tu_choi(moi_truong):
    """GET trang vé với Origin lạ → 403."""
    ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id="uid-1")
    with TestClient(app) as c:
        r = c.get(f"/novnc/dang-nhap?ve={ve}", headers={"Origin": ORIGIN_LA})
    assert r.status_code == 403


def test_get_trang_ve_thieu_origin_cho_qua(moi_truong):
    """GET trang vé không có Origin (trình duyệt gõ link trực tiếp) → 200."""
    ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id="uid-1")
    with TestClient(app) as c:
        r = c.get(f"/novnc/dang-nhap?ve={ve}")
    assert r.status_code == 200


def test_get_trang_ve_origin_dung_cho_qua(moi_truong):
    """GET trang vé với Origin đúng → 200."""
    ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id="uid-1")
    with TestClient(app) as c:
        r = c.get(f"/novnc/dang-nhap?ve={ve}", headers={"Origin": ORIGIN_DUNG})
    assert r.status_code == 200


# ───────────────────────────── 4. Referrer-Policy header ─────────────────────────────

def test_referrer_policy_trang_ve_va_duong_khac(moi_truong):
    """Trang vé mang Referrer-Policy: same-origin; đường khác dưới /novnc/ mang no-referrer."""
    ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id="uid-1")
    with TestClient(app) as c:
        r_ve = c.get(f"/novnc/dang-nhap?ve={ve}")
        assert r_ve.headers.get("referrer-policy") == "same-origin"

        # Đường khác dưới /novnc/
        r_khac = c.get("/novnc/s/phien-khong-co/vnc.html")
        assert r_khac.headers.get("referrer-policy") == "no-referrer"


# ───────────────────────────── 5. Không lộ vé / Origin trong nhật ký và phản hồi ─────────────────────────────

def test_khong_lo_ve_va_origin_trong_nhat_ky_va_phan_hoi(moi_truong):
    """Phản hồi 403 và nhật ký không chứa vé thô hay chuỗi Origin thô."""
    _chen_ho_so_gia()
    ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id="uid-1")
    origin_doc = "http://attacker-evil-site.example"
    with TestClient(app, follow_redirects=False) as c:
        r = c.post(
            "/novnc/dang-nhap",
            data={"ve": ve},
            headers={"Origin": origin_doc, "Content-Type": "application/x-www-form-urlencoded"},
        )
    assert r.status_code == 403
    assert ve not in r.text
    assert origin_doc not in r.text

    dong_nhat_ky = nhat_ky.doc(kenh="trinh_duyet", gioi_han=50)
    for dong in dong_nhat_ky:
        chu = json.dumps(dong, ensure_ascii=False)
        assert ve not in chu, f"Vé lọt vào nhật ký: {chu}"
        assert origin_doc not in chu, f"Origin thô lọt vào nhật ký: {chu}"


# ───────────────────────────── 6. Thu hồi vé & phiên noVNC ─────────────────────────────

def test_thu_hoi_ve_bearer_khi_thu_hoi_tai_khoan_bat_ky(moi_truong):
    """Vé cấp bằng Bearer (nguoi_dung_id=None) bị thu hồi khi thu hồi tài khoản bất kỳ;
    vé gắn tài khoản khác không bị ảnh hưởng."""
    ve_bearer = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id=None)
    ve_khac = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id="user-khac")
    ve_chinh = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id="user-can-thu-hoi")

    assert _ve_con(ve_bearer)
    assert _ve_con(ve_khac)
    assert _ve_con(ve_chinh)

    # Thu hồi user-can-thu-hoi
    novnc.thu_hoi_phien_theo_nguoi_dung("user-can-thu-hoi")

    # Vé chính bị thu hồi
    assert not _ve_con(ve_chinh), "Vé của tài khoản bị thu hồi phải mất"
    # Vé Bearer (không gắn tài khoản cụ thể) bị thu hồi theo nguyên tắc thiên về an toàn
    assert not _ve_con(ve_bearer), "Vé Bearer (id None) phải bị thu hồi"
    # Vé của user-khac vẫn còn nguyên (không thu hồi lan sai)
    assert _ve_con(ve_khac), "Vé của tài khoản khác phải còn nguyên"


def test_thu_hoi_phien_novnc_da_doi(moi_truong):
    """Phiên noVNC đã đổi (trong _PHIEN) cũng bị thu hồi khi thu hồi theo tài khoản."""
    _chen_ho_so_gia()
    ve_bearer = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id=None)
    ve_khac = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id="user-khac")
    ve_chinh = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id="user-can-thu-hoi")

    m_b, p_b, _ = novnc.doi_ve_lay_phien(ve_bearer)
    m_k, p_k, _ = novnc.doi_ve_lay_phien(ve_khac)
    m_c, p_c, _ = novnc.doi_ve_lay_phien(ve_chinh)

    assert m_b == 200 and m_k == 200 and m_c == 200
    assert novnc._bam(p_b) in novnc._PHIEN
    assert novnc._bam(p_k) in novnc._PHIEN
    assert novnc._bam(p_c) in novnc._PHIEN

    # Thu hồi
    so_luong = novnc.thu_hoi_phien_theo_nguoi_dung("user-can-thu-hoi")
    assert so_luong == 2  # phiên chính + phiên Bearer

    assert novnc._bam(p_c) not in novnc._PHIEN
    assert novnc._bam(p_b) not in novnc._PHIEN
    assert novnc._bam(p_k) in novnc._PHIEN, "Phiên của tài khoản khác không bị thu hồi lan"


def test_thu_hoi_phien_nguoi_dung_tru_bam_giu_phien_web_nhung_thu_hoi_novnc(moi_truong):
    """thu_hoi_phien_nguoi_dung(uid, tru_bam=...) giữ lại phiên web tru_bam nhưng thu hồi noVNC."""
    nd = _tao_tai_khoan("user", "test-tru-bam")
    uid = nd["id"]

    # Tạo 2 phiên web: 1 phiên giữ, 1 phiên cũ
    p_giu, tok_giu = phien_web.tao_phien(uid)
    p_cu, tok_cu = phien_web.tao_phien(uid)

    # Cấp vé noVNC cho người dùng này
    ve_novnc = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id=uid)
    assert _ve_con(ve_novnc)

    # Thu hồi giữ lại p_giu
    phien_web.thu_hoi_phien_nguoi_dung(uid, tru_bam=p_giu["bam"])

    # Phiên web p_giu còn
    assert phien_web.xac_thuc_token(tok_giu) is not None
    # Phiên web p_cu mất
    assert phien_web.xac_thuc_token(tok_cu) is None
    # Vé noVNC bị thu hồi
    assert not _ve_con(ve_novnc)


def test_doi_mat_khau_toi_thu_hoi_ve_novnc_dau_cuoi(moi_truong):
    """POST /api/toi/mat-khau thật sự thu hồi vé noVNC của tài khoản qua HTTP đầu-cuối."""
    nd = _tao_tai_khoan("admin", "nv98-doi-mk-dau-cuoi", mk=MK_TOT)
    uid = nd["id"]

    # Đăng nhập lấy cookie phiên
    with TestClient(app) as client:
        r_dn = client.post("/api/dang-nhap", json={"ten_dang_nhap": "nv98-doi-mk-dau-cuoi", "mat_khau": MK_TOT})
        assert r_dn.status_code == 200, r_dn.text
        csrf = r_dn.json()["csrf"]

        # Cấp vé noVNC cho tài khoản này
        ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id=uid)
        assert _ve_con(ve)

        # Đổi mật khẩu qua POST /api/toi/mat-khau
        r_dmk = client.post(
            "/api/toi/mat-khau",
            json={"mat_khau_cu": MK_TOT, "mat_khau_moi": MK_MOI},
            headers={"X-CSRF-Token": csrf},
        )
        assert r_dmk.status_code == 200, r_dmk.text

        # Vé noVNC của chính tài khoản phải bị thu hồi
        assert not _ve_con(ve), "Vé noVNC phải bị thu hồi sau khi tự đổi mật khẩu qua /api/toi/mat-khau"
