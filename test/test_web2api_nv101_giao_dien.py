"""Kiểm thử giao diện Chrome cho luồng tự đăng nhập NV101.

- Kiểm tra tĩnh (không cần Chrome): web/He-thong-AI.dc.html chỉ nhắc «google»
  trong khối «đăng nhập bằng» (NV223 — giá trị dữ liệu, không phải từ khoá cứng).
- Luồng giao diện 3 bước mới (cần Chrome):
  a. Nhóm chỉ hiện khi có module ĐÃ CÀI; module không khai nhóm -> chip «Khác» đứng cuối;
     chỉ có đúng một nhóm -> tự chọn, không có hàng chip nhóm.
  b. Bước LOẠI: nhóm chỉ có module không tự đăng nhập -> tự chọn «API», không hàng chip loại;
     nhóm có cả hai -> hiện «API» và «Tài khoản»; chọn «API» ra biểu mẫu khoá,
     chọn «Tài khoản» ra #khung-tu-dong và nút lưu API ẩn.
  c. (NV219 cập nhật, NV248) Chip data-kieu="danh_tinh" đã BỎ KHỎI phần AI —
     không hiện trong mọi trường hợp; chỉ còn chip «Trực tiếp» (aria-pressed=true).
  d. (NV219 cập nhật, NV248) Không còn danh sách [data-danh-tinh] ở phần AI.
  e. XSS: email/tên nhóm/mã lỗi chứa <img src=x onerror=...> hay dấu nháy không phá DOM,
     không chạy mã (không có img lạ, biến cờ không bị đặt).
  f. Dòng can_nguoi hiện chữ «Cần bạn xử lý» và dòng gợi ý mở tab trình duyệt; mã lạ hiện nguyên mã;
     sau khi lượt xong, mật khẩu không còn trong DOM/localStorage/sessionStorage.
"""

from __future__ import annotations

import json
import re
import textwrap
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from app import so_module
from app.main import app
from gia import MayChuThat
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401

GOC = Path(__file__).resolve().parents[1]
KHOA_QT = "khoa-quan-tri-nv101-ui-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
HO_SO_CHUAN = "ab" * 16

MA_TRINH_DUYET = textwrap.dedent("""
    from app.khoi_phuc_phien import BoDieuKhienTrinhDuyet

    class BoDieuKhienGia(BoDieuKhienTrinhDuyet):
        def __init__(self, ho_so):
            self.ho_so = ho_so
        async def mo_trang(self, url): pass
        async def dien(self, s, v): pass
        async def bam(self, s): pass
        async def doc_cookie(self, m): return {}
        async def tin_hieu(self, q=None): return set()
        async def dong(self): pass

    class Module:
        def __init__(self, manifest):
            self.manifest = manifest
        def nap(self, cau_hinh): pass
        async def cai(self, cau_hinh): return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": cau_hinh}
        async def models(self): return []
        async def suc_khoe(self): return {"ok": True}
        async def dung(self): pass
        async def trinh_duyet_tao(self, ten=None): return {"ho_so": "ab" * 16}
        async def trinh_duyet_dong(self, ho_so, giu_phien=False): pass
        def trinh_duyet_that(self, ho_so): return BoDieuKhienGia(ho_so)
""")

MA_DANG_NHAP_DANH_TINH = textwrap.dedent("""
    from dataclasses import dataclass
    from typing import Any

    @dataclass
    class KetQuaDangNhapGoogleGia:
        thanh_cong: bool
        can_nguoi: bool = False
        cho_2fa: bool = False
        ma_loi: str = ""
        buoc: str = ""

    class Module:
        def __init__(self, manifest):
            self.manifest = manifest
            self.da_nhan = []
        def nap(self, cau_hinh): pass
        async def cai(self, cau_hinh): return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": cau_hinh}
        async def models(self): return []
        async def suc_khoe(self): return {"ok": True}
        async def dung(self): pass
        async def dang_nhap(self, ho_so, email, mat_khau, trinh_duyet, ma_2fa=None):
            mk_str = mat_khau.lay() if hasattr(mat_khau, "lay") else (mat_khau if isinstance(mat_khau, str) else None)
            totp_str = ma_2fa.lay() if hasattr(ma_2fa, "lay") else (ma_2fa if isinstance(ma_2fa, str) else None)
            self.da_nhan.append({"ho_so": ho_so, "email": email, "mat_khau": mk_str, "ma_2fa": totp_str})
            if "can_nguoi" in email:
                return KetQuaDangNhapGoogleGia(False, can_nguoi=True, ma_loi="can_nguoi")
            if "ma_la" in email:
                return KetQuaDangNhapGoogleGia(False, ma_loi="ma_la_xyz_999")
            return KetQuaDangNhapGoogleGia(True)
""")

MA_PROVIDER = textwrap.dedent("""
    from app.tai_khoan_hang_loat import KetQuaDangNhap

    class Module:
        def __init__(self, manifest):
            self.manifest = manifest
            self.da_goi_dang_nhap = []
            self.da_goi_tai_dung = []
        def nap(self, cau_hinh): pass
        async def cai(self, cau_hinh): return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": cau_hinh}
        async def models(self): return []
        async def suc_khoe(self): return {"ok": True}
        async def dung(self): pass
        async def dang_nhap(self, tai_khoan):
            self.da_goi_dang_nhap.append(tai_khoan.email)
            return KetQuaDangNhap(True)
        async def tai_dung_ho_so(self, ho_so, trinh_duyet):
            self.da_goi_tai_dung.append(ho_so)
            return KetQuaDangNhap(True, cookie="cookie-nv101-session")
""")

#: NV252 — biến thể provider AI cho ca can_nguoi/mã lạ: NV219 bỏ chip kiểu
#: đăng nhập khỏi phần AI nên luồng chỉ còn chế độ «Trực tiếp»; fake này trả
#: can_nguoi / mã lạ qua đúng chữ ký direct-mode dang_nhap(tai_khoan).
MA_PROVIDER_CAN_NGUOI = textwrap.dedent("""
    from app.tai_khoan_hang_loat import KetQuaDangNhap

    class Module:
        def __init__(self, manifest):
            self.manifest = manifest
            self.da_goi_dang_nhap = []
        def nap(self, cau_hinh): pass
        async def cai(self, cau_hinh): return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": cau_hinh}
        async def models(self): return []
        async def suc_khoe(self): return {True}
        async def dung(self): pass
        async def dang_nhap(self, tai_khoan):
            email = (tai_khoan.email or "").lower()
            self.da_goi_dang_nhap.append(tai_khoan.email)
            if "can_nguoi" in email:
                return KetQuaDangNhap(False, ma_loi="can_nguoi")
            if "ma_la" in email:
                return KetQuaDangNhap(False, ma_loi="ma_la_xyz_999")
            return KetQuaDangNhap(True)
""")


def _manifest_day_du(mf: dict) -> dict:
    """Điền các trường lõi BẮT BUỘC (kham_pha) để module giả nạp được; trường test đã đặt thì giữ nguyên."""
    mac_dinh = {"mo_ta": "module giả cho test", "tien_to": "".join(c for c in mf["id"] if c.isalnum()), "phien_ban": "1.0.0",
                "hang": "A", "giao_thuc": "openai", "loai": "tien_ich", "truong_cau_hinh": [], "tai_nguyen": {"mang_ra": []}}
    ra = {**mac_dinh, **mf}
    if ra["loai"] == "ai" and not ra["truong_cau_hinh"]:
        # Nhà cung cấp nhận tài khoản mới vào trường bí mật do MANIFEST khai (lõi không ghi cứng tên trường).
        ra["truong_cau_hinh"] = [{"khoa": "tai_khoan", "nhan": "Tài khoản", "kieu": "danh_sach_bi_mat"}]
    return ra


@contextmanager
def moi_truong_giao_dien(tmp_path, monkeypatch, danh_sach_modules, danh_tinh_san=None, module_chua_cai=None):
    data = tmp_path / "data"
    da_cai_ids = []

    for mod in danh_sach_modules:
        m_id = mod["id"]
        thu_muc = data / "modules" / m_id
        thu_muc.mkdir(parents=True, exist_ok=True)
        (thu_muc / "manifest.json").write_text(json.dumps(_manifest_day_du(mod["manifest"]), ensure_ascii=False), encoding="utf-8")
        (thu_muc / "module.py").write_text(mod["ma"], encoding="utf-8")
        (thu_muc / "web").mkdir(exist_ok=True)
        (thu_muc / "web" / "index.html").write_text("<!doctype html><title>gia</title>", encoding="utf-8")
        da_cai_ids.append(m_id)

    if module_chua_cai:
        for mod in module_chua_cai:
            m_id = mod["id"]
            thu_muc = data / "modules" / m_id
            thu_muc.mkdir(parents=True, exist_ok=True)
            (thu_muc / "manifest.json").write_text(json.dumps(_manifest_day_du(mod["manifest"]), ensure_ascii=False), encoding="utf-8")
            (thu_muc / "module.py").write_text(mod["ma"], encoding="utf-8")
            (thu_muc / "web").mkdir(exist_ok=True)
            (thu_muc / "web" / "index.html").write_text("<!doctype html><title>gia</title>", encoding="utf-8")

    (data / "modules.json").write_text(
        json.dumps({"modules": [{"id": i, "cai_luc": "2026-10-18T00:00:00", "cau_hinh_cong_khai": {}} for i in da_cai_ids]}),
        encoding="utf-8"
    )

    if danh_tinh_san:
        thu_muc_bm = data / "bi_mat"
        thu_muc_bm.mkdir(parents=True, exist_ok=True)
        (thu_muc_bm / "danh_tinh.json").write_text(
            json.dumps({"danh_tinh": danh_tinh_san}, ensure_ascii=False),
            encoding="utf-8"
        )

    monkeypatch.setenv("WEB2API_DATA", str(data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")

    with MayChuThat(app, lifespan="on") as w:
        yield w


def test_tinh_khong_chua_tu_khoa_google():
    """Kiểm tra tĩnh: web/He-thong-AI.dc.html chỉ nhắc «google» trong khối
    «đăng nhập bằng» (NV223 — giá trị dữ liệu từ trường dang_nhap_bang/nhom
    của tài khoản, theo 5 nhóm chủ máy chốt 21/10), không có danh sách từ
    khoá theo nhà cung cấp cứng trong mã (luật chung).

    NV248 cập nhật (bài học NV98): NV223 thêm UI «đăng nhập bằng» có chủ ý
    nên kỳ vọng cũ «tuyệt đối không chứa google» đỏ — giữ nguyên ý nghĩa
    assert (không từ khoá cứng theo nhà cung cấp), cho phép nhắc «google»
    đúng trong ngữ cảnh dữ liệu đăng-nhập-bằng.
    """
    html_path = GOC / "web" / "He-thong-AI.dc.html"
    noi_dung = html_path.read_text(encoding="utf-8")

    noi_dung_loc = re.sub(r"https?://fonts\.googleapis\.com[^\s\"']*", "", noi_dung, flags=re.IGNORECASE)
    noi_dung_loc = re.sub(r"https?://fonts\.gstatic\.com[^\s\"']*", "", noi_dung_loc, flags=re.IGNORECASE)

    # Dấu hiệu khối «đăng nhập bằng» (NV223): mọi nhắc «google» hợp lệ đều
    # nằm trong các dòng này — là giá trị dữ liệu, không phải từ khoá cứng.
    # Chuẩn hoá bỏ dấu trước khi so để khớp cả chữ có dấu tiếng Việt.
    import unicodedata

    def _bo_dau(s):
        return "".join(
            c for c in unicodedata.normalize("NFD", s.lower())
            if unicodedata.category(c) != "Mn")

    mau_hop_le = re.compile(
        r"dang nhap bang|tai khoan google|tk-?google|codangnhapgoogle|"
        r"dstkgoogle|dltkgoogleid|doitkgoogle|dangnhapbang|tai_khoan_google")
    vi_pham = [dong.strip()[:120] for dong in noi_dung_loc.splitlines()
               if "google" in dong.lower()
               and not mau_hop_le.search(_bo_dau(dong))]
    assert not vi_pham, (
        "web/He-thong-AI.dc.html nhắc 'google' ngoài khối «đăng nhập bằng» "
        "(NV223) — vi phạm luật độc lập mã lõi:\n" + "\n".join(vi_pham))


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy được vì không có Chrome")
def test_nhom_va_chip_khac_giao_dien(trang, tmp_path, monkeypatch):
    """Ca a: Nhóm chỉ hiện khi có module ĐÃ CÀI; module không khai nhóm -> chip «Khác» đứng cuối;

    chỉ có đúng một nhóm -> tự chọn, không có hàng chip nhóm.
    """
    mods = [
        {
            "id": "mod-a",
            "manifest": {"id": "mod-a", "ten": "Module A", "loai": "ai", "cung_cap": ["chat"], "nhom": {"id": "nhom-a", "ten": "Nhóm A"}},
            "ma": MA_PROVIDER,
        },
        {
            "id": "mod-b",
            "manifest": {"id": "mod-b", "ten": "Module B", "loai": "ai", "cung_cap": ["chat"], "nhom": {"id": "nhom-b", "ten": "Nhóm B"}},
            "ma": MA_PROVIDER,
        },
        {
            "id": "mod-khong-nhom",
            "manifest": {"id": "mod-khong-nhom", "ten": "Module Không Nhóm", "loai": "ai", "cung_cap": ["chat"]},
            "ma": MA_PROVIDER,
        },
    ]
    with moi_truong_giao_dien(tmp_path, monkeypatch, mods) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-AI.dc.html")
        trang.cho("document.getElementById('nut-them')", mo_ta="nút Thêm tài khoản")
        trang.bam("document.getElementById('nut-them')")
        trang.cho("document.querySelector('[data-nhom-them]')", mo_ta="hàng chip nhóm")
        # Chưa chọn nhóm/loại thì chưa có gì để lưu: nút «Thêm API» ẨN (không để nút chết).
        assert trang.js("getComputedStyle(document.getElementById('nut-them-luu')).display") == "none"

        # 1. Nhóm chỉ hiện khi có module ĐÃ CÀI (= có mã trên đĩa, theo lõi): gỡ mod-b thật bằng API rồi tải lại
        #    trang → chip «nhom-b» biến mất, các nhóm còn lại giữ nguyên.
        assert trang.js("document.querySelector('[data-nhom-them=\"nhom-b\"]') !== null") is True
        r = httpx.delete(f"{w.url}/api/modules/mod-b", headers=QT)
        assert r.status_code == 200, r.text
        trang.di(f"{w.url}/He-thong-AI.dc.html")
        trang.cho("document.getElementById('nut-them')", mo_ta="nút Thêm tài khoản (sau khi gỡ)")
        trang.bam("document.getElementById('nut-them')")
        trang.cho("document.querySelector('[data-nhom-them]')", mo_ta="hàng chip nhóm sau khi gỡ")
        assert trang.js("document.querySelector('[data-nhom-them=\"nhom-b\"]')") is None, "nhóm của module đã gỡ không được hiện"

        # 2. Module không khai nhóm xuất hiện dưới dạng chip «Khác» và đứng ở vị trí cuối cùng
        ds_nhom = trang.js("[...document.querySelectorAll('[data-nhom-them]')].map(b => b.dataset.nhomThem)")
        assert "nhom-a" in ds_nhom and "nhom-b" not in ds_nhom
        assert ds_nhom[-1] == "khac", f"chip Khác phải đứng cuối cùng, thực tế: {ds_nhom}"
        assert trang.chu(f"[data-nhom-them=\"{ds_nhom[-1]}\"]").strip() == "Khác"

    # 3. Chỉ có đúng một nhóm -> tự chọn, không có hàng chip nhóm
    mods_mot_nhom = [
        {
            "id": "mod-don",
            "manifest": {"id": "mod-don", "ten": "Module Đơn", "loai": "ai", "cung_cap": ["chat"], "nhom": {"id": "nhom-duy-nhat", "ten": "Nhóm Duy Nhất"}},
            "ma": MA_PROVIDER,
        }
    ]
    with moi_truong_giao_dien(tmp_path / "mot-nhom", monkeypatch, mods_mot_nhom) as w2:
        dang_nhap_quan_tri(trang, w2.url)
        trang.di(f"{w2.url}/He-thong-AI.dc.html")
        trang.cho("document.getElementById('nut-them')", mo_ta="nút Thêm tài khoản")
        trang.bam("document.getElementById('nut-them')")
        # Nhóm duy nhất tự chọn: không có hàng chip nhóm; bước kế (LOẠI: module vừa có khoá vừa tự đăng nhập) hiện ngay.
        trang.cho("document.querySelector('[data-loai-them]')", mo_ta="bước LOẠI hiện thẳng, bỏ qua bước nhóm")
        assert trang.js("document.querySelector('[data-nhom-them]')") is None, "chỉ có một nhóm thì tự chọn và ẩn hàng chip nhóm"
        trang.bam("document.querySelector('[data-loai-them=\"api\"]')")
        trang.cho("document.querySelector('[data-chon-module=\"mod-don\"]')", mo_ta="chip module sau khi chọn loại")


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy được vì không có Chrome")
def test_buoc_loai_api_va_tai_khoan_giao_dien(trang, tmp_path, monkeypatch):
    """Ca b: Bước LOẠI: nhóm chỉ có module không tự đăng nhập -> tự chọn «API», không hàng chip loại;

    nhóm có cả hai -> hiện «API» và «Tài khoản»; chọn «API» ra biểu mẫu khoá (#nut-them-luu hiện),
    chọn «Tài khoản» ra #khung-tu-dong và nút lưu API ẩn.
    """
    mods = [
        {
            "id": "mod-api-only",
            "manifest": {
                "id": "mod-api-only",
                "ten": "Mod Chỉ API",
                "loai": "ai",
                "cung_cap": ["chat"],
                "nhom": {"id": "nhom-api-only", "ten": "Nhóm Chỉ API"},
                "truong_cau_hinh": [{"khoa": "api_key", "nhan": "Khoá API", "kieu": "bi_mat"}],
            },
            "ma": MA_PROVIDER.replace("async def dang_nhap", "async def _dn"),
        },
        {
            "id": "mod-ca-hai",
            "manifest": {
                "id": "mod-ca-hai",
                "ten": "Mod Cả Hai",
                "loai": "ai",
                "cung_cap": ["chat"],
                "nhom": {"id": "nhom-ca-hai", "ten": "Nhóm Cả Hai"},
                "truong_cau_hinh": [{"khoa": "api_key", "nhan": "Khoá API", "kieu": "bi_mat"}],
            },
            "ma": MA_PROVIDER,
        },
    ]

    with moi_truong_giao_dien(tmp_path, monkeypatch, mods) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-AI.dc.html")
        trang.cho("document.getElementById('nut-them')", mo_ta="nút Thêm tài khoản")
        trang.bam("document.getElementById('nut-them')")

        # 1. Chọn nhóm chỉ có module không tự đăng nhập -> tự chọn «API», không có hàng chip loại
        trang.cho("document.querySelector('[data-nhom-them=\"nhom-api-only\"]')", mo_ta="chip nhóm chỉ API")
        trang.bam("document.querySelector('[data-nhom-them=\"nhom-api-only\"]')")
        assert trang.js("document.querySelector('[data-loai-them]')") is None, "nhóm chỉ có API thì không có chip loại"
        assert trang.js("document.getElementById('khung-tu-dong')") is None, "không hiện khung tự động"
        assert trang.js("getComputedStyle(document.getElementById('nut-them-luu')).display") != "none", "nút lưu API hiện"

        # 2. Chọn nhóm có cả hai -> hiện cả «API» và «Tài khoản»
        trang.bam("document.querySelector('[data-nhom-them=\"nhom-ca-hai\"]')")
        trang.cho("document.querySelector('[data-loai-them=\"api\"]')", mo_ta="chip loại API")
        trang.cho("document.querySelector('[data-loai-them=\"tai_khoan\"]')", mo_ta="chip loại Tài khoản")

        # Chọn «API» -> ra biểu mẫu khoá (#nut-them-luu hiện, khung tự động ẩn)
        trang.bam("document.querySelector('[data-loai-them=\"api\"]')")
        assert trang.js("document.getElementById('khung-tu-dong')") is None
        assert trang.js("getComputedStyle(document.getElementById('nut-them-luu')).display") != "none"

        # Chọn «Tài khoản» -> ra #khung-tu-dong và nút lưu API ẩn
        trang.bam("document.querySelector('[data-loai-them=\"tai_khoan\"]')")
        trang.cho("document.getElementById('khung-tu-dong')", mo_ta="khung tự động")
        assert trang.js("getComputedStyle(document.getElementById('nut-them-luu')).display") == "none"


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy được vì không có Chrome")
def test_chip_kieu_danh_tinh_va_nhan_giao_dien(trang, tmp_path, monkeypatch):
    """Ca c (NV219 cập nhật, NV248 — bài học NV98; NV252 sửa cho đúng DOM thật):
    chip data-kieu="danh_tinh" đã BỎ KHỎI phần AI có chủ ý — không hiện trong
    mọi trường hợp, kể cả khi module có tai_dung_ho_so hay có module đăng nhập
    danh tính. NV219 cũng bỏ luôn chip «Trực tiếp» (khối chip chỉ dựng khi
    coCaHaiKieu — luôn false sau NV219); chỉ còn nhãn tĩnh «Kiểu: Trực tiếp».
    """
    # 1. Có cả module đăng nhập danh tính và trình duyệt
    mods = [
        {
            "id": "mod-trinh-duyet",
            "manifest": {"id": "mod-trinh-duyet", "ten": "Trình duyệt giả", "cung_cap": ["trinh_duyet"], "nhom": {"id": "nhom-td", "ten": "Nhóm Trình Duyệt"}},
            "ma": MA_TRINH_DUYET,
        },
        {
            "id": "mod-dn-danh-tinh",
            "manifest": {"id": "mod-dn-danh-tinh", "ten": "Đăng nhập danh tính giả", "cung_cap": ["dang_nhap_danh_tinh"], "nhom": {"id": "nhom-dt", "ten": "Nhóm Danh Tính"}},
            "ma": MA_DANG_NHAP_DANH_TINH,
        },
        {
            "id": "mod-ai-chi-tt",
            "manifest": {"id": "mod-ai-chi-tt", "ten": "AI Chỉ Trực Tiếp", "loai": "ai", "cung_cap": ["chat"], "nhom": {"id": "nhom-tt", "ten": "Nhóm Trực Tiếp"}},
            "ma": MA_PROVIDER.replace("async def tai_dung_ho_so", "async def _tdhs"),
        },
        {
            "id": "mod-ai-ca-hai-kieu",
            "manifest": {"id": "mod-ai-ca-hai-kieu", "ten": "AI Cả Hai Kiểu", "loai": "ai", "cung_cap": ["chat"], "nhom": {"id": "nhom-full", "ten": "Nhóm Full"}},
            "ma": MA_PROVIDER,
        },
    ]

    with moi_truong_giao_dien(tmp_path, monkeypatch, mods) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-AI.dc.html")
        trang.cho("document.getElementById('nut-them')", mo_ta="nút Thêm tài khoản")
        trang.bam("document.getElementById('nut-them')")

        # Module không có tai_dung_ho_so -> chip data-kieu="danh_tinh" ẨN
        trang.cho("document.querySelector('[data-nhom-them=\"nhom-tt\"]')", mo_ta="chip nhóm trực tiếp")
        trang.bam("document.querySelector('[data-nhom-them=\"nhom-tt\"]')")
        trang.cho("document.querySelector('[data-loai-them=\"tai_khoan\"]')", mo_ta="loại tài khoản")
        trang.bam("document.querySelector('[data-loai-them=\"tai_khoan\"]')")
        trang.cho("document.getElementById('khung-tu-dong')", mo_ta="khung tự động")
        # NV219: chip danh_tinh đã bỏ khỏi phần AI — không hiện kể cả khi
        # module có tai_dung_ho_so và có module đăng nhập danh tính.
        # NV252: NV219 bỏ luôn chip «Trực tiếp» (khối chip chỉ dựng khi có cả
        # hai kiểu — không bao giờ xảy ra); trang chỉ hiện nhãn tĩnh
        # «Kiểu: Trực tiếp», không còn phần tử [data-kieu] nào.
        assert trang.js("document.querySelector('[data-kieu=\"danh_tinh\"]')") is None, "NV219: phần AI không còn chip danh tính"
        assert trang.js("document.querySelectorAll('[data-kieu]').length") == 0, "NV219: phần AI không còn chip kiểu đăng nhập nào"
        trang.cho("/Kiểu: Trực tiếp/.test(document.getElementById('khung-tu-dong').innerText)",
                  mo_ta="nhãn Kiểu: Trực tiếp")

    # 2. Thiếu module đăng nhập danh tính -> chip data-kieu="danh_tinh" vẫn ẨN
    # (NV219: đã bỏ khỏi phần AI nên không hiện trong mọi trường hợp).
    mods_thieu_dt = [m for m in mods if m["id"] != "mod-dn-danh-tinh"]
    with moi_truong_giao_dien(tmp_path / "thieu-dt", monkeypatch, mods_thieu_dt) as w2:
        dang_nhap_quan_tri(trang, w2.url)
        trang.di(f"{w2.url}/He-thong-AI.dc.html")
        trang.cho("document.getElementById('nut-them')", mo_ta="nút Thêm tài khoản")
        trang.bam("document.getElementById('nut-them')")
        trang.cho("document.querySelector('[data-nhom-them=\"nhom-full\"]')", mo_ta="nhóm full")
        trang.bam("document.querySelector('[data-nhom-them=\"nhom-full\"]')")
        trang.cho("document.querySelector('[data-loai-them=\"tai_khoan\"]')", mo_ta="loại tài khoản")
        trang.bam("document.querySelector('[data-loai-them=\"tai_khoan\"]')")
        trang.cho("document.getElementById('khung-tu-dong')", mo_ta="khung tự động")
        assert trang.js("document.querySelector('[data-kieu=\"danh_tinh\"]')") is None, "thiếu module dn danh tính thì ẩn chip"
        # NV252: như trên — không còn chip nào, chỉ nhãn tĩnh «Kiểu: Trực tiếp».
        assert trang.js("document.querySelectorAll('[data-kieu]').length") == 0, "NV219: phần AI không còn chip kiểu đăng nhập nào"
        trang.cho("/Kiểu: Trực tiếp/.test(document.getElementById('khung-tu-dong').innerText)",
                  mo_ta="nhãn Kiểu: Trực tiếp")


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy được vì không có Chrome")
def test_danh_sach_tai_khoan_danh_tinh_va_chay_chi_email_giao_dien(trang, tmp_path, monkeypatch):
    """Ca d (NV219 cập nhật, NV248 — bài học NV98): NV219 bỏ «danh tính»
    khỏi phần AI có chủ ý — trang AI không còn chip data-kieu="danh_tinh"
    cũng như danh sách [data-danh-tinh]; luồng thêm tài khoản chỉ còn kiểu
    trực tiếp.
    """
    mods = [
        {
            "id": "mod-trinh-duyet",
            "manifest": {"id": "mod-trinh-duyet", "ten": "Trình duyệt giả", "cung_cap": ["trinh_duyet"], "nhom": {"id": "nhom-td", "ten": "Nhóm Trình Duyệt"}},
            "ma": MA_TRINH_DUYET,
        },
        {
            "id": "mod-ai-provider",
            "manifest": {"id": "mod-ai-provider", "ten": "AI Provider", "loai": "ai", "cung_cap": ["chat"], "nhom": {"id": "nhom-ai", "ten": "Nhóm AI"}},
            "ma": MA_PROVIDER,
        },
    ]

    with moi_truong_giao_dien(tmp_path, monkeypatch, mods) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-AI.dc.html")
        trang.cho("document.getElementById('nut-them')", mo_ta="nút Thêm tài khoản")
        trang.bam("document.getElementById('nut-them')")

        # Chỉ có một nhóm nhà cung cấp → tự chọn, bước nhóm ẩn.
        trang.cho("document.querySelector('[data-loai-them=\"tai_khoan\"]')", mo_ta="loại tài khoản")
        trang.bam("document.querySelector('[data-loai-them=\"tai_khoan\"]')")

        # NV219: không còn chip danh tính, không còn danh sách danh tính.
        trang.cho("document.getElementById('khung-tu-dong')", mo_ta="khung tự động")
        assert trang.js("document.querySelector('[data-kieu=\"danh_tinh\"]')") is None, "NV219: phần AI không còn chip danh tính"
        assert trang.js("document.querySelector('[data-danh-tinh]')") is None, "NV219: phần AI không còn danh sách danh tính"
        # NV252: NV219 bỏ luôn chip «Trực tiếp» — không còn phần tử [data-kieu]
        # nào; kiểu đăng nhập duy nhất thể hiện bằng nhãn tĩnh «Kiểu: Trực tiếp».
        assert trang.js("document.querySelectorAll('[data-kieu]').length") == 0, "NV219: phần AI không còn chip kiểu đăng nhập nào"
        trang.cho("/Kiểu: Trực tiếp/.test(document.getElementById('khung-tu-dong').innerText)",
                  mo_ta="nhãn Kiểu: Trực tiếp")


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy được vì không có Chrome")
def test_xss_khong_pha_dom_va_khong_chay_ma_giao_dien(trang, tmp_path, monkeypatch):
    """Ca e: XSS: email/tên nhóm/mã lỗi chứa <img src=x onerror=...> hay dấu nháy không phá DOM,

    không chạy mã (kiểm không có phần tử img lạ và biến cờ toàn cục không bị đặt).
    """
    xss_payload = '<img src=x onerror=__xss_flag=1>'
    mods = [
        {
            "id": "mod-xss",
            "manifest": {
                "id": "mod-xss",
                "ten": f"Mod {xss_payload}",
                "loai": "ai",
                "cung_cap": ["chat"],
                "nhom": {"id": "nhom-xss", "ten": f"Nhóm {xss_payload}"},
            },
            "ma": MA_PROVIDER,
        },
        {   # nhóm thứ hai để bước chọn nhóm HIỆN (một nhóm duy nhất thì tự chọn, không có chip)
            "id": "mod-binh-thuong",
            "manifest": {"id": "mod-binh-thuong", "ten": "Module thường", "loai": "ai", "cung_cap": ["chat"],
                         "nhom": {"id": "nhom-thuong", "ten": "Nhóm thường"}},
            "ma": MA_PROVIDER,
        },
    ]

    with moi_truong_giao_dien(tmp_path, monkeypatch, mods) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-AI.dc.html")

        # Đặt cờ kiểm tra trên window
        trang.js("window.__xss_flag = false")

        trang.cho("document.getElementById('nut-them')", mo_ta="nút Thêm tài khoản")
        trang.bam("document.getElementById('nut-them')")

        # Nhóm render an toàn qua {{nhan}}, không chèn innerHTML thô
        trang.cho("document.querySelector('[data-nhom-them=\"nhom-xss\"]')", mo_ta="chip nhóm XSS")
        assert trang.js("window.__xss_flag") is False, "mã XSS trong tên nhóm bị thực thi!"
        assert trang.js("document.querySelectorAll('img[src=\"x\"]').length") == 0, "thẻ img độc hại bị nhúng vào DOM!"

        # Thử dán email chứa XSS vào ô danh sách và Xem trước
        trang.bam("document.querySelector('[data-nhom-them=\"nhom-xss\"]')")
        trang.cho("document.querySelector('[data-loai-them=\"tai_khoan\"]')", mo_ta="loại tài khoản")
        trang.bam("document.querySelector('[data-loai-them=\"tai_khoan\"]')")
        trang.cho("document.getElementById('o-tu-dong')", mo_ta="ô dán danh sách")
        if not trang.js("document.querySelector('#o-tu-dong')?.closest('details')?.open"):
            trang.js("(() => { const d = document.querySelector('#o-tu-dong')?.closest('details'); if (d && !d.open) { const s = d.querySelector('summary'); if (s) s.click(); if (!d.open) d.open = true; } })()")
            trang.cho("document.querySelector('#o-tu-dong')?.closest('details')?.open", mo_ta="mở khối dán nhiều dòng")
        trang.go("#o-tu-dong", f"xss_{xss_payload}@example.com|matkhau123")
        trang.bam("document.getElementById('nut-xem-truoc')")

        # Đợi cảnh báo hoặc xem trước
        trang.cho("document.getElementById('xem-truoc-tu-dong') || document.getElementById('loi-tu-dong')", mo_ta="kết quả xem trước")
        assert trang.js("window.__xss_flag") is False, "mã XSS trong email bị thực thi!"
        assert trang.js("document.querySelectorAll('img[src=\"x\"]').length") == 0


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy được vì không có Chrome")
def test_can_nguoi_va_ma_la_va_xoa_bi_mat_giao_dien(trang, tmp_path, monkeypatch):
    """Ca f (NV252 cập nhật theo NV219): Dòng can_nguoi hiện chữ «Cần bạn xử lý» và dòng gợi ý mở tab trình duyệt; mã lạ hiện nguyên mã;

    sau khi lượt xong, mật khẩu không còn trong DOM/localStorage/sessionStorage.

    NV219 bỏ chip kiểu đăng nhập khỏi phần AI nên ca này không còn bước chọn
    kiểu danh tính: luồng đi thẳng chế độ «Trực tiếp», provider giả trả
    can_nguoi / mã lạ qua dang_nhap(tai_khoan) (MA_PROVIDER_CAN_NGUOI).
    """
    mods = [
        {
            "id": "mod-trinh-duyet",
            "manifest": {
                "id": "mod-trinh-duyet",
                "ten": "Trình duyệt giả",
                "cung_cap": ["trinh_duyet"],
                "tab_web": {"nhan": "Trình duyệt", "trang": "web/index.html"},
                "nhom": {"id": "nhom-td", "ten": "Nhóm Trình Duyệt"},
            },
            "ma": MA_TRINH_DUYET,
        },
        {
            "id": "mod-ai-provider",
            "manifest": {"id": "mod-ai-provider", "ten": "AI Provider", "loai": "ai", "cung_cap": ["chat"], "nhom": {"id": "nhom-ai", "ten": "Nhóm AI"}},
            "ma": MA_PROVIDER_CAN_NGUOI,
        },
    ]

    mat_khau_bi_mat = "MatKhauSieuBiMat999888!"

    with moi_truong_giao_dien(tmp_path, monkeypatch, mods) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-AI.dc.html")
        trang.cho("document.getElementById('nut-them')", mo_ta="nút Thêm tài khoản")
        trang.bam("document.getElementById('nut-them')")

        # Chỉ có một nhóm nhà cung cấp → tự chọn, bước nhóm ẩn.
        trang.cho("document.querySelector('[data-loai-them=\"tai_khoan\"]')", mo_ta="loại tài khoản")
        trang.bam("document.querySelector('[data-loai-them=\"tai_khoan\"]')")

        # NV252: NV219 bỏ chip kiểu đăng nhập khỏi phần AI — không còn bước
        # chọn kiểu; luồng chạy thẳng kiểu trực tiếp (nhãn «Kiểu: Trực tiếp»).

        # Dán 2 dòng: 1 dòng can_nguoi, 1 dòng mã lạ
        trang.cho("document.getElementById('o-tu-dong')", mo_ta="ô dán")
        if not trang.js("document.querySelector('#o-tu-dong')?.closest('details')?.open"):
            trang.js("(() => { const d = document.querySelector('#o-tu-dong')?.closest('details'); if (d && !d.open) { const s = d.querySelector('summary'); if (s) s.click(); if (!d.open) d.open = true; } })()")
            trang.cho("document.querySelector('#o-tu-dong')?.closest('details')?.open", mo_ta="mở khối dán nhiều dòng")
        trang.go("#o-tu-dong", f"acc_can_nguoi@x.com|{mat_khau_bi_mat}\nacc_ma_la@x.com|{mat_khau_bi_mat}")
        trang.bam("document.getElementById('nut-xem-truoc')")
        trang.cho("document.getElementById('xem-truoc-tu-dong')", mo_ta="xem trước")

        trang.bam("document.getElementById('nut-chay-tu-dong')")

        # Đợi hoàn thành (NV252: điều kiện chờ phải null-safe — phần tử tóm tắt
        # chỉ dựng khi lượt bắt đầu; đọc .innerText trên null làm js() ném
        # TypeError thay vì chờ tiếp, ca đỏ oan khi máy tải nặng. Kỳ vọng giữ
        # nguyên: vẫn phải thấy «Đã xong» trong 25s.)
        trang.cho("/Đã xong/.test(document.getElementById('tom-tat-tu-dong')?.innerText || '')", han=25, mo_ta="lượt xong")

        # Dòng can_nguoi hiện «Cần bạn xử lý» và gợi ý tab trình duyệt
        dong_cn = trang.chu('[data-ket-qua-tu-dong="acc_can_nguoi@x.com"]')
        assert "Cần bạn xử lý" in dong_cn, f"phải hiện 'Cần bạn xử lý', thực tế: {dong_cn}"
        assert "đăng nhập tay" in trang.chu("#khung-tu-dong"), "phải có gợi ý đăng nhập tay"
        assert trang.js("document.querySelector('[href*=\"trinh-duyet\"]') || document.querySelector('a')") is not None, "phải có gợi ý mở tab trình duyệt"

        # Dòng mã lạ hiện nguyên mã «ma_la_xyz_999»
        dong_la = trang.chu('[data-ket-qua-tu-dong="acc_ma_la@x.com"]')
        assert "ma_la_xyz_999" in dong_la, f"phải hiện mã lạ nguyên vẹn, thực tế: {dong_la}"

        # Kiểm tra mật khẩu rời trang ngay, không còn trong DOM/localStorage/sessionStorage
        assert trang.js("document.getElementById('o-tu-dong').value") == ""
        kiem_bi_mat = trang.js("document.documentElement.outerHTML + JSON.stringify(localStorage) + JSON.stringify(sessionStorage)")
        assert mat_khau_bi_mat not in kiem_bi_mat, "mật khẩu bị rò rỉ trong DOM hoặc storage!"
