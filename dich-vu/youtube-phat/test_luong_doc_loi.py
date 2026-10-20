"""Test NV153 đợt D1 — lỗ hổng test ở đường rơ-le `ro_le.luong_chiu_loi`.

Đo của điều phối: gây đột biến thay khối ``except Exception`` (lỗi giữa khúc)
trong `ro_le.py` bằng ``return`` (bỏ cuộc im lặng khi `read()` ném lỗi giữa
khúc) → cả 66 test cũ vẫn XANH, vì test cũ chỉ có kiểu «phản hồi ngắn hơn đã
xin», không có kiểu `read()` NÉM `OSError`/`TimeoutError`/`ConnectionResetError`.

Tệp này thêm đúng kiểu đó: nguồn giả làm `read()` NÉM LỖI sau N byte, khẳng
định luồng ra ĐỦ byte + đúng nội dung, nối lại từ ĐÚNG offset, log
`luong_dut_noi_lai`; quá 5 lần → ngắt + log `luong_that_bai`; bên nhận đóng
giữa chừng → KHÔNG nối lại. Mọi test ở đây phải ĐỎ trên đột biến «bỏ cuộc im
lặng» của điều phối.
"""
from __future__ import annotations

import asyncio

import pytest

import ro_le

TONG = 1000
DU_LIEU = bytes(i % 251 for i in range(TONG))


class BanDocLoi:
    """Bản thượng nguồn giả: đọc được tới mốc thì `read()` NÉM lỗi đã hẹn.

    `ke` là một trong:
    - None                     → đọc sạch tới hết đoạn.
    - ("sau", moc, exc)        → trả byte tới `moc` rồi lần đọc kế ném `exc`.
    - ("ngay", exc)            → lần đọc đầu tiên ném `exc` ngay.
    """

    def __init__(self, doan: bytes, tu: int, tong: int, ke=None):
        self._doan = doan
        self._vi_tri = 0
        self._ke = ke
        self.headers = {
            "Content-Range": f"bytes {tu}-{tu + len(doan) - 1}/{tong}",
        }
        self.ma_trang_thai = 206
        self.loai_loi_403_410 = False
        self.da_dong = False

    def read(self, n: int) -> bytes:
        ke = self._ke
        if ke is not None and ke[0] == "ngay":
            self._ke = None
            raise ke[1]
        if ke is not None and ke[0] == "sau":
            if self._vi_tri >= ke[1]:
                self._ke = None
                raise ke[2]
            n = min(n, ke[1] - self._vi_tri)
        mieng = self._doan[self._vi_tri:self._vi_tri + n]
        self._vi_tri += len(mieng)
        return mieng

    def close(self) -> None:
        self.da_dong = True


class BoDem:
    """gom các lần mở khúc / giải lại link / ngủ / log để khẳng định."""

    def __init__(self, du_lieu: bytes, cac_ke: list):
        self.du_lieu = du_lieu
        self.cac_ke = list(cac_ke)
        self.lan_mo: list[tuple[int, int]] = []
        self.so_giai_lai = 0
        self.cac_lan_ngu: list[float] = []
        self.nhat_ky: list[tuple[str, dict]] = []

    def mo_khuc(self, tu: int, den: int):
        self.lan_mo.append((tu, den))
        i = len(self.lan_mo) - 1
        ke = self.cac_ke[i] if i < len(self.cac_ke) else None
        return BanDocLoi(self.du_lieu[tu:den + 1], tu, len(self.du_lieu), ke)

    async def giai_lai_link(self) -> None:
        self.so_giai_lai += 1

    async def ngu(self, giay: float) -> None:
        self.cac_lan_ngu.append(giay)

    def ghi_log(self, su_kien: str, **truong) -> None:
        self.nhat_ky.append((su_kien, truong))

    def su_kien(self, ten: str) -> list[dict]:
        return [t for (s, t) in self.nhat_ky if s == ten]


def _doc_tong(headers) -> int | None:
    cr = str(headers.get("Content-Range") or "")
    if "/" not in cr:
        return None
    tong = cr.rsplit("/", 1)[-1].strip()
    return int(tong) if tong.isdigit() else None


def _chay(bo: BoDem):
    """Chạy rơ-le tới hết, trả bytes đã nhận."""
    async def _thu() -> bytes:
        ra = b""
        async for mieng in ro_le.luong_chiu_loi(
            bo.mo_khuc, 0, None,
            giai_lai_link=bo.giai_lai_link, doc_tong=_doc_tong,
            ghi_log=bo.ghi_log, ngu=bo.ngu,
        ):
            ra += mieng
        return ra

    return asyncio.run(_thu())


def test_read_nem_connection_reset_giua_khuc_noi_lai_dung_offset():
    bo = BoDem(DU_LIEU, [("sau", 300, ConnectionResetError("đứt giữa khúc"))])
    ra = _chay(bo)
    assert ra == DU_LIEU
    # Nối lại từ ĐÚNG byte đã gửi (300), không từ đầu, không lệch.
    assert [tu for (tu, _den) in bo.lan_mo] == [0, 300]
    dut = bo.su_kien("luong_dut_noi_lai")
    assert len(dut) == 1
    assert dut[0]["loai_loi"] == "ConnectionResetError"
    assert dut[0]["da_gui"] == 300
    assert bo.su_kien("luong_that_bai") == []
    assert bo.cac_lan_ngu == [ro_le.CHO_NOI_LAI[0]]


def test_read_nem_timeout_giua_khuc_noi_lai_dung_offset():
    bo = BoDem(DU_LIEU, [("sau", 512, TimeoutError("đọc quá giờ"))])
    ra = _chay(bo)
    assert ra == DU_LIEU
    assert [tu for (tu, _den) in bo.lan_mo] == [0, 512]
    dut = bo.su_kien("luong_dut_noi_lai")
    assert len(dut) == 1
    assert dut[0]["loai_loi"] == "TimeoutError"
    assert dut[0]["da_gui"] == 512


def test_khuc_noi_lai_nem_ngay_mieng_dau_thi_noi_tiep():
    bo = BoDem(DU_LIEU, [
        ("sau", 100, ConnectionResetError("đứt lần 1")),
        ("ngay", TimeoutError("khúc nối lại ném ngay miếng đầu")),
        None,
    ])
    ra = _chay(bo)
    assert ra == DU_LIEU
    # Lần mở 2 ném ngay ở offset 100 → lần mở 3 vẫn từ 100, không nhảy, không lùi.
    assert [tu for (tu, _den) in bo.lan_mo] == [0, 100, 100]
    dut = bo.su_kien("luong_dut_noi_lai")
    assert [d["loai_loi"] for d in dut] == ["ConnectionResetError", "TimeoutError"]
    assert [d["da_gui"] for d in dut] == [100, 100]
    assert bo.su_kien("luong_that_bai") == []


def test_qua_5_lan_doc_loi_thi_ngat_va_log_that_bai():
    bo = BoDem(DU_LIEU, [("sau", 10, ConnectionResetError("đứt mãi"))] * 8)

    async def _thu():
        ra = b""
        async for mieng in ro_le.luong_chiu_loi(
            bo.mo_khuc, 0, None,
            giai_lai_link=bo.giai_lai_link, doc_tong=_doc_tong,
            ghi_log=bo.ghi_log, ngu=bo.ngu,
        ):
            ra += mieng
        return ra

    with pytest.raises(ro_le.LoiNguonKhongCuuDuoc):
        asyncio.run(_thu())
    # 5 lần mở (1 đầu + 4 nối lại), lần thất bại thứ 5 thì ngắt.
    assert len(bo.lan_mo) == 5
    assert [tu for (tu, _den) in bo.lan_mo] == [0, 10, 20, 30, 40]
    assert len(bo.su_kien("luong_dut_noi_lai")) == 4
    that_bai = bo.su_kien("luong_that_bai")
    assert len(that_bai) == 1
    assert that_bai[0]["lan_thu"] == 5
    assert that_bai[0]["loai_loi"] == "ConnectionResetError"
    # Ngủ backoff đúng 4 lần đầu; lần 5 raise ngay, không ngủ.
    assert bo.cac_lan_ngu == list(ro_le.CHO_NOI_LAI[:4])


def test_ben_nhan_dong_giua_chung_khong_noi_lai():
    bo = BoDem(DU_LIEU, [None])

    async def _thu():
        gen = ro_le.luong_chiu_loi(
            bo.mo_khuc, 0, None,
            giai_lai_link=bo.giai_lai_link, doc_tong=_doc_tong,
            ghi_log=bo.ghi_log, ngu=bo.ngu,
        )
        mieng_dau = await gen.__anext__()
        assert mieng_dau  # đã nhận được dữ liệu thật
        await gen.aclose()  # bên nhận (loa) đóng giữa chừng

    asyncio.run(_thu())
    # Không mở lại khúc nào, không giải lại link, không log đứt/nối lại.
    assert len(bo.lan_mo) == 1
    assert bo.so_giai_lai == 0
    assert bo.su_kien("luong_dut_noi_lai") == []
    assert bo.su_kien("luong_that_bai") == []
    assert bo.cac_lan_ngu == []
