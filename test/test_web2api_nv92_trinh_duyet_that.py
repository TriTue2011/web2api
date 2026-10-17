"""TrinhDuyetThat — bộ điều khiển thật qua dịch vụ trình duyệt (NV92 P1).

Dịch vụ giả trên loopback (đường P1 mới): kiểm chứng TrinhDuyetThat
(module vnc-xa) thực hiện đúng hợp đồng BoDieuKhienTrinhDuyet:
giành → mo_trang/dien/bam/cho/tin_hieu/doc_cookie → nhả; lỗi dịch vụ
→ LoiDichVu chỉ mang mã trạng thái; giá trị điền không vào log;
mọi lời gọi mang Bearer <redacted> vụ và qua kiểm IP lúc kết nối.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
HO_SO = "b" * 32
KHOA_DV = "khoa-dich-vu-gia-0123456789abcdef"


def _nap_vnc_xa():
    spec = importlib.util.spec_from_file_location(
        "vnc_xa_nv92_that", GOC / "modules" / "vnc-xa" / "module.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DichVuGia:
    """Dịch vụ trình duyệt giả: các đường điều khiển P1, ghi lại lời gọi."""

    def __init__(self, loi_gianh: int | None = None):
        self.loi_gianh = loi_gianh
        self.goi: list[tuple[str, str, dict]] = []
        self._sv = None

    async def __aenter__(self):
        self._sv = await asyncio.start_server(self._xu_ly, "127.0.0.1", 0)
        self.cong = self._sv.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_):
        self._sv.close()
        await self._sv.wait_closed()

    async def _xu_ly(self, r, w):
        try:
            dong_dau = await r.readuntil(b"\r\n")
            phuong_thuc, duong = dong_dau.decode("latin-1").split(" ")[:2]
            headers = {}
            while True:
                d = await r.readuntil(b"\r\n")
                if d == b"\r\n":
                    break
                k, _, v = d.decode("latin-1").partition(":")
                headers[k.strip().lower()] = v.strip()
            dai = int(headers.get("content-length", "0") or 0)
            than = await r.readexactly(dai) if dai else b""
            du_lieu = json.loads(than.decode("utf-8")) if than else {}
            self.goi.append((phuong_thuc, duong.split("?")[0], du_lieu,
                     headers.get("authorization", "")))

            if duong.endswith("/dieu-khien/gianh") and self.loi_gianh:
                ma, tl = self.loi_gianh, b'{"detail": "dang ban"}'
            elif duong.endswith("/dieu-khien/gianh"):
                ma, tl = 200, b'{"ok": true}'
            elif duong.endswith("/dieu-khien/nha"):
                ma, tl = 200, b'{"ok": true}'
            elif duong.endswith("/dieu-huong"):
                ma, tl = 200, b'{"ok": true}'
            elif duong.endswith("/dien"):
                ma, tl = 200, b'{"ok": true}'
            elif duong.endswith("/bam"):
                ma, tl = 200, b'{"ok": true}'
            elif duong.endswith("/cho"):
                ma, tl = 200, b'{"thay": true}'
            elif duong.endswith("/tin-hieu"):
                ma, tl = 200, b'{"tin_hieu": ["da_dang_nhap"]}'
            elif duong.endswith("/cookie-truc-tiep"):
                ma, tl = 200, b'{"cookie": "SID=bi-mat", "so_cookie": 1}'
            else:
                ma, tl = 404, b'{"detail": "khong thay"}'
            w.write(b"HTTP/1.1 %d X\r\nContent-Type: application/json\r\n"
                    b"Connection: close\r\nContent-Length: %d\r\n\r\n" % (ma, len(tl)) + tl)
            await w.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            w.close()


@pytest.fixture
def vnc():
    return _nap_vnc_xa()


def _that(vnc, dv: DichVuGia):
    mod = vnc.Module(json.loads(
        (GOC / "modules" / "vnc-xa" / "manifest.json").read_text(encoding="utf-8")))
    mod._dich_vu_url = f"http://127.0.0.1:{dv.cong}"
    mod._khoa = KHOA_DV
    mod._kiem_ip = lambda ip: None  # test: cho phép loopback
    return mod.trinh_duyet_that(HO_SO)


def test_gianh_va_nguyen_thuy(vnc):
    async def _chay():
        async with DichVuGia() as dv:
            td = _that(vnc, dv)
            await td.gianh()
            await td.mo_trang("https://accounts.google.com/")
            await td.dien("#identifierId", "nguoi@example.com")
            await td.bam("#identifierNext")
            assert await td.cho("#xac-nhan", 2) is True
            # doc_cookie/tin_hieu là đồng bộ theo hợp đồng; dịch vụ giả chạy
            # cùng vòng sự kiện nên phải gọi chúng trên luồng khác (tránh
            # nghẽn: vòng sự kiện vẫn phục vụ dịch vụ giả trong lúc chờ HTTP).
            assert await asyncio.to_thread(td.tin_hieu) == {"da_dang_nhap"}
            assert await asyncio.to_thread(td.doc_cookie, "google.com") == {"SID": "bi-mat"}
            await td.dong()
            duong = [d for _, d, _, _ in dv.goi]
            assert f"/ho-so/{HO_SO}/dieu-khien/gianh" in duong
            assert f"/ho-so/{HO_SO}/dieu-huong" in duong
            assert f"/ho-so/{HO_SO}/dien" in duong
            assert f"/ho-so/{HO_SO}/bam" in duong
            assert f"/ho-so/{HO_SO}/dieu-khien/nha" in duong
            # Thân dien mang đúng bộ chọn + giá trị
            than_dien = [t for _, d, t, _ in dv.goi if d.endswith("/dien")][0]
            assert than_dien == {"bo_chon": "#identifierId", "van_ban": "nguoi@example.com"}

    asyncio.run(_chay())

def test_moi_loi_goi_mang_bearer(vnc):
    async def _chay():
        async with DichVuGia() as dv:
            td = _that(vnc, dv)
            await td.gianh()
            # Mọi lời gọi đều qua client của thể hiện — kiểm gián tiếp qua cookie
            # (gọi đồng bộ trên luồng khác vì dịch vụ giả cùng vòng sự kiện).
            await asyncio.to_thread(td.doc_cookie, "google.com")
            assert dv.goi, "không có lời gọi nào tới dịch vụ"
            for _, _, _, auth in dv.goi:
                assert auth == "Bearer " + KHOA_DV, "thiếu/sai Bearer"
            await td.dong()

    asyncio.run(_chay())

def test_gianh_that_bai_thanh_loi_dich_vu(vnc):
    async def _chay():
        async with DichVuGia(loi_gianh=409) as dv:
            td = _that(vnc, dv)
            with pytest.raises(vnc.LoiDichVu) as exc:
                await td.gianh()
            assert exc.value.ma == 409

    asyncio.run(_chay())

def test_ho_so_sai_dinh_dang_bi_chan(vnc):
    async def _chay():
        async with DichVuGia() as dv:
            mod = vnc.Module(json.loads(
                (GOC / "modules" / "vnc-xa" / "manifest.json").read_text(encoding="utf-8")))
            mod._dich_vu_url = f"http://127.0.0.1:{dv.cong}"
            mod._khoa = KHOA_DV
            with pytest.raises(ValueError):
                mod.trinh_duyet_that("../etc/passwd")

    asyncio.run(_chay())

def test_gia_tri_dien_khong_vao_log(vnc, caplog):
    async def _chay():
        async with DichVuGia() as dv:
            td = _that(vnc, dv)
            await td.gianh()
            bi_mat = "mat-khau-that-bi-mat-999"
            with caplog.at_level(logging.INFO):
                await td.dien("#mk", bi_mat)
            assert bi_mat not in caplog.text
            await td.dong()

    asyncio.run(_chay())

def test_dung_khong_gianh_thi_loi(vnc):
    async def _chay():
        async with DichVuGia() as dv:
            td = _that(vnc, dv)
            # dong() khi chưa giành: vẫn gọi nhả (luỹ đẳng) — không ném
            await td.dong()
            assert any(d.endswith("/dieu-khien/nha") for _, d, _, _ in dv.goi)

    asyncio.run(_chay())