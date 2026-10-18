"""Test NV102-WEB cho module chatgpt-web: model theo đúng thực tế tài khoản.

Kiểm tra:
1. Máy chủ giả GET /backend-api/models trả model mới (gpt-9-xyz) → xuất hiện trong models()
   với id "chatgpt-web/gpt-9-xyz" và nhãn đúng.
2. Chat model mới gpt-9-xyz thành công (200), thân request tới máy chủ giả mang đúng slug.
3. Chat model không có trong danh sách model trả về lỗi 400 rõ ràng.
4. Lọc tài khoản theo model: tài khoản A có gpt-9-pro, tài khoản B chỉ có gpt-4o → yêu cầu
   gpt-9-pro chỉ gửi tới tài khoản A; khi tài khoản A lỗi thì báo lỗi chứ không rơi âm thầm sang B.
5. Dự phòng khi lỗi nguồn: máy chủ giả trả 500 hoặc 401 khi lấy model → giữ bảng cũ,
   không xoá trắng; lam_moi_model ném ngoại lệ khi mọi tài khoản thất bại.
6. Nạp lại khôi phục model_that từ bản ghi đã lưu bền vững.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import types
from pathlib import Path

import httpx
import pytest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "chatgpt-web"
TEN_GOI = "chatgpt_web_nv102_test"


def _tai_goi():
    import sys
    for ten in [t for t in list(sys.modules) if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC)]
    sys.modules[TEN_GOI] = goi
    for ten_tep in ("client", "converter", "module"):
        spec = importlib.util.spec_from_file_location(
            f"{TEN_GOI}.{ten_tep}", THU_MUC / f"{ten_tep}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{TEN_GOI}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return (sys.modules[f"{TEN_GOI}.module"],
            sys.modules[f"{TEN_GOI}.client"],
            sys.modules[f"{TEN_GOI}.converter"])


mod_module, mod_client, mod_converter = _tai_goi()
Module = mod_module.Module
MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


def chay(coro):
    return asyncio.run(coro)


class KhoGia:
    """Kho riêng giả lập để kiểm tra lưu/đọc bản ghi tài khoản."""

    def __init__(self):
        self.du_lieu = {}

    def doc(self, ten: str):
        return self.du_lieu.get(ten)

    def luu(self, ten: str, giatri):
        self.du_lieu[ten] = giatri


def tao_transport_gia(phan_hoi_models=None, phan_hoi_chat=None):
    """Máy chủ giả phục vụ /backend-api/models, /backend-api/me, /backend-api/f/conversation."""
    nhat_ky = []

    def xu_ly(req: httpx.Request):
        auth = req.headers.get("authorization", "")
        token = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        duong = req.url.path

        if duong == "/backend-api/me":
            return httpx.Response(200, json={"email": f"{token}@example.com"})

        if duong == "/backend-api/accounts/check":
            return httpx.Response(200, json={"accounts": {"default": {"plan_type": "plus"}}})

        if duong == "/backend-api/models":
            nhat_ky.append({"loai": "models", "token": token})
            if callable(phan_hoi_models):
                return phan_hoi_models(req, token)
            if isinstance(phan_hoi_models, dict) and token in phan_hoi_models:
                cfg = phan_hoi_models[token]
                if isinstance(cfg, int):
                    return httpx.Response(cfg, text="loi")
                return httpx.Response(200, json=cfg)
            return httpx.Response(200, json={
                "models": [
                    {"slug": "gpt-9-xyz", "title": "GPT 9 XYZ", "description": "Mô hình mới"},
                    {"slug": "gpt-4o", "title": "GPT-4o"},
                ]
            })

        if duong == "/backend-api/f/conversation":
            than = json.loads(req.content.decode("utf-8")) if req.content else {}
            nhat_ky.append({"loai": "chat", "token": token, "than": than})
            if callable(phan_hoi_chat):
                return phan_hoi_chat(req, token, than)
            if isinstance(phan_hoi_chat, dict) and token in phan_hoi_chat:
                code_or_resp = phan_hoi_chat[token]
                if isinstance(code_or_resp, int):
                    return httpx.Response(code_or_resp, text="loi")
            # Trả về SSE OpenAI thành công
            sk = {"message": {"author": {"role": "assistant"},
                              "content": {"content_type": "text", "parts": ["Phản hồi test"]},
                              "status": "finished_successfully",
                              "end_turn": True}}
            dong = f"data: {json.dumps(sk)}\n\ndata: [DONE]\n\n"
            return httpx.Response(200, text=dong, headers={"Content-Type": "text/event-stream"})

        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(xu_ly)
    transport.nhat_ky = nhat_ky
    return transport


def nap_test_module(monkeypatch, transport, tokens):
    def tao_client(self, token):
        return mod_client.ChatGPTWebClient(token, transport=transport)

    monkeypatch.setattr(Module, "_tao_client", tao_client)
    mod = Module(MANIFEST)
    mod.kho = KhoGia()
    mod.nap({"tai_khoan": list(tokens)})
    return mod


def test_1_lay_model_that_xuat_hien_trong_models(monkeypatch):
    """Model mới từ GET /backend-api/models xuất hiện trong models() với id và nhãn đúng."""
    transport = tao_transport_gia(phan_hoi_models={
        "token_1": {
            "models": [
                {"slug": "gpt-9-xyz", "title": "GPT 9 XYZ", "description": "Mô hình mới"},
                {"slug": "gpt-4o", "title": "GPT-4o"},
            ]
        }
    })
    mod = nap_test_module(monkeypatch, transport, ["token_1"])

    # Gọi làm mới model
    chay(mod.lam_moi_model())

    danh_sach = chay(mod.models())
    ids = [m["id"] for m in danh_sach]
    assert "chatgpt-web/gpt-9-xyz" in ids
    item = next(m for m in danh_sach if m["id"] == "chatgpt-web/gpt-9-xyz")
    assert item["nhan"] == "GPT 9 XYZ"
    assert len(item["available_cho"]) == 1


def test_2_chat_model_moi_thanh_cong(monkeypatch):
    """Chat với model mới gpt-9-xyz thành công và truyền đúng slug xuống máy chủ."""
    transport = tao_transport_gia(phan_hoi_models={
        "token_1": {
            "models": [
                {"slug": "gpt-9-xyz", "title": "GPT 9 XYZ"},
            ]
        }
    })
    mod = nap_test_module(monkeypatch, transport, ["token_1"])
    chay(mod.lam_moi_model())

    ma, _, raw = chay(mod.chat({
        "model": "chatgpt-web/gpt-9-xyz",
        "messages": [{"role": "user", "content": "Alo"}],
    }, stream=False))

    assert ma == 200
    # Kiểm tra máy chủ nhận đúng slug gpt-9-xyz
    chat_reqs = [r for r in transport.nhat_ky if r["loai"] == "chat"]
    assert len(chat_reqs) == 1
    assert chat_reqs[0]["than"].get("model") == "gpt-9-xyz"


def test_3_chat_model_khong_co_tra_400(monkeypatch):
    """Chat với model không có trong danh sách hỗ trợ trả 400 rõ ràng."""
    transport = tao_transport_gia()
    mod = nap_test_module(monkeypatch, transport, ["token_1"])

    ma, _, raw = chay(mod.chat({
        "model": "chatgpt-web/khong-ton-tai-xyz",
        "messages": [{"role": "user", "content": "Alo"}],
    }, stream=False))

    assert ma == 400
    du_lieu = json.loads(raw.decode("utf-8"))
    assert du_lieu["error"]["code"] in (400, "model_not_found")
    assert "khong-ton-tai-xyz" in du_lieu["error"]["message"]


def test_4_loc_tai_khoan_theo_model_va_khong_roi_sang_tai_khoan_khac(monkeypatch):
    """Tài khoản A có gpt-9-pro, tài khoản B chỉ có gpt-4o.
    Yêu cầu gpt-9-pro chỉ gửi tới A; khi A lỗi thì báo lỗi, không rơi sang B.
    """
    transport = tao_transport_gia(
        phan_hoi_models={
            "token_A": {"models": [{"slug": "gpt-9-pro", "title": "GPT 9 Pro"}]},
            "token_B": {"models": [{"slug": "gpt-4o", "title": "GPT-4o"}]},
        },
        phan_hoi_chat={
            "token_A": 429,  # A lỗi hạn mức
            "token_B": 200,  # B khoẻ nhưng không có model này
        }
    )
    mod = nap_test_module(monkeypatch, transport, ["token_A", "token_B"])
    chay(mod.lam_moi_model())

    # Kiểm tra danh sách model: gpt-9-pro chỉ available cho token_A
    danh_sach = chay(mod.models())
    item_pro = next(m for m in danh_sach if m["id"] == "chatgpt-web/gpt-9-pro")
    assert len(item_pro["available_cho"]) == 1

    # Chat gpt-9-pro: gửi đến A, A bị 429 → kết quả 429, KHÔNG fallback sang B
    ma, _, _ = chay(mod.chat({
        "model": "chatgpt-web/gpt-9-pro",
        "messages": [{"role": "user", "content": "Alo"}],
    }, stream=False))
    assert ma == 429

    chat_reqs = [r for r in transport.nhat_ky if r["loai"] == "chat"]
    # Đảm bảo chỉ có token_A nhận request, token_B không bao giờ bị gọi
    tokens_nhan = [r["token"] for r in chat_reqs]
    assert "token_B" not in tokens_nhan


def test_5_du_phong_khi_loi_nguon(monkeypatch):
    """Máy chủ giả trả 500 khi lấy danh sách model → giữ nguyên bảng dự phòng,
    lam_moi_model ném RuntimeError khi không tài khoản nào thành công."""
    transport = tao_transport_gia(phan_hoi_models={"token_1": 500})
    mod = nap_test_module(monkeypatch, transport, ["token_1"])

    with pytest.raises(RuntimeError):
        chay(mod.lam_moi_model())

    # Danh sách model không bị trống mà giữ nguyên bảng dự phòng
    danh_sach = chay(mod.models())
    assert len(danh_sach) > 0
    ids = [m["id"] for m in danh_sach]
    assert "chatgpt-web/4o" in ids


def test_6_nap_lai_khoi_phuc_model_that(monkeypatch):
    """Khôi phục model_that từ bản ghi đã lưu trong kho riêng mà không cần gọi lại mạng."""
    transport = tao_transport_gia()
    mod = nap_test_module(monkeypatch, transport, ["token_1"])

    # Giả lập bản ghi đã lưu trước đó trong kho
    khoa = mod._khoa_tu_token("token_1")
    mod.kho.luu("tai_khoan", {
        "ban_ghi": {
            khoa: {
                "khoa": khoa,
                "email": "test@example.com",
                "goi": "plus",
                "trang_thai": "hoat_dong",
                "model_that": [
                    {"slug": "gpt-9-xyz", "nhan": "GPT 9 XYZ", "mo_ta": "Mô hình khôi phục"}
                ]
            }
        }
    })

    # Dựng một thể hiện mới với cùng kho
    mod_moi = Module(MANIFEST)
    mod_moi.kho = mod.kho
    mod_moi.nap({"tai_khoan": ["token_1"]})

    danh_sach = chay(mod_moi.models())
    ids = [m["id"] for m in danh_sach]
    assert "chatgpt-web/gpt-9-xyz" in ids
    item = next(m for m in danh_sach if m["id"] == "chatgpt-web/gpt-9-xyz")
    assert item["nhan"] == "GPT 9 XYZ"
