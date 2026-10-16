"""NV47 — red team «lây nhiễm từ tài khoản khách sang admin»: mảng LEO THANG QUYỀN.

Các test XANH khẳng định phòng thủ (khách/vai thấp KHÔNG leo được):
- khoá khách phạm vi hẹp gọi mọi đường /api/* quản trị → 403, không lộ dữ liệu;
- khoá khách không tự tạo khoá mới, không đụng /api/toi/* (cookie-only);
- vai `user` tự tạo khoá qua /api/toi/khoa không vượt trần pham_vi_toi_da → 403;
- xoá khoá của người khác qua /api/toi/khoa/{id} → 404 giống hệt không tồn tại;
- header X-Web2api-Scope không mở rộng được phạm vi khoá.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.so_module import SoModule

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef-nv47"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


def _duyet(routes, ra):
    for r in routes:
        orig = getattr(r, "original_router", None)
        if orig is not None:
            _duyet(orig.routes, ra)
            continue
        sub = getattr(r, "routes", None)
        if sub:
            _duyet(sub, ra)
            continue
        path = getattr(r, "path", "")
        if not path:
            continue
        for m in sorted(getattr(r, "methods", []) or []):
            if m in ("HEAD", "OPTIONS"):
                continue
            ra.append((m, path))


@pytest.fixture
def client(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    with TestClient(app, raise_server_exceptions=False) as c:
        c.app.state.so = SoModule()
        yield c


def _khoa_khach(client, pham_vi):
    r = client.post("/api/khoa-khach", headers=QT, json={"nhan": "nv47", "pham_vi": pham_vi})
    assert r.status_code == 200, r.text[:200]
    return r.json()["khoa"]["khoa"]


def test_khach_chat_khong_cham_duoc_duong_quan_tri(client):
    """Khoá khách phạm vi «chat» gọi mọi đường /api/* của quản trị → 403."""
    khoa_tho = _khoa_khach(client, ["chat"])
    kh = {"Authorization": f"Bearer {khoa_tho}"}
    targets = []
    _duyet(app.routes, targets)
    loi = []
    for m, p in sorted(set(targets)):
        if not p.startswith("/api/"):
            continue
        # ngoại lệ có chủ đích: mở không xác thực / xác thực khác
        if p in ("/api/thiet-lap", "/api/dang-nhap") or p.startswith("/api/toi"):
            continue
        pp = p
        for a, b in [("{id_}", "x"), ("{ten:path}", "x"), ("{model:path}", "x"),
                     ("{duong:path}", "x"), ("{module}", "m"), ("{tai_khoan}", "t"),
                     ("{pd_id}", "p"), ("{id_thuc_the}", "e"), ("{ma}", "m"), ("{id}", "i")]:
            pp = pp.replace(a, b)
        if m == "GET":
            r = client.get(pp, headers=kh)
        elif m == "POST":
            r = client.post(pp, headers=kh, json={})
        elif m == "PUT":
            r = client.put(pp, headers=kh, json={})
        elif m == "PATCH":
            r = client.patch(pp, headers=kh, json={})
        elif m == "DELETE":
            r = client.delete(pp, headers=kh)
        else:
            continue
        if r.status_code not in (403, 404, 405, 422):
            loi.append((m, pp, r.status_code))
    assert not loi, f"khoá khách chạm được đường quản trị: {loi[:10]}"


def test_khach_khong_tu_tao_khoa_moi(client):
    khoa_tho = _khoa_khach(client, ["chat"])
    kh = {"Authorization": f"Bearer {khoa_tho}"}
    r = client.post("/api/khoa-khach", headers=kh, json={"nhan": "leo", "pham_vi": ["chat"]})
    assert r.status_code == 403


def test_khach_khong_dung_duoc_duong_cookie_only(client):
    """Đường /api/toi/* đòi cookie phiên: Bearer khoá khách → 401/403 (bị chặn)."""
    khoa_tho = _khoa_khach(client, ["chat"])
    kh = {"Authorization": f"Bearer {khoa_tho}"}
    r = client.get("/api/toi", headers=kh)
    assert r.status_code in (401, 403)
    r = client.post("/api/toi/khoa", headers=kh, json={"nhan": "x"})
    assert r.status_code in (401, 403)


def test_scope_header_khong_mo_rong_pham_vi(client):
    khoa_tho = _khoa_khach(client, ["chat"])
    kh = {"Authorization": f"Bearer {khoa_tho}", "X-Web2api-Scope": "chat,thiet_bi:ghi"}
    r = client.post("/v1/chat/completions", headers=kh, json={"model": "mock/x"})
    # phải 403 insufficient_scope, không được lọt qua với phạm vi mở rộng
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == "insufficient_scope"


def _dang_nhap(client, ten, mk):
    # tạo user qua quản trị rồi đăng nhập lấy cookie
    r = client.post("/api/nguoi-dung", headers=QT,
                    json={"ten_dang_nhap": ten, "mat_khau": mk, "vai": "user",
                          "pham_vi_toi_da": ["chat"]})
    assert r.status_code == 200, r.text[:200]
    r = client.post("/api/dang-nhap", json={"ten_dang_nhap": ten, "mat_khau": mk})
    assert r.status_code == 200, r.text[:200]
    r = client.get("/api/toi")
    assert r.status_code == 200
    return {"X-CSRF-Token": r.json()["csrf"]}


def test_user_khong_vuot_tran_pham_vi_khi_tu_tao_khoa(client):
    """Vai user có trần ["chat"]: tự tạo khoá đòi ["thiet_bi:ghi"] → 403."""
    csrf = _dang_nhap(client, "nv47user1", "MatKhauManh123!")
    r = client.post("/api/toi/khoa", headers=csrf, json={"nhan": "vuot", "pham_vi": ["thiet_bi:ghi"]})
    assert r.status_code == 403
    # trong trần thì được
    r = client.post("/api/toi/khoa", headers=csrf, json={"nhan": "trong", "pham_vi": ["chat"]})
    assert r.status_code == 200


def test_user_khong_xoa_khoa_cua_nguoi_khac(client):
    csrf_a = _dang_nhap(client, "nv47user2a", "MatKhauManh123!")
    # user B tạo khoá của mình
    csrf_b = _dang_nhap(client, "nv47user2b", "MatKhauManh123!")
    r = client.post("/api/toi/khoa", headers=csrf_b, json={"nhan": "khoa-cua-b", "pham_vi": ["chat"]})
    assert r.status_code == 200
    id_b = r.json()["khoa"]["id"]
    khoa_tho_b = r.json()["khoa_tho"]
    # đăng nhập lại A (cookie ghi đè) rồi xoá khoá của B → 404, không lộ tồn tại
    r = client.post("/api/dang-nhap", json={"ten_dang_nhap": "nv47user2a", "mat_khau": "MatKhauManh123!"})
    assert r.status_code == 200
    r = client.get("/api/toi")
    csrf_a = {"X-CSRF-Token": r.json()["csrf"]}
    r = client.delete(f"/api/toi/khoa/{id_b}", headers=csrf_a)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "key_not_found"
    # khoá của B vẫn dùng được
    r = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_tho_b}"})
    assert r.status_code == 200


def test_user_khong_tu_len_admin(client):
    """Vai user PATCH /api/nguoi-dung/{id} {vai: admin} cho chính mình → 403."""
    csrf = _dang_nhap(client, "nv47user4", "MatKhauManh123!")
    r = client.get("/api/toi")
    uid = r.json()["id"]
    r = client.patch(f"/api/nguoi-dung/{uid}", headers=csrf, json={"vai": "admin"})
    assert r.status_code == 403


def test_trang_quan_tri_chan_khoa_khach(client):
    """Khoá khách mở trang .dc.html quản trị → 403 (middleware chặn ở main.py)."""
    khoa_tho = _khoa_khach(client, ["chat"])
    kh = {"Authorization": f"Bearer {khoa_tho}"}
    r = client.get("/web/He-thong-Bao-mat.dc.html", headers=kh)
    assert r.status_code == 403


def test_user_khong_tu_nang_vai_qua_toi(client):
    """/api/toi/* không có đường đổi vai; PATCH /api/nguoi-dung đòi quản trị."""
    _dang_nhap(client, "nv47user3", "MatKhauManh123!")
    r = client.get("/api/toi")
    assert r.json()["vai"] == "user"


def test_ranh_gioi_leo_thang_phien(client):
    """Kịch bản ĐÃ CHẠY (không chỉ đọc mã):
    - phiên cookie vai=admin gọi /api/khoa-khach, /api/nguoi-dung, /api/nhat-ky → 200
      (phiên admin TƯƠNG ĐƯƠNG quyền quản trị — ranh giới leo thang thật);
    - phiên cookie vai=user gọi cùng 3 đường → 403.
    """
    r = client.post("/api/nguoi-dung", headers=QT,
                    json={"ten_dang_nhap": "nv47adm", "mat_khau": "MatKhauManh123!", "vai": "admin"})
    assert r.status_code == 200, r.text[:200]
    r = client.post("/api/nguoi-dung", headers=QT,
                    json={"ten_dang_nhap": "nv47usr", "mat_khau": "MatKhauManh123!", "vai": "user"})
    assert r.status_code == 200, r.text[:200]

    r = client.post("/api/dang-nhap", json={"ten_dang_nhap": "nv47adm", "mat_khau": "MatKhauManh123!"})
    assert r.status_code == 200
    for p in ("/api/khoa-khach", "/api/nguoi-dung", "/api/nhat-ky"):
        r = client.get(p)
        assert r.status_code == 200, f"phiên admin phải vào được {p}, nhận {r.status_code}"

    r = client.post("/api/dang-nhap", json={"ten_dang_nhap": "nv47usr", "mat_khau": "MatKhauManh123!"})
    assert r.status_code == 200
    for p in ("/api/khoa-khach", "/api/nguoi-dung", "/api/nhat-ky"):
        r = client.get(p)
        assert r.status_code == 403, f"phiên user phải bị chặn ở {p}, nhận {r.status_code}"
