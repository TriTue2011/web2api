# Triển khai dịch vụ chay-ma (NV171, bổ sung NV171-SUA C7)

Dịch vụ này **không bật mặc định** và không có đường tự bật: nó cần
`/var/run/docker.sock` — ai chiếm được dịch vụ là có **quyền root trên
máy chủ** (tạo container `--privileged` là xong). Chỉ triển khai khi chủ
máy đã hiểu điều đó và chấp nhận; bề mặt chạy mã thật vẫn bị nhốt trong
container dùng-một-lần cách ly (xem `README.md` cùng thư mục).

## Cách chạy (compose mẫu)

```sh
cd dich-vu/chay-ma
mkdir -p /srv/chay-ma-data                 # đường phải trùng trong/ngoài (xem dưới)
export CHAYMA_KHOA=$(openssl rand -hex 32) # khoá riêng của dịch vụ
docker compose -f docker-compose.dich-vu-rieng.yml up -d --build
curl -s http://127.0.0.1:8703/suc-khoe     # → {"ok": true}
```

Rồi trong web2api: Cài đặt module `chay-ma` → bật `chay_bat`,
`dich_vu_url = http://127.0.0.1:8703`, `khoa_dich_vu` = đúng
`CHAYMA_KHOA` ở trên.

## Vì sao compose mẫu trông như vậy

- **Cổng chỉ `127.0.0.1:8703`** trên máy chủ. Dịch vụ bên trong container
  nghe `0.0.0.0` là chuyện của mạng container; publish ra ngoài chỉ ở
  loopback. Đổi thành `8703:8703` là mở cổng chạy-mã-kèm-khoá ra cả LAN —
  không làm.
- **`user: "0:0"`** trong container dịch vụ: cần quyền đọc docker.sock và
  `chown` thư mục lần chạy sang uid 65534 (NV171-SUA C4 — container chạy
  mã ghi tệp ra vào thư mục đó). Đây là container RIÊNG chỉ chứa dịch
  vụ; container chạy mã của người dùng vẫn `--user 65534:65534` +
  `--cap-drop ALL` như cũ. Dockerfile giữ `USER chayma` cho cách chạy
  tay không qua compose (khi đó dịch vụ không chown được: tệp VÀO vẫn
  chạy, tệp RA không ghi được, log có cảnh báo rõ — xem C4).
- **`/srv/chay-ma-data` trùng đường hai phía**: daemon Docker diễn giải
  `-v <đường>` theo MÁY CHỦ, còn dịch vụ tạo thư mục theo đường trong
  container của nó. `CHAYMA_THU_MUC_GOC` và bind mount phải là cùng một
  chuỗi đường, nếu không tệp đầu vào/tệp ra đi hai nơi khác nhau.
- **`restart: "no"`**: dịch vụ không tự sống lại sau reboot — chủ máy
  chủ động bật lại sau khi đã kiểm tra máy.

## Thay thế an toàn hơn (chưa kiểm)

- Docker **rootless** hoặc một proxy socket chỉ cho phép
  `containers/create|start|kill|rm|wait` (vd docker-socket-proxy với
  quyền tối thiểu) sẽ thu hẹp «root máy chủ» xuống nhiều. Chưa kiểm trên
  máy thật nên chưa đưa vào mẫu; khi áp dụng, giữ nguyên hai ràng buộc
  cổng-loopback và đường-dữ-liệu-trùng ở trên.

## Kiểm sau khi dựng

1. `curl -s http://127.0.0.1:8703/suc-khoe` → `{"ok": true}`; gọi
   `POST /chay` không khoá → 401 ngay.
2. Chạy thử qua module: `print(1+1)` → stdout `2`; sau lượt chạy,
   `docker ps -a --filter name=chayma-` phải RỖNG (NV171-SUA C2: mọi
   đường thoát đều kill + rm, không container mồ côi).
3. Mã ngủ `import time; time.sleep(30)` với `han_giay=2` → trả `het_gio`
   sau ~2 giây (giờ tường trong container, NV171-SUA C2).
