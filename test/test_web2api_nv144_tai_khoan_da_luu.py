"""Kiểm thử NV144 — kho tài khoản đã lưu (lưu mật khẩu + TOTP, đăng nhập lại).

Hợp đồng (từ /home/hatch/workspace/drop-inbox/viec-nv144.txt):
1. Kho: lưu email, nhãn, MẬT KHẨU, TOTP SECRET (tuỳ chọn); tệp 0600/thư mục 0700;
   mã hoá khi nghỉ tự động qua app.ham_khoa khi có WEB2API_KHOA_CHINH (không thêm gói).
2. API (đều cần khoá quản trị): liệt kê (email che, KHÔNG bao giờ trả bí mật),
   lưu/cập nhật, xoá, đăng nhập bằng tài khoản đã lưu (server tự lấy bí mật).
   Quy tắc: không khoá→401, khoá khách/vai thấp→403, id lạ→404; kiểm biên chặt.
3. Giao diện theo ô (test Chrome riêng, skipped khi không có Chrome).
4. Tự động hoàn toàn: công tắc «Tự đăng nhập lại khi phiên hết hạn» (mặc định BẬT).
5. Bảo mật: XSS, bí mật không vào log/phản hồi, IDOR, hmac.compare_digest,
   không tên nhà cung cấp trong app//web/, kho hỏng → đóng cửa.

Chạy với module provider giả (không mạng ngoài).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import textwrap

import pytest
from fastapi.testclient import TestClient

from app import kho as kho_mod
from app import kho_tai_khoan as ktk
from app import so_module, tai_khoan_hang_loat
from app.main import app

KHOA_QT = "<redacted>"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
MODULE_ID = "provider-gia-nv144"

MANIFEST_PROVIDER = {
    "id": MODULE_ID, "ten": "Provider giả NV144", "mo_ta": "Module provider giả cho test kho tài khoản đã lưu",
    "loai": "ai", "hang": "A", "giao_thuc": "gia", "tien_to": "providergianv144",
    "cung_cap": ["chat"], "tai_nguyen": {"mang_ra": []},
    "tab_web": {"nhan": "Giả", "trang": "web/index.html"},
    "truong_cau_hinh": [],
}

# Module giả: `dang_nhap` nhận DongDaTach, đọc bí mật qua BiMatTam, ghi lại giá trị
# đã nhận để test đối chiếu; kịch bản {email: (thanh_cong, ma_loi)}.
MA_PROVIDER = textwrap.dedent('''
    from app.tai_khoan_hang_loat import BiMatTam, KetQuaDangNhap

    class Module:
        def __init__(self, manifest):
            self.manifest = manifest
            self.kich_ban = {}
            self.da_nhan = {}
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
        async def dang_nhap(self, tai_khoan):
            mk = tai_khoan.mat_khau.lay_va_xoa() if tai_khoan.mat_khau else None
            tp = tai_khoan.totp
            tp_gia_tri = None
            if tp is not None:
                from app.tai_khoan_hang_loat import BiMatTotp
                tp_gia_tri = tp.lay_va_xoa() if isinstance(tp, BiMatTotp) else tp.lay_va_xoa()
                self.da_nhan[tai_khoan.email] = (mk, "totp")
            else:
                self.da_nhan[tai_khoan.email] = (mk, None)
            thanh_cong, ma_loi = self.kich_ban.get(tai_khoan.email, (True, ""))
            return KetQuaDangNhap(thanh_cong, ma_loi=ma_loi)
''')

MAT_KHAU = "<redacted>"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"
EMAIL = "nguoi@example.com"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data = tmp_path / "data"
    thu_muc = data / "modules" / MODULE_ID
    thu_muc.mkdir(parents=True)
    (thu_muc / "manifest.json").write_text(json.dumps(MANIFEST_PROVIDER, ensure_ascii=False), encoding="utf-8")
    (thu_muc / "module.py").write_text(MA_PROVIDER, encoding="utf-8")
    (thu_muc / "web").mkdir()
    (thu_muc / "web" / "index.html").write_text("<!doctype html><title>gia</title>", encoding="utf-8")
    monkeypatch.setenv("WEB2API_DATA", str(data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    (data / "modules.json").write_text(json.dumps({"modules": [
        {"id": MODULE_ID, "cai_luc": "2026-10-19T00:00:00", "cau_hinh_cong_khai": {}},
    ]}, ensure_ascii=False), encoding="utf-8")
    with TestClient(app) as client:
        assert MODULE_ID in client.app.state.so.dang_chay
        yield client, data


def _module_gia(client):
    return client.app.state.so.dang_chay[MODULE_ID]


def _luu(client, email=EMAIL, mat_khau=MAT_KHAU, totp=TOTP_SECRET, nhan="chính"):
    return client.post("/api/tai-khoan/da-luu", headers=QT,
                       json={"email": email, "mat_khau": mat_khau,
                             "totp_secret": totp, "nhan": nhan})


# ── 1. Lưu → liệt kê không lộ bí mật ──────────────────────────────────────────

def test_luu_roi_liet_ke_khong_lo_bi_mat(moi_truong):
    client, data = moi_truong
    r = _luu(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tai_khoan"]["email_che"].startswith("…")
    assert body["tai_khoan"]["co_mat_khau"] is True
    assert body["tai_khoan"]["co_totp"] is True
    assert "mat_khau" not in body["tai_khoan"]
    assert "totp_secret" not in body["tai_khoan"]
    assert MAT_KHAU not in r.text and TOTP_SECRET not in r.text

    r = client.get("/api/tai-khoan/da-luu", headers=QT)
    assert r.status_code == 200
    ds = r.json()["tai_khoan"]
    assert len(ds) == 1
    assert MAT_KHAU not in r.text and TOTP_SECRET not in r.text
    # Tệp kho 0600, thư mục 0700
    p = data / "bi_mat" / "_tai_khoan_da_luu.json"
    assert p.is_file()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE((data / "bi_mat").stat().st_mode) == 0o700


def test_email_trung_thi_cap_nhat_khong_tao_moi(moi_truong):
    client, _ = moi_truong
    _luu(client)
    r = _luu(client, email="NGUOI@EXAMPLE.COM", mat_khau="MatKhauMoi_456", nhan="phụ")
    assert r.status_code == 200
    r = client.get("/api/tai-khoan/da-luu", headers=QT)
    ds = r.json()["tai_khoan"]
    assert len(ds) == 1
    assert ds[0]["nhan"] == "phụ"


def test_khong_totp_van_luu_duoc(moi_truong):
    client, _ = moi_truong
    r = _luu(client, totp=None)
    assert r.status_code == 200
    assert r.json()["tai_khoan"]["co_totp"] is False


def test_totp_secret_sai_khuon_400(moi_truong):
    client, _ = moi_truong
    r = _luu(client, totp="khong-phai-base32!!!")
    assert r.status_code == 400
    assert TOTP_SECRET not in r.text
    # Mã 6 số dùng một lần không được nhận làm «secret để lưu»
    r = _luu(client, totp="123456")
    assert r.status_code == 400


def test_email_sai_khuon_400(moi_truong):
    client, _ = moi_truong
    for xau in ["khong-phai-email", "a@b", "<script>@x.com", "a b@c.com"]:
        r = _luu(client, email=xau)
        assert r.status_code == 400, xau
        assert xau not in r.text or "<" not in xau


def test_ky_tu_dieu_khien_va_surrogate_400(moi_truong):
    client, _ = moi_truong
    r = _luu(client, mat_khau="ab\x00cd")
    assert r.status_code == 400
    # Surrogate đơn: httpx không encode được nên gửi thân thô với escape \ud800
    than = ('{"email": "%s", "mat_khau": "ok", "nhan": "x\\ud800y"}' % EMAIL).encode("ascii")
    r = client.post("/api/tai-khoan/da-luu", headers={**QT, "Content-Type": "application/json"},
                    content=than)
    assert r.status_code == 400
    assert "invalid_value" in r.text


def test_mat_khau_qua_dai_400(moi_truong):
    client, _ = moi_truong
    r = _luu(client, mat_khau="x" * 257)
    assert r.status_code == 400


def test_xoa_tai_khoan(moi_truong):
    client, _ = moi_truong
    tai_id = _luu(client).json()["tai_khoan"]["id"]
    r = client.delete(f"/api/tai-khoan/da-luu/{tai_id}", headers=QT)
    assert r.status_code == 200
    assert r.json()["da_xoa"] is True
    r = client.get("/api/tai-khoan/da-luu", headers=QT)
    assert r.json()["tai_khoan"] == []
    # Xoá lần nữa → 404
    r = client.delete(f"/api/tai-khoan/da-luu/{tai_id}", headers=QT)
    assert r.status_code == 404


# ── 2. Đăng nhập bằng tài khoản đã lưu ────────────────────────────────────────

def test_dang_nhap_bang_tai_khoan_da_luu(moi_truong):
    client, _ = moi_truong
    tai_id = _luu(client).json()["tai_khoan"]["id"]
    mod = _module_gia(client)
    r = client.post(f"/api/tai-khoan/da-luu/{tai_id}/dang-nhap", headers=QT,
                    json={"module_id": MODULE_ID})
    assert r.status_code == 200, r.text
    assert r.json()["thanh_cong"] is True
    # Module giả đã nhận đúng mật khẩu thô qua BiMatTam
    assert mod.da_nhan[EMAIL][0] == MAT_KHAU
    assert mod.da_nhan[EMAIL][1] == "totp"
    assert MAT_KHAU not in r.text


def test_dang_nhap_id_la_404(moi_truong):
    client, _ = moi_truong
    _luu(client)
    r = client.post("/api/tai-khoan/da-luu/tk-khongton-tai/dang-nhap", headers=QT,
                    json={"module_id": MODULE_ID})
    assert r.status_code == 404


def test_dang_nhap_module_khong_ho_tro_400(moi_truong):
    client, _ = moi_truong
    tai_id = _luu(client).json()["tai_khoan"]["id"]
    r = client.post(f"/api/tai-khoan/da-luu/{tai_id}/dang-nhap", headers=QT,
                    json={"module_id": "module-khong-ton-tai"})
    assert r.status_code == 404


# ── 3. 401 / 403 ─────────────────────────────────────────────────────────────

def test_khong_khoa_401(moi_truong):
    client, _ = moi_truong
    assert client.get("/api/tai-khoan/da-luu").status_code == 401
    assert client.post("/api/tai-khoan/da-luu", json={}).status_code == 401
    assert client.delete("/api/tai-khoan/da-luu/tk-x").status_code == 401
    assert client.get("/api/tai-khoan/tu-dang-nhap-lai").status_code == 401


def test_khoa_khach_403(moi_truong):
    client, data = moi_truong
    # Khoá khách thật (vai thấp) → 403 đúng hợp đồng
    from app import khoa_khach as kk_mod
    _muc, khoa_tho = kk_mod.tao_khoa(nhan="khach-nv144")
    hk = {"Authorization": f"Bearer {khoa_tho}"}
    assert client.get("/api/tai-khoan/da-luu", headers=hk).status_code == 403
    assert client.post("/api/tai-khoan/da-luu", headers=hk, json={}).status_code == 403
    assert client.delete("/api/tai-khoan/da-luu/tk-0000000000000000", headers=hk).status_code == 403


# ── 4. XSS ───────────────────────────────────────────────────────────────────

def test_nhan_chua_xss_duoc_escape(moi_truong):
    client, _ = moi_truong
    r = _luu(client, nhan='<script>alert(1)</script>')
    assert r.status_code == 200
    r = client.get("/api/tai-khoan/da-luu", headers=QT)
    assert "<script>" not in r.text
    assert "&lt;script&gt;" in r.text or "\\u003cscript\\u003e" in r.text


# ── 5. Bí mật không vào log ──────────────────────────────────────────────────

def test_bi_mat_khong_vao_log_va_tep_ngoai_kho(moi_truong, caplog):
    client, data = moi_truong
    with caplog.at_level(logging.DEBUG):
        _luu(client)
        tai_id = client.get("/api/tai-khoan/da-luu", headers=QT).json()["tai_khoan"][0]["id"]
        client.post(f"/api/tai-khoan/da-luu/{tai_id}/dang-nhap", headers=QT,
                    json={"module_id": MODULE_ID})
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert MAT_KHAU not in log_text
    assert TOTP_SECRET not in log_text
    # Quét mọi tệp dữ liệu (trừ chính tệp kho) không chứa mật khẩu thô
    for p in data.rglob("*"):
        if p.is_file() and p.name != "_tai_khoan_da_luu.json":
            try:
                noi_dung = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            assert MAT_KHAU not in noi_dung, f"mật khẩu lọt vào {p}"


# ── 6. Kho hỏng → đóng cửa ────────────────────────────────────────────────────

def test_kho_hong_dong_cua_khong_luu_do(moi_truong):
    client, data = moi_truong
    _luu(client)
    p = data / "bi_mat" / "_tai_khoan_da_luu.json"
    p.write_bytes(b"{json-hong")
    # Đọc kho hỏng → lỗi rõ, không coi như rỗng
    r = client.get("/api/tai-khoan/da-luu", headers=QT)
    assert r.status_code == 500
    # Ghi đè kho hỏng → từ chối, không lưu dở
    r = _luu(client, email="moi@example.com")
    assert r.status_code == 500


# ── 7. Công tắc tự đăng nhập lại ─────────────────────────────────────────────

def test_cong_tac_mac_dinh_bat(moi_truong):
    client, _ = moi_truong
    r = client.get("/api/tai-khoan/tu-dang-nhap-lai", headers=QT)
    assert r.status_code == 200
    assert r.json()["bat"] is True
    r = client.put("/api/tai-khoan/tu-dang-nhap-lai", headers=QT, json={"bat": False})
    assert r.status_code == 200 and r.json()["bat"] is False
    r = client.get("/api/tai-khoan/tu-dang-nhap-lai", headers=QT)
    assert r.json()["bat"] is False
    r = client.put("/api/tai-khoan/tu-dang-nhap-lai", headers=QT, json={"bat": "chuoi"})
    assert r.status_code == 400


def test_tu_dang_nhap_lai_thanh_cong(moi_truong):
    import asyncio
    client, _ = moi_truong
    _luu(client)
    mod = _module_gia(client)
    kq = asyncio.run(ktk.thu_dang_nhap_lai(module=mod, module_id=MODULE_ID, dinh_danh=EMAIL))
    assert kq["thu"] is True and kq["thanh_cong"] is True
    assert mod.da_nhan[EMAIL][0] == MAT_KHAU


def test_tu_dang_nhap_lai_dung_sau_2_hong_lien_tiep(moi_truong):
    import asyncio
    client, _ = moi_truong
    _luu(client)
    mod = _module_gia(client)
    mod.kich_ban[EMAIL] = (False, "sai_mat_khau")
    kq1 = asyncio.run(ktk.thu_dang_nhap_lai(module=mod, module_id=MODULE_ID, dinh_danh=EMAIL))
    assert kq1["thanh_cong"] is False
    kq2 = asyncio.run(ktk.thu_dang_nhap_lai(module=mod, module_id=MODULE_ID, dinh_danh=EMAIL))
    assert kq2["thanh_cong"] is False
    # Lần 3: đã hỏng 2 liên tiếp → không thử nữa
    so_lan_truoc = len(mod.da_nhan)
    kq3 = asyncio.run(ktk.thu_dang_nhap_lai(module=mod, module_id=MODULE_ID, dinh_danh=EMAIL))
    assert kq3["thu"] is False and kq3["ly_do"] == "hong_2_lien_tiep"
    assert len(mod.da_nhan) == so_lan_truoc


def test_tu_dang_nhap_lai_tat_thi_khong_thu(moi_truong):
    import asyncio
    client, _ = moi_truong
    _luu(client)
    client.put("/api/tai-khoan/tu-dang-nhap-lai", headers=QT, json={"bat": False})
    mod = _module_gia(client)
    kq = asyncio.run(ktk.thu_dang_nhap_lai(module=mod, module_id=MODULE_ID, dinh_danh=EMAIL))
    assert kq == {"thu": False, "ly_do": "da_tat"}
    assert EMAIL not in mod.da_nhan


def test_tu_dang_nhap_lai_khong_co_tai_khoan_thi_bo_qua(moi_truong):
    import asyncio
    client, _ = moi_truong
    mod = _module_gia(client)
    kq = asyncio.run(ktk.thu_dang_nhap_lai(module=mod, module_id=MODULE_ID, dinh_danh="la@example.com"))
    assert kq == {"thu": False, "ly_do": "khong_co_tai_khoan"}


# ── 8. Sao lưu không kéo kho bí mật khi không có mật khẩu ────────────────────

def test_sao_luu_khong_mat_khau_khong_chua_kho_tai_khoan(moi_truong):
    from app import sao_luu as sl_mod
    client, data = moi_truong
    _luu(client)
    # Bản sao lưu KHÔNG mật khẩu: thư mục bi_mat/ bị loại khỏi mặc định
    du_lieu, _ten = sl_mod.tao_ban_sao_luu()
    manifest, tep = sl_mod.kiem_va_giai_ma(du_lieu, None)
    assert manifest.get("co_bi_mat") is False
    assert not any("_tai_khoan_da_luu" in t for t in tep), list(tep)
    assert not any(str(t).startswith("bi_mat/") for t in tep), list(tep)


# ── 9. ID chống IDOR: so bằng compare_digest, không đoán được ────────────────

def test_id_khong_doan_duoc_tu_email(moi_truong):
    client, _ = moi_truong
    tai_id = _luu(client).json()["tai_khoan"]["id"]
    assert tai_id != EMAIL and EMAIL not in tai_id
    # id đúng khuôn tk- + 16 hex
    import re
    assert re.fullmatch(r"tk-[0-9a-f]{16}", tai_id)


# ── 10. Tự tái hiện lỗi theo báo cáo soát ────────────────────────────────────

def test_xoa_hoac_cap_nhat_tai_khoan_phai_xoa_trang_thai_hong_lien_tiep(moi_truong):
    """Lỗi 1: Cập nhật mật khẩu hoặc xoá rồi tạo lại phải dọn trạng thái hỏng liên tiếp."""
    import asyncio
    client, _ = moi_truong
    tai_id = _luu(client).json()["tai_khoan"]["id"]
    mod = _module_gia(client)
    mod.kich_ban[EMAIL] = (False, "sai_mat_khau")
    # Làm hỏng 2 lần liên tiếp
    asyncio.run(ktk.thu_dang_nhap_lai(module=mod, module_id=MODULE_ID, dinh_danh=EMAIL))
    asyncio.run(ktk.thu_dang_nhap_lai(module=mod, module_id=MODULE_ID, dinh_danh=EMAIL))
    # Lần 3 bị chặn do hỏng 2 liên tiếp
    kq = asyncio.run(ktk.thu_dang_nhap_lai(module=mod, module_id=MODULE_ID, dinh_danh=EMAIL))
    assert kq["thu"] is False and kq["ly_do"] == "hong_2_lien_tiep"

    # Nhánh A: Cập nhật mật khẩu mới -> phải được thử lại
    mod.kich_ban[EMAIL] = (True, "")
    _luu(client, email=EMAIL, mat_khau="MatKhauMoi_999")
    kq_sau_sua = asyncio.run(ktk.thu_dang_nhap_lai(module=mod, module_id=MODULE_ID, dinh_danh=EMAIL))
    assert kq_sau_sua["thu"] is True and kq_sau_sua["thanh_cong"] is True

    # Làm hỏng lại 2 lần để test Nhánh B (Xoá rồi thêm lại)
    mod.kich_ban[EMAIL] = (False, "sai_mat_khau")
    asyncio.run(ktk.thu_dang_nhap_lai(module=mod, module_id=MODULE_ID, dinh_danh=EMAIL))
    asyncio.run(ktk.thu_dang_nhap_lai(module=mod, module_id=MODULE_ID, dinh_danh=EMAIL))
    kq = asyncio.run(ktk.thu_dang_nhap_lai(module=mod, module_id=MODULE_ID, dinh_danh=EMAIL))
    assert kq["thu"] is False and kq["ly_do"] == "hong_2_lien_tiep"

    # Xoá tài khoản -> thêm lại
    r_xoa = client.delete(f"/api/tai-khoan/da-luu/{tai_id}", headers=QT)
    assert r_xoa.status_code == 200
    mod.kich_ban[EMAIL] = (True, "")
    _luu(client, email=EMAIL, mat_khau="MatKhauMoi_888")
    kq_sau_xoa = asyncio.run(ktk.thu_dang_nhap_lai(module=mod, module_id=MODULE_ID, dinh_danh=EMAIL))
    assert kq_sau_xoa["thu"] is True and kq_sau_xoa["thanh_cong"] is True


def test_ui_chon_tai_khoan_roi_sua_mat_khau_khong_bi_loi_email(moi_truong):
    """Lỗi 2: Chọn tài khoản từ dropdown gửi id + email che không bị 400 invalid_email."""
    client, _ = moi_truong
    r_tao = _luu(client)
    assert r_tao.status_code == 200
    tk = r_tao.json()["tai_khoan"]
    tai_id = tk["id"]
    email_che = tk["email_che"]

    # Form gửi email_che kèm id và mật khẩu mới
    r_sua = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "id": tai_id,
        "email": email_che,
        "mat_khau": "MatKhauMoi_DaSua",
    })
    assert r_sua.status_code == 200, r_sua.text
    assert r_sua.json()["tai_khoan"]["id"] == tai_id
    ban_ghi = ktk.tim_theo_id(tai_id)
    assert ban_ghi is not None
    assert ban_ghi["mat_khau"] == "MatKhauMoi_DaSua"
    assert ban_ghi["email"] == EMAIL


def test_cap_nhat_khong_truyen_totp_phai_giu_totp_cu(moi_truong):
    """Lỗi 3: Cập nhật nhãn hoặc mật khẩu mà không truyền totp_secret phải giữ TOTP cũ."""
    client, _ = moi_truong
    _luu(client, totp=TOTP_SECRET, nhan="gốc")
    ban_ghi = ktk.tim_theo_email(EMAIL)
    assert ban_ghi["totp_secret"] == TOTP_SECRET

    # Cập nhật không truyền trường totp_secret
    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL,
        "mat_khau": "MatKhauMoi_123",
        "nhan": "nhãn mới",
    })
    assert r.status_code == 200
    assert r.json()["tai_khoan"]["co_totp"] is True
    ban_ghi_moi = ktk.tim_theo_email(EMAIL)
    assert ban_ghi_moi["totp_secret"] == TOTP_SECRET
    assert ban_ghi_moi["nhan"] == "nhãn mới"


def test_dang_nhap_module_da_cai_nhung_khong_co_ham_dang_nhap_502(moi_truong):
    """Lỗi 4: Module đã cài nhưng không có hàm dang_nhap phải trả 502 chua_ho_tro."""
    client, _ = moi_truong
    tai_id = _luu(client).json()["tai_khoan"]["id"]
    mod = _module_gia(client)
    ham_goc = getattr(mod, "dang_nhap", None)
    try:
        mod.dang_nhap = None
        r = client.post(f"/api/tai-khoan/da-luu/{tai_id}/dang-nhap", headers=QT,
                        json={"module_id": MODULE_ID})
        assert r.status_code == 502, r.text
        assert r.json()["error"]["code"] == "chua_ho_tro"
    finally:
        mod.dang_nhap = ham_goc



# ── NV177B: 3 việc theo dõi sau soát chéo NV177 ─────────────────────────────

def test_id_rong_don_le_duoc_bo_qua_tao_theo_email(moi_truong):
    """Theo dõi (a): POST id="" + email hợp lệ phải tạo theo email (200),
    không 404 khong_tim_thay oan như bản sau vá agy."""
    client, _ = moi_truong
    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "id": "", "email": EMAIL, "mat_khau": MAT_KHAU})
    assert r.status_code == 200, r.text
    assert r.json()["tai_khoan"]["id"] == ktk.ma_tai_khoan(EMAIL)


def test_id_rong_hang_loat_duoc_bo_qua(moi_truong):
    """Theo dõi (a) đường hàng loạt: mục id="" phải lưu theo email, không lỗi."""
    client, _ = moi_truong
    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "tai_khoan": [{"id": "", "email": EMAIL, "mat_khau": MAT_KHAU}]})
    assert r.status_code == 200, r.text
    than = r.json()
    assert len(than["luu"]) == 1 and than["loi"] == []


def test_email_ba_cham_la_khong_con_duoc_coi_la_che(moi_truong):
    """Theo dõi (c): chỉ email == che_email(email thật) mới giữ email cũ; mọi
    chuỗi bắt đầu bằng «…» khác phải bị đối xử như email thường (→ 400)."""
    client, _ = moi_truong
    tai_id = _luu(client).json()["tai_khoan"]["id"]
    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "id": tai_id, "email": "…***@other.invalid", "mat_khau": "MatKhauMoi_111"})
    assert r.status_code == 400, r.text
    # Mật khẩu KHÔNG được đổi bởi yêu cầu trên
    assert ktk.tim_theo_id(tai_id)["mat_khau"] == MAT_KHAU
    # Email che ĐÚNG vẫn giữ email cũ và cập nhật được
    r2 = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "id": tai_id, "email": ktk.che_email(EMAIL), "mat_khau": "MatKhauMoi_222"})
    assert r2.status_code == 200, r2.text
    ban_ghi = ktk.tim_theo_id(tai_id)
    assert ban_ghi["email"] == EMAIL and ban_ghi["mat_khau"] == "MatKhauMoi_222"


class _ModuleCho:
    """Module giả: lượt đăng nhập đầu hỏng ngay; lượt sau CHỜ cổng rồi hỏng."""

    def __init__(self):
        import asyncio
        self.cổng = asyncio.Event()
        self.da_vao = asyncio.Event()
        self.so_lan = 0

    async def dang_nhap(self, tai_khoan):
        from app.tai_khoan_hang_loat import KetQuaDangNhap
        self.so_lan += 1
        if self.so_lan >= 2:
            self.da_vao.set()
            await self.cổng.wait()
        return KetQuaDangNhap(False, ma_loi="sai_mat_khau")


def test_dua_doi_mat_khau_khong_hoi_sinh_trang_thai(moi_truong):
    """Theo dõi (b): lượt thử đang treo mà quản trị đổi mật khẩu (reset xoá
    trạng thái) thì khi lượt treo hỏng, trạng thái cũ KHÔNG được hồi sinh —
    tài khoản không bị chặn oan cho mật khẩu MỚI."""
    import asyncio
    client, _ = moi_truong
    tai_id = _luu(client).json()["tai_khoan"]["id"]
    # Mốc cap_nhat cố định ở quá khứ: lần đổi mật khẩu (time.time() hiện tại)
    # chắc chắn làm cap_nhat ĐỔI — test không phụ thuộc độ phân giải giây.
    du_lieu = ktk._doc()
    du_lieu[tai_id]["cap_nhat"] = 1_000_000
    ktk._ghi(du_lieu)
    mod = _ModuleCho()

    async def chay():
        kq1 = await ktk.thu_dang_nhap_lai(module=mod, module_id=MODULE_ID, dinh_danh=EMAIL)
        assert kq1["thanh_cong"] is False
        assert ktk._doc_trang_thai()[tai_id]["hong_lien_tiep"] == 1
        task = asyncio.create_task(
            ktk.thu_dang_nhap_lai(module=mod, module_id=MODULE_ID, dinh_danh=EMAIL))
        await asyncio.wait_for(mod.da_vao.wait(), timeout=5)
        # Quản trị đổi mật khẩu mới TRONG LÚC lượt thử đang treo → reset
        ktk.luu_tai_khoan(EMAIL, "MatKhauMoi_999")
        assert tai_id not in ktk._doc_trang_thai()
        mod.cổng.set()
        kq2 = await task
        assert kq2["thu"] is True and kq2["thanh_cong"] is False
        # Trạng thái KHÔNG được hồi sinh sau khi lượt treo ghi kết quả
        assert tai_id not in ktk._doc_trang_thai()
        return mod

    mod = asyncio.run(chay())
    # Và lần thử tiếp theo không bị chặn «hong_2_lien_tiep» oan
    mod.cổng.set()
    kq3 = asyncio.run(ktk.thu_dang_nhap_lai(module=mod, module_id=MODULE_ID, dinh_danh=EMAIL))
    assert kq3["thu"] is True


def test_dua_xoa_tai_khoan_khong_hoi_sinh_trang_thai(moi_truong):
    """Theo dõi (b) nhánh xoá: tài khoản bị xoá giữa lượt thử → lượt treo
    không được ghi lại trạng thái cho một id đã không còn tồn tại."""
    import asyncio
    client, _ = moi_truong
    tai_id = _luu(client).json()["tai_khoan"]["id"]
    mod = _ModuleCho()

    async def chay():
        await ktk.thu_dang_nhap_lai(module=mod, module_id=MODULE_ID, dinh_danh=EMAIL)
        assert ktk._doc_trang_thai()[tai_id]["hong_lien_tiep"] == 1
        task = asyncio.create_task(
            ktk.thu_dang_nhap_lai(module=mod, module_id=MODULE_ID, dinh_danh=EMAIL))
        await asyncio.wait_for(mod.da_vao.wait(), timeout=5)
        assert ktk.xoa_tai_khoan(tai_id) is True
        assert tai_id not in ktk._doc_trang_thai()
        mod.cổng.set()
        await task
        assert tai_id not in ktk._doc_trang_thai()

    asyncio.run(chay())
