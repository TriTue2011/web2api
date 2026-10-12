"""web2api — lõi nhẹ: trang khởi đầu, thông tin máy, sổ module, /v1 chuẩn OpenAI, nguồn module từ git.

Lõi KHÔNG chứa module nào: module có mã được cài lúc chạy vào <WEB2API_DATA>/modules/ (từ git
của dự án) và nạp nóng; module từ xa đăng ký bằng URL. Thêm module không cần dựng lại ảnh
hay khởi động lại tiến trình.

Xác thực: /v1/* và mọi API đổi trạng thái/lộ cấu hình đòi khoá quản trị (app/xac_thuc.py);
/health, /api/he-thong, GET /api/modules(/catalog), GET /api/tinh-nang và tệp tĩnh để mở (không chứa bí mật).
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import he_thong, kho, nhat_ky, tuyen, xac_thuc
from app.bien_loi import cai_bien_loi
from app.loi_openai import LoiOpenAI, xu_ly_loi_openai
from app.nguon_git import NguonGit
from app.nhat_ky import MiddlewareNhatKyCuocGoi
from app.so_module import SoModule

GOC = Path(__file__).resolve().parent.parent
WEB = GOC / "web"
PHIEN_BAN = (GOC / "VERSION").read_text().strip()

log = logging.getLogger("web2api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    xac_thuc.khoa_quan_tri()  # sinh tệp khoá ngay lần chạy đầu để người vận hành lấy được
    # Sổ và vòng nền tạo TRONG vòng sự kiện đang chạy (Lock/Event/Task gắn với nó).
    so = SoModule()
    app.state.so = so
    await so.khoi_dong()
    ng = NguonGit(so)
    app.state.nguon_git = ng
    ng.bat_dau()
    nhat_ky.ghi("he_thong", "thong_tin", f"Khởi động web2api v{PHIEN_BAN}", phien_ban=PHIEN_BAN)
    try:
        yield
    finally:
        await ng.dung()
        await so.dung_het()


app = FastAPI(title="web2api", version=PHIEN_BAN, docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=lifespan)
app.add_exception_handler(LoiOpenAI, xu_ly_loi_openai)
cai_bien_loi(app)  # /v1: mọi lỗi khác cũng ra khuôn OpenAI; lỗi chưa bắt không lộ chi tiết/khoá
app.add_middleware(MiddlewareNhatKyCuocGoi)  # /v1: ghi đúng một dòng cuoc_goi, thêm x-request-id và X-Web2api-Module


def thu_muc_du_lieu() -> str:
    return str(kho.thu_muc_du_lieu())


@app.get("/health")
def health():
    return {"ok": True, "phien_ban": PHIEN_BAN}


@app.get("/api/he-thong")
def api_he_thong():
    return he_thong.tong_hop(thu_muc_du_lieu(), PHIEN_BAN)


app.include_router(tuyen.v1)
app.include_router(tuyen.v1_khong_co)  # NGAY sau v1: /v1 lạ → 404 unknown_endpoint, sai phương thức → 405
app.include_router(tuyen.modules)
app.include_router(tuyen.tinh_nang)
app.include_router(tuyen.nguon_git)
app.include_router(tuyen.dia_chi)
app.include_router(tuyen.nhat_ky)
app.include_router(tuyen.tep_module)


@app.get("/")
def trang_khoi_dau():
    # Trang nhỏ tự chọn trang đầu (Studio khi đã có module chat liên kết, không thì Hệ thống → Module).
    return FileResponse(WEB / "index.html", media_type="text/html; charset=utf-8")


# Mount ở gốc PHẢI đứng sau mọi route: Starlette khớp theo thứ tự đăng ký, mount "/"
# nuốt mọi đường đi qua nó. StaticFiles tự chặn đường dẫn thoát khỏi thư mục
# (so commonpath sau khi chuẩn hoá) — test_chan_thoat_khoi_thu_muc_web kiểm điều đó.
app.mount("/", StaticFiles(directory=WEB), name="web")
