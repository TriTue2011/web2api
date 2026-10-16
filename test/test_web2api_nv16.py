# NV16: chặn redirect tarball sang IP nội bộ (SSRF qua Location header)
#
# app/nguon_git.py theo redirect 302 thủ công mà KHÔNG kiểm đích.
# Location header từ máy chủ có thể trỏ vào 127.0.0.1/metadata đám mây.
# Sửa: client dùng TransportGhimIp + _dia_chi_bi_chan (bộ kiểm chung).
import asyncio

import pytest

from app.nguon_git import NguonGit


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


def test_302_toi_loopback_bi_chan():
    """Kết nối tới 127.0.0.1 qua client NguonGit phải bị chặn (trừ khi test cho phép)."""
    import os
    os.environ.pop("WEB2API_TEST_CHO_PHEP_LOOPBACK", None)

    from app.chuyen_tiep import LoiDichBiChan

    ng = NguonGit.__new__(NguonGit)
    ng._khach = None
    client = ng._client()

    async def chay():
        try:
            # Giả lập redirect tới 127.0.0.1 — transport phải chặn
            req = client.build_request("GET", "http://127.0.0.1:9/tai.tar.gz")
            with pytest.raises(Exception) as ei:
                await client.send(req)
            # Phải là lỗi chặn SSRF, không phải lỗi kết nối thông thường
            assert "chan" in str(ei.value).lower() or isinstance(ei.value, LoiDichBiChan), \
                f"Lỗi không phải do chặn SSRF: {ei.value}"
        finally:
            await client.aclose()

    asyncio.run(chay())
