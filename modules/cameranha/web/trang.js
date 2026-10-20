// Trang Camera nhà (module camera) — JS thuần, chống XSS bằng textContent/createElement.
// Bố cục theo khuôn vnc-xa: không gán HTML thô, không chèn chuỗi thành thẻ.
'use strict';
(() => {
  const MODULE = 'cameranha';
  const GOC = new URL('../../', location.href);
  const $ = (id) => document.getElementById(id);
  if (window.top !== window) document.documentElement.classList.add('trong-khung');

  let csrfToken = '';
  let dsCam = [];

  function the(ten, lop, chu) {
    const e = document.createElement(ten);
    if (lop) e.className = lop;
    if (chu !== undefined) e.textContent = chu;
    return e;
  }
  function thongBao(chu, loai) {
    const o = $('thong-bao'); o.className = 'bao' + (loai ? ' bao-' + loai : '');
    o.textContent = chu || ''; o.hidden = !chu;
  }
  function baoLoi(chu) { const o = $('loi'); o.textContent = chu || ''; o.hidden = !chu; }

  async function layCsrf() {
    if (csrfToken) return csrfToken;
    try {
      const r = await fetch(new URL('api/toi', GOC), { credentials: 'same-origin', cache: 'no-store' });
      if (r.ok) { const j = await r.json(); if (j && j.csrf) { csrfToken = j.csrf; return csrfToken; } }
    } catch (e) {}
    return '';
  }

  async function goi(duong, than) {
    const method = than === undefined ? 'GET' : 'POST';
    const hd = {};
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
      hd['Content-Type'] = 'application/json';
      const csrf = await layCsrf();
      if (csrf) hd['X-CSRF-Token'] = csrf;
    }
    const r = await fetch(new URL(duong, GOC), {
      method, headers: hd, credentials: 'same-origin',
      body: than === undefined ? undefined : JSON.stringify(than),
      cache: 'no-store',
    });
    let j = null;
    try { j = await r.json(); } catch (e) {}
    if (!r.ok) {
      const msg = (j && (j.loi || j.chi_tiet)) || ('Lỗi ' + r.status);
      throw new Error(String(msg).slice(0, 300));
    }
    return j;
  }

  async function hanhDong(idTT, tenHD, thamSo) {
    const kq = await goi('api/thiet-bi/' + encodeURIComponent(idTT) + '/hanh-dong',
      { hanh_dong: tenHD, tham_so: thamSo || {}, xac_nhan: false, ly_do: '' });
    return kq || {};
  }

  async function taiDanhSach() {
    baoLoi('');
    dsCam = [];
    try {
      const ds = await goi('api/thiet-bi?module=' + encodeURIComponent(MODULE));
      dsCam = (Array.isArray(ds) ? ds : []).filter((e) => String(e.loai || '') === 'camera');
    } catch (e) {
      baoLoi('Không tải được danh sách camera: ' + e.message);
      return;
    }
    const vung = $('ds-cam');
    vung.innerHTML = '';
    if (!dsCam.length) {
      vung.appendChild(the('p', 'phu', 'Chưa có camera nào (module chưa được cấu hình).'));
    }
    for (const cam of dsCam) {
      vung.appendChild(veTheCam(cam));
    }
    taiCanhBao();
  }

  function veTheCam(cam) {
    const hop = the('article', 'the-cam');
    hop.appendChild(the('h3', '', cam.ten || cam.id));
    const hang = the('div', 'hang-nut');
    const nutChup = the('button', 'nut nut-nho', 'Chụp');
    nutChup.type = 'button';
    nutChup.addEventListener('click', () => chup(cam, hop));
    hang.appendChild(nutChup);
    hop.appendChild(hang);
    const anh = the('img');
    anh.alt = 'Khung ảnh của ' + (cam.ten || cam.id);
    anh.hidden = true;
    hop.appendChild(anh);
    const phu = the('p', 'phu nho');
    hop.appendChild(phu);
    return hop;
  }

  async function chup(cam, hop) {
    const anh = hop.querySelector('img');
    const phu = hop.querySelector('p.phu');
    thongBao('');
    try {
      const kq = await hanhDong(cam.id, 'chup', {});
      const tt = kq.trang_thai_moi || {};
      if (kq.ok && tt.anh_base64) {
        anh.src = 'data:image/jpeg;base64,' + tt.anh_base64;
        anh.hidden = false;
        const kt = Array.isArray(tt.kich_thuoc) ? tt.kich_thuoc.join('×') + ' px' : '';
        phu.textContent = 'Chụp xong' + (kt ? ' — ' + kt : '');
      } else {
        thongBao('Chụp không được: ' + (kq.loi || 'lỗi không rõ'), 'loi');
      }
    } catch (e) {
      thongBao('Chụp không được: ' + e.message, 'loi');
    }
  }

  async function taiCanhBao() {
    const vung = $('ds-canh-bao');
    vung.innerHTML = '';
    const camDau = dsCam[0];
    if (!camDau) { vung.appendChild(the('p', 'phu', 'Chưa có camera nào.')); return; }
    try {
      const kq = await hanhDong(camDau.id, 'canh_bao_moi_nhat', {});
      const ds = (kq && kq.canh_bao) || [];
      if (!ds.length) { vung.appendChild(the('p', 'phu', 'Chưa có cảnh báo nào.')); return; }
      const ul = the('ul', 'ds-cb');
      for (const cb of ds) {
        const li = the('li');
        li.textContent = (cb.luc || '') + ' · ' + (cb.loai || 'chuyen_dong') +
          (cb.camera ? ' · ' + cb.camera : '');
        ul.appendChild(li);
      }
      vung.appendChild(ul);
    } catch (e) {
      vung.appendChild(the('p', 'phu', 'Không tải được cảnh báo.'));
    }
  }

  $('lam-moi').addEventListener('click', taiDanhSach);
  document.addEventListener('DOMContentLoaded', taiDanhSach);
})();
