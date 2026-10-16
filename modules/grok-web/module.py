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

import hashlib
import ipaddress
import json
import logging
import time
from typing import AsyncIterator
from urllib.parse import urlsplit

from app import danh_tinh
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


def _hash_cookie(chuoi: str) -> str:
    """Dấu vân tay ổn định của một bộ cookie — khoá để nhớ liên kết danh tính
    qua các lần nạp mà không phải lưu cookie hai nơi."""
    return hashlib.sha256(str(chuoi or "").strip().encode("utf-8")).hexdigest()


def _che_email(email: str) -> str:
    """Che email khi hiển thị theo danh tính: giữ ký tự đầu + miền."""
    email = str(email or "")
    cuc_bo, _, mien = email.partition("@")
    dau = (cuc_bo[:1] + "***") if cuc_bo else "***"
    return f"{dau}@{mien}" if mien else dau


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
        self.kho = None  # lõi gán sau khi tạo (kho riêng của module)
        self._dt_id: list[str | None] = []   # danh_tinh_id từng tài khoản
        self._dt_ten: list[str | None] = []  # tên hiển thị theo danh tính

    # ── Gắn danh tính chung (khuôn NV77) ──────────────────────────────────

    def _doc_lien_ket(self) -> dict:
        """Ánh xạ hash cookie → danh_tinh_id trong kho riêng của module. Đọc hỏng
        thì coi như chưa gắn (tài khoản cũ vẫn chạy), không vỡ module."""
        try:
            if self.kho is None:
                return {}
            du_lieu = self.kho.doc("danh_tinh") or {}
            anh_xa = du_lieu.get("anh_xa") or {}
            return {str(k): v for k, v in anh_xa.items() if isinstance(v, str)}
        except Exception as e:
            log.warning("grok-web: không đọc được liên kết danh tính: %s",
                        type(e).__name__)
            return {}

    def _luu_lien_ket(self, anh_xa: dict) -> None:
        if self.kho is None:
            return
        try:
            cu = self.kho.doc("danh_tinh") or {}
        except Exception:
            cu = {}
        try:
            self.kho.luu("danh_tinh", {"anh_xa": anh_xa,
                                       "gan": cu.get("gan") or [],
                                       "chu_so_huu": cu.get("chu_so_huu")})
        except Exception as e:
            log.warning("grok-web: không lưu được liên kết danh tính: %s",
                        type(e).__name__)

    def _ten_theo_danh_tinh(self, dt_id: str) -> str | None:
        """Tên hiển thị theo danh tính: nhãn do người dùng đặt, không thì email che."""
        try:
            dt = danh_tinh.lay(dt_id)
        except Exception as e:
            log.warning("grok-web: không đọc được danh tính %s: %s",
                        dt_id, type(e).__name__)
            return None
        if not dt:
            return None
        nhan = str(dt.get("nhan") or "").strip()
        if nhan:
            return nhan
        return _che_email(dt.get("email") or "")

    def _dong_bo_tham_chieu(self, tap_moi: set[str]) -> None:
        """Đăng ký/gỡ tham chiếu với kho danh tính của lõi theo chênh lệch tuyệt đối.

        Lõi chặn xoá danh tính còn tài khoản trỏ tới (409 con_tai_khoan) dựa trên
        chính các tham chiếu này. Trạng thái đã gắn + chủ sở hữu được nhớ trong
        kho riêng để dung() của thể hiện cũ không gỡ nhầm của thể hiện mới.
        """
        cu: set[str] = set()
        if self.kho is not None:
            try:
                cu = set((self.kho.doc("danh_tinh") or {}).get("gan") or [])
            except Exception as e:
                log.warning("grok-web: không đọc được trạng thái gắn danh tính: %s",
                            type(e).__name__)
        for dt_id in sorted(cu - tap_moi):
            try:
                danh_tinh.go(dt_id, "grok-web")
            except Exception as e:
                log.warning("grok-web: gỡ tham chiếu danh tính %s: %s",
                            dt_id, type(e).__name__)
        for dt_id in sorted(tap_moi - cu):
            # Đã kiểm tồn tại ở nap(); kho danh tính hỏng lúc này thì để 503 lan ra
            # (đóng khi lỗi) thay vì âm thầm chạy không tham chiếu.
            danh_tinh.gan(dt_id, "grok-web")
        if self.kho is not None:
            try:
                du_lieu = self.kho.doc("danh_tinh") or {}
                self.kho.luu("danh_tinh", {"anh_xa": du_lieu.get("anh_xa") or {},
                                           "gan": sorted(tap_moi),
                                           "chu_so_huu": id(self)})
            except Exception as e:
                log.warning("grok-web: không lưu được trạng thái gắn danh tính: %s",
                            type(e).__name__)

    # ── Nạp / cài đặt ──────────────────────────────────────────────────

    def _nap_tu(self, cau_hinh: dict) -> str | None:
        """Nạp cấu hình vào bộ nhớ; trả về thông điệp lỗi hoặc None nếu ổn.

        Khoá phụ «danh_tinh» (không khai trong manifest, lõi cho qua): danh sách
        cùng thứ tự với «tai_khoan», mỗi phần tử là id danh tính hoặc null. id lạ
        → thông điệp lỗi rõ ràng, chưa gắn gì. Không có khoá này thì tra liên kết
        đã nhớ trong kho riêng theo hash cookie (bền qua khởi động); tài khoản
        không gắn chạy y như trước.
        """
        ds = ds_khoa(cau_hinh.get("tai_khoan"))
        tai_khoan = []
        for chuoi in ds:
            jar = _tach_cookie(chuoi)
            if not jar.get("sso"):
                return "mỗi tài khoản phải có cookie «sso»"
            tai_khoan.append(jar)
        gan_moi = cau_hinh.get("danh_tinh")
        if gan_moi is not None:
            if not isinstance(gan_moi, list) or len(gan_moi) != len(ds):
                return (
                    "«danh_tinh» phải là danh sách cùng độ dài với «tai_khoan» "
                    f"(nhận {len(gan_moi) if isinstance(gan_moi, list) else type(gan_moi).__name__}, "
                    f"cần {len(ds)}); mỗi phần tử là id danh tính hoặc null.")
            for dt_id in gan_moi:
                if dt_id is None:
                    continue
                if not isinstance(dt_id, str) or danh_tinh.lay(dt_id) is None:
                    return (
                        f"danh_tinh_id {dt_id!r} không tồn tại trong sổ danh tính "
                        "(tạo trước ở «Hệ thống → AI → Danh tính»).")
        lien_ket = self._doc_lien_ket()
        dt_id_ds: list[str | None] = []
        dt_ten_ds: list[str | None] = []
        for i, chuoi in enumerate(ds):
            if gan_moi is not None:
                dt_id = gan_moi[i]
            else:
                dt_id = lien_ket.get(_hash_cookie(chuoi))
            dt_id_ds.append(dt_id)
            dt_ten_ds.append(self._ten_theo_danh_tinh(dt_id) if dt_id else None)
        if gan_moi is not None:
            for i, chuoi in enumerate(ds):
                h = _hash_cookie(chuoi)
                if dt_id_ds[i]:
                    lien_ket[h] = dt_id_ds[i]
                else:
                    lien_ket.pop(h, None)
            self._luu_lien_ket(lien_ket)
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
        self._dt_id = dt_id_ds
        self._dt_ten = dt_ten_ds
        self._ws_co_so = ws_co_so
        self._goc_https = goc_https
        self._nghi_den = {}
        self._dong_bo_tham_chieu({d for d in dt_id_ds if d})
        n_gan = sum(1 for d in dt_id_ds if d)
        log.info("grok-web: đã nạp %d tài khoản (%d gắn danh tính)",
                 len(tai_khoan), n_gan)
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
        """Gỡ tham chiếu danh tính mà CHÍNH thể hiện này đã đăng ký (khuôn NV77).
        Thể hiện thay thế (nap sau) đã ghi đè chu_so_huu nên không bị gỡ nhầm."""
        try:
            if self.kho is not None:
                du_lieu = self.kho.doc("danh_tinh") or {}
                if du_lieu.get("chu_so_huu") == id(self):
                    for dt_id in du_lieu.get("gan") or []:
                        try:
                            danh_tinh.go(dt_id, "grok-web")
                        except Exception as e:
                            log.warning("grok-web: gỡ tham chiếu danh tính %s khi dừng: %s",
                                        dt_id, type(e).__name__)
                    self.kho.luu("danh_tinh",
                                 {"anh_xa": du_lieu.get("anh_xa") or {},
                                  "gan": [], "chu_so_huu": id(self)})
        except Exception as e:
            log.warning("grok-web: dọn tham chiếu danh tính khi dừng: %s",
                        type(e).__name__)
        self._nghi_den = {}
