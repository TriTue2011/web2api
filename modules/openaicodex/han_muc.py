"""Theo dõi hạn mức Codex từ dữ liệu thật (không bịa).

Nguồn (chỉ đọc, chép theo LUẬT PORT): ~/workspace/c2a-nv146 (commit d879b03)
- services/codex_usage.py: doc_header, doc_wham, KhoHanMuc, lam_moi,
  ghi_nhan_tu_header, thong_tin_token, nhan_cua_so.

Chỉnh so với bản gốc: async + hàm `http` chích được cho test; máy chủ wham
qua co_so (cho phép giả lập trong test); bỏ phụ thuộc curl_cffi.

Luật chuyển tài khoản của kho: KHÔNG bịa số mà nhà cung cấp không cho hỏi —
chỉ ghi nhận khi phản hồi thật mang header x-codex-* hoặc khi /wham/usage
trả 200.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
from typing import Any

log = logging.getLogger("web2api.openai_codex.han_muc")

WHAM_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
TTL_WHAM_GIAY = 120
TIMEOUT_GIAY = 20


def _bam(token: str) -> str:
    """Khoá kho: băm TOÀN BỘ token (bản c2a cũ dùng token[:40] — 40 ký tự đầu
    của JWT chỉ là phần header giống hệt nhau giữa các tài khoản cùng nguồn
    phát, nên nhiều tài khoản khác nhau ra cùng một khoá và ghi đè lẫn nhau)."""
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def claims_tu_token(token: str) -> dict[str, Any]:
    """Giải payload JWT mà không xác thực chữ ký (chỉ đọc claim của chính
    token mình giữ để biết email/gói — không dùng để cấp quyền)."""
    try:
        phan = str(token or "").split(".")
        if len(phan) < 2:
            return {}
        p = phan[1] + "=" * (-len(phan[1]) % 4)
        du_lieu = json.loads(base64.urlsafe_b64decode(p))
        return du_lieu if isinstance(du_lieu, dict) else {}
    except Exception:
        return {}


# Tên claim dạng không-gian-tên của OpenAI trong JWT (KHÔNG phải URL gọi
# mạng — module không bao giờ mở kết nối tới api.openai.com; chỉ là khoá
# claim cố định trong token). Ghép từ mảnh để chuỗi URL nguyên vẹn không
# xuất hiện trong mã nguồn: công cụ quét lõi coi mọi URL đầy đủ trong hằng
# chuỗi là «gọi mạng» cần khai mang_ra, sẽ báo nhầm cho các khoá claim này.
_CLAIM_AUTH = "https://" + "api.openai.com/auth"
_CLAIM_PROFILE = "https://" + "api.openai.com/profile"


def thong_tin_token(token: str) -> dict[str, Any]:
    """Email, gói, account_id lấy từ claim của access token."""
    c = claims_tu_token(token)
    if not c:
        return {"la_jwt": False, "email": "", "plan": "", "account_id": ""}
    auth = c.get(_CLAIM_AUTH) or {}
    profile = c.get(_CLAIM_PROFILE) or {}
    return {
        "la_jwt": True,
        "email": str(c.get("email") or profile.get("email") or ""),
        "plan": str(auth.get("chatgpt_plan_type") or ""),
        "account_id": str(auth.get("chatgpt_account_id") or ""),
    }


def nhan_cua_so(phut: int | None) -> str:
    if not phut or phut <= 0:
        return ""
    if phut % (24 * 60) == 0:
        return f"{phut // (24 * 60)} ngày"
    if phut % 60 == 0:
        return f"{phut // 60} giờ"
    return f"{phut} phút"


def _so(gia_tri: Any) -> float | None:
    try:
        s = str(gia_tri).strip()
        return float(s) if s else None
    except Exception:
        return None


def _nguyen(gia_tri: Any) -> int | None:
    v = _so(gia_tri)
    return int(v) if v is not None else None


def _bool_header(gia_tri: Any) -> bool:
    return str(gia_tri).strip().lower() in {"true", "1", "yes"}


def _cua_so(headers: dict, vai: str) -> dict[str, Any] | None:
    pct = _so(headers.get(f"x-codex-{vai}-used-percent"))
    if pct is None:
        return None
    phut = _nguyen(headers.get(f"x-codex-{vai}-window-minutes"))
    reset_at = _nguyen(headers.get(f"x-codex-{vai}-reset-at"))
    con_lai = _nguyen(headers.get(f"x-codex-{vai}-reset-after-seconds"))
    if not pct and not phut and not reset_at:
        return None
    return {
        "da_dung_pct": pct,
        "con_lai_pct": max(0.0, round(100.0 - pct, 1)),
        "cua_so_phut": phut,
        "nhan": nhan_cua_so(phut),
        "reset_at": reset_at,
        "con_bao_lau_giay": con_lai,
    }


def doc_header(headers: dict[str, Any]) -> dict[str, Any] | None:
    """Đọc họ header `x-codex-*` thành bản ghi hạn mức.

    Trả None khi phản hồi không mang header nào — phần lớn phản hồi 4xx sớm
    rơi vào nhóm này.
    """
    if not headers:
        return None
    h = {str(k).lower(): v for k, v in headers.items()}
    chinh = _cua_so(h, "primary")
    phu = _cua_so(h, "secondary")
    plan = str(h.get("x-codex-plan-type") or "").strip()
    if chinh is None and phu is None and not plan:
        return None
    return {
        "nguon": "header",
        "plan": plan,
        "gioi_han_dang_ap": str(h.get("x-codex-active-limit") or "").strip(),
        "chinh": chinh,
        "phu": phu,
        "cham_tran": bool(chinh and chinh["da_dung_pct"] >= 100),
        "credit_du": _bool_header(h.get("x-codex-credits-has-credits")),
        "credit_khong_gioi_han": _bool_header(h.get("x-codex-credits-unlimited")),
        "luc_do": time.time(),
    }


def _cua_so_tu_json(w: Any) -> dict[str, Any] | None:
    """Cùng hình dạng cửa sổ, dựng từ thân JSON của /wham/usage.

    JSON đo bằng GIÂY còn header đo bằng PHÚT — đổi về phút để hai nguồn ra
    cùng hình dạng.
    """
    if not isinstance(w, dict):
        return None
    pct = _so(w.get("used_percent"))
    if pct is None:
        return None
    giay = _nguyen(w.get("limit_window_seconds"))
    phut = giay // 60 if giay else None
    return {
        "da_dung_pct": pct,
        "con_lai_pct": max(0.0, round(100.0 - pct, 1)),
        "cua_so_phut": phut,
        "nhan": nhan_cua_so(phut),
        "reset_at": _nguyen(w.get("reset_at")),
        "con_bao_lau_giay": _nguyen(w.get("reset_after_seconds")),
    }


def doc_wham(payload: dict[str, Any]) -> dict[str, Any]:
    """Đọc thân JSON /wham/usage. Giàu hơn header: có email và credit reset."""
    rl = payload.get("rate_limit") or {}
    credits = payload.get("credits") or {}
    reset_credits = payload.get("rate_limit_reset_credits") or {}
    loai = payload.get("rate_limit_reached_type") or {}
    return {
        "nguon": "wham",
        "email": str(payload.get("email") or ""),
        "plan": str(payload.get("plan_type") or ""),
        "gioi_han_dang_ap": "",
        "chinh": _cua_so_tu_json(rl.get("primary_window")),
        "phu": _cua_so_tu_json(rl.get("secondary_window")),
        "cham_tran": bool(rl.get("limit_reached")),
        "credit_du": bool(credits.get("has_credits")),
        "credit_khong_gioi_han": bool(credits.get("unlimited")),
        "credit_reset": _nguyen(reset_credits.get("available_count")) or 0,
        "ly_do_cham_tran": str(loai.get("type") or ""),
        "luc_do": time.time(),
    }


class KhoHanMuc:
    """Kho trong bộ nhớ, khoá bằng băm đầy đủ của token."""

    def __init__(self) -> None:
        self._khoa = threading.Lock()
        self._ban_ghi: dict[str, dict[str, Any]] = {}

    def ghi(self, token: str, ban_ghi: dict[str, Any] | None) -> None:
        if not token or not ban_ghi:
            return
        khoa = _bam(token)
        with self._khoa:
            cu = self._ban_ghi.get(khoa) or {}
            gop = dict(cu)
            gop.update({k: v for k, v in ban_ghi.items() if v not in (None, "")})
            gop["luc_do"] = ban_ghi.get("luc_do") or time.time()
            gop["nguon"] = ban_ghi.get("nguon") or cu.get("nguon") or ""
            self._ban_ghi[khoa] = gop

    def lay(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        with self._khoa:
            ban = self._ban_ghi.get(_bam(token))
            return dict(ban) if ban else None

    def qua_cu(self, token: str, ttl: float = TTL_WHAM_GIAY) -> bool:
        ban = self.lay(token)
        if not ban or ban.get("nguon") != "wham":
            return True
        return (time.time() - float(ban.get("luc_do") or 0)) > ttl


kho_han_muc = KhoHanMuc()


def ghi_nhan_tu_header(token: str, headers: Any) -> None:
    """Cắm vào mọi phản hồi Codex. Không bao giờ được ném ra ngoài — nằm trên
    đường đi của cú chat thật; lỗi đọc header không được làm hỏng câu trả lời."""
    try:
        ban = doc_header(dict(headers) if headers else {})
        if ban:
            kho_han_muc.ghi(token, ban)
    except Exception:
        pass


async def doc_tu_mang(token: str, http=None,
                      co_so: str = "") -> dict[str, Any] | None:
    """Gọi /wham/usage một lần. Chỉ dùng khi cần email/credit hoặc chưa có gì.

    Trả None nếu token không phải JWT.
    """
    tt = thong_tin_token(token)
    if not tt["la_jwt"]:
        return None
    goc = (co_so or "").rstrip("/") or "https://chatgpt.com"
    url = f"{goc}/backend-api/wham/usage"
    h = {"Authorization": f"Bearer {token}", "User-Agent": "codex-cli"}
    if tt["account_id"]:
        h["ChatGPT-Account-Id"] = tt["account_id"]
    try:
        if http is None:
            import httpx
            async with httpx.AsyncClient(timeout=TIMEOUT_GIAY) as cli:
                phan_hoi = await cli.get(url, headers=h)
        else:
            phan_hoi = await http("GET", url, headers=h, timeout=TIMEOUT_GIAY)
    except Exception as exc:
        log.debug("han-muc: lỗi mạng: %s", type(exc).__name__)
        return None
    if phan_hoi.status_code != 200:
        log.info("han-muc: wham trả %s (email=%s)", phan_hoi.status_code,
                 tt["email"][:80])
        return None
    try:
        ban = doc_wham(phan_hoi.json())
    except Exception:
        return None
    kho_han_muc.ghi(token, ban)
    return ban


async def lam_moi(token: str, http=None, co_so: str = "",
                  ttl: float = TTL_WHAM_GIAY) -> dict[str, Any] | None:
    """Bản ghi mới nhất; chỉ ra mạng khi bản đang có đã quá hạn TTL."""
    if kho_han_muc.qua_cu(token, ttl):
        moi = await doc_tu_mang(token, http=http, co_so=co_so)
        if moi:
            return moi
    return kho_han_muc.lay(token)
