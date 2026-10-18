"""NV118 — module home-assistant (thiết bị nhà qua REST API của Home Assistant), chuẩn C1.

Test-first: mọi test dưới đây PHẢI ĐỎ trước khi có modules/home-assistant/module.py.
Máy chủ HA GIẢ cục bộ (uvicorn + Starlette): /api/states, /api/services/...,
/api/template, kiểm header Bearer. Dữ liệu GIẢ rõ ràng, không dữ liệu thật.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app import so_module
from app.main import app
from app.so_module import SoModule
from gia import MayChuThat, ghi_thu_muc

GOC = Path(__file__).resolve().parents[1]
TOKEN_GIA = "token-gia-nv118-0123456789abcdef"
KHOA_QT = "khoa-quan-tri-test-nv118-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
KHOA_KHACH = "khoa-khach-test-nv118-0123456789ab"

STATES_GIA = [
    {"entity_id": "light.den_tran_gia", "state": "on",
     "attributes": {"friendly_name": "Đèn trần giả", "brightness": 180,
                    "device_class": None, "supported_features": 1,
                    "mat_khau_noi_bo": "KHONG-DUOC-RO-RA-NGOAI"}},
    {"entity_id": "switch.quat_gia", "state": "off",
     "attributes": {"friendly_name": "Quạt giả"}},
    {"entity_id": "sensor.nhiet_do_gia", "state": "26.5",
     "attributes": {"friendly_name": "Nhiệt độ giả", "unit_of_measurement": "°C",
                    "device_class": "temperature"}},
    {"entity_id": "lock.khoa_cua_gia", "state": "locked",
     "attributes": {"friendly_name": "Khoá cửa giả"}},
    {"entity_id": "cover.cua_garage_gia", "state": "closed",
     "attributes": {"friendly_name": "Cửa garage giả", "device_class": "garage"}},
    {"entity_id": "cover.rem_cua_so_gia", "state": "open",
     "attributes": {"friendly_name": "Rèm cửa sổ giả", "device_class": "shutter"}},
    {"entity_id": "climate.dieu_hoa_gia", "state": "cool",
     "attributes": {"friendly_name": "Điều hoà giả", "temperature": 24,
                    "min_temp": 16, "max_temp": 30}},
    {"entity_id": "script.chao_buoi_sang_gia", "state": "off",
     "attributes": {"friendly_name": "Kịch bản chào buổi sáng"}},
    {"entity_id": "automation.bao_dong_gia", "state": "on",
     "attributes": {"friendly_name": "Tự động hoá báo động"}},
    {"entity_id": "light.den_hong_gia", "state": "unavailable",
     "attributes": {"friendly_name": "Đèn hỏng giả"}},
    {"entity_id": "media_player.tv_gia", "state": "playing",
     "attributes": {"friendly_name": "TV giả"}},
]

KHOA_AN_TOAN_CHO_PHEP = {"device_class", "unit_of_measurement"}


class HaGia:
    """Máy chủ Home Assistant GIẢ: đếm lời gọi, ghi lại dịch vụ được gọi."""

    def __init__(self):
        self.goi_states = 0
        self.goi_template = 0
        self.dich_vu_da_goi: list[dict] = []
        self.states = copy.deepcopy(STATES_GIA)

    def _kiem_token(self, request: Request):
        return request.headers.get("authorization") == f"Bearer {TOKEN_GIA}"

    async def api(self, request: Request):
        if not self._kiem_token(request):
            return JSONResponse({"message": "Unauthorized"}, status_code=401)
        return JSONResponse({"message": "API running."})

    async def states_ep(self, request: Request):
        if not self._kiem_token(request):
            return JSONResponse({"message": "Unauthorized"}, status_code=401)
        self.goi_states += 1
        return JSONResponse(self.states)

    async def template(self, request: Request):
        if not self._kiem_token(request):
            return JSONResponse({"message": "Unauthorized"}, status_code=401)
        self.goi_template += 1
        than = await request.json()
        mau = str(than.get("template", ""))
        # Giả: area_name(entity) → "khu_<miền>" cho mỗi entity_id nhắc trong mẫu
        ket_qua = []
        for s in self.states:
            eid = s["entity_id"]
            if eid in mau:
                ket_qua.append(f"khu_{eid.split('.')[0]}")
        return JSONResponse({"result": "\n".join(ket_qua)})

    async def dich_vu(self, request: Request):
        if not self._kiem_token(request):
            return JSONResponse({"message": "Unauthorized"}, status_code=401)
        than = await request.json()
        self.dich_vu_da_goi.append({
            "mien": request.path_params["mien"],
            "dich_vu": request.path_params["dich_vu"],
            "than": than,
        })
        return JSONResponse([])

    def ung_dung(self):
        return Starlette(routes=[
            Route("/api/", self.api),
            Route("/api/states", self.states_ep),
            Route("/api/template", self.template, methods=["POST"]),
            Route("/api/services/{mien}/{dich_vu}", self.dich_vu, methods=["POST"]),
        ])


@pytest.fixture
def ha_gia():
    ha = HaGia()
    with MayChuThat(ha.ung_dung()) as srv:
        ha.url = srv.url
        yield ha


@pytest.fixture
def loopback_cho_phep(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")


_MOD_CACHE = {}


def _tai_module():
    """Nạp modules/home-assistant/module.py bằng đường dẫn (id có gạch ngang, không import trực tiếp được).

    Cache theo đường dẫn để monkeypatch hằng số (TRAN_PHAN_HOI, SO_THUC_THE_TOI_DA) trúng đúng bản dùng.
    """
    import importlib.util
    import sys
    duong = GOC / "modules" / "home-assistant" / "module.py"
    if not duong.is_file():
        raise ModuleNotFoundError("modules.home_assistant")
    if str(duong) not in _MOD_CACHE:
        ten_pkg = "home_assistant_module_nv118"
        spec = importlib.util.spec_from_file_location(
            ten_pkg,
            duong,
            submodule_search_locations=[str(duong.parent)],
        )
        mm = importlib.util.module_from_spec(spec)
        sys.modules[ten_pkg] = mm
        spec.loader.exec_module(mm)
        _MOD_CACHE[str(duong)] = mm
    return _MOD_CACHE[str(duong)]


def _nap_module(dia_chi: str, token: str = TOKEN_GIA, **them):
    """Nạp Module home-assistant với cấu hình giả (đỏ cho tới khi có mã thật)."""
    mm = _tai_module()
    manifest = json.loads((GOC / "modules" / "home-assistant" / "manifest.json").read_text(encoding="utf-8"))
    m = mm.Module(manifest)
    cau_hinh = {"dia_chi": dia_chi, "token": token}
    cau_hinh.update(them)
    return m, cau_hinh, mm


_VONG_LAP = None

def _chay(coro):
    """Chạy coroutine trên MỘT vòng lặp dùng chung (client httpx gắn với loop tạo nó)."""
    global _VONG_LAP
    if _VONG_LAP is None or _VONG_LAP.is_closed():
        _VONG_LAP = asyncio.new_event_loop()
    return _VONG_LAP.run_until_complete(coro)


# ── cài đặt / kiểm địa chỉ ─────────────────────────────────────────────

def test_nv118_cai_thieu_dia_chi(loopback_cho_phep, ha_gia):
    m, cau_hinh, _ = _nap_module("")
    kq = _chay(m.cai(cau_hinh))
    assert kq["ok"] is False
    assert kq["loi"]


def test_nv118_cai_chan_loopback_khi_chua_bat_env(monkeypatch, ha_gia):
    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    m, cau_hinh, _ = _nap_module(ha_gia.url)
    kq = _chay(m.cai(cau_hinh))
    assert kq["ok"] is False
    assert "loopback" in kq["loi"].lower() or "chặn" in kq["loi"]


def test_nv118_cai_chan_userinfo_trong_url(loopback_cho_phep, ha_gia):
    url = ha_gia.url.replace("http://", "http://nguoi:mat@", 1)
    m, cau_hinh, _ = _nap_module(url)
    kq = _chay(m.cai(cau_hinh))
    assert kq["ok"] is False


def test_nv118_cai_sai_token_khong_lo_token(loopback_cho_phep, ha_gia, caplog):
    m, cau_hinh, _ = _nap_module(ha_gia.url, token="sai-hoan-toan")
    with caplog.at_level(logging.INFO, logger="web2api.home_assistant"):
        kq = _chay(m.cai(cau_hinh))
    assert kq["ok"] is False
    assert "sai-hoan-toan" not in json.dumps(kq, ensure_ascii=False)
    assert "sai-hoan-toan" not in caplog.text


def test_nv118_cai_thanh_cong(loopback_cho_phep, ha_gia):
    m, cau_hinh, _ = _nap_module(ha_gia.url)
    kq = _chay(m.cai(cau_hinh))
    assert kq["ok"] is True
    assert kq["cau_hinh_luu"]["dia_chi"].startswith("http://127.0.0.1")
    assert TOKEN_GIA not in json.dumps({k: v for k, v in kq.items() if k != "cau_hinh_luu"})


# ── thuc_the: ánh xạ, id đảo ngược, lọc thuộc tính ────────────────────

def test_nv118_thuc_the_anh_xa_den(loopback_cho_phep, ha_gia):
    m, cau_hinh, _ = _nap_module(ha_gia.url)
    _chay(m.cai(cau_hinh))
    ds = _chay(m.thuc_the(None))
    den = next(e for e in ds if e["id"] == "home_assistant.light.den_tran_gia")
    assert den["ten"] == "Đèn trần giả"
    assert den["loai"] == "light"
    assert den["khu"] == "khu_light"
    ten_hd = [t if isinstance(t, str) else t["hanh_dong"] for t in den["tinh_nang"]]
    assert {"bat", "tat", "chinh_sang"} <= set(ten_hd)
    chinh = next(t for t in den["tinh_nang"] if isinstance(t, dict) and t["hanh_dong"] == "chinh_sang")
    sc = chinh["tham_so"]["properties"]["muc_sang"]
    assert sc["minimum"] == 0 and sc["maximum"] == 100
    assert chinh["tham_so"]["additionalProperties"] is False
    assert den["rui_ro_mac_dinh"] == "ghi"  # bật/tắt đèn KHÔNG phải nguy hiểm


def test_nv118_thuoc_tinh_chi_truong_an_toan(loopback_cho_phep, ha_gia):
    m, cau_hinh, _ = _nap_module(ha_gia.url)
    _chay(m.cai(cau_hinh))
    ds = _chay(m.thuc_the(None))
    for e in ds:
        assert set(e["thuoc_tinh"]) <= KHOA_AN_TOAN_CHO_PHEP, f"thuoc_tinh rò: {e['thuoc_tinh']}"
        assert "mat_khau_noi_bo" not in json.dumps(ds, ensure_ascii=False)
    cam_bien = next(e for e in ds if e["id"] == "home_assistant.sensor.nhiet_do_gia")
    assert cam_bien["thuoc_tinh"]["unit_of_measurement"] == "°C"
    assert cam_bien["rui_ro_mac_dinh"] == "doc"


def test_nv118_id_dao_nguoc_duoc(loopback_cho_phep, ha_gia):
    m, cau_hinh, _ = _nap_module(ha_gia.url)
    _chay(m.cai(cau_hinh))
    ds = _chay(m.thuc_the(None))
    for e in ds:
        assert e["id"].startswith("home_assistant.")
        entity_id = e["id"].split(".", 1)[1]
        assert any(s["entity_id"] == entity_id for s in STATES_GIA), f"không đảo ngược được: {e['id']}"
    # media_player.tv_gia vẫn đảo ngược dù miền có gạch dưới
    tv = next(e for e in ds if "tv_gia" in e["id"])
    assert tv["id"] == "home_assistant.media_player.tv_gia"


def test_nv118_lock_va_garage_nguy_hiem(loopback_cho_phep, ha_gia):
    m, cau_hinh, _ = _nap_module(ha_gia.url)
    _chay(m.cai(cau_hinh))
    ds = _chay(m.thuc_the(None))
    khoa = next(e for e in ds if e["id"] == "home_assistant.lock.khoa_cua_gia")
    assert khoa["rui_ro_mac_dinh"] == "nguy_hiem"
    garage = next(e for e in ds if e["id"] == "home_assistant.cover.cua_garage_gia")
    assert garage["rui_ro_mac_dinh"] == "nguy_hiem"
    rem = next(e for e in ds if e["id"] == "home_assistant.cover.rem_cua_so_gia")
    assert rem["rui_ro_mac_dinh"] != "nguy_hiem"  # rèm thường: ghi


def test_nv118_script_automation_khong_bao_gio_dieu_khien(loopback_cho_phep, ha_gia):
    m, cau_hinh, _ = _nap_module(ha_gia.url, nhom_duoc_dieu_khien="script,automation,light")
    _chay(m.cai(cau_hinh))
    ds = _chay(m.thuc_the(None))
    kb = next(e for e in ds if e["id"] == "home_assistant.script.chao_buoi_sang_gia")
    assert kb["tinh_nang"] == []
    kq = _chay(m.thuc_hien(kb["id"], "bat", {}))
    assert kq["ok"] is False
    assert ha_gia.dich_vu_da_goi == []  # kể cả cấu hình cho phép, không gì được gửi


# ── thuc_hien: chỉ qua bảng ánh xạ, kiểm trước khi gửi ─────────────────

def test_nv118_thuc_hien_bat_den(loopback_cho_phep, ha_gia):
    m, cau_hinh, _ = _nap_module(ha_gia.url)
    _chay(m.cai(cau_hinh))
    _chay(m.thuc_the(None))
    kq = _chay(m.thuc_hien("home_assistant.light.den_tran_gia", "bat", {}))
    assert kq["ok"] is True
    assert isinstance(kq["ok"], bool)
    assert len(ha_gia.dich_vu_da_goi) == 1
    goi = ha_gia.dich_vu_da_goi[0]
    assert goi["mien"] == "light" and goi["dich_vu"] == "turn_on"
    assert goi["than"]["entity_id"] == "light.den_tran_gia"


def test_nv118_thuc_hien_hanh_dong_la_bi_chan(loopback_cho_phep, ha_gia):
    m, cau_hinh, _ = _nap_module(ha_gia.url)
    _chay(m.cai(cau_hinh))
    _chay(m.thuc_the(None))
    kq = _chay(m.thuc_hien("home_assistant.light.den_tran_gia", "tu_huy", {}))
    assert kq["ok"] is False
    assert ha_gia.dich_vu_da_goi == []


def test_nv118_thuc_hien_tham_so_vuot_bien_bi_chan(loopback_cho_phep, ha_gia):
    m, cau_hinh, _ = _nap_module(ha_gia.url)
    _chay(m.cai(cau_hinh))
    _chay(m.thuc_the(None))
    kq = _chay(m.thuc_hien("home_assistant.light.den_tran_gia", "chinh_sang", {"muc_sang": 150}))
    assert kq["ok"] is False
    assert ha_gia.dich_vu_da_goi == []


def test_nv118_thuc_hien_entity_ngoai_danh_sach_bi_chan(loopback_cho_phep, ha_gia):
    m, cau_hinh, _ = _nap_module(ha_gia.url)
    _chay(m.cai(cau_hinh))
    _chay(m.thuc_the(None))
    kq = _chay(m.thuc_hien("home_assistant.light.khong_co_gia", "bat", {}))
    assert kq["ok"] is False
    assert ha_gia.dich_vu_da_goi == []


def test_nv118_nhom_duoc_dieu_khien_gioi_han(loopback_cho_phep, ha_gia):
    m, cau_hinh, _ = _nap_module(ha_gia.url, nhom_duoc_dieu_khien="light")
    _chay(m.cai(cau_hinh))
    _chay(m.thuc_the(None))
    kq = _chay(m.thuc_hien("home_assistant.switch.quat_gia", "bat", {}))
    assert kq["ok"] is False
    assert ha_gia.dich_vu_da_goi == []


def test_nv118_loi_ha_khong_lo_nguyen_van(loopback_cho_phep, ha_gia):
    m, cau_hinh, _ = _nap_module(ha_gia.url)
    _chay(m.cai(cau_hinh))
    _chay(m.thuc_the(None))
    # token hết hạn giữa chừng (nạp lại cấu hình token sai) → lỗi HA phải che, không lộ nguyên văn
    m.nap({**cau_hinh, "token": "het-han-roi"})
    kq = _chay(m.thuc_hien("home_assistant.light.den_tran_gia", "bat", {}))
    assert kq["ok"] is False
    assert "het-han-roi" not in json.dumps(kq, ensure_ascii=False)
    assert "Unauthorized" not in json.dumps(kq, ensure_ascii=False)


# ── trang_thai: bộ đệm, hop_le ────────────────────────────────────────

def test_nv118_trang_thai_hop_le_va_don_vi(loopback_cho_phep, ha_gia):
    m, cau_hinh, _ = _nap_module(ha_gia.url)
    _chay(m.cai(cau_hinh))
    tt = _chay(m.trang_thai(["home_assistant.sensor.nhiet_do_gia",
                             "home_assistant.light.den_hong_gia"]))
    assert tt["home_assistant.sensor.nhiet_do_gia"]["hop_le"] is True
    assert tt["home_assistant.sensor.nhiet_do_gia"]["don_vi"] == "°C"
    assert "luc" in tt["home_assistant.sensor.nhiet_do_gia"]
    assert tt["home_assistant.light.den_hong_gia"]["hop_le"] is False  # unavailable


def test_nv118_bo_dem_trang_thai_khong_doi_ha(loopback_cho_phep, ha_gia):
    m, cau_hinh, _ = _nap_module(ha_gia.url)
    _chay(m.cai(cau_hinh))
    ids = ["home_assistant.light.den_tran_gia"]
    _chay(m.trang_thai(ids))
    _chay(m.trang_thai(ids))
    assert ha_gia.goi_states <= 2  # lần 2 dùng bộ đệm (lần 1 do thuc_the trong cai? không — cai không gọi states)


def test_nv118_bo_dem_khu_vuc_khong_goi_lai_template(loopback_cho_phep, ha_gia):
    m, cau_hinh, _ = _nap_module(ha_gia.url)
    _chay(m.cai(cau_hinh))
    _chay(m.thuc_the(None))
    so_lan_1 = ha_gia.goi_template
    assert so_lan_1 >= 1
    _chay(m.thuc_the(None))
    assert ha_gia.goi_template == so_lan_1  # bộ đệm khu vực


# ── trần phản hồi, trần thực thể, token không rò ──────────────────────

def test_nv118_phan_hoi_khong_lo_bi_cat(loopback_cho_phep, ha_gia, monkeypatch):
    mm = _tai_module()
    monkeypatch.setattr(mm, "TRAN_PHAN_HOI", 512)  # ép trần nhỏ để kiểm đường cắt
    m, cau_hinh, _ = _nap_module(ha_gia.url)
    _chay(m.cai(cau_hinh))
    ds = _chay(m.thuc_the(None))
    assert ds == []  # vượt trần → bỏ an toàn


def test_nv118_so_thuc_the_toi_da_bi_cat(loopback_cho_phep, ha_gia, monkeypatch):
    mm = _tai_module()
    monkeypatch.setattr(mm, "SO_THUC_THE_TOI_DA", 3)
    m, cau_hinh, _ = _nap_module(ha_gia.url)
    _chay(m.cai(cau_hinh))
    ds = _chay(m.thuc_the(None))
    assert len(ds) == 3


def test_nv118_token_khong_vao_log(loopback_cho_phep, ha_gia, caplog):
    m, cau_hinh, _ = _nap_module(ha_gia.url)
    with caplog.at_level(logging.DEBUG):
        _chay(m.cai(cau_hinh))
        _chay(m.thuc_the(None))
        _chay(m.trang_thai(["home_assistant.light.den_tran_gia"]))
        _chay(m.thuc_hien("home_assistant.light.den_tran_gia", "bat", {}))
    assert TOKEN_GIA not in caplog.text


# ── qua lõi thật ─────────────────────────────────────────────────────

@pytest.fixture
def moi_truong_loi(tmp_path, monkeypatch, loopback_cho_phep):
    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        yield client, data_dir, so


def _cai_module_vao_loi(client, data_dir, ha_gia, cau_hinh=None):
    mod_path = data_dir / "modules" / "home-assistant"
    mod_path.mkdir(parents=True, exist_ok=True)
    for ten in ("manifest.json", "module.py", "README.md", "khop_nhanh.py"):
        nguon = GOC / "modules" / "home-assistant" / ten
        if not nguon.is_file():
            pytest.skip(f"chưa có modules/home-assistant/{ten}")
        (mod_path / ten).write_bytes(nguon.read_bytes())
    r = client.post("/api/modules/nap-lai", headers=QT)
    assert r.status_code == 200
    ch = {"dia_chi": ha_gia.url, "token": TOKEN_GIA}
    if cau_hinh:
        ch.update(cau_hinh)
    r = client.post("/api/modules/home-assistant/cai", headers=QT, json={"cau_hinh": ch})
    assert r.status_code == 200, r.text
    return mod_path


def test_nv118_loi_thay_thuc_the(moi_truong_loi, ha_gia):
    client, data_dir, so = moi_truong_loi
    _cai_module_vao_loi(client, data_dir, ha_gia)
    r = client.get("/api/thiet-bi", headers=QT)
    assert r.status_code == 200
    ids = [e["id"] for e in r.json()["thuc_the"]]
    assert "home_assistant.light.den_tran_gia" in ids
    assert "home_assistant.lock.khoa_cua_gia" in ids


def test_nv118_loi_hanh_dong_nguy_hiem_vao_hang_cho(moi_truong_loi, ha_gia):
    client, data_dir, so = moi_truong_loi
    _cai_module_vao_loi(client, data_dir, ha_gia)
    # khoá khách phạm vi thiet_bi:ghi gọi mở khoá (nguy_hiem) → 202 vào hàng chờ, HA giả không nhận gì
    r_tao = client.post("/api/khoa-khach", headers=QT,
                        json={"nhan": "App nhà NV118", "pham_vi": ["thiet_bi:ghi", "thiet_bi:doc"]})
    assert r_tao.status_code == 200, r_tao.text
    hd_khach = {"Authorization": f"Bearer {r_tao.json()['khoa']['khoa']}"}
    r = client.post("/api/thiet-bi/home_assistant.lock.khoa_cua_gia/hanh-dong",
                    headers=hd_khach,
                    json={"hanh_dong": "mo_khoa", "tham_so": {}})
    assert r.status_code == 202, r.text
    assert r.json().get("cho_phe_duyet") is True
    assert ha_gia.dich_vu_da_goi == []


def test_nv118_loi_chay_khi_chua_cau_hinh(moi_truong_loi, ha_gia):
    client, data_dir, so = moi_truong_loi
    mod_path = data_dir / "modules" / "home-assistant"
    mod_path.mkdir(parents=True, exist_ok=True)
    for ten in ("manifest.json", "module.py"):
        nguon = GOC / "modules" / "home-assistant" / ten
        if not nguon.is_file():
            pytest.skip(f"chưa có modules/home-assistant/{ten}")
        (mod_path / ten).write_bytes(nguon.read_bytes())
    r = client.post("/api/modules/nap-lai", headers=QT)
    assert r.status_code == 200
    r = client.get("/api/thiet-bi", headers=QT)
    assert r.status_code == 200
    assert r.json()["thuc_the"] == []  # chưa cấu hình → rỗng, không vỡ
