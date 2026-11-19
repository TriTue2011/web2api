"""Test NV295 — tự ghim chứng chỉ lần đầu qua IP LAN + nút tin khi đổi chứng chỉ.

Hướng 19:30 ngày 03/11 thay phương án nút ở lần đầu: lần đầu nối tới IP LAN
thì hệ thống TỰ GHIM chứng chỉ thiết bị trình ra (không hỏi người dùng);
nút «Tin chứng chỉ này» chỉ còn cho trường hợp chứng chỉ ĐỔI.

Phạm vi file này: lõi đo vân tay + vé tin (app/chung_chi_tls), đề nghị tin
khi đổi chứng chỉ ở mikrotik-routeros và zte-e2633, và quy tắc mô tả trường
cấu hình. File test/test_nv295_ip_lan_tu_ghim.py khóa riêng: cổng IP LAN
(tên miền/WAN bị từ chối) + tự ghim lần đầu ở cả hai module.

Mọi chứng chỉ/mật khẩu/địa chỉ trong file này đều GIẢ, tạo lúc chạy test
(openssl tự ký, loopback, TEST_NET 192.0.2.x không dùng ở đây); không có
vân tay thật của thiết bị nào.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GOC))

from app.kham_pha import doc_manifest, nap_ma  # noqa: E402

THU_MUC_MIKROTIK = GOC / "modules" / "mikrotik-routeros"

MAT_KHAU_GIA = "mat-khau-gia-nv295"
NGUOI_DUNG_GIA = "nguoi-doc-gia"
VAN_TAY_GIA = "ab" * 32  # 64 ký tự hex giả, chỉ để kiểm định dạng/khớp mặt nạ


@pytest.fixture(autouse=True)
def _cho_phep_loopback(monkeypatch):
    # Máy chủ giả chạy trên loopback — chỉ test mới được phép.
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")


# ───────── Chứng chỉ tự ký + máy chủ TLS trần (chỉ bắt tay, không giao thức) ─────────

@pytest.fixture(scope="session")
def chung_chi_a(tmp_path_factory):
    d = tmp_path_factory.mktemp("nv295-a")
    key, crt = d / "key.pem", d / "crt.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key),
         "-out", str(crt), "-days", "1", "-nodes", "-subj", "/CN=127.0.0.1"],
        check=True, capture_output=True, timeout=60)
    der = ssl.PEM_cert_to_DER_cert(crt.read_text())
    return {"crt": crt, "key": key, "der": der,
            "van_tay": hashlib.sha256(der).hexdigest()}


@pytest.fixture(scope="session")
def chung_chi_b(tmp_path_factory):
    d = tmp_path_factory.mktemp("nv295-b")
    key, crt = d / "key.pem", d / "crt.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key),
         "-out", str(crt), "-days", "1", "-nodes", "-subj", "/CN=127.0.0.1"],
        check=True, capture_output=True, timeout=60)
    der = ssl.PEM_cert_to_DER_cert(crt.read_text())
    return {"crt": crt, "key": key, "der": der,
            "van_tay": hashlib.sha256(der).hexdigest()}


class _XuLyTlsTran(socketserver.BaseRequestHandler):
    def handle(self):
        cfg = self.server.cfg
        s = self.request
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cfg["crt"]), str(cfg["key"]))
        try:
            s = ctx.wrap_socket(s, server_side=True)
        except (OSError, ssl.SSLError):
            return
        s.settimeout(2.0)
        try:
            while True:
                b = s.recv(65536)
                if not b:
                    break
                cfg["da_nhan"].append(b)
        except (OSError, ssl.SSLError):
            pass
        finally:
            try:
                s.close()
            except OSError:
                pass


class MayChuTlsTran(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


@pytest.fixture
def may_chu_tls():
    created = []

    def _tao(chung_chi):
        cfg = {"crt": chung_chi["crt"], "key": chung_chi["key"], "da_nhan": []}
        srv = MayChuTlsTran(("127.0.0.1", 0), _XuLyTlsTran)
        srv.cfg = cfg
        t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05},
                             daemon=True)
        t.start()
        created.append(srv)
        return srv, cfg

    yield _tao
    for srv in created:
        srv.shutdown()
        srv.server_close()


# ───────── Nạp module ─────────

_lop_mikrotik_cls = None


def _lop_mikrotik():
    global _lop_mikrotik_cls
    if _lop_mikrotik_cls is None:
        manifest, loi = doc_manifest(THU_MUC_MIKROTIK)
        assert loi is None, loi
        lop, _ = nap_ma(THU_MUC_MIKROTIK, manifest, "test")
        _lop_mikrotik_cls = lop
    return _lop_mikrotik_cls


def _module_mikrotik():
    manifest, loi = doc_manifest(THU_MUC_MIKROTIK)
    assert loi is None, loi
    return _lop_mikrotik()(manifest)


# ───────── Lõi: mặt nạ, vé, đo ─────────

def test_ma_hien_thi_chi_dau_cuoi():
    from app import chung_chi_tls
    ma = chung_chi_tls.ma_hien_thi(VAN_TAY_GIA)
    assert ma.startswith(VAN_TAY_GIA[:8]) and ma.endswith(VAN_TAY_GIA[-8:])
    assert VAN_TAY_GIA[8:-8] not in ma  # phần giữa không lộ


def test_ve_hop_le_het_han_gia_mao_sai_module():
    from app import chung_chi_tls
    ve = chung_chi_tls.tao_ve("mikrotik-routeros", "192.0.2.1", 8729)
    assert chung_chi_tls.doc_ve(ve, "mikrotik-routeros") == ("192.0.2.1", 8729)
    assert chung_chi_tls.doc_ve(ve, "zte-e2633") is None
    assert chung_chi_tls.doc_ve(ve[:-2] + "xx", "mikrotik-routeros") is None
    ve_het = chung_chi_tls.tao_ve("mikrotik-routeros", "192.0.2.1", 8729, ttl_giay=-1)
    assert chung_chi_tls.doc_ve(ve_het, "mikrotik-routeros") is None


def test_do_van_tay_bang_chung_chi_may_chu(may_chu_tls, chung_chi_a):
    from app import chung_chi_tls
    srv, _ = may_chu_tls(chung_chi_a)
    port = srv.server_address[1]
    assert chung_chi_tls.do_van_tay("127.0.0.1", port) == chung_chi_a["van_tay"]


# ───────── Mikrotik: đề nghị tin ─────────

def _cau_hinh_mikrotik(port, *, kiem_chung_chi="true", van_tay=""):
    return {"dia_chi": f"127.0.0.1:{port}", "tai_khoan": NGUOI_DUNG_GIA,
            "mat_khau": MAT_KHAU_GIA, "kiem_chung_chi_tls": kiem_chung_chi,
            "van_tay_tls": van_tay, "cho_phep_khong_ma_hoa": "false"}


def test_mikrotik_van_tay_sai_de_nghi_tin_lai(may_chu_tls, chung_chi_a):
    srv, cfg = may_chu_tls(chung_chi_a)
    kq = asyncio.run(_module_mikrotik().cai(
        _cau_hinh_mikrotik(srv.server_address[1], van_tay="00" * 32)))
    assert kq["ok"] is False
    de_nghi = kq.get("tin_chung_chi")
    assert de_nghi is not None
    assert de_nghi["ly_do"] == "doi"
    assert "đã đổi" in de_nghi["thong_bao"]
    assert cfg["da_nhan"] == []  # sai vân tay → đóng trước khi đăng nhập


def test_mikrotik_tat_kiem_thi_khong_de_nghi(may_chu_tls, chung_chi_a):
    srv, _ = may_chu_tls(chung_chi_a)
    kq = asyncio.run(_module_mikrotik().cai(
        _cau_hinh_mikrotik(srv.server_address[1], kiem_chung_chi="false")))
    assert kq["ok"] is False
    assert "tin_chung_chi" not in kq  # người dùng đã chọn tắt kiểm → không đề nghị


# ───────── Lõi: hành động tin ─────────

class _SoGia:
    def __init__(self):
        self.da_goi = []

    async def cai(self, module_id, cau_hinh):
        self.da_goi.append((module_id, dict(cau_hinh)))
        return {"ok": True, "ket_qua_tung_khoa": [], "loi": None}


def test_tin_chung_chi_thanh_cong_luu_van_tay_do_duoc(may_chu_tls, chung_chi_a):
    from app import chung_chi_tls
    srv, _ = may_chu_tls(chung_chi_a)
    port = srv.server_address[1]
    ve = chung_chi_tls.tao_ve("mikrotik-routeros", "127.0.0.1", port)
    so = _SoGia()
    kq = asyncio.run(chung_chi_tls.tin_chung_chi(
        so, "mikrotik-routeros",
        {"dia_chi": f"127.0.0.1:{port}", "tai_khoan": NGUOI_DUNG_GIA,
         "mat_khau": MAT_KHAU_GIA},
        ve, chung_chi_tls.ma_hien_thi(chung_chi_a["van_tay"])))
    assert kq["ok"] is True
    assert len(so.da_goi) == 1
    assert so.da_goi[0][1]["van_tay_tls"] == chung_chi_a["van_tay"]


def test_tin_chung_chi_tu_choi_khi_chung_chi_doi(may_chu_tls, chung_chi_a, chung_chi_b):
    from app import chung_chi_tls
    srv, _ = may_chu_tls(chung_chi_a)
    port = srv.server_address[1]
    ve = chung_chi_tls.tao_ve("mikrotik-routeros", "127.0.0.1", port)
    ma_cu = chung_chi_tls.ma_hien_thi(chung_chi_a["van_tay"])
    srv.shutdown()
    srv.server_close()
    srv2, _ = may_chu_tls(chung_chi_b)  # cùng cổng? khác cổng → vé trỏ cổng cũ: đo thất bại
    # Đổi chứng chỉ trên ĐÚNG cổng của vé: dựng lại máy chủ khác trên cổng cũ.
    srv2.shutdown()
    srv2.server_close()
    srv3 = MayChuTlsTran(("127.0.0.1", port), _XuLyTlsTran)
    srv3.cfg = {"crt": chung_chi_b["crt"], "key": chung_chi_b["key"], "da_nhan": []}
    t = threading.Thread(target=srv3.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True)
    t.start()
    try:
        so = _SoGia()
        kq = asyncio.run(chung_chi_tls.tin_chung_chi(
            so, "mikrotik-routeros",
            {"dia_chi": f"127.0.0.1:{port}"}, ve, ma_cu))
        assert kq["ok"] is False
        assert so.da_goi == []  # chứng chỉ đổi → không lưu gì
    finally:
        srv3.shutdown()
        srv3.server_close()


def test_tin_chung_chi_tu_choi_ve_het_han(may_chu_tls, chung_chi_a):
    from app import chung_chi_tls
    srv, _ = may_chu_tls(chung_chi_a)
    port = srv.server_address[1]
    ve = chung_chi_tls.tao_ve("mikrotik-routeros", "127.0.0.1", port, ttl_giay=-1)
    so = _SoGia()
    kq = asyncio.run(chung_chi_tls.tin_chung_chi(
        so, "mikrotik-routeros", {"dia_chi": f"127.0.0.1:{port}"},
        ve, chung_chi_tls.ma_hien_thi(chung_chi_a["van_tay"])))
    assert kq["ok"] is False
    assert so.da_goi == []


# ───────── Ghép đề nghị vào kết quả công khai (lõi chỉ chuyển tiếp đúng trường) ─────────

def test_ghep_de_nghi_tin_khong_lo_host_cong_day_du():
    from app import chung_chi_tls
    kq = {"ok": False, "ket_qua_tung_khoa": [], "loi": "x",
          "tin_chung_chi": {"ly_do": "doi", "ma_hien_thi": "abcd1234…ef567890",
                           "thong_bao": "Chứng chỉ đã đổi.", "host": "192.168.1.10", "cong": 8729}}
    cong_khai = chung_chi_tls.ghep_de_nghi_tin(kq, "mikrotik-routeros")
    assert cong_khai is not None
    assert cong_khai["ly_do"] == "doi"
    assert cong_khai["ma_hien_thi"] == "abcd1234…ef567890"
    assert "ve" in cong_khai and cong_khai["ve"]
    assert "host" not in cong_khai and "cong" not in cong_khai
    assert chung_chi_tls.doc_ve(cong_khai["ve"], "mikrotik-routeros") == ("192.168.1.10", 8729)


def test_ghep_de_nghi_tin_tu_ky_khong_con_duoc_de_nghi():
    # Hướng 19:30: lần đầu tự ký đã tự ghim, không còn đề nghị tu_ky.
    from app import chung_chi_tls
    kq = {"ok": False, "ket_qua_tung_khoa": [], "loi": "x",
          "tin_chung_chi": {"ly_do": "tu_ky", "ma_hien_thi": "abcd1234…ef567890",
                           "thong_bao": "Router tự ký.", "host": "192.0.2.1", "cong": 8729}}
    assert chung_chi_tls.ghep_de_nghi_tin(kq, "mikrotik-routeros") is None


def test_ghep_de_nghi_tin_thieu_truong_thi_khong_ghep():
    from app import chung_chi_tls
    assert chung_chi_tls.ghep_de_nghi_tin({"ok": False}, "mikrotik-routeros") is None
    assert chung_chi_tls.ghep_de_nghi_tin(
        {"tin_chung_chi": {"ly_do": "tu_ky"}}, "mikrotik-routeros") is None


# ───────── ZTE: cùng cơ chế ─────────

# ───────── Mô tả trường cấu hình ─────────

def test_mo_ta_moi_truong_khong_mac_dinh_deu_co():
    for f in sorted((GOC / "modules").glob("*/manifest.json")):
        m = json.loads(f.read_text(encoding="utf-8"))
        for t in m.get("truong_cau_hinh") or []:
            if t.get("mac_dinh") is None or t.get("mac_dinh") == "":
                assert (t.get("mo_ta") or "").strip(), \
                    f"{m['id']}.{t['khoa']} không có mo_ta"


_THUAT_NGU_TRAN = ["SHA-256", "hex", "TLS", "API", "api-ssl", "/ip/", "http://"]


def test_mo_ta_mikrotik_zte_khong_thuat_ngu_tran():
    for module_id in ("mikrotik-routeros", "zte-e2633"):
        m = json.loads((GOC / "modules" / module_id / "manifest.json").read_text())
        for t in m.get("truong_cau_hinh") or []:
            mo_ta = t.get("mo_ta") or ""
            for tu in _THUAT_NGU_TRAN:
                assert tu not in mo_ta, f"{module_id}.{t['khoa']} còn thuật ngữ trần {tu!r}"
        van_tay = next(t for t in m["truong_cau_hinh"] if t["khoa"] == "van_tay_tls")
        assert "Tin chứng chỉ này" in van_tay["mo_ta"]
