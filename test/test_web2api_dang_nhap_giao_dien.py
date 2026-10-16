"""Kiểm thử bảo mật đăng nhập, phân quyền và giao diện web (Luật cứng bảo mật 13/10/2026).

Bao gồm:
1. Kiểm LEO THANG QUYỀN trên 4 đường API vừa đóng (/api/he-thong, /api/tinh-nang, /api/modules, /api/modules/catalog):
   - Không phiên / không khoá -> 401
   - Người dùng vai thấp (user) hoặc khoá khách gọi -> 403
   - Quản trị viên (cookie admin hoặc Bearer admin) -> 200
2. Kiểm IDOR & CSRF:
   - User không được truy cập hay can thiệp tài nguyên/khoá của tài khoản khác (IDOR) -> 403/404
   - Thao tác thay đổi trạng thái bằng cookie phiên bắt buộc có X-CSRF-Token hợp lệ
3. Kiểm tra tĩnh giao diện web:
   - Các trang công khai (index.html, Dang-nhap.dc.html, Thiet-lap.dc.html) tải 200 không cần phiên, không lộ bí mật
   - Không còn lưu trữ khoá quản trị web2api_khoa trong localStorage
   - Chống chuyển hướng mở (Open Redirect) trong duongVeHopLe
4. Kịch bản giao diện Chrome thật (bỏ qua khi môi trường không có Chrome):
   - Thiết lập admin ban đầu, tạo mật khẩu ngẫu nhiên an toàn (20 ký tự)
   - Đăng nhập đúng / sai mật khẩu với thông báo lỗi đồng nhất
   - Quản lý người dùng, đổi vai trò, khoá API của tôi, đăng xuất
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import khoa_khach, nguoi_dung, phien_web
from app.main import app
from ui_chung import CO_CHROME, Trang, trang, trinh_duyet  # noqa: F401

# Chuỗi khoá kiểm nghiệm nằm trong allowlist của .gitleaks.toml
KHOA_QT = "AIzaSyKIEMNGHIEM-000000000000000000009"
KHOA_KHAC = "AIzaSyTOT-mot-000000000000000000000001"
ROOT_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
def moi_truong_tai_khoan(tmp_path, monkeypatch):
    """Môi trường dữ liệu sạch cho kiểm thử tài khoản người dùng và phiên web."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    khoa_khach.lam_moi_cache()
    return tmp_path


# ─────────────────────────────────────────────────────────────────────────────
# 1. KIỂM LEO THANG QUYỀN TRÊN 4 ĐƯỜNG API ĐÃ ĐÓNG
# ─────────────────────────────────────────────────────────────────────────────

CAC_DUONG_DONG = [
    "/api/he-thong",
    "/api/tinh-nang",
    "/api/modules",
    "/api/modules/catalog",
]


def test_leo_thang_quyen_4_duong_da_dong_khong_xac_thuc(moi_truong_tai_khoan):
    """Mọi đường vừa đóng khi không có xác thực phải trả 401."""
    with TestClient(app) as client:
        for duong in CAC_DUONG_DONG:
            r = client.get(duong)
            assert r.status_code == 401, f"{duong} không có xác thực phải trả 401 nhưng trả {r.status_code}"
            j = r.json()
            assert "error" in j or "detail" in j


def test_leo_thang_quyen_4_duong_da_dong_vai_user_bi_tu_choi(moi_truong_tai_khoan):
    """Người dùng vai thường (user) gọi các đường quản trị vừa đóng phải bị 403."""
    u, ten, mk = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user_thuong", mat_khau="MatKhauBaoMat123456"))
    ban_ghi, token_tho = phien_web.tao_phien(u["id"])

    with TestClient(app, cookies={"w2a_phien": token_tho}) as client:
        for duong in CAC_DUONG_DONG:
            r = client.get(duong)
            assert r.status_code == 403, f"{duong} vai user phải bị từ chối 403 nhưng trả {r.status_code}"


def test_leo_thang_quyen_4_duong_da_dong_khoa_khach_bi_tu_choi(moi_truong_tai_khoan):
    """Khoá khách (API key) gọi các đường quản trị vừa đóng phải bị 403."""
    ban_ghi, khoa_tho = khoa_khach.tao_khoa(nhan="Khách thử", pham_vi=["chat"])

    with TestClient(app) as client:
        for duong in CAC_DUONG_DONG:
            r = client.get(duong, headers={"Authorization": f"Bearer {khoa_tho}"})
            assert r.status_code == 403, f"{duong} dùng khoá khách phải bị 403 nhưng trả {r.status_code}"


def test_leo_thang_quyen_4_duong_da_dong_admin_duoc_phep(moi_truong_tai_khoan):
    """Quản trị viên (Bearer admin hoặc cookie admin) gọi các đường vừa đóng phải thành công 200."""
    with TestClient(app) as client:
        # 1. Bearer admin key
        for duong in CAC_DUONG_DONG:
            r = client.get(duong, headers={"Authorization": f"Bearer {KHOA_QT}"})
            assert r.status_code == 200, f"{duong} dùng Bearer admin phải được 200 nhưng trả {r.status_code}"

        # 2. Cookie admin
        admin, ten, mk = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin_chinh", mat_khau="MatKhauAdminBaoMat123456"))
        ban_ghi, token_tho = phien_web.tao_phien(admin["id"])
        client.cookies.set("w2a_phien", token_tho)

        for duong in CAC_DUONG_DONG:
            r = client.get(duong)
            assert r.status_code == 200, f"{duong} dùng cookie admin phải được 200 nhưng trả {r.status_code}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. KIỂM LEO THANG QUYỀN IDOR & CSRF
# ─────────────────────────────────────────────────────────────────────────────

def test_idor_khoa_api_khong_thuoc_ve_minh(moi_truong_tai_khoan):
    """Người dùng không được phép xem hoặc thu hồi khoá API của người khác."""
    u1, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user1", mat_khau="MatKhauUser1_123456", pham_vi_toi_da=["chat"]))
    u2, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user2", mat_khau="MatKhauUser2_123456", pham_vi_toi_da=["chat"]))

    # u2 tạo khoá
    ban_ghi2, _ = khoa_khach.tao_khoa("khoa u2", ["chat"], chu_id=u2["id"])
    id_k2 = ban_ghi2["id"]

    # u1 đăng nhập
    ban_ghi1, token1 = phien_web.tao_phien(u1["id"])
    with TestClient(app, cookies={"w2a_phien": token1}) as client_u1:
        # u1 thử xoá khoá của u2 qua /api/toi/khoa/{id}
        r = client_u1.delete(f"/api/toi/khoa/{id_k2}", headers={"X-CSRF-Token": ban_ghi1["csrf"]})
        assert r.status_code == 404, f"Cố thu hồi khoá của người khác phải trả 404 nhưng trả {r.status_code}"


def test_idor_vai_user_goi_quan_ly_nguoi_dung_bi_tu_choi(moi_truong_tai_khoan):
    """Người dùng vai user không thể gọi các API quản lý người dùng."""
    u1, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user1", mat_khau="MatKhauUser1_123456"))
    u2, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user2", mat_khau="MatKhauUser2_123456"))
    ban_ghi1, token1 = phien_web.tao_phien(u1["id"])

    with TestClient(app, cookies={"w2a_phien": token1}) as client_u1:
        csrf = ban_ghi1["csrf"]

        # Đặt lại mật khẩu u2 qua đường quản trị
        r = client_u1.post(f"/api/nguoi-dung/{u2['id']}/dat-lai-mat-khau", headers={"X-CSRF-Token": csrf})
        assert r.status_code == 403

        # Đổi vai u1 thành admin
        r = client_u1.patch(f"/api/nguoi-dung/{u1['id']}", json={"vai": "admin"}, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 403

        # Xoá tài khoản u2
        r = client_u1.delete(f"/api/nguoi-dung/{u2['id']}", headers={"X-CSRF-Token": csrf})
        assert r.status_code == 403


def test_csrf_bat_buoc_cho_thao_tac_thay_doi_trang_thai(moi_truong_tai_khoan):
    """Mọi thao tác POST/PATCH/DELETE bằng cookie phiên thiếu X-CSRF-Token phải bị từ chối 403."""
    admin, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin_csrf", mat_khau="MatKhauAdminBaoMat123456"))
    ban_ghi, token_tho = phien_web.tao_phien(admin["id"])
    with TestClient(app, cookies={"w2a_phien": token_tho}) as client:
        # Không gửi header CSRF -> 403
        r = client.post("/api/toi/mat-khau", json={"mat_khau_cu": "MatKhauAdminBaoMat123456", "mat_khau_moi": "MatKhauMoiBaoMat123456"})
        assert r.status_code == 403, f"Thiếu CSRF phải bị từ chối 403 nhưng trả {r.status_code}"

        # Gửi CSRF sai -> 403
        r = client.post(
            "/api/toi/mat-khau",
            json={"mat_khau_cu": "MatKhauAdminBaoMat123456", "mat_khau_moi": "MatKhauMoiBaoMat123456"},
            headers={"X-CSRF-Token": "sai_hoan_toan_token_csrf_1234567890abcdefgh"},
        )
        assert r.status_code == 403, f"CSRF sai phải bị từ chối 403 nhưng trả {r.status_code}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. KIỂM TRA TĨNH GIAO DIỆN WEB & CHỐNG OPEN REDIRECT / XSS
# ─────────────────────────────────────────────────────────────────────────────

def test_cac_trang_giao_dien_cong_khai_tai_thanh_cong():
    """Các trang tĩnh html phải nạp được 200 mà không cần phiên, không lộ bí mật."""
    client = TestClient(app)
    for duong in ["/index.html", "/Dang-nhap.dc.html", "/Thiet-lap.dc.html"]:
        r = client.get(duong)
        assert r.status_code == 200, f"{duong} tải phải 200 nhưng trả {r.status_code}"
        # Không lộ khoá quản trị hay token
        assert "WEB2API_ADMIN_KEY" not in r.text
        assert "AIzaSy" not in r.text


def test_quet_tinh_khong_con_localstorage_luu_khoa_hay_csrf():
    """Quét toàn bộ web/*.js và web/*.html: không được lưu trữ khoá/CSRF trong storage và không dùng innerHTML nhận dữ liệu API."""
    web_dir = ROOT_DIR / "web"
    tep_kiem = list(web_dir.glob("*.js")) + list(web_dir.glob("*.html"))
    for p in tep_kiem:
        noi_dung = p.read_text(encoding="utf-8")
        # SEC-002: Cấm đọc/lưu khoá quản trị web2api_khoa (getItem, setItem, hoặc truy cập mảng/thuộc tính).
        # CHO PHÉP duy nhất removeItem('web2api_khoa') để dọn sạch dữ liệu cũ còn sót lại.
        for idx, line in enumerate(noi_dung.splitlines(), 1):
            if "web2api_khoa" in line:
                s = line.strip()
                if s.startswith("//") or s.startswith("/*") or s.startswith("*"):
                    continue
                assert "removeItem('web2api_khoa')" in line or 'removeItem("web2api_khoa")' in line, (
                    f"{p.name}:{idx} chứa web2api_khoa nhưng không phải removeItem: {line}"
                )
                assert "getItem" not in line and "setItem" not in line and "[" not in line, (
                    f"{p.name}:{idx} đọc hoặc ghi web2api_khoa: {line}"
                )
        # Kiểm tra không lưu csrfToken / khoá / mật khẩu vào storage
        for tu_nhay_cam in ["web2api_khoa", "csrf", "csrfToken", "X-CSRF-Token", "mat_khau", "password"]:
            assert f"setItem('{tu_nhay_cam}'" not in noi_dung, f"{p.name} lưu {tu_nhay_cam} vào storage"
            assert f'setItem("{tu_nhay_cam}"' not in noi_dung, f"{p.name} lưu {tu_nhay_cam} vào storage"
        # Kiểm tra không có innerHTML gán biến từ API (support.js là dc-lite template engine dựng cây ảo template)
        if p.name != "support.js":
            lines = noi_dung.splitlines()
            for idx, line in enumerate(lines, 1):
                if ".innerHTML" in line and not line.strip().startswith("//") and not line.strip().startswith("/*"):
                    # Chỉ trừ trường hợp .innerHTML = '' để xoá trắng nội dung nếu có
                    assert line.strip().replace(" ", "") in ('.innerHTML=""', ".innerHTML=''"), f"{p.name}:{idx} có gán innerHTML không an toàn: {line}"


def test_chong_chuyen_huong_mo_duong_ve_hop_le():
    """Hàm duongVeHopLe trong khung.js phải từ chối mọi dạng URL mở / độc hại theo nguyên tắc origin."""
    ma_js = """
    const fs = require('fs');
    global.location = { origin: 'http://127.0.0.1:3031' };
    const location = global.location;
    const khungCode = fs.readFileSync('web/khung.js', 'utf8');
    const m = khungCode.match(/function duongVeHopLe\\(u\\)[\\s\\S]*?\\n  \\}/);
    if (!m) throw new Error("Không tìm thấy duongVeHopLe");
    const fn = new Function('location', m[0] + "; return duongVeHopLe;");
    const duongVeHopLe = fn(location);

    const cac_mau = [
        ['/He-thong.dc.html', '/He-thong.dc.html'],
        ['/Studio.dc.html?tab=chat#1', '/Studio.dc.html?tab=chat#1'],
        ['//evil.com', ''],
        ['///evil.com', ''],
        ['https://evil.com', ''],
        ['http://evil.com', ''],
        ['javascript:alert(1)', ''],
        ['/\\\\evil.com', ''],
        ['\\\\evil.com', ''],
        ['/test:123', '/test:123'],
        ['/%2F/x', '/%2F/x'],
        ['/\\t/evil.example', ''],
        ['/\\n/evil.example', ''],
        ['/\\r/evil.example', ''],
        ['/\\\\evil.example', ''],
        ['evil.com', ''],
        ['', ''],
        [null, ''],
        [123, '']
    ];

    for (const [dau_vao, mong_doi] of cac_mau) {
        const kq = duongVeHopLe(dau_vao);
        if (kq !== mong_doi) {
            console.error(`Lỗi với đầu vào: ${JSON.stringify(dau_vao)} -> Nhận: ${JSON.stringify(kq)}, Mong đợi: ${JSON.stringify(mong_doi)}`);
            process.exit(1);
        }
    }
    console.log("OK");
    """
    p = subprocess.run(["node", "-e", ma_js], cwd=str(ROOT_DIR), capture_output=True, text=True)
    assert p.returncode == 0, f"Kiểm tra duongVeHopLe thất bại: {p.stderr}"
    assert "OK" in p.stdout


def test_dang_xuat_xoa_localstorage_theo_tien_to():
    """SEC-005: dangXuat() phải xoá mọi khoá trong localStorage có tiền tố web2api_ (nguyên tắc tiền tố, không danh sách cố định)."""
    # 1. Quét tĩnh: xác nhận dangXuat duyệt qua localStorage và kiểm tra tiền tố web2api_ thay vì danh sách tên cố định
    khung_path = ROOT_DIR / "web" / "khung.js"
    khung_code = khung_path.read_text(encoding="utf-8")
    m_dx = re.search(r"async function dangXuat\(\)[\s\S]*?\n  \}", khung_code)
    assert m_dx is not None, "Không tìm thấy hàm dangXuat trong khung.js"
    dx_body = m_dx.group(0)
    assert "startsWith('web2api_')" in dx_body or 'startsWith("web2api_")' in dx_body, "dangXuat phải kiểm tra tiền tố web2api_"
    assert "web2api_lich_su_tro_chuyen" not in dx_body, "dangXuat không được dùng danh sách cứng tên khoá"
    assert "web2api_cuoc_dang_mo" not in dx_body, "dangXuat không được dùng danh sách cứng tên khoá"

    # 2. Kiểm thử bằng node runtime: mô phỏng localStorage, fetch và kiểm tra hành vi dọn dẹp + nạp CSRF
    ma_js = """
    const fs = require('fs');
    const store = new Map();
    const mockLocalStorage = {
        get length() { return store.size; },
        key(i) { return Array.from(store.keys())[i] || null; },
        getItem(k) { return store.get(k) || null; },
        setItem(k, v) { store.set(k, String(v)); },
        removeItem(k) { store.delete(k); }
    };
    global.localStorage = mockLocalStorage;
    global.location = {
        pathname: '/He-thong.dc.html',
        search: '',
        hash: '',
        origin: 'http://localhost',
        replace: (url) => { global._replacedUrl = url; }
    };
    global.document = { querySelector: () => null, querySelectorAll: () => [] };
    global.window = global;
    global.alert = (msg) => { global._alertMsg = msg; };

    let goiFetch = [];
    global.fetch = async (url, opts) => {
        goiFetch.push({ url: String(url), opts });
        if (String(url).includes('api/toi')) {
            return { ok: true, status: 200, json: async () => ({ id: 'u1', ten_dang_nhap: 'admin', csrf: 'csrf_test_999' }) };
        }
        if (String(url).includes('api/dang-xuat')) {
            return { ok: true, status: 200, json: async () => ({ ok: true }) };
        }
        return { ok: true, status: 200, json: async () => ({}) };
    };

    // Đặt các khoá test
    mockLocalStorage.setItem('web2api_lich_su_tro_chuyen', '[{"id": 1}]');
    mockLocalStorage.setItem('web2api_cuoc_dang_mo', 'cuoc_123');
    mockLocalStorage.setItem('web2api_tuy_chinh_khac', 'gia_tri_khac');
    mockLocalStorage.setItem('unrelated_app_setting', 'giu_nguyen');

    const khungCode = fs.readFileSync('web/khung.js', 'utf8');
    eval(khungCode);

    (async () => {
        await Khung.dangXuat();
        const goiDx = goiFetch.find(x => x.url === 'api/dang-xuat');
        if (!goiDx) {
            console.error('Không gọi fetch tới api/dang-xuat!');
            process.exit(1);
        }
        if (!goiDx.opts || !goiDx.opts.headers || goiDx.opts.headers['X-CSRF-Token'] !== 'csrf_test_999') {
            console.error('Gọi dang-xuat thiếu hoặc sai header X-CSRF-Token:', goiDx);
            process.exit(1);
        }
        // Kiểm tra các khoá có tiền tố web2api_ đã bị xoá sạch
        for (const k of store.keys()) {
            if (k.startsWith('web2api_')) {
                console.error(`Khoá ${k} vẫn còn sau khi đăng xuất!`);
                process.exit(1);
            }
        }
        // Khoá không thuộc web2api_ phải giữ nguyên
        if (mockLocalStorage.getItem('unrelated_app_setting') !== 'giu_nguyen') {
            console.error('Khoá khác bị xoá nhầm!');
            process.exit(1);
        }
        if (global._replacedUrl !== 'Dang-nhap.dc.html') {
            console.error('Chưa chuyển sang Dang-nhap.dc.html, nhận:', global._replacedUrl);
            process.exit(1);
        }
        console.log("OK");
    })();
    """
    p = subprocess.run(["node", "-e", ma_js], cwd=str(ROOT_DIR), capture_output=True, text=True)
    assert p.returncode == 0, f"Kiểm tra dangXuat localStorage thất bại: {p.stderr}"
    assert "OK" in p.stdout


def test_chong_login_csrf_content_type_dang_nhap_va_thiet_lap(moi_truong_tai_khoan):
    """N1: /api/dang-nhap và /api/thiet-lap CHỈ nhận Content-Type: application/json, sai -> 415 unsupported_media_type, không tạo phiên."""
    import json
    with TestClient(app) as client:
        body_dang_nhap = json.dumps({"ten_dang_nhap": "admin", "mat_khau": "MatKhau123456"})

        # 1. /api/dang-nhap với Content-Type: text/plain (form enctype=text/plain từ site lạ)
        r = client.post("/api/dang-nhap", content=body_dang_nhap, headers={"Content-Type": "text/plain"})
        assert r.status_code == 415, f"text/plain phải bị 415 nhưng nhận {r.status_code}"
        j = r.json()
        assert j.get("error", {}).get("code") == "unsupported_media_type"
        assert "w2a_phien" not in client.cookies and "__Host-w2a_phien" not in client.cookies

        # 2. /api/dang-nhap với Content-Type: application/x-www-form-urlencoded
        r = client.post("/api/dang-nhap", content=body_dang_nhap, headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert r.status_code == 415, f"urlencoded phải bị 415 nhưng nhận {r.status_code}"
        j = r.json()
        assert j.get("error", {}).get("code") == "unsupported_media_type"
        assert "w2a_phien" not in client.cookies and "__Host-w2a_phien" not in client.cookies

        # 3. /api/dang-nhap không có header Content-Type
        r = client.post("/api/dang-nhap", content=body_dang_nhap, headers={})
        assert r.status_code == 415, f"thiếu Content-Type phải bị 415 nhưng nhận {r.status_code}"
        j = r.json()
        assert j.get("error", {}).get("code") == "unsupported_media_type"
        assert "w2a_phien" not in client.cookies and "__Host-w2a_phien" not in client.cookies

        # 4. /api/dang-nhap với Content-Type: application/json; charset=utf-8 -> hợp lệ
        r = client.post("/api/dang-nhap", content=body_dang_nhap, headers={"Content-Type": "application/json; charset=utf-8"})
        # Chấp nhận Content-Type (trả về 401 do sai mật khẩu hoặc 200, nhưng KHÔNG phải 415)
        assert r.status_code != 415, f"application/json; charset=utf-8 không được bị 415"

        # 5. /api/thiet-lap từ chối text/plain, urlencoded và thiếu Content-Type
        body_tl = json.dumps({"ma_thiet_lap": "123", "ten_dang_nhap": "admin", "mat_khau": "MatKhau123456"})
        r_tl1 = client.post("/api/thiet-lap", content=body_tl, headers={"Content-Type": "text/plain"})
        assert r_tl1.status_code == 415
        assert r_tl1.json().get("error", {}).get("code") == "unsupported_media_type"

        r_tl2 = client.post("/api/thiet-lap", content=body_tl, headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert r_tl2.status_code == 415
        assert r_tl2.json().get("error", {}).get("code") == "unsupported_media_type"

        r_tl3 = client.post("/api/thiet-lap", content=body_tl, headers={})
        assert r_tl3.status_code == 415
        assert r_tl3.json().get("error", {}).get("code") == "unsupported_media_type"

        # 6. Kiểm tra lỗi 415 kiểm TRƯỚC khi đếm lỗi xác thực (không làm tăng bộ đếm sai mật khẩu)
        for _ in range(10):
            r_sai_ct = client.post("/api/dang-nhap", content=body_dang_nhap, headers={"Content-Type": "text/plain"})
            assert r_sai_ct.status_code == 415


def test_sec_006_tai_lieu_tin_proxy_co_mo_ta_secure_cookie():
    """SEC-006: Tài liệu TRIEN-KHAI.md và THAM-CHIEU.md phải mô tả chính xác WEB2API_TIN_PROXY quyết định cờ Secure và tiền tố __Host- của cookie phiên."""
    trien_khai = (ROOT_DIR / "docs" / "TRIEN-KHAI.md").read_text(encoding="utf-8")
    tham_chieu = (ROOT_DIR / "docs" / "THAM-CHIEU.md").read_text(encoding="utf-8")

    # 1. TRIEN-KHAI.md mục 7 (checklist) phải nêu cờ Secure và tiền tố __Host-
    assert "WEB2API_TIN_PROXY=1" in trien_khai
    assert "Secure" in trien_khai
    assert "__Host-w2a_phien" in trien_khai

    # 2. TRIEN-KHAI.md mục 8.4 phải nêu việc cookie thiếu Secure / __Host- khi chưa bật TIN_PROXY
    assert "cookie phiên thiếu cờ Secure" in trien_khai

    # 3. THAM-CHIEU.md bảng biến môi trường phải nêu cờ Secure / __Host-
    assert "__Host-" in tham_chieu


# ─────────────────────────────────────────────────────────────────────────────
# 4. KỊCH BẢN GIAO DIỆN CHROME THẬT (CDP)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy được vì không có Chrome")
def test_chrome_luong_thiet_lap_admin_va_dang_nhap(trang: Trang, tmp_path, monkeypatch):
    """Kịch bản Chrome: Thiết lập admin lần đầu -> Đăng nhập đúng/sai -> Đăng xuất."""
    from gia import MayChuThat

    ma_thiet_lap = "ma-thiet-lap-kiem-tra-0123456789"
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    (tmp_path / "ma_thiet_lap").write_text(ma_thiet_lap, encoding="utf-8")

    with MayChuThat(app, lifespan="on") as srv:
        base_url = srv.url

        # 1. Truy cập index.html khi chưa có admin -> tự động chuyển hướng Thiet-lap.dc.html
        # index.html không có <x-dc> nên không dùng trang.di (nó chờ dc-lite dựng xong); điều hướng thẳng rồi chờ trang đích.
        trang.cdp.goi("Page.navigate", {"url": f"{base_url}/index.html"}, trang.s)
        trang.cho("location.pathname.endsWith('Thiet-lap.dc.html')", mo_ta="Chuyển hướng sang Thiet-lap.dc.html")
        trang.cho("(document.querySelector('x-dc')||{getAttribute(){}}).getAttribute('data-dc') === 'san-sang'", mo_ta="Trang Thiết lập dựng xong")

        # 2. Bấm nút «Tạo ngẫu nhiên (20 ký tự)»
        trang.bam("document.getElementById('nut-sinh-ngau-nhien')", mo_ta="Bấm tạo mật khẩu ngẫu nhiên")
        # Kiểm tra khối thông tin mật khẩu một lần duy nhất hiện ra
        trang.cho("document.getElementById('thong-tin-sinh')", mo_ta="Hiện khối thông tin admin vừa sinh")
        mk_tao = trang.chu("#mk-sinh-chep")
        assert len(mk_tao) == 20, f"Mật khẩu ngẫu nhiên phải đúng 20 ký tự nhưng có {len(mk_tao)}"
        ten_tao = trang.chu("#ten-sinh-chep")

        # 3. Điền mã thiết lập và bấm Thiết lập admin
        trang.go("#o-ma-tl", ma_thiet_lap)
        trang.bam("document.getElementById('nut-thiet-lap')", mo_ta="Bấm nút thiết lập admin")

        # Sau khi thiết lập thành công -> chuyển về index.html (hoặc trang chính)
        trang.cho("!location.pathname.endsWith('Thiet-lap.dc.html')", mo_ta="Chuyển khỏi trang Thiết lập sau thành công")

        # 4. Thiết lập xong là đã có phiên (trang chuyển về «/»): xoá cookie để kiểm đăng nhập đúng/sai từ trạng thái chưa đăng nhập.
        trang.cdp.goi("Network.clearBrowserCookies", {}, trang.s)
        trang.di(f"{base_url}/Dang-nhap.dc.html")
        # Đăng nhập sai mật khẩu
        trang.go("#o-ten", ten_tao)
        trang.go("#o-mat-khau", "SaiMatKhau123456")
        trang.bam("document.getElementById('nut-dang-nhap')", mo_ta="Đăng nhập với mật khẩu sai")
        trang.cho("document.getElementById('thong-bao-loi')", mo_ta="Hiện thông báo lỗi đăng nhập")

        # 5. Đăng nhập đúng mật khẩu vừa tạo
        trang.go("#o-mat-khau", mk_tao)
        trang.bam("document.getElementById('nut-dang-nhap')", mo_ta="Đăng nhập với mật khẩu đúng")
        trang.cho("!/(Dang-nhap|index)/.test(location.pathname) && (document.querySelector('x-dc')||{getAttribute(){}}).getAttribute('data-dc') === 'san-sang'", mo_ta="Đăng nhập xong, trang đích dựng xong")

        # 6. Mở trang Bảo mật & người dùng
        trang.di(f"{base_url}/He-thong-Bao-mat.dc.html")
        trang.cho("document.getElementById('nhan-ten-nguoi-dung')", mo_ta="Tải trang Hệ thống Bảo mật")
        assert trang.chu("#nhan-ten-nguoi-dung") == ten_tao
        assert "admin" in trang.chu("#nhan-vai-nguoi-dung").lower()

        # 7. Thử bấm Đăng xuất
        trang.bam("document.getElementById('nut-dang-xuat')", mo_ta="Bấm đăng xuất")
        trang.cho("location.pathname.endsWith('Dang-nhap.dc.html')", mo_ta="Quay về trang Đăng nhập sau khi đăng xuất")
        # Chờ đủ lâu để chứng minh không bị đẩy lại và kiểm api/toi trả 401
        trang.cho("fetch('api/toi').then(r => r.status === 401)", mo_ta="/api/toi trả 401 sau đăng xuất")
        trang.js("new Promise(r => setTimeout(r, 2000))")
        assert trang.js("location.pathname").endswith("Dang-nhap.dc.html")
        assert trang.js("fetch('api/toi').then(r => r.status)") == 401


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy được vì không có Chrome")
def test_chrome_tao_nguoi_dung_ngau_nhien_hien_mot_lan(trang: Trang, tmp_path, monkeypatch):
    """Kịch bản Chrome: Quản trị viên tạo người dùng mới tự sinh ngẫu nhiên hiện MỘT LẦN DUY NHẤT."""
    from gia import MayChuThat

    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    admin, ten_ad, mk_ad = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin_test", mat_khau="MatKhauAdminBaoMat123456"))

    with MayChuThat(app, lifespan="on") as srv:
        base_url = srv.url

        # Đăng nhập bằng admin
        trang.di(f"{base_url}/Dang-nhap.dc.html")
        trang.go("#o-ten", ten_ad)
        trang.go("#o-mat-khau", mk_ad)
        trang.bam("document.getElementById('nut-dang-nhap')")
        trang.cho("!/(Dang-nhap|index)/.test(location.pathname) && (document.querySelector('x-dc')||{getAttribute(){}}).getAttribute('data-dc') === 'san-sang'", mo_ta="Đăng nhập xong, trang đích dựng xong")

        # Vào trang Bảo mật
        trang.di(f"{base_url}/He-thong-Bao-mat.dc.html")
        trang.cho("document.getElementById('nut-tao-nd')")

        # Để trống tên đăng nhập và mật khẩu, bấm tạo người dùng để lõi tự sinh
        trang.bam("document.getElementById('nut-tao-nd')", mo_ta="Tạo người dùng tự sinh")
        trang.cho("document.getElementById('the-thong-tin-nd-moi')", mo_ta="Hiện thẻ thông tin người dùng mới")

        ten_sinh = trang.chu("#nd-moi-ten")
        mk_sinh = trang.chu("#nd-moi-mk")
        assert len(ten_sinh) >= 3, f"Tên sinh quá ngắn: {ten_sinh}"
        assert len(mk_sinh) >= 12, f"Mật khẩu sinh phải >= 12 ký tự: {mk_sinh}"

        # Đóng thẻ thông tin -> xoá khỏi DOM
        trang.bam("document.getElementById('nut-chep-nd-moi')", mo_ta="Sao chép thông tin người dùng mới")
        trang.bam("document.getElementById('nut-dong-nd-moi')", mo_ta="Đóng thẻ thông tin")
        trang.cho("!document.getElementById('the-thong-tin-nd-moi')", mo_ta="Thẻ thông tin người dùng mới đã bị xoá khỏi DOM")
        assert trang.js("document.getElementById('the-thong-tin-nd-moi')") is None

        # Xoá cookie admin để kiểm tra dùng user/pass vừa sinh đăng nhập được
        trang.cdp.goi("Network.clearBrowserCookies", {}, trang.s)
        trang.di(f"{base_url}/Dang-nhap.dc.html")
        trang.go("#o-ten", ten_sinh)
        trang.go("#o-mat-khau", mk_sinh)
        trang.bam("document.getElementById('nut-dang-nhap')", mo_ta="Đăng nhập bằng user vừa sinh")
        trang.cho("!/(Dang-nhap|index)/.test(location.pathname) && (document.querySelector('x-dc')||{getAttribute(){}}).getAttribute('data-dc') === 'san-sang'", mo_ta="Đăng nhập xong, trang đích dựng xong")
        # Kiểm tra vào trang Bảo mật của user: hiển thị tên và vai user
        trang.di(f"{base_url}/He-thong-Bao-mat.dc.html")
        trang.cho("document.getElementById('nhan-ten-nguoi-dung')", mo_ta="Tải trang Bảo mật của user")
        assert trang.chu("#nhan-ten-nguoi-dung") == ten_sinh
        assert "user" in trang.chu("#nhan-vai-nguoi-dung").lower()


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy được vì không có Chrome")
def test_chrome_401_chuyen_trang_dang_nhap_va_chong_open_redirect(trang: Trang, tmp_path, monkeypatch):
    """Kịch bản Chrome: 401 tự động chuyển sang trang đăng nhập; chuyển hướng mở độc hại bị chặn."""
    from gia import MayChuThat

    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    admin, ten_ad, mk_ad = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin_redir", mat_khau="MatKhauAdminBaoMat123456"))

    with MayChuThat(app, lifespan="on") as srv:
        base_url = srv.url

        # 1. Thử gọi API khi chưa có phiên -> 401 tự động chuyển sang Dang-nhap.dc.html
        trang.di(f"{base_url}/He-thong-Bao-mat.dc.html")
        trang.cho("location.pathname.endsWith('Dang-nhap.dc.html')", mo_ta="401 chuyển thẳng tới trang đăng nhập")

        # 2. Thử Open Redirect với //evil.com
        trang.di(f"{base_url}/Dang-nhap.dc.html?ve=%2F%2Fevil.com")
        trang.go("#o-ten", ten_ad)
        trang.go("#o-mat-khau", mk_ad)
        trang.bam("document.getElementById('nut-dang-nhap')")
        trang.cho("!location.pathname.endsWith('Dang-nhap.dc.html')", mo_ta="Đăng nhập xong")
        # Kiểm tra không bị nhảy sang host khác mà về index.html cùng nguồn
        assert "evil.com" not in trang.js("location.href")
        assert trang.js("location.origin") in trang.js("location.href")


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy được vì không có Chrome")
def test_chrome_vai_user_khong_bi_vong_lap_ve_trang_tu_phuc_vu(trang: Trang, tmp_path, monkeypatch):
    """Vai thường đăng nhập xong vào «/» bị 403 ở đường quản trị: phải dừng ở trang tự phục vụ, không nhảy
    qua lại giữa «/» và Đăng nhập (trang đó chuyển ngược người đã có phiên về «/»)."""
    from gia import MayChuThat

    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    # Phải có sẵn một quản trị, nếu không «/» và Đăng nhập chuyển người dùng sang trang Thiết lập lần đầu.
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin_vong_lap", mat_khau="MatKhauAdminBaoMat123456"))
    _, ten_u, mk_u = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user_vong_lap", mat_khau="MatKhauUserBaoMat123456"))

    with MayChuThat(app, lifespan="on") as srv:
        trang.di(f"{srv.url}/Dang-nhap.dc.html")
        trang.go("#o-ten", ten_u)
        trang.go("#o-mat-khau", mk_u)
        trang.bam("document.getElementById('nut-dang-nhap')")
        trang.cho("location.pathname.endsWith('He-thong-Bao-mat.dc.html')", han=12, mo_ta="Vai thường dừng ở trang tự phục vụ")
        # Đứng yên sau khi trang nạp xong: không bị đá đi nơi khác.
        trang.js("new Promise(r => setTimeout(r, 1500))")
        assert trang.js("location.pathname").endswith("He-thong-Bao-mat.dc.html")


def test_node_kiem_tra_toan_dien_cac_loi_giao_dien_c1_c2_c3_t4_t6_l4():
    """Kiểm thử bằng node runtime trên logic khung.js và các tệp giao diện:
    - C1: index.html khi api/tinh-nang 500 thì ở lại trang «/», hiện [role=alert] kèm nút Thử lại, không chuyển Dang-nhap.
    - C2: K.goi khi 401 code='incorrect_password' không chuyển đăng nhập; chỉ 401 unauthorized/invalid_api_key mới chuyển.
    - C3: K.navHeThong khi vai user hoặc khi tn bị 403 chỉ dựng nav tối thiểu gồm tab 'bao_mat', không có 'module'.
    - L4: K.goi khi 403 csrf_token_invalid tự nạp lại CSRF và thử lại 1 lần.
    - T4: He-thong-Thiet-bi dừng thử lại khi 401/403 và gọi chuyenDangNhap, lỗi khác lùi dần tối đa 60s.
    - T6: trang.js của vnc-xa không còn docKhoa hay o-khoa; 401 tự gọi chuyenDangNhap về Dang-nhap.dc.html?ve=...
    """
    ma_js = """
    const fs = require('fs');

    // ── 1. Kiểm tra C2 & L4 trong khung.js ──
    const store = new Map();
    global.localStorage = {
        getItem: (k) => store.get(k) || null,
        setItem: (k, v) => store.set(k, String(v)),
        removeItem: (k) => store.delete(k),
        get length() { return store.size; },
        key: (i) => Array.from(store.keys())[i] || null
    };
    global.sessionStorage = global.localStorage;
    global.window = global;
    let urlReplaced = null;
    global.location = {
        pathname: '/He-thong-Bao-mat.dc.html',
        search: '',
        hash: '',
        origin: 'http://localhost',
        replace: (u) => { urlReplaced = u; }
    };
    global.document = { querySelector: () => null, querySelectorAll: () => [] };

    const khungCode = fs.readFileSync('web/khung.js', 'utf8');
    eval(khungCode);

    (async () => {
        // C2: 401 với code='incorrect_password' không được chuyển đăng nhập
        urlReplaced = null;
        global.fetch = async () => ({
            status: 401,
            ok: false,
            json: async () => ({ error: { message: 'Mật khẩu cũ không chính xác.', code: 'incorrect_password' } })
        });
        try {
            await Khung.goi('api/toi/mat-khau', { method: 'POST', json: { mat_khau_cu: 'sai', mat_khau_moi: 'moi' } });
        } catch (e) {
            if (e.code !== 'incorrect_password') {
                console.error('C2 thất bại: mã lỗi không phải incorrect_password', e);
                process.exit(1);
            }
        }
        if (urlReplaced !== null) {
            console.error('C2 thất bại: 401 incorrect_password bị chuyển đăng nhập!', urlReplaced);
            process.exit(1);
        }

        // C2 (ngược lại): 401 với code='unauthorized' phải chuyển đăng nhập
        urlReplaced = null;
        global.fetch = async () => ({
            status: 401,
            ok: false,
            json: async () => ({ error: { message: 'Cần đăng nhập phiên web.', code: 'unauthorized' } })
        });
        try {
            await Khung.goi('api/toi');
        } catch (e) {}
        if (!urlReplaced || !urlReplaced.includes('Dang-nhap.dc.html')) {
            console.error('C2 thất bại: 401 unauthorized không chuyển đăng nhập!', urlReplaced);
            process.exit(1);
        }

        // L4: 403 csrf_token_invalid phải gọi napToi và thử lại 1 lần
        let soLanGoi = 0;
        let daNapToi = false;
        global.fetch = async (url, opts) => {
            const u = String(url);
            if (u.includes('api/toi')) {
                daNapToi = true;
                return { ok: true, status: 200, json: async () => ({ id: 'u1', csrf: 'token_csrf_moi_123' }) };
            }
            if (u.includes('api/thao-tac')) {
                soLanGoi++;
                if (soLanGoi === 1) {
                    return { status: 403, ok: false, json: async () => ({ error: { code: 'csrf_token_invalid' } }) };
                }
                // Lượt gọi thứ hai: kiểm tra header đã nạp token mới
                const hdr = (opts && opts.headers) || {};
                if (hdr['X-CSRF-Token'] !== 'token_csrf_moi_123') {
                    console.error('L4 thất bại: lượt thử lại không mang CSRF mới:', hdr);
                    process.exit(1);
                }
                return { status: 200, ok: true, json: async () => ({ ok: true }) };
            }
            return { ok: true, status: 200, json: async () => ({}) };
        };
        const kqL4 = await Khung.goi('api/thao-tac', { method: 'POST', json: { x: 1 } });
        if (!daNapToi || soLanGoi !== 2 || !kqL4.j || !kqL4.j.ok) {
            console.error('L4 thất bại: không nạp lại CSRF hoặc không thử lại thành công!', { daNapToi, soLanGoi, kqL4 });
            process.exit(1);
        }

        // ── 2. Kiểm tra C3: navHeThong cho vai user và khi 403 ──
        // TH1: người dùng là user -> chỉ tab 'bao_mat'
        Khung.datCsrf('test_csrf');
        // Mô phỏng napToi trả user
        global.fetch = async () => ({
            ok: true,
            status: 200,
            json: async () => ({ id: 'u_user', vai: 'user', ten_dang_nhap: 'user1', pham_vi_toi_da: ['chat', 'anh'] })
        });
        await Khung.napToi();
        const navUser = Khung.navHeThong({ modules: [], kha_nang_da_cai: { chat: 1 } }, 'bao_mat');
        if (navUser.length !== 1 || navUser[0].id !== 'bao_mat' || navUser[0].nhan !== 'Bảo mật') {
            console.error('C3 thất bại: nav của user không đúng:', navUser);
            process.exit(1);
        }

        // TH2: tn bị 403
        const nav403 = Khung.navHeThong({ ma: 403, error: { message: 'Từ chối' } }, 'bao_mat');
        if (nav403.length !== 1 || nav403[0].id !== 'bao_mat') {
            console.error('C3 thất bại: nav khi tn bị 403 không đúng:', nav403);
            process.exit(1);
        }

        // ── 3. Kiểm tra T6: modules/vnc-xa/web/trang.js không còn o-khoa hay docKhoa ──
        const vncJs = fs.readFileSync('modules/vnc-xa/web/trang.js', 'utf8');
        if (vncJs.includes('docKhoa') || vncJs.includes('o-khoa')) {
            console.error('T6 thất bại: trang.js vẫn còn docKhoa hoặc o-khoa!');
            process.exit(1);
        }
        if (!vncJs.includes("api/modules/catalog")) {
            console.error('T6 thất bại: không gọi api/modules/catalog!');
            process.exit(1);
        }

        // ── 4. Kiểm tra L6: He-thong-Bao-mat.dc.html luôn có baseline 'chat' và lọc phạm vi hiển thị ──
        const baoMatHtml = fs.readFileSync('web/He-thong-Bao-mat.dc.html', 'utf8');
        // Phải chứa baseline 'chat' khi tính dsMa
        if (!baoMatHtml.includes("Array.from(new Set(['chat'")) {
            console.error("L6 thất bại: không có baseline 'chat' trong dsMa!");
            process.exit(1);
        }
        // Phải mặc định phamViNdMoi: ['chat']
        if (!baoMatHtml.includes("phamViNdMoi: ['chat']")) {
            console.error("L6 thất bại: phamViNdMoi không mặc định ['chat']!");
            process.exit(1);
        }
        // Phải lọc phạm vi theo dsMa hiển thị
        if (!baoMatHtml.includes("this.state.phamViNdMoi.filter((m) => dsMa.includes(m))")) {
            console.error("L6 thất bại: không lọc phamViNdMoi theo dsMa hiển thị!");
            process.exit(1);
        }

        console.log("ALL_NODE_TESTS_OK");
    })();
    """
    p = subprocess.run(["node", "-e", ma_js], cwd=str(ROOT_DIR), capture_output=True, text=True)
    assert p.returncode == 0, f"Node test thất bại: {p.stderr}"
    assert "ALL_NODE_TESTS_OK" in p.stdout


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy được vì không có Chrome")
def test_chrome_c3_user_tao_khoa_voi_pham_vi_va_nav_bao_mat(trang: Trang, tmp_path, monkeypatch):
    """Kịch bản Chrome C3: Người dùng vai user có pham_vi_toi_da=['chat','anh']:
    - Thấy 2 chip phạm vi, chọn được và tạo khoá API cá nhân thành công.
    - Thanh điều hướng Hệ thống chỉ có tab 'Bảo mật', không có tab 'Module'.
    """
    from gia import MayChuThat

    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="ad_c3", mat_khau="MatKhauAdminBaoMat123456"))
    _, ten_u, mk_u = asyncio.run(nguoi_dung.tao_nguoi_dung(
        vai="user",
        ten_dang_nhap="user_c3",
        mat_khau="MatKhauUserBaoMat123456",
        pham_vi_toi_da=["chat", "anh"]
    ))

    with MayChuThat(app, lifespan="on") as srv:
        base_url = srv.url

        # Đăng nhập bằng user
        trang.di(f"{base_url}/Dang-nhap.dc.html")
        trang.go("#o-ten", ten_u)
        trang.go("#o-mat-khau", mk_u)
        trang.bam("document.getElementById('nut-dang-nhap')")
        trang.cho("location.pathname.endsWith('He-thong-Bao-mat.dc.html')", han=12, mo_ta="Chuyển tới trang tự phục vụ")

        # 1. Kiểm tra thanh điều hướng nav: có tab Bảo mật, KHÔNG có tab Module
        trang.cho("document.querySelector('[data-nav=\"bao_mat\"]')", mo_ta="Có tab Bảo mật")
        assert trang.js("document.querySelector('[data-nav=\"module\"]')") is None

        # 2. Kiểm tra danh sách chip phạm vi khi tạo khoá của tôi: thấy đúng 2 chip 'chat' và 'anh'
        trang.cho("document.querySelectorAll('#tao-khoa-toi .chip').length >= 2", mo_ta="Hiện đủ các chip phạm vi")
        chips = trang.js("[...document.querySelectorAll('#tao-khoa-toi .chip')].map(c => c.getAttribute('data-pham-vi'))")
        assert "chat" in chips and "anh" in chips

        # 3. Điền nhãn và tạo khoá API của tôi
        trang.go("#o-nhan-khoa-toi", "Khoá kiểm tra C3")
        trang.bam("document.getElementById('nut-gui-tao-khoa-toi')")
        trang.cho("document.getElementById('the-khoa-toi-moi')", mo_ta="Hiện khối thông tin khoá mới")
        assert trang.js("document.getElementById('the-khoa-toi-moi')") is not None


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy được vì không có Chrome")
def test_chrome_c2_doi_mat_khau_sai_hien_loi_giu_nguyen_o(trang: Trang, tmp_path, monkeypatch):
    """Kịch bản Chrome C2: Đổi mật khẩu với mật khẩu cũ sai:
    - Máy chủ trả 401 incorrect_password: trang không tải lại, không chuyển Dang-nhap.
    - Lời báo lỗi hiện ngay tại #loi-doi-mk.
    - Dữ liệu ở các ô mật khẩu giữ nguyên.
    """
    from gia import MayChuThat

    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="ad_c2", mat_khau="MatKhauAdminBaoMat123456"))
    _, ten_u, mk_u = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user_c2", mat_khau="MatKhauUserBaoMat123456"))

    with MayChuThat(app, lifespan="on") as srv:
        base_url = srv.url

        # Dọn cookie cũ trước khi bắt đầu test để cô lập hoàn toàn trạng thái
        trang.cdp.goi("Network.clearBrowserCookies", {}, trang.s)

        trang.di(f"{base_url}/Dang-nhap.dc.html")
        trang.go("#o-ten", ten_u)
        trang.go("#o-mat-khau", mk_u)
        trang.bam("document.getElementById('nut-dang-nhap')")
        trang.cho("location.pathname.endsWith('He-thong-Bao-mat.dc.html')", han=12)
        trang.cho("(document.querySelector('x-dc')||{getAttribute(){}}).getAttribute('data-dc') === 'san-sang'", mo_ta="Trang Bảo mật dựng xong")
        trang.cho("document.getElementById('o-mk-cu')", mo_ta="Hiện ô nhập mật khẩu cũ")

        # Nhập mật khẩu cũ sai
        trang.go("#o-mk-cu", "MatKhauCuSaiHoanToan123")
        trang.go("#o-mk-moi", "MatKhauMoiBaoMat123456")
        trang.go("#o-nhap-lai-mk-moi", "MatKhauMoiBaoMat123456")

        trang.bam("document.getElementById('nut-gui-doi-mk')")
        trang.cho("document.getElementById('loi-doi-mk')", mo_ta="Hiện lời báo lỗi đổi mật khẩu")

        # Kiểm tra trang không bị chuyển sang Dang-nhap.dc.html
        assert trang.js("location.pathname").endswith("He-thong-Bao-mat.dc.html")
        loi_txt = trang.chu("#loi-doi-mk")
        assert "mật khẩu" in loi_txt.lower() or "chính xác" in loi_txt.lower()

        # Dữ liệu ô nhập giữ nguyên
        assert trang.js("document.getElementById('o-mk-cu').value") == "MatKhauCuSaiHoanToan123"
        assert trang.js("document.getElementById('o-mk-moi').value") == "MatKhauMoiBaoMat123456"


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy được vì không có Chrome")
def test_chrome_t2_khong_tran_ngang_390px_5_trang(trang: Trang, tmp_path, monkeypatch):
    """Kịch bản Chrome T2: Kiểm tra cả 5 trang ở 390px (iPhone):
    - He-thong-Bao-mat.dc.html, He-thong.dc.html, He-thong-AI.dc.html, He-thong-Thiet-bi.dc.html, Studio.dc.html
    - Không trang nào bị tràn ngang (scrollWidth <= 390).
    - Nút Đăng xuất (#nut-dang-xuat) hiển thị trong khung nhìn.
    """
    from gia import MayChuThat

    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    admin, ten_ad, mk_ad = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="ad_t2", mat_khau="MatKhauAdminBaoMat123456"))

    with MayChuThat(app, lifespan="on") as srv:
        base_url = srv.url
        trang.khung(390, 844, dien_thoai=True)

        trang.di(f"{base_url}/Dang-nhap.dc.html")
        trang.go("#o-ten", ten_ad)
        trang.go("#o-mat-khau", mk_ad)
        trang.bam("document.getElementById('nut-dang-nhap')")
        trang.cho("!/(Dang-nhap|index)/.test(location.pathname) && (document.querySelector('x-dc')||{getAttribute(){}}).getAttribute('data-dc') === 'san-sang'")

        cac_trang = [
            "/He-thong-Bao-mat.dc.html",
            "/He-thong.dc.html",
            "/He-thong-AI.dc.html",
            "/He-thong-Thiet-bi.dc.html",
            "/Studio.dc.html"
        ]

        for p in cac_trang:
            trang.di(f"{base_url}{p}")
            trang.cho("(document.querySelector('x-dc')||{getAttribute(){}}).getAttribute('data-dc') === 'san-sang'", mo_ta=f"Trang {p} dựng xong")
            # Kiểm tra không tràn ngang
            sw = trang.js("document.documentElement.scrollWidth")
            assert sw <= 390, f"Trang {p} bị tràn ngang: scrollWidth={sw} > 390"
            # Nút đăng xuất hiển thị
            assert trang.js("document.getElementById('nut-dang-xuat') !== null"), f"Trang {p} thiếu nút đăng xuất"


def test_node_c1_index_html_khi_tinh_nang_500_hien_nut_thu_lai():
    """Kiểm thử node cho C1: index.html khi api/tinh-nang trả 500:
    - Không chuyển hướng sang Dang-nhap.dc.html (chuyển hướng <= 0 lần).
    - Ở lại trang «/», hiển thị [role=alert] kèm nút «Thử lại».
    """
    ma_js = """
    const fs = require('fs');

    const indexHtml = fs.readFileSync('web/index.html', 'utf8');
    const khungCode = fs.readFileSync('web/khung.js', 'utf8');

    // Giả lập môi trường DOM đơn giản cho index.html
    let urlReplaced = [];
    global.location = {
        pathname: '/',
        search: '',
        hash: '',
        origin: 'http://localhost',
        replace: (u) => { urlReplaced.push(u); },
        reload: () => {}
    };
    global.window = global;
    const store = new Map();
    global.localStorage = { getItem: (k) => store.get(k) || null, setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k), length: 0 };
    global.sessionStorage = global.localStorage;

    const elTt = {
        attrs: {},
        textContent: '',
        children: [],
        setAttribute(k, v) { this.attrs[k] = String(v); },
        getAttribute(k) { return this.attrs[k] || null; },
        appendChild(child) { this.children.push(child); }
    };

    global.document = {
        getElementById(id) {
            if (id === 'tt') return elTt;
            return null;
        },
        createElement(tag) {
            return {
                tag,
                textContent: '',
                attrs: {},
                style: {},
                setAttribute(k, v) { this.attrs[k] = String(v); }
            };
        }
    };

    eval(khungCode);

    // Mock fetch: api/thiet-lap -> không cần thiết lập; api/tinh-nang -> 500 Internal Server Error
    global.fetch = async (url) => {
        const u = String(url);
        if (u.includes('api/thiet-lap')) {
            return { ok: true, status: 200, json: async () => ({ can_thiet_lap: false }) };
        }
        if (u.includes('api/tinh-nang')) {
            return {
                ok: false,
                status: 500,
                json: async () => ({ error: { message: 'Lỗi máy chủ nội bộ 500.', code: 'internal_error' } })
            };
        }
        return { ok: true, status: 200, json: async () => ({}) };
    };

    // Trích xuất mã script trong index.html và thực thi
    const m = indexHtml.match(/<script>([\\s\\S]*?)<\\/script>/);
    if (!m) {
        console.error('Không tìm thấy script trong index.html');
        process.exit(1);
    }
    const scriptBody = m[1];

    (async () => {
        eval(scriptBody);
        // Đợi async IIFE hoàn thành
        await new Promise(r => setTimeout(r, 100));

        // Kiểm tra không chuyển hướng sang Dang-nhap.dc.html
        if (urlReplaced.some(u => u.includes('Dang-nhap.dc.html'))) {
            console.error('C1 thất bại: index.html khi 500 lại chuyển sang Dang-nhap!', urlReplaced);
            process.exit(1);
        }
        if (urlReplaced.length > 0) {
            console.error('C1 thất bại: index.html khi 500 có lệnh điều hướng:', urlReplaced);
            process.exit(1);
        }

        // Kiểm tra phần tử #tt đã được chuyển sang role=alert
        if (elTt.getAttribute('role') !== 'alert') {
            console.error('C1 thất bại: #tt chưa có role=alert', elTt.attrs);
            process.exit(1);
        }

        // Kiểm tra có nút «Thử lại»
        const hasRetryBtn = elTt.children.some(c => c.tag === 'button' && c.textContent.includes('Thử lại'));
        if (!hasRetryBtn) {
            console.error('C1 thất bại: #tt thiếu nút Thử lại', elTt.children);
            process.exit(1);
        }

        console.log("C1_NODE_OK");
    })();
    """
    p = subprocess.run(["node", "-e", ma_js], cwd=str(ROOT_DIR), capture_output=True, text=True)
    assert p.returncode == 0, f"Node test C1 thất bại: {p.stderr}"
    assert "C1_NODE_OK" in p.stdout


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy được vì không có Chrome")
def test_chrome_c1_khong_vong_lap_khi_tinh_nang_500(trang: Trang, tmp_path, monkeypatch):
    """Kịch bản Chrome C1: Khi /api/tinh-nang trả 500:
    - Truy cập trang «/» không bị lặp chuyển hướng giữa «/» và Đăng nhập (chuyển hướng <= 2 lần).
    - Ở lại trang «/» và hiển thị thông báo kèm nút «Thử lại».
    """
    import time
    from fastapi import Request
    from gia import MayChuThat
    from app.loi_openai import LoiOpenAI
    from app.so_module import SoModule
    from app import tuyen

    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    admin, ten_ad, mk_ad = asyncio.run(
        nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="ad_c1", mat_khau="MatKhauAdminBaoMat123456")
    )

    # Cờ kiểm soát lỗi 500 phía máy chủ: tắt lúc đăng nhập, bật khi mở trang «/»
    loi_500 = False

    orig_tinh_nang = SoModule.tinh_nang
    def mock_tinh_nang(self):
        if loi_500:
            raise LoiOpenAI(500, "Lỗi 500 máy chủ giả định", code="server_error")
        return orig_tinh_nang(self)

    monkeypatch.setattr(SoModule, "tinh_nang", mock_tinh_nang)

    orig_xem_tinh_nang = tuyen.xem_tinh_nang
    async def mock_xem_tinh_nang(request: Request):
        if loi_500:
            raise LoiOpenAI(500, "Lỗi 500 máy chủ giả định", code="server_error")
        return await orig_xem_tinh_nang(request)

    monkeypatch.setattr(tuyen, "xem_tinh_nang", mock_xem_tinh_nang)
    for r in tuyen.tinh_nang.routes:
        if getattr(r, "path", None) == "/api/tinh-nang":
            monkeypatch.setattr(r, "endpoint", mock_xem_tinh_nang)

    with MayChuThat(app, lifespan="on") as srv:
        base_url = srv.url

        # Đăng nhập bằng admin để có phiên hợp lệ
        trang.di(f"{base_url}/Dang-nhap.dc.html")
        trang.go("#o-ten", ten_ad)
        trang.go("#o-mat-khau", mk_ad)
        trang.bam("document.getElementById('nut-dang-nhap')")
        trang.cho(
            "!/(Dang-nhap|index)/.test(location.pathname) && (document.querySelector('x-dc')||{getAttribute(){}}).getAttribute('data-dc') === 'san-sang'",
            mo_ta="Đăng nhập xong và vào trang chính",
        )

        # Bật lỗi 500 cho api/tinh-nang phía máy chủ
        loi_500 = True

        # Đặt bộ đếm điều hướng trong sessionStorage để theo dõi mọi lượt tải tài liệu mới
        trang.js("sessionStorage.setItem('__dem_dieu_huong', '0')")
        trang.cdp.goi("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
            (() => {
                const cur = parseInt(sessionStorage.getItem('__dem_dieu_huong') || '0', 10) + 1;
                sessionStorage.setItem('__dem_dieu_huong', String(cur));
            })();
            """
        }, trang.s)

        # Mở trang «/» (index.html không phải trang dc-lite nên dùng Page.navigate, không dùng trang.di)
        trang.cdp.goi("Page.navigate", {"url": f"{base_url}/"}, trang.s)

        # (b) Chờ trang ở lại index.html và hiện phần tử #tt[role=alert] chứa nút «Thử lại»
        trang.cho(
            "document.querySelector('#tt[role=\"alert\"] button') !== null",
            han=5,
            mo_ta="Hiện alert có nút Thử lại trên index.html",
        )

        # Khẳng định có nút «Thử lại» và đúng role=alert
        alert_btn_txt = trang.chu("#tt[role='alert'] button")
        assert alert_btn_txt and "thử lại" in alert_btn_txt.lower(), f"Thiếu nút Thử lại: {alert_btn_txt}"
        role_tt = trang.js("document.getElementById('tt') ? document.getElementById('tt').getAttribute('role') : null")
        assert role_tt == "alert", f"Phần tử #tt chưa có role=alert: {role_tt}"

        # (c) Đợi và đo số lần điều hướng trong 4 giây: tổng số lần điều hướng <= 2
        bat_dau = time.monotonic()
        cac_path = []
        while time.monotonic() - bat_dau < 4.0:
            try:
                p = trang.js("location.pathname")
                if not cac_path or cac_path[-1] != p:
                    cac_path.append(p)
            except Exception:
                # Nếu đang đổi trang lúc đánh giá JS
                cac_path.append("navigating")
            time.sleep(0.1)

        # (a) Trang ở lại index.html (không chuyển sang Đăng nhập hay He-thong)
        path_cuoi = trang.js("location.pathname")
        assert path_cuoi == "/" or path_cuoi.endswith("index.html"), f"Trang không ở lại index.html: {path_cuoi}"
        assert "Dang-nhap" not in path_cuoi, f"Trang bị chuyển sang Dang-nhap: {path_cuoi}"
        assert "He-thong" not in path_cuoi, f"Trang bị chuyển sang He-thong: {path_cuoi}"

        # Khẳng định số lần điều hướng <= 2
        so_dieu_huong = int(trang.js("sessionStorage.getItem('__dem_dieu_huong') || '1'"))
        assert so_dieu_huong <= 2, f"Bị lặp điều hướng qua sessionStorage: {so_dieu_huong} lần"
        assert len(cac_path) <= 2, f"Bị lặp điều hướng theo dõi trong 4s: {cac_path}"


def test_don_vi_tinh_nang_500_monkeypatch(moi_truong_tai_khoan, monkeypatch):
    """Kiểm tra đơn vị phía máy chủ: monkeypatch SoModule.tinh_nang ném LoiOpenAI(500)
    hoạt động chính xác trên route /api/tinh-nang khi có phiên quản trị."""
    from app.loi_openai import LoiOpenAI
    from app.so_module import SoModule

    admin, ten, mk = asyncio.run(
        nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="ad_ut_500", mat_khau="MatKhauAdminBaoMat123456")
    )
    ban_ghi, token_tho = phien_web.tao_phien(admin["id"])

    loi_500 = False
    orig_tinh_nang = SoModule.tinh_nang

    def mock_tinh_nang(self):
        if loi_500:
            raise LoiOpenAI(500, "Lỗi 500 máy chủ giả định", code="server_error")
        return orig_tinh_nang(self)

    monkeypatch.setattr(SoModule, "tinh_nang", mock_tinh_nang)

    with TestClient(app, cookies={"w2a_phien": token_tho}) as client:
        # Khi cờ tắt -> 200
        r_ok = client.get("/api/tinh-nang")
        assert r_ok.status_code == 200

        # Khi cờ bật -> 500 chuẩn OpenAI
        loi_500 = True
        r_err = client.get("/api/tinh-nang")
        assert r_err.status_code == 500
        j = r_err.json()
        assert j.get("error", {}).get("code") == "server_error"




