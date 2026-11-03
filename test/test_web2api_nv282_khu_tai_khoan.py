"""NV282 — khu «Tài khoản» làm đúng demo «Khu Tài khoản web2api» (bản 9, chủ máy chốt 02/11/2026).

Chrome THẬT (CDP) + lõi THẬT (đường NV283 `dang_nhap`/`loai_hien_thi`, NV285 `chi-tiet`/`lam-moi`) + module AI GIẢ.
Mọi email/mật khẩu/khoá đều GIẢ. Số liệu hạn mức do module giả khai qua `chi_tiet_tai_khoan` (đúng khả năng NV285), kết quả
đăng nhập ghi bằng đúng hàm của lõi (`ghi_ket_qua_dang_nhap`): hạng #1, người giữ #1, đếm trạng thái đều do LÕI tính, trang
chỉ vẽ. Bộ chặn `fetch` trong test CHỈ ghi lại các yêu cầu ghi (POST…) để so khuôn thân; không giả dữ liệu.
"""
from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager

import httpx
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from app import so_module
from app.main import app
from gia import MayChuThat
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

KHOA_QT = "khoa-quan-tri-nv282-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
MAT_KHAU_GIA = "MkGiaNv282#Chinh-001"
THU_MUC_ANH = os.environ.get("NV282_THU_MUC_ANH", "")

MA_MODULE = '''
class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.da_goi_dang_nhap = []
    def nap(self, cau_hinh):
        pass
    async def cai(self, cau_hinh):
        return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": {}}
    async def models(self):
        return []
    async def suc_khoe(self):
        return {"ok": True}
    async def dung(self):
        pass
    # NV285: số liệu hạn mức do TEST đặt (self.ct = {email: {goi, muc: [...]}}), thứ tự xoay = self.thu_tu
    ct = {}
    thu_tu = []
    ct_sau = {}
    lam_moi = []
    def chi_tiet_tai_khoan(self, email):
        return self.ct.get(str(email or "").strip().lower())
    def liet_ke_tai_khoan(self):
        return [{"email": e} for e in self.thu_tu]
    async def lam_moi_han_muc(self, email):
        e = str(email or "").strip().lower()
        self.lam_moi.append(e)
        if e in self.ct_sau:
            self.ct[e] = self.ct_sau[e]
        return self.ct.get(e)
'''
def _ma_module(co_dang_nhap: bool) -> str:
    if not co_dang_nhap:
        return MA_MODULE
    # chèn hàm dang_nhap vào lớp Module (thụt lề đúng)
    return ("from app.tai_khoan_hang_loat import KetQuaDangNhap\n" + MA_MODULE +
            "    async def dang_nhap(self, tai_khoan):\n"
            "        self.da_goi_dang_nhap.append(tai_khoan.email)\n"
            "        mk = tai_khoan.mat_khau.lay() if tai_khoan.mat_khau else ''\n"
            f"        if mk == {MAT_KHAU_GIA!r}:\n"
            "            return KetQuaDangNhap(True, cookie='cookie-gia')\n"
            "        return KetQuaDangNhap(False, ma_loi='sai_mat_khau')\n")


# (id module, nhóm, tên nhóm, có đăng nhập tự động, trường cấu hình)
TR_API = [
    {"khoa": "api_keys", "nhan": "Khoá API", "kieu": "danh_sach_bi_mat", "bat_buoc": True, "mac_dinh": None, "mo_ta": "Một hoặc nhiều khoá giả."},
    {"khoa": "base_url", "nhan": "Địa chỉ gốc", "kieu": "chuoi", "bat_buoc": False, "mac_dinh": "https://gia.example/v1", "mo_ta": ""},
]
MODULES = (
    # Dùng ĐÚNG id module thật để `loai_hien_thi` lấy nhãn từ manifest thật của lõi (chatgpt-web → Web, gemini-api → API, openaicodex → Codex)
    ("chatgpt-web", "chatgpt", "ChatGPT", True, TR_API),  # hai cách: đăng nhập + khoá API
    ("openaicodex", "chatgpt", "ChatGPT", False, []),     # chỉ chứa tài khoản Codex (không thêm tay được → không thành «cách» thứ ba)
    ("gemini-api", "google", "Google", False, TR_API),    # MỘT cách: khoá API
    ("claude-web", "claude", "Claude", True, []),         # MỘT cách: đăng nhập
)


def _chep_module(data, id_, nhom, ten, co_dn, truong):
    d = data / "modules" / id_
    (d / "web").mkdir(parents=True)
    man = {"id": id_, "ten": f"Nguồn {ten}", "mo_ta": "Module giả NV282", "phien_ban": "1.0.0", "hang": "A", "loai": "ai", "giao_thuc": "gia",
           "tien_to": id_.replace("-", ""), "cung_cap": ["chat"], "tai_nguyen": {"mang_ra": []}, "nhom": {"id": nhom, "ten": ten},
           "tab_web": {"nhan": "Giả", "trang": "web/index.html"}, "truong_cau_hinh": truong}
    (d / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    (d / "module.py").write_text(_ma_module(co_dn), encoding="utf-8")
    (d / "web" / "index.html").write_text("<!doctype html><title>gia</title>", encoding="utf-8")


@contextmanager
def loi_that(tmp_path, monkeypatch, tai_khoan=()):
    """Lõi thật + 3 module AI giả (chatgpt: 2 cách; google: khoá API; claude: đăng nhập) + các tài khoản đã lưu
    `tai_khoan` = [(email, module_id, mat_khau_hoac_None)]."""
    data = tmp_path / "data"
    for m in MODULES:
        _chep_module(data, *m)
    (data / "modules.json").write_text(json.dumps({"modules": [
        {"id": m[0], "cai_luc": "2026-10-17T00:00:00", "cau_hinh_cong_khai": {}} for m in MODULES]}), encoding="utf-8")
    monkeypatch.setenv("WEB2API_DATA", str(data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    with MayChuThat(app, lifespan="on") as w:
        with httpx.Client(base_url=w.url, timeout=20) as c:
            for email, mid, mk, *them in tai_khoan:
                r = c.post("/api/tai-khoan/da-luu", headers=QT, json={"email": email, "mat_khau": mk or MAT_KHAU_GIA, "module_id": mid, **(them[0] if them else {})})
                assert r.status_code == 200, r.text
        yield w


def _chup(trang, ten):
    if not THU_MUC_ANH:
        return
    import base64
    from pathlib import Path
    Path(THU_MUC_ANH).mkdir(parents=True, exist_ok=True)
    r = trang.cdp.goi("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True}, trang.s)
    (Path(THU_MUC_ANH) / f"{ten}.png").write_bytes(base64.b64decode(r["data"]))


# Bộ ghi của TRANG (chỉ trong test): ghi lại các yêu cầu GHI (POST/PUT/DELETE) để so khuôn thân. KHÔNG giả dữ liệu nào.
GHI_YC = """
(() => {
  const goc = window.fetch.bind(window); window.__yc = [];
  window.fetch = (u, o) => {
    const url = typeof u === 'string' ? u : u.url;
    if (o && o.method && o.method !== 'GET') { let b = null; try { b = JSON.parse(o.body); } catch (e) {} window.__yc.push({ m: o.method, u: url, b }); }
    return goc(u, o);
  };
})();
"""


def _ghi_yc(trang):
    trang.cdp.goi("Page.enable", {}, trang.s)
    trang.cdp.goi("Page.addScriptToEvaluateOnNewDocument", {"source": GHI_YC}, trang.s)


KET_QUA_DN = {"ok": "thanh_cong", "can_xu_ly": "can_nguoi", "loi": "that_bai"}


def _gan_fx(w, fx: dict, thu_tu=None):
    """Đặt dữ liệu THẬT vào lõi đang chạy (cùng tiến trình): kết quả đăng nhập vào sổ NV283 bằng hàm chính thức của lõi,
    số liệu hạn mức vào các module giả (khả năng `chi_tiet_tai_khoan`). `fx[email] = {"dn": (trang_thai, ma_loi) | None, "ct": {...} | None}`."""
    from app import kho_tai_khoan as ktk
    id_theo_email = {b["email"]: b["id"] for b in ktk.tat_ca_ban_ghi() if b.get("email")}
    for email, f in fx.items():
        dn = f.get("dn")
        if dn:
            tt, ma = dn
            assert ktk.ghi_ket_qua_dang_nhap(id_theo_email[email], KET_QUA_DN[tt], ma, tt == "ok") is not None
    so = app.state.so
    for mid, km in so.catalog.items():          # module giả khai khoá bắt buộc nên chưa «chạy»: cho chạy để lõi hỏi được hạn mức
        if mid not in so.dang_chay:
            so.dang_chay[mid] = km.tao()
    for mod in so.dang_chay.values():
        mod.ct = {e.lower(): f["ct"] for e, f in fx.items() if f.get("ct")}
        mod.thu_tu = [e.lower() for e in (thu_tu or list(fx))]
        mod.ct_sau = {}
        mod.lam_moi = []


def _vao(trang, w, rong=1280, cao=900, cho_ds=True):
    trang.khung(rong, cao)
    trang.di(f"{w.url}/He-thong-AI.dc.html#tai-khoan")
    trang.cho("!!document.querySelector('#o-chon')", han=20, mo_ta="ô chọn tài khoản dựng")
    if cho_ds:
        trang.cho("document.querySelector('#k282-nha [data-nha=\"\"] .n').textContent !== '0'", han=20, mo_ta="danh sách đã nạp")
        trang.cho("!!document.querySelector('#k268-ct[data-k268-ct]') && document.querySelector('#k268-ct').getAttribute('data-k268-ct') !== ''", han=20,
                  mo_ta="tài khoản đầu được chọn sẵn")
    time.sleep(0.4)


MK_ADMIN = "MatKhauAdminBaoMat123456"  # khớp dang_nhap_quan_tri trong ui_chung


def _xac_nhan_lai_step_up(trang):
    """Hộp «xác nhận lại» (step-up) của khung khi POST da-luu qua phiên web."""
    trang.cho("!!document.querySelector('dialog input[type=\"password\"]')", han=10, mo_ta="hộp xác nhận lại (step-up)")
    trang.go("dialog input[type=\"password\"]", MK_ADMIN)
    nut = "[...document.querySelectorAll('dialog button')].find((b) => b.textContent.includes('Xác nhận'))"
    trang.cho(f"!!({nut})", han=5, mo_ta="nút Xác nhận")
    trang.bam(nut, "nút Xác nhận")


def _chon_chip_nha(trang, nha):
    trang.bam(f"document.querySelector('#k282-nha [data-nha=\"{nha}\"]')", f"chip nhà {nha}")
    time.sleep(0.3)


def _mo_o_chon(trang):
    trang.bam("document.querySelector('#o-chon')", "mở ô chọn")
    trang.cho("!!document.querySelector('#ds-tk')", han=5, mo_ta="danh sách mở")


def _chon_tk(trang, email):
    """Mở ô chọn, bấm đúng tài khoản `email`, đợi bảng chi tiết đổi sang tài khoản đó."""
    if not trang.js("!!document.querySelector('#ds-tk')"):
        _mo_o_chon(trang)
    trang.bam(f"[...document.querySelectorAll('#ds-tk [role=option]')].find(li => li.querySelector('.e').textContent === {email!r})", f"tài khoản {email}")
    trang.cho(f"document.querySelector('#k268-ct h2') && document.querySelector('#k268-ct h2').textContent === {email!r}", han=10, mo_ta=f"bảng chi tiết của {email}")
    time.sleep(0.2)


def _ten_dang_chon(trang):
    return trang.js("document.querySelector('#o-chon .ten').textContent")


# ── Dữ liệu cho các ca (đi qua lõi THẬT: ghi sổ đăng nhập NV283, hạn mức module NV285) ──────────────
GPT = [f"gpt{i}.nv282@example.com" for i in range(1, 7)]
GG = ["gg1.nv282@example.com", "gg2.nv282@example.com"]
CODEX = "codex1.nv282@example.com"
WEB = {"loai_tai_khoan": "web", "dich_dung": "web"}   # đường dùng RIÊNG: máy chủ xếp hạng theo (nhà, đường) nên Web không trộn với Codex
TAI_KHOAN = ([(e, "chatgpt-web", None, WEB) for e in GPT] + [(CODEX, "openaicodex", None)] + [(e, "gemini-api", None) for e in GG])


def _muc(ten, nhan, con, tong):
    return {"ten": ten, "nhan": nhan, "con_lai": con, "tong": tong, "da_dung_pct": None, "reset_luc": None, "reset_sau_giay": 7200}


OK = ("ok", "")


def _fx():
    """Trạng thái đăng nhập + hạn mức cho các email giả (thứ tự xoay = thứ tự khai trong dict).
    ChatGPT·Web: GPT[0] ok (ảnh 3/5 → #1 «Tạo ảnh»; «Dán văn bản» cạn), GPT[1] ok (ảnh 2/5 → #2; dán 3/3 → #1 «Dán văn bản»),
    GPT[2] lỗi đăng nhập, GPT[3] chưa đăng nhập nhưng cạn cả hai mục, GPT[4] không số liệu, GPT[5] cần xử lý (captcha).
    ChatGPT·Codex: một tài khoản, cửa sổ 5 giờ đã dùng 42%. Google không có số liệu theo mục."""
    return {
        GPT[0]: {"dn": OK, "ct": {"goi": "plus", "muc": [_muc("image", "Tạo ảnh", 3, 5), _muc("paste", "Dán văn bản", 0, 3)]}},
        GPT[1]: {"dn": OK, "ct": {"goi": "free", "muc": [_muc("image", "Tạo ảnh", 2, 5), _muc("paste", "Dán văn bản", 3, 3)]}},
        GPT[2]: {"dn": ("loi", "sai_mat_khau"), "ct": {"goi": "free", "muc": [_muc("image", "Tạo ảnh", 4, 5), _muc("paste", "Dán văn bản", 2, 3)]}},
        GPT[3]: {"ct": {"goi": "free", "muc": [_muc("image", "Tạo ảnh", 0, 5), _muc("paste", "Dán văn bản", 0, 3)]}},
        GPT[4]: {},
        GPT[5]: {"dn": ("can_xu_ly", "captcha"), "ct": {"goi": "free", "muc": [_muc("image", "Tạo ảnh", 4, 5), _muc("paste", "Dán văn bản", 2, 3)]}},
        CODEX: {"dn": OK, "ct": {"goi": "plus", "muc": [
            {"ten": "cua_so_5h", "nhan": "Cửa sổ 5 giờ", "con_lai": None, "tong": None, "da_dung_pct": 42, "reset_luc": None, "reset_sau_giay": 7800}]}},
        GG[0]: {"dn": OK},
        GG[1]: {},
    }


def _fx_google_co_muc():
    """Như _fx nhưng Google cũng có mục «image» (để kiểm: hạng #1 không trộn giữa hai nhà)."""
    fx = _fx()
    fx[GG[0]] = {"dn": OK, "ct": {"goi": "khong_biet", "muc": [_muc("image", "Tạo ảnh", 5, 5)]}}
    fx[GG[1]] = {"dn": OK, "ct": {"goi": "khong_biet", "muc": [_muc("image", "Tạo ảnh", 4, 5)]}}
    return fx


# ═══════════════════════ Dữ liệu THẬT của lõi hiện nay (chưa NV283/NV285) ═══════════════════════

def test_mac_dinh_mot_o_chon_khong_o_tai_khoan_khac(trang, tmp_path, monkeypatch):
    """DOM mặc định: đúng MỘT ô chọn, không có ô/danh sách tài khoản nào khác, form thêm chưa mở."""
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao(trang, w)
        assert trang.js("document.querySelectorAll('#k268 [role=combobox]').length") == 1
        assert trang.js("document.querySelectorAll('#k268 [role=option], #k268 [role=listbox]').length") == 0, "danh sách phải đóng sẵn"
        assert trang.js("document.querySelectorAll('#k268 .k268-tk, #k268 [data-tk-da-luu], #k268 select, #dl-khoi').length") == 0
        assert trang.js("!!document.querySelector('#form-them')") is False
        # Ô chọn ĐÓNG chỉ có chấm + địa chỉ + «nhà · loại · trạng thái» + mũi tên (KHÔNG huy hiệu).
        assert trang.js("document.querySelectorAll('#o-chon .k282-huy').length") == 0
        assert trang.js("document.querySelectorAll('#o-chon .k282-cham').length") == 1
        assert trang.js("document.querySelectorAll('#o-chon svg').length") == 1
        phu = trang.js("document.querySelector('#o-chon .phu').textContent")
        assert phu.count(" · ") == 2, phu
        _chup(trang, "sau-2-danh-sach-1280")


def test_khong_hien_chua_phan_loai_va_mot_khuon_cho_moi_nhom(trang, tmp_path, monkeypatch):
    """30 tài khoản cùng module → MỘT loại hiển thị suy từ module, không bao giờ «Chưa phân loại»; ô mọi nhóm cùng một khuôn."""
    tk = [(f"gpt{i:02d}.nv282@example.com", "chatgpt-web", None) for i in range(30)] + [(e, "gemini-api", None) for e in GG]
    with loi_that(tmp_path, monkeypatch, tk) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao(trang, w)
        _chon_chip_nha(trang, "chatgpt")
        _mo_o_chon(trang)
        dong = trang.js("[...document.querySelectorAll('#ds-tk [role=option]')].map(li => ({e: li.querySelector('.e').textContent, l: li.querySelector('.l').textContent, c: li.querySelector('.k282-cham').getAttribute('aria-label')}))")
        assert len(dong) == 30
        loai = {d["l"].split(" · ")[1] for d in dong}
        assert len(loai) == 1, f"30 tài khoản cùng module phải CÙNG MỘT loại: {loai}"
        assert "Chưa phân loại" not in trang.js("document.body.innerText")
        for d in dong:                      # dòng đậm = email; dòng nhỏ = «nhà · loại · trạng thái» — một khuôn
            assert "@" in d["e"] and d["l"].count(" · ") == 2 and d["l"].startswith("ChatGPT · ")
            assert d["c"] == "Chưa đăng nhập", "chưa có `dang_nhap` thì chấm là «chưa biết», KHÔNG đoán xanh"
        trang.js("document.querySelector('#o-chon').click()")
        _chon_chip_nha(trang, "google")
        _mo_o_chon(trang)
        gg = trang.js("[...document.querySelectorAll('#ds-tk [role=option]')].map(li => li.querySelector('.l').textContent)")
        assert gg and all(x.count(" · ") == 2 and x.startswith("Google · ") for x in gg), gg


def test_chua_co_so_lieu_thi_khong_bia_so(trang, tmp_path, monkeypatch):
    """Lõi chưa có `chi-tiet`: «Hết limit» chỉ một dòng giải thích (không số 0 giả), bảng nói «chưa có số liệu», #1 không xếp hạng."""
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao(trang, w)
        assert trang.js("document.querySelectorAll('#k282-lim [data-lim]').length") == 0
        assert "chưa có số liệu hạn mức theo mục" in trang.js("document.querySelector('#k282-lim-ghi').textContent")
        assert trang.js("document.querySelectorAll('#k282-h1 [data-h1]').length") == 0
        assert "chưa xếp hạng #1" in trang.js("document.querySelector('#k282-h1-ghi').textContent")
        assert "Chưa có số liệu hạn mức cho loại tài khoản này" in trang.js("document.querySelector('#k282-khong-so').textContent")
        assert trang.js("document.querySelectorAll('#k268-ct [role=progressbar]').length") == 0
        # chấm đều xám: không có xanh/vàng/đỏ nào khi lõi chưa báo
        assert trang.js("document.querySelector('#k282-tt [data-tt=ok] b').textContent") == "0"
        assert trang.js("document.querySelector('#k282-tt [data-tt=chua] b').textContent") == str(len(TAI_KHOAN))


def test_chip_nha_loc_that_va_tu_chon_tai_khoan_dau(trang, tmp_path, monkeypatch):
    """Bấm chip nhà: đổi số đếm, danh sách VÀ tài khoản đang chọn (không còn «bấm Google mà bảng vẫn hiện ChatGPT»).
    Nhà rỗng: không có bảng chi tiết, không giữ tài khoản của nhà khác."""
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao(trang, w)
        dau = _ten_dang_chon(trang)
        assert dau in GG or dau in GPT + [CODEX]
        _chon_chip_nha(trang, "chatgpt")
        assert _ten_dang_chon(trang) in GPT + [CODEX]
        assert trang.js("document.querySelector('#o-chon .phu').textContent").startswith("ChatGPT · ")
        _chon_chip_nha(trang, "google")
        assert _ten_dang_chon(trang) in GG, "bấm Google mà vẫn hiện tài khoản nhà khác"
        assert trang.js("document.querySelector('#k268-ct h2').textContent") in GG
        assert trang.js("document.querySelector('#k282-tt [data-tt=chua] b').textContent") == str(len(GG)), "số đếm phải theo nhà"
        _mo_o_chon(trang)
        ds = trang.js("[...document.querySelectorAll('#ds-tk [role=option] .e')].map(e => e.textContent)")
        assert sorted(ds) == sorted(GG)
        trang.js("document.querySelector('#o-chon').click()")
        _chon_chip_nha(trang, "grok")             # nhà RỖNG
        assert trang.js("document.querySelectorAll('#k268-ct .k282-luoi').length") == 0, "nhà rỗng không được còn bảng của nhà khác"
        assert trang.js("document.querySelector('#k268-ct').getAttribute('data-k268-ct')") == ""
        assert "Chưa có tài khoản" in trang.js("document.querySelector('#o-chon .ten').textContent")
        _chon_chip_nha(trang, "")
        assert _ten_dang_chon(trang) != "Chưa có tài khoản"


def test_chon_google_khong_so_lieu_han_muc_anh(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao(trang, w)
        _chon_chip_nha(trang, "google")
        _chup(trang, "sau-3-chon-google-1280")
        trang.khung(390, 844)
        time.sleep(0.4)
        _chup(trang, "sau-3-chon-google-390")
        assert trang.js("document.documentElement.scrollWidth <= window.innerWidth + 1"), "390px không được cuộn ngang"


def test_ban_phim_o_chon(trang, tmp_path, monkeypatch):
    """role combobox/listbox/option; mũi tên lên/xuống + Enter chọn; Esc đóng; ô tìm lọc không dấu."""
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao(trang, w)
        assert trang.js("document.querySelector('#o-chon').getAttribute('role')") == "combobox"
        assert trang.js("document.querySelector('#o-chon').getAttribute('aria-expanded')") == "false"
        _chon_chip_nha(trang, "chatgpt")
        _mo_o_chon(trang)
        assert trang.js("document.querySelector('#o-chon').getAttribute('aria-expanded')") == "true"
        assert trang.js("document.querySelector('#ds-tk').getAttribute('role')") == "listbox"
        assert trang.js("document.querySelector('#q-tk') === document.activeElement"), "ô tìm phải được focus"
        ds = trang.js("[...document.querySelectorAll('#ds-tk [role=option] .e')].map(e => e.textContent)")
        phim = lambda k: trang.cdp.goi("Input.dispatchKeyEvent", {"type": "keyDown", "key": k, "code": k, "windowsVirtualKeyCode": {"ArrowDown": 40, "ArrowUp": 38, "Enter": 13, "Escape": 27}[k]}, trang.s)  # noqa: E731
        phim("ArrowDown"); phim("ArrowDown"); time.sleep(0.2)
        assert trang.js("[...document.querySelectorAll('#ds-tk [role=option]')].findIndex(o => o.getAttribute('aria-selected') === 'true')") == 2
        phim("ArrowUp"); time.sleep(0.2)
        assert trang.js("[...document.querySelectorAll('#ds-tk [role=option]')].findIndex(o => o.getAttribute('aria-selected') === 'true')") == 1
        phim("Enter"); time.sleep(0.4)
        assert trang.js("!!document.querySelector('#ds-tk')") is False
        assert _ten_dang_chon(trang) == ds[1]
        _mo_o_chon(trang)
        phim("Escape"); time.sleep(0.3)
        assert trang.js("!!document.querySelector('#ds-tk')") is False
        # ô tìm lọc
        _mo_o_chon(trang)
        trang.go("#q-tk", "codex1")
        time.sleep(0.3)
        assert trang.js("[...document.querySelectorAll('#ds-tk [role=option] .e')].map(e => e.textContent)") == [CODEX]
        trang.go("#q-tk", "zzz-khong-co")
        time.sleep(0.3)
        assert "Không có tài khoản nào khớp" in trang.js("document.querySelector('#ds-tk').innerText")


# ═══════════════════════ Dữ liệu theo HỢP ĐỒNG NV283 / NV285 (bộ chặn fetch của trang) ═══════════════════════

def test_cham_trang_thai_dung_lop_va_co_chu(trang, tmp_path, monkeypatch):
    """Mỗi trạng thái → đúng lớp màu VÀ chữ thay thế (title + aria-label); xám gạch đứt cho tạm dừng; màu không phải kênh duy nhất."""
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        # tạm dừng một tài khoản bằng API thật (trang_thai = tam_dung)
        with httpx.Client(base_url=w.url, timeout=20) as c:
            ds = c.get("/api/tai-khoan/da-luu?hien_email=true", headers=QT).json()["tai_khoan"]
            id_gg2 = next(t["id"] for t in ds if t["email"] == GG[1])
            r = c.post(f"/api/tai-khoan/da-luu/{id_gg2}/tam-dung", headers=QT, json={"tam_dung": True})
            assert r.status_code == 200, r.text
        _gan_fx(w, _fx())
        _vao(trang, w)
        _mo_o_chon(trang)
        mau = trang.js("""(() => { const r = {}; document.querySelectorAll('#ds-tk [role=option]').forEach(li => {
          const c = li.querySelector('.k282-cham'); r[li.querySelector('.e').textContent] = {cls: c.className, al: c.getAttribute('aria-label'), ti: c.getAttribute('title')}; }); return r; })()""")
        assert " ok" in mau[GPT[0]]["cls"] and mau[GPT[0]]["al"].startswith("Đăng nhập lúc ")
        assert " xl" in mau[GPT[5]]["cls"] and mau[GPT[5]]["al"].startswith("Cần xử lý: captcha")
        assert " loi" in mau[GPT[2]]["cls"] and mau[GPT[2]]["al"].startswith("Lỗi: sai_mat_khau")
        assert mau[GPT[3]]["cls"].strip() == "k282-cham" and mau[GPT[3]]["al"] == "Chưa đăng nhập"
        assert " dung" in mau[GG[1]]["cls"] and mau[GG[1]]["al"] == "Tạm dừng"
        for v in mau.values():
            assert v["al"] and v["ti"] == v["al"], "mọi chấm phải có title = aria-label"
        # Dải trạng thái đếm đúng theo sổ đăng nhập của lõi
        dem = trang.js("Object.fromEntries([...document.querySelectorAll('#k282-tt [data-tt]')].map(b => [b.dataset.tt, b.querySelector('b').textContent]))")
        assert dem == {"ok": "4", "xl": "1", "loi": "1", "dung": "1", "chua": "2"}, dem


def test_loai_hien_thi_tu_api_lam_ten_loai(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        _gan_fx(w, _fx())
        _vao(trang, w)
        _chon_chip_nha(trang, "chatgpt")
        _mo_o_chon(trang)
        loai = trang.js("[...document.querySelectorAll('#ds-tk .l')].map(l => l.textContent.split(' · ')[1])")
        assert set(loai) == {"Web", "Codex"}, loai


def test_o_chon_mo_co_cham_huy_hieu_1_dung_chu_va_het_limit(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        _gan_fx(w, _fx_google_co_muc())
        _vao(trang, w)
        _chon_chip_nha(trang, "chatgpt")
        trang.cho("document.querySelector('#k282-lim [data-lim]')", han=10, mo_ta="dải «hết limit» có số liệu")
        time.sleep(0.5)
        assert trang.js("document.querySelectorAll('#o-chon .k282-huy').length") == 0, "ô đóng không có huy hiệu"
        _mo_o_chon(trang)
        huy = trang.js("""(() => { const r = {}; document.querySelectorAll('#ds-tk [role=option]').forEach(li => {
          r[li.querySelector('.e').textContent] = [...li.querySelectorAll('.k282-huy')].map(h => h.textContent); }); return r; })()""")
        assert "#1 Tạo ảnh" in huy[GPT[0]] and "Hết limit 1" in huy[GPT[0]]
        assert "#1 Dán văn bản" in huy[GPT[1]] and not any(h.startswith("#1 Tạo ảnh") for h in huy[GPT[1]])
        assert huy[GPT[2]] == [], "tài khoản lỗi đăng nhập không được xếp hạng và không hết limit"
        assert "Hết limit 2" in huy[GPT[3]] and not any(h.startswith("#1") for h in huy[GPT[3]])
        assert "#1 Cửa sổ 5 giờ" in huy[CODEX]
        # mỗi dòng có chấm màu
        assert trang.js("[...document.querySelectorAll('#ds-tk [role=option]')].every(li => !!li.querySelector('.k282-cham[aria-label]'))")


def test_hang_1_theo_nha_duong_dung_muc_khong_tron(trang, tmp_path, monkeypatch):
    """«Đang giữ #1» tính theo (nhà · đường dùng, mục): ChatGPT·Web, ChatGPT·Codex và Google·API đều có hạng 1 riêng."""
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        _gan_fx(w, _fx_google_co_muc())
        _vao(trang, w)
        _chon_chip_nha(trang, "chatgpt")
        # chọn tài khoản GPT[1] (hạng 2 ở «Tạo ảnh», hạng 1 ở «Dán văn bản»)
        trang.cho(f"document.querySelector('#k282-lim [data-lim=\"image\"]')", han=10, mo_ta="dữ liệu mục đã nạp")
        _chon_tk(trang, GPT[1])
        assert trang.js("document.querySelector('#k282-h1-nhan').textContent").startswith("Đang giữ #1 · ChatGPT · Web")
        h1 = trang.js("Object.fromEntries([...document.querySelectorAll('#k282-h1 [data-h1]')].map(b => [b.dataset.h1, b.textContent]))")
        assert "gpt1.nv282" in h1["image"], h1                     # #1 «Tạo ảnh» của ChatGPT·Web = GPT[0], KHÔNG phải Google
        assert "gpt2.nv282" in h1["paste"], h1
        assert "gg1" not in json.dumps(h1) and "codex" not in json.dumps(h1)
        # thanh «Tạo ảnh»: nhãn #2 + dòng «#1 trong ChatGPT · Web đang giữ: [email]» bấm được
        thanh = trang.js("""(() => { const t = document.querySelector('[data-muc=image]'); return {hang: t.querySelector('.tn .k282-huy') && t.querySelector('.tn .k282-huy').textContent,
          chu1: t.querySelector('.k282-chu1').textContent, nut: t.querySelector('[data-giu1]') && t.querySelector('[data-giu1]').textContent}; })()""")
        assert thanh["hang"] == "#2" and "ChatGPT · Web" in thanh["chu1"] and thanh["nut"] == GPT[0], thanh
        # «Dán văn bản»: chính nó giữ hạng 1
        assert "Tài khoản này đang giữ hạng 1" in trang.js("document.querySelector('[data-muc=paste] .k282-chu1').textContent")
        trang.bam("document.querySelector('[data-muc=image] [data-giu1]')", "nhảy sang tài khoản đang giữ #1")
        time.sleep(0.4)
        assert _ten_dang_chon(trang) == GPT[0]
        assert "Tài khoản này đang giữ hạng 1" in trang.js("document.querySelector('[data-muc=image] .k282-chu1').textContent")
        # Google·API có hạng 1 RIÊNG ở cùng mục «image»
        _chon_chip_nha(trang, "google")
        assert _ten_dang_chon(trang) == GG[0]
        assert "Tài khoản này đang giữ hạng 1" in trang.js("document.querySelector('[data-muc=image] .k282-chu1').textContent")
        assert trang.js("document.querySelector('#k282-h1-nhan').textContent").startswith("Đang giữ #1 · Google · API")
        _chon_chip_nha(trang, "chatgpt")
        _chon_tk(trang, CODEX)
        assert "Tài khoản này đang giữ hạng 1" in trang.js("document.querySelector('[data-muc=cua_so_5h] .k282-chu1').textContent")
        _chup(trang, "sau-4-chatgpt-co-han-muc-va-hang-1-1280")


def test_het_limit_chi_ve_muc_co_so_lieu_va_loc_danh_sach(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        _gan_fx(w, _fx())            # Google không có số liệu theo mục
        _vao(trang, w)
        _chon_chip_nha(trang, "google")
        time.sleep(0.5)
        assert trang.js("document.querySelectorAll('#k282-lim [data-lim]').length") == 0, "nhóm không có số liệu thì KHÔNG vẽ mục nào (không số 0 giả)"
        assert "chưa có số liệu" in trang.js("document.querySelector('#k282-lim-ghi').textContent")
        _chon_chip_nha(trang, "chatgpt")
        trang.cho("document.querySelectorAll('#k282-lim [data-lim]').length >= 2", han=10, mo_ta="mục ChatGPT")
        lim = trang.js("Object.fromEntries([...document.querySelectorAll('#k282-lim [data-lim]')].map(b => [b.dataset.lim, b.querySelector('b').textContent]))")
        assert lim == {"image": "1", "paste": "2", "cua_so_5h": "0"}, lim
        trang.bam("document.querySelector('#k282-lim [data-lim=paste]')", "lọc mục paste")
        time.sleep(0.3)
        assert trang.js("document.querySelector('#o-chon').getAttribute('aria-expanded')") == "true", "chip hết limit chỉ mở danh sách đã lọc"
        ds = trang.js("[...document.querySelectorAll('#ds-tk [role=option] .e')].map(e => e.textContent)")
        assert sorted(ds) == sorted([GPT[0], GPT[3]]), ds


def test_so_lieu_tren_trang_khop_tong_quan_cua_loi(trang, tmp_path, monkeypatch):
    """Đối chiếu trang với `GET /api/tai-khoan/tong-quan` (NV285): «Hết limit theo mục» và «Đang giữ #1» trên trang = số máy chủ tính, trang không tự đặt."""
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        _gan_fx(w, _fx())
        with httpx.Client(base_url=w.url, timeout=20) as c:
            tq = c.get("/api/tai-khoan/tong-quan?nha=chatgpt&hien_email=true", headers=QT).json()
            tq_web = c.get("/api/tai-khoan/tong-quan?nha=chatgpt&duong=web&hien_email=true", headers=QT).json()
        _vao(trang, w)
        _chon_chip_nha(trang, "chatgpt")
        _chon_tk(trang, GPT[1])
        trang.cho("document.querySelectorAll('#k282-lim [data-lim]').length >= 3", han=15, mo_ta="dải hết limit nạp xong")
        lim = trang.js("Object.fromEntries([...document.querySelectorAll('#k282-lim [data-lim]')].map(b => [b.dataset.lim, b.querySelector('b').textContent]))")
        assert lim == {m["ten"]: str(m["so_tai_khoan_can"]) for m in tq["het_limit_theo_muc"]}, (lim, tq["het_limit_theo_muc"])
        h1 = trang.js("Object.fromEntries([...document.querySelectorAll('#k282-h1 [data-h1]')].map(b => [b.dataset.h1, b.textContent]))")
        giu = {g["muc"]: g["email"] for g in tq_web["giu_hang_1"]}
        for muc, chu in h1.items():
            assert giu[muc].split("@")[0] in chu, (muc, chu, giu)
        assert set(h1) == set(giu)


def test_thanh_han_muc_progressbar_va_thieu_so_lieu(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        _gan_fx(w, _fx())
        _vao(trang, w)
        _chon_chip_nha(trang, "chatgpt")
        _chon_tk(trang, GPT[0])
        trang.cho("!!document.querySelector('#k268-ct [role=progressbar]')", han=10, mo_ta="thanh hạn mức")
        pb = trang.js("""[...document.querySelectorAll('#k268-ct [role=progressbar]')].map(p => ({n: p.getAttribute('aria-valuenow'), m: p.getAttribute('aria-valuemax'), mn: p.getAttribute('aria-valuemin'), l: p.getAttribute('aria-label')}))""")
        assert pb and all(p["n"] is not None and p["m"] and p["mn"] == "0" and p["l"] for p in pb), pb
        im = next(p for p in pb if p["l"] == "Tạo ảnh")
        assert (im["n"], im["m"]) == ("3", "5")
        # tài khoản ở phiên cần xử lý: ghi chú «lần đo gần nhất»
        _chon_tk(trang, GPT[5])
        assert "lần đo gần nhất" in trang.js("document.querySelector('#k282-han-muc').innerText")
        # tài khoản không có số liệu → thông báo thật, không thanh
        _chon_tk(trang, GPT[4])
        assert trang.js("document.querySelectorAll('#k268-ct [role=progressbar]').length") == 0
        assert "Trang không đoán số" in trang.js("document.querySelector('#k282-khong-so').textContent")
        # Codex: cửa sổ theo % đã dùng
        _chon_tk(trang, CODEX)
        cx = trang.js("(() => { const p = document.querySelector('#k268-ct [role=progressbar]'); return {n: p.getAttribute('aria-valuenow'), m: p.getAttribute('aria-valuemax'), t: document.querySelector('[data-muc=cua_so_5h]').innerText}; })()")
        assert (cx["n"], cx["m"]) == ("42", "100") and "Đã dùng 42%" in cx["t"]


# ═══════════════════════ Form «+ Thêm tài khoản» (MỘT form) ═══════════════════════

DEM_O = """(() => { const f = document.querySelector('#form-them'); if (!f) return -1;
  const hien = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !e.closest('[hidden]') && !e.closest('details:not([open])'); };
  return [...f.querySelectorAll('input:not([type=checkbox]):not([type=hidden]), textarea, select')].filter(hien).length; })()"""


def test_form_mot_form_duy_nhat_khong_form_thu_hai(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao(trang, w)
        trang.bam("document.querySelector('#nut-them')", "+ Thêm tài khoản")
        trang.cho("!!document.querySelector('#form-them')", han=5)
        assert trang.js("document.querySelectorAll('form').length") == 1
        for sel in ("#dl-khoi", "#dl-chon", "#dl-email", "#dl-mat-khau", "#dl-kieu-bi-mat", "#nut-dl-luu"):
            assert trang.js(f"!!document.querySelector({json.dumps(sel)})") is False, sel
        t = trang.js("document.body.innerText")
        for chu in ("Tài khoản đã lưu", "Chọn để điền nhanh", "Kiểu bí mật", "Cách lấy secret", "Dán nhiều dòng (lối phụ)", "Kiểu: Trực tiếp"):
            assert chu not in t, chu
        assert trang.js("document.querySelector('#nut-them').textContent").strip() == "Đóng"


def test_form_nha_hai_cach_hoi_buoc_2_nha_mot_cach_thi_khong(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao(trang, w)
        trang.bam("document.querySelector('#nut-them')")
        trang.cho("!!document.querySelector('#form-them')", han=5)
        assert trang.js("document.querySelectorAll('[data-nhom-them]').length") == 3
        assert trang.js("document.querySelectorAll('[data-cach-them]').length") == 0
        assert DEM_O and trang.js(DEM_O) == 0, "chưa chọn nhà thì chưa có ô nhập"
        # ChatGPT: đăng nhập + khoá API → có bước 2
        trang.bam("document.querySelector('[data-nhom-them=chatgpt]')")
        time.sleep(0.3)
        assert trang.js("document.querySelectorAll('[data-cach-them]').length") == 2
        assert trang.js(DEM_O) == 0, "có hơn một cách: chọn cách rồi mới hiện ô"
        # Claude: MỘT cách (đăng nhập) → không bước 2, ô nhập hiện ngay
        trang.bam("document.querySelector('[data-nhom-them=claude]')")
        time.sleep(0.3)
        assert trang.js("document.querySelectorAll('[data-cach-them]').length") == 0
        assert trang.js("!!document.querySelector('[data-nhap-email]') && !!document.querySelector('[data-nhap-mat-khau]')")
        # Google: MỘT cách (khoá API) → không bước 2
        trang.bam("document.querySelector('[data-nhom-them=google]')")
        time.sleep(0.3)
        assert trang.js("document.querySelectorAll('[data-cach-them]').length") == 0
        assert trang.js("document.querySelectorAll('[data-truong]').length >= 1")


def test_form_so_o_nhap_dang_nhap_toi_da_3_api_toi_da_2(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao(trang, w)
        trang.bam("document.querySelector('#nut-them')")
        trang.cho("!!document.querySelector('#form-them')", han=5)
        trang.bam("document.querySelector('[data-nhom-them=claude]')")
        time.sleep(0.3)
        n_dn = trang.js(DEM_O)
        assert 2 <= n_dn <= 3, n_dn
        # «Có xác minh 2 bước? Thêm khoá OTP» là nút; bấm mới hiện ô OTP (ô thứ ba)
        assert trang.js("!!document.querySelector('[data-nhap-otp]')") is False
        trang.bam("document.querySelector('#nut-mo-otp')")
        time.sleep(0.2)
        assert trang.js("!!document.querySelector('[data-nhap-otp]')") is True
        assert trang.js(DEM_O) == n_dn + 1 <= 3
        _chup(trang, "sau-4-mo-form-dang-nhap-1280")
        trang.bam("document.querySelector('[data-nhom-them=google]')")
        time.sleep(0.3)
        n_api = trang.js(DEM_O)
        assert 1 <= n_api <= 2, n_api
        # Địa chỉ gốc (không bắt buộc) nằm trong «Tuỳ chọn thêm», không ở hàng ô bắt buộc
        assert trang.js("document.querySelector('#tuy-chon-them').open") is False
        assert trang.js("!!document.querySelector('#tuy-chon-them').querySelector('input[type=text]')") is True
        # MỘT nút chính: chỉ một nút gửi đang hiện (cộng nút «Huỷ» phụ)
        assert trang.js("[...document.querySelectorAll('#form-them .k282-hd button')].filter(b => !b.hidden && !b.classList.contains('phu')).length") == 1
        _chup(trang, "sau-4-mo-form-api-1280")


def test_form_tuy_chon_them_dong_san_chua_nhan_bac_goi_dan_nhieu_dong(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao(trang, w)
        trang.bam("document.querySelector('#nut-them')")
        trang.cho("!!document.querySelector('#form-them')", han=5)
        trang.bam("document.querySelector('[data-nhom-them=claude]')")
        time.sleep(0.3)
        d = "document.querySelector('#tuy-chon-them')"
        assert trang.js(f"{d}.open") is False
        n0 = trang.js(DEM_O)
        trang.bam(f"{d}.querySelector('summary')")
        time.sleep(0.3)
        assert trang.js(f"{d}.open") is True
        assert trang.js("!!document.querySelector('#tk-nhan') && !!document.querySelector('#tk-bac-goi')")
        assert "Dán nhiều dòng" in trang.js(f"{d}.innerText")
        assert trang.js(DEM_O) > n0
        # dòng đếm kiểm thử KHÔNG nằm trong giao diện thật
        assert "Ô nhập đang hiện" not in trang.js("document.body.innerText")


def test_form_them_dang_nhap_goi_dung_api_cu_va_chon_tai_khoan_moi(trang, tmp_path, monkeypatch):
    """Khuôn thân yêu cầu = đúng hai lời gọi của «Lưu và đăng nhập» ở form cũ: POST da-luu rồi POST da-luu/{id}/dang-nhap."""
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        _ghi_yc(trang)
        _vao(trang, w)
        trang.bam("document.querySelector('#nut-them')")
        trang.cho("!!document.querySelector('#form-them')", han=5)
        trang.bam("document.querySelector('[data-nhom-them=claude]')")
        time.sleep(0.3)
        trang.go("[data-nhap-email]", "moi.nv282@example.com")
        trang.go("[data-nhap-mat-khau]", MAT_KHAU_GIA)
        trang.bam("document.querySelector('#tuy-chon-them summary')")
        time.sleep(0.2)
        trang.go("#tk-nhan", "Nhãn thử")
        trang.go("#tk-bac-goi", "plus")
        assert MAT_KHAU_GIA not in _html_tho(trang), "mật khẩu đang gõ không được nằm trong thuộc tính HTML"
        trang.bam("document.querySelector('#nut-chay-hang-nhap')", "Đăng nhập")
        _xac_nhan_lai_step_up(trang)
        trang.cho("(document.querySelector('#o-chon .ten') || {}).textContent === 'moi.nv282@example.com'", han=20, mo_ta="tài khoản mới được chọn")
        yc = trang.js("window.__yc")
        post = [y for y in yc if y["m"] == "POST"]
        assert post[0]["u"].endswith("api/tai-khoan/da-luu")
        assert post[0]["b"] == {"email": "moi.nv282@example.com", "mat_khau": MAT_KHAU_GIA, "module_id": "claude-web", "nhan": "Nhãn thử", "bac_goi": "plus",
                                "cach_dang_nhap": "email"}  # NV300: cách đăng nhập theo từng tài khoản
        dn = [y for y in post if "/dang-nhap" in y["u"] and "/da-luu/" in y["u"]]
        assert dn and dn[0]["b"] == {"module_id": "claude-web", "kiem_tra_lai": False}, dn
        # mật khẩu đã rời trang
        assert trang.js("document.querySelector('[data-nhap-mat-khau]') === null || document.querySelector('[data-nhap-mat-khau]').value") in (None, True, "")
        # kết quả thật: đăng nhập xong lõi ghi sổ NV283, chấm chuyển xanh
        trang.cho("document.querySelector('#o-chon .k282-cham').classList.contains('ok')", han=25, mo_ta="chấm chuyển xanh theo kết quả thật")
        assert trang.js("document.querySelector('#o-chon .k282-cham').getAttribute('aria-label')").startswith("Đăng nhập")
        assert trang.js("document.querySelector('#form-them') === null")


def test_form_them_api_goi_dung_api_cu(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        _ghi_yc(trang)
        _vao(trang, w)
        trang.bam("document.querySelector('#nut-them')")
        trang.cho("!!document.querySelector('#form-them')", han=5)
        trang.bam("document.querySelector('[data-nhom-them=google]')")
        time.sleep(0.3)
        trang.go("#form-them input[type=password]", "khoa-gia-nv282-0001")
        trang.bam("document.querySelector('#nut-them-luu')", "Lưu")
        trang.cho("(window.__yc || []).some(y => /api\\/modules\\/gemini-api\\/cai$/.test(y.u))", han=15, mo_ta="POST …/cai")
        y = next(y for y in trang.js("window.__yc") if y["u"].endswith("api/modules/gemini-api/cai"))
        assert y["m"] == "POST" and y["b"] == {"cau_hinh": {"api_keys": {"them": ["khoa-gia-nv282-0001"]}}}, y


def test_neo_tk_mo_form_chon_san_module_va_focus_o_khoa(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.khung(1280, 900)
        trang.di(f"{w.url}/He-thong-AI.dc.html#tk-gemini-api")
        trang.cho("!!document.querySelector('#form-them')", han=15, mo_ta="form mở theo neo")
        trang.cho("document.querySelector('#form-them').getAttribute('data-module') === 'gemini-api'", han=10)
        trang.cho("document.activeElement && document.activeElement.type === 'password'", han=10, mo_ta="focus vào ô khoá")


def test_chua_co_tai_khoan_nao_anh(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, ()) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao(trang, w, cho_ds=False)
        trang.cho("document.querySelector('#k268-ct #k282-rong')", han=15, mo_ta="trạng thái rỗng")
        assert "Chưa có tài khoản nào" in trang.js("document.querySelector('#k268-ct').innerText")
        assert trang.js("document.querySelector('#o-chon .ten').textContent") == "Chưa có tài khoản"
        _chup(trang, "sau-1-chua-co-tai-khoan-1280")
        trang.khung(390, 844)
        time.sleep(0.4)
        _chup(trang, "sau-1-chua-co-tai-khoan-390")
        assert trang.js("document.documentElement.scrollWidth <= window.innerWidth + 1")
        trang.bam("document.querySelector('#nut-them')")
        trang.cho("!!document.querySelector('#form-them')", han=5)
        _chup(trang, "sau-4-mo-form-390")


# ═══════════════════════ Bí mật luôn ẩn, chỉ GHI ĐÈ ═══════════════════════

OTP_GIA = "JBSWY3DPEHPK3PXP"                       # khoá TOTP giả (ví dụ chuẩn của RFC)
KHOA_API_GIA = "khoa-api-gia-nv282-AbCdEf-0001"
MAT_KHAU_MOI = "MkMoiNv282#Ghi-De-002"
URL_GIA = "https://api.gia-nv282.example/v1"
EM_OTP = "otp.nv282@example.com"
EM_API = "api.nv282.example"
TK_BI_MAT = [(EM_OTP, "claude-web", None, {"totp_secret": OTP_GIA}),
             ("", "gemini-api", None, {"bi_mat": KHOA_API_GIA, "kieu_bi_mat": "khoa_api", "dia_chi": URL_GIA})]


def _html_tho(trang):
    return trang.js("document.documentElement.outerHTML")


def _ban_ghi_noi_bo(email):
    from app import kho_tai_khoan as ktk
    return next(b for b in ktk.tat_ca_ban_ghi() if b.get("email") == email)


def test_bi_mat_luon_an_chi_ghi_de_mat_khau(trang, tmp_path, monkeypatch):
    """Thẻ «Đăng nhập và bí mật»: không «Hiện»/«Chép», không bao giờ gọi `bi-mat` lấy bí mật; chỉ «••••• đã lưu» + «Đổi».
    Bấm «Đổi» mới có MỘT ô trống (autocomplete new-password); lưu = POST da-luu {id, mat_khau} ghi đè; giá trị rời DOM/HTML."""
    with loi_that(tmp_path, monkeypatch, TK_BI_MAT) as w:
        dang_nhap_quan_tri(trang, w.url)
        _ghi_yc(trang)
        _vao(trang, w)
        _chon_chip_nha(trang, "claude")
        card = "document.querySelector('[aria-label=\"Đăng nhập và bí mật\"]')"
        t = trang.js(f"{card}.innerText")
        assert "••••• đã lưu" in t and "mật khẩu" in t.lower()
        assert trang.js(f"[...{card}.querySelectorAll('button')].filter(b => ['Hiện', 'Ẩn', 'Chép'].includes(b.textContent.trim())).length") == 0
        assert trang.js("!!document.querySelector('#k268-hien, #k268-chep-bm')") is False
        html = _html_tho(trang)
        for gia_tri in (MAT_KHAU_GIA, OTP_GIA, KHOA_API_GIA):
            assert gia_tri not in html and gia_tri not in trang.js("document.body.innerText"), "bí mật lọt vào trang"
        assert trang.js("!!document.querySelector('#k282-sua')") is False, "ô sửa chỉ mở khi bấm"
        # bấm «Đổi» → MỘT ô trống, kiểu password, autocomplete new-password
        trang.bam("document.querySelector('#k282-doi-bi-mat')", "Đổi mật khẩu")
        trang.cho("!!document.querySelector('#k282-sua input')", han=5, mo_ta="ô ghi đè")
        o = trang.js("(() => { const i = [...document.querySelectorAll('#k282-sua input')]; return {n: i.length, v: i[0].value, t: i[0].type, a: i[0].autocomplete, f: document.activeElement === i[0]}; })()")
        assert o == {"n": 1, "v": "", "t": "password", "a": "new-password", "f": True}, o
        trang.go("#k282-sua-bi_mat", MAT_KHAU_MOI)
        assert MAT_KHAU_MOI not in _html_tho(trang), "giá trị đang gõ không được ghi vào thuộc tính HTML"
        trang.bam("document.querySelector('#k282-sua-luu')", "Lưu (ghi đè)")
        _xac_nhan_lai_step_up(trang)
        trang.cho("!document.querySelector('#k282-sua')", han=15, mo_ta="form sửa đóng sau khi lưu")
        trang.cho("(window.__yc || []).some(y => y.m === 'POST' && /api\\/tai-khoan\\/da-luu$/.test(y.u))", han=5)
        yc = trang.js("window.__yc")
        luu = next(y for y in yc if y["u"].endswith("api/tai-khoan/da-luu"))
        id_ = _ban_ghi_noi_bo(EM_OTP)["id"]
        assert luu["b"] == {"id": id_, "mat_khau": MAT_KHAU_MOI}, "khuôn thân: chỉ id + mật khẩu mới (ghi đè)"
        assert not [y for y in yc if y["u"].endswith("api/tai-khoan/bi-mat") and (y["b"] or {}).get("hanh_dong") == "bi_mat"], "không bao giờ xin bí mật về"
        html = _html_tho(trang)
        assert MAT_KHAU_MOI not in html and MAT_KHAU_MOI not in trang.js("document.body.innerText")
        # đã ghi đè thật trong kho (lõi), mật khẩu cũ không còn
        assert _ban_ghi_noi_bo(EM_OTP).get("mat_khau") == MAT_KHAU_MOI
        # và API công khai không trả bí mật nào
        with httpx.Client(base_url=w.url, timeout=20) as c:
            tho = c.get("/api/tai-khoan/da-luu?hien_email=true", headers=QT).text
            tho2 = c.get(f"/api/tai-khoan/da-luu/{id_}/chi-tiet?hien_email=true", headers=QT).text
        for gia_tri in (MAT_KHAU_MOI, MAT_KHAU_GIA, OTP_GIA, KHOA_API_GIA):
            assert gia_tri not in tho and gia_tri not in tho2


def test_sua_khoa_otp_va_dia_chi_can_nhap_lai_bi_mat_chinh(trang, tmp_path, monkeypatch):
    """Máy chủ lưu cả bộ cùng lúc: sửa khoá OTP (đăng nhập) hoặc địa chỉ (API) vẫn gửi kèm bí mật chính nhập lại; thiếu thì báo, không gửi."""
    with loi_that(tmp_path, monkeypatch, TK_BI_MAT) as w:
        dang_nhap_quan_tri(trang, w.url)
        _ghi_yc(trang)
        _vao(trang, w)
        _chon_chip_nha(trang, "claude")
        assert trang.js("document.querySelector('[data-k268-gt=khoa-otp]').textContent") == "••••• đã lưu"
        trang.bam("document.querySelector('#k282-doi-otp')", "Đổi khoá OTP")
        trang.cho("!!document.querySelector('#k282-sua-totp')", han=5)
        assert trang.js("document.querySelectorAll('#k282-sua input').length") == 2
        trang.go("#k282-sua-totp", "GEZDGNBVGY3TQOJQ")
        trang.bam("document.querySelector('#k282-sua-luu')", "Lưu thiếu mật khẩu")        # thiếu ô thứ hai
        trang.cho("!!document.querySelector('#k282-sua-loi')", han=5, mo_ta="báo thiếu ô")
        assert not [y for y in (trang.js("window.__yc") or []) if y["u"].endswith("api/tai-khoan/da-luu")], "thiếu ô thì không được gửi"
        trang.go("#k282-sua-bi_mat", MAT_KHAU_GIA)
        trang.bam("document.querySelector('#k282-sua-luu')", "Lưu")
        _xac_nhan_lai_step_up(trang)
        trang.cho("!document.querySelector('#k282-sua')", han=15)
        id_ = _ban_ghi_noi_bo(EM_OTP)["id"]
        luu = next(y for y in trang.js("window.__yc") if y["u"].endswith("api/tai-khoan/da-luu"))
        assert luu["b"] == {"id": id_, "mat_khau": MAT_KHAU_GIA, "totp_secret": "GEZDGNBVGY3TQOJQ"}, luu
        assert _ban_ghi_noi_bo(EM_OTP).get("totp_secret") == "GEZDGNBVGY3TQOJQ"
        # tài khoản API: địa chỉ hiện dạng che, sửa kèm khoá nhập lại
        _chon_chip_nha(trang, "google")
        assert trang.js("document.querySelector('[data-k268-gt=dia-chi]').textContent") != URL_GIA, "địa chỉ kết nối phải ẩn/che"
        assert URL_GIA not in _html_tho(trang) and KHOA_API_GIA not in _html_tho(trang)
        trang.bam("document.querySelector('#k282-sua-dia-chi')", "Sửa địa chỉ")
        trang.cho("!!document.querySelector('#k282-sua-dia_chi')", han=5)
        trang.go("#k282-sua-dia_chi", "https://api2.gia-nv282.example/v1")
        trang.go("#k282-sua-bi_mat", KHOA_API_GIA)
        trang.bam("document.querySelector('#k282-sua-luu')", "Lưu")
        trang.cho("!document.querySelector('#k282-sua')", han=15)
        goi = [y for y in trang.js("window.__yc") if y["u"].endswith("api/tai-khoan/da-luu")][-1]["b"]
        assert goi["bi_mat"] == KHOA_API_GIA and goi["kieu_bi_mat"] == "khoa_api" and goi["dia_chi"] == "https://api2.gia-nv282.example/v1" and "mat_khau" not in goi, goi


def test_ma_otp_chi_hien_khi_bam_va_khong_lo_khoa(trang, tmp_path, monkeypatch):
    """Mã 6 số tính ở máy chủ, chỉ lấy khi bấm «Lấy mã OTP»; khoá OTP không bao giờ xuất hiện; «Ẩn mã» xoá mã khỏi trang."""
    with loi_that(tmp_path, monkeypatch, TK_BI_MAT) as w:
        dang_nhap_quan_tri(trang, w.url)
        _ghi_yc(trang)
        _vao(trang, w)
        _chon_chip_nha(trang, "claude")
        assert trang.js("!!document.querySelector('.k282-otp-ma')") is False
        trang.bam("document.querySelector('#k268-hien-otp')", "Lấy mã OTP")
        try:
            trang.cho("!!document.querySelector('.k282-otp-ma')", han=4, mo_ta="mã OTP")
        except Exception:
            _xac_nhan_lai_step_up(trang)
            trang.cho("!!document.querySelector('.k282-otp-ma')", han=10, mo_ta="mã OTP sau xác nhận lại")
        ma = trang.js("document.querySelector('.k282-otp-ma').textContent")
        assert len(ma) == 6 and ma.isdigit(), ma
        assert OTP_GIA not in _html_tho(trang)
        yc = [y for y in trang.js("window.__yc") if y["u"].endswith("api/tai-khoan/bi-mat")]
        assert yc and all(y["b"]["hanh_dong"] == "otp" for y in yc), yc
        trang.bam("document.querySelector('#k268-an-otp')", "Ẩn mã")
        assert trang.js("!!document.querySelector('.k282-otp-ma')") is False and ma not in trang.js("document.body.innerText")


def test_tai_khoan_api_khong_co_nut_dang_nhap(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, TK_BI_MAT) as w:
        dang_nhap_quan_tri(trang, w.url)
        _vao(trang, w)
        _chon_chip_nha(trang, "google")
        assert trang.js("!!document.querySelector('#k268-dn-tu-dong, #k268-dn-tay')") is False, "khoá API không có đăng nhập mật khẩu"
        assert trang.js("!!document.querySelector('#k282-lam-moi')") is True
        _chon_chip_nha(trang, "claude")
        assert trang.js("!!document.querySelector('#k268-dn-tu-dong')") is True


# ═══════════════════════ «Kiểm tra lại hạn mức» (NV285 lam-moi) ═══════════════════════

def test_kiem_tra_lai_han_muc_mot_tai_khoan_va_gian_cach(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        fx = _fx()
        _gan_fx(w, fx)
        mod = app.state.so.dang_chay["chatgpt-web"]
        mod.ct_sau = {GPT[0].lower(): {"goi": "plus", "muc": [_muc("image", "Tạo ảnh", 1, 5), _muc("paste", "Dán văn bản", 0, 3)]}}
        _ghi_yc(trang)
        _vao(trang, w)
        _chon_chip_nha(trang, "chatgpt")
        _chon_tk(trang, GPT[0])
        trang.cho("!!document.querySelector('[data-muc=image] [role=progressbar]')", han=10)
        assert trang.js("document.querySelector('[data-muc=image] [role=progressbar]').getAttribute('aria-valuenow')") == "3"
        trang.bam("document.querySelector('#k282-lam-moi')", "Kiểm tra lại hạn mức")
        try:
            trang.cho("document.querySelector('[data-muc=image] [role=progressbar]').getAttribute('aria-valuenow') === '1'", han=4)
        except Exception:
            _xac_nhan_lai_step_up(trang)
            trang.cho("document.querySelector('[data-muc=image] [role=progressbar]').getAttribute('aria-valuenow') === '1'", han=15, mo_ta="thanh cập nhật theo số mới")
        assert mod.lam_moi == [GPT[0].lower()], "đúng MỘT tài khoản, đúng MỘT lần hỏi module"
        lm = [y for y in trang.js("window.__yc") if "/chi-tiet/lam-moi" in y["u"]]
        assert lm and all(y["m"] == "POST" for y in lm)      # (khung tự gửi lại một lần sau khi xác nhận lại; module vẫn chỉ bị hỏi MỘT lần)
        assert "Đã kiểm tra lại hạn mức" in trang.js("document.querySelector('#k268-dn-kq').textContent")
        # lần hai trong khoảng giãn cách → máy chủ trả 429, trang báo thật, module KHÔNG bị hỏi thêm
        trang.bam("document.querySelector('#k282-lam-moi')", "Kiểm tra lại lần hai")
        trang.cho("document.querySelector('#k268-dn-kq') && document.querySelector('#k268-dn-kq').textContent.includes('quá sớm')", han=15, mo_ta="báo 429")
        assert mod.lam_moi == [GPT[0].lower()]


def test_dang_nhap_that_bai_cham_do_va_ben_sau_khi_tai_lai(trang, tmp_path, monkeypatch):
    """Đăng nhập sai mật khẩu qua form: lõi ghi sổ NV283 → chấm ĐỎ có chữ «Lỗi: …»; tải lại trang vẫn đỏ (không chỉ nhớ trong phiên)."""
    with loi_that(tmp_path, monkeypatch, TAI_KHOAN) as w:
        dang_nhap_quan_tri(trang, w.url)
        _ghi_yc(trang)
        _vao(trang, w)
        trang.bam("document.querySelector('#nut-them')")
        trang.cho("!!document.querySelector('#form-them')", han=5)
        trang.bam("document.querySelector('[data-nhom-them=claude]')")
        time.sleep(0.3)
        trang.go("[data-nhap-email]", "sai.nv282@example.com")
        trang.go("[data-nhap-mat-khau]", "SaiMatKhau#001")
        trang.bam("document.querySelector('#nut-chay-hang-nhap')", "Đăng nhập")
        _xac_nhan_lai_step_up(trang)
        trang.cho("(document.querySelector('#o-chon .ten') || {}).textContent === 'sai.nv282@example.com'", han=20, mo_ta="tài khoản mới được chọn")
        trang.cho("document.querySelector('#o-chon .k282-cham').classList.contains('loi')", han=25, mo_ta="chấm chuyển đỏ theo kết quả thật")
        assert trang.js("document.querySelector('#o-chon .k282-cham').getAttribute('aria-label')").startswith("Lỗi")
        trang.di(f"{w.url}/He-thong-AI.dc.html#tai-khoan")                        # tải lại: kết quả nằm ở sổ của lõi
        trang.cho("!!document.querySelector('#o-chon')", han=20)
        _chon_tk(trang, "sai.nv282@example.com") if trang.js("document.querySelector('#o-chon .ten').textContent") != "sai.nv282@example.com" else None
        assert trang.js("document.querySelector('#o-chon .k282-cham').classList.contains('loi')")


# ═══════════════════════ Ảnh chụp đối chiếu demo (chỉ chạy khi đặt NV282_THU_MUC_ANH) ═══════════════════════

@pytest.mark.skipif(not THU_MUC_ANH, reason="chỉ chạy khi cần chụp ảnh đối chiếu (NV282_THU_MUC_ANH)")
def test_chup_anh_doi_chieu_demo(trang, tmp_path, monkeypatch):
    """Dữ liệu giả cỡ thật (30 ChatGPT Web + 1 Codex + 7 Google + 1 Claude; đủ năm trạng thái, hạn mức theo mục, hạng #1) → ảnh 1280 và 390."""
    web = [f"chatgpt{i + 1:02d}.demo@example.com" for i in range(30)]
    gg = [f"{n}{i + 1}.demo@example.com" for i, n in enumerate(["an", "binh", "chi", "dung", "em", "giang", "hoa"])]
    tk = [(e, "chatgpt-web", None, WEB) for e in web] + [("codex01.demo@example.com", "openaicodex", None)] + [(e, "gemini-api", None) for e in gg] + [("khanh.claude1@example.com", "claude-web", None)]
    tt = ["ok", "xl", "xl", "ok", "xl", "xl", "loi", "xl", "ok", "xl", "chua", "ok", "xl", "xl", "ok", "xl", "chua", "xl", "xl", "loi", "xl", "ok", "xl", "chua", "xl", "ok", "xl", "xl", "chua", "ok"]
    muc = [("deep", "Nghiên cứu sâu", 5), ("file", "Tải tệp tin", 3), ("paste", "Dán văn bản", 3), ("image", "Tạo ảnh", 5), ("reason", "Suy luận", 10)]
    fx = {}
    for i, e in enumerate(web):
        d = {"ok": OK, "xl": ("can_xu_ly", "het_phien"), "loi": ("loi", "sai_mat_khau"), "chua": None}[tt[i]]
        fx[e] = {"dn": d, "ct": {"goi": "plus" if i % 7 == 0 else "free", "muc": [_muc(k, n, max(0, min(t, (i * 5 + j * 11) % (t + 2))), t) for j, (k, n, t) in enumerate(muc)]}}
    fx["codex01.demo@example.com"] = {"dn": OK, "ct": {"goi": "plus", "muc": [{"ten": "cua_so_5h", "nhan": "Cửa sổ 5 giờ", "con_lai": None, "tong": None, "da_dung_pct": 42, "reset_luc": None, "reset_sau_giay": 7800},
                                                                                  {"ten": "cua_so_tuan", "nhan": "Cửa sổ tuần", "con_lai": None, "tong": None, "da_dung_pct": 18, "reset_luc": None, "reset_sau_giay": 360000}]}}
    for i, e in enumerate(gg):
        fx[e] = {"dn": [OK, ("can_xu_ly", "het_phien"), ("can_xu_ly", "captcha"), ("loi", "sai_mat_khau"), OK, None, OK][i]}
    fx["khanh.claude1@example.com"] = {"dn": OK}
    with loi_that(tmp_path, monkeypatch, tk) as w:
        dang_nhap_quan_tri(trang, w.url)
        _gan_fx(w, fx)
        _vao(trang, w)
        trang.js("document.querySelector('#o-chon')")
        _chon_chip_nha(trang, "chatgpt")
        _chon_tk(trang, web[2]) if tt[2] == "ok" else _chon_tk(trang, web[0])
        time.sleep(1.5)
        _chup(trang, "demo-1-chatgpt-1280")
        _mo_o_chon(trang)
        time.sleep(0.5)
        _chup(trang, "demo-2-ds-mo-1280")
        trang.js("document.querySelector('#o-chon').click()")
        trang.bam("document.querySelector('#k282-doi-bi-mat')", "Đổi")
        time.sleep(0.4)
        _chup(trang, "demo-3-sua-de-1280")
        trang.bam("document.querySelector('#k282-sua-huy')")
        _chon_chip_nha(trang, "google")
        time.sleep(0.5)
        _chup(trang, "demo-4-google-1280")
        trang.bam("document.querySelector('#nut-them')")
        trang.cho("!!document.querySelector('#form-them')", han=5)
        trang.bam("document.querySelector('[data-nhom-them=chatgpt]')")
        time.sleep(0.3)
        trang.bam("document.querySelector('[data-cach-them]')")
        time.sleep(0.4)
        _chup(trang, "demo-5-form-chatgpt-1280")
        trang.js("document.querySelector('#nut-them').click()")
        trang.khung(390, 844)
        _chon_chip_nha(trang, "chatgpt")
        time.sleep(1.2)
        _chup(trang, "demo-6-chatgpt-390")
        _mo_o_chon(trang)
        time.sleep(0.5)
        _chup(trang, "demo-7-ds-mo-390")
        assert trang.js("document.documentElement.scrollWidth <= window.innerWidth + 1")


# ═══════════════════════ 390 px ═══════════════════════

def test_390_khong_cuon_ngang_o_chon_mo_form_mo_huy_hieu_xuong_dong(trang, tmp_path, monkeypatch):
    tk = [(f"gpt{i:02d}.nv282@example.com", "chatgpt-web", None) for i in range(30)] + [(e, "gemini-api", None) for e in GG]
    with loi_that(tmp_path, monkeypatch, tk) as w:
        dang_nhap_quan_tri(trang, w.url)
        fx = {f"gpt{i:02d}.nv282@example.com": {"dn": OK, "ct": {"goi": "plus", "muc": [_muc("image", "Tạo ảnh", 0 if i % 2 else 3, 5),
                                                                                           _muc("paste", "Dán văn bản", 2, 3), _muc("deep", "Nghiên cứu sâu", 0, 5)]}}
              for i in range(30)}
        _gan_fx(w, fx)
        _vao(trang, w, rong=390, cao=844)
        _chon_chip_nha(trang, "chatgpt")
        trang.cho("document.querySelector('#k282-lim [data-lim]')", han=15)
        time.sleep(0.8)
        _chup(trang, "sau-4-chatgpt-co-han-muc-va-hang-1-390")
        _mo_o_chon(trang)
        time.sleep(0.3)
        info = trang.js("""(() => { const li = document.querySelector('#ds-tk [role=option]'); const cs = getComputedStyle(li);
          const the = li.querySelector('.k282-hy').getBoundingClientRect(); const e = li.querySelector('.e').getBoundingClientRect();
          return {flex: cs.flexGrow + '/' + cs.flexShrink, theDuoiE: the.top >= e.bottom - 1, rong: document.documentElement.scrollWidth <= window.innerWidth + 1}; })()""")
        assert info["flex"] == "0/0", "hàng trong danh sách KHÔNG được co (flex: none) để huy hiệu không chồng chữ"
        assert info["theDuoiE"], "màn ≤ 560px huy hiệu phải xuống dòng riêng"
        assert info["rong"], "390px không được cuộn ngang khi mở danh sách"
        _chup(trang, "sau-2-o-chon-mo-390")
        trang.js("document.querySelector('#o-chon').click()")
        trang.bam("document.querySelector('#nut-them')")
        trang.cho("!!document.querySelector('#form-them')", han=5)
        trang.bam("document.querySelector('[data-nhom-them=chatgpt]')")
        time.sleep(0.3)
        trang.bam("document.querySelector('[data-cach-them]')")
        time.sleep(0.3)
        assert trang.js("document.documentElement.scrollWidth <= window.innerWidth + 1"), "390px: form không được cuộn ngang"
        _chup(trang, "sau-4-form-co-o-nhap-390")
