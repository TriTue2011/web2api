"""Test module chatgpt-web — toàn bộ chạy trên máy chủ GIẢ (httpx.MockTransport).

Không gọi mạng thật: Module._tao_client bị chích để trả ChatGPTWebClient gắn
transport giả, nói đúng «hợp đồng dây» mà client.py cài đặt (GET /backend-api/me →
POST /backend-api/f/conversation → SSE với message.content.parts).
"""

import asyncio
import importlib.util
import json
import logging
import sys
import types
from pathlib import Path

import httpx
import pytest

from app.kham_pha import kiem_chuan_module, kiem_manifest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "chatgpt-web"
TEN_GOI = "chatgpt_web_mod_test"


def _tai_goi():
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


# ---------------------------------------------------------------------------
# Máy chủ giả: hành vi theo Bearer token trong header Authorization.
# ---------------------------------------------------------------------------

def _su_kien(parts, xong=False):
    return {"message": {"author": {"role": "assistant"},
                       "content": {"content_type": "text", "parts": parts},
                       "status": "finished_successfully" if xong else "in_progress",
                       "end_turn": xong}}


def _lay_token(req):
    auth = req.headers.get("authorization", "")
    return auth[len("Bearer "):] if auth.startswith("Bearer ") else ""


def lam_gia(che_do):
    def xu_ly(req):
        token = _lay_token(req)
        hv = che_do.get(token, "ok")
        duong = req.url.path
        if duong == "/backend-api/me":
            if hv == "me_401":
                return httpx.Response(401, text="het han")
            return httpx.Response(200, json={"email": f"{token}@example.com"})
        if duong == "/backend-api/accounts/check":
            # Gói theo chế độ giả: che_do[token] == "free" → free, còn lại plus.
            if hv in ("401", "me_401"):
                return httpx.Response(401, text="het han")
            goi = "free" if hv == "free" else "plus"
            return httpx.Response(
                200, json={"accounts": {"default": {"plan_type": goi}}})
        if duong == "/backend-api/f/conversation":
            if hv == "401" or hv == "me_401":
                return httpx.Response(401, text="het han")
            if hv == "403":
                return httpx.Response(403, text="cam")
            if hv == "429":
                return httpx.Response(429, text="gioi han")
            if hv == "500":
                return httpx.Response(500, text="loi may chu")
            if hv == "vo_cau_truc":
                return httpx.Response(200, text="day khong phai sse {{{")
            if hv == "khong_su_kien":
                return httpx.Response(200, text="data: [DONE]\n\n")
            if hv == "stream":
                async def phat():
                    yield b'data: {"ping": 1}\n\n'
                    for t in ("Xin ", "Xin chao ", "Xin chao ChatGPT"):
                        yield (f"data: {json.dumps(_su_kien([t]))}\n\n").encode()
                    yield (f"data: {json.dumps(_su_kien(['Xin chao ChatGPT'], xong=True))}\n\n").encode()
                    yield b"data: [DONE]\n\n"
                return httpx.Response(200, content=phat(),
                                      headers={"Content-Type": "text/event-stream"})
            dong = "".join(
                f"data: {json.dumps(_su_kien([t], xong=(t == 'tra-loi-tich-luy')))}\n\n"
                for t in ("tra-loi-", "tra-loi-tich-luy"))
            return httpx.Response(200, text=dong + "data: [DONE]\n\n",
                                  headers={"Content-Type": "text/event-stream"})
        return httpx.Response(404, text="khong co")

    return httpx.MockTransport(xu_ly)


def nap_module(monkeypatch, che_do, tai_khoan, **cau_hinh_them):
    transport = lam_gia(che_do)

    def tao_client(self, token):
        return mod_client.ChatGPTWebClient(token, transport=transport)

    monkeypatch.setattr(Module, "_tao_client", tao_client)
    # Mặc định không đụng kho danh tính thật: chỉ khi test danh tính mới chích riêng.
    mod = Module(MANIFEST)
    cau_hinh = {"tai_khoan": list(tai_khoan)}
    cau_hinh.update(cau_hinh_them)
    mod.nap(cau_hinh)
    return mod


def than_chat(model="chatgpt-web/4o", noi_dung="Xin chào"):
    return {"model": model,
            "messages": [{"role": "user", "content": noi_dung}]}


async def _thu_raw_sse(mod, body):
    ma, tieu_de, luong = await mod.chat(body, True)
    assert ma == 200
    assert tieu_de["Content-Type"] == "text/event-stream"
    return b"".join([c async for c in luong])


def phan_tich_sse(raw: bytes):
    su_kien = []
    for khoi in raw.decode("utf-8").split("\n\n"):
        khoi = khoi.strip()
        if not khoi:
            continue
        assert khoi.startswith("data: "), khoi[:60]
        du_lieu = khoi[len("data: "):]
        su_kien.append("[DONE]" if du_lieu == "[DONE]" else json.loads(du_lieu))
    return su_kien


# ---------------------------------------------------------------------------
# Manifest + hợp đồng chuẩn.
# ---------------------------------------------------------------------------

class TestManifestVaChuan:
    def test_manifest_hop_le(self):
        assert kiem_manifest(MANIFEST) is None
        assert MANIFEST["id"] == "chatgpt-web"
        assert MANIFEST["tien_to"] == "chatgpt-web"
        assert MANIFEST["hang"] == "A"
        assert MANIFEST["can_module"] == ["vnc-xa"]
        assert MANIFEST["tai_nguyen"]["mang_ra"] == ["chatgpt.com"]
        truong = {t["khoa"]: t for t in MANIFEST["truong_cau_hinh"]}
        assert truong["tai_khoan"]["kieu"] == "danh_sach_bi_mat"
        assert truong["danh_tinh_id"]["kieu"] == "chuoi"
        assert truong["danh_tinh_id"]["bat_buoc"] is False
        assert MANIFEST["yeu_cau_goi"] == []

    def test_dung_chuan_module(self):
        assert kiem_chuan_module(MANIFEST["cung_cap"], Module) == {}


# ---------------------------------------------------------------------------
# Chat không stream / stream.
# ---------------------------------------------------------------------------

class TestChat:
    def test_chat_khong_stream(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, ["token-tot"])
        ma, tieu_de, raw = chay(mod.chat(than_chat(), False))
        assert ma == 200
        assert tieu_de["Content-Type"] == "application/json"
        than = json.loads(raw.decode("utf-8"))
        assert than["object"] == "chat.completion"
        assert than["model"] == "chatgpt-web/4o"
        assert than["choices"][0]["message"]["content"] == "tra-loi-tich-luy"
        assert than["choices"][0]["finish_reason"] == "stop"

    def test_chat_stream_tung_dong(self, monkeypatch):
        mod = nap_module(monkeypatch, {"token-tot": "stream"}, ["token-tot"])
        raw = chay(_thu_raw_sse(mod, than_chat()))
        sk = phan_tich_sse(raw)
        assert sk[-1] == "[DONE]"
        assert sk[0]["choices"][0]["delta"] == {"role": "assistant"}
        delta = "".join(e["choices"][0]["delta"].get("content", "") for e in sk
                        if isinstance(e, dict))
        assert delta == "Xin chao ChatGPT"
        assert sk[-2]["choices"][0]["finish_reason"] == "stop"

    def test_model_mac_dinh_va_tien_to(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, ["token-tot"])
        ma, _, raw = chay(mod.chat(than_chat(model="4o"), False))
        assert ma == 200
        assert json.loads(raw.decode())["model"] == "chatgpt-web/4o"
        ma, _, raw = chay(mod.chat(than_chat(model="auto"), False))
        assert ma == 200

    def test_model_khong_co_400(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, ["token-tot"])
        ma, _, raw = chay(mod.chat(than_chat(model="chatgpt-web/khong-ton-tai"), False))
        assert ma == 400
        assert "không có" in json.loads(raw.decode())["error"]["message"]

    def test_thieu_messages_400(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, ["token-tot"])
        ma, _, _ = chay(mod.chat({"model": "chatgpt-web/4o", "messages": []}, False))
        assert ma == 400


# ---------------------------------------------------------------------------
# Xoay vòng tài khoản theo mã lỗi.
# ---------------------------------------------------------------------------

class TestXoayTaiKhoan:
    def test_401_chuyen_tai_khoan(self, monkeypatch):
        mod = nap_module(monkeypatch, {"token-hong": "401"}, ["token-hong", "token-tot"])
        ma, _, raw = chay(mod.chat(than_chat(), False))
        assert ma == 200
        assert json.loads(raw.decode())["choices"][0]["message"]["content"] == "tra-loi-tich-luy"
        assert mod._tai_khoan[0].khoe is False
        assert mod._tai_khoan[0].loai_loi == "khoa_hong"

    def test_403_chuyen_tai_khoan(self, monkeypatch):
        mod = nap_module(monkeypatch, {"token-hong": "403"}, ["token-hong", "token-tot"])
        ma, _, raw = chay(mod.chat(than_chat(), False))
        assert ma == 200
        assert "tra-loi-tich-luy" in raw.decode()

    def test_429_chuyen_roi_het_thi_429(self, monkeypatch):
        mod = nap_module(monkeypatch, {"token-hl": "429"}, ["token-hl", "token-tot"])
        ma, _, _ = chay(mod.chat(than_chat(), False))
        assert ma == 200
        mod2 = nap_module(monkeypatch, {"token-hl": "429"}, ["token-hl"])
        ma, _, raw = chay(mod2.chat(than_chat(), False))
        assert ma == 429
        assert "giới hạn" in json.loads(raw.decode())["error"]["message"]

    def test_500_chuyen_roi_het_thi_502(self, monkeypatch):
        mod = nap_module(monkeypatch, {"token-loi": "500"}, ["token-loi", "token-tot"])
        ma, _, _ = chay(mod.chat(than_chat(), False))
        assert ma == 200
        mod2 = nap_module(monkeypatch, {"token-loi": "500"}, ["token-loi"])
        ma, _, _ = chay(mod2.chat(than_chat(), False))
        assert ma == 502

    def test_tat_ca_401_thi_401(self, monkeypatch):
        mod = nap_module(monkeypatch, {"a": "401", "b": "me_401"}, ["a", "b"])
        ma, _, raw = chay(mod.chat(than_chat(), False))
        assert ma == 401
        assert "token" in json.loads(raw.decode())["error"]["message"].lower()

    def test_stream_xoay_khi_tai_khoan_dau_401(self, monkeypatch):
        mod = nap_module(monkeypatch, {"token-hong": "401", "token-tot": "stream"},
                         ["token-hong", "token-tot"])
        raw = chay(_thu_raw_sse(mod, than_chat()))
        sk = phan_tich_sse(raw)
        assert sk[-1] == "[DONE]"
        delta = "".join(e["choices"][0]["delta"].get("content", "") for e in sk
                        if isinstance(e, dict))
        assert delta == "Xin chao ChatGPT"

    def test_tai_khoan_tung_loi_van_duoc_thu_lai(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, ["token-tot"])
        mod._tai_khoan[0].khoe = False
        ma, _, raw = chay(mod.chat(than_chat(), False))
        assert ma == 200
        assert "tra-loi-tich-luy" in raw.decode()
        assert mod._tai_khoan[0].khoe is True

    def test_phan_loai_loi_hook(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, ["token-tot"])
        assert mod.phan_loai_loi(401, {}, {}) == "khoa_hong"
        assert mod.phan_loai_loi(403, {}, {}) == "khoa_hong"
        assert mod.phan_loai_loi(429, {}, {}) == "het_han_muc"
        assert mod.phan_loai_loi(400, {}, {}) == "yeu_cau_sai"
        assert mod.phan_loai_loi(503, {}, {}) == "may_chu_loi"
        assert mod.phan_loai_loi(418, {}, {}) is None


# ---------------------------------------------------------------------------
# Danh tính chung: gắn / kiểm / hiển thị.
# ---------------------------------------------------------------------------

class TestDanhTinh:
    def test_cai_khong_danh_tinh_van_chay(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, ["token-tot"])
        kq = chay(mod.cai({"tai_khoan": ["token-tot"]}))
        assert kq["ok"] is True
        assert mod._danh_tinh_id is None

    def test_cai_gan_danh_tinh_ton_tai(self, monkeypatch):
        da_gan = []

        def lay_gia(id_):
            # Kho thật luôn casefold email khi tạo, nên email trong kho luôn thường.
            return {"id": id_, "email": "ban@example.com",
                    "nhan": "Tài khoản chính", "ngay_tao": "x", "gan": []}

        def gan_gia(id_, module_id="chatgpt-web"):
            da_gan.append((id_, module_id))
            return {"id": id_}

        monkeypatch.setattr(mod_module, "_lay_danh_tinh", lay_gia)
        monkeypatch.setattr(mod_module, "_gan_danh_tinh", gan_gia)
        mod = nap_module(monkeypatch, {}, ["token-tot"], danh_tinh_id="dt-abcdef1234567890")
        kq = chay(mod.cai({"tai_khoan": ["token-tot"],
                           "danh_tinh_id": "dt-abcdef1234567890"}))
        assert kq["ok"] is True
        assert kq["cau_hinh_luu"]["danh_tinh_id"] == "dt-abcdef1234567890"
        assert da_gan == [("dt-abcdef1234567890", "chatgpt-web")]
        tt = mod._thong_tin_danh_tinh()
        assert tt["id"] == "dt-abcdef1234567890"
        assert tt["nhan"] == "Tài khoản chính"
        # Email che: giữ ký tự đầu + …@ + miền, không lộ đầy đủ.
        assert tt["email_che"] == "b…@example.com"
        assert "ban@example.com" not in json.dumps(tt)

    def test_cai_danh_tinh_khong_ton_tai_loi_ro(self, monkeypatch):
        monkeypatch.setattr(mod_module, "_lay_danh_tinh", lambda id_: None)
        mod = nap_module(monkeypatch, {}, ["token-tot"], danh_tinh_id="dt-khong-co")
        kq = chay(mod.cai({"tai_khoan": ["token-tot"], "danh_tinh_id": "dt-khong-co"}))
        assert kq["ok"] is False
        assert "không tồn tại" in kq["loi"]

    def test_suc_khoe_hien_danh_tinh_che_email(self, monkeypatch):
        monkeypatch.setattr(
            mod_module, "_lay_danh_tinh",
            lambda id_: {"id": id_, "email": "user@example.com", "nhan": "",
                         "ngay_tao": "x", "gan": []})
        mod = nap_module(monkeypatch, {}, ["token-tot"], danh_tinh_id="dt-1")
        sk = chay(mod.suc_khoe())
        assert sk["ok"] is True
        dt = sk["khoa"][0]["danh_tinh"]
        assert dt["email_che"] == "u…@example.com"
        assert "user@example.com" not in json.dumps(sk)

    def test_che_email_don_vi(self):
        assert mod_module._che_email("Ban@Example.com") == "B…@Example.com"
        assert mod_module._che_email("khong-co-a-còng") == "…"
        assert mod_module._che_email("") == "…"


# ---------------------------------------------------------------------------
# Cài đặt / sức khoẻ / models / dung.
# ---------------------------------------------------------------------------

class TestCaiVaSucKhoe:
    def test_cai_thanh_cong(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, ["token-tot", "token-tot-2"])
        kq = chay(mod.cai({"tai_khoan": ["token-tot", "token-tot-2"]}))
        assert kq["ok"] is True
        assert len(kq["ket_qua_tung_khoa"]) == 2
        assert all(m["ok"] for m in kq["ket_qua_tung_khoa"])
        assert kq["loi"] is None

    def test_cai_token_hong(self, monkeypatch):
        mod = nap_module(monkeypatch, {"token-xau": "me_401"}, ["token-xau"])
        kq = chay(mod.cai({"tai_khoan": ["token-xau"]}))
        assert kq["ok"] is False
        assert kq["ket_qua_tung_khoa"][0]["ok"] is False
        assert "401/403" in kq["ket_qua_tung_khoa"][0]["loi"]

    def test_cai_khong_co_token(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, [])
        kq = chay(mod.cai({"tai_khoan": []}))
        assert kq["ok"] is False

    def test_cai_co_so_url_sai(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, ["token-tot"])
        kq = chay(mod.cai({"tai_khoan": ["token-tot"], "co_so_url": "ftp://may-chu"}))
        assert kq["ok"] is False
        assert "co_so_url" in kq["loi"]

    def test_suc_khoe(self, monkeypatch):
        mod = nap_module(monkeypatch, {"token-xau": "me_401"},
                         ["token-tot", "token-xau"])
        sk = chay(mod.suc_khoe())
        assert sk["ok"] is True
        assert len(sk["khoa"]) == 2
        assert sk["khoa"][0]["ok"] is True
        assert sk["khoa"][1]["ok"] is False
        assert sk["khoa"][0]["khoa"].startswith("…")
        assert "token-tot" not in json.dumps(sk["khoa"])

    def test_models(self, monkeypatch):
        """NV93: models() là HỢP theo tài khoản — id ổn định có tiền tố,
        nhãn «nhan» tách riêng, kèm «goi» và «available_cho»."""
        mod = nap_module(monkeypatch,
                         {"token-plus": "plus", "token-free": "free"},
                         ["token-plus", "token-free"])
        kq = chay(mod.cai({"tai_khoan": ["token-plus", "token-free"]}))
        assert kq["ok"] is True
        ds = chay(mod.models())
        assert [m["id"] for m in ds] == ["chatgpt-web/4o-mini", "chatgpt-web/4.1-mini",
                                        "chatgpt-web/4o", "chatgpt-web/4.1",
                                        "chatgpt-web/o3", "chatgpt-web/o4-mini"]
        for m in ds:
            assert m["nhan"]  # nhãn hiển thị tách khỏi id ổn định
            assert m["goi"] in ("free", "plus", "pro")
            assert m["available_cho"]  # khoá mờ các tài khoản có model này
        # model chỉ Plus có → chỉ tài khoản plus «available»
        o3 = next(m for m in ds if m["id"] == "chatgpt-web/o3")
        assert o3["goi"] == "plus"
        assert len(o3["available_cho"]) == 1
        mini = next(m for m in ds if m["id"] == "chatgpt-web/4o-mini")
        assert mini["goi"] == "free"
        assert len(mini["available_cho"]) == 2

    def test_dung(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, ["token-tot"])
        chay(mod.cai({"tai_khoan": ["token-tot"]}))
        chay(mod.dung())  # không vỡ


# ---------------------------------------------------------------------------
# Không tài khoản nào: chạy đúng, không vỡ.
# ---------------------------------------------------------------------------

class TestKhongCoTaiKhoan:
    def test_chat_khong_tai_khoan_401_chu_khong_vo(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, [])
        ma, _, raw = chay(mod.chat(than_chat(), False))
        assert ma == 401
        assert "chưa có tài khoản" in json.loads(raw.decode())["error"]["message"]

    def test_models_va_suc_khoe_khong_tai_khoan(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, [])
        # NV93: models() là hợp theo tài khoản — không tài khoản thì rỗng.
        assert chay(mod.models()) == []
        sk = chay(mod.suc_khoe())
        assert sk["ok"] is False
        assert sk["khoa"] == []


# ---------------------------------------------------------------------------
# Bảo mật: bí mật không vào log hay phản hồi.
# ---------------------------------------------------------------------------

class TestBaoMat:
    def test_bi_mat_khong_vao_log(self, monkeypatch, caplog):
        token_mat = "token-sieu-bi-mat-1234567890"
        mod = nap_module(monkeypatch, {token_mat: "401"}, [token_mat])
        with caplog.at_level(logging.DEBUG, logger="web2api.module.chatgpt-web"):
            chay(mod.cai({"tai_khoan": [token_mat]}))
            chay(mod.chat(than_chat(), False))
        assert token_mat not in caplog.text

    def test_bi_mat_khong_vao_phan_hoi(self, monkeypatch):
        token_mat = "token-sieu-bi-mat-1234567890"
        mod = nap_module(monkeypatch, {token_mat: "401"}, [token_mat])
        ma, _, raw = chay(mod.chat(than_chat(), False))
        assert ma == 401
        assert token_mat not in raw.decode()

    def test_stream_loi_khong_lo_bi_mat(self, monkeypatch):
        token_mat = "token-sieu-bi-mat-1234567890"
        mod = nap_module(monkeypatch, {token_mat: "401"}, [token_mat])
        raw = chay(_thu_raw_sse(mod, than_chat()))
        assert token_mat not in raw.decode()

    def test_repr_client_khong_lo_token(self, monkeypatch):
        transport = lam_gia({})
        c = mod_client.ChatGPTWebClient("token-bi-mat", transport=transport)
        assert "token-bi-mat" not in repr(c)
        chay(c.close())


# ---------------------------------------------------------------------------
# Đáp án lạ từ thượng nguồn: có cấu trúc, không vỡ.
# ---------------------------------------------------------------------------

class TestDapAnLa:
    def test_vo_cau_truc_thanh_502(self, monkeypatch):
        mod = nap_module(monkeypatch, {"t": "vo_cau_truc"}, ["t"])
        ma, _, _ = chay(mod.chat(than_chat(), False))
        assert ma == 502

    def test_khong_su_kien_thanh_502(self, monkeypatch):
        mod = nap_module(monkeypatch, {"t": "khong_su_kien"}, ["t"])
        ma, _, _ = chay(mod.chat(than_chat(), False))
        assert ma == 502


# ---------------------------------------------------------------------------
# Đơn vị: bí danh model, chuyển tin nhắn, bóc sự kiện SSE.
# ---------------------------------------------------------------------------

class TestDonVi:
    def test_bi_danh_model(self):
        assert mod_client.giai_quyet_model("chatgpt-web/4o") == "4o"
        assert mod_client.giai_quyet_model("cgw/4o-mini") == "4o-mini"
        assert mod_client.giai_quyet_model("gpt-4.1") == "4.1"
        assert mod_client.giai_quyet_model("o3") == "o3"
        assert mod_client.giai_quyet_model("auto") == "4o"
        assert mod_client.giai_quyet_model("") == "4o"
        with pytest.raises(mod_client.ModelKhongCo):
            mod_client.giai_quyet_model("chatgpt-web/khong-ton-tai")

    def test_chuyen_tin_nhan_giu_vai_tro_va_system(self):
        tin = mod_client.chuyen_thanh_tin_nhan({
            "messages": [
                {"role": "system", "content": "Bạn là trợ lý."},
                {"role": "user", "content": "Chào"},
                {"role": "assistant", "content": "Chào bạn"},
                {"role": "user", "content": "Tiếp nhé"},
            ]})
        assert [t["author"]["role"] for t in tin] == ["system", "user", "assistant", "user"]
        assert tin[0]["content"]["parts"] == ["Bạn là trợ lý."]
        assert all(t["content"]["content_type"] == "text" for t in tin)
        assert all("id" in t and "metadata" in t for t in tin)

    def test_trich_su_kien_bo_echo_user_va_la(self):
        vb, xong = mod_client.trich_van_ban_su_kien(
            {"message": {"author": {"role": "user"},
                         "content": {"content_type": "text", "parts": ["echo"]}}})
        assert (vb, xong) == ("", False)
        vb, xong = mod_client.trich_van_ban_su_kien(
            {"message": {"author": {"role": "assistant"},
                         "content": {"content_type": "text", "parts": ["a", "b"]},
                         "status": "finished_successfully", "end_turn": True}})
        assert (vb, xong) == ("ab", True)
        assert mod_client.trich_van_ban_su_kien("chuoi") == ("", False)
        assert mod_client.trich_van_ban_su_kien({}) == ("", False)

    def test_dung_payload_toi_thieu(self):
        p = mod_client.dung_payload([mod_client.dung_tin_nhan_hoi_thoai("user", "hi")],
                                    "gpt-4o")
        assert p["action"] == "next"
        assert p["model"] == "gpt-4o"
        assert p["force_use_sse"] is True
        assert p["timezone"] == "Asia/Saigon"
        assert p["timezone_offset_min"] == -420
        assert p["supported_encodings"] == ["v1"]
