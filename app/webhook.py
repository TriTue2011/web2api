"""Cửa webhook chung của lõi: `POST /api/webhook/{module}/{ma}`.

LÝ DO MỞ (không xác thực Bearer — luật bảo mật số 2 của kho): nhà cung cấp dịch vụ
bên ngoài (kênh nhắn tin, nền tảng thanh toán…) gọi vào bằng URL bí mật riêng của
module; họ không có khoá quản trị/khoá khách của web2api. Xác thực thay thế = bí mật
webhook của module so bằng `hmac.compare_digest` (+ chữ ký HMAC trên thân nếu module
khai). Vì đường mở, mọi biện pháp bù đắp đều bật: một phản hồi 404 trống DUY NHẤT
cho mọi lý do sai (không lộ module nào tồn tại, thời gian tương đương), trần byte
đọc TRƯỚC khi phân tích, chống phát lại theo id sự kiện, hạn dòng + đồng thời theo
module, và đếm sai xác thực theo IP qua `app/gioi_han.py` (sai nhiều ⇒ khoá IP như
khi đoán Bearer). Nội dung tới module đóng dấu `nguon_ngoai: true` và KHÔNG bao giờ
là chỉ thị; nhật ký KHÔNG ghi nội dung hay bí mật.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from collections import deque

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app import gioi_han as gioi_han_mod
from app import kho, nhat_ky
from app.loi_openai import LoiOpenAI
from app.than_co_tran import Tran, doc_het

log = logging.getLogger("web2api.webhook")

router = APIRouter(prefix="/api/webhook")

# ───────────── cấu hình (đọc mỗi lượt, đổi không cần khởi động lại) ─────────────

_TRAN_MAC_DINH_BYTE = 65536          # trần thân mặc định theo manifest (webhook.tran_byte)
_TTL_PHAT_LAI_GIAY = 10 * 60         # nhớ id sự kiện 10 phút
_TRAN_PHAT_LAI = 10000               # trần bộ nhớ chống phát lại
_TEN_PHUONG_THUC_MAC_DINH = "su_kien_id"


def _so_thuc(ten: str, mac_dinh: float, toi_thieu: float = 0.0) -> float:
    gia_tri = os.environ.get(ten, "").strip()
    if not gia_tri:
        return mac_dinh
    try:
        so = float(gia_tri)
        if not (so > toi_thieu) or so != so:  # bắt cả nan/inf/âm
            raise ValueError
        return so
    except ValueError:
        log.warning("%s=%r không hợp lệ, dùng mặc định %s", ten, gia_tri[:40], mac_dinh)
        return mac_dinh


def _tran_than_mb() -> float:
    return _so_thuc("WEB2API_WEBHOOK_THAN_TOI_DA_MB", 1.0)


def _tin_phut() -> int:
    return int(_so_thuc("WEB2API_WEBHOOK_TIN_PHUT", 60))


def _dong_thoi() -> int:
    return int(_so_thuc("WEB2API_WEBHOOK_DONG_THOI", 4))


def _thoi_gian_giay() -> float:
    return _so_thuc("WEB2API_WEBHOOK_THOI_GIAN_GIAY", 30)


# ───────────── trạng thái trong bộ nhớ (mất khi khởi động lại — đúng thiết kế) ─────────────

_KHOA = threading.Lock()
_DA_THAY: dict[tuple[str, str], float] = {}   # (module, id_su_kien) -> monotonic lúc thấy
_HAN_DONG: dict[str, deque] = {}              # module -> mốc thời gian các tin trong 60s
_DANG_CHAY: dict[str, int] = {}               # module -> số lượt đang xử lý


def _dat_lai_trang_thai() -> None:
    """Xoá trạng thái — chỉ dùng cho test."""
    with _KHOA:
        _DA_THAY.clear()
        _HAN_DONG.clear()
        _DANG_CHAY.clear()


# ───────────── tra cứu module và bí mật ─────────────

def _lay_webhook(so, module_id: str):
    """(đối tượng module, cấu hình webhook) hoặc None nếu module không chạy/không khai webhook."""
    try:
        m = so.dang_chay.get(module_id)
    except Exception:
        return None
    if m is None:
        return None
    km = so.catalog.get(module_id)
    man = getattr(km, "manifest", None)
    cfg = man.get("webhook") if isinstance(man, dict) else None
    if not isinstance(cfg, dict):
        return None
    return m, cfg


def _lay_bi_mat(module_id: str, khoa_bi_mat: str) -> str | None:
    """Giá trị bí mật webhook đã cấu hình, hoặc None nếu chưa cấu hình (đóng khi lỗi)."""
    try:
        bm = kho.doc_bi_mat(module_id)
    except kho.KhoHong:
        log.warning("kho bí mật của module %s hỏng — từ chối webhook (đóng khi lỗi)", module_id)
        return None
    except OSError as e:
        log.warning("không đọc được kho bí mật của module %s: %s", module_id, e)
        return None
    gia_tri = bm.get(khoa_bi_mat) if isinstance(bm, dict) else None
    return gia_tri if isinstance(gia_tri, str) and gia_tri else None


def _404_trong() -> Response:
    """Một phản hồi 404 trống DUY NHẤT cho mọi lý do sai — không lộ gì thêm."""
    return Response(status_code=404)


def _so_sanh(ma: str, bi_mat: str) -> bool:
    return hmac.compare_digest(ma.encode("utf-8"), bi_mat.encode("utf-8"))


# Bí mật giả để cân bằng thời gian khi module không tồn tại/không khai webhook:
# kẻ dò không phân biệt được «module không có» với «bí mật sai» qua thời gian.
_MA_GIA = "0" * 64


def _ghi_nhat_ky(module: str, ket_qua: str, do_dai: int, id_su_kien: str | None,
                 ms: float | None = None) -> None:
    muc = "thong_tin" if ket_qua in ("ok", "phat_lai") else "canh_bao"
    id_bam = hashlib.sha256(f"{module}:{id_su_kien}".encode()).hexdigest()[:16] if id_su_kien else None
    nhat_ky.ghi("webhook", muc, f"webhook {module} -> {ket_qua}", ms=ms,
                module=module, ket_qua=ket_qua, do_dai=do_dai, id_bam=id_bam)


def _kiem_chu_ky(cfg: dict, bi_mat: str, than_tho: bytes, headers) -> bool:
    """Kiểm chữ ký HMAC trên thân nếu module khai `chu_ky: "header:TÊN"` (+ `thuat_toan`, mặc định hmac-sha256)."""
    khai = cfg.get("chu_ky")
    if khai is None:
        return True
    if not isinstance(khai, str) or not khai.startswith("header:") or not khai[7:]:
        return False
    thuat_toan = cfg.get("thuat_toan", "hmac-sha256")
    if thuat_toan != "hmac-sha256":
        return False
    ten_header = khai[7:].strip().lower()
    gia_tri = None
    for k, v in headers.items():
        if k.lower() == ten_header:
            gia_tri = v.strip()
            break
    if not gia_tri:
        return False
    ky = hmac.new(bi_mat.encode("utf-8"), than_tho, hashlib.sha256).hexdigest()
    # Chấp nhận cả dạng trần và dạng có tiền tố "sha256=" (quy ước phổ biến).
    ung_vien = (gia_tri[7:] if gia_tri.lower().startswith("sha256=") else gia_tri)
    return hmac.compare_digest(ung_vien.encode("utf-8"), ky.encode("utf-8"))


def _kiem_phat_lai(module: str, id_su_kien: str | None) -> bool:
    """True nếu (module, id) đã thấy trong 10 phút → gọi là phát lại. Ghi nhận id mới."""
    if not id_su_kien:
        return False
    bay_gio = time.monotonic()
    khoa = (module, id_su_kien)
    with _KHOA:
        han = bay_gio - _TTL_PHAT_LAI_GIAY
        # Dọn mục hết hạn; trần bộ nhớ: bỏ mục cũ nhất khi vượt trần.
        het_han = [k for k, t in _DA_THAY.items() if t <= han]
        for k in het_han:
            del _DA_THAY[k]
        if khoa in _DA_THAY:
            return True
        while len(_DA_THAY) >= _TRAN_PHAT_LAI:
            _DA_THAY.pop(next(iter(_DA_THAY)))
        _DA_THAY[khoa] = bay_gio
        return False


def _kiem_han_dong(module: str) -> None:
    """Hạn dòng theo module (mặc định 60 tin/phút) — quá thì 429 + Retry-After."""
    gioi_han = _tin_phut()
    bay_gio = time.monotonic()
    with _KHOA:
        hang = _HAN_DONG.setdefault(module, deque())
        while hang and hang[0] <= bay_gio - 60:
            hang.popleft()
        if len(hang) >= gioi_han:
            cu_nhat = hang[0]
            cho = max(1, int(60 - (bay_gio - cu_nhat)) + 1)
            raise LoiOpenAI(429, "Quá nhiều webhook cho module này — thử lại sau.",
                            loai="rate_limit_error", code="rate_limit_exceeded",
                            headers={"Retry-After": str(cho)})
        hang.append(bay_gio)


class _GiuDongThoi:
    """Đếm lượt đang xử lý theo module — quá trần (mặc định 4) thì 429, không chờ."""

    def __init__(self, module: str):
        self.module = module

    def __enter__(self):
        gioi_han = _dong_thoi()
        with _KHOA:
            dang = _DANG_CHAY.get(self.module, 0)
            if dang >= gioi_han:
                raise LoiOpenAI(429, "Module đang bận xử lý webhook — thử lại sau.",
                                loai="rate_limit_error", code="rate_limit_exceeded",
                                headers={"Retry-After": "5"})
            _DANG_CHAY[self.module] = dang + 1

    def __exit__(self, *exc):
        with _KHOA:
            _DANG_CHAY[self.module] = max(0, _DANG_CHAY.get(self.module, 1) - 1)


#: Tối đa số tin một lần webhook được chuyển vào cổng tin vào (chống một thân webhook làm bùng nổ xử lý).
TRAN_TIN_VAO_MOI_LAN = 20


async def _chuyen_tin_vao(app, module: str, ket_qua: dict) -> None:
    """Module kênh qua webhook trả thêm khoá `tin_vao` (danh sách tin chuẩn hoá, xem docs/CHUAN-MODULE.md «Tin vào») → chuyển từng tin vào
    `kenh_vao.nhan_tin` (cổng chung: chống trùng, phân quyền theo NGƯỜI GỬI, lệnh) rồi BỎ khoá đó khỏi phản hồi 200 (không phản chiếu nội dung
    tin ra ngoài). Chỉ chạy sau khi cửa đã xác thực xong; lỗi ở một tin không làm hỏng webhook (nhà cung cấp nhắn tin sẽ gửi lại nếu không nhận 200)."""
    tin_vao = ket_qua.pop("tin_vao", None)
    if not isinstance(tin_vao, list):
        return
    from app import kenh_vao   # nhập trễ: tránh vòng nhập giữa webhook và kenh_vao
    for tin in [t for t in tin_vao if isinstance(t, dict)][:TRAN_TIN_VAO_MOI_LAN]:
        try:
            await kenh_vao.nhan_tin(app, module, tin)
        except Exception as e:   # noqa: BLE001 — đã kiểm toán: không để một tin hỏng làm sập webhook; chỉ ghi tên kiểu lỗi
            log.warning("webhook %s: chuyển tin vào lỗi (%s)", module, type(e).__name__)


@router.post("/{module}/{ma}")
async def nhan_webhook(module: str, ma: str, request: Request):
    bat_dau = time.monotonic()

    def ms() -> float:
        return (time.monotonic() - bat_dau) * 1000

    # Sai xác thực nhiều từ một IP ⇒ khoá IP như khi đoán Bearer (kiểm trước mọi thứ).
    gioi_han_mod.kiem_tra_truoc_xac_thuc(request)

    so = request.app.state.so
    got = _lay_webhook(so, module)

    # Trần thân: min(trần toàn cục, tran_byte của module). Đọc TRƯỚC khi phân tích.
    tran_byte_module = _TRAN_MAC_DINH_BYTE
    if got is not None:
        tb = got[1].get("tran_byte", _TRAN_MAC_DINH_BYTE)
        if type(tb) is int and tb > 0:
            tran_byte_module = tb
    tran_byte = min(int(_tran_than_mb() * 1024 * 1024), tran_byte_module)
    tran = Tran("WEB2API_WEBHOOK_THAN_TOI_DA_MB", tran_byte / 1024 / 1024, "thân webhook")
    try:
        than_tho = await doc_het(request, tran)
    except LoiOpenAI as e:
        # Vượt trần (413): module KHÔNG được gọi; không đếm vào sai xác thực (chưa biết ai).
        if e.ma == 413:
            _ghi_nhat_ky(module, "vuot_tran", 0, None, ms())
        raise

    try:
        than = json.loads(bytes(than_tho))
    except (ValueError, RecursionError):
        than = None
    if not isinstance(than, dict):
        # Thân không phải JSON đối tượng: cùng một 404 như mọi lý do sai khác (không lộ oracle).
        _ghi_nhat_ky(module, "sai_dinh_dang", len(than_tho), None, ms())
        return _404_trong()

    if got is None:
        # Module không có/không chạy/không khai webhook: so với bí mật GIẢ để thời gian
        # tương đương trường hợp «bí mật sai», rồi trả cùng một 404.
        _so_sanh(ma, _MA_GIA)
        _ghi_nhat_ky(module, "khong_co", len(than_tho), None, ms())
        return _404_trong()

    m, cfg = got
    khoa_bi_mat = cfg.get("bi_mat")
    bi_mat = _lay_bi_mat(module, khoa_bi_mat) if isinstance(khoa_bi_mat, str) else None
    if bi_mat is None:
        # Khai webhook nhưng chưa cấu hình bí mật: ĐÓNG KHI LỖI — 503 để quản trị biết mà sửa,
        # không bao giờ mở cửa khi thiếu bí mật (không có nhánh «chỉ một bot thì cho qua»).
        _ghi_nhat_ky(module, "thieu_cau_hinh", len(than_tho), None, ms())
        raise LoiOpenAI(503, "Webhook của module chưa được cấu hình bí mật",
                        loai="server_error", code="webhook_chua_cau_hinh")

    if not _so_sanh(ma, bi_mat):
        gioi_han_mod.ghi_loi_xac_thuc(request)  # sai nhiều ⇒ khoá IP
        _ghi_nhat_ky(module, "sai_bi_mat", len(than_tho), None, ms())
        return _404_trong()

    if not _kiem_chu_ky(cfg, bi_mat, bytes(than_tho), request.headers):
        gioi_han_mod.ghi_loi_xac_thuc(request)
        _ghi_nhat_ky(module, "sai_chu_ky", len(than_tho), None, ms())
        return _404_trong()

    # Chống phát lại: module trích id sự kiện (mặc định qua phương thức su_kien_id).
    id_su_kien = None
    ten_pt = cfg.get("id_su_kien", _TEN_PHUONG_THUC_MAC_DINH)
    trich = getattr(m, ten_pt, None) if isinstance(ten_pt, str) else None
    if callable(trich):
        try:
            ung = trich(than)
            id_su_kien = ung if isinstance(ung, str) and ung else None
        except Exception as e:
            log.warning("module %s.%s ném lỗi khi trích id sự kiện: %s — bỏ qua chống phát lại",
                        module, ten_pt, e)
    if _kiem_phat_lai(module, id_su_kien):
        _ghi_nhat_ky(module, "phat_lai", len(than_tho), id_su_kien, ms())
        return Response(status_code=200)

    # Hạn dòng theo module — sau xác thực để kẻ lạ không đốt hạn mức của module.
    _kiem_han_dong(module)

    # Nội dung tới module: đóng dấu nguồn ngoài (lõi đóng, module/khách không ghi đè được)
    # và KHÔNG bao giờ là chỉ thị — module phải tự coi mọi trường là dữ liệu không tin.
    yc = dict(than)
    yc["nguon_ngoai"] = True

    try:
        with _GiuDongThoi(module):
            try:
                ket_qua = await asyncio.wait_for(m.webhook(yc), timeout=_thoi_gian_giay())
            except asyncio.TimeoutError:
                _ghi_nhat_ky(module, "het_gio", len(than_tho), id_su_kien, ms())
                raise LoiOpenAI(504, "Module xử lý webhook quá lâu",
                                loai="server_error", code="webhook_het_gio") from None
    except LoiOpenAI:
        raise
    except Exception as e:
        log.exception("module %s ném lỗi khi xử lý webhook", module)
        _ghi_nhat_ky(module, "loi_module", len(than_tho), id_su_kien, ms())
        raise LoiOpenAI(500, "Module xử lý webhook thất bại",
                        loai="server_error", code="webhook_loi_module") from e

    _ghi_nhat_ky(module, "ok", len(than_tho), id_su_kien, ms())
    if not isinstance(ket_qua, dict):
        raise LoiOpenAI(500, "Module webhook trả về sai định dạng",
                        loai="server_error", code="webhook_sai_dinh_dang")
    await _chuyen_tin_vao(request.app, module, ket_qua)
    return JSONResponse(ket_qua)
