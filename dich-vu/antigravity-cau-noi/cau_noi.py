"""Cầu nối Antigravity — máy chủ nhỏ nói chuẩn OpenAI, gọi ``agy`` chính thức ở chế độ không tương tác.

Chạy TRÊN MÁY CHỦ (không trong container web2api vì ``agy`` nặng 214 MB). Mỗi yêu cầu chạy MỘT tiến trình
``agy -p … --output-format json`` trong thư mục tạm trống riêng, dọn sau.

Bể tài khoản: mỗi thư mục HOME trong ``AGY_HOMES`` là một tài khoản ``agy`` đã đăng nhập. Hạn mức đọc từ
``/usage`` (cache ngắn) theo CẤU TRÚC dòng «họ model ⇥ cửa sổ ⇥ N% ⇥ mốc ISO»; chọn tài khoản còn nhiều nhất cho
họ model của yêu cầu, xoay vòng giữa các tài khoản ngang nhau; cạn (≤ 0 %) thì nghỉ tới mốc reset của CỬA SỔ CẠN;
mỗi tài khoản tối đa một yêu cầu cùng lúc; trần đồng thời chung.

An toàn (soát 12/10/2026):
  - tiến trình con chạy bằng ``create_subprocess_exec`` (không shell); chuỗi người dùng chỉ vào ĐÚNG MỘT tham số
    (prompt, luôn mở đầu bằng LOI_DAN nên không bao giờ bị hiểu là cờ) và ``--model`` (đã khớp danh mục ``agy models``);
  - môi trường của con là danh sách CHO PHÉP (PATH, LANG, LC_*, …) — khoá cầu nối và mọi bí mật khác của tiến trình
    cầu nối không lọt sang agent (agent có công cụ chạy lệnh: «in biến môi trường» là đủ để lộ khoá);
  - mọi tiến trình con chạy trong phiên riêng (``start_new_session``) và bị giết CẢ NHÓM ở mọi đường ra (xong, hết giờ,
    khách ngắt, bị huỷ), thư mục tạm dọn trong ``finally``;
  - cầu nối KHÔNG mở tệp nào của agy (``$HOME/.gemini/…`` chứa token); email chỉ đọc từ tệp do người vận hành đặt;
  - KHÔNG log prompt, đường HOME hay email — log chỉ số thứ tự tài khoản (``acc0``, ``acc1``…);
  - khoá ``AGY_CAU_NOI_KHOA`` bắt buộc, so bằng ``hmac.compare_digest``; mặc định chỉ nghe 127.0.0.1;
  - thân yêu cầu có trần (413), prompt có trần dưới giới hạn một tham số của Linux (128 KiB).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import shutil
import signal
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

log = logging.getLogger("antigravity.cau_noi")

# ── cấu hình từ biến môi trường ──────────────────────────────────────────────


def _so(ten: str, mac_dinh: int) -> int:
    return int(os.environ.get(ten, str(mac_dinh)))


def _agy_bin() -> str:
    return os.environ.get("AGY_BIN", "agy")


def _agy_homes() -> list[str]:
    return [p.strip() for p in os.environ.get("AGY_HOMES", "").split(":") if p.strip()]


def _agy_toi_da_dong_thoi() -> int:
    return max(1, _so("AGY_TOI_DA_DONG_THOI", 4))


def _agy_han_gio_s() -> int:
    # 90 s: dưới thời gian chờ đọc 120 s của bộ chuyển tiếp web2api, để lỗi hết giờ là của cầu nối (504 rõ ràng).
    return max(1, _so("AGY_HAN_GIO_S", 90))


def _cho_tai_khoan_s() -> int:
    return max(0, _so("AGY_CHO_TAI_KHOAN_S", 20))


def _ttl_models_s() -> int:
    return _so("AGY_TTL_MODELS_S", 600)


def _ttl_usage_s() -> int:
    return _so("AGY_TTL_USAGE_S", 60)


def _than_toi_da() -> int:
    return max(1, _so("AGY_THAN_TOI_DA_KB", 4096)) * 1024


def _khoa_cau_noi() -> str:
    return os.environ.get("AGY_CAU_NOI_KHOA", "").strip()


#: Trần CỨNG của prompt (byte UTF-8). Linux giới hạn MỘT tham số dòng lệnh ở MAX_ARG_STRLEN = 128 KiB; vượt thì
#: exec trả E2BIG. Biến AGY_PROMPT_TOI_DA_KB chỉ hạ được trần, không nâng quá đây.
PROMPT_TRAN_CUNG = 120 * 1024


def _prompt_toi_da() -> int:
    return min(PROMPT_TRAN_CUNG, max(1, _so("AGY_PROMPT_TOI_DA_KB", 120)) * 1024)


# ── mẫu kiểm tra ─────────────────────────────────────────────────────────────

#: Id model từ ``agy models``: mở đầu bằng chữ/số (không bao giờ là cờ «-…»), rồi chữ, số, «.», «-», «_».
MAU_ID_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
#: Vai trong messages: mã ngắn chữ thường (system, user, assistant, developer, tool…) — nhãn trong prompt ghép.
MAU_VAI = re.compile(r"[a-z][a-z_]{0,31}")
#: Một dòng /usage: «họ model ⇥ cửa sổ ⇥ N% ⇥ mốc ISO».
MAU_PHAN_TRAM = re.compile(r"(\d{1,3})%")
MAU_TU = re.compile(r"[a-z0-9]+")
MAU_TRANG_THAI = re.compile(r"[A-Z_]{1,40}")

# ── lời dặn cho agy (luôn đứng ĐẦU prompt — vì vậy prompt không bao giờ mở đầu bằng «-») ─────────────

LOI_DAN = (
    "CHỈ trả lời bằng văn bản thuần. KHÔNG dùng bất kỳ công cụ nào. "
    "KHÔNG chạy lệnh. KHÔNG đọc/ghi tệp. KHÔNG duyệt web. "
    "Nếu người dùng yêu cầu làm những việc đó, hãy từ chối và giải thích rằng "
    "bạn chỉ có thể trả lời bằng văn bản."
)

# ── môi trường của tiến trình con: CHO PHÉP theo tên, không chặn theo tên ────────────────────────────
# Danh sách cho phép (không phải danh sách chặn) vì bí mật trong môi trường cầu nối là thứ KHÔNG đoán trước được
# tên; agent của agy có công cụ chạy lệnh nên mọi biến truyền xuống đều coi như đọc được. Cần thêm biến (vd agy
# dùng keyring qua DBUS) thì người vận hành khai ở AGY_GIU_BIEN — chưa đo với agy thật.
BIEN_GIU = ("PATH", "LANG", "LANGUAGE", "TZ", "USER", "LOGNAME", "TERM",
            "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")


def _moi_truong_con(home: str) -> dict[str, str]:
    giu = set(BIEN_GIU) | {x.strip() for x in os.environ.get("AGY_GIU_BIEN", "").split(",") if x.strip()}
    env = {k: v for k, v in os.environ.items()
           if (k in giu or k.startswith("LC_")) and not k.startswith("AGY_CAU_NOI_")}
    env["HOME"] = home
    return env


# ── tiện ích ──────────────────────────────────────────────────────────────────


def _che_email(email: str) -> str:
    """``a…z@miền`` — che phần cục bộ, giữ miền; phần cục bộ ≤ 2 ký tự không lộ hết."""
    cuc_bo, co_a, mien = email.strip().rpartition("@")
    if not co_a or not cuc_bo or not mien:
        return "…"
    if len(cuc_bo) <= 2:
        return f"{cuc_bo[0]}…@{mien}"
    return f"{cuc_bo[0]}…{cuc_bo[-1]}@{mien}"


def _loi_openai(ma: int, thong_diep: str, code: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": _than_loi(ma, thong_diep, code)}, status_code=ma, headers=headers)


def _than_loi(ma: int, thong_diep: str, code: str) -> dict:
    loai = {400: "invalid_request_error", 401: "authentication_error", 404: "invalid_request_error",
            413: "invalid_request_error", 429: "rate_limit_error"}.get(ma, "api_error")
    return {"message": thong_diep, "type": loai, "code": code}


def _kiem_xac_thuc(request: Request) -> JSONResponse | None:
    """Kiểm ``Authorization: Bearer <khoá>``; trả JSONResponse lỗi hoặc None nếu đúng."""
    khoa = _khoa_cau_noi()
    if not khoa:
        return _loi_openai(500, "Chưa đặt biến AGY_CAU_NOI_KHOA trên máy chủ cầu nối.", "server_misconfigured")
    loai, _, gia_tri = request.headers.get("authorization", "").partition(" ")
    gia_tri = gia_tri.strip()
    if loai.lower() != "bearer" or not gia_tri or not hmac.compare_digest(
        gia_tri.encode("utf-8", "surrogateescape"), khoa.encode("utf-8")
    ):
        return _loi_openai(401, "Thiếu hoặc sai khoá cầu nối. Gửi header «Authorization: Bearer <khoá>».",
                           "invalid_api_key", {"WWW-Authenticate": "Bearer"})
    return None


def _doc_mot_iso(chuoi: str) -> float | None:
    try:
        return datetime.fromisoformat(chuoi.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


# ── chạy tiến trình con ───────────────────────────────────────────────────────


@dataclass
class KetQuaChay:
    ma_thoat: int | None   # None = không khởi động được, hết giờ hay khách ngắt (đã bị giết)
    stdout: bytes
    het_gio: bool
    giay: float


async def _cho_khach_ngat(request: Request) -> None:
    while not await request.is_disconnected():
        await asyncio.sleep(0.2)


def _giet_nhom(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL cả nhóm tiến trình (con + cháu cùng phiên) — gọi đồng bộ, không cần await để có hiệu lực."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.kill()
    except ProcessLookupError:
        pass


async def _chay(lenh: list[str], home: str, han_s: float, request: Request | None = None) -> KetQuaChay:
    """Chạy ``lenh`` (danh sách tham số, KHÔNG shell) với HOME=home trong thư mục tạm trống riêng.

    Con ở phiên riêng; ra khỏi hàm bằng BẤT KỲ đường nào (xong, hết giờ, khách ngắt, bị huỷ, lỗi) mà con còn sống
    thì cả nhóm bị SIGKILL; thư mục tạm luôn bị dọn. stderr bỏ đi: có thể chứa đường dẫn/email của tài khoản.
    """
    thu_muc = tempfile.mkdtemp(prefix="agy_req_")
    bat_dau = time.monotonic()
    proc: asyncio.subprocess.Process | None = None
    cho_xong: asyncio.Future | None = None
    theo_doi: asyncio.Task | None = None
    try:
        try:
            proc = await asyncio.create_subprocess_exec(
                *lenh, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env=_moi_truong_con(home), cwd=thu_muc, start_new_session=True)
        except (OSError, ValueError) as e:
            log.warning("không chạy được agy: %s", type(e).__name__)
            return KetQuaChay(None, b"", False, 0.0)
        cho_xong = asyncio.ensure_future(proc.communicate())
        cho = {cho_xong}
        if request is not None:
            theo_doi = asyncio.create_task(_cho_khach_ngat(request))
            cho.add(theo_doi)
        xong, _ = await asyncio.wait(cho, timeout=han_s, return_when=asyncio.FIRST_COMPLETED)
        if cho_xong in xong:
            stdout, _ = cho_xong.result()
            return KetQuaChay(proc.returncode, stdout or b"", False, time.monotonic() - bat_dau)
        return KetQuaChay(None, b"", not xong, time.monotonic() - bat_dau)
    finally:
        if proc is not None and proc.returncode is None:
            _giet_nhom(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        for t in (cho_xong, theo_doi):
            if t is not None and not t.done():
                t.cancel()
        shutil.rmtree(thu_muc, ignore_errors=True)


def _json_cua(stdout: bytes) -> dict | None:
    try:
        j = json.loads(stdout)
    except (ValueError, RecursionError):
        return None
    return j if isinstance(j, dict) else None


# ── bể tài khoản ─────────────────────────────────────────────────────────────

TEP_EMAIL = ".agy-cau-noi-email"


def _doc_email(home: str) -> str:
    """Email do NGƯỜI VẬN HÀNH ghi ở ``$HOME/.agy-cau-noi-email`` (một dòng). Không mở tệp nào của agy."""
    try:
        with open(os.path.join(home, TEP_EMAIL), encoding="utf-8") as f:
            dong = f.read(320).strip().splitlines()
    except (OSError, ValueError):
        return ""
    return dong[0].strip() if dong else ""


def phan_tich_usage(van_ban: str) -> dict[str, list[dict]]:
    """Bảng /usage → {họ: [{cua_so, phan_tram, moc_reset, moc_ts}]} theo CẤU TRÚC dòng; dòng lạ bị bỏ, không ném."""
    han_muc: dict[str, list[dict]] = {}
    for dong in van_ban.splitlines():
        cot = [c.strip() for c in dong.split("\t")]
        if len(cot) < 4 or not cot[0] or not cot[1]:
            continue
        m = MAU_PHAN_TRAM.fullmatch(cot[2])
        if not m:
            continue
        han_muc.setdefault(cot[0], []).append({
            "cua_so": cot[1], "phan_tram": int(m.group(1)), "moc_reset": cot[3] or None, "moc_ts": _doc_mot_iso(cot[3]),
        })
    return han_muc


class TaiKhoan:
    """Một tài khoản ``agy`` (một thư mục HOME). ``ban`` do BeTaiKhoan giữ dưới điều kiện chung."""

    def __init__(self, thu_tu: int, home: str):
        self.thu_tu = thu_tu
        self.home = home
        self.ban = False
        self.han_muc: dict[str, list[dict]] | None = None
        self.han_muc_luc = 0.0
        self.email = _doc_email(home)
        self._dang_doc = asyncio.Lock()

    @property
    def nhan(self) -> str:
        return f"acc{self.thu_tu}"

    def _ho_cua(self, model: str) -> str | None:
        """Họ trong /usage của model: họ có một TỪ trùng tiền tố id (gemini-… ↔ «Gemini Models», gpt-… ↔ «Claude and
        GPT models»). Không bảng tên họ nào trong mã; không khớp → None (họ «khác»)."""
        tien_to = MAU_TU.match(model.lower())
        if not tien_to or not self.han_muc:
            return None
        for ho in self.han_muc:
            if tien_to.group(0) in MAU_TU.findall(ho.lower()):
                return ho
        return None

    def con(self, model: str) -> int:
        """% còn lại cho model: họ đã biết → cửa sổ THẤP nhất của họ; họ «khác» → cửa sổ CAO nhất bất kỳ.
        Chưa đọc được /usage → 100 (cho thử; lỗi thật sẽ về bằng mã thoát)."""
        if not self.han_muc:
            return 100
        ho = self._ho_cua(model)
        if ho is not None:
            return min(w["phan_tram"] for w in self.han_muc[ho])
        return max(w["phan_tram"] for ds in self.han_muc.values() for w in ds)

    def nghi_den(self, model: str) -> float:
        """Epoch hết nghỉ cho model (0 = không nghỉ). Họ đã biết: chờ cửa sổ cạn reset MUỘN nhất (họ chỉ dùng được
        khi mọi cửa sổ > 0); họ «khác»: chờ cửa sổ reset SỚM nhất. Cạn mà thiếu mốc → nghỉ 1 giờ từ lúc đọc."""
        if self.con(model) > 0:
            return 0.0
        mac_dinh = self.han_muc_luc + 3600
        ho = self._ho_cua(model)
        if ho is not None:
            return max(w["moc_ts"] or mac_dinh for w in self.han_muc[ho] if w["phan_tram"] <= 0)
        return min(w["moc_ts"] or mac_dinh for ds in self.han_muc.values() for w in ds)

    def _can_doc_lai(self, bay_gio: float) -> bool:
        if self.han_muc is None or bay_gio - self.han_muc_luc >= _ttl_usage_s():
            return True
        # Cửa sổ cạn đã qua mốc reset: đọc lại ngay, đừng đợi hết TTL.
        return any(w["phan_tram"] <= 0 and w["moc_ts"] is not None and w["moc_ts"] <= bay_gio
                   for ds in self.han_muc.values() for w in ds)

    async def cap_nhat_han_muc(self, agy_bin: str) -> None:
        """Đọc ``/usage`` khi cache cũ. Bỏ qua khi tài khoản đang bận (không chạy hai agy cùng HOME)."""
        if self.ban or self._dang_doc.locked() or not self._can_doc_lai(time.time()):
            return
        async with self._dang_doc:
            kq = await _chay([agy_bin, "-p", "/usage", "--output-format", "json", "--print-timeout", "30s",
                              "--sandbox"], self.home, 45)
            if kq.ma_thoat != 0:
                log.warning("%s: không đọc được /usage (mã thoát %s, hết giờ %s)", self.nhan, kq.ma_thoat, kq.het_gio)
                return
            j = _json_cua(kq.stdout)
            van_ban = j.get("response") if j is not None else kq.stdout.decode("utf-8", "replace")
            han_muc = phan_tich_usage(van_ban if isinstance(van_ban, str) else "")
            if not han_muc:
                log.warning("%s: /usage không có dòng hạn mức nào đúng cấu trúc", self.nhan)
                return
            self.han_muc = han_muc
            self.han_muc_luc = time.time()
            self.email = _doc_email(self.home)

    def thong_tin_che(self) -> dict:
        """Thông tin tài khoản đã che cho /tai-khoan — không đường HOME, không token."""
        han_muc = {ho: [{k: w[k] for k in ("cua_so", "phan_tram", "moc_reset")} for w in ds]
                   for ho, ds in (self.han_muc or {}).items()}
        return {"thu_tu": self.thu_tu, "email": _che_email(self.email) if self.email else None,
                "dang_ban": self.ban, "han_muc": han_muc or None}


class BeTaiKhoan:
    """Nhiều tài khoản agy: chọn theo hạn mức, một yêu cầu/tài khoản, trần đồng thời chung."""

    def __init__(self, homes: list[str], agy_bin: str):
        self.tai_khoan = [TaiKhoan(i, h) for i, h in enumerate(homes)]
        self.agy_bin = agy_bin
        self.tran = asyncio.Semaphore(_agy_toi_da_dong_thoi())
        self._dk = asyncio.Condition()
        self._vi_tri = 0

    async def cap_nhat_tat_ca(self) -> None:
        await asyncio.gather(*(tk.cap_nhat_han_muc(self.agy_bin) for tk in self.tai_khoan))

    def chon(self, model: str) -> tuple[TaiKhoan | None, str]:
        """(tài khoản, "") hoặc (None, "ban" | "can"). "ban": có tài khoản còn hạn mức nhưng đang bận."""
        n = len(self.tai_khoan)
        ung_vien: list[tuple[int, TaiKhoan]] = []
        co_ban = False
        for tk in self.tai_khoan:
            pt = tk.con(model)
            if pt <= 0:
                continue
            if tk.ban:
                co_ban = True
                continue
            ung_vien.append((pt, tk))
        if not ung_vien:
            return None, "ban" if co_ban else "can"
        cao = max(pt for pt, _ in ung_vien)
        chon = min((tk for pt, tk in ung_vien if pt == cao), key=lambda t: (t.thu_tu - self._vi_tri) % n)
        self._vi_tri = (chon.thu_tu + 1) % n
        return chon, ""

    async def muon(self, model: str, han_cho_s: float) -> TaiKhoan | str:
        """Chọn và ĐÁNH DẤU BẬN trong cùng một đoạn giữ khoá (không await xen giữa). Tài khoản còn hạn mức mà bận
        hết → chờ (có hạn) tới khi một cái rảnh. Trả TaiKhoan, hoặc "can" / "ban"."""
        loop = asyncio.get_running_loop()
        cuoi = loop.time() + han_cho_s
        async with self._dk:
            while True:
                tk, ly_do = self.chon(model)
                if tk is not None:
                    tk.ban = True
                    return tk
                con = cuoi - loop.time()
                if ly_do == "can" or con <= 0:
                    return ly_do
                try:
                    await asyncio.wait_for(self._dk.wait(), timeout=con)
                except asyncio.TimeoutError:
                    return "ban"

    async def tra(self, tk: TaiKhoan) -> None:
        tk.ban = False   # trước mọi await: lượt bị huỷ lần nữa lúc chờ khoá thì tài khoản vẫn đã rảnh
        async with self._dk:
            self._dk.notify_all()

    def thu_lai_sau(self, model: str, bay_gio: float) -> int:
        """Giây tới khi tài khoản ĐẦU TIÊN hết nghỉ cho model (Retry-After của 429)."""
        moc = [m for m in (tk.nghi_den(model) for tk in self.tai_khoan) if m > 0]
        return max(1, int(min(moc) - bay_gio + 0.999)) if moc else 3600


# ── danh mục model (cache) ─────────────────────────────────────────────────

_dem_models: list[dict] = []
_dem_models_luc: float = 0.0
_khoa_models = asyncio.Lock()


def phan_tich_models(van_ban: str) -> list[dict]:
    """``agy models``: mỗi dòng ``<id>\\t<nhãn>``; dòng không đúng cấu trúc (vd «Fetching…») bị bỏ."""
    ds: list[dict] = []
    for dong in van_ban.splitlines():
        cot = dong.strip().split("\t")
        if len(cot) >= 2 and MAU_ID_MODEL.fullmatch(cot[0].strip()):
            ds.append({"id": cot[0].strip(), "nhan": cot[1].strip()})
    return ds


async def _lay_models(agy_bin: str, homes: list[str]) -> list[dict]:
    global _dem_models, _dem_models_luc
    async with _khoa_models:
        if _dem_models and time.time() - _dem_models_luc < _ttl_models_s():
            return _dem_models
        for i, home in enumerate(homes):   # tài khoản đầu hỏng thì hỏi tài khoản kế
            kq = await _chay([agy_bin, "models"], home, 30)
            ds = phan_tich_models(kq.stdout.decode("utf-8", "replace")) if kq.ma_thoat == 0 else []
            if ds:
                _dem_models, _dem_models_luc = ds, time.time()
                break
            log.warning("acc%d: không lấy được danh sách model (mã thoát %s)", i, kq.ma_thoat)
        return _dem_models


# ── yêu cầu chat ─────────────────────────────────────────────────────────────


class LoiYeuCau(Exception):
    def __init__(self, ma: int, thong_diep: str, code: str):
        super().__init__(thong_diep)
        self.ma, self.thong_diep, self.code = ma, thong_diep, code


async def _doc_than(request: Request) -> dict:
    """Đọc thân JSON có TRẦN: Content-Length vượt → 413 ngay; không khai thì đếm từng khúc."""
    tran = _than_toi_da()
    cl = request.headers.get("content-length", "").strip()
    if cl and (not cl.isdigit() or len(cl) > 15 or int(cl) > tran):
        raise LoiYeuCau(413, f"Thân yêu cầu vượt trần {tran // 1024} KB (AGY_THAN_TOI_DA_KB).", "request_too_large")
    buf = bytearray()
    async for khuc in request.stream():
        buf += khuc
        if len(buf) > tran:
            raise LoiYeuCau(413, f"Thân yêu cầu vượt trần {tran // 1024} KB (AGY_THAN_TOI_DA_KB).", "request_too_large")
    try:
        j = json.loads(buf)
    except (ValueError, RecursionError):
        raise LoiYeuCau(400, "Thân yêu cầu không phải JSON hợp lệ.", "invalid_json") from None
    if not isinstance(j, dict):
        raise LoiYeuCau(400, "Thân yêu cầu phải là một đối tượng JSON.", "invalid_request")
    return j


def _ghep_messages(messages: list) -> tuple[str, int]:
    """messages OpenAI → (văn bản có nhãn vai, số khối không phải chữ đã bỏ). Kiểm hình dạng, sai → LoiYeuCau 400."""
    phan: list[str] = []
    bo = 0
    for msg in messages:
        if not isinstance(msg, dict):
            raise LoiYeuCau(400, "Mỗi phần tử của «messages» phải là một đối tượng.", "invalid_request")
        vai = msg.get("role")
        if not isinstance(vai, str) or not MAU_VAI.fullmatch(vai):
            raise LoiYeuCau(400, "«role» phải là mã chữ thường (system, user, assistant…).", "invalid_request")
        noi_dung = msg.get("content")
        if isinstance(noi_dung, list):
            chu: list[str] = []
            for khoi in noi_dung:
                if isinstance(khoi, dict) and khoi.get("type") == "text" and isinstance(khoi.get("text"), str):
                    chu.append(khoi["text"])
                else:
                    bo += 1
            noi_dung = "\n".join(chu)
        elif noi_dung is None:
            noi_dung = ""
        elif not isinstance(noi_dung, str):
            raise LoiYeuCau(400, "«content» phải là chuỗi hoặc danh sách khối.", "invalid_request")
        phan.append(f"[{vai}]\n{noi_dung}")
    return "\n\n".join(phan), bo


def _kiem_prompt(prompt: str) -> None:
    try:
        du_lieu = prompt.encode("utf-8")
    except UnicodeEncodeError:
        raise LoiYeuCau(400, "Nội dung có ký tự Unicode hỏng (surrogate lẻ).", "invalid_request") from None
    if b"\x00" in du_lieu:
        raise LoiYeuCau(400, "Nội dung chứa ký tự NUL — không truyền được cho agy.", "invalid_request")
    tran = _prompt_toi_da()
    if len(du_lieu) > tran:
        raise LoiYeuCau(400, f"Hội thoại dài {len(du_lieu)} byte, vượt trần {tran} byte của một lượt agy.",
                        "context_length_exceeded")


def _phan_loai(kq: KetQuaChay, han_s: int) -> tuple[str, dict] | LoiYeuCau:
    """Kết quả tiến trình → (câu trả lời, usage OpenAI) hoặc lỗi — theo CẤU TRÚC (cờ hết giờ, mã thoát, `status`,
    `denied_actions`), không dò chữ."""
    if kq.het_gio:
        return LoiYeuCau(504, f"agy chạy quá {han_s} s — tiến trình đã bị dừng.", "timeout")
    j = _json_cua(kq.stdout) if kq.ma_thoat == 0 else None
    trang_thai = j.get("status") if j is not None else None
    if j is None or trang_thai != "SUCCESS":
        if kq.ma_thoat is not None and kq.giay >= han_s:   # agy tự dừng ở --print-timeout
            return LoiYeuCau(504, f"agy chạy quá {han_s} s.", "timeout")
        tt = f", trạng thái {trang_thai}" if isinstance(trang_thai, str) and MAU_TRANG_THAI.fullmatch(trang_thai) else ""
        return LoiYeuCau(502, f"agy lỗi (mã thoát {kq.ma_thoat}{tt}).", "upstream_error")
    phan_hoi = j.get("response")
    if phan_hoi is None:
        phan_hoi = ""
    if not isinstance(phan_hoi, str):
        return LoiYeuCau(502, "agy trả `response` không phải chuỗi.", "upstream_error")
    tu_choi = j.get("denied_actions")
    if isinstance(tu_choi, list) and tu_choi and not phan_hoi.strip():
        return LoiYeuCau(502, f"agy dừng lượt vì muốn dùng công cụ bị từ chối ({len(tu_choi)} hành động).",
                         "agent_wanted_tool")
    u = j.get("usage") if isinstance(j.get("usage"), dict) else {}

    def so(k: str) -> int:
        v = u.get(k)
        return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0
    usage = {"prompt_tokens": so("input_tokens"), "completion_tokens": so("output_tokens"),
             "total_tokens": so("total_tokens")}
    return phan_hoi, usage


async def _luot(b: BeTaiKhoan, tk: TaiKhoan, prompt: str, model: str, request: Request):
    """Một lượt agy. SỞ HỮU tài khoản và chỗ trong trần: trả lại trong finally dù lượt kết thúc thế nào."""
    han = _agy_han_gio_s()
    try:
        kq = await _chay([b.agy_bin, "-p", prompt, "--model", model, "--mode", "accept-edits", "--sandbox",
                          "--output-format", "json", "--print-timeout", f"{han}s"], tk.home, han + 5, request)
        ket_qua = _phan_loai(kq, han)
        log.info("%s: model=%s %s", tk.nhan, model,
                 f"lỗi {ket_qua.ma} {ket_qua.code}" if isinstance(ket_qua, LoiYeuCau) else "xong")
        return ket_qua
    finally:
        b.tran.release()
        await b.tra(tk)


# ── SSE ──────────────────────────────────────────────────────────────────────

NHIP_SSE_S = 10.0   # dòng chú thích «: …» giữ kết nối khi agy chưa xong (bộ chuyển tiếp web2api chờ 60 s/khúc)


def _chia_doan(van_ban: str, co_doan: int = 20) -> list[str]:
    return [van_ban[i:i + co_doan] for i in range(0, len(van_ban), co_doan)]


def _dong(du_lieu: dict) -> bytes:
    return f"data: {json.dumps(du_lieu, ensure_ascii=False)}\n\n".encode()


async def _phat_sse(tac_vu: asyncio.Task, model: str, kem_usage: bool) -> AsyncIterator[bytes]:
    """SSE chuẩn OpenAI: chunk vai → (nhịp chú thích khi chờ) → các delta nội dung → finish_reason «stop» →
    [chunk usage, `choices: []`, khi khách xin stream_options.include_usage] → [DONE]. Lỗi giữa chừng → một
    sự kiện `{"error": …}` rồi [DONE]. Khách ngắt → huỷ lượt (tiến trình bị giết trong _chay)."""
    ma = "chatcmpl-" + uuid.uuid4().hex[:24]
    tao = int(time.time())

    def chunk(delta: dict, ket_thuc: str | None = None) -> dict:
        return {"id": ma, "object": "chat.completion.chunk", "created": tao, "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": ket_thuc}]}
    try:
        yield _dong(chunk({"role": "assistant", "content": ""}))
        while not tac_vu.done():
            xong, _ = await asyncio.wait({tac_vu}, timeout=NHIP_SSE_S)
            if not xong:
                yield b": dang-cho-agy\n\n"
        ket_qua = tac_vu.result()
        if isinstance(ket_qua, LoiYeuCau):
            yield _dong({"error": _than_loi(ket_qua.ma, ket_qua.thong_diep, ket_qua.code)})
            yield b"data: [DONE]\n\n"
            return
        phan_hoi, usage = ket_qua
        for doan in _chia_doan(phan_hoi):
            yield _dong(chunk({"content": doan}))
        yield _dong(chunk({}, "stop"))
        if kem_usage:
            yield _dong({"id": ma, "object": "chat.completion.chunk", "created": tao, "model": model,
                         "choices": [], "usage": usage})
        yield b"data: [DONE]\n\n"
    finally:
        if not tac_vu.done():
            tac_vu.cancel()


# ── ứng dụng FastAPI ─────────────────────────────────────────────────────────

be: BeTaiKhoan | None = None


def lay_be() -> BeTaiKhoan:
    global be
    if be is None:
        be = BeTaiKhoan(_agy_homes(), _agy_bin())
    return be


@asynccontextmanager
async def vong_doi(_app: FastAPI):
    b = lay_be()
    if b.tai_khoan:
        await b.cap_nhat_tat_ca()
        log.info("Cầu nối khởi động với %d tài khoản", len(b.tai_khoan))
    else:
        log.warning("AGY_HOMES rỗng — cầu nối không có tài khoản nào")
    yield


app = FastAPI(title="Antigravity Bridge", lifespan=vong_doi)


@app.get("/health")
async def health():
    return {"status": "ok", "accounts": len(lay_be().tai_khoan)}


@app.get("/v1/models")
async def v1_models(request: Request):
    loi = _kiem_xac_thuc(request)
    if loi:
        return loi
    ds = await _lay_models(_agy_bin(), _agy_homes())
    return {"object": "list", "data": [{"id": m["id"], "object": "model", "owned_by": "antigravity"} for m in ds]}


@app.post("/v1/chat/completions")
async def v1_chat(request: Request):
    loi = _kiem_xac_thuc(request)
    if loi:
        return loi
    try:
        than = await _doc_than(request)
        model, messages = than.get("model"), than.get("messages")
        if not isinstance(model, str) or not model:
            raise LoiYeuCau(400, "Thiếu trường «model».", "invalid_request")
        if not isinstance(messages, list) or not messages:
            raise LoiYeuCau(400, "Thiếu trường «messages» (danh sách khác rỗng).", "invalid_request")
        noi_dung, so_bo = _ghep_messages(messages)
        prompt = f"{LOI_DAN}\n\n{noi_dung}"
        _kiem_prompt(prompt)
        # Id model chỉ được dùng khi KHỚP danh mục của agy — chuỗi lạ không bao giờ tới dòng lệnh.
        if model not in {m["id"] for m in await _lay_models(_agy_bin(), _agy_homes())}:
            raise LoiYeuCau(404, f"Model {model!r} không có trong danh mục của agy.", "model_not_found")
    except LoiYeuCau as e:
        return _loi_openai(e.ma, e.thong_diep, e.code)

    stream = than.get("stream") is True
    so = than.get("stream_options")
    kem_usage = stream and isinstance(so, dict) and so.get("include_usage") is True

    b = lay_be()
    if not b.tai_khoan:
        return _loi_openai(503, "Cầu nối chưa có tài khoản nào (AGY_HOMES rỗng).", "no_accounts")
    # Chờ chỗ trong trần chung CÓ HẠN (cùng ngân sách chờ tài khoản): khách xếp hàng vô hạn rồi bỏ đi thì vẫn bị
    # khởi động một lượt agy tốn hạn mức cho không ai.
    try:
        await asyncio.wait_for(b.tran.acquire(), timeout=_cho_tai_khoan_s() if b.tran.locked() else None)
    except asyncio.TimeoutError:
        return _loi_openai(503, "Cầu nối đang chạy đủ số lượt tối đa (AGY_TOI_DA_DONG_THOI) — thử lại sau ít giây.",
                           "accounts_busy", {"Retry-After": "5"})
    try:
        await b.cap_nhat_tat_ca()
        tk = await b.muon(model, _cho_tai_khoan_s())
    except BaseException:
        b.tran.release()
        raise
    if not isinstance(tk, TaiKhoan):
        b.tran.release()
        if tk == "ban":
            return _loi_openai(503, "Mọi tài khoản còn hạn mức đang bận một yêu cầu khác — thử lại sau ít giây.",
                               "accounts_busy", {"Retry-After": "5"})
        giay = b.thu_lai_sau(model, time.time())
        return _loi_openai(429, f"Không tài khoản nào còn hạn mức cho model {model!r}. Thử lại sau {giay} giây.",
                           "rate_limit_exceeded", {"Retry-After": str(giay)})

    # Từ đây lượt SỞ HỮU tài khoản + chỗ trong trần (trả lại trong finally của _luot) — kể cả khi khách ngắt
    # trước khi luồng SSE kịp bắt đầu.
    tac_vu = asyncio.create_task(_luot(b, tk, prompt, model, request))
    tieu_de = {"X-Cau-Noi-Bo-Khoi": str(so_bo)} if so_bo else {}
    if stream:
        return StreamingResponse(_phat_sse(tac_vu, model, kem_usage), media_type="text/event-stream",
                                 headers={**tieu_de, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    try:
        ket_qua = await tac_vu
    finally:
        if not tac_vu.done():
            tac_vu.cancel()
    if isinstance(ket_qua, LoiYeuCau):
        return _loi_openai(ket_qua.ma, ket_qua.thong_diep, ket_qua.code)
    phan_hoi, usage = ket_qua
    return JSONResponse({
        "id": "chatcmpl-" + uuid.uuid4().hex[:24], "object": "chat.completion", "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": phan_hoi}, "finish_reason": "stop"}],
        "usage": usage,
    }, headers=tieu_de)


@app.get("/tai-khoan")
async def tai_khoan(request: Request):
    loi = _kiem_xac_thuc(request)
    if loi:
        return loi
    b = lay_be()
    await b.cap_nhat_tat_ca()
    return {"tai_khoan": [tk.thong_tin_che() for tk in b.tai_khoan]}


def main():
    """Điểm vào khi chạy trực tiếp."""
    import uvicorn

    host = os.environ.get("AGY_CAU_NOI_HOST", "127.0.0.1")
    port = int(os.environ.get("AGY_CAU_NOI_PORT", "8300"))
    if not _khoa_cau_noi():
        raise SystemExit("LỖI: chưa đặt biến AGY_CAU_NOI_KHOA (bắt buộc).")
    logging.basicConfig(level=logging.INFO)
    log.info("Cầu nối nghe tại %s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
