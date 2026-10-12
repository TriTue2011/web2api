"""Chuẩn tri thức của lõi (tri_thuc): tìm kiếm, nguồn tri thức, tích hợp quy trình.

Nguyên tắc:
- Lõi không chứa tên công nghệ cụ thể; chuẩn trung lập cho các module tri thức.
- Kết quả tìm kiếm là đầu vào không tin cậy: luôn gắn `nguon_ngoai: true`.
- Lõi không bao giờ tự động chèn kết quả tìm kiếm vào câu lệnh hay chỉ thị hệ thống.
- Sổ nhật ký kênh `tri_thuc`: chuỗi tìm kiếm cắt tối đa 200 ký tự; tuyệt đối KHÔNG ghi tài liệu/nội dung đoạn text.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app import nhat_ky, quy_trinh
from app.bien_loi import che_chuoi
from app.loi_openai import LoiOpenAI
from app.quy_trinh import LoiQuyTrinh
from app.so_module import SoModule

log = logging.getLogger("web2api.tri_thuc")


async def lay_danh_sach_nguon(so: SoModule) -> list[dict[str, Any]]:
    """Liệt kê các module khai khả năng tim_kiem kèm trạng thái, cấu hình ở dạng che."""
    ds = []
    for mid, km in list(so.catalog.items()):
        cung_cap = km.manifest.get("cung_cap", [])
        if "tim_kiem" not in cung_cap:
            continue
        da_cau_hinh = mid in so.dang_chay
        dung_chuan = "tim_kiem" not in getattr(km, "chuan_loi", {})
        if not dung_chuan:
            trang_thai = "loi_chuan"
        elif da_cau_hinh:
            trang_thai = "san_sang"
        else:
            trang_thai = "chua_cau_hinh"

        muc_da_luu = so.muc_da_luu(mid) or {}
        ds.append({
            "id": mid,
            "ten": km.manifest.get("ten", mid),
            "tien_to": km.tien_to,
            "da_cau_hinh": da_cau_hinh,
            "dung_chuan": dung_chuan,
            "trang_thai": trang_thai,
            "cau_hinh": muc_da_luu.get("cau_hinh_cong_khai") or {},
            "so_do_tim_kiem": km.manifest.get("so_do_tim_kiem") or [],
        })
    return ds


async def thuc_hien_tim_kiem(than: dict, so: SoModule) -> dict[str, Any]:
    """Thực hiện tìm kiếm tri thức qua quy trình hoặc phễu mặc định."""
    chuoi = than.get("chuoi")
    chuoi_log = str(chuoi or "")[:200]
    pheu = than.get("pheu")

    try:
        if pheu is not None:
            if not isinstance(pheu, list):
                raise LoiOpenAI(400, "«pheu» phải là danh sách các tầng quy trình", code="invalid_pipeline")
            res = await quy_trinh.chay(pheu, than, so)
        else:
            # Phễu mặc định: truy_hoi -> gop (RRF) -> loc -> cay -> muc_luc (nếu có) -> chi_muc (nếu có) -> cat
            pheu_mac_dinh: list[dict] = [
                {"ten": "truy_hoi", "kha_nang": "tim_kiem"},
                {"ten": "gop", "kha_nang": "gop"},
                {"ten": "loc", "kha_nang": "loc", "tham_so": than.get("loc") or {}},
                {"ten": "cay", "kha_nang": "cay", "tham_so": than.get("cay") or {}},
            ]
            if than.get("muc_luc"):
                pheu_mac_dinh.append({"ten": "muc_luc", "kha_nang": "muc_luc", "tham_so": than["muc_luc"]})
            if than.get("chi_muc"):
                pheu_mac_dinh.append({"ten": "chi_muc", "kha_nang": "chi_muc", "tham_so": than["chi_muc"]})
            pheu_mac_dinh.append({"ten": "cat", "kha_nang": "cat", "tham_so": {"so_luong": than.get("so_luong", 10)}})

            res = await quy_trinh.chay(pheu_mac_dinh, than, so)

        ket_qua = res.get("ket_qua", {})
        bao_cao = res.get("bao_cao", [])
        muc_raw = ket_qua.get("muc", [])
        muc_hop_le: list[dict] = []

        if isinstance(muc_raw, list):
            for item in muc_raw:
                if isinstance(item, dict):
                    m = dict(item)
                    m["nguon_ngoai"] = True
                    if "nguon" not in m:
                        m["nguon"] = "khong_ro"
                    muc_hop_le.append(m)

        # Lấy danh sách mục trước khi cắt để đếm mặt (mat), duyệt cây (cay_nut), chỉ mục A-Z (chi_muc)
        muc_truoc_cat_raw = ket_qua.get("muc_truoc_cat")
        if isinstance(muc_truoc_cat_raw, list):
            muc_truoc_cat = []
            for item in muc_truoc_cat_raw:
                if isinstance(item, dict):
                    m = dict(item)
                    m["nguon_ngoai"] = True
                    muc_truoc_cat.append(m)
        else:
            muc_truoc_cat = muc_hop_le

        # Ghi nhật ký kênh tri_thuc (KHÔNG ghi tài liệu hay nội dung đoạn)
        ms_tung_tang = [{"ten": b.get("ten"), "ms": b.get("ms")} for b in bao_cao if isinstance(b, dict)]
        nhat_ky.ghi(
            "tri_thuc",
            "thong_tin",
            f"Tìm kiếm: {chuoi_log}" if chuoi_log else "Tìm kiếm tri thức",
            chuoi=chuoi_log,
            so_muc=len(muc_hop_le),
            ms_tung_tang=ms_tung_tang,
        )

        phan_hoi: dict[str, Any] = {
            "muc": muc_hop_le,
            "tong": ket_qua.get("tong", len(muc_hop_le)),
            "bao_cao": bao_cao,
        }

        # Đếm theo mặt (facet)
        if isinstance(than.get("mat"), list) and than["mat"]:
            phan_hoi["mat"] = quy_trinh.dem_mat(muc_truoc_cat, than["mat"])
        elif "mat" in ket_qua:
            phan_hoi["mat"] = ket_qua["mat"]

        # Duyệt cây phân cấp (cay_nut)
        if isinstance(than.get("cay"), dict) and than["cay"].get("dem") is True:
            phan_hoi["cay_nut"] = quy_trinh.dem_cay_nut(muc_truoc_cat, than["cay"])
        elif "cay_nut" in ket_qua:
            phan_hoi["cay_nut"] = ket_qua["cay_nut"]

        # Chỉ mục A-Z (chi_muc)
        if isinstance(than.get("chi_muc"), dict):
            if "chi_muc" in ket_qua:
                phan_hoi["chi_muc"] = ket_qua["chi_muc"]
            else:
                phan_hoi["chi_muc"] = quy_trinh.dem_chi_muc(muc_truoc_cat, than["chi_muc"])
        elif "chi_muc" in ket_qua:
            phan_hoi["chi_muc"] = ket_qua["chi_muc"]

        # Sơ đồ bỏ qua theo nguồn (so_do_bo_qua)
        if "so_do_bo_qua" in ket_qua:
            phan_hoi["so_do_bo_qua"] = ket_qua["so_do_bo_qua"]
        elif isinstance(than.get("bat_tat"), dict) and "so_do" in than["bat_tat"]:
            phan_hoi["so_do_bo_qua"] = {}

        return phan_hoi

    except LoiQuyTrinh as e:
        nhat_ky.ghi(
            "tri_thuc",
            "loi",
            f"Lỗi quy trình tại tầng {e.tang_loi}: {e}",
            chuoi=chuoi_log,
            loi=che_chuoi(str(e)),
            tang_loi=e.tang_loi,
        )
        raise
    except Exception as e:
        nhat_ky.ghi(
            "tri_thuc",
            "loi",
            f"Lỗi tìm kiếm: {e}",
            chuoi=chuoi_log,
            loi=che_chuoi(str(e)),
        )
        raise


def _doc_so_nguyen_duong(ten_bien: str, mac_dinh: int) -> int:
    """Đọc biến môi trường là số nguyên dương; sai/thiếu → mặc định (không để giá trị lạ làm lõi không khởi động được)."""
    try:
        n = int(os.environ.get(ten_bien, mac_dinh))
    except (ValueError, TypeError):
        return mac_dinh
    return n if n > 0 else mac_dinh


TRAN_DO_SAU_MUC_LUC = _doc_so_nguyen_duong("WEB2API_MUC_LUC_TRAN_DO_SAU", 6)
TRAN_SO_NUT_MUC_LUC = _doc_so_nguyen_duong("WEB2API_MUC_LUC_TRAN_SO_NUT", 2000)


def _lam_sach_cay_muc_luc(
    cac_muc_raw: Any,
    do_sau_hien_tai: int,
    tran_do_sau: int,
    dem_nut: list[int],
    tran_nut: int,
) -> list[dict]:
    """Kiểm hình dạng cây mục lục: bỏ nút sai không vỡ cây, áp trần độ sâu và trần số nút."""
    if not isinstance(cac_muc_raw, list):
        return []
    ket_qua = []
    for item in cac_muc_raw:
        if dem_nut[0] >= tran_nut:
            break
        if not isinstance(item, dict):
            continue
        m_id = item.get("id")
        tieu_de = item.get("tieu_de")
        if m_id is None or tieu_de is None:
            continue
        m_id_str = str(m_id).strip()
        tieu_de_str = str(tieu_de).strip()
        if not m_id_str or not tieu_de_str:
            continue

        dem_nut[0] += 1
        cap_val = item.get("cap")
        nut_sach: dict[str, Any] = {
            "id": m_id_str,
            "tieu_de": tieu_de_str,
            "cap": int(cap_val) if isinstance(cap_val, (int, float)) else do_sau_hien_tai,
        }
        if "vi_tri" in item and item["vi_tri"] is not None:
            nut_sach["vi_tri"] = item["vi_tri"]

        for k, v in item.items():
            if k not in ("id", "tieu_de", "cap", "vi_tri", "con"):
                nut_sach[k] = v

        con_raw = item.get("con")
        if isinstance(con_raw, list):
            if do_sau_hien_tai < tran_do_sau:
                nut_sach["con"] = _lam_sach_cay_muc_luc(
                    con_raw,
                    do_sau_hien_tai + 1,
                    tran_do_sau,
                    dem_nut,
                    tran_nut,
                )
            else:
                nut_sach["con"] = []
        ket_qua.append(nut_sach)
    return ket_qua


async def lay_muc_luc(than: dict, so: SoModule) -> dict[str, Any]:
    """Lấy cây mục lục phân cấp của tài liệu từ module tri thức."""
    if not isinstance(than, dict):
        raise LoiOpenAI(400, "Thân yêu cầu phải là đối tượng JSON", code="invalid_request")

    nguon = than.get("nguon")
    if not isinstance(nguon, str) or not nguon.strip():
        raise LoiOpenAI(400, "«nguon» phải là chuỗi không rỗng", code="invalid_request")
    nguon = nguon.strip()

    tai_lieu = than.get("tai_lieu")
    if not isinstance(tai_lieu, str) or not tai_lieu.strip():
        raise LoiOpenAI(400, "«tai_lieu» phải là chuỗi không rỗng", code="invalid_request")
    tai_lieu = tai_lieu.strip()

    m = so.dang_chay.get(nguon)
    km = so.catalog.get(nguon)
    if not m or not km:
        raise LoiOpenAI(404, f"Module {nguon!r} không sẵn sàng hoặc không tồn tại", code="module_not_ready")

    if "tim_kiem" in getattr(km, "chuan_loi", {}):
        raise LoiOpenAI(501, f"Khả năng «tim_kiem» của module {nguon!r} không đúng chuẩn", code="capability_not_supported")

    if not callable(getattr(m, "muc_luc", None)):
        raise LoiOpenAI(501, f"Module {nguon!r} không hỗ trợ phương thức «muc_luc»", code="not_implemented")

    try:
        tran_do_sau = int(os.environ.get("WEB2API_MUC_LUC_TRAN_DO_SAU", TRAN_DO_SAU_MUC_LUC))
    except (ValueError, TypeError):
        tran_do_sau = TRAN_DO_SAU_MUC_LUC

    try:
        tran_so_nut = int(os.environ.get("WEB2API_MUC_LUC_TRAN_SO_NUT", TRAN_SO_NUT_MUC_LUC))
    except (ValueError, TypeError):
        tran_so_nut = TRAN_SO_NUT_MUC_LUC

    do_sau_hieu_luc = tran_do_sau
    if "do_sau" in than and than["do_sau"] is not None:
        try:
            do_sau_in = int(than["do_sau"])
        except (ValueError, TypeError):
            raise LoiOpenAI(400, "«do_sau» phải là số nguyên", code="invalid_request")
        if do_sau_in <= 0:
            raise LoiOpenAI(400, "«do_sau» phải là số nguyên dương", code="invalid_request")
        do_sau_hieu_luc = min(do_sau_in, tran_do_sau)

    yc_module = {"tai_lieu": tai_lieu, "do_sau": do_sau_hieu_luc}
    try:
        res = await m.muc_luc(yc_module)
    except Exception as e:
        log.warning("Module %s lỗi khi gọi muc_luc: %s", nguon, e)
        if isinstance(e, LoiOpenAI):
            raise
        raise LoiOpenAI(502, f"Module lỗi khi lấy mục lục: {type(e).__name__}", code="upstream_error") from e

    if not isinstance(res, dict):
        raise LoiOpenAI(502, "Kết quả từ module muc_luc không phải đối tượng JSON", code="upstream_error")

    dem_nut = [0]
    muc_sach = _lam_sach_cay_muc_luc(res.get("muc", []), 1, do_sau_hieu_luc, dem_nut, tran_so_nut)

    phan_hoi: dict[str, Any] = {
        "tai_lieu": str(res.get("tai_lieu") or tai_lieu),
        "muc": muc_sach,
    
        "nguon_ngoai": True,  # tiêu đề đề mục do module/tài liệu cung cấp: đầu vào KHÔNG tin được
    }
    if "tieu_de" in res and res["tieu_de"] is not None:
        phan_hoi["tieu_de"] = str(res["tieu_de"])

    for k, v in res.items():
        if k not in ("tai_lieu", "muc", "tieu_de", "nguon_ngoai"):
            phan_hoi[k] = v

    return phan_hoi
