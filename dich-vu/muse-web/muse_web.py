#!/usr/bin/env python3
"""muse-web: Công cụ dòng lệnh giao việc cho agent Muse (muse.ai).

Giao việc trực tiếp cho agent Muse có máy ảo riêng và connectors (tương tự `agy -p`),
KHÔNG ép thành API chat. Sử dụng giao thức trực tiếp từ `modules/muse2api`.

Lệnh chạy trong môi trường kiểm của kho (sys.path trỏ tới gốc kho).
Mọi lệnh in JSON ra stdout (tiếng Việt có dấu, ensure_ascii=False).

Nguyên tắc thông báo lỗi: KHÔNG BAO GIỜ chép giá trị người gọi đưa vào (đối số, đường dẫn,
tên tài khoản, nội dung stdin) — người gọi có thể gõ nhầm cookie vào bất kỳ chỗ nào. Thông báo
chỉ nêu TÊN tham số và loại lỗi, dựng từ cấu trúc (kiểu ngoại lệ, mã trạng thái).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import getpass
import json
import math
import os
import re
import secrets
import signal
import stat
import sys
import tempfile
import time
from pathlib import Path

# Đảm bảo gốc kho nằm trong sys.path để import modules.muse2api
GOC_KHO = Path(__file__).resolve().parents[2]
if str(GOC_KHO) not in sys.path:
    sys.path.insert(0, str(GOC_KHO))

LOI_IMPORT: str | None = None
try:
    from modules.muse2api import chat, protocol
except ImportError as _err:
    chat = None      # type: ignore
    protocol = None  # type: ignore
    LOI_IMPORT = str(_err)

MAU_TEN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
TRAN_CHO = 1800.0       # giây, trần --cho (vượt thì kẹp)
TRAN_CHO_RANH = 3600.0  # giây, trần --cho-ranh (vượt thì kẹp)
GOI_Y_THEM = "chạy 'cookie them --ten TÊN' để thêm"


class LoiCuaLenh(Exception):
    """Lỗi của lệnh: mã thoát, mã định danh lỗi và các trường phụ gộp vào JSON lỗi."""

    def __init__(self, code: int, ma: str, loi: str, them: dict | None = None):
        super().__init__(loi)
        self.code = code
        self.ma = ma
        self.loi = loi
        self.them = them or {}


class ChuyenTaiKhoanAuthError(Exception):
    """Tín hiệu chuyển sang tài khoản kế tiếp khi gặp AuthError trước khi gửi."""


class DauRaDaDong(Exception):
    """stdout bị đóng (người đọc bỏ đi giữa chừng) — lỗi phía đầu ra, không phải lỗi mạng."""


class BoPhanTichDoiSo(argparse.ArgumentParser):
    """Bộ phân tích tham số: mọi lỗi cú pháp nổi lên thành `argparse.ArgumentError` (exit_on_error=False)
    để `main` dựng thông báo từ TÊN tham số, không từ thông điệp của argparse (thông điệp đó chép nguyên
    giá trị người gọi). Cấu hình áp cho cả lệnh con vì `add_parser` dựng cùng lớp này."""

    def __init__(self, *a, **kw):
        kw.setdefault("exit_on_error", False)
        kw.setdefault("allow_abbrev", False)
        super().__init__(*a, **kw)

    def error(self, message: str):
        # Chốt chặn: KHÔNG dùng `message` — argparse chép giá trị người gọi vào đó.
        raise LoiCuaLenh(2, "yeu_cau_sai", f"Cú pháp không hợp lệ; chạy '{self.prog} -h' để xem cách dùng")


def _in_json(obj) -> None:
    """In một dòng JSON ra stdout; stdout đã đóng thì nổi lên DauRaDaDong."""
    try:
        print(json.dumps(obj, ensure_ascii=False), flush=True)
    except BrokenPipeError as e:
        raise DauRaDaDong() from e


def _bo_stdout() -> None:
    """Trỏ fd stdout sang /dev/null để lần flush lúc thoát không ném lại BrokenPipeError."""
    try:
        fd_null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(fd_null, sys.stdout.fileno())
        os.close(fd_null)
    except OSError:
        pass


def _che(cookie: str) -> str:
    """Chỉ hiện dạng …abcd (4 ký tự cuối của GIÁ TRỊ cookie đầu).

    Không hiện tên cookie lẫn 4 ký tự đầu; giá trị ≤ 8 ký tự → …
    """
    dau = cookie.split(";")[0].strip()
    if "=" in dau:
        _, v = dau.split("=", 1)
        v = v.strip()
        return f"…{v[-4:]}" if len(v) > 8 else "…"
    return f"…{cookie[-4:]}" if len(cookie) > 8 else "…"


def _la_loi_ket_noi(e: Exception) -> bool:
    """True nếu là lỗi kết nối/máy chủ mạng (không phải Auth, Timeout hay 429)."""
    if protocol is not None and isinstance(e, protocol.AuthError):
        return False
    if isinstance(e, TimeoutError):
        return False
    if protocol is not None and isinstance(e, protocol.GatewayError) and getattr(e, "status", None) == 429:
        return False
    return True


def _mo_ta_loi(e: Exception) -> str:
    ma = getattr(e, "status", None)
    return f"{type(e).__name__}" + (f" (status={ma})" if ma is not None else "")


def _bao_dam_thu_muc_rieng(p: Path) -> None:
    """Thư mục phải là thư mục thật (không phải liên kết tượng trưng), thuộc người đang chạy, quyền 0700.

    Kiểm qua fd đã mở bằng O_NOFOLLOW để không bị tráo giữa lúc kiểm và lúc dùng.
    """
    try:
        os.makedirs(p, mode=0o700, exist_ok=True)
        fd = os.open(p, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as e:
        raise LoiCuaLenh(
            2, "yeu_cau_sai",
            f"Thư mục kho {p} không dùng được ({type(e).__name__}); phải là thư mục thật, "
            "không phải liên kết tượng trưng",
        ) from e
    try:
        st = os.fstat(fd)
        if st.st_uid != os.getuid():
            raise LoiCuaLenh(2, "yeu_cau_sai", f"Thư mục kho {p} không thuộc người dùng đang chạy (uid {os.getuid()})")
        if stat.S_IMODE(st.st_mode) != 0o700:
            try:
                os.fchmod(fd, 0o700)
            except OSError as e:
                raise LoiCuaLenh(
                    2, "yeu_cau_sai", f"Không đặt được quyền 0700 cho thư mục kho {p} ({type(e).__name__})"
                ) from e
    finally:
        os.close(fd)


def lay_thu_muc_kho() -> Path:
    """Lấy thư mục kho cookie ($MUSE_WEB_DIR hoặc ~/.config/muse-web), bảo đảm cả nó và khoa/ đều riêng tư.

    Không resolve() đường dẫn: resolve đi theo liên kết tượng trưng và vô hiệu phép kiểm.
    """
    env_dir = os.environ.get("MUSE_WEB_DIR")
    p = Path(os.path.abspath(env_dir)) if env_dir else Path.home() / ".config" / "muse-web"
    _bao_dam_thu_muc_rieng(p)
    _bao_dam_thu_muc_rieng(p / "khoa")
    return p


def doc_kho_cookie(dir_path: Path) -> list[dict]:
    """Đọc danh sách tài khoản từ cookies.json.

    Tệp phải là tệp thường (không phải liên kết tượng trưng), thuộc người đang chạy, quyền không rộng hơn 0600;
    nội dung phải đúng cấu trúc {"tai_khoan": [{"ten": str, "cookies": str}, …]}. Sai → mã 2.
    """
    tep = dir_path / "cookies.json"
    try:
        fd = os.open(tep, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return []
    except OSError as e:
        raise LoiCuaLenh(
            2, "yeu_cau_sai",
            f"Không mở được {tep} ({type(e).__name__}); tệp không được là liên kết tượng trưng",
        ) from e
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise LoiCuaLenh(2, "yeu_cau_sai", f"{tep} không phải tệp thường")
        if st.st_uid != os.getuid():
            raise LoiCuaLenh(2, "yeu_cau_sai", f"Tệp {tep} không thuộc người dùng đang chạy (uid {os.getuid()})")
        if st.st_mode & 0o077 != 0:
            quyen_hien_tai = oct(stat.S_IMODE(st.st_mode))[-3:]
            raise LoiCuaLenh(
                2,
                "yeu_cau_sai",
                f"Tệp {tep} có quyền {quyen_hien_tai} rộng hơn 0600; hãy chạy 'chmod 600 {tep}'",
            )
        raw = fh.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as e:  # gồm UnicodeDecodeError và JSONDecodeError
        raise LoiCuaLenh(2, "yeu_cau_sai", f"Tệp cookies.json lỗi định dạng: {type(e).__name__}") from e
    ds = data.get("tai_khoan", []) if isinstance(data, dict) else None
    if not isinstance(ds, list) or not all(
        isinstance(i, dict) and isinstance(i.get("ten"), str) and isinstance(i.get("cookies"), str) for i in ds
    ):
        raise LoiCuaLenh(
            2, "yeu_cau_sai",
            'Tệp cookies.json sai cấu trúc: cần {"tai_khoan": [{"ten": chuỗi, "cookies": chuỗi}, …]}',
        )
    return ds


def ghi_kho_cookie(dir_path: Path, tai_khoan: list[dict]) -> None:
    """Ghi nguyên tử danh sách tài khoản vào cookies.json quyền 0600."""
    tep = dir_path / "cookies.json"
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".tmp_cookies_", dir=dir_path)
    try:
        os.chmod(tmp_path, 0o600)
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump({"tai_khoan": tai_khoan}, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, tep)
    except BaseException:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


@contextlib.contextmanager
def _khoa_kho(dir_path: Path):
    """Giữ flock độc quyền suốt chu trình đọc-sửa-ghi cookies.json.

    Khoá trên tệp riêng `.kho.lock`, không khoá chính cookies.json vì os.replace thay inode của nó.
    """
    fd = os.open(dir_path / ".kho.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # đóng fd là nhả flock


def kiem_tra_ten(ten: str, tham_so: str = "--ten") -> None:
    """Kiểm tra tên tài khoản khớp ^[a-z0-9][a-z0-9_-]{0,31}$. Thông báo không chép giá trị."""
    if not MAU_TEN.match(ten):
        raise LoiCuaLenh(
            2,
            "yeu_cau_sai",
            f"{tham_so} không hợp lệ: chỉ gồm chữ thường a-z, số, '-' hoặc '_', bắt đầu bằng chữ/số, "
            "tối đa 32 ký tự (giá trị không được in lại)",
        )


def kiem_tra_cookie_raw(raw: str) -> str:
    """Kiểm tra chuỗi cookie hợp lệ: không rỗng, không xuống dòng giữa chừng, không ký tự điều khiển."""
    s = raw.rstrip("\r\n")
    if not s or not s.strip():
        raise LoiCuaLenh(2, "yeu_cau_sai", "Cookie rỗng")
    if "\n" in s or "\r" in s:
        raise LoiCuaLenh(2, "yeu_cau_sai", "Cookie có ký tự xuống dòng giữa chừng")
    if any(ord(c) < 32 or ord(c) == 127 for c in s):
        raise LoiCuaLenh(2, "yeu_cau_sai", "Cookie chứa ký tự điều khiển")
    return s.strip()


def _doc_dau_vao(tep: str | None, ten_co: str, an_khi_tty: bool = False) -> str:
    """Đọc nội dung từ stdin (tep None hoặc '-') hoặc từ tệp; giải mã UTF-8 nghiêm ngặt.

    an_khi_tty: stdin là bàn phím thì nhập bằng getpass (không hiện chữ, không vào lịch sử shell).
    Lỗi → mã 2; thông báo nêu tên tham số, không chép đường dẫn hay nội dung.
    """
    if tep is None or tep == "-":
        if an_khi_tty and sys.stdin.isatty():
            return getpass.getpass("Dán cookie (không hiện ra màn hình): ")
        nguon = "stdin"
        du_lieu = sys.stdin.buffer.read()
    else:
        nguon = ten_co
        if not Path(tep).is_file():
            raise LoiCuaLenh(2, "yeu_cau_sai", f"{ten_co}: tệp không tồn tại hoặc không phải tệp thường")
        try:
            du_lieu = Path(tep).read_bytes()
        except OSError as e:
            raise LoiCuaLenh(2, "yeu_cau_sai", f"{ten_co}: không đọc được tệp ({type(e).__name__})") from e
    try:
        return du_lieu.decode("utf-8")
    except UnicodeDecodeError as e:
        raise LoiCuaLenh(2, "yeu_cau_sai", f"{nguon}: nội dung không phải UTF-8 hợp lệ") from e


def _kiem_giay(gia_tri: float, ten_co: str, tran: float, cho_phep_0: bool) -> float:
    """Số giây phải hữu hạn, không âm (và > 0 nếu không cho phép 0); vượt trần thì kẹp về trần."""
    if not math.isfinite(gia_tri) or gia_tri < 0 or (gia_tri == 0 and not cho_phep_0):
        dieu_kien = "≥ 0" if cho_phep_0 else "> 0"
        raise LoiCuaLenh(2, "yeu_cau_sai", f"{ten_co} phải là số giây hữu hạn {dieu_kien} (trần {tran:g})")
    return min(gia_tri, tran)


def _ung_vien(dir_path: Path, ten_tk: str | None) -> list[dict]:
    """Danh sách tài khoản sẽ thử: cả kho, hoặc đúng tài khoản nêu ở --tai-khoan."""
    if ten_tk:
        kiem_tra_ten(ten_tk, "--tai-khoan")
    ds = doc_kho_cookie(dir_path)
    if not ds:
        raise LoiCuaLenh(2, "yeu_cau_sai", f"Chưa có tài khoản nào trong kho; {GOI_Y_THEM}")
    if ten_tk:
        ds = [item for item in ds if item["ten"] == ten_tk]
        if not ds:
            raise LoiCuaLenh(2, "yeu_cau_sai", "Không có tài khoản nào khớp --tai-khoan; xem 'cookie ds'")
    return ds


class KhoaTaiKhoan:
    """Khóa tệp điều phối lượt chạy bằng fcntl.flock."""

    def __init__(self, khoa_dir: Path, ten: str):
        self.file_path = khoa_dir / f"{ten}.lock"
        self.ten = ten
        self.fd: int | None = None

    def khoa(self, cho_ranh: float = 0.0) -> bool:
        fd = os.open(self.file_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.chmod(self.file_path, 0o600)
        except OSError:
            pass
        deadline = time.time() + max(0.0, cho_ranh)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.fd = fd
                return True
            except (BlockingIOError, OSError):
                if time.time() >= deadline:
                    os.close(fd)
                    return False
                time.sleep(0.1)

    def mo_khoa(self) -> None:
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


def _lay_khoa_dau_tien(con_lai: list[dict], khoa_dir: Path, cho_ranh: float):
    """Khoá tài khoản rảnh đầu tiên trong con_lai. Trả (tài khoản, khoá) hoặc (None, None)."""
    for u in con_lai:
        kh = KhoaTaiKhoan(khoa_dir, u["ten"])
        if kh.khoa(cho_ranh):
            return u, kh
    return None, None


# ── Xử lý các lệnh ────────────────────────────────────────────────────────


def xu_ly_cookie_them(args, dir_path: Path) -> int:
    kiem_tra_ten(args.ten)
    val = kiem_tra_cookie_raw(_doc_dau_vao(args.tep, "--tep", an_khi_tty=True))
    with _khoa_kho(dir_path):
        ds = doc_kho_cookie(dir_path)
        for item in ds:
            if item["ten"] == args.ten:
                item["cookies"] = val
                break
        else:
            ds.append({"ten": args.ten, "cookies": val})
        ghi_kho_cookie(dir_path, ds)
    _in_json({"ok": True, "ten": args.ten, "khoa": _che(val)})
    return 0


def xu_ly_cookie_ds(args, dir_path: Path) -> int:
    ds = doc_kho_cookie(dir_path)
    _in_json([{"ten": item["ten"], "khoa": _che(item["cookies"])} for item in ds])
    return 0


def xu_ly_cookie_xoa(args, dir_path: Path) -> int:
    kiem_tra_ten(args.ten)
    with _khoa_kho(dir_path):
        ds = doc_kho_cookie(dir_path)
        moi = [item for item in ds if item["ten"] != args.ten]
        if len(moi) == len(ds):
            raise LoiCuaLenh(2, "yeu_cau_sai", "Không có tài khoản nào khớp --ten; xem 'cookie ds'")
        ghi_kho_cookie(dir_path, moi)
    _in_json({"ok": True, "ten": args.ten})
    return 0


def xu_ly_kiem(args, dir_path: Path) -> int:
    if protocol is None:
        raise LoiCuaLenh(5, "loi_ket_noi", f"Thiếu gói phụ thuộc: {LOI_IMPORT}")

    ds = _ung_vien(dir_path, args.tai_khoan)
    ket_qua = []
    tat_ca_auth_error = True
    for item in ds:
        ok = False
        loi = None
        try:
            protocol.fetch_access_token(item["cookies"])
            protocol.fetch_session_info(item["cookies"])
            ok = True
            tat_ca_auth_error = False
        except protocol.AuthError:
            loi = "AuthError (status=401/403)"
        except Exception as e:
            tat_ca_auth_error = False
            loi = _mo_ta_loi(e)
        ket_qua.append({"ten": item["ten"], "ok": ok, "loi": loi})

    _in_json(ket_qua)
    if any(k["ok"] for k in ket_qua):
        return 0
    if tat_ca_auth_error:
        return 4
    return 5


def _loi_sau_khi_chay(e: Exception, da_gui: bool, mot_phan: str) -> LoiCuaLenh:
    """Dựng lỗi cho một lượt gui hỏng, luôn nói rõ việc đã gửi chưa (và phần chữ đã nhận nếu có)."""
    them: dict = {"viec_da_gui": da_gui}
    if mot_phan:
        them["mot_phan"] = mot_phan
    goi_y = (" — việc ĐÃ gửi: đừng gửi lại, dùng 'lich-su' để lấy kết quả" if da_gui
             else " — việc chưa được gửi")
    if isinstance(e, protocol.AuthError):
        return LoiCuaLenh(4, "khoa_hong", f"Cookie bị từ chối: {type(e).__name__}{goi_y}", them)
    if isinstance(e, TimeoutError):
        return LoiCuaLenh(3, "het_gio", f"Hết giờ chờ trả lời: {type(e).__name__}{goi_y}", them)
    return LoiCuaLenh(5, "loi_ket_noi", f"Lỗi kết nối hoặc máy chủ: {_mo_ta_loi(e)}{goi_y}", them)


def _chay_gui_tren_tai_khoan(
    acc: dict,
    viec: str,
    wait_s: float,
    dong: bool,
    co_huy,
    gw_holder: list,
) -> None:
    """Thực thi gửi việc trên một tài khoản đã khóa flock.

    Chỉ nối lại (một lần) khi kết nối chết TRƯỚC khi chat.stream được mở; đã gửi thì không bao giờ gửi lại.
    """
    node_id = secrets.token_hex(8)
    for lan in range(2):
        gw = None
        sess = None
        chunks: list[str] = []
        try:
            gw = protocol.Gateway(acc["cookies"])
            gw_holder[0] = gw
            sess = chat.ChatSession(gw, node_id=node_id, wait_s=wait_s)
            t0 = time.time()
            for delta in sess.run(viec, co_huy=co_huy):
                if co_huy.is_set():
                    return
                if dong:
                    _in_json({"delta": delta})
                else:
                    chunks.append(delta)
            t_giay = round(time.time() - t0, 1)
            if dong:
                _in_json({"ok": True, "xong": True, "tai_khoan": acc["ten"], "giay": t_giay})
            else:
                _in_json({"ok": True, "van_ban": "".join(chunks), "tai_khoan": acc["ten"], "giay": t_giay})
            return
        except DauRaDaDong:
            raise
        except Exception as e:
            da_gui = sess is not None and bool(getattr(sess, "da_gui", False))
            if isinstance(e, protocol.AuthError) and not da_gui:
                raise ChuyenTaiKhoanAuthError() from e
            if lan == 0 and not da_gui and _la_loi_ket_noi(e):
                continue  # gateway cũ được đóng ở finally
            raise _loi_sau_khi_chay(e, da_gui, "".join(chunks)) from e
        finally:
            if gw is not None:
                try:
                    gw.close()
                except Exception:
                    pass
            gw_holder[0] = None


def xu_ly_gui(args, dir_path: Path, co_huy, gw_holder: list, khoa_holder: list) -> int:
    if protocol is None or chat is None:
        raise LoiCuaLenh(5, "loi_ket_noi", f"Thiếu gói phụ thuộc: {LOI_IMPORT}", {"viec_da_gui": False})

    cho = _kiem_giay(args.cho, "--cho", TRAN_CHO, cho_phep_0=False)
    cho_ranh = _kiem_giay(args.cho_ranh, "--cho-ranh", TRAN_CHO_RANH, cho_phep_0=True)

    # Lấy nội dung VIỆC
    if args.tep:
        if args.viec:
            raise LoiCuaLenh(2, "yeu_cau_sai", "Không được cung cấp cả --tep và nội dung việc cùng lúc")
        viec = _doc_dau_vao(args.tep, "--tep")
    elif args.viec == "-":
        viec = _doc_dau_vao(None, "VIỆC")
    elif args.viec:
        viec = args.viec
    else:
        raise LoiCuaLenh(2, "yeu_cau_sai", "Chưa cung cấp nội dung việc cần giao")

    if not viec.strip():
        raise LoiCuaLenh(2, "yeu_cau_sai", "Nội dung việc không được rỗng")

    con_lai = list(_ung_vien(dir_path, args.tai_khoan))
    khoa_dir = dir_path / "khoa"

    while con_lai:
        acc_chon, kh_chon = _lay_khoa_dau_tien(con_lai, khoa_dir, cho_ranh)
        if acc_chon is None:
            raise LoiCuaLenh(6, "khong_co_tai_khoan_ranh", "Không có tài khoản rảnh")

        con_lai.remove(acc_chon)
        khoa_holder[0] = kh_chon

        try:
            _chay_gui_tren_tai_khoan(
                acc=acc_chon,
                viec=viec,
                wait_s=cho,
                dong=args.dong,
                co_huy=co_huy,
                gw_holder=gw_holder,
            )
            return 0
        except ChuyenTaiKhoanAuthError:
            continue
        finally:
            khoa_holder[0] = None
            kh_chon.mo_khoa()

    raise LoiCuaLenh(4, "khoa_hong", "Mọi tài khoản cookie đều bị từ chối hoặc hết hạn — việc chưa được gửi",
                     {"viec_da_gui": False})


def xu_ly_lich_su(args, dir_path: Path, gw_holder: list, khoa_holder: list) -> int:
    if protocol is None or chat is None:
        raise LoiCuaLenh(5, "loi_ket_noi", f"Thiếu gói phụ thuộc: {LOI_IMPORT}")

    gioi_han = args.gioi_han
    if gioi_han < 1 or gioi_han > 100:
        raise LoiCuaLenh(2, "yeu_cau_sai", "--gioi-han phải từ 1 đến 100")
    cho_ranh = _kiem_giay(args.cho_ranh, "--cho-ranh", TRAN_CHO_RANH, cho_phep_0=True)

    con_lai = list(_ung_vien(dir_path, args.tai_khoan))
    khoa_dir = dir_path / "khoa"

    while con_lai:
        acc_chon, kh_chon = _lay_khoa_dau_tien(con_lai, khoa_dir, cho_ranh)
        if acc_chon is None:
            raise LoiCuaLenh(6, "khong_co_tai_khoan_ranh", "Không có tài khoản rảnh")

        con_lai.remove(acc_chon)
        khoa_holder[0] = kh_chon
        gw = None
        try:
            gw = protocol.Gateway(acc_chon["cookies"])
            gw_holder[0] = gw
            d = gw.call_json("chat.history", body={"limit": gioi_han}, timeout=30)
            evs = d.get("chat_events", []) if isinstance(d, dict) else []
            ket_qua = []
            for ev in evs:
                ename = ev.get("event_name", "")
                if ename == "message.assistant":
                    vai = "assistant"
                elif ename == "message.user":
                    vai = "user"
                else:
                    continue
                text = chat.event_text(ev)
                if not text:
                    continue
                ket_qua.append({
                    "seq": ev.get("seq", 0),
                    "vai": vai,
                    "van_ban": text,
                })
            ket_qua.sort(key=lambda x: x["seq"])
            _in_json(ket_qua)
            return 0
        except protocol.AuthError:
            continue  # chỉ đọc nên chuyển sang tài khoản kế là an toàn
        except TimeoutError as e:
            raise LoiCuaLenh(3, "het_gio", f"Hết giờ chờ phản hồi: {type(e).__name__}") from e
        except DauRaDaDong:
            raise
        except Exception as e:
            raise LoiCuaLenh(5, "loi_ket_noi", f"Lỗi kết nối hoặc máy chủ: {_mo_ta_loi(e)}") from e
        finally:
            if gw is not None:
                try:
                    gw.close()
                except Exception:
                    pass
            gw_holder[0] = None
            khoa_holder[0] = None
            kh_chon.mo_khoa()

    raise LoiCuaLenh(4, "khoa_hong", "Mọi tài khoản cookie đều bị từ chối hoặc hết hạn")


# ── Bộ phân tích tham số & Hàm main ──────────────────────────────────────


def tao_parser() -> BoPhanTichDoiSo:
    parser = BoPhanTichDoiSo(
        prog="muse-web",
        description="Giao việc cho agent Muse (muse.ai) qua dòng lệnh.",
    )
    subparsers = parser.add_subparsers(dest="lenh", required=True)

    # 1. cookie
    p_cookie = subparsers.add_parser("cookie", help="Quản lý kho cookie")
    sub_cookie = p_cookie.add_subparsers(dest="lenh_con", required=True)

    # cookie them
    p_them = sub_cookie.add_parser("them", help="Thêm hoặc cập nhật cookie")
    p_them.add_argument("--ten", required=True, help="Tên tài khoản (^[a-z0-9][a-z0-9_-]{0,31}$)")
    p_them.add_argument("--tep", help="Đường dẫn tệp chứa cookie (hoặc '-' đọc stdin)")

    # cookie ds
    sub_cookie.add_parser("ds", help="Liệt kê danh sách cookie (che …abcd)")

    # cookie xoa
    p_xoa = sub_cookie.add_parser("xoa", help="Xóa cookie")
    p_xoa.add_argument("--ten", required=True, help="Tên tài khoản cần xóa")

    # 2. kiem
    p_kiem = subparsers.add_parser("kiem", help="Kiểm tra trạng thái xác thực cookie")
    p_kiem.add_argument("--tai-khoan", help="Kiểm tra riêng một tài khoản")

    # 3. gui
    p_gui = subparsers.add_parser("gui", help="Gửi việc cho agent Muse")
    p_gui.add_argument("viec", nargs="?", default=None, help="Nội dung việc cần giao (hoặc '-')")
    p_gui.add_argument("--tep", help="Đường dẫn tệp chứa nội dung việc (hoặc '-')")
    p_gui.add_argument("--tai-khoan", help="Tên tài khoản chỉ định")
    p_gui.add_argument("--cho", type=float, default=300.0,
                       help="Thời gian chờ trả lời tối đa (giây > 0, mặc định 300, vượt 1800 thì kẹp về 1800)")
    p_gui.add_argument("--cho-ranh", type=float, default=0.0,
                       help="Thời gian chờ tài khoản rảnh (giây ≥ 0, mặc định 0, vượt 3600 thì kẹp về 3600)")
    p_gui.add_argument("--dong", action="store_true", help="In từng dòng JSON khi có chữ mới")

    # 4. lich-su
    p_ls = subparsers.add_parser("lich-su", help="Xem lịch sử cuộc trò chuyện")
    p_ls.add_argument("--gioi-han", type=int, default=15, help="Số sự kiện tối đa (1-100, mặc định 15)")
    p_ls.add_argument("--tai-khoan", help="Tên tài khoản chỉ định")
    p_ls.add_argument("--cho-ranh", type=float, default=0.0,
                      help="Thời gian chờ tài khoản rảnh (giây ≥ 0, mặc định 0, vượt 3600 thì kẹp về 3600)")

    return parser


def _phan_tich(parser: BoPhanTichDoiSo, argv: list[str]) -> argparse.Namespace:
    """Phân tích đối số; lỗi chỉ nêu TÊN tham số hoặc SỐ đối số lạ, không chép giá trị."""
    try:
        args, thua = parser.parse_known_args(argv)
    except argparse.ArgumentError as e:
        if e.argument_name:
            loi = f"Đối số {e.argument_name} thiếu giá trị hoặc giá trị không hợp lệ (giá trị không được in lại)"
        else:
            loi = "Thiếu tham số bắt buộc hoặc cú pháp sai; chạy 'muse-web <lệnh> -h' để xem cách dùng"
        raise LoiCuaLenh(2, "yeu_cau_sai", loi) from None
    if thua:
        raise LoiCuaLenh(
            2, "yeu_cau_sai",
            f"Có {len(thua)} đối số lạ không nhận diện được (giá trị không được in lại); "
            "chạy 'muse-web <lệnh> -h' để xem cách dùng",
        )
    return args


def main(argv: list[str] | None = None) -> int:
    """Điểm vào chính của công cụ muse-web. Trả về mã thoát int."""
    if argv is None:
        argv = sys.argv[1:]

    parser = tao_parser()

    # Bộ giữ tài nguyên để dọn dẹp khi nhận tín hiệu
    gw_holder: list = [None]
    khoa_holder: list[KhoaTaiKhoan | None] = [None]
    import threading
    co_huy = threading.Event()

    def _xu_ly_tin_hieu(signum, frame):
        co_huy.set()
        gw = gw_holder[0]
        if gw is not None:
            try:
                gw.close()
            except Exception:
                pass
        kh = khoa_holder[0]
        if kh is not None:
            kh.mo_khoa()
        ma_thoat = 130 if signum == signal.SIGINT else 143
        sys.exit(ma_thoat)

    old_sigint = signal.signal(signal.SIGINT, _xu_ly_tin_hieu)
    old_sigterm = signal.signal(signal.SIGTERM, _xu_ly_tin_hieu)

    try:
        try:
            args = _phan_tich(parser, argv)
            dir_path = lay_thu_muc_kho()
            if args.lenh == "cookie":
                if args.lenh_con == "them":
                    return xu_ly_cookie_them(args, dir_path)
                if args.lenh_con == "ds":
                    return xu_ly_cookie_ds(args, dir_path)
                return xu_ly_cookie_xoa(args, dir_path)
            if args.lenh == "kiem":
                return xu_ly_kiem(args, dir_path)
            if args.lenh == "gui":
                return xu_ly_gui(args, dir_path, co_huy=co_huy, gw_holder=gw_holder, khoa_holder=khoa_holder)
            return xu_ly_lich_su(args, dir_path, gw_holder=gw_holder, khoa_holder=khoa_holder)
        except LoiCuaLenh as e:
            _in_json({"ok": False, "ma": e.ma, "loi": e.loi, **e.them})
            return e.code
        except KeyboardInterrupt:
            co_huy.set()
            gw = gw_holder[0]
            if gw is not None:
                try:
                    gw.close()
                except Exception:
                    pass
            kh = khoa_holder[0]
            if kh is not None:
                kh.mo_khoa()
            return 130
        except DauRaDaDong:
            raise
        except Exception as e:
            _in_json({"ok": False, "ma": "loi_ket_noi", "loi": _mo_ta_loi(e)})
            return 5
    except DauRaDaDong:
        # Người đọc đã đóng stdout: thoát êm, mã 141 (128 + SIGPIPE) như công cụ Unix thường lệ
        _bo_stdout()
        return 141
    finally:
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGTERM, old_sigterm)


if __name__ == "__main__":
    sys.exit(main())
