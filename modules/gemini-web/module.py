"""Module gemini-web: trò chuyện với Gemini bản web (gemini.google.com).

Xác thực bằng cookie Google do NGƯỜI DÙNG dán vào (trường ``cookies``) —
không tự đăng nhập, không tự động hoá trình duyệt. Hỗ trợ nhiều bộ cookie
và tự chuyển tài khoản khi lỗi, phân loại lỗi bằng mã HTTP (không dò chữ).

Luồng giao tiếp (học từ kho tham khảo chatgpt2api/api/gemini_web.py, qua gói
gemini_webapi): init lấy mã SNlM0e → POST batchexecute → bóc khung đáp án.
Xem client.py để biết chi tiết giao thức. CHƯA KIỂM với dịch vụ thật.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time

from app import danh_tinh
from app.kho import che
from app.loi_openai import loai_theo_ma, nhac_lai, than_loi
from app.tu_xa import kiem_url_lan

from .client import (
    GeminiWebClient,
    LoiGeminiWeb,
    LoiHanMuc,
    LoiHetGio,
    LoiMayChu,
    LoiXacThuc,
    MAY_CHU_MAC_DINH,
    MO_HINH_HO_TRO,
    ModelKhongCo,
)
from .converter import (
    TIEN_TO,
    chunk_delta,
    chunk_ket_thuc,
    chunk_loi,
    chunk_vai_tro,
    chuyen_thanh_prompt,
    dong_done,
    giai_quyet_model_day_du,
    id_day_du,
    phan_hoi_json,
    tach_tien_to,
    tao_hoan_thanh_id,
)

log = logging.getLogger("web2api.module.gemini-web")


def _tach_psid(chuoi: str) -> tuple[str | None, str | None]:
    """'k1=v1; k2=v2' → (__Secure-1PSID, __Secure-1PSIDTS|None)."""
    psid = psidts = None
    for phan in str(chuoi or "").split(";"):
        phan = phan.strip()
        if not phan or "=" not in phan:
            continue
        khoa, _, gia_tri = phan.partition("=")
        khoa = khoa.strip()
        gia_tri = gia_tri.strip().strip('"')
        if khoa == "__Secure-1PSID" and gia_tri:
            psid = gia_tri
        elif khoa == "__Secure-1PSIDTS" and gia_tri:
            psidts = gia_tri
    return psid, psidts


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


class _TaiKhoan:
    """Một bộ cookie: client tạo lười, trạng thái sức khoẻ do module giữ.

    Có thể gắn một danh tính chung của lõi (``danh_tinh_id``): gắn rồi thì hiển
    thị tên theo danh tính thay vì dạng che của cookie. Tài khoản không gắn
    chạy y như trước (tương thích ngược).
    """

    def __init__(self, psid: str, psidts: str | None):
        self.psid = psid
        self.psidts = psidts
        self.client: GeminiWebClient | None = None
        self.khoe = True
        self.loai_loi: str | None = None  # khoa_hong | het_han_muc | may_chu_loi | None
        self.lan_429_cuoi: str | None = None
        self.chuoi_goc: str = ""
        self.danh_tinh_id: str | None = None
        self.ten_danh_tinh: str | None = None

    def ten_che(self) -> str:
        return self.ten_danh_tinh or che(self.psid)


class Module:
    """Lớp Module theo hợp đồng app/kham_pha.py."""

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to", TIEN_TO)
        self.kho = None  # lõi gán sau khi tạo (kho riêng của module)
        self._khoa = threading.Lock()
        self._tai_khoan: list[_TaiKhoan] = []
        self._co_so_url: str | None = None

    # -- tạo client: mối nối để test chích máy chủ giả ---------------------

    def _tao_client(self, psid: str, psidts: str | None) -> GeminiWebClient:
        return GeminiWebClient(psid, psidts,
                               base_url=self._co_so_url or MAY_CHU_MAC_DINH)

    # -- gắn danh tính chung (NV77) -------------------------------------------

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
            log.warning("gemini-web: không đọc được liên kết danh tính: %s",
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
            log.warning("gemini-web: không lưu được liên kết danh tính: %s",
                        type(e).__name__)

    def _ten_theo_danh_tinh(self, dt_id: str) -> str | None:
        """Tên hiển thị theo danh tính: nhãn do người dùng đặt, không thì email che."""
        try:
            dt = danh_tinh.lay(dt_id)
        except Exception as e:
            log.warning("gemini-web: không đọc được danh tính %s: %s",
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
                log.warning("gemini-web: không đọc được trạng thái gắn danh tính: %s",
                            type(e).__name__)
        for dt_id in sorted(cu - tap_moi):
            try:
                danh_tinh.go(dt_id, "gemini-web")
            except Exception as e:
                log.warning("gemini-web: gỡ tham chiếu danh tính %s: %s",
                            dt_id, type(e).__name__)
        for dt_id in sorted(tap_moi - cu):
            # Đã kiểm tồn tại ở nap(); kho danh tính hỏng lúc này thì để 503 lan ra
            # (đóng khi lỗi) thay vì âm thầm chạy không tham chiếu.
            danh_tinh.gan(dt_id, "gemini-web")
        if self.kho is not None:
            try:
                du_lieu = self.kho.doc("danh_tinh") or {}
                self.kho.luu("danh_tinh", {"anh_xa": du_lieu.get("anh_xa") or {},
                                           "gan": sorted(tap_moi),
                                           "chu_so_huu": id(self)})
            except Exception as e:
                log.warning("gemini-web: không lưu được trạng thái gắn danh tính: %s",
                            type(e).__name__)

    # -- hợp đồng lõi -------------------------------------------------------

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM — không gọi mạng, chỉ tách cookie.

        Khoá phụ «danh_tinh» (không khai trong manifest, lõi cho qua): danh sách
        cùng thứ tự với «cookies», mỗi phần tử là id danh tính hoặc null. id lạ
        → ValueError rõ ràng, chưa gắn gì. Không có khoá này thì tra liên kết đã
        nhớ trong kho riêng theo hash cookie (bền qua khởi động); tài khoản không
        gắn chạy y như trước.
        """
        cau_hinh = cau_hinh or {}
        danh_sach = cau_hinh.get("cookies") or []
        if isinstance(danh_sach, str):
            danh_sach = [danh_sach]
        if isinstance(danh_sach, dict):
            raise ValueError("«cookies» phải là danh sách chuỗi cookie.")
        gan_moi = cau_hinh.get("danh_tinh")
        if gan_moi is not None:
            if not isinstance(gan_moi, list) or len(gan_moi) != len(danh_sach):
                raise ValueError(
                    "«danh_tinh» phải là danh sách cùng độ dài với «cookies» "
                    f"(nhận {len(gan_moi) if isinstance(gan_moi, list) else type(gan_moi).__name__}, "
                    f"cần {len(danh_sach)}); mỗi phần tử là id danh tính hoặc null.")
            for dt_id in gan_moi:
                if dt_id is None:
                    continue
                if not isinstance(dt_id, str) or danh_tinh.lay(dt_id) is None:
                    raise ValueError(
                        f"danh_tinh_id {dt_id!r} không tồn tại trong sổ danh tính "
                        "(tạo trước ở «Hệ thống → AI → Danh tính»).")
        lien_ket = self._doc_lien_ket()
        tai_khoan = []
        for i, chuoi in enumerate(danh_sach):
            psid, psidts = _tach_psid(chuoi)
            if not psid:
                log.warning("gemini-web: bỏ qua một bộ cookie thiếu __Secure-1PSID")
                continue
            tk = _TaiKhoan(psid, psidts)
            tk.chuoi_goc = chuoi if isinstance(chuoi, str) else ""
            if gan_moi is not None:
                tk.danh_tinh_id = gan_moi[i]
            else:
                tk.danh_tinh_id = lien_ket.get(_hash_cookie(tk.chuoi_goc))
            if tk.danh_tinh_id:
                tk.ten_danh_tinh = self._ten_theo_danh_tinh(tk.danh_tinh_id)
            tai_khoan.append(tk)
        if gan_moi is not None:
            for tk in tai_khoan:
                h = _hash_cookie(tk.chuoi_goc)
                if tk.danh_tinh_id:
                    lien_ket[h] = tk.danh_tinh_id
                else:
                    lien_ket.pop(h, None)
            self._luu_lien_ket(lien_ket)
        co_so = str(cau_hinh.get("co_so_url") or "").strip().rstrip("/") or None
        with self._khoa:
            self._tai_khoan = tai_khoan
            self._co_so_url = co_so
        self._dong_bo_tham_chieu({tk.danh_tinh_id for tk in tai_khoan
                                  if tk.danh_tinh_id})
        log.info("gemini-web: đã nạp %d tài khoản (%d gắn danh tính)",
                 len(tai_khoan), sum(1 for tk in tai_khoan if tk.danh_tinh_id))

    async def _kiem_co_so_url(self) -> None:
        if self._co_so_url:
            try:
                self._co_so_url = await kiem_url_lan(self._co_so_url)
            except ValueError as e:
                raise ValueError(f"co_so_url không hợp lệ: {e}") from None

    async def _init_tai_khoan(self, tk: _TaiKhoan) -> None:
        if tk.client is None:
            tk.client = self._tao_client(tk.psid, tk.psidts)
        await tk.client.init()

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm từng bộ cookie bằng init() (rẻ, không tốn lượt chat)."""
        self.nap(cau_hinh)
        try:
            await self._kiem_co_so_url()
        except ValueError as e:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": str(e)}
        with self._khoa:
            ds = list(self._tai_khoan)
        if not ds:
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": "Chưa nhập cookie nào (trường cookies)."}
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
                                "loi": "Cookie bị từ chối (401/403) — export lại cookie mới."})
                # Không log cookie: chỉ ghi dạng che.
                log.warning("gemini-web: tài khoản %s bị từ chối ở bước cài", tk.ten_che())
            except LoiGeminiWeb as e:
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
                log.warning("gemini-web: tài khoản %s lỗi không ngờ ở bước cài: %s",
                            tk.ten_che(), type(e).__name__)
            else:
                tk.khoe = True
                tk.loai_loi = None
                tot.append(i)
                ket_qua.append({"khoa": tk.ten_che(), "ok": True,
                                "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                                "loi": None})
        if not tot:
            return {"ok": False, "ket_qua_tung_khoa": ket_qua,
                    "loi": "Không tài khoản nào dùng được (xem chi tiết từng khoá)."}
        # Lưu đúng chuỗi gốc của từng tài khoản còn tốt (theo thể hiện, không theo
        # chỉ số — chỉ số lệch khi có bộ cookie bị bỏ qua lúc tách).
        luu = {"cookies": [ds[i].chuoi_goc for i in tot]}
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
        log.warning("gemini-web: tài khoản #%d (%s) lỗi %s, chuyển tài khoản",
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
        prompt = chuyen_thanh_prompt(body)
        if not prompt:
            return 400, {"Content-Type": "application/json"}, \
                json.dumps(than_loi(400, "thiếu messages (danh sách hội thoại rỗng)"),
                           ensure_ascii=False).encode("utf-8")

        ds = self._thu_tu_tai_khoan()
        if not ds:
            return 401, {"Content-Type": "application/json"}, \
                json.dumps(than_loi(401, "gemini-web chưa có tài khoản nào: "
                                         "thêm cookie Google ở trường cookies rồi cài lại."),
                           ensure_ascii=False).encode("utf-8")
        if stream:
            tieu_de = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                       "X-Accel-Buffering": "no"}
            return 200, tieu_de, self._luong_chat(ds, prompt, model_id, ten_model)

        loi_cuoi: LoiGeminiWeb | None = None
        for i, tk in enumerate(ds):
            try:
                if tk.client is None:
                    tk.client = self._tao_client(tk.psid, tk.psidts)
                van_ban = await tk.client.generate_content(prompt, ten_model)
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
                log.warning("gemini-web: lỗi không ngờ ở tài khoản #%d: %s", i, type(e).__name__)
                loi_cuoi = LoiMayChu(f"lỗi không ngờ: {type(e).__name__}")
                continue
            tk.khoe = True
            tk.loai_loi = None
            return 200, {"Content-Type": "application/json"}, phan_hoi_json(model_id, van_ban)

        # Hết tài khoản: mã theo lỗi cuối (không bao giờ lộ cookie hay đáp án thô).
        ma = loi_cuoi.ma if loi_cuoi else 502
        thong_diep = {
            401: "Mọi tài khoản gemini-web đều bị từ chối — cookie hết hạn hoặc không hợp lệ, export lại.",
            429: "Mọi tài khoản gemini-web đều bị giới hạn lượt — thử lại sau.",
            504: "gemini.google.com không phản hồi kịp.",
        }.get(ma, "gemini.google.com lỗi phía máy chủ.")
        return ma, {"Content-Type": "application/json"}, \
            json.dumps(than_loi(ma, thong_diep), ensure_ascii=False).encode("utf-8")

    async def _luong_chat(self, ds: list[_TaiKhoan], prompt: str,
                          model_id: str, ten_model: str):
        """SSE OpenAI; xoay tài khoản ngay trong luồng khi tài khoản đầu hỏng."""
        hoan_thanh_id = tao_hoan_thanh_id()
        tao_luc = int(time.time())
        yield chunk_vai_tro(model_id, hoan_thanh_id, tao_luc)
        loi_cuoi: LoiGeminiWeb | None = None
        for i, tk in enumerate(ds):
            try:
                if tk.client is None:
                    tk.client = self._tao_client(tk.psid, tk.psidts)
                da_ra_du_lieu = False
                async for delta in tk.client.generate_content_stream(prompt, ten_model):
                    da_ra_du_lieu = True
                    yield chunk_delta(model_id, hoan_thanh_id, tao_luc, delta)
            except LoiXacThuc as e:
                self._danh_dau_loi(tk, "khoa_hong", i)
                loi_cuoi = e
                if da_ra_du_lieu:
                    break  # đã ra dữ liệu thì không trộn hai tài khoản vào một luồng
                continue
            except LoiGeminiWeb as e:
                self._danh_dau_loi(tk, "het_han_muc" if isinstance(e, LoiHanMuc) else "may_chu_loi", i)
                loi_cuoi = e
                if da_ra_du_lieu:
                    break
                continue
            except Exception as e:
                log.warning("gemini-web: lỗi không ngờ khi stream ở tài khoản #%d: %s",
                            i, type(e).__name__)
                loi_cuoi = LoiMayChu(f"lỗi không ngờ: {type(e).__name__}")
                break
            tk.khoe = True
            tk.loai_loi = None
            yield chunk_ket_thuc(model_id, hoan_thanh_id, tao_luc)
            yield dong_done()
            return
        ma = loi_cuoi.ma if loi_cuoi else 502
        yield chunk_loi(f"{loai_theo_ma(ma)}: không còn tài khoản gemini-web dùng được "
                        f"(mã {ma}) — xem nhật ký máy chủ.")
        yield dong_done()

    # -- models / suc_khoe / dung --------------------------------------------

    async def models(self):
        return [{"id": id_day_du(m), "object": "model",
                 "created": 1760000000, "owned_by": "gemini-web"}
                for m in MO_HINH_HO_TRO]

    async def suc_khoe(self):
        with self._khoa:
            ds = list(self._tai_khoan)
        muc = []
        for i, tk in enumerate(ds, start=1):
            bat_dau = time.monotonic()
            try:
                await self._init_tai_khoan(tk)
            except LoiXacThuc as e:
                muc.append({"khoa": tk.ten_che(), "ok": False, "thu_tu": i,
                            "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                            "so_model": 0, "loi": str(e), "dang_nghi_den": None})
            except LoiGeminiWeb as e:
                muc.append({"khoa": tk.ten_che(), "ok": False, "thu_tu": i,
                            "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                            "so_model": 0, "loi": str(e), "dang_nghi_den": None})
            except Exception as e:
                muc.append({"khoa": tk.ten_che(), "ok": False, "thu_tu": i,
                            "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                            "so_model": 0, "loi": type(e).__name__, "dang_nghi_den": None})
            else:
                muc.append({"khoa": tk.ten_che(), "ok": True, "thu_tu": i,
                            "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                            "so_model": len(MO_HINH_HO_TRO), "loi": None,
                            "dang_nghi_den": None})
        ok = any(m["ok"] for m in muc)
        return {"ok": ok, "khoa": muc,
                "ghi_chu": None if ds else "chưa cấu hình tài khoản nào"}

    async def dung(self):
        # Gỡ tham chiếu danh tính mà CHÍNH thể hiện này đã đăng ký. Thể hiện thay
        # thế (nap sau) đã ghi đè chu_so_huu nên không bị gỡ nhầm; thể hiện tạm
        # dùng để kiểm ở bước cài cũng tự dọn sau finally.
        try:
            if self.kho is not None:
                du_lieu = self.kho.doc("danh_tinh") or {}
                if du_lieu.get("chu_so_huu") == id(self):
                    for dt_id in du_lieu.get("gan") or []:
                        try:
                            danh_tinh.go(dt_id, "gemini-web")
                        except Exception as e:
                            log.warning("gemini-web: gỡ tham chiếu danh tính %s khi dừng: %s",
                                        dt_id, type(e).__name__)
                    self.kho.luu("danh_tinh",
                                 {"anh_xa": du_lieu.get("anh_xa") or {},
                                  "gan": [], "chu_so_huu": id(self)})
        except Exception as e:
            log.warning("gemini-web: dọn tham chiếu danh tính khi dừng: %s",
                        type(e).__name__)
        with self._khoa:
            ds = list(self._tai_khoan)
            self._tai_khoan = []
        for tk in ds:
            if tk.client is not None:
                try:
                    await tk.client.close()
                except Exception:
                    pass
