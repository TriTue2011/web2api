"""NV40 — Trang «Trợ giúp» (web/He-thong-Tro-giup.dc.html): kiểm tra tĩnh + điều hướng trực tiếp.

Kiểm (không cần Chrome):
1. Mọi liên kết nội bộ trong trang trỏ tới tệp web/ có thật.
2. Mọi mục trợ giúp ĐÓNG mặc định (không mục nào mở sẵn trong dữ liệu MUC).
3. Không có chuỗi trông giống khoá thật trong trang (gitleaks): cấm `w2a_[A-Za-z0-9_-]{20,}`
   — trang chỉ được dùng placeholder dạng `w2a_<…>`.
4. Template không dùng innerHTML; dữ liệu hiện bằng liên kết dữ liệu/textContent.
5. Script dc-lite dựng được bằng node đúng như web/support.js làm
   (new Function('DCLogic', mã + '\\n;return Component;')).
6. Trang mở được khi gõ địa chỉ trực tiếp (máy chủ thật, đã đăng nhập → 200,
   chứa đúng một cặp <x-dc>…</x-dc>).

Test Chrome thật nằm ở test_web2api_nv40_tro_giup_giao_dien.py (tự bỏ qua khi thiếu Chrome).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import nguoi_dung, phien_web, so_module
from app.main import app

WEB = Path(__file__).resolve().parent.parent / "web"
TRANG = WEB / "He-thong-Tro-giup.dc.html"

KHOA_QT = "khoa-quan-tri-nv40-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return tmp_path


def _doc_trang() -> str:
    return TRANG.read_text(encoding="utf-8")


def _ma_script(ma: str) -> str:
    m = re.search(
        r'<script type="text/x-dc" data-dc-script[^>]*>(.*?)</script>', ma, re.S
    )
    assert m, "không tìm thấy khối script dc-lite"
    return m.group(1)


# ───────────── 1. Liên kết nội bộ ─────────────

def test_moi_lien_ket_noi_bo_tro_toi_tep_co_that():
    ma = _doc_trang()
    script = _ma_script(ma)
    # Bỏ các dòng comment để không bắt nhầm ví dụ trong comment
    script = "\n".join(
        d for d in script.splitlines() if not d.strip().startswith("//")
    )
    hrefs = set(re.findall(r"\{\s*k:\s*'lk'\s*,[^}]*href:\s*'([^']+)'", script))
    assert hrefs, "trang phải có ít nhất một liên kết nội bộ"
    for h in hrefs:
        assert "://" not in h, f"liên kết nội bộ không được là URL ngoài: {h}"
        assert (WEB / h).is_file(), f"liên kết nội bộ trỏ tới tệp không tồn tại: {h}"


# ───────────── 2. Mọi mục đóng mặc định ─────────────

def test_moi_muc_dong_mac_dinh():
    ma = _doc_trang()
    script = _ma_script(ma)
    # Dữ liệu MUC không được chứa mục mở sẵn
    assert "mo: true" not in script
    # State khởi tạo không mở mục nào
    assert re.search(r"mucMo:\s*\{\}", script), "mucMo phải khởi tạo rỗng"
    # Nút tiêu đề là <button> thật với aria-expanded/aria-controls
    assert 'aria-expanded="{{m.moChu}}"' in ma
    assert 'aria-controls="noi-dung-{{m.id}}"' in ma


# ───────────── 3. Không có chuỗi giống khoá thật ─────────────

def test_khong_co_chuoi_giong_khoa_that():
    ma = _doc_trang()
    # Cấm chuỗi dạng khoá khách thật (w2a_ + ≥20 ký tự base64); chỉ cho placeholder w2a_<…>
    khoa_that = re.findall(r"w2a_[A-Za-z0-9_-]{20,}", ma)
    assert not khoa_that, f"trang chứa chuỗi giống khoá thật: {khoa_that[:2]}"
    assert "w2a_<khoá-khách-của-bạn>" in ma, "ví dụ phải dùng placeholder rõ ràng"


# ───────────── 4. Không innerHTML với dữ liệu ─────────────

def test_khong_innerHTML_voi_du_lieu():
    ma = _doc_trang()
    template = ma.split("</x-dc>")[0]
    assert "innerHTML" not in template
    assert "outerHTML" not in template
    # Nội dung mục hiện bằng {{…}} (textContent qua dc-lite), mã mẫu trong <pre>
    assert "<pre>{{d.ma}}</pre>" in ma


# ───────────── 5. Script dựng được bằng node ─────────────

def test_script_dung_duoc_bang_node():
    if not shutil.which("node"):
        pytest.skip("không có node")
    ma = _doc_trang()
    script = _ma_script(ma)
    js = r"""
const fs = require("fs");
const K = {
  BIEU_TUONG: { xuong: "M6 9l6 6 6-6", logo: "", quay_lai: "" },
  menuNav: () => [], navHeThong: () => [], quayLai: () => null,
  tenNguoiDung: (t) => (t && t.ten) || "",
  boDau: (s) => String(s||"").normalize("NFD").replace(/[̀-ͯ]/g,"").replace(/đ/g,"d").replace(/Đ/g,"D"),
  phimNav: () => null, napToi: async () => null, chuyenDangNhap: () => {}, dangXuat: () => {},
};
global.window = { Khung: K };
const code = fs.readFileSync(0, "utf8");
class DCLogic {
  constructor(p){ this.props=p; this.state={}; }
  setState(x){ Object.assign(this.state, x); }
}
const Component = new Function("DCLogic", code + "\n;return Component;")(DCLogic);
const inst = new Component({});
// user thường chỉ thấy mục không dành cho admin
inst.state.toi = { id: "u1", vai: "user", ten: "nd" };
let v = inst.renderVals();
const idsUser = v.mucHienThi.map((x) => x.id);
if (idsUser.length !== 2 || !idsUser.includes("ket-noi") || !idsUser.includes("su-co"))
  throw new Error("user thường phải thấy đúng 2 mục, thấy: " + idsUser.join(","));
if (!v.mucHienThi.every((x) => x.mo === false))
  throw new Error("mọi mục phải đóng mặc định");
// admin thấy đủ 9 mục
inst.state.toi = { id: "a1", vai: "admin", ten: "admin" };
v = inst.renderVals();
if (v.mucHienThi.length !== 9) throw new Error("admin phải thấy 9 mục, thấy " + v.mucHienThi.length);
// lọc chữ
inst.state.tuKhoaLoc = "sao lưu";
v = inst.renderVals();
if (v.mucHienThi.length !== 1 || v.mucHienThi[0].id !== "sao-luu")
  throw new Error("lọc 'sao lưu' phải ra đúng 1 mục");
// mở một mục
inst.state.tuKhoaLoc = "";
inst.chuyenMuc("ket-noi");
v = inst.renderVals();
const muc = v.mucHienThi.find((x) => x.id === "ket-noi");
if (!muc.mo) throw new Error("bấm tiêu đề phải mở mục");
console.log("NODE OK");
"""
    r = subprocess.run(
        ["node", "-e", js], input=script, capture_output=True, text=True, timeout=30
    )
    assert r.returncode == 0, f"node dựng script lỗi: {r.stderr[:500]}"
    assert "NODE OK" in r.stdout


# ───────────── 6. Mở được khi gõ địa chỉ trực tiếp ─────────────

def test_mo_trang_bang_dia_chi_truc_tiep(du_lieu):
    with TestClient(app) as c:
        r = c.get("/He-thong-Tro-giup.dc.html", headers=QT)
        assert r.status_code == 200
        assert r.text.count("<x-dc>") == 1
        assert r.text.count("</x-dc>") == 1
        assert "Trợ giúp" in r.text
