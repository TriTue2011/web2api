"""Công cụ khôi phục quyền quản trị bằng dòng lệnh trên máy chủ (NV79, NV67 nhóm 5 việc 13).

Tầng khôi phục cuối cùng: khi mất mật khẩu quản trị hoặc mất 2FA mà không còn cách nào đăng nhập,
người vận hành SSH vào máy chủ và chạy TRỰC TIẾP công cụ này. CỐ Ý không có đường web tương đương
(đó sẽ là cửa hậu — test ghim: không route nào trong app/tuyen.py nhắc tới module này).

Quy tắc an toàn:
- Chạy trên máy chủ, yêu cầu quyền ĐỌC thư mục dữ liệu (`WEB2API_DATA`); không đọc được → 500
  `khong_doc_du_lieu`, không chạm gì.
- Mật khẩu mới sinh ngẫu nhiên khi không truyền `--mat-khau`; mật khẩu thô chỉ hiện MỘT lần ra stdout
  của lệnh (để người vận hành thấy), KHÔNG bao giờ vào log, nhật ký hay ngoại lệ.
- Mỗi lần dùng ghi một dòng nhật ký kênh `quan_tri` (không nêu mật khẩu).
- Đặt lại mật khẩu đồng thời: bật lại tài khoản, tắt 2FA (xoá mọi trường `totp_*`), xoá khoá đăng
  nhập sai theo tên.

Chạy:  python3 -m app.khoi_phuc_quan_tri dat-lai-mat-khau [--ten <ten>] [--mat-khau <mk>]
        python3 -m app.khoi_phuc_quan_tri tat-2fa [--ten <ten>]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from app import kho, nguoi_dung, nhat_ky
from app.loi_openai import LoiOpenAI

log = logging.getLogger("web2api.khoi_phuc_quan_tri")

#: Mọi trường 2FA của tài khoản đều bắt đầu bằng tiền tố này — công cụ dọn hết khi đặt lại/tắt.
TIEN_TO_2FA = "totp_"


def _goc_du_lieu() -> Path:
    """Thư mục dữ liệu phải đọc được; không thì lỗi rõ, không chạm gì."""
    goc = kho.thu_muc_du_lieu()
    if not os.access(goc, os.R_OK | os.X_OK):
        raise LoiOpenAI(
            500,
            "Không đọc được thư mục dữ liệu — kiểm tra quyền của tiến trình chạy lệnh này.",
            code="khong_doc_du_lieu",
        )
    return goc


def _tim_admin(ten: str | None) -> dict:
    """Chọn tài khoản admin sẽ khôi phục. Nhiều admin mà không chỉ tên → 400 `nhieu_admin`;
    không có admin → 404; chỉ tên không có → 404."""
    ds = nguoi_dung._doc_nguoi_dung()
    tat_ca = [u for u in ds if u.get("vai") == "admin"]
    # Công cụ này là tầng khôi phục CUỐI và còn «bật lại tài khoản»: admin đang bị tắt (kho bị sửa tay hoặc hỏng — qua
    # API thì không tắt được admin bật cuối cùng) PHẢI cứu được. Có admin đang bật thì ưu tiên chúng; không còn
    # admin bật nào thì xét cả admin đang tắt.
    admins = [u for u in tat_ca if u.get("bat", True)] or tat_ca
    if ten:
        ten_chuan = ten.strip().lower()
        for u in tat_ca:
            if str(u.get("ten_dang_nhap", "")).lower() == ten_chuan:
                return dict(u)
        raise LoiOpenAI(404, f"Không tìm thấy tài khoản admin «{ten}».", code="admin_khong_co")
    if not admins:
        raise LoiOpenAI(404, "Không có tài khoản admin nào (cả đang bật lẫn đang tắt).", code="khong_co_admin")
    if len(admins) > 1:
        raise LoiOpenAI(
            400,
            "Có nhiều tài khoản admin — chỉ rõ bằng --ten.",
            code="nhieu_admin",
        )
    return dict(admins[0])


def _tat_2fa(trong_ds: dict) -> bool:
    """Xoá mọi trường 2FA (totp_*) của bản ghi; trả True nếu có trường bị xoá."""
    da_tat = False
    for k in list(trong_ds):
        if isinstance(k, str) and k.startswith(TIEN_TO_2FA):
            trong_ds.pop(k, None)
            da_tat = True
    return da_tat


async def dat_lai_mat_khau_admin(
    *, mat_khau: str | None = None, ten: str | None = None
) -> dict:
    """Đặt lại mật khẩu admin: bật lại tài khoản, tắt 2FA, xoá khoá đăng nhập sai.

    mat_khau=None → sinh ngẫu nhiên (trả về một lần trong «mat_khau_moi»).
    Trả {"ok", "ten_dang_nhap", "mat_khau_moi", "tat_2fa"} — mật khẩu thô chỉ ở đây, không ở nhật ký."""
    _goc_du_lieu()
    u = _tim_admin(ten)
    ten_dn = str(u.get("ten_dang_nhap", ""))
    id_ = str(u.get("id", ""))

    if mat_khau is None:
        mat_khau_moi = nguoi_dung.sinh_mat_khau_ngau_nhien()
    else:
        if not isinstance(mat_khau, str):
            raise LoiOpenAI(400, "Mật khẩu phải là chuỗi.", code="invalid_value")
        mat_khau_moi = mat_khau
    nguoi_dung.kiem_tra_chinh_sach_mat_khau(mat_khau_moi, ten_dn)

    bam = await nguoi_dung.bam_mat_khau(mat_khau_moi)  # băm chậm: NGOÀI khoá

    with nguoi_dung._KHOA_LUONG:
        ds = nguoi_dung._doc_nguoi_dung()
        tim = next((x for x in ds if x.get("id") == id_), None)
        if tim is None:
            raise LoiOpenAI(404, "Không tìm thấy tài khoản admin.", code="admin_khong_co")
        tim["bam_mat_khau"] = bam
        tim["bat"] = True
        tat_2fa = _tat_2fa(tim)
        nguoi_dung._ghi_nguoi_dung(ds)

    nguoi_dung.ghi_nhan_dang_nhap_dung(ten_dn)  # xoá khoá đăng nhập sai theo tên

    nhat_ky.ghi(
        "quan_tri",
        "thong_tin",
        "Công cụ khoi-phuc-quan-tri: đặt lại mật khẩu quản trị",
        ten_dang_nhap=ten_dn,
        tat_2fa=tat_2fa,
    )
    return {"ok": True, "ten_dang_nhap": ten_dn, "mat_khau_moi": mat_khau_moi, "tat_2fa": tat_2fa}


async def tat_2fa_admin(*, ten: str | None = None) -> dict:
    """Tắt 2FA của admin mà không đổi mật khẩu (dùng khi mất thiết bị TOTP)."""
    _goc_du_lieu()
    u = _tim_admin(ten)
    ten_dn = str(u.get("ten_dang_nhap", ""))
    id_ = str(u.get("id", ""))

    with nguoi_dung._KHOA_LUONG:
        ds = nguoi_dung._doc_nguoi_dung()
        tim = next((x for x in ds if x.get("id") == id_), None)
        if tim is None:
            raise LoiOpenAI(404, "Không tìm thấy tài khoản admin.", code="admin_khong_co")
        tat_2fa = _tat_2fa(tim)
        nguoi_dung._ghi_nguoi_dung(ds)

    nhat_ky.ghi(
        "quan_tri",
        "thong_tin",
        "Công cụ khoi-phuc-quan-tri: tắt 2FA quản trị",
        ten_dang_nhap=ten_dn,
    )
    return {"ok": True, "ten_dang_nhap": ten_dn, "tat_2fa": tat_2fa}


def _bo_phan_tich() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="khoi-phuc-quan-tri",
        description="Khôi phục quyền quản trị trên máy chủ (không có đường web tương đương).",
    )
    lenh = p.add_subparsers(dest="lenh", required=True)

    dl = lenh.add_parser("dat-lai-mat-khau", help="Đặt lại mật khẩu admin (bật lại tài khoản, tắt 2FA).")
    dl.add_argument("--ten", default=None, help="Tên đăng nhập admin (bắt buộc khi có nhiều admin).")
    dl.add_argument("--mat-khau", default=None,
                    help="Mật khẩu mới (không truyền → sinh ngẫu nhiên, in ra màn hình một lần).")

    t2 = lenh.add_parser("tat-2fa", help="Tắt 2FA của admin mà không đổi mật khẩu.")
    t2.add_argument("--ten", default=None, help="Tên đăng nhập admin (bắt buộc khi có nhiều admin).")
    return p


def main(argv: list[str] | None = None) -> int:
    """Điểm vào dòng lệnh. Mã thoát: 0 = xong; 2 = lỗi dự kiến (không admin, nhiều admin, mật khẩu
    yếu, không đọc được dữ liệu); 1 = lỗi bất ngờ. Không bao giờ in mật khẩu vào log/nhật ký."""
    args = _bo_phan_tich().parse_args(argv)
    try:
        if args.lenh == "dat-lai-mat-khau":
            tu_sinh = args.mat_khau is None
            ket_qua = asyncio.run(dat_lai_mat_khau_admin(mat_khau=args.mat_khau, ten=args.ten))
            if tu_sinh:
                # Mật khẩu sinh ngẫu nhiên phải hiện cho người vận hành THẤY — chỉ stdout của lệnh này,
                # không log/nhật ký. Người dùng tự truyền --mat-khau thì không in (họ đã biết).
                print(f"Mật khẩu mới (chỉ hiện một lần): {ket_qua['mat_khau_moi']}", flush=True)
            print(f"Đã đặt lại mật khẩu cho «{ket_qua['ten_dang_nhap']}»."
                  + (" Đã tắt 2FA." if ket_qua["tat_2fa"] else ""))
            return 0
        ket_qua = asyncio.run(tat_2fa_admin(ten=args.ten))
        print(f"Đã tắt 2FA cho «{ket_qua['ten_dang_nhap']}».")
        return 0
    except LoiOpenAI as e:
        print(f"Lỗi: {e.than['error']['message']}", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 — lỗi bất ngờ: báo gọn, không lộ chi tiết
        log.exception("Công cụ khoi-phuc-quan-tri lỗi bất ngờ")
        print("Lỗi bất ngờ — xem log máy chủ.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
