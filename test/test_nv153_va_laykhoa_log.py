"""Test vá bảo mật NV153 — siết `lay_khoa` + chống «cướp» filter log (mẫu nv116).

Việc 1 (siết lay_khoa): hành động `lay_khoa` chỉ trả khoá Integration API
đầy đủ khi người gọi là QUẢN TRỊ (lõi định: danh_tinh loai «quan_tri»/«noi_bo»,
đúng định nghĩa `la_quan_tri` ở app/thiet_bi.py). Khách giữ `thiet_bi:ghi`,
người dùng thường, hay thiếu ngữ cảnh gọi → từ chối, khoá không lộ.

Việc 2 (filter log): mỗi bản module nạp lại dưới tên gói khác phải GIỮ filter
của bản cũ — bản mới chỉ gắn filter của chính mình (mẫu
nv116-bo-sung-cuop-filter.patch). Test nạp 2 bản khác tên, nạp khoá khác nhau,
dòng log chứa khoá của bản NÀO cũng phải bị chặn.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import pathlib
import sys

import pytest

from app import ngu_canh

GOC = pathlib.Path(__file__).resolve().parent.parent
MODULE_PY = GOC / "modules" / "youtube-phat" / "module.py"
MANIFEST = json.loads(
    (GOC / "modules" / "youtube-phat" / "manifest.json").read_text(encoding="utf-8")
)
URL_DICH_VU = "http://127.0.0.1:8713"
ID_THUC_THE = "youtube_phat.dieu_khien"

KHOA_A = "khoa-ban-a-0123456789abcdef"
KHOA_B = "khoa-ban-b-fedcba9876543210"


def tai_module(ten):
    spec = importlib.util.spec_from_file_location(ten, MODULE_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(ten, None)
        raise
    return mod


@pytest.fixture()
def mod():
    return tai_module("mod_yp_va_laykhoa_a")


@pytest.fixture()
def the(mod):
    t = mod.Module(MANIFEST)
    t.nap({"dich_vu_url": URL_DICH_VU, "khoa_dich_vu": KHOA_A})
    return t


class _NguCanh:
    """Đặt ngữ cảnh người gọi của lõi (app/ngu_canh) cho phạm vi test."""

    def __init__(self, danh_tinh):
        self.danh_tinh = danh_tinh
        self._token = None

    def __enter__(self):
        self._token = ngu_canh.dat(self.danh_tinh, nguon="nguoi")
        return self

    def __exit__(self, *a):
        ngu_canh.khoi_phuc(self._token)
        return False


# ── Việc 1: siết lay_khoa ─────────────────────────────────────────────────

def test_lay_khoa_tu_choi_khach_co_thiet_bi_ghi(the):
    with _NguCanh({"loai": "khach", "id": "khoa-khach-1",
                   "pham_vi": ["thiet_bi:ghi"]}):
        kq = asyncio.run(the.thuc_hien(ID_THUC_THE, "lay_khoa", {}))
    assert kq["ok"] is False
    assert KHOA_A not in json.dumps(kq, ensure_ascii=False)


def test_lay_khoa_tu_choi_nguoi_dung_thuong_du_pham_vi_sao(the):
    # Kể cả phạm vi "*", không phải quản trị vẫn bị từ chối (khớp định nghĩa
    # la_quan_tri của lõi — kiểm theo «loai», không theo phạm vi).
    with _NguCanh({"loai": "nguoi_dung", "id": "nguoi_dung:7",
                   "pham_vi": ["*"]}):
        kq = asyncio.run(the.thuc_hien(ID_THUC_THE, "lay_khoa", {}))
    assert kq["ok"] is False
    assert KHOA_A not in json.dumps(kq, ensure_ascii=False)


def test_lay_khoa_tu_choi_khi_thieu_ngu_canh(the):
    # Fail-closed: không có ngữ cảnh gọi → không trả khoá.
    token = ngu_canh.NGU_CANH.set(None)
    try:
        kq = asyncio.run(the.thuc_hien(ID_THUC_THE, "lay_khoa", {}))
    finally:
        ngu_canh.NGU_CANH.reset(token)
    assert kq["ok"] is False
    assert KHOA_A not in json.dumps(kq, ensure_ascii=False)


def test_lay_khoa_quan_tri_duoc_lay_day_du(the):
    with _NguCanh({"loai": "quan_tri", "id": "quan_tri", "pham_vi": ["*"]}):
        kq = asyncio.run(the.thuc_hien(ID_THUC_THE, "lay_khoa", {}))
    assert kq["ok"] is True
    assert kq["khoa"] == KHOA_A


def test_lay_khoa_noi_bo_duoc_lay_day_du(the):
    # Lời gọi nội bộ của lõi (tuần hoàn, tác vụ nền) vẫn được phép.
    with _NguCanh({"loai": "noi_bo", "id": "he_thong", "pham_vi": []}):
        kq = asyncio.run(the.thuc_hien(ID_THUC_THE, "lay_khoa", {}))
    assert kq["ok"] is True
    assert kq["khoa"] == KHOA_A


def test_lay_khoa_quan_tri_khi_chua_cau_hinh_bao_loi(mod):
    t = mod.Module(MANIFEST)
    t.nap({"dich_vu_url": URL_DICH_VU, "khoa_dich_vu": ""})
    with _NguCanh({"loai": "quan_tri", "id": "quan_tri", "pham_vi": ["*"]}):
        kq = asyncio.run(t.thuc_hien(ID_THUC_THE, "lay_khoa", {}))
    assert kq["ok"] is False


# ── Việc 2: filter log không bị «cướp» khi nạp lại dưới tên gói khác ────────

def _bi_chan(logger_name, thong_diep):
    ban_ghi = logging.LogRecord(
        logger_name, logging.WARNING, __file__, 1, thong_diep, (), None)
    return logging.getLogger(logger_name).filter(ban_ghi) is False


def test_filter_log_giu_filter_moi_ban_khi_nap_lai_ten_goi_khac():
    mod_a = tai_module("mod_yp_va_ban_a")
    mod_b = tai_module("mod_yp_va_ban_b")  # nạp lại dưới tên gói khác
    the_a = mod_a.Module(MANIFEST)
    the_a.nap({"dich_vu_url": URL_DICH_VU, "khoa_dich_vu": KHOA_A})
    the_b = mod_b.Module(MANIFEST)
    the_b.nap({"dich_vu_url": URL_DICH_VU, "khoa_dich_vu": KHOA_B})

    # Khoá của BẢN NÀO cũng phải bị chặn, dù đã nạp lại dưới tên khác.
    assert _bi_chan("httpx", f"dòng lạ mang khoá bản A: {KHOA_A}"), \
        "khoá bản cũ lọt log sau khi module nạp lại dưới tên gói khác"
    assert _bi_chan("httpx", f"dòng lạ mang khoá bản B: {KHOA_B}")

    # Dòng sạch vẫn qua.
    assert _bi_chan("httpx", "dòng sạch không chứa bí mật") is False


def test_filter_moi_ban_chi_gan_mot_lan():
    mod_c = tai_module("mod_yp_va_ban_c")
    mod_c2 = tai_module("mod_yp_va_ban_c")  # nạp LẠI cùng tên gói
    lg = logging.getLogger("httpx")
    so_loc = sum(
        1 for f in lg.filters
        if getattr(f, "_youtube_phat_loc", False)
        and type(f).__module__ == "mod_yp_va_ban_c")
    assert so_loc == 1, f"nạp lại cùng tên gói phải giữ đúng 1 filter, có {so_loc}"
