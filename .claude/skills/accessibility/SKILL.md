---
name: accessibility
description: Soát khả năng tiếp cận giao diện web2api theo WCAG 2.2 mức AA — bốn nguyên tắc POUR, bàn phím, tương phản, nhãn, vùng chạm. Dùng khi kiểm giao diện mới/sửa hoặc rà soát toàn trang; không dùng để thiết kế lại.
---

# Khả năng tiếp cận (a11y) theo WCAG 2.2 AA

«Chỉ rà soát và sửa lỗi; không đổi diện mạo so với demo của chủ kho.»

Đây là **phiếu rà soát**, không phải hướng dẫn thiết kế lại. Mục tiêu: giao diện
web2api dùng được bằng bàn phím, trình đọc màn hình và trên điện thoại — giữ nguyên
diện mạo demo hiện hành.

## Bốn nguyên tắc POUR

- **P**erceivable (nhận biết được): nội dung cảm nhận được qua nhiều giác quan.
- **O**perable (vận hành được): mọi người đều thao tác được giao diện.
- **U**nderstandable (hiểu được): nội dung và giao diện dễ hiểu.
- **R**obust (vững chắc): nội dung chạy được với công nghệ hỗ trợ.

Mức đích của kho: **AA** (A bắt buộc, AA nên đạt, AAA không bắt buộc).

## 1. Nhìn thấy được (Perceivable)

- Ảnh phải có `alt` mô tả; ảnh trang trí để `alt=""`.
- Nút chỉ có biểu tượng phải có tên đọc được (`aria-label` hoặc chữ ẩn
  `.visually-hidden`).
- Tương phản chữ: chữ thường ≥ 4.5:1, chữ lớn ≥ 3:1; viền nút/ô nhập ≥ 3:1.
- Không dùng **màu sắc làm kênh duy nhất** báo lỗi/trạng thái — kèm thêm biểu
  tượng hoặc chữ.
- Vùng focus khi dùng bàn phím phải nhìn thấy rõ (`:focus-visible`).

## 2. Vận hành được (Operable)

- **Mọi chức năng dùng được bằng bàn phím**: ưu tiên phần tử gốc
  (`<button>`, `<a href>`, ô nhập) — chúng đã có sẵn phím Enter/Space và ngữ nghĩa.
- Không bẫy bàn phím: Tab vào được thì Tab ra được.
- Vùng chạm tối thiểu **24×24px** (AA); khuyến nghị 44×44px trên điện thoại
  (xem skill `adapt`).
- Thao tác kéo-thả phải có cách thay thế bằng một lần chạm/bấm.
- Tôn trọng `prefers-reduced-motion`: tắt hoạt ảnh khi người dùng yêu cầu.

## 3. Hiểu được (Understandable)

- Trang khai `lang` đúng (`<html lang="vi">` cho giao diện tiếng Việt).
- Điều hướng nhất quán giữa các trang; nút/trợ giúp cùng vị trí tương đối.
- Mọi ô nhập có nhãn gắn bằng `for`/`id`; lỗi nhập báo bằng `role="alert"` hoặc
  `aria-live`, đặt `aria-invalid="true"` và focus vào lỗi đầu tiên.
- Không bắt nhập lại thông tin đã nhập trong cùng phiên (trừ xác nhận bảo mật).

## 4. Vững chắc (Robust)

- Ưu tiên phần tử HTML gốc thay vì `div` gắn `role` thủ công.
- Dùng `aria-live` cho nội dung đổi động (thông báo trạng thái) mà không cướp focus.
- Mọi dữ liệu từ ngoài chèn vào giao diện phải **escape** trước khi hiển thị
  (luật XSS của kho) — kể cả nội dung trong thông báo `aria-live`.

## Kiểm bằng công cụ (ví dụ)

> Ghi chú: kho này **KHÔNG cài gói mới** (luật cứng 4); chỉ chạy khi chủ kho cho
> phép. Ưu tiên Chrome DevTools → Lighthouse có sẵn trong `google-chrome`.

```bash
# Ví dụ: kiểm Lighthouse bằng Chrome có sẵn (không cài thêm)
google-chrome --headless --dump-dom https://localhost:PORT/trang

# Ví dụ minh hoạ (chỉ chạy khi chủ kho cho phép cài gói):
# npx lighthouse https://example.com --only-categories=accessibility
# npm install @axe-core/cli -g && axe https://example.com
```

Công cụ tự động chỉ bắt được một phần: điểm 100 không đồng nghĩa đạt WCAG,
điểm thấp không thay thế kiểm thủ công bằng bàn phím và trình đọc màn hình.

## Danh sách kiểm nhanh

- [ ] Tab hết trang bằng bàn phím, Enter/Space kích hoạt được mọi nút.
- [ ] Ảnh có `alt`, nút biểu tượng có tên đọc được.
- [ ] Tương phản chữ và viền đạt mức AA.
- [ ] Ô nhập có nhãn; lỗi báo rõ và focus đúng chỗ.
- [ ] Phóng 200% vẫn dùng được; không cuộn ngang ở 320px.
- [ ] Vùng chạm ≥ 24px (≥ 44px trên điện thoại).

Chi tiết mã mẫu: `references/A11Y-PATTERNS.md`. Bảng tiêu chí WCAG 2.2:
`references/WCAG.md`.
