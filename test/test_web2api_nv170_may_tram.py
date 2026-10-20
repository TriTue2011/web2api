"""Test NV170 — module `maytram` (điều khiển máy trạm từ xa qua agent, hạng B).

Test-first: chạy ĐỎ trước khi có module. Agent là GIẢ hoàn toàn trong tiến trình
(kênh nối cặp, không socket, không mạng); conftest chặn mọi đường ra Internet thật.
Không có dữ liệu thật của nhà chủ kho: tên máy/khoá đều GIẢ rõ ràng.
Mọi kịch bản có kết nối chạy trong MỘT asyncio.run (phiên + bơm kênh cùng vòng lặp).

Bảo mật bắt buộc theo spec (hàng rào an toàn BẮT BUỘC):
- Hạng B, tập lệnh ĐÓNG (không «chạy lệnh tuỳ ý» — exec/kill của c2a KHÔNG port).
- Xác thực hai chiều lõi↔agent (HMAC khoá riêng mỗi máy), chống phát lại
  (nonce + cửa sổ thời gian), thu hồi một máy không ảnh hưởng máy khác.
- Mọi lệnh qua phê duyệt của lõi: mọi thực thể rui_ro_mac_dinh = "nguy_hiem".
- Kết quả agent là DỮ LIỆU KHÔNG TIN: cắt trần, gắn nguon_ngoai/khong_tin.
- Mặc định TẮT (bat=false khi chưa cấu hình).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import time
import types
from pathlib import Path

import pytest

from app.kham_pha import kiem_manifest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "maytram"
TEN_GOI = "maytram_nv170_mod_test"

KHOA_GIA_2 = "bb" * 32
DUONG_DAN_GIA = ["/du-lieu-gia", "/du-lieu-gia/bao-cao"]


def chay(coro):
    return asyncio.run(coro)


def _tai_goi():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC)]
    sys.modules[TEN_GOI] = goi
    spec = importlib.util.spec_from_file_location(f"{TEN_GOI}.module", THU_MUC / "module.py")
    assert spec and spec.loader, "không đọc được module.py của maytram"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{TEN_GOI}.module"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return _tai_goi()


@pytest.fixture()
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _ngu_canh_quan_tri():
    """NV170-SUA (bài học NV98): module nay TỰ kiểm vai người gọi (M2) —
    test cũ gọi thuc_hien trực tiếp phải đặt ngữ cảnh như lõi thật vẫn làm
    (quản trị); assert của từng test giữ nguyên ý nghĩa."""
    from app import ngu_canh

    tok = ngu_canh.dat({"loai": "quan_tri", "id": "admin-test"}, nguon="nguoi")
    yield
    ngu_canh.khoi_phuc(tok)


async def _module_moi_a(mod, du_lieu, **cau_hinh):
    m = mod.Module({"id": "maytram", "tien_to": "maytram",
                    "ten": "Máy trạm", "hang": "A"})
    m._thu_muc_du_lieu = du_lieu / "du_lieu" / "maytram"
    ch = {"bat": "true", **cau_hinh}
    assert (await m.cai(ch))["ok"] is True
    m.nap(ch)
    return m


def _module_moi(mod, du_lieu, **cau_hinh):
    return chay(_module_moi_a(mod, du_lieu, **cau_hinh))


class _DauKenh:
    """Một đầu của kênh nối cặp (giả lập kênh hai chiều trong tiến trình)."""

    def __init__(self, gui_vao, nhan_tu):
        self._gui = gui_vao
        self._nhan = nhan_tu
        self.da_gui = []

    async def gui(self, obj: dict):
        self.da_gui.append(obj)
        await self._gui.put(dict(obj))

    async def nhan(self):
        return await self._nhan.get()


def _kenh_kep():
    a = asyncio.Queue()
    b = asyncio.Queue()
    return _DauKenh(b, a), _DauKenh(a, b)  # (phía lõi, phía agent)


class _NhiemVu:
    """Gói hai task (agent giả + bơm kênh của lõi) — cancel() dọn cả hai."""

    def __init__(self, *tasks):
        self._tasks = tasks

    def cancel(self):
        for t in self._tasks:
            t.cancel()


async def _noi(m, ten, khoa_hex, quyen=None, ket_qua_gia=None, ngoai_tru=None,
               duong_dan_agent=None, **tham_so_agent):
    """Bắt tay thật giữa lõi (module) và agent GIẢ qua bơm kênh (async, cùng vòng lặp).

    Trả (phiên, nhiệm vụ). Ném lỗi bắt tay (BatTayLoi) nếu bị từ chối.
    `ngoai_tru`: tập phiên (theo id đối tượng) KHÔNG tính là đã nối — dùng khi
    kiểm nối lại: phiên cũ còn trong sổ nhưng phải chờ phiên MỚI thay thế.
    """
    mod = sys.modules[TEN_GOI + ".module"]
    kenh_loi, kenh_agent = _kenh_kep()
    ag = mod.AgentGia(khoa_hex, duong_dan_agent or DUONG_DAN_GIA,
                      quyen or {}, ket_qua_gia or {},
                      ten=ten, **tham_so_agent)
    t_agent = asyncio.ensure_future(ag.chay(kenh_agent))
    t_loi = asyncio.ensure_future(m._phien.chay_kenh(kenh_loi))
    try:
        for _ in range(500):  # chờ tối đa ~5 giây
            phien = m._phien.lay(ten)
            if phien is not None and id(phien) not in (ngoai_tru or set()):
                return phien, _NhiemVu(t_agent, t_loi)
            if t_loi.done():
                loi = t_loi.exception()
                if loi is not None:
                    raise loi
                raise RuntimeError("lõi đóng kênh trước khi có phiên")
            await asyncio.sleep(0.01)
        raise RuntimeError("hết giờ chờ bắt tay")
    except Exception:
        t_agent.cancel()
        t_loi.cancel()
        raise


async def _dang_ky(m, ten, **quyen):
    kq = await m.thuc_hien("maytram.dang_ky", "dang_ky",
                           {"ten_may": ten, "duong_dan": DUONG_DAN_GIA, **quyen})
    assert kq["ok"] is True, kq
    return kq["khoa_agent"]


# ── quản lý thiết bị (không kết nối) ─────────────────────────────────────────

def test_dang_ky_tra_khoa_mot_lan(mod, du_lieu):
    m = _module_moi(mod, du_lieu)
    kq = chay(m.thuc_hien("maytram.dang_ky", "dang_ky",
                          {"ten_may": "may-gia-1", "duong_dan": DUONG_DAN_GIA}))
    assert kq["ok"] is True
    khoa = kq["khoa_agent"]
    assert len(khoa) == 64 and all(c in "0123456789abcdef" for c in khoa)
    # máy KHÁC đăng ký vẫn được khoá của mình (trả đúng một lần)
    khoa2 = chay(m.thuc_hien("maytram.dang_ky", "dang_ky",
                             {"ten_may": "may-gia-1b",
                              "duong_dan": DUONG_DAN_GIA}))["khoa_agent"]
    assert len(khoa2) == 64 and khoa2 != khoa
    # nhưng khoá KHÔNG đọc lại được qua mọi đường đọc (danh sách/thực thể)
    ds = m._so.danh_sach()
    assert [d["ten"] for d in ds] == ["may-gia-1", "may-gia-1b"]
    assert all("khoa_agent" not in d for d in ds)
    assert khoa not in json.dumps(chay(m.thuc_the(None)), ensure_ascii=False)


def test_dang_ky_trung_ten_bi_tu_choi(mod, du_lieu):
    m = _module_moi(mod, du_lieu)
    chay(m.thuc_hien("maytram.dang_ky", "dang_ky",
                     {"ten_may": "may-gia-1", "duong_dan": DUONG_DAN_GIA}))
    kq = chay(m.thuc_hien("maytram.dang_ky", "dang_ky",
                          {"ten_may": "may-gia-1", "duong_dan": DUONG_DAN_GIA}))
    assert kq["ok"] is False and "tồn tại" in kq["loi"]


def test_dang_ky_thieu_duong_dan_bi_tu_choi_fail_closed(mod, du_lieu):
    m = _module_moi(mod, du_lieu)
    for dd in ([], None, [""]):
        kq = chay(m.thuc_hien("maytram.dang_ky", "dang_ky",
                              {"ten_may": "may-gia-x", "duong_dan": dd}))
        assert kq["ok"] is False, f"phải từ chối duong_dan={dd!r}"


def test_dang_ky_ten_sai_khuon_bi_tu_choi(mod, du_lieu):
    m = _module_moi(mod, du_lieu)
    for ten in ["May Gia", "../x", "a", "x" * 40, "", "may;rm"]:
        kq = chay(m.thuc_hien("maytram.dang_ky", "dang_ky",
                              {"ten_may": ten, "duong_dan": DUONG_DAN_GIA}))
        assert kq["ok"] is False, f"phải từ chối ten_may={ten!r}"


def test_tap_dong_khong_co_exec_kill(mod, du_lieu):
    m = _module_moi(mod, du_lieu)
    chay(m.thuc_hien("maytram.dang_ky", "dang_ky",
                     {"ten_may": "may-gia-1", "duong_dan": DUONG_DAN_GIA}))
    hanh_dong = {t["hanh_dong"] if isinstance(t, dict) else t
                 for e in chay(m.thuc_the(None)) for t in e["tinh_nang"]}
    assert "exec" not in hanh_dong and "kill" not in hanh_dong
    for hd in ("exec", "kill", "lenh_tuy_y"):
        kq = chay(m.thuc_hien("maytram.may-gia-1", hd, {}))
        assert kq["ok"] is False and "không hỗ trợ" in kq["loi"]


def test_xoa_thiet_bi_mat_khoi_so(mod, du_lieu):
    m = _module_moi(mod, du_lieu)
    chay(m.thuc_hien("maytram.dang_ky", "dang_ky",
                     {"ten_may": "may-gia-1", "duong_dan": DUONG_DAN_GIA}))
    kq = chay(m.thuc_hien("maytram.dang_ky", "xoa",
                          {"ten_may": "may-gia-1", "go_tren_may": False}))
    assert kq["ok"] is True
    assert m._so.lay("may-gia-1") is None
    assert all(e["id"] != "maytram.may-gia-1" for e in chay(m.thuc_the(None)))


def test_thiet_bi_offline_bao_loi_ro(mod, du_lieu):
    m = _module_moi(mod, du_lieu)
    chay(m.thuc_hien("maytram.dang_ky", "dang_ky",
                     {"ten_may": "may-gia-1", "duong_dan": DUONG_DAN_GIA}))
    kq = chay(m.thuc_hien("maytram.may-gia-1", "liet_ke",
                          {"duong_dan": "/du-lieu-gia"}))
    assert kq["ok"] is False and "kết nối" in kq["loi"]


# ── bắt tay + phiên (kịch bản async trọn vòng lặp) ───────────────────────────

def test_thu_hoi_ngat_phien_va_khoa_chet(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        k1 = await _dang_ky(m, "may-gia-1")
        k2 = await _dang_ky(m, "may-gia-2")
        _, t1 = await _noi(m, "may-gia-1", k1)
        _, t2 = await _noi(m, "may-gia-2", k2)
        assert m._phien.lay("may-gia-1") is not None
        kq = await m.thuc_hien("maytram.dang_ky", "thu_hoi",
                               {"ten_may": "may-gia-1"})
        assert kq["ok"] is True
        assert m._phien.lay("may-gia-1") is None  # phiên bị ngắt
        assert m._phien.lay("may-gia-2") is not None  # máy khác không ảnh hưởng
        # khoá cũ không bắt tay lại được
        with pytest.raises(Exception):
            await _noi(m, "may-gia-1", k1)
        t1.cancel()
        t2.cancel()
    chay(kb())


def test_xoay_khoa_khoa_cu_chet_khoa_moi_song(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        cu = await _dang_ky(m, "may-gia-1")
        moi = (await m.thuc_hien("maytram.dang_ky", "xoay_khoa",
                                 {"ten_may": "may-gia-1"}))["khoa_agent"]
        assert moi != cu and len(moi) == 64
        with pytest.raises(Exception):
            await _noi(m, "may-gia-1", cu)
        _, t = await _noi(m, "may-gia-1", moi)  # khoá mới bắt tay được
        t.cancel()
    chay(kb())


def test_doi_quyen_co_hieu_luc_ngay(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        _, t = await _noi(m, "may-gia-1", khoa,
                          quyen={"ghi": True},
                          ket_qua_gia={"ghi_tep": {"ok": True}})
        kq = await m.thuc_hien("maytram.may-gia-1", "ghi_tep",
                               {"duong_dan": "/du-lieu-gia/a.txt",
                                "noi_dung": "x"})
        assert kq["ok"] is False and "quyền" in kq["loi"]
        await m.thuc_hien("maytram.dang_ky", "doi_quyen",
                          {"ten_may": "may-gia-1", "co_quyen_ghi": True})
        kq = await m.thuc_hien("maytram.may-gia-1", "ghi_tep",
                               {"duong_dan": "/du-lieu-gia/a.txt",
                                "noi_dung": "x"})
        assert kq["ok"] is True  # quyền mới có hiệu lực ngay
        t.cancel()
    chay(kb())


def test_bat_tay_dung_cho_phep_lenh(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        phien, t = await _noi(
            m, "may-gia-1", khoa,
            ket_qua_gia={"liet_ke": {"ok": True, "muc": ["a", "b"]}})
        assert m._phien.lay("may-gia-1") is phien
        kq = await m.thuc_hien("maytram.may-gia-1", "liet_ke",
                               {"duong_dan": "/du-lieu-gia"})
        assert kq["ok"] is True and kq["ket_qua"]["muc"] == ["a", "b"]
        assert kq["nguon_ngoai"] is True and kq["khong_tin"] is True
        t.cancel()
    chay(kb())


def test_bat_tay_khoa_sai_bi_tu_choi(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        await _dang_ky(m, "may-gia-1")
        with pytest.raises(Exception):
            await _noi(m, "may-gia-1", KHOA_GIA_2)
        assert m._phien.lay("may-gia-1") is None
    chay(kb())


def test_dap_sai_bi_loi_tu_choi(mod, du_lieu):
    """Agent qua được thách thức của lõi nhưng ĐÁP sai → lõi phải từ chối ngay
    (bắt đúng kiểm chứng `xac_thuc_agent` phía lõi, không nhờ agent tự sập)."""
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        with pytest.raises(Exception):
            await _noi(m, "may-gia-1", khoa, dap_sai=True)
        assert m._phien.lay("may-gia-1") is None
    chay(kb())


def test_phat_lai_nonce_bi_tu_choi(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        _, t = await _noi(m, "may-gia-1", khoa)
        nonce_da_dung = dict(m._phien._nonce_da_dung)
        assert nonce_da_dung, "phải ghi nhận nonce đã dùng"
        with pytest.raises(Exception):
            m._phien._kiem_nonce(next(iter(nonce_da_dung)))
        t.cancel()
    chay(kb())


def test_thoi_gian_lech_qua_cua_so_bi_tu_choi(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        kenh_loi, kenh_agent = _kenh_kep()
        ag = mod.AgentGia(khoa, DUONG_DAN_GIA, {}, {}, thoi_gian_lech=3600.0)
        task = asyncio.ensure_future(ag.chay(kenh_agent))
        with pytest.raises(Exception):
            await m._phien.nhan_agent(kenh_loi)
        task.cancel()
    chay(kb())


def test_agent_tu_choi_loi_gia_mao(mod, du_lieu):
    """Lõi chứng minh sai (khoá khác) → agent GIẢ từ chối nối, lõi hết giờ bắt tay."""
    async def kb():
        m = await _module_moi_a(mod, du_lieu, het_gio_bat_tay="1")
        khoa = await _dang_ky(m, "may-gia-1")
        kenh_loi, kenh_agent = _kenh_kep()
        ag = mod.AgentGia(KHOA_GIA_2, DUONG_DAN_GIA, {})  # agent giữ khoá KHÁC
        task = asyncio.ensure_future(ag.chay(kenh_agent))
        with pytest.raises(Exception):
            await m._phien.nhan_agent(kenh_loi)
        assert m._phien.lay("may-gia-1") is None
        task.cancel()
    chay(kb())


def test_noi_lai_phien_cu_thanh_zombie(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        cu, t1 = await _noi(m, "may-gia-1", khoa)
        moi, t2 = await _noi(m, "may-gia-1", khoa, ngoai_tru={id(cu)})
        assert m._phien.lay("may-gia-1") is moi
        # lệnh chờ trên phiên cũ trả lỗi ngay, không treo
        kq = await cu.gui_lenh("liet_ke", {"duong_dan": "/du-lieu-gia"})
        assert kq["ok"] is False
        t1.cancel()
        t2.cancel()
    chay(kb())


def test_lenh_ghi_can_quyen_tuong_minh(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        _, t = await _noi(m, "may-gia-1", khoa, quyen={"ghi": True},
                          ket_qua_gia={"ghi_tep": {"ok": True}})
        for hd, doi_so in [("ghi_tep", {"duong_dan": "/du-lieu-gia/a",
                                        "noi_dung": "x"}),
                           ("xoa", {"duong_dan": "/du-lieu-gia/a"})]:
            kq = await m.thuc_hien("maytram.may-gia-1", hd, doi_so)
            assert kq["ok"] is False and "quyền" in kq["loi"], hd
        t.cancel()
    chay(kb())


def test_lenh_nguon_can_quyen_rieng(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1", co_quyen_ghi=True)
        _, t = await _noi(m, "may-gia-1", khoa,
                          quyen={"ghi": True, "nguon": True},
                          ket_qua_gia={"tat_may": {"ok": True}})
        kq = await m.thuc_hien("maytram.may-gia-1", "tat_may", {})
        assert kq["ok"] is False and "quyền" in kq["loi"]  # lõi chặn trước khi gửi
        t.cancel()
    chay(kb())


def test_duong_dan_ngoai_allowlist_bi_chan_o_loi(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        _, t = await _noi(m, "may-gia-1", khoa)
        for p in ["/etc/passwd", "/du-lieu-gia/../x", "/du-lieu-gia-bao-cao",
                  "/du-lieu-gia\\..\\x"]:
            kq = await m.thuc_hien("maytram.may-gia-1", "liet_ke",
                                   {"duong_dan": p})
            assert kq["ok"] is False and "phạm vi" in kq["loi"], p
        t.cancel()
    chay(kb())


def test_allowlist_loi_ke_ca_khi_agent_long(mod, du_lieu):
    """Agent GIẢ để allowlist RỘNG hơn sổ ([/]) — chỉ còn lớp của LÕI chặn:
    đường ngoài phạm vi của sổ vẫn phải bị từ chối tại lõi."""
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        _, t = await _noi(m, "may-gia-1", khoa, duong_dan_agent=["/"])
        kq = await m.thuc_hien("maytram.may-gia-1", "liet_ke",
                               {"duong_dan": "/etc/passwd"})
        assert kq["ok"] is False and "phạm vi" in kq["loi"]
        t.cancel()
    chay(kb())


def test_agent_tu_chan_duong_dan_la_lop_thu_hai(mod, du_lieu):
    """Lớp 2 (agent tự kiểm): gửi thẳng qua phiên, bỏ qua kiểm của module."""
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        phien, t = await _noi(m, "may-gia-1", khoa)
        kq = await phien.gui_lenh("liet_ke", {"duong_dan": "/etc/passwd"})
        assert kq["ok"] is False  # agent giả tự từ chối
        t.cancel()
    chay(kb())


def test_idor_ket_qua_phien_b_khong_giai_phien_a(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        k1 = await _dang_ky(m, "may-a")
        k2 = await _dang_ky(m, "may-b")
        pa_, ta = await _noi(m, "may-a", k1, treo_lenh=True)  # A không trả
        pb_, tb = await _noi(m, "may-b", k2)
        # Mở một lệnh CHỜ trên phiên A (agent A treo lệnh) rồi nhét khung lạ vào A
        pa_._het_gio = 1.0
        task_lenh = asyncio.ensure_future(
            pa_.gui_lenh("liet_ke", {"duong_dan": "/du-lieu-gia"}))
        await asyncio.sleep(0.1)
        rid_a = next(iter(pa_._cho))
        # khung ký bằng khoá-phiên của B (MAC đúng với B, id trùng A) → A phải bỏ
        tai = {"ok": True, "bi_nhot": True}
        khung_b = {"id": rid_a, "ket_qua": tai,
                   "mac": pb_._mac_ket_qua(rid_a, tai)}
        pa_.nhan_khung(khung_b)
        assert not pa_._cho[rid_a].done()  # không bị giải oan
        kq = await task_lenh  # hết giờ chứ không nhận kết quả của B
        assert kq["ok"] is False
        ta.cancel()
        tb.cancel()
    chay(kb())


def test_mac_khung_sai_bi_bo(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu, het_gio_lenh="1")
        khoa = await _dang_ky(m, "may-gia-1")
        kenh_loi, kenh_agent = _kenh_kep()
        ag = mod.AgentGia(khoa, DUONG_DAN_GIA, {}, {}, pha_mac=True)
        task = asyncio.ensure_future(ag.chay(kenh_agent))
        t_loi = asyncio.ensure_future(m._phien.chay_kenh(kenh_loi))
        for _ in range(200):
            phien = m._phien.lay("may-gia-1")
            if phien is not None:
                break
            await asyncio.sleep(0.01)
        assert phien is not None
        kq = await phien.gui_lenh("liet_ke", {"duong_dan": "/du-lieu-gia"})
        assert kq["ok"] is False  # khung sai MAC bị bỏ → hết giờ
        task.cancel()
        t_loi.cancel()
    chay(kb())


def test_ket_qua_qua_tran_bi_cat_va_danh_dau(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu, tran_dau_ra="1024")
        # trần 1024 là cận dưới của cấu hình; dùng trần nhỏ qua trực tiếp thuộc tính
        m._tran = 100
        khoa = await _dang_ky(m, "may-gia-1")
        _, t = await _noi(m, "may-gia-1", khoa, ket_qua_gia={
            "doc_tep": {"ok": True, "noi_dung": "z" * 5000}})
        kq = await m.thuc_hien("maytram.may-gia-1", "doc_tep",
                               {"duong_dan": "/du-lieu-gia/a.txt"})
        assert kq["ok"] is True
        assert len(kq["ket_qua"]["noi_dung"]) <= 100
        assert kq["nguon_ngoai"] is True and kq["khong_tin"] is True
        assert kq["ket_qua"].get("bi_cat") is True
        t.cancel()
    chay(kb())


def test_het_gio_lenh_khong_treo(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu, het_gio_lenh="1")
        khoa = await _dang_ky(m, "may-gia-1")
        kenh_loi, kenh_agent = _kenh_kep()
        ag = mod.AgentGia(khoa, DUONG_DAN_GIA, {}, {}, treo_lenh=True)
        task = asyncio.ensure_future(ag.chay(kenh_agent))
        phien = await m._phien.nhan_agent(kenh_loi)
        bat_dau = time.monotonic()
        kq = await phien.gui_lenh("liet_ke", {"duong_dan": "/du-lieu-gia"})
        assert kq["ok"] is False and "không trả lời" in kq["loi"]
        assert time.monotonic() - bat_dau < 5
        task.cancel()
    chay(kb())


def test_trang_thai_online_offline(mod, du_lieu):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        khoa = await _dang_ky(m, "may-gia-1")
        tt = await m.trang_thai(["maytram.may-gia-1"])
        assert tt["maytram.may-gia-1"]["gia_tri"] == "offline"
        _, t = await _noi(m, "may-gia-1", khoa)
        tt = await m.trang_thai(["maytram.may-gia-1"])
        assert tt["maytram.may-gia-1"]["gia_tri"] == "online"
        assert tt["maytram.may-gia-1"]["hop_le"] is True
        t.cancel()
    chay(kb())


def test_khoa_khong_vao_log_va_suc_khoe(mod, du_lieu, caplog):
    async def kb():
        m = await _module_moi_a(mod, du_lieu)
        with caplog.at_level(logging.INFO, logger="web2api.maytram"):
            khoa = await _dang_ky(m, "may-gia-1")
            _, t = await _noi(m, "may-gia-1", khoa)
        assert khoa not in caplog.text
        sk = await m.suc_khoe()
        assert khoa not in json.dumps(sk, ensure_ascii=False)
        assert sk["ok"] is True
        t.cancel()
    chay(kb())


# ── chuẩn module / manifest / mặc định ───────────────────────────────────────

def test_manifest_hop_le():
    du = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    assert kiem_manifest(du) is None
    assert du["id"] == "maytram" and du["tien_to"] == "maytram"
    assert du["hang"] == "A" and "thiet_bi" in du["cung_cap"]


def test_mac_dinh_tat_khong_co_thuc_the(mod, du_lieu):
    m = mod.Module({"id": "maytram", "tien_to": "maytram", "ten": "Máy trạm", "hang": "A"})
    m._thu_muc_du_lieu = du_lieu / "du_lieu" / "maytram"
    assert chay(m.cai({}))["ok"] is True  # không cấu hình vẫn cài được
    m.nap({})
    assert chay(m.thuc_the(None)) == []  # TẮT: không thực thể nào


def test_moi_thuc_the_rui_ro_nguy_hiem(mod, du_lieu):
    m = _module_moi(mod, du_lieu)
    chay(m.thuc_hien("maytram.dang_ky", "dang_ky",
                     {"ten_may": "may-gia-1", "duong_dan": DUONG_DAN_GIA}))
    tts = chay(m.thuc_the(None))
    assert len(tts) == 2  # 1 quản lý + 1 máy
    for e in tts:
        assert e["rui_ro_mac_dinh"] == "nguy_hiem", e["id"]
        assert e["id"].startswith("maytram.")


def test_cai_sai_cau_hinh_bao_loi(mod, du_lieu):
    m = mod.Module({"id": "maytram", "tien_to": "maytram", "ten": "Máy trạm", "hang": "A"})
    m._thu_muc_du_lieu = du_lieu / "du_lieu" / "maytram"
    assert chay(m.cai({"cua_so_thoi_gian": "5"}))["ok"] is False  # < 30
    assert chay(m.cai({"het_gio_lenh": "9999"}))["ok"] is False  # > 300


def test_phuong_thuc_chuan_thiet_bi_day_du(mod, du_lieu):
    m = _module_moi(mod, du_lieu)
    for ten in ("thuc_the", "trang_thai", "thuc_hien", "suc_khoe", "nap", "cai", "dung"):
        assert callable(getattr(m, ten)), ten
