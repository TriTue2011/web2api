"""NV259 — hoàn thiện trang Module: TỔNG theo phạm vi + MỘT TIÊU ĐIỂM + bố cục 720px/popup.

(a) Bấm đúng mục hiện tổng đúng mục: nút gốc → «Tổng web2api» (chia theo nhóm);
    tên làn/chip nhóm → «Tổng nhóm X» (chia theo nhóm con); tên nhóm con → «Nhóm con: …»
    (chia theo module); ô → chi tiết module. Mỗi tổng: đĩa đang dùng (Σ dung_luong_dia
    module đã cài), «x trên y module đã cài», «Dùng nhiều nhất» xếp theo Đĩa
    (chip RAM/CPU có sẵn nhưng «chưa đo» khi chưa có dữ liệu — backend đo là NV261).
    Bấm nhóm LUÔN chọn nhóm đó (không bật/tắt); xem tất cả thì bấm nút gốc.
(b) Một tiêu điểm: aria-pressed chỉ true ở mục đang là tiêu điểm. Bấm ra ngoài
    (xét bằng composedPath) hoặc Esc → bỏ chọn module/nhóm con, về tổng của phạm vi đang xem.
(c) Bố cục: ≥720px hai cột (bảng phải ~320px sticky); <720px bảng là popup giữa màn hình
    (nền mờ, Đóng/Esc đóng, trả tiêu điểm).
"""

from __future__ import annotations

import re

import httpx
import pytest
from conftest import cai_git_hai_luot
from test_web2api_nv255_trang_module import QT, ds_catalog, loi_that, module_con, module_nen, o
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture

can_chrome = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

GIT2 = {"nen-gia": module_nen(), "con-gia": module_con()}


def mo_trang(trang, w):
    dang_nhap_quan_tri(trang, w.url)
    trang.di(w.url + "/He-thong.dc.html")
    trang.cho(o("nen-gia"), han=20, mo_ta="ô nen-gia")
    trang.cho(o("con-gia"), han=20, mo_ta="ô con-gia")


def tieu_de_bang(trang):
    return trang.js("(() => { const e = document.getElementById('chi-tiet-ten'); return e ? e.textContent.trim() : null; })()")


def cai(w, *ids):
    for mid in ids:
        assert cai_git_hai_luot(httpx, mid, headers=QT,
                                duong_dan=f"{w.url}/api/nguon-git/cai/{mid}").status_code == 200


# ───────────── backend: nhom_con cho module nguồn git ─────────────

def test_git_danh_sach_tra_nhom_con(tmp_path, monkeypatch):
    """Module nguồn git (CHƯA cài) cũng phải mang nhom_con trong /api/modules/thong-bao:
    NV255 mới thêm nhom_con ở catalog (đã cài) mà sót ở tóm tắt git — trang đành xếp
    mọi ô chưa cài vào nhóm con mặc định, không hiện tên nhóm con, không bấm chọn được."""
    import time
    with loi_that(tmp_path, monkeypatch, git=GIT2) as w:
        ds = []
        for _ in range(40):
            r = httpx.get(f"{w.url}/api/modules/thong-bao", headers=QT)
            assert r.status_code == 200, r.text
            ds = r.json()["danh_sach"]
            if ds:
                break
            time.sleep(0.25)
        con = next(m for m in ds if m["id"] == "con-gia")
        assert con.get("nhom_con") == {"id": "cong-cu-ai", "ten": "Trợ lý và công cụ dùng AI", "thu_tu": 3}
        nen = next(m for m in ds if m["id"] == "nen-gia")
        assert nen.get("nhom_con") == {"id": "nen-tai-khoan", "ten": "Nền và tài khoản", "thu_tu": 1}


# ───────────── (a) tổng theo phạm vi ─────────────

@can_chrome
def test_tong_web2api_khi_bam_nut_goc(trang, tmp_path, monkeypatch):
    """Nút gốc → «Tổng web2api»: đĩa = Σ dung_luong_dia module đã cài (đối chiếu API),
    đếm «x trên y module đã cài», chia theo nhóm, «Dùng nhiều nhất» theo Đĩa; chip RAM → «chưa đo»."""
    with loi_that(tmp_path, monkeypatch, git=GIT2) as w:
        cai(w, "nen-gia")
        cat = ds_catalog(w)
        dia_tong = sum(m["dung_luong_dia"] for m in cat if isinstance(m.get("dung_luong_dia"), (int, float)))
        mo_trang(trang, w)
        trang.bam("document.querySelector('[data-goc]')", mo_ta="nút gốc web2api")
        trang.cho("document.getElementById('tong-dem')", han=15, mo_ta="bảng Tổng web2api")
        assert tieu_de_bang(trang) == "Tổng web2api"
        so_o = trang.js("document.querySelectorAll('[data-o]').length")
        assert trang.chu("#tong-dem") == f"{len(cat)} trên {so_o} module đã cài"
        assert trang.js("Number(document.getElementById('tong-dia').dataset.byte)") == dia_tong
        # Chia theo nhóm: có dòng nhóm AI; bấm dòng → Tổng nhóm AI.
        assert trang.js("!!document.querySelector('[data-tong-loai=\"nhom\"][data-tong-ma=\"ai\"]')") is True
        # Dùng nhiều nhất theo Đĩa: module đã cài duy nhất đứng đầu.
        dau = trang.js("document.querySelector('.ds-dung-nhieu li') ? document.querySelector('.ds-dung-nhieu li').textContent : ''")
        assert "Nền giả" in dau, dau
        # Chip RAM/CPU có sẵn nhưng chưa có dữ liệu đo → «chưa đo».
        trang.bam("document.querySelector('[data-xep=\"ram\"]')", mo_ta="chip xếp RAM")
        trang.cho("document.getElementById('dung-nhieu-chua-do')", han=10, mo_ta="chưa đo RAM")
        assert "chưa đo" in trang.chu("#dung-nhieu-chua-do")
        trang.bam("document.querySelector('[data-xep=\"dia\"]')", mo_ta="chip xếp Đĩa")
        trang.cho("document.querySelector('.ds-dung-nhieu li')", han=10, mo_ta="danh sách dùng nhiều nhất trở lại")


@can_chrome
def test_tong_nhom_khong_bat_tat_va_nhom_con(trang, tmp_path, monkeypatch):
    """Tên làn → «Tổng nhóm AI» chia theo nhóm con, bấm lại KHÔNG tắt lọc;
    tên nhóm con → «Nhóm con: …» chia theo module; bấm dòng module → chi tiết module."""
    with loi_that(tmp_path, monkeypatch, git=GIT2) as w:
        cai(w, "nen-gia")
        mo_trang(trang, w)
        trang.bam("document.querySelector('[data-loc-lan=\"ai\"]')", mo_ta="tên làn AI")
        trang.cho("document.getElementById('tong-dem')", han=15, mo_ta="bảng Tổng nhóm")
        assert tieu_de_bang(trang) == "Tổng nhóm AI"
        assert trang.js("document.querySelectorAll('[data-lane]').length") == 1
        # Chia theo nhóm con: đủ 2 nhóm con của AI.
        assert trang.js("document.querySelectorAll('[data-tong-loai=\"con\"]').length") == 2
        # Bấm lại tên làn: vẫn chọn nhóm đó (không bật/tắt về tất cả).
        trang.bam("document.querySelector('[data-loc-lan=\"ai\"]')", mo_ta="tên làn AI lần 2")
        assert trang.js("document.querySelectorAll('[data-lane]').length") == 1
        assert tieu_de_bang(trang) == "Tổng nhóm AI"
        # Tên nhóm con → tổng nhóm con, chia theo module.
        trang.bam("document.querySelector('[data-loc-con=\"nen-tai-khoan\"]')", mo_ta="tên nhóm con")
        trang.cho("document.querySelector('[data-tong-loai=\"module\"][data-tong-ma=\"nen-gia\"]')",
                  han=15, mo_ta="dòng module trong tổng nhóm con")
        assert tieu_de_bang(trang) == "Nhóm con: Nền và tài khoản"
        # Bấm dòng module → chi tiết module đó.
        trang.bam("document.querySelector('[data-tong-loai=\"module\"][data-tong-ma=\"nen-gia\"]')", mo_ta="dòng module")
        trang.cho("document.getElementById('dung-luong')", han=15, mo_ta="chi tiết module")
        assert tieu_de_bang(trang) == "Nền giả"


@can_chrome
def test_do_lai_tai_nguyen(trang, tmp_path, monkeypatch):
    """Khối tài nguyên ở bảng tổng: «Đo lại» → đang đo → «Đo lúc HH:MM» (backend đo sâu là NV261)."""
    with loi_that(tmp_path, monkeypatch, git=GIT2) as w:
        mo_trang(trang, w)
        trang.bam("document.querySelector('[data-goc]')", mo_ta="nút gốc")
        trang.cho("document.getElementById('nut-do-lai')", han=15, mo_ta="nút Đo lại")
        assert "chưa đo" in trang.chu("#do-luc")
        trang.bam("document.getElementById('nut-do-lai')", mo_ta="bấm Đo lại")
        trang.cho("!!document.getElementById('do-luc') && /Đo lúc \\d{2}:\\d{2}/.test(document.getElementById('do-luc').textContent)",
                  han=15, mo_ta="Đo lúc HH:MM")
        assert re.search(r"Đo lúc \d{2}:\d{2}", trang.chu("#do-luc"))


# ───────────── (b) một tiêu điểm ─────────────

@can_chrome
def test_aria_pressed_chi_o_tieu_diem(trang, tmp_path, monkeypatch):
    """aria-pressed CHỈ true ở mục đang là tiêu điểm: chọn module thì gốc/chip/tên làn/nhóm con đều false."""
    with loi_that(tmp_path, monkeypatch, git=GIT2) as w:
        mo_trang(trang, w)
        ap = lambda expr: trang.js(f"({expr}).getAttribute('aria-pressed')")  # noqa: E731
        GOC = "document.querySelector('[data-goc]')"
        LAN = "document.querySelector('[data-loc-lan=\"ai\"]')"
        CON = "document.querySelector('[data-loc-con=\"nen-tai-khoan\"]')"
        trang.bam(o("nen-gia"), mo_ta="ô nen-gia")
        trang.cho("document.getElementById('dung-luong')", han=15, mo_ta="chi tiết module")
        assert ap(o("nen-gia")) == "true"
        assert ap(GOC) == "false"
        assert ap(LAN) == "false"
        assert ap(CON) == "false"
        # Chọn nhóm: chỉ tên làn true.
        trang.bam("document.querySelector('[data-loc-lan=\"ai\"]')", mo_ta="tên làn AI")
        trang.cho("document.getElementById('tong-dem')", han=15, mo_ta="tổng nhóm")
        assert ap(LAN) == "true"
        assert ap(GOC) == "false"
        assert ap(o("nen-gia")) == "false"
        # Chọn gốc: chỉ nút gốc true.
        trang.bam("document.querySelector('[data-goc]')", mo_ta="nút gốc")
        trang.cho("document.getElementById('tong-dem')", han=15, mo_ta="tổng web2api")
        assert ap(GOC) == "true"
        assert ap(LAN) == "false"


@can_chrome
def test_bam_ra_ngoai_va_esc_ve_tong_pham_vi(trang, tmp_path, monkeypatch):
    """Bấm ra ngoài → bỏ chọn module, về tổng của phạm vi đang xem; Esc → bỏ chọn nhóm con,
    về tổng nhóm đang lọc; bấm TRONG bảng chi tiết thì không bỏ chọn."""
    with loi_that(tmp_path, monkeypatch, git=GIT2) as w:
        mo_trang(trang, w)
        trang.bam(o("nen-gia"), mo_ta="ô nen-gia")
        trang.cho("document.getElementById('dung-luong')", han=15, mo_ta="chi tiết module")
        # Bấm trong bảng chi tiết: vẫn ở chi tiết module.
        trang.bam("document.getElementById('chi-tiet-ten')", mo_ta="tiêu đề bảng chi tiết")
        assert tieu_de_bang(trang) == "Nền giả"
        # Bấm ra ngoài (đoạn mô tả trang — ngoài mọi vùng an toàn) → về Tổng web2api.
        trang.bam("document.querySelector('.mo-ta-trang')", mo_ta="vùng ngoài")
        trang.cho("document.getElementById('tong-dem')", han=15, mo_ta="về tổng web2api")
        assert tieu_de_bang(trang) == "Tổng web2api"
        assert trang.js(o("nen-gia") + ".getAttribute('aria-pressed')") == "false"
        # Lọc nhóm AI + chọn nhóm con, rồi Esc → về Tổng nhóm AI (phạm vi đang xem).
        trang.bam("document.querySelector('[data-loc-lan=\"ai\"]')", mo_ta="tên làn AI")
        trang.cho("document.getElementById('tong-dem')", han=15, mo_ta="tổng nhóm AI")
        trang.bam("document.querySelector('[data-loc-con=\"cong-cu-ai\"]')", mo_ta="tên nhóm con công cụ")
        trang.cho("document.querySelector('[data-tong-loai=\"module\"]')", han=15, mo_ta="tổng nhóm con")
        assert tieu_de_bang(trang).startswith("Nhóm con:")
        trang.cdp.goi("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape",
                                                 "windowsVirtualKeyCode": 27}, trang.s)
        trang.cdp.goi("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape",
                                                 "windowsVirtualKeyCode": 27}, trang.s)
        trang.cho("document.getElementById('chi-tiet-ten') && document.getElementById('chi-tiet-ten').textContent.trim() === 'Tổng nhóm AI'",
                  han=15, mo_ta="Esc về tổng nhóm")


# ───────────── (c) bố cục 720px / popup ─────────────

@can_chrome
def test_popup_duoi_720_va_hai_cot_tu_720(trang, tmp_path, monkeypatch):
    """<720px: bảng chi tiết là popup giữa màn hình (position fixed, có nền mờ, bấm nền mờ đóng).
    768px: hai cột — bảng sticky cạnh làn, không popup."""
    with loi_that(tmp_path, monkeypatch, git=GIT2) as w:
        cai(w, "nen-gia")
        mo_trang(trang, w)
        # Điện thoại 390×800.
        trang.khung(390, 800, dien_thoai=True)
        trang.di(w.url + "/He-thong.dc.html")
        trang.cho(o("nen-gia"), han=20, mo_ta="ô nen-gia ở 390px")
        trang.bam(o("nen-gia"), mo_ta="ô nen-gia")
        trang.cho("document.getElementById('chi-tiet') && document.getElementById('chi-tiet').classList.contains('mo')",
                  han=15, mo_ta="popup mở")
        geo = trang.js("(() => { const e = document.getElementById('chi-tiet'); const r = e.getBoundingClientRect();"
                       " const cs = getComputedStyle(e); return {pos: cs.position, disp: cs.display,"
                       " w: r.width, h: r.height, cx: r.left + r.width / 2, cy: r.top + r.height / 2}; })()")
        assert geo["pos"] == "fixed" and geo["disp"] != "none", geo
        assert geo["w"] <= 0.92 * 390 + 2, geo
        assert geo["h"] <= min(0.78 * 800, 600) + 2, geo
        assert abs(geo["cx"] - 195) <= 30 and abs(geo["cy"] - 400) <= 30, geo
        assert trang.js("!!document.querySelector('.nen-mo.mo')") is True
        # Bấm nền mờ → popup đóng.
        trang.bam("document.querySelector('.nen-mo')", mo_ta="nền mờ")
        trang.cho("!document.getElementById('chi-tiet') || !document.getElementById('chi-tiet').classList.contains('mo')",
                  han=15, mo_ta="popup đóng")
        assert trang.js("getComputedStyle(document.getElementById('chi-tiet')).display") == "none"
        # Máy tính bảng 768×1024: hai cột.
        trang.khung(768, 1024)
        trang.di(w.url + "/He-thong.dc.html")
        trang.cho(o("nen-gia"), han=20, mo_ta="ô nen-gia ở 768px")
        trang.bam(o("nen-gia"), mo_ta="ô nen-gia")
        trang.cho("document.getElementById('dung-luong')", han=15, mo_ta="chi tiết ở 768px")
        geo2 = trang.js("(() => { const e = document.getElementById('chi-tiet'); const cs = getComputedStyle(e);"
                        " const r = e.getBoundingClientRect(); const l = document.querySelector('.cot-luoi').getBoundingClientRect();"
                        " return {pos: cs.position, disp: cs.display, left: r.left, cotPhai: l.right, w: r.width}; })()")
        assert geo2["pos"] == "sticky", geo2
        assert geo2["disp"] != "none" and geo2["left"] >= geo2["cotPhai"] - 2, geo2  # cạnh nhau, không chồng
        assert abs(geo2["w"] - 320) <= 24, geo2  # cột phải ~320px


def test_bang_chi_tiet_gon_va_thang_cot_theo_mau(trang, tmp_path, monkeypatch):
    """Bảng chi tiết theo tỉ lệ của mẫu: tiêu đề ≤ 18px, chữ thân ≤ 14px, mọi nhãn «dt» cùng một cột (cùng mép trái)
    và mọi giá trị «dd» cùng một cột — ở máy tính lẫn điện thoại."""
    with loi_that(tmp_path, monkeypatch, git=GIT2) as w:
        cai(w, "nen-gia")
        mo_trang(trang, w)
        for rong, cao, dt in ((1280, 900, False), (390, 800, True)):
            trang.khung(rong, cao, dien_thoai=dt)
            trang.di(w.url + "/He-thong.dc.html")
            trang.cho(o("nen-gia"), han=20, mo_ta=f"ô nen-gia ở {rong}px")
            trang.bam(o("nen-gia"), mo_ta="ô nen-gia")
            trang.cho("document.querySelectorAll('#chi-tiet .bang-dong').length >= 3", han=15, mo_ta="bảng có hàng")
            kq = trang.js("(() => { const c = document.getElementById('chi-tiet');"
                          " const px = (e) => parseFloat(getComputedStyle(e).fontSize);"
                          " const dts = [...c.querySelectorAll('.bang-dong dt')].map(e => Math.round(e.getBoundingClientRect().left));"
                          " const dds = [...c.querySelectorAll('.bang-dong dd')].map(e => Math.round(e.getBoundingClientRect().left));"
                          " return {h2: px(c.querySelector('h2')), dt: [...new Set(dts)], dd: [...new Set(dds)],"
                          " tran: c.scrollWidth > c.clientWidth + 1}; })()")
            assert kq["h2"] <= 18, (rong, kq)
            assert len(kq["dt"]) == 1 and len(kq["dd"]) == 1, (rong, kq)   # thẳng cột
            assert kq["tran"] is False, (rong, kq)


def test_them_module_la_nut_nho_mo_popup_o_moi_co(trang, tmp_path, monkeypatch):
    """«Thêm module» là một nút nhỏ (không phải khung to); bấm thì ra POPUP giữa màn hình ở cả 1280px lẫn 390px,
    có nền mờ; Esc đóng."""
    with loi_that(tmp_path, monkeypatch, git=GIT2) as w:
        cai(w, "nen-gia")
        mo_trang(trang, w)
        for rong, cao, dt in ((1280, 900, False), (390, 800, True)):
            trang.khung(rong, cao, dien_thoai=dt)
            trang.di(w.url + "/He-thong.dc.html")
            trang.cho("document.getElementById('o-them')", han=20, mo_ta=f"nút Thêm module ở {rong}px")
            nut = trang.js("(() => { const r = document.getElementById('o-them').getBoundingClientRect();"
                           " return {w: r.width, h: r.height}; })()")
            assert nut["h"] <= 48 and nut["w"] <= 220, (rong, nut)      # nút nhỏ, không phải khung kéo hết hàng
            trang.bam("document.getElementById('o-them')", mo_ta="Thêm module")
            trang.cho("document.getElementById('chi-tiet')?.classList.contains('mo')", han=15, mo_ta="popup mở")
            geo = trang.js("(() => { const e = document.getElementById('chi-tiet'); const r = e.getBoundingClientRect();"
                           " return {pos: getComputedStyle(e).position, role: e.getAttribute('role'),"
                           " nen: !!document.querySelector('.nen-mo.mo'), cx: r.left + r.width / 2}; })()")
            assert geo["pos"] == "fixed" and geo["role"] == "dialog" and geo["nen"] is True, (rong, geo)
            assert abs(geo["cx"] - rong / 2) <= 30, (rong, geo)
            trang.js("document.getElementById('chi-tiet').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
            trang.cho("!document.getElementById('chi-tiet')?.classList.contains('mo')", han=15, mo_ta="Esc đóng popup")
