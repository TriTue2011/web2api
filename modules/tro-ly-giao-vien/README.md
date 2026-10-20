# Trợ lý giáo viên (tro-ly-giao-vien)

Trợ lý giáo viên cho chương trình phổ thông Việt Nam. **Đợt 1 (0.1.0):**
phần thuần hàm KHÔNG gọi mô hình, KHÔNG lưu hồ sơ học sinh; kho đề theo danh
tính giáo viên (0600, ghi nguyên tử, cách ly giữa các danh tính).
**Đợt 2 (0.2.0):** chấm tự luận bằng model của chính lõi (xem mục riêng dưới).

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
| `cham_model.py` | `services/agent/teacher_assess.py::_llm_grade` (đợt 2) | Chấm tự luận bằng model: prompt rào bài làm là DỮ LIỆU, điểm tổng tự cộng từ barem, JSON lệch khuôn → «không chấm được» |
| `ket_qua_cham.py` | mới (không phải c2a) | Lưu kết quả chấm theo danh tính (0600, ghi nguyên tử, trần 200; không chứa bài làm) |

## Thực thể

- `tro_ly_giao_vien.thu_vien` (rủi ro `doc`): ra_de, ra_bai_anh, cham_mot_cau,
  cham_de (đọc đề từ kho), band_diem, rubric, goi_y, giao_an_mau, mon_theo_lop,
  xac_dinh_lop, lo_trinh_mach, muc_luc_markdown, doan_tap, kich_nang_anh, doc_tts.
- `tro_ly_giao_vien.kho_de` (rủi ro `ghi`): luu_de, doc_de, liet_ke_de, xoa_de —
  mọi hành động đều cần ngữ cảnh gọi hợp lệ (`app.ngu_canh.lay()`), thiếu là
  từ chối; đề của danh tính A không thể đọc bởi B.
- `tro_ly_giao_vien.ket_qua_cham` (rủi ro `ghi`, đợt 2): cham_tu_luan_model,
  cham_nhieu_bai_model (tối đa 5 bài/lượt), doc_ket_qua, liet_ke_ket_qua,
  xac_nhan_diem, xoa_ket_qua — cần ngữ cảnh gọi có danh tính; kết quả của
  danh tính A không thể đọc bởi B.

## Đợt 2 — chấm tự luận bằng model (0.2.0)

Nguồn: `_llm_grade` của c2a. Gọi `/v1/chat/completions` của chính lõi qua
loopback bằng khoá khách phạm vi hẹp (mẫu NV135/NV163); cần cấu hình 3 trường
`model_cham`, `khoa_khach`, `dia_chi_v1` (xem manifest). Chưa cấu hình → hành
động chấm model tắt, các hành động thuần vẫn chạy.

- Đầu vào: `de_bai` + `barem` của giáo viên (danh sách `{tieu_chi, diem_toi_da}`;
  bỏ trống = một tiêu chí «Tổng thể» 10 điểm) + `bai_hs`. Đầu ra: điểm từng
  tiêu chí + `diem_de_xuat` + nhận xét.
- **Bài làm là dữ liệu không tin:** prompt hệ thống cố định cấm làm theo chỉ
  dẫn trong bài; bài được đóng khung `<<<BAI_HOC_SINH>>>…<<<HET_BAI>>>`.
  Điểm tổng do mã **tự cộng** từ barem (không tin trường tổng của model); điểm
  từng tiêu chí bị kẹp vào `[0, diem_toi_da]` — bài tiêm lệnh không đẩy điểm
  vượt barem được.
- JSON model lệch khuôn (thiếu/dư tiêu chí, tên không khớp, điểm không phải
  số) → lỗi «không chấm được», không bịa điểm.
- Trần: bài > 8000 ký tự bị từ chối trước khi gọi model; tối đa 5 bài/lượt;
  hết giờ gọi model 90s; lỗi model (timeout/5xx/chưa cấu hình) → lỗi sạch,
  không lộ khoá.
- Kết quả lưu 0600 theo danh tính (không chứa bài làm, không chứa tên học
  sinh), trần 200/danh tính.
- **Điểm model chỉ là ĐỀ XUẤT** (`trang_thai: de_xuat`). Giáo viên dùng
  `xac_nhan_diem` (có thể sửa điểm, trong biên `[0, tổng barem]`) để thành
  điểm chính thức (`da_xac_nhan`).

## An toàn

- Bài làm học sinh là **dữ liệu không tin**: chỉ đối chiếu đáp án ở lớp thuần,
  không log, không đưa vào phản hồi sau khi xử lý; khi chấm bằng model thì
  được rào trong prompt như dữ liệu (đợt 2) và không được lưu cùng kết quả.
- Năm sinh/lớp khai tay chỉ dùng một lần để suy lớp — không lưu hồ sơ học sinh.
- `luu_de`/`xoa_de` và mọi hành động của `ket_qua_cham` thuộc hành động `ghi`;
  mặc định hệ thống tắt các hành động ghi nếu chưa được bật theo quy ước chung.
- Khoá khách gọi model chỉ đi trong header Authorization tới loopback; không
  vào log/nhật ký/phản hồi lỗi; `suc_khoe` chỉ báo có/không, không lộ giá trị.

## Đợt sau (xem `docs/nv176-kiem-ke.md`)

Đề theo học sinh + đặt chỗ (placement), hồ sơ lớp, nạp SGK, vòng lặp agent
tro-ly, tạo ảnh bài giảng (Pillow, ~2.2GB AVIF).
