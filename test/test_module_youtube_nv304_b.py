"""NV304 mảnh (b) — trình phát đúng demo: tìm lên trên cùng, 2 cột màn
hình rộng, nút tròn SVG cho Phát/Queue/Playlist.

Kiểm bằng phân tích HTML tĩnh (html.parser) + đọc trang.js, không cần
trình duyệt thật (máy này không có Chrome):
- Thứ tự mục: «Tìm kiếm» trước «Đang phát»; có mục «Kết quả» riêng
  chứa <ul id="ds-ket-qua">; «Đang phát» + «Nhóm đang phát» ở cột trái,
  «Kết quả» ở cột phải, cả hai trong .luoi-2-cot.
- CSS: từ 1024 px trở lên, .luoi-2-cot là lưới 2 cột; dưới đó một cột.
- trang.js nutKetQua: ba nút kết quả là nút tròn SVG (lớp nut-tron,
  dựng bằng createElementNS — KHÔNG innerHTML với dữ liệu, giữ quy ước
  NV301), nhãn đầy đủ «Phát» / «Thêm vào Queue» / «Thêm vào Playlist»
  qua aria-label (test NV301 cũ vẫn tìm thấy chuỗi), không còn nút viên
  thuốc cho ba nút này.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
from html.parser import HTMLParser

GOC = pathlib.Path(__file__).resolve().parents[1]
HTML = (GOC / "modules" / "youtube" / "web" / "index.html").read_text()
JS = (GOC / "modules" / "youtube" / "web" / "trang.js").read_text()


class _CauTruc(HTMLParser):
    """Ghi, với mỗi id tiêu đề mục, chồng div bao quanh nó."""

    def __init__(self):
        super().__init__()
        self.ngan_xep = []
        self.muc = {}

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "div":
            self.ngan_xep.append(d.get("class", ""))
        if tag in ("h2", "section"):
            pass
        if tag == "h2" and d.get("id"):
            self.muc[d["id"]] = list(self.ngan_xep)

    def handle_endtag(self, tag):
        if tag == "div" and self.ngan_xep:
            self.ngan_xep.pop()


def cau_truc():
    p = _CauTruc()
    p.feed(HTML)
    return p.muc


def test_thu_tu_tim_tren_cung_va_co_muc_ket_qua():
    assert HTML.index('id="tieu-de-tim"') < HTML.index('id="tieu-de-dang-phat"')
    assert 'id="tieu-de-ket-qua"' in HTML
    ket_qua = HTML[HTML.index('id="tieu-de-ket-qua"'):]
    tiep = ket_qua.index("<section", 10) if "<section" in ket_qua[10:] else len(ket_qua)
    assert 'id="ds-ket-qua"' in ket_qua[:tiep]


def test_hai_cot_man_hinh_rong():
    muc = cau_truc()
    assert "luoi-2-cot" in muc["tieu-de-dang-phat"]
    assert "luoi-2-cot" in muc["tieu-de-nhom"]
    assert "luoi-2-cot" in muc["tieu-de-ket-qua"]
    assert "cot-trai" in muc["tieu-de-dang-phat"]
    assert "cot-trai" in muc["tieu-de-nhom"]
    assert "cot-phai" in muc["tieu-de-ket-qua"]
    css = HTML[HTML.index("<style>"):HTML.index("</style>")]
    m = re.search(r"@media\s*\(min-width:\s*1024px\)\s*\{(.*?)\n\s*\}", css, re.S)
    assert m, "thiếu media query 1024px"
    assert ".luoi-2-cot" in m.group(1) and "grid-template-columns" in m.group(1)


def test_nut_ket_qua_tron_svg():
    vung = JS[JS.index("function nutKetQua"):JS.index("async function timKiem")]
    assert "nutTron(" in vung
    assert "nut-tron" in JS  # lớp ở hàm trợ giúp nutTron
    assert "createElementNS" in JS  # SVG dựng bằng createElementNS, không innerHTML
    for nhan in ("Phát", "Thêm vào Queue", "Thêm vào Playlist"):
        assert nhan in vung, f"thiếu nhãn {nhan!r} trong nutKetQua"
    assert "el('button', 'nut nut-nho nut-chinh', 'Phát')" not in vung
    assert ".innerHTML" not in JS


def test_trang_js_hop_le():
    r = subprocess.run(["node", "--check",
                        str(GOC / "modules" / "youtube" / "web" / "trang.js")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
