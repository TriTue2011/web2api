#!/bin/sh
set -e

# Đảm bảo thư mục dữ liệu /data, thư mục khoá /khoa và thư mục tạm tồn tại
mkdir -p /data /khoa /tmp /run/supervisor
mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix
chmod 0700 /khoa /data 2>/dev/null || true

# noVNC/websockify/x11vnc mặc định KHÔNG chạy.
# Chỉ khi CAPTCHA_SOLVER_BAT_NOVNC=1 mới sinh mật khẩu VNC vào /khoa/mat-khau-vnc và chạy supervisord-novnc.conf.
# Giao thức RFB chỉ dùng 8 ký tự đầu nên sinh đúng 8 ký tự ngẫu nhiên A-Za-z0-9 (~47 bit), không in ra stdout.
if [ "$CAPTCHA_SOLVER_BAT_NOVNC" = "1" ] || [ "$CAPTCHA_SOLVER_BAT_NOVNC" = "true" ]; then
    if [ ! -f /khoa/mat-khau-vnc ]; then
        umask 077
        tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 8 > /khoa/mat-khau-vnc
        chmod 0600 /khoa/mat-khau-vnc
    fi
    exec /usr/bin/supervisord -c /etc/supervisor/supervisord-novnc.conf
else
    exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
fi

