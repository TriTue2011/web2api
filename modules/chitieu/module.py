"""Module ``chi-tieu`` — sổ chi tiêu cá nhân theo hũ (JARS), công cụ C2 cho trợ lý.

Port từ c2a ``services/chi_tieu/`` + ``api/chi_tieu.py`` @ d879b03 (bản gốc
``/opt/claude-c2a/chatgpt2api/...`` — đường /opt/claude-c2a không còn trong môi trường này,
nguồn đọc tại bản checkout d879b03 ở ``~/workspace/c2a-nv146``), chỉ đổi lớp kết nối theo
khuôn module web2api (thực thể ``cong_cu`` + ``thuc_hien``); nghiệp vụ nằm nguyên ở
``nghiep_vu.py`` / ``ngan_sach.py`` / ``kho.py``.

Bảo mật dữ liệu (dữ liệu tài chính cá nhân nhạy cảm):
- Mỗi danh tính đăng nhập một sổ riêng: ``thuc_hien`` đọc danh tính từ ``app.ngu_canh``
  (SEC-002, chỉ đọc) rồi ràng buộc ``so_id`` theo danh tính đó — KHÔNG nhận ``so_id`` từ
  tham số, nên không có đường IDOR (kể cả quản trị cũng chỉ chạm sổ của chính mình khi dùng
  công cụ; xem thêm README.md). Thiếu ngữ cảnh → sổ dùng chung của nhà (``chu`` NULL), chỉ
  lối gọi nội bộ không danh tính mới chạm tới.
- Dữ liệu ở SQLite ``<WEB2API_DATA>/du_lieu/chi-tieu/chi_tieu.db`` (0600, thư mục 0700),
  ghi theo giao dịch (nguyên tử); tệp hỏng → lỗi rõ, KHÔNG xoá/ghi đè (tiền lệ NV20).
- Số tiền là số nguyên VNĐ (không float). Ghi chú là DỮ LIỆU: lưu nguyên văn, tầng hiển thị
  chịu trách nhiệm escape (không thực thi HTML từ ghi chú ở bất kỳ đường nào của module).

KHÔNG port (ghi rõ ở README.md mục «Khác biệt với c2a»):
- ``email_chi.py`` (đọc hộp thư IMAP tự ghi chi) — kéo kênh email; phụ thuộc module ``email``
  (chưa gộp vào main), chỉ để lại điểm nối.
- Gửi cảnh báo qua kênh chat + vòng lặp 30 phút của ``canh_bao.py`` c2a — chỉ port logic tính
  mốc; xem cảnh báo qua công cụ ``xem_canh_bao``.
- Liên kết sổ bằng mã 6 số cho bot Zalo/Telegram của c2a — web2api cách ly theo danh tính.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import canh_bao as _cb
from . import kho as _kho
from . import nghiep_vu as _nv

log = logging.getLogger("web2api.modules.chi_tieu")

_ID_DOC = "chi_tieu.doc"
_ID_GHI = "chi_tieu.ghi"

_CHUOI = {"type": "string"}
_SO_NGUYEN = {"type": "integer"}
_SO = {"type": "number"}


def _hd(ten: str, mo_ta: str, thuoc: dict[str, Any], bat_buoc: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"hanh_dong": ten, "mo_ta": mo_ta,
            "tham_so": {"type": "object", "required": list(bat_buoc), "additionalProperties": False,
                        "properties": thuoc}}


_DOC: list[dict[str, Any]] = [
    _hd("xem_ngan_sach", "Xem ngân sách kỳ: từng hũ (hạn mức, đã chi, còn lại, % đã dùng), tổng, cảnh báo.",
        {"thang": {**_CHUOI, "description": "Kỳ YYYY-MM; bỏ trống = kỳ hiện tại"}}),
    _hd("xem_lich_su", "Lịch sử khoản chi trong kỳ (mới nhất trước), kèm tên hũ.",
        {"thang": {**_CHUOI, "description": "Kỳ YYYY-MM; bỏ trống = kỳ hiện tại"}}),
    _hd("thong_ke", "Thống kê tổng chi theo kỳ và theo ngày của kỳ hiện tại.",
        {"so_ky": {**_SO_NGUYEN, "description": "Số kỳ lùi (1–12), mặc định 6"}}),
    _hd("de_xuat", "Đề xuất phân bổ % hũ theo chi thực tế 3 kỳ gần đây + hũ đang sát/vượt hạn mức.", {}),
    _hd("xem_canh_bao", "Cảnh báo ngân sách đang áp dụng (hũ/tổng đã chạm ngưỡng đã đặt).", {}),
    _hd("xem_cong_ty", "Số dư tạm ứng công ty đang mở + lịch sử giải chi.", {}),
    _hd("xem_hu", "Danh sách hũ của sổ và tổng % tỷ lệ.", {}),
]

_GHI: list[dict[str, Any]] = [
    _hd("ghi_chi", "Ghi MỘT khoản chi vào một hũ. Vượt tổng ngân sách kỳ → trả can_xac_nhan=true và CHƯA ghi; "
        "chỉ gọi lại với xac_nhan_vuot_tong=true khi người dùng đồng ý rõ ràng.",
        {"hu": {**_CHUOI, "description": "Tên hũ hoặc id hũ"}, "so_tien": _SO_NGUYEN,
         "ghi_chu": _CHUOI, "xac_nhan_vuot_tong": {"type": "boolean"}}, ("hu", "so_tien")),
    _hd("sua_chi", "Sửa khoản chi (hũ / số tiền / ghi chú). Tăng tiền cũng qua cổng vượt tổng như ghi mới.",
        {"id": _SO_NGUYEN, "hu": _CHUOI, "so_tien": _SO_NGUYEN, "ghi_chu": _CHUOI,
         "xac_nhan_vuot_tong": {"type": "boolean"}}, ("id",)),
    _hd("xoa_chi", "Xoá một khoản chi.", {"id": _SO_NGUYEN}, ("id",)),
    _hd("tach_chi", "Tách một khoản chi thành ≥2 mục (tổng phải bằng khoản gốc).",
        {"id": _SO_NGUYEN, "danh_sach": {"type": "array", "items": {"type": "object"}}}, ("id", "danh_sach")),
    _hd("thu_nhap_them", "Ghi thu nhập thêm của kỳ (tăng hạn mức mọi hũ theo tỷ lệ).",
        {"mo_ta": _CHUOI, "so_tien": _SO_NGUYEN}, ("mo_ta", "so_tien")),
    _hd("chi_phi_dac_biet", "Ghi chi phí đặc biệt (trừ vào các hũ hứng chi phí đặc biệt theo thứ tự).",
        {"mo_ta": _CHUOI, "so_tien": _SO_NGUYEN}, ("mo_ta", "so_tien")),
    _hd("sua_ky", "Sửa mục thu nhập thêm / chi phí đặc biệt.",
        {"loai": {**_CHUOI, "enum": ["thu_nhap_them", "chi_phi_dac_biet"]}, "id": _SO_NGUYEN,
         "mo_ta": _CHUOI, "so_tien": _SO_NGUYEN}, ("loai", "id")),
    _hd("xoa_ky", "Xoá mục thu nhập thêm / chi phí đặc biệt.",
        {"loai": {**_CHUOI, "enum": ["thu_nhap_them", "chi_phi_dac_biet"]}, "id": _SO_NGUYEN}, ("loai", "id")),
    _hd("them_hu", "Thêm hũ mới vào sổ.",
        {"ten": _CHUOI, "ty_le": _SO, "thu_tu_bu": _SO_NGUYEN, "thu_tu_dac_biet": _SO_NGUYEN}, ("ten",)),
    _hd("sua_hu", "Sửa hũ (tên / tỷ lệ % / thứ tự bù / thứ tự hứng chi phí đặc biệt).",
        {"id": _SO_NGUYEN, "ten": _CHUOI, "ty_le": _SO, "thu_tu": _SO_NGUYEN,
         "thu_tu_bu": _SO_NGUYEN, "thu_tu_dac_biet": _SO_NGUYEN}, ("id",)),
    _hd("xoa_hu", "Xoá hũ (còn khoản chi thì xoá mềm — lịch sử giữ nguyên).", {"id": _SO_NGUYEN}, ("id",)),
    _hd("luu_cau_hinh", "Lưu cấu hình sổ: tên, lương kỳ, ngày bắt đầu kỳ (1–28), ngưỡng cảnh báo.",
        {"ten": _CHUOI, "luong": _SO_NGUYEN, "ngay_bat_dau": _SO_NGUYEN,
         "nguong": {"type": "array", "items": _SO}}),
    _hd("cong_ty_tam_ung", "Ghi nhận tạm ứng công ty.", {"so_tien": _SO_NGUYEN, "mo_ta": _CHUOI}, ("so_tien", "mo_ta")),
    _hd("cong_ty_chi", "Ghi chi hộ công ty.", {"so_tien": _SO_NGUYEN, "mo_ta": _CHUOI}, ("so_tien", "mo_ta")),
    _hd("cong_ty_sua", "Sửa giao dịch công ty đang mở.",
        {"id": _SO_NGUYEN, "loai": {**_CHUOI, "enum": ["tam_ung", "chi"]}, "so_tien": _SO_NGUYEN, "mo_ta": _CHUOI},
        ("id",)),
    _hd("cong_ty_xoa", "Xoá giao dịch công ty đang mở.", {"id": _SO_NGUYEN}, ("id",)),
    _hd("giai_chi", "Giải chi: chốt toàn bộ giao dịch công ty đang mở thành một kỳ.", {}),
]

_TEN_DOC = {h["hanh_dong"] for h in _DOC}
_TEN_GHI = {h["hanh_dong"] for h in _GHI}


class Module:
    def __init__(self, manifest: dict, _thu_muc_du_lieu: str | None = None):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to") or "chi_tieu"
        goc = Path(_thu_muc_du_lieu) if _thu_muc_du_lieu else None
        if goc is None and os.environ.get("WEB2API_DATA"):
            goc = Path(os.environ["WEB2API_DATA"]) / "du_lieu" / "chi-tieu"
        self._goc = goc

    # ── nền tảng ──────────────────────────────────────────────────────────
    def nap(self, cau_hinh: dict) -> None:
        if self._goc is not None:
            self._goc.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self._goc, 0o700)
            except OSError:
                pass
            _kho.dat_duong(self._goc / "chi_tieu.db")

    async def cai(self, cau_hinh: dict) -> dict:
        return {"ok": True, "ket_qua_tung_khoa": {}, "loi": None, "cau_hinh_luu": {}}

    async def models(self) -> list[dict]:
        return []

    async def suc_khoe(self) -> dict:
        # Không kèm số tiền hay tên sổ: chỉ trạng thái (sổ thuộc dữ liệu nhạy cảm).
        return {"ok": True}

    async def dung(self) -> None:
        return None

    # ── thực thể / hành động ──────────────────────────────────────────────
    async def thuc_the(self, loc=None) -> list[dict]:
        doc = {"id": _ID_DOC, "ten": "Chi tiêu — xem (ngân sách, lịch sử, thống kê, cảnh báo)",
               "loai": "cong_cu", "thuoc_tinh": {"du_lieu": "cuc_bo"},
               "tinh_nang": _DOC, "rui_ro_mac_dinh": "doc"}
        ghi = {"id": _ID_GHI, "ten": "Chi tiêu — ghi (khoản chi, hũ, cấu hình, tạm ứng công ty)",
               "loai": "cong_cu", "thuoc_tinh": {"du_lieu": "cuc_bo"},
               "tinh_nang": _GHI, "rui_ro_mac_dinh": "ghi"}
        return [doc, ghi]

    async def trang_thai(self, ids) -> dict:
        """Trạng thái THẬT tại thời điểm gọi (hợp đồng C1): module thuần cục
        bộ — sẵn sàng khi thư mục dữ liệu của module đã mở được (self._goc có
        sau `nap`); chưa nạp xong thì báo «chua_cai». KHÔNG nhúng số tiền/số
        dư vào trạng thái (dữ liệu tài chính chỉ ra qua hành động có kiểm
        quyền)."""
        luc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        gia_tri = "san_sang" if getattr(self, "_goc", None) else "chua_cai"
        return {str(i): {"gia_tri": gia_tri, "luc": luc} for i in (ids or [])}

    def _so_id(self, danh_tinh: dict | None) -> int:
        """so_id ràng buộc theo danh tính: có danh tính → sổ của danh tính (tạo khi mở lần đầu);
        không có → sổ dùng chung của nhà (chu NULL, tạo khi cần)."""
        if danh_tinh and str(danh_tinh.get("id") or ""):
            s = _nv.so_cua_tai_khoan(danh_tinh)
            return int(s["id"])  # type: ignore[index]
        from . import kho
        with kho.db() as c:
            r = c.execute("SELECT id FROM so WHERE chu IS NULL ORDER BY id LIMIT 1").fetchone()
        if r is not None:
            return int(r["id"])
        return int(kho.tao_so("Sổ của nhà", None)["id"])

    def _danh_tinh(self) -> dict | None:
        try:
            from app import ngu_canh
            nc = ngu_canh.lay()
        except Exception:  # noqa: BLE001 — không có ngữ cảnh lõi thì coi là gọi nội bộ
            return None
        return dict(nc.danh_tinh) if nc is not None else None

    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict | None) -> dict:
        ts = dict(tham_so or {})
        if id == _ID_DOC:
            cho_phep = _TEN_DOC
        elif id == _ID_GHI:
            cho_phep = _TEN_GHI
        else:
            return {"ok": False, "loi": "khong_thay_thuc_the"}
        if hanh_dong not in cho_phep:
            return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}
        try:
            so_id = self._so_id(self._danh_tinh())
            return {"ok": True, **self._lam(so_id, hanh_dong, ts)}
        except _nv.LoiChiTieu as exc:
            return {"ok": False, "loi": str(exc)}
        except sqlite3.DatabaseError:
            log.error("chi-tieu: tệp dữ liệu hỏng hoặc không đọc được — KHÔNG ghi đè, cần khôi phục từ sao lưu")
            return {"ok": False, "loi": "du_lieu_hong"}
        except Exception:  # noqa: BLE001 — module không làm sập tiến trình lõi
            log.exception("chi-tieu: lỗi khi thực hiện %s", hanh_dong)
            return {"ok": False, "loi": "loi_noi_bo"}

    def _lam(self, so_id: int, viec: str, ts: dict) -> dict:
        nv = _nv
        if viec == "xem_ngan_sach":
            return nv.xem_ngan_sach(so_id, str(ts.get("thang") or ""))
        if viec == "xem_lich_su":
            return nv.xem_lich_su(so_id, str(ts.get("thang") or ""))
        if viec == "thong_ke":
            return nv.thong_ke(so_id, int(ts.get("so_ky") or 6))
        if viec == "de_xuat":
            return nv.de_xuat(so_id)
        if viec == "xem_canh_bao":
            ds = _cb.canh_bao_hien_tai(so_id)
            return {"co_canh_bao": bool(ds), "canh_bao": ds}
        if viec == "xem_cong_ty":
            return nv.xem_cong_ty(so_id)
        if viec == "xem_hu":
            return {"hu": _kho.ds_hu(so_id), "tong_ty_le": nv.tong_ty_le(so_id)}
        if viec == "ghi_chi":
            return nv.ghi_chi(so_id, ts.get("hu"), ts.get("so_tien"), str(ts.get("ghi_chu") or ""),
                              bool(ts.get("xac_nhan_vuot_tong")), nguon="cong_cu")
        if viec == "sua_chi":
            return nv.sua_chi(so_id, int(ts.get("id") or 0), hu=ts.get("hu"), so_tien=ts.get("so_tien"),
                              ghi_chu=ts.get("ghi_chu"), xac_nhan_vuot_tong=bool(ts.get("xac_nhan_vuot_tong")))
        if viec == "xoa_chi":
            return nv.xoa_chi(so_id, int(ts.get("id") or 0))
        if viec == "tach_chi":
            return nv.tach_chi(so_id, int(ts.get("id") or 0), ts.get("danh_sach") or [])
        if viec in ("thu_nhap_them", "chi_phi_dac_biet"):
            return nv.ghi_ky(viec, so_id, ts.get("mo_ta"), ts.get("so_tien"))
        if viec == "sua_ky":
            return nv.sua_ky(_bang_ky(ts.get("loai")), so_id, int(ts.get("id") or 0),
                             ts.get("mo_ta"), ts.get("so_tien"))
        if viec == "xoa_ky":
            return nv.xoa_ky(_bang_ky(ts.get("loai")), so_id, int(ts.get("id") or 0))
        if viec == "them_hu":
            return nv.them_hu(so_id, ts.get("ten"), ts.get("ty_le", 0), ts.get("thu_tu_bu", 50),
                              ts.get("thu_tu_dac_biet", 0))
        if viec == "sua_hu":
            return nv.sua_hu(so_id, int(ts.get("id") or 0),
                             **{k: ts[k] for k in ("ten", "ty_le", "thu_tu", "thu_tu_bu", "thu_tu_dac_biet")
                                if k in ts})
        if viec == "xoa_hu":
            return nv.xoa_hu(so_id, int(ts.get("id") or 0))
        if viec == "luu_cau_hinh":
            return nv.luu_cau_hinh(so_id, ten=ts.get("ten"), luong=ts.get("luong"),
                                   ngay_bat_dau=ts.get("ngay_bat_dau"), nguong=ts.get("nguong"))
        if viec in ("cong_ty_tam_ung", "cong_ty_chi"):
            return nv.ghi_cong_ty(so_id, "tam_ung" if viec == "cong_ty_tam_ung" else "chi",
                                   ts.get("so_tien"), ts.get("mo_ta"))
        if viec == "cong_ty_sua":
            truong: dict[str, Any] = {}
            if ts.get("loai") is not None:
                if ts["loai"] not in ("tam_ung", "chi"):
                    raise nv.LoiChiTieu("loai là tam_ung hoặc chi")
                truong["loai"] = ts["loai"]
            if ts.get("so_tien") is not None:
                truong["so_tien"] = nv._tien(ts["so_tien"])
            if ts.get("mo_ta") is not None:
                truong["mo_ta"] = nv._chu(ts["mo_ta"], "mo_ta")
            if not truong or not _kho.sua_cong_ty(so_id, int(ts.get("id") or 0), **truong):
                raise nv.LoiChiTieu("Không có giao dịch đang mở này (đã giải chi thì không sửa được).")
            return {"da_sua": True}
        if viec == "cong_ty_xoa":
            if not _kho.xoa_cong_ty(so_id, int(ts.get("id") or 0)):
                raise nv.LoiChiTieu("Không có giao dịch đang mở này (đã giải chi thì không xoá được).")
            return {"da_xoa": True}
        if viec == "giai_chi":
            return nv.giai_chi(so_id)
        return {"loi": f"viec không hỗ trợ: {viec}"}


def _bang_ky(loai: Any) -> str:
    if loai == "thu_nhap_them":
        return "thu_nhap_them"
    if loai == "chi_phi_dac_biet":
        return "chi_phi_dac_biet"
    raise _nv.LoiChiTieu("loai là thu_nhap_them hoặc chi_phi_dac_biet")
