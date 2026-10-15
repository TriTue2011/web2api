"""web2api — lõi nhẹ: trang khởi đầu, thông tin máy, sổ module, /v1 chuẩn OpenAI, nguồn module từ git.

Lõi KHÔNG chứa module nào: module có mã được cài lúc chạy vào <WEB2API_DATA>/modules/ (từ git
của dự án) và nạp nóng; module từ xa đăng ký bằng URL. Thêm module không cần dựng lại ảnh
hay khởi động lại tiến trình.

Xác thực: /v1/*, /api/* (kể cả /api/he-thong, /api/modules, /api/tinh-nang) đòi khoá/phiên quản trị;
chỉ còn mở: /health, GET/POST /api/thiet-lap, POST /api/dang-nhap và tệp tĩnh giao diện.
"""

import asyncio
import json
import logging
import random
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from app import gioi_han, he_thong, kho, khoa_khach, nguoi_dung, nhat_ky, phien, phien_web, tuyen, xac_thuc
from app.bien_loi import cai_bien_loi
from app.loi_openai import LoiOpenAI, than_loi, xu_ly_loi_openai
from app.nguon_git import NguonGit
from app.nhat_ky import MiddlewareNhatKyCuocGoi
from app.so_module import SoModule
from app.tieu_de_bao_mat import MiddlewareTieuDeBaoMat

GOC = Path(__file__).resolve().parent.parent
WEB = GOC / "web"
PHIEN_BAN = (GOC / "VERSION").read_text().strip()

log = logging.getLogger("web2api")


async def _vong_dong_bo_khoa():
    while True:
        try:
            await asyncio.sleep(5)
            khoa_khach.dong_bo_dia()
        except asyncio.CancelledError:
            break
        except Exception:
            pass


async def _vong_quet_phien(app: FastAPI):
    while True:
        try:
            chu_ky = phien.chu_ky_phut()
            ty_le = random.uniform(0.9, 1.1)
            cho_giay = max(1.0, chu_ky * 60.0 * ty_le)
            await asyncio.sleep(cho_giay)
            so = getattr(app.state, "so", None)
            if so is not None:
                await phien.quet_mot_vong(so)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning("Lỗi trong vòng quét phiên: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    xac_thuc.khoa_quan_tri()  # sinh tệp khoá ngay lần chạy đầu để người vận hành lấy được
    gioi_han.khoi_dong()  # đọc cấu hình giới hạn sai khoá (WEB2API_RL_*) một lần lúc khởi động
    nguoi_dung.khoi_tao_thiet_lap_lan_dau()  # tạo mã thiết lập lần đầu nếu chưa có admin
    # Sổ và vòng nền tạo TRONG vòng sự kiện đang chạy (Lock/Event/Task gắn với nó).
    so = SoModule()
    app.state.so = so
    await so.khoi_dong()
    ng = NguonGit(so)
    app.state.nguon_git = ng
    ng.bat_dau()
    task_dong_bo = asyncio.create_task(_vong_dong_bo_khoa())
    task_quet_phien = asyncio.create_task(_vong_quet_phien(app))
    nhat_ky.ghi("he_thong", "thong_tin", f"Khởi động web2api v{PHIEN_BAN}", phien_ban=PHIEN_BAN)
    nhat_ky.kiem_tra_chuoi_nen()  # kiểm tra chuỗi băm ở luồng nền; hỏng thì ghi 1 dòng cảnh báo
    try:
        yield
    finally:
        task_dong_bo.cancel()
        task_quet_phien.cancel()
        khoa_khach.dong_bo_dia()
        await ng.dung()
        await so.dung_het()


app = FastAPI(title="web2api", version=PHIEN_BAN, docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=lifespan)
app.add_exception_handler(LoiOpenAI, xu_ly_loi_openai)
cai_bien_loi(app)  # /v1: mọi lỗi khác cũng ra khuôn OpenAI; lỗi chưa bắt không lộ chi tiết/khoá
app.add_middleware(MiddlewareNhatKyCuocGoi)  # /v1: ghi đúng một dòng cuoc_goi, thêm x-request-id và X-Web2api-Module


class MiddlewareChanKhoaKhachQuanTri:
    """Chặn khoá khách gọi các đường quản trị (/api/*) và trang quản trị (.dc.html) → 403."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            la_duong_ngoai_le = (
                path in ("/api/tim-kiem", "/api/tim-kiem/", "/api/tim-kiem/muc-luc")
                or path.startswith("/api/thiet-lap")
                or path.startswith("/api/dang-nhap")
            )
            if ((path.startswith("/api/") and not path.startswith("/api/thiet-bi") and not la_duong_ngoai_le) or path.endswith(".dc.html")):
                for k, v in scope.get("headers", []):
                    if k.lower() == b"authorization":
                        gia_tri = v.decode("latin-1").strip()
                        loai_auth, _, token = gia_tri.partition(" ")
                        if loai_auth.lower() == "bearer" and token.strip():
                            khach = khoa_khach.tim_theo_khoa_tho(token.strip())
                            if khach is not None:
                                id_k = str(khach.get("id", "khach"))
                                nhat_ky.ghi(
                                    "quan_tri",
                                    "canh_bao",
                                    f"Từ chối 403 trên đường quản trị: khoá khách ({id_k}) gọi {scope.get('method', '')} {path}",
                                    duong=path,
                                    phuong_thuc=scope.get("method", ""),
                                    nguoi_goi=f"khach:{id_k}",
                                )
                                noi_dung = json.dumps(
                                    than_loi(403, "Không có quyền truy cập.", "permission_error", "forbidden"),
                                    ensure_ascii=False,
                                ).encode("utf-8")
                                await send({
                                    "type": "http.response.start",
                                    "status": 403,
                                    "headers": [
                                        (b"content-type", b"application/json"),
                                        (b"content-length", str(len(noi_dung)).encode("ascii")),
                                    ],
                                })
                                await send({
                                    "type": "http.response.body",
                                    "body": noi_dung,
                                    "more_body": False,
                                })
                                return
                        break
        await self.app(scope, receive, send)


app.add_middleware(MiddlewareChanKhoaKhachQuanTri)
# /novnc: mọi phản hồi (kể cả lỗi, 405, đường rơi xuống StaticFiles) mang CSP sandbox và header bảo vệ.
app.add_middleware(tuyen.novnc_mod.MiddlewareBaoVeNoVNC)
# Ngoài cùng: 3 header bảo vệ cho MỌI phản hồi HTTP; chỉ đặt khi chưa có (không đè /novnc, /m/).
app.add_middleware(MiddlewareTieuDeBaoMat)


def thu_muc_du_lieu() -> str:
    return str(kho.thu_muc_du_lieu())


@app.get("/health")
def health():
    return {"ok": True, "phien_ban": PHIEN_BAN}


@app.get("/api/he-thong", dependencies=tuyen.QT)
def api_he_thong():
    return he_thong.tong_hop(thu_muc_du_lieu(), PHIEN_BAN)


app.include_router(tuyen.v1)
app.include_router(tuyen.v1_khong_co)  # NGAY sau v1: /v1 lạ → 404 unknown_endpoint, sai phương thức → 405
app.include_router(tuyen.modules)
app.include_router(tuyen.tinh_nang)
app.include_router(tuyen.nguon_git)
app.include_router(tuyen.dia_chi)
app.include_router(tuyen.nhat_ky)
app.include_router(tuyen.khoa_khach)
app.include_router(tuyen.phien)
app.include_router(tuyen.sao_luu)

app.include_router(tuyen.thiet_bi)
app.include_router(tuyen.phe_duyet)
app.include_router(tuyen.tim_kiem)
app.include_router(tuyen.combo)
app.include_router(tuyen.tep_module)
app.include_router(tuyen.trinh_duyet)
app.include_router(tuyen.novnc)

app.include_router(tuyen.thiet_lap)
app.include_router(tuyen.dang_nhap)
app.include_router(tuyen.dang_xuat)
app.include_router(tuyen.toi)
app.include_router(tuyen.nguoi_dung)


@app.get("/")
def trang_khoi_dau():
    # Trang nhỏ tự chọn trang đầu (Studio khi đã có module chat liên kết, không thì Hệ thống → Module).
    return FileResponse(WEB / "index.html", media_type="text/html; charset=utf-8")


# Mount ở gốc PHẢI đứng sau mọi route: Starlette khớp theo thứ tự đăng ký, mount "/"
# nuốt mọi đường đi qua nó. StaticFiles tự chặn đường dẫn thoát khỏi thư mục
# (so commonpath sau khi chuẩn hoá) — test_chan_thoat_khoi_thu_muc_web kiểm điều đó.
app.mount("/", StaticFiles(directory=WEB), name="web")
