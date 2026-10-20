// Test NV172-SUA Z1 + Z2 phía dịch vụ — hạn tốc độ gửi NGUYÊN TỬ và nhãn
// phiên (epoch) của /tin-moi. Chạy: npm test (node --test test/).
//
// Bối cảnh soát độc lập C2 (Z1): bản cũ kiểm hạn ở đầu handler rồi mới
// `await docThan` + `await zalo.guiTin`, gửi XONG mới ghi mốc → 40 yêu cầu
// song song (guiTin có độ trễ mạng) đều thấy mốc cũ và cùng qua (40/40);
// đường gửi LỖI không bao giờ ghi mốc nên gửi lỗi liên tục không bị chặn.
// Vá theo nguyên tắc: kiểm + giữ chỗ trong CÙNG một đoạn đồng bộ ngay trước
// lời gọi Zalo (Node đơn luồng — không await ở giữa thì không ai chen được);
// mọi lần ĐÃ chạm Zalo đều bị đếm (kể cả lỗi — Zalo đã thấy nỗ lực gửi, và
// spam-lỗi là đúng mẫu cần chặn); lần bị từ chối TRƯỚC khi chạm Zalo (sai
// tham số, chưa đăng nhập) không tốn chỗ.
//
// File này đặt trần phút = 3, khoảng cách = 0 (đọc lúc nạp server.js).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

process.env.ZALO_GUI_CACH_GI_MS = '0';
process.env.ZALO_GUI_TOI_DA_PHUT = '3';

const { taoDichVu } = await import('../server.js');
const { LoiZalo } = await import('../zalo-client.js');

const KHOA = 'khoa-gia-0123456789abcdef-ghi-ro-la-gia';
const ANH_PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

function taoClientGia({ treMs = 0, loiGui = null, dangNhap = true } = {}) {
  let daDangNhap = dangNhap;
  let hamTin = null;
  const daGui = [];
  return {
    che_do: 'gia-test',
    sanSang: () => true,
    dangNhap: () => daDangNhap,
    layHoSo: () => (daDangNhap ? { own_id: '0000000001', ten: 'Tai khoan gia' } : null),
    dangNhapQR: async () => ({ anh_base64: ANH_PNG }),
    khoiPhucTuLuuTru: async () => false,
    layHopThoai: async () => {
      if (!daDangNhap) throw new LoiZalo('chua_dang_nhap', 'Chưa đăng nhập');
      return [{ thread_id: '1111111111', loai: 0, ten: 'Nguoi A' }];
    },
    guiTin: async (tin) => {
      if (!daDangNhap) throw new LoiZalo('chua_dang_nhap', 'Chưa đăng nhập');
      if (treMs) await new Promise((r) => setTimeout(r, treMs));
      if (loiGui) throw loiGui;
      daGui.push(tin);
    },
    onTin: (h) => { hamTin = h; },
    dangXuat: async () => { daDangNhap = false; },
    _gia: {
      daGui,
      dangNhapXong: () => { daDangNhap = true; },
      nhanTin: (t) => { if (hamTin) hamTin(t); },
    },
  };
}

async function moDichVu(client) {
  const thuMuc = fs.mkdtempSync(path.join(os.tmpdir(), 'zalo-z1-'));
  const dv = await taoDichVu({
    cauHinh: { cong: 0, may: '127.0.0.1', khoa: KHOA, thuMucDuLieu: thuMuc },
    client,
  });
  const cong = await dv.nghe();
  return { dv, cong };
}

async function guiTin(cong, van_ban = 'chao') {
  const r = await fetch(`http://127.0.0.1:${cong}/gui-tin`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${KHOA}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ thread_id: '1111111111', van_ban }),
  });
  return r.status;
}

test('Z1: 40 lượt gửi song song (guiTin trễ 300ms) chỉ 3 qua trần phút, 37 bị 429', async () => {
  const client = taoClientGia({ treMs: 300 });
  const { dv, cong } = await moDichVu(client);
  try {
    const ma = await Promise.all(Array.from({ length: 40 }, () => guiTin(cong)));
    const dem = (m) => ma.filter((x) => x === m).length;
    assert.equal(dem(200), 3, `phải đúng 3 lượt qua (trần phút), nhận: ${JSON.stringify(ma)}`);
    assert.equal(dem(429), 37);
    assert.equal(client._gia.daGui.length, 3, 'Zalo chỉ được nhận đúng 3 tin');
  } finally {
    await dv.dong();
  }
});

test('Z1: gửi LỖI vẫn bị đếm — hết trần thì gặp 429 thay vì lỗi gửi', async () => {
  const client = taoClientGia({ loiGui: new LoiZalo('loi_gui_tam', 'Zalo từ chối tạm thời') });
  const { dv, cong } = await moDichVu(client);
  try {
    assert.equal(await guiTin(cong), 400);
    assert.equal(await guiTin(cong), 400);
    assert.equal(await guiTin(cong), 400);
    assert.equal(await guiTin(cong), 429, 'lần 4 phải bị hạn tốc độ chặn (gửi lỗi cũng đếm)');
  } finally {
    await dv.dong();
  }
});

test('Z1: chưa đăng nhập → 409 và KHÔNG tốn chỗ (chưa chạm Zalo)', async () => {
  const client = taoClientGia({ dangNhap: false });
  const { dv, cong } = await moDichVu(client);
  try {
    assert.equal(await guiTin(cong), 409);
    client._gia.dangNhapXong();
    assert.equal(await guiTin(cong), 200, 'sau đăng nhập phải gửi được ngay (409 không tốn chỗ)');
  } finally {
    await dv.dong();
  }
});

test('Z2: /tin-moi trả kèm nhãn phiên (phien) — ổn định trong một thể hiện, khác giữa hai thể hiện', async () => {
  const a = await moDichVu(taoClientGia());
  const b = await moDichVu(taoClientGia());
  try {
    const lay = async (cong) => {
      const r = await fetch(`http://127.0.0.1:${cong}/tin-moi?tu=0`, {
        headers: { Authorization: `Bearer ${KHOA}` },
      });
      return r.json();
    };
    const a1 = await lay(a.cong);
    const a2 = await lay(a.cong);
    const b1 = await lay(b.cong);
    assert.equal(typeof a1.phien, 'string');
    assert.ok(a1.phien.length >= 8, 'nhãn phiên phải đủ dài để không đụng nhau');
    assert.equal(a1.phien, a2.phien, 'nhãn phiên không đổi trong một thể hiện');
    assert.notEqual(a1.phien, b1.phien, 'hai lần khởi động phải có nhãn phiên khác nhau');
  } finally {
    await a.dv.dong();
    await b.dv.dong();
  }
});
