"""NV304 mảnh (a) — trình phát YouTube trong tab Đời sống + ẩn tab_web.

Quy ước kiểm (đỏ-trước-xanh, không Internet thật):
- Manifest module youtube: giao_dien khai `nhung_tab: true` (khu Đời sống
  nhúng trang tab thay vì vẽ ô thực thể thô) và tab_web khai
  `an_khoi_thanh: true` (cơ chế NV263 sẵn có ẩn khỏi thanh Hệ thống —
  trang vẫn mở được bằng đường dẫn trực tiếp).
- app/kham_pha.py `_chuan_hoa_giao_dien`: giữ `nhung_tab` bool, sai
  kiểu thì bỏ cả giao_dien (như mọi trường sai định dạng khác).
- web/khung.js `Khung.tabNhungCua` (hàm thuần, test bằng node như test
  markdown): chỉ chọn tab khi module ĐÃ LIÊN KẾT và khai nhung_tab và
  có mục tab trong /api/tinh-nang; chưa nối / không khai / không có tab
  → None.
- Mẫu trang khu có khung nhúng (iframe, cùng sandbox như trang Module);
  trang tab YouTube ẩn chrome (nút quay lại + tiêu đề) khi ?nhung=1.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

GOC = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GOC))

KHUNG = GOC / "web" / "khung.js"
NODE = shutil.which("node")

MANIFEST = json.loads((GOC / "modules" / "youtube" / "manifest.json").read_text())


def test_manifest_youtube_nhung_tab_va_an_khoi_thanh():
    assert MANIFEST["giao_dien"]["khu"] == "doi_song"
    assert MANIFEST["giao_dien"]["nhung_tab"] is True
    assert MANIFEST["tab_web"]["an_khoi_thanh"] is True


def test_chuan_hoa_giao_dien_giu_nhung_tab():
    from app.kham_pha import _chuan_hoa_giao_dien

    m = {"id": "youtube",
         "giao_dien": {"khu": "doi_song", "muc": "youtube", "nhan": "YouTube",
                       "chip": [], "nhung_tab": True}}
    _chuan_hoa_giao_dien(m)
    assert m["giao_dien"]["nhung_tab"] is True

    m2 = {"id": "youtube",
          "giao_dien": {"khu": "doi_song", "muc": "youtube", "nhan": "YouTube",
                        "chip": [], "nhung_tab": "co"}}
    _chuan_hoa_giao_dien(m2)
    assert "giao_dien" not in m2


def test_template_khu_co_khung_nhung():
    for ten in ("Doi-song.dc.html", "Nha.dc.html", "Tac-pham.dc.html"):
        chu = (GOC / "web" / ten).read_text()
        assert "nhungDuong" in chu, ten
        assert "<iframe" in chu, ten
        assert "allow-same-origin" in chu, ten


def test_trang_youtube_an_chrome_khi_nhung():
    chu = (GOC / "modules" / "youtube" / "web" / "index.html").read_text()
    assert "nhung" in chu
    assert "quay-lai" in chu


CHAY_NODE = r"""
const fs = require('fs'), vm = require('vm');
const ctx = { window: {}, URL }; vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
const vao = JSON.parse(fs.readFileSync(0, 'utf8'));
const ra = vao.map((c) => ctx.window.Khung.tabNhungCua(c.mods, c.tab));
process.stdout.write(JSON.stringify(ra));
"""


def tab_nhung_cua(*truong_hop):
    r = subprocess.run([NODE, "-e", CHAY_NODE, str(KHUNG)],
                       input=json.dumps(truong_hop), capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.skipif(not NODE, reason="cần node")
def test_khung_tab_nhung_chi_khi_da_lien_ket_va_co_tab():
    da_noi = [{"id": "youtube", "da_lien_ket": True,
               "giao_dien": {"khu": "doi_song", "nhung_tab": True}}]
    tab = [{"module": "youtube", "nhan": "YouTube",
            "duong": "m/youtube/index.html", "an_khoi_thanh": True}]
    (co, chua_noi, khong_khai, khong_tab) = tab_nhung_cua(
        {"mods": da_noi, "tab": tab},
        {"mods": [{**da_noi[0], "da_lien_ket": False}], "tab": tab},
        {"mods": [{**da_noi[0],
                   "giao_dien": {"khu": "doi_song"}}], "tab": tab},
        {"mods": da_noi, "tab": []},
    )
    assert co == {"duong": "m/youtube/index.html", "nhan": "YouTube"}
    assert chua_noi is None
    assert khong_khai is None
    assert khong_tab is None
