// Test NV172-SUA Z1 (khoảng cách gửi) + Z4 (trang QR/CSP/host) phía dịch vụ.
// Chạy: npm test (node --test test/).
//
// File này đặt khoảng cách gửi = 60 s, trần phút = 20 (đọc lúc nạp server.js)
// nên kiểm được nhánh «≥ N giây giữa hai lần» khi gửi song song — file
// han-gui.test.js kiểm nhánh trần phút.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

process.env.ZALO_GUI_CACH_GI_MS = '60000';
process.env.ZALO_GUI_TOI_DA_PHUT = '20';
process.env.ZALO_KHOA = 'khoa-gia-0123456789abcdef-ghi-ro-la-gia';

const { taoDichVu, docCauHinh } = await import('../server.js');
const { LoiZalo } = await import('../zalo-client.js');

const KHOA = process.env.ZALO_KHOA;
const ANH_PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

function taoClientGia({ anhBase64 = ANH_PNG, treMs = 0, dangNhapBanDau = true } = {}) {
  let hamTin = null;
  let daDangNhap = dangNhapBanDau;
  const daGui = [];
  return {
    che_do: 'gia-test',
    sanSang: () => true,
    dangNhap: () => daDangNhap,
    layHoSo: () => ({ own_id: '0000000001', ten: 'Tai khoan gia' }),
    dangNhapQR: async () => ({ anh_base64: anhBase64 }),
    khoiPhucTuLuuTru: async () => false,
    layHopThoai: async () => [{ thread_id: '1111111111', loai: 0, ten: 'Nguoi A' }],
    guiTin: async (tin) => {
      if (treMs) await new Promise((r) => setTimeout(r, treMs));
      daGui.push(tin);
    },
    onTin: (h) => { hamTin = h; },
    dangXuat: async () => {},
    _gia: { daGui, nhanTin: (t) => { if (hamTin) hamTin(t); } },
  };
}

async function moDichVu(client) {
  const thuMuc = fs.mkdtempSync(path.join(os.tmpdir(), 'zalo-z4-'));
  const dv = await taoDichVu({
    cauHinh: { cong: 0, may: '127.0.0.1', khoa: KHOA, thuMucDuLieu: thuMuc },
    client,
  });
  const cong = await dv.nghe();
  return { dv, cong };
}

test('Z1: 10 lượt gửi song song với khoảng cách 60s — chỉ 1 qua, 9 bị 429', async () => {
  const client = taoClientGia({ treMs: 200 });
  const { dv, cong } = await moDichVu(client);
  try {
    const gui = () => fetch(`http://127.0.0.1:${cong}/gui-tin`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${KHOA}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ thread_id: '1111111111', van_ban: 'chao' }),
    }).then((r) => r.status);
    const ma = await Promise.all(Array.from({ length: 10 }, gui));
    assert.equal(ma.filter((x) => x === 200).length, 1, `nhận: ${JSON.stringify(ma)}`);
    assert.equal(ma.filter((x) => x === 429).length, 9);
    assert.equal(client._gia.daGui.length, 1);
    // Gửi tiếp ngay sau đó (tuần tự) cũng bị khoảng cách chặn
    assert.equal(await gui(), 429);
  } finally {
    await dv.dong();
  }
});

test('Z4: trang QR có CSP form-action/base-uri trong cả header và meta', async () => {
  const { dv, cong } = await moDichVu(taoClientGia({ dangNhapBanDau: false }));
  try {
    const r = await fetch(`http://127.0.0.1:${cong}/tai-khoan/dang-nhap-qr`, {
      method: 'POST', headers: { Authorization: `Bearer ${KHOA}` },
    });
    assert.equal(r.status, 200);
    const { url_qr } = await r.json();
    const trang = await fetch(`http://127.0.0.1:${cong}${url_qr}`);
    assert.equal(trang.status, 200);
    const csp = trang.headers.get('content-security-policy') || '';
    assert.match(csp, /form-action 'none'/, 'header CSP phải có form-action');
    assert.match(csp, /base-uri 'none'/, 'header CSP phải có base-uri');
    const html = await trang.text();
    assert.match(html, /form-action 'none'/, 'thẻ meta CSP phải có form-action');
    assert.match(html, /base-uri 'none'/, 'thẻ meta CSP phải có base-uri');
    assert.ok(!html.includes('<form'), 'trang QR không được chứa form');
  } finally {
    await dv.dong();
  }
});

test('Z4: anh_base64 không đúng khuôn base64 PNG → từ chối tạo phiên QR', async () => {
  const doc = '" ><form action="https://evil.example/x">';
  const { dv, cong } = await moDichVu(taoClientGia({ anhBase64: doc, dangNhapBanDau: false }));
  try {
    const r = await fetch(`http://127.0.0.1:${cong}/tai-khoan/dang-nhap-qr`, {
      method: 'POST', headers: { Authorization: `Bearer ${KHOA}` },
    });
    assert.equal(r.status, 500, 'ảnh QR sai khuôn phải bị từ chối, không dựng trang');
    assert.equal((await r.json()).ma, 'anh_qr_sai');
  } finally {
    await dv.dong();
  }
});

test('Z4: docCauHinh nhận ZALO_HOST=0.0.0.0 (nghe mọi interface TRONG container), vẫn từ chối IP lạ', () => {
  const cu = process.env.ZALO_HOST;
  try {
    process.env.ZALO_HOST = '0.0.0.0';
    assert.doesNotThrow(() => docCauHinh());
    process.env.ZALO_HOST = '192.0.2.10';
    assert.throws(() => docCauHinh(), /ZALO_HOST/);
  } finally {
    if (cu === undefined) delete process.env.ZALO_HOST;
    else process.env.ZALO_HOST = cu;
  }
});

// Giữ tham chiếu để tránh cây phụ thuộc báo thừa (LoiZalo dùng ở client giả khác).
void LoiZalo;
