/* Tab YouTube (NV301 mảnh giao diện) — trình phát đầy đủ qua hành động
   thực thể của lõi (phiên quản trị). Không gọi thẳng đường riêng của
   module (đường đó cần khoá khách, trình duyệt không có) và không giữ
   bí mật nào. Mọi dữ liệu ngoài đều qua textContent (không innerHTML
   với dữ liệu) — tên bài/kênh/tên loa không chèn được mã. */
'use strict';

const $ = (id) => document.getElementById(id);

function el(tag, cls, text) {
  const o = document.createElement(tag);
  if (cls) o.className = cls;
  if (text !== undefined && text !== null) o.textContent = text;
  return o;
}

function hienLoi(idO, chu) {
  const o = $(idO);
  if (!o) return;
  o.textContent = chu || '';
  o.hidden = !chu;
}

function dinhDangGiay(giay) {
  if (giay === null || giay === undefined || isNaN(giay) || giay < 0) return '–';
  const g = Math.floor(giay);
  const gio = Math.floor(g / 3600);
  const phut = Math.floor((g % 3600) / 60);
  const gi = g % 60;
  if (gio > 0) return gio + ':' + String(phut).padStart(2, '0') + ':' + String(gi).padStart(2, '0');
  return phut + ':' + String(gi).padStart(2, '0');
}

let csrfToken = '';
async function layCsrf() {
  if (csrfToken) return csrfToken;
  try {
    const r = await fetch('../../../api/toi', { credentials: 'same-origin', cache: 'no-store' });
    if (r.ok) {
      const j = await r.json();
      if (j && j.csrf) csrfToken = j.csrf;
    }
  } catch (e) { /* lõi sẽ 403 và báo rõ */ }
  return csrfToken;
}

async function goi(duong, than) {
  const method = than === undefined ? 'GET' : 'POST';
  const hd = {};
  if (method !== 'GET') {
    hd['Content-Type'] = 'application/json';
    const csrf = await layCsrf();
    if (csrf) hd['X-CSRF-Token'] = csrf;
  }
  const r = await fetch('../../../' + duong, {
    method, cache: 'no-store', credentials: 'same-origin', headers: hd,
    body: than === undefined ? undefined : JSON.stringify(than),
  });
  let du = null;
  try { du = await r.json(); } catch (e) { du = null; }
  if (r.status === 401) {
    location.replace('../../Dang-nhap.dc.html?ve=' + encodeURIComponent(location.pathname + location.search));
    throw new Error('het_phien');
  }
  return { r, du };
}

/* ── trạng thái trang ─────────────────────────────────────────────── */
let thucTheXem = '';
let thucTheDieuKhien = '';
let ruiRoDieuKhien = '';
let phienHienTai = null;   // phiên đang hiển thị ở thẻ Đang phát
let cacPhien = [];
let phienChon = null;      // id phiên người dùng bấm ở «Nhóm đang phát»
let playlists = [];
let loaTen = {};           // hậu tố entity loa -> tên hiển thị
let nguonTim = 'youtube';
let viTriHien = 0;         // vị trí hiển thị (tự tick khi đang phát)
let dongHoTick = null;

async function timThucThe() {
  if (thucTheXem && thucTheDieuKhien) return;
  const kq = await goi('api/thiet-bi?module=youtube');
  if (!kq.r.ok) throw new Error('Không đọc được danh sách thiết bị (HTTP ' + kq.r.status + ').');
  const ds = ((kq.du || {}).thuc_the || []);
  const xem = ds.find((e) => String(e.id || '').endsWith('.xem'));
  const dk = ds.find((e) => String(e.id || '').endsWith('.dieu_khien'));
  if (!xem || !dk) throw new Error('Chưa thấy thực thể YouTube — module có thể chưa chạy.');
  thucTheXem = xem.id;
  thucTheDieuKhien = dk.id;
  ruiRoDieuKhien = dk.rui_ro || '';
}

function loiTuPhanHoi(kq) {
  const du = kq.du || {};
  return (du.error && du.error.message) || du.loi ||
    'Không thực hiện được (HTTP ' + kq.r.status + ').';
}

function laCanXacNhan(kq) {
  const du = kq.du || {};
  return kq.r.status === 409 && du.error && du.error.code === 'can_xac_nhan';
}

// Mỗi yêu cầu có một Promise riêng; chỉ gắn handler khi hộp trước đã đóng.
let hangXacNhan = Promise.resolve();
function hoiXacNhan(chu) {
  hangXacNhan = hangXacNhan.then(() => new Promise((giai) => {
    const hop = $('hoi-xac-nhan');
    $('hoi-xac-nhan-chu').textContent = chu;
    hop.hidden = false;
    const dong = (dongY) => {
      hop.hidden = true;
      $('nut-xac-nhan').onclick = null;
      $('nut-thoi-xac-nhan').onclick = null;
      giai(dongY);
    };
    $('nut-xac-nhan').onclick = () => dong(true);
    $('nut-thoi-xac-nhan').onclick = () => dong(false);
  }));
  return hangXacNhan;
}

async function hanhDongDoc(ten, thamSo, xacNhan = false, nen = false) {
  await timThucThe();
  const kq = await goi('api/thiet-bi/' + encodeURIComponent(thucTheXem) + '/hanh-dong',
    { hanh_dong: ten, tham_so: thamSo || {}, xac_nhan: !!xacNhan });
  if (!xacNhan && laCanXacNhan(kq)) {
    // Polling nền không mở hộp và không xếp hàng yêu cầu xác nhận.
    if (nen) return { ok: true, daHuy: true };
    const ok = await hoiXacNhan('Máy chủ yêu cầu xác nhận cho «' + ten + '» — bấm «Xác nhận» để thực hiện một lần.');
    if (!ok) return { ok: true, daHuy: true };
    return hanhDongDoc(ten, thamSo, true, nen);
  }
  if (!kq.r.ok) throw new Error(loiTuPhanHoi(kq));
  const du = kq.du || {};
  if (du.ok === false) throw new Error(du.loi || 'Không thực hiện được.');
  return du;
}

async function hanhDong(ten, thamSo, xacNhan = false) {
  await timThucThe();
  if (!xacNhan && ruiRoDieuKhien === 'nguy_hiem') {
    const ok = await hoiXacNhan('Hành động «' + ten + '» được đánh dấu nguy hiểm — bấm «Xác nhận» để thực hiện một lần.');
    if (!ok) return { ok: true, daHuy: true };
    return hanhDong(ten, thamSo, true);
  }
  const kq = await goi('api/thiet-bi/' + encodeURIComponent(thucTheDieuKhien) + '/hanh-dong',
    { hanh_dong: ten, tham_so: thamSo || {}, xac_nhan: !!xacNhan });
  if (!xacNhan && laCanXacNhan(kq)) {
    const ok = await hoiXacNhan('Máy chủ yêu cầu xác nhận cho «' + ten + '» — bấm «Xác nhận» để thực hiện một lần.');
    if (!ok) return { ok: true, daHuy: true };
    return hanhDong(ten, thamSo, true);
  }
  if (!kq.r.ok) throw new Error(loiTuPhanHoi(kq));
  const du = kq.du || {};
  if (du.ok === false) throw new Error(du.loi || 'Không thực hiện được.');
  return du;
}

function loaDaChon() {
  return [...document.querySelectorAll('#ds-thiet-bi input[type="checkbox"]:checked')]
    .map((o) => o.value);
}

function tenLoa(entityId) {
  // outputs lưu dạng «media_player.<phòng>»; tên hiển thị lấy từ danh
  // sách HA, không có thì hiện hậu tố phòng (không hiện id kỹ thuật).
  if (loaTen[entityId]) return loaTen[entityId];
  const phan = String(entityId || '').split('.');
  return phan.length > 1 ? phan[phan.length - 1] : 'loa/tivi';
}

function nhanNguon(source) {
  return { youtube: 'YouTube', zing: 'Zing MP3', facebook: 'Facebook', http: 'HTTP' }[source] || 'YouTube';
}

/* ── thẻ Đang phát ────────────────────────────────────────────────── */
function veDangPhat() {
  const phien = phienHienTai;
  const anh = $('anh-bia');
  const ten = $('ten-bai');
  const kenh = $('kenh-bai');
  const trenLoa = $('tren-loa');
  const chip = $('chip-nguon');
  const nutPhat = $('nut-phat-tam-dung');
  const nutTruoc = $('nut-truoc');
  const nutSau = $('nut-sau');
  const nutDung = $('nut-dung');
  if (!phien || !phien.item) {
    ten.textContent = 'Chưa có dữ liệu.';
    kenh.hidden = true; trenLoa.hidden = true; chip.hidden = true;
    anh.hidden = true;
    nutPhat.setAttribute('aria-label', 'Phát');
    nutPhat.title = 'Phát';
    $('nut-phat-play').toggleAttribute('hidden', false); $('nut-phat-pause').toggleAttribute('hidden', true);
    nutPhat.disabled = true;
    nutTruoc.disabled = true; nutSau.disabled = true; nutDung.disabled = true;
    $('thoi-gian').textContent = '';
    $('tien-do').style.width = '0%';
    return;
  }
  const muc = phien.item;
  ten.textContent = muc.title || muc.id || 'Không rõ tên bài';
  const kenhChu = muc.channel || muc.artist || '';
  kenh.hidden = !kenhChu;
  if (kenhChu) kenh.textContent = kenhChu;
  chip.hidden = false;
  chip.textContent = nhanNguon(muc.source);
  const outputs = phien.outputs || phien.output_entity_ids || [];
  trenLoa.hidden = false;
  trenLoa.textContent = outputs.length
    ? 'Trên ' + outputs.map(tenLoa).join(', ')
    : 'Chưa chọn loa/tivi.';
  if (muc.thumbnail) {
    anh.src = muc.thumbnail;
    anh.alt = '';
    anh.hidden = false;
  } else {
    anh.hidden = true;
  }
  // NV304c: Zing/Facebook đã giải được luồng nên không còn cảnh báo
  // «chưa giải được» — bài chọn sẽ phát thật như YouTube.
  const dangPhat = phien.state === 'playing' || phien.status === 'playing';
  const tamDung = phien.state === 'paused' || phien.status === 'paused';
  nutPhat.disabled = false;
  nutPhat.setAttribute('aria-label', dangPhat ? 'Tạm dừng' : 'Phát');
  nutPhat.title = dangPhat ? 'Tạm dừng' : 'Phát';
  $('nut-phat-play').toggleAttribute('hidden', dangPhat);
  $('nut-phat-pause').toggleAttribute('hidden', !dangPhat);
  nutTruoc.disabled = false;
  nutSau.disabled = false;
  nutDung.disabled = !(dangPhat || tamDung);
  veTienDo();
}

function veTienDo() {
  const phien = phienHienTai;
  if (!phien || !phien.item) return;
  const tong = Number(phien.duration || (phien.item && phien.item.duration) || 0);
  const tyLe = tong > 0 ? Math.min(100, (viTriHien / tong) * 100) : 0;
  $('tien-do').style.width = tyLe + '%';
  $('thanh-tien-do').setAttribute('aria-valuenow', String(Math.round(tyLe)));
  $('thoi-gian').textContent = tong > 0
    ? dinhDangGiay(viTriHien) + ' / ' + dinhDangGiay(tong)
    : (viTriHien > 0 ? dinhDangGiay(viTriHien) : '');
}

function batDongHoTick() {
  if (dongHoTick) return;
  dongHoTick = setInterval(() => {
    const phien = phienHienTai;
    if (!phien) return;
    const dangPhat = phien.state === 'playing' || phien.status === 'playing';
    const tong = Number(phien.duration || (phien.item && phien.item.duration) || 0);
    if (dangPhat && tong > 0 && viTriHien < tong) {
      viTriHien += 1;
      veTienDo();
    }
  }, 1000);
}

function veNhom() {
  const o = $('ds-nhom');
  o.replaceChildren();
  if (!cacPhien.length) {
    o.append(el('li', 'phu', 'Chưa có nhóm nào đang phát.'));
    return;
  }
  cacPhien.forEach((p) => {
    const li = el('li', 'hang-kq');
    if (phienChon === p.session_id) li.classList.add('phien-dang-chon');
    const nd = el('div', 'nd');
    const muc = p.item || {};
    nd.append(el('div', 'tieu-de-bai', muc.title || p.session_id));
    const outputs = p.outputs || p.output_entity_ids || [];
    nd.append(el('div', 'kenh',
      (p.state === 'playing' ? 'Đang phát' : p.state === 'paused' ? 'Tạm dừng' : 'Đã dừng') +
      (outputs.length ? ' · Trên ' + outputs.map(tenLoa).join(', ') : '')));
    li.append(nd);
    li.style.cursor = 'pointer';
    li.addEventListener('click', () => {
      phienChon = p.session_id;
      phienHienTai = p;
      viTriHien = Number(p.position || 0);
      veDangPhat();
      veNhom();
      veQueue();
    });
    o.append(li);
  });
}

function veQueue() {
  const phien = phienHienTai;
  const tomTat = $('queue-tom-tat');
  const o = $('ds-queue');
  o.replaceChildren();
  if (!phien || !phien.queue || !(phien.queue.items || []).length) {
    tomTat.textContent = 'Chưa có phiên nào đang chạy.';
    $('dem-queue').textContent = '0/0';
    return;
  }
  const items = phien.queue.items;
  const chiSo = phien.queue.index || phien.queue_index || 0;
  tomTat.textContent = 'Bài ' + (chiSo + 1) + '/' + items.length + ' trong hàng đợi.';
  $('dem-queue').textContent = (chiSo + 1) + '/' + items.length;
  items.forEach((muc, i) => {
    const li = el('li', 'hang-kq');
    const nd = el('div', 'nd');
    nd.append(el('div', 'tieu-de-bai', (i === chiSo ? '▶ ' : '') + (muc.title || muc.id)));
    nd.append(el('div', 'kenh', (muc.channel || muc.artist || '') +
      (muc.duration ? ' · ' + dinhDangGiay(muc.duration) : '')));
    li.append(nd);
    const nutXoa = el('button', 'nut nut-nho', 'Xoá');
    nutXoa.type = 'button';
    nutXoa.addEventListener('click', async () => {
      try {
        const du = await hanhDong('xoa_khoi_hang_doi', { phien: phien.session_id, chi_so: i });
        if (du.daHuy) return;
        await taiTrangThai();
      } catch (e) {
        if (String(e.message) !== 'het_phien') hienLoi('loi-chung', e.message);
      }
    });
    const nutPhatTuDay = el('button', 'nut nut-nho', 'Phát từ đây');
    nutPhatTuDay.type = 'button';
    nutPhatTuDay.addEventListener('click', async () => {
      try {
        // Phát từ vị trí này, GIỮ NGUYÊN hàng đợi (hành động riêng của
        // module — không phát bài lẻ làm mất hàng đợi).
        const du = await hanhDong('phat_tu_hang_doi', { phien: phien.session_id, chi_so: i });
        if (du.daHuy) return;
        await taiTrangThai();
      } catch (e) {
        if (String(e.message) !== 'het_phien') hienLoi('loi-chung', e.message);
      }
    });
    const hangNut = el('div', 'hang-nut');
    hangNut.append(nutPhatTuDay, nutXoa);
    li.append(hangNut);
    o.append(li);
  });
}

async function taiTrangThai(nen = false) {
  try {
    const [tt, nhom] = await Promise.all([
      hanhDongDoc('trang_thai', {}, false, nen),
      hanhDongDoc('cac_phien', {}, false, nen),
    ]);
    if (tt.daHuy || nhom.daHuy) return;
    cacPhien = nhom.cac_phien || [];
    // Phiên đang hiển thị: phiên đã chọn (nếu còn sống), không thì phiên
    // hiện tại của module.
    const chonConSong = cacPhien.find((p) => p.session_id === phienChon);
    if (phienChon && !chonConSong) phienChon = null;
    phienHienTai = chonConSong || tt.phien || null;
    if (phienHienTai) viTriHien = Number(phienHienTai.position || 0);
    veDangPhat();
    veNhom();
    veQueue();
    hienLoi('loi-chung', '');
  } catch (e) {
    if (String(e.message) !== 'het_phien') {
      $('ten-bai').textContent = 'Chưa có dữ liệu.';
      hienLoi('loi-chung', e.message);
    }
  }
}

/* ── tìm kiếm ─────────────────────────────────────────────────────── */
/* NV304b: nút tròn SVG cho kết quả tìm kiếm (đúng demo). SVG dựng bằng
   createElementNS — KHÔNG innerHTML, giữ quy ước NV301 (dữ liệu ngoài
   chỉ qua textContent); ba biểu tượng đều là hằng trong mã, không chứa
   dữ liệu ngoài. Nhãn đầy đủ nằm ở aria-label + title. */
const NS_SVG = 'http://www.w3.org/2000/svg';
function svgKhoi() {
  const s = document.createElementNS(NS_SVG, 'svg');
  s.setAttribute('viewBox', '0 0 24 24');
  s.setAttribute('aria-hidden', 'true');
  return s;
}
function svgDuong(s, d) {
  const p = document.createElementNS(NS_SVG, 'path');
  p.setAttribute('d', d);
  p.setAttribute('fill', 'none');
  p.setAttribute('stroke', 'currentColor');
  p.setAttribute('stroke-width', '1.8');
  p.setAttribute('stroke-linecap', 'round');
  p.setAttribute('stroke-linejoin', 'round');
  s.append(p);
  return s;
}
function svgPhat() {
  const s = svgKhoi();
  const p = document.createElementNS(NS_SVG, 'path');
  p.setAttribute('d', 'M8 5.5v13l11-6.5z');
  p.setAttribute('fill', 'currentColor');
  s.append(p);
  return s;
}
function svgHangDoi() {
  return svgDuong(svgDuong(svgKhoi(), 'M4 6h12M4 12h12M4 18h8'), 'M18 15v6M15 18h6');
}
function svgPlaylist() {
  const s = svgKhoi();
  svgDuong(s, 'M9 18V6l10-2v11');
  ['M6.5 18a2.5 2.5 0 1 0 0 .01', 'M16.5 15a2.5 2.5 0 1 0 0 .01'].forEach((d) => {
    const c = document.createElementNS(NS_SVG, 'path');
    c.setAttribute('d', d);
    c.setAttribute('fill', 'none');
    c.setAttribute('stroke', 'currentColor');
    c.setAttribute('stroke-width', '1.8');
    s.append(c);
  });
  return s;
}
function nutTron(nhan, veSvg, lopThem) {
  const b = el('button', 'nut-tron' + (lopThem ? ' ' + lopThem : ''));
  b.type = 'button';
  b.setAttribute('aria-label', nhan);
  b.title = nhan;
  b.append(veSvg());
  return b;
}

function nutKetQua(muc) {
  const li = el('li', 'hang-kq');
  if (muc.thumbnail) {
    const img = el('img');
    img.src = muc.thumbnail;
    img.alt = '';
    img.loading = 'lazy';
    li.append(img);
  }
  const nd = el('div', 'nd');
  nd.append(el('div', 'tieu-de-bai', muc.title || muc.id));
  nd.append(el('div', 'kenh', (muc.channel || (muc.artists || []).join(', ') || '') +
    (muc.duration ? ' · ' + dinhDangGiay(muc.duration) : '')));
  li.append(nd);
  const mucTieu = muc.source === 'zing' ? (muc.url || muc.id) : muc.id;
  const nutPhat = nutTron('Phát', svgPhat, 'nut-chinh');
  nutPhat.addEventListener('click', async () => {
    try {
      const loa = loaDaChon();
      const thamSo = { muc_tieu: mucTieu, nguon: muc.source };
      if (loa.length) thamSo.thiet_bi = loa;
      if (muc.title) thamSo.tieu_de = muc.title;
      if (muc.duration) thamSo.thoi_luong = muc.duration;
      const du = await hanhDong('phat', thamSo);
      if (du.daHuy) return;
      await taiTrangThai();
    } catch (e) {
      if (String(e.message) !== 'het_phien') hienLoi('loi-tim', e.message);
    }
  });
  const nutQueue = nutTron('Thêm vào Queue', svgHangDoi);
  nutQueue.addEventListener('click', async () => {
    try {
      const thamSo = { muc_tieu: mucTieu, nguon: muc.source };
      if (phienHienTai) thamSo.phien = phienHienTai.session_id;
      if (muc.title) thamSo.tieu_de = muc.title;
      const du = await hanhDong('them_vao_hang_doi', thamSo);
      if (du.daHuy) return;
      await taiTrangThai();
    } catch (e) {
      if (String(e.message) !== 'het_phien') hienLoi('loi-tim', e.message);
    }
  });
  const nutPl = nutTron('Thêm vào Playlist', svgPlaylist);
  nutPl.addEventListener('click', async () => {
    try {
      const pid = $('o-playlist-nhan').value;
      if (!pid) {
        hienLoi('loi-tim', 'Chưa có playlist — tạo playlist mới ở mục Playlist trước.');
        return;
      }
      const thamSo = { playlist_id: pid, muc_tieu: mucTieu, nguon: muc.source };
      if (muc.title) thamSo.tieu_de = muc.title;
      const du = await hanhDong('them_vao_playlist', thamSo);
      if (du.daHuy) return;
      await taiPlaylist();
      hienLoi('loi-tim', '');
    } catch (e) {
      if (String(e.message) !== 'het_phien') hienLoi('loi-tim', e.message);
    }
  });
  const hangNut = el('div', 'hang-nut');
  hangNut.append(nutPhat, nutQueue, nutPl);
  li.append(hangNut);
  return li;
}

async function timKiem() {
  const tuKhoa = $('o-tu-khoa').value.trim();
  if (!tuKhoa) {
    hienLoi('loi-tim', 'Gõ từ khoá hoặc dán link rồi bấm Tìm.');
    return;
  }
  hienLoi('loi-tim', '');
  try {
    const du = await hanhDongDoc('tim', { tu_khoa: tuKhoa, nguon: nguonTim, gioi_han: 20 });
    if (du.daHuy) return;
    const o = $('ds-ket-qua');
    o.replaceChildren();
    const ds = du.ket_qua || [];
    $('tieu-de-ket-qua').textContent = 'Kết quả (' + ds.length + ' mục)';
    if (!ds.length) o.append(el('li', 'phu', 'Không tìm thấy bài nào.'));
    ds.forEach((muc) => o.append(nutKetQua(muc)));
  } catch (e) {
    if (String(e.message) !== 'het_phien') hienLoi('loi-tim', e.message);
  }
}

/* ── playlist ─────────────────────────────────────────────────────── */
async function taiPlaylist() {
  try {
    const du = await hanhDongDoc('danh_sach_playlist', {});
    if (du.daHuy) return;
    playlists = du.danh_sach || [];
    $('dem-playlist').textContent = String(playlists.length);
    const chon = $('o-playlist-nhan');
    chon.replaceChildren();
    if (!playlists.length) {
      chon.append(el('option', '', 'Chưa có playlist'));
      chon.value = '';
    }
    playlists.forEach((p) => {
      const opt = el('option', '', p.name + ' (' + (p.items || []).length + ' bài)');
      opt.value = p.id;
      chon.append(opt);
    });
    const o = $('ds-playlist');
    o.replaceChildren();
    if (!playlists.length) {
      o.append(el('li', 'phu', 'Chưa có playlist nào. Nhập link/mã ở trên hoặc tạo mới.'));
      return;
    }
    playlists.forEach((p) => {
      const li = el('li', 'hang-kq');
      const nd = el('div', 'nd');
      nd.append(el('div', 'tieu-de-bai', p.name));
      nd.append(el('div', 'kenh', (p.items || []).length + ' bài'));
      li.append(nd);
      const nutPhatPl = el('button', 'nut nut-nho nut-chinh', 'Phát');
      nutPhatPl.type = 'button';
      nutPhatPl.addEventListener('click', async () => {
        try {
          const du = await hanhDong('phat_playlist', { playlist_id: p.id });
          if (du.daHuy) return;
          await taiTrangThai();
        } catch (e) {
          if (String(e.message) !== 'het_phien') hienLoi('loi-playlist', e.message);
        }
      });
      const nutXoa = el('button', 'nut nut-nho', 'Xoá');
      nutXoa.type = 'button';
      nutXoa.addEventListener('click', async () => {
        if (!window.confirm('Xoá playlist «' + p.name + '»?')) return;
        try {
          const du = await hanhDong('xoa_playlist', { playlist_id: p.id });
          if (du.daHuy) return;
          await taiPlaylist();
        } catch (e) {
          if (String(e.message) !== 'het_phien') hienLoi('loi-playlist', e.message);
        }
      });
      const nutMo = el('button', 'nut nut-nho', 'Xem bài');
      nutMo.type = 'button';
      nutMo.addEventListener('click', () => {
        const cu = li.querySelector('ul.ds-bai');
        if (cu) { cu.remove(); return; }
        const ul = el('ul', 'ds-kq ds-bai');
        (p.items || []).forEach((muc, i) => {
          const liBai = el('li', 'hang-kq');
          const ndBai = el('div', 'nd');
          ndBai.append(el('div', 'tieu-de-bai', muc.title || muc.id));
          ndBai.append(el('div', 'kenh', (muc.channel || '') +
            (muc.duration ? ' · ' + dinhDangGiay(muc.duration) : '')));
          liBai.append(ndBai);
          const nutTuDay = el('button', 'nut nut-nho', 'Phát từ đây');
          nutTuDay.type = 'button';
          nutTuDay.addEventListener('click', async () => {
            try {
              const du = await hanhDong('phat_playlist', { playlist_id: p.id, chi_so: i });
              if (du.daHuy) return;
              await taiTrangThai();
            } catch (e) {
              if (String(e.message) !== 'het_phien') hienLoi('loi-playlist', e.message);
            }
          });
          liBai.append(nutTuDay);
          ul.append(liBai);
        });
        li.append(ul);
      });
      const hangNut = el('div', 'hang-nut');
      hangNut.append(nutPhatPl, nutMo, nutXoa);
      li.append(hangNut);
      o.append(li);
    });
  } catch (e) {
    if (String(e.message) !== 'het_phien') hienLoi('loi-playlist', e.message);
  }
}

/* ── loa & tivi ───────────────────────────────────────────────────── */
async function napLoa() {
  const o = $('ds-thiet-bi');
  try {
    const kq = await goi('api/thiet-bi?module=home-assistant');
    const ds = ((kq.du && kq.du.thuc_the) || [])
      .filter((e) => typeof e.id === 'string' && e.id.indexOf('home_assistant.media_player.') === 0);
    o.replaceChildren();
    if (!ds.length) {
      $('bao-chua-co-ha').hidden = false;
      return;
    }
    $('bao-chua-co-ha').hidden = true;
    ds.forEach((e) => {
      const hauTo = e.id.replace(/^home_assistant\./, '');
      loaTen[hauTo] = e.ten || hauTo.split('.').pop();
      const lb = el('label', 'lua-chon');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.value = hauTo;
      const chu = el('span');
      chu.append(el('span', 'ten', e.ten || hauTo));
      lb.append(cb, chu);
      o.append(lb);
    });
  } catch (e) {
    if (String(e.message) !== 'het_phien') hienLoi('loi-loa', e.message);
  }
}

/* ── khởi động ────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-mo-muc]').forEach((nut) => {
    nut.addEventListener('click', () => {
      const muc = nut.getAttribute('data-mo-muc');
      const nd = $('nd-' + muc);
      if (!nd) return;
      const mo = nd.hidden;
      nd.hidden = !mo;
      nut.setAttribute('aria-expanded', mo ? 'true' : 'false');
    });
  });
  document.querySelectorAll('[data-cuon-toi]').forEach((nut) => {
    nut.addEventListener('click', () => {
      const dich = $(nut.getAttribute('data-cuon-toi'));
      if (dich) dich.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });

  const chips = { youtube: $('chip-yt'), zing: $('chip-zing'), facebook: $('chip-fb') };
  const ghiChu = {
    youtube: 'Tìm trên YouTube: gõ từ khoá hoặc dán link bài/playlist.',
    zing: 'Tìm trên Zing MP3: gõ từ khoá.',
    facebook: 'Facebook chỉ nhận link dán vào (không tìm theo từ khoá).',
  };
  Object.entries(chips).forEach(([nguon, nut]) => {
    nut.addEventListener('click', () => {
      nguonTim = nguon;
      Object.entries(chips).forEach(([n, b]) => {
        b.setAttribute('aria-pressed', n === nguon ? 'true' : 'false');
        b.classList.toggle('nut-chinh', n === nguon);
      });
      $('ghi-chu-nguon').textContent = ghiChu[nguon];
    });
  });

  $('nut-tim').addEventListener('click', timKiem);
  $('o-tu-khoa').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); timKiem(); }
  });
  $('nut-truoc').addEventListener('click', async () => {
    try {
      const du = await hanhDong('bai_truoc', phienHienTai ? { phien: phienHienTai.session_id } : {});
      if (du.daHuy) return;
      await taiTrangThai();
    } catch (e) {
      if (String(e.message) !== 'het_phien') hienLoi('loi-chung', e.message);
    }
  });
  $('nut-sau').addEventListener('click', async () => {
    try {
      const du = await hanhDong('bai_ke', phienHienTai ? { phien: phienHienTai.session_id } : {});
      if (du.daHuy) return;
      await taiTrangThai();
    } catch (e) {
      if (String(e.message) !== 'het_phien') hienLoi('loi-chung', e.message);
    }
  });
  $('nut-phat-tam-dung').addEventListener('click', async () => {
    try {
      if (!phienHienTai) return;
      const dangPhat = phienHienTai.state === 'playing' || phienHienTai.status === 'playing';
      const du = await hanhDong(dangPhat ? 'tam_dung' : 'phat', { phien: phienHienTai.session_id });
      if (du.daHuy) return;
      await taiTrangThai();
    } catch (e) {
      if (String(e.message) !== 'het_phien') hienLoi('loi-chung', e.message);
    }
  });
  $('nut-dung').addEventListener('click', async () => {
    try {
      const du = await hanhDong('dung', phienHienTai ? { phien: phienHienTai.session_id } : {});
      if (du.daHuy) return;
      phienChon = null;
      await taiTrangThai();
    } catch (e) {
      if (String(e.message) !== 'het_phien') hienLoi('loi-chung', e.message);
    }
  });
  $('thanh-tien-do').addEventListener('click', async (e) => {
    try {
      const phien = phienHienTai;
      if (!phien) return;
      const tong = Number(phien.duration || (phien.item && phien.item.duration) || 0);
      if (tong <= 0) return;
      const hcn = e.currentTarget.getBoundingClientRect();
      const tyLe = Math.min(1, Math.max(0, (e.clientX - hcn.left) / hcn.width));
      const du = await hanhDong('tua', { phien: phien.session_id, giay: Math.floor(tyLe * tong) });
      if (du.daHuy) return;
      await taiTrangThai();
    } catch (err) {
      if (String(err.message) !== 'het_phien') hienLoi('loi-chung', err.message);
    }
  });
  $('nut-man-hinh').addEventListener('click', () => {
    const bao = $('bao-man-hinh');
    bao.hidden = !bao.hidden;
    if (!bao.hidden) {
      bao.textContent = 'Tab này điều khiển loa/tivi — tiếng phát ra loa nên tắt ' +
        'màn hình vẫn nghe được, không cần giữ tab mở sau khi đã phát.';
    }
  });
  $('nut-nhap').addEventListener('click', async () => {
    try {
      const text = $('o-nhap-text').value.trim();
      if (!text) {
        hienLoi('loi-playlist', 'Dán link playlist YouTube hoặc mã chia sẻ TTPL1. rồi bấm Nhập.');
        return;
      }
      const thamSo = { text };
      const ten = $('o-nhap-ten').value.trim();
      if (ten) thamSo.ten = ten;
      const du = await hanhDong('nhap_playlist', thamSo);
      if (du.daHuy) return;
      const bao = $('bao-playlist');
      bao.hidden = false;
      bao.textContent = 'Đã nhập playlist «' + ((du.playlist || {}).name || '') + '».';
      $('o-nhap-text').value = '';
      $('o-nhap-ten').value = '';
      hienLoi('loi-playlist', '');
      await taiPlaylist();
    } catch (e) {
      if (String(e.message) !== 'het_phien') hienLoi('loi-playlist', e.message);
    }
  });
  $('nut-tao').addEventListener('click', async () => {
    try {
      const ten = $('o-tao-ten').value.trim();
      if (!ten) {
        hienLoi('loi-playlist', 'Gõ tên playlist mới rồi bấm Tạo.');
        return;
      }
      const du = await hanhDong('tao_playlist', { ten });
      if (du.daHuy) return;
      $('o-tao-ten').value = '';
      hienLoi('loi-playlist', '');
      await taiPlaylist();
    } catch (e) {
      if (String(e.message) !== 'het_phien') hienLoi('loi-playlist', e.message);
    }
  });
  $('nut-doi-loa').addEventListener('click', async () => {
    try {
      const loa = loaDaChon();
      if (!loa.length) {
        hienLoi('loi-loa', 'Chọn ít nhất một loa/tivi trước.');
        return;
      }
      const du = await hanhDong('doi_loa', {
        thiet_bi: loa,
        ...(phienHienTai ? { phien: phienHienTai.session_id } : {}),
      });
      if (du.daHuy) return;
      hienLoi('loi-loa', '');
      await taiTrangThai();
    } catch (e) {
      if (String(e.message) !== 'het_phien') hienLoi('loi-loa', e.message);
    }
  });

  taiTrangThai(true);
  taiPlaylist();
  napLoa();
  batDongHoTick();
  setInterval(() => taiTrangThai(true), 20000);
});
