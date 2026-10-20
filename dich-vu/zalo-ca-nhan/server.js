// server.js — dịch vụ phụ «zalo-ca-nhan»: cầu nối HTTP loopback tới Zalo cá
// nhân qua zca-js (API KHÔNG CHÍNH THỨC của Zalo).
//
// NGUỒN PORT (LUẬT PORT): luồng đăng nhập/nhận/gửi qua zca-js được chép từ c2a
// (commit d879b03): zalo-server/api/zalo/zalo.js (loginZaloAccount, sendMessage,
// getAllFriends) và eventListeners.js (extractMessageContent → trích ở
// zalo-client.js). Chỉ đổi: lớp kết nối (HTTP stdlib thay Express), xác thực
// (Bearer bắt buộc, fail-fast khi thiếu/yếu), không proxy/kho nhiều tài khoản,
// QR một lần + CSP. Xem thêm modules/zalo-ca-nhan/README.md.
//
// CẢNH BÁO: dùng API không chính thức — Zalo có thể khoá tài khoản bất cứ lúc
// nào. Dịch vụ nghe loopback, hoặc 0.0.0.0 TRONG container với cổng chỉ
// publish về 127.0.0.1 của máy chủ (Z4); toàn bộ route (trừ trang QR một
// lần) cần `Authorization: Bearer <ZALO_KHOA>`.

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { taoClient, LoiZalo } from './zalo-client.js';

process.umask(0o077);

// Hạn tốc độ gửi (chống tràn tin/bị Zalo khoá): ít nhất 2 s giữa hai lần gửi,
// tối đa 20 lần/phút.
const GUI_CACH_GI_MS = Number(process.env.ZALO_GUI_CACH_GI_MS || 2000);
const GUI_TOI_DA_PHUT = Number(process.env.ZALO_GUI_TOI_DA_PHUT || 20);
const TRAN_THAN = 25 * 1024 * 1024;
const TRAN_VAN_BAN = 4000;
const QR_HET_HAN_GIAY = 120;
const DEM_TIN_TOI_DA = 200;

function log(...a) {
  console.log('[zalo-ca-nhan]', ...a);
}

export function docCauHinh() {
  const cfg = {
    cong: Number(process.env.ZALO_PORT || 8703),
    may: process.env.ZALO_HOST || '127.0.0.1',
    khoa: String(process.env.ZALO_KHOA || ''),
    thuMucDuLieu: process.env.ZALO_DATA || '/data',
  };
  if (!cfg.khoa || cfg.khoa.length < 24) {
    throw new Error('ZALO_KHOA thiếu hoặc ngắn hơn 24 ký tự — tự đặt: openssl rand -hex 32');
  }
  // Loopback khi chạy trực tiếp trên máy chủ; 0.0.0.0/:: CHỈ để nghe mọi
  // interface TRONG container (Dockerfile đặt sẵn) — khi đó cổng PHẢI chỉ
  // publish về 127.0.0.1 của máy chủ (`-p 127.0.0.1:8703:8703`), xem README.
  // IP cụ thể khác (LAN/công khai) luôn bị từ chối.
  if (!['127.0.0.1', '::1', 'localhost', '0.0.0.0', '::'].includes(cfg.may)) {
    throw new Error(`ZALO_HOST chỉ được là loopback hoặc 0.0.0.0 trong container (hiện ${cfg.may})`);
  }
  if (!Number.isFinite(cfg.cong) || cfg.cong < 0 || cfg.cong > 65535) {
    throw new Error(`ZALO_PORT không hợp lệ (${process.env.ZALO_PORT})`);
  }
  return cfg;
}

// ── một thể hiện dịch vụ (tạo qua taoDichVu để test được) ────────────────────

export async function taoDichVu({ cauHinh = null, client = null } = {}) {
  const cfg = cauHinh || docCauHinh();
  fs.mkdirSync(cfg.thuMucDuLieu, { recursive: true });
  try { fs.chmodSync(cfg.thuMucDuLieu, 0o700); } catch { /* bỏ qua */ }
  const thuMucTep = path.join(cfg.thuMucDuLieu, 'tep');
  fs.mkdirSync(thuMucTep, { recursive: true });
  try { fs.chmodSync(thuMucTep, 0o700); } catch { /* bỏ qua */ }

  const zalo = client || (await taoClient({ thuMucDuLieu: cfg.thuMucDuLieu, log }));

  let lanGuiCuoi = 0;
  const mocGuiPhut = [];
  const demTin = [];
  let seq = 0;
  // Nhãn phiên (epoch): sinh ngẫu nhiên MỖI lần dịch vụ khởi động, trả kèm
  // trong /tin-moi. seq về 0 khi khởi động lại — module thấy nhãn đổi thì
  // đặt lại con trỏ của nó, không bỏ tin lặng lẽ (NV172-SUA Z2).
  const nhanPhien = crypto.randomBytes(8).toString('hex');
  const phienQR = new Map(); // phien → {token, cookie_trang, het_han, anh_base64}

  function demTinMoi(tin) {
    // Tin là DỮ LIỆU KHÔNG TIN: chỉ nhận từ người khác (bỏ tin tự gửi), cắt chuỗi.
    if (!tin || tin.tu_gui) return;
    seq += 1;
    demTin.push({
      seq,
      thread_id: String(tin.thread_id || '').slice(0, 64),
      loai: tin.loai === 1 ? 1 : 0,
      nguoi_gui: String(tin.nguoi_gui || '').slice(0, 64),
      ten_tu_khai: tin.ten_tu_khai ? String(tin.ten_tu_khai).slice(0, 100) : null,
      van_ban: String(tin.van_ban || '').slice(0, TRAN_VAN_BAN),
      dinh_kem_loai: tin.dinh_kem_loai || null,
      luc: tin.luc,
    });
    if (demTin.length > DEM_TIN_TOI_DA) demTin.shift();
  }
  zalo.onTin(demTinMoi);

  function traJson(res, ma, obj) {
    const than = JSON.stringify(obj);
    res.writeHead(ma, {
      'Content-Type': 'application/json; charset=utf-8',
      'X-Content-Type-Options': 'nosniff',
      'Content-Length': Buffer.byteLength(than),
    });
    res.end(than);
  }

  function traLoi(res, loi) {
    // Lỗi chỉ mang mã ngắn — không chép thông điệp thô (có thể chứa bí mật).
    if (loi instanceof LoiZalo) {
      return traJson(res, loi.ma === 'chua_dang_nhap' ? 409 : 400, { ok: false, ma: loi.ma });
    }
    log(`loi khong ngu (${loi?.name || 'Error'})`);
    return traJson(res, 500, { ok: false, ma: 'loi_khong_ngu' });
  }

  function docThan(req) {
    return new Promise((resolve, reject) => {
      const mieng = [];
      let tong = 0;
      req.on('data', (c) => {
        tong += c.length;
        if (tong > TRAN_THAN) {
          reject(new LoiZalo('than_qua_lon', 'Thân request quá 25 MB'));
          req.destroy();
          return;
        }
        mieng.push(c);
      });
      req.on('end', () => resolve(Buffer.concat(mieng).toString('utf8')));
      req.on('error', reject);
    });
  }

  function kiemBearer(req) {
    const dau = String(req.headers['authorization'] || '');
    if (!dau.startsWith('Bearer ')) return false;
    const gui = Buffer.from(dau.slice(7));
    const that = Buffer.from(cfg.khoa);
    return gui.length === that.length && crypto.timingSafeEqual(gui, that);
  }

  // Hạn tốc độ gửi — kiểm và GIỮ CHỖ trong cùng một đoạn đồng bộ (NV172-SUA
  // Z1). Bản cũ kiểm ở đầu handler rồi ghi mốc SAU `await zalo.guiTin`:
  // mọi yêu cầu đến đồng thời đều thấy mốc cũ nên cùng qua (đo thật 40/40),
  // và đường gửi lỗi không bao giờ ghi mốc. Nguyên tắc ở đây:
  // - Kiểm + ghi mốc là MỘT thao tác không có `await` ở giữa (Node đơn
  //   luồng ⇒ không yêu cầu nào chen vào giữa kiểm và giữ chỗ).
  // - Mọi lần ĐÃ chạm Zalo đều bị đếm, kể cả gửi lỗi: Zalo đã thấy nỗ lực
  //   gửi, và spam-lỗi là đúng mẫu cần chặn để bảo vệ tài khoản.
  // - Lần bị từ chối TRƯỚC khi chạm Zalo (thân/tham số sai, chưa đăng
  //   nhập) không tốn chỗ — handler kiểm các điều đó trước khi gọi hàm này.
  function giuChoGui() {
    const bayGio = Date.now();
    while (mocGuiPhut.length && bayGio - mocGuiPhut[0] > 60_000) mocGuiPhut.shift();
    if (mocGuiPhut.length >= GUI_TOI_DA_PHUT) return false;
    if (bayGio - lanGuiCuoi < GUI_CACH_GI_MS) return false;
    lanGuiCuoi = bayGio;
    mocGuiPhut.push(bayGio);
    return true;
  }

  function sachTenTep(ten) {
    const goc = path.basename(String(ten || 'tep')).replace(/[^\w.\- ]/g, '_');
    return (goc.slice(0, 100) || 'tep').replace(/^\.+/, '');
  }

  // CSP của trang QR (NV172-SUA Z4): form-action/base-uri KHÔNG rơi về
  // default-src nên phải khai tường minh ở cả thẻ meta lẫn header.
  const CSP_QR = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; form-action 'none'; base-uri 'none'";

  function trangQR(anhBase64) {
    return `<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${CSP_QR}">
<meta http-equiv="refresh" content="7">
<title>Đăng nhập Zalo — quét QR</title>
<style>body{font-family:sans-serif;max-width:560px;margin:2em auto;padding:0 1em;color:#111}
img{width:300px;height:300px;border:1px solid #ccc}.canh-bao{background:#fff3cd;padding:.8em;border:1px solid #e6c200}</style>
</head><body>
<h1>Quét QR để đăng nhập Zalo</h1>
<p class="canh-bao">API KHÔNG CHÍNH THỨC — Zalo có thể khoá tài khoản. Trang này chỉ hiện qua URL MỘT LẦN và hết hạn sau ${QR_HET_HAN_GIAY} giây.</p>
<img src="data:image/png;base64,${anhBase64}" alt="Mã QR đăng nhập Zalo">
<p>Trang tự làm mới; quét xong thì đóng trang này.</p>
</body></html>`;
  }

  async function xuLy(req, res) {
    const url = new URL(req.url || '/', `http://127.0.0.1`);
    const duong = url.pathname;

    // Trang QR một lần: xác thực bằng token trong URL (không Bearer).
    if (req.method === 'GET' && duong.startsWith('/qr/')) {
      const token = duong.slice(4).split('/')[0];
      const phien = [...phienQR.values()].find(
        (p) => p.het_han > Date.now()
          && (p.token === token || String(req.headers['cookie'] || '').includes(`zqr=${p.cookie_trang}`)),
      );
      if (!phien) return traJson(res, 404, { ok: false, ma: 'qr_het_han' });
      const cookie = String(req.headers['cookie'] || '');
      if (!cookie.includes(`zqr=${phien.cookie_trang}`)) {
        res.setHeader('Set-Cookie', `zqr=${phien.cookie_trang}; HttpOnly; Path=/qr; SameSite=Strict; Max-Age=${QR_HET_HAN_GIAY}`);
        phien.token = null; // vô hiệu hoá URL ngay sau lần xem đầu (chỉ còn cookie)
      }
      const html = trangQR(phien.anh_base64);
      res.writeHead(200, {
        'Content-Type': 'text/html; charset=utf-8',
        'Content-Security-Policy': CSP_QR,
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
        'Referrer-Policy': 'no-referrer',
        'Cache-Control': 'no-store',
        'Content-Length': Buffer.byteLength(html),
      });
      return res.end(html);
    }

    // Mọi route còn lại cần Bearer.
    if (!kiemBearer(req)) {
      return traJson(res, 401, { ok: false, ma: 'sai_khoa' });
    }

    if (req.method === 'GET' && duong === '/suc-khoe') {
      return traJson(res, 200, {
        ok: true,
        zca_san_sang: zalo.sanSang(),
        dang_nhap: zalo.dangNhap(),
        so_tin_nhan: demTin.length,
        che_do: zalo.che_do,
      });
    }

    if (req.method === 'POST' && duong === '/tai-khoan/dang-nhap-qr') {
      if (!zalo.sanSang()) return traJson(res, 503, { ok: false, ma: 'chua_san_sang' });
      if (zalo.dangNhap()) return traJson(res, 409, { ok: false, ma: 'da_dang_nhap' });
      try {
        const { anh_base64 } = await zalo.dangNhapQR();
        // Z4: anh_base64 đến từ zca-js (dữ liệu ngoài) và bị chèn vào thuộc
        // tính src của trang QR — chỉ nhận đúng khuôn base64 (độ dài bội 4,
        // bảng chữ base64, đệm = ở cuối); khác khuôn thì TỪ CHỐI tạo phiên,
        // không dựng trang. Đây là kiểm cấu trúc, không phải lọc nội dung.
        const anh = String(anh_base64 || '');
        if (!/^[A-Za-z0-9+/]+={0,2}$/.test(anh) || anh.length % 4 !== 0) {
          return traJson(res, 500, { ok: false, ma: 'anh_qr_sai' });
        }
        const phien = crypto.randomBytes(12).toString('hex');
        const p = {
          token: crypto.randomBytes(24).toString('hex'),
          cookie_trang: crypto.randomBytes(24).toString('hex'),
          het_han: Date.now() + QR_HET_HAN_GIAY * 1000,
          anh_base64: anh, // chỉ giữ bản đã qua kiểm khuôn ở trên
        };
        phienQR.set(phien, p);
        setTimeout(() => phienQR.delete(phien), QR_HET_HAN_GIAY * 1000).unref?.();
        return traJson(res, 200, { ok: true, phien, url_qr: `/qr/${p.token}`, het_han_giay: QR_HET_HAN_GIAY });
      } catch (e) {
        return traLoi(res, e);
      }
    }

    if (req.method === 'GET' && duong === '/hop-thoai') {
      try {
        const hop_thoai = await zalo.layHopThoai();
        return traJson(res, 200, { ok: true, hop_thoai });
      } catch (e) {
        return traLoi(res, e);
      }
    }

    if (req.method === 'POST' && duong === '/gui-tin') {
      // Thứ tự theo nguyên tắc Z1: từ chối TRƯỚC khi chạm Zalo (thân/tham
      // số sai, chưa đăng nhập) không tốn chỗ hạn tốc độ; giữ chỗ là thao
      // tác đồng bộ cuối cùng ngay trước lời gọi Zalo.
      let than;
      try {
        than = JSON.parse(await docThan(req));
      } catch {
        return traJson(res, 400, { ok: false, ma: 'than_sai' });
      }
      const thread_id = String(than?.thread_id || '').trim();
      const loai = Number(than?.loai) === 1 ? 1 : 0;
      const van_ban = typeof than?.van_ban === 'string' ? than.van_ban.slice(0, TRAN_VAN_BAN) : '';
      const tep = than?.tep;
      if (!thread_id || thread_id.length > 64 || (!van_ban && !tep)) {
        return traJson(res, 400, { ok: false, ma: 'tham_so_sai' });
      }
      let tepDuongDan = null;
      if (tep) {
        const kieu = String(tep.kieu || '');
        if (!/^(image|video|audio)\//.test(kieu) && kieu !== 'application/pdf') {
          return traJson(res, 400, { ok: false, ma: 'tep_khong_ho_tro' });
        }
        const nhi_phan = Buffer.from(String(tep.du_lieu_base64 || ''), 'base64');
        if (!nhi_phan.length || nhi_phan.length > TRAN_THAN) {
          return traJson(res, 400, { ok: false, ma: 'tep_sai' });
        }
        tepDuongDan = path.join(thuMucTep, `${Date.now()}-${crypto.randomBytes(6).toString('hex')}-${sachTenTep(tep.ten)}`);
        fs.writeFileSync(tepDuongDan, nhi_phan, { mode: 0o600 });
      }
      if (!zalo.dangNhap()) {
        if (tepDuongDan) {
          try { fs.unlinkSync(tepDuongDan); } catch { /* bỏ qua */ }
        }
        return traJson(res, 409, { ok: false, ma: 'chua_dang_nhap' });
      }
      if (!giuChoGui()) {
        if (tepDuongDan) {
          try { fs.unlinkSync(tepDuongDan); } catch { /* bỏ qua */ }
        }
        res.setHeader('Retry-After', '2');
        return traJson(res, 429, { ok: false, ma: 'gui_qua_nhanh' });
      }
      try {
        await zalo.guiTin({ thread_id, loai, van_ban, tepDuongDan });
        return traJson(res, 200, { ok: true });
      } catch (e) {
        // Chỗ đã giữ KHÔNG hoàn lại: lần gửi lỗi vẫn bị đếm (xem giuChoGui).
        return traLoi(res, e);
      } finally {
        if (tepDuongDan) {
          try { fs.unlinkSync(tepDuongDan); } catch { /* bỏ qua */ }
        }
      }
    }

    if (req.method === 'GET' && duong === '/tin-moi') {
      const tu = Math.max(0, Number(url.searchParams.get('tu')) || 0);
      const tin = demTin.filter((t) => t.seq > tu).slice(0, 100);
      // phien = nhãn phiên khởi động (Z2): module so nhãn để biết seq đã
      // bị đặt lại sau lần khởi động lại, kể cả khi tu_moi tình cờ lớn hơn.
      return traJson(res, 200, { ok: true, tu_moi: seq, phien: nhanPhien, tin });
    }

    if (req.method === 'POST' && duong === '/dang-xuat') {
      try {
        await zalo.dangXuat();
        for (const f of fs.readdirSync(cfg.thuMucDuLieu)) {
          if (/^cred_.+\.json$/.test(f)) {
            try { fs.unlinkSync(path.join(cfg.thuMucDuLieu, f)); } catch { /* bỏ qua */ }
          }
        }
        return traJson(res, 200, { ok: true });
      } catch (e) {
        return traLoi(res, e);
      }
    }

    return traJson(res, 404, { ok: false, ma: 'khong_co_route' });
  }

  const mayChu = http.createServer((req, res) => {
    xuLy(req, res).catch((e) => {
      log(`loi khong ngu (${e?.name || 'Error'})`);
      if (!res.headersSent) traJson(res, 500, { ok: false, ma: 'loi_khong_ngu' });
    });
  });

  return {
    mayChu,
    client: zalo,
    cauHinh: cfg,
    nghe: () => new Promise((resolve) => {
      mayChu.listen(cfg.cong, cfg.may, () => resolve(mayChu.address().port));
    }),
    dong: () => new Promise((resolve) => mayChu.close(resolve)),
  };
}

// ── điểm vào khi chạy thật ────────────────────────────────────────────────────

const laEntrypoint = process.argv[1]
  && import.meta.url === pathToFileURL(process.argv[1]).href;

if (laEntrypoint) {
  let cfg;
  try {
    cfg = docCauHinh();
  } catch (e) {
    console.error(`[zalo-ca-nhan] TỪ CHỐI khởi động: ${e.message}`);
    process.exit(1);
  }
  const dv = await taoDichVu({ cauHinh: cfg });
  await dv.client.khoiPhucTuLuuTru();
  const cong = await dv.nghe();
  log(`dang nghe ${cfg.may}:${cong} (che do ${dv.client.che_do})`);
  process.on('SIGTERM', async () => {
    await dv.dong();
    process.exit(0);
  });
}
