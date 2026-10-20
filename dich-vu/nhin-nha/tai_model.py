"""Tải model cho dịch vụ nhin-nha — kiểm sha256 trước khi dùng.

Khuôn giống các script tải của c2a (scripts/download_nhin_nha.py): model
KHÔNG nằm trong ảnh Docker; tải thủ công về thư mục ``NHINNHA_MODEL_DIR``
(volume gắn vào /model trong container), script kiểm sha256 ngay khi tải —
lệch là tệp hỏng hoặc bị thay, không dùng.

    python3 tai_model.py --dich /data/nhin-nha/model
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from yolo import MODELS, PHAT_HANH  # noqa: E402
from khuon_mat import BO, PHAT_HANH_MAT  # noqa: E402


def kiem_sha256(tep: Path, ky_vong: str) -> bool:
    h = hashlib.sha256()
    with tep.open("rb") as f:
        for khoi in iter(lambda: f.read(1 << 20), b""):
            h.update(khoi)
    return h.hexdigest() == ky_vong


def tai(url: str, dich: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "web2api-nhin-nha/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, dich.open("wb") as f:
        while True:
            khoi = r.read(1 << 20)
            if not khoi:
                break
            f.write(khoi)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dich", default="model", help="thư mục model đích")
    ap.add_argument("--yolo", default="yolo26n", help="mã model YOLO")
    ap.add_argument("--bo-mat", default="buffalo_s", help="bộ dò mặt + vector")
    args = ap.parse_args()

    dich = Path(args.dich)
    dich.mkdir(parents=True, exist_ok=True)

    mh = next((m for m in MODELS if m.ma == args.yolo), None)
    if mh is None:
        print(f"model YOLO lạ: {args.yolo}", file=sys.stderr)
        return 1
    bo = next((b for b in BO if b.ma == args.bo_mat), None)
    if bo is None:
        print(f"bộ mặt lạ: {args.bo_mat}", file=sys.stderr)
        return 1

    ke_hoach = [(PHAT_HANH + mh.tep, dich / mh.tep, mh.sha256),
                (PHAT_HANH_MAT + bo.tep_do, dich / bo.tep_do, bo.sha_do),
                (PHAT_HANH_MAT + bo.tep_vector, dich / bo.tep_vector, bo.sha_vector)]
    for url, tep, sha in ke_hoach:
        if tep.exists() and kiem_sha256(tep, sha):
            print(f"đã có, đúng sha256: {tep.name}")
            continue
        print(f"tải {url} …")
        tai(url, tep)
        if not kiem_sha256(tep, sha):
            tep.unlink(missing_ok=True)
            print(f"LỆCH sha256: {tep.name} — xoá, không dùng", file=sys.stderr)
            return 2
        print(f"xong, đúng sha256: {tep.name}")
    print("sẵn sàng.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
