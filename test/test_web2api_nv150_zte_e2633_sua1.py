"""Test NV150 vòng sửa 1/3 — 6 lỗi L1..L6 + phủ 4 đột biến (mục T).

Chạy ĐỎ trước khi sửa module, XANH sau. Dùng chung hạ tầng bộ phát giả của
test_web2api_nv150_zte_e2633.py (import lại fixture/hằng số từ đó).
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from test_web2api_nv150_zte_e2633 import (  # noqa: F401
    DongHoGia,
    MAT_KHAU_GIA,
    TAI_KHOAN_GIA,
    _cau_hinh,
    chay,
    cho_phep_loopback,
    dong_ho,
    manifest,
    may_chu_gia,
    mod,
)


@pytest.fixture(autouse=True)
def xoa_phien_cai_dung_lai(mod):
    """Sổ phiên dùng lại của cai() (L4) là toàn cục tiến trình: xoá trước mỗi
    test để không rò rỉ giữa các test."""
    so = getattr(mod, "_PHIEN_CAI_DUNG_LAI", None)
    if so is not None:
        so.clear()
    yield
    if so is not None:
        so.clear()


# ── L1: suc_khoe() sai ở phút 5–10 sau lần đọc thành công ─────────────────────

def test_l1_suc_khoe_phut_6_phien_con_song_van_ok(mod, manifest, may_chu_gia,
                                                 cho_phep_loopback, dong_ho):
    """ĐỎ trước sửa: nap + suc_khoe ok ở t0; +6 phút; suc_khoe phải ok True
    (phiên còn sống → đọc luôn, không điều tiết), lan_dang_nhap == 1."""
    tt = may_chu_gia["tt"]
    m = mod.Module(manifest, dong_ho=dong_ho)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        assert chay(m.suc_khoe())["ok"] is True
        dong_ho.nhich(6)  # qua hạn cache 5' nhưng trong khoảng điều tiết 10'
        sk = chay(m.suc_khoe())
    finally:
        chay(m.dung())
    assert sk["ok"] is True, \
        f"phiên còn sống thì suc_khoe phải đọc luôn, không báo chờ thử lại: {sk}"
    assert tt.lan_dang_nhap == 1


# ── L2: đua tranh vỡ luật «một phiên» ────────────────────────────────────────

def test_l2_gather_chi_dang_nhap_mot_lan(mod, manifest, may_chu_gia,
                                        cho_phep_loopback, dong_ho):
    """ĐỎ trước sửa: gather(2×thuc_the + 1×suc_khoe) trên module chưa có phiên
    → đúng 1 lần đăng nhập; người đến sau dùng phiên của người trước."""
    tt = may_chu_gia["tt"]
    m = mod.Module(manifest, dong_ho=dong_ho)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))

    async def _chay():
        try:
            r = await asyncio.gather(
                m.thuc_the(None), m.suc_khoe(), m.thuc_the(None))
            return r
        finally:
            await m.dung()

    kq = asyncio.run(_chay())
    assert tt.lan_dang_nhap == 1, \
        f"3 lời gọi đồng thời chỉ được đăng nhập 1 lần, thực tế {tt.lan_dang_nhap}"
    assert len(kq[0]) == 2 and len(kq[2]) == 2
    assert kq[1]["ok"] is True and kq[1]["so_may"] == 2


# ── L3: chính sách điều tiết dùng chung + giữ dữ liệu cũ có hạn ───────────────

def test_l3_thuc_the_loi_giu_du_lieu_cu_co_han_qua_han_thi_bao_loi(
        mod, manifest, may_chu_gia, cho_phep_loopback, dong_ho):
    """Lỗi mạng/«trang lạ» không được trả [] như «0 máy»: giữ dữ liệu cũ trong
    hạn 5 phút; quá hạn thì NÉM để lõi ghi trạng thái «loi» (không «trong»)."""
    tt = may_chu_gia["tt"]
    m = mod.Module(manifest, dong_ho=dong_ho)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        assert len(chay(m.thuc_the(None))) == 2
        tt.trang_dang_nhap_la = True  # lỗi kéo dài
        # Phiên chết cùng lúc với lỗi (đúng ca «hết phiên» của L3): mọi lần đọc
        # sau đều phải đăng nhập lại → mới chạm vào trang đăng nhập lạ.
        # (Không có dòng này thì phiên còn sống, đọc thành công, test không có
        # ý nghĩa.)
        tt.het_phien_truoc_khi_doc = True
        dong_ho.nhich(3)
        ds = chay(m.thuc_the(None))
        assert len(ds) == 2, "còn hạn: giữ dữ liệu cũ, không trả rỗng"
        dong_ho.nhich(3)  # tổng 6': quá hạn giữ
        with pytest.raises(mod.ZteLoi):
            chay(m.thuc_the(None))
    finally:
        chay(m.dung())


# ── L4: cai() không giữ phiên mồ côi — bản phục vụ dùng lại cookie ────────────

def test_l4_cai_de_lai_phien_ban_phuc_vu_dung_lai(mod, manifest, may_chu_gia,
                                                 cho_phep_loopback):
    """ĐỎ trước sửa: cai() đăng nhập thử thành công → bản phục vụ (thể hiện mới
    nap cùng cấu hình) dùng lại cookie, KHÔNG đăng nhập lần nữa."""
    tt = may_chu_gia["tt"]
    m1 = mod.Module(manifest)
    kq = chay(m1.cai(_cau_hinh(may_chu_gia["dia_chi"])))
    assert kq["ok"] is True
    assert kq["ket_qua_tung_khoa"][0]["ma_loi"] is None
    assert tt.lan_dang_nhap == 1
    m2 = mod.Module(manifest)
    m2.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        ds = chay(m2.thuc_the(None))
    finally:
        chay(m2.dung())
    assert len(ds) == 2
    assert tt.lan_dang_nhap == 1, \
        "bản phục vụ phải dùng lại phiên từ cai(), không đăng nhập mới"


def test_l4_cai_that_bai_xoa_phien_de_lai(mod, manifest, may_chu_gia,
                                         cho_phep_loopback):
    """cai() đăng nhập thử thất bại → không để lại cookie cũ cho bản phục vụ."""
    tt = may_chu_gia["tt"]
    m1 = mod.Module(manifest)
    kq = chay(m1.cai(_cau_hinh(may_chu_gia["dia_chi"])))
    assert tt.lan_dang_nhap == 1
    kq2 = chay(m1.cai(_cau_hinh(may_chu_gia["dia_chi"],
                                mat_khau="SaiMatKhau-Gia-L4")))
    assert kq2["ket_qua_tung_khoa"][0]["ma_loi"] == "MAT_KHAU_BI_TU_CHOI"
    assert not mod._PHIEN_CAI_DUNG_LAI, \
        "đăng nhập thử thất bại phải xoá phiên để lại (tránh cookie cũ)"


# ── L5: ranh giới cai()/suc_khoe() không ném ──────────────────────────────────

def test_l5_cai_dia_chi_di_dang_luu_roi_canh_bao_khong_nem(
        mod, manifest, cho_phep_loopback):
    """ĐỎ trước sửa: cai() với địa chỉ dị dạng (cổng «abc») hiện ném ngoại lệ
    → 500, cấu hình không lưu. Đúng: lưu rồi cảnh báo có cấu trúc."""
    m = mod.Module(manifest)
    kq = chay(m.cai({"dia_chi": "https://192.168.1.1:abc",
                     "tai_khoan": TAI_KHOAN_GIA, "mat_khau": MAT_KHAU_GIA}))
    assert isinstance(kq, dict), "cai() không được ném với địa chỉ dị dạng"
    assert kq["ok"] is True, "địa chỉ dị dạng: vẫn lưu cấu hình, chỉ cảnh báo"
    muc = kq["ket_qua_tung_khoa"][0]
    assert muc["ma_loi"] is not None and muc["canh_bao"]
    assert MAT_KHAU_GIA not in json.dumps(kq["ket_qua_tung_khoa"])


def test_l5_cai_dia_chi_ky_tu_dieu_khien_khong_nem(mod, manifest, cho_phep_loopback):
    m = mod.Module(manifest)
    kq = chay(m.cai({"dia_chi": "https://192.168.1.1/\x00",
                     "tai_khoan": TAI_KHOAN_GIA, "mat_khau": MAT_KHAU_GIA}))
    assert isinstance(kq, dict), "cai() không được ném với ký tự điều khiển"
    assert kq["ok"] is True


def test_l5_suc_khoe_khong_nem_khi_doc_may_loi_la(mod, manifest, may_chu_gia,
                                                cho_phep_loopback, caplog):
    """ĐỎ trước sửa: suc_khoe() ném RuntimeError khi doc_may ném loại lạ —
    trái hợp đồng «không ném»."""
    m = mod.Module(manifest)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))

    async def _hong(*a, **k):
        raise RuntimeError("loi-gia-khong-luong-truoc")

    m._khach.doc_may = _hong
    try:
        with caplog.at_level(logging.DEBUG, logger="web2api.zte_e2633"):
            sk = chay(m.suc_khoe())
    finally:
        chay(m.dung())
    assert sk["ok"] is False and "chưa kết nối được" in sk["loi"]
    assert "RuntimeError" in caplog.text, "phải ghi log KIỂU ngoại lệ"
    assert "loi-gia-khong-luong-truoc" not in caplog.text


# ── L6: nhánh TAI_KHOAN_BI_KHOA có test ───────────────────────────────────────

def test_l6_bi_khoa_co_test_va_tam_ngung_dai(mod, manifest, may_chu_gia,
                                            cho_phep_loopback):
    """Giữ nhánh khoá tài khoản VÌ: phát hiện bằng cấu trúc JSON rẻ, và hành vi
    an toàn đúng nếu thiết bị thật có tín hiệu này (tạm ngừng dài, không dồn
    dập). Ghi rõ «chưa xác nhận trên bộ phát thật» trong README module."""
    tt = may_chu_gia["tt"]
    tt.khoa_tai_khoan = True
    m = mod.Module(manifest)
    kq = chay(m.cai(_cau_hinh(may_chu_gia["dia_chi"])))
    assert kq["ok"] is True
    muc = kq["ket_qua_tung_khoa"][0]
    assert muc["ma_loi"] == "TAI_KHOAN_BI_KHOA", \
        f"tắt nhánh ZteBiKhoa phải làm test này đỏ, thực tế ma_loi={muc['ma_loi']}"
    m2 = mod.Module(manifest)
    m2.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        with pytest.raises(mod.ZteBiKhoa):
            chay(m2.thuc_the(None))
        assert m2._tam_ngung_den is not None, "bị khoá → tạm ngừng dài"
    finally:
        chay(m2.dung())


# ── T: phủ 4 đột biến ─────────────────────────────────────────────────────────

def test_t_tam_ngung_tach_rieng_khoi_gian_cach(mod, manifest, may_chu_gia,
                                              cho_phep_loopback, dong_ho):
    """Đột biến (i): bỏ nhánh tạm ngừng trong suc_khoe() phải đỏ — qua cả giãn
    cách nhưng còn tạm ngừng thì vẫn không được thử lại."""
    tt = may_chu_gia["tt"]
    tt.tu_choi_dang_nhap = True
    m = mod.Module(manifest, dong_ho=dong_ho)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        with pytest.raises(mod.ZteLoiDangNhap):
            chay(m.thuc_the(None))
        assert m._tam_ngung_den is not None
        loi_truoc = tt.lan_dang_nhap_loi
        dong_ho.nhich(11)  # qua giãn cách 10' nhưng còn trong tạm ngừng 30'
        sk = chay(m.suc_khoe())
    finally:
        chay(m.dung())
    assert sk["ok"] is False
    assert "tam_ngung_den" in sk, "nhánh tạm ngừng phải trả mốc tạm ngừng"
    assert tt.lan_dang_nhap_loi == loi_truoc, \
        "đang tạm ngừng: suc_khoe không được gửi thêm yêu cầu"


def test_t_khoang_cach_1_va_30_doi_hanh_vi_that(mod, manifest, may_chu_gia,
                                               cho_phep_loopback):
    """Đột biến (ii)+(iii): bỏ điều tiết / ép khoang_cach về mặc định phải đỏ —
    chỉnh khoang_cach_dang_nhap_phut thành 1 và 30 phải đổi hành vi thật."""
    tt = may_chu_gia["tt"]

    # khoang_cach = 1: sau 6' (hết hạn cache 5') được đăng nhập lại
    dh1 = DongHoGia()
    m1 = mod.Module(manifest, dong_ho=dh1)
    ch1 = _cau_hinh(may_chu_gia["dia_chi"])
    ch1["khoang_cach_dang_nhap_phut"] = "1"
    m1.nap(ch1)
    try:
        assert chay(m1.suc_khoe())["ok"] is True
        lan1 = tt.lan_dang_nhap
        chay(m1.dung())
        dh1.nhich(6)
        sk1 = chay(m1.suc_khoe())
    finally:
        chay(m1.dung())
    assert sk1["ok"] is True, "khoang_cach=1: qua 6' phải được đăng nhập lại"
    assert tt.lan_dang_nhap == lan1 + 1

    # khoang_cach = 30: sau 6' vẫn bị chặn
    dh2 = DongHoGia()
    m2 = mod.Module(manifest, dong_ho=dh2)
    ch2 = _cau_hinh(may_chu_gia["dia_chi"])
    ch2["khoang_cach_dang_nhap_phut"] = "30"
    m2.nap(ch2)
    try:
        assert chay(m2.suc_khoe())["ok"] is True
        lan2 = tt.lan_dang_nhap
        chay(m2.dung())
        dh2.nhich(6)
        sk2 = chay(m2.suc_khoe())
    finally:
        chay(m2.dung())
    assert sk2["ok"] is False and "đang chờ thử lại" in sk2["loi"], \
        "khoang_cach=30: sau 6' vẫn phải chờ"
    assert tt.lan_dang_nhap == lan2, "bị giãn cách: không gửi thêm đăng nhập"


def test_doi_chieu_van_tay_cong_chuoi_khong_nem_ngoai_le_tran(mod):
    """phan.port ném ValueError khi cổng là chuỗi (vd :abc) — phải thành ZteLoiKetNoi."""
    khach = mod.MayKhachZte("https://127.0.0.1:abc", "admin", "mat_khau",
                            van_tay_tls="a" * 64)
    with pytest.raises(mod.ZteLoiKetNoi):
        khach._doi_chieu_van_tay()


def test_dung_xoa_cache_doc_cuoi(mod, manifest, may_chu_gia, cho_phep_loopback):
    """dung() dọn dẹp sạch cache _doc_cuoi và _tam_ngung_den."""
    m = mod.Module(manifest)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        assert chay(m.suc_khoe())["ok"] is True
        assert m._doc_cuoi is not None
        chay(m.dung())
        assert m._doc_cuoi is None
        assert m._tam_ngung_den is None
    finally:
        chay(m.dung())
