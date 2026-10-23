"""web2api — lõi nhẹ: trang khởi đầu, thông tin máy, sổ module, /v1 chuẩn OpenAI, nguồn module từ git.

Lõi KHÔNG chứa module nào: module có mã được cài lúc chạy vào <WEB2API_DATA>/modules/ (từ git
của dự án) và nạp nóng; module từ xa đăng ký bằng URL. Thêm module không cần dựng lại ảnh
hay khởi động lại tiến trình.

Xác thực: /v1/*, /api/* (kể cả /api/he-thong, /api/modules, /api/tinh-nang) đòi khoá/phiên quản trị;
chỉ còn mở: /health, GET/POST /api/thiet-lap, POST /api/dang-nhap và tệp tĩnh giao diện.
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import random
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from app import che_log, dia_chi, do_tai_nguyen, gioi_han, ham_khoa, he_thong, kenh_vao, kho, khoa_khach, lam_moi_jwt, lam_moi_model, model_bo_sung, nguoi_dung, nhat_ky, phien, phien_web, so_thiet_bi, tai_khoan_hang_loat, tu_lam_moi, tuyen, xac_thuc
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
che_log.cai_dat()  # che khoá/token trong MỌI dòng log hệ thống (lỗi httpx hay chứa URL có token)


async def _vong_dong_bo_khoa():
    while True:
        try:
            await asyncio.sleep(5)
            khoa_khach.dong_bo_dia()
        except asyncio.CancelledError:
            break
        except Exception as e:
            nhat_ky.ghi_canh_bao_bo_qua("he_thong", "Đồng bộ khoá khách", e)


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
    ham_khoa.kiem_tra_khoi_dong()  # kiểm tra khoá chính và cấm khởi động nếu thiếu khoá khi bắt buộc mã hoá
    xac_thuc.khoa_quan_tri()  # sinh tệp khoá ngay lần chạy đầu để người vận hành lấy được
    gioi_han.khoi_dong()  # đọc cấu hình giới hạn sai khoá (WEB2API_RL_*) một lần lúc khởi động
    nguoi_dung.khoi_tao_thiet_lap_lan_dau()  # tạo mã thiết lập lần đầu nếu chưa có admin
    # Sổ và vòng nền tạo TRONG vòng sự kiện đang chạy (Lock/Event/Task gắn với nó).
    so = SoModule()
    app.state.so = so
    await so.khoi_dong()
    app.state.quan_ly_luot_chay = tai_khoan_hang_loat.QuanLyLuotChay()
    ng = NguonGit(so)
    app.state.nguon_git = ng
    ng.bat_dau()
    # NV91-P2: vòng tự làm mới phiên định kỳ (đồng bộ với sổ module mỗi 60s;
    # tắt gọn khi dừng máy — không tác vụ nền mồ côi).
    tu_lam_moi.quan_ly().bat_dau(lambda: so.dang_chay)
    # NV207: vòng tự làm mới JWT (giải mã claim exp, chỉ gọi dịch vụ khi dưới
    # ngưỡng; tắt gọn khi dừng máy).
    lam_moi_jwt.quan_ly().bat_dau(lambda: so.dang_chay)
    vong_model = lam_moi_model.VongLamMoiModel(lambda: so.dang_chay)
    app.state.vong_model = vong_model
    vong_model.bat_dau()
    vong_stb = so_thiet_bi.VongQuetSoThietBi(lambda: app)
    app.state.vong_so_thiet_bi = vong_stb
    vong_stb.bat_dau()
    so_thiet_bi.dang_ky_lenh_so_thiet_bi()
    vong_hv = kenh_vao.VongHoiVong(lambda: app)
    app.state.vong_hoi_vong = vong_hv
    vong_hv.bat_dau()
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
        await tu_lam_moi.quan_ly().dung()
        await tu_lam_moi.huy_tat_ca()
        await lam_moi_jwt.quan_ly().dung()
        await lam_moi_jwt.huy_tat_ca()
        await ng.dung()
        await vong_model.dung()
        await vong_stb.dung()
        await vong_hv.dung()
        await so.dung_het()


app = FastAPI(title="web2api", version=PHIEN_BAN, docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=lifespan)
xac_thuc.dat_nguon_so(lambda: getattr(app.state, "so", None))
khoa_khach.dat_nguon_so(lambda: getattr(app.state, "so", None))
app.add_exception_handler(LoiOpenAI, xu_ly_loi_openai)
cai_bien_loi(app)  # /v1: mọi lỗi khác cũng ra khuôn OpenAI; lỗi chưa bắt không lộ chi tiết/khoá
app.add_middleware(MiddlewareNhatKyCuocGoi)  # /v1: ghi đúng một dòng cuoc_goi, thêm x-request-id và X-Web2api-Module


def la_duong_khoa_khach_duoc_goi(path: str) -> bool:
    """Kiểm tra đường /api/* mà khoá khách được phép đi qua middleware để vào router xác thực riêng.

    Các đường này có cơ chế kiểm tra phạm vi (V1_AUTH) hoặc xác thực riêng ở router:
    - /api/gui/*: cổng gửi ra đích danh (phạm vi gui:<module> kiểm trong router).
    - /api/cong-cu*: công cụ AI chuẩn OpenAI (kiểm tra phạm vi thiet_bi:doc, thiet_bi:ghi).
    - /api/thiet-bi*: thực thể thiết bị (kiểm tra phạm vi thiet_bi:*; riêng rui-ro chặn bằng QT).
    - /api/tim-kiem*: tìm kiếm tri thức và mục lục (loại trừ /api/tim-kiem/nguon là đường quản trị).
    - /api/thiet-lap*: thiết lập ban đầu khi chưa có quản trị.
    - /api/dang-nhap*: đăng nhập bằng mật khẩu người dùng.
    """
    if path.startswith("/api/cong-cu") or path.startswith("/api/thiet-bi") or path.startswith("/api/gui/"):
        return True
    if path.startswith("/api/tim-kiem") and not path.startswith("/api/tim-kiem/nguon"):
        return True
    if path.startswith("/api/thiet-lap") or path.startswith("/api/dang-nhap"):
        return True
    return False


class MiddlewareChanKhoaKhachQuanTri:
    """Chặn khoá khách gọi các đường quản trị (/api/*) và trang quản trị (.dc.html) → 403."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            if ((path.startswith("/api/") and not la_duong_khoa_khach_duoc_goi(path)) or path.endswith(".dc.html")):
                for k, v in scope.get("headers", []):
                    if k.lower() == b"authorization":
                        gia_tri = v.decode("latin-1").strip()
                        loai_auth, _, token = gia_tri.partition(" ")
                        if loai_auth.lower() == "bearer" and token.strip():
                            # Khoá quản trị không bao giờ đi qua tra khoá khách (kho khoá hỏng không được chạm nó).
                            la_qt = hmac.compare_digest(token.strip().encode("utf-8"), xac_thuc.khoa_quan_tri().encode("utf-8"))
                            khach = None if la_qt else khoa_khach.tim_theo_khoa_tho(token.strip())
                            if khach is not None:
                                id_k = str(khach.get("id", "khach"))
                                bam_k = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
                                nhat_ky.ghi(
                                    "quan_tri",
                                    "canh_bao",
                                    f"Từ chối 403 trên đường quản trị: khoá khách ({id_k}) gọi {scope.get('method', '')} {path}",
                                    duong=path,
                                    phuong_thuc=scope.get("method", ""),
                                    nguoi_goi=f"khach:{id_k}",
                                    khoa_van_tay=khoa_khach.van_tay(bam_k),
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


class MiddlewareCanhBaoKhoaQuanTri:
    """Ghi cảnh báo kênh quan_tri khi khoá quản trị được dùng từ IP mới (tối đa 1 lần/IP/24h)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        await self.app(scope, receive, send)

        trang_thai = scope.get("state", {})
        if trang_thai.get("nguoi_goi") == "quan_tri":
            from app.gioi_han import lay_ip_an_toan
            ip = lay_ip_an_toan(Request(scope))
            nhat_ky.ghi_nhan_ip_quan_tri(ip)


class MiddlewareKiemTraHost:
    """Kiểm tra tiêu đề Host chống DNS rebinding.

    Nguyên tắc:
    - Ngoại lệ: /health từ loopback luôn qua.
    - Chấp nhận Host là IP literal (không thể rebinding) HOẶC localhost (*.localhost)
      HOẶC host của địa chỉ công khai đã cấu hình.
    - Nếu đã cấu hình địa chỉ công khai mà Host là tên lạ: trả về HTTP 421 (Misdirected Request).
    - Nếu chưa cấu hình địa chỉ công khai: không chặn (tránh khoá ngoài qua tunnel) nhưng ghi cảnh báo
      nhật ký kênh he_thong một lần.
    - Host rỗng hoặc hỏng (cổng sai, IPv6 sai...): trả về HTTP 400.
    - Áp dụng cho cả HTTP và WebSocket.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        client = scope.get("client")
        client_ip = client[0] if client and len(client) > 0 else ""

        # Ngoại lệ: /health từ loopback luôn qua
        if path == "/health" and self._la_loopback(client_ip):
            await self.app(scope, receive, send)
            return

        # Tìm header Host
        headers = scope.get("headers", [])
        host_ds = [v for k, v in headers if k.lower() == b"host"]
        if len(host_ds) != 1:
            await self._tu_choi(scope, receive, send, 400, "Tiêu đề Host thiếu hoặc không hợp lệ")
            return

        try:
            host_str = host_ds[0].decode("latin-1").strip()
        except UnicodeDecodeError:
            await self._tu_choi(scope, receive, send, 400, "Tiêu đề Host không đúng định dạng")
            return

        if not host_str:
            await self._tu_choi(scope, receive, send, 400, "Tiêu đề Host không được để trống")
            return

        dia_chi_ck = dia_chi.dia_chi_cong_khai_da_cau_hinh()

        auth = dia_chi._chuan_hoa_authority(host_str)
        if auth is None:
            if dia_chi_ck:
                await self._tu_choi(scope, receive, send, 400, "Tiêu đề Host chứa tên máy hoặc cổng không hợp lệ")
                return
            self._ghi_canh_bao_host(host_str)
            await self.app(scope, receive, send)
            return

        try:
            tach = urllib.parse.urlsplit(f"//{auth}")
            ten_may = (tach.hostname or "").lower()
        except Exception:
            if dia_chi_ck:
                await self._tu_choi(scope, receive, send, 400, "Không thể phân tích tiêu đề Host")
                return
            self._ghi_canh_bao_host(host_str)
            await self.app(scope, receive, send)
            return

        # 1. IP literal luôn qua
        if dia_chi.la_ip_literal(ten_may):
            await self.app(scope, receive, send)
            return

        # 2. localhost / *.localhost / testserver luôn qua
        if dia_chi.la_localhost(ten_may):
            await self.app(scope, receive, send)
            return

        # 3. Địa chỉ công khai đã cấu hình
        if dia_chi_ck:
            try:
                host_ck = (urllib.parse.urlsplit(dia_chi_ck).hostname or "").lower()
            except Exception:
                host_ck = ""
            if ten_may == host_ck:
                await self.app(scope, receive, send)
                return
            await self._tu_choi(
                scope,
                receive,
                send,
                421,
                "Tên máy chủ không hợp lệ (Host không khớp địa chỉ công khai đã cấu hình)",
            )
            return

        # 4. Chưa cấu hình địa chỉ công khai: cho qua nhưng ghi cảnh báo nhật ký 1 lần
        self._ghi_canh_bao_host(host_str)
        await self.app(scope, receive, send)

    def _ghi_canh_bao_host(self, host_str: str) -> None:
        state = getattr(app, "state", None)
        if state is not None and not getattr(state, "_da_canh_bao_host", False):
            state._da_canh_bao_host = True
            nhat_ky.ghi(
                "he_thong",
                "canh_bao",
                "Chưa cấu hình địa chỉ công khai (WEB2API_PUBLIC_URL hoặc dia_chi.json) — nguy cơ DNS rebinding khi nhận yêu cầu mang Host lạ",
                host=host_str,
            )

    def _la_loopback(self, ip_str: str) -> bool:
        if not ip_str:
            return False
        try:
            return ipaddress.ip_address(ip_str).is_loopback
        except ValueError:
            return False

    async def _tu_choi(self, scope: Scope, receive: Receive, send: Send, ma_trang_thai: int, thong_diep: str) -> None:
        if scope["type"] == "websocket":
            msg = await receive()
            if msg.get("type") == "websocket.connect":
                await send({"type": "websocket.close", "code": 1008 if ma_trang_thai == 400 else 4421})
            return

        loai = "invalid_request_error"
        ma_loi = "invalid_host" if ma_trang_thai == 400 else "misdirected_request"
        noi_dung = json.dumps(
            than_loi(ma_trang_thai, thong_diep, loai, ma_loi),
            ensure_ascii=False,
        ).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": ma_trang_thai,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(noi_dung)).encode("ascii")),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": noi_dung,
            "more_body": False,
        })


app.add_middleware(MiddlewareCanhBaoKhoaQuanTri)
app.add_middleware(MiddlewareChanKhoaKhachQuanTri)
# /novnc: mọi phản hồi (kể cả lỗi, 405, đường rơi xuống StaticFiles) mang CSP sandbox và header bảo vệ.
app.add_middleware(tuyen.novnc_mod.MiddlewareBaoVeNoVNC)
app.add_middleware(MiddlewareKiemTraHost)
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


# NV261: đo tài nguyên CHỈ khi người dùng bấm (không đo nền, không hẹn giờ). POST vì tốn
# tài nguyên và không idempotent; chỉ khoá quản trị; no-store; giới hạn 1 lần/giây/người dùng
# (mốc tính từ lúc đo XONG, và không cho hai lượt đo của cùng một người chạy đè nhau).
_LAN_DO_CUOI: dict[str, float] = {}
_DANG_DO: set[str] = set()


@app.post("/api/he-thong/do-tai-nguyen", dependencies=tuyen.QT + tuyen.KHONG_LUU)
async def api_do_tai_nguyen(request: Request):
    import time as _time

    nguoi = getattr(request.state, "nguoi_goi", "quan_tri")
    if nguoi in _DANG_DO or _time.monotonic() - _LAN_DO_CUOI.get(nguoi, 0.0) < 1.0:
        return JSONResponse(
            {"loi": "Đo quá dày: mỗi người dùng chỉ được đo 1 lần/giây."},
            status_code=429, headers={"Retry-After": "1", "Cache-Control": "no-store"})
    _DANG_DO.add(nguoi)
    try:
        # Phép đo chạy ~2–5 giây (cửa sổ lấy mẫu CPU + quét RAM): cho vào luồng phụ, không chặn vòng lặp.
        return await asyncio.to_thread(do_tai_nguyen.do_tat_ca, request.app.state.so)
    finally:
        _DANG_DO.discard(nguoi)
        _LAN_DO_CUOI[nguoi] = _time.monotonic()


do_tai_nguyen.bat_tracemalloc_neu_can()  # WEB2API_DO_CHUAN=1 → tracemalloc từ lúc khởi động (chẩn đoán)


app.include_router(tuyen.v1)
app.include_router(tuyen.v1_khong_co)  # NGAY sau v1: /v1 lạ → 404 unknown_endpoint, sai phương thức → 405
app.include_router(tuyen.modules)
app.include_router(lam_moi_model.router)
app.include_router(model_bo_sung.router)
app.include_router(tuyen.tinh_nang)
# Cửa webhook chung: ĐƯỜNG MỞ (nhà cung cấp ngoài gọi vào, xác thực bằng bí mật riêng của module) — KHÔNG gắn vào router có QT.
app.include_router(tuyen.webhook_mod.router)
# Cổng gửi ra đích danh: V1_AUTH (khoá khách có phạm vi gui:<module> được vào) — KHÔNG gắn vào router có QT.
app.include_router(tuyen.gui_ra_mod.router)
app.include_router(tuyen.nguon_git)
app.include_router(tuyen.dia_chi)
app.include_router(tuyen.nhat_ky)
app.include_router(tuyen.khoa_khach)
app.include_router(tuyen.phien)
app.include_router(tuyen.sao_luu)

app.include_router(tuyen.thiet_bi)
app.include_router(so_thiet_bi.router)
app.include_router(kenh_vao.router)
app.include_router(tuyen.phe_duyet)
app.include_router(tuyen.tim_kiem)
app.include_router(tuyen.cong_cu)
app.include_router(tuyen.combo)
app.include_router(tuyen.tep_module)
app.include_router(tuyen.trinh_duyet)
app.include_router(tuyen.novnc)

app.include_router(tuyen.thiet_lap)
app.include_router(tuyen.dang_nhap)
app.include_router(tuyen.dang_xuat)
app.include_router(tuyen.toi)
app.include_router(tuyen.nguoi_dung)
app.include_router(tuyen.danh_tinh)
app.include_router(tuyen.tai_khoan)
app.include_router(tuyen.ma_xac_minh)
app.include_router(tuyen.bao_mat)


@app.get("/")
def trang_khoi_dau():
    # Trang nhỏ tự chọn trang đầu (Studio khi đã có module chat liên kết, không thì Hệ thống → Module).
    return FileResponse(WEB / "index.html", media_type="text/html; charset=utf-8")


# Mount ở gốc PHẢI đứng sau mọi route: Starlette khớp theo thứ tự đăng ký, mount "/"
# nuốt mọi đường đi qua nó. StaticFiles tự chặn đường dẫn thoát khỏi thư mục
# (so commonpath sau khi chuẩn hoá) — test_chan_thoat_khoi_thu_muc_web kiểm điều đó.
app.mount("/", StaticFiles(directory=WEB), name="web")
