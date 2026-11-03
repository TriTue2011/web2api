/* Tab YouTube (NV301 mảnh tab) — chỉ ĐỌC trạng thái qua hành động thực thể
   của lõi (phiên quản trị). Không gọi thẳng đường riêng của module (đường đó
   cần khoá khách, trình duyệt không có) và không giữ bí mật nào. Nút điều khiển
   (phát/dừng/chuyển bài/…) đi theo mảnh lõi và mảnh giao diện. */
'use strict';

const $ = (id) => document.getElementById(id);

function hienLoi(chu) {
  const o = $('loi-chung');
  if (!o) return;
  o.textContent = chu || '';
  o.hidden = !chu;
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

const CHU_TRANG_THAI = {
  chua_phat: 'Chưa phát bài nào.',
  dang_phat: 'Đang phát.',
  dung: 'Đã dừng.',
};

async function taiTrangThai() {
  try {
    const kq = await goi('api/thiet-bi?module=youtube');
    if (!kq.r.ok) throw new Error('Không đọc được danh sách thiết bị (HTTP ' + kq.r.status + ').');
    const ds = ((kq.du || {}).thuc_the || []);
    const thucThe = ds.find((e) => String(e.id || '').startsWith('youtube.'));
    if (!thucThe) throw new Error('Chưa thấy thực thể YouTube — module có thể chưa chạy.');
    const hd = await goi('api/thiet-bi/' + encodeURIComponent(thucThe.id) + '/hanh-dong',
      { hanh_dong: 'trang_thai', tham_so: {} });
    if (!hd.r.ok) throw new Error('Không đọc được trạng thái (HTTP ' + hd.r.status + ').');
    const du = hd.du || {};
    if (du.ok === false) throw new Error(du.loi || 'Không đọc được trạng thái.');
    const o = $('trang-thai');
    if (o) o.textContent = CHU_TRANG_THAI[du.trang_thai] || 'Chưa có dữ liệu.';
    const bai = $('bai-hien-tai');
    const phien = du.phien || null;
    const ten = phien && (phien.title || (phien.current_item && (phien.current_item.title || phien.current_item.id)));
    if (bai) {
      bai.hidden = !ten;
      if (ten) bai.textContent = 'Bài: ' + ten;
    }
    hienLoi('');
  } catch (e) {
    const o = $('trang-thai');
    if (o) o.textContent = 'Chưa có dữ liệu.';
    hienLoi(e && e.message ? e.message : 'Không tải được trạng thái.');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  taiTrangThai();
  setInterval(taiTrangThai, 20000);
});
