---
name: code-review
description: Dùng khi nhận phản hồi review, khi xong việc cần xin người khác soát, hoặc trước khi tuyên bố hoàn tất: tiếp nhận có kiểm chứng, xin review qua subagent với đủ ngữ cảnh, và cửa xác minh bằng bằng chứng chạy thật.
---

# Soát mã

Nguyên tắc: **đúng về kỹ thuật quan trọng hơn dễ chịu về mặt xã hội.**
Kiểm chứng trước khi làm theo. Hỏi trước khi đoán. Bằng chứng trước tuyên bố.

## Ba việc, ba thời điểm

1. **Nhận phản hồi** — khi có ý kiến review, nhất là khi thấy mơ hồ hoặc sai về kỹ thuật.
   Đọc → hiểu → xác minh với mã thật → đánh giá → trả lời hoặc phản biện có lý lẽ → làm.
   Không đồng ý lấy lệ («đúng quá!», «ý hay!»), không làm theo khi chưa kiểm chứng,
   chỗ nào chưa rõ thì dừng và hỏi hết trước khi đụng mã. Chi tiết: `references/tiep-nhan-phan-hoi.md`.
2. **Xin review** — sau mỗi việc lớn, trước khi gộp. Giao cho một subagent soát độc lập,
   brief phải có: đã làm gì, kế hoạch/yêu cầu gốc là gì, diff từ đâu tới đâu, và kỳ vọng
   trả về lỗi theo mức (chặn ngay / sửa trước khi đi tiếp / ghi nhận sau). Chi tiết: `references/xin-review.md`.
3. **Cửa xác minh** — trước mọi tuyên bố «xong», «xanh», «đạt». Luật sắt:
   **không có bằng chứng chạy thật mới thì không được tuyên bố.** Chi tiết: `references/cua-xac-minh.md`.

## Bộ kiểm chuẩn của kho (chạy đủ, xét mã thoát)

```bash
.venv/bin/python -m pytest test -q -p no:cacheprovider   # xét MÃ THOÁT
bandit -r app modules -lll                               # mức HIGH phải sạch
docker build -t web2api:latest .                         # ảnh phải dựng được
# thử thật bằng curl sau khi chạy container (xem docs/THAM-CHIEU.md mục 5)
```

Một lệnh hỏng thì DỪNG: không cam kết, không đẩy. Cấm `git push --force`,
`git reset --hard`, `--no-verify`.

## Ngoại lệ về tin báo cáo của agent

Bản gốc coi «tin báo cáo của agent» là dấu hiệu đỏ. Kho này chốt khác:
sau khi một agent đáng tin (Opus/subagent) đã báo kết quả **kèm dòng tổng kết và mã thoát**,
Claude chính chỉ **đọc diff tìm lỗi**, không chạy lại pytest/bandit.
Nhưng báo cáo thiếu dòng kết quả thật (số test, mã thoát) thì vẫn là dấu hiệu đỏ —
lúc đó phải bắt chạy lại, không được tin miệng.

Phỏng theo claudekit-skills (MIT, mrgoonie) — đã Việt hoá và chỉnh theo luật kho web2api.
