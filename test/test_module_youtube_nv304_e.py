"""NV304 mảnh (e) — bám demo + sửa 409 (xác nhận một lần lúc gọi).

Kiểm tĩnh trang tab (không Chrome thật trên máy này): ô tìm tròn +
nút tìm tròn có biểu tượng, nút vận chuyển tròn có biểu tượng, nhóm
nằm trong thẻ Đang phát, chip dưới có biểu tượng, tiêu đề kết quả có
số mục, và đường xác nhận xac_nhan cho thực thể nguy hiểm (NV298).
"""

from __future__ import annotations

import pathlib
from html.parser import HTMLParser

GOC = pathlib.Path(__file__).resolve().parents[1]
HTML = (GOC / "modules" / "youtube" / "web" / "index.html").read_text()
JS = (GOC / "modules" / "youtube" / "web" / "trang.js").read_text()


class _Cay(HTMLParser):
    """Cây section/div thô: mỗi node (tag, attrs, con)."""

    def __init__(self):
        super().__init__()
        self.goc = {"tag": "root", "attrs": {}, "con": []}
        self.ngan = [self.goc]

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "con": []}
        self.ngan[-1]["con"].append(node)
        if tag not in ("input", "img", "br", "meta", "link"):
            self.ngan.append(node)

    def handle_endtag(self, tag):
        if len(self.ngan) > 1 and self.ngan[-1]["tag"] == tag:
            self.ngan.pop()


def _tim(node, dieu_kien):
    if dieu_kien(node):
        return node
    for c in node["con"]:
        kq = _tim(c, dieu_kien)
        if kq is not None:
            return kq
    return None


def _co_id(node, id_):
    return _tim(node, lambda n: n["attrs"].get("id") == id_) is not None


def _cay():
    p = _Cay()
    p.feed(HTML)
    return p.goc


def test_o_tim_tron_va_nut_tim_tron_co_bieu_tuong():
    # Demo: ô tìm viên thuốc (tròn hai đầu) + nút tìm tròn xám có kính lúp.
    assert "o-tim-tron" in HTML
    assert "border-radius: 999px" in HTML
    cay = _cay()
    nut = _tim(cay, lambda n: n["attrs"].get("id") == "nut-tim")
    assert nut is not None and "nut-tron" in nut["attrs"].get("class", "")
    assert nut["attrs"].get("aria-label") == "Tìm"
    assert _tim(nut, lambda n: n["tag"] == "svg") is not None
    o_tim = _tim(cay, lambda n: n["attrs"].get("id") == "o-tu-khoa")
    assert "o-tim-tron" in o_tim["attrs"].get("class", "")


def test_nut_van_chuyen_tron_co_bieu_tuong_va_nhan():
    cay = _cay()
    # Demo: Phát là nút tròn đen LỚN; Trước/Kế là nút tròn thường.
    for id_, nhan in (("nut-truoc", "Bài trước"), ("nut-phat-tam-dung", "Phát"),
                      ("nut-sau", "Bài kế")):
        nut = _tim(cay, lambda n, i=id_: n["attrs"].get("id") == i)
        assert nut is not None, id_
        assert "nut-tron" in nut["attrs"].get("class", ""), id_
        assert nut["attrs"].get("aria-label") == nhan, id_
        assert _tim(nut, lambda n: n["tag"] == "svg") is not None, id_
    nut_phat = _tim(cay, lambda n: n["attrs"].get("id") == "nut-phat-tam-dung")
    assert "nut-lon" in nut_phat["attrs"].get("class", "")
    assert "nut-chinh" in nut_phat["attrs"].get("class", "")
    # Demo: Dừng là ô vuông đỏ nhỏ (không tròn) — vẫn 40px, có biểu tượng.
    nut_dung = _tim(cay, lambda n: n["attrs"].get("id") == "nut-dung")
    assert nut_dung is not None
    assert "nut-vuong-do" in nut_dung["attrs"].get("class", "")
    assert nut_dung["attrs"].get("aria-label") == "Dừng hẳn"
    assert _tim(nut_dung, lambda n: n["tag"] == "svg") is not None


def test_nhom_nam_trong_the_dang_phat():
    # Demo: thẻ đang phát chứa cả nhóm đang phát bên trong, không phải
    # một thẻ riêng ngang hàng.
    cay = _cay()
    the_phat = _tim(cay, lambda n: n["tag"] == "section" and _co_id(n, "tieu-de-dang-phat"))
    assert the_phat is not None
    assert _co_id(the_phat, "tieu-de-nhom")


def test_chip_duoi_co_bieu_tuong():
    cay = _cay()
    nav = _tim(cay, lambda n: n["tag"] == "nav"
               and "thanh-chip-duoi" in n["attrs"].get("class", ""))
    assert nav is not None
    nut = [c for c in nav["con"] if c["tag"] == "button"]
    assert len(nut) == 5
    for b in nut:
        assert _tim(b, lambda n: n["tag"] == "svg") is not None


def test_tieu_de_ket_qua_co_so_muc():
    # Demo: «Gợi ý cho bạn  8 mục» — trang thật: «Kết quả (N mục)».
    assert "tieu-de-ket-qua" in JS and "mục" in JS


def test_xac_nhan_mot_lan_khi_409_can_xac_nhan():
    # NV298: thực thể nguy hiểm thì xác nhận MỘT lần lúc gọi; trang tab
    # (NV301) gọi trực tiếp không kèm xac_nhan nên ăn 409 trên server
    # đã nâng rủi ro — nay phải có đường xác nhận + gửi xac_nhan: true.
    assert "hoi-xac-nhan" in HTML and "nut-xac-nhan" in HTML
    assert "can_xac_nhan" in JS
    assert "xac_nhan" in JS
    # Lỗi phải hiện thông điệp của server (để phân biệt 409 can_xac_nhan
    # với 409 queue_end), không chỉ «HTTP 409» trơ trọi.
    assert "error" in JS and "message" in JS


def test_khong_innerhtml():
    assert ".innerHTML" not in JS
