"""NV106 — rà soát `adapt` mục 1+2: vùng chạm ≥ 44px, chữ không dưới 12px, ô nhập ≥ 16px.

CHƯA CHẠY ĐƯỢC: máy này không có /usr/bin/google-chrome (CO_CHROME = False) nên các test
dưới đây đều bị bỏ qua. Người giao việc chạy bằng Chrome thật và chỉnh nếu cần.

Cốt lõi tập lệnh đo lấy từ `do-adapt-mau.py` của người giao việc (Chrome thật, giả lập
360x740, CÓ bật cảm ứng) + 2 provider giả để trang AI/Studio hiện thật.

Test điện thoại (360x740, mobile=True, cảm ứng BẬT qua Emulation.setTouchEmulationEnabled
để matchMedia('(pointer:coarse)') thật sự đúng — thiếu bước này số đo sai):
đi qua 9 trang (He-thong, He-thong-AI, He-thong-Bao-mat, He-thong-Phien, He-thong-Thiet-bi,
He-thong-Tich-hop, He-thong-Tro-giup, He-thong-Module, Studio), mỗi trang đo 2 lượt
(mặc định + mở hết mục thu gọn), khẳng định:
  - mọi phần tử bấm được hiện ra (button, a[href], [role=button], [role=tab], summary,
    select) có CẢ chiều cao và chiều rộng ≥ 44px, hoặc nằm trong một vùng bấm cha
    (label/a/button) ≥ 44px cả hai chiều;
  - mọi ô nhập (input trừ checkbox/radio, textarea, select) có font-size ≥ 16px;
  - mọi chữ nội dung có font-size ≥ 12px (bỏ qua chữ ẩn cho trình đọc màn hình:
    phần tử .an-chu, aria-hidden, hoặc 0 kích thước);
  - document.documentElement.scrollWidth <= innerWidth + 1 (không tràn ngang).

Test máy tính (1280x800, cảm ứng TẮT): chứng minh máy tính không đổi —
matchMedia('(pointer:coarse)') phải SAI, và chiều cao nút «Đăng xuất» + chip lọc trang
Hệ thống PHẢI y nguyên giá trị thiết kế trong khung.css. Giá trị suy từ CSS (máy làm
NV106 không có Chrome nên chưa đo thật; người giao việc chạy test này sẽ xác nhận):
  - #nut-dang-xuat (class "nut nut-nho"): .nut có box-sizing:border-box, .nut-nho
    đè min-height:40px → cao đúng 40px (viền đã tính trong border-box);
  - #loc-tt .chip: .chip/.loc .chip KHÔNG đặt box-sizing (content-box),
    min-height:40px + viền 1px×2 → cao 42px (nội dung 1 dòng, chữ 13px < 40px).
Người chạy Chrome nếu thấy số khác thì cập nhật 2 hằng này theo số đo thật.
"""

from __future__ import annotations

import json
import time

import pytest

from app import so_module
from app.main import app
from gia import MayChuThat
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome (NV106: chưa chạy trên máy này)")

KHOA_QT = "khoa-quan-tri-nv106-0123456789abcdef"

TRANG_CAN_DO = [
    "He-thong.dc.html", "He-thong-AI.dc.html", "He-thong-Bao-mat.dc.html", "He-thong-Phien.dc.html",
    "He-thong-Thiet-bi.dc.html", "He-thong-Tich-hop.dc.html", "He-thong-Tro-giup.dc.html",
    "He-thong-Module.dc.html", "Studio.dc.html",
]

# Cốt lõi tập lệnh đo của do-adapt-mau.py, mở rộng khẳng định ≥ 44px cả hai chiều,
# vùng bấm cha ≥ 44px, chữ ≥ 12px (trừ chữ ẩn), ô nhập ≥ 16px, không tràn ngang.
JS_KIEM_ADAPT = r"""
(() => {
  const hien = (e) => { const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const mo_ta = (e) => (e.id ? '#' + e.id : '') + ' <' + e.tagName.toLowerCase() + '> «'
    + ((e.innerText || e.value || e.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 28)) + '»';
  const vung_cha = (e) => { let p = e.parentElement;
    while (p && p !== document.body) {
      if (/^(A|BUTTON|LABEL)$/.test(p.tagName) && hien(p)) { const r = p.getBoundingClientRect(); return Math.round(r.width) + 'x' + Math.round(r.height); }
      p = p.parentElement;
    } return null; };
  const cham_nho = [...document.querySelectorAll('button, a[href], [role=button], [role=tab], select, input[type=checkbox], input[type=radio], summary')]
    .filter(hien).map((e) => { const r = e.getBoundingClientRect();
      return {m: mo_ta(e), w: Math.round(r.width), h: Math.round(r.height), cha: vung_cha(e)}; })
    .filter((x) => (x.w < 44 || x.h < 44) && !(x.cha && x.cha.split('x').every((v) => +v >= 44)));
  const nhap_nho = [...document.querySelectorAll('input:not([type=checkbox]):not([type=radio]), textarea, select')].filter(hien)
    .map((e) => ({m: mo_ta(e), px: parseFloat(getComputedStyle(e).fontSize)})).filter((x) => x.px < 16);
  const chu_nho = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n; while ((n = walker.nextNode())) { const t = n.nodeValue.trim(); if (!t) continue; const p = n.parentElement;
    if (!p || !hien(p) || ['SCRIPT', 'STYLE'].includes(p.tagName)) continue;
    if (p.closest('.an-chu,[aria-hidden="true"]')) continue;  // chữ ẩn cho trình đọc màn hình: bỏ qua
    const px = parseFloat(getComputedStyle(p).fontSize);
    if (px < 12 && px > 0) chu_nho.push({m: mo_ta(p), px}); }
  return {cham_nho, nhap_nho, chu_nho: chu_nho.slice(0, 10),
          tran_ngang: document.documentElement.scrollWidth > innerWidth + 1,
          coarse: matchMedia('(pointer:coarse)').matches};
})()
"""


def _tao_provider_gia(data_dir):
    for id_, tp in (("ai-gia-a", "aigiaa"), ("ai-gia-b", "aigiab")):
        d = data_dir / "modules" / id_
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({
            "id": id_, "ten": "Provider giả " + id_[-1].upper(), "mo_ta": "x", "phien_ban": "1",
            "loai": "ai", "hang": "A", "giao_thuc": "openai", "tien_to": tp, "cung_cap": ["chat"],
            "truong_cau_hinh": [{"khoa": "khoa_api", "nhan": "Khoá API", "kieu": "bi_mat", "bat_buoc": True}],
            "tai_nguyen": {"mang_ra": []}}))
        (d / "module.py").write_text("""
class Module:
    def __init__(self, m): self.manifest = m
    def nap(self, c): pass
    async def cai(self, c): return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": c}
    async def suc_khoe(self): return {"ok": True, "khoa": []}
    async def dung(self): pass
    async def models(self): return [{"id": "%s/m1", "object": "model", "owned_by": "gia"}]
    async def chat(self, body, stream): return 200, {"content-type": "application/json"}, b'{"choices":[]}'
""" % tp)


def _nen(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    _tao_provider_gia(data_dir)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")


def _bat_cam_ung(trang):
    trang.cdp.goi("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5}, trang.s)
    trang.cdp.goi("Emulation.setEmitTouchEventsForMouse", {"enabled": True, "configuration": "mobile"}, trang.s)


def test_adapt_cham_44_chu_12(trang, tmp_path, monkeypatch):
    _nen(tmp_path, monkeypatch)
    import httpx
    with MayChuThat(app, lifespan="on") as w:
        base = w.url
        for i in ("ai-gia-a", "ai-gia-b"):
            httpx.post(f"{base}/api/modules/{i}/cai",
                       json={"cau_hinh": {"khoa_api": "khoa-gia-nv106-" + i}},
                       headers={"Authorization": "Bearer " + KHOA_QT}, timeout=30)
        dang_nhap_quan_tri(trang, base)
        trang.khung(360, 740, dien_thoai=True)
        _bat_cam_ung(trang)
        for ten in TRANG_CAN_DO:
            for luot, mo in (("dong", False), ("mo-het", True)):
                trang.di(f"{base}/{ten}", mo_muc=False)
                time.sleep(1.2)
                if mo:
                    trang.mo_het_muc()
                    time.sleep(0.6)
                kq = trang.js(JS_KIEM_ADAPT)
                assert kq["coarse"], f"{ten} ({luot}): (pointer:coarse) phải đúng khi cảm ứng bật"
                assert not kq["tran_ngang"], f"{ten} ({luot}): tràn ngang"
                assert kq["cham_nho"] == [], f"{ten} ({luot}): vùng chạm < 44px: {kq['cham_nho'][:5]}"
                assert kq["nhap_nho"] == [], f"{ten} ({luot}): ô nhập < 16px: {kq['nhap_nho'][:5]}"
                assert kq["chu_nho"] == [], f"{ten} ({luot}): chữ < 12px: {kq['chu_nho'][:5]}"


def test_desktop_khong_doi(trang, tmp_path, monkeypatch):
    """Khung 1280x800, cảm ứng TẮT: giao diện máy tính phải Y NGUYÊN.

    (pointer:coarse) phải sai (mọi quy tắc NV106 nằm trong khối cảm ứng), và
    chiều cao nút «Đăng xuất» phải đúng 40px (.nut border-box + .nut-nho
    min-height:40px), chip lọc #loc-tt đúng 42px (content-box: min-height 40px
    + viền 2px). Hai hằng suy từ khung.css, chưa đo bằng Chrome.
    """
    _nen(tmp_path, monkeypatch)
    with MayChuThat(app, lifespan="on") as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.khung(1280, 800, dien_thoai=False)
        trang.cdp.goi("Emulation.setTouchEmulationEnabled", {"enabled": False}, trang.s)
        trang.di(f"{w.url}/He-thong.dc.html", mo_muc=False)
        time.sleep(1.0)
        kq = trang.js("""(() => {
          const h = (s) => { const e = document.querySelector(s); const r = e.getBoundingClientRect();
            return Math.round(r.height); };
          return {coarse: matchMedia('(pointer:coarse)').matches,
                  dangXuat: h('#nut-dang-xuat'),
                  chip: [...document.querySelectorAll('#loc-tt .chip')].map(h)};
        })()""")
        assert kq["coarse"] is False, "desktop chuột: (pointer:coarse) phải sai"
        assert kq["dangXuat"] == 40, f"nút «Đăng xuất» desktop phải cao 40px, đo được {kq['dangXuat']}"
        assert kq["chip"] and all(c == 42 for c in kq["chip"]), \
            f"chip lọc desktop phải cao 42px (40 + viền 2px), đo được {kq['chip']}"
