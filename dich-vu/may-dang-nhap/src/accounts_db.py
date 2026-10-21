# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/accounts_db.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
"""SQLite storage for Google account credentials (email, password, TOTP secret).

Database is stored at /data/accounts.db (outside the repo).
DO NOT commit this file or its data to git.

`password` và `totp_secret` được mã hoá tại chỗ bằng AES-256-GCM khi
`VAULT_MASTER_KEY` có mặt (xem `vault.py`). Chưa đặt khoá thì vẫn ghi chữ
thường như cũ và có log cảnh báo — bắt buộc biến môi trường sẽ làm container
không lên được sau khi cập nhật.

Bản ghi cũ chưa mã hoá vẫn đọc được, và tự mã hoá ở lần GHI kế tiếp.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from typing import Optional

from . import vault

_DB_PATH = os.environ.get("ACCOUNTS_DB", "/data/accounts.db")


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    c = sqlite3.connect(_DB_PATH)
    c.execute("PRAGMA journal_mode=WAL")
    c.row_factory = sqlite3.Row
    # W2A: Đảm bảo accounts.db luôn có quyền 0600 rõ ràng khi tạo hoặc mở
    if os.path.exists(_DB_PATH):
        try:
            if stat.S_IMODE(os.stat(_DB_PATH).st_mode) != 0o600:
                os.chmod(_DB_PATH, 0o600)
        except OSError:
            pass
    return c


# Hai kho tách biệt trong cùng một bảng, phân bằng cột `loai`:
#
#   'google' — tài khoản Google. Mật khẩu là mật khẩu GOOGLE, 2FA của Google.
#              Phục vụ Flow, Gemini web, Claude web, ChatGPT-qua-Google, Codex.
#   'openai' — tài khoản OpenAI gốc. Mật khẩu là mật khẩu của chính OPENAI,
#              2FA của OpenAI. Chỉ phục vụ luồng `openai_native_login`.
#
# VÌ SAO PHẢI TÁCH: cùng một địa chỉ `@gmail.com` có thể vừa là tài khoản Google
# vừa là tài khoản OpenAI gốc, với HAI mật khẩu khác hẳn nhau. Bảng cũ để
# `email` UNIQUE nên hai thứ đó đè lên nhau, và `resolve_account` khớp theo
# localpart nên luồng đăng nhập OpenAI có thể nhận về mật khẩu Google (và ngược
# lại) — sai mật khẩu liên tiếp là đường ngắn nhất tới khoá tài khoản.
LOAI_GOOGLE = "google"
LOAI_OPENAI = "openai"
_LOAI_HOP_LE = (LOAI_GOOGLE, LOAI_OPENAI)


def _chuan_loai(loai: str | None) -> str:
    l = str(loai or "").strip().lower()
    return l if l in _LOAI_HOP_LE else LOAI_GOOGLE


def _thu_muc_profile() -> str:
    """Thư mục hồ sơ trình duyệt, suy từ đường dẫn DB (cùng `data_dir`)."""
    return os.path.join(os.path.dirname(_DB_PATH), "profiles")


def _loai_theo_ho_so(email: str) -> str:
    """Đoán loại của một bản ghi CŨ bằng hồ sơ trình duyệt có thật trên đĩa.

    Chỉ dùng MỘT LẦN lúc chuyển đổi. Hồ sơ `openai-<localpart>` chỉ có thể do
    thẻ OpenAI gốc tạo ra, nên nó là bằng chứng chắc chắn; không có thì mặc định
    'google' — đúng với mọi bản ghi có từ trước, vì trước đây kho này chỉ dùng
    cho tài khoản Google.
    """
    local = (email or "").split("@")[0]
    an_toan = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in local)
    try:
        co = set(os.listdir(_thu_muc_profile()))
    except OSError:
        return LOAI_GOOGLE
    for ten in (local, an_toan, an_toan.lower()):
        if f"openai-{ten}" in co:
            return LOAI_OPENAI
    return LOAI_GOOGLE


def init_db() -> None:
    c = _conn()
    c.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            password TEXT NOT NULL,
            totp_secret TEXT NOT NULL DEFAULT '',
            label TEXT DEFAULT '',
            loai TEXT NOT NULL DEFAULT 'google',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (email, loai)
        )
    """)
    cot = {r[1] for r in c.execute("PRAGMA table_info(accounts)")}
    if "loai" not in cot:
        # Bảng cũ: `email` UNIQUE một mình. Phải DỰNG LẠI mới đổi được ràng buộc
        # sang UNIQUE(email, loai) — SQLite không ALTER được UNIQUE. Làm gọn
        # trong MỘT transaction: DDL của SQLite có transaction, nên hoặc xong
        # hẳn hoặc không đổi gì. Kho này giữ credential, nửa vời là mất mật khẩu.
        cu = c.execute("SELECT email, password, totp_secret, label, "
                       "created_at, updated_at FROM accounts").fetchall()
        c.execute("BEGIN")
        try:
            c.execute("""
                CREATE TABLE accounts_moi (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL,
                    password TEXT NOT NULL,
                    totp_secret TEXT NOT NULL DEFAULT '',
                    label TEXT DEFAULT '',
                    loai TEXT NOT NULL DEFAULT 'google',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (email, loai)
                )
            """)
            for r in cu:
                c.execute(
                    "INSERT INTO accounts_moi (email, password, totp_secret, label, "
                    "loai, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                    (r["email"], r["password"], r["totp_secret"], r["label"],
                     _loai_theo_ho_so(r["email"]), r["created_at"], r["updated_at"]),
                )
            c.execute("DROP TABLE accounts")
            c.execute("ALTER TABLE accounts_moi RENAME TO accounts")
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    c.commit()
    c.close()


def list_accounts(loai: str | None = None) -> list[dict]:
    """Danh sách tài khoản — KHÔNG kèm mật khẩu lẫn hạt giống TOTP.

    `loai=None` trả CẢ HAI kho (dùng cho việc quản trị/di trú). Giao diện và
    mọi luồng đăng nhập phải truyền loại rõ ràng, nếu không thẻ OpenAI gốc lại
    hiện tài khoản Google và người dùng chọn nhầm.

    Bản cũ ghi chú "without password for safety" nhưng vẫn trả `totp_secret`.
    Hạt giống TOTP không phải "mã 6 số": nó SINH RA mọi mã 6 số từ nay về sau,
    nên lộ nó là mất luôn yếu tố thứ hai chứ không chỉ một lần đăng nhập. Ở đây
    chỉ trả cờ `has_totp` — đủ cho giao diện, không đủ để mạo danh.
    """
    c = _conn()
    if loai is None:
        rows = c.execute(
            "SELECT id, email, totp_secret, label, loai, created_at, updated_at "
            "FROM accounts ORDER BY email"
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT id, email, totp_secret, label, loai, created_at, updated_at "
            "FROM accounts WHERE loai = ? ORDER BY email", (_chuan_loai(loai),)
        ).fetchall()
    c.close()
    ra = []
    for r in rows:
        d = dict(r)
        d["has_totp"] = bool(str(d.pop("totp_secret", "") or "").strip())
        ra.append(d)
    return ra


def get_account(email: str, loai: str | None = None) -> Optional[dict]:
    """Bản ghi đầy đủ, đã giải mã. Chỉ dùng cho luồng đăng nhập thật.

    `loai=None` = lấy bản ghi Google (giữ nguyên hành vi của mọi nơi gọi cũ).
    Luồng OpenAI gốc PHẢI truyền `loai='openai'`: cùng một địa chỉ email có thể
    có hai bản ghi với hai mật khẩu khác nhau, lấy nhầm là gõ mật khẩu Google
    vào form OpenAI rồi bị khoá vì sai liên tiếp.
    """
    c = _conn()
    row = c.execute("SELECT * FROM accounts WHERE email = ? AND loai = ?",
                    (email, _chuan_loai(loai))).fetchone()
    c.close()
    if not row:
        return None
    d = dict(row)
    goc = str(d.get("email") or "")
    d["password"] = vault.giai_ma(d.get("password") or "", goc, "password")
    d["totp_secret"] = vault.giai_ma(d.get("totp_secret") or "", goc, "totp_secret")
    return d


def loai_theo_profile(profile_or_email: str) -> str:
    """Hồ sơ `openai-…` thuộc kho OpenAI gốc; mọi thứ còn lại thuộc kho Google."""
    return (LOAI_OPENAI if str(profile_or_email or "").strip().lower().startswith("openai-")
            else LOAI_GOOGLE)


_TIEN_TO_HO_SO = ("google-", "chatgpt-web-", "chatgpt-", "openai-",
                  "gemini-web-", "gemini-", "claude-web-", "claude-",
                  "codex-", "github-")


def chuan_localpart(ten: str, *, bo_tien_to: bool = True) -> str:
    """Rút một tên hồ sơ hoặc một địa chỉ email về CHỈ chữ-và-số, để so hai bên.

    Tên hồ sơ sinh ra bằng cách đổi MỌI ký tự không phải chữ/số thành '-'
    (`account_recovery._profile_for`, `openai-native-card.tsx`), nên
    `nguoi.e+nhan` thành `nguoi-e-nhan`. Bản cũ chỉ bỏ
    '-' và '.' nên vế email vẫn còn dấu '+' — hai bên không đời nào khớp.

    Đo thật 20/08/2026 trên máy chủ: `resolve_account("openai-nguoi-e-nhan")` trả None dù kho có đúng bản ghi
    `nguoi.e+nhan@example.com` (kho 'openai', đủ cả mật khẩu lẫn hạt
    giống TOTP). Hậu quả: tầng đăng nhập lại của
    `GET /v1/chatgpt/{profile}/refresh-jwt` báo "no saved credentials found for
    this profile" và tài khoản nằm chết chờ người. Cả hai tài khoản OpenAI gốc
    đang lưu đều có dấu '+' trong địa chỉ, nên đường này chưa từng chạy được.
    """
    s = str(ten or "").strip().lower()
    if bo_tien_to:
        for pfx in _TIEN_TO_HO_SO:
            if s.startswith(pfx):
                s = s[len(pfx):]
                break
    return "".join(ch for ch in s.split("@")[0] if ch.isalnum())


def resolve_account(profile_or_email: str, loai: str | None = None) -> Optional[dict]:
    """Tìm credential theo EMAIL chính xác, hoặc theo PROFILE NAME (google-nguoi-b,
    chatgpt-nguoi-b, claude-web-nguoi-b...) bằng cách bỏ tiền tố dịch vụ rồi
    khớp localpart email. Nhờ vậy MỘT Google account đã lưu phục vụ MỌI profile dịch
    vụ cùng localpart → reuse không cần nhập lại mật khẩu.

    Việc khớp theo localpart CHỈ được phép trong CÙNG MỘT KHO. Không có ràng
    buộc đó thì `openai-nguoi-b` sẽ khớp trúng tài khoản Google
    `nguoi.b@example.com` và luồng đăng nhập OpenAI nhận về mật khẩu Google —
    sai mật khẩu vài lần liên tiếp là đủ để OpenAI khoá tài khoản.

    `loai=None` → suy từ tiền tố hồ sơ. Truyền tay khi người gọi chỉ có email.
    """
    if not profile_or_email:
        return None
    kho = _chuan_loai(loai) if loai else loai_theo_profile(profile_or_email)
    acct = get_account(profile_or_email, kho)     # khớp email chính xác
    if acct:
        return acct
    name = chuan_localpart(profile_or_email)
    if not name:
        return None
    khop = [a for a in list_accounts(kho)
            if chuan_localpart(str(a.get("email") or ""), bo_tien_to=False) == name]
    # Tên profile thay mọi ký tự lạ bằng '-', nên a+b, a-b, a.b và ab có thể
    # cùng một dạng chuẩn hoá. Chọn bản ghi đầu tiên ở đây đồng nghĩa đem mật
    # khẩu/TOTP của tài khoản khác đi thử; khi mơ hồ phải dừng để đăng nhập tay.
    if len(khop) != 1:
        return None
    return get_account(khop[0]["email"], kho)


def save_account(email: str, password: str, totp_secret: str = "", label: str = "",
                 loai: str | None = None) -> dict:
    """Insert or update an account. Returns the saved row (without password).

    Ghi vào ĐÚNG kho `loai`. Cùng một email ở hai kho là hai bản ghi độc lập —
    lưu mật khẩu OpenAI không được đè lên mật khẩu Google của cùng địa chỉ đó.
    """
    kho = _chuan_loai(loai)
    # Mã hoá TRƯỚC khi chạm SQL — không để giá trị thường đi vào câu lệnh,
    # vì SQLite ghi cả câu lệnh vào WAL.
    mk = vault.ma_hoa(password, email, "password")
    tt = vault.ma_hoa(totp_secret, email, "totp_secret")
    c = _conn()
    existing = c.execute("SELECT id FROM accounts WHERE email = ? AND loai = ?",
                         (email, kho)).fetchone()
    if existing:
        c.execute(
            """UPDATE accounts
               SET password = ?, totp_secret = ?, label = ?, updated_at = CURRENT_TIMESTAMP
               WHERE email = ? AND loai = ?""",
            (mk, tt, label, email, kho),
        )
    else:
        c.execute(
            "INSERT INTO accounts (email, password, totp_secret, label, loai) "
            "VALUES (?, ?, ?, ?, ?)",
            (email, mk, tt, label, kho),
        )
    c.commit()
    row = c.execute("SELECT id, email, totp_secret, label, loai, created_at, updated_at "
                    "FROM accounts WHERE email = ? AND loai = ?", (email, kho)).fetchone()
    c.close()
    if not row:
        return {}
    d = dict(row)
    d["has_totp"] = bool(str(d.pop("totp_secret", "") or "").strip())
    return d


def set_totp(email: str, totp_secret: str, loai: str | None = None) -> bool:
    """Chỉ cập nhật hạt giống TOTP, KHÔNG đụng mật khẩu.

    Cần một đường riêng vì `save_account` ghi cả mật khẩu — giao diện đặt TOTP
    thì không có mật khẩu trong tay, gọi `save_account` là xoá mất nó.
    """
    tt = vault.ma_hoa(totp_secret, email, "totp_secret")
    c = _conn()
    try:
        cur = c.execute(
            "UPDATE accounts SET totp_secret = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE email = ? AND loai = ?", (tt, email, _chuan_loai(loai)))
        ok = cur.rowcount > 0
        c.commit()
    finally:
        c.close()
    return ok


def delete_account(email: str, loai: str | None = None) -> bool:
    c = _conn()
    # `rowcount` nằm trên CON TRỎ, không nằm trên kết nối. Bản cũ đọc
    # `c.rowcount` nên ném AttributeError ngay TRƯỚC `c.commit()` — lệnh xoá
    # không bao giờ được ghi xuống, tài khoản vẫn nguyên trong kho rồi lần đồng
    # bộ sau lại nạp về, trông y như hệ thống "tự thêm tài khoản free".
    # Đo trên máy chủ 05/08: xoá nguoi.c@example.com trả HTTP 500, tới
    # 19:06 tài khoản đó xuất hiện lại với "Thêm 1 free".
    try:
        cur = c.execute("DELETE FROM accounts WHERE email = ? AND loai = ?",
                        (email, _chuan_loai(loai)))
        deleted = cur.rowcount > 0
        c.commit()
    finally:
        c.close()
    return deleted


# Initialize on import
init_db()
