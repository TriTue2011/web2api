/*
 * dc-lite — runtime mã nguồn mở chạy NGUYÊN VĂN các file *.dc.html của bản demo
 * trên trình duyệt thường. Tên file là support.js để dòng
 * <script src="./support.js"> sẵn có trong demo khớp mà không phải sửa demo.
 *
 * Cú pháp hỗ trợ (đo bằng grep trên cả 10 trang demo, không đoán):
 *   <x-dc> … </x-dc>            khuôn mẫu của trang
 *   <helmet> … </helmet>        <link>/<style> đưa lên <head>, chỉ một lần
 *   <sc-for list="{{a.b}}" as="n">   lặp; trong thân có n và $index
 *   <sc-if value="{{a.b}}">     hiện khi giá trị "truthy" kiểu JS ([] vẫn là đúng)
 *   {{a.b}} trong chữ và trong thuộc tính; {{true}} / {{false}} là hằng
 *   onClick / onInput / onChange / onKeyDown / onPointer* / onMouse* /
 *   onTouch* / onFocus / onSubmit … = "{{hàm}}" — hàm lấy từ renderVals()
 *   class Component extends DCLogic { state = {…}; renderVals() {…} }
 *   this.setState(phần | hàm), this.props (từ data-props của thẻ script)
 *   didMount() — móc tuỳ chọn của RIÊNG ta, gọi một lần sau lần dựng đầu.
 *
 * Các quyết định và vì sao:
 * 1. Đọc lại MÃ NGUỒN trang bằng fetch(location.href), không đọc DOM của <x-dc>.
 *    Bộ phân tích HTML của trình duyệt vứt thẻ lạ nằm trong <select> (Nha.dc.html
 *    có 49 <sc-for> trong <select>), nên DOM đã hỏng trước khi script kịp đọc.
 *    Hệ quả: phải mở trang qua http(s); mở file:// sẽ báo lỗi rõ trên trang.
 * 2. Dựng ở mức CHUỖI với đúng nghĩa của bộ dựng tham chiếu (_audit.js): giá trị
 *    null/undefined/hàm/đối tượng thành rỗng, phạm vi lặp nối bằng prototype.
 *    Khác tham chiếu có chủ đích: giá trị chèn vào được thoát ký tự HTML; thuộc
 *    tính on* không ra DOM mà gắn thành bộ nghe sự kiện; thuộc tính boolean
 *    (disabled, checked…) sai thì bỏ hẳn — vì disabled="false" trong HTML vẫn là TẮT.
 * 3. Mỗi khối sc-for/sc-if được bọc bằng cặp chú thích <!--dcN--> … <!--/dcN-->
 *    (N = vị trí của khối trong khuôn mẫu). Khi vá DOM, nhờ cặp neo này mà một
 *    sc-if bật/tắt không làm lệch các nút anh em phía sau — ô nhập đứng sau một
 *    thông báo vừa hiện vẫn là CHÍNH phần tử cũ, không mất focus.
 * 4. Dựng lại sau setState bằng cách VÁ DOM (so từng nút theo thứ tự trong từng
 *    đoạn neo), không thay cả cây: giữ focus, vị trí con trỏ, vị trí cuộn.
 *    value/checked được gán qua thuộc tính JS (thuộc tính HTML value không đổi
 *    được ô người dùng đã gõ) và chỉ khi khác giá trị đang có. Theo chuẩn HTML,
 *    gán CÙNG giá trị không dời con trỏ (đo trên Chrome 150: bỏ điều kiện này
 *    test gõ phím vẫn xanh) — điều kiện giữ lại chỉ để không đụng DOM thừa.
 * 5. setState trộn nông vào một đối tượng state MỚI rồi hẹn dựng lại ở microtask:
 *    nhiều setState trong một lần bấm chỉ dựng một lần, và setState gọi từ chính
 *    renderVals không gây đệ quy. Quá 50 lần dựng liền trong cùng một lượt sự
 *    kiện thì coi là vòng lặp và báo lỗi thay vì treo trang.
 */
(() => {
  'use strict';

  // Giấu khuôn mẫu thô ngay từ <head> để người xem không thấy «{{…}}» nháy lên
  // trong lúc chờ đọc mã nguồn. Phần tử gốc được hiện lại bằng style nội tuyến.
  const an = document.createElement('style');
  an.textContent = 'x-dc{display:none}';
  document.head.appendChild(an);

  const BOOL = new Set(['disabled', 'checked', 'selected', 'readonly', 'multiple', 'hidden',
    'autofocus', 'required', 'open', 'novalidate', 'inert']);
  const MOT_BIEN = /^\{\{\s*([\w.$]+)\s*\}\}$/;
  const BIEN = /\{\{\s*([\w.$]+)\s*\}\}/g;

  // ---------- Giá trị ----------
  const lay = (x, p) => (p === 'true' ? true : p === 'false' ? false
    : p.split('.').reduce((a, k) => (a == null ? undefined : a[k]), x));
  const chu = (v) => (v == null || typeof v === 'function' || typeof v === 'object' ? '' : String(v));
  const thoat = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const thoatTT = (s) => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');

  // ---------- Biên dịch khuôn mẫu MỘT lần thành cây {chuỗi | hàm | khối} ----------
  // Dò thẻ đóng cùng loại có đếm độ sâu — y như hàm close() của bộ dựng tham chiếu.
  function timDong(s, tu, tag) {
    let d = 1; const re = new RegExp('<(/?)' + tag + '\\b[^>]*>', 'g'); re.lastIndex = tu; let m;
    while ((m = re.exec(s))) { d += m[1] ? -1 : 1; if (!d) return { s: m.index, e: re.lastIndex }; }
    throw new Error('Khuôn mẫu thiếu thẻ đóng </' + tag + '>');
  }
  function duongDan(at, ten, tag) {
    const m = new RegExp(ten + '="\\{\\{\\s*([\\w.$]+)\\s*\\}\\}"').exec(at);
    if (!m) throw new Error('<' + tag + '> thiếu ' + ten + '="{{…}}": ' + at.trim());
    return m[1];
  }
  function bienDich(s, goc) {
    const ra = []; const re = /<(sc-for|sc-if)\b([^>]*)>/g; let i = 0; let m;
    while ((m = re.exec(s))) {
      const tag = m[1], at = m[2], st = m.index, sau = st + m[0].length, dong = timDong(s, sau, tag);
      ra.push(...bienChu(s.slice(i, st)));
      const kids = bienDich(s.slice(sau, dong.s), goc + sau);
      if (tag === 'sc-if') ra.push({ k: 'if', p: duongDan(at, 'value', tag), id: goc + st, kids });
      else {
        const as = /\sas="(\w+)"/.exec(at);
        if (!as) throw new Error('<sc-for> thiếu as="…": ' + at.trim());
        ra.push({ k: 'for', p: duongDan(at, 'list', tag), as: as[1], id: goc + st, kids });
      }
      i = dong.e; re.lastIndex = dong.e;
    }
    ra.push(...bienChu(s.slice(i)));
    return ra;
  }
  // Một đoạn không còn khối: tách thành chuỗi tĩnh và hàm chèn giá trị.
  function bienChu(s) {
    const ra = []; const re = /<([a-zA-Z][\w:-]*)([^>]*)>|\{\{\s*([\w.$]+)\s*\}\}/g; let i = 0; let m;
    while ((m = re.exec(s))) {
      if (m.index > i) ra.push(s.slice(i, m.index));
      if (m[3]) { const p = m[3]; ra.push((x) => thoat(chu(lay(x, p)))); }
      else if (m[2].includes('{{')) ra.push(...bienThe(m[1], m[2]));
      else ra.push(m[0]);
      i = re.lastIndex;
    }
    if (i < s.length) ra.push(s.slice(i));
    return ra;
  }
  // Một thẻ mở có thuộc tính gắn giá trị.
  function bienThe(tag, at) {
    const ra = ['<' + tag]; const re = /(\s+)([^\s"'>\/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
    const kieu = (/\stype="([^"{]*)"/.exec(at) || [])[1] || '';
    let i = 0; let m;
    while ((m = re.exec(at))) {
      const ten = m[2], tt = ten.toLowerCase(), val = m[3] ?? m[4] ?? m[5];
      i = re.lastIndex;
      if (val === undefined || !val.includes('{{')) { ra.push(m[0]); continue; }
      const mot = MOT_BIEN.exec(val);
      if (mot && /^on[a-z]/.test(tt)) {
        // onChange của ô chữ: React bắn theo từng phím (sự kiện input); select/checkbox/file thì 'change'.
        let loai = tt.slice(2);
        if (loai === 'doubleclick') loai = 'dblclick';
        if (loai === 'change' && (tag.toLowerCase() === 'textarea' ||
          (tag.toLowerCase() === 'input' && !/^(checkbox|radio|file)$/i.test(kieu)))) loai = 'input';
        const p = mot[1];
        ra.push((x, ctx) => { const f = lay(x, p); return typeof f === 'function' ? ' data-dc-on-' + loai + '="' + (ctx.ham.push(f) - 1) + '"' : ''; });
      } else if (mot && BOOL.has(tt)) {
        const p = mot[1];
        ra.push((x) => (lay(x, p) ? ' ' + ten + '=""' : ''));
      } else {
        ra.push((x) => ' ' + ten + '="' + thoatTT(val.replace(BIEN, (_, p) => chu(lay(x, p)))) + '"');
      }
    }
    ra.push(at.slice(i) + '>');
    return ra;
  }

  // ---------- Dựng chuỗi HTML từ cây đã biên dịch ----------
  function dung(cay, x, ctx) {
    let o = '';
    for (const n of cay) {
      if (typeof n === 'string') o += n;
      else if (typeof n === 'function') o += n(x, ctx);
      else {
        o += '<!--dc' + n.id + '-->';
        if (n.k === 'if') { if (lay(x, n.p)) o += dung(n.kids, x, ctx); }
        else (lay(x, n.p) || []).forEach((it, ix) => { o += dung(n.kids, Object.assign(Object.create(x), { [n.as]: it, $index: ix }), ctx); });
        o += '<!--/dc' + n.id + '-->';
      }
    }
    return o;
  }

  // ---------- Vá DOM ----------
  const laNeo = (n) => n.nodeType === 8 && /^\/?dc\d+$/.test(n.data);
  // Tìm neo cùng tên từ vị trí p, không vượt qua neo đóng của đoạn đang đứng.
  function timNeo(p, data) {
    let sau = 0;
    for (let n = p; n; n = n.nextSibling) {
      if (!laNeo(n)) continue;
      if (sau === 0 && n.data === data) return n;
      if (n.data[0] === '/') { if (sau === 0) return null; sau--; } else sau++;
    }
    return null;
  }
  const hopNhau = (l, f) => l.nodeType === f.nodeType && !laNeo(l) &&
    (f.nodeType !== 1 || (l.nodeName === f.nodeName && l.namespaceURI === f.namespaceURI && l.id === f.id));

  function vaCon(cu, moi, ctx) {
    let p = cu.firstChild;
    for (const f of Array.from(moi.childNodes)) {
      if (laNeo(f)) {
        const t = timNeo(p, f.data);
        if (t) { while (p !== t) { const n = p.nextSibling; cu.removeChild(p); p = n; } p = p.nextSibling; }
        else cu.insertBefore(f, p);
        continue;
      }
      if (p && hopNhau(p, f)) { vaNut(p, f, ctx); p = p.nextSibling; }
      else { cu.insertBefore(f, p); ganMoi(f, ctx); }
    }
    while (p) { const n = p.nextSibling; cu.removeChild(p); p = n; }
  }
  function vaNut(l, f, ctx) {
    if (f.nodeType !== 1) { if (l.data !== f.data) l.data = f.data; return; }
    const on = {};
    for (const a of f.attributes) {
      if (a.name.startsWith('data-dc-on-')) { on[a.name.slice(11)] = ctx.ham[+a.value]; continue; }
      if (l.getAttribute(a.name) !== a.value) {
        if (a.namespaceURI) l.setAttributeNS(a.namespaceURI, a.name, a.value); else l.setAttribute(a.name, a.value);
      }
    }
    for (const a of Array.from(l.attributes)) if (!f.hasAttribute(a.name)) l.removeAttribute(a.name);
    ganSuKien(l, on);
    if (l.nodeName !== 'TEXTAREA') vaCon(l, f, ctx);
    dieuKhien(l);
  }
  // Phần tử mới chèn: chuyển data-dc-on-* thành bộ nghe, đặt value/checked thật.
  function ganMoi(n, ctx) {
    if (n.nodeType !== 1) return;
    for (const e of [n, ...n.querySelectorAll('*')]) {
      const on = {};
      for (const a of Array.from(e.attributes)) {
        if (a.name.startsWith('data-dc-on-')) { on[a.name.slice(11)] = ctx.ham[+a.value]; e.removeAttribute(a.name); }
      }
      ganSuKien(e, on);
      dieuKhien(e);
      if (e.hasAttribute('autofocus')) ctx.focus = e; // React: autoFocus chỉ khi phần tử MỚI xuất hiện
    }
  }
  // Mỗi phần tử chỉ addEventListener một lần cho mỗi loại; hàm thật đọc từ _dcOn
  // nên mỗi lần dựng chỉ cần thay tham chiếu hàm, không gỡ/gắn lại bộ nghe.
  function ganSuKien(el, on) {
    const cu = el._dcOn || (el._dcOn = {});
    for (const k in cu) if (!(k in on)) cu[k] = null;
    for (const k in on) {
      if (!(k in cu)) el.addEventListener(k, (e) => { const h = el._dcOn[k]; if (h) h(e); });
      cu[k] = on[k];
    }
  }
  // Ô điều khiển theo state (kiểu React): giá trị thật nằm ở thuộc tính JS.
  function dieuKhien(el) {
    const tag = el.nodeName;
    if (tag === 'INPUT') {
      const t = el.type;
      if (t === 'checkbox' || t === 'radio') { const c = el.hasAttribute('checked'); if (el.checked !== c) el.checked = c; }
      else if (t !== 'file' && el.hasAttribute('value')) { const v = el.getAttribute('value'); if (el.value !== v) el.value = v; }
    } else if ((tag === 'TEXTAREA' || tag === 'SELECT') && el.hasAttribute('value')) {
      const v = el.getAttribute('value'); if (el.value !== v) el.value = v;
    }
  }

  // ---------- Thành phần ----------
  class DCLogic {
    constructor(props) { this.props = props || {}; this.state = {}; }
    setState(phan) {
      const them = typeof phan === 'function' ? phan(this.state, this.props) : phan;
      if (them == null) return;
      this.state = Object.assign({}, this.state, them);
      henVe();
    }
  }
  window.DCLogic = DCLogic;

  let goc = null, cay = null, comp = null, daGan = false, hen = false, chuoi = 0;
  function henVe() {
    if (!daGan || hen) return;
    hen = true;
    queueMicrotask(() => {
      hen = false;
      if (++chuoi > 50) { baoLoi(new Error('setState được gọi liên tục hơn 50 lần — có thể renderVals() tự gọi setState vô điều kiện.')); return; }
      setTimeout(() => { chuoi = 0; });
      veLai();
    });
  }
  function veLai() {
    const ctx = { ham: [], focus: null };
    let vals;
    try { vals = comp.renderVals(); } catch (e) { baoLoi(e, 'renderVals() ném lỗi'); return false; }
    let html;
    try { html = dung(cay, Object.assign(Object.create(null), vals), ctx); } catch (e) { baoLoi(e, 'Không dựng được khuôn mẫu'); return false; }
    const t = document.createElement('template');
    t.innerHTML = html;
    vaCon(goc, t.content, ctx);
    if (ctx.focus) ctx.focus.focus();
    const cu = document.getElementById('dc-loi'); if (cu) cu.remove();
    return true;
  }
  // Lỗi hiện thành hộp cố định trên trang (không để trắng trơn), đồng thời ra console.
  function baoLoi(e, tieuDe) {
    console.error('[dc-lite]', tieuDe || '', e);
    let h = document.getElementById('dc-loi');
    if (!h) {
      h = document.createElement('div'); h.id = 'dc-loi'; h.setAttribute('role', 'alert');
      h.style.cssText = 'position:fixed;left:12px;right:12px;top:12px;z-index:2147483647;max-height:70vh;overflow:auto;box-sizing:border-box;padding:14px 16px;border:2px solid #B42318;border-radius:12px;background:#FDECEA;color:#8A1C12;font:14px/1.5 system-ui,sans-serif;box-shadow:0 12px 32px rgba(0,0,0,.18)';
      document.body.appendChild(h);
    }
    h.textContent = '';
    const b = document.createElement('strong'); b.textContent = 'dc-lite: ' + (tieuDe || 'lỗi khi dựng trang'); h.appendChild(b);
    const m = document.createElement('div'); m.textContent = String((e && e.message) || e); h.appendChild(m);
    if (e && e.stack) { const s = document.createElement('pre'); s.style.cssText = 'margin:8px 0 0;white-space:pre-wrap;font-size:12px'; s.textContent = e.stack; h.appendChild(s); }
  }

  async function khoiDong() {
    goc = document.querySelector('x-dc');
    if (!goc) return;
    try {
      // <helmet> trong DOM đã phân tích vẫn nguyên vẹn (link/style hợp lệ trong body) — dời lên <head>.
      const helmet = goc.querySelector('helmet');
      if (helmet) document.head.append(...helmet.childNodes);
      if (location.protocol === 'file:') throw new Error('Trang .dc.html phải mở qua máy chủ http(s): dc-lite cần đọc lại mã nguồn trang bằng fetch, mà trình duyệt chặn fetch với file://.');
      const r = await fetch(location.href, { cache: 'force-cache' });
      if (!r.ok) throw new Error('Đọc lại mã nguồn trang thất bại: HTTP ' + r.status);
      const nguon = await r.text();
      const dau = nguon.indexOf('<x-dc>'), cuoi = nguon.lastIndexOf('</x-dc>');
      if (dau < 0 || cuoi < dau) throw new Error('Không thấy cặp <x-dc>…</x-dc> trong mã nguồn trang.');
      const khuon = nguon.slice(dau + 6, cuoi).replace(/<helmet>[\s\S]*?<\/helmet>/, '');
      cay = bienDich(khuon, dau + 6);

      const sc = document.querySelector('script[data-dc-script]');
      if (!sc) throw new Error('Không thấy <script type="text/x-dc" data-dc-script> chứa class Component.');
      let props = {};
      try { props = JSON.parse(sc.getAttribute('data-props') || '{}'); } catch (e) { throw new Error('data-props không phải JSON hợp lệ: ' + e.message); }
      const Component = new Function('DCLogic', sc.textContent + '\n;return Component;')(DCLogic);
      comp = new Component(props);

      goc.textContent = '';             // bỏ bản DOM đã bị trình duyệt phân tích hỏng
      goc.style.display = 'contents';    // gốc không tạo hộp riêng: bố cục y như dựng thẳng vào <body>
      daGan = true;
      if (!veLai()) { goc.setAttribute('data-dc', 'loi'); return; }
      goc.setAttribute('data-dc', 'san-sang');
      if (typeof comp.didMount === 'function') comp.didMount();
    } catch (e) {
      if (goc) { goc.setAttribute('data-dc', 'loi'); goc.style.display = 'contents'; }
      baoLoi(e, 'không khởi động được trang');
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', khoiDong);
  else khoiDong();
})();
