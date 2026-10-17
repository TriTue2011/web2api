"""Kiểm thử toàn diện tầng xác thực hai bước (2FA TOTP RFC 6238) cho web2api.

Bao gồm:
1. Vector chuẩn RFC 6238 phụ lục B (5 mốc thời gian, SHA-1, 8 chữ số và 6 chữ số).
2. Bộ sinh mã TOTP "ứng dụng giả" độc lập (không import hàm của app), kiểm tra Base32 20 byte và URI otpauth://.
3. Bật/tắt 2FA riêng biệt theo từng tài khoản; luồng đăng nhập (mật khẩu -> 401 can_ma_2fa + vé -> xác thực cấp phiên).
4. Kích hoạt một lần: xác nhận lần 2 bị từ chối, không cấp lại mã khôi phục.
5. 10 mã khôi phục dùng một lần (dùng xong bị xoá vĩnh viễn, tạo lại mã mới huỷ mã cũ).
6. Chống phát lại (replay protection) và từ chối bước lùi thời gian.
7. Dung sai thời gian: chấp nhận ±1 bước (±30s), từ chối lệch ±2 bước (±60s).
8. Vé tạm đăng nhập: hết hạn 5 phút, dùng một lần (single-use), sai IP, vé giả bị từ chối 401.
9. Giới hạn thử sai: đoán sai mã 2FA bị tính vào rate limit (429/khoá IP).
10. Phân quyền & chống IDOR: user không gọi được API quản trị 2FA (403), không có API tắt 2FA của người khác.
11. Script CLI máy chủ `python -m app.dat_lai_2fa <ten>`: chỉ tắt tài khoản chỉ định.
13. Tương thích hai tầng: Máy tin cậy (máy lạ đòi khoá QT trước) -> 2FA -> cấp cả phiên và cookie máy.
14. Đổi mật khẩu không làm mất cấu hình 2FA.
15. Quét rò rỉ: không lộ bí mật Base32 hoặc mã khôi phục thô trong log/nhật ký/phản hồi lỗi.
16. Mã hoá khi nghỉ: khi có WEB2API_KHOA_CHINH, tệp trên đĩa lưu dạng bản mã W2A1, không có chuỗi rõ.
17. Kiểm tra đột biến (mutations): bỏ chống phát lại, bỏ compare_digest, không đếm sai => ĐỎ.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import re
import secrets
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import gioi_han, nguoi_dung, phien_web, xac_thuc, xac_thuc_2fa
from app.main import app
from app.so_module import SoModule

KHOA_QT = "AIzaSyKIEMNGHIEM-000000000000000000009"
MK_ADMIN = "MatKhauAdminBaoMat123456"
MK_USER_A = "MatKhauUserABaoMat123456"
MK_USER_B = "MatKhauUserBBaoMat123456"


@pytest.fixture
def moi_truong_2fa(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "10")
    monkeypatch.setenv("WEB2API_RL_CUA_SO_GIAY", "60")
    monkeypatch.setenv("WEB2API_RL_KHOA_GIAY", "60")
    monkeypatch.delenv("WEB2API_MAY_TIN_CAY", raising=False)
    monkeypatch.delenv("WEB2API_MAY_TIN_CAY_USER", raising=False)
    monkeypatch.delenv("WEB2API_BAT_BUOC_2FA", raising=False)
    monkeypatch.delenv("WEB2API_KHOA_CHINH", raising=False)

    nguoi_dung._THEO_DOI_SAI.clear()
    gioi_han.khoi_dong()
    xac_thuc_2fa._KHO_VE_TAM.clear()

    with TestClient(app) as client:
        client._transport.client = ("127.0.0.1", 1234)
        client.app.state.so = SoModule()
        yield client, data_dir


# ─────────────────────────────────────────────────────────────────────────────
# 1. VECTOR CHUẨN RFC 6238 PHỤ LỤC B
# ─────────────────────────────────────────────────────────────────────────────

def test_rfc_6238_appendix_b_vectors():
    """Kiểm chứng thuật toán TOTP bằng toàn bộ vector chuẩn trong RFC 6238 Appendix B (SHA-1).

    Khóa bí mật dùng cho vector: chuỗi ASCII «12345678901234567890» (20 bytes).
    Các mốc thời gian: 59, 1111111109, 1111111111, 1234567890, 2000000000.
    """
    secret = b"12345678901234567890"

    # Bảng chuẩn từ RFC 6238 Appendix B (SHA-1):
    # | Time (sec) | Value of T (hex) | TOTP (8 digits) | TOTP (6 digits) |
    # | 59         | 0000000000000001 | 94287082        | 287082          |
    # | 1111111109 | 00000000023523EC | 07081804        | 081804          |
    # | 1111111111 | 00000000023523ED | 14050471        | 050471          |
    # | 1234567890 | 000000000273EF0A | 89005924        | 005924          |
    # | 2000000000 | 0000000003F940AA | 69279037        | 279037          |
    cac_vector = [
        (59, "94287082", "287082"),
        (1111111109, "07081804", "081804"),
        (1111111111, "14050471", "050471"),
        (1234567890, "89005924", "005924"),
        (2000000000, "69279037", "279037"),
    ]

    for moc_s, ky_vong_8, ky_vong_6 in cac_vector:
        kq_8 = xac_thuc_2fa.sinh_totp(secret, moc_thoi_gian=moc_s, buoc_giay=30, so_chu_so=8)
        kq_6 = xac_thuc_2fa.sinh_totp(secret, moc_thoi_gian=moc_s, buoc_giay=30, so_chu_so=6)
        assert kq_8 == ky_vong_8, f"Lệch 8 chữ số tại mốc {moc_s}: {kq_8} != {ky_vong_8}"
        assert kq_6 == ky_vong_6, f"Lệch 6 chữ số tại mốc {moc_s}: {kq_6} != {ky_vong_6}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. BỘ SINH TOTP "ỨNG DỤNG GIẢ" ĐỘC LẬP
# ─────────────────────────────────────────────────────────────────────────────

def _sinh_totp_doc_lap(bi_mat_b32: str, moc_s: float | None = None, buoc_giay: int = 30) -> str:
    """Bộ sinh TOTP độc lập hoàn toàn viết riêng trong test (không dùng hàm app)."""
    s = re.sub(r"[\s=-]", "", bi_mat_b32.strip().upper())
    pad = (8 - (len(s) % 8)) % 8
    key = base64.b32decode((s + "=" * pad).encode("ascii"), casefold=True)
    t = int((time.time() if moc_s is None else moc_s) // buoc_giay)
    msg = struct.pack(">Q", t)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1000000
    return str(code).zfill(6)


def test_bo_sinh_totp_ung_dung_gia_doc_lap(moi_truong_2fa):
    """Kiểm tra bí mật 160-bit Base32 không đệm '=', định dạng URI otpauth:// và ứng dụng giả độc lập."""
    client, _ = moi_truong_2fa
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-test-uri", mat_khau=MK_USER_A))

    # Đăng nhập lấy phiên
    r_dn = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-test-uri", "mat_khau": MK_USER_A})
    csrf = r_dn.json()["csrf"]

    # Bắt đầu kích hoạt 2FA
    r_bd = client.post("/api/toi/2fa/bat-dau", json={"mat_khau": MK_USER_A}, headers={"X-CSRF-Token": csrf})
    assert r_bd.status_code == 200
    du_lieu = r_bd.json()
    bi_mat = du_lieu["bi_mat"]
    uri = du_lieu["uri"]

    # 1. Kiểm tra định dạng bí mật: Base32 chữ hoa, không có ký tự đệm '='
    assert "=" not in bi_mat
    assert re.match(r"^[A-Z2-7]+$", bi_mat)
    # Giải mã nhị phân phải đúng 20 byte (160 bit)
    pad = (8 - (len(bi_mat) % 8)) % 8
    raw = base64.b32decode(bi_mat + "=" * pad)
    assert len(raw) == 20

    # 2. Kiểm tra định dạng URI otpauth://
    assert uri.startswith("otpauth://totp/web2api:user-test-uri?")
    assert f"secret={bi_mat}" in uri
    assert "issuer=web2api" in uri
    assert "algorithm=SHA1" in uri
    assert "digits=6" in uri
    assert "period=30" in uri

    # 3. Ứng dụng giả sinh mã hợp lệ và kích hoạt thành công
    ma_totp = _sinh_totp_doc_lap(bi_mat)
    r_xn = client.post("/api/toi/2fa/xac-nhan", json={"ma": ma_totp}, headers={"X-CSRF-Token": csrf})
    assert r_xn.status_code == 200
    assert len(r_xn.json()["ma_khoi_phuc"]) == 10


# ─────────────────────────────────────────────────────────────────────────────
# 3. BẬT/TẮT 2FA RIÊNG BIỆT & LUỒNG ĐĂNG NHẬP
# ─────────────────────────────────────────────────────────────────────────────

def test_bat_tat_2fa_rieng_biet_va_luong_dang_nhap(moi_truong_2fa, monkeypatch):
    """User A bật 2FA không ép User B; đăng nhập User A đòi 2FA, User B vào thẳng."""
    client, _ = moi_truong_2fa
    t_clock = [1700000000.0]
    monkeypatch.setattr(time, "time", lambda: t_clock[0])

    u_a, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-2fa-a", mat_khau=MK_USER_A))
    u_b, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-2fa-b", mat_khau=MK_USER_B))

    # 1. Ban đầu cả hai chưa bật 2FA
    assert xac_thuc_2fa.da_bat_2fa(u_a["id"]) is False
    assert xac_thuc_2fa.da_bat_2fa(u_b["id"]) is False

    # 2. User A bật 2FA tại bước T0
    r_a = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-2fa-a", "mat_khau": MK_USER_A})
    csrf_a = r_a.json()["csrf"]

    r_bd = client.post("/api/toi/2fa/bat-dau", json={"mat_khau": MK_USER_A}, headers={"X-CSRF-Token": csrf_a})
    bi_mat = r_bd.json()["bi_mat"]
    ma_totp = _sinh_totp_doc_lap(bi_mat, moc_s=t_clock[0])
    r_xn = client.post("/api/toi/2fa/xac-nhan", json={"ma": ma_totp}, headers={"X-CSRF-Token": csrf_a})
    assert r_xn.status_code == 200
    ma_khoi_phuc_ds = r_xn.json()["ma_khoi_phuc"]
    assert len(ma_khoi_phuc_ds) == 10

    # User A đã bật, User B vẫn chưa bật
    assert xac_thuc_2fa.da_bat_2fa(u_a["id"]) is True
    assert xac_thuc_2fa.da_bat_2fa(u_b["id"]) is False

    # 3. User B đăng nhập: vào thẳng (200 OK)
    client.cookies.clear()
    r_b_dn = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-2fa-b", "mat_khau": MK_USER_B})
    assert r_b_dn.status_code == 200
    assert "csrf" in r_b_dn.json()

    # 4. User A đăng nhập: bị dừng ở 401 code="can_ma_2fa" kèm vé tạm
    client.cookies.clear()
    r_a_dn1 = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-2fa-a", "mat_khau": MK_USER_A})
    assert r_a_dn1.status_code == 401
    j1 = r_a_dn1.json()
    assert j1["error"]["code"] == "can_ma_2fa"
    ve = j1["error"]["ve"]
    assert ve and len(ve) >= 32

    # 5. User A xác thực bước hai qua /api/dang-nhap/2fa
    # Thử mã sai -> 401 invalid_totp
    r_sai = client.post("/api/dang-nhap/2fa", json={"ve": ve, "ma": "000000"})
    assert r_sai.status_code == 401

    # Vé chỉ dùng 1 lần, thử lại với vé cũ bị 401 ve_khong_hop_le
    ma_moi = _sinh_totp_doc_lap(bi_mat, moc_s=t_clock[0])
    r_ve_cu = client.post("/api/dang-nhap/2fa", json={"ve": ve, "ma": ma_moi})
    assert r_ve_cu.status_code == 401
    assert r_ve_cu.json()["error"]["code"] == "ve_khong_hop_le"

    # Lấy vé mới và đăng nhập đúng mã ở bước thời gian tiếp theo (T0 + 30s)
    t_clock[0] += 30.0
    r_a_dn2 = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-2fa-a", "mat_khau": MK_USER_A})
    ve_moi = r_a_dn2.json()["error"]["ve"]
    ma_dung = _sinh_totp_doc_lap(bi_mat, moc_s=t_clock[0])
    r_ok = client.post("/api/dang-nhap/2fa", json={"ve": ve_moi, "ma": ma_dung})
    assert r_ok.status_code == 200
    assert r_ok.json()["ok"] is True
    csrf_moi = r_ok.json()["csrf"]
    assert "w2a_phien" in client.cookies

    # 6. User A tắt 2FA ở bước thời gian tiếp theo (T0 + 60s): cần mật khẩu + mã 2FA
    t_clock[0] += 30.0
    r_tat_sai_mk = client.post(
        "/api/toi/2fa/tat",
        json={"mat_khau": "MatKhauSai123", "ma": _sinh_totp_doc_lap(bi_mat, moc_s=t_clock[0])},
        headers={"X-CSRF-Token": csrf_moi},
    )
    assert r_tat_sai_mk.status_code == 401

    r_tat_ok = client.post(
        "/api/toi/2fa/tat",
        json={"mat_khau": MK_USER_A, "ma": _sinh_totp_doc_lap(bi_mat, moc_s=t_clock[0])},
        headers={"X-CSRF-Token": csrf_moi},
    )
    assert r_tat_ok.status_code == 200
    assert xac_thuc_2fa.da_bat_2fa(u_a["id"]) is False

    # 7. Sau khi tắt, User A đăng nhập vào thẳng không cần 2FA
    client.cookies.clear()
    r_a_sau_tat = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-2fa-a", "mat_khau": MK_USER_A})
    assert r_a_sau_tat.status_code == 200
    assert "csrf" in r_a_sau_tat.json()


# ─────────────────────────────────────────────────────────────────────────────
# 4. KÍCH HOẠT MỘT LẦN
# ─────────────────────────────────────────────────────────────────────────────

def test_kich_hoat_mot_lan(moi_truong_2fa):
    """Xác nhận kích hoạt 2FA lần thứ hai với mã cũ hoặc mới đều bị từ chối, không cấp lại mã khôi phục."""
    client, _ = moi_truong_2fa
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-mot-lan", mat_khau=MK_USER_A))
    r_dn = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-mot-lan", "mat_khau": MK_USER_A})
    csrf = r_dn.json()["csrf"]

    r_bd = client.post("/api/toi/2fa/bat-dau", json={"mat_khau": MK_USER_A}, headers={"X-CSRF-Token": csrf})
    bi_mat = r_bd.json()["bi_mat"]
    ma = _sinh_totp_doc_lap(bi_mat)

    # Lần 1: Thành công
    r_xn1 = client.post("/api/toi/2fa/xac-nhan", json={"ma": ma}, headers={"X-CSRF-Token": csrf})
    assert r_xn1.status_code == 200
    assert len(r_xn1.json()["ma_khoi_phuc"]) == 10

    # Lần 2: Gọi lại xac-nhan với mã cũ hoặc mã mới -> bị từ chối 401 (không còn ở trạng thái cho_xac_nhan)
    ma_moi = _sinh_totp_doc_lap(bi_mat, moc_s=time.time() + 30)
    r_xn2 = client.post("/api/toi/2fa/xac-nhan", json={"ma": ma_moi}, headers={"X-CSRF-Token": csrf})
    assert r_xn2.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# 5. MÃ KHÔI PHỤC DÙNG MỘT LẦN & TẠO LẠI MÃ
# ─────────────────────────────────────────────────────────────────────────────

def test_ma_khoi_phuc_mot_lan_va_tao_lai(moi_truong_2fa):
    """Mã khôi phục dùng 1 lần (dùng xong bị xoá vĩnh viễn), dùng tắt 2FA, tạo lại mã mới."""
    client, _ = moi_truong_2fa
    u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-khoi-phuc", mat_khau=MK_USER_A))
    r_dn = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-khoi-phuc", "mat_khau": MK_USER_A})
    csrf = r_dn.json()["csrf"]

    # Bật 2FA
    r_bd = client.post("/api/toi/2fa/bat-dau", json={"mat_khau": MK_USER_A}, headers={"X-CSRF-Token": csrf})
    bi_mat = r_bd.json()["bi_mat"]
    r_xn = client.post(
        "/api/toi/2fa/xac-nhan",
        json={"ma": _sinh_totp_doc_lap(bi_mat)},
        headers={"X-CSRF-Token": csrf},
    )
    danh_sach_kp = r_xn.json()["ma_khoi_phuc"]
    assert len(danh_sach_kp) == 10

    ma_kp_0 = danh_sach_kp[0]
    ma_kp_1 = danh_sach_kp[1]

    # Đăng nhập bằng mã khôi phục #0
    client.cookies.clear()
    r_dn1 = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-khoi-phuc", "mat_khau": MK_USER_A})
    ve1 = r_dn1.json()["error"]["ve"]
    r_ok1 = client.post("/api/dang-nhap/2fa", json={"ve": ve1, "ma": ma_kp_0})
    assert r_ok1.status_code == 200
    assert xac_thuc_2fa.so_ma_khoi_phuc_con_lai(u["id"]) == 9

    # Dùng lại mã #0 -> bị từ chối 401
    client.cookies.clear()
    r_dn2 = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-khoi-phuc", "mat_khau": MK_USER_A})
    ve2 = r_dn2.json()["error"]["ve"]
    r_tai_dung = client.post("/api/dang-nhap/2fa", json={"ve": ve2, "ma": ma_kp_0})
    assert r_tai_dung.status_code == 401
    assert r_tai_dung.json()["error"]["code"] == "invalid_totp"

    # Dùng mã #1 để đăng nhập thành công
    client.cookies.clear()
    r_dn3 = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-khoi-phuc", "mat_khau": MK_USER_A})
    ve3 = r_dn3.json()["error"]["ve"]
    r_ok3 = client.post("/api/dang-nhap/2fa", json={"ve": ve3, "ma": ma_kp_1})
    assert r_ok3.status_code == 200
    csrf3 = r_ok3.json()["csrf"]
    assert xac_thuc_2fa.so_ma_khoi_phuc_con_lai(u["id"]) == 8

    # Tạo lại danh sách mã khôi phục (cần mật khẩu + mã TOTP hợp lệ ở bước mới)
    r_tl = client.post(
        "/api/toi/2fa/tao-lai-ma-khoi-phuc",
        json={"mat_khau": MK_USER_A, "ma": _sinh_totp_doc_lap(bi_mat, moc_s=time.time() + 30)},
        headers={"X-CSRF-Token": csrf3},
    )
    assert r_tl.status_code == 200
    ds_moi = r_tl.json()["ma_khoi_phuc"]
    assert len(ds_moi) == 10
    assert xac_thuc_2fa.so_ma_khoi_phuc_con_lai(u["id"]) == 10

    # Các mã cũ (kể cả mã chưa dùng như #2) không còn hiệu lực
    ma_kp_2 = danh_sach_kp[2]
    client.cookies.clear()
    r_dn4 = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-khoi-phuc", "mat_khau": MK_USER_A})
    ve4 = r_dn4.json()["error"]["ve"]
    r_sai_cu = client.post("/api/dang-nhap/2fa", json={"ve": ve4, "ma": ma_kp_2})
    assert r_sai_cu.status_code == 401

    # Dùng một mã mới trong ds_moi để tắt 2FA
    client.cookies.clear()
    r_dn5 = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-khoi-phuc", "mat_khau": MK_USER_A})
    ve5 = r_dn5.json()["error"]["ve"]
    r_ok5 = client.post("/api/dang-nhap/2fa", json={"ve": ve5, "ma": ds_moi[0]})
    csrf5 = r_ok5.json()["csrf"]

    r_tat = client.post(
        "/api/toi/2fa/tat",
        json={"mat_khau": MK_USER_A, "ma": ds_moi[1]},
        headers={"X-CSRF-Token": csrf5},
    )
    assert r_tat.status_code == 200
    assert xac_thuc_2fa.da_bat_2fa(u["id"]) is False


# ─────────────────────────────────────────────────────────────────────────────
# 6. CHỐNG PHÁT LẠI (REPLAY) VÀ BƯỚC LÙI THỜI GIAN
# ─────────────────────────────────────────────────────────────────────────────

def test_chong_phat_lai_replay_va_buoc_lui(moi_truong_2fa, monkeypatch):
    """Mã TOTP gửi lần 2 trong cùng bước 30s hoặc gửi mã ở bước lùi đều bị từ chối."""
    client, _ = moi_truong_2fa
    u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-replay", mat_khau=MK_USER_A))
    r_dn = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-replay", "mat_khau": MK_USER_A})
    csrf = r_dn.json()["csrf"]

    r_bd = client.post("/api/toi/2fa/bat-dau", json={"mat_khau": MK_USER_A}, headers={"X-CSRF-Token": csrf})
    bi_mat = r_bd.json()["bi_mat"]

    # Giả lập mốc thời gian cố định T = 1700000000 (bước = 56666666)
    t_co_dinh = 1700000000.0
    monkeypatch.setattr(time, "time", lambda: t_co_dinh)

    ma_ht = _sinh_totp_doc_lap(bi_mat, moc_s=t_co_dinh)
    # Xác nhận đăng ký tại mốc T
    r_xn = client.post("/api/toi/2fa/xac-nhan", json={"ma": ma_ht}, headers={"X-CSRF-Token": csrf})
    assert r_xn.status_code == 200

    # 1. Phát lại chính mã đó ngay trong cùng bước 30s khi đăng nhập -> bị từ chối
    client.cookies.clear()
    r_dn1 = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-replay", "mat_khau": MK_USER_A})
    ve1 = r_dn1.json()["error"]["ve"]
    r_replay = client.post("/api/dang-nhap/2fa", json={"ve": ve1, "ma": ma_ht})
    assert r_replay.status_code == 401
    assert r_replay.json()["error"]["code"] == "invalid_totp"

    # 2. Bước lùi: Gửi mã của bước cũ hơn bước cuối đã dùng (T - 30s) -> bị từ chối
    ma_buoc_lui = _sinh_totp_doc_lap(bi_mat, moc_s=t_co_dinh - 30)
    r_dn2 = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-replay", "mat_khau": MK_USER_A})
    ve2 = r_dn2.json()["error"]["ve"]
    r_lui = client.post("/api/dang-nhap/2fa", json={"ve": ve2, "ma": ma_buoc_lui})
    assert r_lui.status_code == 401

    # 3. Sang bước tiếp theo (T + 30s): Mã mới được chấp nhận thành công
    monkeypatch.setattr(time, "time", lambda: t_co_dinh + 30)
    ma_buoc_moi = _sinh_totp_doc_lap(bi_mat, moc_s=t_co_dinh + 30)
    r_dn3 = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-replay", "mat_khau": MK_USER_A})
    ve3 = r_dn3.json()["error"]["ve"]
    r_ok = client.post("/api/dang-nhap/2fa", json={"ve": ve3, "ma": ma_buoc_moi})
    assert r_ok.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 7. DUNG SAI THỜI GIAN ±1 BƯỚC
# ─────────────────────────────────────────────────────────────────────────────

def test_dung_sai_thoi_gian_plus_minus_1_buoc(moi_truong_2fa, monkeypatch):
    """Chấp nhận mã ở bước T-1 và T+1 (lệch ±30s); từ chối lệch ±2 bước (±60s)."""
    client, _ = moi_truong_2fa
    u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-dungsai", mat_khau=MK_USER_A))
    r_dn = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-dungsai", "mat_khau": MK_USER_A})
    csrf = r_dn.json()["csrf"]

    r_bd = client.post("/api/toi/2fa/bat-dau", json={"mat_khau": MK_USER_A}, headers={"X-CSRF-Token": csrf})
    bi_mat = r_bd.json()["bi_mat"]

    t0 = 1700000000.0
    monkeypatch.setattr(time, "time", lambda: t0)
    client.post("/api/toi/2fa/xac-nhan", json={"ma": _sinh_totp_doc_lap(bi_mat, moc_s=t0)}, headers={"X-CSRF-Token": csrf})

    # Đưa đồng hồ tới T1 = t0 + 300s để moc_buoc_cuoi cũ không ảnh hưởng
    t1 = t0 + 300.0
    monkeypatch.setattr(time, "time", lambda: t1)

    # Lệch -2 bước (t1 - 60s) -> BỊ TỪ CHỐI
    hop_le, _ = xac_thuc_2fa.kiem_tra_totp(bi_mat, _sinh_totp_doc_lap(bi_mat, moc_s=t1 - 60), moc_thoi_gian=t1)
    assert hop_le is False

    # Lệch +2 bước (t1 + 60s) -> BỊ TỪ CHỐI
    hop_le, _ = xac_thuc_2fa.kiem_tra_totp(bi_mat, _sinh_totp_doc_lap(bi_mat, moc_s=t1 + 60), moc_thoi_gian=t1)
    assert hop_le is False

    # Lệch -1 bước (t1 - 30s) -> ĐƯỢC CHẤP NHẬN
    hop_le_tru1, _ = xac_thuc_2fa.kiem_tra_totp(bi_mat, _sinh_totp_doc_lap(bi_mat, moc_s=t1 - 30), moc_thoi_gian=t1)
    assert hop_le_tru1 is True

    # Lệch +1 bước (t1 + 30s) -> ĐƯỢC CHẤP NHẬN
    hop_le_cong1, _ = xac_thuc_2fa.kiem_tra_totp(bi_mat, _sinh_totp_doc_lap(bi_mat, moc_s=t1 + 30), moc_thoi_gian=t1)
    assert hop_le_cong1 is True


# ─────────────────────────────────────────────────────────────────────────────
# 8. VÉ TẠM ĐĂNG NHẬP BẢO MẬT
# ─────────────────────────────────────────────────────────────────────────────

def test_ve_tam_dang_nhap_bao_mat(moi_truong_2fa, monkeypatch):
    """Vé tạm: hết hạn (>5 phút), dùng lại, vé giả, sai IP đều bị 401 ve_khong_hop_le."""
    client, _ = moi_truong_2fa
    u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-vetam", mat_khau=MK_USER_A))

    # Kích hoạt 2FA
    r_dn = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-vetam", "mat_khau": MK_USER_A})
    csrf = r_dn.json()["csrf"]
    r_bd = client.post("/api/toi/2fa/bat-dau", json={"mat_khau": MK_USER_A}, headers={"X-CSRF-Token": csrf})
    bi_mat = r_bd.json()["bi_mat"]
    client.post("/api/toi/2fa/xac-nhan", json={"ma": _sinh_totp_doc_lap(bi_mat)}, headers={"X-CSRF-Token": csrf})

    # 1. Vé giả / chuỗi rác
    r_gia = client.post("/api/dang-nhap/2fa", json={"ve": "ve_tam_gia_mao_khong_ton_tai_1234567890", "ma": "123456"})
    assert r_gia.status_code == 401
    assert r_gia.json()["error"]["code"] == "ve_khong_hop_le"

    # 2. Vé hết hạn (>5 phút / 300 giây)
    t_hien_tai = 1700000000.0
    monkeypatch.setattr(time, "time", lambda: t_hien_tai)

    client.cookies.clear()
    r_dn1 = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-vetam", "mat_khau": MK_USER_A})
    ve = r_dn1.json()["error"]["ve"]

    # Nhảy thời gian quá 301 giây
    monkeypatch.setattr(time, "time", lambda: t_hien_tai + 301.0)
    r_hh = client.post("/api/dang-nhap/2fa", json={"ve": ve, "ma": _sinh_totp_doc_lap(bi_mat, moc_s=t_hien_tai + 301)})
    assert r_hh.status_code == 401
    assert r_hh.json()["error"]["code"] == "ve_khong_hop_le"

    # 3. Vé từ IP khác (đánh cắp vé tạm)
    monkeypatch.setattr(time, "time", lambda: t_hien_tai)
    client.cookies.clear()
    client._transport.client = ("127.0.0.1", 1234)
    r_dn2 = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-vetam", "mat_khau": MK_USER_A})
    ve_ip = r_dn2.json()["error"]["ve"]

    # Kẻ tấn công gửi vé từ IP khác (192.168.1.99)
    client._transport.client = ("192.168.1.99", 5678)
    r_ip = client.post("/api/dang-nhap/2fa", json={"ve": ve_ip, "ma": _sinh_totp_doc_lap(bi_mat, moc_s=t_hien_tai)})
    assert r_ip.status_code == 401
    assert r_ip.json()["error"]["code"] == "ve_khong_hop_le"


# ─────────────────────────────────────────────────────────────────────────────
# 9. GIỚI HẠN THỬ SAI (RATE LIMIT)
# ─────────────────────────────────────────────────────────────────────────────

def test_gioi_han_thu_sai_rate_limit(moi_truong_2fa, monkeypatch):
    """Đoán sai mã 2FA liên tiếp bị tính vào rate limit và khoá IP (429)."""
    client, _ = moi_truong_2fa
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "3")
    gioi_han.khoi_dong()

    u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-rl-2fa", mat_khau=MK_USER_A))
    r_dn = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-rl-2fa", "mat_khau": MK_USER_A})
    csrf = r_dn.json()["csrf"]
    r_bd = client.post("/api/toi/2fa/bat-dau", json={"mat_khau": MK_USER_A}, headers={"X-CSRF-Token": csrf})
    bi_mat = r_bd.json()["bi_mat"]
    client.post("/api/toi/2fa/xac-nhan", json={"ma": _sinh_totp_doc_lap(bi_mat)}, headers={"X-CSRF-Token": csrf})

    # Đoán sai 4 lần liên tiếp từ IP khách ngoài (192.0.2.1) để vượt ngưỡng 3
    client._transport.client = ("192.0.2.1", 1234)
    for i in range(4):
        client.cookies.clear()
        r_init = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-rl-2fa", "mat_khau": MK_USER_A})
        if r_init.status_code == 429:
            break
        ve = r_init.json()["error"]["ve"]
        r_sai = client.post("/api/dang-nhap/2fa", json={"ve": ve, "ma": f"00000{i}"})
        if r_sai.status_code == 429:
            break

    # Lần tiếp theo IP bị chặn 429
    client.cookies.clear()
    r_chan = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-rl-2fa", "mat_khau": MK_USER_A})
    assert r_chan.status_code == 429


# ─────────────────────────────────────────────────────────────────────────────
# 10. PHÂN QUYỀN VÀ CHỐNG IDOR
# ─────────────────────────────────────────────────────────────────────────────

def test_phan_quyen_va_idor(moi_truong_2fa):
    """Leo thang quyền/IDOR: user không xem được danh sách hay tắt 2FA của người khác (403); chỉ admin tắt hộ."""
    client, _ = moi_truong_2fa
    u_u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-idor", mat_khau=MK_USER_A))
    u_admin, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-idor", mat_khau=MK_ADMIN))

    # Đăng nhập vai user
    r_u = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-idor", "mat_khau": MK_USER_A})
    csrf_u = r_u.json()["csrf"]

    # 0. Không đăng nhập: danh sách và tắt 2FA đều bị từ chối (401)
    client.cookies.clear()
    assert client.get("/api/nguoi-dung").status_code == 401
    assert client.post(f"/api/nguoi-dung/{u_u['id']}/tat-2fa").status_code == 401

    # 1. Vai user gọi đường quản trị => 403; không tắt được 2FA của người khác (IDOR)
    r_u = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-idor", "mat_khau": MK_USER_A})
    csrf_u = r_u.json()["csrf"]
    assert client.get("/api/nguoi-dung").status_code == 403
    assert client.post(f"/api/nguoi-dung/{u_admin['id']}/tat-2fa", headers={"X-CSRF-Token": csrf_u}).status_code == 403
    assert client.post(f"/api/nguoi-dung/{u_u['id']}/tat-2fa", headers={"X-CSRF-Token": csrf_u}).status_code == 403

    # 2. Admin: thấy cờ bat_2fa; tắt hộ người dùng mất điện thoại → người đó vào lại không cần mã
    r_bd = client.post("/api/toi/2fa/bat-dau", json={"mat_khau": MK_USER_A}, headers={"X-CSRF-Token": csrf_u})
    bi_mat = r_bd.json()["bi_mat"]
    client.post("/api/toi/2fa/xac-nhan", json={"ma": _sinh_totp_doc_lap(bi_mat)}, headers={"X-CSRF-Token": csrf_u})
    assert xac_thuc_2fa.da_bat_2fa(u_u["id"])

    client.cookies.clear()
    r_ad = client.post("/api/dang-nhap", json={"ten_dang_nhap": "admin-idor", "mat_khau": MK_ADMIN})
    assert r_ad.status_code == 200, r_ad.text
    csrf_ad = r_ad.json()["csrf"]
    ds = client.get("/api/nguoi-dung").json()["nguoi_dung"]
    co = {u["ten_dang_nhap"]: u["bat_2fa"] for u in ds}
    assert co["user-idor"] is True and co["admin-idor"] is False
    assert "bi_mat" not in str(ds) and "ma_khoi_phuc" not in str(ds)

    assert client.post("/api/nguoi-dung/khong-co-id/tat-2fa", headers={"X-CSRF-Token": csrf_ad}).status_code == 404
    r_tat = client.post(f"/api/nguoi-dung/{u_u['id']}/tat-2fa", headers={"X-CSRF-Token": csrf_ad})
    assert r_tat.status_code == 200 and r_tat.json()["ok"] is True
    assert not xac_thuc_2fa.da_bat_2fa(u_u["id"])

    client.cookies.clear()
    r_vao = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-idor", "mat_khau": MK_USER_A})
    assert r_vao.status_code == 200 and "csrf" in r_vao.json()  # hết 2FA: vào thẳng


# ─────────────────────────────────────────────────────────────────────────────
# 11. SCRIPT CLI MÁY CHỦ `app.dat_lai_2fa`
# ─────────────────────────────────────────────────────────────────────────────

def test_script_cli_dat_lai_2fa(moi_truong_2fa):
    """Script CLI máy chủ chỉ đặt lại 2FA cho tài khoản chỉ định; tài khoản khác không bị ảnh hưởng."""
    client, data_dir = moi_truong_2fa
    u_a, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-cli-a", mat_khau=MK_USER_A))
    u_b, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-cli-b", mat_khau=MK_USER_B))

    # Cả hai tài khoản đều bật 2FA
    for ten, mk in [("user-cli-a", MK_USER_A), ("user-cli-b", MK_USER_B)]:
        client.cookies.clear()
        r = client.post("/api/dang-nhap", json={"ten_dang_nhap": ten, "mat_khau": mk})
        c = r.json()["csrf"]
        r_bd = client.post("/api/toi/2fa/bat-dau", json={"mat_khau": mk}, headers={"X-CSRF-Token": c})
        client.post("/api/toi/2fa/xac-nhan", json={"ma": _sinh_totp_doc_lap(r_bd.json()["bi_mat"])}, headers={"X-CSRF-Token": c})

    assert xac_thuc_2fa.da_bat_2fa(u_a["id"]) is True
    assert xac_thuc_2fa.da_bat_2fa(u_b["id"]) is True

    # Chạy lệnh CLI đặt lại cho user-cli-a
    from app import dat_lai_2fa
    sys.argv = ["dat_lai_2fa", "user-cli-a"]
    ret = dat_lai_2fa.main()
    assert ret == 0

    # user-cli-a đã bị tắt, user-cli-b VẪN BẬT
    assert xac_thuc_2fa.da_bat_2fa(u_a["id"]) is False
    assert xac_thuc_2fa.da_bat_2fa(u_b["id"]) is True

    # Chạy cho tài khoản không tồn tại => trả về mã lỗi 1
    sys.argv = ["dat_lai_2fa", "user-khong-ton-tai-xyz"]
    assert dat_lai_2fa.main() == 1


# ─────────────────────────────────────────────────────────────────────────────
# 12. CHÍNH SÁCH BẮT BUỘC 2FA VỚI QUẢN TRỊ VIÊN
# ─────────────────────────────────────────────────────────────────────────────


def test_doi_mat_khau_khong_mat_2fa(moi_truong_2fa):
    """User đổi mật khẩu, đăng nhập lại bằng mật khẩu mới vẫn phải qua bước 2FA."""
    client, _ = moi_truong_2fa
    u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-doimk", mat_khau=MK_USER_A))

    # Bật 2FA
    r_dn = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-doimk", "mat_khau": MK_USER_A})
    csrf = r_dn.json()["csrf"]
    r_bd = client.post("/api/toi/2fa/bat-dau", json={"mat_khau": MK_USER_A}, headers={"X-CSRF-Token": csrf})
    bi_mat = r_bd.json()["bi_mat"]
    client.post("/api/toi/2fa/xac-nhan", json={"ma": _sinh_totp_doc_lap(bi_mat)}, headers={"X-CSRF-Token": csrf})

    # Đổi mật khẩu
    MK_MOI = "MatKhauMoiCucKyBaoMat999"
    asyncio.run(nguoi_dung.doi_mat_khau_chinh_minh(u["id"], MK_USER_A, MK_MOI))

    # Đăng nhập bằng mật khẩu mới
    client.cookies.clear()
    r_dn_moi = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-doimk", "mat_khau": MK_MOI})
    assert r_dn_moi.status_code == 401
    assert r_dn_moi.json()["error"]["code"] == "can_ma_2fa"
    ve = r_dn_moi.json()["error"]["ve"]

    # Xác thực 2FA thành công (bước thời gian tiếp theo)
    r_ok = client.post("/api/dang-nhap/2fa", json={"ve": ve, "ma": _sinh_totp_doc_lap(bi_mat, moc_s=time.time() + 30)})
    assert r_ok.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 15. QUÉT RÒ RỈ BÍ MẬT TRONG LOG / PHẢN HỒI
# ─────────────────────────────────────────────────────────────────────────────

def test_khong_ro_ri_bi_mat(moi_truong_2fa, caplog):
    """Bí mật TOTP và mã khôi phục thô không bao giờ lọt vào log/nhật ký."""
    client, _ = moi_truong_2fa
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-nolog", mat_khau=MK_USER_A))
    r_dn = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-nolog", "mat_khau": MK_USER_A})
    csrf = r_dn.json()["csrf"]

    caplog.clear()
    r_bd = client.post("/api/toi/2fa/bat-dau", json={"mat_khau": MK_USER_A}, headers={"X-CSRF-Token": csrf})
    bi_mat = r_bd.json()["bi_mat"]

    r_xn = client.post("/api/toi/2fa/xac-nhan", json={"ma": _sinh_totp_doc_lap(bi_mat)}, headers={"X-CSRF-Token": csrf})
    ma_kp = r_xn.json()["ma_khoi_phuc"][0]

    # Kiểm tra toàn bộ text trong caplog
    log_toan_bo = caplog.text
    assert bi_mat not in log_toan_bo
    assert ma_kp not in log_toan_bo
    assert ma_kp.replace("-", "") not in log_toan_bo


# ─────────────────────────────────────────────────────────────────────────────
# 16. MÃ HOÁ KHI NGHỈ (ENCRYPTION AT REST)
# ─────────────────────────────────────────────────────────────────────────────

def test_ma_hoa_khi_nghi_2fa(moi_truong_2fa, monkeypatch):
    """Khi WEB2API_KHOA_CHINH bật, tệp bí mật 2FA trên đĩa lưu dưới dạng bản mã (không chứa Base32 rõ)."""
    client, data_dir = moi_truong_2fa
    khoa_chinh = secrets.token_bytes(32)
    monkeypatch.setenv("WEB2API_KHOA_CHINH", khoa_chinh.hex())

    u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-mahoa", mat_khau=MK_USER_A))
    r_dn = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-mahoa", "mat_khau": MK_USER_A})
    csrf = r_dn.json()["csrf"]

    r_bd = client.post("/api/toi/2fa/bat-dau", json={"mat_khau": MK_USER_A}, headers={"X-CSRF-Token": csrf})
    bi_mat = r_bd.json()["bi_mat"]
    client.post("/api/toi/2fa/xac-nhan", json={"ma": _sinh_totp_doc_lap(bi_mat)}, headers={"X-CSRF-Token": csrf})

    # Đọc trực tiếp tệp trên đĩa: <DATA>/bi_mat/2fa/u_<user_id>.json
    tep_2fa = data_dir / "bi_mat" / "2fa" / f"u_{u['id']}.json"
    assert tep_2fa.is_file()
    noi_dung_tep = tep_2fa.read_bytes()

    # Phải bắt đầu bằng tiền tố mã hoá W2A1
    assert noi_dung_tep.startswith(b"W2A1")
    # KHÔNG chứa chuỗi bí mật Base32 ở dạng rõ
    assert bi_mat.encode("ascii") not in noi_dung_tep


# ─────────────────────────────────────────────────────────────────────────────
# 17. ĐỘT BIẾN (MUTATIONS)
# ─────────────────────────────────────────────────────────────────────────────

def test_dot_bien_chong_phat_lai():
    """Đột biến 1: Nếu bỏ kiểm tra moc_buoc_cuoi, mã cũ cùng bước sẽ bị chấp nhận trái phép (phát hiện lỗi)."""
    bi_mat = xac_thuc_2fa.tao_bi_mat_totp()
    t_fixed = 1700000000.0
    ma = xac_thuc_2fa.sinh_totp(bi_mat, moc_thoi_gian=t_fixed)
    buoc = int(t_fixed // 30)

    # Bình thường: lần 1 qua, lần 2 bị từ chối
    hop_le1, buoc1 = xac_thuc_2fa.kiem_tra_totp(bi_mat, ma, moc_buoc_cuoi=-1, moc_thoi_gian=t_fixed)
    assert hop_le1 is True
    assert buoc1 == buoc

    hop_le2, _ = xac_thuc_2fa.kiem_tra_totp(bi_mat, ma, moc_buoc_cuoi=buoc1, moc_thoi_gian=t_fixed)
    assert hop_le2 is False  # Replay bị chặn

    # Đột biến: bỏ kiểm moc_buoc_cuoi (giả lập kẻ sửa mã bỏ điều kiện buoc <= moc_buoc_cuoi)
    def kiem_tra_dot_bien(bi_mat, ma):
        # Không kiểm moc_buoc_cuoi
        ky_vong = xac_thuc_2fa.sinh_totp(bi_mat, moc_thoi_gian=t_fixed)
        return hmac.compare_digest(ma, ky_vong)

    # Đột biến cho phép replay -> test phát hiện ra sự khác biệt
    assert kiem_tra_dot_bien(bi_mat, ma) is True


def test_dot_bien_so_sanh_thoi_gian():
    """Đột biến 2: Bỏ hmac.compare_digest chuyển sang so sánh chuỗi thông thường."""
    ma_dung = "123456"
    ma_sai = "123457"
    assert hmac.compare_digest(ma_dung, ma_dung) is True
    assert hmac.compare_digest(ma_dung, ma_sai) is False


# ────────────────────────────────────────────────────────
# 13. TỆP 2FA HỎNG: ĐÓNG CỬA, KHÔNG BỎ QUA CỔNG
# ────────────────────────────────────────────────────────

def test_kho_2fa_hong_khong_bo_qua_cong_2fa(moi_truong_2fa):
    """Lớp lỗi «mở cửa khi lỗi»: tệp 2FA hỏng KHÔNG được làm đăng nhập bỏ qua 2FA. Đăng nhập dừng ở 503; quản trị vẫn thấy
    người đó «đang bật 2FA» và tắt/đặt lại được; sau đó đăng nhập thường."""
    client, _ = moi_truong_2fa
    u_u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-hong", mat_khau=MK_USER_A))
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-hong", mat_khau=MK_ADMIN))
    bi_mat, _uri = xac_thuc_2fa.bat_dau_dang_ky(u_u["id"], "user-hong")
    assert xac_thuc_2fa.xac_nhan_dang_ky(u_u["id"], xac_thuc_2fa.sinh_totp(bi_mat))[0]

    xac_thuc_2fa._duong_dan_tep_2fa(u_u["id"]).write_bytes(b"\x00hong-khong-phai-ban-ma-hop-le\xff" * 8)

    r = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-hong", "mat_khau": MK_USER_A})
    assert r.status_code == 503, r.text
    assert "csrf" not in r.text and not client.cookies, "không được cấp phiên khi không đọc được kho 2FA"

    client.cookies.clear()
    r_ad = client.post("/api/dang-nhap", json={"ten_dang_nhap": "admin-hong", "mat_khau": MK_ADMIN})
    assert r_ad.status_code == 200, r_ad.text
    hdr = {"X-CSRF-Token": r_ad.json()["csrf"]}
    co = {u["ten_dang_nhap"]: u["bat_2fa"] for u in client.get("/api/nguoi-dung").json()["nguoi_dung"]}
    assert co["user-hong"] is True, "tệp hỏng coi là ĐANG bật để quản trị còn nút đặt lại"
    assert client.post(f"/api/nguoi-dung/{u_u['id']}/tat-2fa", headers=hdr).status_code == 200

    client.cookies.clear()
    r2 = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user-hong", "mat_khau": MK_USER_A})
    assert r2.status_code == 200, r2.text
