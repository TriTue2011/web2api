"""Kiểm tra tính nhất quán và an toàn của tài liệu docs/TRIEN-KHAI.md.

Quy tắc:
1. Mọi biến WEB2API_* được nhắc trong docs/TRIEN-KHAI.md đều phải có trong docs/THAM-CHIEU.md
   (suy tự động từ nội dung, không dùng danh sách gõ tay).
2. Tài liệu không chứa địa chỉ IP riêng thật hoặc IP thật ngoài các dải tài liệu chuẩn RFC 5737
   (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24) và loopback (127.0.0.1).
3. Đầy đủ các phần kiến trúc và bảo mật cốt lõi.
"""

import ipaddress
import re
from pathlib import Path

GOC = Path(__file__).resolve().parents[1]
TRIEN_KHAI_PATH = GOC / "docs" / "TRIEN-KHAI.md"
THAM_CHIEU_PATH = GOC / "docs" / "THAM-CHIEU.md"

DAI_RFC5737_VA_CHO_PHEP = [
    ipaddress.ip_network("192.0.2.0/24"),      # TEST-NET-1 (RFC 5737)
    ipaddress.ip_network("198.51.100.0/24"),   # TEST-NET-2 (RFC 5737)
    ipaddress.ip_network("203.0.113.0/24"),    # TEST-NET-3 (RFC 5737)
    ipaddress.ip_network("127.0.0.0/8"),       # Loopback (RFC 1122)
    ipaddress.ip_network("0.0.0.0/32"),        # Any address (mô tả bind cổng 0.0.0.0)
]


def test_moi_bien_web2api_trong_trien_khai_co_trong_tham_chieu():
    assert TRIEN_KHAI_PATH.is_file(), "docs/TRIEN-KHAI.md phải tồn tại"
    assert THAM_CHIEU_PATH.is_file(), "docs/THAM-CHIEU.md phải tồn tại"

    nd_tk = TRIEN_KHAI_PATH.read_text(encoding="utf-8")
    nd_tc = THAM_CHIEU_PATH.read_text(encoding="utf-8")

    bien_tk = set(re.findall(r"\bWEB2API_[A-Z0-9_]+\b", nd_tk))
    bien_tc = set(re.findall(r"\bWEB2API_[A-Z0-9_]+\b", nd_tc))

    assert bien_tk, "docs/TRIEN-KHAI.md phải nhắc ít nhất một biến WEB2API_*"
    thieu = sorted(bien_tk - bien_tc)
    assert not thieu, f"Biến trong TRIEN-KHAI.md chưa có trong THAM-CHIEU.md: {thieu}"


def test_khong_chua_ip_rieng_that_ngoai_rfc5737():
    nd_tk = TRIEN_KHAI_PATH.read_text(encoding="utf-8")

    # Tìm các mẫu giống IPv4
    cac_ip = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", nd_tk)
    assert cac_ip, "Tài liệu phải có ví dụ IP (ít nhất loopback hoặc RFC 5737)"

    for ip_str in cac_ip:
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        hop_le = any(addr in net for net in DAI_RFC5737_VA_CHO_PHEP)
        assert hop_le, (
            f"Địa chỉ IP {ip_str} trong docs/TRIEN-KHAI.md không thuộc dải tài liệu RFC 5737 "
            f"hoặc loopback cho phép"
        )


def test_noi_dung_trien_khai_du_cac_muc_bat_buoc():
    nd_tk = TRIEN_KHAI_PATH.read_text(encoding="utf-8")

    # 1. Sơ đồ mermaid
    assert "```mermaid" in nd_tk
    assert "Cloudflare" in nd_tk

    # 2. Hai kiểu đặt sau Cloudflare
    assert "Cloudflare Tunnel" in nd_tk
    assert "cloudflared" in nd_tk
    assert "WEB2API_TRUST_PROXY" in nd_tk
    assert "cloudflare.com/ips" in nd_tk

    # 3. Hậu quả khi thiếu TRUST_PROXY và cách kiểm tra
    assert "429" in nd_tk
    assert "Retry-After" in nd_tk
    assert "/api/nhat-ky" in nd_tk or "nhat-ky" in nd_tk

    # 4. HSTS và TLS
    assert "HSTS" in nd_tk
    assert "TLS" in nd_tk or "HTTPS" in nd_tk

    # 5. SSH và cổng mạng
    assert "PasswordAuthentication no" in nd_tk
    assert "UPnP" in nd_tk

    # 6. Bảng danh sách kiểm
    assert "- [ ]" in nd_tk or "- [x]" in nd_tk

    # 7. Các lỗi hay gặp
    assert "lỗi hay gặp" in nd_tk.lower() or "cac-loi-hay-gap" in nd_tk.lower()
