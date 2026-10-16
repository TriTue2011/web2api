"""Module chatgpt-web: trò chuyện với ChatGPT bản web (chatgpt.com).

Xác thực bằng Bearer access token do NGƯỜI DÙNG dán vào (trường ``tai_khoan``) —
không tự đăng nhập, không tự động hoá trình duyệt. Hỗ trợ nhiều token và tự
chuyển tài khoản khi lỗi, phân loại lỗi bằng mã HTTP (không dò chữ).

KHÁC các module web trước: mỗi cấu hình có thể gắn ``danh_tinh_id`` của kho
danh tính lõi (``app/danh_tinh.py``) — một Gmail nhập một lần ở lõi, module chỉ
giữ token của MÌNH trỏ về danh tính đó. Khi gắn, module tự ghi mình vào sổ
``gan`` của danh tính (lũy đẳng) để lõi chặn xoá danh tính còn tài khoản trỏ tới.

Luồng giao tiếp học từ kho tham khảo chatgpt2api
(``services/openai_backend_api.py``, ``services/account_service.py``,
``services/codex_token_refresh.py``): GET /backend-api/me kiểm token →
POST /backend-api/conversation → bóc SSE. Xem client.py để biết chi tiết.
CHƯA KIỂM với dịch vụ thật.
"""

from __future__ import annotations

import json
import logging
import threading
import time

from app.kho import che
from app.loi_openai import loai_theo_ma, nhac_lai, than_loi
from app.tu_xa import kiem_url_lan

from .client import (
    MAY_CHU_MAC_DINH,
    MO_HINH_HO_TRO,
    ChatGPTWebClient,
    LoiChatGPTWeb,
    LoiHanMuc,
    LoiHetGio,
    LoiMayChu,
    LoiXacThuc,
    ModelKhongCo,
    chuyen_thanh_tin_nhan,
)
from .converter import (
    TIEN_TO,
    chunk_delta,
    chunk_ket_thuc,
    chunk_loi,
    chunk_vai_tro,
    dong_done,
    giai_quyet_model_day_du,
    id_day_du,
    phan_hoi_json,
    tach_tien_to,
    tao_hoan_thanh_id,
)

log = logging.getLogger("web2api.module.chatgpt-web")


def _lay_danh_tinh(id_: str):
    """Bản ghi công khai của danh tính; None nếu không có. Bọc để test chích."""
    from app import danh_tinh
    return danh_tinh.lay(id_)


def _gan_danh_tinh(id_: str, module_id: str = TIEN_TO):
    """Ghi module vào sổ gắn của danh tính (lũy đẳng). Bọc để test chích."""
    from app import danh_tinh
    return danh_tinh.gan(id_, module_id)


def _che_email(email: str) -> str:
    """Che email để hiển thị theo danh tính: giữ ký tự đầu + …@ + miền."""
    email = str(email or "")
    if "@" not in email:
        return "…"
    dau, mien = email.split("@", 1)
    return f"{dau[:1]}…@{mien}" if dau else f"…@{mien}"


class _TaiKhoan:
    """Một Bearer token: client tạo lười, trạng thái sức khoẻ do module giữ."""

    def __init__(self, token: str):
        self.token = token
        self.client: ChatGPTWebClient | None = None
        self.khoe = True
        self.loai_loi: str | None = None  # khoa_hong | het_han_muc | may_chu_loi | None

    def ten_che(self) -> str:
        return che(self.token)


class Module:
    """Lớp Module theo hợp đồng app/kham_pha.py."""

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to", TIEN_TO)
        self.kho = None  # lõi gán sau khi tạo (kho riêng của module)
        self._khoa = threading.Lock()
        self._tai_khoan: list[_TaiKhoan] = []
        self._co_so_url: str | None = None
        self._danh_tinh_id: str | None = None

    # -- tạo client: mối nối để test chích máy chủ giả ---------------------

    def _tao_client(self, token: str) -> ChatGPTWebClient:
        return ChatGPTWebClient(token, base_url=self._co_so_url or MAY_CHU_MAC_DINH)

    # -- danh tính chung ----------------------------------------------------

    def _kiem_danh_tinh(self, danh_tinh_id: str | None) -> dict | None:
        """Kiểm danh tính tồn tại; không tồn tại → ValueError với thông điệp rõ."""
        if not danh_tinh_id:
            return None
        ban_ghi = _lay_danh_tinh(danh_tinh_id)
        if ban_ghi is None:
            raise ValueError(
                f"danh_tinh_id {danh_tinh_id!r} không tồn tại trong kho danh tính "
                "— tạo trước ở /api/danh-tinh rồi gắn lại.")
        return ban_ghi

    def _thong_tin_danh_tinh(self) -> dict | None:
        """Thông tin hiển thị theo danh tính (email đã che)."""
        if not self._danh_tinh_id:
            return None
        ban_ghi = _lay_danh_tinh(self._danh_tinh_id) or {}
        email = str(ban_ghi.get("email") or "")
        return {"id": self._danh_tinh_id,
                "nhan": str(ban_ghi.get("nhan") or ""),
                "email_che": _che_email(email)}

    # -- hợp đồng lõi -------------------------------------------------------

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM — không gọi mạng, chỉ tách token."""
        cau_hinh = cau_hinh or {}
        danh_sach = cau_hinh.get("tai_khoan") or []
        if isinstance(danh_sach, str):
            danh_sach = [danh_sach]
        tai_khoan = [_TaiKhoan(str(t).strip()) for t in danh_sach
                     if isinstance(t, str) and str(t).strip()]
        danh_tinh_id = str(cau_hinh.get("danh_tinh_id") or "").strip() or None
        co_so = str(cau_hinh.get("co_so_url") or "").strip().rstrip("/") or None
        with self._khoa:
            self._tai_khoan = tai_khoan
            self._danh_tinh_id = danh_tinh_id
            self._co_so_url = co_so
        log.info("chatgpt-web: đã nạp %d tài khoản%s", len(tai_khoan),
                 f" (danh tính {danh_tinh_id})" if danh_tinh_id else "")

    async def _kiem_co_so_url(self) -> None:
        if self._co_so_url:
            try:
                self._co_so_url = await kiem_url_lan(self._co_so_url)
            except ValueError as e:
                raise ValueError(f"co_so_url không hợp lệ: {e}") from None

    async def _init_tai_khoan(self, tk: _TaiKhoan) -> None:
        if tk.client is None:
            tk.client = self._tao_client(tk.token)
        await tk.client.init()

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm từng token bằng /backend-api/me (rẻ, không tốn lượt chat)."""
        self.nap(cau_hinh)
        try:
            await self._kiem_co_so_url()
        except ValueError as e:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": str(e)}
        try:
            self._kiem_danh_tinh(self._danh_tinh_id)
        except ValueError as e:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": str(e)}
        with self._khoa:
            ds = list(self._tai_khoan)
            danh_tinh_id = self._danh_tinh_id
        if not ds:
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": "Chưa nhập token nào (trường tai_khoan)."}
        ket_qua = []
        tot = []
        for i, tk in enumerate(ds):
            bat_dau = time.monotonic()
            try:
                await self._init_tai_khoan(tk)
            except LoiXacThuc:
                tk.khoe = False
                tk.loai_loi = "khoa_hong"
                ket_qua.append({"khoa": tk.ten_che(), "ok": False,
                                "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                                "loi": "Token bị từ chối (401/403) — dán lại token mới."})
                log.warning("chatgpt-web: tài khoản %s bị từ chối ở bước cài", tk.ten_che())
            except LoiChatGPTWeb as e:
                tk.khoe = False
                tk.loai_loi = "may_chu_loi"
                ket_qua.append({"khoa": tk.ten_che(), "ok": False,
                                "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                                "loi": f"Tạm thời không kiểm được: {e}"})
            except Exception as e:  # không để ngoại lệ lạ vỡ bước cài
                tk.khoe = False
                tk.loai_loi = "may_chu_loi"
                ket_qua.append({"khoa": tk.ten_che(), "ok": False,
                                "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                                "loi": f"Lỗi không ngờ: {type(e).__name__}"})
                log.warning("chatgpt-web: tài khoản %s lỗi không ngờ ở bước cài: %s",
                            tk.ten_che(), type(e).__name__)
            else:
                tk.khoe = True
                tk.loai_loi = None
                tot.append(i)
                ket_qua.append({"khoa": tk.ten_che(), "ok": True,
                                "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                                "loi": None})
        if danh_tinh_id and tot:
            # Gắn vào sổ của danh tính để lõi biết còn tài khoản trỏ tới
            # (chặn xoá danh tính khi còn tài khoản — NV72).
            try:
                _gan_danh_tinh(danh_tinh_id)
            except Exception as e:
                log.warning("chatgpt-web: không gắn được danh tính %s: %s",
                            danh_tinh_id, type(e).__name__)
        if not tot:
            return {"ok": False, "ket_qua_tung_khoa": ket_qua,
                    "loi": "Không tài khoản nào dùng được (xem chi tiết từng khoá)."}
        ds_token = cau_hinh.get("tai_khoan") or []
        luu = {"tai_khoan": [ds_token[i] for i in tot if i < len(ds_token)]}
        if danh_tinh_id:
            luu["danh_tinh_id"] = danh_tinh_id
        if self._co_so_url:
            luu["co_so_url"] = self._co_so_url
        return {"ok": True, "ket_qua_tung_khoa": ket_qua, "loi": None,
                "cau_hinh_luu": luu}

    def phan_loai_loi(self, ma: int, loi: dict, boi_canh: dict) -> str | None:
        """Phân loại bằng mã HTTP — không dò chữ trong thông điệp."""
        if ma in (401, 403):
            return "khoa_hong"
        if ma == 429:
            return "het_han_muc"
        if ma == 400:
            return "yeu_cau_sai"
        if 500 <= ma <= 599:
            return "may_chu_loi"
        return None  # để lõi dùng phân loại mặc định

    # -- chat ---------------------------------------------------------------

    def _thu_tu_tai_khoan(self) -> list[_TaiKhoan]:
        """Tài khoản khoẻ trước; tài khoản từng lỗi đứng sau nhưng KHÔNG BAO GIỜ
        bị chặn hẳn — hết lựa chọn khoẻ thì vẫn thử chúng (lựa chọn cuối)."""
        with self._khoa:
            ds = list(self._tai_khoan)
        return sorted(ds, key=lambda t: 0 if t.khoe else 1)

    def _danh_dau_loi(self, tk: _TaiKhoan, loai: str, chi_so: int) -> None:
        tk.khoe = False
        tk.loai_loi = loai
        log.warning("chatgpt-web: tài khoản #%d (%s) lỗi %s, chuyển tài khoản",
                    chi_so, tk.ten_che(), loai)

    async def chat(self, body: dict, stream: bool):
        body = dict(body or {})
        model_goc = str(body.get("model") or "")
        try:
            ten_model = giai_quyet_model_day_du(model_goc)
        except ModelKhongCo:
            thong_diep = (f"model {nhac_lai(tach_tien_to(model_goc))} không có; "
                          f"chọn trong: {', '.join(id_day_du(m) for m in MO_HINH_HO_TRO)}")
            return 400, {"Content-Type": "application/json"}, \
                json.dumps(than_loi(400, thong_diep), ensure_ascii=False).encode("utf-8")
        model_id = id_day_du(ten_model)
        tin_nhan = chuyen_thanh_tin_nhan(body)
        if not tin_nhan:
            return 400, {"Content-Type": "application/json"}, \
                json.dumps(than_loi(400, "thiếu messages (danh sách hội thoại rỗng)"),
                           ensure_ascii=False).encode("utf-8")

        ds = self._thu_tu_tai_khoan()
        if not ds:
            return 401, {"Content-Type": "application/json"}, \
                json.dumps(than_loi(401, "chatgpt-web chưa có tài khoản nào: "
                                         "thêm token ở trường tai_khoan rồi cài lại."),
                           ensure_ascii=False).encode("utf-8")
        if stream:
            tieu_de = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                       "X-Accel-Buffering": "no"}
            return 200, tieu_de, self._luong_chat(ds, tin_nhan, model_id, ten_model)

        loi_cuoi: LoiChatGPTWeb | None = None
        for i, tk in enumerate(ds):
            try:
                if tk.client is None:
                    tk.client = self._tao_client(tk.token)
                van_ban = await tk.client.generate_content(tin_nhan, ten_model)
            except LoiXacThuc as e:
                self._danh_dau_loi(tk, "khoa_hong", i)
                loi_cuoi = e
                continue
            except LoiHanMuc as e:
                self._danh_dau_loi(tk, "het_han_muc", i)
                loi_cuoi = e
                continue
            except LoiHetGio as e:
                self._danh_dau_loi(tk, "may_chu_loi", i)
                loi_cuoi = e
                continue
            except LoiMayChu as e:
                self._danh_dau_loi(tk, "may_chu_loi", i)
                loi_cuoi = e
                continue
            except Exception as e:  # phòng thủ: không để ngoại lệ lạ thành 500 vỡ lõi
                log.warning("chatgpt-web: lỗi không ngờ ở tài khoản #%d: %s", i, type(e).__name__)
                loi_cuoi = LoiMayChu(f"lỗi không ngờ: {type(e).__name__}")
                continue
            tk.khoe = True
            tk.loai_loi = None
            return 200, {"Content-Type": "application/json"}, phan_hoi_json(model_id, van_ban)

        # Hết tài khoản: mã theo lỗi cuối (không bao giờ lộ token hay đáp án thô).
        ma = loi_cuoi.ma if loi_cuoi else 502
        thong_diep = {
            401: "Mọi tài khoản chatgpt-web đều bị từ chối — token hết hạn hoặc không hợp lệ, dán lại.",
            429: "Mọi tài khoản chatgpt-web đều bị giới hạn lượt — thử lại sau.",
            504: "chatgpt.com không phản hồi kịp.",
        }.get(ma, "chatgpt.com lỗi phía máy chủ.")
        return ma, {"Content-Type": "application/json"}, \
            json.dumps(than_loi(ma, thong_diep), ensure_ascii=False).encode("utf-8")

    async def _luong_chat(self, ds: list[_TaiKhoan], tin_nhan: list[dict],
                          model_id: str, ten_model: str):
        """SSE OpenAI; xoay tài khoản ngay trong luồng khi tài khoản đầu hỏng."""
        hoan_thanh_id = tao_hoan_thanh_id()
        tao_luc = int(time.time())
        yield chunk_vai_tro(model_id, hoan_thanh_id, tao_luc)
        loi_cuoi: LoiChatGPTWeb | None = None
        for i, tk in enumerate(ds):
            try:
                if tk.client is None:
                    tk.client = self._tao_client(tk.token)
                da_ra_du_lieu = False
                async for delta in tk.client.generate_content_stream(tin_nhan, ten_model):
                    da_ra_du_lieu = True
                    yield chunk_delta(model_id, hoan_thanh_id, tao_luc, delta)
            except LoiXacThuc as e:
                self._danh_dau_loi(tk, "khoa_hong", i)
                loi_cuoi = e
                if da_ra_du_lieu:
                    break  # đã ra dữ liệu thì không trộn hai tài khoản vào một luồng
                continue
            except LoiChatGPTWeb as e:
                self._danh_dau_loi(tk, "het_han_muc" if isinstance(e, LoiHanMuc) else "may_chu_loi", i)
                loi_cuoi = e
                if da_ra_du_lieu:
                    break
                continue
            except Exception as e:
                log.warning("chatgpt-web: lỗi không ngờ khi stream ở tài khoản #%d: %s",
                            i, type(e).__name__)
                loi_cuoi = LoiMayChu(f"lỗi không ngờ: {type(e).__name__}")
                break
            tk.khoe = True
            tk.loai_loi = None
            yield chunk_ket_thuc(model_id, hoan_thanh_id, tao_luc)
            yield dong_done()
            return
        ma = loi_cuoi.ma if loi_cuoi else 502
        yield chunk_loi(f"{loai_theo_ma(ma)}: không còn tài khoản chatgpt-web dùng được "
                        f"(mã {ma}) — xem nhật ký máy chủ.")
        yield dong_done()

    # -- models / suc_khoe / dung --------------------------------------------

    async def models(self):
        return [{"id": id_day_du(m), "object": "model",
                 "created": 1760000000, "owned_by": "chatgpt-web"}
                for m in MO_HINH_HO_TRO]

    async def suc_khoe(self):
        with self._khoa:
            ds = list(self._tai_khoan)
        tt_danh_tinh = self._thong_tin_danh_tinh()
        muc = []
        for i, tk in enumerate(ds, start=1):
            bat_dau = time.monotonic()
            try:
                await self._init_tai_khoan(tk)
            except LoiXacThuc as e:
                muc.append({"khoa": tk.ten_che(), "ok": False, "thu_tu": i,
                            "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                            "so_model": 0, "loi": str(e), "danh_tinh": tt_danh_tinh})
            except LoiChatGPTWeb as e:
                muc.append({"khoa": tk.ten_che(), "ok": False, "thu_tu": i,
                            "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                            "so_model": 0, "loi": str(e), "danh_tinh": tt_danh_tinh})
            except Exception as e:
                muc.append({"khoa": tk.ten_che(), "ok": False, "thu_tu": i,
                            "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                            "so_model": 0, "loi": type(e).__name__, "danh_tinh": tt_danh_tinh})
            else:
                muc.append({"khoa": tk.ten_che(), "ok": True, "thu_tu": i,
                            "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                            "so_model": len(MO_HINH_HO_TRO), "loi": None,
                            "danh_tinh": tt_danh_tinh})
        ok = any(m["ok"] for m in muc)
        return {"ok": ok, "khoa": muc,
                "ghi_chu": None if ds else "chưa cấu hình tài khoản nào"}

    async def dung(self):
        with self._khoa:
            ds = list(self._tai_khoan)
            self._tai_khoan = []
        for tk in ds:
            if tk.client is not None:
                try:
                    await tk.client.close()
                except Exception as e:
                    # Ghi nhật ký thay vì nuốt lặng (test NV45a chốt danh sách
                    # nuốt lỗi: điểm mới chưa kiểm toán sẽ làm đỏ).
                    log.warning("chatgpt-web: lỗi khi đóng client ở bước dung: %s",
                                type(e).__name__)
