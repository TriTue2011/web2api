"""So sánh QUYỀN giữa hai manifest — chống «rug-pull» khi module cập nhật từ git.

Hàm THUẦN (chỉ dùng thư viện chuẩn, không nhập `app.*`, không đọc môi trường): suy quyền
theo CẤU TRÚC từ hợp đồng manifest (đầu `app/kham_pha.py`):
- `cung_cap`: mỗi mã khả năng là một quyền;
- `truong_cau_hinh[].nhan_cookie`: mỗi tên miền là một quyền;
- `yeu_cau_goi`: mỗi gói Python là một quyền;
- `tai_nguyen.mang_ra`: mỗi mục mạng ra là một quyền; khoá lạ khác trong `tai_nguyen`
  cũng là quyền;
- khoá cấp cao LẠ chưa biết → quyền (mặc định ĐÓNG, không danh sách trắng tên).

Chỉ phần THÊM mới cần duyệt; bớt quyền được qua tự do.
"""

from __future__ import annotations

import hashlib
import json

#: Khoá cấp cao của manifest KHÔNG mang quyền (mô tả, hiển thị, cấu hình do quản trị nhập).
#: Mọi khoá khác → quyền. `truong_cau_hinh`/`tai_nguyen`/`yeu_cau_goi`/`cung_cap` có mặt ở đây
#: vì khía cạnh quyền của chúng được rút theo cấu trúc riêng, không phải vì khoá vô hại.
_TRUONG_KHONG_QUYEN = frozenset({
    "id", "ten", "mo_ta", "loai", "giao_thuc", "tien_to",
    "loai_ten", "loai_thu_tu", "muc", "tab_web",
    "truong_cau_hinh", "tai_nguyen", "phien_ban",
    "yeu_cau_goi", "cung_cap",
})

#: Khoá đã biết trong mỗi phần tử `truong_cau_hinh` (ngoài `nhan_cookie` mang quyền).
_TRUONG_CAU_HINH_KHONG_QUYEN = frozenset({"khoa", "nhan", "kieu", "bat_buoc", "mo_ta", "mac_dinh"})


def _bam_gia_tri(gia_tri) -> str:
    """Băm chuẩn của một giá trị JSON — để đổi giá trị của trường lạ cũng thành «thêm quyền»."""
    return hashlib.sha256(
        json.dumps(gia_tri, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def tap_quyen(manifest: dict | None) -> set[tuple]:
    """Tập quyền của một manifest: các bộ (loai, ten, bam_gia_tri | None).

    `bam_gia_tri` chỉ có cho trường lạ (đổi giá trị → quyền khác); trường đã biết
    so theo đúng (loai, ten).
    """
    m = manifest if isinstance(manifest, dict) else {}
    q: set[tuple] = set()
    for ma in m.get("cung_cap") or []:
        q.add(("cung_cap", str(ma), None))
    for goi in m.get("yeu_cau_goi") or []:
        q.add(("yeu_cau_goi", str(goi), None))
    tai_nguyen = m.get("tai_nguyen")
    if isinstance(tai_nguyen, dict):
        for muc in tai_nguyen.get("mang_ra") or []:
            q.add(("mang_ra", str(muc), None))
        for khoa, gia_tri in tai_nguyen.items():
            if khoa != "mang_ra":
                q.add(("tai_nguyen", str(khoa), _bam_gia_tri(gia_tri)))
    for truong in m.get("truong_cau_hinh") or []:
        if not isinstance(truong, dict):
            continue
        nc = truong.get("nhan_cookie")
        if isinstance(nc, dict) and nc.get("mien"):
            q.add(("nhan_cookie", str(nc["mien"]), None))
        khoa = str(truong.get("khoa") or "?")
        for k, gia_tri in truong.items():
            if k not in _TRUONG_CAU_HINH_KHONG_QUYEN and k != "nhan_cookie":
                q.add(("truong_cau_hinh", f"{khoa}.{k}", _bam_gia_tri(gia_tri)))
    for khoa, gia_tri in m.items():
        if khoa not in _TRUONG_KHONG_QUYEN:
            q.add(("truong_la", str(khoa), _bam_gia_tri(gia_tri)))
    return q


def _sang_muc(bo: tuple) -> dict:
    loai, ten, bam = bo
    muc = {"loai": loai, "ten": ten}
    if bam is not None:
        muc["bam_gia_tri"] = bam
    return muc


def _sap_xep(cac_bo: set[tuple]) -> list[dict]:
    return sorted(
        (_sang_muc(b) for b in cac_bo),
        key=lambda d: json.dumps(d, sort_keys=True, ensure_ascii=False),
    )


def diff_quyen(manifest_cu: dict | None, manifest_moi: dict | None) -> dict:
    """So sánh quyền: {"them": [...], "bot": [...]} — mỗi mục {"loai", "ten"} (+ "bam_gia_tri").

    Sắp xếp xác định để quản trị gửi lại đúng danh sách khi duyệt và để băm ổn định.
    """
    cu = tap_quyen(manifest_cu)
    moi = tap_quyen(manifest_moi)
    return {"them": _sap_xep(moi - cu), "bot": _sap_xep(cu - moi)}


def bam_diff(diff: dict) -> str:
    """Băm SHA-256 của diff đã chuẩn hoá — gắn duyệt với đúng diff đã xem (chống TOCTOU)."""
    return hashlib.sha256(
        json.dumps(
            {"them": diff.get("them") or [], "bot": diff.get("bot") or []},
            sort_keys=True, ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def cac_muc_chuan(cac_muc: list[dict]) -> list[dict]:
    """Chuẩn hoá một danh sách mục quyền (từ thân duyệt của quản trị) để so khớp chính xác."""
    ra = []
    for muc in cac_muc or []:
        if not isinstance(muc, dict) or "loai" not in muc or "ten" not in muc:
            continue
        d = {"loai": str(muc["loai"]), "ten": str(muc["ten"])}
        if muc.get("bam_gia_tri"):
            d["bam_gia_tri"] = str(muc["bam_gia_tri"])
        ra.append(d)
    return sorted(ra, key=lambda d: json.dumps(d, sort_keys=True, ensure_ascii=False))
