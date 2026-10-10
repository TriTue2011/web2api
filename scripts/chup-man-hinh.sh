#!/usr/bin/env bash
# Chụp ảnh màn hình THẬT của web2api cho README — chạy lại bao nhiêu lần cũng được.
#
# Làm gì: dựng một lõi web2api tạm (uvicorn, thư mục dữ liệu tạm, cổng ngẫu nhiên — lõi RỖNG như
# container mới), chụp từng trang bằng Chrome headless ở hai khổ:
#   - máy tính 1280×800;
#   - điện thoại 390×844: Chrome headless=new không cho cửa sổ hẹp hơn 500 px (đo 10/10/2026: đặt
#     --window-size=390 vẫn ra innerWidth=500, bố cục điện thoại sai), nên trang được đặt trong
#     iframe rộng đúng 390 px — bên trong iframe innerWidth = 390 và media query chạy như điện thoại
#     thật — rồi cắt ảnh về 390×844.
# Lưu PNG vào docs/anh/<trang>-may-tinh.png và <trang>-dien-thoai.png, mỗi ảnh ≤ 250 KB
# (quá thì giảm màu), rồi tắt lõi tạm và xoá thư mục tạm.
#
# Dùng (từ gốc kho):  scripts/chup-man-hinh.sh
# Biến tuỳ chọn: CHROME (mặc định /usr/bin/google-chrome), PYTHON (mặc định .venv ở gốc kho, không có thì
# python3). Python đó cần fastapi, uvicorn, httpx (requirements.txt) và Pillow (requirements-dev.txt).
set -euo pipefail

GOC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # gốc kho web2api (tính theo vị trí script)
RA="$GOC/docs/anh"
CHROME="${CHROME:-/usr/bin/google-chrome}"
if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x "$GOC/.venv/bin/python" ]]; then PYTHON="$GOC/.venv/bin/python"; else PYTHON=python3; fi
fi
# Các trang chụp: "tên-ảnh|đường dẫn".
TRANG=("khoi-dau|/" "cai-module|/Cai-module.dc.html")

TAM="$(mktemp -d /tmp/web2api-chup.XXXXXX)"
PID=""
PID_KHUNG=""
don_dep() {
  for p in "$PID" "$PID_KHUNG"; do
    if [[ -n "$p" ]]; then kill "$p" 2>/dev/null || true; wait "$p" 2>/dev/null || true; fi
  done
  [[ "$TAM" == /tmp/web2api-chup.* ]] && rm -r -- "$TAM"
}
trap don_dep EXIT

CONG="$("$PYTHON" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
export WEB2API_DATA="$TAM/data"
export WEB2API_ADMIN_KEY="chup-man-hinh-$(date +%s%N)"
# Số của MÁY do người vận hành khai — đúng cách README khuyên (đọc /proc trong container giới hạn là sai).
export WEB2API_MAY_RAM_MB="${WEB2API_MAY_RAM_MB:-$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)}"
export WEB2API_MAY_CPU="${WEB2API_MAY_CPU:-$(nproc)}"
mkdir -p "$WEB2API_DATA" "$RA"
# Thư mục dữ liệu chỉ có MÃ module gemini-api (đúng trạng thái ngay sau khi bấm «Cài» ở thông báo git):
# trang Module hiện thẻ Gemini để cấu hình. Không có khoá API, không có cấu hình — không bí mật nào lên ảnh.
mkdir -p "$WEB2API_DATA/modules"
cp -R "$GOC/modules/gemini-api" "$WEB2API_DATA/modules/gemini-api"
# Không thăm dò GitHub thật khi chụp (ảnh phải lặp lại được, không phụ thuộc mạng).
unset WEB2API_GIT_REPO WEB2API_GIT_TOKEN

"$PYTHON" -m uvicorn app.main:app --app-dir "$GOC" --host 127.0.0.1 --port "$CONG" --log-level warning \
  >"$TAM/uvicorn.log" 2>&1 &
PID=$!
for _ in $(seq 1 100); do
  curl -sf --noproxy '*' "http://127.0.0.1:$CONG/health" >/dev/null && break
  sleep 0.1
done
curl -sf --noproxy '*' "http://127.0.0.1:$CONG/health" >/dev/null || { echo "Lõi tạm không khởi động:"; cat "$TAM/uvicorn.log"; exit 1; }

# Khung 390 px phải được phục vụ qua http://127.0.0.1 — mở bằng file:// thì Chrome chặn iframe gọi
# vào 127.0.0.1 (đo: ảnh trắng trơn), vì trang file:// không được coi là cùng mạng riêng.
mkdir -p "$TAM/khung"
CONG_KHUNG="$("$PYTHON" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
"$PYTHON" -m http.server "$CONG_KHUNG" --bind 127.0.0.1 --directory "$TAM/khung" >"$TAM/khung.log" 2>&1 &
PID_KHUNG=$!
for _ in $(seq 1 50); do
  curl -sf --noproxy '*' "http://127.0.0.1:$CONG_KHUNG/" >/dev/null && break
  sleep 0.1
done

chup() {  # chup <rộng> <cao> <url> <tệp ra>
  "$CHROME" --headless=new --no-sandbox --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
    --user-data-dir="$TAM/chrome" --window-size="$1,$2" --virtual-time-budget=6000 \
    --host-resolver-rules="MAP fonts.googleapis.com ~NOTFOUND, MAP fonts.gstatic.com ~NOTFOUND" \
    --screenshot="$4" "$3" >/dev/null 2>&1
}

for muc in "${TRANG[@]}"; do
  ten="${muc%%|*}"; duong="${muc#*|}"
  url="http://127.0.0.1:$CONG$duong"
  chup 1280 800 "$url" "$RA/$ten-may-tinh.png"
  cat >"$TAM/khung/$ten.html" <<EOF
<!doctype html><html><body style="margin:0;background:#fff"><iframe src="$url" style="width:390px;height:844px;border:0;display:block"></iframe></body></html>
EOF
  chup 500 844 "http://127.0.0.1:$CONG_KHUNG/$ten.html" "$TAM/$ten-dien-thoai.png"
  "$PYTHON" - "$TAM/$ten-dien-thoai.png" "$RA/$ten-dien-thoai.png" <<'EOF'
import sys
from PIL import Image
Image.open(sys.argv[1]).crop((0, 0, 390, 844)).save(sys.argv[2], optimize=True)
EOF
done

# Trần 250 KB/ảnh: quá thì giảm về bảng 256 màu (chữ vẫn nét, nền phẳng nén tốt).
"$PYTHON" - "$RA" <<'EOF'
import sys
from pathlib import Path
from PIL import Image
TRAN = 250 * 1024
for p in sorted(Path(sys.argv[1]).glob("*.png")):
    if p.stat().st_size > TRAN:
        Image.open(p).convert("RGB").quantize(256).save(p, optimize=True)
    co = p.stat().st_size
    print(f"{p.name}: {Image.open(p).size[0]}x{Image.open(p).size[1]}, {co // 1024} KB" + ("  << VƯỢT 250 KB" if co > TRAN else ""))
    if co > TRAN:
        sys.exit(1)
EOF
