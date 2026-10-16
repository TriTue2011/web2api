# NV16: chặn redirect tarball sang IP nội bộ (SSRF qua Location header)
#
# app/nguon_git.py theo redirect 302 thủ công mà KHÔNG kiểm đích.
# Location header từ máy chủ có thể trỏ vào 127.0.0.1/metadata đám mây.
# Sửa: client dùng TransportGhimIp + _dia_chi_bi_chan (bộ kiểm chung).
import asyncio

import pytest
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route

from app.nguon_git import LoiGit, NguonGit
from gia import MayChuThat


def test_client_nguon_git_co_ghim_ip():
    """Client của NguonGit phải dùng TransportGhimIp để chặn SSRF/DNS-rebinding."""
    from app.chuyen_tiep import TransportGhimIp

    ng = NguonGit.__new__(NguonGit)
    ng._khach = None
    client = ng._client()
    try:
        transport = client._transport
        assert isinstance(transport, TransportGhimIp), \
            f"Transport phải là TransportGhimIp, đang là {type(transport)}"
    finally:
        asyncio.run(client.aclose())


def test_302_toi_loopback_bi_chan(monkeypatch):
    """Máy chủ giả trả 302 về metadata 169.254.169.254: _tai_tarball phải bắt và quấn thành LoiGit."""
    async def tra_302_metadata(req):
        return Response(status_code=302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})

    app = Starlette(routes=[
        Route("/repos/{repo:path}/tarball/{sha:str}", tra_302_metadata)
    ])

    with MayChuThat(app) as s:
        monkeypatch.setenv("WEB2API_GITHUB_API", s.url)
        monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")

        ng = NguonGit.__new__(NguonGit)
        ng._khach = None
        ng.trang_thai = {}
        ng.token = lambda: None

        async def chay():
            try:
                with pytest.raises(LoiGit) as ei:
                    await ng._tai_tarball("chu-may/repo-test", "0123456789abcdef0123456789abcdef01234567")
                assert "LoiDichBiChan" in str(ei.value)
            finally:
                if ng._khach:
                    await ng._khach.aclose()

        asyncio.run(chay())


def test_302_toi_loopback_thuc_bi_chan(monkeypatch):
    """Máy chủ giả trả 302 về loopback 127.0.0.1: khi không cho phép loopback ở đích redirect, _tai_tarball ném LoiGit."""
    from app import tu_xa
    da_goi_lan_dau = False

    def cho_phep_co_dieu_kien():
        # Lần đầu (gọi tới s.url ở 127.0.0.1) cho phép; sau khi đã nhận 302 thì cấm loopback
        if not da_goi_lan_dau:
            return True
        return False

    async def tra_302_loopback(req):
        nonlocal da_goi_lan_dau
        da_goi_lan_dau = True
        return Response(status_code=302, headers={"Location": "http://127.0.0.1:9/tai.tar.gz"})

    app = Starlette(routes=[
        Route("/repos/{repo:path}/tarball/{sha:str}", tra_302_loopback)
    ])

    monkeypatch.setattr(tu_xa, "cho_phep_loopback_cho_test", cho_phep_co_dieu_kien)

    with MayChuThat(app) as s:
        monkeypatch.setenv("WEB2API_GITHUB_API", s.url)

        ng = NguonGit.__new__(NguonGit)
        ng._khach = None
        ng.trang_thai = {}
        ng.token = lambda: None

        async def chay():
            try:
                with pytest.raises(LoiGit) as ei:
                    await ng._tai_tarball("chu-may/repo-test", "0123456789abcdef0123456789abcdef01234567")
                assert "LoiDichBiChan" in str(ei.value)
            finally:
                if ng._khach:
                    await ng._khach.aclose()

        asyncio.run(chay())
