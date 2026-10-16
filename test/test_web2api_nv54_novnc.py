"""NV54 — test trước cho vé noVNC: thu hồi theo tài khoản và kiểm Origin.

Chỉ test, không sửa app/.

Bối cảnh: báo cáo NV47 còn hai lỗ hổng P3 (vé noVNC của khoá Bearer quản trị không
thu hồi theo tài khoản) và P4 (đường noVNC không kiểm Origin). Đã có người sửa một
phần (vé có giới hạn số lần và thời hạn) nên đo lại trên main 1f698dd bằng chạy thật:

- P3a (XANH): vé gắn nguoi_dung_id → vô hiệu / đặt lại mật khẩu người khác / xoá
  tài khoản → bị thu hồi (app/tuyen.py gọi cả thu_hoi_phien_nguoi_dung lẫn
  novnc.thu_hoi_phien_theo_nguoi_dung).
- P3b (ĐỎ): vé cấp bằng khoá Bearer quản trị có nguoi_dung_id=None →
  thu_hoi_phien_theo_nguoi_dung không bao giờ chạm tới (hàm trả 0 ngay khi
  not nguoi_dung_id) → vé vẫn sống sau mọi thu hồi theo tài khoản.
- (ĐỎ): tự đổi mật khẩu — app/tuyen.py doi_mat_khau_toi gọi
  thu_hoi_phien_nguoi_dung(uid, tru_bam=...) → nhánh `if tru_bam is None` bỏ qua
  mọi mốc (kể cả mốc noVNC) → vé/phiên noVNC của chính tài khoản vẫn sống.
- (ĐỎ): hạ vai admin→user — cap_nhat_nguoi_dung chỉ thu hồi khi ban_ghi["bat"]
  là False → hạ vai mà tài khoản vẫn bật thì vé noVNC còn sống.
- P4 (ĐỎ): không có kiểm Origin nào trong app/novnc.py (grep "origin" chỉ ra
  comment và danh sách header bỏ) — GET/POST /novnc/dang-nhap với Origin lạ vẫn
  200/đi tiếp, không 403.
- (XANH): vé một lần dùng (doi_ve_lay_phien pop vé khỏi _VE sau khi qua trần);
  không vé → 401; khoá khách gọi đường quản trị → 403.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import gioi_han, nguoi_dung, novnc, phien_web
from app.main import app

KHOA_QT_GIA = "<redacted>"
MK_TOT = "MatKhauTot123456"
HO_SO_GIA = "abcdef1234567890"
MODULE_GIA = "mod-gia-nv54"
ORIGIN_LA = "https://ke-la.example"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT_GIA)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "1000")
    monkeypatch.setenv("WEB2API_RL_CUA_SO_GIAY", "60")
    monkeypatch.setenv("WEB2API_RL_KHOA_GIAY", "60")
    monkeypatch.setenv("WEB2API_DANG_NHAP_SAI_TOI_DA", "100")
    monkeypatch.delenv("WEB2API_TIN_PROXY", raising=False)
    nguoi_dung._THEO_DOI_SAI.clear()
    gioi_han.khoi_dong()
    yield data_dir
    novnc._VE.clear()
    novnc._PHIEN.clear()
    novnc._HO_SO.clear()


def _tao_tai_khoan(vai: str, ten: str) -> dict:
    ban_ghi, _ten_chuan, _mk = asyncio.run(
        nguoi_dung.tao_nguoi_dung(vai=vai, ten_dang_nhap=ten, mat_khau=MK_TOT)
    )
    return ban_ghi


def _ve_con(ve: str) -> bool:
    return novnc._bam(ve) in novnc._VE


def _chen_ho_so_gia() -> None:
    """Chèn hồ sơ giả vào _HO_SO để doi_ve_lay_phien qua được bước kiểm hồ sơ."""
    novnc._HO_SO[(MODULE_GIA, HO_SO_GIA)] = {"module": MODULE_GIA, "ho_so": HO_SO_GIA}


def _qt() -> dict:
    return {"Authorization": f"Bearer {KHOA_QT_GIA}"}


# ───────────── Xanh: thu hồi theo tài khoản ─────────────

def test_nv54_ve_bearer_khong_bi_thu_hoi_theo_tai_khoan(moi_truong):
    """Vé cấp bằng khoá Bearer quản trị phải mất hiệu lực khi thu hồi theo tài khoản."""
    ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id=None)
    assert _ve_con(ve)
    novnc.thu_hoi_phien_theo_nguoi_dung("bat-ky-id-nv54")
    assert not _ve_con(ve), "vé của Bearer quản trị vẫn sống sau thu hồi theo tài khoản"


def test_nv54_tu_doi_mat_khau_thu_hoi_ve_novnc(moi_truong):
    """Mô phỏng app/tuyen.py doi_mat_khau_toi: tự đổi mật khẩu phải thu hồi vé noVNC của chính tài khoản."""
    uid = _tao_tai_khoan("admin", "nv54-doi-mk")["id"]
    ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id=uid)
    assert _ve_con(ve)
    # doi_mat_khau_toi giữ lại phiên web hiện tại bằng tru_bam -> mốc noVNC không được gọi
    phien_web.thu_hoi_phien_nguoi_dung(uid, tru_bam="bam-gia-nv54")
    assert not _ve_con(ve), "vé noVNC vẫn sống sau khi chủ tài khoản tự đổi mật khẩu"


def test_nv54_ha_vai_thu_hoi_ve_novnc(moi_truong):
    """Hạ vai admin→user (tài khoản vẫn bật) phải thu hồi vé noVNC đã cấp cho tài khoản đó."""
    _tao_tai_khoan("admin", "nv54-giu-lai")
    bi_ha = _tao_tai_khoan("admin", "nv54-bi-ha-vai")
    ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id=bi_ha["id"])
    assert _ve_con(ve)
    with TestClient(app) as c:
        r = c.patch(f"/api/nguoi-dung/{bi_ha['id']}", json={"vai": "user"}, headers=_qt())
        assert r.status_code == 200, r.text
    assert not _ve_con(ve), "vé noVNC vẫn sống sau khi tài khoản bị hạ vai admin→user"


# ───────────── Xanh: kiểm Origin ─────────────

def test_nv54_origin_la_trang_ve_bi_tu_choi(moi_truong):
    """GET trang vé với Origin không thuộc danh sách cho phép → 403."""
    ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id="uid-nv54")
    with TestClient(app) as c:
        r = c.get(f"/novnc/dang-nhap?ve={ve}", headers={"Origin": ORIGIN_LA})
    assert r.status_code == 403, f"Origin lạ không bị chặn ở trang vé: {r.status_code}"


def test_nv54_origin_la_doi_ve_bi_tu_choi(moi_truong):
    """POST đổi vé với Origin không thuộc danh sách cho phép → 403, vé không bị tiêu thụ."""
    _chen_ho_so_gia()
    ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id="uid-nv54")
    with TestClient(app, follow_redirects=False) as c:
        r = c.post(
            "/novnc/dang-nhap",
            data={"ve": ve},
            headers={"Origin": ORIGIN_LA, "Content-Type": "application/x-www-form-urlencoded"},
        )
    assert r.status_code == 403, f"Origin lạ không bị chặn khi đổi vé: {r.status_code}"
    assert _ve_con(ve), "vé bị tiêu thụ dù Origin không hợp lệ"


def test_nv54_thieu_origin_khi_doi_ve_bi_tu_choi(moi_truong):
    """POST đổi vé không có Origin trong khi vé được gửi → 403."""
    _chen_ho_so_gia()
    ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id="uid-nv54")
    with TestClient(app, follow_redirects=False) as c:
        # TestClient luôn gửi Origin cho POST? ép không gửi bằng cách không đặt header
        r = c.post(
            "/novnc/dang-nhap",
            content=f"ve={ve}".encode("ascii"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    # httpx/TestClient có thể tự thêm Origin; nếu request thực sự không có Origin thì phải 403
    origin_gui = r.request.headers.get("origin")
    if origin_gui:
        pytest.skip("TestClient tự thêm Origin nên không đo được nhánh thiếu Origin")
    assert r.status_code == 403, f"thiếu Origin khi đổi vé không bị từ chối: {r.status_code}"


# ───────────── Xanh: phòng thủ (hành vi đã đúng trên main) ─────────────

def test_nv54_ve_dung_mot_lan(moi_truong):
    """Vé đổi thành công một lần; dùng lại → 401."""
    _chen_ho_so_gia()
    ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id="uid-nv54")
    with TestClient(app, follow_redirects=False) as c:
        r1 = c.post(
            "/novnc/dang-nhap",
            data={"ve": ve},
            headers={"Origin": "http://testserver", "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r1.status_code == 303, r1.text
        assert not _ve_con(ve), "vé phải bị tiêu thụ sau khi đổi"
        r2 = c.post(
            "/novnc/dang-nhap",
            data={"ve": ve},
            headers={"Origin": "http://testserver", "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r2.status_code == 401, f"dùng lại vé phải 401, được {r2.status_code}"


def test_nv54_vo_hieu_thu_hoi_ve(moi_truong):
    """Vô hiệu tài khoản → vé noVNC gắn tài khoản bị thu hồi (đã đúng trên main)."""
    uid = _tao_tai_khoan("admin", "nv54-vo-hieu")["id"]
    ve = novnc.cap_ve(MODULE_GIA, HO_SO_GIA, nguoi_dung_id=uid)
    assert _ve_con(ve)
    # mô phỏng nhánh vô hiệu của cap_nhat_nguoi_dung: thu hồi cả hai đường
    phien_web.thu_hoi_phien_nguoi_dung(uid)
    novnc.thu_hoi_phien_theo_nguoi_dung(uid)
    assert not _ve_con(ve), "vé phải mất hiệu lực khi tài khoản bị vô hiệu"


def test_nv54_doi_ve_khong_ve_401(moi_truong):
    """POST đổi vé không gửi vé → 401."""
    with TestClient(app) as c:
        r = c.post(
            "/novnc/dang-nhap",
            data={},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    assert r.status_code == 401, f"không vé phải 401, được {r.status_code}"


def test_nv54_ve_sai_dang_trang_ve_400(moi_truong):
    """GET trang vé với vé sai dạng → 400 ngay, không tra cứu."""
    with TestClient(app) as c:
        r = c.get("/novnc/dang-nhap?ve=ve!!khong_hop_le!!")
    assert r.status_code == 400, f"vé sai dạng phải 400, được {r.status_code}"


def test_nv54_khoa_khach_goi_mo_trinh_duyet_403(moi_truong):
    """Khoá khách gọi đường quản trị mở trình duyệt → 403."""
    with TestClient(app) as c:
        rk = c.post("/api/khoa-khach", json={"nhan": "nv54-khach"}, headers=_qt())
        assert rk.status_code == 200, rk.text
        khoa_khach = rk.json()["khoa"]["khoa"]
        assert khoa_khach.startswith("w2a_")
        r = c.post(
            f"/api/trinh-duyet/{MODULE_GIA}/mo",
            json={"loai": "chromium", "ho_so": HO_SO_GIA},
            headers={"Authorization": f"Bearer {khoa_khach}"},
        )
    assert r.status_code == 403, f"khoá khách gọi đường quản trị phải 403, được {r.status_code}"


def test_nv54_khong_khoa_goi_mo_trinh_duyet_401(moi_truong):
    """Không khoá gọi đường quản trị mở trình duyệt → 401."""
    with TestClient(app) as c:
        r = c.post(
            f"/api/trinh-duyet/{MODULE_GIA}/mo",
            json={"loai": "chromium", "ho_so": HO_SO_GIA},
        )
    assert r.status_code == 401, f"không khoá phải 401, được {r.status_code}"
