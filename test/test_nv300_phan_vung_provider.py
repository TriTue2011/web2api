"""NV300 — kho tài khoản phân vùng THEO PROVIDER (module).

Ràng buộc chủ máy 03/11: «mỗi provider tách biệt nhau». Cùng một Gmail ở
gemini-web và grok-web (và claude/chatgpt) là các bản ghi ĐỘC LẬP: thêm,
sửa, xoá, ghi kết quả đăng nhập ở provider này KHÔNG chạm provider kia; dữ
liệu cũ (id theo email) vẫn đọc được, không mất bản ghi; vòng lấy lại phiên
nền chạy riêng từng provider.

Mọi email, mật khẩu trong test là chuỗi GIẢ. Không Internet, không Chrome.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app import kho, kho_tai_khoan as ktk
from app import khoi_phuc_phien as kp
from app import tu_lam_moi as tlm

EMAIL = "gmail-chung-gia-nv300@example.com"
MK_GEMINI = "mat-khau-gia-gemini-nv300"
GEMINI, GROK, CLAUDE, CHATGPT = "gemini-web", "grok-web", "claude-web", "chatgpt-web"
NHOM = {GEMINI: "google", GROK: "grok", CLAUDE: "claude", CHATGPT: "chatgpt",
        "antigravity": "google"}   # provider = NHÓM của module (như manifest)


@pytest.fixture(autouse=True)
def _kho_rieng(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))


def _them_gemini():
    return ktk.luu_tai_khoan(EMAIL, MK_GEMINI, None, "Bản Gemini",
                             module_id=GEMINI, nhom="google",
                             cach_dang_nhap="email")


def _them_grok_google(id_ho_so):
    return ktk.luu_tai_khoan(EMAIL, None, None, "Bản Grok",
                             module_id=GROK, nhom="grok",
                             cach_dang_nhap="google", ho_so=id_ho_so)


def _theo_module(module_id):
    return [b for b in ktk.tat_ca_ban_ghi()
            if b.get("email") == EMAIL and b.get("module_id") == module_id]


def test_cung_gmail_hai_provider_la_hai_ban_ghi_doc_lap():
    gem = _them_gemini()
    grok = _them_grok_google(gem["id"])
    assert grok["id"] != gem["id"]
    assert len(ktk.tat_ca_ban_ghi()) == 2
    b_gem = ktk.tim_theo_id(gem["id"])
    assert b_gem["module_id"] == GEMINI
    assert b_gem["nhan"] == "Bản Gemini"
    assert b_gem["cach_dang_nhap"] == "email"
    assert b_gem["mat_khau"] == MK_GEMINI          # không bị đè mất bí mật
    b_grok = ktk.tim_theo_id(grok["id"])
    assert b_grok["module_id"] == GROK
    assert b_grok["cach_dang_nhap"] == "google"
    assert b_grok["ho_so_google"] == gem["id"]


def test_them_lai_cung_provider_la_cap_nhat_khong_nhan_doi():
    gem = _them_gemini()
    _them_grok_google(gem["id"])
    gem2 = ktk.luu_tai_khoan(EMAIL, None, None, "Gemini đổi nhãn",
                             module_id=GEMINI, nhom="google")
    assert gem2["id"] == gem["id"]
    assert len(ktk.tat_ca_ban_ghi()) == 2
    assert ktk.tim_theo_id(gem["id"])["nhan"] == "Gemini đổi nhãn"
    assert ktk.tim_theo_id(gem["id"])["mat_khau"] == MK_GEMINI


def test_sua_xoa_tam_dung_mot_ben_khong_cham_ben_kia():
    gem = _them_gemini()
    grok = _them_grok_google(gem["id"])
    ktk.luu_tai_khoan(None, None, None, "Grok đổi nhãn", id=grok["id"])
    assert ktk.tim_theo_id(gem["id"])["nhan"] == "Bản Gemini"
    ktk.dat_tam_dung(grok["id"], True)
    assert not ktk.da_tam_dung(gem["id"])
    assert ktk.xoa_tai_khoan(grok["id"]) is True
    con = ktk.tim_theo_id(gem["id"])
    assert con is not None and con["mat_khau"] == MK_GEMINI
    # Xoá bản Gemini sau cùng cũng không ảnh hưởng ngược lại.
    grok2 = _them_grok_google(gem["id"])
    assert ktk.xoa_tai_khoan(gem["id"]) is True
    assert ktk.tim_theo_id(grok2["id"]) is not None


def test_tim_theo_email_va_ket_qua_dang_nhap_theo_provider():
    gem = _them_gemini()
    grok = _them_grok_google(gem["id"])
    assert ktk.tim_theo_email(EMAIL, module_id=GEMINI)["id"] == gem["id"]
    assert ktk.tim_theo_email(EMAIL, module_id=GROK)["id"] == grok["id"]
    # Ghi kết quả đăng nhập của Grok không tô chấm bản Gemini.
    ktk.ghi_ket_qua_theo_email(
        EMAIL, {"thanh_cong": False, "ma_loi": "sai_mat_khau"},
        module_id=GROK)
    assert ktk.lay_ket_qua_dang_nhap(gem["id"]) is None
    assert ktk.lay_ket_qua_dang_nhap(grok["id"]) is not None
    # Email có ở hai provider mà không nói provider → không đoán.
    assert ktk.ghi_ket_qua_theo_email(
        EMAIL, {"thanh_cong": True}) is None


def test_luu_khong_noi_provider_khi_co_hai_ban_ghi_thi_tu_choi():
    gem = _them_gemini()
    _them_grok_google(gem["id"])
    with pytest.raises(ktk.LoiKhoTaiKhoan):
        ktk.luu_tai_khoan(EMAIL, "mat-khau-moi-gia", None, "mơ hồ")
    assert ktk.tim_theo_id(gem["id"])["mat_khau"] == MK_GEMINI


def test_doi_module_vao_phan_vung_da_co_bi_tu_choi():
    gem = _them_gemini()
    grok = _them_grok_google(gem["id"])
    with pytest.raises(ktk.LoiKhoTaiKhoan):
        ktk.luu_tai_khoan(None, None, None, "", id=grok["id"],
                          module_id=GEMINI, nhom="google")
    with pytest.raises(ktk.LoiKhoTaiKhoan):
        ktk.dat_module(grok["id"], GEMINI, "google")
    assert ktk.tim_theo_id(grok["id"])["module_id"] == GROK


def test_bon_provider_cung_mot_gmail():
    ids = set()
    for mid in (GEMINI, GROK, CLAUDE, CHATGPT):
        bk = ktk.luu_tai_khoan(EMAIL, "mk-gia-" + mid, None, mid,
                               module_id=mid, nhom=NHOM[mid])
        ids.add(bk["id"])
    assert len(ids) == 4
    for mid in (GEMINI, GROK, CLAUDE, CHATGPT):
        (b,) = _theo_module(mid)
        assert b["mat_khau"] == "mk-gia-" + mid


def test_di_tru_du_lieu_cu_30_chatgpt_7_google_khong_mat_ban_ghi():
    """Kho cũ: id = hash(email) — không trường phân vùng. Phải đọc được hết."""
    du_lieu = {}

    def cu(email, mid):
        id_ = ktk.ma_tai_khoan(email)
        du_lieu[id_] = {
            "id": id_, "email": email, "nhan": "", "mat_khau": "mk-gia",
            "totp_secret": "", "trang_thai": "", "module_id": mid,
            "nhom": NHOM[mid], "cap_nhat": int(time.time()),
        }
        return id_

    for i in range(30):
        cu(f"chatgpt{i}-gia-nv300@example.com", CHATGPT)
    id_google = [cu(f"google{i}-gia-nv300@example.com", GEMINI)
                 for i in range(7)]
    kho.ghi_bi_mat(ktk.TEN_KHO, du_lieu)
    assert len(ktk.liet_ke()) == 37
    # Thêm Grok cho một Gmail cũ: bản cũ nguyên vẹn, kho thành 38.
    email_gg = "google3-gia-nv300@example.com"
    grok = ktk.luu_tai_khoan(email_gg, None, None, "", module_id=GROK,
                             nhom="grok", cach_dang_nhap="google",
                             ho_so=id_google[3])
    assert grok["id"] != id_google[3]
    assert len(ktk.liet_ke()) == 38
    assert ktk.tim_theo_id(id_google[3])["module_id"] == GEMINI
    # Cập nhật trong CÙNG provider với bản cũ → vẫn là bản cũ, không nhân đôi.
    ktk.luu_tai_khoan(email_gg, None, None, "nhãn mới", module_id=GEMINI,
                      nhom="google")
    assert len(ktk.liet_ke()) == 38
    assert ktk.tim_theo_id(id_google[3])["nhan"] == "nhãn mới"
    # Gọi cũ không nêu provider, email chỉ có MỘT bản → vẫn cập nhật bản đó.
    ktk.luu_tai_khoan("chatgpt5-gia-nv300@example.com", None, None, "n5")
    assert len(ktk.liet_ke()) == 38
    assert ktk.tim_theo_email("chatgpt5-gia-nv300@example.com")["nhan"] == "n5"


# ── Vòng lấy lại phiên nền: mỗi provider một vòng riêng ──────────────────

class _ModGia:
    def __init__(self, khoa, hong=False):
        self._khoa, self._hong, self.so_lan = khoa, hong, 0

    def khoa_tai_khoan(self):
        return [self._khoa]

    async def lam_moi_phien(self, khoa):
        self.so_lan += 1
        if self._hong:
            raise RuntimeError("lỗi giả của provider này")
        return kp.KetQuaKhoiPhuc(True, tang="a")


@pytest.fixture
def _sach(monkeypatch, tmp_path):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data2"))
    monkeypatch.setenv("WEB2API_TU_LAM_MOI_GIAN_CACH_TAI_KHOAN_GIAY", "0")
    monkeypatch.setenv("WEB2API_TU_LAM_MOI_KHOANG_GIAY", "1")


def test_hai_vong_nen_doc_lap_loi_va_dung_mot_ben_khong_anh_huong(_sach):
    khoa = "khoa-chung-gia-nv300"            # cùng khoá phiên ở hai provider
    gem, grok = _ModGia(khoa, hong=True), _ModGia(khoa)
    v_gem = tlm.VongTuLamMoi(gem, GEMINI)
    v_grok = tlm.VongTuLamMoi(grok, GROK)

    async def _chay():
        v_gem.bat_dau()
        v_grok.bat_dau()
        await asyncio.sleep(0.05)
        # Gemini lỗi, Grok vẫn làm mới; dừng Gemini không dừng Grok.
        v_gem.dung()
        await asyncio.sleep(0.01)
        assert v_gem.da_dung and not v_grok.da_dung
        # «Cần người» của Gemini không chặn lượt làm mới của Grok.
        kp.dat_can_nguoi(GEMINI, khoa, "x", "hd")
        assert not kp.lay_trang_thai(GROK, khoa).get("can_nguoi")
        v_grok.dung()

    asyncio.run(_chay())
    assert gem.so_lan >= 1 and grok.so_lan >= 1


def test_cung_nha_cung_cap_van_chung_mot_ban_ghi_mo_hinh_nv224():
    """Provider = NHÓM: hai module cùng nhóm Google (gemini-web, antigravity) vẫn
    là MỘT bản ghi với «dung_cho» tích luỹ (quyết định NV224 của chủ máy)."""
    a = ktk.luu_tai_khoan(EMAIL, MK_GEMINI, None, "", module_id=GEMINI, nhom="google")
    b = ktk.luu_tai_khoan(EMAIL, None, None, "", module_id="antigravity", nhom="google")
    assert a["id"] == b["id"]
    assert len([x for x in ktk.tat_ca_ban_ghi() if x["email"] == EMAIL]) == 1
