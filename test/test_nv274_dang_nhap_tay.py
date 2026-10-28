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


def test_giao_dien_khong_gui_loai_trinh_duyet_loi_dat_mac_dinh():
    """Giao diện (web/) không nhắc tên trình duyệt cụ thể; lõi (app/novnc.py) đặt mặc định khi thiếu «loai»."""
    ham = HTML[HTML.index("async khuDangNhapTay"):][:2500]
    assert "loai" not in ham
    nguon = (GOC / "app" / "novnc.py").read_text(encoding="utf-8")
    assert "LOAI_MAC_DINH" in nguon and 'loai = than.get("loai")' in nguon


def test_dang_nhap_tu_dong_hien_khung_xem_anh_va_buoc_nhu_c2a():
    """Chủ máy 28/10/2026: bấm «Đăng nhập tự động» phải cho XEM trình duyệt đang làm gì và HIỆN trạng thái như c2a. Kiểm tĩnh."""
    assert 'id="k268-theo-doi"' in HTML and 'id="k268-dn-buoc"' in HTML and 'id="k268-xem-anh"' in HTML
    i = HTML.index("async dlDangNhapDaLuu(chon, id)")
    ham = HTML[i:i + 2600]
    # hiện khung xem TRƯỚC khi gọi đăng nhập, rồi hỏi bước song song
    assert ham.index("khuBatXem") < ham.index("dlDangNhapDaLuuLoi") and "dlHienBuoc" in ham
    j = HTML.index("khuBatXem(modId, id, chiBat)")
    xem = HTML[j:j + 2600]
    # ảnh lấy bằng yêu cầu có phiên (không đặt khoá vào địa chỉ ảnh), mỗi giây, dừng sau nhiều lần lỗi
    assert "/man-hinh" in xem and 'credentials: "same-origin"' in xem and "setTimeout(r, 1000)" in xem and "soLoi >= 20" in xem
    assert "revokeObjectURL" in HTML[HTML.index("khuDungXem() {"):][:600]
    # thành công thì đóng khung xem; đóng bảng chi tiết thì dừng hỏi ảnh
    assert "this.khuDungXem(); delete cn[id]" in HTML
    assert "khuDong() {\n    this.khuDungXem();" in HTML
    # module cho xem do lõi công bố theo cấu trúc, giao diện không nêu tên nhà cung cấp
    assert "m.man_hinh && m.da_lien_ket" in HTML and "may-dang-nhap" not in xem


def test_dang_nhap_tu_dong_goi_ben_thuc_thi_co_dich_vu_trinh_duyet_va_bao_loi_trong_bang():
    """Chủ máy 28/10/2026 (ảnh chụp: khung xem trống, báo «chưa có trình duyệt nào mở», không thấy lỗi): giao diện gọi module SỞ HỮU tài khoản,
    module đó cần trình duyệt từ xa nên trả lỗi ngay và không tới dịch vụ nào. Phải gọi module có dịch vụ trình duyệt thật, và hiện lỗi
    ngay trong chi tiết tài khoản (bảng che thông báo chung của trang)."""
    i = HTML.index("async dlDangNhapDaLuu(chon, id)")
    ham = HTML[i:i + 2600]
    assert "const bep = (modXem && modXem.dang_nhap_tu_dong) ? modXem : chon;" in ham
    assert "dlDangNhapDaLuuLoi(bep, id)" in ham and "dlHienBuoc(id, bep.id, luot)" in ham
    assert 'id="k268-dn-kq"' in HTML and "dlBao(id, chu, loi, chiPanel)" in HTML
    i0 = HTML.index("async dlDangNhapDaLuuLoi")
    loi = HTML[i0:HTML.index("async dlGuiMa2fa()", i0)]
    assert "this.msg(" not in loi, "mọi thông báo của lượt đăng nhập phải qua dlBao (hiện cả trong bảng)"


def test_khung_xem_khong_dung_the_anh_khi_chua_co_anh_va_co_the_phong_to():
    """Ảnh chụp 28/10/2026: khung hiện biểu tượng ảnh hỏng khi chưa có ảnh; cần phóng to để theo dõi (điện thoại)."""
    assert "coAnh: s.xemId === id && !!s.xemAnh" in HTML and "chuaAnh: s.xemId === id && !s.xemAnh" in HTML
    assert '<sc-if value="{{khuCt.coAnh}}"><img id="k268-xem-anh"' in HTML
    assert 'id="k268-xem-to"' in HTML and "khuPhongTo()" in HTML and "requestFullscreen" in HTML
    assert ".k268-xem.to{position:fixed;inset:0" in HTML                         # dự phòng khi không có toàn màn hình: phủ kín cửa sổ
    assert "xemTo: false" in HTML                                                # thoát khung xem thì thu nhỏ lại
