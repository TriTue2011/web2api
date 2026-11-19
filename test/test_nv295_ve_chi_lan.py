"""NV295: lõi chỉ ký/dùng vé tin chứng chỉ cho IP LAN, không DNS/mạng thật."""

from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app import chung_chi_tls

MODULE_GIA = "module-gia-nv295"
VAN_TAY_GIA = "ab" * 32
CONG_GIA = 443
HOST_BI_CAM = (
    "169.254.169.254", "8.8.8.8", "localhost", "example.com",
    "100.64.0.1", "192.0.2.1",
)
HOST_LAN = ("192.168.1.10", "10.0.0.5")


@pytest.fixture(autouse=True)
def cam_dns_va_mang(monkeypatch):
    """Mock đo không bao giờ chạm mạng; vẫn chặn DNS/socket nếu có đường khác."""
    cac_mock = []
    for doi_tuong, ten in (
        (socket, "getaddrinfo"),
        (socket, "gethostbyname"),
        (socket, "gethostbyname_ex"),
        (socket, "create_connection"),
        (socket.socket, "connect"),
        (socket.socket, "connect_ex"),
    ):
        cam = Mock(side_effect=AssertionError("Test vé không được DNS/nối mạng"))
        monkeypatch.setattr(doi_tuong, ten, cam)
        cac_mock.append(cam)
    yield
    for cam in cac_mock:
        cam.assert_not_called()


def _ket_qua_de_nghi(host):
    return {
        "ok": False,
        "ket_qua_tung_khoa": [],
        "loi": "Chứng chỉ giả đã đổi.",
        "tin_chung_chi": {
            "ly_do": "doi",
            "ma_hien_thi": chung_chi_tls.ma_hien_thi(VAN_TAY_GIA),
            "thong_bao": "Chứng chỉ giả đã đổi.",
            "host": host,
            "cong": CONG_GIA,
        },
    }


@pytest.mark.parametrize("host", HOST_BI_CAM)
def test_khong_ky_ve_cho_host_ngoai_lan(host):
    assert chung_chi_tls.ghep_de_nghi_tin(_ket_qua_de_nghi(host), MODULE_GIA) is None


@pytest.mark.parametrize("host", HOST_LAN)
def test_ip_lan_van_co_ve_hop_le(host):
    de_nghi = chung_chi_tls.ghep_de_nghi_tin(_ket_qua_de_nghi(host), MODULE_GIA)
    assert de_nghi is not None
    assert chung_chi_tls.doc_ve(de_nghi["ve"], MODULE_GIA) == (host, CONG_GIA)
    assert de_nghi["ly_do"] == "doi"
    assert de_nghi["ma_hien_thi"] == chung_chi_tls.ma_hien_thi(VAN_TAY_GIA)
    assert "host" not in de_nghi and "cong" not in de_nghi


@pytest.fixture
def do_va_so_gia(monkeypatch):
    do = Mock(return_value=VAN_TAY_GIA)
    monkeypatch.setattr(chung_chi_tls, "do_van_tay", do)
    so = SimpleNamespace(cai=AsyncMock(return_value={
        "ok": True, "ket_qua_tung_khoa": [], "loi": None,
    }))
    return do, so


@pytest.mark.parametrize("host", HOST_BI_CAM)
def test_ve_da_ky_cho_host_bi_cam_phai_tu_choi_truoc_khi_do(host, do_va_so_gia):
    do, so = do_va_so_gia
    # Vé thật, đúng HMAC/module/hạn: mô phỏng vé đã ký trước bản sửa.
    ve = chung_chi_tls.tao_ve(MODULE_GIA, host, CONG_GIA)
    assert chung_chi_tls.doc_ve(ve, MODULE_GIA) == (host, CONG_GIA)
    kq = asyncio.run(chung_chi_tls.tin_chung_chi(
        so, MODULE_GIA,
        {"dia_chi": "https://192.168.1.10"},  # Form LAN không hợp thức hoá host trong vé.
        ve, chung_chi_tls.ma_hien_thi(VAN_TAY_GIA),
    ))
    assert {
        "ok": kq["ok"],
        "lan_do_van_tay": do.call_count,
        "lan_cai": so.cai.call_count,
    } == {"ok": False, "lan_do_van_tay": 0, "lan_cai": 0}


@pytest.mark.parametrize("host", HOST_LAN)
def test_ve_lan_van_do_va_luu_van_tay(host, do_va_so_gia):
    do, so = do_va_so_gia
    de_nghi = chung_chi_tls.ghep_de_nghi_tin(_ket_qua_de_nghi(host), MODULE_GIA)
    assert de_nghi is not None
    cau_hinh = {"dia_chi": f"https://{host}"}
    kq = asyncio.run(chung_chi_tls.tin_chung_chi(
        so, MODULE_GIA, cau_hinh, de_nghi["ve"], de_nghi["ma_hien_thi"],
    ))
    assert kq["ok"] is True
    do.assert_called_once_with(host, CONG_GIA)
    so.cai.assert_awaited_once_with(
        MODULE_GIA, {**cau_hinh, "van_tay_tls": VAN_TAY_GIA},
    )
    assert "van_tay_tls" not in cau_hinh
