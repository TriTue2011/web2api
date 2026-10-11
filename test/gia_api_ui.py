"""API GIẢ cho test giao diện trang «Module» (web/Cai-module.dc.html).

Vì sao giả chứ không dùng lõi thật: test giao diện cần ép những trạng thái mà lõi thật khó tạo
theo ý — 401, lỗi 500, máy chủ chậm, luồng SSE nhỏ giọt hoặc dồn 80 khúc một lần, nguồn git
chưa cấu hình / trả 503, tên module chứa HTML độc. Hình dạng phản hồi chép theo mã lõi THẬT
(app/tuyen.py, app/so_module.py, app/nguon_git.py, app/chuyen_tiep.py đọc ngày 10/10/2026),
không theo mô tả bằng lời: ví dụ kết quả từng khoá dùng trường `khoa` (đã che), catalog bọc
trong {"modules", "loi"}, tab_web là {"nhan", "duong"}.

Chạy trong luồng của chính tiến trình pytest nên test đọc/đổi `GiaApi.che_do` và
`GiaApi.nhat_ky` trực tiếp, không cần cổng điều khiển riêng.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

WEB = Path(__file__).resolve().parent.parent / "web"
TEP_TINH = {
    "/Cai-module.dc.html": ("Cai-module.dc.html", "text/html; charset=utf-8"),
    "/Khoi-dau.dc.html": ("Khoi-dau.dc.html", "text/html; charset=utf-8"),
    "/support.js": ("support.js", "text/javascript; charset=utf-8"),
}

KHOA_QT = "khoa-quan-tri-DUNG-0123456789abcdef"
XSS_TEN = '<img src=x onerror="window.__xss=1">'
XSS_TEN_2 = "<img src=x onerror=window.__xss=2>"
XSS_COMMIT = '<img src=x onerror="window.__xss=4">'
XSS_MODEL = "gemini/<img src=x onerror=window.__xss=5>"
GIB = 1024 ** 3

HE_THONG = {
    "phien_ban": "0.2.0-gia",
    "ram": {"may": {"tong": 16 * GIB, "trong": 6 * GIB}, "container": {"gioi_han": 2 * GIB, "dang_dung": GIB // 2}},
    "cpu": {"so_nhan_may": 8, "gioi_han_container": None},
    "dia": {"thu_muc": "/data", "tong": 99 * GIB, "trong": 19 * GIB, "loi": None},
    "gpu": None,
}

CATALOG = [
    {
        "id": "gemini-gia", "ten": "Gemini giả", "mo_ta": "Module giả có đủ ba kiểu trường cấu hình.",
        "phien_ban": "0.1.0", "loai": "ai", "giao_thuc": "openai", "tien_to": "gemini",
        "truong_cau_hinh": [
            {"khoa": "api_keys", "nhan": "Khoá API", "kieu": "danh_sach_bi_mat", "bat_buoc": True, "mac_dinh": None,
             "mo_ta": "Một hoặc nhiều khoá."},
            {"khoa": "base_url", "nhan": "Địa chỉ gốc", "kieu": "chuoi", "bat_buoc": False,
             "mac_dinh": "https://gia.example/v1beta/openai", "mo_ta": "Để trống là gọi thẳng."},
            {"khoa": "ma_phu", "nhan": "Mã bí mật phụ", "kieu": "bi_mat", "bat_buoc": True, "mac_dinh": None,
             "mo_ta": "Trường bí mật một dòng."},
        ],
        "tai_nguyen": {"ram_them_mb": None, "dia_mb": 12, "gpu": False, "mang_ra": ["gia.example"],
                       "ghi_chu": "RAM chưa đo."},
        "yeu_cau_goi": ["httpx"], "tab_web": None, "nguon": "anh", "da_cau_hinh": False,
    },
    {
        "id": "xss-mod", "ten": XSS_TEN, "mo_ta": "<b>mô tả</b><script>window.__xss=6</script>",
        "phien_ban": "9.9.9", "loai": "ai", "giao_thuc": "openai", "tien_to": "xss",
        "truong_cau_hinh": [],
        "tai_nguyen": {"ram_them_mb": 999999, "dia_mb": None, "gpu": None, "mang_ra": None, "ghi_chu": None},
        "yeu_cau_goi": [], "tab_web": None, "nguon": "du_lieu", "da_cau_hinh": False,
    },
]

# Như lõi thật (app/tuyen.py ds_modules): mọi module CÓ MÃ trên máy đều có mặt (kể cả chưa liên kết),
# kèm da_cau_hinh / can_cau_hinh / nguon / tab_web; module không khai tab riêng có tab trỏ thẻ của nó ở trang Module.
MODS = [
    {"id": "gemini-gia", "ten": "Gemini giả", "loai": "ai", "tien_to": "gemini", "phien_ban": "0.1.0", "nguon": "anh",
     "can_cau_hinh": True, "da_cau_hinh": False,
     "tab_web": {"nhan": "Gemini giả", "duong": "Cai-module.dc.html#the-gemini-gia"}},
    {"id": "xss-mod", "ten": XSS_TEN, "loai": "ai", "tien_to": "xss", "phien_ban": "9.9.9", "nguon": "du_lieu",
     "can_cau_hinh": False, "da_cau_hinh": False,
     "tab_web": {"nhan": XSS_TEN, "duong": "Cai-module.dc.html#the-xss-mod"}},
    {"id": "tien-ich", "ten": "Tiện ích có tab", "cai_luc": "2026-10-10T08:00:00+00:00",
     "cau_hinh_cong_khai": {"api_keys": ["…wxyz"], "base_url": "https://gia.example/v1"},
     "da_cau_hinh": True, "tab_web": {"nhan": "Tab giả", "duong": "/m/tien-ich/index.html"}},
    {"id": "xau", "ten": XSS_TEN_2, "cai_luc": "2026-10-10T08:00:00+00:00", "cau_hinh_cong_khai": {},
     "da_cau_hinh": True, "tab_web": {"nhan": "Tab độc", "duong": "javascript:window.__xss=3"}},
    {"id": None, "hong": "'chuoi-hong'"},
]

THONG_BAO = {
    "so_moi": 2,
    "danh_sach": [
        {"id": "dich-gia", "ten": "Dịch giả", "trang_thai": "moi", "phien_ban_cu": None, "phien_ban_moi": "0.2.0",
         "commit": {"sha": "abcdef1234567890abcdef", "tac_gia": "Tri Tue", "thong_diep": "feat: thêm dịch\n\nchi tiết dài"},
         "ly_do": None, "ghi_chu": None},
        {"id": "gemini-gia", "ten": "Gemini giả", "trang_thai": "co_ban_moi", "phien_ban_cu": "0.1.0", "phien_ban_moi": "0.1.1",
         "commit": {"sha": "0123456789abcdef0123", "tac_gia": "Ai Đó", "thong_diep": XSS_COMMIT}, "ly_do": None, "ghi_chu": None},
        {"id": "hong", "ten": "Hỏng", "trang_thai": "loi_manifest", "phien_ban_cu": None, "phien_ban_moi": None,
         "commit": {"sha": "0123456789abcdef0123", "tac_gia": "Ai Đó", "thong_diep": "x"}, "ly_do": "thiếu manifest.json",
         "ghi_chu": None},
    ],
    "dong_bo": {"kiem_luc": "2026-10-10T08:00:00+00:00", "loi": None, "canh_bao": None, "han_muc_con": 4999,
                "reset_luc": "2026-10-10T09:00:00+00:00"},
}

NGUON_GIT = {
    "cau_hinh": {"repo": "TriTue2011/web2api", "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 300,
                 "tu_dong": False, "yeu_cau_chu_ky": False, "token": "…abcd"},
    "trang_thai": {"kiem_luc": "2026-10-10T08:00:00+00:00", "loi": None, "canh_bao": None, "han_muc_con": 4999,
                   "reset_luc": "2026-10-10T09:00:00+00:00", "thu_lai_luc": None, "commit": None, "so_module": 3},
}

MODELS = ["gemini/gemini-2.5-flash", "gemini/gemini-pro", XSS_MODEL]


def che(k: str) -> str:
    """Cùng quy tắc với app/kho.py::che."""
    return "…" + k[-4:] if len(k) >= 12 else "…"


class GiaApi:
    def __init__(self):
        self.khoa = KHOA_QT
        self.catalog = copy.deepcopy(CATALOG)
        self.mods = copy.deepcopy(MODS)
        self.thong_bao = copy.deepcopy(THONG_BAO)
        self.nguon_git = copy.deepcopy(NGUON_GIT)
        self.models = list(MODELS)
        self.che_do = {
            "catalog": "ok",          # ok | 500 | cham
            "thong_bao": "ok",        # ok | 404 | 503
            "cai": "ok",              # ok | tu_choi (400 kèm kết quả) | 500
            "chat": "ok",             # ok | loi
            "khuc": ["Xin ", "chào, ", "tôi ", "là ", "model ", "giả."],
            "tre_khuc": 0.15,         # giây giữa hai khúc SSE
            "don_mot_lan": False,     # True: mọi khúc trong MỘT lần ghi
        }
        self.nhat_ky: list[dict] = []
        self.ngat: list[str] = []     # đường mà khách cắt kết nối giữa chừng (nút Dừng)
        self._khoa_luong = threading.Lock()

    # ---------- tiện cho test ----------
    def goi_toi(self, method: str, duong: str) -> list[dict]:
        with self._khoa_luong:
            return [x for x in self.nhat_ky if x["method"] == method and x["path"] == duong]

    def ghi(self, muc: dict) -> None:
        with self._khoa_luong:
            self.nhat_ky.append(muc)


def _xu_ly(api: GiaApi):
    class XuLy(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        # ---------- trả lời ----------
        def _json(self, ma: int, du_lieu, them_dau: dict | None = None):
            than = json.dumps(du_lieu, ensure_ascii=False).encode()
            self.send_response(ma)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(than)))
            for k, v in (them_dau or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(than)

        def _loi(self, ma: int, tin: str, code=None):
            loai = {400: "invalid_request_error", 401: "authentication_error", 404: "not_found_error"}.get(ma, "api_error")
            self._json(ma, {"error": {"message": tin, "type": loai, "code": code if code is not None else ma}},
                       {"WWW-Authenticate": "Bearer"} if ma == 401 else None)

        def _dung_khoa(self) -> bool:
            loai, _, gt = self.headers.get("Authorization", "").partition(" ")
            if loai.lower() == "bearer" and gt.strip() == api.khoa:
                return True
            self._loi(401, "Thiếu hoặc sai khoá quản trị. Gửi header «Authorization: Bearer <khoá>».", "invalid_api_key")
            return False

        def _doc_than(self):
            n = int(self.headers.get("Content-Length") or 0)
            tho = self.rfile.read(n).decode() if n else ""
            return tho

        def _ghi_nhat_ky(self, than: str):
            api.ghi({"method": self.command, "path": unquote(self.path.split("?", 1)[0]),
                     "auth": self.headers.get("Authorization", ""), "body": than, "luc": time.monotonic()})

        # ---------- định tuyến ----------
        def do_GET(self):
            self._ghi_nhat_ky("")
            self._dinh_tuyen("GET", "")

        def do_POST(self):
            than = self._doc_than()
            self._ghi_nhat_ky(than)
            self._dinh_tuyen("POST", than)

        def do_PUT(self):
            than = self._doc_than()
            self._ghi_nhat_ky(than)
            self._dinh_tuyen("PUT", than)

        def do_DELETE(self):
            self._ghi_nhat_ky("")
            self._dinh_tuyen("DELETE", "")

        def _dinh_tuyen(self, m: str, than: str):
            p = unquote(self.path.split("?", 1)[0])
            if m == "GET" and p in TEP_TINH:
                ten, kieu = TEP_TINH[p]
                du_lieu = (WEB / ten).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", kieu)
                self.send_header("Content-Length", str(len(du_lieu)))
                self.end_headers()
                self.wfile.write(du_lieu)
                return
            if m == "GET" and p.startswith("/m/"):
                du_lieu = "<!doctype html><title>tab</title><p>Tab của module</p>".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(du_lieu)))
                self.end_headers()
                self.wfile.write(du_lieu)
                return
            # ----- MỞ -----
            if m == "GET" and p == "/api/he-thong":
                return self._json(200, HE_THONG)
            if m == "GET" and p == "/api/modules":
                so = api.thong_bao["so_moi"] if api.che_do["thong_bao"] == "ok" else 0
                return self._json(200, {"modules": api.mods, "canh_bao": None, "thong_bao_so": so})
            if m == "GET" and p == "/api/modules/catalog":
                cd = api.che_do["catalog"]
                if cd == "500":
                    return self._loi(500, "Sổ module hỏng (giả)")
                if cd == "cham":
                    time.sleep(1.5)
                return self._json(200, {"modules": api.catalog, "loi": [{"thu_muc": "/data/modules/hong", "ly_do": "thiếu manifest.json"}]})
            # ----- CẦN KHOÁ -----
            if not self._dung_khoa():
                return
            if m == "GET" and p == "/api/modules/thong-bao":
                cd = api.che_do["thong_bao"]
                if cd in ("404", "503"):
                    return self._loi(int(cd), "nguồn git chưa bật (giả)")
                if api.nguon_git["cau_hinh"] is None:
                    return self._json(200, {"so_moi": 0, "danh_sach": [], "dong_bo": {}})
                return self._json(200, api.thong_bao)
            if m == "GET" and p == "/api/nguon-git":
                return self._json(200, api.nguon_git)
            if m == "PUT" and p == "/api/nguon-git":
                j = json.loads(than)
                cfg = {k: j.get(k) for k in ("repo", "nhanh", "thu_muc", "chu_ky_giay", "tu_dong", "yeu_cau_chu_ky")}
                cu = api.nguon_git["cau_hinh"] or {}
                cfg["token"] = che(j["token"]) if j.get("token") else (None if "token" in j else cu.get("token"))
                api.nguon_git["cau_hinh"] = cfg
                return self._json(200, api.nguon_git)
            if m == "POST" and p.startswith("/api/nguon-git/cai/"):
                return self._cai_git(p.rsplit("/", 1)[-1])
            if m == "POST" and p == "/api/modules/tu-xa":
                j = json.loads(than)
                ban = {"id": j["id"], "ten": j["ten"], "loai": "tu_xa", "tien_to": j["tien_to"],
                       "cai_luc": "2026-10-10T08:00:00+00:00",
                       "cau_hinh_cong_khai": {"base_url": j["base_url"], "api_key": che(j["api_key"]) if j.get("api_key") else None}}
                api.mods.append(ban)
                sk = {"ok": True, "base_url": j["base_url"],
                      "khoa": [{"khoa": che(j["api_key"]) if j.get("api_key") else "(không khoá)", "ok": True,
                                "do_tre_ms": 33, "so_model": 2, "loi": None, "dang_nghi_den": None}]}
                return self._json(200, {"ok": True, "module": ban, "suc_khoe": sk})
            if m == "POST" and p.startswith("/api/modules/") and p.endswith("/cai"):
                return self._cai(p.split("/")[3], than)
            if m == "GET" and p.startswith("/api/modules/") and p.endswith("/suc-khoe"):
                return self._json(200, {"ok": True, "base_url": "https://gia.example/v1", "khoa": [
                    {"khoa": "…wxyz", "ok": True, "do_tre_ms": 55, "so_model": 7, "loi": None, "dang_nghi_den": None},
                    {"khoa": "…hong", "ok": False, "do_tre_ms": 80, "so_model": 0, "loi": "HTTP 429: hết hạn mức", "dang_nghi_den": None}]})
            if m == "DELETE" and p.startswith("/api/modules/"):
                id_ = p.rsplit("/", 1)[-1]
                api.mods = [x for x in api.mods if x.get("id") != id_]
                return self._json(200, {"ok": True, "id": id_})
            if m == "GET" and p == "/v1/models":
                return self._json(200, {"object": "list", "data": [{"id": x, "object": "model"} for x in api.models]})
            if m == "POST" and p == "/v1/chat/completions":
                return self._chat(json.loads(than))
            self._loi(404, f"không có {m} {p} (giả)")

        def _cai_git(self, id_: str):
            """Như lõi thật (app/nguon_git.py): cài xong thì mục thành «da_cai_dung» và RỜI danh sách
            thông báo; module có trường bắt buộc được cài (mã về máy) nhưng chưa liên kết — vào catalog và
            /api/modules với da_cau_hinh=false, `cai` = None — chờ người dùng thêm khoá."""
            muc = next((x for x in api.thong_bao["danh_sach"] if x["id"] == id_), None)
            if muc is None:
                return self._loi(404, f"module {id_!r} không có trong lần đồng bộ gần nhất")
            api.thong_bao["danh_sach"].remove(muc)
            api.thong_bao["so_moi"] = sum(x["trang_thai"] in ("moi", "co_ban_moi") for x in api.thong_bao["danh_sach"])
            if not any(c["id"] == id_ for c in api.catalog):
                api.catalog.append({
                    "id": id_, "ten": muc["ten"], "mo_ta": "Module tải từ git.", "phien_ban": muc["phien_ban_moi"],
                    "loai": "ai", "giao_thuc": "openai", "tien_to": id_.split("-")[0],
                    "truong_cau_hinh": [{"khoa": "api_key", "nhan": "Khoá dịch vụ", "kieu": "bi_mat", "bat_buoc": True,
                                         "mac_dinh": None, "mo_ta": None}],
                    "tai_nguyen": {"ram_them_mb": 64, "dia_mb": 3, "gpu": False, "mang_ra": [], "ghi_chu": None},
                    "yeu_cau_goi": [], "tab_web": None, "nguon": "du_lieu", "da_cau_hinh": False})
                api.mods.insert(0, {"id": id_, "ten": muc["ten"], "loai": "ai", "tien_to": id_.split("-")[0],
                                    "phien_ban": muc["phien_ban_moi"], "nguon": "du_lieu", "can_cau_hinh": True,
                                    "da_cau_hinh": False,
                                    "tab_web": {"nhan": muc["ten"], "duong": f"Cai-module.dc.html#the-{id_}"}})
            return self._json(200, {"ok": True, "id": id_, "phien_ban": muc["phien_ban_moi"], "sha_commit": "abcdef1234",
                                    "nap_lai": {"them": [id_], "doi": [], "go": [], "loi": []}, "cai": None})

        def _cai(self, id_: str, than: str):
            cd = api.che_do["cai"]
            if cd == "500":
                return self._loi(500, "Lõi gặp sự cố khi cài (giả)")
            cau_hinh = json.loads(than).get("cau_hinh") or {}
            ds = []
            for k in cau_hinh.get("api_keys") or []:
                tot = cd == "ok" and "hong" not in k
                ds.append({"khoa": che(k), "ok": tot, "do_tre_ms": 123 if tot else 456, "so_model": 42 if tot else 0,
                           "loi": None if tot else "HTTP 400: API key not valid. Please pass a valid API key."})
            ok = any(x["ok"] for x in ds) or (cd == "ok" and not ds)
            if not ok:
                return self._json(400, {"ok": False, "ket_qua_tung_khoa": ds,
                                        "loi": "Không khoá nào gọi được https://gia.example/v1beta/openai/models."})
            cu = {}
            for c in api.catalog:
                if c["id"] == id_:
                    c["da_cau_hinh"] = True
            for x in api.mods:
                if x.get("id") == id_:
                    cu = x
            api.mods = [x for x in api.mods if x.get("id") != id_] + [
                {**cu, "id": id_, "cai_luc": "2026-10-10T08:00:00+00:00", "da_cau_hinh": True,
                 "cau_hinh_cong_khai": {"api_keys": [x["khoa"] for x in ds if x["ok"]], "ma_phu": "…"}}]
            return self._json(200, {"ok": True, "ket_qua_tung_khoa": ds, "loi": None})

        def _chat(self, j: dict):
            if api.che_do["chat"] == "loi":
                return self._loi(400, "Model «gemini/khong-co» không tồn tại (giả)", "model_not_found")
            khuc = list(api.che_do["khuc"])
            if not j.get("stream"):
                return self._json(200, {"id": "x", "object": "chat.completion", "model": j.get("model"),
                                        "choices": [{"index": 0, "message": {"role": "assistant", "content": "".join(khuc)},
                                                     "finish_reason": "stop"}]})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def dong(c):
                return ("data: " + json.dumps({"id": "x", "object": "chat.completion.chunk",
                                                "choices": [{"index": 0, "delta": {"content": c}}]}, ensure_ascii=False)
                        + "\n\n").encode()

            try:
                if api.che_do["don_mot_lan"]:
                    self.wfile.write(b"".join(dong(c) for c in khuc) + b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return
                # Khúc đầu là vai trò (không có chữ) như OpenAI thật; trang phải bỏ qua nó.
                self.wfile.write(b'data: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}\n\n')
                self.wfile.flush()
                for c in khuc:
                    time.sleep(api.che_do["tre_khuc"])
                    self.wfile.write(dong(c))
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                api.ngat.append("/v1/chat/completions")

    return XuLy


class MayChuGia:
    """Máy chủ giả trong luồng nền, cổng tự chọn."""

    def __init__(self):
        self.api = GiaApi()
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), _xu_ly(self.api))
        self._srv.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._srv.server_address[1]}"

    def __enter__(self):
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_):
        self._srv.shutdown()
        self._srv.server_close()
