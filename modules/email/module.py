"""Module email — kênh email hai chiều: GỬI RA qua SMTP (chuẩn `kenh_ra`) và
NHẬN TIN VÀO qua IMAP hỏi vòng (chuẩn `hoi_vong`, CHUAN-MODULE mục 12.3).

PORT từ c2a (kho của chính chủ máy — CHÉP mã đang chạy thật, chỉ đổi lớp kết
nối/khuôn module/xác thực; KHÔNG viết lại):
  nguồn: ~/workspace/c2a-nv146/services/email_channel.py @ commit d879b03
  (865 dòng, chỉ dùng thư viện chuẩn smtplib/imaplib/email/ssl).
  Đã chép: `_norm_account`, `_allowed`, `_decode_hdr`, `_extract_body`,
  `_attachments`/`_attachment_text` (rút gọn: chỉ đọc văn bản, không ghi đĩa),
  `send_email`, `_sec_mode`, `_ssl_ctx`, `_imap_connect`, `poll_account`,
  `_friendly_error`, `_needs_app_pw`.
  KHÔNG chép: `_process_message` (gọi agent), `_gui_tai_lieu`, `_user_id_for`,
  `thong_bao`/`digest` (tóm tắt + chống trùng bằng seen-UID của module),
  `calendar_connector`, `_legacy`, `_thong_bao_kenh`, `status`, vòng lặp
  `_loop`/`_poll_worker`/`start`/`stop` (lõi duy trì qua `hoi_vong`),
  `send_digest_now`, `test_connection` (gộp vào `suc_khoe`).

Thiết kế bảo mật:
- Nội dung email là dữ liệu KHÔNG TIN: không bao giờ làm theo chỉ dẫn trong
  thư; HTML lọc thành văn bản; đính kèm KHÔNG ghi đĩa, chỉ trích văn bản có
  trần; tiêu đề/người gửi giới hạn độ dài.
- Danh sách người gửi được phép mặc định RỖNG = không nhận ai (fail-closed);
  so khớp CẤU TRÚC địa chỉ (khớp chính xác hoặc @domain), KHÔNG khớp chuỗi con.
- Mật khẩu kiểu `danh_sach_bi_mat` (kho bí mật lõi, 0600, cùng thứ tự với tài
  khoản); không vào log (bộ lọc chặn dòng chứa mật khẩu trên logger imaplib,
  smtplib và module), phản hồi, `suc_khoe`, `kenh_ra`, thông báo lỗi.
- TLS kiểm chứng chỉ MẶC ĐỊNH; tắt chỉ khi chủ máy khai tường minh.
- Máy chủ do người dùng khai đi qua ĐÚNG bộ kiểm `app/tu_xa.py`
  (`kiem_url_lan` — chặn SSRF loopback/link-local/metadata); dải nội bộ chỉ
  khi chủ máy khai tường minh `mang_noi_bo: "co"`.
- Vòng IMAP lùi dần theo cấp số nhân khi lỗi (tối đa 5 phút), dừng hẳn hộp thư
  sau 10 lỗi liên tiếp và báo MỘT lần; gỡ module dừng vòng nền («dừng là dừng
  hẳn»); khởi động lại không phát lại thư đã xử lý (nhớ UID theo hộp thư, lưu
  `<WEB2API_DATA>/bi_mat/mo-dun/email/da_thay.json` quyền 0600, không lưu nội
  dung thư).
"""

from __future__ import annotations

import asyncio
import contextlib
import email
import email.policy
import hashlib
import imaplib
import ipaddress
import json
import logging
import os
import re
import smtplib
import ssl
import time
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from typing import Any

from app.kenh_vao import TRAN_VAN_BAN, lam_sach_ten, lam_sach_van_ban
from app.tu_xa import cho_phep_loopback_cho_test, kiem_url_lan

log = logging.getLogger("modules.email.module")

# ── hằng số ──────────────────────────────────────────────────────────────────

#: Mã lỗi ngắn trả cho lõi/người gọi — không lộ nội dung thư hay chi tiết thô.
MA_LOI = (
    "chua_cau_hinh", "tai_khoan_sai", "nguoi_nhan_sai", "nguoi_nhan_la",
    "tieu_de_sai", "noi_dung_sai", "sai_dang_nhap", "mang_loi",
    "may_chu_loi", "loi_khong_ngu",
)

_TEN_SO_UID = "da_thay"
_GIOI_HAN_UID = 500          # số UID nhớ mỗi hộp thư
_LOI_LIEN_TIEP_DUNG = 10     # dừng hộp thư sau N lỗi liên tiếp
_LUI_TOI_DA_GIAY = 300.0     # lùi dần tối đa 5 phút
_BURST_POLL = 10             # số thư xử lý mỗi lượt poll một hộp
_HET_GIO_IMAP = 30
_HET_GIO_SMTP = 30

_TIEU_DE_TOI_DA = 200
_NGUOI_GUI_TOI_DA = 120

#: Đính kèm: chỉ đọc văn bản, có trần (KHÔNG ghi đĩa, KHÔNG đọc nhị phân).
_PLAIN_EXT = {".txt", ".md", ".csv", ".log", ".json", ".yml", ".yaml", ".ini"}
_ATTACH_MAX = 8 * 1024 * 1024
_ATTACH_CHARS = 4000
_ATTACH_TEP_TOI_DA = 5

#: Các nhà cung cấp lớn bắt buộc App Password (port từ c2a).
_APP_PW_HOSTS = ("gmail", "google", "outlook", "office365", "hotmail",
                 "live.com", "yahoo")
_APP_PW_HINT = (
    "Cần App Password (mật khẩu ứng dụng), KHÔNG dùng mật khẩu đăng nhập. "
    "Gmail: bật 2FA rồi lấy 16 ký tự tại https://myaccount.google.com/apppasswords "
    "· Outlook: https://account.live.com/proofs/AppPassword"
)

_MAU_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-]{0,253}[A-Za-z0-9])?$")
_MAU_DIA_CHI = re.compile(r"^[^@\s<>()\[\]\\,;:]{1,64}@[^@\s<>()\[\]\\,;:]{1,253}$")


#: Mật khẩu đang dùng (chỉ thêm) — bộ lọc log chặn mọi dòng chứa mật khẩu.
_MAT_KHAU_DANG_DUNG: set[str] = set()


class _ChanMatKhau(logging.Filter):
    """Bỏ dòng log chứa mật khẩu đang dùng. Cài MỘT lần lên logger imaplib,
    smtplib và module — không đổi mức log toàn cục."""
    _email_loc = True

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            noi_dung = record.getMessage()
        except Exception:
            return False
        return not any(mk in noi_dung for mk in _MAT_KHAU_DANG_DUNG if mk)


def _cai_bo_loc_log() -> None:
    for ten in ("imaplib", "smtplib", "modules.email.module"):
        lg = logging.getLogger(ten)
        for f in [f for f in lg.filters if getattr(f, "_email_loc", False)]:
            lg.removeFilter(f)
        lg.addFilter(_ChanMatKhau())


_cai_bo_loc_log()


class _SaiDangNhap(Exception):
    """Đăng nhập IMAP/SMTP thất bại — không mang mật khẩu hay chi tiết."""


class _MangLoi(Exception):
    """Lỗi mạng/kết nối — không mang bí mật."""


# ── hàm port từ c2a (giữ nguyên logic, đổi lớp kết nối) ───────────────────────

def _needs_app_pw(host: str) -> bool:
    h = (host or "").lower()
    return any(k in h for k in _APP_PW_HOSTS)


def _friendly_error(exc: Exception | str, host: str = "") -> str:
    """Lỗi IMAP/SMTP → câu tiếng Việt hành động được (không chứa mật khẩu)."""
    raw = str(exc)
    low = raw.lower()
    if "application-specific password" in low or "app password" in low:
        return f"❌ {_APP_PW_HINT}"
    if ("invalid credentials" in low or "authenticationfailed" in low
            or ("auth" in low and "fail" in low)):
        if _needs_app_pw(host):
            return f"❌ Sai tài khoản hoặc mật khẩu. {_APP_PW_HINT}"
        return ("❌ Sai tài khoản hoặc mật khẩu. Mail công ty/nội bộ dùng mật khẩu "
                "đăng nhập thường (không cần App Password) — kiểm tra lại bằng webmail.")
    if "name or service not known" in low or "getaddrinfo" in low:
        return "❌ Sai IMAP/SMTP host (không phân giải được tên miền)."
    if "timed out" in low or "timeout" in low:
        return "❌ Hết thời gian kết nối — kiểm tra host/port hoặc firewall."
    if "certificate" in low or "ssl" in low:
        return ("❌ Lỗi SSL/chứng chỉ — server nội bộ hay dùng chứng chỉ tự ký: "
                "bật «Chấp nhận mọi chứng chỉ» trong cài đặt hộp thư, "
                "hoặc kiểm tra lại host/port/loại bảo mật.")
    return f"❌ {raw[:200]}"


def _decode_hdr(val: Any) -> str:
    if not val:
        return ""
    try:
        return str(make_header(decode_header(str(val))))
    except Exception:
        return str(val)


def _extract_body(msg: email.message.Message, max_c: int = 6000) -> str:
    text_parts: list[str] = []
    html_parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            try:
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                decoded = payload.decode(charset, errors="replace")
            except Exception:
                continue
            if ctype == "text/plain":
                text_parts.append(decoded)
            elif ctype == "text/html":
                html_parts.append(decoded)
    else:
        try:
            payload = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if (msg.get_content_type() or "").lower() == "text/html":
                html_parts.append(decoded)
            else:
                text_parts.append(decoded)
        except Exception:
            pass
    body = "\n".join(text_parts).strip()
    if not body and html_parts:
        raw = "\n".join(html_parts)
        body = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw)
        body = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", body)
        body = re.sub(r"(?s)<[^>]+>", " ", body)
        body = re.sub(r"\s+", " ", body).strip()
    return body[:max_c]


def _attachment_text(filename: str, payload: bytes) -> str:
    """Nội dung CHỮ của một tệp đính kèm ('' nếu không đọc được).

    RÚT GỌN so với c2a: chỉ đọc văn bản thuần, KHÔNG ghi đĩa, KHÔNG đọc
    PDF/Office (module không có pipeline tài liệu; spec NV154: «chỉ đọc văn
    bản»). Không raise.
    """
    name = str(filename or "file")
    ext = os.path.splitext(name)[1].lower()
    if not payload or len(payload) > _ATTACH_MAX:
        return ""
    if ext in _PLAIN_EXT:
        try:
            return payload.decode("utf-8", errors="replace")[:_ATTACH_CHARS]
        except Exception:
            return ""
    return ""


def _attachments(msg: email.message.Message) -> list[dict[str, Any]]:
    """[{name, size, text}] — text rỗng nếu không phải văn bản đọc được."""
    out: list[dict[str, Any]] = []
    if not msg.is_multipart():
        return out
    for part in msg.walk():
        disp = str(part.get("Content-Disposition") or "").lower()
        fname = _decode_hdr(part.get_filename() or "")
        if "attachment" not in disp and not fname:
            continue
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:
            payload = b""
        out.append({"name": fname or "(không tên)",
                    "size": len(payload),
                    "text": _attachment_text(fname, payload)})
        if len(out) >= _ATTACH_TEP_TOI_DA:
            break
    return out


def _sec_mode(acc: dict[str, Any]) -> str:
    sec = str(acc.get("security") or "").lower()
    if sec in ("ssl", "starttls", "plain"):
        return sec
    return "ssl"


def _ssl_ctx(acc: dict[str, Any]) -> ssl.SSLContext:
    if bool(acc.get("verify_ssl", True)):
        return ssl.create_default_context()
    # Chủ máy CHỦ ĐỘNG tắt kiểm chứng chỉ cho server nội bộ tự ký
    # (README cảnh báo rủi ro; mặc định BẬT kiểm chứng).
    return ssl._create_unverified_context()  # nosec B323


def _che_email(addr: str) -> str:
    """Che địa chỉ email trong log/phản hồi: a***@example.com."""
    try:
        local, _, domain = str(addr).partition("@")
        if not domain:
            return "***"
        return f"{local[:1]}***@{domain}"
    except Exception:
        return "***"


# ── so khớp cấu trúc địa chỉ ─────────────────────────────────────────────────

def _khop_danh_sach(danh_sach: list[str], dia_chi: str) -> bool:
    """So khớp CẤU TRÚC: khớp chính xác hoặc theo @domain. KHÔNG khớp chuỗi
    con (chống «admin» khớp «admin@evil.com»)."""
    dia_chi_l = (dia_chi or "").strip().lower()
    for muc in danh_sach:
        a = str(muc or "").strip().lower()
        if not a:
            continue
        if a == "*" or a == dia_chi_l:
            return True
        if a.startswith("@") and (dia_chi_l.endswith(a) or dia_chi_l == a[1:]):
            return True
    return False


def _allowed(acc: dict[str, Any], sender: str) -> bool:
    raw = acc.get("allowed_senders")
    if not isinstance(raw, list) or not raw:
        return False  # fail-closed: trống = chặn hết
    return _khop_danh_sach(raw, sender)


def _dia_chi_hop_le(addr: Any) -> str:
    """Trả địa chỉ đã chuẩn hoá; '' nếu sai khuôn (chống chèn header)."""
    if not isinstance(addr, str):
        return ""
    _, a = parseaddr(addr)
    a = (a or addr).strip()
    if any(c in a for c in "\r\n") or not _MAU_DIA_CHI.match(a):
        return ""
    return a.lower()


def _host_hop_le(host: Any) -> str:
    """Trả hostname/IP đã chuẩn hoá; '' nếu sai khuôn."""
    if not isinstance(host, str):
        return ""
    h = host.strip().lower().rstrip(".")
    if not h or any(c in h for c in "\r\n \t/\\?#@:"):
        return ""
    try:
        ipaddress.ip_address(h)
        return h
    except ValueError:
        pass
    if _MAU_HOST.match(h):
        return h
    return ""


class Module:
    """Kênh email: gửi ra qua SMTP, nhận vào qua IMAP hỏi vòng."""

    def __init__(self, manifest, _imap_ssl=None, _imap=None,
                 _smtp_ssl=None, _smtp=None):
        self.manifest = manifest
        # Lớp kết nối tiêm cho test (mặc định: imaplib/smtplib thật).
        self._imap_ssl = _imap_ssl or imaplib.IMAP4_SSL
        self._imap = _imap or imaplib.IMAP4
        self._smtp_ssl = _smtp_ssl or smtplib.SMTP_SSL
        self._smtp = _smtp or smtplib.SMTP
        self.kho = None  # lõi gán KhoRieng trước nap()
        self._tai_khoan: list[dict] = []
        self._nguoi_nhan_cho_phep: list[str] = []
        self._mang_noi_bo = False
        self._dung = False
        self._da_thay: dict[str, dict[str, bool]] = {}  # acc_id -> {uid: True}
        self._loi_lien_tiep: dict[str, int] = {}
        self._tam_dung_vi_loi: set[str] = set()
        self._da_bao_dung: set[str] = set()
        self._lan_poll: dict[str, float] = {}
        self._bo_qua_nguoi_la = 0
        self._loi_cuoi = ""

    # ── phân tích cấu hình ──

    @staticmethod
    def _tach_danh_sach_chuoi(tho: Any) -> list[str]:
        if isinstance(tho, str):
            return [s.strip() for s in tho.replace(",", "\n").split("\n") if s.strip()]
        if isinstance(tho, list):
            return [str(s).strip() for s in tho if str(s).strip()]
        return []

    def _phan_tich(self, cau_hinh: dict) -> tuple[list[dict], list[str]]:
        """Trả (tai_khoan, mat_khau); ném ValueError kèm lý do ngắn khi sai."""
        if not isinstance(cau_hinh, dict):
            raise ValueError("cấu hình phải là dict")
        tho_tk = cau_hinh.get("tai_khoan")
        if isinstance(tho_tk, str):
            try:
                ds_tk = json.loads(tho_tk)
            except Exception:
                raise ValueError("tai_khoan: JSON không hợp lệ")
        elif isinstance(tho_tk, list):
            ds_tk = tho_tk
        else:
            ds_tk = []
        mat_khaus = self._tach_danh_sach_chuoi(cau_hinh.get("mat_khau"))
        if not ds_tk:
            raise ValueError("tai_khoan: cần ít nhất một hộp thư (JSON)")
        if not isinstance(ds_tk, list):
            raise ValueError("tai_khoan: cần mảng JSON")
        if len(mat_khaus) != len(ds_tk):
            raise ValueError("số mật khẩu phải bằng số hộp thư (cùng thứ tự)")
        tai_khoan = []
        thay_id = set()
        for i, tho in enumerate(ds_tk):
            if not isinstance(tho, dict):
                raise ValueError(f"hộp thư {i + 1}: mỗi hộp là dict")
            user = _dia_chi_hop_le(tho.get("user"))
            if not user:
                raise ValueError(f"hộp thư {i + 1}: user không phải địa chỉ email hợp lệ")
            imap_host = _host_hop_le(tho.get("imap_host"))
            if not imap_host:
                raise ValueError(f"hộp thư {i + 1}: imap_host không hợp lệ")
            smtp_host = _host_hop_le(tho.get("smtp_host") or "")
            if not smtp_host:
                smtp_host = imap_host.replace("imap.", "smtp.")
                if not _host_hop_le(smtp_host):
                    smtp_host = imap_host
            try:
                imap_port = int(tho.get("imap_port") or 993)
                smtp_port = int(tho.get("smtp_port") or 465)
            except (TypeError, ValueError):
                raise ValueError(f"hộp thư {i + 1}: cổng phải là số")
            if not (1 <= imap_port <= 65535 and 1 <= smtp_port <= 65535):
                raise ValueError(f"hộp thư {i + 1}: cổng ngoài 1-65535")
            sec = str(tho.get("security") or "ssl").lower()
            if sec not in ("ssl", "starttls", "plain"):
                raise ValueError(f"hộp thư {i + 1}: security chỉ nhận ssl/starttls/plain")
            acc_id = str(tho.get("id") or "").strip()
            if not acc_id:
                acc_id = hashlib.sha256(user.encode()).hexdigest()[:8]
            if acc_id in thay_id:
                raise ValueError(f"hộp thư {i + 1}: id {acc_id!r} trùng")
            thay_id.add(acc_id)
            try:
                max_body = max(500, int(tho.get("max_body_chars") or 6000))
            except (TypeError, ValueError):
                raise ValueError(f"hộp thư {i + 1}: max_body_chars phải là số")
            try:
                poll_s = max(20, int(tho.get("poll_seconds") or 60))
            except (TypeError, ValueError):
                raise ValueError(f"hộp thư {i + 1}: poll_seconds phải là số")
            gui = tho.get("allowed_senders")
            if isinstance(gui, str):
                gui = [s.strip() for s in gui.replace(",", "\n").split("\n") if s.strip()]
            tai_khoan.append({
                "id": acc_id,
                "label": str(tho.get("label") or user).strip()[:80],
                "enabled": bool(tho.get("enabled", True)),
                "imap_host": imap_host, "imap_port": imap_port,
                "smtp_host": smtp_host, "smtp_port": smtp_port,
                "user": user,
                "security": sec,
                "verify_ssl": bool(tho.get("verify_ssl", True)),
                "allowed_senders": [str(s) for s in (gui or []) if str(s).strip()],
                "mark_seen": bool(tho.get("mark_seen", True)),
                "max_body_chars": max_body,
                "poll_seconds": poll_s,
            })
        return tai_khoan, mat_khaus

    # ── sổ UID đã thấy ──

    def _doc_so_uid(self) -> None:
        kho = getattr(self, "kho", None)
        if kho is None:
            return
        try:
            du_lieu = kho.doc(_TEN_SO_UID)
        except Exception as e:
            log.warning("email: đọc sổ UID lỗi (%s)", type(e).__name__)
            return
        if not isinstance(du_lieu, dict):
            return
        for acc_id, uids in (du_lieu.get("tai_khoan") or {}).items():
            if isinstance(uids, list):
                self._da_thay[str(acc_id)] = {u: True for u in uids if isinstance(u, str)}

    def _ghi_so_uid(self) -> None:
        kho = getattr(self, "kho", None)
        if kho is None:
            return
        try:
            kho.luu(_TEN_SO_UID, {"tai_khoan": {k: list(v) for k, v in self._da_thay.items()}})
        except Exception as e:
            log.warning("email: ghi sổ UID lỗi (%s)", type(e).__name__)

    def _nho_uid(self, acc_id: str, uid: str) -> None:
        d = self._da_thay.setdefault(acc_id, {})
        d[uid] = True
        while len(d) > _GIOI_HAN_UID:
            d.pop(next(iter(d)))

    # ── hợp đồng module ──

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình (đồng bộ — lõi gọi ở nơi không chờ được); lỗi →
        module rỗng + warning, không ném."""
        try:
            tai_khoan, mat_khaus = self._phan_tich(cau_hinh)
            for acc, mk in zip(tai_khoan, mat_khaus):
                acc["mat_khau"] = mk
            self._tai_khoan = tai_khoan
            self._nguoi_nhan_cho_phep = self._tach_danh_sach_chuoi(
                cau_hinh.get("nguoi_nhan_cho_phep"))
            self._mang_noi_bo = str(cau_hinh.get("mang_noi_bo") or "").strip().lower() == "co"
            _MAT_KHAU_DANG_DUNG.update(mk for mk in mat_khaus if mk)
            self._dung = False
            self._loi_lien_tiep = {}
            self._tam_dung_vi_loi = set()
            self._da_bao_dung = set()
            self._doc_so_uid()
        except Exception as e:
            log.warning("email: cấu hình sai (%s) — module rỗng", e)
            self._tai_khoan = []

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm cấu hình: khuôn + máy chủ qua app/tu_xa.py. Lỗi → ok False
        + câu ngắn; không ném, không lộ mật khẩu."""
        try:
            tai_khoan, mat_khaus = self._phan_tich(cau_hinh)
        except Exception as e:
            return {"ok": False, "loi": f"cấu hình sai: {e}"}
        if not self._mang_noi_bo:
            for acc in tai_khoan:
                for host in (acc["imap_host"], acc["smtp_host"]):
                    try:
                        # Máy chủ thư công khai được phép; loopback/link-local/
                        # metadata bị chặn — đúng bộ kiểm app/tu_xa.py, không
                        # viết bộ thứ hai. Dải nội bộ: chủ máy khai tường minh
                        # mang_noi_bo="co" (README cảnh báo rủi ro SSRF).
                        await kiem_url_lan("https://" + host)
                    except ValueError as e:
                        return {"ok": False,
                                "loi": f"máy chủ {host}: {e}"}
                    except Exception as e:
                        return {"ok": False,
                                "loi": f"không kiểm được máy chủ {host} ({type(e).__name__})"}
        self._tai_khoan = tai_khoan
        for acc, mk in zip(tai_khoan, mat_khaus):
            acc["mat_khau"] = mk
        self._nguoi_nhan_cho_phep = self._tach_danh_sach_chuoi(
            cau_hinh.get("nguoi_nhan_cho_phep"))
        self._mang_noi_bo = str(cau_hinh.get("mang_noi_bo") or "").strip().lower() == "co"
        _MAT_KHAU_DANG_DUNG.update(mk for mk in mat_khaus if mk)
        self._doc_so_uid()
        # cau_hinh_luu: lõi tự tách bí mật (danh_sach_bi_mat) vào kho 0600 —
        # modules.json không bao giờ thấy mật khẩu.
        return {"ok": True, "cau_hinh_luu": {
            "tai_khoan": json.dumps(
                [{k: v for k, v in acc.items() if k != "mat_khau"} for acc in tai_khoan],
                ensure_ascii=False),
            "mat_khau": mat_khaus,
            "nguoi_nhan_cho_phep": ", ".join(self._nguoi_nhan_cho_phep),
            "mang_noi_bo": "co" if self._mang_noi_bo else "",
            "so_tai_khoan": len(tai_khoan),
        }}

    def kenh_ra(self) -> list[dict]:
        """Danh sách hộp thư gửi được — KHÔNG chứa mật khẩu."""
        return [{"id": a["id"], "ten": a["label"]} for a in self._tai_khoan]

    async def suc_khoe(self) -> dict:
        # Không ném; không lộ mật khẩu (chỉ email đã che + mã lỗi ngắn).
        rows = []
        ok_het = True
        try:
            for acc in self._tai_khoan:
                if not acc.get("enabled"):
                    rows.append({"id": acc["id"], "trang_thai": "tat", "loi": ""})
                    continue
                if acc["id"] in self._tam_dung_vi_loi:
                    rows.append({"id": acc["id"], "email_che": _che_email(acc["user"]),
                                 "trang_thai": "loi_lien_tiep",
                                 "loi": "dừng vì lỗi liên tiếp — kiểm tra cấu hình rồi cài lại"})
                    ok_het = False
                    continue
                try:
                    await asyncio.to_thread(self._kiem_tra_ket_noi_sync, acc)
                    rows.append({"id": acc["id"], "email_che": _che_email(acc["user"]),
                                 "trang_thai": "ok", "loi": ""})
                except _SaiDangNhap as e:
                    ok_het = False
                    rows.append({"id": acc["id"], "email_che": _che_email(acc["user"]),
                                 "trang_thai": "loi",
                                 "loi": "chưa kết nối được: " + _friendly_error(
                                     "authenticationfailed", acc["imap_host"]).lstrip("❌ ")})
                except Exception as e:
                    ok_het = False
                    rows.append({"id": acc["id"], "email_che": _che_email(acc["user"]),
                                 "trang_thai": "loi",
                                 "loi": "chưa kết nối được: " + _friendly_error(
                                     e, acc["imap_host"]).lstrip("❌ ")})
        except Exception as e:
            log.warning("email: suc_khoe loi_khong_ngu (%s)", type(e).__name__)
            return {"ok": False, "loi": "loi_khong_ngu"}
        return {"ok": ok_het and bool(self._tai_khoan),
                "so_tai_khoan": len(self._tai_khoan),
                "bo_qua_nguoi_la": self._bo_qua_nguoi_la,
                "loi_cuoi": self._loi_cuoi,
                "tai_khoan": rows}

    async def dung(self) -> None:
        """Gỡ module → dừng hẳn vòng nền («dừng là dừng hẳn»)."""
        self._dung = True

    # ── kết nối (lớp tiêm cho test) ──

    def _ket_noi_imap(self, acc: dict, timeout: int):
        host, port = acc["imap_host"], acc["imap_port"]
        sec = _sec_mode(acc)
        if sec == "ssl":
            return self._imap_ssl(host, port, timeout=timeout, ssl_context=_ssl_ctx(acc))
        m = self._imap(host, port, timeout=timeout)
        if sec == "starttls":
            m.starttls(_ssl_ctx(acc))
        return m

    def _dang_nhap_imap(self, acc: dict, timeout: int):
        """Mở IMAP + LOGIN. Ném _SaiDangNhap / _MangLoi (không mang bí mật)."""
        try:
            m = self._ket_noi_imap(acc, timeout)
        except (OSError, ssl.SSLError) as e:
            log.warning("email: %s ket noi that bai (%s)",
                        _che_email(acc["user"]), type(e).__name__)
            raise _MangLoi from None
        try:
            m.login(acc["user"], acc["mat_khau"])
        except imaplib.IMAP4.error:
            with contextlib.suppress(Exception):
                m.logout()
            log.warning("email: %s dang nhap that bai", _che_email(acc["user"]))
            raise _SaiDangNhap from None
        except (OSError, ssl.SSLError) as e:
            with contextlib.suppress(Exception):
                m.logout()
            log.warning("email: %s loi mang khi dang nhap (%s)",
                        _che_email(acc["user"]), type(e).__name__)
            raise _MangLoi from None
        return m

    def _kiem_tra_ket_noi_sync(self, acc: dict) -> None:
        m = self._dang_nhap_imap(acc, 10)
        try:
            typ, _ = m.select("INBOX")
            if typ != "OK":
                raise _MangLoi("khong mo duoc INBOX")
        finally:
            with contextlib.suppress(Exception):
                m.logout()

    # ── hỏi vòng (lõi gọi) ──

    def _uid_cua_thu(self, msg: email.message.Message, raw: bytes) -> str:
        mid = str(msg.get("Message-ID") or "").strip().strip("<>")
        if mid and len(mid) <= 200 and _MAU_DIA_CHI.search(mid.replace("<", "").replace(">", "")):
            return f"mid:{mid[:180]}"
        return "hash:" + hashlib.sha256(raw[:4096]).hexdigest()[:24]

    @staticmethod
    def _luc_iso(msg: email.message.Message) -> str | None:
        try:
            dt = parsedate_to_datetime(str(msg.get("Date") or ""))
            return dt.isoformat()
        except Exception:
            return None

    def _xu_ly_thu(self, acc: dict, raw: bytes) -> dict | None | str:
        """Trả tin chuẩn | None (bỏ) | 'loi' (đếm lỗi)."""
        try:
            msg = email.message_from_bytes(raw, policy=email.policy.default)
        except Exception as e:
            log.warning("email: parse thu loi (%s)", type(e).__name__)
            return "loi"
        from_hdr = _decode_hdr(msg.get("From"))
        _, from_addr = parseaddr(from_hdr)
        from_addr = (from_addr or "").strip().lower()
        if not from_addr or len(from_addr) > _NGUOI_GUI_TOI_DA:
            return None
        if not _MAU_DIA_CHI.match(from_addr):
            return None
        if not _allowed(acc, from_addr):
            self._bo_qua_nguoi_la += 1
            log.info("email: bo thu tu nguoi gui ngoai danh sach (%s)",
                     _che_email(from_addr))
            return None
        uid = self._uid_cua_thu(msg, raw)
        if uid in self._da_thay.get(acc["id"], {}):
            return None
        subject = _decode_hdr(msg.get("Subject"))[:_TIEU_DE_TOI_DA]
        body = _extract_body(msg, min(int(acc.get("max_body_chars") or 6000),
                                      TRAN_VAN_BAN - 600))
        atts = _attachments(msg)
        if not body and not subject and not atts:
            return None
        self._nho_uid(acc["id"], uid)
        parts = []
        if subject:
            parts.append(f"Tiêu đề: {subject}")
        if body:
            parts.append(body)
        for a in atts:
            if a.get("text"):
                parts.append(f"--- Tệp đính kèm: {a['name'][:80]} ---\n{a['text']}")
        van_ban = lam_sach_van_ban("\n\n".join(parts)).strip()[:TRAN_VAN_BAN]
        if not van_ban:
            return None
        ten_hien_thi = parseaddr(from_hdr)[0]
        return {
            "id_tin": f"email:{acc['id']}:{uid}",
            "nguoi_gui": from_addr,
            "noi_gui": acc["id"],
            "la_nhom": False,
            "ten_tu_khai": lam_sach_ten(ten_hien_thi),
            "van_ban": van_ban,
            "luc": self._luc_iso(msg),
        }

    def _poll_account_sync(self, acc: dict) -> list[dict]:
        """Đọc thư CHƯA ĐỌC của một hộp. Chạy trong thread (blocking)."""
        tins: list[dict] = []
        m = self._dang_nhap_imap(acc, _HET_GIO_IMAP)
        try:
            typ, _ = m.select("INBOX")
            if typ != "OK":
                raise _MangLoi("khong mo duoc INBOX")
            typ, data = m.search(None, "UNSEEN")
            ids = data[0].split() if (typ == "OK" and data and data[0]) else []
            for num in ids[:_BURST_POLL]:
                typ, msg_data = m.fetch(num, "(RFC822)")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                ket_qua = self._xu_ly_thu(acc, bytes(raw))
                if isinstance(ket_qua, dict):
                    tins.append(ket_qua)
                if ket_qua is not None and acc.get("mark_seen", True):
                    with contextlib.suppress(Exception):
                        # Chỉ đánh dấu đã đọc; mọi thao tác ghi khác không dùng.
                        m.store(num, "+FLAGS", "\\Seen")
        finally:
            with contextlib.suppress(Exception):
                m.logout()
        return tins

    def _ghi_nhan_loi(self, acc: dict, ten_loi: str) -> None:
        acc_id = acc["id"]
        n = self._loi_lien_tiep.get(acc_id, 0) + 1
        self._loi_lien_tiep[acc_id] = n
        if n >= _LOI_LIEN_TIEP_DUNG and acc_id not in self._tam_dung_vi_loi:
            self._tam_dung_vi_loi.add(acc_id)
            self._da_bao_dung.add(acc_id)
            # Báo MỘT lần — không spam log mỗi lượt poll.
            log.warning("email: %s dung vi loi lien tiep (%d lan, %s)",
                        _che_email(acc["user"]), n, ten_loi)
            self._loi_cuoi = f"{acc_id}: dung_vi_loi_lien_tiep"

    async def hoi_vong(self) -> list[dict]:
        """Một lượt hỏi vòng mọi hộp thư đang bật. Lõi đẩy từng tin vào
        `kenh_vao.nhan_tin`; lõi tự lùi dần khi hàm này ném."""
        if self._dung:
            return []
        tins: list[dict] = []
        bay_gio = time.monotonic()
        co_doi = False
        for acc in self._tai_khoan:
            if not acc.get("enabled"):
                continue
            acc_id = acc["id"]
            if acc_id in self._tam_dung_vi_loi:
                continue  # đã dừng vì lỗi liên tiếp — chờ cài lại
            # Lùi dần theo cấp số nhân khi lỗi (tối đa 5 phút).
            loi = self._loi_lien_tiep.get(acc_id, 0)
            nghi = min(acc["poll_seconds"] * (2 ** loi), _LUI_TOI_DA_GIAY)
            if bay_gio - self._lan_poll.get(acc_id, 0.0) < nghi:
                continue
            self._lan_poll[acc_id] = bay_gio
            try:
                batch = await asyncio.to_thread(self._poll_account_sync, acc)
            except (_SaiDangNhap, _MangLoi) as e:
                self._ghi_nhan_loi(acc, type(e).__name__)
                self._loi_cuoi = f"{acc_id}: {type(e).__name__}"
                continue
            except Exception as e:
                log.warning("email: %s poll loi khong ngu (%s)",
                            _che_email(acc["user"]), type(e).__name__)
                self._ghi_nhan_loi(acc, type(e).__name__)
                continue
            self._loi_lien_tiep[acc_id] = 0
            tins.extend(batch)
            if batch:
                co_doi = True
        if co_doi:
            self._ghi_so_uid()
        return tins

    # ── gửi tin ──

    def _gui_smtp_sync(self, acc: dict, to_addr: str, subject: str, body: str) -> None:
        user = acc["user"]
        host, port = acc["smtp_host"], int(acc["smtp_port"])
        msg = EmailMessage()
        msg["From"] = user
        msg["To"] = to_addr
        msg["Subject"] = subject or "(không tiêu đề)"
        msg.set_content(body or "(trống)")
        sec = _sec_mode(acc)
        try:
            if sec == "ssl" and port != 587:
                with self._smtp_ssl(host, port, timeout=_HET_GIO_SMTP,
                                    context=_ssl_ctx(acc)) as smtp:
                    smtp.login(user, acc["mat_khau"])
                    smtp.send_message(msg)
            else:
                with self._smtp(host, port, timeout=_HET_GIO_SMTP) as smtp:
                    smtp.ehlo()
                    if sec != "plain" or port == 587:
                        smtp.starttls(context=_ssl_ctx(acc))
                        smtp.ehlo()
                    smtp.login(user, acc["mat_khau"])
                    smtp.send_message(msg)
        except smtplib.SMTPAuthenticationError:
            log.warning("email: %s smtp dang nhap that bai", _che_email(user))
            raise _SaiDangNhap from None
        except (OSError, ssl.SSLError, smtplib.SMTPException) as e:
            log.warning("email: %s smtp loi (%s)", _che_email(user), type(e).__name__)
            raise _MangLoi from None

    async def gui_tin(self, dich: dict, noi_dung: dict) -> dict:
        """Gửi email qua hộp thư của đích. Trả {"ok": True} chỉ khi gửi thật."""
        if not self._tai_khoan:
            return {"ok": False, "ma": "chua_cau_hinh"}
        kenh_id = str((dich or {}).get("kenh_id") or (dich or {}).get("id") or "").strip()
        acc = next((a for a in self._tai_khoan if a["id"] == kenh_id), None)
        if acc is None:
            return {"ok": False, "ma": "tai_khoan_sai"}
        nd = noi_dung or {}
        van_ban = nd.get("van_ban")
        tieu_de = nd.get("tieu_de") or ""
        nguoi_nhan = nd.get("nguoi_nhan") or ""
        if not isinstance(van_ban, str) or not van_ban.strip():
            return {"ok": False, "ma": "noi_dung_sai"}
        if not isinstance(tieu_de, str):
            return {"ok": False, "ma": "tieu_de_sai"}
        # Chống chèn header bằng \r\n (tấn công Bcc lén).
        if any(c in tieu_de for c in "\r\n"):
            return {"ok": False, "ma": "tieu_de_sai"}
        nguoi_nhan_sach = _dia_chi_hop_le(nguoi_nhan)
        if not nguoi_nhan_sach:
            return {"ok": False, "ma": "nguoi_nhan_sai"}
        if self._nguoi_nhan_cho_phep and not _khop_danh_sach(
                self._nguoi_nhan_cho_phep, nguoi_nhan_sach):
            log.info("email: tu choi gui toi nguoi nhan ngoai danh sach (%s)",
                     _che_email(nguoi_nhan_sach))
            return {"ok": False, "ma": "nguoi_nhan_la"}
        try:
            await asyncio.to_thread(self._gui_smtp_sync, acc, nguoi_nhan_sach,
                                    tieu_de[:_TIEU_DE_TOI_DA], van_ban)
        except _SaiDangNhap:
            return {"ok": False, "ma": "sai_dang_nhap"}
        except _MangLoi:
            return {"ok": False, "ma": "mang_loi"}
        except Exception as e:
            log.warning("email: gui tin loi_khong_ngu (%s)", type(e).__name__)
            return {"ok": False, "ma": "loi_khong_ngu"}
        return {"ok": True}
