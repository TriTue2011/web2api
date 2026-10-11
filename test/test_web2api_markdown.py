"""Bộ phân tích markdown của khung (Khung.phanTichMarkdown trong web/khung.js) chạy bằng node — tầng THUẦN, không DOM.

Phần dựng DOM, chặn HTML độc và stream dở kiểm bằng Chrome thật ở test_web2api_giao_dien.py; ở đây kiểm cấu trúc
khối/dòng ở các ca khó (lồng, bảng, rào chưa đóng, dấu chưa đóng) và trần thời gian với chuỗi bệnh lý — nhanh, không
cần trình duyệt.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
KHUNG = Path(__file__).resolve().parents[1] / "web" / "khung.js"
pytestmark = pytest.mark.skipif(not NODE, reason="cần node")

# khung.js là IIFE gắn vào window.Khung; chạy trong vm với `window` rỗng (customElements không có → bỏ qua đăng ký).
CHAY = r"""
const fs = require('fs'), vm = require('vm');
const ctx = { window: {}, URL }; vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
const vao = JSON.parse(fs.readFileSync(0, 'utf8'));
const ra = vao.map((c) => { const t = process.hrtime.bigint(); const kq = ctx.window.Khung.phanTichMarkdown(c);
  return { kq, ms: Number(process.hrtime.bigint() - t) / 1e6 }; });
process.stdout.write(JSON.stringify(ra));
"""


def phan_tich(*chu: str) -> list[dict]:
    r = subprocess.run([NODE, "-e", CHAY, str(KHUNG)], input=json.dumps(chu), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def chu_phang(ds) -> str:
    """Chữ hiển thị của một dãy đoạn trong dòng (để so nhanh)."""
    ra = ""
    for x in ds:
        ra += x.get("v", "\n" if x["t"] == "xuong" else "") + chu_phang(x.get("con", []))
    return ra


def test_cau_tra_loi_that_cua_chu_may():
    # Câu chủ máy dán từ Studio: «**» phải thành in đậm, không còn hiện nguyên chữ.
    (kq,) = phan_tich("Hôm nay là **thứ Năm**, ngày **11/07/2024** (tính theo giờ Việt Nam). Bạn cần tôi tra cứu thông tin gì nữa không?")
    (doan,) = kq["kq"]
    assert doan["t"] == "doan"
    assert [x["t"] for x in doan["con"]] == ["chu", "dam", "chu", "dam", "chu"]
    assert "**" not in chu_phang(doan["con"])


def test_khoi_tieu_de_danh_sach_long_trich_dan_ke_ma():
    a, b, c = phan_tich(
        "# Một\n#### Bốn\n\n- một\n- **hai**\n  - con a\n  - con b\n- ba\n\n3. x\n4. y",
        "> trích\n> **dẫn**\n\n---\n\ncuối",
        "Trước\n```py\nprint('*không* **đậm**')\n```\nsau",
    )
    k = a["kq"]
    assert [(x["t"], x.get("cap")) for x in k[:2]] == [("tieu_de", 1), ("tieu_de", 4)]
    ul, ol = k[2], k[3]
    assert ul["t"] == "ds" and not ul["so"] and len(ul["muc"]) == 3
    long = ul["muc"][1]
    assert long[0]["con"][0]["t"] == "dam" and long[1]["t"] == "ds" and len(long[1]["muc"]) == 2   # lồng một cấp
    assert ol["so"] and ol["bat_dau"] == 3 and len(ol["muc"]) == 2
    assert [x["t"] for x in b["kq"]] == ["trich", "ke", "doan"]
    assert b["kq"][0]["con"][0]["con"][-1]["t"] == "dam"
    assert [x["t"] for x in c["kq"]] == ["doan", "ma", "doan"]
    assert c["kq"][1] == {"t": "ma", "ngon_ngu": "py", "chu": "print('*không* **đậm**')", "mo": False}   # trong mã: chữ y nguyên


def test_bang_gfm_canh_le_va_o_thieu():
    (kq,) = phan_tich("| Tên | Giá |\n|:--|--:|\n| táo | **5** |\n| lê \\| mận |\n\nsau")
    bang, doan = kq["kq"]
    assert bang["t"] == "bang" and bang["canh"] == ["left", "right"]
    assert [chu_phang(o) for o in bang["dau"]] == ["Tên", "Giá"]
    assert bang["hang"][0][1][0]["t"] == "dam"
    assert [chu_phang(o) for o in bang["hang"][1]] == ["lê | mận", ""]   # «\|» là chữ; thiếu ô thì bù ô rỗng
    assert doan["t"] == "doan"


def test_lien_ket_chi_http_https_mailto():
    (kq,) = phan_tich("[a](https://vd.example/x_(y)) [b](mailto:ai@vd.example) [c](javascript:alert(1)) "
                      "[d](JAVASCRIPT:alert(1)) [e](data:text/html,<b>x</b>) [f](/tuong-doi) ![g](https://vd.example/a.png) "
                      "<script>alert(1)</script> https://vd.example/tran.")
    con = kq["kq"][0]["con"]
    lk = [(x["href"], chu_phang(x["con"])) for x in con if x["t"] == "lien_ket"]
    assert lk == [("https://vd.example/x_(y)", "a"), ("mailto:ai@vd.example", "b"),
                  ("https://vd.example/tran", "https://vd.example/tran")]
    chu = chu_phang(con)
    for nguyen_van in ("[c](javascript:alert(1))", "[d](JAVASCRIPT:alert(1))", "[f](/tuong-doi)",
                       "![g](https://vd.example/a.png)", "<script>alert(1)</script>"):
        assert nguyen_van in chu, nguyen_van


def test_stream_do_dang_khong_vo_cau_truc():
    dam_do, rao_mo, bang_do, ma_do = phan_tich("Đang **dở", "Xem:\n```js\nlet a = 1", "| a | b |", "gọi `ham(")
    assert dam_do["kq"] == [{"t": "doan", "con": [{"t": "chu", "v": "Đang **dở"}]}]   # chưa đóng → chữ thường
    assert rao_mo["kq"][1] == {"t": "ma", "ngon_ngu": "js", "chu": "let a = 1", "mo": True}
    assert bang_do["kq"][0]["t"] == "doan"   # chưa có dòng |---| → chưa là bảng
    assert chu_phang(ma_do["kq"][0]["con"]) == "gọi `ham("


def test_nhan_manh_khong_bat_nham():
    (kq,) = phan_tich("snake_case_name, 2 * 3 * 4, *nghiêng*, ***cả hai***, ~~gạch~~, \\*thoát\\*")
    con = kq["kq"][0]["con"]
    assert [x["t"] for x in con if x["t"] != "chu"] == ["nghieng", "dam", "gach"]
    assert "snake_case_name, 2 * 3 * 4, " in chu_phang(con) and "*thoát*" in chu_phang(con)


def test_giu_khoang_trang_cuoi_va_chuoi_benh_ly_nhanh():
    k80 = "".join(f"k{i} " for i in range(80))
    thuong, *benh = phan_tich(k80, "[" * 20000, "*a" * 10000, "**" * 10000, "> " * 3000 + "x", "- " * 3000 + "x",
                              "`" * 5000 + "x")
    assert chu_phang(thuong["kq"][0]["con"]) == k80   # stream 80 khúc: không cắt khoảng trắng cuối
    for b in benh:
        assert b["ms"] < 1000, b["ms"]
