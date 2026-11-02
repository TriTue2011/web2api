"""Chi tiết một tài khoản đã lưu (NV285): gói, trạng thái đăng nhập/phiên,
lượng dùng, hạn mức từng tính năng, hạng xoay vòng.

Một đường, một lượt gọi cho giao diện — gộp (i) bản công khai + ``dang_nhap``
+ ``loai_hien_thi`` (NV283), (ii) lượng dùng 14 ngày từ
``luong_dung_tai_khoan``, (iii) trạng thái từng đường từ ``duong_dung``,
(iv) hạn mức từ khả năng tuỳ chọn ``chi_tiet_tai_khoan`` của module.

Nguyên tắc:
- CHỈ ĐỌC từ bộ đệm của lượt init/dùng gần nhất khi gọi
  ``chi_tiet_tai_khoan`` — không gọi mạng thêm. Nút «Kiểm tra lại hạn mức»
  (``lam_moi_han_muc``) mới được gọi mạng, MỘT tài khoản, giãn cách tối
  thiểu ``LAM_MOI_GIAN_CACH_GIAY``.
- KHÔNG bịa số: module không có số liệu → ``han_muc.nguon = "khong_biet"``,
  mục không có số → các trường số là None, hạng là None.
- Hạng #n lấy từ thứ tự xoay vòng THẬT: thứ tự tài khoản trong
  ``liet_ke_tai_khoan`` của chính module sở hữu (thứ tự module thử các
  tài khoản khi phục vụ) nếu module đang chạy và khai khả năng đó; không
  thì thứ tự liệt kê của kho (``cap_nhat`` giảm dần, như ``liet_ke``).
  Đường dùng trong một tài khoản thì theo ``duong_dung.chon_duong``
  (codex trước, rồi web). KHÔNG tự đặt thứ tự mới.
- Chỉ tài khoản còn lượt (không cạn mục đó, không hết hạn mức cấp tài
  khoản) VÀ đăng nhập «ok» mới được xếp hạng.
"""

from __future__ import annotations

import inspect
import logging
import re
import time

log = logging.getLogger("web2api.chi_tiet_tai_khoan")

#: Giãn cách tối thiểu giữa hai lần «Kiểm tra lại hạn mức» của MỘT tài
#: khoản (giây) — cùng cơ chế chống ồ ạt hiện có (NV285 §2b).
LAM_MOI_GIAN_CACH_GIAY = 60

#: Mốc làm mới hạn mức gần nhất theo id tài khoản (bộ nhớ tiến trình).
_MOC_LAM_MOI: dict[str, float] = {}

#: Tên mục hạn mức hợp lệ.
_MAU_TEN_MUC = re.compile(r"^[a-z0-9_]{1,64}$")

#: Thứ tự ưu tiên đường dùng trong một tài khoản (như duong_dung).
_THU_TU_DUONG = ("codex", "web")


def con_lai_lam_moi_giay(id_: str, bay_gio: float | None = None) -> float:
    """Số giây còn phải chờ trước lần làm mới kế (0 = được làm mới ngay)."""
    if not isinstance(id_, str) or not id_:
        return 0.0
    moc = _MOC_LAM_MOI.get(id_, 0.0)
    if not moc:
        return 0.0
    con = LAM_MOI_GIAN_CACH_GIAY - ((bay_gio if bay_gio is not None else time.time()) - moc)
    return con if con > 0 else 0.0


def danh_dau_lam_moi(id_: str, bay_gio: float | None = None) -> None:
    """Ghi mốc làm mới (chỉ gọi sau khi module đã chạy xong không ném)."""
    if isinstance(id_, str) and id_:
        _MOC_LAM_MOI[id_] = bay_gio if bay_gio is not None else time.time()


def chuan_hoa_goi(goi) -> str:
    """Gói theo hợp đồng NV285 §3b: chỉ «plus»/«free», còn lại «khong_biet».

    (Các bậc khác như pro/team hiện gộp vào «khong_biet» theo đúng hợp
    đồng đóng — xem hoi-nv285.md về việc có mở rộng hợp đồng hay không.)
    """
    if isinstance(goi, str) and goi.strip().lower() in ("plus", "free"):
        return goi.strip().lower()
    return "khong_biet"


def _so_nguyen(gia_tri):
    if isinstance(gia_tri, bool) or not isinstance(gia_tri, (int, float)):
        return None
    if gia_tri != gia_tri or abs(gia_tri) == float("inf"):
        return None
    return int(gia_tri)


def _so_thuc(gia_tri):
    if isinstance(gia_tri, bool) or not isinstance(gia_tri, (int, float)):
        return None
    if gia_tri != gia_tri or abs(gia_tri) == float("inf"):
        return None
    return float(gia_tri)


def chuan_hoa_muc(muc_tho) -> dict | None:
    """Một mục hạn mức từ module → khuôn chuẩn, None nếu không dùng được.

    Chỉ giữ các trường đã biết; chuỗi/bí mật lạ (token, cookie, email…)
    trong mục thô bị LOẠI (không bao giờ lọt ra phản hồi).
    """
    if not isinstance(muc_tho, dict):
        return None
    ten = muc_tho.get("ten")
    if not isinstance(ten, str) or not _MAU_TEN_MUC.fullmatch(ten.strip()):
        return None
    nhan = muc_tho.get("nhan")
    nhan = nhan.strip() if isinstance(nhan, str) and nhan.strip() else ten.strip()
    pct = _so_thuc(muc_tho.get("da_dung_pct"))
    if pct is not None:
        pct = min(100.0, max(0.0, pct))
    reset_luc = _so_nguyen(muc_tho.get("reset_luc"))
    reset_sau = _so_nguyen(muc_tho.get("reset_sau_giay"))
    if reset_sau is not None and reset_sau < 0:
        reset_sau = None
    return {
        "ten": ten.strip(),
        "nhan": nhan[:80],
        "con_lai": _so_nguyen(muc_tho.get("con_lai")),
        "tong": _so_nguyen(muc_tho.get("tong")),
        "da_dung_pct": pct,
        "reset_luc": reset_luc,
        "reset_sau_giay": reset_sau,
        "hang": None,
    }


def chuan_hoa_han_muc(chi_tiet_tho) -> dict:
    """Kết quả ``chi_tiet_tai_khoan`` của module → khuôn ``han_muc`` chuẩn.

    Không phải dict, hoặc không có mục nào có số liệu → «khong_biet»
    (module khai khả năng nhưng chưa có số — không bịa).
    """
    rong = {"nguon": "khong_biet", "cap_nhat_luc": 0, "muc": []}
    if not isinstance(chi_tiet_tho, dict):
        return rong
    muc_tho = chi_tiet_tho.get("muc")
    if not isinstance(muc_tho, list):
        return rong
    muc = []
    for m in muc_tho:
        chuan = chuan_hoa_muc(m)
        if chuan is not None:
            muc.append(chuan)
    if not muc:
        return rong
    cap_nhat = _so_nguyen(chi_tiet_tho.get("cap_nhat_luc")) or 0
    return {"nguon": "module", "cap_nhat_luc": cap_nhat, "muc": muc}


def _co_so_lieu(muc: dict) -> bool:
    return muc.get("con_lai") is not None or muc.get("tong") is not None \
        or muc.get("da_dung_pct") is not None


def _can_muc(muc: dict) -> bool:
    """Mục đã cạn: còn 0 lượt, hoặc đã dùng 100%."""
    if muc.get("con_lai") is not None and muc["con_lai"] <= 0:
        return True
    if muc.get("da_dung_pct") is not None and muc["da_dung_pct"] >= 100.0:
        return True
    return False


async def _goi_kha_nang(mod, ten, *args):
    """Gọi khả năng module (đồng bộ hoặc coroutine); không có → None."""
    ham = getattr(mod, ten, None) if mod is not None else None
    if not callable(ham):
        return None
    kq = ham(*args)
    if inspect.isawaitable(kq):
        kq = await kq
    return kq


async def lay_han_muc_tu_module(so, ban_ghi: dict) -> tuple[dict, str]:
    """Hạn mức + gói của một tài khoản từ module sở hữu (chỉ đọc bộ đệm).

    Trả (han_muc_chuan, goi_chuan). Module không chạy/không khai khả
    năng/không có số → thử hạn mức thụ động từ tiêu đề (nguồn «tieu_de»,
    không có mục từng tính năng); không có gì → «khong_biet». Lỗi module
    → log.warning có kiểu lỗi, trả «khong_biet» (không hỏng route).
    """
    from app import luong_dung_tai_khoan as ldtk

    id_ = ban_ghi.get("id", "") if isinstance(ban_ghi, dict) else ""
    module_id = ban_ghi.get("module_id", "") if isinstance(ban_ghi, dict) else ""
    email = ban_ghi.get("email", "") if isinstance(ban_ghi, dict) else ""
    goi_mac_dinh = chuan_hoa_goi(ban_ghi.get("bac_goi") if isinstance(ban_ghi, dict) else None)
    mod = None
    try:
        mod = (so.dang_chay.get(module_id) if so is not None and module_id else None)
    except Exception:
        mod = None
    if mod is not None and email:
        try:
            tho = await _goi_kha_nang(mod, "chi_tiet_tai_khoan", email)
        except Exception as e:
            log.warning("chi-tiet: module %s lỗi: %s", module_id, type(e).__name__)
            tho = None
        if isinstance(tho, dict):
            han_muc = chuan_hoa_han_muc(tho)
            if han_muc["nguon"] == "module":
                return han_muc, chuan_hoa_goi(tho.get("goi")) \
                    if tho.get("goi") else goi_mac_dinh
    # Nguồn thụ động từ tiêu đề (luong_dung): có số liệu chung nhưng KHÔNG
    # có mục từng tính năng → muc rỗng, không bịa mục «chung» thành tính năng.
    try:
        tieu_de = ldtk.lay_han_muc(id_) if id_ else None
    except Exception:
        tieu_de = None
    if isinstance(tieu_de, dict) and tieu_de:
        return {"nguon": "tieu_de",
                "cap_nhat_luc": _so_nguyen(tieu_de.get("luc_doc")) or 0,
                "muc": []}, goi_mac_dinh
    return {"nguon": "khong_biet", "cap_nhat_luc": 0, "muc": []}, goi_mac_dinh


def dung_thong_ke(id_: str) -> dict:
    """Thống kê dùng cho chi-tiet.dung (NV285 §c3).

    Cửa sổ 14 ngày gần nhất từ ``luong_dung_tai_khoan``: ``luot`` chỉ đếm
    lượt thành công, ``loi_*`` đếm lỗi tách loại — nên thanh_cong/that_bai
    tách được (không bao giờ phải trả null với lược đồ hiện tại; null giữ
    trong hợp đồng cho lược đồ tương lai không tách được).
    """
    from app import luong_dung_tai_khoan as ldtk

    rong = {"hom_nay": 0, "7_ngay": 0, "thanh_cong": 0, "that_bai": 0,
            "theo_ngay": [], "lan_dung_cuoi": 0}
    if not isinstance(id_, str) or not id_:
        return rong
    try:
        tk = ldtk.thong_ke(id_)
    except Exception:
        return rong
    if tk is None:
        return rong
    from datetime import datetime, timedelta, timezone

    bay_gio = datetime.fromtimestamp(int(time.time()), tz=timezone.utc)
    hom_nay_str = bay_gio.strftime("%Y-%m-%d")
    muc_7 = (bay_gio - timedelta(days=6)).strftime("%Y-%m-%d")
    muc_14 = (bay_gio - timedelta(days=13)).strftime("%Y-%m-%d")
    hang_14 = [d for d in tk["ngay"] if d["ngay"] >= muc_14][-14:]
    hom_nay = 0
    bay_ngay = 0
    thanh_cong = 0
    that_bai = 0
    theo_ngay = []
    for d in hang_14:
        theo_ngay.append({"ngay": d["ngay"], "so_luot": d["luot"]})
        thanh_cong += d["luot"]
        that_bai += d["loi_han_muc"] + d["loi_het_phien"] + d["loi_khac"]
        if d["ngay"] == hom_nay_str:
            hom_nay = d["luot"]
        if d["ngay"] >= muc_7:
            bay_ngay += d["luot"]
    try:
        lan_cuoi = ldtk.lan_dung_cuoi(id_)
    except Exception:
        lan_cuoi = 0
    return {"hom_nay": hom_nay, "7_ngay": bay_ngay, "thanh_cong": thanh_cong,
            "that_bai": that_bai, "theo_ngay": theo_ngay,
            "lan_dung_cuoi": lan_cuoi or 0}


def duong_cong_khai(ban_ghi: dict) -> list[dict]:
    """Trạng thái từng đường của tài khoản, xếp theo thứ tự ưu tiên đường
    của ``duong_dung`` (codex trước, rồi web, rồi các đường khác)."""
    from app import kho_tai_khoan as ktk

    tt = ktk.lay_trang_thai_duong(ban_ghi) if isinstance(ban_ghi, dict) else {}
    if not isinstance(tt, dict):
        return []
    thu_tu = [d for d in _THU_TU_DUONG if d in tt]
    thu_tu += [d for d in tt if d not in thu_tu]
    return [{"dich": d,
             "trang_thai": tt[d].get("trang_thai", ""),
             "moc_hoi_lai": tt[d].get("moc_hoi_lai", 0)} for d in thu_tu]


def nhom_duong_cua_ban(ban_cong_khai: dict) -> tuple[str, str]:
    """Nhóm (nhà, đường) của một bản công khai: nhà = nhom hiệu lực,
    đường = dich_dung chính, không có thì dung_cho đầu, không có thì ""."""
    from app import kho_tai_khoan as ktk

    nhom = ktk.nhom_hieu_luc(ban_cong_khai.get("nhom", "") or "")
    duong = ban_cong_khai.get("dich_dung", "") or ""
    if not duong:
        dung_cho = ban_cong_khai.get("dung_cho") or []
        duong = dung_cho[0] if dung_cho else ""
    return nhom, duong


def thu_tu_xoay_cua_nhom(so, cac_ban: list[dict]) -> dict[str, int]:
    """Thứ tự xoay vòng của các tài khoản trong một nhóm (email → vị trí).

    Ưu tiên thứ tự ``liet_ke_tai_khoan`` của chính module sở hữu (module
    thử tài khoản theo thứ tự đó khi phục vụ); tài khoản không có trong
    danh sách module xếp sau theo thứ tự liệt kê của kho. Module không
    chạy/không khai liệt kê → toàn bộ theo thứ tự kho.
    """
    thu_tu: dict[str, int] = {}
    # Thứ tự module trước (vị trí nhỏ = thử trước); tài khoản không có
    # trong danh sách module xếp sau theo thứ tự liệt kê của kho
    # (cap_nhat giảm dần, như kho.liet_ke).
    if so is not None:
        for module_id in sorted({b.get("module_id", "") for b in cac_ban if b.get("module_id")}):
            try:
                mod = so.dang_chay.get(module_id)
                liet_ke = getattr(mod, "liet_ke_tai_khoan", None) if mod is not None else None
                if not callable(liet_ke):
                    continue
                for i, muc in enumerate(liet_ke() or []):
                    if isinstance(muc, dict):
                        email = str(muc.get("email") or "").strip().lower()
                        if email and email not in thu_tu:
                            thu_tu[email] = i
            except Exception as e:
                log.warning("chi-tiet: đọc thứ tự module %s lỗi: %s",
                            module_id, type(e).__name__)
    for i, b in enumerate(cac_ban):
        email = (b.get("_email_noi_bo") or "").strip().lower()
        if email and email not in thu_tu:
            thu_tu[email] = 100000 + i
    return thu_tu


def lap_bang_hang(nhom: str, duong: str, cac_ban: list[dict],
                 han_muc_theo_id: dict, thu_tu: dict[str, int]) -> tuple[dict, list[dict]]:
    """Hạng từng tài khoản theo từng mục + người giữ #1 của từng mục.

    Trả (hang_theo_id: {id: {ten_muc: hang|None}},
         giu_hang_1: [{nhom, muc, tai_khoan_id, email}]).
    Ứng viên xếp hạng: không tạm dừng, dang_nhap «ok», còn lượt ở mục đó
    (không cạn, không hết hạn mức cấp tài khoản — kiểm qua luong_dung).
    """
    from app import kho_tai_khoan as ktk
    from app import luong_dung_tai_khoan as ldtk

    ten_nhom = f"{nhom}/{duong}" if duong else nhom
    ung_vien_theo_muc: dict[str, list[dict]] = {}
    for b in cac_ban:
        if b.get("trang_thai") == ktk.TRANG_THAI_TAM_DUNG:
            continue
        if b.get("dang_nhap", {}).get("trang_thai") != "ok":
            continue
        try:
            if ldtk.con_het_han_muc(b["id"]):
                continue
        except Exception as e:
            # Hạn mức là QoS (như _con_het_han_muc đã duyệt): lỗi đọc sổ
            # → coi như chưa hết, nhưng ghi log kiểu lỗi, không nuốt im.
            log.warning("lap_bang_hang: loi doc han muc: %s", type(e).__name__)
        han_muc = han_muc_theo_id.get(b["id"]) or {}
        for muc in han_muc.get("muc") or []:
            if not _co_so_lieu(muc) or _can_muc(muc):
                continue
            ung_vien_theo_muc.setdefault(muc["ten"], []).append(b)
    hang_theo_id: dict[str, dict] = {b["id"]: {} for b in cac_ban}
    giu_hang_1: list[dict] = []
    for ten_muc in sorted(ung_vien_theo_muc):
        ds = ung_vien_theo_muc[ten_muc]
        ds.sort(key=lambda b: (thu_tu.get((b.get("_email_noi_bo") or "").strip().lower(), 1 << 30),
                               -b.get("cap_nhat", 0)))
        for hang, b in enumerate(ds, start=1):
            hang_theo_id[b["id"]][ten_muc] = hang
        giu = ds[0]
        email_hien = giu.get("email") or giu.get("email_che", "")
        giu_hang_1.append({"nhom": ten_nhom, "muc": ten_muc,
                           "tai_khoan_id": giu["id"], "email": email_hien})
    return hang_theo_id, giu_hang_1


def dem_tong_quan(cac_ban: list[dict], han_muc_theo_id: dict) -> dict:
    """Đếm trạng thái + hết-limit theo mục cho một danh sách đã lọc
    (không tính hạng — hạng tính riêng theo từng nhóm).

    binh_thuong = dang_nhap ok VÀ không mục nào cạn; chua_biet = chưa có
    kết quả đăng nhập; tài khoản ok nhưng cạn hạn mức KHÔNG rơi vào ô
    trạng thái nào (đếm riêng ở het_limit_theo_muc — dải tổng hợp vẽ
    riêng hàng «Hết limit theo mục»); tạm dừng đếm riêng.
    het_limit_theo_muc CHỈ gồm mục mà danh sách có số liệu.
    """
    from app import kho_tai_khoan as ktk

    tong = len(cac_ban)
    binh_thuong = can_xu_ly = loi = tam_dung = chua_biet = 0
    thong_ke_muc: dict[str, dict] = {}
    for b in cac_ban:
        if b.get("trang_thai") == ktk.TRANG_THAI_TAM_DUNG:
            tam_dung += 1
            continue
        tt = b.get("dang_nhap", {}).get("trang_thai")
        if tt == "can_xu_ly":
            can_xu_ly += 1
        elif tt == "loi":
            loi += 1
        elif tt == "chua":
            chua_biet += 1
        elif tt == "ok":
            han_muc = han_muc_theo_id.get(b["id"]) or {}
            if not any(_co_so_lieu(m) and _can_muc(m) for m in han_muc.get("muc") or []):
                binh_thuong += 1
        han_muc = han_muc_theo_id.get(b["id"]) or {}
        for muc in han_muc.get("muc") or []:
            if not _co_so_lieu(muc):
                continue
            thong = thong_ke_muc.setdefault(
                muc["ten"], {"ten": muc["ten"], "nhan": muc["nhan"],
                             "so_tai_khoan_can": 0, "so_tai_khoan_co_so_lieu": 0})
            thong["so_tai_khoan_co_so_lieu"] += 1
            if _can_muc(muc):
                thong["so_tai_khoan_can"] += 1
    het_limit = [thong_ke_muc[k] for k in sorted(thong_ke_muc)]
    return {
        "tong": tong,
        "binh_thuong": binh_thuong,
        "can_xu_ly": can_xu_ly,
        "loi": loi,
        "tam_dung": tam_dung,
        "chua_biet": chua_biet,
        "het_limit_theo_muc": het_limit,
        "co_so_lieu_han_muc": bool(het_limit),
    }


def tinh_tong_quan(nhom: str, duong: str, cac_ban: list[dict],
                   han_muc_theo_id: dict, thu_tu: dict[str, int]) -> dict:
    """Tổng quan MỘT nhóm (NV285 §c2/2z): đếm + người giữ #1 từng mục."""
    dem = dem_tong_quan(cac_ban, han_muc_theo_id)
    _hang, giu_hang_1 = lap_bang_hang(nhom, duong, cac_ban, han_muc_theo_id, thu_tu)
    return {**dem, "giu_hang_1": giu_hang_1}
