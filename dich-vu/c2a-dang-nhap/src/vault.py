# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/vault.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
"""Mã hoá tại chỗ cho mật khẩu Google và hạt giống TOTP.

# W2A: Khoá chính lưu tại key_dir (mặc định /khoa, biến CAPTCHA_SOLVER_KEY_DIR),
tách riêng khỏi volume dữ liệu /data. CHỈ sao lưu volume dữ liệu; volume khoá giữ riêng,
mất khoá = mất mật khẩu đã lưu (cần nhập lại). Tự sinh khoá 32 byte base64 vào
<key_dir>/vault.key quyền 0600 nếu chưa đặt VAULT_MASTER_KEY. Nếu tệp khoá rỗng hoặc
hỏng, dừng dịch vụ (fail-fast) với RuntimeError tiếng Việt để bảo vệ dữ liệu đã mã hoá.

Thiết kế:
  - AES-256-GCM, nonce 12 byte riêng cho từng lần ghi.
  - AAD gồm email và tên trường.
  - Tiền tố phiên bản: v1:<nonce b64>:<ct b64>.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
import secrets
import stat

logger = logging.getLogger(__name__)

TIEN_TO = "v1:"
_da_canh_bao = False


# W2A: Đọc thư mục lưu trữ khoá key_dir để lưu hoặc đọc vault.key (tách riêng khỏi volume dữ liệu data_dir).
def _thu_muc_khoa() -> Path:
    from .settings import settings
    d = os.environ.get("CAPTCHA_SOLVER_KEY_DIR", "").strip()
    return Path(d) if d else settings.key_dir


# W2A: Đọc hoặc tự sinh khoá 32 byte tại <key_dir>/vault.key (0600, base64) nguyên tử; tệp rỗng/hỏng ném RuntimeError tiếng Việt.
def _khoa(tu_sinh: bool = False) -> bytes | None:
    """32 byte khoá chính: ưu tiên biến VAULT_MASTER_KEY; nếu chưa đặt thì đọc/sinh tệp vault.key (0600) trong key_dir."""
    raw = os.environ.get("VAULT_MASTER_KEY", "").strip()
    if raw:
        try:
            k = base64.b64decode(raw, validate=True)
            if len(k) == 32:
                return k
            raise RuntimeError(f"Biến VAULT_MASTER_KEY giải mã ra {len(k)} byte, cần đúng 32 byte.")
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError("Biến VAULT_MASTER_KEY không phải định dạng base64 hợp lệ.")

    tep_khoa = _thu_muc_khoa() / "vault.key"
    if tep_khoa.is_file():
        st = tep_khoa.stat()
        if stat.S_IMODE(st.st_mode) != 0o600:
            os.chmod(tep_khoa, 0o600)
        raw_text = tep_khoa.read_text(encoding="utf-8").strip()
        # W2A: Tệp rỗng hoặc sai chuẩn ném lỗi ngay để bảo vệ dữ liệu, không ghi đè khoá mới
        if not raw_text:
            raise RuntimeError(f"Tệp vault.key '{tep_khoa}' rỗng — dừng dịch vụ để bảo vệ dữ liệu đã mã hoá.")
        try:
            k = base64.b64decode(raw_text, validate=True)
            if len(k) == 32:
                return k
            raise RuntimeError(f"Tệp vault.key '{tep_khoa}' giải mã ra {len(k)} byte, cần đúng 32 byte.")
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(f"Tệp vault.key '{tep_khoa}' không phải định dạng base64 hợp lệ.")

    if tu_sinh:
        # W2A: nếu accounts.db đã có bản ghi tiền tố v1: mà không có khoá -> KHÔNG sinh khoá mới, ném VaultChuaSanSang
        if _co_ban_ghi_v1():
            raise VaultChuaSanSang("Két chưa sẵn sàng: thiếu khoá")

        k = secrets.token_bytes(32)
        thu_muc = _thu_muc_khoa()
        thu_muc.mkdir(parents=True, exist_ok=True)
        tep_tmp = thu_muc / f".vault.key.tmp.{secrets.token_hex(6)}"
        try:
            fd = os.open(tep_tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with open(fd, "w", encoding="utf-8") as f:
                f.write(base64.b64encode(k).decode() + "\n")
            os.chmod(tep_tmp, 0o600)
            try:
                # W2A: Tạo khoá lần đầu bằng O_EXCL / os.link để không đua hai tiến trình
                os.link(tep_tmp, tep_khoa)
                tep_tmp.unlink(missing_ok=True)
            except FileExistsError:
                tep_tmp.unlink(missing_ok=True)
                return _khoa(tu_sinh=False)
            except OSError:
                if tep_khoa.is_file():
                    tep_tmp.unlink(missing_ok=True)
                    return _khoa(tu_sinh=False)
                os.replace(tep_tmp, tep_khoa)
        except FileExistsError:
            if tep_khoa.is_file():
                return _khoa(tu_sinh=False)
            raise
        logger.info("Đã sinh vault master key mới tại: %s", tep_khoa)
        return k

    return None


def _duong_dan_db() -> Path:
    from .settings import settings
    d_env = os.environ.get("CAPTCHA_SOLVER_DATA_DIR", "").strip()
    data_dir = Path(d_env) if d_env else settings.data_dir
    db_env = os.environ.get("ACCOUNTS_DB", "").strip()
    if db_env:
        p = Path(db_env)
        if d_env and p.parent != Path(d_env):
            return Path(d_env) / "accounts.db"
        return p
    return data_dir / "accounts.db"


def _co_ban_ghi_v1() -> bool:
    """W2A: Kiểm tra accounts.db đã có bản ghi nào mang tiền tố v1: chưa."""
    db_path = _duong_dan_db()
    if not db_path.is_file():
        return False
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='accounts'"
        )
        if not cur.fetchone():
            conn.close()
            return False
        cur = conn.execute(
            "SELECT 1 FROM accounts WHERE password LIKE 'v1:%' OR totp_secret LIKE 'v1:%' LIMIT 1"
        )
        has_v1 = cur.fetchone() is not None
        conn.close()
        return has_v1
    except Exception:
        return False


def trang_thai_ket() -> tuple[bool, str]:
    """W2A: Trả về (san_sang, thong_diep). Khi accounts.db có bản ghi v1: mà mất khoá -> (False, 'két chưa sẵn sàng: thiếu khoá')."""
    if _co_ban_ghi_v1() and _khoa(tu_sinh=False) is None:
        return False, "két chưa sẵn sàng: thiếu khoá"
    return True, "san_sang"


def dang_bat() -> bool:
    return _khoa(tu_sinh=False) is not None


def _aad(email: str, truong: str) -> bytes:
    return f"{str(email or '').strip().lower()}|{str(truong or '').strip()}".encode("utf-8")


class VaultChuaSanSang(RuntimeError):
    """Chưa có khoá mà lại định ghi credential mới."""


def _bat_buoc_ma_hoa() -> bool:
    """`VAULT_REQUIRE_ENCRYPTION=1` → từ chối ghi credential khi chưa có khoá.

    Mặc định TẮT để bản ghi cũ và luồng đang chạy không đứng. Bật lên thì
    đường GHI fail-closed: thà không lưu được mật khẩu mới còn hơn lưu chữ
    thường trong khi chủ máy tưởng đã mã hoá.
    """
    return str(os.environ.get("VAULT_REQUIRE_ENCRYPTION", "")).strip() in {"1", "true", "yes", "on"}


def ma_hoa(gia_tri: str, email: str, truong: str) -> str:
    """Trả chuỗi để lưu vào DB. Chưa có khoá → trả nguyên giá trị."""
    gt = str(gia_tri or "")
    if not gt:
        return ""
    # W2A: Có sẵn v1: mà không khoá -> từ chối ngay, không tự sinh khoá mới
    if _co_ban_ghi_v1() and _khoa(tu_sinh=False) is None:
        raise VaultChuaSanSang("Két chưa sẵn sàng: thiếu khoá")
    # W2A: Gọi _khoa(tu_sinh=True) để tự sinh vault.key khi chưa có khoá.
    k = _khoa(tu_sinh=True)
    if k is None:
        if _bat_buoc_ma_hoa():
            raise VaultChuaSanSang(
                "VAULT_REQUIRE_ENCRYPTION đang bật nhưng VAULT_MASTER_KEY chưa "
                "đặt (hoặc sai định dạng) — từ chối ghi credential dạng chữ "
                "thường. Sinh khoá: python3 -c \"import base64,os;"
                "print(base64.b64encode(os.urandom(32)).decode())\"")
        return gt
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(12)
    ct = AESGCM(k).encrypt(nonce, gt.encode("utf-8"), _aad(email, truong))
    return TIEN_TO + base64.b64encode(nonce).decode() + ":" + base64.b64encode(ct).decode()


def giai_ma(luu_tru: str, email: str, truong: str) -> str:
    """Đọc giá trị. Chuỗi không có tiền tố = dữ liệu cũ, trả thẳng."""
    s = str(luu_tru or "")
    if not s or not s.startswith(TIEN_TO):
        return s
    # W2A: Gọi _khoa(tu_sinh=False) để không tự sinh khoá mới khi đang giải mã mà tệp khoá bị mất.
    k = _khoa(tu_sinh=False)
    if k is None:
        # Có ciphertext mà mất khoá: KHÔNG trả chuỗi rác ra ngoài. Trả rỗng để
        # nơi gọi thấy "không có mật khẩu" và báo lỗi rõ, thay vì đem chuỗi vô
        # nghĩa đi đăng nhập rồi nhận một lỗi chẳng liên quan.
        logger.error({"event": "vault_mat_khoa",
                      "msg": "accounts.db có dữ liệu đã mã hoá nhưng VAULT_MASTER_KEY "
                             "trống hoặc sai — không đọc được"})
        return ""
    try:
        _, nonce_b64, ct_b64 = s.split(":", 2)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        thuong = AESGCM(k).decrypt(base64.b64decode(nonce_b64),
                                   base64.b64decode(ct_b64), _aad(email, truong))
        return thuong.decode("utf-8")
    except Exception as exc:
        logger.error({"event": "vault_giai_ma_hong", "error": type(exc).__name__})
        return ""


def da_ma_hoa(luu_tru: str) -> bool:
    return str(luu_tru or "").startswith(TIEN_TO)


__all__ = ["TIEN_TO", "VaultChuaSanSang", "da_ma_hoa", "dang_bat",
           "giai_ma", "ma_hoa"]
