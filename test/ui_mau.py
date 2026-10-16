"""Module GIẢ cho test giao diện chạy với LÕI THẬT (và cho scripts/chup-man-hinh.sh).

Thay cho API giả cũ (gia_api_ui.py): giao diện nói chuyện với lõi thật (uvicorn, lifespan, vòng nền git), nên
hình dạng phản hồi là của mã lõi chứ không phải bản chép tay. Trạng thái khó tạo theo ý (luồng SSE nhỏ giọt hay
dồn cục, tạo ảnh bị từ chối kiểu khoá miễn phí, tên module/model chứa HTML độc) do chính mã module giả làm ra —
module chạy TRONG tiến trình lõi nên đọc được biến môi trường test đặt:
  GIA_KHUC   JSON danh sách khúc chữ của câu trả lời stream   (mặc định ["Xin ", "chào, ", "tôi ", "là ", "model ", "giả."])
  GIA_TRE    giây giữa hai khúc                                (mặc định 0.05)
  GIA_DON    "1" → mọi khúc trong MỘT lần ghi
  GIA_ANH    "403" → tạo ảnh trả 403 no_key_permitted như khoá hạng miễn phí
  GIA_MODEL_ANH "1" → models() có thêm model ảnh `<tiền tố>/ve-1` (`kha_nang: ["anh"]`); mặc định không có
  GIA_SK     "cu"  → suc_khoe dạng TỐI THIỂU của hợp đồng (không thu_tu / dang_dung / han_muc / kha_nang)
             "het" → khoá đầu đang nghỉ chat vì hết hạn mức (429), không có trần (`toi_da` null)
Khoá giả: chuỗi chứa «hong» bị coi là khoá hỏng.
"""

from __future__ import annotations

import json

XSS_TEN = '<img src=x onerror="window.__xss=1">'
XSS_MODEL = "<img src=x onerror=window.__xss=5>"

MA_AI = r'''
import asyncio, base64, json, os

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYPj/HwADAgH/tH0CVwAAAABJRU5ErkJggg==")
VIDEO = b"VIDEO-GIA-" * 100
_HOI = {}


def _loi(ma, tin, code):
    return ma, {"content-type": "application/json"}, json.dumps({"error": {"message": tin, "type": "invalid_request_error", "code": code}}, ensure_ascii=False).encode()


class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self.khoa = []
        self.base = ""

    def nap(self, cau_hinh):
        self.khoa = list(cau_hinh.get("api_keys") or [])
        self.base = cau_hinh.get("base_url") or ""

    async def cai(self, cau_hinh):
        khoa = list(cau_hinh.get("api_keys") or [])
        ds = [{"khoa": "…" + k[-4:], "ok": "hong" not in k, "do_tre_ms": 7, "so_model": 3 if "hong" not in k else 0,
               "loi": None if "hong" not in k else "HTTP 401: khoá không hợp lệ (giả)"} for k in khoa]
        tot = [k for k in khoa if "hong" not in k]
        if not tot:
            return {"ok": False, "ket_qua_tung_khoa": ds, "loi": "Không khoá nào dùng được (giả)."}
        luu = {"api_keys": tot}
        if cau_hinh.get("base_url"):
            luu["base_url"] = cau_hinh["base_url"]
        return {"ok": True, "ket_qua_tung_khoa": ds, "loi": None, "cau_hinh_luu": luu}

    async def chat(self, body, stream):
        if body.get("model", "").endswith("/loi"):
            return _loi(400, "Model không tồn tại (giả)", "model_not_found")
        khuc = json.loads(os.environ.get("GIA_KHUC") or '["Xin ", "chào, ", "tôi ", "là ", "model ", "giả."]')
        if not stream:
            than = {"object": "chat.completion", "model": body["model"],
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "".join(khuc)}, "finish_reason": "stop"}]}
            return 200, {"content-type": "application/json"}, json.dumps(than, ensure_ascii=False).encode()
        tre = float(os.environ.get("GIA_TRE") or 0.05)
        don = os.environ.get("GIA_DON") == "1"

        def dong(c):
            return ("data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": c}}]}, ensure_ascii=False) + "\n\n").encode()

        async def phat():
            if don:
                yield b"".join(dong(c) for c in khuc) + b"data: [DONE]\n\n"
                return
            yield b'data: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}\n\n'
            for c in khuc:
                await asyncio.sleep(tre)
                yield dong(c)
            yield b"data: [DONE]\n\n"
        return 200, {"content-type": "text/event-stream"}, phat()

    async def anh(self, body):
        if os.environ.get("GIA_ANH") == "403":
            return _loi(403, "Không khoá nào được phép tạo ảnh (giả, như khoá hạng miễn phí).", "no_key_permitted")
        return 200, {"content-type": "application/json"}, json.dumps({"created": 1, "data": [{"b64_json": base64.b64encode(PNG).decode()}]}).encode()

    async def video_tao(self, body):
        _HOI["v1"] = 0
        return 200, {"content-type": "application/json"}, json.dumps({"id": "v1", "object": "video", "status": "queued", "model": body.get("model")}).encode()

    async def video_trang_thai(self, ma):
        _HOI[ma] = _HOI.get(ma, 0) + 1
        tt = "completed" if _HOI[ma] >= 2 else "in_progress"
        return 200, {"content-type": "application/json"}, json.dumps({"id": ma, "object": "video", "status": tt, "progress": 100 if tt == "completed" else 50}).encode()

    async def video_noi_dung(self, ma):
        return 200, {"content-type": "video/mp4"}, VIDEO

    async def models(self):
        t = self.tien_to
        ds = [{"id": t + "/nhanh", "object": "model"}, {"id": t + "/" + XSS_MODEL_ID, "object": "model"},
              {"id": t + "/phim-1", "object": "model", "kha_nang": ["video"]}]
        if os.environ.get("GIA_MODEL_ANH") == "1":
            ds.append({"id": t + "/ve-1", "object": "model", "kha_nang": ["anh"]})
        return ds

    async def suc_khoe(self):
        sk = os.environ.get("GIA_SK")
        if sk == "cu":
            return {"ok": True, "khoa": [{"khoa": "…" + k[-4:], "ok": True, "do_tre_ms": 5, "so_model": 3, "loi": None,
                                          "dang_nghi_den": None} for k in self.khoa]}
        if sk == "het":
            return {"ok": True, "khoa": [
                {"khoa": "…" + k[-4:], "ok": True, "do_tre_ms": 5, "so_model": 3, "loi": None,
                 "dang_nghi_den": "2099-01-01T00:00:00+00:00" if i == 0 else None, "thu_tu": i + 1, "dang_dung": i == 1,
                 "dung_cuoi": None, "so_429": 2 if i == 0 else 0, "lan_429_cuoi": None,
                 "han_muc": [{"ten": "chat", "da_dung": 0, "toi_da": None, "ma": 429 if i == 0 else None,
                              "hoi_luc": "2099-01-01T00:00:00+00:00" if i == 0 else None,
                              "trang_thai": "het_han_muc" if i == 0 else "chua_ro"}]} for i, k in enumerate(self.khoa)]}
        return {"ok": True, "base_url": self.base or "https://gia.example/v1", "khoa": [
            {"khoa": "…" + k[-4:], "ok": True, "do_tre_ms": 5, "so_model": 3, "loi": None, "dang_nghi_den": None,
             "thu_tu": i + 1, "dang_dung": i == 0, "dung_cuoi": "2026-10-12T07:55:00+00:00",
             "so_429": 0, "lan_429_cuoi": None,
             "han_muc": [{"ten": "chat", "da_dung": 12, "toi_da": 100, "hoi_luc": None, "trang_thai": "dung_duoc", "ma": None},
                         {"ten": "anh", "da_dung": 0, "toi_da": None, "hoi_luc": "2026-10-12T08:00:00+00:00", "trang_thai": "bi_tu_choi", "ma": 403},
                         {"ten": "video", "da_dung": 0, "toi_da": None, "hoi_luc": None, "trang_thai": "chua_ro", "ma": None}],
             "kha_nang": {"anh": {"trang_thai": "bi_tu_choi", "ma": 403, "thu_lai_luc": "2026-10-12T08:00:00+00:00"},
                          "video": {"trang_thai": "chua_ro"}}} for i, k in enumerate(self.khoa)]}

    async def dung(self):
        pass
'''

MA_KHONG_KHOA = r'''
import json


class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]

    def nap(self, cau_hinh):
        pass

    async def cai(self, cau_hinh):
        return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": {}}

    async def chat(self, body, stream):
        return 400, {"content-type": "application/json"}, b'{"error":{"message":"module nay khong chat","code":"x"}}'

    async def models(self):
        return []

    async def suc_khoe(self):
        return {"ok": True, "khoa": []}

    async def dung(self):
        pass
'''


def module_ai(id_: str = "ai-gia", ten: str = "AI giả", tien_to: str = "gia", cung_cap=("chat", "anh", "video")) -> dict:
    """Nguồn AI giả có trường bắt buộc (cài xong là «chờ khoá»)."""
    m = {
        "id": id_, "ten": ten, "mo_ta": "Nguồn AI giả cho test giao diện: chat stream, ảnh, video, khoá xoay vòng.",
        "phien_ban": "1.0.0", "hang": "A", "loai": "ai", "loai_ten": "AI", "loai_thu_tu": 10, "giao_thuc": "openai", "tien_to": tien_to,
        "cung_cap": list(cung_cap),
        "truong_cau_hinh": [
            {"khoa": "api_keys", "nhan": "Khoá API", "kieu": "danh_sach_bi_mat", "bat_buoc": True, "mac_dinh": None,
             "mo_ta": "Một hoặc nhiều khoá giả."},
            {"khoa": "base_url", "nhan": "Địa chỉ gốc", "kieu": "chuoi", "bat_buoc": False,
             "mac_dinh": "https://gia.example/v1", "mo_ta": "Để trống là gọi thẳng."}],
        "tai_nguyen": {"ram_them_mb": 64, "dia_mb": None, "gpu": False, "mang_ra": ["gia.example"],
                       "ghi_chu": "Không cần cài gì thêm — module giả chạy ngay trong lõi."},
    }
    return {"manifest.json": json.dumps(m, ensure_ascii=False).encode(),
            "module.py": MA_AI.replace("XSS_MODEL_ID", repr(XSS_MODEL)).encode()}


def module_khong_khoa(id_: str, ten: str, loai: str, tien_to: str, *, loai_ten=None, loai_thu_tu=None,
                      cung_cap=(), muc=None, tab=False, mo_ta="Module giả không cần khoá.") -> dict:
    m = {"id": id_, "ten": ten, "mo_ta": mo_ta, "phien_ban": "0.1.0", "hang": "A", "loai": loai, "giao_thuc": "openai",
         "tien_to": tien_to, "cung_cap": list(cung_cap), "truong_cau_hinh": [],
         "tai_nguyen": {"ram_them_mb": None, "dia_mb": 2, "gpu": None, "mang_ra": [], "ghi_chu": ""}}
    if loai_ten:
        m["loai_ten"] = loai_ten
    if loai_thu_tu is not None:
        m["loai_thu_tu"] = loai_thu_tu
    if muc is not None:
        m["muc"] = muc
    tep = {"module.py": MA_KHONG_KHOA.encode()}
    if tab:
        m["tab_web"] = {"nhan": ten, "trang": "web/index.html"}
        tep["web/index.html"] = b"<!doctype html><title>tab</title><p>Tab cua module</p>"
    tep["manifest.json"] = json.dumps(m, ensure_ascii=False).encode()
    return tep


def module_mang() -> dict:
    """Loại LẠ (`mang`) + khả năng LẠ + mục có `can` — chứng minh khung không giữ danh sách loại/khả năng."""
    return module_khong_khoa("mang-gia", "Mạng giả", "mang", "mang", loai_ten="Mạng", loai_thu_tu=30,
                             cung_cap=["dinh_tuyen"], tab=True,
                             muc=[{"id": "bang", "nhan": "Bảng định tuyến", "can": []},
                                  {"id": "tu-dong", "nhan": "Tự động điều chỉnh", "can": ["chat"]}],
                             mo_ta="Module giả loại «mạng»: xem bảng định tuyến; tự điều chỉnh khi có AI chat.")
