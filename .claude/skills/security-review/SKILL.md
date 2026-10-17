---
name: security-review
description: Soát bảo mật web2api theo LUẬT CỨNG ở CLAUDE.md mục «Bảo mật» — leo thang quyền/IDOR, XSS, SSRF, bí mật, mã bên thứ ba, cổng mạng. Dùng khi thêm đường API, chạm xác thực/phiên/proxy/giao diện, hoặc trước khi gộp commit.
---

# Soát bảo mật web2api

Luật ở `CLAUDE.md` mục «Bảo mật — LUẬT CỨNG (chủ kho chốt 13/10/2026)».
Vi phạm một điều là làm lại. Không bịa thêm luật mới — mọi mục dưới đây truy
được về luật đó hoặc mã kho.

## 1. Kiểm leo thang quyền, không chỉ bề mặt

Mọi đường/phương thức mới có xác thực **phải có test** bốn tình huống:

| Tình huống | Mong muốn |
|---|---|
| Không khoá | `401` |
| Khoá khách hoặc vai thấp gọi đường quản trị | `403` |
| Đụng tài nguyên của người/hồ sơ/phiên khác bằng cách đổi ID hoặc đường dẫn (IDOR) | `403`/`404` |
| Tài khoản mới tạo | không tự có quyền cao |

- 401 (ai gọi) và 403 (được làm gì) tách bạch, không trộn.
- «Đổi được mật khẩu admin» không chứng minh gì nếu không kiểm leo thang.

## 2. Mọi đường đều có xác thực; ngoại lệ phải có chủ đích

- Bearer/khoá **luôn bắt buộc**, dù chỉ một người dùng; không có chế độ
  «tắt khoá cho tiện» (CLAUDE.md cấm rõ).
- Đường mở không xác thực (hiện chỉ kiểm tra sức khoẻ và đổi vé) phải **ghi lý do
  ở đầu mã** và có test giới hạn (dùng một lần, hết hạn, không lộ lý do sai).

## 3. Giao diện chống XSS

- Không chèn dữ liệu từ ngoài vào `innerHTML`/thuộc tính sự kiện khi chưa escape.
- Trang trả về có CSP và header bảo mật (`app/tieu_de_bao_mat.py`).
- Cookie phiên: `HttpOnly`, `SameSite=Strict`, `Secure` khi qua HTTPS, giới hạn `Path`.

## 4. Bí mật không vào log hay phản hồi

- **Khoá API không bao giờ vào log hay phản hồi** — chỉ hiện dạng che (`…abcd`).
- Bí mật lưu ở `<WEB2API_DATA>/bi_mat/`, quyền `0600`.
- `app/che_log.py`: che bí mật trong dòng log trước khi ghi.
- `app/loi_openai.py::lam_sach_thong_diep`: làm sạch thông báo lỗi (cắt token
  liền mạch dài, bỏ ký tự điều khiển, giới hạn 400 ký tự) trước khi trả khách.
- Thêm đường API hay dòng log mới thì tự hỏi: **có thể lộ khoá không?**
- Kho PUBLIC: khoá API, khoá quản trị, token, IP nội bộ, tên thiết bị không bao
  giờ vào mã/test/ảnh tài liệu. Khoá đã commit là đã lộ dù xoá ở commit sau.

## 5. Mã bên thứ ba là KHÔNG TIN ĐƯỢC cho tới khi đọc

- Không `curl … | sh`; không cài gói/chạy mã xử lý cookie, khoá hay phiên khi
  **chưa đọc mã**.
- Gói mới vào `requirements*.txt` phải ghim đúng phiên bản và nêu lý do trong commit.
- Chạy `bandit` và gitleaks trước khi gộp.
- Module cài lúc chạy chỉ từ nguồn đã khai (xem `app/nguon_git.py`, `app/kham_pha.py`).

## 6. Cổng mạng

- Mặc định không công bố cổng ra ngoài: chỉ `127.0.0.1` hoặc mạng nội bộ Docker;
  không dựa vào UPnP.
- Dịch vụ phụ (cầu nối, trình duyệt từ xa) nghe nội bộ và có khoá riêng.
- Tài liệu triển khai (`docs/TRIEN-KHAI.md`) phải nói: SSH chỉ dùng tệp khoá,
  tắt đăng nhập bằng mật khẩu.

## 7. Chống SSRF và đường dẫn

- URL do quản trị/khách khai **phải qua bộ kiểm chung** `app/tu_xa.py::kiem_url_lan`
  — không tự kiểm lẻ ở từng chỗ.
- Đường tới tệp/hồ sơ ghép từ ID **đã kiểm** và sau `resolve()` phải nằm dưới
  thư mục gốc; ID không hợp lệ là 400, ID của người khác là 403/404.

## 8. Quy trình an toàn của commit

- Mỗi commit chạm xác thực, phiên, proxy, giao diện, cổng mạng hoặc mã bên thứ ba
  thêm **một đoạn «Bảo mật»** vào thông điệp: đường nào mở/không xác thực, tên
  test leo thang quyền/IDOR đã viết, phụ thuộc mới nào và đã đọc mã chưa.
- Trước khi gộp phải có **một lượt soát độc lập**, trọng tâm leo thang
  quyền/IDOR/XSS.
