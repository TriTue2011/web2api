"""Module chay-ma — thực thi mã Python qua dịch vụ phụ cách ly (NV171).

- Hành động `chay_python` rủi ro `nguy_hiem` (mô hình không tự gọi được,
  lõi bắt phê duyệt); `kiem_tinh` (soi tĩnh, không chạy) rủi ro `doc`.
- Mặc định TẮT (`chay_bat`); chưa bật hoặc không nối được dịch vụ → báo
  «chưa bật» (đóng khi lỗi, không lộ chi tiết) và KHÔNG gọi mạng.
- Mã và đầu ra là DỮ LIỆU KHÔNG TIN: module cắt ở trần, gắn `nguon_ngoai=True`.
- Khoá dịch vụ không vào log (một logging.Filter theo khuôn van-phong).

Dịch vụ GIẢ cục bộ (monkeypatch httpx của module). Docker/dịch vụ thật
không cần cho test này.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "chay-ma"

# Chuỗi GIẢ rõ ràng — không phải bí mật thật.
KHOA_GIA = "khoa-gia-chay-ma-0123456789abcdef"
URL_GIA = "http://127.0.0.1:18703"


def _module_py():
    p = THU_MUC / "module.py"
    spec = importlib.util.spec_from_file_location("chay_ma_nv171", p)
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    return md


@pytest.fixture()
def md():
    return _module_py()


@pytest.fixture()
def mod(md):
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    m = md.Module(manifest)
    m.nap({"chay_bat": False, "dich_vu_url": URL_GIA, "khoa_dich_vu": KHOA_GIA})
    return m


def test_manifest_hop_le(md):
    from app.kham_pha import kiem_manifest
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    assert kiem_manifest(manifest) is None
    assert manifest["id"] == "chay-ma"
    assert manifest["hang"] == "B"


def test_thuc_the_va_rui_ro(mod):
    thuc_the = {e["id"]: e for e in asyncio.run(mod.thuc_the())}
    kiem = thuc_the["chay_ma.kiem"]
    chay = thuc_the["chay_ma.chay"]
    assert kiem["rui_ro_mac_dinh"] == "doc"
    assert chay["rui_ro_mac_dinh"] == "nguy_hiem"
    ten_kiem = {t["hanh_dong"] for t in kiem["tinh_nang"]}
    ten_chay = {t["hanh_dong"] for t in chay["tinh_nang"]}
    assert ten_kiem == {"kiem_tinh"}
    assert ten_chay == {"chay_python"}
    assert not (ten_kiem & ten_chay)
    muc_chay = next(t for t in chay["tinh_nang"] if t["hanh_dong"] == "chay_python")
    assert muc_chay["tham_so"]["required"] == ["ma"], "tham_so của chay_python phải bắt buộc «ma»"


def test_chua_bat_thi_bao_chua_bat_va_khong_goi_mang(mod, md, monkeypatch):
    da_goi = []
    monkeypatch.setattr(md.httpx, "AsyncClient", lambda *a, **k: da_goi.append(1) or 1 / 0)
    for id_, hd in (("chay_ma.chay", "chay_python"), ("chay_ma.kiem", "kiem_tinh")):
        kq = asyncio.run(mod.thuc_hien(id_, hd, {"ma": "print(1)"}))
        assert kq["ok"] is False
        assert "chưa bật" in kq["loi"]
    assert not da_goi, "chưa bật thì không được gọi mạng"


def _gia_lap_dich_vu(md, monkeypatch, tra_ve=None, ma_http=200):
    class TraLoiGia:
        status_code = ma_http

        def json(self):
            return tra_ve if tra_ve is not None else {"ok": True, "da_chay": True,
                                                      "stdout": "2\n", "stderr": "",
                                                      "chan_doan": "", "ma_thoat": 0,
                                                      "het_gio": False, "bi_cat": False,
                                                      "tep_ra": [], "ly_do_bo_qua": ""}

    class ClientGia:
        da_goi = None

        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **k):
            ClientGia.da_goi = (url, k.get("json"), k.get("headers"))
            return TraLoiGia()

    monkeypatch.setattr(md.httpx, "AsyncClient", ClientGia)
    return ClientGia


def test_bat_thi_goi_dich_vu_dung_duong_va_gan_nguon_ngoai(mod, md, monkeypatch):
    ClientGia = _gia_lap_dich_vu(md, monkeypatch)
    mod.nap({"chay_bat": True, "dich_vu_url": URL_GIA, "khoa_dich_vu": KHOA_GIA})
    kq = asyncio.run(mod.thuc_hien("chay_ma.chay", "chay_python",
                                   {"ma": "print(1+1)", "han_giay": 10}))
    assert kq["ok"] is True
    assert kq["nguon_ngoai"] is True
    url, than, tieu_de = ClientGia.da_goi
    assert url == URL_GIA + "/chay"
    assert than["ma"] == "print(1+1)" and than["han_giay"] == 10
    assert tieu_de["Authorization"] == f"Bearer {KHOA_GIA}"


def test_kiem_tinh_goi_dung_duong(mod, md, monkeypatch):
    ClientGia = _gia_lap_dich_vu(md, monkeypatch,
                                  {"ok": True, "goi_y": False,
                                   "ly_do": "code có 'socket.'", "kiem_tinh": "vấn đề giả"})
    mod.nap({"chay_bat": True, "dich_vu_url": URL_GIA, "khoa_dich_vu": KHOA_GIA})
    kq = asyncio.run(mod.thuc_hien("chay_ma.kiem", "kiem_tinh", {"ma": "import socket"}))
    assert kq["ok"] is True
    assert kq["goi_y"] is False
    assert ClientGia.da_goi[0] == URL_GIA + "/kiem-tinh"


def test_dau_ra_qua_tran_bi_cat_va_gan_nguon_ngoai(mod, md, monkeypatch):
    _gia_lap_dich_vu(md, monkeypatch, {"ok": True, "da_chay": True,
                                       "stdout": "x" * (md.TRAN_DAU_RA + 5000),
                                       "stderr": "", "chan_doan": "", "ma_thoat": 0,
                                       "het_gio": False, "bi_cat": True,
                                       "tep_ra": [], "ly_do_bo_qua": ""})
    mod.nap({"chay_bat": True, "dich_vu_url": URL_GIA, "khoa_dich_vu": KHOA_GIA})
    kq = asyncio.run(mod.thuc_hien("chay_ma.chay", "chay_python", {"ma": "print(1)"}))
    assert len(kq["stdout"]) <= md.TRAN_DAU_RA + 100
    assert kq["nguon_ngoai"] is True


def test_khong_noi_duoc_dich_vu_bao_chua_bat(mod, md, monkeypatch):
    class ClientHong:
        def __init__(self, *a, **k):
            raise ConnectionError("mang hong gia")

    monkeypatch.setattr(md.httpx, "AsyncClient", ClientHong)
    mod.nap({"chay_bat": True, "dich_vu_url": URL_GIA, "khoa_dich_vu": KHOA_GIA})
    kq = asyncio.run(mod.thuc_hien("chay_ma.chay", "chay_python", {"ma": "print(1)"}))
    assert kq["ok"] is False
    assert "chưa bật" in kq["loi"]


def test_dich_vu_bao_loi_ma_ngan_duoc_truyen_nguyen(mod, md, monkeypatch):
    """Mã lỗi NGẮN có cấu trúc của dịch vụ (không chứa dữ liệu) được truyền nguyên."""
    _gia_lap_dich_vu(md, monkeypatch, {"loi": "ten_tep_khong_hop_le"}, ma_http=400)
    mod.nap({"chay_bat": True, "dich_vu_url": URL_GIA, "khoa_dich_vu": KHOA_GIA})
    kq = asyncio.run(mod.thuc_hien("chay_ma.chay", "chay_python",
                                   {"ma": "print(1)", "tep_dau_vao": [{"ten": "../x", "noi_dung": ""}]}))
    assert kq["ok"] is False and kq["loi"] == "ten_tep_khong_hop_le"


def test_hanh_dong_va_thuc_the_la_bi_tu_choi_truoc_khi_goi_mang(mod, md, monkeypatch):
    da_goi = []
    monkeypatch.setattr(md.httpx, "AsyncClient", lambda *a, **k: da_goi.append(1) or 1 / 0)
    mod.nap({"chay_bat": True, "dich_vu_url": URL_GIA, "khoa_dich_vu": KHOA_GIA})
    kq = asyncio.run(mod.thuc_hien("chay_ma.chay", "chay_shell", {"ma": "ls"}))
    assert kq["ok"] is False
    kq = asyncio.run(mod.thuc_hien("chay_ma.la", "chay_python", {"ma": "print(1)"}))
    assert kq["ok"] is False
    kq = asyncio.run(mod.thuc_hien("chay_ma.kiem", "chay_python", {"ma": "print(1)"}))
    assert kq["ok"] is False, "hành động nguy_hiem không được đi qua thực thể doc"
    assert not da_goi


def test_bo_loc_log_chan_khoa_dich_vu(md):
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    md.Module(manifest).nap({"chay_bat": True, "dich_vu_url": URL_GIA,
                             "khoa_dich_vu": KHOA_GIA})  # đăng khoá như khi nap thật
    logger = logging.getLogger("httpx")
    ban_ghi = logging.LogRecord("httpx", logging.INFO, __file__, 1,
                                f"Authorization: Bearer {KHOA_GIA}", None, None)
    da_chan = [f for f in logger.filters if getattr(f, "_chay_ma_loc", False)]
    assert da_chan, "thiếu logging.Filter chặn khoá dịch vụ"
    assert not any(f.filter(ban_ghi) for f in da_chan), "khoá dịch vụ lọt qua log"


def test_suc_khoe_khong_lo_khoa(mod):
    sk = asyncio.run(mod.suc_khoe())
    assert sk["ok"] is True
    assert KHOA_GIA not in json.dumps(sk)


# ── NV171-SUA C1: SSRF qua app/tu_xa — kiểm IP SAU phân giải ────────────

def _phan_giai_gia(md, monkeypatch, bang: dict):
    """Giả getaddrinfo: host → danh sách IP (mọi dạng host lạ đều do bảng
    này quyết — đúng cách inet_aton/getaddrinfo thật phân giải)."""
    import socket as sk

    def gia(host, port=0, *a, **k):
        if host not in bang:
            raise sk.gaierror(f"gia: khong phan giai duoc {host}")
        ra = []
        for ip in bang[host]:
            if ":" in ip:
                ra.append((sk.AF_INET6, sk.SOCK_STREAM, 6, "", (ip, port or 0, 0, 0)))
            else:
                ra.append((sk.AF_INET, sk.SOCK_STREAM, 6, "", (ip, port or 0)))
        return ra

    monkeypatch.setattr(md.socket, "getaddrinfo", gia)


def _module_moi(md):
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    return md.Module(manifest)


@pytest.mark.parametrize("host", ["2852039166", "0xa9fea9fe", "025077524776",
                                  "169.254.169.254"])
def test_c1_host_so_hex_batphan_tro_metadata_bi_chan(md, monkeypatch, host):
    """C1: host dạng số nguyên/hex/bát phân được inet_aton hiểu thành
    169.254.169.254 — bản cũ cho qua (chỉ kiểm hình dạng), bản sửa phân
    giải rồi kiểm IP thật bằng app/tu_xa → chặn."""
    _phan_giai_gia(md, monkeypatch, {host: ["169.254.169.254"]})
    m = _module_moi(md)
    kq = asyncio.run(m.cai({"chay_bat": True, "dich_vu_url": f"http://{host}:8703",
                            "khoa_dich_vu": KHOA_GIA}))
    assert kq["ok"] is False, f"host {host} trỏ metadata phải bị chặn"


def test_c1_ipv6_mapped_metadata_bi_chan(md, monkeypatch):
    _phan_giai_gia(md, monkeypatch,
                   {"::ffff:169.254.169.254": ["::ffff:169.254.169.254"]})
    m = _module_moi(md)
    kq = asyncio.run(m.cai({"chay_bat": True,
                            "dich_vu_url": "http://[::ffff:169.254.169.254]:8703",
                            "khoa_dich_vu": KHOA_GIA}))
    assert kq["ok"] is False


def test_c1_ten_dns_tro_link_local_bi_chan_ten_tro_lan_duoc(md, monkeypatch):
    _phan_giai_gia(md, monkeypatch, {
        "dich-vu-xau": ["169.254.169.254"],
        "dich-vu-nha": ["10.0.0.9"],
        "localhost": ["127.0.0.1"],
    })
    m = _module_moi(md)
    kq = asyncio.run(m.cai({"chay_bat": True, "dich_vu_url": "http://dich-vu-xau:8703",
                            "khoa_dich_vu": KHOA_GIA}))
    assert kq["ok"] is False, "DNS trỏ link-local phải bị chặn (không tin tên)"
    kq = asyncio.run(m.cai({"chay_bat": True, "dich_vu_url": "http://dich-vu-nha:8703",
                            "khoa_dich_vu": KHOA_GIA}))
    assert kq["ok"] is True, f"DNS trỏ LAN riêng phải được: {kq}"
    kq = asyncio.run(m.cai({"chay_bat": True, "dich_vu_url": "http://localhost:8703",
                            "khoa_dich_vu": KHOA_GIA}))
    assert kq["ok"] is True, "localhost→loopback là ngoại lệ cục bộ có chủ đích"


def test_c1_kiem_ip_dich_vu_theo_cau_truc(md):
    import ipaddress
    kiem = md._kiem_ip_dich_vu
    assert kiem(ipaddress.ip_address("127.0.0.1")) is None
    assert kiem(ipaddress.ip_address("::1")) is None
    assert kiem(ipaddress.ip_address("192.168.1.9")) is None
    assert kiem(ipaddress.ip_address("169.254.169.254")) is not None
    assert kiem(ipaddress.ip_address("::ffff:169.254.169.254")) is not None
    assert kiem(ipaddress.ip_address("::ffff:127.0.0.1")) is None
    # IPv4 nhúng trong 6to4/Teredo chịu cùng luật (kiểm theo cấu trúc):
    assert kiem(ipaddress.ip_address("2002:7f00:1::")) is not None, "6to4 mang loopback"
    assert kiem(ipaddress.ip_address("2002:a9fe:a9fe::")) is not None, "6to4 mang metadata"
    # Dịch vụ thực thi mã không nằm trên Internet công cộng:
    assert kiem(ipaddress.ip_address("8.8.8.8")) is not None


def test_c1_nap_url_bi_chan_thi_hanh_dong_tu_choi_khong_goi_mang(md, monkeypatch):
    """C1: kiểm lại ở nap() — cấu hình nạp từ đĩa trỏ metadata thì hành
    động từ chối thẳng, httpx không được chạm."""
    _phan_giai_gia(md, monkeypatch, {"dich-vu-xau": ["169.254.169.254"]})
    da_goi = []
    monkeypatch.setattr(md.httpx, "AsyncClient", lambda *a, **k: da_goi.append(1) or 1 / 0)
    m = _module_moi(md)
    m.nap({"chay_bat": True, "dich_vu_url": "http://dich-vu-xau:8703",
           "khoa_dich_vu": KHOA_GIA})
    kq = asyncio.run(m.thuc_hien("chay_ma.chay", "chay_python", {"ma": "print(1)"}))
    assert kq["ok"] is False and "dich_vu_url" in kq["loi"]
    assert not da_goi


# ── NV171-SUA C8: lược đồ khớp trần 8 KB của lõi ───────────────────────────

def test_c8_luoc_do_khop_tran_8kb_cua_loi(mod):
    thuc_the = {e["id"]: e for e in asyncio.run(mod.thuc_the())}
    chay = next(t for t in thuc_the["chay_ma.chay"]["tinh_nang"]
                if t["hanh_dong"] == "chay_python")
    thuoc = chay["tham_so"]["properties"]
    assert thuoc["ma"]["maxLength"] <= 8000, \
        "ma hứa 40.000 ký tự nhưng lõi từ chối tham số > 8 KB (app/thiet_bi.py)"
    tep = thuoc["tep_dau_vao"]["items"]["properties"]
    assert tep["noi_dung"]["maxLength"] <= 8000


# ── cai() ────────────────────────────────────────────────────────────────

def test_cai_tu_choi_url_metadata(md):
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    m = md.Module(manifest)
    kq = asyncio.run(m.cai({"chay_bat": True, "dich_vu_url": "http://169.254.169.254/",
                            "khoa_dich_vu": KHOA_GIA}))
    assert kq["ok"] is False


def test_cai_bat_ma_thieu_khoa_bi_tu_choi(md):
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    m = md.Module(manifest)
    kq = asyncio.run(m.cai({"chay_bat": True, "dich_vu_url": URL_GIA}))
    assert kq["ok"] is False, "bật chạy mã mà không có khoá dịch vụ phải bị từ chối"


def test_cai_khoa_ngan_bi_tu_choi(md):
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    m = md.Module(manifest)
    kq = asyncio.run(m.cai({"chay_bat": True, "dich_vu_url": URL_GIA, "khoa_dich_vu": "ngan"}))
    assert kq["ok"] is False


def test_cai_tat_khong_can_khoa_va_luu_sach(md):
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    m = md.Module(manifest)
    kq = asyncio.run(m.cai({"chay_bat": False, "dich_vu_url": URL_GIA, "khoa_dich_vu": KHOA_GIA}))
    assert kq["ok"] is True
    assert KHOA_GIA not in json.dumps(kq["ket_qua_tung_khoa"])
    assert kq["cau_hinh_luu"]["chay_bat"] == "0"


# ── Cài qua lõi (SoModule) + leo thang quyền qua API ──────────────────────

def test_cai_qua_so_module_luu_cau_hinh_va_nap_lai(tmp_path, monkeypatch):
    import shutil
    from app import kho, so_module
    from app.so_module import SoModule

    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    mod_dir = tmp_path / "modules" / "chay-ma"
    shutil.copytree(THU_MUC, mod_dir, ignore=shutil.ignore_patterns("__pycache__"))

    so = SoModule()
    asyncio.run(so.khoi_dong())
    assert "chay-ma" in so.catalog

    kq_ssrf = asyncio.run(so.cai("chay-ma", {
        "chay_bat": "1", "dich_vu_url": "http://169.254.169.254:8703", "khoa_dich_vu": KHOA_GIA,
    }))
    assert kq_ssrf["ok"] is False, "SSRF link-local phải bị từ chối"

    kq_cai = asyncio.run(so.cai("chay-ma", {
        "chay_bat": "1", "dich_vu_url": URL_GIA, "khoa_dich_vu": KHOA_GIA,
    }))
    assert kq_cai["ok"] is True, f"Cài đặt thất bại: {kq_cai}"
    assert KHOA_GIA not in json.dumps(kq_cai)

    dl_modules = json.loads((tmp_path / "modules.json").read_text(encoding="utf-8"))
    muc = next(m for m in dl_modules["modules"] if m["id"] == "chay-ma")
    assert muc["cau_hinh_cong_khai"].get("khoa_dich_vu", "").startswith("…")
    assert kho.doc_bi_mat("chay-ma").get("khoa_dich_vu") == KHOA_GIA

    so_moi = SoModule()
    asyncio.run(so_moi.khoi_dong())
    mod_moi = so_moi.dang_chay["chay-ma"]
    assert mod_moi._bat is True
    assert mod_moi._khoa == KHOA_GIA


def test_cai_module_xac_thuc_leo_thang_quyen(tmp_path, monkeypatch):
    """LUẬT CỨNG #1: POST /api/modules/{id}/cai — không khoá 401; khoá sai
    401/403; module không tồn tại 404; quản trị 200."""
    import shutil
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", "khoa-quan-tri-test-0123456789abcdef")
    mod_dir = tmp_path / "modules" / "chay-ma"
    shutil.copytree(THU_MUC, mod_dir, ignore=shutil.ignore_patterns("__pycache__"))

    with TestClient(app) as client:
        client.post("/api/modules/nap-lai",
                    headers={"Authorization": "Bearer khoa-quan-tri-test-0123456789abcdef"})
        cau_hinh = {"cau_hinh": {"chay_bat": "1", "dich_vu_url": URL_GIA,
                                 "khoa_dich_vu": KHOA_GIA}}

    r = client.post("/api/modules/chay-ma/cai", json=cau_hinh)
    assert r.status_code == 401, f"Không khoá phải 401, nhận {r.status_code}"
    r = client.post("/api/modules/chay-ma/cai",
                    headers={"Authorization": "Bearer khoa-khach-hoac-sai-quyen"}, json=cau_hinh)
    assert r.status_code in (401, 403), f"Khoá sai phải 401/403, nhận {r.status_code}"
    r = client.post("/api/modules/module-khong-ton-tai-999/cai",
                    headers={"Authorization": "Bearer khoa-quan-tri-test-0123456789abcdef"},
                    json=cau_hinh)
    assert r.status_code == 404, f"Module không tồn tại phải 404, nhận {r.status_code}"
    r = client.post("/api/modules/chay-ma/cai",
                    headers={"Authorization": "Bearer khoa-quan-tri-test-0123456789abcdef"},
                    json=cau_hinh)
    assert r.status_code == 200, f"Quản trị phải 200, nhận {r.status_code}: {r.text}"
    assert r.json().get("ok") is True
