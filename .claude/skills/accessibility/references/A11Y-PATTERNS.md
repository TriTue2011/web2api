# Mẫu mã khả năng tiếp cận

Các mẫu sẵn dùng cho yêu cầu tiếp cận thường gặp. Ví dụ mã giữ nguyên tiếng
Anh; chú thích giải thích bằng tiếng Việt.

## Bẫy focus trong hộp thoại

Giữ phím Tab xoay vòng trong hộp thoại, Esc đóng. (Phần tử `<dialog>` gốc đã tự
làm việc này — ưu tiên dùng nó.)

```javascript
function openModal(modal) {
  const focusableElements = modal.querySelectorAll(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
  );
  const firstElement = focusableElements[0];
  const lastElement = focusableElements[focusableElements.length - 1];

  modal.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') {
      // Tab ở phần tử cuối → quay về đầu; Shift+Tab ở đầu → nhảy xuống cuối
      if (e.shiftKey && document.activeElement === firstElement) {
        e.preventDefault();
        lastElement.focus();
      } else if (!e.shiftKey && document.activeElement === lastElement) {
        e.preventDefault();
        firstElement.focus();
      }
    }
    if (e.key === 'Escape') {
      closeModal();
    }
  });

  firstElement.focus();
}
```

## Liên kết bỏ qua

Cho người dùng bàn phím nhảy thẳng tới nội dung chính, bỏ qua điều hướng lặp lại.

```html
<body>
  <!-- Liên kết "bỏ qua tới nội dung chính" -->
  <a href="#main-content" class="skip-link">Skip to main content</a>
  <header><!-- navigation --></header>
  <main id="main-content" tabindex="-1">
    <!-- main content -->
  </main>
</body>
```

```css
.skip-link {
  position: absolute;
  top: -40px; /* ẩn khỏi tầm nhìn, hiện khi focus */
  left: 0;
  background: #000;
  color: #fff;
  padding: 8px 16px;
  z-index: 100;
}

.skip-link:focus {
  top: 0;
}
```

## Báo lỗi biểu mẫu

Báo lỗi cho trình đọc màn hình và focus vào ô lỗi đầu tiên khi gửi.

```html
<form novalidate>
  <div class="field" aria-live="polite">
    <label for="email">Email</label>
    <input type="email" id="email"
           aria-invalid="true"
           aria-describedby="email-error">
    <!-- role="alert": đọc ngay cho trình đọc màn hình -->
    <p id="email-error" class="error" role="alert">
      Please enter a valid email address (e.g., name@example.com)
    </p>
  </div>
</form>
```

```javascript
form.addEventListener('submit', (e) => {
  const firstError = form.querySelector('[aria-invalid="true"]');
  if (firstError) {
    e.preventDefault();
    firstError.focus(); // đưa focus tới lỗi đầu tiên
  }
});
```

## Nhãn cho ô nhập

Mỗi ô nhập cần nhãn gắn rõ — hoặc `for`/`id`, hoặc `<label>` bọc ngoài.

```html
<!-- SAI: không gắn nhãn -->
<input type="email" placeholder="Email">

<!-- ĐÚNG: nhãn gắn bằng for/id -->
<label for="email">Email address</label>
<input type="email" id="email" name="email"
       autocomplete="email" required>
```

## Kéo-thả có cách thay thế

Mọi thao tác kéo phải có cách làm bằng một lần bấm (WCAG 2.5.7).

```html
<!-- SAI: chỉ kéo mới sắp xếp được -->
<ul class="sortable-list" draggable="true">
  <li>Item 1</li>
</ul>

<!-- ĐÚNG: kéo + nút lên/xuống -->
<ul class="sortable-list">
  <li>
    <span>Item 1</span>
    <!-- aria-label tiếng Anh để trình đọc màn hình đọc đúng -->
    <button aria-label="Move Item 1 up">↑</button>
    <button aria-label="Move Item 1 down">↓</button>
  </li>
</ul>
```

## Vùng thông báo trực tiếp

`aria-live` báo nội dung đổi động cho trình đọc màn hình mà không cướp focus.
**Lưu ý luật XSS của kho:** mọi dữ liệu từ ngoài đưa vào đây phải escape trước.

```html
<!-- Cập nhật trạng thái (polite: đợi lúc đọc ngắt quãng) -->
<div aria-live="polite" aria-atomic="true" class="status"></div>

<!-- Báo khẩn (assertive: ngắt lời đang đọc) -->
<div role="alert" aria-live="assertive"></div>
```

```javascript
function showNotification(message, type = 'polite') {
  const container = document.getElementById(`${type}-announcer`);
  container.textContent = ''; // xoá trước để tin nhắn trùng vẫn được đọc lại
  requestAnimationFrame(() => {
    container.textContent = message; // textContent tự escape, an toàn hơn innerHTML
  });
}
```

## Phím tắt trình đọc màn hình

| Thao tác | VoiceOver (Mac) | NVDA (Windows) |
|---|---|---|
| Bật/tắt | ⌘ + F5 | Ctrl + Alt + N |
| Mục tiếp theo | VO + → | ↓ |
| Mục trước | VO + ← | ↑ |
| Kích hoạt | VO + Space | Enter |
| Liệt kê tiêu đề | VO + U, rồi mũi tên | H / Shift + H |
| Liệt kê liên kết | VO + U | K / Shift + K |
