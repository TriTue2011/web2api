"""Tab «YouTube phát» (NV153 đợt C) bằng Chrome THẬT: 360px, cảm ứng bật.

Ghi chú môi trường: Chromium 152 trên máy này chặn mọi điều hướng tới 127.0.0.1
(Local Network Access, không tắt được bằng flag) nên test mở trang module qua
`file://` và giả lập API bằng `Page.addScriptToEvaluateOnNewDocument` — trang,
CSS, JS và thao tác chạm đều là thật. Lõi thật không với tới được trong môi
trường này (test Chrome cũ của repo cũng đỏ cùng lỗi — có sẵn, không do đợt C).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ui_chung import CO_CHROME, trang, trinh_duyet  # noqa: F401

GOC = Path(__file__).resolve().parent.parent
TRANG = (GOC / "modules" / "youtube-phat" / "web" / "index.html").as_uri()
KHUNG_CSS = (GOC / "web" / "khung.css").as_uri()

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

KHOA_DAY_DU = "khoa-that-day-du-cho-chi-test-0123456789"

STUB = """
(() => {
  const KHOA = %s;
  window.__daChep = [];
  const csrf = "csrf-gia-test";
  const thucThe = {
    id: "youtube_phat.dieu_khien",
    thuoc_tinh: { dich_vu_url: "http://192.0.2.10:8713", khoa: "…6789" },
  };
  const loa = [
    { id: "home_assistant.media_player.phong_khach", ten: "Loa phòng khách" },
    { id: "home_assistant.media_player.tivi", ten: "Tivi" },
  ];
  async function hanhDong(ten, thamSo) {
    if (ten === "tim") return { ok: true, ket_qua: [
      { ma_bai: "xss001", tieu_de: "<img src=x onerror=alert(1)>", kenh: "Kênh & Co",
        thoi_luong: 200, nguon: "youtube", url: "https://www.youtube.com/watch?v=xss001" },
      { ma_bai: "bai002", tieu_de: "Bài thường", kenh: "", thoi_luong: null,
        nguon: "zing", url: "https://zingmp3.vn/bai002" },
    ], tong: 2 };
    if (ten === "lay_khoa") return { ok: true, khoa: KHOA };
    if (ten === "trang_thai") return { ok: true, trang_thai: "playing",
      phien: { session_id: "s1", state: "playing",
               item: { title: "Bài đang phát" },
               queue: { index: 0, items: [{ title: "Bài đang phát" }, { title: "Bài kế" }] } } };
    if (ten === "danh_sach_playlist") return { ok: true, danh_sach: [
      { id: "abcdef012345", ten: "Nhạc Việt", so_bai: 3 } ] };
    if (ten === "nhap_ma_chia_se") {
      if (!String((thamSo||{}).ma || "").startsWith("TTPL1."))
        return { ok: false, loi: "mã chia sẻ phải bắt đầu bằng «TTPL1.»" };
      return { ok: true, playlist: { id: "zzzz9999", name: "Nhập từ web" } };
    }
    if (ten === "phat_len_loa" || ten === "dung_phien") return { ok: true };
    return { ok: false, loi: "hanh_dong_khong_ho_tro" };
  }
  window.fetch = async (url, tuyChon) => {
    const u = String(url);
    const than = tuyChon && tuyChon.body ? JSON.parse(tuyChon.body) : {};
    const tra = (ma, obj) => new Response(JSON.stringify(obj),
      { status: ma, headers: { "Content-Type": "application/json" } });
    if (u.endsWith("api/toi")) return tra(200, { csrf });
    if (u.includes("api/thiet-bi?module=youtube-phat")) return tra(200, { thuc_the: [thucThe] });
    if (u.includes("api/thiet-bi?module=home-assistant")) return tra(200, { thuc_the: loa });
    if (u.includes("/hanh-dong")) {
      if (u.includes("youtube_phat.dieu_khien")) {
        if (than.hanh_dong === "dung_phien" && window.__dung409) {
          window.__dung409 = false;
          return tra(409, { error: { message: "can_xac_nhan: cần xác nhận" } });
        }
        return tra(200, await hanhDong(than.hanh_dong, than.tham_so));
      }
      return tra(200, { ok: true });
    }
    return tra(404, {});
  };
  try {
    Object.defineProperty(navigator, "clipboard", { value: {
      writeText: async (t) => { window.__daChep.push(t); },
    }, configurable: true });
  } catch (e) { /* vài bản Chrome không cho định nghĩa lại */ }
})();
""" % json.dumps(KHOA_DAY_DU)


def _mo(trang):
    # Page.enable bắt buộc trước addScriptToEvaluateOnNewDocument (Chromium mới)
    trang.cdp.goi("Page.enable", {}, trang.s)
    trang.cdp.goi("Page.addScriptToEvaluateOnNewDocument",
                  {"source": STUB}, trang.s)
    trang.cdp.goi("Page.navigate", {"url": TRANG}, trang.s)
    trang.cho("!!document.querySelector('main.trang-tb h1')", mo_ta="trang dựng xong")
    # file:// làm ../../khung.css trỏ nhầm chỗ (triển khai thật /m/<id>/ thì đúng);
    # nạp khung.css thật để đo kích thước đúng như khi chạy qua lõi.
    trang.js(f"""(() => {{ const l = document.createElement("link");
      l.rel = "stylesheet"; l.href = {json.dumps(KHUNG_CSS)};
      document.head.appendChild(l); }})()""")
    trang.cho("getComputedStyle(document.getElementById('nut-tim')).minHeight === '44px'",
              mo_ta="khung.css đã nạp")


def test_bo_cuc_360px_cam_ung(trang):
    trang.khung(360, 640, dien_thoai=True)
    trang.cdp.goi("Emulation.setTouchEmulationEnabled", {"enabled": True}, trang.s)
    _mo(trang)
    assert "YouTube phát" in trang.chu("h1")
    assert trang.js("document.querySelectorAll('[data-mo-muc]').length") == 6
    # mặc định đóng cả 6
    assert trang.js("[...document.querySelectorAll('[data-mo-muc]')].filter(b => b.getAttribute('aria-expanded') === 'false').length") == 6
    # chạm mở từng mục (cuộn vào tầm nhìn trước khi tap; thử lại nếu trượt)
    for i in range(6):
        for lan in range(3):
            trang.js(f"document.querySelectorAll('[data-mo-muc]')[{i}].scrollIntoView({{block: 'center'}})")
            box = trang.js(f"(() => {{ const r = document.querySelectorAll('[data-mo-muc]')[{i}].getBoundingClientRect(); return [r.x + r.width/2, r.y + r.height/2]; }})()")
            trang.cdp.goi("Input.dispatchTouchEvent",
                          {"type": "touchStart", "touchPoints": [{"x": box[0], "y": box[1], "id": 1}]}, trang.s)
            trang.cdp.goi("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []}, trang.s)
            try:
                trang.cho(f"document.querySelectorAll('[data-mo-muc]')[{i}].getAttribute('aria-expanded') === 'true'",
                          han=2, mo_ta=f"mở mục {i}")
                break
            except AssertionError:
                continue
    assert trang.js("[...document.querySelectorAll('[data-mo-muc]')].filter(b => b.getAttribute('aria-expanded') === 'true').length") == 6
    # một cột, không tràn ngang
    assert trang.js("document.documentElement.scrollWidth <= 361"), "tràn ngang ở 360px"
    # nút ≥ 44px
    nho = trang.js("(() => [...document.querySelectorAll('button')].filter(b => b.offsetParent !== null && b.getBoundingClientRect().height < 44).map(b => (b.textContent||'').trim().slice(0,20)))()")
    assert nho == [], f"nút < 44px: {nho}"
    # chữ ≥ 12px
    chu_nho = trang.js("(() => [...document.querySelectorAll('body *')].filter(e => e.offsetParent !== null && e.children.length === 0 && parseFloat(getComputedStyle(e).fontSize) < 12).slice(0,5).map(e => (e.textContent||'').trim().slice(0,20)))()")
    assert chu_nho == [], f"chữ < 12px: {chu_nho}"


def test_tim_ket_qua_xss_hien_dang_chu(trang):
    _mo(trang)
    trang.bam("document.querySelector('[data-mo-muc=\"tim\"]')")
    trang.js("document.getElementById('o-tu-khoa').value = 'xss'")
    trang.bam("document.getElementById('nut-tim')")
    trang.cho("document.querySelectorAll('#ds-ket-qua > li').length === 2", mo_ta="2 kết quả")
    # payload XSS phải hiện dạng CHỮ, không tạo phần tử img
    assert trang.js("document.querySelectorAll('#ds-ket-qua img').length") == 0
    assert "<img src=x" in trang.js("document.getElementById('ds-ket-qua').innerText")
    # esc() trong trang
    assert trang.js("window.esc('<img src=x onerror=alert(1)>')") == "&lt;img src=x onerror=alert(1)&gt;"


def test_chep_token_chi_khi_bam_co_chu_dich(trang):
    _mo(trang)
    trang.bam("document.querySelector('[data-mo-muc=\"ket-noi\"]')")
    trang.cho("document.getElementById('o-url').value !== ''", mo_ta="URL đã nạp")
    assert trang.js("document.getElementById('o-url').value") == "http://192.0.2.10:8713"
    # mặc định che
    assert trang.js("document.getElementById('o-token').type") == "password"
    assert trang.js("window.__daChep.length") == 0
    # bấm chép token → clipboard nhận đầy đủ, ô không lưu lại
    trang.bam("document.getElementById('nut-chep-token')")
    trang.cho("window.__daChep.length === 1", mo_ta="đã chép")
    assert trang.js("window.__daChep[0]") == KHOA_DAY_DU
    assert trang.js("document.getElementById('o-token').value") == ""


def test_loi_409_bao_dung_chuyen(trang):
    _mo(trang)
    trang.js("window.__dung409 = true")
    trang.bam("document.querySelector('[data-mo-muc=\"dang-phat\"]')")
    trang.bam("document.getElementById('nut-dung')")
    trang.cho("!document.getElementById('loi-dang-phat').hidden", mo_ta="hiện lỗi 409")
    chu = trang.js("document.getElementById('loi-dang-phat').textContent")
    assert "Hệ thống" in chu and "Thiết bị" in chu and "Xác nhận" in chu, chu


def test_playlist_va_hang_doi(trang):
    _mo(trang)
    trang.bam("document.querySelector('[data-mo-muc=\"playlist\"]')")
    trang.cho("document.querySelectorAll('#ds-playlist > li').length === 1", mo_ta="1 playlist")
    assert "abcdef012345" in trang.js("document.getElementById('ds-playlist').innerText")
    # nhập mã sai khuôn → báo lỗi, không gọi
    trang.js("document.getElementById('o-ma-chia-se').value = 'sai'")
    trang.bam("document.getElementById('nut-nhap-ma')")
    trang.cho("!document.getElementById('loi-playlist').hidden", mo_ta="báo mã sai")
    # nhập đúng
    trang.js("document.getElementById('o-ma-chia-se').value = 'TTPL1.test'")
    trang.bam("document.getElementById('nut-nhap-ma')")
    trang.cho("!document.getElementById('bao-playlist').hidden", mo_ta="báo nhập xong")
    # hàng đợi từ trạng thái
    trang.bam("document.querySelector('[data-mo-muc=\"hang-doi\"]')")
    trang.bam("document.querySelector('[data-mo-muc=\"dang-phat\"]')")
    trang.cho("document.querySelectorAll('#ds-hang-doi > li').length === 2", mo_ta="2 bài trong hàng đợi")
