"""Test NV92 P2: module ho-so-google + API đăng nhập danh tính qua trình duyệt.

- Kho danh tính: hai trường mới (trang_thai_dang_nhap, ho_so_trinh_duyet),
  cập nhật có kiểm, tìm theo hồ sơ; bản ghi cũ thiếu trường vẫn đọc được.
- Module ho-so-google (khả năng «dang_nhap_danh_tinh»): ngữ nghĩa 4 bước
  (di_tat / bam_google / cho_phien / can_nguoi), gửi mã 2FA khi chờ, bí mật bị
  xoá sau khi dùng, lỗi lạ đóng cửa. Lõi gọi module theo KHẢ NĂNG, không theo tên.
- API danh tính (chỉ quản trị, NV219 đã gỡ các đường đăng nhập qua danh
  tính: POST /api/danh-tinh/dang-nhap, GET .../{id}/trang-thai,
  POST .../{id}/ma-2fa): liệt kê kèm trạng thái đăng nhập
  (GET /api/danh-tinh), xoá kèm đóng hồ sơ (DELETE .../{id}).
- Mật khẩu/mã 2FA GIẢ không lộ ở bất kỳ phản hồi nào.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import danh_tinh, kho, khoa_khach, khoi_phuc_phien, so_module
from app.main import app
from app.tai_khoan_hang_loat import BiMatTam, DaCoLuotChay

GOC = Path(__file__).resolve().parents[1]
THU_MUC_HSG = GOC / "modules" / "ho-so-google"
TEN_GOI = "ho_so_google_nv92_test"

KHOA_QT = "<redacted>"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
HO_SO_GIA = "ab" * 16
MAT_KHAU_GIA = "mat-khau-gia-nv92-001"
MA_2FA_GIA = "111222"


def _tai_module_hsg():
    for ten in [t for t in list(sys.modules)
                if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC_HSG)]
    sys.modules[TEN_GOI] = goi
    spec = importlib.util.spec_from_file_location(
        f"{TEN_GOI}.module", THU_MUC_HSG / "module.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{TEN_GOI}.module"] = mod
    spec.loader.exec_module(mod)
    return mod


mod_hsg = _tai_module_hsg()
ModuleHSG = mod_hsg.Module
MANIFEST_HSG = json.loads((THU_MUC_HSG / "manifest.json").read_text(encoding="utf-8"))


def chay(coro):
    return asyncio.run(coro)


# ───────────── bộ điều khiển trình duyệt giả ─────────────

class TrinhDuyetGiaNV92(khoi_phuc_phien.BoDieuKhienTrinhDuyet):
    """Bộ điều khiển giả cho test P2: tín hiệu theo trang, lập kịch bản được.

    `da_dien` chỉ ghi TÊN bộ chọn — không ghi giá trị (tránh lộ bí mật nếu ai
    đó log nó), giống TrinhDuyetGia của lõi.
    """

    def __init__(self):
        self.trang = None
        self.tin_hieu_trang: dict[str, set] = {}
        self.da_dien: list[str] = []
        self.da_bam: list[str] = []
        self.cookie: dict = {}
        self.sau_bam: dict[str, dict] = {}
        self.loi = None
        #: Thứ tự giành/nhả quyền (NV92 P1: dịch vụ thật đòi gianh trước).
        self.thu_tu_gianh: list[str] = []

    async def gianh(self) -> None:
        self.thu_tu_gianh.append("gianh")

    async def nha(self) -> None:
        self.thu_tu_gianh.append("nha")

    async def mo_trang(self, url: str) -> None:
        if self.loi is not None:
            raise self.loi
        self.trang = url

    async def dien(self, bo_chon: str, gia_tri: str) -> None:
        self.da_dien.append(bo_chon)

    async def bam(self, bo_chon: str) -> None:
        self.da_bam.append(bo_chon)
        for trang, tin_hieu in self.sau_bam.get(bo_chon, {}).items():
            self.tin_hieu_trang[trang] = set(tin_hieu)

    async def doc_cookie(self, mien: str) -> dict:
        return dict(self.cookie)

    async def tin_hieu(self, quy_tac=None) -> set:
        # NV94: hợp đồng async + quy tắc cấu trúc; giả P2 giữ kịch bản cũ
        # (đánh giá quy tắc cookie theo self.cookie khi được truyền).
        ra = set(self.tin_hieu_trang.get(self.trang, ()))
        for q in (quy_tac or []):
            if isinstance(q, dict) and q.get("loai") == "cookie" \
                    and q.get("ten") in self.cookie:
                ra.add(q.get("tin_hieu"))
        return ra

    async def dong(self) -> None:
        pass


def _bi_mat_da_xoa(bm: BiMatTam) -> bool:
    return all(b == 0 for b in bm._buf)


# ───────────── module ho-so-google: 4 bước ─────────────

def test_di_tat_khong_cham_mat_khau():
    """Hồ sơ đã đăng nhập sẵn → xong ngay bước «đi tắt», không điền gì."""
    mod = ModuleHSG(MANIFEST_HSG)
    td = TrinhDuyetGiaNV92()
    td.tin_hieu_trang[mod_hsg.URL_KIEM_TRA] = {"da_dang_nhap"}
    bm = BiMatTam(MAT_KHAU_GIA)
    kq = chay(mod.dang_nhap(HO_SO_GIA, "ban@gmail.com", bm, td))
    assert kq.thanh_cong and kq.buoc == "di_tat"
    assert td.da_dien == [] and td.da_bam == []
    assert _bi_mat_da_xoa(bm)
    assert mod.tien_do(HO_SO_GIA)["buoc"] == "di_tat"


def test_bam_google_thanh_cong():
    """Điền email/mật khẩu → phiên sống → xong bước «bấm Google»."""
    mod = ModuleHSG(MANIFEST_HSG)
    td = TrinhDuyetGiaNV92()
    td.tin_hieu_trang[mod_hsg.URL_KIEM_TRA] = {"dang_nhap"}
    td.tin_hieu_trang[mod_hsg.URL_DANG_NHAP] = {"dang_nhap"}
    td.sau_bam[mod_hsg.BO_CHON_TIEP_MAT_KHAU] = {
        mod_hsg.URL_DANG_NHAP: {"da_dang_nhap"}}
    bm = BiMatTam(MAT_KHAU_GIA)
    kq = chay(mod.dang_nhap(HO_SO_GIA, "ban@gmail.com", bm, td))
    assert kq.thanh_cong and kq.buoc == "bam_google", kq.sang_dict()
    assert mod_hsg.BO_CHON_EMAIL in td.da_dien
    assert mod_hsg.BO_CHON_MAT_KHAU in td.da_dien
    assert _bi_mat_da_xoa(bm)


def test_cho_phien_2fa_roi_nhap_ma():
    """Google đòi 2FA → cho_2fa; gửi mã → xong; sai trạng thái → từ chối."""
    mod = ModuleHSG(MANIFEST_HSG)
    td = TrinhDuyetGiaNV92()
    td.tin_hieu_trang[mod_hsg.URL_KIEM_TRA] = {"dang_nhap"}
    td.tin_hieu_trang[mod_hsg.URL_DANG_NHAP] = {"dang_nhap"}
    td.sau_bam[mod_hsg.BO_CHON_TIEP_MAT_KHAU] = {
        mod_hsg.URL_DANG_NHAP: {"2fa"}}
    td.sau_bam[mod_hsg.BO_CHON_TIEP_MA_2FA] = {
        mod_hsg.URL_DANG_NHAP: {"da_dang_nhap"}}
    bm = BiMatTam(MAT_KHAU_GIA)
    kq = chay(mod.dang_nhap(HO_SO_GIA, "ban@gmail.com", bm, td))
    assert not kq.thanh_cong and kq.cho_2fa and kq.buoc == "cho_phien"
    assert kq.ma_loi == "cho_2fa"
    assert mod.tien_do(HO_SO_GIA)["cho_2fa"] is True

    ma = BiMatTam(MA_2FA_GIA)
    kq2 = chay(mod.nhap_ma_2fa(HO_SO_GIA, ma, td))
    assert kq2.thanh_cong and kq2.buoc == "bam_google", kq2.sang_dict()
    assert _bi_mat_da_xoa(ma)
    assert mod.tien_do(HO_SO_GIA)["cho_2fa"] is False


def test_nhap_ma_2fa_khi_khong_cho():
    mod = ModuleHSG(MANIFEST_HSG)
    td = TrinhDuyetGiaNV92()
    ma = BiMatTam(MA_2FA_GIA)
    kq = chay(mod.nhap_ma_2fa(HO_SO_GIA, ma, td))
    assert not kq.thanh_cong and kq.ma_loi == "khong_cho_2fa"
    assert _bi_mat_da_xoa(ma)


def test_can_nguoi_khi_captcha():
    """Gặp CAPTCHA → dừng bước «can_nguoi», không điền mật khẩu tiếp."""
    mod = ModuleHSG(MANIFEST_HSG)
    td = TrinhDuyetGiaNV92()
    td.tin_hieu_trang[mod_hsg.URL_KIEM_TRA] = {"dang_nhap"}
    td.tin_hieu_trang[mod_hsg.URL_DANG_NHAP] = {"captcha"}
    bm = BiMatTam(MAT_KHAU_GIA)
    kq = chay(mod.dang_nhap(HO_SO_GIA, "ban@gmail.com", bm, td))
    assert not kq.thanh_cong and kq.can_nguoi and kq.buoc == "can_nguoi"
    assert kq.ma_loi == "can_nguoi"
    assert mod_hsg.BO_CHON_MAT_KHAU not in td.da_dien
    assert _bi_mat_da_xoa(bm)
    assert "CAPTCHA" in kq.huong_dan


def test_loi_la_dong_cua():
    """Bộ điều khiển ném lỗi lạ → hỏng đóng cửa, không bao giờ báo thành công."""
    mod = ModuleHSG(MANIFEST_HSG)
    td = TrinhDuyetGiaNV92()
    td.loi = RuntimeError("mang-gia-dut")
    bm = BiMatTam(MAT_KHAU_GIA)
    kq = chay(mod.dang_nhap(HO_SO_GIA, "ban@gmail.com", bm, td))
    assert not kq.thanh_cong and kq.ma_loi == "loi_module"
    assert _bi_mat_da_xoa(bm)


def test_khong_mat_khau_chua_dang_nhap_can_nguoi():
    mod = ModuleHSG(MANIFEST_HSG)
    td = TrinhDuyetGiaNV92()
    td.tin_hieu_trang[mod_hsg.URL_KIEM_TRA] = {"dang_nhap"}
    kq = chay(mod.dang_nhap(HO_SO_GIA, "ban@gmail.com", None, td))
    assert not kq.thanh_cong and kq.can_nguoi


def test_ket_qua_khong_chua_bi_mat():
    mod = ModuleHSG(MANIFEST_HSG)
    td = TrinhDuyetGiaNV92()
    td.tin_hieu_trang[mod_hsg.URL_KIEM_TRA] = {"da_dang_nhap"}
    kq = chay(mod.dang_nhap(HO_SO_GIA, "ban@gmail.com",
                                   BiMatTam(MAT_KHAU_GIA), td))
    d = kq.sang_dict()
    assert MAT_KHAU_GIA not in json.dumps(d, ensure_ascii=False)
    assert MA_2FA_GIA not in json.dumps(d, ensure_ascii=False)


# ───────────── kho danh tính: trường mới ─────────────

@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    return tmp_path / "data"


def test_danh_tinh_truong_moi_mac_dinh(du_lieu):
    dt = danh_tinh.tao("moi@example.com")
    assert dt["trang_thai_dang_nhap"] == "chua_dang_nhap"
    assert dt["ho_so_trinh_duyet"] == ""


def test_danh_tinh_cap_nhat_va_tim_theo_ho_so(du_lieu):
    dt = danh_tinh.tao("c@example.com")
    ra = danh_tinh.cap_nhat_trang_thai(dt["id"], trang_thai="dang_nhap",
                                       ho_so=HO_SO_GIA)
    assert ra["trang_thai_dang_nhap"] == "dang_nhap"
    assert ra["ho_so_trinh_duyet"] == HO_SO_GIA
    assert danh_tinh.tim_theo_ho_so(HO_SO_GIA)["id"] == dt["id"]
    assert danh_tinh.tim_theo_ho_so("ff" * 16) is None


def test_danh_tinh_cap_nhat_sai_bi_chan(du_lieu):
    dt = danh_tinh.tao("sai@example.com")
    with pytest.raises(Exception):
        danh_tinh.cap_nhat_trang_thai(dt["id"], trang_thai="dang_nhap_x")
    with pytest.raises(Exception):
        danh_tinh.cap_nhat_trang_thai(dt["id"], ho_so="../x")
    with pytest.raises(Exception):
        danh_tinh.cap_nhat_trang_thai("dt-khong-co", trang_thai="dang_nhap")


def test_danh_tinh_ban_ghi_cu_thieu_truong(du_lieu):
    """Bản ghi cũ (trước NV92) thiếu hai trường → đọc ra giá trị mặc định."""
    dt = danh_tinh.tao("cu@example.com")
    assert danh_tinh.lay(dt["id"])["trang_thai_dang_nhap"] == "chua_dang_nhap"


# ───────────── helper tìm module trình duyệt (hồi quy NV92) ─────────────

def _yeu_cau_gia(dang_chay: dict):
    import types
    return types.SimpleNamespace(app=types.SimpleNamespace(
        state=types.SimpleNamespace(so=types.SimpleNamespace(
            dang_chay=dang_chay))))


class _ModTrinhDuyetThat:
    """Module Y NHƯ hợp đồng thật: cung_cap «trinh_duyet» + trinh_duyet_that."""

    def __init__(self):
        self.manifest = {"id": "vnc-xa-gia", "cung_cap": ["trinh_duyet"]}

    def trinh_duyet_that(self, ho_so):
        return object()


def test_tim_module_trinh_duyet_theo_hop_dong_that():
    """HỒI QUY: helper phải tìm module theo đúng tên phương thức lõi dùng
    (`trinh_duyet_that`, xem app/khoi_phuc_phien.lay_trinh_duyet_that) —
    tên cũ `trinh_duyet_lay_dieu_khien` không tồn tại trên module thật nên
    mọi đường hồ sơ Google đều 503 oan."""
    from app import tuyen
    req = _yeu_cau_gia({"vnc-xa": _ModTrinhDuyetThat()})
    assert tuyen._tim_module_trinh_duyet(req) is not None
    req2 = _yeu_cau_gia({})
    assert tuyen._tim_module_trinh_duyet(req2) is None


# ───────────── API danh tính + đăng nhập qua trình duyệt (chỉ quản trị) ─────────────
#
# Lõi gọi module theo KHẢ NĂNG «dang_nhap_danh_tinh», không theo tên module.

class _ModTrinhDuyetGiaP2:
    """Module trình duyệt giả đúng hợp đồng thật: cung_cap + trinh_duyet_tao
    + trinh_duyet_that + trinh_duyet_dong."""

    def __init__(self, tin_hieu_kiem_tra=frozenset()):
        self.manifest = {"id": "mod-trinh-duyet-gia", "cung_cap": ["trinh_duyet"]}
        self._th = set(tin_hieu_kiem_tra)
        self.sau_bam_2fa: dict = {}
        self.da_tao: list = []
        self.da_dong: list = []
        self.dieu_khien_da_cap: list = []

    async def trinh_duyet_tao(self, ten):
        self.da_tao.append(ten)
        return {"ho_so": HO_SO_GIA, "ten": ten}

    def trinh_duyet_that(self, ho_so):
        td = TrinhDuyetGiaNV92()
        td.tin_hieu_trang[mod_hsg.URL_KIEM_TRA] = set(self._th)
        td.sau_bam.update(self.sau_bam_2fa)
        self.dieu_khien_da_cap.append(td)
        return td

    async def trinh_duyet_dong(self, ho_so, giu_phien):
        self.da_dong.append((ho_so, giu_phien))
        return {"ok": True}


class _SoGiaP2:
    def __init__(self):
        self.dang_chay = {}

    def tien_to_cua(self, module_id):
        return "gia" if module_id in self.dang_chay else None


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    with TestClient(app, raise_server_exceptions=False) as client:
        so = _SoGiaP2()
        monkeypatch.setattr(client.app.state, "so", so)
        yield client, so


def _nap_dang_nhap_va_trinh_duyet(so, tin_hieu=frozenset({"da_dang_nhap"})):
    mod_dn_inst = ModuleHSG(MANIFEST_HSG)
    mod_td = _ModTrinhDuyetGiaP2(tin_hieu)
    so.dang_chay["mod-dang-nhap-gia"] = mod_dn_inst
    so.dang_chay["mod-trinh-duyet-gia"] = mod_td
    return mod_dn_inst, mod_td


def test_hsg_403_khoa_khach(api):
    client, _ = api
    _, khoa_tho = khoa_khach.tao_khoa(nhan="khach-nv92", pham_vi=["chat"])
    khach = {"Authorization": f"Bearer {khoa_tho}"}
    assert client.post("/api/danh-tinh/dang-nhap", headers=khach,
                       json={"email": "x@example.com"}).status_code == 403
    assert client.get("/api/danh-tinh/dt-x/trang-thai",
                      headers=khach).status_code == 403


def test_hsg_liet_ke_kem_trang_thai(api):
    """GET /api/danh-tinh liệt kê kèm trạng thái đăng nhập, hồ sơ trình duyệt,
    module đã gắn (không cần đường riêng)."""
    client, so = api
    _nap_dang_nhap_va_trinh_duyet(so)
    danh_tinh.tao("liet-ke@example.com")
    r = client.get("/api/danh-tinh", headers=QT)
    assert r.status_code == 200, r.text
    ds = r.json()["danh_tinh"]
    assert any(h["email"] == "liet-ke@example.com"
               and h["trang_thai_dang_nhap"] == "chua_dang_nhap"
               and h["ho_so_trinh_duyet"] == "" for h in ds)


def test_hsg_trang_thai_404_va_id_xau(api):
    client, so = api
    _nap_dang_nhap_va_trinh_duyet(so)
    r = client.get("/api/danh-tinh/dt-khong-co/trang-thai", headers=QT)
    assert r.status_code == 404
    r = client.get("/api/danh-tinh/..%2F..%2Fx/trang-thai", headers=QT)
    assert r.status_code in (404, 422)


def test_hsg_xoa_404_va_thanh_cong(api):
    client, so = api
    _, mod_td = _nap_dang_nhap_va_trinh_duyet(so)
    r = client.delete("/api/danh-tinh/dt-khong-co", headers=QT)
    assert r.status_code == 404
    dt = danh_tinh.tao("xoa@example.com")
    danh_tinh.cap_nhat_trang_thai(dt["id"], ho_so=HO_SO_GIA)
    r = client.delete(f"/api/danh-tinh/{dt['id']}", headers=QT)
    assert r.status_code == 200, r.text
    assert "canh_bao" in r.json()
    assert mod_td.da_dong == [(HO_SO_GIA, False)]
    assert danh_tinh.lay(dt["id"]) is None


def test_hsg_xoa_khong_ho_so_tra_dung_khuon_cu(api):
    """Xoá danh tính chưa gắn hồ sơ trình duyệt → đúng {"ok": True} như cũ."""
    client, so = api
    _nap_dang_nhap_va_trinh_duyet(so)
    dt = danh_tinh.tao("xoathuong@example.com")
    r = client.delete(f"/api/danh-tinh/{dt['id']}", headers=QT)
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_hsg_xoa_409_khi_con_gan_khong_dong_ho_so(api):
    """Danh tính còn gắn module → 409 và hồ sơ trình duyệt (phiên đăng nhập)
    còn nguyên: KHÔNG được đóng/xoá hồ sơ trước khi biết việc xoá bị từ chối."""
    client, so = api
    _, mod_td = _nap_dang_nhap_va_trinh_duyet(so)
    dt = danh_tinh.tao("con-gan@example.com")
    danh_tinh.cap_nhat_trang_thai(dt["id"], ho_so=HO_SO_GIA)
    danh_tinh.gan(dt["id"], "mod-provider-gia")
    r = client.delete(f"/api/danh-tinh/{dt['id']}", headers=QT)
    assert r.status_code == 409, r.text
    assert mod_td.da_dong == []
    assert danh_tinh.lay(dt["id"]) is not None


def test_bo_chon_khop_cau_truc_trang_google_that():
    """Ô email thật: <input type="text" name="identifier" id="identifierId"> (không phải type=email).
    Trang có ô type=password ẨN tên hiddenPassword: bộ chọn mật khẩu phải loại nó."""
    assert "#identifierId" in mod_hsg.BO_CHON_EMAIL and 'name="identifier"' in mod_hsg.BO_CHON_EMAIL
    assert 'name="Passwd"' in mod_hsg.BO_CHON_MAT_KHAU
    assert ':not([name="hiddenPassword"])' in mod_hsg.BO_CHON_MAT_KHAU


def test_trang_rejected_cua_google_la_can_nguoi_khong_phai_loi_phien():
    """Google chặn trình duyệt tự động bằng /signin/rejected: phải DỪNG ở «cần người», không báo khong_lay_duoc_phien."""
    quy_tac = mod_hsg.QUY_TAC_TIN_HIEU
    assert any(q.get("loai") == "url_chua" and "signin/rejected" in q.get("gia_tri", "")
               and q.get("tin_hieu") == "thu_thach_truy_cap" for q in quy_tac)
    assert "thu_thach_truy_cap" in mod_hsg.TIN_HIEU_CAN_NGUOI
    class TdTuChoi(TrinhDuyetGiaNV92):
        """Như Firefox thật khi Google từ chối: bấm Tiếp xong mà không có ô mật khẩu → điền mật khẩu thất bại."""
        async def dien(self, bo_chon, gia_tri):
            if bo_chon == mod_hsg.BO_CHON_MAT_KHAU:
                raise RuntimeError("no such element")
            await super().dien(bo_chon, gia_tri)

        async def bam(self, bo_chon):
            await super().bam(bo_chon)
            if bo_chon == mod_hsg.BO_CHON_TIEP_EMAIL:
                self.tin_hieu_trang[self.trang] = {"thu_thach_truy_cap"}

    mod = ModuleHSG(MANIFEST_HSG)
    td = TdTuChoi()
    td.tin_hieu_trang[mod_hsg.URL_KIEM_TRA] = set()
    td.tin_hieu_trang[mod_hsg.URL_DANG_NHAP] = set()
    bm = BiMatTam(MAT_KHAU_GIA)
    kq = chay(mod.dang_nhap(HO_SO_GIA, "ban@gmail.com", bm, td))
    assert not kq.thanh_cong and kq.can_nguoi and kq.ma_loi == "can_nguoi", kq.sang_dict()
    assert _bi_mat_da_xoa(bm)


def test_khong_co_o_mat_khau_va_khong_ro_ly_do_thi_dong_cua():
    """Không có ô mật khẩu mà cũng không có tín hiệu thử thách (ví dụ email không tồn tại): báo HỎNG, không bao giờ báo thành công."""
    class TdThieuO(TrinhDuyetGiaNV92):
        async def dien(self, bo_chon, gia_tri):
            if bo_chon == mod_hsg.BO_CHON_MAT_KHAU:
                raise RuntimeError("no such element")
            await super().dien(bo_chon, gia_tri)

    mod = ModuleHSG(MANIFEST_HSG)
    td = TdThieuO()
    td.tin_hieu_trang[mod_hsg.URL_KIEM_TRA] = set()
    td.tin_hieu_trang[mod_hsg.URL_DANG_NHAP] = set()
    bm = BiMatTam(MAT_KHAU_GIA)
    kq = chay(mod.dang_nhap(HO_SO_GIA, "ban@gmail.com", bm, td))
    assert not kq.thanh_cong and not kq.can_nguoi and kq.ma_loi == "loi_module", kq.sang_dict()
    assert _bi_mat_da_xoa(bm)


def test_bo_chon_dang_nhap_cua_chatgpt_va_gemini_khop_trang_that():
    """Đo trên trang thật 18/10/2026 (bấm thử đều chuyển tới accounts.google.com): bộ chọn cũ không khớp trang hiện tại."""
    import importlib.util
    from pathlib import Path
    goc = Path(__file__).resolve().parents[1] / "modules"
    nguon = {m: (goc / m / "module.py").read_text(encoding="utf-8") for m in ("chatgpt-web", "gemini-web", "claude-web")}
    assert '[data-testid="login-form"] form > div > button:first-of-type' in nguon["chatgpt-web"]
    assert 'a[data-test-id="sidenav-error-action-link"]' in nguon["gemini-web"]
    assert 'button[data-testid="login-with-google"]' in nguon["claude-web"]
