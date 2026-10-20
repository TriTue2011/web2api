"""Module maytram — điều khiển máy trạm từ xa qua agent (hạng B, NV170).

PORT từ c2a (kho của chính chủ máy, ~/workspace/c2a-nv146 @ d879b03, CHỈ ĐỌC):
- services/device_agents.py → `phien_agent.py`: phiên theo id yêu cầu, trần lệnh
  chờ, trần hết giờ, allowlist fail-closed, hai lớp kiểm (lõi + agent),
  quyền ghi/nguồn/ghi-màn-hình mặc định TẮT.
- api/devices.py → thực thể chuẩn C1 của lõi (`thiet_bi`): đăng ký (sinh khoá,
  trả một lần), xoay khoá, đổi quyền, xoá (gỡ trên máy TRƯỚC khi xoá ở sổ).

KHÔNG port (ghi rõ trong báo cáo, mục «Phần KHÔNG port và vì sao»):
- «exec»/«kill» của c2a = chạy lệnh shell tuỳ ý / tắt tiến trình tuỳ ý —
  spec NV170 cấm («tập lệnh là TẬP ĐÓNG, không có chạy lệnh tuỳ ý»).
- Route REST/WebSocket của c2a: module web2api không tự mở đường; điểm gắn
  truyền tải (kênh hai chiều `gui`/`nhan`) do lõi quyết — xem README.

An toàn cứng:
- Mọi thực thể rui_ro_mac_dinh = «nguy_hiem»: lõi buộc quản trị xac_nhan:true
  và khách vào hàng chờ phê duyệt (SEC-004c/f) TRƯỚC khi module được gọi.
- Khoá máy (256 bit) mỗi máy một cái, chỉ trả đúng một lần, không vào log/
  phản hồi/suc_khoe; sổ lưu 0600 qua app.kho (mã hoá khi nghỉ nếu lõi bật).
- Kết quả agent là DỮ LIỆU KHÔNG TIN: cắt trần, bọc {nguon_ngoai, khong_tin}.
- Mặc định TẮT: chưa cài bật thì không có thực thể nào.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from app import kho as kho_loi

from . import phien_agent as pa
from .agent_gia import AgentGia  # noqa: F401 — phơi cho test và mô tả giao thức
from .so_thiet_bi import ID_QUAN_LY, SoThietBi

log = logging.getLogger("web2api.maytram")

_ID_QUAN_LY = ID_QUAN_LY
_LENH_QUAN_LY = ("dang_ky", "thu_hoi", "xoa", "xoay_khoa", "doi_quyen")

#: Trần độ sâu cấu trúc kết quả agent (như lõi: app/thiet_bi.py
#: `_kiem_tra_gia_tri_theo_luoc_do` chặn ở 6 tầng) và trần tổng số phần tử —
#: lớp chặn thứ hai sau ngân sách TỔNG ký tự của `_cat_tran` (NV170-SUA M3).
_DO_SAU_DAU_RA = 6
_TRAN_PHAN_TU_DAU_RA = 50_000


def _nguoi_goi_la_quan_tri() -> bool:
    """Module TỰ kiểm vai người gọi (NV170-SUA M2, theo mẫu khoa-cua).

    Lõi tính rủi ro theo THỰC THỂ và cho phép HẠ mức đó — module không được
    phụ thuộc: quản trị lỡ hạ rủi ro của thực thể maytram xuống «ghi»/«doc»
    thì khách có `thiet_bi:ghi` cũng KHÔNG được chạm vào module này. Chỉ
    `quan_tri`/`noi_bo` mới qua; thiếu ngữ cảnh hoặc lỗi khi đọc → False
    (đóng khi lỗi).
    """
    try:
        from app.ngu_canh import lay  # chỉ đọc, không gọi sâu vào lõi

        nc = lay()
    except Exception:
        return False
    if nc is None:
        return False
    dt = nc.danh_tinh or {}
    return str(dt.get("loai") or "") in ("quan_tri", "noi_bo")

_MO_TA_LENH = {
    "liet_ke": "Liệt kê thư mục trên máy trạm",
    "doc_tep": "Đọc một tệp trong thư mục cho phép",
    "thong_ke": "Thông tin một tệp/thư mục",
    "tim_tep": "Tìm tệp theo mẫu trong thư mục cho phép",
    "thong_tin": "Thông tin hệ thống của máy (lệnh cố định của agent)",
    "tai_nguyen": "Tài nguyên CPU/RAM/đĩa (lệnh cố định của agent)",
    "tien_trinh": "Danh sách tiến trình (lệnh cố định của agent)",
    "dich_vu": "Danh sách dịch vụ (lệnh cố định của agent)",
    "ghi_tep": "Ghi tệp (cần quyền ghi đã cấp cho máy)",
    "tao_thu_muc": "Tạo thư mục (cần quyền ghi)",
    "xoa": "Xoá tệp/thư mục (cần quyền ghi)",
    "noi_tep": "Nối thêm vào cuối tệp (cần quyền ghi)",
    "khoa_man_hinh": "Khoá màn hình (cần quyền nguồn)",
    "ngu": "Cho máy ngủ (cần quyền nguồn)",
    "dang_xuat": "Đăng xuất phiên người dùng trên máy (cần quyền nguồn)",
    "tat_may": "Tắt máy (cần quyền nguồn)",
    "khoi_dong_lai": "Khởi động lại máy (cần quyền nguồn)",
    "mo_khoa": "Mở khoá màn hình (cần quyền nguồn)",
    "chup_man_hinh": "Chụp ảnh màn hình (cần quyền ghi màn hình)",
    "chup_webcam": "Chụp webcam (cần quyền ghi màn hình)",
}
_LENH_CO_DUONG_DAN = ("liet_ke", "doc_tep", "thong_ke", "tim_tep",
                      "ghi_tep", "tao_thu_muc", "xoa", "noi_tep")


def _luc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tham_so_lenh(lenh: str) -> dict:
    thuoc = {"duong_dan": {"type": "string",
                           "description": "Đường dẫn trên máy trạm — phải nằm "
                                          "trong danh sách thư mục đã khai khi đăng ký"}}
    bat_buoc = []
    if lenh in _LENH_CO_DUONG_DAN:
        bat_buoc.append("duong_dan")
    if lenh == "ghi_tep":
        thuoc["noi_dung"] = {"type": "string",
                             "description": "Nội dung ghi vào tệp"}
        bat_buoc.append("noi_dung")
    if lenh == "tim_tep":
        thuoc["mau"] = {"type": "string",
                        "description": "Mẫu tên tệp (mặc định «*»)"}
    return {"type": "object", "required": bat_buoc,
            "additionalProperties": False, "properties": thuoc}


def _tinh_nang_may() -> list[dict]:
    return [{"hanh_dong": lenh, "mo_ta": _MO_TA_LENH[lenh],
             "tham_so": _tham_so_lenh(lenh)} for lenh in pa.LENH_DONG]


def _tinh_nang_quan_ly() -> list[dict]:
    return [
        {"hanh_dong": "dang_ky",
         "mo_ta": "Đăng ký MỘT máy trạm mới, sinh khoá agent (trả đúng MỘT lần). "
                  "Bắt buộc khai ít nhất một thư mục trong duong_dan (fail-closed). "
                  "Quyền ghi/nguồn/ghi màn hình đều mặc định TẮT.",
         "tham_so": {"type": "object", "required": ["ten_may", "duong_dan"],
                     "additionalProperties": False,
                     "properties": {
                         "ten_may": {"type": "string",
                                     "description": "Tên máy: a-z 0-9 _ - (2–31 ký tự)"},
                         "nhan": {"type": "string", "description": "Nhãn hiển thị"},
                         "duong_dan": {"type": "array",
                                       "items": {"type": "string"},
                                       "description": "Thư mục agent được phép chạm"},
                         "co_quyen_ghi": {"type": "boolean"},
                         "co_quyen_nguon": {"type": "boolean"},
                         "co_quyen_ghi_man_hinh": {"type": "boolean"}}}},
        {"hanh_dong": "thu_hoi",
         "mo_ta": "Thu hồi một máy: khoá chết vĩnh viễn, phiên đang mở bị ngắt. "
                  "Máy khác không bị ảnh hưởng.",
         "tham_so": {"type": "object", "required": ["ten_may"],
                     "additionalProperties": False,
                     "properties": {"ten_may": {"type": "string"}}}},
        {"hanh_dong": "xoa",
         "mo_ta": "Xoá máy khỏi sổ. go_tren_may=true (mặc định) nhờ agent tự gỡ "
                  "TRƯỚC khi khoá chết (như c2a — xoá ở sổ trước thì không còn "
                  "đường ra lệnh gỡ, máy kia vẫn tự chạy và gõ cửa bằng khoá chết).",
         "tham_so": {"type": "object", "required": ["ten_may"],
                     "additionalProperties": False,
                     "properties": {"ten_may": {"type": "string"},
                                    "go_tren_may": {"type": "boolean"}}}},
        {"hanh_dong": "xoay_khoa",
         "mo_ta": "Sinh khoá MỚI cho máy (trả đúng MỘT lần); khoá cũ chết ngay, "
                  "phiên đang chạy bằng khoá cũ bị ngắt.",
         "tham_so": {"type": "object", "required": ["ten_may"],
                     "additionalProperties": False,
                     "properties": {"ten_may": {"type": "string"}}}},
        {"hanh_dong": "doi_quyen",
         "mo_ta": "Đổi nhãn/thư mục/quyền/bật-tắt cho máy đã có, giữ nguyên khoá. "
                  "Có hiệu lực ngay cả với phiên đang mở.",
         "tham_so": {"type": "object", "required": ["ten_may"],
                     "additionalProperties": False,
                     "properties": {
                         "ten_may": {"type": "string"},
                         "nhan": {"type": "string"},
                         "duong_dan": {"type": "array",
                                       "items": {"type": "string"}},
                         "co_quyen_ghi": {"type": "boolean"},
                         "co_quyen_nguon": {"type": "boolean"},
                         "co_quyen_ghi_man_hinh": {"type": "boolean"},
                         "bat": {"type": "boolean"}}}},
    ]


def _so_nguyen(tham: dict, khoa: str, mac_dinh: int, nho: int, lon: int) -> int | None:
    gia_tri = tham.get(khoa, mac_dinh)
    try:
        so = int(gia_tri)
    except (TypeError, ValueError):
        return None
    if so < nho or so > lon:
        return None
    return so


class Module:
    """Lớp Module theo hợp đồng app/kham_pha.py (khả năng thiet_bi)."""

    def __init__(self, manifest: dict, _thu_muc_du_lieu: str | Path | None = None):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to", "maytram")
        self._thu_muc_du_lieu = Path(_thu_muc_du_lieu) if _thu_muc_du_lieu else None
        self._bat = False
        self._cua_so = 120
        self._tran = 20_000
        self._het_gio = 60.0
        self._het_gio_bat_tay = 15.0
        self._so: SoThietBi | None = None
        self._phien: pa.QuanLyPhien | None = None

    # ── hạ tầng ──────────────────────────────────────────────────────────────
    def _thu_muc(self) -> Path:
        if self._thu_muc_du_lieu is not None:
            return self._thu_muc_du_lieu
        return kho_loi.thu_muc_du_lieu() / "du_lieu" / "maytram"

    def _khoi_tao(self) -> None:
        self._so = SoThietBi(self._thu_muc())
        self._phien = pa.QuanLyPhien(
            self._so.lay, het_gio_lenh=self._het_gio,
            cua_so_giay=float(self._cua_so), het_gio_bat_tay=self._het_gio_bat_tay)

    # ── cấu hình ─────────────────────────────────────────────────────────────
    def nap(self, cau_hinh: dict) -> None:
        cau_hinh = cau_hinh or {}
        if self._phien is None:
            self._ap_cau_hinh(cau_hinh)
            self._khoi_tao()
        # nap lại không phá phiên đang mở; chỉ đổi các trần đọc tại chỗ.
        self._ap_cau_hinh(cau_hinh)

    def _ap_cau_hinh(self, cau_hinh: dict) -> None:
        self._bat = str(cau_hinh.get("bat", "false")).strip().lower() == "true"
        self._cua_so = _so_nguyen(cau_hinh, "cua_so_thoi_gian", 120, 30, 600) or 120
        self._tran = _so_nguyen(cau_hinh, "tran_dau_ra", 20_000, 1_024,
                                200_000) or 20_000
        self._het_gio = float(_so_nguyen(cau_hinh, "het_gio_lenh", 60, 1, 300)
                              or 60)
        self._het_gio_bat_tay = float(_so_nguyen(cau_hinh, "het_gio_bat_tay",
                                                 15, 1, 60) or 15)

    async def cai(self, cau_hinh: dict) -> dict:
        cau_hinh = cau_hinh or {}
        for khoa, mac_dinh, nho, lon in (
                ("cua_so_thoi_gian", 120, 30, 600),
                ("tran_dau_ra", 20_000, 1_024, 200_000),
                ("het_gio_lenh", 60, 1, 300),
                ("het_gio_bat_tay", 15, 1, 60)):
            if _so_nguyen(cau_hinh, khoa, mac_dinh, nho, lon) is None:
                return {"ok": False, "ket_qua_tung_khoa": [],
                        "loi": f"«{khoa}» phải là số nguyên trong [{nho}, {lon}]"}
        bat = str(cau_hinh.get("bat", "false")).strip().lower()
        luu = {
            "bat": "true" if bat == "true" else "false",
            "cua_so_thoi_gian": str(_so_nguyen(cau_hinh, "cua_so_thoi_gian",
                                               120, 30, 600)),
            "tran_dau_ra": str(_so_nguyen(cau_hinh, "tran_dau_ra", 20_000,
                                          1_024, 200_000)),
            "het_gio_lenh": str(_so_nguyen(cau_hinh, "het_gio_lenh", 60, 1, 300)),
            "het_gio_bat_tay": str(_so_nguyen(cau_hinh, "het_gio_bat_tay",
                                              15, 1, 60)),
        }
        return {"ok": True, "ket_qua_tung_khoa": [], "loi": None,
                "cau_hinh_luu": luu}

    # ── thực thể ─────────────────────────────────────────────────────────────
    def _thuc_the_quan_ly(self) -> dict:
        return {
            "id": f"{self.tien_to}.{_ID_QUAN_LY}",
            "ten": "Quản lý máy trạm",
            "loai": "quan_ly",
            "thuoc_tinh": {"nguon": "maytram", "so_may": len(self._so.danh_sach())},
            "rui_ro_mac_dinh": "nguy_hiem",
            "tinh_nang": _tinh_nang_quan_ly(),
        }

    def _thuc_the_may(self, ban_ghi: dict) -> dict:
        phien = self._phien.lay(ban_ghi["ten"]) if self._phien else None
        tt = phien.thong_tin if phien else {}
        return {
            "id": f"{self.tien_to}.{ban_ghi['ten']}",
            "ten": ban_ghi.get("nhan") or ban_ghi["ten"],
            "loai": "may_tram",
            "thuoc_tinh": {
                "ten_may_tu_khai": str(tt.get("ten_may") or "")[:40],
                "nen_tang": tt.get("nen_tang", ""),
                "phien_ban_agent": tt.get("phien_ban", ""),
                "ket_noi": "online" if phien else "offline",
                "quyen": {"ghi": bool(ban_ghi.get("co_quyen_ghi")),
                          "nguon": bool(ban_ghi.get("co_quyen_nguon")),
                          "ghi_man_hinh": bool(ban_ghi.get("co_quyen_ghi_man_hinh"))},
            },
            "rui_ro_mac_dinh": "nguy_hiem",
            "tinh_nang": _tinh_nang_may(),
        }

    async def thuc_the(self, loc: dict | None) -> list[dict]:
        if not self._bat or self._so is None:
            return []  # TẮT: không thực thể nào (mặc định theo spec)
        ra = [self._thuc_the_quan_ly()]
        for ban_ghi in self._so.danh_sach():
            if not ban_ghi.get("bat", True) or ban_ghi.get("da_thu_hoi"):
                continue
            ra.append(self._thuc_the_may(ban_ghi))
        if isinstance(loc, dict) and loc.get("loai"):
            ra = [e for e in ra if e.get("loai") == loc["loai"]]
        return ra

    async def trang_thai(self, ids: list[str]) -> dict[str, dict]:
        luc = _luc_iso()
        ra: dict[str, dict] = {}
        for i in ids:
            id_chuan = str(i)
            ten = id_chuan[len(self.tien_to) + 1:] if id_chuan.startswith(
                f"{self.tien_to}.") else id_chuan
            if ten == _ID_QUAN_LY:
                ra[id_chuan] = {"gia_tri": "bat" if self._bat else "tat",
                                "hop_le": True, "luc": luc}
                continue
            online = bool(self._phien and self._phien.lay(ten))
            ra[id_chuan] = {"gia_tri": "online" if online else "offline",
                            "hop_le": True, "luc": luc}
        return ra

    # ── thực hiện ────────────────────────────────────────────────────────────
    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict) -> dict:
        if self._so is None or self._phien is None:
            return {"ok": False, "loi": "module chưa được nạp"}
        if not self._bat:
            return {"ok": False,
                    "loi": "module maytram đang TẮT (cài đặt «bat» = true để dùng)"}
        tham_so = tham_so or {}
        ten = str(id)[len(self.tien_to) + 1:] if str(id).startswith(
            f"{self.tien_to}.") else str(id)
        if ten == _ID_QUAN_LY:
            return await self._thuc_hien_quan_ly(hanh_dong, tham_so)
        return await self._thuc_hien_may(ten, hanh_dong, tham_so)

    async def _thuc_hien_quan_ly(self, hanh_dong: str, tham_so: dict) -> dict:
        if not _nguoi_goi_la_quan_tri():
            return {"ok": False,
                    "loi": "Chỉ quản trị mới được quản lý máy trạm "
                           "(đăng ký/xoay khoá/thu hồi/đổi quyền/xoá)"}
        if hanh_dong not in _LENH_QUAN_LY:
            return {"ok": False,
                    "loi": f"hành động «{hanh_dong}» không hỗ trợ ở thực thể quản lý"}
        ten = str(tham_so.get("ten_may") or "").strip()

        if hanh_dong == "dang_ky":
            try:
                ban_ghi, khoa = self._so.dang_ky(
                    ten, str(tham_so.get("nhan") or ""),
                    [str(p) for p in (tham_so.get("duong_dan") or [])],
                    co_quyen_ghi=bool(tham_so.get("co_quyen_ghi", False)),
                    co_quyen_nguon=bool(tham_so.get("co_quyen_nguon", False)),
                    co_quyen_ghi_man_hinh=bool(
                        tham_so.get("co_quyen_ghi_man_hinh", False)))
            except ValueError as e:
                return {"ok": False, "loi": str(e)}
            log.info({"su_kien": "maytram_dang_ky", "may": ten,
                      "dau_van": ban_ghi["dau_van"]})
            return {
                "ok": True, "ten_may": ten, "khoa_agent": khoa,
                "dau_van": ban_ghi["dau_van"],
                "ghi_chu": ("Giữ khoá này — nó KHÔNG hiện lại ở đâu khác. Nhập "
                            "khoá này vào agent trên máy trạm; agent TỰ kết nối "
                            "ra tới địa chỉ agent của lõi (lõi công bố kèm khi "
                            "bật điểm gắn truyền tải — xem README module). "
                            "Mất khoá thì dùng hành động «xoay_khoa»."),
            }

        if hanh_dong == "thu_hoi":
            if not self._so.thu_hoi(ten):
                return {"ok": False, "loi": f"không thấy máy «{ten}»"}
            await self._phien.ngat(ten, "máy đã bị thu hồi")
            return {"ok": True, "ten_may": ten, "da_thu_hoi": True}

        if hanh_dong == "xoay_khoa":
            khoa_moi = self._so.xoay_khoa(ten)
            if khoa_moi is None:
                return {"ok": False, "loi": f"không thấy máy «{ten}»"}
            await self._phien.ngat(ten, "khoá đã xoay — chạy lại agent với khoá mới")
            return {"ok": True, "ten_may": ten, "khoa_agent": khoa_moi,
                    "ghi_chu": "Khoá mới chỉ hiện lần này. Khoá cũ đã chết."}

        if hanh_dong == "doi_quyen":
            thay_doi = {k: tham_so[k] for k in (
                "nhan", "duong_dan", "co_quyen_ghi", "co_quyen_nguon",
                "co_quyen_ghi_man_hinh", "bat") if k in tham_so}
            try:
                ban_ghi = self._so.doi_quyen(ten, **thay_doi)
            except ValueError as e:
                return {"ok": False, "loi": str(e)}
            if ban_ghi is None:
                return {"ok": False, "loi": f"không thấy máy «{ten}»"}
            if "bat" in thay_doi and not thay_doi["bat"]:
                # M6 (NV170-SUA): tắt máy là NGẮT PHIÊN NGAY (cùng đường với
                # thu hồi/xoay khoá ở M1) — chỉ đổi sổ thì phiên đang sống vẫn
                # nhận lệnh qua đường gọi thẳng, thực thể biến mất là chưa đủ.
                await self._phien.ngat(ten, "máy đã bị tắt trong sổ (doi_quyen)")
            else:
                phien = self._phien.lay(ten)
                if phien is not None:
                    phien.cfg = self._so.lay(ten) or phien.cfg
            return {"ok": True, "ten_may": ten, "ban_ghi": ban_ghi}

        # xoa — gỡ trên MÁY TRƯỚC rồi xoá ở sổ (thứ tự bắt buộc như c2a).
        go = {"da_thu": False}
        phien = self._phien.lay(ten)
        if tham_so.get("go_tren_may", True) and phien is not None:
            go["da_thu"] = True
            try:
                kq = await phien.gui_lenh("go_cai_dat", {})
                go["ok"] = bool(kq.get("ok"))
            except Exception:
                go["ok"] = False
        if not self._so.xoa(ten):
            return {"ok": False, "loi": f"không thấy máy «{ten}»"}
        await self._phien.ngat(ten, "máy đã bị xoá khỏi sổ")
        return {"ok": True, "ten_may": ten, "go_tren_may": go}

    async def _thuc_hien_may(self, ten: str, hanh_dong: str,
                             tham_so: dict) -> dict:
        if not _nguoi_goi_la_quan_tri():
            return {"ok": False,
                    "loi": "Chỉ quản trị mới được điều khiển máy trạm"}
        if hanh_dong not in pa.LENH_DONG:
            return {"ok": False,
                    "loi": (f"hành động «{hanh_dong}» không hỗ trợ — tập lệnh "
                            "của maytram là TẬP ĐÓNG (không chạy lệnh tuỳ ý)")}
        ban_ghi = self._so.lay(ten)
        if ban_ghi is None:
            return {"ok": False, "loi": f"không thấy máy «{ten}»"}
        if ban_ghi.get("da_thu_hoi"):
            return {"ok": False, "loi": f"máy «{ten}» đã bị thu hồi"}
        if not ban_ghi.get("bat", True):
            return {"ok": False, "loi": f"máy «{ten}» đang tắt trong sổ"}
        phien = self._phien.lay(ten)
        if phien is None:
            return {"ok": False,
                    "loi": f"máy «{ten}» chưa kết nối (agent chưa quay ra)"}
        # M1 (NV170-SUA): đối chiếu dấu vân khoá của PHIÊN với khoá HIỆN HÀNH
        # ở mỗi lệnh — phiên nào còn sót lại sau khi khoá đã xoay (chưa kịp
        # ngắt ở đường quản lý) thì chết tại đây, lệnh không tới kẻ giữ khoá cũ.
        if str(getattr(phien, "dau_van", "") or "") != str(
                ban_ghi.get("dau_van") or ""):
            await self._phien.ngat(ten, "khoá đã đổi — phiên giữ khoá cũ bị ngắt")
            return {"ok": False,
                    "loi": f"máy «{ten}» đã đổi khoá — phiên cũ bị ngắt, "
                           "agent phải kết nối lại bằng khoá hiện hành"}
        # Kiểm quyền ở LÕI trước khi gửi (tầng 1 — agent tự kiểm lại, tầng 2).
        if hanh_dong in pa.LENH_GHI and not ban_ghi.get("co_quyen_ghi"):
            return {"ok": False,
                    "loi": (f"máy «{ten}» KHÔNG được cấp quyền ghi "
                            "(co_quyen_ghi=false) — cấp qua hành động «doi_quyen» "
                            "và cũng phải bật cờ tương ứng trên agent")}
        if hanh_dong in pa.LENH_NGUON and not ban_ghi.get("co_quyen_nguon"):
            return {"ok": False,
                    "loi": (f"máy «{ten}» KHÔNG được cấp quyền nguồn "
                            "(co_quyen_nguon=false)")}
        if (hanh_dong in pa.LENH_GHI_MAN_HINH
                and not ban_ghi.get("co_quyen_ghi_man_hinh")):
            return {"ok": False,
                    "loi": (f"máy «{ten}» KHÔNG được cấp quyền ghi màn hình "
                            "(co_quyen_ghi_man_hinh=false)")}
        # Kiểm allowlist ở LÕI (tầng 1).
        for truong in pa.TRUONG_DUONG_DAN:
            p = tham_so.get(truong)
            if p and not pa.duong_dan_duoc_phep(ban_ghi.get("duong_dan"), str(p)):
                return {"ok": False,
                        "loi": (f"«{p}» ngoài phạm vi cho phép của máy. "
                                f"Được phép: {', '.join(ban_ghi.get('duong_dan') or [])}")}
        phien.cfg = ban_ghi  # quyền mới (sau doi_quyen) có hiệu lực ngay
        kq = await phien.gui_lenh(hanh_dong, dict(tham_so))
        return self._boc_ket_qua(kq, ten, hanh_dong)

    # ── kết quả là dữ liệu KHÔNG TIN ─────────────────────────────────────────
    def _cat_tran(self, obj):
        """Cắt kết quả agent ở trần cấu hình. Trả (bản cắt, đã cắt?).

        NV170-SUA M3: trần tính theo TỔNG ký tự của TOÀN cấu trúc (bộ đếm
        dùng chung cho cả lần đệ quy — bản cũ cắt TỪNG chuỗi nên 2000 khoá ×
        1 KB với trần 1024 vẫn trả ra ~2 MB), cộng hai lớp chặn độc lập:
        độ sâu tối đa `_DO_SAU_DAU_RA` (như lõi) và tổng số phần tử
        `_TRAN_PHAN_TU_DAU_RA` (phần tử vô định như số cũng không phình vô
        hạn). Nhờ trần độ sâu, hàm này KHÔNG bao giờ đệ quy sâu theo dữ liệu
        không tin (bản cũ lồng 3000 tầng làm RecursionError thoát thuc_hien).
        """
        st = {"con_lai": float(self._tran), "da_cat": False, "phan_tu": 0}

        def di(o, sau: int):
            if sau > _DO_SAU_DAU_RA or st["phan_tu"] >= _TRAN_PHAN_TU_DAU_RA:
                st["da_cat"] = True
                return "…"
            if isinstance(o, str):
                st["phan_tu"] += 1
                cho = max(0, int(st["con_lai"]) - 2)  # trừ hai dấu nháy JSON
                if len(o) > cho:
                    st["da_cat"] = True
                    st["con_lai"] = 0.0
                    return o[:cho]
                st["con_lai"] -= len(o) + 2
                return o
            if isinstance(o, dict):
                st["phan_tu"] += 1
                st["con_lai"] -= 2  # { }
                ra = {}
                for k, v in o.items():
                    ks = str(k)
                    if st["con_lai"] < len(ks) + 3:
                        st["da_cat"] = True
                        break
                    st["con_lai"] -= len(ks) + 3  # "khoá":
                    ra[ks] = di(v, sau + 1)
                if st["da_cat"]:
                    ra["bi_cat"] = True
                return ra
            if isinstance(o, (list, tuple)):
                st["phan_tu"] += 1
                st["con_lai"] -= 2  # [ ]
                ra = []
                for v in o:
                    if st["con_lai"] <= 0:
                        st["da_cat"] = True
                        break
                    st["con_lai"] -= 1  # dấu phẩy ngăn cách
                    ra.append(di(v, sau + 1))
                return ra
            # Số/bool/None…: tính theo độ dài dạng JSON, trần cứng cho repr lạ.
            st["phan_tu"] += 1
            st["con_lai"] -= min(len(repr(o)), 64)
            return o

        return di(obj, 0), st["da_cat"]

    def _boc_ket_qua(self, kq: dict, ten: str, hanh_dong: str) -> dict:
        """Bọc kết quả agent: DỮ LIỆU KHÔNG TIN — cắt trần, đóng dấu nguồn ngoài.

        Mọi lỗi khi xử lý kết quả (kể cả RecursionError từ cấu trúc lạ) đều
        thành `ok: false` — `thuc_hien` không bao giờ ném vì dữ liệu agent.
        """
        try:
            if isinstance(kq, dict) and kq.get("ok"):
                ket_qua, _ = self._cat_tran(
                    kq.get("ket_qua") if "ket_qua" in kq else kq)
                if isinstance(ket_qua, dict):
                    ket_qua = {k: v for k, v in ket_qua.items() if k != "ok"}
                return {"ok": True, "ket_qua": ket_qua,
                        "nguon_ngoai": True, "khong_tin": True,
                        "thiet_bi": ten, "lenh": hanh_dong}
            loi = str((kq or {}).get("loi") or "lỗi không rõ")[:500]
            return {"ok": False, "loi": loi, "nguon_ngoai": True,
                    "thiet_bi": ten, "lenh": hanh_dong}
        except Exception:
            log.warning({"su_kien": "maytram_ket_qua_khong_xu_ly_duoc",
                         "may": ten, "lenh": hanh_dong})
            return {"ok": False,
                    "loi": "kết quả từ agent không xử lý được "
                           "(quá lớn hoặc sai cấu trúc)",
                    "nguon_ngoai": True, "thiet_bi": ten, "lenh": hanh_dong}

    async def suc_khoe(self) -> dict:
        if not self._bat or self._so is None:
            return {"ok": True, "bat": False, "so_may": 0, "so_online": 0}
        ds = self._so.danh_sach()
        return {"ok": True, "bat": True, "so_may": len(ds),
                "so_online": len(self._phien.danh_sach_phien())
                if self._phien else 0}

    async def dung(self) -> None:
        if self._phien is not None:
            for phien in list(self._phien._phien.values()):
                await phien.dong("module dừng")
            self._phien._phien.clear()
