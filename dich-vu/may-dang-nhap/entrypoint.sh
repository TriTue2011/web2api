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
# Chỉ khi CAPTCHA_SOLVER_BAT_NOVNC=1 mới chạy supervisord-novnc.conf.
# Giao thức RFB chỉ dùng 8 ký tự đầu nên sinh đúng 8 ký tự ngẫu nhiên A-Za-z0-9 (~47 bit), không in ra stdout.
# NV305: tệp mật khẩu ĐÃ CÓ thì GIỮ NGUYÊN — mật khẩu chủ máy đặt qua giao diện (đường PUT /v1/novnc/mat-khau)
# không bị ghi đè mỗi lần khởi động lại container. Biến CAPTCHA_SOLVER_MAT_KHAU_VNC chỉ dùng làm mật khẩu
# BAN ĐẦU khi chưa có tệp nào (đường tạm trước NV305); muốn đổi mật khẩu thì đặt qua giao diện, không đổi biến.
if [ "$CAPTCHA_SOLVER_BAT_NOVNC" = "1" ] || [ "$CAPTCHA_SOLVER_BAT_NOVNC" = "true" ]; then
    if [ ! -f /khoa/mat-khau-vnc ]; then
        if [ -n "$CAPTCHA_SOLVER_MAT_KHAU_VNC" ]; then
            umask 077
            printf '%s' "$CAPTCHA_SOLVER_MAT_KHAU_VNC" | head -c 8 > /khoa/mat-khau-vnc
            chmod 0600 /khoa/mat-khau-vnc
        else
            umask 077
            tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 8 > /khoa/mat-khau-vnc
            chmod 0600 /khoa/mat-khau-vnc
        fi
    fi
    exec /usr/bin/supervisord -c /etc/supervisor/supervisord-novnc.conf
else
    exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
fi

