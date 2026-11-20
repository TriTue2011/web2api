"""Hồi quy Chrome NV305 với web/ thật và các đường API giả trên loopback."""

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from gia import MayChuThat
from ui_chung import CO_CHROME, trang, trinh_duyet  # noqa: F401 — fixture

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

MAT_KHAU = "  MatKhauGiaBaoMat305  "
MAT_KHAU_MOI = "MatKhauMoiGiaBaoMat305"
MA_2FA = "123456"
MAT_KHAU_VNC = '<b>&"'


@pytest.fixture
def api_gia():
    trang_thai = {"2fa": False, "step_up": False, "man_hinh": False}
    yeu_cau = []

    def loi(ma, code):
        return JSONResponse({"error": {"code": code, "message": code}}, status_code=ma)

    async def api(req):
        duong = req.url.path
        if req.method == "GET":
            ds = {
                "/api/toi": {"id": "admin-gia", "ten": "Quản trị giả", "vai": "admin",
                             "bat_2fa": trang_thai["2fa"], "csrf": "csrf-gia"},
                "/api/toi/2fa": {"bat": trang_thai["2fa"]},
                "/api/toi/khoa": {"khoa": []},
                "/api/tinh-nang": {},
                "/api/ket-noi": {},
                "/api/nguoi-dung": {"nguoi_dung": []},
                "/api/khoa-khach": {"khoa": []},
                "/api/bao-mat/khoa-dang-nhap": {"khoa": []},
                "/api/trinh-duyet/mat-khau-vnc/trang-thai": {"da_dat": True},
                "/api/trinh-duyet/man-hinh/trang-thai": {
                    "bat": trang_thai["man_hinh"], "dang_chay": trang_thai["man_hinh"], "da_dat": True},
            }
            return JSONResponse(ds[duong]) if duong in ds else loi(404, "khong_tim_thay")

        than = await req.json()
        yeu_cau.append((duong, than))
        if req.headers.get("x-csrf-token") != "csrf-gia":
            return loi(403, "csrf_token_invalid")
        bang_chung = {"ma_2fa": MA_2FA} if trang_thai["2fa"] else {"mat_khau": MAT_KHAU}
        if duong == "/api/toi/mat-khau":
            can = bang_chung if trang_thai["2fa"] else {"mat_khau_cu": MAT_KHAU}
            if than != {**can, "mat_khau_moi": MAT_KHAU_MOI}:
                return loi(400, "invalid_input")
        elif duong == "/api/toi/2fa/bat-dau":
            if than != {"mat_khau": MAT_KHAU}:
                return loi(401, "invalid_credentials")
            return JSONResponse({"ok": True, "bi_mat": "DU-LIEU-GIA"})
        elif duong == "/api/toi/2fa/xac-nhan":
            if than != {"ma": MA_2FA}:
                return loi(401, "invalid_totp")
            trang_thai["2fa"] = True
            return JSONResponse({"ok": True, "ma_khoi_phuc": ["MA-KHOI-PHUC-GIA"]})
        elif duong == "/api/toi/2fa/tat":
            if than != {"mat_khau": MAT_KHAU, "ma": MA_2FA}:
                return loi(401, "invalid_credentials")
            trang_thai["2fa"] = False
        elif duong == "/api/toi/xac-nhan-lai":
            if than != bang_chung:
                return loi(401, "invalid_credentials")
            trang_thai["step_up"] = True
        elif duong == "/api/trinh-duyet/mat-khau-vnc/hien":
            if than != bang_chung:
                return loi(401, "invalid_credentials")
            return JSONResponse({"ok": True, "mat_khau": MAT_KHAU_VNC})
        elif duong == "/api/trinh-duyet/man-hinh/bat-tat":
            if not trang_thai["step_up"]:
                return loi(403, "step_up_required")
            if type(than.get("bat")) is not bool:
                return loi(400, "invalid_input")
            trang_thai["man_hinh"] = than["bat"]
            return JSONResponse({"ok": True, "bat": than["bat"], "dang_chay": than["bat"]})
        else:
            return loi(404, "khong_tim_thay")
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/api/{duong:path}", api, methods=["GET", "POST"]),
                            Mount("/", app=StaticFiles(directory="web"))])
    with MayChuThat(app) as srv:
        yield srv.url, trang_thai, yeu_cau


def mo_bao_mat(trang, url):
    trang.di(f"{url}/He-thong-Bao-mat.dc.html", mo_muc=False)
    trang.cho("window.Khung?.layNguoiDung()?.id === 'admin-gia'")


def bam(trang, selector):
    trang.bam(f"document.querySelector({selector!r})")


def nhap_hop_thoai(trang, bat_2fa):
    selector = "dialog[open] input"
    trang.cho(f"!!document.querySelector({selector!r})")
    assert trang.js(f"document.querySelector({selector!r}).type") == ("text" if bat_2fa else "password")
    trang.go(selector, f"  {MA_2FA}  " if bat_2fa else MAT_KHAU)
    bam(trang, "dialog[open] button:last-of-type")


@pytest.mark.parametrize("bat_2fa", [False, True], ids=["mat-khau-cu", "ma-2fa"])
def test_doi_mat_khau_gui_dung_bang_chung(trang, api_gia, bat_2fa):
    url, trang_thai, yeu_cau = api_gia
    trang_thai["2fa"] = bat_2fa
    mo_bao_mat(trang, url)
    bam(trang, '[data-mo-muc="doi-mat-khau"]')
    assert trang.js("!!document.querySelector('#o-mk-cu')") is not bat_2fa
    if bat_2fa:
        trang.go("#o-doi-mk-ma-2fa", f"  {MA_2FA}  ")
        can = {"ma_2fa": MA_2FA}
    else:
        trang.go("#o-mk-cu", MAT_KHAU)
        can = {"mat_khau_cu": MAT_KHAU}
    trang.go("#o-mk-moi", MAT_KHAU_MOI)
    trang.go("#o-nhap-lai-mk-moi", MAT_KHAU_MOI)
    bam(trang, "#nut-gui-doi-mk")
    trang.cho("!!document.querySelector('#thong-bao-doi-mk, #loi-doi-mk')")
    assert yeu_cau == [("/api/toi/mat-khau", {**can, "mat_khau_moi": MAT_KHAU_MOI})]
    assert trang.chu("#thong-bao-doi-mk")


def test_bat_va_tat_2fa_cap_nhat_nguoi_dung_chung(trang, api_gia):
    url, trang_thai, _ = api_gia
    mo_bao_mat(trang, url)
    bam(trang, '[data-mo-muc="hai-buoc"]')
    bam(trang, '[data-mo-muc="doi-mat-khau"]')
    trang.go("#o-2fa-mk", MAT_KHAU)
    bam(trang, "#bat-dau-2fa")
    trang.cho("!!document.querySelector('#o-2fa-ma')")
    trang.go("#o-2fa-ma", MA_2FA)
    bam(trang, "#xac-nhan-2fa")
    trang.cho("!!document.querySelector('#tat-2fa:not(:disabled)')")
    assert trang_thai["2fa"] is True
    assert trang.js("window.Khung.layNguoiDung().bat_2fa") is True
    assert trang.js("!!document.querySelector('#o-doi-mk-ma-2fa')") is True

    trang.go("#o-2fa-mk2", MAT_KHAU)
    trang.go("#o-2fa-ma2", MA_2FA)
    bam(trang, "#tat-2fa")
    trang.cho("!!document.querySelector('#bat-dau-2fa:not(:disabled)')")
    assert trang_thai["2fa"] is False
    assert trang.js("window.Khung.layNguoiDung().bat_2fa") is False
    assert trang.js("!!document.querySelector('#o-mk-cu')") is True


def test_step_up_doc_lai_loai_bang_chung_truoc_khi_hoi(trang, api_gia):
    url, trang_thai, yeu_cau = api_gia
    mo_bao_mat(trang, url)
    trang.cho("!!document.querySelector('#o-bat-tat-man-hinh:not(:disabled)')")
    # Tài khoản bật 2FA sau khi trang hiện tại đã tải, như khi thao tác ở tab khác.
    assert trang.js("window.Khung.layNguoiDung().bat_2fa") is False
    trang_thai["2fa"] = True
    bam(trang, "#o-bat-tat-man-hinh")
    nhap_hop_thoai(trang, True)
    trang.cho("!!document.querySelector('#tin-bat-tat-man-hinh')")
    assert ("/api/toi/xac-nhan-lai", {"ma_2fa": MA_2FA}) in yeu_cau


@pytest.mark.parametrize("rong,bat_2fa", [(375, True), (1280, False)])
def test_vnc_hien_an_va_bat_tat_qua_step_up(trang, api_gia, rong, bat_2fa):
    url, trang_thai, yeu_cau = api_gia
    trang_thai["2fa"] = bat_2fa
    trang.khung(rong, 800)
    mo_bao_mat(trang, url)
    trang.cho("!!document.querySelector('#nut-hien-mat-khau-vnc:not(:disabled)')")
    assert trang.js("document.querySelector('#o-mat-khau-vnc').value") == ""
    bam(trang, "#nut-hien-mat-khau-vnc")
    nhap_hop_thoai(trang, bat_2fa)
    trang.cho("!!document.querySelector('#mat-khau-vnc-da-hien')")
    assert trang.chu("#mat-khau-vnc-da-hien") == MAT_KHAU_VNC
    assert trang.js("document.querySelector('#mat-khau-vnc-da-hien').childElementCount") == 0
    assert trang.js("document.querySelector('#o-mat-khau-vnc').value") == ""
    bam(trang, "#nut-an-mat-khau-vnc")
    trang.cho("!document.querySelector('#mat-khau-vnc-da-hien')")
    assert not any(duong == "/api/toi/xac-nhan-lai" for duong, _ in yeu_cau)

    trang.cho("!!document.querySelector('#o-bat-tat-man-hinh:not(:disabled)')")
    bam(trang, "#o-bat-tat-man-hinh")
    nhap_hop_thoai(trang, bat_2fa)
    trang.cho("!!document.querySelector('#tin-bat-tat-man-hinh, #loi-bat-tat-man-hinh')")
    can = {"ma_2fa": MA_2FA} if bat_2fa else {"mat_khau": MAT_KHAU}
    assert ("/api/toi/xac-nhan-lai", can) in yeu_cau
    assert trang_thai["man_hinh"] is True

    mo_bao_mat(trang, url)
    trang.cho("!!document.querySelector('#o-bat-tat-man-hinh:checked:not(:disabled)')")
    bam(trang, "#o-bat-tat-man-hinh")
    trang.cho("!!document.querySelector('#o-bat-tat-man-hinh:not(:checked):not(:disabled)')")
    assert trang_thai["man_hinh"] is False
