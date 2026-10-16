"""NV79 (NV67 nhóm 5, việc 11) — sao lưu gồm danh tính: bộ kiểm riêng cho bi_mat/danh_tinh.json.

Kịch bản đúng thiết kế đã chốt:
- bản sao lưu có mật khẩu chứa bi_mat/danh_tinh.json (danh tính đi theo bản sao lưu);
- bản sao lưu KHÔNG mật khẩu không chứa bất kỳ bí mật nào — chỉ cấu trúc;
- khôi phục bản có danh_tinh.json sai hình dạng (id sai khuôn, email sai, trùng email,
  trùng id, gan sai khuôn, không phải đối tượng, không phải JSON) → 400 `sao_luu_khong_hop_le`
  nêu tên tệp; thư mục dữ liệu KHÔNG đổi (kiểm hợp lệ trước khi ghi đè, không bao giờ ghi
  đè khi tệp hỏng);
- vòng tròn: tạo danh tính → sao lưu có mật khẩu → xoá kho → khôi phục → về y hệt.

Test chạy trên thư mục dữ liệu tạm, không mạng ngoài. Mỗi tệp test mới dưới 15 giây.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import struct
import tarfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from app import danh_tinh, kho, sao_luu
from app.loi_openai import LoiOpenAI
from app.sao_luu import MAGIC, PHIEN_BAN

MAT_KHAU_SAO_LUU = "MatKhauSaoLuu123456!"
BI_MAT_GIA = "bi-mat-cookie-that-xyz-123"


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    """Thư mục dữ liệu tạm riêng cho từng test."""
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(d))
    return d


def thanh_vien_tar(ban_sao: bytes) -> list[str]:
    """Liệt kê tên thành viên của bản sao lưu THÔ (không mã hoá)."""
    tar_bytes = ban_sao[len(MAGIC) + 2:]
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tf:
        return tf.getnames()


def _dung_tar_tho(tho: dict[str, bytes]) -> bytes:
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode="w") as tf:
        for ten, data in tho.items():
            ti = tarfile.TarInfo(name=ten)
            ti.size = len(data)
            ti.mtime = 0
            ti.mode = 0o600
            tf.addfile(ti, io.BytesIO(data))
    return MAGIC + bytes([PHIEN_BAN, 0]) + bio.getvalue()


def _dung_ban_sua_tu_ban_ma_hoa(ban_ma_hoa: bytes, ten_tep: str, du_lieu_moi: bytes) -> bytes:
    """Giải mã bản sao lưu có mật khẩu, thay một tệp trong tar, đóng lại thành bản THÔ
    (manifest sửa lại sha256 + kích thước). Dùng cho test khôi phục bản hỏng."""
    header = ban_ma_hoa[:51]
    n, r, p = struct.unpack(">III", ban_ma_hoa[11:23])
    khoa_ma = Scrypt(salt=ban_ma_hoa[23:39], length=32, n=n, r=r, p=p).derive(MAT_KHAU_SAO_LUU.encode("utf-8"))
    tar_bytes = AESGCM(khoa_ma).decrypt(ban_ma_hoa[39:51], ban_ma_hoa[51:], associated_data=header)
    tho: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tf:
        for m in tf:
            f = tf.extractfile(m)
            tho[m.name] = f.read() if f is not None else b""
    assert ten_tep in tho, f"bản gốc không có {ten_tep}"
    tho[ten_tep] = du_lieu_moi
    man = json.loads(tho["manifest.json"])
    for item in man["tep"]:
        if item["duong"] == ten_tep:
            item["sha256"] = hashlib.sha256(du_lieu_moi).hexdigest()
            item["kich_thuoc"] = len(du_lieu_moi)
    tho["manifest.json"] = json.dumps(man, ensure_ascii=False, indent=2).encode("utf-8")
    return _dung_tar_tho(tho)


def bam_thu_muc(thu_muc: Path) -> dict[str, str]:
    """SHA-256 mọi tệp trong thư mục dữ liệu (trừ nhật ký) — so sánh trước/sau khôi phục."""
    ra = {}
    for p in sorted(thu_muc.rglob("*")):
        if p.is_file() and "nhat_ky" not in p.parts:
            ra[p.relative_to(thu_muc).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return ra


def chay(coro):
    return asyncio.run(coro)


# ───────────── 1. Sao lưu không mật khẩu không lộ bí mật ─────────────

def test_sao_luu_khong_mat_khau_khong_chua_bi_mat(du_lieu):
    """Không mật khẩu → bản sao lưu KHÔNG chứa bi_mat/ (kể cả danh_tinh.json) và không chứa chuỗi bí mật."""
    danh_tinh.tao("ban@gmail.com", "Gmail cá nhân")
    kho.ghi_bi_mat("thu_nghiem", {"khoa": BI_MAT_GIA})

    ban, _ = sao_luu.tao_ban_sao_luu(None)
    thanh_vien = thanh_vien_tar(ban)
    assert not any(t == "bi_mat" or t.startswith("bi_mat/") for t in thanh_vien), \
        f"bản không mật khẩu vẫn chứa bí mật: {[t for t in thanh_vien if 'bi_mat' in t]}"
    assert BI_MAT_GIA.encode("utf-8") not in ban, "chuỗi bí mật lọt vào bản sao lưu không mật khẩu"
    assert b"ban@gmail.com" not in ban


# ───────────── 2. Sao lưu có mật khẩu chứa danh tính ─────────────

def test_sao_luu_co_mat_khau_chua_danh_tinh(du_lieu):
    """Có mật khẩu → bi_mat/danh_tinh.json nằm trong bản sao lưu và giải mã đọc được."""
    danh_tinh.tao("ban@gmail.com", "Gmail cá nhân")

    ban, _ = sao_luu.tao_ban_sao_luu(MAT_KHAU_SAO_LUU)
    manifest, noi_dung = chay(asyncio.to_thread(sao_luu.kiem_va_giai_ma, ban, MAT_KHAU_SAO_LUU))
    assert "bi_mat/danh_tinh.json" in noi_dung, "bản có mật khẩu thiếu danh_tinh.json"
    ds = noi_dung["bi_mat/danh_tinh.json"]["danh_tinh"]
    assert [d["email"] for d in ds] == ["ban@gmail.com"]


# ───────────── 3. danh_tinh.json sai hình dạng bị từ chối khi khôi phục ─────────────

CAC_BAN_HONG = [
    (b'{"danh_tinh": [{"id": "dt-khong-dung", "email": "ban@gmail.com", "nhan": "", '
     b'"ngay_tao": "2026-01-01T00:00:00+00:00", "gan": []}]}', "id sai khuôn"),
    (b'{"danh_tinh": [{"id": "dt-aaaaaaaaaaaaaaaa", "email": "khong-phai-email", "nhan": "", '
     b'"ngay_tao": "2026-01-01T00:00:00+00:00", "gan": []}]}', "email sai khuôn"),
    (b'{"danh_tinh": [{"id": "dt-aaaaaaaaaaaaaaaa", "email": "ban@gmail.com", "nhan": "", '
     b'"ngay_tao": "2026-01-01T00:00:00+00:00", "gan": []}, '
     b'{"id": "dt-bbbbbbbbbbbbbbbb", "email": "ban@gmail.com", "nhan": "", '
     b'"ngay_tao": "2026-01-01T00:00:00+00:00", "gan": []}]}', "trùng email"),
    (b'{"danh_tinh": [{"id": "dt-aaaaaaaaaaaaaaaa", "email": "a@gmail.com", "nhan": "", '
     b'"ngay_tao": "2026-01-01T00:00:00+00:00", "gan": []}, '
     b'{"id": "dt-aaaaaaaaaaaaaaaa", "email": "b@gmail.com", "nhan": "", '
     b'"ngay_tao": "2026-01-01T00:00:00+00:00", "gan": []}]}', "trùng id"),
    (b'{"danh_tinh": [{"id": "dt-aaaaaaaaaaaaaaaa", "email": "ban@gmail.com", "nhan": "", '
     b'"ngay_tao": "2026-01-01T00:00:00+00:00", "gan": ["mod khong hop le!"]}]}', "gan sai khuôn"),
    (b'["khong", "phai", "doi", "tuong"]', "gốc không phải đối tượng"),
    (b'khong-phai-json{{{', "không phải JSON"),
]


@pytest.mark.parametrize("noi_dung_xau,mo_ta", CAC_BAN_HONG)
def test_danh_tinh_sai_hinh_dang_bi_tu_choi(du_lieu, noi_dung_xau, mo_ta):
    """Khôi phục bản có bi_mat/danh_tinh.json sai hình dạng → 400 sao_luu_khong_hop_le nêu tên tệp;
    thư mục dữ liệu KHÔNG đổi (kiểm hợp lệ trước khi ghi đè, không bao giờ ghi đè khi tệp hỏng)."""
    danh_tinh.tao("cu@gmail.com", "Tài khoản cũ")
    ban_ma_hoa, _ = sao_luu.tao_ban_sao_luu(MAT_KHAU_SAO_LUU)
    ban_sua = _dung_ban_sua_tu_ban_ma_hoa(ban_ma_hoa, "bi_mat/danh_tinh.json", noi_dung_xau)
    truoc = bam_thu_muc(du_lieu)

    with pytest.raises(LoiOpenAI) as exc:
        chay(sao_luu.kiem_khoi_phuc(ban_sua, None))
    assert exc.value.ma == 400 and exc.value.code == "sao_luu_khong_hop_le", f"{mo_ta}: {exc.value}"
    assert "bi_mat/danh_tinh.json" in str(exc.value), f"{mo_ta}: thông báo không nêu tên tệp"

    # ap_dung cũng phải từ chối TRƯỚC khi ghi — dữ liệu cũ nguyên vẹn
    sha = hashlib.sha256(ban_sua).hexdigest()
    with pytest.raises(LoiOpenAI):
        chay(sao_luu.ap_dung_khoi_phuc(ban_sua, None, sha256_da_kiem=sha))
    assert bam_thu_muc(du_lieu) == truoc, "khôi phục từ bản hỏng đã làm đổi thư mục dữ liệu"
    assert [d["email"] for d in danh_tinh.danh_sach()] == ["cu@gmail.com"]


# ───────────── 4. Vòng tròn danh tính ─────────────

def test_vong_tron_danh_tinh(du_lieu):
    """Tạo danh tính → sao lưu có mật khẩu → xoá kho → khôi phục → danh tính về y hệt."""
    ban_ghi = danh_tinh.tao("ban@gmail.com", "Gmail cá nhân")
    ban, _ = sao_luu.tao_ban_sao_luu(MAT_KHAU_SAO_LUU)

    (du_lieu / "bi_mat" / "danh_tinh.json").unlink()
    assert danh_tinh.danh_sach() == []

    kiem = chay(sao_luu.kiem_khoi_phuc(ban, MAT_KHAU_SAO_LUU))
    assert kiem["co_the_ap_dung"] is True
    assert "bi_mat/danh_tinh.json" in kiem["se_ap_dung"]
    ket_qua = chay(sao_luu.ap_dung_khoi_phuc(ban, MAT_KHAU_SAO_LUU, sha256_da_kiem=kiem["sha256"]))
    assert ket_qua["ok"] is True

    ds = danh_tinh.danh_sach()
    assert len(ds) == 1 and ds[0]["id"] == ban_ghi["id"] and ds[0]["email"] == "ban@gmail.com"
