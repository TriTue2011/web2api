"""Cổng IP cho đường quản trị (cẩm nang §25): WEB2API_QT_IP_CHO_PHEP.

Khuôn test/test_web2api_ma_tran_quyen.py: dùng lại thu_thap_routes, phan_loai_route,
thay_the_tham_so để khẳng định TỰ ĐỘNG cho MỌI route lớp «quan_tri».

Giả IP khách bằng thuộc tính `client` của transport trong starlette TestClient
(scope["client"] lấy từ đó mỗi request) — đi qua đúng đường phân giải IP thật.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_web2api_ma_tran_quyen import (  # noqa: E402
    KHOA_QT,
    cac_phuong_thuc_chinh,
    phan_loai_route,
    thay_the_tham_so,
    thu_thap_routes,
)

from app.main import app  # noqa: E402

TEN_BIEN = "WEB2API_QT_IP_CHO_PHEP"
IP_TRONG = "192.0.2.5"      # TEST-NET-1, nằm trong 192.0.2.0/24
IP_NGOAI = "203.0.113.9"    # TEST-NET-3, ngoài danh sách
MANG_NHA = "192.0.2.0/24"   # dải tài liệu RFC 5737 dùng cho ví dụ/test
IP_PROXY = "198.51.100.7"   # TEST-NET-2: giả làm proxy tin cậy (cloudflared) trong test XFF


def _routes_quan_tri():
    return [r for r in thu_thap_routes(app) if phan_loai_route(r) == "quan_tri"]


def _goi(client, route, headers_them=None):
    """Gọi route một lần bằng phương thức chính đầu tiên; trả response."""
    phuong_thuc = cac_phuong_thuc_chinh(route.methods)[0]
    duong = thay_the_tham_so(route.path)
    headers = {"Authorization": f"Bearer {KHOA_QT}", **(headers_them or {})}
    return client.request(phuong_thuc, duong, headers=headers)


def _la_chan_ip(r) -> bool:
    """Response/dict có phải 403 ip_not_allowed không."""
    try:
        than = r.json() if hasattr(r, "json") else r
        return than["error"]["code"] == "ip_not_allowed"
    except Exception:
        return False


def _bi_chan(r) -> bool:
    """Response có phải bị chặn IP không — an toàn khi thân không phải JSON."""
    if not r.headers.get("content-type", "").startswith("application/json"):
        return False
    return _la_chan_ip(r)


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    from app import ip_cho_phep

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")  # tắt đếm sai khoá, trừ test riêng
    monkeypatch.delenv(TEN_BIEN, raising=False)
    monkeypatch.delenv("WEB2API_TRUST_PROXY", raising=False)
    ip_cho_phep._LAN_GHI_CUOI.clear()
    return monkeypatch


@pytest.fixture
def khach(moi_truong):
    with TestClient(app) as c:
        yield c


def _dat_ip(khach, ip: str):
    """Gán IP peer cho TestClient (scope['client'] của starlette lấy từ đây mỗi request)."""
    khach._transport.client = (ip, 1234)


# ── Mặc định TẮT ─────────────────────────────────────────────────────────────

def test_mac_dinh_tat_khong_han_che(khach):
    """Không đặt biến: peer không phải IP ("testclient") + khoá đúng vẫn qua cổng."""
    for route in _routes_quan_tri():
        r = _goi(khach, route)
        assert not _bi_chan(r), f"{route.path} bị chặn IP dù biến chưa đặt"


# ── Ma trận cho MỌI route quản trị ────────────────────────────────────────────

def test_ma_tran_ip_ngoai_danh_sach_bi_403(khach, moi_truong):
    """Bật danh sách: IP ngoài + khoá quản trị ĐÚNG → 403 ip_not_allowed trên MỌI route quan_tri."""
    moi_truong.setenv(TEN_BIEN, MANG_NHA)
    _dat_ip(khach, IP_NGOAI)
    for route in _routes_quan_tri():
        r = _goi(khach, route)
        assert r.status_code == 403, f"{route.path}: mong 403, được {r.status_code}"
        than = r.json()
        assert than["error"]["code"] == "ip_not_allowed", f"{route.path}: {than}"
        assert IP_NGOAI not in r.text, f"{route.path}: thân lỗi lộ IP khách"
        assert MANG_NHA not in r.text, f"{route.path}: thân lỗi lộ danh sách"


def test_ma_tran_ip_trong_danh_sach_qua_cong(khach, moi_truong):
    """Bật danh sách: IP trong dải → KHÔNG route nào trả ip_not_allowed."""
    moi_truong.setenv(TEN_BIEN, MANG_NHA)
    _dat_ip(khach, IP_TRONG)
    for route in _routes_quan_tri():
        r = _goi(khach, route)
        assert not _bi_chan(r), f"{route.path} chặn nhầm IP trong danh sách"


# ── Các bẫy ──────────────────────────────────────────────────────────────────

def test_vong_lap_truc_tiep_duoc_phep(khach, moi_truong):
    """Loopback trực tiếp, không XFF → qua (healthcheck, docker exec để khôi phục)."""
    moi_truong.setenv(TEN_BIEN, MANG_NHA)
    _dat_ip(khach, "127.0.0.1")
    for route in _routes_quan_tri()[:5]:
        r = _goi(khach, route)
        assert not _bi_chan(r), f"{route.path} chặn loopback trực tiếp"


def test_peer_khong_phai_ip_bi_tu_choi(khach, moi_truong):
    """Peer không phân tích được thành IP → TỪ CHỐI (đóng khi lỗi), dù có khoá đúng."""
    moi_truong.setenv(TEN_BIEN, MANG_NHA)
    _dat_ip(khach, "testclient")  # như TestClient mặc định đặt
    for route in _routes_quan_tri()[:5]:
        r = _goi(khach, route)
        assert r.status_code == 403 and _la_chan_ip(r.json()), f"{route.path} cho qua peer không phải IP"


def test_ipv4_mapped_duoc_so_nhu_ipv4(khach, moi_truong):
    """::ffff:192.0.2.5 phải khớp 192.0.2.0/24 như IPv4."""
    moi_truong.setenv(TEN_BIEN, MANG_NHA)
    _dat_ip(khach, "::ffff:192.0.2.5")
    for route in _routes_quan_tri()[:5]:
        r = _goi(khach, route)
        assert not _bi_chan(r), f"{route.path} không so IPv4-mapped như IPv4"


def test_cidr_hong_bi_bo_qua(khach, moi_truong):
    """Mục CIDR hỏng bị bỏ qua (có cảnh báo), mục tốt vẫn có hiệu lực."""
    moi_truong.setenv(TEN_BIEN, f"{MANG_NHA}, khong-phai-cidr")
    _dat_ip(khach, IP_TRONG)
    r = _goi(khach, _routes_quan_tri()[0])
    assert not _bi_chan(r), "mục CIDR tốt bị vô hiệu vì mục hỏng bên cạnh"
    _dat_ip(khach, IP_NGOAI)
    r = _goi(khach, _routes_quan_tri()[0])
    assert r.status_code == 403 and _la_chan_ip(r.json())


def test_tat_ca_cidr_hong_thi_dong_tru_vong_lap(khach, moi_truong):
    """Có giá trị mà TẤT CẢ đều hỏng → chặn mọi thứ, trừ loopback trực tiếp."""
    moi_truong.setenv(TEN_BIEN, "hong-1, hong-2")
    _dat_ip(khach, IP_TRONG)
    r = _goi(khach, _routes_quan_tri()[0])
    assert r.status_code == 403 and _la_chan_ip(r.json()), "tất cả CIDR hỏng phải đóng khi lỗi"
    _dat_ip(khach, "127.0.0.1")
    r = _goi(khach, _routes_quan_tri()[0])
    assert not _bi_chan(r), "loopback trực tiếp phải qua ngay cả khi cấu hình hỏng hết"


def test_doi_bien_khong_can_khoi_dong_lai(khach, moi_truong):
    """Đọc biến MỖI lượt: đổi env giữa hai request, không khởi động lại."""
    route = _routes_quan_tri()[0]
    _dat_ip(khach, IP_NGOAI)
    moi_truong.setenv(TEN_BIEN, MANG_NHA)
    assert _la_chan_ip(_goi(khach, route).json())
    moi_truong.setenv(TEN_BIEN, "")  # tắt
    assert not _bi_chan(_goi(khach, route))
    moi_truong.setenv(TEN_BIEN, "203.0.113.0/24")  # bật lại với dải chứa IP_NGOAI
    assert not _bi_chan(_goi(khach, route))


def test_duong_khach_va_mo_khong_bi_anh_huong(khach, moi_truong):
    """Lớp «khách» (/v1) và đường mở không đi qua cổng IP quản trị."""
    moi_truong.setenv(TEN_BIEN, MANG_NHA)
    _dat_ip(khach, IP_NGOAI)
    r = khach.get("/v1/models", headers={"Authorization": f"Bearer {KHOA_QT}"})
    assert r.status_code == 200, r.text
    assert not _la_chan_ip(r.json())
    r = khach.get("/health")
    assert r.status_code == 200, r.text


def test_khong_dem_vao_gioi_han_sai_khoa(khach, moi_truong):
    """403 ip_not_allowed KHÔNG phải lỗi xác thực: không bị đếm vào bộ đếm sai khoá."""
    from app import gioi_han

    moi_truong.setenv("WEB2API_RL_LOI_XAC_THUC", "3")
    moi_truong.setenv("WEB2API_RL_CUA_SO_GIAY", "60")
    moi_truong.setenv("WEB2API_RL_KHOA_GIAY", "120")
    gioi_han.khoi_dong()  # nạp lại cấu hình giới hạn từ env vừa đặt
    moi_truong.setenv(TEN_BIEN, MANG_NHA)
    _dat_ip(khach, IP_NGOAI)
    route = _routes_quan_tri()[0]
    for _ in range(5):  # vượt ngưỡng 3 — nếu bị đếm thì đã 429
        r = _goi(khach, route)
        assert r.status_code == 403 and _la_chan_ip(r.json()), f"lần này phải 403 ip_not_allowed, được {r.status_code}"


def test_ghi_nhat_ky_mot_dong(khach, moi_truong):
    """Từ chối ghi dòng cảnh báo kênh quan_tri: có IP đã làm sạch + đường, không có khoá."""
    from app import nhat_ky

    moi_truong.setenv(TEN_BIEN, MANG_NHA)
    _dat_ip(khach, IP_NGOAI)
    route = _routes_quan_tri()[0]
    r = _goi(khach, route)
    assert r.status_code == 403
    dong = nhat_ky.doc(kenh="quan_tri", muc="canh_bao", q="ip_not_allowed", gioi_han=5)
    assert dong, "không thấy dòng nhật ký từ chối IP"
    tom_tat = dong[0]["tom_tat"]
    assert IP_NGOAI in tom_tat and route.path in tom_tat
    assert KHOA_QT not in tom_tat


# ── Bổ sung của người soát (Claude): giả mạo XFF, qua proxy tin cậy, chống ngập nhật ký ──

def test_xff_gia_tu_peer_khong_tin_cay_khong_qua_cong(khach, moi_truong):
    """Peer ở NGOÀI danh sách tự gõ `X-Forwarded-For: <IP trong dải nhà>` KHÔNG được lọt: XFF chỉ được tin
    khi peer nằm trong WEB2API_TRUST_PROXY (hoặc là loopback)."""
    moi_truong.setenv(TEN_BIEN, MANG_NHA)
    _dat_ip(khach, IP_NGOAI)
    for route in _routes_quan_tri()[:5]:
        r = _goi(khach, route, {"X-Forwarded-For": IP_TRONG})
        assert r.status_code == 403 and _la_chan_ip(r.json()), f"{route.path}: XFF giả đã vượt cổng IP"


def test_qua_proxy_tin_cay_dung_ip_khach_that(khach, moi_truong):
    """Peer là proxy tin cậy: IP khách thật lấy từ phần tử không-tin-cậy NGOÀI CÙNG BÊN PHẢI của XFF.
    Khách trong dải nhà → qua; khách ngoài → chặn; phần giả bên trái XFF không đổi được kết quả."""
    from app import gioi_han

    moi_truong.setenv("WEB2API_TRUST_PROXY", f"{IP_PROXY}/32")
    gioi_han.khoi_dong()
    moi_truong.setenv(TEN_BIEN, MANG_NHA)
    _dat_ip(khach, IP_PROXY)
    route = _routes_quan_tri()[0]
    # khách thật ở nhà (proxy nối IP kết nối vào cuối)
    assert not _bi_chan(_goi(khach, route, {"X-Forwarded-For": IP_TRONG}))
    # khách thật ở ngoài, tự gõ thêm IP nhà ở bên TRÁI để giả mạo → vẫn bị chặn
    r = _goi(khach, route, {"X-Forwarded-For": f"{IP_TRONG}, {IP_NGOAI}"})
    assert r.status_code == 403 and _la_chan_ip(r.json()), "phần giả bên trái XFF đã vượt cổng IP"
    # khách thật ở ngoài, không giả
    r = _goi(khach, route, {"X-Forwarded-For": IP_NGOAI})
    assert r.status_code == 403 and _la_chan_ip(r.json())


def test_nhat_ky_toi_da_mot_dong_moi_ip_va_lam_sach_duong(khach, moi_truong):
    """Kẻ quét gọi liên tục: chỉ MỘT dòng cảnh báo mỗi IP trong cửa sổ; đường dẫn dài/độc không vào nhật ký nguyên văn."""
    from app import nhat_ky

    moi_truong.setenv(TEN_BIEN, MANG_NHA)
    _dat_ip(khach, IP_NGOAI)
    duong_doc = "/api/" + "A" * 3000 + "%0A%1B[31mDO"
    for _ in range(30):
        r = khach.get(duong_doc, headers={"Authorization": f"Bearer {KHOA_QT}"})
        assert r.status_code in (403, 404)
    dong = nhat_ky.doc(kenh="quan_tri", muc="canh_bao", q="ip_not_allowed", gioi_han=50)
    # đường rơi 404 trước khi tới cổng nên chuyển sang một route quản trị thật để chắc có dòng log
    route = _routes_quan_tri()[0]
    for _ in range(30):
        _goi(khach, route)
    dong = nhat_ky.doc(kenh="quan_tri", muc="canh_bao", q="ip_not_allowed", gioi_han=50)
    assert len([d for d in dong if IP_NGOAI in d["tom_tat"]]) == 1, "nhật ký bị ngập: phải tối đa một dòng mỗi IP"
    for d in dong:
        assert "A" * 130 not in d["tom_tat"] and "\x1b" not in d["tom_tat"] and "\n" not in d["tom_tat"]


# ── Đơn vị: phân loại IP hiệu lực (bẫy a) ─────────────────────────────────────

def test_phan_loai_ip_hieu_luc(moi_truong):
    from app import gioi_han

    ip, loai = gioi_han.ip_hieu_luc_phan_loai("127.0.0.1", None)
    assert loai == "vong_lap" and ip == "127.0.0.1"
    ip, loai = gioi_han.ip_hieu_luc_phan_loai("::1", None)
    assert loai == "vong_lap"
    assert gioi_han.ip_hieu_luc_phan_loai("testclient", None)[1] == "khong_xac_dinh"
    assert gioi_han.ip_hieu_luc_phan_loai("", None)[1] == "khong_xac_dinh"
    assert gioi_han.ip_hieu_luc_phan_loai("192.0.2.5", None) == ("192.0.2.5", "ip")
    # loopback kèm XFF không rút được IP khách → đóng khi lỗi (không miễn cả luồng)
    assert gioi_han.ip_hieu_luc_phan_loai("127.0.0.1", "127.0.0.1")[1] == "khong_xac_dinh"
