# Thông báo bên thứ ba

Thư mục `.claude/skills/` của kho này chứa tài liệu được phỏng theo ba nguồn mở
dưới đây. Bản phỏng theo đã được Việt hoá, rút gọn và chỉnh cho khớp luật của kho
web2api; nội dung gốc thuộc về tác giả của chúng theo giấy phép MIT.

## 1. claudekit-skills

- Nguồn: https://github.com/mrgoonie/claudekit-skills
- Trích tại commit: `80113d8`
- Giấy phép: MIT (theo `marketplace.json` của họ)
- Dùng cho: bốn skill `debugging`, `problem-solving`, `code-review`, `mermaid-diagram`
  (gốc tên `mermaidjs-v11`).

## 2. ag-kit

- Nguồn: https://github.com/vudovn/ag-kit
- Trích tại commit: `549ba3d`
- Giấy phép: MIT
- Dòng bản quyền nguyên văn của họ: `Copyright (c) 2026 VUDOVN`
- Dùng cho: ý điều phối trong `docs/DIEU-PHOI-AGENT.md`
  (trích từ `orchestrator.md`, `coordinator-mode`, `coordinate.md`, `parallel-agents`).

## 3. ecc

- Nguồn: https://github.com/affaan-m/ecc (tên cũ ECC)
- Trích tại commit: `76aaa77`, bản `2.2.3`
- Giấy phép: MIT
- Dòng bản quyền nguyên văn của họ: `Copyright (c) 2026 Affaan Mustafa`
- Dùng cho: năm skill `fastapi-patterns`, `python-testing`, `security-review`,
  `verification-loop`, `api-design` — đã **dịch và rút gọn** (mỗi skill ≤ 150
  dòng so với bản gốc 135–800 dòng), bỏ phần không khớp kho (Next.js/npm/Supabase/
  Vercel, SQLAlchemy/Alembic/JWT mẫu), viết lại theo luật cứng của kho ở CLAUDE.md.

Nội dung giấy phép MIT của họ (nguyên văn, tiếng Anh):

```
MIT License

Copyright (c) 2026 Affaan Mustafa

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Giấy phép MIT (tóm tắt)

Được phép dùng, sao chép, sửa đổi và phân phối lại, kể cả trong sản phẩm thương mại,
với điều kiện giữ lại thông báo bản quyền và giấy phép này trong mọi bản sao
hoặc phần trích đáng kể. Phần mềm được cung cấp «nguyên trạng», không bảo hành.
