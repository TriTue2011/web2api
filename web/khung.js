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
   *   trang     — dòng là một trang «chính» ở thanh trên (đường tương đối tới trang)
   *   thuoc     — dòng là một phần (chế độ / tab con) của trang mang mã đó
   *   neo       — tab con mở bằng neo #<neo> của trang (không có: tab mặc định của trang)
   *   bieu_tuong — tên hình trong BIEU_TUONG (thanh trên / thanh dưới điện thoại); thiếu thì hình chung
   */
  const DIEU_KIEN = {
    module: { nhan: 'Module', can: [] },
    ai: { nhan: 'AI', can: [AI], pham_vi: 'da_cai' },
    tai_khoan: { nhan: 'Tài khoản', can: [AI], pham_vi: 'da_cai', thuoc: 'ai', neo: 'tai-khoan', bieu_tuong: 'tai_khoan' },
    provider: { nhan: 'Provider', can: [AI], thuoc: 'ai', neo: 'provider', bieu_tuong: 'provider' },
    combo: { nhan: 'Combo', can: [AI], toi_thieu: 2, chua_co: 'Combo chưa có backend ở bản này', thuoc: 'ai', neo: 'combo', bieu_tuong: 'combo' },
    studio: { nhan: 'Studio', can: ['chat'], pham_vi: 'da_cai', trang: 'Studio.dc.html', bieu_tuong: 'chat' },
    studio_chat: { nhan: 'Chat', can: ['chat'], pham_vi: 'da_cai', thuoc: 'studio' },
    studio_anh: { nhan: 'Ảnh', can: ['anh'], pham_vi: 'da_cai', thuoc: 'studio' },
    studio_video: { nhan: 'Video', can: ['video'], pham_vi: 'da_cai', thuoc: 'studio' }
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
    for (const x of [m.loai].concat(Array.isArray(m.cung_cap) ? m.cung_cap : [])) if (x) dem[x] = 1;
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
  // Nơi thêm khoá cho một module: tab Tài khoản (Hệ thống → AI) nếu module thuộc tab đó, ngược lại bảng chi tiết của
  // chính nó ở Hệ thống → Module. Neo theo id để tới ĐÚNG module khi có nhiều. `tab`: mã dòng của nơi đến.
  function noiThemKhoa(m, tn) {
    const id = encodeURIComponent((m && m.id) || '');
    return moduleDapUng(m, 'tai_khoan') && du('tai_khoan', tn)
      ? { tab: 'tai_khoan', href: 'He-thong-AI.dc.html#tk-' + id, nhan: 'Hệ thống → AI → ' + DIEU_KIEN.tai_khoan.nhan }
      : { tab: 'module', href: 'He-thong.dc.html#module-' + id, nhan: 'Hệ thống → ' + DIEU_KIEN.module.nhan };
  }
  // Module đã cài, CHƯA liên kết, đáp ứng dòng `ma` (vd chế độ Chat) — ai cần khoá để chế độ đó gọi được /v1.
  const chuaKhoaCho = (ma, tn) => ((tn && tn.modules) || []).filter((m) => m && !m.da_lien_ket && moduleDapUng(m, ma));

  // ---------- khoá quản trị ----------
  // localStorage: cách trang cũ đã lưu (cùng tên khoá). Có thể bị chặn (ẩn danh, chặn dữ liệu trang) → khoá chỉ sống
  // trong bộ nhớ của trang tới khi đóng trang: `khoaTrang` giữ nó, để MỌI lời gọi đọc khoá qua docKhoa (goi() mặc
  // định) vẫn mang đúng khoá vừa dán — không thì lời gọi đi không khoá, nhận 401 và trang tưởng khoá sai.
  let khoaTrang = '';
  function docKhoa() { try { return localStorage.getItem(KHOA_LUU) || khoaTrang; } catch (e) { return khoaTrang; } }
  function luuKhoa(k) { khoaTrang = k; try { localStorage.setItem(KHOA_LUU, k); return true; } catch (e) { return false; } }

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
    quay_lai: 'M19 12H5 M11 6l-6 6 6 6',
    xuong: 'M6 9l6 6 6-6'
  };
  // Thanh trên của trang chính: các dòng `trang` của DIEU_KIEN đủ điều kiện (Studio…) + tab riêng của module đã liên
  // kết có `tab_web`. Mỗi mục: {id, nhan, href, d (hình), ac (aria-current)}.
  function navChinh(tn, hienTai) {
    const ra = [];
    for (const [ma, dk] of Object.entries(DIEU_KIEN)) {
      if (dk.trang && du(ma, tn)) ra.push({ id: ma, nhan: dk.nhan, href: dk.trang, d: BIEU_TUONG[dk.bieu_tuong] || BIEU_TUONG.module });
    }
    for (const t of (tn && tn.tab) || []) {
      const href = lienKetAnToan(t.duong);
      if (href) ra.push({ id: 'm-' + t.module, nhan: t.nhan, href, d: BIEU_TUONG.module });
    }
    return ra.map((x) => Object.assign(x, { ac: x.id === hienTai ? 'page' : 'false' }));
  }
  // Thanh dưới của trang chính trên điện thoại (`nav.bn` của Main.dc.html): cùng các mục của thanh trên + «Hệ thống».
  function navDuoi(tn, hienTai) {
    return navChinh(tn, hienTai).concat([{ id: 'he-thong', nhan: 'Hệ thống', href: 'He-thong.dc.html', d: BIEU_TUONG.he_thong, ac: 'false' }]);
  }
  // Mục của trang Hệ thống, theo thứ tự thanh Hệ thống của demo (Tổng quan, AI ▾, Tiện ích, Nhật ký ▾, Cấu hình…):
  // chỉ vẽ mục có thật — «AI ▾» (khi có module mang mã «ai» đã cài) rồi «Module» ở đúng chỗ ô «Tiện ích» của demo
  // (chủ máy 11/10: tab quản lý module mang nhãn «Module»). Mục còn lại của demo chưa có backend → không vẽ.
  // `soTb`: số module mới/có bản mới trên git (GET /api/modules → thong_bao_so) → chấm đỏ ở mục Module.
  // Mục AI có danh sách con — chỉ các dòng DIEU_KIEN `thuoc: 'ai'` ĐANG đủ điều kiện; dòng `chua_co` (Combo) không vào.
  function navHeThong(tn, hienTai, soTb) {
    const n = Number(soTb) || 0;
    const ra = [];
    if (du('ai', tn)) {
      const con = Object.entries(DIEU_KIEN).filter(([ma, dk]) => dk.thuoc === 'ai' && du(ma, tn))
        .map(([ma, dk]) => ({ id: ma, nhan: dk.nhan, d: BIEU_TUONG[dk.bieu_tuong] || BIEU_TUONG.module, href: 'He-thong-AI.dc.html' + (dk.neo ? '#' + dk.neo : '') }));
      ra.push({ id: 'ai', nhan: 'AI', href: 'He-thong-AI.dc.html', d: BIEU_TUONG.ai, cham: false, nhanCham: '', con });
    }
    ra.push({ id: 'module', nhan: 'Module', href: 'He-thong.dc.html', d: BIEU_TUONG.phich, cham: n > 0, nhanCham: n + ' module mới hoặc có bản mới trên git', con: [] });
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

  window.Khung = { AI, DIEU_KIEN, thieu, du, dungDuoc, dkMuc, tenMa, coMa, moduleDapUng, trangChinhCua, noiThemKhoa, chuaKhoaCho, docKhoa, luuKhoa, thongDiep, taoLoi, goi, taiTinhNang,
    BIEU_TUONG, navChinh, navDuoi, navHeThong, trangThaiChung, menuNav, quayLai, trangDau, chanNeuThieu, loiThieu, lienKetAnToan, dl, ngayGio, boDau, diaChiV1,
    dongKhoa, cauHinh, mdLienKet, phanTichMarkdown, markdownRaDom };
})();
