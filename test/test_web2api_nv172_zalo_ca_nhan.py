"""Module zalo-ca-nhan — kênh Zalo cá nhân qua dịch vụ phụ Node (NV172).

- GỬI ra: chuẩn `kenh_ra` (`kenh_ra()` liệt kê hội thoại từ dịch vụ,
  `gui_tin(dich, noi_dung)` → POST /gui-tin, hỗ trợ tệp qua `_tep`).
- NHẬN vào: `hoi_vong()` hỏi dịch vụ /tin-moi → tin chuẩn cho lõi (chuẩn tin
  vào mục 12 CHUAN-MODULE); CHỈ nhận người trong `danh_sach_cho_phep`
  (mặc định RỖNG = không nhận ai); tin tự gửi bị bỏ.
- Mặc định TẮT; khoá Bearer dịch vụ không vào log (logging.Filter theo khuôn
  zalo-bot/van-phong); chứng chỉ Zalo do DỊCH VỤ giữ, module không bao giờ thấy.
- API không chính thức — README + manifest cảnh báo rủi ro khoá tài khoản.

Dịch vụ GIẢ cục bộ (threading HTTP trên 127.0.0.1) nói đúng hợp đồng của
dich-vu/zalo-ca-nhan; KHÔNG chạm Zalo thật.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import logging
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "zalo-ca-nhan"

KHOA_GIA = "khoa-gia-zalo-ca-nhan-0123456789abcdef"


def _module_py():
    p = THU_MUC / "module.py"
    spec = importlib.util.spec_from_file_location("zalo_ca_nhan_nv172", p)
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    return md


# ── dịch vụ GIẢ ─────────────────────────────────────────────────────────────

class MayChuGia:
    """Máy chủ Node GIẢ nói hợp đồng của dich-vu/zalo-ca-nhan."""

    def __init__(self, *, dang_nhap=True, khoa=KHOA_GIA, phien="phien-gia-1",
                 co_phien=True, tre_hop_thoai=0.0):
        self.khoa = khoa
        self.dang_nhap = dang_nhap
        # NV172-SUA Z2: dịch vụ thật trả kèm nhãn phiên (epoch) trong /tin-moi;
        # co_phien=False mô phỏng dịch vụ CŨ (chưa có nhãn) để kiểm đường dự phòng.
        self.phien = phien
        self.co_phien = co_phien
        self.tre_hop_thoai = tre_hop_thoai
        self.so_goi_hop_thoai = 0
        self.hop_thoai = [
            {"thread_id": "1111111111", "loai": 0, "ten": "Nguoi A"},
            {"thread_id": "2222222222", "loai": 1, "ten": "Nhom B"},
        ]
        self.tin = []  # [{seq, thread_id, loai, nguoi_gui, ten_tu_khai, van_ban, dinh_kem_loai, luc}]
        self.seq = 0
        self.da_gui = []
        self.so_luot_goi = 0
        self.han_gui = False  # bật → /gui-tin trả 429
        may = self

        class XuLy(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _tra(self, ma, obj):
                may.so_luot_goi += 1
                than = json.dumps(obj).encode()
                self.send_response(ma)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(than)))
                self.end_headers()
                self.wfile.write(than)

            def _auth(self):
                if self.headers.get("Authorization") != f"Bearer {may.khoa}":
                    self._tra(401, {"ok": False, "ma": "sai_khoa"})
                    return False
                return True

            def do_GET(self):
                if self.path.startswith("/qr/"):
                    self._tra(404, {"ok": False, "ma": "qr_het_han"})
                    return
                if not self._auth():
                    return
                if self.path == "/suc-khoe":
                    self._tra(200, {"ok": True, "zca_san_sang": True,
                                    "dang_nhap": may.dang_nhap, "so_tin_nhan": len(may.tin),
                                    "che_do": "gia"})
                elif self.path == "/hop-thoai":
                    may.so_goi_hop_thoai += 1
                    if may.tre_hop_thoai:
                        time.sleep(may.tre_hop_thoai)
                    if not may.dang_nhap:
                        self._tra(409, {"ok": False, "ma": "chua_dang_nhap"})
                    else:
                        self._tra(200, {"ok": True, "hop_thoai": may.hop_thoai})
                elif self.path.startswith("/tin-moi"):
                    tu = int(urllib.parse.parse_qs(
                        urllib.parse.urlsplit(self.path).query).get("tu", ["0"])[0] or 0)
                    tin = [t for t in may.tin if t["seq"] > tu]
                    than = {"ok": True, "tu_moi": may.seq, "tin": tin}
                    if may.co_phien:
                        than["phien"] = may.phien
                    self._tra(200, than)
                else:
                    self._tra(404, {"ok": False, "ma": "khong_co_route"})

            def do_POST(self):
                if not self._auth():
                    return
                dai = int(self.headers.get("Content-Length") or 0)
                than = self.rfile.read(dai) if dai else b"{}"
                try:
                    body = json.loads(than.decode() or "{}")
                except Exception:
                    self._tra(400, {"ok": False, "ma": "than_sai"})
                    return
                if self.path == "/tai-khoan/dang-nhap-qr":
                    self._tra(200, {"ok": True, "phien": "p1",
                                    "url_qr": "/qr/token-gia", "het_han_giay": 120})
                elif self.path == "/gui-tin":
                    if not may.dang_nhap:
                        self._tra(409, {"ok": False, "ma": "chua_dang_nhap"})
                    elif may.han_gui:
                        self._tra(429, {"ok": False, "ma": "gui_qua_nhanh"})
                    else:
                        may.da_gui.append(body)
                        self._tra(200, {"ok": True})
                elif self.path == "/dang-xuat":
                    may.dang_nhap = False
                    self._tra(200, {"ok": True})
                else:
                    self._tra(404, {"ok": False, "ma": "khong_co_route"})

        self._may_chu = ThreadingHTTPServer(("127.0.0.1", 0), XuLy)
        self.url = f"http://127.0.0.1:{self._may_chu.server_port}"
        self._luong = threading.Thread(target=self._may_chu.serve_forever, daemon=True)

    def __enter__(self):
        self._luong.start()
        return self

    def __exit__(self, *a):
        self._may_chu.shutdown()
        self._luong.join(timeout=5)

    def nhan_tin(self, **ghi):
        self.seq += 1
        tin = {"seq": self.seq, "loai": 0, "ten_tu_khai": None,
               "dinh_kem_loai": None, "luc": "2026-10-20T00:00:00Z"}
        tin.update(ghi)
        self.tin.append(tin)
        return tin

    def khoi_dong_lai(self, phien_moi=None):
        """Mô phỏng dịch vụ Node khởi động lại: đệm tin mất, seq về 0,
        nhãn phiên đổi (nếu cho phien_moi)."""
        self.tin = []
        self.seq = 0
        if phien_moi is not None:
            self.phien = phien_moi


# ── fixture ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def md():
    return _module_py()


@pytest.fixture()
def manifest(md):
    return json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


def _mod(md, manifest, may_chu, cho_phep=("1111111111",)):
    m = md.Module(manifest)
    m.nap({
        "bat": "1",
        "dich_vu_url": may_chu.url,
        "khoa_dich_vu": KHOA_GIA,
        "danh_sach_cho_phep": json.dumps(list(cho_phep)),
    })
    return m


def test_manifest_hop_le(md):
    from app.kham_pha import kiem_manifest
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    assert kiem_manifest(manifest) is None
    assert manifest["id"] == "zalo-ca-nhan"
    assert "kenh_ra" in manifest["cung_cap"]
    assert manifest["hang"] == "B"
    assert manifest["hoi_vong"]["chu_ky_giay"] == 5
    assert "không chính thức" in manifest["mo_ta"].lower()


def test_bat_tat_thi_khong_goi_mang(md, manifest):
    with MayChuGia() as may_chu:
        m = md.Module(manifest)
        m.nap({"bat": "0", "dich_vu_url": may_chu.url, "khoa_dich_vu": KHOA_GIA,
               "danh_sach_cho_phep": "[]"})
        assert asyncio.run(m.gui_tin({"kenh_id": "1111111111"}, {"van_ban": "x"}))["ma"] == "chua_bat"
        assert asyncio.run(m.hoi_vong()) == []
        assert m.kenh_ra() == []
        assert may_chu.so_luot_goi == 0, "module TẮT không được gọi mạng"


def test_cai_kiem_url_khoa_va_cho_phep(md, manifest):
    with MayChuGia() as may_chu:
        m = md.Module(manifest)
        # URL không loopback → từ chối NGAY khi kiểm cấu hình (chống SSRF
        # dịch vụ nội bộ — không được «vượt» sang bước ping mạng)
        r = asyncio.run(m.cai({"bat": "1", "dich_vu_url": "http://192.0.2.10:8703",
                               "khoa_dich_vu": KHOA_GIA}))
        assert r["ok"] is False
        assert r["ma"] == "cau_hinh_sai"
        # Bật nhưng thiếu khoá → từ chối, khoá chỉ bắt buộc khi bật
        r = asyncio.run(m.cai({"bat": "1", "dich_vu_url": may_chu.url, "khoa_dich_vu": ""}))
        assert r["ok"] is False
        # Khoá ngắn → từ chối
        r = asyncio.run(m.cai({"bat": "1", "dich_vu_url": may_chu.url,
                               "khoa_dich_vu": "ngan"}))
        assert r["ok"] is False
        # Tắt + không khoá → OK (không bắt khoá cho module tắt)
        r = asyncio.run(m.cai({"bat": "0", "dich_vu_url": may_chu.url, "khoa_dich_vu": ""}))
        assert r["ok"] is True
        # danh_sach_cho_phep sai JSON → từ chối
        r = asyncio.run(m.cai({"bat": "1", "dich_vu_url": may_chu.url,
                               "khoa_dich_vu": KHOA_GIA, "danh_sach_cho_phep": "khong-phai-json"}))
        assert r["ok"] is False
        # Bật + khoá đúng + dịch vụ trả lời → OK, cau_hinh_luu chuẩn hoá
        r = asyncio.run(m.cai({"bat": "1", "dich_vu_url": may_chu.url,
                               "khoa_dich_vu": KHOA_GIA,
                               "danh_sach_cho_phep": '["1111111111"]'}))
        assert r["ok"] is True
        assert r["cau_hinh_luu"]["dich_vu_url"] == may_chu.url
        assert r["cau_hinh_luu"]["danh_sach_cho_phep"] == '["1111111111"]'
        # Khoá không vào thông điệp lỗi/loại lỗi (cau_hinh_luu giữ nó như
        # zalo-bot giữ token — đó là kho lưu của quản trị, không phải phản hồi lỗi)
        assert KHOA_GIA not in (r.get("loi") or "")


def test_cai_bao_khi_dich_vu_khong_noi(md, manifest):
    m = md.Module(manifest)
    r = asyncio.run(m.cai({"bat": "1", "dich_vu_url": "http://127.0.0.1:9",
                           "khoa_dich_vu": KHOA_GIA}))
    assert r["ok"] is False
    assert r["ma"] == "khong_noi_duoc_dich_vu"


def test_kenh_ra_lay_tu_dich_vu(md, manifest):
    # NV172-SUA Z3: kenh_ra() theo hợp đồng lõi là hàm ĐỒNG BỘ nên chỉ đọc bộ
    # đệm — danh sách đích được làm mới ở các đường async (cai/hoi_vong/
    # gui_tin). Test cập nhật theo hành vi mới (bài học NV98): làm mới qua
    # một vòng hoi_vong rồi mới đọc kenh_ra.
    with MayChuGia() as may_chu:
        m = _mod(md, manifest, may_chu)
        asyncio.run(m.hoi_vong())
        dich = m.kenh_ra()
        assert {d["id"] for d in dich} == {"1111111111", "2222222222"}
        assert all("ten" in d and "chat_id" not in d and "token" not in d for d in dich)


def test_gui_tin_dich_sai_khong_goi_mang(md, manifest):
    with MayChuGia() as may_chu:
        m = _mod(md, manifest, may_chu)
        r = asyncio.run(m.gui_tin({"kenh_id": "khong-ton-tai"}, {"van_ban": "x"}))
        assert r == {"ok": False, "ma": "dich_sai"}
        assert may_chu.da_gui == [], "đích sai không được gọi dịch vụ"


def test_gui_tin_thieu_noi_dung(md, manifest):
    with MayChuGia() as may_chu:
        m = _mod(md, manifest, may_chu)
        r = asyncio.run(m.gui_tin({"kenh_id": "1111111111"}, {}))
        assert r["ma"] == "noi_dung_sai"
        assert may_chu.da_gui == []


def test_gui_tin_thanh_cong_va_tep(md, manifest):
    with MayChuGia() as may_chu:
        m = _mod(md, manifest, may_chu)
        r = asyncio.run(m.gui_tin({"kenh_id": "1111111111"}, {"van_ban": "chao"}))
        assert r == {"ok": True}
        assert may_chu.da_gui[-1]["thread_id"] == "1111111111"
        assert may_chu.da_gui[-1]["van_ban"] == "chao"
        # Gửi nhóm: loai=1
        r = asyncio.run(m.gui_tin({"kenh_id": "2222222222"}, {"van_ban": "nhom"}))
        assert r == {"ok": True}
        assert may_chu.da_gui[-1]["loai"] == 1
        # Gửi kèm tệp
        tep = {"ten": "a.png", "kieu": "image/png",
               "du_lieu": base64.b64encode(b"PNG").decode()}
        r = asyncio.run(m.gui_tin({"kenh_id": "1111111111"}, {"van_ban": "", "_tep": [tep]}))
        assert r == {"ok": True}
        assert may_chu.da_gui[-1]["tep"]["kieu"] == "image/png"


def test_gui_tin_han_toc_do(md, manifest):
    with MayChuGia() as may_chu:
        may_chu.han_gui = True
        m = _mod(md, manifest, may_chu)
        r = asyncio.run(m.gui_tin({"kenh_id": "1111111111"}, {"van_ban": "x"}))
        assert r["ma"] == "het_han_muc"


def test_gui_tin_khi_dich_vu_chua_dang_nhap(md, manifest):
    with MayChuGia(dang_nhap=False) as may_chu:
        m = _mod(md, manifest, may_chu)
        r = asyncio.run(m.gui_tin({"kenh_id": "1111111111"}, {"van_ban": "x"}))
        assert r["ma"] == "chua_dang_nhap"


def test_hoi_vong_chi_nhan_nguoi_cho_phep(md, manifest):
    with MayChuGia() as may_chu:
        m = _mod(md, manifest, may_chu, cho_phep=("1111111111",))
        may_chu.nhan_tin(thread_id="1111111111", nguoi_gui="1111111111",
                         ten_tu_khai="Nguoi A", van_ban="xin chao", luc="2026-10-20T00:01:00Z")
        may_chu.nhan_tin(thread_id="2222222222", nguoi_gui="9999999999",
                         ten_tu_khai="Nguoi La", van_ban="spam", luc="2026-10-20T00:02:00Z")
        tin = asyncio.run(m.hoi_vong())
        assert len(tin) == 1, "chỉ tin của người trong danh sách cho phép được nhận"
        t = tin[0]
        assert t["nguoi_gui"] == "1111111111"
        assert t["noi_gui"] == "1111111111"
        assert t["van_ban"] == "xin chao"
        assert t["la_nhom"] is False
        assert t["ten_tu_khai"] == "Nguoi A"
        assert t["luc"] == "2026-10-20T00:01:00Z"
        # NV172-SUA Z2: id_tin mang nhãn phiên của dịch vụ để không đụng
        # id của tin trước lần khởi động lại (chống trùng của lõi nuốt trùng).
        assert t["id_tin"] == "1111111111:phien-gia-1:1"
        # Lần hai không lặp tin cũ
        assert asyncio.run(m.hoi_vong()) == []


def test_hoi_vong_mac_dinh_rong_khong_nhan_ai(md, manifest):
    with MayChuGia() as may_chu:
        m = md.Module(manifest)
        m.nap({"bat": "1", "dich_vu_url": may_chu.url, "khoa_dich_vu": KHOA_GIA,
               "danh_sach_cho_phep": "[]"})
        may_chu.nhan_tin(thread_id="1111111111", nguoi_gui="1111111111",
                         van_ban="chao", luc="2026-10-20T00:01:00Z")
        assert asyncio.run(m.hoi_vong()) == []


def test_hoi_vong_bo_tin_sai_khuon(md, manifest):
    with MayChuGia() as may_chu:
        m = _mod(md, manifest, may_chu, cho_phep=("1111111111", "ABC!123"))
        # nguoi_gui CÓ trong danh sách cho phép nhưng chứa ký tự cấm
        # (MAU_NGUOI_GUI) → vẫn phải bỏ (kiểm khuôn, không tin allowlist)
        may_chu.nhan_tin(thread_id="1111111111", nguoi_gui="ABC!123", van_ban="x")
        # van_ban rỗng → bỏ
        may_chu.nhan_tin(thread_id="1111111111", nguoi_gui="1111111111", van_ban="   ")
        # van_ban quá dài → cắt ở TRAN_VAN_BAN
        may_chu.nhan_tin(thread_id="1111111111", nguoi_gui="1111111111",
                         van_ban="z" * 5000)
        tin = asyncio.run(m.hoi_vong())
        assert len(tin) == 1
        assert len(tin[0]["van_ban"]) <= 4000


def test_suc_khoe_khong_lo_bi_mat(md, manifest):
    with MayChuGia() as may_chu:
        m = _mod(md, manifest, may_chu)
        asyncio.run(m.hoi_vong())  # làm mới danh sách đích (đường async, Z3)
        sk = asyncio.run(m.suc_khoe())
        assert sk["ok"] is True
        assert sk["bat"] is True
        assert sk["dang_nhap"] is True
        assert sk["so_dich"] == 2
        assert KHOA_GIA not in json.dumps(sk, ensure_ascii=False)


def test_khoa_khong_vao_log(md, manifest, caplog):
    with MayChuGia() as may_chu:
        m = md.Module(manifest)
        m.nap({"bat": "1", "dich_vu_url": may_chu.url, "khoa_dich_vu": KHOA_GIA,
               "danh_sach_cho_phep": "[]"})
        with caplog.at_level(logging.DEBUG):
            asyncio.run(m.gui_tin({"kenh_id": "1111111111"}, {"van_ban": "x"}))
            logging.getLogger("httpx").warning("thu %s", f"Bearer {KHOA_GIA}")
    assert KHOA_GIA not in caplog.text, "khoá dịch vụ không được vào log"


# ── NV172-SUA: Z2 (khởi động lại dịch vụ), Z3 (kenh_ra chặn vòng lặp),
#    Z4-phụ (cai() phải nhận ra sai_khoa) ───────────────────────────────────

def test_z2_khoi_dong_lai_dich_vu_khong_mat_tin(md, manifest):
    """Dịch vụ khởi động lại (seq về 0, nhãn phiên đổi): module phải đặt lại
    con trỏ và nhận đủ tin mới — bản cũ giữ _seq cũ nên bỏ lặng lẽ."""
    with MayChuGia() as may_chu:
        m = _mod(md, manifest, may_chu, cho_phep=("1111111111",))
        for i in range(3):
            may_chu.nhan_tin(thread_id="1111111111", nguoi_gui="1111111111",
                             van_ban=f"truoc {i}", luc="2026-10-20T00:01:00Z")
        assert len(asyncio.run(m.hoi_vong())) == 3
        assert m._seq == 3
        may_chu.khoi_dong_lai(phien_moi="phien-gia-2")
        may_chu.nhan_tin(thread_id="1111111111", nguoi_gui="1111111111",
                         van_ban="sau restart 1", luc="2026-10-20T00:02:00Z")
        may_chu.nhan_tin(thread_id="1111111111", nguoi_gui="1111111111",
                         van_ban="sau restart 2", luc="2026-10-20T00:03:00Z")
        tin = asyncio.run(m.hoi_vong())
        assert [t["van_ban"] for t in tin] == ["sau restart 1", "sau restart 2"], \
            "tin sau khởi động lại không được mất lặng lẽ"
        assert tin[0]["id_tin"] == "1111111111:phien-gia-2:1"
        assert m._seq == 2
        assert asyncio.run(m.hoi_vong()) == []


def test_z2_seq_lui_khi_dich_vu_cu_khong_co_phien(md, manifest):
    """Dự phòng khi dịch vụ CŨ không trả nhãn phiên: tu_moi < _seq đang giữ
    ⇒ dịch vụ đã đặt lại seq ⇒ module cũng đặt lại, không bỏ tin."""
    with MayChuGia(co_phien=False) as may_chu:
        m = _mod(md, manifest, may_chu, cho_phep=("1111111111",))
        for i in range(3):
            may_chu.nhan_tin(thread_id="1111111111", nguoi_gui="1111111111",
                             van_ban=f"truoc {i}", luc="2026-10-20T00:01:00Z")
        assert len(asyncio.run(m.hoi_vong())) == 3
        may_chu.khoi_dong_lai()
        may_chu.nhan_tin(thread_id="1111111111", nguoi_gui="1111111111",
                         van_ban="sau restart", luc="2026-10-20T00:02:00Z")
        tin = asyncio.run(m.hoi_vong())
        assert [t["van_ban"] for t in tin] == ["sau restart"]
        assert m._seq == 1


def test_z3_kenh_ra_chi_doc_bo_dem_trong_ttl(md, manifest):
    """Trong hạn TTL, kenh_ra() + hoi_vong() không gọi lại /hop-thoai."""
    with MayChuGia() as may_chu:
        m = _mod(md, manifest, may_chu)
        asyncio.run(m.hoi_vong())
        assert may_chu.so_goi_hop_thoai == 1
        assert len(m.kenh_ra()) == 2
        assert len(m.kenh_ra()) == 2
        asyncio.run(m.hoi_vong())
        assert may_chu.so_goi_hop_thoai == 1, "bộ đệm còn tươi thì không gọi mạng lại"


def test_z3_kenh_ra_va_gui_tin_khong_chan_vong_lap(md, manifest):
    """Dịch vụ chậm 1,2 s ở /hop-thoai: kenh_ra() (đồng bộ) phải trả ngay từ
    bộ đệm; gui_tin chạm đường làm mới ASYNC không được đóng băng vòng lặp
    sự kiện (bản cũ gọi httpx.get đồng bộ tới 10 s mỗi lần)."""
    with MayChuGia(tre_hop_thoai=1.2) as may_chu:
        m = _mod(md, manifest, may_chu)

        t0 = time.monotonic()
        assert m.kenh_ra() == []
        assert time.monotonic() - t0 < 0.3, "kenh_ra không được chờ mạng"

        async def chay():
            dem_nhip = 0
            dung = asyncio.Event()

            async def nhip():
                nonlocal dem_nhip
                while not dung.is_set():
                    dem_nhip += 1
                    await asyncio.sleep(0.05)

            tac_vu_nhip = asyncio.create_task(nhip())
            # gui_tin vào đích có thật nhưng bộ đệm đang lạnh → phải làm mới
            # qua đường async (không chặn vòng lặp) rồi mới gửi được.
            kq = await m.gui_tin({"kenh_id": "1111111111"}, {"van_ban": "chao"})
            dung.set()
            await tac_vu_nhip
            return kq, dem_nhip

        kq, dem_nhip = asyncio.run(chay())
        assert kq == {"ok": True}
        assert dem_nhip >= 8, \
            f"vòng lặp bị chặn khi làm mới đích (chỉ {dem_nhip} nhịp trong ~1,2 s)"
        assert {d["id"] for d in m.kenh_ra()} == {"1111111111", "2222222222"}


def test_cai_voi_khoa_sai_bi_tu_choi(md, manifest):
    """Z4 (đuôi soát): cai() kiểm sống gặp 401 sai_khoa phải báo lỗi —
    bản cũ chỉ chặn mang_loi nên lưu cấu hình với khoá sai mà vẫn báo ok."""
    with MayChuGia(khoa="khoa-cua-dich-vu-khac-0123456789") as may_chu:
        m = md.Module(manifest)
        r = asyncio.run(m.cai({"bat": "1", "dich_vu_url": may_chu.url,
                               "khoa_dich_vu": KHOA_GIA}))
        assert r["ok"] is False
        assert r["ma"] == "sai_khoa"
