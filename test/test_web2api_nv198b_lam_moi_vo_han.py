"""NV198b — vòng lặp làm mới token VÔ HẠN khi 401 (openaicodex).

Nguyên nhân gốc (NV198a đo): `khoa` = sha256(access_token) đổi sau mỗi lần làm
mới thành công nên tập «đã làm mới trong lượt» không bao giờ khớp. Cách sửa
đúng NGUYÊN TẮC: (1) khoá «đã thử / đã làm mới trong lượt» là mã ỔN ĐỊNH của
tài khoản, (2) trần cứng tổng số lần làm mới trong một lượt chat, (3) 401 sau
khi vừa làm mới đếm ĐÚNG MỘT lần vào ngưỡng vô hiệu và không bị làm mới xoá.

Mọi test chạy qua LÕI THẬT + máy chủ OAuth/Codex giả (fixture của NV198a), có
bẫy fail-fast: vòng lặp vô hạn thành ĐỎ gọn, không treo suite.
"""

from __future__ import annotations

import threading
import time

import pytest

from test_web2api_nv198a_oauth_do_khoang_cach import (  # noqa: F401
    cai_voi_refresh_token, chay, may_chu, mod, tao_jwt)

BODY = {"model": "openaicodex/gpt-5.5",
        "messages": [{"role": "user", "content": "chào"}]}


def chat_co_bay(the, may_chu, toi_da_lam_moi_them=3):
    """Chạy MỘT lượt chat trong luồng riêng; vòng lặp làm mới vô hạn → ĐỎ.

    Khi bẫy kích: cho refresh thất bại (invalid_grant) để vòng lặp tự thoát,
    không để luồng nền đốt CPU suốt phiên test."""
    ban_dau = may_chu.so_lan_lam_moi
    ket_qua: dict = {}

    def _goi():
        ket_qua["kq"] = chay(the.chat(BODY, False))

    luong = threading.Thread(target=_goi, daemon=True)
    luong.start()
    han = time.time() + 30
    while luong.is_alive() and time.time() < han:
        if may_chu.so_lan_lam_moi - ban_dau > toi_da_lam_moi_them:
            may_chu.kich_ban = "invalid_grant"
            luong.join(5)
            pytest.fail("vòng lặp làm mới vô hạn: "
                        f"{may_chu.so_lan_lam_moi - ban_dau} lần làm mới "
                        "trong MỘT lượt chat")
        time.sleep(0.05)
    assert not luong.is_alive(), "treo: chat() không trả sau 30s"
    return ket_qua["kq"]


def them_tai_khoan(the, email, rt):
    lop = type(the._tai_khoan[0])
    tk = lop(tao_jwt(email=email, account_id="acc-" + rt), rt)
    the._tai_khoan.append(tk)
    return tk


def test_mot_tai_khoan_401_lam_moi_dung_mot_lan_roi_tra_401(mod, may_chu):
    the, _goi = mod
    tk = cai_voi_refresh_token(the, may_chu)
    may_chu.token_codex_hop_le = "khong-bao-gio-khop"
    truoc = may_chu.so_lan_lam_moi
    ma, _h, _b = chat_co_bay(the, may_chu)
    assert ma == 401
    assert may_chu.so_lan_lam_moi - truoc == 1, "phải làm mới ĐÚNG 1 lần/lượt"
    assert may_chu.so_lan_codex == 2  # 401 → làm mới → thử lại → 401 → dừng
    assert tk.so_401 == 1 and tk.trang_thai == "hoat_dong"


def test_401_sau_lam_moi_cong_don_qua_cac_luot_toi_nguong_vo_hieu(mod, may_chu):
    """Làm mới KHÔNG được xoá bộ đếm 401: đã 1 → lượt này lên 2 (còn hoạt
    động); lượt kế lên 3 → vô hiệu; lượt sau nữa không còn tài khoản (503)
    và không làm mới thêm."""
    the, _goi = mod
    tk = cai_voi_refresh_token(the, may_chu)
    may_chu.token_codex_hop_le = "khong-bao-gio-khop"
    tk.so_401 = 1
    ma, _h, _b = chat_co_bay(the, may_chu)
    assert ma == 401
    assert tk.so_401 == 2, "làm mới đã xoá bộ đếm 401"
    assert tk.trang_thai == "hoat_dong"
    ma, _h, _b = chat_co_bay(the, may_chu)
    assert ma == 401
    assert tk.trang_thai == "vo_hieu", "đủ 3 lần 401 mà chưa vô hiệu"
    truoc = may_chu.so_lan_lam_moi
    ma, _h, _b = chat_co_bay(the, may_chu)
    assert ma == 503
    assert may_chu.so_lan_lam_moi == truoc, "tài khoản vô hiệu vẫn bị làm mới"


def test_tran_cung_la_tong_cho_ca_luot_khong_phai_moi_tai_khoan(mod, may_chu):
    """Hai tài khoản cùng 401: lượt chat chỉ làm mới TỔNG 1 lần (tài khoản
    đầu); tài khoản sau không bị làm mới và không bị đếm 401 oan."""
    the, _goi = mod
    a = cai_voi_refresh_token(the, may_chu)
    b = them_tai_khoan(the, "nguoi.b@example.com", "RT-B-1")
    token_b = b.access_token
    may_chu.token_codex_hop_le = "khong-bao-gio-khop"
    truoc = may_chu.so_lan_lam_moi
    ma, _h, _b = chat_co_bay(the, may_chu)
    assert ma == 401
    assert may_chu.so_lan_lam_moi - truoc == 1, "vượt trần 1 lần làm mới/lượt"
    assert may_chu.so_lan_codex == 3  # A 401, A (token mới) 401, B 401
    assert a.so_401 == 1
    assert b.access_token == token_b and b.so_401 == 0
    assert b.trang_thai == "hoat_dong"


def test_ma_on_dinh_bat_chap_khoa_xoay_giua_luot(mod, may_chu):
    """Khoá xoay (token đổi do luồng khác làm mới) giữa lượt: tài khoản đã
    thử không được chọn lại. Cùng lớp lỗi với vòng lặp vô hạn: định danh
    «đã thử» không được lệ thuộc giá trị xoay theo token."""
    the, _goi = mod
    a = cai_voi_refresh_token(the, may_chu)
    b = them_tai_khoan(the, "nguoi.b@example.com", "RT-B-1")
    cac_lan: list[str] = []

    async def gia(tk, than):  # noqa: ARG001
        cac_lan.append(tk.ma_on_dinh)
        if len(cac_lan) == 2:
            a.access_token = tao_jwt()  # luồng khác làm mới A: khoa A đổi
        return 500, b'{"error":{"message":"loi"}}', {}

    the._goi_codex = gia
    ma, _h, _b = chay(the.chat(BODY, False))
    assert ma == 500
    assert cac_lan == [a.ma_on_dinh, b.ma_on_dinh], \
        f"tài khoản đã thử bị chọn lại sau khi khoa xoay: {len(cac_lan)} lần gọi"
