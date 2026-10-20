"""Dịch vụ dich-vu/rclone — vòng SỬA theo soát độc lập A (NV161-SUA).

Mỗi test ứng một phát hiện:
- A3/CAO-1: bảng loại remote ĐÓNG + tham số ĐÓNG theo từng loại (cấm local,
  sftp kèm `ssh`, alias/union/crypt…, cấm tham số lạ) — áp cả ở `tao_remote`
  lẫn `config_ghi` (phân tích INI).
- A3/CAO-2: `_chay` truyền môi trường tối thiểu — KHÔNG có RCLONE_KHOA.
- A4/CAO-3: `config_xem` che theo CẤU TRÚC (mặc định che; chỉ tập khoá an
  toàn mới hiện giá trị) — `sas_url`, `access_grant`, URL chứa mật khẩu bị che.
- A6: `tai_ve` có trần kích thước; tệp tạm được dọn (sau /tep-xuong, sau
  gui_len thành công, và quét tệp quá hạn).
- A8: 401 trả NGAY không đọc thân; `tham_so` không phải dict không làm sập
  kết nối (trả JSON lỗi).
- A9: tên remote không được bắt đầu bằng `-` (tiêm cờ CLI).
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parent
GIA = GOC / "ho_tro_test" / "rclone_gia.py"
KHOA = "khoa-gia-dich-vu-rclone-0123456789abcdef"
HDR = {"Authorization": f"Bearer {KHOA}"}
BI_MAT = "BI-MAT-THAT-0123456789"


def _nap_server():
    spec = importlib.util.spec_from_file_location("rclone_srv_sua", GOC / "server.py")
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    return md


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    md = _nap_server()
    monkeypatch.setenv("RCLONE_KHOA", KHOA)
    monkeypatch.setenv("RCLONE_BIN", str(GIA))
    monkeypatch.setenv("RCLONE_THU_MUC_GOC", str(tmp_path))
    monkeypatch.setenv("RCLONE_PORT", "0")
    monkeypatch.setenv("RCLONE_GIA_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("PATH", os.pathsep.join([str(GIA.parent), os.environ.get("PATH", "")]))
    server, _ = md.tao_may_chu()
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield md, f"http://127.0.0.1:{server.server_address[1]}", tmp_path
    server.shutdown()


def _post(base, duong, obj, headers=None):
    data = json.dumps(obj, ensure_ascii=False).encode()
    req = urllib.request.Request(base + duong, data=data,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    return urllib.request.urlopen(req, timeout=30)


def _lenh(base, lenh, tham_so=None):
    r = _post(base, "/lenh", {"lenh": lenh, "tham_so": tham_so or {}}, HDR)
    return r.status, json.loads(r.read().decode("utf-8"))


# ── A3: bảng loại remote ĐÓNG ────────────────────────────────────────────────

@pytest.mark.parametrize("loai", ["local", "alias", "union", "combine",
                                  "hasher", "crypt", "cache", "chunker",
                                  "memory", "ssh", "drive2"])
def test_loai_remote_cam_bi_tu_choi(srv, loai):
    _, base, _ = srv
    _, kq = _lenh(base, "them_remote", {"ten": "r1", "loai": loai, "tham_so": {}})
    assert kq["ok"] is False, f"loại {loai} phải bị cấm"


def test_sftp_tham_so_ssh_bi_cam(srv):
    """sftp được phép nhưng TUYỆT ĐỐI không kèm `ssh` (chạy lệnh ngoài)."""
    _, base, _ = srv
    _, kq = _lenh(base, "them_remote",
                  {"ten": "s1", "loai": "sftp",
                   "tham_so": {"host": "host.example.invalid",
                               "ssh": "sh -c 'id >/data/pwn'"}})
    assert kq["ok"] is False


def test_tham_so_la_bi_cam(srv):
    """Tham số ngoài tập đóng của loại bị từ chối (kể cả khoá dạng *_command)."""
    _, base, _ = srv
    _, kq = _lenh(base, "them_remote",
                  {"ten": "d1", "loai": "drive",
                   "tham_so": {"shell_command": "id"}})
    assert kq["ok"] is False
    _, kq = _lenh(base, "them_remote",
                  {"ten": "d2", "loai": "drive", "tham_so": {"xyz_la": "1"}})
    assert kq["ok"] is False


def test_loai_duoc_phep_va_tham_so_dung_tap(srv):
    """Đối chứng: loại đám mây thật + tham số trong tập đóng vẫn chạy."""
    _, base, _ = srv
    _, kq = _lenh(base, "them_remote",
                  {"ten": "s3a", "loai": "s3",
                   "tham_so": {"provider": "AWS", "access_key_id": "AKIA-GIA",
                               "secret_access_key": "bi-mat", "region": "hn",
                               "endpoint": "https://s3.example.invalid"}})
    assert kq["ok"] is True
    _, kq = _lenh(base, "them_remote",
                  {"ten": "sftpa", "loai": "sftp",
                   "tham_so": {"host": "host.example.invalid", "user": "u",
                               "pass": "p", "port": "22"}})
    assert kq["ok"] is True
    _, kq = _lenh(base, "them_remote",
                  {"ten": "wda", "loai": "webdav",
                   "tham_so": {"url": "https://dav.example.invalid/",
                               "user": "u", "pass": "p", "vendor": "other"}})
    assert kq["ok"] is True


def test_config_ghi_ap_cung_bang_dong(srv):
    """config_ghi (dán rclone.conf) phải qua CÙNG bảng đóng như them_remote."""
    md, base, _ = srv
    kq = md.dat_config("[xau]\ntype = local\n")
    assert kq["ok"] is False
    kq = md.dat_config("[xau]\ntype = sftp\nhost = h.example.invalid\n"
                       "ssh = sh -c id\n")
    assert kq["ok"] is False
    kq = md.dat_config("[tot]\ntype = drive\nscope = drive\n")
    assert kq["ok"] is True


# ── A3: môi trường tối thiểu cho tiến trình con ─────────────────────────────

def test_chay_khong_ke_thua_khoa_va_bien_la(srv, monkeypatch):
    md, _, _ = srv
    monkeypatch.setenv("DAU_HIEU_RIENG_NV161", "gia-tri-rieng")
    bat = {}

    class _Xong:
        returncode = 0
        stdout = ""
        stderr = ""

    def _run_gia(cmd, **kw):
        bat["env"] = kw.get("env")
        return _Xong()

    monkeypatch.setattr(md.subprocess, "run", _run_gia)
    ok, _, _ = md._chay(["version"])
    assert ok is True
    env = bat["env"]
    assert env is not None, "_chay phải truyền env tường minh"
    assert "RCLONE_KHOA" not in env
    assert "DAU_HIEU_RIENG_NV161" not in env
    assert "PATH" in env


# ── A4: config_xem che theo cấu trúc ────────────────────────────────────────

def test_config_xem_che_theo_cau_truc(srv):
    _, base, tmp = srv
    conf = tmp / "rclone" / "rclone.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(
        "[kho]\n"
        "type = s3\n"
        "provider = AWS\n"
        "region = hn\n"
        f"sas_url = https://ac.blob.core.windows.net/c?sig={BI_MAT}\n"
        f"access_grant = {BI_MAT}\n"
        f"url = https://nguoi:{BI_MAT}@dav.example.invalid/\n"
        f"token = {{\"access_token\": \"{BI_MAT}\"}}\n",
        encoding="utf-8")
    _, kq = _lenh(base, "config_xem")
    assert kq["ok"] is True
    nd = kq["noi_dung"]
    assert BI_MAT not in nd
    # Khoá an toàn (siêu dữ liệu không bí mật) vẫn hiện để người dùng đối chiếu.
    assert "type = s3" in nd
    assert "provider = AWS" in nd
    assert "region = hn" in nd


# ── A6: trần tai_ve + dọn tệp tạm ───────────────────────────────────────────

def test_tai_ve_qua_tran_bi_tu_choi_va_don(srv, monkeypatch):
    md, base, _ = srv
    _lenh(base, "them_remote", {"ten": "g1", "loai": "drive", "tham_so": {}})
    monkeypatch.setattr(md, "TRAN_TEP_QUA_LENH", 4)  # tệp giả dài 11 byte
    _, kq = _lenh(base, "tai_ve", {"duong_dan": "g1:a.txt"})
    assert kq["ok"] is False
    assert list(md.thu_muc_lam_viec().glob("tep-*.bin")) == []


def test_tep_xuong_xoa_tep_sau_khi_tai(srv):
    md, base, _ = srv
    _lenh(base, "them_remote", {"ten": "g3", "loai": "drive", "tham_so": {}})
    _, kq = _lenh(base, "tai_ve", {"duong_dan": "g3:bao-cao.xlsx"})
    assert kq["ok"] is True
    req = urllib.request.Request(base + "/tep-xuong/" + kq["id_tep"], headers=HDR)
    r = urllib.request.urlopen(req, timeout=10)
    assert r.read() == b"du-lieu-gia"
    assert list(md.thu_muc_lam_viec().glob("tep-*.bin")) == []
    # Tải lại lần hai: tệp đã dọn → 404.
    try:
        urllib.request.urlopen(
            urllib.request.Request(base + "/tep-xuong/" + kq["id_tep"],
                                   headers=HDR), timeout=10)
    except urllib.error.HTTPError as e:
        assert e.code == 404
    else:
        raise AssertionError("phải 404 sau khi tệp đã dọn")


def test_gui_len_xoa_tep_tam_sau_thanh_cong(srv):
    md, base, _ = srv
    _lenh(base, "them_remote", {"ten": "g2", "loai": "drive", "tham_so": {}})
    req = urllib.request.Request(base + "/tep-len", data=b"0123456789",
                                 headers={**HDR,
                                          "Content-Type": "application/octet-stream"})
    r = urllib.request.urlopen(req, timeout=30)
    id_tep = json.loads(r.read().decode())["id_tep"]
    _, kq = _lenh(base, "gui_len",
                  {"id_tep": id_tep, "thu_muc": "g2:thu/muc", "ten": "a.txt"})
    assert kq["ok"] is True
    assert list(md.thu_muc_lam_viec().glob("tep-*.bin")) == []


def test_don_tep_tam_qua_han(srv):
    md, _, _ = srv
    goc = md.thu_muc_lam_viec()
    cu = goc / "tep-0123456789abcdef.bin"
    cu.write_bytes(b"cu")
    moi = goc / "tep-fedcba9876543210.bin"
    moi.write_bytes(b"moi")
    khac = goc / "tep-khac.txt"
    khac.write_bytes(b"khac")
    qua_khu = time.time() - 7200
    os.utime(cu, (qua_khu, qua_khu))
    os.utime(khac, (qua_khu, qua_khu))
    md.don_tep_tam(600)
    assert not cu.exists()
    assert moi.exists()
    assert khac.exists()  # chỉ dọn đúng mẫu tep-*.bin


# ── A8/A9: hàng rào HTTP + tên remote ────────────────────────────────────────

def test_401_tra_ngay_khong_doc_than(srv):
    """Không khoá + Content-Length lớn + không gửi thân: 401 phải về ngay."""
    _, base, _ = srv
    cong = int(base.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", cong), timeout=5) as s:
        s.settimeout(4)
        s.sendall(b"POST /lenh HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                  b"Content-Type: application/json\r\n"
                  b"Content-Length: 1000000\r\n\r\n")
        nhan = s.recv(4096)
    assert b"401" in nhan


def test_tham_so_khong_phai_dict_khong_sap_ket_noi(srv):
    """`tham_so` lồng bên trong không phải dict: bản cũ `dict(chuỗi)` ném
    ValueError không ai bắt → kết nối rớt (RemoteDisconnected), không JSON lỗi."""
    _, base, _ = srv
    r = _post(base, "/lenh",
              {"lenh": "them_remote",
               "tham_so": {"ten": "x1", "loai": "drive", "tham_so": "chuoi-la"}},
              HDR)
    kq = json.loads(r.read().decode("utf-8"))
    assert kq["ok"] is False


def test_ten_remote_bat_dau_bang_gach_bi_cam(srv):
    _, base, _ = srv
    _, kq = _lenh(base, "them_remote",
                  {"ten": "--log-file", "loai": "drive", "tham_so": {}})
    assert kq["ok"] is False
    _, kq = _lenh(base, "xoa_remote", {"ten": "--help"})
    assert kq["ok"] is False
