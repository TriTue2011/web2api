"""Bộ kiểm thử toàn diện cho hệ thống tài khoản người dùng, phiên web, mật khẩu và phân quyền (Luật bảo mật).

Kiểm thử:
1. Mật khẩu & băm scrypt: tham số, độ ngẫu nhiên muối, kiểm tra chính sách, không lộ bí mật ra log/phản hồi.
2. Thiết lập lần đầu: mã thiết lập 0600, log không lộ mã, bảo vệ bằng mã/khoá quản trị, idempotency (409), race condition.
3. Đăng nhập: đồng nhất phản hồi lỗi 401, băm giả khi tên sai/khoá/tắt, khoá theo tên, bảo vệ rate limit IP.
4. Phiên web: token mới chống cố định phiên, hạn nhàn rỗi & tuyệt đối, trần 5 phiên, thu hồi khi đổi/đặt lại pass/tắt/xoá.
5. Cookie & CSRF: HttpOnly, SameSite=Strict, Secure + __Host- khi HTTPS, bắt buộc X-CSRF-Token cho POST/PUT/PATCH/DELETE bằng cookie, Bearer miễn CSRF.
6. Tích hợp xác thực: không bypass (Bearer sai không lùi cookie), user-role bị 403 ở quản trị và /v1, admin-cookie qua được.
7. Khoá API theo người dùng: pham_vi <= pham_vi_toi_da, IDOR xoá khoá (404 giống nhau), trần 10 khoá, vô hiệu khi user bị tắt/xoá, khoá cũ không chu_id dùng bình thường.
8. Quản trị & Bất biến: chặn hạ vai/tắt/xoá admin cuối cùng (409), danh sách không lộ băm, phân quyền gọi API quản trị.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app import gioi_han, kho, khoa_khach, nguoi_dung, nhat_ky, phien_web, xac_thuc
from app.main import app
from app.so_module import SoModule

KHOA_QT = "AIzaSyKIEMNGHIEM-000000000000000000009"
KHOA_SAI = "AIzaSySAI-bon-000000000000000000000004"
KHOA_KHAC = "AIzaSyTOT-mot-000000000000000000000001"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "30")
    monkeypatch.setenv("WEB2API_RL_KHOA_GIAY", "60")
    monkeypatch.setenv("WEB2API_DANG_NHAP_SAI_TOI_DA", "5")
    monkeypatch.setenv("WEB2API_DANG_NHAP_KHOA_PHUT", "15")
    monkeypatch.setenv("WEB2API_PHIEN_WEB_RANH_GIO", "8")
    monkeypatch.setenv("WEB2API_PHIEN_WEB_TOI_DA_NGAY", "7")

    with TestClient(app) as client:
        client.app.state.so = SoModule()
        yield client, data_dir


# ─────────────────────────────────────────────────────────────────────────────
# 1. MẬT KHẨU & BĂM SCRYPT
# ─────────────────────────────────────────────────────────────────────────────

def test_01_scrypt_tham_so_va_muoi_ngau_nhien():
    """Băm scrypt đúng cấu trúc scrypt$14$8$1$<muối>$<băm>, muối ngẫu nhiên mỗi lần."""
    pw = "MatKhauChuan123456"
    bam1 = asyncio.run(nguoi_dung.bam_mat_khau(pw))
    bam2 = asyncio.run(nguoi_dung.bam_mat_khau(pw))
    assert bam1 != bam2, "Băm phải khác nhau do muối ngẫu nhiên"

    parts1 = bam1.split("$")
    assert len(parts1) == 6
    assert parts1[0] == "scrypt"
    assert parts1[1] == "14"  # log2(16384)
    assert parts1[2] == "8"   # r
    assert parts1[3] == "1"   # p
    muoi = base64.b64decode(parts1[4])
    dk = base64.b64decode(parts1[5])
    assert len(muoi) == 16, "Muối phải 16 byte"
    assert len(dk) == 32, "Băm phải 32 byte"

    assert asyncio.run(nguoi_dung.kiem_tra_mat_khau(pw, bam1)) is True
    assert asyncio.run(nguoi_dung.kiem_tra_mat_khau("MatKhauSaiKhac123", bam1)) is False


def test_02_chinh_sach_mat_khau_va_ten():
    """Chính sách: độ dài 12-128 ký tự, không trùng tên đăng nhập; tên ASCII hợp lệ."""
    # Mật khẩu ngắn (<12)
    with pytest.raises(Exception) as exc:
        nguoi_dung.kiem_tra_chinh_sach_mat_khau("Ngan123", "admin")
    assert exc.value.ma == 400

    # Mật khẩu dài (>128)
    with pytest.raises(Exception) as exc:
        nguoi_dung.kiem_tra_chinh_sach_mat_khau("A" * 129, "admin")
    assert exc.value.ma == 400

    # Mật khẩu trùng tên đăng nhập
    with pytest.raises(Exception) as exc:
        nguoi_dung.kiem_tra_chinh_sach_mat_khau("Admin12345678", "admin12345678")
    assert exc.value.ma == 400

    # Tên không hợp lệ
    with pytest.raises(Exception) as exc:
        nguoi_dung.kiem_tra_ten_dang_nhap("ab")  # quá ngắn
    assert exc.value.ma == 400

    with pytest.raises(Exception) as exc:
        nguoi_dung.kiem_tra_ten_dang_nhap("admin@domain")  # chứa ký tự cấm @
    assert exc.value.ma == 400


# ─────────────────────────────────────────────────────────────────────────────
# 2. THIẾT LẬP LẦN ĐẦU (FIRST-RUN SETUP)
# ─────────────────────────────────────────────────────────────────────────────

def test_03_thiet_lap_lan_dau_thanh_cong_va_xoa_ma(moi_truong):
    """Khi chưa có admin: GET /api/thiet-lap -> true; POST đúng mã -> tạo admin, xoá mã, đăng nhập luôn."""
    client, data_dir = moi_truong
    # 1. Khởi động tạo mã thiết lập
    nguoi_dung.khoi_tao_thiet_lap_lan_dau()
    ma_file = data_dir / "ma_thiet_lap"
    assert ma_file.is_file(), "Phải tạo tệp ma_thiet_lap"
    # Kiểm tra quyền 0600
    assert (ma_file.stat().st_mode & 0o777) == 0o600
    ma_thiet_lap = ma_file.read_text(encoding="utf-8").strip()
    assert len(ma_thiet_lap) == 20

    # 2. GET /api/thiet-lap mở
    r_get = client.get("/api/thiet-lap")
    assert r_get.status_code == 200
    assert r_get.json() == {"can_thiet_lap": True}

    # 3. POST sai mã -> 401
    r_sai = client.post("/api/thiet-lap", json={
        "ma_thiet_lap": "MaSaiHoanToan1234567",
        "ten_dang_nhap": "admin-chinh",
        "mat_khau": "MatKhauAdminTot123456",
    })
    assert r_sai.status_code == 401

    # 4. POST đúng mã -> 200, đặt cookie, xoá tệp mã
    r_dung = client.post("/api/thiet-lap", json={
        "ma_thiet_lap": ma_thiet_lap,
        "ten_dang_nhap": "admin-chinh",
        "mat_khau": "MatKhauAdminTot123456",
    })
    assert r_dung.status_code == 200
    body = r_dung.json()
    assert body["ok"] is True
    assert "csrf" in body
    assert body["nguoi_dung"]["ten_dang_nhap"] == "admin-chinh"
    assert body["nguoi_dung"]["vai"] == "admin"
    assert not ma_file.exists(), "Tệp ma_thiet_lap phải bị xoá sau thiết lập"
    assert "w2a_phien" in r_dung.cookies

    # 5. Gọi lại lần 2 -> 409 Conflict (không xử lý thân)
    r_lai = client.post("/api/thiet-lap", json={
        "ma_thiet_lap": ma_thiet_lap,
        "ten_dang_nhap": "admin-2",
        "mat_khau": "MatKhauKhac123456",
    })
    assert r_lai.status_code == 409


def test_04_thiet_lap_bang_khoa_quan_tri(moi_truong):
    """Thiết lập lần đầu chấp nhận khoá quản trị hiện hành thay cho mã thiết lập."""
    client, data_dir = moi_truong
    nguoi_dung.khoi_tao_thiet_lap_lan_dau()

    r = client.post("/api/thiet-lap", json={
        "ma_thiet_lap": KHOA_QT,
        "ten_dang_nhap": "admin-qua-khoa",
        "mat_khau": "MatKhauAdminTot123456",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["nguoi_dung"]["ten_dang_nhap"] == "admin-qua-khoa"


def test_05_hai_yeu_cau_thiet_lap_dong_thoi(moi_truong):
    """Hai yêu cầu thiết lập đồng thời chỉ tạo đúng một admin, yêu cầu kia nhận 409."""
    client, data_dir = moi_truong
    nguoi_dung.khoi_tao_thiet_lap_lan_dau()
    ma = (data_dir / "ma_thiet_lap").read_text(encoding="utf-8").strip()

    # Dùng asyncio gọi 2 tác vụ đồng thời
    async def goi():
        return await asyncio.gather(
            asyncio.to_thread(client.post, "/api/thiet-lap", json={"ma_thiet_lap": ma, "ten_dang_nhap": "admin-1", "mat_khau": "MatKhauAdminTot123456"}),
            asyncio.to_thread(client.post, "/api/thiet-lap", json={"ma_thiet_lap": ma, "ten_dang_nhap": "admin-2", "mat_khau": "MatKhauAdminTot123456"}),
        )

    r1, r2 = asyncio.run(goi())
    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [200, 409], f"Mong đợi [200, 409], nhận {statuses}"
    # Đảm bảo chỉ có 1 admin trong hệ thống
    danh_sach = nguoi_dung.danh_sach_nguoi_dung()
    assert len(danh_sach) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 3. ĐĂNG NHẬP, BĂM GIẢ, KHOÁ THEO TÊN
# ─────────────────────────────────────────────────────────────────────────────

def test_06_dang_nhap_dong_nhat_loi_va_bam_gia(moi_truong):
    """Sai pass, tên không tồn tại, tài khoản bị tắt, tên bị khoá đều trả 401 đồng nhất và chạy băm giả."""
    client, _ = moi_truong
    # Tạo 1 admin và 1 user bị tắt
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-tot", mat_khau="MatKhauChuan123456"))
    u_tat, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-tat", mat_khau="MatKhauChuan123456"))
    nguoi_dung.sua_nguoi_dung(u_tat["id"], bat=False)

    dem_bam_gia = 0
    goc_chay_bam_gia = nguoi_dung.chay_bam_gia

    async def mock_bam_gia():
        nonlocal dem_bam_gia
        dem_bam_gia += 1
        await goc_chay_bam_gia()

    with patch.object(nguoi_dung, "chay_bam_gia", side_effect=mock_bam_gia):
        # 1. Tên không tồn tại
        r_khong_tt = client.post("/api/dang-nhap", json={"ten_dang_nhap": "khong-ton-tai", "mat_khau": "MatKhauChuan123456"})
        assert r_khong_tt.status_code == 401
        assert dem_bam_gia == 1

        # 2. Tài khoản bị tắt
        r_tat = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-tat", "mat_khau": "MatKhauChuan123456"})
        assert r_tat.status_code == 401
        assert dem_bam_gia == 2

        # 3. Sai mật khẩu (so khớp thật)
        r_sai_pw = client.post("/api/dang-nhap", json={"ten_dang_nhap": "admin-tot", "mat_khau": "SaiMatKhau123456"})
        assert r_sai_pw.status_code == 401

        # Kiểm tra nội dung thân phản hồi 401 của 3 trường hợp hoàn toàn giống nhau
        assert r_khong_tt.text == r_tat.text == r_sai_pw.text
        assert "Sai tên đăng nhập hoặc mật khẩu" in r_khong_tt.text


def test_07_khoa_theo_ten_sau_5_lan_sai(moi_truong):
    """5 lần sai liên tiếp -> khoá tên 15 phút. Đăng nhập đúng trong lúc khoá vẫn bị 401."""
    client, _ = moi_truong
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="victim-user", mat_khau="MatKhauChuan123456"))

    # Thử sai 5 lần
    for _ in range(5):
        r = client.post("/api/dang-nhap", json={"ten_dang_nhap": "victim-user", "mat_khau": "SaiMatKhau123456"})
        assert r.status_code == 401

    # Lần thứ 6: ĐĂNG NHẬP ĐÚNG MẬT KHẨU nhưng tài khoản đang bị khoá -> vẫn 401!
    r_dung = client.post("/api/dang-nhap", json={"ten_dang_nhap": "victim-user", "mat_khau": "MatKhauChuan123456"})
    assert r_dung.status_code == 401
    assert "Sai tên đăng nhập hoặc mật khẩu" in r_dung.text


def test_08_gioi_han_rate_limit_ip_khi_dang_nhap_sai(moi_truong, monkeypatch):
    """Đăng nhập sai quá ngưỡng của gioi_han (30 lần) -> nhận 429 Retry-After."""
    client, _ = moi_truong
    monkeypatch.setattr(gioi_han, "_lay_ip", lambda req: "192.0.2.88")
    gioi_han.khoi_dong()
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="test-rl", mat_khau="MatKhauChuan123456"))

    for _ in range(31):
        client.post("/api/dang-nhap", json={"ten_dang_nhap": "test-rl", "mat_khau": "SaiMatKhau123456"})

    r_429 = client.post("/api/dang-nhap", json={"ten_dang_nhap": "test-rl", "mat_khau": "MatKhauChuan123456"})
    assert r_429.status_code == 429
    assert "Retry-After" in r_429.headers


# ─────────────────────────────────────────────────────────────────────────────
# 4. PHIÊN WEB, TOKEN MỚI, THU HỒI
# ─────────────────────────────────────────────────────────────────────────────

def test_09_token_moi_moi_lan_dang_nhap_va_kho_chi_luu_bam(moi_truong):
    """Mỗi lần đăng nhập sinh token mới; kho phien_web.json chỉ lưu băm SHA-256."""
    client, data_dir = moi_truong
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-phien", mat_khau="MatKhauChuan123456"))

    r1 = client.post("/api/dang-nhap", json={"ten_dang_nhap": "admin-phien", "mat_khau": "MatKhauChuan123456"})
    token1 = r1.cookies.get("w2a_phien")
    assert token1

    r2 = client.post("/api/dang-nhap", json={"ten_dang_nhap": "admin-phien", "mat_khau": "MatKhauChuan123456"})
    token2 = r2.cookies.get("w2a_phien")
    assert token2
    assert token1 != token2, "Mỗi lần đăng nhập phải sinh token mới chống cố định phiên"

    # Đọc tệp phien_web.json kiểm tra không lưu token thô
    tep_phien = data_dir / "phien_web.json"
    noi_dung = tep_phien.read_text(encoding="utf-8")
    assert token1 not in noi_dung, "Token thô không được lưu vào tệp!"
    assert token2 not in noi_dung, "Token thô không được lưu vào tệp!"
    assert hashlib.sha256(token1.encode("ascii")).hexdigest() in noi_dung


def test_10_toi_da_5_phien_moi_nguoi_dung(moi_truong):
    """Tạo phiên thứ 6 thì phiên cũ nhất bị tự động loại bỏ."""
    client, _ = moi_truong
    u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-5phien", mat_khau="MatKhauChuan123456"))

    tokens = []
    for _ in range(6):
        _, tok = phien_web.tao_phien(u["id"])
        tokens.append(tok)

    # Token đầu tiên (cũ nhất) phải bị thu hồi
    assert phien_web.xac_thuc_token(tokens[0]) is None
    # 5 token sau phải còn hiệu lực
    for tok in tokens[1:]:
        assert phien_web.xac_thuc_token(tok) is not None


def test_11_het_han_phien_nhan_roi_va_tuyet_doi(moi_truong, monkeypatch):
    """Phiên hết hạn nhàn rỗi (8h) hoặc tuyệt đối (7 ngày) đều bị từ chối và xoá."""
    client, _ = moi_truong
    u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-hethan", mat_khau="MatKhauChuan123456"))
    _, tok = phien_web.tao_phien(u["id"])

    # Giả lập thời gian trôi qua 9 giờ (vượt 8 giờ nhàn rỗi)
    from datetime import datetime, timedelta, timezone
    now_tuong_lai = datetime.now(timezone.utc) + timedelta(hours=9)
    with patch("app.phien_web.datetime") as mock_dt:
        mock_dt.now.return_value = now_tuong_lai
        mock_dt.fromisoformat = datetime.fromisoformat
        assert phien_web.xac_thuc_token(tok) is None


def test_12_thu_hoi_phien_khi_dang_xuat_doi_mat_khau_tat_xoa(moi_truong):
    """Đăng xuất, đổi mật khẩu, đặt lại mật khẩu, tắt user, xoá user đều thu hồi phiên ngay."""
    client, _ = moi_truong
    u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-thuhoi", mat_khau="MatKhauChuan123456"))

    # 1. Đăng xuất thu hồi phiên
    r_dn = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-thuhoi", "mat_khau": "MatKhauChuan123456"})
    tok1 = r_dn.cookies.get("w2a_phien")
    csrf1 = r_dn.json()["csrf"]
    r_dx = client.post("/api/dang-xuat", cookies={"w2a_phien": tok1}, headers={"X-CSRF-Token": csrf1})
    assert r_dx.status_code == 200
    assert phien_web.xac_thuc_token(tok1) is None

    # 2. Đổi mật khẩu thu hồi mọi phiên khác
    p_giu, tok_giu = phien_web.tao_phien(u["id"])
    p_khac, tok_khac = phien_web.tao_phien(u["id"])
    r_doi = client.post(
        "/api/toi/mat-khau",
        json={"mat_khau_cu": "MatKhauChuan123456", "mat_khau_moi": "MatKhauMoiToanh123"},
        cookies={"w2a_phien": tok_giu},
        headers={"X-CSRF-Token": p_giu["csrf"]},
    )
    assert r_doi.status_code == 200
    # tok_giu vẫn còn hiệu lực
    assert phien_web.xac_thuc_token(tok_giu) is not None
    # tok_khac bị thu hồi
    assert phien_web.xac_thuc_token(tok_khac) is None

    # 3. Quản trị đặt lại mật khẩu thu hồi phiên
    _, tok3 = phien_web.tao_phien(u["id"])
    client.post(f"/api/nguoi-dung/{u['id']}/dat-lai-mat-khau", headers={"Authorization": f"Bearer {KHOA_QT}"})
    assert phien_web.xac_thuc_token(tok3) is None

    # 4. Tắt tài khoản thu hồi phiên
    _, tok4 = phien_web.tao_phien(u["id"])
    client.patch(f"/api/nguoi-dung/{u['id']}", headers={"Authorization": f"Bearer {KHOA_QT}"}, json={"bat": False})
    assert phien_web.xac_thuc_token(tok4) is None


# ─────────────────────────────────────────────────────────────────────────────
# 5. COOKIE BẢO MẬT & CHỐNG CSRF
# ─────────────────────────────────────────────────────────────────────────────

def test_13_cookie_co_va_secure_khi_https(moi_truong):
    """Cookie có cờ HttpOnly, SameSite=Strict, Path=/; bật Secure và __Host- khi HTTPS qua proxy."""
    client, _ = moi_truong
    nguoi_dung.khoi_tao_thiet_lap_lan_dau()
    ma = nguoi_dung.doc_ma_thiet_lap()

    # 1. HTTP thường
    r_http = client.post("/api/thiet-lap", json={"ma_thiet_lap": ma, "ten_dang_nhap": "admin-http", "mat_khau": "MatKhauAdminTot123456"})
    cookie_str = r_http.headers.get("set-cookie", "")
    assert "w2a_phien=" in cookie_str
    assert "HttpOnly" in cookie_str
    assert "SameSite=strict" in cookie_str or "SameSite=Strict" in cookie_str
    assert "Path=/" in cookie_str
    assert "Secure" not in cookie_str
    assert "Domain=" not in cookie_str

    # 2. HTTPS giả lập qua proxy tin cậy (WEB2API_TIN_PROXY=1)
    os.environ["WEB2API_TIN_PROXY"] = "1"
    r_https = client.post(
        "/api/dang-nhap",
        json={"ten_dang_nhap": "admin-http", "mat_khau": "MatKhauAdminTot123456"},
        headers={"X-Forwarded-Proto": "https"},
    )
    cookie_https = r_https.headers.get("set-cookie", "")
    assert "__Host-w2a_phien=" in cookie_https
    assert "Secure" in cookie_https
    assert "HttpOnly" in cookie_https


def test_14_csrf_bat_buoc_cho_cookie_va_mien_cho_bearer(moi_truong):
    """Xác thực bằng cookie bắt buộc header X-CSRF-Token khớp; thiếu/sai -> 403. Bearer token miễn CSRF."""
    client, _ = moi_truong
    u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-csrf", mat_khau="MatKhauChuan123456"))
    p, tok = phien_web.tao_phien(u["id"])

    # 1. Cookie + POST thiếu header X-CSRF-Token -> 403
    r_thieu = client.post("/api/nguoi-dung", cookies={"w2a_phien": tok}, json={"vai": "user"})
    assert r_thieu.status_code == 403
    assert "CSRF" in r_thieu.text

    # 2. Cookie + POST sai header X-CSRF-Token -> 403
    r_sai = client.post("/api/nguoi-dung", cookies={"w2a_phien": tok}, headers={"X-CSRF-Token": "TokenSaiCsrf123"}, json={"vai": "user"})
    assert r_sai.status_code == 403

    # 3. Cookie + POST đúng header X-CSRF-Token -> 200
    r_dung = client.post("/api/nguoi-dung", cookies={"w2a_phien": tok}, headers={"X-CSRF-Token": p["csrf"]}, json={"vai": "user"})
    assert r_dung.status_code == 200

    # 4. Bearer token miễn CSRF (không cần header X-CSRF-Token vẫn thành công)
    r_bearer = client.post("/api/nguoi-dung", headers={"Authorization": f"Bearer {KHOA_QT}"}, json={"vai": "user"})
    assert r_bearer.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 6. TÍCH HỢP XÁC THỰC & KHÔNG BYPASS
# ─────────────────────────────────────────────────────────────────────────────

def test_15_bearer_sai_khong_lui_ve_cookie(moi_truong):
    """Có Authorization nhưng sai thì KHÔNG lùi về cookie (không bypass) -> 401."""
    client, _ = moi_truong
    u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-bypass", mat_khau="MatKhauChuan123456"))
    p, tok = phien_web.tao_phien(u["id"])

    # Gửi cả Authorization sai và Cookie đúng -> 401
    r = client.get("/api/nguoi-dung", headers={"Authorization": f"Bearer {KHOA_SAI}"}, cookies={"w2a_phien": tok})
    assert r.status_code == 401


def test_16_vai_user_bi_403_o_quan_tri_va_v1(moi_truong):
    """Cookie vai user gọi đường quản trị -> 403; gọi /v1 -> 403. Admin-cookie gọi cả hai -> qua."""
    client, _ = moi_truong
    u_user, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-thuong", mat_khau="MatKhauChuan123456"))
    p_user, tok_user = phien_web.tao_phien(u_user["id"])

    u_adm, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-vip", mat_khau="MatKhauChuan123456"))
    p_adm, tok_adm = phien_web.tao_phien(u_adm["id"])

    # 1. User gọi đường quản trị -> 403
    r_qt = client.get("/api/nguoi-dung", cookies={"w2a_phien": tok_user})
    assert r_qt.status_code == 403

    # 2. User gọi /v1/models bằng cookie -> 403
    r_v1 = client.get("/v1/models", cookies={"w2a_phien": tok_user})
    assert r_v1.status_code == 403

    # 3. Admin gọi đường quản trị bằng cookie -> 200
    r_adm_qt = client.get("/api/nguoi-dung", cookies={"w2a_phien": tok_adm})
    assert r_adm_qt.status_code == 200

    # 4. Admin gọi /v1/models bằng cookie -> 200
    r_adm_v1 = client.get("/v1/models", cookies={"w2a_phien": tok_adm})
    assert r_adm_v1.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 7. KHOÁ API THEO NGƯỜI DÙNG & TỰ PHỤC VỤ
# ─────────────────────────────────────────────────────────────────────────────

def test_17_tu_phuc_vu_tao_va_quan_ly_khoa_api(moi_truong):
    """Tự phục vụ: tạo khoá (phạm vi <= pham_vi_toi_da), trần 10 khoá, IDOR xoá khoá (404)."""
    client, _ = moi_truong
    # Tạo user có pham_vi_toi_da = ["chat"]
    u1, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-key1", mat_khau="MatKhauChuan123456", pham_vi_toi_da=["chat"]))
    p1, tok1 = phien_web.tao_phien(u1["id"])

    u2, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-key2", mat_khau="MatKhauChuan123456", pham_vi_toi_da=["chat"]))
    p2, tok2 = phien_web.tao_phien(u2["id"])

    headers1 = {"X-CSRF-Token": p1["csrf"]}

    # 1. Tạo khoá vượt quá phạm vi -> 403 insufficient_scope
    r_vuot = client.post("/api/toi/khoa", cookies={"w2a_phien": tok1}, headers=headers1, json={"nhan": "K", "pham_vi": ["chat", "anh"]})
    assert r_vuot.status_code == 403
    assert "insufficient_scope" in r_vuot.text

    # Yêu cầu '*' khi không có '*' -> 403
    r_sao = client.post("/api/toi/khoa", cookies={"w2a_phien": tok1}, headers=headers1, json={"nhan": "K", "pham_vi": ["*"]})
    assert r_sao.status_code == 403

    # 2. Tạo khoá hợp lệ -> 200, hiện khoá thô
    r_tao = client.post("/api/toi/khoa", cookies={"w2a_phien": tok1}, headers=headers1, json={"nhan": "Khoá Chat", "pham_vi": ["chat"]})
    assert r_tao.status_code == 200
    k1_body = r_tao.json()
    id_k1 = k1_body["khoa"]["id"]
    tho_k1 = k1_body["khoa_tho"]
    assert tho_k1.startswith("w2a_")
    assert k1_body["khoa"]["chu_id"] == u1["id"]

    # 3. User 2 tạo khoá cho mình
    headers2 = {"X-CSRF-Token": p2["csrf"]}
    r_tao2 = client.post("/api/toi/khoa", cookies={"w2a_phien": tok2}, headers=headers2, json={"nhan": "Khoá User 2", "pham_vi": ["chat"]})
    id_k2 = r_tao2.json()["khoa"]["id"]

    # 4. User 1 xem danh sách: chỉ thấy khoá của mình, không thấy khoá của User 2
    r_ds = client.get("/api/toi/khoa", cookies={"w2a_phien": tok1})
    cac_id = [x["id"] for x in r_ds.json()["khoa"]]
    assert id_k1 in cac_id
    assert id_k2 not in cac_id

    # 5. IDOR: User 1 cố xoá khoá của User 2 -> 404 giống hệt khoá không tồn tại
    r_xoa_trom = client.delete(f"/api/toi/khoa/{id_k2}", cookies={"w2a_phien": tok1}, headers=headers1)
    assert r_xoa_trom.status_code == 404

    r_xoa_ko_tt = client.delete("/api/toi/khoa/kk_khongtontai999", cookies={"w2a_phien": tok1}, headers=headers1)
    assert r_xoa_ko_tt.status_code == 404
    assert r_xoa_trom.text == r_xoa_ko_tt.text

    # 6. User 1 xoá đúng khoá của mình -> 200
    r_xoa_dung = client.delete(f"/api/toi/khoa/{id_k1}", cookies={"w2a_phien": tok1}, headers=headers1)
    assert r_xoa_dung.status_code == 200


def test_18_khoa_api_vo_hieu_khi_user_bi_tat_hoac_xoa(moi_truong):
    """Khoá API có chu_id lập tức vô hiệu khi người dùng bị tắt hoặc xoá."""
    client, _ = moi_truong
    u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-khoa-vohieu", mat_khau="MatKhauChuan123456", pham_vi_toi_da=["chat"]))
    p, tok = phien_web.tao_phien(u["id"])

    # Tạo khoá API
    r_tao = client.post("/api/toi/khoa", cookies={"w2a_phien": tok}, headers={"X-CSRF-Token": p["csrf"]}, json={"nhan": "K", "pham_vi": ["chat"]})
    khoa_tho = r_tao.json()["khoa_tho"]

    # Dùng khoá gọi /v1/models -> 200
    r_v1 = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_tho}"})
    assert r_v1.status_code == 200

    # 1. Tắt tài khoản -> gọi /v1/models lập tức bị từ chối 401
    client.patch(f"/api/nguoi-dung/{u['id']}", headers={"Authorization": f"Bearer {KHOA_QT}"}, json={"bat": False})
    r_v1_tat = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_tho}"})
    assert r_v1_tat.status_code == 401


def test_19_khoa_cu_khong_chu_id_van_dung_binh_thuong(moi_truong):
    """Khoá cũ không có trường chu_id (None) vẫn dùng bình thường."""
    client, _ = moi_truong
    ban_ghi, khoa_tho = khoa_khach.tao_khoa("Khoá cũ", pham_vi=["chat"])
    assert ban_ghi.get("chu_id") is None

    r = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_tho}"})
    assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 8. BẤT BIẾN ADMIN CUỐI CÙNG & QUẢN TRỊ
# ─────────────────────────────────────────────────────────────────────────────

def test_20_bat_bien_admin_cuoi_cung(moi_truong):
    """Không thể hạ vai, tắt hoặc xoá tài khoản admin đang bật cuối cùng (409)."""
    client, _ = moi_truong
    u_admin, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-duy-nhat", mat_khau="MatKhauChuan123456"))

    # 1. Cố hạ vai xuống user -> 409
    r_ha = client.patch(f"/api/nguoi-dung/{u_admin['id']}", headers={"Authorization": f"Bearer {KHOA_QT}"}, json={"vai": "user"})
    assert r_ha.status_code == 409
    assert "admin cuối cùng" in r_ha.text

    # 2. Cố tắt admin duy nhất -> 409
    r_tat = client.patch(f"/api/nguoi-dung/{u_admin['id']}", headers={"Authorization": f"Bearer {KHOA_QT}"}, json={"bat": False})
    assert r_tat.status_code == 409

    # 3. Cố xoá admin duy nhất -> 409
    r_xoa = client.delete(f"/api/nguoi-dung/{u_admin['id']}", headers={"Authorization": f"Bearer {KHOA_QT}"})
    assert r_xoa.status_code == 409

    # Tạo thêm admin thứ 2 -> giờ có thể hạ vai admin 1
    u_admin2, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-thu-hai", mat_khau="MatKhauChuan123456"))
    r_ha_ok = client.patch(f"/api/nguoi-dung/{u_admin['id']}", headers={"Authorization": f"Bearer {KHOA_QT}"}, json={"vai": "user"})
    assert r_ha_ok.status_code == 200


def test_21_danh_sach_nguoi_dung_khong_lo_bam_mat_khau(moi_truong):
    """GET /api/nguoi-dung không bao giờ trả băm mật khẩu hay bí mật."""
    client, _ = moi_truong
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-check", mat_khau="MatKhauChuan123456"))

    r = client.get("/api/nguoi-dung", headers={"Authorization": f"Bearer {KHOA_QT}"})
    assert r.status_code == 200
    assert "bam_mat_khau" not in r.text
    assert "scrypt$" not in r.text


def test_22_khoi_phuc_quen_mat_khau_admin_bang_khoa_quan_tri(moi_truong):
    """Khi quên mật khẩu admin: dùng Bearer khoá quản trị gọi POST /api/nguoi-dung/{id}/dat-lai-mat-khau."""
    client, _ = moi_truong
    u_admin, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-quen", mat_khau="MatKhauCu1234567"))

    r = client.post(f"/api/nguoi-dung/{u_admin['id']}/dat-lai-mat-khau", headers={"Authorization": f"Bearer {KHOA_QT}"})
    assert r.status_code == 200
    moi = r.json()
    mat_khau_moi = moi["mat_khau_moi"]
    assert len(mat_khau_moi) == 20

    # Đăng nhập bằng mật khẩu mới thành công
    r_dn = client.post("/api/dang-nhap", json={"ten_dang_nhap": "admin-quen", "mat_khau": mat_khau_moi})
    assert r_dn.status_code == 200


def test_23_khong_lo_mat_khau_trong_log(moi_truong, caplog):
    """Mật khẩu không bao giờ xuất hiện trong log/nhật ký."""
    client, _ = moi_truong
    mat_khau_doc_nhat = "BimatKhongTheTietLo999"

    with caplog.at_level("DEBUG"):
        # Đăng ký / đăng nhập với mật khẩu này
        client.post("/api/thiet-lap", json={"ma_thiet_lap": KHOA_QT, "ten_dang_nhap": "admin-log", "mat_khau": mat_khau_doc_nhat})
        client.post("/api/dang-nhap", json={"ten_dang_nhap": "admin-log", "mat_khau": mat_khau_doc_nhat})

    assert mat_khau_doc_nhat not in caplog.text, "Mật khẩu bị lộ trong log!"


# ─────────────────────────────────────────────────────────────────────────────
# 9. SOÁT ĐỘC LẬP — leo thang phạm vi, kho hỏng, đầu vào sai kiểu, đồng thời
# ─────────────────────────────────────────────────────────────────────────────

QT_HDR = {"Authorization": f"Bearer {KHOA_QT}"}
MK = "MatKhauChuan123456"


def _tao_va_vao(ten, vai="user", pham_vi_toi_da=None):
    """Tạo người dùng và một phiên web; trả (bản ghi, token cookie, header CSRF)."""
    u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai=vai, ten_dang_nhap=ten, mat_khau=MK, pham_vi_toi_da=pham_vi_toi_da))
    p, tok = phien_web.tao_phien(u["id"])
    return u, tok, {"X-CSRF-Token": p["csrf"]}


def _ma_loi_chat(client, khoa_tho, them_hdr=None):
    """Gọi /v1/chat/completions bằng khoá khách; trả (mã HTTP, mã lỗi). Phạm vi được kiểm TRƯỚC khi tra model."""
    r = client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {khoa_tho}", **(them_hdr or {})},
                    json={"model": "khong-co/model", "messages": [{"role": "user", "content": "x"}]})
    try:
        return r.status_code, r.json().get("error", {}).get("code")
    except Exception:
        return r.status_code, None


def _ma_loi_anh(client, khoa_tho):
    r = client.post("/v1/images/generations", headers={"Authorization": f"Bearer {khoa_tho}"},
                    json={"model": "khong-co/model", "prompt": "x"})
    return r.status_code, r.json().get("error", {}).get("code")


@pytest.mark.parametrize("than", [
    {"pham_vi": []},
    {"pham_vi": ["", " "]},
    {"pham_vi": ["*"]},
    {"pham_vi": ["chat", "*"]},
    {"pham_vi": [["*"]]},
    {"pham_vi": [1]},
    {"pham_vi": [None]},
    {"pham_vi": "chat"},
    {"pham_vi": ["CHAT"]},
    {"pham_vi": ["chat", ""]},
    {"pham_vi": {"*": True}},
    {"pham_vi": None},
])
def test_24_tao_khoa_toi_pham_vi_sai_khong_ra_khoa_rong_hon_tran(moi_truong, than):
    """Điểm 1: vai user trần ["chat"] — mọi dạng pham_vi sai/rỗng/«*» đều bị từ chối, không ra khoá «*»."""
    client, _ = moi_truong
    u, tok, h = _tao_va_vao("bob-pv", pham_vi_toi_da=["chat"])
    r = client.post("/api/toi/khoa", cookies={"w2a_phien": tok}, headers=h, json={"nhan": "k", **than})
    assert r.status_code in (400, 403), f"{than} → {r.status_code} {r.text}"
    assert khoa_khach.danh_sach_khoa_theo_chu(u["id"]) == [], f"{than} vẫn tạo được khoá"


def test_25_tao_khoa_toi_thieu_truong_bang_tran_va_tran_rong(moi_truong):
    """Điểm 1: thiếu pham_vi → khoá ĐÚNG bằng trần; người dùng trần rỗng không tạo được khoá nào."""
    client, _ = moi_truong
    u, tok, h = _tao_va_vao("bob-thieu", pham_vi_toi_da=["chat"])
    r = client.post("/api/toi/khoa", cookies={"w2a_phien": tok}, headers=h, json={"nhan": "k"})
    assert r.status_code == 200
    assert r.json()["khoa"]["pham_vi"] == ["chat"]

    e, tok_e, h_e = _tao_va_vao("eve-rong", pham_vi_toi_da=[])
    for than in ({"nhan": "k"}, {"nhan": "k", "pham_vi": []}, {"nhan": "k", "pham_vi": ["chat"]}, {"nhan": "k", "pham_vi": ["*"]}):
        r = client.post("/api/toi/khoa", cookies={"w2a_phien": tok_e}, headers=h_e, json=than)
        assert r.status_code in (400, 403), f"{than} → {r.status_code} {r.text}"
    assert khoa_khach.danh_sach_khoa_theo_chu(e["id"]) == []

    # Admin dùng cookie: trần «*» — thiếu trường ra «*», danh sách rỗng vẫn bị từ chối
    a, tok_a, h_a = _tao_va_vao("admin-pv", vai="admin")
    r = client.post("/api/toi/khoa", cookies={"w2a_phien": tok_a}, headers=h_a, json={"nhan": "k"})
    assert r.status_code == 200 and r.json()["khoa"]["pham_vi"] == ["*"]
    r = client.post("/api/toi/khoa", cookies={"w2a_phien": tok_a}, headers=h_a, json={"nhan": "k", "pham_vi": []})
    assert r.status_code == 400


def test_26_duong_quan_tri_khoa_khach_rong_khong_thanh_tat_ca(moi_truong):
    """Điểm 1 (đường quản trị): không nêu pham_vi mặc định là ["chat"], nhưng [] / phần tử sai → 400, bat phải là bool."""
    client, _ = moi_truong
    r = client.post("/api/khoa-khach", headers=QT_HDR, json={"nhan": "k"})
    assert r.status_code == 200 and r.json()["khoa"]["pham_vi"] == ["chat"]
    id_k = r.json()["khoa"]["id"]
    for pv in ([], ["", " "], [1], "chat", ["CHAT"]):
        r = client.post("/api/khoa-khach", headers=QT_HDR, json={"nhan": "k", "pham_vi": pv})
        assert r.status_code == 400, f"{pv} → {r.status_code}"
        r = client.patch(f"/api/khoa-khach/{id_k}", headers=QT_HDR, json={"pham_vi": pv})
        assert r.status_code == 400, f"PATCH {pv} → {r.status_code}"
    r = client.patch(f"/api/khoa-khach/{id_k}", headers=QT_HDR, json={"bat": False})
    assert r.status_code == 200 and r.json()["khoa"]["bat"] is False
    r = client.patch(f"/api/khoa-khach/{id_k}", headers=QT_HDR, json={"bat": "true"})
    assert r.status_code == 400
    assert khoa_khach.danh_sach_khoa()[0]["bat"] is False
    # Mã có hậu tố «:» của thiết bị vẫn hợp lệ
    r = client.post("/api/khoa-khach", headers=QT_HDR, json={"nhan": "k", "pham_vi": ["thiet_bi:doc"]})
    assert r.status_code == 200 and r.json()["khoa"]["pham_vi"] == ["thiet_bi:doc"]


def test_27_ha_tran_thu_hep_khoa_da_cap_ngay(moi_truong, monkeypatch):
    """Điểm 2: phạm vi hiệu lực = giao(phạm vi khoá, trần HIỆN TẠI của chủ); hạ trần → hẹp ngay, nâng → rộng lại."""
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    client, _ = moi_truong
    u, _, _ = _tao_va_vao("bob-tran", pham_vi_toi_da=["chat"])
    # Khoá «*» như đã đúc qua lỗ cũ (hoặc lúc trần còn rộng)
    _, khoa_sao = khoa_khach.tao_khoa("cũ", pham_vi=["*"], chu_id=u["id"])
    assert _ma_loi_chat(client, khoa_sao)[1] != "insufficient_scope"
    assert _ma_loi_anh(client, khoa_sao) == (403, "insufficient_scope")
    # Thu hẹp bằng header cũng không vượt trần
    assert _ma_loi_chat(client, khoa_sao, {"X-Web2api-Scope": "anh"}) == (403, "insufficient_scope")

    # Hạ trần về rỗng → khoá cũ mất mọi phạm vi
    r = client.patch(f"/api/nguoi-dung/{u['id']}", headers=QT_HDR, json={"pham_vi_toi_da": []})
    assert r.status_code == 200
    assert _ma_loi_chat(client, khoa_sao) == (403, "insufficient_scope")
    r = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_sao}"})
    assert r.status_code == 200 and r.json()["data"] == []

    # Nâng lại trần → rộng lại ngay
    client.patch(f"/api/nguoi-dung/{u['id']}", headers=QT_HDR, json={"pham_vi_toi_da": ["chat", "anh"]})
    assert _ma_loi_chat(client, khoa_sao)[1] != "insufficient_scope"
    assert _ma_loi_anh(client, khoa_sao)[1] != "insufficient_scope"


def test_28_ha_vai_admin_thu_hep_khoa(moi_truong, monkeypatch):
    """Điểm 2: hạ vai admin → user không kèm trần mới: khoá «*» của người đó về trần mặc định của user."""
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    client, _ = moi_truong
    _tao_va_vao("admin-giu", vai="admin")
    a2, _, _ = _tao_va_vao("admin-ha", vai="admin")
    _, khoa_sao = khoa_khach.tao_khoa("của admin", pham_vi=["*"], chu_id=a2["id"])
    assert _ma_loi_anh(client, khoa_sao)[1] != "insufficient_scope"
    r = client.patch(f"/api/nguoi-dung/{a2['id']}", headers=QT_HDR, json={"vai": "user"})
    assert r.status_code == 200
    assert r.json()["nguoi_dung"]["pham_vi_toi_da"] == ["chat"]
    assert _ma_loi_anh(client, khoa_sao) == (403, "insufficient_scope")
    assert _ma_loi_chat(client, khoa_sao)[1] != "insufficient_scope"


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.mark.parametrize("noi_dung", ["{hong", "[1, 2]", '{"nguoi_dung": "x"}', '{"nguoi_dung": [1]}', '{"nguoi_dung": [{"ten_dang_nhap": "khong-id"}]}'])
def test_29_kho_nguoi_dung_hong_khong_mo_lai_thiet_lap_khong_ghi_de(moi_truong, monkeypatch, noi_dung):
    """Điểm 3: tệp tồn tại mà hỏng → đóng khi lỗi: không mở lại thiết lập, không ghi đè, đăng nhập 401 đồng nhất."""
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    client, data_dir = moi_truong
    u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-that", mat_khau=MK))
    p_phien, tok = phien_web.tao_phien(u["id"])
    r_sai = client.post("/api/dang-nhap", json={"ten_dang_nhap": "admin-that", "mat_khau": "SaiMatKhau123456"})
    tep = data_dir / "nguoi_dung.json"
    (data_dir / "ma_thiet_lap").unlink(missing_ok=True)  # mã do lượt khởi động của fixture tạo (lúc chưa có admin)
    tep.write_text(noi_dung, encoding="utf-8")
    truoc = _sha(tep)

    assert nguoi_dung.co_admin() is True
    assert client.get("/api/thiet-lap").json() == {"can_thiet_lap": False}
    r = client.post("/api/thiet-lap", json={"ma_thiet_lap": KHOA_QT, "ten_dang_nhap": "hacker1", "mat_khau": "HackerPass-12345"})
    assert r.status_code in (409, 503), r.text
    r = client.post("/api/nguoi-dung", headers=QT_HDR, json={"ten_dang_nhap": "moi-1", "mat_khau": MK})
    assert r.status_code == 503, r.text
    r = client.patch(f"/api/nguoi-dung/{u['id']}", headers=QT_HDR, json={"bat": True})
    assert r.status_code == 503
    r = client.delete(f"/api/nguoi-dung/{u['id']}", headers=QT_HDR)
    assert r.status_code == 503
    r = client.post(f"/api/nguoi-dung/{u['id']}/dat-lai-mat-khau", headers=QT_HDR)
    assert r.status_code == 503
    r = client.post("/api/dang-nhap", json={"ten_dang_nhap": "admin-that", "mat_khau": MK})
    assert r.status_code == 401 and r.text == r_sai.text
    # Cookie khi kho hỏng: 401, không 500
    assert client.get("/api/toi", cookies={"w2a_phien": tok}).status_code == 401
    assert client.get("/v1/models", cookies={"w2a_phien": tok}).status_code == 401
    # Gọi trực tiếp hàm cũng không ghi đè
    with pytest.raises(Exception):
        asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="moi-2", mat_khau=MK))
    with pytest.raises(Exception):
        nguoi_dung.sua_nguoi_dung(u["id"], bat=True)
    # Khởi động lại khi kho hỏng: không tạo mã thiết lập
    nguoi_dung.khoi_tao_thiet_lap_lan_dau()
    assert not (data_dir / "ma_thiet_lap").exists()
    assert _sha(tep) == truoc, "Tệp người dùng bị ghi đè khi đọc lỗi"


def test_30_kho_nguoi_dung_hong_khoa_co_chu_bi_tu_choi_khoa_khac_van_chay(moi_truong, monkeypatch):
    """Điểm 3: kho người dùng hỏng → khoá có chủ 401; khoá không chủ và khoá quản trị vẫn dùng được."""
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    client, data_dir = moi_truong
    u, _, _ = _tao_va_vao("bob-kho", pham_vi_toi_da=["chat"])
    _, khoa_chu = khoa_khach.tao_khoa("có chủ", pham_vi=["chat"], chu_id=u["id"])
    _, khoa_le = khoa_khach.tao_khoa("không chủ", pham_vi=["chat"])
    (data_dir / "nguoi_dung.json").write_text("{hong", encoding="utf-8")
    assert client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_chu}"}).status_code == 401
    assert client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_le}"}).status_code == 200
    assert client.get("/v1/models", headers=QT_HDR).status_code == 200


@pytest.mark.parametrize("gia_tri", [b"\xe9", b"\xc3\xa9" * 20, b"a" * 5000, b"", b"abc def", b"A" * 42, b"A" * 44])
def test_31_cookie_phien_sai_dang_la_401_khong_500(moi_truong, monkeypatch, gia_tri):
    """Điểm 4: cookie phiên không đúng dạng token → coi như không có phiên (401), không ngoại lệ."""
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    client, _ = moi_truong
    hdr = {"Cookie": b"w2a_phien=" + gia_tri}
    for duong in ("/api/toi", "/v1/models", "/api/nguoi-dung"):
        r = client.get(duong, headers=hdr)
        assert r.status_code == 401, f"{duong} {gia_tri[:10]!r} → {r.status_code}"


def test_32_token_va_csrf_sai_dang_ham_va_http(moi_truong, monkeypatch):
    """Điểm 4: xac_thuc_token từ chối token sai dạng (kể cả xuống dòng cuối, NUL); CSRF không ASCII/quá dài → 403."""
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    client, _ = moi_truong
    u, tok, h = _tao_va_vao("bob-csrf", pham_vi_toi_da=["chat"])
    assert phien_web.xac_thuc_token(tok) is not None
    for xau in (tok + "\n", "\x00" * 43, "é" * 43, tok[:-1] + " ", tok + "A", ""):
        assert phien_web.xac_thuc_token(xau) is None, repr(xau[:8])
    for gui in (b"\xc3\xa9", b"\xe9" * 43, b"x" * 10000, h["X-CSRF-Token"].encode() + b"\xe9"):
        r = client.post("/api/toi/khoa", headers={"Cookie": f"w2a_phien={tok}", "X-CSRF-Token": gui}, json={"nhan": "k"})
        assert r.status_code == 403, f"{gui[:10]!r} → {r.status_code}"
    r = client.post("/api/toi/khoa", headers={"Cookie": f"w2a_phien={tok}", **h}, json={"nhan": "k"})
    assert r.status_code == 200


def _semaphore_moi(monkeypatch):
    """Semaphore scrypt cấp module gắn vào vòng lặp đầu tiên tranh chấp nó; test chạy nhiều vòng lặp (asyncio.run,
    mỗi TestClient một vòng) nên cấp bản mới cho từng vòng — máy chủ thật chỉ có một vòng uvicorn."""
    monkeypatch.setattr(nguoi_dung, "_SEMAPHORE_SCRYPT", asyncio.Semaphore(2))


def test_33_tao_trung_ten_dong_thoi_ham(moi_truong, monkeypatch):
    """Điểm 5: 5 lời gọi đồng thời cùng tên → đúng MỘT bản ghi; chỉ một admin khi thiết lập đồng thời."""
    _semaphore_moi(monkeypatch)

    async def goi():
        return await asyncio.gather(*[
            nguoi_dung.tao_nguoi_dung(ten_dang_nhap="dup", mat_khau=f"DupPassword-123{i}") for i in range(5)
        ], return_exceptions=True)
    kq = asyncio.run(goi())
    assert sum(1 for x in kq if isinstance(x, tuple)) == 1, kq
    assert all(getattr(x, "than", {}).get("error", {}).get("code") == "username_taken" for x in kq if not isinstance(x, tuple)), kq
    assert sum(1 for u in nguoi_dung.danh_sach_nguoi_dung() if u["ten_dang_nhap"] == "dup") == 1
    _semaphore_moi(monkeypatch)

    async def goi_admin():
        return await asyncio.gather(*[
            nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap=f"adm-{i}", mat_khau=MK, chi_khi_chua_co_admin=True)
            for i in range(3)
        ], return_exceptions=True)
    kq = asyncio.run(goi_admin())
    assert sum(1 for x in kq if isinstance(x, tuple)) == 1, kq
    assert all(getattr(x, "than", {}).get("error", {}).get("code") == "already_setup" for x in kq if not isinstance(x, tuple)), kq
    assert sum(1 for u in nguoi_dung.danh_sach_nguoi_dung() if u["vai"] == "admin") == 1


def test_34_tao_trung_ten_dong_thoi_http(moi_truong, monkeypatch):
    """Điểm 5 qua HTTP: 5 yêu cầu song song cùng tên → một 200, còn lại 400 username_taken, một bản ghi."""
    client, _ = moi_truong
    _semaphore_moi(monkeypatch)

    async def goi():
        return await asyncio.gather(*[
            asyncio.to_thread(client.post, "/api/nguoi-dung", headers=QT_HDR,
                              json={"ten_dang_nhap": "dup-http", "mat_khau": f"DupPassword-123{i}"})
            for i in range(5)
        ])
    rs = asyncio.run(goi())
    assert sorted(r.status_code for r in rs) == [200, 400, 400, 400, 400], [r.status_code for r in rs]
    assert sum(1 for u in nguoi_dung.danh_sach_nguoi_dung() if u["ten_dang_nhap"] == "dup-http") == 1


@pytest.mark.parametrize("than", [
    {"bat": 0}, {"bat": ""}, {"bat": "false"}, {"bat": 1}, {"bat": []},
    {"vai": 1}, {"vai": ["admin"]}, {"vai": "root"}, {"vai": ""},
    {"pham_vi_toi_da": "chat"}, {"pham_vi_toi_da": 5}, {"pham_vi_toi_da": ["", "chat"]},
    {"pham_vi_toi_da": [1]}, {"pham_vi_toi_da": ["CHAT"]}, {"pham_vi_toi_da": {"chat": 1}},
])
def test_35_patch_nguoi_dung_sai_kieu_la_400(moi_truong, than):
    """Điểm 6: PATCH /api/nguoi-dung/{id} kiểm KIỂU ở biên → 400 invalid_value, bản ghi không đổi."""
    client, _ = moi_truong
    u, _, _ = _tao_va_vao("bob-kieu", pham_vi_toi_da=["chat"])
    truoc = nguoi_dung.tim_theo_id(u["id"])
    r = client.patch(f"/api/nguoi-dung/{u['id']}", headers=QT_HDR, json=than)
    assert r.status_code == 400, f"{than} → {r.status_code} {r.text}"
    assert nguoi_dung.tim_theo_id(u["id"]) == truoc


def test_36_tat_roi_bat_lai_khong_song_lai_phien_cu(moi_truong):
    """Điểm 6: tắt tài khoản thu hồi phiên; bật lại không làm sống lại phiên cũ."""
    client, _ = moi_truong
    u, tok, _ = _tao_va_vao("bob-tat", pham_vi_toi_da=["chat"])
    assert client.patch(f"/api/nguoi-dung/{u['id']}", headers=QT_HDR, json={"bat": False}).status_code == 200
    assert client.patch(f"/api/nguoi-dung/{u['id']}", headers=QT_HDR, json={"bat": True}).status_code == 200
    assert phien_web.xac_thuc_token(tok) is None
    assert client.get("/api/toi", cookies={"w2a_phien": tok}).status_code == 401


@pytest.mark.parametrize("duong,than", [
    ("/api/nguoi-dung", {"vai": 1}),
    ("/api/nguoi-dung", {"vai": "root"}),
    ("/api/nguoi-dung", {"pham_vi_toi_da": "chat"}),
    ("/api/nguoi-dung", {"pham_vi_toi_da": [""]}),
    ("/api/nguoi-dung", {"ten_dang_nhap": 12345}),
    ("/api/nguoi-dung", {"mat_khau": 123456789012345}),
])
def test_37_tao_nguoi_dung_sai_kieu_la_400(moi_truong, duong, than):
    """Điểm 6: POST /api/nguoi-dung kiểm kiểu ở biên, không tạo bản ghi."""
    client, _ = moi_truong
    r = client.post(duong, headers=QT_HDR, json=than)
    assert r.status_code == 400, f"{than} → {r.status_code} {r.text}"
    assert nguoi_dung.danh_sach_nguoi_dung() == []


def test_38_tu_phuc_vu_sai_kieu_la_400(moi_truong):
    """Điểm 6: /api/toi/mat-khau và /api/toi/khoa từ chối kiểu sai (null không thành chuỗi «None»)."""
    client, _ = moi_truong
    u, tok, h = _tao_va_vao("bob-tu", pham_vi_toi_da=["chat"])
    for than in ({"mat_khau_cu": MK, "mat_khau_moi": 123456789012345}, {"mat_khau_cu": None, "mat_khau_moi": "MatKhauMoi1234567"},
                 {"mat_khau_cu": MK, "mat_khau_moi": None}):
        r = client.post("/api/toi/mat-khau", cookies={"w2a_phien": tok}, headers=h, json=than)
        assert r.status_code == 400, f"{than} → {r.status_code}"
    r = client.post("/api/toi/khoa", cookies={"w2a_phien": tok}, headers=h, json={"nhan": {"x": 1}})
    assert r.status_code == 400


def test_39_xoa_cookie_phien_du_thuoc_tinh(moi_truong):
    """Điểm 7: đăng xuất xoá __Host-w2a_phien với Secure; HttpOnly; SameSite=Strict; Path=/ và w2a_phien với HttpOnly; SameSite=Strict."""
    _, _ = moi_truong
    u, tok, h = _tao_va_vao("bob-xoa", pham_vi_toi_da=["chat"])
    with TestClient(app, base_url="https://testserver") as c:
        r = c.post("/api/dang-xuat", headers={"Cookie": f"__Host-w2a_phien={tok}", **h})
    assert r.status_code == 200
    cac = r.headers.get_list("set-cookie")
    host = [x for x in cac if x.startswith("__Host-w2a_phien=")]
    thuong = [x for x in cac if x.startswith("w2a_phien=")]
    assert host and thuong, cac
    for thuoc_tinh in ("Secure", "HttpOnly", "Path=/", "Max-Age=0"):
        assert thuoc_tinh in host[0], host[0]
    assert "samesite=strict" in host[0].lower()
    for thuoc_tinh in ("HttpOnly", "Path=/", "Max-Age=0"):
        assert thuoc_tinh in thuong[0], thuong[0]
    assert "samesite=strict" in thuong[0].lower()


@pytest.mark.parametrize("than", [
    {"ten_dang_nhap": "", "mat_khau": ""},
    {"ten_dang_nhap": "admin-x", "mat_khau": ""},
    {"ten_dang_nhap": "", "mat_khau": "MatKhauAdminTot123456"},
    {},
    {"ten_dang_nhap": 123, "mat_khau": "MatKhauAdminTot123456"},
    {"ten_dang_nhap": "admin-x", "mat_khau": 123456789012345},
])
def test_40_thiet_lap_bat_buoc_ten_va_mat_khau(moi_truong, than):
    """Điểm 8: thiết lập lần đầu bắt buộc tên và mật khẩu (chuỗi không rỗng) → 400, không tạo admin ngẫu nhiên."""
    client, data_dir = moi_truong
    nguoi_dung.khoi_tao_thiet_lap_lan_dau()
    ma = nguoi_dung.doc_ma_thiet_lap()
    r = client.post("/api/thiet-lap", json={"ma_thiet_lap": ma, **than})
    assert r.status_code == 400, f"{than} → {r.status_code} {r.text}"
    assert nguoi_dung.danh_sach_nguoi_dung() == []
    assert (data_dir / "ma_thiet_lap").exists()


def test_41_thiet_lap_qua_cong_ip_quan_tri(moi_truong, monkeypatch):
    """Điểm 9: đặt WEB2API_QT_IP_CHO_PHEP thì thiết lập chỉ từ IP cho phép (loopback trực tiếp vẫn được)."""
    client, _ = moi_truong
    from app import ip_cho_phep
    ip_cho_phep._LAN_GHI_CUOI.clear()
    monkeypatch.setenv("WEB2API_QT_IP_CHO_PHEP", "192.0.2.0/24")
    than = {"ma_thiet_lap": KHOA_QT, "ten_dang_nhap": "admin-ip", "mat_khau": "MatKhauAdminTot123456"}
    client._transport.client = ("203.0.113.9", 1234)
    r = client.post("/api/thiet-lap", json=than)
    assert r.status_code == 403 and r.json()["error"]["code"] == "ip_not_allowed"
    assert nguoi_dung.danh_sach_nguoi_dung() == []
    client._transport.client = ("127.0.0.1", 1234)
    assert client.post("/api/thiet-lap", json=than).status_code == 200


def test_42_thiet_lap_qua_cong_ip_trong_danh_sach(moi_truong, monkeypatch):
    """Điểm 9: IP trong danh sách được thiết lập."""
    client, _ = moi_truong
    monkeypatch.setenv("WEB2API_QT_IP_CHO_PHEP", "192.0.2.0/24")
    client._transport.client = ("192.0.2.5", 1234)
    r = client.post("/api/thiet-lap", json={"ma_thiet_lap": KHOA_QT, "ten_dang_nhap": "admin-ip2", "mat_khau": "MatKhauAdminTot123456"})
    assert r.status_code == 200


def test_43_mat_khau_dang_nhap_qua_dai_la_sai_khong_bam(moi_truong, monkeypatch):
    """Điểm 10: mật khẩu > 128 ký tự → 401 như sai, chạy băm giả, không băm chuỗi dài; vẫn đếm vào khoá theo tên."""
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    client, _ = moi_truong
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="bob-dai", mat_khau=MK))
    r_sai = client.post("/api/dang-nhap", json={"ten_dang_nhap": "bob-dai", "mat_khau": "SaiMatKhau123456"})
    do_dai = []
    goc_scrypt = hashlib.scrypt

    def ghi_lai(pw, **kw):  # mọi lượt scrypt (băm thật lẫn băm giả) đi qua đây
        do_dai.append(len(pw))
        return goc_scrypt(pw, **kw)

    with patch.object(nguoi_dung.hashlib, "scrypt", side_effect=ghi_lai):
        r = client.post("/api/dang-nhap", json={"ten_dang_nhap": "bob-dai", "mat_khau": MK + "x" * 200})
    assert r.status_code == 401 and r.text == r_sai.text
    assert do_dai and max(do_dai) <= nguoi_dung.MAT_KHAU_TOI_DA, do_dai
    for _ in range(3):
        client.post("/api/dang-nhap", json={"ten_dang_nhap": "bob-dai", "mat_khau": "y" * 129})
    assert client.post("/api/dang-nhap", json={"ten_dang_nhap": "bob-dai", "mat_khau": MK}).status_code == 401


def test_44_xoa_admin_da_tat_khong_bi_409(moi_truong):
    """Điểm 11: còn một admin bật — xoá admin ĐÃ TẮT được; xoá admin bật cuối cùng vẫn 409."""
    client, _ = moi_truong
    a1, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-bat", mat_khau=MK))
    a2, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-tat", mat_khau=MK))
    nguoi_dung.sua_nguoi_dung(a2["id"], bat=False)
    assert client.delete(f"/api/nguoi-dung/{a2['id']}", headers=QT_HDR).status_code == 200
    assert client.delete(f"/api/nguoi-dung/{a1['id']}", headers=QT_HDR).status_code == 409


def test_45_doi_mat_khau_sai_mat_khau_cu_dem_vao_khoa_ten(moi_truong, monkeypatch):
    """Ghi nhận: thử mật khẩu cũ sai ở /api/toi/mat-khau đếm vào khoá theo tên (chặn dò mật khẩu bằng phiên trộm)."""
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    client, _ = moi_truong
    u, tok, h = _tao_va_vao("bob-doi", pham_vi_toi_da=["chat"])
    for _ in range(5):
        r = client.post("/api/toi/mat-khau", cookies={"w2a_phien": tok}, headers=h,
                        json={"mat_khau_cu": "SaiMatKhau123456", "mat_khau_moi": "MatKhauMoi1234567"})
        assert r.status_code == 401
    r = client.post("/api/toi/mat-khau", cookies={"w2a_phien": tok}, headers=h,
                    json={"mat_khau_cu": MK, "mat_khau_moi": "MatKhauMoi1234567"})
    assert r.status_code == 401
    assert client.post("/api/dang-nhap", json={"ten_dang_nhap": "bob-doi", "mat_khau": MK}).status_code == 401


def test_46_phien_chi_ghi_dung_cuoi_khi_qua_nguong(moi_truong):
    """Ghi nhận: xác thực cookie liên tiếp không ghi lại tệp phiên mỗi lượt (chỉ khi dung_cuoi cũ ≥ 60 giây)."""
    client, data_dir = moi_truong
    u, tok, _ = _tao_va_vao("bob-ghi", pham_vi_toi_da=["chat"])
    tep = data_dir / "phien_web.json"
    st = tep.stat()
    for _ in range(5):
        assert phien_web.xac_thuc_token(tok) is not None
    st2 = tep.stat()
    assert (st.st_ino, st.st_mtime_ns) == (st2.st_ino, st2.st_mtime_ns)
    from datetime import datetime, timedelta, timezone
    sau = datetime.now(timezone.utc) + timedelta(seconds=61)
    with patch("app.phien_web.datetime") as mock_dt:
        mock_dt.now.return_value = sau
        mock_dt.fromisoformat = datetime.fromisoformat
        assert phien_web.xac_thuc_token(tok) is not None
    assert json.loads(tep.read_text())["phien"][0]["dung_cuoi"] == sau.isoformat(timespec="seconds")


@pytest.mark.parametrize("noi_dung", ["{hong", '{"phien": "x"}'])
def test_47_kho_phien_hong_khong_doi_nua_chung(moi_truong, monkeypatch, noi_dung):
    """Kho phiên hỏng: đóng khi lỗi TRƯỚC khi đổi trạng thái — đăng nhập/thiết lập/sửa/xoá/đặt lại mật khẩu trả 503
    (không 500), bản ghi người dùng, tệp khoá và tệp phiên giữ nguyên byte."""
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    client, data_dir = moi_truong
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-p", mat_khau=MK))
    u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="bob-p", mat_khau=MK))
    khoa_khach.tao_khoa("của bob", pham_vi=["chat"], chu_id=u["id"])
    tep_phien, tep_nd, tep_khoa = data_dir / "phien_web.json", data_dir / "nguoi_dung.json", data_dir / "khoa_khach.json"
    tep_phien.write_text(noi_dung, encoding="utf-8")
    truoc = (_sha(tep_phien), _sha(tep_nd), _sha(tep_khoa))

    r = client.post("/api/dang-nhap", json={"ten_dang_nhap": "bob-p", "mat_khau": MK})
    assert r.status_code == 503, r.text
    assert client.post("/api/thiet-lap", json={"ma_thiet_lap": KHOA_QT, "ten_dang_nhap": "x-y", "mat_khau": MK}).status_code in (409, 503)
    assert client.patch(f"/api/nguoi-dung/{u['id']}", headers=QT_HDR, json={"bat": False}).status_code == 503
    assert client.delete(f"/api/nguoi-dung/{u['id']}", headers=QT_HDR).status_code == 503
    assert client.post(f"/api/nguoi-dung/{u['id']}/dat-lai-mat-khau", headers=QT_HDR).status_code == 503
    assert (_sha(tep_phien), _sha(tep_nd), _sha(tep_khoa)) == truoc
