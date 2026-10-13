"""Giới hạn tần suất khi SAI khoá xác thực: đếm 401 theo IP, vượt ngưỡng → khoá tạm thời → 429."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import gioi_han, xac_thuc
from app.gioi_han import GioiHanSaiKhoa
from app.loi_openai import LoiOpenAI
from app.main import app

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
KHOA_SAI = "khoa-sai-0000000000000000000000000000ab"


def _yeu_cau(host, khoa=None, xff=None, scope=None):
    headers = []
    if khoa is not None:
        headers.append((b"authorization", f"Bearer {khoa}".encode("ascii")))
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode("ascii")))
    if scope is not None:
        headers.append((b"x-web2api-scope", scope.encode("ascii")))
    scope_ = {"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
              "server": ("testserver", 80), "path": "/v1/models", "query_string": b"",
              "headers": headers, "client": (host, 1234)}
    return Request(scope_)


@pytest.fixture
def dong_ho():
    return [1000.0]


@pytest.fixture
def bo(dong_ho, tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "3")
    monkeypatch.setenv("WEB2API_RL_CUA_SO_GIAY", "60")
    monkeypatch.setenv("WEB2API_RL_KHOA_GIAY", "120")
    monkeypatch.setenv("WEB2API_TRUST_PROXY", "")
    b = GioiHanSaiKhoa.tu_moi_truong(dong_ho=lambda: dong_ho[0])
    monkeypatch.setattr(gioi_han, "_BO", b, raising=False)
    return b


def _sai(bo, host, n=1):
    for _ in range(n):
        bo.ghi_loi(host)


# ── Unit: cửa sổ trượt, khoá, mở khoá ──

def test_duoi_nguong_khong_chan(bo):
    _sai(bo, "192.0.2.1", 3)
    bo.kiem_tra("192.0.2.1")  # ngưỡng 3: 3 lần chưa vượt


def test_vuot_nguong_khoa_429_va_retry_after(bo):
    _sai(bo, "192.0.2.1", 4)
    with pytest.raises(LoiOpenAI) as e:
        bo.kiem_tra("192.0.2.1")
    assert e.value.ma == 429
    assert e.value.headers["Retry-After"] == "120"
    assert e.value.than["error"]["type"] == "rate_limit_error"
    assert e.value.than["error"]["code"] == "rate_limit_exceeded"
    assert KHOA_SAI not in str(e.value.than)


def test_het_thoi_gian_khoa_thi_mo(bo, dong_ho):
    _sai(bo, "192.0.2.1", 4)
    dong_ho[0] += 121
    bo.kiem_tra("192.0.2.1")  # không ném


def test_cua_so_truot_loai_loi_cu(bo, dong_ho):
    _sai(bo, "192.0.2.1", 3)
    dong_ho[0] += 61  # quá cửa sổ 60s → lỗi cũ bị loại
    _sai(bo, "192.0.2.1", 1)
    bo.kiem_tra("192.0.2.1")  # chỉ còn 1 lỗi trong cửa sổ


def test_ip_khac_khong_bi_anh_huong(bo):
    _sai(bo, "192.0.2.1", 4)
    bo.kiem_tra("192.0.2.2")  # không ném


def test_tat_khi_nguong_bang_0(dong_ho, monkeypatch):
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    b = GioiHanSaiKhoa.tu_moi_truong(dong_ho=lambda: dong_ho[0])
    assert b.bat is False
    b.ghi_loi("192.0.2.1")
    b.kiem_tra("192.0.2.1")  # không ném, không đếm


def test_bo_nho_co_tran(dong_ho):
    b = GioiHanSaiKhoa(toi_da_ip=5, dong_ho=lambda: dong_ho[0])
    for i in range(7):
        b.ghi_loi(f"192.0.2.{i}")
    assert len(b._ip) <= 5


# ── IP hiệu lực: loopback miễn, XFF chỉ khi proxy được khai ──

def test_loopback_mien(bo):
    assert bo.ip_hieu_luc("127.0.0.1", None) is None
    assert bo.ip_hieu_luc("::1", None) is None


def test_xff_chi_tin_khi_proxy_duoc_khai(dong_ho, monkeypatch):
    import ipaddress
    b = GioiHanSaiKhoa(proxy_tin_cay=[ipaddress.ip_network("203.0.113.0/24")],
                       dong_ho=lambda: dong_ho[0])
    assert b.ip_hieu_luc("203.0.113.5", "198.51.100.7") == "198.51.100.7"
    assert b.ip_hieu_luc("198.51.100.7", "203.0.113.5") == "198.51.100.7"  # peer không tin → bỏ XFF
    assert bo_ip_khong_tin(dong_ho).ip_hieu_luc("203.0.113.5", "198.51.100.7") == "203.0.113.5"


def bo_ip_khong_tin(dong_ho):
    return GioiHanSaiKhoa(proxy_tin_cay=[], dong_ho=lambda: dong_ho[0])


def _bo_cf(dong_ho):
    import ipaddress
    # Giả lập dải của proxy ngược (Cloudflare) bằng dải tài liệu RFC 5737.
    return GioiHanSaiKhoa(proxy_tin_cay=[ipaddress.ip_network("203.0.113.0/24")], dong_ho=lambda: dong_ho[0])


def test_xff_gia_o_ben_trai_khong_doi_duoc_ip(dong_ho):
    b = _bo_cf(dong_ho)
    # Proxy NỐI THÊM IP kết nối vào cuối: phần trái do khách gõ. Phải lấy phần tử không-tin-cậy NGOÀI CÙNG BÊN PHẢI.
    assert b.ip_hieu_luc("203.0.113.5", "9.9.9.9, 198.51.100.7") == "198.51.100.7"
    assert b.ip_hieu_luc("203.0.113.5", "9.9.9.9, 198.51.100.7, 203.0.113.9") == "198.51.100.7"  # chuỗi proxy tin cậy


def test_xff_hong_hoac_peer_la_dung_peer(dong_ho):
    b = _bo_cf(dong_ho)
    assert b.ip_hieu_luc("203.0.113.5", "khong-phai-ip") == "203.0.113.5"
    assert b.ip_hieu_luc("203.0.113.5", "198.51.100.7, rac") == "203.0.113.5"
    assert b.ip_hieu_luc("198.51.100.1", "9.9.9.9") == "198.51.100.1"  # peer không tin cậy: bỏ XFF


def test_proxy_cung_may_loopback_co_xff_van_bi_dem(dong_ho):
    b = GioiHanSaiKhoa(dong_ho=lambda: dong_ho[0])  # cloudflared chạy cùng máy: không khai CIDR nào
    assert b.ip_hieu_luc("127.0.0.1", "198.51.100.7") == "198.51.100.7"
    assert b.ip_hieu_luc("::1", "9.9.9.9, 198.51.100.7") == "198.51.100.7"
    assert b.ip_hieu_luc("127.0.0.1", None) is None  # healthcheck trực tiếp vẫn miễn


def test_khoa_theo_khach_that_sau_proxy_khong_lan_sang_nguoi_khac(dong_ho):
    b = _bo_cf(dong_ho)
    xau = b.ip_hieu_luc("203.0.113.5", "1.1.1.1, 198.51.100.7")
    lanh = b.ip_hieu_luc("203.0.113.5", "198.51.100.8")
    for _ in range(31):
        b.ghi_loi(xau)
    with pytest.raises(LoiOpenAI):
        b.kiem_tra(xau)
    b.kiem_tra(lanh)  # cùng cạnh proxy nhưng khách khác: không bị chặn lây
    b.kiem_tra("203.0.113.5")  # chính IP của proxy cũng không bị khoá


# ── Tích hợp nguoi_goi: chỉ đếm 401, 403/đúng khoá không bị ──

def test_nguoi_goi_sai_khoa_vuot_nguong_thi_429(bo):
    for _ in range(4):
        with pytest.raises(LoiOpenAI) as e:
            asyncio.run(xac_thuc.nguoi_goi(_yeu_cau("192.0.2.10", khoa=KHOA_SAI)))
        assert e.value.ma == 401
    with pytest.raises(LoiOpenAI) as e:
        asyncio.run(xac_thuc.nguoi_goi(_yeu_cau("192.0.2.10", khoa=KHOA_SAI)))
    assert e.value.ma == 429
    assert e.value.headers["Retry-After"] == "120"
    assert e.value.than["error"]["type"] == "rate_limit_error"


def test_nguoi_goi_dung_khoa_khong_cham(bo):
    _sai(bo, "192.0.2.11", 2)  # dưới ngưỡng
    nguoi = asyncio.run(xac_thuc.nguoi_goi(_yeu_cau("192.0.2.11", khoa=KHOA_QT)))
    assert nguoi["loai"] == "quan_tri"


def test_nguoi_goi_ip_bi_khoa_thi_ca_khoa_dung_cung_429(bo):
    _sai(bo, "192.0.2.12", 4)
    with pytest.raises(LoiOpenAI) as e:
        asyncio.run(xac_thuc.nguoi_goi(_yeu_cau("192.0.2.12", khoa=KHOA_QT)))
    assert e.value.ma == 429  # mọi yêu cầu từ IP bị khoá đều 429


def test_khong_dem_403(bo, tmp_path, monkeypatch):
    from app import khoa_khach
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    _, khoa_tho = khoa_khach.tao_khoa("Khách hẹp", ["chat"])
    with pytest.raises(LoiOpenAI) as e:
        asyncio.run(xac_thuc.nguoi_goi(_yeu_cau("192.0.2.13", khoa=khoa_tho, scope="admin:x")))
    assert e.value.ma == 403
    assert "192.0.2.13" not in bo._ip  # 403 không được đếm


# ── End-to-end qua HTTP: khuôn lỗi OpenAI + Retry-After ──

def test_e2e_429_qua_http(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "3")
    monkeypatch.setenv("WEB2API_RL_CUA_SO_GIAY", "60")
    monkeypatch.setenv("WEB2API_RL_KHOA_GIAY", "120")
    monkeypatch.setenv("WEB2API_TRUST_PROXY", "")
    # TestClient đặt client.host = "testclient" (không phải IP) → cố định IP để kiểm.
    monkeypatch.setattr(gioi_han, "_lay_ip", lambda request: "192.0.2.99")
    with TestClient(app) as c:
        for _ in range(4):
            r = c.get("/v1/models", headers={"Authorization": f"Bearer {KHOA_SAI}"})
            assert r.status_code == 401, r.text
        r = c.get("/v1/models", headers={"Authorization": f"Bearer {KHOA_SAI}"})
        assert r.status_code == 429, r.text
        assert r.headers["retry-after"] == "120"
        than = r.json()
        assert than["error"]["type"] == "rate_limit_error"
        assert KHOA_SAI not in r.text
