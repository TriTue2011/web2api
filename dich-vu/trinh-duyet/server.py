"""Máy chủ dịch vụ trình duyệt cô lập cho web2api — chuẩn C6.

Chạy trong container riêng cạnh web2api, cung cấp trình duyệt (Chromium / Firefox)
trên màn hình ảo Xvfb với x11vnc và websockify (noVNC), cho phép người dùng đăng nhập
tay qua giao diện web và giữ lại phiên trong hồ sơ bền vững (profile directory).

Điều khiển qua HTTP nội bộ (FastAPI), xác thực bằng Bearer token hằng thời gian.
Tiến trình con chạy với môi trường tối thiểu, không kế thừa khoá bí mật.

Đường KHÔNG cần khoá (ngoại lệ có chủ đích, duy nhất): ``GET /suc-khoe`` — chỉ trả ``{"ok": true}``
để Docker/lõi kiểm còn sống, không đọc hay lộ trạng thái nào. Mọi đường khác (kể cả đường không tồn tại)
đi qua lớp xác thực ASGI ``XacThucBearer`` TRƯỚC định tuyến và phân tích thân.

websockify của mỗi hồ sơ đòi Basic auth bằng bí mật NGẪU NHIÊN 256 bit sinh mới mỗi lần dựng hồ sơ;
bí mật chỉ đi ra qua trường ``dich`` của ``POST /ho-so/mo`` (dạng userinfo của URL).

Workspace (mô hình của c2a): ``POST /ho-so`` ``{ten}`` tạo thư mục hồ sơ ID NGẪU NHIÊN + ``.workspace.json`` ghi tên
hiển thị (tên không bao giờ quyết thư mục); ``GET /ho-so`` trả ``ten``/``dang_mo``/``thu_cong``; ``POST /ho-so/mo`` nhận
``url`` tuỳ chọn (http/https, đã kiểm) làm trang đầu; ``dong`` mặc định giữ phiên và trả ``session_preserved``.

Cookie đăng nhập của hồ sơ chỉ đi ra qua ``GET /ho-so/{id}/cookie?mien=`` (Bearer như mọi đường), và chỉ với hồ sơ
FIREFOX: cookie Firefox nằm trong SQLite KHÔNG mã hoá (``cookies.sqlite``, bảng ``moz_cookies``). Chromium mã hoá
cookie và muốn đọc phải qua cổng điều khiển trình duyệt — cố ý không hỗ trợ (501), không mở cổng điều khiển nào.
Cách đọc: sao chép ``cookies.sqlite`` (+ ``-wal`` nếu có — Firefox đang mở giữ dữ liệu mới ở đó) sang thư mục tạm 0700
mới, mở bản sao chỉ-đọc, lọc theo tên miền và hạn, xoá thư mục tạm trong ``finally``. Giá trị cookie không bao giờ vào
log hay thông báo lỗi.
"""

from __future__ import annotations

import asyncio
import ctypes
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import signal
import sqlite3
import stat
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ── Nhật ký & Mẫu kiểm tra ────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("dich_vu.trinh_duyet")

#: ID hồ sơ chỉ gồm 16-64 ký tự hex chữ thường do lõi sinh, không suy từ email.
MAU_HO_SO = re.compile(r"^[a-f0-9]{16,64}$")
LOAI_HOP_LE = {"chromium", "firefox"}

#: Biến môi trường được phép truyền xuống tiến trình con — so KHỚP TÊN ĐÚNG, không theo tiền tố hay từ khoá:
#: biến mang bí mật có thể mang tên bất kỳ (kể cả ``LC_…``), nên chỉ những tên này mới đi qua.
BIEN_CHO_PHEP = frozenset(
    {
        "PATH",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "TZ",
        "USER",
        "LOGNAME",
        "TERM",
        "HOME",
        "XAUTHORITY",
        "DISPLAY",
    }
)

#: Tên người dùng Basic auth của websockify; mật khẩu là bí mật riêng của hồ sơ.
TEN_NGUOI_DUNG_WS = "w2a"
#: Thư mục chứa plugin xac_thuc_ws.py (cùng chỗ với server.py) — đưa vào PYTHONPATH của riêng websockify.
THU_MUC_MA = Path(__file__).resolve().parent
#: Thư mục con (dưới gốc hồ sơ) chứa tệp bí mật websockify; tên không khớp MAU_HO_SO nên không bị coi là hồ sơ.
TEN_THU_MUC_BI_MAT = ".bi-mat-ws"

# ── Đọc cookie hồ sơ Firefox ──
#: Tên miền: các nhãn [a-z0-9-] (không bắt đầu/kết thúc bằng «-», ≤ 63 ký tự) nối bằng «.», ÍT NHẤT HAI nhãn, tổng
#: ≤ 253. Chữ thường; không «.» đầu/cuối, không «..». Một nhãn đơn («ai», «localhost») bị từ chối: hỏi cookie của cả
#: một đuôi tên miền là lấy cookie của mọi trang dưới nó.
MAU_MIEN = re.compile(r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
#: Tên cookie: đúng bảng ký tự «token» của RFC 6265 (không khoảng trắng, không «=», «;», ký tự điều khiển).
MAU_TEN_COOKIE = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
#: Giá trị cookie: ASCII in được trừ «;» — chặn xuống dòng/ký tự điều khiển (chèn tiêu đề) và «;» (chèn cookie).
MAU_GIA_TRI_COOKIE = re.compile(r"[\x20-\x3a\x3c-\x7e]*")
TEN_TEP_COOKIE = "cookies.sqlite"
#: Trần cỡ mỗi tệp sao chép (cookies.sqlite của Firefox thường dưới vài MB).
TRAN_TEP_COOKIE_BYTE = 64 * 1024 * 1024
TRAN_SO_COOKIE = 150
#: Trần «tên=giá trị» của MỘT cookie — đúng mức tối thiểu trình duyệt phải nhận (RFC 6265 §6.1: 4096 byte).
TRAN_DO_DAI_COOKIE = 4096
TRAN_CHUOI_COOKIE = 32 * 1024
#: `expiry` của moz_cookies: Firefox cũ lưu GIÂY, bản mới lưu MILI-GIÂY. Phân biệt theo ĐỘ LỚN từng dòng: 1e11 giây là
#: năm ~5138, 1e11 mili-giây là tháng 3/1973 — không cookie còn hạn nào rơi vào vùng chồng lấn, nên ngưỡng này tách
#: chắc chắn hai đơn vị mà không phải đoán phiên bản Firefox.
NGUONG_MILI_GIAY = 10**11

# ── Workspace (mô hình của c2a: tên hiển thị tuỳ ý, thư mục hồ sơ ID ngẫu nhiên — tên không bao giờ quyết thư mục) ──
TEN_TEP_WORKSPACE = ".workspace.json"
TRAN_TEN_WORKSPACE = 100
TRAN_URL_BAT_DAU = 2048


# ── Cấu hình ──────────────────────────────────────────────────────────────────


def _so(ten: str, mac_dinh: int) -> int:
    try:
        return int(os.environ.get(ten, str(mac_dinh)))
    except (ValueError, TypeError):
        return mac_dinh


def kiem_tra_khoa(khoa: str | None = None) -> str:
    """Kiểm tra khoá dịch vụ từ biến môi trường hoặc tham số.

    Khoá bắt buộc, dài ít nhất 24 ký tự VÀ có ít nhất 10 ký tự khác nhau (chặn «aaaa…», «abab…»).
    Không đạt thì từ chối khởi động. KHÔNG in giá trị khoá ra log hay thông báo lỗi.
    """
    k = (os.environ.get("TRINH_DUYET_KHOA", "") if khoa is None else khoa).strip()
    if len(k) < 24:
        raise RuntimeError(
            "TRINH_DUYET_KHOA bắt buộc và phải có độ dài tối thiểu 24 ký tự."
        )
    if len(set(k)) < 10:
        raise RuntimeError(
            "TRINH_DUYET_KHOA quá đơn điệu: cần ít nhất 10 ký tự khác nhau (nên sinh ngẫu nhiên, ví dụ `openssl rand -hex 32`)."
        )
    return k


#: Khoá dịch vụ sau khi nạp — giữ trong bộ nhớ tiến trình, KHÔNG còn trong môi trường.
_khoa_dich_vu: str | None = None


def _xoa_dau_moi_truong(ten: str) -> None:
    """Ghi đè giá trị biến ``ten`` trong vùng nhớ môi trường gốc của tiến trình.

    ``/proc/<pid>/environ`` đọc vùng nhớ chuỗi môi trường dựng lúc exec, KHÔNG phải ``os.environ``:
    ``os.environ.pop`` chỉ gỡ con trỏ (đã đo: khoá vẫn đọc được). ``getenv`` của libc trả con trỏ vào
    đúng chuỗi đó, nên ghi số 0 lên phần giá trị là xoá được dấu. Chỉ dùng ở ``main()``.
    """
    gia_tri = os.environb.get(ten.encode())
    if not gia_tri:
        return
    libc = ctypes.CDLL(None)
    libc.getenv.restype = ctypes.c_void_p
    libc.getenv.argtypes = [ctypes.c_char_p]
    con_tro = libc.getenv(ten.encode())
    if con_tro:
        ctypes.memset(con_tro, 0, len(gia_tri))


def nap_khoa(xoa_dau: bool = False) -> str:
    """Đọc khoá dịch vụ MỘT lần vào bộ nhớ rồi gỡ khỏi môi trường tiến trình.

    Có biến ``TRINH_DUYET_KHOA`` thì kiểm, giữ vào ``_khoa_dich_vu``, (tuỳ chọn) xoá dấu trong
    ``/proc/<pid>/environ`` rồi gỡ khỏi ``os.environ``. Không có biến thì dùng khoá đã nạp trước đó;
    chưa từng nạp thì ``RuntimeError``.
    """
    global _khoa_dich_vu
    if "TRINH_DUYET_KHOA" in os.environ:
        khoa = kiem_tra_khoa()
        _khoa_dich_vu = khoa
        if xoa_dau:
            _xoa_dau_moi_truong("TRINH_DUYET_KHOA")
        os.environ.pop("TRINH_DUYET_KHOA", None)
        return khoa
    if _khoa_dich_vu is None:
        kiem_tra_khoa()  # ném RuntimeError với thông báo chuẩn
    return _khoa_dich_vu  # type: ignore[return-value]



def lay_cau_hinh() -> dict[str, Any]:
    return {
        "host": os.environ.get("TRINH_DUYET_HOST", "0.0.0.0"),
        "port": _so("TRINH_DUYET_PORT", 8700),
        "thu_muc_goc": Path(os.environ.get("TRINH_DUYET_THU_MUC_GOC", "/hoso")),
        "cong_tu": _so("TRINH_DUYET_CONG_TU", 6100),
        "cong_den": _so("TRINH_DUYET_CONG_DEN", 6199),
        "toi_da": max(1, _so("TRINH_DUYET_TOI_DA", 3)),
        "idle_phut": max(1, _so("TRINH_DUYET_IDLE_PHUT", 120)),
        "toi_da_ho_so": max(1, _so("TRINH_DUYET_TOI_DA_HO_SO", 20)),
        "toi_da_phut": max(1, _so("TRINH_DUYET_TOI_DA_PHUT", 480)),
        # Chờ ngắn sau khi dựng rồi kiểm cả bộ tiến trình còn sống (chết ngay -> 502, không để hồ sơ ma).
        "cho_khoi_dong_giay": 1.0,
        "novnc_dir": os.environ.get("TRINH_DUYET_NOVNC_DIR", "/usr/share/novnc"),
        "host_noi_bo": os.environ.get("TRINH_DUYET_HOST_NOI_BO", "trinh-duyet"),
        "chromium_bin": os.environ.get("TRINH_DUYET_CHROMIUM_BIN", "chromium"),
        "firefox_bin": os.environ.get("TRINH_DUYET_FIREFOX_BIN", "firefox-esr"),
        "xvfb_bin": os.environ.get("TRINH_DUYET_XVFB_BIN", "Xvfb"),
        "x11vnc_bin": os.environ.get("TRINH_DUYET_X11VNC_BIN", "x11vnc"),
        "websockify_bin": os.environ.get("TRINH_DUYET_WEBSOCKIFY_BIN", "websockify"),
    }


def _loc_moi_truong_con(
    display: str | None = None, home: str | None = None
) -> dict[str, str]:
    """Tạo môi trường tối thiểu cho tiến trình con: chỉ các tên trong BIEN_CHO_PHEP đi qua."""
    env: dict[str, str] = {k: v for k, v in os.environ.items() if k in BIEN_CHO_PHEP}

    if display:
        env["DISPLAY"] = display
    if home:
        env["HOME"] = home
    return env


def kiem_tra_duong_dan_ho_so(ho_so_id: str, thu_muc_goc: Path) -> Path:
    """Kiểm tra tính hợp lệ của ID hồ sơ và chống thoát thư mục (kể cả symlink)."""
    if not MAU_HO_SO.fullmatch(ho_so_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"ID hồ sơ không hợp lệ (cần chuỗi hex 16-64 ký tự): {ho_so_id}",
        )
    goc_chuan = thu_muc_goc.resolve()
    duong_dan = (goc_chuan / ho_so_id).resolve()

    # Sau resolve() phải là ĐÚNG <gốc>/<id>: chặn cả symlink ra ngoài gốc lẫn symlink sang hồ sơ KHÁC trong gốc (IDOR).
    if duong_dan != goc_chuan / ho_so_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Đường dẫn hồ sơ không hợp lệ (thoát khỏi thư mục gốc).",
        )
    return duong_dan


def mo_tep_thuong(duong: Path | str, co: int, quyen: int = 0o600, dir_fd: int | None = None) -> int:
    """Mở tệp nằm ở vùng trình duyệt (cùng uid) ghi được: O_NOFOLLOW (không theo symlink sang hồ sơ khác), O_NONBLOCK
    (FIFO không làm treo lúc mở — mở FIFO để đọc/ghi mà không có đầu kia sẽ chặn mãi), rồi CHỈ nhận tệp thường (fstat).
    Với tệp thường O_NONBLOCK không đổi cách đọc/ghi. Không phải tệp thường → OSError."""
    fd = os.open(duong, co | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, quyen, dir_fd=dir_fd)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError("không phải tệp thường")
    return fd


class _KhongCoKhoCookie(Exception):
    """Không có tệp cookie dùng được (thiếu, symlink, không phải tệp thường) — trả 404 chung."""


class _TepCookieQuaLon(Exception):
    pass


def _khop_mien(host: str, mien: str) -> bool:
    """Cookie của `mien` hoặc tên miền con của nó. `host` của Firefox có «.» đầu với cookie dạng domain."""
    h = host[1:] if host.startswith(".") else host
    return h == mien or h.endswith("." + mien)


def _han_giay(expiry: Any) -> float | None:
    if type(expiry) is not int:
        return None
    return expiry / 1000 if expiry >= NGUONG_MILI_GIAY else float(expiry)


def _sao_chep_tep_cookie(thu_muc_ho_so: Path, tam: Path) -> None:
    """Sao chép cookies.sqlite (+ -wal nếu có) sang `tam`. Mở thư mục hồ sơ rồi mở tệp TƯƠNG ĐỐI với nó, mọi bước
    O_NOFOLLOW: thư mục hay tệp bị thay bằng symlink (trình duyệt cùng uid làm được) không dắt sang hồ sơ khác."""
    fd_thu_muc = os.open(thu_muc_ho_so, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for ten in (TEN_TEP_COOKIE, TEN_TEP_COOKIE + "-wal"):
            try:
                fd_vao = mo_tep_thuong(ten, os.O_RDONLY, dir_fd=fd_thu_muc)
            except FileNotFoundError:
                if ten == TEN_TEP_COOKIE:
                    raise _KhongCoKhoCookie() from None
                continue
            except OSError:  # ELOOP (symlink), không phải tệp thường (FIFO, thư mục…)
                raise _KhongCoKhoCookie() from None
            try:
                if os.fstat(fd_vao).st_size > TRAN_TEP_COOKIE_BYTE:
                    raise _TepCookieQuaLon()
                fd_ra = os.open(tam / ten, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                try:
                    da_chep = 0
                    while khuc := os.read(fd_vao, 1 << 20):
                        da_chep += len(khuc)
                        if da_chep > TRAN_TEP_COOKIE_BYTE:  # tệp lớn lên trong lúc chép
                            raise _TepCookieQuaLon()
                        os.write(fd_ra, khuc)
                finally:
                    os.close(fd_ra)
            finally:
                os.close(fd_vao)
    finally:
        os.close(fd_thu_muc)


def _doc_cookie_tu_ban_sao(tep: Path, mien: str, bay_gio: float) -> tuple[str, int]:
    """Đọc BẢN SAO chỉ-đọc → chuỗi ``tên=giá trị; …`` và số cookie. Chỉ ngữ cảnh mặc định (originAttributes rỗng:
    không ngăn chứa/phân vùng); thứ tự như trình duyệt gửi (đường dài trước, tạo sớm trước); trùng tên giữ cookie
    đứng trước; cookie hết hạn hoặc có ký tự cấm bị bỏ; dừng ở trần số lượng/độ dài."""
    con = sqlite3.connect(f"{tep.as_uri()}?mode=ro", uri=True)
    try:
        hang = con.execute(
            "SELECT name, value, host, expiry FROM moz_cookies WHERE originAttributes = '' "
            "ORDER BY length(path) DESC, creationTime ASC, id ASC"
        ).fetchall()
    finally:
        con.close()
    cac_muc: list[str] = []
    da_co: set[str] = set()
    tong = 0
    for ten, gia_tri, host, expiry in hang:
        if not (isinstance(ten, str) and isinstance(gia_tri, str) and isinstance(host, str)):
            continue
        han = _han_giay(expiry)
        if han is None or han <= bay_gio or not _khop_mien(host, mien):
            continue
        if not MAU_TEN_COOKIE.fullmatch(ten) or not MAU_GIA_TRI_COOKIE.fullmatch(gia_tri) or ten in da_co:
            continue
        muc = f"{ten}={gia_tri}"
        if len(muc) > TRAN_DO_DAI_COOKIE:
            continue
        them = len(muc) + (2 if cac_muc else 0)
        if len(cac_muc) >= TRAN_SO_COOKIE or tong + them > TRAN_CHUOI_COOKIE:
            break
        cac_muc.append(muc)
        da_co.add(ten)
        tong += them
    return "; ".join(cac_muc), len(cac_muc)


def _xoa_thu_muc_tam(tam: Path) -> None:
    """Thư mục tạm do chính ta tạo (mkdtemp, 0700) chỉ chứa tệp phẳng (bản sao + -wal/-shm SQLite tự tạo)."""
    try:
        for tep in tam.iterdir():
            tep.unlink()
        tam.rmdir()
    except OSError as e:
        log.warning("Không xoá hết được thư mục tạm cookie (%s).", type(e).__name__)


#: Tên thư mục tạm đọc cookie do mkdtemp(prefix="w2a-cookie-") sinh.
MAU_THU_MUC_TAM_COOKIE = re.compile(r"w2a-cookie-[A-Za-z0-9_]{1,64}")


def don_thu_muc_tam_cookie_cu() -> int:
    """Lúc KHỞI ĐỘNG: xoá bản sao cookie sót lại khi tiến trình bị giết giữa lúc đọc (`finally` không chạy). Chỉ trong
    thư mục tạm của chính dịch vụ, chỉ tên đúng khuôn, chỉ thư mục THẬT (lstat — không theo symlink), 0700, cùng uid."""
    goc = Path(tempfile.gettempdir())
    so = 0
    try:
        cac_muc = list(goc.iterdir())
    except OSError:
        return 0
    for muc in cac_muc:
        if not MAU_THU_MUC_TAM_COOKIE.fullmatch(muc.name):
            continue
        try:
            tt = os.lstat(muc)
        except OSError:
            continue
        if stat.S_ISDIR(tt.st_mode) and tt.st_uid == os.getuid() and stat.S_IMODE(tt.st_mode) == 0o700:
            _xoa_thu_muc_tam(muc)
            so += 1
    if so:
        log.warning("Đã dọn %d thư mục tạm cookie sót lại từ lần chạy trước.", so)
    return so


def _doc_json_khong_theo(tep: Path) -> Any:
    """Đọc tệp JSON nhỏ trong hồ sơ, KHÔNG theo symlink (trình duyệt cùng uid đặt được symlink sang hồ sơ khác).
    Thiếu/symlink/hỏng → None."""
    try:
        fd = mo_tep_thuong(tep, os.O_RDONLY)
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as f:
            return json.loads(f.read(64 * 1024).decode("utf-8"))
    except (OSError, ValueError):
        return None


def _doc_loai_tren_dia(thu_muc: Path) -> str:
    """Loại trình duyệt ghi trong `.thong_tin.json`; thiếu/hỏng thì coi là chromium."""
    data = _doc_json_khong_theo(thu_muc / ".thong_tin.json")
    if isinstance(data, dict) and data.get("loai") in LOAI_HOP_LE:
        return data["loai"]
    return "chromium"


def _doc_ten_workspace(thu_muc: Path) -> str:
    """Tên hiển thị trong `.workspace.json`; thiếu/hỏng thì là chính ID (như c2a). Cắt ở trần độ dài."""
    data = _doc_json_khong_theo(thu_muc / TEN_TEP_WORKSPACE)
    ten = data.get("name") if isinstance(data, dict) else None
    return (ten if isinstance(ten, str) and ten.strip() else thu_muc.name)[:TRAN_TEN_WORKSPACE]


def _kiem_url_bat_dau(url: Any) -> str | None:
    """Trang bắt đầu của trình duyệt: http(s), có tên máy, không userinfo, chỉ ký tự ASCII in được không khoảng trắng
    (đi vào mảng đối số của tiến trình — không shell, nhưng vẫn không cho đối số trông như cờ). Rỗng → None."""
    if url is None or url == "":
        return None
    sai = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"URL trang bắt đầu cần là http(s), có tên máy, không chứa mật khẩu, tối đa {TRAN_URL_BAT_DAU} ký tự.",
    )
    if not isinstance(url, str) or len(url) > TRAN_URL_BAT_DAU or not re.fullmatch(r"[\x21-\x7e]+", url):
        raise sai
    try:
        u = urlsplit(url)
        if u.scheme not in ("http", "https") or not u.hostname or u.username is not None or u.password is not None:
            raise ValueError()
    except ValueError:
        raise sai from None
    return url


# ── Quản lý tiến trình ────────────────────────────────────────────────────────


class TienTrinhHandle:
    """Đại diện cho một tiến trình con đang chạy."""

    def dang_chay(self) -> bool:
        raise NotImplementedError

    async def dung(self) -> None:
        raise NotImplementedError


class TienTrinhThucTe(TienTrinhHandle):
    """Bọc tiến trình asyncio thực tế."""

    def __init__(self, proc: asyncio.subprocess.Process):
        self._proc = proc
        self.pid = proc.pid

    def dang_chay(self) -> bool:
        return self._proc.returncode is None

    async def dung(self) -> None:
        """Dừng CẢ NHÓM tiến trình (tiến trình chạy với start_new_session nên pgid = pid).

        Không dừng sớm khi tiến trình đầu đã chết: con cháu của nó (renderer, zygote của Chromium…)
        vẫn có thể sống trong nhóm và giữ cổng/display/thư mục hồ sơ. SIGTERM cả nhóm, chờ tối đa
        2 giây, rồi SIGKILL cả nhóm VÔ ĐIỀU KIỆN (nhóm đã trống thì bỏ qua).
        """
        nhom = self._proc.pid
        try:
            os.killpg(nhom, signal.SIGTERM)
        except ProcessLookupError:
            pass

        try:
            await asyncio.wait_for(self._proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

        try:
            os.killpg(nhom, signal.SIGKILL)
        except ProcessLookupError:
            pass

        try:
            await asyncio.wait_for(self._proc.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            log.warning("Tiến trình %d chưa thoát sau SIGKILL.", nhom)


class BoChayTienTrinh:
    """Bộ chạy tiến trình con, có thể thay thế bằng bộ giả trong kiểm thử."""

    async def chay(
        self,
        lenh: list[str],
        env: dict[str, str],
        cwd: str | None = None,
    ) -> TienTrinhHandle:
        raise NotImplementedError


class BoChayThucTe(BoChayTienTrinh):
    """Chạy tiến trình thật với asyncio.create_subprocess_exec (không shell=True)."""

    async def chay(
        self,
        lenh: list[str],
        env: dict[str, str],
        cwd: str | None = None,
    ) -> TienTrinhHandle:
        proc = await asyncio.create_subprocess_exec(
            *lenh,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            cwd=cwd,
            start_new_session=True,  # Nhóm tiến trình riêng để killpg dọn cả con cháu
        )
        return TienTrinhThucTe(proc)


# ── Kiểm tra kết nối mạng (phát hiện kết nối noVNC / websockify) ───────────────


class BoKiemTraKetNoi:
    """Đếm kết nối noVNC ĐÃ XÁC THỰC còn sống của một hồ sơ."""

    def dem_ket_noi(self, ho_so: "ThongTinHoSoMo") -> int:
        raise NotImplementedError


class BoKiemTraKetNoiDaXacThuc(BoKiemTraKetNoi):
    """Đếm dấu plugin xac_thuc_ws để lại (``<thư mục dấu>/<pid>`` chứa thời điểm bắt đầu của tiến trình).

    Không đếm kết nối TCP: kết nối treo chưa qua xác thực (gửi 1 byte rồi giữ socket) cũng ESTABLISHED và
    sẽ giữ hồ sơ sống tới hạn tuyệt đối. Dấu chỉ tính khi tiến trình ``pid`` còn sống, không phải xác sống,
    và có đúng thời điểm bắt đầu đã ghi (chống pid bị tái dùng). Dấu chết thì xoá.
    """

    def dem_ket_noi(self, ho_so: "ThongTinHoSoMo") -> int:
        thu_muc = ho_so.thu_muc_ket_noi
        try:
            cac_dau = list(thu_muc.iterdir()) if thu_muc is not None else []
        except OSError:
            return 0
        so = 0
        for dau in cac_dau:
            if dau.name.isdigit() and self._con_song(int(dau.name), dau):
                so += 1
            else:
                try:
                    dau.unlink()
                except OSError:
                    pass
        return so

    @staticmethod
    def _con_song(pid: int, dau: Path) -> bool:
        try:
            truong = Path(f"/proc/{pid}/stat").read_bytes().rsplit(b")", 1)[1].split()
            fd = mo_tep_thuong(dau, os.O_RDONLY)  # thư mục dấu nằm dưới gốc hồ sơ — trình duyệt ghi được
            with os.fdopen(fd, "rb") as f:
                return truong[0] != b"Z" and truong[19] == f.read(64).strip()
        except (OSError, IndexError):
            return False


# ── Thu dọn tiến trình xác sống ───────────────────────────────────────────────


def thu_don_xac_song(bo_qua: set[int] | frozenset[int] = frozenset()) -> list[int]:
    """Thu dọn (waitpid) con xác sống của tiến trình này mà asyncio không theo dõi.

    Trong container, server.py là PID 1 (không dùng `init: true`: tini sẽ là PID 1 và giữ nguyên khoá
    dịch vụ trong /proc/1/environ). Con cháu trình duyệt bị killpg trở thành mồ côi, được gán cho PID 1;
    không ai waitpid thì chúng thành xác sống tích dần tới pids_limit. ``bo_qua``: pid asyncio đang theo
    dõi (tiến trình của hồ sơ còn mở) — không đụng để asyncio vẫn đọc được mã thoát.
    """
    toi = os.getpid()
    da_thu: list[int] = []
    try:
        cac_muc = os.listdir("/proc")
    except OSError:
        return da_thu
    for ten in cac_muc:
        if not ten.isdigit() or int(ten) in bo_qua:
            continue
        try:
            with open(f"/proc/{ten}/stat", "rb") as f:
                truong = f.read().rsplit(b")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if len(truong) >= 2 and truong[0] == b"Z" and int(truong[1]) == toi:
            try:
                os.waitpid(int(ten), os.WNOHANG)
                da_thu.append(int(ten))
            except ChildProcessError:
                pass
    return da_thu


# ── Quản lý Hồ sơ Trình duyệt ─────────────────────────────────────────────────


@dataclass
class ThongTinHoSoMo:
    ho_so_id: str
    loai: str
    cong: int
    vnc_port: int
    display_num: int
    luc_mo: str
    lan_hoat_dong_cuoi: float
    tien_trinh: list[TienTrinhHandle]
    duong_dan: Path
    #: Mốc mở theo đồng hồ đơn điệu — để áp hạn tuyệt đối TRINH_DUYET_TOI_DA_PHUT.
    luc_mo_don_dieu: float = 0.0
    #: Bí mật websockify của LẦN mở này (ngẫu nhiên). Không log, không trả qua GET /ho-so.
    bi_mat: str = field(default="", repr=False)
    #: Thư mục dấu kết nối đã xác thực do plugin ghi.
    thu_muc_ket_noi: Path | None = None


class _LoiKhoiDong(Exception):
    """Một tiến trình của hồ sơ chết ngay sau khi dựng."""


class QuanLyHoSo:
    """Quản lý vòng đời các hồ sơ trình duyệt, cổng, màn hình Xvfb và dọn dẹp idle."""

    def __init__(
        self,
        cau_hinh: dict[str, Any] | None = None,
        bo_chay: BoChayTienTrinh | None = None,
        bo_kiem_tra: BoKiemTraKetNoi | None = None,
    ):
        self.cau_hinh = cau_hinh or lay_cau_hinh()
        self.thu_muc_goc: Path = self.cau_hinh["thu_muc_goc"]
        self.cong_tu: int = self.cau_hinh["cong_tu"]
        self.cong_den: int = self.cau_hinh["cong_den"]
        self.toi_da: int = self.cau_hinh["toi_da"]
        self.idle_phut: int = self.cau_hinh["idle_phut"]
        self.toi_da_ho_so: int = self.cau_hinh["toi_da_ho_so"]
        self.toi_da_phut: int = self.cau_hinh["toi_da_phut"]
        self.cho_khoi_dong_giay: float = self.cau_hinh["cho_khoi_dong_giay"]
        self.novnc_dir: str = self.cau_hinh["novnc_dir"]
        self.host_noi_bo: str = self.cau_hinh["host_noi_bo"]
        self.bo_chay: BoChayTienTrinh = bo_chay or BoChayThucTe()
        self.bo_kiem_tra: BoKiemTraKetNoi = bo_kiem_tra or BoKiemTraKetNoiDaXacThuc()
        #: Đồng hồ đơn điệu (thay được bằng đồng hồ giả trong kiểm thử).
        self.dong_ho = time.monotonic

        self.danh_sach_mo: dict[str, ThongTinHoSoMo] = {}
        self.khoa = asyncio.Lock()
        #: Con trỏ cấp cổng theo vòng: cổng vừa giải phóng không bị cấp lại ngay.
        self._con_tro_cong = 0

    # ── cổng, đĩa, tệp bí mật ──

    def _tim_cong_va_display_ranh(self) -> tuple[int, int, int]:
        """Cấp cổng theo VÒNG bắt đầu từ sau cổng cấp gần nhất, bỏ qua cổng đang dùng.

        Không tái dùng ngay cổng vừa giải phóng: vé/khách noVNC cũ của hồ sơ A không nối nhầm vào hồ sơ B.
        """
        so_cong = self.cong_den - self.cong_tu + 1
        cong_dang_dung = {h.cong for h in self.danh_sach_mo.values()}
        for i in range(so_cong):
            cong = self.cong_tu + (self._con_tro_cong + i) % so_cong
            if cong not in cong_dang_dung:
                self._con_tro_cong = (cong - self.cong_tu + 1) % so_cong
                # Display ảo bắt đầu từ 100 để tránh trùng display hệ thống :0
                display_num = 100 + (cong - self.cong_tu)
                vnc_port = 5900 + display_num
                return cong, display_num, vnc_port
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Hết cổng khả dụng trong dải cấp phát.",
        )

    def _dem_ho_so_tren_dia(self) -> int:
        goc = self.thu_muc_goc.resolve()
        if not goc.is_dir():
            return 0
        return sum(1 for m in goc.iterdir() if m.is_dir() and MAU_HO_SO.fullmatch(m.name))

    def _thu_muc_bi_mat(self) -> Path:
        thu_muc = self.thu_muc_goc.resolve() / TEN_THU_MUC_BI_MAT
        if thu_muc.is_symlink():
            raise RuntimeError("Thư mục bí mật websockify là symlink — từ chối dùng.")
        thu_muc.mkdir(mode=0o700, exist_ok=True)
        os.chmod(thu_muc, 0o700)
        return thu_muc

    def _ghi_tep_bi_mat(self, ho_so_id: str, bi_mat: str) -> tuple[Path, Path]:
        """Ghi ``w2a:<bí mật>`` vào tệp 0600 cho plugin websockify (không đưa bí mật lên dòng lệnh) và
        tạo thư mục dấu kết nối đã xác thực ``<tệp>.ket-noi`` (0700, làm mới)."""
        tep = self._thu_muc_bi_mat() / ho_so_id
        fd = mo_tep_thuong(tep, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, f"{TEN_NGUOI_DUNG_WS}:{bi_mat}".encode())
        finally:
            os.close(fd)
        thu_muc_dau = tep.with_name(tep.name + ".ket-noi")
        self._xoa_thu_muc_dau(thu_muc_dau)
        thu_muc_dau.mkdir(mode=0o700)
        return tep, thu_muc_dau

    def _xoa_tep_bi_mat(self, ho_so_id: str) -> None:
        tep = self.thu_muc_goc.resolve() / TEN_THU_MUC_BI_MAT / ho_so_id
        try:
            os.remove(tep)
        except FileNotFoundError:
            pass
        self._xoa_thu_muc_dau(tep.with_name(tep.name + ".ket-noi"))

    @staticmethod
    def _xoa_thu_muc_dau(thu_muc: Path) -> None:
        """Thư mục dấu chỉ chứa tệp phẳng nhỏ (một tệp mỗi kết nối): xoá từng tệp rồi xoá thư mục."""
        if thu_muc.is_symlink():
            thu_muc.unlink()
            return
        try:
            for tep in thu_muc.iterdir():
                tep.unlink(missing_ok=True)
            thu_muc.rmdir()
        except FileNotFoundError:
            pass

    def _dich(self, ho_so: ThongTinHoSoMo) -> str:
        """Địa chỉ nội bộ cho lõi, mang userinfo ``w2a:<bí mật>``. KHÔNG log giá trị này."""
        return f"http://{TEN_NGUOI_DUNG_WS}:{ho_so.bi_mat}@{self.host_noi_bo}:{ho_so.cong}"

    # ── dừng / dọn (gọi khi đang giữ self.khoa) ──

    @staticmethod
    async def _dung_bo(tien_trinh: list[TienTrinhHandle]) -> None:
        # Ngược thứ tự dựng: cắt websockify (lối vào) trước, Xvfb sau cùng.
        for p in reversed(tien_trinh):
            await p.dung()

    def _thu_don_xac_song(self) -> list[int]:
        dang_theo_doi = {
            pid for h in self.danh_sach_mo.values() for p in h.tien_trinh if (pid := getattr(p, "pid", None))
        }
        return thu_don_xac_song(dang_theo_doi)

    async def _dong_noi_bo(self, ho_so_id: str) -> ThongTinHoSoMo | None:
        """Bỏ hồ sơ khỏi danh sách, đợi cả bộ tiến trình chết, rồi xoá tệp bí mật."""
        ho_so_mo = self.danh_sach_mo.pop(ho_so_id, None)
        if ho_so_mo is not None:
            await self._dung_bo(ho_so_mo.tien_trinh)
            self._xoa_tep_bi_mat(ho_so_id)
            self._thu_don_xac_song()
        return ho_so_mo

    async def _don_ho_so_chet(self) -> list[str]:
        """Dọn hồ sơ có bất kỳ tiến trình nào đã chết — không để hồ sơ «ma» giữ chỗ và trả `dich` cũ."""
        chet = [i for i, h in self.danh_sach_mo.items() if not all(p.dang_chay() for p in h.tien_trinh)]
        for ho_so_id in chet:
            log.warning("Hồ sơ %s có tiến trình đã chết, dọn cả bộ.", ho_so_id)
            await self._dong_noi_bo(ho_so_id)
        return chet

    # ── vòng đời ──

    async def tao(self, ten: Any, loai: str = "firefox") -> dict[str, str]:
        """Tạo workspace: thư mục hồ sơ ID NGẪU NHIÊN (không suy từ tên/email) + `.workspace.json` ghi tên. Không mở
        trình duyệt."""
        if loai not in LOAI_HOP_LE:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Loại trình duyệt chỉ nhận 'chromium' hoặc 'firefox'.")
        ten = ten.strip() if isinstance(ten, str) else ""
        if not ten or len(ten) > TRAN_TEN_WORKSPACE or not ten.isprintable():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Nhập tên workspace (1–{TRAN_TEN_WORKSPACE} ký tự, không xuống dòng).")
        async with self.khoa:
            if self._dem_ho_so_tren_dia() >= self.toi_da_ho_so:
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                                    detail=f"Đã đạt trần {self.toi_da_ho_so} hồ sơ lưu trên đĩa.")
            ho_so_id = secrets.token_hex(12)
            self.thu_muc_goc.resolve().mkdir(parents=True, exist_ok=True)
            duong_dan = kiem_tra_duong_dan_ho_so(ho_so_id, self.thu_muc_goc)
            duong_dan.mkdir(mode=0o700)
            os.chmod(duong_dan, 0o700)
            for ten_tep, du_lieu in ((TEN_TEP_WORKSPACE, {"name": ten}), (".thong_tin.json", {"loai": loai})):
                fd = mo_tep_thuong(duong_dan / ten_tep, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(du_lieu, f, ensure_ascii=False)
        log.info("Đã tạo workspace %s.", ho_so_id)
        return {"ho_so": ho_so_id, "ten": ten}

    async def mo(self, ho_so_id: str, loai: str, url: Any = None) -> dict[str, str]:
        """Mở hồ sơ trình duyệt trên màn hình ảo và noVNC. `url` (tuỳ chọn, đã kiểm) là trang đầu khi trình duyệt
        KHỞI ĐỘNG; hồ sơ đang mở thì không đổi trang (không có cổng điều khiển trình duyệt)."""
        if loai not in LOAI_HOP_LE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Loại trình duyệt không hỗ trợ: '{loai}'. Chỉ chấp nhận 'chromium' hoặc 'firefox'.",
            )
        trang_dau = _kiem_url_bat_dau(url) or "about:blank"

        duong_dan = kiem_tra_duong_dan_ho_so(ho_so_id, self.thu_muc_goc)

        async with self.khoa:
            await self._don_ho_so_chet()

            # Hồ sơ đang mở VÀ cả bộ tiến trình còn sống: trả lại đích cũ, không mở trùng
            if ho_so_id in self.danh_sach_mo:
                ho_so_cu = self.danh_sach_mo[ho_so_id]
                ho_so_cu.lan_hoat_dong_cuoi = self.dong_ho()
                log.info("Hồ sơ %s đã mở từ trước, trả lại đích cũ (cổng %d).", ho_so_id, ho_so_cu.cong)
                return {"ho_so": ho_so_id, "loai": ho_so_cu.loai, "dich": self._dich(ho_so_cu)}

            # Kiểm tra trần số hồ sơ mở đồng thời
            if len(self.danh_sach_mo) >= self.toi_da:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Đã đạt trần {self.toi_da} hồ sơ mở đồng thời.",
                )

            # Trần số hồ sơ TRÊN ĐĨA: chỉ chặn tạo hồ sơ mới; hồ sơ đã có vẫn mở lại được
            if not duong_dan.exists() and self._dem_ho_so_tren_dia() >= self.toi_da_ho_so:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Đã đạt trần {self.toi_da_ho_so} hồ sơ lưu trên đĩa.",
                )

            # Cấp phát cổng và display
            cong, display_num, vnc_port = self._tim_cong_va_display_ranh()

            env_con = _loc_moi_truong_con(
                display=f":{display_num}", home=str(duong_dan)
            )
            # Bí mật NGẪU NHIÊN cho lần mở này: `dich` cũ lỡ lộ (log của lõi…) hết giá trị khi hồ sơ đóng.
            bi_mat = secrets.token_hex(32)
            #: Thư mục hồ sơ do CHÍNH lần mở này tạo — dựng hỏng thì xoá, không để hồ sơ mồ côi chiếm trần.
            tao_moi = not duong_dan.exists()

            cac_tien_trinh: list[TienTrinhHandle] = []
            try:
                # Tạo thư mục hồ sơ với quyền 0700
                duong_dan.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.chmod(duong_dan, 0o700)

                # Lưu metadata loại trình duyệt để khi đóng vẫn nhận diện được
                # O_NOFOLLOW: trình duyệt (cùng uid) có thể đã đặt tệp này thành symlink tới tệp của hồ sơ KHÁC — khi đó
                # ELOOP → dựng hỏng (502), không ghi xuyên sang hồ sơ khác.
                fd = mo_tep_thuong(duong_dan / ".thong_tin.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"loai": loai}, f)

                tep_bi_mat, thu_muc_dau = self._ghi_tep_bi_mat(ho_so_id, bi_mat)

                # 1. Xvfb màn hình ảo
                lenh_xvfb = [
                    self.cau_hinh["xvfb_bin"],
                    f":{display_num}",
                    "-screen",
                    "0",
                    "1280x800x24",
                    "-nolisten",
                    "tcp",
                ]
                cac_tien_trinh.append(await self.bo_chay.chay(lenh_xvfb, env_con))

                # 2. Trình duyệt riêng biệt theo profile
                if loai == "chromium":
                    # Container không có user namespace/setuid sandbox nên cần --no-sandbox.
                    # Hệ quả: các hồ sơ KHÔNG cô lập với nhau — ranh giới an ninh là container (xem README).
                    lenh_browser = [
                        self.cau_hinh["chromium_bin"],
                        f"--user-data-dir={duong_dan}",
                        "--no-sandbox",
                        "--no-first-run",
                        "--disable-gpu",
                        "--disable-dev-shm-usage",
                        "--window-size=1280,800",
                        trang_dau,
                    ]
                else:
                    lenh_browser = [
                        self.cau_hinh["firefox_bin"],
                        "--profile",
                        str(duong_dan),
                        "--no-remote",
                        "-width",
                        "1280",
                        "-height",
                        "800",
                        trang_dau,
                    ]
                cac_tien_trinh.append(await self.bo_chay.chay(lenh_browser, env_con, cwd=str(duong_dan)))

                # 3. x11vnc gắn vào :N, chỉ nghe 127.0.0.1, không mật khẩu (chỉ websockify cục bộ nối tới).
                #    -nevershared -dontdisconnect: một khách tại một thời điểm, khách thứ hai bị TỪ CHỐI
                #    (không đá người đang dùng); -forever: khách thoát thì vẫn chờ khách kế (tải lại trang).
                lenh_vnc = [
                    self.cau_hinh["x11vnc_bin"],
                    "-display",
                    f":{display_num}",
                    "-listen",
                    "127.0.0.1",
                    "-rfbport",
                    str(vnc_port),
                    "-nopw",
                    "-forever",
                    "-nevershared",
                    "-dontdisconnect",
                ]
                cac_tien_trinh.append(await self.bo_chay.chay(lenh_vnc, env_con))

                # 4. websockify phục vụ noVNC, đòi Basic auth bằng bí mật riêng của hồ sơ cho CẢ WebSocket
                #    lẫn trang tĩnh (--web-auth). Bí mật đọc từ tệp 0600 qua plugin xac_thuc_ws.
                env_ws = dict(env_con, PYTHONPATH=str(THU_MUC_MA), PYTHONDONTWRITEBYTECODE="1")
                lenh_ws = [
                    self.cau_hinh["websockify_bin"],
                    "--web",
                    self.novnc_dir,
                    "--web-auth",
                    "--auth-plugin",
                    "xac_thuc_ws.BasicTuTep",
                    "--auth-source",
                    str(tep_bi_mat),
                    str(cong),
                    f"127.0.0.1:{vnc_port}",
                ]
                cac_tien_trinh.append(await self.bo_chay.chay(lenh_ws, env_ws))

                # Chờ ngắn rồi kiểm cả bộ còn sống: tiến trình chết ngay (thiếu thư viện, display trùng,
                # plugin không đọc được tệp…) thì KHÔNG đăng ký hồ sơ.
                if self.cho_khoi_dong_giay > 0:
                    await asyncio.sleep(self.cho_khoi_dong_giay)
                if not all(p.dang_chay() for p in cac_tien_trinh):
                    raise _LoiKhoiDong()

            except BaseException as e:
                # Dọn sạch: giết cả nhóm mọi tiến trình đã dựng, xoá tệp bí mật; cổng/display tự được
                # giải phóng vì hồ sơ chưa vào danh_sach_mo.
                await self._dung_bo(cac_tien_trinh)
                self._xoa_tep_bi_mat(ho_so_id)
                if tao_moi:
                    await self._xoa_thu_muc_ho_so(duong_dan)
                if not isinstance(e, Exception):
                    raise
                log.warning("Không khởi động được hồ sơ %s (%s).", ho_so_id, type(e).__name__)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Không khởi động được trình duyệt cho hồ sơ này.",
                ) from None

            bay_gio = self.dong_ho()
            ho_so_mo = ThongTinHoSoMo(
                ho_so_id=ho_so_id,
                loai=loai,
                cong=cong,
                vnc_port=vnc_port,
                display_num=display_num,
                luc_mo=datetime.now(timezone.utc).isoformat(),
                lan_hoat_dong_cuoi=bay_gio,
                tien_trinh=cac_tien_trinh,
                duong_dan=duong_dan,
                luc_mo_don_dieu=bay_gio,
                bi_mat=bi_mat,
                thu_muc_ket_noi=thu_muc_dau,
            )
            self.danh_sach_mo[ho_so_id] = ho_so_mo

            log.info("Đã mở hồ sơ %s (%s) tại cổng %d.", ho_so_id, loai, cong)
            return {"ho_so": ho_so_id, "loai": loai, "dich": self._dich(ho_so_mo)}

    async def _xoa_thu_muc_ho_so(self, duong_dan: Path) -> None:
        """Xoá thư mục hồ sơ (đã kiểm bằng kiem_tra_duong_dan_ho_so) — chỉ thư mục con dưới gốc, ngoài event loop.
        Gọi SAU khi cả nhóm tiến trình đã chết (không còn ai ghi vào)."""
        goc_chuan = self.thu_muc_goc.resolve()
        if duong_dan.exists() and duong_dan != goc_chuan and goc_chuan in duong_dan.parents:
            await asyncio.to_thread(shutil.rmtree, duong_dan, ignore_errors=True)

    async def dong(self, ho_so_id: str, giu_phien: bool = True) -> None:
        """Đóng hồ sơ, dừng mọi tiến trình con và tuỳ chọn xoá thư mục."""
        duong_dan = kiem_tra_duong_dan_ho_so(ho_so_id, self.thu_muc_goc)

        async with self.khoa:
            if await self._dong_noi_bo(ho_so_id) is not None:
                log.info("Đã dừng các tiến trình của hồ sơ %s", ho_so_id)

            if not giu_phien:
                log.info("Xoá thư mục hồ sơ: %s", ho_so_id)
                await self._xoa_thu_muc_ho_so(duong_dan)

    async def danh_sach(self) -> list[dict[str, Any]]:
        """Lấy danh sách tất cả hồ sơ (cả đang mở lẫn đã đóng trên đĩa). KHÔNG trả `dich`/bí mật."""
        async with self.khoa:
            ket_qua: dict[str, dict[str, Any]] = {}

            # Quét các hồ sơ đã có trên đĩa
            goc = self.thu_muc_goc.resolve()
            if goc.exists() and goc.is_dir():
                for muc in goc.iterdir():
                    if muc.is_dir() and not muc.is_symlink() and MAU_HO_SO.fullmatch(muc.name):
                        ket_qua[muc.name] = {
                            "ho_so": muc.name,
                            "ten": _doc_ten_workspace(muc),
                            "loai": _doc_loai_tren_dia(muc),
                            "trang_thai": "dong",
                            "dang_mo": False,
                            "thu_cong": False,
                            "luc_mo": None,
                        }

            # Cập nhật các hồ sơ đang mở
            # Mọi lần mở ở dịch vụ này là THỦ CÔNG (người dùng qua noVNC) — `thu_cong` giữ cùng nghĩa với c2a.
            for id_mo, thong_tin in self.danh_sach_mo.items():
                ket_qua[id_mo] = {
                    "ho_so": id_mo,
                    "ten": _doc_ten_workspace(thong_tin.duong_dan),
                    "loai": thong_tin.loai,
                    "trang_thai": "dang_mo",
                    "dang_mo": True,
                    "thu_cong": True,
                    "luc_mo": thong_tin.luc_mo,
                }

            return sorted(ket_qua.values(), key=lambda x: x["ho_so"])

    async def doc_cookie(self, ho_so_id: str, mien: str) -> dict[str, Any]:
        """Cookie của `mien` (và tên miền con) trong hồ sơ Firefox, dạng ``tên=giá trị; …``. KHÔNG log giá trị."""
        if not isinstance(mien, str) or not MAU_MIEN.fullmatch(mien):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tên miền không hợp lệ (chữ thường, số, «-», «.»; ít nhất hai nhãn, ví dụ vi-du.example).",
            )
        duong_dan = kiem_tra_duong_dan_ho_so(ho_so_id, self.thu_muc_goc)
        khong_co = HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                 detail="Hồ sơ không có hoặc chưa có kho cookie.")
        tam = Path(tempfile.mkdtemp(prefix="w2a-cookie-"))  # 0700, tên ngẫu nhiên
        try:
            # Giữ khoá khi KIỂM và SAO CHÉP: `dong(giu_phien=False)` không xoá thư mục giữa chừng.
            async with self.khoa:
                # Chép cookies.sqlite rồi -wal KHÔNG nguyên tử khi trình duyệt đang ghi → chỉ đọc hồ sơ ĐÃ ĐÓNG.
                if ho_so_id in self.danh_sach_mo:
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                        detail="Hồ sơ đang mở — đóng (giữ phiên) rồi lấy cookie.")
                if not duong_dan.is_dir():
                    raise khong_co
                loai = _doc_loai_tren_dia(duong_dan)
                if loai != "firefox":
                    raise HTTPException(
                        status_code=status.HTTP_501_NOT_IMPLEMENTED,
                        detail="Chỉ đọc được cookie của hồ sơ Firefox (Chromium mã hoá cookie — không hỗ trợ).",
                    )
                try:
                    await asyncio.to_thread(_sao_chep_tep_cookie, duong_dan, tam)
                except _KhongCoKhoCookie:
                    raise khong_co from None
                except _TepCookieQuaLon:
                    raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                                        detail="Kho cookie của hồ sơ quá lớn.") from None
            try:
                chuoi, so = await asyncio.to_thread(_doc_cookie_tu_ban_sao, tam / TEN_TEP_COOKIE, mien, time.time())
            except sqlite3.Error:
                log.warning("Không đọc được kho cookie của hồ sơ %s.", ho_so_id)
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                    detail="Không đọc được kho cookie của hồ sơ.") from None
        finally:
            await asyncio.to_thread(_xoa_thu_muc_tam, tam)
        log.info("Đã đọc %d cookie của hồ sơ %s.", so, ho_so_id)
        return {"cookie": chuoi, "so_cookie": so}

    async def don_idle(self) -> list[str]:
        """Tự đóng (giữ phiên) hồ sơ: idle quá hạn và không ai kết nối, HOẶC quá hạn tuyệt đối, HOẶC đã chết."""
        async with self.khoa:
            # Con cháu đã bị SIGKILL ở lần dọn trước giờ mới thành xác sống: thu dọn định kỳ ở đây.
            self._thu_don_xac_song()
            can_dong = await self._don_ho_so_chet()
            bay_gio = self.dong_ho()
            nguong_idle_giay = self.idle_phut * 60
            nguong_tuyet_doi_giay = self.toi_da_phut * 60
            het_han: list[str] = []

            for ho_so_id, ho_so in list(self.danh_sach_mo.items()):
                if bay_gio - ho_so.luc_mo_don_dieu >= nguong_tuyet_doi_giay:
                    # Kết nối treo (hay khách quên đóng tab) không giữ hồ sơ sống mãi
                    het_han.append(ho_so_id)
                    continue
                so_ket_noi = self.bo_kiem_tra.dem_ket_noi(ho_so)
                if so_ket_noi > 0:
                    # Có phiên noVNC ĐÃ XÁC THỰC còn sống: bảo vệ khỏi dọn idle
                    ho_so.lan_hoat_dong_cuoi = bay_gio
                elif bay_gio - ho_so.lan_hoat_dong_cuoi >= nguong_idle_giay:
                    het_han.append(ho_so_id)

            for ho_so_id in het_han:
                log.info("Hồ sơ %s quá hạn idle/tuyệt đối, tự đóng (giữ phiên).", ho_so_id)
                await self._dong_noi_bo(ho_so_id)

            return can_dong + het_han

    async def dong_tat_ca(self) -> None:
        """Dừng tất cả tiến trình khi dịch vụ tắt."""
        async with self.khoa:
            for ho_so_id in list(self.danh_sach_mo):
                await self._dong_noi_bo(ho_so_id)


# ── Schemas & Xác thực ────────────────────────────────────────────────────────


class MoHoSoYeuCau(BaseModel):
    ho_so: str = Field(..., description="ID hồ sơ hex 16-64 ký tự")
    loai: str = Field(..., description="Loại trình duyệt: 'chromium' hoặc 'firefox'")
    url: str | None = Field(None, description="Trang đầu khi trình duyệt khởi động (http/https, tuỳ chọn)")


class TaoHoSoYeuCau(BaseModel):
    ten: Any = Field(..., description="Tên hiển thị 1-100 ký tự (kiểm ở QuanLyHoSo.tao, không phản chiếu)")
    loai: str = Field("firefox", description="Loại trình duyệt của workspace")


class DongHoSoYeuCau(BaseModel):
    giu_phien: bool = Field(True, description="Giữ lại dữ liệu hồ sơ hay xoá sạch")


#: Đường duy nhất không cần khoá (lý do ở đầu tệp).
DUONG_MO = frozenset({"/suc-khoe"})


class XacThucBearer:
    """Lớp ASGI xác thực Bearer cho MỌI yêu cầu HTTP/WebSocket, trừ DUONG_MO.

    Đặt ở tầng ASGI (không phải Depends) vì FastAPI đọc và phân tích thân JSON TRƯỚC khi chạy
    dependency: thân hỏng không khoá sẽ nhận 422 kèm phản chiếu thân thay vì 401. Ở đây khách không
    khoá dừng lại trước định tuyến, trước phân tích thân; đường mới thêm sau này mặc định được bảo vệ.
    """

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket") or scope.get("path") in DUONG_MO:
            await self.app(scope, receive, send)
            return

        if not _khoa_dich_vu:
            await self._tu_choi(scope, receive, send, 500, "Máy chủ chưa được cấu hình TRINH_DUYET_KHOA hợp lệ.")
            return

        tieu_de = b""
        for ten, gia_tri in scope.get("headers") or []:
            if ten == b"authorization":
                tieu_de = gia_tri
                break
        kieu, _, token = tieu_de.partition(b" ")
        if kieu.lower() != b"bearer" or not hmac.compare_digest(token.strip(), _khoa_dich_vu.encode("utf-8")):
            await self._tu_choi(scope, receive, send, 401, "Thiếu hoặc sai khoá: cần 'Authorization: Bearer <khoá>'.")
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _tu_choi(scope: dict, receive: Any, send: Any, ma: int, thong_bao: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        tieu_de = {"WWW-Authenticate": "Bearer"} if ma == 401 else None
        await JSONResponse({"detail": thong_bao}, status_code=ma, headers=tieu_de)(scope, receive, send)


# ── Vòng lặp dọn dẹp nền & Khởi động ──────────────────────────────────────────

quan_ly_toan_cuc: QuanLyHoSo | None = None


async def _vong_lap_don_idle(quan_ly: QuanLyHoSo) -> None:
    """Tác vụ nền chạy định kỳ kiểm tra và dọn dẹp các hồ sơ idle."""
    while True:
        try:
            await asyncio.sleep(30.0)
            await quan_ly.don_idle()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning("Lỗi trong vòng lặp dọn idle: %s", type(e).__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Nạp khoá (hoặc dùng khoá main() đã nạp); thiếu/yếu thì từ chối khởi động
    nap_khoa()
    don_thu_muc_tam_cookie_cu()

    global quan_ly_toan_cuc
    if quan_ly_toan_cuc is None:
        quan_ly_toan_cuc = QuanLyHoSo()

    task_idle = asyncio.create_task(_vong_lap_don_idle(quan_ly_toan_cuc))
    yield
    task_idle.cancel()
    try:
        await task_idle
    except asyncio.CancelledError:
        pass
    if quan_ly_toan_cuc is not None:
        await quan_ly_toan_cuc.dong_tat_ca()


app = FastAPI(
    title="web2api — Dịch vụ Trình duyệt Cô lập (noVNC)",
    version="0.1.0",
    lifespan=lifespan,
    # Tắt tài liệu tự sinh: mọi đường phải có xác thực, ngoại lệ duy nhất là /suc-khoe.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(XacThucBearer)


@app.get("/suc-khoe", response_model=None)
async def suc_khoe() -> dict[str, bool]:
    """Kiểm tra sức khoẻ dịch vụ — không cần khoá, không lộ thông tin."""
    return {"ok": True}


def _quan_ly() -> QuanLyHoSo:
    global quan_ly_toan_cuc
    if quan_ly_toan_cuc is None:
        quan_ly_toan_cuc = QuanLyHoSo()
    return quan_ly_toan_cuc


@app.get("/ho-so")
async def danh_sach_ho_so() -> list[dict[str, Any]]:
    """Lấy danh sách các hồ sơ trình duyệt (không có `dich`/bí mật)."""
    return await _quan_ly().danh_sach()


@app.post("/ho-so")
async def tao_ho_so(req: TaoHoSoYeuCau) -> dict[str, str]:
    """Tạo workspace (thư mục hồ sơ ID ngẫu nhiên + tên hiển thị). Không mở trình duyệt."""
    return await _quan_ly().tao(req.ten, req.loai)


@app.post("/ho-so/mo")
async def mo_ho_so(req: MoHoSoYeuCau) -> dict[str, str]:
    """Mở một hồ sơ trình duyệt trên màn hình ảo và noVNC. `dich` mang bí mật — chỉ đường này trả."""
    return await _quan_ly().mo(req.ho_so, req.loai, req.url)


@app.get("/ho-so/{id}/cookie")
async def cookie_ho_so(id: str, mien: str = "") -> dict[str, Any]:
    """Cookie của `mien` trong hồ sơ Firefox — đường DUY NHẤT trả cookie (GET /ho-so không trả)."""
    return await _quan_ly().doc_cookie(id, mien)


@app.post("/ho-so/{id}/dong")
async def dong_ho_so(id: str, req: DongHoSoYeuCau | None = None) -> dict[str, bool]:
    """Đóng hồ sơ trình duyệt và dừng các tiến trình."""
    giu_phien = True if req is None else req.giu_phien
    await _quan_ly().dong(id, giu_phien=giu_phien)
    return {"ok": True, "session_preserved": giu_phien}


def main() -> None:
    import uvicorn

    try:
        # Đọc khoá một lần vào bộ nhớ, xoá dấu khỏi /proc/<pid>/environ, gỡ khỏi os.environ
        nap_khoa(xoa_dau=True)
    except Exception as e:
        sys.stderr.write(f"LỖI KHỞI ĐỘNG: {e}\n")
        sys.exit(1)

    cfg = lay_cau_hinh()
    # access_log=False: dòng truy cập chứa đường dẫn (ID hồ sơ) — không ghi.
    uvicorn.run(app, host=cfg["host"], port=cfg["port"], log_level="info", access_log=False)


if __name__ == "__main__":
    main()
