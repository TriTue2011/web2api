#!/usr/bin/env bash
# Chặn container trình duyệt từ xa (web2api-trinh-duyet) chạm mạng LAN/mạng riêng
# và CHÍNH MÁY CHỦ.
#
# Bối cảnh: dich-vu/trinh-duyet/ chạy Chromium/Firefox trong container, giữ cookie
# các tài khoản đã đăng nhập. Compose cố ý KHÔNG đặt mạng `internal` (cần ra
# Internet), nên một trang độc/quảng cáo chạy JS có thể gọi http://<IP-LAN>:.../
# tới Home Assistant, router, gateway Docker... từ TRONG mạng nhà.
#
# Cách chặn: luật iptables/ip6tables của MÁY CHỦ, đánh theo DẢI ĐỊA CHỈ (không theo
# tên miền). Mọi luật do script tạo đều gắn nhãn comment `web2api-trinh-duyet`
# để cài/gỡ/kiểm idempotent mà không đụng luật của người khác. Hai mặt trận:
#   (a) chuỗi DOCKER-USER: lưu lượng container -> MẠNG KHÁC (đi qua FORWARD);
#   (b) chuỗi INPUT: lưu lượng container -> CHÍNH MÁY CHỦ (cổng 3030/3031/22/8099...
#       nghe 0.0.0.0, hoặc qua IP gateway của cầu) — KHÔNG đi qua DOCKER-USER.
#       Luật INPUT gắn theo GIAO DIỆN CẦU (-i <cầu của mạng>), không theo IP.
#
# Vì sao lõi web2api vẫn nói chuyện được với container trình duyệt: web2api được
# nối vào CHÍNH mạng này (docker network connect) nên lưu lượng lõi <-> trình duyệt
# đi TRONG cầu (L2 bridge) — qua chuỗi FORWARD, được ngoại lệ "trong mạng" của
# DOCKER-USER chấp nhận trước mọi luật chặn; không đi qua INPUT của máy chủ.
#
# Thứ tự luật DOCKER-USER (quan trọng, ngoại lệ đứng TRƯỚC lệnh chặn):
#   1. ACCEPT lưu lượng trong chính mạng của dịch vụ (lõi web2api được nối vào mạng
#      này bằng `docker network connect`; container <-> container đi qua cầu).
#      Đọc docker-compose.yml của dịch vụ: chỉ có MỘT service (trinh-duyet), không
#      có dịch vụ phụ nào khác, nên không cần ngoại lệ nào thêm ngoài DNS.
#   2. ACCEPT UDP/TCP cổng 53 tới gateway của mạng Docker (trình phân giải DNS).
#      Ghi chú: DNS mặc định của container (127.0.0.11) do Docker chặn ngay trong
#      netns của container, không đi qua DOCKER-USER; ngoại lệ này phòng trường
#      hợp container dùng gateway làm DNS.
#   3. REJECT từ mạng của dịch vụ tới các dải riêng tư/link-local/CGNAT/loopback
#      của máy chủ: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16,
#      100.64.0.0/10, 127.0.0.0/8 (và fc00::/7, fe80::/10, ::1/128 nếu mạng bật IPv6).
#
# Thứ tự luật INPUT (cài bằng -I vào ĐẦU chuỗi để đứng trước luật cho phép khác):
#   1. ACCEPT ESTABLISHED,RELATED từ cầu (trả lời cho kết nối do máy chủ khởi xướng
#      tới container, ví dụ kiểm tra từ máy chủ).
#   2. ACCEPT UDP/TCP cổng 53 tới gateway (phòng container dùng gateway làm DNS;
#      DNS mặc định 127.0.0.11 không đi tới đây).
#   3. REJECT tất cả còn lại từ cầu tới máy chủ.
#
# Cách dùng:
#   sudo ./scripts/chan-lan-trinh-duyet.sh            # cài luật (idempotent)
#   sudo ./scripts/chan-lan-trinh-duyet.sh --kiem     # chỉ kiểm tra, không đổi gì
#   sudo ./scripts/chan-lan-trinh-duyet.sh --go       # gỡ ĐÚNG các luật có nhãn
#   ./scripts/chan-lan-trinh-duyet.sh --dry-run       # in lệnh sẽ chạy, không làm gì
#
# Yêu cầu: quyền root (hoặc CAP_NET_ADMIN) khi cài/kiểm/gỡ; `docker` đọc được mạng
# `web2api-trinh-duyet`. KHÔNG hard-code dải mạng Docker: lấy từ
# `docker network inspect web2api-trinh-duyet`. Tên cầu lấy từ Options
# "com.docker.network.bridge.name", nếu không có thì dạng br-<12 ký tự đầu của Id>.
set -euo pipefail

NHAN="web2api-trinh-duyet"      # nhãn comment gắn trên mọi luật do script tạo
TEN_MANG="web2api-trinh-duyet"  # tên mạng Docker của dịch vụ trình duyệt
CHUOI="DOCKER-USER"             # chuỗi iptables của máy chủ mà Docker tôn trọng
CHUOI_VAO="INPUT"               # chuỗi cho lưu lượng container -> chính máy chủ

# Dải đích bị chặn: riêng tư / link-local / CGNAT / loopback của máy chủ.
DAI_CHAN_V4=(10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10 127.0.0.0/8)
DAI_CHAN_V6=(fc00::/7 fe80::/10 ::1/128)

CHEDO="cai"     # cai | kiem | go
KHO_CHAY=0      # --dry-run: chỉ in lệnh

loi() { printf 'loi: %s\n' "$*" >&2; }

dung() {
  printf 'Cách dùng: %s [--kiem | --go | --dry-run]\n' "$0"
  printf '  (mặc định)  cài luật chặn vào chuỗi DOCKER-USER và INPUT (idempotent)\n'
  printf '  --kiem       chỉ in trạng thái từng luật, thoát 0 nếu đủ, 1 nếu thiếu\n'
  printf '  --go         gỡ đúng các luật có nhãn `%s`, không đụng luật khác\n' "$NHAN"
  printf '  --dry-run    in các lệnh sẽ chạy, không chạm iptables/docker thật\n'
}

# Đọc subnet/gateway/IPv6/Id/tên cầu của mạng Docker. Thoát 3 nếu không đọc được.
doc_mang() {
  local ra
  if ! ra=$(docker network inspect "$TEN_MANG" \
      -f '{{(index .IPAM.Config 0).Subnet}}|{{(index .IPAM.Config 0).Gateway}}|{{.EnableIPv6}}|{{.Id}}|{{index .Options "com.docker.network.bridge.name"}}|{{(index .IPAM.Config 1).Subnet}}|{{(index .IPAM.Config 1).Gateway}}' 2>/dev/null); then
    loi "không đọc được mạng Docker '$TEN_MANG' (docker network inspect thất bại)"
    exit 3
  fi
  local -a truong=()
  IFS='|' read -ra truong <<< "$ra"
  SUBNET_V4="${truong[0]-}"; GATEWAY_V4="${truong[1]-}"; CO_IPV6="${truong[2]-}"
  local id_mang="${truong[3]-}" ten_cau_tho="${truong[4]-}"
  local subnet_v6_tho="${truong[5]-}" gateway_v6_tho="${truong[6]-}"
  if [[ ! "$SUBNET_V4" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$ ]]; then
    loi "subnet đọc được không hợp lệ: '$SUBNET_V4'"
    exit 3
  fi
  if [[ ! "$GATEWAY_V4" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    loi "gateway đọc được không hợp lệ: '$GATEWAY_V4'"
    exit 3
  fi
  # Tên cầu: ưu tiên Options "com.docker.network.bridge.name"; không có thì
  # dạng br-<12 ký tự đầu của Id> (quy ước đặt tên cầu của Docker).
  if [[ -n "$ten_cau_tho" && "$ten_cau_tho" != "<no value>" ]]; then
    CAU="$ten_cau_tho"
  elif [[ "$id_mang" =~ ^[0-9a-fA-F]{12} ]]; then
    CAU="br-${id_mang:0:12}"
  else
    loi "không xác định được tên cầu của mạng '$TEN_MANG' (thiếu com.docker.network.bridge.name, Id lạ: '$id_mang')"
    exit 3
  fi
  # Tên cầu đi thẳng vào tham số iptables: kiểm chặt để chặn chèn lệnh.
  # Tên giao diện Linux tối đa 15 ký tự (br- + 12 ký tự hex = 15).
  if [[ ! "$CAU" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,14}$ ]]; then
    loi "tên cầu đọc được không hợp lệ/không an toàn: '$CAU' — từ chối để tránh chèn tham số iptables"
    exit 3
  fi
  SUBNET_V6=""; GATEWAY_V6=""
  if [[ "$CO_IPV6" == "true" ]]; then
    if [[ "$subnet_v6_tho" == *":"* ]]; then
      SUBNET_V6="$subnet_v6_tho"
      # Gateway v6 có thể vắng: khi đó chỉ bỏ ngoại lệ DNS v6 (fail-closed),
      # vẫn chặn bình thường.
      if [[ "$gateway_v6_tho" == *":"* ]]; then
        GATEWAY_V6="$gateway_v6_tho"
      fi
    else
      loi "mạng bật IPv6 nhưng không tìm được subnet v6"
      exit 3
    fi
  fi
}

# Kiểm tra quyền thao tác iptables (bằng chính thao tác đọc; KHÔNG kiểm tra ở --dry-run).
kiem_quyen() {
  if ! iptables -L "$CHUOI" -n >/dev/null 2>&1; then
    loi "không đọc/ghi được iptables (cần root hoặc CAP_NET_ADMIN); chuỗi '$CHUOI' cũng có thể chưa tồn tại"
    exit 2
  fi
  if ! iptables -L "$CHUOI_VAO" -n >/dev/null 2>&1; then
    loi "không đọc/ghi được iptables (cần root hoặc CAP_NET_ADMIN); chuỗi '$CHUOI_VAO' không đọc được"
    exit 2
  fi
}

# Chạy một lệnh iptables/ip6tables với spec là chuỗi tham số cách nhau bằng khoảng trắng.
# (spec do chính script sinh, không chứa khoảng trắng; dùng read -ra để tách an toàn.)
chay() { # $1: iptables|ip6tables, $2: hành động, $3: chuỗi, $4: spec (có thể rỗng)
  local bin="$1" hanh_dong="$2" chuoi="$3" spec="${4-}"
  local -a tham_so=()
  if [[ -n "$spec" ]]; then
    read -ra tham_so <<< "$spec"
  fi
  "$bin" "$hanh_dong" "$chuoi" "${tham_so[@]}"
}

# Sinh danh sách spec luật v4 cho DOCKER-USER, mỗi dòng một spec, ĐÚNG thứ tự áp dụng.
sinh_luat_v4() {
  local dai
  printf -- '-s %s -d %s -j ACCEPT -m comment --comment %s\n' "$SUBNET_V4" "$SUBNET_V4" "$NHAN"
  printf -- '-s %s -d %s/32 -p udp --dport 53 -j ACCEPT -m comment --comment %s\n' "$SUBNET_V4" "$GATEWAY_V4" "$NHAN"
  printf -- '-s %s -d %s/32 -p tcp --dport 53 -j ACCEPT -m comment --comment %s\n' "$SUBNET_V4" "$GATEWAY_V4" "$NHAN"
  for dai in "${DAI_CHAN_V4[@]}"; do
    printf -- '-s %s -d %s -j REJECT -m comment --comment %s\n' "$SUBNET_V4" "$dai" "$NHAN"
  done
}

# Sinh danh sách spec luật v6 cho DOCKER-USER (rỗng nếu mạng không bật IPv6).
sinh_luat_v6() {
  [[ -n "${SUBNET_V6-}" ]] || return 0
  local dai
  printf -- '-s %s -d %s -j ACCEPT -m comment --comment %s\n' "$SUBNET_V6" "$SUBNET_V6" "$NHAN"
  if [[ -n "${GATEWAY_V6-}" ]]; then
    printf -- '-s %s -d %s/128 -p udp --dport 53 -j ACCEPT -m comment --comment %s\n' "$SUBNET_V6" "$GATEWAY_V6" "$NHAN"
    printf -- '-s %s -d %s/128 -p tcp --dport 53 -j ACCEPT -m comment --comment %s\n' "$SUBNET_V6" "$GATEWAY_V6" "$NHAN"
  fi
  for dai in "${DAI_CHAN_V6[@]}"; do
    printf -- '-s %s -d %s -j REJECT -m comment --comment %s\n' "$SUBNET_V6" "$dai" "$NHAN"
  done
}

# Sinh danh sách spec luật v4 cho INPUT: chặn container -> CHÍNH MÁY CHỦ.
# Gắn theo giao diện cầu (-i), không theo IP. Thứ tự: ESTABLISHED trước,
# rồi ngoại lệ DNS tới gateway, cuối cùng REJECT tất cả còn lại.
sinh_luat_vao_v4() {
  printf -- '-i %s -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT -m comment --comment %s\n' "$CAU" "$NHAN"
  printf -- '-i %s -d %s/32 -p udp --dport 53 -j ACCEPT -m comment --comment %s\n' "$CAU" "$GATEWAY_V4" "$NHAN"
  printf -- '-i %s -d %s/32 -p tcp --dport 53 -j ACCEPT -m comment --comment %s\n' "$CAU" "$GATEWAY_V4" "$NHAN"
  printf -- '-i %s -j REJECT -m comment --comment %s\n' "$CAU" "$NHAN"
}

# Sinh danh sách spec luật v6 cho INPUT (rỗng nếu mạng không bật IPv6).
sinh_luat_vao_v6() {
  [[ -n "${SUBNET_V6-}" ]] || return 0
  printf -- '-i %s -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT -m comment --comment %s\n' "$CAU" "$NHAN"
  if [[ -n "${GATEWAY_V6-}" ]]; then
    printf -- '-i %s -d %s/128 -p udp --dport 53 -j ACCEPT -m comment --comment %s\n' "$CAU" "$GATEWAY_V6" "$NHAN"
    printf -- '-i %s -d %s/128 -p tcp --dport 53 -j ACCEPT -m comment --comment %s\n' "$CAU" "$GATEWAY_V6" "$NHAN"
  fi
  printf -- '-i %s -j REJECT -m comment --comment %s\n' "$CAU" "$NHAN"
}

DEM_THEM=0  # đếm luật mới thêm trong chế độ cài

# Cài một danh sách spec (đọc từ stdin) vào chuỗi; kieu = A (nối cuối) hoặc
# I (chèn vào đầu). Với I, duyệt NGƯỢC để thứ tự cuối cùng đúng như sinh ra.
cai_danh_sach() { # $1: iptables|ip6tables, $2: chuỗi, $3: A|I
  local bin="$1" chuoi="$2" kieu="$3" spec
  local -a ds_spec=()
  while IFS= read -r spec; do
    [[ -n "$spec" ]] || continue
    ds_spec+=("$spec")
  done
  if [[ "$kieu" == "I" ]]; then
    local -a dao=() i
    for ((i=${#ds_spec[@]}-1; i>=0; i--)); do dao+=("${ds_spec[$i]}"); done
    ds_spec=("${dao[@]}")
  fi
  for spec in "${ds_spec[@]}"; do
    if ! chay "$bin" -C "$chuoi" "$spec" >/dev/null 2>&1; then
      chay "$bin" "-$kieu" "$chuoi" "$spec"
      DEM_THEM=$((DEM_THEM + 1))
      printf 'them: %s %s\n' "$chuoi" "$spec"
    fi
  done
}

che_do_cai() {
  DEM_THEM=0
  # Đọc stdin từ < <(...) để hàm chạy trong shell hiện tại (giữ được DEM_THEM).
  cai_danh_sach iptables "$CHUOI" A < <(sinh_luat_v4)
  cai_danh_sach iptables "$CHUOI_VAO" I < <(sinh_luat_vao_v4)
  if [[ -n "${SUBNET_V6-}" ]]; then
    if ! command -v ip6tables >/dev/null 2>&1; then
      loi "cảnh báo: mạng bật IPv6 nhưng không có ip6tables — BỎ QUA luật v6 (lỗ hổng IPv6 còn mở)"
    else
      cai_danh_sach ip6tables "$CHUOI" A < <(sinh_luat_v6)
      cai_danh_sach ip6tables "$CHUOI_VAO" I < <(sinh_luat_vao_v6)
    fi
  fi
  printf 'xong: đã thêm %d luật mới (chạy lại không sinh luật trùng)\n' "$DEM_THEM"
}

# Kiểm tra một danh sách spec (đọc từ stdin) có đủ trong chuỗi không.
kiem_danh_sach() { # $1: iptables|ip6tables, $2: chuỗi
  local bin="$1" chuoi="$2" spec
  while IFS= read -r spec; do
    [[ -n "$spec" ]] || continue
    if chay "$bin" -C "$chuoi" "$spec" >/dev/null 2>&1; then
      printf 'CO    [%s] %s\n' "$chuoi" "$spec"
    else
      printf 'THIEU [%s] %s\n' "$chuoi" "$spec"
      THIEU=$((THIEU + 1))
    fi
  done
}

che_do_kiem() {
  THIEU=0
  kiem_danh_sach iptables "$CHUOI" < <(sinh_luat_v4)
  kiem_danh_sach iptables "$CHUOI_VAO" < <(sinh_luat_vao_v4)
  if [[ -n "${SUBNET_V6-}" ]]; then
    if ! command -v ip6tables >/dev/null 2>&1; then
      printf 'BO QUA (thiếu ip6tables): luật v6\n'
    else
      kiem_danh_sach ip6tables "$CHUOI" < <(sinh_luat_v6)
      kiem_danh_sach ip6tables "$CHUOI_VAO" < <(sinh_luat_vao_v6)
    fi
  else
    printf '(mạng Docker không bật IPv6: bỏ qua luật v6)\n'
  fi
  if [[ "$THIEU" -gt 0 ]]; then
    printf 'kết luận: THIẾU %d luật\n' "$THIEU"
    return 1
  fi
  printf 'kết luận: đủ luật\n'
  return 0
}

# Gỡ một danh sách spec (đọc từ stdin) khỏi chuỗi, chỉ gỡ đúng spec có nhãn.
go_danh_sach() { # $1: iptables|ip6tables, $2: chuỗi
  local bin="$1" chuoi="$2" spec
  while IFS= read -r spec; do
    [[ -n "$spec" ]] || continue
    while chay "$bin" -C "$chuoi" "$spec" >/dev/null 2>&1; do
      chay "$bin" -D "$chuoi" "$spec"
      DEM_GO=$((DEM_GO + 1))
      printf 'go: %s %s\n' "$chuoi" "$spec"
    done
  done
}

che_do_go() {
  DEM_GO=0
  go_danh_sach iptables "$CHUOI" < <(sinh_luat_v4)
  go_danh_sach iptables "$CHUOI_VAO" < <(sinh_luat_vao_v4)
  if [[ -n "${SUBNET_V6-}" ]] && command -v ip6tables >/dev/null 2>&1; then
    go_danh_sach ip6tables "$CHUOI" < <(sinh_luat_v6)
    go_danh_sach ip6tables "$CHUOI_VAO" < <(sinh_luat_vao_v6)
  fi
  printf 'xong: đã gỡ %d luật có nhãn `%s` (luật khác giữ nguyên)\n' "$DEM_GO" "$NHAN"
}

che_do_kho_chay() {
  local spec
  printf '# Lệnh sẽ chạy ở chế độ cài (mạng %s, cầu %s):\n' "$TEN_MANG" "$CAU"
  printf '# --- chuỗi %s (lưu lượng container -> mạng khác) ---\n' "$CHUOI"
  while IFS= read -r spec; do
    [[ -n "$spec" ]] || continue
    printf 'iptables -A %s %s\n' "$CHUOI" "$spec"
  done < <(sinh_luat_v4)
  printf '# --- chuỗi %s (lưu lượng container -> chính máy chủ, theo giao diện cầu) ---\n' "$CHUOI_VAO"
  while IFS= read -r spec; do
    [[ -n "$spec" ]] || continue
    printf 'iptables -I %s %s\n' "$CHUOI_VAO" "$spec"
  done < <(sinh_luat_vao_v4)
  if [[ -n "${SUBNET_V6-}" ]]; then
    while IFS= read -r spec; do
      [[ -n "$spec" ]] || continue
      printf 'ip6tables -A %s %s\n' "$CHUOI" "$spec"
    done < <(sinh_luat_v6)
    while IFS= read -r spec; do
      [[ -n "$spec" ]] || continue
      printf 'ip6tables -I %s %s\n' "$CHUOI_VAO" "$spec"
    done < <(sinh_luat_vao_v6)
  else
    printf '# (mạng Docker không bật IPv6: không có lệnh ip6tables)\n'
  fi
}

# --- phân tích tham số ---
for ts in "$@"; do
  case "$ts" in
    --kiem) CHEDO="kiem" ;;
    --go) CHEDO="go" ;;
    --dry-run) KHO_CHAY=1 ;;
    -h|--help) dung; exit 0 ;;
    *) loi "tham số lạ: $ts"; dung >&2; exit 4 ;;
  esac
done

doc_mang

if [[ "$KHO_CHAY" -eq 1 ]]; then
  che_do_kho_chay
  exit 0
fi

kiem_quyen

case "$CHEDO" in
  cai) che_do_cai ;;
  kiem) che_do_kiem ;;
  go) che_do_go ;;
esac
