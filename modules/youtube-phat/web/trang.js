/* Trang «YouTube phát» — NV153 đợt C (NV181: tách thực thể theo rủi ro).
 *
 * Mọi lời gọi đi qua máy chủ: giao diện gọi hành động của module bằng đường lõi
 * POST /api/thiet-bi/<thực thể>/hanh-dong (chuẩn C1, phiên quản trị
 * + CSRF như trang vnc-xa). Hành động ĐỌC (tim/trang_thai/danh_sach_playlist)
 * đi qua thực thể «youtube_phat.xem» (rủi ro «doc»); hành động ĐIỀU KHIỂN +
 * `lay_khoa` đi qua «youtube_phat.dieu_khien» (rủi ro «ghi»). Khoá dịch vụ
 * KHÔNG ra trình duyệt, không lưu ở đâu;
 * chỉ khi quản trị bấm «Chép token» mới gọi `lay_khoa` một lần rồi chép ngay.
 *
 * Mọi dữ liệu ngoài (tên bài, kênh, tên loa…) đều qua esc() + textContent —
 * không gán HTML trực tiếp từ dữ liệu. Trang không dùng bộ nhớ web
 * (không lưu token ở trình duyệt dưới mọi hình thức).
 */
"use strict";

/* Escape HTML cho dữ liệu ngoài — hàm thuần, có test riêng bằng node. */
function esc(giaTri) {
  const s = giaTri === null || giaTri === undefined ? "" : String(giaTri);
  return s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

(function () {
  const THUC_THE = "youtube_phat.dieu_khien";
  const THUC_THE_XEM = "youtube_phat.xem";
  const HANH_DONG_DOC = new Set(["tim", "trang_thai", "danh_sach_playlist"]);
  const $ = (id) => document.getElementById(id);

  function the(ten, lop, chu) {
    const e = document.createElement(ten);
    if (lop) e.className = lop;
    if (chu !== undefined && chu !== null) e.textContent = chu;
    return e;
  }

  function baoLoi(oId, chu) {
    const o = $(oId);
    if (!o) return;
    o.textContent = chu || "";
    o.hidden = !chu;
  }

  function chuyenDangNhap() {
    const duongHienTai = location.pathname + location.search + location.hash;
    location.replace("../../Dang-nhap.dc.html?ve=" + encodeURIComponent(duongHienTai));
  }

  let csrfToken = "";
  async function layCsrf() {
    if (csrfToken) return csrfToken;
    try {
      const r = await fetch("../../../api/toi", { credentials: "same-origin", cache: "no-store" });
      if (r.ok) {
        const j = await r.json();
        if (j && j.csrf) csrfToken = j.csrf;
      }
    } catch (e) { /* bỏ qua: gọi không CSRF, lõi sẽ 403 và báo rõ */ }
    return csrfToken;
  }

  async function goi(duong, than) {
    const method = than === undefined ? "GET" : "POST";
    const hd = {};
    if (method !== "GET") {
      hd["Content-Type"] = "application/json";
      const csrf = await layCsrf();
      if (csrf) hd["X-CSRF-Token"] = csrf;
    }
    const r = await fetch("../../../" + duong, {
      method, cache: "no-store", credentials: "same-origin", headers: hd,
      body: than === undefined ? undefined : JSON.stringify(than),
    });
    let du = null;
    try { du = await r.json(); } catch (e) { du = null; }
    if (r.status === 401) { chuyenDangNhap(); throw new Error("het_phien"); }
    return { r, du };
  }

  function loiCua(kq) {
    const m = kq.du && kq.du.error && typeof kq.du.error.message === "string"
      ? kq.du.error.message : "";
    if (/can_xac_nhan/.test(m)) {
      return "Thực thể «Máy phát YouTube/Zing» có hành động mới, cần xác nhận một lần: " +
        "vào Hệ thống → Thiết bị → bấm «Xác nhận» cho thực thể này rồi thử lại.";
    }
    return m || ("Lỗi HTTP " + kq.r.status);
  }

  async function hanhDong(ten, thamSo) {
    const thucThe = HANH_DONG_DOC.has(ten) ? THUC_THE_XEM : THUC_THE;
    const kq = await goi("api/thiet-bi/" + thucThe + "/hanh-dong",
      { hanh_dong: ten, tham_so: thamSo || {} });
    if (!kq.r.ok) throw new Error(loiCua(kq));
    const du = kq.du || {};
    if (du.ok === false) throw new Error(du.loi || "Không thực hiện được.");
    return du;
  }

  async function chep(text, o, nhan) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      if (o) { o.focus(); o.select(); }
      throw new Error("Trình duyệt chặn chép tự động — ô đã được bôi đen, nhấn Ctrl+C để chép " + nhan + ".");
    }
  }

  function dinhDangGiay(giay) {
    const v = Number(giay);
    if (!Number.isFinite(v) || v <= 0) return "";
    const h = Math.floor(v / 3600), m = Math.floor((v % 3600) / 60), s = Math.floor(v % 60);
    const mm = h ? String(m).padStart(2, "0") : String(m);
    return (h ? h + ":" : "") + mm + ":" + String(s).padStart(2, "0");
  }

  /* ── mục gập ─────────────────────────────────────────────── */
  document.querySelectorAll("[data-mo-muc]").forEach((nut) => {
    nut.addEventListener("click", () => {
      const mo = nut.getAttribute("aria-expanded") === "true";
      nut.setAttribute("aria-expanded", mo ? "false" : "true");
      const nd = document.getElementById(nut.getAttribute("aria-controls"));
      if (nd) nd.hidden = mo;
    });
  });

  /* ── 1. Kết nối HA ───────────────────────────────────────── */
  let daNapKetNoi = false;
  async function napKetNoi() {
    if (daNapKetNoi) return;
    daNapKetNoi = true;
    try {
      const kq = await goi("api/thiet-bi?module=youtube-phat");
      const ds = (kq.du && kq.du.thuc_the) || [];
      const ent = ds.find((e) => e.id === THUC_THE);
      const tt = (ent && ent.thuoc_tinh) || {};
      $("o-url").value = tt.dich_vu_url || "";
      $("o-token").value = tt.khoa || "";
    } catch (e) {
      if (String(e && e.message) !== "het_phien") baoLoi("loi-chung", String(e.message || e));
    }
  }

  $("nut-chep-url").addEventListener("click", async () => {
    try { await chep($("o-url").value, $("o-url"), "URL"); }
    catch (e) { baoLoi("loi-chung", e.message); }
  });

  $("nut-hien-token").addEventListener("click", async () => {
    const o = $("o-token"), nut = $("nut-hien-token");
    const dangAn = o.type === "password";
    if (dangAn) {
      // Lấy đầy đủ CHỈ khi bấm — thao tác có chủ đích, không lưu lại.
      try {
        const du = await hanhDong("lay_khoa", {});
        o.value = du.khoa || "";
      } catch (e) {
        if (String(e.message) !== "het_phien") baoLoi("loi-chung", e.message);
        return;
      }
    }
    o.type = dangAn ? "text" : "password";
    nut.textContent = dangAn ? "Ẩn" : "Hiện";
    nut.setAttribute("aria-pressed", dangAn ? "true" : "false");
  });

  $("nut-chep-token").addEventListener("click", async () => {
    try {
      const du = await hanhDong("lay_khoa", {});
      const tam = the("input");
      tam.value = du.khoa || "";
      document.body.append(tam);
      await chep(tam.value, tam, "token");
      tam.remove();
      $("o-token").value = "";
      $("o-token").type = "password";
      $("nut-hien-token").textContent = "Hiện";
    } catch (e) {
      if (String(e && e.message) !== "het_phien") baoLoi("loi-chung", e.message);
    }
  });

  /* ── 2. Tìm nhạc ─────────────────────────────────────────── */
  function hangKetQua(muc) {
    const li = the("li");
    li.append(the("div", "dong-tieu-de", muc.tieu_de || muc.ma_bai || "(không tên)"));
    const phu = [muc.kenh, dinhDangGiay(muc.thoi_luong)].filter(Boolean).join(" · ");
    if (phu) li.append(the("div", "dong-phu", phu + " · " + (muc.nguon || "")));
    const hang = the("div", "hang-nut");
    const nutChep = the("button", "nut nut-nho", "Chép mã");
    nutChep.type = "button";
    nutChep.addEventListener("click", async () => {
      try { await chep(muc.ma_bai || muc.url || "", null, "mã bài"); }
      catch (e) { baoLoi("loi-tim", e.message); }
    });
    const nutPhat = the("button", "nut nut-nho nut-chinh", "Phát lên loa");
    nutPhat.type = "button";
    nutPhat.addEventListener("click", () => phatLenLoa(muc));
    hang.append(nutChep, nutPhat);
    li.append(hang);
    return li;
  }

  async function timKiem() {
    baoLoi("loi-tim", "");
    const ds = $("ds-ket-qua");
    ds.replaceChildren(the("li", "dong-phu", "Đang tìm…"));
    try {
      const du = await hanhDong("tim", {
        tu_khoa: $("o-tu-khoa").value.trim(),
        nguon: $("o-nguon").value,
        gioi_han: 10,
      });
      const kq = du.ket_qua || [];
      ds.replaceChildren();
      if (!kq.length) ds.append(the("li", "dong-phu", "Không tìm thấy bài nào."));
      kq.forEach((muc) => ds.append(hangKetQua(muc)));
    } catch (e) {
      ds.replaceChildren();
      if (String(e.message) !== "het_phien") baoLoi("loi-tim", e.message);
    }
  }

  $("nut-tim").addEventListener("click", timKiem);
  $("o-tu-khoa").addEventListener("keydown", (e) => { if (e.key === "Enter") timKiem(); });

  /* ── 3. Loa/tivi ─────────────────────────────────────────── */
  function thietBiDaChon() {
    return [...document.querySelectorAll('#ds-thiet-bi input[type="checkbox"]:checked')]
      .map((o) => o.value);
  }

  async function napThietBi() {
    const o = $("ds-thiet-bi");
    try {
      const kq = await goi("api/thiet-bi?module=home-assistant");
      const ds = ((kq.du && kq.du.thuc_the) || [])
        .filter((e) => typeof e.id === "string" && e.id.indexOf("home_assistant.media_player.") === 0);
      o.replaceChildren();
      if (!ds.length) {
        $("bao-chua-co-ha").hidden = false;
        return;
      }
      ds.forEach((e) => {
        const lb = the("label", "lua-chon");
        const cb = the("input");
        cb.type = "checkbox";
        // entity_id HA để phát: phần sau tiền tố module
        cb.value = e.id.replace(/^home_assistant\./, "");
        const chu = the("span");
        chu.append(the("span", "ten", e.ten || cb.value));
        chu.append(the("br"));
        chu.append(the("span", "ma", cb.value));
        lb.append(cb, chu);
        o.append(lb);
      });
    } catch (e) {
      if (String(e && e.message) !== "het_phien") baoLoi("loi-loa", e.message);
    }
  }

  async function phatLenLoa(muc) {
    baoLoi("loi-loa", "");
    const tb = thietBiDaChon();
    if (!tb.length) {
      baoLoi("loi-loa", "Chọn ít nhất một loa/tivi ở mục «Phát lên loa/tivi» trước khi phát.");
      return;
    }
    try {
      await hanhDong("phat_len_loa", {
        nguon: muc.nguon || "youtube",
        muc_tieu: muc.ma_bai || muc.url,
        thiet_bi: tb,
      });
      napTrangThai();
    } catch (e) {
      if (String(e.message) !== "het_phien") baoLoi("loi-loa", e.message);
    }
  }

  /* ── 4. Đang phát ────────────────────────────────────────── */
  async function napTrangThai() {
    const o = $("o-trang-thai");
    baoLoi("loi-dang-phat", "");
    try {
      const du = await hanhDong("trang_thai", {});
      const phien = du.phien || {};
      const bai = du.bai || phien.item || {};
      o.replaceChildren();
      const tieuDe = bai.title || bai.tieu_de || "(chưa có bài)";
      o.append(the("div", "dong-tieu-de", tieuDe));
      const trangThai = du.trang_thai || phien.state || "";
      o.append(the("div", "dong-phu", "Trạng thái: " + (trangThai || "không rõ")));
      napHangDoi(phien);
      napThanhTua(phien);
    } catch (e) {
      if (String(e.message) !== "het_phien") {
        o.replaceChildren(the("p", "phu", "Chưa tải trạng thái."));
        baoLoi("loi-dang-phat", e.message);
      }
    }
  }

  function napHangDoi(phien) {
    const ds = $("ds-hang-doi");
    ds.replaceChildren();
    const q = (phien && phien.queue) || {};
    const items = q.items || [];
    if (!items.length) {
      ds.append(the("li", "dong-phu", "Hàng đợi trống."));
      return;
    }
    items.forEach((muc, i) => {
      const li = the("li");
      const dangPhat = i === q.index ? "▶ " : "";
      li.append(the("div", "dong-tieu-de", dangPhat + (muc.title || muc.tieu_de || muc.id || "")));
      li.append(the("div", "dong-phu", [muc.channel || muc.kenh, dinhDangGiay(muc.duration || muc.thoi_luong)].filter(Boolean).join(" · ")));
      ds.append(li);
    });
  }

  $("nut-lam-moi-tt").addEventListener("click", napTrangThai);

  /* ── 4b. Bài kế / bài trước / tua (đợt D4) ───────────────────────── */

  function napThanhTua(phien) {
    const thanh = $("thanh-tua");
    const chu = $("chu-tua");
    const bai = (phien && phien.item) || {};
    const thoiLuong = Number((phien && phien.duration != null)
      ? phien.duration : bai.duration) || 0;
    const viTri = Number((phien && phien.position != null)
      ? phien.position : 0) || 0;
    if (thoiLuong > 0) {
      thanh.max = String(Math.floor(thoiLuong));
      thanh.value = String(Math.min(Math.floor(viTri), Math.floor(thoiLuong)));
      thanh.disabled = false;
      chu.textContent = (dinhDangGiay(viTri) || "0:00") + " / " + dinhDangGiay(thoiLuong);
    } else {
      thanh.max = "0"; thanh.value = "0"; thanh.disabled = true;
      chu.textContent = "–";
    }
  }

  async function chuyenBai(ten) {
    baoLoi("loi-dang-phat", "");
    try {
      await hanhDong(ten, {});
      await napTrangThai();
    } catch (e) {
      if (String(e.message) !== "het_phien") baoLoi("loi-dang-phat", e.message);
    }
  }

  $("nut-bai-ke").addEventListener("click", () => chuyenBai("bai_ke"));
  $("nut-bai-truoc").addEventListener("click", () => chuyenBai("bai_truoc"));

  // Kéo xong thả tay mới tua một lần (change), không phát theo từng nấc kéo.
  $("thanh-tua").addEventListener("change", async () => {
    baoLoi("loi-dang-phat", "");
    try {
      await hanhDong("tua", { giay: Number($("thanh-tua").value) });
      await napTrangThai();
    } catch (e) {
      if (String(e.message) !== "het_phien") baoLoi("loi-dang-phat", e.message);
    }
  });

  async function dieuKhienLoa(hanhDongHa) {
    baoLoi("loi-dang-phat", "");
    const tb = thietBiDaChon();
    if (!tb.length) {
      baoLoi("loi-dang-phat", "Chọn ít nhất một loa/tivi ở mục «Phát lên loa/tivi».");
      return;
    }
    try {
      for (const idCua of tb) {
        const kq = await goi("api/thiet-bi/home_assistant." + encodeURIComponent(idCua) + "/hanh-dong",
          { hanh_dong: hanhDongHa, tham_so: {} });
        if (!kq.r.ok) throw new Error(loiCua(kq));
      }
      napTrangThai();
    } catch (e) {
      if (String(e.message) !== "het_phien") baoLoi("loi-dang-phat", e.message);
    }
  }

  $("nut-tam-dung").addEventListener("click", () => dieuKhienLoa("tam_dung"));
  $("nut-tiep-tuc").addEventListener("click", () => dieuKhienLoa("phat"));
  $("nut-dung").addEventListener("click", async () => {
    baoLoi("loi-dang-phat", "");
    try {
      await hanhDong("dung_phien", {});
      // Dừng hẳn cả loa đã chọn cho chắc (dịch vụ đã ngắt luồng).
      const tb = thietBiDaChon();
      for (const idCua of tb) {
        try {
          await goi("api/thiet-bi/home_assistant." + encodeURIComponent(idCua) + "/hanh-dong",
            { hanh_dong: "dung", tham_so: {} });
        } catch (e2) { /* loa đã dừng thì thôi */ }
      }
      napTrangThai();
    } catch (e) {
      if (String(e.message) !== "het_phien") baoLoi("loi-dang-phat", e.message);
    }
  });

  /* ── 5. Playlist ─────────────────────────────────────────── */
  async function napPlaylist() {
    const ds = $("ds-playlist");
    baoLoi("loi-playlist", "");
    try {
      const du = await hanhDong("danh_sach_playlist", {});
      const list = du.danh_sach || [];
      ds.replaceChildren();
      if (!list.length) ds.append(the("li", "dong-phu", "Chưa có playlist nào."));
      list.forEach((pl) => {
        const li = the("li");
        li.append(the("div", "dong-tieu-de", pl.ten || pl.id));
        li.append(the("div", "dong-phu", (pl.so_bai || 0) + " bài · mã: " + (pl.id || "")));
        const hang = the("div", "hang-nut");
        const nut = the("button", "nut nut-nho", "Chép id");
        nut.type = "button";
        nut.addEventListener("click", async () => {
          try { await chep(pl.id || "", null, "id playlist"); }
          catch (e) { baoLoi("loi-playlist", e.message); }
        });
        hang.append(nut);
        li.append(hang);
        ds.append(li);
      });
    } catch (e) {
      if (String(e.message) !== "het_phien") baoLoi("loi-playlist", e.message);
    }
  }

  $("nut-nhap-ma").addEventListener("click", async () => {
    baoLoi("loi-playlist", "");
    $("bao-playlist").hidden = true;
    const ma = $("o-ma-chia-se").value.trim();
    if (!ma) { baoLoi("loi-playlist", "Nhập mã chia sẻ (bắt đầu bằng «TTPL1.»)."); return; }
    try {
      const du = await hanhDong("nhap_ma_chia_se", { ma });
      const pl = du.playlist || {};
      const o = $("bao-playlist");
      o.textContent = "Đã nhập playlist «" + (pl.name || pl.id || "") + "».";
      o.hidden = false;
      $("o-ma-chia-se").value = "";
      napPlaylist();
    } catch (e) {
      if (String(e.message) !== "het_phien") baoLoi("loi-playlist", e.message);
    }
  });

  /* ── khởi động: nạp lười theo mục mở ─────────────────────── */
  let daNap = { loa: false, dangPhat: false, playlist: false };
  document.querySelectorAll("[data-mo-muc]").forEach((nut) => {
    nut.addEventListener("click", () => {
      const mo = nut.getAttribute("aria-expanded") === "true";
      if (!mo) return;
      const muc = nut.getAttribute("data-mo-muc");
      if (muc === "ket-noi") napKetNoi();
      else if (muc === "loa" && !daNap.loa) { daNap.loa = true; napThietBi(); }
      else if (muc === "dang-phat" && !daNap.dangPhat) { daNap.dangPhat = true; napTrangThai(); }
      else if (muc === "playlist" && !daNap.playlist) { daNap.playlist = true; napPlaylist(); }
      else if (muc === "hang-doi" && !daNap.dangPhat) { daNap.dangPhat = true; napTrangThai(); }
    });
  });
})();
