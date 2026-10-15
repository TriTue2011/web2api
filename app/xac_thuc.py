"""Xác thực hai lớp của web2api: khoá quản trị và khoá khách có phạm vi (Việc E).

Quy tắc:
- MỘT hàm `nguoi_goi(request)` trả {loai: "quan_tri" | "khach", id: str, pham_vi: list[str]}.
- Đường /api/* và quản trị: chỉ quan_tri (khách gọi → 403, không lộ tồn tại).
- Đường /v1/*: mở cho cả quan_tri và khach; khach bị kiểm soát phạm vi và trần đồng thời
  WEB2API_KHACH_DONG_THOI → 429 + Retry-After (không tính là lỗi provider).
- Khách chỉ được thu hẹp phạm vi (X-Web2api-Scope hoặc X-Scope), không được mở rộng (403),
  không thể sửa khoá hay tạo khoá khác. Tắt/thu hồi → 401 ngay.
- Sổ thao tác: ghi nhận kênh nhật ký `quan_tri` cho các lần 401/403 ở đường quản trị, KHÔNG ghi khoá.

Vì sao bắt buộc ngay từ module đầu tiên: /v1/* tiêu hạn mức của nhà cung cấp AI (và mọi dịch vụ
khác) của chủ máy; mở trần trên LAN thì bất kỳ máy nào trong nhà — kể cả thiết bị IoT
bị chiếm — dùng không giới hạn.

Nguồn khoá: biến `WEB2API_ADMIN_KEY`; không đặt thì sinh một lần (secrets.token_urlsafe(32),
256 bit) và lưu `<WEB2API_DATA>/admin_key` quyền 0600. KHÔNG BAO GIỜ in khoá ra log —
log chỉ nêu đường dẫn tệp để người vận hành `docker exec … cat` lấy.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from typing import Any

from fastapi import Request

from app import gioi_han, ip_cho_phep, kho, khoa_khach, nhat_ky
from app.loi_openai import LoiOpenAI

log = logging.getLogger("web2api.xac_thuc")


def _nguoi_dung_cua_phien(phien: dict[str, Any] | None) -> dict[str, Any] | None:
    """Người dùng ĐANG BẬT của một phiên web; kho người dùng hỏng → None (đóng khi lỗi: 401, không 500)."""
    if phien is None:
        return None
    from app import nguoi_dung
    try:
        nd = nguoi_dung.tim_theo_id(phien.get("nguoi_dung_id", ""))
    except nguoi_dung.LoiKhoNguoiDung:
        return None
    if nd is None or not nd.get("bat", True):
        return None
    return nd

_KHOA_TU_TEP: dict[tuple, str] = {}


def khoa_quan_tri() -> str:
    """Khoá quản trị từ biến WEB2API_ADMIN_KEY hoặc tệp <WEB2API_DATA>/admin_key (0600)."""
    tu_bien = os.environ.get("WEB2API_ADMIN_KEY", "").strip()
    if tu_bien:
        return tu_bien
    p = kho.thu_muc_du_lieu() / "admin_key"
    try:
        st = os.stat(p)
        danh_tinh = (p, st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        khoa = _KHOA_TU_TEP.get(danh_tinh)
        if khoa is None:
            khoa = p.read_text(encoding="utf-8").strip()
            _KHOA_TU_TEP.clear()
            if khoa:
                _KHOA_TU_TEP[danh_tinh] = khoa
    except FileNotFoundError:
        p.parent.mkdir(parents=True, exist_ok=True)
        khoa = secrets.token_urlsafe(32)
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return khoa_quan_tri()
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(khoa + "\n")
        log.warning(
            "Đã sinh khoá quản trị lần đầu, lưu ở %s (quyền 0600). Đọc bằng: docker exec <tên> cat %s",
            p,
            p,
        )
        return khoa
    if not khoa:
        raise RuntimeError(f"{p} rỗng — xoá tệp này để web2api sinh khoá mới, hoặc đặt WEB2API_ADMIN_KEY")
    return khoa


async def nguoi_goi(request: Request) -> dict[str, Any]:
    """Hàm xác thực duy nhất: phân tích header Authorization, trả về thông tin danh tính.

    Kết quả:
      {"loai": "quan_tri", "id": "quan_tri", "pham_vi": ["*"]}
      hoặc
      {"loai": "khach", "id": "<id>", "pham_vi": ["*"] | [mã khả năng]}
    Sai/thiếu khoá → LoiOpenAI(401).
    Khoá khách bị tắt/thu hồi → LoiOpenAI(401).
    Khách yêu cầu mở rộng phạm vi → LoiOpenAI(403 insufficient_scope).
    """
    danh_tinh = getattr(request.state, "danh_tinh", None)
    if isinstance(danh_tinh, dict):
        return danh_tinh

    gioi_han.kiem_tra_truoc_xac_thuc(request)  # IP đang bị khoá vì sai khoá nhiều → 429

    loai_auth, _, gia_tri = request.headers.get("authorization", "").partition(" ")
    gia_tri = gia_tri.strip()

    if request.headers.get("authorization"):
        if loai_auth.lower() != "bearer" or not gia_tri:
            gioi_han.ghi_loi_xac_thuc(request)
            raise LoiOpenAI(
                401,
                "Thiếu hoặc sai khoá xác thực. Gửi header «Authorization: Bearer <khoá>».",
                loai="invalid_request_error",
                code="invalid_api_key",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 1. So khớp khoá quản trị
        if hmac.compare_digest(gia_tri.encode("utf-8"), khoa_quan_tri().encode("utf-8")):
            request.state.nguoi_goi = "quan_tri"
            danh_tinh = {"loai": "quan_tri", "id": "quan_tri", "pham_vi": ["*"]}
            request.state.danh_tinh = danh_tinh
            return danh_tinh

        # 2. So khớp khoá khách. Kho khoá hỏng là lỗi HẠ TẦNG của máy chủ, không phải khách đoán sai: chuỗi có đúng
        # dạng khoá khách → 503, KHÔNG đếm vào giới hạn sai khoá (khách hợp lệ gọi lại không được khoá IP — kéo theo
        # khoá quản trị cùng IP). Chuỗi dạng khác vẫn đếm như cũ: kho hỏng không mở đường dò khoá quản trị.
        try:
            khach = khoa_khach.tim_theo_khoa_tho(gia_tri, nem_khi_kho_hong=True)
        except khoa_khach.LoiKhoKhoaKhach:
            if khoa_khach.DANG_KHOA_THO.fullmatch(gia_tri):
                raise
            khach = None
        if khach is not None:
            if not khach.get("bat", True):
                gioi_han.ghi_loi_xac_thuc(request)
                raise LoiOpenAI(
                    401,
                    "Khoá khách đã bị tắt hoặc thu hồi.",
                    loai="invalid_request_error",
                    code="invalid_api_key",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            id_khach = str(khach.get("id"))
            khoa_khach.ghi_nhan_su_dung(id_khach)

            pham_vi_goc = list(khach.get("pham_vi", ["*"]))
            pham_vi_hieu_luc = pham_vi_goc

            # Khách có thể tự thu hẹp phạm vi thông qua header X-Web2api-Scope hoặc X-Scope
            thu_hep = request.headers.get("x-web2api-scope") or request.headers.get("x-scope")
            if thu_hep:
                cac_yeu_cau = [s.strip() for s in thu_hep.split(",") if s.strip()]
                if cac_yeu_cau:
                    if "*" not in pham_vi_goc:
                        for s in cac_yeu_cau:
                            if s not in pham_vi_goc:
                                raise LoiOpenAI(
                                    403,
                                    f"Không thể mở rộng phạm vi vượt quá phạm vi của khoá (thiếu «{s}»).",
                                    loai="insufficient_scope",
                                    code="insufficient_scope",
                                )
                    pham_vi_hieu_luc = cac_yeu_cau

            request.state.nguoi_goi = f"khach:{id_khach}"
            bam_k = hashlib.sha256(gia_tri.encode("utf-8")).hexdigest()
            vt_k = khoa_khach.van_tay(bam_k)
            danh_tinh = {"loai": "khach", "id": id_khach, "pham_vi": pham_vi_hieu_luc, "khoa_van_tay": vt_k}
            request.state.danh_tinh = danh_tinh
            return danh_tinh

        # Có Authorization nhưng sai -> KHÔNG lùi về cookie (không bypass)
        gioi_han.ghi_loi_xac_thuc(request)
        raise LoiOpenAI(
            401,
            "Thiếu hoặc sai khoá xác thực. Gửi header «Authorization: Bearer <khoá>».",
            loai="invalid_request_error",
            code="invalid_api_key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Nhánh phiên web khi KHÔNG có header Authorization
    from app import phien_web
    phien = phien_web.xac_thuc_cookie(request)
    if phien is not None:
        nd = _nguoi_dung_cua_phien(phien)
        if nd is not None:
            phien_web.kiem_tra_csrf(request, phien)
            nd_id = str(nd.get("id"))
            vai = nd.get("vai", "user")
            if vai == "admin":
                ip_cho_phep.kiem_tra_ip_quan_tri(request)  # phiên admin = quyền quản trị: cùng cổng IP với Bearer admin
            request.state.nguoi_goi = f"nguoi_dung:{nd_id}"
            request.state.phien_web = phien
            request.state.nguoi_dung = nd
            if vai == "admin":
                danh_tinh = {"loai": "quan_tri", "id": f"nguoi_dung:{nd_id}", "pham_vi": ["*"]}
            else:
                danh_tinh = {"loai": "nguoi_dung", "id": f"nguoi_dung:{nd_id}", "pham_vi": []}
            request.state.danh_tinh = danh_tinh
            return danh_tinh

    gioi_han.ghi_loi_xac_thuc(request)
    raise LoiOpenAI(
        401,
        "Thiếu hoặc sai khoá xác thực. Gửi header «Authorization: Bearer <khoá>».",
        loai="invalid_request_error",
        code="invalid_api_key",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def can_phien_web(request: Request) -> dict[str, Any]:
    """Phụ thuộc FastAPI cho các đường tự phục vụ web (/api/toi/*, /api/dang-xuat):
    đòi cookie phiên hợp lệ (cho cả admin và user). Gọi bằng Bearer bị từ chối 401.
    Phiên vai admin từ IP ngoài cổng quản trị → 403 ip_not_allowed (kể cả đăng xuất — cookie đó vô dụng ngoài cổng).
    """
    auth_hdr = request.headers.get("authorization", "").strip()
    if auth_hdr:
        raise LoiOpenAI(
            401,
            "Cần đăng nhập phiên web bằng cookie để thực hiện thao tác này.",
            loai="invalid_request_error",
            code="unauthorized",
        )
    from app import phien_web
    phien = phien_web.xac_thuc_cookie(request)
    if phien is None:
        raise LoiOpenAI(
            401,
            "Cần đăng nhập phiên web để thực hiện thao tác này.",
            loai="invalid_request_error",
            code="unauthorized",
        )
    nd = _nguoi_dung_cua_phien(phien)
    if nd is None:
        raise LoiOpenAI(
            401,
            "Tài khoản không tồn tại hoặc đã bị tắt.",
            loai="invalid_request_error",
            code="unauthorized",
        )
    vai = nd.get("vai", "user")
    if vai == "admin":
        # Phiên admin tự phục vụ đúc được khoá «*» và đổi mật khẩu admin — là quyền quản trị, nên chịu CÙNG cổng IP
        # quản trị (WEB2API_QT_IP_CHO_PHEP; loopback trực tiếp vẫn qua). Người dùng thường không bị cổng này.
        ip_cho_phep.kiem_tra_ip_quan_tri(request)
    phien_web.kiem_tra_csrf(request, phien)
    request.state.phien_web = phien
    request.state.nguoi_dung = nd
    nd_id = str(nd.get("id"))
    request.state.nguoi_goi = f"nguoi_dung:{nd_id}"
    danh_tinh = {
        "loai": "quan_tri" if vai == "admin" else "nguoi_dung",
        "id": f"nguoi_dung:{nd_id}",
        "pham_vi": ["*"] if vai == "admin" else [],
    }
    request.state.danh_tinh = danh_tinh
    return danh_tinh


async def can_quan_tri(request: Request) -> dict[str, Any]:
    """Phụ thuộc FastAPI cho các đường quản trị (/api/*): chỉ cho phép quan_tri.

    Khách gọi → 403 (không lộ dữ liệu). Lỗi 401/403 được ghi vào kênh nhật ký `quan_tri`.
    """
    ip_cho_phep.kiem_tra_ip_quan_tri(request)  # cổng IP đường quản trị (cẩm nang §25), trước mọi xác thực
    try:
        nguoi = await nguoi_goi(request)
    except LoiOpenAI as e:
        if e.ma == 401:
            nhat_ky.ghi(
                "quan_tri",
                "canh_bao",
                f"Từ chối 401 trên đường quản trị: {request.method} {request.url.path}",
                duong=request.url.path,
                phuong_thuc=request.method,
            )
        raise

    if nguoi.get("loai") != "quan_tri":
        id_k = nguoi.get("id", "khach")
        vt = nguoi.get("khoa_van_tay")
        nk_kwargs = {
            "duong": request.url.path,
            "phuong_thuc": request.method,
            "nguoi_goi": f"khach:{id_k}",
        }
        if vt:
            nk_kwargs["khoa_van_tay"] = vt
        nhat_ky.ghi(
            "quan_tri",
            "canh_bao",
            f"Từ chối 403 trên đường quản trị: khoá khách ({id_k}) gọi {request.method} {request.url.path}",
            **nk_kwargs,
        )
        raise LoiOpenAI(
            403,
            "Không có quyền truy cập.",
            loai="permission_error",
            code="forbidden",
        )
    return nguoi


async def xac_thuc_v1(request: Request) -> dict[str, Any]:
    """Phụ thuộc FastAPI cho /v1/*: nhận cả quan_tri và khach.

    Khách bị kiểm soát trần đồng thời WEB2API_KHACH_DONG_THOI.
    Loại khác (nguoi_dung) bị 403.
    """
    nguoi = await nguoi_goi(request)
    if nguoi.get("loai") not in ("quan_tri", "khach"):
        raise LoiOpenAI(
            403,
            "Không có quyền truy cập.",
            loai="permission_error",
            code="forbidden",
        )
    if nguoi.get("loai") == "khach":
        id_khach = nguoi.get("id")
        if id_khach and not getattr(request.state, "khach_id", None):
            khoa_khach.vao_dong_thoi(id_khach)
            request.state.khach_id = id_khach
    return nguoi


def kiem_tra_pham_vi(request: Request, *cac_kha_nang: str) -> None:
    """Kiểm tra phạm vi quyền hạn cho đường gọi /v1/*.

    Khách thiếu phạm vi → LoiOpenAI(403 insufficient_scope).
    """
    danh_tinh = getattr(request.state, "danh_tinh", None)
    if not isinstance(danh_tinh, dict):
        return
    if danh_tinh.get("loai") == "quan_tri":
        return
    pham_vi = danh_tinh.get("pham_vi", [])
    if "*" in pham_vi:
        return
    if any(kn in pham_vi for kn in cac_kha_nang):
        return
    can_kn = cac_kha_nang[0] if cac_kha_nang else "chuc_nang"
    raise LoiOpenAI(
        403,
        f"Khoá không có quyền thực hiện thao tác này (cần phạm vi «{can_kn}»).",
        loai="insufficient_scope",
        code="insufficient_scope",
    )
