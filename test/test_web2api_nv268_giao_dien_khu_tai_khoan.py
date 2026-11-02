"""NV268 — test giao diện khu Tài khoản (web/He-thong-AI.dc.html), cập nhật theo NV282 («Khu Tài khoản web2api» đã chốt).

Chrome thật + lõi thật (MayChuThat). Mọi tài khoản/khoá/mật khẩu đều GIẢ, không chạm mạng ngoài. Test luồng:
  1280px: chip nhà → MỘT ô chọn (nhà · loại · trạng thái) → bảng chi tiết; bí mật LUÔN ẩn («••••• đã lưu»), chỉ ghi đè
  (POST da-luu theo id) — trang không bao giờ xin bí mật thô; OTP đúng RFC 6238 chỉ lấy khi bấm, khoá TOTP không lộ.
  390px: MỘT cột, không tràn ngang (thay cho bảng popup của NV268: NV282 chốt «điện thoại một cột»).
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
    trang.cho("document.querySelector('#k282-nha [data-nha=\"\"] .n') && document.querySelector('#k282-nha [data-nha=\"\"] .n').textContent === '3'", han=15,
              mo_ta="đủ 3 tài khoản")
    # Chờ bảng phân loại về (tên nhà/loại hiển thị đúng) — taiDlDs tải tuần tự.
    trang.cho("document.querySelector('#k268').innerText.includes('Google')", han=15, mo_ta="tên nhà từ bảng phân loại")


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


def _chon(trang, ten, mo_ta=""):
    """Mở ô chọn, bấm đúng dòng có địa chỉ/tên `ten` (dòng đậm), đợi bảng chi tiết đổi sang tài khoản đó."""
    if not trang.js("!!document.querySelector('#ds-tk')"):
        trang.bam("document.querySelector('#o-chon')", "mở ô chọn")
        trang.cho("!!document.querySelector('#ds-tk')", han=5, mo_ta="danh sách mở")
    trang.bam(f"[...document.querySelectorAll('#ds-tk [role=option]')].find(li => li.querySelector('.e').textContent === {ten!r})", mo_ta or f"tài khoản {ten}")
    trang.cho(f"document.querySelector('#k268-ct h2') && document.querySelector('#k268-ct h2').textContent === {ten!r}", han=8, mo_ta=f"bảng chi tiết của {ten}")


# ── 1280px ────────────────────────────────────────────────────────────────

def test_1280_khu_nha_o_chon(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao_tab(trang, w)
        chip = trang.js("document.querySelector('#k282-nha').innerText")
        assert "Tất cả" in chip and "Google" in chip and "ChatGPT" in chip, "thiếu chip nhà từ dữ liệu"
        # MỘT ô chọn; mở ra có đủ 3 tài khoản, mỗi dòng «nhà · loại · trạng thái»
        assert trang.js("document.querySelectorAll('#k268 [role=combobox]').length") == 1
        trang.bam("document.querySelector('#o-chon')", "mở ô chọn")
        trang.cho("!!document.querySelector('#ds-tk')", han=5)
        dong = trang.js("[...document.querySelectorAll('#ds-tk [role=option]')].map(li => [li.querySelector('.e').textContent, li.querySelector('.l').textContent])")
        ten = sorted(d[0] for d in dong)
        assert EMAIL_GG in ten and "phu.nv268@example.com" in ten and len(dong) == 3, dong
        loai = {d[0]: d[1] for d in dong}
        assert loai[EMAIL_GG].startswith("Google · ") and loai["phu.nv268@example.com"].startswith("ChatGPT · Web")
        # Bí mật KHÔNG nằm trong DOM
        toan_trang = trang.js("document.body.innerText + document.documentElement.outerHTML")
        for gia_tri in (MAT_KHAU_GIA, TOKEN_GIA, KHOA_API_GIA, TOTP_KHOA_GIA):
            assert gia_tri not in toan_trang
        trang.chup("nv268-khu-1280")


def test_1280_chon_tai_khoan_mo_bang_chi_tiet(trang, tmp_path, monkeypatch):
    """Chọn tài khoản → bảng chi tiết hiện ngay dưới ô chọn, bí mật vẫn ẩn."""
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao_tab(trang, w)
        _chon(trang, EMAIL_GG)
        assert trang.js("document.querySelector('#k268-ct').getBoundingClientRect().top < window.innerHeight"), "bảng chi tiết phải bắt đầu trong khung nhìn"
        chu = trang.js("document.querySelector('#k268-ct').innerText")
        assert "••••• đã lưu" in chu and "Đổi" in chu
        assert trang.js("[...document.querySelectorAll('#k268-ct button')].filter(b => ['Hiện', 'Chép'].includes(b.textContent.trim())).length") == 0, "không còn nút Hiện/Chép bí mật"
        assert MAT_KHAU_GIA not in chu
        assert trang.js("!!document.querySelector('#k268-dn-tu-dong')")
        assert trang.js("document.querySelector('#k268-dn-tu-dong').textContent.includes('Đăng nhập tự động')")
        trang.chup("nv268-chi-tiet-1280")


def test_1280_khong_bao_gio_xin_bi_mat_tho_va_chi_ghi_de(trang, tmp_path, monkeypatch):
    """Đi qua cả ba tài khoản: không có yêu cầu `bi-mat` kiểu `bi_mat` nào; «Đổi» token = POST da-luu {id, bi_mat, kieu_bi_mat} (ghi đè)."""
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao_tab(trang, w)
        trang.js("window.__yc = []; const g = window.fetch.bind(window); window.fetch = (u, o) => { if (o && o.method && o.method !== 'GET') { let b = null; try { b = JSON.parse(o.body); } catch (e) {} window.__yc.push({u, b}); } return g(u, o); }")
        for ten in (EMAIL_GG, "phu.nv268@example.com"):
            _chon(trang, ten)
        # tài khoản token: «Đổi» → ô trống → lưu → step-up → ghi đè
        trang.bam("document.querySelector('#k282-doi-bi-mat')", "Đổi token")
        trang.cho("!!document.querySelector('#k282-sua-bi_mat')", han=5)
        assert trang.js("document.querySelector('#k282-sua-bi_mat').value") == ""
        trang.go("#k282-sua-bi_mat", "tok-moi-gia-nv268-9999")
        trang.bam("document.querySelector('#k282-sua-luu')", "Lưu")
        xac_nhan_lai_step_up(trang)
        trang.cho("!document.querySelector('#k282-sua')", han=15, mo_ta="form sửa đóng")
        yc = trang.js("window.__yc")
        luu = [y for y in yc if y["u"].endswith("api/tai-khoan/da-luu")][-1]["b"]
        assert luu["bi_mat"] == "tok-moi-gia-nv268-9999" and luu["kieu_bi_mat"] == "token" and set(luu) == {"id", "bi_mat", "kieu_bi_mat"}, luu
        assert not [y for y in yc if y["u"].endswith("api/tai-khoan/bi-mat") and (y["b"] or {}).get("hanh_dong") == "bi_mat"]
        assert "tok-moi-gia-nv268-9999" not in trang.js("document.documentElement.outerHTML + document.body.innerText")


def test_1280_otp_rfc6238(trang, tmp_path, monkeypatch):
    """OTP do máy chủ tính, khớp RFC 6238, chỉ lấy khi bấm; khoá TOTP không lộ ở bất kỳ đâu."""
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao_tab(trang, w)
        _chon(trang, EMAIL_GG)  # tài khoản Google có TOTP
        assert trang.js("!!document.querySelector('#k268-hien-otp')"), "thiếu nút «Lấy mã OTP»"
        assert trang.js("!!document.querySelector('.k282-otp-ma')") is False, "mã không hiện trước khi bấm"
        trang.bam("document.querySelector('#k268-hien-otp')", "bấm «Lấy mã OTP»")
        xac_nhan_lai_step_up(trang)
        trang.cho("!!document.querySelector('.k282-otp-ma') && /^[0-9]{6}$/.test(document.querySelector('.k282-otp-ma').textContent)", han=10, mo_ta="mã OTP 6 số hiện")
        ma = trang.js("document.querySelector('.k282-otp-ma').textContent")
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

def test_390_mot_cot_khong_tran_ngang(trang, tmp_path, monkeypatch):
    """<720px: MỘT cột (NV282), ô chọn mở ra không tràn ngang, nút đăng nhập chạm được (≥ 40px)."""
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao_tab(trang, w, rong=390, cao=844)
        rong = "document.documentElement.scrollWidth <= window.innerWidth + 1"
        assert trang.js(rong)
        _chon(trang, EMAIL_GG)
        info = trang.js("""(() => { const l = document.querySelector('.k282-luoi'); const cs = getComputedStyle(l).gridTemplateColumns.trim().split(' ').length;
          return { cot: cs, nut: document.querySelector('#k268-dn-tu-dong').getBoundingClientRect().height }; })()""")
        assert info["cot"] == 1, f"390px phải một cột: {info}"
        assert info["nut"] >= 40
        assert trang.js(rong), "tab tràn ngang ở 390px"
        trang.bam("document.querySelector('#o-chon')", "mở ô chọn")
        trang.cho("!!document.querySelector('#ds-tk')", han=5)
        assert trang.js(rong), "mở danh sách tràn ngang ở 390px"
        trang.chup("nv268-390")


def test_che_do_toi_khu_dung_du(trang, tmp_path, monkeypatch):
    """Chế độ tối: khu vẫn dựng đủ nhà/ô chọn."""
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao_tab(trang, w)
        trang.cdp.goi("Emulation.setEmulatedMedia",
                      {"features": [{"name": "prefers-color-scheme", "value": "dark"}]},
                      trang.s)
        trang.cho("!!document.querySelector('#o-chon')", han=8, mo_ta="khu dựng ở chế độ tối")
        chu = trang.js("document.querySelector('#k268').innerText")
        assert "Google" in chu and trang.js("document.querySelectorAll('#k282-nha button').length") >= 3
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
