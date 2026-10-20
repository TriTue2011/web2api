# Module nhin-nha — Nhìn nhà

Thị giác nhà cho web2api: phát hiện vật thể (YOLO) và nhận diện khuôn mặt
trên ảnh camera/ảnh tải lên, qua **dịch vụ ML phụ** chạy trong máy nhà
(`dich-vu/nhin-nha/`). Module này là *client + sổ khuôn mặt* — phần ML nặng
không nằm trong lõi.

## Cấu trúc

- `module.py` — client HTTP (Bearer, chống SSRF), 3 thực thể chuẩn `thiet_bi`,
  sổ khuôn mặt (đọc/ghi qua kho riêng của lõi).
- `so_mat.py` — logic thuần port từ c2a `services/so_mat_nha.py`: cách nhận
  IRIS (cosine, ngưỡng tự chỉnh theo dữ liệu nhà), kiểm tra tên/chất lượng.

## Thực thể

| Thực thể | Tên | Rủi ro | Hành động |
|---|---|---|---|
| `nhin_nha.mat` | Mắt nhà | doc | `quet_anh`, `trang_thai_mo_hinh` |
| `nhin_nha.so_mat` | Sổ khuôn mặt | nguy_hiem | `danh_sach_nguoi`, `day_mat`, `doi_ten`, `xoa_nguoi`, `xoa_tat_ca`, `danh_sach_mat_la`, `dat_ten_mat_la`, `bo_qua_mat_la`, `su_kien_gan` |
| `nhin_nha.nhan_dien` | Nhận diện khuôn mặt | nguy_hiem | `nhan_mat_trong_anh` (chỉ khi bật `nhan_mat_bat`) |

## Hàng rào sinh trắc (bắt buộc, không tắt được)

- Dữ liệu embedding/tên/ảnh mặt chỉ nằm trong kho riêng của module — mã hoá
  khi nghỉ nếu lõi có khoá chính, không thì 0600.
- KHÔNG log embedding/ảnh/tên. Vector không bao giờ xuất hiện trong phản hồi
  của module hay `/suc-khoe`.
- Dịch vụ chỉ qua `dich_vu_url` bị giới hạn vòng lặp trong + mạng nhà riêng
  (loopback/RFC1918) — không có đường gửi ra dịch vụ ngoài lõi/máy nhà.
- Nhận diện khuôn mặt **mặc định TẮT** (`nhan_mat_bat=0`): bật phải xác nhận
  cấp quản trị theo cổng nguy_hiem của lõi + ghi nhật ký.
- Xoá: `xoa_nguoi` (một người, sự kiện giữ nhưng mất tên) và `xoa_tat_ca`.
- Kết quả nhận sai **không** được dùng để mở/khoá cửa hay hành động nguy
  hiểm — module chỉ là sổ.

## Cách bật

1. Chạy dịch vụ: xem `dich-vu/nhin-nha/README.md` (tải model có kiểm
   sha256, đặt `NHINNHA_KHOA`).
2. Cài module: `nhin_nha_bat=1`, `dich_vu_url=http://127.0.0.1:8703`,
   `khoa_dich_vu=<cùng khoá với NHINNHA_KHOA>`.
3. Muốn nhận diện: thêm `nhan_mat_bat=1` (ghi nhật ký), dạy mặt bằng
   `day_mat` với ảnh rõ, một mặt.

## Nguồn

Port từ c2a commit `d879b03` — chi tiết ở đầu `module.py` + `so_mat.py`.
Phần không port: canh camera liên tục, gom cụm mặt lạ tự động theo lượt,
đổi bộ model (phải dạy lại sổ).
