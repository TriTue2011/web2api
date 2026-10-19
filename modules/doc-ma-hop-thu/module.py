"""Module doc-ma-hop-thu — ĐỌC mã xác minh từ Gmail qua IMAP (chuẩn `ma_xac_minh`).

CHỈ ĐỌC, không bao giờ ghi: kết nối IMAP qua lớp `_IMAPChiDoc` — mọi phương
thức ghi (STORE/EXPUNGE/COPY/APPEND/DELETE) bị chặn theo CẤU TRÚC (ném lỗi),
mọi hộp thư mở bằng EXAMINE (readonly), mọi FETCH dùng `BODY.PEEK[]` (không
đánh dấu đã đọc). Mã nguồn không hề gọi các phương thức đó.

An toàn theo cấu trúc, không theo danh sách:
- Miền người gửi khớp theo RANH GIỚI NHÃN (`x.openai.com` khớp `openai.com`,
  `evilopenai.com` KHÔNG khớp) — `_mien_khop`.
- Mã là chuỗi số LIỀN, đứng ĐỘC LẬP, dài trong `do_dai` — regex
  `(?<!\\d)\\d{min,max}(?!\\d)`; lấy mã của thư MỚI NHẤT khớp — `_tim_ma`.
- Máy chủ CỐ ĐỊNH `imap.gmail.com:993` qua SSL có kiểm chứng chỉ; tham số nội bộ
  `_goc_kiem_thu` chỉ có hiệu lực khi `WEB2API_TEST_CHO_PHEP_LOOPBACK=1` VÀ là
  loopback (chống SSRF) — khuôn modules/telegram-bot.
- Mật khẩu ứng dụng không bao giờ vào ngoại lệ/log: lỗi IMAP/mạng được ánh xạ
  thành mã ngắn (`sai_dang_nhap`, `mang_loi`) qua lớp ngoại lệ nội bộ KHÔNG mang
  dữ liệu; bộ lọc log `_ChanRoMatKhau` bỏ mọi dòng chứa mật khẩu đang dùng
  (cài MỘT lần, không đổi mức log toàn cục — khuôn modules/zalo-bot).
- Trần: FETCH một phần `BODY.PEEK[]<0.262144>` (256 KB/thư), xét tối đa 20 thư
  mới nhất, hỏi vòng mỗi ~3 s, tổng không quá `cho_giay` (kẹp 5..180).

Cấu hình theo khuôn modules/zalo-bot (danh sách bí mật + cặp theo thứ tự):
form chung chưa hỗ trợ danh sách có cấu trúc, nên `mat_khau_ung_dung` là
`danh_sach_bi_mat` (mỗi dòng một mật khẩu, lõi lưu riêng, không API nào trả
lại) và `email` là chuỗi danh sách cùng THỨ TỰ; số lượng phải khớp nhau.
"""

from __future__ import annotations

import asyncio
import contextlib
import imaplib
import ipaddress
import logging
import re
import socket
import ssl
from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.utils import getaddresses, parsedate_to_datetime
from urllib.parse import urlsplit

from app.tu_xa import cho_phep_loopback_cho_test

log = logging.getLogger(__name__)

#: Máy chủ IMAP CỐ ĐỊNH — không đọc từ cấu hình người dùng (chống SSRF).
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
#: Trần byte đọc mỗi thư (256 KB) — thực hiện bằng FETCH một phần.
TRAN_THU_BYTE = 262144
#: Trần số thư mới nhất xét mỗi lượt.
SO_THU_TOI_DA = 20
#: Chỉ xét hai hộp này — không đọc nhãn khác.
HOP_THU_TIM = ("INBOX", "[Gmail]/All Mail")
#: Hết giờ kết nối / đọc mỗi lượt IMAP.
_HET_GIO_KET_NOI = 10.0
_HET_GIO_MOT_LUOT = 30.0
#: Khoảng hỏi vòng giữa các lượt.
_CHU_KY_HOI = 3.0

#: Mã lỗi ngắn trả cho lõi — mã mở, không lộ nội dung thư hay chi tiết lỗi thô.
MA_LOI = (
    "khong_co_hop_thu", "het_gio", "sai_dang_nhap", "mang_loi",
    "khong_thay_ma", "yeu_cau_sai", "loi_khong_ro",
)


class _SaiDangNhap(Exception):
    """Đăng nhập IMAP thất bại — không mang theo mật khẩu hay chi tiết."""


class _MangLoi(Exception):
    """Lỗi mạng/kết nối/đọc IMAP — không mang theo bí mật."""


def _chan_ghi(lop_co_so):
    """Trộn vào lớp IMAP để mọi phương thức GHI ném lỗi theo cấu trúc.

    Dù mã sau này có vô tình gọi store/expunge/copy/append/delete thì cũng
    hỏng ồn ào thay vì ghi lén lên hộp thư.
    """
    class ChiDoc(lop_co_so):
        def store(self, *a, **k):
            raise RuntimeError("doc-ma-hop-thu: chi doc, khong STORE")

        def expunge(self, *a, **k):
            raise RuntimeError("doc-ma-hop-thu: chi doc, khong EXPUNGE")

        def copy(self, *a, **k):
            raise RuntimeError("doc-ma-hop-thu: chi doc, khong COPY")

        def append(self, *a, **k):
            raise RuntimeError("doc-ma-hop-thu: chi doc, khong APPEND")

        def delete(self, *a, **k):
            raise RuntimeError("doc-ma-hop-thu: chi doc, khong DELETE")

    ChiDoc.__name__ = "IMAPChiDoc"
    return ChiDoc


_IMAPChiDocTLS = _chan_ghi(imaplib.IMAP4_SSL)
_IMAPChiDocThuong = _chan_ghi(imaplib.IMAP4)


#: Mật khẩu đang dùng (chỉ thêm) — bộ lọc log bỏ mọi dòng chứa mật khẩu.
_MAT_KHAU_DANG_DUNG: set[str] = set()


class _ChanRoMatKhau(logging.Filter):
    """Bỏ dòng log chứa mật khẩu ứng dụng. Cài MỘT lần — KHÔNG đổi mức log
    toàn cục (đổi quanh mỗi lời gọi rồi khôi phục sẽ kẹt logger khi hai lượt
    chạy xen kẽ) — khuôn modules/zalo-bot."""
    _doc_ma_hop_thu_loc = True

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            noi_dung = record.getMessage()
        except Exception:
            return False   # không định dạng được → bỏ dòng (đóng khi lỗi, không để lọt bí mật)
        return not any(mk in noi_dung for mk in _MAT_KHAU_DANG_DUNG if mk)


def _cai_bo_loc_log() -> None:
    for ten in ("imaplib", __name__):
        lg = logging.getLogger(ten)
        for f in [f for f in lg.filters if getattr(f, "_doc_ma_hop_thu_loc", False)]:
            lg.removeFilter(f)   # nạp lại module: bỏ bộ lọc của bản cũ
        lg.addFilter(_ChanRoMatKhau())


_cai_bo_loc_log()


def _mien_khop(mien_gui: str, cho_phep: list[str]) -> bool:
    """Miền người gửi có thuộc một trong các miền cho phép không — khớp theo
    ranh giới nhãn: `x.openai.com` khớp `openai.com`, `evilopenai.com` KHÔNG."""
    h = (mien_gui or "").strip().lower().rstrip(".")
    for a in cho_phep:
        a = (a or "").strip().lower().rstrip(".")
        if not a:
            continue
        if h == a or h.endswith("." + a):
            return True
    return False


def _tim_ma(van_ban: str, dai_toi_thieu: int, dai_toi_da: int):
    """Trích mã: chuỗi số LIỀN, đứng ĐỘC LẬP (không dính số dài hơn), dài trong
    [dai_toi_thieu, dai_toi_da]. Trả mã đầu tiên tìm thấy, hoặc None."""
    mau = re.compile(r"(?<!\d)\d{%d,%d}(?!\d)" % (dai_toi_thieu, dai_toi_da))
    m = mau.search(van_ban or "")
    return m.group(0) if m else None


def _che_email(email: str) -> str:
    cuc_bo, _, mien = email.partition("@")
    dau = cuc_bo[:1] if cuc_bo else ""
    return f"{dau}***@{mien}" if mien else "***"


_MAU_MIEN = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")


class Module:
    """Đọc mã xác minh từ Gmail qua IMAP (chuẩn `ma_xac_minh`). Không có route HTTP."""

    def __init__(self, manifest, _goc_kiem_thu: str | None = None,
                 _kiem_thu_tls: bool = True):
        self.manifest = manifest
        self._goc_kiem_thu = _goc_kiem_thu
        self._kiem_thu_tls = _kiem_thu_tls
        self._hop_thu: list[dict] = []   # [{"email":..., "mat_khau":...}]

    # ── kết nối (ghi đè gốc chỉ cho test) ──

    def _noi_dau(self) -> tuple:
        """Trả (host, port, tls, xac_thuc_chung_chi, la_kiem_thu).

        Tham số nội bộ `_goc_kiem_thu` CHỈ có hiệu lực khi biến môi trường test
        `WEB2API_TEST_CHO_PHEP_LOOPBACK=1` được đặt VÀ giá trị là loopback —
        người dùng không thể lái module tới máy chủ khác (chống SSRF)."""
        g = self._goc_kiem_thu
        if g and cho_phep_loopback_cho_test():
            try:
                phan = urlsplit("//" + str(g).strip())
                ip = ipaddress.ip_address(phan.hostname or "")
                if ip.is_loopback and phan.port:
                    return (str(ip), phan.port, self._kiem_thu_tls, False, True)
            except ValueError:
                pass
        return (IMAP_HOST, IMAP_PORT, True, True, False)

    def _ket_noi(self, email: str, mat_khau: str):
        """Mở IMAP và LOGIN. Ném _SaiDangNhap / _MangLoi (không mang bí mật)."""
        host, cong, tls, xac_thuc, _ = self._noi_dau()
        try:
            if tls:
                ctx = ssl.create_default_context() if xac_thuc else ssl._create_unverified_context()
                imap = _IMAPChiDocTLS(host, cong, ssl_context=ctx, timeout=_HET_GIO_KET_NOI)
            else:
                imap = _IMAPChiDocThuong(host, cong, timeout=_HET_GIO_KET_NOI)
        except (OSError, socket.timeout, ssl.SSLError) as e:
            log.warning("doc-ma-hop-thu: ket noi that bai (%s)", type(e).__name__)
            raise _MangLoi from None
        try:
            imap.login(email, mat_khau)
        except imaplib.IMAP4.error:
            with contextlib.suppress(Exception):
                imap.logout()
            log.warning("doc-ma-hop-thu: dang nhap that bai cho %s", _che_email(email))
            raise _SaiDangNhap from None
        except (OSError, socket.timeout, ssl.SSLError) as e:
            with contextlib.suppress(Exception):
                imap.logout()
            log.warning("doc-ma-hop-thu: loi mang khi dang nhap (%s)", type(e).__name__)
            raise _MangLoi from None
        return imap

    # ── phân tích cấu hình ──

    @staticmethod
    def _phan_tich(cau_hinh: dict) -> list[dict]:
        """Trả [{"email","mat_khau"}]; ném ValueError kèm lý do ngắn khi sai.

        Khuôn modules/zalo-bot: form chung chưa hỗ trợ danh sách có cấu trúc,
        nên mật khẩu là `danh_sach_bi_mat` (mỗi dòng một mật khẩu — lõi lưu
        riêng, không API nào trả lại) và email là chuỗi danh sách CÙNG THỨ TỰ.
        """
        if not isinstance(cau_hinh, dict):
            raise ValueError("cấu hình phải là dict")
        tho_email = cau_hinh.get("email")
        if isinstance(tho_email, str):
            emails = [e.strip().lower() for e in tho_email.replace(",", "\n").split("\n") if e.strip()]
        elif isinstance(tho_email, list):
            emails = [str(e).strip().lower() for e in tho_email if str(e).strip()]
        else:
            emails = []
        tho_mk = cau_hinh.get("mat_khau_ung_dung")
        if isinstance(tho_mk, str):
            mat_khaus = [m for m in (x.strip().replace(" ", "") for x in tho_mk.split("\n")) if m]
        elif isinstance(tho_mk, list):
            mat_khaus = [str(m).strip().replace(" ", "") for m in tho_mk if str(m).strip()]
        else:
            mat_khaus = []
        if not emails:
            raise ValueError("email: cần ít nhất một địa chỉ Gmail")
        if not mat_khaus:
            raise ValueError("mat_khau_ung_dung: cần ít nhất một mật khẩu ứng dụng")
        if len(emails) != len(mat_khaus):
            raise ValueError("số email và số mật khẩu ứng dụng phải bằng nhau (cùng thứ tự)")
        for e in emails:
            if "@" not in e or not _MAU_MIEN.match(e.split("@", 1)[1]):
                raise ValueError(f"email không hợp lệ: {_che_email(e)}")
        return [{"email": e, "mat_khau": mk} for e, mk in zip(emails, mat_khaus)]

    # ── hợp đồng module ──

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình; lỗi → module rỗng + warning (không ném)."""
        try:
            self._hop_thu = self._phan_tich(cau_hinh)
            _MAT_KHAU_DANG_DUNG.update(h["mat_khau"] for h in self._hop_thu)
        except Exception as e:
            log.warning("doc-ma-hop-thu: cấu hình sai (%s) — module rỗng", e)
            self._hop_thu = []

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm cấu hình bằng cách LOGIN thử một lần rồi LOGOUT ngay (không đọc
        thư). Trả ok + so_hop_thu; sai → ok False + mã ngắn, không in mật khẩu."""
        try:
            hop_thu = self._phan_tich(cau_hinh)
        except Exception as e:
            return {"ok": False, "ma_loi": "yeu_cau_sai", "loi": f"cấu hình sai: {e}"}
        for h in hop_thu:
            try:
                await asyncio.to_thread(self._dang_nhap_thu, h["email"], h["mat_khau"])
            except _SaiDangNhap:
                return {"ok": False, "ma_loi": "sai_dang_nhap",
                        "loi": f"đăng nhập thất bại cho {_che_email(h['email'])}"}
            except _MangLoi:
                return {"ok": False, "ma_loi": "mang_loi", "loi": "không kết nối được máy chủ thư"}
        self._hop_thu = hop_thu
        _MAT_KHAU_DANG_DUNG.update(h["mat_khau"] for h in hop_thu)
        return {"ok": True, "so_hop_thu": len(hop_thu)}

    def _dang_nhap_thu(self, email: str, mat_khau: str) -> None:
        imap = self._ket_noi(email, mat_khau)
        try:
            imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass

    def hop_thu_ra(self) -> list[dict]:
        """Danh sách hộp thư đọc được — CHỈ id và email che, KHÔNG mật khẩu."""
        return [{"id": h["email"], "email_che": _che_email(h["email"])} for h in self._hop_thu]

    async def suc_khoe(self) -> dict:
        return {"ok": True, "so_hop_thu": len(self._hop_thu)}

    async def dung(self) -> None:
        return None

    # ── chuẩn ma_xac_minh ──

    @staticmethod
    def _kiem_yeu_cau(yeu_cau: dict):
        """Trả (email, mat_khau, tu_mien, sau_luc, dai_min, dai_max, cho_giay).
        Ném ValueError khi yêu cầu sai hình dạng."""
        if not isinstance(yeu_cau, dict):
            raise ValueError("yeu_cau phải là dict")
        hop_thu = str(yeu_cau.get("hop_thu") or "").strip().lower()
        tu_mien = yeu_cau.get("tu_mien")
        if not hop_thu or not isinstance(tu_mien, list) or not tu_mien or not all(
                isinstance(m, str) and _MAU_MIEN.match(m.strip().lower()) and "." in m for m in tu_mien):
            raise ValueError("hop_thu/tu_mien sai")
        try:
            sau_luc = datetime.fromisoformat(str(yeu_cau.get("sau_luc")).replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("sau_luc phải là ISO UTC")
        if sau_luc.tzinfo is None:
            sau_luc = sau_luc.replace(tzinfo=timezone.utc)
        do_dai = yeu_cau.get("do_dai")
        if (not isinstance(do_dai, (list, tuple)) or len(do_dai) != 2
                or not all(isinstance(n, int) for n in do_dai)
                or not 1 <= do_dai[0] <= do_dai[1] <= 32):
            raise ValueError("do_dai phải là [min, max] với 1 <= min <= max <= 32")
        try:
            cho_giay = float(yeu_cau.get("cho_giay"))
        except (TypeError, ValueError):
            raise ValueError("cho_giay phải là số")
        cho_giay = min(180.0, max(5.0, cho_giay))   # kẹp 5..180
        return hop_thu, [m.strip().lower() for m in tu_mien], sau_luc, do_dai[0], do_dai[1], cho_giay

    async def lay_ma(self, yeu_cau: dict) -> dict:
        """Hỏi vòng IMAP mỗi ~3 s tới khi thấy thư mới hơn sau_luc chứa mã.

        Trả {"ok": True, "ma": "<mã>"} hoặc {"ok": False, "ma_loi": "<mã ngắn>"}.
        Không bao giờ trả nội dung thư, tiêu đề, người gửi hay chi tiết lỗi thô.
        """
        try:
            hop_thu, tu_mien, sau_luc, dai_min, dai_max, cho_giay = self._kiem_yeu_cau(yeu_cau)
        except ValueError as e:
            log.warning("doc-ma-hop-thu: yeu_cau_sai (%s)", e)
            return {"ok": False, "ma_loi": "yeu_cau_sai"}
        muc = next((h for h in self._hop_thu if h["email"] == hop_thu), None)
        if muc is None:
            return {"ok": False, "ma_loi": "khong_co_hop_thu"}
        han = asyncio.get_event_loop().time() + cho_giay
        while True:
            try:
                ma = await asyncio.wait_for(
                    asyncio.to_thread(self._quet_mot_luot, muc, tu_mien, sau_luc, dai_min, dai_max),
                    timeout=_HET_GIO_MOT_LUOT + cho_giay,
                )
            except _SaiDangNhap:
                return {"ok": False, "ma_loi": "sai_dang_nhap"}
            except asyncio.TimeoutError:
                return {"ok": False, "ma_loi": "het_gio"}
            except _MangLoi:
                return {"ok": False, "ma_loi": "mang_loi"}
            except Exception as e:
                log.warning("doc-ma-hop-thu: loi_khong_ro (%s)", type(e).__name__)
                return {"ok": False, "ma_loi": "loi_khong_ro"}
            if ma:
                return {"ok": True, "ma": ma}
            con_lai = han - asyncio.get_event_loop().time()
            if con_lai <= 0:
                return {"ok": False, "ma_loi": "het_gio"}
            await asyncio.sleep(min(_CHU_KY_HOI, con_lai))

    def _quet_mot_luot(self, muc: dict, tu_mien: list[str], sau_luc: datetime,
                       dai_min: int, dai_max: int):
        """Một lượt quét IMAP (chạy trong thread): trả mã tìm thấy hoặc None.
        Ném _SaiDangNhap / _MangLoi."""
        imap = self._ket_noi(muc["email"], muc["mat_khau"])
        try:
            for hop in HOP_THU_TIM:
                try:
                    loai, _ = imap.select(hop, readonly=True)   # EXAMINE — chỉ đọc
                except imaplib.IMAP4.error:
                    continue   # hộp không có → bỏ qua, không vỡ lượt
                if loai != "OK":
                    continue
                try:
                    loai, du_lieu = imap.search(None, "ALL")
                except imaplib.IMAP4.error as e:
                    log.warning("doc-ma-hop-thu: SEARCH that bai (%s)", type(e).__name__)
                    continue
                if loai != "OK" or not du_lieu:
                    continue
                uids = du_lieu[0].split()
                for uid in reversed(uids[-SO_THU_TOI_DA:]):   # mới nhất trước
                    ma = self._xet_thu(imap, uid, tu_mien, sau_luc, dai_min, dai_max)
                    if ma:
                        return ma
        finally:
            with contextlib.suppress(Exception):
                imap.logout()
        return None

    def _xet_thu(self, imap, uid: bytes, tu_mien: list[str], sau_luc: datetime,
                 dai_min: int, dai_max: int):
        """Xét một thư: mới hơn sau_luc + từ miền cho phép → trích mã. Trả mã/None."""
        try:
            loai, du_lieu = imap.fetch(
                uid, "(INTERNALDATE BODY.PEEK[]<0.%d>)" % TRAN_THU_BYTE)
        except imaplib.IMAP4.error as e:
            log.warning("doc-ma-hop-thu: FETCH that bai (%s)", type(e).__name__)
            return None
        if loai != "OK" or not du_lieu:
            return None
        dau, than = None, b""
        for muc_dl in du_lieu:
            if isinstance(muc_dl, tuple) and len(muc_dl) == 2:
                dau, than = muc_dl
                break
        if dau is None:
            return None
        m_ngay = re.search(rb'INTERNALDATE "([^"]+)"', bytes(dau))
        if not m_ngay:
            return None
        try:
            luc = parsedate_to_datetime(m_ngay.group(1).decode("ascii", "replace"))
        except (ValueError, TypeError):
            return None
        if luc.tzinfo is None:
            luc = luc.replace(tzinfo=timezone.utc)
        if luc <= sau_luc:
            return None   # thư cũ — bỏ
        try:
            thu = message_from_bytes(bytes(than), policy=policy.default)
        except Exception:
            return None
        gui = getaddresses(thu.get_all("From", []))
        if not gui:
            return None
        mien_gui = gui[0][1].rsplit("@", 1)[-1] if "@" in gui[0][1] else ""
        if not _mien_khop(mien_gui, tu_mien):
            return None   # không đúng miền — bỏ
        return _tim_ma(self._van_ban(thu), dai_min, dai_max)

    @staticmethod
    def _van_ban(thu) -> str:
        """Trích văn bản thuần từ các phần text (không lưu nội dung đi đâu)."""
        phan: list[str] = []
        try:
            for p in thu.walk():
                if p.is_multipart():
                    continue
                loai = p.get_content_type()
                if loai not in ("text/plain", "text/html"):
                    continue
                try:
                    nd = p.get_content()
                except Exception:
                    continue
                if not isinstance(nd, str):
                    continue
                if loai == "text/html":
                    nd = re.sub(r"<[^>]+>", " ", nd)
                phan.append(nd)
        except Exception:
            return ""
        return "\n".join(phan)
