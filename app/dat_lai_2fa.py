"""Lệnh đặt lại 2FA trên máy chủ (chỉ người có quyền shell trực tiếp).

Cách dùng:
    python -m app.dat_lai_2fa <tên_đăng_nhập>

Chỉ đặt lại/tắt 2FA của CHÍNH tài khoản được chỉ định, không ảnh hưởng các tài khoản khác.
"""

from __future__ import annotations

import sys

from app import nguoi_dung, xac_thuc_2fa


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("Lỗi: Thiếu tên đăng nhập.\nCách dùng: python -m app.dat_lai_2fa <tên_đăng_nhập>\n")
        return 1

    ten = sys.argv[1].strip()
    if not ten:
        sys.stderr.write("Lỗi: Tên đăng nhập rỗng.\n")
        return 1

    u = nguoi_dung.tim_theo_ten(ten)
    if not u:
        sys.stderr.write(f"Lỗi: Không tìm thấy người dùng «{ten}».\n")
        return 1

    u_id = u["id"]
    if not xac_thuc_2fa.da_bat_2fa(u_id):
        sys.stdout.write(f"Người dùng «{ten}» hiện chưa bật 2FA.\n")
        return 0

    xac_thuc_2fa.tat_2fa(u_id)
    sys.stdout.write(f"Đã đặt lại và tắt 2FA thành công cho người dùng «{ten}».\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
