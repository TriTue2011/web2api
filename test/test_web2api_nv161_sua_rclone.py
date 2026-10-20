"""NV161-SUA — module rclone: nối lõi thật + tách thực thể + SSRF + gui_len.

Mỗi test ứng một phát hiện của soát độc lập A:
- A1/CAO-4: manifest có «thiet_bi»; `thuc_the`/`trang_thai`/`thuc_hien` đều
  async; và có test chạy QUA LÕI THẬT (TestClient + SoModule thật): thực thể
  hiện ở /api/thiet-bi, hành động chạy qua cổng /api/thiet-bi/{id}/hanh-dong.
- A2/CAO-5: TÁCH thực thể theo mức — rclone.doc (doc), rclone.ghi (ghi),
  rclone.xoa + rclone.cau_hinh (nguy_hiem). Qua lõi thật: sau khi quản trị đã
  XÁC NHẬN thực thể một lần, xoá/ghi cấu hình VẪN đòi `xac_nhan` từng lần,
  còn hành động «ghi» thì không.
- A5: `dich_vu_url` chỉ loopback/mạng nội bộ — IP công khai và IPv6 6to4 bị
  chặn ở `cai()`; `nap()` với URL xấu thì module từ chối gọi mạng.
- A1/CAO-6: `gui_len` KHÔNG truyền nội dung qua tham số (trần 8 KB của lõi):
  module đọc tệp tại đường dẫn trong `thu_muc_len` đã khai; ngoài thư mục đó
  thì từ chối trước khi gọi mạng; không còn nhận `du_lieu_b64`.
- A7: gọi dịch vụ bằng httpx.AsyncClient — không còn httpx.Client đồng bộ
  chiếm luồng chung của lõi.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import threading
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "rclone"
DICH_VU = GOC / "dich-vu" / "rclone"

KHOA_GIA = "khoa-gia-rclone-0123456789abcdef"


def _nap_dich_vu():
    spec = importlib.util.spec_from_file_location("rclone_srv_nv161_sua",
                                                  DICH_VU / "server.py")
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    return md


def _module_py():
    spec = importlib.util.spec_from_file_location("rclone_nv161_sua",
                                                  THU_MUC / "module.py")
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    return md


def _manifest():
    return json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture()
def md():
    return _module_py()


@pytest.fixture()
def dich_vu(tmp_path, monkeypatch):
    """Dịch vụ rclone thật với nhị phân giả (giống bộ NV161)."""
    srv = _nap_dich_vu()
    monkeypatch.setenv("RCLONE_KHOA", KHOA_GIA)
    monkeypatch.setenv("RCLONE_BIN", str(DICH_VU / "ho_tro_test" / "rclone_gia.py"))
    monkeypatch.setenv("RCLONE_THU_MUC_GOC", str(tmp_path / "dv"))
    monkeypatch.setenv("RCLONE_PORT", "0")
    monkeypatch.setenv("RCLONE_GIA_STATE", str(tmp_path / "dv" / "state.json"))
    server, _ = srv.tao_may_chu()
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture()
def mod(md, dich_vu, tmp_path):
    m = md.Module(_manifest())
    m.nap({"dich_vu_url": dich_vu, "khoa_dich_vu": KHOA_GIA, "ghi_bat": "1",
           "thu_muc_len": str(tmp_path / "len")})
    (tmp_path / "len").mkdir(exist_ok=True)
    return m


def chay(coro):
    return asyncio.run(coro)


# ── A1: hợp đồng nối lõi ─────────────────────────────────────────────────────

def test_manifest_co_thiet_bi_va_ba_ham_async(md):
    manifest = _manifest()
    assert "thiet_bi" in manifest["cung_cap"]
    for ten in ("thuc_the", "trang_thai", "thuc_hien"):
        assert inspect.iscoroutinefunction(getattr(md.Module, ten)), ten


def test_bon_thuc_the_moi_thuc_the_mot_muc(md, dich_vu):
    m = md.Module(_manifest())
    m.nap({"dich_vu_url": dich_vu, "khoa_dich_vu": KHOA_GIA, "ghi_bat": "1"})
    thuc_the = {e["id"]: e for e in chay(m.thuc_the())}
    assert set(thuc_the) == {"rclone.doc", "rclone.ghi",
                             "rclone.xoa", "rclone.cau_hinh"}
    bang = {i: {t["hanh_dong"]: t["rui_ro"] for t in e["tinh_nang"]}
            for i, e in thuc_the.items()}
    assert thuc_the["rclone.doc"]["rui_ro_mac_dinh"] == "doc"
    assert set(bang["rclone.doc"]) == {"liet_ke", "doc_chu", "tai_ve",
                                       "remotes", "kiem_tra", "config_xem",
                                       "drive_start"}
    assert thuc_the["rclone.ghi"]["rui_ro_mac_dinh"] == "ghi"
    assert bang["rclone.ghi"] == {"gui_len": "ghi", "chuyen_tep": "ghi"}
    assert thuc_the["rclone.xoa"]["rui_ro_mac_dinh"] == "nguy_hiem"
    assert bang["rclone.xoa"] == {"xoa": "nguy_hiem", "xoa_remote": "nguy_hiem"}
    assert thuc_the["rclone.cau_hinh"]["rui_ro_mac_dinh"] == "nguy_hiem"
    assert bang["rclone.cau_hinh"] == {
        "them_remote": "nguy_hiem", "config_ghi": "nguy_hiem",
        "drive_exchange": "nguy_hiem", "luu_khoa_json": "nguy_hiem"}
    # Mỗi thực thể MỘT mức: nhãn từng hành động == mức mặc định của thực thể.
    for e in thuc_the.values():
        assert {t["rui_ro"] for t in e["tinh_nang"]} == {e["rui_ro_mac_dinh"]}


def test_trang_thai_tra_theo_thuc_the(mod):
    tt = chay(mod.trang_thai(["rclone.doc", "rclone.ghi"]))
    assert set(tt) == {"rclone.doc", "rclone.ghi"}
    assert tt["rclone.doc"]["ok"] is True


def test_xoa_khong_con_o_thuc_the_ghi(mod):
    kq = chay(mod.thuc_hien("rclone.ghi", "xoa", {"duong_dan": "g:a.txt"}))
    assert kq["ok"] is False
    assert kq["loi"] == "hanh_dong_khong_ho_tro"
    kq = chay(mod.thuc_hien("rclone.ghi", "them_remote",
                            {"ten": "g", "loai": "drive", "tham_so": {}}))
    assert kq["loi"] == "hanh_dong_khong_ho_tro"


# ── A5: dich_vu_url chỉ loopback / mạng nội bộ, kiểm cả ở nap() ─────────────

@pytest.mark.parametrize("url", [
    "http://8.8.8.8:8702",
    "https://1.1.1.1",
    "http://[2002:a9fe:a9fe::1]:8702",   # 6to4 mang 169.254.169.254
    "http://[2002:7f00:1::]:8702",      # 6to4 mang 127.0.0.1
])
def test_cai_chan_url_cong_khai_va_6to4(md, url):
    m = md.Module(_manifest())
    kq = chay(m.cai({"dich_vu_url": url, "khoa_dich_vu": KHOA_GIA}))
    assert kq["ok"] is False
    assert "dia_chi_dich_vu_khong_hop_le" in kq["loi"]


def test_cai_cho_loopback_va_lan(md):
    m = md.Module(_manifest())
    for url in ("http://127.0.0.1:8702", "http://192.168.1.50:8702",
                "http://10.0.0.5:8702", "http://[::1]:8702"):
        kq = chay(m.cai({"dich_vu_url": url, "khoa_dich_vu": KHOA_GIA}))
        assert kq["ok"] is True, url


def test_nap_url_xau_module_tu_choi_goi_mang(md, monkeypatch):
    m = md.Module(_manifest())
    m.nap({"dich_vu_url": "http://169.254.169.254:8702",
           "khoa_dich_vu": KHOA_GIA, "ghi_bat": "1"})

    def _cam(*a, **k):
        raise AssertionError("không được chạm mạng khi URL đã bị chặn")

    monkeypatch.setattr(md.httpx, "AsyncClient", _cam)
    monkeypatch.setattr(md.httpx, "Client", _cam)
    kq = chay(m.thuc_hien("rclone.doc", "remotes", {}))
    assert kq["ok"] is False
    assert kq["loi"] == "dia_chi_dich_vu_khong_hop_le"


# ── CAO-6: gui_len theo đường dẫn trong thu_muc_len ─────────────────────────

def test_gui_len_theo_duong_dan(mod, tmp_path):
    chay(mod.thuc_hien("rclone.cau_hinh", "them_remote",
                       {"ten": "g3", "loai": "drive", "tham_so": {}}))
    tep = tmp_path / "len" / "tep.txt"
    tep.write_bytes(b"noi dung")
    kq = chay(mod.thuc_hien(
        "rclone.ghi", "gui_len",
        {"duong_dan_tep": str(tep), "thu_muc": "g3:thu/muc", "ten": "tep.txt"}))
    assert kq["ok"] is True
    assert "g3:thu/muc/tep.txt" in kq["duong_dan"]


def test_gui_len_khong_con_nhan_du_lieu_b64(mod):
    import base64
    kq = chay(mod.thuc_hien(
        "rclone.ghi", "gui_len",
        {"du_lieu_b64": base64.b64encode(b"noi dung").decode(),
         "thu_muc": "g3:thu/muc", "ten": "tep.txt"}))
    assert kq["ok"] is False
    assert kq["loi"] == "thieu_duong_dan_tep"


def test_gui_len_ngoai_thu_muc_len_bi_chan_truoc_mang(mod, md, tmp_path,
                                                     monkeypatch):
    ngoai = tmp_path / "ngoai.txt"
    ngoai.write_bytes(b"bi-mat")

    def _cam(*a, **k):
        raise AssertionError("không được chạm mạng")

    monkeypatch.setattr(md.httpx, "AsyncClient", _cam)
    monkeypatch.setattr(md.httpx, "Client", _cam)
    kq = chay(mod.thuc_hien(
        "rclone.ghi", "gui_len",
        {"duong_dan_tep": str(ngoai), "thu_muc": "g3:thu/muc", "ten": "x.txt"}))
    assert kq["ok"] is False
    assert kq["loi"] == "duong_dan_ngoai_thu_muc_len"


def test_gui_len_chua_khai_thu_muc_len(md, dich_vu, tmp_path):
    m = md.Module(_manifest())
    m.nap({"dich_vu_url": dich_vu, "khoa_dich_vu": KHOA_GIA, "ghi_bat": "1"})
    tep = tmp_path / "a.txt"
    tep.write_bytes(b"x")
    kq = chay(m.thuc_hien(
        "rclone.ghi", "gui_len",
        {"duong_dan_tep": str(tep), "thu_muc": "g3:thu/muc", "ten": "a.txt"}))
    assert kq["ok"] is False
    assert kq["loi"] == "chua_khai_thu_muc_len"


def test_gui_len_tep_qua_tran(mod, tmp_path):
    tep = tmp_path / "len" / "lon.bin"
    with open(tep, "wb") as f:
        f.truncate(25 * 1024 * 1024 + 1)  # thưa: chỉ kiểm kích thước, chưa đọc
    kq = chay(mod.thuc_hien(
        "rclone.ghi", "gui_len",
        {"duong_dan_tep": str(tep), "thu_muc": "g3:thu/muc", "ten": "lon.bin"}))
    assert kq["ok"] is False
    assert kq["loi"] == "tep_qua_lon"


# ── A7: không còn httpx.Client đồng bộ ────────────────────────────────────────

def test_khong_dung_httpx_client_dong_bo(mod, md, monkeypatch):
    def _cam(*a, **k):
        raise AssertionError("module không được dùng httpx.Client đồng bộ")

    monkeypatch.setattr(md.httpx, "Client", _cam)
    kq = chay(mod.thuc_hien("rclone.doc", "remotes", {}))
    assert kq["ok"] is True


# ── QUA LÕI THẬT ─────────────────────────────────────────────────────────────

KHOA_QT = "khoa-quan-tri-test-nv161-sua-0123456789"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


@pytest.fixture()
def loi_that(tmp_path, monkeypatch, dich_vu):
    """Lõi thật (TestClient) + module rclone THẬT cài vào SoModule thật."""
    from fastapi.testclient import TestClient

    from app import so_module
    from app.main import app
    from app.so_module import SoModule
    from gia import ghi_thu_muc

    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    anh_dir = tmp_path / "anh"
    anh_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh_dir)

    tep_module = {
        "manifest.json": (THU_MUC / "manifest.json").read_bytes(),
        "module.py": (THU_MUC / "module.py").read_bytes(),
    }
    ghi_thu_muc(data_dir / "modules" / "rclone", tep_module)
    thu_muc_len = tmp_path / "len"
    thu_muc_len.mkdir(exist_ok=True)
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        r = client.post("/api/modules/nap-lai", headers=QT)
        assert "rclone" in r.json()["them"]
        r = client.post("/api/modules/rclone/cai", headers=QT, json={
            "cau_hinh": {"dich_vu_url": dich_vu, "khoa_dich_vu": KHOA_GIA,
                         "ghi_bat": "1", "thu_muc_len": str(thu_muc_len)}})
        assert r.status_code == 200, r.text
        yield client, thu_muc_len


def _hanh_dong(client, thuc_the, hanh_dong, tham_so=None, xac_nhan=None,
               headers=None):
    than = {"hanh_dong": hanh_dong, "tham_so": tham_so or {}}
    if xac_nhan is not None:
        than["xac_nhan"] = xac_nhan
    return client.post(f"/api/thiet-bi/{thuc_the}/hanh-dong",
                       headers=headers or QT, json=than)


def test_loi_that_thay_bon_thuc_the_va_doc_chay(loi_that):
    client, _ = loi_that
    r = client.get("/api/thiet-bi", headers=QT)
    assert r.status_code == 200
    ids = {e["id"] for e in r.json()["thuc_the"]}
    assert {"rclone.doc", "rclone.ghi", "rclone.xoa",
            "rclone.cau_hinh"} <= ids
    # Đọc qua cổng thật: xác nhận thực thể đọc rồi gọi remotes.
    assert client.post("/api/thiet-bi/rclone.doc/xac-nhan",
                       headers=QT).status_code == 200
    r = _hanh_dong(client, "rclone.doc", "remotes")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_loi_that_xoa_va_cau_hinh_luon_doi_xac_nhan(loi_that):
    """A2: quản trị xác nhận THỰC THỂ một lần vẫn không đủ cho nguy_hiem."""
    client, _ = loi_that
    for thuc_the in ("rclone.xoa", "rclone.cau_hinh", "rclone.ghi"):
        assert client.post(f"/api/thiet-bi/{thuc_the}/xac-nhan",
                           headers=QT).status_code == 200
    # Khai remote trước (kèm xac_nhan từng lần).
    r = _hanh_dong(client, "rclone.cau_hinh", "them_remote",
                   {"ten": "g1", "loai": "drive", "tham_so": {}},
                   xac_nhan=True)
    assert r.status_code == 200 and r.json()["ok"] is True
    # Xoá KHÔNG kèm xac_nhan → 409 dù thực thể đã được xác nhận trước đó.
    r = _hanh_dong(client, "rclone.xoa", "xoa", {"duong_dan": "g1:a.txt"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "can_xac_nhan"
    # Xoá kèm xac_nhan → chạy tới module.
    r = _hanh_dong(client, "rclone.xoa", "xoa", {"duong_dan": "g1:a.txt"},
                   xac_nhan=True)
    assert r.status_code == 200 and r.json()["ok"] is True
    # Ghi cấu hình cũng vậy.
    r = _hanh_dong(client, "rclone.cau_hinh", "config_ghi",
                   {"noi_dung": "[g1]\ntype = drive\n"})
    assert r.status_code == 409
    # Hành động «ghi» trên thực thể đã xác nhận: KHÔNG cần xac_nhan.
    r = _hanh_dong(client, "rclone.ghi", "chuyen_tep",
                   {"nguon": "g1:a.txt", "dich": "g1:b.txt"})
    assert r.status_code == 200 and r.json()["ok"] is True
    # Xoá gọi nhầm qua thực thể ghi → 400 (không thuộc tính năng).
    r = _hanh_dong(client, "rclone.ghi", "xoa", {"duong_dan": "g1:a.txt"})
    assert r.status_code == 400


def test_loi_that_khach_goi_xoa_vao_hang_cho(loi_that):
    client, _ = loi_that
    r_tao = client.post("/api/khoa-khach", headers=QT, json={
        "nhan": "App kiểm NV161-SUA", "pham_vi": ["thiet_bi:ghi"]})
    assert r_tao.status_code == 200
    hd = {"Authorization": f"Bearer {r_tao.json()['khoa']['khoa']}"}
    r = _hanh_dong(client, "rclone.xoa", "xoa",
                   {"duong_dan": "g1:a.txt"}, headers=hd)
    assert r.status_code == 202
    assert r.json()["cho_phe_duyet"] is True
