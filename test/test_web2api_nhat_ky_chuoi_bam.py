"""Kiểm thử chuỗi băm chống sửa/xoá lén nhật ký (NV8, cẩm nang §20).

Mỗi bản ghi mới có `truoc` (băm bản ghi liền trước, hoặc "GOC") và `bam` = SHA-256 của
(truoc + dạng chuẩn hoá của bản ghi không có `bam`). `kiem_tra_chuoi()` quét phát hiện
sửa/xoá/đổi chỗ/chèn lén.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import nhat_ky
from app.main import app

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


@pytest.fixture
def nhat_ky_sach(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_NHAT_KY_CHUOI_BAM", "1")
    nhat_ky._CHUOI.clear()
    nhat_ky._DEM.clear()
    nhat_ky._last_luc = ""
    yield tmp_path
    nhat_ky._CHUOI.clear()
    nhat_ky._DEM.clear()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_NHAT_KY_CHUOI_BAM", "1")
    nhat_ky._CHUOI.clear()
    nhat_ky._DEM.clear()
    with TestClient(app) as c:
        yield c
    nhat_ky._CHUOI.clear()
    nhat_ky._DEM.clear()


def _doc_tat_ca_cu_den_moi() -> list[dict]:
    """Đọc toàn bộ bản ghi từ cũ đến mới (xuyên tệp xoay)."""
    recs = []
    tep = list(reversed(nhat_ky._cac_tep_doc()))
    for p in tep:
        if not p.is_file():
            continue
        for raw in p.read_bytes().split(b"\n"):
            if raw.strip():
                recs.append(json.loads(raw))
    return recs


def _ghi_tep_truc_tiep(p: Path, recs: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ───────────── chuỗi cơ bản ─────────────

def test_chuoi_lien_tuc_qua_nhieu_lan_ghi(nhat_ky_sach):
    for i in range(10):
        nhat_ky.ghi("he_thong", "thong_tin", f"bản ghi {i}")
    recs = _doc_tat_ca_cu_den_moi()
    assert len(recs) == 10
    assert recs[0]["truoc"] == "GOC"
    for truoc_rec, sau_rec in zip(recs, recs[1:]):
        assert sau_rec["truoc"] == truoc_rec["bam"]
    kq = nhat_ky.kiem_tra_chuoi()
    assert kq["ok"] is True
    assert kq["so_ban_ghi"] == 10
    assert kq["so_ban_ghi_cu"] == 0
    assert kq["loi"] == []
    assert kq["neo_dau"] == "GOC"
    assert kq["dau_cuoi"] == recs[-1]["bam"]


def test_chuoi_xuyen_xoay_vong(nhat_ky_sach, monkeypatch):
    monkeypatch.setenv("WEB2API_NHAT_KY_TOI_DA_MB", "0.005")
    for i in range(100):
        nhat_ky.ghi("cuoc_goi", "thong_tin", f"dòng {i} " + "x" * 100, so=i)
    tep = list(nhat_ky.thu_muc_nhat_ky().glob("nhat_ky*.jsonl"))
    assert len(tep) > 1  # đã xoay vòng thật
    kq = nhat_ky.kiem_tra_chuoi()
    assert kq["ok"] is True
    assert kq["loi"] == []
    # nối xuyên tệp: bản ghi đầu tệp mới có truoc = bam bản ghi cuối tệp cũ
    recs = _doc_tat_ca_cu_den_moi()
    assert len(recs) == kq["so_ban_ghi"]
    for truoc_rec, sau_rec in zip(recs, recs[1:]):
        assert sau_rec["truoc"] == truoc_rec["bam"]


def test_bam_dung_noi_dung_da_che(nhat_ky_sach):
    # băm đúng cái ghi xuống đĩa (sau khi che bí mật), không băm bản trước khi che
    nhat_ky.ghi("he_thong", "thong_tin", "khoá bí mật <redacted>")
    recs = _doc_tat_ca_cu_den_moi()
    assert len(recs) == 1
    rec = recs[0]
    khong_bam = {k: v for k, v in rec.items() if k != "bam"}
    chuan = json.dumps(khong_bam, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    import hashlib
    assert rec["bam"] == hashlib.sha256(("GOC" + chuan).encode("utf-8")).hexdigest()


# ───────────── phát hiện sửa lén ─────────────

def _sua_tep_hien_tai(nhat_ky_sach_dir, ham_sua):
    p = nhat_ky.thu_muc_nhat_ky() / "nhat_ky.jsonl"
    recs = _doc_tat_ca_cu_den_moi()
    recs = ham_sua(recs)
    _ghi_tep_truc_tiep(p, recs)
    # giả lập tiến trình khác sửa từ ngoài: xoá trạng thái bộ nhớ để lần kiểm sau đọc lại
    nhat_ky._CHUOI.clear()
    nhat_ky._DEM.clear()


def test_sua_mot_byte_giua_bao_bam_sai(nhat_ky_sach):
    for i in range(5):
        nhat_ky.ghi("he_thong", "thong_tin", f"bản ghi {i}")

    def sua(recs):
        recs[2]["tom_tat"] = recs[2]["tom_tat"].replace("2", "X")
        return recs
    _sua_tep_hien_tai(nhat_ky_sach, sua)

    kq = nhat_ky.kiem_tra_chuoi()
    assert kq["ok"] is False
    assert any(e["ly_do"] == "bam_sai" and e["vi_tri"] == 3 for e in kq["loi"])


def test_xoa_dong_giua_bao_dut_chuoi(nhat_ky_sach):
    for i in range(5):
        nhat_ky.ghi("he_thong", "thong_tin", f"bản ghi {i}")

    def sua(recs):
        return recs[:2] + recs[3:]
    _sua_tep_hien_tai(nhat_ky_sach, sua)

    kq = nhat_ky.kiem_tra_chuoi()
    assert kq["ok"] is False
    assert any(e["ly_do"] == "dut_chuoi" for e in kq["loi"])


def test_doi_thu_tu_hai_dong_bao_loi(nhat_ky_sach):
    for i in range(5):
        nhat_ky.ghi("he_thong", "thong_tin", f"bản ghi {i}")

    def sua(recs):
        recs[1], recs[2] = recs[2], recs[1]
        return recs
    _sua_tep_hien_tai(nhat_ky_sach, sua)

    kq = nhat_ky.kiem_tra_chuoi()
    assert kq["ok"] is False
    assert kq["loi"]


def test_chen_dong_khong_bam_bao_thieu_bam(nhat_ky_sach):
    for i in range(3):
        nhat_ky.ghi("he_thong", "thong_tin", f"bản ghi {i}")

    def sua(recs):
        chen = {"luc": "2026-01-01T00:00:00.000000Z", "kenh": "he_thong",
                "muc": "thong_tin", "tom_tat": "dòng chèn tay", "ms": None, "them": {}}
        return recs[:2] + [chen] + recs[2:]
    _sua_tep_hien_tai(nhat_ky_sach, sua)

    kq = nhat_ky.kiem_tra_chuoi()
    assert kq["ok"] is False
    assert any(e["ly_do"] == "thieu_bam_giua_chuoi" for e in kq["loi"])


def test_sua_dong_va_tinh_lai_bam_bao_dut_chuoi(nhat_ky_sach):
    import hashlib
    for i in range(4):
        nhat_ky.ghi("he_thong", "thong_tin", f"bản ghi {i}")

    def sua(recs):
        # kẻ tấn công sửa dòng 1 và tính lại bam cho ĐÚNG dòng đó, nhưng các dòng sau vẫn trỏ bam cũ
        recs[1]["tom_tat"] = "đã sửa lén"
        khong_bam = {k: v for k, v in recs[1].items() if k != "bam"}
        chuan = json.dumps(khong_bam, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        recs[1]["bam"] = hashlib.sha256((recs[1]["truoc"] + chuan).encode("utf-8")).hexdigest()
        return recs
    _sua_tep_hien_tai(nhat_ky_sach, sua)

    kq = nhat_ky.kiem_tra_chuoi()
    assert kq["ok"] is False
    assert any(e["ly_do"] == "dut_chuoi" for e in kq["loi"])


# ───────────── tương thích và biên ─────────────

def test_xoa_cu_xoa_dau_chuoi_van_ok(nhat_ky_sach):
    for i in range(5):
        nhat_ky.ghi("he_thong", "thong_tin", f"bản ghi {i}")
    truoc_khi = nhat_ky.kiem_tra_chuoi()
    assert truoc_khi["ok"] is True

    nhat_ky.xoa_cu(36500)  # xoá gần như mọi thứ? không — cần xoá một phần đầu
    # Xoá bằng cách sửa thời gian: ghi đè luc của 3 dòng đầu thành quá khứ rồi xoa_cu(1)
    p = nhat_ky.thu_muc_nhat_ky() / "nhat_ky.jsonl"
    recs = _doc_tat_ca_cu_den_moi()
    for r in recs[:3]:
        r["luc"] = "2020-01-01T00:00:00.000000Z"
        # tính lại bam cho khớp để không báo bam_sai (giả lập bản ghi cũ hợp lệ)
        import hashlib
        khong_bam = {k: v for k, v in r.items() if k != "bam"}
        chuan = json.dumps(khong_bam, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        r["bam"] = hashlib.sha256((r["truoc"] + chuan).encode("utf-8")).hexdigest()
    _ghi_tep_truc_tiep(p, recs)
    nhat_ky._CHUOI.clear()
    nhat_ky._DEM.clear()

    da_xoa = nhat_ky.xoa_cu(30)
    assert da_xoa == 3
    kq = nhat_ky.kiem_tra_chuoi()
    assert kq["ok"] is True
    assert kq["so_ban_ghi"] == 2
    # neo đầu đổi thành truoc của bản ghi đầu còn lại (trỏ vào bản ghi đã xoá) — không phải lỗi
    assert kq["neo_dau"] != "GOC"


def test_ban_ghi_cu_khong_bam_o_dau(nhat_ky_sach):
    p = nhat_ky.thu_muc_nhat_ky() / "nhat_ky.jsonl"
    cu = [
        {"luc": "2026-01-01T00:00:00.000001Z", "kenh": "he_thong", "muc": "thong_tin",
         "tom_tat": "bản ghi thời chưa có chuỗi", "ms": None, "them": {}},
        {"luc": "2026-01-01T00:00:00.000002Z", "kenh": "he_thong", "muc": "thong_tin",
         "tom_tat": "bản ghi cũ thứ hai", "ms": None, "them": {}},
    ]
    _ghi_tep_truc_tiep(p, cu)
    nhat_ky._CHUOI.clear()
    nhat_ky._DEM.clear()
    for i in range(3):
        nhat_ky.ghi("he_thong", "thong_tin", f"bản ghi mới {i}")
    kq = nhat_ky.kiem_tra_chuoi()
    assert kq["ok"] is True
    assert kq["so_ban_ghi"] == 3
    assert kq["so_ban_ghi_cu"] == 2


def test_khoi_dong_lai_ghi_tiep_khong_dut(nhat_ky_sach):
    for i in range(3):
        nhat_ky.ghi("he_thong", "thong_tin", f"trước khởi động {i}")
    # giả lập khởi động lại: mất toàn bộ trạng thái bộ nhớ
    nhat_ky._CHUOI.clear()
    nhat_ky._DEM.clear()
    nhat_ky._last_luc = ""
    for i in range(3):
        nhat_ky.ghi("he_thong", "thong_tin", f"sau khởi động {i}")
    kq = nhat_ky.kiem_tra_chuoi()
    assert kq["ok"] is True
    assert kq["so_ban_ghi"] == 6


def test_nhieu_luong_ghi_dong_thoi(nhat_ky_sach):
    loi = []

    def ghi_n(n):
        try:
            for i in range(50):
                nhat_ky.ghi("he_thong", "thong_tin", f"luồng {n} dòng {i}")
        except Exception as e:  # noqa: BLE001
            loi.append(e)

    cac_luong = [threading.Thread(target=ghi_n, args=(n,)) for n in range(4)]
    for t in cac_luong:
        t.start()
    for t in cac_luong:
        t.join()
    assert not loi
    kq = nhat_ky.kiem_tra_chuoi()
    assert kq["ok"] is True
    assert kq["so_ban_ghi"] == 200
    # không hai bản ghi nào cùng truoc (mỗi truoc chỉ dùng một lần)
    recs = _doc_tat_ca_cu_den_moi()
    cac_truoc = [r["truoc"] for r in recs]
    assert len(set(cac_truoc)) == len(cac_truoc)


def test_loi_dia_khi_ghi_roi_ghi_tiep_khong_dut(nhat_ky_sach, monkeypatch):
    import errno
    for i in range(3):
        nhat_ky.ghi("he_thong", "thong_tin", f"trước lỗi {i}")

    that_bai = {"lan": 0}
    goc_write = nhat_ky.os.write

    def write_luc_dau_gay_loi(fd, data):
        if that_bai["lan"] == 0:
            that_bai["lan"] += 1
            raise OSError(errno.ENOSPC, "No space left on device")
        return goc_write(fd, data)

    monkeypatch.setattr(nhat_ky.os, "write", write_luc_dau_gay_loi)
    nhat_ky.ghi("he_thong", "thong_tin", "lần ghi bị lỗi đĩa")  # không ném
    monkeypatch.setattr(nhat_ky.os, "write", goc_write)
    nhat_ky.ghi("he_thong", "thong_tin", "sau lỗi đĩa")
    kq = nhat_ky.kiem_tra_chuoi()
    assert kq["ok"] is True
    # lần ghi lỗi không để lại bản ghi dở làm đứt chuỗi
    assert kq["so_ban_ghi"] == 4


def test_tat_chuoi_bang_bien_moi_truong(nhat_ky_sach, monkeypatch):
    monkeypatch.setenv("WEB2API_NHAT_KY_CHUOI_BAM", "0")
    nhat_ky._CHUOI.clear()
    nhat_ky.ghi("he_thong", "thong_tin", "không chuỗi")
    recs = _doc_tat_ca_cu_den_moi()
    assert "bam" not in recs[0]
    assert "truoc" not in recs[0]


# ───────────── API ─────────────

def test_api_chuoi_xac_thuc_va_noi_dung(client):
    assert client.get("/api/nhat-ky/chuoi").status_code == 401
    assert client.get("/api/nhat-ky/chuoi", headers={"Authorization": "Bearer sai"}).status_code == 401

    nhat_ky.ghi("he_thong", "thong_tin", "bản ghi cho api")
    r = client.get("/api/nhat-ky/chuoi", headers=QT)
    assert r.status_code == 200
    du_lieu = r.json()
    assert du_lieu["ok"] is True
    assert du_lieu["so_ban_ghi"] >= 1
    # không lộ nội dung bản ghi
    than = r.text
    assert "bản ghi cho api" not in than


def test_api_chuoi_khoa_khach_bi_403(client):
    from app import khoa_khach
    _, khoa_tho = khoa_khach.tao_khoa("test", ["chat"])
    r = client.get("/api/nhat-ky/chuoi", headers={"Authorization": f"Bearer {khoa_tho}"})
    assert r.status_code == 403


# ───────────── hiệu năng ─────────────

def test_kiem_tra_chuoi_tep_lon_khong_vot_rss(nhat_ky_sach):
    import resource
    p = nhat_ky.thu_muc_nhat_ky() / "nhat_ky.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    # dựng tệp ~20 MB trực tiếp (nhanh hơn ghi từng dòng qua API)
    nhat_ky._CHUOI.clear()
    truoc = "GOC"
    import hashlib
    with open(p, "w", encoding="utf-8") as f:
        for i in range(60000):
            dong = {"luc": f"2026-01-01T00:00:{i // 1000:02d}.{i % 1000:03d}000Z",
                    "kenh": "cuoc_goi", "muc": "thong_tin",
                    "tom_tat": f"dòng {i} " + "y" * 200, "ms": 1.0, "them": {},
                    "truoc": truoc}
            chuan = json.dumps(dong, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            dong["bam"] = hashlib.sha256((truoc + chuan).encode("utf-8")).hexdigest()
            truoc = dong["bam"]
            f.write(json.dumps(dong, ensure_ascii=False) + "\n")
    assert p.stat().st_size > 15 * 1024 * 1024
    truoc_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    kq = nhat_ky.kiem_tra_chuoi()
    sau_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    assert kq["ok"] is True
    assert kq["so_ban_ghi"] == 60000
    # RSS tăng dưới 100 MB (quét luồng, không nạp cả tệp)
    assert (sau_rss - truoc_rss) < 100 * 1024
