"""Module claude-web — trò chuyện với Claude qua API web nội bộ của claude.ai.

Xác thực bằng cookie/sessionKey do NGƯỜI DÙNG dán (không tự đăng nhập, không
browser automation). Hỗ trợ nhiều tài khoản: xoay vòng khi gặp 429/529 (hết hạn
mức), 401/403 (cookie hỏng), hoặc lỗi tạm thời 5xx/mạng — phân loại bằng mã
trạng thái HTTP, không dò chữ trong thân phản hồi.

Bí mật: cookie chỉ đi trong header Cookie tới đúng base_url đã kiểm; log và mọi
phản hồi API chỉ nêu dạng che «…abcd», không bao giờ mang giá trị thô.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from urllib.parse import urlsplit

import httpx

from app import danh_tinh

from . import converter as cv
from .client import BASE_MAC_DINH, LoiClaudeWeb, MayKhachClaudeWeb

LOG = logging.getLogger("web2api.module.claude-web")

#: Số tài khoản tối đa thử trong một lượt chat (mọi tài khoản đã nạp đều được thử).
GIOI_HAN_LUOT = 8


def _che(cookie: str) -> str:
    """Che cookie theo quy ước kho: chỉ hiện «…abcd» (4 ký tự cuối của GIÁ TRỊ
    cookie đầu). Không hiện tên cookie; giá trị ≤ 8 ký tự → «…»."""
    dau = str(cookie or "").split(";")[0].strip()
    if "=" in dau:
        _, v = dau.split("=", 1)
        v = v.strip()
        return f"…{v[-4:]}" if len(v) > 8 else "…"
    s = str(cookie or "").strip()
    return f"…{s[-4:]}" if len(s) > 8 else "…"


def chuan_hoa_cookie(gia_tri: str) -> str:
    """Chuỗi cookie đầy đủ «k1=v1; k2=v2» giữ nguyên; giá trị trần (không có «=»)
    được coi là sessionKey."""
    s = str(gia_tri or "").strip()
    if s and "=" not in s:
        s = "sessionKey=" + s
    return s


def chuan_hoa_base_url(gia_tri) -> str:
    s = str(gia_tri or "").strip().rstrip("/")
    if not s:
        return BASE_MAC_DINH
    u = urlsplit(s)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError("base_url phải bắt đầu bằng http:// hoặc https:// và có tên máy")
    return s


def ds_cookie(gia_tri) -> list[str]:
    if isinstance(gia_tri, str):
        gia_tri = [gia_tri]
    if not isinstance(gia_tri, list):
        return []
    ra: list[str] = []
    for c in gia_tri:
        c = chuan_hoa_cookie(c)
        if c and c not in ra:
            ra.append(c)
    return ra


class _TaiKhoan:
    """Một bộ cookie claude.ai + máy khách riêng.

    Có thể gắn một danh tính chung của lõi (``danh_tinh_id``): gắn rồi thì hiển
    thị tên theo danh tính thay vì dạng che của cookie. Tài khoản không gắn
    chạy y như trước (tương thích ngược).
    """

    def __init__(self, cookie: str, base_url: str):
        self.cookie = cookie
        self.ten_che = _che(cookie)
        self.may_khach = MayKhachClaudeWeb(cookie, base_url)
        self.khoe = True
        self.danh_tinh_id: str | None = None
        self.ten_danh_tinh: str | None = None

    def ten_hien_thi(self) -> str:
        return self.ten_danh_tinh or self.ten_che


def phan_loai_ma(ma: int) -> str:
    """Mã HTTP → nhóm lỗi: chỉ dùng mã trạng thái, không dò chữ."""
    if ma in (401, 403):
        return "xac_thuc"
    if ma in (429, 529):
        return "han_muc"
    return "tam_thoi"


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
    """Module claude-web: nap / cai / models / chat / suc_khoe / dung."""

    def __init__(self, manifest: dict):
        self.manifest = manifest or {}
        self._tien_to = str(self.manifest.get("tien_to") or "clw")
        self._accs: list[_TaiKhoan] = []
        self._base_url = BASE_MAC_DINH
        self.kho = None  # lõi gán sau khi tạo (kho riêng của module)

    # ── gắn danh tính chung (khuôn NV77) ──────────────────────────────────

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
            LOG.warning("claude-web: không đọc được liên kết danh tính: %s",
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
            LOG.warning("claude-web: không lưu được liên kết danh tính: %s",
                        type(e).__name__)

    def _ten_theo_danh_tinh(self, dt_id: str) -> str | None:
        """Tên hiển thị theo danh tính: nhãn do người dùng đặt, không thì email che."""
        try:
            dt = danh_tinh.lay(dt_id)
        except Exception as e:
            LOG.warning("claude-web: không đọc được danh tính %s: %s",
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
                LOG.warning("claude-web: không đọc được trạng thái gắn danh tính: %s",
                            type(e).__name__)
        for dt_id in sorted(cu - tap_moi):
            try:
                danh_tinh.go(dt_id, "claude-web")
            except Exception as e:
                LOG.warning("claude-web: gỡ tham chiếu danh tính %s: %s",
                            dt_id, type(e).__name__)
        for dt_id in sorted(tap_moi - cu):
            # Đã kiểm tồn tại ở nap(); kho danh tính hỏng lúc này thì để 503 lan ra
            # (đóng khi lỗi) thay vì âm thầm chạy không tham chiếu.
            danh_tinh.gan(dt_id, "claude-web")
        if self.kho is not None:
            try:
                du_lieu = self.kho.doc("danh_tinh") or {}
                self.kho.luu("danh_tinh", {"anh_xa": du_lieu.get("anh_xa") or {},
                                           "gan": sorted(tap_moi),
                                           "chu_so_huu": id(self)})
            except Exception as e:
                LOG.warning("claude-web: không lưu được trạng thái gắn danh tính: %s",
                            type(e).__name__)

    # ── vòng đời ─────────────────────────────────────────────────────

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM.

        Khoá phụ «danh_tinh» (không khai trong manifest, lõi cho qua): danh sách
        cùng thứ tự với «cookies», mỗi phần tử là id danh tính hoặc null. id lạ
        → ValueError rõ ràng, chưa gắn gì. Không có khoá này thì tra liên kết đã
        nhớ trong kho riêng theo hash cookie (bền qua khởi động); tài khoản không
        gắn chạy y như trước.
        """
        cau_hinh = cau_hinh or {}
        try:
            self._base_url = chuan_hoa_base_url(cau_hinh.get("base_url"))
        except ValueError:
            self._base_url = BASE_MAC_DINH
        ds = ds_cookie(cau_hinh.get("cookies"))
        gan_moi = cau_hinh.get("danh_tinh")
        if gan_moi is not None:
            if not isinstance(gan_moi, list) or len(gan_moi) != len(ds):
                raise ValueError(
                    "«danh_tinh» phải là danh sách cùng độ dài với «cookies» "
                    f"(nhận {len(gan_moi) if isinstance(gan_moi, list) else type(gan_moi).__name__}, "
                    f"cần {len(ds)}); mỗi phần tử là id danh tính hoặc null.")
            for dt_id in gan_moi:
                if dt_id is None:
                    continue
                if not isinstance(dt_id, str) or danh_tinh.lay(dt_id) is None:
                    raise ValueError(
                        f"danh_tinh_id {dt_id!r} không tồn tại trong sổ danh tính "
                        "(tạo trước ở «Hệ thống → AI → Danh tính»).")
        lien_ket = self._doc_lien_ket()
        accs = []
        for i, c in enumerate(ds):
            acc = _TaiKhoan(c, self._base_url)
            if gan_moi is not None:
                acc.danh_tinh_id = gan_moi[i]
            else:
                acc.danh_tinh_id = lien_ket.get(_hash_cookie(c))
            if acc.danh_tinh_id:
                acc.ten_danh_tinh = self._ten_theo_danh_tinh(acc.danh_tinh_id)
            accs.append(acc)
        if gan_moi is not None:
            for i, c in enumerate(ds):
                h = _hash_cookie(c)
                if accs[i].danh_tinh_id:
                    lien_ket[h] = accs[i].danh_tinh_id
                else:
                    lien_ket.pop(h, None)
            self._luu_lien_ket(lien_ket)
        self._accs = accs
        self._dong_bo_tham_chieu({a.danh_tinh_id for a in accs
                                  if a.danh_tinh_id})
        LOG.info("claude-web: đã nạp %d tài khoản (%d gắn danh tính)",
                 len(accs), sum(1 for a in accs if a.danh_tinh_id))

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm từng bộ cookie bằng một lượt GET /api/organizations (không tốn tiền).

        Cookie bị từ chối (401/403) bị loại; cookie lỗi tạm thời (5xx/mạng) được
        GIỮ lại (đánh dấu ok=false) để thử lại sau — tránh bắt người dùng dán lại
        chỉ vì mạng chập chờn lúc cài.
        """
        cau_hinh = cau_hinh or {}
        try:
            base_url = chuan_hoa_base_url(cau_hinh.get("base_url"))
        except ValueError as e:
            return {"ok": False, "loi": str(e), "ket_qua_tung_khoa": [],
                    "cau_hinh_luu": {}}
        cookies = ds_cookie(cau_hinh.get("cookies"))
        if not cookies:
            return {"ok": False, "loi": "Chưa có cookie nào — dán cookie từ tab claude.ai đã đăng nhập.",
                    "ket_qua_tung_khoa": [], "cau_hinh_luu": {}}
        ket_qua = []
        giu = []
        for c in cookies:
            khach = MayKhachClaudeWeb(c, base_url)
            try:
                dung_duoc, ly_do, la_xac_thuc = await khach.kiem_tra()
            finally:
                await khach.dong()
            muc = {"cookie": _che(c), "ok": dung_duoc}
            if not dung_duoc:
                muc["loi"] = ly_do
                if not la_xac_thuc:
                    giu.append(c)  # lỗi tạm: giữ lại để thử sau
            else:
                giu.append(c)
            ket_qua.append(muc)
        if not giu:
            return {"ok": False, "loi": "Mọi cookie đều bị claude.ai từ chối (401/403).",
                    "ket_qua_tung_khoa": ket_qua, "cau_hinh_luu": {}}
        return {"ok": True, "ket_qua_tung_khoa": ket_qua,
                "cau_hinh_luu": {"cookies": giu, "base_url": base_url}}

    async def models(self) -> list[dict]:
        return cv.danh_sach_model(self._tien_to)

    async def suc_khoe(self) -> dict:
        return {
            "ok": any(a.khoe for a in self._accs),
            "base_url": self._base_url,
            "tai_khoan": [{"cookie": a.ten_che, "khoe": a.khoe} for a in self._accs],
        }

    async def dung(self) -> None:
        """Gỡ tham chiếu danh tính mà CHÍNH thể hiện này đã đăng ký (khuôn NV77),
        rồi đóng máy khách. Thể hiện thay thế (nap sau) đã ghi đè chu_so_huu
        nên không bị gỡ nhầm."""
        try:
            if self.kho is not None:
                du_lieu = self.kho.doc("danh_tinh") or {}
                if du_lieu.get("chu_so_huu") == id(self):
                    for dt_id in du_lieu.get("gan") or []:
                        try:
                            danh_tinh.go(dt_id, "claude-web")
                        except Exception as e:
                            LOG.warning("claude-web: gỡ tham chiếu danh tính %s khi dừng: %s",
                                        dt_id, type(e).__name__)
                    self.kho.luu("danh_tinh",
                                 {"anh_xa": du_lieu.get("anh_xa") or {},
                                  "gan": [], "chu_so_huu": id(self)})
        except Exception as e:
            LOG.warning("claude-web: dọn tham chiếu danh tính khi dừng: %s",
                        type(e).__name__)
        for a in self._accs:
            await a.may_khach.dong()
        self._accs = []

    # ── chat ────────────────────────────────────────────────────────

    def _chon(self, da_thu: set[int]) -> _TaiKhoan | None:
        con_lai = [a for a in self._accs if id(a) not in da_thu]
        for a in con_lai:
            if a.khoe:
                return a
        # Không còn tài khoản khoẻ: cho tài khoản đã đánh dấu hỏng một cơ hội
        # hồi phục (cookie có thể chỉ bị từ chối thoáng qua).
        return con_lai[0] if con_lai else None

    async def chat(self, body: dict, stream: bool):
        body = body or {}
        try:
            prompt = cv.xay_dung_prompt(body.get("messages"))
        except cv.LoiYeuCau as e:
            return e.ma, {}, cv.goi_loi(e.thong_bao, "yeu_cau_sai")
        model = str(body.get("model") or "")
        if not model:
            return 400, {}, cv.goi_loi("Thiếu «model» (dạng clw/<tên>, ví dụ clw/sonnet).",
                                       "thieu_model")
        if not self._accs:
            return 401, {}, cv.goi_loi(
                "Module claude-web chưa có tài khoản nào — thêm cookie ở Hệ thống → AI.",
                "chua_co_tai_khoan")
        model_noi_bo = cv.giai_model(model, self._tien_to)

        da_thu: set[int] = set()
        loi_cuoi: LoiClaudeWeb | None = None
        for _ in range(min(GIOI_HAN_LUOT, len(self._accs))):
            acc = self._chon(da_thu)
            if acc is None:
                break
            da_thu.add(id(acc))
            try:
                return await self._chat_voi(acc, model, model_noi_bo, prompt, stream)
            except LoiClaudeWeb as e:
                loi_cuoi = e
                nhom = phan_loai_ma(e.ma)
                if nhom in ("xac_thuc", "han_muc"):
                    acc.khoe = False
                LOG.warning("claude-web: tài khoản %s lỗi HTTP %s (%s), chuyển tài khoản",
                            acc.ten_che, e.ma, nhom)
        return self._loi_tong(loi_cuoi)

    async def _chat_voi(self, acc: _TaiKhoan, model: str, model_noi_bo: str,
                        prompt: str, stream: bool):
        # Mọi lỗi trước khi có response 200 đều ném LoiClaudeWeb → chat() xoay
        # tài khoản. Sau khi đã trả 200 + generator, lỗi giữa luồng thành sự
        # kiện «error» (không [DONE]), không thử lại để khỏi hỏi Claude hai lần.
        org = await acc.may_khach.lay_org_id()
        conv = await acc.may_khach.tao_hoi_thoai(org)
        r = await acc.may_khach.mo_luong(org, conv, prompt, model_noi_bo)
        if stream:
            return 200, {"Content-Type": "text/event-stream",
                         "Cache-Control": "no-cache"}, self._luong(acc, r, model)
        van_ban: list[str] = []
        mo_hinh_that: str | None = None
        async for sk in acc.may_khach.doc_su_kien(r):
            m = cv.mo_hinh_that(sk)
            if m:
                mo_hinh_that = m
            van_ban.append(cv.trich_van_ban_sse(sk))
        acc.khoe = True
        return 200, {"Content-Type": "application/json"}, cv.phan_hoi_khong_stream(
            model, "".join(van_ban), mo_hinh_that)

    async def _luong(self, acc: _TaiKhoan, r: httpx.Response, model: str):
        ma_hoi = f"chatcmpl-{uuid.uuid4().hex}"
        luc = int(time.time())
        mo_hinh = model
        da_gui_role = False
        try:
            async for sk in acc.may_khach.doc_su_kien(r):
                m = cv.mo_hinh_that(sk)
                if m:
                    mo_hinh = m
                van_ban = cv.trich_van_ban_sse(sk)
                if van_ban:
                    delta = {"content": van_ban}
                    if not da_gui_role:
                        da_gui_role = True
                        delta = {"role": "assistant", **delta}
                    yield cv.chunk_sse(mo_hinh, ma_hoi, luc, delta)
            acc.khoe = True
            yield cv.chunk_sse(mo_hinh, ma_hoi, luc, {}, ket_thuc="stop")
            yield b"data: [DONE]\n\n"
        except (LoiClaudeWeb, httpx.HTTPError):
            # Lỗi giữa luồng: kết thúc bằng sự kiện «error», KHÔNG có [DONE],
            # và không ném tiếp — ném tiếp sẽ làm lõi cắt ngang kết nối của
            # khách. (CancelledError/GeneratorExit không bị bắt: vẫn lan ra
            # để việc dọn dẹp khi khách ngắt diễn ra đúng.)
            yield (b"data: " + json.dumps(
                {"error": {"message": "Luồng claude.ai bị ngắt giữa chừng.",
                           "type": "loi_luong"}},
                ensure_ascii=False).encode("utf-8") + b"\n\n")

    def _loi_tong(self, e: LoiClaudeWeb | None):
        ma = e.ma if e else 502
        nhom = phan_loai_ma(ma)
        if nhom == "xac_thuc":
            return 401, {}, cv.goi_loi(
                "Cookie claude.ai không hợp lệ hoặc hết hạn — đăng nhập lại claude.ai "
                "trên trình duyệt rồi dán lại cookie ở Hệ thống → AI.", "khoa_hong", 401)
        if nhom == "han_muc":
            return 429, {}, cv.goi_loi(
                "claude.ai báo hết hạn mức cho mọi tài khoản — đợi hạn mức hồi rồi thử lại.",
                "het_han_muc", 429)
        return 502, {}, cv.goi_loi(
            f"Dịch vụ claude.ai lỗi (HTTP {ma}) — thử lại sau.", "may_chu_loi", 502)
