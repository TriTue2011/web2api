"""Quản lý khoá khách có phạm vi và kiểm soát đồng thời (Việc E).

Thiết kế:
- Tệp `<DATA>/khoa_khach.json` (KHÔNG chứa khoá):
    {
      "khoa": [
        {
          "id": "kk_...",
          "nhan": "...",
          "bat": true,
          "pham_vi": ["*"] | [mã khả năng],
          "tao_luc": "ISO",
          "dung_cuoi": "ISO" | null,
          "so_luot": 0,
          "bam": SHA-256 hex
        }
      ]
    }
- Khoá sinh dạng secrets.token_urlsafe(32) tiền tố "w2a_", hiện ĐÚNG MỘT LẦN khi tạo.
- Băm SHA-256, so bằng hmac.compare_digest; ghi nguyên tử quyền 0600.
- Đường nóng (/v1/*): đếm lượt và dung_cuoi ghi vào bộ nhớ, đồng bộ ra đĩa theo nhịp
  hoặc khi tắt tiến trình — không ghi đĩa mỗi lượt.
- Trần đồng thời: WEB2API_KHACH_DONG_THOI → 429 + Retry-After (không tính là lỗi provider).
- Sổ thao tác: ghi kênh `quan_tri` cho tạo/sửa/thu hồi và từ chối 401/403, KHÔNG ghi khoá.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app import kho, nhat_ky
from app.kham_pha import MA_KHA_NANG
from app.loi_openai import LoiOpenAI

log = logging.getLogger("web2api.khoa_khach")

#: Số giây mặc định khoá cũ còn dùng được khi xoay khoá (7 ngày).
CU_GIAY_MAC_DINH = 7 * 86400
#: Trần số giây khoá cũ còn dùng được (30 ngày). 0 = hết hạn ngay.
CU_GIAY_TOI_DA = 30 * 86400

#: Trần số yêu cầu đồng thời mặc định cho mỗi khoá khách.
MAC_DINH_KHACH_DONG_THOI = 4
#: Trần số yêu cầu đồng thời mặc định toàn cục cho /v1.
MAC_DINH_V1_DONG_THOI_TOAN_CUC = 32

_KHOA_LUONG = threading.RLock()
_KHOA_KHACH_CACHE: list[dict[str, Any]] = []
_CACHE_FILE_STAT: tuple[Any, ...] | None = None
_CO_THAY_DOI = False
_DONG_THOI_DANG_CHAY: dict[str, int] = {}
_V1_DONG_THOI_HIEN_TAI = 0
_KHOA_VAN_TAY_CACHE: tuple[Path, bytes] | None = None


def _lay_khoa_noi_bo() -> bytes | None:
    """Lấy hoặc sinh khoá nội bộ 32 byte để tính HMAC vân tay. Lưu ở <DATA>/bi_mat/khoa_van_tay (quyền 0600).
    Nạp lười, giữ trong bộ nhớ. Lỗi đọc/ghi thì trả None (vân tay trả '?', không chặn xác thực)."""
    global _KHOA_VAN_TAY_CACHE
    p = kho.thu_muc_du_lieu() / "bi_mat" / "khoa_van_tay"
    if _KHOA_VAN_TAY_CACHE is not None and _KHOA_VAN_TAY_CACHE[0] == p:
        return _KHOA_VAN_TAY_CACHE[1]
    try:
        if p.is_file():
            data = p.read_bytes()
            if len(data) == 32:
                _KHOA_VAN_TAY_CACHE = (p, data)
                return data
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(p.parent, 0o700)
        except OSError:
            pass
        moi = secrets.token_bytes(32)
        fd, tam = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tam")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(moi)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tam, p)
        except BaseException:
            try:
                os.unlink(tam)
            except FileNotFoundError:
                pass
            raise
        _KHOA_VAN_TAY_CACHE = (p, moi)
        return moi
    except Exception as e:
        log.warning("Không thể đọc/ghi khoá nội bộ vân tay (%s): %s", p, e)
        return None


def van_tay(bam: str) -> str:
    """HMAC-SHA256(khoa_noi_bo, bam) lấy 16 ký tự hex đầu. Không ném ngoại lệ (lỗi trả '?')."""
    if not isinstance(bam, str) or not bam:
        return "?"
    knb = _lay_khoa_noi_bo()
    if knb is None:
        return "?"
    try:
        return hmac.new(knb, bam.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    except Exception:
        return "?"


def _la_iso_hop_le(s: Any) -> bool:
    """Kiểm tra một chuỗi có phải ISO-8601 hợp lệ."""
    if not isinstance(s, str) or not s:
        return False
    try:
        dt = datetime.fromisoformat(s)
        return dt is not None
    except (ValueError, TypeError):
        return False


def _la_tuong_lai(iso_str: Any) -> bool:
    """Kiểm tra thời điểm ISO còn ở tương lai so với hiện tại (UTC). Thiếu/hỏng/quá khứ → False."""
    if not isinstance(iso_str, str) or not iso_str:
        return False
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt > datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


def _don_khoa_cu_het_han_trong_cache() -> None:
    """Dọn bam_cu và cu_het_han đã hết hạn khỏi cache theo cách lười trước khi ghi đĩa."""
    for k in _KHOA_KHACH_CACHE:
        if k.get("bam_cu") and not _la_tuong_lai(k.get("cu_het_han")):
            k.pop("bam_cu", None)
            k.pop("cu_het_han", None)



class LoiKhoKhoaKhach(LoiOpenAI):
    """khoa_khach.json tồn tại nhưng không đọc/phân tích được. 503, không lộ chi tiết tệp ra phản hồi.

    ĐÓNG KHI LỖI (cùng nguyên tắc với `nguoi_dung.LoiKhoNguoiDung`): không bao giờ coi tệp hỏng là «chưa có khoá» —
    lần ghi kế tiếp sẽ GHI ĐÈ mất mọi khoá cũ. Mọi đường ghi đọc qua `_doc_tu_dia_neu_can` nên dừng ở đây; đường xác
    thực (`tim_theo_bam`) bắt lỗi này và trả None (không cấp quyền, không 500)."""

    def __init__(self) -> None:
        super().__init__(
            503,
            "Kho khoá khách không đọc được. Khôi phục tệp khoa_khach.json từ bản sao lưu (Hệ thống → Sao lưu) rồi thử "
            "lại; các thao tác ghi khoá bị khoá để không ghi đè khoá hiện có.",
            loai="server_error",
            code="guest_key_store_unreadable",
        )


def _p_khoa_khach() -> Path:
    return kho.thu_muc_du_lieu() / "khoa_khach.json"


#: Trần số phần tử một danh sách phạm vi (chặn thân yêu cầu nhồi danh sách khổng lồ vào tệp).
TOI_DA_PHAN_TU_PHAM_VI = 64

_NGUON_SO_FN: Any = None


def dat_nguon_so(fn: Any) -> None:
    """Điểm tiêm do lõi đăng ký để cung cấp sổ module (SoModule) mà không import ngược app.main."""
    global _NGUON_SO_FN
    _NGUON_SO_FN = fn


def module_ton_tai(mod_id: str, so: Any = None) -> bool:
    """Kiểm tra module có tồn tại trên máy (trong catalog, tu_xa, trên đĩa)."""
    if not isinstance(mod_id, str) or not mod_id:
        return False
    so_hien_tai = so
    if so_hien_tai is None and _NGUON_SO_FN is not None:
        try:
            so_hien_tai = _NGUON_SO_FN()
        except Exception:
            pass
    if so_hien_tai is not None:
        if mod_id in getattr(so_hien_tai, "catalog", {}) or mod_id in getattr(so_hien_tai, "tu_xa", {}):
            return True
    try:
        from app.so_module import THU_MUC_ANH
        if (THU_MUC_ANH / mod_id / "manifest.json").is_file():
            return True
    except Exception:
        pass
    try:
        p_du_lieu = kho.thu_muc_du_lieu() / "modules" / mod_id / "manifest.json"
        if p_du_lieu.is_file():
            return True
    except Exception:
        pass
    try:
        goc = Path(__file__).resolve().parent.parent / "modules" / mod_id / "manifest.json"
        if goc.is_file():
            return True
    except Exception:
        pass
    try:
        p_tx = kho.thu_muc_du_lieu() / "tu_xa.json"
        if p_tx.is_file():
            ds_tx = kho.doc_json(p_tx)
            if isinstance(ds_tx, list) and any(isinstance(m, dict) and m.get("id") == mod_id for m in ds_tx):
                return True
    except Exception:
        pass
    return False


def la_ma_pham_vi(x: Any) -> bool:
    """Một phần tử phạm vi hợp lệ: `*`, mã khả năng mẫu MA_KHA_NANG (có thể kèm hậu tố `:<mã>`),
    hoặc phạm vi giới hạn theo module «<kha_nang>@<module>» (vd `chat@module-a`).
    Mã MỞ: kiểm MẪU, không giữ danh sách mã."""
    if not isinstance(x, str) or not x:
        return False
    if x == "*":
        return True
    if "@" in x:
        if x.count("@") != 1:
            return False
        phan_kn, _, phan_mod = x.partition("@")
        if not phan_kn or not phan_mod:
            return False
        from app.kham_pha import ID_DANH_RIENG, ID_HOP_LE
        if not ID_HOP_LE.match(phan_mod) or phan_mod in ID_DANH_RIENG:
            return False
        if phan_kn == "*":
            return True
        phan = phan_kn.split(":")
        return len(phan) <= 2 and all(bool(MA_KHA_NANG.match(m)) for m in phan)
    phan = x.split(":")
    return len(phan) <= 2 and all(bool(MA_KHA_NANG.match(m)) for m in phan)


def chuan_hoa_pham_vi(
    gia_tri: Any,
    *,
    cho_phep_rong: bool,
    ten_truong: str = "pham_vi",
    kiem_tra_module: bool = False,
    so: Any = None,
) -> list[str]:
    """Bộ kiểm DUY NHẤT cho danh sách phạm vi do khách gửi (khoá khách và trần của người dùng).

    Phải là list các chuỗi hợp lệ (`la_ma_pham_vi`), không phần tử rỗng/khoảng trắng/sai kiểu, bỏ trùng giữ thứ tự.
    KHÔNG BAO GIỜ đổi danh sách rỗng thành «*»: rỗng nghĩa là KHÔNG có quyền nào — `cho_phep_rong=False`
    (phạm vi một khoá) thì rỗng → 400; `cho_phep_rong=True` (trần của người dùng) thì rỗng hợp lệ.
    `kiem_tra_module=True`: kiểm tra sự tồn tại của module cho phạm vi có `@<module>` (module lạ → 400).
    """
    if not isinstance(gia_tri, list) or len(gia_tri) > TOI_DA_PHAN_TU_PHAM_VI:
        raise LoiOpenAI(400, f"Trường «{ten_truong}» phải là danh sách mã phạm vi (tối đa {TOI_DA_PHAN_TU_PHAM_VI} phần tử).",
                        code="invalid_value")
    ket_qua: list[str] = []
    for x in gia_tri:
        if not la_ma_pham_vi(x):
            raise LoiOpenAI(400, f"Trường «{ten_truong}» chỉ nhận «*», mã khả năng [a-z][a-z0-9_] (có thể kèm «:<mã>») hoặc «<kha_nang>@<module>».",
                            code="invalid_value")
        if kiem_tra_module and "@" in x:
            _, _, phan_mod = x.partition("@")
            if not module_ton_tai(phan_mod, so=so):
                raise LoiOpenAI(400, f"Module «{phan_mod}» không tồn tại trên hệ thống.", code="module_not_found")
        if x not in ket_qua:
            ket_qua.append(x)
    if not ket_qua and not cho_phep_rong:
        raise LoiOpenAI(400, f"Trường «{ten_truong}» không được rỗng — rỗng là không có quyền nào, không phải «tất cả».",
                        code="invalid_value")
    return ket_qua


def pham_vi_nam_trong_tran(pv: str, tran: list[str]) -> bool:
    """Kiểm tra quyền `pv` có nằm trong trần `tran` hay không (không vượt trần)."""
    if "*" in tran:
        from app.xac_thuc import la_kha_nang_nguy_hiem
        kn = pv.split("@")[0]
        if la_kha_nang_nguy_hiem(kn):
            return pv in tran
        return True
    if pv in tran:
        return True
    if "@" in pv:
        kn, _, mod = pv.partition("@")
        if kn in tran:
            return True
        if "*@" + mod in tran:
            from app.xac_thuc import la_kha_nang_nguy_hiem
            return not la_kha_nang_nguy_hiem(kn)
    return False


def giao_pham_vi(pham_vi: list[str], tran: list[str]) -> list[str]:
    """Phạm vi hiệu lực = GIAO của phạm vi khoá với trần."""
    if "*" in tran:
        return list(pham_vi)
    if "*" in pham_vi:
        return list(tran)
    ket_qua = []
    for p in pham_vi:
        if pham_vi_nam_trong_tran(p, tran):
            ket_qua.append(p)
    return ket_qua


def tran_dong_thoi() -> int | None:
    """Trần yêu cầu đồng thời cho mỗi khoá khách. Mặc định 4.
    Biến WEB2API_KHACH_DONG_THOI: số nguyên > 0 đặt trần; '0' = tắt tường minh (không giới hạn)."""
    val = os.environ.get("WEB2API_KHACH_DONG_THOI")
    if val is None:
        return MAC_DINH_KHACH_DONG_THOI
    val = val.strip()
    if not val:
        return MAC_DINH_KHACH_DONG_THOI
    try:
        n = int(val)
        if n == 0:
            return None
        return n if n > 0 else MAC_DINH_KHACH_DONG_THOI
    except ValueError:
        return MAC_DINH_KHACH_DONG_THOI


def tran_dong_thoi_toan_cuc() -> int | None:
    """Trần yêu cầu đồng thời toàn cục cho /v1. Mặc định 32.
    Biến WEB2API_V1_DONG_THOI_TOAN_CUC: số nguyên > 0 đặt trần; '0' = tắt tường minh (không giới hạn)."""
    val = os.environ.get("WEB2API_V1_DONG_THOI_TOAN_CUC")
    if val is None:
        return MAC_DINH_V1_DONG_THOI_TOAN_CUC
    val = val.strip()
    if not val:
        return MAC_DINH_V1_DONG_THOI_TOAN_CUC
    try:
        n = int(val)
        if n == 0:
            return None
        return n if n > 0 else MAC_DINH_V1_DONG_THOI_TOAN_CUC
    except ValueError:
        return MAC_DINH_V1_DONG_THOI_TOAN_CUC


def _doc_tu_dia_neu_can() -> list[dict[str, Any]]:
    """Đọc tệp khoa_khach.json nếu cache chưa có hoặc tệp bị đổi bên ngoài (so stat).

    Luôn gọi khi đang giữ _KHOA_LUONG. Tệp KHÔNG tồn tại → kho rỗng. Tệp tồn tại mà đọc lỗi, JSON hỏng hoặc sai cấu
    trúc (không qua `kiem_tep` — cùng bộ kiểm của khôi phục) → LoiKhoKhoaKhach, cache giữ
    nguyên. Không lọc lặng lẽ phần tử hỏng: lọc rồi lần ghi sau sẽ xoá mất chúng.
    """
    global _KHOA_KHACH_CACHE, _CACHE_FILE_STAT, _CO_THAY_DOI
    p = _p_khoa_khach()
    try:
        st = os.stat(p)
    except FileNotFoundError:
        if not _CO_THAY_DOI:
            _KHOA_KHACH_CACHE = []
            _CACHE_FILE_STAT = None
        return _KHOA_KHACH_CACHE
    except OSError as e:
        log.warning("Không đọc được %s: %s", p, type(e).__name__)
        raise LoiKhoKhoaKhach() from None
    danh_tinh = (str(p), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
    if _CACHE_FILE_STAT == danh_tinh:
        return _KHOA_KHACH_CACHE
    try:
        du_lieu = kho.doc_json(p)
    except kho.KhoHong as e:
        log.warning("Kho khoá khách hỏng: %s", e)
        raise LoiKhoKhoaKhach() from None
    try:
        # CÙNG bộ kiểm của khôi phục (`kiem_tep`): kiểm nông «có id và bam» từng để lọt `bam` không ASCII (ném ở
        # compare_digest → 500 cho MỌI Bearer, cả khoá quản trị) và `pham_vi` sai kiểu (500 ở danh sách khoá).
        # Chỉ nới trần độ dài nhãn: tệp do bản cũ ghi (chưa chặn nhãn dài) không được làm sập cả kho.
        kiem_tep(du_lieu, nhan_toi_da=None)
    except ValueError as e:
        log.warning("Kho khoá khách sai cấu trúc (%s): %s", p, e)
        raise LoiKhoKhoaKhach() from None
    _KHOA_KHACH_CACHE = [dict(k) for k in du_lieu["khoa"]]
    _CACHE_FILE_STAT = danh_tinh
    _CO_THAY_DOI = False
    return _KHOA_KHACH_CACHE


def kiem_tra_kho() -> None:
    """Ném LoiKhoKhoaKhach nếu kho tồn tại mà không đọc được — cho đường cần kiểm TRƯỚC khi đổi trạng thái khác."""
    with _KHOA_LUONG:
        _doc_tu_dia_neu_can()


def _ghi_ra_dia_khoi_tao() -> None:
    """Ghi cache hiện tại ra đĩa với quyền 0600 (nguyên tử). Luôn gọi khi đang giữ _KHOA_LUONG."""
    global _CACHE_FILE_STAT, _CO_THAY_DOI
    _don_khoa_cu_het_han_trong_cache()
    p = _p_khoa_khach()
    du_lieu = {"khoa": _KHOA_KHACH_CACHE}
    kho.ghi_nguyen_tu(p, kho.json.dumps(du_lieu, ensure_ascii=False, indent=2), quyen=0o600)
    try:
        st = os.stat(p)
        _CACHE_FILE_STAT = (str(p), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
    except Exception:
        _CACHE_FILE_STAT = None
    _CO_THAY_DOI = False


def dong_bo_dia() -> None:
    """Đồng bộ dữ liệu thay đổi từ bộ nhớ ra đĩa nếu có cờ _CO_THAY_DOI (chạy theo nhịp / shutdown)."""
    with _KHOA_LUONG:
        if not _CO_THAY_DOI:
            return
        try:
            _doc_tu_dia_neu_can()  # tệp đổi từ ngoài → nạp lại (bỏ đếm lượt); hỏng → KHÔNG ghi đè
        except LoiKhoKhoaKhach:
            return  # không ném: vòng nền và lúc tắt tiến trình gọi hàm này
        if _CO_THAY_DOI:
            _ghi_ra_dia_khoi_tao()


def lam_moi_cache() -> None:
    """Xoá cache trong bộ nhớ để lượt đọc tiếp theo nạp lại tệp khoa_khach.json từ đĩa."""
    global _KHOA_KHACH_CACHE, _CACHE_FILE_STAT, _CO_THAY_DOI
    with _KHOA_LUONG:
        _KHOA_KHACH_CACHE = []
        _CACHE_FILE_STAT = None
        _CO_THAY_DOI = False


@contextmanager
def thay_tep_tu_ngoai():
    """Ghi đè khoa_khach.json từ NGOÀI module (khôi phục) trong khối này: giữ khoá luồng suốt lượt ghi để nhịp
    `dong_bo_dia` không ghi cache CŨ đè lên tệp mới, rồi bỏ cache để lượt tra sau đọc lại từ đĩa."""
    with _KHOA_LUONG:
        try:
            yield
        finally:
            lam_moi_cache()


_ID_KHOA = re.compile(r"[A-Za-z0-9_-]{1,64}")
_BAM_HOP_LE = re.compile(r"[0-9a-f]{64}")


#: Trần độ dài nhãn khoá — kiểm ở biên tạo/sửa và ở khôi phục (mọi khoá ứng dụng ghi ra phải khôi phục lại được).
TOI_DA_NHAN = 200


def _kiem_nhan(nhan: Any) -> str:
    if not isinstance(nhan, str) or len(nhan) > TOI_DA_NHAN:
        raise LoiOpenAI(400, f"Trường «nhan» phải là chuỗi tối đa {TOI_DA_NHAN} ký tự.", code="invalid_value")
    return nhan


def kiem_tep(du_lieu: Any, *, nhan_toi_da: int | None = TOI_DA_NHAN) -> list[dict[str, Any]]:
    """Kiểm THUẦN (không ghi) nội dung khoa_khach.json — bộ kiểm DUY NHẤT cho cả khôi phục và lượt nạp tệp từ đĩa
    (`_doc_tu_dia_neu_can`, với `nhan_toi_da=None`), cùng hình dạng `tao_khoa` ghi ra.

    Mọi trường quyết định quyền đều BẮT BUỘC và đúng kiểu: thiếu `pham_vi`/`bat` không được rơi về mặc định «*»/bật
    của đường xác thực. Trả danh sách bản ghi chuẩn hoá; sai → ValueError nêu vị trí và quy tắc, không chép giá trị.
    """
    ds = du_lieu.get("khoa") if isinstance(du_lieu, dict) else None
    if not isinstance(ds, list):
        raise ValueError('cần dạng {"khoa": [...]}')
    ra, id_da_thay, bam_da_thay = [], set(), set()
    for i, k in enumerate(ds):
        if not isinstance(k, dict):
            raise ValueError(f"khoa[{i}]: phải là đối tượng")
        if not isinstance(k.get("id"), str) or not _ID_KHOA.fullmatch(k["id"]):
            raise ValueError(f"khoa[{i}]: «id» phải gồm chữ, số, «_», «-» (1–64 ký tự)")
        if not isinstance(k.get("bam"), str) or not _BAM_HOP_LE.fullmatch(k["bam"]):
            raise ValueError(f"khoa[{i}]: «bam» phải là SHA-256 dạng 64 ký tự hex thường")
        bam_cu = k.get("bam_cu")
        cu_het_han = k.get("cu_het_han")
        if bam_cu is not None or cu_het_han is not None:
            if bam_cu is None or cu_het_han is None:
                raise ValueError(f"khoa[{i}]: phải có đủ cả «bam_cu» và «cu_het_han» (hoặc không có cả hai)")
            if not isinstance(bam_cu, str) or not _BAM_HOP_LE.fullmatch(bam_cu):
                raise ValueError(f"khoa[{i}]: «bam_cu» phải là SHA-256 dạng 64 ký tự hex thường")
            if not _la_iso_hop_le(cu_het_han):
                raise ValueError(f"khoa[{i}]: «cu_het_han» phải là chuỗi thời gian ISO hợp lệ")
            if k["bam"] == bam_cu:
                raise ValueError(f"khoa[{i}]: «bam_cu» không được trùng «bam» của chính khoá này")
            if bam_cu in bam_da_thay:
                raise ValueError(f"khoa[{i}]: «bam_cu» trùng băm mục trước")
        if k["id"] in id_da_thay or k["bam"] in bam_da_thay:
            raise ValueError(f"khoa[{i}]: «id» hoặc «bam» trùng mục trước")
        if not isinstance(k.get("bat"), bool):
            raise ValueError(f"khoa[{i}]: «bat» phải là true/false")
        try:  # CÙNG bộ kiểm với đường tạo/sửa khoá — tệp khôi phục không được hẹp hơn hay rộng hơn thứ đã ghi ra
            pv = chuan_hoa_pham_vi(k.get("pham_vi"), cho_phep_rong=False)
        except LoiOpenAI:
            raise ValueError(f"khoa[{i}]: «pham_vi» phải là danh sách khác rỗng (tối đa {TOI_DA_PHAN_TU_PHAM_VI} phần "
                             "tử) gồm «*» hoặc mã khả năng (có thể kèm «:<mã>»)") from None
        nhan = k.get("nhan", "Khoá khách")
        so_luot = k.get("so_luot", 0)
        if not isinstance(nhan, str) or (nhan_toi_da is not None and len(nhan) > nhan_toi_da):
            raise ValueError(f"khoa[{i}]: «nhan» phải là chuỗi ≤ {TOI_DA_NHAN} ký tự")
        if not isinstance(so_luot, int) or isinstance(so_luot, bool) or so_luot < 0:
            raise ValueError(f"khoa[{i}]: «so_luot» phải là số nguyên ≥ 0")
        for t in ("tao_luc", "dung_cuoi", "het_han"):
            if k.get(t) is not None and not isinstance(k[t], str):
                raise ValueError(f"khoa[{i}]: «{t}» phải là chuỗi hoặc null")
        chu_id = k.get("chu_id")
        if chu_id is not None and (not isinstance(chu_id, str) or not _ID_KHOA.fullmatch(chu_id)):
            raise ValueError(f"khoa[{i}]: «chu_id» phải là null hoặc mã người dùng hợp lệ")
        het_han = k.get("het_han")
        id_da_thay.add(k["id"])
        bam_da_thay.add(k["bam"])
        if bam_cu:
            bam_da_thay.add(bam_cu)
        muc_ra = {"id": k["id"], "nhan": nhan, "bat": k["bat"], "pham_vi": pv,
                  "tao_luc": k.get("tao_luc"), "dung_cuoi": k.get("dung_cuoi"), "so_luot": so_luot,
                  "bam": k["bam"], **({"chu_id": chu_id} if chu_id else {})}
        if het_han is not None:
            muc_ra["het_han"] = het_han
        if bam_cu:
            muc_ra["bam_cu"] = bam_cu
            muc_ra["cu_het_han"] = cu_het_han
        ra.append(muc_ra)
    return ra


def _rong_hon(moi: list[str], cu: list[str] | None) -> bool:
    """Phạm vi `moi` cho quyền mà `cu` không cho (khoá mới: `cu=None`, chỉ «*» mới là cờ đỏ)."""
    if cu is None:
        return "*" in moi
    if "*" in cu:
        from app.xac_thuc import la_kha_nang_nguy_hiem
        for x in moi:
            kn = x.split("@")[0]
            if la_kha_nang_nguy_hiem(kn) and x not in cu:
                return True
        return False
    if "*" in moi:
        return True
    return any(not pham_vi_nam_trong_tran(x, cu) for x in moi)


def _chu_cua(chu_id: str) -> tuple[dict[str, Any] | None, str | None]:
    """(bản ghi chủ, None) hoặc (None, lý do chủ không dùng được). Gọi NGOÀI _KHOA_LUONG."""
    from app import nguoi_dung
    try:
        nd = nguoi_dung.tim_theo_id(chu_id)
    except nguoi_dung.LoiKhoNguoiDung:
        return None, "kho người dùng không đọc được"
    if nd is None:
        return None, "chủ không tồn tại trên máy này"
    if nd.get("bat", True) is not True:
        return None, "chủ đang bị tắt"
    return nd, None


def khac_biet(moi: list[dict[str, Any]]) -> dict[str, Any]:
    """So danh sách khoá (đã qua `kiem_tep`) với khoá đang có — cho người duyệt khôi phục thấy mình chấp nhận gì:
    khoá MỚI (có thể là băm do người khác chọn = cửa vào /v1), khoá cùng id mà ĐỔI BĂM, ĐỔI CHỦ (`doi_chu`), đổi phạm
    vi/bật/nhãn, và khoá đang có sẽ MẤT. Mỗi mục nêu `chu_id` (và `chu_id_hien_tai` nếu khoá đang có), cờ
    `mo_rong_pham_vi` (khoá mới «*», hoặc phạm vi rộng hơn bản đang có), cờ `bat_lai` (khoá thô đang TẮT sẽ được bật)
    và `canh_bao` (khoá mới/đổi có «*», bật lại, chủ không dùng được → khoá sẽ bị đóng sau khôi phục). `bo_chu=True` khi khoá thô ĐANG có chủ (khớp theo id HOẶC theo băm — băm mới là thứ xác
    thực, id chỉ là nhãn) mà bản sao lưu bỏ/đổi chủ — sao_luu chặn mục đó. Kho hiện tại hỏng → mọi khoá là «moi», `kho_hien_tai_hong=True` (khôi phục là đường cứu,
    không được chặn nó). Không trả băm."""
    hong = False
    with _KHOA_LUONG:
        try:
            cu = {k.get("id"): dict(k) for k in _doc_tu_dia_neu_can()}
        except LoiKhoKhoaKhach:
            cu, hong = {}, True
    cu_theo_bam: dict[str, dict[str, Any]] = {}
    for c in cu.values():
        if c.get("bam"):
            cu_theo_bam[c["bam"]] = c
        if c.get("bam_cu"):
            cu_theo_bam[c["bam_cu"]] = c
    ds = []
    for k in moi:
        c = cu.get(k["id"])
        cac_bam_k = [k["bam"]]
        if k.get("bam_cu"):
            cac_bam_k.append(k["bam_cu"])
        dang_co = [c] if c is not None else []
        for b in cac_bam_k:
            cb = cu_theo_bam.get(b)
            if cb is not None and cb not in dang_co:
                dang_co.append(cb)
        chu_dang_co = {x.get("chu_id") for x in dang_co if x.get("chu_id")}
        if c is None:
            tt = "moi"
        elif c.get("bam") != k["bam"]:
            tt = "doi_bam"
        elif c.get("chu_id") != k.get("chu_id"):
            tt = "doi_chu"
        elif (c.get("bat", True), list(c.get("pham_vi", ["*"])), c.get("nhan"),
              c.get("bam_cu"), c.get("cu_het_han"), c.get("het_han")) != (
                  k["bat"], k["pham_vi"], k["nhan"], k.get("bam_cu"), k.get("cu_het_han"), k.get("het_han")):
            tt = "doi"
        else:
            tt = "giu_nguyen"
        trung_khoa_khac = any(x.get("id") != k["id"] for x in dang_co)
        bo_chu = any(ch != k.get("chu_id") for ch in chu_dang_co)
        muc = {"id": k["id"], "nhan": k["nhan"], "bat": k["bat"], "pham_vi": list(k["pham_vi"]),
               "chu_id": k.get("chu_id"), "trang_thai": tt,
               "mo_rong_pham_vi": _rong_hon(k["pham_vi"], None if c is None else list(c.get("pham_vi", ["*"]))),
               "bat_lai": k["bat"] and any(x.get("bat", True) is False for x in dang_co),
               "canh_bao": None, "bo_chu": bo_chu, "trung_khoa_khac": trung_khoa_khac,
               "co_khoa_cu": bool(k.get("bam_cu")), "cu_het_han": k.get("cu_het_han"),
               "het_han": k.get("het_han")}
        if c is not None:
            muc["chu_id_hien_tai"] = c.get("chu_id")
        canh_bao = []
        if tt != "giu_nguyen" and "*" in k["pham_vi"]:
            canh_bao.append("phạm vi «*» — chỉ bao gồm khả năng đọc/suy luận an toàn; các khả năng nguy hiểm (điều khiển thiết bị, trình duyệt, gửi ra) cần cấp đích danh")
        if muc["bat_lai"]:
            canh_bao.append("khoá đang TẮT sẽ được BẬT LẠI")
        if k.get("chu_id"):
            _, ly_do = _chu_cua(k["chu_id"])
            if ly_do:
                canh_bao.append(f"{ly_do} — khoá sẽ bị đóng sau khôi phục (401) cho tới khi chủ có mặt và được bật")
        muc["canh_bao"] = "; ".join(canh_bao) or None
        ds.append(muc)
    con = {k["id"] for k in moi}
    return {"so_khoa": len(moi), "khoa": ds, "se_mat": sorted(str(i) for i in cu if i not in con),
            "kho_hien_tai_hong": hong}


def _khoa_het_han_hoac_tu_tat(k: dict[str, Any]) -> bool:
    """Kiểm tra khoá khách hết hạn theo trường `het_han` (ISO UTC) hoặc tự tắt khi lâu không dùng
    (`WEB2API_KHACH_TU_TAT_NGAY`). Tra cứu thời gian ĐÓNG KHI LỖI: trường thời gian hỏng → True (hết hạn).
    """
    het_han = k.get("het_han")
    if het_han is not None:
        if not _la_tuong_lai(het_han):
            return True

    env_ngay = os.environ.get("WEB2API_KHACH_TU_TAT_NGAY", "0").strip()
    try:
        n_ngay = int(env_ngay)
    except ValueError:
        n_ngay = 0
    if n_ngay > 0:
        moc_str = k.get("dung_cuoi") or k.get("tao_luc")
        if not moc_str or not isinstance(moc_str, str):
            return True
        try:
            dt = datetime.fromisoformat(moc_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - dt > timedelta(days=n_ngay):
                return True
        except Exception:
            return True

    return False


def ap_chinh_sach_chu(k: dict[str, Any]) -> dict[str, Any]:
    """Chính sách DUY NHẤT từ bản ghi thô ra khoá hiệu lực (bản sao). Khoá không chủ → nguyên. Khoá có chủ: chủ bị
    tắt/xoá hoặc kho người dùng đọc lỗi → `bat=False` (đóng khi lỗi); còn lại `pham_vi` = GIAO(phạm vi khoá, trần
    HIỆN TẠI của chủ) — hạ trần/hạ vai thu hẹp khoá đã cấp ngay ở lượt sau.
    Khoá hết hạn hoặc quá hạn lâu không dùng → `bat=False`. Gọi NGOÀI _KHOA_LUONG."""
    k = dict(k)
    if _khoa_het_han_hoac_tu_tat(k):
        k["bat"] = False
        return k
    chu_id = k.get("chu_id")
    if not chu_id:
        return k
    from app import nguoi_dung
    nd, _ = _chu_cua(chu_id)
    if nd is None:
        k["bat"] = False
    else:
        k["pham_vi"] = giao_pham_vi(list(k.get("pham_vi", ["*"])), nguoi_dung.tran_pham_vi(nd))
    return k


def ban_ghi_theo_bam(bam_tim: str) -> dict[str, Any] | None:
    """Bản ghi THÔ (bản sao, chưa áp chính sách chủ) theo băm — thứ đã ghi trên đĩa. Kho hỏng → LoiKhoKhoaKhach."""
    if not isinstance(bam_tim, str) or not _BAM_HOP_LE.fullmatch(bam_tim):
        return None
    bam_tim_bytes = bam_tim.encode("ascii")
    zero_bytes = b"0" * 64
    with _KHOA_LUONG:
        for k in _doc_tu_dia_neu_can():
            khop_moi = hmac.compare_digest(bam_tim_bytes, str(k.get("bam", "")).encode("ascii"))
            bam_cu = k.get("bam_cu")
            khop_cu = False
            if bam_cu and _la_tuong_lai(k.get("cu_het_han")):
                khop_cu = hmac.compare_digest(bam_tim_bytes, str(bam_cu).encode("ascii"))
            else:
                _ = hmac.compare_digest(bam_tim_bytes, zero_bytes)
            if khop_moi or khop_cu:
                return dict(k)
    return None


def tim_theo_bam(bam_tim: str, *, nem_khi_kho_hong: bool = False) -> dict[str, Any] | None:
    """Tra khoá khách theo băm SHA-256 (đường tra thật của xác thực, sau bước băm): bản ghi thô qua
    `ap_chinh_sach_chu`. None nếu không có — và cả khi kho khoá khách hỏng (ĐÓNG: không cấp quyền, không 500; khoá
    quản trị vẫn dùng được để khôi phục). Tra chủ chạy sau khi nhả khoá khoá khách."""
    try:
        k = ban_ghi_theo_bam(bam_tim)
    except LoiKhoKhoaKhach:
        if nem_khi_kho_hong:  # bên gọi cần phân biệt «không có khoá này» với «máy chủ không đọc được kho»
            raise
        return None
    return None if k is None else ap_chinh_sach_chu(k)


#: Dạng khoá khách do `tao_khoa` sinh: «w2a_» + token_urlsafe(32). Dùng để biết một Bearer CÓ THỂ là khoá khách.
DANG_KHOA_THO = re.compile(r"w2a_[A-Za-z0-9_-]{43}")


def tim_theo_khoa_tho(khoa_tho: str, *, nem_khi_kho_hong: bool = False) -> dict[str, Any] | None:
    """Đường nóng: tìm khoá khách theo chuỗi khoá thô (tính sha256 rồi tra bằng `tim_theo_bam`).

    Không ghi đĩa. Trả về dict khoá (bản sao hoặc tham chiếu an toàn) hoặc None.
    """
    if not khoa_tho or not isinstance(khoa_tho, str):
        return None
    return tim_theo_bam(hashlib.sha256(khoa_tho.encode("utf-8")).hexdigest(), nem_khi_kho_hong=nem_khi_kho_hong)


def ghi_nhan_su_dung(id_khoa: str) -> None:
    """Đường nóng: tăng số lượt và cập nhật lần dùng cuối trong bộ nhớ; đánh dấu cờ thay đổi."""
    global _CO_THAY_DOI
    bay_gio = kho.bay_gio_iso()
    with _KHOA_LUONG:
        for k in _KHOA_KHACH_CACHE:
            if k.get("id") == id_khoa:
                k["so_luot"] = int(k.get("so_luot", 0)) + 1
                k["dung_cuoi"] = bay_gio
                _CO_THAY_DOI = True
                break


def vao_dong_thoi(id_khoa: str) -> None:
    """Kiểm tra và chiếm một vị trí xử lý đồng thời cho khoá khách. Vượt trần → 429 kèm Retry-After."""
    tran = tran_dong_thoi()
    if tran is None:
        return
    with _KHOA_LUONG:
        hien_tai = _DONG_THOI_DANG_CHAY.get(id_khoa, 0)
        if hien_tai >= tran:
            raise LoiOpenAI(
                429,
                f"Quá giới hạn yêu cầu đồng thời cho khoá khách (tối đa {tran} yêu cầu cùng lúc).",
                loai="rate_limit_error",
                code="rate_limit_exceeded",
                headers={"Retry-After": "1"},
            )
        _DONG_THOI_DANG_CHAY[id_khoa] = hien_tai + 1


def ra_dong_thoi(id_khoa: str | None) -> None:
    """Giải phóng một vị trí xử lý đồng thời khi kết thúc lượt gọi."""
    if not id_khoa:
        return
    with _KHOA_LUONG:
        if id_khoa in _DONG_THOI_DANG_CHAY:
            con = _DONG_THOI_DANG_CHAY[id_khoa] - 1
            if con <= 0:
                _DONG_THOI_DANG_CHAY.pop(id_khoa, None)
            else:
                _DONG_THOI_DANG_CHAY[id_khoa] = con


def vao_dong_thoi_toan_cuc() -> None:
    """Kiểm tra và chiếm một vị trí xử lý đồng thời toàn cục cho /v1. Vượt trần → 429 kèm Retry-After."""
    global _V1_DONG_THOI_HIEN_TAI
    tran = tran_dong_thoi_toan_cuc()
    if tran is None:
        return
    with _KHOA_LUONG:
        if _V1_DONG_THOI_HIEN_TAI >= tran:
            raise LoiOpenAI(
                429,
                f"Cổng /v1 quá tải yêu cầu đồng thời toàn cục (tối đa {tran} yêu cầu cùng lúc).",
                loai="rate_limit_error",
                code="rate_limit_exceeded",
                headers={"Retry-After": "1"},
            )
        _V1_DONG_THOI_HIEN_TAI += 1


def ra_dong_thoi_toan_cuc() -> None:
    """Giải phóng một vị trí xử lý đồng thời toàn cục cho /v1."""
    global _V1_DONG_THOI_HIEN_TAI
    with _KHOA_LUONG:
        if _V1_DONG_THOI_HIEN_TAI > 0:
            _V1_DONG_THOI_HIEN_TAI -= 1


# ───────────── Quản trị qua API ─────────────

def _sach_khoa(k: dict[str, Any]) -> dict[str, Any]:
    """Trả về bản ghi an toàn: bỏ trường `bam`, `bam_cu` và mọi thông tin bí mật.
    Thêm `van_tay`, `cu_het_han`, `het_han` và `co_khoa_cu: bool`."""
    bam = k.get("bam", "")
    cu_het_han = k.get("cu_het_han")
    het_han = k.get("het_han")
    co_khoa_cu = bool(k.get("bam_cu") and _la_tuong_lai(cu_het_han))
    return {
        "id": k.get("id"),
        "nhan": k.get("nhan"),
        "bat": bool(k.get("bat", True)),
        "pham_vi": list(k.get("pham_vi", ["*"])),
        "chu_id": k.get("chu_id"),
        "tao_luc": k.get("tao_luc"),
        "dung_cuoi": k.get("dung_cuoi"),
        "so_luot": int(k.get("so_luot", 0)),
        "van_tay": van_tay(bam) if bam else "?",
        "het_han": het_han,
        "cu_het_han": cu_het_han,
        "co_khoa_cu": co_khoa_cu,
    }


def _kem_hieu_luc(ds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bản ghi an toàn kèm `pham_vi_hieu_luc` — đúng thứ xác thực sẽ cấp (cùng `ap_chinh_sach_chu`); khoá tắt hoặc chủ
    không dùng được → []. `pham_vi` giữ nguyên là phạm vi gốc đã lưu."""
    ra = []
    for k in ds:
        hl = ap_chinh_sach_chu(k)
        ra.append({**_sach_khoa(k), "pham_vi_hieu_luc": list(hl.get("pham_vi", ["*"])) if hl.get("bat", True) else []})
    return ra


def danh_sach_khoa() -> list[dict[str, Any]]:
    """Liệt kê mọi khoá khách (không băm, không khoá thô)."""
    with _KHOA_LUONG:
        ds = [dict(k) for k in _doc_tu_dia_neu_can()]
    return _kem_hieu_luc(ds)


def danh_sach_khoa_theo_chu(chu_id: str) -> list[dict[str, Any]]:
    """Liệt kê khoá khách thuộc về một người dùng cụ thể."""
    with _KHOA_LUONG:
        ds = [dict(k) for k in _doc_tu_dia_neu_can() if k.get("chu_id") == chu_id]
    return _kem_hieu_luc(ds)


def xoa_khoa_theo_chu(chu_id: str) -> int:
    """Xoá toàn bộ khoá khách thuộc về một người dùng (khi xoá người dùng)."""
    with _KHOA_LUONG:
        ds = _doc_tu_dia_neu_can()
        ban_dau = len(ds)
        _KHOA_KHACH_CACHE[:] = [k for k in ds if k.get("chu_id") != chu_id]
        da_xoa = ban_dau - len(_KHOA_KHACH_CACHE)
        if da_xoa > 0:
            _ghi_ra_dia_khoi_tao()
    return da_xoa


def tao_khoa(
    nhan: str = "",
    pham_vi: list[str] | None = None,
    chu_id: str | None = None,
    het_han: str | None = None,
    so: Any = None,
) -> tuple[dict[str, Any], str]:
    """Tạo khoá khách mới: sinh khoá ngẫu nhiên hiện MỘT LẦN, ghi nguyên tử 0600 và ghi sổ quan_tri.

    `pham_vi=None` (không nêu) = ["chat"] — mặc định hẹp nhất, an toàn khi khoá lộ. Đã nêu thì phải qua `chuan_hoa_pham_vi`
    (danh sách rỗng → 400). Muốn toàn quyền phải nêu ["*"] tường minh.
    `het_han`: chuỗi thời gian ISO UTC thời điểm hết hạn (tuỳ chọn). Quá hạn hoặc hỏng → 401 như khoá bị tắt.
    """
    nhan_chuan = _kiem_nhan("" if nhan is None else nhan).strip() or "Khoá khách"
    pv = ["chat"] if pham_vi is None else chuan_hoa_pham_vi(pham_vi, cho_phep_rong=False, kiem_tra_module=True, so=so)
    if het_han is not None:
        if not _la_iso_hop_le(het_han):
            raise LoiOpenAI(400, "Trường «het_han» phải là chuỗi thời gian ISO hợp lệ.", code="invalid_value")
    khoa_tho = f"w2a_{secrets.token_urlsafe(32)}"
    bam = hashlib.sha256(khoa_tho.encode("utf-8")).hexdigest()
    id_khoa = f"kk_{secrets.token_hex(6)}"
    tao_luc = kho.bay_gio_iso()

    muc = {
        "id": id_khoa,
        "nhan": nhan_chuan,
        "bat": True,
        "pham_vi": pv,
        "chu_id": chu_id,
        "tao_luc": tao_luc,
        "dung_cuoi": None,
        "so_luot": 0,
        "bam": bam,
    }
    if het_han is not None:
        muc["het_han"] = het_han

    with _KHOA_LUONG:
        _doc_tu_dia_neu_can()
        _KHOA_KHACH_CACHE.append(muc)
        _ghi_ra_dia_khoi_tao()

    nk_kwargs: dict[str, Any] = {
        "id_khoa": id_khoa,
        "khoa_van_tay": van_tay(bam),
        "nhan": nhan_chuan,
        "pham_vi": pv,
        "chu_id": chu_id,
    }
    if het_han is not None:
        nk_kwargs["het_han"] = het_han
    nhat_ky.ghi("quan_tri", "thong_tin", f"Tạo khoá khách «{nhan_chuan}» (id: {id_khoa})", **nk_kwargs)
    ban_ghi = _sach_khoa(muc)
    return ban_ghi, khoa_tho


def xoay_khoa(
    id_khoa: str,
    cu_giay: Any = CU_GIAY_MAC_DINH,
) -> tuple[dict[str, Any], str]:
    """Xoay khoá khách: sinh khoá mới, đẩy bam hiện tại thành bam_cu, đặt cu_het_han.

    - `cu_giay`: số giây khoá cũ còn dùng được (0 đến CU_GIAY_TOI_DA; mặc định CU_GIAY_MAC_DINH).
    - cu_giay=0: khoá cũ hết hạn ngay.
    - Trả (bản_ghi_an_toàn, khoa_thô_mới).
    """
    if cu_giay is None:
        cu_giay = CU_GIAY_MAC_DINH
    if isinstance(cu_giay, bool) or not isinstance(cu_giay, int):
        raise LoiOpenAI(400, "Trường «cu_giay» phải là số nguyên (giây).", code="invalid_value")
    if cu_giay < 0 or cu_giay > CU_GIAY_TOI_DA:
        raise LoiOpenAI(400, f"Trường «cu_giay» phải từ 0 đến {CU_GIAY_TOI_DA} giây.", code="invalid_value")

    khoa_tho = f"w2a_{secrets.token_urlsafe(32)}"
    bam_moi = hashlib.sha256(khoa_tho.encode("utf-8")).hexdigest()
    cu_het_han_dt = datetime.now(timezone.utc) + timedelta(seconds=cu_giay)
    cu_het_han = cu_het_han_dt.isoformat(timespec="seconds")

    with _KHOA_LUONG:
        ds = _doc_tu_dia_neu_can()
        tim_thay = None
        for k in ds:
            if k.get("id") == id_khoa:
                tim_thay = k
                break
        if not tim_thay:
            raise LoiOpenAI(404, f"Không tìm thấy khoá khách có id {id_khoa!r}", code="key_not_found")

        bam_hien_tai = tim_thay["bam"]
        tim_thay["bam_cu"] = bam_hien_tai
        tim_thay["cu_het_han"] = cu_het_han
        tim_thay["bam"] = bam_moi

        ban_ghi = _sach_khoa(tim_thay)
        ban_ghi["cu_het_han"] = cu_het_han

        _ghi_ra_dia_khoi_tao()

    vt_moi = van_tay(bam_moi)
    nhat_ky.ghi(
        "quan_tri",
        "thong_tin",
        f"Xoay khoá khách (id: {id_khoa})",
        id_khoa=id_khoa,
        khoa_van_tay=vt_moi,
        cu_het_han=cu_het_han,
    )
    return ban_ghi, khoa_tho


_KHONG_DOI = object()


def sua_khoa(
    id_khoa: str,
    bat: bool | None = None,
    nhan: str | None = None,
    pham_vi: list[str] | None = None,
    het_han: Any = _KHONG_DOI,
    so: Any = None,
) -> dict[str, Any]:
    """Cập nhật thông tin khoá khách (bật/tắt, nhãn, phạm vi, hạn dùng). None = giữ nguyên; `bat` phải là bool thật."""
    if bat is not None and not isinstance(bat, bool):
        raise LoiOpenAI(400, "Trường «bat» phải là true hoặc false.", code="invalid_value")
    pv_moi = None if pham_vi is None else chuan_hoa_pham_vi(pham_vi, cho_phep_rong=False, kiem_tra_module=True, so=so)
    nhan_moi = None if nhan is None else _kiem_nhan(nhan).strip()

    if het_han is not _KHONG_DOI and het_han is not None and het_han != "":
        if not _la_iso_hop_le(het_han):
            raise LoiOpenAI(400, "Trường «het_han» phải là chuỗi thời gian ISO hợp lệ.", code="invalid_value")

    with _KHOA_LUONG:
        ds = _doc_tu_dia_neu_can()
        tim_thay = None
        for k in ds:
            if k.get("id") == id_khoa:
                tim_thay = k
                break
        if not tim_thay:
            raise LoiOpenAI(404, f"Không tìm thấy khoá khách có id {id_khoa!r}", code="key_not_found")

        if bat is not None:
            tim_thay["bat"] = bat
        if nhan_moi is not None:
            tim_thay["nhan"] = nhan_moi or tim_thay.get("nhan", "Khoá khách")
        if pv_moi is not None:
            tim_thay["pham_vi"] = pv_moi
        if het_han is not _KHONG_DOI:
            if het_han is None or het_han == "":
                tim_thay.pop("het_han", None)
            else:
                tim_thay["het_han"] = het_han

        _ghi_ra_dia_khoi_tao()
        kq = _sach_khoa(tim_thay)

    nhat_ky.ghi(
        "quan_tri",
        "thong_tin",
        f"Cập nhật khoá khách (id: {id_khoa})",
        id_khoa=id_khoa,
        khoa_van_tay=van_tay(tim_thay.get("bam", "")),
        bat=kq["bat"],
        nhan=kq["nhan"],
        pham_vi=kq["pham_vi"],
        het_han=kq.get("het_han"),
    )
    return kq


def xoa_khoa(id_khoa: str) -> bool:
    """Thu hồi khoá khách (xoá khỏi danh sách, ghi đĩa ngay, các lượt sau lập tức 401)."""
    bam_da_xoa = None
    with _KHOA_LUONG:
        ds = _doc_tu_dia_neu_can()
        ban_dau = len(ds)
        for k in ds:
            if k.get("id") == id_khoa:
                bam_da_xoa = k.get("bam")
                break
        _KHOA_KHACH_CACHE[:] = [k for k in ds if k.get("id") != id_khoa]
        da_xoa = len(_KHOA_KHACH_CACHE) < ban_dau
        if da_xoa:
            _ghi_ra_dia_khoi_tao()

    if da_xoa:
        vt = van_tay(bam_da_xoa) if bam_da_xoa else "?"
        nhat_ky.ghi(
            "quan_tri",
            "thong_tin",
            f"Thu hồi khoá khách (id: {id_khoa})",
            id_khoa=id_khoa,
            khoa_van_tay=vt,
        )
    return da_xoa
