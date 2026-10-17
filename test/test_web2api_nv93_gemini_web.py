"""Test NV93 cho module gemini-web: «hồ sơ Google + tái dùng» + NV91-P3.

- `tai_dung_ho_so(ho_so, trinh_duyet)`: «Đăng nhập bằng Google» TRONG hồ sơ
  (bấm nút Đăng nhập → chọn tài khoản Google → về gemini.google.com);
  đi tắt khi hồ sơ đã có phiên; CAPTCHA/2FA → can_nguoi.
- Phiên: cookie __Secure-1PSID/__Secure-1PSIDTS đọc từ hồ sơ Google.
- NV91-P3: model theo TÀI KHOẢN (Free thấy ít hơn Pro); mã ỔN ĐỊNH, nhãn tách riêng;
  /v1/models = HỢP các tài khoản; định tuyến chỉ chọn tài khoản CÓ model;
  thiếu → lỗi rõ, KHÔNG fallback âm thầm.

Trình duyệt GIẢ, máy chủ HTTP giả. Không Internet, không Chrome.
Chuỗi bí mật trong test đều là chuỗi GIẢ rõ ràng.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import httpx
import pytest

from app import kho
from app.khoi_phuc_phien import TrinhDuyetGia
from app.tai_khoan_hang_loat import KetQuaDangNhap

from ho_tro_nv93 import TrinhDuyetChuyenDoi, TrinhDuyetLoi

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "gemini-web"
TEN_GOI = "gemini_web_nv93_mod_test"

PSID_GIA_PRO = "psid-gia-nv93-pro-0001"
PSID_GIA_FREE = "psid-gia-nv93-free-0002"
PSIDTS_GIA = "psidts-gia-nv93-0003"
EMAIL_GIA = "ban-gia-nv93@example.com"
HO_SO_GIA = "hs-gia-nv93-0001"


def _tai_goi():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC)]
    sys.modules[TEN_GOI] = goi
    for ten_tep in ("client", "converter", "module"):
        spec = importlib.util.spec_from_file_location(
            f"{TEN_GOI}.{ten_tep}", THU_MUC / f"{ten_tep}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{TEN_GOI}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return (sys.modules[f"{TEN_GOI}.module"],
            sys.modules[f"{TEN_GOI}.client"])


mod_module, mod_client = _tai_goi()
Module = mod_module.Module
MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


def chay(coro):
    return asyncio.run(coro)


def _may_chu_gia(model_theo_psid=None):
    """Máy chủ giả: trang chủ (SNlM0e + danh sách model theo tài khoản)."""
    model_theo_psid = model_theo_psid or {}

    def xu_ly(req):
        cookie = req.headers.get("cookie", "")
        psid = ""
        for phan in cookie.split(";"):
            k, _, v = phan.partition("=")
            if k.strip() == "__Secure-1PSID":
                psid = v.strip()
        if req.url.path == "/":
            if psid in (PSID_GIA_PRO, PSID_GIA_FREE):
                ds_model = model_theo_psid.get(psid, ["gemini-3-flash"])
                # trang chủ giả: SNlM0e + danh sách model nhúng theo cấu trúc
                html = ('<html><body>"SNlM0e":"snlm0e-gia-123"'
                        '<script id="model-list" type="application/json">'
                        + json.dumps({"models": ds_model}) +
                        "</script></body></html>")
                return httpx.Response(200, text=html)
            return httpx.Response(401, text="het han")
        return httpx.Response(404, text="khong co")

    return httpx.MockTransport(xu_ly)


def _mod_moi(tmp_path, monkeypatch, model_theo_psid=None):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    mod = Module(dict(MANIFEST))
    mod.kho = kho.kho_rieng("gemini-web")
    transport = _may_chu_gia(model_theo_psid)

    def tao_client(psid, psidts=None):
        return mod_client.GeminiWebClient(
            psid, psidts, base_url="http://127.0.0.1:9",
            transport=transport)
    mod._tao_client = tao_client
    return mod


@pytest.fixture
def mod(tmp_path, monkeypatch):
    return _mod_moi(tmp_path, monkeypatch)


def _ho_so_gia(hs_id=HO_SO_GIA, email=EMAIL_GIA):
    return {"id": hs_id, "email": email, "trang_thai": "da_dang_nhap"}


def _td_co_phien(psid=PSID_GIA_PRO):
    return TrinhDuyetGia(trang={
        "https://gemini.google.com/": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {"__Secure-1PSID": psid,
                       "__Secure-1PSIDTS": PSIDTS_GIA},
        }})


# ───────────── tai_dung_ho_so ─────────────

def test_tai_dung_ho_so_di_tat(mod):
    """Hồ sơ đã có cookie __Secure-1PSID còn hiệu lực → đi tắt: không bấm,
    không điền gì thêm."""
    td = _td_co_phien()
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert isinstance(kq, KetQuaDangNhap)
    assert kq.thanh_cong, kq.ma_loi
    ten_buoc = [h[0] for h in td.hanh_dong]
    assert "bam" not in ten_buoc
    assert "dien" not in ten_buoc
    ds = mod.liet_ke_tai_khoan()
    assert len(ds) == 1
    assert ds[0]["ho_so"] == HO_SO_GIA


def test_tai_dung_ho_so_luong_day_du(mod):
    """Chưa có phiên → mở gemini.google.com → bấm Đăng nhập → chọn tài khoản
    Google trong hồ sơ → về trang đã đăng nhập (chuyển trạng thái sau bấm)."""
    td = TrinhDuyetChuyenDoi(
        trang={
            "https://gemini.google.com/": {"tin_hieu": [], "cookie": {}},
        },
        sau_bam={
            "https://gemini.google.com/": {
                "url": None,  # ở yên trang, đổi trạng thái sau SSO
                "trang": {"tin_hieu": ["da_dang_nhap"],
                          "cookie": {"__Secure-1PSID": PSID_GIA_PRO,
                                     "__Secure-1PSIDTS": PSIDTS_GIA}},
            },
        })
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert kq.thanh_cong, kq.ma_loi
    ten_buoc = [h[0] for h in td.hanh_dong]
    assert "bam" in ten_buoc  # bấm nút Đăng nhập / chọn tài khoản Google
    assert "dien" not in ten_buoc  # không nhập lại email/mật khẩu


def test_tai_dung_ho_so_gap_captcha_thi_can_nguoi(mod):
    td = TrinhDuyetGia(trang={
        "https://gemini.google.com/": {
            "tin_hieu": ["captcha"],
            "cookie": {},
        }})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"


# ───────────── NV91-P3: model theo tài khoản ─────────────

def test_models_hop_theo_tai_khoan_free_va_pro(tmp_path, monkeypatch):
    mod = _mod_moi(tmp_path, monkeypatch, model_theo_psid={
        PSID_GIA_PRO: ["gemini-3-flash", "gemini-3-pro"],
        PSID_GIA_FREE: ["gemini-3-flash"],
    })
    chay(mod.tai_dung_ho_so(_ho_so_gia(), _td_co_phien(PSID_GIA_PRO)))
    chay(mod.tai_dung_ho_so(_ho_so_gia("hs-free", "f@example.com"),
                            _td_co_phien(PSID_GIA_FREE)))
    ds = chay(mod.models())
    id_model = [m["id"] for m in ds]
    # hợp: pro có cả hai, free chỉ có flash
    assert "gemini-web/gemini-3-flash" in id_model
    assert "gemini-web/gemini-3-pro" in id_model
    # mỗi model kèm thông tin tài khoản/gói dùng được
    pro = next(m for m in ds if m["id"] == "gemini-web/gemini-3-pro")
    assert "available_cho" in pro or "goi" in pro


def test_dinh_tuyen_model_pro_khong_roi_ve_free(tmp_path, monkeypatch):
    """Tài khoản Free không có gemini-3-pro → yêu cầu pro phải lỗi rõ."""
    mod = _mod_moi(tmp_path, monkeypatch, model_theo_psid={
        PSID_GIA_FREE: ["gemini-3-flash"],
    })
    chay(mod.tai_dung_ho_so(_ho_so_gia("hs-free", "f@example.com"),
                            _td_co_phien(PSID_GIA_FREE)))
    ma, _td, _body = chay(mod.chat({"model": "gemini-web/gemini-3-pro",
                       "messages": [{"role": "user", "content": "hi"}]},
                      stream=False))
    assert ma in (400, 404), f"mong 400/404, được {ma}"
    assert b"pro" in _body.lower()


def test_ma_model_on_dinh_nhan_doi_khong_vo(tmp_path, monkeypatch):
    """Đổi nhãn hiển thị không làm vỡ id đã lưu."""
    mod = _mod_moi(tmp_path, monkeypatch, model_theo_psid={
        PSID_GIA_PRO: ["gemini-3-flash", "gemini-3-pro"],
    })
    chay(mod.tai_dung_ho_so(_ho_so_gia(), _td_co_phien(PSID_GIA_PRO)))
    ds = chay(mod.models())
    # id là mã ổn định, không phải nhãn webui
    for m in ds:
        assert m["id"].startswith("gemini-web/gemini-")
        assert " " not in m["id"]


# ───────────── go_tai_khoan / liet_ke ─────────────

def test_go_tai_khoan_va_khong_lo_bi_mat(mod):
    chay(mod.tai_dung_ho_so(_ho_so_gia(), _td_co_phien()))
    ds = mod.liet_ke_tai_khoan()
    assert len(ds) == 1
    assert PSID_GIA_PRO not in json.dumps(ds)
    assert "phien" not in ds[0] and "cookie" not in ds[0]
    mod.go_tai_khoan(ds[0]["khoa"])
    assert mod.liet_ke_tai_khoan() == []


# ───────────── tai_dung_ho_so: các trường hợp lỗi ─────────────

def test_tai_dung_ho_so_khong_co_trinh_duyet_thi_can_nguoi(mod):
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), None))
    assert isinstance(kq, KetQuaDangNhap)
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"


def test_tai_dung_ho_so_gap_xac_minh_thi_can_nguoi(mod):
    td = TrinhDuyetGia(trang={
        "https://gemini.google.com/": {
            "tin_hieu": ["xac_minh"],
            "cookie": {},
        }})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"
    assert mod.liet_ke_tai_khoan() == []


def test_tai_dung_ho_so_phien_khong_dung_duoc(mod):
    """Cookie đọc được nhưng máy chủ giả báo hết hạn → lỗi rõ."""
    td = TrinhDuyetGia(trang={
        "https://gemini.google.com/": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {"__Secure-1PSID": "psid-het-han-gia",
                       "__Secure-1PSIDTS": PSIDTS_GIA},
        }})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "phien_khong_dung_duoc"
    assert mod.liet_ke_tai_khoan() == []


def test_tai_dung_ho_so_het_gio_cho_phien(monkeypatch, mod):
    """Không bao giờ đăng nhập được → hết lượt chờ → lỗi rõ (sleep giả)."""
    async def ngu_gia(giay):
        return None
    monkeypatch.setattr(asyncio, "sleep", ngu_gia)
    td = TrinhDuyetGia(trang={
        "https://gemini.google.com/": {"tin_hieu": [], "cookie": {}},
    })
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "khong_lay_duoc_phien"
    assert mod.liet_ke_tai_khoan() == []


def test_tai_dung_ho_so_loi_la_dong_cua(mod):
    """Trình duyệt ném lỗi lạ ở chỗ quyết định → đóng cửa."""
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), TrinhDuyetLoi()))
    assert isinstance(kq, KetQuaDangNhap)
    assert not kq.thanh_cong
    assert kq.ma_loi
    assert mod.liet_ke_tai_khoan() == []


def test_bi_mat_khong_vao_log(mod, caplog):
    """Bí mật (cookie PSID) không lọt vào log khi tái dùng hồ sơ."""
    import logging
    with caplog.at_level(logging.DEBUG):
        chay(mod.tai_dung_ho_so(_ho_so_gia(), _td_co_phien()))
    nhat_ky = "\n".join(r.getMessage() for r in caplog.records)
    assert PSID_GIA_PRO not in nhat_ky
    assert PSIDTS_GIA not in nhat_ky


def test_hai_module_cung_ho_so_doc_lap(tmp_path, monkeypatch):
    """Hai module (kho riêng id khác nhau) cùng một hồ sơ → gỡ bên này không
    động tài khoản bên kia."""
    mod1 = _mod_moi(tmp_path, monkeypatch)
    mod2 = _mod_moi(tmp_path, monkeypatch)
    mod2.kho = kho.kho_rieng("chatgpt-web")  # id kho khác, cùng thư mục dữ liệu
    chay(mod1.tai_dung_ho_so(_ho_so_gia(), _td_co_phien()))
    chay(mod2.tai_dung_ho_so(_ho_so_gia(), _td_co_phien()))
    assert len(mod1.liet_ke_tai_khoan()) == 1
    assert len(mod2.liet_ke_tai_khoan()) == 1
    mod1.go_tai_khoan(mod1.liet_ke_tai_khoan()[0]["khoa"])
    assert mod1.liet_ke_tai_khoan() == []
    assert len(mod2.liet_ke_tai_khoan()) == 1  # không bị động
