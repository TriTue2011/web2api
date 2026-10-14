"""Ghim IP lúc KẾT NỐI cho module vnc-xa — chống DNS rebinding còn sót.

`cai()` gọi `kiem_url_lan(dich_vu_url)` MỘT lần lúc cấu hình, nhưng 7 chỗ tạo
`httpx.AsyncClient` trần sau đó gọi `self._dich_vu_url` mỗi lượt và để httpx tự
phân giải lại. Kẻ điều khiển DNS trả IP công khai lúc kiểm rồi 127.0.0.1 lúc gọi
thì lọt SSRF — mà các lời gọi này còn mang khoá dịch vụ (Bearer) nên lộ khoá.

Khuôn: test_moi_duong_goi_deu_bi_ghim_ip (NV4) — máy chủ THẬT trên loopback đếm
kết nối + vá `socket.getaddrinfo`, không cần mạng thật.
"""

import asyncio
import importlib.util
import json
import os
import socket
from pathlib import Path

import pytest

TEN = "dich-vu-trinh-duyet.example.test"
TEN_BYTES = TEN.encode("ascii")  # anyio/httpx có thể truyền host dạng bytes cho getaddrinfo
IP_CONG_KHAI = "198.51.100.45"  # TEST-NET-2: công khai, qua kiểm lúc cài
IP_DICH = "127.0.0.1"           # loopback: bị chặn lúc gọi
KHOA_DV = "khoa-dich-vu-gia-0123456789abcdef"
HO_SO = "a" * 32

GOC = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((GOC / "modules" / "vnc-xa" / "manifest.json").read_text(encoding="utf-8"))


def _nap_module():
    spec = importlib.util.spec_from_file_location("vnc_xa_ghim_ip_test", GOC / "modules" / "vnc-xa" / "module.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Module(MANIFEST)


def _va_dns(monkeypatch, tra_ve):
    """Vá socket.getaddrinfo cho TEN: `tra_ve(lan_thu)` -> IP (chuỗi).

    Đồng thời cho TEN vào NO_PROXY để httpx nối TRỰC TIẾP (không qua proxy của
    môi trường test) — nếu không, httpx không phân giải DNS mà ném ConnectError.
    """
    dem = {"n": 0}
    that = socket.getaddrinfo

    def gia(host, port, *a, **kw):
        if host == TEN or host == TEN_BYTES:
            dem["n"] += 1
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (tra_ve(dem["n"]), port or 0))]
        return that(host, port, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", gia)
    no_proxy = os.environ.get("NO_PROXY", "")
    monkeypatch.setenv("NO_PROXY", f"{no_proxy},{TEN}" if no_proxy else TEN)
    monkeypatch.delenv("no_proxy", raising=False)
    return dem


class MayChuTrinhDuyet:
    """Dịch vụ trình duyệt giả TRÊN LOOPBACK: đếm kết nối, trả JSON đúng hình từng đường."""

    def __init__(self):
        self.n = 0

    async def __aenter__(self):
        self._sv = await asyncio.start_server(self._xu_ly, "127.0.0.1", 0)
        self.cong = self._sv.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_):
        self._sv.close()
        await self._sv.wait_closed()

    async def _xu_ly(self, r, w):
        self.n += 1
        try:
            dong_dau = await r.readuntil(b"\r\n")
            phuong_thuc, duong = dong_dau.decode("latin-1").split(" ")[:2]
            while (await r.readuntil(b"\r\n")) != b"\r\n":
                pass  # bỏ qua header yêu cầu
            duong = duong.split("?")[0]
            if duong == "/ho-so" and phuong_thuc == "GET":
                than = b"[]"
            elif duong == "/ho-so" and phuong_thuc == "POST":
                than = b'{"ho_so": "' + HO_SO.encode() + b'", "ten": "gia"}'
            elif duong == "/ho-so/mo":
                than = b'{"ho_so": "' + HO_SO.encode() + b'", "dich": "http://127.0.0.1:1"}'
            elif duong.endswith("/dong"):
                than = b'{"ok": true}'
            elif duong.endswith("/cookie"):
                than = b'{"cookie": "gia-tri-cookie", "so_cookie": 1}'
            else:
                than = b"{}"
            w.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
                    b"Content-Length: %d\r\n\r\n" % len(than) + than)
            await w.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            w.close()


async def _goi_6_phuong_thuc(mod, an_loi=True):
    """Gọi cả 6 phương thức công khai có gọi mạng của module. an_loi=True: nuốt ngoại lệ (đường bị chặn)."""
    async def goi(fn, *a):
        try:
            return await fn(*a)
        except Exception:
            if not an_loi:
                raise
            return None

    await goi(mod.trinh_duyet_tao, "gia")
    await goi(mod.trinh_duyet_mo, HO_SO, "firefox")
    await goi(mod.trinh_duyet_dong, HO_SO, False)
    await goi(mod.trinh_duyet_ds)
    await goi(mod.trinh_duyet_cookie, HO_SO, "vi-du.example.test")
    await goi(mod.suc_khoe)


def test_cai_bi_chan_khi_dns_rebinding(monkeypatch):
    """Kịch bản rebinding đầy đủ: cai() kiểm thấy IP công khai (qua `kiem_url_lan`),
    nhưng GET kiểm tra ngay sau đó lúc DNS đã đổi sang loopback cũng bị chặn —
    không nối vào đó dù chỉ một lần."""
    _va_dns(monkeypatch, lambda n: IP_CONG_KHAI if n == 1 else IP_DICH)

    async def chay():
        async with MayChuTrinhDuyet() as sv:
            mod = _nap_module()
            kq = await mod.cai({"dich_vu_url": f"http://{TEN}:{sv.cong}", "khoa_dich_vu": KHOA_DV})
            return kq, sv.n

    kq, n = asyncio.run(chay())
    assert kq["ok"] is False, kq
    assert "LoiDichBiChan" in kq["loi"], kq
    assert n == 0, f"máy chủ loopback vẫn nhận {n} kết nối từ cai()"


def test_rebinding_moi_phuong_thuc_deu_khong_noi(monkeypatch):
    """Module đã cấu hình từ lúc DNS còn sạch (nap trực tiếp như lõi làm sau cai);
    khi DNS đổi sang loopback thì MỌI phương thức đều không được nối vào đó."""
    _va_dns(monkeypatch, lambda n: IP_DICH)  # tên miền luôn trỏ vào loopback

    async def chay():
        async with MayChuTrinhDuyet() as sv:
            mod = _nap_module()
            mod.nap({"dich_vu_url": f"http://{TEN}:{sv.cong}", "khoa_dich_vu": KHOA_DV})
            truoc = sv.n
            await _goi_6_phuong_thuc(mod)
            return sv.n - truoc

    moi = asyncio.run(chay())
    assert moi == 0, f"rebinding: máy chủ loopback vẫn nhận {moi} kết nối mới — có đường gọi chưa ghim IP"


def test_doi_chung_duong_mo_cho_phep_thi_noi_duoc(monkeypatch):
    """Đối chứng dương: kiểm IP cho phép thì cả 6 phương thức đều nối được (test không «xanh vì rỗng»)."""
    _va_dns(monkeypatch, lambda n: IP_DICH)  # tên miền luôn trỏ vào máy chủ loopback

    async def chay():
        async with MayChuTrinhDuyet() as sv:
            mod = _nap_module()
            if hasattr(mod, "_kiem_ip"):
                monkeypatch.setattr(mod, "_kiem_ip", lambda ip: None)
            mod.nap({"dich_vu_url": f"http://{TEN}:{sv.cong}", "khoa_dich_vu": KHOA_DV})
            await mod.trinh_duyet_tao("gia")
            await mod.trinh_duyet_mo(HO_SO, "firefox")
            await mod.trinh_duyet_dong(HO_SO, False)
            ds = await mod.trinh_duyet_ds()
            assert ds == []
            kq = await mod.trinh_duyet_cookie(HO_SO, "vi-du.example.test")
            assert kq == {"cookie": "gia-tri-cookie", "so_cookie": 1}
            assert (await mod.suc_khoe())["ok"] is True
            return sv.n

    n = asyncio.run(chay())
    assert n >= 6, f"đối chứng: cho phép thì 6 phương thức phải nối được, chỉ có {n} kết nối"
