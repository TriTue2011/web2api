"""Làm mới danh sách model tự động cho các module (NV102-LÕI).

Chạy định kỳ nền và khi cấu hình xong module; ghi nhận trạng thái vào model_so.json.
Lõi không chứa bất kỳ tên dịch vụ cụ thể nào; phát hiện khả năng làm mới bằng cấu trúc
thông qua phương thức tuỳ chọn `lam_moi_model()`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import unicodedata
from typing import Any, Callable

from fastapi import APIRouter, Depends, Request

from app import kho
from app.kham_pha import ID_HOP_LE
from app.loi_openai import LoiOpenAI
from app.xac_thuc import can_quan_tri

log = logging.getLogger("web2api.lam_moi_model")

CHU_KY_MAC_DINH_GIO = 3.0
CHU_KY_TOI_THIEU_GIO = 1.0
CHU_KY_TOI_DA_GIO = 24.0
HET_GIO_MODULE_GIAY = 60.0
TRI_HOAN_DAU_GIAY = 30.0
TOI_DA_MODEL = 500
DO_DAI_TOI_DA_MA = 128
DO_DAI_TOI_DA_NHAN = 80
SO_CHU_KY_VANG = 2

_tac_vu_nen: set[asyncio.Task] = set()


def chu_ky_gio() -> float:
    """Đọc chu kỳ làm mới model từ biến môi trường (giờ); kẹp trong khoảng 1..24."""
    val = os.environ.get("WEB2API_CHU_KY_MODEL_GIO", "").strip()
    if not val:
        return CHU_KY_MAC_DINH_GIO
    try:
        so = float(val)
        if math.isnan(so) or math.isinf(so):
            raise ValueError("Số không hợp lệ")
    except (ValueError, TypeError):
        log.warning(
            "Biến %s mang giá trị không hợp lệ: %s",
            "WEB2API_CHU_KY_MODEL_GIO",
            val[:40],
        )
        return CHU_KY_MAC_DINH_GIO
    return min(CHU_KY_TOI_DA_GIO, max(CHU_KY_TOI_THIEU_GIO, so))


def chuan_hoa_ma_model(ma: Any) -> str | None:
    """Chuẩn hoá mã model: chuỗi 1..128 ký tự, không chứa ký tự điều khiển hay ngắt dòng."""
    if not isinstance(ma, str):
        return None
    s = ma.strip()
    if not (1 <= len(s) <= DO_DAI_TOI_DA_MA):
        return None
    for c in s:
        cat = unicodedata.category(c)
        if cat.startswith("C") or cat in ("Zl", "Zp"):
            return None
    return s


def chuan_hoa_nhan(nhan: Any) -> str | None:
    """Chuẩn hoá nhãn hiển thị: bỏ ký tự điều khiển, cắt còn tối đa 80 ký tự."""
    if not isinstance(nhan, str):
        return None
    s = nhan.strip()
    loc = "".join(c for c in s if not unicodedata.category(c).startswith("C"))
    cat_nhan = loc[:DO_DAI_TOI_DA_NHAN]
    return cat_nhan if cat_nhan else None


def loc_danh_sach_model(ds: Any) -> list[dict]:
    """Lọc danh sách model từ kết quả models() của module, chuẩn hoá và bỏ trùng."""
    if not isinstance(ds, list):
        return []
    ket_qua = []
    da_thay: set[str] = set()
    for it in ds:
        if not isinstance(it, dict):
            continue
        mid = chuan_hoa_ma_model(it.get("id"))
        if mid is None or mid in da_thay:
            continue
        da_thay.add(mid)
        nhan = chuan_hoa_nhan(it.get("nhan"))
        ket_qua.append({"id": mid, "nhan": nhan})

    if len(ket_qua) > TOI_DA_MODEL:
        bi_cat = len(ket_qua) - TOI_DA_MODEL
        log.warning(
            "Danh sách model vượt giới hạn %d, đã cắt %d model",
            TOI_DA_MODEL,
            bi_cat,
        )
        ket_qua = ket_qua[:TOI_DA_MODEL]
    return ket_qua


def _doc_so() -> dict:
    p = kho.thu_muc_du_lieu() / "model_so.json"
    try:
        if not p.is_file():
            return {"phien": 1, "chay_cuoi": None, "modules": {}}
        noi_dung = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(noi_dung, dict):
            return {"phien": 1, "chay_cuoi": None, "modules": {}}
        return {
            "phien": noi_dung.get("phien", 1),
            "chay_cuoi": noi_dung.get("chay_cuoi"),
            "modules": noi_dung.get("modules") if isinstance(noi_dung.get("modules"), dict) else {},
        }
    except Exception as e:
        log.warning("Đọc sổ model thất bại: %s", type(e).__name__)
        return {"phien": 1, "chay_cuoi": None, "modules": {}}


def _ghi_so(du_lieu: dict) -> None:
    p = kho.thu_muc_du_lieu() / "model_so.json"
    kho.ghi_nguyen_tu(
        p,
        json.dumps(du_lieu, ensure_ascii=False, indent=2),
        quyen=0o600,
    )


def cap_nhat_so(id_module: str, ds_sach: list[dict], bay_gio: str | None = None) -> dict:
    """Cập nhật sổ model_so.json cho một module.

    ds_sach rỗng nghĩa là «không có thông tin» (nguồn lỗi nuốt ngoại lệ cũng trả rỗng),
    KHÔNG phải «mọi model biến mất»: không đổi gì, không tăng vang.
    """
    if not ds_sach:
        return {"moi": [], "khong_con": [], "tro_lai": []}

    bay_gio = bay_gio or kho.bay_gio_iso()
    with kho.giu_khoa_kho():
        so = _doc_so()
        modules = so.setdefault("modules", {})
        mod_dict = modules.setdefault(id_module, {})
        moi: list[str] = []
        khong_con: list[str] = []
        tro_lai: list[str] = []
        ds_ids = {m["id"]: m.get("nhan") for m in ds_sach if isinstance(m, dict) and "id" in m}

        for mid, nhan in ds_ids.items():
            muc = mod_dict.get(mid)
            if muc is None:
                mod_dict[mid] = {
                    "nhan": nhan,
                    "thay_dau": bay_gio,
                    "thay_cuoi": bay_gio,
                    "vang": 0,
                    "con": True,
                }
                moi.append(mid)
            else:
                da_tung_mat = not muc.get("con", True)
                muc["vang"] = 0
                muc["con"] = True
                muc["thay_cuoi"] = bay_gio
                if nhan is not None:
                    muc["nhan"] = nhan
                if da_tung_mat:
                    tro_lai.append(mid)

        for mid, muc in mod_dict.items():
            if mid not in ds_ids:
                muc["vang"] = muc.get("vang", 0) + 1
                if muc["vang"] >= SO_CHU_KY_VANG and muc.get("con", True):
                    muc["con"] = False
                    khong_con.append(mid)

        _ghi_so(so)
        return {"moi": moi, "khong_con": khong_con, "tro_lai": tro_lai}


class VongLamMoiModel:
    """Vòng làm mới danh sách model định kỳ nền cho tất cả module đang chạy."""

    def __init__(self, lay_dang_chay: Callable[[], dict], *, ngu: Callable[[float], Any] | None = None):
        self._lay_dang_chay = lay_dang_chay
        self._ngu = ngu or asyncio.sleep
        self._dung_co = False
        self._task: asyncio.Task | None = None
        self._dang_chay_ids: set[str] = set()

    def bat_dau(self) -> None:
        """Tạo tác vụ vòng lặp trong vòng sự kiện đang chạy (idempotent)."""
        if self._task is not None and not self._task.done():
            return
        self._dung_co = False
        self._task = asyncio.get_running_loop().create_task(self._vong_lap())

    async def dung(self) -> None:
        """Dừng vòng lặp và chờ tác vụ kết thúc (idempotent)."""
        self._dung_co = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _vong_lap(self) -> None:
        try:
            await self._ngu(TRI_HOAN_DAU_GIAY)
            while not self._dung_co:
                try:
                    await self.chay_mot_chu_ky()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.warning("Vòng làm mới model chu kỳ lỗi: %s", type(e).__name__)
                chu_ky = chu_ky_gio()
                await self._ngu(chu_ky * 3600.0)
        except asyncio.CancelledError:
            pass

    async def chay_mot_chu_ky(self) -> dict[str, str]:
        """Chạy một chu kỳ làm mới cho mọi module có hỗ trợ phương thức lam_moi_model."""
        dang_chay = self._lay_dang_chay()
        cac_id = [
            mid for mid, m in dang_chay.items()
            if callable(getattr(m, "lam_moi_model", None))
        ]
        if not cac_id:
            return {}

        ket_qua_ds = await asyncio.gather(
            *(self.lam_moi_mot(mid) for mid in cac_id),
            return_exceptions=True,
        )

        ra: dict[str, str] = {}
        for mid, res in zip(cac_id, ket_qua_ds):
            if isinstance(res, Exception):
                log.warning("Module %s làm mới gặp ngoại lệ: %s", mid, type(res).__name__)
                ra[mid] = "loi"
            elif isinstance(res, dict):
                ra[mid] = res.get("ket_qua", "loi")
            else:
                ra[mid] = "loi"

        with kho.giu_khoa_kho():
            so = _doc_so()
            so["chay_cuoi"] = kho.bay_gio_iso()
            cac_hop_le = set(cac_id)
            so["modules"] = {k: v for k, v in so.get("modules", {}).items() if k in cac_hop_le}
            _ghi_so(so)

        return ra

    async def lam_moi_mot(self, id_module: str) -> dict:
        """Thực hiện một lượt làm mới cho một module (single-flight theo module)."""
        if id_module in self._dang_chay_ids:
            return {"ket_qua": "dang_chay", "moi": [], "khong_con": []}

        self._dang_chay_ids.add(id_module)
        try:
            m = self._lay_dang_chay().get(id_module)
            if m is None or not callable(getattr(m, "lam_moi_model", None)):
                return {"ket_qua": "loi", "moi": [], "khong_con": []}

            het_han = HET_GIO_MODULE_GIAY
            try:
                await asyncio.wait_for(m.lam_moi_model(), het_han)
                raw = await asyncio.wait_for(m.models(), het_han)
            except (asyncio.TimeoutError, TimeoutError):
                log.warning("Làm mới danh sách model module %s: het_gio (%s)", id_module, "TimeoutError")
                return {"ket_qua": "het_gio", "moi": [], "khong_con": []}
            except Exception as e:
                log.warning("Làm mới danh sách model module %s: loi (%s)", id_module, type(e).__name__)
                return {"ket_qua": "loi", "moi": [], "khong_con": []}

            ds_sach = loc_danh_sach_model(raw)
            kq = cap_nhat_so(id_module, ds_sach)
            return {
                "ket_qua": "ok",
                "moi": kq["moi"][:50],
                "khong_con": kq["khong_con"][:50],
            }
        finally:
            self._dang_chay_ids.discard(id_module)


def sau_khi_cai(app: Any, id_module: str, ket_qua: dict) -> dict:
    """Tạo tác vụ nền làm mới model ngay sau khi module được cấu hình thành công."""
    try:
        so = getattr(app.state, "so", None)
        if so is None:
            return ket_qua
        m = so.dang_chay.get(id_module)
        if m is not None and callable(getattr(m, "lam_moi_model", None)):
            vong = getattr(app.state, "vong_model", None)
            if vong is None:
                vong = VongLamMoiModel(lambda: so.dang_chay)
            task = asyncio.create_task(vong.lam_moi_mot(id_module))
            _tac_vu_nen.add(task)
            task.add_done_callback(_tac_vu_nen.discard)
            return {**ket_qua, "model_dang_lay": True}
    except Exception as e:
        log.warning("sau_khi_cai thất bại cho %s: %s", id_module, type(e).__name__)
    return ket_qua


router = APIRouter(prefix="/api/modules", dependencies=[Depends(can_quan_tri)])


@router.post("/{id_}/lam-moi-model")
async def lam_moi_model_api(id_: str, request: Request):
    """Kích hoạt làm mới danh sách model của một module và trả kết quả."""
    if not ID_HOP_LE.match(id_):
        raise LoiOpenAI(404, f"Không có module {id_!r}")
    so = getattr(request.app.state, "so", None)
    if so is None or (id_ not in so.catalog and id_ not in so.tu_xa):
        raise LoiOpenAI(404, f"Không có module {id_!r}")
    m = so.dang_chay.get(id_)
    if m is None:
        raise LoiOpenAI(404, f"Module {id_!r} chưa cấu hình", code="module_not_configured")
    if not callable(getattr(m, "lam_moi_model", None)):
        return {"id": id_, "ho_tro": False}

    vong = getattr(request.app.state, "vong_model", None)
    if vong is None:
        vong = VongLamMoiModel(lambda: so.dang_chay)

    try:
        kq = await asyncio.wait_for(vong.lam_moi_mot(id_), timeout=HET_GIO_MODULE_GIAY * 2)
    except (asyncio.TimeoutError, TimeoutError):
        raise LoiOpenAI(504, f"Làm mới danh sách model module {id_!r} quá thời gian", code="upstream_timeout") from None
    except Exception as e:
        raise LoiOpenAI(502, f"Lỗi khi làm mới model: {type(e).__name__}", code="upstream_error") from None

    return {
        "id": id_,
        "ho_tro": True,
        "ket_qua": kq["ket_qua"],
        "moi": kq["moi"],
        "khong_con": kq["khong_con"],
    }


@router.get("/{id_}/model-so")
async def xem_model_so(id_: str, request: Request):
    """Xem danh sách model trong sổ model_so.json cùng chu kỳ làm mới và lần chạy cuối."""
    if not ID_HOP_LE.match(id_):
        raise LoiOpenAI(404, f"Không có module {id_!r}")
    so = getattr(request.app.state, "so", None)
    if so is None or (id_ not in so.catalog and id_ not in so.tu_xa):
        raise LoiOpenAI(404, f"Không có module {id_!r}")

    with kho.giu_khoa_kho():
        so_data = _doc_so()
        mod_dict = so_data.get("modules", {}).get(id_, {})
        chay_cuoi = so_data.get("chay_cuoi")

    models = [
        {
            "id": mid,
            "nhan": muc.get("nhan"),
            "con": muc.get("con", True),
            "vang": muc.get("vang", 0),
            "thay_dau": muc.get("thay_dau"),
            "thay_cuoi": muc.get("thay_cuoi"),
        }
        for mid, muc in mod_dict.items()
    ]
    models.sort(key=lambda x: x["id"])

    return {
        "id": id_,
        "chu_ky_gio": chu_ky_gio(),
        "chay_cuoi": chay_cuoi,
        "models": models,
    }
