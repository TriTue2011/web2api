"""Test trước (đỏ) cho NV57: cảnh báo đăng nhập bất thường + băng rôn cổng IP quản trị trống.

HỢP ĐỒNG ĐỀ XUẤT (chưa có trên main — các test đỏ đánh dấu xfail(strict)):

A. Cảnh báo đăng nhập bất thường (mục 3(c) báo cáo NV46 v2):
   Khi đăng nhập web thành công, lõi ghi dấu thiết bị = băm(IP hiệu lực + "|" + User-Agent)
   cho từng tài khoản (bộ nhớ có trần, không lưu nguyên IP/UA thô quá dài).
   - Lần đầu thấy dấu thiết bị → nhat_ky.ghi("quan_tri", "canh_bao",
     "Đăng nhập từ thiết bị/IP chưa từng thấy: «<tên>»", ...) — KHÔNG ghi mật khẩu/khoá/vé.
   - Dấu thiết bị đã biết → không cảnh báo (tránh báo động giả).
   - Đăng nhập thành công sau N lần thất bại → cảnh báo nêu rõ "sau N lần đăng nhập thất bại".
   - Cảnh báo chỉ đọc được qua /api/nhat-ky (quyền quản trị) nên không lộ cho kẻ tấn công;
     phản hồi 401 khi sai thông tin vẫn đồng nhất, không lộ tài khoản có tồn tại.

B. Băng rôn cổng IP quản trị trống (mục 3(f) báo cáo NV46 v2):
   Route đề xuất (chưa có): GET /api/bao-mat/trang-thai, chỉ quản trị (không khoá → 401,
   khoá khách/vai user → 403), trả:
     {"cong_ip_quan_tri_trong": true|false, "bang_ron": str|None}
   - WEB2API_QT_IP_CHO_PHEP trống → trong=true, bang_ron là chuỗi TĨNH do server định
     (không nội suy nội dung từ ngoài → không chèn được mã).
   - Đã cấu hình → trong=false, bang_ron=null.
   Trang He-thong-Bao-mat.dc.html đọc route này và hiện băng rôn khi trong=true.

Đo trên main 45956b4 (chạy thật):
- Đăng nhập sai → đã có nhat_ky "quan_tri"/"canh_bao" (xanh, giữ làm phòng thủ).
- Đăng nhập thành công từ thiết bị/IP mới → chỉ có "thong_tin", KHÔNG có cảnh báo (đỏ).
- Không có route /api/bao-mat/trang-thai (404) → mọi test băng rôn đỏ.
- /api/nhat-ky: không xác thực → 401; khoá khách/user → 403 (xanh).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import gioi_han, ip_cho_phep, khoa_khach, nguoi_dung, nhat_ky, novnc, phien_web
from app.main import app
from app.so_module import SoModule

# Chuỗi khoá/mật khẩu giả, đã có trong allowlist của .gitleaks.toml (mẫu các test cũ).
KHOA_QT = "<redacted>"
MK_CHUAN = "MkGiaNv57012345"
MK_SAI = "SaiHoanToanNv57999"

IP_MOI = "198.51.100.10"   # TEST-NET-2, địa chỉ giả trong test
IP_CU = "198.51.100.11"
UA_MOI = "TrinhDuyetMoi-Nv57/1.0"
UA_CU = "TrinhDuyetCu-Nv57/1.0"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "30")
    monkeypatch.setenv("WEB2API_RL_CUA_SO_GIAY", "60")
    monkeypatch.setenv("WEB2API_RL_KHOA_GIAY", "60")
    monkeypatch.setenv("WEB2API_DANG_NHAP_SAI_TOI_DA", "5")
    monkeypatch.setenv("WEB2API_DANG_NHAP_TOAN_CUC_SAI_TOI_DA", "50")
    monkeypatch.setenv("WEB2API_DANG_NHAP_KHOA_PHUT", "15")
    monkeypatch.setenv("WEB2API_TRUST_PROXY", "testclient,127.0.0.1,::1,203.0.113.0/24")
    monkeypatch.delenv("WEB2API_QT_IP_CHO_PHEP", raising=False)

    nguoi_dung._THEO_DOI_SAI.clear()
    novnc._VE.clear()
    khoa_khach.lam_moi_cache()
    gioi_han.khoi_dong()

    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin", mat_khau=MK_CHUAN))
    with TestClient(app) as client:
        client._transport.client = ("127.0.0.1", 1234)
        client.app.state.so = SoModule()
        client.cookies.clear()
        yield client, data_dir


def _dang_nhap(client, ten="admin", mat_khau=MK_CHUAN, ip=IP_MOI, ua=UA_MOI):
    return client.post(
        "/api/dang-nhap",
        json={"ten_dang_nhap": ten, "mat_khau": mat_khau},
        headers={"x-forwarded-for": ip, "user-agent": ua},
    )


def _so_canh_bao() -> int:
    return len(nhat_ky.doc(kenh="quan_tri", muc="canh_bao", gioi_han=500))


# ─────────────────────────────────────────────────────────────────────────────
# A. Cảnh báo đăng nhập bất thường
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="NV57 01a: chưa có cảnh báo khi đăng nhập từ thiết bị/IP mới")
def test_01a_dang_nhap_tu_thiet_bi_moi_ghi_canh_bao(moi_truong):
    """Đăng nhập thành công từ (IP, User-Agent) chưa từng thấy → có bản ghi
    quan_tri/canh_bao về thiết bị mới (hợp đồng đề xuất ở đầu tệp)."""
    client, _ = moi_truong
    truoc = _so_canh_bao()
    r = _dang_nhap(client)
    assert r.status_code == 200
    sau = _so_canh_bao()
    assert sau > truoc, "đăng nhập từ thiết bị mới phải ghi cảnh báo kênh quan_tri"
    moi = nhat_ky.doc(kenh="quan_tri", muc="canh_bao", gioi_han=50)
    ban_ghi = moi[0]
    assert "admin" in ban_ghi.get("tom_tat", "")
    assert MK_CHUAN not in str(ban_ghi), "cảnh báo không được chứa mật khẩu"


def test_01b_dang_nhap_lai_thiet_bi_da_biet_khong_canh_bao(moi_truong):
    """Đăng nhập lại từ cùng (IP, User-Agent) → không thêm cảnh báo (tránh báo động giả)."""
    client, _ = moi_truong
    assert _dang_nhap(client).status_code == 200
    giua = _so_canh_bao()
    assert _dang_nhap(client).status_code == 200
    assert _so_canh_bao() == giua


@pytest.mark.xfail(strict=True, reason="NV57 01c: chưa có cảnh báo khi đăng nhập đúng sau nhiều lần sai")
def test_01c_dang_nhap_dung_sau_nhieu_lan_sai_ghi_canh_bao(moi_truong):
    """3 lần sai rồi đăng nhập đúng → bản ghi cảnh báo cho lần đăng nhập thành công
    nêu rõ có lần thất bại trước đó (hợp đồng đề xuất ở đầu tệp)."""
    client, _ = moi_truong
    for _ in range(3):
        assert _dang_nhap(client, mat_khau=MK_SAI).status_code == 401
    truoc = _so_canh_bao()
    r = _dang_nhap(client)
    assert r.status_code == 200
    assert _so_canh_bao() > truoc, "đăng nhập đúng sau nhiều lần sai phải ghi cảnh báo"
    ban_ghi = nhat_ky.doc(kenh="quan_tri", muc="canh_bao", gioi_han=50)[0]
    assert "thất bại" in ban_ghi.get("tom_tat", ""), "cảnh báo phải nêu số lần thất bại trước đó"
    assert MK_CHUAN not in str(ban_ghi) and MK_SAI not in str(ban_ghi)


def test_01d_dang_nhap_sai_ghi_canh_bao(moi_truong):
    """Đăng nhập sai mật khẩu → đã có cảnh báo kênh quan_tri (hành vi hiện có, giữ xanh)."""
    client, _ = moi_truong
    truoc = _so_canh_bao()
    assert _dang_nhap(client, mat_khau=MK_SAI).status_code == 401
    assert _so_canh_bao() > truoc


def test_01e_canh_bao_va_phan_hoi_khong_chua_bi_mat(moi_truong):
    """Nội dung cảnh báo và phản hồi 401 không chứa mật khẩu đã gửi."""
    client, _ = moi_truong
    r = _dang_nhap(client, mat_khau=MK_SAI)
    assert r.status_code == 401
    assert MK_SAI not in r.text
    for rec in nhat_ky.doc(kenh="quan_tri", muc="canh_bao", gioi_han=50):
        assert MK_SAI not in str(rec), f"bản ghi nhật ký lộ mật khẩu: {rec.get('tom_tat')}"


def test_01f_401_dong_nhat_khong_lo_tai_khoan_co_ton_tai(moi_truong):
    """Tên không tồn tại vs sai mật khẩu → cùng 401 và cùng thông báo (không lộ qua phản hồi)."""
    client, _ = moi_truong
    r_khong = _dang_nhap(client, ten="khong_ton_tai_nv57", mat_khau=MK_SAI)
    r_sai = _dang_nhap(client, ten="admin", mat_khau=MK_SAI)
    assert r_khong.status_code == r_sai.status_code == 401
    assert r_khong.json() == r_sai.json()


def test_01g_khong_doc_duoc_nhat_ky_khi_chua_xac_thuc(moi_truong):
    """Kẻ tấn công chưa xác thực không đọc được kênh cảnh báo → 401."""
    client, _ = moi_truong
    client.cookies.clear()
    r = client.get("/api/nhat-ky")
    assert r.status_code == 401


def test_01h_khoa_khach_va_user_khong_doc_duoc_nhat_ky(moi_truong):
    """Khoá khách hoặc phiên user gọi /api/nhat-ky → 403."""
    client, _ = moi_truong
    _, khoa_tho = khoa_khach.tao_khoa("Khach nv57", ["chat"])
    client.cookies.clear()
    r = client.get("/api/nhat-ky", headers={"authorization": f"Bearer {khoa_tho}"})
    assert r.status_code == 403
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user1", mat_khau=MK_CHUAN))
    assert _dang_nhap(client, ten="user1", ip=IP_CU, ua=UA_CU).status_code == 200
    r2 = client.get("/api/nhat-ky")
    assert r2.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# B. Băng rôn cổng IP quản trị trống
# ─────────────────────────────────────────────────────────────────────────────
# Hợp đồng đề xuất: GET /api/bao-mat/trang-thai (chỉ quản trị)
#   → {"cong_ip_quan_tri_trong": bool, "bang_ron": str | None}

def _trang_thai_bao_mat(client, headers=None):
    return client.get("/api/bao-mat/trang-thai", headers=headers or {})


def _tieu_de_qt(client):
    client.cookies.clear()
    return {"authorization": f"Bearer {KHOA_QT}"}


@pytest.mark.xfail(strict=True, reason="NV57 02a: chưa có route /api/bao-mat/trang-thai")
def test_02a_cong_ip_trong_bao_trong(moi_truong, monkeypatch):
    """WEB2API_QT_IP_CHO_PHEP trống → trang-thai báo cong_ip_quan_tri_trong=true + băng rôn."""
    client, _ = moi_truong
    monkeypatch.delenv("WEB2API_QT_IP_CHO_PHEP", raising=False)
    r = _trang_thai_bao_mat(client, _tieu_de_qt(client))
    assert r.status_code == 200
    du_lieu = r.json()
    assert du_lieu.get("cong_ip_quan_tri_trong") is True
    assert isinstance(du_lieu.get("bang_ron"), str) and du_lieu["bang_ron"]


@pytest.mark.xfail(strict=True, reason="NV57 02b: chưa có route /api/bao-mat/trang-thai")
def test_02b_cong_ip_da_cau_hinh_bao_khong_trong(moi_truong, monkeypatch):
    """WEB2API_QT_IP_CHO_PHEP đã đặt → trong=false, không có băng rôn."""
    client, _ = moi_truong
    monkeypatch.setenv("WEB2API_QT_IP_CHO_PHEP", "203.0.113.0/24")
    r = _trang_thai_bao_mat(client, _tieu_de_qt(client))
    assert r.status_code == 200
    du_lieu = r.json()
    assert du_lieu.get("cong_ip_quan_tri_trong") is False
    assert du_lieu.get("bang_ron") is None


@pytest.mark.xfail(strict=True, reason="NV57 02c: chưa có route /api/bao-mat/trang-thai")
def test_02c_bang_ron_khong_chen_noi_dung_ngoai(moi_truong, monkeypatch):
    """Băng rôn là chuỗi tĩnh do server định — không phản chiếu nội dung từ ngoài."""
    client, _ = moi_truong
    monkeypatch.delenv("WEB2API_QT_IP_CHO_PHEP", raising=False)
    monkeypatch.setenv("WEB2API_CANARY_NV57", "<script>alert('nv57')</script>")
    r = _trang_thai_bao_mat(client, _tieu_de_qt(client))
    assert r.status_code == 200
    assert "<script>alert('nv57')</script>" not in r.text


@pytest.mark.xfail(strict=True, reason="NV57 02d: chưa có route /api/bao-mat/trang-thai")
def test_02d_duong_bao_mat_doi_xac_thuc(moi_truong):
    """Không khoá gọi /api/bao-mat/trang-thai → 401."""
    client, _ = moi_truong
    client.cookies.clear()
    r = _trang_thai_bao_mat(client)
    assert r.status_code == 401


@pytest.mark.xfail(strict=True, reason="NV57 02e: chưa có route /api/bao-mat/trang-thai")
def test_02e_duong_bao_mat_chan_khach_va_user(moi_truong):
    """Khoá khách hoặc phiên user gọi /api/bao-mat/trang-thai → 403."""
    client, _ = moi_truong
    _, khoa_tho = khoa_khach.tao_khoa("Khach nv57", ["chat"])
    client.cookies.clear()
    r = _trang_thai_bao_mat(client, {"authorization": f"Bearer {khoa_tho}"})
    assert r.status_code == 403
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user2", mat_khau=MK_CHUAN))
    assert _dang_nhap(client, ten="user2", ip=IP_CU, ua=UA_CU).status_code == 200
    r2 = _trang_thai_bao_mat(client)
    assert r2.status_code == 403


def test_02f_doc_danh_sach_trong_tat_cong(moi_truong, monkeypatch):
    """WEB2API_QT_IP_CHO_PHEP trống → _doc_danh_sach() trả None (cổng tắt, hành vi hiện có)."""
    monkeypatch.delenv("WEB2API_QT_IP_CHO_PHEP", raising=False)
    assert ip_cho_phep._doc_danh_sach() is None


def test_02g_doc_danh_sach_co_gia_tri(moi_truong, monkeypatch):
    """WEB2API_QT_IP_CHO_PHEP có CIDR hợp lệ → _doc_danh_sach() trả danh sách mạng."""
    monkeypatch.setenv("WEB2API_QT_IP_CHO_PHEP", "203.0.113.0/24")
    mangs = ip_cho_phep._doc_danh_sach()
    assert isinstance(mangs, list) and len(mangs) == 1
