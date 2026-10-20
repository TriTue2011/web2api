"""NV160 — module `chi-tieu`: sổ chi tiêu cá nhân (port từ c2a services/chi_tieu @ d879b03).

Bao phủ: ghi/xem/trừ bù theo hũ, cổng vượt tổng, IDOR giữa hai danh tính (mỗi người một sổ),
đầu vào sai, tách khoản, xoá hũ còn khoản thì xoá mềm, tệ dữ liệu hỏng không bị ghi đè,
quyền tệp 0600, không port email_chi (IMAP — phụ thuộc module email chưa gộp).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from app import ngu_canh
from app.kham_pha import doc_manifest, nap_ma

GOC = Path(__file__).resolve().parents[1]
THU_MUC_MODULE = GOC / "modules" / "chitieu"
ID_DOC, ID_GHI = "chi_tieu.doc", "chi_tieu.ghi"


def _nap():
    manifest, loi = doc_manifest(THU_MUC_MODULE)
    assert loi is None, loi
    lop, ten_goi = nap_ma(THU_MUC_MODULE, manifest, "test")
    return lop, manifest, ten_goi


@pytest.fixture()
def module(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    lop, manifest, _ = _nap()
    m = lop(manifest, _thu_muc_du_lieu=str(tmp_path / "du_lieu" / "chitieu"))
    m.nap({})
    return m


def _goi(module, id_, viec, ts=None, nguoi=None):
    """Gọi công cụ với ngữ cảnh danh tính ``nguoi`` (không có → bối cảnh rỗng)."""
    tok = None
    if nguoi is not None:
        tok = ngu_canh.dat({"id": nguoi, "loai": "khach", "pham_vi": ["thiet_bi:ghi"]})
    try:
        return asyncio.run(module.thuc_hien(id_, viec, ts or {}))
    finally:
        if tok is not None:
            ngu_canh.khoi_phuc(tok)


def _ghi(module, hu, tien, ghi_chu="", nguoi="nguoi.a@example.com", **them):
    return _goi(module, ID_GHI, "ghi_chi", {"hu": hu, "so_tien": tien, "ghi_chu": ghi_chu, **them}, nguoi=nguoi)


# ── manifest & thực thể ──────────────────────────────────────────────────────
def test_manifest_dung_chuan(module):
    manif = module.manifest
    assert manif["id"] == "chitieu" and manif["tien_to"] == "chi_tieu"
    assert manif["cung_cap"] == ["thiet_bi"]
    # Không có đường mạng ra: module thuần cục bộ.
    assert manif["tai_nguyen"]["mang_ra"] == []
    ds = asyncio.run(module.thuc_the())  # NV181: thuc_the là async def (hợp đồng lõi)
    assert [e["id"] for e in ds] == [ID_DOC, ID_GHI]
    doc = next(e for e in ds if e["id"] == ID_DOC)
    ghi = next(e for e in ds if e["id"] == ID_GHI)
    assert doc["rui_ro_mac_dinh"] == "doc" and ghi["rui_ro_mac_dinh"] == "ghi"
    assert doc["loai"] == ghi["loai"] == "cong_cu"
    ten_doc = {t["hanh_dong"] if isinstance(t, dict) else t for t in doc["tinh_nang"]}
    ten_ghi = {t["hanh_dong"] if isinstance(t, dict) else t for t in ghi["tinh_nang"]}
    assert "ghi_chi" not in ten_doc and "xem_ngan_sach" not in ten_ghi


def test_thuc_hien_tu_choi_hanh_dong_khong_khai(module):
    kq = _goi(module, ID_DOC, "ghi_chi", {"hu": "Thiết Yếu", "so_tien": 1000})
    assert kq["ok"] is False and kq["loi"] == "hanh_dong_khong_ho_tro"
    kq2 = _goi(module, "chi_tieu.la", "xem_ngan_sach", {})
    assert kq2["ok"] is False and kq2["loi"] == "khong_thay_thuc_the"


# ── nghiệp vụ cốt lõi ──────────────────────────────────────────────────────
def test_ghi_va_xem_ngan_sach(module):
    kq = _ghi(module, "Thiết Yếu", 50000, "cơm trưa")
    assert kq["da_ghi"] is True and kq["so_tien_vua_ghi"] == 50000 and kq["hu"] == "Thiết Yếu"
    ns = _goi(module, ID_DOC, "xem_ngan_sach", {}, nguoi="nguoi.a@example.com")
    assert ns["tong_da_chi"] == 50000 and ns["tong_ngan_sach"] == 10_000_000
    ty = next(h for h in ns["hu"] if h["ten"] == "Thiết Yếu")
    assert ty["han_muc_hieu_luc"] == 5_500_000 and ty["da_chi"] == 50000


def test_tim_hu_khong_dau_va_mo_hu(module):
    kq = _ghi(module, "thiet yeu", 10000)
    assert kq["da_ghi"] is True and kq["hu"] == "Thiết Yếu"
    loi = _ghi(module, "hũ không có", 10000)
    assert loi["ok"] is False and "Không có hũ" in loi["loi"]


def test_tu_bu_theo_thu_tu(module):
    # Thiết Yếu có 5,5tr; chi vượt → phần vượt rút từ Hưởng Thụ (thu_tu_bu=1) trước.
    _goi(module, ID_GHI, "luu_cau_hinh", {"luong": 1_000_000}, nguoi="nguoi.a@example.com")
    kq = _ghi(module, "Thiết Yếu", 600_000)  # hạn mức 550.000 → vượt 50.000
    assert kq["da_ghi"] is True
    assert kq["trang_thai"] == "vuot_han_muc" and kq["duoc_bu"] == 50_000
    assert kq["bu_tu"][0]["ten"] == "Hưởng Thụ"


def test_cong_vuot_tong(module):
    _goi(module, ID_GHI, "luu_cau_hinh", {"luong": 1_000_000}, nguoi="nguoi.a@example.com")
    kq = _ghi(module, "Thiết Yếu", 950_000)
    assert kq["da_ghi"] is True
    kq2 = _ghi(module, "Thiết Yếu", 100_000)  # còn 50.000 → hỏi lại
    assert kq2.get("da_ghi") is False and kq2.get("can_xac_nhan") is True
    assert "VƯỢT" in kq2["canh_bao"]
    ls = _goi(module, ID_DOC, "xem_lich_su", {}, nguoi="nguoi.a@example.com")
    assert ls["so_luong"] == 1                      # chưa ghi thật khi chưa xác nhận
    kq3 = _ghi(module, "Thiết Yếu", 100_000, xac_nhan_vuot_tong=True)
    assert kq3["da_ghi"] is True


def test_sua_chi_cung_qua_cong_vuot_tong(module):
    _goi(module, ID_GHI, "luu_cau_hinh", {"luong": 1_000_000}, nguoi="nguoi.a@example.com")
    g = _ghi(module, "Thiết Yếu", 950_000)
    _ghi(module, "Hưởng Thụ", 40_000)
    sua = _goi(module, ID_GHI, "sua_chi", {"id": g["id"], "so_tien": 961_000}, nguoi="nguoi.a@example.com")
    assert sua.get("can_xac_nhan") is True and sua.get("da_sua") is not True


def test_chi_phi_dac_biet_tru_hu_hung(module):
    _goi(module, ID_GHI, "luu_cau_hinh", {"luong": 1_000_000}, nguoi="nguoi.a@example.com")
    kq = _goi(module, ID_GHI, "chi_phi_dac_biet", {"mo_ta": "sửa xe", "so_tien": 150_000},
              nguoi="nguoi.a@example.com")
    assert kq["da_ghi"] is True
    ns = _goi(module, ID_DOC, "xem_ngan_sach", {}, nguoi="nguoi.a@example.com")
    dp = next(h for h in ns["hu"] if "Dự Phòng" in h["ten"])
    ht = next(h for h in ns["hu"] if h["ten"] == "Hưởng Thụ")
    # Dự Phòng (hạn mức 100.000, hứng trước) bị trừ hết, còn thiếu 50.000 trừ Hưởng Thụ.
    assert dp["han_muc_hieu_luc"] == 0 and ht["han_muc_hieu_luc"] == 50_000


def test_tach_chi_kiem_tong(module):
    g = _ghi(module, "Thiết Yếu", 100_000, "hoá đơn gộp")
    tach = _goi(module, ID_GHI, "tach_chi", {"id": g["id"], "danh_sach": [
        {"hu": "Thiết Yếu", "so_tien": 60_000}, {"hu": "Hưởng Thụ", "so_tien": 30_000}]},
        nguoi="nguoi.a@example.com")
    assert tach["ok"] is False and "phải bằng" in tach["loi"]
    tach2 = _goi(module, ID_GHI, "tach_chi", {"id": g["id"], "danh_sach": [
        {"hu": "Thiết Yếu", "so_tien": 60_000}, {"hu": "Hưởng Thụ", "so_tien": 40_000}]},
        nguoi="nguoi.a@example.com")
    assert tach2["da_tach"] is True and len(tach2["id_moi"]) == 2


def test_xoa_hu_con_khoan_chi_thi_xoa_mem(module):
    _ghi(module, "Học Tập", 10_000)
    ns1 = _goi(module, ID_DOC, "xem_ngan_sach", {}, nguoi="nguoi.a@example.com")
    hu_id = next(h["id"] for h in ns1["hu"] if h["ten"] == "Học Tập")
    _goi(module, ID_GHI, "xoa_hu", {"id": hu_id}, nguoi="nguoi.a@example.com")
    ns2 = _goi(module, ID_DOC, "xem_ngan_sach", {}, nguoi="nguoi.a@example.com")
    assert ns2["tong_da_chi"] == 10_000          # tiền đã tiêu vẫn tính
    assert all(h["ten"] != "Học Tập" for h in ns2["hu"])
    ls = _goi(module, ID_DOC, "xem_lich_su", {}, nguoi="nguoi.a@example.com")
    assert ls["giao_dich"][0]["hu_ten"] == "Học Tập"   # lịch sử đọc được tên hũ đã xoá


def test_de_xuat_chia_theo_thu_nhap_tung_ky(module):
    dx = _goi(module, ID_DOC, "de_xuat", {}, nguoi="nguoi.a@example.com")
    assert dx["thang"] and isinstance(dx["de_xuat"], list)
    assert "phan_bo_de_xuat" in dx


def test_canh_bao_chi_doc(module):
    _goi(module, ID_GHI, "luu_cau_hinh", {"luong": 1_000_000}, nguoi="nguoi.a@example.com")
    _ghi(module, "Thiết Yếu", 900_000)  # > 80% tổng kỳ
    cb = _goi(module, ID_DOC, "xem_canh_bao", {}, nguoi="nguoi.a@example.com")
    assert cb["co_canh_bao"] is True and any("hạn mức" in x for x in cb["canh_bao"])


def test_cong_ty_va_giai_chi(module):
    _goi(module, ID_GHI, "cong_ty_tam_ung", {"so_tien": 500_000, "mo_ta": "tạm ứng"}, nguoi="nguoi.a@example.com")
    _goi(module, ID_GHI, "cong_ty_chi", {"so_tien": 200_000, "mo_ta": "taxi"}, nguoi="nguoi.a@example.com")
    xem = _goi(module, ID_DOC, "xem_cong_ty", {}, nguoi="nguoi.a@example.com")
    assert xem["so_du"] == 300_000
    gc = _goi(module, ID_GHI, "giai_chi", {}, nguoi="nguoi.a@example.com")
    assert gc["da_giai_chi"] is True and gc["so_du"] == 300_000
    sua = _goi(module, ID_GHI, "cong_ty_xoa", {"id": 1}, nguoi="nguoi.a@example.com")
    assert sua["ok"] is False  # đã giải chi thì không sửa/xoá được


# ── cách ly theo danh tính (IDOR) ───────────────────────────────────────────
def test_moi_danh_tinh_mot_so_rieng(module):
    _ghi(module, "Thiết Yếu", 111_000, nguoi="nguoi.a@example.com")
    _ghi(module, "Thiết Yếu", 222_000, nguoi="nguoi.b@example.com")
    a = _goi(module, ID_DOC, "xem_ngan_sach", {}, nguoi="nguoi.a@example.com")
    b = _goi(module, ID_DOC, "xem_ngan_sach", {}, nguoi="nguoi.b@example.com")
    assert a["tong_da_chi"] == 111_000 and b["tong_da_chi"] == 222_000
    assert a["ten_so"] != b["ten_so"] or a["tong_da_chi"] != b["tong_da_chi"]


def test_idor_khong_sua_xoa_so_nguoi_khac(module):
    ga = _ghi(module, "Thiết Yếu", 111_000, nguoi="nguoi.a@example.com")
    xoa = _goi(module, ID_GHI, "xoa_chi", {"id": ga["id"]}, nguoi="nguoi.b@example.com")
    assert xoa["ok"] is False
    sua = _goi(module, ID_GHI, "sua_chi", {"id": ga["id"], "so_tien": 1}, nguoi="nguoi.b@example.com")
    assert sua["ok"] is False
    ls_b = _goi(module, ID_DOC, "xem_lich_su", {}, nguoi="nguoi.b@example.com")
    assert ls_b["so_luong"] == 0
    ls_a = _goi(module, ID_DOC, "xem_lich_su", {}, nguoi="nguoi.a@example.com")
    assert ls_a["so_luong"] == 1


# ── kiểm đầu vào / bảo mật dữ liệu ──────────────────────────────────────────
@pytest.mark.parametrize("tien,loi_mong", [
    (50_000.5, "số tiền"), (True, "số tiền"), (0, "dương"), (-5, "dương"),
    (10**12 + 1, "tối đa"), ("abc", "số tiền"),
])
def test_kiem_so_tien(module, tien, loi_mong):
    kq = _ghi(module, "Thiết Yếu", tien)
    assert kq["ok"] is False and loi_mong in kq["loi"]


def test_tien_so_thuc_tron_va_chuoi_so(module):
    assert _ghi(module, "Thiết Yếu", 50_000.0)["da_ghi"] is True
    assert _ghi(module, "Thiết Yếu", "30000")["da_ghi"] is True


def test_tran_do_dai_ghi_chu(module):
    kq = _ghi(module, "Thiết Yếu", 1000, "x" * 501)
    assert kq["ok"] is False and "quá dài" in kq["loi"]


def test_xss_ghi_chu_luu_dang_du_lieu(module):
    doc = "<script>alert(1)</script>"
    _ghi(module, "Thiết Yếu", 1000, doc)
    ls = _goi(module, ID_DOC, "xem_lich_su", {}, nguoi="nguoi.a@example.com")
    # Dữ liệu round-trip nguyên văn (dạng DATA); JSON mã hoá an toàn — hiển thị do tầng giao diện escape.
    assert ls["giao_dich"][0]["ghi_chu"] == doc
    assert json.dumps(ls, ensure_ascii=False)


def test_db_hong_khong_bi_ghi_de(module, tmp_path):
    _ghi(module, "Thiết Yếu", 1000)
    db = tmp_path / "du_lieu" / "chitieu" / "chi_tieu.db"
    assert db.exists()
    rac = b"KHOONG PHAI SQLITE " * 10
    db.write_bytes(rac)
    kq = _ghi(module, "Thiết Yếu", 2000)
    assert kq["ok"] is False
    assert db.read_bytes() == rac   # còn nguyên, không có đường nào xoá/ghi đè


def test_quyen_tep_du_lieu_0600(module, tmp_path):
    _ghi(module, "Thiết Yếu", 1000)
    db = tmp_path / "du_lieu" / "chitieu" / "chi_tieu.db"
    assert (db.stat().st_mode & 0o777) == 0o600
    assert ((tmp_path / "du_lieu" / "chitieu").stat().st_mode & 0o777) == 0o700
    assert (db.stat().st_size) > 0


def test_khong_port_email_chi(module):
    # email_chi.py của c2a kéo theo IMAP + thread quét + model — spec yêu cầu KHÔNG port,
    # chỉ ghi phụ thuộc vào module `email` (chưa gộp). Module này không được có mã đọc hộp thư.
    ma = (THU_MUC_MODULE / "module.py").read_text(encoding="utf-8")
    ma += (THU_MUC_MODULE / "kho.py").read_text(encoding="utf-8")
    ma += (THU_MUC_MODULE / "nghiep_vu.py").read_text(encoding="utf-8")
    ma += (THU_MUC_MODULE / "canh_bao.py").read_text(encoding="utf-8")
    for cum in ("import imaplib", "imaplib.IMAP", "BODY.PEEK", "CREATE TABLE IF NOT EXISTS hop_thu",
                "CREATE TABLE IF NOT EXISTS thu_da_doc", "def quet_ngay", "def thu_ket_noi"):
        assert cum not in ma, cum
    doc = (THU_MUC_MODULE / "README.md").read_text(encoding="utf-8")
    assert "email" in doc.lower() and "chưa gộp" in doc.lower()  # điểm nối đã ghi vào tài liệu


# ── NV160-SUA · D2: nhật ký lõi không ghi số tiền/ghi chú rõ chữ ─────────────
# Phát hiện D2 (soat-doc-lap-B.md mục 5): lược đồ hành động `_hd()` không khai
# «bi_mat» nên lõi (app.thiet_bi.lam_sach_tham_so, SEC-004b) giữ NGUYÊN giá trị
# khi ghi nhật ký kiểm toán kênh «thiet_bi» (bước 9 của thuc_hien_hanh_dong_loi)
# và khi ghi hàng chờ duyệt → quản trị đọc nhật ký là thấy chi tiêu của mọi người.
# Vá: khai «bi_mat: true» cho MỌI trường mang nội dung tài chính cá nhân
# (số tiền, văn bản tự do, lương, tên hũ/tên sổ, danh sách tách khoản).

#: Trường mang nội dung tài chính cá nhân — ở bất kỳ hành động nào cũng phải che.
_TRUONG_NHAY_CAM = {"so_tien", "ghi_chu", "mo_ta", "luong", "hu", "ten", "danh_sach"}

_SO_TIEN_LE = 987_654_321
_GHI_CHU_LE = "khám bệnh — bí mật của B QZX987"


def _cac_thuc_the(module):
    """thuc_the() đồng bộ ở bản này, bất đồng bộ sau NV181 (D1) — chịu cả hai."""
    import inspect
    kq = module.thuc_the()
    if inspect.iscoroutine(kq):
        kq = asyncio.run(kq)
    return kq


def test_d2_moi_truong_nhay_cam_deu_khai_bi_mat(module):
    """Mọi trường nhạy cảm xuất hiện trong lược đồ đều phải «bi_mat: true»."""
    thieu = []
    for ent in _cac_thuc_the(module):
        for tn in ent["tinh_nang"]:
            props = (tn.get("tham_so") or {}).get("properties") or {}
            for ten_truong, spec in props.items():
                if ten_truong in _TRUONG_NHAY_CAM and spec.get("bi_mat") is not True:
                    thieu.append(f"{ent['id']}.{tn['hanh_dong']}.{ten_truong}")
    assert not thieu, f"trường nhạy cảm chưa khai bi_mat: {thieu}"


def test_d2_lam_sach_qua_loi_che_het_noi_dung_nhay_cam(module):
    """Cho tham số qua ĐÚNG hàm làm sạch của lõi: không còn số tiền/ghi chú/lương/tên."""
    from app.thiet_bi import lam_sach_tham_so, lay_luoc_do_tham_so

    ds = {e["id"]: e for e in _cac_thuc_the(module)}
    ghi, doc = ds[ID_GHI], ds[ID_DOC]

    cac_ca = [
        (ghi, "ghi_chi", {"hu": "Thiết Yếu", "so_tien": _SO_TIEN_LE, "ghi_chu": _GHI_CHU_LE,
                          "xac_nhan_vuot_tong": True}),
        (ghi, "sua_chi", {"id": 7, "hu": "Học Tập", "so_tien": _SO_TIEN_LE, "ghi_chu": _GHI_CHU_LE}),
        (ghi, "tach_chi", {"id": 3, "danh_sach": [{"so_tien": _SO_TIEN_LE, "ghi_chu": _GHI_CHU_LE},
                                                  {"so_tien": 5, "ghi_chu": "mục hai"}]}),
        (ghi, "thu_nhap_them", {"mo_ta": _GHI_CHU_LE, "so_tien": _SO_TIEN_LE}),
        (ghi, "luu_cau_hinh", {"ten": "Sổ bí mật QZX987", "luong": _SO_TIEN_LE, "ngay_bat_dau": 1}),
        (ghi, "cong_ty_chi", {"so_tien": _SO_TIEN_LE, "mo_ta": _GHI_CHU_LE}),
        (ghi, "chi_phi_dac_biet", {"mo_ta": _GHI_CHU_LE, "so_tien": _SO_TIEN_LE}),
        (ghi, "cong_ty_tam_ung", {"so_tien": _SO_TIEN_LE, "mo_ta": _GHI_CHU_LE}),
        (ghi, "them_hu", {"ten": "Hũ bí mật QZX987", "ty_le": 10}),
    ]
    for ent, hd, ts in cac_ca:
        luoc_do = lay_luoc_do_tham_so(ent["tinh_nang"], hd)
        assert luoc_do, f"không thấy lược đồ cho {hd}"
        phang = json.dumps(lam_sach_tham_so(ts, luoc_do), ensure_ascii=False)
        for le in (str(_SO_TIEN_LE), "khám bệnh", "bí mật của B", "Sổ bí mật"):
            assert le not in phang, f"{hd}: còn lộ «{le}» trong tham số đã làm sạch: {phang}"
    # Trường cấu trúc (không phải nội dung cá nhân) vẫn giữ nguyên — che có mục tiêu:
    luoc_do = lay_luoc_do_tham_so(ghi["tinh_nang"], "ghi_chi")
    sach = lam_sach_tham_so({"xac_nhan_vuot_tong": True}, luoc_do)
    assert sach["xac_nhan_vuot_tong"] is True
    luoc_do2 = lay_luoc_do_tham_so(ghi["tinh_nang"], "sua_chi")
    assert lam_sach_tham_so({"id": 7}, luoc_do2)["id"] == 7


_KHOA_QT_D2 = "khoa-quan-tri-test-nv160-sua-0123456789ab"
_QT_D2 = {"Authorization": f"Bearer {_KHOA_QT_D2}"}


@pytest.fixture()
def moi_truong_loi(tmp_path, monkeypatch):
    """Môi trường lõi THẬT (khuôn test_web2api_thiet_bi.moi_truong): app đầy đủ +
    SoModule rỗng, module chitieu được thả vào thư mục modules của dữ liệu."""
    import shutil

    from fastapi.testclient import TestClient

    from app import so_module as _so_module_mod
    from app.main import app
    from app.so_module import SoModule

    data_dir = tmp_path / "data-loi"
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    anh_dir = tmp_path / "anh"
    anh_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", _KHOA_QT_D2)
    monkeypatch.setattr(_so_module_mod, "THU_MUC_ANH", anh_dir)

    dich = data_dir / "modules" / "chitieu"
    shutil.copytree(THU_MUC_MODULE, dich,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        r_nap = client.post("/api/modules/nap-lai", headers=_QT_D2)
        assert r_nap.status_code == 200, r_nap.text
        assert "chitieu" in (r_nap.json().get("them") or []), r_nap.json()
        r_cai = client.post("/api/modules/chitieu/cai", headers=_QT_D2,
                            json={"cau_hinh": {}})
        assert r_cai.status_code == 200 and r_cai.json().get("ok") is True, r_cai.text
        yield client


def test_d2_nhat_ky_loi_sau_ghi_chi_khong_chua_so_tien_ghi_chu(moi_truong_loi):
    """QUA LÕI THẬT: ghi một khoản chi bằng HTTP → đọc nhật ký lõi (kênh thiet_bi):
    dòng kiểm toán của chính lần ghi đó KHÔNG chứa số tiền/ghi chú rõ chữ, mà khoản
    chi vẫn ghi thành công (test không rỗng). Cần NV181 (D1: thuc_the bất đồng bộ)
    áp trước — trên cây chưa nối lõi, thực thể không hiện nên test đỏ ở bước tra cứu."""
    from app import nhat_ky

    client = moi_truong_loi
    r_xn = client.post("/api/thiet-bi/chi_tieu.ghi/xac-nhan", headers=_QT_D2)
    assert r_xn.status_code == 200, r_xn.text
    # Số tiền phải dưới tổng ngân sách mặc định (10.000.000) kẻo module đòi xác nhận vượt tổng.
    tien_e2e = 9_876_543
    r = client.post("/api/thiet-bi/chi_tieu.ghi/hanh-dong", headers=_QT_D2, json={
        "hanh_dong": "ghi_chi",
        "tham_so": {"hu": "Thiết Yếu", "so_tien": tien_e2e, "ghi_chu": _GHI_CHU_LE},
    })
    assert r.status_code == 200, r.text
    assert r.json().get("da_ghi") is True, r.json()

    dong = nhat_ky.doc(kenh="thiet_bi", gioi_han=500)
    dong_ghi = [d for d in dong if (d.get("them") or {}).get("hanh_dong") == "ghi_chi"]
    assert dong_ghi, "không thấy dòng nhật ký của lần ghi_chi — test sẽ rỗng"
    phang = json.dumps(dong_ghi, ensure_ascii=False)
    for le in (str(tien_e2e), "khám bệnh", "bí mật của B", "Thiết Yếu"):
        assert le not in phang, f"nhật ký lõi còn lộ «{le}»: {phang}"
    # Tham số trong nhật ký chỉ còn dạng che:
    ts = (dong_ghi[0].get("them") or {}).get("tham_so") or {}
    assert ts.get("so_tien") == "…che" and ts.get("ghi_chu") == "…che", ts
