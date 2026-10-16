"""URL ký cho media phục vụ an toàn (CHUAN-BAO-MAT-MODULE.md mục 3.8).

- ky_url(duong, *, het_han_giay, phuong_thuc="GET") -> str
- kiem_chu_ky(phuong_thuc, duong, tham_so) -> bool
- duong_tep_an_toan(goc, ten) -> Path

Chữ ký HMAC-SHA256 phủ:
  PHƯƠNG_THỨC\nĐƯỜNG_DẪN_CHUẨN_HOÁ\nHẠN_UNIX_TIMESTAMP
Khoá HMAC nội bộ được lưu tại <DATA>/bi_mat/khoa_url_ky quyền 0600 (có threading.Lock).
Tuyệt đối không nhét khoá quản trị vào URL.
Hết hạn, sai chữ ký, sai đường dẫn, đảo tham số => False mà KHÔNG tiết lộ lý do với bên ngoài.
"""

import hashlib
import hmac
import logging
import math
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

from app import kho

log = logging.getLogger("web2api.url_ky")

_KHOA_LUONG = threading.RLock()
_KHOA_URL_KY_CACHE: tuple[Path, int, bytes] | None = None
HAN_TOI_DA_GIAY = 7 * 86400  # Trần hạn tối đa 7 ngày


def _lay_khoa_ky() -> bytes:
    """Lấy hoặc sinh khoá HMAC 32 byte riêng cho URL ký.

    Lưu ở <DATA>/bi_mat/khoa_url_ky (quyền 0600).
    Thread-safe, hỗ trợ nhiều worker chia sẻ chung file khoá qua đĩa (kiểm tra mtime).
    """
    global _KHOA_URL_KY_CACHE
    with _KHOA_LUONG:
        p = kho.thu_muc_du_lieu() / "bi_mat" / "khoa_url_ky"
        if p.is_file():
            st = p.stat()
            if _KHOA_URL_KY_CACHE is not None and _KHOA_URL_KY_CACHE[0] == p and _KHOA_URL_KY_CACHE[1] == st.st_mtime_ns:
                return _KHOA_URL_KY_CACHE[2]
            du_lieu = p.read_bytes()
            if len(du_lieu) != 32:
                log.critical("Tệp khoá URL ký %s có độ dài %d != 32 bytes!", p, len(du_lieu))
                raise ValueError(f"Tệp khoá URL ký bị lỗi độ dài: {len(du_lieu)} != 32")
            _KHOA_URL_KY_CACHE = (p, st.st_mtime_ns, du_lieu)
            return du_lieu

        if _KHOA_URL_KY_CACHE is not None and _KHOA_URL_KY_CACHE[0] == p:
            return _KHOA_URL_KY_CACHE[2]

        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(p.parent, 0o700)
            except OSError:
                pass

            moi = secrets.token_bytes(32)
            fd, tam = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tam")
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(moi)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tam, p)
            except BaseException:
                try:
                    os.unlink(tam)
                except FileNotFoundError:
                    pass
                raise

            st = p.stat()
            _KHOA_URL_KY_CACHE = (p, st.st_mtime_ns, moi)
            return moi
        except Exception as e:
            log.warning(
                "Không thể ghi khoá URL ký vào %s: %s. Dùng khoá tạm trong bộ nhớ (lưu ý: các worker sẽ không chia sẻ được khoá).",
                p,
                e,
            )
            if _KHOA_URL_KY_CACHE is not None and _KHOA_URL_KY_CACHE[0] == p:
                return _KHOA_URL_KY_CACHE[2]
            tam_khoa = secrets.token_bytes(32)
            _KHOA_URL_KY_CACHE = (p, 0, tam_khoa)
            return tam_khoa


def chuan_hoa_duong(duong: str) -> str:
    """Chuẩn hoá đường dẫn URL nghiêm ngặt để ký và xác thực.

    Bỏ query string / hash. Giải mã URL bytes theo UTF-8 strict (từ chối %ff/%fe).
    Từ chối đường dẫn chứa //, .., hoặc . thành phần.
    Đảm bảo luôn bắt đầu bằng dấu '/'.
    """
    s = str(duong or "").split("?")[0].split("#")[0].strip()

    # B11: Giải mã bytes strict UTF-8
    try:
        s_bytes = urllib.parse.unquote_to_bytes(s)
        s_giai_ma = s_bytes.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        raise ValueError("đường dẫn chứa byte UTF-8 không hợp lệ")

    # B11: Từ chối hai dấu slash liền nhau //
    if "//" in s_giai_ma:
        raise ValueError("đường dẫn chứa '//' không hợp lệ")

    cac_doan = s_giai_ma.split("/")
    for doan in cac_doan:
        if doan in (".", ".."):
            raise ValueError("đường dẫn chứa '.' hoặc '..' không hợp lệ")

    if not s_giai_ma.startswith("/"):
        s_giai_ma = "/" + s_giai_ma
    return s_giai_ma


def _chuan_hoa_query(raw_query: str | dict[str, Any] | list[tuple[str, str]] | None) -> str:
    """Chuẩn hoá query string: loại bỏ _ky và _het_han, sắp xếp theo khoá và giá trị."""
    if not raw_query:
        return ""

    pairs: list[tuple[str, str]] = []
    if isinstance(raw_query, str):
        q_clean = raw_query.lstrip("?")
        for k, v in urllib.parse.parse_qsl(q_clean, keep_blank_values=True):
            if k not in ("_ky", "_het_han"):
                pairs.append((k, v))
    elif isinstance(raw_query, dict):
        for k, val in raw_query.items():
            if k in ("_ky", "_het_han"):
                continue
            if isinstance(val, (list, tuple)):
                for v in val:
                    pairs.append((str(k), str(v)))
            else:
                pairs.append((str(k), str(val)))
    elif isinstance(raw_query, (list, tuple)):
        for item in raw_query:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                k, v = str(item[0]), str(item[1])
                if k not in ("_ky", "_het_han"):
                    pairs.append((k, v))

    pairs.sort(key=lambda x: (x[0], x[1]))
    return urllib.parse.urlencode(pairs)


def duong_tep_an_toan(goc: Path | str, ten: str) -> Path:
    """Giải quyết và kiểm tra đường dẫn tệp nằm hoàn toàn dưới thư mục gốc.

    Chống tấn công Path Traversal / zip-slip / symlink trỏ ra ngoài.
    Ném ValueError nếu đường dẫn vượt ra ngoài thư mục gốc hoặc không an toàn.
    """
    goc_path = Path(goc).resolve()
    # Nếu ten rỗng hoặc chứa khoảng trắng thuần
    if not str(ten or "").strip():
        raise ValueError("đường dẫn không an toàn hoặc nằm ngoài thư mục gốc")

    dich_path = (goc_path / str(ten)).resolve()

    if not dich_path.is_relative_to(goc_path) or dich_path == goc_path:
        raise ValueError("đường dẫn không an toàn hoặc nằm ngoài thư mục gốc")

    return dich_path


def ky_url(
    duong: str,
    *,
    het_han_giay: int | float,
    phuong_thuc: str = "GET",
) -> str:
    """Ký URL media với thời hạn xác định bằng HMAC-SHA256.

    B4: Chữ ký HMAC phủ cả phương thức + đường dẫn chuẩn hoá + query đã sắp xếp + hạn timestamp.
    B10: Bỏ fragment #frag, kiểm tra nan/inf/âm/quá lớn của het_han_giay.
    Trả về chuỗi URL/đường dẫn kèm các tham số _het_han và _ky.
    """
    if (
        not isinstance(het_han_giay, (int, float))
        or isinstance(het_han_giay, bool)
        or math.isnan(het_han_giay)
        or math.isinf(het_han_giay)
        or het_han_giay <= 0
    ):
        raise ValueError("het_han_giay phải là số dương hợp lệ")

    if het_han_giay > HAN_TOI_DA_GIAY:
        raise ValueError(f"het_han_giay ({het_han_giay}) vượt trần tối đa {HAN_TOI_DA_GIAY} giây (7 ngày)")

    # B10: Loại bỏ fragment nếu có
    duong_khong_frag = duong.split("#")[0].strip()
    parts = duong_khong_frag.split("?", 1)
    path_part = parts[0]
    query_part = parts[1] if len(parts) > 1 else ""

    pt = str(phuong_thuc or "GET").upper().strip()
    p_chuan = chuan_hoa_duong(path_part)
    q_chuan = _chuan_hoa_query(query_part)
    het_han = int(time.time()) + int(het_han_giay)

    thong_diep = f"{pt}\n{p_chuan}\n{q_chuan}\n{het_han}".encode("utf-8")
    khoa = _lay_khoa_ky()
    chu_ky = hmac.new(khoa, thong_diep, hashlib.sha256).hexdigest()

    ghep = "&" if "?" in duong_khong_frag else "?"
    return f"{duong_khong_frag}{ghep}_het_han={het_han}&_ky={chu_ky}"


def kiem_chu_ky(
    phuong_thuc: str,
    duong: str,
    tham_so: dict[str, Any] | str | None = None,
) -> bool:
    """Kiểm tra tính hợp lệ của chữ ký URL.

    - phuong_thuc: phương thức HTTP gọi tới (GET, POST...).
    - duong: đường dẫn yêu cầu.
    - tham_so: dict query params hoặc chuỗi query string. Nếu None, bóc từ duong.

    Trả về True nếu chữ ký hợp lệ và chưa hết hạn; False trong mọi trường hợp khác
    (hết hạn, sai chữ ký, sai đường dẫn, sai phương thức, thiếu tham số, đảo tham số)
    mà KHÔNG tiết lộ lý do chi tiết ra bên ngoài.
    """
    pt = str(phuong_thuc or "").upper().strip()
    if not pt:
        return False

    duong_khong_frag = duong.split("#")[0].strip()
    parts = duong_khong_frag.split("?", 1)
    path_part = parts[0]
    url_query = parts[1] if len(parts) > 1 else ""

    raw_pairs: list[tuple[str, str]] = []
    if tham_so is None:
        if not url_query:
            return False
        raw_pairs = urllib.parse.parse_qsl(url_query, keep_blank_values=True)
    elif isinstance(tham_so, str):
        q_str = tham_so.lstrip("?")
        raw_pairs = urllib.parse.parse_qsl(q_str, keep_blank_values=True)
    elif isinstance(tham_so, dict):
        for k, val in tham_so.items():
            if isinstance(val, (list, tuple)):
                for v in val:
                    raw_pairs.append((str(k), str(v)))
            else:
                raw_pairs.append((str(k), str(val)))
    else:
        return False

    raw_het_han: str | None = None
    raw_ky: str | None = None
    loc_pairs: list[tuple[str, str]] = []

    for k, v in raw_pairs:
        if k == "_het_han" and raw_het_han is None:
            raw_het_han = v
        elif k == "_ky" and raw_ky is None:
            raw_ky = v
        elif k not in ("_het_han", "_ky"):
            loc_pairs.append((k, v))

    # B11: _het_han chỉ chấp nhận 1-12 chữ số ASCII thuần tuý
    if not raw_het_han or not isinstance(raw_het_han, str) or not re.fullmatch(r"^[0-9]{1,12}$", raw_het_han):
        return False

    # B5: raw_ky phải là hex 64 ký tự ASCII
    if not raw_ky or not isinstance(raw_ky, str) or not re.fullmatch(r"^[0-9a-fA-F]{64}$", raw_ky):
        return False

    het_han_int = int(raw_het_han)
    # Kiểm tra thời hạn
    if int(time.time()) > het_han_int:
        return False

    try:
        p_chuan = chuan_hoa_duong(path_part)
    except (ValueError, UnicodeError):
        return False

    loc_pairs.sort(key=lambda x: (x[0], x[1]))
    q_chuan = urllib.parse.urlencode(loc_pairs)

    thong_diep = f"{pt}\n{p_chuan}\n{q_chuan}\n{het_han_int}".encode("utf-8")
    khoa = _lay_khoa_ky()
    chu_ky_mong_doi = hmac.new(khoa, thong_diep, hashlib.sha256).hexdigest()

    # B5: So sánh digest an toàn dạng bytes ASCII chống lỗi TypeError với chuỗi non-ASCII
    return hmac.compare_digest(raw_ky.lower().encode("ascii"), chu_ky_mong_doi.lower().encode("ascii"))
