"""So dc-lite (web/support.js) với bộ dựng THAM CHIẾU trên các trang demo THẬT, bằng Chrome thật.

Cách so: mỗi trang được dựng hai lần ở TRẠNG THÁI MẶC ĐỊNH —
  (1) bộ tham chiếu (tham_chieu_dung_trang.js, trích từ _audit.js) ra chuỗi HTML rồi
      document.write vào trang;
  (2) chính file .dc.html chạy với dc-lite —
cả hai chạy trong CÙNG Chrome và đi qua CÙNG bộ phân tích HTML (--dump-dom), rồi được
chuẩn hoá và so cây DOM. Bộ tham chiếu chạy trong Chrome chứ không trong node vì đã đo:
He-thong.dc.html vẽ biểu đồ bằng Math.sin/pow/log, và V8 của node 22 với V8 của Chrome 150
lệch bit cuối → toFixed(2) ra 73.13 so với 73.12. Đó là khác biệt của máy JS, không phải
của bộ dựng; chạy cùng máy thì phép so chỉ còn đo bộ dựng.

Chênh lệch CÓ CHỦ ĐÍCH (chỉ những thứ dưới đây được chuẩn hoá; mọi khác biệt khác làm test đỏ):
  1. Thuộc tính on* (onClick…): tham chiếu ghi onclick="" vào DOM vì hàm chèn thành rỗng;
     dc-lite gắn bộ nghe sự kiện thật và không để thuộc tính → bỏ on* ở cả hai phía.
  2. Thuộc tính boolean (disabled, checked, autofocus…): tham chiếu ghi "true"/"false"/"" —
     mà disabled="false" trong HTML vẫn là TẮT nút. dc-lite: đúng → có, sai → không có.
     So theo nghĩa: "true" ⇔ có; "false" ⇔ không có; "" (tham chiếu không phân biệt được
     giá trị undefined với thuộc tính tĩnh) → chấp nhận cả hai, có đếm và in ra.
  3. Chú thích HTML: dc-lite đặt cặp neo <!--dcN--> quanh mỗi sc-for/sc-if để vá DOM không
     lệch → bỏ mọi chú thích ở cả hai phía, rồi gộp các nút chữ liền nhau.
  4. Khoảng trắng: gộp chuỗi khoảng trắng thành một dấu cách, bỏ đầu/cuối, bỏ nút chữ rỗng.
  5. Gốc: tham chiếu dựng thẳng vào <body>; dc-lite dựng vào <x-dc style="display:contents">
     → so con của <body> (tham chiếu) với con của <x-dc> (dc-lite).
  6. <helmet>: tham chiếu đặt nội dung helmet vào <head>; dc-lite DỜI các nút đó lên <head>
     và thêm <style>x-dc{display:none}</style> → kiểm riêng: mọi <link>/<style> của helmet
     tham chiếu phải có trong <head> của dc-lite.
Giá trị chèn vào được dc-lite thoát ký tự HTML còn tham chiếu thì không — không chuẩn hoá
điều này: nếu dữ liệu demo có '<' hay '&…;' làm hai bên khác nhau, test phải đỏ để ta thấy.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import pytest

from ui_chung import dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401  (hai fixture Chrome + đăng nhập bằng phiên)

THU_MUC_TEST = Path(__file__).resolve().parent
WEB = THU_MUC_TEST.parent / "web"
# Các trang demo thiết kế nằm NGOÀI kho (kho thiết kế riêng) → chỉ chạy khi chỉ rõ thư mục, không đoán đường dẫn.
_DEMO_BIEN = os.environ.get("WEB2API_DEMO_DIR", "").strip()
DEMO = Path(_DEMO_BIEN) if _DEMO_BIEN else None
CHROME = os.environ.get("WEB2API_CHROME", "/usr/bin/google-chrome")
NODE = shutil.which("node")
# Chặn Google Fonts: phông không ảnh hưởng cấu trúc DOM, chặn thì test chạy được khi mất mạng.
CHAN_FONT = "--host-resolver-rules=MAP fonts.googleapis.com ~NOTFOUND, MAP fonts.gstatic.com ~NOTFOUND"

CAN_CHROME = pytest.mark.skipif(not (Path(CHROME).exists() and NODE), reason="cần Chrome và node")
CAN_DEMO = pytest.mark.skipif(DEMO is None or not DEMO.is_dir(),
                              reason="đặt WEB2API_DEMO_DIR=<thư mục trang demo .dc.html> để chạy" if DEMO is None
                              else f"không có thư mục demo {DEMO}")

TRANG = [
    "Tien-ich.dc.html",
    "Main.dc.html",
    "Bo-nao.dc.html",
    "Doi-song.dc.html",
    "He-thong.dc.html",
    "Kien-truc.dc.html",
    "Nha.dc.html",
    "Nhat-ky.dc.html",
    "Studio-dien-thoai.dc.html",
    "Tac-pham.dc.html",
]

BOOL = {"disabled", "checked", "selected", "readonly", "multiple", "hidden", "autofocus", "required", "open", "novalidate", "inert"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


# ---------------- máy chủ cục bộ do test tự dựng ----------------
class _XuLy(SimpleHTTPRequestHandler):
    """/support.js → dc-lite của ta; /ref/<tên>.dc.html → trang bọc chạy bộ tham chiếu;
    /tham_chieu_dung_trang.js → bộ tham chiếu; /<tên>.dc.html → demo THẬT (chỉ đọc, không chép)."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path.split("?", 1)[0]
        if p.startswith("/ref/"):
            trang = json.dumps(p.rsplit("/", 1)[-1])
            # ?pre=<JSON> → state dựng sẵn cho bộ tham chiếu (cùng tham số pre của _audit.js).
            q = parse_qs(urlsplit(self.path).query)
            pre = json.dumps(json.loads(q["pre"][0])) if "pre" in q else "{}"
            # Shim tối thiểu để file CommonJS chạy được như <script> thường; nhánh CLI tự bỏ qua.
            body = (
                '<!doctype html><html><head><meta charset="utf-8">'
                "<script>var module = { exports: {} }; var require = function () { return {}; };</script>"
                '<script src="/tham_chieu_dung_trang.js"></script><script>'
                f"var x = new XMLHttpRequest(); x.open('GET', '/' + {trang}, false); x.send();"
                f"var r = module.exports.dungThamChieu(x.responseText, {pre});"
                "document.open(); document.write('<!doctype html><html><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">' + r.head + '</head><body>' + r.body + '</body></html>');"
                "document.close();</script></head><body></body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def translate_path(self, path):
        p = path.split("?", 1)[0]
        ten = p.rsplit("/", 1)[-1]
        if "/" in p.strip("/"):
            return "/khong-co"
        if p == "/support.js":
            return str(WEB / "support.js")
        if p == "/tham_chieu_dung_trang.js":
            return str(THU_MUC_TEST / ten)
        return str(DEMO / ten) if DEMO is not None else "/khong-co"


@pytest.fixture(scope="module")
def may_chu():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _XuLy)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def dump_dom(url: str, budget_ms: int = 6000) -> str:
    with tempfile.TemporaryDirectory() as ho_so:
        r = subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", f"--user-data-dir={ho_so}",
             "--window-size=1280,900", CHAN_FONT, f"--virtual-time-budget={budget_ms}", "--dump-dom", url],
            capture_output=True, text=True, timeout=120,
        )
    assert r.stdout.strip(), f"Chrome không trả DOM cho {url}: {r.stderr[-500:]}"
    return r.stdout


# ---------------- phân tích + chuẩn hoá DOM ----------------
class _Cay(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.goc = {"tag": "#goc", "attrs": {}, "con": []}
        self.ngan = [self.goc]

    def handle_starttag(self, tag, attrs):
        n = {"tag": tag, "attrs": {k: (v if v is not None else "") for k, v in attrs}, "con": []}
        self.ngan[-1]["con"].append(n)
        if tag not in VOID:
            self.ngan.append(n)

    def handle_startendtag(self, tag, attrs):
        self.ngan[-1]["con"].append({"tag": tag, "attrs": {k: (v or "") for k, v in attrs}, "con": []})

    def handle_endtag(self, tag):
        for i in range(len(self.ngan) - 1, 0, -1):
            if self.ngan[i]["tag"] == tag:
                del self.ngan[i:]
                return

    def handle_data(self, data):
        self.ngan[-1]["con"].append(data)

    # handle_comment không ghi gì: chú thích bị bỏ (chênh lệch có chủ đích số 3).


def phan_tich(html: str) -> dict:
    p = _Cay()
    p.feed(html)
    return p.goc


def tim(n, tag):
    if isinstance(n, dict):
        if n["tag"] == tag:
            return n
        for c in n["con"]:
            r = tim(c, tag)
            if r:
                return r
    return None


def chuan_hoa_con(ds, phia, dem):
    ra, chu = [], []

    def xa_chu():
        s = re.sub(r"\s+", " ", "".join(chu)).strip()
        if s:
            ra.append(s)
        chu.clear()

    for c in ds:
        if isinstance(c, str):
            chu.append(c)
            continue
        xa_chu()
        if c["tag"] in ("script",):
            continue
        attrs = {}
        for k, v in c["attrs"].items():
            if k.startswith("on") or k.startswith("data-dc-on-"):
                continue
            if k in BOOL:
                if phia == "ref":
                    if v == "false":
                        continue
                    if v == "":
                        dem["mo_ho"] += 1
                        attrs[k] = "?"
                        continue
                attrs[k] = ""
                continue
            attrs[k] = v
        ra.append({"tag": c["tag"], "attrs": attrs, "con": chuan_hoa_con(c["con"], phia, dem)})
    xa_chu()
    return ra


def so_cay(a, b, duong, loi):
    """So danh sách nút đã chuẩn hoá; ghi tối đa 8 chỗ khác đầu tiên."""
    for i in range(max(len(a), len(b))):
        if len(loi) >= 8:
            return
        x = a[i] if i < len(a) else None
        y = b[i] if i < len(b) else None
        d = f"{duong}/{i}"
        if isinstance(x, str) or isinstance(y, str) or x is None or y is None:
            if x != y:
                loi.append(f"{d}: tham chiếu={_ngan(x)} | dc-lite={_ngan(y)}")
            continue
        if x["tag"] != y["tag"]:
            loi.append(f"{d}: thẻ {x['tag']} ≠ {y['tag']}")
            continue
        ax, ay = dict(x["attrs"]), dict(y["attrs"])
        for k, v in list(ax.items()):
            if v == "?":  # mơ hồ (xem chênh lệch số 2): chấp nhận có hoặc không
                ax.pop(k)
                ay.pop(k, None)
        if ax != ay:
            khac = sorted(k for k in set(ax) | set(ay) if ax.get(k) != ay.get(k))
            loi.append(f"{d}<{x['tag']}>: thuộc tính khác {[(k, ax.get(k), ay.get(k)) for k in khac][:4]}")
        so_cay(x["con"], y["con"], f"{d}<{x['tag']}>", loi)


def _ngan(x):
    if isinstance(x, dict):
        return f"<{x['tag']} {json.dumps(x['attrs'], ensure_ascii=False)[:120]}>"
    return repr(x)[:160]


# ---------------- test so sánh cấu trúc ----------------
@CAN_CHROME
@CAN_DEMO
@pytest.mark.parametrize("trang", TRANG)
def test_dc_lite_khop_tham_chieu(may_chu, trang):
    if not (DEMO / trang).exists():
        pytest.skip(f"thiếu {trang}")
    goc = may_chu
    ref = phan_tich(dump_dom(f"{goc}/ref/{trang}"))
    assert len(tim(ref, "body")["con"]) > 0, "bộ tham chiếu không dựng ra gì"
    cua_ta = phan_tich(dump_dom(f"{goc}/{trang}"))

    xdc = tim(cua_ta, "x-dc")
    assert xdc is not None, "dc-lite không để lại <x-dc>"
    assert xdc["attrs"].get("data-dc") == "san-sang", f"dc-lite không dựng xong: {xdc['attrs']} {_ngan(tim(cua_ta, 'div'))}"
    assert not any(isinstance(c, dict) and c["attrs"].get("id") == "dc-loi" for c in tim(cua_ta, "body")["con"]), "dc-lite báo lỗi trên trang"

    dem = {"mo_ho": 0}
    a = chuan_hoa_con(tim(ref, "body")["con"], "ref", dem)
    b = chuan_hoa_con(xdc["con"], "cua_ta", dem)
    loi = []
    so_cay(a, b, "", loi)
    assert not loi, f"{trang}: DOM dc-lite khác tham chiếu:\n" + "\n".join(loi)
    print(f"{trang}: khớp; {dem['mo_ho']} thuộc tính boolean mơ hồ ở tham chiếu")

    # Helmet: mọi <link>/<style> của tham chiếu phải có trong <head> của dc-lite.
    def helmet(head):
        return [(c["tag"], c["attrs"].get("href", ""), re.sub(r"\s+", " ", "".join(x for x in c["con"] if isinstance(x, str))).strip())
                for c in head["con"] if isinstance(c, dict) and c["tag"] in ("link", "style")]

    co = helmet(tim(cua_ta, "head"))
    for muc in helmet(tim(ref, "head")):
        assert muc in co, f"helmet thiếu trong <head> của dc-lite: {muc[:2]}"


# ---------------- test đường VÁ DOM: bấm rồi so, rồi bấm ngược và so lại ----------------
_BAM_AI = "[...document.querySelectorAll('.tm button[aria-haspopup=menu]')].find((b) => b.textContent.trim() === 'AI').click()"
_BAM_THE = "[...document.querySelectorAll('[role=tab]')].find((b) => b.textContent.trim() === {}).click()"
_GO_TIM = "(() => {{ const i = document.querySelector('form input[type=search]'); i.value = {}; i.dispatchEvent(new Event('input', {{ bubbles: true }})); }})()"
# Mỗi pre chỉ chứa đúng những khoá mà hàm bấm đặt (đã đọc mã demo: toggle menu đặt menuMo+accMo,
# thẻ loại đặt cat, ô tìm của Đời sống đặt q).
KICH_BAN_VA = {
    # Mở menu «AI» chèn sc-if có sc-for lồng vào GIỮA anh em; thẻ «Kênh chat» làm danh sách co lại.
    "Tien-ich.dc.html": [
        (_BAM_AI, {"menuMo": "AI", "accMo": False}),
        (_BAM_AI, {"menuMo": None, "accMo": False}),
        (_BAM_THE.format(json.dumps("Kênh chat")), {"cat": "kenh"}),
        (_BAM_THE.format(json.dumps("Tất cả")), {"cat": "tat-ca"}),
    ],
    # Nút «Tìm» có disabled="{{x.btnOff}}": gõ chữ thì thuộc tính phải BỊ GỠ, xoá chữ thì có lại.
    # Đây là đường gỡ thuộc tính thừa của vaNut — Tien-ich không đi qua đường này.
    "Doi-song.dc.html": [
        (_GO_TIM.format(json.dumps("a")), {"q": "a"}),
        (_GO_TIM.format(json.dumps("")), {"q": ""}),
    ],
}


@CAN_CHROME
@CAN_DEMO
@pytest.mark.parametrize("trang", list(KICH_BAN_VA))
def test_va_dom_sau_tuong_tac_khop_tham_chieu(may_chu, trang):
    """Lần dựng đầu chỉ CHÈN nút; chỉ tương tác mới đi qua vaNut / neo / gỡ thuộc tính thừa.

    Đi A→B→A; sau mỗi bước DOM (đã vá) phải khớp tham chiếu dựng thẳng với state tương ứng.
    Quay về A phải khớp lại — bắt được thuộc tính cũ còn sót, phần tử danh sách thừa, nội dung
    lọt sai đoạn neo.
    """
    goc = may_chu
    buoc = KICH_BAN_VA[trang]
    r = subprocess.run([NODE, str(THU_MUC_TEST / "cdp_dieu_khien.js"), CHROME, f"{goc}/{trang}",
                        "--buoc", json.dumps([b for b, _ in buoc])], capture_output=True, text=True, timeout=90)
    kq = json.loads([d for d in r.stdout.splitlines() if d.startswith("{")][-1])
    assert r.returncode == 0, kq
    for i, ((_, pre), anh) in enumerate(zip(buoc, kq["anh"])):
        ref = phan_tich(dump_dom(f"{goc}/ref/{trang}?pre={quote(json.dumps(pre))}"))
        dem = {"mo_ho": 0}
        a = chuan_hoa_con(tim(ref, "body")["con"], "ref", dem)
        b = chuan_hoa_con(tim(phan_tich(anh), "x-dc")["con"], "cua_ta", dem)
        loi = []
        so_cay(a, b, "", loi)
        assert not loi, f"{trang} bước {i + 1} (pre={pre}): DOM sau khi vá khác tham chiếu:\n" + "\n".join(loi)
    # Bước đầu phải thật sự làm DOM khác bước sau — nếu không, phép so trên vô nghĩa.
    assert kq["anh"][0] != kq["anh"][1]


# ---------------- test hành vi: gõ phím thật ----------------
def go_phim(url: str, sel: str, them: str = "null") -> dict:
    r = subprocess.run([NODE, str(THU_MUC_TEST / "cdp_dieu_khien.js"), CHROME, url, sel, them],
                       capture_output=True, text=True, timeout=90)
    dong = [d for d in r.stdout.splitlines() if d.startswith("{")]
    assert dong, f"cdp_dieu_khien không in kết quả: {r.stdout[-400:]} {r.stderr[-400:]}"
    kq = json.loads(dong[-1])
    assert r.returncode == 0, kq
    return kq


@CAN_CHROME
@CAN_DEMO
def test_go_vao_o_tim_khong_mat_focus_va_con_tro(may_chu):
    goc = may_chu
    # Sau khi gõ: (1) chữ «không khớp» chứng tỏ đã dựng lại theo q mới; (2) bấm thẻ «Kênh chat»
    # rồi đọc aria-selected — chứng tỏ onClick được gắn và setState chạy qua sự kiện bấm.
    kq = go_phim(f"{goc}/Tien-ich.dc.html", "#tim", """(async () => {
        const khop = document.body.innerText.includes('Không có tiện ích nào khớp.');
        [...document.querySelectorAll('[role=tab]')].find((b) => b.textContent.trim() === 'Kênh chat').click();
        await new Promise((r) => setTimeout(r, 50));
        return { khop, chon: document.querySelector('[role=tab][aria-selected=true]').textContent.trim() };
    })()""")
    assert kq["cungPhanTu"] and kq["conTrongTrang"], kq
    assert kq["giaTri"] == "abXcd", kq
    assert kq["conTro"] == 3, f"con trỏ nhảy: {kq}"
    # Chứng cứ trang ĐÃ dựng lại theo state mới (nếu không dựng lại, test trên qua mà vô nghĩa):
    assert kq["thuocTinhValue"] == "abXcd" and kq["them"]["khop"] is True, kq
    assert kq["them"]["chon"] == "Kênh chat", kq


@CAN_CHROME
@CAN_DEMO
def test_go_vao_textarea_main_khong_mat_focus(may_chu):
    goc = may_chu
    kq = go_phim(f"{goc}/Main.dc.html", "#loi-nhan")
    assert kq["cungPhanTu"] and kq["conTrongTrang"] and kq["giaTri"] == "abXcd" and kq["conTro"] == 3, kq
    assert kq["thuocTinhValue"] == "abXcd", kq


KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"


# ---------------- trang khởi đầu chạy với API THẬT ----------------
@pytest.fixture
def may_chu_web2api(tmp_path, monkeypatch):
    from app.main import app
    from gia import MayChuThat

    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    with MayChuThat(app, lifespan="on") as srv:
        yield srv.url


def dom_sau_dang_nhap(trang, goc: str, duong: str) -> str:
    """Trang quản trị cần phiên: đăng nhập thật bằng giao diện, mở `duong`, chờ dựng xong rồi lấy DOM đã dựng."""
    dang_nhap_quan_tri(trang, goc)
    trang.di(goc + duong)
    trang.cho("(document.querySelector('x-dc')||{getAttribute(){}}).getAttribute('data-dc') === 'san-sang'",
              mo_ta="trang dựng xong")
    trang.cho("document.querySelectorAll('[data-nav]').length >= 2", mo_ta="thanh điều hướng")
    trang.cho("!document.querySelector('.bao-cho')", mo_ta="danh sách module đọc xong")
    return trang.js("document.documentElement.outerHTML")


@CAN_CHROME
def test_dia_chi_goc_mo_trang_module_voi_api_that(may_chu_web2api, trang):
    # Lõi rỗng: «/» (index.html) chọn trang đầu theo /api/tinh-nang → Hệ thống → Module, đã dựng bằng dc-lite.
    dom = dom_sau_dang_nhap(trang, may_chu_web2api, "/")
    cay = phan_tich(dom)
    assert tim(cay, "x-dc")["attrs"].get("data-dc") == "san-sang", dom[-800:]
    # Bỏ <script>/<style> trước khi lấy chữ: mã nguồn trang cũng chứa các câu này, so trên đó là đạt suông.
    chu = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)\b.*?</\1>", " ", dom, flags=re.S)))
    assert "Kết nối với web2api" in chu
    assert re.findall(r'data-nav="([^"]+)"', dom) == ["module", "bao_mat"]   # chưa cài gì: không Studio, không AI; «Bảo mật» là tab của lõi nên luôn có
    assert "Đang đọc danh sách module" not in chu
    assert 'class="cham"' not in dom  # chưa có thông báo module mới → không có chấm báo
    assert 'id="dc-loi"' not in dom


@CAN_CHROME
def test_trang_module_cham_bao_module_moi_va_tab_module(tmp_path, monkeypatch, trang):
    # Trạng thái đồng bộ git có một module «mới» chưa cài → /api/modules trả thong_bao_so=1 → mục Module có chấm báo.
    import json as _json

    from gia import MayChuThat, ghi_thu_muc, tep_echo

    from app import so_module
    from app.main import app

    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    (tmp_path / "nguon_git_trang_thai.json").write_text(_json.dumps({
        "commit": {"sha": "a" * 40, "tac_gia": "x", "thong_diep": "y", "cay": "b" * 40},
        "modules": [{"id": "dich", "ten": "Dịch", "tree_sha": "c" * 40, "loi": None}]}))
    # Một module ĐÃ LIÊN KẾT có tab_web → hiện thành tab trên thanh Hệ thống (sau Module, trước Bảo mật & người dùng).
    ghi_thu_muc(tmp_path / "modules" / "echo", tep_echo())
    (tmp_path / "modules.json").write_text(_json.dumps({"modules": [{"id": "echo", "cau_hinh_cong_khai": {}}]}))
    with MayChuThat(app, lifespan="on") as w:
        dom = dom_sau_dang_nhap(trang, w.url, "/He-thong.dc.html")
    assert re.search(r'<span class="cham" role="img" aria-label="1 module mới hoặc có bản mới trên git"', dom), dom[:3000]
    # echo là module loại AI đã liên kết nên thanh Hệ thống có «AI» ở đầu; tab riêng của module đứng SAU «Module» và TRƯỚC «Bảo mật».
    assert re.findall(r'data-nav="([^"]+)"', dom) == ["ai", "module", "m-echo", "bao_mat"]
    the = re.search(r'<[^>]*data-nav="m-echo"[^>]*>', dom)
    assert the, dom[:3000]  # thuộc tính HTML không có thứ tự cố định: lấy cả thẻ rồi kiểm href trong đó
    assert re.search(r'href="http://127\.0\.0\.1:\d+/m/echo/echo\.dc\.html"', the.group(0)), the.group(0)
    assert 'data-nav="quay-lai"' not in dom
    assert 'data-o="echo"' in dom and 'data-tt="dang-chay"' in dom
