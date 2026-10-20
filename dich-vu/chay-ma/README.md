# Dịch vụ chay-ma (NV171)

Máy chủ chạy mã Python trong **container phụ dùng-một-lần cách ly** —
đây là bề mặt nguy hiểm nhất của hệ thống, mọi ràng buộc cách ly đều
mặc định BẬT và không tắt được qua cấu hình.

## Chạy

```sh
openssl rand -hex 32                                   # sinh CHAYMA_KHOA
CHAYMA_KHOA=<khoá> python3 server.py                   # nghe 127.0.0.1:8703
```

Triển khai bằng container: dùng compose MẪU cùng thư mục
(`docker-compose.dich-vu-rieng.yml`) và đọc `README-trien-khai.md`
trước — dịch vụ cầm `/var/run/docker.sock` (= quyền root máy chủ),
cổng chỉ publish ở `127.0.0.1`, **không bật mặc định**.

## API (khoá riêng qua header `Authorization: Bearer <CHAYMA_KHOA>`)

| Đường | Ghi chú |
|---|---|
| `GET /suc-khoe` | Không cần khoá, chỉ trả `{"ok": true}` |
| `POST /kiem-tinh` | Soi tĩnh, không chạy: `{ok, goi_y, ly_do, kiem_tinh}` (`goi_y` = gợi ý sớm, KHÔNG phải bằng chứng an toàn) |
| `POST /chay` | `{ok, da_chay, ly_do_bo_qua, kiem_tinh, ma_thoat, het_gio, stdout, stderr, chan_doan, bi_cat, tep_ra}` |

Thân yêu cầu `/chay`: `{ma, ngon_ngu: "python", han_giay: ≤60, tep_dau_vao: [{ten, noi_dung}]}`.

## Ràng buộc cách ly (không cấu hình được)

- Mỗi lượt chạy: `docker run --rm` dùng một lần, id ngẫu nhiên 8 byte hex.
- `--network none`; `--read-only` + `tmpfs /tmp:rw,noexec,nosuid,size=16m`;
  `--user 65534:65534`; `--cap-drop ALL`; `no-new-privileges`;
  `--cpus 1 --memory 512m --memory-swap 512m --pids-limit 64`;
  `--pull never`; bind-mount **duy nhất** thư mục tạm của lần chạy →
  `/lam-viec`; không mount dữ liệu lõi, không docker.sock, không thiết bị.
- Môi trường trong container là whitelist tối thiểu
  (`PATH, HOME, LANG, LC_ALL, PYTHONIOENCODING, PYTHONDONTWRITEBYTECODE`).
  Tiến trình docker client cũng chạy với env tối thiểu — bí mật của lõi
  (biến môi trường, khoá) không lọt vào môi trường chạy mã.
- **Gợi ý sớm** (tĩnh, trước khi gọi docker — KHÔNG phải ranh giới an
  toàn): mã có dấu hiệu `os.system`, `subprocess`, `socket`, `open("/"),
  `while true:`, `input(`, … hoặc cần dịch vụ nội bộ của c2a thì bỏ qua
  sớm cho khỏi tốn lượt chạy. Đây chỉ là danh sách dấu hiệu chuỗi, LÁCH
  ĐƯỢC (nối chuỗi/getattr/importlib) — mã lọt qua vẫn chỉ chạy trong
  container cách ly dưới đây; đừng bao giờ nới cờ container vì tin lớp
  này. (Đổi vai trò ở NV171-SUA C3; hàm là `goi_y_som`, tên cũ của c2a là
  `co_the_chay`.)
- Lớp thứ hai trong container: bootstrap đặt `RLIMIT_CPU`, `RLIMIT_AS`,
  `RLIMIT_FSIZE` sau exec; giờ TƯỜNG bằng `timeout -s KILL <han_giay>`
  bọc ngoài python3 (RLIMIT_CPU không tính thời gian ngủ).
- `--log-driver none`: daemon không ghi nhật ký container xuống đĩa
  máy chủ.
- Hết giờ/tràn/lỗi/xong: diệt cả process group của client, và `finally`
  LUÔN `docker kill` + `docker rm -f` container theo tên — không để
  container mồ côi (NV171-SUA C2). Đầu ra phun vượt trần cứng (2MB) thì
  diệt ngay, giữ lại ≤64KB/luồng.
- Thư mục lần chạy được `chown` sang uid 65534 + quyền `0o700`
  (NV171-SUA C4) để container ghi được tệp ra; KHÔNG bao giờ chmod 777.
- HTTP: xác thực Bearer TRƯỚC khi đọc thân (sai khoá → 401 ngay, đóng
  kết nối); socket có hết giờ (`CHAYMA_HET_GIO_SOCKET`, mặc định 20s) và
  trần kết nối đồng thời (`CHAYMA_KET_NOI_TOI_DA`, mặc định 32) —
  NV171-SUA C5.
- Tệp đầu vào: tên đơn đúng khuôn, ≤8 tệp × 64KB, resolve() dưới thư mục
  lần chạy; thu tệp ra **không đi theo symlink**.

## Mã nguồn port

Từ kho CỦA CHÍNH CHỦ MÁY `/opt/claude-c2a/chatgpt2api` (không còn trên VM,
dùng bản sao `~/workspace/c2a-nv146`, cùng commit d879b03):
`boc_code_python`, `co_the_chay` (đổi tên `goi_y_som` + hạ vai trò xuống
gợi ý sớm — NV171-SUA C3), `kiem_tinh`, `chan_doan_loi`, bootstrap
rlimit. KHÔNG port `chay()` chạy trực tiếp (thay bằng container cách ly),
KHÔNG port `code_pipeline.py` (agent-specific của c2a).

## Chưa kiểm

Chưa chạy với Docker thật (VM không có Docker daemon). Kịch bản cần chủ
máy chạy thủ công: fork bomb (`os.fork` chỉ bị gợi ý sớm bỏ qua; lớp
rlimit pids là chặn thật), đọc `/proc/1`, symlink ra ngoài, thử gọi mạng,
ghi tệp >64MB, leo `--privileged` qua tham số, tệp ra ghi bởi uid 65534
trên thư mục đã chown (C4), `timeout` coreutils có trong ảnh
`python:3.13-slim`.

## Test

```sh
python3 -m pytest dich-vu/chay-ma/ -q
```

Dùng docker **giả** (`ho_tro_test/docker_gia.py`) ghi lại argv/env nhận
được để test đọc lại từng cờ cách ly của lệnh mà dịch vụ sinh ra.
