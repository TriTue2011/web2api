# Cầu nối Antigravity (`agy`) cho web2api

Tiến trình nhỏ chạy **trên máy chủ** (ngoài container web2api), nói chuẩn OpenAI (`/v1/models`,
`/v1/chat/completions` có stream SSE, `/tai-khoan`) và dùng chính ứng dụng chính thức `agy` (Antigravity CLI) ở chế
độ không tương tác làm động cơ. Không gọi API Google trực tiếp, không giả danh ứng dụng, không đọc hay in token.

Vì sao ngoài container: `agy` nặng khoảng 214 MB, container web2api chỉ được cấp 256 MB RAM.

**Trạng thái:** chỉ mới kiểm bằng `agy` GIẢ (`agy_gia.py`, test ở `test_cau_noi.py`); **chưa chạy với `agy` thật**
(mỗi lượt tốn hạn mức tài khoản). Những điểm phụ thuộc hành vi thật của `agy` được ghi «chưa kiểm» bên dưới.

## Cần gì để chạy

- Python 3.13 với `fastapi`, `uvicorn` (đã có trong `requirements.txt` của kho — không thêm gói).
- `agy` đã cài và đã đăng nhập, mỗi tài khoản một thư mục HOME riêng.
- Nên chạy bằng một người dùng **không phải root**, và HOME của các tài khoản **không phải** `/root` hay HOME của
  người dùng thật: agent của `agy` có công cụ đọc tệp/chạy lệnh trong hộp cát của nó; HOME riêng thu hẹp thứ nó
  thấy được (chưa kiểm với `agy` thật hộp cát chặn tới đâu).

## Cấu hình `settings.json` của từng tài khoản

Trong mỗi HOME, tệp `$HOME/.gemini/antigravity-cli/settings.json` (do `agy` đọc — cầu nối KHÔNG mở thư mục này):

```json
{
  "toolPermission": "proceed-in-sandbox",
  "enableTerminalSandbox": true
}
```

- `toolPermission: "proceed-in-sandbox"`: công cụ cần quyền thì chạy trong hộp cát thay vì hỏi — ở chế độ `-p`
  không ai trả lời được câu hỏi quyền, và **một lần bị từ chối là dừng cả lượt** (cầu nối trả `502 agent_wanted_tool`).
- `enableTerminalSandbox: true`: hộp cát namespace Linux — không mạng, chỉ thấy thư mục làm việc, chặn `~/.ssh`, `.env`.

Email hiển thị ở `/tai-khoan` (dạng che `a…z@miền`) lấy từ tệp một dòng **do người vận hành tự ghi**:
`$HOME/.agy-cau-noi-email`. Không có tệp này thì `email` là `null`. Cầu nối không đoán email từ tệp của `agy`
(thư mục đó chứa token).

## Biến môi trường

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `AGY_CAU_NOI_KHOA` | *(bắt buộc)* | Khoá cho `Authorization: Bearer <khoá>`. Thiếu thì cầu nối không khởi động (chạy qua `uvicorn` thì mọi đường có khoá trả 500). Dùng chuỗi dài, ngẫu nhiên. |
| `AGY_HOMES` | *(rỗng)* | Các thư mục HOME của tài khoản, ngăn bằng `:`. |
| `AGY_BIN` | `agy` | Tệp thực thi `agy`. |
| `AGY_CAU_NOI_HOST` | `127.0.0.1` | Địa chỉ nghe (xem «Đăng ký vào web2api» khi web2api chạy trong Docker). |
| `AGY_CAU_NOI_PORT` | `8300` | Cổng nghe. |
| `AGY_TOI_DA_DONG_THOI` | `4` | Trần số lượt `agy` chạy cùng lúc trên mọi tài khoản. |
| `AGY_HAN_GIO_S` | `90` | Thời gian tối đa một lượt (truyền `--print-timeout`; quá 5 s nữa thì cầu nối giết cả nhóm tiến trình). Giữ dưới 120 s — thời gian chờ đọc của web2api. |
| `AGY_CHO_TAI_KHOAN_S` | `20` | Mọi tài khoản còn hạn mức đều đang bận thì chờ tối đa bấy nhiêu giây để có một cái rảnh, quá thì `503 accounts_busy`. |
| `AGY_THAN_TOI_DA_KB` | `4096` | Trần thân yêu cầu; vượt → `413`. |
| `AGY_PROMPT_TOI_DA_KB` | `120` | Trần hội thoại đã ghép (byte UTF-8); vượt → `400 context_length_exceeded`. Không nâng được quá 120 KiB: Linux giới hạn một tham số dòng lệnh ở 128 KiB. |
| `AGY_GIU_BIEN` | *(rỗng)* | Tên biến môi trường thêm cho phép truyền xuống `agy` (ngăn bằng dấu phẩy). Mặc định `agy` chỉ nhận `PATH`, `LANG`, `LC_*`, `TZ`, `USER`, `LOGNAME`, `TERM`, biến proxy và `HOME`; biến `AGY_CAU_NOI_*` không bao giờ được truyền. Chưa kiểm `agy` thật có cần thêm biến nào (vd keyring) không. |
| `AGY_TTL_MODELS_S` | `600` | Thời gian đệm danh mục model (`agy models`). |
| `AGY_TTL_USAGE_S` | `60` | Thời gian đệm hạn mức (`agy -p "/usage"`); cửa sổ đang cạn qua mốc reset thì đọc lại ngay. |

## Cách chạy

```bash
export AGY_CAU_NOI_KHOA="$(openssl rand -hex 24)"
export AGY_HOMES="/srv/agy-homes/acc1:/srv/agy-homes/acc2"
python3 dich-vu/antigravity-cau-noi/cau_noi.py
```

hoặc qua `uvicorn` (tên thư mục có dấu gạch ngang nên phải chỉ `--app-dir`):

```bash
uvicorn --app-dir dich-vu/antigravity-cau-noi cau_noi:app --host 127.0.0.1 --port 8300
```

## Cầu nối làm gì với một yêu cầu

1. Kiểm khoá (so sánh thời gian hằng), đọc thân có trần, kiểm hình dạng `messages`; id `model` phải **khớp đúng**
   danh mục của `agy models` (lạ → `404 model_not_found`, không chạy gì).
2. Ghép `messages` thành một prompt có nhãn vai (`[system]`, `[user]`…), đứng sau lời dặn «chỉ trả lời bằng văn
   bản, không dùng công cụ». Khối không phải chữ (ảnh…) bị bỏ, số khối bỏ báo ở tiêu đề `X-Cau-Noi-Bo-Khoi`.
3. Chọn tài khoản: họ model suy từ tiền tố id so với tên họ trong `/usage` (`gemini-…` ↔ «Gemini Models»,
   `claude-…`/`gpt-…` ↔ «Claude and GPT models»); họ đã biết lấy cửa sổ thấp nhất, id không khớp họ nào thì lấy cửa
   sổ cao nhất bất kỳ. Chọn tài khoản còn nhiều nhất, xoay vòng khi bằng nhau, mỗi tài khoản một lượt cùng lúc. Cạn
   hết → `429` kèm `Retry-After` tới mốc reset của **cửa sổ đang cạn**.
4. Chạy `agy -p <prompt> --model <id> --mode accept-edits --sandbox --output-format json --print-timeout <n>s` bằng
   `create_subprocess_exec` (không shell) trong thư mục tạm trống riêng, phiên tiến trình riêng, môi trường tối giản.
   Hết giờ, khách ngắt hay cầu nối huỷ lượt → SIGKILL **cả nhóm** tiến trình; thư mục tạm luôn được dọn.
5. Phân loại theo cấu trúc: hết giờ → `504 timeout`; mã thoát ≠ 0, JSON hỏng hay `status` ≠ `SUCCESS` → `502
   upstream_error`; `response` rỗng mà `denied_actions` khác rỗng → `502 agent_wanted_tool`. `usage` của `agy` →
   `usage` OpenAI. Stream: phát lại câu trả lời thành SSE chuẩn (chunk vai, các delta, `finish_reason: "stop"`,
   chunk `usage` khi khách xin `stream_options.include_usage`, `[DONE]`); trong lúc chờ `agy` gửi dòng chú thích
   `: …` mỗi 10 s để kết nối không bị coi là chết; lỗi sau khi đã bắt đầu stream → một sự kiện `{"error": …}`.

Nhật ký chỉ ghi số thứ tự tài khoản (`acc0`…), model và mã lỗi — không prompt, không đường HOME, không email;
stderr của `agy` bị bỏ (có thể chứa đường dẫn/email).

## Đăng ký vào web2api

1. Cài module `antigravity` ở Hệ thống → Module (cài không cần khoá).
2. Điền `dia_chi_cau_noi` và `khoa_cau_noi` (= `AGY_CAU_NOI_KHOA`) rồi «Kiểm tra và lưu»: web2api gọi `/health` và
   `/v1/models` của cầu nối.
3. Dùng model dạng `antigravity/<id>` (vd `antigravity/gemini-3.8-flash-high`).

**web2api chạy trong Docker** (mặc định của kho): trong container, `127.0.0.1` là **chính container**, không phải
máy chủ. Cho cầu nối nghe ở địa chỉ cổng nối Docker của máy chủ (`AGY_CAU_NOI_HOST=<IP cổng nối>`, xem bằng
`ip -4 addr show docker0` hoặc mạng của compose) và điền `http://<IP cổng nối>:8300` vào `dia_chi_cau_noi`. Đừng
nghe `0.0.0.0` khi máy chủ có cổng mở ra ngoài. Chưa kiểm cách này trên máy chủ thật.

## Chi phí và rủi ro

- **Hạn mức:** câu tối thiểu («Trả lời: OK») tốn khoảng **12.500 token vào** vì khung agent của `agy` nạp sẵn ngữ
  cảnh; một việc viết mã nhỏ có thể tới 290.000 token. Dùng làm **nguồn dự phòng**, không làm nguồn chat chính.
  Chưa đo `agy -p "/usage"` có tốn token không (cầu nối gọi nó tối đa mỗi `AGY_TTL_USAGE_S` giây mỗi tài khoản).
- **Ai giữ khoá cầu nối là điều khiển được một agent trên máy chủ** (`--mode accept-edits`, trong hộp cát). Lời dặn
  «không dùng công cụ» chỉ là lời dặn, không phải rào chắn — rào chắn là hộp cát của `agy`, môi trường tối giản và
  thư mục tạm trống. Giữ khoá như mật khẩu quản trị.
- **Tài khoản:** dùng CLI chính thức, không can thiệp token; gọi dồn dập vẫn làm cạn hạn mức tài khoản Google.
- **Chức năng:** chỉ chat văn bản; không function-calling chuẩn OpenAI; mỗi lượt một tiến trình (vài giây trở lên).
