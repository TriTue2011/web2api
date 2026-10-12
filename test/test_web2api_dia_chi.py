"""Kiểm tra địa chỉ truy cập công khai và soạn hướng dẫn kết nối (Đợt F).

Bảo đảm:
- Thứ tự ưu tiên 3 nguồn: giá trị đã đặt (dia_chi.json) > biến môi trường (WEB2API_PUBLIC_URL) > suy ra từ request (Host).
- Header giả bị bỏ khi WEB2API_TIN_PROXY tắt, được dùng khi bật (kể cả header độc → bỏ).
- URL sai (credentials, scheme lạ, quá dài, có query/fragment) bị từ chối 400 khuôn OpenAI.
- Cảnh báo HTTP công khai vs IP riêng (192.168/10/172.16-31/127/::1/.local không cảnh báo).
- GET /api/ket-noi không chứa khoá thật và không đặt khoá vào URL.
- Cả hai API yêu cầu khoá quản trị (401 khi thiếu/sai).
- Ghi nguyên tử dia_chi.json theo khuôn mẫu app/kho.py.
- Bốn điều kiện nghiệm thu chung.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app import kho, xac_thuc
from app.dia_chi import dia_chi_hien_tai, kiem_tra_va_chuan_hoa_url, lay_thong_tin_dia_chi
from app.main import app

KHOA_QT = "khoa-quan-tri-dia-chi-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.delenv("WEB2API_PUBLIC_URL", raising=False)
    monkeypatch.delenv("WEB2API_TIN_PROXY", raising=False)
    return tmp_path


@pytest.fixture
def client(du_lieu):
    with TestClient(app) as c:
        yield c


def test_dia_chi_thu_tu_uu_tien_3_nguon(client, du_lieu, monkeypatch):
    # 1. Mặc định chưa đặt gì: suy ra từ request
    r = client.get("/api/dia-chi", headers=QT)
    assert r.status_code == 200
    kq = r.json()
    assert kq["nguon"] == "suy_ra"
    assert kq["dang_doan"] is True
    assert kq["tin_proxy"] is False
    assert kq["url"].startswith("http://")
    assert any("đoán" in c.lower() for c in kq["canh_bao"])

    # 2. Đặt biến môi trường WEB2API_PUBLIC_URL
    monkeypatch.setenv("WEB2API_PUBLIC_URL", "https://env.example.com")
    r = client.get("/api/dia-chi", headers=QT)
    assert r.status_code == 200
    kq = r.json()
    assert kq["nguon"] == "bien_moi_truong"
    assert kq["dang_doan"] is False
    assert kq["url"] == "https://env.example.com"
    assert not any("đoán" in c.lower() for c in kq["canh_bao"])

    # 3. Đặt giá trị qua PUT /api/dia-chi (ưu tiên cao nhất, đè biến môi trường)
    r = client.put("/api/dia-chi", headers=QT, json={"url": "https://custom.example.com"})
    assert r.status_code == 200
    kq = r.json()
    assert kq["nguon"] == "da_dat"
    assert kq["dang_doan"] is False
    assert kq["url"] == "https://custom.example.com"

    # Kiểm tra tệp dia_chi.json trên đĩa
    p = du_lieu / "dia_chi.json"
    assert p.is_file()
    assert json.loads(p.read_text(encoding="utf-8")) == {"url": "https://custom.example.com"}

    # Gọi GET /api/dia-chi để chắc chắn giá trị đã lưu được dùng
    r = client.get("/api/dia-chi", headers=QT)
    assert r.status_code == 200
    kq = r.json()
    assert kq["nguon"] == "da_dat"
    assert kq["url"] == "https://custom.example.com"

    # 4. Xoá giá trị đã đặt bằng PUT /api/dia-chi {"url": null}
    r = client.put("/api/dia-chi", headers=QT, json={"url": None})
    assert r.status_code == 200
    kq = r.json()
    # Khi xoá, fallback về biến môi trường
    assert kq["nguon"] == "bien_moi_truong"
    assert kq["url"] == "https://env.example.com"
    assert not p.exists()

    # 5. Bỏ biến môi trường, fallback về suy ra
    monkeypatch.delenv("WEB2API_PUBLIC_URL", raising=False)
    r = client.get("/api/dia-chi", headers=QT)
    assert r.status_code == 200
    kq = r.json()
    assert kq["nguon"] == "suy_ra"
    assert kq["dang_doan"] is True


def test_tin_proxy_va_header_doc(client, monkeypatch):
    # Mặc định: KHÔNG tin proxy, bỏ qua X-Forwarded-*
    headers = dict(QT)
    headers["X-Forwarded-Proto"] = "https"
    headers["X-Forwarded-Host"] = "proxy.example.com"
    r = client.get("/api/dia-chi", headers=headers)
    assert r.status_code == 200
    kq = r.json()
    assert kq["tin_proxy"] is False
    assert kq["url"] != "https://proxy.example.com"
    assert kq["url"].startswith("http://")

    # Bật WEB2API_TIN_PROXY=1
    monkeypatch.setenv("WEB2API_TIN_PROXY", "1")
    r = client.get("/api/dia-chi", headers=headers)
    assert r.status_code == 200
    kq = r.json()
    assert kq["tin_proxy"] is True
    assert kq["url"] == "https://proxy.example.com"

    # Header an toàn dạng LAN IP và cổng
    h_lan = dict(QT)
    h_lan["X-Forwarded-Proto"] = "http"
    h_lan["X-Forwarded-Host"] = "192.168.1.100:3031"
    r = client.get("/api/dia-chi", headers=h_lan)
    assert r.json()["url"] == "http://192.168.1.100:3031"

    # Header nhiều giá trị KHÁC nhau: bỏ cả header (brief). Lấy giá trị đầu là tin phần do KHÁCH gửi — proxy nối
    # thêm giá trị của nó vào CUỐI chuỗi.
    h_nhieu = dict(QT)
    h_nhieu["X-Forwarded-Proto"] = "https, http"
    h_nhieu["X-Forwarded-Host"] = "main.example.com, backup.example.com"
    r = client.get("/api/dia-chi", headers=h_nhieu)
    assert "main.example.com" not in r.json()["url"] and r.json()["url"].startswith("http://")
    # Nhiều giá trị GIỐNG nhau (hai tầng proxy cùng ghi): không mơ hồ, dùng được
    h_giong = dict(QT)
    h_giong["X-Forwarded-Proto"] = "https, https"
    h_giong["X-Forwarded-Host"] = "main.example.com"
    r = client.get("/api/dia-chi", headers=h_giong)
    assert r.json()["url"] == "https://main.example.com"

    # Header độc/lạ: scheme không hợp lệ (ftp, javascript, CRLF)
    h_doc1 = dict(QT)
    h_doc1["X-Forwarded-Proto"] = "ftp"
    h_doc1["X-Forwarded-Host"] = "safe.example.com"
    r = client.get("/api/dia-chi", headers=h_doc1)
    # proto lạ bị bỏ → dùng scheme mặc định
    assert r.json()["url"].startswith("http://safe.example.com")

    h_doc2 = dict(QT)
    h_doc2["X-Forwarded-Proto"] = "https\r\nEvil: 1"
    h_doc2["X-Forwarded-Host"] = "safe.example.com"
    r = client.get("/api/dia-chi", headers=h_doc2)
    assert r.json()["url"].startswith("http://safe.example.com")

    # Header host độc: chứa đường dẫn, HTML/script, CRLF, quá dài
    h_host_doc1 = dict(QT)
    h_host_doc1["X-Forwarded-Proto"] = "https"
    h_host_doc1["X-Forwarded-Host"] = "safe.example.com/bad/path"
    r = client.get("/api/dia-chi", headers=h_host_doc1)
    # Host độc bị bỏ, fallback về host thường
    assert "safe.example.com/bad/path" not in r.json()["url"]

    h_host_doc2 = dict(QT)
    h_host_doc2["X-Forwarded-Proto"] = "https"
    h_host_doc2["X-Forwarded-Host"] = "<script>alert(1)</script>"
    r = client.get("/api/dia-chi", headers=h_host_doc2)
    assert "<script>" not in r.json()["url"]

    h_host_dai = dict(QT)
    h_host_dai["X-Forwarded-Proto"] = "https"
    h_host_dai["X-Forwarded-Host"] = "a" * 300 + ".example.com"
    r = client.get("/api/dia-chi", headers=h_host_dai)
    assert len(r.json()["url"]) < 300


def test_put_dia_chi_kiem_tra_url_hop_le_va_tu_choi(client):
    # 1. URL hợp lệ
    hop_le = [
        ("https://ai.example.com", "https://ai.example.com"),
        ("https://ai.example.com/", "https://ai.example.com"),
        ("http://192.168.1.10:3031", "http://192.168.1.10:3031"),
        ("https://my-domain.vn:8443/proxy/web2api/", "https://my-domain.vn:8443/proxy/web2api"),
        ("http://[::1]:3031", "http://[::1]:3031"),
    ]
    for gui, mong_doi in hop_le:
        r = client.put("/api/dia-chi", headers=QT, json={"url": gui})
        assert r.status_code == 200, f"gửi {gui} thất bại: {r.text}"
        assert r.json()["url"] == mong_doi

    # 2. URL sai bị từ chối 400 khuôn OpenAI
    sai = [
        "ftp://example.com",
        "ws://example.com",
        "http://user:pass@example.com",
        "https://example.com/api?a=1",
        "https://example.com/api#fragment",
        "https://",
        "",
        "   ",
        "not-a-url",
        "https://" + "a" * 2050 + ".com",
        "https://example.com/khoảng trống",
    ]
    for s in sai:
        r = client.put("/api/dia-chi", headers=QT, json={"url": s})
        assert r.status_code == 400, f"URL sai {s!r} không bị 400: {r.status_code}"
        loi = r.json().get("error", {})
        assert loi.get("type") == "invalid_request_error"
        assert loi.get("message")

    # Sai kiểu dữ liệu thân JSON
    assert client.put("/api/dia-chi", headers=QT, json={"url": 12345}).status_code == 400
    assert client.put("/api/dia-chi", headers=QT, json={}).status_code == 400


def test_canh_bao_http_cong_khai_vs_ip_rieng(client):
    # Các địa chỉ HTTP riêng / loopback / .local -> KHÔNG cảnh báo chạy HTTP
    rieng = [
        "http://192.168.1.1:3031",
        "http://10.0.0.5:3031",
        "http://172.16.0.10:3031",
        "http://172.31.255.255:3031",
        "http://127.0.0.1:3031",
        "http://[::1]:3031",
        "http://may-chu.local:3031",
        "http://localhost:3031",
    ]
    for u in rieng:
        r = client.put("/api/dia-chi", headers=QT, json={"url": u})
        assert r.status_code == 200
        canh_bao = r.json()["canh_bao"]
        assert not any("không mã hoá" in cb.lower() for cb in canh_bao), f"{u} bị cảnh báo HTTP: {canh_bao}"

    # Các địa chỉ HTTP công khai (domain công khai hoặc IP công khai) -> PHẢI có cảnh báo
    cong_khai = [
        "http://ai.example.com",
        "http://api.domain.vn:3031",
        "http://8.8.8.8:3031",
        "http://1.1.1.1",
    ]
    for u in cong_khai:
        r = client.put("/api/dia-chi", headers=QT, json={"url": u})
        assert r.status_code == 200
        canh_bao = r.json()["canh_bao"]
        assert any("không mã hoá" in cb.lower() for cb in canh_bao), f"{u} thiếu cảnh báo HTTP: {canh_bao}"

    # HTTPS không bị cảnh báo không mã hoá
    r = client.put("/api/dia-chi", headers=QT, json={"url": "https://ai.example.com"})
    assert r.status_code == 200
    canh_bao = r.json()["canh_bao"]
    assert not any("không mã hoá" in cb.lower() for cb in canh_bao)


def test_get_ket_noi_huong_dan_khong_chua_khoa_that(client):
    # Đặt địa chỉ công khai
    client.put("/api/dia-chi", headers=QT, json={"url": "https://ai.example.com"})

    r = client.get("/api/ket-noi", headers=QT)
    assert r.status_code == 200
    kq = r.json()

    # Kiểm tra các trường hướng dẫn cơ bản
    assert kq["base_url"] == "https://ai.example.com/v1"
    assert "curl" in kq and isinstance(kq["curl"], str)
    assert "python" in kq and isinstance(kq["python"], str)
    assert "ung_dung" in kq and isinstance(kq["ung_dung"], dict)
    assert "trong_nha" in kq and isinstance(kq["trong_nha"], dict)
    assert "qua_domain" in kq and isinstance(kq["qua_domain"], dict)

    # Đảm bảo KHÔNG chứa khoá quản trị thật trong toàn bộ phản hồi
    text = r.text
    assert KHOA_QT not in text
    assert "<KHOÁ>" in text or "<KHOA_QUAN_TRI>" in text

    # Đảm bảo KHÔNG đặt khoá vào URL hay query string
    assert "?key=" not in text
    assert "?api_key=" not in text
    assert "Authorization: Bearer" in text

    # Kiểm tra hai bản trong nhà và qua domain
    assert "http://<IP-máy-chủ>:3031/v1" in kq["trong_nha"]["base_url"]
    assert "<IP-máy-chủ>" in kq["trong_nha"]["curl"]
    assert "https://<tên-miền>/v1" in kq["qua_domain"]["base_url"]
    assert "<tên-miền>" in kq["qua_domain"]["curl"]


def test_api_dia_chi_va_ket_noi_doi_khoa_quan_tri(client):
    cac_api = [
        ("GET", "/api/dia-chi"),
        ("PUT", "/api/dia-chi"),
        ("GET", "/api/ket-noi"),
    ]
    for phuong_thuc, duong in cac_api:
        # Không có header Authorization
        r = client.request(phuong_thuc, duong, json={"url": "https://test.com"} if phuong_thuc == "PUT" else None)
        assert r.status_code == 401, f"{phuong_thuc} {duong} thiếu khoá không trả về 401: {r.status_code}"
        loi = r.json()["error"]
        assert loi["type"] == "invalid_request_error" and loi["code"] == "invalid_api_key"

        # Khoá sai
        r = client.request(phuong_thuc, duong, headers={"Authorization": "Bearer sai-khoa"})
        assert r.status_code == 401

        # Khoá đúng
        r = client.request(phuong_thuc, duong, headers=QT, json={"url": "https://test.com"} if phuong_thuc == "PUT" else None)
        assert r.status_code == 200


def test_ghi_nguyen_tu_dia_chi(du_lieu):
    # Đảm bảo việc ghi dia_chi.json dùng cơ chế nguyên tử và không để lại tệp tạm
    url = "https://atomic.example.com"
    kho.luu_dia_chi_da_dat(url)

    p = du_lieu / "dia_chi.json"
    assert p.is_file()
    assert json.loads(p.read_text(encoding="utf-8")) == {"url": url}

    # Không còn tệp tạm nào sót lại
    tams = list(du_lieu.glob(".*.tam"))
    assert not tams, f"sót tệp tạm: {tams}"

    # Đọc lại
    assert kho.doc_dia_chi_da_dat() == url

    # Xoá
    assert kho.xoa_dia_chi_da_dat() is True
    assert not p.exists()
    assert kho.doc_dia_chi_da_dat() is None


def test_ham_dung_chung_dia_chi_hien_tai(du_lieu, monkeypatch):
    from starlette.requests import Request

    # Mock scope cho Request
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"host", b"localhost:3031")],
        "scheme": "http",
        "root_path": "",
    }
    req = Request(scope)

    # 1. Mặc định: suy ra từ host
    assert dia_chi_hien_tai(req) == "http://localhost:3031"

    # 2. Biến môi trường
    monkeypatch.setenv("WEB2API_PUBLIC_URL", "https://env.example.com")
    assert dia_chi_hien_tai(req) == "https://env.example.com"

    # 3. Đã đặt trên đĩa
    kho.luu_dia_chi_da_dat("https://custom.example.com")
    assert dia_chi_hien_tai(req) == "https://custom.example.com"



# ───────────── Soát độc lập: các lỗi tìm thấy (viết trước bản sửa) ─────────────

def test_host_doc_khong_quay_lai_qua_duong_du_phong(client):
    """Host bị bộ lọc từ chối thì KHÔNG được dùng lại qua request.url.netloc (Starlette dựng nó từ chính Host đó)."""
    for host in ('a"$(id)', "x`id`", "evil.com/../x", "a b"):
        r = client.get("/api/dia-chi", headers=QT | {"host": host})
        assert r.status_code == 200
        url = r.json()["url"]
        for ky_tu in '"$`() ':
            assert ky_tu not in url.split("://", 1)[1], (host, url)
        assert r.json()["dang_doan"] is True
        kn = client.get("/api/ket-noi", headers=QT | {"host": host}).json()
        assert "$(" not in kn["curl"] and "`" not in kn["curl"]


def test_header_lap_nhieu_dong_khac_nhau_bi_bo(client, monkeypatch):
    monkeypatch.setenv("WEB2API_TIN_PROXY", "1")
    r = client.get("/api/dia-chi", headers=[("authorization", QT["Authorization"]),
                                            ("x-forwarded-host", "a.example.com"),
                                            ("x-forwarded-host", "b.example.com")])
    url = r.json()["url"]
    assert "a.example.com" not in url and "b.example.com" not in url


def test_url_kiem_nghiem_host_cong_ky_tu(client):
    sai = [
        "http://:80",                     # không có tên máy
        "http://example.com:99999",       # cổng ngoài 1–65535
        "http://example.com:abc",         # cổng không phải số
        "https://example.com/$(id)",      # ký tự shell trong đường dẫn — đi thẳng vào lệnh curl soạn sẵn
        'https://exa"mple.com',           # dấu nháy trong tên máy
        "https://example.com/a'b",
        "https://example.com/a\\b",
        "https://ex\x0bample.com",        # ký tự điều khiển
        "https://-bad-.example.com",
    ]
    for u in sai:
        r = client.put("/api/dia-chi", headers=QT, json={"url": u})
        assert r.status_code == 400, f"URL sai {u!r} được nhận: {r.status_code} {r.text}"
    # Tên miền tiếng Việt (IDN) → dạng punycode mà curl/SDK dùng được
    r = client.put("/api/dia-chi", headers=QT, json={"url": "https://tên-miền.vn/ai"})
    assert r.status_code == 200 and r.json()["url"] == "https://xn--tn-min-iva0322d.vn/ai"
    # Tên container Docker có gạch dưới vẫn hợp lệ
    assert client.put("/api/dia-chi", headers=QT, json={"url": "http://web2api_1:8000"}).status_code == 200


def test_canh_bao_phan_loai_bang_ipaddress(client):
    def co_canh_bao(u):
        r = client.put("/api/dia-chi", headers=QT, json={"url": u})
        assert r.status_code == 200, (u, r.text)
        return any("không mã hoá" in c.lower() for c in r.json()["canh_bao"])
    # Không phải địa chỉ Internet công khai → không cảnh báo
    for u in ("http://100.64.1.2:3031",       # CGNAT/Tailscale: không định tuyến được từ Internet (is_global False)
              "http://[fe80::1]:3031", "http://[fd00::5]:3031", "http://169.254.1.1",
              "http://2130706433:3031",       # = 127.0.0.1 theo cách curl/trình duyệt hiểu (inet_aton)
              "http://127.1:3031", "http://may-chu.local.:3031", "http://web.localhost:3031"):
        assert not co_canh_bao(u), u
    # Công khai → cảnh báo
    for u in ("http://[2001:4860:4860::8888]:3031", "http://8.8.8.8", "http://ai.example.com"):
        assert co_canh_bao(u), u


def test_bien_moi_truong_sai_va_tep_hong_co_canh_bao(client, du_lieu, monkeypatch):
    monkeypatch.setenv("WEB2API_PUBLIC_URL", "https://user:matkhau-gia-xyz@x.example.com")
    kq = client.get("/api/dia-chi", headers=QT).json()
    assert kq["nguon"] == "suy_ra"
    assert any("WEB2API_PUBLIC_URL" in c for c in kq["canh_bao"]), kq["canh_bao"]
    assert "matkhau-gia-xyz" not in json.dumps(kq)  # không phản chiếu thông tin xác thực
    monkeypatch.delenv("WEB2API_PUBLIC_URL")
    (du_lieu / "dia_chi.json").write_text("{hỏng")
    kq = client.get("/api/dia-chi", headers=QT).json()
    assert any("dia_chi.json" in c for c in kq["canh_bao"]), kq["canh_bao"]
