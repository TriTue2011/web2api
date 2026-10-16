"""Kiểm thử năm lỗ hổng hạ tầng vào/ra của lõi (rút từ so sánh bảo mật với c2a).

1. Nhật ký kiểm toán xoá lén được (DELETE /api/nhat-ky, ngưỡng tối thiểu, ghi sổ sau xoá, cảnh báo IP quản trị mới).
2. Hàng chờ băm mật khẩu không bỏ tải (trần hàng chờ băm, 429 Retry-After, tính giới hạn IP).
3. Kiểm tra tiêu đề Host chống DNS rebinding (IP literal, localhost, host công khai, ngoại lệ /health loopback, WebSocket).
4. Cờ Secure của cookie phiên tự động bật khi host khớp PUBLIC_URL là https.
5. Kiểm tra ma trận quyền và tính chính xác của tài liệu triển khai.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import dia_chi, kho, nguoi_dung, nhat_ky, phien_web, xac_thuc
from app.main import app

KHOA_QT = "AIzaSyKIEMNGHIEM-000000000000000000009"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.delenv("WEB2API_NHAT_KY_XOA_TOI_THIEU_NGAY", raising=False)
    monkeypatch.delenv("WEB2API_PUBLIC_URL", raising=False)
    monkeypatch.delenv("WEB2API_TIN_PROXY", raising=False)
    if hasattr(nhat_ky, "xoa_bo_nho_ip_quan_tri"):
        nhat_ky.xoa_bo_nho_ip_quan_tri()
    return tmp_path


@pytest.fixture
def client(du_lieu):
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        yield c


# =====================================================================
# 1. NHẬT KÝ KIỂM TOÁN XOÁ LÉN ĐƯỢC
# =====================================================================

def test_xoa_nhat_ky_duoi_nguong_toi_thieu_bi_400(client, du_lieu, monkeypatch):
    """(b) truoc_ngay dưới ngưỡng tối thiểu (mặc định 1 ngày) bị 400."""
    # Mặc định: truoc_ngay=0 hoặc 0.5 < 1 ngày -> 400
    r0 = client.delete("/api/nhat-ky?truoc_ngay=0", headers=QT)
    assert r0.status_code == 400
    assert "ngưỡng tối thiểu" in r0.text.lower() or "phải trong khoảng" in r0.text.lower()

    r_nua_ngay = client.delete("/api/nhat-ky?truoc_ngay=0.5", headers=QT)
    assert r_nua_ngay.status_code == 400

    # Đặt biến môi trường ngưỡng tối thiểu 7 ngày
    monkeypatch.setenv("WEB2API_NHAT_KY_XOA_TOI_THIEU_NGAY", "7")
    r3 = client.delete("/api/nhat-ky?truoc_ngay=3", headers=QT)
    assert r3.status_code == 400

    r7 = client.delete("/api/nhat-ky?truoc_ngay=7", headers=QT)
    assert r7.status_code == 200


def test_xoa_nhat_ky_ghi_dong_quan_tri_sau_khi_xoa(client, du_lieu):
    """(a) ngay sau khi xoá ghi một dòng kênh quan_tri (số dòng, danh tính, IP, tham số) — còn lại sau xoá."""
    # Tạo bản ghi cũ (10 ngày trước)
    thu_muc = nhat_ky.thu_muc_nhat_ky()
    tep = thu_muc / "nhat_ky.jsonl"
    ngay_cu = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    dong_cu = json.dumps({"luc": ngay_cu, "kenh": "he_thong", "muc": "thong_tin",
                          "tom_tat": "Bản ghi cũ 10 ngày trước", "ms": 1, "them": {}}, ensure_ascii=False) + "\n"
    with open(tep, "a", encoding="utf-8") as f:
        f.write(dong_cu)

    # Xoá nhật ký cũ hơn 2 ngày
    r = client.delete("/api/nhat-ky?truoc_ngay=2", headers=QT)
    assert r.status_code == 200
    assert r.json()["da_xoa"] >= 1

    # Dòng kênh quan_tri ghi nhận việc xoá phải CÒN LẠI trong nhật ký
    tat_ca = nhat_ky.doc(kenh="quan_tri")
    assert len(tat_ca) >= 1
    dong_xoa = [d for d in tat_ca if "xoá" in d["tom_tat"].lower() or "xoa" in d["tom_tat"].lower()]
    assert len(dong_xoa) == 1
    d = dong_xoa[0]
    assert d["muc"] in ("canh_bao", "thong_tin")
    them = d.get("them", {})
    assert "da_xoa" in them
    assert them["da_xoa"] >= 1
    assert "nguoi_goi" in them
    assert them["nguoi_goi"] == "quan_tri"
    assert "ip" in them
    assert "truoc_ngay" in them
    assert them["truoc_ngay"] == 2.0


def test_canh_bao_khoa_quan_tri_dung_tu_ip_moi(client, du_lieu, monkeypatch):
    """(c) ghi một dòng cảnh báo «khoá quản trị dùng từ IP mới» tối đa một lần cho mỗi IP mỗi 24 giờ."""
    monkeypatch.setenv("WEB2API_TRUST_PROXY", "testclient,127.0.0.1")
    from app import gioi_han
    gioi_han.khoi_dong()

    ip_moi_1 = "198.51.100.1"
    headers_1 = {**QT, "X-Forwarded-For": ip_moi_1}

    # Lần đầu gọi từ IP mới -> ghi nhận cảnh báo
    r1 = client.get("/api/he-thong", headers=headers_1)
    assert r1.status_code == 200

    ds_qt = nhat_ky.doc(kenh="quan_tri")
    dong_ip1 = [d for d in ds_qt if "ip mới" in d["tom_tat"].lower() and ip_moi_1 in str(d)]
    assert len(dong_ip1) == 1
    # Tuyệt đối không lộ khoá trong nhật ký
    assert KHOA_QT not in json.dumps(dong_ip1[0], ensure_ascii=False)

    # Gọi lại lần 2 từ cùng IP -> không ghi thêm
    r2 = client.get("/api/he-thong", headers=headers_1)
    assert r2.status_code == 200
    ds_qt2 = nhat_ky.doc(kenh="quan_tri")
    dong_ip1_lan2 = [d for d in ds_qt2 if "ip mới" in d["tom_tat"].lower() and ip_moi_1 in str(d)]
    assert len(dong_ip1_lan2) == 1

    # Gọi từ IP mới thứ hai -> ghi nhận cảnh báo cho IP thứ hai
    ip_moi_2 = "198.51.100.2"
    headers_2 = {**QT, "X-Forwarded-For": ip_moi_2}
    r3 = client.get("/api/he-thong", headers=headers_2)
    assert r3.status_code == 200
    ds_qt3 = nhat_ky.doc(kenh="quan_tri")
    dong_ip2 = [d for d in ds_qt3 if "ip mới" in d["tom_tat"].lower() and ip_moi_2 in str(d)]
    assert len(dong_ip2) == 1


# =====================================================================
# 2. HÀNG CHỜ BĂM MẬT KHẨU KHÔNG BỎ TẢI
# =====================================================================

def test_hang_cho_bam_qua_tran_tra_429_va_tinh_gioi_han_ip(client, du_lieu, monkeypatch):
    """Bắn N+10 lượt song song (đồng bộ bằng sự kiện, không sleep), đúng 10 lượt bị 429,
    các lượt còn lại hoàn tất; quá trần không làm rò việc tên có tồn tại hay không.
    """
    import concurrent.futures
    import threading
    from app import gioi_han

    N = 4
    monkeypatch.setenv("WEB2API_DANG_NHAP_HANG_CHO_TOI_DA", str(N))
    monkeypatch.setenv("WEB2API_TRUST_PROXY", "testclient,127.0.0.1")
    gioi_han.khoi_dong()

    if hasattr(nguoi_dung, "_dem_hang_cho_scrypt"):
        monkeypatch.setattr(nguoi_dung, "_dem_hang_cho_scrypt", 0)

    nd_ton_tai = "admin_hop_le"
    mk_dung = "MatKhauManh123@"
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap=nd_ton_tai, mat_khau=mk_dung))

    event_bat_dau = threading.Event()
    event_xa_hang = threading.Event()
    dem_vao_bam = 0
    khoa_dem = threading.Lock()

    goc_bam = nguoi_dung._bam_mat_khau_dong_bo

    event_du_N = threading.Event()
    def _hook(dem):
        if dem >= N:
            event_du_N.set()
    monkeypatch.setattr(nguoi_dung, "_HOOK_VAO_HANG_CHO", _hook)

    def _bam_gia_lap(mk, muoi=None):
        nonlocal dem_vao_bam
        with khoa_dem:
            dem_vao_bam += 1
            if dem_vao_bam >= 2:
                event_bat_dau.set()
        event_xa_hang.wait(timeout=10.0)
        return goc_bam(mk, muoi)

    monkeypatch.setattr(nguoi_dung, "_bam_mat_khau_dong_bo", _bam_gia_lap)

    tong_so = N + 10

    with concurrent.futures.ThreadPoolExecutor(max_workers=tong_so) as executor:
        def _gui_dang_nhap(i: int):
            ten = nd_ton_tai if i % 2 == 0 else f"nd_khong_ton_tai_{i}"
            res = client.post(
                "/api/dang-nhap",
                json={"ten_dang_nhap": ten, "mat_khau": "MatKhauSaiKhac123@"},
                headers={"Content-Type": "application/json", "X-Forwarded-For": f"198.51.100.{10 + (i % 3)}"},
            )
            return (i, ten, res.status_code, res.headers.get("retry-after"), res.text)

        futures = []
        for i in range(N):
            futures.append(executor.submit(_gui_dang_nhap, i))

        # Đợi chắc chắn N lượt đầu đã vào hàng chờ (đang băm hoặc đang xếp hàng)
        event_du_N.wait(timeout=5.0)

        # Bắn tiếp 10 lượt (sẽ vượt trần và nhận 429 ngay lập tức)
        for i in range(N, tong_so):
            futures.append(executor.submit(_gui_dang_nhap, i))

        cac_luot_tran = [f.result(timeout=5.0) for f in futures[N:]]
        event_xa_hang.set()
        cac_luot_dau = [f.result(timeout=5.0) for f in futures[:N]]

    assert len(cac_luot_tran) == 10
    for idx, ten, code, retry_after, body in cac_luot_tran:
        assert code == 429, f"Lượt {idx} ({ten}) phải nhận 429 nhưng nhận {code}: {body}"
        assert retry_after is not None, f"Lượt {idx} thiếu Retry-After header"
        assert "rate_limit_exceeded" in body or "quá tải" in body.lower()

    assert len(cac_luot_dau) == N
    for idx, ten, code, retry_after, body in cac_luot_dau:
        assert code in (401, 200), f"Lượt {idx} ({ten}) phải hoàn tất nhưng nhận {code}: {body}"

    than_429_ton_tai = [body for idx, ten, code, _, body in cac_luot_tran if ten == nd_ton_tai]
    than_429_khong_ton_tai = [body for idx, ten, code, _, body in cac_luot_tran if ten != nd_ton_tai]
    assert than_429_ton_tai and than_429_khong_ton_tai
    assert than_429_ton_tai[0] == than_429_khong_ton_tai[0]


# =====================================================================
# 3. KIỂM TRA HOST CHỐNG DNS REBINDING
# =====================================================================

def test_kiem_tra_host_ip_literal_va_localhost_qua(client, du_lieu, monkeypatch):
    """Host là IP literal (không rebinding được) hoặc localhost luôn qua."""
    monkeypatch.setenv("WEB2API_PUBLIC_URL", "https://api.example.com")

    # IP literal IPv4
    r1 = client.get("/health", headers={"Host": "127.0.0.1"})
    assert r1.status_code == 200

    r2 = client.get("/api/thiet-lap", headers={"Host": "192.168.1.100"})
    assert r2.status_code == 200

    # IP literal kèm cổng
    r3 = client.get("/api/thiet-lap", headers={"Host": "192.168.1.100:3031"})
    assert r3.status_code == 200

    # IPv6 literal
    r4 = client.get("/api/thiet-lap", headers={"Host": "[::1]"})
    assert r4.status_code == 200

    r5 = client.get("/api/thiet-lap", headers={"Host": "[::1]:3031"})
    assert r5.status_code == 200

    # localhost và *.localhost
    r6 = client.get("/api/thiet-lap", headers={"Host": "localhost"})
    assert r6.status_code == 200

    r7 = client.get("/api/thiet-lap", headers={"Host": "localhost:3031"})
    assert r7.status_code == 200

    r8 = client.get("/api/thiet-lap", headers={"Host": "sub.localhost:3031"})
    assert r8.status_code == 200


def test_kiem_tra_host_ten_la_khi_da_cau_hinh_tra_421(client, du_lieu, monkeypatch):
    """Khi đã cấu hình địa chỉ công khai, tên lạ bị từ chối 421."""
    monkeypatch.setenv("WEB2API_PUBLIC_URL", "https://myapi.example.com")

    # Host đúng theo cấu hình -> 200
    r_dung = client.get("/api/thiet-lap", headers={"Host": "myapi.example.com"})
    assert r_dung.status_code == 200

    r_dung_cong = client.get("/api/thiet-lap", headers={"Host": "myapi.example.com:443"})
    assert r_dung_cong.status_code == 200

    # Tên lạ -> 421
    r_la = client.get("/api/thiet-lap", headers={"Host": "evil.com"})
    assert r_la.status_code == 421

    r_la_cong = client.get("/api/thiet-lap", headers={"Host": "evil.com:3031"})
    assert r_la_cong.status_code == 421


def test_kiem_tra_host_chua_cau_hinh_khong_chan_nhung_ghi_canh_bao(client, du_lieu, monkeypatch):
    """Chưa cấu hình địa chỉ công khai -> không chặn tên lạ nhưng ghi cảnh báo nhật ký một lần."""
    monkeypatch.delenv("WEB2API_PUBLIC_URL", raising=False)
    p = du_lieu / "dia_chi.json"
    p.unlink(missing_ok=True)
    if hasattr(app.state, "_da_canh_bao_host"):
        delattr(app.state, "_da_canh_bao_host")

    # Lần 1: gửi tên lạ -> qua (không chặn để không khoá chủ máy ngoài tunnel)
    r1 = client.get("/api/thiet-lap", headers={"Host": "tunnel.example.com"})
    assert r1.status_code == 200

    # Phải có cảnh báo nhật ký kênh he_thong
    ds = nhat_ky.doc(kenh="he_thong")
    dong_cb = [d for d in ds if "địa chỉ công khai" in d["tom_tat"].lower() or "dns rebinding" in d["tom_tat"].lower()]
    assert len(dong_cb) == 1

    # Lần 2: gửi tiếp tên lạ khác -> không sinh thêm dòng cảnh báo mới
    r2 = client.get("/api/thiet-lap", headers={"Host": "another-tunnel.example.com"})
    assert r2.status_code == 200
    ds2 = nhat_ky.doc(kenh="he_thong")
    dong_cb2 = [d for d in ds2 if "địa chỉ công khai" in d["tom_tat"].lower() or "dns rebinding" in d["tom_tat"].lower()]
    assert len(dong_cb2) == 1


def test_kiem_tra_host_hong_rong_tra_400_hoac_421(client, du_lieu, monkeypatch):
    """Host hỏng hoặc rỗng bị 400 hoặc 421."""
    monkeypatch.setenv("WEB2API_PUBLIC_URL", "https://myapi.example.com")

    r_rong = client.get("/api/thiet-lap", headers={"Host": ""})
    assert r_rong.status_code in (400, 421)

    r_hong = client.get("/api/thiet-lap", headers={"Host": "evil.com:invalid_port"})
    assert r_hong.status_code in (400, 421)

    r_hong2 = client.get("/api/thiet-lap", headers={"Host": "[invalid_ipv6"})
    assert r_hong2.status_code in (400, 421)


def test_kiem_tra_host_ngoai_le_health_loopback_luon_qua(client, du_lieu, monkeypatch):
    """/health từ loopback luôn qua kể cả khi Host lạ."""
    monkeypatch.setenv("WEB2API_PUBLIC_URL", "https://myapi.example.com")

    # Gọi /health từ loopback (client=("127.0.0.1", 50000)) với Host lạ
    r = client.get("/health", headers={"Host": "evil.com"})
    assert r.status_code == 200


def test_kiem_tra_host_websocket_bi_chan_khi_host_la(client, du_lieu, monkeypatch):
    """Đường WebSocket (scope websocket như noVNC) cũng chịu kiểm tra Host."""
    monkeypatch.setenv("WEB2API_PUBLIC_URL", "https://myapi.example.com")

    # Kết nối websocket với Host lạ -> bị từ chối / đóng
    with pytest.raises(Exception):
        with client.websocket_connect("/novnc/s/test_session/websockify", headers={"Host": "evil.com"}):
            pass


# =====================================================================
# 4. CỜ SECURE CỦA COOKIE PHIÊN TỰ ĐỘNG BẬT KHI HOST KHỚP PUBLIC_URL HTTPS
# =====================================================================

def test_cookie_secure_khi_host_khop_public_url_https_du_chua_bat_tin_proxy(client, du_lieu, monkeypatch):
    """la_https cũng đúng khi địa chỉ công khai đã cấu hình là https:// VÀ yêu cầu đến từ host đó;
    ghi cảnh báo nhật ký (một lần) khi PUBLIC_URL là https nhưng TIN_PROXY chưa bật.
    Cookie có Secure và __Host-w2a_phien trong trường hợp này.
    """
    monkeypatch.setenv("WEB2API_PUBLIC_URL", "https://api.example.com")
    monkeypatch.delenv("WEB2API_TIN_PROXY", raising=False)
    if hasattr(phien_web, "xoa_canh_bao_tin_proxy"):
        phien_web.xoa_canh_bao_tin_proxy()

    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin_sec", mat_khau="MatKhauManh123@"))

    # Lần 1: gửi đăng nhập với Host = api.example.com (HTTP thông thường, không tin proxy)
    res1 = client.post(
        "/api/dang-nhap",
        json={"ten_dang_nhap": "admin_sec", "mat_khau": "MatKhauManh123@"},
        headers={"Host": "api.example.com"},
    )
    assert res1.status_code == 200
    set_cookie1 = res1.headers.get("set-cookie", "")
    assert "__Host-w2a_phien" in set_cookie1
    assert "secure" in set_cookie1.lower()

    # Phải có cảnh báo nhật ký kênh he_thong một lần
    ds1 = nhat_ky.doc(kenh="he_thong")
    dong_cb1 = [
        d for d in ds1
        if "tin_proxy" in d["tom_tat"].lower() or "tin_proxy" in str(d.get("them", {})).lower() or "proxy" in d["tom_tat"].lower()
    ]
    assert len(dong_cb1) == 1
    assert "https" in dong_cb1[0]["tom_tat"].lower() or "https" in str(dong_cb1[0].get("them", {})).lower()

    # Lần 2: gửi đăng nhập tiếp từ cùng Host -> vẫn Secure nhưng không ghi thêm cảnh báo
    res2 = client.post(
        "/api/dang-nhap",
        json={"ten_dang_nhap": "admin_sec", "mat_khau": "MatKhauManh123@"},
        headers={"Host": "api.example.com"},
    )
    assert res2.status_code == 200
    set_cookie2 = res2.headers.get("set-cookie", "")
    assert "__Host-w2a_phien" in set_cookie2
    assert "secure" in set_cookie2.lower()

    ds2 = nhat_ky.doc(kenh="he_thong")
    dong_cb2 = [
        d for d in ds2
        if "tin_proxy" in d["tom_tat"].lower() or "tin_proxy" in str(d.get("them", {})).lower() or "proxy" in d["tom_tat"].lower()
    ]
    assert len(dong_cb2) == 1


def test_cookie_khong_secure_khi_truy_cap_truc_tiep_ip_http(client, du_lieu, monkeypatch):
    """Khi truy cập trực tiếp bằng IP http, cookie KHÔNG có cờ Secure và dùng tên w2a_phien thường."""
    monkeypatch.setenv("WEB2API_PUBLIC_URL", "https://api.example.com")
    monkeypatch.delenv("WEB2API_TIN_PROXY", raising=False)

    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin_ip", mat_khau="MatKhauManh123@"))

    res = client.post(
        "/api/dang-nhap",
        json={"ten_dang_nhap": "admin_ip", "mat_khau": "MatKhauManh123@"},
        headers={"Host": "127.0.0.1"},
    )
    assert res.status_code == 200
    set_cookie = res.headers.get("set-cookie", "")
    assert "w2a_phien" in set_cookie
    assert "__Host-" not in set_cookie
    # Cookie không được chứa cờ Secure
    assert "secure" not in set_cookie.lower()


# =====================================================================
# 5. TÀI LIỆU TRIỂN KHAI VÀ XÁC THỰC BỐN ĐƯỜNG API QUẢN TRỊ
# =====================================================================

def test_tai_lieu_trien_khai_va_xac_thuc_bon_duong_api(client, du_lieu):
    """docs/TRIEN-KHAI.md không còn ghi 4 đường /api/* là đường mở,
    và cả 4 đường đều từ chối 401 khi không có khoá, chấp nhận khi có khoá QT.
    """
    goc = Path(__file__).resolve().parent.parent
    nd_tk = (goc / "docs" / "TRIEN-KHAI.md").read_text(encoding="utf-8")

    # Tài liệu không còn coi 4 đường này là ngoại lệ mở
    assert "bốn đường /api/he-thong" not in nd_tk
    assert "chặn chúng ở Cloudflare" not in nd_tk

    # Kiểm tra cả 4 đường: không khoá -> 401
    duong_ds = [
        "/api/he-thong",
        "/api/modules",
        "/api/modules/catalog",
        "/api/tinh-nang",
    ]
    for d in duong_ds:
        r_khong_khoa = client.get(d)
        assert r_khong_khoa.status_code == 401, f"{d} không khoá phải trả 401 nhưng trả {r_khong_khoa.status_code}"

        r_co_khoa = client.get(d, headers=QT)
        assert r_co_khoa.status_code == 200, f"{d} có khoá QT phải trả 200 nhưng trả {r_co_khoa.status_code}"


def test_la_https_loi_phan_tich_tra_ve_false_va_ghi_canh_bao(monkeypatch, du_lieu):
    """Khi phân tích địa chỉ trong la_https gặp lỗi:
    - Trả về False (fail-closed, không tự ý bật Secure/tin cậy proxy);
    - Ghi cảnh báo nhật ký không nuốt âm thầm;
    - Cảnh báo không chứa thông tin nhạy cảm.
    """
    from starlette.requests import Request
    from app import phien_web, dia_chi, nhat_ky
    import urllib.parse

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"host", b"api.example.com")],
        "scheme": "http",
    }
    req = Request(scope)

    monkeypatch.setattr(dia_chi, "dia_chi_cong_khai_da_cau_hinh", lambda: "https://api.example.com")
    monkeypatch.setattr(dia_chi, "la_tin_proxy", lambda: False)

    orig_split = urllib.parse.urlsplit

    def _split_loi(url, *args, **kwargs):
        if url == "https://api.example.com":
            raise ValueError("Lỗi phân tích URL mô phỏng")
        return orig_split(url, *args, **kwargs)

    monkeypatch.setattr(urllib.parse, "urlsplit", _split_loi)

    ket_qua = phien_web.la_https(req)
    assert ket_qua is False

    ds = nhat_ky.doc(kenh="he_thong")
    dong_cb = [
        d for d in ds
        if "Phân tích địa chỉ HTTPS trong la_https" in d.get("tom_tat", "")
    ]
    assert len(dong_cb) >= 1
    noi_dung_log = str(dong_cb[0]).lower()
    for tu_khoa in ("password", "secret", "token", "khoa_bi_mat", "bearer"):
        assert tu_khoa not in noi_dung_log


