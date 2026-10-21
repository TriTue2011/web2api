"""Test NV207: tự làm mới JWT của module chatgpt-web qua dịch vụ GIẢ.

Đỏ→xanh qua LÕI THẬT: Module chatgpt-web thật + BoLamMoiJWT thật
(modules/chatgpt-web/lam_moi_jwt.py) + QuanLyLamMoiJWT thật
(app/lam_moi_jwt.py). Dịch vụ may-dang-nhap là GIẢ (DichVuGia) chích qua
`_tao_client_dich_vu`. Không Internet, không Chrome.

Mọi token/JWT trong test đều GIẢ, tự dựng bằng tay (không xác thực chữ ký —
mã thật chỉ đọc claim `exp`, không kiểm chữ ký). Không có khoá/IP/email thật.

Bảng BƯỚC 1 (web2api hiện làm gì → mong muốn):
- JWT còn > ngưỡng        → không làm gì  → không gọi mạng (chua_toi_nguong)
- JWT còn < ngưỡng        → không làm gì  → gọi làm mới, thay token (da_lam_moi)
- JWT đã hết hạn          → không làm gì  → vẫn thử làm mới
- claim exp hỏng/thiếu    → không làm gì  → can_nguoi, không làm bừa
- token không có hồ sơ    → không làm gì  → can_nguoi (khong_ghep_duoc_ho_so)
- dịch vụ trả 401         → không làm gì  → dừng vĩnh viễn + cần đăng nhập lại
- dịch vụ sập/quá hạn     → không làm gì  → nghỉ luỹ thừa, trần N lần/ngày
- hai lượt chạy chồng     → không làm gì  → single-flight, chỉ một lượt gọi
- token mới giống token cũ → không làm gì → không thay, không lặp vô hạn
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import sys
import time
import types
from pathlib import Path

import pytest

from app import kho

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "chatgpt-web"
TEN_GOI = "chatgpt_web_nv207_mod_test"

HO_SO_GIA = "hs-gia-nv207-0001"
EMAIL_GIA = "ban-gia-nv207@example.com"


def _b64url(obj: dict) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwt_gia(*, exp=None, email=EMAIL_GIA, extra=None) -> str:
    """JWT GIẢ tự dựng: header.payload.chuky-gia (mã thật không kiểm chữ ký)."""
    payload = {"email": email}
    if exp is not None:
        payload["exp"] = exp
    if extra:
        payload.update(extra)
    return _b64url({"alg": "none", "typ": "JWT"}) + "." + _b64url(payload) + ".chuky-gia"


def _tai_goi():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC)]
    sys.modules[TEN_GOI] = goi
    for ten_tep in ("client", "converter", "lam_moi_jwt", "module"):
        spec = importlib.util.spec_from_file_location(
            f"{TEN_GOI}.{ten_tep}", THU_MUC / f"{ten_tep}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{TEN_GOI}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{TEN_GOI}.module"]


mod_module = _tai_goi()
mod_lam_moi_jwt = sys.modules[TEN_GOI + ".lam_moi_jwt"]
Module = mod_module.Module
MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


def chay(coro):
    return asyncio.run(coro)


class DichVuGia:
    """Dịch vụ may-dang-nhap GIẢ: cùng hình dạng kết quả với module thật."""

    def __init__(self):
        self.ho_so = [HO_SO_GIA]
        self.che_do = "ok"  # ok | loi401 | sap | giong_cu
        self.dem_ds = 0
        self.dem_lam_moi = 0
        self.token_moi = None

    async def danh_sach_ho_so(self):
        self.dem_ds += 1
        return {"ok": True, "profiles": list(self.ho_so), "nguon_ngoai": True}

    async def lam_moi_jwt(self, ho_so):
        self.dem_lam_moi += 1
        if self.che_do == "loi401":
            # Hình dạng 401 thật của may-dang-nhap._goi (không có trường "http").
            return {"ok": False, "ma_loi": "sai_khoa_dich_vu",
                    "loi": "Khoá dịch vụ không đúng hoặc hết hạn",
                    "nguon_ngoai": True}
        if self.che_do == "sap":
            raise ConnectionError("dich-vu-gia-sap")
        if self.che_do == "giong_cu":
            return {"ok": True, "access_token": self.token_cu,
                    "nguon_ngoai": True}
        return {"ok": True, "access_token": self.token_moi,
                "nguon_ngoai": True}


@pytest.fixture
def mod(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    m = Module(dict(MANIFEST))
    m.kho = kho.kho_rieng("chatgpt-web")
    return m


def _gan_tai_khoan(mod, token, ho_so=HO_SO_GIA):
    tk = mod_module._TaiKhoan(token)
    tk.email = EMAIL_GIA
    tk.ho_so = ho_so
    with mod._khoa:
        mod._tai_khoan = [tk]
        mod._dich_vu_dang_nhap_url = "http://127.0.0.1:9"
        mod._khoa_dich_vu_dang_nhap = "khoa-gia"
        mod._jwt_nguong_ngay = 2.0
        mod._jwt_tran_thu_ngay = 5
    return tk


def _gan_dich_vu_gia(mod, che_do="ok", token_moi=None):
    dv = DichVuGia()
    dv.che_do = che_do
    dv.token_moi = token_moi
    mod._bo_lam_moi_jwt._tao_client_dich_vu = lambda: dv
    return dv


def _khoa_cua(mod):
    with mod._khoa:
        return mod._khoa_tu_token(mod._tai_khoan[0].token)


# ── giải mã exp (không gọi mạng) ──────────────────────────────────────────

def test_giai_ma_exp_cac_dang():
    lm = mod_lam_moi_jwt
    bay_gio = int(time.time())
    assert lm.exp_cua(_jwt_gia(exp=bay_gio + 3600)) == bay_gio + 3600
    assert lm.exp_cua(_jwt_gia()) is None                    # thiếu exp
    assert lm.exp_cua(_jwt_gia(exp="khong-phai-so")) is None  # exp hỏng
    assert lm.exp_cua(_jwt_gia(exp=-5)) is None               # exp âm
    assert lm.exp_cua("khong-phai-jwt") is None
    assert lm.exp_cua("") is None
    con_lai = lm.con_lai_ngay(bay_gio + 172800, bay_gio)
    assert 1.99 < con_lai < 2.01


# ── trên ngưỡng: KHÔNG gọi mạng ───────────────────────────────────────────

def test_tren_nguong_khong_goi_dich_vu(mod):
    token = _jwt_gia(exp=int(time.time()) + 5 * 86400)
    _gan_tai_khoan(mod, token)
    dv = _gan_dich_vu_gia(mod)
    kq = chay(mod.lam_moi_jwt(_khoa_cua(mod)))
    assert kq["ok"] is True and kq["ma"] == "chua_toi_nguong", kq
    assert dv.dem_ds == 0 and dv.dem_lam_moi == 0
    assert kq["con_lai_ngay"] > 2.0


# ── dưới ngưỡng: làm mới thành công, thay token ───────────────────────────

def test_duoi_nguong_lam_moi_thanh_cong(mod):
    token_cu = _jwt_gia(exp=int(time.time()) + 1 * 86400)
    token_moi = _jwt_gia(exp=int(time.time()) + 30 * 86400)
    _gan_tai_khoan(mod, token_cu)
    khoa_cu = _khoa_cua(mod)
    dv = _gan_dich_vu_gia(mod, token_moi=token_moi)
    kq = chay(mod.lam_moi_jwt(khoa_cu))
    assert kq["ok"] is True and kq["ma"] == "da_lam_moi", kq
    assert dv.dem_lam_moi == 1
    with mod._khoa:
        tokens = [t.token for t in mod._tai_khoan]
    assert token_moi in tokens and token_cu not in tokens
    # Cấu hình ĐÃ LƯU trên đĩa cũng đã thay (đường cai {giu, them} thật,
    # không chỉ bộ nhớ — khởi động lại không mất token mới).
    da_luu = kho.doc_bi_mat("chatgpt-web").get("tai_khoan", [])
    assert token_moi in da_luu and token_cu not in da_luu
    # Sổ trạng thái ghi đã làm mới DƯỚI KHOÁ MỚI (mã pop khoá cũ); kết quả không lộ token.
    tt = mod._doc_trang_thai_jwt()
    assert khoa_cu not in tt
    assert tt[kq["khoa_moi"]]["trang_thai"] == "da_lam_moi"
    assert token_cu not in json.dumps(kq) and token_moi not in json.dumps(kq)


def test_da_het_han_van_thu_lam_moi(mod):
    token_cu = _jwt_gia(exp=int(time.time()) - 3600)
    token_moi = _jwt_gia(exp=int(time.time()) + 30 * 86400)
    _gan_tai_khoan(mod, token_cu)
    dv = _gan_dich_vu_gia(mod, token_moi=token_moi)
    kq = chay(mod.lam_moi_jwt(_khoa_cua(mod)))
    assert kq["ok"] is True and kq["ma"] == "da_lam_moi", kq
    assert dv.dem_lam_moi == 1


# ── exp hỏng: can_nguoi, không làm bừa ────────────────────────────────────

def test_exp_hong_danh_dau_can_nguoi(mod):
    _gan_tai_khoan(mod, "token-khong-phai-jwt")
    dv = _gan_dich_vu_gia(mod)
    kq = chay(mod.lam_moi_jwt(_khoa_cua(mod)))
    assert kq["ok"] is False and kq["ma"] == "exp_khong_doc_duoc", kq
    assert kq.get("can_nguoi") is True
    assert dv.dem_ds == 0 and dv.dem_lam_moi == 0


# ── không ghép được hồ sơ: can_nguoi, không gọi làm mới bừa ───────────────

def test_khong_ghep_duoc_ho_so(mod):
    token = _jwt_gia(exp=int(time.time()) + 86400)
    _gan_tai_khoan(mod, token, ho_so="hs-khac-khong-ton-tai")
    dv = _gan_dich_vu_gia(mod, token_moi=_jwt_gia(exp=int(time.time()) + 30 * 86400))
    kq = chay(mod.lam_moi_jwt(_khoa_cua(mod)))
    assert kq["ok"] is False and kq["ma"] == "khong_ghep_duoc_ho_so", kq
    assert kq.get("can_nguoi") is True
    assert dv.dem_ds == 1 and dv.dem_lam_moi == 0  # chỉ liệt kê, không làm mới


# ── 401: dừng vĩnh viễn, không thử lại mãi ─────────────────────────────────

def test_401_dung_vinh_vien(mod):
    token = _jwt_gia(exp=int(time.time()) + 86400)
    _gan_tai_khoan(mod, token)
    dv = _gan_dich_vu_gia(mod, che_do="loi401")
    khoa = _khoa_cua(mod)
    kq = chay(mod.lam_moi_jwt(khoa))
    assert kq["ok"] is False and kq["ma"] == "ho_so_dang_xuat", kq
    assert kq.get("can_nguoi") is True
    assert dv.dem_lam_moi == 1
    # Lượt sau: KHÔNG gọi lại dịch vụ (dừng vĩnh viễn cho khoá này).
    kq2 = chay(mod.lam_moi_jwt(khoa))
    assert kq2["ma"] == "can_nguoi", kq2
    assert dv.dem_lam_moi == 1


# ── dịch vụ sập: nghỉ luỹ thừa + trần ngày ─────────────────────────────────

def test_dich_vu_sap_nghi_luy_thua(mod):
    token = _jwt_gia(exp=int(time.time()) + 86400)
    _gan_tai_khoan(mod, token)
    dv = _gan_dich_vu_gia(mod, che_do="sap")
    khoa = _khoa_cua(mod)
    kq = chay(mod.lam_moi_jwt(khoa))
    assert kq["ok"] is False and kq["ma"] == "loi_dich_vu", kq
    assert dv.dem_lam_moi == 1
    # Gọi ngay lại: đang nghỉ luỹ thừa → không gọi dịch vụ nữa.
    kq2 = chay(mod.lam_moi_jwt(khoa))
    assert kq2["ma"] == "dang_nghi_luy_thua", kq2
    assert dv.dem_lam_moi == 1


# ── token mới giống cũ: không thay, không lặp ──────────────────────────────

def test_token_moi_giong_cu_khong_thay(mod):
    token = _jwt_gia(exp=int(time.time()) + 86400)
    _gan_tai_khoan(mod, token)
    dv = _gan_dich_vu_gia(mod, che_do="giong_cu")
    dv.token_cu = token
    khoa = _khoa_cua(mod)
    kq = chay(mod.lam_moi_jwt(khoa))
    assert kq["ok"] is False and kq["ma"] == "token_moi_giong_cu", kq
    with mod._khoa:
        assert mod._tai_khoan[0].token == token  # không thay gì


# ── chạy chồng: single-flight ─────────────────────────────────────────────

def test_chay_chong_chi_mot_luot_goi(mod):
    token_cu = _jwt_gia(exp=int(time.time()) + 86400)
    token_moi = _jwt_gia(exp=int(time.time()) + 30 * 86400)
    _gan_tai_khoan(mod, token_cu)
    dv = _gan_dich_vu_gia(mod, token_moi=token_moi)

    orig = dv.lam_moi_jwt

    async def cham(ho_so):
        await asyncio.sleep(0.3)
        return await orig(ho_so)

    dv.lam_moi_jwt = cham
    khoa = _khoa_cua(mod)

    async def hai_luot():
        return await asyncio.gather(mod.lam_moi_jwt(khoa), mod.lam_moi_jwt(khoa))

    kq1, kq2 = chay(hai_luot())
    mas = {kq1["ma"], kq2["ma"]}
    assert mas == {"da_lam_moi", "dang_chay"}, (kq1, kq2)
    assert dv.dem_lam_moi == 1  # chỉ một lượt gọi dịch vụ thật


# ── vòng lõi thật quét module ─────────────────────────────────────────────

def test_vong_loi_quet_va_lam_moi(mod):
    from app import lam_moi_jwt as loi_vong
    token_cu = _jwt_gia(exp=int(time.time()) + 86400)
    token_moi = _jwt_gia(exp=int(time.time()) + 30 * 86400)
    _gan_tai_khoan(mod, token_cu)
    dv = _gan_dich_vu_gia(mod, token_moi=token_moi)
    # VongLamMoiJWT thật, KHÔNG bat_dau (không vòng nền đua); ngu no-op cho nhanh.
    vong = loi_vong.VongLamMoiJWT(mod, "chatgpt-web",
                                  ngu=lambda giay: asyncio.sleep(0))

    async def mot_vong():
        return await vong.chay_mot_vong()

    thong_ke = chay(mot_vong())
    assert thong_ke.get("da_lam_moi") == 1, thong_ke
    assert dv.dem_lam_moi == 1
    with mod._khoa:
        assert mod._tai_khoan[0].token == token_moi


# ── jwt_trang_thai: có hạn dùng, không lộ bí mật ───────────────────────────

def test_jwt_trang_thai_khong_lo_token(mod):
    token = _jwt_gia(exp=int(time.time()) + 3 * 86400)
    _gan_tai_khoan(mod, token)
    tt = mod.jwt_trang_thai()
    assert tt["nguong_ngay"] == 2.0
    assert len(tt["khoa"]) == 1
    muc = tt["khoa"][0]
    assert muc["het_han"] and muc["con_lai_ngay"] is not None
    assert 2.9 < muc["con_lai_ngay"] < 3.1
    assert muc["canh_bao"] is False  # còn 3 ngày > ngưỡng 2 ngày
    blob = json.dumps(tt)
    assert token not in blob and EMAIL_GIA in blob
