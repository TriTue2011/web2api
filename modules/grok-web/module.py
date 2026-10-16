"""Module Grok Web — trò chuyện với Grok qua giao thức WebSocket của grok.com.

Xác thực bằng cookie của người dùng đã đăng nhập (trường cấu hình ``tai_khoan``,
danh sách — mỗi phần tử là một tài khoản). Không tự đăng nhập, không tự động
hoá trình duyệt. Hỗ trợ nhiều tài khoản và tự chuyển tài khoản khi:

- lỗi phiên (bắt tay 401/403, sự kiện ``error`` có mã thuộc nhóm phiên),
- hết lượt (``stream_error`` có kind thuộc nhóm hạn mức),
- phiên xong nhưng không trả chữ nào.

Phân loại lỗi bằng mã trạng thái/cấu trúc sự kiện, không dò chữ trong thông điệp.
Chỉ chuyển tài khoản khi CHƯA có chữ nào được gửi — đã stream dở thì dừng tại
đó để khỏi hỏi Grok hai lần. Tài khoản vừa hết lượt được nghỉ một lúc; nhưng
không bao giờ chặn lựa chọn cuối: hết đường thì vẫn dùng tài khoản ít lỗi nhất.

Cookie không bao giờ vào log hay phản hồi — log chỉ ghi chỉ số tài khoản.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import time
from typing import AsyncIterator
from urllib.parse import urlsplit

from app.chuyen_tiep import ds_khoa

from .client import (
    GOC_HTTPS_MAC_DINH,
    WS_DUONG_MAC_DINH,
    KhachGrokWeb,
    LoiGrokWeb,
    LoiGrokWebRong,
    LoiHanMucGrokWeb,
    LoiMayChuGrokWeb,
    LoiPhienGrokWeb,
    lay_uid_tu_phien,
)
from .converter import (
    CAC_CHE_DO,
    LoiGiuaLuu,
    gom_json_openai,
    hop_van_ban,
    luong_sse_openai,
    tach_che_do,
)

log = logging.getLogger("web2api.module.grok_web")

#: Tài khoản vừa hết lượt thì nghỉ chừng này trước khi được thử lại.
NGHI_SAU_HET_LUOT = 120.0


def _tach_cookie(chuoi: str) -> dict[str, str]:
    """Tách chuỗi ``k1=v1; k2=v2`` thành dict; mục hỏng bị bỏ qua."""
    ra: dict[str, str] = {}
    for muc in chuoi.split(";"):
        muc = muc.strip()
        if not muc or "=" not in muc:
            continue
        k, v = muc.split("=", 1)
        k, v = k.strip(), v.strip()
        if k and v and k not in ra:
            ra[k] = v
    return ra


def _kiem_ws_url(ws_url: str) -> str | None:
    """Kiểm địa chỉ WebSocket ghi đè: scheme ws/wss, không userinfo,
    máy là grok.com hoặc loopback (cho test). Trả về lý do lỗi hoặc None."""
    try:
        phan = urlsplit(ws_url)
    except Exception:
        return "địa chỉ không phân tích được"
    if phan.scheme not in ("ws", "wss"):
        return "scheme phải là ws hoặc wss"
    if phan.username or phan.password:
        return "không được kèm thông tin đăng nhập trong địa chỉ"
    may = (phan.hostname or "").lower()
    if not may:
        return "thiếu tên máy"
    if may in ("grok.com", "www.grok.com"):
        return None
    try:
        ip = ipaddress.ip_address(may)
    except ValueError:
        return "chỉ chấp nhận grok.com hoặc địa chỉ loopback"
    if ip.is_loopback:
        return None
    return "chỉ chấp nhận grok.com hoặc địa chỉ loopback"


def _loi_openai(thong_diep: str, loai: str, ma: str) -> bytes:
    return json.dumps(
        {"error": {"message": thong_diep, "type": loai, "code": ma}},
        ensure_ascii=False,
    ).encode("utf-8")


class Module:
    """Điểm vào của module theo hợp đồng CHUAN-MODULE."""

    def __init__(self, manifest: dict | None = None) -> None:
        manifest = manifest or {}
        self._manifest = manifest
        self._tien_to = str(manifest.get("tien_to") or "grok")
        self._tai_khoan: list[dict[str, str]] = []  # cookie từng tài khoản
        self._ws_co_so = ""
        self._goc_https = GOC_HTTPS_MAC_DINH
        self._nghi_den: dict[int, float] = {}  # chỉ số tài khoản → mốc hết nghỉ

    # ── Nạp / cài đặt ──────────────────────────────────────────────────

    def _nap_tu(self, cau_hinh: dict) -> str | None:
        """Nạp cấu hình vào bộ nhớ; trả về thông điệp lỗi hoặc None nếu ổn."""
        ds = ds_khoa(cau_hinh.get("tai_khoan"))
        tai_khoan = []
        for chuoi in ds:
            jar = _tach_cookie(chuoi)
            if not jar.get("sso"):
                return "mỗi tài khoản phải có cookie «sso»"
            tai_khoan.append(jar)
        ws_url = str(cau_hinh.get("ws_url") or "").strip()
        ws_co_so = WS_DUONG_MAC_DINH
        goc_https = GOC_HTTPS_MAC_DINH
        if ws_url:
            loi = _kiem_ws_url(ws_url)
            if loi:
                return f"ws_url không hợp lệ: {loi}"
            phan = urlsplit(ws_url)
            ws_co_so = ws_url
            goc_https = f"{'https' if phan.scheme == 'wss' else 'http'}://{phan.hostname}"
            if phan.port:
                goc_https += f":{phan.port}"
        self._tai_khoan = tai_khoan
        self._ws_co_so = ws_co_so
        self._goc_https = goc_https
        self._nghi_den = {}
        return None

    async def nap(self, cau_hinh: dict) -> None:
        loi = self._nap_tu(cau_hinh or {})
        if loi:
            raise ValueError(loi)

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm tra cấu hình cài đặt.

        Chỉ kiểm định dạng cookie (bắt buộc có ``sso``). Khi có ``ws_url`` ghi đè
        (kiểm thử/trỏ proxy) thì thử mở WebSocket rồi đóng ngay để chắc địa chỉ
        nghe được — không tạo phiên, không tốn lượt.
        """
        loi = self._nap_tu(cau_hinh or {})
        if loi:
            return {"ok": False, "loi": loi}
        if not self._tai_khoan:
            return {
                "ok": False,
                "loi": "Chưa có tài khoản nào: nhập cookie của ít nhất một tài khoản grok.com đã đăng nhập.",
            }
        if str((cau_hinh or {}).get("ws_url") or "").strip():
            try:
                khach = KhachGrokWeb(self._ws_co_so, self._tai_khoan[0], uid="kiem_tra")
                ws = await khach._mo_ws()
                await ws.close()
            except LoiPhienGrokWeb:
                # Bắt tay tới nơi nhưng máy chủ từ chối — địa chỉ vẫn nghe được.
                pass
            except Exception as e:
                return {
                    "ok": False,
                    "loi": f"Không mở được WebSocket tới {self._ws_co_so}: {e}",
                }
        return {
            "ok": True,
            "ghi_chu": f"Đã nhận {len(self._tai_khoan)} tài khoản Grok.",
        }

    # ── Xoay tài khoản ──────────────────────────────────────────────────

    def _thu_tu_tai_khoan(self) -> list[int]:
        bay_gio = time.monotonic()
        san_sang = [i for i in range(len(self._tai_khoan))
                    if self._nghi_den.get(i, 0.0) <= bay_gio]
        if san_sang:
            return san_sang
        # Không bao giờ chặn lựa chọn cuối: tất cả đang nghỉ thì vẫn thử,
        # tài khoản hết nghỉ sớm nhất trước.
        return sorted(range(len(self._tai_khoan)),
                      key=lambda i: self._nghi_den.get(i, 0.0))

    def _danh_dau_loi(self, idx: int, loi: Exception) -> None:
        if isinstance(loi, LoiHanMucGrokWeb):
            self._nghi_den[idx] = time.monotonic() + NGHI_SAU_HET_LUOT
            log.info("grok-web: tài khoản #%d hết lượt, nghỉ %ds",
                     idx, int(NGHI_SAU_HET_LUOT))
        else:
            log.info("grok-web: tài khoản #%d lỗi %s",
                     idx, type(loi).__name__)

    def _danh_dau_khoe(self, idx: int) -> None:
        self._nghi_den.pop(idx, None)

    async def _khach(self, idx: int) -> KhachGrokWeb:
        jar = self._tai_khoan[idx]
        uid = await lay_uid_tu_phien(jar, self._goc_https)
        return KhachGrokWeb(self._ws_co_so, jar, uid)

    async def _luong_van_ban(
        self, che_do: str, loi_nhan: str
    ) -> AsyncIterator[str]:
        """Yield chữ của lượt chat, tự xoay tài khoản khi lỗi TRƯỚC khi có chữ.

        Mọi lỗi xảy ra trước khi có chữ nào (lỗi phiên, hết lượt, lỗi máy chủ,
        mất kết nối…) đều chuyển sang tài khoản kế. Đã có chữ rồi mà lỗi thì
        dừng (không xoay để khỏi hỏi Grok hai lần). Hết tài khoản mà thử được
        thì ném lỗi cuối cùng.
        """
        loi_cuoi: Exception | None = None
        for idx in self._thu_tu_tai_khoan():
            da_co_chu = False
            try:
                khach = await self._khach(idx)
            except LoiGrokWeb as e:
                self._danh_dau_loi(idx, e)
                loi_cuoi = e
                continue
            try:
                async for manh in khach.chay(che_do, loi_nhan):
                    da_co_chu = True
                    yield manh
                self._danh_dau_khoe(idx)
                return
            except LoiGrokWeb as e:
                self._danh_dau_loi(idx, e)
                loi_cuoi = e
                if da_co_chu:
                    break  # đã stream dở: dừng, không xoay
                continue
        raise loi_cuoi or LoiGrokWeb("không có tài khoản nào dùng được")

    # ── Chat ───────────────────────────────────────────────────────────

    def _loi_chat(self, loi: Exception) -> tuple[int, dict, bytes]:
        tieu_de = {"content-type": "application/json"}
        if isinstance(loi, LoiPhienGrokWeb):
            return 401, tieu_de, _loi_openai(
                "Phiên Grok hết hạn hoặc cookie bị từ chối — đăng nhập lại grok.com rồi cập nhật cookie.",
                "authentication_error", "loi_phien",
            )
        if isinstance(loi, LoiHanMucGrokWeb):
            tieu_de["retry-after"] = "60"
            return 429, tieu_de, _loi_openai(
                "Tài khoản Grok đã hết lượt dùng cho chế độ này — thử lại sau hoặc thêm tài khoản khác.",
                "rate_limit_error", "het_luot",
            )
        if isinstance(loi, LoiGrokWebRong):
            return 502, tieu_de, _loi_openai(
                "Grok xong nhưng không trả chữ nào.",
                "upstream_error", "tra_loi_rong",
            )
        if isinstance(loi, LoiMayChuGrokWeb):
            return 502, tieu_de, _loi_openai(
                "Máy chủ Grok kết thúc phiên với trạng thái lỗi.",
                "upstream_error", "loi_may_chu",
            )
        return 502, tieu_de, _loi_openai(
            f"Lỗi khi gọi grok.com ({type(loi).__name__}).",
            "upstream_error", "loi_grok",
        )

    async def chat(self, body: dict, stream: bool):
        """Chuyển tiếp yêu cầu chat OpenAI tới Grok web."""
        if not self._tai_khoan:
            return 404, {"content-type": "application/json"}, _loi_openai(
                "Module grok-web chưa được cấu hình tài khoản («tai_khoan»).",
                "module_not_configured", "missing_credentials",
            )
        model = str(body.get("model") or "")
        che_do = tach_che_do(model)
        ten_model = f"{self._tien_to}/{che_do}"
        loi_nhan = hop_van_ban(body.get("messages") or [])
        if not loi_nhan.strip():
            return 400, {"content-type": "application/json"}, _loi_openai(
                "Thiếu nội dung tin nhắn.", "invalid_request_error", "missing_messages",
            )

        if stream:
            # Lấy mảnh đầu NGAY để lỗi trước stream ánh xạ đúng mã HTTP;
            # lỗi giữa stream thì kết thúc không [DONE] (ghi log).
            gen = self._luong_van_ban(che_do, loi_nhan)
            try:
                manh_dau = await gen.__anext__()
            except StopAsyncIteration:
                return self._loi_chat(LoiGrokWebRong("luồng rỗng"))
            except LoiGrokWeb as e:
                return self._loi_chat(e)

            async def tiep() -> AsyncIterator[str]:
                yield manh_dau
                try:
                    async for manh in gen:
                        yield manh
                except LoiGrokWeb as e:
                    log.warning("grok-web: lỗi giữa stream (%s) — kết thúc không [DONE]",
                                type(e).__name__)
                    raise LoiGiuaLuu() from e

            return (
                200,
                {"content-type": "text/event-stream"},
                luong_sse_openai(tiep(), ten_model),
            )

        try:
            noi_dung = await gom_json_openai(
                self._luong_van_ban(che_do, loi_nhan), ten_model
            )
        except LoiGrokWeb as e:
            return self._loi_chat(e)
        return (
            200,
            {"content-type": "application/json"},
            json.dumps(noi_dung, ensure_ascii=False).encode("utf-8"),
        )

    # ── Danh sách model ────────────────────────────────────────────────

    async def models(self) -> list[dict]:
        """Bốn chế độ của web app Grok dưới dạng model ``grok/<chế độ>``."""
        return [
            {"id": f"{self._tien_to}/{m}", "object": "model", "owned_by": "grok-web"}
            for m in CAC_CHE_DO
        ]

    # ── Sức khoẻ ──────────────────────────────────────────────────────

    async def suc_khoe(self) -> dict:
        """Tình trạng module — không để lộ cookie."""
        if not self._tai_khoan:
            return {
                "trang_thai": "chưa cấu hình",
                "chi_tiet": "Thiếu danh sách cookie tài khoản («tai_khoan»)",
            }
        return {
            "trang_thai": "sẵn sàng",
            "ghi_chu": f"{len(self._tai_khoan)} tài khoản đã cấu hình",
        }

    # ── Dừng ───────────────────────────────────────────────────────────

    async def dung(self) -> None:
        """Không có kết nối nền cần dọn."""
        self._nghi_den = {}
