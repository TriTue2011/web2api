"""NV268 — test giao diện khu Tài khoản theo mẫu (web/He-thong-AI.dc.html).

Chrome thật + lõi thật (MayChuThat). Mọi tài khoản/khoá/mật khẩu đều GIẢ,
không chạm mạng ngoài. Test luồng:
  1280px: nhóm → loại → ô; bấm ô thấy bảng chi tiết ngay (không cuộn);
  «Hiện» mới có giá trị bí mật (qua POST /api/tai-khoan/bi-mat + step-up);
  tự che lại sau 30s (đồng hồ giả window.__khu_che_ms);
  OTP đúng RFC 6238, khoá TOTP không lộ ở bất kỳ phản hồi nào.
  390px: bảng chi tiết là popup giữa màn hình, nền mờ, Esc/bấm nền đóng;
  nút đăng nhập thấy được không cần cuộn; không tràn ngang.
  Chế độ tối: khu dựng đủ.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from gia import MayChuThat
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture

from app.main import app

KHOA_QT = "KhoaQuanTriGiaNv268-0001"
MK_ADMIN = "MatKhauAdminBaoMat123456"

# ── Dữ liệu GIẢ cho test (không phải bí mật thật) ────────────────────────────
EMAIL_GG = "an.nv268@example.com"
MAT_KHAU_GIA = "MkGiaNv268#Chinh-001"
TOTP_KHOA_GIA = "JBSWY3DPEHPK3PXP"          # base32 giả, chỉ dùng trong test
TOKEN_GIA = "tok-gia-nv268-0001"
KHOA_API_GIA = "sk-gia-nv268-0002"


def _totp(ma_khoa_b32: str, moc: float | None = None) -> str:
    """TOTP RFC 6238 (SHA1, chu kỳ 30s, 6 số) — đối chiếu mã OTP máy chủ trả."""
    khoa = base64.b32decode(ma_khoa_b32.upper())
    dem = int((time.time() if moc is None else moc) // 30)
    mac = hmac.new(khoa, struct.pack(">Q", dem), hashlib.sha1).digest()
    o = mac[-1] & 0x0F
    ma = struct.unpack(">I", mac[o:o + 4])[0] & 0x7FFFFFFF
    return f"{ma % 10 ** 6:06d}"


def _ma_totp_cho_phep() -> set[str]:
    """Chấp nhận lệch ±1 chu kỳ (ranh giới 30s giữa máy chủ và test)."""
    bay_gio = time.time()
    return {_totp(TOTP_KHOA_GIA, bay_gio + d) for d in (-30, 0, 30)}


@contextmanager
def loi_that(tmp_path, monkeypatch):
    """Lõi thật + 2 module AI giả trên đĩa (nhóm google/chatgpt trong manifest)
    + 3 tài khoản giả."""
    from gia import ghi_thu_muc
    from ui_mau import module_ai
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    anh = tmp_path / "anh-nv268"
    anh.mkdir(exist_ok=True)
    monkeypatch.setenv("WEB2API_THU_MUC_ANH", str(anh))
    # THU_MUC_ANH đọc lúc import ui_chung — patch trực tiếp.
    import ui_chung
    monkeypatch.setattr(ui_chung, "THU_MUC_ANH", str(anh))
    # Module giả: lõi nạp lúc khởi động; nhóm suy từ manifest (NV197c).
    for mid, nhom_id, nhom_ten in (
            ("mod-google-gia", "google", "Google"),
            ("mod-chatgpt-gia", "chatgpt", "ChatGPT")):
        tep = module_ai(id_=mid, ten=f"AI giả {nhom_ten}", tien_to=mid)
        man = json.loads(tep["manifest.json"])
        man["nhom"] = {"id": nhom_id, "ten": nhom_ten}
        tep["manifest.json"] = json.dumps(man, ensure_ascii=False).encode()
        ghi_thu_muc(tmp_path / "modules" / mid, tep)
    with MayChuThat(app, lifespan="on") as w:
        qt = {"Authorization": "Bearer " + KHOA_QT}
        import httpx
        with httpx.Client(base_url=w.url, timeout=15) as c:
            r = c.post("/api/tai-khoan/da-luu", headers=qt, json={
                "email": EMAIL_GG, "mat_khau": MAT_KHAU_GIA,
                "totp_secret": TOTP_KHOA_GIA, "nhan": "Chính",
                "module_id": "mod-google-gia",
                "loai_tai_khoan": "gmail", "dich_dung": "gemini",
                "bac_goi": "plus"})
            assert r.status_code == 200, r.text
            r = c.post("/api/tai-khoan/da-luu", headers=qt, json={
                "email": "phu.nv268@example.com",
                "bi_mat": TOKEN_GIA, "kieu_bi_mat": "token",
                "nhan": "Phụ", "module_id": "mod-chatgpt-gia",
                "loai_tai_khoan": "web", "dich_dung": "web"})
            assert r.status_code == 200, r.text
            r = c.post("/api/tai-khoan/da-luu", headers=qt, json={
                "bi_mat": KHOA_API_GIA, "kieu_bi_mat": "khoa_api",
                "nhan": "Khoá API", "module_id": "mod-chatgpt-gia",
                "loai_tai_khoan": "api", "dich_dung": "api",
                "dia_chi": "https://api.nha-cung-cap-gia.example/v1"})
            assert r.status_code == 200, r.text
        yield w
        # Giữ bản sao ảnh ở thư mục bền vững để đính kèm báo cáo.
        anh_ben = os.environ.get("NV268_THU_MUC_ANH_BEN")
        if anh_ben:
            import shutil
            os.makedirs(anh_ben, exist_ok=True)
            for f in sorted(anh.glob("*.png")):
                shutil.copy2(f, os.path.join(anh_ben, f.name))


def _vao_tab(trang, w, rong=1280, cao=800):
    trang.khung(rong, cao)
    trang.di(f"{w.url}/He-thong-AI.dc.html#tai-khoan")
    trang.cho("!!document.querySelector('#k268')", han=15, mo_ta="khu Tài khoản dựng")
    trang.cho("document.querySelectorAll('#k268 .k268-tk').length >= 3", han=15,
              mo_ta="đủ ô tài khoản")
    # Chờ bảng phân loại về (tên nhóm/loại hiển thị đúng) — taiDlDs tải tuần tự.
    trang.cho("document.querySelector('#k268').innerText.includes('Google')", han=15,
              mo_ta="tên nhóm từ bảng phân loại")


def xac_nhan_lai_step_up(trang):
    """Xử lý dialog step-up của khung (403 step_up_required)."""
    trang.cho("!!document.querySelector('dialog input[type=\"password\"]')", han=10,
              mo_ta="dialog xác nhận lại (step-up)")
    trang.go("dialog input[type=\"password\"]", MK_ADMIN)
    nut = ("(() => { const ds = [...document.querySelectorAll('dialog button')];"
           " return !!ds.find((b) => b.textContent.includes('Xác nhận')); })()")
    trang.cho(nut, han=5, mo_ta="nút Xác nhận step-up")
    trang.bam(("(() => { const ds = [...document.querySelectorAll('dialog button')];"
               " return ds.find((b) => b.textContent.includes('Xác nhận')); })()"),
              "nút Xác nhận step-up")


def _bam_o_dau(trang, mo_ta="bấm ô tài khoản đầu tiên"):
    trang.bam("document.querySelector('#k268 .k268-tk')", mo_ta)
    trang.cho("!!document.querySelector('#k268-ct.mo')", han=8,
              mo_ta="bảng chi tiết mở")


# ── 1280px ──────────────────────────────────────────────────────────────────

def test_1280_khu_nhom_loai_o(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao_tab(trang, w)
        chu = trang.js("document.querySelector('#k268').innerText")
        assert "Google" in chu and "ChatGPT" in chu, "thiếu nhóm từ dữ liệu"
        assert "Gmail" in chu, "thiếu loại Gmail của nhóm Google"
        assert "Web" in chu and "API" in chu, "thiếu loại Web/API của nhóm ChatGPT"
        assert "Chính" in chu and "Phụ" in chu and "Khoá API" in chu, "thiếu ô tài khoản"
        # Chip lọc «Tất cả» + từng nhóm nằm ở tablist trên khu
        chip = trang.js("document.querySelector('[aria-label=\"Nhóm tài khoản\"]').innerText")
        assert "Tất cả" in chip, "thiếu chip lọc «Tất cả»"
        assert "Google" in chip and "ChatGPT" in chip, "thiếu chip nhóm từ dữ liệu"
        assert "Chưa có tài khoản nào trong nhóm này." in chu, "thiếu câu nhóm trống"
        # Bí mật KHÔNG nằm trong DOM trước khi bấm «Hiện»
        toan_trang = trang.js("document.body.innerText")
        assert MAT_KHAU_GIA not in toan_trang
        assert TOKEN_GIA not in toan_trang
        assert KHOA_API_GIA not in toan_trang
        assert TOTP_KHOA_GIA not in toan_trang
        trang.chup("nv268-khu-1280")


def test_1280_bam_o_mo_bang_chi_tiet(trang, tmp_path, monkeypatch):
    """Bấm ô → bảng chi tiết hiện NGAY trong khung nhìn (không cần cuộn)."""
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao_tab(trang, w)
        _bam_o_dau(trang)
        trong = trang.js("""(() => {
          const r = document.querySelector('#k268-ct').getBoundingClientRect();
          return r.top >= 0 && r.bottom <= window.innerHeight && r.width > 200;
        })()""")
        assert trong, "bảng chi tiết không nằm gọn trong khung nhìn 1280px"
        chu = trang.js("document.querySelector('#k268-ct').innerText")
        assert "Địa chỉ" in chu and "Chép" in chu
        assert "Mật khẩu" in chu and "Hiện" in chu
        # Bí mật vẫn che trước khi bấm «Hiện»
        assert "••••" in chu
        assert MAT_KHAU_GIA not in chu
        # Hai nút đăng nhập dùng hàm sẵn có (tay chỉ hiện khi có đường trình duyệt)
        assert trang.js("!!document.querySelector('#k268-dn-tu-dong')")
        nut_tay = trang.js("!!document.querySelector('#k268-dn-tay')")
        assert trang.js("document.querySelector('#k268-dn-tu-dong').textContent.includes('Đăng nhập tự động')")
        trang.chup("nv268-chi-tiet-1280")


def test_1280_hien_bi_mat_qua_step_up(trang, tmp_path, monkeypatch):
    """«Hiện» → step-up → giá trị bí mật hiện; chỉ lấy qua POST bi-mat."""
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao_tab(trang, w)
        _bam_o_dau(trang)
        trang.bam("document.querySelector('#k268-hien')", "bấm «Hiện» mật khẩu")
        xac_nhan_lai_step_up(trang)
        trang.cho(
            f"document.querySelector('[data-k268-gt=\"bi-mat\"]').textContent.includes({MAT_KHAU_GIA!r})",
            han=10, mo_ta="bí mật hiện sau step-up")
        # Che lại bằng nút «Che»
        trang.bam("document.querySelector('#k268-hien')", "bấm «Che»")
        trang.cho("document.querySelector('[data-k268-gt=\"bi-mat\"]').textContent.includes('••••')",
                  han=5, mo_ta="bí mật che lại")


def test_1280_tu_che_sau_30s(trang, tmp_path, monkeypatch):
    """Sau khi hiện, không dùng → tự che lại (đồng hồ giả rút còn 300ms)."""
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao_tab(trang, w)
        trang.js("window.__khu_che_ms = 300;")
        _bam_o_dau(trang)
        trang.bam("document.querySelector('#k268-hien')", "bấm «Hiện» mật khẩu")
        xac_nhan_lai_step_up(trang)
        trang.cho(
            f"document.querySelector('[data-k268-gt=\"bi-mat\"]').textContent.includes({MAT_KHAU_GIA!r})",
            han=10, mo_ta="bí mật hiện sau step-up")
        trang.cho("document.querySelector('[data-k268-gt=\"bi-mat\"]').textContent.includes('••••')",
                  han=8, mo_ta="tự che lại sau hẹn giờ")


def test_1280_otp_rfc6238(trang, tmp_path, monkeypatch):
    """OTP do máy chủ tính, khớp RFC 6238; khoá TOTP không lộ ở bất kỳ đâu."""
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao_tab(trang, w)
        _bam_o_dau(trang)  # ô đầu = tài khoản Google có TOTP
        assert trang.js("!!document.querySelector('#k268-hien-otp')"), "thiếu nút Hiện OTP"
        trang.bam("document.querySelector('#k268-hien-otp')", "bấm «Hiện» OTP")
        xac_nhan_lai_step_up(trang)
        trang.cho("!!document.querySelector('[data-k268-gt=\"otp\"] span').textContent.match(/^[0-9]{6}$/)",
                  han=10, mo_ta="mã OTP 6 số hiện")
        ma = trang.js("document.querySelector('[data-k268-gt=\"otp\"] span').textContent")
        assert ma in _ma_totp_cho_phep(), f"OTP {ma} không khớp RFC 6238"
        # Khoá TOTP không lộ: DOM + phản hồi API trực tiếp
        assert TOTP_KHOA_GIA not in trang.js("document.body.innerText")
        assert TOTP_KHOA_GIA not in trang.js("document.documentElement.outerHTML")
        import httpx
        tk_id = trang.js("document.querySelector('#k268-ct').getAttribute('data-k268-ct')")
        with httpx.Client(base_url=w.url, timeout=15) as c:
            r = c.post("/api/tai-khoan/bi-mat",
                       headers={"Authorization": "Bearer " + KHOA_QT},
                       json={"id": tk_id, "hanh_dong": "otp"})
            assert r.status_code == 200, r.text
            assert TOTP_KHOA_GIA not in r.text, "khoá TOTP lộ trong phản hồi API"
            assert r.json()["ma"] in _ma_totp_cho_phep()
        trang.chup("nv268-otp-1280")


def test_1280_khong_con_danh_sach_xo_provider(trang, tmp_path, monkeypatch):
    """Hành vi đổi có chủ ý (NV98): bỏ khối xổ ra theo nhà cung cấp ở khu."""
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao_tab(trang, w)
        assert trang.js("document.querySelectorAll('[data-khoi-pv]').length") == 0, \
            "khu vẫn còn khối xổ provider cũ"


# ── 390px + chế độ tối ───────────────────────────────────────────────────────

def test_390_popup_giua_man_hinh(trang, tmp_path, monkeypatch):
    """<720px: popup giữa màn hình, nền mờ; nút đăng nhập thấy không cần cuộn;
    Esc đóng; bấm nền đóng; tab không tràn ngang."""
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao_tab(trang, w, rong=390, cao=844)
        _bam_o_dau(trang)
        giua = trang.js("""(() => {
          const ct = document.querySelector('#k268-ct').getBoundingClientRect();
          const nen = document.querySelector('#k268-nen');
          const cs = getComputedStyle(nen);
          const lech = Math.abs(ct.left + ct.width / 2 - window.innerWidth / 2);
          const nut = document.querySelector('#k268-dn-tu-dong').getBoundingClientRect();
          const tay = document.querySelector('#k268-dn-tay');
          const nut2 = tay ? tay.getBoundingClientRect() : null;
          return { lech, mo: cs.display !== 'none',
                   nutTrong: nut.top >= 0 && nut.bottom <= window.innerHeight,
                   nut2Trong: !nut2 || (nut2.top >= 0 && nut2.bottom <= window.innerHeight),
                   rong: document.documentElement.scrollWidth <= window.innerWidth + 1 };
        })()""")
        assert giua["lech"] < 60, f"popup lệch giữa {giua['lech']}px"
        assert giua["mo"], "nền mờ không hiện"
        assert giua["nutTrong"] and giua["nut2Trong"], "nút đăng nhập phải thấy không cuộn"
        assert giua["rong"], "tab tràn ngang ở 390px"
        trang.chup("nv268-popup-390")
        # Esc đóng
        trang.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
        trang.cho("!document.querySelector('#k268-ct.mo')", han=5, mo_ta="Esc đóng popup")
        # Bấm nền đóng
        _bam_o_dau(trang)
        trang.bam("document.querySelector('#k268-nen')", "bấm nền mờ")
        trang.cho("!document.querySelector('#k268-ct.mo')", han=5, mo_ta="bấm nền đóng popup")


def test_che_do_toi_khu_dung_du(trang, tmp_path, monkeypatch):
    """Chế độ tối: khu vẫn dựng đủ nhóm/ô."""
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao_tab(trang, w)
        trang.cdp.goi("Emulation.setEmulatedMedia",
                      {"features": [{"name": "prefers-color-scheme", "value": "dark"}]},
                      trang.s)
        trang.cho("document.querySelectorAll('#k268 .k268-tk').length >= 3", han=8,
                  mo_ta="khu dựng ở chế độ tối")
        chu = trang.js("document.querySelector('#k268').innerText")
        assert "Google" in chu and "Chính" in chu
        trang.chup("nv268-khu-toi-1280")
        trang.cdp.goi("Emulation.setEmulatedMedia",
                      {"features": [{"name": "prefers-color-scheme", "value": "light"}]},
                      trang.s)


# ── Cú pháp JS ────────────────────────────────────────────────────────────────

def test_trang_js_hop_le_node_check():
    """Mọi khối <script> của trang qua node --check."""
    from pathlib import Path
    s = (Path(__file__).parent.parent / "web" / "He-thong-AI.dc.html").read_text(encoding="utf-8")
    khoi = [k for k in re.findall(r"<script[^>]*>(.*?)</script>", s, flags=re.S) if k.strip()]
    assert khoi, "không tìm thấy khối script nào trong trang"
    for i, js in enumerate(khoi):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(js)
            p = f.name
        try:
            r = subprocess.run(["node", "--check", p], capture_output=True,
                               text=True, timeout=30)
            assert r.returncode == 0, f"script {i}: {r.stderr}"
        finally:
            os.unlink(p)
