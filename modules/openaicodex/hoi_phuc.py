"""Tài khoản Codex bị vô hiệu hoá (OpenAI `account_deactivated`).

Nguồn (chỉ đọc, chép theo LUẬT PORT): ~/workspace/c2a-nv146 (commit d879b03)
- services/codex_deactivated.py: đánh dấu status='deactivated' → ngừng thử
  lại, báo admin, chờ duyệt «xóa/giữ» (lưu bền pending).

Chỉnh so với bản gốc: pending lưu trong kho riêng mã hoá của module
(qua tay cầm kho do lõi trao) thay vì data/agent/*.json; KHÔNG port:
(1) lệnh chat «xóa <email>/giữ <email>» của admin (cần nối kenh_vao của lõi —
ghi trong «Phần KHÔNG port» của báo cáo), thay bằng hai hành động thiết bị
`xoa_tai_khoan` / `giu_tai_khoan`; (2) xoá dòng `codex_auto_list` (cấu hình
riêng của c2a, web2api không có).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger("web2api.openai_codex.hoi_phuc")

TEN_TEP_CHO = "deactivated_cho"
_khoa = threading.RLock()


def doc_cho_duyet(kho) -> list[dict[str, Any]]:
    """Danh sách pending «deactivated chờ duyệt xoá/giữ» (kho riêng module)."""
    try:
        du_lieu = kho.doc(TEN_TEP_CHO) or {}
    except Exception:
        return []
    muc = du_lieu.get("muc") if isinstance(du_lieu, dict) else None
    return [m for m in (muc or []) if isinstance(m, dict)]


def _luu_cho_duyet(kho, ds: list[dict]) -> None:
    kho.luu(TEN_TEP_CHO, {"muc": ds})


def danh_dau_deactivated(kho, email: str, ly_do: str = "") -> None:
    """Ghi pending deactivated (lũy đẳng theo email)."""
    email = str(email or "").strip()
    if not email:
        return
    with _khoa:
        ds = doc_cho_duyet(kho)
        khoa = email.lower()
        ds = [m for m in ds if str(m.get("email") or "").lower() != khoa]
        ds.append({"email": email, "ly_do": str(ly_do)[:160],
                   "luc": time.time()})
        _luu_cho_duyet(kho, ds)
    log.warning("openai-codex: tài khoản %s bị vô hiệu hoá (deactivated) — "
                "ngừng tự thử lại, chờ duyệt", _che_email(email))


def giai_quyet_cho_duyet(kho, email: str, giu: bool) -> bool:
    """Bỏ một pending khỏi danh sách. Trả True nếu đã từng có pending đó."""
    with _khoa:
        ds = doc_cho_duyet(kho)
        khoa = str(email or "").lower()
        moi = [m for m in ds if str(m.get("email") or "").lower() != khoa]
        if len(moi) == len(ds):
            return False
        _luu_cho_duyet(kho, moi)
    log.info("openai-codex: %s pending deactivated của %s",
             "giữ lại" if giu else "xoá", _che_email(email))
    return True


def _che_email(email: str) -> str:
    email = str(email or "")
    if "@" not in email:
        return ""
    ten, mien = email.rsplit("@", 1)
    return (ten[:1] + "***@" + mien) if ten else "***@" + mien
