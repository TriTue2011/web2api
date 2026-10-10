// Điều khiển Chrome qua DevTools Protocol cho test dc-lite. Hai chế độ:
//
// (A) GÕ PHÍM: gõ phím THẬT vào một ô nhập (Input.insertText là sự kiện
// tin cậy, giống người gõ), để kiểm dc-lite không làm mất focus/con trỏ khi onInput gọi
// setState theo từng phím. Gõ vào GIỮA chuỗi: nếu dc-lite thay phần tử (mất focus) hoặc gán
// một giá trị KHÁC vào ô (con trỏ nhảy về cuối) thì lộ ra; gõ ở cuối chuỗi thì việc con trỏ
// nhảy về cuối không phân biệt được. Lưu ý đã đo: gán lại CÙNG giá trị không dời con trỏ
// trên Chrome 150 (đúng chuẩn HTML), nên test này không bắt được lỗi kiểu đó — vì nó vô hại.
//
//     Dùng: node cdp_dieu_khien.js <chrome> <url> <css-selector> [biểu-thức-JS-kiểm-thêm]
//
// (B) BẤM TỪNG BƯỚC: chạy lần lượt các biểu thức JS (thường là .click()), sau mỗi bước chờ
//     dựng lại rồi chụp outerHTML của <x-dc> — để so DOM SAU KHI VÁ với bộ tham chiếu.
//     Lần dựng đầu chỉ CHÈN nút mới; chỉ có tương tác mới đi qua đường vá (vaNut, neo, gỡ
//     thuộc tính thừa), nên chế độ này là thứ kiểm đường vá.
//     Dùng: node cdp_dieu_khien.js <chrome> <url> --buoc '<mảng JSON các biểu thức JS>'
//
// In ra một dòng JSON. Node 22 có sẵn WebSocket và fetch nên không cần gói ngoài.
'use strict';
const cp = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const [chrome, url, sel, them] = process.argv.slice(2);
const hoSo = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-'));
const proc = cp.spawn(chrome, ['--headless=new', '--disable-gpu', '--no-sandbox', '--remote-debugging-port=0',
  '--user-data-dir=' + hoSo, '--window-size=1280,900',
  '--host-resolver-rules=MAP fonts.googleapis.com ~NOTFOUND, MAP fonts.gstatic.com ~NOTFOUND', 'about:blank'],
  { stdio: ['ignore', 'ignore', 'pipe'] });
const ngu = (ms) => new Promise((r) => setTimeout(r, ms));
const xong = (code, obj) => { console.log(JSON.stringify(obj)); try { proc.kill('SIGKILL'); } catch (e) { /* đã tắt */ } setTimeout(() => { try { fs.rmSync(hoSo, { recursive: true, force: true }); } catch (e) { /* bỏ qua */ } process.exit(code); }, 300); };
setTimeout(() => xong(2, { loi: 'quá 60 giây' }), 60000);

let errBuf = '';
proc.stderr.on('data', async (d) => {
  errBuf += d;
  const m = /DevTools listening on (ws:\/\/[^\s]+)/.exec(errBuf);
  if (!m || proc._daNoi) return;
  proc._daNoi = true;
  try { await chay(new URL(m[1]).port); } catch (e) { xong(1, { loi: String(e && e.stack || e) }); }
});

async function chay(cong) {
  const ds = await (await fetch('http://127.0.0.1:' + cong + '/json/list')).json();
  const trang = ds.find((t) => t.type === 'page');
  const ws = new WebSocket(trang.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  let id = 0; const cho = new Map();
  ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && cho.has(m.id)) { cho.get(m.id)(m); cho.delete(m.id); } };
  const goi = (method, params) => new Promise((r) => { const i = ++id; cho.set(i, r); ws.send(JSON.stringify({ id: i, method, params: params || {} })); });
  const tinh = async (expr) => {
    const r = await goi('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result.exceptionDetails) throw new Error('Lỗi khi chạy: ' + expr + '\n' + JSON.stringify(r.result.exceptionDetails));
    return r.result.result.value;
  };

  await goi('Page.navigate', { url });
  let trangThai = null;
  for (let i = 0; i < 100 && !trangThai; i++) { await ngu(100); trangThai = await tinh("(document.querySelector('x-dc')||{}).getAttribute ? document.querySelector('x-dc').getAttribute('data-dc') : null").catch(() => null); }
  if (trangThai !== 'san-sang') return xong(1, { loi: 'trang không sẵn sàng', trangThai });

  if (sel === '--buoc') {
    const anh = [];
    for (const buoc of JSON.parse(them)) {
      await tinh(buoc);
      await ngu(150);
      anh.push(await tinh("document.querySelector('x-dc').outerHTML"));
    }
    ws.close();
    return xong(0, { anh });
  }

  const S = JSON.stringify(sel);
  if (!await tinh('(() => { const e = document.querySelector(' + S + '); if (!e) return false; window.__o = e; e.focus(); return document.activeElement === e; })()')) return xong(1, { loi: 'không focus được ' + sel });
  await goi('Input.insertText', { text: 'abcd' });
  await ngu(150);
  await tinh('window.__o.setSelectionRange(2, 2)');
  await goi('Input.insertText', { text: 'X' });
  await ngu(150);
  const kq = await tinh(`(async () => { const e = window.__o; return {
    cungPhanTu: document.activeElement === e, conTrongTrang: e.isConnected, giaTri: e.value,
    conTro: e.selectionStart, thuocTinhValue: e.getAttribute('value'), them: await (${them || 'null'}) }; })()`);
  ws.close();
  xong(0, kq);
}
