"""«Đăng nhập tay» trong chi tiết tài khoản (chủ máy 23/10 + 28/10/2026): trình duyệt từ xa CHỈ mở từ nút này, tab «Trình duyệt»
không còn trên thanh điều hướng, hai liên kết «Mở trình duyệt» rời đã bỏ. Kiểm tĩnh trên mã giao diện và manifest."""
import json
import re
from pathlib import Path

GOC = Path(__file__).resolve().parents[1]
HTML = (GOC / "web" / "He-thong-AI.dc.html").read_text(encoding="utf-8")


def test_nut_dang_nhap_tay_la_nut_goi_ham_khong_phai_lien_ket_tab():
    assert re.search(r'<button[^>]*id="k268-dn-tay"[^>]*onClick="\{\{khuCt\.dangNhapTay\}\}"', HTML)
    assert 'href="{{khuCt.duongTay}}"' not in HTML


def test_khong_con_lien_ket_mo_trinh_duyet_roi():
    assert "nut-dl-trinh-duyet" not in HTML
    assert "them.td.duongTrinhDuyet" not in HTML


def test_ham_dang_nhap_tay_dung_api_mo_va_ve_cua_trinh_duyet_tu_xa():
    i = HTML.index("async khuDangNhapTay")
    ham = HTML[i:i + 2500]
    assert "/mo" in ham and "/ve" in ham and "api/trinh-duyet/" in ham
    assert "window.open" in ham                      # mở cửa sổ ngay trong cú bấm (không bị chặn)
    ma = "\n".join(l for l in ham.split("\n") if not l.strip().startswith("//"))
    assert "email" not in ma.lower()                 # mã: hồ sơ cố định theo id tài khoản, không đụng tới email


def test_tab_trinh_duyet_an_khoi_thanh_dieu_huong():
    man = json.loads((GOC / "modules" / "vnc-xa" / "manifest.json").read_text(encoding="utf-8"))
    assert man["tab_web"].get("an_khoi_thanh") is True


def test_module_doi_ma_phai_tang_phien_ban():
    """Server chạy module từ bản cài trong dữ liệu và chỉ cập nhật khi `phien_ban` đổi: các module đã sửa 28/10/2026 phải
    có phiên bản mới hơn bản đã cài trên máy chủ (0.2.0 / 0.1.0)."""
    cu = {"vnc-xa": "0.2.0"}
    for mod in ("vnc-xa", "gemini-web", "chatgpt-web", "grok-web", "claude-web", "antigravity", "may-dang-nhap", "openaicodex"):
        v = json.loads((GOC / "modules" / mod / "manifest.json").read_text(encoding="utf-8"))["phien_ban"]
        goc = cu.get(mod, "0.1.0")
        assert tuple(map(int, v.split("."))) > tuple(map(int, goc.split("."))), f"{mod}: {v} chưa mới hơn {goc}"
