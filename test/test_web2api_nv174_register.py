"""NV174 — module `register`: điều phối đăng ký tài khoản CÓ NGƯỜI TRONG VÒNG LẶP.

Hàng rào cứng của spec: module KHÔNG giải/vượt thử thách chống bot, KHÔNG tự
điền biểu mẫu, KHÔNG tạo tài khoản hàng loạt. Gặp thử thách/xác minh điện thoại
→ DỪNG ở trạng thái «can_nguoi» và không đi tiếp; người thật xử lý qua trình
duyệt từ xa (noVNC) rồi ghi nhận mới tiếp tục được.

Phủ:
- manifest hạng B + mọi trường bí mật có mo_ta LẤY Ở ĐÂU/TỰ ĐẶT;
- cai(): mặc định TẮT; SSRF cả hai đường URL (trang đăng ký = bộ kiểm không
  tin của lõi, hộp thư = bộ kiểm LAN của lõi); khoá hộp thư chỉ hiện dạng che;
- phân loại trang GIẢ theo cấu trúc: form sẵn sàng / thử thách / điện thoại /
  mã một lần / trang lạ (trang lạ = dừng, fail-closed);
- lượt: một lượt đang chạy, trần lượt/ngày, đợi mã từ hộp thư GIẢ;
- bí mật (mật khẩu sinh, mã) hiện MỘT lần ở kết quả bắt đầu; không xuất hiện
  trong xem trạng thái / nhật ký / suc_khoe; huỷ → xoá khỏi kho bí mật;
- API lõi: 401 (thiếu khoá), 403 (thiếu phạm vi), 202 hàng chờ cho khách gọi
  hành động nguy_hiem (module KHÔNG chạy), quản trị xác nhận mới chạy.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import re
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parent.parent
if str(GOC) not in sys.path:
    sys.path.insert(0, str(GOC))

from app.kham_pha import doc_manifest  # noqa: E402

THU_MUC_MODULE = GOC / "modules" / "register"
KHOA_QT = {"Authorization": "Bearer khoa-quan-tri-gia-nv174"}
MA_THU = "483920"
KHOA_HOP_THU = "khoa-hop-thu-gia-123456"

TRANG_FORM = """<!doctype html><html><head><title>Đăng ký</title></head><body>
<form method="post" action="/dang-ky">
<label>Email <input type="email" name="email" autocomplete="email"></label>
<label>Mật khẩu <input type="password" name="mat_khau" autocomplete="new-password"></label>
<button type="submit">Tạo tài khoản</button>
</form></body></html>"""

TRANG_THU_THACH = """<!doctype html><html><head><title>Chờ một chút</title></head><body>
<iframe src="https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/f/ov2"></iframe>
<div class="cf-turnstile" data-sitekey="0x4AAAAAAA"></div>
</body></html>"""

TRANG_DIEN_THOAI = """<!doctype html><html><head><title>Xác minh</title></head><body>
<form method="post">
<label>Số điện thoại <input type="tel" name="sdt" autocomplete="tel"></label>
<button type="submit">Gửi mã</button>
</form></body></html>"""

TRANG_MA = """<!doctype html><html><head><title>Nhập mã</title></head><body>
<form method="post">
<label>Mã <input type="text" name="ma" autocomplete="one-time-code" inputmode="numeric"></label>
<button type="submit">Xác minh</button>
</form></body></html>"""

TRANG_LA = "<!doctype html><html><body><h1>Trang chủ</h1><p>Xin chào.</p></body></html>"


# ───────────────────────── Máy chủ GIẢ (trang + hộp thư Inbucket) ─────────────────────────


class _XuLyGia(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # tắt log của máy chủ giả
        pass

    def _gui(self, ma_http: int, than: bytes, loai: str = "text/html; charset=utf-8"):
        self.send_response(ma_http)
        self.send_header("Content-Type", loai)
        self.send_header("Content-Length", str(len(than)))
        self.end_headers()
        self.wfile.write(than)

    def do_GET(self):
        sv = self.server
        sv.dem[self.path] = sv.dem.get(self.path, 0) + 1
        if self.path == "/dang-ky-form":
            return self._gui(200, TRANG_FORM.encode())
        if self.path == "/dang-ky-thach":
            return self._gui(200, TRANG_THU_THACH.encode())
        if self.path == "/dang-ky-dien-thoai":
            return self._gui(200, TRANG_DIEN_THOAI.encode())
        if self.path == "/dang-ky-ma":
            return self._gui(200, TRANG_MA.encode())
        if self.path == "/dang-ky-la":
            return self._gui(200, TRANG_LA.encode())
        m = re.fullmatch(r"/api/v1/mailbox/([a-z0-9-]+)", self.path)
        if m:
            ten = m.group(1)
            if ten == "web2api-kiem-tra" or not sv.co_ma:
                return self._gui(200, b"[]", "application/json")
            ds = [{
                "id": "m1",
                "from": "noreply@nha-cung-cap-gia.test",
                "subject": f"Mã xác minh {MA_THU}",
                "date": "Mon, 20 Oct 2026 04:00:00 +0000",
            }]
            return self._gui(200, json.dumps(ds).encode(), "application/json")
        m = re.fullmatch(r"/api/v1/mailbox/([a-z0-9-]+)/([a-z0-9]+)", self.path)
        if m:
            ten = m.group(1)
            nhan_den = "nguoi-khac@mien.test" if getattr(sv, "to_sai", False) else f"{ten}@mien.test"
            ma = "111222" if getattr(sv, "to_sai", False) else MA_THU
            chi_tiet = {
                "id": "m1",
                "from": "noreply@nha-cung-cap-gia.test",
                "subject": f"Mã xác minh {ma}",
                "date": "Mon, 20 Oct 2026 04:00:00 +0000",
                "header": {"To": [nhan_den]},
                "body": {"text": f"Mã xác minh của bạn là {ma}. Mã hết hạn sau 10 phút."},
            }
            return self._gui(200, json.dumps(chi_tiet).encode(), "application/json")
        return self._gui(404, b"khong thay", "text/plain")


@pytest.fixture()
def may_gia():
    sv = ThreadingHTTPServer(("127.0.0.1", 0), _XuLyGia)
    sv.dem = {}
    sv.co_ma = True
    luong = threading.Thread(target=sv.serve_forever, daemon=True)
    luong.start()
    yield f"http://127.0.0.1:{sv.server_address[1]}", sv
    sv.shutdown()
    sv.server_close()


@pytest.fixture()
def moi_truong(tmp_path, monkeypatch, may_gia):
    """Module đã nạp cấu hình; trả (module, gốc_url_giả, server_giả)."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    goc_url, sv = may_gia

    spec = importlib.util.spec_from_file_location(
        "nv174_module", THU_MUC_MODULE / "module.py"
    )
    tep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tep)
    manifest, loi = doc_manifest(THU_MUC_MODULE)
    assert loi is None and manifest is not None

    def tao(cau_hinh: dict | None = None):
        m = tep.Module(manifest)
        m.nap(cau_hinh or {})
        return m

    return tep, tao, goc_url, sv


def _cau_hinh_day_du(goc_url: str, **them) -> dict:
    ch = {
        "bat": "true",
        "url_dang_ky": f"{goc_url}/dang-ky-form",
        "hop_thu_api": goc_url,
        "hop_thu_mien": "mien.test",
        "hop_thu_khoa": KHOA_HOP_THU,
        "gioi_han_moi_ngay": "1",
    }
    ch.update(them)
    return ch


def _chay(coro):
    return asyncio.run(coro)


async def _cai_va_nap(m, cau_hinh: dict) -> dict:
    kq = await m.cai(cau_hinh)
    assert kq["ok"] is True, kq
    m.nap(kq["cau_hinh_luu"])
    return kq


# ───────────────────────── Phân loại trang (hàm thuần) ─────────────────────────


def test_phan_loai_form_dang_ky(moi_truong):
    tep, *_ = moi_truong
    assert tep.phan_loai_trang(TRANG_FORM)["loai"] == "form_dang_ky"


def test_phan_loai_thu_thach(moi_truong):
    tep, *_ = moi_truong
    assert tep.phan_loai_trang(TRANG_THU_THACH)["loai"] == "thu_thach"


def test_phan_loai_xac_minh_dien_thoai(moi_truong):
    tep, *_ = moi_truong
    assert tep.phan_loai_trang(TRANG_DIEN_THOAI)["loai"] == "xac_minh_dien_thoai"


def test_phan_loai_trang_doi_ma(moi_truong):
    tep, *_ = moi_truong
    assert tep.phan_loai_trang(TRANG_MA)["loai"] == "doi_ma"


def test_phan_loai_trang_la_fail_closed(moi_truong):
    tep, *_ = moi_truong
    assert tep.phan_loai_trang(TRANG_LA)["loai"] == "khong_ro"


# ───────────────────────── Mã sinh + trích mã (port từ c2a) ─────────────────────────


def test_sinh_mat_khau_du_manh_va_khac_nhau(moi_truong):
    tep, *_ = moi_truong
    a, b = tep.sinh_mat_khau(), tep.sinh_mat_khau()
    assert a != b and len(a) >= 16
    assert any(c.isupper() for c in a) and any(c.islower() for c in a)
    assert any(c.isdigit() for c in a) and any(not c.isalnum() for c in a)


def test_trich_ma_tu_thu(moi_truong):
    tep, *_ = moi_truong
    thu = {
        "subject": "Mã xác minh",
        "text_content": f"Mã xác minh của bạn là {MA_THU}. Hết hạn sau 10 phút.",
        "html_content": "",
    }
    assert tep.trich_ma_xac_minh(thu) == MA_THU


def test_trich_ma_bo_qua_ma_mau_va_so_dai(moi_truong):
    tep, *_ = moi_truong
    thu_mau = {
        "subject": "Hướng dẫn",
        "text_content": "Ví dụ mã mẫu 177010 và số điện thoại 0901234567.",
        "html_content": "",
    }
    assert tep.trich_ma_xac_minh(thu_mau) is None


def test_thu_khop_email(moi_truong):
    tep, *_ = moi_truong
    dung = {"recipients": ["a@mien.test"], "raw": {}}
    sai = {"recipients": ["b@mien.test"], "raw": {}}
    assert tep.thu_khop_email(dung, "a@mien.test") is True
    assert tep.thu_khop_email(sai, "a@mien.test") is False


# ───────────────────────── Manifest + cai() ─────────────────────────


def test_manifest_hang_b_va_bi_mat_co_mo_ta():
    manifest, loi = doc_manifest(THU_MUC_MODULE)
    assert loi is None and manifest is not None
    assert manifest["hang"] == "B"
    assert "thiet_bi" in manifest["cung_cap"]
    for truong in manifest["truong_cau_hinh"]:
        if truong["kieu"] == "bi_mat":
            mo = (truong.get("mo_ta") or "").lower()
            assert "tự đặt" in mo or "lấy" in mo, truong["khoa"]


def test_cai_mac_dinh_tat_va_bat_dau_bi_tu_choi(moi_truong):
    _, tao, _, sv = moi_truong
    m = tao()
    kq = _chay(m.cai({}))
    assert kq["ok"] is True
    m.nap(kq["cau_hinh_luu"])
    r = _chay(m.thuc_hien("dang_ky.luot", "bat_dau", {}))
    assert r["ok"] is False and r["ma_loi"] == "module_dang_tat"
    assert sv.dem == {}, "module tắt thì không được gọi mạng nào"


def test_cai_bat_thieu_url_bao_loi(moi_truong):
    _, tao, goc_url, _ = moi_truong
    m = tao()
    kq = _chay(m.cai({"bat": "true", "hop_thu_api": goc_url, "hop_thu_mien": "mien.test"}))
    assert kq["ok"] is False


def test_cai_ssrf_url_dang_ky_bi_chan(moi_truong):
    _, tao, goc_url, _ = moi_truong
    m = tao()
    ch = _cau_hinh_day_du(goc_url, url_dang_ky="http://169.254.169.254/latest/meta-data/")
    kq = _chay(m.cai(ch))
    assert kq["ok"] is False


def test_cai_ssrf_hop_thu_bi_chan(moi_truong):
    _, tao, goc_url, _ = moi_truong
    m = tao()
    ch = _cau_hinh_day_du(goc_url, hop_thu_api="http://169.254.169.254:9000")
    kq = _chay(m.cai(ch))
    assert kq["ok"] is False


def test_cai_day_du_ok_va_khoa_chi_hien_che(moi_truong):
    _, tao, goc_url, _ = moi_truong
    m = tao()
    kq = _chay(m.cai(_cau_hinh_day_du(goc_url)))
    assert kq["ok"] is True, kq
    che = kq["ket_qua_tung_khoa"]["hop_thu_khoa"]
    assert KHOA_HOP_THU not in json.dumps(che, ensure_ascii=False)
    assert "…" in json.dumps(che, ensure_ascii=False)


# ───────────────────────── Luồng lượt ─────────────────────────


def test_bat_dau_form_sang_san_va_bi_mat_hien_mot_lan(moi_truong):
    _, tao, goc_url, sv = moi_truong
    m = tao()
    _chay(_cai_va_nap(m, _cau_hinh_day_du(goc_url)))
    r = _chay(m.thuc_hien("dang_ky.luot", "bat_dau", {}))
    assert r["ok"] is True, r
    assert r["trang_thai_moi"]["buoc"] == "cho_nguoi_dien"
    thong_tin = r["ket_qua"]["thong_tin_tai_khoan"]
    assert thong_tin["email"].endswith("@mien.test")
    assert len(thong_tin["mat_khau"]) >= 16
    assert sv.dem.get("/dang-ky-form") == 1
    # Xem lại: KHÔNG còn mật khẩu, email chỉ dạng che.
    xem = _chay(m.thuc_hien("dang_ky.trang_thai", "xem", {}))
    chuoi = json.dumps(xem, ensure_ascii=False)
    assert thong_tin["mat_khau"] not in chuoi
    assert thong_tin["email"] not in chuoi
    assert "…" in chuoi


def test_bat_dau_gap_thu_thach_dung_can_nguoi_khong_di_tiep(moi_truong):
    _, tao, goc_url, _ = moi_truong
    m = tao()
    _chay(_cai_va_nap(m, _cau_hinh_day_du(goc_url, url_dang_ky=f"{goc_url}/dang-ky-thach")))
    r = _chay(m.thuc_hien("dang_ky.luot", "bat_dau", {}))
    assert r["ok"] is True, r
    moi = r["trang_thai_moi"]
    assert moi["buoc"] == "can_nguoi" and moi["ly_do"] == "thu_thach_chong_bot"
    assert "novnc" in moi["huong_dan"].lower()
    # Đang dừng cần người: không hành động nào đi tiếp được trừ ghi nhận của người.
    r2 = _chay(m.thuc_hien("dang_ky.luot", "doi_ma", {"cho_giay": 1}))
    assert r2["ok"] is False and r2["ma_loi"] == "dang_can_nguoi"
    r3 = _chay(m.thuc_hien("dang_ky.luot", "ghi_nhan_nguoi_xu_ly", {}))
    assert r3["ok"] is True and r3["trang_thai_moi"]["buoc"] == "cho_nguoi_dien"


def test_bat_dau_gap_xac_minh_dien_thoai_dung_can_nguoi(moi_truong):
    _, tao, goc_url, _ = moi_truong
    m = tao()
    _chay(_cai_va_nap(m, _cau_hinh_day_du(goc_url, url_dang_ky=f"{goc_url}/dang-ky-dien-thoai")))
    r = _chay(m.thuc_hien("dang_ky.luot", "bat_dau", {}))
    assert r["trang_thai_moi"]["buoc"] == "can_nguoi"
    assert r["trang_thai_moi"]["ly_do"] == "xac_minh_dien_thoai"


def test_bat_dau_trang_la_dung_can_nguoi(moi_truong):
    _, tao, goc_url, _ = moi_truong
    m = tao()
    _chay(_cai_va_nap(m, _cau_hinh_day_du(goc_url, url_dang_ky=f"{goc_url}/dang-ky-la")))
    r = _chay(m.thuc_hien("dang_ky.luot", "bat_dau", {}))
    assert r["trang_thai_moi"]["buoc"] == "can_nguoi"
    assert r["trang_thai_moi"]["ly_do"] == "khong_nhan_dien_duoc_trang"


def test_hai_luot_cung_luc_bi_chan(moi_truong):
    _, tao, goc_url, _ = moi_truong
    m = tao()
    _chay(_cai_va_nap(m, _cau_hinh_day_du(goc_url)))
    assert _chay(m.thuc_hien("dang_ky.luot", "bat_dau", {}))["ok"] is True
    r = _chay(m.thuc_hien("dang_ky.luot", "bat_dau", {}))
    assert r["ok"] is False and r["ma_loi"] == "da_co_luot_dang_chay"


def test_doi_ma_lay_ma_tu_hop_thu(moi_truong):
    _, tao, goc_url, _ = moi_truong
    m = tao()
    _chay(_cai_va_nap(m, _cau_hinh_day_du(goc_url)))
    _chay(m.thuc_hien("dang_ky.luot", "bat_dau", {}))
    r = _chay(m.thuc_hien("dang_ky.luot", "doi_ma", {"cho_giay": 5}))
    assert r["ok"] is True, r
    assert r["ket_qua"]["ma_xac_minh"] == MA_THU
    # Mã chỉ để NGƯỜI nhập vào trình duyệt từ xa — module không tự gửi đi đâu.
    assert "trình duyệt" in r["ket_qua"]["huong_dan"].lower()


def test_doi_ma_chua_co_thu_bao_loi_nhe(moi_truong):
    _, tao, goc_url, sv = moi_truong
    sv.co_ma = False
    m = tao()
    _chay(_cai_va_nap(m, _cau_hinh_day_du(goc_url)))
    _chay(m.thuc_hien("dang_ky.luot", "bat_dau", {}))
    r = _chay(m.thuc_hien("dang_ky.luot", "doi_ma", {"cho_giay": 1}))
    assert r["ok"] is False and r["ma_loi"] == "chua_thay_ma"
    xem = _chay(m.thuc_hien("dang_ky.trang_thai", "xem", {}))
    assert xem["ket_qua"]["buoc"] == "cho_nguoi_dien"


def test_doi_ma_bo_qua_thu_cua_nguoi_khac(moi_truong):
    """Thư trong hộp thư nhưng gửi cho địa chỉ khác → KHÔNG lấy mã của nó."""
    _, tao, goc_url, sv = moi_truong
    sv.to_sai = True
    m = tao()
    _chay(_cai_va_nap(m, _cau_hinh_day_du(goc_url)))
    _chay(m.thuc_hien("dang_ky.luot", "bat_dau", {}))
    r = _chay(m.thuc_hien("dang_ky.luot", "doi_ma", {"cho_giay": 1}))
    assert r["ok"] is False and r["ma_loi"] == "chua_thay_ma"


def test_tran_luot_moi_ngay(moi_truong):
    _, tao, goc_url, _ = moi_truong
    m = tao()
    _chay(_cai_va_nap(m, _cau_hinh_day_du(goc_url, gioi_han_moi_ngay="1")))
    _chay(m.thuc_hien("dang_ky.luot", "bat_dau", {}))
    r = _chay(m.thuc_hien("dang_ky.luot", "xac_nhan_hoan_tat", {}))
    assert r["ok"] is True and r["trang_thai_moi"]["buoc"] == "hoan_tat"
    r2 = _chay(m.thuc_hien("dang_ky.luot", "bat_dau", {}))
    assert r2["ok"] is False and r2["ma_loi"] == "vuot_han_moi_ngay"


def test_huy_xoa_bi_mat_khoi_kho(moi_truong):
    from app import kho

    _, tao, goc_url, _ = moi_truong
    m = tao()
    _chay(_cai_va_nap(m, _cau_hinh_day_du(goc_url)))
    r = _chay(m.thuc_hien("dang_ky.luot", "bat_dau", {}))
    luot = r["ket_qua"]["luot_id"]
    assert kho.doc_bi_mat(f"register-{luot}").get("mat_khau")
    r2 = _chay(m.thuc_hien("dang_ky.luot", "huy", {}))
    assert r2["ok"] is True and r2["trang_thai_moi"]["buoc"] == "da_huy"
    assert kho.doc_bi_mat(f"register-{luot}") == {}


def test_ghi_nhan_nguoi_sai_trang_thai_bi_tu_choi(moi_truong):
    _, tao, goc_url, _ = moi_truong
    m = tao()
    _chay(_cai_va_nap(m, _cau_hinh_day_du(goc_url)))
    _chay(m.thuc_hien("dang_ky.luot", "bat_dau", {}))
    r = _chay(m.thuc_hien("dang_ky.luot", "ghi_nhan_nguoi_xu_ly", {}))
    assert r["ok"] is False and r["ma_loi"] == "khong_o_trang_thai_can_nguoi"


def test_hanh_dong_la_bi_tu_choi(moi_truong):
    _, tao, goc_url, _ = moi_truong
    m = tao()
    _chay(_cai_va_nap(m, _cau_hinh_day_du(goc_url)))
    r = _chay(m.thuc_hien("dang_ky.luot", "hanh_dong_khong_co", {}))
    assert r["ok"] is False


def test_bi_mat_khong_vao_nhat_ky(moi_truong, caplog):
    _, tao, goc_url, _ = moi_truong
    m = tao()
    _chay(_cai_va_nap(m, _cau_hinh_day_du(goc_url)))
    with caplog.at_level(logging.DEBUG):
        r = _chay(m.thuc_hien("dang_ky.luot", "bat_dau", {}))
        _chay(m.thuc_hien("dang_ky.luot", "doi_ma", {"cho_giay": 3}))
    mat_khau = r["ket_qua"]["thong_tin_tai_khoan"]["mat_khau"]
    assert mat_khau not in caplog.text
    assert KHOA_HOP_THU not in caplog.text
    assert MA_THU not in caplog.text


def test_suc_khoe_khong_lo_bi_mat(moi_truong):
    _, tao, goc_url, _ = moi_truong
    m = tao()
    assert _chay(m.suc_khoe())["ok"] is False  # chưa cấu hình/bật
    _chay(_cai_va_nap(m, _cau_hinh_day_du(goc_url)))
    sk = _chay(m.suc_khoe())
    assert sk["ok"] is True
    chuoi = json.dumps(sk, ensure_ascii=False)
    assert KHOA_HOP_THU not in chuoi


# ───────────────────────── Qua API của lõi ─────────────────────────


@pytest.fixture()
def client(tmp_path, monkeypatch, may_gia):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", "khoa-quan-tri-gia-nv174")
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    dich = tmp_path / "modules"
    dich.mkdir(parents=True)
    shutil.copytree(THU_MUC_MODULE, dich / "register")
    with TestClient(app) as c:
        yield c, tmp_path, may_gia[0]


def test_api_401_403_hang_cho_va_quan_tri_chay(client):
    c, _, goc_url = client
    r_cai = c.post("/api/modules/register/cai", headers=KHOA_QT,
                   json={"cau_hinh": _cau_hinh_day_du(goc_url)})
    assert r_cai.status_code == 200 and r_cai.json()["ok"] is True, r_cai.text

    # Thiếu khoá → 401.
    r = c.post("/api/thiet-bi/dang_ky.luot/hanh-dong", json={"hanh_dong": "bat_dau"})
    assert r.status_code == 401

    # Khoá khách chỉ có phạm vi chat → 403.
    r_tao = c.post("/api/khoa-khach", headers=KHOA_QT,
                   json={"nhan": "Khách chat", "pham_vi": ["chat"]})
    hd_chat = {"Authorization": f"Bearer {r_tao.json()['khoa']['khoa']}"}
    r = c.post("/api/thiet-bi/dang_ky.luot/hanh-dong", headers=hd_chat,
               json={"hanh_dong": "bat_dau"})
    assert r.status_code == 403

    # Khoá khách CÓ thiet_bi:ghi: thực thể nguy_hiem chưa xác nhận → 202 hàng
    # chờ, module KHÔNG chạy (trạng thái vẫn chưa bắt đầu).
    r_tao2 = c.post("/api/khoa-khach", headers=KHOA_QT,
                    json={"nhan": "App nhà", "pham_vi": ["thiet_bi:ghi", "thiet_bi:doc"]})
    hd_khach = {"Authorization": f"Bearer {r_tao2.json()['khoa']['khoa']}"}
    r = c.post("/api/thiet-bi/dang_ky.luot/hanh-dong", headers=hd_khach,
               json={"hanh_dong": "bat_dau"})
    assert r.status_code == 202 and r.json()["cho_phe_duyet"] is True
    # Thực thể mới chưa xác nhận → lõi coi là nguy_hiem: quản trị phải xác nhận
    # cả hai thực thể một lần (SEC-004f), sau đó mới xem/chạy được.
    assert c.post("/api/thiet-bi/dang_ky.trang_thai/xac-nhan", headers=KHOA_QT).status_code == 200
    xem = c.post("/api/thiet-bi/dang_ky.trang_thai/hanh-dong", headers=KHOA_QT,
                 json={"hanh_dong": "xem"})
    assert xem.status_code == 200
    assert xem.json()["ket_qua"]["buoc"] == "chua_bat_dau"

    # Quản trị xác nhận thực thể + xác nhận hành động → chạy thật.
    assert c.post("/api/thiet-bi/dang_ky.luot/xac-nhan", headers=KHOA_QT).status_code == 200
    r = c.post("/api/thiet-bi/dang_ky.luot/hanh-dong", headers=KHOA_QT,
               json={"hanh_dong": "bat_dau", "xac_nhan": True})
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    assert r.json()["trang_thai_moi"]["buoc"] == "cho_nguoi_dien"
    # Mật khẩu chỉ nằm trong phản hồi lần này của quản trị — xem lại thì không còn.
    mat_khau = r.json()["ket_qua"]["thong_tin_tai_khoan"]["mat_khau"]
    xem2 = c.post("/api/thiet-bi/dang_ky.trang_thai/hanh-dong", headers=KHOA_QT,
                  json={"hanh_dong": "xem"})
    assert mat_khau not in xem2.text
