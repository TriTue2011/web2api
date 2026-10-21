# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/settings.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
from __future__ import annotations

import logging
import os
from pathlib import Path
import secrets
import stat

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("captcha-solver")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CAPTCHA_SOLVER_", env_file=".env")

    # W2A: Bỏ khoá mặc định 'change-me' để bắt buộc dùng khoá mạnh hoặc tự sinh vào /khoa/khoa-dich-vu.
    api_key: str = ""
    # W2A: Thêm công tắc tính năng (mặc định chỉ bật nhóm dang_nhap).
    tinh_nang_bat: str = "dang_nhap"
    # Where chromium profiles (cookies, localStorage) live across restarts.
    data_dir: Path = Path("/data")
    # W2A: Thư mục lưu trữ khoá bảo mật (/khoa), tách riêng khỏi volume dữ liệu /data.
    key_dir: Path = Path("/khoa")
    # How many concurrent browser contexts to keep ready in the pool.
    pool_size: int = 2
    # Per-solve timeout (seconds) before we bail.
    solve_timeout: int = 90
    # Display number used by Xvfb; the headful VNC flow attaches here.
    display: str = ":99"
    # Where the noVNC websocket is exposed for the manual-login UX.
    novnc_external_url: str = "http://localhost:6080"
    # Browser engine: "chromium" (default) or "firefox" (bypasses Google
    # Safe Browsing "unsafe browser" detection on VPS IPs).
    browser: str = "chromium"
    # W2A: Công tắc bật noVNC (mặc định tắt = False để bảo mật).
    bat_novnc: bool = False



settings = Settings()


# W2A: Nhóm tính năng cho công tắc CAPTCHA_SOLVER_TINH_NANG_BAT (mặc định chỉ bật dang_nhap; lo_bi_mat tắt).
NHOM_TINH_NANG_HOP_LE = frozenset({
    "dang_nhap",
    "giai_captcha",
    "lo_bi_mat",
    "luong_anh",
    "facebook",
    "tu_dang_ky",
    "chay_tuy_y",
    "ai_qua_trinh_duyet",
    "day_token_ngoai",
})


# W2A: Đọc danh sách nhóm tính năng được bật từ môi trường hoặc settings; auto_login dùng trực tiếp không cần import ngược main.
def lay_cac_nhom_tinh_nang_bat() -> set[str]:
    raw = os.environ.get("CAPTCHA_SOLVER_TINH_NANG_BAT")
    if raw is None:
        raw = getattr(settings, "tinh_nang_bat", "dang_nhap")
    cac_nhom = set()
    for item in str(raw or "").split(","):
        n = item.strip().lower()
        if not n:
            continue
        if n in NHOM_TINH_NANG_HOP_LE:
            cac_nhom.add(n)
        else:
            logger.warning("Bỏ qua nhóm tính năng không hợp lệ: %s", n)
    return cac_nhom


# W2A: Hàm lấy khoá dịch vụ nội bộ: biến CAPTCHA_SOLVER_API_KEY hoặc tệp <key_dir>/khoa-dich-vu (0600), tự sinh nếu thiếu.
def lay_khoa_dich_vu(key_dir: Path | None = None, data_dir: Path | None = None) -> str:
    """Nguồn khoá API nội bộ:
    1. Biến CAPTCHA_SOLVER_API_KEY (>= 24 ký tự, >= 10 ký tự khác nhau, không được là change-me).
    2. Tệp <key_dir>/khoa-dich-vu quyền 0600 (mặc định /khoa).
    3. Nếu chưa có, sinh mới bằng secrets.token_urlsafe(32) và ghi file quyền 0600 nguyên tử (tệp tạm rồi os.replace).
    Tuyệt đối không log giá trị khoá.
    """
    env_key = os.environ.get("CAPTCHA_SOLVER_API_KEY", "").strip()
    if env_key:
        if env_key == "change-me":
            raise RuntimeError("CAPTCHA_SOLVER_API_KEY không được dùng giá trị mặc định 'change-me'.")
        if len(env_key) < 24:
            raise RuntimeError("CAPTCHA_SOLVER_API_KEY bắt buộc và phải có độ dài tối thiểu 24 ký tự.")
        if len(set(env_key)) < 10:
            raise RuntimeError("CAPTCHA_SOLVER_API_KEY quá đơn điệu: cần ít nhất 10 ký tự khác nhau.")
        return env_key

    if key_dir is not None:
        thu_muc = key_dir
    elif data_dir is not None:
        thu_muc = data_dir
    else:
        env_kd = os.environ.get("CAPTCHA_SOLVER_KEY_DIR", "").strip()
        thu_muc = Path(env_kd) if env_kd else settings.key_dir

    tep_khoa = thu_muc / "khoa-dich-vu"
    if tep_khoa.is_file():
        st = tep_khoa.stat()
        if stat.S_IMODE(st.st_mode) != 0o600:
            os.chmod(tep_khoa, 0o600)
        k = tep_khoa.read_text(encoding="utf-8").strip()
        # W2A: Tệp rỗng hoặc sai khuôn ném RuntimeError tiếng Việt ngay, không sinh lại đè lên
        if not k:
            raise RuntimeError(f"Tệp khoá dịch vụ '{tep_khoa}' rỗng — dừng dịch vụ để tránh lỗi xác thực.")
        if len(k) < 24 or len(set(k)) < 10:
            raise RuntimeError("Khoá dịch vụ trong tệp không hợp lệ: cần ít nhất 24 ký tự và 10 ký tự khác nhau.")
        return k

    # W2A: Ghi nguyên tử bằng tệp tạm cùng thư mục rồi os.replace (tránh đè tệp hoặc tệp rác khi có nhiều tiến trình)
    k = secrets.token_urlsafe(32)
    thu_muc.mkdir(parents=True, exist_ok=True)
    tep_tmp = thu_muc / f".khoa-dich-vu.tmp.{secrets.token_hex(6)}"
    try:
        fd = os.open(tep_tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with open(fd, "w", encoding="utf-8") as f:
            f.write(k + "\n")
        os.chmod(tep_tmp, 0o600)
        if tep_khoa.is_file():
            tep_tmp.unlink(missing_ok=True)
            return lay_khoa_dich_vu(key_dir=thu_muc)
        os.replace(tep_tmp, tep_khoa)
    except FileExistsError:
        if tep_khoa.is_file():
            return lay_khoa_dich_vu(key_dir=thu_muc)
        raise
    logger.info("Đã sinh khoá dịch vụ mới tại: %s", tep_khoa)
    return k

