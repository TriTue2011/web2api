"""NV217 — ĐO vòng đời tài khoản AI qua 3 lớp (bước «a»: đo, CHƯA sửa mã).

Qua LÕI THẬT (TestClient + routes thật) với DỊCH VỤ ĐĂNG NHẬP GIẢ NGHIÊM
(``MayDangNhapGia``): chỉ công nhận đúng tên hồ sơ trong tập cho phép,
TỪ CHỐI hồ sơ lạ (``lam_moi_jwt`` trả ``ho_so_la``). Module sở hữu là
module chatgpt-web THẬT (nạp qua importlib như test NV207) để đo đúng
``gan_jwt_tu_dang_nhap`` / ``BoLamMoiJWT`` thật.

Mỗi ô a–f một test. Test assert HÀNH VI MONG MUỐN; ô nào đỏ thì báo cáo
ghi nguyên nhân dự đoán + dòng mã cụ thể. Không sửa mã nguồn.

Mọi token/JWT/email đều GIẢ, tự dựng (mã thật chỉ đọc claim, không kiểm
chữ ký). Không Internet, không Chrome.
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
from types import SimpleNamespace

import pytest

GOC = Path(__file__).resolve().parents[1]
_CW = GOC / "modules" / "chatgpt-web"
_TEN_GOI = "chatgpt_web_nv217_mod_test"

_MK = "MatKhauGia217!"
_TOTP = "JBSWY3DPEHPK3PXP"  # base32 hợp lệ
_KHOA_QT = "khoa-qt-nv217-do-0123456789abcdef"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}
_EXEC = "may-dang-nhap-gia-nv217"
_CHU = "chatgpt-web"  # module sở hữu THẬT trong các test a/b/d/f

_HO_SO_HOP_LE = frozenset({"chatgpt-hs-gia-hop-le"})
_EMAIL_A = "do-a-nv217@example.com"
_EMAIL_B = "do-b-nv217@example.com"


def _b64url(obj: dict) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwt_gia(*, email: str, exp: float | None = None) -> str:
    payload = {"email": email}
    if exp is not None:
        payload["exp"] = exp
    return _b64url({"alg": "none", "typ": "JWT"}) + "." + _b64url(payload) + ".chuky-gia-nv217"


def _tai_chatgpt_web():
    for ten in [t for t in list(sys.modules) if t == _TEN_GOI or t.startswith(_TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(_TEN_GOI)
    goi.__path__ = [str(_CW)]
    sys.modules[_TEN_GOI] = goi
    for tep in ("client", "converter", "lam_moi_jwt", "module"):
        spec = importlib.util.spec_from_file_location(f"{_TEN_GOI}.{tep}", _CW / f"{tep}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{_TEN_GOI}.{tep}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{_TEN_GOI}.module"], sys.modules[f"{_TEN_GOI}.lam_moi_jwt"]


_mod_cw, _mod_lmj = _tai_chatgpt_web()
MANIFEST_CW = json.loads((_CW / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture()
def moi_truong(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.so_module import SoModule

    data_dir = tmp_path / "data-loi"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", _KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    with TestClient(app) as client:
        client.app.state.so = SoModule()
        yield client


class MayDangNhapGia:
    """Dịch vụ đăng nhập GIẢ NGHIÊM: chỉ công nhận hồ sơ trong tập cho phép."""

    manifest = {"id": _EXEC}

    def __init__(self):
        self.dang_nhap_goi: list[str] = []
        self.token_phat_hanh: dict[str, str] = {}  # email -> access_token vừa phát
        self.ds_goi = 0
        self.lam_moi_goi: list[str] = []

    async def dang_nhap(self, dong):
        from app.tai_khoan_hang_loat import KetQuaDangNhap

        email = str(getattr(dong, "email", "") or "")
        self.dang_nhap_goi.append(email)
        hs = "chatgpt-hs-gia-hop-le"
        if hs not in _HO_SO_HOP_LE:
            kq = KetQuaDangNhap(False)
            kq.ma_loi = "ho_so_la"
            return kq
        kq = KetQuaDangNhap(True)
        token = _jwt_gia(email=email, exp=time.time() + 30 * 86400)
        self.token_phat_hanh[email] = token
        object.__setattr__(kq, "jwt_moi", {
            "access_token": token,
            "ho_so": hs, "email": email})
        return kq

    async def dung(self):
        return None

    # Hình dạng dịch vụ may-dang-nhap cho NV207:
    async def danh_sach_ho_so(self):
        self.ds_goi += 1
        return {"ok": True, "profiles": sorted(_HO_SO_HOP_LE), "nguon_ngoai": True}

    async def lam_moi_jwt(self, ho_so):
        self.lam_moi_goi.append(ho_so)
        if ho_so not in _HO_SO_HOP_LE:
            return {"ok": False, "ma_loi": "ho_so_la", "nguon_ngoai": True}
        return {"ok": True, "access_token": _jwt_gia(email="lam-moi@example.com",
                                                    exp=time.time() + 30 * 86400),
                "nguon_ngoai": True}


def _cai(so, mid, doi_tuong, manifest=None):
    man = dict(manifest or getattr(doi_tuong, "manifest", None) or {"id": mid})
    ns = SimpleNamespace(id=mid, tien_to=mid, manifest=man,
                         truong_cau_hinh=list(man.get("truong_cau_hinh", [])),
                         tao=lambda: doi_tuong)
    so.catalog[mid] = ns
    so.dang_chay[mid] = doi_tuong
    return doi_tuong


def _chatgpt_web_that():
    from app import kho as kho_mod

    m = _mod_cw.Module(dict(MANIFEST_CW))
    m.kho = kho_mod.kho_rieng("chatgpt-web")
    return m


def _luu(client, email, module_id=_CHU, **them):
    body = {"email": email, "mat_khau": _MK, "totp_secret": _TOTP, "module_id": module_id}
    body.update(them)
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json=body)
    assert r.status_code == 200, r.text
    return r.json()["tai_khoan"]


def _dang_nhap(client, tk_id):
    return client.post(f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap",
                       headers=_QT, json={"module_id": _EXEC})


# ── a. lưu → đăng nhập → khoá trong chatgpt-web CÓ ho_so + đúng email ───────

def test_a_dang_nhap_xong_khoa_co_ho_so_va_dung_email(moi_truong):
    client = moi_truong
    so = client.app.state.so
    cw = _cai(so, _CHU, _chatgpt_web_that(), MANIFEST_CW)
    _cai(so, _EXEC, MayDangNhapGia())
    tk = _luu(client, _EMAIL_A)
    r = _dang_nhap(client, tk["id"])
    assert r.status_code == 200, r.text
    assert r.json()["thanh_cong"] is True
    ds = cw.liet_ke_tai_khoan()
    assert len(ds) == 1, ds
    assert ds[0]["ho_so"] == "chatgpt-hs-gia-hop-le", ds
    assert ds[0]["email"] == _EMAIL_A, ds


# ── b. khoá cũ nhập tay + cùng email → THAY, không sinh khoá thứ hai ─────────

def test_b_dang_nhap_thay_khoa_cu_cung_email(moi_truong):
    client = moi_truong
    so = client.app.state.so
    cw = _cai(so, _CHU, _chatgpt_web_that(), MANIFEST_CW)
    exe = _cai(so, _EXEC, MayDangNhapGia())
    token_cu = _jwt_gia(email=_EMAIL_B, exp=time.time() + 30 * 86400)
    kq_them = cw.gan_jwt_tu_dang_nhap(_EMAIL_B, token_cu, None)  # nhập tay: chưa có ho_so
    assert kq_them["ok"] is True
    assert cw.liet_ke_tai_khoan()[0]["ho_so"] in (None, ""), "tiền đề: khoá cũ chưa có ho_so"
    tk = _luu(client, _EMAIL_B)
    r = _dang_nhap(client, tk["id"])
    assert r.status_code == 200 and r.json()["thanh_cong"] is True, r.text
    ds = cw.liet_ke_tai_khoan()
    assert len(ds) == 1, f"phải còn đúng 1 khoá, nhận được {len(ds)}: {ds}"
    assert ds[0]["ho_so"] == "chatgpt-hs-gia-hop-le", ds
    assert ds[0]["email"] == _EMAIL_B, ds


# ── c. NV207: chạm ngưỡng + có ho_so → làm mới; không ho_so → cần người ──────

def _khoa_moi(mod, email, con_lai_gio, ho_so):
    tk = _mod_cw._TaiKhoan(_jwt_gia(email=email, exp=time.time() + con_lai_gio * 3600))
    tk.email = email
    tk.ho_so = ho_so
    with mod._khoa:
        mod._tai_khoan.append(tk)
        mod._dich_vu_dang_nhap_url = "http://127.0.0.1:9"
        mod._khoa_dich_vu_dang_nhap = "khoa-gia"
    return mod._khoa_tu_token(tk.token)


def test_c_lam_moi_jwt_co_ho_so_thi_moi_khong_ho_so_thi_can_nguoi(moi_truong):
    dv = MayDangNhapGia()
    cw = _chatgpt_web_that()
    khoa_co = _khoa_moi(cw, "co-hs-nv217@example.com", con_lai_gio=20, ho_so="chatgpt-hs-gia-hop-le")
    khoa_khong = _khoa_moi(cw, "khong-hs-nv217@example.com", con_lai_gio=20, ho_so=None)
    bo = _mod_lmj.BoLamMoiJWT(cw, tao_client_dich_vu=lambda: dv)

    kq_co = asyncio.run(bo.lam_moi(khoa_co))
    assert kq_co.get("ma") == "da_lam_moi", kq_co
    assert dv.lam_moi_goi == ["chatgpt-hs-gia-hop-le"], dv.lam_moi_goi

    kq_khong = asyncio.run(bo.lam_moi(khoa_khong))
    assert kq_khong.get("can_nguoi") is True, kq_khong
    assert kq_khong.get("ma") == "khong_ghep_duoc_ho_so", kq_khong
    assert dv.lam_moi_goi == ["chatgpt-hs-gia-hop-le"], \
        "không ho_so thì KHÔNG được gọi làm mới thêm lần nào"


# ── d. tam_dung bị bỏ qua ở 3 đường ─────────────────────────────────────────

def test_d_tam_dung_bo_qua_dang_nhap_tay_hang_loat_tu_dang_nhap_lai(moi_truong):
    from app import kho_tai_khoan as ktk

    client = moi_truong
    so = client.app.state.so
    cw = _cai(so, _CHU, _chatgpt_web_that(), MANIFEST_CW)
    exe = _cai(so, _EXEC, MayDangNhapGia())

    email_nghi = "nghi-nv217@example.com"
    email_chay = "chay-nv217@example.com"
    tk_nghi = _luu(client, email_nghi)
    tk_chay = _luu(client, email_chay)
    r = client.post(f"/api/tai-khoan/da-luu/{tk_nghi['id']}/tam-dung",
                    headers=_QT, json={"tam_dung": True})
    assert r.status_code == 200, r.text

    # 1) đăng nhập tay → 403 tam_dung, executor KHÔNG được gọi
    r = _dang_nhap(client, tk_nghi["id"])
    assert r.status_code == 403, r.text
    assert r.json().get("ma_loi") == "tam_dung" or "tam_dung" in r.text, r.text
    assert email_nghi not in exe.dang_nhap_goi

    # 2) hàng loạt → bỏ qua tài khoản tạm dừng
    r = client.post("/api/tai-khoan/da-luu/dang-nhap-theo-module", headers=_QT,
                    json={"module_id": _EXEC, "module_so_huu": _CHU})
    assert r.status_code == 200, r.text
    han = time.time() + 20
    tt = None
    while time.time() < han:
        tt = client.get("/api/tai-khoan/dang-nhap-hang-loat/trang-thai", headers=_QT).json()
        if tt.get("trang_thai_luot") == "hoan_thanh":
            break
        time.sleep(0.2)
    assert tt and tt.get("trang_thai_luot") == "hoan_thanh", tt
    assert email_chay in exe.dang_nhap_goi, exe.dang_nhap_goi
    assert email_nghi not in exe.dang_nhap_goi, exe.dang_nhap_goi

    # 3) tự đăng nhập lại → {"thu": False, "ly_do": "tam_dung"}, không gọi module
    ktk.dat_tu_dang_nhap_lai(True)
    goi_truoc = list(exe.dang_nhap_goi)
    kq = asyncio.run(ktk.thu_dang_nhap_lai(module=exe, module_id=_EXEC, dinh_danh=email_nghi))
    assert kq == {"thu": False, "ly_do": "tam_dung"}, kq
    assert exe.dang_nhap_goi == goi_truoc


# ── e. MỘT tài khoản Google dùng cho nhiều đích (NV224 viết lại) ────────────
# Bản NV217 mong kho tách 2 bản ghi cùng email theo module_id. Chủ máy chốt
# mô hình khác ở NV224: MỘT bản ghi/email giữ user + mật khẩu + OTP; đích
# dùng là danh sách ``dung_cho`` TÍCH LŨY (lưu thêm không đè). Test này
# viết lại theo mô hình đó (bài học NV98: cập nhật test cũ theo hành vi mới).

def test_e_hai_tai_khoan_cung_email_module_id_khac_nhau(moi_truong):
    from app import kho_tai_khoan as ktk

    client = moi_truong
    so = client.app.state.so
    _MAN_GOOGLE = {"nhom": {"id": "google", "ten": "Google"}}
    _cai(so, "gemini-web", SimpleNamespace(manifest={"id": "gemini-web"}),
         manifest={"id": "gemini-web", **_MAN_GOOGLE})
    _cai(so, "antigravity", SimpleNamespace(manifest={"id": "antigravity"}),
         manifest={"id": "antigravity", **_MAN_GOOGLE})
    email = "chung-nv217@example.com"
    _luu(client, email, module_id="gemini-web",
         loai_tai_khoan="gmail", dich_dung="gemini")
    _luu(client, email, module_id="antigravity",
         loai_tai_khoan="gmail", dich_dung="antigravity")
    ds = [b for b in ktk.liet_ke() if (ktk.tim_theo_id(b["id"]) or {}).get("email") == email]
    # Mô hình NV224: vẫn đúng MỘT bản ghi cho email đó…
    assert len(ds) == 1, f"phải còn đúng 1 bản ghi cho cùng email: {ds}"
    # …giữ cả mật khẩu + TOTP, và dung_cho tích lũy cả hai đích.
    assert ds[0]["co_mat_khau"] is True and ds[0]["co_totp"] is True, ds
    assert ds[0]["dung_cho"] == ["gemini", "antigravity"], ds


# ── f. API không lộ mật khẩu / TOTP / token ──────────────────────────────────

def test_f_api_khong_lo_bi_mat(moi_truong):
    client = moi_truong
    so = client.app.state.so
    cw = _cai(so, _CHU, _chatgpt_web_that(), MANIFEST_CW)
    exe = _cai(so, _EXEC, MayDangNhapGia())

    email = "lo-bi-mat-nv217@example.com"
    cac_than = []

    # 1) lưu → phản hồi công khai
    tk = _luu(client, email)
    # 2) liệt kê
    r = client.get("/api/tai-khoan/da-luu", headers=_QT)
    assert r.status_code == 200, r.text
    cac_than.append(("liet-ke", r.text))
    # 3) đăng nhập thành công (executor trả jwt_moi thật)
    r = _dang_nhap(client, tk["id"])
    assert r.status_code == 200, r.text
    cac_than.append(("dang-nhap-ok", r.text))
    jwt_phat_hanh = exe.token_phat_hanh.get(email, "")
    assert jwt_phat_hanh, "tiền đề: executor đã phát token"
    # 4) đăng nhập lỗi (id lạ)
    r = client.post("/api/tai-khoan/da-luu/id-khong-co/dang-nhap", headers=_QT,
                    json={"module_id": _EXEC})
    cac_than.append(("dang-nhap-404", r.text))
    # 5) jwt-trang-thai
    r = client.get(f"/api/modules/{_CHU}/jwt-trang-thai", headers=_QT)
    assert r.status_code == 200, r.text
    cac_than.append(("jwt-trang-thai", r.text))
    # 6) nhật ký
    r = client.get("/api/nhat-ky", headers=_QT)
    assert r.status_code == 200, r.text
    cac_than.append(("nhat-ky", r.text))

    for ten, than in cac_than:
        assert _MK not in than, f"{ten}: LỘ mật khẩu"
        assert _TOTP not in than, f"{ten}: LỘ totp_secret"
        assert jwt_phat_hanh not in than, f"{ten}: LỘ token đầy đủ"
