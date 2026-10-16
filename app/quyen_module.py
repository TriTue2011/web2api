"""So sánh QUYỀN giữa hai manifest — chống «rug-pull» khi module cập nhật từ git.

Hàm THUẦN (chỉ dùng thư viện chuẩn, không nhập `app.*`, không đọc môi trường): suy quyền
theo CẤU TRÚC từ hợp đồng manifest (đầu `app/kham_pha.py`):
- `cung_cap`: mỗi mã khả năng là một quyền;
- `can_module`: mỗi id module cần tới là một quyền (module có thêm đường nhờ module khác);
- `truong_cau_hinh[].nhan_cookie`: mỗi tên miền là một quyền;
- `yeu_cau_goi`: mỗi gói Python là một quyền;
- `tai_nguyen.mang_ra`: mỗi mục mạng ra là một quyền;
- `tai_nguyen`: phân bốn loại theo cấu trúc giá trị (NV9, NV15) —
  (1) ghi chú cho người đọc (`ghi_chu`, chuỗi): không phải quyền, không so sánh;
  (2) số tài nguyên không âm (RAM/đĩa…): chỉ TĂNG so với bản cũ mới là quyền thêm;
  (2b) bool (vd `gpu`): chỉ false→true mới là quyền thêm, true→false là hạ quyền;
  (3) mọi khoá khác chưa biết: quyền (mặc định ĐÓNG), đổi giá trị cũng tính;
- khoá cấp cao LẠ chưa biết → quyền (mặc định ĐÓNG, không danh sách trắng tên).

Chỉ phần THÊM mới cần duyệt; bớt quyền được qua tự do.
"""

from __future__ import annotations

import hashlib
import json
import math

#: Khoá cấp cao của manifest KHÔNG mang quyền (mô tả, hiển thị, cấu hình do quản trị nhập).
#: Mọi khoá khác → quyền. `truong_cau_hinh`/`tai_nguyen`/`yeu_cau_goi`/`cung_cap`/`can_module` có mặt ở đây
#: vì khía cạnh quyền của chúng được rút theo cấu trúc riêng, không phải vì khoá vô hại.
#: NV15b: `tab_web` KHÔNG còn ở đây — false→true cho module chạy JS ở origin lõi
#: (đọc được localStorage khoá quản trị) nên là quyền, chỉ tăng mới cần duyệt.
_TRUONG_KHONG_QUYEN = frozenset({
    "id", "ten", "mo_ta", "loai", "giao_thuc", "tien_to",
    "loai_ten", "loai_thu_tu", "muc",
    "truong_cau_hinh", "tai_nguyen", "phien_ban",
    "yeu_cau_goi", "cung_cap", "can_module",
})

#: Khoá đã biết trong mỗi phần tử `truong_cau_hinh` (ngoài `nhan_cookie` mang quyền).
_TRUONG_CAU_HINH_KHONG_QUYEN = frozenset({"khoa", "nhan", "kieu", "bat_buoc", "mo_ta", "mac_dinh"})

#: Khoá `tai_nguyen` là ghi chú cho người đọc (loại 1) — không phải quyền, không so sánh.
#: Nguồn (bảng mô tả hợp đồng, NV9): mọi manifest thật (5/5) dùng `ghi_chu` làm chuỗi mô tả
#: tự do cho người đọc; hợp đồng `app/kham_pha.py` chỉ định nghĩa `tai_nguyen` là dict và
#: `tai_nguyen.mang_ra` là danh sách chuỗi.
_TAI_NGUYEN_GHI_CHU = frozenset({"ghi_chu"})

#: Trần số tài nguyên "bình thường" (đơn vị của manifest, thường là MB): lớn hơn → loại (3)
#: đóng khi lỗi. 10**12 MB = 1 exabyte — vượt mọi máy thật.
_TRAN_SO_TAI_NGUYEN = 10 ** 12


def _la_so_tai_nguyen(gia_tri) -> bool:
    """Số tài nguyên loại (2): int/float không âm, hữu hạn, không quá lớn. bool/chuỗi/âm/
    quá lớn → False (rơi về loại 3, đóng khi lỗi)."""
    if isinstance(gia_tri, bool) or not isinstance(gia_tri, (int, float)):
        return False
    return math.isfinite(gia_tri) and 0 <= gia_tri <= _TRAN_SO_TAI_NGUYEN


def _loai_tai_nguyen(khoa: str, gia_tri) -> str:
    """Phân loại giá trị trong `tai_nguyen`: "ghi_chu" | "so" | "bool" | "la"."""
    if khoa in _TAI_NGUYEN_GHI_CHU:
        return "ghi_chu" if isinstance(gia_tri, str) else "la"
    if isinstance(gia_tri, bool):
        return "bool"
    return "so" if _la_so_tai_nguyen(gia_tri) else "la"


def _so_tai_nguyen(manifest: dict | None) -> dict[str, int | float]:
    """Số tài nguyên loại (2) trong `tai_nguyen`: {ten: số}."""
    m = manifest if isinstance(manifest, dict) else {}
    tai_nguyen = m.get("tai_nguyen")
    if not isinstance(tai_nguyen, dict):
        return {}
    return {str(k): v for k, v in tai_nguyen.items()
            if k != "mang_ra" and _loai_tai_nguyen(k, v) == "so"}


def _bool_tai_nguyen(manifest: dict | None) -> dict[str, bool]:
    """Bool trong `tai_nguyen` (vd `gpu`): {ten: bool}. NV15: chỉ false→true mới là
    quyền thêm (tăng); true→false là hạ quyền, qua tự do — khuôn theo NV9 cho số."""
    m = manifest if isinstance(manifest, dict) else {}
    tai_nguyen = m.get("tai_nguyen")
    if not isinstance(tai_nguyen, dict):
        return {}
    return {str(k): v for k, v in tai_nguyen.items()
            if k != "mang_ra" and _loai_tai_nguyen(k, v) == "bool"}


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
    cm = m.get("can_module")
    if isinstance(cm, list):
        for dep in cm:
            q.add(("can_module", str(dep), None))
    for goi in m.get("yeu_cau_goi") or []:
        q.add(("yeu_cau_goi", str(goi), None))
    tai_nguyen = m.get("tai_nguyen")
    if isinstance(tai_nguyen, dict):
        for muc in tai_nguyen.get("mang_ra") or []:
            q.add(("mang_ra", str(muc), None))
        for khoa, gia_tri in tai_nguyen.items():
            if khoa == "mang_ra":
                continue
            # Loại (1) ghi_chu: không phải quyền. Loại (2) số và bool: so theo tăng ở
            # diff_quyen (NV9 cho số, NV15 cho bool). Chỉ loại (3) lạ mới vào tập quyền
            # tuyệt đối.
            if _loai_tai_nguyen(khoa, gia_tri) == "la":
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
        if khoa not in _TRUONG_KHONG_QUYEN and khoa != "tab_web":
            # NV15b: tab_web xử lý riêng như bool ở diff_quyen (chỉ false→true mới là quyền thêm),
            # không cho vào truong_la (băm giá trị sẽ khiến true→false cũng bị coi là thêm).
            q.add(("truong_la", str(khoa), _bam_gia_tri(gia_tri)))
    return q


def _sang_muc(bo: tuple) -> dict:
    loai, ten, bam = bo
    muc = {"loai": loai, "ten": ten}
    if bam is None:
        return muc
    if isinstance(bam, tuple) and len(bam) == 3 and bam[0] == "tang":
        # Loại (2): tăng số tài nguyên — nêu rõ mức tăng để quản trị duyệt đúng.
        _, cu_so, moi_so = bam
        muc["tang_tu"] = cu_so
        muc["tang_len"] = moi_so
    else:
        muc["bam_gia_tri"] = bam
    return muc


def _sap_xep(cac_bo: set[tuple]) -> list[dict]:
    return sorted(
        (_sang_muc(b) for b in cac_bo),
        key=lambda d: json.dumps(d, sort_keys=True, ensure_ascii=False),
    )


def diff_quyen(manifest_cu: dict | None, manifest_moi: dict | None) -> dict:
    """So sánh quyền: {"them": [...], "bot": [...]} — mỗi mục {"loai", "ten"}
    (+ "bam_gia_tri" cho trường lạ, hoặc "tang_tu"/"tang_len" cho số tài nguyên tăng).

    Sắp xếp xác định để quản trị gửi lại đúng danh sách khi duyệt và để băm ổn định.
    Số tài nguyên (loại 2): chỉ TĂNG so với bản cũ (thiếu/null coi như 0) mới vào "them";
    giảm/bằng/không đổi thì không. Bool (NV15, vd `gpu`): chỉ false→true mới vào "them";
    true→false là hạ quyền, qua tự do.
    """
    cu = tap_quyen(manifest_cu)
    moi = tap_quyen(manifest_moi)
    them = set(moi - cu)
    bot = set(cu - moi)
    so_cu = _so_tai_nguyen(manifest_cu)
    so_moi = _so_tai_nguyen(manifest_moi)
    for ten, moi_so in so_moi.items():
        cu_so = so_cu.get(ten, 0)
        if moi_so > cu_so:
            them.add(("tai_nguyen", ten, ("tang", cu_so, moi_so)))
    bool_cu = _bool_tai_nguyen(manifest_cu)
    bool_moi = _bool_tai_nguyen(manifest_moi)
    for ten, moi_bool in bool_moi.items():
        # Thiếu/null coi như False: chỉ bật từ tắt/mới thêm mới là quyền thêm.
        if moi_bool and not bool_cu.get(ten, False):
            them.add(("tai_nguyen", ten, None))
    # NV15b: tab_web là quyền bool ở cấp manifest — false→true cho module chạy JS ở origin
    # lõi (đọc được localStorage khoá quản trị) nên cần duyệt; true→false là hạ quyền.
    def _tab_web(m):
        return bool((m or {}).get("tab_web")) if isinstance(m, dict) else False
    if _tab_web(manifest_moi) and not _tab_web(manifest_cu):
        them.add(("tab_web", "tab_web", None))
    # NV31: khai webhook mở một cửa vào công khai cho module — thêm mới cần duyệt;
    # gỡ bỏ là hạ quyền, qua tự do.
    def _webhook(m):
        return bool((m or {}).get("webhook")) if isinstance(m, dict) else False
    if _webhook(manifest_moi) and not _webhook(manifest_cu):
        them.add(("webhook", "webhook", None))
    return {"them": _sap_xep(them), "bot": _sap_xep(bot)}


def bam_diff(diff: dict) -> str:
    """Băm SHA-256 của diff đã chuẩn hoá — gắn duyệt với đúng diff đã xem (chống TOCTOU)."""
    return hashlib.sha256(
        json.dumps(
            {"them": diff.get("them") or [], "bot": diff.get("bot") or []},
            sort_keys=True, ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def cac_muc_chuan(cac_muc: list[dict]) -> list[dict]:
    """Chuẩn hoá một danh sách mục quyền (từ thân duyệt của quản trị) để so khớp chính xác.

    Giữ "bam_gia_tri" (trường lạ) và "tang_tu"/"tang_len" (số tài nguyên tăng, phải là số
    không phải bool) — thiếu hoặc sai kiểu thì mục không khớp → 409 (đóng khi lỗi).
    """
    ra = []
    for muc in cac_muc or []:
        if not isinstance(muc, dict) or "loai" not in muc or "ten" not in muc:
            continue
        d = {"loai": str(muc["loai"]), "ten": str(muc["ten"])}
        if muc.get("bam_gia_tri"):
            d["bam_gia_tri"] = str(muc["bam_gia_tri"])
        if "tang_tu" in muc and "tang_len" in muc:
            tu, ln = muc["tang_tu"], muc["tang_len"]
            if (isinstance(tu, bool) or isinstance(ln, bool)
                    or not isinstance(tu, (int, float)) or not isinstance(ln, (int, float))):
                continue
            d["tang_tu"] = tu
            d["tang_len"] = ln
        ra.append(d)
    return sorted(ra, key=lambda d: json.dumps(d, sort_keys=True, ensure_ascii=False))
