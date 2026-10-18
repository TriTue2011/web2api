"""Bảng model bổ sung do chủ máy khai bằng DỮ LIỆU (NV102-LÕI).

Dành cho các nhà cung cấp không có nguồn danh sách công khai, cho phép chủ máy
thêm model mới qua thư mục dữ liệu <WEB2API_DATA>/model_bo_sung/ mà không sửa mã.
Quyền thư mục 0700, quyền tệp 0600.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request

from app import kho, nhat_ky
from app.kham_pha import ID_HOP_LE
from app.lam_moi_model import chuan_hoa_ma_model, chuan_hoa_nhan
from app.loi_openai import LoiOpenAI
from app.than_co_tran import THAN_JSON, doc_het
from app.xac_thuc import can_quan_tri, dinh_danh_nguoi_thuc_hien

log = logging.getLogger("web2api.model_bo_sung")

TOI_DA_MUC = 100
TOI_DA_THAN_BYTE = 64 * 1024


def duong_tep(id_module: str) -> Path:
    """Trả về đường dẫn tệp JSON của module; kiểm tra ID_HOP_LE và chống traversal."""
    if not isinstance(id_module, str) or not ID_HOP_LE.match(id_module):
        raise ValueError(f"id module không hợp lệ: {id_module!r}")
    goc = (kho.thu_muc_du_lieu() / "model_bo_sung").resolve()
    p = (goc / f"{id_module}.json").resolve()
    if not p.is_relative_to(goc) or p == goc:
        raise ValueError(f"đường dẫn không an toàn: {id_module!r}")
    return p


def kiem(du_lieu: Any) -> list[dict]:
    """Kiểm tra khuôn nghiêm ngặt của dữ liệu model bổ sung.

    Khuôn: {"models": [{"id": "...", "nhan": "..."}]}
    """
    if not isinstance(du_lieu, dict):
        raise ValueError("Dữ liệu phải là đối tượng JSON")
    if set(du_lieu.keys()) != {"models"}:
        raise ValueError("Dữ liệu chỉ được chứa khoá «models»")
    ds = du_lieu.get("models")
    if not isinstance(ds, list):
        raise ValueError("«models» phải là danh sách")
    if len(ds) > TOI_DA_MUC:
        raise ValueError(f"Số lượng model vượt quá giới hạn {TOI_DA_MUC}")

    ra: list[dict] = []
    da_thay: set[str] = set()

    for idx, it in enumerate(ds):
        if not isinstance(it, dict):
            raise ValueError(f"Mục {idx}: phải là đối tượng")
        khoa_hop_le = {"id", "nhan"}
        if not (set(it.keys()) <= khoa_hop_le and "id" in it):
            raise ValueError(f"Mục {idx}: khoá không hợp lệ hoặc thiếu «id»")

        id_raw = it["id"]
        id_chuan = chuan_hoa_ma_model(id_raw)
        if id_chuan is None or id_chuan != id_raw:
            raise ValueError(f"Mục {idx}: id không hợp lệ")

        if id_chuan in da_thay:
            raise ValueError(f"Mục {idx}: id bị trùng")
        da_thay.add(id_chuan)

        muc: dict[str, Any] = {"id": id_chuan}
        if "nhan" in it and it["nhan"] is not None:
            nhan_raw = it["nhan"]
            if not isinstance(nhan_raw, str):
                raise ValueError(f"Mục {idx}: nhãn phải là chuỗi")
            nhan_chuan = chuan_hoa_nhan(nhan_raw)
            if nhan_chuan is not None:
                muc["nhan"] = nhan_chuan

        ra.append(muc)

    return ra


def doc(id_module: str) -> list[dict]:
    """Đọc danh sách model bổ sung của một module; không bao giờ ném ngoại lệ."""
    try:
        p = duong_tep(id_module)
        if not p.is_file():
            return []
        noi_dung = json.loads(p.read_text(encoding="utf-8"))
        return kiem(noi_dung)
    except Exception as e:
        log.warning("Không đọc được model bổ sung cho %s: %s", id_module, type(e).__name__)
        return []


def ghi(id_module: str, ds: list[dict]) -> list[dict]:
    """Kiểm tra và ghi danh sách model bổ sung nguyên tử xuống đĩa với quyền 0600."""
    ds_sach = kiem({"models": ds})
    p = duong_tep(id_module)
    thu_muc = p.parent
    thu_muc.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(thu_muc, 0o700)
    except OSError:
        pass

    noi_dung = json.dumps({"models": ds_sach}, ensure_ascii=False, indent=2)
    kho.ghi_nguyen_tu(p, noi_dung, quyen=0o600)
    return ds_sach


def _tu_choi_constant(c: str) -> None:
    raise ValueError(f"Giá trị số không hợp lệ: {c}")


async def _doc_than_json(request: Request) -> dict:
    dai = request.headers.get("content-length")
    if dai is not None and dai.strip().isdigit() and int(dai.strip()) > TOI_DA_THAN_BYTE:
        raise LoiOpenAI(413, f"Thân yêu cầu vượt trần {TOI_DA_THAN_BYTE // 1024} KB", code="request_too_large")

    raw = await doc_het(request, THAN_JSON)
    if len(raw) > TOI_DA_THAN_BYTE:
        raise LoiOpenAI(413, f"Thân yêu cầu vượt trần {TOI_DA_THAN_BYTE // 1024} KB", code="request_too_large")

    try:
        than = json.loads(raw, parse_constant=_tu_choi_constant)
    except (ValueError, RecursionError):
        raise LoiOpenAI(400, "Thân yêu cầu không phải JSON hợp lệ", code="invalid_request") from None
    if not isinstance(than, dict):
        raise LoiOpenAI(400, "Thân yêu cầu phải là đối tượng JSON", code="invalid_request")
    return than


router = APIRouter(prefix="/api/modules", dependencies=[Depends(can_quan_tri)])


def _kiem_id_module(id_: str, request: Request) -> None:
    if not isinstance(id_, str) or not ID_HOP_LE.match(id_):
        raise LoiOpenAI(404, f"Không có module {id_!r}")
    so = getattr(request.app.state, "so", None)
    if so is None or (id_ not in so.catalog and id_ not in so.tu_xa):
        raise LoiOpenAI(404, f"Không có module {id_!r}")


@router.get("/{id_}/model-bo-sung")
async def xem_model_bo_sung(id_: str, request: Request):
    """Xem danh sách model bổ sung do chủ máy khai báo cho module."""
    _kiem_id_module(id_, request)
    ds = doc(id_)
    return {"id": id_, "models": ds, "toi_da": TOI_DA_MUC}


@router.put("/{id_}/model-bo-sung")
async def cap_nhat_model_bo_sung(id_: str, request: Request):
    """Cập nhật danh sách model bổ sung cho module (tối đa 100 mục, thân ≤ 64 KB)."""
    _kiem_id_module(id_, request)
    than = await _doc_than_json(request)

    try:
        ds_sach = ghi(id_, than.get("models"))
    except ValueError as e:
        raise LoiOpenAI(400, str(e), code="invalid_request") from None

    nhat_ky.ghi(
        "quan_tri",
        "thong_tin",
        f"Cập nhật model bổ sung cho module {id_}: {len(ds_sach)} mục",
        module=id_,
        nguoi_thuc_hien=dinh_danh_nguoi_thuc_hien(request),
    )

    return {"id": id_, "models": ds_sach, "toi_da": TOI_DA_MUC}
