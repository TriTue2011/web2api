"""Kiểm thử các bản vá giao thức OpenAI ở lõi (VIỆC: giao-thuc-1):
1. finish_reason khi có tool_calls: chuẩn hoá "stop" thành 'tool_calls' (thường và stream); "length"/"content_filter"
   GIỮ NGUYÊN — lời gọi hàm bị cắt do hết token có tham số JSON đứt, gắn 'tool_calls' là nói dối rằng nó hoàn chỉnh.
   Thân không stream là trọn một lượt nên finish_reason THIẾU/null cũng được chuẩn hoá; trong stream null nghĩa là
   chưa xong nên không đụng.
2. SSE giữ kết nối: khi stream im lặng quá WEB2API_SSE_GIU_GIAY gửi ': keep-alive\\n\\n', chỉ chen GIỮA hai sự kiện
   (lõi tự đệm theo ranh giới sự kiện SSE, không dựa cách module chia khúc), huỷ + đóng luồng module khi khách ngắt.
3. GET /v1/models/{model}: trả đúng mục trong /v1/models, tôn trọng model tắt, phạm vi khoá khách; không có -> 404 model_not_found.
4. X-Request-ID: nhận từ khách nếu hợp lệ (<=128 ký tự, [A-Za-z0-9._-]), không thì sinh mới; ghi vào nhật ký trường ma_yeu_cau.
5. Bỏ bắt buộc chat: module chỉ có 'nghe' không có 'chat' nạp bình thường; gọi /v1/chat/completions -> lỗi chuẩn (501 capability_not_supported, không 500).
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import kho, nhat_ky, so_module
from app.main import app

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    anh = tmp_path / "anh"
    anh.mkdir()
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh)
    (tmp_path / "data" / "modules").mkdir(parents=True)
    return tmp_path


def _tao_module_gia(
    goc: Path,
    id_: str,
    tien_to: str,
    models: list[dict],
    cung_cap: list[str] | None = None,
    co_chat: bool = True,
    co_nghe: bool = False,
) -> Path:
    thu_muc = goc / id_
    thu_muc.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": id_,
        "ten": f"Module {id_}",
        "mo_ta": "Module test giao thức",
        "phien_ban": "1.0.0",
        "hang": "A",
        "loai": "ai",
        "giao_thuc": "openai",
        "tien_to": tien_to,
        "cung_cap": cung_cap if cung_cap is not None else (["chat"] if co_chat else []),
        "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []},
    }
    (thu_muc / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    phuong_thuc = []
    if co_chat:
        phuong_thuc.append("""
    async def chat(self, body, stream):
        if stream:
            async def gen():
                yield b'data: {"id": "c1", "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]}\\n\\n'
                yield b'data: [DONE]\\n\\n'
            return 200, {"content-type": "text/event-stream"}, gen()
        return 200, {"content-type": "application/json"}, json.dumps({
            "id": "c1",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]
        }).encode("utf-8")
""")
    if co_nghe:
        phuong_thuc.append("""
    async def nghe(self, yc):
        return 200, {"content-type": "application/json"}, json.dumps({"text": "am thanh"}).encode("utf-8")
""")

    noi_dung_phuong_thuc = "".join(phuong_thuc)
    ma_python = f"""import json

class Module:
    def __init__(self, m):
        self.manifest = m
        self.tien_to = m["tien_to"]
        self.raw_models = {models!r}
    def nap(self, cfg):
        pass
    async def cai(self, cfg):
        return {{"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": cfg}}
    async def suc_khoe(self):
        return {{"ok": True, "khoa": []}}
    async def dung(self):
        pass
    async def models(self):
        return [dict(x) for x in self.raw_models]
{noi_dung_phuong_thuc}
"""
    (thu_muc / "module.py").write_text(ma_python, encoding="utf-8")
    return thu_muc


# ───────────── 1. finish_reason khi có tool_calls ─────────────


def test_finish_reason_khi_co_tool_calls_chat_thuong(du_lieu):
    """Phản hồi chat thường có tool_calls nhưng finish_reason='stop' -> lõi chuẩn hoá thành 'tool_calls'."""
    id_ = "tool-test"
    tien_to = "tool"
    thu_muc = du_lieu / "data" / "modules" / id_
    thu_muc.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": id_,
        "ten": "Tool Test",
        "mo_ta": "Test finish reason",
        "phien_ban": "1.0.0",
        "hang": "A",
        "loai": "ai",
        "giao_thuc": "openai",
        "tien_to": tien_to,
        "cung_cap": ["chat"],
        "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []},
    }
    (thu_muc / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    ma_python = """import json

class Module:
    def __init__(self, m):
        self.manifest = m
    def nap(self, cfg):
        pass
    async def cai(self, cfg):
        return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": cfg}
    async def suc_khoe(self):
        return {"ok": True, "khoa": []}
    async def dung(self):
        pass
    async def models(self):
        return [{"id": "tool/model-1", "kha_nang": ["chat"]}]
    async def chat(self, body, stream):
        # Trả về phản hồi có tool_calls nhưng finish_reason là 'stop'
        tra_ve = {
            "id": "chatcmpl-tool-1",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_abc123",
                                "type": "function",
                                "function": {"name": "lay_thoi_tiet", "arguments": "{\\"dia_diem\\": \\"Ha Noi\\"}"}
                            }
                        ]
                    },
                    "finish_reason": "stop"
                }
            ]
        }
        return 200, {"content-type": "application/json"}, json.dumps(tra_ve).encode("utf-8")
"""
    (thu_muc / "module.py").write_text(ma_python, encoding="utf-8")

    with TestClient(app) as c:
        c.post(f"/api/modules/{id_}/cai", json={"cau_hinh": {}}, headers=QT)
        r = c.post(
            "/v1/chat/completions",
            json={"model": "tool/model-1", "messages": [{"role": "user", "content": "Thời tiết"}]},
            headers=QT,
        )
        assert r.status_code == 200
        j = r.json()
        assert j["choices"][0]["finish_reason"] == "tool_calls"
        assert len(j["choices"][0]["message"]["tool_calls"]) == 1


def test_finish_reason_khi_co_tool_calls_stream(du_lieu):
    """Phản hồi chat stream có tool_calls qua delta, khúc cuối gửi finish_reason='stop' -> chuẩn hoá thành 'tool_calls'."""
    id_ = "tool-stream"
    tien_to = "toolstr"
    thu_muc = du_lieu / "data" / "modules" / id_
    thu_muc.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": id_,
        "ten": "Tool Stream",
        "mo_ta": "Test stream finish reason",
        "phien_ban": "1.0.0",
        "hang": "A",
        "loai": "ai",
        "giao_thuc": "openai",
        "tien_to": tien_to,
        "cung_cap": ["chat"],
        "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []},
    }
    (thu_muc / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    ma_python = """import json

class Module:
    def __init__(self, m):
        self.manifest = m
    def nap(self, cfg):
        pass
    async def cai(self, cfg):
        return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": cfg}
    async def suc_khoe(self):
        return {"ok": True, "khoa": []}
    async def dung(self):
        pass
    async def models(self):
        return [{"id": "toolstr/model-1", "kha_nang": ["chat"]}]
    async def chat(self, body, stream):
        async def gen():
            # Khúc 1: gửi tool_calls
            yield b'data: {"id": "c1", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "f1", "arguments": ""}}]}, "finish_reason": null}]}\\n\\n'
            # Khúc 2: gửi kết thúc với finish_reason là 'stop' (sai chuẩn khi có tool_calls)
            yield b'data: {"id": "c1", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}\\n\\n'
            # Khúc 3: [DONE]
            yield b'data: [DONE]\\n\\n'
        return 200, {"content-type": "text/event-stream"}, gen()
"""
    (thu_muc / "module.py").write_text(ma_python, encoding="utf-8")

    with TestClient(app) as c:
        c.post(f"/api/modules/{id_}/cai", json={"cau_hinh": {}}, headers=QT)
        r = c.post(
            "/v1/chat/completions",
            json={"model": "toolstr/model-1", "messages": [{"role": "user", "content": "gọi hàm"}], "stream": True},
            headers=QT,
        )
        assert r.status_code == 200
        dong_cac = [d for d in r.text.split("\n\n") if d.strip()]
        assert len(dong_cac) == 3
        j2 = json.loads(dong_cac[1].removeprefix("data: "))
        assert j2["choices"][0]["finish_reason"] == "tool_calls"


def test_finish_reason_khong_phai_json_chuyen_nguyen_van(du_lieu):
    """Khúc stream không phải JSON hợp lệ thì chuyển nguyên văn."""
    id_ = "raw-stream"
    tien_to = "raw"
    thu_muc = du_lieu / "data" / "modules" / id_
    thu_muc.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": id_,
        "ten": "Raw Stream",
        "mo_ta": "Test raw stream",
        "phien_ban": "1.0.0",
        "hang": "A",
        "loai": "ai",
        "giao_thuc": "openai",
        "tien_to": tien_to,
        "cung_cap": ["chat"],
        "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []},
    }
    (thu_muc / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    ma_python = """import json

class Module:
    def __init__(self, m):
        self.manifest = m
    def nap(self, cfg):
        pass
    async def cai(self, cfg):
        return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": cfg}
    async def suc_khoe(self):
        return {"ok": True, "khoa": []}
    async def dung(self):
        pass
    async def models(self):
        return [{"id": "raw/model-1", "kha_nang": ["chat"]}]
    async def chat(self, body, stream):
        async def gen():
            yield b'data: {khong hop le json}\\n\\n'
            yield b'data: [DONE]\\n\\n'
        return 200, {"content-type": "text/event-stream"}, gen()
"""
    (thu_muc / "module.py").write_text(ma_python, encoding="utf-8")

    with TestClient(app) as c:
        c.post(f"/api/modules/{id_}/cai", json={"cau_hinh": {}}, headers=QT)
        r = c.post(
            "/v1/chat/completions",
            json={"model": "raw/model-1", "messages": [{"role": "user", "content": "hi"}], "stream": True},
            headers=QT,
        )
        assert r.status_code == 200
        assert "data: {khong hop le json}\n\n" in r.text


# ───────────── 2. SSE giữ kết nối ─────────────


def test_sse_keep_alive_khi_im_lang(du_lieu, monkeypatch):
    """Khi stream im lặng quá WEB2API_SSE_GIU_GIAY, lõi gửi chú thích ': keep-alive\\n\\n'."""
    monkeypatch.setenv("WEB2API_SSE_GIU_GIAY", "0.05")  # 50ms cho test chạy nhanh
    id_ = "slow-stream"
    tien_to = "slow"
    thu_muc = du_lieu / "data" / "modules" / id_
    thu_muc.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": id_,
        "ten": "Slow Stream",
        "mo_ta": "Test keep-alive",
        "phien_ban": "1.0.0",
        "hang": "A",
        "loai": "ai",
        "giao_thuc": "openai",
        "tien_to": tien_to,
        "cung_cap": ["chat"],
        "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []},
    }
    (thu_muc / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    ma_python = """import asyncio, json

class Module:
    def __init__(self, m):
        self.manifest = m
    def nap(self, cfg):
        pass
    async def cai(self, cfg):
        return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": cfg}
    async def suc_khoe(self):
        return {"ok": True, "khoa": []}
    async def dung(self):
        pass
    async def models(self):
        return [{"id": "slow/model-1", "kha_nang": ["chat"]}]
    async def chat(self, body, stream):
        async def gen():
            yield b'data: {"id": "c1", "choices": [{"index": 0, "delta": {"content": "bat dau"}, "finish_reason": null}]}\\n\\n'
            # Ngủ 0.12s (lâu hơn 50ms ngưỡng keep-alive)
            await asyncio.sleep(0.12)
            yield b'data: {"id": "c1", "choices": [{"index": 0, "delta": {"content": "tiep theo"}, "finish_reason": "stop"}]}\\n\\n'
            yield b'data: [DONE]\\n\\n'
        return 200, {"content-type": "text/event-stream"}, gen()
"""
    (thu_muc / "module.py").write_text(ma_python, encoding="utf-8")

    with TestClient(app) as c:
        c.post(f"/api/modules/{id_}/cai", json={"cau_hinh": {}}, headers=QT)
        r = c.post(
            "/v1/chat/completions",
            json={"model": "slow/model-1", "messages": [{"role": "user", "content": "hi"}], "stream": True},
            headers=QT,
        )
        assert r.status_code == 200
        # Thứ tự đúng, không trùng khúc: sự kiện 1 → ≥1 keep-alive → sự kiện 2 → [DONE].
        cac = [d for d in r.text.split("\n\n") if d]
        assert "bat dau" in cac[0] and "tiep theo" in cac[-2] and cac[-1] == "data: [DONE]", cac
        giua = cac[1:-2]
        assert giua and all(d == ": keep-alive" for d in giua), cac
        assert r.text.count("bat dau") == 1 and r.text.count("tiep theo") == 1
        assert r.text.endswith("data: [DONE]\n\n")


@pytest.mark.parametrize("gia_tri,mong", [
    ("0", 15.0), ("-1", 15.0), ("abc", 15.0), ("nan", 15.0), ("inf", 15.0), ("-inf", 15.0), ("", 15.0),
    ("2.5", 2.5),
])
def test_sse_giu_giay_bien_moi_truong_xau_ve_mac_dinh(monkeypatch, gia_tri, mong):
    from app.tuyen import sse_giu_giay

    monkeypatch.setenv("WEB2API_SSE_GIU_GIAY", gia_tri)
    assert sse_giu_giay() == mong
    monkeypatch.delenv("WEB2API_SSE_GIU_GIAY")
    assert sse_giu_giay() == 15.0


def test_sse_huy_khi_khach_ngat():
    """Khi stream bị huỷ giữa chừng (khách ngắt kết nối), generator và tác vụ ngắt."""
    from app.tuyen import _luong_sse_chuan_hoa

    da_huy = False

    async def module_stream():
        nonlocal da_huy
        try:
            yield b'data: {"id": "c1"}\n\n'
            await asyncio.sleep(10.0)
            yield b'data: {"id": "c2"}\n\n'
        except asyncio.CancelledError:
            da_huy = True
            raise

    async def chay():
        gen = _luong_sse_chuan_hoa(module_stream(), 0.05)
        # Lấy khúc đầu
        khuc1 = await anext(gen)
        assert b"c1" in khuc1

        # Giả lập khách ngắt kết nối bằng cách huỷ coroutine tiêu thụ
        async def tieu_thu():
            async for _ in gen:
                pass

        task = asyncio.create_task(tieu_thu())
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(chay())
    assert da_huy is True


# ───────────── 3. GET /v1/models/{model} ─────────────


def test_models_retrieve_thanh_cong(du_lieu):
    """SDK client.models.retrieve gọi GET /v1/models/{model} (hỗ trợ dấu /)."""
    _tao_module_gia(
        du_lieu / "data" / "modules",
        "mod-retr",
        "retr",
        [
            {"id": "retr/gemini-flash", "owned_by": "test", "kha_nang": ["chat"]},
            {"id": "retr/sub/deep-model", "owned_by": "test", "kha_nang": ["chat"]},
        ],
    )
    with TestClient(app) as c:
        c.post("/api/modules/mod-retr/cai", json={"cau_hinh": {}}, headers=QT)

        # Retrieve model đơn
        r = c.get("/v1/models/retr/gemini-flash", headers=QT)
        assert r.status_code == 200
        assert r.json()["id"] == "retr/gemini-flash"
        assert r.json()["owned_by"] == "test"

        # Retrieve model có nhiều dấu / (path converter)
        r2 = c.get("/v1/models/retr/sub/deep-model", headers=QT)
        assert r2.status_code == 200
        assert r2.json()["id"] == "retr/sub/deep-model"


def test_models_retrieve_khong_ton_tai(du_lieu):
    """Model không tồn tại -> 404 model_not_found khuôn OpenAI."""
    _tao_module_gia(
        du_lieu / "data" / "modules",
        "mod-retr2",
        "r2",
        [{"id": "r2/model-1", "kha_nang": ["chat"]}],
    )
    with TestClient(app) as c:
        c.post("/api/modules/mod-retr2/cai", json={"cau_hinh": {}}, headers=QT)
        r = c.get("/v1/models/r2/khong-ton-tai", headers=QT)
        assert r.status_code == 404
        j = r.json()
        assert j["error"]["code"] == "model_not_found"
        assert "không tồn tại" in j["error"]["message"]


def test_models_retrieve_model_da_tat(du_lieu):
    """Model đã bị tắt trong Hệ thống -> AI trả 404 model_not_found."""
    _tao_module_gia(
        du_lieu / "data" / "modules",
        "mod-retr3",
        "r3",
        [{"id": "r3/model-1", "kha_nang": ["chat"]}],
    )
    kho.luu_cai_dat_model_module("mod-retr3", tat=["r3/model-1"], mac_dinh={})
    with TestClient(app) as c:
        c.post("/api/modules/mod-retr3/cai", json={"cau_hinh": {}}, headers=QT)
        r = c.get("/v1/models/r3/model-1", headers=QT)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "model_not_found"


def test_models_retrieve_khoa_khach_thieu_pham_vi(du_lieu):
    """Khoá khách chỉ có phạm vi 'anh' gọi retrieve model 'chat' -> 404 model_not_found."""
    _tao_module_gia(
        du_lieu / "data" / "modules",
        "mod-retr4",
        "r4",
        [{"id": "r4/chat-model", "kha_nang": ["chat"]}],
    )
    with TestClient(app) as c:
        c.post("/api/modules/mod-retr4/cai", json={"cau_hinh": {}}, headers=QT)
        # Tạo khoá khách chỉ có phạm vi 'anh'
        res_khoa = c.post("/api/khoa-khach", json={"nhan": "Khach Test", "pham_vi": ["anh"]}, headers=QT)
        assert res_khoa.status_code == 200
        khoa_khach = res_khoa.json()["khoa"]["khoa"]
        KHACH = {"Authorization": f"Bearer {khoa_khach}"}

        # Gọi retrieve model chat -> bị lọc khỏi danh sách -> 404
        r = c.get("/v1/models/r4/chat-model", headers=KHACH)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "model_not_found"


# ───────────── 4. X-Request-ID ─────────────


def test_x_request_id_hop_le_va_ghi_ma_yeu_cau(du_lieu):
    """x-request-id hợp lệ (<=128 ký tự, [A-Za-z0-9._-]) -> giữ nguyên, trả lại header và ghi ma_yeu_cau vào nhật ký."""
    _tao_module_gia(
        du_lieu / "data" / "modules",
        "mod-reqid",
        "reqmod",
        [{"id": "reqmod/model-1", "kha_nang": ["chat"]}],
    )
    with TestClient(app) as c:
        c.post("/api/modules/mod-reqid/cai", json={"cau_hinh": {}}, headers=QT)
        req_id_hop_le = "my-req.123_abc"
        r = c.get("/v1/models", headers={**QT, "x-request-id": req_id_hop_le})
        assert r.status_code == 200
        assert r.headers.get("x-request-id") == req_id_hop_le

        # Kiểm tra nhật ký cuoc_goi
        danh_sach = nhat_ky.doc(kenh="cuoc_goi")
        dong = next((d for d in danh_sach if d.get("them", {}).get("ma_yeu_cau") == req_id_hop_le), None)
        assert dong is not None
        assert dong["them"]["ma_yeu_cau"] == req_id_hop_le
        assert dong["them"]["request_id"] == req_id_hop_le


def test_x_request_id_khong_hop_le_bi_thay_the(du_lieu):
    """x-request-id không hợp lệ (ký tự đặc biệt, dấu cách, hoặc quá 128 ký tự) -> sinh mới, không ghi giá trị khách gửi."""
    _tao_module_gia(
        du_lieu / "data" / "modules",
        "mod-reqid2",
        "reqmod2",
        [{"id": "reqmod2/model-1", "kha_nang": ["chat"]}],
    )
    with TestClient(app) as c:
        c.post("/api/modules/mod-reqid2/cai", json={"cau_hinh": {}}, headers=QT)
        req_id_khong_hop_le = "bad request id with spaces & symbols @#$!"
        r = c.get("/v1/models", headers={**QT, "x-request-id": req_id_khong_hop_le})
        assert r.status_code == 200
        id_moi = r.headers.get("x-request-id")
        assert id_moi != req_id_khong_hop_le
        assert id_moi.startswith("req.")

        # Nhật ký chỉ ghi id mới, không chứa chuỗi không hợp lệ
        danh_sach = nhat_ky.doc(kenh="cuoc_goi")
        dong = next((d for d in danh_sach if d.get("them", {}).get("ma_yeu_cau") == id_moi), None)
        assert dong is not None
        assert req_id_khong_hop_le not in json.dumps(dong)


# ───────────── 5. Bỏ bắt buộc chat ─────────────


def test_module_khong_co_chat_van_nap_duoc_va_chat_loi_chuan(du_lieu):
    """Module chỉ có 'nghe' (không có 'chat') nạp bình thường; gọi /v1/chat/completions -> 501 capability_not_supported (không 500)."""
    id_ = "nghe-only"
    tien_to = "audio"
    _tao_module_gia(
        du_lieu / "data" / "modules",
        id_,
        tien_to,
        models=[{"id": "audio/whisper-1", "kha_nang": ["nghe"]}],
        cung_cap=["nghe"],
        co_chat=False,
        co_nghe=True,
    )
    with TestClient(app) as c:
        # Module nạp thành công trong catalog
        ds_catalog = c.get("/api/modules/catalog", headers=QT).json()["modules"]
        assert any(m["id"] == id_ for m in ds_catalog)

        # Cài đặt module thành công
        r_cai = c.post(f"/api/modules/{id_}/cai", json={"cau_hinh": {}}, headers=QT)
        assert r_cai.status_code == 200
        assert r_cai.json()["ok"] is True

        # Module có mặt trong /v1/models
        ds_models = c.get("/v1/models", headers=QT).json()["data"]
        assert any(m["id"] == "audio/whisper-1" for m in ds_models)

        # Gọi /v1/chat/completions tới model này -> lỗi chuẩn 501 capability_not_supported, không phải 500
        r_chat = c.post(
            "/v1/chat/completions",
            json={"model": "audio/whisper-1", "messages": [{"role": "user", "content": "alo"}]},
            headers=QT,
        )
        assert r_chat.status_code == 501
        j_err = r_chat.json()
        assert j_err["error"]["code"] == "capability_not_supported"
        assert "không hỗ trợ" in j_err["error"]["message"]


# ───────────── Bổ sung sau soát độc lập ─────────────

KEEP = b": keep-alive\n\n"
TOOL = (b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"c","type":"function",'
        b'"function":{"name":"f","arguments":""}}]},"finish_reason":null}]}\n\n')


def _module_tu_ma(goc: Path, id_: str, tien_to: str, cung_cap: list[str], than_lop: str, loai: str = "ai") -> None:
    """Module giả: vòng đời tối thiểu + `than_lop` (các phương thức thêm, viết ở cột 0)."""
    thu_muc = goc / id_
    thu_muc.mkdir(parents=True, exist_ok=True)
    manifest = {"id": id_, "ten": f"Module {id_}", "mo_ta": "giả", "phien_ban": "1.0.0", "hang": "A", "loai": loai,
                "giao_thuc": "openai", "tien_to": tien_to, "cung_cap": cung_cap, "truong_cau_hinh": [],
                "tai_nguyen": {"mang_ra": []}}
    (thu_muc / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    goc_lop = textwrap.dedent("""\
        def nap(self, cfg):
            pass
        async def cai(self, cfg):
            return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": cfg}
        async def suc_khoe(self):
            return {"ok": True, "khoa": []}
        async def dung(self):
            pass
        """)
    ma = ("import asyncio, json\n\nclass Module:\n    def __init__(self, m):\n        self.manifest = m\n"
          + textwrap.indent(goc_lop + textwrap.dedent(than_lop), "    "))
    (thu_muc / "module.py").write_text(ma, encoding="utf-8")


CHAT_OK = """
async def models(self):
    return [{"id": self.manifest["tien_to"] + "/m", "kha_nang": ["chat"]}]
async def chat(self, body, stream):
    return 200, {"content-type": "application/json"}, json.dumps(
        {"choices": [{"index": 0, "message": {"role": "assistant", "content": "chao"}, "finish_reason": "stop"}]}
    ).encode()
"""


# ── Điểm 1: định tuyến chat theo phương thức CÓ THẬT, không theo lời khai `cung_cap` ──

@pytest.mark.parametrize("cung_cap,them", [
    ([], ""),
    (["anh"], "async def anh(self, body):\n    return 200, {}, b'{}'\n"),
])
def test_chat_chay_khi_co_ham_chat_du_cung_cap_khong_khai(du_lieu, cung_cap, them):
    """Hợp đồng lõi (đầu app/kham_pha.py): định tuyến /v1 dựa vào phương thức CÓ THẬT, không dựa lời khai. Module có
    `async chat` mà `cung_cap` không khai «chat» vẫn trò chuyện được (trước đây chạy — không được hồi quy)."""
    _module_tu_ma(du_lieu / "data" / "modules", "khai-thieu", "kt", cung_cap, CHAT_OK + them)
    with TestClient(app) as c:
        assert c.post("/api/modules/khai-thieu/cai", json={"cau_hinh": {}}, headers=QT).status_code == 200
        r = c.post("/v1/chat/completions", json={"model": "kt/m", "messages": []}, headers=QT)
        assert r.status_code == 200, r.text
        assert r.json()["choices"][0]["message"]["content"] == "chao"


def test_chat_khong_co_ham_chat_la_501_chuan(du_lieu):
    _module_tu_ma(du_lieu / "data" / "modules", "khong-chat", "kc", ["chat"],
                  'async def models(self):\n    return [{"id": "kc/m"}]\n')
    with TestClient(app) as c:
        c.post("/api/modules/khong-chat/cai", json={"cau_hinh": {}}, headers=QT)
        r = c.post("/v1/chat/completions", json={"model": "kc/m", "messages": []}, headers=QT)
        assert r.status_code == 501 and r.json()["error"]["code"] == "capability_not_supported", r.text


# ── Điểm 2: đóng luồng module khi khách ngắt — mọi đường thoát của generator ──

class _LuongGia:
    """Iterator module giả dạng LỚP: đếm aclose, đếm lần bị huỷ khi đang chờ khúc kế."""

    def __init__(self, khuc: list[bytes], het: bool = False):
        self.khuc = list(khuc)
        self.het = het
        self.so_aclose = 0
        self.bi_huy = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.khuc:
            return self.khuc.pop(0)
        if self.het:
            raise StopAsyncIteration
        try:
            await asyncio.Event().wait()  # im lặng mãi (mô hình suy luận lâu)
        except asyncio.CancelledError:
            self.bi_huy += 1
            raise

    async def aclose(self):
        self.so_aclose += 1


def _chi_con_tac_vu_nay() -> bool:
    return asyncio.all_tasks() == {asyncio.current_task()}


def test_sse_dong_generator_o_yield_keep_alive_thi_huy_cho_va_dong_luong_module():
    """Khách chậm/ngắt khiến Starlette ĐÓNG generator khi nó đang dừng ở `yield` (GeneratorExit, không phải
    CancelledError): tác vụ đang chờ khúc kế phải bị huỷ và aclose() của luồng module được gọi đúng một lần."""
    from app.tuyen import _luong_sse_chuan_hoa

    async def chay():
        luong = _LuongGia([b'data: {"a":1}\n\n'])
        gen = _luong_sse_chuan_hoa(luong, 0.02)
        assert await anext(gen) == b'data: {"a":1}\n\n'
        assert await anext(gen) == KEEP  # đang dừng ở yield keep-alive, tác vụ chờ khúc kế còn treo
        await gen.aclose()
        assert luong.bi_huy == 1 and luong.so_aclose == 1
        assert _chi_con_tac_vu_nay()

    asyncio.run(chay())


def test_sse_khach_ngat_luc_cho_thi_huy_va_dong_luong_module():
    from app.tuyen import _luong_sse_chuan_hoa

    async def chay():
        luong = _LuongGia([b'data: {"a":1}\n\n'])
        gen = _luong_sse_chuan_hoa(luong, 10.0)

        async def tieu_thu():
            async for _ in gen:
                pass

        t = asyncio.create_task(tieu_thu())
        await asyncio.sleep(0.05)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        assert luong.bi_huy == 1 and luong.so_aclose == 1
        assert _chi_con_tac_vu_nay()

    asyncio.run(chay())


def test_sse_khach_ngat_kieu_starlette_anyio_van_dong_luong_module():
    """StreamingResponse của Starlette huỷ bằng cancel scope của anyio — huỷ LẶP LẠI ở mọi await còn trong scope.
    Việc dọn (huỷ tác vụ chờ, aclose luồng module) vẫn phải chạy tới cùng."""
    import anyio

    from app.tuyen import _luong_sse_chuan_hoa

    async def chay():
        luong = _LuongGia([b'data: {"a":1}\n\n'])

        async def tieu_thu():
            async for _ in _luong_sse_chuan_hoa(luong, 0.01):
                pass

        async with anyio.create_task_group() as tg:
            tg.start_soon(tieu_thu)
            await asyncio.sleep(0.05)
            tg.cancel_scope.cancel()
        for _ in range(20):  # việc dọn được che chắn chạy nốt ở tác vụ riêng
            if luong.so_aclose:
                break
            await asyncio.sleep(0.01)
        assert luong.bi_huy == 1 and luong.so_aclose == 1

    asyncio.run(chay())


def test_sse_doc_het_thi_dong_luong_module_mot_lan():
    from app.tuyen import _luong_sse_chuan_hoa

    async def chay():
        luong = _LuongGia([b'data: {"a":1}\n\n', b"data: [DONE]\n\n"], het=True)
        ra = [k async for k in _luong_sse_chuan_hoa(luong, 10.0)]
        assert ra == [b'data: {"a":1}\n\n', b"data: [DONE]\n\n"]
        assert luong.so_aclose == 1 and luong.bi_huy == 0
        assert _chi_con_tac_vu_nay()

    asyncio.run(chay())


# ── Điểm 4: lõi tự đệm theo RANH GIỚI SỰ KIỆN SSE, không dựa cách module chia khúc ──

def _chay_luong(cac_khuc: list, giu: float = 0.02) -> list[bytes]:
    """`cac_khuc`: bytes là một khúc module trả; số là thời gian im lặng (giây) trước khúc kế."""
    from app.tuyen import _luong_sse_chuan_hoa

    async def module():
        for k in cac_khuc:
            if isinstance(k, (int, float)):
                await asyncio.sleep(k)
            else:
                yield k

    async def chay():
        return [k async for k in _luong_sse_chuan_hoa(module(), giu)]

    return asyncio.run(chay())


def _finish(su_kien: bytes):
    return json.loads(su_kien.strip()[len(b"data:"):])["choices"][0]["finish_reason"]


def test_sse_khuc_cat_giua_dong_data_van_chuan_hoa_va_khong_chen_keep_alive():
    ra = _chay_luong([TOOL, b'data: {"choices":[{"index":0,"finish_re', 0.15,
                      b'ason":"stop","delta":{}}]}\n\n', b"data: [DONE]\n\n"])
    assert KEEP not in ra, ra  # im lặng GIỮA một sự kiện: chen chú thích vào là làm hỏng dòng data
    assert len(ra) == 3 and ra[0] == TOOL and ra[2] == b"data: [DONE]\n\n"
    assert _finish(ra[1]) == "tool_calls"


def test_sse_hai_su_kien_trong_mot_khuc_phat_rieng_tung_su_kien():
    stop = b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    ra = _chay_luong([TOOL + stop])
    assert ra[0] == TOOL and len(ra) == 2 and _finish(ra[1]) == "tool_calls"


@pytest.mark.parametrize("dong", [b"\n", b"\r\n"])
def test_sse_su_kien_cat_o_ranh_gioi_cho_du_roi_moi_phat(dong):
    """Dòng data đến trước, dòng trống kết thúc sự kiện đến sau (kể cả cắt giữa «\\r\\n\\r\\n»): không keep-alive
    chen giữa, sự kiện phát đúng một lần khi đủ ranh giới; byte giữ nguyên."""
    su_kien = b'data: {"choices":[{"index":0,"delta":{"content":"x"},"finish_reason":null}]}' + dong
    khuc = [su_kien, 0.1, dong] if dong == b"\n" else [su_kien + b"\r", 0.1, b"\n"]
    ra = _chay_luong(khuc)
    assert ra == [su_kien + dong]


def test_sse_dong_dong_kieu_aiter_lines_giu_byte_va_keep_alive_giua_su_kien():
    """Khuôn của chuyen_tiep._chuyen_dong (mỗi khúc một dòng, dòng trống là khúc riêng): byte ghép lại y nguyên;
    keep-alive chỉ ở giữa hai sự kiện."""
    vao = [b'data: {"id":"1"}\n', b"\n", 0.1, b'data: {"id":"2"}\n', b"\n", b"data: [DONE]\n", b"\n"]
    ra = _chay_luong(vao)
    assert ra[0] == b'data: {"id":"1"}\n\n' and ra[-1] == b"data: [DONE]\n\n"
    assert KEEP in ra
    assert b"".join(k for k in ra if k != KEEP) == b"".join(k for k in vao if isinstance(k, bytes))


def test_sse_khuc_do_dang_luc_ket_thuc_phat_nguyen_van():
    ra = _chay_luong([b'data: {"id":"1"}\n\n', b'data: {"choices":[{"finish_reason":"stop"'])
    assert ra == [b'data: {"id":"1"}\n\n', b'data: {"choices":[{"finish_reason":"stop"']


# ── Điểm 3 + 5 + 9: chuẩn hoá finish_reason theo cấu trúc ──

def _su_kien(obj) -> bytes:
    return b"data: " + json.dumps(obj).encode() + b"\n\n"


@pytest.mark.parametrize("ly_do", ["length", "content_filter", "tool_calls"])
def test_stream_finish_reason_khac_stop_giu_nguyen(ly_do):
    cuoi = _su_kien({"choices": [{"index": 0, "delta": {}, "finish_reason": ly_do}]})
    ra = _chay_luong([TOOL, cuoi])
    assert ra[1] == cuoi


@pytest.mark.parametrize("ly_do", ["length", "content_filter"])
def test_chat_thuong_finish_reason_length_giu_nguyen(ly_do):
    from app.tuyen import _chuan_hoa_chat_thuong

    than = json.dumps({"choices": [{"index": 0, "finish_reason": ly_do, "message": {
        "tool_calls": [{"id": "c", "type": "function", "function": {"name": "f", "arguments": '{"a": '}}]}}]}).encode()
    assert _chuan_hoa_chat_thuong(than) == than


@pytest.mark.parametrize("co_truong", [True, False])
def test_chat_thuong_finish_reason_thieu_hoac_null_thanh_tool_calls(co_truong):
    """Thân không stream là trọn một lượt: finish_reason thiếu/null cùng tool_calls → 'tool_calls'."""
    from app.tuyen import _chuan_hoa_chat_thuong

    choice = {"index": 0, "message": {"tool_calls": [{"id": "c", "type": "function",
                                                      "function": {"name": "f", "arguments": "{}"}}]}}
    if co_truong:
        choice["finish_reason"] = None
    ra = json.loads(_chuan_hoa_chat_thuong(json.dumps({"choices": [choice]}).encode()))
    assert ra["choices"][0]["finish_reason"] == "tool_calls"


@pytest.mark.parametrize("than", [b"khong phai json", b"[1, 2]", b'{"choices": "x"}', b"\xff\xfe"])
def test_chat_thuong_than_la_chuyen_nguyen_van(than):
    from app.tuyen import _chuan_hoa_chat_thuong

    assert _chuan_hoa_chat_thuong(than) == than


def test_stream_n_lon_hon_1_chi_choice_co_tool_bi_doi():
    ra = _chay_luong([
        _su_kien({"choices": [{"index": 1, "delta": {"tool_calls": [{"index": 0, "id": "c"}]}, "finish_reason": None}]}),
        _su_kien({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"},
                              {"index": 1, "delta": {}, "finish_reason": "stop"}]}),
    ])
    cuoi = json.loads(ra[1][len(b"data: "):])["choices"]
    assert [c["finish_reason"] for c in cuoi] == ["stop", "tool_calls"]


def test_stream_tool_calls_va_finish_cung_khuc():
    ra = _chay_luong([_su_kien({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "c"}]},
                                             "finish_reason": "stop"}]})])
    assert _finish(ra[0]) == "tool_calls"


def test_stream_khuc_usage_choices_rong_chuyen_nguyen_van():
    usage = _su_kien({"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}})
    ra = _chay_luong([TOOL, usage, b"data: [DONE]\n\n"])
    assert ra == [TOOL, usage, b"data: [DONE]\n\n"]


@pytest.mark.parametrize("idx", [[0], {"a": 1}, True, "0", 1.0])
def test_stream_index_la_khong_nem_va_chuyen_nguyen_van(idx):
    from app.tuyen import _chuan_hoa_khuc_stream

    khuc = _su_kien({"choices": [{"index": idx, "delta": {"tool_calls": [{"id": "c"}]}, "finish_reason": "stop"}]})
    assert _chuan_hoa_khuc_stream(khuc, set()) == khuc


def test_stream_dong_crlf_giu_cr_khi_ghi_lai():
    from app.tuyen import _chuan_hoa_khuc_stream

    khuc = b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"id":"c"}]},"finish_reason":"stop"}]}\r\n\r\n'
    ra = _chuan_hoa_khuc_stream(khuc, set())
    assert ra.endswith(b"}\r\n\r\n") and b'"tool_calls"' in ra.split(b"finish_reason")[1]


# ── Điểm 6: GET /v1/models/{model} — xác thực, không phản chiếu nguyên văn, module từ xa ──

def test_models_retrieve_doi_khoa(du_lieu):
    with TestClient(app) as c:
        for tieu_de in ({}, {"Authorization": "Bearer khoa-sai-0000"}, {"x-api-key": KHOA_QT}):
            r = c.get("/v1/models/x/y", headers=tieu_de)
            assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key", r.text


def test_models_retrieve_khong_phan_chieu_model_dai_hay_ky_tu_dieu_khien(du_lieu):
    with TestClient(app) as c:
        dai = "a/" + "Z" * 5000
        r = c.get("/v1/models/" + dai, headers=QT)
        assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"
        assert "Z" * 200 not in r.text and len(r.text) < 1000
        r2 = c.get("/v1/models/a/b%0Ac%1B%5B31m", headers=QT)
        assert r2.status_code == 404
        tin = r2.json()["error"]["message"]
        assert "\n" not in tin and "\x1b" not in tin and "\\n" not in tin and "\\x1b" not in tin


def test_models_retrieve_khoa_khach_co_pham_vi_thay_duoc(du_lieu):
    _tao_module_gia(du_lieu / "data" / "modules", "mod-retr5", "r5", [{"id": "r5/chat-model", "kha_nang": ["chat"]}])
    with TestClient(app) as c:
        c.post("/api/modules/mod-retr5/cai", json={"cau_hinh": {}}, headers=QT)
        khoa = c.post("/api/khoa-khach", json={"nhan": "K", "pham_vi": ["chat"]}, headers=QT).json()["khoa"]["khoa"]
        r = c.get("/v1/models/r5/chat-model", headers={"Authorization": f"Bearer {khoa}"})
        assert r.status_code == 200 and r.json()["id"] == "r5/chat-model"
        assert r.headers.get("x-web2api-module") == "r5"


def test_models_retrieve_module_tu_xa(du_lieu, monkeypatch):
    from gia import MayChuThat, OpenAIGia

    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    khoa_xa = "sk-lan-0000000000000000000000000000xyzw"
    g = OpenAIGia(khoa=khoa_xa)
    with MayChuThat(g.app) as s, TestClient(app) as c:
        r = c.post("/api/modules/tu-xa", json={"id": "gpu-lan", "ten": "LAN", "tien_to": "lan",
                                                "base_url": s.url + "/v1", "api_key": khoa_xa}, headers=QT)
        assert r.status_code == 200, r.text
        r1 = c.get("/v1/models/lan/qwen-3b", headers=QT)
        assert r1.status_code == 200 and r1.json()["id"] == "lan/qwen-3b"
        assert r1.headers.get("x-web2api-module") == "lan"
        assert khoa_xa not in r1.text
        r2 = c.get("/v1/models/lan/khong-co", headers=QT)
        assert r2.status_code == 404 and r2.json()["error"]["code"] == "model_not_found"


# ── Điểm 7: phương thức khác GET trên /v1/models/<x> ──

@pytest.mark.parametrize("pt", ["POST", "PUT", "DELETE", "PATCH"])
def test_models_x_phuong_thuc_khac_la_404_unknown_endpoint(du_lieu, pt):
    """Route `/models/{model:path}` nuốt MỌI hậu tố, nên khớp-một-phần của nó không chứng minh đường tồn tại
    (xem duong_v1_khong_co); giữ 404 như trước khi có đường retrieve (test_web2api_bien_loi ghim PATCH)."""
    with TestClient(app) as c:
        r = c.request(pt, "/v1/models/x", headers=QT)
        assert r.status_code == 404 and r.json()["error"]["code"] == "unknown_endpoint", r.text


# ── Điểm 8: `models` chỉ bắt buộc khi khai khả năng định tuyến theo model ──

def test_module_khong_khai_kha_nang_model_khong_can_models(du_lieu):
    """`loai: "ai"` không còn là lý do bắt `models` (mã loại là mã MỞ); thiếu `models` thì các đường liệt kê model
    trả danh sách rỗng/404 model_not_found — không 502 lộ tên ngoại lệ."""
    _module_tu_ma(du_lieu / "data" / "modules", "chi-vong-doi", "cvd", [], "")
    with TestClient(app) as c:
        ds = c.get("/api/modules/catalog", headers=QT).json()
        assert any(m["id"] == "chi-vong-doi" for m in ds["modules"]), ds["loi"]
        assert c.post("/api/modules/chi-vong-doi/cai", json={"cau_hinh": {}}, headers=QT).status_code == 200
        r = c.get("/api/modules/chi-vong-doi/model", headers=QT)
        assert r.status_code == 200 and r.json()["models"] == [], r.text
        assert "AttributeError" not in r.text
        r2 = c.put("/api/modules/chi-vong-doi/model", json={"tat": ["cvd/x"]}, headers=QT)
        assert r2.status_code == 200 and "AttributeError" not in r2.text
        assert c.get("/v1/models", headers=QT).status_code == 200
        r3 = c.get("/v1/models/cvd/x", headers=QT)
        assert r3.status_code == 404 and r3.json()["error"]["code"] == "model_not_found"
        r4 = c.post("/v1/chat/completions", json={"model": "cvd/x", "messages": []}, headers=QT)
        assert r4.status_code == 501 and r4.json()["error"]["code"] == "capability_not_supported"


@pytest.mark.parametrize("cung_cap", [["chat"], ["nghe"]])
def test_khai_kha_nang_theo_model_ma_thieu_models_bi_tu_choi(du_lieu, cung_cap):
    ham = {"chat": "async def chat(self, body, stream):\n    return 200, {}, b'{}'\n",
           "nghe": "async def nghe(self, yc):\n    return 200, {}, b'{}'\n"}[cung_cap[0]]
    _module_tu_ma(du_lieu / "data" / "modules", "thieu-models", "tm", cung_cap, ham)
    with TestClient(app) as c:
        ds = c.get("/api/modules/catalog", headers=QT).json()
        assert not any(m["id"] == "thieu-models" for m in ds["modules"])
        assert any("thiếu phương thức: models" in l["ly_do"] for l in ds["loi"]), ds["loi"]


# ── Điểm 9: X-Request-ID — biên độ dài và ký tự cuối ──

def test_x_request_id_dung_128_giu_129_thay(du_lieu):
    # Chèn «.» mỗi 10 ký tự: chuỗi [A-Za-z0-9_-]{20,} bị che trong nhật ký như token, mã này phải hiện nguyên.
    du_128 = ("abcdefghi." * 13)[:128]
    qua_129 = ("abcdefghi." * 13)[:129]
    assert len(du_128) == 128 and len(qua_129) == 129
    with TestClient(app) as c:
        r = c.get("/v1/models", headers={**QT, "x-request-id": du_128})
        assert r.headers["x-request-id"] == du_128
        assert any(d.get("them", {}).get("ma_yeu_cau") == du_128 for d in nhat_ky.doc(kenh="cuoc_goi"))
        r2 = c.get("/v1/models", headers={**QT, "x-request-id": qua_129})
        assert r2.headers["x-request-id"] != qua_129 and r2.headers["x-request-id"].startswith("req.")
        assert qua_129 not in json.dumps(nhat_ky.doc(kenh="cuoc_goi"))


@pytest.mark.parametrize("gia_tri", [b"abc\n", b"abc\r\n", b"a\nb", b"", b"abc def"])
def test_x_request_id_ky_tu_xuong_dong_bi_thay(gia_tri):
    """HTTP thật không chở được «\\n» trong tiêu đề, nên gọi thẳng hàm: «abc\\n» không được coi là «abc»."""
    from app.nhat_ky import _request_id

    ra = _request_id({"headers": [(b"x-request-id", gia_tri)]})
    assert ra.startswith("req.") and ra != gia_tri.decode().strip()


def test_x_request_id_hop_le_khong_bi_cat_khoang_trang_bien():
    from app.nhat_ky import _request_id

    assert _request_id({"headers": [(b"x-request-id", b"abc")]}) == "abc"
