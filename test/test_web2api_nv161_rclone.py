"""Module rclone — cầu nối lưu trữ đám mây qua container phụ (NV161).

- Ép hành động ghi/xoá cần `ghi_bat` (mặc định TẮT): tắt thì báo «chua_bat_ghi»
  và không gọi mạng — chống ghi/xoá nhầm khi chưa bật.
- Hành động ngoài tập của thực thể → từ chối TRƯỚC khi gọi mạng.
- NV161-SUA: bốn thực thể tách theo mức rủi ro (doc/ghi/xoa/cau_hinh);
  hợp đồng nối lõi + gui_len theo đường dẫn: xem test_web2api_nv161_sua_rclone.py.
- Khoá dịch vụ không vào log (một logging.Filter theo khuôn zalo-bot).
- Địa chỉ dịch vụ kiểm lúc `cai`: loopback/mạng nội bộ được; link-local,
  dải không định tuyến bị chặn.
- Đầu vào/đầu ra qua máy chủ dịch vụ THẬT (dich-vu/rclone/server.py) chạy trên
  127.0.0.1 với nhị phân rclone GIẢ (ho_tro_test/rclone_gia.py).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import threading
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "rclone"
DICH_VU = GOC / "dich-vu" / "rclone"

KHOA_GIA = "khoa-gia-rclone-0123456789abcdef"


def _nap_dich_vu():
    spec = importlib.util.spec_from_file_location("rclone_srv_nv161",
                                                  DICH_VU / "server.py")
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    return md


def _module_py():
    spec = importlib.util.spec_from_file_location("rclone_nv161",
                                                  THU_MUC / "module.py")
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    return md


@pytest.fixture()
def md():
    return _module_py()


@pytest.fixture()
def dich_vu(tmp_path, monkeypatch):
    """Dịch vụ rclone thật với nhị phân giả."""
    srv = _nap_dich_vu()
    monkeypatch.setenv("RCLONE_KHOA", KHOA_GIA)
    monkeypatch.setenv("RCLONE_BIN", str(DICH_VU / "ho_tro_test" / "rclone_gia.py"))
    monkeypatch.setenv("RCLONE_THU_MUC_GOC", str(tmp_path))
    monkeypatch.setenv("RCLONE_PORT", "0")
    monkeypatch.setenv("RCLONE_GIA_STATE", str(tmp_path / "state.json"))
    server, _ = srv.tao_may_chu()
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture()
def mod(md, dich_vu, tmp_path):
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    m = md.Module(manifest)
    m.nap({"dich_vu_url": dich_vu, "khoa_dich_vu": KHOA_GIA, "ghi_bat": "1",
           "thu_muc_len": str(tmp_path)})
    return m


@pytest.fixture()
def mod_chi_doc(md, dich_vu):
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    m = md.Module(manifest)
    m.nap({"dich_vu_url": dich_vu, "khoa_dich_vu": KHOA_GIA, "ghi_bat": "0"})
    return m


def chay(coro):
    return asyncio.run(coro)


def test_manifest_hop_le():
    from app.kham_pha import kiem_manifest
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    assert kiem_manifest(manifest) is None
    assert manifest["id"] == "rclone"


def test_thuc_the_va_rui_ro(mod):
    # NV161-SUA: bốn thực thể, mỗi thực thể MỘT mức (lõi tính theo thực thể).
    thuc_the = {e["id"]: e for e in chay(mod.thuc_the())}
    assert set(thuc_the) == {"rclone.doc", "rclone.ghi",
                             "rclone.xoa", "rclone.cau_hinh"}
    bang_doc = {t["hanh_dong"]: t["rui_ro"] for t in thuc_the["rclone.doc"]["tinh_nang"]}
    assert bang_doc["liet_ke"] == "doc"
    assert bang_doc["tai_ve"] == "doc"
    bang_ghi = {t["hanh_dong"]: t["rui_ro"] for t in thuc_the["rclone.ghi"]["tinh_nang"]}
    assert bang_ghi == {"gui_len": "ghi", "chuyen_tep": "ghi"}
    bang_xoa = {t["hanh_dong"]: t["rui_ro"] for t in thuc_the["rclone.xoa"]["tinh_nang"]}
    assert bang_xoa == {"xoa": "nguy_hiem", "xoa_remote": "nguy_hiem"}
    bang_ch = {t["hanh_dong"]: t["rui_ro"] for t in thuc_the["rclone.cau_hinh"]["tinh_nang"]}
    assert bang_ch["them_remote"] == "nguy_hiem"
    assert bang_ch["config_ghi"] == "nguy_hiem"


def test_ghi_bat_tat_bao_chua_bat_ghi_va_khong_goi_mang(mod_chi_doc, md, monkeypatch):
    da_goi = []
    monkeypatch.setattr(md.httpx, "Client",
                        lambda *a, **k: da_goi.append(1) or 1 / 0)
    monkeypatch.setattr(md.httpx, "AsyncClient",
                        lambda *a, **k: da_goi.append(1) or 1 / 0)
    kq = chay(mod_chi_doc.thuc_hien(
        "rclone.xoa", "xoa", {"duong_dan": "g:a.txt"}))
    assert kq["ok"] is False
    assert kq["loi"] == "chua_bat_ghi"
    assert da_goi == []


def test_hanh_dong_ngoai_tap_tu_choi(mod, md, monkeypatch):
    da_goi = []
    monkeypatch.setattr(md.httpx, "Client",
                        lambda *a, **k: da_goi.append(1))
    monkeypatch.setattr(md.httpx, "AsyncClient",
                        lambda *a, **k: da_goi.append(1))
    kq = chay(mod.thuc_hien("rclone.doc", "lenh_luy_thua", {}))
    assert kq["ok"] is False
    assert kq["loi"] == "hanh_dong_khong_ho_tro"
    assert da_goi == []


def test_thuc_the_la(mod):
    kq = chay(mod.thuc_hien("rclone.la", "liet_ke", {}))
    assert kq["ok"] is False
    assert kq["loi"] == "khong_thay_thuc_the"


def test_doc_qua_dich_vu_that(mod):
    kq = chay(mod.thuc_hien("rclone.doc", "remotes", {}))
    assert kq["ok"] is True
    kq = chay(mod.thuc_hien("rclone.cau_hinh", "them_remote",
                           {"ten": "gdrive1", "loai": "drive",
                            "tham_so": {"scope": "drive"}}))
    assert kq["ok"] is True
    kq = chay(mod.thuc_hien("rclone.doc", "liet_ke", {"duong_dan": "gdrive1:thu/muc"}))
    assert kq["ok"] is True
    assert kq["muc"][0]["ten"] == "a.txt"
    kq = chay(mod.thuc_hien("rclone.doc", "doc_chu", {"duong_dan": "gdrive1:a.txt"}))
    assert kq["ok"] is True
    assert "noi-dung-gia" in kq["noi_dung"]


def test_tai_ve_hai_buoc(mod):
    chay(mod.thuc_hien("rclone.cau_hinh", "them_remote",
                       {"ten": "g2", "loai": "drive", "tham_so": {}}))
    kq = chay(mod.thuc_hien("rclone.doc", "tai_ve", {"duong_dan": "g2:bao-cao.xlsx"}))
    assert kq["ok"] is True
    import base64
    assert base64.b64decode(kq["du_lieu_b64"]) == b"du-lieu-gia"


def test_gui_len_hai_buoc(mod, tmp_path):
    # NV161-SUA: gui_len đọc tệp trong thu_muc_len (không còn du_lieu_b64).
    chay(mod.thuc_hien("rclone.cau_hinh", "them_remote",
                       {"ten": "g3", "loai": "drive", "tham_so": {}}))
    tep = tmp_path / "tep.txt"
    tep.write_bytes(b"noi dung")
    kq = chay(mod.thuc_hien(
        "rclone.ghi", "gui_len",
        {"duong_dan_tep": str(tep),
         "thu_muc": "g3:thu/muc", "ten": "tep.txt"}))
    assert kq["ok"] is True
    assert "g3:thu/muc/tep.txt" in kq["duong_dan"]


def test_gui_len_tep_khong_ton_tai(mod, tmp_path):
    kq = chay(mod.thuc_hien("rclone.ghi", "gui_len",
                            {"duong_dan_tep": str(tmp_path / "khong-co.txt"),
                             "thu_muc": "g3:thu/muc", "ten": "x.txt"}))
    assert kq["ok"] is False
    assert kq["loi"] == "khong_thay_tep"


def test_config_xem_che_khoa(mod):
    """config_xem qua module không lộ token của remote."""
    chay(mod.thuc_hien("rclone.cau_hinh", "them_remote",
                       {"ten": "s3a", "loai": "s3",
                        "tham_so": {"secret_access_key": "gia-tri-bi-mat"}}))
    kq = chay(mod.thuc_hien("rclone.doc", "config_xem", {}))
    assert kq["ok"] is True
    assert "gia-tri-bi-mat" not in json.dumps(kq)


def test_cai_khoa_qua_ngan(mod):
    kq = chay(mod.cai({"dich_vu_url": "http://127.0.0.1:8702",
                       "khoa_dich_vu": "ngan"}))
    assert kq["ok"] is False
    assert "qua_ngan" in kq["loi"]


def test_cai_dia_chi_link_local_bi_chan(mod):
    kq = chay(mod.cai({"dich_vu_url": "http://169.254.169.254:8702",
                       "khoa_dich_vu": KHOA_GIA}))
    assert kq["ok"] is False
    assert "dia_chi_dich_vu_khong_hop_le" in kq["loi"]


def test_cai_dia_chi_loopback_duoc(mod):
    kq = chay(mod.cai({"dich_vu_url": "http://127.0.0.1:8702",
                       "khoa_dich_vu": KHOA_GIA, "ghi_bat": "0"}))
    assert kq["ok"] is True
    assert kq["cau_hinh_luu"]["ghi_bat"] == "0"


def test_bo_loc_log_chan_khoa(md, caplog):
    """Dòng log có «Authorization … Bearer» phải bị bộ lọc bỏ."""
    bo_loc = md._ChanKhoaDichVu()
    rec = logging.LogRecord(
        "httpx", logging.INFO, __file__, 1,
        "Authorization: Bearer %s", (KHOA_GIA,), None)
    assert bo_loc.filter(rec) is False
    rec2 = logging.LogRecord(
        "httpx", logging.INFO, __file__, 1, "dòng thường", (), None)
    assert bo_loc.filter(rec2) is True


def test_suc_khoe_khong_lo_khoa(mod):
    kq = chay(mod.suc_khoe())
    assert kq["ok"] is True
    assert KHOA_GIA not in json.dumps(kq)


def test_xoa_can_ghi_bat(mod_chi_doc):
    kq = chay(mod_chi_doc.thuc_hien("rclone.xoa", "xoa",
                                    {"duong_dan": "g:a.txt"}))
    assert kq["loi"] == "chua_bat_ghi"
