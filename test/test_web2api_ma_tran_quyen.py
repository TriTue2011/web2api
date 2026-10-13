"""Kiểm thử ma trận quyền tự dựng từ cấu trúc router (Luật bảo mật số 1).

Tự duyệt cây route của FastAPI (app.routes) và các router lồng nhau, phân loại bằng cấu trúc dependency:
- QT (Depends(can_quan_tri)) -> «quản trị»
- V1_AUTH (Depends(xac_thuc_v1)) -> «API khách (theo phạm vi)»
- Không có dependency xác thực -> «mở» (bắt buộc nằm trong bảng ngoại lệ có chủ đích)

Kiểm tra:
1. Mọi route mở nằm trong BANG_NGOAI_LE_MO với lý do rõ ràng.
2. Mọi route quản trị & khách: không khoá -> 401; khoá sai -> 401.
3. Mọi route quản trị: khoá khách gọi -> 403.
4. Mọi route API khách: khoá khách ngoài phạm vi -> 403 (hoặc 200/404 theo thiết kế models).
5. IDOR: ID không tồn tại -> 404/403; không phản chiếu ID độc hại nguyên văn.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import Mount, Route, WebSocketRoute

from app import khoa_khach
from app.main import app
from app.so_module import SoModule
from app.xac_thuc import can_quan_tri, xac_thuc_v1

# ─────────────────────────────────────────────────────────────────────────────
# BẢNG NGOẠI LỆ MỞ CÓ CHỦ ĐÍCH DUY NHẤT (Luật bảo mật số 2)
# Mọi route không gắn phụ thuộc xác thực (QT / V1_AUTH) BẮT BUỘC phải có mặt ở đây.
# Tuyệt đối KHÔNG chứa tên module hoặc tên nhà cung cấp cụ thể.
# ─────────────────────────────────────────────────────────────────────────────
BANG_NGOAI_LE_MO: dict[tuple[str, str], str] = {
    ("GET", "/health"): "Kiểm tra sức khỏe hệ thống (mở cho orchestrator/docker healthcheck, không chứa dữ liệu nhạy cảm)",
    ("GET", "/api/he-thong"): "Thông tin cấu hình máy và phiên bản hệ thống (chỉ đọc, không chứa bí mật hay thông tin nhạy cảm)",
    ("GET", "/api/modules"): "Danh sách các module đã cài đặt (chỉ đọc, không chứa bí mật hay kho mã nguồn)",
    ("GET", "/api/modules/catalog"): "Danh mục module có sẵn từ nguồn cấu hình (chỉ đọc, không chứa bí mật)",
    ("GET", "/api/tinh-nang"): "Danh sách cờ tính năng và khả năng của lõi (chỉ đọc, không chứa bí mật)",
    ("GET", "/m/{id_}/{duong:path}"): "Tệp tĩnh giao diện web của module đã cài (công khai, kiểm tra an toàn đường dẫn)",
    ("GET", "/novnc/dang-nhap"): "Trang nút mở phiên trình duyệt từ xa (chỉ GET form, không script, không tiêu thụ vé)",
    ("POST", "/novnc/dang-nhap"): "Đổi vé sử dụng một lần (60 giây) trong thân form urlencoded lấy phiên noVNC ngắn hạn",
    ("*", "/novnc/s/{phien}/{duong:path}"): "Proxy HTTP noVNC bảo vệ bằng sha256 phiên 256-bit trong URL, CSP sandbox không same-origin",
    ("GET", "/"): "Trang khởi đầu điều hướng giao diện (trả về tệp tĩnh index.html)",
}

# Chuỗi khoá giả nằm trong allowlist của .gitleaks.toml
KHOA_QT = "AIzaSyKIEMNGHIEM-000000000000000000009"
KHOA_SAI = "AIzaSySAI-bon-000000000000000000000004"
KHOA_KHAC = "AIzaSyTOT-mot-000000000000000000000001"


# ─────────────────────────────────────────────────────────────────────────────
# CÔNG CỤ DUYỆT CÂU TRÚC ROUTE VÀ PHỤ THUỘC
# ─────────────────────────────────────────────────────────────────────────────
def thu_thap_routes(target: Any) -> list[APIRoute]:
    """Thu thập toàn bộ HTTP routes từ FastAPI app hoặc router, đệ quy qua các router lồng nhau."""
    cac_route: list[APIRoute] = []
    if not hasattr(target, "routes"):
        if getattr(target, "methods", None) is not None:
            return [target]
        return []
    for r in target.routes:
        # Router lồng nhau trong FastAPI 0.142+ (_IncludedRouter)
        if type(r).__name__ == "_IncludedRouter":
            orig = getattr(r, "original_router", None)
            if orig is not None:
                cac_route.extend(thu_thap_routes(orig))
            continue
        # Bỏ qua Mount tĩnh (ví dụ StaticFiles) hoặc duyệt tiếp nếu mount sub-app
        if isinstance(r, Mount):
            if hasattr(r, "app") and hasattr(r.app, "routes"):
                cac_route.extend(thu_thap_routes(r.app))
            continue
        # Router hoặc danh sách route con
        if hasattr(r, "routes"):
            cac_route.extend(thu_thap_routes(r))
            continue
        # Bỏ qua WebSocket (không có methods)
        if getattr(r, "methods", None) is None or isinstance(r, WebSocketRoute):
            continue
        # Route thông thường
        cac_route.append(r)
    return cac_route


def lay_phu_thuoc(route: Any) -> set[Any]:
    """Duyệt cây phụ thuộc (route.dependant và route.dependencies) để tìm các callable xác thực."""
    callables = set()
    if hasattr(route, "dependencies") and route.dependencies:
        for d in route.dependencies:
            if hasattr(d, "dependency") and d.dependency:
                callables.add(d.dependency)

    def _quet(dep: Any) -> None:
        if dep is None:
            return
        if hasattr(dep, "call") and dep.call:
            callables.add(dep.call)
        for sub in getattr(dep, "dependencies", []):
            _quet(sub)

    if hasattr(route, "dependant"):
        _quet(route.dependant)
    return callables


def phan_loai_route(route: Any) -> str:
    """Phân loại route theo CẤU TRÚC phụ thuộc (không khớp chuỗi đường)."""
    deps = lay_phu_thuoc(route)
    if can_quan_tri in deps:
        return "quan_tri"
    if xac_thuc_v1 in deps:
        return "khach"
    return "mo"


def thay_the_tham_so(duong_mau: str) -> str:
    """Thay thế tham số đường {param} bằng giá trị giả hợp lệ về cú pháp."""
    duong = duong_mau
    duong = duong.replace("{model:path}", "mock/chat-model")
    duong = duong.replace("{duong:path}", "tep-test.txt")
    duong = duong.replace("{id_}", "kk_test123456")
    duong = duong.replace("{id}", "kk_test123456")
    duong = duong.replace("{module}", "mock-module")
    duong = duong.replace("{ho_so}", "0123456789abcdef")  # 16 hex hợp lệ theo MAU_HO_SO
    duong = duong.replace("{ma}", "vid_test123")
    duong = duong.replace("{tai_khoan}", "tk_test")
    duong = duong.replace("{id_thuc_the}", "tb_test")
    duong = duong.replace("{pd_id}", "pd_test123")
    duong = duong.replace("{phien}", "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
    # Thay thế bất kỳ {...} còn lại
    duong = re.sub(r"\{[^}]+\}", "gia-tri-test", duong)
    return duong


def cac_phuong_thuc_chinh(methods: set[str] | None) -> list[str]:
    """Lấy danh sách các phương thức HTTP cần kiểm thử (bỏ HEAD/OPTIONS/TRACE/CONNECT khi đã có GET/POST)."""
    if not methods:
        return ["GET"]
    chinh = [m for m in ("GET", "POST", "PUT", "DELETE", "PATCH") if m in methods]
    return chinh if chinh else list(methods)[:1]


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURE KHỞI TẠO MÔI TRƯỜNG
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    # Bắt buộc tắt rate limit sai khoá để tránh 429 trong bộ kiểm ma trận
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")

    # Tạo một khoá khách có phạm vi hẹp để kiểm thử phân quyền
    ban_ghi, khoa_khach_tho = khoa_khach.tao_khoa(
        nhan="Khách test phạm vi hẹp",
        pham_vi=["pham_vi_hep_khong_trung_khop"],
    )

    with TestClient(app) as client:
        client.app.state.so = SoModule()
        yield client, khoa_khach_tho, ban_ghi


# ─────────────────────────────────────────────────────────────────────────────
# TEST CASES
# ─────────────────────────────────────────────────────────────────────────────

def test_01_thong_ke_va_tu_bao_so_route():
    """Tự báo số route đã kiểm theo từng loại (assert >= 1 mỗi loại) và in danh sách bị bỏ qua."""
    routes = thu_thap_routes(app)
    quan_tri: list[str] = []
    khach: list[str] = []
    mo: list[str] = []

    for r in routes:
        loai = phan_loai_route(r)
        if loai == "quan_tri":
            quan_tri.append(r.path)
        elif loai == "khach":
            khach.append(r.path)
        else:
            mo.append(r.path)

    print("\n=== THỐNG KÊ MA TRẬN QUYỀN ===")
    print(f"- Số route Quản trị (QT): {len(quan_tri)}")
    print(f"- Số route API khách (V1_AUTH): {len(khach)}")
    print(f"- Số route Mở (không xác thực): {len(mo)}")
    print(f"- Tổng số route HTTP đã duyệt: {len(routes)}")

    print("\n=== DANH SÁCH BỎ QUA CÓ CHỦ ĐÍCH ===")
    print("- /novnc/s/{phien}/websockify (Loại: WebSocketRoute, lý do: kết nối nhị phân hai chiều riêng)")
    print("- Mount '/' -> StaticFiles (Loại: Mount, lý do: giao diện tĩnh HTML/JS/CSS không phải API)")

    assert len(quan_tri) >= 1, "Phải có ít nhất 1 route quản trị được kiểm"
    assert len(khach) >= 1, "Phải có ít nhất 1 route API khách được kiểm"
    assert len(mo) >= 1, "Phải có ít nhất 1 route mở được kiểm"


def test_02_moi_route_mo_phai_nam_trong_bang_ngoai_le():
    """Mọi route mở không có xác thực BẮT BUỘC phải nằm trong bảng ngoại lệ BANG_NGOAI_LE_MO."""
    routes = thu_thap_routes(app)
    cac_route_mo = [r for r in routes if phan_loai_route(r) == "mo"]

    for r in cac_route_mo:
        for m in cac_phuong_thuc_chinh(r.methods):
            khop = (m, r.path) in BANG_NGOAI_LE_MO or ("*", r.path) in BANG_NGOAI_LE_MO
            assert khop, (
                f"Phát hiện route MỞ chưa được phép: {m} {r.path}. "
                "Mọi route mở phải có lý do an toàn trong BANG_NGOAI_LE_MO!"
            )


def test_03_ma_tran_khong_khoa_tra_401(moi_truong):
    """Mọi route quản trị và API khách: không gửi khoá -> 401 Unauthorized (xác thực chạy trước khi đọc thân)."""
    client, _, _ = moi_truong
    routes = thu_thap_routes(app)
    cac_route_can_bao_ve = [r for r in routes if phan_loai_route(r) in ("quan_tri", "khach")]

    for r in cac_route_can_bao_ve:
        duong = thay_the_tham_so(r.path)
        for m in cac_phuong_thuc_chinh(r.methods):
            res = client.request(m, duong, json={})
            assert res.status_code == 401, (
                f"Route {m} {r.path} khi không có khoá trả {res.status_code} thay vì 401! "
                f"Nội dung: {res.text}"
            )


def test_04_ma_tran_khoa_sai_tra_401(moi_truong):
    """Mọi route quản trị và API khách: gửi khoá sai -> 401 Unauthorized."""
    client, _, _ = moi_truong
    routes = thu_thap_routes(app)
    cac_route_can_bao_ve = [r for r in routes if phan_loai_route(r) in ("quan_tri", "khach")]
    headers_sai = {"Authorization": f"Bearer {KHOA_SAI}"}

    for r in cac_route_can_bao_ve:
        duong = thay_the_tham_so(r.path)
        for m in cac_phuong_thuc_chinh(r.methods):
            res = client.request(m, duong, headers=headers_sai, json={})
            assert res.status_code == 401, (
                f"Route {m} {r.path} khi khoá sai trả {res.status_code} thay vì 401! "
                f"Nội dung: {res.text}"
            )


def test_05_ma_tran_khoa_khach_goi_quan_tri_tra_403(moi_truong):
    """Mọi route quản trị (QT): khoá khách phạm vi hẹp gọi -> 403 Forbidden (không lộ dữ liệu quản trị)."""
    client, khoa_khach_tho, _ = moi_truong
    routes = thu_thap_routes(app)
    cac_route_qt = [r for r in routes if phan_loai_route(r) == "quan_tri"]
    headers_khach = {"Authorization": f"Bearer {khoa_khach_tho}"}

    for r in cac_route_qt:
        duong = thay_the_tham_so(r.path)
        for m in cac_phuong_thuc_chinh(r.methods):
            res = client.request(m, duong, headers=headers_khach, json={})
            assert res.status_code == 403, (
                f"Route quản trị {m} {r.path} khi khoá khách gọi trả {res.status_code} thay vì 403! "
                f"Nội dung: {res.text}"
            )


def test_06_ma_tran_khoa_khach_goi_api_khach_ngoai_pham_vi(moi_truong):
    """Mọi route API khách: khoá khách phạm vi hẹp gọi -> 403 Forbidden (hoặc 200/404 theo thiết kế models)."""
    client, khoa_khach_tho, _ = moi_truong
    routes = thu_thap_routes(app)
    cac_route_khach = [r for r in routes if phan_loai_route(r) == "khach"]
    headers_khach = {"Authorization": f"Bearer {khoa_khach_tho}"}

    for r in cac_route_khach:
        duong = thay_the_tham_so(r.path)
        for m in cac_phuong_thuc_chinh(r.methods):
            res = client.request(m, duong, headers=headers_khach, json={})
            # Xử lý các route đặc thù theo chuẩn OpenAI của lõi:
            if r.path == "/v1/models":
                # GET /v1/models trả 200 danh sách rỗng (lọc theo phạm vi khách, không trả 403)
                assert res.status_code == 200, f"GET /v1/models mong đợi 200 lọc, nhận {res.status_code}"
            elif r.path == "/v1/models/{model:path}":
                # GET /v1/models/{model} trả 404 (giấu model ngoài phạm vi để không lộ sự tồn tại)
                assert res.status_code == 404, f"GET /v1/models/{'{model:path}'} mong đợi 404, nhận {res.status_code}"
            elif r.path in ("/v1", "/v1/{duong:path}"):
                # Catch-all v1_khong_co trả 404 unknown_endpoint hoặc 405
                assert res.status_code in (404, 405), f"{r.path} mong đợi 404/405, nhận {res.status_code}"
            else:
                # Mọi endpoint API khách có kiểm soát khả năng (kiem_tra_pham_vi) phải trả 403
                assert res.status_code == 403, (
                    f"Route API khách {m} {r.path} khi thiếu phạm vi mong đợi 403, nhận {res.status_code}! "
                    f"Nội dung: {res.text}"
                )


# ─────────────────────────────────────────────────────────────────────────────
# KIỂM THỬ IDOR (INSECURE DIRECT OBJECT REFERENCE)
# ─────────────────────────────────────────────────────────────────────────────

def test_07_idor_quan_tri_id_khong_ton_tai(moi_truong):
    """Với khoá quản trị: gọi route có ID không tồn tại -> 404 Not Found (không phải 500)."""
    client, _, _ = moi_truong
    headers_qt = {"Authorization": f"Bearer {KHOA_QT}"}

    # 1. Quản lý khoá khách: PATCH /api/khoa-khach/{id_}
    r1 = client.patch("/api/khoa-khach/kk_khongtontai999", headers=headers_qt, json={"nhan": "Sửa"})
    assert r1.status_code == 404, f"PATCH /api/khoa-khach/{'{id}'} mong đợi 404, nhận {r1.status_code}"

    # 2. Phê duyệt: POST /api/phe-duyet/{pd_id}/duyet
    r2 = client.post("/api/phe-duyet/pd_khongtontai999/duyet", headers=headers_qt)
    assert r2.status_code == 404, f"POST /api/phe-duyet/{'{pd_id}'}/duyet mong đợi 404, nhận {r2.status_code}"

    # 3. Trình duyệt: POST /api/trinh-duyet/{module}/{ho_so}/ve
    r3 = client.post("/api/trinh-duyet/mod-khong-ton-tai/0123456789abcdef/ve", headers=headers_qt)
    assert r3.status_code == 404, f"POST /api/trinh-duyet/.../ve mong đợi 404, nhận {r3.status_code}"

    # 4. Quản lý phiên: DELETE /api/phien/{module}/{tai_khoan}
    r4 = client.delete("/api/phien/mod-khong-ton-tai/tk-khong-ton-tai", headers=headers_qt)
    assert r4.status_code == 404, f"DELETE /api/phien/... mong đợi 404, nhận {r4.status_code}"

    # 5. Chi tiết thiết bị: GET /api/thiet-bi/{id_thuc_the}
    r5 = client.get("/api/thiet-bi/tb-khong-ton-tai", headers=headers_qt)
    assert r5.status_code == 404, f"GET /api/thiet-bi/{'{id}'} mong đợi 404, nhận {r5.status_code}"


@pytest.mark.xfail(
    strict=True,
    reason="DELETE /api/khoa-khach/{id_} hiện trả 200 da_xoa=False thay vì 404 khi ID không tồn tại",
)
def test_08_idor_delete_khoa_khach_khong_ton_tai_tra_404(moi_truong):
    """Phát hiện: DELETE /api/khoa-khach/{id_} khi ID không tồn tại cần trả 404 thay vì 200."""
    client, _, _ = moi_truong
    headers_qt = {"Authorization": f"Bearer {KHOA_QT}"}
    res = client.delete("/api/khoa-khach/kk_khongtontai999", headers=headers_qt)
    assert res.status_code == 404, f"Mong đợi 404 nhưng nhận {res.status_code}"


@pytest.mark.xfail(
    strict=True,
    reason="PATCH /api/khoa-khach/{id_} phản chiếu nguyên văn chuỗi ID dài trong thông báo 404 mà không qua _nhac_lai",
)
def test_09_idor_patch_khoa_khach_khong_phan_chieu_id_dai(moi_truong):
    """Phát hiện: Thông báo lỗi 404 của PATCH /api/khoa-khach/{id_} phản chiếu chuỗi ID dài nguyên văn."""
    client, _, _ = moi_truong
    headers_qt = {"Authorization": f"Bearer {KHOA_QT}"}
    id_dai = "kk_" + "A" * 200
    res = client.patch(f"/api/khoa-khach/{id_dai}", headers=headers_qt, json={"nhan": "Sửa"})
    assert res.status_code == 404
    # Thông báo lỗi bảo mật không được phản chiếu nguyên văn chuỗi dài 200 ký tự (phải cắt <= 120 ký tự)
    assert id_dai not in res.text, "Chuỗi ID dài bị phản chiếu nguyên văn trong thông báo lỗi!"


def test_10_idor_khoa_khach_doi_id_sang_tai_nguyen_khac(moi_truong):
    """Khoá khách đổi ID sang tài nguyên của khoá khác hoặc tài nguyên hệ thống -> 403 Forbidden."""
    client, khoa_khach_tho, ban_ghi_khach = moi_truong
    headers_khach = {"Authorization": f"Bearer {khoa_khach_tho}"}

    # 1. Khách cố sửa khoá khách của người khác (hoặc chính mình) -> 403
    r_patch = client.patch("/api/khoa-khach/kk_nguoikhac123", headers=headers_khach, json={"nhan": "Hack"})
    assert r_patch.status_code == 403

    # 2. Khách cố xoá khoá khách của người khác -> 403
    r_del = client.delete("/api/khoa-khach/kk_nguoikhac123", headers=headers_khach)
    assert r_del.status_code == 403

    # 3. Khách cố cấp vé trình duyệt cho hồ sơ -> 403
    r_ve = client.post("/api/trinh-duyet/mock/0123456789abcdef/ve", headers=headers_khach)
    assert r_ve.status_code == 403

    # 4. Khách cố duyệt yêu cầu phê duyệt -> 403
    r_pd = client.post("/api/phe-duyet/pd_123/duyet", headers=headers_khach)
    assert r_pd.status_code == 403

    # 5. Khách cố xoá phiên tài khoản của module -> 403
    r_phien = client.delete("/api/phien/mock/tk1", headers=headers_khach)
    assert r_phien.status_code == 403
