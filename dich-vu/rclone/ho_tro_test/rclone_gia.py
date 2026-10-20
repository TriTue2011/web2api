#!/usr/bin/env python3
"""Nhị phân rclone GIẢ cho kiểm thử (NV161): trả dữ liệu cố định, không gọi mạng.

Dùng qua biến môi trường RCLONE_BIN trỏ tới tệp này. Trạng thái giữ trong tệp
JSON do RCLONE_GIA_STATE chỉ (mặc định ./rclone-gia-state.json): remotes đã khai
và «đám mây» giả (ánh xạ tên_remote → cây thư mục).

Chỉ dùng cho test — không có bảo mật, không dùng ở triển khai.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _state_path() -> Path:
    p = os.environ.get("RCLONE_GIA_STATE", "./rclone-gia-state.json")
    return Path(p)


def _tai() -> dict:
    p = _state_path()
    if p.exists():
        try:
            return json.loads(p.read_text("utf-8"))
        except (ValueError, OSError):
            pass
    return {"remotes": {}, "cloud": {}}


def _luu(s: dict) -> None:
    _state_path().write_text(json.dumps(s, ensure_ascii=False), "utf-8")


def _loi(msg: str) -> int:
    sys.stderr.write(msg + "\n")
    return 1


def main() -> int:
    args = sys.argv[1:]
    # rclone thật nhận ``--config <đường>`` ở đầu mọi lệnh — bỏ qua ở bản giả.
    if args[:1] == ["--config"]:
        args = args[2:]
    s = _tai()
    remotes = s["remotes"]
    cloud = s["cloud"]

    if not args:
        return _loi("thiếu lệnh")

    # ── các lệnh quản trị không cần remote tồn tại ──
    if args[0] == "version":
        print("rclone v1.75.2-gia")
        return 0
    if args == ["config", "dump"]:
        print(json.dumps(remotes))
        return 0
    if args[:2] == ["config", "create"]:
        # rclone config create <ten> <loai> [--obscure] -- <k> <v> ...
        ten, loai = args[2], args[3]
        rest = args[4:]
        if "--" in rest:
            rest = rest[rest.index("--") + 1:]
        cfg = {"type": loai}
        it = iter(rest)
        for k in it:
            try:
                cfg[k] = next(it)
            except StopIteration:
                break
        remotes[ten] = cfg
        _luu(s)
        return 0
    if args[:2] == ["config", "delete"]:
        remotes.pop(args[2], None)
        _luu(s)
        return 0

    # ── lệnh trên remote: đối số cuối/đầu là ten:duong/dan ──
    def _remote(duong: str):
        ten = duong.split(":", 1)[0]
        if ten not in remotes:
            print(f"không thấy remote {ten!r}", file=sys.stderr)
            return None
        return ten

    if args[0] == "about":
        if not _remote(args[1]):
            return 1
        print(json.dumps({"total": 1000, "used": 10}))
        return 0
    if args[0] == "lsd":
        if not _remote(args[1]):
            return 1
        return 0
    if args[0] == "lsjson":
        dd = args[1]
        if not _remote(dd):
            return 1
        muc = cloud.get(dd, [{"Name": "a.txt", "IsDir": False, "Size": 5,
                              "ModTime": "2026-10-20T00:00:00Z"}])
        print(json.dumps(muc))
        return 0
    if args[0] == "copyto":
        nguon, dich = args[1], args[2]
        # copyto cục-bộ→đám-mây hoặc đám-mây→cục-bộ
        if ":" in nguon and not os.path.isabs(nguon):
            if not _remote(nguon):
                return 1
            # đám mây → cục bộ: ghi nội dung giả
            Path(dich).write_bytes(b"du-lieu-gia")
        else:
            if not _remote(dich):
                return 1
            if not Path(nguon).is_file():
                return _loi("không thấy tệp nguồn")
        return 0
    if args[0] == "cat":
        if not _remote(args[1]):
            return 1
        n = None
        if "--count" in args:
            n = int(args[args.index("--count") + 1])
        noi_dung = "noi-dung-gia-tu-dam-may"
        sys.stdout.write(noi_dung[:n] if n else noi_dung)
        return 0
    if args[0] == "deletefile":
        if not _remote(args[1]):
            return 1
        return 0
    if args[0] == "moveto":
        nguon, dich = args[1], args[2]
        if not _remote(nguon) or not _remote(dich):
            return 1
        return 0

    return _loi(f"lệnh giả không hỗ trợ: {args[0]}")


if __name__ == "__main__":
    raise SystemExit(main())
