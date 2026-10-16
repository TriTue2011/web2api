"""Một cổng gọi khả năng module (goi_kha_nang) — Giải quyết SEC-002.

Nguyên tắc:
1. MỘT cổng duy nhất cho mọi lời gọi khả năng tới module:
   - Khả năng phải nằm trong `cung_cap` của module VÀ là phương thức hợp đồng (HOP_DONG_KHA_NANG của app/kham_pha.py).
   - Kiểm tra phạm vi quyền hạn của khả năng đó theo `ngu_canh` (thiếu ngữ cảnh -> 403 missing_context).
   - Với khả năng thiết bị/hành động: đi qua cổng rủi ro + hàng chờ phê duyệt của thiet_bi (thuc_hien_hanh_dong_loi).
   - Ghi sổ kiểm toán kênh `quan_tri` hoặc `thiet_bi` với danh tính và `nguon` do LÕI đóng dấu.
   - `nguon` do người gọi gửi vào bị loại bỏ (người gọi không tự đặt được).
"""

from __future__ import annotations

import logging
from typing import Any

from app import nhat_ky
from app.kham_pha import HOP_DONG_KHA_NANG
from app.loi_openai import LoiOpenAI
from app.ngu_canh import (
    NguCanh,
    dat as dat_ngu_canh,
    khoi_phuc as khoi_phuc_ngu_canh,
    lay as lay_ngu_canh,
)

log = logging.getLogger("web2api.goi_kha_nang")

#: Bảng ánh xạ phương thức / khả năng sang phạm vi quyền hạn yêu cầu
PHAM_VI_YEU_CAU: dict[str, tuple[str, ...]] = {
    "chat": ("chat",),
    "anh": ("anh", "anh_sua"),
    "anh_sua": ("anh_sua",),
    "video": ("video", "video_tao", "video_trang_thai", "video_noi_dung"),
    "video_tao": ("video", "video_tao"),
    "video_trang_thai": ("video", "video_trang_thai"),
    "video_noi_dung": ("video", "video_noi_dung"),
    "noi": ("noi",),
    "nghe": ("nghe",),
    "nhung": ("nhung",),
    "xep_hang": ("xep_hang",),
    "tim_kiem": ("tim_kiem",),
    "muc_luc": ("tim_kiem",),
    "trinh_duyet": ("trinh_duyet",),
    "trinh_duyet_mo": ("trinh_duyet",),
    "trinh_duyet_dong": ("trinh_duyet",),
    "trinh_duyet_ds": ("trinh_duyet",),
    "thiet_bi": ("thiet_bi:ghi",),
    "thuc_hien": ("thiet_bi:ghi",),
    "thuc_the": ("thiet_bi:doc", "thiet_bi:ghi"),
    "trang_thai": ("thiet_bi:doc", "thiet_bi:ghi"),
    "su_kien": ("thiet_bi:su_kien",),
}


async def goi(
    module: Any,
    kha_nang: str,
    *args: Any,
    ngu_canh: NguCanh | None = None,
    **kw: Any,
) -> Any:
    """MỘT cổng duy nhất gọi khả năng trên module (SEC-002).

    Nguyên tắc kiểm tra khả năng:
    - «chat»: Khả năng cốt lõi (hợp đồng đầu app/kham_pha.py), định tuyến theo phương thức `chat` có thật trên thể hiện.
    - Các khả năng chuyên biệt khác («anh», «video», «noi», «nghe», «thiet_bi», «nhung», «xep_hang», «tim_kiem»...):
      Bắt buộc đòi hỏi CẢ khai báo trong `cung_cap` của manifest LẪN phương thức hợp đồng tương ứng trên thể hiện.
    - Người gọi bắt buộc phải có ngữ cảnh hợp lệ (danh tính và phạm vi truy cập).

    Tham số:
    - `module`: Thể hiện module (đối tượng có manifest hoặc catalog).
    - `kha_nang`: Tên khả năng ("chat", "thiet_bi"...) hoặc phương thức hợp đồng ("thuc_hien", "video_tao"...).
    - `*args`: Tham số truyền cho phương thức module.
    - `ngu_canh`: Ngữ cảnh gọi (NguCanh); nếu None sẽ lấy từ NGU_CANH hiện tại.
    - `**kw`: Tham số từ khoá bổ sung. Lưu ý trường `nguon` bị loại bỏ (lõi tự quản lý).
    """
    # Không cho phép người gọi tự đặt trường `nguon`
    kw.pop("nguon", None)

    # 1. Bắt buộc phải có ngữ cảnh gọi (nguyên tắc ĐÓNG KHI LỖI)
    if ngu_canh is None:
        ngu_canh = lay_ngu_canh()
    if ngu_canh is None:
        raise LoiOpenAI(
            403,
            "Thiếu ngữ cảnh gọi hợp lệ (missing context).",
            loai="permission_error",
            code="missing_context",
        )

    # 2. Xác định khả năng và phương thức hợp đồng
    cap: str | None = None
    phuong_thuc: str | None = None

    if kha_nang in HOP_DONG_KHA_NANG:
        cap = kha_nang
        cac_pt = HOP_DONG_KHA_NANG[cap]
        if len(cac_pt) == 1:
            phuong_thuc = cac_pt[0]
        elif cap == "thiet_bi":
            phuong_thuc = kw.pop("phuong_thuc", "thuc_hien")
        else:
            phuong_thuc = kw.pop("phuong_thuc", cac_pt[0])
    else:
        for c, methods in HOP_DONG_KHA_NANG.items():
            if kha_nang in methods:
                cap = c
                phuong_thuc = kha_nang
                break
        if cap is None:
            # Phương thức mở rộng đã chuẩn hoá (như muc_luc thuộc tim_kiem)
            if kha_nang == "muc_luc":
                cap = "tim_kiem"
                phuong_thuc = "muc_luc"
            else:
                raise LoiOpenAI(
                    400,
                    f"Khả năng hoặc phương thức «{kha_nang}» không nằm trong hợp đồng khả năng.",
                    code="invalid_capability",
                )

    # 3. Kiểm tra module cung cấp khả năng và phương thức hợp đồng có thật trên thể hiện
    fn = getattr(module, phuong_thuc, None)
    co_phuong_thuc = callable(fn)

    # Với thiet_bi, có thể hỗ trợ qua thuc_hien hoặc thuc_the
    if cap == "thiet_bi" and not co_phuong_thuc:
        co_phuong_thuc = callable(getattr(module, "thuc_hien", None)) or callable(getattr(module, "thuc_the", None))

    manifest = getattr(module, "manifest", None)
    if not isinstance(manifest, dict):
        manifest = getattr(module, "_manifest", None)
    if not isinstance(manifest, dict) and hasattr(module, "du_lieu") and isinstance(getattr(module, "du_lieu"), dict):
        manifest = getattr(module, "du_lieu")

    if not isinstance(manifest, dict):
        so_obj = kw.get("so") or getattr(module, "so", None)
        if so_obj is None:
            try:
                from app.main import app as main_app
                so_obj = getattr(getattr(main_app, "state", None), "so", None)
            except Exception:
                pass
        if so_obj is not None:
            mod_id = getattr(module, "id", None)
            tien_to = getattr(module, "tien_to", None)
            if hasattr(so_obj, "catalog"):
                for k, km in getattr(so_obj, "catalog", {}).items():
                    if (mod_id and k == mod_id) or (tien_to and getattr(km, "tien_to", None) == tien_to):
                        manifest = getattr(km, "manifest", None)
                        break
            if not isinstance(manifest, dict) and hasattr(so_obj, "tu_xa"):
                for k, tx in getattr(so_obj, "tu_xa", {}).items():
                    if (mod_id and k == mod_id) or (tien_to and isinstance(tx, dict) and tx.get("tien_to") == tien_to):
                        manifest = tx if (isinstance(tx, dict) and "cung_cap" in tx) else {"cung_cap": ["chat", "anh", "noi", "nghe", "video"]}
                        break

    cung_cap: list[str] | None = None
    if isinstance(manifest, dict):
        raw_cc = manifest.get("cung_cap") if "cung_cap" in manifest else manifest.get("kha_nang")
        if isinstance(raw_cc, (list, tuple, set)):
            cung_cap = list(raw_cc)
    elif hasattr(module, "models") and callable(getattr(module, "models")):
        cung_cap = [m for m in ("chat", "anh", "noi", "nghe", "video", "thiet_bi", "tim_kiem", "xep_hang") if callable(getattr(module, m, None))]

    # Hợp đồng kham_pha.py: chat là khả năng cốt lõi (có hàm chat thì luôn được hỗ trợ dù cung_cap không khai).
    # Các khả năng chuyên biệt khác (anh, video, noi, nghe, thiet_bi, xep_hang, tim_kiem...): đòi CẢ phương thức LẪN cung_cap.
    # Nguyên tắc bảo mật (Fail closed): Không biết khai báo (cung_cap is None) -> ĐÓNG (từ chối 501).
    if cap == "chat":
        co_cung_cap = (cung_cap is not None and "chat" in cung_cap) or co_phuong_thuc
    else:
        if cung_cap is None:
            co_cung_cap = False
        else:
            kha_nang_ct = getattr(getattr(module, "_ct", None), "kha_nang", ())
            co_cung_cap = (cap in cung_cap) or (cap in kha_nang_ct) or (cap == "anh" and "anh_sua" in kha_nang_ct)

    if not co_cung_cap:
        raise LoiOpenAI(
            501,
            f"Module không cung cấp khả năng «{cap}».",
            code="capability_not_supported",
        )

    if not co_phuong_thuc:
        raise LoiOpenAI(
            501,
            f"Module thiếu phương thức «{phuong_thuc}» cho khả năng «{cap}».",
            code="capability_not_supported",
        )

    mod_id = getattr(module, "id", None) or (
        manifest.get("id") if isinstance(manifest, dict) else type(module).__name__
    )

    # 5. Kiểm tra phạm vi quyền hạn theo ngữ cảnh gọi
    can_pv = PHAM_VI_YEU_CAU.get(phuong_thuc) or PHAM_VI_YEU_CAU.get(cap) or (cap,)
    danh_tinh = ngu_canh.danh_tinh
    pv_hieu_luc = ngu_canh.pham_vi_hieu_luc

    la_quan_tri = danh_tinh.get("loai") in ("quan_tri", "noi_bo")
    if not la_quan_tri and "*" not in pv_hieu_luc:
        if not any(p in pv_hieu_luc for p in can_pv):
            can_p = can_pv[0] if can_pv else cap
            nhat_ky.ghi(
                "goi_kha_nang",
                "canh_bao",
                f"Từ chối lời gọi khả năng «{cap}» (phương thức «{phuong_thuc}»): thiếu phạm vi «{can_p}»",
                module=mod_id,
                kha_nang=cap,
                phuong_thuc=phuong_thuc,
                nguon=ngu_canh.nguon,
                danh_tinh_loai=danh_tinh.get("loai"),
                danh_tinh_id=danh_tinh.get("id"),
            )
            raise LoiOpenAI(
                403,
                f"Khoá không có quyền thực hiện thao tác này (cần phạm vi «{can_p}»).",
                loai="insufficient_scope",
                code="insufficient_scope",
            )

    # 6. Với khả năng thiết bị / hành động (thiet_bi: thuc_hien): qua cổng lõi chung
    if cap == "thiet_bi" and phuong_thuc == "thuc_hien":
        from app import thiet_bi as thiet_bi_mod

        id_thuc_the = args[0] if len(args) > 0 else kw.pop("id_thuc_the", kw.pop("id", ""))
        hanh_dong = args[1] if len(args) > 1 else kw.pop("hanh_dong", "")
        tham_so = args[2] if len(args) > 2 else kw.pop("tham_so", {})
        xac_nhan = kw.pop("xac_nhan", False)
        ly_do = kw.pop("ly_do", "")
        so = kw.pop("so", None)

        ma, kq = await thiet_bi_mod.thuc_hien_hanh_dong_loi(
            id_thuc_the=id_thuc_the,
            hanh_dong=hanh_dong,
            tham_so=tham_so,
            xac_nhan=xac_nhan,
            ly_do=ly_do,
            ngu_canh=ngu_canh,
            so=so,
            module=module,
        )
        return kq

    # 7. Ghi sổ kiểm toán kênh goi_kha_nang: chỉ ghi hành động ghi/nguy hiểm (không ghi chat/models thường)
    la_hanh_dong_ghi = phuong_thuc in ("anh_sua", "video_tao")
    if la_hanh_dong_ghi:
        nhat_ky.ghi(
            "goi_kha_nang",
            "thong_tin",
            f"Lõi gọi khả năng ghi «{cap}» (phương thức «{phuong_thuc}») trên module {mod_id}",
            module=mod_id,
            kha_nang=cap,
            phuong_thuc=phuong_thuc,
            nguon=ngu_canh.nguon,
            danh_tinh_loai=danh_tinh.get("loai"),
            danh_tinh_id=danh_tinh.get("id"),
        )

    # 8. Thực hiện lời gọi, bảo toàn ContextVar cho luồng thực thi
    token = dat_ngu_canh(
        ngu_canh.danh_tinh,
        nguon=ngu_canh.nguon,
        pham_vi_hieu_luc=ngu_canh.pham_vi_hieu_luc,
    )
    try:
        return await fn(*args, **kw)
    finally:
        khoi_phuc_ngu_canh(token)
