"""Test NV230: muse2api TỰ LẤY cookie mới từ workspace Firefox đã đăng nhập.

Hiện trạng (đo trước khi sửa): `lam_moi_phien` chỉ kiểm lại đúng cookie đã có
bằng bootstrap; hết hạn → `het_han`, KHÔNG có đường nào kéo cookie mới từ hồ
sơ Firefox đã gắn dù workspace còn đăng nhập → chat 401 «cookies expired».

Mong muốn (spec NV230): khi bootstrap TỪ CHỐI cookie (401/AuthError), tầng
(a) tìm hồ sơ Firefox GẮN CHẮC CHẮN với khoá (trường `ho_so` trong bản ghi
kho riêng NV93, ghép qua hatch_sess — không đoán tên), đọc cookie muse.ai
qua đường sẵn có (client dịch vụ trình duyệt kiểu vnc-xa: trinh_duyet_ds +
trinh_duyet_cookie), kiểm bằng bootstrap, qua thì THAY cookie của khoá.
Hồ sơ đang mở (409) / không có hatch_sess → «cần người»; cookie mới cũng
hỏng → không thay + nghỉ luỹ thừa; khoá không gắn hồ sơ → không gọi dịch vụ.
Trần thử/ngày + single-flight như NV207.

Qua module THẬT + dịch vụ trình duyệt GIẢ (DichVuGia chích qua
`_bo_lam_moi_cookie._tao_client_dich_vu`, đúng mẫu NV207). Không Internet,
không Firefox thật. Mọi cookie trong test đều GIẢ rõ ràng.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import pytest

from app import kho
from modules.muse2api import chat as cc
from modules.muse2api import module as mm

GOC = Path(__file__).resolve().parents[1]
MANIFEST = json.loads(
    (GOC / "modules" / "muse2api" / "manifest.json").read_text(encoding="utf-8"))

HO_SO = "a1b2c3d4e5f6a7b8c9d0"  # id hồ sơ GIẢ (hex)
EMAIL_GIA = "ban-gia-nv230@example.com"
COOKIE_CU = "hatch_sess=het-han-gia-nv230-cu; hatch_gw=gw-cu-gia-nv230"
COOKIE_MOI = "hatch_sess=sess-moi-gia-nv230; hatch_gw=gw-moi-gia-nv230"
COOKIE_MOI_HONG = "hatch_sess=het-han-gia-nv230-moi; hatch_gw=gw-moi-hong"
COOKIE_KHONG_HATCH = "hatch_gw=chi-co-gw-gia-nv230; muc_khac=x"
SESS_CU = "het-han-gia-nv230-cu"
SESS_MOI = "sess-moi-gia-nv230"


def chay(coro):
    return asyncio.run(coro)


def _kiem_cookie_gia(cookies: str):
    if "het-han-gia" in cookies:
        return False, "cookie het han (gia)", True
    if "loi-mang-gia" in cookies:
        return False, "timeout (gia)", False
    return True, None, False


class LoiDichVuGia(Exception):
    """Lỗi dịch vụ GIẢ mang mã trạng thái như LoiDichVu của vnc-xa."""

    def __init__(self, ma: int):
        super().__init__(f"dich vu gia tra {ma}")
        self.ma = ma


class DichVuGia:
    """Dịch vụ trình duyệt GIẢ: cùng hình dạng client vnc-xa mà mã thật dùng."""

    def __init__(self):
        self.ho_so_ds = [{"ho_so": HO_SO, "ten": "muse gia nv230",
                          "loai": "firefox", "dang_mo": False}]
        self.cookie_theo_ho_so = {HO_SO: COOKIE_MOI}
        self.ma_loi_cookie: int | None = None
        self.dem_ds = 0
        self.dem_cookie = 0

    async def trinh_duyet_ds(self):
        self.dem_ds += 1
        return [dict(x) for x in self.ho_so_ds]

    async def trinh_duyet_cookie(self, ho_so, mien):
        self.dem_cookie += 1
        assert mien == "muse.ai"
        if self.ma_loi_cookie is not None:
            raise LoiDichVuGia(self.ma_loi_cookie)
        c = self.cookie_theo_ho_so.get(ho_so)
        if c is None:
            raise LoiDichVuGia(404)
        return {"cookie": c, "so_cookie": 3}


# ── Gateway giả cho ca chat (theo khuôn test_web2api_muse2api.py) ──────────

@pytest.fixture(autouse=True)
def rut_nhip_chatsession(monkeypatch):
    orig_init = cc.ChatSession.__init__

    def _fake_init(self, gw, node_id, poll_interval=0.05, wait_s=1.0, quiet_s=0.2):
        orig_init(self, gw, node_id, poll_interval=poll_interval,
                  wait_s=wait_s, quiet_s=quiet_s)

    monkeypatch.setattr(cc.ChatSession, "__init__", _fake_init)


class GatewayGia:
    def __init__(self):
        self.lich_su: list[dict] = []
        self.open_calls: list[dict] = []

    def call_json(self, method: str, body=None, query=None, timeout=30, **kw):
        return {"chat_events": list(self.lich_su)}

    def _open(self, method: str, body=None, **kw):
        self.open_calls.append({"method": method, "body": body})
        for e in self.lich_su:
            if e.get("event_name") == "message.assistant" and "seq" in e:
                e["seq"] = e.get("seq", 0) + 100 * len(self.open_calls)
        return len(self.open_calls)

    def close(self):
        pass


# ── Fixture module ─────────────────────────────────────────────────────────

@pytest.fixture()
def mod(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(mm.Module, "_kiem_cookie",
                        staticmethod(_kiem_cookie_gia))
    md = mm.Module(dict(MANIFEST))
    md.kho = kho.kho_rieng("muse2api")
    return md


def _gan_ho_so(md, cookie: str, ho_so: str = HO_SO) -> str:
    """Gắn hồ sơ vào khoá của `cookie` qua bản ghi kho riêng NV93 (như thật)."""
    hs = mm._hatch_sess_tu_cookie(cookie)
    khoa93 = md._khoa_tu_hatch_sess(hs)
    bg = {"khoa": khoa93, "email": EMAIL_GIA, "ho_so": ho_so,
          "goi": "khong_ro", "trang_thai": "hoat_dong", "lan_dung_cuoi": None}
    md.kho.luu("tai_khoan_nv93", {"ban_ghi": {khoa93: bg}})
    return khoa93


def _mod_cookie_hong_co_gan(mod, dv: DichVuGia | None = None):
    """Module có 1 cookie ĐÃ HỎNG, gắn hồ sơ HO_SO; trả (mod, khoa, dv)."""
    _gan_ho_so(mod, COOKIE_CU)
    mod.nap({"cookies": [COOKIE_CU]})
    khoa = mod._khoa_tu_cookies(COOKIE_CU)
    dv = dv if dv is not None else DichVuGia()
    mod._bo_lam_moi_cookie._tao_client_dich_vu = lambda: dv
    return mod, khoa, dv


# ── (a) cookie cũ hỏng + hồ sơ có cookie mới → thay khoá, chat 200 ─────────

def test_a_thay_cookie_va_chat_200(mod):
    mod, khoa, dv = _mod_cookie_hong_co_gan(mod)
    kq = chay(mod.lam_moi_phien(khoa))
    assert kq.thanh_cong and kq.tang == "a", kq
    acc = mod._accs[0]
    assert acc.cookies == COOKIE_MOI
    assert acc.khoe is True
    khoa_moi = mod._khoa_tu_cookies(COOKIE_MOI)
    assert khoa_moi != khoa
    assert mod.co_tai_khoan(khoa_moi)
    # Bản ghi NV93 đi theo hatch_sess mới, vẫn gắn đúng hồ sơ.
    tk = mod._tai_khoan_nv93[0]
    assert tk.hatch_sess == SESS_MOI and tk.ho_so == HO_SO
    # Chat được bằng cookie mới (Gateway giả): 200 + nội dung.
    gw = GatewayGia()
    gw.lich_su = [{"event_name": "message.assistant", "seq": 10,
                   "payload": {"display_text": "Chào NV230!",
                               "message_id": "m10"}}]
    acc._gw = gw

    async def _chat():
        ma, _hd, body = await mod.chat(
            {"messages": [{"role": "user", "content": "Hi"}]}, stream=False)
        assert ma == 200
        assert json.loads(body)["choices"][0]["message"]["content"] == "Chào NV230!"

    chay(_chat())
    # Lượt làm mới nữa: cookie đã sống → bootstrap qua ngay, không gọi dịch vụ thêm.
    dem = dv.dem_cookie
    kq2 = chay(mod.lam_moi_phien(khoa_moi))
    assert kq2.thanh_cong and dv.dem_cookie == dem


# ── (b) hồ sơ đang mở (409) → cần người, không thử lại ─────────────────────

def test_b_ho_so_dang_mo_can_nguoi_khong_thu_lai(mod):
    dv = DichVuGia()
    dv.ho_so_ds[0]["dang_mo"] = True
    mod, khoa, dv = _mod_cookie_hong_co_gan(mod, dv)
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong and kq.can_nguoi, kq
    assert dv.dem_cookie == 0  # đang mở → không đọc cookie, không đóng cưỡng bức
    assert mod._accs[0].cookies == COOKIE_CU
    # Gọi lại ngay: vẫn cần người, KHÔNG gọi lại dịch vụ (không thử lại mãi).
    kq2 = chay(mod.lam_moi_phien(khoa))
    assert kq2.can_nguoi and dv.dem_ds == 1


def test_b2_cookie_tra_409_can_nguoi(mod):
    dv = DichVuGia()
    dv.ma_loi_cookie = 409
    mod, khoa, dv = _mod_cookie_hong_co_gan(mod, dv)
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong and kq.can_nguoi, kq
    assert mod._accs[0].cookies == COOKIE_CU


# ── (c) hồ sơ không có hatch_sess → cần người ───────────────────────────────

def test_c_khong_hatch_sess_can_nguoi(mod):
    dv = DichVuGia()
    dv.cookie_theo_ho_so[HO_SO] = COOKIE_KHONG_HATCH
    mod, khoa, dv = _mod_cookie_hong_co_gan(mod, dv)
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong and kq.can_nguoi, kq
    assert mod._accs[0].cookies == COOKIE_CU


def test_c2_ho_so_chua_co_cookie_404_can_nguoi(mod):
    dv = DichVuGia()
    dv.cookie_theo_ho_so = {}
    mod, khoa, dv = _mod_cookie_hong_co_gan(mod, dv)
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong and kq.can_nguoi, kq
    assert mod._accs[0].cookies == COOKIE_CU


def test_ho_so_gan_khong_con_trong_dich_vu_can_nguoi(mod):
    dv = DichVuGia()
    dv.ho_so_ds = [{"ho_so": "ffffffffffffffff", "ten": "ho so khac",
                    "loai": "firefox", "dang_mo": False}]
    mod, khoa, dv = _mod_cookie_hong_co_gan(mod, dv)
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong and kq.can_nguoi, kq
    assert dv.dem_cookie == 0


# ── (d) cookie mới cũng hỏng → không thay, nghỉ luỹ thừa ────────────────────

def test_d_cookie_moi_cung_hong_khong_thay_nghi_luy_thua(mod):
    dv = DichVuGia()
    dv.cookie_theo_ho_so[HO_SO] = COOKIE_MOI_HONG
    mod, khoa, dv = _mod_cookie_hong_co_gan(mod, dv)
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong and not kq.can_nguoi, kq
    assert kq.ma_loi == "het_han"
    assert mod._accs[0].cookies == COOKIE_CU  # KHÔNG thay bằng cookie hỏng
    tt = mod._doc_trang_thai_cookie().get(khoa) or {}
    assert tt.get("trang_thai") == "loi_tam_thoi"
    assert tt.get("ma_loi") == "cookie_moi_cung_hong"
    assert float(tt.get("nghi_den") or 0) > time.time()  # đã ghi nghỉ luỹ thừa
    # Đang nghỉ: gọi lại không chạm dịch vụ nữa.
    kq2 = chay(mod.lam_moi_phien(khoa))
    assert not kq2.thanh_cong and dv.dem_cookie == 1 and dv.dem_ds == 1


def test_cookie_moi_giong_cu_khong_thay(mod):
    dv = DichVuGia()
    dv.cookie_theo_ho_so[HO_SO] = COOKIE_CU  # workspace vẫn giữ đúng phiên chết
    mod, khoa, dv = _mod_cookie_hong_co_gan(mod, dv)
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong and not kq.can_nguoi, kq
    assert mod._accs[0].cookies == COOKIE_CU
    tt = mod._doc_trang_thai_cookie().get(khoa) or {}
    assert tt.get("ma_loi") == "cookie_moi_giong_cu"


def test_dich_vu_sap_loi_tam_thoi_khong_thay(mod):
    dv = DichVuGia()
    dv.ma_loi_cookie = 500
    mod, khoa, dv = _mod_cookie_hong_co_gan(mod, dv)
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong and not kq.can_nguoi, kq
    assert kq.ma_loi == "het_han"
    assert mod._accs[0].cookies == COOKIE_CU


# ── (e) không lộ cookie ─────────────────────────────────────────────────────

def test_e_khong_lo_cookie(mod, caplog):
    mod, khoa, dv = _mod_cookie_hong_co_gan(mod)
    with caplog.at_level(logging.DEBUG, logger="web2api"):
        kq = chay(mod.lam_moi_phien(khoa))
    assert kq.thanh_cong
    van_ban = caplog.text + json.dumps(kq.sang_dict(), ensure_ascii=False)
    tt = json.dumps(mod._doc_trang_thai_cookie(), ensure_ascii=False)
    for bi_mat in (SESS_CU, SESS_MOI, COOKIE_CU, COOKIE_MOI):
        assert bi_mat not in van_ban
        assert bi_mat not in tt


# ── (f) khoá không gắn hồ sơ → không làm mới bừa ────────────────────────────

def test_f_khong_gan_ho_so_khong_goi_dich_vu(mod):
    mod.nap({"cookies": [COOKIE_CU]})  # KHÔNG gắn hồ sơ (không có bản ghi NV93)
    khoa = mod._khoa_tu_cookies(COOKIE_CU)
    dv = DichVuGia()
    mod._bo_lam_moi_cookie._tao_client_dich_vu = lambda: dv
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong and not kq.can_nguoi, kq
    assert kq.ma_loi == "het_han"  # giữ nguyên hành vi cũ
    assert dv.dem_ds == 0 and dv.dem_cookie == 0
    assert mod._accs[0].cookies == COOKIE_CU


def test_chua_cau_hinh_dich_vu_giu_nguyen_het_han(mod):
    _gan_ho_so(mod, COOKIE_CU)
    mod.nap({"cookies": [COOKIE_CU]})  # có gắn hồ sơ nhưng không cấu hình dịch vụ
    khoa = mod._khoa_tu_cookies(COOKIE_CU)
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong and kq.ma_loi == "het_han", kq


def test_het_tran_thu_ngay_khong_goi_dich_vu(mod):
    mod, khoa, dv = _mod_cookie_hong_co_gan(mod)
    hom_nay = time.strftime("%Y-%m-%d", time.gmtime())
    mod._luu_trang_thai_cookie({khoa: {
        "trang_thai": "loi_tam_thoi", "so_lan_thu": 99, "ngay_thu": hom_nay,
        "nghi_den": 0, "loi_lien_tiep": 0}})
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong and kq.ma_loi == "het_han", kq
    assert dv.dem_ds == 0 and dv.dem_cookie == 0


# ── hồi quy tầng (a) cũ + hợp đồng vòng nền ─────────────────────────────────

def test_cookie_con_song_khong_goi_dich_vu(mod):
    _gan_ho_so(mod, COOKIE_MOI)
    mod.nap({"cookies": [COOKIE_MOI]})
    khoa = mod._khoa_tu_cookies(COOKIE_MOI)
    dv = DichVuGia()
    mod._bo_lam_moi_cookie._tao_client_dich_vu = lambda: dv
    kq = chay(mod.lam_moi_phien(khoa))
    assert kq.thanh_cong and dv.dem_ds == 0 and dv.dem_cookie == 0


def test_khoa_tai_khoan_va_danh_dau_khoe(mod):
    mod.nap({"cookies": [COOKIE_MOI, COOKIE_CU]})
    ds = mod.khoa_tai_khoan()
    assert ds == [mod._khoa_tu_cookies(COOKIE_MOI),
                  mod._khoa_tu_cookies(COOKIE_CU)]
    mod._accs[0].khoe = False
    mod.danh_dau_khoe(ds[0])
    assert mod._accs[0].khoe is True
