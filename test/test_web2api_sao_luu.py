"""Kiểm thử tính năng Sao lưu và khôi phục cấu hình web2api.

Bao gồm:
- Vòng tròn sao lưu → xoá → khôi phục → cấu hình y hệt và khoá khách CŨ vẫn xác thực được.
- Không mật khẩu → bản sao KHÔNG chứa bi_mat/ lẫn phiên tài khoản.
- Có mật khẩu → chứa bi_mat/; kem_phien=True mới chứa phiên.
- Sai mật khẩu / một byte bị sửa / đầu tệp sai / cắt cụt → 400 sao_luu_khong_hop_le, thư mục dữ liệu KHÔNG đổi.
- Thành viên tar độc: ../x, đường tuyệt đối, symlink, hardlink, tên lạ trong bi_mat/, tệp ngoài danh sách → từ chối cả bản.
- Bom nén (nhiều thành viên / quá dung lượng) → từ chối.
- Chế độ kiem: chỉ kiểm tra, KHÔNG ghi gì lên đĩa.
- Khoá quản trị KHÔNG đổi sau khôi phục.
- Kiểm leo thang quyền: khách 403, không khoá 401.
- Mật khẩu và giá trị bí mật KHÔNG lọt vào phản hồi, log, nhật ký.
- Mật khẩu < 12 ký tự → 400.
- Ghi nguyên tử và xoay vòng tối đa 3 bản .truoc-khoi-phuc-*.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import struct
import tarfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import kho, khoa_khach, nhat_ky, sao_luu, xac_thuc
from app.main import app
from app.sao_luu import (
    KIEU_THO,
    MAGIC,
    PHIEN_BAN,
    TRAN_SO_THANH_VIEN,
)
from app.so_module import SoModule

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
MAT_KHAU_CHUAN = "MatKhauSaoLuu123456!"
KHOA_API_GIA = "AIzaSyUI-TOT-0000000000000000000000tot1"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    khoa_khach.lam_moi_cache()

    with TestClient(app) as client:
        yield client, client.app.state.so, data_dir


def tinh_bam_thu_muc(thu_muc: Path) -> dict[str, str]:
    """Tính SHA-256 của các tệp cấu hình/bí mật trong thư mục dữ liệu (loại trừ nhat_ky)."""
    bams = {}
    if not thu_muc.exists():
        return bams
    for p in sorted(thu_muc.rglob("*")):
        if p.is_file() and not p.name.startswith(".tam") and "nhat_ky" not in p.parts:
            rel = p.relative_to(thu_muc).as_posix()
            bams[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return bams


# ───────────── 1. Kiểm tra leo thang quyền & mật khẩu ngắn ─────────────

def test_xac_thuc_leo_thang_quyen(moi_truong):
    """Mọi đường mới đều có xác thực: không khoá → 401; khoá khách → 403."""
    client, _, _ = moi_truong

    # 1.1 /api/sao-luu không khoá → 401
    r = client.post("/api/sao-luu", json={"mat_khau": MAT_KHAU_CHUAN})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_api_key"

    # 1.2 /api/khoi-phuc không khoá → 401
    r = client.post(
        "/api/khoi-phuc",
        files={"tep": ("backup.w2a", b"racc", "application/octet-stream")},
    )
    assert r.status_code == 401

    # Tạo một khoá khách
    ban_ghi, khoa_tho = khoa_khach.tao_khoa("Khách thường", ["*"])
    khach_hd = {"Authorization": f"Bearer {khoa_tho}"}

    # 1.3 Khoá khách gọi /api/sao-luu → 403
    r = client.post("/api/sao-luu", headers=khach_hd, json={"mat_khau": MAT_KHAU_CHUAN})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"

    # 1.4 Khoá khách gọi /api/khoi-phuc → 403
    r = client.post(
        "/api/khoi-phuc",
        headers=khach_hd,
        files={"tep": ("backup.w2a", b"racc", "application/octet-stream")},
    )
    assert r.status_code == 403


def test_mat_khau_ngan_va_sai_kieu(moi_truong):
    """Mật khẩu < 12 ký tự hoặc sai kiểu → 400 mat_khau_ngan."""
    client, _, _ = moi_truong

    # Mật khẩu 11 ký tự (< 12)
    r = client.post("/api/sao-luu", headers=QT, json={"mat_khau": "12345678901"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "mat_khau_ngan"

    # Mật khẩu kiểu số
    r = client.post("/api/sao-luu", headers=QT, json={"mat_khau": 1234567890123})
    assert r.status_code == 400


# ───────────── 2. Vòng tròn sao lưu & khôi phục giữ nguyên khoá khách ─────────────

def test_vong_tron_sao_luu_khoi_phuc_giu_khoa_khach(moi_truong):
    """Vòng tròn sao lưu → xoá → khôi phục: cấu hình y hệt và khoá khách CŨ vẫn dùng được."""
    client, so, data_dir = moi_truong

    # Chuẩn bị dữ liệu ban đầu
    modules_cfg = {"modules": [{"id": "mock-mod", "cai_luc": "2026-10-13T00:00:00Z", "cau_hinh_cong_khai": {"mo_hinh": "test"}}]}
    (data_dir / "modules.json").write_text(json.dumps(modules_cfg), encoding="utf-8")
    (data_dir / "dia_chi.json").write_text(json.dumps({"url": "http://127.0.0.1:3031"}), encoding="utf-8")

    # Tạo bí mật
    bm_dir = data_dir / "bi_mat"
    bm_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(bm_dir, 0o700)
    (bm_dir / "mock-mod.json").write_text(json.dumps({"api_key": KHOA_API_GIA}), encoding="utf-8")
    os.chmod(bm_dir / "mock-mod.json", 0o600)

    # Tạo phiên tài khoản
    phien_dir = bm_dir / "phien" / "mock-mod"
    phien_dir.mkdir(parents=True, exist_ok=True)
    (phien_dir / "tai_khoan_1.json").write_text(json.dumps({"tai_khoan": "tk1", "du_lieu": {"session": "abc"}}), encoding="utf-8")

    # Tạo khoá khách
    ban_ghi, khoa_tho_cu = khoa_khach.tao_khoa("Client App Cũ", ["chat"])
    assert khoa_khach.tim_theo_khoa_tho(khoa_tho_cu) is not None

    # Module git giả
    mod_git_dir = data_dir / "modules" / "mock-mod"
    mod_git_dir.mkdir(parents=True, exist_ok=True)
    (mod_git_dir / ".nguon.json").write_text(json.dumps({"repo": "owner/repo", "sha_commit": "1234567890abcdef1234567890abcdef12345678"}), encoding="utf-8")

    # Bước 1: Sao lưu có mật khẩu và kèm phiên
    r_sao_luu = client.post("/api/sao-luu", headers=QT, json={"mat_khau": MAT_KHAU_CHUAN, "kem_phien": True})
    assert r_sao_luu.status_code == 200
    assert r_sao_luu.headers["content-type"] == "application/octet-stream"
    assert "web2api-sao-luu-" in r_sao_luu.headers["content-disposition"]
    tep_sao_luu = r_sao_luu.content
    assert len(tep_sao_luu) > 100

    # Bước 2: Xoá sạch cấu hình và khoá
    (data_dir / "modules.json").unlink()
    (data_dir / "dia_chi.json").unlink()
    shutil.rmtree(bm_dir)
    (data_dir / "khoa_khach.json").unlink()
    khoa_khach.lam_moi_cache()

    # Chứng minh khoá khách cũ đã mất hiệu lực khi bị xoá
    assert khoa_khach.tim_theo_khoa_tho(khoa_tho_cu) is None

    # Bước 3: Khôi phục lại ở chế độ "ap_dung"
    r_khoi_phuc = client.post(
        "/api/khoi-phuc",
        headers=QT,
        data={"mat_khau": MAT_KHAU_CHUAN, "che_do": "ap_dung", "sha256": hashlib.sha256(tep_sao_luu).hexdigest()},
        files={"tep": ("backup.w2a", tep_sao_luu, "application/octet-stream")},
    )
    assert r_khoi_phuc.status_code == 200
    res_kp = r_khoi_phuc.json()
    assert res_kp["ok"] is True
    assert "modules.json" in res_kp["da_ap_dung"]
    assert "khoa_khach.json" in res_kp["da_ap_dung"]
    assert "bi_mat/mock-mod.json" in res_kp["da_ap_dung"]
    assert res_kp["bi_mat"] == "đã khôi phục"
    assert set(res_kp["nap_lai"]) >= {"them", "doi", "go", "loi"}  # sổ module thật đã nạp lại

    # Bước 4: Kiểm tra đĩa và quyền tệp
    assert (data_dir / "modules.json").is_file()
    assert (data_dir / "dia_chi.json").is_file()
    assert (data_dir / "bi_mat" / "mock-mod.json").is_file()
    assert (data_dir / "bi_mat" / "phien" / "mock-mod" / "tai_khoan_1.json").is_file()

    # Quyền bí mật phải là 0600
    stat_bm = (data_dir / "bi_mat" / "mock-mod.json").stat().st_mode & 0o777
    assert stat_bm == 0o600
    stat_kk = (data_dir / "khoa_khach.json").stat().st_mode & 0o777
    assert stat_kk == 0o600

    # Bước 5: BÀI HỌC C2A - Khoá khách CŨ VẪN XÁC THỰC ĐƯỢC NGAY LẬP TỨC!
    khach_tim = khoa_khach.tim_theo_khoa_tho(khoa_tho_cu)
    assert khach_tim is not None
    assert khach_tim["nhan"] == "Client App Cũ"
    assert khach_tim["pham_vi"] == ["chat"]


# ───────────── 3. Không mật khẩu vs Có mật khẩu ─────────────

def test_sao_luu_khong_mat_khau_khong_chua_bi_mat(moi_truong):
    """Không có mật khẩu → bản sao lưu KHÔNG chứa bi_mat/ lẫn phiên."""
    client, _, data_dir = moi_truong

    (data_dir / "modules.json").write_text(json.dumps({"modules": []}), encoding="utf-8")
    bm_dir = data_dir / "bi_mat"
    bm_dir.mkdir(parents=True, exist_ok=True)
    (bm_dir / "mock-mod.json").write_text(json.dumps({"api_key": KHOA_API_GIA}), encoding="utf-8")

    # Sao lưu không mật khẩu
    r = client.post("/api/sao-luu", headers=QT, json={"mat_khau": None, "kem_phien": False})
    assert r.status_code == 200
    tep_data = r.content

    # Tách tar từ tệp không mã hoá (bỏ header 11 byte)
    assert tep_data[:9] == MAGIC
    assert tep_data[9] == PHIEN_BAN
    assert tep_data[10] == KIEU_THO
    tar_bytes = tep_data[11:]

    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tf:
        names = tf.getnames()
        assert "manifest.json" in names
        assert "modules.json" in names
        # Tuyệt đối không chứa bi_mat
        assert not any(n.startswith("bi_mat") for n in names)

        # Kiểm tra manifest.json nêu rõ
        mf = json.loads(tf.extractfile("manifest.json").read().decode("utf-8"))
        assert mf["co_bi_mat"] is False
        assert mf["ghi_chu"] == "đã bỏ bí mật vì không có mật khẩu"


def test_kem_phien_mac_dinh_tat(moi_truong):
    """kem_phien mặc định là false: có mật khẩu nhưng kem_phien=False thì không đưa phiên vào."""
    client, _, data_dir = moi_truong

    (data_dir / "modules.json").write_text(json.dumps({"modules": []}), encoding="utf-8")
    bm_dir = data_dir / "bi_mat"
    bm_dir.mkdir(parents=True, exist_ok=True)
    (bm_dir / "mock-mod.json").write_text(json.dumps({"api_key": KHOA_API_GIA}), encoding="utf-8")

    phien_dir = bm_dir / "phien" / "mock-mod"
    phien_dir.mkdir(parents=True, exist_ok=True)
    (phien_dir / "tk1.json").write_text(json.dumps({"tai_khoan": "tk1"}), encoding="utf-8")

    # Mặc định không gửi kem_phien
    r = client.post("/api/sao-luu", headers=QT, json={"mat_khau": MAT_KHAU_CHUAN})
    assert r.status_code == 200
    tep_data = r.content

    # Kiểm tra giải mã
    mf, noi_dung = sao_luu.kiem_va_giai_ma(tep_data, MAT_KHAU_CHUAN)
    assert "bi_mat/mock-mod.json" in noi_dung
    assert not any("phien" in k for k in noi_dung)
    assert mf["co_phien"] is False


# ───────────── 4. Thử thách giải mã, sai mật khẩu, dữ liệu bị sửa ─────────────

def test_sai_mat_khau_va_sua_byte_khong_doi_du_lieu(moi_truong):
    """Sai mật khẩu / 1 byte bị sửa / cắt cụt → 400 sao_luu_khong_hop_le; đĩa giữ nguyên băm."""
    client, _, data_dir = moi_truong

    (data_dir / "modules.json").write_text(json.dumps({"modules": [{"id": "goc"}]}), encoding="utf-8")
    bam_truoc = tinh_bam_thu_muc(data_dir)

    # Tạo bản sao lưu hợp lệ
    r = client.post("/api/sao-luu", headers=QT, json={"mat_khau": MAT_KHAU_CHUAN})
    assert r.status_code == 200
    tep_chuan = bytearray(r.content)

    # 4.1 Sai mật khẩu
    r = client.post(
        "/api/khoi-phuc",
        headers=QT,
        data={"mat_khau": "MatKhauSaiHoanToan123!", "che_do": "ap_dung", "sha256": hashlib.sha256(bytes(tep_chuan)).hexdigest()},
        files={"tep": ("backup.w2a", bytes(tep_chuan), "application/octet-stream")},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "sao_luu_khong_hop_le"
    assert tinh_bam_thu_muc(data_dir) == bam_truoc

    # 4.2 Sửa đúng 1 byte trong phần ciphertext
    tep_sua = bytearray(tep_chuan)
    tep_sua[-5] = (tep_sua[-5] + 1) % 256
    r = client.post(
        "/api/khoi-phuc",
        headers=QT,
        data={"mat_khau": MAT_KHAU_CHUAN, "che_do": "ap_dung", "sha256": hashlib.sha256(bytes(tep_sua)).hexdigest()},
        files={"tep": ("backup.w2a", bytes(tep_sua), "application/octet-stream")},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "sao_luu_khong_hop_le"
    assert tinh_bam_thu_muc(data_dir) == bam_truoc

    # 4.3 Cắt cụt tệp (bị thiếu dữ liệu)
    tep_cut = bytes(tep_chuan[:len(tep_chuan) - 20])
    r = client.post(
        "/api/khoi-phuc",
        headers=QT,
        data={"mat_khau": MAT_KHAU_CHUAN, "che_do": "ap_dung", "sha256": hashlib.sha256(tep_cut).hexdigest()},
        files={"tep": ("backup.w2a", tep_cut, "application/octet-stream")},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "sao_luu_khong_hop_le"
    assert tinh_bam_thu_muc(data_dir) == bam_truoc

    # 4.4 Đầu tệp sai magic bytes
    tep_sai_magic = bytearray(tep_chuan)
    tep_sai_magic[:4] = b"FAKE"
    r = client.post(
        "/api/khoi-phuc",
        headers=QT,
        data={"mat_khau": MAT_KHAU_CHUAN, "che_do": "ap_dung", "sha256": hashlib.sha256(bytes(tep_sai_magic)).hexdigest()},
        files={"tep": ("backup.w2a", bytes(tep_sai_magic), "application/octet-stream")},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "sao_luu_khong_hop_le"
    assert tinh_bam_thu_muc(data_dir) == bam_truoc


# ───────────── 5. Chống thành viên tar độc (Tar Slip, ngoài allowlist) ─────────────

def tao_tar_gia(thanh_vien: list[tuple[str, bytes, int | None]], co_manifest: bool = True) -> bytes:
    """Helper tạo tệp tar theo danh sách (tên, nội dung, type)."""
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode="w") as tf:
        manifest_entries = []
        for ten, data, loai in thanh_vien:
            ti = tarfile.TarInfo(name=ten)
            ti.size = len(data)
            ti.mtime = int(time.time())
            ti.type = tarfile.REGTYPE if loai is None else loai
            ti.mode = 0o644
            if loai == tarfile.SYMTYPE:
                ti.linkname = "modules.json"
            tf.addfile(ti, io.BytesIO(data))
            if ten != "manifest.json":
                manifest_entries.append({
                    "duong": ten,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "kich_thuoc": len(data),
                })
        if co_manifest:
            mf = {
                "phien_ban": 1,
                "tao_luc": "2026-10-13T00:00:00Z",
                "tep": manifest_entries,
                "modules_git": [],
                "co_bi_mat": False,
                "co_phien": False,
            }
            mf_bytes = json.dumps(mf).encode("utf-8")
            ti = tarfile.TarInfo(name="manifest.json")
            ti.size = len(mf_bytes)
            ti.mtime = int(time.time())
            ti.type = tarfile.REGTYPE
            tf.addfile(ti, io.BytesIO(mf_bytes))
    return MAGIC + bytes([PHIEN_BAN, KIEU_THO]) + bio.getvalue()


def test_tu_choi_thanh_vien_doc_va_ngoai_allowlist(moi_truong):
    """Tar Slip (../, tuyệt đối), symlink, hardlink, admin_key, tên lạ trong bi_mat/ → từ chối."""
    client, _, data_dir = moi_truong
    bam_truoc = tinh_bam_thu_muc(data_dir)

    cac_truong_hop = [
        # ../ thoát thư mục
        [("../thoat.json", b"{}", None)],
        # Đường tuyệt đối
        [("/etc/passwd", b"root:x:0:0", None)],
        # Tệp admin_key (không được phép trong sao lưu)
        [("admin_key", b"fake_admin_key", None)],
        # Tệp lạ ngoài danh sách
        [("malicious.sh", b"echo 1", None)],
        # Symlink
        [("symlink.json", b"", tarfile.SYMTYPE)],
        # Tên lạ trong bi_mat/ (vi phạm regex _TEN_BI_MAT)
        [("bi_mat/ten-co-dau-cach .json", b"{}", None)],
        [("bi_mat/../../root.json", b"{}", None)],
    ]

    for th in cac_truong_hop:
        tar_doc = tao_tar_gia(th)
        r = client.post(
            "/api/khoi-phuc",
            headers=QT,
            data={"che_do": "ap_dung", "sha256": hashlib.sha256(tar_doc).hexdigest()},
            files={"tep": ("backup.w2a", tar_doc, "application/octet-stream")},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "sao_luu_khong_hop_le"
        assert tinh_bam_thu_muc(data_dir) == bam_truoc


def test_bom_nen_qua_so_thanh_vien(moi_truong):
    """Bom nén: nhiều hơn TRAN_SO_THANH_VIEN thành viên → từ chối cả bản."""
    client, _, data_dir = moi_truong
    th = [("modules.json", b'{"modules":[]}', None)]
    for i in range(TRAN_SO_THANH_VIEN + 2):
        th.append((f"bi_mat/m{i}.json", b"{}", None))

    tar_bom = tao_tar_gia(th)
    r = client.post(
        "/api/khoi-phuc",
        headers=QT,
        data={"che_do": "ap_dung", "sha256": hashlib.sha256(tar_bom).hexdigest()},
        files={"tep": ("backup.w2a", tar_bom, "application/octet-stream")},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "sao_luu_khong_hop_le"


# ───────────── 6. Chế độ kiem và khoá quản trị không đổi ─────────────

def test_che_do_kiem_khong_ghi_gi(moi_truong):
    """Chế độ kiem: chỉ giải mã, kiểm tra và liệt kê sẽ đổi gì; KHÔNG ghi bất cứ gì lên đĩa."""
    client, _, data_dir = moi_truong

    (data_dir / "modules.json").write_text(json.dumps({"modules": []}), encoding="utf-8")
    bm_dir = data_dir / "bi_mat"
    bm_dir.mkdir(parents=True, exist_ok=True)
    (bm_dir / "mod1.json").write_text(json.dumps({"api_key": KHOA_API_GIA}), encoding="utf-8")

    r_sao = client.post("/api/sao-luu", headers=QT, json={"mat_khau": MAT_KHAU_CHUAN})
    tep_sao = r_sao.content

    # Xoá bớt một file trên đĩa
    (bm_dir / "mod1.json").unlink()
    bam_truoc = tinh_bam_thu_muc(data_dir)

    # Khôi phục ở chế độ kiem (mặc định)
    r_kiem = client.post(
        "/api/khoi-phuc",
        headers=QT,
        data={"mat_khau": MAT_KHAU_CHUAN, "che_do": "kiem"},
        files={"tep": ("backup.w2a", tep_sao, "application/octet-stream")},
    )
    assert r_kiem.status_code == 200
    res = r_kiem.json()
    assert res["ok"] is True
    assert res["che_do"] == "kiem"
    assert "bi_mat/mod1.json" in res["se_ap_dung"]
    assert res["bi_mat"] == "có trong bản sao lưu"

    # Đĩa KHÔNG thay đổi gì
    assert tinh_bam_thu_muc(data_dir) == bam_truoc
    assert not (bm_dir / "mod1.json").exists()


def test_khoa_quan_tri_hien_tai_khong_doi(moi_truong):
    """Khoá quản trị hiện tại KHÔNG bị đổi sau khi khôi phục."""
    client, _, data_dir = moi_truong

    p_admin = data_dir / "admin_key"
    p_admin.write_text("ADMIN_KEY_GOC_1234567890\n", encoding="utf-8")

    (data_dir / "modules.json").write_text(json.dumps({"modules": []}), encoding="utf-8")
    r_sao = client.post("/api/sao-luu", headers=QT, json={"mat_khau": None})
    tep_sao = r_sao.content

    # Áp dụng khôi phục
    r_ap_dung = client.post(
        "/api/khoi-phuc",
        headers=QT,
        data={"che_do": "ap_dung", "sha256": hashlib.sha256(tep_sao).hexdigest()},
        files={"tep": ("backup.w2a", tep_sao, "application/octet-stream")},
    )
    assert r_ap_dung.status_code == 200

    # Tệp admin_key vẫn giữ nguyên giá trị ban đầu
    assert p_admin.read_text(encoding="utf-8") == "ADMIN_KEY_GOC_1234567890\n"


# ───────────── 7. Quét rò rỉ bí mật vào log / nhật ký / phản hồi ─────────────

def test_khong_lot_mat_khau_va_bi_mat_ra_phan_hoi_va_nhat_ky(moi_truong):
    """Mật khẩu và khoá API bí mật KHÔNG bao giờ lọt vào log, nhật ký, hay phản hồi."""
    client, _, data_dir = moi_truong

    (data_dir / "modules.json").write_text(json.dumps({"modules": []}), encoding="utf-8")
    bm_dir = data_dir / "bi_mat"
    bm_dir.mkdir(parents=True, exist_ok=True)
    (bm_dir / "mod1.json").write_text(json.dumps({"api_key": KHOA_API_GIA}), encoding="utf-8")

    # 7.1 Sao lưu
    r_sao = client.post("/api/sao-luu", headers=QT, json={"mat_khau": MAT_KHAU_CHUAN})
    tep_sao = r_sao.content

    # 7.2 Khôi phục
    r_kp = client.post(
        "/api/khoi-phuc",
        headers=QT,
        data={"mat_khau": MAT_KHAU_CHUAN, "che_do": "ap_dung", "sha256": hashlib.sha256(tep_sao).hexdigest()},
        files={"tep": ("backup.w2a", tep_sao, "application/octet-stream")},
    )
    phan_hoi_chuoi = r_kp.text

    # Kiểm tra phản hồi
    assert MAT_KHAU_CHUAN not in phan_hoi_chuoi
    assert KHOA_API_GIA not in phan_hoi_chuoi

    # Kiểm tra nhật ký quản trị
    nhat_ky_p = data_dir / "nhat_ky.json"
    if nhat_ky_p.exists():
        noi_dung_nk = nhat_ky_p.read_text(encoding="utf-8")
        assert MAT_KHAU_CHUAN not in noi_dung_nk
        assert KHOA_API_GIA not in noi_dung_nk


# ───────────── 8. Ghi nguyên tử & xoay vòng bản sao lưu trước ─────────────

def test_xoay_vong_ban_sao_luu_truoc(moi_truong):
    """Lưu trước sang .truoc-khoi-phuc-<thời điểm>/, xoay vòng tối đa 3 bản."""
    client, _, data_dir = moi_truong

    (data_dir / "modules.json").write_text(json.dumps({"modules": []}), encoding="utf-8")
    r_sao = client.post("/api/sao-luu", headers=QT, json={"mat_khau": None})
    tep_sao = r_sao.content

    # Gọi áp dụng 5 lần liên tiếp
    for _ in range(5):
        time.sleep(0.01)
        r = client.post(
            "/api/khoi-phuc",
            headers=QT,
            data={"che_do": "ap_dung", "sha256": hashlib.sha256(tep_sao).hexdigest()},
            files={"tep": ("backup.w2a", tep_sao, "application/octet-stream")},
        )
        assert r.status_code == 200

    # Đếm số thư mục .truoc-khoi-phuc-*
    ds_truoc = [d for d in data_dir.iterdir() if d.is_dir() and d.name.startswith(".truoc-khoi-phuc-")]
    assert len(ds_truoc) <= 3


def test_bom_nen_qua_dung_luong(moi_truong, monkeypatch):
    """Bom nén: tổng kích thước giải nén vượt trần → từ chối 400."""
    client, _, data_dir = moi_truong
    # Giảm trần tổng để test nhanh (trần thật 16 MiB)
    monkeypatch.setattr(sao_luu, "TRAN_TONG", 1024 * 1024)  # 1 MiB

    du_lieu_lon = b"a" * (2 * 1024 * 1024)  # 2 MiB
    tar_bom = tao_tar_gia([("modules.json", du_lieu_lon, None)])
    r = client.post(
        "/api/khoi-phuc",
        headers=QT,
        data={"che_do": "ap_dung", "sha256": hashlib.sha256(tar_bom).hexdigest()},
        files={"tep": ("backup.w2a", tar_bom, "application/octet-stream")},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "sao_luu_khong_hop_le"


def test_multipart_thieu_hoac_thua_tep(moi_truong):
    """Multipart thiếu hoặc gửi thừa tệp trong trường tep → từ chối."""
    client, _, _ = moi_truong

    # Thiếu tệp nhưng là multipart
    r = client.post(
        "/api/khoi-phuc",
        headers=QT,
        data={"che_do": "kiem"},
        files={"khong_phai_tep": ("a.txt", b"abc", "text/plain")},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "sao_luu_khong_hop_le"

    # Không phải multipart → 415
    r = client.post("/api/khoi-phuc", headers=QT, data={"che_do": "kiem"})
    assert r.status_code == 415

    # Thừa tệp (2 tệp trong trường tep)
    r = client.post(
        "/api/khoi-phuc",
        headers=QT,
        data={"che_do": "kiem"},
        files=[
            ("tep", ("t1.w2a", b"1", "application/octet-stream")),
            ("tep", ("t2.w2a", b"2", "application/octet-stream")),
        ],
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "sao_luu_khong_hop_le"


def test_ghi_nguyen_tu_loi_giua_chung_khong_de_tep_tam(moi_truong, monkeypatch):
    """Mô phỏng lỗi giữa chừng khi ghi tệp (os.replace lỗi MỘT lần) → 500 «đã hoàn tác», không để lại tệp tạm .tam.
    (Trạng thái sau lỗi = trước, từng tệp: test_pocb_… ở test_web2api_sao_luu_an_toan.py.)"""
    client, _, data_dir = moi_truong

    (data_dir / "modules.json").write_text(json.dumps({"modules": []}), encoding="utf-8")
    r_sao = client.post("/api/sao-luu", headers=QT, json={"mat_khau": None})
    tep_sao = r_sao.content

    goc_replace = os.replace

    da_hong = []

    def replace_loi(src, dst):
        if "modules.json" in str(dst) and ".truoc-khoi-phuc-" not in str(dst) and not da_hong:
            da_hong.append(dst)
            raise OSError("Mô phỏng lỗi đĩa hoặc mất quyền khi replace")
        return goc_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace_loi)

    r = client.post(
        "/api/khoi-phuc",
        headers=QT,
        data={"che_do": "ap_dung", "sha256": hashlib.sha256(tep_sao).hexdigest()},
        files={"tep": ("backup.w2a", tep_sao, "application/octet-stream")},
    )
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "khoi_phuc_da_hoan_tac"
    assert da_hong

    # Đảm bảo không còn tệp .tam nào còn sót lại
    tep_tam = list(data_dir.rglob("*.tam"))
    assert len(tep_tam) == 0
