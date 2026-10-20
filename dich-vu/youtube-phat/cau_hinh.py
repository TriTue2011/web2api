"""Cấu hình dịch vụ youtube-phat: biến môi trường, thư mục dữ liệu, khoá bí mật.

Viết mới cho dịch vụ độc lập (đợt A NV153). Ý tưởng tệp bí mật (ghi qua .tmp +
chmod 0600 + rename nguyên tử) chép từ c2a
(TriTue2011/chatgpt2api, services/youtube_phat/dich_vu.py, commit d879b03) —
CHỈ ĐỌC nguồn, không sửa c2a.

Biến môi trường:
    YT_DATA_DIR   thư mục dữ liệu gốc (mặc định "/data"); dữ liệu dịch vụ nằm ở
                  `<YT_DATA_DIR>/youtube_phat/` (playlists.json, history.json,
                  integration_token, hmac_key, cai_dat.json, zing_keys*.json).
    YT_HOST       địa chỉ bind (mặc định "127.0.0.1").
    YT_PORT       cổng HTTP (mặc định 8713).
    YT_PUBLISH_IP IP LAN công bố cho loa (vd "192.168.1.50") — ghép với YT_PORT
                  thành URL gốc; không đặt thì server.py tự tính theo Host.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

TEN_THU_MUC_DV = "youtube_phat"
MAY_CHU_MAC_DINH = "127.0.0.1"
CONG_MAC_DINH = 8713
THU_MUC_DU_LIEU_MAC_DINH = "/data"


def thu_muc_dich_vu(thu_muc_du_lieu: str | Path) -> Path:
    """Thư mục dữ liệu riêng của dịch vụ: `<thu_muc_du_lieu>/youtube_phat/`."""
    return Path(thu_muc_du_lieu) / TEN_THU_MUC_DV


def _ghi_tep_bi_mat(duong: Path, du_lieu: bytes) -> None:
    """Ghi tệp bí mật: qua .tmp + chmod 0600 + rename nguyên tử."""
    duong.parent.mkdir(parents=True, exist_ok=True)
    tam = duong.with_name(duong.name + ".tmp")
    tam.write_bytes(du_lieu)
    tam.chmod(0o600)
    tam.replace(duong)


def giai_quyet_token_tich_hop(thu_muc_du_lieu: str | Path) -> str:
    """Token Integration API: đọc tệp `integration_token` trong thư mục dữ liệu,
    chưa có thì sinh (`secrets.token_urlsafe(32)`) và lưu quyền 0600.

    Token KHÔNG bao giờ được in/log — hàm này chỉ trả về chuỗi cho người gọi giữ.
    """
    duong = Path(thu_muc_du_lieu) / "integration_token"
    try:
        token = duong.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if token:
        return token
    token = secrets.token_urlsafe(32)
    _ghi_tep_bi_mat(duong, token.encode("utf-8"))
    return token


def _khoa_hmac(thu_muc: Path) -> bytes:
    """Khoá HMAC ký URL luồng: tệp `hmac_key` (0600), chưa có thì
    `secrets.token_bytes(32)` ghi qua .tmp + rename như token."""
    duong = thu_muc / "hmac_key"
    try:
        khoa = duong.read_bytes()
    except OSError:
        khoa = b""
    if khoa:
        return khoa
    khoa = secrets.token_bytes(32)
    _ghi_tep_bi_mat(duong, khoa)
    return khoa


def _url_lan_tu_env() -> str | None:
    """URL LAN công bố cho loa từ YT_PUBLISH_IP + YT_PORT, None nếu không đặt."""
    ip = (os.environ.get("YT_PUBLISH_IP") or "").strip().rstrip("/")
    if not ip:
        return None
    cong = (os.environ.get("YT_PORT") or "").strip() or str(CONG_MAC_DINH)
    if "://" in ip:
        return ip  # chủ máy tự đưa URL đầy đủ
    return f"http://{ip}:{cong}"


@dataclass
class CauHinh:
    thu_muc_du_lieu: str  # thư mục gốc (YT_DATA_DIR); dữ liệu ở <này>/youtube_phat/
    may_chu: str = MAY_CHU_MAC_DINH
    cong: int = CONG_MAC_DINH
    url_lan: str | None = None
    khoa_hmac: bytes = field(default=b"", repr=False)


def doc_cau_hinh(thu_muc_du_lieu: str | None = None) -> CauHinh:
    """Đọc cấu hình từ biến môi trường; tạo thư mục dữ liệu nếu chưa có.

    `thu_muc_du_lieu` cho phép gọi trực tiếp (test) ghi đè YT_DATA_DIR."""
    goc = (thu_muc_du_lieu or os.environ.get("YT_DATA_DIR") or "").strip() or THU_MUC_DU_LIEU_MAC_DINH
    thu_muc = thu_muc_dich_vu(goc)
    thu_muc.mkdir(parents=True, exist_ok=True)
    cong_raw = (os.environ.get("YT_PORT") or "").strip()
    try:
        cong = int(cong_raw) if cong_raw else CONG_MAC_DINH
    except ValueError:
        cong = CONG_MAC_DINH
    return CauHinh(
        thu_muc_du_lieu=goc,
        may_chu=(os.environ.get("YT_HOST") or "").strip() or MAY_CHU_MAC_DINH,
        cong=cong,
        url_lan=_url_lan_tu_env(),
        khoa_hmac=_khoa_hmac(thu_muc),
    )
