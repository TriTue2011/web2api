"""Kho phiên tài khoản ở lõi và bộ quét kiểm phiên định kỳ (Việc C7).

Bối cảnh: nhiều dịch vụ (kiểu 2api) đăng nhập bằng cookie/phiên. Người dùng đăng nhập
tay một lần (có OTP/captcha), phiên được cất lại ở lõi; sau đó module chỉ mở lấy lại
phiên. Khi phiên chết hoặc hết hạn, lõi phát hiện và báo «cần đăng nhập lại» (không tự
vượt OTP/captcha, không giữ mật khẩu/OTP).

Thiết kế:
- Tệp: `<WEB2API_DATA>/bi_mat/phien/<module>/<tai_khoan>.json`
  Quyền tệp 0600, thư mục 0700. Ghi nguyên tử (mkstemp cùng thư mục -> fsync -> os.replace).
- Nội dung: cục JSON mờ do module quyết (`du_lieu`), cộng siêu dữ liệu do lõi giữ:
  `{tai_khoan, luc_luu, luc_kiem, het_han, trang_thai, so_lan_loi, du_lieu}`
- Trạng thái: "con_song" | "het_phien" | "chua_ro" | "can_dang_nhap".
- An toàn: ID module và tài khoản kiểm soát bằng regex chống đi lên thư mục (chỉ [a-z0-9_-]).
- Nhật ký: kênh `phien`, ghi nhận lưu/xoá/kiểm và cảnh báo khi cần đăng nhập lại;
  tuyệt đối KHÔNG ghi `du_lieu` hay khoá bí mật.
- Chưa mã hoá khi nghỉ (ghi nhận trong giới hạn tài liệu).
"""

from __future__ import annotations

import copy
import json
import logging
import os
import random
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import kho, nhat_ky

log = logging.getLogger("web2api.phien")

ID_HOP_LE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\Z")
_KHOA = threading.RLock()

MAC_DINH_CHU_KY_PHUT = 180
MAC_DINH_TOI_DA_MOI_VONG = 2
KHOANG_CACH_KIEM_TOI_THIEU_GIAY = 1800  # 30 phút


def chu_ky_phut() -> int:
    val = os.environ.get("WEB2API_PHIEN_CHU_KY_PHUT", "").strip()
    if not val:
        return MAC_DINH_CHU_KY_PHUT
    try:
        n = int(val)
        return n if n > 0 else MAC_DINH_CHU_KY_PHUT
    except ValueError:
        return MAC_DINH_CHU_KY_PHUT


def toi_da_moi_vong() -> int:
    val = os.environ.get("WEB2API_PHIEN_TOI_DA_MOI_VONG", "").strip()
    if not val:
        return MAC_DINH_TOI_DA_MOI_VONG
    try:
        n = int(val)
        return n if n > 0 else MAC_DINH_TOI_DA_MOI_VONG
    except ValueError:
        return MAC_DINH_TOI_DA_MOI_VONG


def kiem_tra_id(module: str, tai_khoan: str) -> None:
    if not isinstance(module, str) or not ID_HOP_LE.match(module):
        raise ValueError(f"id module không hợp lệ: {module!r}")
    if not isinstance(tai_khoan, str) or not ID_HOP_LE.match(tai_khoan):
        raise ValueError(f"tên tài khoản không hợp lệ: {tai_khoan!r}")


def thu_muc_phien(module: str) -> Path:
    goc_bi_mat = kho.thu_muc_du_lieu() / "bi_mat"
    goc_phien = goc_bi_mat / "phien"
    p = goc_phien / module
    p.mkdir(parents=True, exist_ok=True)
    for d in (goc_bi_mat, goc_phien, p):
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
    return p


def duong_tep_phien(module: str, tai_khoan: str) -> Path:
    kiem_tra_id(module, tai_khoan)
    return thu_muc_phien(module) / f"{tai_khoan}.json"


def da_het_han(het_han: str | None) -> bool:
    if not het_han:
        return False
    try:
        dt = datetime.fromisoformat(het_han)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt <= datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


def _ghi_nguyen_tu_0600(p: Path, du_lieu: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    noi_dung = json.dumps(du_lieu, ensure_ascii=False, indent=2)
    fd, tam = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tam")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(noi_dung)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tam, p)
    except BaseException:
        try:
            os.unlink(tam)
        except FileNotFoundError:
            pass
        raise


def luu(module: str, tai_khoan: str, du_lieu: dict, het_han: str | None = None) -> dict:
    kiem_tra_id(module, tai_khoan)
    if not isinstance(du_lieu, dict):
        raise ValueError("du_lieu phiên phải là đối tượng dict")
    if het_han is not None:
        try:
            datetime.fromisoformat(het_han)
        except (ValueError, TypeError) as e:
            raise ValueError(f"het_han không phải định dạng ISO hợp lệ: {het_han!r}") from e

    with _KHOA:
        p = duong_tep_phien(module, tai_khoan)
        trang_thai = "can_dang_nhap" if da_het_han(het_han) else "con_song"
        ban_ghi = {
            "tai_khoan": tai_khoan,
            "luc_luu": kho.bay_gio_iso(),
            "luc_kiem": None,
            "het_han": het_han,
            "trang_thai": trang_thai,
            "so_lan_loi": 0,
            "du_lieu": copy.deepcopy(du_lieu),
        }
        _ghi_nguyen_tu_0600(p, ban_ghi)
        nhat_ky.ghi("phien", "thong_tin", f"Lưu phiên cho {module}/{tai_khoan}", module=module, tai_khoan=tai_khoan)
        return copy.deepcopy(ban_ghi)


def lay(module: str, tai_khoan: str) -> dict | None:
    kiem_tra_id(module, tai_khoan)
    with _KHOA:
        p = duong_tep_phien(module, tai_khoan)
        if not p.is_file():
            return None
        try:
            ban_ghi = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(ban_ghi, dict):
            return None

        if da_het_han(ban_ghi.get("het_han")) and ban_ghi.get("trang_thai") != "can_dang_nhap":
            ban_ghi["trang_thai"] = "can_dang_nhap"
            try:
                _ghi_nguyen_tu_0600(p, ban_ghi)
            except OSError:
                pass
        return copy.deepcopy(ban_ghi)


def xoa(module: str, tai_khoan: str) -> bool:
    kiem_tra_id(module, tai_khoan)
    with _KHOA:
        p = duong_tep_phien(module, tai_khoan)
        if not p.is_file():
            return False
        try:
            p.unlink()
        except FileNotFoundError:
            return False
        nhat_ky.ghi("phien", "thong_tin", f"Xoá phiên cho {module}/{tai_khoan}", module=module, tai_khoan=tai_khoan)
        return True


def liet_ke(module: str | None = None) -> list[dict]:
    goc_phien = kho.thu_muc_du_lieu() / "bi_mat" / "phien"
    if not goc_phien.is_dir():
        return []

    with _KHOA:
        ket_qua = []
        if module is not None:
            if not isinstance(module, str) or not ID_HOP_LE.match(module):
                raise ValueError(f"id module không hợp lệ: {module!r}")
            ds_mod = [goc_phien / module]
        else:
            try:
                ds_mod = [d for d in goc_phien.iterdir() if d.is_dir()]
            except OSError:
                return []

        for d_mod in sorted(ds_mod, key=lambda d: d.name):
            mod_id = d_mod.name
            if not ID_HOP_LE.match(mod_id):
                continue
            try:
                cac_tep = [f for f in d_mod.glob("*.json") if f.is_file()]
            except OSError:
                continue

            for tep in sorted(cac_tep, key=lambda f: f.name):
                tk_id = tep.stem
                if not ID_HOP_LE.match(tk_id):
                    continue
                try:
                    ban_ghi = json.loads(tep.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(ban_ghi, dict):
                    continue

                het_han = ban_ghi.get("het_han")
                trang_thai = ban_ghi.get("trang_thai", "chua_ro")
                if da_het_han(het_han) and trang_thai != "can_dang_nhap":
                    trang_thai = "can_dang_nhap"
                    ban_ghi["trang_thai"] = "can_dang_nhap"
                    try:
                        _ghi_nguyen_tu_0600(tep, ban_ghi)
                    except OSError:
                        pass

                ket_qua.append({
                    "module": mod_id,
                    "tai_khoan": ban_ghi.get("tai_khoan", tk_id),
                    "luc_luu": ban_ghi.get("luc_luu"),
                    "luc_kiem": ban_ghi.get("luc_kiem"),
                    "het_han": het_han,
                    "trang_thai": trang_thai,
                    "so_lan_loi": ban_ghi.get("so_lan_loi", 0),
                })
        return ket_qua


def thong_ke() -> dict:
    ds = liet_ke()
    can_dang_nhap = sum(1 for x in ds if x.get("trang_thai") == "can_dang_nhap")
    return {"tong": len(ds), "can_dang_nhap": can_dang_nhap}


async def kiem_mot_phien(module: str, tai_khoan: str, so: Any | None = None) -> dict:
    kiem_tra_id(module, tai_khoan)
    ban_ghi = lay(module, tai_khoan)
    if ban_ghi is None:
        raise KeyError(f"Không tìm thấy phiên {module}/{tai_khoan}")

    p = duong_tep_phien(module, tai_khoan)
    m = None
    if so is not None and hasattr(so, "dang_chay"):
        m = so.dang_chay.get(module)

    # Module chưa chạy -> chưa rõ
    if m is None:
        ban_ghi["trang_thai"] = "chua_ro"
        ban_ghi["luc_kiem"] = kho.bay_gio_iso()
        with _KHOA:
            _ghi_nguyen_tu_0600(p, ban_ghi)
        nhat_ky.ghi("phien", "thong_tin", f"Kiểm phiên {module}/{tai_khoan}: module chưa chạy, trạng thái chua_ro",
                    module=module, tai_khoan=tai_khoan)
        return _che_ban_ghi(module, ban_ghi)

    # Module không có phương thức phien_con_song -> chua_ro
    if not hasattr(m, "phien_con_song") or not callable(getattr(m, "phien_con_song")):
        ban_ghi["trang_thai"] = "chua_ro"
        ban_ghi["luc_kiem"] = kho.bay_gio_iso()
        with _KHOA:
            _ghi_nguyen_tu_0600(p, ban_ghi)
        nhat_ky.ghi("phien", "thong_tin", f"Kiểm phiên {module}/{tai_khoan}: module không có phien_con_song",
                    module=module, tai_khoan=tai_khoan)
        return _che_ban_ghi(module, ban_ghi)

    # Hết hạn -> thẳng can_dang_nhap
    if da_het_han(ban_ghi.get("het_han")):
        truoc_do = ban_ghi.get("trang_thai")
        ban_ghi["trang_thai"] = "can_dang_nhap"
        ban_ghi["luc_kiem"] = kho.bay_gio_iso()
        with _KHOA:
            _ghi_nguyen_tu_0600(p, ban_ghi)
        if truoc_do != "can_dang_nhap":
            nhat_ky.ghi("phien", "canh_bao", f"Phiên {module}/{tai_khoan} hết hạn, cần đăng nhập lại",
                        module=module, tai_khoan=tai_khoan)
        return _che_ban_ghi(module, ban_ghi)

    # Kiểm phiên còn sống
    du_lieu = ban_ghi.get("du_lieu") or {}
    song = False
    try:
        song = bool(await m.phien_con_song(du_lieu))
    except Exception as e:
        log.warning("phien_con_song của %s ném ngoại lệ: %s", module, e)
        song = False

    # Thang xử lý: T0 kiểm lại một lần ngay
    if not song:
        try:
            song = bool(await m.phien_con_song(du_lieu))
        except Exception:
            song = False

    # Vẫn hỏng -> thử lam_moi_phien nếu module có
    if not song and hasattr(m, "lam_moi_phien") and callable(getattr(m, "lam_moi_phien")):
        try:
            moi = await m.lam_moi_phien(du_lieu)
            if isinstance(moi, dict) and moi:
                du_lieu = moi
                ban_ghi["du_lieu"] = copy.deepcopy(moi)
                try:
                    song = bool(await m.phien_con_song(du_lieu))
                except Exception:
                    song = False
        except Exception as e:
            log.warning("lam_moi_phien của %s ném ngoại lệ: %s", module, e)

    ban_ghi["luc_kiem"] = kho.bay_gio_iso()
    if song:
        ban_ghi["trang_thai"] = "con_song"
        ban_ghi["so_lan_loi"] = 0
        nhat_ky.ghi("phien", "thong_tin", f"Kiểm phiên {module}/{tai_khoan}: con_song",
                    module=module, tai_khoan=tai_khoan)
    else:
        truoc_do = ban_ghi.get("trang_thai")
        ban_ghi["trang_thai"] = "can_dang_nhap"
        ban_ghi["so_lan_loi"] = ban_ghi.get("so_lan_loi", 0) + 1
        if truoc_do != "can_dang_nhap":
            nhat_ky.ghi("phien", "canh_bao", f"Phiên {module}/{tai_khoan} cần đăng nhập lại",
                        module=module, tai_khoan=tai_khoan)
        else:
            nhat_ky.ghi("phien", "thong_tin", f"Kiểm phiên {module}/{tai_khoan}: can_dang_nhap",
                        module=module, tai_khoan=tai_khoan)

    with _KHOA:
        _ghi_nguyen_tu_0600(p, ban_ghi)
    return _che_ban_ghi(module, ban_ghi)


def _che_ban_ghi(module: str, ban_ghi: dict) -> dict:
    return {
        "module": module,
        "tai_khoan": ban_ghi.get("tai_khoan"),
        "luc_luu": ban_ghi.get("luc_luu"),
        "luc_kiem": ban_ghi.get("luc_kiem"),
        "het_han": ban_ghi.get("het_han"),
        "trang_thai": ban_ghi.get("trang_thai"),
        "so_lan_loi": ban_ghi.get("so_lan_loi", 0),
    }


async def quet_mot_vong(so: Any) -> dict:
    """Quét một vòng các phiên đã lưu. Bỏ qua phiên vừa kiểm chưa tới 30 phút.

    Mỗi vòng tối đa WEB2API_PHIEN_TOI_DA_MOI_VONG (mặc định 2) phiên để không dồn tải.
    """
    toi_da = toi_da_moi_vong()
    tat_ca = liet_ke()
    bay_gio = datetime.now(timezone.utc)

    ung_vien = []
    for s in tat_ca:
        mod_id = s.get("module")
        if not mod_id:
            continue
        m = getattr(so, "dang_chay", {}).get(mod_id) if hasattr(so, "dang_chay") else None
        # Chỉ quét khi module đang chạy và có phien_con_song
        if m is None or not hasattr(m, "phien_con_song") or not callable(getattr(m, "phien_con_song")):
            continue
        # Phiên đang can_dang_nhap thì KHÔNG kiểm lại nhiều lần
        if s.get("trang_thai") == "can_dang_nhap":
            continue
        # Bỏ qua phiên vừa kiểm chưa tới 30 phút
        luc_kiem = s.get("luc_kiem")
        if luc_kiem:
            try:
                dt_k = datetime.fromisoformat(luc_kiem)
                if dt_k.tzinfo is None:
                    dt_k = dt_k.replace(tzinfo=timezone.utc)
                if (bay_gio - dt_k).total_seconds() < KHOANG_CACH_KIEM_TOI_THIEU_GIAY:
                    continue
            except (ValueError, TypeError):
                pass
        ung_vien.append(s)

    # Ưu tiên phiên chưa từng kiểm (luc_kiem None), rồi đến phiên kiểm lâu nhất
    def _khoa_sap_xep(x):
        lk = x.get("luc_kiem")
        return (lk is not None, lk or "")

    ung_vien.sort(key=_khoa_sap_xep)
    chon = ung_vien[:toi_da]

    da_quet = []
    for s in chon:
        try:
            kq = await kiem_mot_phien(s["module"], s["tai_khoan"], so=so)
            da_quet.append(kq)
        except Exception as e:
            log.warning("Lỗi khi quét phiên %s/%s: %s", s.get("module"), s.get("tai_khoan"), e)

    return {"da_quet": len(da_quet), "danh_sach": da_quet}
