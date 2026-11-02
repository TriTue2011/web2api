"""NV296: bảng chi tiết Hệ thống → Module có tab «Kết nối» (cấu hình kết nối nằm NGAY trong bảng, không đẩy sang trang khác).

Chủ máy (03/11): demo đặt phần KẾT NỐI nằm trong bảng chi tiết của module, dạng TAB, thông tin đăng nhập LUÔN ẨN, bấm
mới mở ô để SỬA. Chrome THẬT, lõi thật, module giả «thiết bị» có địa chỉ + tài khoản + mật khẩu (bắt buộc), cổng và vân tay
(tuỳ chọn, vân tay là bí mật):
- chưa kết nối: tab «Kết nối» mở sẵn, chỉ ô BẮT BUỘC, phần còn lại thu vào «Nâng cao»;
- đã kết nối: mật khẩu hiện «••••• đã lưu» + nút Đổi, địa chỉ hiện giá trị + nút Sửa; bấm mới mở ô; bí mật không bao giờ
  nằm trong DOM/thuộc tính/state của trang (kể cả dạng che trả về từ API);
- Bảo mật & người dùng không còn form cấu hình module; neo cũ #khoa-module-<id> chuyển thẳng về tab «Kết nối».
"""

from __future__ import annotations

import json

import httpx
import pytest

from test_web2api_giao_dien import QT, loi_that, o
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture

chrome = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

MAT_KHAU = "MatKhau-Thiet-Bi-bimat-7788"
VAN_TAY = "AA:BB:CC:vantay-bimat-9911"
DIA_CHI = "192.168.1.77"

MA_THIET_BI = r'''
class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]

    def nap(self, cau_hinh):
        pass

    async def cai(self, cau_hinh):
        if not cau_hinh.get("mat_khau"):
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": "Thiếu mật khẩu (giả)."}
        return {"ok": True, "ket_qua_tung_khoa": [{"khoa": "mat_khau", "ok": True}], "loi": None, "cau_hinh_luu": cau_hinh}

    async def dung(self):
        pass

    async def models(self):
        return []

    async def suc_khoe(self):
        return {"ok": True, "khoa": []}
'''


def module_thiet_bi(id_: str = "thiet-bi-gia") -> dict[str, bytes]:
    m = {
        "id": id_, "ten": "Thiết bị giả", "mo_ta": "Thiết bị trong nhà, nối qua địa chỉ + tài khoản.", "phien_ban": "0.1.0",
        "hang": "A", "loai": "thiet_bi", "loai_ten": "Thiết bị", "loai_thu_tu": 40, "giao_thuc": "openai", "tien_to": "tbg",
        "cung_cap": [],
        "truong_cau_hinh": [
            {"khoa": "dia_chi", "nhan": "Địa chỉ", "kieu": "chuoi", "bat_buoc": True, "mac_dinh": None,
             "mo_ta": "Địa chỉ của thiết bị trong mạng nhà, ví dụ 192.168.1.20."},
            {"khoa": "tai_khoan", "nhan": "Tài khoản", "kieu": "chuoi", "bat_buoc": True, "mac_dinh": None,
             "mo_ta": "Tên đăng nhập vào thiết bị."},
            {"khoa": "mat_khau", "nhan": "Mật khẩu", "kieu": "bi_mat", "bat_buoc": True, "mac_dinh": None,
             "mo_ta": "Mật khẩu đăng nhập thiết bị; web2api chỉ giữ, không hiện lại."},
            {"khoa": "cong", "nhan": "Cổng", "kieu": "chuoi", "bat_buoc": False, "mac_dinh": "8080",
             "mo_ta": "Để trống là dùng cổng 8080."},
            {"khoa": "van_tay", "nhan": "Vân tay chứng chỉ", "kieu": "bi_mat", "bat_buoc": False, "mac_dinh": None,
             "mo_ta": "Chỉ cần khi thiết bị dùng chứng chỉ tự ký."}],
        "tai_nguyen": {"ram_them_mb": 16, "dia_mb": None, "gpu": False, "mang_ra": [], "ghi_chu": ""},
    }
    return {"manifest.json": json.dumps(m, ensure_ascii=False).encode(), "module.py": MA_THIET_BI.encode()}


def mo_bang(trang, w, id_="thiet-bi-gia"):
    dang_nhap_quan_tri(trang, w.url)
    trang.di(f"{w.url}/He-thong.dc.html#module-{id_}")
    trang.cho("document.getElementById('ct-trang-thai')", han=20, mo_ta="bảng chi tiết")


def khong_lo_bi_mat(trang):
    """Bí mật không nguyên văn ở HTML, giá trị/thuộc tính ô nhập hay state; đuôi dạng che (API trả về) cũng không được VẼ."""
    html = trang.js("document.documentElement.outerHTML")
    gia_tri_o = trang.js("[...document.querySelectorAll('input, textarea')].map((e) => e.value + '|' + [...e.attributes].map((a) => a.value).join('|'))")
    chu_ve = html + "\n" + "\n".join(gia_tri_o)
    toan = chu_ve + "\n" + "\n".join(trang.state_thanh_phan())
    for bi_mat in (MAT_KHAU, VAN_TAY):
        assert bi_mat not in toan, "bí mật lộ nguyên văn"
        assert bi_mat[-4:] not in chu_ve, "đuôi bí mật (dạng che) bị vẽ lại"


@chrome
def test_cho_khoa_mo_san_tab_ket_noi_chi_o_bat_buoc_con_lai_vao_nang_cao(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, modules={"thiet-bi-gia": module_thiet_bi()}) as w:
        mo_bang(trang, w)
        assert trang.js(o("thiet-bi-gia") + ".dataset.tt") == "cho-khoa"
        # Tab «Kết nối» CÓ và đang chọn; không liên kết nào sang Bảo mật; vẫn ở trang Module.
        assert trang.chu("#tab-ket-noi") == "Kết nối" and trang.chu("#tab-tong-quan") == "Tổng quan"
        assert trang.js("document.getElementById('tab-ket-noi').getAttribute('aria-selected')") == "true"
        assert trang.js("!!document.getElementById('lien-ket')") is True
        assert trang.js("document.getElementById('lk-khoa-bao-mat')") is None
        assert trang.js("[...document.querySelectorAll('#chi-tiet a')].some((a) => /Bao-mat/.test(a.getAttribute('href') || ''))") is False
        assert trang.js("location.pathname").endswith("/He-thong.dc.html")
        # Chưa kết nối: ô nhập của trường BẮT BUỘC (địa chỉ, tài khoản, mật khẩu), mô tả bằng tiếng thường.
        khoa = trang.js("[...document.querySelectorAll('#lien-ket [data-truong]')].map((e) => e.dataset.truong)")
        assert khoa == ["dia_chi", "tai_khoan", "mat_khau"], khoa
        assert trang.js("document.querySelectorAll('#lien-ket input[type=password]').length") == 1
        assert "mạng nhà" in trang.chu("#lien-ket [data-truong=dia_chi]")
        # Phần còn lại (cổng, vân tay) thu vào «Nâng cao».
        assert trang.chu("#nut-nang-cao") == "Nâng cao (2)"
        trang.bam("document.getElementById('nut-nang-cao')")
        trang.cho("document.querySelector('#lien-ket [data-truong=van_tay]')", mo_ta="Nâng cao mở ra")
        assert trang.js("[...document.querySelectorAll('#lien-ket [data-truong]')].map((e) => e.dataset.truong)") \
            == ["dia_chi", "tai_khoan", "mat_khau", "cong", "van_tay"]
        trang.bam("document.getElementById('nut-nang-cao')")
        trang.cho("!document.querySelector('#lien-ket [data-truong=van_tay]')", mo_ta="Nâng cao đóng lại")
        # Thiếu mật khẩu → báo, chưa lưu.
        trang.go("#lien-ket [data-truong=dia_chi] input", DIA_CHI)
        trang.go("#lien-ket [data-truong=tai_khoan] input", "admin")
        trang.bam("document.getElementById('nut-luu-cau-hinh')")
        trang.cho("document.querySelector('#lien-ket .loi-form')", mo_ta="báo thiếu mật khẩu")
        assert "Mật khẩu" in trang.chu("#lien-ket .loi-form")


@chrome
def test_da_ket_noi_bi_mat_an_bam_doi_moi_hien_o_khong_lo_gia_tri(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, modules={"thiet-bi-gia": module_thiet_bi()}) as w:
        mo_bang(trang, w)
        # Kết nối qua chính ô trong tab (không qua API) — mật khẩu gõ vào ô, gửi đi là xoá khỏi trang.
        trang.go("#lien-ket [data-truong=dia_chi] input", DIA_CHI)
        trang.go("#lien-ket [data-truong=tai_khoan] input", "admin")
        trang.go("#lien-ket [data-truong=mat_khau] input", MAT_KHAU)
        trang.bam("document.getElementById('nut-luu-cau-hinh')")
        trang.cho("document.getElementById('kq-ok')", han=20, mo_ta="kết nối xong")
        trang.cho(o("thiet-bi-gia") + "?.dataset.tt === 'dang-chay'", han=15, mo_ta="module đang chạy")
        khong_lo_bi_mat(trang)

        # Đã kết nối: thu gọn. Bí mật «••••• đã lưu» + Đổi; trường thường hiện giá trị + Sửa; không ô nhập nào.
        trang.cho("document.querySelector('#lien-ket [data-gon=mat_khau]')", mo_ta="dạng thu gọn")
        trang.bam("document.getElementById('tab-ket-noi')")
        assert trang.chu("#lien-ket [data-gon=mat_khau] .gia-tri-gon") == "••••• đã lưu"
        assert trang.chu("#lien-ket [data-gon=mat_khau] button") == "Đổi"
        assert trang.chu("#lien-ket [data-gon=dia_chi] .gia-tri-gon") == DIA_CHI
        assert trang.chu("#lien-ket [data-gon=dia_chi] button") == "Sửa"
        assert trang.chu("#lien-ket [data-gon=tai_khoan] .gia-tri-gon") == "admin"
        assert trang.js("document.querySelectorAll('#lien-ket input, #lien-ket textarea').length") == 0
        assert trang.js("!!document.getElementById('nut-luu-cau-hinh')") is False   # chưa sửa gì: chưa có nút lưu
        khong_lo_bi_mat(trang)

        # Bấm Đổi: mới hiện ô nhập, RỖNG (không điền sẵn giá trị cũ), kiểu password; nút Lưu xuất hiện.
        trang.bam("document.querySelector('#lien-ket [data-gon=mat_khau] button')")
        trang.cho("document.querySelector('#lien-ket [data-truong=mat_khau] input')", mo_ta="ô đổi mật khẩu")
        assert trang.js("document.querySelector('#lien-ket [data-truong=mat_khau] input').type") == "password"
        assert trang.js("document.querySelector('#lien-ket [data-truong=mat_khau] input').value") == ""
        assert trang.js("!!document.getElementById('nut-luu-cau-hinh')") is True
        assert trang.js("document.querySelectorAll('#lien-ket input').length") == 1      # chỉ ô vừa mở
        khong_lo_bi_mat(trang)
        # Thôi → đóng lại, giá trị cũ vẫn thế.
        trang.bam("document.querySelector('#lien-ket [data-hd=dong-truong]')")
        trang.cho("document.querySelector('#lien-ket [data-gon=mat_khau]')", mo_ta="thu gọn lại")
        assert trang.js("document.querySelectorAll('#lien-ket input').length") == 0

        # Sửa địa chỉ: ô chữ điền sẵn giá trị đang lưu; đổi rồi lưu → giữ nguyên mật khẩu đã lưu.
        trang.bam("document.querySelector('#lien-ket [data-gon=dia_chi] button')")
        trang.cho("document.querySelector('#lien-ket [data-truong=dia_chi] input')", mo_ta="ô sửa địa chỉ")
        assert trang.js("document.querySelector('#lien-ket [data-truong=dia_chi] input').value") == DIA_CHI
        trang.js("document.querySelector('#lien-ket [data-truong=dia_chi] input').select()")
        trang.go("#lien-ket [data-truong=dia_chi] input", "192.168.1.99")
        trang.bam("document.getElementById('nut-luu-cau-hinh')")
        trang.cho("document.getElementById('kq-ok')", han=20, mo_ta="lưu địa chỉ mới")
        trang.cho("document.querySelector('#lien-ket [data-gon=dia_chi] .gia-tri-gon')?.textContent.includes('192.168.1.99')",
                  han=15, mo_ta="địa chỉ mới hiện ở dạng thu gọn")
        assert trang.chu("#lien-ket [data-gon=mat_khau] .gia-tri-gon") == "••••• đã lưu"
        khong_lo_bi_mat(trang)
        luu = json.loads((tmp_path / "bi_mat" / "thiet-bi-gia.json").read_text())
        assert luu["mat_khau"] == MAT_KHAU      # mật khẩu cũ còn nguyên sau khi chỉ sửa địa chỉ


@chrome
def test_van_tay_bi_mat_tuy_chon_them_bang_nut_them_va_tab_tong_quan_chuyen_qua_lai(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, modules={"thiet-bi-gia": module_thiet_bi()}) as w:
        r = httpx.post(f"{w.url}/api/modules/thiet-bi-gia/cai", headers=QT, json={"cau_hinh": {
            "dia_chi": DIA_CHI, "tai_khoan": "admin", "mat_khau": MAT_KHAU, "van_tay": VAN_TAY}})
        assert r.status_code == 200 and r.json()["ok"], r.text
        mo_bang(trang, w)
        # Đang chạy → mở «Tổng quan» trước; chuyển qua lại không tải lại trang, tab «Kết nối» là đường duy nhất tới ô cấu hình.
        trang.js("window.__khongTaiLai = 1")
        assert trang.js("document.getElementById('tab-tong-quan').getAttribute('aria-selected')") == "true"
        assert trang.js("document.getElementById('lien-ket')") is None
        assert trang.js("!!document.getElementById('nut-go')") is True
        trang.bam("document.getElementById('tab-ket-noi')")
        trang.cho("document.getElementById('lien-ket')", mo_ta="tab Kết nối")
        assert trang.chu("#lien-ket [data-gon=van_tay] .gia-tri-gon") == "••••• đã lưu"
        assert trang.chu("#lien-ket [data-gon=cong] .gia-tri-gon") == "Mặc định: 8080"
        khong_lo_bi_mat(trang)
        trang.bam("document.getElementById('tab-tong-quan')")
        trang.cho("!document.getElementById('lien-ket') && document.getElementById('ct-id')", mo_ta="về Tổng quan")
        assert trang.js("window.__khongTaiLai") == 1
        khong_lo_bi_mat(trang)


@chrome
def test_xoa_khoa_ngay_trong_tab_ket_noi_va_bao_mat_khong_con_form_module(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, modules={"thiet-bi-gia": module_thiet_bi()}) as w:
        r = httpx.post(f"{w.url}/api/modules/thiet-bi-gia/cai", headers=QT, json={"cau_hinh": {
            "dia_chi": DIA_CHI, "tai_khoan": "admin", "mat_khau": MAT_KHAU}})
        assert r.status_code == 200 and r.json()["ok"], r.text
        mo_bang(trang, w)
        trang.bam("document.getElementById('tab-ket-noi')")
        trang.cho("document.getElementById('nut-xoa-khoa')", mo_ta="nút xoá khoá trong tab Kết nối")
        trang.bam("document.getElementById('nut-xoa-khoa')")
        trang.bam("document.getElementById('nut-xoa-khoa-han')")
        trang.cho(o("thiet-bi-gia") + "?.dataset.tt === 'cho-khoa'", han=20, mo_ta="module về «chờ khoá»")
        trang.cho("document.querySelector('#lien-ket input[type=password]')", han=15, mo_ta="form đầy đủ trở lại")
        assert (tmp_path / "modules" / "thiet-bi-gia").exists() and not (tmp_path / "bi_mat" / "thiet-bi-gia.json").exists()

        # Bảo mật & người dùng: chỉ còn khối «Khoá cho dịch vụ ngoài gọi vào» (NV289), KHÔNG còn khối/form cấu hình module
        # và không có ô nhập khoá (khoá do web2api tự sinh).
        trang.di(f"{w.url}/He-thong-Bao-mat.dc.html")
        trang.cho("document.getElementById('khoa-dich-vu-ngoai')", han=20, mo_ta="khối Khoá cho dịch vụ ngoài gọi vào")
        assert trang.js("document.getElementById('khoa-module')") is None
        assert trang.js("document.querySelectorAll('#khoa-dich-vu-ngoai input[type=password], #khoa-dich-vu-ngoai textarea').length") == 0
        assert trang.js("document.getElementById('khoa-module-thiet-bi-gia')") is None
        # Neo cũ #khoa-module-<id> → tab Kết nối của đúng module.
        trang.di(f"{w.url}/He-thong-Bao-mat.dc.html#khoa-module-thiet-bi-gia")
        trang.cho("location.pathname.endsWith('/He-thong.dc.html') && document.getElementById('lien-ket')", han=25,
                  mo_ta="neo cũ chuyển về tab Kết nối")
        assert trang.js("location.hash") == "#module-thiet-bi-gia" or trang.js("location.hash") == ""
        assert trang.chu("#chi-tiet-ten") == "Thiết bị giả"
