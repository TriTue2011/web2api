"""NV179 — «Kiểm lại đăng nhập» cho danh tính + cập nhật trạng thái sau «Đóng · giữ phiên».

Bối cảnh (soát NV155b mục 4.1/4.2/5.1): trước NV179, trạng thái đăng nhập của danh
tính chỉ được ghi ở lần đăng nhập/nhận workspace; sau khi người dùng đóng workspace
bằng «Đóng · giữ phiên» ở trang trình duyệt, trạng thái KHÔNG được cập nhật — và
danh tính chưa gắn hồ sơ thì không hiện ở đâu trên giao diện. NV179 thêm:

- `POST /api/danh-tinh/{id}/kiem-lai` (chỉ quản trị): kiểm lại tín hiệu phiên của
  hồ sơ đã gắn bằng QUY TẮC TÍN HIỆU của module «dang_nhap_danh_tinh» (cùng cơ chế
  NV155c ở `app/tuyen.py::_kiem_tin_hieu_dang_nhap`) — chỉ NÂNG trạng thái lên
  `dang_nhap` khi tín hiệu kiểm được; kiểm không được thì GIỮ NGUYÊN (không đoán);
  chưa gắn hồ sơ → 400 `chua_co_ho_so`; không có danh tính → 404.
- `POST /api/trinh-duyet/{module}/{ho_so}/dong` (noVNC): đóng thành công VỚI
  `giu_phien=true` mà hồ sơ có gắn danh tính thì tự kiểm lại như trên và phản hồi
  kèm trường `danh_tinh` (id + trạng thái) để giao diện báo đúng.

Mọi dịch vụ/module đều là GIẢ cục bộ; tín hiệu do bộ điều khiển giả chấm theo cookie
của hồ sơ, đúng cách NV155c kiểm «Nhận làm tài khoản».
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import danh_tinh, khoa_khach, khoi_phuc_phien, novnc
from test_web2api_nv155c_nhan_workspace import (
    MANIFEST_HSG,
    QT,
    ModuleHSG,
    _DieuKhienGia,
)

KHOA = "khoa-quan-tri-nv179"
QT179 = {"Authorization": f"Bearer {KHOA}"}


def _khach() -> dict:
    """Khoá khách THẬT trong kho (vai khách → 403 ở đường quản trị; khoá lạ → 401)."""
    _, khoa_tho = khoa_khach.tao_khoa(nhan="khach-nv179", pham_vi=["chat"])
    return {"Authorization": f"Bearer {khoa_tho}"}
HO_SO = "aabbccddeeff00112233445566778899"
HO_SO_KHONG_DT = "00112233445566778899aabbccddeeff"


class _DK(_DieuKhienGia):
    """Bộ điều khiển giả: cookie đặt được sau khi tạo; `loi` đặt vào thì tin_hieu ném
    (mô phỏng dịch vụ/workspace chết giữa chừng)."""

    def __init__(self):
        super().__init__({})
        self.loi: Exception | None = None

    async def tin_hieu(self, quy_tac=None) -> set:
        if self.loi is not None:
            raise self.loi
        return await super().tin_hieu(quy_tac)


class _ModTD:
    """Module «trinh_duyet» giả: `trinh_duyet_that` trả bộ điều khiển cho trước;
    `trinh_duyet_dong` báo ok (dùng cho cả đường lõi lẫn đường noVNC đóng)."""

    manifest = {"id": "trinh-duyet-gia179", "cung_cap": ["trinh_duyet"]}

    def __init__(self, dk: _DK):
        self._dk = dk

    def trinh_duyet_that(self, ho_so: str):
        return self._dk

    def trinh_duyet_ds(self) -> list:
        return []

    async def trinh_duyet_dong(self, ho_so: str, giu_phien: bool) -> dict:
        return {"ok": True}


class _SoGia:
    def __init__(self, mods: list):
        self.dang_chay = {m.manifest["id"]: m for m in mods}

    def tien_to_cua(self, module_id: str) -> str:
        return module_id


_APP_GOC = None  # NV200: object app gốc trước reload (để trả lại sau test)


def _nap_app(tmp_path, monkeypatch, mods: list):
    global _APP_GOC
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA)
    import importlib

    import app.so_module as somod

    somod._KHOA_CHINH_DA_DOI = True
    import app.main as main

    if _APP_GOC is None:
        # Lần đầu: chụp object app gốc. importlib.reload bên dưới tạo app MỚI và
        # đăng ký lại dat_nguon_so trỏ sang nó — phải trả lại sau test (rò thứ tự).
        _APP_GOC = main.app
    importlib.reload(main)
    client = TestClient(main.app)
    client.app.state.so = _SoGia(mods)
    somod._KHOA_CHINH_DA_DOI = False
    return client


def _tra_lai_app_goc():
    """NV200: reload đã đăng ký lại dat_nguon_so trỏ sang app mới của riêng file
    này — trả về app gốc mà mọi test khác đang giữ (chống rò thứ tự)."""
    import app.main as _main
    from app import xac_thuc as _xt

    _main.app = _APP_GOC
    _xt.dat_nguon_so(lambda: getattr(_APP_GOC.state, "so", None))
    khoa_khach.dat_nguon_so(lambda: getattr(_APP_GOC.state, "so", None))


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """App thật của lõi; `dang_chay` = module hồ-sơ-google GIẢ (quy tắc tín hiệu
    cookie SID của HSG) + module trình duyệt giả chấm tín hiệu theo cookie."""
    dk = _DK()
    client = _nap_app(tmp_path, monkeypatch, [_ModTD(dk), ModuleHSG(MANIFEST_HSG)])
    yield client, dk
    client.close()
    _tra_lai_app_goc()


def _tao(api_, email: str, *, ho_so: str | None = None, trang_thai: str = "chua_dang_nhap") -> str:
    dt = danh_tinh.tao(email)
    if ho_so is not None:
        danh_tinh.cap_nhat_trang_thai(dt["id"], trang_thai=trang_thai, ho_so=ho_so)
    elif trang_thai != "chua_dang_nhap":
        danh_tinh.cap_nhat_trang_thai(dt["id"], trang_thai=trang_thai)
    return dt["id"]


# ── Kiểm lại đăng nhập ────────────────────────────────────────────────────────


def test_kiem_lai_da_go_khoi_ai_nv219(api):
    """NV219: POST /api/danh-tinh/{id}/kiem-lai đã gỡ khỏi phần AI (cùng tab Danh tính).

    Cập nhật theo bài học NV98: endpoint kiểm lại đăng nhập qua danh tính không
    còn; các ca kiểm tín hiệu cũ được thay bằng khẳng định 404/405.
    """
    client, _ = api
    id_ = _tao(api, "a@example.com", ho_so=HO_SO)
    r = client.post(f"/api/danh-tinh/{id_}/kiem-lai", headers=QT179)
    assert r.status_code in (404, 405), r.text


def _dong(client, ho_so: str, giu_phien: bool = True):
    return client.post(
        f"/api/trinh-duyet/trinh-duyet-gia179/{ho_so}/dong",
        json={"giu_phien": giu_phien},
        headers=QT179,
    )


@pytest.fixture()
def api_dong(tmp_path, monkeypatch):
    """Như `api`: module trình duyệt giả phục vụ cả đường noVNC đóng
    (`trinh_duyet_dong` báo ok) lẫn bộ điều khiển chấm tín hiệu."""
    dk = _DK()
    client = _nap_app(tmp_path, monkeypatch, [_ModTD(dk), ModuleHSG(MANIFEST_HSG)])
    yield client, dk
    client.close()
    _tra_lai_app_goc()
    novnc._VE.clear()


def test_dong_can_quan_tri(api_dong):
    client, _ = api_dong
    r = client.post(
        f"/api/trinh-duyet/trinh-duyet-gia179/{HO_SO}/dong", json={"giu_phien": True}
    )
    assert r.status_code == 401
    r = client.post(
        f"/api/trinh-duyet/trinh-duyet-gia179/{HO_SO}/dong",
        json={"giu_phien": True},
        headers=_khach(),
    )
    assert r.status_code == 403


def test_dong_giu_phien_co_tin_hieu_cap_nhat_danh_tinh(api_dong):
    client, dk = api_dong
    id_ = _tao(api_dong, "dong-co-phien@example.com", ho_so=HO_SO)
    dk.cookie = {"SID": "x"}
    r = _dong(client, HO_SO)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True
    assert j["danh_tinh"] == {"id": id_, "trang_thai_dang_nhap": "dang_nhap"}
    assert danh_tinh.lay(id_)["trang_thai_dang_nhap"] == "dang_nhap"


def test_dong_giu_phien_khong_tin_hieu_giu_nguyen(api_dong):
    client, dk = api_dong
    id_ = _tao(api_dong, "dong-khong-phien@example.com", ho_so=HO_SO)
    dk.cookie = {}
    r = _dong(client, HO_SO)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["danh_tinh"] == {"id": id_, "trang_thai_dang_nhap": "chua_dang_nhap"}
    assert danh_tinh.lay(id_)["trang_thai_dang_nhap"] == "chua_dang_nhap"


def test_dong_giu_phien_loi_tin_hieu_dong_van_ok(api_dong):
    """Lỗi kiểm tín hiệu KHÔNG được làm hỏng đường đóng: đóng vẫn 200, trạng thái
    giữ nguyên (không đoán), phản hồi vẫn kèm trường danh_tinh."""
    client, dk = api_dong
    id_ = _tao(api_dong, "dong-loi@example.com", ho_so=HO_SO, trang_thai="dang_nhap")
    dk.loi = RuntimeError("workspace chết giữa chừng")
    r = _dong(client, HO_SO)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True
    assert j["danh_tinh"]["trang_thai_dang_nhap"] == "dang_nhap"
    assert danh_tinh.lay(id_)["trang_thai_dang_nhap"] == "dang_nhap"


def test_dong_ho_so_khong_gan_danh_tinh_khong_co_truong(api_dong):
    client, dk = api_dong
    dk.cookie = {"SID": "x"}
    r = _dong(client, HO_SO_KHONG_DT)
    assert r.status_code == 200, r.text
    assert "danh_tinh" not in r.json()


def test_dong_khong_giu_phien_khong_kiem(api_dong):
    """Đóng KHÔNG giữ phiên (xoá dữ liệu) thì không kiểm tín hiệu, không cập nhật."""
    client, dk = api_dong
    id_ = _tao(api_dong, "dong-xoa@example.com", ho_so=HO_SO)
    dk.cookie = {"SID": "x"}
    r = _dong(client, HO_SO, giu_phien=False)
    assert r.status_code == 200, r.text
    assert "danh_tinh" not in r.json()
    assert danh_tinh.lay(id_)["trang_thai_dang_nhap"] == "chua_dang_nhap"


# ── Kiểm tĩnh (giao diện/tài liệu đi kèm) ───────────────────────────────────────


def test_khung_dang_ky_muc_danh_tinh():
    """NV219: mục «Danh tính» đã GỠ khỏi DIEU_KIEN trong khung.js (bỏ tab Danh tính
    khỏi phần AI theo thiết kế chủ máy). Cập nhật theo bài học NV98."""
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "web" / "khung.js").read_text(encoding="utf-8")
    m = re.search(r"danh_tinh:\s*\{([^}]*)\}", src)
    assert m is None, "khung.js vẫn còn dòng danh_tinh trong DIEU_KIEN (NV219 đã gỡ)"


def test_trang_vnc_xa_phan_biet_hai_nghia_tai_dung():
    """index.html của vnc-xa: đoạn phân biệt «Qua danh tính» vs «Lấy cookie sang
    module» + liên kết tới mục Danh tính (liên kết chéo hai nghĩa «tái dùng»,
    soát NV155b 5.2) và ghi rõ sau «Đóng · giữ phiên» hệ thống tự kiểm lại phiên."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "modules" / "vnc-xa" / "web" / "index.html").read_text(encoding="utf-8")
    assert "Qua danh tính" in src
    assert "Lấy cookie sang module" in src
    assert "He-thong-AI.dc.html#danh-tinh" in src
    assert "tự kiểm lại phiên" in src


def test_dong_sau_kiem_lai_nhat_quan(api_dong):
    """NV219: endpoint kiem-lai đã gỡ; chỉ kiểm đường «đóng giữ phiên» vẫn cập
    nhật trạng thái danh tính (phần giữ lại cho vnc-xa)."""
    client, dk = api_dong
    id_ = _tao(api_dong, "nhat-quan@example.com", ho_so=HO_SO)
    dk.cookie = {"SID": "x"}
    assert _dong(client, HO_SO).status_code == 200
    # Trạng thái được cập nhật qua đường đóng (không cần gọi kiem-lai riêng)
    assert danh_tinh.lay(id_)["trang_thai_dang_nhap"] == "dang_nhap"
