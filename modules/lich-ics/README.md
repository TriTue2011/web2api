# Lịch ICS — đọc lịch, CHỈ ĐỌC (`lich-ics`)

Đọc lịch từ **link ICS bí mật** (Google Calendar, Outlook, Nextcloud…) và trả
**sự kiện sắp tới** cho trợ lý qua chuẩn C2 công cụ (`thiet_bi`). Port từ dịch vụ
`calendar_connector` của c2a (chỉ đọc). **Không ghi lịch, không CalDAV** — đợt
này chỉ đọc.

## Khả năng

- Công cụ `lich_ics.lich`, rủi ro `doc`, hai hành động:
  - `su_kien_sap_toi` — sự kiện trong N ngày tới (mặc định 7), tối đa K sự kiện
    (mặc định 8), chọn theo lịch (`lich` = id hoặc tên), luôn gắn
    `nguon_ngoai: true` (nội dung là dữ liệu ngoài, không phải chỉ dẫn).
  - `kiem_tra_lich` — tải lại ngay, báo số sự kiện trong tệp + xem trước.
- Nhiều lịch song song: trường «Tên các lịch» và «Đường dẫn ICS» cùng thứ tự.
- Tôn trọng **TZID** và UTC (`Z`), sự kiện cả ngày, **RRULE** (DAILY/WEEKLY/
  MONTHLY/YEARLY + INTERVAL/COUNT/UNTIL/BYDAY/BYMONTHDAY/EXDATE), giữ giờ địa
  phương qua chuyển DST. (Bản c2a bỏ qua TZID và coi UTC là giờ địa phương —
  port này sửa.)
- Bộ đệm theo từng lịch (mặc định 900 giây, tối thiểu 60); tải lỗi thì dùng bản
  đệm cũ thay vì làm rỗng lịch.

## Cấu hình chính

| Trường | Ý nghĩa |
|---|---|
| `ten_lich` | Tên từng lịch, ngăn cách bằng dấu phẩy/xuống dòng — cùng thứ tự với link. |
| `url_lich` | Link `.ics` bí mật, mỗi dòng một link (kiểu `danh_sach_bi_mat`). Cách lấy ghi ngay trong mô tả trường ở trang cài đặt. |
| `so_ngay_mac_dinh` | 1..31, mặc định 7. |
| `toi_da_mac_dinh` | 1..30, mặc định 8. |
| `cache_giay` | ≥ 60, mặc định 900. |
| `mui_gio` | Múi giờ nhà (IANA), mặc định `Asia/Ho_Chi_Minh`. |

Lúc «Cài đặt», module kiểm khuôn → kiểm SSRF bằng `app/tu_xa.kiem_url_lan` →
**tải thử từng lịch** (sai link/404/trang HTML đăng nhập đều bị từ chối với lý
do ngắn gọn, không lộ URL) và làm ấm bộ đệm.

## An toàn

- URL lịch chứa mã bí mật: lưu trong kho bí mật, **không API nào trả lại**,
  không vào log (một `logging.Filter` chặn mọi dòng log chứa URL thuộc **tên
  máy đã chuẩn hoá** của các URL lịch đang dùng và đã tải — gồm cả đích
  chuyển hướng; đăng ký trước cả lần tải thử đầu tiên; mẫu NV116: bản nạp
  lại dưới tên gói khác không cướp bộ lọc của bản cũ), không vào
  `suc_khoe`/nhật ký/phản hồi.
- Mọi URL được kiểm SSRF lúc cài **và ở mỗi bước chuyển hướng** (tối đa 5 bước)
  bằng đúng bộ kiểm của lõi — module không viết bộ kiểm thứ hai; kết nối thật
  **ghim IP** bằng `TransportGhimIp` của lõi (phân giải + kiểm IP ngay trước
  khi nối) nên không còn cửa sổ DNS rebinding giữa lúc kiểm và lúc nối.
- Nội dung ICS là dữ liệu không tin: trần tệp 2 MiB, trần 5000 sự kiện/tệp,
  trần mở rộng RRULE 500 mốc trả về/sự kiện (COUNT tính trên mốc thô trước
  EXDATE), trần tổng 20.000 mốc sinh cho một lần gọi công cụ, trần số kỳ sinh
  chống treo; mở rộng RRULE chạy ngoài vòng lặp sự kiện và **nhảy thẳng tới
  cửa sổ xem** (sự kiện lặp cũ không biến mất, COUNT vẫn áp đúng),
  RECURRENCE-ID loại buổi gốc đã bị dời; FREQ dưới DAILY («lặp mỗi giây»)
  không mở rộng; tiêu đề/địa điểm/mô tả lọc ký tự điều khiển và cắt theo độ
  dài; kết quả vận chuyển dạng JSON.
- Không có đường ghi nào: không tạo/sửa/xoá sự kiện, không CalDAV.

## Giới hạn / chưa kiểm

- Chưa kiểm với lịch Google/Outlook/Nextcloud thật (chỉ máy chủ ICS giả cục bộ).
- Chưa có giao diện web riêng; quản lý qua trang Module của lõi.
- RRULE hỗ trợ: BYMONTH, BYMONTHDAY (kể cả số âm), BYDAY có thứ tự
  («thứ Hai đầu tiên», «thứ Sáu cuối tháng»), BYSETPOS cho MONTHLY/YEARLY,
  UNTIL dạng ngày (hết ngày đó). Chưa hỗ trợ: BYHOUR/BYMINUTE/BYSECOND,
  BYWEEKNO, BYYEARDAY, BYEASTER, RDATE và RECURRENCE-ID với
  RANGE=THISANDFUTURE — sự kiện dùng các phần này vẫn hiện ở lần đầu và kết
  quả công cụ có `ghi_chu` nói rõ là quy tắc phức tạp chưa mở rộng được.
- Chưa đo RAM/đĩa thật (`tai_nguyen` để null).

Kiểm thử: `test/test_web2api_nv159_lich_ics.py` (45 test: múi giờ/DST/năm nhuận,
RRULE dị dạng, trần tệp/sự kiện/lặp, SSRF kể cả qua chuyển hướng, bí mật không
vào log, bộ lọc tồn tại sau nạp lại; 10 đột biến đều bị giết) và
`test/test_web2api_nv159_sua.py` (25 test của vòng sửa sau soát độc lập:
RRULE ác ý không treo được tiến trình, nhảy cửa sổ + đối chiếu hai đường mở
rộng, RECURRENCE-ID, BYMONTH/BYDAY-thứ-tự/BYSETPOS/UNTIL-ngày, che log theo
tên máy chuẩn hoá, ghim IP, trần tổng mốc).
