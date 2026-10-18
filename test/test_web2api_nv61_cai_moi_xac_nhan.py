"""NV61 — test trước (đỏ) cho: cài module MỚI từ git cũng phải có xác nhận của quản trị.

Bối cảnh: cổng duyệt quyền NV7 chỉ áp cho CẬP NHẬT (SoModule.cai_thu_muc: «cài mới
không chặn», app/so_module.py). Cài lần đầu từ git là CHẠY MÃ NGAY sau cú bấm «Cài»,
nên cú bấm là quyết định tin cậy duy nhất — kẻ có khoá quản trị (hoặc chiếm phiên
admin) bấm một cái là mã lạ chạy trong tiến trình lõi.

HỢP ĐỒNG ĐỀ XUẤT (test viết theo; người viết mã có thể đổi tên trường nhưng phải giữ
ngữ nghĩa, và cập nhật test):
- Lượt 1: POST /api/nguon-git/cai/{id} (module CHƯA cài, không kèm xác nhận) →
  409 {"ok": false, "ma": "can_xac_nhan_cai_moi", "id", "repo", "sha_commit",
       "phien_ban", "quyen": [danh sách quyền module xin từ manifest],
       "ma_xac_nhan": "<mã dùng một lần>"}.
  KHÔNG cài, KHÔNG chạy mã ở lượt 1. Mã gắn với (id, sha_commit), hết hạn ngắn,
  chỉ lưu dạng băm ở server.
- Lượt 2: POST cùng đường với thân {"xac_nhan": true, "ma_xac_nhan": "<mã>"} →
  mã đúng → cài (200). Mã sai / đã dùng / hết hạn / của sha_commit khác → 409.
  Thân có "xac_nhan": true mà không có mã → 409.
- Ngoại lệ có chủ đích: module đi kèm ảnh (nguon == "anh") không qua bước này.
- Tự cài (tu_dong) vẫn KHÔNG cài module mới (mặc định tắt) — giữ hành vi hiện có.

Trên main hiện tại: lượt 1 cài ngay → 200 (ĐỎ). Các test dưới đánh dấu
xfail(strict=True) cho hành vi chưa có; test xanh giữ hành vi đã đúng.

NV75 — tối ưu tốc độ (hợp đồng hành vi GIỮ NGUYÊN, chỉ đổi cách dựng test):
- Bản đầu chờ vòng nền thăm dò qua poll /api/modules tới 15s/test (tổng ~2,5 phút).
  Nay gọi thẳng một lượt đồng bộ `NguonGit.mot_lan()` trong loop của app qua
  `TestClient.portal` — xác định, không chờ, không sleep.
- Manifest echo dùng `yeu_cau_goi` dạng tên gói Python (nền mới kiểm chặt hơn:
  dict như bản đầu → `loi_manifest` → không bao giờ vào danh sách cài được).
  `mang_ra` giữ nguyên để `quyen` ở lượt 1 (khi có) vẫn non-empty.
"""

import pytest
from fastapi.testclient import TestClient

from app import so_module
from app.main import app
from gia import GitHubGia, MayChuThat, tep_echo, tep_kho

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
KHOA_KHACH_GIA = "sk-test-nv61-khach-000"
REPO = "chu-may/c2a"
CAU_HINH = {"repo": REPO, "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 3600}


@pytest.fixture(scope="module")
def _gh():
    g = GitHubGia(REPO)
    with MayChuThat(g.app) as s:
        g.url = s.url
        yield g


@pytest.fixture
def gh(_gh, monkeypatch):
    for k in ("nhanh", "commits", "trees", "blobs", "blob_sai", "nhat_ky", "loi_commit"):
        getattr(_gh, k).clear()
    _gh.token = None
    _gh.han_muc = None
    _gh.cat_cay = False
    monkeypatch.setenv("WEB2API_GITHUB_API", _gh.url)
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    return _gh


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return tmp_path


def dong_bo_ngay(c):
    """Đồng bộ git MỘT lượt ngay, không chờ vòng nền.

    Dừng vòng nền của app trước (tránh đua hai lượt `mot_lan` cùng lúc), rồi gọi
    thẳng `mot_lan()` trong đúng event loop của app qua `TestClient.portal`.
    Xác định và tức thì (vài trăm ms) thay vì poll /api/modules tới 15s.
    """
    ng = app.state.nguon_git

    async def _():
        await ng.dung()  # dừng vòng nền; lifespan lúc đóng TestClient gọi lại cũng an toàn
        await ng.mot_lan()

    c.portal.call(_)
    assert ng.trang_thai.get("commit"), "đồng bộ một lượt phải thấy commit"


def khoi_tao_nguon(c, gh):
    gh.day(tep_kho({"echo": tep_echo("echo v1", "1.0.0",
                                     mang_ra=["api.gia-nv61.test"])}),
           thong_diep="thêm echo có quyền")
    assert c.put("/api/nguon-git", json=CAU_HINH, headers=QT).status_code == 200
    dong_bo_ngay(c)


# ───────────── XÁC NHẬN CÀI MỚI (NV61) ─────────────

def test_cai_moi_luot_1_phai_409_can_xac_nhan(gh, du_lieu):
    with TestClient(app) as c:
        khoi_tao_nguon(c, gh)
        r = c.post("/api/nguon-git/cai/echo", headers=QT)
        assert r.status_code == 409, r.text
        than = r.json()
        assert than.get("ma") == "can_xac_nhan_cai_moi"
        assert than.get("id") == "echo"
        assert than.get("repo") == REPO
        assert than.get("sha_commit")  # sha của commit đã thấy
        assert than.get("ma_xac_nhan")  # mã dùng một lần để lượt 2 gửi lại
        assert isinstance(than.get("quyen"), list) and than["quyen"]  # quyền module xin
        # KHÔNG cài, KHÔNG chạy mã ở lượt 1
        mods = c.get("/api/modules", headers=QT).json()["modules"]
        assert all(m["id"] != "echo" for m in mods)


def test_cai_moi_luot_2_ma_dung_thi_cai(gh, du_lieu):
    with TestClient(app) as c:
        khoi_tao_nguon(c, gh)
        r1 = c.post("/api/nguon-git/cai/echo", headers=QT)
        assert r1.status_code == 409 and r1.json().get("ma") == "can_xac_nhan_cai_moi"
        ma = r1.json()["ma_xac_nhan"]
        r2 = c.post("/api/nguon-git/cai/echo", headers=QT,
                    json={"xac_nhan": True, "ma_xac_nhan": ma})
        assert r2.status_code == 200, r2.text
        mods = c.get("/api/modules", headers=QT).json()["modules"]
        assert any(m["id"] == "echo" for m in mods)


def test_cai_moi_ma_sai_phai_409(gh, du_lieu):
    with TestClient(app) as c:
        khoi_tao_nguon(c, gh)
        r1 = c.post("/api/nguon-git/cai/echo", headers=QT)
        assert r1.status_code == 409 and r1.json().get("ma") == "can_xac_nhan_cai_moi"
        r2 = c.post("/api/nguon-git/cai/echo", headers=QT,
                    json={"xac_nhan": True, "ma_xac_nhan": "ma-sai-nv61-000"})
        assert r2.status_code == 409, r2.text
        mods = c.get("/api/modules", headers=QT).json()["modules"]
        assert all(m["id"] != "echo" for m in mods)


def test_cai_moi_ma_dung_roi_khong_dung_lai_duoc(gh, du_lieu):
    with TestClient(app) as c:
        khoi_tao_nguon(c, gh)
        r1 = c.post("/api/nguon-git/cai/echo", headers=QT)
        assert r1.status_code == 409 and r1.json().get("ma") == "can_xac_nhan_cai_moi"
        ma = r1.json()["ma_xac_nhan"]
        r2 = c.post("/api/nguon-git/cai/echo", headers=QT,
                    json={"xac_nhan": True, "ma_xac_nhan": ma})
        assert r2.status_code == 200, r2.text  # lượt 2 dùng mã → cài xong
        # gỡ để cài lại, rồi dùng LẠI mã cũ → phải 409 (mã dùng một lần)
        assert c.delete("/api/modules/echo", headers=QT).status_code in (200, 204)
        r3 = c.post("/api/nguon-git/cai/echo", headers=QT)
        assert r3.status_code == 409 and r3.json().get("ma") == "can_xac_nhan_cai_moi"
        r4 = c.post("/api/nguon-git/cai/echo", headers=QT,
                    json={"xac_nhan": True, "ma_xac_nhan": ma})
        assert r4.status_code == 409, r4.text


def test_cai_moi_ma_cua_sha_khac_phai_409(gh, du_lieu):
    with TestClient(app) as c:
        khoi_tao_nguon(c, gh)
        r1 = c.post("/api/nguon-git/cai/echo", headers=QT)
        assert r1.status_code == 409 and r1.json().get("ma") == "can_xac_nhan_cai_moi"
        ma_cu = r1.json()["ma_xac_nhan"]
        sha_cu = r1.json()["sha_commit"]
        # đẩy commit mới → sha đổi
        gh.day(tep_kho({"echo": tep_echo("echo v2", "1.0.1")}), thong_diep="bản mới")
        dong_bo_ngay(c)
        r2 = c.post("/api/nguon-git/cai/echo", headers=QT)
        assert r2.status_code == 409 and r2.json().get("ma") == "can_xac_nhan_cai_moi"
        assert r2.json()["sha_commit"] != sha_cu
        # dùng mã của sha cũ cho sha mới → 409
        r3 = c.post("/api/nguon-git/cai/echo", headers=QT,
                    json={"xac_nhan": True, "ma_xac_nhan": ma_cu})
        assert r3.status_code == 409, r3.text


def test_cai_moi_co_xac_nhan_ma_thieu_ma_phai_409(gh, du_lieu):
    with TestClient(app) as c:
        khoi_tao_nguon(c, gh)
        r = c.post("/api/nguon-git/cai/echo", headers=QT, json={"xac_nhan": True})
        assert r.status_code == 409, r.text
        mods = c.get("/api/modules", headers=QT).json()["modules"]
        assert all(m["id"] != "echo" for m in mods)


def test_ma_xac_nhan_khong_vao_log(gh, du_lieu, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        with TestClient(app) as c:
            khoi_tao_nguon(c, gh)
            r1 = c.post("/api/nguon-git/cai/echo", headers=QT)
            assert r1.status_code == 409 and r1.json().get("ma") == "can_xac_nhan_cai_moi"
            ma = r1.json()["ma_xac_nhan"]
    assert ma not in caplog.text


# ───────────── XANH: luật bắt buộc + ngoại lệ có chủ đích ─────────────

def test_khong_khoa_401(gh, du_lieu):
    with TestClient(app) as c:
        khoi_tao_nguon(c, gh)
        assert c.post("/api/nguon-git/cai/echo").status_code == 401


def test_khoa_khach_403(gh, du_lieu):
    with TestClient(app) as c:
        khoi_tao_nguon(c, gh)
        r = c.post("/api/nguon-git/cai/echo",
                   headers={"Authorization": f"Bearer {KHOA_KHACH_GIA}"})
        assert r.status_code in (401, 403)


def test_doi_id_khong_co_404_khong_lo(gh, du_lieu):
    with TestClient(app) as c:
        khoi_tao_nguon(c, gh)
        r = c.post("/api/nguon-git/cai/khong-co-dau-nv61", headers=QT)
        assert r.status_code == 404
        assert REPO not in r.text


def test_module_di_kem_anh_khong_qua_xac_nhan_cai_moi(gh, du_lieu):
    """Ngoại lệ có chủ đích: module nguon=anh không qua bước xác nhận cài mới —
    cài đè từ git vẫn bị 409 «đã đi kèm ảnh» như hiện tại."""
    from gia import ghi_thu_muc
    ghi_thu_muc(du_lieu / "anh-rong" / "mod-anh",
                tep_echo("anh v1", "1.0.0", id_="mod-anh", tien_to="mod-anh"))
    with TestClient(app) as c:
        gh.day(tep_kho({"mod-anh": tep_echo("anh v1", "1.0.0", id_="mod-anh",
                                            tien_to="mod-anh")}),
               thong_diep="thêm mod-anh")
        assert c.put("/api/nguon-git", json=CAU_HINH, headers=QT).status_code == 200
        dong_bo_ngay(c)
        r = c.post("/api/nguon-git/cai/mod-anh", headers=QT)
        # đã đi kèm ảnh → 409 với lý do ảnh, KHÔNG phải can_xac_nhan_cai_moi
        assert r.status_code == 409, r.text
        assert r.json().get("ma") != "can_xac_nhan_cai_moi"


def test_tu_dong_mac_dinh_tat_khong_cai_module_moi(gh, du_lieu):
    """Giữ hành vi hiện có: tu_dong mặc định tắt → module mới không tự cài."""
    with TestClient(app) as c:
        gh.day(tep_kho({"echo": tep_echo()}), thong_diep="thêm echo")
        r = c.put("/api/nguon-git", json=CAU_HINH, headers=QT)
        assert r.status_code == 200
        cfg = c.get("/api/nguon-git", headers=QT).json()
        assert cfg["cau_hinh"].get("tu_dong") is False
        dong_bo_ngay(c)
        mods = c.get("/api/modules", headers=QT).json()["modules"]
        assert all(m["id"] != "echo" for m in mods)


def test_cai_moi_than_rong_khong_kem_ma_409_khong_500_khong_cai(gh, du_lieu):
    """(d) Gọi đường API cài cho module mới với thân rỗng hoặc không kèm mã -> 409 can_xac_nhan_cai_moi, không 500, không cài."""
    with TestClient(app) as c:
        khoi_tao_nguon(c, gh)
        # 1. Gọi không có body
        r1 = c.post("/api/nguon-git/cai/echo", headers=QT)
        assert r1.status_code == 409 and r1.json().get("ma") == "can_xac_nhan_cai_moi"
        # 2. Gọi với body rỗng {}
        r2 = c.post("/api/nguon-git/cai/echo", headers=QT, json={})
        assert r2.status_code == 409 and r2.json().get("ma") == "can_xac_nhan_cai_moi"
        # 3. Gọi với body {"xac_nhan": False}
        r3 = c.post("/api/nguon-git/cai/echo", headers=QT, json={"xac_nhan": False})
        assert r3.status_code == 409 and r3.json().get("ma") == "can_xac_nhan_cai_moi"
        # Đảm bảo tuyệt đối không cài
        mods = c.get("/api/modules", headers=QT).json()["modules"]
        assert all(m["id"] != "echo" for m in mods)


# ───────────── TEST TÁI HIỆN LỖI (LƯỢT SỬA 2) ─────────────

def test_cai_tu_nguon_module_moi_chua_xac_nhan_nem_loi_can_xac_nhan(gh, du_lieu):
    """Lỗi 1 (TRUNG BÌNH): cai_tu_nguon cài module mới chưa xác nhận -> phải ném LoiCanXacNhanCaiMoi."""
    from app.nguon_git import LoiCanXacNhanCaiMoi
    with TestClient(app) as c:
        khoi_tao_nguon(c, gh)
        ng = app.state.nguon_git

        async def _():
            co_loi = False
            try:
                await ng.cai_tu_nguon("chinh", "echo")
            except LoiCanXacNhanCaiMoi as e:
                co_loi = True
                assert e.id_ == "echo"
                assert e.repo == REPO
                assert e.sha_commit
                assert e.ma_xac_nhan
            assert co_loi is True, "cai_tu_nguon cho module mới chưa xác nhận phải ném LoiCanXacNhanCaiMoi"

        c.portal.call(_)
        # Chưa cài vào catalog
        mods = c.get("/api/modules", headers=QT).json()["modules"]
        assert all(m["id"] != "echo" for m in mods)


def test_cai_tu_nguon_module_moi_xac_nhan_ma_dung_thi_cai_thanh_cong(gh, du_lieu):
    """Lỗi 1: cai_tu_nguon lượt 2 gửi kèm xac_nhan=True và ma_xac_nhan đúng -> cài thành công."""
    from app.nguon_git import LoiCanXacNhanCaiMoi
    with TestClient(app) as c:
        khoi_tao_nguon(c, gh)
        ng = app.state.nguon_git

        async def _():
            ma = None
            try:
                await ng.cai_tu_nguon("chinh", "echo")
            except LoiCanXacNhanCaiMoi as e:
                ma = e.ma_xac_nhan
            assert ma is not None, "Lượt 1 phải ném LoiCanXacNhanCaiMoi"
            kq = await ng.cai_tu_nguon("chinh", "echo", xac_nhan=True, ma_xac_nhan=ma)
            assert kq.get("ok") is True

        c.portal.call(_)
        mods = c.get("/api/modules", headers=QT).json()["modules"]
        assert any(m["id"] == "echo" for m in mods)


def test_thoi_gian_xac_nhan_cai_kep_duoi_30_thanh_30(monkeypatch):
    """Lỗi 2 (THẤP): WEB2API_XAC_NHAN_CAI_GIAY < 30 phải được kẹp về 30 thay vì quay về 300."""
    from app.nguon_git import _thoi_gian_xac_nhan_cai
    monkeypatch.setenv("WEB2API_XAC_NHAN_CAI_GIAY", "10")
    assert _thoi_gian_xac_nhan_cai() == 30


def test_thoi_gian_xac_nhan_cai_kep_tren_3600_thanh_3600(monkeypatch):
    """Lỗi 2 (THẤP): WEB2API_XAC_NHAN_CAI_GIAY > 3600 phải được kẹp về 3600 thay vì quay về 300."""
    from app.nguon_git import _thoi_gian_xac_nhan_cai
    monkeypatch.setenv("WEB2API_XAC_NHAN_CAI_GIAY", "5000")
    assert _thoi_gian_xac_nhan_cai() == 3600


def test_thoi_gian_xac_nhan_cai_khong_phai_so_mac_dinh_300_co_canh_bao(monkeypatch, caplog):
    """Lỗi 2: WEB2API_XAC_NHAN_CAI_GIAY không phải số nguyên hợp lệ -> mặc định 300 + cảnh báo log."""
    import logging
    from app.nguon_git import _thoi_gian_xac_nhan_cai
    monkeypatch.setenv("WEB2API_XAC_NHAN_CAI_GIAY", "khong-phai-so")
    with caplog.at_level(logging.WARNING):
        val = _thoi_gian_xac_nhan_cai()
    assert val == 300
    assert "WEB2API_XAC_NHAN_CAI_GIAY" in caplog.text


