"""Test NV211 — sentinel + PoW cho module chatgpt-web.

Máy chủ GIẢ dựng ĐÚNG hành vi đo thật ngày 21/10/2026:
- GET /backend-api/me → 200; GET /backend-api/models → 200
- POST /backend-api/sentinel/chat-requirements → 200 với proofofwork (seed/difficulty)
- POST /backend-api/f/conversation → 403 "Unusual activity" nếu thiếu/sai tiêu đề
  sentinel, 200 SSE nếu đủ tiêu đề hợp lệ (proof được KIỂM THẬT bằng sha3_512).

Đỏ→xanh qua LÕI THẬT (Module thật + ChatGPTWebClient thật + transport giả).
"""

import asyncio
import base64
import hashlib
import importlib.util
import json
import logging
import sys
import types
from pathlib import Path

import httpx
import pytest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "chatgpt-web"
TEN_GOI = "chatgpt_web_nv211_mod_test"


def _tai_goi():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC)]
    sys.modules[TEN_GOI] = goi
    for ten_tep in ("client", "module", "sentinel"):
        try:
            duong = THU_MUC / f"{ten_tep}.py"
            if not duong.exists():
                continue
            spec = importlib.util.spec_from_file_location(
                f"{TEN_GOI}.{ten_tep}", duong)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[f"{TEN_GOI}.{ten_tep}"] = mod
            spec.loader.exec_module(mod)
        except FileNotFoundError:
            pass
    return (sys.modules.get(f"{TEN_GOI}.module"),
            sys.modules.get(f"{TEN_GOI}.client"),
            sys.modules.get(f"{TEN_GOI}.sentinel"))


mod_module, mod_client, _ = _tai_goi()
Module = mod_module.Module

MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))

TOKEN_TOT = "token-tot-nv211"


def chay(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Máy chủ giả
# ---------------------------------------------------------------------------

SEED_GIA = "seed-kiem-thu-nv211"
DO_KHO_GIA = "0fffff"  # dễ, giải được trong vài ms


def _kiem_proof(seed: str, difficulty: str, proof_b64: str) -> bool:
    try:
        raw = proof_b64.encode("ascii")
        digest = hashlib.sha3_512(seed.encode() + raw).digest()
        muc_tieu = bytes.fromhex(difficulty)
        return digest[:len(muc_tieu)] <= muc_tieu
    except Exception:
        return False


def _su_kien(parts, xong=False):
    return {"message": {"author": {"role": "assistant"},
                        "content": {"content_type": "text", "parts": parts},
                        "status": "finished_successfully" if xong else "in_progress",
                        "end_turn": xong}}


def _lay_token(req):
    auth = req.headers.get("authorization", "")
    return auth[len("Bearer "):] if auth.startswith("Bearer ") else ""


class MayChuGia:
    """Trạng thái + bản ghi các yêu cầu đã nhận."""

    def __init__(self, che_do=None):
        self.che_do = che_do or {}
        self.tieu_de_da_nhan = []   # [(path, dict(headers))]
        self.so_lan_hoi_thoai = 0
        self.token_yeu_cau = "req-token-nv211"

    def transport(self):
        def xu_ly(req):
            token = _lay_token(req)
            che = self.che_do.get(token, "ok")
            duong = req.url.path
            if duong == "/backend-api/me":
                if che == "hong":
                    return httpx.Response(401, text="het han")
                return httpx.Response(200, json={"email": f"{token}@example.com"})
            if duong == "/backend-api/models":
                return httpx.Response(200, json={"models": [
                    {"slug": "gpt-5-3-mini", "title": "GPT-5.3 mini"}]})
            if duong == "/backend-api/sentinel/chat-requirements":
                than = json.loads(req.content or b"{}")
                p = than.get("p", "")
                if not (isinstance(p, str) and p.startswith("gAAAAAC")):
                    return httpx.Response(400, text="thieu p")
                if che == "turnstile":
                    return httpx.Response(200, json={
                        "token": self.token_yeu_cau,
                        "proofofwork": {"required": False},
                        "turnstile": {"required": True, "dx": "dx-gia"}})
                return httpx.Response(200, json={
                    "token": self.token_yeu_cau,
                    "proofofwork": {"required": True, "seed": SEED_GIA,
                                    "difficulty": DO_KHO_GIA},
                    "turnstile": {"required": False}})
            if duong == "/backend-api/f/conversation":
                self.so_lan_hoi_thoai += 1
                self.tieu_de_da_nhan.append((duong, dict(req.headers)))
                if che == "hong":
                    return httpx.Response(401, text="het han")
                if che == "chan_ke_ca_sentinel":
                    return httpx.Response(
                        403, json={"detail": "Unusual activity has been detected"})
                td = {k.lower(): v for k, v in req.headers.items()}
                hop_le = (
                    td.get("openai-sentinel-chat-requirements-token") == self.token_yeu_cau
                    and td.get("openai-sentinel-proof-token", "").startswith("gAAAAAB")
                    and _kiem_proof(SEED_GIA, DO_KHO_GIA,
                                    td["openai-sentinel-proof-token"][len("gAAAAAB"):])
                    and td.get("oai-device-id")
                )
                if not hop_le:
                    return httpx.Response(
                        403, json={"detail": "Unusual activity has been detected"})
                dong = "".join(
                    f"data: {json.dumps(_su_kien([t], xong=(t == 'tra-loi-tich-luy')))}\n\n"
                    for t in ("tra-loi-", "tra-loi-tich-luy"))
                return httpx.Response(200, text=dong + "data: [DONE]\n\n",
                                      headers={"Content-Type": "text/event-stream"})
            return httpx.Response(404, text="khong co")
        return httpx.MockTransport(xu_ly)


def nap_module(monkeypatch, che_do, tai_khoan, **cau_hinh_them):
    may_chu = MayChuGia(che_do)
    transport = may_chu.transport()

    def tao_client(self, token):
        return mod_client.ChatGPTWebClient(token, transport=transport)

    monkeypatch.setattr(Module, "_tao_client", tao_client)
    mod = Module(MANIFEST)
    cau_hinh = {"tai_khoan": list(tai_khoan)}
    cau_hinh.update(cau_hinh_them)
    mod.nap(cau_hinh)
    mod._may_chu_gia = may_chu
    return mod


def _client_truc_tiep(che_do, token=TOKEN_TOT):
    may_chu = MayChuGia(che_do)
    client = mod_client.ChatGPTWebClient(token, transport=may_chu.transport())
    client._may_chu_gia = may_chu
    return client


# ---------------------------------------------------------------------------
# Test đỏ
# ---------------------------------------------------------------------------

def test_gui_du_tieu_de_sentinel_truoc_chat(monkeypatch):
    """Chat gửi đủ tiêu đề sentinel/PoW/device-id thì máy chủ giả cho qua (200)."""
    mod = nap_module(monkeypatch, {}, [TOKEN_TOT])
    ma, _td, than = chay(mod.chat(
        {"model": "chatgpt-web/4o-mini",
         "messages": [{"role": "user", "content": "chao"}]}, stream=False))
    assert ma == 200, than[:200]
    assert mod._may_chu_gia.so_lan_hoi_thoai == 1
    _duong, tieu_de = mod._may_chu_gia.tieu_de_da_nhan[-1]
    td = {k.lower(): v for k, v in tieu_de.items()}
    assert td.get("openai-sentinel-chat-requirements-token") == "req-token-nv211"
    assert td.get("openai-sentinel-proof-token", "").startswith("gAAAAAB")
    assert td.get("oai-device-id"), "thiếu oai-device-id ổn định"
    assert td.get("oai-language"), "thiếu oai-language"
    assert "ChatGPTWebClient" not in str(td.get("user-agent", ""))


def test_403_sau_sentinel_la_loi_tam_khong_dot_khoa(monkeypatch):
    """403 ở hội thoại (đã qua init + sentinel) = lỗi TẠM của lượt.

    Không đánh dấu khoá hỏng, không thử khoá khác, trả 403 với thông điệp rõ
    «không phải lỗi token».
    """
    mod = nap_module(monkeypatch, {"token-a": "chan_ke_ca_sentinel"}, ["token-a", "token-b"])
    ma, _td, than = chay(mod.chat(
        {"model": "chatgpt-web/4o-mini",
         "messages": [{"role": "user", "content": "chao"}]}, stream=False))
    assert ma == 403
    noi_dung = than.decode("utf-8") if isinstance(than, bytes) else str(than)
    assert "không phải lỗi token" in noi_dung or "tạm" in noi_dung
    # Khoá KHÔNG bị đánh dấu hỏng, vẫn khoẻ
    tk = mod._tai_khoan[0]
    assert tk.khoe is True
    assert tk.loai_loi != "khoa_hong"
    # Không đốt khoá thứ hai
    assert mod._may_chu_gia.so_lan_hoi_thoai == 1


def test_403_stream_cung_la_loi_tam(monkeypatch):
    mod = nap_module(monkeypatch, {"token-a": "chan_ke_ca_sentinel"}, ["token-a", "token-b"])
    ma, _td, luong = chay(mod.chat(
        {"model": "chatgpt-web/4o-mini",
         "messages": [{"role": "user", "content": "chao"}]}, stream=True))
    assert ma == 200  # SSE mở, lỗi nằm trong chunk

    async def gom():
        return [c async for c in luong]
    cac_chunk = chay(gom())
    van_ban = "".join(c if isinstance(c, str) else c.decode("utf-8", "ignore") for c in cac_chunk)
    assert "403" in van_ban or "tạm" in van_ban
    assert mod._tai_khoan[0].khoe is True


def test_turnstile_yeu_cau_xu_ly_thu_cong():
    """turnstile.required → lỗi rõ «cần xử lý thủ công», KHÔNG tự giải CAPTCHA."""
    client = _client_truc_tiep({"token-t": "turnstile"}, "token-t")
    with pytest.raises(Exception) as ghi:
        chay(client.generate_content(
            [{"role": "user", "content": "chao"}], "chatgpt-web/4o-mini"))
    thong_diep = str(ghi.value).lower()
    assert "thủ công" in thong_diep or "manual" in thong_diep


def test_pow_co_tran_thoi_gian():
    """PoW độ khó không tưởng + trần ngắn → lỗi trong giới hạn, không treo."""
    import time
    bat_dau = time.monotonic()
    with pytest.raises(Exception):
        mod_client.giai_proof_of_work(
            "seed-khong-the", "0000000001", "ua-gia", thoi_gian_toi_da=0.5)
    assert time.monotonic() - bat_dau < 10, "PoW treo quá lâu — thiếu trần"


def test_pow_vector_biet_truoc():
    """PoW giải đúng: digest có tiền tố ≤ difficulty."""
    proof = mod_client.giai_proof_of_work(
        SEED_GIA, DO_KHO_GIA, "ua-gia", thoi_gian_toi_da=10.0)
    assert proof.startswith("gAAAAAB")
    assert _kiem_proof(SEED_GIA, DO_KHO_GIA, proof[len("gAAAAAB"):])


def test_gioi_han_so_khoa_moi_yeu_cau(monkeypatch):
    """Trần số khoá thử mỗi yêu cầu (mặc định cấu hình được)."""
    monkeypatch.setenv("WEB2API_CHATGPT_TOI_DA_TAI_KHOAN", "2")
    mod = nap_module(monkeypatch, {"k1": "hong", "k2": "hong", "k3": "hong"},
                     ["k1", "k2", "k3"])
    ma, _td, _than = chay(mod.chat(
        {"model": "chatgpt-web/4o-mini",
         "messages": [{"role": "user", "content": "chao"}]}, stream=False))
    assert ma == 401
    # me 401 cho mỗi khoá đã thử; khoá thứ 3 không bị đụng tới
    assert mod._tai_khoan[2].khoe is True


def test_device_id_on_dinh_moi_khoa():
    """oai-device-id ổn định cho mỗi khoá (mỗi client instance)."""
    c1 = _client_truc_tiep({}, "token-x")
    c2 = _client_truc_tiep({}, "token-x")
    c3 = _client_truc_tiep({}, "token-y")
    assert c1._device_id == c2._device_id, "cùng khoá phải cùng device-id"
    assert c1._device_id != c3._device_id, "khác khoá phải khác device-id"


def test_suc_khoe_sau_khong_ton_luot_chat(monkeypatch):
    """suc_khoe(sau=True): chạy sentinel + chat-requirements, KHÔNG gọi conversation."""
    mod = nap_module(monkeypatch, {}, [TOKEN_TOT])
    kq = chay(mod.suc_khoe(sau=True))
    assert kq["ok"] is True
    muc = kq["khoa"][0]
    assert muc.get("san_sang_chat") is True
    assert mod._may_chu_gia.so_lan_hoi_thoai == 0, "mức sâu không được tốn lượt chat"


def test_token_khong_vao_nhat_ky(monkeypatch, caplog):
    """Token và tiêu đề sentinel không lọt vào log."""
    with caplog.at_level(logging.DEBUG, logger="web2api.module.chatgpt-web"):
        mod = nap_module(monkeypatch, {}, [TOKEN_TOT])
        chay(mod.chat(
            {"model": "chatgpt-web/4o-mini",
             "messages": [{"role": "user", "content": "chao"}]}, stream=False))
    nhat_ky = "\n".join(r.getMessage() for r in caplog.records)
    assert TOKEN_TOT not in nhat_ky
    assert "req-token-nv211" not in nhat_ky


def test_phan_loai_403_theo_ngu_canh(monkeypatch):
    """403 phân loại theo bản chất: bước cài = khoá hỏng; chat sau init = lỗi tạm."""
    mod = nap_module(monkeypatch, {}, [TOKEN_TOT])
    assert mod.phan_loai_loi(403, {}, {}) == "khoa_hong"
    assert mod.phan_loai_loi(403, {}, {"buoc": "cai"}) == "khoa_hong"
    assert mod.phan_loai_loi(403, {},
                             {"buoc": "chat", "da_xac_thuc": True}) == "loi_tam"
