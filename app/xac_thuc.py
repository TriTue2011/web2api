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
        from app import ngu_canh
        ngu_canh.dat(danh_tinh, nguon="nguoi", pham_vi_hieu_luc=tuple(danh_tinh.get("pham_vi", ())))
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
            from app import ngu_canh
            ngu_canh.dat(danh_tinh, nguon="nguoi", pham_vi_hieu_luc=("*",))
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
                    for s in cac_yeu_cau:
                        if not khoa_khach.pham_vi_nam_trong_tran(s, pham_vi_goc):
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
            from app import ngu_canh
            ngu_canh.dat(danh_tinh, nguon="nguoi", pham_vi_hieu_luc=tuple(pham_vi_hieu_luc))
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
            from app import ngu_canh
            ngu_canh.dat(danh_tinh, nguon="nguoi", pham_vi_hieu_luc=tuple(danh_tinh.get("pham_vi", ())))
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
    from app import ngu_canh
    ngu_canh.dat(danh_tinh, nguon="nguoi", pham_vi_hieu_luc=tuple(danh_tinh.get("pham_vi", ())))
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

    Cả quản trị và khách đều chịu trần đồng thời toàn cục WEB2API_V1_DONG_THOI_TOAN_CUC (mặc định 32).
    Khách bị kiểm soát trần đồng thời theo khoá WEB2API_KHACH_DONG_THOI (mặc định 4).
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
    # 1. Trần đồng thời toàn cục cho /v1 (cả quản trị và khách đều chịu)
    if not getattr(request.state, "v1_dong_thoi", False):
        khoa_khach.vao_dong_thoi_toan_cuc()
        request.state.v1_dong_thoi = True

    # 2. Trần đồng thời theo khoá khách (chỉ khách chịu, quản trị không chịu)
    if nguoi.get("loai") == "khach":
        id_khach = nguoi.get("id")
        if id_khach and not getattr(request.state, "khach_id", None):
            try:
                khoa_khach.vao_dong_thoi(id_khach)
            except Exception:
                khoa_khach.ra_dong_thoi_toan_cuc()
                request.state.v1_dong_thoi = False
                raise
            request.state.khach_id = id_khach
    return nguoi


_NGUON_SO_FN: Any = None
_TRA_MANIFEST_FN: Any = None


def dat_nguon_so(fn: Any) -> None:
    """Điểm tiêm do lõi đăng ký để cung cấp sổ module (SoModule) mà không import ngược app.main."""
    global _NGUON_SO_FN
    _NGUON_SO_FN = fn


def dat_ham_tra_manifest(fn: Any) -> None:
    """Điểm tiêm tra cứu manifest module (dùng khi tuỳ biến hoặc kiểm thử)."""
    global _TRA_MANIFEST_FN
    _TRA_MANIFEST_FN = fn


def lay_manifest_module(mod_id: str) -> dict[str, Any] | None:
    """Tra cứu manifest của module theo id qua điểm tiêm hoặc sổ module."""
    if _TRA_MANIFEST_FN is not None:
        return _TRA_MANIFEST_FN(mod_id)
    if _NGUON_SO_FN is not None:
        so = _NGUON_SO_FN()
        if so is not None:
            km = getattr(so, "catalog", {}).get(mod_id)
            if km is not None:
                return getattr(km, "manifest", None)
            tx = getattr(so, "tu_xa", {}).get(mod_id)
            if isinstance(tx, dict):
                if "cung_cap" in tx:
                    return tx
                m = getattr(so, "dang_chay", {}).get(mod_id)
                kns = [k for k in ("chat", "anh", "anh_sua", "noi", "nghe", "nhung", "xep_hang") if hasattr(m, k)]
                if hasattr(m, "video_tao") or hasattr(m, "video"):
                    kns.append("video")
                return {"id": mod_id, "cung_cap": kns or ["chat", "anh", "noi", "video"], "nguy_hiem": []}
            m = getattr(so, "dang_chay", {}).get(mod_id)
            if m is not None:
                if hasattr(m, "manifest") and isinstance(m.manifest, dict):
                    return m.manifest
                kns = [k for k in ("chat", "anh", "anh_sua", "noi", "nghe", "nhung", "xep_hang") if hasattr(m, k)]
                if hasattr(m, "video_tao") or hasattr(m, "video"):
                    kns.append("video")
                return {"id": mod_id, "cung_cap": kns or ["chat"], "nguy_hiem": []}
    return None


#: Danh mục các khả năng an toàn chuẩn của lõi (chỉ đọc/suy luận, không thay đổi trạng thái ngoài)
KHA_NANG_AN_TOAN_LOI = frozenset({
    "chat",
    "anh",
    "anh_sua",
    "video",
    "video_tao",
    "video_trang_thai",
    "video_noi_dung",
    "noi",
    "nghe",
    "nhung",
    "xep_hang",
    "tim_kiem",
    "thiet_bi:doc",
    "thiet_bi:su_kien_doc",
})


def la_kha_nang_nguy_hiem(kn: str, module: str | None = None, manifest: dict | None = None) -> bool:
    """Kiểm tra xem một khả năng có thuộc nhóm nguy hiểm hay không (nguyên tắc fail-closed)."""
    from app.kham_pha import KHA_NANG_NGUY_HIEM

    # 1. Các nhóm khả năng luôn được coi là nguy hiểm tĩnh
    if kn in KHA_NANG_NGUY_HIEM:
        return True
    if kn.endswith(":ghi"):
        return True
    if kn.startswith("gui:") or kn.startswith("gui_ra:"):
        return True
    if kn.startswith("trinh_duyet:") or kn.startswith("trinh_duyet_"):
        return True
    if kn == "chay_ma" or kn.startswith("chay_ma:"):
        return True

    # 2. Tra cứu manifest nếu chưa có và có chỉ định module
    man = manifest
    if man is None and module:
        try:
            man = lay_manifest_module(module)
        except Exception as e:
            nhat_ky.ghi(
                "he_thong",
                "canh_bao",
                f"Lỗi khi tra catalog xác định mức độ nguy hiểm của khả năng «{kn}» (module={module}): {e}",
                kha_nang=kn,
                module=module,
                loi=str(e),
            )
            return True  # Fail-closed: tra catalog bị lỗi -> coi là nguy hiểm

    # 3. Khi có thông tin manifest của module
    if isinstance(man, dict):
        ds_nguy_hiem = man.get("nguy_hiem")
        if isinstance(ds_nguy_hiem, (list, tuple, set)) and kn in ds_nguy_hiem:
            return True

        cung_cap = man.get("cung_cap")
        if not isinstance(cung_cap, (list, tuple, set)):
            cung_cap = man.get("kha_nang")
        cung_cap_set = set(cung_cap) if isinstance(cung_cap, (list, tuple, set)) else set()

        goc_kn = kn.partition(":")[0]
        co_trong_catalog = (kn in cung_cap_set) or (goc_kn in cung_cap_set)
        if co_trong_catalog:
            return False

        nhat_ky.ghi(
            "he_thong",
            "canh_bao",
            f"Khả năng «{kn}» không có trong catalog của module «{module}»",
            kha_nang=kn,
            module=module,
        )
        return True  # Fail-closed: khả năng lạ không có trong catalog của module

    # 4. Có module nhưng không tìm thấy trong catalog
    if module:
        nhat_ky.ghi(
            "he_thong",
            "canh_bao",
            f"Không tìm thấy module «{module}» trong catalog khi kiểm tra khả năng «{kn}»",
            kha_nang=kn,
            module=module,
        )
        return True  # Fail-closed: module không có trong catalog

    # 5. Kiểm tra khả năng không gắn với module
    if kn in KHA_NANG_AN_TOAN_LOI:
        return False

    nhat_ky.ghi(
        "he_thong",
        "canh_bao",
        f"Khả năng «{kn}» chưa xác định và không có module trong catalog để đối chiếu",
        kha_nang=kn,
    )
    return True  # Fail-closed: khả năng lạ chưa xác định


def khop_pham_vi(pv: str, kn: str, module: str | None = None, manifest: dict | None = None) -> bool:
    """Kiểm tra quyền `pv` có thoả mãn khả năng `kn` trên `module` hay không.

    - `pv == "*"`: chỉ khớp nếu `kn` là AN TOÀN (không nguy hiểm).
    - `pv == <kn>`: khớp cho mọi module (kể cả nguy hiểm vì đã cấp đích danh).
    - `pv == <kn>@<mod>`: chỉ khớp khi module == mod. Nếu module mod đã bị gỡ -> không khớp.
    """
    nguy_hiem = la_kha_nang_nguy_hiem(kn, module=module, manifest=manifest)
    if pv == "*":
        return not nguy_hiem

    pv_kn, co_at, pv_mod = pv.partition("@")
    if co_at:
        if module is not None and pv_mod != module:
            return False
        if not khoa_khach.module_ton_tai(pv_mod):
            return False
        if pv_kn == "*":
            return not nguy_hiem
        return pv_kn == kn or kn.startswith(f"{pv_kn}:")
    else:
        return pv == kn or kn.startswith(f"{pv}:")


def kiem_tra_pham_vi(
    request: Request | None,
    *cac_kha_nang: str,
    module: str | None = None,
    manifest: dict | None = None,
    ngu_canh: Any = None,
    danh_tinh: dict | None = None,
) -> None:
    """Kiểm tra phạm vi quyền hạn cho đường gọi /v1/* và gọi nội bộ.

    MỘT hàm duy nhất kiểm tra phạm vi cho mọi đường API và nội bộ:
    - Hỗ trợ cú pháp mở rộng «<kha_nang>@<module>».
    - Khả năng nguy hiểm (thiet_bi:ghi, trinh_duyet, gui_ra...) đòi hỏi cấp ĐÍCH DANH,
      khoá «*» không còn bao gồm nhóm nguy hiểm.
    - Module bị gỡ sau khi tạo khoá => quyền trên module đó bị đóng (403).
    - Khách thiếu phạm vi → LoiOpenAI(403 insufficient_scope).
    - Thiếu danh tính trong ngữ cảnh gọi nội bộ → LoiOpenAI(403 missing_context).
    """
    if danh_tinh is None and request and hasattr(request, "state"):
        danh_tinh = getattr(request.state, "danh_tinh", None)

    if ngu_canh is not None:
        if danh_tinh is None and hasattr(ngu_canh, "danh_tinh"):
            danh_tinh = ngu_canh.danh_tinh
        pham_vi = getattr(ngu_canh, "pham_vi_hieu_luc", None)
    else:
        pham_vi = None

    if not isinstance(danh_tinh, dict):
        from app import ngu_canh as ngu_canh_mod
        nc = ngu_canh_mod.lay()
        if nc is not None and isinstance(nc.danh_tinh, dict):
            danh_tinh = nc.danh_tinh
            pham_vi = nc.pham_vi_hieu_luc
        else:
            raise LoiOpenAI(
                403,
                "Thiếu danh tính hoặc ngữ cảnh gọi hợp lệ (missing context).",
                loai="permission_error",
                code="missing_context",
            )
    elif pham_vi is None:
        pham_vi = danh_tinh.get("pham_vi", [])

    if danh_tinh.get("loai") in ("quan_tri", "noi_bo"):
        return

    # Kiểm tra xem có quyền nào trong pham_vi khớp với ít nhất một trong cac_kha_nang không
    for kn in cac_kha_nang:
        for pv in pham_vi:
            if khop_pham_vi(pv, kn, module=module, manifest=manifest):
                return

    can_kn = cac_kha_nang[0] if cac_kha_nang else "chuc_nang"
    co_sao = "*" in pham_vi or any(p.split("@")[0] == "*" for p in pham_vi)
    la_nguy_hiem = any(la_kha_nang_nguy_hiem(kn, module=module, manifest=manifest) for kn in cac_kha_nang)
    if co_sao and la_nguy_hiem:
        thong_diep = (
            f"Khoá không có quyền thực hiện thao tác này (khả năng nguy hiểm «{can_kn}» "
            f"cần được cấp quyền đích danh, không bao gồm trong «*»)."
        )
    elif module:
        thong_diep = f"Khoá không có quyền thực hiện thao tác này (cần phạm vi «{can_kn}» hoặc «{can_kn}@{module}»)."
    else:
        thong_diep = f"Khoá không có quyền thực hiện thao tác này (cần phạm vi «{can_kn}»)."

    raise LoiOpenAI(
        403,
        thong_diep,
        loai="insufficient_scope",
        code="insufficient_scope",
    )


def dinh_danh_nguoi_thuc_hien(request: Request | None = None) -> str:
    """Định danh người thực hiện thao tác quản trị (tài khoản/quản trị, KHÔNG phải khoá)."""
    if request is not None and hasattr(request, "state"):
        ng = getattr(request.state, "nguoi_goi", None)
        if ng:
            return str(ng)
        dt = getattr(request.state, "danh_tinh", None)
        if isinstance(dt, dict) and dt.get("id"):
            return str(dt["id"])
    from app import ngu_canh
    nc = ngu_canh.lay()
    if nc is not None and isinstance(nc.danh_tinh, dict) and nc.danh_tinh.get("id"):
        return str(nc.danh_tinh["id"])
    return "quan_tri"


def kiem_tra_step_up(request: Request) -> None:
    """Kiểm tra xác nhận lại (step-up) cho thao tác quản trị nguy hiểm.

    - Gọi bằng Bearer Admin trực tiếp: được miễn (đã gửi khoá bí mật cao nhất trong Authorization header).
    - Gọi bằng phiên web (cookie): bắt buộc phiên phải đã xác nhận lại trong cửa sổ ngắn.
    - Không phiên, chưa xác nhận lại hoặc đã hết hạn: từ chối 403 chung chung (không phân biệt id tồn tại hay không).
    """
    # 1. Chỉ miễn trừ khi danh tính là khoá quản trị Bearer (loai="quan_tri", id="quan_tri")
    danh_tinh = getattr(request.state, "danh_tinh", None) if hasattr(request, "state") else None
    if isinstance(danh_tinh, dict) and danh_tinh.get("loai") == "quan_tri" and danh_tinh.get("id") == "quan_tri":
        return

    # Nếu danh tính chưa có trên request.state (gọi trực tiếp hàm), kiểm tra Authorization header với khoá quản trị
    auth_hdr = request.headers.get("authorization", "").strip() if hasattr(request, "headers") else ""
    loai_auth, _, gia_tri = auth_hdr.partition(" ")
    if loai_auth.lower() == "bearer" and gia_tri.strip():
        if hmac.compare_digest(gia_tri.strip().encode("utf-8"), khoa_quan_tri().encode("utf-8")):
            return

    # 2. Cổng bảo mật fail-closed: nếu không phải Bearer quản trị, bắt buộc có phiên hợp lệ và đã xác nhận lại
    from app import phien_web
    phien = getattr(request.state, "phien_web", None) if hasattr(request, "state") else None
    if phien is None:
        phien = phien_web.xac_thuc_cookie(request)

    if phien is None or not phien_web.la_phien_da_xac_nhan_lai(phien):
        raise LoiOpenAI(
            403,
            "Thao tác quản trị yêu cầu xác nhận lại.",
            loai="permission_error",
            code="step_up_required",
        )

