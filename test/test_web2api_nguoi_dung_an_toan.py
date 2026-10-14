"""Soát bảo mật lần hai của tài khoản người dùng — mỗi test tái hiện một lỗi người soát đã chứng minh bằng mã chạy thật.

1. Bộ theo dõi đăng nhập sai khoá theo chuỗi khách gửi, không trần độ dài → một yêu cầu tên 24 MB ghim RAM vĩnh viễn.
4. Phiên web vai admin không chịu cổng IP quản trị ở /api/toi/* và /v1 → cookie admin lộ dùng từ ngoài cổng đúc được
   khoá «*», đổi được mật khẩu admin.
6. Đổi mật khẩu kiểm băm cũ NGOÀI khoá rồi ghi vô điều kiện → admin «đặt lại mật khẩu» giữa chừng bị lặng lẽ huỷ.
7. khoa_khach.json hỏng bị đọc thành «rỗng» → lần tạo khoá kế tiếp GHI ĐÈ mất mọi khoá cũ.
8. Phản hồi mang bí mật một lần phải `Cache-Control: no-store`; danh sách khoá phải nêu phạm vi HIỆU LỰC.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from fastapi.testclient import TestClient

from app import ip_cho_phep, khoa_khach, nguoi_dung, phien_web
from app.loi_openai import LoiOpenAI
from app.main import app
from app.so_module import SoModule

KHOA_QT = "AIzaSyKIEMNGHIEM-000000000000000000009"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
MK = "MatKhauKiemThu-12345"
IP_TRONG = "192.0.2.5"      # TEST-NET-1
IP_NGOAI = "203.0.113.9"    # TEST-NET-3
MANG_NHA = "192.0.2.0/24"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setenv("WEB2API_DATA", str(d))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    monkeypatch.delenv("WEB2API_QT_IP_CHO_PHEP", raising=False)
    monkeypatch.delenv("WEB2API_TRUST_PROXY", raising=False)
    khoa_khach.lam_moi_cache()
    nguoi_dung._THEO_DOI_SAI.clear()
    ip_cho_phep._LAN_GHI_CUOI.clear()
    with TestClient(app) as c:
        c.app.state.so = SoModule()
        yield c, d
    khoa_khach.lam_moi_cache()
    nguoi_dung._THEO_DOI_SAI.clear()


def _tao(vai: str, ten: str, pv=None) -> dict:
    nd, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai=vai, ten_dang_nhap=ten, mat_khau=MK, pham_vi_toi_da=pv))
    return nd


def _cookie(client, nd_id: str) -> dict:
    p, tok = phien_web.tao_phien(nd_id)
    client.cookies.set("w2a_phien", tok)
    return {"X-CSRF-Token": p["csrf"]}


# ───────────── 1. bộ theo dõi đăng nhập sai có trần theo khoá ─────────────

@pytest.mark.parametrize("ten", ["a" * 1_000_000, "ab", "Tên có dấu", "x" * 33, "co khoang trang"])
def test_ten_sai_dang_khong_tao_muc_theo_doi(moi_truong, ten):
    """Tên KHÔNG thể là tài khoản (sai mẫu/quá dài): cùng 401 đồng nhất, KHÔNG thêm mục vào bộ theo dõi trong RAM."""
    client, _ = moi_truong
    _tao("admin", "admin-a")
    truoc = len(nguoi_dung._THEO_DOI_SAI)
    r = client.post("/api/dang-nhap", json={"ten_dang_nhap": ten, "mat_khau": "x" * 14})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"
    assert len(nguoi_dung._THEO_DOI_SAI) == truoc
    nguoi_dung.ghi_nhan_dang_nhap_sai(ten)  # cả lớp dưới cũng không nhận khoá không đúng mẫu
    assert len(nguoi_dung._THEO_DOI_SAI) == truoc


def test_ten_dung_mau_van_bi_khoa_sau_nhieu_lan_sai(moi_truong):
    """Không làm yếu khoá theo tên: tên đúng mẫu (kể cả chưa tồn tại) vẫn được đếm và khoá."""
    client, _ = moi_truong
    _tao("admin", "admin-a")
    for _ in range(5):
        assert client.post("/api/dang-nhap", json={"ten_dang_nhap": "admin-a", "mat_khau": "sai" * 5}).status_code == 401
    assert "admin-a" in nguoi_dung._THEO_DOI_SAI
    r = client.post("/api/dang-nhap", json={"ten_dang_nhap": "admin-a", "mat_khau": MK})
    assert r.status_code == 401, "đã bị khoá theo tên thì mật khẩu đúng cũng 401"
    client.post("/api/dang-nhap", json={"ten_dang_nhap": "chua-co-ai", "mat_khau": "sai" * 5})
    assert "chua-co-ai" in nguoi_dung._THEO_DOI_SAI


# ───────────── 4. phiên admin chịu cổng IP quản trị ─────────────

def test_cookie_admin_ngoai_cong_ip_bi_403(moi_truong, monkeypatch):
    """Leo thang quyền: cookie admin dùng từ IP ngoài cổng không đúc được khoá «*», không đổi được mật khẩu, không
    gọi được /v1 — y như Bearer admin ở đường quản trị."""
    client, _ = moi_truong
    adm = _tao("admin", "admin-a")
    monkeypatch.setenv("WEB2API_QT_IP_CHO_PHEP", MANG_NHA)
    h = _cookie(client, adm["id"])
    client._transport.client = (IP_NGOAI, 1234)
    r = client.post("/api/toi/khoa", headers=h, json={})
    assert r.status_code == 403 and r.json()["error"]["code"] == "ip_not_allowed", r.text
    assert IP_NGOAI not in r.text
    assert client.get("/api/toi/khoa").status_code == 403
    assert client.get("/api/toi").status_code == 403
    r = client.post("/api/toi/mat-khau", headers=h, json={"mat_khau_cu": MK, "mat_khau_moi": MK + "b"})
    assert r.status_code == 403
    assert client.get("/v1/models").status_code == 403
    assert khoa_khach.danh_sach_khoa() == []
    assert asyncio.run(nguoi_dung.kiem_tra_mat_khau(MK, nguoi_dung.tim_theo_id(adm["id"])["bam_mat_khau"]))


def test_cookie_admin_trong_cong_va_loopback_van_qua(moi_truong, monkeypatch):
    client, _ = moi_truong
    adm = _tao("admin", "admin-a")
    monkeypatch.setenv("WEB2API_QT_IP_CHO_PHEP", MANG_NHA)
    h = _cookie(client, adm["id"])
    for ip in (IP_TRONG, "127.0.0.1"):
        client._transport.client = (ip, 1234)
        assert client.get("/api/toi").status_code == 200, ip
        assert client.post("/api/toi/khoa", headers=h, json={"nhan": ip}).status_code == 200, ip
        assert client.get("/v1/models").status_code == 200, ip


def test_cookie_user_ngoai_cong_ip_khong_bi_cong(moi_truong, monkeypatch):
    """Cổng IP chỉ dành cho quyền quản trị: người dùng thường ngoài cổng vẫn tự phục vụ được (trong trần của họ)."""
    client, _ = moi_truong
    _tao("admin", "admin-a")
    u = _tao("user", "bob-u", ["chat"])
    monkeypatch.setenv("WEB2API_QT_IP_CHO_PHEP", MANG_NHA)
    h = _cookie(client, u["id"])
    client._transport.client = (IP_NGOAI, 1234)
    assert client.get("/api/toi").status_code == 200
    r = client.post("/api/toi/khoa", headers=h, json={})
    assert r.status_code == 200 and r.json()["khoa"]["pham_vi"] == ["chat"]


# ───────────── 6. đổi mật khẩu không huỷ lặng lẽ lần đặt lại của admin ─────────────

def test_doi_mat_khau_dua_voi_dat_lai_cua_admin_tra_409(moi_truong, monkeypatch):
    """Đặt lại mật khẩu của admin chen giữa lúc người dùng đang băm mật khẩu mới: đổi phải 409, mật khẩu admin vừa đặt
    còn hiệu lực (không bị ghi đè bằng mật khẩu người dùng chọn)."""
    _tao("admin", "admin-a")
    u = _tao("user", "carol-u")
    MOI = "UserChosen-Pass-123"
    goc = nguoi_dung.bam_mat_khau
    mk_admin: list[str] = []

    async def bam_chen(mat_khau):
        if mat_khau == MOI and not mk_admin:
            _, mk = await nguoi_dung.dat_lai_mat_khau_nguoi_dung(u["id"])  # admin đặt lại ĐÚNG lúc này
            mk_admin.append(mk)
        return await goc(mat_khau)

    monkeypatch.setattr(nguoi_dung, "bam_mat_khau", bam_chen)
    with pytest.raises(LoiOpenAI) as e:
        asyncio.run(nguoi_dung.doi_mat_khau_chinh_minh(u["id"], MK, MOI))
    assert e.value.ma == 409
    bam = nguoi_dung.tim_theo_id(u["id"])["bam_mat_khau"]
    assert asyncio.run(nguoi_dung.kiem_tra_mat_khau(mk_admin[0], bam)), "mật khẩu admin vừa đặt bị huỷ lặng lẽ"
    assert not asyncio.run(nguoi_dung.kiem_tra_mat_khau(MOI, bam))


def test_doi_mat_khau_khong_dua_van_thanh_cong(moi_truong):
    _tao("admin", "admin-a")
    u = _tao("user", "carol-u")
    asyncio.run(nguoi_dung.doi_mat_khau_chinh_minh(u["id"], MK, "MatKhauMoi-67890"))
    assert asyncio.run(nguoi_dung.kiem_tra_mat_khau("MatKhauMoi-67890", nguoi_dung.tim_theo_id(u["id"])["bam_mat_khau"]))


# ───────────── 7. khoa_khach.json hỏng: đóng khi lỗi, không ghi đè ─────────────

def _sha(p) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.mark.parametrize("hong", ["cat_doi", '{"khoa": "x"}', '[1, 2]', "thieu_bam"])
def test_kho_khoa_khach_hong_khong_bi_ghi_de(moi_truong, hong):
    client, d = moi_truong
    adm = _tao("admin", "admin-a")
    _, tho = khoa_khach.tao_khoa(nhan="a", pham_vi=["chat"])
    khoa_khach.tao_khoa(nhan="b", pham_vi=["chat"])
    p = d / "khoa_khach.json"
    s = p.read_text()
    if hong == "cat_doi":
        p.write_text(s[: len(s) // 2])
    elif hong == "thieu_bam":
        p.write_text(s.replace('"bam"', '"khong_phai_bam"', 1))
    else:
        p.write_text(hong)
    khoa_khach.lam_moi_cache()
    truoc = _sha(p)

    with pytest.raises(LoiOpenAI) as e:
        khoa_khach.tao_khoa(nhan="moi", pham_vi=["chat"])
    assert e.value.ma == 503 and e.value.than["error"]["code"] == "guest_key_store_unreadable"
    r = client.post("/api/khoa-khach", headers=QT, json={"nhan": "moi"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "guest_key_store_unreadable"
    h = _cookie(client, adm["id"])
    assert client.post("/api/toi/khoa", headers=h, json={}).status_code == 503
    client.cookies.clear()
    assert client.get("/api/khoa-khach", headers=QT).status_code == 503
    assert _sha(p) == truoc, "tệp khoá hỏng bị ghi đè — mất mọi khoá cũ"

    # Đường xác thực ĐÓNG (không cấp quyền, không 500); khoá quản trị và /health vẫn chạy để khôi phục được.
    assert khoa_khach.tim_theo_khoa_tho(tho) is None
    assert client.get("/v1/models", headers={"Authorization": f"Bearer {tho}"}).status_code == 401
    assert client.get("/v1/models", headers=QT).status_code == 200
    assert client.get("/api/nguoi-dung", headers=QT).status_code == 200
    assert client.get("/health").status_code == 200
    # Xoá người dùng không bỏ dở giữa chừng (xoá người rồi mới 503 ở bước xoá khoá).
    u = _tao("user", "bob-u")
    assert client.delete(f"/api/nguoi-dung/{u['id']}", headers=QT).status_code == 503
    assert nguoi_dung.tim_theo_id(u["id"]) is not None
    assert _sha(p) == truoc


def test_nhip_dong_bo_khong_ghi_de_tep_hong(moi_truong):
    """Cache đang «bẩn» (đếm lượt) rồi tệp bị sửa hỏng từ ngoài: nhịp đồng bộ không được ghi cache đè lên tệp đó."""
    _, d = moi_truong
    k, tho = khoa_khach.tao_khoa(nhan="a", pham_vi=["chat"])
    assert khoa_khach.tim_theo_khoa_tho(tho) is not None
    khoa_khach.ghi_nhan_su_dung(k["id"])
    p = d / "khoa_khach.json"
    p.write_text("{hong")
    truoc = _sha(p)
    khoa_khach.dong_bo_dia()  # không ném (vòng nền và lúc tắt tiến trình gọi hàm này)
    assert _sha(p) == truoc


# ───────────── 8. no-store cho bí mật một lần; phạm vi hiệu lực trong danh sách ─────────────

def test_phan_hoi_mang_bi_mat_mot_lan_khong_luu_dem(moi_truong):
    client, d = moi_truong
    nguoi_dung.khoi_tao_thiet_lap_lan_dau()
    ma = (d / "ma_thiet_lap").read_text().strip()
    r = client.post("/api/thiet-lap", json={"ma_thiet_lap": ma, "ten_dang_nhap": "admin-a", "mat_khau": MK})
    assert r.status_code == 200 and r.headers.get("cache-control") == "no-store"
    client.cookies.clear()
    r = client.post("/api/dang-nhap", json={"ten_dang_nhap": "admin-a", "mat_khau": MK})
    assert r.status_code == 200 and r.headers.get("cache-control") == "no-store"
    h = {"X-CSRF-Token": r.json()["csrf"]}
    r = client.get("/api/toi")
    assert r.status_code == 200 and r.headers.get("cache-control") == "no-store"
    r = client.post("/api/toi/khoa", headers=h, json={})
    assert r.status_code == 200 and "khoa_tho" in r.json() and r.headers.get("cache-control") == "no-store"
    client.cookies.clear()
    r = client.post("/api/khoa-khach", headers=QT, json={"nhan": "qt"})
    assert r.status_code == 200 and r.headers.get("cache-control") == "no-store"
    r = client.post("/api/nguoi-dung", headers=QT, json={"vai": "user"})
    assert r.status_code == 200 and "mat_khau" in r.json() and r.headers.get("cache-control") == "no-store"
    r = client.post(f"/api/nguoi-dung/{r.json()['nguoi_dung']['id']}/dat-lai-mat-khau", headers=QT)
    assert r.status_code == 200 and "mat_khau_moi" in r.json() and r.headers.get("cache-control") == "no-store"


def test_danh_sach_khoa_neu_pham_vi_hieu_luc(moi_truong):
    """Khoá «*» của người dùng trần [chat] liệt kê `pham_vi` gốc VÀ `pham_vi_hieu_luc` = [chat]; chủ tắt → []."""
    client, _ = moi_truong
    _tao("admin", "admin-a")
    u = _tao("user", "bob-u", ["chat"])
    k, _ = khoa_khach.tao_khoa(nhan="cua-bob", pham_vi=["*"], chu_id=u["id"])
    k2, _ = khoa_khach.tao_khoa(nhan="khong-chu", pham_vi=["anh"])
    theo_id = {x["id"]: x for x in client.get("/api/khoa-khach", headers=QT).json()["khoa"]}
    assert theo_id[k["id"]]["pham_vi"] == ["*"] and theo_id[k["id"]]["pham_vi_hieu_luc"] == ["chat"]
    assert theo_id[k2["id"]]["pham_vi_hieu_luc"] == ["anh"]
    _cookie(client, u["id"])
    ds = client.get("/api/toi/khoa").json()["khoa"]
    assert [x["pham_vi_hieu_luc"] for x in ds] == [["chat"]]
    client.cookies.clear()
    nguoi_dung.sua_nguoi_dung(u["id"], bat=False)
    theo_id = {x["id"]: x for x in client.get("/api/khoa-khach", headers=QT).json()["khoa"]}
    assert theo_id[k["id"]]["pham_vi_hieu_luc"] == []
