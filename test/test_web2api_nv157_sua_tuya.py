"""Test VÒNG SỬA NV157 (soát độc lập B, mục 3 — B1..B5 + nghi ngờ cmd 7).

Mỗi test ứng một phát hiện của `soat-doc-lap-B.md`:
- B1: mã ngành LAN tự khai qua mặt luật «khoá/bếp/bình nóng lạnh không bao
  giờ điều khiển» khi đám mây mất kết nối / thực thể trùng id.
- B2: min/max/kiểu/enum của đặc tả thiết bị không được kiểm ở đâu.
- B3: thực thể trùng id đám mây + LAN làm hỏng điều khiển hợp lệ.
- B4: `cong` LAN không kiểm (ValueError/OverflowError).
- B5: hai tính năng ghi trong tài liệu nhưng chưa làm (tự lấy local_key qua
  đám mây; LAN hỏng quay về đám mây).
- Nghi ngờ cmd 7: phản hồi lệnh điều khiển 3.3 chỉ có mã trả, không payload
  → lệnh đã chạy nhưng module báo ok:false.

Dịch vụ/thiết bị đều GIẢ (MockTransport + TCP loopback), không mạng thật.
"""
from __future__ import annotations

import asyncio
import json
import socket
import struct
import sys
import threading
import time
import zlib
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.kham_pha import doc_manifest, nap_ma
from app.loi_openai import LoiOpenAI
from app.ngu_canh import NguCanh
from app.thiet_bi import kiem_tra_tham_so, thuc_hien_hanh_dong_loi

GOC = Path(__file__).resolve().parents[1]
THU_MUC_MODULE = GOC / "modules" / "tuya"

AID = "access-id-gia-0123"
SECRET = "access-secret-gia-456789"
LOCAL_KEY = "localkeygia12345"  # 16 ký tự — khoá LAN giả

DEN = "dev-sua-den"      # đèn dj — có cả đám mây và LAN
KHOA = "dev-sua-khoa"    # khoá cửa videolock trên đám mây
BINH = "dev-sua-binh"    # bình nóng lạnh rs trên đám mây
LANH = "dev-sua-lanh"    # điều hoà ktkzq (Enum + Integer)

_goi = None


def chay(coro):
    return asyncio.run(coro)


def _tai():
    global _goi
    if _goi is None:
        manifest, loi = doc_manifest(THU_MUC_MODULE)
        assert loi is None, loi
        lop, ten_goi = nap_ma(THU_MUC_MODULE, manifest, "test157sua")
        _goi = (lop, sys.modules[ten_goi])
    return _goi


def _manifest():
    manifest, loi = doc_manifest(THU_MUC_MODULE)
    assert loi is None, loi
    return manifest


def _module_moi():
    lop, _ = _tai()
    return lop(_manifest())


# ───────── Máy chủ Tuya đám mây GIẢ (điều khiển được sống/chết) ─────────

THIET_BI = [
    {"id": DEN, "name": "Đèn sửa", "category": "dj", "model": "M1",
     "is_online": True},
    {"id": KHOA, "name": "Khoá sửa", "category": "videolock", "model": "L1",
     "is_online": True},
    {"id": BINH, "name": "Bình sửa", "category": "rs", "model": "W1",
     "is_online": True},
    {"id": LANH, "name": "Điều hoà sửa", "category": "ktkzq", "model": "A1",
     "is_online": True},
]

THUOC_TINH = {
    DEN: [{"code": "switch_led", "value": True},
          {"code": "bright_value_v2", "value": 500}],
    KHOA: [{"code": "unlock_fingerprint", "value": 3}],
    BINH: [{"code": "switch", "value": False}],
    LANH: [{"code": "switch", "value": True}],
}

CHUC_NANG = {
    DEN: {
        "functions": [
            {"code": "switch_led", "type": "Boolean", "values": "{}"},
            {"code": "bright_value_v2", "type": "Integer",
             "values": '{"min": 10, "max": 1000, "scale": 0, "step": 1}'},
        ],
        "status": [
            {"code": "switch_led", "type": "Boolean", "values": "{}",
             "dp_id": 1},
            {"code": "bright_value_v2", "type": "Integer",
             "values": '{"min": 10, "max": 1000}', "dp_id": 2},
        ],
    },
    KHOA: {
        "functions": [
            {"code": "unlock_remote", "type": "Boolean", "values": "{}"}],
        "status": [
            {"code": "unlock_fingerprint", "type": "Integer", "values": "{}",
             "dp_id": 12}],
    },
    BINH: {
        "functions": [
            {"code": "switch", "type": "Boolean", "values": "{}"},
            {"code": "temp_set", "type": "Integer",
             "values": '{"min": 30, "max": 75, "unit": "℃"}'},
        ],
        "status": [
            {"code": "switch", "type": "Boolean", "values": "{}", "dp_id": 1},
            {"code": "temp_set", "type": "Integer",
             "values": '{"min": 30, "max": 75}', "dp_id": 2},
        ],
    },
    LANH: {
        "functions": [
            {"code": "switch", "type": "Boolean", "values": "{}"},
            {"code": "mode", "type": "Enum",
             "values": '{"range": ["auto", "cool", "dry", "fan"]}'},
            {"code": "temp_set", "type": "Integer",
             "values": '{"min": 16, "max": 30, "unit": "℃"}'},
        ],
        "status": [
            {"code": "switch", "type": "Boolean", "values": "{}", "dp_id": 1},
            {"code": "mode", "type": "Enum",
             "values": '{"range": ["auto", "cool", "dry", "fan"]}',
             "dp_id": 2},
            {"code": "temp_set", "type": "Integer",
             "values": '{"min": 16, "max": 30}', "dp_id": 3},
        ],
    },
}


class MayTuyaSua:
    """MockTransport cho vòng sửa: `song=False` = đám mây mất kết nối."""

    def __init__(self):
        self.song = True
        self.lenh: list[dict] = []
        self.so_lan_lay_khoa = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if not self.song:
            raise httpx.ConnectError("dam may mat ket noi", request=req)
        duong = req.url.path
        if duong == "/v1.0/token":
            return httpx.Response(200, json={
                "success": True,
                "result": {"access_token": "TOKEN-GIA", "expire_time": 7200}})
        if duong == "/v2.0/cloud/thing/device":
            return httpx.Response(200, json={
                "success": True, "result": {"list": THIET_BI}})
        if "/shadow/properties" in duong:
            ma = duong.split("/")[4]
            return httpx.Response(200, json={
                "success": True,
                "result": {"properties": THUOC_TINH.get(ma, [])}})
        if duong.endswith("/specifications"):
            ma = duong.split("/")[3]
            return httpx.Response(200, json={
                "success": True, "result": CHUC_NANG.get(ma, {})})
        if duong.startswith("/v1.0/iot-03/devices/") \
                and duong.endswith("/commands"):
            self.lenh.append(json.loads(req.content or b"{}"))
            return httpx.Response(200, json={"success": True, "result": True})
        if duong.startswith("/v1.0/devices/"):
            self.so_lan_lay_khoa += 1
            ma = duong.split("/")[3]
            return httpx.Response(200, json={
                "success": True, "result": {"local_key": LOCAL_KEY, "id": ma}})
        return httpx.Response(404, json={"success": False, "msg": "not found"})


class LanGhi:
    """Tầng LAN ghi lại lệnh (thay kết nối thật) cho các test B1."""

    def __init__(self):
        self.dat_goi: list[tuple[str, dict]] = []

    async def hoi(self, ban_ghi: dict) -> dict:
        return {"1": True}

    async def dat(self, ban_ghi: dict, dps: dict) -> dict:
        self.dat_goi.append((ban_ghi["id"], dict(dps)))
        return dict(dps)


@pytest.fixture
def may():
    return MayTuyaSua()


def _mod(may, lan_cauhinh=None, khoa_lan=None, **them):
    """Module: đám mây giả + (tuỳ chọn) mục LAN; tầng LAN thay bằng bộ ghi."""
    mod = _module_moi()
    cau_hinh = {"access_id": AID, "access_secret": SECRET, "vung": "america"}
    if lan_cauhinh is not None:
        cau_hinh["thiet_bi_lan"] = json.dumps(lan_cauhinh)
        cau_hinh["local_key_lan"] = khoa_lan or []
    cau_hinh.update(them)
    mod.nap(cau_hinh)
    khach = httpx.AsyncClient(transport=httpx.MockTransport(may))
    mod._dam_may._lay_client = lambda: khach
    return mod


def _ngu_canh_quan_tri():
    return NguCanh(danh_tinh={"loai": "quan_tri", "id": "qt-sua"},
                   nguon="nguoi", pham_vi_hieu_luc=())


# ───────── B1: ngành LAN tự khai không được qua mặt luật cấm ─────────

def test_b1_dam_may_mat_ket_noi_lan_khai_nham_khoa_van_bi_chan(may):
    """Đám mây chết + LAN khai khoá là «kg», bình là «dj» + điều khiển BẬT:
    thực thể LAN của chúng KHÔNG được có hành động và lệnh không tới LAN."""
    mod = _mod(
        may,
        lan_cauhinh=[
            {"id": KHOA, "ip": "192.168.99.2", "ten": "Khoá", "nganh": "kg"},
            {"id": BINH, "ip": "192.168.99.3", "ten": "Bình", "nganh": "dj"},
        ],
        khoa_lan=[LOCAL_KEY, LOCAL_KEY],
        cho_phep_dieu_khien="bat")
    ghi = LanGhi()
    mod._lan = ghi
    may.song = False  # đám mây mất kết nối ngay từ đầu
    tt = {x["id"]: x for x in chay(mod.thuc_the(None))}
    for ma in (KHOA, BINH):
        assert tt[f"tuya.{ma}"]["tinh_nang"] == [], ma
        assert tt[f"tuya.{ma}"]["rui_ro_mac_dinh"] == "nguy_hiem", ma
    for ma in (KHOA, BINH):
        kq = chay(mod.thuc_hien(f"tuya.{ma}", "bat_1", {}))
        assert kq["ok"] is False, ma
    assert ghi.dat_goi == []


def test_b1_dam_may_song_khong_phat_hai_thuc_the_trung_id(may):
    """Cùng thiết bị có cả đám mây (ngành cấm) và LAN (ngành tự khai «kg»):
    chỉ MỘT thực thể, không hành động; gọi qua cổng lõi thật bị 400."""
    mod = _mod(
        may,
        lan_cauhinh=[{"id": KHOA, "ip": "192.168.99.2", "ten": "Khoá",
                      "nganh": "kg"}],
        khoa_lan=[LOCAL_KEY],
        cho_phep_dieu_khien="bat")
    ghi = LanGhi()
    mod._lan = ghi
    tt = chay(mod.thuc_the(None))
    ids = [x["id"] for x in tt]
    assert len(ids) == len(set(ids)), f"trùng id: {ids}"
    ent = next(x for x in tt if x["id"] == f"tuya.{KHOA}")
    assert ent["nguon"] == "dam_may"
    assert ent["tinh_nang"] == []
    assert ent["rui_ro_mac_dinh"] == "nguy_hiem"
    # Lớp kiểm lại của module: gọi thẳng hành động của khoá cũng bị từ chối
    kq = chay(mod.thuc_hien(f"tuya.{KHOA}", "bat_unlock_remote", {}))
    assert kq["ok"] is False
    assert may.lenh == [] and ghi.dat_goi == []
    # Qua cổng lõi thật (đúng đường soát độc lập đã tái hiện): 400 invalid_action
    with pytest.raises(LoiOpenAI) as ei:
        chay(thuc_hien_hanh_dong_loi(
            f"tuya.{KHOA}", "bat_unlock_remote", {}, xac_nhan=True,
            ngu_canh=_ngu_canh_quan_tri(), module=mod))
    assert ei.value.status_code == 400


def test_b1_cam_dam_may_nho_qua_lan_mat_ket_noi(may):
    """Đám mây TỪNG báo ngành cấm → mất kết nối sau đó vẫn cấm (nhớ trong phiên)."""
    mod = _mod(
        may,
        lan_cauhinh=[{"id": KHOA, "ip": "192.168.99.2", "ten": "Khoá",
                      "nganh": "kg"}],
        khoa_lan=[LOCAL_KEY],
        cho_phep_dieu_khien="bat")
    ghi = LanGhi()
    mod._lan = ghi
    chay(mod.thuc_the(None))  # đám mây sống: module đã biết KHOA là khoá cửa
    may.song = False
    tt = {x["id"]: x for x in chay(mod.thuc_the(None))}
    assert tt[f"tuya.{KHOA}"]["tinh_nang"] == []
    kq = chay(mod.thuc_hien(f"tuya.{KHOA}", "bat_1", {}))
    assert kq["ok"] is False
    assert ghi.dat_goi == []


def test_b1_cua_thuc_hien_chan_ca_khi_ban_do_con_hanh_dong(may):
    """Ghìm THẲNG cổng ở thuc_hien: lớp phòng thủ thứ hai sau bản đồ hành
    động. Thiết bị lọt vào tập «đám mây từng báo ngành cấm» thì lệnh bị chặn
    ngay tại cửa dù bản đồ hành động còn sót mục ghi (phát từ lúc thiết bị
    còn ngành an toàn)."""
    mod = _mod(may, cho_phep_dieu_khien="bat")
    chay(mod.thuc_the(None))  # LANH đang có hành động dat_mode/dat_temp_set
    mod._ma_cam_dieu_khien.add(LANH)
    kq = chay(mod.thuc_hien(f"tuya.{LANH}", "dat_temp_set", {"gia_tri": 22}))
    assert kq["ok"] is False
    assert may.lenh == []


def test_b1_nganh_cam_tung_bao_khong_phat_lai_hanh_dong(may):
    """Thiết bị TỪNG bị đám mây báo ngành cấm, sau đó đám mây báo lại ngành
    an toàn (đổi ngành/đăng ký lại giữa phiên) → vẫn KHÔNG phát hành động
    trong cùng phiên: «từng là khoá» thắng «hiện khai là đèn»."""
    mod = _mod(may, cho_phep_dieu_khien="bat")
    muc_den = next(x for x in THIET_BI if x["id"] == DEN)
    try:
        muc_den["category"] = "videolock"
        chay(mod.thuc_the(None))
        muc_den["category"] = "dj"
        tt = {x["id"]: x for x in chay(mod.thuc_the(None))}
    finally:
        muc_den["category"] = "dj"
    assert tt[f"tuya.{DEN}"]["tinh_nang"] == []


def test_b1_lan_thuan_nganh_la_khong_hanh_dong():
    """Thiết bị chỉ có LAN, ngành lạ → không hành động, nguy_hiem (fail-closed);
    ngành bỏ trống = KHÔNG RÕ ngành → cũng nguy_hiem, không hành động (B1:
    ngành chỉ do người khai/không rõ thì không bao giờ mở điều khiển)."""
    mod = _module_moi()
    mod.nap({
        "thiet_bi_lan": json.dumps([
            {"id": "lan-la", "ip": "192.168.99.4", "nganh": "xyzla"},
            {"id": "lan-trong", "ip": "192.168.99.5"},
        ]),
        "local_key_lan": [LOCAL_KEY, LOCAL_KEY],
        "cho_phep_dieu_khien": "bat",
    })
    tt = {x["id"]: x for x in chay(mod.thuc_the(None))}
    assert tt["tuya.lan-la"]["tinh_nang"] == []
    assert tt["tuya.lan-la"]["rui_ro_mac_dinh"] == "nguy_hiem"
    assert tt["tuya.lan-trong"]["tinh_nang"] == []
    assert tt["tuya.lan-trong"]["rui_ro_mac_dinh"] == "nguy_hiem"


# ───────── B3: đèn có cả hai đường, LAN trống ngành vẫn điều khiển được ─────────

def test_b3_den_ca_hai_duong_lan_trong_nganh_van_dieu_khien(may):
    mod = _mod(
        may,
        lan_cauhinh=[{"id": DEN, "ip": "192.168.99.6", "ten": "Đèn"}],
        khoa_lan=[LOCAL_KEY],
        cho_phep_dieu_khien="bat")
    tt = chay(mod.thuc_the(None))
    ids = [x["id"] for x in tt]
    assert len(ids) == len(set(ids))
    ent = next(x for x in tt if x["id"] == f"tuya.{DEN}")
    assert ent["nguon"] == "dam_may"
    assert "bat_switch_led" in {x["hanh_dong"] for x in ent["tinh_nang"]}
    kq = chay(mod.thuc_hien(f"tuya.{DEN}", "bat_switch_led", {}))
    assert kq["ok"] is True
    assert may.lenh == [{"commands": [{"code": "switch_led", "value": True}]}]


# ───────── B2: khoảng giá trị / kiểu / enum theo đặc tả ─────────

def _tinh_nang_cua(mod, ma, hanh_dong):
    tt = {x["id"]: x for x in chay(mod.thuc_the(None))}
    for tn in tt[f"tuya.{ma}"]["tinh_nang"]:
        if tn["hanh_dong"] == hanh_dong:
            return tt[f"tuya.{ma}"]["tinh_nang"], tn
    raise AssertionError(f"không thấy hành động {hanh_dong}")


def test_b2_luoc_do_la_json_schema_chuan_cua_loi(may):
    _, tn = _tinh_nang_cua(_mod(may), DEN, "dat_bright_value_v2")
    luoc = tn["tham_so"]
    assert isinstance(luoc, dict), "tham_so phải là JSON Schema (dict)"
    assert luoc["type"] == "object"
    assert luoc["required"] == ["gia_tri"]
    assert luoc["additionalProperties"] is False
    gt = luoc["properties"]["gia_tri"]
    assert gt["type"] == "integer"
    assert gt["minimum"] == 10 and gt["maximum"] == 1000


def test_b2_cong_loi_chan_gia_tri_sai(may):
    """Đi đúng đường lõi dùng (kiem_tra_tham_so): ngoài khoảng, thừa trường,
    sai kiểu đều bị 400 TRƯỚC khi module chạy."""
    tinh_nang, _ = _tinh_nang_cua(_mod(may), DEN, "dat_bright_value_v2")
    for tham_so in ({"gia_tri": 99999999}, {"gia_tri": -5}, {"gia_tri": 9},
                    {"gia_tri": 1001}, {"gia_tri": 5.9}, {"gia_tri": True},
                    {"gia_tri": "500"}, {"gia_tri": 500, "bi_mat_them": "x"},
                    {}):
        with pytest.raises(LoiOpenAI) as ei:
            kiem_tra_tham_so(tinh_nang, "dat_bright_value_v2", tham_so)
        assert ei.value.status_code == 400, tham_so
    kiem_tra_tham_so(tinh_nang, "dat_bright_value_v2", {"gia_tri": 10})
    kiem_tra_tham_so(tinh_nang, "dat_bright_value_v2", {"gia_tri": 1000})
    kiem_tra_tham_so(tinh_nang, "dat_bright_value_v2", {"gia_tri": 500})


def test_b2_module_kiem_lai_khong_tin_loi(may):
    """Lớp kiểm lại trong thuc_hien: kể cả khi bị gọi thẳng (bỏ qua cổng lõi),
    giá trị ngoài khoảng/kiểu lạ KHÔNG được tới Tuya."""
    mod = _mod(may, cho_phep_dieu_khien="bat")
    chay(mod.thuc_the(None))
    for gt in (99999999, -5, 9, 1001, 5.9, True, "500", None):
        kq = chay(mod.thuc_hien(f"tuya.{DEN}", "dat_bright_value_v2",
                                {"gia_tri": gt}))
        assert kq["ok"] is False, gt
    assert may.lenh == []
    for gt in (10, 500, 1000):  # biên: đúng khoảng thì đi
        kq = chay(mod.thuc_hien(f"tuya.{DEN}", "dat_bright_value_v2",
                                {"gia_tri": gt}))
        assert kq["ok"] is True, gt
    assert [l["commands"][0]["value"] for l in may.lenh] == [10, 500, 1000]


def test_b2_enum_chi_nhan_gia_tri_trong_range(may):
    mod = _mod(may, cho_phep_dieu_khien="bat")
    tinh_nang, tn = _tinh_nang_cua(mod, LANH, "dat_mode")
    assert tn["tham_so"]["properties"]["gia_tri"]["enum"] == \
        ["auto", "cool", "dry", "fan"]
    with pytest.raises(LoiOpenAI):
        kiem_tra_tham_so(tinh_nang, "dat_mode", {"gia_tri": "nong"})
    with pytest.raises(LoiOpenAI):
        kiem_tra_tham_so(tinh_nang, "dat_mode", {"gia_tri": {"la": 1}})
    for gt in ("nong", "", None, {"la": 1}, ["cool"], 5):
        kq = chay(mod.thuc_hien(f"tuya.{LANH}", "dat_mode", {"gia_tri": gt}))
        assert kq["ok"] is False, gt
    assert may.lenh == []
    kq = chay(mod.thuc_hien(f"tuya.{LANH}", "dat_mode", {"gia_tri": "cool"}))
    assert kq["ok"] is True
    assert may.lenh[-1]["commands"][0] == {"code": "mode", "value": "cool"}


def test_b2_khong_co_luoc_do_thi_tu_choi_so(may):
    """Thiết bị LAN thuần, ngành ghi nhưng không có đặc tả đám mây: hành động
    đặt số KHÔNG tồn tại (chỉ bat/tat điểm 1) — không có đường nào gửi số
    chưa kiểm. (Nguyên tắc B2: không lược đồ → từ chối.)"""
    mod = _module_moi()
    mod.nap({
        "thiet_bi_lan": json.dumps(
            [{"id": "lan-cz", "ip": "192.168.99.7", "nganh": "cz"}]),
        "local_key_lan": [LOCAL_KEY],
        "cho_phep_dieu_khien": "bat",
    })
    tt = {x["id"]: x for x in chay(mod.thuc_the(None))}
    cac_hd = {x["hanh_dong"] for x in tt["tuya.lan-cz"]["tinh_nang"]}
    assert cac_hd == {"bat_1", "tat_1"}


# ───────── B4: cong LAN phải được kiểm ─────────

def test_b4_cong_sai_bao_loi_cau_hinh_o_cai():
    mod = _module_moi()
    for cong in ("abc", 99999, -1, 0, 65536):
        kq = chay(mod.cai({
            "thiet_bi_lan": json.dumps(
                [{"id": "x1", "ip": "192.168.99.2", "cong": cong}]),
            "local_key_lan": [LOCAL_KEY]}))
        assert kq["ok"] is False, cong
        assert "x1" in (kq["loi"] or ""), (cong, kq["loi"])


def test_b4_cong_bien_hop_le(may):
    mod = _module_moi()
    for cong in (1, 6668, 65535):
        kq = chay(mod.cai({
            "thiet_bi_lan": json.dumps(
                [{"id": "x1", "ip": "192.168.99.2", "cong": cong}]),
            "local_key_lan": [LOCAL_KEY]}))
        assert kq["ok"] is True, cong


def test_b4_nap_cong_hong_khong_lam_sap_suc_khoe():
    """nap() trực tiếp với cong 99999: mục bị loại, suc_khoe không ném
    OverflowError (đường soát độc lập đã tái hiện)."""
    mod = _module_moi()
    mod.nap({
        "thiet_bi_lan": json.dumps(
            [{"id": "x1", "ip": "192.168.99.2", "cong": 99999}]),
        "local_key_lan": [LOCAL_KEY],
    })
    kq = chay(mod.suc_khoe())  # không được ném lỗi
    assert isinstance(kq, dict)
    assert "x1" not in mod._ban_do_lan


# ───────── Thiết bị LAN GIẢ (TCP loopback) cho B5 + cmd 7 ─────────

def _aes_tho(khoa: bytes, du_lieu: bytes) -> bytes:
    pad = 16 - len(du_lieu) % 16
    du_lieu += bytes([pad]) * pad
    enc = Cipher(algorithms.AES(khoa), modes.ECB()).encryptor()
    return enc.update(du_lieu) + enc.finalize()


class ThietBiLanSua:
    """Thiết bị Tuya giả cho vòng sửa.

    che_cmd7: «day_du» trả dps như thường; «rong0»/«rong1» trả khung chỉ có
    mã trả 0/1 (không payload) cho lệnh điều khiển — như thiết bị 3.3 thật.
    """

    def __init__(self, khoa: str, dps: dict, che_cmd7: str = "day_du"):
        self.khoa = khoa.encode()
        self.dps = dict(dps)
        self.che_cmd7 = che_cmd7
        self.lenhs: list[dict] = []
        self._dung = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.cong = self.sock.getsockname()[1]
        self.sock.listen(5)
        self.luong = threading.Thread(target=self._chay, daemon=True)
        self.luong.start()

    def _doc(self, s, n):
        b = b""
        while len(b) < n:
            c = s.recv(n - len(b))
            if not c:
                return None
            b += c
        return b

    def _chay(self):
        self.sock.settimeout(0.5)
        while not self._dung.is_set():
            try:
                ket, _ = self.sock.accept()
            except socket.timeout:
                continue
            threading.Thread(target=self._phien, args=(ket,),
                             daemon=True).start()

    def _gui_khung(self, ket, seq, cmd, du_lieu):
        dau = struct.pack(">IIII", 0x000055AA, seq, cmd, len(du_lieu) + 8)
        crc = struct.pack(">I", zlib.crc32(dau + du_lieu) & 0xFFFFFFFF)
        ket.sendall(dau + du_lieu + crc + b"\x00\x00\xaa\x55")

    def _phien(self, ket):
        try:
            while True:
                dau = self._doc(ket, 16)
                if not dau:
                    return
                tien_to, seq, cmd, dai = struct.unpack(">IIII", dau)
                if tien_to != 0x000055AA:
                    return
                than = self._doc(ket, dai)
                if not than:
                    return
                payload = than[:-8]
                if cmd == 10:  # HOI_DP
                    ban_ma = b"3.3" + b"\x00" * 12 + _aes_tho(
                        self.khoa, json.dumps(
                            {"dps": self.dps, "t": int(time.time())}).encode())
                    self._gui_khung(ket, seq, 8, struct.pack(">I", 0) + ban_ma)
                elif cmd == 7:  # DIEU_KHIEN
                    yeu = json.loads(self._giai(payload))
                    self.lenhs.append(yeu.get("dps", {}))
                    self.dps.update(yeu.get("dps", {}))
                    if self.che_cmd7 == "day_du":
                        ban_ma = b"3.3" + b"\x00" * 12 + _aes_tho(
                            self.khoa, json.dumps(
                                {"dps": self.dps,
                                 "t": int(time.time())}).encode())
                        self._gui_khung(ket, seq, 8,
                                        struct.pack(">I", 0) + ban_ma)
                    elif self.che_cmd7 in ("rong0", "rong1"):
                        ma_tra = 0 if self.che_cmd7 == "rong0" else 1
                        self._gui_khung(ket, seq, 8, struct.pack(">I", ma_tra))
        except OSError:
            pass
        finally:
            ket.close()

    def _giai(self, payload: bytes) -> bytes:
        if payload.startswith(b"3.3"):
            payload = payload[15:]
        dec = Cipher(algorithms.AES(self.khoa), modes.ECB()).decryptor()
        du_lieu = dec.update(payload) + dec.finalize()
        return du_lieu[:-du_lieu[-1]]

    def dong(self):
        self._dung.set()
        try:
            self.sock.close()
        except OSError:
            pass


@pytest.fixture
def tb_day_du():
    tb = ThietBiLanSua(LOCAL_KEY, {"1": True, "2": 0})
    yield tb
    tb.dong()


def _mod_lan_that(may, cong, khoa_lan, **them):
    """Module đám mây giả + thiết bị DEN khai LAN trỏ vào thiết bị giả."""
    cau_hinh = {
        "access_id": AID, "access_secret": SECRET, "vung": "america",
        "thiet_bi_lan": json.dumps(
            [{"id": DEN, "ip": "127.0.0.1", "cong": cong, "ten": "Đèn LAN",
              "nganh": "dj"}]),
        "local_key_lan": khoa_lan,
    }
    cau_hinh.update(them)
    mod = _module_moi()
    mod.nap(cau_hinh)
    khach = httpx.AsyncClient(transport=httpx.MockTransport(may))
    mod._dam_may._lay_client = lambda: khach
    return mod


# ───────── B5a: thiếu local_key → tự lấy qua đám mây ─────────

def test_b5a_lan_thieu_khoa_tu_lay_qua_dam_may(may, tb_day_du, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    mod = _mod_lan_that(may, tb_day_du.cong, [])  # KHÔNG khai local_key
    tt = chay(mod.trang_thai([f"tuya.{DEN}"]))
    assert tt[f"tuya.{DEN}"]["hop_le"] is True
    assert tt[f"tuya.{DEN}"]["gia_tri"] == "on"
    assert may.so_lan_lay_khoa == 1  # đã hỏi đám mây đúng một lần
    tt2 = chay(mod.trang_thai([f"tuya.{DEN}"]))
    assert tt2[f"tuya.{DEN}"]["hop_le"] is True
    assert may.so_lan_lay_khoa == 1  # lần hai dùng khoá đã nhớ, không hỏi lại


def test_b5a_suc_khoe_lan_thieu_khoa(may, tb_day_du, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    mod = _mod_lan_that(may, tb_day_du.cong, [])
    kq = chay(mod.suc_khoe())
    assert kq["lan"]["ok"] is True
    assert kq["lan"]["so_ket_noi_duoc"] == 1


# ───────── B5b: LAN hỏng → quay về đám mây ─────────

def test_b5b_lan_khoa_sai_roi_ve_dam_may(may, tb_day_du, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    mod = _mod_lan_that(may, tb_day_du.cong, ["khoa-sai-16-ky-tu"])
    tt = chay(mod.trang_thai([f"tuya.{DEN}"]))
    assert tt[f"tuya.{DEN}"]["hop_le"] is True  # từ shadow đám mây
    assert tt[f"tuya.{DEN}"]["gia_tri"] == "on"


def test_b5b_lan_mat_ket_noi_roi_ve_dam_may(may, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    # Cổng không ai nghe trên loopback → kết nối bị từ chối ngay
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    cong_chet = s.getsockname()[1]
    s.close()
    mod = _mod_lan_that(may, cong_chet, [LOCAL_KEY])
    tt = chay(mod.trang_thai([f"tuya.{DEN}"]))
    assert tt[f"tuya.{DEN}"]["hop_le"] is True
    assert tt[f"tuya.{DEN}"]["gia_tri"] == "on"


def test_b5b_lan_loi_khong_co_dam_may_thi_bao_loi_lan(monkeypatch):
    """Không có đám mây để rơi về → vẫn báo lỗi LAN như cũ (không nuốt lỗi)."""
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    tb = ThietBiLanSua(LOCAL_KEY, {"1": True})
    try:
        mod = _module_moi()
        mod.nap({
            "thiet_bi_lan": json.dumps(
                [{"id": "lan-x", "ip": "127.0.0.1", "cong": tb.cong,
                  "nganh": "cz"}]),
            "local_key_lan": ["khoa-sai-16-ky-tu"],
        })
        tt = chay(mod.trang_thai(["tuya.lan-x"]))
        assert tt["tuya.lan-x"]["hop_le"] is False
        assert "loi" in tt["tuya.lan-x"]
    finally:
        tb.dong()


# ───────── Nghi ngờ cmd 7: phản hồi chỉ có mã trả ─────────

def test_cmd7_phan_hoi_chi_ma_tra_0_la_thanh_cong(monkeypatch):
    """Thiết bị 3.3 thật: khung trả của lệnh điều khiển chỉ mang mã trả 0.
    Lệnh ĐÃ chạy → dat_dps phải báo thành công, không phải «giải mã thất bại»."""
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    _, goi = _tai()
    tb = ThietBiLanSua(LOCAL_KEY, {"1": True}, che_cmd7="rong0")
    try:
        kq = chay(goi.lan.dat_dps("127.0.0.1", tb.cong, LOCAL_KEY, "3.3",
                                  "dev-cmd7", {"1": False}))
        assert kq == {"1": False}
        assert tb.lenhs[-1] == {"1": False}  # lệnh thật sự đã tới thiết bị
    finally:
        tb.dong()


def test_cmd7_ma_tra_khac_0_van_bao_loi(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    _, goi = _tai()
    tb = ThietBiLanSua(LOCAL_KEY, {"1": True}, che_cmd7="rong1")
    try:
        with pytest.raises(goi.lan.LoiLan):
            chay(goi.lan.dat_dps("127.0.0.1", tb.cong, LOCAL_KEY, "3.3",
                                 "dev-cmd7", {"1": False}))
    finally:
        tb.dong()


def test_cmd7_phan_hoi_day_du_van_chay(monkeypatch, tb_day_du):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    _, goi = _tai()
    kq = chay(goi.lan.dat_dps("127.0.0.1", tb_day_du.cong, LOCAL_KEY, "3.3",
                              "dev-cmd7", {"1": False}))
    assert kq == {"1": False, "2": 0}
