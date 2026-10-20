"""Test NV170-SUA — module `maytram`: vá M1–M6 theo soát độc lập C1 (mục 3).

- M1: xoay khoá/thu hồi/xoá GIỮA bắt tay phải vô hiệu bắt tay đang dở; phiên
  giữ dấu vân khoá và `_thuc_hien_may` đối chiếu khoá hiện hành mỗi lệnh;
  xoay khoá phải ngắt phiên đang sống.
- M2: `_thuc_hien_quan_ly` và `_thuc_hien_may` tự kiểm vai người gọi
  (quan_tri/noi_bo, đóng khi lỗi) — không phụ thuộc mức rủi ro lõi gán cho
  thực thể (quản trị hạ rủi ro xuống «ghi»/«doc» thì khách vẫn bị từ chối).
- M3: trần đầu ra tính theo TỔNG ký tự toàn cấu trúc + giới hạn phần tử/độ
  sâu; `thuc_hien` không bao giờ ném (kể cả RecursionError).
- M4: `thoi_gian` phải là số hữu hạn, không phải bool; kiểm thời gian TRƯỚC
  kiểm nonce (khung chào sai giờ không được đốt nonce).
- M5: tên máy không được trùng không gian tên của thực thể quản lý.
- M6: `doi_quyen bat=false` ngắt phiên đang sống.

Dùng lại bộ khung của test NV170 gốc (kênh nối cặp, agent giả) — không mạng.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import secrets
import sys
import time
from contextlib import contextmanager

import pytest

from app import ngu_canh

from test_web2api_nv170_may_tram import (
    DUONG_DAN_GIA,
    TEN_GOI,
    _dang_ky,
    _kenh_kep,
    _module_moi_a,
    _noi,
    _tai_goi,
    chay,
)

QT = {"loai": "quan_tri", "id": "admin-sua"}
NOI_BO = {"loai": "noi_bo", "id": "loi"}
KHACH = {"loai": "khach", "id": "khach-1", "pham_vi": ["thiet_bi:ghi"]}


@pytest.fixture()
def mod():
    return _tai_goi()


@pytest.fixture()
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    return tmp_path


@contextmanager
def _vai(danh_tinh, nguon="nguoi"):
    tok = ngu_canh.dat(danh_tinh, nguon=nguon)
    try:
        yield
    finally:
        ngu_canh.khoi_phuc(tok)


async def _trong_vai(danh_tinh, coro_fn):
    """Chạy coro_fn() với ngữ cảnh danh_tinh đặt sẵn (khôi phục sau)."""
    tok = ngu_canh.dat(danh_tinh, nguon="nguoi")
    try:
        return await coro_fn()
    finally:
        ngu_canh.khoi_phuc(tok)


def _pa():
    return importlib.import_module(f"{TEN_GOI}.phien_agent")


async def _bat_tay_tay(m, ten, khoa_hex, *, giua=None, thoi_gian=None,
                       nonce=None):
    """Bắt tay THỦ CÔNG từng bước qua kênh nối cặp (không qua AgentGia).

    `giua`: callable (đồng bộ) chạy SAU khi lõi đã gửi «thach_thuc» và TRƯỚC
    khi agent gửi «dap» — chỗ chèn xoay khoá/thu hồi/xoá giữa bắt tay.
    Trả (phien, loi, thach) — thach là khung thách thức lõi đã gửi (None nếu
    lõi từ chối ngay ở khung chào).
    """
    pa = _pa()
    khoa = bytes.fromhex(khoa_hex)
    kenh_loi, kenh_agent = _kenh_kep()
    task = asyncio.ensure_future(m._phien.nhan_agent(kenh_loi))
    nonce = nonce or secrets.token_hex(16)
    tg = time.time() if thoi_gian is None else thoi_gian
    await kenh_agent.gui({"loai": "chao", "ten": ten,
                          "nonce_agent": nonce, "thoi_gian": tg})
    t_nhan = asyncio.ensure_future(kenh_agent.nhan())
    done, _ = await asyncio.wait({task, t_nhan}, timeout=5,
                                 return_when=asyncio.FIRST_COMPLETED)
    if t_nhan not in done:
        t_nhan.cancel()
        loi = task.exception() if task.done() else None
        if not task.done():
            task.cancel()
            loi = RuntimeError("lõi không phản hồi khung chào")
        return None, loi, None
    thach = t_nhan.result()
    if giua is not None:
        giua()
    nonce_loi = str(thach.get("nonce_loi") or "")
    dap = pa._hmac_hex(khoa, b"agent:", nonce.encode(), b":",
                       nonce_loi.encode())
    await kenh_agent.gui({"loai": "dap", "xac_thuc_agent": dap,
                          "thong_tin": {"nen_tang": "gia",
                                        "ten_may": "may-gia",
                                        "phien_ban": "0-test"}})
    try:
        phien = await asyncio.wait_for(task, timeout=5)
        return phien, None, thach
    except Exception as e:  # BatTayLoi hoặc lỗi khác
        return None, e, thach


# ── M1: bắt tay đang dở phải chết khi khoá/máy đổi giữa chừng ───────────────

def test_m1_xoay_khoa_giua_bat_tay_bi_tu_choi(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa_cu = await _dang_ky(m, "may-gia-1")
        phien, loi, _ = await _bat_tay_tay(
            m, "may-gia-1", khoa_cu, giua=lambda: m._so.xoay_khoa("may-gia-1"))
        assert phien is None and loi is not None, (
            "xoay khoá giữa bắt tay: kẻ giữ khoá CŨ không được lập phiên")
        assert isinstance(loi, _pa().BatTayLoi)
        assert m._phien.lay("may-gia-1") is None
    chay(_trong_vai(QT, kb))


def test_m1_thu_hoi_giua_bat_tay_bi_tu_choi(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        phien, loi, _ = await _bat_tay_tay(
            m, "may-gia-1", khoa, giua=lambda: m._so.thu_hoi("may-gia-1"))
        assert phien is None and isinstance(loi, _pa().BatTayLoi), (
            "máy đã thu hồi giữa bắt tay không được có phiên sống")
        assert m._phien.lay("may-gia-1") is None
    chay(_trong_vai(QT, kb))


def test_m1_xoa_giua_bat_tay_bi_tu_choi(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        phien, loi, _ = await _bat_tay_tay(
            m, "may-gia-1", khoa, giua=lambda: m._so.xoa("may-gia-1"))
        assert phien is None and isinstance(loi, _pa().BatTayLoi)
        assert m._phien.lay("may-gia-1") is None
    chay(_trong_vai(QT, kb))


def test_m1_bat_tay_sach_va_phien_giu_dau_van(mod, du_lieu):
    """Đối chứng: không ai can thiệp giữa bắt tay → phiên lập được và phiên
    giữ ĐÚNG dấu vân của khoá hiện hành."""
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        phien, loi, _ = await _bat_tay_tay(m, "may-gia-1", khoa)
        assert loi is None and phien is not None
        assert getattr(phien, "dau_van", None) == m._so.lay("may-gia-1")["dau_van"]
        await m._phien.ngat("may-gia-1", "hết test")
    chay(_trong_vai(QT, kb))


def test_m1_xoay_khoa_ngat_phien_dang_song(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        _, t = await _noi(m, "may-gia-1", khoa)
        assert m._phien.lay("may-gia-1") is not None
        kq = await m.thuc_hien("maytram.dang_ky", "xoay_khoa",
                               {"ten_may": "may-gia-1"})
        assert kq["ok"] is True
        assert m._phien.lay("may-gia-1") is None, "phiên cũ phải bị ngắt ngay"
        kq = await m.thuc_hien("maytram.may-gia-1", "liet_ke",
                               {"duong_dan": "/du-lieu-gia"})
        assert kq["ok"] is False and "kết nối" in kq["loi"]
        t.cancel()
    chay(_trong_vai(QT, kb))


def test_m1_phien_dau_van_cu_bi_chan_o_moi_lenh(mod, du_lieu):
    """Phiên còn sót trong sổ với dấu vân CŨ (xoay khoá ngoài đường module,
    phiên chưa kịp ngắt): _thuc_hien_may phải đối chiếu khoá hiện hành, từ
    chối lệnh và ngắt phiên đó."""
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        phien, t = await _noi(m, "may-gia-1", khoa)
        m._so.xoay_khoa("may-gia-1")  # xoay thẳng ở sổ — phiên vẫn còn trong sổ phiên
        assert m._phien.lay("may-gia-1") is phien
        kq = await m.thuc_hien("maytram.may-gia-1", "liet_ke",
                               {"duong_dan": "/du-lieu-gia"})
        assert kq["ok"] is False and "khoá" in kq["loi"], kq
        assert m._phien.lay("may-gia-1") is None, "phiên lệch khoá phải bị ngắt"
        assert phien.so_lenh == 0, "lệnh không được tới tay agent giữ khoá cũ"
        t.cancel()
    chay(_trong_vai(QT, kb))


# ── M2: module tự kiểm vai người gọi ở CẢ hai thực thể ──────────────────────

def test_m2_khach_bi_tu_choi_moi_hanh_dong_quan_ly(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        van = m._so.lay("may-gia-1")["dau_van"]
        with _vai(KHACH):
            for hd, ts in (
                    ("dang_ky", {"ten_may": "may-cua-khach",
                                 "duong_dan": ["/"], "co_quyen_ghi": True,
                                 "co_quyen_nguon": True,
                                 "co_quyen_ghi_man_hinh": True}),
                    ("thu_hoi", {"ten_may": "may-gia-1"}),
                    ("xoa", {"ten_may": "may-gia-1", "go_tren_may": False}),
                    ("xoay_khoa", {"ten_may": "may-gia-1"}),
                    ("doi_quyen", {"ten_may": "may-gia-1",
                                   "co_quyen_ghi": True})):
                kq = await m.thuc_hien("maytram.dang_ky", hd, ts)
                assert kq["ok"] is False and "quản trị" in kq["loi"], (hd, kq)
        # KHÔNG tác dụng phụ nào xảy ra
        assert [d["ten"] for d in m._so.danh_sach()] == ["may-gia-1"]
        bg = m._so.lay("may-gia-1")
        assert bg["dau_van"] == van and bg["da_thu_hoi"] is False
        assert bg["co_quyen_ghi"] is False
        assert khoa  # khoá gốc vẫn là khoá duy nhất đã phát
    chay(_trong_vai(QT, kb))


def test_m2_khach_bi_tu_choi_moi_lenh_may(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1", co_quyen_ghi=True,
                              co_quyen_nguon=True, co_quyen_ghi_man_hinh=True)
        phien, t = await _noi(
            m, "may-gia-1", khoa,
            quyen={"ghi": True, "nguon": True, "ghi_man_hinh": True})
        pa = _pa()
        with _vai(KHACH):
            for lenh in pa.LENH_DONG:
                kq = await m.thuc_hien(
                    "maytram.may-gia-1", lenh,
                    {"duong_dan": "/du-lieu-gia", "noi_dung": "x"})
                assert kq["ok"] is False and "quản trị" in kq["loi"], (lenh, kq)
        assert phien.so_lenh == 0, "không lệnh nào được tới agent"
        t.cancel()
    chay(_trong_vai(QT, kb))


def test_m2_thieu_ngu_canh_bi_tu_choi_dong_khi_loi(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        # KHÔNG đặt ngữ cảnh nào — như lõi gọi thiếu ngữ cảnh: đóng khi lỗi.
        kq = await m.thuc_hien("maytram.dang_ky", "dang_ky",
                               {"ten_may": "may-x", "duong_dan": DUONG_DAN_GIA})
        assert kq["ok"] is False
        assert m._so.danh_sach() == []
    chay(kb())


def test_m2_noi_bo_duoc_phep(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        kq = await m.thuc_hien("maytram.dang_ky", "dang_ky",
                               {"ten_may": "may-gia-1",
                                "duong_dan": DUONG_DAN_GIA})
        assert kq["ok"] is True  # noi_bo là đường lõi/tác vụ nền — được phép
    chay(_trong_vai(NOI_BO, kb))


# ── M3: trần đầu ra theo TỔNG + độ sâu + không ném ──────────────────────────

def test_m3_tran_tinh_theo_tong_ky_tu(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        m._tran = 1024
        to = {f"k{i}": "x" * 1024 for i in range(2000)}  # ~2 MB nếu chỉ cắt từng chuỗi
        kq = m._boc_ket_qua({"ok": True, "ket_qua": to}, "may-gia-1", "doc_tep")
        assert kq["ok"] is True
        tong = len(json.dumps(kq["ket_qua"], ensure_ascii=False))
        assert tong <= 1024 + 128, f"trần 1024 mà trả {tong} ký tự"
        assert kq["ket_qua"].get("bi_cat") is True
    chay(_trong_vai(QT, kb))


def test_m3_long_3000_tang_khong_nem_ngoai_le(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        sau: dict = {}
        cur = sau
        for _ in range(3000):
            cur["a"] = {}
            cur = cur["a"]
        # Trước vá: RecursionError thoát khỏi _boc_ket_qua/thuc_hien. Bắt ở
        # đây và đổi thành AssertionError NÔNG (from None): pytest dựng báo
        # cáo cho RecursionError ~1000 khung hình bị treo (đo thật khi soát).
        try:
            kq = m._boc_ket_qua({"ok": True, "ket_qua": sau},
                                "may-gia-1", "doc_tep")
        except RecursionError:
            raise AssertionError(
                "_boc_ket_qua ném RecursionError với kết quả lồng 3000 tầng"
            ) from None
        assert isinstance(kq, dict) and "ok" in kq
        json.dumps(kq)  # kết quả bọc xong phải tuần tự hoá được, không đệ quy sâu
    chay(_trong_vai(QT, kb))


def test_m3_ket_qua_to_qua_duong_lenh_that_bi_cat_tong(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu, tran_dau_ra="1024")
        khoa = await _dang_ky(m, "may-gia-1")
        to = {"ok": True, **{f"k{i}": "x" * 1024 for i in range(2000)}}
        _, t = await _noi(m, "may-gia-1", khoa, ket_qua_gia={"doc_tep": to})
        kq = await m.thuc_hien("maytram.may-gia-1", "doc_tep",
                               {"duong_dan": "/du-lieu-gia/a.txt"})
        assert kq["ok"] is True
        tong = len(json.dumps(kq["ket_qua"], ensure_ascii=False))
        assert tong <= 1024 + 128, f"trần 1024 mà trả {tong} ký tự"
        t.cancel()
    chay(_trong_vai(QT, kb))


# ── M4: thoi_gian hữu hạn; kiểm giờ TRƯỚC nonce ─────────────────────────────

def test_m4_thoi_gian_nan_bi_tu_choi_ngay_o_khung_chao(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        phien, loi, thach = await _bat_tay_tay(
            m, "may-gia-1", khoa, thoi_gian=float("nan"))
        assert phien is None and isinstance(loi, _pa().BatTayLoi), loi
        assert thach is None, "NaN phải bị từ chối TRƯỚC khi gửi thách thức"
    chay(_trong_vai(QT, kb))


def test_m4_thoi_gian_vo_cuc_va_bool_bi_tu_choi(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        for tg in (float("inf"), float("-inf"), True, "1234567890"):
            phien, loi, thach = await _bat_tay_tay(
                m, "may-gia-1", khoa, thoi_gian=tg)
            assert phien is None and isinstance(loi, _pa().BatTayLoi), (tg, loi)
            assert thach is None
    chay(_trong_vai(QT, kb))


def test_m4_khung_chao_sai_gio_khong_dot_nonce(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        nonce = secrets.token_hex(16)
        phien, loi, _ = await _bat_tay_tay(
            m, "may-gia-1", khoa, thoi_gian=float("nan"), nonce=nonce)
        assert phien is None  # bị từ chối vì giờ NaN
        # Dùng LẠI đúng nonce đó với giờ đúng → phải bắt tay được (nonce
        # không bị đốt bởi khung chào sai giờ của kẻ không có khoá).
        phien, loi, _ = await _bat_tay_tay(m, "may-gia-1", khoa, nonce=nonce)
        assert loi is None and phien is not None, loi
        await m._phien.ngat("may-gia-1", "hết test")
    chay(_trong_vai(QT, kb))


# ── M5: tên máy không trùng không gian tên quản lý ───────────────────────────

def test_m5_ten_trung_thuc_the_quan_ly_bi_tu_choi(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        for ten in ("dang_ky", "thu_hoi", "xoa", "xoay_khoa", "doi_quyen"):
            kq = await m.thuc_hien("maytram.dang_ky", "dang_ky",
                                   {"ten_may": ten,
                                    "duong_dan": DUONG_DAN_GIA})
            assert kq["ok"] is False, f"tên «{ten}» phải bị từ chối: {kq}"
            with pytest.raises(ValueError):
                m._so.dang_ky(ten, "", DUONG_DAN_GIA)
        assert m._so.danh_sach() == []
        ids = [e["id"] for e in await m.thuc_the(None)]
        assert len(ids) == len(set(ids)) == 1  # chỉ thực thể quản lý, không trùng
    chay(_trong_vai(QT, kb))


# ── M6: doi_quyen bat=false ngắt phiên đang sống ─────────────────────────────

def test_m6_doi_quyen_tat_may_ngat_phien_ngay(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        _, t = await _noi(m, "may-gia-1", khoa)
        assert m._phien.lay("may-gia-1") is not None
        kq = await m.thuc_hien("maytram.dang_ky", "doi_quyen",
                               {"ten_may": "may-gia-1", "bat": False})
        assert kq["ok"] is True
        assert m._phien.lay("may-gia-1") is None, (
            "bat=false phải ngắt phiên đang sống ngay (cùng đường M1)")
        kq = await m.thuc_hien("maytram.may-gia-1", "liet_ke",
                               {"duong_dan": "/du-lieu-gia"})
        assert kq["ok"] is False and "tắt" in kq["loi"]
        t.cancel()
    chay(_trong_vai(QT, kb))
