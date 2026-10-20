"""Test Chrome THẬT cho hai giao diện AI mới (NV197b «Tạm dừng/Bật lại», NV199b «Thêm nhà cung cấp» theo preset).

Bám khuôn test_web2api_nv144_giao_dien.py (tài khoản đã lưu, hộp thoại xác nhận lại) và
test_web2api_giao_dien.py (module từ xa): gõ từng ô như người thật, phiên web qua cookie (không Bearer) nên
step-up thật sự xảy ra. Tài khoản đã lưu được GIEO bằng Bearer quản trị (được miễn step-up) để lần bấm «Tạm dừng»
đầu tiên chắc chắn phải qua hộp thoại — gieo bằng giao diện thì phiên đã xác nhận lại và không kiểm được đường mới.

Khổ điện thoại ~390px: không tràn ngang, và từng nút nằm TRONG màn hình (dòng tài khoản có overflow:hidden nên
chỉ đo scrollWidth là chưa đủ).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

try:
    from app import so_module
    from app import tuyen
    from app.main import app
    from gia import MayChuThat, OpenAIGia
    from test_web2api_giao_dien import loi_that, o as o_ma
    from test_web2api_nv144_giao_dien import (
        KHOA_QT as KHOA_QT_NV144,
        MODULE_ID,
        _mo_tab_tai_khoan,
        _xac_nhan_lai,
        moi_truong_ui,
    )
    from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture
    CO_UI = True
except ImportError:  # thiếu gói/Chrome: cả tệp tự bỏ qua
    CO_UI = False
    CO_CHROME = False

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="chưa chạy vì không có Chrome")

QT = {"Authorization": f"Bearer {KHOA_QT_NV144}"} if CO_UI else {}
EMAIL_DAI = "nguoi.dung.co.ten.rat.dai.de.kiem.tran.bo.cuc@mien-rat-dai-example.com"
NHAN_DAI = "Nhãn khá dài của tài khoản"
DUONG_PRESET = Path(__file__).resolve().parent.parent / "app" / "tu_xa_preset.json"
KHOA_XA = "sk-ui-nv199b-0000000000000000zzzz"
KHOA_XA_SAI = "sk-ui-nv199b-sai-1111111111111111"


def _preset() -> list[dict]:
    return json.loads(DUONG_PRESET.read_text(encoding="utf-8"))["preset"]


def _bo_route(request, bo_dinh_tuyen, duong: str):
    """Giả «lõi cũ»: gỡ đường API khỏi bộ định tuyến con (app.include_router giữ tham chiếu tới chính danh sách này nên
    phải sửa TẠI CHỖ rồi báo đã đổi) để máy chủ thật trả đúng như bản chưa có tính năng — đường rơi xuống StaticFiles: GET → 404,
    POST → 405. Trả lại nguyên trạng sau test."""
    goc = list(bo_dinh_tuyen.routes)
    bo_dinh_tuyen.routes[:] = [r for r in goc if getattr(r, "path", "") != duong]
    assert len(bo_dinh_tuyen.routes) == len(goc) - 1, f"không gỡ được {duong}"
    bo_dinh_tuyen._mark_routes_changed()  # FastAPI nhớ bảng đường theo số phiên bản: không tăng thì thay đổi bị bỏ qua

    def tra_lai():
        bo_dinh_tuyen.routes[:] = goc
        bo_dinh_tuyen._mark_routes_changed()
    request.addfinalizer(tra_lai)


def _gieo(w, email: str, nhan: str = "") -> str:
    r = httpx.post(f"{w.url}/api/tai-khoan/da-luu", headers=QT,
                   json={"email": email, "mat_khau": "MatKhauGieo-NV197b-1", "nhan": nhan})
    assert r.status_code == 200, r.text
    return r.json()["tai_khoan"]["id"]


def _trang_thai_api(w, id_tk: str) -> str:
    ds = httpx.get(f"{w.url}/api/tai-khoan/da-luu", headers=QT).json()["tai_khoan"]
    return next(t for t in ds if t["id"] == id_tk).get("trang_thai", "")


def _mo_lai_tab_tai_khoan(trang, w):
    """Tải lại trang (phiên cookie còn nguyên) rồi mở lại khối «Tài khoản đã lưu» — KHÔNG đăng nhập lại."""
    trang.di(f"{w.url}/He-thong-AI.dc.html")
    trang.cho("document.getElementById('nut-them')", mo_ta="nút Thêm tài khoản (sau tải lại)")
    trang.bam("document.getElementById('nut-them')")
    trang.cho(f"document.querySelector('[data-chon-module=\"{MODULE_ID}\"]')", mo_ta="chọn module")
    trang.bam(f"document.querySelector('[data-chon-module=\"{MODULE_ID}\"]')")
    trang.cho("document.getElementById('dl-khoi')", mo_ta="khối Tài khoản đã lưu (sau tải lại)")


def _nut_tam_dung(id_tk: str) -> str:
    return f"document.querySelector('[data-dl-tam-dung=\"{id_tk}\"]')"


def _hang(id_tk: str) -> str:
    return f"document.querySelector('[data-tk-da-luu=\"{id_tk}\"]')"


def _hien(expr_phan_tu: str) -> str:
    """Biểu thức JS: phần tử CÓ THẤY thật (không hidden, không display:none)."""
    return f"(() => {{ const e = {expr_phan_tu}; return !!e && e.offsetParent !== null && e.getClientRects().length > 0; }})()"


def _doi_tam_dung(trang, id_tk: str, nhan_cu: str, nhan_moi: str, mk_admin: str | None, trang_thai_moi: str):
    """Bấm nút đổi trạng thái. Hộp thoại xác nhận lại có thể mở (phiên chưa xác nhận) hoặc không (còn hạn step-up)."""
    assert trang.chu(f"[data-dl-tam-dung=\"{id_tk}\"]") == nhan_cu
    trang.bam(_nut_tam_dung(id_tk), mo_ta=f"nút «{nhan_cu}»")
    if mk_admin is not None:
        _xac_nhan_lai(trang, mk_admin)
    trang.cho(f"{_nut_tam_dung(id_tk)}?.innerText === {json.dumps(nhan_moi)}", han=10, mo_ta=f"nút đổi thành «{nhan_moi}»")
    assert trang.js(f"!!document.getElementById('loi-dl')") is False, trang.chu("#loi-dl")
    return trang_thai_moi


# ───────────────────────────── (a) NV197b: Tạm dừng / Bật lại ─────────────────────────────

def test_a_tam_dung_roi_bat_lai_qua_step_up_va_ben_sau_tai_lai(trang, tmp_path, monkeypatch):
    with moi_truong_ui(tmp_path, monkeypatch) as w:
        id_tk = _gieo(w, "nv197b-ui@example.com")
        mk_admin = _mo_tab_tai_khoan(trang, w)
        trang.cho(_hang(id_tk), mo_ta="dòng tài khoản đã gieo")
        # Trạng thái đầu: đang dùng, nút «Tạm dừng» hiện thật.
        assert "Đã tạm dừng" not in trang.chu(f"[data-tk-da-luu=\"{id_tk}\"]")
        assert trang.js(_hien(_nut_tam_dung(id_tk))), "nút «Tạm dừng» phải hiện thật"
        # Phiên web chưa xác nhận lại → hộp thoại step-up PHẢI mở (kiểm đúng đường mới đòi step-up).
        assert trang.chu(f"[data-dl-tam-dung=\"{id_tk}\"]") == "Tạm dừng"
        trang.bam(_nut_tam_dung(id_tk))
        _xac_nhan_lai(trang, mk_admin)
        trang.cho(f"{_hang(id_tk)}?.innerText.includes('Đã tạm dừng')", han=10, mo_ta="trạng thái «Đã tạm dừng»")
        trang.cho(f"{_nut_tam_dung(id_tk)}?.innerText === 'Bật lại'", mo_ta="nút đổi thành «Bật lại»")
        assert trang.js(_hien(_nut_tam_dung(id_tk)))
        assert trang.js("!!document.getElementById('loi-dl')") is False, trang.chu("#loi-dl")
        assert _trang_thai_api(w, id_tk) == "tam_dung"
        # Bền sau tải lại trang.
        _mo_lai_tab_tai_khoan(trang, w)
        trang.cho(f"{_hang(id_tk)}?.innerText.includes('Đã tạm dừng')", mo_ta="vẫn «Đã tạm dừng» sau tải lại")
        assert trang.chu(f"[data-dl-tam-dung=\"{id_tk}\"]") == "Bật lại"
        # Bấm «Bật lại» → bình thường. Hộp thoại có hoặc không tuỳ hạn step-up: chờ «hộp thoại hoặc nhãn đổi».
        trang.bam(_nut_tam_dung(id_tk))
        trang.cho(f"!!document.querySelector('dialog[open]') || {_nut_tam_dung(id_tk)}?.innerText === 'Tạm dừng'",
                  mo_ta="hộp thoại xác nhận hoặc nhãn đổi")
        if trang.js("!!document.querySelector('dialog[open]')"):
            _xac_nhan_lai(trang, mk_admin)
        trang.cho(f"{_nut_tam_dung(id_tk)}?.innerText === 'Tạm dừng'", han=10, mo_ta="nút trở lại «Tạm dừng»")
        assert "Đã tạm dừng" not in trang.chu(f"[data-tk-da-luu=\"{id_tk}\"]")
        assert "đã lưu" in trang.chu(f"[data-tk-da-luu=\"{id_tk}\"]")
        assert trang.js("!!document.getElementById('loi-dl')") is False, trang.chu("#loi-dl")
        assert _trang_thai_api(w, id_tk) == ""
        _mo_lai_tab_tai_khoan(trang, w)
        trang.cho(_hang(id_tk), mo_ta="dòng sau tải lại lần hai")
        assert trang.chu(f"[data-dl-tam-dung=\"{id_tk}\"]") == "Tạm dừng"
        assert "Đã tạm dừng" not in trang.chu(f"[data-tk-da-luu=\"{id_tk}\"]")


def test_a_huy_hop_thoai_xac_nhan_thi_khong_doi_trang_thai(trang, tmp_path, monkeypatch):
    with moi_truong_ui(tmp_path, monkeypatch) as w:
        id_tk = _gieo(w, "nv197b-huy@example.com")
        _mo_tab_tai_khoan(trang, w)
        trang.cho(_hang(id_tk), mo_ta="dòng tài khoản")
        trang.bam(_nut_tam_dung(id_tk))
        trang.cho("!!document.querySelector('dialog[open] input[type=password]')", mo_ta="hộp thoại xác nhận lại")
        trang.bam("[...document.querySelectorAll('dialog[open] button')].find((b) => b.textContent === 'Huỷ')")
        trang.cho("!document.querySelector('dialog[open]')", mo_ta="hộp thoại đóng")
        # Báo lỗi rõ cho người dùng (không im lặng), nhãn không đổi, máy chủ không đổi.
        trang.cho("!!document.getElementById('loi-dl')", mo_ta="thông báo lỗi sau khi huỷ xác nhận")
        assert trang.chu(f"[data-dl-tam-dung=\"{id_tk}\"]") == "Tạm dừng"
        assert "Đã tạm dừng" not in trang.chu(f"[data-tk-da-luu=\"{id_tk}\"]")
        assert _trang_thai_api(w, id_tk) == ""


def test_a_loi_cu_khong_co_duong_tam_dung_thi_an_nut(trang, tmp_path, monkeypatch, request):
    """Lõi cũ chưa có đường «tam-dung»: nút tự ẩn, không báo lỗi. Phiên web vẫn qua step-up trước (đường nằm dưới
    /api/tai-khoan/da-luu nên bộ chặn step-up chạy trước định tuyến) nên người thật phải gõ mật khẩu rồi mới biết."""
    _bo_route(request, tuyen.tai_khoan, "/api/tai-khoan/da-luu/{id}/tam-dung")
    with moi_truong_ui(tmp_path, monkeypatch) as w:
        id_tk = _gieo(w, "nv197b-cu@example.com")
        mk_admin = _mo_tab_tai_khoan(trang, w)
        trang.cho(_hang(id_tk), mo_ta="dòng tài khoản")
        assert trang.js(_hien(_nut_tam_dung(id_tk))), "chưa biết lõi cũ → nút còn hiện"
        trang.bam(_nut_tam_dung(id_tk))
        trang.cho("!!document.querySelector('dialog[open]') || " + _nut_tam_dung(id_tk) + " === null || !" + _hien(_nut_tam_dung(id_tk)),
                  mo_ta="hộp thoại xác nhận hoặc nút đã ẩn")
        if trang.js("!!document.querySelector('dialog[open]')"):
            _xac_nhan_lai(trang, mk_admin)
        trang.cho(f"!{_hien(_nut_tam_dung(id_tk))}", mo_ta="nút «Tạm dừng» ẩn khi lõi cũ chưa có đường (404/405 không mã lỗi)")
        assert trang.js("!!document.getElementById('loi-dl')") is False, "ẩn lặng lẽ, không báo lỗi ầm ĩ"
        # Các nút khác của dòng vẫn dùng được.
        assert trang.js(_hien(f"document.querySelector('[data-dl-dang-nhap=\"{id_tk}\"]')"))
        assert trang.js(_hien(f"document.querySelector('[data-dl-xoa=\"{id_tk}\"]')"))


@pytest.mark.parametrize("tam_dung", [False, True], ids=["dang_dung", "tam_dung"])
def test_a_dien_thoai_390_dong_tai_khoan_khong_tran(trang, tmp_path, monkeypatch, tam_dung):
    with moi_truong_ui(tmp_path, monkeypatch) as w:
        id_tk = _gieo(w, EMAIL_DAI, NHAN_DAI)
        if tam_dung:
            r = httpx.post(f"{w.url}/api/tai-khoan/da-luu/{id_tk}/tam-dung", headers=QT, json={"tam_dung": True})
            assert r.status_code == 200, r.text
        trang.khung(390, 844, dien_thoai=True)
        _mo_tab_tai_khoan(trang, w)
        trang.cho(_hang(id_tk), mo_ta="dòng tài khoản")
        # Chờ bố cục ổn định, rồi đo ở CẢ hai trạng thái nhãn nút xoá (dài nhất khi «Bấm lần nữa để xoá»).
        for buoc in ("thuong", "xoa_hoi_lai"):
            if buoc == "xoa_hoi_lai":
                trang.bam(f"document.querySelector('[data-dl-xoa=\"{id_tk}\"]')")
                trang.cho(f"document.querySelector('[data-dl-xoa=\"{id_tk}\"]').innerText.includes('lần nữa')", mo_ta="nút xoá hỏi lại")
            do = trang.js(f"""(() => {{
                const hang = {_hang(id_tk)};
                const rong = window.innerWidth;
                const nut = [...hang.querySelectorAll('button')].map((b) => {{ const r = b.getBoundingClientRect();
                    return {{chu: b.innerText, trai: Math.round(r.left), phai: Math.round(r.right), cao: Math.round(r.height)}}; }});
                const hr = hang.getBoundingClientRect();
                return {{rong, cuon: document.documentElement.scrollWidth, hangPhai: Math.round(hr.right), nut}};
            }})()""")
            trang.chup(f"dl-390-{'tam-dung' if tam_dung else 'dang-dung'}-{buoc}")
            assert do["cuon"] <= do["rong"], f"[{buoc}] trang tràn ngang: {do}"
            assert do["hangPhai"] <= do["rong"], f"[{buoc}] dòng tràn khỏi màn hình: {do}"
            assert len(do["nut"]) == 3, do
            for n in do["nut"]:
                assert 0 <= n["trai"] and n["phai"] <= do["rong"], f"[{buoc}] nút «{n['chu']}» bị tràn/cắt: {do}"
                assert n["cao"] >= 44, f"[{buoc}] nút «{n['chu']}» thấp hơn 44px: {do}"
            # Chữ trạng thái không bị cắt mất (nowrap + không co).
            tt = trang.js(f"(() => {{ const s = [...{_hang(id_tk)}.querySelectorAll('span')].find((x) => /đã lưu|Đã tạm dừng/.test(x.innerText)); "
                          "if (!s) return null; const r = s.getBoundingClientRect(); return {phai: Math.round(r.right), rong: Math.round(r.width), cuon: s.scrollWidth}; })()")
            assert tt and tt["phai"] <= do["rong"] and tt["cuon"] <= tt["rong"] + 1, f"[{buoc}] chữ trạng thái bị cắt: {tt}"


# ───────────────────────────── (b)(c) NV199b: Thêm nhà cung cấp theo preset ─────────────────────────────

def _mo_o_tu_xa(trang, w):
    dang_nhap_quan_tri(trang, w.url)
    trang.di(w.url + "/He-thong.dc.html")
    trang.js("window.__khongTaiLai = 1")
    trang.bam("document.getElementById('o-them')")
    trang.cho("document.getElementById('tu-xa')", mo_ta="khối Từ xa")


def _chon_preset(trang, ma: str):
    """Người dùng chọn một dòng trong <select>: đặt value rồi phát sự kiện change (như nv144 với «dl-chon»)."""
    trang.js(f"document.getElementById('tx-preset').value = {json.dumps(ma)}")
    trang.js("document.getElementById('tx-preset').dispatchEvent(new Event('change', {bubbles: true}))")


def _go_de(trang, sel: str, chu: str):
    """Thay nội dung ô đã có sẵn giá trị: chọn hết rồi gõ đè (Input.insertText thay vùng chọn)."""
    trang.js(f"(() => {{ const e = document.querySelector({json.dumps(sel)}); e.focus(); e.select(); }})()")
    trang.go(sel, chu)


def _o_khoa() -> str:
    return "#tu-xa input[type=password]"


def _khoa_ro_va_khong_lot(trang, *khoa: str):
    assert trang.js(f"document.querySelector({json.dumps(_o_khoa())}).value") == "", "ô khoá phải rỗng"
    html = trang.js("document.documentElement.outerHTML")
    for k in khoa:
        assert k not in html, "khoá còn trong DOM"
        assert not any(k in x for x in trang.state_thanh_phan()), "khoá còn trong state"
        assert k not in trang.js("document.body.innerText")


def test_b_chon_preset_dien_san_dung_va_hien_ghi_chu(trang, tmp_path, monkeypatch):
    preset = _preset()
    with loi_that(tmp_path, monkeypatch) as w:
        _mo_o_tu_xa(trang, w)
        trang.cho("document.getElementById('tx-preset')", mo_ta="ô chọn preset")
        # Chọn preset: «Tuỳ chỉnh» đứng đầu rồi tới từng nhà theo tệp preset.
        ten_opt = trang.js("[...document.querySelectorAll('#tx-preset option')].map((x) => x.textContent.trim())")
        assert ten_opt == ["Tuỳ chỉnh tương thích OpenAI"] + [p["ten"] for p in preset]
        assert trang.js("document.getElementById('tx-preset').value") == ""
        for p in preset:
            _chon_preset(trang, p["id"])
            trang.cho(f"document.getElementById('tx-id').value === {json.dumps(p['id_goi_y'])}", mo_ta=f"điền sẵn {p['id']}")
            assert trang.js("document.getElementById('tx-ten').value") == p["ten_goi_y"]
            assert trang.js("document.getElementById('tx-tien-to').value") == p["tien_to_goi_y"]
            assert trang.js("document.getElementById('tx-url').value") == p["base_url_mac_dinh"]
            assert trang.js("document.getElementById('tx-preset').value") == p["id"], "ô chọn giữ đúng preset đã chọn"
            ghi_chu = trang.js("document.querySelector('#tu-xa [role=note]')?.innerText || ''")
            assert ghi_chu.strip() == p["ghi_chu_gioi_han"].strip(), f"{p['id']}: ghi chú sai: {ghi_chu!r}"
            tk = next(t for t in p["truong"] if t["khoa"] == "api_key")
            tu_xa = trang.chu("#tu-xa")
            assert tk["mo_ta"] in tu_xa, f"{p['id']}: thiếu cách lấy khoá"
            assert tk["nhan"] in tu_xa, f"{p['id']}: thiếu nhãn ô khoá"
            lk = trang.js("document.querySelector('#tu-xa a[target=_blank]')?.getAttribute('href')")
            assert lk == tk["lien_ket"], f"{p['id']}: liên kết trang chính thức sai: {lk}"
            assert trang.js("document.querySelector('#tu-xa a[target=_blank]').getAttribute('rel')") == "noopener"
            assert trang.js(_hien("document.querySelector('#tu-xa [role=note]')"))
        # Quay về «Tuỳ chỉnh»: ghi chú và hướng dẫn khoá biến mất, ô trống.
        _chon_preset(trang, "")
        trang.cho("!document.querySelector('#tu-xa [role=note]')", mo_ta="ghi chú biến mất khi về Tuỳ chỉnh")
        assert trang.js("document.getElementById('tx-preset').value") == ""
        assert trang.js("!!document.querySelector('#tu-xa a[target=_blank]')") is False
        assert trang.js("window.__khongTaiLai") == 1


def test_b_kiem_tra_dat_khoa_sai_dia_chi_sai_va_o_khoa_rong(trang, tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    gia = OpenAIGia(khoa=KHOA_XA)
    with MayChuThat(gia.app) as s_o, loi_that(tmp_path, monkeypatch) as w:
        _mo_o_tu_xa(trang, w)
        trang.cho("document.getElementById('tx-preset')", mo_ta="ô chọn preset")
        _chon_preset(trang, "custom_openai")
        trang.cho("document.getElementById('tx-id').value === 'ollama'", mo_ta="điền sẵn preset tuỳ chỉnh")
        _go_de(trang, "#tx-url", s_o.url + "/v1")
        # Ca 1: khoá đúng → đạt.
        trang.go(_o_khoa(), KHOA_XA)
        trang.bam("[...document.querySelectorAll('#tu-xa button')].find((b) => b.innerText === 'Kiểm tra')", mo_ta="nút Kiểm tra")
        trang.cho("(document.querySelector('#tu-xa p.bao-tot[role=status]')?.innerText || '').startsWith('Kết nối được')", han=15,
                  mo_ta="kết quả đạt")
        kq = trang.chu("#tu-xa p.bao-tot[role=status]")
        assert "1 model" in kq, kq
        _khoa_ro_va_khong_lot(trang, KHOA_XA)
        assert KHOA_XA in str(gia.nhat_ky[-1]["auth"]), "nhà giả phải nhận đúng khoá đã gõ"
        # Ca 2: khoá sai → «Chưa dùng được», nói rõ chuyện khoá, KHÔNG nói chuyện địa chỉ.
        trang.go(_o_khoa(), KHOA_XA_SAI)
        trang.bam("[...document.querySelectorAll('#tu-xa button')].find((b) => b.innerText === 'Kiểm tra')")
        trang.cho("(document.querySelector('#tu-xa p.bao-loi[role=status]')?.innerText || '').startsWith('Chưa dùng được')", han=15,
                  mo_ta="kết quả khoá sai")
        loi_khoa = trang.chu("#tu-xa p.bao-loi[role=status]")
        assert "khoá" in loi_khoa.lower(), loi_khoa
        assert KHOA_XA_SAI not in loi_khoa and KHOA_XA not in loi_khoa
        assert trang.js("document.querySelector('#tu-xa p.bao-tot[role=status]')") is None, "kết quả đạt cũ phải biến mất"
        _khoa_ro_va_khong_lot(trang, KHOA_XA, KHOA_XA_SAI)
        # Ca 3: địa chỉ không kết nối được → báo chuyện địa chỉ, khác lời của ca khoá.
        _go_de(trang, "#tx-url", "http://127.0.0.1:1/v1")
        trang.go(_o_khoa(), KHOA_XA)
        trang.bam("[...document.querySelectorAll('#tu-xa button')].find((b) => b.innerText === 'Kiểm tra')")
        trang.cho("(document.querySelector('#tu-xa p.bao-loi[role=status]')?.innerText || '') !== " + json.dumps(loi_khoa)
                  + " && !!document.querySelector('#tu-xa p.bao-loi[role=status]')", han=15, mo_ta="kết quả địa chỉ sai")
        loi_dc = trang.chu("#tu-xa p.bao-loi[role=status]")
        assert loi_dc.startswith("Chưa dùng được") and loi_dc != loi_khoa, loi_dc
        assert "địa chỉ" in loi_dc.lower() or "kết nối" in loi_dc.lower(), loi_dc
        _khoa_ro_va_khong_lot(trang, KHOA_XA)
        # Thiếu địa chỉ → nhắc tại chỗ, không gọi máy chủ.
        _go_de(trang, "#tx-url", " ")
        trang.js("document.getElementById('tx-url').value = ''; document.getElementById('tx-url').dispatchEvent(new Event('input', {bubbles: true}))")
        trang.bam("[...document.querySelectorAll('#tu-xa button')].find((b) => b.innerText === 'Kiểm tra')")
        trang.cho("(document.querySelector('#tu-xa p.bao-loi[role=status]')?.innerText || '').startsWith('Nhập địa chỉ gốc')",
                  mo_ta="nhắc nhập địa chỉ")
        assert trang.js("window.__khongTaiLai") == 1


def test_b_kiem_tra_roi_dang_ky_nha_hien_trong_danh_sach(trang, tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    p = next(x for x in _preset() if x["id"] == "nvidia_nim")
    gia = OpenAIGia(khoa=KHOA_XA)
    with MayChuThat(gia.app) as s_o, loi_that(tmp_path, monkeypatch) as w:
        _mo_o_tu_xa(trang, w)
        trang.cho("document.getElementById('tx-preset')", mo_ta="ô chọn preset")
        _chon_preset(trang, p["id"])
        trang.cho(f"document.getElementById('tx-id').value === {json.dumps(p['id_goi_y'])}", mo_ta="điền sẵn")
        _go_de(trang, "#tx-url", s_o.url + "/v1")
        trang.go(_o_khoa(), KHOA_XA)
        trang.bam("[...document.querySelectorAll('#tu-xa button')].find((b) => b.innerText === 'Kiểm tra')")
        trang.cho("(document.querySelector('#tu-xa p.bao-tot[role=status]')?.innerText || '').startsWith('Kết nối được')", han=15,
                  mo_ta="kiểm tra đạt")
        _khoa_ro_va_khong_lot(trang, KHOA_XA)
        # Ô khoá đã xoá theo thiết kế → gõ lại rồi mới đăng ký.
        trang.go(_o_khoa(), KHOA_XA)
        trang.bam("document.querySelector('#tu-xa button[type=submit]')")
        trang.cho(o_ma(p["id_goi_y"]) + "?.dataset.tt === 'dang-chay'", han=15, mo_ta="nhà cung cấp hiện trong danh sách")
        trang.cho("(document.querySelector('#tu-xa p.bao-tot[role=status]')?.innerText || '').startsWith('Đã đăng ký')", han=15,
                  mo_ta="thông báo đã đăng ký")
        _khoa_ro_va_khong_lot(trang, KHOA_XA)
        assert trang.js("window.__khongTaiLai") == 1
        # Sau đăng ký, biểu mẫu sạch: không còn kết quả kiểm tra cũ làm người dùng hiểu lầm.
        assert trang.js("document.getElementById('tx-id').value") == ""
        # Bền: tải lại trang vẫn thấy nhà đó.
        trang.di(w.url + "/He-thong.dc.html")
        trang.cho(o_ma(p["id_goi_y"]) + "?.dataset.tt === 'dang-chay'", han=15, mo_ta="nhà vẫn có sau tải lại")


def test_c_loi_cu_khong_co_duong_preset_thi_an_o_chon(trang, tmp_path, monkeypatch, request):
    _bo_route(request, tuyen.modules, "/api/modules/tu-xa/preset")
    with loi_that(tmp_path, monkeypatch) as w:
        _mo_o_tu_xa(trang, w)
        trang.cho("document.getElementById('tx-id')", mo_ta="biểu mẫu thủ công")
        # Cho trang đủ thời gian nhận 404 của đường preset rồi mới kết luận «ẩn».
        trang.cho("performance.getEntriesByType('resource').some((r) => r.name.includes('api/modules/tu-xa/preset'))",
                  mo_ta="trang đã hỏi đường preset")
        assert trang.js("document.getElementById('tx-preset')") is None, "lõi cũ: ô chọn preset phải ẩn"
        assert "Thêm nhà cung cấp" not in trang.chu("#tu-xa")
        assert trang.js("document.querySelector('#tu-xa [role=note]')") is None
        # Biểu mẫu thủ công và nút «Kiểm tra» vẫn dùng được.
        for sel in ("#tx-id", "#tx-ten", "#tx-tien-to", "#tx-url", "#tx-model"):
            assert trang.js(_hien(f"document.querySelector({json.dumps(sel)})")), sel
        assert trang.js(_hien("[...document.querySelectorAll('#tu-xa button')].find((b) => b.innerText === 'Kiểm tra')"))
        assert trang.js(_hien("document.querySelector('#tu-xa button[type=submit]')"))


def test_b_dien_thoai_390_o_tu_xa_khong_tran(trang, tmp_path, monkeypatch):
    preset = _preset()
    dai_nhat = max(preset, key=lambda p: len(p["ghi_chu_gioi_han"]))
    with loi_that(tmp_path, monkeypatch) as w:
        trang.khung(390, 844, dien_thoai=True)
        _mo_o_tu_xa(trang, w)
        trang.cho("document.getElementById('tx-preset')", mo_ta="ô chọn preset")
        _chon_preset(trang, dai_nhat["id"])
        trang.cho(f"document.getElementById('tx-id').value === {json.dumps(dai_nhat['id_goi_y'])}", mo_ta="điền sẵn")
        trang.js("document.getElementById('tu-xa').scrollIntoView()")
        do = trang.js("""(() => {
            const rong = window.innerWidth;
            const ra = (e) => { const r = e.getBoundingClientRect(); return {trai: Math.round(r.left), phai: Math.round(r.right), cao: Math.round(r.height)}; };
            const ds = [...document.querySelectorAll('#tu-xa select, #tu-xa input, #tu-xa button, #tu-xa [role=note], #tu-xa a')]
              .filter((e) => e.offsetParent !== null).map((e) => Object.assign({ten: e.id || e.tagName + ':' + (e.innerText || '').slice(0, 20)}, ra(e), {cuon: e.scrollWidth, rong: e.clientWidth}));
            return {rong, cuon: document.documentElement.scrollWidth, ds};
        })()""")
        trang.chup("tx-390")
        assert do["cuon"] <= do["rong"], f"trang tràn ngang: {do['cuon']} > {do['rong']}"
        for e in do["ds"]:
            assert 0 <= e["trai"] and e["phai"] <= do["rong"], f"{e['ten']} tràn khỏi màn hình: {e}"
            if e["ten"] == "tx-preset":  # ô MỚI của NV199b tự đặt min-height 44; ô nhập/nút khác theo CSS chung (40px) có từ trước
                assert e["cao"] >= 44, f"{e['ten']} thấp hơn 44px: {e}"
        sel = next(e for e in do["ds"] if e["ten"] == "tx-preset")
        assert sel["cuon"] <= sel["rong"] + 1
