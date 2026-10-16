"""Hàm khoá và mã hoá khi nghỉ cho web2api (Việc F5 / Báo cáo bảo mật theo chức năng).

Bảo vệ bí mật (khoá API, token, cookie) và phiên tài khoản khi nghỉ trên đĩa:
- Khoá chính 32 byte lấy theo thứ tự:
  1. Biến môi trường `WEB2API_KHOA_CHINH` (base64 hoặc hex 64 ký tự).
  2. Tệp `WEB2API_KHOA_CHINH_TEP` (quyền 0600, bắt buộc NGOÀI volume dữ liệu /data).
  3. Không có: chế độ KHÔNG mã hoá (ghi cảnh báo một lần).
- Cờ `WEB2API_BAT_BUOC_MA_HOA=1`: bật mà thiếu hoặc sai khoá chính ⇒ lõi KHÔNG khởi động.
- Mã hoá từng tệp bằng AES-256-GCM:
  Header b"W2A1" (4 byte) + phiên bản b"\\x01" (1 byte) + nonce (12 byte) + ciphertext & tag (16 byte).
  AAD = đường dẫn tương đối của tệp so với thư mục dữ liệu (chống tráo tệp).
- Đọc trong suốt: tệp cũ chưa mã hoá vẫn đọc được; lần ghi kế tiếp tự động mã hoá.
- Tay cầm kho riêng `KhoRieng(module_id)`: cô lập bí mật/phiên theo từng module.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger("web2api.ham_khoa")

TIEN_TO_MA_HOA = b"W2A1"
PHIEN_BAN_MA_HOA = b"\x01"
DO_DAI_KHOA = 32
DO_DAI_NONCE = 12
DO_DAI_TAG = 16
DO_DAI_DAU_TEP = len(TIEN_TO_MA_HOA) + len(PHIEN_BAN_MA_HOA) + DO_DAI_NONCE  # 17 byte

_TEN_KHO_RIENG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\Z")
_DA_CANH_BAO_KHONG_KHOA = False
_KHOA_LUONG = threading.Lock()


class KhoHong(Exception):
    """Tệp kho hỏng hoặc giải mã thất bại: KHÔNG tự ghi đè, KHÔNG coi là rỗng."""


def _kiem_tra_khoa_hop_le(k: bytes) -> bytes:
    """Kiểm tra độ dài và độ an toàn cơ bản của khoá chính 32 byte."""
    if len(k) != DO_DAI_KHOA:
        raise ValueError(f"Khoá chính cần đúng {DO_DAI_KHOA} byte")
    # Từ chối khoá toàn byte lặp hoặc entropy quá thấp (ít hơn 8 byte phân biệt)
    if len(set(k)) < 8:
        raise ValueError("Khoá chính không an toàn: entropy quá thấp hoặc toàn byte lặp")
    return k


def _giai_ma_chuoi_khoa(chuoi: str) -> bytes:
    """Giải mã chuỗi khoá chính từ hex hoặc base64, kiểm tra chặt chẽ đúng 32 byte và entropy."""
    s = chuoi.strip()
    # Thử hex (64 ký tự hex = 32 byte)
    if len(s) == 64:
        try:
            k = bytes.fromhex(s)
            return _kiem_tra_khoa_hop_le(k)
        except ValueError as e:
            if "entropy" in str(e):
                raise
            pass

    # Thử base64 chuẩn hoặc urlsafe
    s_pad = s + ("=" * (4 - (len(s) % 4)) if len(s) % 4 else "")
    try:
        k = base64.urlsafe_b64decode(s_pad)
        if len(k) == DO_DAI_KHOA:
            return _kiem_tra_khoa_hop_le(k)
    except Exception as e:
        if "entropy" in str(e):
            raise

    try:
        k = base64.b64decode(s_pad)
        if len(k) == DO_DAI_KHOA:
            return _kiem_tra_khoa_hop_le(k)
    except Exception as e:
        if "entropy" in str(e):
            raise

    raise ValueError(f"Khoá chính không hợp lệ (cần đúng {DO_DAI_KHOA} byte sau khi giải mã hex/base64)")


def bat_buoc_ma_hoa() -> bool:
    """Kiểm tra cờ WEB2API_BAT_BUOC_MA_HOA=1."""
    val = os.environ.get("WEB2API_BAT_BUOC_MA_HOA", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def cho_doc_ro() -> bool:
    """Kiểm tra cờ WEB2API_CHO_DOC_RO=1 (cho phép đọc bản rõ để di trú).
    Bị cấm hẳn nếu WEB2API_BAT_BUOC_MA_HOA=1."""
    if bat_buoc_ma_hoa():
        return False
    val = os.environ.get("WEB2API_CHO_DOC_RO", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def lay_khoa_chinh() -> bytes | None:
    """Lấy khoá chính 32 byte theo thứ tự ưu tiên:
    1. Biến môi trường WEB2API_KHOA_CHINH
    2. Tệp WEB2API_KHOA_CHINH_TEP (quyền 0600, ngoài volume dữ liệu)
    3. Không có: None (chế độ không mã hoá; cảnh báo 1 lần).
    Nếu biến môi trường / tệp đã chỉ định mà sai định dạng hoặc không hợp lệ ⇒ raise RuntimeError.
    Nếu WEB2API_BAT_BUOC_MA_HOA=1 mà không có khoá hợp lệ ⇒ raise RuntimeError.
    """
    global _DA_CANH_BAO_KHONG_KHOA

    # 1. Từ biến môi trường
    tu_bien = os.environ.get("WEB2API_KHOA_CHINH", "").strip()
    if tu_bien:
        try:
            return _giai_ma_chuoi_khoa(tu_bien)
        except Exception as e:
            raise RuntimeError(f"Khoá chính từ WEB2API_KHOA_CHINH không hợp lệ: {e}") from e

    # 2. Từ tệp khoá chính
    tep_str = os.environ.get("WEB2API_KHOA_CHINH_TEP", "").strip()
    if tep_str:
        p = Path(tep_str).resolve()
        from app import kho
        goc_data = kho.thu_muc_du_lieu().resolve()
        try:
            if p.is_relative_to(goc_data):
                msg = (f"Tệp khoá chính {p} không được nằm trong thư mục dữ liệu {goc_data} "
                       "(khoá nằm cạnh dữ liệu thì mã hoá vô nghĩa khi bị chụp ổ đĩa)")
                raise RuntimeError(msg)
        except (RuntimeError,):
            raise
        except Exception:
            pass

        if not p.is_file():
            raise RuntimeError(f"Tệp khoá chính {p} không tồn tại hoặc không phải là tệp")

        # Kiểm tra quyền tệp trên hệ điều hành POSIX
        if os.name == "posix":
            quyen = p.stat().st_mode & 0o777
            if (quyen & 0o077) != 0:
                raise PermissionError(f"Tệp khoá chính {p} có quyền quá rộng (0o{quyen:o}); bắt buộc 0600")

        raw = p.read_bytes()
        if len(raw) == DO_DAI_KHOA:
            try:
                return _kiem_tra_khoa_hop_le(raw)
            except ValueError as e:
                raise RuntimeError(f"Nội dung tệp khoá chính {p} không hợp lệ: {e}") from e
        try:
            txt = raw.decode("utf-8").strip()
            return _giai_ma_chuoi_khoa(txt)
        except Exception as e:
            raise RuntimeError(f"Nội dung tệp khoá chính {p} không hợp lệ: {e}") from e

    # 3. Không có khoá
    if bat_buoc_ma_hoa():
        raise RuntimeError("Bắt buộc mã hoá (WEB2API_BAT_BUOC_MA_HOA=1) nhưng không tìm thấy khoá chính hợp lệ")

    with _KHOA_LUONG:
        if not _DA_CANH_BAO_KHONG_KHOA:
            _DA_CANH_BAO_KHONG_KHOA = True
            try:
                from app import nhat_ky
                nhat_ky.ghi("he_thong", "canh_bao",
                            "Chưa cấu hình khoá chính (WEB2API_KHOA_CHINH) — bí mật và phiên lưu dạng rõ trên đĩa")
            except Exception:
                pass
    return None


def kiem_tra_khoi_dong() -> None:
    """Kiểm tra cấu hình khoá khi khởi động lõi.
    - Ném RuntimeError nếu bắt buộc mà thiếu khoá (WEB2API_BAT_BUOC_MA_HOA=1).
    - Ném RuntimeError nếu khoá đã cấu hình nhưng sai định dạng/quyền/entropy.
    """
    lay_khoa_chinh()


def kiem_tra_khong_symlink(p: Path) -> None:
    """Kiểm tra p và mọi thành phần thư mục cha bên dưới bi_mat/ không được là liên kết tượng trưng."""
    from app import kho
    try:
        goc_bi_mat = kho.thu_muc_du_lieu() / "bi_mat"
    except Exception:
        return
    curr = p
    while True:
        if curr.is_symlink():
            raise ValueError(f"Phát hiện liên kết tượng trưng không an toàn: {curr}")
        if curr == goc_bi_mat or curr.parent == curr:
            break
        curr = curr.parent


def tinh_aad(p: Path, goc: Path | None = None) -> str:
    """Tính dữ liệu xác thực bổ sung (AAD) từ đường dẫn tương đối của tệp so với thư mục dữ liệu."""
    if goc is None:
        from app import kho
        goc = kho.thu_muc_du_lieu()
    try:
        rel = p.relative_to(goc)
        if ".." not in rel.parts:
            # Kiểm tra xem có symlink không (nếu là symlink thì resolve để kiểm tra)
            if not p.is_symlink():
                return rel.as_posix()
    except (ValueError, RuntimeError):
        pass

    p_res = p.resolve()
    goc_res = goc.resolve()
    if not p_res.is_relative_to(goc_res):
        raise ValueError(f"Đường dẫn {p} không nằm dưới thư mục dữ liệu {goc}")
    return p_res.relative_to(goc_res).as_posix()


def ma_hoa(du_lieu_bytes: bytes, aad: str | bytes, khoa: bytes | None = None) -> bytes:
    """Mã hoá AES-256-GCM. Không có khoá ⇒ trả nguyên văn du_lieu_bytes."""
    if khoa is None:
        khoa = lay_khoa_chinh()
    if khoa is None:
        return du_lieu_bytes

    if isinstance(aad, str):
        aad = aad.encode("utf-8")
    nonce = secrets.token_bytes(DO_DAI_NONCE)
    aesgcm = AESGCM(khoa)
    ct = aesgcm.encrypt(nonce, du_lieu_bytes, aad)
    return TIEN_TO_MA_HOA + PHIEN_BAN_MA_HOA + nonce + ct


def giai_ma(noi_dung: bytes, aad: str | bytes, khoa: bytes | None = None) -> bytes:
    """Giải mã AES-256-GCM.
    - Không có tiền tố W2A1:
      + Chưa có khoá chính: trả nguyên văn (chế độ không mã hoá).
      + ĐÃ CÓ khoá chính: ném KhoHong từ chối bản rõ (chống hạ cấp), trừ khi cờ
        WEB2API_CHO_DOC_RO=1 được bật (chỉ dùng cho di trú; bị cấm khi BAT_BUOC_MA_HOA=1).
    - Có tiền tố W2A1: giải mã bằng khoá chính; thiếu khoá hoặc sai thẻ/AAD ⇒ KhoHong.
    """
    if not noi_dung.startswith(TIEN_TO_MA_HOA):
        khoa_hien_co = khoa if khoa is not None else lay_khoa_chinh()
        if khoa_hien_co is not None:
            if not cho_doc_ro():
                raise KhoHong("Tệp không có tiêu đề mã hoá W2A1 (từ chối bản rõ khi đã cấu hình khoá chính)")
        return noi_dung

    if khoa is None:
        khoa = lay_khoa_chinh()
    if khoa is None:
        raise KhoHong("Tệp đã mã hoá nhưng chưa cấu hình khoá chính (WEB2API_KHOA_CHINH)")

    if len(noi_dung) < DO_DAI_DAU_TEP + DO_DAI_TAG:
        raise KhoHong("Dữ liệu tệp mã hoá quá ngắn hoặc bị cắt cụt")

    phien_ban = noi_dung[len(TIEN_TO_MA_HOA):len(TIEN_TO_MA_HOA) + 1]
    if phien_ban != PHIEN_BAN_MA_HOA:
        raise KhoHong(f"Phiên bản gói mã hoá không hỗ trợ: {phien_ban!r}")

    nonce = noi_dung[5:17]
    ct = noi_dung[17:]
    if isinstance(aad, str):
        aad = aad.encode("utf-8")

    try:
        aesgcm = AESGCM(khoa)
        return aesgcm.decrypt(nonce, ct, aad)
    except Exception as e:
        raise KhoHong(f"Giải mã thất bại (sai khoá chính, tráo tệp AAD hoặc dữ liệu hỏng): {e}") from e


def doc_bi_mat_tep(p: Path) -> dict | None:
    """Đọc tệp bí mật hoặc phiên trên đĩa. Giải mã nếu đã mã hoá. Chưa có tệp ⇒ None. Hỏng ⇒ KhoHong."""
    kiem_tra_khong_symlink(p)
    if not p.is_file():
        return None
    try:
        raw = p.read_bytes()
    except OSError as e:
        raise KhoHong(f"Không đọc được tệp {p}: {e}") from e

    aad = tinh_aad(p)
    pt_bytes = giai_ma(raw, aad)
    try:
        du_lieu = json.loads(pt_bytes.decode("utf-8"))
        if not isinstance(du_lieu, dict):
            raise KhoHong(f"Nội dung tệp {p} không phải đối tượng JSON")
        return du_lieu
    except UnicodeDecodeError as e:
        raise KhoHong(f"Nội dung tệp {p} không phải chuỗi UTF-8 hợp lệ: {e}") from e
    except json.JSONDecodeError as e:
        raise KhoHong(f"Tệp {p} hỏng định dạng JSON: {e}") from e


def ghi_bi_mat_tep(p: Path, du_lieu: dict, quyen: int = 0o600) -> None:
    """Ghi nguyên tử tệp bí mật hoặc phiên. Tự động mã hoá khi nghỉ nếu có khoá chính."""
    if not isinstance(du_lieu, dict):
        raise ValueError("dữ liệu bí mật phải là đối tượng dict")

    # Kiểm tra chống symlink dưới bi_mat
    kiem_tra_khong_symlink(p)

    # Trước khi ghi đè, kiểm tra xem tệp cũ có đang mang mã hoá W2A1 không
    if p.is_file():
        try:
            raw_cu = p.read_bytes()
            if raw_cu.startswith(TIEN_TO_MA_HOA):
                if lay_khoa_chinh() is None:
                    raise KhoHong("Không thể ghi đè tệp đã mã hoá khi chưa có khoá chính hợp lệ")
        except OSError:
            pass

    # Tạo thư mục và chmod 0700 toàn chuỗi cha dưới thư mục dữ liệu
    p.parent.mkdir(parents=True, exist_ok=True)
    from app import kho
    goc_data = kho.thu_muc_du_lieu().resolve()
    curr = p.parent.resolve()
    while curr != goc_data and curr.parent != curr:
        os.chmod(curr, 0o700)
        curr = curr.parent

    raw_bytes = json.dumps(du_lieu, ensure_ascii=False, indent=2).encode("utf-8")
    aad = tinh_aad(p)
    noi_dung_ghi = ma_hoa(raw_bytes, aad)

    kho.ghi_nguyen_tu(p, noi_dung_ghi, quyen)


def ma_hoa_lai_tat_ca() -> int:
    """Di trú một lần: đọc toàn bộ tệp trong bi_mat/ (kể cả mo-dun/ và phien/),
    nếu tệp là bản rõ thì đọc và ghi lại với mã hoá khoá chính hiện tại.
    Trả về số tệp đã mã hoá lại."""
    khoa = lay_khoa_chinh()
    if khoa is None:
        raise RuntimeError("Cần cấu hình khoá chính trước khi mã hoá lại")
    from app import kho
    goc = kho.thu_muc_du_lieu() / "bi_mat"
    if not goc.is_dir():
        return 0
    so_tep = 0
    for p in sorted(goc.rglob("*.json")):
        if not p.is_file() or p.is_symlink():
            continue
        try:
            kiem_tra_khong_symlink(p)
        except ValueError:
            continue
        raw = p.read_bytes()
        if not raw.startswith(TIEN_TO_MA_HOA):
            try:
                obj = json.loads(raw.decode("utf-8"))
                if isinstance(obj, dict):
                    ghi_bi_mat_tep(p, obj, 0o600)
                    so_tep += 1
            except Exception as e:
                log.warning("Không thể mã hoá lại tệp %s: %s", p, e)
    return so_tep


class KhoRieng:
    """Tay cầm kho dữ liệu bí mật và phiên dành riêng cho một module (Việc F5 / Báo cáo F5).

    - Bó chặt trong không gian tên `bi_mat/mo-dun/<id>/` và `bi_mat/phien/<id>/`.
    - ID module do LÕI gắn lúc tạo tay cầm, module KHÔNG chọn hoặc đổi được.
    - Chống IDOR và chống đi ngược thư mục qua regex + resolve + is_relative_to.
    """

    def __init__(self, module_id: str):
        from app.kham_pha import ID_HOP_LE
        if not isinstance(module_id, str) or not ID_HOP_LE.match(module_id):
            raise ValueError(f"id module không hợp lệ: {module_id!r}")
        self._module_id = module_id

    @property
    def module_id(self) -> str:
        return self._module_id

    def _duong_tep(self, ten: str) -> Path:
        if not isinstance(ten, str) or not _TEN_KHO_RIENG.match(ten):
            raise ValueError(f"tên tệp bí mật không hợp lệ: {ten!r}")
        from app import kho
        tm_mod = kho.thu_muc_du_lieu() / "bi_mat" / "mo-dun" / self._module_id
        # Kiểm tra symlink trước khi resolve (chống symlink mo-dun/mod-c -> mo-dun/mod-b)
        kiem_tra_khong_symlink(tm_mod / f"{ten}.json")
        goc = tm_mod.resolve()
        p = (goc / f"{ten}.json").resolve()
        if not p.is_relative_to(goc):
            raise ValueError(f"đường dẫn vượt ngoài không gian tên của module: {ten!r}")
        return p

    def doc(self, ten: str) -> dict | None:
        """Đọc tệp bí mật của module. Chưa có ⇒ None; hỏng ⇒ KhoHong."""
        p = self._duong_tep(ten)
        return doc_bi_mat_tep(p)

    def luu(self, ten: str, du_lieu: dict) -> None:
        """Lưu tệp bí mật của module (tự động mã hoá khi nghỉ)."""
        p = self._duong_tep(ten)
        ghi_bi_mat_tep(p, du_lieu, 0o600)

    def xoa(self, ten: str) -> bool:
        """Xoá tệp bí mật của module."""
        p = self._duong_tep(ten)
        try:
            p.unlink()
            return True
        except FileNotFoundError:
            return False

    def liet_ke(self) -> list[str]:
        """Liệt kê tên các tệp bí mật trong không gian tên của module."""
        from app import kho
        goc = kho.thu_muc_du_lieu() / "bi_mat" / "mo-dun" / self._module_id
        if not goc.is_dir():
            return []
        ra = []
        for p in sorted(goc.glob("*.json")):
            if p.is_file():
                ra.append(p.stem)
        return ra

    # ───────────── phiên qua tay cầm (module không thể truyền id khác) ─────────────

    def phien_lay(self, tai_khoan: str) -> dict | None:
        """Lấy phiên tài khoản của CHÍNH module này."""
        from app import phien
        return phien.lay(self._module_id, tai_khoan)

    def phien_luu(self, tai_khoan: str, du_lieu: dict, het_han: str | None = None) -> dict:
        """Lưu phiên tài khoản của CHÍNH module này."""
        from app import phien
        return phien.luu(self._module_id, tai_khoan, du_lieu, het_han)

    def phien_xoa(self, tai_khoan: str) -> bool:
        """Xoá phiên tài khoản của CHÍNH module này."""
        from app import phien
        return phien.xoa(self._module_id, tai_khoan)

    def phien_liet_ke(self) -> list[dict]:
        """Liệt kê các phiên tài khoản của CHÍNH module này."""
        from app import phien
        return phien.liet_ke(self._module_id)
