# Trợ lý giáo viên (tro-ly-giao-vien)

Trợ lý giáo viên cho chương trình phổ thông Việt Nam. **Đợt 1 (0.1.0):**
phần thuần hàm KHÔNG gọi mô hình, KHÔNG lưu hồ sơ học sinh; kho đề theo danh
tính giáo viên (0600, ghi nguyên tử, cách ly giữa các danh tính).

Mã port từ c2a theo LUẬT PORT — mỗi tệp ghi rõ nguồn và điều chỉnh ở đầu tệp:

| Tệp module | Nguồn c2a | Nội dung |
|---|---|---|
| `tieng_anh.py` | `services/agent/teacher_english.py` | Sao chép nguyên văn: kỹ năng/bản đồ theo lớp, mẫu câu |
| `cham.py` | `services/agent/teacher_assess.py` | Ra đề, chấm heuristic, rubric, gợi ý bậc thang (bỏ `_llm_grade`, bỏ ghi đĩa — xem điều chỉnh ở đầu tệp) |
| `giao_an.py` | `services/agent/teacher.py` | Giáo án 6 pha, lời khen, verbalize cho TTS, phản hồi lớp học |
| `mon_hoc.py` | `services/agent/teacher_workspace.py` | Danh mục môn theo lớp, cấp tiểu/THCS/THPT, viết tắt |
| `lop.py` | `services/agent/teacher_path.py` | Xác định lớp từ năm sinh, mạch kiến thức Toán/Văn/Anh |
| `muc_luc.py` | `services/agent/teacher_lecture.py` + `teacher_workspace.detect_volume` | Mục lục từ markdown SGK, đoán tập sách |
| `kho_de.py` | mới (không phải c2a) | Kho đề 0600 theo danh tính, ghi nguyên tử, trần 100 đề |

## Thực thể

- `tro_ly_giao_vien.thu_vien` (rủi ro `doc`): ra_de, ra_bai_anh, cham_mot_cau,
  cham_de (đọc đề từ kho), band_diem, rubric, goi_y, giao_an_mau, mon_theo_lop,
  xac_dinh_lop, lo_trinh_mach, muc_luc_markdown, doan_tap, kich_nang_anh, doc_tts.
- `tro_ly_giao_vien.kho_de` (rủi ro `ghi`): luu_de, doc_de, liet_ke_de, xoa_de —
  mọi hành động đều cần ngữ cảnh gọi hợp lệ (`app.ngu_canh.lay()`), thiếu là
  từ chối; đề của danh tính A không thể đọc bởi B.

## An toàn

- Bài làm học sinh là **dữ liệu không tin**: chỉ đối chiếu đáp án ở lớp thuần,
  không log, không đưa vào phản hồi sau khi xử lý.
- Năm sinh/lớp khai tay chỉ dùng một lần để suy lớp — không lưu hồ sơ học sinh.
- `luu_de`/`xoa_de` thuộc hành động `ghi`; mặc định hệ thống tắt các hành động
  ghi nếu chưa được bật theo quy ước chung.

## Đợt sau (xem `docs/nv176-kiem-ke.md`)

Chấm tự luận bằng mô hình, đề theo học sinh + đặt chỗ (placement), hồ sơ lớp,
nạp SGK, vòng lặp agent tro-ly, tạo ảnh bài giảng (Pillow, ~2.2GB AVIF).
