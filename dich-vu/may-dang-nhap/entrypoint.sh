#!/bin/sh
set -e

# NV206: BIEN_THE chỉ là build-arg — ánh xạ sang CAPTCHA_SOLVER_BROWSER lúc chạy
# cho khớp với trình duyệt đã dựng trong ảnh (mặc định compose BIEN_THE=firefox).
# Người vận hành vẫn có thể ghi đè bằng biến môi trường.
if [ -z "$CAPTCHA_SOLVER_BROWSER" ]; then
    case "${BIEN_THE_ANH:-firefox}" in
        firefox) export CAPTCHA_SOLVER_BROWSER=firefox ;;
        *) export CAPTCHA_SOLVER_BROWSER=chromium ;;
    esac
fi

# Đảm bảo thư mục dữ liệu /data, thư mục khoá /khoa và thư mục tạm tồn tại
mkdir -p /data /khoa /tmp /run/supervisor
mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix
chmod 0700 /khoa /data 2>/dev/null || true

# noVNC/websockify/x11vnc mặc định KHÔNG chạy.
# Cờ bền /khoa/novnc-bat thắng biến môi trường sau khi công tắc được dùng.
# Khi chưa có cờ, CAPTCHA_SOLVER_BAT_NOVNC vẫn là mặc định tương thích.
# Cờ hỏng/không đọc được phải giữ tắt, cùng quy tắc với bộ quản lý runtime.
# Mật khẩu VNC CHỈ được đặt qua giao diện; entrypoint không đọc biến mật khẩu,
# không sinh mật khẩu và không ghi đè tệp cũ. x11vnc đọc /khoa/mat-khau-vnc.
CO_NOVNC="$(python3 -c '
from pathlib import Path
from src.novnc_bat_tat import doc_co
co = doc_co(Path("/khoa"))
print("" if co is None else "bat" if co else "tat")
')" || CO_NOVNC="tat"
if [ "$CO_NOVNC" = "bat" ]; then
    exec /usr/bin/supervisord -c /etc/supervisor/supervisord-novnc.conf
elif [ "$CO_NOVNC" = "tat" ]; then
    exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
elif [ "$CAPTCHA_SOLVER_BAT_NOVNC" = "1" ] || [ "$CAPTCHA_SOLVER_BAT_NOVNC" = "true" ]; then
    exec /usr/bin/supervisord -c /etc/supervisor/supervisord-novnc.conf
else
    exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
fi
