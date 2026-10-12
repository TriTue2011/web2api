"""Chuẩn tri thức của lõi (tri_thuc): tìm kiếm, nguồn tri thức, tích hợp quy trình.

Nguyên tắc:
- Lõi không chứa tên công nghệ cụ thể; chuẩn trung lập cho các module tri thức.
- Kết quả tìm kiếm là đầu vào không tin cậy: luôn gắn `nguon_ngoai: true`.
- Lõi không bao giờ tự động chèn kết quả tìm kiếm vào câu lệnh hay chỉ thị hệ thống.
- Sổ nhật ký kênh `tri_thuc`: chuỗi tìm kiếm cắt tối đa 200 ký tự; tuyệt đối KHÔNG ghi tài liệu/nội dung đoạn text.
"""

from __future__ import annotations

import logging
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
            # Phễu mặc định: truy_hoi -> gop (RRF) -> loc -> cay -> cat
            pheu_mac_dinh: list[dict] = [
                {"ten": "truy_hoi", "kha_nang": "tim_kiem"},
                {"ten": "gop", "kha_nang": "gop"},
            ]
            if than.get("loc"):
                pheu_mac_dinh.append({"ten": "loc", "kha_nang": "loc", "tham_so": than["loc"]})
            if than.get("cay"):
                pheu_mac_dinh.append({"ten": "cay", "kha_nang": "cay", "tham_so": than["cay"]})
            so_luong = than.get("so_luong", 10)
            pheu_mac_dinh.append({"ten": "cat", "kha_nang": "cat", "tham_so": {"so_luong": so_luong}})

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

        return {
            "muc": muc_hop_le,
            "tong": ket_qua.get("tong", len(muc_hop_le)),
            "bao_cao": bao_cao,
        }

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
