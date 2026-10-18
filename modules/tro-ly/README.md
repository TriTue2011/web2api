# Module tro-ly — Trợ lý (vòng lặp agent)

Khối xây dựng cho vòng lặp agent của web2api (NV136 G1.5): bộ dựng prompt
theo ngân sách, bộ chọn công cụ theo tầng, X-quang prompt và sổ lỗi công cụ.
Module này CHƯA có vòng lặp gọi mô hình — đó là NV135 (đang làm song song);
NV135 sẽ gọi các hàm của module này để dựng prompt mỗi lượt.

## Cài đặt từng bước

1. Vào tab **Hệ thống → Module**, tìm `tro-ly`, bấm **Cài đặt**.
2. Không cần cấu hình gì thêm — module thuần tính toán, không gọi mạng.
3. Kiểm tra: `GET /api/tinh-nang` phải liệt kê `tro-ly` trong danh sách module.

## Cách dùng (dành cho NV135 / nhà phát triển)

```python
from modules.tro_ly import module as tro_ly  # lõi nạp qua manifest
m = tro_ly.Module(manifest)
m.nap({})

# 1. Dựng prompt theo ngân sách
nen = m.manh_nen()  # prompt nền tối giản (≤ 400 token ước tính)
manhs = [nen,
         tro_ly._mp.ManhPrompt(id="s1", nguon="skill", tang="ngu_canh",
                               uu_tien=60, noi_dung="...", tran_token=300,
                               dieu_kien_nap=lambda bc: bc.get("can_skill")),
         m.manh_so_loi("ho-so-1", "dieu_khien_den"),  # có thể None
]
manhs = [x for x in manhs if x]
van_ban, xq = m.dung_prompt(manhs, ngan_sach_tong=4000,
                            ngan_sach_tang={"on_dinh": 1000, "ngu_canh": 2000,
                                            "hay_doi": 1000},
                            boi_canh={"can_skill": True})

# 2. Chọn công cụ qua danh mục (tên + một dòng, không dán schema vào prompt)
kq = m.tim_cong_cu("bật đèn bếp", danh_muc)  # danh_muc: [{ten, mo_ta, mien, ...}]
if kq["mo_ho"]:
    # hỏi lại người dùng: kq["cau_hoi"], kq["lua_chon"] (đánh số)
    ...
elif kq["thieu_tham_so"]:
    # hỏi thêm tham số còn thiếu
    ...

# 3. Ghi X-quang mỗi lượt (chỉ-thêm, không chứa bí mật)
m.ghi_x_quang(luot_id="...", ho_so_id="...", x_quang=xq,
              cong_cu_schema=[kq["cong_cu"]], cong_cu_danh_muc=[...])
# Đọc (phân quyền theo hồ sơ):
dong = m.doc_x_quang(ho_so_id="...", nguoi_hoi_ho_so_id="...")

# 4. Sổ lỗi: ghi khi công cụ thất bại, đọc khi nạp lại công cụ đó
m.ghi_loi("ho-so-1", "gui_email", "sai_tham_so", "thiếu người nhận")
```

## Thiết kế (tóm tắt)

- **Mảnh prompt** (`manh_prompt.py`): mỗi mảnh có `nguon` (loi/ho_so/skill/module/
  bo_nho/cong_cu), `tang` (on_dinh/ngu_canh/hay_doi), `dieu_kien_nap`, `tran_token`,
  `uu_tien`. Vượt trần → bỏ NGUYÊN mảnh theo ưu tiên (không cắt giữa mảnh).
  Chống trùng bằng băm nội dung chuẩn hoá. Tầng ổn định luôn đứng trước và
  bất biến giữa các lượt cùng đầu vào (byte-by-byte) để bộ nhớ đệm trúng.
- **Chọn công cụ** (`chon_cong_cu.py`): ≤ 8 công cụ nền luôn nạp schema
  (`tim_cong_cu`, `tim_tri_thuc`, `doc_tri_thuc`, `hoi_lai`, `ket_thuc`,
  `ghi_nho_tam`); danh mục chỉ tên + một dòng (≤ 150 ký tự) và CHỈ qua
  `tim_cong_cu(y_dinh)`; schema đầy đủ chỉ của công cụ được chọn (tối đa K=8,
  trần 16). Xếp hạng nhiều tín hiệu thuần Python (tương đồng chuỗi LCS,
  token-max, Jaccard 3-gram, BM25 trên chính tập ứng viên, khớp miền C1,
  tỉ lệ thành công) + **cổng biên**: điểm thấp / chênh hạng 1–2 hẹp / cặp đối
  nghịch không vượt biên đầy đủ / cặp mô tả chồng chéo → `mo_ho=true`, hỏi lại.
  Không danh sách từ khoá ánh xạ ý định→công cụ.
- **X-quang** (`x_quang.py`): JSONL chỉ-thêm; lọc bí mật theo tên trường và mẫu
  `key=value`; đọc phân quyền theo hồ sơ (sai hồ sơ → `PermissionError`).
- **Sổ lỗi** (`so_loi.py`): tối đa 3 dòng gần nhất của ĐÚNG công cụ đang xét
  được đưa vào mảnh `hay_doi`; không dán cả sổ.

## Giới hạn đã biết (CHƯA kiểm)

- Chưa đo trên nhật ký lượt chạy thật của nhà (cần NV135 chạy một thời gian);
  ngưỡng cổng biên là giá trị mặc định, chưa hiệu chỉnh theo dữ liệu thật.
- Chế độ «tự chọn công cụ không hỏi» mặc định TẮT cho tới khi đo đủ.
- API HTTP quản trị (chạy lượt, xem nhật ký) do NV135 / lõi nối sau; module chỉ
  cung cấp hàm có phân quyền.
- Ước tính token là `số ký tự // 4` (ước tính thô), chưa hiệu chỉnh theo tokenizer thật.

## Nguồn tham khảo (đã đọc, chỉ học ý)

- c2a `services/ha_intent_rank.py` (kho công khai `TriTue2011/chatgpt2api`,
  nhánh main): ensemble nhiều tín hiệu + cổng biên + prefilter n-gram.
- c2a `services/agent/skills.py`: mô tả skill ≤ 150 ký tự trong bộ định tuyến.
- Anthropic «Effective context engineering for AI agents» (đọc 2026-10-18):
  tập token tín hiệu cao nhỏ nhất; bắt đầu từ prompt tối giản.
