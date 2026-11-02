/*
 * khung.js — phần DÙNG CHUNG của các trang web2api (Studio, Hệ thống → Module, Hệ thống → AI).
 *
 * dc-lite (support.js) dựng mỗi trang từ khuôn mẫu của chính trang đó và không có "include", nên khuôn thanh
 * trên vẫn nằm trong từng trang; còn LOGIC chung nằm ở đây: gọi API, khoá quản trị, bảng điều kiện của khung
 * và MỘT hàm đánh giá điều kiện duy nhất (`thieu`) dùng cho cả tab của khung lẫn `muc` do module khai.
 *
 * Nguyên tắc: khung KHÔNG biết tên module hay nhà cung cấp nào. Mọi thứ hiện/ẩn suy từ GET /api/tinh-nang
 * (lõi đếm mã `loai` + `cung_cap` của module đã cài / đã liên kết). Thêm module mới = thả thư mục module;
 * thêm tính năng mới của khung = thêm MỘT dòng vào DIEU_KIEN bên dưới.
 *
 * Tab «chính» (Studio…) hiện khi module ĐÃ CÀI (chủ máy 11/10: «chưa khoá cũng ra web chính, người dùng tự thêm
 * khoá trong Hệ thống → AI»); trong đó, gọi được /v1 hay chưa là CÙNG điều kiện ấy xét ở phạm vi ĐÃ LIÊN KẾT
 * (`dungDuoc`) — chưa đủ thì trang hiện trạng thái rỗng chỉ chỗ thêm khoá, không ẩn.
 *
 * Mọi đường dẫn là TƯƠNG ĐỐI: web2api nằm sau proxy ở thư mục con vẫn chạy đúng.
 */
(() => {
  'use strict';
  // Dọn khoá quản trị cũ từ phiên bản trước còn sót lại trong storage trình duyệt (SEC-002)
  try {
    localStorage.removeItem('web2api_khoa');
    sessionStorage.removeItem('web2api_khoa');
  } catch (e) { /* nuốt lỗi khi storage bị chặn */ }

  // Biến bộ nhớ phiên: csrfToken và thông tin người dùng nạp từ GET /api/toi.
  // KHÔNG lưu vào localStorage/sessionStorage/cookie (luật cứng bảo mật).
  let csrfToken = '';
  let nguoiDungHienTai = null;

  // Mã khả năng mà các trang «AI» của khung xoay quanh. Đây là điều kiện của KHUNG (theo tính năng — bảng ở
  // KE-HOACH-GIAO-DIEN-MODULE.md mục 3), không phải tên module: module nào khai `loai`/`cung_cap` chứa mã này
  // đều được tính, module mới không phải sửa khung.
  const AI = 'ai';

  /*
   * Bảng điều kiện của KHUNG. Mỗi dòng cùng dạng với `muc` của module:
   *   can       — các mã phải có (mã loại hoặc mã khả năng, tập MỞ)
   *   pham_vi   — 'lien_ket' (mặc định: đếm module ĐÃ LIÊN KẾT) hoặc 'da_cai' (đếm module đã cài)
   *   toi_thieu — số module tối thiểu cho mỗi mã (mặc định 1)
   *   chua_co   — tính năng chưa có backend: KHÔNG BAO GIỜ hiện, kể cả khi đủ điều kiện
   *   trang     — dòng là một trang «chính» ở thanh trên (đường tương đối tới trang)
   *   thuoc     — dòng là một phần (chế độ / tab con) của trang mang mã đó
   *   neo       — tab con mở bằng neo #<neo> của trang (không có: tab mặc định của trang)
   *   bieu_tuong — tên hình trong BIEU_TUONG (thanh trên / thanh dưới điện thoại); thiếu thì hình chung
   */
  const DIEU_KIEN = {
    module: { nhan: 'Module', can: [] },
    bao_mat: { nhan: 'Bảo mật & người dùng', can: [] },
    ai: { nhan: 'AI', can: [AI], pham_vi: 'da_cai' },
    tai_khoan: { nhan: 'Tài khoản', can: [AI], pham_vi: 'da_cai', thuoc: 'ai', neo: 'tai-khoan', bieu_tuong: 'tai_khoan' },
    // Provider hiện từ lúc CÀI (chủ máy 11/10: «cài AI rồi phải thấy có tài khoản, có provider như demo») — chưa có khoá
    // thì trang hiện provider với tab Model / Tài khoản rỗng, chỉ chỗ thêm khoá.
    provider: { nhan: 'Provider', can: [AI], pham_vi: 'da_cai', thuoc: 'ai', neo: 'provider', bieu_tuong: 'provider' },
    combo: { nhan: 'Combo', can: [AI], toi_thieu: 2, thuoc: 'ai', neo: 'combo', bieu_tuong: 'combo' },
    studio: { nhan: 'Studio', can: ['chat'], pham_vi: 'da_cai', trang: 'Studio.dc.html', bieu_tuong: 'chat' },
    studio_chat: { nhan: 'Chat', can: ['chat'], pham_vi: 'da_cai', thuoc: 'studio' },
    studio_anh: { nhan: 'Ảnh', can: ['anh'], pham_vi: 'da_cai', thuoc: 'studio' },
    studio_video: { nhan: 'Video', can: ['video'], pham_vi: 'da_cai', thuoc: 'studio' },
    // NV297: ba tab «khu» của thanh chính theo demo — hiện khi có module ĐÃ CÀI mang mã `khu_<tab>`. Mã này do lõi suy từ
    // cấu trúc manifest (`giao_dien` module khai, hoặc `loai`/`nhom_con`/`cung_cap`; app/kham_pha.py giao_dien_cua) và đếm
    // cùng chỗ với loại/cung_cap, nên vẫn là MỘT hàm `thieu` cho mọi điều kiện. Gỡ module cuối của khu thì tab biến mất.
    // Thứ tự dòng = thứ tự trên thanh: Studio · Tác phẩm · Nhà · Đời sống.
    tac_pham: { nhan: 'Tác phẩm', can: ['khu_tac_pham'], pham_vi: 'da_cai', trang: 'Tac-pham.dc.html', bieu_tuong: 'tac_pham' },
    nha: { nhan: 'Nhà', can: ['khu_nha'], pham_vi: 'da_cai', trang: 'Nha.dc.html', bieu_tuong: 'nha' },
    doi_song: { nhan: 'Đời sống', can: ['khu_doi_song'], pham_vi: 'da_cai', trang: 'Doi-song.dc.html', bieu_tuong: 'doi_song' }
  };
  const KHU_TEN_MA = { khu_tac_pham: 'ảnh, video hay tệp tạo ra', khu_nha: 'thiết bị nhà, mạng hay camera', khu_doi_song: 'việc cá nhân, nhạc hay video' };

  const tenMa = (ma, tn) => {
    if (KHU_TEN_MA[ma]) return KHU_TEN_MA[ma];
    const l = ((tn && tn.loai) || []).find((x) => x && x.ma === ma);
    return (l && l.ten) || ma;
  };

  // MỘT hàm đánh giá cho mọi điều kiện (khung + mục của module). Trả [] nếu đủ; ngược lại các câu «còn thiếu gì».
  function thieu(dk, tn) {
    const ra = [];
    if (!dk) return ['điều kiện không xác định'];
    tn = tn || {};
    if (dk.module) {
      const m = (tn.modules || []).find((x) => x && x.id === dk.module);
      if (!m || !m.da_lien_ket) ra.push('liên kết module «' + ((m && m.ten) || dk.module) + '»');
    }
    const daCai = dk.pham_vi === 'da_cai';
    const bang = (daCai ? tn.kha_nang_da_cai : tn.kha_nang_lien_ket) || {};
    const n = dk.toi_thieu || 1;
    for (const ma of dk.can || []) {
      if ((bang[ma] || 0) < n) {
        ra.push((daCai ? 'cài ' : 'liên kết ') + (n > 1 ? n + ' module' : 'một module') + ' có «' + tenMa(ma, tn) + '»'
          + (n > 1 && (bang[ma] || 0) ? ' (đang có ' + bang[ma] + ')' : ''));
      }
    }
    if (dk.chua_co) ra.push(dk.chua_co);
    return ra;
  }
  const du = (ma, tn) => !!tn && thieu(DIEU_KIEN[ma], tn).length === 0;
  // Cùng dòng điều kiện, xét ở phạm vi ĐÃ LIÊN KẾT: tab «chính» đã hiện (đã cài) có gọi được /v1 chưa.
  const dungDuoc = (ma, tn) => !!tn && !!DIEU_KIEN[ma]
    && thieu(Object.assign({}, DIEU_KIEN[ma], { pham_vi: 'lien_ket' }), tn).length === 0;
  // Mục do module khai: chỉ hiện khi CHÍNH module đó đã liên kết và mọi mã trong `can` có ở module đã liên kết.
  const dkMuc = (muc) => ({ can: Array.isArray(muc.can) ? muc.can : [], module: muc.module });

  // ---------- một module so với một dòng điều kiện ----------
  const coMa = (m, ma) => !!m && (m.loai === ma || (Array.isArray(m.cung_cap) && m.cung_cap.includes(ma)));
  // Một module RIÊNG nó có đáp ứng dòng `ma` không (bỏ `toi_thieu`, `chua_co`): dựng «tính năng» chỉ gồm module đó rồi
  // đánh giá bằng chính `thieu` — không đường kiểm thứ hai, không bảng mã nào ngoài DIEU_KIEN.
  function moduleDapUng(m, ma) {
    const dk = DIEU_KIEN[ma];
    if (!m || !dk) return false;
    const dem = {};
    for (const x of [m.loai].concat(Array.isArray(m.cung_cap) ? m.cung_cap : [], m.giao_dien ? ['khu_' + m.giao_dien.khu] : [])) if (x) dem[x] = 1;
    return thieu({ can: dk.can, pham_vi: 'da_cai' }, { kha_nang_da_cai: dem }).length === 0;
  }
  // Trang chính mà module mang lại (đang đủ điều kiện, và module đáp ứng dòng của trang hay một chế độ của nó) —
  // để «Cài» xong chuyển thẳng tới đó. Không có → null (ở lại trang Module).
  function trangChinhCua(id, tn) {
    const m = ((tn && tn.modules) || []).find((x) => x && x.id === id);
    if (!m) return null;
    for (const [ma, dk] of Object.entries(DIEU_KIEN)) {
      if (!dk.trang || !du(ma, tn)) continue;
      const lienQuan = Object.keys(DIEU_KIEN).filter((k) => k === ma || DIEU_KIEN[k].thuoc === ma);
      if (lienQuan.some((k) => moduleDapUng(m, k))) return { id: ma, nhan: dk.nhan, href: dk.trang };
    }
    return null;
  }
  // NV296: nơi thêm khoá/kết nối cho một module. Module thuộc AI → Tài khoản cần ĐĂNG NHẬP (không phải khoá)
  // mở trang Tài khoản đúng module. Mọi module còn lại (thiết bị, dịch vụ ngoài, app có trường bí mật) trỏ về
  // tab «Kết nối» NGAY TRONG bảng chi tiết của chính module đó (Hệ thống → Module, neo #module-<id>) — không
  // sang trang khác; Bảo mật & người dùng chỉ giữ khoá do web2api tự cấp cho dịch vụ ngoài gọi vào.
  // Thanh trạng thái «Chờ khoá», bảng chi tiết và Studio đều dùng chung hàm này nên tự đổi theo.
  // Neo theo id để tới ĐÚNG module khi có nhiều. `tab`: mã dòng của nơi đến.
  // NV289: module khai đường riêng cho dịch vụ ngoài gọi vào (`duong_ngoai`) không có ô nhập khoá — khoá do lõi
  // tự tạo ở khối «Khoá cho dịch vụ ngoài gọi vào» (neo #khoa-dich-vu-ngoai) và chọn module được phép ở đó.
  function noiThemKhoa(m, tn) {
    const id = encodeURIComponent((m && m.id) || '');
    if (m && m.duong_ngoai) return { tab: 'bao_mat', href: 'He-thong-Bao-mat.dc.html#khoa-dich-vu-ngoai', nhan: 'Hệ thống → Bảo mật & người dùng → Khoá cho dịch vụ ngoài gọi vào' };
    return moduleDapUng(m, 'tai_khoan') && du('tai_khoan', tn)
      ? { tab: 'tai_khoan', href: 'He-thong-AI.dc.html#tk-' + id, nhan: 'Hệ thống → AI → ' + DIEU_KIEN.tai_khoan.nhan }
      : { tab: 'ket_noi', href: 'He-thong.dc.html#module-' + id, nhan: 'Hệ thống → Module → Kết nối' };
  }
  // Module đã cài, CHƯA liên kết, đáp ứng dòng `ma` (vd chế độ Chat) — ai cần khoá để chế độ đó gọi được /v1.
  const chuaKhoaCho = (ma, tn) => ((tn && tn.modules) || []).filter((m) => m && !m.da_lien_ket && moduleDapUng(m, ma));

  // ---------- phiên đăng nhập & CSRF ----------
  function datCsrf(token) {
    if (typeof token === 'string') csrfToken = token;
  }
  function layCsrf() { return csrfToken; }
  function layNguoiDung() { return nguoiDungHienTai; }
  const tenNguoiDung = (nd) => (nd && (nd.ten || nd.ten_dang_nhap)) || '';

  async function napToi() {
    let r;
    try {
      r = await fetch('api/toi', { credentials: 'same-origin', cache: 'no-store' });
    } catch (e) {
      throw e;
    }
    let j = null;
    try { j = await r.json(); } catch (e) { j = null; }
    if (r.ok) {
      if (j && j.csrf) csrfToken = j.csrf;
      if (j && !j.ten && j.ten_dang_nhap) j.ten = j.ten_dang_nhap;
      nguoiDungHienTai = j;
      return j;
    }
    if (r.status === 401) {
      csrfToken = '';
      nguoiDungHienTai = null;
      return null;
    }
    throw taoLoi(thongDiep(j, r.status), r.status, j && j.error && j.error.code);
  }

  // Chống chuyển hướng mở (Open Redirect): chuỗi phải bắt đầu '/', rồi phân tích bằng
  // new URL(s, location.origin) và chỉ nhận khi x.origin === location.origin.
  function duongVeHopLe(u) {
    if (typeof u !== 'string') return '';
    const s = u.trim();
    if (!s.startsWith('/')) return '';
    try {
      const x = new URL(s, location.origin);
      if (x.origin === location.origin) {
        return x.pathname + x.search + x.hash;
      }
    } catch (e) { /* nuốt lỗi URL không hợp lệ */ }
    return '';
  }

  function chuyenDangNhap() {
    if (typeof location === 'undefined') return;
    const pathHienTai = location.pathname || '';
    const trangHienTai = pathHienTai.split('/').pop() || '';
    if (trangHienTai === 'Dang-nhap.dc.html' || trangHienTai === 'Thiet-lap.dc.html') return;
    const duongHienTai = location.pathname + location.search + location.hash;
    const ve = duongVeHopLe(duongHienTai);
    location.replace('Dang-nhap.dc.html' + (ve ? '?ve=' + encodeURIComponent(ve) : ''));
  }

  async function dangXuat() {
    if (!csrfToken) {
      try { await napToi(); } catch (e) {}
    }
    let res;
    try {
      res = await fetch('api/dang-xuat', {
        method: 'POST',
        headers: csrfToken ? { 'X-CSRF-Token': csrfToken } : {},
        credentials: 'same-origin',
        cache: 'no-store'
      });
    } catch (e) {
      if (typeof alert === 'function') alert('Không thể kết nối máy chủ để đăng xuất.');
      return;
    }
    if (!res || !res.ok) {
      let msg = 'Đăng xuất thất bại.';
      try {
        const j = await res.json();
        msg = (j && j.error && j.error.message) || (j && j.detail) || msg;
      } catch (_) {}
      if (typeof alert === 'function') alert(msg);
      return;
    }
    try {
      if (typeof localStorage !== 'undefined') {
        const cacKhoa = [];
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i);
          if (k && k.startsWith('web2api_')) cacKhoa.push(k);
        }
        for (const k of cacKhoa) localStorage.removeItem(k);
      }
    } catch (e) { /* nuốt lỗi khi storage bị chặn */ }
    csrfToken = '';
    nguoiDungHienTai = null;
    location.replace('Dang-nhap.dc.html');
  }

  // Tương thích giao diện cũ nếu còn gọi, không lưu đĩa
  function docKhoa() { return ''; }
  function luuKhoa() { return false; }

  // ---------- gọi API ----------
  // Lỗi kiểu OpenAI {"error":{"message","code"}} là chuẩn của web2api; {"detail"} phòng khi FastAPI tự trả.
  const thongDiep = (j, ma) => (j && j.error && j.error.message) || (j && typeof j.detail === 'string' && j.detail)
    || ('máy chủ trả HTTP ' + ma);
  function taoLoi(tin, ma, code) { const e = new Error(tin); e.ma = ma; e.code = code || null; return e; }

  // Step-up: máy chủ đòi xác nhận lại (403 step_up_required) → hỏi mật khẩu trong hộp thoại (type=password, chỉ textContent),
  // gửi api/toi/xac-nhan-lai rồi thử lại đúng MỘT lần. Trả null nếu người dùng bỏ qua.
  function hoiMatKhauXacNhanLai() {
    return new Promise((xong) => {
      const hop = document.createElement('dialog');
      hop.style.cssText = 'max-width:min(92vw,360px);padding:16px;border-radius:12px;border:1px solid #8884';
      const td = document.createElement('p');
      td.textContent = 'Thao tác nguy hiểm cần xác nhận lại. Nhập mật khẩu của bạn để tiếp tục.';
      const o = document.createElement('input');
      o.type = 'password'; o.autocomplete = 'current-password'; o.setAttribute('aria-label', 'Mật khẩu');
      o.style.cssText = 'width:100%;box-sizing:border-box;min-height:44px;margin:8px 0';
      const hang = document.createElement('div');
      hang.style.cssText = 'display:flex;gap:8px;justify-content:flex-end';
      const huy = document.createElement('button'); huy.type = 'button'; huy.textContent = 'Huỷ'; huy.style.minHeight = '44px';
      const dong = document.createElement('button'); dong.type = 'button'; dong.textContent = 'Xác nhận'; dong.style.minHeight = '44px';
      let kq = null;
      huy.onclick = () => hop.close();
      dong.onclick = () => { kq = o.value || null; hop.close(); };
      o.onkeydown = (ev) => { if (ev.key === 'Enter') dong.onclick(); };
      hop.onclose = () => { hop.remove(); xong(kq); };
      hang.append(huy, dong); hop.append(td, o, hang);
      document.body.appendChild(hop);
      hop.showModal(); o.focus();
    });
  }

  async function goi(duong, tc) {
    tc = tc || {};
    const method = (tc.method || 'GET').toUpperCase();
    const hd = Object.assign({}, tc.headers || {});

    // Tự đính X-CSRF-Token cho phương thức không an toàn khi dùng cookie phiên
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
      if (!csrfToken && !hd['X-CSRF-Token']) {
        try { await napToi(); } catch (e) {}
      }
      if (csrfToken && !hd['X-CSRF-Token']) {
        hd['X-CSRF-Token'] = csrfToken;
      }
    }

    if (tc.khoa) {
      hd.Authorization = 'Bearer ' + tc.khoa;
    }

    let than;
    if (tc.json !== undefined) {
      hd['Content-Type'] = 'application/json';
      than = JSON.stringify(tc.json);
    } else if (tc.body !== undefined) {
      than = tc.body;
    }

    const r = await fetch(duong, {
      method,
      headers: hd,
      body: than,
      cache: 'no-store',
      credentials: 'same-origin'
    });

    let j = null;
    try { j = await r.json(); } catch (e) { j = null; }

    const code = j && j.error && j.error.code;

    // L4: CSRF cũ không tự làm mới (403 csrf_token_invalid) ⇒ napToi() rồi thử lại MỘT lần
    if (r.status === 403 && code === 'csrf_token_invalid' && !tc._daThuLaiCsrf) {
      tc._daThuLaiCsrf = true;
      try {
        await napToi();
        if (tc.headers) {
          delete tc.headers['X-CSRF-Token'];
          delete tc.headers['x-csrf-token'];
        }
        if (hd['X-CSRF-Token']) delete hd['X-CSRF-Token'];
        return await goi(duong, tc);
      } catch (_) {}
    }

    // Step-up: hỏi mật khẩu, xác nhận lại rồi thử lại MỘT lần (sai mật khẩu → báo đúng lỗi của máy chủ)
    if (r.status === 403 && code === 'step_up_required' && !tc._daThuLaiStepUp && typeof document !== 'undefined') {
      tc._daThuLaiStepUp = true;
      const mk = await hoiMatKhauXacNhanLai();
      if (mk) {
        await goi('api/toi/xac-nhan-lai', { method: 'POST', json: { mat_khau: mk } });
        return await goi(duong, tc);
      }
    }

    // C2: Chỉ chuyển Đăng nhập khi mã lỗi cho thấy PHIÊN không hợp lệ (unauthorized / invalid_api_key)
    // 401 từ khoá do trang truyền tường minh (tc.khoa) hoặc lỗi khác như sai mật khẩu cũ (incorrect_password)
    // KHÔNG được chuyển đăng nhập.
    const laLoiPhien = r.status === 401 && !tc.khoa && (code === 'unauthorized' || code === 'invalid_api_key');
    if (laLoiPhien) {
      chuyenDangNhap();
    }

    if (!r.ok && !(tc.chapNhan || []).includes(r.status)) {
      throw taoLoi(thongDiep(j, r.status), r.status, code);
    }
    return { ma: r.status, j };
  }

  const taiTinhNang = () => goi('api/tinh-nang').then(({ j }) => j);

  // ---------- điều hướng ----------
  // Hình nét (path SVG 24×24, nét 1.6) của thanh điều hướng — chép từ bản demo. Đây là HÌNH của khung, không phải
  // danh sách module: dòng DIEU_KIEN chọn hình bằng `bieu_tuong`, tab riêng của module dùng hình chung `module`.
  const BIEU_TUONG = {
    logo: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z',
    chat: 'M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z',
    anh: 'M4 5h16v14H4z M4 16l5-5 4 4 3-3 4 4 M9 9.5h.01',
    video: 'M3 6h12v12H3z M15 10l6-3v10l-6-3',
    he_thong: 'M4 7h10 M18 7h2 M4 17h2 M10 17h10 M16 5v4 M8 15v4',
    module: 'M4 4h7v7H4z M13 4h7v7h-7z M4 13h7v7H4z M13 13h7v7h-7z',
    // Thanh Hệ thống của demo (ZNAV): AI, phích cắm (ô «Tiện ích» của demo — ở web2api là «Module»), Tài khoản, Provider, Combo.
    ai: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z M19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z',
    phich: 'M9 3v5 M15 3v5 M6 8h12v3a6 6 0 0 1-12 0z M12 17v4',
    tai_khoan: 'M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z M2.5 20a6.5 6.5 0 0 1 13 0 M16 4.5a3.5 3.5 0 0 1 0 6.5 M18 14a6.5 6.5 0 0 1 3.5 6',
    provider: 'M7 18a4 4 0 0 1-.6-8 5.5 5.5 0 0 1 10.7 1.2A3.4 3.4 0 0 1 17 18z',
    combo: 'M12 3l9 5-9 5-9-5z M3 13l9 5 9-5',
    bao_mat: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
    tac_pham: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
    nha: 'M3 11l9-8 9 8 M5 10v10h14V10 M10 20v-6h4v6',
    doi_song: 'M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10c0 5.6-7 10-7 10z',
    quay_lai: 'M19 12H5 M11 6l-6 6 6 6',
    xuong: 'M6 9l6 6 6-6'
  };
  // Thanh trên của trang chính: các dòng `trang` của DIEU_KIEN đủ điều kiện (Studio · Tác phẩm · Nhà · Đời sống).
  // Mỗi mục: {id, nhan, href, d (hình), ac (aria-current), con (trang con ▾), coCon}. Tab «khu» (Tác phẩm/Nhà/Đời sống) có
  // menu thả ▾ liệt kê các MỤC của khu (mỗi mục một trang con: Nhà → Mạng…), suy từ module đã cài (xem mucCua).
  function navChinh(tn, hienTai) {
    const ra = [];
    for (const [ma, dk] of Object.entries(DIEU_KIEN)) {
      if (!dk.trang || !du(ma, tn)) continue;
      const con = mucCua(tn, ma).map((m) => ({ id: m.id, nhan: m.nhan, d: BIEU_TUONG[dk.bieu_tuong] || BIEU_TUONG.module,
        href: dk.trang + '#' + encodeURIComponent(m.id) }));
      ra.push({ id: ma, nhan: dk.nhan, href: dk.trang, d: BIEU_TUONG[dk.bieu_tuong] || BIEU_TUONG.module, con, wst: '' });
    }
    return ra.map((x) => Object.assign(x, { ac: x.id === hienTai ? 'page' : 'false', coCon: x.con.length > 0 }));
  }
  const MUC_HE_THONG = () => ({ id: 'he-thong', nhan: 'Hệ thống', href: 'He-thong.dc.html', d: BIEU_TUONG.he_thong, ac: 'false', con: [], coCon: false });
  // Thanh dưới của trang chính trên điện thoại (`nav.bn` của Main.dc.html): cùng các mục của thanh trên + «Hệ thống»
  // (tối đa 5 mục: Studio, Tác phẩm, Nhà, Đời sống, Hệ thống).
  function navDuoi(tn, hienTai) {
    return navChinh(tn, hienTai).concat([MUC_HE_THONG()]);
  }
  // Thanh trên đầy đủ của trang chính (có menu thả, «Hệ thống» căn phải) — một chỗ cho Studio, Nhà, Đời sống, Tác phẩm.
  function navTren(comp, tn, hienTai, conHienTai) {
    return menuNav(comp, navChinh(tn, hienTai).concat([Object.assign(MUC_HE_THONG(), { wst: 'margin-left:auto' })]), conHienTai);
  }
  function navDuoiMenu(comp, tn, hienTai, conHienTai) { return menuNav(comp, navDuoi(tn, hienTai), conHienTai); }
  // Mục của trang Hệ thống, theo thứ tự thanh Hệ thống của demo (Tổng quan, AI ▾, Tiện ích, Nhật ký ▾, Cấu hình…):
  // chỉ vẽ mục có thật — «AI ▾» (khi có module mang mã «ai» đã cài) rồi «Module» ở đúng chỗ ô «Tiện ích» của demo
  // (chủ máy 11/10: tab quản lý module mang nhãn «Module»), tiếp theo là các tab riêng của module đã liên kết (tab_web),
  // và cuối cùng là «Bảo mật & người dùng». Mục còn lại của demo chưa có backend → không vẽ.
  // `soTb`: số module mới/có bản mới trên git (GET /api/modules → thong_bao_so) → chấm đỏ ở mục Module.
  // Mục AI có danh sách con — chỉ các dòng DIEU_KIEN `thuoc: 'ai'` ĐANG đủ điều kiện
  // (Combo hiện khi có ≥ 2 module AI đã liên kết, đếm theo dữ liệu thật).
  function navHeThong(tn, hienTai, soTb) {
    const nd = layNguoiDung();
    const laUser = nd && nd.vai !== 'admin';
    const biChan403 = tn && (tn.ma === 403 || tn.status === 403 || (tn.error && tn.error.code === 'permission_error'));
    if (laUser || biChan403) {
      const ra = [
        { id: 'bao_mat', nhan: 'Bảo mật', href: 'He-thong-Bao-mat.dc.html', d: BIEU_TUONG.bao_mat, cham: false, nhanCham: '', con: [] }
      ];
      return ra.map((x) => Object.assign(x, { ac: x.id === hienTai ? 'page' : 'false', coCon: false }));
    }
    const n = Number(soTb) || 0;
    const ra = [];
    if (du('ai', tn)) {
      const con = Object.entries(DIEU_KIEN).filter(([ma, dk]) => dk.thuoc === 'ai' && du(ma, tn))
        .map(([ma, dk]) => ({ id: ma, nhan: dk.nhan, d: BIEU_TUONG[dk.bieu_tuong] || BIEU_TUONG.module, href: 'He-thong-AI.dc.html' + (dk.neo ? '#' + dk.neo : '') }));
      ra.push({ id: 'ai', nhan: 'AI', href: 'He-thong-AI.dc.html', d: BIEU_TUONG.ai, cham: false, nhanCham: '', con });
    }
    ra.push({ id: 'module', nhan: 'Module', href: 'He-thong.dc.html', d: BIEU_TUONG.phich, cham: n > 0, nhanCham: n + ' module mới hoặc có bản mới trên git', con: [] });
    for (const t of (tn && tn.tab) || []) {
      // Tab riêng của module nằm TRONG Hệ thống (trang chủ chứa khung), không rời sang trang rời.
      // NV263: module khai `tab_web.an_khoi_thanh` thì ẨN KHỎI thanh điều hướng —
      // trang vẫn mở được bằng đường dẫn trực tiếp (t.duong) và qua He-thong-Module.dc.html?m=<id>.
      if (t.an_khoi_thanh) continue;
      if (lienKetAnToan(t.duong)) ra.push({ id: 'm-' + t.module, nhan: t.nhan, href: 'He-thong-Module.dc.html?m=' + encodeURIComponent(t.module), d: BIEU_TUONG.module, cham: false, nhanCham: '', con: [] });
    }
    if (!tn || du('bao_mat', tn)) {
      ra.push({ id: 'bao_mat', nhan: 'Bảo mật & người dùng', href: 'He-thong-Bao-mat.dc.html', d: BIEU_TUONG.bao_mat, cham: false, nhanCham: '', con: [] });
    }
    return ra.map((x) => Object.assign(x, { ac: x.id === hienTai ? 'page' : 'false', coCon: x.con.length > 0 }));
  }
  // Mục có danh sách con (AI) mở bảng xổ khi bấm, như demo: thanh trên → bảng xổ dưới nút; thanh dưới điện thoại → tờ
  // nổi phía trên thanh, có mũi nhọn chỉ đúng mục (vị trí theo thứ tự mục, công thức của demo). Trạng thái mở nằm ở
  // comp.state.menuMo (mã mục). `conHienTai`: mã tab con đang xem (đánh dấu aria-current trong bảng xổ).
  function menuNav(comp, ds, conHienTai) {
    const mo = comp.state.menuMo || null;
    const dong = () => comp.setState({ menuMo: null });
    return ds.map((x, i) => {
      const pt = (((i + 0.5) / ds.length) * 100).toFixed(3);
      return Object.assign({}, x, {
        mo: mo === x.id, moChu: mo === x.id ? 'true' : 'false', khongCon: !x.coCon,
        bat: () => comp.setState({ menuMo: mo === x.id ? null : x.id }),
        phim: (e) => { if (e.key === 'Escape') dong(); },
        viTri: 'left:clamp(8px,calc(' + pt + '% - 100px),calc(100% - 208px))', viTriMui: 'left:calc(' + pt + '% - 6px)',
        con: x.con.map((c) => Object.assign({}, c, { ac: c.id === conHienTai ? 'page' : 'false', dong }))
      });
    });
  }
  // Viên trạng thái chung (demo: «Mọi dịch vụ ổn», góc phải trên khung nội dung) — suy từ dữ liệu THẬT, không viết cứng:
  // đỏ khi không đọc được /api/tinh-nang; vàng khi có module đã cài mà chưa liên kết (chưa khoá) — bấm tới nơi thêm khoá;
  // xanh khi mọi module đã cài đều đã liên kết (khoá đã được thử bằng lời gọi thật lúc lưu).
  // `ma` (tuỳ chọn): dòng DIEU_KIEN của trang đang xem — chỉ xét module mang lại trang đó hay một phần của nó (Studio
  // không vàng vì một module loại khác chưa liên kết).
  function trangThaiChung(tn, loi, ma) {
    if (loi) return { lop: 'tt-chung tt-do', chu: 'Không đọc được trạng thái', href: '' };
    if (!tn) return { lop: 'tt-chung tt-xam', chu: 'Đang đọc…', href: '' };
    const dong = ma ? Object.keys(DIEU_KIEN).filter((k) => k === ma || DIEU_KIEN[k].thuoc === ma) : null;
    const cho = (tn.modules || []).filter((m) => m && !m.da_lien_ket && (!dong || dong.some((k) => moduleDapUng(m, k))));
    if (cho.length) {
      return { lop: 'tt-chung tt-vang', chu: 'Chờ khoá', tieu: cho.length + ' module chờ khoá: ' + cho.map((m) => m.ten || m.id).join(', '),
        href: noiThemKhoa(cho[0], tn).href };
    }
    return { lop: 'tt-chung tt-xanh', chu: 'Mọi dịch vụ ổn', href: '' };
  }
  // «Quay lại» của Hệ thống: về trang chính đầu tiên đang có; chưa có trang chính nào → null.
  function quayLai(tn) { const n = navChinh(tn, ''); return n.length ? n[0] : null; }
  // Địa chỉ gốc: trang chính đầu tiên trong DIEU_KIEN đang đủ điều kiện (đã cài là đủ); chưa có → Hệ thống → Module.
  function trangDau(tn) {
    const c = Object.entries(DIEU_KIEN).find(([ma, dk]) => dk.trang && du(ma, tn));
    return c ? c[1].trang : 'He-thong.dc.html';
  }
  // Trang/tab gõ tay mà chưa đủ điều kiện → về trang Module kèm mã tính năng (trang Module tự soạn câu giải thích
  // từ DIEU_KIEN — không đưa chữ tuỳ ý qua URL).
  function chanNeuThieu(ma, tn) {
    if (du(ma, tn)) return false;
    location.replace('He-thong.dc.html?thieu=' + encodeURIComponent(ma));
    return true;
  }
  function loiThieu(ma, tn) {
    const dk = DIEU_KIEN[ma];
    if (!dk) return '';
    const t = thieu(dk, tn);
    return t.length ? '«' + dk.nhan + '» chưa mở được: cần ' + t.join('; ') + '.' : '';
  }

  // ---------- tiện ích ----------
  // {{ }} của dc-lite thoát ký tự HTML nhưng KHÔNG chặn href="javascript:…" → chỉ nhận http(s).
  function lienKetAnToan(u) {
    if (typeof u !== 'string' || !u.trim()) return null;
    try { const x = new URL(u, location.href); return x.protocol === 'http:' || x.protocol === 'https:' ? x.href : null; }
    catch (e) { return null; }
  }
  // Đơn vị nhị phân (1024) cho khớp `docker run --memory` và `free -h`, dấu phẩy thập phân kiểu Việt.
  function dl(b) {
    if (b == null) return null;
    if (b >= 1024 ** 3) return (b / 1024 ** 3).toLocaleString('vi-VN', { maximumFractionDigits: 1 }) + ' GB';
    if (b >= 1024 ** 2) return (b / 1024 ** 2).toLocaleString('vi-VN', { maximumFractionDigits: 1 }) + ' MB';
    return Math.max(1, Math.round(b / 1024)).toLocaleString('vi-VN') + ' KB';
  }
  function ngayGio(s) {
    if (!s) return '';
    const d = new Date(s);
    return isNaN(d) ? String(s) : d.toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  }
  // «3 phút trước» như dòng «lần dùng cuối» của c2a. Giờ máy chủ nhanh hơn trình duyệt (âm) → «vừa xong».
  function tuongDoi(s) {
    if (!s) return '';
    const d = new Date(s);
    if (isNaN(d)) return String(s);
    const giay = Math.max(0, (Date.now() - d.getTime()) / 1000);
    if (giay < 60) return 'vừa xong';
    if (giay < 3600) return Math.floor(giay / 60) + ' phút trước';
    if (giay < 86400) return Math.floor(giay / 3600) + ' giờ trước';
    return Math.floor(giay / 86400) + ' ngày trước';
  }
  // So khớp tìm kiếm không phân biệt dấu tiếng Việt ("gia" khớp "giả").
  const boDau = (s) => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd').replace(/Đ/g, 'D').toLowerCase();
  const diaChiV1 = () => new URL('v1', location.href).href;

  // ---------- một dòng kết quả kiểm khoá / sức khoẻ ----------
  const TT_KHA_NANG = { chua_ro: 'chưa rõ (chưa dùng thật lần nào)', dung_duoc: 'dùng được' };
  function dongKhoa(k) {
    const ok = !!(k && k.ok);
    const ct = [];
    if (k && k.do_tre_ms != null) ct.push(k.do_tre_ms + ' ms');
    if (ok && k.so_model != null) ct.push(k.so_model + ' model');
    if (k && k.dang_nghi_den) ct.push('đang nghỉ đến ' + ngayGio(k.dang_nghi_den));
    if (!ok && k && k.loi) ct.push('Lý do: ' + k.loi);
    // Trạng thái từng khả năng tốn tiền theo khoá (module khai qua suc_khoe; mã HTTP ở trường `ma`).
    const kn = k && k.kha_nang && typeof k.kha_nang === 'object' ? Object.keys(k.kha_nang).map((ma) => {
      const t = k.kha_nang[ma] || {};
      const chu = t.trang_thai === 'bi_tu_choi'
        ? 'bị từ chối' + (t.ma != null ? ' (HTTP ' + t.ma + ')' : '') + (t.thu_lai_luc ? ', thử lại lúc ' + ngayGio(t.thu_lai_luc) : '')
        : (TT_KHA_NANG[t.trang_thai] || String(t.trang_thai || 'không rõ'));
      return { ma, chu, lop: 'huy ' + (t.trang_thai === 'dung_duoc' ? 'huy-xanh' : t.trang_thai === 'bi_tu_choi' ? 'huy-do' : 'huy-xam') };
    }) : [];
    return { khoa: (k && (k.khoa_che || k.khoa)) || '(không khoá)', lop: ok ? 'kq-tot' : 'kq-hong', lopDau: 'dau ' + (ok ? 'dau-tot' : 'dau-hong'),
      ok, hong: !ok, trangThai: ok ? 'dùng được' : 'không dùng được', chiTiet: ct.join(' · '), kn, coKn: kn.length > 0 };
  }

  /*
   * Form «Thêm tài khoản/khoá API» dựng từ `truong_cau_hinh` — dùng chung cho bảng chi tiết (Hệ thống → Module)
   * và Hệ thống → AI → Tài khoản. Trạng thái nằm ở comp.state.cf[id] = {ch, gt, bot, lan, dang, kq, loi}.
   *   - Khoá đã lưu chỉ thấy dạng che (GET …/cau-hinh); bớt TỪNG khoá bằng chỉ số, thêm khoá mới — gửi dạng
   *     {"giu": [chỉ số], "them": [khoá mới]} nên không ai phải dán lại (hay thấy) khoá cũ.
   *   - Ô chuỗi để trống hay không sửa thì KHÔNG gửi: máy chủ giữ giá trị đã lưu (chưa lưu thì module tự dùng
   *     mặc định). Trang cũ gửi đè `mac_dinh` khi ô trống — làm mất base_url người dùng đã đặt.
   *   - Bí mật chỉ nằm trong state lúc đang gõ; gửi đi là xoá, và id ô đổi theo `lan` để trình duyệt dựng ô MỚI rỗng.
   */
  const laBiMat = (t) => t && (t.kieu === 'bi_mat' || t.kieu === 'danh_sach_bi_mat');
  const cauHinh = {
    cf(comp, id) { return (comp.state.cf || {})[id] || { ch: null, gt: {}, bot: {}, sua: {}, nangCao: false, lan: 0, dang: false, kq: null, loi: '' }; },
    dat(comp, id, phan) {
      comp.setState((s) => ({ cf: Object.assign({}, s.cf, { [id]: Object.assign({}, cauHinh.cf({ state: s }, id), phan) }) }));
    },
    async tai(comp, id) {
      try {
        const { j } = await goi('api/modules/' + encodeURIComponent(id) + '/cau-hinh');
        cauHinh.dat(comp, id, { ch: j, loiTai: '' });
      } catch (e) { cauHinh.dat(comp, id, { loiTai: e.message }); }
    },
    /* tuyChon.thuGon (NV296, tab «Kết nối» của bảng chi tiết Module): đã kết nối thì MỖI trường chỉ hiện giá trị
     * đã lưu — bí mật là «••••• đã lưu» (không bao giờ vẽ lại, kể cả dạng che), trường thường là giá trị — kèm nút
     * Đổi/Sửa; bấm mới mở ô nhập. Chưa kết nối thì hiện ô nhập của trường BẮT BUỘC, phần còn lại thu vào «Nâng cao». */
    vm(comp, id, tienTo, daXong, tuyChon) {
      const thuGon = !!(tuyChon && tuyChon.thuGon);
      const f = cauHinh.cf(comp, id);
      const ch = f.ch;
      const gt = (ch && ch.gia_tri) || {};
      const daKetNoi = !!(ch && ch.da_cau_hinh);
      const sua = f.sua || {};
      const datSua = (khoa, bat) => comp.setState((st) => { const c = cauHinh.cf({ state: st }, id);
        const gtMoi = Object.assign({}, c.gt); if (!bat) delete gtMoi[khoa];
        return { cf: Object.assign({}, st.cf, { [id]: Object.assign({}, c, { sua: Object.assign({}, c.sua, { [khoa]: bat }), gt: gtMoi }) }) }; });
      const truongTatCa = ((ch && ch.truong_cau_hinh) || []).map((t, j) => {
        const daLuu = gt[t.khoa];
        const bot = f.bot[t.khoa] || {};
        const coLuu = daLuu != null && daLuu !== '' && !(Array.isArray(daLuu) && !daLuu.length);
        return {
          khoa: t.khoa, nhan: t.nhan || t.khoa, moTa: t.mo_ta || '', batBuoc: !!t.bat_buoc,
          laDs: t.kieu === 'danh_sach_bi_mat', laBiMat: t.kieu === 'bi_mat', laChuoi: !laBiMat(t),
          idO: 'f-' + tienTo + '-' + id + '-' + j + '-' + f.lan,
          giaTri: laBiMat(t) ? '' : (f.gt[t.khoa] != null ? String(f.gt[t.khoa]) : (daLuu == null ? '' : String(daLuu))),
          goiY: !laBiMat(t) && t.mac_dinh != null ? String(t.mac_dinh) : '',
          ghiChuChuoi: !laBiMat(t) ? (coLuu ? 'Để trống hoặc không sửa thì giữ giá trị đang lưu.'
            : (t.mac_dinh != null && t.mac_dinh !== '' ? 'Để trống thì module dùng mặc định: ' + t.mac_dinh : '')) : '',
          biMatLuu: t.kieu === 'bi_mat' && coLuu ? String(daLuu) : '',
          // NV296: dạng thu gọn
          moO: !thuGon || !daKetNoi || !!sua[t.khoa], gon: thuGon && daKetNoi && !sua[t.khoa],
          chuGon: laBiMat(t)
            ? (!coLuu ? 'Chưa có' : t.kieu === 'danh_sach_bi_mat' && Array.isArray(daLuu) ? '••••• ' + daLuu.length + ' khoá đã lưu' : '••••• đã lưu')
            : (coLuu ? String(daLuu) : (t.mac_dinh != null && t.mac_dinh !== '' ? 'Mặc định: ' + t.mac_dinh : 'Chưa đặt')),
          nhanMo: laBiMat(t) ? (coLuu ? 'Đổi' : 'Thêm') : 'Sửa',
          ariaMo: (laBiMat(t) ? (coLuu ? 'Đổi ' : 'Thêm ') : 'Sửa ') + (t.nhan || t.khoa),
          mo: () => { datSua(t.khoa, true); setTimeout(() => { const o = document.getElementById('f-' + tienTo + '-' + id + '-' + j + '-' + cauHinh.cf(comp, id).lan); if (o) o.focus(); }, 30); },
          dong: () => datSua(t.khoa, false), coNutThoi: thuGon && daKetNoi,
          anNangCao: thuGon && !daKetNoi && !t.bat_buoc && !f.nangCao,
          daLuu: t.kieu === 'danh_sach_bi_mat' && Array.isArray(daLuu) ? daLuu.map((che, i) => ({
            i: String(i), che: String(che), bot: !!bot[i], trangThai: bot[i] ? 'sẽ bớt khi lưu' : 'đang lưu',
            nhanNut: bot[i] ? 'Giữ lại' : 'Bớt', aria: (bot[i] ? 'Giữ lại khoá ' : 'Bớt khoá ') + che,
            lop: bot[i] ? 'khoa-bot' : '',
            doi: () => comp.setState((s) => { const c = cauHinh.cf({ state: s }, id); const b = c.bot[t.khoa] || {};
              return { cf: Object.assign({}, s.cf, { [id]: Object.assign({}, c, { bot: Object.assign({}, c.bot, { [t.khoa]: Object.assign({}, b, { [i]: !b[i] }) }) }) }) }; })
          })) : [],
          onInput: (e) => { const v = e.target.value; comp.setState((s) => { const c = cauHinh.cf({ state: s }, id);
            return { cf: Object.assign({}, s.cf, { [id]: Object.assign({}, c, { gt: Object.assign({}, c.gt, { [t.khoa]: v }) }) }) }; }); }
        };
      });
      // Chưa kết nối: trường bắt buộc lên trước; trường tuỳ chọn chỉ hiện khi mở «Nâng cao».
      const truong = (thuGon && !daKetNoi
        ? truongTatCa.filter((t) => t.batBuoc).concat(truongTatCa.filter((t) => !t.batBuoc))
        : truongTatCa).filter((t) => !t.anNangCao);
      const soNangCao = thuGon && !daKetNoi ? truongTatCa.filter((t) => !t.batBuoc).length : 0;
      const kq = f.kq;
      return {
        daKetNoi, coNangCao: soNangCao > 0, nangCaoMo: !!f.nangCao, nhanNangCao: f.nangCao ? 'Ẩn phần nâng cao' : 'Nâng cao (' + soNangCao + ')',
        bamNangCao: () => cauHinh.dat(comp, id, { nangCao: !cauHinh.cf(comp, id).nangCao }),
        coNutLuu: !thuGon || !daKetNoi || Object.keys(sua).some((k) => sua[k]),
        coForm: !!ch, dangTai: !ch && !f.loiTai, loiTai: f.loiTai || '', truong, khongTruong: !!ch && truongTatCa.length === 0,
        luu: (e) => cauHinh.luu(comp, id, e, daXong), dang: !!f.dang, nhanNut: f.dang ? 'Đang kiểm từng khoá…' : 'Kiểm tra và lưu',
        loi: f.loi || '', coKq: !!kq, kqOk: !!(kq && kq.ok), kqLoi: kq && !kq.ok ? kq.loi || 'không rõ lý do' : '',
        kqDs: kq ? (kq.ds || []).map(dongKhoa) : [], kqGoLai: !!(kq && kq.goLai)
      };
    },
    async luu(comp, id, e, daXong) {
      if (e) e.preventDefault();
      const f = cauHinh.cf(comp, id);
      const ch = f.ch;
      if (!ch) return;
      const gt = ch.gia_tri || {};
      const cau_hinh = {}; const thieuTr = []; let coBiMat = false;
      for (const t of ch.truong_cau_hinh || []) {
        const go = f.gt[t.khoa];
        const daLuu = gt[t.khoa];
        if (t.kieu === 'danh_sach_bi_mat') {
          const moi = String(go || '').split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
          const cu = Array.isArray(daLuu) ? daLuu : [];
          const bot = f.bot[t.khoa] || {};
          const giu = cu.map((_, i) => i).filter((i) => !bot[i]);
          if (moi.length || giu.length !== cu.length) cau_hinh[t.khoa] = { giu, them: moi };
          if (moi.length) coBiMat = true;
          if (t.bat_buoc && giu.length + moi.length === 0) thieuTr.push(t.nhan || t.khoa);
        } else if (t.kieu === 'bi_mat') {
          const s = String(go || '').trim();
          if (s) { cau_hinh[t.khoa] = s; coBiMat = true; }
          if (t.bat_buoc && !s && !daLuu) thieuTr.push(t.nhan || t.khoa);
        } else {
          const s = go == null ? '' : String(go).trim();
          if (s && s !== String(daLuu == null ? '' : daLuu)) cau_hinh[t.khoa] = s;
          if (t.bat_buoc && !s && (daLuu == null || daLuu === '')) thieuTr.push(t.nhan || t.khoa);
        }
      }
      if (thieuTr.length) { cauHinh.dat(comp, id, { loi: 'Còn thiếu: ' + thieuTr.join(', ') + '.', kq: null }); return; }
      if (!Object.keys(cau_hinh).length && ch.da_cau_hinh) { cauHinh.dat(comp, id, { loi: 'Chưa có thay đổi nào để lưu.', kq: null }); return; }
      // Xoá bí mật khỏi state TRƯỚC khi gửi; đổi `lan` để dc-lite dựng ô mới rỗng.
      cauHinh.dat(comp, id, { gt: {}, bot: {}, lan: f.lan + 1, dang: true, loi: '', kq: null });
      let kq;
      try {
        const { j } = await goi('api/modules/' + encodeURIComponent(id) + '/cai', { method: 'POST', json: { cau_hinh }, chapNhan: [400] });
        kq = j && typeof j === 'object' && 'ok' in j
          ? { ok: !!j.ok, ds: j.ket_qua_tung_khoa || [], loi: j.loi || (j.ok ? '' : 'module từ chối cấu hình') }
          : { ok: false, ds: [], loi: thongDiep(j, 400) };
      } catch (err) { kq = { ok: false, ds: [], loi: err.message }; }
      kq.goLai = !kq.ok && coBiMat;
      cauHinh.dat(comp, id, { dang: false, kq });
      if (kq.ok) { cauHinh.dat(comp, id, { sua: {}, nangCao: false }); await cauHinh.tai(comp, id); if (daXong) daXong(id); }
    }
  };

  // ---------- NV297: trang của một «khu» (Tác phẩm · Nhà · Đời sống) ----------
  // Ba trang Tac-pham / Nha / Doi-song dùng CHUNG một bộ dựng (đây): trang chỉ khai mã khu. Khung không biết tên module:
  // mục, chip, ô số liệu đều suy từ `giao_dien` của manifest (hay từ cấu trúc, xem app/kham_pha.py giao_dien_cua) và từ thực
  // thể module trả về (GET /api/thiet-bi). Thực thể CHỈ ĐỌC của module quản trị đã cài là đã tin (lõi tính, app/thiet_bi.py);
  // hành động có ghi/nguy hiểm vẫn xác nhận MỘT lần LÚC GỌI ngay trong ô của nó. Không có phân loại rủi ro cho người dùng.
  const tenGon = (m) => String((m && (m.ten || m.id)) || '').split(' (')[0];

  // Các MỤC của một khu (mỗi mục = một trang con ở menu ▾), suy từ module đã cài. Module cùng `giao_dien.muc` dồn chung một
  // mục. Chip của mục = chip các module khai (gộp theo id, xếp theo thu_tu) hoặc, nếu module không khai chip, MỘT chip mang
  // tên module. `loc` của chip: các (module, nhom) đóng góp thực thể cho chip — nhom null = mọi thực thể của module,
  // [] = thực thể KHÔNG gắn nhóm, [..] = thực thể thuộc các nhóm đó. Chip «Cài đặt» luôn ở cuối (thêm ở khu.vm).
  function mucCua(tn, ma) {
    const ra = [];
    for (const m of (tn && tn.modules) || []) {
      const g = m && m.giao_dien;
      if (!g || g.khu !== ma) continue;
      let x = ra.find((y) => y.id === g.muc);
      if (!x) { x = { id: g.muc, nhan: g.nhan, modules: [], chip: [] }; ra.push(x); }
      x.modules.push(m);
      const khai = Array.isArray(g.chip) ? g.chip : [];
      for (const c of khai.length ? khai : [{ id: 'm-' + m.id, nhan: tenGon(m), nhom: null, thu_tu: null }]) {
        let k = x.chip.find((y) => y.id === c.id);
        if (!k) { k = { id: c.id, nhan: c.nhan, thuTu: c.thu_tu == null ? 50 : c.thu_tu, loc: [] }; x.chip.push(k); }
        k.loc.push({ module: m.id, nhom: c.nhom == null ? null : c.nhom });
      }
    }
    for (const x of ra) x.chip = x.chip.map((c, i) => [c, i]).sort((a, b) => a[0].thuTu - b[0].thuTu || a[1] - b[1]).map((p) => p[0]);
    return ra;
  }
  const thucTheCuaChip = (chip, ds) => (ds || []).filter((e) => chip.loc.some((l) => l.module === e.module
    && (l.nhom == null || (l.nhom.length ? l.nhom.includes(e.nhom) : !e.nhom))));

  // Chữ hiển thị của MỘT giá trị đo: tiếng Việt thường, không mã kỹ thuật. Chưa đọc được → «Chưa có dữ liệu» (không số giả).
  const TT_CHU = { bat: 'Bật', tat: 'Tắt', on: 'Bật', off: 'Tắt', len: 'Đang nối', xuong: 'Mất kết nối' };
  function giaTriHienThi(e) {
    const t = e && e.trang_thai;
    if (!t || t.hop_le === false || t.gia_tri == null || t.gia_tri === '') return { chu: 'Chưa có dữ liệu', coDL: false };
    const v = t.gia_tri;
    const dv = t.don_vi ? ' ' + t.don_vi : '';
    if (typeof v === 'number') return { chu: v.toLocaleString('vi-VN', { maximumFractionDigits: 2 }) + dv, coDL: true };
    if (typeof v === 'boolean') return { chu: v ? 'Bật' : 'Tắt', coDL: true };
    const s = String(v);
    return { chu: (TT_CHU[s.toLowerCase()] || (/^[a-z0-9_]+$/.test(s) ? s.replace(/_/g, ' ') : s)) + dv, coDL: true };
  }
  // Hành động của thực thể mà ô điều khiển vẽ được: chỉ loại MỘT tham số bắt buộc kiểu đúng/sai (bật/tắt). Loại khác để
  // API/Studio — không vẽ ô nửa vời.
  function hanhDongDon(e) {
    return ((e && e.tinh_nang) || []).filter((t) => t && typeof t === 'object' && t.hanh_dong).map((t) => {
      const sc = t.tham_so || {}; const bat = sc.required || []; const tt = sc.properties || {};
      return bat.length === 1 && tt[bat[0]] && tt[bat[0]].type === 'boolean' ? { hd: t.hanh_dong, khoa: bat[0], moTa: String(t.mo_ta || '') } : null;
    }).filter(Boolean);
  }
  // Lõi điền `ten` = id khi module không đặt tên: coi như chưa có tên (không để id kỹ thuật lộ ra cho người dùng).
  const tenThat = (e) => (e && e.ten && e.ten !== e.id ? e.ten : '');
  const cauDau = (s) => String(s || '').split(/[.\n]/)[0].trim();

  const khu = {
    trangThaiDau: () => ({ nguoiDung: null, tn: null, tnLoi: '', dangTai: true, thucThe: null, thucTheLoi: '', hangCho: [], mucId: null, chipId: null,
      cf: {}, hdDang: null, hdHoi: null, hdTin: '', hdLoi: '', menuMo: null, pdDang: null, tinNoi: '' }),
    async napDuLieu(comp) {
      try { const { j } = await goi('api/thiet-bi'); comp.setState({ thucThe: (j && j.thuc_the) || [], thucTheLoi: '' }); }
      catch (e) { comp.setState((s) => ({ thucThe: s.thucThe || [], thucTheLoi: e.message })); }
      try { const { j } = await goi('api/phe-duyet'); comp.setState({ hangCho: (j && j.hang_cho) || [] }); } catch (e) { /* người dùng thường không đọc được hàng chờ */ }
    },
    // Địa chỉ #<mục> chọn mục (menu ▾ của thanh trên); chưa chọn → mục đầu. Chip đầu là chip dữ liệu; mục mà MỌI module còn
    // chờ kết nối thì mở thẳng «Cài đặt» (một form ngắn trong chính module). Đã nối rồi thì Cài đặt luôn ẩn.
    chonTheoDiaChi(comp, ma) {
      const ds = mucCua(comp.state.tn, ma);
      const id = decodeURIComponent((location.hash || '').slice(1));
      // ?da_cai=<module>: trang Module vừa cài module đó rồi chuyển tới đây → mở đúng mục của nó, rồi bỏ tham số khỏi địa chỉ.
      const vuaCai = new URLSearchParams(location.search).get('da_cai');
      if (vuaCai) history.replaceState(null, '', location.pathname + location.hash);
      const m = ds.find((x) => x.id === id) || ds.find((x) => x.modules.some((y) => y.id === vuaCai)) || ds[0] || null;
      comp.setState({ mucId: m ? m.id : null, chipId: m && m.modules.every((x) => !x.da_lien_ket) ? 'cai-dat' : null, menuMo: null });
    },
    async didMount(comp, ma) {
      comp._daTai = new Set();
      napToi().then((nd) => { if (nd) comp.setState({ nguoiDung: nd }); }).catch(() => {});
      let tn;
      try { tn = await taiTinhNang(); } catch (e) { comp.setState({ tnLoi: e.message, dangTai: false }); return; }
      if (chanNeuThieu(ma, tn)) return;
      comp.setState({ tn, dangTai: false });
      khu.chonTheoDiaChi(comp, ma);
      window.addEventListener('hashchange', () => khu.chonTheoDiaChi(comp, ma));
      window.addEventListener('pageshow', (e) => { if (e.persisted) khu.lamMoi(comp, ma); });
      await khu.napDuLieu(comp);
      comp._dong = setInterval(() => khu.napDuLieu(comp), 20000);
    },
    willUnmount(comp) { if (comp._dong) clearInterval(comp._dong); },
    async lamMoi(comp, ma) {
      try { const tn = await taiTinhNang(); comp.setState({ tn, tnLoi: '' }); if (chanNeuThieu(ma, tn)) return; } catch (e) { comp.setState({ tnLoi: e.message }); }
      await khu.napDuLieu(comp);
    },
    // Lưu kết nối xong: đọc lại «tính năng đang có» + số liệu rồi về chip dữ liệu đầu của mục, kèm MỘT dòng báo đã nối.
    async sauKetNoi(comp, id, ma) {
      await khu.lamMoi(comp, ma);
      const m = ((comp.state.tn && comp.state.tn.modules) || []).find((x) => x.id === id);
      comp.setState({ chipId: null, tinNoi: 'Đã nối «' + tenGon(m || { id }) + '». Phần cài đặt đã thu gọn — bấm «Cài đặt» khi cần sửa.' });
    },
    async chayHanhDong(comp, e, h, gt, xacNhan) {
      const ten = tenThat(e) || 'thiết bị';
      comp.setState({ hdDang: e.id, hdHoi: null, hdTin: '', hdLoi: '' });
      try {
        await goi('api/thiet-bi/' + encodeURIComponent(e.id) + '/hanh-dong', { method: 'POST', json: { hanh_dong: h.hd, tham_so: { [h.khoa]: gt }, xac_nhan: !!xacNhan } });
        comp.setState({ hdDang: null, hdTin: 'Đã ' + (gt ? 'bật' : 'tắt') + ' «' + ten + '».' });
      } catch (err) { comp.setState({ hdDang: null, hdLoi: 'Chưa làm được: ' + err.message }); }
      await khu.napDuLieu(comp);
    },
    async xuLyHangCho(comp, id, duyet) {
      comp.setState({ pdDang: id, hdLoi: '', hdTin: '' });
      try { await goi('api/phe-duyet/' + encodeURIComponent(id) + (duyet ? '/duyet' : '/tu-choi'), { method: 'POST' }); comp.setState({ pdDang: null, hdTin: duyet ? 'Đã duyệt yêu cầu.' : 'Đã từ chối yêu cầu.' }); }
      catch (err) { comp.setState({ pdDang: null, hdLoi: 'Chưa xử lý được: ' + err.message }); }
      await khu.napDuLieu(comp);
    },
    vm(comp, ma) {
      const s = comp.state; const tn = s.tn;
      const dsMuc = tn ? mucCua(tn, ma) : [];
      const muc = dsMuc.find((x) => x.id === s.mucId) || dsMuc[0] || null;
      const chips = muc ? muc.chip.concat([{ id: 'cai-dat', nhan: 'Cài đặt', loc: [] }]) : [];
      const chipId = chips.some((c) => c.id === s.chipId) ? s.chipId : (chips[0] ? chips[0].id : null);
      const laCaiDat = chipId === 'cai-dat';
      const chon = (id) => () => comp.setState({ chipId: id, hdHoi: null, tinNoi: '' });
      const ds = s.thucThe || [];
      const tenThucThe = (id) => tenThat(ds.find((x) => x.id === id)) || 'Một thiết bị';

      // Chip dữ liệu: ô gọn của thực thể (tên thường, giá trị đọc được) + hành động bật/tắt (nguy hiểm: xác nhận MỘT lần tại ô).
      const chip = chips.find((c) => c.id === chipId);
      const mods = chip && !laCaiDat ? chip.loc.map((l) => muc.modules.find((m) => m.id === l.module)).filter(Boolean) : [];
      const chuaNoi = mods.filter((m) => !m.da_lien_ket).map((m) => ({ ten: tenGon(m), noiNgay: chon('cai-dat') }));
      const noi = new Set(mods.filter((m) => m.da_lien_ket).map((m) => m.id));
      const o = chip && !laCaiDat ? thucTheCuaChip(chip, ds.filter((e) => noi.has(e.module))).map((e) => {
        const g = giaTriHienThi(e);
        const hd = hanhDongDon(e).map((h) => {
          const ttV = e.trang_thai && e.trang_thai.gia_tri;
          return [[true, 'Bật', ttV === 'bat'], [false, 'Tắt', ttV === 'tat']].map(([gt, nhan, dang]) => ({
            nhan, dangChon: dang ? 'true' : 'false', khoa: s.hdDang === e.id,
            bam: () => {
              if (e.rui_ro === 'nguy_hiem') comp.setState({ hdHoi: { id: e.id, h, gt, nhan }, hdTin: '', hdLoi: '' });
              else khu.chayHanhDong(comp, e, h, gt, false);
            }
          }));
        }).reduce((a, b) => a.concat(b), []);
        const hoi = s.hdHoi && s.hdHoi.id === e.id ? s.hdHoi : null;
        return {
          ten: tenThat(e) || (e.loai === 'may_mang' ? 'Máy chưa đặt tên' : 'Thiết bị'),
          phu: (e.thuoc_tinh && e.thuoc_tinh.ip) || '', coPhu: !!(e.thuoc_tinh && e.thuoc_tinh.ip),
          giaTri: g.chu, lopGiaTri: g.coDL ? 'gia-tri' : 'gia-tri chua-co', luc: g.coDL && e.trang_thai.luc ? 'cập nhật ' + tuongDoi(e.trang_thai.luc) : '',
          hd, coHd: hd.length > 0,
          hoiXn: !!hoi, xnChu: hoi ? 'Xác nhận «' + hoi.nhan + '» cho «' + (tenThat(e) || 'thiết bị') + '»? ' + (cauDau(hoi.h.moTa) ? cauDau(hoi.h.moTa) + '. ' : '') + 'Việc này chỉ hỏi một lần, ngay lúc làm.' : '',
          xacNhan: () => khu.chayHanhDong(comp, e, hoi.h, hoi.gt, true),
          thoi: () => comp.setState({ hdHoi: null })
        };
      }) : [];
      const chuaCo = !!chip && !laCaiDat && s.thucThe !== null && o.length === 0 && chuaNoi.length === 0;

      // Chip «Cài đặt» (luôn ẩn tới khi bấm): từng module một khối, giá trị đã lưu thu gọn, bí mật «••••• đã lưu» + Đổi.
      const cd = laCaiDat && muc ? muc.modules.map((m) => {
        if (!comp._daTai.has(m.id)) { comp._daTai.add(m.id); setTimeout(() => cauHinh.tai(comp, m.id), 0); }
        return { id: m.id, ten: tenGon(m), noi: !!m.da_lien_ket, trang: m.da_lien_ket ? 'Đã nối' : 'Chưa nối', lopTrang: 'huy ' + (m.da_lien_ket ? 'huy-xanh' : 'huy-vang'),
          f: cauHinh.vm(comp, m.id, 'khu', (id) => khu.sauKetNoi(comp, id, ma), { thuGon: true }) };
      }) : [];

      const hangCho = (s.hangCho || []).map((pd) => {
        const e = ds.find((x) => x.id === pd.thuc_the);
        const h = e && (e.tinh_nang || []).find((t) => t && t.hanh_dong === pd.hanh_dong);
        return { chu: 'Một ứng dụng dùng khoá khách muốn «' + (cauDau(h && h.mo_ta) || 'thực hiện một thao tác') + '» trên «' + tenThucThe(pd.thuc_the) + '».' + (pd.ly_do ? ' Lý do: ' + pd.ly_do : ''),
          khoa: s.pdDang === pd.id, duyet: () => khu.xuLyHangCho(comp, pd.id, true), tuChoi: () => khu.xuLyHangCho(comp, pd.id, false) };
      });
      return {
        hinh: BIEU_TUONG,
        nav: navTren(comp, tn, ma, muc && muc.id), navDuoi: navDuoiMenu(comp, tn, ma, muc && muc.id),
        menuMo: !!s.menuMo, dongMenu: () => comp.setState({ menuMo: null }),
        tenNguoiDung: tenNguoiDung(s.nguoiDung), nhanVai: s.nguoiDung ? (s.nguoiDung.vai === 'admin' ? 'Quản trị viên' : 'Người dùng') : '', dangXuat: () => dangXuat(),
        tnLoi: s.tnLoi, thucTheLoi: s.thucTheLoi, dangTai: s.dangTai, tenMuc: muc ? muc.nhan : '',
        hangChip: chips.map((c) => ({ id: c.id, nhan: c.nhan, on: c.id === chipId ? 'true' : 'false', chon: chon(c.id) })), coChip: chips.length > 0,
        laDuLieu: !!chip && !laCaiDat, laCaiDat, dangDoc: !!chip && !laCaiDat && s.thucThe === null && mods.some((m) => m.da_lien_ket),
        chuaNoi, coChuaNoi: chuaNoi.length > 0, o, coO: o.length > 0, chuaCo,
        tinNoi: s.tinNoi, hdTin: s.hdTin, hdLoi: s.hdLoi, cd,
        hangCho, coHangCho: hangCho.length > 0, soHangCho: hangCho.length
      };
    }
  };

  /*
   * Markdown của câu trả lời model (Studio) — bộ dựng nhỏ, không thư viện ngoài, KHÔNG có nhánh theo nhà cung cấp:
   * đây là phần hiển thị của ứng dụng khách, như giao diện chat của c2a (react-markdown + remark-gfm).
   *
   * Hai tầng tách bạch:
   *   phanTichMarkdown(chu) — hàm THUẦN: chữ → danh sách khối (đoạn, tiêu đề, khối mã, danh sách, trích dẫn, đường kẻ,
   *                           bảng), mỗi khối chứa đoạn trong dòng (chữ, đậm, nghiêng, gạch, mã, liên kết, xuống dòng).
   *                           Không đụng DOM — chạy được bằng node (test/test_web2api_markdown.py).
   *   markdownRaDom(chu)    — dựng DocumentFragment từ cây trên CHỈ bằng createElement/textContent/setAttribute:
   *                           không innerHTML, nên HTML thô trong câu trả lời (<script>, <img onerror>…) là CHỮ.
   * Liên kết chỉ nhận URL tuyệt đối http/https/mailto (mdLienKet); ảnh ![..](..) và lược đồ khác hiện nguyên văn.
   *
   * Stream từng khúc: dấu mở (**, *, `, ~~) chỉ thành định dạng khi đã có dấu đóng — chưa có thì là chữ thường;
   * khối ``` chưa đóng là khối mã đang mở tới hết chữ; bảng chỉ thành bảng khi dòng kẻ |---| đã tới.
   * Độ lồng (trích dẫn, danh sách, đậm trong nghiêng…) có trần MD_SAU: vượt trần là chữ thường, không đệ quy vô hạn.
   */
  const MD_SAU = 6;
  const RE_RAO = /^( {0,3})(`{3,}|~{3,})[ \t]*([^\s`]*)(.*)$/;
  const RE_TIEU_DE = /^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$/;
  const RE_KE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
  const RE_TRICH = /^ {0,3}> ?(.*)$/;
  const RE_DS = /^( *)([-*+]|(\d{1,9})[.)])(?:[ \t]+(.*))?$/;
  const RE_PHAN_CACH = /^ *\|? *:?-+:? *(\| *:?-+:? *)*\|? *$/;
  const DAU_CAU = /[!-/:-@[-`{-~]/;          // dấu câu ASCII — thoát được bằng «\»
  const CHU_SO = /[\p{L}\p{N}]/u;

  // Lược đồ an toàn cho liên kết trong câu trả lời: chỉ URL TUYỆT ĐỐI http/https/mailto (javascript:, data:… → null).
  function mdLienKet(u) {
    try { const x = new URL(String(u).trim()); return ['http:', 'https:', 'mailto:'].includes(x.protocol) ? x.href : null; }
    catch (e) { return null; }
  }
  const thutDau = (d) => d.length - d.trimStart().length;
  function laRao(d) {
    const m = RE_RAO.exec(d);
    // Rào ``` không được có ` trong phần sau (vd «```a```» trên một dòng là mã trong dòng, không phải khối).
    return m && !(m[2][0] === '`' && m[4].includes('`')) ? m : null;
  }
  const laBangDau = (dong, i) => dong[i].includes('|') && i + 1 < dong.length && dong[i + 1].includes('|')
    && RE_PHAN_CACH.test(dong[i + 1]) && tachO(dong[i]).length === tachO(dong[i + 1]).length;
  // Dòng này có mở một khối khác không (thì đoạn văn đang gom dừng lại). Danh sách đánh số chỉ cắt đoạn khi bắt đầu
  // từ 1 (như CommonMark) — «2024. Năm ấy…» đầu dòng giữa đoạn vẫn là chữ.
  function batDauKhoi(dong, i) {
    const d = dong[i];
    const m = RE_DS.exec(d);
    return !!(laRao(d) || RE_TIEU_DE.test(d) || RE_KE.test(d) || RE_TRICH.test(d) || laBangDau(dong, i)
      || (m && m[4] && (m[3] == null || m[3] === '1')));
  }
  // Ô của một dòng bảng GFM: bỏ | đầu/cuối, tách theo | chưa thoát; «\|» là chữ |.
  function tachO(d) {
    let s = d.trim();
    if (s.startsWith('|')) s = s.slice(1);
    if (s.endsWith('|') && !s.endsWith('\\|')) s = s.slice(0, -1);
    const ra = []; let o = '';
    for (let k = 0; k < s.length; k++) {
      if (s[k] === '\\' && s[k + 1] === '|') { o += '|'; k++; continue; }
      if (s[k] === '|') { ra.push(o.trim()); o = ''; continue; }
      o += s[k];
    }
    ra.push(o.trim());
    return ra;
  }

  function phanKhoi(dong, sau) {
    const ra = []; let i = 0; let m;
    while (i < dong.length) {
      const d = dong[i];
      if (!d.trim()) { i++; continue; }
      if ((m = laRao(d))) {
        const thut = m[1].length, rao = m[2]; const ma = []; let dong_ = false; i++;
        for (; i < dong.length; i++) {
          const t = dong[i].trim();
          if (thutDau(dong[i]) < 4 && t.length >= rao.length && t[0] === rao[0] && /^(`+|~+)$/.test(t)) { dong_ = true; i++; break; }
          ma.push(dong[i].slice(Math.min(thut, thutDau(dong[i]))));
        }
        ra.push({ t: 'ma', ngon_ngu: /^[\w+#.-]{1,24}$/.test(m[3]) ? m[3] : '', chu: ma.join('\n'), mo: !dong_ });
        continue;
      }
      if ((m = RE_TIEU_DE.exec(d))) { ra.push({ t: 'tieu_de', cap: m[1].length, con: dongTrong(m[2] || '', sau) }); i++; continue; }
      if (RE_KE.test(d)) { ra.push({ t: 'ke' }); i++; continue; }
      if (RE_TRICH.test(d)) {
        const con = [];
        while (i < dong.length && dong[i].trim() && (m = RE_TRICH.exec(dong[i]))) { con.push(m[1]); i++; }
        ra.push(sau < MD_SAU ? { t: 'trich', con: phanKhoi(con, sau + 1) } : { t: 'doan', con: dongTrong(con.join('\n'), sau) });
        continue;
      }
      if (laBangDau(dong, i)) {
        const canh = tachO(dong[i + 1]).map((c) => (c.endsWith(':') ? (c.startsWith(':') ? 'center' : 'right') : c.startsWith(':') ? 'left' : ''));
        const dau = tachO(dong[i]); const n = dau.length; const hang = [];
        for (i += 2; i < dong.length && dong[i].trim() && dong[i].includes('|'); i++) {
          const o = tachO(dong[i]).slice(0, n);
          while (o.length < n) o.push('');
          hang.push(o.map((x) => dongTrong(x, sau)));
        }
        ra.push({ t: 'bang', canh, dau: dau.map((x) => dongTrong(x, sau)), hang });
        continue;
      }
      if ((m = RE_DS.exec(d))) {
        // Mục cùng cấp: dòng mang dấu mục cùng kiểu (gạch / số) thụt ≤ thut0+1. Dòng thụt sâu hơn (kể cả sau dòng
        // trống) thuộc mục đang mở — gồm danh sách lồng; bỏ phần thụt rồi phân tích lại như một văn bản con.
        const thut0 = m[1].length, coSo = m[3] != null; const muc = [];
        let cur = null;
        while (i < dong.length) {
          const x = dong[i]; const mm = RE_DS.exec(x);
          if (mm && mm[1].length <= thut0 + 1 && (mm[3] != null) === coSo && !RE_KE.test(x)) {
            cur = { dong: [mm[4] || ''], ci: x.length - (mm[4] || '').length }; muc.push(cur); i++; continue;
          }
          if (!x.trim()) {
            let j = i + 1; while (j < dong.length && !dong[j].trim()) j++;
            if (j < dong.length && thutDau(dong[j]) > thut0 + 1) { cur.dong.push(''); i++; continue; }
            const mj = j < dong.length ? RE_DS.exec(dong[j]) : null;
            if (mj && mj[1].length <= thut0 + 1 && (mj[3] != null) === coSo) { i = j; continue; }
            break;
          }
          if (thutDau(x) > thut0 + 1) { cur.dong.push(x.slice(Math.min(cur.ci, thutDau(x)))); i++; continue; }
          break;
        }
        ra.push({ t: 'ds', so: coSo, bat_dau: coSo ? parseInt(m[3], 10) : 1,
          muc: muc.map((x) => (sau < MD_SAU ? phanKhoi(x.dong, sau + 1) : [{ t: 'doan', con: dongTrong(x.dong.join('\n'), sau) }])) });
        continue;
      }
      const doan = [d]; i++;
      while (i < dong.length && dong[i].trim() && !batDauKhoi(dong, i)) { doan.push(dong[i]); i++; }
      ra.push({ t: 'doan', con: dongTrong(doan.map((x) => x.replace(/^ {0,3}/, '')).join('\n'), sau) });
    }
    return ra;
  }

  // [chữ](url "tiêu đề") bắt đầu ở s[i] === '[' → {chu, url, het} hoặc null. Dò tối đa TRAN_LK ký tự mỗi phần để
  // chuỗi «[[[[…» dài không thành O(n²).
  const TRAN_LK = 2000;
  function docLienKet(s, i) {
    let sau = 0, j = i;
    for (; j < s.length && j - i < TRAN_LK; j++) {
      if (s[j] === '\\') { j++; continue; }
      if (s[j] === '[') sau++;
      else if (s[j] === ']' && --sau === 0) break;
    }
    if (s[j] !== ']' || s[j + 1] !== '(') return null;
    const chu = s.slice(i + 1, j);
    let k = j + 2; while (s[k] === ' ') k++;
    let url = '';
    if (s[k] === '<') {
      const e = s.indexOf('>', k); if (e < 0 || e - k > TRAN_LK) return null;
      url = s.slice(k + 1, e); k = e + 1;
    } else {
      let ngoac = 0; const bd = k;
      for (; k < s.length && k - bd < TRAN_LK; k++) {
        const c = s[k];
        if (c === '\\') { k++; continue; }
        if (/\s/.test(c)) break;
        if (c === '(') ngoac++;
        else if (c === ')') { if (!ngoac) break; ngoac--; }
      }
      url = s.slice(bd, k);
    }
    while (s[k] === ' ') k++;
    const mo = s[k];
    if (mo === '"' || mo === "'" || mo === '(') {
      const e = s.indexOf(mo === '(' ? ')' : mo, k + 1); if (e < 0) return null;
      k = e + 1; while (s[k] === ' ') k++;
    }
    return s[k] === ')' ? { chu, url, het: k + 1 } : null;
  }

  function dongTrong(s, sau, trongLk) {
    const ra = []; let chu = '';
    const day = () => { if (chu) { ra.push({ t: 'chu', v: chu }); chu = ''; } };
    const khongCo = {};   // dấu đóng → vị trí mà từ đó trở đi chắc chắn không còn dấu đóng hợp lệ (tránh dò lại O(n²))
    const cuoiLk = s.lastIndexOf('](');   // sau chỗ này không thể còn liên kết: «[» chỉ là chữ, khỏi dò
    // Dấu đóng của nhấn mạnh: đúng `dau` (không dính thêm ký tự cùng loại), sát chữ bên trái; với «_» bên phải không
    // là chữ/số (snake_case giữ nguyên).
    function timDong(tu, dau) {
      if (khongCo[dau] != null && tu >= khongCo[dau]) return -1;
      const c = dau[0], n = dau.length;
      for (let j = s.indexOf(dau, tu); j >= 0; j = s.indexOf(dau, j + 1)) {
        if (j > tu && !/\s/.test(s[j - 1]) && s[j - 1] !== c && s[j + n] !== c && !(c === '_' && CHU_SO.test(s[j + n] || ''))) return j;
      }
      khongCo[dau] = tu;
      return -1;
    }
    let i = 0;
    while (i < s.length) {
      const c = s[i];
      if (c === '\\' && i + 1 < s.length && DAU_CAU.test(s[i + 1])) { chu += s[i + 1]; i += 2; continue; }
      if (c === '\n') { day(); ra.push({ t: 'xuong' }); i++; continue; }
      if (c === '`') {
        let n = 1; while (s[i + n] === '`') n++;
        let j = i + n, dong_ = -1;
        while ((j = s.indexOf('`'.repeat(n), j)) >= 0) {
          let k = j; while (s[k] === '`') k++;
          if (k - j === n) { dong_ = j; break; }
          j = k;
        }
        if (dong_ < 0) { chu += '`'.repeat(n); i += n; continue; }
        let v = s.slice(i + n, dong_).replace(/\n/g, ' ');
        if (v.length > 2 && v[0] === ' ' && v[v.length - 1] === ' ' && v.trim()) v = v.slice(1, -1);
        day(); ra.push({ t: 'ma', v }); i = dong_ + n; continue;
      }
      if ((c === '[' || (c === '!' && s[i + 1] === '[')) && i < cuoiLk) {
        const lk = docLienKet(s, c === '!' ? i + 1 : i);
        if (lk) {
          const href = c === '[' && !trongLk && sau < MD_SAU ? mdLienKet(lk.url) : null;
          if (href) { day(); ra.push({ t: 'lien_ket', href, con: dongTrong(lk.chu, sau + 1, true) }); }
          else chu += s.slice(i, lk.het);   // ảnh, lược đồ không an toàn, liên kết lồng: hiện nguyên văn như chữ
          i = lk.het; continue;
        }
      }
      if (c === '*' || c === '_' || c === '~') {
        let n = 1; while (s[i + n] === c) n++;
        const ke = s[i + n] || ' ';
        const hop = c === '~' ? n === 2 : n <= 3 && (c === '*' || !CHU_SO.test(s[i - 1] || ''));
        if (hop && sau < MD_SAU && !/\s/.test(ke)) {
          const j = timDong(i + n, c.repeat(n));
          if (j >= 0) {
            const trong = dongTrong(s.slice(i + n, j), sau + 1, trongLk);
            day();
            ra.push(c === '~' ? { t: 'gach', con: trong } : n === 1 ? { t: 'nghieng', con: trong }
              : n === 2 ? { t: 'dam', con: trong } : { t: 'dam', con: [{ t: 'nghieng', con: trong }] });
            i = j + n; continue;
          }
        }
        chu += c.repeat(n); i += n; continue;
      }
      // Địa chỉ trần http(s)://… (như GFM autolink): bỏ dấu câu dính cuối và «)» thừa.
      if ((c === 'h' || c === 'H') && !trongLk && !CHU_SO.test(s[i - 1] || '') && /^https?:\/\//i.test(s.slice(i, i + 8))) {
        let u = /^[^\s<>]+/.exec(s.slice(i, i + TRAN_LK))[0];
        for (;;) {
          if (/[.,:;!?'"*_~]$/.test(u)) u = u.slice(0, -1);
          else if (u.endsWith(')') && (u.match(/\(/g) || []).length < (u.match(/\)/g) || []).length) u = u.slice(0, -1);
          else break;
        }
        const href = mdLienKet(u);
        if (href) { day(); ra.push({ t: 'lien_ket', href, con: [{ t: 'chu', v: u }] }); i += u.length; continue; }
      }
      chu += c; i++;
    }
    day();
    return ra;
  }
  const phanTichMarkdown = (chu) => phanKhoi(String(chu == null ? '' : chu).replace(/\r\n?/g, '\n')
    .split('\n').map((d) => d.replace(/^\t+/, (t) => '    '.repeat(t.length))), 0);

  function markdownRaDom(chu) {
    const tao = (tag) => document.createElement(tag);
    function trongDong(ds, cha) {
      for (const x of ds) {
        if (x.t === 'chu') { cha.appendChild(document.createTextNode(x.v)); continue; }
        if (x.t === 'xuong') { cha.appendChild(tao('br')); continue; }
        if (x.t === 'ma') { const e = tao('code'); e.textContent = x.v; cha.appendChild(e); continue; }
        const e = tao({ dam: 'strong', nghieng: 'em', gach: 'del', lien_ket: 'a' }[x.t]);
        if (x.t === 'lien_ket') {
          e.setAttribute('href', x.href); e.setAttribute('target', '_blank'); e.setAttribute('rel', 'noopener noreferrer');
        }
        trongDong(x.con, e);
        cha.appendChild(e);
      }
      return cha;
    }
    function khoi(ds, cha) {
      for (const k of ds) {
        let e;
        if (k.t === 'doan') e = trongDong(k.con, tao('p'));
        else if (k.t === 'tieu_de') e = trongDong(k.con, tao('h' + k.cap));
        else if (k.t === 'ke') e = tao('hr');
        else if (k.t === 'trich') e = khoi(k.con, tao('blockquote'));
        else if (k.t === 'ma') {
          e = tao('pre');
          if (k.ngon_ngu) e.setAttribute('data-ngon-ngu', k.ngon_ngu);
          if (k.mo) e.setAttribute('data-dang-mo', '');
          const c = tao('code'); c.textContent = k.chu; e.appendChild(c);
        } else if (k.t === 'ds') {
          e = tao(k.so ? 'ol' : 'ul');
          if (k.so && k.bat_dau !== 1) e.setAttribute('start', String(k.bat_dau));
          for (const m of k.muc) e.appendChild(khoi(m, tao('li')));
        } else if (k.t === 'bang') {
          // Bảng rộng cuộn ngang TRONG khung bọc, không banh bong bóng (điện thoại).
          e = tao('div'); e.className = 'md-bang';
          const b = tao('table'); const th = tao('thead'); const tb = tao('tbody'); const r = tao('tr');
          k.dau.forEach((o, j) => { const c = trongDong(o, tao('th')); if (k.canh[j]) c.style.textAlign = k.canh[j]; r.appendChild(c); });
          th.appendChild(r);
          for (const h of k.hang) {
            const rr = tao('tr');
            h.forEach((o, j) => { const c = trongDong(o, tao('td')); if (k.canh[j]) c.style.textAlign = k.canh[j]; rr.appendChild(c); });
            tb.appendChild(rr);
          }
          b.append(th, tb); e.appendChild(b);
        }
        cha.appendChild(e);
      }
      return cha;
    }
    return khoi(phanTichMarkdown(chu), document.createDocumentFragment());
  }
  // <md-chu chu="…">: phần tử tự dựng con từ thuộc tính `chu` bằng markdownRaDom. Dùng trong khuôn mẫu dc-lite như
  // <md-chu chu="{{t.tra}}">: dc-lite chỉ đặt lại thuộc tính khi chữ đổi (mỗi khúc stream) và KHÔNG vá con của
  // custom element đã đăng ký (support.js, ý 6) — nên câu trả lời không bị xoá khi trang dựng lại vì gõ phím.
  if (typeof customElements !== 'undefined' && !customElements.get('md-chu')) {
    customElements.define('md-chu', class extends HTMLElement {
      static get observedAttributes() { return ['chu']; }
      attributeChangedCallback() { this.replaceChildren(markdownRaDom(this.getAttribute('chu') || '')); }
    });
  }

  window.Khung = { AI, DIEU_KIEN, khu, mucCua, navTren, navDuoiMenu, thieu, du, dungDuoc, dkMuc, tenMa, coMa, moduleDapUng, trangChinhCua, noiThemKhoa, chuaKhoaCho, docKhoa, luuKhoa, thongDiep, taoLoi, goi, taiTinhNang,
    BIEU_TUONG, navChinh, navDuoi, navHeThong, trangThaiChung, menuNav, quayLai, trangDau, chanNeuThieu, loiThieu, lienKetAnToan, dl, ngayGio, tuongDoi, boDau, diaChiV1,
    dongKhoa, cauHinh, mdLienKet, phanTichMarkdown, markdownRaDom,
    datCsrf, layCsrf, napToi, layNguoiDung, tenNguoiDung, dangXuat, duongVeHopLe, chuyenDangNhap };
})();
