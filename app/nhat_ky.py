"""Kho nhật ký hệ thống theo KÊNH của web2api (Đợt C phần lõi).

Nguyên tắc chủ máy:
- Lõi độc lập với module — KÊNH nhật ký là mã MỞ, module nào cũng ghi được kênh riêng
  qua `from app import nhat_ky; nhat_ky.ghi(...)` mà không phải sửa lõi.
- Ghi dạng JSON Lines trong `<WEB2API_DATA>/nhat_ky/`, xoay vòng theo kích thước và số dòng
  (cấu hình bằng `WEB2API_NHAT_KY_TOI_DA_MB`, mặc định 20 MB). Quyền tệp 0600, thư mục 0700.
- An toàn luồng (khoá luồng RLock), KHÔNG BAO GIỜ ném lỗi ra ngoài (nuốt và log; riêng tín hiệu điều khiển
  như CancelledError/KeyboardInterrupt thì đi tiếp), và CHE bí mật (dùng `che_chuoi` trong app/bien_loi.py).
- `ghi` chạy trên đường nóng của MỌI lượt /v1 (đồng bộ, trong vòng lặp sự kiện): một dòng tốn I/O cỡ độ dài dòng.
  Kích thước tệp lấy bằng fstat (O(1)); số dòng giữ bằng bộ đếm trong bộ nhớ gắn với DANH TÍNH tệp (thiết bị, inode,
  kích thước đã biết) — tệp bị thay/xoay/ghi từ ngoài thì đếm lại MỘT lần. Xoay vòng xét TRƯỚC khi ghi nên không tệp
  nào vượt trần mỗi tệp (trần tổng / SO_TEP_XOAY).
- Đọc (`doc`, `danh_sach_kenh`) chỉ giữ khoá lúc MỞ các tệp (ảnh chụp nhất quán: fd đã mở vẫn đúng nội dung dù tệp bị
  xoay/thay sau đó), rồi quét không giữ khoá — trang quản trị đọc nhật ký không chặn `ghi` của các lượt /v1. `doc` đọc
  từ CUỐI tệp lùi dần: trang mới nhất chỉ chạm phần đuôi.
- Kênh có sẵn của lõi:
  + `cuoc_goi`: MỖI lượt /v1/* (kể cả lỗi, 401, 404, stream đứt) ghi đúng MỘT dòng qua middleware ASGI.
  + `he_thong`: nạp/đổi/gỡ module, lỗi nạp, khởi động, cảnh báo cấu hình hỏng (vd TZ sai).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import secrets
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import kho
from app.bien_loi import che_chuoi, la_duong_v1
from app.kham_pha import MA_KHA_NANG

log = logging.getLogger("web2api.nhat_ky")

MUC_HOP_LE = {"thong_tin", "canh_bao", "loi"}
MAC_DINH_TOI_DA_MB = 20
SO_TEP_XOAY = 5  # tệp hiện tại + 4 tệp cũ
_KHOA = threading.RLock()
_last_luc = ""
#: Bộ đếm dòng của tệp hiện tại: {"danh_tinh": (đường, thiết bị, inode), "co": kích thước đã biết, "dong": số dòng}.
_DEM: dict[str, Any] = {}
_REQ_ID_HOP_LE = re.compile(r"^[A-Za-z0-9._-]{1,128}\Z")
#: Thân JSON (không stream) lớn hơn mức này thì không phân tích tìm `usage` — ảnh b64_json cỡ MB, phân tích trên
#: vòng lặp sự kiện chỉ để lấy vài con số là không đáng.
TRAN_THAN_USAGE = 1024 * 1024


def tran_mb() -> float:
    try:
        val = float(os.environ.get("WEB2API_NHAT_KY_TOI_DA_MB", MAC_DINH_TOI_DA_MB))
        return val if val > 0 and math.isfinite(val) else float(MAC_DINH_TOI_DA_MB)
    except (ValueError, TypeError):
        return float(MAC_DINH_TOI_DA_MB)


def _cac_tran() -> tuple[int, int, int]:
    """(trần tổng byte, trần byte mỗi tệp, trần dòng mỗi tệp)."""
    mb = tran_mb()
    tong = max(1024, int(mb * 1024 * 1024))
    return tong, max(512, tong // SO_TEP_XOAY), max(50, int(mb * 1000))


def thu_muc_nhat_ky() -> Path:
    return kho.thu_muc_du_lieu() / "nhat_ky"


def _p_hien_tai() -> Path:
    return thu_muc_nhat_ky() / "nhat_ky.jsonl"


def _p_cu(i: int) -> Path:
    return thu_muc_nhat_ky() / f"nhat_ky.{i}.jsonl"


def _sinh_luc() -> str:
    """`luc` tăng ngặt trong tiến trình (con trỏ phân trang dựa vào đó). Chỉ gọi khi đang giữ _KHOA."""
    global _last_luc
    now = datetime.now(timezone.utc)
    s = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if s <= _last_luc:
        try:
            prev_dt = datetime.strptime(_last_luc, "%Y-%m-%dT%H:%M:%S.%fZ")
            now = prev_dt + timedelta(microseconds=1)
            s = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        except ValueError:
            pass
    _last_luc = s
    return s


def chuyen_luc(chuoi: str) -> datetime:
    """ISO 8601 → datetime CÓ múi giờ (không ghi múi giờ thì hiểu là UTC). Sai dạng → ValueError."""
    dt = datetime.fromisoformat(str(chuoi).replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _luc_cua(rec: dict) -> datetime | None:
    try:
        return chuyen_luc(rec.get("luc", ""))
    except (ValueError, TypeError):
        return None


# ───────────── ghi ─────────────

def _mo_ghi(p: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT | os.O_APPEND  # đọc được: _so_dong đếm dòng qua chính fd này
    try:
        return os.open(p, flags, 0o600)
    except FileNotFoundError:  # chưa có thư mục: tạo một lần (0700)
        p.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(p.parent, 0o700)
        return os.open(p, flags, 0o600)


def _so_dong(p: Path, fd: int, st: os.stat_result) -> int:
    """Số dòng của tệp đang mở. Chỉ đếm (đọc trọn tệp) khi tệp KHÁC tệp đã biết: lần đầu trong tiến trình, sau xoay
    vòng, sau `xoa_cu` (thay tệp), hay ai đó ghi từ ngoài (kích thước lệch)."""
    danh_tinh = (str(p), st.st_dev, st.st_ino)
    if _DEM.get("danh_tinh") != danh_tinh or _DEM.get("co") != st.st_size:
        os.fchmod(fd, 0o600)  # tệp lạ (có sẵn từ trước): ép lại quyền một lần
        dem, vi_tri = 0, 0
        while khoi := os.pread(fd, 1 << 20, vi_tri):
            dem += khoi.count(b"\n")
            vi_tri += len(khoi)
        _DEM.clear()
        _DEM.update(danh_tinh=danh_tinh, co=st.st_size, dong=dem)
    return _DEM["dong"]


def _xoay_vong(tran_tong: int, tran_tep: int) -> None:
    """hiện tại → .1 → .2 … → .(SO_TEP_XOAY-1); tệp cũ nhất bị đè. Rồi xoá bớt tệp cũ nhất nếu tổng tệp cũ vượt
    phần trần dành cho chúng (trần bị hạ giữa hai lần chạy). Chỉ chạy khi chạm ngưỡng."""
    for i in range(SO_TEP_XOAY - 1, 0, -1):
        try:
            os.replace(_p_hien_tai() if i == 1 else _p_cu(i - 1), _p_cu(i))
        except FileNotFoundError:
            pass
    _DEM.clear()
    cu = [_p_cu(i) for i in range(1, SO_TEP_XOAY)]
    co = {p: p.stat().st_size for p in cu if p.is_file()}
    tong = sum(co.values())
    for p in reversed(cu):  # cũ nhất trước
        if tong <= tran_tong - tran_tep:
            break
        if p in co:
            p.unlink(missing_ok=True)
            tong -= co[p]


def _ghi_dong(du_lieu: bytes) -> None:
    """Nối một dòng vào tệp hiện tại (gọi khi đang giữ _KHOA). Xoay vòng TRƯỚC nếu dòng này làm tệp vượt trần."""
    tran_tong, tran_tep, tran_dong = _cac_tran()
    p = _p_hien_tai()
    fd = _mo_ghi(p)
    try:
        st = os.fstat(fd)
        dong = _so_dong(p, fd, st)
        if st.st_size and (st.st_size + len(du_lieu) > tran_tep or dong + 1 > tran_dong):
            os.close(fd)
            fd = -1
            _xoay_vong(tran_tong, tran_tep)
            fd = _mo_ghi(p)
            st = os.fstat(fd)
            dong = _so_dong(p, fd, st)
        da_ghi = 0
        while da_ghi < len(du_lieu):
            da_ghi += os.write(fd, du_lieu[da_ghi:])
        _DEM.update(co=st.st_size + len(du_lieu), dong=dong + 1)
    finally:
        if fd >= 0:
            os.close(fd)


def _so_huu_han(v: int | float) -> int | float | None:
    """Số không hữu hạn (NaN, ±inf) → None: JSONResponse của Starlette từ chối chúng (allow_nan=False), một dòng như
    vậy làm GET /api/nhat-ky trả 500 cho tới khi tệp bị xoay đi."""
    return v if isinstance(v, int) or math.isfinite(v) else None


def _lam_sach(v: Any, do_dai: int) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return _so_huu_han(v)
    return che_chuoi(str(v))[:do_dai]


def ghi(kenh: str, muc: str, tom_tat: str, /, ms: float | int | None = None, **them) -> None:
    """Ghi một dòng nhật ký vào kho.

    An toàn luồng, không bao giờ ném lỗi ra ngoài và che mọi chuỗi bí mật. Ba tham số đầu chỉ-theo-vị-trí: module
    truyền khoá phụ tên `kenh`/`muc`/`tom_tat` thì vào `them`, không thành TypeError ở chỗ gọi.
    """
    try:
        kenh_str = str(kenh or "").strip()
        if not MA_KHA_NANG.match(kenh_str):
            kenh_str = "he_thong"

        muc_str = str(muc or "").strip()
        if muc_str not in MUC_HOP_LE:
            muc_str = "thong_tin"

        tom_tat_str = che_chuoi(str(tom_tat or ""))[:300]

        ms_val = None
        if ms is not None:
            try:
                ms_num = round(float(ms), 1)
                if math.isfinite(ms_num):
                    ms_val = int(ms_num) if ms_num.is_integer() else ms_num
            except (ValueError, TypeError, OverflowError):
                ms_val = None

        # Làm sạch `them`: tối đa 20 khoá; giá trị chuỗi/số/bool, dict một tầng hoặc danh sách ngắn.
        clean_them: dict[str, Any] = {}
        for k, v in list(them.items())[:20]:
            k_str = str(k)[:50]
            if v is None:
                continue
            if isinstance(v, dict):
                clean_them[k_str] = {str(sk)[:50]: _lam_sach(sv, 300) for sk, sv in list(v.items())[:20]}
            elif isinstance(v, (list, tuple)):
                clean_them[k_str] = [_lam_sach(x, 100) for x in v[:10]]
            else:
                clean_them[k_str] = _lam_sach(v, 300)

        with _KHOA:
            dong = {
                "luc": _sinh_luc(),
                "kenh": kenh_str,
                "muc": muc_str,
                "tom_tat": tom_tat_str,
                "ms": ms_val,
                "them": clean_them,
            }
            _ghi_dong((json.dumps(dong, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
    except Exception as e:
        log.warning("Không thể ghi nhật ký (bỏ qua để lõi chạy tiếp): %s: %s", type(e).__name__, che_chuoi(str(e)))


# ───────────── đọc ─────────────

def _cac_tep_doc() -> list[Path]:
    """Danh sách tệp nhật ký theo thứ tự từ mới nhất đến cũ nhất."""
    return [_p_hien_tai()] + [_p_cu(i) for i in range(1, SO_TEP_XOAY)]


def _mo_anh_chup() -> list[BinaryIO]:
    """Mở mọi tệp (mới → cũ) trong khi giữ khoá: xoay vòng/xoá cũ không chen được vào giữa, nên tập fd là một ảnh
    chụp nhất quán. Quét fd sau đó KHÔNG cần khoá (os.replace/unlink không đổi nội dung fd đã mở)."""
    tep: list[BinaryIO] = []
    with _KHOA:
        for p in _cac_tep_doc():
            try:
                tep.append(open(p, "rb"))
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError as e:
                log.warning("Không đọc được %s: %s", p, e)
    return tep


def _dong_nguoc(f: BinaryIO, khoi: int = 1 << 16) -> Iterator[bytes]:
    """Các dòng của tệp từ CUỐI lên đầu, đọc từng khối — trang mới nhất chỉ chạm phần đuôi tệp."""
    f.seek(0, os.SEEK_END)
    vi_tri = f.tell()
    du = b""
    while vi_tri > 0:
        n = min(khoi, vi_tri)
        vi_tri -= n
        f.seek(vi_tri)
        phan = (f.read(n) + du).split(b"\n")
        du = phan[0]
        for x in reversed(phan[1:]):
            if x.strip():
                yield x
    if du.strip():
        yield du


def _ban_ghi(raw: bytes) -> dict | None:
    try:
        rec = json.loads(raw)
    except ValueError:  # dòng dở (đang ghi) hay hỏng: bỏ qua
        return None
    return rec if isinstance(rec, dict) else None


def doc(kenh: str | None = None, muc: str | None = None, q: str | None = None,
        truoc: str | None = None, gioi_han: int = 50) -> list[dict]:
    """Đọc bản ghi nhật ký: mới nhất trước, phân trang bằng con trỏ `truoc` (= `luc` của dòng cuối trang trước).
    `truoc` sai dạng → ValueError (API trả 400; lặng lẽ bỏ qua thì khách nhận lại trang đầu và lặp vô tận)."""
    gioi_han = min(max(1, int(gioi_han)), 500)
    kenh_loc = kenh.strip() if kenh else None
    muc_loc = muc.strip() if muc else None
    q_loc = q.strip().lower() if q else None
    truoc_dt = chuyen_luc(truoc) if truoc else None

    ket_qua: list[dict] = []
    tep = _mo_anh_chup()
    try:
        for f in tep:
            for raw in _dong_nguoc(f):
                rec = _ban_ghi(raw)
                if rec is None:
                    continue
                if kenh_loc and rec.get("kenh") != kenh_loc:
                    continue
                if muc_loc and rec.get("muc") != muc_loc:
                    continue
                if q_loc and q_loc not in str(rec.get("tom_tat", "")).lower():
                    continue
                if truoc_dt is not None:
                    rec_dt = _luc_cua(rec)
                    if rec_dt is None or rec_dt >= truoc_dt:
                        continue
                ket_qua.append(rec)
                if len(ket_qua) >= gioi_han:
                    return ket_qua
        return ket_qua
    finally:
        for f in tep:
            f.close()


def danh_sach_kenh() -> list[dict]:
    """Danh sách các kênh đang có bản ghi: [{kenh, so_dong, lan_cuoi}]. Mã lạ xuất hiện nguyên văn."""
    thong_ke: dict[str, dict] = {}
    tep = _mo_anh_chup()
    try:
        for f in tep:
            for raw in f:
                rec = _ban_ghi(raw) if raw.strip() else None
                if rec is None:
                    continue
                k = rec.get("kenh")
                luc = rec.get("luc", "")
                if not k or not isinstance(k, str) or not isinstance(luc, str):
                    continue
                if k not in thong_ke:
                    thong_ke[k] = {"kenh": k, "so_dong": 0, "lan_cuoi": luc}
                thong_ke[k]["so_dong"] += 1
                if luc > thong_ke[k]["lan_cuoi"]:
                    thong_ke[k]["lan_cuoi"] = luc
    finally:
        for f in tep:
            f.close()
    return sorted(thong_ke.values(), key=lambda x: x["kenh"])


def xoa_cu(truoc_ngay: float) -> int:
    """Xoá các bản ghi cũ hơn `truoc_ngay` ngày (0 ≤ truoc_ngay, hữu hạn). Trả về số dòng đã xoá.

    Giữ khoá suốt lúc viết lại (không cho `ghi` chen giữa đọc và thay tệp) — thao tác quản trị hiếm; `ghi` của các
    lượt /v1 chờ trong lúc đó. Tệp bị thay (inode mới) nên bộ đếm dòng tự đếm lại ở lần ghi sau."""
    if not math.isfinite(truoc_ngay) or truoc_ngay < 0:
        raise ValueError("truoc_ngay phải là số hữu hạn ≥ 0")
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=truoc_ngay)
    except OverflowError:  # xa hơn mọi ngày biểu diễn được: không có gì cũ hơn
        return 0
    da_xoa = 0
    with _KHOA:
        for p in _cac_tep_doc():
            try:
                with open(p, "rb") as f:
                    dong_cac = f.readlines()
            except (FileNotFoundError, NotADirectoryError):
                continue
            giu = []
            for raw in dong_cac:
                rec = _ban_ghi(raw) if raw.strip() else None
                rec_dt = _luc_cua(rec) if rec is not None else None
                if rec_dt is not None and rec_dt < cutoff:
                    da_xoa += 1
                    continue
                if raw.strip():
                    giu.append(raw)
            if len(giu) == len(dong_cac):
                continue
            if not giu and p != _p_hien_tai():
                p.unlink(missing_ok=True)
            else:
                kho.ghi_nguyen_tu(p, b"".join(giu).decode("utf-8", errors="replace"), quyen=0o600)
    return da_xoa


# ───────────── Middleware ASGI cho kênh cuoc_goi ─────────────
#
# Middleware KHÔNG đọc thân yêu cầu: `model`, module đã phục vụ và người gọi do chính đường /v1 ghi vào
# `request.state` (= scope["state"]) sau khi lõi đã phân tích thân theo cấu trúc (app/tuyen.py: _module_theo_model,
# _module_theo_ma_video; app/xac_thuc.py: can_quan_tri). Không dò chữ trong thân, không nhân đôi thân trong bộ nhớ.

def _trich_xuat_usage_json(data_bytes: bytes) -> dict | None:
    try:
        j = json.loads(data_bytes)
    except ValueError:
        return None
    return j["usage"] if isinstance(j, dict) and isinstance(j.get("usage"), dict) else None


def _trich_xuat_usage_sse(chunk: bytes) -> dict | None:
    """`usage` ở khúc SSE cuối (theo cấu trúc: dòng `data:` là đối tượng JSON có khoá `usage` ở gốc)."""
    if b"usage" not in chunk:  # lọc rẻ trước khi phân tích; quyết định vẫn theo cấu trúc JSON bên dưới
        return None
    for line in reversed(chunk.decode("utf-8", errors="ignore").splitlines()):
        line = line.strip()
        if line.startswith("data:"):
            try:
                j = json.loads(line[5:].strip())
            except ValueError:
                continue
            if isinstance(j, dict) and isinstance(j.get("usage"), dict):
                return j["usage"]
    return None


def _request_id(scope: Scope) -> str:
    for k, v in scope.get("headers", []):
        if k.lower() == b"x-request-id":
            gia_tri = v.decode("latin-1").strip()
            if _REQ_ID_HOP_LE.match(gia_tri):
                return gia_tri
            break
    # 64 bit ngẫu nhiên, ngắt bằng «.» để KHÔNG mang dạng token (che_chuoi che mọi chuỗi [A-Za-z0-9_-]{20,}):
    # mã này phải hiện nguyên trong nhật ký để khớp với tiêu đề. Mã khách gửi dài kiểu token thì vẫn bị che — chuỗi
    # khách điều khiển, có thể mang khoá.
    return f"req.{secrets.token_hex(8)}"


class MiddlewareNhatKyCuocGoi:
    """Middleware ASGI thuần ghi đúng một dòng `cuoc_goi` cho mỗi lượt /v1/* — kể cả lỗi, 401, 404, khách cắt
    giữa stream, tác vụ bị huỷ (dòng ghi ở `finally`). Thêm `x-request-id` (của khách nếu hợp lệ, không thì sinh;
    luôn ĐÈ giá trị module tự đặt để mã trong tiêu đề khớp mã trong nhật ký) và `X-Web2api-Module` (tiền tố module
    đã phục vụ, do lõi ghi nhận — module không tự khai được)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not la_duong_v1(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        bat_dau = time.perf_counter()
        req_id = _request_id(scope)
        trang_thai = scope.setdefault("state", {})  # cùng dict với request.state ở đường /v1
        ma_http: int | None = None
        xong = False  # đã gửi trọn khúc cuối (more_body=False)
        loai_than = None
        than_json: list[bytes] = []
        co_than_json = 0
        usage = None
        loi = False

        async def gui(msg: Message) -> None:
            nonlocal ma_http, xong, loai_than, co_than_json, usage
            if msg["type"] == "http.response.start":
                ma_http = msg.get("status", 200)
                headers = [(k, v) for k, v in msg.get("headers", [])
                           if k.lower() not in (b"x-request-id", b"x-web2api-module")]
                for k, v in headers:
                    if k.lower() == b"content-type":
                        v = v.lower()
                        loai_than = "json" if b"application/json" in v else "sse" if b"text/event-stream" in v else None
                headers.append((b"x-request-id", req_id.encode("ascii")))
                tien_to = trang_thai.get("tien_to_module")
                if tien_to:  # TIEN_TO_HOP_LE của manifest: chỉ ASCII
                    headers.append((b"x-web2api-module", str(tien_to).encode("ascii", errors="replace")))
                msg = {**msg, "headers": headers}
            elif msg["type"] == "http.response.body":
                b = msg.get("body", b"")
                if loai_than == "json" and b and co_than_json <= TRAN_THAN_USAGE:
                    than_json.append(b)
                    co_than_json += len(b)
                elif loai_than == "sse" and b:
                    usage = _trich_xuat_usage_sse(b) or usage
                if not msg.get("more_body", False) and loai_than == "json" and co_than_json <= TRAN_THAN_USAGE:
                    usage = _trich_xuat_usage_json(b"".join(than_json))
            await send(msg)
            if msg["type"] == "http.response.body" and not msg.get("more_body", False):
                xong = True

        try:
            await self.app(scope, receive, gui)
        except Exception:
            loi = True
            raise
        finally:
            if trang_thai.get("khach_id"):
                from app.khoa_khach import ra_dong_thoi
                ra_dong_thoi(trang_thai.get("khach_id"))
            ms_chay = round((time.perf_counter() - bat_dau) * 1000, 1)
            dut = ma_http is not None and not xong  # đã gửi đầu phản hồi mà luồng không trọn
            ma = ma_http if ma_http is not None else (500 if loi else 499)
            muc = "loi" if ma >= 500 else "canh_bao" if ma >= 400 or dut else "thong_tin"
            them: dict[str, Any] = {
                "request_id": req_id,
                "nguoi_goi": trang_thai.get("nguoi_goi") or "chua_xac_thuc",
                "duong": scope.get("path", ""),
                "ma_http": ma,
                "model": trang_thai.get("model_khach"),
                "module": trang_thai.get("module"),
                "tien_to": trang_thai.get("tien_to_module"),
                "usage": usage,
            }
            if dut:
                them["dut"] = True
            ghi("cuoc_goi", muc, f"{scope.get('method', '')} {scope.get('path', '')} {ma}"
                + (" (luồng đứt giữa chừng)" if dut else ""), ms=ms_chay, **them)
