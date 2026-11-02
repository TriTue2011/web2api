"""Test lõi cho đường riêng của module /m/<id>/api/... (NV277 + NV289).

Lõi tự xác thực bằng KHOÁ KHÁCH THẬT do lõi sinh (app/khoa_khach.py), phạm vi
«tich_hop@<module>», + giới hạn LAN; module chỉ là đối tượng giả có tich_hop/luong.
Không Internet, không module thật. Kiểm (nghiệm thu NV289):
- Không header / khoá sai → 401; khoá quản trị, khoá «*», khoá «*@m», khoá của
  module KHÁC, khoá có thêm phạm vi lạ → 403; khoá đúng → 200; khoá đã thu hồi → 401;
  cookie phiên/khoá hẹp bằng X-Web2api-Scope không mở rộng được quyền;
- Khoá tich_hop@m không dùng được ở /v1 và không có khả năng «*»-ngầm;
- Người dùng thường có trần «*» KHÔNG tự tạo được khoá tich_hop (leo thang);
- Module không chạy / không khai duong_module → 404; thân POST quá trần/JSON hỏng → 400;
- Cổng LAN qua đường hầm: peer IP riêng của gateway + XFF/CF-Connecting-IP công cộng
  → KHÔNG phải LAN; peer riêng không header → LAN; khách tự đặt XFF=IP riêng cũng không qua;
- Luồng: vé ký đúng → 200/206, Range lặp lại; vé giả → 404 thân rỗng; vé hết hạn → 404; HEAD chỉ header;
- `tao_duong_luong` có `goc` thì trả URL tuyệt đối.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import duong_module, gioi_han, khoa_khach

KHOA_QT = "khoa-qt-nv289-0123456789abcdef0123456789ab"


class ModGia:
    def __init__(self, nhan):
        self.nhan = nhan
        self.goc_nhan = None

    async def tich_hop(self, duong, phuong_thuc, query, than, goc=""):
        self.goc_nhan = goc
        return 200, {}, {"nhan": self.nhan, "duong": duong,
                         "phuong_thuc": phuong_thuc, "success": True}

    async def luong(self, ve, khoang, phuong_thuc):
        du_lieu = b"0123456789abcdef" * 4
        if khoang == "bytes=10-19":
            return 206, {"Content-Range": f"bytes 10-19/{len(du_lieu)}",
                         "Content-Length": "10", "Accept-Ranges": "bytes"}, du_lieu[10:20]
        return 200, {"Content-Length": str(len(du_lieu)), "Accept-Ranges": "bytes"}, du_lieu


def _so():
    return SimpleNamespace(
        catalog={
            "youtube": SimpleNamespace(manifest={"duong_module": {"lan_chi": True}}),
            "other": SimpleNamespace(manifest={"duong_module": {"lan_chi": True}}),
            "khong-duong": SimpleNamespace(manifest={}),
        },
        dang_chay={"youtube": ModGia("youtube"), "other": ModGia("other"),
                   "khong-duong": ModGia("khong-duong")},
    )


class Moi:
    """Lõi tối thiểu + kho khoá khách thật trên thư mục dữ liệu tạm."""

    def __init__(self, monkeypatch, tmp_path, client=("127.0.0.1", 50000)):
        monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
        monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
        monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
        monkeypatch.delenv("WEB2API_TRUST_PROXY", raising=False)
        monkeypatch.setattr(gioi_han, "_BO", None)  # bộ đếm/ proxy tin cậy là singleton: dựng lại theo env
        khoa_khach.lam_moi_cache()
        self.so = _so()
        app = FastAPI()
        app.include_router(duong_module.router)
        app.state.so = self.so
        self.c = TestClient(app, raise_server_exceptions=False, client=client)

    def khoa(self, pham_vi, nhan="khoa-test"):
        ban_ghi, tho = khoa_khach.tao_khoa(nhan=nhan, pham_vi=pham_vi, so=self.so)
        return ban_ghi, tho

    def goi(self, khoa, duong="/m/youtube/api/integration/health", **kw):
        h = dict(kw.pop("headers", {}))
        if khoa is not None:
            h["Authorization"] = f"Bearer {khoa}"
        return self.c.get(duong, headers=h, **kw)


@pytest.fixture()
def moi(monkeypatch, tmp_path):
    return Moi(monkeypatch, tmp_path)


def test_khong_khoa_va_sai_khoa_401(moi):
    assert moi.goi(None).status_code == 401
    r = moi.goi("sai")
    assert r.status_code == 401 and r.json() == {"error": "invalid_auth"}
    r = moi.c.get("/m/youtube/api/integration/health", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401


def test_khoa_dung_200_va_goc_duoc_truyen(moi):
    _, tho = moi.khoa(["tich_hop@youtube"])
    r = moi.goi(tho, headers={"Host": "192.0.2.10:3031"})
    assert r.status_code == 200 and r.json()["nhan"] == "youtube"
    assert r.headers["Cache-Control"] == "no-store"
    assert moi.so.dang_chay["youtube"].goc_nhan == "http://192.0.2.10:3031"
    assert tho not in r.text


def test_leo_thang_quyen_mot_khoa_moi_loai_403(moi):
    # khoá quản trị: cổng này chỉ nhận khoá khách (giống MCP)
    assert moi.goi(KHOA_QT).status_code == 403
    # khoá «*» và «*@youtube» không phải tich_hop@youtube
    _, tho_sao = moi.khoa(["*"])
    assert moi.goi(tho_sao).status_code == 403
    assert moi.goi(tho_sao, headers={"X-Web2api-Scope": "tich_hop@youtube"}).status_code == 403
    _, tho_sao_m = moi.khoa(["*@youtube"])
    assert moi.goi(tho_sao_m).status_code == 403
    # khoá chat
    _, tho_chat = moi.khoa(["chat"])
    assert moi.goi(tho_chat).status_code == 403
    # khoá của module KHÁC
    _, tho_khac = moi.khoa(["tich_hop@other"])
    assert moi.goi(tho_khac).status_code == 403
    # khoá có thêm phạm vi lạ (không thuần tich_hop@…)
    _, tho_tron = moi.khoa(["tich_hop@youtube", "chat"])
    assert moi.goi(tho_tron).status_code == 403
    # khoá hai module: dùng được cả hai (người dùng chọn module được phép)
    _, tho_hai = moi.khoa(["tich_hop@youtube", "tich_hop@other"])
    assert moi.goi(tho_hai).status_code == 200
    assert moi.goi(tho_hai, "/m/other/api/integration/health").status_code == 200


def test_idor_doi_id_module(moi):
    _, tho = moi.khoa(["tich_hop@youtube"])
    r = moi.goi(tho, "/m/other/api/integration/health")
    assert r.status_code == 403 and "other" not in r.text


def test_thu_hoi_tat_va_doi_module_co_hieu_luc_ngay(moi):
    ban_ghi, tho = moi.khoa(["tich_hop@youtube"])
    assert moi.goi(tho).status_code == 200
    khoa_khach.sua_khoa(ban_ghi["id"], pham_vi=["tich_hop@other"], so=moi.so)
    assert moi.goi(tho).status_code == 403
    khoa_khach.sua_khoa(ban_ghi["id"], pham_vi=["tich_hop@youtube"], so=moi.so)
    assert moi.goi(tho).status_code == 200
    khoa_khach.sua_khoa(ban_ghi["id"], bat=False, so=moi.so)
    assert moi.goi(tho).status_code == 401
    khoa_khach.sua_khoa(ban_ghi["id"], bat=True, so=moi.so)
    khoa_khach.xoa_khoa(ban_ghi["id"])
    assert moi.goi(tho).status_code == 401


def test_khoa_tich_hop_khong_dung_duoc_o_v1_va_api_quan_tri(monkeypatch, tmp_path):
    from app.main import app as app_that
    from app.so_module import SoModule

    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    khoa_khach.lam_moi_cache()
    with TestClient(app_that) as c:
        c.app.state.so = SoModule()
        r = c.post("/api/khoa-khach", headers={"Authorization": f"Bearer {KHOA_QT}"},
                   json={"nhan": "t", "pham_vi": ["tich_hop@youtube"]})
        assert r.status_code == 200, r.text  # module `youtube` có thật trong modules/ của kho
        tho = r.json()["khoa"]["khoa"]
        h = {"Authorization": f"Bearer {tho}"}
        assert c.post("/v1/chat/completions", headers=h,
                      json={"model": "x/y", "messages": []}).status_code in (401, 403)
        assert c.get("/api/khoa-khach", headers=h).status_code in (401, 403)
        # «*» của khoá khách và trần «*» của người dùng KHÔNG bao gồm tich_hop (khả năng nguy hiểm)
        from app.xac_thuc import la_kha_nang_nguy_hiem
        assert la_kha_nang_nguy_hiem("tich_hop") is True
        assert khoa_khach.pham_vi_nam_trong_tran("tich_hop@youtube", ["*"]) is False
        assert khoa_khach.pham_vi_nam_trong_tran("tich_hop@youtube", ["tich_hop@youtube"]) is True
        # (/api/toi/khoa và PATCH khoá chặn bằng chính hàm này: người dùng trần «*» không tự tạo được khoá tich_hop)
        assert khoa_khach.pham_vi_nam_trong_tran("tich_hop@youtube", ["chat", "tich_hop@other"]) is False


def test_module_khong_chay_hoac_khong_khai_duong_404(moi):
    _, tho = moi.khoa(["tich_hop@youtube"])
    assert moi.goi(tho, "/m/khong-co/api/integration/health").status_code == 404
    assert moi.goi(tho, "/m/khong-duong/api/integration/health").status_code == 404


def test_than_post_qua_tran_va_json_hong_400(moi):
    _, tho = moi.khoa(["tich_hop@youtube"])
    h = {"Authorization": f"Bearer {tho}"}
    r = moi.c.post("/m/youtube/api/integration/playlists", content=b"x" * 400_001, headers=h)
    assert r.status_code == 400
    r = moi.c.post("/m/youtube/api/integration/playlists", content=b"{hong", headers=h)
    assert r.status_code == 400


# ───────────── Cổng LAN: đường hầm cloudflared (NV289 mục 3) ─────────────

GATEWAY = "172.18.0.1"      # IP riêng của gateway Docker — peer thấy được khi qua hầm
CONG_CONG = "8.8.8.8"       # IP công cộng thật (chỉ để phân tích chuỗi, không có kết nối nào)
NOI_BO = "192.168.1.50"


def _lan(monkeypatch, peer, headers=None, trust=None):
    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    if trust is None:
        monkeypatch.delenv("WEB2API_TRUST_PROXY", raising=False)
    else:
        monkeypatch.setenv("WEB2API_TRUST_PROXY", trust)
    monkeypatch.setattr(gioi_han, "_BO", None)
    req = SimpleNamespace(client=SimpleNamespace(host=peer),
                          headers={k.lower(): v for k, v in (headers or {}).items()})
    req.headers = _Headers(req.headers)
    return duong_module._la_lan(req)


class _Headers(dict):
    def get(self, k, d=None):
        return super().get(k.lower(), d)


def test_la_lan_peer_rieng_khong_header_chuyen_tiep_la_lan_that(monkeypatch):
    assert _lan(monkeypatch, NOI_BO) is True
    assert _lan(monkeypatch, "127.0.0.1") is True
    assert _lan(monkeypatch, GATEWAY) is True       # HA nối thẳng từ trong mạng Docker/LAN
    assert _lan(monkeypatch, CONG_CONG) is False    # IP công cộng nối thẳng


@pytest.mark.parametrize("trust", [None, "172.18.0.0/16"])
@pytest.mark.parametrize("header", [{"X-Forwarded-For": CONG_CONG}, {"CF-Connecting-IP": CONG_CONG},
                                    {"X-Forwarded-For": CONG_CONG, "CF-Connecting-IP": CONG_CONG}])
def test_la_lan_hau_qua_cloudflared_ip_cong_cong_bi_chan(monkeypatch, trust, header):
    """Peer = IP riêng gateway Docker + header chuyển tiếp là IP công cộng ⇒ KHÔNG phải LAN,
    dù WEB2API_TRUST_PROXY có đặt hay để trống (bỏ trống là mặc định ngoài đời)."""
    assert _lan(monkeypatch, GATEWAY, header, trust=trust) is False


def test_la_lan_khach_tu_dat_xff_ip_rieng_khong_qua(monkeypatch):
    # Qua hầm: cloudflared NỐI IP thật vào cuối XFF; phần bên trái do khách tự gõ.
    gia = {"X-Forwarded-For": f"{NOI_BO}, {CONG_CONG}"}
    assert _lan(monkeypatch, GATEWAY, gia, trust=None) is False       # lõi không tin proxy ⇒ đóng
    assert _lan(monkeypatch, GATEWAY, gia, trust="172.18.0.0/16") is False  # tin proxy ⇒ phần phải = công cộng
    # Khách Internet nối thẳng (không qua proxy) gõ XFF=IP riêng: peer công cộng ⇒ chặn
    assert _lan(monkeypatch, CONG_CONG, {"X-Forwarded-For": NOI_BO}) is False
    # Chỉ có Forwarded/X-Real-IP từ peer lõi không tin: đóng
    assert _lan(monkeypatch, GATEWAY, {"X-Real-IP": CONG_CONG}) is False
    assert _lan(monkeypatch, GATEWAY, {"Forwarded": f"for={CONG_CONG}"}) is False


def test_la_lan_proxy_tin_cay_voi_khach_lan_that_van_qua(monkeypatch):
    # Proxy ngược LAN tin cậy chuyển tiếp khách LAN thật: qua.
    assert _lan(monkeypatch, GATEWAY, {"X-Forwarded-For": NOI_BO}, trust="172.18.0.0/16") is True
    assert _lan(monkeypatch, GATEWAY, {"X-Forwarded-For": NOI_BO, "CF-Connecting-IP": NOI_BO},
                trust="172.18.0.0/16") is True
    # Proxy tin cậy nhưng CF-Connecting-IP công cộng: chặn
    assert _lan(monkeypatch, GATEWAY, {"X-Forwarded-For": NOI_BO, "CF-Connecting-IP": CONG_CONG},
                trust="172.18.0.0/16") is False
    # Loopback (cloudflared cùng máy) với XFF công cộng: chặn
    assert _lan(monkeypatch, "127.0.0.1", {"X-Forwarded-For": CONG_CONG}) is False


def test_qua_ham_ca_hai_nhom_duong_bi_chan_o_http(monkeypatch, tmp_path):
    """Đầu-cuối qua TestClient: peer gateway + XFF công cộng ⇒ integration 403, luồng 404 trống, kể cả khoá/vé đúng."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    moi = Moi(monkeypatch, tmp_path, client=(GATEWAY, 40000))
    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    _, tho = moi.khoa(["tich_hop@youtube"])
    h = {"X-Forwarded-For": CONG_CONG}
    assert moi.goi(tho).status_code == 200                         # LAN thật: qua
    assert moi.goi(tho, headers=h).status_code == 403              # qua hầm: chặn dù khoá đúng
    url = duong_module.tao_duong_luong("youtube", "ve-1")
    assert moi.c.get(url).status_code == 200
    r = moi.c.get(url, headers={"CF-Connecting-IP": CONG_CONG})
    assert r.status_code == 404 and r.content == b""


def test_luong_ve_dung_range_lap_lai_ve_gia_het_han(moi):
    c = moi.c
    url = duong_module.tao_duong_luong("youtube", "ve-1")
    r = c.get(url)
    assert r.status_code == 200 and len(r.content) == 64
    r1 = c.get(url, headers={"Range": "bytes=10-19"})
    r2 = c.get(url, headers={"Range": "bytes=10-19"})
    assert r1.status_code == 206 and r2.status_code == 206
    assert r1.content == r2.content == bytes(b"0123456789abcdef" * 4)[10:20]
    # Vé giả (thêm ký tự vào cuối query) → 404 thân rỗng, không nêu lý do.
    r = c.get(url + "x")
    assert r.status_code == 404 and r.content == b""
    # Vé hết hạn (ký 1 giây rồi đợi qua biên giây — url_ky chỉ hết hạn
    # khi now > _het_han, nên đợi 2,2 giây cho chắc) → 404.
    url_ngan = duong_module.tao_duong_luong("youtube", "ve-2", het_han_giay=1)
    time.sleep(2.2)
    r = c.get(url_ngan)
    assert r.status_code == 404 and r.content == b""
    # HEAD cùng vé: chỉ header, không byte.
    r = c.head(url)
    assert r.status_code == 200 and r.content == b""
    assert r.headers["Content-Length"] == "64"


def test_tao_duong_luong_tuyet_doi_khi_co_goc():
    tuyet_doi = duong_module.tao_duong_luong("youtube", "ve-1", "http://192.0.2.10:3031/")
    assert tuyet_doi.startswith("http://192.0.2.10:3031/m/youtube/api/stream/ve-1?")
    assert duong_module.tao_duong_luong("youtube", "ve-1").startswith("/m/youtube/api/stream/ve-1?")


def test_luong_lan_chi_chan_nguon_ngoai(monkeypatch, tmp_path):
    # Không bật cờ loopback: peer TestClient không phân tích được → chặn.
    moi = Moi(monkeypatch, tmp_path, client=("testclient", 50000))
    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    r = moi.c.get(duong_module.tao_duong_luong("youtube", "ve-1"))
    assert r.status_code == 404
