# Dịch vụ chay-ma (NV171)

Máy chủ chạy mã Python trong **container phụ dùng-một-lần cách ly** —
đây là bề mặt nguy hiểm nhất của hệ thống, mọi ràng buộc cách ly đều
mặc định BẬT và không tắt được qua cấu hình.

## Chạy

```sh
openssl rand -hex 32                                   # sinh CHAYMA_KHOA
CHAYMA_KHOA=<khoá> python3 server.py                   # nghe 127.0.0.1:8703
docker compose -f ../docker-compose.dich-vu-rieng.yml up chay-ma
```

## API (khoá riêng qua header `Authorization: Bearer <CHAYMA_KHOA>`)

| Đường | Ghi chú |
|---|---|
| `GET /suc-khoe` | Không cần khoá, chỉ trả `{"ok": true}` |
| `POST /kiem-tinh` | Soi tĩnh, không chạy: `{ok, co_the_chay, ly_do, kiem_tinh}` |
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
- Cổng mã nguy hiểm (tĩnh, trước khi gọi docker): `os.system`,
  `subprocess`, `socket`, `open("/"), `while true:`, `input(`, … — bị bỏ
  qua và không chạm tới docker.
- Lớp thứ hai trong container: bootstrap đặt `RLIMIT_CPU`, `RLIMIT_AS`,
  `RLIMIT_FSIZE` sau exec.
- Hết giờ: diệt cả process group của client + `docker kill <name>` dự phòng;
  đầu ra phun vượt trần cứng (2MB) thì diệt ngay, giữ lại ≤64KB/luồng.
- Tệp đầu vào: tên đơn đúng khuôn, ≤8 tệp × 64KB, resolve() dưới thư mục
  lần chạy; thu tệp ra **không đi theo symlink**.

## Mã nguồn port

Từ kho CỦA CHÍNH CHỦ MÁY `/opt/claude-c2a/chatgpt2api` (không còn trên VM,
dùng bản sao `~/workspace/c2a-nv146`, cùng commit d879b03):
`boc_code_python`, `co_the_chay`, `kiem_tinh`, `chan_doan_loi`, bootstrap
rlimit. KHÔNG port `chay()` chạy trực tiếp (thay bằng container cách ly),
KHÔNG port `code_pipeline.py` (agent-specific của c2a).

## Chưa kiểm

Chưa chạy với Docker thật (VM không có Docker daemon). Kịch bản cần chủ
máy chạy thủ công: fork bomb (`os.fork` bị chặn tĩnh; lớp rlimit pids),
đọc `/proc/1`, symlink ra ngoài, thử gọi mạng, ghi tệp >64MB, leo
`--privileged` qua tham số.

## Test

```sh
python3 -m pytest dich-vu/chay-ma/ -q
```

Dùng docker **giả** (`ho_tro_test/docker_gia.py`) ghi lại argv/env nhận
được để test đọc lại từng cờ cách ly của lệnh mà dịch vụ sinh ra.
