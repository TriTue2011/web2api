"""Chạy JS thật của tab bằng node; HTTP và DOM tối thiểu được giả tại biên.

Theo khuôn node/vm trong test_web2api_markdown.py, không gọi mạng.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="cần node")

KHUNG = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('modules/youtube/web/index.html', 'utf8');
const js = fs.readFileSync('modules/youtube/web/trang.js', 'utf8');
function taoNut() {
  return {
    hidden: false, textContent: '', value: '', style: {}, children: [], attrs: {}, handlers: {},
    classList: { add() {}, toggle() {} },
    setAttribute(k, v) { this.attrs[k] = v; },
    toggleAttribute(k, co) { if (co) this.attrs[k] = ''; else delete this.attrs[k]; },
    getAttribute(k) { return this.attrs[k]; },
    addEventListener(k, v) { this.handlers[k] = v; },
    append(...con) { this.children.push(...con); },
    replaceChildren(...con) { this.children = con; },
  };
}
const nodes = {};
for (const m of html.matchAll(/<[a-z][\w:-]*\b([^>]*\bid="([^"]+)"[^>]*)>/gi)) {
  const nut = taoNut();
  nut.hidden = /\bhidden\b/.test(m[1]);
  nodes[m[2]] = nut;
}
let ready;
const intervals = [];
const requests = [];
let traLoi = () => ({ status: 200, du: { ok: true } });
const ctx = vm.createContext({
  document: {
    getElementById: (id) => nodes[id] || null,
    createElement: taoNut,
    createElementNS: taoNut,
    querySelectorAll: () => [],
    addEventListener: (ten, ham) => { if (ten === 'DOMContentLoaded') ready = ham; },
  },
  window: { confirm: () => true },
  location: { pathname: '/web/modules/youtube/index.html', search: '', replace() {} },
  setInterval: (ham, ms) => { intervals.push({ ham, ms }); return intervals.length; },
  fetch: async (url, opts) => {
    const body = opts.body === undefined ? null : JSON.parse(opts.body);
    requests.push({ url, body, opts });
    const { status, du } = await traLoi(url, body);
    return { status, ok: status >= 200 && status < 300, json: async () => du };
  },
});
vm.runInContext(js, ctx);
const run = (ma) => vm.runInContext(ma, ctx);
run("thucTheXem = 'youtube.xem'; thucTheDieuKhien = 'youtube.dieu_khien'; csrfToken = 'csrf-gia'; ruiRoDieuKhien = 'ghi';");
const tick = () => new Promise((resolve) => setImmediate(resolve));
const canXacNhan = { status: 409, du: { error: { code: 'can_xac_nhan', message: 'Cần xác nhận' } } };
"""


def chay(ma: str) -> None:
    kq = subprocess.run(
        ["node", "-e", KHUNG
         + "\nconst han = setTimeout(() => { console.error('Promise chưa kết thúc'); process.exitCode = 1; }, 3000);"
         + "\n(async () => {\n" + ma
         + "\n})().then(() => clearTimeout(han), (e) => { clearTimeout(han); console.error(e); process.exitCode = 1; });"],
        capture_output=True, text=True, timeout=15,
    )
    assert kq.returncode == 0, kq.stdout + kq.stderr


def test_lenh_ghi_nguy_hiem_chi_gui_sau_khi_dong_y():
    chay(r"""
run("thucTheXem = ''; thucTheDieuKhien = '';");
traLoi = (url) => url.includes('?module=')
  ? { status: 200, du: { thuc_the: [
      { id: 'youtube.xem', rui_ro: 'doc' },
      { id: 'youtube.dieu_khien', rui_ro: 'nguy_hiem' },
    ] } }
  : { status: 200, du: { ok: true } };
const pending = run("hanhDong('dung', {phien: 'gia'})");
await tick();
assert.equal(nodes['hoi-xac-nhan'].hidden, false);
assert.equal(requests.filter((r) => r.body).length, 0);
nodes['nut-xac-nhan'].onclick();
assert.equal((await pending).ok, true);
const gui = requests.filter((r) => r.body);
assert.equal(gui.length, 1);
assert.equal(gui[0].body.xac_nhan, true);
assert.equal(gui[0].opts.headers['X-CSRF-Token'], 'csrf-gia');
assert.equal(gui[0].opts.credentials, 'same-origin');
""")


@pytest.mark.parametrize("ham", ["hanhDong", "hanhDongDoc"])
def test_409_chi_gui_lai_mot_lan_va_khong_lap_xac_nhan(ham):
    chay(r"""
traLoi = () => canXacNhan;
const pending = run("HAM('tim', {})");
const loi = assert.rejects(pending, /Cần xác nhận/);
await tick();
assert.equal(requests.length, 1);
nodes['nut-xac-nhan'].onclick();
await loi;
assert.equal(requests.length, 2);
assert.deepEqual(requests.map((r) => r.body.xac_nhan), [false, true]);
assert.equal(nodes['hoi-xac-nhan'].hidden, true);
assert.equal(nodes['nut-xac-nhan'].onclick, null);
""".replace("HAM", ham))


def test_xac_nhan_dong_thoi_khong_treo_hoac_dung_chung_dong_y():
    chay(r"""
run("ruiRoDieuKhien = 'nguy_hiem';");
const a = run("hanhDong('phat', {})");
const b = run("hanhDong('dung', {})");
await tick();
assert.match(nodes['hoi-xac-nhan-chu'].textContent, /«phat»/);
assert.equal(requests.length, 0);
nodes['nut-xac-nhan'].onclick();
await tick();
assert.match(nodes['hoi-xac-nhan-chu'].textContent, /«dung»/);
assert.deepEqual(requests.map((r) => r.body.hanh_dong), ['phat']);
nodes['nut-thoi-xac-nhan'].onclick();
assert.equal((await a).ok, true);
assert.equal((await b).daHuy, true);
assert.equal(requests.length, 1);
""")


def test_polling_khong_chiem_hop_xac_nhan_va_giu_trang_thai_khi_huy():
    chay(r"""
run("ruiRoDieuKhien = 'nguy_hiem'; phienChon = 'giu-lai'; phienHienTai = {session_id: 'giu-lai'};");
traLoi = (url) => url.includes('youtube.xem') ? canXacNhan : { status: 200, du: { ok: true } };
const pending = run("hanhDong('dung', {})");
await tick();
const chu = nodes['hoi-xac-nhan-chu'].textContent;
await run('taiTrangThai(true)');
assert.equal(nodes['hoi-xac-nhan-chu'].textContent, chu);
assert.equal(run('phienChon'), 'giu-lai');
nodes['nut-thoi-xac-nhan'].onclick();
assert.equal((await pending).daHuy, true);
await tick();
assert.equal(nodes['hoi-xac-nhan'].hidden, true);
assert.equal(requests.length, 2);
""")


def test_huy_doc_nhom_khong_xoa_phien_dang_chon():
    chay(r"""
run("phienChon = 'giu-lai'; phienHienTai = {session_id: 'giu-lai'};");
traLoi = (url, body) => body.hanh_dong === 'cac_phien'
  ? canXacNhan : { status: 200, du: { ok: true, phien: null } };
const pending = run('taiTrangThai()');
await tick();
nodes['nut-thoi-xac-nhan'].onclick();
await pending;
assert.equal(run('phienChon'), 'giu-lai');
assert.equal(run('phienHienTai.session_id'), 'giu-lai');
""")


@pytest.mark.parametrize("hanh_dong", ["nhap", "tao", "dung"])
def test_huy_lenh_ghi_khong_xoa_input_hay_bao_thanh_cong(hanh_dong):
    chay(r"""
ready();
await tick();
requests.length = 0;
run("ruiRoDieuKhien = 'nguy_hiem'; phienChon = 'giu-lai';");
nodes['o-nhap-text'].value = 'TTPL1.gia';
nodes['o-nhap-ten'].value = 'Tên nhập';
nodes['o-tao-ten'].value = 'Tên mới';
const pending = nodes['nut-HANH_DONG'].handlers.click();
await tick();
nodes['nut-thoi-xac-nhan'].onclick();
await pending;
assert.equal(requests.length, 0);
assert.equal(nodes['o-nhap-text'].value, 'TTPL1.gia');
assert.equal(nodes['o-nhap-ten'].value, 'Tên nhập');
assert.equal(nodes['o-tao-ten'].value, 'Tên mới');
assert.equal(nodes['bao-playlist'].hidden, true);
assert.equal(run('phienChon'), 'giu-lai');
const poll = intervals.find((i) => i.ms === 20000);
assert.ok(poll);
traLoi = () => canXacNhan;
await poll.ham();
await tick();
assert.equal(nodes['hoi-xac-nhan'].hidden, true);
""".replace("HANH_DONG", hanh_dong))


@pytest.mark.parametrize("ma, code", [(403, "step_up_required"), (409, "queue_end")])
def test_loi_khac_khong_tu_xac_nhan_hoac_gui_lai(ma, code):
    chay(r"""
const message = '<img src=x onerror=alert(1)>';
traLoi = () => ({ status: MA, du: { error: { code: 'CODE', message } } });
await assert.rejects(run("hanhDong('dung', {})"), (e) => e.message === message);
assert.equal(requests.length, 1);
assert.equal(nodes['hoi-xac-nhan'].hidden, true);
run('hienLoi("loi-chung", ' + JSON.stringify(message) + ')');
assert.equal(nodes['loi-chung'].textContent, message);
assert.equal(nodes['loi-chung'].children.length, 0);
""".replace("MA", str(ma)).replace("CODE", code))


def test_tim_kiem_co_tieu_de_that_va_giu_ket_qua_khi_huy():
    chay(r"""
nodes['o-tu-khoa'].value = 'Bài giả';
traLoi = () => ({ status: 200, du: { ok: true, ket_qua: [
  { id: 'gia-1', source: 'youtube', title: '<script>khong_chay()</script>' },
] } });
await run('timKiem()');
assert.equal(nodes['tieu-de-ket-qua'].textContent, 'Kết quả (1 mục)');
assert.equal(nodes['ds-ket-qua'].children.length, 1);
assert.equal(nodes['loi-tim'].textContent, '');
const cu = nodes['ds-ket-qua'].children[0];
traLoi = () => canXacNhan;
const pending = run('timKiem()');
await tick();
nodes['nut-thoi-xac-nhan'].onclick();
await pending;
assert.equal(nodes['ds-ket-qua'].children[0], cu);
assert.equal(nodes['tieu-de-ket-qua'].textContent, 'Kết quả (1 mục)');
""")


def test_bieu_tuong_phat_va_tam_dung_dung_trang_thai():
    chay(r"""
run("phienHienTai = {state: 'playing', item: {source: 'zing', title: 'Giả'}}; veDangPhat();");
assert.equal('hidden' in nodes['nut-phat-play'].attrs, true);
assert.equal('hidden' in nodes['nut-phat-pause'].attrs, false);
assert.equal(nodes['nut-phat-tam-dung'].title, 'Tạm dừng');
run('phienHienTai = null; veDangPhat();');
assert.equal('hidden' in nodes['nut-phat-play'].attrs, false);
assert.equal('hidden' in nodes['nut-phat-pause'].attrs, true);
assert.equal(nodes['nut-phat-tam-dung'].attrs['aria-label'], 'Phát');
assert.equal(nodes['nut-phat-tam-dung'].title, 'Phát');
""")
