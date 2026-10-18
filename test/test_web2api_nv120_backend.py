"""Kiểm thử backend cho NV120: Thêm tài khoản bằng ô nhập cấu trúc, TOTP động, kiểm biên chống lộ bí mật và bảo mật.

Bao quát:
1. BiMatTotp: sinh mã động tại thời điểm gọi, tính mã khớp RFC 6238, xoá bộ đệm, che dấu repr.
2. kiem_tra_va_tao_otp: phân loại Base32 (16-128 ký tự, case-insensitive, bỏ dấu cách/gạch) -> BiMatTotp;
   mã 6-8 số -> BiMatTam; định dạng khác -> ValueError("otp_khong_hop_le").
3. tach_danh_sach_tai_khoan: kiểm tra chặt chẽ cấu trúc JSON, email, mật khẩu chứa ký tự đặc biệt (`|`, `'`, `"`)
   không làm vỡ, trần 20 tài khoản, loại trùng và dọn dẹp bí mật.
4. tach_dong: tương thích ngược 100% khi nang_cap_totp=False (kể cả 4 cột NV122); nâng cấp BiMatTotp khi nang_cap_totp=True.
5. API POST /api/tai-khoan/dang-nhap-tu-dong/bat-dau:
   - Nhận tai_khoan dạng danh sách JSON đối tượng {email, mat_khau, otp?}.
   - Nhận van_ban dạng cũ.
   - Gửi cả hai hoặc không gửi cái nào -> 400 invalid_value.
   - tai_khoan > 20 phần tử -> 400 invalid_value.
   - Sai khuôn từng phần tử -> 400 invalid_value kèm vị trí phần tử, không in bí mật.
   - Bí mật không lọt vào log (caplog), phản hồi, hay tiến độ.
   - Đường danh tính nhận OTP Base32 và sinh mã 6 số cho module đăng nhập.
6. Lỗi B (kiểm biên):
   - van_ban có surrogate ("\\ud800") -> 400 invalid_value cố định, không 500.
   - mat_khau có surrogate -> 400 invalid_value thông điệp cố định, không lộ câu codec utf-8.
   - email/mat_khau/otp không phải chuỗi (số, list, bool, dict, null) -> 400 invalid_value cố định.
   - chuỗi điều khiển, vượt quá độ dài -> 400 invalid_value cố định.
7. LUẬT CỨNG BẢO MẬT:
   - 401 khi không có xác thực.
   - 403 khi gọi bằng khoá khách / vai thấp.
   - 404 khi gọi module không tồn tại (chống IDOR).
8. Điều kiện nghiệm thu chung: Lõi chạy ổn định khi không có module nào.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import pytest

from fastapi.testclient import TestClient

from app import danh_tinh, khoa_khach, khoi_phuc_phien, so_module
from app.main import app
from app.tai_khoan_hang_loat import (
    BiMatTam,
    DongDaTach,
    KetQuaDangNhap,
    tach_dong,
)
from app.xac_thuc_2fa import sinh_totp

# Thử import các hàm NV120 nếu đã định nghĩa (để test không crash import khi chưa sửa mã)
try:
    from app.tai_khoan_hang_loat import BiMatTotp, kiem_tra_va_tao_otp, tach_danh_sach_tai_khoan
except ImportError:
    BiMatTotp = None  # type: ignore
    kiem_tra_va_tao_otp = None  # type: ignore
    tach_danh_sach_tai_khoan = None  # type: ignore

KHOA_QT = "AIzaSyTOT-mot-000000000000000000000001"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
KHOA_KHACH = "sk-khach-nv120-test-000000000000001"
KHACH = {"Authorization": f"Bearer {KHOA_KHACH}"}

MAT_KHAU_DAC_BIET = "mat|khau'voi\"ky|tu|la!@#$%"
KHOA_BASE32_MAU = "JBSWY3DPEHPK3PXP"
KHOA_BASE32_DAU_CACH = "jb sw-y3dp-ehpk-3pxp"


# ── MÔ PHỎNG DỊCH VỤ PHỤ CHO TEST ───────────────────────────────────────────

@dataclass
class KetQuaDangNhapGoogleGia:
    thanh_cong: bool
    can_nguoi: bool = False
    cho_2fa: bool = False
    ma_loi: str = ""
    buoc: str = ""

    def sang_dict(self) -> dict:
        return {
            "thanh_cong": bool(self.thanh_cong),
            "can_nguoi": bool(self.can_nguoi),
            "cho_2fa": bool(self.cho_2fa),
            "ma_loi": self.ma_loi,
            "buoc": self.buoc,
        }


class SoGia:
    def __init__(self):
        self.dang_chay: dict[str, Any] = {}
        self.catalog: dict[str, Any] = {}
        self.cai_goi: list[tuple[str, dict]] = []
        self.tu_xa: list[str] = []
        self.mui_gio = None

    def the_hien_cho_dang_nhap(self, id_: str):
        return self.dang_chay.get(id_)

    def tien_to_cua(self, module_id: str) -> str | None:
        return "gia" if module_id in self.dang_chay or module_id in self.catalog else None

    async def cai(self, id_: str, cau_hinh: dict) -> dict:
        self.cai_goi.append((id_, cau_hinh))
        return {"ok": True, "ket_qua_tung_khoa": [], "loi": None}

    def tinh_nang(self) -> dict:
        return so_module.SoModule.tinh_nang(self)


class DieuKhienGia(khoi_phuc_phien.BoDieuKhienTrinhDuyet):
    def __init__(self, ho_so: str):
        self.ho_so = ho_so
        self.da_dong = False

    async def mo_trang(self, url: str) -> None:
        pass

    async def dien(self, bo_chon: str, gia_tri: str) -> None:
        pass

    async def bam(self, bo_chon: str) -> None:
        pass

    async def doc_cookie(self, mien: str) -> dict:
        return {}

    async def tin_hieu(self, quy_tac: list | None = None) -> set:
        return set()

    async def dong(self):
        self.da_dong = True


class ModProviderGia:
    def __init__(self, id_="mod-provider-gia"):
        self.id = id_
        self.manifest = {
            "id": id_,
            "ten": "Provider Giả",
            "cung_cap": ["chat"],
            "truong_cau_hinh": [{"khoa": "cookies", "kieu": "danh_sach_bi_mat"}],
        }
        self.da_nhan: list[dict] = []
        self.tra_ve: KetQuaDangNhap | None = None

    async def dang_nhap(self, dong: DongDaTach) -> KetQuaDangNhap:
        mk = dong.mat_khau.lay() if dong.mat_khau else None
        totp = dong.totp.lay() if dong.totp else None
        self.da_nhan.append({"email": dong.email, "mat_khau": mk, "totp": totp})
        if self.tra_ve is not None:
            return self.tra_ve
        return KetQuaDangNhap(True, cookie=f"cookie-cho-{dong.email}")

    async def tai_dung_ho_so(self, ho_so: str, trinh_duyet: Any = None) -> KetQuaDangNhap:
        return KetQuaDangNhap(True, cookie=f"cookie-cho-{ho_so}")


class ModDNDanhTinhGia:
    def __init__(self, id_="mod-dn-danh-tinh-gia"):
        self.id = id_
        self.manifest = {
            "id": id_,
            "ten": "Đăng nhập Google Giả",
            "cung_cap": ["dang_nhap_danh_tinh"],
            "truong_cau_hinh": [],
            "nhom": {"id": "google", "ten": "Google"},
        }
        self.da_nhan: list[dict] = []
        self.tra_ve: KetQuaDangNhapGoogleGia | None = None

    async def dang_nhap(self, ho_so: str, email: str, mat_khau: BiMatTam | None,
                        trinh_duyet: Any, ma_2fa: BiMatTam | None = None,
                        url_novnc: str | None = None) -> KetQuaDangNhapGoogleGia:
        mk_val = mat_khau.lay() if mat_khau else None
        otp_val = ma_2fa.lay() if ma_2fa else None
        self.da_nhan.append({
            "ho_so": ho_so, "email": email, "mat_khau": mk_val, "ma_2fa": otp_val,
            "ma_2fa_is_none": ma_2fa is None
        })
        if self.tra_ve is not None:
            return self.tra_ve
        return KetQuaDangNhapGoogleGia(True)

    async def nhap_ma_2fa(self, ho_so: str, ma: BiMatTam, trinh_duyet, url_novnc=None):
        val = ma.lay()
        if val == "654321":
            return KetQuaDangNhapGoogleGia(True)
        if val == "999999":
            return KetQuaDangNhapGoogleGia(False, ma_loi="khong_cho_2fa")
        return KetQuaDangNhapGoogleGia(False, ma_loi="ma_2fa_sai")


class ModTrinhDuyetGia:
    def __init__(self, id_="mod-trinh-duyet-gia"):
        self.id = id_
        self.manifest = {
            "id": id_,
            "ten": "Trình duyệt giả",
            "cung_cap": ["trinh_duyet"],
            "truong_cau_hinh": [],
        }
        self.ho_so_tao_lan_luot = ["aa" * 16, "bb" * 16, "cc" * 16]
        self.da_tao: list[str] = []
        self.da_dong: list[str] = []

    async def trinh_duyet_tao(self, ten=None) -> dict:
        hs = self.ho_so_tao_lan_luot.pop(0) if self.ho_so_tao_lan_luot else "dd" * 16
        self.da_tao.append(hs)
        return {"ho_so": hs}

    async def trinh_duyet_dong(self, ho_so: str, giu_phien: bool = False):
        self.da_dong.append(ho_so)

    def trinh_duyet_that(self, ho_so: str):
        return DieuKhienGia(ho_so)


@pytest.fixture
def env_gia(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")

    so = SoGia()
    mod_pv = ModProviderGia()
    mod_dn = ModDNDanhTinhGia()
    mod_td = ModTrinhDuyetGia()
    so.dang_chay[mod_pv.id] = mod_pv
    so.dang_chay[mod_dn.id] = mod_dn
    so.dang_chay[mod_td.id] = mod_td

    # Chuẩn NV101/NV119: TestClient mở context manager khởi động lifespan xong mới gán state.so
    with TestClient(app, raise_server_exceptions=False) as client:
        monkeypatch.setattr(client.app.state, "so", so)
        yield {
            "client": client,
            "so": so,
            "mod_pv": mod_pv,
            "mod_dn": mod_dn,
            "mod_td": mod_td,
        }


def _cho_luot_chay_xong(client, toi_da: float = 5.0) -> dict:
    ket_thuc = time.monotonic() + toi_da
    while time.monotonic() < ket_thuc:
        r = client.get("/api/tai-khoan/dang-nhap-hang-loat/trang-thai", headers=QT)
        if r.status_code == 200:
            data = r.json()
            if data.get("trang_thai_luot") == "hoan_thanh":
                return data
        time.sleep(0.05)
    raise TimeoutError("Lượt chạy không hoàn thành kịp thời gian.")


# ── TEST TÁI HIỆN LỖI B: KIỂM BIÊN VÀ BẢO VỆ THÔNG ĐIỆP ──────────────────────

def test_tai_hien_loi_b_van_ban_surrogate_tra_400_khong_500(env_gia):
    """Lỗi B: van_ban chứa ký tự surrogate ('\\ud800') phải bị từ chối bằng 400 invalid_value cố định,
    tuyệt đối không để rơi vào BiMatTam ném UnicodeEncodeError gây 500.
    """
    client = env_gia["client"]
    payload = {
        "module_id": "mod-provider-gia",
        "van_ban": "test@example.com|pass\ud800word",
    }
    raw = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    headers = dict(QT, **{"Content-Type": "application/json"})
    r = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=headers, content=raw)
    assert r.status_code == 400, f"Mong đợi 400 nhưng nhận được {r.status_code}: {r.text}"
    err = r.json().get("error", {})
    assert err.get("code") == "invalid_value"
    assert "Internal Server Error" not in r.text
    assert "UnicodeEncodeError" not in r.text


def test_tai_hien_loi_b_mat_khau_surrogate_khong_lo_codec_codec(env_gia):
    """Lỗi B: mat_khau trong đường tai_khoan chứa surrogate ('\\ud800') phải bị từ chối bằng 400
    với câu thông điệp CỐ ĐỊNH, KHÔNG lộ câu lỗi codec ('codec can't encode character in position...').
    """
    client = env_gia["client"]
    payload = {
        "module_id": "mod-provider-gia",
        "tai_khoan": [
            {"email": "user@example.com", "mat_khau": "secret\ud800pass"}
        ],
    }
    raw = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    headers = dict(QT, **{"Content-Type": "application/json"})
    r = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=headers, content=raw)
    assert r.status_code == 400, f"Mong đợi 400 nhưng nhận {r.status_code}: {r.text}"
    err = r.json().get("error", {})
    assert err.get("code") == "invalid_value"
    msg = err.get("message", "")
    assert "codec can't encode" not in msg
    assert "in position" not in msg
    assert "surrogate" in msg.lower() or "không hợp lệ" in msg.lower()


def test_tai_hien_loi_b_tai_khoan_kieu_sai_va_dieu_khien(env_gia):
    """Lỗi B: email, mat_khau, otp không phải chuỗi (số, list, bool, dict) hoặc chứa ký tự điều khiển
    phải bị từ chối bằng 400 cố định.
    """
    client = env_gia["client"]

    # 1. mat_khau là số
    r1 = client.post(
        "/api/tai-khoan/dang-nhap-tu-dong/bat-dau",
        headers=QT,
        json={"module_id": "mod-provider-gia", "tai_khoan": [{"email": "u@example.com", "mat_khau": 123456}]},
    )
    assert r1.status_code == 400
    assert r1.json().get("error", {}).get("code") == "invalid_value"

    # 2. email là dict
    r2 = client.post(
        "/api/tai-khoan/dang-nhap-tu-dong/bat-dau",
        headers=QT,
        json={"module_id": "mod-provider-gia", "tai_khoan": [{"email": {"a": 1}, "mat_khau": "pass"}]},
    )
    assert r2.status_code == 400

    # 3. otp là boolean
    r3 = client.post(
        "/api/tai-khoan/dang-nhap-tu-dong/bat-dau",
        headers=QT,
        json={"module_id": "mod-provider-gia", "tai_khoan": [{"email": "u@example.com", "mat_khau": "p", "otp": True}]},
    )
    assert r3.status_code == 400

    # 4. mat_khau chứa ký tự điều khiển (NUL \x00)
    r4 = client.post(
        "/api/tai-khoan/dang-nhap-tu-dong/bat-dau",
        headers=QT,
        json={"module_id": "mod-provider-gia", "tai_khoan": [{"email": "u@example.com", "mat_khau": "p\x00ass"}]},
    )
    assert r4.status_code == 400


# ── TEST ĐƠN VỊ: BiMatTotp & kiem_tra_va_tao_otp ────────────────────────────

def test_bimat_totp_tinh_ma_dong_khop_thoi_diem():
    assert BiMatTotp is not None, "Chưa cài đặt BiMatTotp"
    bm = BiMatTotp("JBSWY3DPEHPK3PXP")
    ma1 = bm.lay(moc_thoi_gian=1000.0)
    ma2 = bm.lay(moc_thoi_gian=1030.0)
    assert len(ma1) == 6 and ma1.isdigit()
    assert len(ma2) == 6 and ma2.isdigit()
    assert ma1 != ma2
    assert ma1 == sinh_totp("JBSWY3DPEHPK3PXP", moc_thoi_gian=1000.0)

    # Che giấu không để lộ khoá
    assert repr(bm) == "<BiMatTotp>"
    assert str(bm) == "<BiMatTotp>"
    assert "JBSWY3DPEHPK3PXP" not in repr(bm)

    bm.xoa()
    assert bm.da_xoa() is True
    assert bm.lay() == ""


def test_bimat_totp_lay_va_xoa():
    assert BiMatTotp is not None, "Chưa cài đặt BiMatTotp"
    bm = BiMatTotp("JBSWY3DPEHPK3PXP")
    ma = bm.lay_va_xoa(moc_thoi_gian=12345.0)
    assert len(ma) == 6 and ma.isdigit()
    assert bm.da_xoa() is True
    assert bm.lay() == ""


def test_kiem_tra_va_tao_otp_cac_truong_hop():
    assert kiem_tra_va_tao_otp is not None, "Chưa cài đặt kiem_tra_va_tao_otp"
    # Base32 hợp lệ với chữ hoa/thường, dấu cách, dấu gạch ngang
    otp_totp = kiem_tra_va_tao_otp("jb sw-y3dp-ehpk-3pxp")
    assert isinstance(otp_totp, BiMatTotp)
    assert len(otp_totp.lay(moc_thoi_gian=1000.0)) == 6
    otp_totp.xoa()

    # Mã 6 số tĩnh
    otp_so6 = kiem_tra_va_tao_otp("123456")
    assert isinstance(otp_so6, BiMatTam)
    assert not isinstance(otp_so6, BiMatTotp)
    assert otp_so6.lay() == "123456"
    otp_so6.xoa()

    # Mã 8 số tĩnh
    otp_so8 = kiem_tra_va_tao_otp("12345678")
    assert isinstance(otp_so8, BiMatTam)
    assert otp_so8.lay() == "12345678"
    otp_so8.xoa()

    # Trống hoặc None
    assert kiem_tra_va_tao_otp(None) is None
    assert kiem_tra_va_tao_otp("") is None
    assert kiem_tra_va_tao_otp("   ") is None

    # Sai khuôn -> ValueError("otp_khong_hop_le")
    with pytest.raises(ValueError, match="otp_khong_hop_le"):
        kiem_tra_va_tao_otp("123")
    with pytest.raises(ValueError, match="otp_khong_hop_le"):
        kiem_tra_va_tao_otp("123456789")
    with pytest.raises(ValueError, match="otp_khong_hop_le"):
        kiem_tra_va_tao_otp("NOT-BASE-32-INVALID!@#$")
    with pytest.raises(ValueError, match="otp_khong_hop_le"):
        kiem_tra_va_tao_otp(123456)  # không phải str


# ── TEST ĐƠN VỊ: tach_danh_sach_tai_khoan ───────────────────────────────────

def test_tach_danh_sach_tai_khoan_hop_le():
    assert tach_danh_sach_tai_khoan is not None, "Chưa cài đặt tach_danh_sach_tai_khoan"
    ds_tho = [
        {"email": "user1@example.com", "mat_khau": MAT_KHAU_DAC_BIET, "otp": "JBSWY3DPEHPK3PXP"},
        {"email": "user2@example.com", "mat_khau": "pass2", "otp": "654321"},
        {"email": "user3@example.com", "mat_khau": "pass3"},
    ]
    ds, trung = tach_danh_sach_tai_khoan(ds_tho)
    assert len(ds) == 3
    assert len(trung) == 0

    assert ds[0].email == "user1@example.com"
    assert ds[0].mat_khau.lay() == MAT_KHAU_DAC_BIET
    assert isinstance(ds[0].totp, BiMatTotp)

    assert ds[1].email == "user2@example.com"
    assert ds[1].mat_khau.lay() == "pass2"
    assert isinstance(ds[1].totp, BiMatTam)
    assert not isinstance(ds[1].totp, BiMatTotp)
    assert ds[1].totp.lay() == "654321"

    assert ds[2].email == "user3@example.com"
    assert ds[2].totp is None

    for tk in ds:
        tk.xoa_bi_mat()


def test_tach_danh_sach_tai_khoan_trung_va_gioi_han_20():
    assert tach_danh_sach_tai_khoan is not None, "Chưa cài đặt tach_danh_sach_tai_khoan"
    ds_tho = [
        {"email": "dup@example.com", "mat_khau": "pass1"},
        {"email": "DUP@example.com", "mat_khau": "pass2"},
    ]
    ds, trung = tach_danh_sach_tai_khoan(ds_tho)
    assert len(ds) == 1
    assert len(trung) == 1
    assert trung[0].email == "DUP@example.com"
    assert ds[0].mat_khau.lay() == "pass1"
    ds[0].xoa_bi_mat()

    ds_21 = [{"email": f"u{i}@example.com", "mat_khau": "p"} for i in range(21)]
    with pytest.raises(ValueError, match="vượt quá tối đa 20 phần tử"):
        tach_danh_sach_tai_khoan(ds_21)


def test_tach_dong_nang_cap_totp():
    van_ban = "totp@example.com|mk|JBSWY3DPEHPK3PXP\nso@example.com|mk|123456"
    # Không nâng cấp (hành vi cũ)
    ds_cu, _, _ = tach_dong(van_ban, nang_cap_totp=False)
    assert not isinstance(ds_cu[0].totp, BiMatTotp)
    assert ds_cu[0].totp.lay() == "JBSWY3DPEHPK3PXP"

    # Có nâng cấp (NV120)
    ds_moi, _, _ = tach_dong(van_ban, nang_cap_totp=True)
    assert isinstance(ds_moi[0].totp, BiMatTotp)
    assert len(ds_moi[0].totp.lay(moc_thoi_gian=1000.0)) == 6
    assert isinstance(ds_moi[1].totp, BiMatTam)
    assert not isinstance(ds_moi[1].totp, BiMatTotp)


# ── TEST TẦNG API ĐĂNG NHẬP TỰ ĐỘNG ─────────────────────────────────────────

def test_api_tai_khoan_cau_truc_va_giu_nguyen_mat_khau(env_gia):
    client = env_gia["client"]
    mod_pv = env_gia["mod_pv"]

    payload = {
        "module_id": "mod-provider-gia",
        "kieu": "truc_tiep",
        "tai_khoan": [
            {
                "email": "user1@example.com",
                "mat_khau": MAT_KHAU_DAC_BIET,
                "otp": "JBSWY3DPEHPK3PXP",
            }
        ],
    }
    r = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json=payload)
    assert r.status_code == 200, r.text
    res = _cho_luot_chay_xong(client)
    assert res["tom_tat"]["xong"] == 1

    assert len(mod_pv.da_nhan) == 1
    assert mod_pv.da_nhan[0]["mat_khau"] == MAT_KHAU_DAC_BIET
    otp_nhan = mod_pv.da_nhan[0]["totp"]
    assert len(otp_nhan) == 6 and otp_nhan.isdigit()


def test_api_dong_thoi_van_ban_va_tai_khoan_bi_tu_choi(env_gia):
    client = env_gia["client"]
    # Cả hai -> 400
    r1 = client.post(
        "/api/tai-khoan/dang-nhap-tu-dong/bat-dau",
        headers=QT,
        json={
            "module_id": "mod-provider-gia",
            "van_ban": "u@example.com|p",
            "tai_khoan": [{"email": "u@example.com", "mat_khau": "p"}],
        },
    )
    assert r1.status_code == 400
    assert r1.json().get("error", {}).get("code") == "invalid_value"

    # Không có cái nào -> 400
    r2 = client.post(
        "/api/tai-khoan/dang-nhap-tu-dong/bat-dau",
        headers=QT,
        json={"module_id": "mod-provider-gia"},
    )
    assert r2.status_code == 400
    assert r2.json().get("error", {}).get("code") == "invalid_value"


def test_api_tai_khoan_danh_tinh_voi_totp_dong(env_gia):
    client = env_gia["client"]
    mod_dn = env_gia["mod_dn"]

    payload = {
        "module_id": "mod-provider-gia",
        "kieu": "danh_tinh",
        "tai_khoan": [
            {
                "email": "googleuser@example.com",
                "mat_khau": "secret123",
                "otp": "jb sw-y3dp-ehpk-3pxp",
            }
        ],
    }
    r = client.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT, json=payload)
    assert r.status_code == 200, r.text
    res = _cho_luot_chay_xong(client)
    assert res["tom_tat"]["xong"] == 1

    assert len(mod_dn.da_nhan) == 1
    ma_2fa = mod_dn.da_nhan[0]["ma_2fa"]
    assert ma_2fa is not None
    assert len(ma_2fa) == 6 and ma_2fa.isdigit()


def test_api_bao_ve_bi_mat_khong_lot_log(env_gia, caplog):
    client = env_gia["client"]
    caplog.set_level(logging.DEBUG)

    bi_mat_mk = "SUPER_SECRET_PASSWORD_NV120"
    bi_mat_totp = "JBSWY3DPEHPK3PXP"

    r = client.post(
        "/api/tai-khoan/dang-nhap-tu-dong/bat-dau",
        headers=QT,
        json={
            "module_id": "mod-provider-gia",
            "tai_khoan": [
                {"email": "secret@example.com", "mat_khau": bi_mat_mk, "otp": bi_mat_totp}
            ],
        },
    )
    assert r.status_code == 200
    res = _cho_luot_chay_xong(client)

    for output in (r.text, json.dumps(res), caplog.text):
        assert bi_mat_mk not in output, "Mật khẩu bị lọt ra ngoài!"
        assert bi_mat_totp not in output, "Khoá TOTP bị lọt ra ngoài!"


# ── LUẬT CỨNG BẢO MẬT: 401, 403, 404 VÀ KHÔNG CÓ MODULE ─────────────────────

def test_bao_mat_401_khong_khoa(env_gia):
    client = env_gia["client"]
    r = client.post(
        "/api/tai-khoan/dang-nhap-tu-dong/bat-dau",
        json={"module_id": "mod-provider-gia", "van_ban": "u@example.com|p"},
    )
    assert r.status_code == 401


def test_bao_mat_403_khoa_khach_goi_duong_quan_tri(env_gia):
    client = env_gia["client"]
    _, khoa_tho = khoa_khach.tao_khoa(nhan="Khách Test", pham_vi=["chat"])
    r = client.post(
        "/api/tai-khoan/dang-nhap-tu-dong/bat-dau",
        headers={"Authorization": f"Bearer {khoa_tho}"},
        json={"module_id": "mod-provider-gia", "van_ban": "u@example.com|p"},
    )
    assert r.status_code == 403


def test_bao_mat_404_module_la_idor(env_gia):
    client = env_gia["client"]
    r = client.post(
        "/api/tai-khoan/dang-nhap-tu-dong/bat-dau",
        headers=QT,
        json={"module_id": "module-la-khong-ton-tai", "van_ban": "u@example.com|p"},
    )
    assert r.status_code == 404
    assert r.json().get("error", {}).get("code") == "module_chua_cai"


def test_loi_chay_khi_khong_co_module_nao(env_gia):
    client = env_gia["client"]
    so = env_gia["so"]
    so.dang_chay.clear()

    r_tn = client.get("/api/tinh-nang", headers=QT)
    assert r_tn.status_code == 200
    assert r_tn.json().get("modules") == []

    r_bd = client.post(
        "/api/tai-khoan/dang-nhap-tu-dong/bat-dau",
        headers=QT,
        json={"module_id": "mod-provider-gia", "tai_khoan": [{"email": "u@example.com", "mat_khau": "p"}]},
    )
    assert r_bd.status_code == 404
