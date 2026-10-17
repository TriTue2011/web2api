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
import ipaddress
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

from marionette import LoiMarionette

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

# ── Điều khiển tự động qua Marionette (NV92 P1, Cách B) ────────────────────────
#: Dải cổng Marionette trên 127.0.0.1 — mỗi hồ sơ Firefox một cổng, do dịch vụ
#: cấp phát. Firefox KHÔNG có cờ dòng lệnh đổi cổng (đã kiểm --help ở Firefox
#: 158: chỉ có `--marionette`), nên cổng được đặt qua `user.js` của hồ sơ
#: (`marionette.port`) + cờ `--marionette` khi khởi động.
#: Trần độ dài bộ chọn CSS / văn bản điền / số quy tắc tín hiệu / tên tín hiệu.
TRAN_BO_CHON = 1000
TRAN_VAN_BAN = 100_000
TRAN_QUY_TAC = 10
TRAN_TEN_TIN_HIEU = 64
#: Các loại quy tắc «dấu hiệu» mà lời gọi /tin-hieu được phép gửi (dữ liệu do
#: module cung cấp — dịch vụ không biết tên nhà cung cấp nào).
LOAI_QUY_TAC = frozenset({"url_chua", "tieu_de_chua", "bo_chon", "cookie"})
#: Mẫu tên tín hiệu (mã mở, do module đặt — vd "da_dang_nhap", "can_nguoi").
MAU_TEN_TIN_HIEU = re.compile(r"[a-z0-9_]{1,64}\Z")
#: Script nhận tín hiệu CHUNG bằng cấu trúc DOM (mã mở — không né phát hiện):
#: iframe của các tiện ích captcha phổ biến, tiêu đề thử thách Cloudflare,
#: ô nhập mã xác minh một lần. Không che cờ navigator.webdriver.
SCRIPT_TIN_HIEU = """(() => {
  const ra = [];
  try {
    const srcs = Array.from(document.querySelectorAll('iframe'),
                            f => f.src || '').join('\\n');
    if (/recaptcha|hcaptcha|challenges\\.cloudflare|turnstile/i.test(srcs))
      ra.push('captcha');
  } catch (e) {}
  try {
    if (/^\\s*just a moment\\s*$/i.test(document.title)) ra.push('captcha');
  } catch (e) {}
  try {
    if (document.querySelector(
          'input[type="tel"], input[autocomplete="one-time-code"]'))
      ra.push('2fa');
  } catch (e) {}
  return ra;
})()"""


def _tao_marionette(cong: int):
    """Dựng máy khách Marionette cho cổng đã cấp phát (NV92 P1).

    Điểm vá cho kiểm thử: test monkeypatch ``server._tao_marionette`` bằng
    máy khách giả, không cần Firefox thật.
    """
    from marionette import MarionetteClient
    return MarionetteClient(port=cong)


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
        # NV92 P1: dải cổng Marionette trên 127.0.0.1 cho hồ sơ Firefox.
        "marionette_tu": _so("TRINH_DUYET_MARIONETTE_TU", 32000),
        "marionette_den": _so("TRINH_DUYET_MARIONETTE_DEN", 32099),
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
    #: Cổng Marionette trên 127.0.0.1 của hồ sơ Firefox (None với Chromium).
    cong_marionette: int | None = None
    #: Máy khách Marionette đã nối — chỉ tồn tại khi dang_ban (không repr).
    marionette: Any = field(default=None, repr=False)
    #: Cờ «đang bận»: True khi máy đang giữ quyền điều khiển — người không mở
    #: noVNC cùng lúc và máy khác không giành được (mở/giành lúc đang bận → 409).
    dang_ban: bool = False


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
        #: Dải cổng Marionette trên 127.0.0.1 (NV92 P1, Cách B).
        self.marionette_tu: int = self.cau_hinh["marionette_tu"]
        self.marionette_den: int = self.cau_hinh["marionette_den"]
        #: Cổng Marionette đang cấp cho các hồ sơ đang mở.
        self._cong_marionette_dang_dung: set[int] = set()
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

    # ── Điều khiển tự động qua Marionette (NV92 P1, Cách B) ──

    def _cap_cong_marionette(self) -> int:
        """Cấp cổng Marionette rảnh trên 127.0.0.1 cho một hồ sơ Firefox."""
        for cong in range(self.marionette_tu, self.marionette_den + 1):
            if cong not in self._cong_marionette_dang_dung:
                self._cong_marionette_dang_dung.add(cong)
                return cong
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Hết cổng Marionette khả dụng trong dải cấp phát.",
        )

    def _nha_cong_marionette(self, cong: int | None) -> None:
        if cong is not None:
            self._cong_marionette_dang_dung.discard(cong)

    @staticmethod
    def _ghi_user_js_marionette(duong_dan: Path, cong: int) -> None:
        """Ghi cổng Marionette vào ``user.js`` của hồ sơ (giữ lại pref khác đã có).

        Firefox không có cờ dòng lệnh đổi cổng Marionette (đã kiểm --help ở
        Firefox 158) nên cổng đi qua pref ``marionette.port``; cờ ``--marionette``
        vẫn được thêm ở dòng lệnh khi khởi động để bật rõ ràng.
        """
        tep = duong_dan / "user.js"
        try:
            dong_cu = tep.read_text(encoding="utf-8").splitlines()
        except OSError:
            dong_cu = []
        giu = [d for d in dong_cu if 'user_pref("marionette.' not in d]
        giu.append('user_pref("marionette.enabled", true);')
        giu.append(f'user_pref("marionette.port", {int(cong)});')
        fd = mo_tep_thuong(tep, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(giu) + "\n")

    async def _dong_marionette_noi_bo(self, ho_so_mo: ThongTinHoSoMo) -> None:
        """Ngắt máy khách Marionette và nhả cờ bận (không ném)."""
        khach = ho_so_mo.marionette
        ho_so_mo.marionette = None
        ho_so_mo.dang_ban = False
        if khach is not None:
            for fn in (khach.xoa_phien, khach.dong):
                try:
                    await asyncio.to_thread(fn)
                except Exception:
                    pass

    def _lay_dieu_khien(self, ho_so_id: str) -> tuple[ThongTinHoSoMo, Any]:
        """Hồ sơ + máy khách Marionette đã giành; chưa giành → 409, chưa mở → 404."""
        kiem_tra_duong_dan_ho_so(ho_so_id, self.thu_muc_goc)
        ho_so_mo = self.danh_sach_mo.get(ho_so_id)
        if ho_so_mo is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không thấy hồ sơ đang mở: '{ho_so_id}'.",
            )
        if (ho_so_mo.loai != "firefox" or ho_so_mo.marionette is None
                or not ho_so_mo.dang_ban):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Chưa giành quyền điều khiển máy cho hồ sơ này — "
                       "gọi /dieu-khien/gianh trước.",
            )
        return ho_so_mo, ho_so_mo.marionette

    async def _goi_marionette(self, ho_so_mo: ThongTinHoSoMo,
                              fn: Any, *doi_so: Any) -> Any:
        """Gọi máy khách (blocking) trong to_thread.

        Lỗi giao thức → 502 chỉ mang MÃ lỗi (không nội dung trang); lỗi lạ →
        ĐÓNG CỬA: ngắt máy khách + nhả quyền rồi 500.
        """
        try:
            return await asyncio.to_thread(fn, *doi_so)
        except LoiMarionette as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=e.ma_loi,
            ) from None
        except Exception:
            log.warning("Điều khiển hồ sơ %s lỗi lạ — đóng cửa, nhả quyền.",
                        ho_so_mo.ho_so_id)
            await self._dong_marionette_noi_bo(ho_so_mo)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="loi_dieu_khien",
            ) from None

    @staticmethod
    def _loai_ho_so_tren_dia(ho_so_id: str, thu_muc_goc: Path) -> str | None:
        """Loại trình duyệt của hồ sơ trên đĩa (đọc ``.thong_tin.json``).

        Không thấy/không đọc được → None. Không ném (đường gọi tự quyết 404).
        """
        try:
            du_lieu = json.loads(
                (thu_muc_goc / ho_so_id / ".thong_tin.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        loai = du_lieu.get("loai") if isinstance(du_lieu, dict) else None
        return loai if loai in LOAI_HOP_LE else None

    async def gianh_dieu_khien(self, ho_so_id: str) -> dict[str, bool]:
        """Máy giành quyền điều khiển hồ sơ Firefox (NV92 P1, NV94-a).

        NV94-a: hồ sơ Firefox tồn tại trên đĩa nhưng chưa mở → TỰ MỞ rồi giành
        (đường /api/danh-tinh/dang-nhap, /tai-dung, /ma-2fa không phải mở tay
        trước). Hồ sơ Chromium/không tồn tại → 404 như cũ.

        501 hồ sơ Chromium đang mở; 409 đang bận hoặc đang có người dùng noVNC;
        502 nối Marionette hỏng. Người và máy KHÔNG dùng chung hồ sơ cùng lúc.

        NV94-P3: không giữ khoá toàn cục trong lúc nối Marionette (tới 40 s);
        nhánh lỗi nào cũng đóng socket trước khi trả lỗi.
        """
        kiem_tra_duong_dan_ho_so(ho_so_id, self.thu_muc_goc)

        async def _trang_thai() -> ThongTinHoSoMo | None:
            async with self.khoa:
                return self.danh_sach_mo.get(ho_so_id)

        ho_so_mo = await _trang_thai()
        if ho_so_mo is None:
            # NV94-a: tự mở nếu là hồ sơ Firefox trên đĩa.
            if self._loai_ho_so_tren_dia(ho_so_id, self.thu_muc_goc) != "firefox":
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Không thấy hồ sơ đang mở: '{ho_so_id}'.",
                )
            await self.mo(ho_so_id, "firefox")
            ho_so_mo = await _trang_thai()
            if ho_so_mo is None:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Tự mở hồ sơ không thành công.",
                )
        async with self.khoa:
            if ho_so_mo.loai != "firefox":
                raise HTTPException(
                    status_code=status.HTTP_501_NOT_IMPLEMENTED,
                    detail="Điều khiển tự động chỉ hỗ trợ hồ sơ Firefox.",
                )
            if ho_so_mo.dang_ban:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Hồ sơ đang được máy điều khiển (lượt khác) — "
                           "nhả quyền rồi giành lại.",
                )
            if self.bo_kiem_tra.dem_ket_noi(ho_so_mo) > 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Đang có người dùng noVNC trong hồ sơ — người và "
                           "máy không điều khiển cùng lúc.",
                )
            if ho_so_mo.cong_marionette is None:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Hồ sơ mở trước khi có điều khiển tự động — "
                           "đóng rồi mở lại.",
                )
            cong = ho_so_mo.cong_marionette
        # Nối Marionette NGOÀI khoá toàn cục (NV94-P3): nối có thể treo tới
        # 40 s, giữ khoá sẽ chặn mọi đường khác của dịch vụ.
        khach = _tao_marionette(cong)
        try:
            await asyncio.to_thread(khach.ket_noi)
            await asyncio.to_thread(khach.phien_moi)
        except LoiMarionette as e:
            await self._dong_khach_an_toan(khach)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=e.ma_loi,
            ) from None
        except Exception:
            await self._dong_khach_an_toan(khach)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="loi_noi_dieu_khien",
            ) from None
        async with self.khoa:
            # Nối xong mới giành: có lượt khác chen giữa lúc nối thì 409.
            if ho_so_mo.dang_ban:
                await self._dong_khach_an_toan(khach)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Hồ sơ vừa bị lượt khác giành trong lúc nối — "
                           "nhả quyền rồi giành lại.",
                )
            ho_so_mo.marionette = khach
            ho_so_mo.dang_ban = True
            ho_so_mo.lan_hoat_dong_cuoi = self.dong_ho()
            log.info("Đã giành quyền điều khiển máy cho hồ sơ %s", ho_so_id)
            return {"ok": True}

    @staticmethod
    async def _dong_khach_an_toan(khach: Any) -> None:
        """Đóng socket máy khách Marionette, không ném (mọi nhánh lỗi)."""
        try:
            await asyncio.to_thread(khach.dong)
        except Exception:
            pass

    async def nha_dieu_khien(self, ho_so_id: str) -> dict[str, bool]:
        """Nhả quyền điều khiển máy (luỹ đẳng)."""
        kiem_tra_duong_dan_ho_so(ho_so_id, self.thu_muc_goc)
        async with self.khoa:
            ho_so_mo = self.danh_sach_mo.get(ho_so_id)
            if ho_so_mo is not None:
                await self._dong_marionette_noi_bo(ho_so_mo)
            log.info("Đã nhả quyền điều khiển máy cho hồ sơ %s", ho_so_id)
            return {"ok": True}

    @staticmethod
    def _kiem_bo_chon(bo_chon: Any) -> str:
        if not isinstance(bo_chon, str) or not 1 <= len(bo_chon) <= TRAN_BO_CHON:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="«bo_chon» phải là chuỗi CSS 1-1000 ký tự.",
            )
        return bo_chon

    @staticmethod
    def _kiem_quy_tac(quy_tac: Any) -> list[dict[str, Any]]:
        """Kiểm cấu trúc quy tắc tín hiệu (dữ liệu do module cung cấp)."""
        if not isinstance(quy_tac, list) or len(quy_tac) > TRAN_QUY_TAC:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="«quy_tac» phải là danh sách tối đa 10 quy tắc.",
            )
        for q in quy_tac:
            if not isinstance(q, dict):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail="Mỗi quy tắc phải là một đối tượng.")
            if q.get("loai") not in LOAI_QUY_TAC:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"«loai» quy tắc không hợp lệ: {q.get('loai')!r}.",
                )
            ten = q.get("tin_hieu")
            if not isinstance(ten, str) or not MAU_TEN_TIN_HIEU.fullmatch(ten):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="«tin_hieu» phải khớp [a-z0-9_]{1,64}.",
                )
            loai = q["loai"]
            if loai in ("url_chua", "tieu_de_chua"):
                gt = q.get("gia_tri")
                if not isinstance(gt, str) or not 1 <= len(gt) <= 500:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="«gia_tri» phải là chuỗi 1-500 ký tự.",
                    )
            elif loai == "bo_chon":
                QuanLyHoSo._kiem_bo_chon(q.get("bo_chon"))
            else:  # cookie
                mien = q.get("mien")
                ten_ck = q.get("ten")
                if not isinstance(mien, str) or not 1 <= len(mien) <= 253:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="«mien» phải là chuỗi 1-253 ký tự.",
                    )
                if not isinstance(ten_ck, str) or not 1 <= len(ten_ck) <= 100:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="«ten» cookie phải là chuỗi 1-100 ký tự.",
                    )
        return quy_tac

    async def dieu_huong(self, ho_so_id: str, url: Any) -> dict[str, bool]:
        ho_so_mo, khach = self._lay_dieu_khien(ho_so_id)
        url_sach = _kiem_url_bat_dau(url)
        if url_sach is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="«url» phải là http/https hợp lệ, không trỏ metadata/LAN.",
            )
        # Chặn IP chữ (metadata/LAN): máy điều khiển không được lái trình duyệt
        # vào dịch vụ nội bộ. Tên máy chữ để Firefox tự phân giải.
        try:
            ten_may = urlsplit(url_sach).hostname or ""
            ip = ipaddress.ip_address(ten_may)
        except ValueError:
            ip = None
        if ip is not None and not ip.is_global:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="«url» không được trỏ địa chỉ IP nội bộ/metadata.",
            )
        await self._goi_marionette(ho_so_mo, khach.dieu_huong, url_sach)
        return {"ok": True}

    async def dien_vao(self, ho_so_id: str, bo_chon: Any, van_ban: Any) -> dict[str, bool]:
        bo_chon = self._kiem_bo_chon(bo_chon)
        if not isinstance(van_ban, str) or len(van_ban) > TRAN_VAN_BAN:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="«van_ban» phải là chuỗi tối đa 100000 ký tự.",
            )
        ho_so_mo, khach = self._lay_dieu_khien(ho_so_id)
        await self._goi_marionette(ho_so_mo, khach.dien, bo_chon, van_ban)
        # Giá trị điền KHÔNG bao giờ vào log.
        log.info("dien: hồ sơ %s, bộ chọn %d ký tự", ho_so_id, len(bo_chon))
        return {"ok": True}

    async def bam_vao(self, ho_so_id: str, bo_chon: Any) -> dict[str, bool]:
        bo_chon = self._kiem_bo_chon(bo_chon)
        ho_so_mo, khach = self._lay_dieu_khien(ho_so_id)
        await self._goi_marionette(ho_so_mo, khach.bam, bo_chon)
        return {"ok": True}

    async def cho_phan_tu(self, ho_so_id: str, bo_chon: Any, giay: Any) -> dict[str, bool]:
        bo_chon = self._kiem_bo_chon(bo_chon)
        try:
            giay_f = float(giay)
        except (TypeError, ValueError):
            giay_f = -1.0
        if not 0 <= giay_f <= 60:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="«giay» phải trong khoảng 0-60.",
            )
        ho_so_mo, khach = self._lay_dieu_khien(ho_so_id)
        thay = await self._goi_marionette(ho_so_mo, khach.cho_phan_tu, bo_chon, giay_f)
        return {"thay": bool(thay)}

    async def doc_tin_hieu(self, ho_so_id: str, quy_tac: Any) -> dict[str, list[str]]:
        """Tín hiệu = script cấu trúc chung (mã mở) ∪ quy tắc của module."""
        quy_tac = self._kiem_quy_tac(quy_tac)
        ho_so_mo, khach = self._lay_dieu_khien(ho_so_id)
        ra: set[str] = set()
        script_ra = await self._goi_marionette(ho_so_mo, khach.thuc_thi, SCRIPT_TIN_HIEU, [])
        for s in script_ra or []:
            if isinstance(s, str) and MAU_TEN_TIN_HIEU.fullmatch(s):
                ra.add(s)
        url = await self._goi_marionette(ho_so_mo, khach.url_hien_tai)
        tieu_de = await self._goi_marionette(ho_so_mo, khach.tieu_de)
        url = url if isinstance(url, str) else ""
        tieu_de = tieu_de if isinstance(tieu_de, str) else ""
        for q in quy_tac:
            loai = q["loai"]
            if loai == "url_chua":
                if q["gia_tri"] in url:
                    ra.add(q["tin_hieu"])
            elif loai == "tieu_de_chua":
                if q["gia_tri"] in tieu_de:
                    ra.add(q["tin_hieu"])
            elif loai == "bo_chon":
                if await self._goi_marionette(ho_so_mo, khach.cho_phan_tu,
                                              q["bo_chon"], 0.5):
                    ra.add(q["tin_hieu"])
            else:  # cookie
                cookies = await self._goi_marionette(ho_so_mo, khach.cookie) or []
                for c in cookies:
                    if (isinstance(c, dict)
                            and _khop_mien(str(c.get("domain") or ""), q["mien"])
                            and c.get("name") == q["ten"]):
                        ra.add(q["tin_hieu"])
                        break
        return {"tin_hieu": sorted(ra)}

    async def doc_cookie_truc_tiep(self, ho_so_id: str, mien: Any) -> dict[str, Any]:
        """Cookie từ phiên Marionette (nhanh, cho máy) — lọc theo miền, có trần."""
        if not isinstance(mien, str) or not 1 <= len(mien) <= 253:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="«mien» phải là chuỗi 1-253 ký tự.",
            )
        ho_so_mo, khach = self._lay_dieu_khien(ho_so_id)
        cookies = await self._goi_marionette(ho_so_mo, khach.cookie) or []
        chon: list[tuple[str, str]] = []
        for c in cookies:
            if not isinstance(c, dict):
                continue
            ten = c.get("name")
            gt = c.get("value")
            if not isinstance(ten, str) or not MAU_TEN_COOKIE.fullmatch(ten):
                continue
            if not isinstance(gt, str) or not MAU_GIA_TRI_COOKIE.fullmatch(gt):
                continue
            if _khop_mien(str(c.get("domain") or ""), mien):
                chon.append((ten, gt))
            if len(chon) >= TRAN_SO_COOKIE:
                break
        chuoi = "; ".join(f"{t}={g}" for t, g in chon)
        if len(chuoi) > TRAN_CHUOI_COOKIE:
            chuoi = chuoi[:TRAN_CHUOI_COOKIE]
        return {"cookie": chuoi, "so_cookie": len(chon)}

    async def chup_trang_thai(self, ho_so_id: str) -> dict[str, Any]:
        """Trạng thái trang để chẩn đoán: url + tiêu đề + tín hiệu (KHÔNG ảnh)."""
        ho_so_mo, khach = self._lay_dieu_khien(ho_so_id)
        url = await self._goi_marionette(ho_so_mo, khach.url_hien_tai)
        tieu_de = await self._goi_marionette(ho_so_mo, khach.tieu_de)
        ra = await self._goi_marionette(ho_so_mo, khach.thuc_thi, SCRIPT_TIN_HIEU, [])
        tin_hieu = sorted(s for s in (ra or [])
                          if isinstance(s, str) and MAU_TEN_TIN_HIEU.fullmatch(s))
        return {
            "url": url if isinstance(url, str) else "",
            "tieu_de": tieu_de if isinstance(tieu_de, str) else "",
            "tin_hieu": tin_hieu,
        }

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
            # NV92 P1: ngắt máy khách điều khiển máy (nếu còn) và trả cổng Marionette.
            await self._dong_marionette_noi_bo(ho_so_mo)
            self._nha_cong_marionette(ho_so_mo.cong_marionette)
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

            # NV92 P1: hồ sơ đang được MÁY điều khiển thì người không mở noVNC
            # cùng lúc — lỗi rõ ràng, không làm hỏng hồ sơ.
            ho_so_dang_mo = self.danh_sach_mo.get(ho_so_id)
            if ho_so_dang_mo is not None and ho_so_dang_mo.dang_ban:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Hồ sơ đang được máy điều khiển tự động — hãy đợi lượt "
                           "chạy xong (hoặc dừng nó) rồi mở noVNC.",
                )

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
            #: NV92 P1: cổng Marionette riêng cho hồ sơ Firefox (Chromium: None).
            cong_marionette: int | None = None
            if loai == "firefox":
                cong_marionette = self._cap_cong_marionette()

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
                    # NV92 P1: bật máy chủ điều khiển Marionette trên 127.0.0.1
                    # (cổng qua user.js — xem _ghi_user_js_marionette).
                    self._ghi_user_js_marionette(duong_dan, cong_marionette)
                    lenh_browser = [
                        self.cau_hinh["firefox_bin"],
                        "--profile",
                        str(duong_dan),
                        "--no-remote",
                        "--marionette",
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
                self._nha_cong_marionette(cong_marionette)
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
                cong_marionette=cong_marionette,
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


# ── Điều khiển tự động qua Marionette (NV92 P1, Cách B) ──


class DieuHuongYeuCau(BaseModel):
    url: Any = Field(..., description="URL http/https cần điều hướng tới")


class DienYeuCau(BaseModel):
    bo_chon: Any = Field(..., description="Bộ chọn CSS 1-1000 ký tự")
    van_ban: Any = Field(..., description="Văn bản điền (tối đa 100000 ký tự, không vào log)")


class BamYeuCau(BaseModel):
    bo_chon: Any = Field(..., description="Bộ chọn CSS 1-1000 ký tự")


class ChoYeuCau(BaseModel):
    bo_chon: Any = Field(..., description="Bộ chọn CSS 1-1000 ký tự")
    giay: Any = Field(10, description="Hết giờ chờ, 0-60 giây")


class TinHieuYeuCau(BaseModel):
    quy_tac: Any = Field(..., description="Danh sách tối đa 10 quy tắc {loai, tin_hieu, ...}")


class CookieTrucTiepYeuCau(BaseModel):
    mien: Any = Field(..., description="Miền cần lọc cookie (1-253 ký tự)")


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


# ── Điều khiển tự động qua Marionette (NV92 P1, Cách B) ──
# Mọi đường dưới đây đều qua xác thực Bearer <redacted> dịch vụ (middleware toàn cục).
# Người và máy KHÔNG dùng chung hồ sơ cùng lúc (cờ đang-bận → 409).


@app.post("/ho-so/{id}/dieu-khien/gianh")
async def gianh_dieu_khien(id: str) -> dict[str, bool]:
    """Máy giành quyền điều khiển hồ sơ Firefox đã mở."""
    return await _quan_ly().gianh_dieu_khien(id)


@app.post("/ho-so/{id}/dieu-khien/nha")
async def nha_dieu_khien(id: str) -> dict[str, bool]:
    """Nhả quyền điều khiển máy (luỹ đẳng)."""
    return await _quan_ly().nha_dieu_khien(id)


@app.post("/ho-so/{id}/dieu-huong")
async def dieu_huong(id: str, req: DieuHuongYeuCau) -> dict[str, bool]:
    """Điều hướng trang trong hồ sơ (đã giành quyền)."""
    return await _quan_ly().dieu_huong(id, req.url)


@app.post("/ho-so/{id}/dien")
async def dien_vao(id: str, req: DienYeuCau) -> dict[str, bool]:
    """Điền văn bản vào ô (đã giành quyền). Giá trị KHÔNG vào log."""
    return await _quan_ly().dien_vao(id, req.bo_chon, req.van_ban)


@app.post("/ho-so/{id}/bam")
async def bam_vao(id: str, req: BamYeuCau) -> dict[str, bool]:
    """Bấm phần tử (đã giành quyền)."""
    return await _quan_ly().bam_vao(id, req.bo_chon)


@app.post("/ho-so/{id}/cho")
async def cho_phan_tu(id: str, req: ChoYeuCau) -> dict[str, bool]:
    """Chờ phần tử xuất hiện (đã giành quyền)."""
    return await _quan_ly().cho_phan_tu(id, req.bo_chon, req.giay)


@app.post("/ho-so/{id}/tin-hieu")
async def doc_tin_hieu(id: str, req: TinHieuYeuCau) -> dict[str, list[str]]:
    """Đọc tín hiệu trang: script cấu trúc chung (mã mở) ∪ quy tắc của module."""
    return await _quan_ly().doc_tin_hieu(id, req.quy_tac)


@app.post("/ho-so/{id}/cookie-truc-tiep")
async def cookie_truc_tiep(id: str, req: CookieTrucTiepYeuCau) -> dict[str, Any]:
    """Cookie từ phiên Marionette, lọc theo miền (đã giành quyền)."""
    return await _quan_ly().doc_cookie_truc_tiep(id, req.mien)


@app.post("/ho-so/{id}/chup-trang-thai")
async def chup_trang_thai(id: str) -> dict[str, Any]:
    """Trạng thái trang để chẩn đoán: url + tiêu đề + tín hiệu (KHÔNG ảnh)."""
    return await _quan_ly().chup_trang_thai(id)


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
