#!/usr/bin/env python3
"""Docker CLI GIẢ cho test của dịch vụ chay-ma (NV171).

KHÔNG phải docker thật: chỉ đóng vai trò «lớp vỏ lệnh» để test kiểm được
HỢP ĐỒNG mà dịch vụ sinh ra:

- Mọi lời gọi được GHI LẠI vào thư mục chụp (biến CHAYMA_GIA_THU_MUC):
  `argv.json` (đối số của lệnh `run` — gồm cả cờ cách ly; chỉ `run` mới ghi
  đè tệp này), `argv_all.jsonl` (MỌI lời gọi theo thứ tự, một dòng JSON một
  lượt — để test kiểm kill/rm sau run), `env.json` (môi trường mà dịch vụ
  truyền cho tiến trình docker — phải TỐI THIỂU, không chứa khoá),
  `thu_muc_chay.json` (danh sách tệp trong thư mục lần chạy + nội dung tệp
  đầu vào nhỏ + `_mount_uid`/`_mount_mode` của thư mục nguồn mount, để test
  thấy tệp đầu vào đã vào đúng thư mục và thư mục thuộc uid nào).
- Chế độ hành vi qua biến CHAYMA_GIA_CHE_DO:
    echo    (mặc định) in «stdout-gia», tạo ket-qua.txt = «42», thoát 0.
    loi     in traceback giả ra stderr, thoát 3.
    ngu     ngủ 30 s (test hết giờ phải diệt sớm).
    phun    phun ~6 MB ra stdout (test trần đầu ra phải cắt + diệt sớm).
    symlink tạo ket-qua.txt thật + symlink «thoat.txt» → /etc/hostname
            (test thu tệp ra KHÔNG được đi theo symlink ra ngoài).
- `kill`/`--version`: trả lời tối thiểu để dịch vụ dọn dẹp/kiểm tra được.

Chương trình này chỉ chạy trong test; dịch vụ thật dùng docker CLI thật.
"""

from __future__ import annotations

import json
import os
import sys
import time


def _ghi(ten: str, du_lieu) -> None:
    thu_muc = os.environ.get("CHAYMA_GIA_THU_MUC", "")
    if not thu_muc:
        return
    try:
        os.makedirs(thu_muc, exist_ok=True)
        with open(os.path.join(thu_muc, ten), "w", encoding="utf-8") as f:
            json.dump(du_lieu, f, ensure_ascii=False)
    except OSError:
        pass


def _ghi_them(ten: str, du_lieu) -> None:
    """Ghi THÊM một dòng JSON (cho argv_all.jsonl — lịch sử mọi lời gọi)."""
    thu_muc = os.environ.get("CHAYMA_GIA_THU_MUC", "")
    if not thu_muc:
        return
    try:
        os.makedirs(thu_muc, exist_ok=True)
        with open(os.path.join(thu_muc, ten), "a", encoding="utf-8") as f:
            f.write(json.dumps(du_lieu, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _tim_thu_muc_chay(argv: list[str]) -> str:
    """Tìm thông số -v «<host>:/lam-viec:rw» trong argv của `docker run`."""
    for i, muc in enumerate(argv):
        if muc == "-v" and i + 1 < len(argv) and ":/lam-viec:" in argv[i + 1]:
            return argv[i + 1].split(":", 1)[0]
    return ""


def main() -> int:
    argv = sys.argv[1:]
    _ghi_them("argv_all.jsonl", argv)
    if argv and argv[0] == "run":
        # argv.json/env.json = hợp đồng của lệnh run (kill/rm không ghi đè).
        _ghi("argv.json", argv)
        _ghi("env.json", dict(os.environ))
    if not argv:
        return 2
    if argv[0] == "--version":
        print("Docker version 27.5.1, build gia-nv171")
        return 0
    if argv[0] in ("kill", "rm"):
        print(argv[1] if len(argv) > 1 else "")
        return 0
    if argv[0] != "run":
        print(f"docker gia: khong ho tro {argv[0]!r}", file=sys.stderr)
        return 2

    che_do = os.environ.get("CHAYMA_GIA_CHE_DO", "echo")
    thu_muc_chay = _tim_thu_muc_chay(argv)
    if thu_muc_chay and os.path.isdir(thu_muc_chay):
        st = os.stat(thu_muc_chay)
        chup = {"_mount_uid": st.st_uid,
                "_mount_mode": oct(st.st_mode & 0o7777)}
        for ten in sorted(os.listdir(thu_muc_chay)):
            duong = os.path.join(thu_muc_chay, ten)
            muc = {"symlink": os.path.islink(duong)}
            if os.path.isfile(duong) and not os.path.islink(duong):
                try:
                    with open(duong, "rb") as f:
                        muc["noi_dung"] = f.read(4096).decode("utf-8", "replace")
                except OSError:
                    pass
            chup[ten] = muc
        _ghi("thu_muc_chay.json", chup)

    if che_do == "ngu":
        time.sleep(30)
        return 0
    if che_do == "phun":
        khoi = b"x" * 65536
        for _ in range(96):  # ~6 MB
            try:
                sys.stdout.buffer.write(khoi)
                sys.stdout.buffer.flush()
            except BrokenPipeError:
                return 0
        return 0
    if che_do == "loi":
        sys.stderr.write(
            "Traceback (most recent call last):\n"
            '  File "/lam-viec/ma.py", line 2, in <module>\n'
            '    raise RuntimeError("loi gia")\n'
            "RuntimeError: loi gia\n"
        )
        return 3
    if che_do == "symlink":
        if thu_muc_chay:
            with open(os.path.join(thu_muc_chay, "ket-qua.txt"), "w", encoding="utf-8") as f:
                f.write("42\n")
            try:
                os.symlink("/etc/hostname", os.path.join(thu_muc_chay, "thoat.txt"))
            except OSError:
                pass
        print("stdout-gia: da chay xong")
        return 0
    # echo
    if thu_muc_chay:
        with open(os.path.join(thu_muc_chay, "ket-qua.txt"), "w", encoding="utf-8") as f:
            f.write("42\n")
    print("stdout-gia: da chay xong")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
