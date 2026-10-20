"""Dịch vụ dich-vu/rclone — kiểm với nhị phân GIẢ (NV161).

Máy chủ thật trên 127.0.0.1, RCLONE_BIN trỏ ho_tro_test/rclone_gia.py.
Conftest chặn Internet — drive_exchange dùng urlopen giả (monkeypatch).
"""

from __future__ import annotations

import importlib.util
import json
import os
import threading
import urllib.request
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parent
GIA = GOC / "ho_tro_test" / "rclone_gia.py"
KHOA = "khoa-gia-dich-vu-rclone-0123456789abcdef"
HDR = {"Authorization": f"Bearer {KHOA}"}


def _nap_server():
    spec = importlib.util.spec_from_file_location("rclone_srv", GOC / "server.py")
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
    # nhị phân GIẢ là script python: cần python trong PATH (đã có).
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


def test_suc_khoe_khong_can_khoa(srv):
    _, base, _ = srv
    r = urllib.request.urlopen(base + "/suc-khoe", timeout=10)
    assert r.status == 200
    assert json.loads(r.read().decode()) == {"ok": True}


def test_lenh_khong_khoa_401(srv):
    _, base, _ = srv
    try:
        _post(base, "/lenh", {"lenh": "trang_thai"})
    except urllib.error.HTTPError as e:
        assert e.code == 401
    else:
        raise AssertionError("phải 401")


def test_lenh_sai_khoa_401(srv):
    _, base, _ = srv
    try:
        _post(base, "/lenh", {"lenh": "trang_thai"},
              {"Authorization": "Bearer sai-hoan-toan-0123456789abcdef"})
    except urllib.error.HTTPError as e:
        assert e.code == 401
    else:
        raise AssertionError("phải 401")


def test_khoa_yeu_tu_choi_khoi_dong(tmp_path, monkeypatch):
    md = _nap_server()
    monkeypatch.setenv("RCLONE_KHOA", "ngan")
    monkeypatch.setenv("RCLONE_BIN", str(GIA))
    monkeypatch.setenv("RCLONE_THU_MUC_GOC", str(tmp_path))
    with pytest.raises(RuntimeError):
        md.tao_may_chu()


def test_khoa_don_dieu_tu_choi_khoi_dong(tmp_path, monkeypatch):
    md = _nap_server()
    monkeypatch.setenv("RCLONE_KHOA", "a" * 30)
    monkeypatch.setenv("RCLONE_BIN", str(GIA))
    monkeypatch.setenv("RCLONE_THU_MUC_GOC", str(tmp_path))
    with pytest.raises(RuntimeError):
        md.tao_may_chu()


def test_lenh_ngoai_tap_dong_400(srv):
    _, base, _ = srv
    try:
        _post(base, "/lenh", {"lenh": "ls; rm -rf /"}, HDR)
    except urllib.error.HTTPError as e:
        assert e.code == 400
    else:
        raise AssertionError("phải 400 lenh_khong_ho_tro")


def test_trang_thai(srv):
    _, base, _ = srv
    ma, kq = _lenh(base, "trang_thai")
    assert ma == 200
    assert kq["ok"] is True
    assert "v1.75.2-gia" in kq["version"]


def test_remotes_rong_roi_them_remote(srv):
    _, base, _ = srv
    _, kq = _lenh(base, "remotes")
    assert kq["remotes"] == []
    _, kq = _lenh(base, "them_remote",
                  {"ten": "gdrive", "loai": "drive",
                   "tham_so": {"scope": "drive"}})
    assert kq["ok"] is True
    _, kq = _lenh(base, "remotes")
    assert kq["remotes"] == [{"name": "gdrive", "type": "drive"}]


def test_them_remote_ten_sai(srv):
    _, base, _ = srv
    _, kq = _lenh(base, "them_remote",
                  {"ten": "a; rm -rf", "loai": "drive", "tham_so": {}})
    assert kq["ok"] is False
    assert "chỉ gồm chữ, số" in kq["error"]


def test_them_remote_loai_sai(srv):
    _, base, _ = srv
    _, kq = _lenh(base, "them_remote",
                  {"ten": "x1", "loai": "--config", "tham_so": {}})
    assert kq["ok"] is False


def test_them_remote_khoa_tham_so_sai(srv):
    _, base, _ = srv
    _, kq = _lenh(base, "them_remote",
                  {"ten": "x2", "loai": "s3",
                   "tham_so": {"--config": "x"}})
    assert kq["ok"] is False
    assert "tham số remote không hợp lệ" in kq["error"]


def test_them_remote_dung_obscure(srv, monkeypatch):
    """--obscure bắt buộc: kiểm bằng cách chọc vào _chay."""
    md, base, _ = srv
    da_goi = []

    def _chay_gia(args, *, timeout=30):
        da_goi.append(args)
        return True, "{}", ""

    monkeypatch.setattr(md, "_chay", _chay_gia)
    md.tao_remote("rr1", "s3", {"mat_khau": "bi-mat"})
    assert "--obscure" in da_goi[0]
    assert "--" in da_goi[0]


def test_config_xem_che_khoa(srv):
    _, base, _ = srv
    _lenh(base, "them_remote", {"ten": "s3a", "loai": "s3",
                                "tham_so": {"mat_khau": "bi-mat-that"}})
    # nhị phân giả không ghi vào rclone.conf thật — ghi tay để kiểm che.
    conf = Path(srv[2]) / "rclone" / "rclone.conf"
    conf.write_text("[s3a]\ntype = s3\nsecret_access_key = bi-mat-that\nregion = hn\n",
                    encoding="utf-8")
    _, kq = _lenh(base, "config_xem")
    assert kq["ok"] is True
    assert "bi-mat-that" not in kq["noi_dung"]
    assert "secret_access_key = •••" in kq["noi_dung"]
    assert "region = hn" in kq["noi_dung"]


def test_config_ghi_hong_giu_ban_cu(srv, monkeypatch):
    """Nhị phân giả không làm hỏng config — ép _chay trả lỗi để kiểm rollback."""
    md, base, tmp = srv
    conf = tmp / "rclone" / "rclone.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("[cu]\ntype = s3\n", encoding="utf-8")

    def _chay_hong(args, *, timeout=30):
        if args[:2] == ["config", "dump"]:
            return False, "", "config hỏng"
        return True, "{}", ""

    monkeypatch.setattr(md, "_chay", _chay_hong)
    kq = md.dat_config("[moi]\ntype = drive\n")
    assert kq["ok"] is False
    assert conf.read_text(encoding="utf-8") == "[cu]\ntype = s3\n"


def test_config_ghi_chan_ban_che(srv):
    _, base, _ = srv
    _, kq = _lenh(base, "config_ghi", {"noi_dung": "[a]\nmat_khau= •••\n"})
    assert kq["ok"] is False
    assert "•••" in kq["error"]


def test_liet_ke_va_doc_chu(srv):
    _, base, _ = srv
    _lenh(base, "them_remote", {"ten": "g1", "loai": "drive", "tham_so": {}})
    ma, kq = _lenh(base, "liet_ke", {"duong_dan": "g1:thu/muc"})
    assert ma == 200 and kq["ok"] is True
    assert kq["muc"][0]["ten"] == "a.txt"
    ma, kq = _lenh(base, "doc_chu", {"duong_dan": "g1:a.txt"})
    assert kq["ok"] is True
    assert "noi-dung-gia" in kq["noi_dung"]


def test_liet_ke_remote_la(srv):
    _, base, _ = srv
    _, kq = _lenh(base, "liet_ke", {"duong_dan": "khongco:thu/muc"})
    assert kq["ok"] is False


def test_liet_ke_ten_sai(srv):
    _, base, _ = srv
    _, kq = _lenh(base, "liet_ke", {"duong_dan": "khong-co-dau-hai-cham"})
    assert kq["ok"] is False


def test_duong_dan_ra_ngoai_bi_chan(srv, monkeypatch):
    """.. và tuyệt đối trỏ ra ngoài thư mục làm việc → 403/400."""
    md, base, _ = srv
    goc = md.thu_muc_lam_viec()
    with pytest.raises(md.LoiYeuCau):
        md._duong_dan_cuc_bo("../ngoai", goc)
    with pytest.raises(md.LoiYeuCau):
        md._duong_dan_cuc_bo("/etc/passwd", goc)
    # symlink trỏ ra ngoài cũng bị chặn
    ngoai = base and Path(srv[2]) / "ngoai.txt"
    ngoai.write_text("x", encoding="utf-8")
    lien_ket = goc / "lk"
    try:
        lien_ket.symlink_to(ngoai)
    except OSError:
        pytest.skip("không tạo được symlink")
    with pytest.raises(md.LoiYeuCau):
        md._duong_dan_cuc_bo("lk", goc)


def test_tep_len_va_gui_len_va_xoa(srv):
    _, base, _ = srv
    _lenh(base, "them_remote", {"ten": "g2", "loai": "drive", "tham_so": {}})
    # bước 1: đẩy byte
    req = urllib.request.Request(base + "/tep-len", data=b"0123456789" * 10,
                                 headers={**HDR,
                                          "Content-Type": "application/octet-stream"})
    r = urllib.request.urlopen(req, timeout=30)
    kq = json.loads(r.read().decode())
    assert kq["ok"] is True
    id_tep = kq["id_tep"]
    # bước 2: gửi lên đám mây
    _, kq = _lenh(base, "gui_len",
                  {"id_tep": id_tep, "thu_muc": "g2:thu/muc", "ten": "a.txt"})
    assert kq["ok"] is True
    assert "g2:thu/muc/a.txt" in kq["duong_dan"]
    # xoá
    _, kq = _lenh(base, "xoa", {"duong_dan": "g2:thu/muc/a.txt"})
    assert kq["ok"] is True


def test_tep_xuong_id_la_404(srv):
    _, base, _ = srv
    try:
        req = urllib.request.Request(base + "/tep-xuong/ffffffffffffffff",
                                     headers=HDR)
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as e:
        assert e.code == 404
    else:
        raise AssertionError("phải 404")


def test_tai_ve_va_tep_xuong(srv):
    _, base, _ = srv
    _lenh(base, "them_remote", {"ten": "g3", "loai": "drive", "tham_so": {}})
    _, kq = _lenh(base, "tai_ve", {"duong_dan": "g3:bao-cao.xlsx"})
    assert kq["ok"] is True
    req = urllib.request.Request(base + "/tep-xuong/" + kq["id_tep"], headers=HDR)
    r = urllib.request.urlopen(req, timeout=10)
    assert r.status == 200
    assert r.read() == b"du-lieu-gia"


def test_chuyen_tep_khac_remote_bi_chan(srv):
    _, base, _ = srv
    _, kq = _lenh(base, "chuyen_tep",
                  {"nguon": "g1:a.txt", "dich": "g2:b.txt"})
    assert kq["ok"] is False
    assert "cùng kho" in kq["error"]


def test_kiem_tra(srv):
    _, base, _ = srv
    _lenh(base, "them_remote", {"ten": "g4", "loai": "drive", "tham_so": {}})
    _, kq = _lenh(base, "kiem_tra", {"ten": "g4"})
    assert kq["ok"] is True
    _, kq = _lenh(base, "kiem_tra", {"ten": "khongco"})
    assert kq["ok"] is False


def test_xoa_remote(srv):
    _, base, _ = srv
    _lenh(base, "them_remote", {"ten": "gx", "loai": "drive", "tham_so": {}})
    _, kq = _lenh(base, "xoa_remote", {"ten": "gx"})
    assert kq["ok"] is True
    _, kq = _lenh(base, "remotes")
    assert all(r["name"] != "gx" for r in kq["remotes"])


def test_drive_start_dung_duong_dan(srv):
    _, base, _ = srv
    _, kq = _lenh(base, "drive_start",
                  {"client_id": "cid-gia.apps.googleusercontent.com"})
    assert kq["ok"] is True
    assert "accounts.google.com" in kq["auth_url"]
    assert "offline" in kq["auth_url"]


def test_drive_start_thieu_client_id(srv):
    _, base, _ = srv
    _, kq = _lenh(base, "drive_start", {"client_id": ""})
    assert kq["ok"] is False


def test_drive_exchange_doi_ma(monkeypatch, srv):
    """urlopen giả trả token có refresh_token."""
    md, base, _ = srv

    class _PhanHoi:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"access_token": "at",
                               "refresh_token": "rt",
                               "expires_in": 3600}).encode()

    monkeypatch.setattr(md, "urlopen", lambda *a, **k: _PhanHoi())
    _, kq = _lenh(base, "drive_exchange",
                  {"ten": "gd", "client_id": "cid", "client_secret": "cs",
                   "redirect_url": "http://127.0.0.1:53682/?code=ma-gia"})
    assert kq["ok"] is True


def test_drive_exchange_khong_refresh_token(monkeypatch, srv):
    md, base, _ = srv

    class _PhanHoi:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"access_token": "at"}).encode()

    monkeypatch.setattr(md, "urlopen", lambda *a, **k: _PhanHoi())
    _, kq = _lenh(base, "drive_exchange",
                  {"ten": "gd2", "client_id": "cid", "client_secret": "cs",
                   "redirect_url": "ma-gia-truc-tiep"})
    assert kq["ok"] is False
    assert "refresh_token" in kq["error"]


def test_luu_khoa_json_hop_le(srv):
    _, base, _ = srv
    noi_dung = json.dumps({"client_email": "a@b.iam.gserviceaccount.com",
                           "private_key": "-----BEGIN PRIVATE KEY-----"})
    _, kq = _lenh(base, "luu_khoa_json",
                  {"ten": "khoa-cua-toi", "noi_dung": noi_dung})
    assert kq["ok"] is True
    assert kq["email"] == "a@b.iam.gserviceaccount.com"
    p = Path(kq["duong_dan"])
    assert p.is_file()
    assert oct(p.stat().st_mode)[-3:] == "600"


def test_luu_khoa_json_sai_dang(srv):
    _, base, _ = srv
    _, kq = _lenh(base, "luu_khoa_json",
                  {"ten": "x", "noi_dung": json.dumps({"a": 1})})
    assert kq["ok"] is False
    _, kq = _lenh(base, "luu_khoa_json", {"ten": "x", "noi_dung": "khong-phai-json"})
    assert kq["ok"] is False


def test_xoa_chi_xoa_file(srv):
    """deletefile trên remote lạ → lỗi, không crash."""
    _, base, _ = srv
    _, kq = _lenh(base, "xoa", {"duong_dan": "khongco:a.txt"})
    assert kq["ok"] is False
