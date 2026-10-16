#!/usr/bin/env bash
# Chặn container trình duyệt từ xa (web2api-trinh-duyet) chạm mạng LAN/mạng riêng.
#
# Bối cảnh: dich-vu/trinh-duyet/ chạy Chromium/Firefox trong container, giữ cookie
# các tài khoản đã đăng nhập. Compose cố ý KHÔNG đặt mạng `internal` (cần ra
# Internet), nên một trang độc/quảng cáo chạy JS có thể gọi http://<IP-LAN>:.../
# tới Home Assistant, router, gateway Docker... từ TRONG mạng nhà.
#
# Cách chặn: luật iptables/ip6tables trong chuỗi DOCKER-USER của MÁY CHỦ, đánh theo
# DẢI ĐỊA CHỈ (không theo tên miền). Mọi luật do script tạo đều gắn nhãn comment
# `web2api-trinh-duyet` để cài/gỡ/kiểm idempotent mà không đụng luật của người khác.
#
# Thứ tự luật (quan trọng, ngoại lệ đứng TRƯỚC lệnh chặn):
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
# Cách dùng:
#   sudo ./scripts/chan-lan-trinh-duyet.sh            # cài luật (idempotent)
#   sudo ./scripts/chan-lan-trinh-duyet.sh --kiem     # chỉ kiểm tra, không đổi gì
#   sudo ./scripts/chan-lan-trinh-duyet.sh --go       # gỡ ĐÚNG các luật có nhãn
#   ./scripts/chan-lan-trinh-duyet.sh --dry-run       # in lệnh sẽ chạy, không làm gì
#
# Yêu cầu: quyền root (hoặc CAP_NET_ADMIN) khi cài/kiểm/gỡ; `docker` đọc được mạng
# `web2api-trinh-duyet`. KHÔNG hard-code dải mạng Docker: lấy từ
# `docker network inspect web2api-trinh-duyet`.
set -euo pipefail

NHAN="web2api-trinh-duyet"      # nhãn comment gắn trên mọi luật do script tạo
TEN_MANG="web2api-trinh-duyet"  # tên mạng Docker của dịch vụ trình duyệt
CHUOI="DOCKER-USER"             # chuỗi iptables của máy chủ mà Docker tôn trọng

# Dải đích bị chặn: riêng tư / link-local / CGNAT / loopback của máy chủ.
DAI_CHAN_V4=(10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10 127.0.0.0/8)
DAI_CHAN_V6=(fc00::/7 fe80::/10 ::1/128)

CHEDO="cai"     # cai | kiem | go
KHO_CHAY=0      # --dry-run: chỉ in lệnh

loi() { printf 'loi: %s\n' "$*" >&2; }

dung() {
  printf 'Cách dùng: %s [--kiem | --go | --dry-run]\n' "$0"
  printf '  (mặc định)  cài luật chặn vào chuỗi DOCKER-USER (idempotent)\n'
  printf '  --kiem       chỉ in trạng thái từng luật, thoát 0 nếu đủ, 1 nếu thiếu\n'
  printf '  --go         gỡ đúng các luật có nhãn `%s`, không đụng luật khác\n' "$NHAN"
  printf '  --dry-run    in các lệnh sẽ chạy, không chạm iptables/docker thật\n'
}

# Đọc subnet/gateway/IPv6 của mạng Docker. Thoát 3 nếu không đọc được.
doc_mang() {
  local ra
  if ! ra=$(docker network inspect "$TEN_MANG" \
      -f '{{(index .IPAM.Config 0).Subnet}}|{{(index .IPAM.Config 0).Gateway}}|{{.EnableIPv6}}' 2>/dev/null); then
    loi "không đọc được mạng Docker '$TEN_MANG' (docker network inspect thất bại)"
    exit 3
  fi
  SUBNET_V4="${ra%%|*}"; ra="${ra#*|}"
  GATEWAY_V4="${ra%%|*}"; CO_IPV6="${ra##*|}"
  if [[ ! "$SUBNET_V4" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$ ]]; then
    loi "subnet đọc được không hợp lệ: '$SUBNET_V4'"
    exit 3
  fi
  if [[ ! "$GATEWAY_V4" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    loi "gateway đọc được không hợp lệ: '$GATEWAY_V4'"
    exit 3
  fi
  SUBNET_V6=""; GATEWAY_V6=""
  if [[ "$CO_IPV6" == "true" ]]; then
    local v6
    v6=$(docker network inspect "$TEN_MANG" \
      -f '{{range .IPAM.Config}}{{if .Subnet}}{{.Subnet}}|{{.Gateway}};{{end}}{{end}}' 2>/dev/null || true)
    local muc
    while IFS= read -r muc; do
      [[ "$muc" == *":"* ]] || continue
      SUBNET_V6="${muc%%|*}"; GATEWAY_V6="${muc#*|}"; GATEWAY_V6="${GATEWAY_V6%;}"
      break
    done < <(tr ';' '\n' <<< "$v6")
    if [[ -z "$SUBNET_V6" ]]; then
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
}

# Chạy một lệnh iptables/ip6tables với spec là chuỗi tham số cách nhau bằng khoảng trắng.
# (spec do chính script sinh, không chứa khoảng trắng; dùng read -ra để tách an toàn.)
chay() { # $1: iptables|ip6tables, $2: hành động, $3: spec (có thể rỗng)
  local bin="$1" hanh_dong="$2" spec="${3-}"
  local -a tham_so=()
  if [[ -n "$spec" ]]; then
    read -ra tham_so <<< "$spec"
  fi
  "$bin" "$hanh_dong" "$CHUOI" "${tham_so[@]}"
}

# Sinh danh sách spec luật v4, mỗi dòng một spec, ĐÚNG thứ tự áp dụng.
sinh_luat_v4() {
  local dai
  printf -- '-s %s -d %s -j ACCEPT -m comment --comment %s\n' "$SUBNET_V4" "$SUBNET_V4" "$NHAN"
  printf -- '-s %s -d %s/32 -p udp --dport 53 -j ACCEPT -m comment --comment %s\n' "$SUBNET_V4" "$GATEWAY_V4" "$NHAN"
  printf -- '-s %s -d %s/32 -p tcp --dport 53 -j ACCEPT -m comment --comment %s\n' "$SUBNET_V4" "$GATEWAY_V4" "$NHAN"
  for dai in "${DAI_CHAN_V4[@]}"; do
    printf -- '-s %s -d %s -j REJECT -m comment --comment %s\n' "$SUBNET_V4" "$dai" "$NHAN"
  done
}

# Sinh danh sách spec luật v6 (rỗng nếu mạng không bật IPv6).
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

che_do_cai() {
  local spec dem_them=0
  while IFS= read -r spec; do
    [[ -n "$spec" ]] || continue
    if ! chay iptables -C "$spec" >/dev/null 2>&1; then
      chay iptables -A "$spec"
      dem_them=$((dem_them + 1))
      printf 'them: %s\n' "$spec"
    fi
  done < <(sinh_luat_v4)
  if [[ -n "${SUBNET_V6-}" ]]; then
    if ! command -v ip6tables >/dev/null 2>&1; then
      loi "cảnh báo: mạng bật IPv6 nhưng không có ip6tables — BỎ QUA luật v6 (lỗ hổng IPv6 còn mở)"
    else
      while IFS= read -r spec; do
        [[ -n "$spec" ]] || continue
        if ! chay ip6tables -C "$spec" >/dev/null 2>&1; then
          chay ip6tables -A "$spec"
          dem_them=$((dem_them + 1))
          printf 'them: %s\n' "$spec"
        fi
      done < <(sinh_luat_v6)
    fi
  fi
  printf 'xong: đã thêm %d luật mới (chạy lại không sinh luật trùng)\n' "$dem_them"
}

che_do_kiem() {
  local spec thieu=0
  while IFS= read -r spec; do
    [[ -n "$spec" ]] || continue
    if chay iptables -C "$spec" >/dev/null 2>&1; then
      printf 'CO    %s\n' "$spec"
    else
      printf 'THIEU %s\n' "$spec"
      thieu=$((thieu + 1))
    fi
  done < <(sinh_luat_v4)
  if [[ -n "${SUBNET_V6-}" ]]; then
    if ! command -v ip6tables >/dev/null 2>&1; then
      printf 'BO QUA (thiếu ip6tables): luật v6\n'
    else
      while IFS= read -r spec; do
        [[ -n "$spec" ]] || continue
        if chay ip6tables -C "$spec" >/dev/null 2>&1; then
          printf 'CO    %s\n' "$spec"
        else
          printf 'THIEU %s\n' "$spec"
          thieu=$((thieu + 1))
        fi
      done < <(sinh_luat_v6)
    fi
  else
    printf '(mạng Docker không bật IPv6: bỏ qua luật v6)\n'
  fi
  if [[ "$thieu" -gt 0 ]]; then
    printf 'kết luận: THIẾU %d luật\n' "$thieu"
    return 1
  fi
  printf 'kết luận: đủ luật\n'
  return 0
}

che_do_go() {
  local spec dem_go=0
  while IFS= read -r spec; do
    [[ -n "$spec" ]] || continue
    while chay iptables -C "$spec" >/dev/null 2>&1; do
      chay iptables -D "$spec"
      dem_go=$((dem_go + 1))
      printf 'go: %s\n' "$spec"
    done
  done < <(sinh_luat_v4)
  if [[ -n "${SUBNET_V6-}" ]] && command -v ip6tables >/dev/null 2>&1; then
    while IFS= read -r spec; do
      [[ -n "$spec" ]] || continue
      while chay ip6tables -C "$spec" >/dev/null 2>&1; do
        chay ip6tables -D "$spec"
        dem_go=$((dem_go + 1))
        printf 'go: %s\n' "$spec"
      done
    done < <(sinh_luat_v6)
  fi
  printf 'xong: đã gỡ %d luật có nhãn `%s` (luật khác giữ nguyên)\n' "$dem_go" "$NHAN"
}

che_do_kho_chay() {
  local spec
  printf '# Lệnh sẽ chạy ở chế độ cài (chuỗi %s, mạng %s):\n' "$CHUOI" "$TEN_MANG"
  while IFS= read -r spec; do
    [[ -n "$spec" ]] || continue
    printf 'iptables -A %s %s\n' "$CHUOI" "$spec"
  done < <(sinh_luat_v4)
  if [[ -n "${SUBNET_V6-}" ]]; then
    while IFS= read -r spec; do
      [[ -n "$spec" ]] || continue
      printf 'ip6tables -A %s %s\n' "$CHUOI" "$spec"
    done < <(sinh_luat_v6)
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
