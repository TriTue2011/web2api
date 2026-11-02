"""Máy chủ RouterOS GIẢ phục vụ bộ test NV279.

Hỗ trợ đầy đủ các lệnh đọc mới:
- /interface/print (bộ đếm sửa được giữa các lượt)
- /system/resource/print (uptime, cpu, ram, nhiệt độ sửa được)
- /interface/wireguard/peers/print
- /ip/kid-control/device/print
- /interface/monitor-traffic
- Giả lập !trap theo lệnh, lỗi mạng, và trả chậm (delay).
"""
from __future__ import annotations

import socketserver
import ssl
import threading
import time
from typing import Any

from test_mikrotik_routeros import (
    MayChuGia,
    _XuLy,
    _cau,
    _doc_cau,
    _lay_tu,
    LEASES_GIA,
    HOSTS_GIA,
    NGUOI_DUNG_GIA,
    MAT_KHAU_GIA,
)

INTERFACES_GIA = [
    {
        "name": "ether1",
        "type": "ether",
        "running": "true",
        "disabled": "false",
        "rx-byte": "1000000",
        "tx-byte": "2000000",
    },
    {
        "name": "ether2",
        "type": "ether",
        "running": "false",
        "disabled": "false",
        "rx-byte": "0",
        "tx-byte": "0",
    },
    {
        "name": "wlan1",
        "type": "wlan",
        "running": "false",
        "disabled": "true",
        "rx-byte": "500",
        "tx-byte": "500",
    },
]

RESOURCE_GIA = [
    {
        "uptime": "1w2d3h4m5s",
        "cpu-load": "15",
        "free-memory": "134217728",    # 128 MiB
        "total-memory": "268435456",   # 256 MiB -> 50%
        "temperature": "42.5",
        "cpu-temperature": "45.0",
    }
]

WIREGUARD_PEERS_GIA = [
    {
        "interface": "wg0",
        "name": "dien-thoai-chinh",
        "comment": "peer 1",
        "public-key": "aW52YWxpZC1wdWJsaWMta2V5LXRlc3QtMDEyMzQ1Njc4OQ==",
        "last-handshake": "45s",
        "rx": "1024",
        "tx": "2048",
        "disabled": "false",
    },
    {
        "interface": "wg0",
        "name": "may-tinh-phu",
        "comment": "peer 2",
        "public-key": "YW5vdGhlci1pbnZhbGlkLXB1YmxpYy1rZXktdGVzdC05ODc2NQ==",
        "last-handshake": "never",
        "rx": "0",
        "tx": "0",
        "disabled": "false",
    },
]

KID_CONTROL_GIA = [
    {
        "mac-address": "AA:BB:CC:00:00:01",
        "name": "may-tinh-van-phong",
        "rate-down": "1500000",  # 1.5 Mbit/s
        "rate-up": "500000",     # 0.5 Mbit/s
        "disabled": "false",
    }
]


class _XuLyNV279(_XuLy):
    def _phuc_vu(self, s, cfg):
        while True:
            cau = _doc_cau(s)
            if cau is None:
                return
            cfg["da_nhan"].append(cau)
            if not cau:
                continue
            lenh = cau[0]

            # Chế độ lỗi RIÊNG theo lệnh (nhieu_cau / cat_giua_chung / rac…), các lệnh khác về chế độ gốc
            cfg["che_do"] = cfg.get("che_do_theo_lenh", {}).get(lenh, cfg["che_do_goc"])

            # Kiểm tra độ trễ theo lệnh
            do_tre = cfg.get("do_tre_theo_lenh", {}).get(lenh, 0.0)
            if do_tre > 0:
                time.sleep(do_tre)

            # Kiểm tra bẫy !trap theo lệnh
            if lenh in cfg.get("traps", set()):
                s.sendall(_cau("!trap", f"=message=feature disabled on {lenh}") + _cau("!done"))
                continue

            if lenh == "/login":
                ten, mk = _lay_tu(cau, "name"), _lay_tu(cau, "password")
                cfg["da_thu_dang_nhap"].append((ten, mk))
                if ten == cfg["nguoi_dung"] and mk == cfg["mat_khau"]:
                    s.sendall(_cau("!done"))
                else:
                    s.sendall(_cau("!trap", "=message=cannot log in") + _cau("!done"))
            elif lenh == "/ip/dhcp-server/lease/print":
                self._tra_bang(s, cfg, cfg.get("leases", LEASES_GIA))
            elif lenh == "/interface/bridge/host/print":
                self._tra_bang(s, cfg, cfg.get("hosts", HOSTS_GIA))
            elif lenh == "/interface/print":
                self._tra_bang(s, cfg, cfg.get("interfaces", INTERFACES_GIA))
            elif lenh == "/system/resource/print":
                self._tra_bang(s, cfg, cfg.get("resource", RESOURCE_GIA))
            elif lenh == "/interface/wireguard/peers/print":
                self._tra_bang(s, cfg, cfg.get("wireguard", WIREGUARD_PEERS_GIA))
            elif lenh == "/ip/kid-control/device/print":
                self._tra_bang(s, cfg, cfg.get("kid_control", KID_CONTROL_GIA))
            elif lenh == "/interface/monitor-traffic":
                s.sendall(
                    _cau("!re", "=rx-bits-per-second=1000000", "=tx-bits-per-second=500000")
                    + _cau("!done")
                )
            else:
                s.sendall(_cau("!trap", "=message=unknown command") + _cau("!done"))


def tao_may_chu_nv279(chung_chi_tls, **kwargs):
    """Hàm helper tạo máy chủ giả NV279."""
    cfg: dict[str, Any] = {
        "tls": True,
        "crt": chung_chi_tls["crt"],
        "key": chung_chi_tls["key"],
        "nguoi_dung": NGUOI_DUNG_GIA,
        "mat_khau": MAT_KHAU_GIA,
        "leases": kwargs.get("leases", [dict(x) for x in LEASES_GIA]),
        "hosts": kwargs.get("hosts", [dict(x) for x in HOSTS_GIA]),
        "interfaces": kwargs.get("interfaces", [dict(x) for x in INTERFACES_GIA]),
        "resource": kwargs.get("resource", [dict(x) for x in RESOURCE_GIA]),
        "wireguard": kwargs.get("wireguard", [dict(x) for x in WIREGUARD_PEERS_GIA]),
        "kid_control": kwargs.get("kid_control", [dict(x) for x in KID_CONTROL_GIA]),
        "traps": set(kwargs.get("traps", set())),
        "do_tre_theo_lenh": dict(kwargs.get("do_tre_theo_lenh", {})),
        "che_do": kwargs.get("che_do", "thuong"),
        "che_do_goc": kwargs.get("che_do", "thuong"),
        "che_do_theo_lenh": dict(kwargs.get("che_do_theo_lenh", {})),
        "da_nhan": [],
        "da_thu_dang_nhap": [],
    }
    srv = MayChuGia(("127.0.0.1", 0), _XuLyNV279)
    srv.cfg = cfg
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    return srv, cfg
