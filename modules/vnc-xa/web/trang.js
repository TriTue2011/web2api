// Trang «Workspace trình duyệt» của module vnc-xa — bố cục, nhãn, nút theo thẻ Workspace trình duyệt + mục «Tái dùng»
// của c2a, viết bằng JS thuần. Chống XSS: dữ liệu từ máy chủ CHỈ đi qua textContent / createElement / thuộc tính đã
// kiểm; không gán HTML thô, không chèn chuỗi thành thẻ. Khoá quản trị đọc từ localStorage['web2api_khoa'] (cùng khoá với web/khung.js),
// chỉ gửi trong tiêu đề Authorization, không bao giờ lên URL. Cookie không bao giờ đi qua trang này.
'use strict';
(() => {
  const KHOA_LUU = 'web2api_khoa';
  const MODULE = 'vnc-xa';
  const MAU_HO_SO = /^[a-f0-9]{16,64}$/;
  const KIEU_BI_MAT = ['bi_mat', 'danh_sach_bi_mat'];
  // Gợi ý trang bắt đầu (nằm trong MODULE — lõi không biết tên dịch vụ nào).
  const DICH_VU = [['Muse', 'https://muse.ai/'], ['Google', 'https://myaccount.google.com/'],
    ['ChatGPT', 'https://chatgpt.com/'], ['Claude', 'https://claude.ai/'], ['Gemini', 'https://gemini.google.com/']];
  const GOC = new URL('../../', location.href);
  const $ = (id) => document.getElementById(id);

  let khoaTrang = '';
  let ban = false;
  let dsWs = [];
  let dsDich = [];
  // Vé noVNC dùng một lần trong 60 giây (lõi): ẩn liên kết sớm hơn chút để không bấm vào vé vừa hết hạn.
  const HAN_VE_MS = 55000;
  const lienKet = {}; // ho_so → {url: URL vé đã kiểm, het: mốc hết hạn (ms)}
  let henVe = 0;

  function docKhoa() { try { return localStorage.getItem(KHOA_LUU) || khoaTrang; } catch (e) { return khoaTrang; } }
  function luuKhoa(k) { khoaTrang = k; try { localStorage.setItem(KHOA_LUU, k); } catch (e) { /* chỉ giữ trong trang */ } }

  function the(ten, lop, chu) {
    const e = document.createElement(ten);
    if (lop) e.className = lop;
    if (chu !== undefined) e.textContent = chu;
    return e;
  }

  function thongBao(chu, loai) {
    const o = $('thong-bao');
    o.className = 'bao' + (loai ? ' bao-' + loai : '');
    o.textContent = chu;
    o.hidden = !chu;
  }

  function loiDanhSach(chu) { const o = $('loi'); o.textContent = chu || ''; o.hidden = !chu; }

  async function goi(duong, than) {
    const khoa = docKhoa();
    if (!khoa) { $('o-khoa').hidden = false; throw new Error('Cần khoá quản trị.'); }
    const tuyChon = { method: than === undefined ? 'GET' : 'POST', cache: 'no-store', credentials: 'same-origin',
      headers: { Authorization: 'Bearer ' + khoa } };
    if (than !== undefined) { tuyChon.headers['Content-Type'] = 'application/json'; tuyChon.body = JSON.stringify(than); }
    const r = await fetch(new URL(duong, GOC), tuyChon);
    let du = null;
    try { du = await r.json(); } catch (e) { du = null; }
    if (r.status === 401) $('o-khoa').hidden = false;
    return { r, du };
  }

  function loiCua(kq) {
    const m = kq.du && kq.du.error && typeof kq.du.error.message === 'string' ? kq.du.error.message : '';
    return m || ('Lỗi HTTP ' + kq.r.status);
  }

  async function chay(viec) {
    if (ban) return;
    ban = true; veNut();
    try { await viec(); } catch (e) { thongBao(String(e && e.message || e), 'loi'); }
    finally { ban = false; veNut(); }
  }

  // ── danh sách workspace ──
  async function napWs() {
    const kq = await goi('api/trinh-duyet/' + MODULE);
    if (!kq.r.ok || !Array.isArray(kq.du)) { loiDanhSach(loiCua(kq)); dsWs = []; veWs(); return; }
    loiDanhSach('');
    dsWs = kq.du.filter((x) => x && typeof x.ho_so === 'string' && MAU_HO_SO.test(x.ho_so));
    veWs();
  }

  // Dịch vụ này không có mở tự động: mọi workspace đang mở đều là mở thủ công (qua noVNC) — không phân biệt thêm.
  function trangThai(w) { return w.dang_mo ? 'Đang mở thủ công' : 'Đã đóng · giữ phiên'; }

  function veWs() {
    const ds = $('ds-ws');
    ds.replaceChildren();
    clearTimeout(henVe);
    let ganNhat = Infinity;
    if (!dsWs.length && $('loi').hidden) ds.append(the('p', 'phu', 'Chưa có workspace.'));
    for (const w of dsWs) {
      const o = the('div', 'ws');
      o.dataset.hoSo = w.ho_so;
      const dau = the('div', 'ws-dau');
      dau.append(the('span', 'ws-ten', typeof w.ten === 'string' && w.ten ? w.ten : w.ho_so),
        the('span', 'ws-tt', trangThai(w)));
      const nut = the('div', 'ws-nut');
      const mo = the('button', 'nut nut-chinh nut-nho mo', 'Mở');
      mo.type = 'button';
      mo.addEventListener('click', () => moWs(w));
      nut.append(mo);
      if (w.dang_mo) {
        const dong = the('button', 'nut nut-nho dong', 'Đóng · giữ phiên');
        dong.type = 'button';
        dong.addEventListener('click', () => dongWs(w));
        nut.append(dong);
      }
      o.append(dau, the('p', 'ws-id', w.ho_so), nut);
      const lk = lienKet[w.ho_so];
      if (lk && Date.now() < lk.het) {
        const a = the('a', 'vao', 'Vào trình duyệt ↗ (dùng một lần trong 60 giây)');
        a.href = lk.url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        o.append(a);
        ganNhat = Math.min(ganNhat, lk.het - Date.now());
      } else if (lk && w.dang_mo) {
        // Vé đã hết hạn: không để liên kết chết trên trang — xin vé mới cho đúng workspace đang mở.
        const lai = the('button', 'nut nut-nho mo-lai', 'Mở lại');
        lai.type = 'button';
        lai.addEventListener('click', () => moWs(w, true));
        nut.append(lai);
      }
      ds.append(o);
    }
    if (ganNhat !== Infinity) henVe = setTimeout(veWs, ganNhat + 50);
    veTaiDung();
    veNut();
  }

  // URL vé do lõi trả: chỉ nhận đường tới trang vé noVNC CÙNG origin — không bao giờ gắn `javascript:`/máy lạ vào href.
  function urlVe(u) {
    if (typeof u !== 'string') return null;
    let dich;
    try { dich = new URL(u, location.origin); } catch (e) { return null; }
    return dich.origin === location.origin && dich.pathname === '/novnc/dang-nhap' ? dich.href : null;
  }

  function moWs(w, xinVeMoi) {
    chay(async () => {
      const than = { loai: w.loai === 'chromium' ? 'chromium' : 'firefox', ho_so: w.ho_so };
      const url = xinVeMoi ? '' : $('url').value.trim();
      // Trang bắt đầu chỉ áp dụng khi trình duyệt KHỞI ĐỘNG (không có cổng điều khiển) — đang mở thì không gửi, báo rõ.
      const boQuaUrl = Boolean(url && w.dang_mo);
      if (url && !boQuaUrl) than.url = url;
      const kq = await goi('api/trinh-duyet/' + MODULE + '/mo', than);
      if (!kq.r.ok) throw new Error(loiCua(kq));
      const ve = urlVe(kq.du && kq.du.url);
      if (!ve) throw new Error('Địa chỉ vào trình duyệt không hợp lệ.');
      lienKet[w.ho_so] = { url: ve, het: Date.now() + HAN_VE_MS };
      await napWs();
      if (boQuaUrl) thongBao('Đã mở ' + (w.ten || w.ho_so) + '. URL chỉ áp dụng khi mở mới; đóng rồi mở lại để đổi trang '
        + '(hoặc gõ địa chỉ trong noVNC).', 'vang');
      else thongBao('Đã mở ' + (w.ten || w.ho_so) + '. Bấm «Vào trình duyệt» để đăng nhập.', 'tot');
    });
  }

  function dongWs(w) {
    chay(async () => {
      const kq = await goi('api/trinh-duyet/' + MODULE + '/' + w.ho_so + '/dong', { giu_phien: true });
      delete lienKet[w.ho_so];
      if (!kq.r.ok) throw new Error(loiCua(kq));
      await napWs();
      thongBao('Đã đóng trình duyệt, giữ dữ liệu đăng nhập.', 'tot');
    });
  }

  function taoWs() {
    chay(async () => {
      const ten = $('ten').value.trim();
      if (!ten) return;
      const kq = await goi('api/trinh-duyet/' + MODULE, { ten });
      if (!kq.r.ok) throw new Error(loiCua(kq));
      $('ten').value = '';
      await napWs();
      thongBao('Đã tạo hồ sơ riêng. Bấm Mở để đăng nhập.', 'tot');
    });
  }

  // ── gợi ý trang bắt đầu ──
  function veDichVu() {
    const o = $('chon-dv');
    o.replaceChildren();
    for (const [nhan, dich] of DICH_VU) {
      const b = the('button', 'nut nut-nho', nhan);
      b.type = 'button';
      b.dataset.url = dich;
      b.setAttribute('aria-pressed', String($('url').value.trim() === dich));
      b.addEventListener('click', () => { $('url').value = dich; veDichVu(); });
      o.append(b);
    }
  }

  // ── tái dùng: workspace đã đóng → cookie → module đích ──
  function veTaiDung() {
    const chon = $('ws-tai-dung');
    const cu = chon.value;
    chon.replaceChildren();
    const dong = dsWs.filter((w) => !w.dang_mo);
    if (!dong.length) chon.append(new Option('(chưa có workspace đã đóng)', ''));
    for (const w of dong) chon.append(new Option((w.ten || w.ho_so) + ' · ' + w.ho_so.slice(0, 8) + '…', w.ho_so));
    if (dong.some((w) => w.ho_so === cu)) chon.value = cu;
  }

  async function napDich() {
    // Danh mục module là đường MỞ (không bí mật). Chỉ liệt kê trường mà CHÍNH module đích khai nhận cookie
    // (`nhan_cookie: {mien}` trong manifest) — không ghi nhầm vào khoá API của module khác; tên miền theo bản khai.
    const r = await fetch(new URL('api/modules/catalog', GOC), { cache: 'no-store' });
    const du = await r.json().catch(() => null);
    dsDich = [];
    for (const m of (du && Array.isArray(du.modules) ? du.modules : [])) {
      if (!m || typeof m.id !== 'string' || m.id === MODULE || !Array.isArray(m.truong_cau_hinh)) continue;
      for (const t of m.truong_cau_hinh) {
        if (!t) continue;
        const nc = t.nhan_cookie;
        if (typeof t.khoa === 'string' && KIEU_BI_MAT.includes(t.kieu) && nc && typeof nc.mien === 'string') {
          dsDich.push({ module: m.id, truong: t.khoa, mien: nc.mien,
            nhan: String(m.ten || m.id) + ' — ' + String(t.nhan || t.khoa) + ' · ' + nc.mien });
        }
      }
    }
    const chon = $('dich');
    chon.replaceChildren();
    if (!dsDich.length) chon.append(new Option('(chưa có module nào khai nhận cookie)', ''));
    else if (dsDich.length > 1) chon.append(new Option('— Chọn module nhận —', ''));
    dsDich.forEach((d, i) => chon.append(new Option(d.nhan, String(i))));
    chon.value = dsDich.length === 1 ? '0' : '';
    chonDich();
  }

  function chonDich() {
    const d = dsDich[Number($('dich').value)];
    $('mien').value = $('dich').value !== '' && d ? d.mien : '';
    veNut();
  }

  function veKetQua(trangThaiChu, loai, loiChinh, dsKhoa) {
    const o = $('ket-qua');
    o.replaceChildren();
    const hop = the('div', 'trang-thai ' + loai);
    hop.append(the('span', 'nhan-tt', trangThaiChu));
    if (loiChinh) hop.append(the('span', '', loiChinh));
    if (dsKhoa && dsKhoa.length) {
      const ul = the('ul');
      for (const k of dsKhoa) {
        const dongChu = String(k && k.khoa || '…') + ' — ' + (k && k.ok ? 'dùng được' : 'lỗi' + (k && k.loi ? ': ' + k.loi : ''));
        ul.append(the('li', '', dongChu));
      }
      hop.append(ul);
    }
    o.append(hop);
  }

  function layCookie() {
    chay(async () => {
      const hoSo = $('ws-tai-dung').value;
      const d = $('dich').value === '' ? null : dsDich[Number($('dich').value)];
      if (!MAU_HO_SO.test(hoSo)) throw new Error('Chọn workspace đã đóng.');
      if (!d) throw new Error('Chọn module nhận.');
      const mien = d.mien;
      veKetQua('Đang chạy', '', 'Đang lấy cookie và kiểm bằng module…');
      const kq = await goi('api/trinh-duyet/' + MODULE + '/' + hoSo + '/cookie-sang-module',
        { mien, module_dich: d.module, truong: d.truong });
      const du = kq.du;
      if (du && typeof du.ok === 'boolean') {
        const dsK = Array.isArray(du.ket_qua_tung_khoa) ? du.ket_qua_tung_khoa : [];
        // Module đích có thể tự bỏ khoá bị từ chối khỏi danh sách đã lưu trong cùng lượt kiểm — chỉ cảnh báo khi phản
        // hồi THẬT SỰ có khoá không ok (không đoán).
        const coTuChoi = dsK.some((k) => k && k.ok === false);
        const canhBao = coTuChoi ? ' Có khoá bị module từ chối — module có thể đã tự bỏ khoá đó khỏi danh sách đã lưu.' : '';
        const n = Number(du.da_them) || 0;
        if (du.ok && n > 0) {
          veKetQua('Thành công', 'tot', 'Đã thêm ' + n + ' cookie vào ' + d.nhan + '.' + canhBao, dsK);
        } else if (du.ok) {
          veKetQua('Chưa thêm cookie mới', 'vang', 'Không thêm cookie mới vào ' + d.nhan
            + ' (đã có sẵn, hoặc module không nhận cookie này) — xem kết quả từng khoá.' + canhBao, dsK);
        } else {
          veKetQua('Lỗi', 'loi', (typeof du.loi === 'string' ? du.loi : 'Module từ chối cookie.') + canhBao, dsK);
        }
        return;
      }
      veKetQua('Lỗi', 'loi', loiCua(kq));
    });
  }

  function veNut() {
    $('tao').disabled = ban || !$('ten').value.trim();
    for (const id of ['lam-moi', 'tai-lai', 'lay-cookie']) $(id).disabled = ban;
    $('lay-cookie').disabled = ban || !$('ws-tai-dung').value || $('dich').value === '';
    for (const b of document.querySelectorAll('.ws button')) b.disabled = ban;
  }

  function napTatCa() {
    chay(async () => { await Promise.all([napWs(), napDich()]); });
  }

  document.addEventListener('DOMContentLoaded', () => {
    $('ten').addEventListener('input', veNut);
    $('ten').addEventListener('keydown', (e) => { if (e.key === 'Enter') taoWs(); });
    $('tao').addEventListener('click', taoWs);
    $('lam-moi').addEventListener('click', napTatCa);
    $('tai-lai').addEventListener('click', napTatCa);
    $('url').addEventListener('input', veDichVu);
    $('ws-tai-dung').addEventListener('change', veNut);
    $('dich').addEventListener('change', chonDich);
    $('lay-cookie').addEventListener('click', layCookie);
    $('luu-khoa').addEventListener('click', () => {
      const k = $('khoa').value.trim();
      if (!k) return;
      luuKhoa(k);
      $('khoa').value = '';
      $('o-khoa').hidden = true;
      napTatCa();
    });
    veDichVu();
    veTaiDung();
    if (!docKhoa()) { $('o-khoa').hidden = false; napDich().catch(() => {}); veWs(); return; }
    napTatCa();
  });
})();
