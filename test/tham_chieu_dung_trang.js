// Bộ dựng THAM CHIẾU cho test so sánh dc-lite: trích nguyên nghĩa từ hàm build() của
// _audit.js (bộ dựng tĩnh phiên trước đã dùng để kiểm 383 ca trang demo), bỏ phần đo
// tràn ngang. Giữ y hệt: dựng ở mức chuỗi, giá trị null/hàm/đối tượng thành rỗng,
// sc-if theo "truthy" của JS, sc-for nối phạm vi bằng prototype, setState trộn tại chỗ,
// localStorage giả luôn trả null. KHÔNG sửa nghĩa ở đây để test "dễ qua" — chênh lệch
// có chủ đích được liệt kê ở test_web2api_dc_lite.py.
//
// Dùng: node tham_chieu_dung_trang.js <trang.dc.html>  → in ra một tài liệu HTML đầy đủ.
// Test so sánh thì nạp file này như <script> thường TRONG Chrome (shim module/require), để
// bộ tham chiếu và dc-lite chạy cùng một máy JS — xem docstring test_web2api_dc_lite.py.
'use strict';
const fs = require('fs');

const get = (o, p) => p.split('.').reduce((a, k) => (a == null ? undefined : a[k]), o);

function dungThamChieu(html, pre) {
  const m = html.match(/<script type="text\/x-dc" data-dc-script[^>]*>([\s\S]*?)<\/script>/);
  class DCLogic { constructor(p) { this.props = p || {}; } setState(p) { Object.assign(this.state, typeof p === 'function' ? p(this.state) : p); } }
  const C = new Function('DCLogic', 'localStorage', m[1] + '\nreturn Component;')(DCLogic, { getItem: () => null, setItem() {} });
  // Thêm duy nhất so với _audit.js: Kien-truc.dc.html không khai báo state (_audit chưa từng dựng trang này),
  // Object.assign(undefined, …) sẽ ném lỗi. Đặt state rỗng KHÔNG đổi nghĩa dựng của các trang khác.
  const c = new C(); if (!c.state) c.state = {}; Object.assign(c.state, pre || {}); const sc = c.renderVals();
  const interp = (s, x) => s.replace(/\{\{\s*([\w.$]+)\s*\}\}/g, (_, k) => { const v = get(x, k); return v === undefined || v === null || typeof v === 'function' || typeof v === 'object' ? '' : String(v); });
  const close = (s, from, tag) => { let d = 1; const re = new RegExp('<(/?)' + tag + '\\b[^>]*>', 'g'); re.lastIndex = from; let mm; while ((mm = re.exec(s))) { d += mm[1] ? -1 : 1; if (!d) return { s: mm.index, e: re.lastIndex }; } throw new Error('thiếu đóng ' + tag); };
  const render = (s, x) => { let o = '', i = 0; const re = /<(sc-for|sc-if)\b([^>]*)>/g; let mm;
    while ((mm = re.exec(s.slice(i)))) { const st = i + mm.index, tag = mm[1], at = mm[2], af = st + mm[0].length, cl = close(s, af, tag); o += interp(s.slice(i, st), x); const inner = s.slice(af, cl.s);
      if (tag === 'sc-if') { if (get(x, /value="\{\{\s*([\w.$]+)\s*\}\}"/.exec(at)[1])) o += render(inner, x); }
      else { const L = get(x, /list="\{\{\s*([\w.$]+)\s*\}\}"/.exec(at)[1]) || []; const as = /as="(\w+)"/.exec(at)[1]; L.forEach((it, ix) => { o += render(inner, Object.assign(Object.create(x), { [as]: it, $index: ix })); }); }
      i = cl.e; re.lastIndex = 0; }
    return o + interp(s.slice(i), x); };
  let body = html.slice(html.indexOf('<x-dc>') + 6, html.indexOf('</x-dc>')); const hm = /<helmet>([\s\S]*?)<\/helmet>/.exec(body); body = body.replace(hm[0], '');
  return { head: hm[1], body: render(body, Object.assign(Object.create(null), sc)) };
}

module.exports = { dungThamChieu };

if (require.main === module) {
  const r = dungThamChieu(fs.readFileSync(process.argv[2], 'utf8'), {});
  process.stdout.write('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' + r.head + '</head><body>' + r.body + '</body></html>');
}
