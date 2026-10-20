# Dịch vụ phụ `zalo-ca-nhan` (Node) — cầu nối Zalo cá nhân

Dịch vụ Node chạy trong container phụ, kết nối Zalo bằng tài khoản CÁ NHÂN qua
thư viện `zca-js` (API **không chính thức** của Zalo, chép luồng từ c2a —
xem mục «Tận dụng từ c2a» trong báo cáo NV172 và lời dẫn đầu `zalo-client.js`),
phơi một HTTP nội bộ nhỏ cho module `modules/zalo-ca-nhan` của web2api.

> ⚠️ **CẢNH BÁO RỦI RO**: Zalo không cung cấp API này chính thức. Dùng dịch vụ
> này có thể bị Zalo **khoá tài khoản** bất cứ lúc nào; chủ máy tự chịu. Chỉ
> dùng tài khoản phụ, đừng dùng tài khoản chính/ngân hàng. Dịch vụ **mặc định
> không tự chạy gì** — phải đặt `ZALO_KHOA` và đăng nhập QR thì mới hoạt động.

## Hợp đồng HTTP (mọi route trừ trang QR đều cần `Authorization: Bearer <ZALO_KHOA>`)

| Route | Việc |
|---|---|
| `GET /suc-khoe` | `{ok, zca_san_sang, dang_nhap, so_tin_nhan, che_do}` — không chứa bí mật |
| `POST /tai-khoan/dang-nhap-qr` | Bắt đầu đăng nhập QR → `{ok, url_qr, het_han_giay: 120}` |
| `GET /qr/<token>` | Trang quét QR (HTML, CSP `default-src 'none'`); token trong URL chỉ dùng **một lần** |
| `GET /hop-thoai` | `[{thread_id, loai: 0\|1, ten}]` — bạn bè + nhóm của tài khoản |
| `POST /gui-tin` | `{thread_id, loai, van_ban, tep?: {ten, kieu, du_lieu_base64}}` — hạn tốc độ: ≥2 s/lần, ≤20/phút |
| `GET /tin-moi?tu=<seq>` | Đệm vòng 200 tin nhận mới nhất (chỉ tin của người khác) |
| `POST /dang-xuat` | Đăng xuất + xoá chứng chỉ lưu `cred_*.json` |

## Chạy

```bash
# Sinh khoá (tự đặt, ≥24 ký tự) — dùng CHUNG một giá trị cho container và module:
openssl rand -hex 32

docker build -t zalo-ca-nhan dich-vu/zalo-ca-nhan
docker run -d --name zalo-ca-nhan \
  -e ZALO_KHOA='<khoá vừa sinh>' \
  -v zalo-ca-nhan-data:/data \
  --network <mạng nội bộ của web2api> \
  zalo-ca-nhan
```

Dịch vụ chỉ nghe `127.0.0.1:8703` **trong container** (không publish cổng ra
ngoài); module web2api gọi qua mạng nội bộ Docker hoặc khi chạy cùng máy thì
qua cổng đã map nội bộ. Biến môi trường: `ZALO_KHOA` (bắt buộc, thiếu/ngắn thì
dịch vụ **từ chối khởi động**), `ZALO_PORT` (8703), `ZALO_HOST` (chỉ loopback),
`ZALO_DATA` (`/data`), `ZALO_GUI_CACH_GI_MS` (2000), `ZALO_GUI_TOI_DA_PHUT` (20).

## Đăng nhập

```bash
curl -s -X POST -H "Authorization: Bearer $ZALO_KHOA" http://127.0.0.1:8703/tai-khoan/dang-nhap-qr
# → {"ok":true,"url_qr":"/qr/<token>",...} — mở trang đó MỘT lần bằng trình duyệt
# trên máy nội bộ, quét bằng ứng dụng Zalo trong 120 giây.
```

Chứng chỉ phiên (`cred_<ownId>.json`: imei/cookie/userAgent) lưu trong `/data`
quyền 0600 — đây là **bí mật tương đương mật khẩu**: không sao chép ra ngoài,
không đưa vào log. Khởi động lại, dịch vụ tự khôi phục phiên từ chứng chỉ; hỏng
thì phải quét QR lại.

## Kiểm tra

```bash
npm test   # node --test, adapter GIẢ — không chạm Zalo thật
```

Tài nguyên: chưa đo RAM/đĩa thật (ghi `null` ở manifest module). **Chưa kiểm
với Zalo thật** trong môi trường nộp bài.
