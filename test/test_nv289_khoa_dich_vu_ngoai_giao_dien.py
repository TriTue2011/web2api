"""NV289 — khối «Khoá cho dịch vụ ngoài gọi vào» ở Bảo mật & người dùng, bằng Chrome THẬT + LÕI THẬT.

Module `youtube` thật (khai `duong_module`, không có trường khoá) được đặt vào thư mục module của lõi; người dùng:
chọn module → w2a TỰ sinh khoá (hiện MỘT lần) → khoá gọi được đúng `/m/youtube/api/integration/*` (200) → đổi
module được phép (khoá về 403) → thu hồi (khoá về 401). Không có ô nhập khoá; không có khối `khoa-module-youtube`
của NV281 (tránh trùng id).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture
from test_web2api_giao_dien import loi_that, lien_ket, KHOA_QT  # noqa: F401

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

GOC = Path(__file__).resolve().parent.parent
HD = "/m/youtube/api/integration/health"


def _tep_youtube() -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in (GOC / "modules" / "youtube").iterdir()
            if p.is_file() and p.suffix in (".py", ".json")}


def test_tao_khoa_chon_module_dung_thu_hoi(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, modules={"youtube": _tep_youtube()}) as w:
        lien_ket(w, "youtube")            # không có trường cấu hình: cài là chạy
        tn = httpx.get(f"{w.url}/api/tinh-nang", headers={"Authorization": f"Bearer {KHOA_QT}"}).json()
        assert next(m for m in tn["modules"] if m["id"] == "youtube").get("duong_ngoai") is True
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-Bao-mat.dc.html#khoa-dich-vu-ngoai")
        trang.cho("document.querySelector('#khoa-dich-vu-ngoai [data-dvn-module=\"youtube\"]')", han=20,
                  mo_ta="chip module trong khối khoá cho dịch vụ ngoài")
        # Không có ô nhập khoá, không có khối khoá-nhập-tay của NV281 cho module này.
        assert trang.js("document.getElementById('khoa-module-youtube')") is None
        assert trang.js("document.querySelectorAll('#khoa-dich-vu-ngoai input[type=password], "
                        "#khoa-dich-vu-ngoai textarea').length") == 0
        # Tạo khi chưa chọn module → báo lỗi, chưa có khoá nào.
        trang.bam("document.getElementById('dvn-nut-tao')")
        trang.cho("document.getElementById('dvn-loi-tao')", mo_ta="lỗi chưa chọn module")
        assert httpx.get(f"{w.url}{HD}").status_code == 401
        # Chọn module rồi tạo → khoá w2a_… hiện một lần.
        trang.bam("document.querySelector('[data-dvn-module=\"youtube\"]')")
        trang.go("#dvn-nhan", "Loa phòng khách")
        trang.bam("document.getElementById('dvn-nut-tao')")
        khoa = trang.cho("(document.getElementById('dvn-khoa-moi-gia-tri')||{}).innerText", han=15,
                         mo_ta="khoá vừa tạo hiện ra")
        assert khoa.startswith("w2a_")
        h = {"Authorization": f"Bearer {khoa}"}
        r = httpx.get(f"{w.url}{HD}", headers=h)
        assert r.status_code == 200 and r.json()["status"] == "ok", r.text
        # Đóng khung: khoá không còn trong DOM; danh sách có đúng một khoá với chip youtube đang chọn.
        trang.bam("document.querySelector('#dvn-khoa-moi button.nut-nho')")
        trang.cho("!document.getElementById('dvn-khoa-moi')", mo_ta="khung khoá đã đóng")
        assert khoa not in trang.js("document.documentElement.outerHTML")
        assert not any(khoa in s for s in trang.state_thanh_phan())
        trang.cho("document.querySelectorAll('#dvn-ds-khoa li').length === 1", mo_ta="một khoá trong danh sách")
        assert trang.js("document.querySelector('#dvn-ds-khoa [data-dvn-khoa-module=\"youtube\"]')"
                        ".getAttribute('aria-pressed')") == "true"
        # Bỏ chọn module cuối cùng → báo cần ít nhất một module, khoá vẫn dùng được.
        trang.bam("document.querySelector('#dvn-ds-khoa [data-dvn-khoa-module=\"youtube\"]')")
        trang.cho("document.getElementById('dvn-loi-ds')", mo_ta="cần ít nhất một module")
        assert httpx.get(f"{w.url}{HD}", headers=h).status_code == 200
        # Thu hồi (hai bước) → khoá cũ 401, danh sách rỗng.
        trang.bam("document.querySelector('[data-hd=\"dvn-thu-hoi\"]')")
        trang.bam("document.querySelector('[data-hd=\"dvn-xac-nhan-thu-hoi\"]')")
        trang.cho("document.querySelectorAll('#dvn-ds-khoa li').length === 0", mo_ta="đã thu hồi")
        assert httpx.get(f"{w.url}{HD}", headers=h).status_code == 401
        # Ghi nhớ: khoá của tôi (thu hồi) cũng không vào log trang.
        assert json.loads(httpx.get(f"{w.url}/api/khoa-khach",
                                    headers={"Authorization": f"Bearer {KHOA_QT}"}).text)["khoa"] == []
