"""Chrome THẬT: tab riêng của module (vnc-xa «Trình duyệt») nằm TRONG Hệ thống như AI và Module.

Chủ máy 17/10: bấm «Trình duyệt» mà ra trang rời («← Hệ thống») thì sai; phải giữ thanh Hệ thống, trang module
nằm trong khung. Kiểm:
1. (NV263: đổi) Thanh Hệ thống KHÔNG còn mục «Trình duyệt» (module khai an_khoi_thanh); trang vẫn mở bằng đường dẫn trực tiếp.
2. Khung nhúng trang module CÙNG GỐC, trang module dựng xong bên trong khung, KHÔNG hiện «← Hệ thống».
3. Mã module lạ / mã chứa mã độc trong ?m= → không dựng khung, hiện lời giải thích bằng chữ.
4. Khung có sandbox, không có allow-top-navigation (trang module không đổi được trang cha).
5. Header: trang module chỉ cho CÙNG GỐC nhúng (SAMEORIGIN), gốc khác vẫn bị chặn.
"""

from __future__ import annotations

import httpx
import pytest

from test_web2api_vnc_xa_ui import QT, moi_truong  # noqa: F401  (fixture dùng chung)
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401


def _cho_trang_module(trang):
    trang.cho(
        "(document.querySelector('x-dc')||{getAttribute(){}}).getAttribute('data-dc') === 'san-sang'"
        " && (document.querySelector('iframe.khung-module') || document.querySelector('.khong-thay'))",
        mo_ta="trang chứa module dựng xong",
    )


def test_header_trang_module_chi_cho_cung_goc_nhung(moi_truong):
    w, _dv = moi_truong
    r = httpx.get(f"{w.url}/m/vnc-xa/index.html", headers=QT)
    assert r.status_code == 200
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'self'" in csp
    assert "frame-ancestors *" not in csp


@pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")
def test_tab_trinh_duyet_nam_trong_he_thong(trang, moi_truong):
    w, _dv = moi_truong
    dang_nhap_quan_tri(trang, w.url)
    trang.di(f"{w.url}/He-thong-Module.dc.html?m=vnc-xa")
    _cho_trang_module(trang)

    # 1. NV263 (chủ máy bỏ tab «Trình duyệt» khỏi thanh điều hướng): module khai `an_khoi_thanh` nên KHÔNG có mục
    #    trong thanh Hệ thống; trang vẫn mở được bằng đường dẫn trực tiếp (bước 2 dưới đây).
    ds = trang.js("[...document.querySelectorAll('#thanh-tab [data-nav]')].map(a => a.getAttribute('data-nav'))")
    assert "module" in ds and "bao_mat" in ds, ds
    assert "m-vnc-xa" not in ds, ds

    # 2. Khung cùng gốc, trang module dựng xong trong khung, không có «← Hệ thống»
    src = trang.js("document.querySelector('iframe.khung-module').getAttribute('src')")
    assert src.endswith("/m/vnc-xa/index.html") and not src.startswith("javascript:"), src
    trang.cho(
        "(() => { const f = document.querySelector('iframe.khung-module'); const d = f && f.contentDocument;"
        " return !!(d && d.readyState === 'complete' && d.getElementById('ds-ws')); })()",
        mo_ta="trang module dựng xong trong khung",
    )
    assert trang.js(
        "getComputedStyle(document.querySelector('iframe.khung-module').contentDocument.querySelector('.quay-lai')).display"
    ) == "none", "trong khung không hiện «← Hệ thống»"

    # 4. Khung có sandbox, không cho đổi trang cha
    sb = trang.js("document.querySelector('iframe.khung-module').getAttribute('sandbox')")
    assert sb and "allow-top-navigation" not in sb, sb

    # Trang module mở thẳng (không qua khung) vẫn còn «← Hệ thống»
    trang.cdp.goi("Page.navigate", {"url": f"{w.url}/m/vnc-xa/index.html"}, trang.s)
    trang.cho("document.readyState === 'complete' && !!document.getElementById('ds-ws')", mo_ta="trang module mở thẳng")
    assert trang.js("getComputedStyle(document.querySelector('.quay-lai')).display") != "none"


@pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")
@pytest.mark.parametrize("ma", ["khong-co-module-nay", "<img src=x onerror=window.__xss=1>", "../../api/modules", "vnc-xa%00"])
def test_ma_module_la_khong_dung_khung(trang, moi_truong, ma):
    w, _dv = moi_truong
    dang_nhap_quan_tri(trang, w.url)
    trang.di(f"{w.url}/He-thong-Module.dc.html?m={ma}")
    _cho_trang_module(trang)
    assert trang.js("document.querySelector('iframe.khung-module')") is None, "mã lạ không được dựng khung"
    assert "không có trang riêng" in trang.chu(".khong-thay")
    assert trang.js("window.__xss") is None
