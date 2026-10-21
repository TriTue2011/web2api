# test_c2a_loc_du_lieu_nha.py — Quét toàn bộ thư mục dịch vụ bảo đảm không lọt bí mật hay dữ liệu thật của nhà
from __future__ import annotations

import re
from pathlib import Path

import pytest

GOC_DICH_VU = Path(__file__).resolve().parent
GOC_REPO = GOC_DICH_VU.parents[1]

# 1. Regex email tổng quát
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
MIEN_VI_DU_HOP_LE = {"example.com", "example.org", "example.net"}

# 2. Regex IPv4
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
IP_CHO_PHEP = {
    "127.0.0.1",  # localhost nghe nội bộ
    "0.0.0.0",    # địa chỉ nghe trong container
}
# Danh sách ngoại lệ nhỏ có chú thích rõ:
IP_ALLOWLIST_DOCS = {
    "130.0.0.0",        # Phiên bản User-Agent trình duyệt Chrome (Chrome/130.0.0.0)
    "169.254.169.254",  # Địa chỉ metadata dịch vụ đám mây trong test chống SSRF
    "169.254.0.0",      # Dải link-local trong kiểm tra chặn SSRF của module
    "224.0.0.1",        # Địa chỉ multicast trong test kiểm tra chặn SSRF
}

# 3. Regex MAC
MAC_RE = re.compile(r"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b")

# 4. Regex chuỗi giống khoá / token / JWT (nối chuỗi để bản thân test không bị bắt nhầm)
KHOA_RE = re.compile(
    r"\b("
    + "AI" + r"za[0-9A-Za-z_-]{35}|"
    + "gh" + r"p_[0-9A-Za-z]{36}|"
    + "s" + r"k-[0-9A-Za-z]{20,}|"
    + "xo" + r"x[bap]-[0-9A-Za-z-]{10,}|"
    + "ey" + r"J[0-9A-Za-z_-]{30,}\.[0-9A-Za-z_-]{30,}"
    + r")\b"
)

# 5. Regex tên hồ sơ google-
TEN_HO_SO_RE = re.compile(r"\bgoogle-[a-z0-9._-]+\b")
TEN_HO_SO_CHO_PHEP = {
    "google-oauth2",
    "google-fx",
    "google-chrome",
    "google-chrome-stable",
    "google-chrome.gpg",
    "google-chrome.list",
    "google-session",
    "google-chooser",
    "google-multi",
    "google-onboard",
}

# 6. Regex UUID
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
UUID_CHO_PHEP = {
    "00000000-0000-0000-0000-000000000000",
}


def kiem_ten_ho_so(txt: str) -> list[str]:
    loi = []
    for m in TEN_HO_SO_RE.finditer(txt):
        val = m.group(0)
        # Cho phép google-nguoi-[a-z]
        if re.match(r"^google-nguoi-[a-z]$", val):
            continue
        # Cho phép các tên cố định
        if val in TEN_HO_SO_CHO_PHEP:
            continue
        loi.append(val)
    return loi


def kiem_uuid(txt: str) -> list[str]:
    loi = []
    for m in UUID_RE.finditer(txt):
        val = m.group(0)
        if val in UUID_CHO_PHEP:
            continue
        loi.append(val)
    return loi


def kiem_email(txt: str) -> list[str]:
    loi = []
    for m in EMAIL_RE.finditer(txt):
        mien = m.group(1).lower()
        if mien not in MIEN_VI_DU_HOP_LE and not mien.endswith((".example.com", ".example.org", ".example.net")):
            loi.append(m.group(0))
    return loi


def kiem_ip(txt: str) -> list[str]:
    loi = []
    for m in IPV4_RE.finditer(txt):
        ip = m.group(0)
        if ip in IP_CHO_PHEP or ip in IP_ALLOWLIST_DOCS:
            continue
        if ip.startswith(("192.0.2.", "198.51.100.", "203.0.113.")):
            continue
        loi.append(ip)
    return loi


def kiem_mac(txt: str) -> list[str]:
    return [m.group(0) for m in MAC_RE.finditer(txt)]


def kiem_khoa(txt: str) -> list[str]:
    return [m.group(0) for m in KHOA_RE.finditer(txt)]


def _duyet_tep_van_ban():
    cac_tap = []
    # 1. dich-vu/may-dang-nhap/
    cac_tap.extend(GOC_DICH_VU.rglob("*"))
    # 2. modules/may-dang-nhap/
    mod_dir = GOC_REPO / "modules" / "may-dang-nhap"
    if mod_dir.is_dir():
        cac_tap.extend(mod_dir.rglob("*"))
    # 3. test/test_web2api_dang_nhap_tu_dong.py
    test_dn = GOC_REPO / "test" / "test_web2api_dang_nhap_tu_dong.py"
    if test_dn.is_file():
        cac_tap.append(test_dn)

    for f in cac_tap:
        if not f.is_file():
            continue
        # Bỏ qua tệp hash requirements, thư mục git, cache và chính tệp test quét
        if f.name.startswith("requirements-") or ".git" in str(f) or "__pycache__" in str(f) or f.name == "test_c2a_loc_du_lieu_nha.py":
            continue
        try:
            txt = f.read_text(encoding="utf-8")
            yield f, txt
        except Exception:
            continue


def test_khong_ten_ho_so_la():
    loi = []
    for f, txt in _duyet_tep_van_ban():
        phat_hien = kiem_ten_ho_so(txt)
        if phat_hien:
            loi.append(f"{f.name}: {len(phat_hien)} mục ({phat_hien})")
    assert not loi, f"Phát hiện tên hồ sơ lạ: {len(loi)} tệp vi phạm"


def test_khong_uuid_that():
    loi = []
    for f, txt in _duyet_tep_van_ban():
        phat_hien = kiem_uuid(txt)
        if phat_hien:
            loi.append(f"{f.name}: {len(phat_hien)} mục ({phat_hien})")
    assert not loi, f"Phát hiện UUID ngoài danh sách cho phép: {len(loi)} tệp vi phạm"


def test_khong_email_that():
    loi = []
    for f, txt in _duyet_tep_van_ban():
        phat_hien = kiem_email(txt)
        if phat_hien:
            loi.append(f"{f.name}: {len(phat_hien)} mục ({phat_hien})")
    assert not loi, f"Phát hiện địa chỉ email thật: {len(loi)} tệp vi phạm"


def test_khong_ip_that():
    loi = []
    for f, txt in _duyet_tep_van_ban():
        phat_hien = kiem_ip(txt)
        if phat_hien:
            loi.append(f"{f.name}: {len(phat_hien)} mục ({phat_hien})")
    assert not loi, f"Phát hiện địa chỉ IP không hợp lệ ngoài dải tài liệu/nghe: {len(loi)} tệp vi phạm"


def test_khong_mac_address():
    loi = []
    for f, txt in _duyet_tep_van_ban():
        phat_hien = kiem_mac(txt)
        if phat_hien:
            loi.append(f"{f.name}: {len(phat_hien)} mục ({phat_hien})")
    assert not loi, f"Phát hiện địa chỉ MAC: {len(loi)} tệp vi phạm"


def test_khong_chuoi_khoa_that():
    loi = []
    for f, txt in _duyet_tep_van_ban():
        phat_hien = kiem_khoa(txt)
        if phat_hien:
            loi.append(f"{f.name}: {len(phat_hien)} mục ({phat_hien})")
    assert not loi, f"Phát hiện chuỗi giống khoá/token thật: {len(loi)} tệp vi phạm"


def test_khong_tep_va_thu_muc_cam_tren_dia():
    cam = {"accounts.db", ".env", "vault.key", "khoa-dich-vu", "data"}
    loi = []
    for p in GOC_DICH_VU.rglob("*"):
        if p.name in cam or p.name.endswith(".sqlite"):
            loi.append(str(p.relative_to(GOC_DICH_VU)))
    assert not loi, f"Phát hiện tệp/thư mục cấm trong cây dịch vụ: {len(loi)} mục"


def test_dot_bien_phat_hien_ten_ho_so_la():
    """Mutation testing: nếu có tên profile google- lạ -> kiem_ten_ho_so phát hiện lỗi."""
    ten_la = "google-" + "nguoi" + "99"
    loi = kiem_ten_ho_so(f"Profile: {ten_la}")
    assert len(loi) > 0, "Hàm kiem_ten_ho_so phải phát hiện tên profile lạ"
    assert ten_la in loi

    # Khẳng định mẫu cho phép không bị bắt
    for ten_hop_le in ["google-nguoi-a", "google-nguoi-b", "google-oauth2", "google-fx"]:
        assert not kiem_ten_ho_so(f"Profile: {ten_hop_le}"), f"Mẫu hợp lệ {ten_hop_le} không được bị bắt"


def test_dot_bien_phat_hien_uuid_that():
    """Mutation testing: nếu có UUID lạ -> kiem_uuid phát hiện lỗi."""
    uuid_la = "12345678-" + "1234-" + "1234-" + "1234-" + "123456789abc"
    loi = kiem_uuid(f"ID: {uuid_la}")
    assert len(loi) > 0, "Hàm kiem_uuid phải phát hiện UUID lạ"
    assert uuid_la in loi

    # Khẳng định UUID cho phép không bị bắt
    uuid_cho_phep = "00000000-0000-0000-0000-000000000000"
    assert not kiem_uuid(f"ID: {uuid_cho_phep}"), "UUID 0000... cho phép không được bị bắt"


def test_dot_bien_phat_hien_email_la():
    """Mutation testing: nếu có email lạ trong nội dung -> hàm kiem_email phát hiện và trả lỗi."""
    email_gia = "nguoi.x@" + "gmail.com"
    mau_gia = f"Đây là văn bản chứa email {email_gia} của người dùng."
    loi = kiem_email(mau_gia)
    assert len(loi) > 0, "Hàm kiem_email phải phát hiện email lạ"
    assert email_gia in loi

    # Khẳng định email hợp lệ không bị bắt
    assert not kiem_email("Email: nguoi.a@example.com va nguoi.b@sub.example.com")


def test_dot_bien_phat_hien_khoa_that():
    """Mutation testing: nếu có chuỗi dạng sk- hoặc xox trong nội dung -> hàm kiem_khoa phát hiện và trả lỗi."""
    khoa_mau = "s" + "k-123456789012345678901234567890"
    loi = kiem_khoa(f"Bearer {khoa_mau}")
    assert len(loi) > 0, "Hàm kiem_khoa phải phát hiện chuỗi dạng sk-"
    assert khoa_mau in loi

    khoa_slack = "xo" + "xb-123456789012-abcdef12345"
    loi_slack = kiem_khoa(f"Token: {khoa_slack}")
    assert len(loi_slack) > 0, "Hàm kiem_khoa phải phát hiện chuỗi dạng xoxb-"
    assert khoa_slack in loi_slack


def test_dot_bien_phat_hien_ip_lan():
    """Mutation testing: nếu có IP LAN -> kiem_ip phát hiện và trả lỗi."""
    ip_lan = "192.168." + "1.100"
    loi = kiem_ip(f"Server listen on {ip_lan}")
    assert len(loi) > 0, "Hàm kiem_ip phải phát hiện IP LAN"
    assert ip_lan in loi

    assert not kiem_ip("IP: 127.0.0.1, 0.0.0.0, 192.0.2.1")
