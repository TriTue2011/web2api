# NV176 — Kiểm kê trợ lý giáo viên (c2a) → module `tro-ly-giao-vien`

Soát xong ngày 2026-10-20 từ bản sao `~/workspace/c2a-nv146` (nguồn gốc đã nêu
`/opt/claude-c2a/chatgpt2api` commit `d879b03` — không còn tồn tại trên VM, nên
mọi tham chiếu dưới đây đều đối chiếu trên bản sao; được ghi rõ ở đầu mỗi tệp
module). Đợt này (NV176 đợt 1): **sản phẩm kiểm kê này + port phần thuần hàm
KHÔNG gọi mô hình, KHÔNG phụ thuộc tro-ly**. **Cập nhật đợt 2 (20/10/2026,
theo quyết chủ máy):** đã làm `cham_tu_luan_model` — xem mục Đợt 2 dưới đây.

## 1. Sơ đồ nhóm tệp trong c2a

| # | Nhóm tệp (c2a) | Quy mô | Việc nó làm | Phụ thuộc ngoài | Rủi ro |
|---|---|---|---|---|---|
| A | `services/agent/teacher.py` (495 dòng) | agent loop | Điều phối: chọn kỹ năng theo lớp, soạn giáo án, chấm, khen, classroom TTS | `services.agent.runtime.call_model`, `tw` (B), DB học sinh | cần mô hình + DB |
| B | `services/agent/teacher_workspace.py` (1418 dòng) | KB/lớp học | Không gian lớp, mục lục SGK, nạp/xử lý markdown, ghi đĩa | `DATA_DIR`, KB trên đĩa | cần hạ tầng dữ liệu |
| C | `services/agent/teacher_assess.py` (1025 dòng) | kiểm tra/chấm | Ra đề từ mẫu, chấm heuristic, rubric, gợi ý bậc thang, chấm tự luận qua model | `runtime.call_model` (một số hàm), `_ROOT` đĩa | tách thuần/mô hình |
| D | `services/agent/teacher_bai_tap.py` (659 dòng) | bài tập hàng ngày | Bài tập cá nhân hoá theo lịch học | DB lịch + học sinh | cần DB |
| E | `services/agent/teacher_classroom.py` (1013 dòng) | lớp học | Trạng thái lớp, phản hồi TTS, điểm danh | DB trạng thái | cần DB |
| F | `services/agent/teacher_english.py` (822 dòng) | tiếng Anh | Mẫu câu theo cấp lớp/CEFR, phát hiện trọng tâm | không | **thuần — port nguyên** |
| G | `services/agent/teacher_lecture.py` (443 dòng) | bài giảng | Tạo bài giảng qua model, tra cứu mục lục | model + mục lục đã lưu | tách thuần/mô hình |
| H | `services/agent/teacher_path.py` (1269 dòng) | lộ trình | Hồ sơ học sinh, đặt chỗ (placement), mạch kiến thức | DB học sinh | tách thuần/DB |
| I | `services/agent/teacher_seed.py` (347 dòng) | hạt dữ liệu | Tự tải/tạo nội dung khởi tạo (gọi mạng) | mạng ngoài | cần mạng |
| J | `services/agent/sgk_autofill.py` (239), `sgk_bulk.py` (572), `sgk_fetch.py` (704), `sgk_taphuan.py` (1341) | nạp SGK | Tải/mở rộng/sửa markdown SGK | mạng ngoài (DuckDuckGo, Cloudflare — đo 2026-10-20: 202 anomaly / 403) | cần mạng + bản quyền |
| K | `api/teacher.py` (1352 dòng) | API HTTP | Giao thức API của c2a | FastAPI c2a | không dùng (khác hợp đồng) |
| L | `services/huong_dan_hoc/` (17 tệp .md) | nội dung hướng dẫn học | Prompt/tài liệu hướng dẫn — **không phải mã** | không | không port |
| M | `teacher_default/sgk/` (36 tệp .md, lớp 1–12) | nội dung sách giáo khoa | Nội dung sách — **bản quyền, KHÔNG sao chép** | không | không port |

Ước lượng dòng (tập trung nhóm A–J): ~9.6K dòng; phần thuần không mô hình ~2.4K dòng.

## 2. Phân đợt

### Đợt 1 — XONG trong NV176 này (port thuần hàm, không gọi mô hình)

| Hàm/lớp port | Từ c2a | Tệp module |
|---|---|---|
| Toàn bộ (nguyên văn) | F `teacher_english.py` | `modules/tro-ly-giao-vien/tieng_anh.py` |
| `make_quiz` (bỏ ghi đĩa), `make_quiz_adaptive`, `make_check`, `grade_answer` (đường thuần; bỏ bước LLM), `grade_quiz` (nhận dict đề), `progressive_hints` (bỏ tra KB), `rubric_band`, `format_rubric_help`, `format_quiz_for_student`, `_summary_vi`, `_math_item`, `_english_item`, `_van_item`, `_hint_from_body`, `_fallback_prompt`, `SUBJECT_HINT`, `_rng_seed` | C | `modules/tro-ly-giao-vien/cham.py` |
| `skill_for_grade`, `_MATH_VERBAL`, `verbalize_for_tts`, `praise_for`, `build_lesson_plan`, `format_classroom_reply` | A | `modules/tro-ly-giao-vien/giao_an.py` |
| `SUBJECTS`, `SUBJECT_LABEL`, `GRADE_SUBJECTS`, `GRADES`, `SUBJECT_ALIASES`, `subjects_for`, `level_of`, `level_label`, `_normalize_subject` → `chuan_mon` | B | `modules/tro-ly-giao-vien/mon_hoc.py` |
| `SCHOOL_YEAR_CUTOFF_MONTH`, `school_year_start`, `grade_from_birth_year`, `resolve_grade` → `xac_dinh_lop`, `_math_band`, `MATH_STRANDS`, `VAN_STRANDS`, `EN_STRANDS`, `strands_for` → `mach_kien_thuc` | H | `modules/tro-ly-giao-vien/lop.py` |
| `_TOC_LINE_RE`, `toc_tu_markdown` → `muc_luc_tu_markdown`, `detect_volume` → `doan_tap` | G + B | `modules/tro-ly-giao-vien/muc_luc.py` |
| Kho đề theo danh tính (0600, ghi nguyên tử, trần 100) — viết mới, không phải c2a | — | `modules/tro-ly-giao-vien/kho_de.py` |

### Đợt 2 — cần mô hình — **LÀM MỘT PHẦN 20/10/2026 (theo quyết chủ máy)**

- ✅ `grade_answer` bước `_llm_grade` → hành động mới `cham_tu_luan_model`
  (+ `cham_nhieu_bai_model`, tối đa 5 bài/lượt) trên thực thể
  `tro_ly_giao_vien.ket_qua_cham`. Thay cho `tro-ly`/`runtime.call_model` của
  c2a: gọi `/v1/chat/completions` của chính lõi qua loopback bằng khoá khách
  phạm vi hẹp (mẫu NV135/NV163 — cấu hình `model_cham`/`khoa_khach`/`dia_chi_v1`).
  Barem do giáo viên tự khai theo tiêu chí; điểm model là **ĐỀ XUẤT**, mã tự
  cộng tổng từ barem và kẹp điểm từng tiêu chí vào trần barem (chống tiêm
  lệnh từ bài làm); giáo viên `xac_nhan_diem` mới thành điểm chính thức. Kết
  quả lưu 0600 theo danh tính, **không chứa bài làm**. Mã: `cham_model.py` +
  `ket_qua_cham.py`; test: `test/test_nv176_dot2_cham_model.py`.
- ⏳ Chưa làm (vẫn thuộc đợt 2): A vòng lặp agent (call_model + chọn kỹ năng —
  cần lõi tro-ly NV135); G sinh bài giảng qua mô hình
  (`ai_draft_lesson`/`ai_draft_assignment`); D bài tập hàng ngày;
  `english.models` (ánh xạ mô hình tiếng Anh).

### Đợt 3 — cần hạ tầng dữ liệu (độc lập với đợt 2)

- Lưu đề đã xong kho (đợt 1); còn: hồ sơ học sinh bền vững (`get_or_create_profile` của H) — dữ liệu trẻ em, cần đánh giá pháp lý/lưu trữ trước khi port.
- Đặt chỗ (placement), lịch trình lộ trình theo học sinh, trạng thái classroom (E), nạp mục lục SGK (B, cần quyết bản quyền J/M).

### Đợt 4 — hạ tầng/tích hợp nặng (cần quyết riêng từng cái)

- I `teacher_seed` (tự tải mạng): chỉ port khi người dùng kích hoạt rõ ràng.
- Tạo ảnh bài giảng (Pillow AVIF, phụ thuộc hệ thống ~2.2GB): đề xuất tách thành dịch vụ phụ, không nhúng vào lõi module.
- J `sgk_fetch`/`sgk_taphuan`: đo 2026-10-20 DuckDuckGo trả 202 anomaly, taphuan Cloudflare 403 — không port tới khi có đường ổn định.
- L `huong_dan_hoc/*.md`, M `teacher_default/sgk/*.md`: không port (nội dung, không phải mã; sách có bản quyền).

## 3. Điều chỉnh khi port (đợt 1)

1. **Đổi tên công khai sang tiếng Việt** ở lớp bí danh (`chuan_mon`, `xac_dinh_lop`, `mach_kien_thuc`, `muc_luc_tu_markdown`, `doan_tap`, `loi_khen`, `mau_giao_an`, `verbalize_cho_tts`, `nhan`) — hàm c2a giữ nguyên tên gốc trong cùng tệp để đối chiếu.
2. **`grade_answer` bỏ tham số `use_llm` và bước `_llm_grade`** (đợt 2 trả lại dưới tên `cham_tu_luan_model`).
3. **`make_quiz` bỏ ghi đĩa** — lưu đề qua `kho_de` của module (0600, nguyên tử, cách ly theo danh tính); `grade_quiz` nhận `quiz` dict thay vì `quiz_id`.
4. **`load_quiz`/`_ROOT` đĩa cũ không port** — thay bằng `kho_de`.
5. **`progressive_hints` bỏ nhánh tra KB SGK** (cần RAG c2a); markdown SGK trong `muc_luc_markdown` là **dữ liệu không tin** (chỉ phân tích regex, không thực thi).
6. **Bài làm học sinh** (`student_answer`): chỉ dùng trong lượt chấm, strip khỏi đầu ra, không log.
7. **Năm sinh** chỉ dùng một lần trong `xac_dinh_lop`, không lưu hồ sơ học sinh.
8. `build_lesson_plan` template có nhắc các hành động đợt sau (`search_sgk`, `teacher_hint`...) — giữ nguyên như c2a; các đợt sau cài tiếp.
9. (Đợt 2) `_llm_grade` của c2a chọn model theo môn/kỹ năng (`model_write`,
   `model_for_english_skill`) — web2api dùng MỘT `model_cham` do quản trị cấu
   hình cho module; c2a nuốt lỗi LLM rồi rơi xuống heuristic, bản port **báo
   lỗi «không chấm được»** và không rơi xuống heuristic (không bịa điểm).
   Barem mặc định của c2a là rubric theo môn; bản port dùng barem giáo viên
   tự khai (mặc định 1 tiêu chí «Tổng thể» 10 điểm) để điểm từng tiêu chí
   kiểm chứng được.

## 4. Trạng thái dữ liệu học sinh

Tối thiểu hoá: đợt này module không lưu tên học sinh, năm sinh, hay bài làm vào bất cứ nơi nào. `xac_dinh_lop` chỉ trả lớp suy được; kho đề chứa đề do giáo viên tạo (không chứa dữ liệu học sinh).

## 5. Cần chủ máy quyết

1. ✅ ĐÃ QUYẾT (20/10/2026): làm đợt 2 chấm tự luận qua mô hình — đã làm
   `cham_tu_luan_model` nối loopback lõi (không đợi tro-ly NV135). Còn mở:
   có làm tiếp phần còn lại của đợt 2 (vòng lặp agent, sinh bài giảng, bài
   tập hàng ngày) không?
2. Có port hồ sơ học sinh/placement (đợt 3) không — dữ liệu trẻ em, cần ràng buộc lưu trữ/pháp lý trước?
3. Có nạp nội dung SGK (nhóm J/M — bản quyền) không?
4. Có tách tạo ảnh bài giảng thành dịch vụ phụ (2.2GB) không?
5. Có tích vòng lặp agent tro-ly (đợt 4) không?
