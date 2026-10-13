"""Ghim IP lúc KẾT NỐI cho module từ xa — chống DNS rebinding (TOCTOU ở kiểm SSRF).

`kiem_url_lan` chỉ phân giải tên miền MỘT lần lúc đăng ký; trước đây `ChuyenTiepOpenAI` sau đó để httpx tự phân giải lại
mỗi lần gọi. Kẻ điều khiển DNS trả IP công khai lúc kiểm rồi 127.0.0.1 lúc gọi thì lọt SSRF. Các test dưới mô phỏng
đúng kịch bản đó bằng cách vá `socket.getaddrinfo` (không cần mạng thật) và dùng một máy chủ THẬT trên loopback đếm
số kết nối để chứng minh mọi đường gọi đều không nối được vào đó.
"""

import asyncio
import json
import socket

import httpx
import pytest

from app.chuyen_tiep import ChuyenTiepOpenAI, LoiDichBiChan, TransportGhimIp
from app.tu_xa import ModuleTuXa, _dia_chi_bi_chan, kiem_url_lan

TEN = "may-lan.example.test"
IP_CONG_KHAI = "198.51.100.23"  # TEST-NET-2: công khai, qua kiểm
IP_DICH = "127.0.0.1"           # loopback: bị chặn
CONG = 8471
KHOA = "sk-test-0123456789abcdef"
YC_CHAT = {"model": "lan/m", "messages": [{"role": "user", "content": "xin chào"}]}


def _vá_dns(monkeypatch, tra_ve):
    """Vá socket.getaddrinfo cho TEN: `tra_ve(lan_thu)` -> IP (chuỗi). Trả bộ đếm lần phân giải."""
    dem = {"n": 0}
    that = socket.getaddrinfo

    def gia(host, port, *a, **kw):
        if host == TEN:
            dem["n"] += 1
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (tra_ve(dem["n"]), port or CONG))]
        return that(host, port, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", gia)
    return dem


@pytest.fixture
def dns_rebinding(monkeypatch):
    """Lần phân giải đầu (lúc đăng ký) → IP công khai; các lần sau (lúc gọi) → 127.0.0.1."""
    return _vá_dns(monkeypatch, lambda n: IP_CONG_KHAI if n == 1 else IP_DICH)


def _mod_xa(base):
    return ModuleTuXa({"id": "lan", "ten": "Lan", "tien_to": "lan", "base_url": base}, KHOA)


def test_dns_rebinding_bi_chan_truoc_khi_noi(dns_rebinding):
    """Kịch bản tấn công: kiểm lúc đăng ký thấy IP công khai, lúc gọi DNS đổi sang loopback."""
    async def chay():
        base = await kiem_url_lan(f"http://{TEN}:{CONG}/v1")  # lần 1: IP công khai → qua kiểm
        return await _mod_xa(base).chat(YC_CHAT, stream=False)

    ma, _hd, than = asyncio.run(chay())
    assert dns_rebinding["n"] >= 2, "phải phân giải LẠI tên miền lúc gọi (đây chính là điểm sửa)"
    assert ma == 502, than
    loi = json.loads(than)["error"]
    assert loi["code"] == "ssrf_blocked" and "bị chặn" in loi["message"], loi
    assert IP_DICH not in loi["message"], "không nêu IP đã phân giải cho khách gọi API"


def test_ip_literal_bi_chan_khong_can_dns():
    """IP chữ trong base_url cũng bị kiểm lúc nối (không chỉ tên miền mới phân giải)."""
    ma, _hd, than = asyncio.run(_mod_xa(f"http://{IP_DICH}:{CONG}/v1").chat(YC_CHAT, stream=False))
    assert ma == 502, than
    assert json.loads(than)["error"]["code"] == "ssrf_blocked", than


def test_ghim_ip_giu_host_va_sni(dns_rebinding):
    """IP đã kiểm được ghim vào URL nối; Host và SNI vẫn là tên gốc (khuôn app/novnc.py)."""
    class Gia(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            self.req = request
            return httpx.Response(200, json={"data": []})

    async def chay():
        gia = Gia()
        async with httpx.AsyncClient(transport=TransportGhimIp(_dia_chi_bi_chan, goc=gia)) as c:
            await c.get(f"https://{TEN}:{CONG}/v1/models")
        return gia.req

    req = asyncio.run(chay())
    assert req.url.host == IP_CONG_KHAI and req.url.port == CONG
    assert req.headers["host"] == f"{TEN}:{CONG}"
    assert req.extensions["sni_hostname"] == TEN


def test_khong_dat_kiem_ip_thi_dung_transport_mac_dinh():
    """Không đặt `kiem_ip` (module tự cố định đích) → không ghim, hành vi như cũ."""
    async def chay():
        ct = ChuyenTiepOpenAI(ten="Lan", tien_to="lan", base_url=f"http://{TEN}:{CONG}/v1", khoa=[KHOA])
        return isinstance(ct._client()._transport, TransportGhimIp)

    assert asyncio.run(chay()) is False


# ── Mọi đường gọi đều bị ghim: máy chủ THẬT trên loopback không được nhận kết nối nào ──

class MayChuDem:
    """Máy chủ HTTP tối thiểu trên loopback, đếm số kết nối nhận được."""

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
            await r.readuntil(b"\r\n\r\n")
            than = b'{"data": [], "choices": []}'
            w.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
                    b"Content-Length: %d\r\n\r\n" % len(than) + than)
            await w.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            w.close()


async def _goi_het_cac_duong(ct, base):
    """Gọi MỌI loại lời gọi mạng của ChuyenTiepOpenAI: chat, khả năng khác, models, kiểm khoá."""
    r = await ct.chat(YC_CHAT, stream=False)
    ket_qua = {"chat": r}
    ket_qua["kha_nang"] = await ct.goi_kha_nang("anh", "tạo ảnh", "POST", f"{base}/images/generations",
                                                json_than={"prompt": "x"}, tieu_de=lambda k: {})
    ket_qua["models"] = await ct.models()
    ket_qua["kiem_khoa"] = await ct.kiem_khoa(KHOA)
    return ket_qua


def test_moi_duong_goi_deu_bi_ghim_ip(monkeypatch):
    """Đối chứng dương: kiểm IP cho phép thì CẢ BỐN đường nối được máy chủ loopback (test không rỗng);
    kiểm IP thật (`_dia_chi_bi_chan`) thì máy chủ không nhận kết nối nào — kể cả đường khả năng khác (ảnh, giọng nói…)."""
    _vá_dns(monkeypatch, lambda n: IP_DICH)  # tên miền luôn trỏ vào loopback

    async def chay(kiem_ip):
        async with MayChuDem() as sv:
            base = f"http://{TEN}:{sv.cong}/v1"
            ct = ChuyenTiepOpenAI(ten="Lan", tien_to="lan", base_url=base, khoa=[KHOA], kiem_ip=kiem_ip)
            try:
                kq = await _goi_het_cac_duong(ct, base)
            finally:
                await ct._client().aclose()
            return sv.n, kq

    n_mo, _ = asyncio.run(chay(lambda ip: None))
    assert n_mo >= 4, f"đối chứng: không ghim thì phải nối được cả 4 đường, chỉ có {n_mo} kết nối"

    n_chan, kq = asyncio.run(chay(_dia_chi_bi_chan))
    assert n_chan == 0, f"máy chủ loopback vẫn nhận {n_chan} kết nối — có đường gọi chưa được ghim IP"
    assert kq["chat"][0] == 502 and json.loads(kq["chat"][2])["error"]["code"] == "ssrf_blocked"
    ma_kn, loi_kn = kq["kha_nang"][1][0], json.loads(kq["kha_nang"][1][2])
    assert ma_kn == 502 and loi_kn["error"]["code"] == "ssrf_blocked"
    assert kq["models"] == [] and kq["kiem_khoa"]["ok"] is False


def test_loi_dich_bi_chan_la_loi_van_chuyen():
    """LoiDichBiChan là httpx.ConnectError: nơi nào chỉ bắt httpx.HTTPError cũng tự coi là «không nối được»."""
    assert issubclass(LoiDichBiChan, httpx.HTTPError)
