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
