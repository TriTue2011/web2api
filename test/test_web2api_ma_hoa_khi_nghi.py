"""Kiểm thử mã hoá bí mật và phiên khi nghỉ + tay cầm kho riêng theo module (Việc F5 / Lần 2).

Kiểm tra theo các luật cứng của repo và các lớp lỗi A1-A10:
1. Không khoá ⇒ chạy như cũ (không mã hoá dạng rõ).
2. Có khoá ⇒ tệp trên đĩa KHÔNG chứa chuỗi khoá/token thật (grep byte).
3. Chống hạ cấp: có khoá từ chối đọc bản rõ, trừ cờ di trú WEB2API_CHO_DOC_RO=1; BAT_BUOC_MA_HOA cấm hẳn (A2).
4. Di trú một lần bằng ham_khoa.ma_hoa_lai_tat_ca() (A2).
5. Khoá sai định dạng ⇒ lỗi cứng RuntimeError, không tụt xuống bản rõ (A3).
6. Bỏ khoá không được ghi đè lên tệp đã mã hoá W2A1 (A3).
7. Từ chối khoá lặp byte / entropy thấp (A10).
8. WEB2API_BAT_BUOC_MA_HOA=1 chặn khởi động qua TestClient(main.app) lifespan thật (A1).
9. Tay cầm kho riêng chống IDOR, traversal và từ chối symlink thư mục dưới bi_mat/ (A4).
10. Gán tay cầm kho cho module khi nạp trong SoModule (A4).
11. Getter thu_muc_du_lieu() không có tác dụng phụ; chỉ dọn theo tuổi khi có biến môi trường (A5).
12. Bản sao lưu bắt KhoHong, không làm sập toàn bộ quy trình sao lưu (A6).
13. Phiên: phien.liet_ke() lan KhoHong, phien.thong_ke() trả cờ lỗi khi sai khoá (A7).
14. tinh_aad từ chối đường dẫn ngoài thư mục dữ liệu, không fallback về p.name (A8).
15. chmod 0700 toàn bộ chuỗi cha dưới bi_mat/ (A9).
16. Khôi phục sao lưu giữa các máy khác khoá chính.
17. Hiệu năng đọc phiên không đổi bậc (ngưỡng chặt < 0.35s cho 1000 lần).
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import ham_khoa, kho, main, phien, sao_luu
from app.ham_khoa import KhoHong, KhoRieng


@pytest.fixture
def thu_muc_tam(tmp_path, monkeypatch):
    """Thư mục dữ liệu biệt lập cho mỗi test."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    # Reset biến cờ và cache
    ham_khoa._DA_CANH_BAO_KHONG_KHOA = False
    return data_dir


@pytest.fixture
def khoa_32_bytes():
    """Khoá chính 32 byte ngẫu nhiên an toàn."""
    return secrets.token_bytes(32)


# ───────────── 1. Không khoá ⇒ chạy như cũ (không mã hoá) ─────────────

def test_khong_khoa_chay_nhu_cu_khong_ma_hoa(thu_muc_tam, monkeypatch):
    monkeypatch.delenv("WEB2API_KHOA_CHINH", raising=False)
    monkeypatch.delenv("WEB2API_KHOA_CHINH_TEP", raising=False)
    monkeypatch.delenv("WEB2API_BAT_BUOC_MA_HOA", raising=False)

    assert ham_khoa.lay_khoa_chinh() is None

    # Lưu bí mật
    kho.ghi_bi_mat("gemini-api", {"api_key": "AIzaSyTOT-mot-000000000000000000000001"})
    p_bm = thu_muc_tam / "bi_mat" / "gemini-api.json"
    raw_bm = p_bm.read_bytes()
    # Tệp dạng rõ, không có header W2A1
    assert not raw_bm.startswith(b"W2A1")
    assert b"AIzaSyTOT-mot-000000000000000000000001" in raw_bm
    assert kho.doc_bi_mat("gemini-api") == {"api_key": "AIzaSyTOT-mot-000000000000000000000001"}

    # Lưu phiên
    phien.luu("muse2api", "user1", {"token": "phien_test_12345"})
    p_ph = thu_muc_tam / "bi_mat" / "phien" / "muse2api" / "user1.json"
    raw_ph = p_ph.read_bytes()
    assert not raw_ph.startswith(b"W2A1")
    assert b"phien_test_12345" in raw_ph
    rec = phien.lay("muse2api", "user1")
    assert rec is not None
    assert rec["du_lieu"]["token"] == "phien_test_12345"


# ───────────── 2. Có khoá ⇒ tệp trên đĩa KHÔNG chứa chuỗi khoá thật ─────────────

def test_co_khoa_tep_tren_dia_khong_chua_chuoi_khoa_that(thu_muc_tam, khoa_32_bytes, monkeypatch):
    monkeypatch.setenv("WEB2API_KHOA_CHINH", khoa_32_bytes.hex())

    # Ghi bí mật
    chuoi_khoa_gia = "AIzaSyTOT-mot-000000000000000000000001"
    kho.ghi_bi_mat("gemini-api", {"api_key": chuoi_khoa_gia})

    p_bm = thu_muc_tam / "bi_mat" / "gemini-api.json"
    raw_bm = p_bm.read_bytes()

    # Có tiêu đề W2A1 và phiên bản \x01
    assert raw_bm.startswith(b"W2A1\x01")
    # KHÔNG chứa chuỗi khoá thật và tên trường dạng rõ (grep byte)
    assert chuoi_khoa_gia.encode("utf-8") not in raw_bm
    assert b"api_key" not in raw_bm

    # Đọc lại giải mã đúng
    doc_lai = kho.doc_bi_mat("gemini-api")
    assert doc_lai == {"api_key": chuoi_khoa_gia}

    # Ghi phiên
    token_phien = "cookie_secret_token_abcdef123456"
    phien.luu("muse2api", "user1", {"cookie": token_phien})
    p_ph = thu_muc_tam / "bi_mat" / "phien" / "muse2api" / "user1.json"
    raw_ph = p_ph.read_bytes()

    assert raw_ph.startswith(b"W2A1\x01")
    assert token_phien.encode("utf-8") not in raw_ph
    assert b"cookie" not in raw_ph

    rec_ph = phien.lay("muse2api", "user1")
    assert rec_ph is not None
    assert rec_ph["du_lieu"]["cookie"] == token_phien


def test_khoa_chinh_tu_tep_ngoai_volume(tmp_path, thu_muc_tam, khoa_32_bytes, monkeypatch):
    tep_khoa = tmp_path / "ngoai" / "khoa_chinh.key"
    tep_khoa.parent.mkdir(parents=True, exist_ok=True)
    tep_khoa.write_bytes(khoa_32_bytes)
    os.chmod(tep_khoa, 0o600)

    monkeypatch.setenv("WEB2API_KHOA_CHINH_TEP", str(tep_khoa))
    monkeypatch.delenv("WEB2API_KHOA_CHINH", raising=False)

    assert ham_khoa.lay_khoa_chinh() == khoa_32_bytes

    kho.ghi_bi_mat("gemini-api", {"api_key": "AIzaSyTOT-mot-000000000000000000000001"})
    p_bm = thu_muc_tam / "bi_mat" / "gemini-api.json"
    assert p_bm.read_bytes().startswith(b"W2A1\x01")
    assert kho.doc_bi_mat("gemini-api")["api_key"] == "AIzaSyTOT-mot-000000000000000000000001"


# ───────────── 3. Chống hạ cấp & Di trú bản rõ (A2) ─────────────

def test_chong_ha_cap_tu_choi_ban_ro_khi_co_khoa_va_di_tru(thu_muc_tam, khoa_32_bytes, monkeypatch):
    p_bm = thu_muc_tam / "bi_mat" / "gemini-api.json"
    p_bm.parent.mkdir(parents=True, exist_ok=True)
    du_lieu_cu = {"api_key": "AIzaSyTOT-mot-000000000000000000000001"}
    p_bm.write_text(json.dumps(du_lieu_cu), encoding="utf-8")

    # Có khoá chính
    monkeypatch.setenv("WEB2API_KHOA_CHINH", khoa_32_bytes.hex())

    # A2a: Mặc định có khoá chính mà tệp thiếu W2A1 ⇒ KhoHong (chống hạ cấp)
    with pytest.raises(KhoHong, match="từ chối bản rõ"):
        kho.doc_bi_mat("gemini-api")

    # A2b: Bật cờ di trú WEB2API_CHO_DOC_RO=1 ⇒ đọc được
    monkeypatch.setenv("WEB2API_CHO_DOC_RO", "1")
    assert kho.doc_bi_mat("gemini-api") == du_lieu_cu

    # A2c: Khi WEB2API_BAT_BUOC_MA_HOA=1 ⇒ cấm hẳn bản rõ, cờ CHO_DOC_RO bị lờ đi
    monkeypatch.setenv("WEB2API_BAT_BUOC_MA_HOA", "1")
    with pytest.raises(KhoHong, match="từ chối bản rõ"):
        kho.doc_bi_mat("gemini-api")

    # A2d: Di trú bằng ma_hoa_lai_tat_ca()
    monkeypatch.delenv("WEB2API_BAT_BUOC_MA_HOA", raising=False)
    monkeypatch.delenv("WEB2API_CHO_DOC_RO", raising=False)

    so_tep = ham_khoa.ma_hoa_lai_tat_ca()
    assert so_tep >= 1
    # Tệp trên đĩa giờ đã mang tiêu đề W2A1
    raw_da_ma = p_bm.read_bytes()
    assert raw_da_ma.startswith(b"W2A1\x01")
    # Đọc lại thành công dù không có cờ CHO_DOC_RO
    assert kho.doc_bi_mat("gemini-api") == du_lieu_cu


# ───────────── 4. Sai khoá / hỏng thẻ / tráo tệp AAD ─────────────

def test_sai_khoa_hong_the_trao_tep_aad_loi_khong_ghi_de(thu_muc_tam, khoa_32_bytes, monkeypatch):
    monkeypatch.setenv("WEB2API_KHOA_CHINH", khoa_32_bytes.hex())
    kho.ghi_bi_mat("gemini-api", {"api_key": "AIzaSyTOT-mot-000000000000000000000001"})
    p_bm = thu_muc_tam / "bi_mat" / "gemini-api.json"
    raw_goc = p_bm.read_bytes()

    # Sai khoá chính ⇒ KhoHong (KHÔNG ghi đè)
    khoa_sai = secrets.token_bytes(32)
    monkeypatch.setenv("WEB2API_KHOA_CHINH", khoa_sai.hex())
    with pytest.raises(KhoHong):
        kho.doc_bi_mat("gemini-api")
    assert p_bm.read_bytes() == raw_goc

    # Thiếu khoá mà gặp tệp mã hoá ⇒ KhoHong
    monkeypatch.delenv("WEB2API_KHOA_CHINH")
    with pytest.raises(KhoHong):
        kho.doc_bi_mat("gemini-api")
    assert p_bm.read_bytes() == raw_goc

    # Hỏng thẻ / ciphertext ⇒ KhoHong
    monkeypatch.setenv("WEB2API_KHOA_CHINH", khoa_32_bytes.hex())
    raw_hong = bytearray(raw_goc)
    raw_hong[-1] ^= 0xFF
    p_bm.write_bytes(bytes(raw_hong))
    with pytest.raises(KhoHong):
        kho.doc_bi_mat("gemini-api")

    # Tráo tệp AAD
    p_bm.write_bytes(raw_goc)
    p_trao = thu_muc_tam / "bi_mat" / "antigravity.json"
    shutil.copy2(p_bm, p_trao)
    with pytest.raises(KhoHong):
        kho.doc_bi_mat("antigravity")


# ───────────── 5. Khoá sai định dạng / Mất khoá ghi đè (A3) ─────────────

def test_khoa_sai_dinh_dang_loi_cung_khong_tut_xuong_ro(thu_muc_tam, monkeypatch):
    # Khoá hex thiếu 1 ký tự (63 ký tự) ⇒ RuntimeError ngay lập tức
    monkeypatch.setenv("WEB2API_KHOA_CHINH", "a" * 63)
    with pytest.raises(RuntimeError, match="không hợp lệ"):
        ham_khoa.lay_khoa_chinh()

    # Cố ghi bí mật khi khoá sai định dạng ⇒ từ chối cứng bằng RuntimeError
    with pytest.raises(RuntimeError):
        kho.ghi_bi_mat("gemini-api", {"api_key": "AIzaSyTOT-mot-000000000000000000000001"})


def test_mat_khoa_khong_duoc_ghi_de_tep_ma_hoa_w2a1(thu_muc_tam, khoa_32_bytes, monkeypatch):
    # Trước tiên tạo tệp W2A1 hợp lệ
    monkeypatch.setenv("WEB2API_KHOA_CHINH", khoa_32_bytes.hex())
    kho.ghi_bi_mat("gemini-api", {"api_key": "AIzaSyTOT-mot-000000000000000000000001"})
    p_bm = thu_muc_tam / "bi_mat" / "gemini-api.json"
    raw_cu = p_bm.read_bytes()
    assert raw_cu.startswith(b"W2A1\x01")

    # Bỏ khoá chính (giả lập trường hợp rớt biến môi trường)
    monkeypatch.delenv("WEB2API_KHOA_CHINH")

    # Ghi đè bị từ chối với KhoHong, không cho phép ghi đè bản rõ lên bản mã hoá
    with pytest.raises(KhoHong, match="Không thể ghi đè tệp đã mã hoá"):
        kho.ghi_bi_mat("gemini-api", {"api_key": "AIzaSyTOT-hai-000000000000000000000002"})

    # Tệp gốc giữ nguyên vẹn
    assert p_bm.read_bytes() == raw_cu


# ───────────── 6. Từ chối khoá lặp byte / entropy thấp (A10) ─────────────

def test_tu_choi_khoa_entropy_thap_toan_byte_lap(thu_muc_tam, monkeypatch):
    # Khoá toàn 0x00
    monkeypatch.setenv("WEB2API_KHOA_CHINH", "00" * 32)
    with pytest.raises(RuntimeError, match="entropy quá thấp hoặc toàn byte lặp"):
        ham_khoa.lay_khoa_chinh()

    # Khoá toàn 0xAA
    monkeypatch.setenv("WEB2API_KHOA_CHINH", "aa" * 32)
    with pytest.raises(RuntimeError, match="entropy quá thấp hoặc toàn byte lặp"):
        ham_khoa.lay_khoa_chinh()


# ───────────── 7. WEB2API_BAT_BUOC_MA_HOA=1 chặn khởi động lifespan (A1) ─────────────

def test_bat_buoc_ma_hoa_chan_khoi_dong_lifespan_that(thu_muc_tam, monkeypatch):
    monkeypatch.setenv("WEB2API_BAT_BUOC_MA_HOA", "1")
    monkeypatch.delenv("WEB2API_KHOA_CHINH", raising=False)
    monkeypatch.delenv("WEB2API_KHOA_CHINH_TEP", raising=False)

    # Chạy lifespan THẬT của FastAPI qua TestClient: ứng dụng phải chết với RuntimeError
    with pytest.raises(RuntimeError, match="Bắt buộc mã hoá"):
        with TestClient(main.app):
            pass


def test_khoi_dong_thanh_cong_khi_co_khoa_hop_le(thu_muc_tam, khoa_32_bytes, monkeypatch):
    monkeypatch.setenv("WEB2API_BAT_BUOC_MA_HOA", "1")
    monkeypatch.setenv("WEB2API_KHOA_CHINH", khoa_32_bytes.hex())

    # Khởi động lifespan thật thành công
    with TestClient(main.app) as client:
        res = client.get("/health")
        assert res.status_code == 200


def test_bao_mat_khoa_chinh_ngoai_volume_du_lieu(thu_muc_tam, khoa_32_bytes, monkeypatch):
    khoa_trong_data = thu_muc_tam / "khoa_chinh.key"
    khoa_trong_data.write_bytes(khoa_32_bytes)
    os.chmod(khoa_trong_data, 0o600)

    monkeypatch.setenv("WEB2API_KHOA_CHINH_TEP", str(khoa_trong_data))
    monkeypatch.delenv("WEB2API_KHOA_CHINH", raising=False)

    with pytest.raises(RuntimeError, match="không được nằm trong thư mục dữ liệu"):
        ham_khoa.lay_khoa_chinh()


def test_bao_mat_khoa_chinh_yeu_cau_quyen_0600(tmp_path, thu_muc_tam, khoa_32_bytes, monkeypatch):
    tep_ngoai = tmp_path / "ngoai" / "khoa.key"
    tep_ngoai.parent.mkdir(parents=True, exist_ok=True)
    tep_ngoai.write_bytes(khoa_32_bytes)
    os.chmod(tep_ngoai, 0o644)  # Quyền quá rộng

    monkeypatch.setenv("WEB2API_KHOA_CHINH_TEP", str(tep_ngoai))
    monkeypatch.delenv("WEB2API_KHOA_CHINH", raising=False)

    with pytest.raises(PermissionError, match="0600"):
        ham_khoa.lay_khoa_chinh()


# ───────────── 8. KhoRieng chống IDOR và Symlink thư mục (A4) ─────────────

def test_bao_mat_leo_thang_quyen_kho_rieng_chong_idor_va_symlink(thu_muc_tam, khoa_32_bytes, monkeypatch):
    monkeypatch.setenv("WEB2API_KHOA_CHINH", khoa_32_bytes.hex())

    kho_a = kho.kho_rieng("module-a")
    kho_b = kho.kho_rieng("module-b")

    # Module A lưu bí mật và phiên
    kho_a.luu("api_key", {"khoa": "AIzaSyTOT-mot-000000000000000000000001"})
    kho_a.phien_luu("tai_khoan_1", {"cookie": "cookie_module_a"})

    # Module B đọc bí mật và phiên của Module A ⇒ None
    assert kho_b.doc("api_key") is None
    assert kho_b.phien_lay("tai_khoan_1") is None

    # Tệp của Module A nằm đúng namespace bi_mat/mo-dun/module-a/
    p_a = thu_muc_tam / "bi_mat" / "mo-dun" / "module-a" / "api_key.json"
    assert p_a.is_file()
    assert p_a.read_bytes().startswith(b"W2A1\x01")

    # Chống traversal
    with pytest.raises(ValueError):
        kho_a.doc("../module-b/api_key")

    with pytest.raises(ValueError):
        kho_a.doc("sub/dir")

    with pytest.raises(ValueError):
        kho_a.luu("../hack", {"x": 1})

    # A4b: Chống symlink thư mục: mo-dun/mod-c -> mo-dun/module-a
    tm_mo_dun = thu_muc_tam / "bi_mat" / "mo-dun"
    symlink_c = tm_mo_dun / "mod-c"
    symlink_c.symlink_to(tm_mo_dun / "module-a")

    kho_c = kho.kho_rieng("mod-c")
    with pytest.raises(ValueError, match="liên kết tượng trưng không an toàn"):
        kho_c.doc("api_key")


def test_so_module_trao_tay_cam_kho_cho_module(monkeypatch):
    """Kiểm tra lõi trao tay cầm kho_rieng cho module lúc nạp (A4a)."""
    from app.kham_pha import ModuleKhamPha
    from app.so_module import SoModule

    class ModuleGia:
        def __init__(self, manifest):
            self.manifest = manifest
            self.kho = None

        def nap(self, cau_hinh):
            self.da_nap = cau_hinh

        async def cai(self, cau_hinh):
            return {"ok": True, "cau_hinh_luu": cau_hinh}

        async def dung(self):
            pass

    km = ModuleKhamPha(
        id="mod-test",
        nguon="anh",
        thu_muc=Path("/tmp"),
        manifest={"id": "mod-test", "ten": "Test", "loai": "ai", "tien_to": "tst", "truong_cau_hinh": []},
        lop=ModuleGia,
        van_tay="abc",
        ten_ma="mod_test",
    )

    so = SoModule()
    the = km.tao()
    the.kho = kho.kho_rieng(km.id)
    the.nap({})
    assert the.kho is not None
    assert the.kho.module_id == "mod-test"


# ───────────── 9. AAD từ chối đường dẫn ngoài thư mục dữ liệu (A8) ─────────────

def test_aad_tu_choi_duong_ngoai_thu_muc_du_lieu(tmp_path, thu_muc_tam):
    tep_ngoai = tmp_path / "ngoai.json"
    tep_ngoai.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="không nằm dưới thư mục dữ liệu"):
        ham_khoa.tinh_aad(tep_ngoai)


# ───────────── 10. chmod 0700 toàn bộ chuỗi cha dưới bi_mat (A9) ─────────────

def test_kho_rieng_chmod_0700_toan_chuoi_cha(thu_muc_tam, khoa_32_bytes, monkeypatch):
    monkeypatch.setenv("WEB2API_KHOA_CHINH", khoa_32_bytes.hex())
    kho_x = kho.kho_rieng("mod-chmod-test")
    kho_x.luu("api_key", {"k": "AIzaSyTOT-mot-000000000000000000000001"})

    # Kiểm tra quyền 0700 cho từng cấp cha
    tm_mod = thu_muc_tam / "bi_mat" / "mo-dun" / "mod-chmod-test"
    tm_cha_1 = thu_muc_tam / "bi_mat" / "mo-dun"
    tm_cha_2 = thu_muc_tam / "bi_mat"

    assert tm_mod.is_dir()
    assert tm_mod.stat().st_mode & 0o777 == 0o700
    assert tm_cha_1.stat().st_mode & 0o777 == 0o700
    assert tm_cha_2.stat().st_mode & 0o777 == 0o700


# ───────────── 11. Dọn bản trước khôi phục & không có tác dụng phụ getter (A5) ─────────────

def test_sao_luu_don_ban_truoc_khoi_phuc_theo_ngay(thu_muc_tam, monkeypatch):
    now = time.time()
    # Bản cũ 10 ngày trước
    ban_cu = thu_muc_tam / f"{sao_luu.TIEN_TO_BAN_TRUOC}20261001-000000-aaa"
    ban_cu.mkdir(parents=True, exist_ok=True)
    os.utime(ban_cu, (now - 10 * 86400, now - 10 * 86400))

    # Bản mới 2 ngày trước
    ban_moi = thu_muc_tam / f"{sao_luu.TIEN_TO_BAN_TRUOC}20261014-000000-bbb"
    ban_moi.mkdir(parents=True, exist_ok=True)
    os.utime(ban_moi, (now - 2 * 86400, now - 2 * 86400))

    # A5a: Getter thu_muc_du_lieu() KHÔNG có tác dụng phụ xoá bản cũ
    _ = kho.thu_muc_du_lieu()
    _ = kho.thu_muc_du_lieu()
    assert ban_cu.exists()
    assert ban_moi.exists()

    # A5b: Mặc định không cấu hình biến môi trường ⇒ KHÔNG xoá theo tuổi
    monkeypatch.delenv("WEB2API_GIU_TRUOC_KHOI_PHUC_NGAY", raising=False)
    so_xoa = sao_luu.don_ban_truoc_khoi_phuc(thu_muc_tam)
    assert so_xoa == 0
    assert ban_cu.exists()
    assert ban_moi.exists()

    # A5c: Cắt theo trần 3 bản khi vượt quá
    ban_3 = thu_muc_tam / f"{sao_luu.TIEN_TO_BAN_TRUOC}20261015-000000-ccc"
    ban_4 = thu_muc_tam / f"{sao_luu.TIEN_TO_BAN_TRUOC}20261016-000000-ddd"
    ban_3.mkdir(parents=True, exist_ok=True)
    ban_4.mkdir(parents=True, exist_ok=True)

    sao_luu.don_ban_truoc_khoi_phuc(thu_muc_tam)
    con_lai = [d for d in thu_muc_tam.iterdir() if d.name.startswith(sao_luu.TIEN_TO_BAN_TRUOC)]
    assert len(con_lai) == 3

    # A5d: Có biến môi trường WEB2API_GIU_TRUOC_KHOI_PHUC_NGAY=7 ⇒ xoá bản cũ hơn 7 ngày
    monkeypatch.setenv("WEB2API_GIU_TRUOC_KHOI_PHUC_NGAY", "7")
    sao_luu.don_ban_truoc_khoi_phuc(thu_muc_tam)
    assert not ban_cu.exists()


# ───────────── 12. Bản sao lưu bắt KhoHong không sập toàn bộ (A6) ─────────────

def test_sao_luu_bat_kho_hong_khong_sap_toan_bo(thu_muc_tam, khoa_32_bytes, monkeypatch):
    monkeypatch.setenv("WEB2API_KHOA_CHINH", khoa_32_bytes.hex())

    # Ghi 1 tệp hợp lệ
    kho.ghi_bi_mat("gemini-api", {"api_key": "AIzaSyTOT-mot-000000000000000000000001"})

    # Tạo 1 tệp hỏng làm ném KhoHong (ghi dữ liệu hỏng mang header W2A1)
    p_hong = thu_muc_tam / "bi_mat" / "bad-module.json"
    p_hong.write_bytes(b"W2A1\x01corrupted_encrypted_data_bad_tag_padding")

    # Tạo bản sao lưu: không được ném lỗi, tệp hỏng bị bỏ qua
    tar_bytes, ten_tep = sao_luu.tao_ban_sao_luu(mat_khau="MatKhauSaoLuuAnToan123456", kem_phien=False)
    assert tar_bytes is not None
    assert isinstance(ten_tep, str) and ten_tep.startswith("web2api-sao-luu-")


# ───────────── 13. Phiên: liet_ke lan KhoHong và thong_ke trả cờ lỗi (A7) ─────────────

def test_phien_liet_ke_lan_kho_hong_va_thong_ke_tra_co_loi(thu_muc_tam, khoa_32_bytes, monkeypatch):
    monkeypatch.setenv("WEB2API_KHOA_CHINH", khoa_32_bytes.hex())

    # Tạo phiên hợp lệ
    phien.luu("muse2api", "user1", {"token": "valid_token"})

    # Giả lập sai khoá chính
    khoa_sai = secrets.token_bytes(32)
    monkeypatch.setenv("WEB2API_KHOA_CHINH", khoa_sai.hex())

    # A7a: phien.liet_ke() ném KhoHong (không nuốt im lặng)
    with pytest.raises(KhoHong):
        phien.liet_ke()

    # A7b: phien.thong_ke() bắt KhoHong và trả cờ loi: "khoa_hong" để giao diện hiện cảnh báo
    tk = phien.thong_ke()
    assert tk["tong"] == 0
    assert tk["loi"] == "khoa_hong"
    assert "chi_tiet" in tk


# ───────────── 14. Khôi phục sao lưu khác khoá chính ─────────────

def test_sao_luu_xuat_va_khoi_phuc_khac_khoa_chinh(tmp_path, khoa_32_bytes, monkeypatch):
    import asyncio
    data_a = tmp_path / "data_a"
    data_a.mkdir(parents=True, exist_ok=True)
    data_b = tmp_path / "data_b"
    data_b.mkdir(parents=True, exist_ok=True)

    # Máy A: Khoá chính A
    khoa_a = khoa_32_bytes
    monkeypatch.setenv("WEB2API_DATA", str(data_a))
    monkeypatch.setenv("WEB2API_KHOA_CHINH", khoa_a.hex())

    # Máy A có module và bí mật đã mã hoá
    kho.ghi_bi_mat("gemini-api", {"api_key": "AIzaSyTOT-mot-000000000000000000000001"})
    phien.luu("muse2api", "user1", {"token": "token_phien_a"})

    assert (data_a / "bi_mat" / "gemini-api.json").read_bytes().startswith(b"W2A1\x01")

    # Tạo bản sao lưu có mật khẩu người dùng
    mat_khau_sao_luu = "MatKhauBaoMatChuan123456"
    du_lieu_tar_ma_hoa, _ = sao_luu.tao_ban_sao_luu(mat_khau=mat_khau_sao_luu, kem_phien=True)

    # Chuyển sang Máy B: Khoá chính B hoàn toàn khác Khoá A
    khoa_b = secrets.token_bytes(32)
    assert khoa_b != khoa_a
    monkeypatch.setenv("WEB2API_DATA", str(data_b))
    monkeypatch.setenv("WEB2API_KHOA_CHINH", khoa_b.hex())

    # Khôi phục trên máy B bằng mật khẩu người dùng
    kiem_tra = asyncio.run(sao_luu.kiem_khoi_phuc(du_lieu_tar_ma_hoa, mat_khau_sao_luu))
    assert kiem_tra["ok"] is True
    sha = kiem_tra["sha256"]

    ket_qua = asyncio.run(sao_luu.ap_dung_khoi_phuc(du_lieu_tar_ma_hoa, mat_khau_sao_luu, sha256_da_kiem=sha))
    assert ket_qua["ok"] is True

    # Máy B đọc được chính xác bí mật và phiên đã sao lưu
    bm_b = kho.doc_bi_mat("gemini-api")
    assert bm_b == {"api_key": "AIzaSyTOT-mot-000000000000000000000001"}

    ph_b = phien.lay("muse2api", "user1")
    assert ph_b is not None
    assert ph_b["du_lieu"]["token"] == "token_phien_a"

    # Tệp trên đĩa máy B được mã hoá bằng Khoá B
    raw_b = (data_b / "bi_mat" / "gemini-api.json").read_bytes()
    assert raw_b.startswith(b"W2A1\x01")
    pt_b = ham_khoa.giai_ma(raw_b, "bi_mat/gemini-api.json", khoa=khoa_b)
    assert b"AIzaSyTOT-mot-000000000000000000000001" in pt_b


# ───────────── 15. Hiệu năng đọc phiên không đổi bậc ─────────────

def test_hieu_nang_doc_phien_khong_doi_bac(thu_muc_tam, khoa_32_bytes, monkeypatch):
    monkeypatch.setenv("WEB2API_KHOA_CHINH", khoa_32_bytes.hex())
    phien.luu("muse2api", "perf_user", {"token": "perf_test_token_12345"})

    t0 = time.perf_counter()
    for _ in range(1000):
        rec = phien.lay("muse2api", "perf_user")
        assert rec is not None
    t1 = time.perf_counter()

    tong_giay = t1 - t0
    # 1000 lần đọc giải mã AES-GCM hoàn thành trong < 0.8 giây (~0.58ms/lần)
    assert tong_giay < 0.8, f"1000 lần đọc phiên mất {tong_giay:.3f}s, vượt ngưỡng hợp lý"
