"""Test rơ-le luồng chịu lỗi ``ro_le.luong_chiu_loi`` — NV153 đợt A.

Mỗi kịch bản của máy chủ googlevideo giả (ho_tro_test/googlevideo_gia.py):
cắt giữa khúc / trước-sau ranh giới 4MiB / 3 lần liên tiếp / 403 khi mở lại
(phải GIẢI LẠI link) / 200 thay vì 206 / Content-Range lệch / đổi tổng cỡ /
quá 5 lần (luồng NGẮN + log luong_that_bai). Toàn vẹn byte kiểm bằng sha256.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import tracemalloc
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import ro_le

TONG = 10 * 1024 * 1024  # 10 MiB -> 3 khúc 4 MiB


@pytest.fixture(scope="module")
def du_lieu():
    return os.urandom(TONG)


class _Ban:
    """Obj thượng nguồn theo hợp đồng ro_le.mo_khuc (đọc qua urllib)."""

    def __init__(self, phan_hoi=None, ma: int = 200, loi_403_410: bool = False):
        self._phan_hoi = phan_hoi
        self.ma_trang_thai = ma
        self.loai_loi_403_410 = loi_403_410
        self.headers = dict(phan_hoi.headers.items()) if phan_hoi is not None else {}

    def read(self, n: int) -> bytes:
        return self._phan_hoi.read(n)

    def close(self) -> None:
        try:
            if self._phan_hoi is not None:
                self._phan_hoi.close()
        except Exception:
            pass


def _mo_khuc_cho(hop):
    """mo_khuc async (tu, den) dùng URL hiện tại trong hop['url']."""

    async def _mo(tu: int, den):
        req = Request(hop["url"], headers={"Range": f"bytes={tu}-{den}"})
        try:
            phan_hoi = await asyncio.to_thread(urlopen, req, timeout=10)
        except HTTPError as exc:
            return _Ban(ma=exc.code or 500, loi_403_410=(exc.code in {403, 404, 410}))
        except OSError as exc:
            raise exc
        return _Ban(phan_hoi, ma=getattr(phan_hoi, "status", 200) or 200)

    return _mo


def _doc_tong(headers) -> int | None:
    cr = str(headers.get("Content-Range") or "")
    if "/" not in cr:
        return None
    tong = cr.rsplit("/", 1)[-1].strip()
    return int(tong) if tong.isdigit() else None


async def _ngu_nhanh(giay: float) -> None:
    """Bỏ qua backoff trong test (không ngủ 0.5/1/2/2/2 thật)."""
    return None


class _NhatKy:
    def __init__(self):
        self.su_kien: list = []

    def __call__(self, ten: str, **truong):
        self.su_kien.append((ten, truong))

    def dem(self, ten: str) -> int:
        return sum(1 for t, _ in self.su_kien if t == ten)


async def _chay(hop, kich_ban, *, kiem_dung=None):
    """Chạy relay tới hết; trả (bytes | None nếu hết cách cứu, nhat_ky)."""
    log = _NhatKy()

    async def _khong_giai():
        raise AssertionError("khong duoc giai lai link o kich ban " + kich_ban)

    gen = ro_le.luong_chiu_loi(
        _mo_khuc_cho(hop), 0, None,
        giai_lai_link=_khong_giai,
        doc_tong=_doc_tong,
        ghi_log=log,
        kiem_dung=kiem_dung,
        ngu=_ngu_nhanh,
    )
    try:
        return b"".join([m async for m in gen]), log
    except ro_le.LoiNguonKhongCuuDuoc:
        return None, log


def test_cat_giua_khuc_noi_lai_du_byte(may_chu_gia, du_lieu):
    may = may_chu_gia("cat_giua_khuc", du_lieu)
    got, log = asyncio.run(_chay({"url": may.url}, "cat_giua_khuc"))
    assert got is not None
    assert hashlib.sha256(got).digest() == hashlib.sha256(du_lieu).digest()
    assert log.dem("luong_dut_noi_lai") >= 1
    assert log.dem("luong_hoan_tat") == 1


def test_cat_truoc_ranh_gioi_4mib(may_chu_gia, du_lieu):
    may = may_chu_gia("cat_truoc_ranh_gioi_4MiB", du_lieu)
    got, log = asyncio.run(_chay({"url": may.url}, "cat_truoc_ranh_gioi_4MiB"))
    assert got is not None
    assert hashlib.sha256(got).digest() == hashlib.sha256(du_lieu).digest()
    assert log.dem("luong_dut_noi_lai") >= 1
    assert log.dem("luong_hoan_tat") == 1


def test_cat_sau_ranh_gioi_4mib(may_chu_gia, du_lieu):
    may = may_chu_gia("cat_sau_ranh_gioi", du_lieu)
    got, log = asyncio.run(_chay({"url": may.url}, "cat_sau_ranh_gioi"))
    assert got is not None
    assert hashlib.sha256(got).digest() == hashlib.sha256(du_lieu).digest()
    assert log.dem("luong_dut_noi_lai") >= 1


def test_3_lan_lien_tiep(may_chu_gia, du_lieu):
    may = may_chu_gia("3_lan_lien_tiep", du_lieu)
    got, log = asyncio.run(_chay({"url": may.url}, "3_lan_lien_tiep"))
    assert got is not None
    assert hashlib.sha256(got).digest() == hashlib.sha256(du_lieu).digest()
    assert log.dem("luong_dut_noi_lai") == 3


def test_403_khi_mo_lai_phai_giai_lai_link(may_chu_gia, du_lieu):
    may = may_chu_gia("403_khi_mo_lai", du_lieu)
    hop = {"url": may.url, "so_lan_giai": 0}
    log = _NhatKy()

    async def _giai_lai_link():
        hop["so_lan_giai"] += 1
        hop["url"] = may.url + "&moi=1"  # link mới sau khi giải lại

    async def _go():
        gen = ro_le.luong_chiu_loi(
            _mo_khuc_cho(hop), 0, None,
            giai_lai_link=_giai_lai_link,
            doc_tong=_doc_tong,
            ghi_log=log,
            ngu=_ngu_nhanh,
        )
        try:
            return b"".join([m async for m in gen])
        except ro_le.LoiNguonKhongCuuDuoc:
            return None

    got = asyncio.run(_go())
    assert hop["so_lan_giai"] >= 1, "403 khi mở lại mà không gọi giải lại link"
    assert got is not None
    assert hashlib.sha256(got).digest() == hashlib.sha256(du_lieu).digest()


def test_200_thay_vi_206_tinh_1_that_bai_van_du_byte(may_chu_gia, du_lieu):
    may = may_chu_gia("200_thay_vi_206", du_lieu)
    got, log = asyncio.run(_chay({"url": may.url}, "200_thay_vi_206"))
    assert got is not None
    assert hashlib.sha256(got).digest() == hashlib.sha256(du_lieu).digest()
    assert log.dem("luong_dut_noi_lai") == 1, log.su_kien


def test_content_range_lech_tinh_1_that_bai(may_chu_gia, du_lieu):
    may = may_chu_gia("content_range_lech", du_lieu)
    got, log = asyncio.run(_chay({"url": may.url}, "content_range_lech"))
    assert got is not None
    assert hashlib.sha256(got).digest() == hashlib.sha256(du_lieu).digest()
    assert log.dem("luong_dut_noi_lai") == 1, log.su_kien


def test_doi_tong_co_tinh_that_bai(may_chu_gia, du_lieu):
    may = may_chu_gia("doi_tong_co", du_lieu)
    got, log = asyncio.run(_chay({"url": may.url}, "doi_tong_co"))
    assert got is not None
    assert hashlib.sha256(got).digest() == hashlib.sha256(du_lieu).digest()
    assert log.dem("luong_dut_noi_lai") == 1, log.su_kien


def test_qua_5_lan_luong_ngan_va_log_that_bai(may_chu_gia, du_lieu):
    may = may_chu_gia("qua_5_lan", du_lieu)
    got, log = asyncio.run(_chay({"url": may.url}, "qua_5_lan"))
    assert got is None, "hết 5 lần phải raise LoiNguonKhongCuuDuoc"
    that_bai = [truong for t, truong in log.su_kien if t == "luong_that_bai"]
    assert len(that_bai) == 1
    assert that_bai[0]["lan_thu"] == 5
    # 4 lần đầu log dut_noi_lai, lần 5 log that_bai
    assert log.dem("luong_dut_noi_lai") == 4
    assert log.dem("luong_hoan_tat") == 0


def test_doc_cham_bo_nho_khong_phinh(may_chu_gia, du_lieu):
    """Loa đọc chậm từng miếng: relay không được đệm cả luồng vào RAM."""
    may = may_chu_gia("binh_thuong", du_lieu)
    log = _NhatKy()

    async def _go():
        gen = ro_le.luong_chiu_loi(
            _mo_khuc_cho({"url": may.url}), 0, None,
            giai_lai_link=None,  # không cắt ở kịch bản này nên không cần
            doc_tong=_doc_tong,
            ghi_log=log,
            ngu=_ngu_nhanh,
        )
        bam = hashlib.sha256()
        so_mieng = 0
        async for mieng in gen:
            bam.update(mieng)
            so_mieng += 1
            await asyncio.sleep(0.001)  # loa đọc chậm
        return bam.digest(), so_mieng

    tracemalloc.start()
    try:
        digest, so_mieng = asyncio.run(_go())
        _, dinh = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert digest == hashlib.sha256(du_lieu).digest()
    assert so_mieng > 100, f"chỉ {so_mieng} miếng — relay đang đệm gộp?"
    assert dinh < 2 * 1024 * 1024, f"đỉnh bộ nhớ {dinh} B — relay đệm cả luồng?"
