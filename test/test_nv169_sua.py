"""Test NV169-SUA — vòng sửa theo soát độc lập C1 (K1 phần module, K2, K3, K4).

- K2 (Trung bình): MỌI hành động của module (kể cả «xem_nhat_ky» — trước đây
  khách chỉ có thiet_bi:ghi đọc được tên người + mã vân tay + giờ mở cửa)
  phải qua cổng vai quản trị/nội bộ dùng chung, đóng khi lỗi.
- K3 (Thấp): «ma_thiet_bi» chỉ nhận [A-Za-z0-9_-]{1,64} — chèn ../ ? . # %
  vào đường dẫn API Tuya phải bị từ chối ở cai() VÀ ở hàm dựng backend.
- K4 (Thấp): nhật ký «bất biến» làm thật: thư mục 0700, tệp tạo bằng
  os.open 0600, mỗi dòng mang băm của dòng trước (xích băm) + hàm kiểm xích;
  «từ đâu» ghi đúng nguồn trong ngữ cảnh; ghi nhật ký SAU khi đã có kết quả
  vật lý mà lỗi thì không được lật kết quả.
- K1 (phần module): README phải nói thẳng giới hạn của step-up.

Dịch vụ GIẢ như bộ test NV169 gốc. Không mạng thật.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import stat
import time
from pathlib import Path

import pytest

from test_nv169_khoa_cua import (
    AI_DT, KHACH_DT, QT_DT, KhoaGia, _cau_hinh, _module_moi, _tai, chay,
    ngu_canh_cua,
)

GOC = Path(__file__).resolve().parents[1]
README_MODULE = GOC / "modules" / "khoa-cua" / "README.md"

TAT_CA_HANH_DONG = [
    ("khoa_cua.khoa_chinh", "chuan_bi_mo", {}),
    ("khoa_cua.khoa_chinh", "mo_khoa", {"ma_xac_nhan": "ma-gia-dung-do-dai-0123"}),
    ("khoa_cua.khoa_chinh", "dong_khoa", {}),
    ("khoa_cua.nhat_ky", "xem_nhat_ky", {"so_ngay": 1}),
    ("khoa_cua.nhat_ky", "dat_ten_ma", {"ma": "face#17", "ten": "Người lạ"}),
    ("khoa_cua.nhat_ky", "xoa_ten_ma", {"ma": "face#17"}),
]


def _mod_co_nhat_ky(tmp_path, monkeypatch):
    """Module + khoá giả có sẵn một mục nhật ký mang TÊN NGƯỜI (dữ liệu cá nhân)."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    be = KhoaGia()
    be.nhat_ky = [{"ma": "fingerprint#11", "ten_tuya": "Con trai Nam",
                   "ts": time.time() - 60, "thiet_bi": "khoá giả"}]
    mod._be = be
    return mod, be


# ── K2: khách bị từ chối ở TỪNG hành động ─────────────────────────────────

@pytest.mark.parametrize("thuc_the,hd,ts", TAT_CA_HANH_DONG,
                         ids=[h[1] for h in TAT_CA_HANH_DONG])
def test_K2_khach_bi_tu_choi_o_tung_hanh_dong(tmp_path, monkeypatch,
                                              thuc_the, hd, ts):
    mod, be = _mod_co_nhat_ky(tmp_path, monkeypatch)
    with ngu_canh_cua(KHACH_DT, nguon="kenh"):
        kq = chay(mod.thuc_hien(thuc_the, hd, ts))
    assert kq["ok"] is False, (hd, kq)
    assert be.so_mo == 0 and be.so_dong == 0


@pytest.mark.parametrize("thuc_the,hd,ts", TAT_CA_HANH_DONG,
                         ids=[h[1] for h in TAT_CA_HANH_DONG])
def test_K2_thieu_ngu_canh_bi_tu_choi_o_tung_hanh_dong(tmp_path, monkeypatch,
                                                       thuc_the, hd, ts):
    mod, be = _mod_co_nhat_ky(tmp_path, monkeypatch)
    kq = chay(mod.thuc_hien(thuc_the, hd, ts))  # không đặt ngữ cảnh — đóng khi lỗi
    assert kq["ok"] is False, (hd, kq)
    assert be.so_mo == 0 and be.so_dong == 0


def test_K2_khach_xem_nhat_ky_khong_lo_ten_nguoi(tmp_path, monkeypatch):
    """Đúng kịch bản soát: khách thiet_bi:ghi gọi xem_nhat_ky — không được
    nhận tên người/mã vân tay/giờ mở cửa trong bất kỳ trường nào."""
    mod, be = _mod_co_nhat_ky(tmp_path, monkeypatch)
    with ngu_canh_cua(KHACH_DT):
        kq = chay(mod.thuc_hien("khoa_cua.nhat_ky", "xem_nhat_ky", {"so_ngay": 1}))
    assert kq["ok"] is False
    chuoi = json.dumps(kq, ensure_ascii=False)
    assert "Con trai Nam" not in chuoi
    assert "fingerprint#11" not in chuoi


def test_K2_mo_hinh_xem_nhat_ky_bi_tu_choi(tmp_path, monkeypatch):
    mod, be = _mod_co_nhat_ky(tmp_path, monkeypatch)
    with ngu_canh_cua(AI_DT, nguon="ai"):
        kq = chay(mod.thuc_hien("khoa_cua.nhat_ky", "xem_nhat_ky", {"so_ngay": 1}))
    assert kq["ok"] is False


def test_K2_quan_tri_xem_nhat_ky_van_duoc(tmp_path, monkeypatch):
    """Chặn khách không được làm hỏng đường của quản trị."""
    mod, be = _mod_co_nhat_ky(tmp_path, monkeypatch)
    with ngu_canh_cua(QT_DT):
        kq = chay(mod.thuc_hien("khoa_cua.nhat_ky", "xem_nhat_ky", {"so_ngay": 1}))
    assert kq["ok"] is True
    assert kq["muc"][0]["ma"] == "fingerprint#11"


# ── K3: ma_thiet_bi chỉ ký tự an toàn ──────────────────────────────────────

@pytest.mark.parametrize("ma_xau", [
    "abc/../../../v1.0/iot-03/devices/OTHER/commands?x=",  # mẫu của soát C1
    "../", "abc?x=1", "abc.def", "abc#1", "abc%20d", "a/b", "a b", "a" * 65,
])
def test_K3_cai_tu_choi_ma_thiet_bi_xau(tmp_path, monkeypatch, ma_xau):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    lop, _ = _tai()
    from test_nv169_khoa_cua import _manifest
    mod = lop(_manifest())
    r = chay(mod.cai(_cau_hinh(ma_thiet_bi=ma_xau)))
    assert r["ok"] is False, ma_xau
    assert mod._be is None  # không dựng backend với mã xấu


@pytest.mark.parametrize("ma_tot", ["bf1234567890abcdef1234", "khoa-gia-001",
                                    "ABC_def-123"])
def test_K3_cai_chap_nhan_ma_thiet_bi_hop_le(tmp_path, monkeypatch, ma_tot):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    lop, _ = _tai()
    from test_nv169_khoa_cua import _manifest
    mod = lop(_manifest())
    r = chay(mod.cai(_cau_hinh(ma_thiet_bi=ma_tot)))
    assert r["ok"] is True, ma_tot


def test_K3_backend_tu_choi_ma_xau_o_ham_dung(tmp_path, monkeypatch):
    """Lớp sâu nhất: dựng thẳng KhoaDamMay bằng mã chèn đường dẫn → lỗi ngay,
    mã không bao giờ tới được URL."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    _module_moi()  # nạp gói module để import dam_may như anh em
    import importlib
    from test_nv169_khoa_cua import _ten_goi
    dam_may = importlib.import_module(f"{_ten_goi}.dam_may")
    with pytest.raises(dam_may.LoiKhoaCua):
        dam_may.KhoaDamMay("access-id-gia-0123", "secret-gia-0123456789abcdef",
                           "america", "abc/../../../v1.0/devices/OTHER?x=")
    assert dam_may.ma_thiet_bi_hop_le("bf1234567890abcdef1234") is True
    assert dam_may.ma_thiet_bi_hop_le("abc?x=") is False


# ── K4: nhật ký xích băm, quyền tệp, nguồn gọi ─────────────────────────────

def _luong_mo_day_du(mod):
    with ngu_canh_cua(QT_DT):
        cb = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "chuan_bi_mo", {}))
        assert cb["ok"] is True
        kq = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "mo_khoa",
                                {"ma_xac_nhan": cb["ma_xac_nhan"]}))
        assert kq["ok"] is True
        chay(mod.thuc_hien("khoa_cua.khoa_chinh", "dong_khoa", {}))


def _p_nhat_ky(tmp_path):
    return tmp_path / "du_lieu" / "khoa-cua" / "nhat-ky-bat-bien.jsonl"


def test_K4_thu_muc_0700_tep_0600(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    mod._be = KhoaGia()
    _luong_mo_day_du(mod)
    p = _p_nhat_ky(tmp_path)
    assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_K4_xich_bam_lien_tiep_va_kiem_xich_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    mod._be = KhoaGia()
    _luong_mo_day_du(mod)
    p = _p_nhat_ky(tmp_path)
    raw = [d for d in p.read_bytes().splitlines() if d.strip()]
    assert len(raw) >= 3
    bam_truoc = "0" * 64
    for dong in raw:
        d = json.loads(dong)
        assert d["bam_truoc"] == bam_truoc
        bam_truoc = hashlib.sha256(dong).hexdigest()
    kq = mod.kiem_xich_nhat_ky()
    assert kq["ok"] is True
    assert kq["so_dong"] == len(raw)


def test_K4_sua_dong_giua_bi_phat_hien(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    mod._be = KhoaGia()
    _luong_mo_day_du(mod)
    p = _p_nhat_ky(tmp_path)
    dong = p.read_text(encoding="utf-8").splitlines()
    d = json.loads(dong[1])
    d["ai"] = "ke-gia-mao"  # sửa lặng lẽ người mở cửa
    dong[1] = json.dumps(d, ensure_ascii=False)
    p.write_text("\n".join(dong) + "\n", encoding="utf-8")
    kq = mod.kiem_xich_nhat_ky()
    assert kq["ok"] is False
    assert kq["dong_loi"] == 3  # dòng SAU dòng bị sửa mang bam_truoc không khớp


def test_K4_xoa_dong_giua_bi_phat_hien(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    mod._be = KhoaGia()
    _luong_mo_day_du(mod)
    p = _p_nhat_ky(tmp_path)
    dong = p.read_text(encoding="utf-8").splitlines()
    del dong[1]
    p.write_text("\n".join(dong) + "\n", encoding="utf-8")
    assert mod.kiem_xich_nhat_ky()["ok"] is False


def test_K4_nguon_ghi_dung_nguon_trong_ngu_canh(tmp_path, monkeypatch):
    """«Từ đâu» = nguồn thật trong ngữ cảnh (ở đây «kenh»), không cứng «nguoi»."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    mod._be = KhoaGia()
    with ngu_canh_cua(QT_DT, nguon="kenh"):
        cb = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "chuan_bi_mo", {}))
    assert cb["ok"] is True
    dong = [json.loads(x) for x in
            _p_nhat_ky(tmp_path).read_text(encoding="utf-8").splitlines()]
    cb_dong = [d for d in dong if d["su_kien"] == "chuan_bi_mo"]
    assert cb_dong and cb_dong[0]["nguon"] == "kenh"


def test_K4_loi_ghi_sau_khi_cua_da_mo_khong_lat_ket_qua(tmp_path, monkeypatch):
    """Ghi nhật ký «thành công» lỗi (đĩa đầy…) sau khi cửa ĐÃ mở: lệnh vẫn
    phải báo thành công — báo thất bại sẽ làm người dùng tưởng cửa chưa mở."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod = _module_moi()
    be = KhoaGia()
    mod._be = be
    ghi_that = mod._ghi_nhat_ky

    def ghi_loi_o_thanh_cong(su_kien, ai, nguon, chi_tiet=""):
        if su_kien == "mo_khoa_thanh_cong":
            raise OSError("đĩa đầy (giả)")
        return ghi_that(su_kien, ai, nguon, chi_tiet)

    mod._ghi_nhat_ky = ghi_loi_o_thanh_cong
    with ngu_canh_cua(QT_DT):
        cb = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "chuan_bi_mo", {}))
        kq = chay(mod.thuc_hien("khoa_cua.khoa_chinh", "mo_khoa",
                                {"ma_xac_nhan": cb["ma_xac_nhan"]}))
    assert be.so_mo == 1
    assert kq["ok"] is True


# ── K1 (phần module): README nói thẳng giới hạn step-up ────────────────────

def test_K1_readme_noi_thang_gioi_han_step_up():
    chu = README_MODULE.read_text(encoding="utf-8")
    assert "bấm nhầm" in chu
    assert "lấy cắp phiên" in chu
    assert "không chống" in chu
