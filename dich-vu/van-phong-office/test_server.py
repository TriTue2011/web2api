"""Dịch vụ van-phong-office (NV138): máy chủ HTTP nội bộ gọi OfficeCLI trong container phụ.

Hợp đồng kiểm ở đây:
- Chỉ nhận {lenh, doi_so[]} với lenh trong TẬP ĐÓNG (18 lệnh); mọi lệnh ngoài tập
  (kể cả `mcp`, `raw`, `watch`, `install`, `config`) bị chặn TRƯỚC khi gọi nhị phân.
- Mọi đối số đường dẫn qua resolve() dưới thư mục làm việc; từ chối ký tự điều
  khiển, «..», symlink ra ngoài; không bao giờ truyền chuỗi thô vào shell.
- Hết giờ 60 s (lệnh dài: batch/merge/import → 180 s); đầu ra ≤ 80.000 ký tự.
- Sai/thiếu khoá dịch vụ → 401; /suc-khoe mở không khoá (chỉ báo còn sống).
- Tải lên ≤ 25 MB, kiểm magic bytes zip OOXML; tên tệp do hệ thống sinh.
- Nhị phân OfficeCLI THẬT không cần cho test: OFFICE_BIN trỏ sang chương trình giả.

Máy chủ ngoài đều GIẢ cục bộ (127.0.0.1).
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import sys
import threading
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parent
if str(GOC) not in sys.path:
    sys.path.insert(0, str(GOC))

import server  # noqa: E402  (chưa có server.py → test ĐỎ, đúng chu trình test-trước)

KHOA_GIA = "khoa-gia-0123456789abcdef-ghi-ro-la-gia"
KHOA_SAI = "khoa-sai-0123456789abcdef-ghi-ro-la-gia"


@pytest.fixture()
def may_chu(tmp_path, monkeypatch):
    """Dựng máy chủ thật trên 127.0.0.1 với nhị phân GIẢ."""
    gia_bin = GOC / "ho_tro_test" / "officecli_gia.py"
    monkeypatch.setenv("OFFICE_BIN", str(gia_bin))
    monkeypatch.setenv("OFFICE_KHOA", KHOA_GIA)
    monkeypatch.setenv("OFFICE_THU_MUC_GOC", str(tmp_path / "lam_viec"))
    monkeypatch.setenv("OFFICE_HOST", "127.0.0.1")
    monkeypatch.setenv("OFFICE_PORT", "0")
    monkeypatch.setenv("OFFICE_GIA_CHE_DO", "echo")
    monkeypatch.setenv("OFFICECLI_SKIP_UPDATE", "1")
    srv, _ = server.tao_may_chu()
    cong = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{cong}", tmp_path / "lam_viec", gia_bin
    srv.shutdown()
    t.join(timeout=5)


def _goi(co_so, duong_dan, than=None, khoa=KHOA_GIA, phuong_thuc="POST", kieu="application/json"):
    cn = http.client.HTTPConnection("127.0.0.1", int(co_so.rsplit(":", 1)[1]), timeout=30)
    tieu_de = {}
    if khoa is not None:
        tieu_de["Authorization"] = f"Bearer {khoa}"
    du_lieu = None
    if than is not None:
        if isinstance(than, (dict, list)):
            du_lieu = json.dumps(than).encode()
        else:
            du_lieu = than
        tieu_de["Content-Type"] = kieu
        tieu_de["Content-Length"] = str(len(du_lieu))
    cn.request(phuong_thuc, duong_dan, body=du_lieu, headers=tieu_de)
    tra = cn.getresponse()
    return tra.status, tra.read()


def _lenh(co_so, lenh, doi_so=None, khoa=KHOA_GIA):
    return _goi(co_so, "/lenh", {"lenh": lenh, "doi_so": doi_so if doi_so is not None else []}, khoa=khoa)


# ── Tập lệnh đóng ─────────────────────────────────────────────────────────────

LENH_NGOAI_TAP = [
    "mcp", "raw", "raw-set", "add-part", "watch", "install", "config",
    "shell", "exec", "curl", "wget", "rm", "cat", "ls", "sh", "python3",
    ";", "&&", "$()", "`id`", "create ", " Create", "VIEW", "McP",
]


@pytest.mark.parametrize("lenh_la", LENH_NGOAI_TAP)
def test_lenh_ngoai_tap_dong_bi_chan_truoc_khi_goi(may_chu, lenh_la):
    co_so, _, gia_bin = may_chu
    log = gia_bin.parent / "da_goi.log"
    if log.exists():
        log.unlink()
    ma, than = _lenh(co_so, lenh_la, ["a.docx"])
    assert ma == 400, f"lệnh {lenh_la!r} phải bị chặn, nhận {ma}: {than[:200]}"
    assert not log.exists(), f"lệnh {lenh_la!r} đã chạm tới nhị phân — phải chặn TRƯỚC khi gọi"


def test_du_tap_lenh_dong_di_qua(may_chu):
    co_so, _, _ = may_chu
    for lenh in sorted(server.LENH_CHO_PHEP):
        ma, than = _lenh(co_so, lenh, [])
        assert ma == 200, f"lệnh {lenh!r} trong tập đóng phải đi qua, nhận {ma}: {than[:200]}"
    assert len(server.LENH_CHO_PHEP) == 18


def test_lenh_khong_phai_chuoi_bi_tu_choi(may_chu):
    co_so, _, _ = may_chu
    for xau in (None, 123, ["create"], {"lenh": "create"}):
        ma, _ = _lenh(co_so, xau, [])
        assert ma == 400


def test_doi_so_khong_phai_danh_sach_bi_tu_choi(may_chu):
    co_so, _, _ = may_chu
    ma, _ = _lenh(co_so, "view", "khong-phai-danh-sach")
    assert ma == 400


# ── Đường dẫn ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("duong_xau", [
    "../../etc/passwd",
    "..\\..\\windows\\system32",
    "/etc/passwd",
    "a/../../b.docx",
    "tep\x00.docx",
    "tep\n.docx",
    "tep\x1f.docx",
])
def test_duong_dan_thoat_thu_muc_bi_chan(may_chu, duong_xau):
    co_so, _, _ = may_chu
    ma, than = _lenh(co_so, "view", [duong_xau])
    assert ma in (400, 403), f"{duong_xau!r} phải bị chặn, nhận {ma}: {than[:200]}"


def test_symlink_ra_ngoai_bi_chan(may_chu):
    co_so, thu_muc, _ = may_chu
    ngoai = thu_muc.parent / "bi_mat_ngoai.txt"
    ngoai.write_text("du-lieu-nhay-cam")
    (thu_muc / "lien_ket").symlink_to(ngoai)
    ma, _ = _lenh(co_so, "view", ["lien_ket"])
    assert ma == 403


def test_duong_dan_trong_thu_muc_di_qua(may_chu):
    co_so, thu_muc, _ = may_chu
    (thu_muc / "bao_cao.docx").write_bytes(b"PK\x03\x04gia")
    ma, than = _lenh(co_so, "view", ["bao_cao.docx"])
    assert ma == 200, than[:200]
    # Đối số tới nhị phân là đường tuyệt đối đã resolve, KHÔNG qua shell.
    ban_ghi = json.loads((GOC / "ho_tro_test" / "doi_so_cuoi.json").read_text())
    assert ban_ghi[1] == str((thu_muc / "bao_cao.docx").resolve())


def test_khong_bao_gio_dung_shell(may_chu):
    co_so, _, _ = may_chu
    ma, _ = _lenh(co_so, "query", ["a.docx", "run:contains(x); rm -rf /"])
    assert ma == 200
    ban_ghi = json.loads((GOC / "ho_tro_test" / "doi_so_cuoi.json").read_text())
    # Chuỗi nguy hiểm đi NGUYÊN VẸN như một phần tử argv — không bị shell diễn giải.
    assert "run:contains(x); rm -rf /" in ban_ghi


def test_co_data_tro_ra_ngoai_bi_chan(may_chu):
    co_so, _, _ = may_chu
    ma, _ = _lenh(co_so, "merge", ["mau.docx", "ra.docx", "--data", "/etc/passwd"])
    assert ma in (400, 403)


def test_doi_so_dict_duoc_ghi_tam_trong_thu_muc(may_chu):
    co_so, thu_muc, _ = may_chu
    ma, than = _lenh(co_so, "merge", ["mau.docx", "ra.docx", {"ten": "Acme"}])
    assert ma == 200, than[:200]
    ban_ghi = json.loads((GOC / "ho_tro_test" / "doi_so_cuoi.json").read_text())
    duong_data = Path(ban_ghi[ban_ghi.index("--data") + 1]) if "--data" in ban_ghi else None
    assert duong_data is not None and duong_data.resolve().is_relative_to(thu_muc.resolve())


# ── Hết giờ và đầu ra ─────────────────────────────────────────────────────────

def test_het_gio_ngat_tien_trinh(may_chu, monkeypatch):
    co_so, _, _ = may_chu
    monkeypatch.setenv("OFFICE_GIA_CHE_DO", "ngu")
    monkeypatch.setenv("OFFICE_HET_GIO_MAC_DINH", "1")
    srv2, _ = server.tao_may_chu()
    cong = srv2.server_address[1]
    t = threading.Thread(target=srv2.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    try:
        ma, than = _goi(f"http://127.0.0.1:{cong}", "/lenh", {"lenh": "view", "doi_so": ["a.docx"]})
    finally:
        srv2.shutdown()
        t.join(timeout=5)
    assert ma == 200
    assert json.loads(than)["het_gio"] is True


def test_dau_ra_qua_lon_bi_cat(may_chu, monkeypatch):
    co_so, _, _ = may_chu
    monkeypatch.setenv("OFFICE_GIA_CHE_DO", "dai")
    srv2, _ = server.tao_may_chu()
    cong = srv2.server_address[1]
    t = threading.Thread(target=srv2.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    try:
        ma, than = _goi(f"http://127.0.0.1:{cong}", "/lenh", {"lenh": "view", "doi_so": ["a.docx"]})
    finally:
        srv2.shutdown()
        t.join(timeout=5)
    assert ma == 200
    kq = json.loads(than)
    assert kq["bi_cat"] is True
    assert len(kq["dau_ra"]) <= server.TRAN_DAU_RA + 100


# ── Xác thực ──────────────────────────────────────────────────────────────────

def test_thieu_khoa_401(may_chu):
    co_so, _, _ = may_chu
    ma, _ = _lenh(co_so, "view", ["a.docx"], khoa=None)
    assert ma == 401


def test_khoa_sai_401_va_khong_lo_ly_do(may_chu):
    co_so, _, _ = may_chu
    ma, than = _lenh(co_so, "view", ["a.docx"], khoa=KHOA_SAI)
    assert ma == 401
    assert KHOA_GIA.encode() not in than and KHOA_SAI.encode() not in than


def test_suc_khoe_khong_can_khoa(may_chu):
    co_so, _, _ = may_chu
    ma, than = _goi(co_so, "/suc-khoe", khoa=None, phuong_thuc="GET")
    assert ma == 200
    assert json.loads(than) == {"ok": True}


def test_khoa_yeu_bi_tu_choi_khoi_dong(monkeypatch):
    monkeypatch.setenv("OFFICE_KHOA", "ngan")
    with pytest.raises(RuntimeError):
        server.tao_may_chu()


# ── Tải lên / tải xuống ───────────────────────────────────────────────────────

def _multipart(boundary: bytes, ten_truong: str, ten_tep: str, du_lieu: bytes) -> bytes:
    return (
        b"--" + boundary + b"\r\n"
        + f'Content-Disposition: form-data; name="{ten_truong}"; filename="{ten_tep}"\r\n'.encode()
        + b"Content-Type: application/vnd.openxmlformats\r\n\r\n"
        + du_lieu + b"\r\n--" + boundary + b"--\r\n"
    )


def test_tai_len_hop_le_tra_ten_he_thong_sinh(may_chu):
    co_so, thu_muc, _ = may_chu
    ranh = b"ranh-gioi-123"
    tep = _multipart(ranh, "tep", "../../doc-khach-dat-ten.docx", b"PK\x03\x04" + b"x" * 100)
    ma, than = _goi(co_so, "/tai-len", tep, kieu=f"multipart/form-data; boundary={ranh.decode()}")
    assert ma == 200, than[:200]
    kq = json.loads(than)
    assert ".." not in kq["id"] and "/" not in kq["id"]
    assert kq["id"] != "../../doc-khach-dat-ten.docx"
    assert (thu_muc / (kq["id"] + ".docx")).exists()


def test_tai_len_sai_magic_bi_tu_choi(may_chu):
    co_so, _, _ = may_chu
    ranh = b"ranh-gioi-123"
    tep = _multipart(ranh, "tep", "gia.docx", b"KHONG-PHAI-ZIP" + b"x" * 100)
    ma, _ = _goi(co_so, "/tai-len", tep, kieu=f"multipart/form-data; boundary={ranh.decode()}")
    assert ma == 400


def test_tai_len_qua_25mb_bi_tu_choi(may_chu, monkeypatch):
    monkeypatch.setenv("OFFICE_TRAN_TAI_LEN", str(1024))
    co_so, _, _ = may_chu
    srv2, _ = server.tao_may_chu()
    cong = srv2.server_address[1]
    t = threading.Thread(target=srv2.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    try:
        ranh = b"ranh-gioi-123"
        tep = _multipart(ranh, "tep", "lon.docx", b"PK\x03\x04" + b"x" * 2048)
        ma, _ = _goi(f"http://127.0.0.1:{cong}", "/tai-len", tep,
                     kieu=f"multipart/form-data; boundary={ranh.decode()}")
    finally:
        srv2.shutdown()
        t.join(timeout=5)
    assert ma == 413


def test_tai_xuong_id_la_404(may_chu):
    co_so, _, _ = may_chu
    ma, _ = _goi(co_so, "/tai-xuong/khong-co-gi", khoa=KHOA_GIA, phuong_thuc="GET")
    assert ma == 404


def test_tai_xuong_tra_dung_noi_dung(may_chu):
    co_so, _, _ = may_chu
    ranh = b"ranh-gioi-123"
    noi_dung = b"PK\x03\x04" + b"y" * 50
    tep = _multipart(ranh, "tep", "a.docx", noi_dung)
    ma, than = _goi(co_so, "/tai-len", tep, kieu=f"multipart/form-data; boundary={ranh.decode()}")
    id_tep = json.loads(than)["id"]
    ma2, than2 = _goi(co_so, f"/tai-xuong/{id_tep}", khoa=KHOA_GIA, phuong_thuc="GET")
    assert ma2 == 200
    assert than2 == noi_dung


# ── Khởi động tự kiểm ─────────────────────────────────────────────────────────

def test_thieu_co_tat_cap_nhat_thi_khong_khoi_dong(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFICE_KHOA", KHOA_GIA)
    monkeypatch.setenv("OFFICE_THU_MUC_GOC", str(tmp_path))
    monkeypatch.delenv("OFFICECLI_SKIP_UPDATE", raising=False)
    with pytest.raises(RuntimeError):
        server.tao_may_chu()
