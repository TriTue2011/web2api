"""Máy chủ GIẢ chạy cục bộ cho test web2api: Google (OpenAI-compat), OpenAI từ xa, GitHub.

Chạy bằng uvicorn THẬT trong luồng riêng (không phải TestClient) vì phải chứng minh
những thứ TestClient che mất: stream có bị gom bộ đệm không, nén gzip có được bỏ
đúng không, và tiến trình lõi con (test kiểm nghiệm) gọi sang được qua socket thật.
"""

import asyncio
import base64
import gzip
import hashlib
import json
import socket
import threading
import time

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route


class MayChuThat:
    """Chạy một ứng dụng ASGI bằng uvicorn trong luồng nền, cổng tự chọn."""

    def __init__(self, app, lifespan="off"):
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan=lifespan)
        )
        self._luong = threading.Thread(target=self._server.run, daemon=True)
        self.url = ""

    def __enter__(self):
        self._luong.start()
        han = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > han or not self._luong.is_alive():
                raise RuntimeError("uvicorn giả không khởi động được")
            time.sleep(0.01)
        cong = self._server.servers[0].sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{cong}"
        return self

    def __exit__(self, *_):
        self._server.should_exit = True
        self._luong.join(10)


def cong_trong() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ───────────────────────────── Google giả ─────────────────────────────

MODELS_GOOGLE = [
    {"id": "models/gemini-2.5-flash", "object": "model", "owned_by": "google"},
    {"id": "models/gemini-3-flash-preview", "object": "model", "owned_by": "google"},
]


#: Danh sách model ở API GỐC (/v1beta/models): có `supportedGenerationMethods`. Mặc định chỉ hai model chat
#: (generateContent) — test video tự thêm model predictLongRunning.
MODELS_GOC_MAC_DINH = [
    {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent", "countTokens"]},
    {"name": "models/gemini-3-flash-preview", "supportedGenerationMethods": ["generateContent", "countTokens"]},
]
GOOGLE_THAT = "https://generativelanguage.googleapis.com"
TRANG_THAI_GOOGLE = {400: "INVALID_ARGUMENT", 401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED",
                     404: "NOT_FOUND", 429: "RESOURCE_EXHAUSTED"}


def du_lieu_video(op: str, co: int) -> bytes:
    """Nội dung video giả xác định theo mã thao tác — test so băm sau khi tải qua web2api."""
    mau = f"VIDEO-GIA-{op}-".encode()
    return (mau * (co // len(mau) + 1))[:co]


class GoogleGia:
    """Mô phỏng https://generativelanguage.googleapis.com: đường tương thích OpenAI (/v1beta/openai — chat,
    models, images/generations) và phần API GỐC module dùng cho video (/v1beta/models, :predictLongRunning,
    hỏi thao tác, tải tệp).

    Hành vi theo KHOÁ (Bearer hoặc x-goog-api-key) đặt trong `hanh_vi`: {"kieu": "tot"|"429"|"401"|"400_khoa"|
    "403_vung"|"400_vung"|"500"|"cham", ...}. Khoá lạ → 401. Lỗi 401/400_khoa/500 cố ý NHẮC LẠI khoá trong thân để
    kiểm việc che khoá. «400_khoa» = khoá sai/bị thu hồi như Google thật trả qua đường tương thích OpenAI (đo
    11/10/2026): HTTP 400, MẢNG, status INVALID_ARGUMENT, không có `reason` — riêng việc nhắc lại khoá là của bản giả.
    «404_model» (kèm "models": [tên model không tiền tố], tuỳ chọn "nhac_khoa": True) = khoá thuộc dự án mới bị Google
    trả 404 «no longer available to new users» cho các model đó (đo 12/10/2026), model khác vẫn 200.

    Ảnh/video theo khoá ở `anh` / `video`: {"kieu": "tot"} (mặc định) hoặc {"kieu": "tu_choi", "ma": 403|429|400}.
    GIẢ ĐỊNH, CHƯA ĐO: Google từ chối khoá hạng miễn phí bằng 400/403/429 — chưa có khoá thật để đo dạng lỗi thật;
    thân lỗi giả cũng nhắc lại khoá để kiểm việc che khoá.
    """

    def __init__(self):
        self.hanh_vi: dict[str, dict] = {}
        self.nhat_ky: list[dict] = []
        self.models_loi = False
        self.cho_khuc_2 = threading.Event()
        self.qua_han_cho = False
        # Các khúc của câu trả lời stream (thân yêu cầu chat ghi ở nhat_ky[...]["than"]). Test markdown/ảnh README
        # đặt câu trả lời có in đậm, danh sách…; `cho_khuc_2` chặn giữa khúc đầu và phần còn lại.
        self.khuc_chat: list[str] = ["Một", "Hai"]
        self.url = ""
        self.dat_lai_anh_video()
        self.app = Starlette(
            routes=[
                Route("/v1beta/openai/models", self.models),
                Route("/v1beta/openai/chat/completions", self.chat, methods=["POST"]),
                Route("/v1beta/openai/images/generations", self.tao_anh, methods=["POST"]),
                Route("/v1beta/models", self.models_goc),
                Route("/v1beta/models/{con:path}", self.models_con, methods=["GET", "POST"]),
                Route("/v1beta/files/{ten}", self.tai_tep),
            ]
        )

    def dat_lai_anh_video(self):
        self.anh: dict[str, dict] = {}
        self.video: dict[str, dict] = {}
        self.models_goc_ds: list[dict] = list(MODELS_GOC_MAC_DINH)
        self.models_them: list[dict] = []      # model THÊM vào danh sách của đường tương thích (sau MODELS_GOOGLE)
        self.models_goc_loi = False
        self.thao_tac: dict[str, dict] = {}  # mã thao tác → {khoa, model, con_lai, ket_qua}
        self.so_lan_hoi = 2                    # số lần hỏi còn «chưa xong» trước khi xong
        self.ket_qua_video = "tot"             # "tot" | "loi" | "loc" (bị lọc an toàn, không có mẫu)
        self.co_video = 3 * 1024 * 1024
        self.goc_uri = GOOGLE_THAT             # máy ghi trong URI video Google trả về
        self.chuyen_huong_toi: str | None = None  # gốc máy chủ tệp khác để chuyển hướng tải (khác máy)
        self.cho_khuc_video = threading.Event()
        self.video_cho = False
        self.video_qua_han_cho = False

    @staticmethod
    def _khoa(req: Request) -> str:
        return req.headers.get("x-goog-api-key") or req.headers.get("authorization", "").removeprefix("Bearer ")

    def _ghi(self, req: Request, duong: str, **them) -> str:
        khoa = self._khoa(req)
        self.nhat_ky.append({"duong": duong, "khoa": khoa, "url": str(req.url),
                             "kieu_khoa": "x-goog-api-key" if req.headers.get("x-goog-api-key") else "bearer", **them})
        return khoa

    @staticmethod
    def _tu_choi(ma: int, khoa: str, viec: str) -> Response:
        return JSONResponse({"error": {"code": ma, "message": f"{viec} bị từ chối cho khoá {khoa} (giả định)",
                                       "status": TRANG_THAI_GOOGLE.get(ma, "UNKNOWN")}}, status_code=ma)

    async def _loi_theo_khoa(self, khoa: str) -> Response | None:
        hv = self.hanh_vi.get(khoa, {"kieu": "401"})
        k = hv["kieu"]
        if k == "tot":
            return None
        if k == "404_model":
            return None  # khoá dùng được; chỉ chat với model đặt trước mới 404 (xem `chat`)
        if k == "cham":
            await asyncio.sleep(hv.get("giay", 1.5))
            return None
        if k == "429":
            hd = {"retry-after": str(hv["retry_after"])} if "retry_after" in hv else {}
            return JSONResponse(
                [{"error": {"code": 429, "message": "Resource has been exhausted (e.g. check quota).",
                            "status": "RESOURCE_EXHAUSTED"}}],
                status_code=429, headers=hd)
        if k == "401":
            return JSONResponse(
                {"error": {"code": 401, "message": f"API key not valid: {khoa}", "status": "UNAUTHENTICATED"}},
                status_code=401)
        if k == "400_khoa":
            return JSONResponse(
                [{"error": {"code": 400, "message": f"API key not valid. Please pass a valid API key. ({khoa})",
                            "status": "INVALID_ARGUMENT"}}],
                status_code=400)
        if k in ("403_vung", "400_vung"):
            ma = 403 if k == "403_vung" else 400
            return JSONResponse(
                [{"error": {"code": ma, "message": "User location is not supported for the API use.",
                            "status": "FAILED_PRECONDITION"}}],
                status_code=ma)
        if k == "500":
            return JSONResponse(
                {"error": {"code": 500, "message": f"Internal error, key={khoa}", "status": "INTERNAL"}},
                status_code=500)
        raise AssertionError(k)

    async def models(self, req: Request):
        khoa = self._ghi(req, "models")
        loi = await self._loi_theo_khoa(khoa)
        if loi is not None:
            return loi
        if self.models_loi:
            return JSONResponse({"error": {"message": "backend lỗi", "status": "INTERNAL"}}, status_code=500)
        return JSONResponse({"object": "list", "data": MODELS_GOOGLE + self.models_them})

    async def chat(self, req: Request):
        than = await req.json()
        khoa = self._ghi(req, "chat", than=than)
        loi = await self._loi_theo_khoa(khoa)
        if loi is not None:
            return loi
        model = than.get("model")
        hv = self.hanh_vi.get(khoa, {})
        if hv.get("kieu") == "404_model" and model in hv["models"]:
            # Như Google thật trả (đo 12/10/2026, khoá thuộc dự án mới với gemini-2.5-flash). `nhac_khoa` chỉ để
            # test việc che khoá — thân thật không nhắc khoá.
            tin = (f"This model models/{model} is no longer available to new users. Please update your code to use a "
                   "newer model for the latest features and improvements.")
            if hv.get("nhac_khoa"):
                tin += f" ({khoa})"
            return JSONResponse({"error": {"code": 404, "message": tin, "status": "NOT_FOUND"}}, status_code=404)
        if model == "loi-400":
            return JSONResponse({"error": {"code": 400, "message": "* GenerateContentRequest.model: unexpected model",
                                           "status": "INVALID_ARGUMENT"}}, status_code=400)
        if model == "loi-400-mang":
            return JSONResponse([{"error": {"code": 400, "message": "Request contains an invalid argument.",
                                            "status": "INVALID_ARGUMENT"}}], status_code=400)
        if than.get("stream"):
            cho = self.hanh_vi.get(khoa, {}).get("cho_khuc_2", False)
            khuc = list(self.khuc_chat)

            def dong(c: str) -> str:
                # Gọn, không dấu cách (test_web2api_gemini so đúng từng byte dòng SSE).
                return "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": c}}]}, ensure_ascii=False,
                                             separators=(",", ":")) + "\n\n"

            async def phat():
                yield dong(khuc[0])
                if cho:
                    # Chỉ nhả khúc 2 khi test đã NHẬN khúc 1 qua web2api — gom bộ đệm ở đâu là kẹt ở đây.
                    ok = await asyncio.to_thread(self.cho_khuc_2.wait, 5)
                    if not ok:
                        self.qua_han_cho = True
                for c in khuc[1:]:
                    yield dong(c)
                yield "data: [DONE]\n\n"

            return StreamingResponse(phat(), media_type="text/event-stream")
        noi_dung = json.dumps({
            "id": "chatcmpl-gia", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "chào từ Google giả"},
                         "finish_reason": "stop"}],
            "x_truong_la_cua_google": {"giu": True},
        }).encode()
        # Google thật nén gzip; httpx tự giải nén nên chuyển tiếp nguyên header content-encoding là hỏng khách.
        return Response(gzip.compress(noi_dung), headers={"content-encoding": "gzip",
                                                          "content-type": "application/json"})

    # ── ảnh (đường tương thích OpenAI) ──

    async def tao_anh(self, req: Request):
        than = await req.json()
        khoa = self._ghi(req, "anh", than=than)
        loi = await self._loi_theo_khoa(khoa)
        if loi is not None:
            return loi
        hv = self.anh.get(khoa, {"kieu": "tot"})
        if hv["kieu"] == "tu_choi":
            hd = {"retry-after": hv["retry_after"]} if "retry_after" in hv else {}
            r = self._tu_choi(hv["ma"], khoa, "Tạo ảnh")
            r.headers.update(hd)
            return r
        if than.get("model") == "loi-400":
            return JSONResponse({"error": {"code": 400, "message": "Invalid aspect ratio", "status": "INVALID_ARGUMENT"}},
                                status_code=400)
        anh = [{"b64_json": base64.b64encode(f"PNG-GIA-{than.get('model')}-{i}".encode()).decode()}
               for i in range(int(than.get("n") or 1))]
        return JSONResponse({"created": 1760000000, "data": anh})

    # ── API gốc: danh sách model, Veo predictLongRunning, hỏi thao tác, tải tệp ──

    async def models_goc(self, req: Request):
        self._ghi(req, "models_goc", trang=req.query_params.get("pageToken"))
        if self.models_goc_loi:
            return JSONResponse({"error": {"code": 500, "message": "lỗi", "status": "INTERNAL"}}, status_code=500)
        # Phân trang thật như Google: mỗi trang 1 model nếu pageSize=1, để test thấy module đi hết các trang.
        co = int(req.query_params.get("pageSize") or 50)
        bat_dau = int(req.query_params.get("pageToken") or 0)
        trang = self.models_goc_ds[bat_dau:bat_dau + co]
        ra = {"models": trang}
        if bat_dau + co < len(self.models_goc_ds):
            ra["nextPageToken"] = str(bat_dau + co)
        return JSONResponse(ra)

    async def models_con(self, req: Request):
        con = req.path_params["con"]
        if req.method == "POST" and con.endswith(":predictLongRunning"):
            return await self._tao_video(req, con.removesuffix(":predictLongRunning"))
        model, _, op = con.partition("/operations/")
        if req.method == "GET" and op:
            return self._hoi_thao_tac(req, model, op)
        return JSONResponse({"error": {"code": 404, "message": "không có", "status": "NOT_FOUND"}}, status_code=404)

    async def _tao_video(self, req: Request, model: str):
        than = await req.json()
        khoa = self._ghi(req, "video_tao", than=than, model=model)
        loi = await self._loi_theo_khoa(khoa)
        if loi is not None:
            return loi
        hv = self.video.get(khoa, {"kieu": "tot"})
        if hv["kieu"] == "tu_choi":
            return self._tu_choi(hv["ma"], khoa, "Tạo video")
        op = f"op{len(self.thao_tac) + 1}"
        self.thao_tac[op] = {"khoa": khoa, "model": model, "con_lai": self.so_lan_hoi, "ket_qua": self.ket_qua_video}
        return JSONResponse({"name": f"models/{model}/operations/{op}"})

    def _hoi_thao_tac(self, req: Request, model: str, op: str):
        khoa = self._ghi(req, "video_hoi", op=op)
        tt = self.thao_tac.get(op)
        # Thao tác thuộc dự án của khoá đã tạo: khoá khác không thấy (giả định, như tài nguyên theo dự án).
        if tt is None or tt["khoa"] != khoa or tt["model"] != model:
            return JSONResponse({"error": {"code": 404, "message": f"Operation not found: {op}", "status": "NOT_FOUND"}},
                                status_code=404)
        ten = f"models/{model}/operations/{op}"
        if tt["con_lai"] > 0:
            tt["con_lai"] -= 1
            return JSONResponse({"name": ten, "done": False})
        if tt["ket_qua"] == "loi":
            return JSONResponse({"name": ten, "done": True,
                                 "error": {"code": 3, "message": f"Video bị lỗi khi tạo (khoá {khoa})"}})
        if tt["ket_qua"] == "loc":
            return JSONResponse({"name": ten, "done": True, "response": {"generateVideoResponse": {
                "raiMediaFilteredCount": 1, "raiMediaFilteredReasons": ["Bị bộ lọc an toàn chặn (giả)"]}}})
        return JSONResponse({"name": ten, "done": True, "response": {"generateVideoResponse": {"generatedSamples": [
            {"video": {"uri": f"{self.goc_uri}/v1beta/files/{op}:download?alt=media"}}]}}})

    async def tai_tep(self, req: Request):
        ten = req.path_params["ten"]
        khoa = self._ghi(req, "tai_tep", ten=ten)
        op = ten.removesuffix(":download")
        tt = self.thao_tac.get(op)
        if tt is None or tt["khoa"] != khoa:
            return JSONResponse({"error": {"code": 403, "message": "không có quyền với tệp", "status": "PERMISSION_DENIED"}},
                                status_code=403)
        if self.chuyen_huong_toi:
            return Response(status_code=302, headers={"location": f"{self.chuyen_huong_toi}/tai-ve/{op}?chu_ky=gia"})
        return self.phat_video(op)

    def phat_video(self, op: str) -> StreamingResponse:
        du_lieu = du_lieu_video(op, self.co_video)
        cho = self.video_cho

        async def phat():
            khuc = 64 * 1024
            yield du_lieu[:khuc]
            if cho:
                # Chỉ nhả phần còn lại khi test đã NHẬN khúc đầu qua web2api — gom cả tệp ở đâu là kẹt ở đây.
                if not await asyncio.to_thread(self.cho_khuc_video.wait, 5):
                    self.video_qua_han_cho = True
            for i in range(khuc, len(du_lieu), khuc):
                yield du_lieu[i:i + khuc]

        return StreamingResponse(phat(), media_type="video/mp4")


class MayTepGia:
    """Máy chủ tệp KHÁC máy (đích chuyển hướng khi tải video): ghi lại header để chứng minh khoá không bị
    mang sang máy khác."""

    def __init__(self, google: GoogleGia):
        self.google = google
        self.nhat_ky: list[dict] = []
        self.app = Starlette(routes=[Route("/{duong:path}", self.bat_ky)])

    async def bat_ky(self, req: Request):
        self.nhat_ky.append({"x_goog_api_key": req.headers.get("x-goog-api-key"),
                             "authorization": req.headers.get("authorization"), "url": str(req.url)})
        duong = req.path_params["duong"]
        if duong.startswith("tai-ve/"):
            return self.google.phat_video(duong.removeprefix("tai-ve/"))
        return JSONResponse({"error": {"code": 404, "message": "không có"}}, status_code=404)


# ───────────────────────────── OpenAI từ xa giả ─────────────────────────────

class OpenAIGia:
    """Một dịch vụ nói chuẩn OpenAI (ví dụ module GPU trong LAN) ở /v1."""

    def __init__(self, khoa: str | None = None):
        self.khoa = khoa
        self.nhat_ky: list[dict] = []
        self.app = Starlette(routes=[
            Route("/v1/models", self.models),
            Route("/v1/chat/completions", self.chat, methods=["POST"]),
        ])

    def _sai_khoa(self, req):
        if self.khoa and req.headers.get("authorization") != f"Bearer {self.khoa}":
            return JSONResponse({"error": {"message": f"sai khoá {req.headers.get('authorization')}",
                                           "type": "invalid_request_error"}}, status_code=401)
        return None

    async def models(self, req):
        self.nhat_ky.append({"duong": "models", "auth": req.headers.get("authorization")})
        return self._sai_khoa(req) or JSONResponse(
            {"object": "list", "data": [{"id": "qwen-3b", "object": "model", "owned_by": "lan"}]})

    async def chat(self, req):
        than = await req.json()
        self.nhat_ky.append({"duong": "chat", "auth": req.headers.get("authorization"), "than": than})
        sai = self._sai_khoa(req)
        if sai:
            return sai
        if than.get("stream"):
            async def phat():
                yield 'data: {"choices":[{"delta":{"content":"xa"}}]}\n\n'
                yield "data: [DONE]\n\n"
            return StreamingResponse(phat(), media_type="text/event-stream")
        return JSONResponse({"object": "chat.completion", "model": than["model"],
                             "choices": [{"index": 0, "message": {"role": "assistant", "content": "từ xa"}}]})


# ───────────────────────────── GitHub giả ─────────────────────────────

def bam_blob(du_lieu: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(du_lieu) + du_lieu).hexdigest()


def _bam(x) -> str:
    return hashlib.sha1(repr(x).encode()).hexdigest()


class GitHubGia:
    """Mô phỏng phần API GitHub web2api dùng: commits (ETag/304, verification), git/trees, git/blobs.

    `day(tep)` = một commit chứa ĐÚNG các tệp trong `tep` (ảnh chụp toàn kho). sha cây thư mục
    băm theo nội dung con nên thư mục không đổi giữ nguyên sha qua các commit — như git thật.
    """

    def __init__(self, repo: str = "chu-may/c2a", token: str | None = None):
        self.repo = repo
        self.token = token
        self.nhanh: dict[str, str] = {}
        self.commits: dict[str, dict] = {}
        self.trees: dict[str, list[dict]] = {}
        self.blobs: dict[str, bytes] = {}
        self.blob_sai: dict[str, bytes] = {}
        self.loi_commit: list[tuple] = []  # (mã, headers[, thông điệp]) trả cho các lần gọi commits kế tiếp
        self.han_muc: tuple[int, int] | None = None  # (còn lại, reset epoch) gắn vào MỌI phản hồi
        self.cat_cay = False
        self.nhat_ky: list[dict] = []
        p = "/repos/{owner}/{ten}"
        self.app = Starlette(routes=[
            Route(p + "/commits/{nhanh:path}", self.commit),
            Route(p + "/git/trees/{sha}", self.cay),
            Route(p + "/git/blobs/{sha}", self.blob),
        ])

    def day(self, tep: dict, thong_diep: str = "cập nhật module", xac_minh: bool = False,
            nhanh: str = "main", tac_gia: str = "Chủ máy") -> str:
        muc = []
        thu_muc = set()
        for duong, v in tep.items():
            phan = duong.split("/")
            for i in range(1, len(phan)):
                thu_muc.add("/".join(phan[:i]))
            if isinstance(v, tuple) and v[0] == "symlink":
                sha = bam_blob(v[1])
                self.blobs[sha] = v[1]
                muc.append({"path": duong, "mode": "120000", "type": "blob", "sha": sha, "size": len(v[1])})
            elif isinstance(v, tuple) and v[0] == "submodule":
                muc.append({"path": duong, "mode": "160000", "type": "commit", "sha": v[1]})
            else:
                sha = bam_blob(v)
                self.blobs[sha] = v
                muc.append({"path": duong, "mode": "100644", "type": "blob", "sha": sha, "size": len(v)})
        la = list(muc)  # chỉ tệp: sha thư mục không được phụ thuộc thứ tự duyệt tập thư mục
        for d in sorted(thu_muc):
            con = sorted((m["path"], m["sha"], m["mode"]) for m in la if m["path"].startswith(d + "/"))
            muc.append({"path": d, "mode": "040000", "type": "tree", "sha": _bam(con)})
        goc = _bam(sorted((m["path"], m["sha"]) for m in muc))
        self.trees[goc] = sorted(muc, key=lambda m: m["path"])
        cha = self.nhanh.get(nhanh)
        sha = _bam((goc, cha, thong_diep, len(self.commits)))
        self.commits[sha] = {"cay": goc, "thong_diep": thong_diep, "xac_minh": xac_minh, "tac_gia": tac_gia}
        self.nhanh[nhanh] = sha
        return sha

    def _hm(self) -> dict:
        if self.han_muc is None:
            return {}
        return {"x-ratelimit-remaining": str(self.han_muc[0]), "x-ratelimit-reset": str(self.han_muc[1])}

    def _ghi(self, req: Request, loai: str):
        self.nhat_ky.append({"loai": loai, "duong": req.url.path, "auth": req.headers.get("authorization"),
                             "if_none_match": req.headers.get("if-none-match")})

    def _kiem(self, req: Request):
        if f"{req.path_params['owner']}/{req.path_params['ten']}" != self.repo:
            return JSONResponse({"message": "Not Found"}, status_code=404)
        if self.token and req.headers.get("authorization") != f"Bearer {self.token}":
            return JSONResponse({"message": "Bad credentials"}, status_code=401)
        return None

    async def commit(self, req: Request):
        self._ghi(req, "commit")
        if (loi := self._kiem(req)) is not None:
            return loi
        if self.loi_commit:
            ma, hd, *tin = self.loi_commit.pop(0)
            return JSONResponse({"message": tin[0] if tin else f"lỗi giả {ma}"}, status_code=ma, headers=hd)
        sha = self.nhanh.get(req.path_params["nhanh"])
        if sha is None:
            return JSONResponse({"message": "Git Repository is empty."}, status_code=409, headers=self._hm())
        etag = f'W/"{sha}"'
        if req.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"etag": etag, **self._hm()})
        c = self.commits[sha]
        return JSONResponse({
            "sha": sha,
            "commit": {
                "message": c["thong_diep"],
                "author": {"name": c["tac_gia"], "date": "2026-10-10T00:00:00Z"},
                "tree": {"sha": c["cay"]},
                "verification": {"verified": c["xac_minh"], "reason": "valid" if c["xac_minh"] else "unsigned"},
            },
        }, headers={"etag": etag, **self._hm()})

    async def cay(self, req: Request):
        self._ghi(req, "tree")
        if (loi := self._kiem(req)) is not None:
            return loi
        sha = req.path_params["sha"]
        if sha not in self.trees:
            return JSONResponse({"message": "Not Found"}, status_code=404)
        return JSONResponse({"sha": sha, "tree": self.trees[sha], "truncated": self.cat_cay}, headers=self._hm())

    async def blob(self, req: Request):
        self._ghi(req, "blob")
        if (loi := self._kiem(req)) is not None:
            return loi
        sha = req.path_params["sha"]
        if sha not in self.blobs:
            return JSONResponse({"message": "Not Found"}, status_code=404)
        du_lieu = self.blob_sai.get(sha, self.blobs[sha])
        return JSONResponse({"sha": sha, "size": len(du_lieu), "encoding": "base64",
                             "content": base64.encodebytes(du_lieu).decode()}, headers=self._hm())


# ───────────────────────────── module mẫu cho test ─────────────────────────────

def tep_echo(loi_chao: str = "echo v1", phien_ban: str = "1.0.0", hong: bool = False,
             tien_to: str = "echo", id_: str = "echo", yeu_cau_goi: list | None = None,
             mang_ra: list | None = None) -> dict[str, bytes]:
    """Các tệp của module «echo» giả: không cần cấu hình, có tab web. `hong` = lỗi cú pháp."""
    manifest = {
        "id": id_, "ten": f"Echo {phien_ban}", "mo_ta": "Lặp lại câu cuối — module giả cho test",
        "loai": "ai", "giao_thuc": "openai", "tien_to": tien_to, "phien_ban": phien_ban,
        "truong_cau_hinh": [],
        "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False, "mang_ra": mang_ra or [], "ghi_chu": ""},
        "tab_web": {"nhan": "Echo", "trang": "web/echo.dc.html"},
    }
    if yeu_cau_goi is not None:
        manifest["yeu_cau_goi"] = yeu_cau_goi
    ma = f'''import json

LOI_CHAO = {loi_chao!r}


class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self.da_dung = False

    def nap(self, cau_hinh):
        self.cau_hinh = cau_hinh

    async def cai(self, cau_hinh):
        return {{"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": {{}}}}

    async def chat(self, body, stream):
        cuoi = body["messages"][-1]["content"]
        than = {{"object": "chat.completion", "model": body["model"],
                "choices": [{{"index": 0, "message": {{"role": "assistant", "content": LOI_CHAO + ": " + cuoi}},
                             "finish_reason": "stop"}}]}}
        return 200, {{"content-type": "application/json"}}, json.dumps(than).encode()

    async def models(self):
        return [{{"id": self.tien_to + "/lap-lai", "object": "model", "owned_by": "echo"}}]

    async def suc_khoe(self):
        return {{"ok": True, "phien_ban": LOI_CHAO}}

    async def dung(self):
        self.da_dung = True
'''
    if hong:
        ma += "\ndef hong(:\n"
    return {
        "manifest.json": json.dumps(manifest, ensure_ascii=False).encode(),
        "module.py": ma.encode(),
        "web/echo.dc.html": f"<x-dc>{loi_chao}</x-dc>".encode(),
    }


def tep_kho(modules: dict[str, dict], thu_muc: str = "modules", them: dict | None = None) -> dict:
    """Ảnh chụp toàn kho cho GitHubGia.day: {id: {đường tương đối: nội dung}} → {đường đầy đủ: nội dung}."""
    ra = {"README.md": b"# kho", **(them or {})}
    for id_, tep in modules.items():
        for rel, v in tep.items():
            ra[f"{thu_muc}/{id_}/{rel}"] = v
    return ra


def ghi_thu_muc(goc, tep: dict[str, bytes]):
    for duong, v in tep.items():
        p = goc / duong
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(v)
    return goc
