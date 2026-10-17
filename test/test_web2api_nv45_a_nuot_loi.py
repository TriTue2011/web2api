"""NV45 (A): kiểm toán «nuốt lỗi» (except trần / except Exception với thân chỉ
pass/continue/return mặc định) trên app/ và modules/*/module.py.

- test_danh_sach_nuot_loi_khong_tang (XANH): chốt danh sách 23 điểm đã kiểm
  toán thủ công; thêm điểm mới mà chưa phân loại -> test đỏ, bắt buộc xem lại.

Phân loại (chi tiết trong báo cáo NV45):
- AN TOÀN (17 điểm): dọn dẹp best-effort (novnc x2, muse2api.bo), fail-closed
  (nguoi_dung._kiem_tra_mat_khau_dong_bo -> False), hook test
  (giu_slot_scrypt), chuỗi dự phòng có chủ đích (muse._giai_ma_khoa_thiet_bi),
  get defensive (gui_ra._lay_module, webhook._lay_webhook), che_log x2 (có bảo
  vệ trong từng nhánh, ngoài là lưới cuối để logging không sập app),
  dia_chi x2 (hợp đồng ghi rõ None khi hỏng + đường UI lay_thong_tin_dia_chi
  báo rõ trong canh_bao), ham_khoa.kiem_tra_khong_symlink
  (thu_muc_du_lieu() thực tế không ném được), ham_khoa.lay_khoa_chinh@138
  (is_relative_to không ném với 2 Path tuyệt đối), ham_khoa.lay_khoa_chinh@173
  (ghi nhật ký best-effort), phien_web.thu_hoi_phien_nguoi_dung (thu hồi chính
  đã xong trước khi gọi hook).
- CHE LỖI (5): main._vong_dong_bo_khoa (nên log thay vì pass), phien.liet_ke
  (phiên giải mã hỏng bị bỏ lặng lẽ), thiet_bi.lay_mot_thuc_the x2 (lỗi module
  -> coi như không có thực thể/trạng thái), phien_web.la_https (lỗi bất ngờ ->
  cookie không Secure).
- NỚI BẢO VỆ (1): ham_khoa.ghi_bi_mat_tep@330 — chmod 0700 thất bại bị nuốt,
  tệp khoá có thể ở lại quyền lỏng hơn.

Ghi chú: hai ứng viên xfail đã bị LOẠI sau khi kiểm kỹ (không phải lỗi thật):
- dia_chi_cong_khai_da_cau_hinh nuốt tệp hỏng: ĐÚNG theo docstring («None nếu
  cấu hình hỏng»); đường dùng cho UI (lay_thong_tin_dia_chi) báo rõ trong
  canh_bao. Ép ném KhoHong sẽ phá hợp đồng đã ghi.
- kiem_tra_khong_symlink bỏ qua khi không đọc được thư mục dữ liệu: mọi thao
  tác tệp sau đó cũng hỏng theo, không tạo ra đường lỏng hơn.

Nền: web2api-main-4dda468. Không sửa app/.
"""
import pytest
import ast
from pathlib import Path

GOC = Path(__file__).resolve().parents[1]

# (tệp, hàm) chứa 15 điểm nuốt lỗi đã kiểm toán (toàn bộ là AN TOÀN).
DA_KIEM_TOAN = {
    ("app/che_log.py", "filter"),
    ("app/chuyen_tiep.py", "lam_sach_ten_tep"),
    ("app/dia_chi.py", "dia_chi_cong_khai_da_cau_hinh"),
    ("app/goi_kha_nang.py", "goi"),
    ("app/gui_ra.py", "_lay_module"),
    ("app/ham_khoa.py", "kiem_tra_khong_symlink"),
    ("app/ham_khoa.py", "lay_khoa_chinh"),
    ("app/kham_pha.py", "tao"),
    # Đã duyệt 17/10 (điều phối đọc từng hàm): bỏ qua lỗi theo hướng AN TOÀN, không mở quyền.
    ("app/khoa_khach.py", "module_ton_tai"),        # lỗi tra cứu → coi là «không có module» → phạm vi đích danh không khớp
    ("app/nguoi_dung.py", "giu_slot_scrypt"),
    ("app/nguoi_dung.py", "_kiem_tra_mat_khau_dong_bo"),
    ("app/novnc.py", "novnc_websocket_proxy"),
    ("app/phien_web.py", "thu_hoi_phien_nguoi_dung"),
    ("app/phien_web.py", "la_phien_da_xac_nhan_lai"),  # ngày giờ hỏng → coi là CHƯA xác nhận lại (fail-closed)
    ("app/webhook.py", "_lay_webhook"),
    ("modules/gemini-web/module.py", "dung"),       # đóng kết nối khi tắt module: lỗi đóng không làm hỏng việc tắt
    ("modules/muse/module.py", "_giai_ma_khoa_thiet_bi"),
    ("modules/muse2api/module.py", "bo"),
    # Đã duyệt NV88 (đọc từng hàm): bỏ qua lỗi theo hướng AN TOÀN, không mở quyền.
    ("modules/chatgpt-web/module.py", "_kiem_token"),  # lỗi mạng lạ → coi token hỏng (fail-closed)
    ("modules/chatgpt-web/module.py", "mat_khau_da_luu"),  # kho hỏng → coi như chưa lưu mật khẩu
    # Đã duyệt NV89 (đọc từng hàm, cùng mẫu chatgpt-web): kho đọc hỏng → coi
    # như chưa lưu mật khẩu → dang_nhap_lai/tầng (b) trả THẤT BẠI (fail-closed).
    ("modules/gemini-web/module.py", "mat_khau_da_luu"),
    ("modules/grok-web/module.py", "mat_khau_da_luu"),
    ("modules/claude-web/module.py", "mat_khau_da_luu"),
    # Đã duyệt patch bổ sung NV90 (đọc từng hàm): fail-closed, không mở quyền.
    # Logic vượt thử thách tách ra lõi dùng chung (NV91 tầng b, NV92 dùng luôn).
    ("app/khoi_phuc_phien.py", "vuot_thu_thach_toi_da"),  # lỗi lạ khi đọc tín hiệu/cố vượt → False = không vượt được → can_nguoi
    ("app/khoi_phuc_phien.py", "la_captcha_chu"),  # đọc tín hiệu hỏng → False; bên gọi đã fail-closed trước đó nếu tin_hieu() ném
}


def _than_mac_dinh(cac_lenh):
    if len(cac_lenh) == 1 and isinstance(cac_lenh[0], (ast.Pass, ast.Continue)):
        return True
    if len(cac_lenh) == 1 and isinstance(cac_lenh[0], ast.Return):
        g = cac_lenh[0].value
        return g is None or (isinstance(g, ast.Constant) and g.value in (None, False, 0, "", [], {}))
    return all(isinstance(l, ast.Expr) and isinstance(l.value, ast.Constant) for l in cac_lenh)


def _loai_except(h):
    if h.type is None:
        return "tran"
    if isinstance(h.type, ast.Name):
        return h.type.id
    if isinstance(h.type, ast.Tuple):
        return "(" + ",".join(e.id for e in h.type.elts if isinstance(e, ast.Name)) + ")"
    return ast.unparse(h.type)


def tim_diem_nuot_loi():
    ket = set()
    for p in sorted((GOC / "app").rglob("*.py")) + sorted((GOC / "modules").glob("*/module.py")):
        try:
            cay = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        def visit(n, fstack):
            nf = fstack + [n.name] if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) else fstack
            if isinstance(n, ast.Try):
                for h in n.handlers:
                    loai = _loai_except(h)
                    rong = "tran" in loai or "BaseException" in loai or "Exception" in loai
                    if rong and _than_mac_dinh(h.body):
                        ket.add((str(p.relative_to(GOC)), nf[-1] if nf else "<module>"))
            for ch in ast.iter_child_nodes(n):
                visit(ch, nf)

        visit(cay, [])
    return ket


def test_danh_sach_nuot_loi_khong_tang():
    hien = tim_diem_nuot_loi()
    moi = hien - DA_KIEM_TOAN
    assert not moi, f"điểm nuốt lỗi MỚI chưa kiểm toán: {sorted(moi)}"
    assert hien == DA_KIEM_TOAN, f"danh sách đã đổi: thiếu={sorted(DA_KIEM_TOAN - hien)}"
