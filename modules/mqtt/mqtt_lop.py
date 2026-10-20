"""Lớp MQTT cho module web2api `mqtt` — khám phá thiết bị qua MQTT, KHÔNG cần Home Assistant.

PORT từ c2a (kho của chủ máy): services/mqtt_nha.py @ commit d879b03 (19/10/2026).
Chép cấu trúc đang chạy thật, chỉ đổi:
  (a) bỏ mọi phụ thuộc nội bộ của c2a (services.config, services.lich_su_nha,
      services.mat_ngoai, services.canh_camera_nha, services.thiet_bi_bo,
      services.agent.vi_text, utils.log) — không tồn tại trong web2api;
  (b) trạng thái chuyển từ biến toàn cục sang thể hiện của lớp LopMqtt để khớp
      vòng đời module web2api (nap/dung) và để test cô lập được;
  (c) cấu hình do module truyền vào (không đọc config của c2a);
  (d) bỏ số đo gắn với nhà thật trong chú thích (211 bản ghi, 747 tin/40 giây,
      tên phòng/thiết bị thật); giữ nguyên chú thích bài học EMQX về `#`;
  (e) trần an toàn mới: payload > 64 KiB bị bỏ, giá trị lưu cắt ở 4096 ký tự,
      chủ đề dài > 1024 ký tự bị bỏ (dữ liệu từ máy chủ MQTT là KHÔNG TIN).

KHÔNG chép từ c2a (ngoài phạm vi NV151, thuộc module khác sau này):
  dem_nguoi (đếm người Frigate), chu_de_con_khai, ghi lịch sử (lich_su_nha),
  nhận mặt (mat_ngoai), canh camera, thiết bị bỏ qua (thiet_bi_bo).
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
import unicodedata
from typing import Any, Callable, Optional

log = logging.getLogger("web2api.mqtt")

#: Độ sâu quét ``+``. Sáu tầng phủ hết chủ đề thực tế đã gặp; sâu hơn chỉ tốn
#: đăng ký mà không thêm nhánh nào.
_SAU_TOI_DA = 6
#: Giữ tối đa ngần này chủ đề trong RAM. Không chặn thì tiến trình phình theo
#: thời gian chạy.
_TRAN_CHU_DE = 5000
#: Giá trị một chủ đề giữ tối đa ngần này ký tự (cắt, không vỡ).
TRAN_GIA_TRI = 4096
#: Payload lớn hơn ngần này byte thì BỎ HẲN (không tin máy chủ MQTT).
_TRAN_PAYLOAD = 64 * 1024
#: Chủ đề dài hơn ngần này ký tự thì BỎ (chống tràn bộ nhớ qua chủ đề).
_TRAN_CHU_DE_DAI = 1024

#: Tầng cuối của chủ đề là LỆNH GỬI ĐI, không phải quan sát về nhà.
#: (Zigbee2MQTT nhận lệnh ở `<thiết bị>/set`; giữ để lọc trùng nếu sau này
#: module ghi lịch sử — hiện tại web2api không ghi lịch sử MQTT.)
_CHU_DE_LENH = frozenset({"set", "get"})

#: Quá bao lâu không nhận tin thì coi số liệu là CŨ, không dám khẳng định nữa.
_HAN_TUOI = 180.0
#: Rớt quá bao lâu mà paho chưa tự nối lại thì tự dựng phiên mới.
_HAN_ROI = 60.0
#: Đang "nối" nhưng câm quá lâu cũng là chết — máy chủ thật phát liên tục.
_HAN_IM = 600.0

_BACKOFF_START = 5.0
_BACKOFF_CAP = 120.0


class LoiMqtt(RuntimeError):
    """Hỏng ở phía MQTT — thông điệp đã sẵn sàng đọc cho người dùng."""


class _BiMat:
    """Vỏ bọc mật khẩu: ``repr`` không bao giờ lộ giá trị thật.

    AN TOÀN 1: mật khẩu sống trong thể hiện LopMqtt để luồng nền dùng, nhưng
    mọi đường ``repr(log/dict)`` tình cờ (kể cả ``repr(lop.__dict__)``) chỉ
    thấy ``***`` chứ không thấy chuỗi bí mật.
    """

    __slots__ = ("gia_tri",)

    def __init__(self, gia_tri: str):
        self.gia_tri = gia_tri or ""

    def __bool__(self):
        return bool(self.gia_tri)

    def __repr__(self):
        return "***"


def _client_id(tien_to: str) -> str:
    """client_id cho phiên TẠM THỜI (gửi lệnh/quét/thử nối).

    Hậu tố ngẫu nhiên để hai phiên đồng thời không đá nhau: broker bắt buộc
    ngắt phiên cũ khi một phiên mới dùng trùng client_id. Phiên nền dùng id
    cố định vì chỉ có đúng một gương chạy.
    """
    return f"{tien_to}-{secrets.token_hex(4)}"


def _can_paho():
    """Import lười paho: lõi từ chối nạp module khi thiếu (yeu_cau_goi),
    nhưng gọi trực tiếp vẫn báo rõ thay vì ImportError thô."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        raise LoiMqtt("Chưa cài thư viện paho-mqtt — cài trong container: "
                      "pip install 'paho-mqtt==2.1.0'.")
    return mqtt


def che_mat_khau(c: dict[str, Any]) -> dict[str, Any]:
    """Bỏ mật khẩu khỏi một bản ghi cấu hình trước khi cho ai đó nhìn thấy."""
    m = dict(c)
    if m.get("mat_khau"):
        m["mat_khau"] = "***"
    # tương thích bản khai kiểu c2a nếu ai đó chép cấu hình cũ sang
    if m.get("password"):
        m["password"] = "***"
    return m


def _gap_dau(s: str) -> str:
    """Bỏ dấu tiếng Việt để khớp tên (thay cho services.agent.vi_text.fold của c2a)."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return s.replace("đ", "d").replace("Đ", "D").lower()


def _tu(s: str) -> set[str]:
    return {t for t in re.split(r"[^0-9a-z]+", _gap_dau(s)) if t}


# ── Bộ lọc đăng ký ───────────────────────────────────────────────────────────

def bo_loc_do_sau() -> list[str]:
    """Các bộ lọc ``+`` dùng để DÒ nhánh gốc.

    Tách thành hàm riêng để test khẳng định được: không bao giờ sinh ra ``#``.
    """
    return ["/".join(["+"] * n) for n in range(1, _SAU_TOI_DA + 1)]


def bo_loc_khai_bao() -> list[str]:
    """Bộ lọc đọc bản tự khai báo. Hai độ sâu vì có loại lồng thêm một tầng."""
    return ["homeassistant/+/+/config", "homeassistant/+/+/+/config"]


# ── Đọc bản tự khai báo ──────────────────────────────────────────────────────

def doc_ban_khai(chu_de: str, payload: bytes) -> tuple[str, str, dict[str, Any]] | None:
    """Đọc một bản tự khai báo thành ``(tên thiết bị, tên thực thể, mô tả)``.

    Trả ``None`` khi không phải bản khai hợp lệ — payload rỗng là chuyện BÌNH
    THƯỜNG, đó là cách gỡ một thiết bị khỏi hệ thống chứ không phải lỗi.
    """
    if not payload:
        return None
    try:
        d = json.loads(payload)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None

    dev = d.get("device")
    ten_tb = ""
    if isinstance(dev, dict):
        ten_tb = str(dev.get("name") or "").strip()
    if not ten_tb:
        # Không khai thiết bị cha thì lấy tên thực thể làm tên thiết bị — vẫn
        # điều khiển được, chỉ là mỗi thực thể đứng riêng một dòng.
        ten_tb = str(d.get("name") or "").strip()
    if not ten_tb:
        return None

    phan = chu_de.split("/")
    loai = phan[1] if len(phan) > 1 else "?"
    ten_tt = str(d.get("name") or d.get("object_id") or phan[-2] if len(phan) > 1 else "?")

    mo_ta = {
        "loai": loai,
        "doc": d.get("state_topic") or "",
        "dieu_khien": d.get("command_topic") or "",
        "bat": d.get("payload_on"),
        "tat": d.get("payload_off"),
        "mau": d.get("value_template") or "",
        "don_vi": d.get("unit_of_measurement") or "",
    }
    return ten_tb, str(ten_tt), mo_ta


def nhan_dang_frigate(chu_de: str) -> dict[str, Any] | None:
    """Nhận ra chủ đề của Frigate. Frigate KHÔNG dùng chuẩn tự khai báo.

    Frigate đăng theo lối riêng: ``frigate/<camera>/<vật thể>`` là số đếm,
    ``frigate/events`` là sự kiện, ``frigate/stats`` là tình trạng máy.
    """
    phan = chu_de.split("/")
    if not phan or phan[0] != "frigate":
        return None
    if len(phan) == 2 and phan[1] in ("events", "stats", "available", "reviews"):
        return {"camera": "", "muc": phan[1], "loai": "he_thong"}
    if len(phan) >= 3:
        return {"camera": phan[1], "muc": "/".join(phan[2:]), "loai": "camera"}
    return None


class LopMqtt:
    """Một phiên gương MQTT: luồng nền + sổ thiết bị đã khám phá.

    ``cau_hinh``: {"dia_chi": "may[:cong]", "nguoi_dung": ..., "mat_khau": ...}.
    Mật khẩu chỉ sống trong thể hiện này, không bao giờ vào log/phản hồi.
    """

    def __init__(self, cau_hinh: dict[str, Any] | None = None):
        c = cau_hinh or {}
        self.dia_chi = str(c.get("dia_chi") or "").strip()
        self.nguoi_dung = str(c.get("nguoi_dung") or "").strip()
        # Mật khẩu bọc trong _BiMat: repr(lop.__dict__) không lộ bí mật.
        self._mat_khau = _BiMat(str(c.get("mat_khau") or ""))
        # Trần số chủ đề để trên thể hiện để test chỉnh được.
        self._TRAN_CHU_DE = _TRAN_CHU_DE

        self._khoa = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._gia_tri: dict[str, tuple[str, float]] = {}
        self._so_thiet_bi: dict[str, dict[str, dict[str, Any]]] = {}
        self._khai_theo_chu_de: dict[str, tuple[str, str]] = {}
        self._nhanh_goc: set[str] = set()
        self._stats: dict[str, Any] = {
            "connected": False, "tin": 0, "thiet_bi": 0, "nhanh": 0,
            "last_error": "", "reconnects": 0, "last_tin_ts": 0.0,
        }

    # ── cấu hình ─────────────────────────────────────────────────────────

    def cau_hinh_che(self) -> dict[str, Any]:
        """Cấu hình cho web/model xem: mật khẩu đã che."""
        return che_mat_khau({"dia_chi": self.dia_chi,
                             "nguoi_dung": self.nguoi_dung,
                             "mat_khau": "***" if self._mat_khau else ""})

    def _tach_may_chu(self) -> tuple[str, int]:
        """dia_chi dạng «may[:cong]» → (may, cong). Ném LoiMqtt khi sai."""
        dc = self.dia_chi
        if not dc:
            raise LoiMqtt("Chưa khai địa chỉ máy chủ MQTT.")
        if dc.startswith("["):  # IPv6 [::1]:1883
            ket = dc.find("]")
            if ket < 0:
                raise LoiMqtt("Địa chỉ IPv6 phải dạng [::1]:1883.")
            host, con_lai = dc[1:ket], dc[ket + 1:]
            cong = int(con_lai[1:]) if con_lai.startswith(":") else 1883
        elif dc.count(":") > 1:
            raise LoiMqtt("Địa chỉ IPv6 phải đặt trong [ ].")
        else:
            if ":" in dc:
                host, cong_s = dc.rsplit(":", 1)
                try:
                    cong = int(cong_s)
                except ValueError:
                    raise LoiMqtt("Cổng phải là một con số.")
            else:
                host, cong = dc, 1883
        host = host.strip()
        if not host:
            raise LoiMqtt("Chưa nhập máy chủ MQTT.")
        if not (1 <= cong <= 65535):
            raise LoiMqtt("Cổng phải nằm trong khoảng 1–65535.")
        return host, cong

    # ── luồng nền ────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Khởi động gương (idempotent). Trả True nếu đang/đã chạy."""
        if not self.dia_chi:
            return False
        with self._khoa:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._stop.clear()
            self._thread = threading.Thread(target=self._chay_mai, daemon=True,
                                            name="mqtt-nen")
            self._thread.start()
            log.info("mqtt: đã khởi động luồng nền")
            return True

    def stop(self) -> None:
        self._stop.set()

    def _tao_client(self, cid: str):
        mqtt = _can_paho()
        host, port = self._tach_may_chu()
        cl = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
        if self.nguoi_dung:
            cl.username_pw_set(self.nguoi_dung, self._mat_khau.gia_tri)
        return cl, host, port

    def _chay_mot_phien(self) -> None:
        """Một phiên kết nối. Ném ngoại lệ khi đứt để vòng ngoài nối lại."""
        cl, host, port = self._tao_client("web2api-mqtt-nen")

        def on_connect(client, userdata, flags, rc, props=None):
            # Đăng ký DÒ bằng '+' nhiều độ sâu. KHÔNG dùng '#' — EMQX (và mọi
            # máy chủ đặt `no_match = deny`) TỪ CHỐI `#` và `$SYS/#`, nhưng từ
            # chối IM LẶNG: kết nối vẫn thành công, chỉ là không tin nào về.
            # Ai "gọn lại" thành `#` sẽ mất sạch dữ liệu và tưởng máy chủ hỏng.
            # Cũng đừng rút bớt độ sâu: chỉ `+/+` thì mất phần lớn thiết bị,
            # vì chủ đề dài ngắn khác nhau.
            client.subscribe([(f, 0) for f in bo_loc_do_sau()])
            client.subscribe([(f, 0) for f in bo_loc_khai_bao()])
            with self._khoa:
                da_biet = sorted(self._nhanh_goc)
            for g in da_biet:
                client.subscribe(f"{g}/#", 0)
            self._stats["connected"] = True
            log.info("mqtt: đã nối tới %s (%d nhánh cũ)", host, len(da_biet))

        def on_message(client, userdata, m):
            try:
                self._nap_tin(m.topic, m.payload, client.subscribe)
            except Exception as exc:
                log.warning("mqtt: nạp tin lỗi: %s", str(exc)[:120])

        def on_disconnect(client, userdata, flags, rc, props=None):
            self._stats["connected"] = False
            self._stats["_roi_tu"] = time.time()
            log.warning("mqtt: rớt (rc=%s)", str(rc)[:40])

        cl.on_connect = on_connect
        cl.on_message = on_message
        cl.on_disconnect = on_disconnect

        cl.connect(host, port, 60)
        cl.loop_start()
        try:
            # paho có thể kẹt ở trạng thái "đã rớt mà không nối lại được" mà
            # không báo gì — gương chết IM LẶNG mà vẫn trả số đã đóng băng.
            # Nên canh THÊM độ tươi: cờ connected tắt quá lâu, hoặc đang nối mà
            # không tin nào về quá lâu → ném để vòng ngoài dựng phiên mới.
            while not self._stop.is_set():
                self._stop.wait(1.0)
                now = time.time()
                if not self._stats.get("connected"):
                    if now - self._stats.get("_roi_tu", now) > _HAN_ROI:
                        raise RuntimeError(
                            f"rớt quá {_HAN_ROI:.0f}s mà paho không nối lại được")
                    self._stats.setdefault("_roi_tu", now)
                    continue
                self._stats.pop("_roi_tu", None)
                cuoi = float(self._stats.get("last_tin_ts") or 0)
                if cuoi and now - cuoi > _HAN_IM:
                    raise RuntimeError(
                        f"nối nhưng {(now - cuoi) / 60:.0f} phút không tin nào về")
        finally:
            self._stats["connected"] = False
            try:
                cl.loop_stop()
                cl.disconnect()
            except Exception:
                pass

    def _chay_mai(self) -> None:
        backoff = _BACKOFF_START
        while not self._stop.is_set():
            try:
                self._chay_mot_phien()
                backoff = _BACKOFF_START
            except Exception as exc:
                self._stats["last_error"] = str(exc)[:160]
                log.warning("mqtt: đứt (%s), thử lại sau %ss",
                            str(exc)[:160], round(backoff))
            if self._stop.is_set():
                return
            self._stats["reconnects"] += 1
            self._stop.wait(backoff)
            backoff = min(backoff * 2, _BACKOFF_CAP)

    # ── nạp tin ──────────────────────────────────────────────────────────

    def _nap_tin(self, chu_de: str, payload: bytes,
                 dang_ky: Callable | None) -> None:
        """Xử lý một tin đến. ``dang_ky`` là hàm đăng ký thêm chủ đề (có thể None).

        Tách khỏi callback của paho để test gọi thẳng được, không cần máy chủ thật.
        """
        if not isinstance(chu_de, str) or len(chu_de) > _TRAN_CHU_DE_DAI:
            return
        if not isinstance(payload, (bytes, bytearray)) or len(payload) > _TRAN_PAYLOAD:
            return
        goc = chu_de.split("/")[0]

        with self._khoa:
            moi = goc and goc not in self._nhanh_goc
            if moi:
                self._nhanh_goc.add(goc)
            # Vượt trần thì thôi không nhớ thêm chủ đề mới, nhưng vẫn cập nhật
            # chủ đề đã biết — mất chủ đề lạ còn hơn phình bộ nhớ vô hạn.
            if chu_de in self._gia_tri or len(self._gia_tri) < self._TRAN_CHU_DE:
                try:
                    gt = bytes(payload).decode("utf-8", "replace")
                except Exception:
                    gt = repr(bytes(payload)[:80])
                self._gia_tri[chu_de] = (gt[:TRAN_GIA_TRI], time.time())
            self._stats["tin"] += 1
            self._stats["last_tin_ts"] = time.time()
            self._stats["nhanh"] = len(self._nhanh_goc)

        # Nhánh gốc mới → mở rộng ra cả nhánh đó. Đây là bước biến "+" thành phủ đủ.
        if moi and dang_ky is not None:
            try:
                dang_ky(f"{goc}/#", 0)
                log.info("mqtt: nhánh mới %s", goc)
            except Exception as exc:
                log.warning("mqtt: đăng ký nhánh %s lỗi: %s", goc, str(exc)[:120])

        # Bản tự khai báo → dựng sổ thiết bị.
        if goc == "homeassistant" and chu_de.endswith("/config"):
            kq = doc_ban_khai(chu_de, bytes(payload))
            with self._khoa:
                cu = self._khai_theo_chu_de.pop(chu_de, None)
                if cu and cu[0] in self._so_thiet_bi:
                    self._so_thiet_bi[cu[0]].pop(cu[1], None)
                    if not self._so_thiet_bi[cu[0]]:
                        del self._so_thiet_bi[cu[0]]
                if kq:
                    ten_tb, ten_tt, mo_ta = kq
                    self._so_thiet_bi.setdefault(ten_tb, {})[ten_tt] = mo_ta
                    self._khai_theo_chu_de[chu_de] = (ten_tb, ten_tt)
                self._stats["thiet_bi"] = len(self._so_thiet_bi)

    # ── đọc ra ───────────────────────────────────────────────────────────

    def suc_khoe_dict(self) -> dict[str, Any]:
        """Trạng thái gương. KHÔNG BAO GIỜ chứa mật khẩu."""
        with self._khoa:
            s = dict(self._stats)
        cuoi = float(s.get("last_tin_ts") or 0)
        s["cu"] = (not cuoi) or (time.time() - cuoi > _HAN_TUOI)
        return s

    def danh_sach(self) -> list[dict[str, Any]]:
        """Thiết bị đã tìm thấy. Ba nguồn, không nguồn nào lộ mật khẩu.

        ``tu_khai_bao`` là thiết bị tự nói mình là gì (Zigbee2MQTT, Tasmota…);
        ``frigate`` là camera; ``tho`` là nhánh chưa nhận ra — hiện nguyên chủ
        đề chứ không đoán bừa nó là thiết bị gì.
        """
        ra: list[dict[str, Any]] = []
        with self._khoa:
            so = {k: dict(v) for k, v in self._so_thiet_bi.items()}
            gt = dict(self._gia_tri)
            nhanh = set(self._nhanh_goc)

        for ten, ents in sorted(so.items()):
            doc, dk = [], []
            for ten_tt, m in sorted(ents.items()):
                muc = {"ten": ten_tt, "loai": m.get("loai") or "",
                       "don_vi": m.get("don_vi") or ""}
                if m.get("dieu_khien"):
                    muc["chu_de"] = m["dieu_khien"]
                    muc["bat"] = m.get("bat")
                    muc["tat"] = m.get("tat")
                    dk.append(muc)
                else:
                    ct = m.get("doc") or ""
                    muc["chu_de"] = ct
                    v = gt.get(ct)
                    muc["gia_tri"] = v[0][:120] if v else None
                    doc.append(muc)
            ra.append({"ten": ten, "nguon": "tu_khai_bao", "nhanh": "homeassistant",
                       "doc": doc, "dieu_khien": dk})

        cam: dict[str, list[dict[str, Any]]] = {}
        for ct, (v, _ts) in sorted(gt.items()):
            f = nhan_dang_frigate(ct)
            if f and f["loai"] == "camera":
                cam.setdefault(f["camera"], []).append(
                    {"ten": f["muc"], "chu_de": ct, "gia_tri": str(v)[:120]})
        for c, ds in sorted(cam.items()):
            ra.append({"ten": f"Camera {c}", "nguon": "frigate", "nhanh": "frigate",
                       "doc": ds, "dieu_khien": []})

        da_biet = {"homeassistant", "frigate"}
        for g in sorted(nhanh - da_biet):
            ds = [{"ten": ct, "chu_de": ct, "gia_tri": str(v)[:120]}
                  for ct, (v, _ts) in sorted(gt.items()) if ct.split("/")[0] == g][:40]
            if ds:
                ra.append({"ten": g, "nguon": "tho", "nhanh": g,
                           "doc": ds, "dieu_khien": []})
        return ra

    def trang_thai_thiet_bi(self, ten: str) -> dict[str, Any] | None:
        """Giá trị mới nhất của một thiết bị. Không khớp tên thì trả ``None``."""
        khop = self._khop_ten(ten)
        if not khop:
            return None
        with self._khoa:
            ents = dict(self._so_thiet_bi.get(khop) or {})
            gt = dict(self._gia_tri)
        ra: dict[str, Any] = {"ten": khop}
        for ten_tt, m in ents.items():
            ct = m.get("doc") or ""
            if ct and ct in gt:
                ra[ten_tt] = gt[ct][0][:200]
        return ra

    def _khop_ten(self, ten: str) -> str:
        """Khớp tên với một thiết bị đã tìm thấy (khớp chính xác trước, rồi
        khớp không dấu; mập mờ thì trả rỗng — không đoán bừa)."""
        ten = (ten or "").strip()
        if not ten:
            return ""
        with self._khoa:
            so = list(self._so_thiet_bi)
        if ten in so:
            return ten
        q = _tu(ten)
        if not q:
            return ""
        ung = [k for k in so if _tu(k) == q]
        return ung[0] if len(ung) == 1 else ""

    # ── điều khiển ───────────────────────────────────────────────────────

    def dieu_khien(self, ten: str, thuc_the: str, gia_tri: Any) -> bool:
        """Gửi một lệnh tới thiết bị. Ném ``LoiMqtt`` với lời giải thích khi không được.

        Chỉ gửi khi tên khớp một thiết bị và thực thể đó thật sự có chủ đề nhận
        lệnh — không suy đoán chủ đề từ tên, vì gửi nhầm chỗ thì thiết bị khác
        bật lên mà người dùng không biết vì sao.
        """
        khop = self._khop_ten(ten)
        if not khop:
            with self._khoa:
                co = sorted(self._so_thiet_bi)
            if not co:
                raise LoiMqtt("Chưa tìm thấy thiết bị nào trên MQTT. Quét trước.")
            raise LoiMqtt(f"Không rõ '{ten}' là thiết bị nào. "
                          f"Đang có: {', '.join(co[:12])}.")

        with self._khoa:
            ents = dict(self._so_thiet_bi.get(khop) or {})

        m = ents.get(thuc_the)
        if m is None:
            q = _tu(thuc_the)
            ung = [k for k in ents if _tu(k) == q] if q else []
            if len(ung) == 1:
                thuc_the, m = ung[0], ents[ung[0]]
        if m is None:
            dk = [k for k, v in ents.items() if v.get("dieu_khien")]
            raise LoiMqtt(f"Thiết bị '{khop}' không có '{thuc_the}'. "
                          f"Điều khiển được: {', '.join(dk) if dk else 'không có gì'}.")

        ct = m.get("dieu_khien")
        if not ct:
            raise LoiMqtt(f"'{thuc_the}' của '{khop}' chỉ đọc được, không điều khiển được.")

        # Chữ bật/tắt lấy từ chính bản khai của thiết bị, không tự chế.
        # Bản khai THIẾU payload_on/payload_off thì theo mặc định của chuẩn
        # Home Assistant là "ON"/"OFF". Phải dùng `or`, KHÔNG dùng
        # m.get(k, mặc định): doc_ban_khai luôn đặt khoá với giá trị None khi
        # thiết bị không khai, và .get trả None đó — trước vòng sửa NV151,
        # «tat» rơi xuống nhánh `tin is None` thành "ON" (bấm tắt hoá bật).
        tin = gia_tri
        if isinstance(gia_tri, bool):
            tin = (m.get("bat") or "ON") if gia_tri else (m.get("tat") or "OFF")
        elif isinstance(gia_tri, str) and _gap_dau(gia_tri.strip()) in ("on", "bat", "mo"):
            tin = m.get("bat") or "ON"
        elif isinstance(gia_tri, str) and _gap_dau(gia_tri.strip()) in ("off", "tat", "dong"):
            tin = m.get("tat") or "OFF"
        if tin is None:
            tin = "ON"

        cl, host, port = self._tao_client(_client_id("web2api-mqtt-gui"))
        try:
            cl.connect(host, port, 20)
            cl.loop_start()
            r = cl.publish(ct, str(tin), qos=1)
            r.wait_for_publish(10)
            ok = r.is_published()
        except Exception as exc:
            raise LoiMqtt(f"Gửi lệnh không được: {str(exc)[:120]}")
        finally:
            try:
                cl.loop_stop()
                cl.disconnect()
            except Exception:
                pass

        log.info("mqtt: đã gửi lệnh tới %s/%s", khop, thuc_the)
        if not ok:
            raise LoiMqtt("Máy chủ MQTT không xác nhận đã nhận lệnh.")
        return True

    # ── quét / thử ──────────────────────────────────────────────────────

    def lam_moi(self, giay: float = 20) -> dict[str, Any]:
        """Quét lại máy chủ trong ``giay`` giây, trả tóm tắt tìm được.

        Mở một kết nối riêng, nghe một lúc rồi đóng. Không đụng luồng nền đang
        chạy — hai kết nối cùng lúc là chuyện bình thường với MQTT, và tách ra
        thì quét hỏng cũng không làm chết gương.
        """
        giay = max(3.0, min(float(giay or 20), 60.0))
        cl, host, port = self._tao_client(_client_id("web2api-mqtt-quet"))

        def on_connect(client, userdata, flags, rc, props=None):
            client.subscribe([(f, 0) for f in bo_loc_do_sau()])
            client.subscribe([(f, 0) for f in bo_loc_khai_bao()])

        def on_message(client, userdata, m):
            try:
                self._nap_tin(m.topic, m.payload, client.subscribe)
            except Exception:
                pass

        cl.on_connect = on_connect
        cl.on_message = on_message
        try:
            cl.connect(host, port, 30)
        except Exception as exc:
            raise LoiMqtt(f"Không nối được tới {host}:{port} — {str(exc)[:120]}")
        cl.loop_start()
        time.sleep(giay)
        cl.loop_stop()
        try:
            cl.disconnect()
        except Exception:
            pass

        with self._khoa:
            kq = {"so_thiet_bi": len(self._so_thiet_bi), "so_chu_de": len(self._gia_tri),
                  "nhanh": sorted(self._nhanh_goc)}
        log.info("mqtt: quét xong: %s", kq)
        return kq

    def thu_ket_noi(self, cho_giay: float = 5.0) -> dict[str, Any]:
        """Thử nối tới máy chủ đã khai (chưa cần lưu). Trả ``{ok, error}``.

        Nghe một lúc để đếm nhánh — nối được mà không tin nào về thường là
        quyền bị giới hạn, và người dùng cần biết điều đó ngay lúc khai chứ
        không phải lúc bot trả lời sai.
        """
        mqtt = _can_paho()
        try:
            host, port = self._tach_may_chu()
        except LoiMqtt as exc:
            return {"ok": False, "error": str(exc)}

        thay: set[str] = set()
        da_xac_nhan = threading.Event()
        cl = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=_client_id("web2api-mqtt-thu"))
        if self.nguoi_dung:
            cl.username_pw_set(self.nguoi_dung, self._mat_khau.gia_tri)

        def _khi_noi(c, u, f, rc, props=None):
            # Chỉ coi là nối được khi máy chủ TRẢ LỜI CONNACK thành công —
            # socket "nối được" mà không ai trả lời (tường lửa/proxy trong
            # suốt nuốt kết nối) không phải là máy chủ MQTT đang chạy.
            that_bai = getattr(rc, "is_failure", False)
            if not that_bai:
                da_xac_nhan.set()
                c.subscribe([(x, 0) for x in bo_loc_do_sau()])

        cl.on_connect = _khi_noi
        cl.on_message = lambda c, u, m: thay.add(m.topic.split("/")[0])

        try:
            cl.connect(host, port, 20)
        except Exception as exc:
            return {"ok": False, "error": f"Không nối được: {str(exc)[:140]}"}
        cl.loop_start()
        time.sleep(max(1.0, cho_giay))
        cl.loop_stop()
        try:
            cl.disconnect()
        except Exception:
            pass

        if not da_xac_nhan.is_set():
            return {"ok": False,
                    "error": f"Nối được tới {host}:{port} nhưng không nhận được "
                             "xác nhận từ máy chủ MQTT."}
        return {"ok": True, "so_nhanh": len(thay), "nhanh": sorted(thay)}

    def _reset_for_tests(self) -> None:
        """Xoá sạch trạng thái. Chỉ dùng trong test."""
        with self._khoa:
            self._gia_tri.clear()
            self._so_thiet_bi.clear()
            self._khai_theo_chu_de.clear()
            self._nhanh_goc.clear()
        self._stats.update({"connected": False, "tin": 0, "thiet_bi": 0, "nhanh": 0,
                            "last_error": "", "reconnects": 0, "last_tin_ts": 0.0})
