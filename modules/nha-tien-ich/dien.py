"""Mất điện — đọc UPS qua NUT, báo khi mất điện / có điện lại / pin yếu.

Port GẦN NGUYÊN từ ``services/dien_nha.py`` (c2a, commit d879b03) — thuật toán đã
đo thật ngày 05/10/2026 (mất điện 14:28, UPS cạn 14:48 mà không ai được báo):

- Đọc thẳng ``upsd`` bằng giao thức NUT (cổng 3493, ``LIST VAR``) qua socket
  stdlib — không qua cảm biến HA (HA hỏi 60s/lần, còn từ 15% pin tới lúc tắt
  máy chỉ ~1–2 phút).
- Chạy pin liên tục ``XAC_NHAN_MAT`` (10s) mới báo mất điện — chớp điện vài giây thì im.
- Không đọc được UPS quá ``MAT_DOC_GIAY`` (180s) thì báo lỗi hệ thống (NUT hỏng
  là lần mất điện sau lại im).
- Tin chưa gửi được thì GIỮ LẠI gửi sau (mất điện modem có thể tắt theo), không bỏ.
- CHIA VIỆC (giữ nguyên): việc TẮT/BẬT máy do NUT trên chính máy Proxmox lo —
  module này chỉ ĐỌC và BÁO.

Chỉ đổi lớp kết nối/cấu hình cho khuôn module web2api:

- ``services.config`` → địa chỉ NUT do module tiêm qua ``dat_dia_chi()`` (từ
  ``truong_cau_hinh.nut`` của manifest); rỗng = tắt giám sát.
- ``_FILE`` → ``_duong_dan.tep("dien_nha.json")`` (0600).
- ``thong_bao.gui`` → hàng đợi nội bộ ``hang_doi``: module giữ tin, tầng
  ``module.py`` quyết định gửi qua kênh nào (không import trực tiếp kênh).
- Bỏ luồng nền ``start()/stop()`` của c2a (lõi web2api không gọi): giữ
  ``chay_mot_lan()`` để cron/worker gọi định kỳ, và thực thể C1 ``nhatienich.dien``.
- Địa chỉ NUT đi qua bộ kiểm riêng ``kiem_dia_chi()``: chỉ host trong mạng nhà
  (LAN riêng tư; dải tài liệu 192.0.2.0/24… trong test), chặn loopback /
  link-local / multicast / không định tuyến / toàn cầu — chống SSRF
  (quy ước chung: URL do người dùng khai qua app/tu_xa.py; NUT cần luật nới
  cho dải tài liệu nên tự kiểm theo CẤU TRÚC địa chỉ, không theo chuỗi).
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from . import _duong_dan

log = logging.getLogger("web2api.nha_tien_ich.dien")

_TZ = timezone(timedelta(hours=7))
_TEP = "dien_nha.json"
_khoa = threading.RLock()

KHOA = "nha.mat_dien"
KHOA_LOI = "he_thong.loi"
#: Giây giữa hai lần đọc UPS.
NHIP_GIAY = 5.0
#: Chạy pin liên tục ngần này giây mới báo mất điện — chớp điện vài giây thì im.
XAC_NHAN_MAT = 10.0
#: Không đọc được UPS lâu ngần này thì báo lỗi hệ thống.
MAT_DOC_GIAY = 180.0
#: Tin chưa gửi được thì thử lại sau ngần này giây; quá hạn giữ thì bỏ.
GUI_LAI_GIAY = 60.0
GIU_TIN_GIAY = 6 * 3600.0
HET_GIO = 5.0

_dia_chi: str = ""


def dat_dia_chi(dc: str) -> None:
    """Tiêm địa chỉ NUT từ cấu hình module (dạng ``ups@host[:cổng]``)."""
    global _dia_chi
    _dia_chi = str(dc or "").strip()


def dat_lai() -> None:
    global _dia_chi
    _dia_chi = ""


def dia_chi() -> str:
    return _dia_chi


#: Dải được coi là «mạng nhà» cho NUT: LAN riêng tư thật + dải tài liệu
#: (RFC 5737) — test dùng 192.0.2.x làm «mạng nhà» giả. Dải benchmarking
#: 198.18.0.0/15 (DNS sandbox trỏ mọi tên miền về đây) KHÔNG phải mạng nhà.
_MANG_NHA = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),       # IPv6 unique local
    ipaddress.ip_network("192.0.2.0/24"),   # tài liệu (test)
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
)


async def kiem_dia_chi(dc: str) -> str:
    """Kiểm địa chỉ NUT: dạng ``ups@host[:cổng]`` + host phải trong MẠNG NHÀ.

    Allowlist theo CẤU TRÚC địa chỉ (không theo chuỗi): chỉ LAN riêng tư
    (10/8, 172.16/12, 192.168/16, fc00::/7) và dải tài liệu (dùng trong test
    làm mạng nhà giả) được chấp nhận. Mọi thứ khác — loopback, link-local,
    multicast, không định tuyến, toàn cầu, dải đặc biệt — đều bị chặn:
    chống SSRF (NUT phải ở trong mạng nhà, không phải máy này hay Internet).

    Trả địa chỉ đã chuẩn hoá; ném ValueError khi sai.
    """
    ups, _, may = str(dc or "").strip().partition("@")
    if not ups or not may:
        raise ValueError(f"Địa chỉ NUT phải dạng ups@máy[:cổng], nhận «{dc}»")
    host, _, cong = may.partition(":")
    host = host.strip().strip("[]")
    cong_n = int(cong or 3493)
    if not (1 <= cong_n <= 65535):
        raise ValueError(f"Cổng NUT không hợp lệ: «{cong}»")
    try:
        ds = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            thong_tin = await asyncio.get_running_loop().getaddrinfo(
                host, None, type=socket.SOCK_STREAM)
        except OSError as e:
            raise ValueError(f"không phân giải được tên máy «{host}»: {e}") from None
        ds = list({ipaddress.ip_address(x[4][0]) for x in thong_tin})
    for ip in ds:
        if ip.is_loopback:
            raise ValueError(f"«{host}» là loopback — NUT phải nằm trong mạng nhà, không phải máy này")
        if not any(ip in mang for mang in _MANG_NHA):
            raise ValueError(f"«{host}» ({ip}) ngoài mạng nhà — bị chặn (SSRF)")
    return f"{ups}@{host}:{cong_n}"


def _tach(dc: str) -> tuple[str, str, int]:
    ups, _, may = dc.partition("@")
    host, _, cong = may.partition(":")
    return ups, host, int(cong or 3493)


def doc(dc: str) -> dict[str, str]:
    """Mọi biến của UPS: ``{"ups.status": "OB", "battery.charge": "87", …}``.
    Lỗi thì ném OSError/ValueError — port nguyên từ c2a."""
    ups, host, cong = _tach(dc)
    with socket.create_connection((host, cong), timeout=HET_GIO) as s:
        s.sendall(f"LIST VAR {ups}\n".encode())
        buf = b""
        het = f"END LIST VAR {ups}\n".encode()
        while not buf.endswith(het):
            phan = s.recv(4096)
            if not phan:
                raise OSError("upsd đóng kết nối giữa chừng")
            buf += phan
            if buf.startswith(b"ERR "):
                raise ValueError(f"upsd trả lỗi: {buf.decode(errors='replace').strip()}")
    ra: dict[str, str] = {}
    for dong in buf.decode("utf-8", "replace").splitlines():
        if dong.startswith(f"VAR {ups} "):
            ten, _, gt = dong[len(f"VAR {ups} "):].partition(" ")
            ra[ten] = gt.strip().strip('"')
    return ra


_TEN_CO = {"OL": "đang dùng điện lưới", "OB": "ĐANG CHẠY PIN", "LB": "PIN YẾU", "FSD": "ĐANG TẮT MÁY",
           "CHRG": "đang sạc", "RB": "cần thay ắc quy", "OFF": "UPS tắt nguồn ra"}


def tom_tat(d: dict[str, str]) -> str:
    """Một câu cho nút «Kiểm tra kết nối» — port nguyên từ c2a."""
    co = [_TEN_CO.get(x, x) for x in d.get("ups.status", "").split()] or ["không rõ trạng thái"]
    ten = " ".join(x for x in (d.get("device.mfr"), d.get("device.model")) if x) or "UPS"
    ra = f"{ten} — {', '.join(co)}, pin {d.get('battery.charge', '?')}%"
    if d.get("battery.charge.low"):
        ra += f" (máy chủ tắt khi dưới {d['battery.charge.low']}%)"
    if d.get("ups.load"):
        ra += f", tải {d['ups.load']}%"
    if d.get("input.voltage"):
        ra += f", điện vào {d['input.voltage']} V"
    return ra


def _gio(ts: float) -> str:
    return datetime.fromtimestamp(ts, _TZ).strftime("%H:%M %d/%m")


def buoc(so: dict[str, Any], d: dict[str, str] | None, now: float,
         nut: str = "") -> list[tuple[str, str]]:
    """Một lần đọc UPS → các tin cần gửi ``[(khoá thông báo, nội dung)]``. Sửa
    ``so`` tại chỗ — port nguyên từ c2a (``d`` là None khi không đọc được UPS)."""
    tin: list[tuple[str, str]] = []
    if d is None:
        tu = so.setdefault("mat_doc_tu", now)
        if now - tu >= MAT_DOC_GIAY and not so.get("da_bao_mat_doc"):
            so["da_bao_mat_doc"] = True
            tin.append((KHOA_LOI, f"⚠️ Không đọc được UPS (NUT {nut}) từ {_gio(tu)} — mất điện sẽ KHÔNG được "
                                  f"báo. Kiểm dịch vụ nut-server trên máy Proxmox."))
        return tin
    so.pop("mat_doc_tu", None)
    if so.pop("da_bao_mat_doc", False):
        tin.append((KHOA_LOI, f"✅ Đọc lại được UPS (NUT {nut}) lúc {_gio(now)}."))

    st = d.get("ups.status", "").split()
    pin, nguong, tai = d.get("battery.charge", "?"), d.get("battery.charge.low", "?"), d.get("ups.load", "?")
    if "OB" in st:
        tu = so.setdefault("pin_tu", now)
        yeu = "LB" in st or "FSD" in st
        if not so.get("da_bao_mat") and (now - tu >= XAC_NHAN_MAT or yeu):
            so["da_bao_mat"] = True
            tin.append((KHOA, f"⚡ Mất điện từ {_gio(tu)}. UPS đang chạy pin: còn {pin}%, tải {tai}%. Pin dưới "
                              f"{nguong}% thì máy chủ Proxmox tự tắt; có điện ổn định trở lại thì tự bật."))
        if yeu and not so.get("da_bao_tat"):
            so["da_bao_tat"] = True
            tin.append((KHOA, f"🪫 Pin UPS còn {pin}% (ngưỡng {nguong}%) — máy chủ Proxmox đang tắt, mạng và mọi "
                              f"dịch vụ trong nhà tắt theo. Có điện ổn định trở lại thì máy tự bật."))
    elif "OL" in st:
        tu = so.pop("pin_tu", None)
        da_bao = so.pop("da_bao_mat", False)
        da_tat = so.pop("da_bao_tat", False)
        if tu is not None and da_bao:
            tin.append((KHOA, f"🔌 Có điện lại — mất điện từ {_gio(tu)}, đọc lại UPS lúc {_gio(now)} "
                              f"(~{(now - tu) / 60:.0f} phút). Pin UPS còn {pin}%."
                              + (" Máy chủ đã tự khởi động lại." if da_tat else "")))
    return tin


def _doc_so() -> dict[str, Any]:
    try:
        d = json.loads(_duong_dan.tep(_TEP).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _ghi_so(d: dict[str, Any]) -> None:
    p = _duong_dan.tep(_TEP)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        import os
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(p)


def chay_mot_lan(now: float | None = None) -> list[str]:
    """Đọc UPS một lần, cập nhật sổ, xếp tin vào hàng đợi. Trả các tin mới sinh
    ra lượt này. Port từ c2a (thay ``thong_bao.gui`` bằng hàng đợi nội bộ)."""
    dc = dia_chi()
    if not dc:
        return []
    now = now or time.time()
    loi = ""
    try:
        d: dict[str, str] | None = doc(dc)
    except (OSError, ValueError) as exc:
        loi, d = str(exc)[:160], None
    with _khoa:
        so = _doc_so()
        if loi and "mat_doc_tu" not in so:
            log.info("dien_nha_doc_loi nut=%s loi=%s", dc, loi)
        moi = buoc(so, d, now, nut=dc)
        so.setdefault("hang_doi", []).extend({"khoa": k, "tin": t, "luc": now} for k, t in moi)
        if moi:
            so.pop("thu_luc", None)
        _ghi_so(so)
    for k, t in moi:
        log.info("dien_nha_bao khoa=%s tin=%s", k, t[:80])
    return [t for _, t in moi]


def lay_hang_doi() -> list[dict[str, Any]]:
    """Tin đang chờ gửi (mất điện modem có thể tắt theo — giữ lại, không bỏ)."""
    return list(_doc_so().get("hang_doi") or [])


def xoa_hang_doi(khoa: str, luc: float) -> None:
    with _khoa:
        so = _doc_so()
        so["hang_doi"] = [x for x in so.get("hang_doi") or []
                          if not (x.get("khoa") == khoa and float(x.get("luc", 0)) == float(luc))]
        _ghi_so(so)


def trang_thai() -> dict[str, Any]:
    with _khoa:
        return {"nut": dia_chi(), **_doc_so()}
