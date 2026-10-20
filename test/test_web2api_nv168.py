"""Module nhin-nha (NV168): thị giác nhà — YOLO + nhận diện khuôn mặt qua dịch vụ phụ.

Mỗi test kèm đột biến (bảng trong nv168-bao-cao.md): đột biến tương ứng phải làm
test ĐỎ, mã thật XANH.

Dịch vụ ML GIẢ cục bộ (MayChuMLGia): mô phỏng đúng hợp đồng của
dich-vu/nhin-nha/server.py:
- GET /suc-khoe (Bearer bắt buộc) → {ok, yolo: {model, da_tai}, khuon_mat: {bo, da_tai}}
- POST /quet (Bearer bắt buộc) ← {anh_b64, nhan_mat} → {vat_the: [...], mat: [{hop, moc, vector_b64 (float32 512), net, chuan}]}
  Nội dung ảnh là nhãn định danh: b"MAT_A" / b"MAT_B" / b"MAT_C" / b"MAT_P34" / b"MAT_P36"
  / b"MAT_MO" (net thấp) / b"MAT_HAI" (2 mặt) / b"KHONG_MAT".
Dữ liệu sinh trắc: vector là bytes — KHÔNG bao giờ vào log/phản hồi/suc_khoe.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import os
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "nhin-nha"

KHOA_DICH_VU_GIA = "k" * 24 + "giua-de-du-dai"  # ≥24 ký tự, chỉ dùng cho test


# ── Vector giả (512 float32, đã chuẩn hoá) ────────────────────────────────────

def _vec(cac_cp: list[tuple[int, float]]) -> bytes:
    v = [0.0] * 512
    for i, x in cac_cp:
        v[i] = x
    chuan = math.sqrt(sum(x * x for x in v)) or 1.0
    return struct.pack("<512f", *[(x / chuan) for x in v])


def _cos(a: bytes, b: bytes) -> float:
    fa = struct.unpack("<512f", a)
    fb = struct.unpack("<512f", b)
    return sum(x * y for x, y in zip(fa, fb))


# A·B = 0.29 (độ giống 29/100); A·P34 = 0.34; A·P36 = 0.36; C trực giao với mọi mặt.
MAT_A = _vec([(0, 1.0)])
MAT_B = _vec([(0, 0.29), (1, math.sqrt(1 - 0.29 ** 2))])
MAT_C = _vec([(7, 1.0)])
MAT_P34 = _vec([(0, 0.34), (2, math.sqrt(1 - 0.34 ** 2))])
MAT_P36 = _vec([(0, 0.36), (2, math.sqrt(1 - 0.36 ** 2))])

for _a, _b, _ky_vong in ((MAT_A, MAT_B, 29.0), (MAT_A, MAT_P34, 34.0),
                         (MAT_A, MAT_P36, 36.0), (MAT_A, MAT_C, 0.0)):
    assert abs(_cos(_a, _b) * 100 - _ky_vong) < 0.05, (_ky_vong, _cos(_a, _b))

MAT_THEO_NHAN = {
    b"MAT_A": MAT_A, b"MAT_B": MAT_B, b"MAT_C": MAT_C,
    b"MAT_P34": MAT_P34, b"MAT_P36": MAT_P36,
}
MAT_NET = {b"MAT_MO": 1.0}  # mặt mờ: net thấp → dạy bị từ chối


def _mat_dict(vec: bytes, net: float = 20.0, chuan: float = 20.0, x: int = 10) -> dict:
    return {
        "hop": [x, 10, x + 120, 190], "diem_do": 0.9,
        "moc": [[40.0, 40.0], [80.0, 40.0], [60.0, 70.0], [45.0, 100.0], [75.0, 100.0]],
        "vector_b64": base64.b64encode(vec).decode(), "net": net, "chuan": chuan,
    }


# ── Máy chủ ML giả ──────────────────────────────────────────────────────────

class MayChuMLGia:
    """Dịch vụ ML giả trên loopback, ghi mọi lượt gọi để kiểm «không gọi khi tắt»."""

    def __init__(self):
        self.so_luot_quet = 0
        self.so_luot_suc_khoe = 0
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._tao_lop())
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._t = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._t.start()

    def _tao_lop(self):
        me = self

        class Lop(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _lay_than(self):
                n = int(self.headers.get("Content-Length", "0") or 0)
                return self.rfile.read(n)

            def _auth(self) -> bool:
                return self.headers.get("Authorization") == f"Bearer {KHOA_DICH_VU_GIA}"

            def _tra(self, ma: int, obj: dict):
                body = json.dumps(obj).encode()
                self.send_response(ma)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not self._auth():
                    return self._tra(401, {"loi": "sai khoa"})
                me.so_luot_suc_khoe += 1
                if self.path == "/suc-khoe":
                    return self._tra(200, {"ok": True,
                                           "yolo": {"model": "yolo26n", "da_tai": True},
                                           "khuon_mat": {"bo": "buffalo_s", "da_tai": True}})
                return self._tra(404, {"loi": "khong thay"})

            def do_POST(self):
                if not self._auth():
                    return self._tra(401, {"loi": "sai khoa"})
                if self.path != "/quet":
                    return self._tra(404, {"loi": "khong thay"})
                try:
                    than = json.loads(self._lay_than().decode())
                except Exception:
                    return self._tra(400, {"loi": "than sai"})
                anh_b64 = str(than.get("anh_b64") or "")
                if len(anh_b64) > 14_000_000:
                    return self._tra(413, {"loi": "anh qua lon"})
                nhan_mat = bool(than.get("nhan_mat", False))
                try:
                    nhan = base64.b64decode(anh_b64)
                except Exception:
                    nhan = b""
                me.so_luot_quet += 1
                mat = []
                if nhan_mat:
                    if nhan == b"MAT_HAI":
                        mat = [_mat_dict(MAT_A, x=10), _mat_dict(MAT_B, x=300)]
                    elif nhan in MAT_THEO_NHAN:
                        mat = [_mat_dict(MAT_THEO_NHAN[nhan])]
                    elif nhan in MAT_NET:  # mặt mờ: vector của A nhưng nét dưới ngưỡng
                        mat = [_mat_dict(MAT_A, net=MAT_NET[nhan])]
                vat_the = [{"nhan": "person", "ten": "người", "diem": 0.91,
                            "hop": [10, 10, 100, 200], "hop_ti_le": [0.05, 0.05, 0.5, 1.0],
                            "vi_tri": "giữa khung"}]
                return self._tra(200, {"vat_the": vat_the, "mat": mat})

        return Lop

    def close(self):
        self._server.shutdown()
        self._t.join(timeout=5)


@pytest.fixture(autouse=True)
def _du_lieu_tam(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))


@pytest.fixture()
def may_chu():
    s = MayChuMLGia()
    yield s
    s.close()


# ── Nạp module ─────────────────────────────────────────────────────────────

def _manifest():
    from app.kham_pha import doc_manifest
    m, loi = doc_manifest(THU_MUC)
    assert loi is None, loi
    return m


def _lop():
    from app.kham_pha import nap_ma, van_tay
    m = _manifest()
    lop, _ = nap_ma(THU_MUC, m, van_tay(THU_MUC))
    return lop, m


def _module(may_chu, **ghi_de):
    lop, m = _lop()
    inst = lop(m, _goc_kiem_thu=may_chu.url if may_chu else None)
    # Tay cầm kho riêng do lõi gắn (app/so_module.py); test tự gắn trên thư mục tạm.
    from app.kho import kho_rieng
    inst.kho = kho_rieng("nhin-nha")
    return inst


def _anh_b64(nhan: bytes) -> str:
    return base64.b64encode(nhan).decode()


def _cau_hinh(may_chu, nhan_mat_bat="0", nhin_nha_bat="1", khoa=KHOA_DICH_VU_GIA):
    return {"nhin_nha_bat": nhin_nha_bat, "nhan_mat_bat": nhan_mat_bat,
            "dich_vu_url": may_chu.url, "khoa_dich_vu": khoa,
            "nguong_co_the": "40", "nguong_chac": "55"}


async def _nap_on(may_chu, **ghi_de):
    inst = _module(may_chu)
    r = await inst.cai(_cau_hinh(may_chu, **ghi_de))
    assert r["ok"] is True, r
    inst.nap(r["cau_hinh_luu"])
    return inst


def _thuc_hien(inst, id_, hanh_dong, tham_so=None):
    return asyncio.run(inst.thuc_hien(id_, hanh_dong, tham_so or {}))


# ── A. Manifest và meta ─────────────────────────────────────────────────────

def test_manifest_hop_le():
    m = _manifest()
    assert m["id"] == "nhin-nha" and m["tien_to"] == "nhin_nha"
    assert m["hang"] in ("A", "B")
    assert "thiet_bi" in m["cung_cap"]


def test_moi_truong_bi_mat_co_mo_ta_lay_o_dau():
    m = _manifest()
    for truong in m["truong_cau_hinh"]:
        if truong.get("kieu") == "bi_mat":
            assert len(str(truong.get("mo_ta") or "")) > 20, truong["khoa"]


def test_tai_nguyen_null_khong_bia_so():
    m = _manifest()
    tn = m["tai_nguyen"]
    assert tn["dia_mb"] is None and tn["ram_them_mb"] is None
    assert tn["ghi_chu"] and len(tn["ghi_chu"]) > 20


def test_thuc_the_dung_tien_to(may_chu):
    inst = asyncio.run(_nap_on(may_chu))
    tts = asyncio.run(inst.thuc_the())
    assert len(tts) == 3
    for t in tts:
        assert t["id"].startswith("nhin_nha."), t["id"]
        assert t["rui_ro_mac_dinh"] in ("doc", "ghi", "nguy_hiem")


def test_suc_khoe_khong_lo_bi_mat(may_chu):
    inst = asyncio.run(_nap_on(may_chu))
    sk = asyncio.run(inst.suc_khoe())
    assert KHOA_DICH_VU_GIA not in json.dumps(sk, ensure_ascii=False)
    assert "khoa_dich_vu" not in json.dumps(sk, ensure_ascii=False)


# ── B. cài() và chống SSRF ──────────────────────────────────────────────────

def test_cai_tu_choi_ssrf_metadata(may_chu):
    inst = _module(may_chu)
    cfg = _cau_hinh(may_chu)
    cfg["dich_vu_url"] = "http://169.254.169.254:8703"
    r = asyncio.run(inst.cai(cfg))
    assert r["ok"] is False
    assert "chặn" in str(r["loi"]) or "SSRF" in str(r["loi"]), r["loi"]


def test_cai_tu_choi_ip_cong_khai(may_chu):
    inst = _module(may_chu)
    cfg = _cau_hinh(may_chu)
    cfg["dich_vu_url"] = "http://8.8.8.8:8703"
    r = asyncio.run(inst.cai(cfg))
    assert r["ok"] is False
    assert "chặn" in str(r["loi"]) or "SSRF" in str(r["loi"]), r["loi"]


def test_cai_tu_choi_khoa_yeu(may_chu):
    inst = _module(may_chu)
    cfg = _cau_hinh(may_chu, khoa="ngan")
    r = asyncio.run(inst.cai(cfg))
    assert r["ok"] is False and "24" in str(r["loi"]), r["loi"]


def test_cai_sai_khoa_khong_lo_khoa(may_chu):
    inst = _module(may_chu)
    r = asyncio.run(inst.cai(_cau_hinh(may_chu, khoa="s" * 24 + "sai-o-day")))
    assert r["ok"] is False
    assert "sai-o-day" not in json.dumps(r, ensure_ascii=False)


def test_cai_ok_voi_may_chu_gia(may_chu):
    inst = _module(may_chu)
    r = asyncio.run(inst.cai(_cau_hinh(may_chu)))
    assert r["ok"] is True
    assert r["cau_hinh_luu"]["dich_vu_url"] == may_chu.url


# ── C. quet_anh — hành vi, log, trần ────────────────────────────────────────

def test_tat_mac_dinh_khong_goi_mang(may_chu):
    inst = asyncio.run(_nap_on(may_chu, nhin_nha_bat="0"))
    kq = _thuc_hien(inst, "nhin_nha.mat", "quet_anh", {"anh_b64": _anh_b64(b"MAT_A")})
    assert kq["ok"] is False and kq["loi"] == "chua_bat"
    assert may_chu.so_luot_quet == 0


def test_quet_anh_tra_vat_the(may_chu):
    inst = asyncio.run(_nap_on(may_chu))
    kq = _thuc_hien(inst, "nhin_nha.mat", "quet_anh", {"anh_b64": _anh_b64(b"MAT_A")})
    assert kq["ok"] is True
    vt = kq["trang_thai_moi"]["vat_the"]
    assert vt[0]["nhan"] == "person" and vt[0]["ten"] == "người"
    assert kq["trang_thai_moi"]["nguon_ngoai"] is True


def test_anh_khong_vao_log(may_chu, caplog):
    dau_anh = "QUANG-VAN-BAN-ANH-" + "Z" * 4000
    inst = asyncio.run(_nap_on(may_chu))
    with caplog.at_level(logging.DEBUG):
        _thuc_hien(inst, "nhin_nha.mat", "quet_anh", {"anh_b64": _anh_b64(dau_anh.encode())})
        logging.getLogger("web2api.module.nhin_nha").info("kiem %s", "ok")
    assert dau_anh[:200] not in caplog.text


def test_anh_qua_lon_bi_chan(may_chu):
    inst = asyncio.run(_nap_on(may_chu))
    lon = base64.b64encode(b"\xff" * (11 * 1024 * 1024)).decode()
    kq = _thuc_hien(inst, "nhin_nha.mat", "quet_anh", {"anh_b64": lon})
    assert kq["ok"] is False


def test_dich_vu_chet_khong_treo(may_chu):
    inst = asyncio.run(_nap_on(may_chu))
    inst.nap({**_cau_hinh(may_chu), "dich_vu_url": "http://127.0.0.1:9"})
    kq = _thuc_hien(inst, "nhin_nha.mat", "quet_anh", {"anh_b64": _anh_b64(b"MAT_A")})
    assert kq["ok"] is False and "mang" in kq["loi"]


# ── D. Sổ khuôn mặt ─────────────────────────────────────────────────────────

def _day(inst, ten, nhan):
    return _thuc_hien(inst, "nhin_nha.so_mat", "day_mat",
                      {"ten": ten, "anh_b64": _anh_b64(nhan)})


def test_day_mat_tao_nguoi(may_chu):
    inst = asyncio.run(_nap_on(may_chu, nhan_mat_bat="1"))
    kq = _day(inst, "Nguyễn Văn An", b"MAT_A")
    assert kq["ok"] is True and kq["trang_thai_moi"]["nguoi_moi"] is True
    # vector KHÔNG ra phản hồi
    assert "vector" not in json.dumps(kq["trang_thai_moi"], ensure_ascii=False).lower()


def test_ten_sach_ky_tu_dieu_khien(may_chu):
    inst = asyncio.run(_nap_on(may_chu, nhan_mat_bat="1"))
    kq = _day(inst, "An\x00\x1b<img>", b"MAT_A")
    assert kq["ok"] is True
    kq2 = _thuc_hien(inst, "nhin_nha.so_mat", "danh_sach_nguoi", {})
    ten = kq2["trang_thai_moi"]["nguoi"][0]["ten"]
    assert "\x00" not in ten and "\x1b" not in ten
    json.dumps(kq2)  # JSON an toàn


def test_mat_mo_khong_day_duoc(may_chu):
    inst = asyncio.run(_nap_on(may_chu, nhan_mat_bat="1"))
    kq = _day(inst, "Người Mờ", b"MAT_MO")
    assert kq["ok"] is False


def test_anh_nhieu_mat_khong_day_duoc(may_chu):
    inst = asyncio.run(_nap_on(may_chu, nhan_mat_bat="1"))
    kq = _day(inst, "Người Đôi", b"MAT_HAI")
    assert kq["ok"] is False


def test_vector_khong_vao_log_khi_day(may_chu, caplog):
    inst = asyncio.run(_nap_on(may_chu, nhan_mat_bat="1"))
    dau = base64.b64encode(MAT_A).decode()[:60]
    with caplog.at_level(logging.DEBUG):
        _day(inst, "An", b"MAT_A")
        logging.getLogger("httpx").warning("thu nghiem")
    assert dau not in caplog.text


def test_nguong_tu_chinh_theo_du_lieu(may_chu):
    """c2a: ngưỡng «có thể» = min(cấu hình, max(30, cặp-khác-người cao nhất + 6)).
    A·B = 29 → ngưỡng hiệu lực 35."""
    inst = asyncio.run(_nap_on(may_chu, nhan_mat_bat="1"))
    assert _day(inst, "An", b"MAT_A")["ok"] is True
    assert _day(inst, "Bình", b"MAT_B")["ok"] is True
    # 34/100 → người lạ; 36/100 → «có thể là»
    inst2 = asyncio.run(_nap_on(may_chu, nhan_mat_bat="1"))
    kq_la = _thuc_hien(inst2, "nhin_nha.nhan_dien", "nhan_mat_trong_anh",
                       {"anh_b64": _anh_b64(b"MAT_P34")})
    kq_co_the = _thuc_hien(inst2, "nhin_nha.nhan_dien", "nhan_mat_trong_anh",
                           {"anh_b64": _anh_b64(b"MAT_P36")})
    assert [m["loai"] for m in kq_la["trang_thai_moi"]["mat"]] == ["la"]
    assert [m["loai"] for m in kq_co_the["trang_thai_moi"]["mat"]] == ["co_the"]


def test_nhan_dien_mac_dinh_tat(may_chu):
    inst = asyncio.run(_nap_on(may_chu, nhan_mat_bat="0"))
    kq = _thuc_hien(inst, "nhin_nha.nhan_dien", "nhan_mat_trong_anh",
                    {"anh_b64": _anh_b64(b"MAT_A")})
    assert kq["ok"] is False and kq["loi"] == "chua_bat"


def test_nhan_dien_quen_va_la(may_chu):
    inst = asyncio.run(_nap_on(may_chu, nhan_mat_bat="1"))
    assert _day(inst, "An", b"MAT_A")["ok"] is True
    inst2 = asyncio.run(_nap_on(may_chu, nhan_mat_bat="1"))
    kq = _thuc_hien(inst2, "nhin_nha.nhan_dien", "nhan_mat_trong_anh",
                    {"anh_b64": _anh_b64(b"MAT_A")})
    assert kq["ok"] is True
    m = kq["trang_thai_moi"]["mat"][0]
    assert m["loai"] == "quen" and m["ten"] == "An" and m["do_giong"] >= 95
    # Không có trường vector/embedding trong kết quả
    assert "vector" not in json.dumps(kq["trang_thai_moi"], ensure_ascii=False).lower()
    kq2 = _thuc_hien(inst2, "nhin_nha.nhan_dien", "nhan_mat_trong_anh",
                     {"anh_b64": _anh_b64(b"MAT_C")})
    assert kq2["trang_thai_moi"]["mat"][0]["loai"] == "la"


def test_mat_la_gom_cum(may_chu):
    inst = asyncio.run(_nap_on(may_chu, nhan_mat_bat="1"))
    for _ in range(2):
        _thuc_hien(inst, "nhin_nha.nhan_dien", "nhan_mat_trong_anh",
                   {"anh_b64": _anh_b64(b"MAT_C")})
    kq = _thuc_hien(inst, "nhin_nha.so_mat", "danh_sach_mat_la", {})
    cum = kq["trang_thai_moi"]["mat_la"]
    assert len(cum) == 1 and cum[0]["so_lan"] == 2


def test_dat_ten_mat_la_thanh_nguoi(may_chu):
    inst = asyncio.run(_nap_on(may_chu, nhan_mat_bat="1"))
    _thuc_hien(inst, "nhin_nha.nhan_dien", "nhan_mat_trong_anh",
               {"anh_b64": _anh_b64(b"MAT_C")})
    cum = _thuc_hien(inst, "nhin_nha.so_mat", "danh_sach_mat_la", {})["trang_thai_moi"]["mat_la"]
    kq = _thuc_hien(inst, "nhin_nha.so_mat", "dat_ten_mat_la",
                    {"cum_id": cum[0]["id"], "ten": "Chị Lan"})
    assert kq["ok"] is True
    kq2 = _thuc_hien(inst, "nhin_nha.nhan_dien", "nhan_mat_trong_anh",
                     {"anh_b64": _anh_b64(b"MAT_C")})
    assert kq2["trang_thai_moi"]["mat"][0]["ten"] == "Chị Lan"


def test_xoa_nguoi_giu_su_kien_mat_ten(may_chu):
    inst = asyncio.run(_nap_on(may_chu, nhan_mat_bat="1"))
    _day(inst, "An", b"MAT_A")
    _thuc_hien(inst, "nhin_nha.nhan_dien", "nhan_mat_trong_anh",
               {"anh_b64": _anh_b64(b"MAT_A")})
    nguoi = _thuc_hien(inst, "nhin_nha.so_mat", "danh_sach_nguoi", {})["trang_thai_moi"]["nguoi"]
    kq = _thuc_hien(inst, "nhin_nha.so_mat", "xoa_nguoi", {"nguoi_id": nguoi[0]["id"]})
    assert kq["ok"] is True
    sk = _thuc_hien(inst, "nhin_nha.so_mat", "su_kien_gan", {})["trang_thai_moi"]["su_kien"]
    assert len(sk) >= 1 and all(e.get("nguoi_id") is None for e in sk)


def test_xoa_tat_ca(may_chu, tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    inst = asyncio.run(_nap_on(may_chu, nhan_mat_bat="1"))
    assert _day(inst, "An", b"MAT_A")["ok"] is True
    kq = _thuc_hien(inst, "nhin_nha.so_mat", "xoa_tat_ca", {})
    assert kq["ok"] is True
    kq2 = _thuc_hien(inst, "nhin_nha.so_mat", "danh_sach_nguoi", {})
    assert kq2["trang_thai_moi"]["nguoi"] == []


# ── E. Mã hoá khi nghỉ / quyền tệp ──────────────────────────────────────────

def test_ma_hoa_khi_co_khoa_chinh(may_chu, tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_KHOA_CHINH", base64.b64encode(os.urandom(32)).decode())
    inst = asyncio.run(_nap_on(may_chu, nhan_mat_bat="1"))
    assert _day(inst, "An", b"MAT_A")["ok"] is True
    from app.kho import thu_muc_du_lieu
    teps = list((thu_muc_du_lieu() / "bi_mat" / "mo-dun" / "nhin-nha").glob("*.json"))
    assert teps, "chưa ghi tệp sổ"
    raw = teps[0].read_bytes()
    assert raw.startswith(b"W2A1"), "phải mã hoá W2A1 khi có khoá chính"


def test_tep_0600_khi_khong_khoa_chinh(may_chu, tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.delenv("WEB2API_KHOA_CHINH", raising=False)
    inst = asyncio.run(_nap_on(may_chu, nhan_mat_bat="1"))
    assert _day(inst, "An", b"MAT_A")["ok"] is True
    from app.kho import thu_muc_du_lieu
    teps = list((thu_muc_du_lieu() / "bi_mat" / "mo-dun" / "nhin-nha").glob("*.json"))
    assert teps
    import stat
    assert stat.S_IMODE(teps[0].stat().st_mode) == 0o600


# ── F. Cổng thiet_bi chung ──────────────────────────────────────────────────

def test_thuc_the_la_404(may_chu):
    inst = asyncio.run(_nap_on(may_chu))
    kq = _thuc_hien(inst, "nhin_nha.khong_co", "quet_anh", {})
    assert kq["ok"] is False and "404" in kq["loi"]


def test_hanh_dong_la_400(may_chu):
    inst = asyncio.run(_nap_on(may_chu))
    kq = _thuc_hien(inst, "nhin_nha.mat", "bay_len_troi", {})
    assert kq["ok"] is False and "400" in kq["loi"]


def test_idor_tien_to_khac(may_chu):
    inst = asyncio.run(_nap_on(may_chu))
    kq = _thuc_hien(inst, "telegram-bot.x", "gui_tin", {})
    assert kq["ok"] is False


def test_trang_thai_mat_so_lieu(may_chu):
    inst = asyncio.run(_nap_on(may_chu, nhan_mat_bat="1"))
    assert _day(inst, "An", b"MAT_A")["ok"] is True
    tt = asyncio.run(inst.trang_thai(["nhin_nha.so_mat"]))
    assert tt["nhin_nha.so_mat"]["so_nguoi"] == 1


def test_bat_nhan_mat_ghi_nhat_ky(may_chu, monkeypatch):
    goi = []
    import app.nhat_ky as nk
    monkeypatch.setattr(nk, "ghi", lambda *a, **k: goi.append((a, k)))
    inst = _module(may_chu)
    r = asyncio.run(inst.cai(_cau_hinh(may_chu, nhan_mat_bat="1")))
    assert r["ok"] is True
    assert any(g[0][0] == "nhin-nha" for g in goi), "bật nhận mặt phải ghi nhật ký"
