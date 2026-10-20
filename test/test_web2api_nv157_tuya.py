"""Test module tuya (modules/tuya/) — NV157: thiết bị Tuya chuẩn C1, hai đường
đám mây (Open API ký HMAC-SHA256) và LAN (giao thức 6668 tự viết, thư viện chuẩn
+ cryptography đã có trong requirements lõi).

Mọi dịch vụ đều GIẢ: đám mây qua httpx.MockTransport có kiểm chữ ký thật; thiết bị
LAN là máy chủ TCP/UDP loopback dựng bằng socket thư viện chuẩn. Không mạng thật.
Khoá/IP trong test là GIẢ rõ ràng (192.168.99.x là dải riêng dùng cho test).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
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

from app.kham_pha import doc_manifest, kiem_manifest, nap_ma

GOC = Path(__file__).resolve().parents[1]
THU_MUC_MODULE = GOC / "modules" / "tuya"

AID = "access-id-gia-0123"
SECRET = "access-secret-gia-456789"
LOCAL_KEY = "localkeygia12345"  # 16 ký tự — khoá LAN giả
TB1 = "dev-gia-0001"   # đèn (dj)
TB_KHOA = "dev-gia-khoa"  # khoá cửa (videolock)
TB_BINH = "dev-gia-binh"  # bình nóng lạnh (rs)
TB_QUAT = "dev-gia-quat"  # quạt (ks) — thiết bị GHI được nhưng mã khác đèn

_goi = None


def chay(coro):
    return asyncio.run(coro)


def _tai():
    global _goi
    if _goi is None:
        manifest, loi = doc_manifest(THU_MUC_MODULE)
        assert loi is None, loi
        lop, ten_goi = nap_ma(THU_MUC_MODULE, manifest, "test157")
        _goi = (lop, sys.modules[ten_goi])
    return _goi


def _manifest():
    manifest, loi = doc_manifest(THU_MUC_MODULE)
    assert loi is None, loi
    return manifest


def _module_moi():
    lop, _ = _tai()
    return lop(_manifest())


def _mod_dam_may(**them):
    """Module đã nạp cấu hình đám mây giả; client trỏ vào MockTransport."""
    mod = _module_moi()
    cau_hinh = {"access_id": AID, "access_secret": SECRET, "vung": "america"}
    cau_hinh.update(them)
    mod.nap(cau_hinh)
    return mod

# ───────── Máy chủ Tuya đám mây GIẢ (kiểm chữ ký như thật) ─────────

THIET_BI_GIA = [
    {"id": TB1, "name": "Đèn phòng khách", "category": "dj", "model": "M1",
     "is_online": True},
    {"id": TB_KHOA, "name": "Khoá cửa chính", "category": "videolock",
     "model": "L1", "is_online": True},
    {"id": TB_BINH, "name": "Bình nóng lạnh", "category": "rs", "model": "W1",
     "is_online": False},
    {"id": TB_QUAT, "name": "Quạt phòng ngủ", "category": "ks", "model": "F1",
     "is_online": True},
]

THUOC_TINH_GIA = {
    TB1: [{"code": "switch_led", "value": True},
          {"code": "bright_value_v2", "value": 500}],
    TB_KHOA: [{"code": "unlock_fingerprint", "value": 3}],
    TB_BINH: [{"code": "switch", "value": False},
              {"code": "temp_current", "value": 40}],
    TB_QUAT: [{"code": "switch", "value": False}],
}

CHUC_NANG_GIA = {
    TB1: {
        "functions": [
            {"code": "switch_led", "type": "Boolean", "values": "{}"},
            {"code": "bright_value_v2", "type": "Integer",
             "values": '{"min": 10, "max": 1000, "scale": 0, "step": 1}'},
        ],
        "status": [
            {"code": "switch_led", "type": "Boolean", "values": "{}", "dp_id": 1},
            {"code": "bright_value_v2", "type": "Integer",
             "values": '{"min": 10, "max": 1000, "scale": 0, "step": 1}', "dp_id": 2},
        ],
    },
    TB_KHOA: {
        "functions": [{"code": "unlock_remote", "type": "Boolean", "values": "{}"}],
        "status": [{"code": "unlock_fingerprint", "type": "Integer",
                    "values": "{}", "dp_id": 12}],
    },
    TB_QUAT: {
        "functions": [{"code": "switch", "type": "Boolean", "values": "{}"}],
        "status": [{"code": "switch", "type": "Boolean", "values": "{}",
                    "dp_id": 1}],
    },
    TB_BINH: {
        "functions": [
            {"code": "switch", "type": "Boolean", "values": "{}"},
            {"code": "temp_set", "type": "Integer",
             "values": '{"min": 30, "max": 75, "scale": 0, "step": 1, "unit": "℃"}'},
        ],
        "status": [
            {"code": "switch", "type": "Boolean", "values": "{}", "dp_id": 1},
            {"code": "temp_current", "type": "Integer",
             "values": '{"unit": "℃"}', "dp_id": 3},
        ],
    },
}


class MayTuyaGia:
    """MockTransport: kiểm chữ ký HMAC như máy chủ Tuya thật, ghi lại lệnh."""

    def __init__(self):
        self.lenh: list[dict] = []
        self.so_lan_token = 0

    def _kiem_ky(self, req: httpx.Request) -> bool:
        aid = req.headers.get("client_id", "")
        token = req.headers.get("access_token", "")
        t = req.headers.get("t", "")
        duong = req.url.path
        if req.url.query:
            cac = sorted(req.url.query.decode().split("&"))
            duong += "?" + "&".join(cac)
        chuoi = (f"{aid}{token}{t}{req.method}\n"
                 f"{hashlib.sha256(req.content).hexdigest()}\n\n{duong}")
        mong = hmac.new(SECRET.encode(), chuoi.encode(),
                        hashlib.sha256).hexdigest().upper()
        return hmac.compare_digest(mong, req.headers.get("sign", ""))

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if not self._kiem_ky(req):
            return httpx.Response(200, json={"success": False, "msg": "sign invalid"})
        duong = req.url.path
        if duong == "/v1.0/token":
            self.so_lan_token += 1
            return httpx.Response(200, json={
                "success": True,
                "result": {"access_token": "TOKEN-GIA", "expire_time": 7200}})
        if duong == "/v2.0/cloud/thing/device":
            moc = req.url.params.get("last_row_key", "")
            if not moc:
                return httpx.Response(200, json={
                    "success": True,
                    "result": {"list": THIET_BI_GIA[:2], "last_row_key": "trang2"}})
            return httpx.Response(200, json={
                "success": True, "result": {"list": THIET_BI_GIA[2:]}})
        if "/shadow/properties" in duong:
            ma = duong.split("/")[4]
            return httpx.Response(200, json={
                "success": True,
                "result": {"properties": THUOC_TINH_GIA.get(ma, [])}})
        if duong.endswith("/specifications"):
            ma = duong.split("/")[3]
            return httpx.Response(200, json={
                "success": True, "result": CHUC_NANG_GIA.get(ma, {})})
        if duong.startswith("/v1.0/iot-03/devices/") and duong.endswith("/commands"):
            than = json.loads(req.content or b"{}")
            self.lenh.append(than)
            return httpx.Response(200, json={"success": True, "result": True})
        if duong.startswith("/v1.0/devices/"):
            ma = duong.split("/")[3]
            return httpx.Response(200, json={
                "success": True, "result": {"local_key": LOCAL_KEY, "id": ma}})
        return httpx.Response(404, json={"success": False, "msg": "not found"})


@pytest.fixture
def may_gia():
    return MayTuyaGia()


@pytest.fixture
def mod_may(may_gia):
    """Module đám mây với client giả (MockTransport)."""
    mod = _mod_dam_may()
    khach = httpx.AsyncClient(transport=httpx.MockTransport(may_gia))
    mod._dam_may._lay_client = lambda: khach
    return mod

# ───────── Manifest ─────────

def test_manifest_hop_le_va_chuan_c1():
    m = _manifest()
    assert kiem_manifest(m) is None
    assert m["id"] == "tuya"
    assert m["cung_cap"] == ["thiet_bi"]
    assert m["hang"] == "A"


def test_truong_bi_mat_khai_dung_kieu():
    m = _manifest()
    truong = {t["khoa"]: t for t in m["truong_cau_hinh"]}
    assert truong["access_secret"]["kieu"] == "bi_mat"
    assert truong["local_key_lan"]["kieu"] == "danh_sach_bi_mat"
    # Điều khiển mặc định TẮT
    assert truong["cho_phep_dieu_khien"]["mac_dinh"] == "tat"
    # Mọi trường bí mật phải có mo_ta nói rõ lấy ở đâu / tự đặt thế nào
    for khoa in ("access_secret", "local_key_lan"):
        assert len(truong[khoa]["mo_ta"]) > 40


# ───────── Ký HMAC (vector từ c2a, đo công thức) ─────────

def test_ky_dung_cong_thuc_tuya():
    """Sai một dấu xuống dòng là Tuya trả 'sign invalid' — vector của c2a."""
    _, goi = _tai()
    dam_may = goi.dam_may
    body_hash = hashlib.sha256(b"").hexdigest()
    chuoi = AID + "1234GET\n" + body_hash + "\n\n/v1.0/token?grant_type=1"
    mong = hmac.new(SECRET.encode(), chuoi.encode(),
                    hashlib.sha256).hexdigest().upper()
    assert dam_may._ky(SECRET, chuoi) == mong


def test_che_bi_mat_khong_lo():
    _, goi = _tai()
    c = goi.dam_may.che_bi_mat({
        "access_id": "aaaa1111bbbb2222cccc",
        "access_secret": "dddd3333eeee4444ffff",
        "local_key": "abc123khoa", "password": "MatKhau@123"})
    phang = json.dumps(c, ensure_ascii=False)
    for bi_mat in ("dddd3333eeee4444ffff", "MatKhau@123", "abc123khoa",
                   "aaaa1111bbbb2222cccc"):
        assert bi_mat not in phang


def test_may_chu_chi_thuoc_mien_tuya():
    _, goi = _tai()
    for vung, base in goi.dam_may.MAY_CHU.items():
        assert goi.dam_may.kiem_mien_tuya(base), vung
    assert not goi.dam_may.kiem_mien_tuya("https://evil.example.com")
    assert not goi.dam_may.kiem_mien_tuya("https://openapi.tuyaus.com.evil.com")
    assert not goi.dam_may.kiem_mien_tuya("http://openapi.tuyaus.com")


# ───────── Đám mây qua máy giả ─────────

def test_token_dung_lai_khong_xin_moi_moi_lan(mod_may, may_gia):
    chay(mod_may._dam_may.goi_api("/v2.0/cloud/thing/device?page_size=20"))
    chay(mod_may._dam_may.goi_api("/v2.0/cloud/thing/device?page_size=20"))
    assert may_gia.so_lan_token == 1


def test_danh_sach_thiet_bi_lat_trang(mod_may):
    ds = chay(mod_may._dam_may.danh_sach_thiet_bi())
    assert [d["id"] for d in ds] == [TB1, TB_KHOA, TB_BINH, TB_QUAT]
    assert ds[0]["loai"] == "Đèn"


def test_thuc_the_rui_ro_theo_cau_truc(mod_may):
    tt = {x["id"]: x for x in chay(mod_may.thuc_the(None))}
    assert tt[f"tuya.{TB1}"]["rui_ro_mac_dinh"] == "ghi"
    # Khoá cửa và bình nóng lạnh: nguy_hiem và KHÔNG có hành động ghi nào
    for ma in (TB_KHOA, TB_BINH):
        assert tt[f"tuya.{ma}"]["rui_ro_mac_dinh"] == "nguy_hiem"
        assert tt[f"tuya.{ma}"]["tinh_nang"] == []
    # Đèn có hành động bật/tắt theo mã điểm dữ liệu của chính thiết bị
    hd = {x["hanh_dong"] for x in tt[f"tuya.{TB1}"]["tinh_nang"]}
    assert "bat_switch_led" in hd and "tat_switch_led" in hd
    assert "dat_bright_value_v2" in hd


def test_trang_thai_dam_may(mod_may):
    tt = chay(mod_may.trang_thai([f"tuya.{TB1}", f"tuya.{TB_BINH}",
                                  "tuya.khong-co"]))
    assert tt[f"tuya.{TB1}"]["gia_tri"] == "on"
    assert tt[f"tuya.{TB1}"]["hop_le"] is True
    assert tt[f"tuya.{TB_BINH}"]["hop_le"] is False  # is_online False
    assert "tuya.khong-co" not in tt


def test_dieu_khien_mac_dinh_tat_khong_goi_mang(mod_may, may_gia):
    chay(mod_may.thuc_the(None))
    kq = chay(mod_may.thuc_hien(f"tuya.{TB1}", "bat_switch_led", {}))
    assert kq["ok"] is False
    assert may_gia.lenh == []  # không một lệnh nào được gửi


def test_dieu_khien_bat_gui_lenh_dung(mod_may, may_gia):
    mod = _mod_dam_may(cho_phep_dieu_khien="bat")
    khach = httpx.AsyncClient(transport=httpx.MockTransport(may_gia))
    mod._dam_may._lay_client = lambda: khach
    chay(mod.thuc_the(None))
    kq = chay(mod.thuc_hien(f"tuya.{TB1}", "bat_switch_led", {}))
    assert kq["ok"] is True
    assert may_gia.lenh == [{"commands": [{"code": "switch_led", "value": True}]}]


def test_khoa_cua_khong_bao_gio_dieu_khien(mod_may, may_gia):
    mod = _mod_dam_may(cho_phep_dieu_khien="bat")
    khach = httpx.AsyncClient(transport=httpx.MockTransport(may_gia))
    mod._dam_may._lay_client = lambda: khach
    chay(mod.thuc_the(None))
    kq = chay(mod.thuc_hien(f"tuya.{TB_KHOA}", "bat_unlock_remote", {}))
    assert kq["ok"] is False
    kq2 = chay(mod.thuc_hien(f"tuya.{TB_BINH}", "bat_switch", {}))
    assert kq2["ok"] is False
    assert may_gia.lenh == []


def test_thuc_hien_id_la_bi_tu_choi(mod_may, may_gia):
    mod = _mod_dam_may(cho_phep_dieu_khien="bat")
    khach = httpx.AsyncClient(transport=httpx.MockTransport(may_gia))
    mod._dam_may._lay_client = lambda: khach
    chay(mod.thuc_the(None))
    kq = chay(mod.thuc_hien("tuya.thiet-bi-cua-nguoi-khac", "bat_switch_led", {}))
    assert kq["ok"] is False
    kq = chay(mod.thuc_hien("home_assistant.light.den", "bat_switch_led", {}))
    assert kq["ok"] is False
    # Mượn hành động của THIẾT BỊ KHÁC: quạt không có bat_switch_led —
    # dù hành động tồn tại ở đèn, gọi qua quạt phải bị từ chối.
    kq = chay(mod.thuc_hien(f"tuya.{TB_QUAT}", "bat_switch_led", {}))
    assert kq["ok"] is False
    assert may_gia.lenh == []


def test_suc_khoe_khong_lo_bi_mat(mod_may):
    kq = chay(mod_may.suc_khoe())
    assert kq["ok"] is True
    phang = json.dumps(kq, ensure_ascii=False)
    assert SECRET not in phang and AID not in phang


def test_chua_cau_hinh_suc_khoe_that_bai():
    mod = _module_moi()
    mod.nap({})
    kq = chay(mod.suc_khoe())
    assert kq["ok"] is False


def test_cai_vung_la_bi_tu_choi():
    mod = _module_moi()
    kq = chay(mod.cai({"access_id": AID, "access_secret": SECRET,
                       "vung": "sao-hoa"}))
    assert kq["ok"] is False


def test_cai_thieu_mot_nua_khoa_dam_may():
    mod = _module_moi()
    kq = chay(mod.cai({"access_id": AID, "vung": "america"}))
    assert kq["ok"] is False

# ───────── LAN: AES (vector NIST đo thật) ─────────

def test_aes_ecb_vector_nist():
    """NIST SP 800-38A F.1.1: khoá/mã hoá/thông điệp đo độc lập."""
    _, goi = _tai()
    khoa = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    ban_ro = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
    mong = bytes.fromhex("3ad77bb40d7a3660a89ecaf32466ef97")
    assert goi.lan.aes_ecb_tho(khoa, ban_ro) == mong
    assert goi.lan.aes_ecb_giai_tho(khoa, mong) == ban_ro


def test_khung_tron_vong_crc():
    """Đóng/mở khung: CRC đúng qua, CRC sai bị từ chối."""
    _, goi = _tai()
    lan = goi.lan
    seq = 7
    payload = json.dumps({"dps": {"1": True}}).encode()
    khung = lan.dong_khung(seq, lan.LENH_HOI_DP, payload, "3.3", LOCAL_KEY)
    seq2, cmd2, du_lieu2 = lan.mo_khung(khung, "3.3", LOCAL_KEY)
    assert seq2 == seq and cmd2 == lan.LENH_HOI_DP
    assert json.loads(du_lieu2)["dps"] == {"1": True}
    # Sửa 1 bit trong payload → CRC sai → LoiLan
    hong = bytearray(khung)
    hong[20] ^= 0x01
    try:
        lan.mo_khung(bytes(hong), "3.3", LOCAL_KEY)
    except lan.LoiLan:
        pass
    else:
        raise AssertionError("CRC sai phải bị từ chối")
    # Tiền tố sai → LoiLan
    try:
        lan.mo_khung(b"\x00" * 24, "3.3", LOCAL_KEY)
    except lan.LoiLan:
        pass
    else:
        raise AssertionError("tiền tố sai phải bị từ chối")


def test_khoa_sai_giai_ma_hong():
    """Bài học c2a: lỗi giải mã nghĩa là local_key ĐÃ ĐỔI — phải báo, không sập."""
    _, goi = _tai()
    lan = goi.lan
    khung = lan.dong_khung(1, lan.LENH_HOI_DP,
                           json.dumps({"dps": {"1": True}}).encode(),
                           "3.3", LOCAL_KEY)
    try:
        lan.mo_khung(khung, "3.3", "khoa-sai-16-ky-tu")
    except lan.LoiLan as e:
        assert "khoá" in str(e).lower() or "giải mã" in str(e).lower()
    else:
        raise AssertionError("khoa sai phải ném LoiLan")


# ───────── Thiết bị LAN GIẢ (máy chủ TCP thật trên loopback) ─────────

def _aes_tho(khoa: bytes, du_lieu: bytes) -> bytes:
    """Mã hoá AES-ECB PKCS7 — phía máy giả, xây ĐỘC LẬP bằng cryptography."""
    pad = 16 - len(du_lieu) % 16
    du_lieu += bytes([pad]) * pad
    enc = Cipher(algorithms.AES(khoa), modes.ECB()).encryptor()
    return enc.update(du_lieu) + enc.finalize()


class ThietBiLanGia:
    """Thiết bị Tuya giả: nhận HOI_DP/DIEU_KHIEN, trả TRANG_THAI mã hoá 3.3."""

    def __init__(self, khoa: str, dps: dict):
        self.khoa = khoa.encode()
        self.dps = dict(dps)
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
            threading.Thread(target=self._phien, args=(ket,), daemon=True).start()

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
                payload, crc, hau_to = than[:-8], than[-8:-4], than[-4:]
                if hau_to != b"\x00\x00\xaa\x55":
                    return
                if zlib.crc32(dau + than[:-8]) & 0xFFFFFFFF != struct.unpack(">I", crc)[0]:
                    return
                tra = None
                if cmd == 10:  # HOI_DP
                    tra = (8, {"dps": self.dps, "t": int(time.time())})
                elif cmd == 7:  # DIEU_KHIEN
                    than_du_lieu = self._giai(payload)
                    yeu = json.loads(than_du_lieu)
                    self.lenhs.append(yeu.get("dps", {}))
                    self.dps.update(yeu.get("dps", {}))
                    tra = (8, {"dps": self.dps, "t": int(time.time())})
                if tra:
                    cmd_tra, noi_dung = tra
                    ban_ma = b"3.3" + b"\x00" * 12 + _aes_tho(
                        self.khoa, json.dumps(noi_dung).encode())
                    ret = struct.pack(">I", 0)
                    du_lieu = ret + ban_ma
                    dai_tra = len(du_lieu) + 8
                    dau_tra = struct.pack(">IIII", 0x000055AA, seq, cmd_tra, dai_tra)
                    crc_tra = struct.pack(">I", zlib.crc32(dau_tra + du_lieu) & 0xFFFFFFFF)
                    ket.sendall(dau_tra + du_lieu + crc_tra + b"\x00\x00\xaa\x55")
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
def thiet_bi_gia():
    tb = ThietBiLanGia(LOCAL_KEY, {"1": True, "2": 0})
    yield tb
    tb.dong()


def _mod_lan(thiet_bi_gia, **them):
    mod = _module_moi()
    cau_hinh = {
        "thiet_bi_lan": json.dumps(
            [{"id": "lan-0001", "ip": "127.0.0.1", "cong": thiet_bi_gia.cong,
              "ten": "Ổ cắm giả", "nganh": "cz"}]),
        "local_key_lan": [LOCAL_KEY],
    }
    cau_hinh.update(them)
    mod.nap(cau_hinh)
    return mod


def test_lan_trang_thai_doc_duoc(thiet_bi_gia, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    mod = _mod_lan(thiet_bi_gia)
    dps = chay(mod._lan.hoi(mod._ban_do_lan["lan-0001"]))
    assert dps == {"1": True, "2": 0}


def test_lan_thiet_bi_qua_thuc_the(thiet_bi_gia, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    mod = _mod_lan(thiet_bi_gia)
    tt = {x["id"]: x for x in chay(mod.thuc_the(None))}
    ent = tt["tuya.lan-0001"]
    assert ent["loai"] == "Ổ cắm"  # nganh từ cấu hình → tên loại
    assert ent["rui_ro_mac_dinh"] == "ghi"
    # Thiết bị LAN đọc được → trạng thái hợp lệ
    trang = chay(mod.trang_thai(["tuya.lan-0001"]))
    assert trang["tuya.lan-0001"]["hop_le"] is True
    assert trang["tuya.lan-0001"]["gia_tri"] == "on"


def test_lan_khoa_sai_bao_loi_khong_sap(thiet_bi_gia, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    mod = _mod_lan(thiet_bi_gia, local_key_lan=["khoa-sai-16-ky-tu"])
    trang = chay(mod.trang_thai(["tuya.lan-0001"]))
    assert trang["tuya.lan-0001"]["hop_le"] is False
    assert "khoá" in str(trang["tuya.lan-0001"].get("loi", "")).lower()


def test_lan_dieu_khien_gui_dps(thiet_bi_gia, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    mod = _mod_lan(thiet_bi_gia, cho_phep_dieu_khien="bat")
    chay(mod.thuc_the(None))
    kq = chay(mod.thuc_hien("tuya.lan-0001", "tat_1", {}))
    assert kq["ok"] is True
    assert thiet_bi_gia.lenhs and thiet_bi_gia.lenhs[-1] == {"1": False}


def test_lan_ip_cong_cong_bi_tu_choi_o_cai():
    mod = _module_moi()
    kq = chay(mod.cai({
        "thiet_bi_lan": json.dumps([{"id": "x", "ip": "8.8.8.8"}]),
        "local_key_lan": [LOCAL_KEY]}))
    assert kq["ok"] is False


def test_lan_ip_loopback_bi_tu_choi_khi_khong_phai_test():
    mod = _module_moi()
    kq = chay(mod.cai({
        "thiet_bi_lan": json.dumps([{"id": "x", "ip": "127.0.0.1"}]),
        "local_key_lan": [LOCAL_KEY]}))
    assert kq["ok"] is False


def test_lan_so_khoa_khac_so_thiet_bi_bao_loi():
    mod = _module_moi()
    kq = chay(mod.cai({
        "thiet_bi_lan": json.dumps([{"id": "a", "ip": "192.168.99.2"},
                                    {"id": "b", "ip": "192.168.99.3"}]),
        "local_key_lan": [LOCAL_KEY]}))
    assert kq["ok"] is False
    assert "cùng thứ tự" in kq["loi"]


# ───────── Dò thiết bị (phân tích gói phát quảng bá) ─────────

def test_phan_tich_phat_quang_ban_ro():
    _, goi = _tai()
    goi_phat = {"ip": "192.168.99.10", "gwId": "bf0123abcd", "version": "3.1"}
    dg = struct.pack(">IIII", 0x000055AA, 1, 19,
                     len(json.dumps(goi_phat).encode()) + 8)
    dg += json.dumps(goi_phat).encode()
    dg += struct.pack(">I", zlib.crc32(dg) & 0xFFFFFFFF) + b"\x00\x00\xaa\x55"
    kq = goi.lan.phan_tich_phat_quang(dg, 6667)
    assert kq == {"id": "bf0123abcd", "ip": "192.168.99.10", "version": "3.1"}


def test_phan_tich_phat_quang_ma_hoa():
    _, goi = _tai()
    khoa = hashlib.md5(b"yGAdlopoPVldABfn").digest()
    goi_phat = {"ip": "192.168.99.11", "gwId": "bf0456efgh", "version": "3.3"}
    ban_ma = goi.lan.aes_ecb_ma_hoa(khoa, json.dumps(goi_phat).encode())
    dg = struct.pack(">IIII", 0x000055AA, 2, 19, len(ban_ma) + 8)
    dg += ban_ma
    dg += struct.pack(">I", zlib.crc32(dg) & 0xFFFFFFFF) + b"\x00\x00\xaa\x55"
    kq = goi.lan.phan_tich_phat_quang(dg, 6666)
    assert kq == {"id": "bf0456efgh", "ip": "192.168.99.11", "version": "3.3"}


def test_do_thiet_bi_nhan_qua_udp_that(monkeypatch):
    """Gói quảng bá thật qua UDP loopback → module dò được."""
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    _, goi = _tai()
    goi_phat = {"ip": "192.168.99.12", "gwId": "bf0789ijkl", "version": "3.1"}
    noi_dung = json.dumps(goi_phat).encode()
    dg = (struct.pack(">IIII", 0x000055AA, 3, 19, len(noi_dung) + 8) + noi_dung
          + struct.pack(">I", zlib.crc32(
              struct.pack(">IIII", 0x000055AA, 3, 19, len(noi_dung) + 8)
              + noi_dung) & 0xFFFFFFFF) + b"\x00\x00\xaa\x55")
    try:
        _thu = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _thu.sendto(b"", ("127.0.0.1", 6667))
        _thu.close()
    except PermissionError:
        pytest.skip("sandbox chặn UDP (EPERM) — parse quảng bá đã kiểm riêng")
    def gui():
        time.sleep(0.2)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.sendto(dg, ("127.0.0.1", 6667))
        except PermissionError:
            pytest.skip("sandbox chặn UDP (EPERM) — parse đã kiểm riêng")
    t = threading.Thread(target=gui, daemon=True)
    t.start()
    ds = goi.lan.do_thiet_bi(giay=2.0, chi_loopback=True)
    t.join()
    assert any(d["id"] == "bf0789ijkl" for d in ds)

# ───────── cài() với LAN + đám mây ─────────

def test_cai_lan_hop_le(mod_may):
    mod = _module_moi()
    kq = chay(mod.cai({
        "thiet_bi_lan": json.dumps(
            [{"id": "a", "ip": "192.168.99.2", "ten": "Đèn LAN"}]),
        "local_key_lan": [LOCAL_KEY]}))
    assert kq["ok"] is True
    assert kq["cau_hinh_luu"]["thiet_bi_lan"]


def test_cai_khong_cau_hinh_gi_bao_loi():
    mod = _module_moi()
    kq = chay(mod.cai({}))
    assert kq["ok"] is False


def test_cai_dam_may_goi_that_qua_may_gia(may_gia):
    mod = _module_moi()
    khach = httpx.AsyncClient(transport=httpx.MockTransport(may_gia))
    mod._dam_may = None  # ép dựng lại trong cai()
    mod._lay_client_gia = lambda: khach
    kq = chay(mod.cai({"access_id": AID, "access_secret": SECRET,
                       "vung": "america"}))
    assert kq["ok"] is True
    assert kq["cau_hinh_luu"]["access_id"] == AID
    # bí mật không nằm trong cau_hinh_luu dạng thô trả về? (lõi lưu riêng)
    assert kq["cau_hinh_luu"]["access_secret"] == SECRET


def test_cai_dam_may_sai_khoa_bao_loi_ro():
    def may_loi(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "msg": "sign invalid"})

    mod = _module_moi()
    khach = httpx.AsyncClient(transport=httpx.MockTransport(may_loi))
    mod._lay_client_gia = lambda: khach
    kq = chay(mod.cai({"access_id": AID, "access_secret": "sai",
                       "vung": "america"}))
    assert kq["ok"] is False
    assert "sai" in kq["loi"] and "sai" in kq["loi"]


def test_suc_khoe_lan(monkeypatch, thiet_bi_gia):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    mod = _mod_lan(thiet_bi_gia)
    kq = chay(mod.suc_khoe())
    assert kq["ok"] is True
    assert "lan" in kq


def test_dung_dong_sach():
    mod = _module_moi()
    chay(mod.dung())
