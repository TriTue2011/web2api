"""Bộ tự kiểm đăng nhập với dịch vụ giả cho Cổng MCP — chạy trong tiến trình con.

Chứng minh LOGIC ĐIỀU PHỐI của lõi chạy đúng:
- Tách dòng và phân loại lỗi bằng mã trạng thái.
- Giãn cách, dừng sớm khi hỏng liên tiếp, một lượt một lúc.
- Khôi phục nhiều tầng rẻ -> đắt, dừng ngay khi gặp «cần người».
- Dọn sạch bí mật tạm (BiMatTam) khỏi bộ nhớ sau khi dùng.
- Không bao giờ để lộ bí mật/canary ra kết quả.

Không chứng minh đăng nhập với nhà cung cấp thật được (việc đó là tool dang_nhap_bat_dau).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("web2api.cong_mcp.tu_kiem")

# Hạn mức thời gian và khoảng cách nghỉ
MAC_DINH_TOI_DA_GIAY = 60.0
MAC_DINH_NGHI_GIAY = 10.0
TRAN_XUAT_BYTE = 65536
#: Cờ do hàm dựng môi trường con đặt. Chạy tay tệp này (vd `docker exec … python app/cong_mcp_tu_kiem.py`) mà thiếu cờ thì
#: các ca sẽ ghi trạng thái giả vào THƯ MỤC DỮ LIỆU ĐANG PHỤC VỤ — nên thiếu cờ là thoát ngay, chưa nhập gì của `app`.
BIEN_DANH_DAU_CON = "W2A_TU_KIEM_CON"
#: Thư mục dữ liệu của tiến trình con PHẢI là thư mục tạm do hàm cha tạo: nằm dưới `tempfile.gettempdir()` và tên bắt đầu bằng tiền tố này.
TIEN_TO_THU_MUC_TAM = "w2a_mcp_tu_kiem_"


def _dung_moi_truong_con(thu_muc_tam: str) -> dict[str, str]:
    """Dựng môi trường tiến trình con sạch từ đầu (whitelist hẹp)."""
    goc_kho = str(Path(__file__).resolve().parent.parent)
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": goc_kho,
        "PYTHONDONTWRITEBYTECODE": "1",
        "WEB2API_DATA": thu_muc_tam,
        "TMPDIR": tempfile.gettempdir(),  # để con tính `gettempdir()` giống cha khi kiểm thư mục dữ liệu
        "WEB2API_ADMIN_KEY": secrets.token_urlsafe(32),
        "WEB2API_DANG_NHAP_GIAN_CACH_GIAY": "0",
        BIEN_DANH_DAU_CON: "1",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "",
    }


def chay_cac_ca(cac_ca: list[tuple[str, str, Callable[[], tuple[bool, str] | Any]]]) -> dict[str, Any]:
    """Bộ gom kết quả thuần túy — bắt mọi ngoại lệ của từng ca, không để sập bộ kiểm."""
    danh_sach = []
    tong = len(cac_ca)
    dat = 0
    hong = 0

    for ma, mo_ta, fn in cac_ca:
        try:
            res = fn()
            if asyncio.iscoroutine(res):
                res = asyncio.run(res)
            if isinstance(res, tuple) and len(res) == 2:
                ok, ly_do = res
            else:
                ok = bool(res)
                ly_do = "đạt" if ok else "không đạt"
        except BaseException as e:
            ok = False
            ten_loi = type(e).__name__
            ly_do = f"{ten_loi}: kiểm tra thất bại"

        if ok:
            dat += 1
        else:
            hong += 1

        danh_sach.append({
            "ma": str(ma),
            "mo_ta": str(mo_ta),
            "dat": bool(ok),
            "ly_do": str(ly_do)[:200],
        })

    return {
        "tong": tong,
        "dat": dat,
        "hong": hong,
        "ca": danh_sach,
    }


# ==============================================================================
# 12 CA KIỂM THỬ ĐIỀU PHỐI ĐĂNG NHẬP
# ==============================================================================

def ca_phan_loai_loi_theo_ma() -> tuple[bool, str]:
    from app.tai_khoan_hang_loat import phan_loai_loi

    bang = [
        (401, "sai_chung_thuc"),
        (403, "bi_cam"),
        (404, "khong_tim_thay"),
        (429, "han_muc"),
        (400, "yeu_cau_sai"),
        (500, "loi_may_chu"),
        (None, "loi_mang"),
    ]
    for ma_http, ma_ky_vong in bang:
        thu = phan_loai_loi(ma_http)
        if thu != ma_ky_vong:
            return False, f"HTTP {ma_http} -> {thu} (kỳ vọng {ma_ky_vong})"
    return True, "khớp toàn bộ bảng mã"


def ca_tach_dong_dung_khuon() -> tuple[bool, str]:
    from app.tai_khoan_hang_loat import tach_dong

    vb = (
        "# Dòng ghi chú\n"
        "hop-le-1@example.com|mat-khau-1|JBSWY3DPEHPK3PXP\n"
        "hop-le-2@example.com|cookie=sid=abcdef123456789\n"
        "dong-sai-email\n"
        "hop-le-1@example.com|mat-khau-trung\n"
    )
    ds, loi, bo_trung = tach_dong(vb)
    if len(ds) != 2:
        return False, f"Số dòng hợp lệ: {len(ds)} (kỳ vọng 2)"
    if len(loi) != 1 or loi[0].dong != 4:
        return False, f"Báo lỗi sai dòng: {loi}"
    if len(bo_trung) != 1 or bo_trung[0].email != "hop-le-1@example.com":
        return False, f"Bỏ trùng sai: {bo_trung}"

    for d in ds:
        d.xoa_bi_mat()
    return True, "tách đúng khuôn và báo đúng lỗi/trùng"


def ca_luot_chay_thanh_cong() -> tuple[bool, str]:
    from app.tai_khoan_hang_loat import BiMatTam, DongDaTach, KetQuaDangNhap, LuotChay

    bm = BiMatTam("canary-mk-luot-chay-001")
    tk = DongDaTach(dong=1, email="test1@example.com", loai="mat_khau", mat_khau=bm)

    async def ham_gia(dong):
        return KetQuaDangNhap(thanh_cong=True)

    lc = LuotChay([tk], ham_gia)
    tt = asyncio.run(lc.chay())

    if tt.xong != 1 or tt.loi != 0:
        return False, f"Kết quả lượt chạy: xong={tt.xong}, loi={tt.loi}"
    if not bm.da_xoa():
        return False, "Bí mật tạm chưa bị xoá sau lượt chạy"
    return True, "chạy thành công và đã xoá bí mật"


def ca_luot_chay_dung_sau_hong_lien_tiep() -> tuple[bool, str]:
    from app.tai_khoan_hang_loat import BiMatTam, DongDaTach, KetQuaDangNhap, LuotChay

    cac_tk = [
        DongDaTach(1, "tk1@example.com", "mat_khau", mat_khau=BiMatTam("mk1")),
        DongDaTach(2, "tk2@example.com", "mat_khau", mat_khau=BiMatTam("mk2")),
        DongDaTach(3, "tk3@example.com", "mat_khau", mat_khau=BiMatTam("mk3")),
    ]
    goi_dem = 0

    async def ham_gia(dong):
        nonlocal goi_dem
        goi_dem += 1
        return KetQuaDangNhap(thanh_cong=False, ma_trang_thai=401)

    lc = LuotChay(cac_tk, ham_gia)
    tt = asyncio.run(lc.chay())

    if goi_dem != 2:
        return False, f"Hàm giả bị gọi {goi_dem} lần (kỳ vọng 2)"
    if tt.bo_qua != 1:
        return False, f"Số dòng bỏ qua: {tt.bo_qua} (kỳ vọng 1)"
    if tt.ket_qua[2].ma_loi != "hong_2_lien_tiep":
        return False, f"Mã lỗi dòng 3: {tt.ket_qua[2].ma_loi}"
    return True, "dừng cả hàng sau 2 hỏng liên tiếp"


def ca_ma_loi_la_bi_thay() -> tuple[bool, str]:
    from app.tai_khoan_hang_loat import BiMatTam, DongDaTach, KetQuaDangNhap, LuotChay

    canary = "canary_bi_mat_ma_loi_99999"
    tk = DongDaTach(1, "test@example.com", "mat_khau", mat_khau=BiMatTam("mk"))

    async def ham_gia(dong):
        return KetQuaDangNhap(thanh_cong=False, ma_loi=f"Loi rat dai chua {canary} co khoang trang", ma_trang_thai=403)

    lc = LuotChay([tk], ham_gia)
    tt = asyncio.run(lc.chay())
    ma_kq = tt.ket_qua[0].ma_loi

    if canary in ma_kq:
        return False, "Canary lọt vào mã lỗi"
    if not re.fullmatch(r"[a-z0-9_]{1,64}", ma_kq):
        return False, f"Mã lỗi không chuẩn: {ma_kq}"
    return True, "mã lỗi lạ đã được thay bằng mã an toàn"


def ca_ngoai_le_module_khong_ro_ri() -> tuple[bool, str]:
    from app.tai_khoan_hang_loat import BiMatTam, DongDaTach, LuotChay

    canary = "canary_ngoai_le_bi_mat_88888"
    tk = DongDaTach(1, "test@example.com", "mat_khau", mat_khau=BiMatTam("mk"))

    async def ham_gia(dong):
        raise RuntimeError(f"Ngoại lệ thô: {canary}")

    lc = LuotChay([tk], ham_gia)
    tt = asyncio.run(lc.chay())
    chuoi_kq = json.dumps([b.__dict__ for b in tt.ket_qua], ensure_ascii=False)

    if canary in chuoi_kq:
        return False, "Canary ngoại lệ lọt vào kết quả"
    if tt.ket_qua[0].trang_thai != "loi":
        return False, f"Trạng thái không phải loi: {tt.ket_qua[0].trang_thai}"
    return True, "bắt ngoại lệ an toàn không lộ chi tiết"


def ca_mot_luot_mot_luc() -> tuple[bool, str]:
    from app.tai_khoan_hang_loat import BiMatTam, DaCoLuotChay, DongDaTach, KetQuaDangNhap, QuanLyLuotChay

    ql = QuanLyLuotChay()

    async def chay_kiem_tra():
        su_kien = asyncio.Event()

        async def ham_gia(dong):
            await su_kien.wait()
            return KetQuaDangNhap(True)

        tk1 = DongDaTach(1, "a@example.com", "mat_khau", mat_khau=BiMatTam("mk"))
        tk2 = DongDaTach(1, "b@example.com", "mat_khau", mat_khau=BiMatTam("mk"))

        ql.bat_dau([tk1], ham_gia, "mod-test")

        try:
            ql.bat_dau([tk2], ham_gia, "mod-test")
            su_kien.set()
            return False, "Không ném DaCoLuotChay khi bắt đầu lần hai"
        except DaCoLuotChay:
            su_kien.set()
            return True, "ném DaCoLuotChay đúng như kỳ vọng"

    return asyncio.run(chay_kiem_tra())


def ca_khoi_phuc_tang_a_thanh_cong() -> tuple[bool, str]:
    from types import SimpleNamespace
    from app.khoi_phuc_phien import KetQuaKhoiPhuc, TaiKhoanCanKhoiPhuc, TANG_A, khoi_phuc

    goi_b_dem = 0

    class ModGia:
        manifest = {"id": "mod-a"}

        async def lam_moi_phien(self, khoa):
            return KetQuaKhoiPhuc(thanh_cong=True, tang=TANG_A)

        async def dang_nhap_tu_dong(self, tk, td):
            nonlocal goi_b_dem
            goi_b_dem += 1
            return KetQuaKhoiPhuc(thanh_cong=False)

    tk = TaiKhoanCanKhoiPhuc(khoa="tk-khoa-a", email="a@example.com")
    kq = asyncio.run(khoi_phuc(ModGia(), tk))

    if not kq.thanh_cong or kq.tang != TANG_A:
        return False, f"Kết quả không thành công ở tầng a: {kq}"
    if goi_b_dem != 0:
        return False, "Tầng b bị gọi dù tầng a đã thành công"
    return True, "thành công ở tầng a và không gọi tầng b"


def ca_khoi_phuc_can_nguoi_dung_ngay() -> tuple[bool, str]:
    from app.khoi_phuc_phien import KetQuaKhoiPhuc, TaiKhoanCanKhoiPhuc, TANG_A, khoi_phuc

    goi_b_dem = 0

    class ModGia:
        manifest = {"id": "mod-cn"}

        async def lam_moi_phien(self, khoa):
            return KetQuaKhoiPhuc(thanh_cong=False, can_nguoi=True, huong_dan="Hãy giải captcha")

        async def dang_nhap_tu_dong(self, tk, td):
            nonlocal goi_b_dem
            goi_b_dem += 1
            return KetQuaKhoiPhuc(thanh_cong=False)

    tk = TaiKhoanCanKhoiPhuc(khoa="tk-khoa-cn", email="cn@example.com")
    kq = asyncio.run(khoi_phuc(ModGia(), tk))

    if not kq.can_nguoi:
        return False, "Không dừng ở trạng thái can_nguoi"
    if goi_b_dem != 0:
        return False, "Tầng b bị gọi dù tầng a báo can_nguoi"
    return True, "dừng ngay khi gặp tín hiệu cần người"


def ca_khoi_phuc_ngung_sau_hai_hong_tang_b() -> tuple[bool, str]:
    from app.khoi_phuc_phien import BiMatTam, KetQuaKhoiPhuc, TaiKhoanCanKhoiPhuc, TANG_B, khoi_phuc, lay_trang_thai

    goi_b_dem = 0
    mod_id = "mod-hong-b"

    class ModGia:
        manifest = {"id": mod_id}

        def mat_khau_da_luu(self, khoa):
            return BiMatTam("mat-khau-luu-canary-010")

        async def dang_nhap_tu_dong(self, tk, td):
            nonlocal goi_b_dem
            goi_b_dem += 1
            return KetQuaKhoiPhuc(thanh_cong=False, tang=TANG_B, ma_loi="sai_mat_khau")

    m = ModGia()
    tk = TaiKhoanCanKhoiPhuc(khoa="tk-hong-b", email="hb@example.com")

    asyncio.run(khoi_phuc(m, tk, trinh_duyet=object()))
    asyncio.run(khoi_phuc(m, tk, trinh_duyet=object()))
    if goi_b_dem != 2:
        return False, f"Tầng b được gọi {goi_b_dem} lần sau 2 lượt (kỳ vọng 2)"

    tt = lay_trang_thai(mod_id, "tk-hong-b")
    if not tt.get("ngung_tu_dang_nhap"):
        return False, f"Chưa đánh dấu ngung_tu_dang_nhap sau 2 lần hỏng: {tt}"

    asyncio.run(khoi_phuc(m, tk, trinh_duyet=object()))
    if goi_b_dem != 2:
        return False, "Tầng b vẫn bị gọi sau khi đã ngừng tự đăng nhập"

    return True, "ngừng tự đăng nhập sau 2 lần hỏng liên tiếp ở tầng b"


def ca_khoi_phuc_xoa_mat_khau_sau_dung() -> tuple[bool, str]:
    from app.khoi_phuc_phien import BiMatTam, KetQuaKhoiPhuc, TaiKhoanCanKhoiPhuc, TANG_B, khoi_phuc

    bm_truc_tiep = BiMatTam("mat-khau-tam-canary-007")
    ban_sao_kho: list[BiMatTam] = []
    goi_b_dem = 0

    class ModGia:
        manifest = {"id": "mod-mk"}

        def mat_khau_da_luu(self, khoa):
            b = BiMatTam("mat-khau-kho-canary-008")
            ban_sao_kho.append(b)
            return b

        async def dang_nhap_tu_dong(self, tk, td):
            nonlocal goi_b_dem
            goi_b_dem += 1
            return KetQuaKhoiPhuc(thanh_cong=False, tang=TANG_B, ma_loi="loi_thu")

    m = ModGia()
    # (1) mật khẩu người gọi đưa trực tiếp; (2) mật khẩu lấy từ kho của module
    asyncio.run(khoi_phuc(m, TaiKhoanCanKhoiPhuc(khoa="tk-mk", email="mk@example.com", mat_khau=bm_truc_tiep),
                          trinh_duyet=object()))
    asyncio.run(khoi_phuc(m, TaiKhoanCanKhoiPhuc(khoa="tk-mk2", email="mk2@example.com"), trinh_duyet=object()))

    if goi_b_dem != 2:
        return False, f"Tầng b được gọi {goi_b_dem} lần (kỳ vọng 2)"
    if not bm_truc_tiep.da_xoa():
        return False, "BiMatTam người gọi đưa chưa bị xoá sau tầng b"
    if not ban_sao_kho or not all(b.da_xoa() for b in ban_sao_kho):
        return False, "BiMatTam lấy từ kho module chưa bị xoá sau tầng b"
    return True, "mật khẩu tạm (cả hai nguồn) đã được xoá sau tầng b"


def ca_dau_ra_khong_chua_canary() -> tuple[bool, str]:
    from app.tai_khoan_hang_loat import BiMatTam, DongDaTach, KetQuaDangNhap, LuotChay

    canary = "CANARY_SECRET_SUPER_CANARY_12345"
    bm = BiMatTam(canary)
    tk = DongDaTach(1, "canary@example.com", "mat_khau", mat_khau=bm)

    async def ham_gia(dong):
        return KetQuaDangNhap(thanh_cong=True)

    lc = LuotChay([tk], ham_gia)
    tt = asyncio.run(lc.chay())

    chuoi_json = json.dumps([b.__dict__ for b in tt.ket_qua], ensure_ascii=False)
    if canary in chuoi_json:
        return False, "Canary xuất hiện trong JSON kết quả"
    return True, "kết quả sạch sẽ không chứa canary"


DANH_SACH_CAC_CA = [
    ("phan_loai_loi_theo_ma", "Phân loại mã HTTP sang mã lỗi ngắn", ca_phan_loai_loi_theo_ma),
    ("tach_dong_dung_khuon", "Tách dòng tài khoản đúng khuôn và lọc lỗi", ca_tach_dong_dung_khuon),
    ("luot_chay_thanh_cong", "Lượt chạy thành công và xoá bí mật tạm", ca_luot_chay_thanh_cong),
    ("luot_chay_dung_sau_hong_lien_tiep", "Dừng cả hàng sau 2 tài khoản hỏng liên tiếp", ca_luot_chay_dung_sau_hong_lien_tiep),
    ("ma_loi_la_bi_thay", "Mã lỗi lạ của module được ép về mã chuẩn", ca_ma_loi_la_bi_thay),
    ("ngoai_le_module_khong_ro_ri", "Bắt ngoại lệ module an toàn không lộ chi tiết", ca_ngoai_le_module_khong_ro_ri),
    ("mot_luot_mot_luc", "Một lượt chạy một lúc (chặn chạy chồng)", ca_mot_luot_mot_luc),
    ("khoi_phuc_tang_a_thanh_cong", "Khôi phục thành công ở tầng a không gọi tầng b", ca_khoi_phuc_tang_a_thanh_cong),
    ("khoi_phuc_can_nguoi_dung_ngay", "Gặp tín hiệu cần người dừng ngay không thử tầng đắt", ca_khoi_phuc_can_nguoi_dung_ngay),
    ("khoi_phuc_ngung_sau_hai_hong_tang_b", "Ngừng tự đăng nhập sau 2 lần hỏng tầng b", ca_khoi_phuc_ngung_sau_hai_hong_tang_b),
    ("khoi_phuc_xoa_mat_khau_sau_dung", "Xoá mật khẩu tạm sau tầng b kể cả khi lỗi", ca_khoi_phuc_xoa_mat_khau_sau_dung),
    ("dau_ra_khong_chua_canary", "Kết quả đầu ra không bao giờ lọt canary", ca_dau_ra_khong_chua_canary),
]


# ==============================================================================
# PHẦN CHA — Điều khiển tiến trình con và giới hạn
# ==============================================================================

_KHOA_TU_KIEM = asyncio.Lock()
_DANG_TU_KIEM = False
_TU_KIEM_LAN_CUOI: float = 0.0


def dat_lai_trang_thai() -> None:
    """Dùng cho test: xoá cờ đang chạy và mốc nghỉ."""
    global _DANG_TU_KIEM, _TU_KIEM_LAN_CUOI
    _DANG_TU_KIEM = False
    _TU_KIEM_LAN_CUOI = 0.0


def _doc_nghi_giay() -> float:
    try:
        v = float(os.environ.get("WEB2API_MCP_TU_KIEM_NGHI_GIAY", str(MAC_DINH_NGHI_GIAY)))
        return max(0.0, v)
    except (ValueError, TypeError):
        return MAC_DINH_NGHI_GIAY


def _doc_toi_da_giay() -> float:
    try:
        v = float(os.environ.get("WEB2API_MCP_TU_KIEM_TOI_DA_GIAY", str(MAC_DINH_TOI_DA_GIAY)))
        return max(1.0, v)
    except (ValueError, TypeError):
        return MAC_DINH_TOI_DA_GIAY


async def chay_tu_kiem() -> dict[str, Any]:
    """Chạy bộ tự kiểm trong tiến trình con riêng biệt, môi trường sạch."""
    global _DANG_TU_KIEM, _TU_KIEM_LAN_CUOI

    async with _KHOA_TU_KIEM:
        if _DANG_TU_KIEM:
            return {
                "isError": True,
                "loi": "dang_ban",
                "thong_diep": "Đang có một lượt tự kiểm đăng nhập đang chạy.",
            }

        bay_gio = time.monotonic()
        nghi_giay = _doc_nghi_giay()
        troi_qua = bay_gio - _TU_KIEM_LAN_CUOI
        if _TU_KIEM_LAN_CUOI > 0 and troi_qua < nghi_giay:
            con_lai = int(nghi_giay - troi_qua) + 1
            return {
                "isError": True,
                "loi": "phai_nghi",
                "thong_diep": "Cần nghỉ giữa các lần tự kiểm.",
                "thu_lai_sau_giay": max(1, con_lai),
            }

        _DANG_TU_KIEM = True

    thu_muc_tam = tempfile.mkdtemp(prefix=TIEN_TO_THU_MUC_TAM)
    goc_kho = str(Path(__file__).resolve().parent.parent)
    tep_chay = str(Path(__file__).resolve())
    env = _dung_moi_truong_con(thu_muc_tam)
    timeout = _doc_toi_da_giay()

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            tep_chay,
            cwd=goc_kho,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        try:
            stdout_data, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.CancelledError:
            try:
                proc.kill()
            except ProcessLookupError:
                log.info("MCP tự kiểm: tiến trình con đã thoát trước khi bị giết")
            raise
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                log.info("MCP tự kiểm: tiến trình con đã thoát trước khi bị giết")
            return {
                "isError": True,
                "loi": "het_gio",
                "thong_diep": "Quá thời gian tối đa khi chạy tự kiểm.",
            }

        if len(stdout_data) > TRAN_XUAT_BYTE:
            stdout_data = stdout_data[:TRAN_XUAT_BYTE]

        dong_cuoi = [d for d in stdout_data.decode("utf-8", errors="replace").splitlines() if d.strip()]
        dong = dong_cuoi[-1] if dong_cuoi else ""
        try:
            du_lieu = json.loads(dong)
            if not isinstance(du_lieu, dict):
                raise ValueError
            du_lieu["isError"] = False
            return du_lieu
        except Exception:
            return {
                "isError": True,
                "loi": "ket_qua_sai",
                "thong_diep": "Tiến trình con không trả về JSON hợp lệ.",
            }
    finally:
        shutil.rmtree(thu_muc_tam, ignore_errors=True)
        async with _KHOA_TU_KIEM:
            _DANG_TU_KIEM = False
            _TU_KIEM_LAN_CUOI = time.monotonic()


# ==============================================================================
# ĐIỂM CHẠY TIẾN TRÌNH CON
# ==============================================================================

if __name__ == "__main__":
    if os.environ.get(BIEN_DANH_DAU_CON) != "1":
        print("Từ chối: tệp này chỉ chạy làm tiến trình con của công cụ tu_kiem_dang_nhap "
              "(chạy tay sẽ ghi vào thư mục dữ liệu đang dùng).", file=sys.stderr)
        sys.exit(2)
    _du_lieu = os.environ.get("WEB2API_DATA", "")
    _tam = Path(tempfile.gettempdir()).resolve()
    _p = Path(_du_lieu).resolve() if _du_lieu else None
    if _p is None or not _p.name.startswith(TIEN_TO_THU_MUC_TAM) or _p.parent != _tam or not _p.is_dir():
        print("Từ chối: WEB2API_DATA phải là thư mục tạm do công cụ tạo (tiền tố "
              f"{TIEN_TO_THU_MUC_TAM}, nằm trực tiếp dưới thư mục tạm).", file=sys.stderr)
        sys.exit(2)
    goc = str(Path(__file__).resolve().parent.parent)
    if goc not in sys.path:
        sys.path.insert(0, goc)

    ket_qua = chay_cac_ca(DANH_SACH_CAC_CA)
    print(json.dumps(ket_qua, ensure_ascii=False))
    sys.exit(0)
