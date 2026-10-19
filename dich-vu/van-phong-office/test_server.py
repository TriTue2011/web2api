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
    assert len(server.LENH_CHO_PHEP) == 19


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
    assert ban_ghi[2] == str((thu_muc / "bao_cao.docx").resolve())


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


# ── Mục 4: Đường phần tử hợp lệ & Danh sách ≥ 25 dạng lách ───────────────────

@pytest.mark.parametrize("lenh,doi_so_them", [
    ("get", ["/body/p[1]"]),
    ("get", ["/slide[1]/shape[1]"]),
    ("get", ["/Sheet1/A1"]),
    ("get", ["/body/p[2]/r[1]"]),
    ("get", ["/"]),
    ("set", ["/body/p[1]", "--prop", "text=Xin chao"]),
    ("add", ["/body", "--type", "paragraph", "--prop", "text=Them doan"]),
    ("query", ["p:first"]),
    ("remove", ["/body/p[1]"]),
])
def test_duong_phan_tu_hop_le_qua_duoc(may_chu, lenh, doi_so_them):
    """Mục 4 (i): Đường phần tử hợp lệ qua được cho get/set/add/query/remove,
    đối số đến nơi nguyên vẹn."""
    co_so, thu_muc, gia_bin = may_chu
    (thu_muc / "tai_lieu.docx").write_bytes(b"PK\x03\x04gia")
    log_file = gia_bin.parent / "doi_so_cuoi.json"
    if log_file.exists():
        log_file.unlink()

    ma, than = _lenh(co_so, lenh, ["tai_lieu.docx", *doi_so_them])
    assert ma == 200, f"Lệnh {lenh} với đối số {doi_so_them} phải thành công 200, nhận {ma}: {than[:200]}"
    assert log_file.exists(), f"Nhị phân phải được gọi cho {lenh}"
    ban_ghi = json.loads(log_file.read_text(encoding="utf-8"))
    # Kiểm tra đối số đến nơi nguyên vẹn
    for ds in doi_so_them:
        if not ds.startswith("--prop"):
            assert ds in ban_ghi, f"Đối số {ds} phải có trong argv gửi tới nhị phân: {ban_ghi}"


DANG_LACH_25 = [
    # 1-3. Cờ --output trỏ ngoài thư mục
    ("save", ["a.docx", "--output", "../../x"]),
    ("save", ["a.docx", "--output=../../x"]),
    ("save", ["a.docx", "--output", "/etc/passwd"]),
    # 4-7. Cờ --prop tham chiếu tệp ngoài thư mục
    ("set", ["a.docx", "/body/p[1]", "--prop", "src=/etc/passwd"]),
    ("set", ["a.docx", "/body/p[1]", "--prop=src=/etc/passwd"]),
    ("set", ["a.docx", "/body/p[1]", "--prop", "src=../x"]),
    ("set", ["a.docx", "/body/p[1]", "--prop", "path=/etc/passwd"]),
    ("set", ["a.docx", "/body/p[1]", "--prop", "file=../../bi_mat"]),
    # 8-11. Cờ --input / --data trỏ ngoài thư mục
    ("batch", ["a.docx", "--input=/etc/passwd"]),
    ("batch", ["a.docx", "--input", "/etc/passwd"]),
    ("merge", ["a.docx", "b.docx", "--data=/etc/passwd"]),
    ("merge", ["a.docx", "b.docx", "--data", "../../x"]),
    # 12-13. Đối số @file đọc tệp ngoài thư mục
    ("view", ["a.docx", "@/etc/passwd"]),
    ("view", ["a.docx", "@../x"]),
    # 14-15. Cờ viết tắt -o trỏ ra ngoài
    ("save", ["a.docx", "-o", "/tmp/x"]),
    ("save", ["a.docx", "-o=../../x"]),
    # 16-19. Đường phần tử bất thường (thoát thư mục, ký tự lạ)
    ("get", ["a.docx", "/body/../p[1]"]),
    ("get", ["a.docx", "/body//p[1]"]),
    ("get", ["a.docx", "/body\\p[1]"]),
    ("get", ["a.docx", "/body/p[1]\x00"]),
    ("get", ["a.docx", "/body/p[1]\n"]),
    # 20. Tên tệp có dấu phân tách thư mục
    ("view", ["thu_muc/a.docx"]),
    # 21-23. Cờ lạ ngoài tập khai báo
    ("view", ["a.docx", "--exec", "id"]),
    ("view", ["a.docx", "--shell", "sh"]),
    ("view", ["a.docx", "--url", "http://127.0.0.1:8701"]),
    # 24. Đối số vượt quá độ dài trần
    ("view", ["a.docx", "x" * 60_000]),
    # 25-28. Cờ và dạng lách khác
    ("get", ["a.docx", "/body/p[1]", "--depth", "khong_phai_so"]),
    ("get", ["a.docx", "/body/p[1]", "--depth=9999"]),
    ("set", ["a.docx", "/body/p[1]", "--prop", "khoa khong hop le=gia_tri"]),
    ("view", ["a.docx", "--cờ_lạ=123"]),
]


@pytest.mark.parametrize("lenh,doi_so", DANG_LACH_25)
def test_25_dang_lach_bi_tu_choi_va_khong_goi_nhi_phan(may_chu, lenh, doi_so):
    """Mục 4 (ii): Danh sách ≥ 25 dạng lách đều bị từ chối (400/403) và
    nhị phân giả KHÔNG được gọi."""
    co_so, thu_muc, gia_bin = may_chu
    (thu_muc / "a.docx").write_bytes(b"PK\x03\x04gia")
    log_file = gia_bin.parent / "da_goi.log"
    if log_file.exists():
        log_file.unlink()

    ma, than = _lenh(co_so, lenh, doi_so)
    assert ma in (400, 403), f"Dạng lách {lenh} {doi_so} phải bị từ chối 400/403, nhận {ma}: {than[:200]}"
    assert not log_file.exists(), f"Dạng lách {lenh} {doi_so} đã gọi nhị phân (phải chặn trước)!"


# ── Mục 15: Khoá chứa ký tự không-ASCII ─────────────────────────────────────

def test_xac_thuc_khoa_chua_ky_tu_khong_ascii_tra_401(may_chu):
    """Mục 15: compare_digest trên bytes UTF-8; khoá chứa ký tự không-ASCII trả 401 chứ không 500."""
    co_so, _, _ = may_chu
    khoa_unicode = "kho\xe1-dich-vu-12345678901234567890"
    ma, than = _lenh(co_so, "view", ["a.docx"], khoa=khoa_unicode)
    assert ma == 401, f"Khoá unicode sai phải trả 401, nhận {ma}: {than[:200]}"
    assert json.loads(than) == {"loi": "khong_xac_thuc"}


# ── Mục 16: Xoá tệp tạm & Chống zip bomb ──────────────────────────────────────

def test_xoa_tep_tam_sau_lenh_ke_ca_khi_loi(may_chu, monkeypatch):
    """Mục 16: Tệp tạm .du-lieu-*.json phải bị xoá sau lệnh, kể cả khi lệnh gặp lỗi."""
    co_so, thu_muc, _ = may_chu
    (thu_muc / "mau.docx").write_bytes(b"PK\x03\x04gia")

    # 1. Chạy lệnh thành công
    ma, _ = _lenh(co_so, "merge", ["mau.docx", "ra.docx", {"ten": "CongTy"}])
    assert ma == 200
    tep_tam = list(thu_muc.glob(".du-lieu-*.json"))
    assert not tep_tam, f"Tệp tạm không được tồn tại sau lệnh thành công: {tep_tam}"

    # 2. Chạy lệnh thất bại (nhị phân lỗi)
    monkeypatch.setenv("OFFICE_GIA_CHE_DO", "loi")
    ma_loi, _ = _lenh(co_so, "merge", ["mau.docx", "ra.docx", {"ten": "CongTy"}])
    tep_tam_loi = list(thu_muc.glob(".du-lieu-*.json"))
    assert not tep_tam_loi, f"Tệp tạm phải bị xoá cả khi lệnh lỗi: {tep_tam_loi}"


def test_tai_len_chong_zip_bomb(may_chu):
    """Mục 16: Tải lên tệp zip bomb (kích thước giải nén khổng lồ) bị từ chối 400/413."""
    import io
    import zipfile

    co_so, _, _ = may_chu
    ranh = b"ranh-gioi-zip-bomb"

    # Tạo tệp zip với file_size giải nén lớn (> 100MB)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Ghi 100MB số 0 nhưng nén lại rất nhỏ (< 100KB)
        zf.writestr("word/document.xml", b"\x00" * (120 * 1024 * 1024))
    du_lieu_zip = buf.getvalue()

    tep = _multipart(ranh, "tep", "bomb.docx", du_lieu_zip)
    ma, than = _goi(co_so, "/tai-len", tep, kieu=f"multipart/form-data; boundary={ranh.decode()}")
    assert ma in (400, 413), f"Zip bomb phải bị từ chối 400/413, nhận {ma}: {than[:200]}"

