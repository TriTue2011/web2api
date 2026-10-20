// Test dịch vụ dich-vu/zalo-ca-nhan bằng adapter GIẢ (ZALO_ADAPTER=gia).
// Chạy: npm test  (node --test test/). Không chạm mạng ngoài, không Zalo thật.
//
// Hợp đồng kiểm ở đây:
// - Mọi route (trừ /qr/<token>) cần Bearer: thiếu/sai → 401 {ma: "sai_khoa"}.
// - Thiếu ZALO_KHOA hoặc <24 ký tự → docCauHinh ném (fail-fast).
// - ZALO_HOST không loopback → docCauHinh ném.
// - QR: POST dang-nhap-qr → url_qr; GET trang QR 200 + CSP nghiêm; URL chỉ dùng
//   MỘT lần (lần 2 không cookie → 404); QR hết token thì 404.
// - /gui-tin: chưa đăng nhập → 409 chua_dang_nhap; khi đăng nhập xong → 200;
//   hai lần gửi liên tiếp quá nhanh → 429 gui_qua_nhanh; tep kiểu lạ → 400.
// - /tin-moi: chỉ tin của NGƯỜI KHÁC (tu_gui bị bỏ); lọc theo seq.
// - /suc-khoe không chứa bí mật (khoa/cookie).

import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

process.env.ZALO_ADAPTER = 'gia';
process.env.ZALO_DATA = fs.mkdtempSync(path.join(os.tmpdir(), 'zalo-test-'));

const { taoDichVu, docCauHinh } = await import('../server.js');

const KHOA = 'khoa-gia-0123456789abcdef-ghi-ro-la-gia';
let dv;
let cong;

before(async () => {
  dv = await taoDichVu({
    cauHinh: { cong: 0, may: '127.0.0.1', khoa: KHOA, thuMucDuLieu: process.env.ZALO_DATA },
  });
  cong = await dv.nghe();
});

after(async () => {
  await dv.dong();
});

async function goi(phuongThuc, duong, { than = null, khoa = KHOA } = {}) {
  const tuy = { method: phuongThuc, headers: {} };
  if (khoa !== null) tuy.headers['Authorization'] = `Bearer ${khoa}`;
  if (than !== null) {
    tuy.headers['Content-Type'] = 'application/json';
    tuy.body = JSON.stringify(than);
  }
  return fetch(`http://127.0.0.1:${cong}${duong}`, tuy);
}

function phaiThieuKhoaVaNgan() {
  const cu = process.env.ZALO_KHOA;
  delete process.env.ZALO_KHOA;
  assert.throws(() => docCauHinh(), /ZALO_KHOA/);
  process.env.ZALO_KHOA = 'ngan';
  assert.throws(() => docCauHinh(), /ZALO_KHOA/);
  if (cu === undefined) delete process.env.ZALO_KHOA;
  else process.env.ZALO_KHOA = cu;
}

function phaiHostLoopback() {
  const cuK = process.env.ZALO_KHOA;
  const cuH = process.env.ZALO_HOST;
  process.env.ZALO_KHOA = KHOA;
  process.env.ZALO_HOST = '192.0.2.10';
  assert.throws(() => docCauHinh(), /loopback/);
  process.env.ZALO_HOST = '127.0.0.1';
  assert.doesNotThrow(() => docCauHinh());
  if (cuK === undefined) delete process.env.ZALO_KHOA; else process.env.ZALO_KHOA = cuK;
  if (cuH === undefined) delete process.env.ZALO_HOST; else process.env.ZALO_HOST = cuH;
}

test('thieu khoa hoac khoa ngan → tu choi doc cau hinh', phaiThieuKhoaVaNgan);
test('host khong loopback → tu choi', phaiHostLoopback);

test('thieu/sai Bearer → 401', async () => {
  const r1 = await goi('GET', '/suc-khoe', { khoa: null });
  assert.equal(r1.status, 401);
  const r2 = await goi('GET', '/suc-khoe', { khoa: 'khoa-sai-0123456789abcdef-ghi' });
  assert.equal(r2.status, 401);
  assert.equal((await r2.json()).ma, 'sai_khoa');
  const r3 = await goi('GET', '/khong-co-route-nay');
  assert.equal(r3.status, 404);
});

test('suc-khoe khong chua bi mat', async () => {
  const r = await goi('GET', '/suc-khoe');
  assert.equal(r.status, 200);
  const than = await r.json();
  assert.equal(than.ok, true);
  assert.equal(than.dang_nhap, false);
  const chuoi = JSON.stringify(than);
  assert.ok(!chuoi.includes(KHOA), 'suc-khoe khong duoc chua khoa');
  assert.ok(!/cookie|imei|useragent/i.test(chuoi));
});

test('gui tin truoc dang nhap → 409 chua_dang_nhap', async () => {
  const r = await goi('POST', '/gui-tin', { than: { thread_id: '1111111111', van_ban: 'chao' } });
  assert.equal(r.status, 409);
  assert.equal((await r.json()).ma, 'chua_dang_nhap');
});

test('QR: URL chi dung mot lan, co CSP, het token → 404', async () => {
  const r = await goi('POST', '/tai-khoan/dang-nhap-qr');
  assert.equal(r.status, 200);
  const than = await r.json();
  assert.equal(than.ok, true);
  assert.ok(than.url_qr.startsWith('/qr/'));

  const trang = await fetch(`http://127.0.0.1:${cong}${than.url_qr}`);
  assert.equal(trang.status, 200);
  assert.match(trang.headers.get('content-security-policy') || '', /default-src 'none'/);
  const html = await trang.text();
  assert.match(html, /data:image\/png;base64,/);
  assert.ok(!html.includes('<script'), 'trang QR khong duoc co script');

  // Lần 2 không cookie → 404 (URL đã vô hiệu)
  const lan2 = await fetch(`http://127.0.0.1:${cong}${than.url_qr}`);
  assert.equal(lan2.status, 404);
  const maLa = await fetch(`http://127.0.0.1:${cong}/qr/token-khong-ton-tai`);
  assert.equal(maLa.status, 404);
});

test('sau dang nhap: hop-thoai, gui tin, han toc do', async () => {
  dv.client._gia.dangNhapXong();
  const r = await goi('GET', '/hop-thoai');
  assert.equal(r.status, 200);
  const than = await r.json();
  assert.equal(than.hop_thoai.length, 2);

  // Tệp kiểu lạ → 400 (kiểm trước khi gửi thật, để không tốn ngân sách hạn tốc độ)
  const g3 = await goi('POST', '/gui-tin', {
    than: {
      thread_id: '1111111111',
      tep: { ten: 'x.exe', kieu: 'application/x-msdownload', du_lieu_base64: Buffer.from('x').toString('base64') },
    },
  });
  assert.equal(g3.status, 400);

  const g1 = await goi('POST', '/gui-tin', { than: { thread_id: '1111111111', van_ban: 'tin thu' } });
  assert.equal(g1.status, 200);
  const g2 = await goi('POST', '/gui-tin', { than: { thread_id: '1111111111', van_ban: 'tin 2' } });
  assert.equal(g2.status, 429);
  assert.equal((await g2.json()).ma, 'gui_qua_nhanh');
});

test('tin-moi: bo tin tu gui, loc theo seq', async () => {
  dv.client._gia.nhanTin({
    thread_id: '1111111111', loai: 0, nguoi_gui: '1111111111', ten_tu_khai: 'Nguoi A',
    van_ban: 'chao tu nguoi la', dinh_kem_loai: null, tu_gui: false,
    luc: new Date().toISOString(),
  });
  dv.client._gia.nhanTin({
    thread_id: '1111111111', loai: 0, nguoi_gui: '0000000001', ten_tu_khai: 'Toi',
    van_ban: 'tin toi gui', dinh_kem_loai: null, tu_gui: true,
    luc: new Date().toISOString(),
  });
  const r = await goi('GET', '/tin-moi?tu=0');
  assert.equal(r.status, 200);
  const than = await r.json();
  assert.equal(than.tin.length, 1);
  assert.equal(than.tin[0].van_ban, 'chao tu nguoi la');
  const r2 = await goi('GET', `/tin-moi?tu=${than.tu_moi}`);
  assert.equal((await r2.json()).tin.length, 0);
});

test('dang-xuat xoa dang nhap', async () => {
  const r = await goi('POST', '/dang-xuat');
  assert.equal(r.status, 200);
  const sk = await (await goi('GET', '/suc-khoe')).json();
  assert.equal(sk.dang_nhap, false);
});
