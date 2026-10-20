"""NV199c — module từ xa khai thêm header theo bản ghi (`header_them`).

Đỏ→xanh QUA LÕI THẬT (TestClient + máy chủ giả qua MayChuThat).
Vá cho opencode: header lạ bắt buộc `x-opencode-client: desktop`.
"""

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app import so_module
from app.chuyen_tiep import ChuyenTiepOpenAI
from app.main import app
from gia import MayChuThat

KHOA_QT = "khoa-quan-tri-nv199c-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
KHOA_XA = "khoa-xa-gia-nv199c-abcdef0123456789"
BI_MAT = "bi-mat-header-gia-nv199c-9f8e7d6c5b4a"


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return tmp_path


def dang_ky(c, id_, ten, tien_to, base_url, api_key=KHOA_XA, header_them=None):
    than = {"id": id_, "ten": ten, "tien_to": tien_to, "base_url": base_url}
    if api_key is not None:
        than["api_key"] = api_key
    if header_them is not None:
        than["header_them"] = header_them
    return c.post("/api/modules/tu-xa", json=than, headers=QT)


def chat(c, model, **them):
    than = {"model": model, "messages": [{"role": "user", "content": "xin chao"}], **them}
    return c.post("/v1/chat/completions", json=than, headers=QT)


class GiaOpencodeNho:
    """opencode thu gọn: /zen/v1, chat bắt buộc x-opencode-client: desktop."""

    def __init__(self):
        self.nhat_ky = []
        self.app = Starlette(routes=[
            Route("/zen/v1/models", self.models),
            Route("/zen/v1/chat/completions", self.chat, methods=["POST"]),
        ])

    async def models(self, req):
        self.nhat_ky.append({"duong": "models", "td": dict(req.headers)})
        return JSONResponse({"object": "list", "data": [{"id": "qwen-free", "object": "model"}]})

    async def chat(self, req):
        than = await req.json()
        self.nhat_ky.append({"duong": "chat", "td": dict(req.headers), "than": than})
        if req.headers.get("x-opencode-client") != "desktop":
            return JSONResponse({"error": {"message": "thieu tieu de", "type": "invalid_request_error"}},
                                status_code=400)
        if req.headers.get("authorization") != "Bearer public":
            return JSONResponse({"error": {"message": "sai khoa", "type": "invalid_request_error"}},
                                status_code=401)
        return JSONResponse({"object": "chat.completion", "model": than["model"],
                             "choices": [{"index": 0, "message": {"role": "assistant",
                                                                 "content": "opencode tra loi"},
                                          "finish_reason": "stop"}]})


def test_dang_ky_doi_admin_key(du_lieu):
    """Đường khai báo yêu cầu quyền quản trị: không/khóa sai → 401."""
    g = GiaOpencodeNho()
    with MayChuThat(g.app) as s, TestClient(app) as c:
        than = {"id": "oc-401", "ten": "X", "tien_to": "oc401", "base_url": s.url + "/zen/v1",
                "header_them": {"x-opencode-client": "desktop"}}
        assert c.post("/api/modules/tu-xa", json=than).status_code == 401
        assert c.post("/api/modules/tu-xa", json=than,
                      headers={"Authorization": "Bearer sai"}).status_code == 401


def test_opencode_chat_200_voi_header_them(du_lieu):
    """Có header_them: chat đi qua, fake thấy đúng header → 200 (đây là ca vá của NV199c)."""
    g = GiaOpencodeNho()
    with MayChuThat(g.app) as s, TestClient(app) as c:
        r = dang_ky(c, "oc-h", "OpenCode", "och", s.url + "/zen/v1", "public",
                    header_them={"x-opencode-client": "desktop"})
        assert r.status_code == 200, r.text
        r2 = chat(c, "och/qwen-free")
        assert r2.status_code == 200, r2.text
        assert r2.json()["choices"][0]["message"]["content"] == "opencode tra loi"
        assert g.nhat_ky[-1]["td"]["x-opencode-client"] == "desktop"


def test_khong_header_them_thieu_tieu_de_400(du_lieu):
    """Không khai header: fake vẫn 400 như đo ở NV199a (hành vi cũ giữ nguyên)."""
    g = GiaOpencodeNho()
    with MayChuThat(g.app) as s, TestClient(app) as c:
        assert dang_ky(c, "oc-k", "OpenCode", "ock", s.url + "/zen/v1", "public").status_code == 200
        r2 = chat(c, "ock/qwen-free")
        assert r2.status_code == 400, r2.text


def _loi_400(c, base_url, header_them, api_key=KHOA_XA):
    return dang_ky(c, "oc-x", "X", "ocx", base_url, api_key, header_them)


def test_tu_choi_ten_host(du_lieu):
    g = GiaOpencodeNho()
    with MayChuThat(g.app) as s, TestClient(app) as c:
        r = _loi_400(c, s.url + "/zen/v1", {"Host": "evil.example"})
        assert r.status_code == 400, r.text


def test_tu_choi_hop_by_hop_connection(du_lieu):
    g = GiaOpencodeNho()
    with MayChuThat(g.app) as s, TestClient(app) as c:
        r = _loi_400(c, s.url + "/zen/v1", {"Connection": "keep-alive"})
        assert r.status_code == 400, r.text


def test_tu_choi_te_va_proxy_authorization(du_lieu):
    g = GiaOpencodeNho()
    with MayChuThat(g.app) as s, TestClient(app) as c:
        assert _loi_400(c, s.url + "/zen/v1", {"TE": "trailers"}).status_code == 400
        assert _loi_400(c, s.url + "/zen/v1", {"Proxy-Authorization": "Basic x"}).status_code == 400


def test_tu_choi_crlf_trong_gia_tri(du_lieu):
    g = GiaOpencodeNho()
    with MayChuThat(g.app) as s, TestClient(app) as c:
        r = _loi_400(c, s.url + "/zen/v1", {"x-a": "mot\r\nX-Injected: 1"})
        assert r.status_code == 400, r.text
        assert "X-Injected" not in r.text  # giá trị độc không dội lại qua lỗi


def test_tu_choi_ky_tu_dieu_khien_trong_ten(du_lieu):
    g = GiaOpencodeNho()
    with MayChuThat(g.app) as s, TestClient(app) as c:
        assert _loi_400(c, s.url + "/zen/v1", {"x-a\x01b": "v"}).status_code == 400
        assert _loi_400(c, s.url + "/zen/v1", {"x a": "v"}).status_code == 400


def test_tu_choi_authorization_khi_co_khoa(du_lieu):
    """Đã có api_key (lõi gửi Bearer) mà còn đặt tay Authorization → 400 (trùng/xung đột)."""
    g = GiaOpencodeNho()
    with MayChuThat(g.app) as s, TestClient(app) as c:
        r = _loi_400(c, s.url + "/zen/v1", {"Authorization": "ApiKey xyz"}, api_key="public")
        assert r.status_code == 400, r.text


def test_cho_phep_authorization_khi_khong_khoa(du_lieu):
    """Không api_key (lõi không gửi Authorization) → được đặt tay scheme lạ."""
    g = GiaOpencodeNho()
    with MayChuThat(g.app) as s, TestClient(app) as c:
        r = dang_ky(c, "oc-au", "AU", "ocau", s.url + "/zen/v1", api_key=None,
                    header_them={"Authorization": "ApiKey khong-bearer"})
        assert r.status_code == 200, r.text
        assert g.nhat_ky  # models đã gọi lúc đăng ký/kiểm
        td = g.nhat_ky[-1]["td"]
        assert td.get("authorization") == "ApiKey khong-bearer"


def test_tu_choi_qua_16_header(du_lieu):
    g = GiaOpencodeNho()
    with MayChuThat(g.app) as s, TestClient(app) as c:
        r = _loi_400(c, s.url + "/zen/v1", {f"x-h{i}": "v" for i in range(17)})
        assert r.status_code == 400, r.text


def test_tu_choi_gia_tri_qua_8192_ky_tu(du_lieu):
    g = GiaOpencodeNho()
    with MayChuThat(g.app) as s, TestClient(app) as c:
        r = _loi_400(c, s.url + "/zen/v1", {"x-a": "v" * 8193})
        assert r.status_code == 400, r.text


def test_gia_tri_header_khong_vao_log(du_lieu):
    """Giá trị header được che như khoá khi qua bộ lọc log/lỗi."""
    ct = ChuyenTiepOpenAI(ten="t", tien_to="t", base_url="http://127.0.0.1:9/v1",
                          khoa=[], header_them={"x-bi-mat": BI_MAT})
    da_che = ct.che_khoa(f"lỗi khi gọi upstream, đã gửi {BI_MAT} đi")
    assert BI_MAT not in da_che
    assert "…" in da_che


def test_gia_tri_khong_lo_qua_suc_khoe_va_danh_sach(du_lieu):
    g = GiaOpencodeNho()
    with MayChuThat(g.app) as s, TestClient(app) as c:
        r = dang_ky(c, "oc-s", "OpenCode", "ocs", s.url + "/zen/v1", "public",
                    header_them={"x-bi-mat": BI_MAT, "x-opencode-client": "desktop"})
        assert r.status_code == 200, r.text
        assert BI_MAT not in r.text  # phản hồi đăng ký
        sk = c.get("/api/modules/oc-s/suc-khoe", headers=QT)
        assert sk.status_code == 200, sk.text
        assert BI_MAT not in sk.text
        ds = c.get("/api/modules", headers=QT)
        assert ds.status_code == 200, ds.text
        assert BI_MAT not in ds.text


def test_header_gui_toi_dung_dia_chi_models_va_chat(du_lieu):
    """Header đi kèm cả /models lẫn /chat (cùng gốc đã khai)."""
    g = GiaOpencodeNho()
    with MayChuThat(g.app) as s, TestClient(app) as c:
        assert dang_ky(c, "oc-m", "OpenCode", "ocm", s.url + "/zen/v1", "public",
                        header_them={"x-opencode-client": "desktop"}).status_code == 200
        assert chat(c, "ocm/qwen-free").status_code == 200
        duong_td = [(n["duong"], n["td"].get("x-opencode-client")) for n in g.nhat_ky]
        assert ("models", "desktop") in duong_td
        assert ("chat", "desktop") in duong_td


def test_header_khong_theo_chuyen_huong(du_lieu):
    """Upstream trả 307 sang máy khác: lõi KHÔNG theo chuyển hướng → header không lọt sang."""
    nhan_b = []

    class MayB:
        def __init__(self):
            self.app = Starlette(routes=[Route("/lay", self.lay, methods=["POST"])])

        async def lay(self, req):
            nhan_b.append(dict(req.headers))
            return JSONResponse({"ok": True})

    b = MayB()

    class MayA:
        def __init__(self, url_b):
            self.url_b = url_b
            self.app = Starlette(routes=[
                Route("/v1/models", self.models),
                Route("/v1/chat/completions", self.chuyen, methods=["POST"]),
            ])

        async def models(self, req):
            return JSONResponse({"object": "list", "data": [{"id": "m", "object": "model"}]})

        async def chuyen(self, req):
            from starlette.responses import Response
            return Response(status_code=307, headers={"location": self.url_b + "/lay"})

    with MayChuThat(b.app) as sb:
        a = MayA(sb.url)
        with MayChuThat(a.app) as sa, TestClient(app) as c:
            assert dang_ky(c, "oc-r", "R", "ocr", sa.url + "/v1", "public",
                            header_them={"x-bi-mat": BI_MAT}).status_code == 200
            chat(c, "ocr/m")
            assert nhan_b == [], "header đã lọt sang máy chuyển hướng tới"


def test_bat_tat_giu_header(du_lieu):
    """Tắt/bật lại module: header vẫn còn (kho bí mật không bị ghi đè mất)."""
    g = GiaOpencodeNho()
    with MayChuThat(g.app) as s, TestClient(app) as c:
        assert dang_ky(c, "oc-b", "OpenCode", "ocb", s.url + "/zen/v1", "public",
                        header_them={"x-opencode-client": "desktop"}).status_code == 200
        assert chat(c, "ocb/qwen-free").status_code == 200
        assert c.post("/api/modules/oc-b/bat-tat", json={"bat": False}, headers=QT).status_code == 200
        assert c.post("/api/modules/oc-b/bat-tat", json={"bat": True}, headers=QT).status_code == 200
        r = chat(c, "ocb/qwen-free")
        assert r.status_code == 200, r.text
        assert g.nhat_ky[-1]["td"]["x-opencode-client"] == "desktop"


def test_nap_lai_giu_header(du_lieu):
    """Nạp lại từ đĩa: thể hiện dựng lại vẫn đọc header từ kho bí mật."""
    g = GiaOpencodeNho()
    with MayChuThat(g.app) as s, TestClient(app) as c:
        assert dang_ky(c, "oc-n", "OpenCode", "ocn", s.url + "/zen/v1", "public",
                        header_them={"x-opencode-client": "desktop"}).status_code == 200
        assert c.post("/api/modules/nap-lai", headers=QT).status_code == 200
        r = chat(c, "ocn/qwen-free")
        assert r.status_code == 200, r.text
        assert g.nhat_ky[-1]["td"]["x-opencode-client"] == "desktop"
