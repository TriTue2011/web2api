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
 * Mọi đường dẫn là TƯƠNG ĐỐI: web2api nằm sau proxy ở thư mục con vẫn chạy đúng.
 */
(() => {
  'use strict';
  const KHOA_LUU = 'web2api_khoa'; // cùng tên với trang cũ: khoá đã lưu trước đây vẫn dùng được

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
   */
  const DIEU_KIEN = {
    module: { nhan: 'Module', can: [] },
    ai: { nhan: 'AI', can: [AI], pham_vi: 'da_cai' },
    tai_khoan: { nhan: 'Tài khoản', can: [AI], pham_vi: 'da_cai' },
    provider: { nhan: 'Provider', can: [AI] },
    combo: { nhan: 'Combo', can: [AI], toi_thieu: 2, chua_co: 'Combo chưa có backend ở bản này' },
    studio: { nhan: 'Studio', can: ['chat'] },
    studio_chat: { nhan: 'Chat', can: ['chat'] },
    studio_anh: { nhan: 'Ảnh', can: ['anh'] },
    studio_video: { nhan: 'Video', can: ['video'] }
  };

  const tenMa = (ma, tn) => {
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
  // Mục do module khai: chỉ hiện khi CHÍNH module đó đã liên kết và mọi mã trong `can` có ở module đã liên kết.
  const dkMuc = (muc) => ({ can: Array.isArray(muc.can) ? muc.can : [], module: muc.module });

  // ---------- khoá quản trị ----------
  // localStorage: cách trang cũ đã lưu (cùng tên khoá). Có thể bị chặn (ẩn danh, chặn dữ liệu trang) → khoá chỉ sống
  // tới khi đóng trang, trang tự báo.
  function docKhoa() { try { return localStorage.getItem(KHOA_LUU) || ''; } catch (e) { return ''; } }
  function luuKhoa(k) { try { localStorage.setItem(KHOA_LUU, k); return true; } catch (e) { return false; } }

  // ---------- gọi API ----------
  // Lỗi kiểu OpenAI {"error":{"message","code"}} là chuẩn của web2api; {"detail"} phòng khi FastAPI tự trả.
  const thongDiep = (j, ma) => (j && j.error && j.error.message) || (j && typeof j.detail === 'string' && j.detail)
    || ('máy chủ trả HTTP ' + ma);
  function taoLoi(tin, ma, code) { const e = new Error(tin); e.ma = ma; e.code = code || null; return e; }
  // `khoa`: khoá dùng cho lời gọi (mặc định khoá đã lưu); `chapNhan`: mã lỗi mà thân vẫn mang kết quả nghiệp vụ.
  async function goi(duong, tc) {
    tc = tc || {};
    const hd = {};
    const khoa = tc.khoa !== undefined ? tc.khoa : docKhoa();
    if (khoa) hd.Authorization = 'Bearer ' + khoa;
    let than;
    if (tc.json !== undefined) { hd['Content-Type'] = 'application/json'; than = JSON.stringify(tc.json); }
    const r = await fetch(duong, { method: tc.method || 'GET', headers: hd, body: than, cache: 'no-store' });
    let j = null;
    try { j = await r.json(); } catch (e) { j = null; }
    if (!r.ok && !(tc.chapNhan || []).includes(r.status)) {
      throw taoLoi(r.status === 401 ? 'khoá quản trị sai hoặc thiếu (máy chủ trả 401)' : thongDiep(j, r.status), r.status,
        j && j.error && j.error.code);
    }
    return { ma: r.status, j };
  }
  const taiTinhNang = () => goi('api/tinh-nang', { khoa: '' }).then(({ j }) => j);

  // ---------- điều hướng ----------
  // Thanh trên của trang chính: Studio (nếu đủ điều kiện) + tab riêng của module đã liên kết có `tab_web`.
  function navChinh(tn, hienTai) {
    const ra = [];
    if (du('studio', tn)) ra.push({ id: 'studio', nhan: 'Studio', href: 'Studio.dc.html' });
    for (const t of (tn && tn.tab) || []) {
      const href = lienKetAnToan(t.duong);
      if (href) ra.push({ id: 'm-' + t.module, nhan: t.nhan, href });
    }
    return ra.map((x) => Object.assign(x, { ac: x.id === hienTai ? 'page' : 'false' }));
  }
  // Mục con của trang Hệ thống (thanh trên khi đang ở Hệ thống, như bản demo).
  // `soTb`: số module mới/có bản mới trên git (GET /api/modules → thong_bao_so) → chấm đỏ ở mục Module.
  function navHeThong(tn, hienTai, soTb) {
    const n = Number(soTb) || 0;
    const ra = [{ id: 'module', nhan: 'Module', href: 'He-thong.dc.html', cham: n > 0, nhanCham: n + ' module mới hoặc có bản mới trên git' }];
    if (du('ai', tn)) ra.push({ id: 'ai', nhan: 'AI', href: 'He-thong-AI.dc.html' });
    return ra.map((x) => Object.assign(x, { ac: x.id === hienTai ? 'page' : 'false' }));
  }
  // «Quay lại» của Hệ thống: về trang chính đầu tiên đang có; chưa có trang chính nào (chưa liên kết gì) → null.
  function quayLai(tn) { const n = navChinh(tn, ''); return n.length ? n[0] : null; }
  function trangDau(tn) { return du('studio', tn) ? 'Studio.dc.html' : 'He-thong.dc.html'; }
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
    cf(comp, id) { return (comp.state.cf || {})[id] || { ch: null, gt: {}, bot: {}, lan: 0, dang: false, kq: null, loi: '' }; },
    dat(comp, id, phan) {
      comp.setState((s) => ({ cf: Object.assign({}, s.cf, { [id]: Object.assign({}, cauHinh.cf({ state: s }, id), phan) }) }));
    },
    async tai(comp, id) {
      try {
        const { j } = await goi('api/modules/' + encodeURIComponent(id) + '/cau-hinh');
        cauHinh.dat(comp, id, { ch: j, loiTai: '' });
      } catch (e) { cauHinh.dat(comp, id, { loiTai: e.message }); }
    },
    vm(comp, id, tienTo, daXong) {
      const f = cauHinh.cf(comp, id);
      const ch = f.ch;
      const gt = (ch && ch.gia_tri) || {};
      const truong = ((ch && ch.truong_cau_hinh) || []).map((t, j) => {
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
      const kq = f.kq;
      return {
        coForm: !!ch, dangTai: !ch && !f.loiTai, loiTai: f.loiTai || '', truong, khongTruong: !!ch && truong.length === 0,
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
      if (kq.ok) { await cauHinh.tai(comp, id); if (daXong) daXong(id); }
    }
  };

  window.Khung = { AI, DIEU_KIEN, thieu, du, dkMuc, tenMa, docKhoa, luuKhoa, thongDiep, taoLoi, goi, taiTinhNang,
    navChinh, navHeThong, quayLai, trangDau, chanNeuThieu, loiThieu, lienKetAnToan, dl, ngayGio, boDau, diaChiV1,
    dongKhoa, cauHinh };
})();
