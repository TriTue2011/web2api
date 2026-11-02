"""NV297 + NV298 — «Diện mạo mới»: thanh chính Studio · Tác phẩm ▾ · Nhà ▾ · Đời sống ▾ … Hệ thống; mỗi tab chỉ hiện khi có module
ĐÃ CÀI thuộc khu đó; trong tab là chip quản lý/đọc/kiểm soát của module + chip «Cài đặt» luôn ẩn; không còn trang «Thiết bị» chung
hay phân loại rủi ro Đọc/Ghi/Nguy hiểm.

Hai tầng:
  - lõi (không cần Chrome): `giao_dien_cua` suy tab từ cấu trúc manifest; bộ chuẩn hoá `giao_dien`; thực thể CHỈ ĐỌC của module
    quản trị đã cài là đã tin, còn thực thể có hành động giữ cổng SEC-004f (không nới);
  - Chrome THẬT (CDP) + lõi thật: cài/gỡ module giả theo loại → tab hiện/biến mất; Nhà → Mạng với dữ liệu của module; «Cài đặt»
    ẩn tới khi bấm, bí mật «••••• đã lưu»; hành động nguy hiểm xác nhận MỘT lần tại ô; 375 px, thanh dưới ≤ 5 mục.
Giá trị giả toàn bộ (quy ước spec).
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from app import kham_pha, so_module, thiet_bi as tb
from app.main import app
from gia import MayChuThat, ghi_thu_muc
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture

GOC = Path(__file__).resolve().parent.parent
KHOA_QT = "khoa-qt-nv297-chrome-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
MK_ADMIN = "MatKhauAdminBaoMat123456"  # khớp dang_nhap_quan_tri trong ui_chung
MK_GIA = "MatKhauGiaNv297-xyz"
chrome = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

# ───────────────────────────── lõi: suy tab từ cấu trúc manifest ─────────────────────────────


def _man(**kw):
    m = {"id": "x-gia", "ten": "Module Giả (thử)", "loai": "thiet_bi", "cung_cap": ["thiet_bi"], "tien_to": "xgia"}
    m.update(kw)
    return m


def test_giao_dien_cua_suy_tu_cau_truc_khong_ten_module():
    # thiết bị nhà / mạng / camera → Nhà (loại «nha» hay «thiet_bi»)
    assert kham_pha.giao_dien_cua(_man(loai="nha"))["khu"] == "nha"
    g = kham_pha.giao_dien_cua(_man(loai="thiet_bi"))
    assert g["khu"] == "nha" and g["muc"] == "x-gia" and g["nhan"] == "Module Giả" and g["chip"] == []
    # việc cá nhân / giải trí → Đời sống; kênh nhắn tin KHÔNG vào tab nào
    ds = _man(loai="tien_ich", cung_cap=["thiet_bi"], nhom_con={"id": "doi-song-va-giai-tri", "ten": "x", "thu_tu": 3})
    assert kham_pha.giao_dien_cua(ds)["khu"] == "doi_song"
    assert kham_pha.giao_dien_cua(_man(loai="tien_ich", nhom_con={"id": "mot-nhom-khac", "ten": "x"}))["khu"] == "doi_song"
    assert kham_pha.giao_dien_cua(_man(loai="tien_ich"))["khu"] == "doi_song"
    assert kham_pha.giao_dien_cua(_man(loai="tien_ich", nhom_con={"id": "nhan-tin-va-kenh", "ten": "x"})) is None
    # ảnh / video / tệp tạo ra → Tác phẩm; AI chỉ chat thì không
    assert kham_pha.giao_dien_cua(_man(loai="ai", cung_cap=["chat", "anh"]))["khu"] == "tac_pham"
    assert kham_pha.giao_dien_cua(_man(loai="ai", cung_cap=["video"]))["khu"] == "tac_pham"
    assert kham_pha.giao_dien_cua(_man(loai="ai", cung_cap=["chat"])) is None


def test_giao_dien_khai_tuong_minh_thang_suy_ra_va_sai_dinh_dang_thi_bo():
    m = _man(loai="ai", giao_dien={"khu": "nha", "muc": "mang", "nhan": "Mạng", "chip": [{"id": "a", "nhan": "A", "nhom": ["x"], "thu_tu": 2}]})
    kham_pha._chuan_hoa_giao_dien(m)
    assert kham_pha.giao_dien_cua(m) == {"khu": "nha", "muc": "mang", "nhan": "Mạng", "chip": [{"id": "a", "nhan": "A", "nhom": ["x"], "thu_tu": 2}]}
    sai = [
        "chuoi", {"khu": "he_thong", "muc": "mang", "nhan": "M"}, {"khu": "nha", "muc": "Mạng!", "nhan": "M"},
        {"khu": "nha", "muc": "mang", "nhan": ""}, {"khu": "nha", "muc": "mang", "nhan": "M", "chip": "x"},
        {"khu": "nha", "muc": "mang", "nhan": "M", "chip": [{"id": "a", "nhan": "A", "nhom": "x"}]},
        {"khu": "nha", "muc": "mang", "nhan": "M", "chip": [{"id": "a", "nhan": "A", "thu_tu": "1"}]},
    ]
    for g in sai:
        m = _man(loai="ai", cung_cap=["chat"], giao_dien=g)
        kham_pha._chuan_hoa_giao_dien(m)  # không ném — bỏ trường, module vẫn nạp được
        assert "giao_dien" not in m and kham_pha.giao_dien_cua(m) is None, g


def test_hai_module_mang_that_cung_muc_mang_voi_chip_nhu_demo():
    """Manifest THẬT của hai module mạng trong repo: cùng khu Nhà, cùng mục «Mạng»; chip đúng thứ tự demo."""
    ra = {}
    for id_ in ("mikrotik-routeros", "adguard"):
        m = json.loads((GOC / "modules" / id_ / "manifest.json").read_text(encoding="utf-8"))
        assert kham_pha.kiem_manifest(m) is None
        ra[id_] = kham_pha.giao_dien_cua(m)
        assert ra[id_]["khu"] == "nha" and ra[id_]["muc"] == "mang" and ra[id_]["nhan"] == "Mạng"
    chip = sorted(ra["mikrotik-routeros"]["chip"] + ra["adguard"]["chip"], key=lambda c: c["thu_tu"])
    assert [c["nhan"] for c in chip] == ["Thiết bị", "Chờ duyệt", "VPN", "DNS & chặn"]


# ───────────────────────────── lõi: thực thể chỉ đọc là đã tin (NV298) ─────────────────────────────


def test_thuc_the_chi_doc_da_tin_con_hanh_dong_giu_cong_sec_004f():
    # chỉ đọc (khai «doc», không hành động): đã tin ngay, không «chờ xác nhận», cũng không cần ghi sổ lúc cài
    assert tb.tinh_rui_ro_hieu_luc("m.cam_bien", {"rui_ro_mac_dinh": "doc", "tinh_nang": []}, {}, {}) == ("doc", "doc", False)
    assert tb.tinh_rui_ro_hieu_luc("m.cam_bien", {"rui_ro_mac_dinh": "doc"}, {}, {}) == ("doc", "doc", False)
    # có hành động (dù khai «doc»/«ghi»): vẫn nguy_hiem + chờ xác nhận cho tới khi xác nhận — KHÔNG nới
    hd = [{"hanh_dong": "bat"}]
    assert tb.tinh_rui_ro_hieu_luc("m.den", {"rui_ro_mac_dinh": "doc", "tinh_nang": hd}, {}, {}) == ("doc", "nguy_hiem", True)
    assert tb.tinh_rui_ro_hieu_luc("m.den", {"rui_ro_mac_dinh": "ghi", "tinh_nang": hd}, {}, {}) == ("ghi", "nguy_hiem", True)
    # khai nguy_hiem thì mãi nguy_hiem (không «chờ xác nhận» — không có gì để hạ xuống)
    assert tb.tinh_rui_ro_hieu_luc("m.cat", {"rui_ro_mac_dinh": "nguy_hiem", "tinh_nang": hd}, {}, {}) == ("nguy_hiem", "nguy_hiem", False)
    # khai sai/không khai → coi là nguy_hiem như cũ
    assert tb.tinh_rui_ro_hieu_luc("m.la", {"tinh_nang": []}, {}, {})[1] == "nguy_hiem"


# ───────────────────────────── Chrome thật ─────────────────────────────

MA_MODULE = r'''
import json

THUC_THE = json.loads(__THUC_THE__)
TRANG_THAI = json.loads(__TRANG_THAI__)


class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]

    def nap(self, c): self.cfg = dict(c or {})
    async def cai(self, c): return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": dict(c or {})}
    async def chat(self, b, s=False): return 501, {}, b"{}"
    async def models(self): return []
    async def anh(self, body): return 501, {}, b"{}"
    async def suc_khoe(self): return {"ok": True}
    async def dung(self): pass

    async def thuc_the(self, loc=None):
        return [dict(e, id=self.tien_to + "." + e["id"]) for e in THUC_THE]

    async def trang_thai(self, ids):
        return {self.tien_to + "." + k: dict(v) for k, v in TRANG_THAI.items()}

    async def thuc_hien(self, id, hanh_dong, tham_so):
        TRANG_THAI["bao_ve"] = {"gia_tri": "bat" if tham_so.get("bat") else "tat", "don_vi": None, "luc": None, "hop_le": True}
        return {"ok": True}
'''

# Giá trị giả: số liệu đọc được, một ô chưa có dữ liệu, một máy không tên, nhóm «vpn»; một công tắc bảo vệ khai nguy hiểm.
THUC_THE_MANG = [
    {"id": "tong_truy_van", "ten": "Tổng truy vấn DNS", "loai": "cam_bien", "tinh_nang": [], "rui_ro_mac_dinh": "doc"},
    {"id": "ti_le_chan", "ten": "Tỉ lệ chặn", "loai": "cam_bien", "tinh_nang": [], "rui_ro_mac_dinh": "doc"},
    {"id": "chua_do", "ten": "Thời gian xử lý trung bình", "loai": "cam_bien", "tinh_nang": [], "rui_ro_mac_dinh": "doc"},
    {"id": "may_a", "ten": "", "loai": "may_mang", "nhom": "may", "thuoc_tinh": {"ip": "192.168.1.50"}, "tinh_nang": [], "rui_ro_mac_dinh": "doc"},
    {"id": "vpn_1", "ten": "WireGuard Điện thoại (kết nối)", "loai": "cam_bien", "nhom": "vpn", "tinh_nang": [], "rui_ro_mac_dinh": "doc"},
    {"id": "bao_ve", "ten": "Bảo vệ DNS", "loai": "cong_tac", "rui_ro_mac_dinh": "nguy_hiem",
     "tinh_nang": [{"hanh_dong": "bao_ve", "mo_ta": "Bật hoặc tắt bảo vệ DNS. Tắt thì cả nhà không còn chặn quảng cáo.",
                    "tham_so": {"type": "object", "required": ["bat"], "additionalProperties": False,
                                "properties": {"bat": {"type": "boolean"}}}}]},
]
TRANG_THAI_MANG = {
    "tong_truy_van": {"gia_tri": 12345, "don_vi": None, "luc": None, "hop_le": True},
    "ti_le_chan": {"gia_tri": 17.5, "don_vi": "%", "luc": None, "hop_le": True},
    "chua_do": {"gia_tri": None, "don_vi": "giây", "luc": None, "hop_le": False},
    "may_a": {"gia_tri": "len", "don_vi": None, "luc": None, "hop_le": True},
    "vpn_1": {"gia_tri": "len", "don_vi": None, "luc": None, "hop_le": True},
    "bao_ve": {"gia_tri": "bat", "don_vi": None, "luc": None, "hop_le": True},
}
GD_MANG = {"khu": "nha", "muc": "mang", "nhan": "Mạng", "chip": [
    {"id": "thiet_bi", "nhan": "Thiết bị", "nhom": ["may"], "thu_tu": 10},
    {"id": "cho_duyet", "nhan": "Chờ duyệt", "nhom": ["khong_co"], "thu_tu": 20},
    {"id": "vpn", "nhan": "VPN", "nhom": ["vpn"], "thu_tu": 30},
    {"id": "dns", "nhan": "DNS & chặn", "nhom": [], "thu_tu": 40}]}
TRUONG_KET_NOI = [
    {"khoa": "dia_chi", "nhan": "Địa chỉ máy", "kieu": "chuoi", "bat_buoc": True, "mac_dinh": None, "mo_ta": "Địa chỉ trong mạng nhà, ví dụ 192.168.1.2."},
    {"khoa": "mat_khau", "nhan": "Mật khẩu", "kieu": "bi_mat", "bat_buoc": True, "mac_dinh": None, "mo_ta": "Mật khẩu của máy đó."},
]


def tao_module(id_, tien_to, *, loai, cung_cap=("thiet_bi",), giao_dien=None, nhom_con=None, thuc_the=(), trang_thai=None, truong=()):
    manifest = {"id": id_, "ten": f"Module {id_} (giả)", "mo_ta": "Module giả cho test NV297", "loai": loai, "hang": "A", "giao_thuc": "cuc_bo",
                "tien_to": tien_to, "cung_cap": list(cung_cap), "truong_cau_hinh": list(truong),
                "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False, "mang_ra": [], "ghi_chu": ""}}
    if giao_dien:
        manifest["giao_dien"] = giao_dien
    if nhom_con:
        manifest["nhom_con"] = nhom_con
    ma = MA_MODULE.replace("__THUC_THE__", repr(json.dumps(list(thuc_the)))).replace("__TRANG_THAI__", repr(json.dumps(trang_thai or {})))
    return {"manifest.json": json.dumps(manifest, ensure_ascii=False).encode(), "module.py": ma.encode()}


def mod_mang(id_="mang-gia", tien_to="mg", **kw):
    return tao_module(id_, tien_to, loai="thiet_bi", giao_dien=GD_MANG, thuc_the=THUC_THE_MANG, trang_thai=TRANG_THAI_MANG, **kw)


def mod_chat(id_="chat-gia", tien_to="cg"):
    return tao_module(id_, tien_to, loai="ai", cung_cap=("chat",))


@contextmanager
def loi_that(tmp_path, monkeypatch, modules=None):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    for id_, tep in (modules or {}).items():
        ghi_thu_muc(tmp_path / "modules" / id_, tep)
    with MayChuThat(app, lifespan="on") as w:
        yield w


def cai(w, id_, cau_hinh=None):
    r = httpx.post(f"{w.url}/api/modules/{id_}/cai", json={"cau_hinh": cau_hinh or {}}, headers=QT)
    assert r.status_code == 200, r.text


def nav_chinh(trang):
    """Mã các tab thanh trên (data-nav) theo thứ tự trong DOM — chờ tới khi thanh dựng xong."""
    trang.cho("document.querySelector('#thanh-tab [data-nav=\"he-thong\"]')", han=30, mo_ta="thanh chính dựng xong")
    return trang.js("[...document.querySelectorAll('#thanh-tab [data-nav]')].map((a) => a.dataset.nav)")


def mo_menu(trang, ma):
    trang.bam(f"document.querySelector('#thanh-tab button[data-nav=\"{ma}\"]')", f"tab {ma}")
    trang.cho(f"document.querySelector('#thanh-tab [data-nav-con]')", mo_ta="menu thả hiện")


def chu_trang(trang):
    return trang.js("document.body.innerText")


def vao(trang, w, tep, *, cho_chip=True):
    trang.di(f"{w.url}/{tep}")
    if cho_chip:
        trang.cho("document.querySelector('[data-chip]')", han=15, mo_ta="hàng chip dựng xong")


def xac_nhan_lai_step_up(trang):
    trang.cho("!!document.querySelector('dialog input[type=\"password\"]')", han=10, mo_ta="dialog xác nhận lại (step-up)")
    trang.go("dialog input[type=\"password\"]", MK_ADMIN)
    trang.bam("[...document.querySelectorAll('dialog button')].find((b) => b.textContent.includes('Xác nhận'))", "nút Xác nhận step-up")


KY_THUAT = ("mg.", "tong_truy_van", "san_sang", "Đặt rủi ro", "Chưa phân khu", "Mọi rủi ro", "Nguy hiểm", "cam_bien", "cong_tac", "may_mang")


@chrome
def test_chi_studio_khi_chua_co_module_khu_roi_gan_vao_thi_hien_tab_go_di_thi_het(trang, tmp_path, monkeypatch):
    """Cài/gỡ module giả theo loại → tab hiện/biến mất; tab không có module nào thì ẨN; không còn «Thiết bị» ở thanh Hệ thống."""
    mods = {"chat-gia": mod_chat(), "mang-gia": mod_mang(),
            "nhac-gia": tao_module("nhac-gia", "ng", loai="tien_ich", nhom_con={"id": "doi-song-va-giai-tri", "ten": "Đời sống và giải trí", "thu_tu": 3}),
            "anh-gia": tao_module("anh-gia", "ag", loai="ai", cung_cap=("chat", "anh"))}
    with loi_that(tmp_path, monkeypatch, {"chat-gia": mods["chat-gia"]}) as w:
        cai(w, "chat-gia")
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/Studio.dc.html")
        assert nav_chinh(trang) == ["studio", "he-thong"], "chưa có module khu nào: chỉ Studio + Hệ thống"
        # đặt thêm ba module theo CẤU TRÚC manifest (nhà/mạng, đời sống, ảnh) rồi cài
        for id_ in ("mang-gia", "nhac-gia", "anh-gia"):
            ghi_thu_muc(tmp_path / "modules" / id_, mods[id_])
        assert httpx.post(f"{w.url}/api/modules/nap-lai", headers=QT).status_code == 200
        for id_ in ("mang-gia", "nhac-gia", "anh-gia"):
            cai(w, id_)
        trang.di(f"{w.url}/Studio.dc.html")
        assert nav_chinh(trang) == ["studio", "tac_pham", "nha", "doi_song", "he-thong"], "đúng thứ tự demo: Studio · Tác phẩm · Nhà · Đời sống … Hệ thống"
        # menu thả ▾ của Nhà liệt kê MỤC (Mạng), không id kỹ thuật
        mo_menu(trang, "nha")
        assert trang.js("[...document.querySelectorAll('#thanh-tab [data-nav-con]')].map((a) => a.textContent.trim())") == ["Mạng"]
        assert trang.js("document.querySelector('#thanh-tab [data-nav-con]').getAttribute('href')") == "Nha.dc.html#mang"
        trang.chup("thanh-chinh-menu-nha")
        # không còn mục «Thiết bị» ở thanh Hệ thống
        trang.di(f"{w.url}/He-thong.dc.html")
        trang.cho("document.querySelector('#thanh-tab [data-nav=\"module\"]')", mo_ta="thanh Hệ thống")
        assert "thiet_bi" not in trang.js("[...document.querySelectorAll('#thanh-tab [data-nav]')].map((a) => a.dataset.nav)")
        # gỡ module nhà → tab Nhà biến mất; tab khác còn
        assert httpx.delete(f"{w.url}/api/modules/mang-gia", headers=QT).status_code == 200
        trang.di(f"{w.url}/Studio.dc.html")
        assert nav_chinh(trang) == ["studio", "tac_pham", "doi_song", "he-thong"]
        # gõ tay địa chỉ trang đã hết module → về Hệ thống → Module kèm lời giải thích (không trang rỗng)
        trang.di(f"{w.url}/Nha.dc.html", mo_muc=False)
        trang.cho("location.pathname.endsWith('He-thong.dc.html')", han=30, mo_ta="về trang Module")
        trang.cho("document.body.innerText.includes('chưa mở được')", han=15, mo_ta="lời giải thích")
        assert "«Nhà» chưa mở được" in chu_trang(trang)


@chrome
def test_nha_mang_chip_dung_demo_du_lieu_that_cua_module_khong_id_ky_thuat_cai_dat_luon_an(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, {"mang-gia": mod_mang(truong=TRUONG_KET_NOI)}) as w:
        cai(w, "mang-gia", {"dia_chi": "192.168.1.2", "mat_khau": MK_GIA})
        dang_nhap_quan_tri(trang, w.url)
        trang.khung(1400, 1000)
        vao(trang, w, "Nha.dc.html#mang")
        chips = trang.js("[...document.querySelectorAll('[data-chip]')].map((b) => b.textContent.trim())")
        assert chips == ["Thiết bị", "Chờ duyệt", "VPN", "DNS & chặn", "Cài đặt"], "chip như demo, «Cài đặt» ở CUỐI"
        assert trang.js("document.querySelector('[data-chip][aria-selected=true]').textContent.trim()") == "Thiết bị"
        # chip «Thiết bị»: máy không tên hiện «Máy chưa đặt tên» + địa chỉ; trạng thái bằng chữ thường
        trang.cho("document.querySelectorAll('[data-o]').length >= 1", mo_ta="ô thiết bị")
        chu = chu_trang(trang)
        assert "Máy chưa đặt tên" in chu and "192.168.1.50" in chu and "Đang nối" in chu
        trang.chup("nha-mang-thiet-bi")
        # chip DNS: số liệu thật, ô chưa đo được ghi «Chưa có dữ liệu» (không số giả), công tắc Bật/Tắt
        trang.bam("document.querySelector('[data-chip=\"dns\"]')", "chip DNS & chặn")
        trang.cho("document.querySelectorAll('[data-o]').length >= 4", mo_ta="ô số liệu DNS")
        chu = chu_trang(trang)
        assert "12.345" in chu and "17,5 %" in chu and "Tổng truy vấn DNS" in chu and "Bảo vệ DNS" in chu
        trang.chup("nha-mang-dns")
        assert trang.js("[...document.querySelectorAll('[data-o]')].find((o) => o.textContent.includes('Thời gian xử lý')).textContent").count("Chưa có dữ liệu") == 1
        # chip «Chờ duyệt»: không module nào cấp → báo thẳng, không mục giả
        trang.bam("document.querySelector('[data-chip=\"cho_duyet\"]')", "chip Chờ duyệt")
        trang.cho("document.querySelector('#chua-co-du-lieu')", mo_ta="trạng thái rỗng thật")
        # KHÔNG id kỹ thuật, KHÔNG phân loại rủi ro, KHÔNG «Thiết bị» chung ở bất kỳ chip nào
        for chip in ("thiet_bi", "dns", "vpn", "cho_duyet"):
            trang.bam(f"document.querySelector('[data-chip=\"{chip}\"]')", chip)
            time.sleep(0.2)
            chu = chu_trang(trang)
            for x in KY_THUAT:
                assert x not in chu, f"chip {chip}: lộ «{x}»"
        # «Cài đặt» LUÔN ẨN: chưa bấm thì chưa có ô nhập/nhãn kết nối nào
        assert "Địa chỉ máy" not in chu and trang.js("document.querySelectorAll('input').length") == 0
        trang.bam("document.querySelector('[data-chip=\"cai-dat\"]')", "chip Cài đặt")
        trang.cho("document.querySelector('[data-gon=\"mat_khau\"]')", han=15, mo_ta="Cài đặt thu gọn dựng xong")
        gon = trang.js("document.querySelector('[data-gon=\"mat_khau\"]').innerText")
        assert "••••• đã lưu" in gon and "Đổi" in gon
        html = trang.js("document.documentElement.outerHTML")
        assert MK_GIA not in html and MK_GIA not in chu_trang(trang), "bí mật không bao giờ trả lại"
        assert trang.js("document.querySelectorAll('input[type=password]').length") == 0, "bí mật chỉ hiện ô nhập khi bấm Đổi"
        trang.bam("document.querySelector('[data-gon=\"mat_khau\"] button')", "Đổi mật khẩu")
        trang.cho("document.querySelector('input[type=password]')", mo_ta="ô nhập mật khẩu mới")
        trang.chup("nha-mang-cai-dat")


@chrome
def test_chua_noi_thi_mo_san_mot_form_ngan_noi_xong_thay_so_lieu_va_cai_dat_thu_gon(trang, tmp_path, monkeypatch):
    """Cài module (chưa có địa chỉ/mật khẩu) → Nhà mở thẳng MỘT form ngắn trong chính module → Lưu → thấy số liệu, Cài đặt ẩn."""
    with loi_that(tmp_path, monkeypatch, {"mang-gia": mod_mang(truong=TRUONG_KET_NOI)}) as w:
        dang_nhap_quan_tri(trang, w.url)
        vao(trang, w, "Nha.dc.html#mang")
        trang.cho("document.querySelector('[data-cai-dat=\"mang-gia\"] input')", han=15, mo_ta="form kết nối hiện sẵn")
        assert trang.js("document.querySelector('[data-chip][aria-selected=true]').textContent.trim()") == "Cài đặt"
        assert "Chưa nối" in chu_trang(trang)
        trang.go("input[type=text]", "192.168.1.2")
        trang.go("input[type=password]", MK_GIA)
        trang.bam("document.querySelector('[data-hd=\"luu\"]')", "Kiểm tra và lưu")
        trang.cho("document.querySelector('#tin-noi')", han=20, mo_ta="báo đã nối")
        trang.cho("document.querySelectorAll('[data-o]').length >= 1", han=15, mo_ta="số liệu hiện ngay sau khi nối")
        assert trang.js("document.querySelector('[data-chip][aria-selected=true]').textContent.trim()") == "Thiết bị"
        assert "Địa chỉ máy" not in chu_trang(trang), "cài đặt đã thu gọn"


@chrome
def test_hanh_dong_nguy_hiem_xac_nhan_mot_lan_tai_o_khong_con_dat_rui_ro(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, {"mang-gia": mod_mang()}) as w:
        cai(w, "mang-gia")
        dang_nhap_quan_tri(trang, w.url)
        vao(trang, w, "Nha.dc.html#mang")
        trang.bam("document.querySelector('[data-chip=\"dns\"]')", "chip DNS")
        trang.cho("[...document.querySelectorAll('[data-o]')].some((o) => o.textContent.includes('Bảo vệ DNS'))", mo_ta="ô Bảo vệ DNS")
        o = "[...document.querySelectorAll('[data-o]')].find((o) => o.textContent.includes('Bảo vệ DNS'))"
        assert trang.js(f"{o}.querySelector('button[aria-pressed=true]').textContent") == "Bật"
        trang.bam(f"[...{o}.querySelectorAll('button')].find((b) => b.textContent === 'Tắt')", "Tắt")
        # chưa gọi gì: phải hỏi xác nhận ngay tại ô, kèm mô tả bằng tiếng thường
        trang.cho(f"{o}.querySelector('[role=alertdialog]')", mo_ta="hỏi xác nhận tại ô")
        assert "Bật hoặc tắt bảo vệ DNS" in trang.js(f"{o}.querySelector('[role=alertdialog]').innerText")
        assert trang.js(f"{o}.querySelector('button[aria-pressed=true]').textContent") == "Bật", "chưa xác nhận thì chưa đổi"
        trang.bam(f"[...{o}.querySelectorAll('[role=alertdialog] button')].find((b) => b.textContent === 'Xác nhận')", "Xác nhận")
        xac_nhan_lai_step_up(trang)  # quản trị làm hành động nguy hiểm: lõi đòi nhập lại mật khẩu MỘT lần (step-up, giữ nguyên)
        trang.cho("document.querySelector('#tin-hd')", han=20, mo_ta="báo đã tắt")
        trang.cho(f"{o}.querySelector('button[aria-pressed=true]').textContent === 'Tắt'", han=20, mo_ta="trạng thái đổi theo lõi")
        assert trang.js("document.querySelectorAll('[data-o] [role=alertdialog]').length") == 0
        for x in ("Đặt rủi ro", "Mọi rủi ro", "Chờ duyệt thực thể"):
            assert x not in chu_trang(trang)


@chrome
def test_hang_cho_phe_duyet_khach_van_hien_de_quan_tri_duyet(trang, tmp_path, monkeypatch):
    """Cổng lõi giữ nguyên: khách gọi hành động chưa xác nhận → hàng chờ 202; trang Nhà hiện «Chờ bạn duyệt», bấm Duyệt chạy được."""
    with loi_that(tmp_path, monkeypatch, {"mang-gia": mod_mang()}) as w:
        cai(w, "mang-gia")
        k = httpx.post(f"{w.url}/api/khoa-khach", headers=QT, json={"nhan": "App nhà", "pham_vi": ["thiet_bi:ghi", "thiet_bi:doc"]})
        khoa = k.json()["khoa"]["khoa"]
        r = httpx.post(f"{w.url}/api/thiet-bi/mg.bao_ve/hanh-dong", headers={"Authorization": f"Bearer {khoa}"},
                       json={"hanh_dong": "bao_ve", "tham_so": {"bat": False}})
        assert r.status_code == 202, r.text
        dang_nhap_quan_tri(trang, w.url)
        vao(trang, w, "Nha.dc.html#mang")
        trang.cho("document.querySelector('#hang-cho')", han=15, mo_ta="khối chờ duyệt")
        chu = trang.js("document.querySelector('#hang-cho').innerText")
        assert "Bảo vệ DNS" in chu and "Bật hoặc tắt bảo vệ DNS" in chu
        assert "mg.bao_ve" not in chu and "bao_ve" not in chu
        trang.bam("[...document.querySelectorAll('#hang-cho button')].find((b) => b.textContent === 'Duyệt')", "Duyệt")
        try:
            trang.cho("!document.querySelector('#hang-cho')", han=6, mo_ta="hàng chờ rỗng")
        except AssertionError:
            xac_nhan_lai_step_up(trang)
            trang.cho("!document.querySelector('#hang-cho')", han=20, mo_ta="hàng chờ rỗng sau step-up")


@chrome
def test_dien_thoai_375_khong_tran_ngang_thanh_duoi_toi_da_5_muc_menu_tha(trang, tmp_path, monkeypatch):
    mods = {"chat-gia": mod_chat(), "mang-gia": mod_mang(),
            "nhac-gia": tao_module("nhac-gia", "ng", loai="tien_ich", nhom_con={"id": "doi-song-va-giai-tri", "ten": "Đời sống và giải trí", "thu_tu": 3}),
            "anh-gia": tao_module("anh-gia", "ag", loai="ai", cung_cap=("chat", "anh"))}
    with loi_that(tmp_path, monkeypatch, mods) as w:
        for id_ in mods:
            cai(w, id_)
        dang_nhap_quan_tri(trang, w.url)
        trang.khung(375, 800, dien_thoai=True)
        for tep in ("Studio.dc.html", "Nha.dc.html#mang", "Doi-song.dc.html", "Tac-pham.dc.html"):
            trang.di(f"{w.url}/{tep}")
            trang.cho("document.querySelectorAll('nav.bn [data-nav-duoi]').length > 0", han=15, mo_ta=f"thanh dưới ({tep})")
            assert trang.js("document.querySelectorAll('nav.bn [data-nav-duoi]').length") <= 5, tep
            assert trang.js("document.documentElement.scrollWidth <= window.innerWidth"), f"{tep}: tràn ngang ở 375px"
        trang.di(f"{w.url}/Nha.dc.html#mang")
        trang.cho("document.querySelector('[data-chip]')", han=15)
        assert trang.js("[...document.querySelectorAll('nav.bn [data-nav-duoi]')].map((a) => a.dataset.navDuoi)") == ["studio", "tac_pham", "nha", "doi_song", "he-thong"]
        trang.bam("document.querySelector('nav.bn button[data-nav-duoi=\"nha\"]')", "Nhà ở thanh dưới")
        trang.cho("document.querySelector('.bn-menu [data-nav-con-duoi]')", mo_ta="menu thả thanh dưới")
        assert trang.js("document.documentElement.scrollWidth <= window.innerWidth")
        trang.chup("nha-375")


@chrome
def test_doi_song_va_tac_pham_hien_dung_khi_co_module_trang_thai_that_khong_mui_gia(trang, tmp_path, monkeypatch):
    mods = {"chat-gia": mod_chat(),
            "nhac-gia": tao_module("nhac-gia", "ng", loai="tien_ich", nhom_con={"id": "doi-song-va-giai-tri", "ten": "Đời sống và giải trí", "thu_tu": 3},
                                   thuc_the=[{"id": "bai", "ten": "Bài đang phát", "loai": "cam_bien", "tinh_nang": [], "rui_ro_mac_dinh": "doc"}],
                                   trang_thai={"bai": {"gia_tri": "Nhạc ru cho bé", "don_vi": None, "luc": None, "hop_le": True}}),
            "anh-gia": tao_module("anh-gia", "ag", loai="ai", cung_cap=("chat", "anh"))}
    with loi_that(tmp_path, monkeypatch, mods) as w:
        for id_ in mods:
            cai(w, id_)
        dang_nhap_quan_tri(trang, w.url)
        vao(trang, w, "Doi-song.dc.html")
        trang.cho("document.querySelectorAll('[data-o]').length >= 1", han=15, mo_ta="ô của module đời sống")
        chu = chu_trang(trang)
        assert "Bài đang phát" in chu and "Nhạc ru cho bé" in chu
        trang.chup("doi-song")
        assert trang.js("[...document.querySelectorAll('[data-chip]')].map((b) => b.textContent.trim()).pop()") == "Cài đặt"
        vao(trang, w, "Tac-pham.dc.html")
        # Tác phẩm: chưa có nơi lưu tác phẩm (Studio giữ ảnh trong trang) → báo thật, KHÔNG vẽ ảnh mẫu
        trang.cho("document.querySelector('#chua-co-du-lieu')", han=15, mo_ta="Tác phẩm rỗng thật")
        assert trang.js("document.querySelectorAll('img, video').length") == 0
        assert "Chưa có dữ liệu" in chu_trang(trang)
        trang.chup("tac-pham")


@chrome
def test_vua_cai_chuyen_toi_nha_mo_dung_muc_va_bo_tham_so_khoi_dia_chi(trang, tmp_path, monkeypatch):
    """Trang Module cài xong chuyển tới `Nha.dc.html?da_cai=<id>`: mở đúng mục của module đó, địa chỉ sạch tham số."""
    with loi_that(tmp_path, monkeypatch, {"mang-gia": mod_mang()}) as w:
        cai(w, "mang-gia")
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/Nha.dc.html?da_cai=mang-gia")
        trang.cho("document.querySelector('[data-chip]')", han=15)
        assert "da_cai" not in trang.js("location.search")
        assert trang.js("document.querySelector('h1').textContent").endswith("Mạng")
