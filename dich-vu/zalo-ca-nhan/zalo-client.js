// zalo-client.js — bộ chuyển đổi Zalo cá nhân cho dịch vụ dich-vu/zalo-ca-nhan.
//
// NGUỒN PORT (LUẬT PORT của dự án): các lời gọi zca-js trong phần «adapter THẬT»
// được chép từ mã đang chạy của c2a (commit d879b03):
// - zalo-server/api/zalo/zalo.js: loginZaloAccount() (zalo.login(cred),
//   zalo.loginQR(null, cb) với qrData.data.image, api.fetchAccountInfo(),
//   api.getContext() → {imei, cookie, userAgent}, lưu cred_<ownId>.json chmod
//   0600, api.listener.start()), sendMessageByAccount() (api.sendMessage(thân,
//   String(threadId), loaiThread)) và getAllFriendsByAccount() (api.getAllFriends).
// - zalo-server/eventListeners.js: extractMessageContent() (nội dung từ
//   msg.data.content + msgType chat.photo/chat.sticker/chat.video) — chép dưới
//   dạng tríchNoiDungTin().
// Chỉ đổi: lớp kết nối (adapter độc lập thay cho Express của c2a), bỏ proxy,
//   bỏ sharp/node-fetch proxy — Zalo được tạo với { selfListen: true } thuần tuý.
// PHẦN KHÔNG PORT: quản lý nhiều tài khoản + proxy chọn tự động của c2a (dịch vụ
//   này chỉ giữ MỘT tài khoản cá nhân), nhóm lịch sử/kho tin của c2a (dịch vụ chỉ
//   giữ đệm vòng 200 tin mới nhất), self-reply/webhook n8n (không thuộc hợp đồng).
//
// Adapter GIẢ (ZALO_ADAPTER=gia): chỉ dùng trong test — không import zca-js,
// không chạm mạng; cung cấp các hàm gia* để test mô phỏng đăng nhập/tin đến.
// Adapter giả KHÔNG bao giờ tự kích hoạt: chỉ bật khi biến môi trường
// ZALO_ADAPTER đúng bằng «gia».

import fs from 'node:fs';
import path from 'node:path';

export class LoiZalo extends Error {
  constructor(ma, thongDiep) {
    super(thongDiep || ma);
    this.ma = ma; // mã ngắn, an toàn để trả ra ngoài — không chứa bí mật
  }
}

// ── Trích nội dung tin (port extractMessageContent của c2a eventListeners.js) ──

function trichNoiDungTin(msg) {
  const c = msg?.data?.content;
  if (!c) return { van_ban: '', dinh_kem: null };
  if (typeof c === 'string') return { van_ban: c, dinh_kem: null };
  if (msg.data?.msgType === 'chat.photo' && c.href) {
    return { van_ban: '[Ảnh]', dinh_kem: { loai: 'photo', url: String(c.href) } };
  }
  if (msg.data?.msgType === 'chat.sticker') {
    return { van_ban: '[Sticker]', dinh_kem: { loai: 'sticker', url: String(c.href || '') } };
  }
  if (msg.data?.msgType === 'chat.video' && c.href) {
    return { van_ban: '[Video]', dinh_kem: { loai: 'video', url: String(c.href) } };
  }
  if (c.href) {
    return {
      van_ban: String(c.title || c.description || '[File]'),
      dinh_kem: { loai: 'file', url: String(c.href) },
    };
  }
  if (c.msg) return { van_ban: String(c.msg), dinh_kem: null };
  return { van_ban: String(c.title || ''), dinh_kem: null };
}

// Chuẩn hoá một sự kiện tin zca-js → khuôn của dịch vụ (không chứa cookie/token).
// Tin là DỮ LIỆU KHÔNG TIN: chỉ lấy các trường hiển thị, cắt độ dài ở tầng module.
export function chuanHoaTinZca(msg, ownId) {
  if (!msg || typeof msg !== 'object') return null;
  const { van_ban, dinh_kem } = trichNoiDungTin(msg);
  const loai = Number(msg.type) === 1 ? 1 : 0; // ThreadType: 0=User, 1=Group
  const thread_id = String(msg.threadId || msg.data?.idTo || '').trim();
  const nguoi_gui = String(msg.data?.uidFrom || msg.data?.idFrom || thread_id).trim();
  if (!thread_id || !nguoi_gui) return null;
  const lucMs = Number(msg.data?.ts || msg.data?.timestamp || Date.now());
  return {
    thread_id,
    loai,
    nguoi_gui,
    ten_tu_khai: String(msg.data?.dName || '').slice(0, 100) || null,
    van_ban,
    dinh_kem_loai: dinh_kem ? dinh_kem.loai : null,
    tu_gui: Boolean(msg.isSelf),
    luc: new Date(Number.isFinite(lucMs) ? lucMs : Date.now()).toISOString(),
  };
}

// ── Adapter THẬT (zca-js) ─────────────────────────────────────────────────────

async function taoAdapterThat({ thuMucDuLieu, log }) {
  let ZaloPkg = null;
  try {
    ZaloPkg = await import('zca-js');
  } catch {
    log('zalo-client: chua cai zca-js — dich vu chay che do chua_san_sang');
  }

  let api = null;
  let hoSo = null; // {own_id, ten} — KHÔNG gồm cookie/imei/userAgent
  let hamTin = null;

  function sanSang() {
    return Boolean(ZaloPkg);
  }

  function dangNhap() {
    return Boolean(api && hoSo);
  }

  function luuChungChi(context) {
    // Chép mẫu c2a: cred_<ownId>.json trong thư mục dữ liệu, ghi nguyên tử,
    // chmod 0600. Nội dung: {imei, cookie, userAgent} — BÍ MẬT, không log.
    const ten = `cred_${hoSo.own_id}.json`;
    const duongDan = path.join(thuMucDuLieu, ten);
    const tam = `${duongDan}.tmp-${process.pid}`;
    fs.writeFileSync(tam, JSON.stringify({
      imei: context.imei,
      cookie: context.cookie,
      userAgent: context.userAgent,
    }), { mode: 0o600 });
    fs.renameSync(tam, duongDan);
    try { fs.chmodSync(duongDan, 0o600); } catch { /* hệ tệp không hỗ trợ */ }
    return duongDan;
  }

  async function ketNoiVoiApi(apiMoi) {
    const thongTin = await apiMoi.fetchAccountInfo();
    const profile = thongTin?.profile;
    if (!profile) throw new LoiZalo('khong_co_ho_so', 'Không lấy được hồ sơ tài khoản Zalo');
    hoSo = { own_id: String(profile.userId || ''), ten: String(profile.displayName || '') };
    api = apiMoi;
    const context = await api.getContext();
    luuChungChi(context);
    api.listener.on('message', (msg) => {
      try {
        const tin = chuanHoaTinZca(msg, hoSo.own_id);
        if (tin && hamTin) hamTin(tin);
      } catch (e) {
        log(`zalo-client: loi xu ly tin (${e?.name || 'Error'})`);
      }
    });
    api.listener.start();
    return hoSo;
  }

  async function dangNhapQR() {
    if (!sanSang()) throw new LoiZalo('chua_san_sang', 'Chưa cài thư viện zca-js');
    if (dangNhap()) throw new LoiZalo('da_dang_nhap', 'Đã đăng nhập rồi');
    const zalo = new ZaloPkg.Zalo({ selfListen: true });
    // loginQR của zca-js: callback nhận qrData.data.image (base64 PNG).
    const anhHua = new Promise((resolve, reject) => {
      dangNhapQR._choApi = zalo.loginQR(null, (qrData) => {
        const anh = qrData?.data?.image;
        if (anh) resolve(String(anh));
        else reject(new LoiZalo('khong_co_qr', 'zca-js không trả mã QR'));
      });
      dangNhapQR._choApi.catch(reject);
    });
    const anhBase64 = await anhHua;
    // Khi quét xong, loginQR resolve ra api — kết nối ở nền.
    dangNhapQR._choApi
      .then((apiMoi) => ketNoiVoiApi(apiMoi))
      .catch((e) => log(`zalo-client: dang nhap QR that bai (${e?.ma || e?.name || 'Error'})`));
    return { anh_base64: anhBase64 };
  }

  async function khoiPhucTuLuuTru() {
    if (!sanSang() || dangNhap()) return false;
    let tep = null;
    try {
      const ds = fs.readdirSync(thuMucDuLieu).filter((t) => /^cred_.+\.json$/.test(t));
      if (ds.length) tep = path.join(thuMucDuLieu, ds[0]);
    } catch { /* thư mục chưa có */ }
    if (!tep) return false;
    let cred;
    try {
      cred = JSON.parse(fs.readFileSync(tep, 'utf8'));
    } catch {
      return false;
    }
    try {
      const zalo = new ZaloPkg.Zalo({ selfListen: true });
      const apiMoi = await zalo.login(cred);
      await ketNoiVoiApi(apiMoi);
      log('zalo-client: khoi phuc phien tu luu tru thanh cong');
      return true;
    } catch (e) {
      log(`zalo-client: khoi phuc phien that bai (${e?.name || 'Error'}) — can quet QR lai`);
      return false;
    }
  }

  async function layHopThoai() {
    if (!dangNhap()) throw new LoiZalo('chua_dang_nhap', 'Chưa đăng nhập Zalo');
    const ra = [];
    try {
      const banBe = await api.getAllFriends(200, 0);
      const dsBan = Array.isArray(banBe) ? banBe : (banBe?.friends || []);
      for (const b of dsBan) {
        const id = String(b?.userId || b?.id || '').trim();
        if (id) ra.push({ thread_id: id, loai: 0, ten: String(b?.displayName || b?.zaloName || id) });
      }
    } catch (e) {
      log(`zalo-client: lay ban be loi (${e?.name || 'Error'})`);
    }
    try {
      const nhom = await api.getAllGroups();
      const dsNhom = Array.isArray(nhom?.gridVerMap) ? [] : (nhom?.groupList || nhom || []);
      const muc = Array.isArray(dsNhom) ? dsNhom : [];
      for (const n of muc) {
        const id = String(n?.groupId || n?.id || '').trim();
        if (id) ra.push({ thread_id: id, loai: 1, ten: String(n?.name || id) });
      }
    } catch (e) {
      log(`zalo-client: lay nhom loi (${e?.name || 'Error'})`);
    }
    return ra;
  }

  async function guiTin({ thread_id, loai, van_ban, tepDuongDan }) {
    if (!dangNhap()) throw new LoiZalo('chua_dang_nhap', 'Chưa đăng nhập Zalo');
    // Chép mẫu c2a sendMessageByAccount: thân là chuỗi, hoặc {msg, attachments}.
    const than = tepDuongDan
      ? { msg: van_ban || '', attachments: [tepDuongDan] }
      : van_ban;
    await api.sendMessage(than, String(thread_id), loai === 1 ? 1 : 0);
  }

  return {
    che_do: 'that',
    sanSang,
    dangNhap,
    layHoSo: () => hoSo,
    dangNhapQR,
    khoiPhucTuLuuTru,
    layHopThoai,
    guiTin,
    onTin: (ham) => { hamTin = ham; },
    dangXuat: async () => {
      try { api?.listener?.stop?.(); } catch { /* bỏ qua */ }
      api = null;
      hoSo = null;
    },
  };
}

// ── Adapter GIẢ (chỉ test) ────────────────────────────────────────────────────

function taoAdapterGia() {
  let daDangNhap = false;
  let hamTin = null;
  const daGui = [];
  return {
    che_do: 'gia',
    sanSang: () => true,
    dangNhap: () => daDangNhap,
    layHoSo: () => (daDangNhap ? { own_id: '0000000001', ten: 'Tai khoan gia' } : null),
    dangNhapQR: async () => {
      if (daDangNhap) throw new LoiZalo('da_dang_nhap', 'Đã đăng nhập rồi');
      return { anh_base64: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==' };
    },
    khoiPhucTuLuuTru: async () => false,
    layHopThoai: async () => {
      if (!daDangNhap) throw new LoiZalo('chua_dang_nhap', 'Chưa đăng nhập Zalo');
      return [
        { thread_id: '1111111111', loai: 0, ten: 'Nguoi A (gia)' },
        { thread_id: '2222222222', loai: 1, ten: 'Nhom B (gia)' },
      ];
    },
    guiTin: async (tin) => {
      if (!daDangNhap) throw new LoiZalo('chua_dang_nhap', 'Chưa đăng nhập Zalo');
      daGui.push({ ...tin, tepDuongDan: tin.tepDuongDan ? '(co tep)' : null });
    },
    onTin: (ham) => { hamTin = ham; },
    dangXuat: async () => { daDangNhap = false; },
    // Hàm mô phỏng — CHỈ adapter giả có; server chỉ gọi qua kiểm tra che_do.
    _gia: {
      daGui,
      dangNhapXong: () => { daDangNhap = true; },
      nhanTin: (tin) => { if (hamTin) hamTin(tin); },
    },
  };
}

// ── Điểm vào ──────────────────────────────────────────────────────────────────

export async function taoClient({ thuMucDuLieu, log = () => {} }) {
  if (process.env.ZALO_ADAPTER === 'gia') {
    log('zalo-client: adapter GIA (chi dung trong test)');
    return taoAdapterGia();
  }
  return taoAdapterThat({ thuMucDuLieu, log });
}
