"""Kiểm tra chặn trần bộ đệm SSE và bỏ quét lại bậc hai (app/tuyen.py::_luong_sse_chuan_hoa).

1. Trần bộ đệm: biến môi trường WEB2API_SSE_DEM_TOI_DA_KB (mặc định 1024 KiB = 1 MiB), giá trị xấu về mặc định.
2. Vượt trần: luồng 5 MiB không có dòng trống phát nguyên văn, không mất byte nào, bộ đệm không vượt quá trần + 1 khúc,
   đúng 1 cảnh báo nhật ký (không chứa nội dung luồng).
3. Bỏ quét lại bậc hai: quét từ max(0, len_cu - 3) cho kết quả giống hệt bản quét từ đầu trên nhiều cách cắt khúc ngẫu nhiên
   và mọi vị trí cắt ranh giới CRLF; hiệu năng O(N) thay vì O(N^2) trên 20000 khúc nhỏ.
"""

import asyncio
import logging
import math
import random
import time
import pytest

from app import tuyen
from app.tuyen import (
    _HET_SU_KIEN,
    _chuan_hoa_khuc_stream,
    _dong_luong_module,
    _khuc_ke,
    _luong_sse_chuan_hoa,
    sse_dem_toi_da_bytes,
)


class _LuongDongGia:
    def __init__(self, cac_khuc: list[bytes]):
        self.cac_khuc = list(cac_khuc)
        self.da_dong = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.cac_khuc:
            raise StopAsyncIteration
        return self.cac_khuc.pop(0)

    async def aclose(self):
        self.da_dong = True


async def _luong_sse_tham_chieu_quet_tu_dau(iterator, giu_giay: float, dem_toi_da: int | None = None):
    """Cài đặt tham chiếu: quét ranh giới từ 0 mỗi khúc mới (thuật toán cũ) để đối chứng."""
    if dem_toi_da is None:
        dem_toi_da = sse_dem_toi_da_bytes()
    co_tool: set[int] = set()
    it = iterator.__aiter__()
    cho: asyncio.Task | None = None
    dem = b""
    canh_bao_tran = False
    try:
        while True:
            if cho is None:
                cho = asyncio.create_task(_khuc_ke(it))
            xong, _ = await asyncio.wait({cho}, timeout=giu_giay)
            if not xong:
                if not dem:
                    yield tuyen._KEEP_ALIVE
                continue
            tac_vu, cho = cho, None
            try:
                khuc = tac_vu.result()
            except StopAsyncIteration:
                break
            dem += khuc.encode("utf-8") if isinstance(khuc, str) else khuc
            # Cài đặt cũ: quét từ đầu (pos = 0)
            while (het := _HET_SU_KIEN.search(dem)) is not None:
                su_kien, dem = dem[:het.end()], dem[het.end():]
                yield _chuan_hoa_khuc_stream(su_kien, co_tool)
            if dem and not dem.strip(b"\r\n"):
                yield dem
                dem = b""
            elif len(dem) > dem_toi_da:
                if not canh_bao_tran:
                    tuyen.log.warning("Bộ đệm SSE vượt trần %d byte, phát nguyên văn", dem_toi_da)
                    canh_bao_tran = True
                yield dem
                dem = b""
        if dem:
            yield dem
    finally:
        await asyncio.shield(_dong_luong_module(cho, it))


# ── 1. Biến môi trường WEB2API_SSE_DEM_TOI_DA_KB ────────────────────────────

def test_bien_moi_truong_sse_dem_toi_da_kb_mac_dinh_va_xau(monkeypatch):
    monkeypatch.delenv("WEB2API_SSE_DEM_TOI_DA_KB", raising=False)
    assert sse_dem_toi_da_bytes() == 1024 * 1024

    monkeypatch.setenv("WEB2API_SSE_DEM_TOI_DA_KB", "2048")
    assert sse_dem_toi_da_bytes() == 2048 * 1024

    monkeypatch.setenv("WEB2API_SSE_DEM_TOI_DA_KB", "512")
    assert sse_dem_toi_da_bytes() == 512 * 1024

    # Giá trị xấu: chữ, <= 0, nan, inf, rỗng -> phải về mặc định 1024 KiB, KHÔNG được tắt trần
    for xau in ["abc", "khong", "-1", "-1024", "0", "0.0", "nan", "inf", "-inf", "", "   "]:
        monkeypatch.setenv("WEB2API_SSE_DEM_TOI_DA_KB", xau)
        assert sse_dem_toi_da_bytes() == 1024 * 1024, f"Giá trị xấu {xau!r} phải về mặc định 1024 KiB"


# ── 2. Luồng 5 MiB không có dòng trống: chặn trần, không mất byte, 1 cảnh báo ─

def test_sse_luong_5mib_khong_dong_trong_chan_tran_va_dung_mot_canh_bao(caplog):
    """Luồng 5 MiB không có dòng trống: toàn bộ byte ra đủ và đúng thứ tự,
    bộ nhớ đệm không vượt trần quá một khúc, có đúng 1 cảnh báo không chứa nội dung."""
    kich_thuoc_khuc = 64 * 1024  # 64 KiB = 65,536 bytes
    so_khuc = 80  # 80 * 64 KiB = 5120 KiB = 5 MiB = 5,242,880 bytes
    mau_du_lieu = [
        ((f"KHUC_{i:04d}_".encode("ascii") * (kich_thuoc_khuc // 10 + 1))[:kich_thuoc_khuc])
        for i in range(so_khuc)
    ]
    toan_bo_vao = b"".join(mau_du_lieu)
    assert len(toan_bo_vao) == 5 * 1024 * 1024

    tran_bytes = 1024 * 1024  # 1 MiB

    async def chay():
        luong = _LuongDongGia(mau_du_lieu)
        with caplog.at_level(logging.WARNING):
            cac_khuc_ra = [k async for k in _luong_sse_chuan_hoa(luong, 10.0, dem_toi_da=tran_bytes)]
        return cac_khuc_ra

    cac_khuc_ra = asyncio.run(chay())

    # 1. Toàn bộ byte ra đủ và đúng thứ tự
    assert b"".join(cac_khuc_ra) == toan_bo_vao

    # 2. Bộ nhớ đệm không vượt trần quá một khúc:
    # Vì mỗi lần vượt trần phát nguyên văn dem và reset dem = b"", nên mỗi khúc phát ra <= tran_bytes + kich_thuoc_khuc
    assert len(cac_khuc_ra) > 1, "Phải phát nhiều khúc do chạm trần, không được gom hết tới cuối"
    for k in cac_khuc_ra:
        assert len(k) <= tran_bytes + kich_thuoc_khuc, f"Khúc phát dài {len(k)} vượt trần + 1 khúc"

    # 3. Có đúng 1 cảnh báo nhật ký cho cả lượt gọi
    canh_bao = [rec for rec in caplog.records if rec.levelno >= logging.WARNING and "SSE" in rec.message]
    assert len(canh_bao) == 1, f"Phải có đúng 1 cảnh báo cho lượt gọi, nhận được: {len(canh_bao)}"

    # 4. Cảnh báo KHÔNG chứa nội dung luồng
    assert "KHUC_" not in canh_bao[0].message


def test_sse_tran_bo_dem_sau_do_gap_su_kien_hop_le_van_chuan_hoa_dung(caplog):
    """Dữ liệu tràn trần trước, sau đó có sự kiện SSE chuẩn chứa finish_reason='stop' -> sự kiện sau vẫn được chuẩn hoá."""
    tran_bytes = 10 * 1024  # 10 KiB
    # Hai khúc rác mỗi khúc > 10 KiB -> cả hai lần đều vượt trần và bị phát nguyên văn, dem quay về b""
    rac_1 = b"X" * (10 * 1024 + 100)
    rac_2 = b"Y" * (10 * 1024 + 200)
    su_kien_tool = b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"id":"1"}]}}]}\n\n'
    su_kien_stop = b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    su_kien_done = b"data: [DONE]\n\n"

    cac_khuc_vao = [rac_1, rac_2, su_kien_tool, su_kien_stop, su_kien_done]

    async def chay():
        luong = _LuongDongGia(cac_khuc_vao)
        with caplog.at_level(logging.WARNING):
            return [k async for k in _luong_sse_chuan_hoa(luong, 10.0, dem_toi_da=tran_bytes)]

    cac_khuc_ra = asyncio.run(chay())

    # Ghép lại phần rác ban đầu nguyên vẹn
    du_lieu_ra = b"".join(cac_khuc_ra)
    assert du_lieu_ra.startswith(rac_1 + rac_2)

    # Sự kiện sau đó được chuẩn hoá đúng
    assert b'"finish_reason": "tool_calls"' in du_lieu_ra
    assert b"data: [DONE]\n\n" in du_lieu_ra

    # Đúng 1 cảnh báo
    canh_bao = [rec for rec in caplog.records if rec.levelno >= logging.WARNING and "SSE" in rec.message]
    assert len(canh_bao) == 1


# ── 3. So sánh quét ranh giới mới với cài đặt tham chiếu quét-từ-đầu ─────────

@pytest.mark.parametrize("vi_tri_cat", [
    (b"\r", b"\n\r\n"),      # ranh giới bị cắt kiểu \r | \n\r\n
    (b"\r\n", b"\r\n"),      # ranh giới bị cắt kiểu \r\n | \r\n
    (b"\r\n\r", b"\n"),      # ranh giới bị cắt kiểu \r\n\r | \n
    (b"\n", b"\n"),          # ranh giới bị cắt kiểu \n | \n
    (b"\r\n", b"\n"),        # ranh giới bị cắt kiểu \r\n | \n
])
def test_so_sanh_ranh_gioi_bi_cat_giua_hai_khuc(vi_tri_cat):
    nua_truoc, nua_sau = vi_tri_cat
    su_kien_1 = b'data: {"choices":[{"index":0,"delta":{"content":"A"}}]}' + nua_truoc
    su_kien_2 = nua_sau + b'data: {"choices":[{"index":0,"delta":{"content":"B"}}]}\n\n'

    vao = [su_kien_1, su_kien_2]

    async def chay_so_sanh():
        ra_moi = [k async for k in _luong_sse_chuan_hoa(_LuongDongGia(vao), 10.0)]
        ra_cu = [k async for k in _luong_sse_tham_chieu_quet_tu_dau(_LuongDongGia(vao), 10.0)]
        return ra_moi, ra_cu

    ra_moi, ra_cu = asyncio.run(chay_so_sanh())
    assert ra_moi == ra_cu


def test_so_sanh_ngau_nhien_nhieu_cach_cat_khuc():
    """Tạo chuỗi SSE đa dạng và cắt thành nhiều khúc ngẫu nhiên (hạt giống cố định),
    kết quả của bản tối ưu phải GIỐNG HỆT bản tham chiếu quét từ đầu."""
    rng = random.Random(42)

    chuoi_sse = (
        b'data: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}\n\n'
        b': chu thich keep-alive\n\n'
        b'data: {"choices":[{"index":0,"delta":{"content":"Xin "}}]}\r\n\r\n'
        b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"id":"call_1"}]}}]}\n\n'
        b'\n\n'  # dòng trống lẻ
        b'data: {"choices":[{"index":0,"delta":{"content":"chao"}}]}\r\n\n'
        b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        b'data: [DONE]\n\n'
    )

    for i in range(30):
        # Cắt chuỗi thành các khúc kích thước ngẫu nhiên từ 1 đến 15 byte
        vi_tri = 0
        cac_khuc = []
        while vi_tri < len(chuoi_sse):
            buoc = rng.randint(1, 15)
            cac_khuc.append(chuoi_sse[vi_tri:vi_tri + buoc])
            vi_tri += buoc

        async def chay():
            ra_moi = [k async for k in _luong_sse_chuan_hoa(_LuongDongGia(cac_khuc), 10.0)]
            ra_cu = [k async for k in _luong_sse_tham_chieu_quet_tu_dau(_LuongDongGia(cac_khuc), 10.0)]
            return ra_moi, ra_cu

        ra_moi, ra_cu = asyncio.run(chay())
        assert ra_moi == ra_cu, f"Lệch ở lần thử cắt ngẫu nhiên #{i}"


# ── 4. Hiệu năng: bỏ quét bậc hai trên 20000 khúc nhỏ ────────────────────────

def test_hieu_nang_20000_khuc_nho_tuyen_tinh_khong_bac_hai(monkeypatch):
    """20,000 khúc nhỏ (mỗi khúc 50 byte) ghép thành một sự kiện 1 MiB.
    Bản mới chỉ quét từ max(0, len_cu - 3) nên số byte quét là tuyến tính O(N).
    Nếu quét từ 0 (O(N^2)), số byte quét lên tới 10 tỷ byte và test này sẽ đỏ."""
    so_khuc = 20000
    kich_thuoc_khuc = 50
    # 19,999 khúc đầu không có dòng trống; khúc cuối có \n\n
    cac_khuc = [b"a" * kich_thuoc_khuc for _ in range(so_khuc - 1)]
    cac_khuc.append(b"a" * (kich_thuoc_khuc - 2) + b"\n\n")

    tong_byte_quet = 0
    search_goc = _HET_SU_KIEN.search

    class _MauRegexWrapper:
        def __init__(self, goc):
            self._goc = goc

        def search(self, dem, pos=0):
            nonlocal tong_byte_quet
            tong_byte_quet += len(dem) - pos
            return self._goc.search(dem, pos)

    monkeypatch.setattr(tuyen, "_HET_SU_KIEN", _MauRegexWrapper(_HET_SU_KIEN))

    # Đặt trần 2 MiB để chứa đủ 1 MiB sự kiện này
    tran_bytes = 2 * 1024 * 1024

    async def chay():
        bat_dau = time.perf_counter()
        luong = _LuongDongGia(cac_khuc)
        ra = [k async for k in _luong_sse_chuan_hoa(luong, 10.0, dem_toi_da=tran_bytes)]
        tg = time.perf_counter() - bat_dau
        return ra, tg

    ra, thoi_gian = asyncio.run(chay())

    assert len(ra) == 1
    assert len(ra[0]) == so_khuc * kich_thuoc_khuc

    # Bản mới: mỗi khúc chỉ quét ~53 byte -> tổng quét ~ 20000 * 53 ≈ 1.06 MB < 2 MB
    # Bản cũ quét từ 0: tổng quét = 50 * (1 + 2 + ... + 20000) ≈ 10,000,000,000 byte (10 GB)!
    assert tong_byte_quet < 2 * 1024 * 1024, (
        f"Số byte quét regex quá lớn ({tong_byte_quet:,} byte), chứng tỏ đang quét lại bậc hai từ 0!"
    )
    # Thời gian chạy phải nhanh (tuyến tính O(N), tránh flaky do tải máy chủ)
    assert thoi_gian < 5.0, f"Thời gian chạy quá chậm ({thoi_gian:.2f}s)"

