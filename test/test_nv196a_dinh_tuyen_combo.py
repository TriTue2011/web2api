"""NV196a — ĐO khoảng cách ĐỊNH TUYẾN + COMBO giữa c2a và web2api.

CHỈ ĐO, TUYỆT ĐỐI KHÔNG SỬA app/modules. Mỗi test chạy QUA LÕI THẬT
(`BoDieuPhoiCombo._chay`) bằng module GIẢ (nhà cung cấp giả).

Ánh xạ hành vi c2a (services/backend_router.py, provider_order.py,
provider_circuit.py, account_switch_resume.py, vòng combo trong
services/protocol/openai_v1_chat_complete.py) → web2api (app/combo.py,
đường /v1 qua app/tuyen.py::_module_theo_model).

Kết quả đo xem nv196a-ma-tran.md. Đỏ ở đây = khoảng cách thật, NV196b… sửa sau.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from app.combo import (
    BoDieuPhoiCombo,
    bo_cau_chi,
    bo_dinh_phien,
    bo_han_muc,
    doc_retry_after,
)
from app.loi_openai import LoiOpenAI

ENV_NGUONG = "WEB2API_COMBO_NGUONG"
ENV_NGHI = "WEB2API_COMBO_NGHI_GIAY"
ENV_TOI_DA = "WEB2API_COMBO_TOI_DA_GIAY"
ENV_DINH = "WEB2API_COMBO_DINH_GIAY"


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

    async def anh(self, than: dict):  # pragma: no cover - đo chat là chính
        return await self.chat(than)


def lam_combo(monkeypatch, ten: str, mods: list[ModGia], **env) -> BoDieuPhoiCombo:
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


def uid(monkeypatch, i: int) -> str:
    """Tiền tố/id duy nhất cho mỗi test — singleton cầu chì/hạ bậc/dính phiên dùng chung."""
    return f"n196a{i}"


# ── 1. Chọn theo thứ tự gốc ──────────────────────────────────────────────
def test_chon_theo_thu_tu_goc(monkeypatch):
    u = uid(monkeypatch, 1)
    a = ModGia(f"{u}-a", f"{u}a", [("ok", None)])
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb1-{u}", [a, b])
    ma, headers, _ = chay(cb)
    assert ma == 200
    assert len(a.da_goi) == 1 and not b.da_goi  # bước đầu khỏe → không chạm bước sau
    assert headers["x-web2api-route"] == f"{u}a/m1"


# ── 2. 429 lẻ → hạ bậc có hạn (tới hết Retry-After), không hạ vĩnh viễn ──
def test_429_le_ha_bac_co_han(monkeypatch):
    u = uid(monkeypatch, 2)
    a = ModGia(f"{u}-a", f"{u}a", [("loi", 429, '{"error":{"message":"het han muc"}}',
                                    {"retry-after": "60"})])
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb2-{u}", [a, b])
    ma, _, _ = chay(cb)
    assert ma == 200 and len(b.da_goi) == 1
    assert bo_han_muc.dang_ha(f"{u}-a", f"{u}a/m1", "chat")  # hạ có hạn…
    assert bo_cau_chi.trang_thai(f"{u}-a", "chat") == "dong"  # …nhưng cầu chì KHÔNG mở vì 1 cú 429


def test_het_retry_after_ve_cho_cu(monkeypatch):
    u = uid(monkeypatch, 3)
    a = ModGia(f"{u}-a", f"{u}a", [("loi", 429, '{"error":{"message":"x"}}', {"retry-after": "1"}),
                                   ("ok", None)])
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb3-{u}", [a, b])
    chay(cb)  # lượt 1: A 429 → B trả lời, A bị hạ 1 giây
    assert len(a.da_goi) == 1 and len(b.da_goi) == 1
    import time
    time.sleep(1.2)  # hết Retry-After
    chay(cb, user="khac")  # lượt 2 (phiên khác, tránh dính): A về vị trí gốc
    assert len(a.da_goi) == 2 and a.da_goi[-1]["model"] == f"{u}a/m1"


# ── 3. Mạch ngắt: lỗi liên tiếp → mở; 413 không tính; không chặn lựa chọn cuối ──
def test_mach_ngat_sau_loi_lien_tiep(monkeypatch):
    u = uid(monkeypatch, 4)
    thu_tu: list = []
    a = ModGia(f"{u}-a", f"{u}a", [("loi", 500, "loi may chu", {})], thu_tu)
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None), ("ok", None), ("ok", None),
                                   ("loi", 500, "b cung hong", {})], thu_tu)
    cb = lam_combo(monkeypatch, f"cb4-{u}", [a, b], **{ENV_NGUONG: 3, ENV_NGHI: 60, ENV_DINH: 0})
    for _ in range(3):
        chay(cb)  # A 500 ×3 → cầu chì (A, chat) mở; B 200
    assert bo_cau_chi.trang_thai(f"{u}-a", "chat") == "mo"
    thu_tu.clear()
    ma, _, _ = chay(cb)  # lượt 4: B cũng 500 → A (cầu chì mở) vẫn được thử SAU B, không chặn hẳn
    assert thu_tu == [f"{u}-b", f"{u}-a"], thu_tu
    assert ma == 500  # hết bước: trả lỗi cuối (của A)


def test_413_khong_tinh_loi_cau_chi(monkeypatch):
    u = uid(monkeypatch, 5)
    a = ModGia(f"{u}-a", f"{u}a", [("loi", 413, "than qua lon", {})])
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb5-{u}", [a, b], **{ENV_NGUONG: 1})
    for _ in range(3):
        ma, _, _ = chay(cb)
        assert ma == 200  # 413 → bỏ qua, B trả lời
    assert bo_cau_chi.trang_thai(f"{u}-a", "chat") == "dong"  # 413 không tính hỏng


def test_khong_chan_lua_chon_cuoi(monkeypatch):
    u = uid(monkeypatch, 6)
    a = ModGia(f"{u}-a", f"{u}a", [("loi", 500, "loi", {}), ("ok", None)])
    cb = lam_combo(monkeypatch, f"cb6-{u}", [a], **{ENV_NGUONG: 1, ENV_NGHI: 60, ENV_DINH: 0})
    chay(cb)  # 1 lỗi → cầu chì mở ngay (chỉ còn 1 bước)
    assert bo_cau_chi.trang_thai(f"{u}-a", "chat") == "mo"
    ma, _, _ = chay(cb)  # lựa chọn cuối vẫn được thử → 200
    assert ma == 200 and len(a.da_goi) == 2


def test_401_mo_cau_chi_ngay(monkeypatch):
    u = uid(monkeypatch, 7)
    a = ModGia(f"{u}-a", f"{u}a", [("loi", 401, '{"error":{"message":"khoa hong"}}', {})])
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb7-{u}", [a, b], **{ENV_NGUONG: 3, ENV_DINH: 0})
    ma, _, _ = chay(cb)
    assert ma == 200 and len(b.da_goi) == 1
    assert bo_cau_chi.trang_thai(f"{u}-a", "chat") == "mo"  # 401/403 mở NGAY, không đợi đủ ngưỡng


def test_nua_mo_chi_mot_tham_do(monkeypatch):
    import time
    u = uid(monkeypatch, 8)
    a = ModGia(f"{u}-a", f"{u}a", [("loi", 500, "loi", {}), ("ok", None)])
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb8-{u}", [a, b], **{ENV_NGUONG: 1, ENV_NGHI: 1, ENV_DINH: 0})
    chay(cb)
    assert bo_cau_chi.trang_thai(f"{u}-a", "chat") == "mo"
    time.sleep(1.2)  # hết giờ nghỉ → nửa mở
    assert bo_cau_chi.trang_thai(f"{u}-a", "chat") == "cho_tham_do"
    ma, _, _ = chay(cb)  # thăm dò 200 → đóng hẳn
    assert ma == 200
    assert bo_cau_chi.trang_thai(f"{u}-a", "chat") == "dong"


# ── 4. Phân loại lỗi: 400 phân xử; 404/501 bỏ qua ────────────────────────
def test_400_phan_xu_ha_bac_buoc_dau(monkeypatch):
    u = uid(monkeypatch, 9)
    a = ModGia(f"{u}-a", f"{u}a", [("loi", 400, '{"error":{"message":"yeu cau sai"}}', {})])
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb9-{u}", [a, b])
    ma, _, _ = chay(cb)
    assert ma == 200 and len(b.da_goi) == 1
    assert bo_han_muc.dang_ha(f"{u}-a", f"{u}a/m1", "chat")  # 400 của bước ấy → hạ bậc có hạn


def test_404_501_bo_qua_khong_tinh_hong(monkeypatch):
    u = uid(monkeypatch, 10)
    a = ModGia(f"{u}-a", f"{u}a", [("loi", 404, "khong thay", {}), ("loi", 501, "chua co", {})])
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb10-{u}", [a, b], **{ENV_NGUONG: 1})
    for _ in range(2):
        assert chay(cb)[0] == 200
    assert bo_cau_chi.trang_thai(f"{u}-a", "chat") == "dong"  # 404/501 không tính hỏng


# ── 5. Thử lại có trần: c2a thử lại NGAY 1 lần khi rớt kết nối thoáng qua ──
@pytest.mark.xfail(reason="khoảng trống đã đo (NV196a ô #6); sửa ở NV196b", strict=False)
def test_retry_nhanh_khi_rot_ket_noi_thoang_qua(monkeypatch):
    # c2a: _goi_mot_lan thử lại 1 lần nếu rớt đường truyền NHANH, không mã HTTP
    # (_THU_LAI_NHANH_GIAY = 10.0). Kỳ vọng web2api cũng thử lại bước ấy.
    u = uid(monkeypatch, 11)
    a = ModGia(f"{u}-a", f"{u}a", [("raise", ConnectionError("reset")), ("ok", None)])
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb11-{u}", [a, b], **{ENV_DINH: 0})
    ma, _, _ = chay(cb)
    assert ma == 200
    assert len(a.da_goi) == 2, (  # ĐỎ: web2api gọi A đúng 1 lần rồi sang B, không thử lại
        f"web2api không thử lại bước rớt kết nối thoáng qua (A gọi {len(a.da_goi)} lần, kỳ vọng 2)")


def test_tran_thoi_gian_tong_504(monkeypatch):
    u = uid(monkeypatch, 12)
    a = ModGia(f"{u}-a", f"{u}a", [("treo", 5.0)])
    cb = lam_combo(monkeypatch, f"cb12-{u}", [a], **{ENV_TOI_DA: 0.3})
    with pytest.raises(LoiOpenAI) as ei:
        chay(cb)
    assert ei.value.ma == 504 and ei.value.than["error"]["code"] == "combo_timeout"


# ── 6. Giữ phiên khi chuyển nhà cung cấp (dính phiên) ─────────────────────
def test_dinh_phien_giu_buoc_da_tra_loi(monkeypatch):
    u = uid(monkeypatch, 13)
    a = ModGia(f"{u}-a", f"{u}a", [("loi", 500, "loi", {})])
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb13-{u}", [a, b], **{ENV_DINH: 900})
    chay(cb, user="khach-x")  # A hỏng → B trả lời → dính (combo, chat, băm("khach-x")) → B
    assert len(b.da_goi) == 1
    a.da_goi.clear(); b.da_goi.clear()
    chay(cb, user="khach-x")  # cùng phiên → B lên trước, không đốt A
    assert len(b.da_goi) == 1 and not a.da_goi


def test_dinh_phien_evict_khi_khoa_hong(monkeypatch):
    u = uid(monkeypatch, 14)
    a = ModGia(f"{u}-a", f"{u}a", [("ok", None)])
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None),
                                    ("loi", 401, '{"error":{"message":"het phien"}}', {})])
    cb = lam_combo(monkeypatch, f"cb14-{u}", [a, b], **{ENV_DINH: 900, ENV_NGUONG: 3})
    chay(cb, user="khach-y")  # A trả lời → dính A
    b.da_goi.clear(); a.da_goi.clear()
    chay(cb, user="khach-y")  # A dính → A gọi trước, 200
    assert len(a.da_goi) == 1 and not b.da_goi


def test_phien_khac_khong_keo_buoc_dinh(monkeypatch):
    u = uid(monkeypatch, 15)
    a = ModGia(f"{u}-a", f"{u}a", [("loi", 500, "loi", {})])
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb15-{u}", [a, b], **{ENV_DINH: 900})
    chay(cb, user="khach-1")  # dính B cho khach-1
    a.da_goi.clear(); b.da_goi.clear()
    chay(cb, user="khach-2")  # phiên khác → thứ tự gốc: A trước
    assert len(a.da_goi) == 1  # A được thử trước (rồi hỏng, B trả lời)


# ── 7. Ghi vì sao chọn ───────────────────────────────────────────────────
def test_ghi_route_da_chon_tren_tieu_de(monkeypatch):
    u = uid(monkeypatch, 16)
    a = ModGia(f"{u}-a", f"{u}a", [("loi", 500, "loi", {})])
    b = ModGia(f"{u}-b", f"{u}b", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb16-{u}", [a, b])
    ma, headers, _ = chay(cb)
    assert ma == 200
    assert headers["x-web2api-route"] == f"{u}b/m1"  # module THẬT đã trả lời
    assert headers["x-web2api-combo"] == f"cb16-{u}"


# ── 8. Lọc sạch thành viên: web2api 503, c2a giữ nguyên để thử ────────────
def test_loc_sach_thanh_vien_503(monkeypatch):
    u = uid(monkeypatch, 17)
    a = ModGia(f"{u}-a", f"{u}a", [("ok", None)])
    cb = lam_combo(monkeypatch, f"cb17-{u}", [a])
    cb.combo["buoc"][0]["bat"] = False  # mọi bước tắt → không còn bước hợp lệ
    with pytest.raises(LoiOpenAI) as ei:
        chay(cb)
    # web2api: 503 no_available_routes. c2a route_combo: lọc sạch → GIỮ NGUYÊN danh sách gốc để thử.
    assert ei.value.ma == 503 and ei.value.than["error"]["code"] == "no_available_routes"
    assert not a.da_goi  # không bước nào bị gọi
