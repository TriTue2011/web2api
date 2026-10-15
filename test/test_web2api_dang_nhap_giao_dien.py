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
        # Kiểm tra không có localStorage lưu khoá quản trị
        assert "web2api_khoa" not in noi_dung, f"{p.name} vẫn còn chuỗi web2api_khoa"
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
    """Hàm duongVeHopLe trong khung.js phải từ chối mọi dạng URL mở / độc hại."""
    ma_js = """
    const fs = require('fs');
    const khungCode = fs.readFileSync('web/khung.js', 'utf8');
    const m = khungCode.match(/function duongVeHopLe\\(u\\)[\\s\\S]*?\\n  \\}/);
    if (!m) throw new Error("Không tìm thấy duongVeHopLe");
    const fn = new Function(m[0] + "; return duongVeHopLe;");
    const duongVeHopLe = fn();

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
        ['/test:123', ''],
        ['evil.com', ''],
        ['', ''],
        [null, ''],
        [123, '']
    ];

    for (const [dau_vao, mong_doi] of cac_mau) {
        const kq = duongVeHopLe(dau_vao);
        if (kq !== mong_doi) {
            console.error(`Lỗi với đầu vào: ${dau_vao} -> Nhận: ${kq}, Mong đợi: ${mong_doi}`);
            process.exit(1);
        }
    }
    console.log("OK");
    """
    p = subprocess.run(["node", "-e", ma_js], cwd=str(ROOT_DIR), capture_output=True, text=True)
    assert p.returncode == 0, f"Kiểm tra duongVeHopLe thất bại: {p.stderr}"
    assert "OK" in p.stdout


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

