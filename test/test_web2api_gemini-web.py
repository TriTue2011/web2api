"""Test module gemini-web — toàn bộ chạy trên máy chủ GIẢ (httpx.MockTransport).

Không gọi mạng thật: Module._tao_client bị chích để trả GeminiWebClient gắn
transport giả, nói đúng «hợp đồng dây» mà client.py cài đặt (init → SNlM0e →
POST batchexecute → khung ["wrb.fr", …]).
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
THU_MUC = GOC / "modules" / "gemini-web"
TEN_GOI = "gemini_web_mod_test"


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
# Máy chủ giả: hành vi theo giá trị __Secure-1PSID trong header Cookie.
# ---------------------------------------------------------------------------

def _than_khung(text):
    inner = json.dumps([None, None, None, None, [["cid-1", text]]])
    return json.dumps(["wrb.fr", "hNvQH", inner, None, None, None, "generic"])


def _lay_psid(req):
    for phan in req.headers.get("cookie", "").split(";"):
        phan = phan.strip()
        if phan.startswith("__Secure-1PSID="):
            return phan.split("=", 1)[1]
    return ""


def lam_gia(che_do):
    def xu_ly(req):
        psid = _lay_psid(req)
        hv = che_do.get(psid, "ok")
        duong = req.url.path
        if duong == "/":
            if hv == "init_401":
                return httpx.Response(401, text="dang nhap lai di")
            return httpx.Response(200, text='<html>"SNlM0e":"snl-gia"</html>')
        if duong == "/_/BardChatUi/data/batchexecute":
            if hv == "401":
                return httpx.Response(401, text="het han")
            if hv == "403":
                return httpx.Response(403, text="cam")
            if hv == "429":
                return httpx.Response(429, text="gioi han")
            if hv == "500":
                return httpx.Response(500, text="loi may chu")
            if hv == "vo_cau_truc":
                return httpx.Response(200, text="day khong phai batchexecute {{{")
            if hv == "khung_rong":
                return httpx.Response(200, text=")]}'\n[]")
            if hv == "stream":
                async def phat():
                    yield b")]}'\n"
                    for t in ("Xin ", "Xin chao ", "Xin chao Gemini"):
                        yield (_than_khung(t) + "\n").encode()
                return httpx.Response(200, content=phat())
            return httpx.Response(200, text=")]}'\n" + _than_khung(f"tra-loi-cua-{psid}"))
        return httpx.Response(404, text="khong co")

    return httpx.MockTransport(xu_ly)


def ck(psid):
    return f"__Secure-1PSID={psid}; __Secure-1PSIDTS=ts-cua-{psid}"


def nap_module(monkeypatch, che_do, cookies):
    transport = lam_gia(che_do)

    def tao_client(self, psid, psidts):
        return mod_client.GeminiWebClient(psid, psidts, transport=transport)

    monkeypatch.setattr(Module, "_tao_client", tao_client)
    mod = Module(MANIFEST)
    mod.nap({"cookies": list(cookies)})
    return mod


def than_chat(model="gemini-web/gemini-3-flash", noi_dung="Xin chào"):
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
        assert MANIFEST["id"] == "gemini-web"
        assert MANIFEST["tien_to"] == "gemini-web"
        assert MANIFEST["hang"] == "A"
        assert MANIFEST["tai_nguyen"]["mang_ra"] == ["gemini.google.com",
                                                         "accounts.google.com"]  # NV89: miền trang đăng nhập
        truong = {t["khoa"]: t for t in MANIFEST["truong_cau_hinh"]}
        assert truong["cookies"]["kieu"] == "danh_sach_bi_mat"
        assert truong["cookies"]["nhan_cookie"] == {"mien": "gemini.google.com"}
        assert MANIFEST["yeu_cau_goi"] == []

    def test_dung_chuan_module(self):
        assert kiem_chuan_module(MANIFEST["cung_cap"], Module) == {}


# ---------------------------------------------------------------------------
# Chat không stream / stream.
# ---------------------------------------------------------------------------

class TestChat:
    def test_chat_khong_stream(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, [ck("psid-tot")])
        ma, tieu_de, raw = chay(mod.chat(than_chat(), False))
        assert ma == 200
        assert tieu_de["Content-Type"] == "application/json"
        than = json.loads(raw.decode("utf-8"))
        assert than["object"] == "chat.completion"
        assert than["model"] == "gemini-web/gemini-3-flash"
        assert than["choices"][0]["message"]["content"] == "tra-loi-cua-psid-tot"
        assert than["choices"][0]["finish_reason"] == "stop"

    def test_chat_stream_tung_dong(self, monkeypatch):
        mod = nap_module(monkeypatch, {"psid-tot": "stream"}, [ck("psid-tot")])
        raw = chay(_thu_raw_sse(mod, than_chat()))
        sk = phan_tich_sse(raw)
        assert sk[-1] == "[DONE]"
        assert sk[0]["choices"][0]["delta"] == {"role": "assistant"}
        delta = "".join(e["choices"][0]["delta"].get("content", "") for e in sk
                        if isinstance(e, dict))
        assert delta == "Xin chao Gemini"
        assert sk[-2]["choices"][0]["finish_reason"] == "stop"

    def test_model_mac_dinh_va_tien_to(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, [ck("psid-tot")])
        ma, _, raw = chay(mod.chat(than_chat(model="gemini-3-flash"), False))
        assert ma == 200
        assert json.loads(raw.decode())["model"] == "gemini-web/gemini-3-flash"
        # auto → mặc định
        ma, _, raw = chay(mod.chat(than_chat(model="auto"), False))
        assert ma == 200

    def test_model_khong_co_400(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, [ck("psid-tot")])
        ma, _, raw = chay(mod.chat(than_chat(model="gemini-web/khong-ton-tai"), False))
        assert ma == 400
        assert "không có" in json.loads(raw.decode())["error"]["message"]

    def test_thieu_messages_400(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, [ck("psid-tot")])
        ma, _, _ = chay(mod.chat({"model": "gemini-web/gemini-3-flash", "messages": []}, False))
        assert ma == 400


# ---------------------------------------------------------------------------
# Xoay vòng tài khoản theo mã lỗi.
# ---------------------------------------------------------------------------

class TestXoayTaiKhoan:
    def test_401_chuyen_tai_khoan(self, monkeypatch):
        mod = nap_module(monkeypatch, {"psid-hong": "401"}, [ck("psid-hong"), ck("psid-tot")])
        ma, _, raw = chay(mod.chat(than_chat(), False))
        assert ma == 200
        assert json.loads(raw.decode())["choices"][0]["message"]["content"] == "tra-loi-cua-psid-tot"
        assert mod._tai_khoan[0].khoe is False
        assert mod._tai_khoan[0].loai_loi == "khoa_hong"

    def test_403_chuyen_tai_khoan(self, monkeypatch):
        mod = nap_module(monkeypatch, {"psid-hong": "403"}, [ck("psid-hong"), ck("psid-tot")])
        ma, _, raw = chay(mod.chat(than_chat(), False))
        assert ma == 200
        assert "tra-loi-cua-psid-tot" in raw.decode()

    def test_429_chuyen_roi_het_thi_429(self, monkeypatch):
        mod = nap_module(monkeypatch, {"psid-hl": "429"}, [ck("psid-hl"), ck("psid-tot")])
        ma, _, _ = chay(mod.chat(than_chat(), False))
        assert ma == 200
        mod2 = nap_module(monkeypatch, {"psid-hl": "429"}, [ck("psid-hl")])
        ma, _, raw = chay(mod2.chat(than_chat(), False))
        assert ma == 429
        assert "giới hạn" in json.loads(raw.decode())["error"]["message"]

    def test_500_chuyen_roi_het_thi_502(self, monkeypatch):
        mod = nap_module(monkeypatch, {"psid-loi": "500"}, [ck("psid-loi"), ck("psid-tot")])
        ma, _, _ = chay(mod.chat(than_chat(), False))
        assert ma == 200
        mod2 = nap_module(monkeypatch, {"psid-loi": "500"}, [ck("psid-loi")])
        ma, _, _ = chay(mod2.chat(than_chat(), False))
        assert ma == 502

    def test_tat_ca_401_thi_401(self, monkeypatch):
        mod = nap_module(monkeypatch, {"a": "401", "b": "init_401"}, [ck("a"), ck("b")])
        ma, _, raw = chay(mod.chat(than_chat(), False))
        assert ma == 401
        assert "cookie" in json.loads(raw.decode())["error"]["message"].lower()

    def test_stream_xoay_khi_tai_khoan_dau_401(self, monkeypatch):
        mod = nap_module(monkeypatch, {"psid-hong": "401", "psid-tot": "stream"},
                         [ck("psid-hong"), ck("psid-tot")])
        raw = chay(_thu_raw_sse(mod, than_chat()))
        sk = phan_tich_sse(raw)
        assert sk[-1] == "[DONE]"
        delta = "".join(e["choices"][0]["delta"].get("content", "") for e in sk
                        if isinstance(e, dict))
        assert delta == "Xin chao Gemini"

    def test_tai_khoan_tung_loi_van_duoc_thu_lai(self, monkeypatch):
        # «Không bao giờ chặn lựa chọn cuối»: tài khoản từng lỗi vẫn được thử
        # khi không còn lựa chọn khoẻ nào.
        mod = nap_module(monkeypatch, {}, [ck("psid-tot")])
        mod._tai_khoan[0].khoe = False
        ma, _, raw = chay(mod.chat(than_chat(), False))
        assert ma == 200
        assert "tra-loi-cua-psid-tot" in raw.decode()
        assert mod._tai_khoan[0].khoe is True  # khoẻ lại ngay khi trả lời được

    def test_phan_loai_loi_hook(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, [ck("psid-tot")])
        assert mod.phan_loai_loi(401, {}, {}) == "khoa_hong"
        assert mod.phan_loai_loi(403, {}, {}) == "khoa_hong"
        assert mod.phan_loai_loi(429, {}, {}) == "het_han_muc"
        assert mod.phan_loai_loi(400, {}, {}) == "yeu_cau_sai"
        assert mod.phan_loai_loi(503, {}, {}) == "may_chu_loi"
        assert mod.phan_loai_loi(418, {}, {}) is None  # để lõi dùng mặc định


# ---------------------------------------------------------------------------
# Cài đặt / sức khoẻ / models / dung.
# ---------------------------------------------------------------------------

class TestCaiVaSucKhoe:
    def test_cai_thanh_cong(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, [ck("psid-tot"), ck("psid-tot-2")])
        kq = chay(mod.cai({"cookies": [ck("psid-tot"), ck("psid-tot-2")]}))
        assert kq["ok"] is True
        assert len(kq["ket_qua_tung_khoa"]) == 2
        assert all(m["ok"] for m in kq["ket_qua_tung_khoa"])
        assert kq["loi"] is None

    def test_cai_cookie_hong(self, monkeypatch):
        mod = nap_module(monkeypatch, {"psid-xau": "init_401"}, [ck("psid-xau")])
        kq = chay(mod.cai({"cookies": [ck("psid-xau")]}))
        assert kq["ok"] is False
        assert kq["ket_qua_tung_khoa"][0]["ok"] is False
        assert "401/403" in kq["ket_qua_tung_khoa"][0]["loi"]

    def test_cai_khong_co_cookie(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, [])
        kq = chay(mod.cai({"cookies": []}))
        assert kq["ok"] is False

    def test_cai_co_so_url_sai(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, [ck("psid-tot")])
        kq = chay(mod.cai({"cookies": [ck("psid-tot")], "co_so_url": "ftp://may-chu"}))
        assert kq["ok"] is False
        assert "co_so_url" in kq["loi"]

    def test_cai_co_so_url_loopback_bi_chan(self, monkeypatch):
        # conftest KHÔNG đặt WEB2API_TEST_CHO_PHEP_LOOPBACK → loopback bị chặn.
        mod = nap_module(monkeypatch, {}, [ck("psid-tot")])
        kq = chay(mod.cai({"cookies": [ck("psid-tot")], "co_so_url": "http://127.0.0.1:9"}))
        assert kq["ok"] is False
        assert "co_so_url" in kq["loi"]

    def test_suc_khoe(self, monkeypatch):
        mod = nap_module(monkeypatch, {"psid-xau": "init_401"},
                         [ck("psid-tot"), ck("psid-xau")])
        sk = chay(mod.suc_khoe())
        assert sk["ok"] is True
        assert len(sk["khoa"]) == 2
        assert sk["khoa"][0]["ok"] is True
        assert sk["khoa"][1]["ok"] is False
        # Dạng che …abcd, không lộ cookie.
        assert sk["khoa"][0]["khoa"].startswith("…")
        assert "psid-tot" not in json.dumps(sk["khoa"])

    def test_models(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, [])
        ds = chay(mod.models())
        # NV93: mã ổn định, nhãn tách riêng; chưa có tài khoản → bảng tĩnh.
        assert [m["id"] for m in ds] == ["gemini-web/gemini-3-flash",
                                        "gemini-web/gemini-3-pro"]
        assert all("nhan" in m and "available_cho" in m for m in ds)

    def test_dung(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, [ck("psid-tot")])
        chay(mod.cai({"cookies": [ck("psid-tot")]}))
        chay(mod.dung())  # không vỡ


# ---------------------------------------------------------------------------
# Không tài khoản nào: chạy đúng, không vỡ.
# ---------------------------------------------------------------------------

class TestKhongCoTaiKhoan:
    def test_chat_khong_tai_khoan_401_chu_khong_vo(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, [])
        ma, _, raw = chay(mod.chat(than_chat(), False))
        assert ma == 401
        # NV272: chat chưa có phiên trả mã rõ can_dang_nhap (thay thông điệp cũ).
        err = json.loads(raw.decode())["error"]
        assert err["code"] == "can_dang_nhap"
        assert "chưa có phiên" in err["message"]

    def test_models_va_suc_khoe_khong_tai_khoan(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, [])
        # NV93: bảng tĩnh mã ổn định (2 model), nhãn tách riêng.
        assert len(chay(mod.models())) == 2
        sk = chay(mod.suc_khoe())
        assert sk["ok"] is False
        assert sk["khoa"] == []

    def test_nap_bo_qua_cookie_thieu_psid(self, monkeypatch):
        mod = nap_module(monkeypatch, {}, ["foo=bar; __Secure-1PSIDTS=ts"])
        assert mod._tai_khoan == []


# ---------------------------------------------------------------------------
# Bảo mật: bí mật không vào log hay phản hồi.
# ---------------------------------------------------------------------------

class TestBaoMat:
    def test_bi_mat_khong_vao_log(self, monkeypatch, caplog):
        psid_mat = "psid-sieu-bi-mat-1234567890"
        mod = nap_module(monkeypatch, {"psid-sieu-bi-mat-1234567890": "401"},
                         [ck(psid_mat)])
        with caplog.at_level(logging.DEBUG, logger="web2api.module.gemini-web"):
            chay(mod.cai({"cookies": [ck(psid_mat)]}))
            chay(mod.chat(than_chat(), False))
        assert psid_mat not in caplog.text

    def test_bi_mat_khong_vao_phan_hoi(self, monkeypatch):
        psid_mat = "psid-sieu-bi-mat-1234567890"
        mod = nap_module(monkeypatch, {"psid-sieu-bi-mat-1234567890": "401"},
                         [ck(psid_mat)])
        ma, _, raw = chay(mod.chat(than_chat(), False))
        assert ma == 401
        assert psid_mat not in raw.decode()
        assert "ts-cua-" not in raw.decode()

    def test_stream_loi_khong_lo_bi_mat(self, monkeypatch):
        psid_mat = "psid-sieu-bi-mat-1234567890"
        mod = nap_module(monkeypatch, {"psid-sieu-bi-mat-1234567890": "401"},
                         [ck(psid_mat)])
        raw = chay(_thu_raw_sse(mod, than_chat()))
        assert psid_mat not in raw.decode()

    def test_repr_client_khong_lo_cookie(self, monkeypatch):
        transport = lam_gia({})
        c = mod_client.GeminiWebClient("psid-bi-mat", transport=transport)
        assert "psid-bi-mat" not in repr(c)
        chay(c.close())


# ---------------------------------------------------------------------------
# Đáp án lạ từ thượng nguồn: có cấu trúc, không vỡ.
# ---------------------------------------------------------------------------

class TestDapAnLa:
    def test_vo_cau_truc_thanh_502(self, monkeypatch):
        mod = nap_module(monkeypatch, {"psid-vo": "vo_cau_truc"}, [ck("psid-vo")])
        ma, _, _ = chay(mod.chat(than_chat(), False))
        assert ma == 502

    def test_khung_rong_thanh_502(self, monkeypatch):
        mod = nap_module(monkeypatch, {"psid-r": "khung_rong"}, [ck("psid-r")])
        ma, _, _ = chay(mod.chat(than_chat(), False))
        assert ma == 502

    def test_init_khong_co_snlm0e_thanh_502(self, monkeypatch):
        def xu_ly(req):
            if req.url.path == "/":
                return httpx.Response(200, text="<html>khong co ma phien</html>")
            return httpx.Response(404)

        transport = httpx.MockTransport(xu_ly)

        def tao_client(self, psid, psidts):
            return mod_client.GeminiWebClient(psid, psidts, transport=transport)

        monkeypatch.setattr(Module, "_tao_client", tao_client)
        mod = Module(MANIFEST)
        mod.nap({"cookies": [ck("psid-tot")]})
        ma, _, _ = chay(mod.chat(than_chat(), False))
        assert ma == 502


# ---------------------------------------------------------------------------
# Đơn vị: tách cookie, bí danh model, chuyển prompt.
# ---------------------------------------------------------------------------

class TestDonVi:
    def test_tach_psid(self):
        tach = mod_module._tach_psid
        psid, psidts = tach("__Secure-1PSID=abc123; __Secure-1PSIDTS=ts9; k=v")
        assert (psid, psidts) == ("abc123", "ts9")
        psid, psidts = tach("__Secure-1PSID=abc123")
        assert (psid, psidts) == ("abc123", None)
        psid, _ = tach("khong-co-gi")
        assert psid is None

    def test_bi_danh_model(self):
        # NV93: mọi tên đều giải về MÃ ỔN ĐỊNH (nhãn tách riêng ở nhan_model).
        assert mod_client.giai_quyet_model("gemini-web/3.5-flash") == "gemini-3-flash"
        assert mod_client.giai_quyet_model("gma/pro") == "gemini-3-pro"
        assert mod_client.giai_quyet_model("flash-lite") == "gemini-3-flash"
        assert mod_client.giai_quyet_model("auto") == "gemini-3-flash"
        assert mod_client.giai_quyet_model("") == "gemini-3-flash"
        assert mod_client.giai_quyet_model("gemini-3-flash") == "gemini-3-flash"
        assert mod_client.giai_quyet_model("gemini-3-pro") == "gemini-3-pro"
        assert mod_client.nhan_model("gemini-3-flash") == "Gemini 3 Flash"
        with pytest.raises(mod_client.ModelKhongCo):
            mod_client.giai_quyet_model("gemini-web/khong-ton-tai")

    def test_chuyen_prompt_giu_vai_tro(self):
        prompt = mod_converter.chuyen_thanh_prompt({
            "messages": [
                {"role": "system", "content": "Bạn là trợ lý."},
                {"role": "user", "content": "Chào"},
                {"role": "assistant", "content": "Chào bạn"},
                {"role": "user", "content": "Tiếp nhé"},
            ]})
        assert prompt == ("System: Bạn là trợ lý.\nUser: Chào\n"
                          "Model: Chào bạn\nUser: Tiếp nhé")

    def test_chuyen_prompt_bo_qua_anh(self):
        prompt = mod_converter.chuyen_thanh_prompt({
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Mô tả ảnh "},
                {"type": "image_url", "image_url": {"url": "data:..."}},
            ]}]})
        assert prompt == "User: Mô tả ảnh"

    def test_trich_van_ban_khung_an_toan(self):
        assert mod_client.trich_van_ban_khung("chuoi") == ""
        assert mod_client.trich_van_ban_khung(["sai", "cau", "truc"]) == ""
        assert mod_client.trich_van_ban_khung(["wrb.fr", "x", "{khong json"]) == ""
