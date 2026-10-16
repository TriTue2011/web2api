"""Bộ kiểm thử cho cơ chế khoá đăng nhập hai tầng (IP, tên) và giới hạn noVNC (NV23 của Muse).

Kiểm thử:
1. Tầng 1 theo cặp (IP, tên): 5 lần sai từ IP A khoá IP A; từ IP B đăng nhập mật khẩu đúng vẫn thành công (200).
2. Tầng 2 theo tên toàn cục: 50 lần sai phân tán từ nhiều IP khoá tên tài khoản đối với mọi IP.
3. Chống giả mạo IP: XFF giả từ peer không tin cậy không đổi được IP và không tránh được khoá cặp.
4. Trần bộ nhớ và khoá an toàn: trần 10000 mục, khoá cặp không phình khi IP/tên dài, tên sai mẫu không vào bộ đếm.
5. noVNC đổi vé: 31 vé sai cùng IP trả về 429; vé đúng trước đó đổi được (303); trần kho vé TOI_DA_VE = 10000.
6. Đăng nhập đúng chỉ trừ phần sai của chính cặp đó ở tầng 2, không xoá trắng bộ đếm toàn cục.
7. Đổi mật khẩu chính mình (POST /api/toi/mat-khau): 5 lần sai mật khẩu cũ từ IP A khoá cặp (IP A, tên); IP B thử đúng nhận 200, không bị lây.

Đột biến chứng minh (đều đỏ khi bỏ dòng bảo vệ tương ứng):
- DOT_BIEN_KHOA_THEO_TEN_TOAN_CUC_SOM: khoá tên toàn cục ở ngưỡng 5 như mã cũ -> IP B bị 401 thay vì 200.
- DOT_BIEN_BO_KHOA_TANG_2: bỏ kiểm tra tầng 2 -> 50 sai phân tán vẫn cho IP mới đăng nhập 200 thay vì 401.
- DOT_BIEN_TIN_XFF_MU_QUANG: tin XFF từ peer không tin cậy -> đổi XFF tránh được khoá cặp (200 thay vì 401).
- DOT_BIEN_BO_TRAN_BO_NHO: bỏ trần 10000 -> bộ đếm phình quá 10000 mục.
- DOT_BIEN_NOVNC_KHONG_DEM_LOI: bỏ ghi_loi_xac_thuc trong novnc_doi_ve -> 31 vé sai nhận 401 thay vì 429.
- DOT_BIEN_XOA_TRANG_TANG_2: đăng nhập đúng xoá sạch tầng 2 -> bộ đếm tầng 2 về 0 thay vì 3.
- DOT_BIEN_DOI_MAT_KHAU_KHONG_TRUYEN_IP: bỏ truyền IP khi đổi mật khẩu -> sau 5 sai, IP A thử đúng nhận 200 thay vì 401.
"""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import gioi_han, nguoi_dung, novnc, phien_web
from app.main import app
from app.so_module import SoModule

KHOA_QT = "AIzaSyKIEMNGHIEM-000000000000000000009"
MK_CHUAN = "MatKhauAdminTot123456"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "30")
    monkeypatch.setenv("WEB2API_RL_CUA_SO_GIAY", "60")
    monkeypatch.setenv("WEB2API_RL_KHOA_GIAY", "60")
    monkeypatch.setenv("WEB2API_DANG_NHAP_SAI_TOI_DA", "5")
    monkeypatch.setenv("WEB2API_DANG_NHAP_TOAN_CUC_SAI_TOI_DA", "50")
    monkeypatch.setenv("WEB2API_DANG_NHAP_KHOA_PHUT", "15")
    monkeypatch.setenv("WEB2API_TRUST_PROXY", "testclient,127.0.0.1,::1,203.0.113.0/24")

    nguoi_dung._THEO_DOI_SAI.clear()
    novnc._VE.clear()
    gioi_han.khoi_dong()

    with TestClient(app) as client:
        client._transport.client = ("127.0.0.1", 1234)
        client.app.state.so = SoModule()
        yield client, data_dir


def test_5_sai_ip_a_khoa_cap_ip_b_dang_nhap_thanh_cong(moi_truong):
    """5 sai từ IP A cho admin -> IP A bị khoá cặp (401); từ IP B đăng nhập đúng -> 200 (test chính, trước sửa ĐỎ).

    Đột biến chứng minh: DOT_BIEN_KHOA_THEO_TEN_TOAN_CUC_SOM (khoá tên toàn cục ở 5 lần sai -> IP B nhận 401).
    """
    client, _ = moi_truong
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin", mat_khau=MK_CHUAN))

    ip_a = "198.51.100.1"
    ip_b = "198.51.100.2"

    # IP A gửi 5 lần mật khẩu sai
    for _ in range(5):
        r = client.post(
            "/api/dang-nhap",
            json={"ten_dang_nhap": "admin", "mat_khau": "SaiMatKhau123"},
            headers={"x-forwarded-for": ip_a},
        )
        assert r.status_code == 401

    # Lần 6: IP A gửi mật khẩu ĐÚNG -> vẫn bị 401 vì cặp (IP A, admin) đang bị khoá
    r_a_dung = client.post(
        "/api/dang-nhap",
        json={"ten_dang_nhap": "admin", "mat_khau": MK_CHUAN},
        headers={"x-forwarded-for": ip_a},
    )
    assert r_a_dung.status_code == 401
    assert "Sai tên đăng nhập hoặc mật khẩu" in r_a_dung.text

    # IP B gửi mật khẩu ĐÚNG -> THÀNH CÔNG (200), không bị ảnh hưởng bởi IP A!
    r_b_dung = client.post(
        "/api/dang-nhap",
        json={"ten_dang_nhap": "admin", "mat_khau": MK_CHUAN},
        headers={"x-forwarded-for": ip_b},
    )
    assert r_b_dung.status_code == 200
    assert r_b_dung.json()["ok"] is True
    assert r_b_dung.json()["nguoi_dung"]["ten_dang_nhap"] == "admin"


def test_50_sai_phan_tan_khoa_tang_2_toan_cuc(moi_truong):
    """50 sai phân tán từ nhiều IP khác nhau -> tầng 2 khoá tên toàn cục, mật khẩu đúng từ IP mới cũng bị từ chối (401).

    Đột biến chứng minh: DOT_BIEN_BO_KHOA_TANG_2 (bỏ kiểm tra tầng 2 -> IP mới nhận 200 thay vì 401).
    """
    client, _ = moi_truong
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-pt", mat_khau=MK_CHUAN))

    # 50 IP khác nhau, mỗi IP chỉ sai 1 lần (tầng 1 của từng IP chưa chạm ngưỡng 5)
    for i in range(1, 51):
        ip = f"198.51.100.{i}"
        r = client.post(
            "/api/dang-nhap",
            json={"ten_dang_nhap": "admin-pt", "mat_khau": "SaiMatKhau123"},
            headers={"x-forwarded-for": ip},
        )
        assert r.status_code == 401

    # Lúc này tầng 2 đã đạt 50 lần sai -> tên admin-pt bị khoá toàn cục
    # Một IP hoàn toàn mới (chưa từng gửi sai) gửi mật khẩu ĐÚNG -> VẪN BỊ 401
    ip_moi = "198.51.100.99"
    r_moi_dung = client.post(
        "/api/dang-nhap",
        json={"ten_dang_nhap": "admin-pt", "mat_khau": MK_CHUAN},
        headers={"x-forwarded-for": ip_moi},
    )
    assert r_moi_dung.status_code == 401
    assert "Sai tên đăng nhập hoặc mật khẩu" in r_moi_dung.text


def test_xff_gia_tu_peer_khong_tin_cay_khong_doi_duoc_ip(moi_truong, monkeypatch):
    """XFF giả từ peer không tin cậy KHÔNG đổi được IP (không tránh được khoá cặp).

    Đột biến chứng minh: DOT_BIEN_TIN_XFF_MU_QUANG (tin XFF mù quáng -> đổi XFF nhận 200).
    """
    client, _ = moi_truong
    # Xoá proxy tin cậy để peer ngoài không thể giả mạo XFF
    monkeypatch.setenv("WEB2API_TRUST_PROXY", "")
    gioi_han.khoi_dong()
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-xff", mat_khau=MK_CHUAN))

    # Giả lập peer trực tiếp từ internet 198.51.100.77 (không phải loopback/proxy)
    peer_ip = "198.51.100.77"

    # Gọi trực tiếp lay_ip_an_toan với các XFF giả
    for i in range(5):
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/dang-nhap",
            "headers": [
                (b"content-type", b"application/json"),
                (b"x-forwarded-for", f"10.0.0.{i}".encode("ascii")),
            ],
            "client": (peer_ip, 54321),
        }
        req = Request(scope)
        ip_nhan = gioi_han.lay_ip_an_toan(req)
        assert ip_nhan == peer_ip, "Peer không tin cậy phải bị bỏ XFF, giữ nguyên peer IP"
        nguoi_dung.ghi_nhan_dang_nhap_sai("admin-xff", ip=ip_nhan)

    # Sau 5 lần, cặp (peer_ip, admin-xff) bị khoá
    # Kẻ tấn công đổi XFF sang 10.0.0.99 và thử mật khẩu đúng
    scope_dung = {
        "type": "http",
        "method": "POST",
        "path": "/api/dang-nhap",
        "headers": [
            (b"content-type", b"application/json"),
            (b"x-forwarded-for", b"10.0.0.99"),
        ],
        "client": (peer_ip, 54321),
    }
    req_dung = Request(scope_dung)
    ip_nhan_dung = gioi_han.lay_ip_an_toan(req_dung)
    assert ip_nhan_dung == peer_ip

    with pytest.raises(Exception) as exc:
        nguoi_dung.kiem_tra_khoa_ten("admin-xff", ip=ip_nhan_dung)
    assert exc.value.ma == 401, "Cặp vẫn bị khoá bất chấp header XFF giả"


def test_bo_dem_tran_10000_va_khoa_ngan_khong_phinh(moi_truong):
    """Bộ đếm có trần 10000 mục; khoá cặp không phình khi gửi tên/IP rất dài; tên sai mẫu không vào bộ đếm.

    Đột biến chứng minh: DOT_BIEN_BO_TRAN_BO_NHO (bỏ trần 10000 -> len(_THEO_DOI_SAI) vượt 10000).
    """
    client, _ = moi_truong
    truoc = len(nguoi_dung._THEO_DOI_SAI)

    # 1. Tên sai mẫu hoặc quá dài (>64) không vào bộ đếm
    nguoi_dung.ghi_nhan_dang_nhap_sai("a" * 100, ip="198.51.100.1")
    nguoi_dung.ghi_nhan_dang_nhap_sai("Tên Có Dấu", ip="198.51.100.1")
    assert len(nguoi_dung._THEO_DOI_SAI) == truoc

    # 2. IP rất dài bị cắt trần 45 ký tự, khoá cặp <= 80 ký tự
    ip_sieu_dai = "2001:0db8:85a3:0000:0000:8a2e:0370:7334" + "x" * 100
    nguoi_dung.ghi_nhan_dang_nhap_sai("user-test", ip=ip_sieu_dai)
    # Tìm khoá cặp vừa thêm
    khoa_vua_them = [k for k in nguoi_dung._THEO_DOI_SAI if ":" in k and k.endswith(":user-test")]
    assert len(khoa_vua_them) == 1
    assert len(khoa_vua_them[0]) <= 80, f"Khoá cặp phải <= 80 ký tự, nhận {len(khoa_vua_them[0])}"

    # 3. Trần bộ nhớ 10000 mục
    nguoi_dung._THEO_DOI_SAI.clear()
    for i in range(10010):
        nguoi_dung.ghi_nhan_dang_nhap_sai("user-tran", ip=f"10.{i // 256}.{i % 256}.1")
    assert len(nguoi_dung._THEO_DOI_SAI) <= nguoi_dung.TOI_DA_THEO_DOI


def test_novnc_gioi_han_ve_sai_va_tran_kho_ve(moi_truong, monkeypatch):
    """noVNC: 31 vé sai cùng IP -> 429; vé đúng trước đó vẫn đổi được; _VE không vượt TOI_DA_VE.

    Đột biến chứng minh: DOT_BIEN_NOVNC_KHONG_DEM_LOI (bỏ ghi_loi_xac_thuc trong novnc_doi_ve -> 31 vé sai nhận 401).
    """
    client, _ = moi_truong
    ip_test = "198.51.100.55"

    # Giả lập 30 lần đổi vé sai từ cùng IP -> nhận 401
    for i in range(30):
        r = client.post(
            "/novnc/dang-nhap",
            data=f"ve=ve-sai-khong-hop-le-chua-tung-co-{i:04d}",
            headers={
                "origin": "http://testserver",
                "content-type": "application/x-www-form-urlencoded",
                "x-forwarded-for": ip_test,
            },
        )
        assert r.status_code == 401, f"Lần {i + 1} phải là 401"

    # Lần thứ 31: vượt ngưỡng 30 lỗi -> IP bị khoá tạm thời, nhận 429 kèm Retry-After
    r_31 = client.post(
        "/novnc/dang-nhap",
        data="ve=ve-sai-thu-31-kich-hoat-chan-ip-ngay",
        headers={
            "origin": "http://testserver",
            "content-type": "application/x-www-form-urlencoded",
            "x-forwarded-for": ip_test,
        },
    )
    assert r_31.status_code == 429
    assert "Retry-After" in r_31.headers
    assert r_31.json()["error"]["code"] == "rate_limit_exceeded"

    # IP khác chưa chạm ngưỡng vẫn đổi được vé đúng (chưa bị khoá)
    ip_khac = "198.51.100.66"
    # Giả lập ghim hồ sơ và cấp vé đúng
    with novnc._KHOA:
        novnc._HO_SO[("test-mod", "0123456789abcdef")] = {
            "scheme": "http",
            "host": "127.0.0.1",
            "port": 6080,
            "duong": "",
            "ip": "127.0.0.1",
            "host_hdr": "127.0.0.1:6080",
            "auth": None,
            "mod": None,
            "loai": "chrome",
        }
    ve_dung = novnc.cap_ve("test-mod", "0123456789abcdef")
    r_dung = client.post(
        "/novnc/dang-nhap",
        data=f"ve={ve_dung}",
        headers={
            "origin": "http://testserver",
            "content-type": "application/x-www-form-urlencoded",
            "x-forwarded-for": ip_khac,
        },
        follow_redirects=False,
    )
    assert r_dung.status_code == 303, "Vé đúng từ IP chưa khoá phải được đổi thành công (303)"

    # Kiểm tra trần kho vé TOI_DA_VE = 10000
    novnc._VE.clear()
    for _ in range(10010):
        novnc.cap_ve("test-mod", "0123456789abcdef")
    assert len(novnc._VE) <= novnc.TOI_DA_VE


def test_dang_nhap_dung_chi_tru_phan_cua_cap_o_tang_2(moi_truong):
    """Đăng nhập thành công chỉ trừ số lần sai của chính cặp đó ở tầng 2, không xoá trắng bộ đếm tầng 2.

    Đột biến chứng minh: DOT_BIEN_XOA_TRANG_TANG_2 (đăng nhập đúng xoá trắng tầng 2 -> so_sai về 0 thay vì 3).
    """
    client, _ = moi_truong
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-tru", mat_khau=MK_CHUAN))

    ip_a = "198.51.100.10"
    ip_b = "198.51.100.20"

    # IP A sai 2 lần
    for _ in range(2):
        client.post(
            "/api/dang-nhap",
            json={"ten_dang_nhap": "admin-tru", "mat_khau": "Sai1"},
            headers={"x-forwarded-for": ip_a},
        )
    # IP B sai 3 lần
    for _ in range(3):
        client.post(
            "/api/dang-nhap",
            json={"ten_dang_nhap": "admin-tru", "mat_khau": "Sai2"},
            headers={"x-forwarded-for": ip_b},
        )

    # Tầng 2 ghi nhận tổng cộng 5 lần sai cho admin-tru
    assert nguoi_dung._THEO_DOI_SAI["admin-tru"]["so_sai"] == 5

    # IP A đăng nhập THÀNH CÔNG với mật khẩu đúng
    r_ok = client.post(
        "/api/dang-nhap",
        json={"ten_dang_nhap": "admin-tru", "mat_khau": MK_CHUAN},
        headers={"x-forwarded-for": ip_a},
    )
    assert r_ok.status_code == 200

    # Cặp IP A đã bị xoá
    khoa_cap_a = f"{ip_a}:admin-tru"
    assert khoa_cap_a not in nguoi_dung._THEO_DOI_SAI

    # Tầng 2 CHỈ trừ 2 lần của IP A -> còn 3 lần của IP B!
    assert "admin-tru" in nguoi_dung._THEO_DOI_SAI
    assert nguoi_dung._THEO_DOI_SAI["admin-tru"]["so_sai"] == 3


def test_doi_mat_khau_5_sai_khoa_cap_ip_khac_khong_lay(moi_truong):
    """5 lần sai mật khẩu cũ từ cùng IP ở POST /api/toi/mat-khau -> cặp bị khoá; IP khác không bị lây.

    Đột biến chứng minh: DOT_BIEN_DOI_MAT_KHAU_KHONG_TRUYEN_IP (bỏ truyền IP khi đổi mật khẩu -> sau 5 sai, IP A thử đúng nhận 200 thay vì 401).
    """
    client, _ = moi_truong
    u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-doi-mk", mat_khau=MK_CHUAN))
    p, tok = phien_web.tao_phien(u["id"])

    ip_a = "198.51.100.11"
    ip_b = "198.51.100.22"

    # Giả lập kẻ giữ phiên bị đánh cắp ở IP A: thử sai mật khẩu cũ 5 lần
    for _ in range(5):
        r = client.post(
            "/api/toi/mat-khau",
            json={"mat_khau_cu": "SaiMatKhau123", "mat_khau_moi": "MatKhauMoi123456"},
            cookies={"w2a_phien": tok},
            headers={"X-CSRF-Token": p["csrf"], "x-forwarded-for": ip_a},
        )
        assert r.status_code == 401
        assert "Mật khẩu cũ không chính xác" in r.text

    # Cặp (ip_a, "user-doi-mk") bị khoá ở tầng 1 (ngưỡng 5)
    khoa_cap_a = f"{ip_a}:user-doi-mk"
    assert khoa_cap_a in nguoi_dung._THEO_DOI_SAI
    assert nguoi_dung._THEO_DOI_SAI[khoa_cap_a]["khoa_den"] is not None

    # Lần thứ 6: IP A thử mật khẩu cũ ĐÚNG -> VẪN BỊ 401 vì cặp (IP A, tên) đang bị khoá
    r_a_dung = client.post(
        "/api/toi/mat-khau",
        json={"mat_khau_cu": MK_CHUAN, "mat_khau_moi": "MatKhauMoi123456"},
        cookies={"w2a_phien": tok},
        headers={"X-CSRF-Token": p["csrf"], "x-forwarded-for": ip_a},
    )
    assert r_a_dung.status_code == 401
    assert "Mật khẩu cũ không chính xác" in r_a_dung.text

    # IP B (người dùng hợp lệ từ IP khác, dùng phiên riêng của mình) đổi mật khẩu ĐÚNG -> THÀNH CÔNG (200), không bị lây từ IP A
    p_b, tok_b = phien_web.tao_phien(u["id"])
    r_b_dung = client.post(
        "/api/toi/mat-khau",
        json={"mat_khau_cu": MK_CHUAN, "mat_khau_moi": "MatKhauMoi123456"},
        cookies={"w2a_phien": tok_b},
        headers={"X-CSRF-Token": p_b["csrf"], "x-forwarded-for": ip_b},
    )
    assert r_b_dung.status_code == 200
    assert r_b_dung.json()["ok"] is True
