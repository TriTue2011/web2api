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
    # Đã duyệt 18/10 (điều phối đọc hàm): bộ lọc log chặn token của module kênh — không định dạng được dòng log thì BỎ dòng đó (đóng khi lỗi,
    # không để lọt token); không được ném ra vì ngoại lệ trong bộ lọc làm hỏng chính lời gọi ghi log.
    ("modules/zalo-bot/module.py", "filter"),
    ("modules/telegram-bot/module.py", "filter"),   # cùng lý do với zalo-bot: không định dạng được dòng log → bỏ dòng (đóng khi lỗi)
    # Đã duyệt NV138 (cùng mẫu zalo-bot/telegram-bot): bộ lọc log chặn khoá dịch vụ van-phong-office —
    # không định dạng được dòng log thì BỎ dòng đó (đóng khi lỗi, không để lọt khoá); ngoại lệ trong bộ lọc
    # làm hỏng chính lời gọi ghi log nên không được ném ra.
    ("modules/van-phong/module.py", "filter"),
    # Đã duyệt NV159 (cùng mẫu zalo-bot/telegram-bot/van-phong): bộ lọc log chặn URL lịch bí mật —
    # không định dạng được dòng log thì BỎ dòng đó (đóng khi lỗi, không để lọt URL bí mật); ngoại lệ trong
    # bộ lọc làm hỏng chính lời gọi ghi log nên không được ném ra.
    ("modules/lich-ics/module.py", "filter"),
    # Đã duyệt NV172 (cùng mẫu van-phong/zalo-bot): bộ lọc log chặn khoá Bearer của dịch vụ zalo-ca-nhan —
    # không định dạng được dòng log thì BỎ dòng đó (đóng khi lỗi, không để lọt khoá); ngoại lệ trong bộ lọc
    # làm hỏng chính lời gọi ghi log nên không được ném ra.
    ("modules/zalo-ca-nhan/module.py", "filter"),
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
    ("modules/claude-web/module.py", "mat_khau_da_luu"),    # Đã duyệt patch bổ sung NV90 (đọc từng hàm): fail-closed, không mở quyền.
    # Đã duyệt NV122 (đọc từng hàm): hop_thu_ra() của module đọc hộp thư lạ hỏng →
    # bỏ module đó (continue), tiếp tục quét module khác — fail-closed, không mở quyền.
    ("app/ma_xac_minh.py", "danh_sach_hop_thu"),
    # Logic vượt thử thách tách ra lõi dùng chung (NV91 tầng b, NV92 dùng luôn).
    ("app/khoi_phuc_phien.py", "vuot_thu_thach_toi_da"),  # lỗi lạ khi đọc tín hiệu/cố vượt → False = không vượt được → can_nguoi
    ("app/khoi_phuc_phien.py", "la_captcha_chu"),  # đọc tín hiệu hỏng → False; bên gọi đã fail-closed trước đó nếu tin_hieu() ném
    # Đã duyệt NV91 (đọc từng hàm): dọn dẹp best-effort khi gỡ module/tắt máy —
    # chờ tác vụ đã huỷ xong; lỗi của tác vụ không được làm hỏng bước dọn.
    ("app/tu_lam_moi.py", "huy_cua_module"),  # dọn tác vụ nền của module bị gỡ: nuốt lỗi tác vụ đã huỷ
    ("app/tu_lam_moi.py", "huy_tat_ca"),  # dọn mọi tác vụ khi tắt máy/cuối test: nuốt lỗi tác vụ đã huỷ
    ("app/tu_lam_moi.py", "dung"),  # QuanLyTuLamMoi.dung: chờ tác vụ đồng bộ đã huỷ; lỗi không được chặn tắt máy
    # Đã duyệt NV94 (đọc từng hàm): bỏ qua lỗi theo hướng AN TOÀN, không mở quyền.
    ("app/khoi_phuc_phien.py", "khoi_phuc"),  # giành trình duyệt lỗi (409 bận/không mở) → log.warning + tầng (b) hỏng mã trinh_duyet_ban → sang tầng kế; finally dong() best-effort
    ("app/tu_lam_moi.py", "_dang_chay_hien_tai"),  # registry dang_chay hỏng → None → khoi_phuc chạy không cầu nối (đường cũ)
    ("modules/chatgpt-web/module.py", "danh_tinh_id_cua"),  # kho danh tính hỏng → "" → không qua cầu nối trình duyệt thật → đường cũ
    ("modules/claude-web/module.py", "danh_tinh_id_cua"),  # như trên
    ("modules/gemini-web/module.py", "danh_tinh_id_cua"),  # như trên
    ("modules/grok-web/module.py", "danh_tinh_id_cua"),  # như trên
    # Đã duyệt NV121 (đọc từng hàm): bỏ qua lỗi theo hướng AN TOÀN, không mở quyền.
    ("modules/doc-ma-hop-thu/module.py", "filter"),  # không định dạng được dòng log → bỏ dòng đó (đóng khi lỗi, không để lọt mật khẩu)
    ("modules/doc-ma-hop-thu/module.py", "_xet_thu"),  # phân tích thư hỏng → bỏ thư đó (fail-closed, không trích mã nhầm)
    ("modules/doc-ma-hop-thu/module.py", "_van_ban"),  # trích văn bản hỏng → bỏ (fail-closed)
    # Đã duyệt vá bảo mật NV153 2026-10-20 (đọc từng hàm, fail-closed, không mở quyền):
    # filter này đã CÓ từ đợt B nhưng chưa từng đăng ký danh sách kiểm toán — bổ sung cùng đợt vá.
    ("modules/youtube-phat/module.py", "filter"),  # không định dạng được dòng log → bỏ dòng đó (đóng khi lỗi, không để lọt khoá dịch vụ);
    # ngoại lệ trong bộ lọc làm hỏng chính lời gọi ghi log nên không được ném ra (cùng mẫu zalo-bot/telegram-bot/van-phong).
    ("modules/youtube-phat/module.py", "_nguoi_goi_la_quan_tri"),  # đọc ngữ cảnh người gọi hỏng/thiếu →
    # KHÔNG phải quản trị → hành động lay_khoa bị từ chối (đóng khi lỗi, đường lấy bí mật không được mở theo mặc định lạc quan).
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
