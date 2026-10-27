"""NV271 — HTTP tests: Nối «Codex TRƯỚC, hết hạn mức sang Web CÙNG tài khoản».

Đo các ca bắt buộc T1–T11 qua đường POST /v1/chat/completions thật:
T1: A có codex (san_sang) + web; codex còn hạn mức -> 200 từ codex, web giả 0 lần.
T2: codex của A trả 429 -> 200 từ web, web ghim A; kho ghi het_han_muc; lượt sau codex không bị gọi thêm.
T3: tài khoản chỉ có web trong dung_cho -> chỉ web được gọi, codex 0 lần.
T4: hai tài khoản A và B. Codex A 429 -> web ghim A. Biến thể: web A hỏng 500 -> sang B, codex B 200.
T5 (hồi quy): không có bản ghi kho -> gọi thẳng module khách gọi tên (tai_khoan_email=None).
T6: chặn bot (403 x-web2api-loi-loai: chan_bot) không làm hỏng đường; 403 trơn làm hỏng đường (can_dang_nhap_lai).
T7: codex 401 -> can_dang_nhap_lai, web phục vụ, lượt sau không thử codex.
T8: model không có trong codex -> codex bị bỏ qua, web phục vụ, kho không ghi trạng thái.
T9: khoá khách chỉ có phạm vi chat@chatgpt-web -> codex bị bỏ qua, web phục vụ.
T10: stream=true: SSE từ codex khi 200; SSE từ web khi codex 429.
T11: GET /api/tai-khoan/duong-dung phản ánh đúng trạng thái het_han_muc và dang_dung_duong.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app import kho, kho_tai_khoan, khoa_khach

GOC = Path(__file__).resolve().parents[1]
_KHOA_QT = "khoa-qt-nv271-test-0123456789abcdef"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}
_TOTP = "JBSWY3DPEHPK3PXP"

THU_MUC_CODEX = GOC / "modules" / "openaicodex"
TEN_GOI_CODEX = "openai_codex_nv271_http_test"


def _tai_goi_codex():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI_CODEX or t.startswith(TEN_GOI_CODEX + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI_CODEX)
    goi.__path__ = [str(THU_MUC_CODEX)]
    sys.modules[TEN_GOI_CODEX] = goi
    for ten_tep in ("oauth", "han_muc", "hoi_phuc", "module"):
        spec = importlib.util.spec_from_file_location(
            f"{TEN_GOI_CODEX}.{ten_tep}", THU_MUC_CODEX / f"{ten_tep}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{TEN_GOI_CODEX}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{TEN_GOI_CODEX}.module"]


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def tao_jwt(email="user@example.com", plan="plus", het_sau_giay=8 * 3600, account_id="acc-123"):
    payload = {
        "email": email,
        "exp": int(time.time()) + het_sau_giay,
        "https://api.openai.com/auth": {
            "chatgpt_account_id": account_id,
            "chatgpt_plan_type": plan,
        },
        "https://api.openai.com/profile": {"email": email},
    }
    return f"{_b64({'alg': 'none'})}.{_b64(payload)}.chu-ky-gia"


@pytest.fixture()
def moi_truong(tmp_path, monkeypatch):
    """Lõi thật với TestClient, dữ liệu tạm cô lập."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.so_module import SoModule

    data_dir = tmp_path / "data-http"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", _KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")

    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        yield client, so


class ModuleWebGia:
    """Module giả cho đường web (chatgpt-web)."""

    def __init__(self, danh_sach_email=None, kich_ban=None):
        self.tien_to = "chatgpt-web"
        self.manifest = {
            "id": "chatgpt-web",
            "tien_to": "chatgpt-web",
            "nhom": {"id": "chatgpt", "ten": "ChatGPT"},
            "duong": "web",
            "cung_cap": ["chat"],
        }
        self.danh_sach_email = [e.casefold() for e in (danh_sach_email or [])]
        self.kich_ban = kich_ban or {}  # email -> (ma, headers, body)
        self.lich_su_ghim: list[str | None] = []
        self.so_lan_goi = 0
        self.danh_sach_model = ["chatgpt-web/gpt-5.5", "chatgpt-web/gpt-5"]

    def co_tai_khoan_email(self, email: str) -> bool:
        return str(email).casefold() in self.danh_sach_email

    async def models(self):
        return [{"id": m} for m in self.danh_sach_model]

    async def chat(self, body, stream=False, *, tai_khoan_email=None):
        self.lich_su_ghim.append(tai_khoan_email)
        self.so_lan_goi += 1

        if tai_khoan_email is not None and tai_khoan_email.casefold() not in self.danh_sach_email:
            return 503, {"content-type": "application/json"}, b'{"error": "khong co tai khoan"}'

        kb = self.kich_ban.get(tai_khoan_email) or self.kich_ban.get("*")
        if kb:
            ma, hd, than = kb
            if callable(than):
                than = than()
            return ma, hd, than

        if stream:
            async def luong():
                yield b'data: {"choices":[{"delta":{"content":"tra loi web"}}]}\n\n'
                yield b'data: [DONE]\n\n'
            return 200, {"content-type": "text/event-stream"}, luong()

        return 200, {"content-type": "application/json"}, json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "tra loi web"}}]
        }).encode("utf-8")


def _cai_module(so, mid, mod):
    so.catalog[mid] = SimpleNamespace(tien_to=mod.tien_to, manifest=mod.manifest)
    so.dang_chay[mid] = mod


def _nap_tai_khoan_codex(m_codex, ds_tk):
    m_codex.kho.luu("tai_khoan", {"tai_khoan": [
        {
            "access_token": t["token"],
            "refresh_token": t.get("refresh", "rf-gia"),
            "email": t["email"],
            "expires_at": time.time() + 8 * 3600,
            "trang_thai": "hoat_dong",
        } for t in ds_tk
    ]})
    m_codex.nap({})


# ===========================================================================
# Các ca test BẮT BUỘC T1 - T11
# ===========================================================================

def test_t1_codex_con_han_muc_chay_codex(moi_truong):
    client, so = moi_truong
    mod_codex_cls = _tai_goi_codex()
    manifest_codex = json.loads((THU_MUC_CODEX / "manifest.json").read_text(encoding="utf-8"))
    m_codex = mod_codex_cls.Module(dict(manifest_codex))
    m_codex.kho = kho.kho_rieng("openaicodex")

    email_a = "user.a.t1@example.com"
    token_a = tao_jwt(email=email_a)
    _nap_tai_khoan_codex(m_codex, [{"token": token_a, "email": email_a}])

    dem_codex = {token_a: 0}

    def xu_ly_codex(req: httpx.Request) -> httpx.Response:
        auth = req.headers.get("authorization", "")
        t = auth[7:] if auth.startswith("Bearer ") else ""
        if t in dem_codex:
            dem_codex[t] += 1
        sse = 'data: {"type": "response.output_text.delta", "delta": "tra loi codex"}\n\ndata: {"type": "response.completed", "response": {}}\n\n'
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

    m_codex._gan_transport_gia(httpx.MockTransport(xu_ly_codex))

    m_web = ModuleWebGia(danh_sach_email=[email_a])

    _cai_module(so, "openaicodex", m_codex)
    _cai_module(so, "chatgpt-web", m_web)

    # Lưu tài khoản kho
    tk = kho_tai_khoan.luu_tai_khoan(
        email_a, "MatKhau123!", _TOTP,
        nhom="chatgpt", loai_tai_khoan="web", dich_dung="web", dung_cho=["web", "codex"]
    )
    kho_tai_khoan.dat_trang_thai_duong(tk["id"], "codex", "san_sang")
    kho_tai_khoan.dat_trang_thai_duong(tk["id"], "web", "san_sang")

    # Gọi chatgpt-web/gpt-5.5
    res = client.post("/v1/chat/completions", headers=_QT, json={
        "model": "chatgpt-web/gpt-5.5",
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert res.status_code == 200
    du_lieu = res.json()
    assert du_lieu["choices"][0]["message"]["content"] == "tra loi codex"
    assert res.headers.get("x-web2api-duong-dung") == "codex"
    assert dem_codex[token_a] == 1
    assert m_web.so_lan_goi == 0


def test_t2_codex_429_chuyen_sang_web_va_ghi_trang_thai(moi_truong):
    client, so = moi_truong
    mod_codex_cls = _tai_goi_codex()
    manifest_codex = json.loads((THU_MUC_CODEX / "manifest.json").read_text(encoding="utf-8"))
    m_codex = mod_codex_cls.Module(dict(manifest_codex))
    m_codex.kho = kho.kho_rieng("openaicodex")

    email_a = "user.a.t2@example.com"
    token_a = tao_jwt(email=email_a)
    _nap_tai_khoan_codex(m_codex, [{"token": token_a, "email": email_a}])

    dem_codex = {token_a: 0}
    now = int(time.time())
    reset_epoch = now + 600

    def xu_ly_codex(req: httpx.Request) -> httpx.Response:
        auth = req.headers.get("authorization", "")
        t = auth[7:] if auth.startswith("Bearer ") else ""
        if t in dem_codex:
            dem_codex[t] += 1
        return httpx.Response(429, json={"error": {"code": "usage_limit_reached", "resets_at": reset_epoch}},
                              headers={"x-codex-primary-reset-at": str(reset_epoch)})

    m_codex._gan_transport_gia(httpx.MockTransport(xu_ly_codex))

    m_web = ModuleWebGia(danh_sach_email=[email_a])

    _cai_module(so, "openaicodex", m_codex)
    _cai_module(so, "chatgpt-web", m_web)

    tk = kho_tai_khoan.luu_tai_khoan(
        email_a, "MatKhau123!", _TOTP,
        nhom="chatgpt", loai_tai_khoan="web", dich_dung="web", dung_cho=["web", "codex"]
    )
    kho_tai_khoan.dat_trang_thai_duong(tk["id"], "codex", "san_sang")
    kho_tai_khoan.dat_trang_thai_duong(tk["id"], "web", "san_sang")

    # Lượt 1: Codex 429 -> web ghim đúng email_a
    res = client.post("/v1/chat/completions", headers=_QT, json={
        "model": "chatgpt-web/gpt-5.5",
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert res.status_code == 200
    assert res.json()["choices"][0]["message"]["content"] == "tra loi web"
    assert res.headers.get("x-web2api-duong-dung") == "web"
    assert dem_codex[token_a] == 1
    assert m_web.so_lan_goi == 1
    assert m_web.lich_su_ghim[-1] == email_a

    # Kiểm tra kho
    tt = kho_tai_khoan.lay_trang_thai_duong(tk["id"])
    assert tt["codex"]["trang_thai"] == "het_han_muc"
    assert now + 500 <= tt["codex"]["moc_hoi_lai"] <= now + 700

    # Lượt 2 ngay sau đó: codex không bị gọi thêm, web nhận lượt thứ 2
    res2 = client.post("/v1/chat/completions", headers=_QT, json={
        "model": "chatgpt-web/gpt-5.5",
        "messages": [{"role": "user", "content": "hello again"}],
    })
    assert res2.status_code == 200
    assert dem_codex[token_a] == 1  # Không đổi
    assert m_web.so_lan_goi == 2


def test_t3_tai_khoan_chi_co_web_khong_goi_codex(moi_truong):
    client, so = moi_truong
    mod_codex_cls = _tai_goi_codex()
    manifest_codex = json.loads((THU_MUC_CODEX / "manifest.json").read_text(encoding="utf-8"))
    m_codex = mod_codex_cls.Module(dict(manifest_codex))
    m_codex.kho = kho.kho_rieng("openaicodex")

    email_khac = "khac@example.com"
    token_khac = tao_jwt(email=email_khac)
    _nap_tai_khoan_codex(m_codex, [{"token": token_khac, "email": email_khac}])

    dem_codex = {token_khac: 0}

    def xu_ly_codex(req: httpx.Request) -> httpx.Response:
        auth = req.headers.get("authorization", "")
        t = auth[7:] if auth.startswith("Bearer ") else ""
        if t in dem_codex:
            dem_codex[t] += 1
        return httpx.Response(200, content=b"codex ok")

    m_codex._gan_transport_gia(httpx.MockTransport(xu_ly_codex))

    email_web = "chi.web.t3@example.com"
    m_web = ModuleWebGia(danh_sach_email=[email_web])

    _cai_module(so, "openaicodex", m_codex)
    _cai_module(so, "chatgpt-web", m_web)

    # Tài khoản kho chỉ có dung_cho=["web"]
    tk = kho_tai_khoan.luu_tai_khoan(
        email_web, "MatKhau123!", _TOTP,
        nhom="chatgpt", loai_tai_khoan="web", dich_dung="web", dung_cho=["web"]
    )
    kho_tai_khoan.dat_trang_thai_duong(tk["id"], "web", "san_sang")

    res = client.post("/v1/chat/completions", headers=_QT, json={
        "model": "chatgpt-web/gpt-5.5",
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert res.status_code == 200
    assert m_web.so_lan_goi == 1
    assert dem_codex[token_khac] == 0


def test_t4_cung_tai_khoan_a_sang_web_a_khong_dong_b(moi_truong):
    client, so = moi_truong
    mod_codex_cls = _tai_goi_codex()
    manifest_codex = json.loads((THU_MUC_CODEX / "manifest.json").read_text(encoding="utf-8"))
    m_codex = mod_codex_cls.Module(dict(manifest_codex))
    m_codex.kho = kho.kho_rieng("openaicodex")

    email_1 = "acc1.t4@example.com"
    email_2 = "acc2.t4@example.com"
    tk1 = kho_tai_khoan.luu_tai_khoan(email_1, "MatKhau1!", _TOTP, nhom="chatgpt", loai_tai_khoan="web", dich_dung="web", dung_cho=["web", "codex"])
    tk2 = kho_tai_khoan.luu_tai_khoan(email_2, "MatKhau2!", _TOTP, nhom="chatgpt", loai_tai_khoan="web", dich_dung="web", dung_cho=["web", "codex"])

    # Xác định vai A (đứng đầu) và B (đứng sau) theo kho_tai_khoan.liet_ke()
    ds = kho_tai_khoan.liet_ke()
    id_dau = ds[0]["id"]
    if id_dau == tk1["id"]:
        email_a, email_b = email_1, email_2
        id_a, id_b = tk1["id"], tk2["id"]
    else:
        email_a, email_b = email_2, email_1
        id_a, id_b = tk2["id"], tk1["id"]

    kho_tai_khoan.dat_trang_thai_duong(id_a, "codex", "san_sang")
    kho_tai_khoan.dat_trang_thai_duong(id_a, "web", "san_sang")
    kho_tai_khoan.dat_trang_thai_duong(id_b, "codex", "san_sang")
    kho_tai_khoan.dat_trang_thai_duong(id_b, "web", "san_sang")

    token_a = tao_jwt(email=email_a)
    token_b = tao_jwt(email=email_b)
    _nap_tai_khoan_codex(m_codex, [
        {"token": token_a, "email": email_a},
        {"token": token_b, "email": email_b},
    ])

    dem_codex = {token_a: 0, token_b: 0}

    def xu_ly_codex(req: httpx.Request) -> httpx.Response:
        auth = req.headers.get("authorization", "")
        t = auth[7:] if auth.startswith("Bearer ") else ""
        if t in dem_codex:
            dem_codex[t] += 1
        if t == token_a:
            return httpx.Response(429, json={"error": {"code": "usage_limit_reached"}},
                                  headers={"x-codex-primary-reset-at": str(int(time.time()) + 600)})
        return httpx.Response(200, content=b'data: {"type": "response.output_text.delta", "delta": "b codex"}\n\ndata: {"type": "response.completed", "response": {}}\n\n',
                              headers={"content-type": "text/event-stream"})

    m_codex._gan_transport_gia(httpx.MockTransport(xu_ly_codex))
    m_web = ModuleWebGia(danh_sach_email=[email_a, email_b])

    _cai_module(so, "openaicodex", m_codex)
    _cai_module(so, "chatgpt-web", m_web)

    # Chạy lần 1: Codex A 429 -> Web A 200; B không bị đụng
    res = client.post("/v1/chat/completions", headers=_QT, json={
        "model": "chatgpt-web/gpt-5.5",
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert res.status_code == 200
    assert res.json()["choices"][0]["message"]["content"] == "tra loi web"
    assert dem_codex[token_a] == 1
    assert dem_codex[token_b] == 0
    assert m_web.lich_su_ghim == [email_a]

    # Biến thể: Web A cũng hỏng 500 -> mới sang B, B dùng Codex B
    m_web.kich_ban[email_a] = (500, {"content-type": "application/json"}, b'{"error": "web a loi 500"}')
    res2 = client.post("/v1/chat/completions", headers=_QT, json={
        "model": "chatgpt-web/gpt-5.5",
        "messages": [{"role": "user", "content": "hello again"}],
    })
    assert res2.status_code == 200
    assert res2.json()["choices"][0]["message"]["content"] == "b codex"
    assert dem_codex[token_b] == 1


def test_t5_hoi_quy_khong_co_ban_ghi_kho_goi_thang(moi_truong):
    client, so = moi_truong
    m_web = ModuleWebGia(danh_sach_email=["random@example.com"])
    _cai_module(so, "chatgpt-web", m_web)

    # Không có tài khoản nào trong kho
    assert len(kho_tai_khoan.liet_ke()) == 0

    res = client.post("/v1/chat/completions", headers=_QT, json={
        "model": "chatgpt-web/gpt-5.5",
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert res.status_code == 200
    assert m_web.so_lan_goi == 1
    # Không ghim email
    assert m_web.lich_su_ghim[-1] is None


def test_t6_chan_bot_khong_ghi_hong_duong(moi_truong):
    client, so = moi_truong
    mod_codex_cls = _tai_goi_codex()
    manifest_codex = json.loads((THU_MUC_CODEX / "manifest.json").read_text(encoding="utf-8"))
    m_codex = mod_codex_cls.Module(dict(manifest_codex))
    m_codex.kho = kho.kho_rieng("openaicodex")

    email_1 = "acc1.t6@example.com"
    email_2 = "acc2.t6@example.com"
    tk1 = kho_tai_khoan.luu_tai_khoan(email_1, "MatKhau1!", _TOTP, nhom="chatgpt", loai_tai_khoan="web", dich_dung="web", dung_cho=["web", "codex"])
    tk2 = kho_tai_khoan.luu_tai_khoan(email_2, "MatKhau2!", _TOTP, nhom="chatgpt", loai_tai_khoan="web", dich_dung="web", dung_cho=["web", "codex"])

    ds = kho_tai_khoan.liet_ke()
    if ds[0]["id"] == tk1["id"]:
        email_a, email_b = email_1, email_2
        id_a, id_b = tk1["id"], tk2["id"]
    else:
        email_a, email_b = email_2, email_1
        id_a, id_b = tk2["id"], tk1["id"]

    kho_tai_khoan.dat_trang_thai_duong(id_a, "codex", "san_sang")
    kho_tai_khoan.dat_trang_thai_duong(id_a, "web", "san_sang")
    kho_tai_khoan.dat_trang_thai_duong(id_b, "codex", "san_sang")
    kho_tai_khoan.dat_trang_thai_duong(id_b, "web", "san_sang")

    token_a = tao_jwt(email=email_a)
    token_b = tao_jwt(email=email_b)
    _nap_tai_khoan_codex(m_codex, [
        {"token": token_a, "email": email_a},
        {"token": token_b, "email": email_b},
    ])

    def xu_ly_codex(req: httpx.Request) -> httpx.Response:
        auth = req.headers.get("authorization", "")
        t = auth[7:] if auth.startswith("Bearer ") else ""
        if t == token_a:
            return httpx.Response(429, json={"error": {"code": "usage_limit_reached"}},
                                  headers={"x-codex-primary-reset-at": str(int(time.time()) + 600)})
        return httpx.Response(200, content=b'data: {"type": "response.output_text.delta", "delta": "codex b"}\n\ndata: {"type": "response.completed", "response": {}}\n\n',
                              headers={"content-type": "text/event-stream"})

    m_codex._gan_transport_gia(httpx.MockTransport(xu_ly_codex))

    # Web A trả 403 kèm x-web2api-loi-loai: chan_bot
    m_web = ModuleWebGia(danh_sach_email=[email_a, email_b])
    m_web.kich_ban[email_a] = (
        403,
        {"content-type": "application/json", "x-web2api-loi-loai": "chan_bot"},
        b'{"error": "bot blocked"}',
    )

    _cai_module(so, "openaicodex", m_codex)
    _cai_module(so, "chatgpt-web", m_web)

    res = client.post("/v1/chat/completions", headers=_QT, json={
        "model": "chatgpt-web/gpt-5.5",
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert res.status_code == 200
    assert res.json()["choices"][0]["message"]["content"] == "codex b"

    # Kho: đường web của A VẪN san_sang (không bị ghi can_dang_nhap_lai)
    tt_a = kho_tai_khoan.lay_trang_thai_duong(id_a)
    assert tt_a["web"]["trang_thai"] == "san_sang"

    # Biến thể: 403 KHÔNG có tiêu đề -> ghi can_dang_nhap_lai
    m_web.kich_ban[email_a] = (403, {"content-type": "application/json"}, b'{"error": "forbidden"}')
    # Reset codex của A để thử
    kho_tai_khoan.dat_trang_thai_duong(id_a, "codex", "san_sang")
    client.post("/v1/chat/completions", headers=_QT, json={
        "model": "chatgpt-web/gpt-5.5",
        "messages": [{"role": "user", "content": "hello"}],
    })
    tt_a2 = kho_tai_khoan.lay_trang_thai_duong(id_a)
    assert tt_a2["web"]["trang_thai"] == "can_dang_nhap_lai"


def test_t7_codex_401_can_dang_nhap_lai_sang_web(moi_truong):
    client, so = moi_truong
    mod_codex_cls = _tai_goi_codex()
    manifest_codex = json.loads((THU_MUC_CODEX / "manifest.json").read_text(encoding="utf-8"))
    m_codex = mod_codex_cls.Module(dict(manifest_codex))
    m_codex.kho = kho.kho_rieng("openaicodex")

    email_a = "user.a.t7@example.com"
    token_a = tao_jwt(email=email_a)
    _nap_tai_khoan_codex(m_codex, [{"token": token_a, "email": email_a}])

    dem_codex = {token_a: 0}

    def xu_ly_codex(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "oauth/token" in url:
            # Refresh token cũng lỗi
            return httpx.Response(400, json={"error": "invalid_grant"})
        auth = req.headers.get("authorization", "")
        t = auth[7:] if auth.startswith("Bearer ") else ""
        if t in dem_codex:
            dem_codex[t] += 1
        return httpx.Response(401, json={"error": {"code": "invalid_token"}})

    m_codex._gan_transport_gia(httpx.MockTransport(xu_ly_codex))
    m_web = ModuleWebGia(danh_sach_email=[email_a])

    _cai_module(so, "openaicodex", m_codex)
    _cai_module(so, "chatgpt-web", m_web)

    tk = kho_tai_khoan.luu_tai_khoan(
        email_a, "MatKhau123!", _TOTP,
        nhom="chatgpt", loai_tai_khoan="web", dich_dung="web", dung_cho=["web", "codex"]
    )
    kho_tai_khoan.dat_trang_thai_duong(tk["id"], "codex", "san_sang")
    kho_tai_khoan.dat_trang_thai_duong(tk["id"], "web", "san_sang")

    res = client.post("/v1/chat/completions", headers=_QT, json={
        "model": "chatgpt-web/gpt-5.5",
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert res.status_code == 200
    assert res.json()["choices"][0]["message"]["content"] == "tra loi web"
    tt = kho_tai_khoan.lay_trang_thai_duong(tk["id"])
    assert tt["codex"]["trang_thai"] == "can_dang_nhap_lai"

    # Lượt sau không gọi lại codex
    dem_cu = dem_codex[token_a]
    res2 = client.post("/v1/chat/completions", headers=_QT, json={
        "model": "chatgpt-web/gpt-5.5",
        "messages": [{"role": "user", "content": "hello again"}],
    })
    assert res2.status_code == 200
    assert dem_codex[token_a] == dem_cu


def test_t8_model_khong_co_trong_codex_bo_qua(moi_truong):
    client, so = moi_truong
    mod_codex_cls = _tai_goi_codex()
    manifest_codex = json.loads((THU_MUC_CODEX / "manifest.json").read_text(encoding="utf-8"))
    m_codex = mod_codex_cls.Module(dict(manifest_codex))
    m_codex.kho = kho.kho_rieng("openaicodex")

    email_a = "user.a.t8@example.com"
    token_a = tao_jwt(email=email_a)
    _nap_tai_khoan_codex(m_codex, [{"token": token_a, "email": email_a}])

    dem_codex = {token_a: 0}

    def xu_ly_codex(req: httpx.Request) -> httpx.Response:
        auth = req.headers.get("authorization", "")
        t = auth[7:] if auth.startswith("Bearer ") else ""
        if t in dem_codex:
            dem_codex[t] += 1
        return httpx.Response(200, content=b"ok")

    m_codex._gan_transport_gia(httpx.MockTransport(xu_ly_codex))

    # Web hỗ trợ model đặc thù "gpt-4o", codex không có gpt-4o
    m_web = ModuleWebGia(danh_sach_email=[email_a])
    m_web.danh_sach_model = ["chatgpt-web/gpt-4o"]

    _cai_module(so, "openaicodex", m_codex)
    _cai_module(so, "chatgpt-web", m_web)

    tk = kho_tai_khoan.luu_tai_khoan(
        email_a, "MatKhau123!", _TOTP,
        nhom="chatgpt", loai_tai_khoan="web", dich_dung="web", dung_cho=["web", "codex"]
    )
    kho_tai_khoan.dat_trang_thai_duong(tk["id"], "codex", "san_sang")
    kho_tai_khoan.dat_trang_thai_duong(tk["id"], "web", "san_sang")

    res = client.post("/v1/chat/completions", headers=_QT, json={
        "model": "chatgpt-web/gpt-4o",
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert res.status_code == 200
    assert m_web.so_lan_goi == 1
    assert dem_codex[token_a] == 0  # Codex bị bỏ qua 0 lần
    # Kho không bị ghi gì hỏng
    tt = kho_tai_khoan.lay_trang_thai_duong(tk["id"])
    assert tt["codex"]["trang_thai"] == "san_sang"


def test_t9_khoa_khach_gioi_han_pham_vi_bo_qua_codex(moi_truong):
    client, so = moi_truong
    mod_codex_cls = _tai_goi_codex()
    manifest_codex = json.loads((THU_MUC_CODEX / "manifest.json").read_text(encoding="utf-8"))
    m_codex = mod_codex_cls.Module(dict(manifest_codex))
    m_codex.kho = kho.kho_rieng("openaicodex")

    email_a = "user.a.t9@example.com"
    token_a = tao_jwt(email=email_a)
    _nap_tai_khoan_codex(m_codex, [{"token": token_a, "email": email_a}])

    dem_codex = {token_a: 0}

    def xu_ly_codex(req: httpx.Request) -> httpx.Response:
        auth = req.headers.get("authorization", "")
        t = auth[7:] if auth.startswith("Bearer ") else ""
        if t in dem_codex:
            dem_codex[t] += 1
        return httpx.Response(200, content=b"ok")

    m_codex._gan_transport_gia(httpx.MockTransport(xu_ly_codex))

    m_web = ModuleWebGia(danh_sach_email=[email_a])

    _cai_module(so, "openaicodex", m_codex)
    _cai_module(so, "chatgpt-web", m_web)

    tk = kho_tai_khoan.luu_tai_khoan(
        email_a, "MatKhau123!", _TOTP,
        nhom="chatgpt", loai_tai_khoan="web", dich_dung="web", dung_cho=["web", "codex"]
    )
    kho_tai_khoan.dat_trang_thai_duong(tk["id"], "codex", "san_sang")
    kho_tai_khoan.dat_trang_thai_duong(tk["id"], "web", "san_sang")

    # Tạo khoá khách CHỈ có phạm vi chat@chatgpt-web
    _, khoa_kh = khoa_khach.tao_khoa(nhan="khach web only", pham_vi=["chat@chatgpt-web"])
    auth_khach = {"Authorization": f"Bearer {khoa_kh}"}

    res = client.post("/v1/chat/completions", headers=auth_khach, json={
        "model": "chatgpt-web/gpt-5.5",
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert res.status_code == 200
    assert m_web.so_lan_goi == 1
    assert dem_codex[token_a] == 0


def test_t10_stream_true_sse(moi_truong):
    client, so = moi_truong
    mod_codex_cls = _tai_goi_codex()
    manifest_codex = json.loads((THU_MUC_CODEX / "manifest.json").read_text(encoding="utf-8"))
    m_codex = mod_codex_cls.Module(dict(manifest_codex))
    m_codex.kho = kho.kho_rieng("openaicodex")

    email_a = "user.a.t10@example.com"
    token_a = tao_jwt(email=email_a)
    _nap_tai_khoan_codex(m_codex, [{"token": token_a, "email": email_a}])

    che_do = {"429": False}

    def xu_ly_codex(req: httpx.Request) -> httpx.Response:
        if che_do["429"]:
            return httpx.Response(429, json={"error": {"code": "usage_limit_reached"}},
                                  headers={"x-codex-primary-reset-at": str(int(time.time()) + 600)})
        sse = 'data: {"type": "response.output_text.delta", "delta": "sse codex"}\n\ndata: {"type": "response.completed", "response": {}}\n\n'
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

    m_codex._gan_transport_gia(httpx.MockTransport(xu_ly_codex))

    m_web = ModuleWebGia(danh_sach_email=[email_a])

    _cai_module(so, "openaicodex", m_codex)
    _cai_module(so, "chatgpt-web", m_web)

    tk = kho_tai_khoan.luu_tai_khoan(
        email_a, "MatKhau123!", _TOTP,
        nhom="chatgpt", loai_tai_khoan="web", dich_dung="web", dung_cho=["web", "codex"]
    )
    kho_tai_khoan.dat_trang_thai_duong(tk["id"], "codex", "san_sang")
    kho_tai_khoan.dat_trang_thai_duong(tk["id"], "web", "san_sang")

    # 1. Codex 200 -> SSE từ codex
    res = client.post("/v1/chat/completions", headers=_QT, json={
        "model": "chatgpt-web/gpt-5.5",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    })
    assert res.status_code == 200
    assert "text/event-stream" in res.headers.get("content-type", "")
    assert "sse codex" in res.text

    # 2. Codex 429 -> SSE từ web
    che_do["429"] = True
    res2 = client.post("/v1/chat/completions", headers=_QT, json={
        "model": "chatgpt-web/gpt-5.5",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    })
    assert res2.status_code == 200
    assert "text/event-stream" in res2.headers.get("content-type", "")
    assert "tra loi web" in res2.text


def test_t11_api_tai_khoan_duong_dung(moi_truong):
    client, so = moi_truong
    # Chạy kịch bản T2 (codex 429 -> web phục vụ, kho ghi het_han_muc cho codex)
    test_t2_codex_429_chuyen_sang_web_va_ghi_trang_thai(moi_truong)

    res = client.get("/api/tai-khoan/duong-dung", headers=_QT)
    assert res.status_code == 200
    danh_sach = res.json().get("tai_khoan") or []
    assert len(danh_sach) == 1
    muc = danh_sach[0]
    assert muc.get("dang_dung_duong") == "web"
    theo_duong = {d["dich"]: d for d in muc.get("duong") or []}
    assert theo_duong["codex"]["trang_thai"] == "het_han_muc"
    assert theo_duong["codex"]["moc_hoi_lai"] > int(time.time())
    assert theo_duong["web"]["trang_thai"] == "san_sang"
