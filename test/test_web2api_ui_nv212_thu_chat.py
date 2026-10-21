"""Test Chrome THẬT cho nút «Thử chat» (NV212) ở Hệ thống → AI → Nhà cung cấp (Provider) → tab Tài khoản.

Bám khuôn test_web2api_ui_nv197b_199b.py: lõi thật (uvicorn) + Chrome thật qua CDP; nhà cung cấp là máy chủ GIẢ
(OpenAIGia) chạy HTTP thật, đăng ký qua đường quản trị POST /api/modules/tu-xa (cùng đường ô «Từ xa» gọi), người dùng
đăng nhập bằng giao diện (phiên cookie) rồi bấm CHUỘT THẬT (Input.dispatchMouseEvent) vào nút — đi qua kiểm tra điểm bấm
của trình duyệt, nên nút bị đè/không thấy thì test hỏng.

Bốn ý chứng minh:
  1. nút «Thử chat» thấy được, đứng cạnh «Làm mới»; chưa bấm thì dòng kết quả ghi «chưa thử chat»;
  2. bấm → nút «Đang thử…» và không bấm chồng được; xong hiện ĐẠT + độ trễ (ms) + câu trả lời ngắn; nhà giả nhận đúng MỘT
     yêu cầu chat nhỏ;
  3. nhà giả trả 401/429/500 → dòng kết quả có mã lỗi tiếng Việt dễ hiểu, KHÔNG lộ khoá hay thân lỗi thô trong DOM/state;
  4. khổ điện thoại 390px: nút trong màn hình, cao ≥ 44px, trang không tràn ngang.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading

import httpx
import pytest
from starlette.responses import JSONResponse

try:
    from gia import MayChuThat, OpenAIGia
    from test_web2api_giao_dien import KHOA_QT, bam_chuot, loi_that
    from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture
except ImportError:  # thiếu gói/Chrome: cả tệp tự bỏ qua
    CO_CHROME = False

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="chưa chạy vì không có Chrome")

QT = {"Authorization": f"Bearer {KHOA_QT}"} if CO_CHROME else {}
KHOA_XA = "sk-ui-nv212-khoa-bi-mat-0000000000000zzzz"
THAN_LOI_THO = "THAN-LOI-THO-NV212-khong-duoc-hien-ra"
ID_NHA = "nha-nv212"
TIEN_TO = "nv212"
TEN_NHA = "Nhà giả NV212"


@pytest.fixture(autouse=True)
def _dat_lai_giu_nhip_thu_chat():
    """Lõi chạy cùng tiến trình test: sổ «lần thử cuối» (giãn cách chống bấm dồn 5 giây, theo id module) là biến toàn cục
    nên lần thử của test trước làm test sau gặp «Đang thử dồn». Xoá sổ giữa các test; giãn cách mặc định giữ nguyên."""
    if CO_CHROME:
        from app import tuyen
        tuyen._THU_CHAT_LAN_CUOI.clear()
        tuyen._THU_CHAT_KHOA.clear()
    yield


class NhaGia(OpenAIGia):
    """OpenAIGia có kịch bản cho /v1/chat/completions: `ma` (200 = đạt), `giu` (Event: chưa set thì giữ yêu cầu lại,
    để test quan sát trạng thái «Đang thử…»). Thân lỗi cố ý NHẮC LẠI khoá và một dấu hiệu thô để kiểm việc che."""

    def __init__(self, khoa: str):
        super().__init__(khoa=khoa)
        self.ma = 200
        self.giu: threading.Event | None = None
        self.da_nhan = threading.Event()

    async def chat(self, req):
        than = await req.json()
        self.nhat_ky.append({"duong": "chat", "auth": req.headers.get("authorization"), "than": than})
        self.da_nhan.set()
        while self.giu is not None and not self.giu.is_set():
            await asyncio.sleep(0.02)
        if self.ma != 200:
            return JSONResponse({"error": {"message": f"{THAN_LOI_THO} khoá {self.khoa}", "type": "x"}}, status_code=self.ma)
        return JSONResponse({"object": "chat.completion", "model": than["model"],
                             "choices": [{"index": 0, "message": {"role": "assistant", "content": "từ xa"}}]})

    def so_chat(self) -> int:
        return sum(1 for x in self.nhat_ky if x["duong"] == "chat")


def _dang_ky_nha(w, gia_url: str):
    r = httpx.post(f"{w.url}/api/modules/tu-xa", headers=QT, json={
        "id": ID_NHA, "ten": TEN_NHA, "tien_to": TIEN_TO, "base_url": gia_url + "/v1", "api_key": KHOA_XA})
    assert r.status_code == 200, r.text


def _mo_hang_tai_khoan(trang, w):
    """Đăng nhập bằng giao diện → Hệ thống → AI → Provider → tab «Tài khoản» → mở dòng khoá đầu tiên."""
    dang_nhap_quan_tri(trang, w.url)
    trang.di(w.url + "/He-thong-AI.dc.html#provider")
    trang.cho("document.querySelector('[data-pv-tab=tk]')", han=15, mo_ta="tab Tài khoản của Provider")
    trang.bam("document.querySelector('[data-pv-tab=tk]')")
    trang.cho("document.querySelector('[data-khoa-pv=\"0\"] button')", han=15, mo_ta="dòng khoá của nhà cung cấp")
    trang.bam("document.querySelector('[data-khoa-pv=\"0\"] button')")
    trang.cho("document.querySelector('[data-khoa-pv=\"0\"] [data-hd=thu-chat]')", mo_ta="nút Thử chat sau khi mở dòng")


NUT = "document.querySelector('[data-khoa-pv=\"0\"] [data-hd=thu-chat]')"
NUT_LAM_MOI = "document.querySelector('[data-khoa-pv=\"0\"] [data-hd=lam-moi]')"
DONG_KQ = "[data-khoa-pv=\"0\"] [data-kv=thu-chat]"


def _hien(expr: str) -> str:
    return f"(() => {{ const e = {expr}; return !!e && e.offsetParent !== null && e.getClientRects().length > 0; }})()"


def _khong_lo(trang, *chuoi: str):
    """Khoá và thân lỗi thô không có mặt ở HTML, chữ hiển thị lẫn state của thành phần."""
    html = trang.js("document.documentElement.outerHTML")
    chu = trang.js("document.body.innerText")
    state = trang.state_thanh_phan()
    for c in chuoi:
        assert c not in html, f"«{c}» lộ trong DOM"
        assert c not in chu, f"«{c}» lộ trong chữ hiển thị"
        assert not any(c in s for s in state), f"«{c}» lộ trong state của trang"


def test_a_nut_thu_chat_canh_lam_moi_va_chua_thu_chat(trang, tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    gia = NhaGia(KHOA_XA)
    with MayChuThat(gia.app) as s_o, loi_that(tmp_path, monkeypatch) as w:
        _dang_ky_nha(w, s_o.url)
        _mo_hang_tai_khoan(trang, w)
        assert trang.js(_hien(NUT)), "nút «Thử chat» phải nhìn thấy được"
        assert trang.js(_hien(NUT_LAM_MOI)), "nút «Làm mới» phải còn"
        assert trang.chu("[data-khoa-pv=\"0\"] [data-hd=thu-chat]") == "Thử chat"
        # Cạnh «Làm mới»: cùng hàng, liền kề trong khối nút.
        ten_nut = trang.js("[...document.querySelector('[data-khoa-pv=\"0\"] [data-hd=lam-moi]').parentElement.children]"
                           ".map((b) => b.dataset.hd)")
        i = ten_nut.index("lam-moi")
        assert ten_nut[i + 1] == "thu-chat", f"«Thử chat» phải liền sau «Làm mới»: {ten_nut}"
        hai = trang.js(f"[{NUT_LAM_MOI}, {NUT}].map((e) => {{ const r = e.getBoundingClientRect(); return [Math.round(r.top), Math.round(r.bottom)]; }})")
        assert abs(hai[0][0] - hai[1][0]) <= 2 and abs(hai[0][1] - hai[1][1]) <= 2, f"hai nút không cùng hàng: {hai}"
        # Chưa bấm: «chưa thử chat», và nhà giả chưa nhận yêu cầu chat nào.
        assert trang.chu(DONG_KQ) == "chưa thử chat"
        assert gia.so_chat() == 0


def test_b_bam_thu_chat_dang_thu_khong_bam_chong_roi_dat_kem_do_tre(trang, tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    gia = NhaGia(KHOA_XA)
    gia.giu = threading.Event()  # giữ yêu cầu chat ở nhà giả tới khi test cho đi
    with MayChuThat(gia.app) as s_o, loi_that(tmp_path, monkeypatch) as w:
        _dang_ky_nha(w, s_o.url)
        _mo_hang_tai_khoan(trang, w)
        assert trang.chu(DONG_KQ) == "chưa thử chat"
        bam_chuot(trang, NUT, "nút Thử chat")
        # Trong lúc chạy: nhãn «Đang thử…», nút bị khoá, dòng kết quả «đang thử…».
        trang.cho(f"{NUT}?.innerText === 'Đang thử…'", mo_ta="nút đổi nhãn «Đang thử…»")
        assert trang.js(f"{NUT}.disabled") is True, "nút phải bị khoá khi đang thử"
        assert trang.chu(DONG_KQ) == "đang thử…"
        assert gia.da_nhan.wait(10), "nhà giả chưa nhận yêu cầu chat"
        # Bấm chồng (chuột thật + Enter/Space tại nút): không sinh thêm yêu cầu nào.
        for _ in range(3):
            trang.js(f"{NUT}.click()")
            trang.cdp.goi("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 5, "y": 5, "button": "left", "clickCount": 1}, trang.s)
            trang.cdp.goi("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 5, "y": 5, "button": "left", "clickCount": 1}, trang.s)
        assert trang.js(f"{NUT}?.innerText") == "Đang thử…"
        assert trang.js(f"{_hien(NUT)}"), "nút đang thử vẫn phải thấy được"
        # Để thời gian trôi đủ rồi mới cho nhà giả trả lời → độ trễ đo được > vài trăm ms.
        trang.cho("(() => { window.__t0 = window.__t0 || performance.now(); return performance.now() - window.__t0 > 400; })()",
                  mo_ta="đợi 400ms ở trạng thái đang thử")
        gia.giu.set()
        trang.cho(f"{NUT}?.innerText === 'Thử chat'", han=15, mo_ta="nút trở lại «Thử chat»")
        trang.cho(f"(document.querySelector({json.dumps(DONG_KQ)})?.innerText || '').startsWith('đạt')", han=15, mo_ta="kết quả đạt")
        kq = trang.chu(DONG_KQ)
        khop = re.match(r"^đạt — «(?P<tl>.+)» \((?P<ms>\d+) ms\)(?: · lần thử .+)?$", kq)
        assert khop, f"dòng kết quả không đúng khuôn «đạt — «…» (N ms)»: {kq!r}"
        assert khop["tl"] == "từ xa", kq
        assert int(khop["ms"]) >= 300, f"độ trễ {khop['ms']} ms nhỏ hơn thời gian nhà giả bị giữ: {kq!r}"
        assert trang.js(f"{NUT}.disabled") is False
        # Nhà giả nhận ĐÚNG MỘT yêu cầu chat, nhỏ, có khoá đã cấu hình, không stream, model của chính nhà đó.
        assert gia.so_chat() == 1, [x["duong"] for x in gia.nhat_ky]
        gui = next(x for x in gia.nhat_ky if x["duong"] == "chat")
        assert gui["auth"] == f"Bearer {KHOA_XA}"
        assert gui["than"].get("stream") is not True
        assert gui["than"]["model"] == "qwen-3b", gui["than"]
        assert len(gui["than"]["messages"]) == 1 and len(gui["than"]["messages"][0]["content"]) < 100, gui["than"]
        assert gui["than"].get("max_tokens", 0) <= 64, gui["than"]
        _khong_lo(trang, KHOA_XA)


@pytest.mark.parametrize("ma, cau", [(401, "Token bị từ chối (401)"), (429, "Hết hạn mức (429)"), (500, "Dịch vụ lỗi (500)")],
                         ids=["401", "429", "500"])
def test_c_nha_gia_tra_loi_thi_hien_ma_loi_tieng_viet_khong_lo_khoa(trang, tmp_path, monkeypatch, ma, cau):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    gia = NhaGia(KHOA_XA)
    gia.ma = ma
    with MayChuThat(gia.app) as s_o, loi_that(tmp_path, monkeypatch) as w:
        _dang_ky_nha(w, s_o.url)
        _mo_hang_tai_khoan(trang, w)
        bam_chuot(trang, NUT, "nút Thử chat")
        trang.cho(f"(document.querySelector({json.dumps(DONG_KQ)})?.innerText || '').startsWith('không đạt')", han=20,
                  mo_ta=f"kết quả không đạt (nhà giả trả {ma})")
        kq = trang.chu(DONG_KQ)
        assert kq.startswith(f"không đạt — {cau}"), kq
        assert trang.js(f"{NUT}.innerText") == "Thử chat" and trang.js(f"{NUT}.disabled") is False, "nút phải dùng lại được"
        assert gia.so_chat() >= 1
        # Không lộ khoá, không lộ thân lỗi thô, không lộ chữ «error»/mã JSON của nhà cung cấp trong dòng kết quả.
        _khong_lo(trang, KHOA_XA, THAN_LOI_THO)
        assert "{" not in kq and "invalid_request_error" not in kq


def _do_390(trang, buoc: str) -> dict:
    """Đo hàng khoá đang mở ở khổ điện thoại: nút «Thử chat»/«Làm mới», dòng kết quả, độ tràn ngang của trang."""
    trang.js(f"{NUT}.scrollIntoView({{block: 'center'}})")
    d = trang.js(f"""(() => {{
        const rong = window.innerWidth;
        const ra = (e) => {{ const r = e.getBoundingClientRect(); return {{trai: Math.round(r.left), phai: Math.round(r.right), cao: Math.round(r.height)}}; }};
        const hang = document.querySelector('[data-khoa-pv="0"]');
        const hr = hang.getBoundingClientRect();
        return {{rong, cuon: document.documentElement.scrollWidth, hangPhai: Math.round(hr.right),
                 thu: ra({NUT}), lam: ra({NUT_LAM_MOI}),
                 kq: (() => {{ const e = hang.querySelector('[data-kv=thu-chat]'); return e ? Object.assign(ra(e), {{cuon: e.scrollWidth, rong: e.clientWidth}}) : null; }})()}};
    }})()""")
    trang.chup(f"thu-chat-390-{buoc}")
    return d


def _mo_dien_thoai_va_thu(trang, w, gia_url: str, buoc_do):
    """Khổ 390px; đo khi CHƯA thử và sau khi ĐÃ thử (dòng kết quả dài hơn)."""
    _dang_ky_nha(w, gia_url)
    trang.khung(390, 844, dien_thoai=True)
    _mo_hang_tai_khoan(trang, w)
    assert trang.js(_hien(NUT))
    buoc_do(_do_390(trang, "chua-thu"), "chua-thu")
    bam_chuot(trang, NUT, "nút Thử chat (điện thoại)")
    trang.cho(f"(document.querySelector({json.dumps(DONG_KQ)})?.innerText || '').startsWith('đạt')", han=15, mo_ta="đạt trên điện thoại")
    buoc_do(_do_390(trang, "da-thu"), "da-thu")


def test_d_dien_thoai_390_nut_trong_man_hinh_khong_tran_ngang(trang, tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    gia = NhaGia(KHOA_XA)

    def kiem(d, buoc):
        assert d["cuon"] <= d["rong"], f"[{buoc}] trang tràn ngang: {d}"
        assert d["hangPhai"] <= d["rong"], f"[{buoc}] dòng tràn khỏi màn hình: {d}"
        for ten in ("thu", "lam"):
            n = d[ten]
            assert 0 <= n["trai"] and n["phai"] <= d["rong"], f"[{buoc}] nút {ten} ra khỏi màn hình: {d}"
            assert n["cao"] > 0 and n["phai"] > n["trai"], f"[{buoc}] nút {ten} không có kích thước: {d}"
        assert d["kq"] and d["kq"]["phai"] <= d["rong"] and d["kq"]["cuon"] <= d["kq"]["rong"] + 1, f"[{buoc}] dòng kết quả tràn: {d}"

    with MayChuThat(gia.app) as s_o, loi_that(tmp_path, monkeypatch) as w:
        _mo_dien_thoai_va_thu(trang, w, s_o.url, kiem)


def test_d_dien_thoai_390_nut_cao_it_nhat_44px(trang, tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    gia = NhaGia(KHOA_XA)

    def kiem(d, buoc):
        for ten in ("thu", "lam"):
            assert d[ten]["cao"] >= 44, f"[{buoc}] nút {ten} cao {d[ten]['cao']}px < 44px: {d}"

    with MayChuThat(gia.app) as s_o, loi_that(tmp_path, monkeypatch) as w:
        _mo_dien_thoai_va_thu(trang, w, s_o.url, kiem)
