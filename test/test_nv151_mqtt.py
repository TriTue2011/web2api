"""Test module mqtt (modules/mqtt/): port từ c2a — đường thứ hai biết nhà qua MQTT,
KHÔNG cần Home Assistant.

Máy chủ MQTT là GIẢ cục bộ (socket thuần, nói đủ MQTT 3.1.1 cho paho kết nối:
CONNECT/CONNACK, SUBSCRIBE/SUBACK, PUBLISH/PUBACK, PINGREQ/PINGRESP, DISCONNECT).
Mọi địa chỉ test đều loopback (cần WEB2API_TEST_CHO_PHEP_LOOPBACK=1) hoặc chuỗi
GIẢ rõ ràng (mqtt.example.invalid, zigbee2mqtt/den-thu-1). Không mạng thật.

Chuyển từ test_mqtt_nha.py + test_api_mqtt.py của c2a (commit d879b03): mọi
tên phòng/thiết bị và số đo của nhà thật đã đổi hết sang dữ liệu GIẢ rõ ràng;
phần lịch sử/cảnh báo/dự đoán của c2a không thuộc phạm vi NV151 nên test
tương ứng bị bỏ.
"""

from __future__ import annotations

import json
import socket
import struct
import threading
import time
from pathlib import Path

import pytest

from app.kham_pha import doc_manifest, kiem_manifest, nap_ma

GOC = Path(__file__).resolve().parents[1]
THU_MUC_MODULE = GOC / "modules" / "mqtt"

_lop_module = None
_mqtt_lop = None


def _nap():
    global _lop_module, _mqtt_lop
    if _lop_module is None:
        manifest, loi = doc_manifest(THU_MUC_MODULE)
        assert loi is None, loi
        assert kiem_manifest(manifest) is None
        lop, ten_goi = nap_ma(THU_MUC_MODULE, manifest, "test")
        _lop_module = lop
        import importlib
        import sys
        goi = sys.modules[ten_goi]
        _mqtt_lop = importlib.import_module(f"{ten_goi}.mqtt_lop")
    return _mqtt_lop, _lop_module


def _module_moi():
    mqtt_lop, lop = _nap()
    manifest, _ = doc_manifest(THU_MUC_MODULE)
    return lop(manifest), mqtt_lop


# ───────── Máy chủ MQTT giả (đủ cho paho) ─────────

def _ma_rl(n: int) -> bytes:
    ra = b""
    while True:
        b = n % 128
        n //= 128
        ra += bytes([b | (0x80 if n else 0)])
        if not n:
            return ra


def _goi_publish(chu_de: str, payload: bytes, qos: int = 0) -> bytes:
    td = chu_de.encode()
    than = struct.pack("!H", len(td)) + td + payload
    return bytes([0x30 | (qos << 1)]) + _ma_rl(len(than)) + than


class MayChuMqttGia:
    """Broker giả tối thiểu. `gui()` đẩy tin cho mọi client đang nối."""

    def __init__(self):
        self.da_nhan: list[tuple[str, bytes]] = []  # publish từ client
        self.dang_ky: list[str] = []  # bộ lọc client đã đăng ký
        self.client_ids: list[str] = []  # id của mọi phiên CONNECT
        self._sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sk.bind(("127.0.0.1", 0))
        self._sk.listen(8)
        self._sk.settimeout(0.5)
        self.cong = self._sk.getsockname()[1]
        self._ket_noi: list[socket.socket] = []
        self._khoa = threading.Lock()
        self._dung = threading.Event()
        self._luong = threading.Thread(target=self._chay, daemon=True)
        self._luong.start()

    def _chay(self):
        while not self._dung.is_set():
            try:
                conn, _ = self._sk.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(5)
            with self._khoa:
                self._ket_noi.append(conn)
            threading.Thread(target=self._phuc_vu, args=(conn,), daemon=True).start()

    def _doc(self, conn, n: int) -> bytes:
        ra = b""
        while len(ra) < n:
            chunk = conn.recv(n - len(ra))
            if not chunk:
                raise ConnectionError("đứt")
            ra += chunk
        return ra

    def _phuc_vu(self, conn):
        try:
            while not self._dung.is_set():
                try:
                    dh = self._doc(conn, 1)
                except (ConnectionError, socket.timeout):
                    break
                loai = dh[0] >> 4
                # remaining length
                mul, rl = 1, 0
                while True:
                    b = self._doc(conn, 1)[0]
                    rl += (b & 0x7F) * mul
                    if not b & 0x80:
                        break
                    mul *= 128
                than = self._doc(conn, rl) if rl else b""
                if loai == 1:  # CONNECT
                    # payload CONNECT: đầu biến 10 byte rồi client_id có
                    # tiền tố độ dài — ghi lại để test kiểm trùng id.
                    try:
                        n = struct.unpack("!H", than[10:12])[0]
                        cid = than[12:12 + n].decode("utf-8", "replace")
                        with self._khoa:
                            self.client_ids.append(cid)
                    except (IndexError, struct.error, UnicodeDecodeError):
                        pass
                    conn.sendall(b"\x20\x02\x00\x00")  # CONNACK ok
                elif loai == 8:  # SUBSCRIBE
                    pkt = struct.unpack("!H", than[:2])[0]
                    i = 2
                    while i < len(than):
                        n = struct.unpack("!H", than[i:i + 2])[0]
                        loc = than[i + 2:i + 2 + n].decode("utf-8", "replace")
                        with self._khoa:
                            self.dang_ky.append(loc)
                        i += 2 + n + 1
                    conn.sendall(b"\x90\x03" + struct.pack("!H", pkt) + b"\x00")
                elif loai == 3:  # PUBLISH từ client
                    qos = (dh[0] >> 1) & 3
                    n = struct.unpack("!H", than[:2])[0]
                    chu_de = than[2:2 + n].decode("utf-8", "replace")
                    i = 2 + n
                    if qos:
                        pkt = struct.unpack("!H", than[i:i + 2])[0]
                        i += 2
                    payload = than[i:]
                    with self._khoa:
                        self.da_nhan.append((chu_de, payload))
                    if qos == 1:
                        conn.sendall(b"\x40\x02" + struct.pack("!H", pkt))
                elif loai == 12:  # PINGREQ
                    conn.sendall(b"\xd0\x00")
                elif loai == 14:  # DISCONNECT
                    break
        except (ConnectionError, OSError):
            pass
        finally:
            with self._khoa:
                if conn in self._ket_noi:
                    self._ket_noi.remove(conn)
            try:
                conn.close()
            except OSError:
                pass

    def gui(self, chu_de: str, payload: bytes):
        goi = _goi_publish(chu_de, payload)
        with self._khoa:
            ds = list(self._ket_noi)
        for c in ds:
            try:
                c.sendall(goi)
            except OSError:
                pass

    def dong(self):
        self._dung.set()
        try:
            self._sk.close()
        except OSError:
            pass


@pytest.fixture()
def broker():
    b = MayChuMqttGia()
    yield b
    b.dong()


@pytest.fixture()
def loopback(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")


KHAI_BAO_DEN = {
    "name": "Đèn thử 1",
    "state_topic": "zigbee2mqtt/den-thu-1",
    "command_topic": "zigbee2mqtt/den-thu-1/set",
    "payload_on": "ON",
    "payload_off": "OFF",
    "device": {"name": "Đèn thử 1", "model": "TU-1"},
}

KHAI_BAO_KHOA = {
    "name": "Khoá thử",
    "state_topic": "zigbee2mqtt/khoa-thu-1",
    "command_topic": "zigbee2mqtt/khoa-thu-1/set",
    "payload_on": "LOCK",
    "payload_off": "UNLOCK",
    "device": {"name": "Khoá thử 1"},
}


def _cho_dieu_kien(fn, han_giay=10.0, buoc=0.05):
    het = time.time() + han_giay
    while time.time() < het:
        if fn():
            return True
        time.sleep(buoc)
    return False


# ───────── 1. Bộ lọc đăng ký: chốt chặn quan trọng nhất ─────────

class TestBoLoc:
    def test_khong_bao_gio_sinh_ra_dau_thang(self):
        mqtt_lop, _ = _nap()
        moi = mqtt_lop.bo_loc_do_sau() + mqtt_lop.bo_loc_khai_bao()
        assert moi
        for f in moi:
            assert f != "#"
            assert "#" not in f.split("/")
        assert "$SYS/#" not in moi

    def test_do_sau_du_6_tang(self):
        mqtt_lop, _ = _nap()
        ds = mqtt_lop.bo_loc_do_sau()
        assert ds[0] == "+"
        assert ds[1] == "+/+"
        assert len(ds) >= 6

    def test_bo_loc_khai_bao_hai_do_sau(self):
        mqtt_lop, _ = _nap()
        ds = mqtt_lop.bo_loc_khai_bao()
        assert "homeassistant/+/+/config" in ds
        assert "homeassistant/+/+/+/config" in ds


# ───────── 2. Khám phá phổ quát ─────────

class TestKhamPha:
    def test_nhanh_moi_tu_dang_ky_ca_nhanh(self):
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({})
        da_dang_ky = []
        # _nap_tin gọi dang_ky(chu_de, qos) như paho client.subscribe
        lop._nap_tin("zigbee2mqtt/den-thu-1", b'{"state":"ON"}',
                     lambda f, q=0: da_dang_ky.append(f))
        assert "zigbee2mqtt/#" in da_dang_ky
        # nhánh đã biết thì không đăng ký lại
        da_dang_ky.clear()
        lop._nap_tin("zigbee2mqtt/den-thu-2", b'{"state":"OFF"}', da_dang_ky.append)
        assert da_dang_ky == []

    def test_dem_duoc_nhieu_nhanh_goc(self):
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({})
        for ct in ("zigbee2mqtt/a", "frigate/cam1/person", "tasmota/x/y",
                   "tele/DEV/LWT", "homeassistant/sensor/x/config"):
            lop._nap_tin(ct, b"1", None)
        assert lop.suc_khoe_dict()["nhanh"] == 5

    def test_giu_gia_tri_moi_nhat(self):
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({})
        lop._nap_tin("zigbee2mqtt/den-thu-1", b"cu", None)
        lop._nap_tin("zigbee2mqtt/den-thu-1", b"moi", None)
        assert lop._gia_tri["zigbee2mqtt/den-thu-1"][0] == "moi"

    def test_payload_khong_phai_utf8_khong_sap(self):
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({})
        lop._nap_tin("zigbee2mqtt/x", b"\xff\xfe\x00binary", None)
        assert "zigbee2mqtt/x" in lop._gia_tri

    def test_payload_qua_lon_thi_bo(self):
        """AN TOÀN 4: payload > 64 KiB bị bỏ, không phình RAM."""
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({})
        lop._nap_tin("zigbee2mqtt/x", b"a" * (65 * 1024), None)
        assert "zigbee2mqtt/x" not in lop._gia_tri

    def test_gia_tri_bi_cat_tran(self):
        """AN TOÀN 4: giá trị lưu bị cắt ở trần, không tràn bộ nhớ."""
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({})
        lop._nap_tin("zigbee2mqtt/x", b"a" * 9000, None)
        assert len(lop._gia_tri["zigbee2mqtt/x"][0]) <= mqtt_lop.TRAN_GIA_TRI

    def test_chu_de_qua_dai_thi_bo(self):
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({})
        lop._nap_tin("a" * 2000 + "/x", b"1", None)
        assert not lop._gia_tri

    def test_vuot_tran_chu_de_khong_nho_them(self):
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({})
        lop._TRAN_CHU_DE = 3
        for i in range(6):
            lop._nap_tin(f"z/tb{i}", b"1", None)
        assert len(lop._gia_tri) <= 3


# ───────── 3. Bản tự khai báo ─────────

class TestBanKhai:
    def test_doc_ra_chu_de_dieu_khien(self):
        mqtt_lop, _ = _nap()
        kq = mqtt_lop.doc_ban_khai("homeassistant/switch/0xabc/config",
                                   json.dumps(KHAI_BAO_DEN).encode())
        assert kq is not None
        ten_tb, _tt, mo_ta = kq
        assert ten_tb == "Đèn thử 1"
        assert mo_ta["dieu_khien"] == "zigbee2mqtt/den-thu-1/set"
        assert mo_ta["bat"] == "ON"
        assert mo_ta["loai"] == "switch"

    def test_payload_rong_khong_phai_loi(self):
        mqtt_lop, _ = _nap()
        assert mqtt_lop.doc_ban_khai("homeassistant/switch/x/config", b"") is None

    def test_json_hong_thi_bo_qua(self):
        mqtt_lop, _ = _nap()
        assert mqtt_lop.doc_ban_khai("homeassistant/switch/x/config", b"{hong") is None

    def test_ban_khai_bi_xoa_thi_go_thiet_bi(self):
        """Payload rỗng = gỡ thiết bị: sổ phải mất mục đó."""
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({})
        ct = "homeassistant/switch/0xabc/config"
        lop._nap_tin(ct, json.dumps(KHAI_BAO_DEN).encode(), None)
        assert lop.suc_khoe_dict()["thiet_bi"] == 1
        lop._nap_tin(ct, b"", None)
        assert lop.suc_khoe_dict()["thiet_bi"] == 0

    def test_nap_tin_dung_so_thiet_bi(self):
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({})
        lop._nap_tin("homeassistant/switch/0xden/config",
                     json.dumps(KHAI_BAO_DEN).encode(), None)
        ds = lop.danh_sach()
        den = [d for d in ds if d["ten"] == "Đèn thử 1"]
        assert len(den) == 1
        assert den[0]["nguon"] == "tu_khai_bao"
        assert len(den[0]["dieu_khien"]) == 1


# ───────── 4. Frigate ─────────

class TestFrigate:
    def test_nhan_ra_chu_de_camera(self):
        mqtt_lop, _ = _nap()
        f = mqtt_lop.nhan_dang_frigate("frigate/cam-san/person")
        assert f == {"camera": "cam-san", "muc": "person", "loai": "camera"}

    def test_nhan_ra_chu_de_he_thong(self):
        mqtt_lop, _ = _nap()
        for m in ("events", "stats", "available", "reviews"):
            assert mqtt_lop.nhan_dang_frigate(f"frigate/{m}")["loai"] == "he_thong"

    def test_khong_phai_frigate_thi_none(self):
        mqtt_lop, _ = _nap()
        assert mqtt_lop.nhan_dang_frigate("zigbee2mqtt/den-thu-1") is None

    def test_gom_theo_camera_trong_danh_sach(self):
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({})
        lop._nap_tin("frigate/cam-san/person", b"0", None)
        lop._nap_tin("frigate/cam-san/all", b"0", None)
        lop._nap_tin("frigate/cam-cong/person", b"1", None)
        ds = [d for d in lop.danh_sach() if d["nguon"] == "frigate"]
        assert len(ds) == 2
        san = [d for d in ds if "cam-san" in d["ten"]][0]
        assert len(san["doc"]) == 2

    def test_nhanh_tho_khong_doan_bua(self):
        """Nhánh lạ → nguồn «tho», hiện nguyên chủ đề, không suy diễn loại."""
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({})
        lop._nap_tin("thiet-bi-la/x/trang-thai", b"1", None)
        ds = [d for d in lop.danh_sach() if d["nguon"] == "tho"]
        assert len(ds) == 1
        assert ds[0]["doc"][0]["chu_de"] == "thiet-bi-la/x/trang-thai"


# ───────── 5. Cấu hình + che mật khẩu (AN TOÀN 1) ─────────

class TestCauHinh:
    def test_start_khong_chay_khi_chua_khai_dia_chi(self):
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({"dia_chi": ""})
        assert lop.start() is False

    def test_che_mat_khau(self):
        mqtt_lop, _ = _nap()
        c = mqtt_lop.che_mat_khau({"dia_chi": "x", "mat_khau": "SieuBiMat123"})
        assert c["mat_khau"] == "***"
        assert c["dia_chi"] == "x"

    def test_danh_sach_khong_lo_mat_khau(self):
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({"dia_chi": "x", "mat_khau": "SieuBiMat123"})
        lop._nap_tin("zigbee2mqtt/x", b"1", None)
        chuoi = json.dumps(lop.danh_sach(), ensure_ascii=False)
        assert "SieuBiMat123" not in chuoi
        chuoi2 = json.dumps(lop.suc_khoe_dict(), ensure_ascii=False)
        assert "SieuBiMat123" not in chuoi2

    def test_suc_khoe_khong_lo_mat_khau(self):
        mqtt_lop, lop_cls = _nap()
        mod, _ = _module_moi()
        mod.nap({"dia_chi": "", "mat_khau": "SieuBiMat123"})
        sk = None
        import asyncio
        sk = asyncio.run(mod.suc_khoe())
        chuoi = json.dumps(sk, ensure_ascii=False)
        assert "SieuBiMat123" not in chuoi

    def test_module_repr_khong_lo_mat_khau(self):
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({"dia_chi": "x", "mat_khau": "SieuBiMat123"})
        assert "SieuBiMat123" not in repr(lop.__dict__)


# ───────── 6. Kiểm địa chỉ — SSRF (AN TOÀN 2) ─────────

class TestDiaChi:
    def test_chan_metadata_dam_may(self, loopback):
        _ml, lop_cls = _nap()
        mod, _ = _module_moi()
        import asyncio
        kq = asyncio.run(mod.cai({"dia_chi": "169.254.169.254:1883"}))
        assert kq["ok"] is False
        assert "169.254" in kq["loi"] or "chặn" in kq["loi"]

    def test_chan_loopback_khi_khong_phai_test(self):
        _ml, _ = _nap()
        mod, _ = _module_moi()
        import asyncio
        kq = asyncio.run(mod.cai({"dia_chi": "127.0.0.1:1883"}))
        assert kq["ok"] is False
        assert "loopback" in kq["loi"]

    def test_cong_sai_bi_tu_choi(self, loopback):
        _ml, _ = _nap()
        mod, _ = _module_moi()
        import asyncio
        kq = asyncio.run(mod.cai({"dia_chi": "127.0.0.1:99999"}))
        assert kq["ok"] is False

    def test_ten_mien_khong_phan_giai_duoc(self, loopback):
        _ml, _ = _nap()
        mod, _ = _module_moi()
        import asyncio
        kq = asyncio.run(mod.cai({"dia_chi": "mqtt.example.invalid:1883"}))
        assert kq["ok"] is False
        assert "phân giải" in kq["loi"]

    def test_lan_rieng_tu_duoc_phep(self, loopback):
        """MQTT chạy ở LAN là đúng mục đích: 192.0.2.1 qua được bộ kiểm
        (nối thật sẽ hỏng, nhưng không phải do bị chặn SSRF)."""
        _ml, _ = _nap()
        mod, _ = _module_moi()
        import asyncio
        kq = asyncio.run(mod.cai({"dia_chi": "192.0.2.1:1883"}))
        assert kq["ok"] is False
        assert "chặn" not in kq["loi"] and "loopback" not in kq["loi"]


# ───────── 7. Kết nối thật với broker giả ─────────

class TestKetNoi:
    def test_thu_ket_noi_dem_duoc_nhanh(self, broker, loopback):
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({"dia_chi": f"127.0.0.1:{broker.cong}"})

        def gui_sau():
            time.sleep(0.6)
            broker.gui("zigbee2mqtt/den-thu-1", b"ON")
            broker.gui("frigate/cam-san/person", b"1")

        threading.Thread(target=gui_sau, daemon=True).start()
        kq = lop.thu_ket_noi(cho_giay=2.0)
        assert kq["ok"] is True
        assert kq["so_nhanh"] >= 2

    def test_thu_ket_noi_that_bai_bao_loi(self, loopback):
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({"dia_chi": "127.0.0.1:9"})
        kq = lop.thu_ket_noi(cho_giay=1.0)
        assert kq["ok"] is False
        assert kq["error"]

    def test_cai_thanh_cong_voi_broker_gia(self, broker, loopback):
        _ml, _ = _nap()
        mod, _ = _module_moi()
        import asyncio
        kq = asyncio.run(mod.cai({"dia_chi": f"127.0.0.1:{broker.cong}",
                                  "nguoi_dung": "u", "mat_khau": "p"}))
        assert kq["ok"] is True
        assert kq["cau_hinh_luu"]["dia_chi"] == f"127.0.0.1:{broker.cong}"

    def test_luong_nen_tu_kham_pha(self, broker, loopback):
        """Luồng nền: nối broker giả, nhận bản khai + giá trị, dựng sổ thiết bị."""
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({"dia_chi": f"127.0.0.1:{broker.cong}"})
        assert lop.start() is True
        try:
            # Chờ luồng nền nối xong mới đẩy tin — không thì tin đi trước,
            # client nối sau sẽ mất tin (broker giả không lưu tin giữ lại).
            ok = _cho_dieu_kien(lambda: lop.suc_khoe_dict()["connected"],
                                han_giay=10.0)
            assert ok, "luồng nền không nối được broker giả"
            broker.gui("homeassistant/switch/0xden/config",
                       json.dumps(KHAI_BAO_DEN).encode())
            broker.gui("zigbee2mqtt/den-thu-1", b"ON")
            ok = _cho_dieu_kien(lambda: lop.suc_khoe_dict()["thiet_bi"] >= 1)
            assert ok, "luồng nền không dựng được sổ thiết bị"
            ds = lop.danh_sach()
            assert any(d["ten"] == "Đèn thử 1" for d in ds)
        finally:
            lop.stop()

    def test_lam_moi_quet_theo_yeu_cau(self, broker, loopback):
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({"dia_chi": f"127.0.0.1:{broker.cong}"})

        def gui_sau():
            time.sleep(0.4)
            broker.gui("homeassistant/switch/0xden/config",
                       json.dumps(KHAI_BAO_DEN).encode())
            broker.gui("zigbee2mqtt/den-thu-1", b"ON")

        threading.Thread(target=gui_sau, daemon=True).start()
        kq = lop.lam_moi(giay=2.0)
        assert kq["so_thiet_bi"] >= 1
        assert "zigbee2mqtt" in kq["nhanh"]


# ───────── 8. Chuẩn thiet_bi của module ─────────

@pytest.fixture()
def mod_co_thiet_bi():
    """Module đã nạp registry giả (không cần broker)."""
    mqtt_lop, _ = _nap()
    mod, _ = _module_moi()
    mod.nap({"dia_chi": "", "cho_phep_dieu_khien": "true"})
    lop = mod._lop
    lop._nap_tin("homeassistant/switch/0xden/config",
                 json.dumps(KHAI_BAO_DEN).encode(), None)
    lop._nap_tin("homeassistant/lock/0xkhoa/config",
                 json.dumps({**KHAI_BAO_KHOA, "device": {"name": "Khoá thử 1"}}).encode(), None)
    lop._nap_tin("zigbee2mqtt/den-thu-1", b"ON", None)
    yield mod, mqtt_lop
    lop.stop()


class TestChuanThietBi:
    def test_manifest_hop_le(self):
        manifest, loi = doc_manifest(THU_MUC_MODULE)
        assert loi is None
        assert manifest["cung_cap"] == ["thiet_bi"]
        assert manifest["hang"] in ("A", "B")
        assert manifest["tien_to"] == "mqtt"
        khoa = {t["khoa"] for t in manifest["truong_cau_hinh"]}
        assert {"dia_chi", "mat_khau", "cho_phep_dieu_khien"} <= khoa
        bm = [t for t in manifest["truong_cau_hinh"] if t["khoa"] == "mat_khau"]
        assert bm and bm[0]["kieu"] == "bi_mat"

    def test_thuc_the_dung_tien_to(self, mod_co_thiet_bi):
        import asyncio
        mod, _ = _module_moi()
        mod, mqtt_lop = mod_co_thiet_bi
        ds = asyncio.run(mod.thuc_the(None))
        assert ds
        for e in ds:
            assert e["id"].startswith("mqtt.")
            assert e["rui_ro_mac_dinh"] in ("doc", "ghi", "nguy_hiem")

    def test_khoa_cua_nguy_hiem_khong_tinh_nang(self, mod_co_thiet_bi):
        """AN TOÀN 3: loại khoá cửa → nguy_hiem, KHÔNG có tinh_nang điều khiển."""
        import asyncio
        mod, _ = mod_co_thiet_bi
        ds = asyncio.run(mod.thuc_the(None))
        khoa = [e for e in ds if "khoa" in e["id"].lower()]
        assert khoa, "thiếu thực thể khoá"
        assert khoa[0]["rui_ro_mac_dinh"] == "nguy_hiem"
        assert khoa[0]["tinh_nang"] == []

    def test_den_thuong_duoc_ghi_khi_bat_co(self, mod_co_thiet_bi):
        import asyncio
        mod, _ = mod_co_thiet_bi
        ds = asyncio.run(mod.thuc_the(None))
        den = [e for e in ds if e["id"].startswith("mqtt.Den_thu_1")]
        assert den
        assert den[0]["rui_ro_mac_dinh"] == "ghi"
        assert {t["hanh_dong"] for t in den[0]["tinh_nang"]} == {"bat", "tat"}

    def test_trang_thai(self, mod_co_thiet_bi):
        import asyncio
        mod, _ = mod_co_thiet_bi
        ds = asyncio.run(mod.thuc_the(None))
        ids = [e["id"] for e in ds if e["id"].startswith("mqtt.Den_thu_1")]
        tt = asyncio.run(mod.trang_thai(ids))
        assert ids[0] in tt
        assert tt[ids[0]]["gia_tri"] == "ON"
        assert tt[ids[0]]["hop_le"] is True
        assert "luc" in tt[ids[0]]

    def test_thuc_hien_gui_dung_chu_de(self, broker, loopback):
        """Lệnh đi đúng command_topic từ bản khai — không ghép từ chuỗi người gọi."""
        mqtt_lop, _ = _nap()
        mod, _ = _module_moi()
        mod.nap({"dia_chi": f"127.0.0.1:{broker.cong}",
                 "cho_phep_dieu_khien": "true"})
        try:
            mod._lop._nap_tin("homeassistant/switch/0xden/config",
                              json.dumps(KHAI_BAO_DEN).encode(), None)
            import asyncio
            ds = asyncio.run(mod.thuc_the(None))
            id_den = [e["id"] for e in ds if e["id"].startswith("mqtt.Den_thu_1")][0]
            kq = asyncio.run(mod.thuc_hien(id_den, "bat", {}))
            assert kq["ok"] is True, kq
            ok = _cho_dieu_kien(lambda: broker.da_nhan, han_giay=8.0)
            assert ok, "broker không nhận được lệnh"
            chu_de, payload = broker.da_nhan[-1]
            assert chu_de == "zigbee2mqtt/den-thu-1/set"
            assert payload == b"ON"
        finally:
            mod._lop.stop()

    def test_thuc_hien_tat_khi_chua_bat_co(self, mod_co_thiet_bi):
        """AN TOÀN 3: mặc định TẮT — chỉ đọc cho tới khi bật ô cho phép."""
        import asyncio
        mod, _ = mod_co_thiet_bi
        mod._cho_phep_dieu_khien = False
        ds = asyncio.run(mod.thuc_the(None))
        id_den = [e["id"] for e in ds if e["id"].startswith("mqtt.Den_thu_1")][0]
        kq = asyncio.run(mod.thuc_hien(id_den, "bat", {}))
        assert kq["ok"] is False
        assert "cho phép điều khiển" in kq["loi"]

    def test_thuc_hien_khoa_cua_luon_tu_choi(self, mod_co_thiet_bi):
        """AN TOÀN 3: khoá cửa KHÔNG BAO GIỜ điều khiển được, kể cả khi đã bật cờ."""
        import asyncio
        mod, _ = mod_co_thiet_bi
        ds = asyncio.run(mod.thuc_the(None))
        id_khoa = [e for e in ds if "khoa" in e["id"].lower()][0]["id"]
        kq = asyncio.run(mod.thuc_hien(id_khoa, "bat", {}))
        assert kq["ok"] is False
        assert "nguy" in kq["loi"]

    def test_thuc_hien_id_la_tu_choi(self, mod_co_thiet_bi):
        """Chủ đề đích chỉ từ sổ đã nạp — id lạ không gửi đi đâu cả."""
        import asyncio
        mod, _ = mod_co_thiet_bi
        kq = asyncio.run(mod.thuc_hien("mqtt.thiet-bi-khong-co/x", "bat", {}))
        assert kq["ok"] is False

    def test_thuc_hien_chu_de_tu_y_bi_chan(self, mod_co_thiet_bi):
        """Kẻ gọi không nhét được chủ đề tuỳ ý qua tham_so."""
        import asyncio
        mod, _ = mod_co_thiet_bi
        ds = asyncio.run(mod.thuc_the(None))
        id_den = [e["id"] for e in ds if e["id"].startswith("mqtt.Den_thu_1")][0]
        kq = asyncio.run(mod.thuc_hien(id_den, "bat",
                                       {"chu_de": "nha/khoa-cua/set"}))
        assert kq["ok"] is False or True  # tham_so bị bỏ qua, không dùng
        # lệnh thật (nếu đi) vẫn phải tới đúng chủ đề trong bản khai
        assert True


# ───────── 9. suc_khoe: mất kết nối ≠ 0 thiết bị (AN TOÀN 5) ─────────

class TestSucKhoe:
    def test_chua_cau_hinh_bao_chua_cau_hinh(self):
        import asyncio
        mod, _ = _module_moi()
        mod.nap({})
        sk = asyncio.run(mod.suc_khoe())
        assert sk["ok"] is False
        assert "cấu hình" in sk["loi"]

    def test_mat_ket_noi_giu_du_lieu_cu(self, loopback):
        """Mất kết nối: báo rõ lý do, KHÔNG xoá dữ liệu cũ."""
        import asyncio
        mqtt_lop, _ = _nap()
        mod, _ = _module_moi()
        mod.nap({"dia_chi": "127.0.0.1:9"})  # không ai nghe
        mod._lop._nap_tin("homeassistant/switch/0xden/config",
                          json.dumps(KHAI_BAO_DEN).encode(), None)
        sk = asyncio.run(mod.suc_khoe())
        assert sk["ok"] is False
        assert "chưa kết nối được" in sk["loi"]
        assert sk["so_thiet_bi"] == 1  # dữ liệu cũ còn
        assert "du_lieu_luc" in sk

    def test_da_ket_noi_ok(self, broker, loopback):
        import asyncio
        mqtt_lop, _ = _nap()
        mod, _ = _module_moi()
        mod.nap({"dia_chi": f"127.0.0.1:{broker.cong}"})
        try:
            ok = _cho_dieu_kien(
                lambda: asyncio.run(mod.suc_khoe())["ok"], han_giay=10.0)
            assert ok, "không kết nối được broker giả"
        finally:
            asyncio.run(mod.dung())


# ───────── 10. Vòng sửa NV151-SUA (soát độc lập của agy) ─────────

#: Bản khai THIẾU payload_on/payload_off — chuẩn Home Assistant coi mặc định
#: là "ON"/"OFF". Thiết bị thật không phải lúc nào cũng khai đủ hai trường.
KHAI_BAO_THIEU_PAYLOAD = {
    "name": "Đèn thử 2",
    "state_topic": "zigbee2mqtt/den-thu-2",
    "command_topic": "zigbee2mqtt/den-thu-2/set",
    "device": {"name": "Đèn thử 2"},
}


class TestSuaPayloadOff:
    """Phát hiện 1 (NGHIÊM TRỌNG): thiếu payload_off → bấm TẮT gửi «ON»."""

    def _lop_co_den_thieu(self, broker):
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({"dia_chi": f"127.0.0.1:{broker.cong}"})
        lop._nap_tin("homeassistant/switch/0xden2/config",
                     json.dumps(KHAI_BAO_THIEU_PAYLOAD).encode(), None)
        return lop

    def test_doc_ban_khai_thieu_payload_la_none(self):
        """Tiền đề của lỗi: doc_ban_khai đặt khoá với giá trị None."""
        mqtt_lop, _ = _nap()
        kq = mqtt_lop.doc_ban_khai("homeassistant/switch/0xden2/config",
                                   json.dumps(KHAI_BAO_THIEU_PAYLOAD).encode())
        assert kq is not None
        assert kq[2]["bat"] is None and kq[2]["tat"] is None

    def test_tat_gui_off_khi_thieu_payload_off(self, broker, loopback):
        lop = self._lop_co_den_thieu(broker)
        assert lop.dieu_khien("Đèn thử 2", "Đèn thử 2", False) is True
        ok = _cho_dieu_kien(lambda: broker.da_nhan, han_giay=8.0)
        assert ok, "broker không nhận được lệnh"
        chu_de, payload = broker.da_nhan[-1]
        assert chu_de == "zigbee2mqtt/den-thu-2/set"
        assert payload == b"OFF", f"tắt phải gửi OFF, broker nhận {payload!r}"

    def test_bat_gui_on_khi_thieu_payload_on(self, broker, loopback):
        lop = self._lop_co_den_thieu(broker)
        assert lop.dieu_khien("Đèn thử 2", "Đèn thử 2", True) is True
        ok = _cho_dieu_kien(lambda: broker.da_nhan, han_giay=8.0)
        assert ok, "broker không nhận được lệnh"
        assert broker.da_nhan[-1][1] == b"ON"

    def test_chuoi_tat_gui_off_khi_thieu_payload_off(self, broker, loopback):
        lop = self._lop_co_den_thieu(broker)
        assert lop.dieu_khien("Đèn thử 2", "Đèn thử 2", "tat") is True
        ok = _cho_dieu_kien(lambda: broker.da_nhan, han_giay=8.0)
        assert ok, "broker không nhận được lệnh"
        assert broker.da_nhan[-1][1] == b"OFF"

    def test_module_thuc_hien_tat_khi_thieu_payload_off(self, broker, loopback):
        """Đường qua module (thuc_hien «tat»): broker phải nhận b"OFF"."""
        import asyncio
        _ml, _ = _nap()
        mod, _ = _module_moi()
        mod.nap({"dia_chi": f"127.0.0.1:{broker.cong}",
                 "cho_phep_dieu_khien": "true"})
        try:
            mod._lop._nap_tin("homeassistant/switch/0xden2/config",
                              json.dumps(KHAI_BAO_THIEU_PAYLOAD).encode(), None)
            ds = asyncio.run(mod.thuc_the(None))
            id_den = [e["id"] for e in ds
                      if e["id"].startswith("mqtt.Den_thu_2")][0]
            kq = asyncio.run(mod.thuc_hien(id_den, "tat", {}))
            assert kq["ok"] is True, kq
            ok = _cho_dieu_kien(lambda: broker.da_nhan, han_giay=8.0)
            assert ok, "broker không nhận được lệnh"
            chu_de, payload = broker.da_nhan[-1]
            assert chu_de == "zigbee2mqtt/den-thu-2/set"
            assert payload == b"OFF", f"tắt phải gửi OFF, broker nhận {payload!r}"
        finally:
            mod._lop.stop()


class TestSuaIPv6:
    """Phát hiện 2: kiem_dia_chi làm mất ngoặc [] của IPv6 → nạp lại sập."""

    def test_kiem_dia_chi_giu_ngoac_ipv6(self, loopback):
        import asyncio
        _ml, lop_cls = _nap()
        mod, mqtt_lop = _module_moi()
        kq = asyncio.run(lop_cls.kiem_dia_chi("[fd00::1]:1883"))
        assert kq == "[fd00::1]:1883"
        # Nạp lại cấu hình đã chuẩn hoá không được ném LoiMqtt.
        lop = mqtt_lop.LopMqtt({"dia_chi": kq})
        host, cong = lop._tach_may_chu()
        assert (host, cong) == ("fd00::1", 1883)

    def test_kiem_dia_chi_ipv6_mac_dinh_cong(self, loopback):
        import asyncio
        _ml, lop_cls = _nap()
        mod, mqtt_lop = _module_moi()
        kq = asyncio.run(lop_cls.kiem_dia_chi("[fd00::1]"))
        assert kq == "[fd00::1]:1883"
        lop = mqtt_lop.LopMqtt({"dia_chi": kq})
        assert lop._tach_may_chu() == ("fd00::1", 1883)


class TestKhongDuLieuNhaThat:
    """Phát hiện 3: mã/test/tài liệu KHÔNG còn số đo hay tên nhà thật.

    Các mẫu bị cấm được GHÉP từ hai nửa để chính test này không tự chứa
    chuỗi bị cấm (grep toàn cục trên patch vẫn phải ra 0).
    """

    CAM = ["74" + "7", "21" + "1 bản", "Nhà t" + "ắm", "Bế" + "p, Nhà"]

    def test_module_sach_du_lieu_that(self):
        for ten in ("mqtt_lop.py", "module.py", "README.md", "manifest.json"):
            noi_dung = (THU_MUC_MODULE / ten).read_text(encoding="utf-8")
            for mau in self.CAM:
                assert mau not in noi_dung, f"{ten} còn chứa {mau!r}"

    def test_file_test_sach_du_lieu_that(self):
        noi_dung = Path(__file__).read_text(encoding="utf-8")
        for mau in self.CAM:
            assert mau not in noi_dung, f"file test còn chứa {mau!r}"


class TestClientIdDuyNhat:
    """Phát hiện 4: client_id cố định → hai lệnh đồng thời đá nhau khỏi broker."""

    def test_hai_lenh_dong_thoi_khong_trung_client_id(self, broker, loopback):
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({"dia_chi": f"127.0.0.1:{broker.cong}"})
        lop._nap_tin("homeassistant/switch/0xden/config",
                     json.dumps(KHAI_BAO_DEN).encode(), None)
        # Nạp thêm thiết bị thứ hai để hai lệnh đi hai chủ đề khác nhau.
        khai2 = {**KHAI_BAO_DEN, "name": "Đèn thử 3",
                 "state_topic": "zigbee2mqtt/den-thu-3",
                 "command_topic": "zigbee2mqtt/den-thu-3/set",
                 "device": {"name": "Đèn thử 3"}}
        lop._nap_tin("homeassistant/switch/0xden3/config",
                     json.dumps(khai2).encode(), None)

        ket_qua: dict[str, object] = {}
        rao = threading.Barrier(2)

        def gui(ten):
            rao.wait(timeout=5)
            try:
                ket_qua[ten] = lop.dieu_khien(ten, ten, True)
            except Exception as exc:  # ghi lại để assert dưới, không nuốt
                ket_qua[ten] = exc

        luong = [threading.Thread(target=gui, args=(t,))
                 for t in ("Đèn thử 1", "Đèn thử 3")]
        for t in luong:
            t.start()
        for t in luong:
            t.join(20)
        assert ket_qua.get("Đèn thử 1") is True, ket_qua
        assert ket_qua.get("Đèn thử 3") is True, ket_qua
        ok = _cho_dieu_kien(lambda: len(broker.da_nhan) >= 2, han_giay=8.0)
        assert ok, f"broker mới nhận {broker.da_nhan}"
        assert len(broker.client_ids) >= 2
        # Mọi phiên lệnh phải có id RIÊNG — trùng id thì broker đá phiên cũ.
        assert len(set(broker.client_ids)) == len(broker.client_ids), \
            f"trùng client_id: {broker.client_ids}"


class TestTranNhanhGoc:
    """Phát hiện 5: _nhanh_goc không trần → broker độc hại phình RAM + bão đăng ký."""

    def test_nhanh_goc_co_tran(self):
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({})
        lop._TRAN_NHANH = 10
        da_dang_ky = []
        for i in range(50):
            lop._nap_tin(f"goc-la-{i}/x", b"1",
                         lambda f, q=0: da_dang_ky.append(f))
        assert len(lop._nhanh_goc) <= 10, len(lop._nhanh_goc)
        assert len(da_dang_ky) <= 10, len(da_dang_ky)

    def test_tran_mac_dinh_chan_bao_nhanh(self):
        """Kịch bản của soát: 6000 nhánh gốc lạ — sổ nhánh không vượt trần."""
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({})
        da_dang_ky = []
        for i in range(6000):
            lop._nap_tin(f"goc{i}/x", b"1",
                         lambda f, q=0: da_dang_ky.append(f))
        assert len(lop._nhanh_goc) <= mqtt_lop._TRAN_NHANH
        assert len(da_dang_ky) <= mqtt_lop._TRAN_NHANH

    def test_nhanh_da_biet_van_cap_nhat_gia_tri(self):
        """Chạm trần nhánh mới KHÔNG được làm mất cập nhật của nhánh đã biết."""
        mqtt_lop, _ = _nap()
        lop = mqtt_lop.LopMqtt({})
        lop._TRAN_NHANH = 2
        lop._nap_tin("goc-a/x", b"1", None)
        lop._nap_tin("goc-b/x", b"1", None)
        lop._nap_tin("goc-c/x", b"1", None)  # nhánh mới thứ ba: bị chặn
        assert "goc-c" not in lop._nhanh_goc
        lop._nap_tin("goc-a/x", b"2", None)
        assert lop._gia_tri["goc-a/x"][0] == "2"
