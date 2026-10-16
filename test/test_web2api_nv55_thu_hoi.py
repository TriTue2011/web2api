"""Test trước (NV55) cho «thu hồi một nút»: module, khoá khách, phiên quản trị.

Bối cảnh: NV48 mục 4 bước 5. Khi phát hiện module có vấn đề hoặc khoá/phiên bị lộ,
quản trị phải thu hồi ngay bằng MỘT thao tác:
1. Module bị vô hiệu: gỡ khỏi nạp, không chạy tiếp, không tự cài lại khi đồng bộ git.
2. Khoá khách bị thu hồi: bị từ chối ngay ở yêu cầu kế tiếp (không chờ hết cache).
3. Đổi mật khẩu / hạ vai / tắt tài khoản quản trị: mọi phiên web của tài khoản đó
   mất hiệu lực ở yêu cầu kế tiếp.
4. Thu hồi ghi nhật ký kênh quản trị (người thực hiện + đối tượng).
5. Thu hồi là thao tác một bước, nhưng vẫn cần quyền quản trị.

Quy ước: hành vi CHƯA có trên main → xfail(strict=True); hành vi ĐÃ đúng → test xanh.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from gia import ghi_thu_muc, tep_echo
from app import khoa_khach as khoa_khach_mod
from app import nguoi_dung, nhat_ky as nhat_ky_mod
from app.main import app
from app.nguon_git import NguonGit
from app.so_module import SoModule

KHOA_QT = "sk-test-quan-tri-000"
MK_ADMIN = "MatKhauAdminTot123456"
MK_USER = "MatKhauUserTot123456"
MK_USER_MOI = "MatKhauUserMoi123456"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "100")
    monkeypatch.setenv("WEB2API_RL_KHOA_GIAY", "60")
    monkeypatch.setenv("WEB2API_DANG_NHAP_SAI_TOI_DA", "20")
    monkeypatch.setenv("WEB2API_DANG_NHAP_KHOA_PHUT", "15")
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        client.app.state.nguon_git = NguonGit(so)
        yield client, data_dir


def _qt():
    return {"Authorization": f"Bearer {KHOA_QT}"}


def _tao_nguoi_dung(ten, mat_khau, vai="user"):
    ban_ghi, _, _ = asyncio.run(
        nguoi_dung.tao_nguoi_dung(vai=vai, ten_dang_nhap=ten, mat_khau=mat_khau)
    )
    return ban_ghi


def _dang_nhap_web(client, ten, mat_khau):
    """Đăng nhập web, trả về (cookies, headers CSRF)."""
    r = client.post(
        "/api/dang-nhap", json={"ten_dang_nhap": ten, "mat_khau": mat_khau}
    )
    assert r.status_code == 200, f"đăng nhập web phải 200, nhận {r.status_code}: {r.text[:200]}"
    tok = r.cookies.get("w2a_phien")
    csrf = r.json()["csrf"]
    assert tok and csrf
    return {"w2a_phien": tok}, {"X-CSRF-Token": csrf}


def _cai_module_echo(client, data_dir, id_="echo"):
    """Thả module echo giả vào kho dữ liệu, nạp và cài cấu hình rỗng."""
    ghi_thu_muc(data_dir / "modules", {f"{id_}/{k}": v for k, v in tep_echo(id_=id_).items()})
    r = client.post("/api/modules/nap-lai", headers=_qt())
    assert r.status_code == 200, f"nap-lai phải 200, nhận {r.status_code}: {r.text[:200]}"
    r = client.post(f"/api/modules/{id_}/cai", json={"cau_hinh": {}}, headers=_qt())
    assert r.status_code == 200, f"cài module phải 200, nhận {r.status_code}: {r.text[:200]}"
    r = client.get(f"/api/modules/{id_}/suc-khoe", headers=_qt())
    assert r.status_code == 200, "module phải đang phục vụ sau khi cài"


def _muc_quan_tri(client, tu_khoa):
    r = client.get("/api/nhat-ky", params={"kenh": "quan_tri", "q": tu_khoa}, headers=_qt())
    assert r.status_code == 200
    return r.json()


# ─────────────────────────────────────────────────────────────────────────────
# 1. THU HỒI MODULE
# ─────────────────────────────────────────────────────────────────────────────


def test_go_module_mot_buoc_ngung_phuc_vu_ngay(moi_truong):
    """DELETE /api/modules/{id}: một thao tác duy nhất → module dừng phục vụ ngay,
    thư mục mã biến mất khỏi khám phá, nạp lại cũng không thấy."""
    client, data_dir = moi_truong
    _cai_module_echo(client, data_dir)

    r = client.delete("/api/modules/echo", headers=_qt())
    assert r.status_code == 200, f"gỡ module phải 200, nhận {r.status_code}: {r.text[:200]}"

    # Không còn phục vụ: lỗi rõ ràng, không treo, không 500
    r = client.get("/api/modules/echo/suc-khoe", headers=_qt())
    assert r.status_code == 404, f"module đã gỡ phải 404, nhận {r.status_code}"

    # Thư mục mã đã đổi tên/xoá: quét lại không thấy module quay về
    assert not (data_dir / "modules" / "echo").exists(), "thư mục module phải biến mất khỏi khám phá"
    r = client.post("/api/modules/nap-lai", headers=_qt())
    assert r.status_code == 200
    r = client.get("/api/modules/echo/suc-khoe", headers=_qt())
    assert r.status_code == 404, "nạp lại xong module đã gỡ vẫn phải 404"


def test_go_module_da_go_chan_tu_cai_lai_ban_cu(moi_truong):
    """da_go() nhớ sha của bản vừa gỡ: vòng tự cài gặp đúng sha đó phải bỏ qua,
    sha mới vẫn được cài (không chặn cứng id)."""
    client, _ = moi_truong
    ng = client.app.state.nguon_git
    ng.trang_thai = {"modules": [{"id": "echo", "tree_sha": "sha-ban-xau-abc"}]}

    ng.da_go("echo")

    da_go = (ng.trang_thai.get("da_go") or {}).get("echo")
    assert da_go == "sha-ban-xau-abc", "phải nhớ sha bản vừa gỡ"
    # Cùng điều kiện vòng tự cài dùng để bỏ qua (nguon_git.py::_tu_dong):
    assert (ng.trang_thai.get("da_go") or {}).get("echo") == "sha-ban-xau-abc"
    # Bản mới (sha khác) không bị chặn:
    assert (ng.trang_thai.get("da_go") or {}).get("echo") != "sha-ban-moi"


def test_go_module_ghi_nhat_ky_quan_tri(moi_truong):
    """Thu hồi module phải để lại dấu vết ở kênh quản trị (đối tượng bị gỡ)."""
    client, data_dir = moi_truong
    _cai_module_echo(client, data_dir)

    r = client.delete("/api/modules/echo", headers=_qt())
    assert r.status_code == 200

    muc = _muc_quan_tri(client, "echo")
    assert any("echo" in str(m.get("tom_tat", "")) for m in muc), (
        "kênh quản trị phải có dòng ghi việc gỡ module «echo»"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. THU HỒI KHOÁ KHÁCH
# ─────────────────────────────────────────────────────────────────────────────


def _tao_khoa_khach(client, nhan="khoa-nv55"):
    r = client.post("/api/khoa-khach", json={"nhan": nhan}, headers=_qt())
    assert r.status_code == 200, f"tạo khoá khách phải 200: {r.text[:200]}"
    khoa = r.json()["khoa"]
    return khoa["id"], khoa["khoa"]


def test_thu_hoi_khoa_khach_tuc_thi_mot_buoc(moi_truong):
    """DELETE /api/khoa-khach/{id}: một thao tác → yêu cầu kế tiếp dùng khoá đó
    bị 401 ngay, không chờ hết cache."""
    client, _ = moi_truong
    id_khoa, khoa_tho = _tao_khoa_khach(client)

    r = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_tho}"})
    assert r.status_code == 200, "khoá mới tạo phải dùng được"

    r = client.delete(f"/api/khoa-khach/{id_khoa}", headers=_qt())
    assert r.status_code == 200, "thu hồi khoá là thao tác một bước, phải 200 ngay"

    r = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_tho}"})
    assert r.status_code == 401, f"khoá đã thu hồi phải 401 ngay, nhận {r.status_code}"


def test_tat_khoa_khach_bat_false_tuc_thi(moi_truong):
    """PATCH bat=false cũng là thu hồi tức thì: khoá bị tắt phải 401 ở yêu cầu kế tiếp."""
    client, _ = moi_truong
    id_khoa, khoa_tho = _tao_khoa_khach(client)

    r = client.patch(f"/api/khoa-khach/{id_khoa}", json={"bat": False}, headers=_qt())
    assert r.status_code == 200

    r = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_tho}"})
    assert r.status_code == 401, f"khoá đã tắt phải 401 ngay, nhận {r.status_code}"


def test_thu_hoi_khoa_khach_ghi_nhat_ky_quan_tri(moi_truong):
    """Thu hồi khoá khách ghi kênh quản trị, nêu rõ đối tượng (id khoá)."""
    client, _ = moi_truong
    id_khoa, _ = _tao_khoa_khach(client)

    r = client.delete(f"/api/khoa-khach/{id_khoa}", headers=_qt())
    assert r.status_code == 200

    muc = _muc_quan_tri(client, id_khoa)
    assert any(
        "thu hồi" in str(m.get("tom_tat", "")).lower() and id_khoa in str(m.get("them", {}))
        for m in muc
    ), "kênh quản trị phải ghi việc thu hồi khoá khách kèm id khoá"


def test_thu_hoi_khoa_khach_ghi_nguoi_thuc_hien(moi_truong):
    """Dòng nhật ký thu hồi phải ghi người thực hiện (phân biệt quản trị thật
    với kẻ chiếm phiên — xem NV46)."""
    client, _ = moi_truong
    id_khoa, _ = _tao_khoa_khach(client)
    client.delete(f"/api/khoa-khach/{id_khoa}", headers=_qt())

    muc = _muc_quan_tri(client, id_khoa)
    assert any(
        "nguoi_thuc_hien" in m.get("them", {}) or "nguoi_thuc_hien" in str(m.get("tom_tat", ""))
        for m in muc
    ), "dòng thu hồi phải nêu người thực hiện"


def test_thu_hoi_khoa_khach_id_la_khong_lo_thong_tin(moi_truong):
    """Thu hồi id không tồn tại: không 500, không lộ khoá nào còn hiệu lực."""
    client, _ = moi_truong
    r = client.delete("/api/khoa-khach/kk_khong_ton_tai_000", headers=_qt())
    assert r.status_code == 200
    assert r.json()["da_xoa"] is False


def test_thu_hoi_khoa_khach_bang_khoa_khach_403(moi_truong):
    """Khoá khách không được thu hồi khoá khác (đường quản trị)."""
    client, _ = moi_truong
    id_khoa, khoa_tho = _tao_khoa_khach(client)
    r = client.delete(
        f"/api/khoa-khach/{id_khoa}",
        headers={"Authorization": f"Bearer {khoa_tho}"},
    )
    assert r.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# 3. THU HỒI PHIÊN WEB
# ─────────────────────────────────────────────────────────────────────────────


def test_doi_mat_khau_chinh_minh_thu_hoi_phien_khac(moi_truong):
    """Đổi mật khẩu chính mình: các phiên khác mất hiệu lực ngay, phiên hiện tại giữ."""
    client, _ = moi_truong
    _tao_nguoi_dung("nv55u1", MK_USER)
    ck1, cs1 = _dang_nhap_web(client, "nv55u1", MK_USER)
    ck2, cs2 = _dang_nhap_web(client, "nv55u1", MK_USER)

    r = client.post(
        "/api/toi/mat-khau",
        json={"mat_khau_cu": MK_USER, "mat_khau_moi": MK_USER_MOI},
        cookies=ck1,
        headers=cs1,
    )
    assert r.status_code == 200, f"đổi mật khẩu phải 200: {r.text[:200]}"

    r = client.get("/api/toi", cookies=ck2)
    assert r.status_code == 401, f"phiên khác phải 401 sau đổi mật khẩu, nhận {r.status_code}"
    r = client.get("/api/toi", cookies=ck1)
    assert r.status_code == 200, "phiên đang đổi mật khẩu được giữ lại"


def test_dat_lai_mat_khau_thu_hoi_tat_ca_phien(moi_truong):
    """Quản trị đặt lại mật khẩu: mọi phiên web của tài khoản đó mất hiệu lực."""
    client, _ = moi_truong
    nd = _tao_nguoi_dung("nv55u2", MK_USER)
    ck1, _ = _dang_nhap_web(client, "nv55u2", MK_USER)
    ck2, _ = _dang_nhap_web(client, "nv55u2", MK_USER)

    r = client.post(f"/api/nguoi-dung/{nd['id']}/dat-lai-mat-khau", headers=_qt())
    assert r.status_code == 200, f"đặt lại mật khẩu phải 200: {r.text[:200]}"

    for i, ck in enumerate((ck1, ck2), 1):
        r = client.get("/api/toi", cookies=ck)
        assert r.status_code == 401, f"phiên {i} phải 401 sau đặt lại mật khẩu, nhận {r.status_code}"


def test_tat_tai_khoan_thu_hoi_phien(moi_truong):
    """Tắt tài khoản (bat=false): phiên web đang mở mất hiệu lực ở yêu cầu kế tiếp."""
    client, _ = moi_truong
    nd = _tao_nguoi_dung("nv55u3", MK_USER)
    ck, _ = _dang_nhap_web(client, "nv55u3", MK_USER)

    r = client.patch(f"/api/nguoi-dung/{nd['id']}", json={"bat": False}, headers=_qt())
    assert r.status_code == 200

    r = client.get("/api/toi", cookies=ck)
    assert r.status_code == 401, f"phiên của tài khoản đã tắt phải 401, nhận {r.status_code}"


def test_ha_vai_admin_thu_hoi_phien(moi_truong):
    """Hạ vai admin→user (tài khoản vẫn bật): mọi phiên web của tài khoản đó phải
    mất hiệu lực ở yêu cầu kế tiếp — kẻ chiếm phiên không được dùng tiếp."""
    client, _ = moi_truong
    _tao_nguoi_dung("nv55adm_goc", MK_ADMIN, vai="admin")
    nd = _tao_nguoi_dung("nv55adm", MK_ADMIN, vai="admin")
    ck1, _ = _dang_nhap_web(client, "nv55adm", MK_ADMIN)
    ck2, _ = _dang_nhap_web(client, "nv55adm", MK_ADMIN)

    r = client.patch(f"/api/nguoi-dung/{nd['id']}", json={"vai": "user"}, headers=_qt())
    assert r.status_code == 200, f"hạ vai phải 200: {r.text[:200]}"

    for i, ck in enumerate((ck1, ck2), 1):
        r = client.get("/api/toi", cookies=ck)
        assert r.status_code == 401, f"phiên {i} phải 401 sau hạ vai, nhận {r.status_code}"


def test_xoa_tai_khoan_thu_hoi_ca_khoa_khach_va_phien(moi_truong):
    """Xoá tài khoản: khoá khách của tài khoản đó và mọi phiên web đều mất hiệu lực."""
    client, _ = moi_truong
    nd = _tao_nguoi_dung("nv55u4", MK_USER)
    ck, _ = _dang_nhap_web(client, "nv55u4", MK_USER)
    ban_ghi, khoa_tho = khoa_khach_mod.tao_khoa(nhan="khoa-cua-u4", chu_id=nd["id"])
    id_khoa = ban_ghi["id"]

    r = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_tho}"})
    assert r.status_code == 200

    r = client.delete(f"/api/nguoi-dung/{nd['id']}", headers=_qt())
    assert r.status_code == 200, f"xoá tài khoản phải 200: {r.text[:200]}"

    r = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_tho}"})
    assert r.status_code == 401, f"khoá của tài khoản đã xoá phải 401, nhận {r.status_code}"
    r = client.get("/api/toi", cookies=ck)
    assert r.status_code == 401, f"phiên của tài khoản đã xoá phải 401, nhận {r.status_code}"
    assert id_khoa  # giữ biến dùng trong assert trên, tránh cảnh báo


# ─────────────────────────────────────────────────────────────────────────────
# LUẬT BẮT BUỘC
# ─────────────────────────────────────────────────────────────────────────────


def test_thu_hoi_khong_khoa_401(moi_truong):
    """Không khoá → 401 trên mọi đường thu hồi."""
    client, _ = moi_truong
    assert client.delete("/api/modules/echo").status_code == 401
    assert client.delete("/api/khoa-khach/kk_x").status_code == 401
    assert client.post("/api/nguoi-dung/x/dat-lai-mat-khau").status_code == 401


def test_thu_hoi_vai_user_403(moi_truong):
    """Tài khoản vai user (kể cả mới tạo) không có quyền thu hồi."""
    client, _ = moi_truong
    _tao_nguoi_dung("nv55u5", MK_USER)
    ck, cs = _dang_nhap_web(client, "nv55u5", MK_USER)

    r = client.delete("/api/modules/echo", cookies=ck, headers=cs)
    assert r.status_code == 403, f"user gỡ module phải 403, nhận {r.status_code}"
    r = client.delete("/api/khoa-khach/kk_x", cookies=ck, headers=cs)
    assert r.status_code == 403, f"user thu hồi khoá phải 403, nhận {r.status_code}"
    r = client.post("/api/nguoi-dung/x/dat-lai-mat-khau", cookies=ck, headers=cs)
    assert r.status_code == 403, f"user đặt lại mật khẩu người khác phải 403, nhận {r.status_code}"


def test_go_module_khong_ton_tai_404(moi_truong):
    """Gỡ module không có → 404, không 500."""
    client, _ = moi_truong
    r = client.delete("/api/modules/khong-co-module-nay", headers=_qt())
    assert r.status_code == 404, f"module không có phải 404, nhận {r.status_code}"


def test_thu_hoi_khong_lo_khoa_trong_nhat_ky(moi_truong):
    """Nhật ký thu hồi không chứa khoá thô (luật cứng CLAUDE.md)."""
    client, _ = moi_truong
    id_khoa, khoa_tho = _tao_khoa_khach(client)
    client.delete(f"/api/khoa-khach/{id_khoa}", headers=_qt())

    muc = _muc_quan_tri(client, id_khoa)
    assert muc, "phải có dòng nhật ký thu hồi"
    for m in muc:
        dong = str(m.get("tom_tat", "")) + str(m.get("them", ""))
        assert khoa_tho not in dong, "khoá thô không được vào nhật ký"
