"""Khoá đã xác thực ĐÚNG từ một IP không bị khoá lây khi kẻ khác (cùng IP) dò nhiều khoá khác nhau (hậu kiểm NV47/NV99).

Giới hạn còn lại sau NV99: kẻ thử hơn TOI_DA_KHOA_SAI_RIENG_BIET khoá khác nhau từ cùng IP khoá cả IP, gồm khoá đúng của admin cùng IP (NAT/Wi-Fi chung).
Sửa: khoá (băm) từng xác thực đúng từ IP đó được tin cậy TIN_CAY_GIAY; kẻ dò khoá không có băm trong tập nên vẫn bị chặn.
"""

import asyncio

import pytest
from starlette.requests import Request

from app import gioi_han, xac_thuc
from app.gioi_han import GioiHanSaiKhoa, TIN_CAY_GIAY, TOI_DA_TIN_CAY, _bam_khoa_thu
from app.loi_openai import LoiOpenAI
from app.main import app  # noqa: F401  (nạp cấu hình như các test cùng loại)

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
IP = "192.0.2.50"


def _yeu_cau(khoa=None, host=IP):
    headers = [] if khoa is None else [(b"authorization", f"Bearer {khoa}".encode("ascii"))]
    return Request({"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http", "server": ("testserver", 80),
                    "path": "/v1/models", "query_string": b"", "headers": headers, "client": (host, 1234)})


@pytest.fixture
def dong_ho():
    return [1000.0]


@pytest.fixture
def bo(dong_ho, tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "30")
    monkeypatch.setenv("WEB2API_RL_CUA_SO_GIAY", "60")
    monkeypatch.setenv("WEB2API_RL_KHOA_GIAY", "120")
    monkeypatch.setenv("WEB2API_TRUST_PROXY", "")
    b = GioiHanSaiKhoa.tu_moi_truong(dong_ho=lambda: dong_ho[0])
    monkeypatch.setattr(gioi_han, "_BO", b, raising=False)
    return b


def _ke_do_khoa(n=11):
    """Kẻ cùng IP thử n khoá sai KHÁC NHAU qua đúng đường xác thực thật."""
    for i in range(n):
        with pytest.raises(LoiOpenAI):
            asyncio.run(xac_thuc.nguoi_goi(_yeu_cau(f"khoa-sai-khac-nhau-{i:04d}-xxxxxxxxxxxxxxxx")))


def test_admin_da_xac_thuc_van_qua_khi_ip_bi_khoa_vi_ke_do_khoa(bo):
    nguoi = asyncio.run(xac_thuc.nguoi_goi(_yeu_cau(KHOA_QT)))                 # admin đăng nhập đúng trước
    assert nguoi["loai"] == "quan_tri"
    _ke_do_khoa()
    assert bo.liet_ke_khoa(), "kẻ dò khoá đã khoá IP"
    nguoi = asyncio.run(xac_thuc.nguoi_goi(_yeu_cau(KHOA_QT)))                 # khoá tin cậy vẫn qua
    assert nguoi["loai"] == "quan_tri"


def test_ke_do_khoa_van_bi_chan_khi_ip_bi_khoa(bo):
    asyncio.run(xac_thuc.nguoi_goi(_yeu_cau(KHOA_QT)))
    _ke_do_khoa()
    with pytest.raises(LoiOpenAI) as e:                                         # khoá lạ (chưa từng đúng) vẫn 429
        asyncio.run(xac_thuc.nguoi_goi(_yeu_cau("khoa-sai-moi-hoan-toan-yyyyyyyyyyyyyyyy")))
    assert e.value.ma == 429


def test_khoa_dung_nhung_chua_tung_xac_thuc_tu_ip_nay_van_bi_chan(bo):
    """Không tạo oracle: khi IP bị khoá, khoá đúng CHƯA từng xác thực từ IP này không được kiểm (429), nên kẻ dò không biết khoá nào đúng."""
    _ke_do_khoa()
    with pytest.raises(LoiOpenAI) as e:
        asyncio.run(xac_thuc.nguoi_goi(_yeu_cau(KHOA_QT)))
    assert e.value.ma == 429


def test_tin_cay_theo_tung_ip(bo):
    asyncio.run(xac_thuc.nguoi_goi(_yeu_cau(KHOA_QT, host="192.0.2.50")))
    for i in range(11):
        with pytest.raises(LoiOpenAI):
            asyncio.run(xac_thuc.nguoi_goi(_yeu_cau(f"khoa-sai-khac-nhau-{i:04d}-xxxxxxxxxxxxxxxx", host="192.0.2.99")))
    with pytest.raises(LoiOpenAI) as e:                                         # IP khác chưa từng xác thực: vẫn bị khoá
        asyncio.run(xac_thuc.nguoi_goi(_yeu_cau(KHOA_QT, host="192.0.2.99")))
    assert e.value.ma == 429


def test_tin_cay_het_han(bo, dong_ho):
    asyncio.run(xac_thuc.nguoi_goi(_yeu_cau(KHOA_QT)))
    _ke_do_khoa()
    dong_ho[0] += TIN_CAY_GIAY + 1
    bo._ip[IP]["khoa_den"] = dong_ho[0] + 100                                   # IP lại đang bị khoá, nhưng tin cậy đã hết hạn
    with pytest.raises(LoiOpenAI) as e:
        asyncio.run(xac_thuc.nguoi_goi(_yeu_cau(KHOA_QT)))
    assert e.value.ma == 429


def test_khoa_tin_cay_bi_thu_hoi_thi_het_tin_cay(bo, monkeypatch):
    """Khoá từng đúng nhưng nay SAI (đã đổi/thu hồi): lần sai đó gỡ khỏi tập tin cậy; từ đó IP bị khoá thì khoá này cũng 429."""
    asyncio.run(xac_thuc.nguoi_goi(_yeu_cau(KHOA_QT)))
    assert (IP, _bam_khoa_thu(f"Bearer {KHOA_QT}")) in bo._khoa_da_xac_thuc
    monkeypatch.setenv("WEB2API_ADMIN_KEY", "khoa-quan-tri-moi-da-doi-0123456789abcd")   # khoá cũ nay SAI
    with pytest.raises(LoiOpenAI) as e:
        asyncio.run(xac_thuc.nguoi_goi(_yeu_cau(KHOA_QT)))
    assert e.value.ma == 401
    assert (IP, _bam_khoa_thu(f"Bearer {KHOA_QT}")) not in bo._khoa_da_xac_thuc


def test_khong_ghi_tin_cay_khi_khong_co_bearer_va_co_tran(bo):
    bo.ghi_thanh_cong(IP, "")
    assert not bo._khoa_da_xac_thuc
    for i in range(TOI_DA_TIN_CAY + 5):
        bo.ghi_thanh_cong(f"10.0.{i // 250}.{i % 250}", f"{i:032x}")
    assert len(bo._khoa_da_xac_thuc) == TOI_DA_TIN_CAY
