"""NV266 (bản đúng) — nối «đo tài nguyên» (backend NV261) vào trang Module mới của NV259.

(a) Chưa đo: bảng tổng hiện «chưa đo» ở RAM/CPU/GPU + nút «Đo ngay»; MỞ TRANG/ĐỔI
    TIÊU ĐIỂM KHÔNG TỰ ĐO (không gọi POST /api/he-thong/do-tai-nguyen), không hẹn giờ.
(b) Bấm «Đo ngay» → POST API thật, xong nút thành «Đo lại», đóng dấu «Đo lúc HH:MM»,
    hàng RAM/CPU/GPU (phạm vi) = tổng các module trong phạm vi từ ảnh đo, nhãn nguồn
    đúng như API trả («ước lượng»/«đo»/«chưa đo»), không bịa số.
(c) «Dùng nhiều nhất»: chip RAM/CPU chỉ có số sau khi đo (trước đó «chưa đo»);
    top 3 theo ảnh đo, có số liệu + nhãn nguồn; chấm đánh dấu trên ô/pill của 3 module
    đó CHỈ sau khi đã đo.
(d) Lỗi (401/403/429/5xx, mạng): thông báo ngắn trong khối, không văng,
    không xoá ảnh đo cũ (đo lại lần 2 lỗi 429 → số cũ giữ nguyên).
"""

from __future__ import annotations

import datetime

import httpx
import pytest
import test_web2api_nv255_trang_module as m255
from test_web2api_nv255_trang_module import QT, module_con, module_nen, o  # noqa: F401
from test_web2api_nv259_trang_module import cai, mo_trang, tieu_de_bang  # noqa: F401
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture

can_chrome = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

GIT2 = {"nen-gia": module_nen(), "con-gia": module_con()}

# Ảnh đo giả, định hướng theo contract thật của POST /api/he-thong/do-tai-nguyen
# (xem app/do_tai_nguyen.py::do_tat_ca).
ANH_DO = {
    "nen-gia": {"ram_mb": 12.5, "cpu_pct": 3.2, "nguon": "uoc_luong", "rieng": False, "goc": "quet_tham_chieu"},
    "con-gia": {"ram_mb": 40.0, "cpu_pct": 10.0, "nguon": "do", "rieng": True, "goc": "cgroup"},
}


def mock_do(monkeypatch, anh_do=None):
    """Thay do_tat_ca bằng ảnh đo giả + bộ đếm số lần POST thật qua route."""
    dem = {"n": 0}

    def gia(so, *a, **k):
        dem["n"] += 1
        return {"luc": "2026-10-24T01:00:00+00:00", "cua_so_cpu_giay": 2.0,
                "module": anh_do if anh_do is not None else dict(ANH_DO),
                "tong": {"gpu": None}, "do_chuan": {"tracemalloc": False}}

    import app.do_tai_nguyen as dtn
    monkeypatch.setattr(dtn, "do_tat_ca", gia)
    return dem


def text_cua(trang, id_):
    return trang.js(
        f"(() => {{ const e = document.getElementById('{id_}'); return e ? e.textContent.trim() : null; }})()")


def mo_tong_web2api(trang, w):
    mo_trang(trang, w)
    trang.bam("document.querySelector('[data-goc]')", mo_ta="nút gốc web2api")
    assert tieu_de_bang(trang) == "Tổng web2api"


def bat_dem_do(trang):
    """Đếm số lượt trang GỌI POST /api/he-thong/do-tai-nguyen (kể cả khi server
    trả 429 — khác với đếm do_tat_ca chỉ đếm lượt đo thành công)."""
    assert trang.js("""(() => {
      if (window.__demDoSpy) return true;
      window.__demDo = 0; window.__demDoSpy = true;
      const _f = window.fetch.bind(window);
      window.fetch = function(u, o) {
        try { if (String(u && u.url || u).indexOf('do-tai-nguyen') >= 0) window.__demDo++; } catch (e) {}
        return _f(u, o);
      };
      return true;
    })()""") is True


def dem_do(trang):
    return trang.js("window.__demDo || 0")


# ───────────── (a) chưa đo, không tự đo ─────────────

@can_chrome
@pytest.mark.parametrize("rong,cao", [(390, 800), (768, 1024), (1280, 800)])
def test_mo_trang_khong_tu_do(trang, tmp_path, monkeypatch, rong, cao):
    """Mở trang: nút «Đo ngay», RAM/CPU/GPU «chưa đo», KHÔNG gọi POST đo."""
    dem = mock_do(monkeypatch)
    with m255.loi_that(tmp_path, monkeypatch, git=GIT2) as w:
        cai(w, "nen-gia", "con-gia")
        trang.khung(rong, cao, dien_thoai=(rong < 720))
        mo_tong_web2api(trang, w)
        bat_dem_do(trang)
        assert text_cua(trang, "nut-do-lai") == "Đo ngay"
        assert text_cua(trang, "tong-ram") == "chưa đo"
        assert text_cua(trang, "tong-cpu") == "chưa đo"
        assert text_cua(trang, "tong-gpu") == "chưa đo"
        assert text_cua(trang, "do-luc") == "chưa đo"
        # chip RAM/CPU: chưa đo → «chưa đo»
        trang.bam("document.querySelector('[data-xep=\"ram\"]')", mo_ta="chip RAM")
        assert text_cua(trang, "dung-nhieu-chua-do") == "chưa đo"
        assert dem["n"] == 0, "mở trang/đổi chip KHÔNG được tự gọi API đo"
        assert dem_do(trang) == 0, "không có lượt POST đo nào được gọi"


# ───────────── (b) bấm đo: số đúng phạm vi ─────────────

@can_chrome
def test_bam_do_hien_dung_pham_vi(trang, tmp_path, monkeypatch):
    """Bấm «Đo ngay»: 1 lượt POST, nút → «Đo lại», «Đo lúc HH:MM»,
    RAM (phạm vi) = 12.5 + 40.0 = 52.5 MB, có nhãn nguồn."""
    dem = mock_do(monkeypatch)
    with m255.loi_that(tmp_path, monkeypatch, git=GIT2) as w:
        cai(w, "nen-gia", "con-gia")
        mo_tong_web2api(trang, w)
        bat_dem_do(trang)
        trang.bam("document.getElementById('nut-do-lai')", mo_ta="nút Đo ngay")
        trang.cho("document.getElementById('nut-do-lai').textContent.trim() === 'Đo lại'",
                  han=20, mo_ta="nút thành Đo lại")
        assert dem["n"] == 1
        assert dem_do(trang) == 1
        gio = datetime.datetime.now().strftime("%H:%M")
        assert text_cua(trang, "do-luc") == "Đo lúc " + gio
        # so khớp TUYỆT ĐỐI (không dùng `in`: "-52.5 MB" cũng chứa "52.5 MB")
        assert text_cua(trang, "tong-ram") == "52.5 MB (hỗn hợp)"
        assert text_cua(trang, "tong-cpu") == "13.2% (hỗn hợp)"
        # chưa đo máy này không GPU → «không có GPU» (từ API, không bịa)
        assert text_cua(trang, "tong-gpu") == "không có GPU"


# ───────────── (c) đổi tiêu điểm không tự đo ─────────────

@can_chrome
def test_doi_tieu_diem_khong_tu_do(trang, tmp_path, monkeypatch):
    """Đo xong ở Tổng web2api, bấm tên làn → không gọi thêm lượt đo nào."""
    dem = mock_do(monkeypatch)
    with m255.loi_that(tmp_path, monkeypatch, git=GIT2) as w:
        cai(w, "nen-gia", "con-gia")
        mo_tong_web2api(trang, w)
        bat_dem_do(trang)
        trang.bam("document.getElementById('nut-do-lai')", mo_ta="nút Đo ngay")
        trang.cho("document.getElementById('nut-do-lai').textContent.trim() === 'Đo lại'",
                  han=20, mo_ta="nút thành Đo lại")
        assert dem_do(trang) == 1
        trang.bam("document.querySelector('.lane-ten')", mo_ta="tên làn đầu")
        trang.js("new Promise(r => setTimeout(r, 1500))")
        assert dem_do(trang) == 1, "đổi tiêu điểm KHÔNG được tự đo"


# ───────────── (d) «chưa đo» khi API trả chua_do ─────────────

@can_chrome
def test_chua_do_khi_api_tra_chua_do(trang, tmp_path, monkeypatch):
    """Module trong ảnh đo có ram_mb=None/nguon='chua_do' → hàng hiện «chưa đo»."""
    anh = {"nen-gia": {"ram_mb": None, "cpu_pct": None, "nguon": "chua_do", "rieng": True, "goc": "cgroup"},
           "con-gia": {"ram_mb": None, "cpu_pct": None, "nguon": "chua_do", "rieng": True, "goc": "cgroup"}}
    mock_do(monkeypatch, anh)
    with m255.loi_that(tmp_path, monkeypatch, git=GIT2) as w:
        cai(w, "nen-gia", "con-gia")
        mo_tong_web2api(trang, w)
        trang.bam("document.getElementById('nut-do-lai')", mo_ta="nút Đo ngay")
        trang.cho("document.getElementById('nut-do-lai').textContent.trim() === 'Đo lại'",
                  han=20, mo_ta="nút thành Đo lại")
        assert text_cua(trang, "tong-ram") == "chưa đo"
        assert text_cua(trang, "tong-cpu") == "chưa đo"


# ───────────── (e) 429: thông báo, giữ số cũ ─────────────

@can_chrome
def test_429_thong_bao_giu_so_cu(trang, tmp_path, monkeypatch):
    """Đo lần 1 OK; lần 2 server trả 429 → thông báo ngắn, ảnh đo cũ còn nguyên."""
    import app.do_tai_nguyen as dtn

    class Loi429(Exception):
        def __init__(self):
            super().__init__("Đo quá dày: mỗi người dùng chỉ được đo 1 lần/giây.")
            self.ma = 429

    lan = {"n": 0}

    def gia(so, *a, **k):
        lan["n"] += 1
        if lan["n"] > 1:
            raise Loi429()
        return {"luc": "2026-10-24T01:00:00+00:00", "module": dict(ANH_DO),
                "tong": {"gpu": None}, "do_chuan": {}}

    monkeypatch.setattr(dtn, "do_tat_ca", gia)
    with m255.loi_that(tmp_path, monkeypatch, git=GIT2) as w:
        cai(w, "nen-gia", "con-gia")
        mo_tong_web2api(trang, w)
        trang.bam("document.getElementById('nut-do-lai')", mo_ta="nút Đo ngay")
        trang.cho("document.getElementById('nut-do-lai').textContent.trim() === 'Đo lại'",
                  han=20, mo_ta="nút thành Đo lại")
        ram_cu = text_cua(trang, "tong-ram")
        assert ram_cu == "52.5 MB (hỗn hợp)", ram_cu
        # bấm Đo lại → 429
        trang.bam("document.getElementById('nut-do-lai')", mo_ta="nút Đo lại")
        trang.cho("!!document.getElementById('do-loi').textContent.trim()",
                  han=20, mo_ta="thông báo lỗi")
        loi = text_cua(trang, "do-loi")
        assert "quá dày" in loi, loi
        assert text_cua(trang, "tong-ram") == ram_cu, "429 KHÔNG được xoá ảnh đo cũ"
        assert text_cua(trang, "nut-do-lai") == "Đo lại"


# ───────────── (f) dùng nhiều nhất + chấm đánh dấu ─────────────

@can_chrome
def test_dung_nhieu_nhat_ram_va_cham(trang, tmp_path, monkeypatch):
    """Sau khi đo, chip RAM → top theo ảnh đo (con-gia 40.0 MB trước nen-gia 12.5 MB),
    có số liệu + nhãn nguồn; ô/pill của các module đó có chấm đánh dấu.
    Trước khi đo: không có chấm nào."""
    mock_do(monkeypatch)
    with m255.loi_that(tmp_path, monkeypatch, git=GIT2) as w:
        cai(w, "nen-gia", "con-gia")
        mo_tong_web2api(trang, w)
        cham_truoc = trang.js("document.querySelectorAll('.pill .cham').length")
        assert cham_truoc == 0, "chưa đo KHÔNG được có chấm đánh dấu"
        trang.bam("document.getElementById('nut-do-lai')", mo_ta="nút Đo ngay")
        trang.cho("document.getElementById('nut-do-lai').textContent.trim() === 'Đo lại'",
                  han=20, mo_ta="nút thành Đo lại")
        trang.bam("document.querySelector('[data-xep=\"ram\"]')", mo_ta="chip RAM")
        ds = trang.js(
            "(() => [...document.querySelectorAll('.ds-dung-nhieu li')].map(e => e.textContent.trim()))()")
        assert len(ds) == 2, ds
        assert "40.0 MB" in ds[0] and "12.5 MB" in ds[1], ds
        cham = trang.js("document.querySelectorAll('.pill .cham').length")
        assert cham == 2, f"pill của 2 module top RAM phải có chấm, thấy {cham}"
        for mid in ("nen-gia", "con-gia"):
            co = trang.js(
                "(() => { const p = document.querySelector('[data-o=\"" + mid + "\"]');"
                " return !!(p && p.querySelector('.cham')); })()")
            assert co, f"pill {mid} thiếu chấm đánh dấu"


# ───────────── API thật: contract ─────────────

def test_api_do_tai_nguyen_that(tmp_path, monkeypatch):
    """POST /api/he-thong/do-tai-nguyen thật (không giả): trả đúng contract
    {luc, module{...}, tong, do_chuan}, nguon ∈ {do, uoc_luong, chua_do}."""
    with m255.loi_that(tmp_path, monkeypatch, git=GIT2) as w:
        cai(w, "nen-gia", "con-gia")
        r = httpx.post(f"{w.url}/api/he-thong/do-tai-nguyen", headers=QT, timeout=60)
        assert r.status_code == 200, r.text
        j = r.json()
        assert "module" in j and "tong" in j and "luc" in j
        for mid, muc in j["module"].items():
            assert muc["nguon"] in ("do", "uoc_luong", "chua_do"), (mid, muc)
