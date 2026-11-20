"""Tab thật trong Chrome với HTTP giả; không tải trang hay tài nguyên ngoài.

Dùng CDP và fixture sẵn có trong ui_chung, không cần máy chủ hoặc HA thật.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ui_chung import CO_CHROME, trang, trinh_duyet  # noqa: F401

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")


@pytest.fixture
def trinh_phat(trang):
    trang.cdp.goi("Network.enable", {}, trang.s)
    trang.cdp.goi("Network.setBlockedURLs", {"urls": ["*"]}, trang.s)
    html = Path("modules/youtube/web/index.html").read_text(encoding="utf-8")
    css = Path("web/khung.css").read_text(encoding="utf-8")
    js = Path("modules/youtube/web/trang.js").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="../../khung.css">', "<style>" + css + "</style>")
    html = html.replace('<script src="trang.js" defer></script>', "")
    frame = trang.cdp.goi("Page.getFrameTree", {}, trang.s)["frameTree"]["frame"]["id"]
    trang.cdp.goi("Page.setDocumentContent", {"frameId": frame, "html": html}, trang.s)
    trang.js(r"""
window.__calls = [];
window.__errors = [];
window.__mode = 'ok';
window.addEventListener('error', (e) => __errors.push(e.message));
window.addEventListener('unhandledrejection', (e) => __errors.push(String(e.reason)));
window.fetch = async (url, opts = {}) => {
  const body = opts.body ? JSON.parse(opts.body) : null;
  __calls.push({url, body});
  let status = 200, du = {ok: true};
  if (url.endsWith('api/toi')) du = {csrf: 'csrf-gia'};
  else if (url.includes('?module=youtube')) du = {thuc_the: [
    {id: 'youtube.xem', rui_ro: 'doc'}, {id: 'youtube.dieu_khien', rui_ro: 'ghi'},
  ]};
  else if (url.includes('?module=home-assistant')) du = {thuc_the: []};
  else if (__mode === 'confirm' && !body.xac_nhan) {
    status = 409; du = {error: {code: 'can_xac_nhan', message: 'Cần xác nhận'}};
  } else if (__mode === 'forbidden') {
    status = 403; du = {error: {code: 'step_up_required', message: 'Cần xác nhận lại phiên'}};
  } else if (body.hanh_dong === 'tim') du = {ket_qua: [
    {id: 'gia-1', source: 'youtube', title: '<img src=x onerror=alert(1)>'},
    {id: 'gia-2', source: 'youtube', title: 'Bài giả hai'},
  ]};
  return {status, ok: status >= 200 && status < 300, json: async () => du};
};
""")
    trang.js(js + '\ndocument.dispatchEvent(new Event("DOMContentLoaded"));')
    trang.cho("__calls.some((r) => r.body && r.body.hanh_dong === 'danh_sach_playlist')")
    return trang


@pytest.mark.parametrize("rong", [375, 1280])
def test_hien_thi_va_tuong_tac_trinh_phat_that(trinh_phat, rong):
    t = trinh_phat
    t.khung(rong, 900, dien_thoai=rong < 768)
    assert t.js("document.documentElement.scrollWidth <= innerWidth"), "Trang bị tràn ngang"
    assert t.js("document.querySelector('#nut-tim').getBoundingClientRect().width") == 40
    assert t.js("document.querySelector('#nut-phat-tam-dung').getBoundingClientRect().width") == 56
    assert t.js("[...document.querySelectorAll('button.nut-tron, button.nut-vuong-do')].every((b) => b.getAttribute('aria-label'))")
    assert t.js("document.querySelector('[aria-labelledby=\"tieu-de-dang-phat\"]').contains(document.querySelector('#ds-nhom'))")

    t.js("phienHienTai = {state: 'playing', item: {source: 'zing', title: 'Bài giả'}}; veDangPhat();")
    assert t.js("getComputedStyle(document.getElementById('nut-phat-play')).display") == "none"
    assert t.js("getComputedStyle(document.getElementById('nut-phat-pause')).display") != "none"
    t.js("phienHienTai = null; veDangPhat();")
    assert t.js("getComputedStyle(document.getElementById('nut-phat-pause')).display") == "none"

    t.js("document.getElementById('o-tu-khoa').value = 'Bài giả';")
    t.bam("document.getElementById('nut-tim')")
    t.cho("document.getElementById('tieu-de-ket-qua').textContent === 'Kết quả (2 mục)'")
    assert t.js("document.getElementById('ds-ket-qua').querySelectorAll('img').length") == 0
    assert "<img src=x" in t.chu("#ds-ket-qua")

    t.js("__mode = 'confirm'; window.__pending = hanhDong('dung', {}); void 0;")
    t.cho("!document.getElementById('hoi-xac-nhan').hidden")
    t.bam("document.getElementById('nut-xac-nhan')")
    assert t.js("__pending.then((du) => du.ok)")
    assert t.js("__calls.filter((r) => r.body && r.body.hanh_dong === 'dung').length") == 2
    assert t.js("__calls.filter((r) => r.body && r.body.hanh_dong === 'dung')[1].body.xac_nhan")

    t.js("__mode = 'forbidden';")
    t.bam("document.getElementById('nut-tim')")
    t.cho("!document.getElementById('loi-tim').hidden")
    assert t.chu("#loi-tim") == "Cần xác nhận lại phiên"
    assert t.js("document.getElementById('hoi-xac-nhan').hidden")
    assert t.js("__errors") == []
