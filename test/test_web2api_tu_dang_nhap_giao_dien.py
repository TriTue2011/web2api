"""Chrome THẬT: «Tự đăng nhập bằng Gmail» trong Hệ thống → AI → Tài khoản → Thêm API.

Chủ máy 17/10: «chỗ thêm Gmail ở đâu, để tự động đăng nhập». Kiểm vòng đủ trên lõi thật + module provider GIẢ (không mạng):
1. Chế độ chỉ hiện cho module KHAI hàm `dang_nhap` (cờ `dang_nhap_tu_dong` suy từ cấu trúc, không từ tên).
2. Dán danh sách → «Xem trước» (máy chủ tách dòng) → bỏ tick một dòng → chạy LẦN LƯỢT chỉ các dòng còn tick.
3. Mật khẩu rời trang ngay khi bấm chạy (ô dán rỗng, không còn trong DOM/localStorage).
4. Theo dõi tiến độ tới «Đã xong», từng dòng có kết quả; lỗi hiện bằng chữ, không lộ bí mật.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import httpx
import pytest

from app import so_module
from app.main import app
from gia import MayChuThat
from test_web2api_nv88_api import MA_PROVIDER, MANIFEST_PROVIDER, MAT_KHAU_GIA, MODULE_ID
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401

KHOA_QT = "khoa-quan-tri-tu-dang-nhap-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
MA_KHONG_DANG_NHAP = MA_PROVIDER.replace("async def dang_nhap(self, tai_khoan):", "async def _khong_dang_nhap(self, tai_khoan):")
ID_KHONG = "provider-khong-dn"
COOKIE_GIA = "cookie=gia-tu-dang-nhap-000"


@contextmanager
def loi_that(tmp_path, monkeypatch):
    data = tmp_path / "data"
    for id_, ma in ((MODULE_ID, MA_PROVIDER), (ID_KHONG, MA_KHONG_DANG_NHAP)):
        thu_muc = data / "modules" / id_
        thu_muc.mkdir(parents=True)
        man = dict(
            MANIFEST_PROVIDER,
            id=id_,
            ten="Provider " + id_,
            tien_to=id_.replace("-", ""),
            nhom={"id": f"nhom-{id_}", "ten": f"Nhóm {id_}"},
        )
        (thu_muc / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
        (thu_muc / "module.py").write_text(ma.replace("MAT_KHAU_GIA", MAT_KHAU_GIA), encoding="utf-8")
        (thu_muc / "web").mkdir()
        (thu_muc / "web" / "index.html").write_text("<!doctype html><title>gia</title>", encoding="utf-8")
    (data / "modules.json").write_text(json.dumps({"modules": [
        {"id": i, "cai_luc": "2026-10-17T00:00:00", "cau_hinh_cong_khai": {}} for i in (MODULE_ID, ID_KHONG)]}), encoding="utf-8")
    monkeypatch.setenv("WEB2API_DATA", str(data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    with MayChuThat(app, lifespan="on") as w:
        yield w


def test_co_dang_nhap_tu_dong_suy_tu_cau_truc(tmp_path, monkeypatch):
    """Cờ do máy chủ suy từ việc module CÓ hàm `dang_nhap`; không có → False (giao diện không vẽ chế độ)."""
    with loi_that(tmp_path, monkeypatch) as w:
        mods = {m["id"]: m for m in httpx.get(f"{w.url}/api/tinh-nang", headers=QT).json()["modules"]}
        assert mods[MODULE_ID]["dang_nhap_tu_dong"] is True
        assert mods[ID_KHONG]["dang_nhap_tu_dong"] is False


@pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")
def test_vong_tu_dang_nhap_tren_giao_dien(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-AI.dc.html")
        trang.cho("document.getElementById('nut-them')", mo_ta="nút Thêm tài khoản")
        trang.bam("document.getElementById('nut-them')")

        # 1. Module KHÔNG có dang_nhap (và không có trường dán khoá): chẳng có gì để thêm → nhóm của nó không hiện,
        #    cũng không có chip module hay khung tự động cho nó.
        trang.cho(f"document.querySelector('[data-chon-module=\"{MODULE_ID}\"]')", mo_ta="chip provider")
        assert trang.js(f"document.querySelector('[data-nhom-them=\"nhom-{ID_KHONG}\"]')") is None, "nhóm không có gì để thêm phải ẩn"
        assert trang.js(f"document.querySelector('[data-chon-module=\"{ID_KHONG}\"]')") is None

        # 2. Module CÓ dang_nhap: chỉ còn một nhóm và một loại («Tài khoản») → tự chọn cả hai, không có chip nhóm/loại.
        assert trang.js("document.querySelector('[data-nhom-them]')") is None, "một nhóm duy nhất thì tự chọn"
        assert trang.js("document.querySelector('[data-loai-them]')") is None, "một loại duy nhất thì tự chọn"
        trang.bam(f"document.querySelector('[data-chon-module=\"{MODULE_ID}\"]')")
        trang.cho("document.getElementById('o-tu-dong')", mo_ta="ô dán danh sách")
        assert trang.js("getComputedStyle(document.getElementById('nut-them-luu')).display") == "none", "nút lưu API ẩn ở loại Tài khoản"

        # dán 3 dòng + 1 dòng lỗi + 1 dòng trùng
        if not trang.js("document.querySelector('#o-tu-dong')?.closest('details')?.open"):
            trang.js("(() => { const d = document.querySelector('#o-tu-dong')?.closest('details'); if (d && !d.open) { const s = d.querySelector('summary'); if (s) s.click(); if (!d.open) d.open = true; } })()")
            trang.cho("document.querySelector('#o-tu-dong')?.closest('details')?.open", mo_ta="mở khối dán nhiều dòng")
        trang.go("#o-tu-dong", f"a@x.com|{MAT_KHAU_GIA}\nb@x.com|mat-khau-sai-123\nc@x.com|{COOKIE_GIA}\nkhong-phai-email|x\nA@x.com|{MAT_KHAU_GIA}")
        trang.bam("document.getElementById('nut-xem-truoc')")
        trang.cho("document.getElementById('xem-truoc-tu-dong')", mo_ta="xem trước")
        assert trang.js("[...document.querySelectorAll('[data-tk-tu-dong]')].map(l => l.dataset.tkTuDong)") == ["a@x.com", "b@x.com", "c@x.com"]
        xt = trang.chu("#xem-truoc-tu-dong")
        assert "3 tài khoản hợp lệ" in xt and "1 dòng lỗi" in xt and "trùng" in xt
        assert MAT_KHAU_GIA not in xt, "xem trước không hiện mật khẩu"
        # bỏ tick b → chỉ chạy a và c
        trang.bam("document.querySelector('[data-tk-tu-dong=\"b@x.com\"] input')")
        trang.cho("/2 tài khoản/.test(document.getElementById('nut-chay-tu-dong').innerText)", mo_ta="nhãn nút theo số đã tick")
        trang.bam("document.getElementById('nut-chay-tu-dong')")

        # mật khẩu rời trang ngay
        trang.cho("document.getElementById('tien-do-tu-dong')", mo_ta="khung tiến độ")
        assert trang.js("document.getElementById('o-tu-dong').value") == ""
        assert MAT_KHAU_GIA not in trang.js("document.documentElement.outerHTML + JSON.stringify(localStorage) + JSON.stringify(sessionStorage)")

        # theo dõi tới «Đã xong»; chỉ hai tài khoản đã tick có kết quả
        trang.cho("/Đã xong/.test(document.getElementById('tom-tat-tu-dong').innerText)", han=20, mo_ta="lượt xong")
        co = trang.js("[...document.querySelectorAll('[data-ket-qua-tu-dong]')].map(l => l.dataset.ketQuaTuDong).sort()")
        assert co == ["a@x.com", "c@x.com"], co
        assert "Xong" in trang.chu("[data-ket-qua-tu-dong=\"a@x.com\"]")
        goi = app.state.so.dang_chay[MODULE_ID].da_goi_dang_nhap
        assert sorted(goi) == ["a@x.com", "c@x.com"] and "b@x.com" not in goi, goi
