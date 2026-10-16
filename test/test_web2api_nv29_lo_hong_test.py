"""NV29 — Lấp 3 lỗ hổng test do NV26b tìm ra (CL6, CL3, DN4).

Mỗi test chạy lại đúng đột biến gốc của NV26b và phải ĐỎ khi đột biến được áp:
- CL6: `che_log.py` bỏ che `record.msg` (giữ che `args`) → token nối trực tiếp trong msg lọt.
- CL3: `che_log.py` đặt `record.args = None` → vỡ formatter kiểu uvicorn access log.
- DN4: `tuyen.py` route /api/dang-nhap lấy IP từ XFF thô thay vì `lay_ip_an_toan`.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app import gioi_han, nguoi_dung
from app.che_log import CheBiMat
from app.loi_openai import LoiOpenAI
from app.main import app  # noqa: F401 — nạp app để che_log.cai_dat() có hiệu lực
from app.so_module import SoModule

# Token GIẢ (cùng chuỗi test_web2api_nv13.py dùng — trong allowlist gitleaks).
TOKEN_GIA = "bot123456:TEST-FAKE-TOKEN-abcdef1234567890abcdef1234567890"

KHOA_QT = "khoa-quan-tri-test-nv29-lo-hong-0123456789abcdef"


# ───────── CL6: token nối trực tiếp trong msg ─────────

def test_cl6_token_noi_truc_tiep_trong_msg_bi_che():
    """Đột biến CL6 (bỏ che record.msg, giữ che args) → token lọt → ĐỎ.

    Test cũ chỉ đặt token qua đối số %s nên không bắt được đột biến này.
    """
    record = logging.LogRecord(
        "web2api.test_nv29_cl6", logging.WARNING, __file__, 10,
        "Lỗi trực tiếp: " + TOKEN_GIA, (), None,
    )
    CheBiMat().filter(record)
    assert TOKEN_GIA not in record.getMessage()


def test_cl6_qua_logger_that_voi_fstring(caplog):
    """Đường thật: log.warning(f"...{TOKEN}") qua nhà máy đã cài ở app.main → token bị che."""
    caplog.set_level(logging.DEBUG)
    log = logging.getLogger("web2api.test_nv29_cl6_that")
    log.warning(f"Kết nối tới API đứt: {TOKEN_GIA}")
    assert TOKEN_GIA not in caplog.text


# ───────── CL3: args giữ nguyên cấu trúc tuple ─────────

def test_cl3_args_giu_nguyen_cau_truc_tuple_sau_loc():
    """Đột biến CL3 (record.args = None) → unpack/getMessage ném lỗi → ĐỎ.

    Mô phỏng bản ghi kiểu uvicorn access log: msg có 5 chỗ %s, args là bộ năm phần.
    Sau filter, args vẫn là tuple 5 phần tử và AccessFormatter mở ra không ném lỗi.
    """
    msg = '%s - "%s %s %s" %s'
    args = ("203.0.113.5", "GET", "/api/x?token=" + TOKEN_GIA, "HTTP/1.1", 200)
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 10, msg, args, None)
    CheBiMat().filter(record)
    assert isinstance(record.args, tuple), f"args phải còn là tuple, nhận {type(record.args)}"
    assert len(record.args) == 5, f"args phải còn đủ 5 phần tử, nhận {len(record.args)}"
    # AccessFormatter-style: mở bộ năm phần không ném lỗi
    client_addr, method, full_path, http_version, status_code = record.args
    assert (client_addr, method, http_version, status_code) == ("203.0.113.5", "GET", "HTTP/1.1", 200)
    # token trong args vẫn bị che, thông điệp còn định dạng được
    assert TOKEN_GIA not in record.getMessage()
    assert TOKEN_GIA not in str(record.args)


# ───────── DN4: XFF giả qua route /api/dang-nhap ─────────

PEER_KHONG_TIN = "203.0.113.99"   # peer ngoài trực tiếp, không trong WEB2API_TRUST_PROXY
XFF_GIA = "198.51.100.7"          # XFF do kẻ tấn công tự gõ
TEN_DN4 = "dn4nguoidung"           # khớp TEN_DANG_NHAP_RE nhưng không tồn tại


@pytest.fixture
def moi_truong_dn4(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "30")
    monkeypatch.setenv("WEB2API_RL_CUA_SO_GIAY", "60")
    monkeypatch.setenv("WEB2API_RL_KHOA_GIAY", "60")
    monkeypatch.setenv("WEB2API_DANG_NHAP_SAI_TOI_DA", "5")
    monkeypatch.setenv("WEB2API_DANG_NHAP_TOAN_CUC_SAI_TOI_DA", "50")
    monkeypatch.setenv("WEB2API_TRUST_PROXY", "")  # không tin proxy nào
    nguoi_dung._THEO_DOI_SAI.clear()
    gioi_han.khoi_dong()
    with TestClient(app) as client:
        # Giả peer ngoài không tin cậy (mẫu test_web2api_gioi_han.py)
        client._transport.client = (PEER_KHONG_TIN, 1234)
        client.app.state.so = SoModule()
        yield client


def test_dn4_xff_gia_qua_route_khong_doi_duoc_ip_khoa(moi_truong_dn4):
    """Đột biến DN4 (route lấy IP từ XFF thô) → cặp bị khoá là (XFF_GIA, tên) thay vì
    (PEER_KHONG_TIN, tên) → hai assert dưới đảo ngược → ĐỎ.

    Đi qua route /api/dang-nhap THẬT với peer không tin cậy + XFF giả: 5 lần sai phải
    khoá cặp (peer IP, tên), XFF giả không đổi được IP dùng để khoá.
    """
    client = moi_truong_dn4
    for _ in range(5):
        r = client.post(
            "/api/dang-nhap",
            json={"ten_dang_nhap": TEN_DN4, "mat_khau": "SaiMatKhauDN4"},
            headers={"x-forwarded-for": XFF_GIA},
        )
        assert r.status_code == 401

    # Cặp (peer IP thật, tên) bị khoá tầng 1…
    with pytest.raises(LoiOpenAI):
        nguoi_dung.kiem_tra_khoa_ten(TEN_DN4, ip=PEER_KHONG_TIN)
    # …còn cặp (XFF giả, tên) KHÔNG bị khoá: XFF giả không đổi được IP khoá.
    nguoi_dung.kiem_tra_khoa_ten(TEN_DN4, ip=XFF_GIA)
