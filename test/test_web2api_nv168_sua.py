"""NV168-SUA — vòng sửa theo soát độc lập C2 (N1–N4 phía module).

- N1: MỌI đường chạm dữ liệu sinh trắc (dạy/nhận/đọc/đặt tên/bỏ qua/sự kiện)
  phải qua MỘT cổng chung `nhan_mat_bat`, đóng khi lỗi. Xoá là ngoại lệ có
  chủ ý: xoá dữ liệu phải luôn làm được (giảm dữ liệu, không tạo mới).
- N2: vòng đọc-sửa-ghi sổ mặt phải nguyên tử trước các lượt gọi bất đồng bộ
  chồng nhau (khoá bao trọn cả đoạn chờ dịch vụ ML): dạy song song không
  mất người, xoá xen giữa không bị «hồi sinh».
- N3: sự kiện KHÔNG lưu vector; `xoa_nguoi` xoá TRIỆT ĐỂ vector của người
  đó ở mọi bản sao còn lại trong sổ (kể cả sự kiện ghi trước vòng sửa).
- N4: cụm mặt lạ có TRẦN số + HẠN giữ (cấu hình), tự dọn khi lưu sổ; có
  hành động xoá riêng mặt lạ.

Dịch vụ ML GIẢ: nhãn ảnh b"MAT_<i>" → vector đơn vị ở chiều i (trực giao
từng đôi một) → mỗi nhãn là một người khác hẳn nhau.
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "nhin-nha"
KHOA = "k" * 24 + "sua-168"


def _vec_don_vi(i: int) -> bytes:
    v = [0.0] * 512
    v[i] = 1.0
    return struct.pack("<512f", *v)


def _mat_dict(vec: bytes, net: float = 20.0) -> dict:
    return {"hop": [10, 10, 130, 190], "diem_do": 0.9,
            "moc": [[40.0, 40.0], [80.0, 40.0], [60.0, 70.0],
                    [45.0, 100.0], [75.0, 100.0]],
            "vector_b64": base64.b64encode(vec).decode(),
            "net": net, "chuan": 20.0}


class MayChuMLGia:
    """Máy chủ giả của test_web2api_nv168 + cổng chặn /quet để xen lượt gọi."""

    def __init__(self):
        self.so_luot_quet = 0
        self.cong_quet = threading.Event()
        self.cong_quet.set()          # mở = trả lời ngay
        self.da_nhan_quet = threading.Event()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._tao_lop())
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._t = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._t.start()

    def _tao_lop(self):
        me = self

        class Lop(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _tra(self, ma, obj):
                body = json.dumps(obj).encode()
                self.send_response(ma)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _auth(self):
                return self.headers.get("Authorization") == f"Bearer {KHOA}"

            def do_GET(self):
                if not self._auth():
                    return self._tra(401, {"loi": "sai khoa"})
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
                n = int(self.headers.get("Content-Length", "0") or 0)
                than = json.loads(self.rfile.read(n).decode())
                me.so_luot_quet += 1
                me.da_nhan_quet.set()
                me.cong_quet.wait(timeout=10)
                nhan = base64.b64decode(str(than.get("anh_b64") or ""))
                mat = []
                if than.get("nhan_mat") and nhan.startswith(b"MAT_"):
                    mat = [_mat_dict(_vec_don_vi(int(nhan[4:])))]
                return self._tra(200, {"vat_the": [], "mat": mat})

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


def _module(may_chu):
    from app.kham_pha import doc_manifest, nap_ma, van_tay
    m, loi = doc_manifest(THU_MUC)
    assert loi is None, loi
    lop, _ = nap_ma(THU_MUC, m, van_tay(THU_MUC))
    inst = lop(m, _goc_kiem_thu=may_chu.url)
    from app.kho import kho_rieng
    inst.kho = kho_rieng("nhin-nha")
    return inst


def _cau_hinh(may_chu, **them):
    cfg = {"nhin_nha_bat": "1", "nhan_mat_bat": "1",
           "dich_vu_url": may_chu.url, "khoa_dich_vu": KHOA,
           "nguong_co_the": "40", "nguong_chac": "55"}
    cfg.update(them)
    return cfg


async def _nap(may_chu, **them):
    inst = _module(may_chu)
    r = await inst.cai(_cau_hinh(may_chu, **them))
    assert r["ok"] is True, r
    inst.nap(r["cau_hinh_luu"])
    return inst


def _anh(i: int) -> str:
    return base64.b64encode(f"MAT_{i}".encode()).decode()


async def _day(inst, ten, i):
    return await inst.thuc_hien("nhin_nha.so_mat", "day_mat",
                                {"ten": ten, "anh_b64": _anh(i)})


async def _nhan(inst, i):
    return await inst.thuc_hien("nhin_nha.nhan_dien", "nhan_mat_trong_anh",
                                {"anh_b64": _anh(i)})


async def _ds_nguoi(inst):
    kq = await inst.thuc_hien("nhin_nha.so_mat", "danh_sach_nguoi", {})
    assert kq["ok"] is True, kq
    return kq["trang_thai_moi"]["nguoi"]


# ── N1: cổng chung cho mọi đường chạm sinh trắc ─────────────────────────────

_HANH_DONG_SINH_TRAC = [
    ("danh_sach_nguoi", {}),
    ("day_mat", {"ten": "An", "anh_b64": ""}),
    ("doi_ten", {"nguoi_id": "x", "ten": "Bình"}),
    ("danh_sach_mat_la", {}),
    ("dat_ten_mat_la", {"cum_id": "x", "ten": "Bình"}),
    ("bo_qua_mat_la", {"cum_id": "x"}),
    ("su_kien_gan", {}),
]


def test_n1_tat_nhan_mat_chan_moi_hanh_dong_so_mat(may_chu):
    async def chinh():
        inst = await _nap(may_chu, nhan_mat_bat="0")
        for hd, ts in _HANH_DONG_SINH_TRAC:
            kq = await inst.thuc_hien("nhin_nha.so_mat", hd, ts)
            assert kq["ok"] is False and kq["loi"] == "chua_bat", (hd, kq)
        # day_mat bị chặn ở cổng TRƯỚC khi gọi dịch vụ ML
        assert may_chu.so_luot_quet == 0
    asyncio.run(chinh())


def test_n1_xoa_luon_lam_duoc_khi_tat(may_chu):
    async def chinh():
        inst = await _nap(may_chu)  # đang bật: dạy 1 người + 1 mặt lạ
        assert (await _day(inst, "An", 0))["ok"] is True
        assert (await _nhan(inst, 5))["ok"] is True
        tat = await _nap(may_chu, nhan_mat_bat="0")  # cùng kho, đã tắt
        nguoi = await _ds_nguoi_bang_doc_thao(tat)
        kq1 = await tat.thuc_hien("nhin_nha.so_mat", "xoa_nguoi",
                                  {"nguoi_id": nguoi[0]["id"]})
        assert kq1["ok"] is True, kq1
        kq2 = await tat.thuc_hien("nhin_nha.so_mat", "xoa_tat_ca", {})
        assert kq2["ok"] is True, kq2
    asyncio.run(chinh())


async def _ds_nguoi_bang_doc_thao(inst):
    return inst._doc_so()["nguoi"]


def test_n1_bat_len_thi_mo_lai(may_chu):
    async def chinh():
        inst = await _nap(may_chu)
        assert (await _day(inst, "An", 0))["ok"] is True
        assert len(await _ds_nguoi(inst)) == 1
    asyncio.run(chinh())


# ── N2: đọc-sửa-ghi nguyên tử trước lượt gọi chồng nhau ─────────────────────

def test_n2_day_song_song_khong_mat_nguoi(may_chu):
    async def chinh():
        inst = await _nap(may_chu)
        kqs = await asyncio.gather(*[
            _day(inst, f"Nguoi {i}", i) for i in range(5)])
        assert all(k["ok"] for k in kqs), kqs
        ten = sorted(n["ten"] for n in await _ds_nguoi(inst))
        assert ten == [f"Nguoi {i}" for i in range(5)], ten
    asyncio.run(chinh())


def test_n2_xoa_xen_giua_khong_hoi_sinh(may_chu):
    async def chinh():
        inst = await _nap(may_chu)
        assert (await _day(inst, "Nguoi A", 0))["ok"] is True
        may_chu.cong_quet.clear()
        may_chu.da_nhan_quet.clear()
        viec_day = asyncio.create_task(_day(inst, "Nguoi B", 1))
        # chờ lượt dạy B đã vào dịch vụ (đang giữ bản sổ cũ trong tay)
        for _ in range(100):
            if may_chu.da_nhan_quet.is_set():
                break
            await asyncio.sleep(0.02)
        assert may_chu.da_nhan_quet.is_set()
        viec_xoa = asyncio.create_task(
            inst.thuc_hien("nhin_nha.so_mat", "xoa_tat_ca", {}))
        await asyncio.sleep(0.3)
        may_chu.cong_quet.set()
        kq_day, kq_xoa = await asyncio.gather(viec_day, viec_xoa)
        assert kq_day["ok"] is True and kq_xoa["ok"] is True
        ten = [n["ten"] for n in await _ds_nguoi(inst)]
        assert "Nguoi A" not in ten, ten  # đã xoá là xoá hẳn, không hồi sinh
    asyncio.run(chinh())


# ── N3: xoá triệt để vector của người bị xoá ────────────────────────────────

def test_n3_su_kien_khong_luu_vector(may_chu):
    async def chinh():
        inst = await _nap(may_chu)
        assert (await _day(inst, "An", 0))["ok"] is True
        assert (await _nhan(inst, 0))["ok"] is True   # sự kiện «quen»
        assert (await _nhan(inst, 7))["ok"] is True   # sự kiện «la»
        so = inst._doc_so()
        assert so["su_kien"], "phải có sự kiện để kiểm"
        for e in so["su_kien"]:
            assert not e.get("vec_b64"), e
    asyncio.run(chinh())


def test_n3_xoa_nguoi_xoa_vector_khap_so(may_chu):
    async def chinh():
        inst = await _nap(may_chu)
        assert (await _day(inst, "An", 0))["ok"] is True
        assert (await _nhan(inst, 0))["ok"] is True
        vec_a = base64.b64encode(_vec_don_vi(0)).decode()
        # Giả lập dữ liệu CŨ (trước vòng sửa): sự kiện còn giữ vector.
        so = inst._doc_so()
        nguoi_id = so["nguoi"][0]["id"]
        so["su_kien"].append({"id": "cu123", "ts": time.time(), "camera": "",
                              "loai": "quen", "nguoi_id": nguoi_id,
                              "mat_la_id": None, "do_giong": 99.0,
                              "hop": [], "vec_b64": vec_a})
        inst._luu_so(so)
        kq = await inst.thuc_hien("nhin_nha.so_mat", "xoa_nguoi",
                                  {"nguoi_id": nguoi_id})
        assert kq["ok"] is True
        tho = json.dumps(inst._doc_so(), ensure_ascii=False)
        assert vec_a not in tho, "vector của người bị xoá còn sót trong sổ"
    asyncio.run(chinh())


# ── N4: mặt lạ có trần + hạn giữ + xoá riêng ────────────────────────────────

def test_n4_tran_so_cum_mat_la(may_chu):
    async def chinh():
        inst = await _nap(may_chu, mat_la_toi_da="3")
        for i in range(10, 15):  # 5 người lạ khác nhau
            assert (await _nhan(inst, i))["ok"] is True
        kq = await inst.thuc_hien("nhin_nha.so_mat", "danh_sach_mat_la", {})
        assert kq["ok"] is True
        assert len(kq["trang_thai_moi"]["mat_la"]) == 3, kq
    asyncio.run(chinh())


def test_n4_cum_qua_han_tu_don(may_chu):
    async def chinh():
        inst = await _nap(may_chu, mat_la_giu_ngay="30")
        assert (await _nhan(inst, 3))["ok"] is True
        so = inst._doc_so()
        assert len(so["mat_la"]) == 1
        so["mat_la"][0]["lan_cuoi"] = time.time() - 40 * 86400  # 40 ngày trước
        inst._luu_so(so)  # lưu sổ là phải tự dọn cụm quá hạn
        assert inst._doc_so()["mat_la"] == []
    asyncio.run(chinh())


def test_n4_xoa_mat_la_rieng(may_chu):
    async def chinh():
        inst = await _nap(may_chu)
        assert (await _nhan(inst, 3))["ok"] is True
        assert (await _nhan(inst, 4))["ok"] is True
        kq = await inst.thuc_hien("nhin_nha.so_mat", "danh_sach_mat_la", {})
        cum = kq["trang_thai_moi"]["mat_la"]
        assert len(cum) == 2
        kq2 = await inst.thuc_hien("nhin_nha.so_mat", "xoa_mat_la",
                                   {"cum_id": cum[0]["id"]})
        assert kq2["ok"] is True, kq2
        kq3 = await inst.thuc_hien("nhin_nha.so_mat", "danh_sach_mat_la", {})
        assert [c["id"] for c in kq3["trang_thai_moi"]["mat_la"]] == [cum[1]["id"]]
    asyncio.run(chinh())


# ── NV181: thuc_the/trang_thai phải async để lõi await được ─────────────────

def test_nv181_thuc_the_trang_thai_goi_duoc_bang_await(may_chu):
    async def chinh():
        inst = await _nap(may_chu)
        tts = await inst.thuc_the()
        assert len(tts) == 3
        tt = await inst.trang_thai(["nhin_nha.so_mat"])
        assert "nhin_nha.so_mat" in tt
    asyncio.run(chinh())
