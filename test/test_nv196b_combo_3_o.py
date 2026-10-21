"""NV196b — VÁ combo (app/combo.py): 3 ô từ ma trận NV196a.

Mỗi ô một test đỏ→xanh qua LÕI THẬT (`BoDieuPhoiCombo._chay`) bằng module GIẢ.
Ô1 (retry nhanh): test đỏ có sẵn ở test_nv196a_dinh_tuyen_combo.py
(`test_retry_nhanh_khi_rot_ket_noi_thoang_qua`); ở đây thêm các nhánh KHÔNG thử lại.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from app.combo import (
    TRAN_DUONG_TIEU_DE,
    BoDieuPhoiCombo,
    bo_cau_chi,
    bo_dinh_phien,
    bo_han_muc,
)
from app import nhat_ky
from app.loi_openai import LoiOpenAI

ENV_NGUONG = "WEB2API_COMBO_NGUONG"
ENV_NGHI = "WEB2API_COMBO_NGHI_GIAY"
ENV_TOI_DA = "WEB2API_COMBO_TOI_DA_GIAY"
ENV_DINH = "WEB2API_COMBO_DINH_GIAY"
ENV_THU_LAI_NHANH = "WEB2API_COMBO_THU_LAI_NHANH_GIAY"


class ModGia:
    """Module giả: kịch bản là danh sách các lượt trả lời.

    Mỗi mục: ("ok", than) | ("loi", ma, than, headers) | ("treo", giay) | ("raise", exc).
    Hết kịch bản → lặp lại mục cuối. Mọi lượt gọi được ghi vào `da_goi`.
    """

    def __init__(self, id_: str, tien_to: str, kich_ban: list[tuple], thu_tu: list | None = None):
        self.id = id_
        self.tien_to = tien_to
        self.kich_ban = list(kich_ban)
        self.da_goi: list[dict] = []
        self.luot = 0
        self.thu_tu = thu_tu

    async def chat(self, than: dict, stream: bool = False):
        self.da_goi.append({"model": than.get("model")})
        if self.thu_tu is not None:
            self.thu_tu.append(self.id)
        muc = self.kich_ban[min(self.luot, len(self.kich_ban) - 1)]
        self.luot += 1
        kieu = muc[0]
        if kieu == "ok":
            return 200, {"content-type": "application/json"}, b'{"lua_chon": "%s"}' % self.id.encode()
        if kieu == "loi":
            _, ma, than_loi, headers = muc
            return ma, headers, than_loi.encode("utf-8")
        if kieu == "treo":
            await asyncio.sleep(muc[1])
            return 200, {}, b"{}"
        if kieu == "raise":
            raise muc[1]
        raise AssertionError(f"kich ban la: {muc!r}")

    async def anh(self, than: dict):
        return await self.chat(than)


class RotChamGia:
    """Module giả rớt CHẬM: ngủ `giay` rồi ném ngoại lệ (không mã HTTP)."""

    def __init__(self, id_: str, tien_to: str, giay: float, exc: BaseException):
        self.id = id_
        self.tien_to = tien_to
        self.giay = giay
        self.exc = exc
        self.da_goi: list[dict] = []

    async def chat(self, than: dict, stream: bool = False):
        self.da_goi.append({"model": than.get("model")})
        await asyncio.sleep(self.giay)
        raise self.exc

    async def anh(self, than: dict):
        return await self.chat(than)


class StreamDutGia:
    """Module giả trả stream: 1 chunk rồi đứt (đã qua byte đầu → không thử lại)."""

    def __init__(self, id_: str, tien_to: str):
        self.id = id_
        self.tien_to = tien_to
        self.da_goi: list[dict] = []

    async def chat(self, than: dict, stream: bool = False):
        self.da_goi.append({"model": than.get("model")})

        async def gen():
            yield b'data: {"lua_chon": 1}\n\n'
            raise ConnectionError("dut giua chung")

        return 200, {"content-type": "text/event-stream"}, gen()

    async def anh(self, than: dict):
        return await self.chat(than)


def lam_combo(monkeypatch, ten: str, mods: list, **env) -> BoDieuPhoiCombo:
    """Dựng BoDieuPhoiCombo thật với phan_giai theo tiền tố (đúng cơ chế /v1)."""
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    anh_xa = {m.tien_to: (m.id, m) for m in mods}

    def phan_giai(model: str):
        tien_to, _, _ = model.partition("/")
        if tien_to not in anh_xa:
            raise LoiOpenAI(404, "khong co", code="model_not_found")
        return anh_xa[tien_to]

    combo = {"ten": ten, "mo_ta": "", "kha_nang": ["chat"],
             "buoc": [{"model": f"{m.tien_to}/m1", "bat": True} for m in mods]}
    return BoDieuPhoiCombo(ten, combo, None, None, phan_giai=phan_giai)


def chay(dieu_phoi: BoDieuPhoiCombo, user: str = "u1"):
    than = {"model": "combo/x", "user": user,
            "messages": [{"role": "user", "content": "xin chao"}]}
    return asyncio.run(dieu_phoi.chat(than))


def uid(i: int) -> str:
    """Tiền tố/id duy nhất cho mỗi test — singleton cầu chì/hạ bậc/dính phiên dùng chung."""
    return f"n196b{i}"


class GhiGia:
    """nhat_ky.ghi giả: ghi lại mọi lượt gọi để assert."""

    def __init__(self):
        self.goi: list[tuple] = []

    def __call__(self, kenh: str, muc: str, tom_tat: str, /, ms=None, **them):
        self.goi.append((kenh, muc, tom_tat, them))


@pytest.fixture
def bat_ghi(monkeypatch):
    gia = GhiGia()
    monkeypatch.setattr(nhat_ky, "ghi", gia)
    return gia


# ── Ô1: thử lại nhanh khi rớt kết nối thoáng qua ─────────────────────────
# (test đỏ→xanh chính ở test_nv196a_dinh_tuyen_combo.py::test_retry_nhanh_khi_rot_ket_noi_thoang_qua)


def test_o1_chi_thu_lai_dung_mot_lan(monkeypatch):
    # Lần thử lại cũng rớt → KHÔNG thử lần 3, sang bước kế (200 của B).
    u = uid(1)
    a = ModGia(f"{u}-a", f"{u}a", [("raise", ConnectionError("reset")),
                                   ("raise", ConnectionError("reset"))])
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb-o1-{u}", [a, b], **{ENV_DINH: 0})
    ma, _, _ = chay(cb)
    assert ma == 200 and len(b.da_goi) == 1
    assert len(a.da_goi) == 2, f"kỳ vọng thử lại ĐÚNG 1 lần (A gọi {len(a.da_goi)} lần)"


def test_o1_khong_thu_lai_khi_ngoai_le_co_ma_http(monkeypatch):
    # Ngoại lệ đã mang mã HTTP (LoiOpenAI) → không phải «rớt thoáng qua», không thử lại.
    u = uid(2)
    a = ModGia(f"{u}-a", f"{u}a", [("raise", LoiOpenAI(503, "nap lai", code="nap_lai")),
                                   ("ok", None)])
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb-o1-{u}", [a, b], **{ENV_DINH: 0})
    ma, _, _ = chay(cb)
    assert ma == 200 and len(b.da_goi) == 1
    assert len(a.da_goi) == 1, f"ngoại lệ có mã HTTP không được thử lại (A gọi {len(a.da_goi)} lần)"


def test_o1_khong_thu_lai_khi_rot_cham(monkeypatch):
    # Rớt CHẬM (vượt trần thu_lai_nhanh_giay) → không thử lại.
    u = uid(3)
    a = RotChamGia(f"{u}-a", f"{u}a", 0.25, ConnectionError("reset cham"))
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb-o1-{u}", [a, b],
                   **{ENV_DINH: 0, ENV_THU_LAI_NHANH: 0.05})
    ma, _, _ = chay(cb)
    assert ma == 200 and len(b.da_goi) == 1
    assert len(a.da_goi) == 1, f"rớt chậm không được thử lại (A gọi {len(a.da_goi)} lần)"


def test_o1_stream_khong_thu_lai_sau_byte_dau(monkeypatch):
    # Stream đã gửi byte đầu rồi mới đứt → không thử lại, trả sự kiện lỗi chuẩn.
    # (Một vòng asyncio.run duy nhất: generator stream tạo trong loop nào phải được
    #  đọc hết trong loop ấy — asyncio.run đóng loop sẽ aclose mọi async generator
    #  dở dang của loop đó, lần run thứ hai chỉ còn StopAsyncIteration.)
    u = uid(4)
    a = StreamDutGia(f"{u}-a", f"{u}a")
    cb = lam_combo(monkeypatch, f"cb-o1-{u}", [a], **{ENV_DINH: 0})
    than = {"model": "combo/x", "user": "u1",
            "messages": [{"role": "user", "content": "xin chao"}]}

    async def chay_va_doc():
        ma, _, noi_dung = await cb.chat(than, stream=True)
        assert ma == 200
        return [c async for c in noi_dung]

    cac_chunk = asyncio.run(chay_va_doc())
    assert len(a.da_goi) == 1, "đã gửi byte đầu thì không thử lại"
    assert any(b"stream_interrupted" in c for c in cac_chunk), cac_chunk


# ── Ô2: đường đi đầy đủ — x-web2api-duong + nhật ký ──────────────────────


def test_o2_tieu_de_duong_ghi_tung_buoc(monkeypatch):
    u = uid(5)
    bi_mat = "khoa_mat_" + "x" * 40  # thân lỗi NCC nhắc lại khoá → KHÔNG được lộ vào tiêu đề
    a = ModGia(f"{u}-a", f"{u}a", [("loi", 500, f'{{"error":{{"message":"{bi_mat}"}}}}', {})])
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb-o2-{u}", [a, b], **{ENV_DINH: 0})
    ma, headers, _ = chay(cb)
    assert ma == 200
    duong = headers.get("x-web2api-duong")
    assert duong, "thiếu tiêu đề x-web2api-duong"
    assert f"{u}a/m1:500:may_chu_loi" in duong, duong
    assert f"{u}b/m1:200:ok" in duong, duong
    assert "ms)" in duong, f"thiếu độ trễ từng chặng: {duong}"
    assert bi_mat not in duong, "LỘ thân lỗi của nhà cung cấp vào tiêu đề!"
    assert len(duong) <= TRAN_DUONG_TIEU_DE
    # x-web2api-route hiện có vẫn nguyên
    assert headers["x-web2api-route"] == f"{u}b/m1"


def test_o2_duong_co_tran_giu_chang_moi_nhat(monkeypatch):
    u = uid(6)
    mods = [ModGia(f"{u}-{i:02d}", f"{u}{i:02d}", [("loi", 500, "loi", {})])
            for i in range(100)]  # 100 chặng ≈ 3100 ký tự > trần 2048 → buộc phải cắt
    mods.append(ModGia(f"{u}-cuoi", f"{u}cuoi", [("ok", None)]))
    cb = lam_combo(monkeypatch, f"cb-o2-{u}", mods, **{ENV_DINH: 0, ENV_NGUONG: 1000})
    ma, headers, _ = chay(cb)
    assert ma == 200
    duong = headers["x-web2api-duong"]
    assert len(duong) <= TRAN_DUONG_TIEU_DE, len(duong)
    assert f"{u}cuoi/m1:200:ok" in duong, "trần cắt mất chặng mới nhất!"


def test_o2_nhat_ky_ghi_duong_di(monkeypatch, bat_ghi):
    u = uid(7)
    a = ModGia(f"{u}-a", f"{u}a", [("loi", 429, '{"error":{"message":"het"}}', {"retry-after": "60"})])
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb-o2-{u}", [a, b], **{ENV_DINH: 0})
    assert chay(cb)[0] == 200
    cac_dong = [g for g in bat_ghi.goi if g[0] == "combo" and "đường đi" in g[2]]
    assert cac_dong, [g[2] for g in bat_ghi.goi if g[0] == "combo"]
    tom_tat = cac_dong[-1][2]
    assert f"{u}a/m1" in tom_tat and f"{u}b/m1" in tom_tat, tom_tat
    assert "het_han_muc" in tom_tat, tom_tat


# ── Ô3: lọc sạch vì sức khoẻ → thử danh sách gốc; 503 chỉ khi cấu trúc rỗng ──


def test_o3_loc_sach_suc_khoe_thu_danh_sach_goc(monkeypatch, bat_ghi):
    # A: cầu chì nửa mở (được thăm dò); B: đang hạ bậc. Mọi bước đều yếu vì SỨC KHOẺ
    # → thử lại DANH SÁCH GỐC theo thứ tự gốc (A trước B), không 503.
    u = uid(8)
    thu_tu: list = []
    a = ModGia(f"{u}-a", f"{u}a", [("loi", 500, "loi", {}), ("ok", None)], thu_tu)
    b = ModGia(f"{u}-b", f"{u}b", [("loi", 500, "loi", {}), ("ok", None)], thu_tu)
    cb = lam_combo(monkeypatch, f"cb-o3-{u}", [a, b], **{ENV_DINH: 0, ENV_NGUONG: 100, ENV_NGHI: 1})
    bo_cau_chi.mo_ngay(f"{u}-a", "chat", 401, "khoa hong")  # cầu chì A mở…
    time.sleep(1.3)  # …hết giờ nghỉ (trần tối thiểu 1s) → nửa mở (được một thăm dò)
    assert bo_cau_chi.trang_thai(f"{u}-a", "chat") == "cho_tham_do"
    bo_han_muc.ha(f"{u}-b", f"{u}b/m1", "chat", 600, 429)  # B đang hạ bậc
    ma, _, _ = chay(cb)
    assert ma == 500  # cả hai đều 500 → trả lỗi cuối, nhưng ĐÃ THỬ cả hai
    assert thu_tu == [f"{u}-a", f"{u}-b"], f"lọc sạch sức khoẻ phải thử theo thứ tự gốc, được {thu_tu}"
    ly_do = [g[2] for g in bat_ghi.goi if g[0] == "combo"]
    assert any("lọc sạch → thử danh sách gốc" in t for t in ly_do), ly_do


def test_o3_503_khi_khong_con_buoc_hop_le(monkeypatch):
    # Combo rỗng thật / bước tắt / model không tồn tại → 503, không gọi gì.
    u = uid(9)
    a = ModGia(f"{u}-a", f"{u}a", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb-o3-{u}", [a], **{ENV_DINH: 0})
    cb.combo["buoc"][0]["bat"] = False  # công tắc tắt
    cb.combo["buoc"].append({"model": "khong-co/m9", "bat": True})  # không tồn tại
    with pytest.raises(LoiOpenAI) as ei:
        chay(cb)
    assert ei.value.ma == 503 and ei.value.than["error"]["code"] == "no_available_routes"
    assert not a.da_goi, "bước tắt TUYỆT ĐỐI không được gọi"


def test_o3_buoc_tat_khong_bao_gio_duoc_goi_khi_loc_sach(monkeypatch):
    # Lọc sạch vì sức khoẻ NHƯNG bước tắt vẫn không được gọi.
    u = uid(10)
    a = ModGia(f"{u}-a", f"{u}a", [("ok", None)])
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb-o3-{u}", [a, b], **{ENV_DINH: 0, ENV_NGHI: 600})
    cb.combo["buoc"][0]["bat"] = False  # A tắt
    bo_cau_chi.mo_ngay(f"{u}-b", "chat", 401, "khoa hong")  # B: cầu chì mở (sức khoẻ)
    ma, _, _ = chay(cb)
    assert ma == 200 and len(b.da_goi) == 1
    assert not a.da_goi, "bước tắt TUYỆT ĐỐI không được gọi, kể cả khi lọc sạch"
