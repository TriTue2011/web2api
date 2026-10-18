# Thông báo bên thứ ba

Thư mục `.claude/skills/` của kho này chứa tài liệu được phỏng theo năm nguồn mở
dưới đây. Bản phỏng theo đã được Việt hoá, rút gọn và chỉnh cho khớp luật của kho
web2api; nội dung gốc thuộc về tác giả của chúng theo giấy phép của từng nguồn.

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

## 4. impeccable

- Nguồn: https://github.com/pbakaus/impeccable
- Trích tại commit: `08d5b470495d`
- Giấy phép: Apache License 2.0
- Dùng cho: skill `adapt` — đã **Việt hoá và chỉnh sửa** (rút gọn từ 318 dòng còn
  ~90 dòng; chỉ giữ phần rà soát giao diện web trên điện thoại/máy tính bảng;
  bỏ phần email, in ấn, native và phần «thiết kế lại cho ngữ cảnh mới» vì kho
  web2api chỉ có giao diện web và giao diện phải giống y hệt demo).
- Dòng bản quyền nguyên văn của họ: `Copyright 2025 Paul Bakaus`
- Toàn văn giấy phép giữ tại `.claude/skills/adapt/LICENSE-Apache-2.0.txt`.

Đoạn thông báo bản quyền/giấy phép cần giữ (nguyên văn, từ tệp LICENSE-impeccable
của họ):

```
Copyright 2025 Paul Bakaus

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

## 5. web-quality-skills

- Nguồn: https://github.com/addyosmani/web-quality-skills
- Trích tại commit: `afa8da942115`
- Giấy phép: MIT
- Dòng bản quyền nguyên văn của họ: `Copyright (c) 2026 Addy Osmani`
- Dùng cho: skill `accessibility` và `references/` — đã **Việt hoá và chỉnh sửa**
  (SKILL.md rút từ 464 dòng còn ~110 dòng; references Việt hoá, ví dụ mã giữ
  nguyên tiếng Anh kèm chú thích tiếng Việt; lệnh `npx lighthouse` /
  `npm install @axe-core/cli` chỉ giữ dạng ví dụ kèm ghi chú kho không cài gói mới).
- Toàn văn giấy phép giữ tại `.claude/skills/accessibility/LICENSE-MIT.txt`.

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
