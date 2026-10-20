"""Sổ đăng ký máy trạm của module maytram — kho khoá riêng mỗi máy (NV170).

Nguồn: thiết kế mới cho khuôn web2api, đi theo mô hình sổ `device_agents`
của ~/workspace/c2a-nv146/services/device_agents.py @ d879b03 («mỗi thiết bị
một token, khai tay, không hiện lại») và mẫu lưu trữ của kho lõi
(app.kho.ghi_bi_mat_tep — tệp 0600, ghi nguyên tử, mã hoá khi nghỉ nếu lõi cấu hình).
KHÔNG port từ c2a: sổ của c2a nằm trong config của gateway (đọc lại được khoá
bất cứ lúc nào); ở đây khoá CHỈ được trả đúng một lần lúc đăng ký/xoay khoá,
mọi lần đọc sau đều không chứa khoá (chỉ còn dấu vân sha256 rút gọn để đối chiếu).

Luật an toàn:
- Khoá máy là bí mật kiểu `bi_mat`: không vào log/phản hồi/suc_khoe.
- Đăng ký THIẾU danh sách thư mục cho phép → từ chối (fail-closed, như c2a:
  «thiết bị không có thư mục nào thì để rỗng dễ bị hiểu nhầm là mở tất cả»).
- Thu hồi = vô hiệu khoá vĩnh viễn trong sổ (không xoá vết): khoá cũ không
  bắt tay lại được; muốn dùng lại phải đăng ký mới/xoay khoá.
- Mọi thao tác đọc-sửa-ghi đi qua một khoá threading.RLock (như app/kho.py).
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from app import kho as kho_loi

# Khuôn tên máy — chép đúng khuôn của c2a api/devices.py (dòng đăng ký).
MAU_TEN = re.compile(r"\A[a-z0-9][a-z0-9_-]{1,30}\Z")

TEN_TEP = "thiet_bi.json"


def _bay_gio_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dau_van(khoa_hex: str) -> str:
    """Dấu vân sha256 rút gọn của khoá — đủ đối chiếu, không đủ dùng lại."""
    return hashlib.sha256(bytes.fromhex(khoa_hex)).hexdigest()[:12]


class SoThietBi:
    """Sổ {ten_may: bản ghi} bền vững trong <WEB2API_DATA>/du_lieu/maytram/."""

    def __init__(self, thu_muc: Path):
        self._thu_muc = Path(thu_muc)
        self._tep = self._thu_muc / TEN_TEP
        self._khoa = threading.RLock()

    # ── đọc/ghi nguyên tử ────────────────────────────────────────────────────
    def _doc(self) -> dict:
        try:
            du = kho_loi.doc_bi_mat_tep(self._tep)
        except Exception:
            return {}
        if isinstance(du, dict) and isinstance(du.get("thiet_bi"), dict):
            return du["thiet_bi"]
        return {}

    def _ghi(self, thiet_bi: dict) -> None:
        self._thu_muc.mkdir(parents=True, exist_ok=True)
        try:
            import os
            os.chmod(self._thu_muc, 0o700)
        except OSError:
            pass
        kho_loi.ghi_bi_mat_tep(self._tep, {"thiet_bi": thiet_bi}, 0o600)

    @staticmethod
    def _cong_khai(ban_ghi: dict) -> dict:
        """Bản ghi CÔNG KHAI — bỏ khoá, chỉ giữ dấu vân (không lộ qua mọi đường đọc)."""
        ra = {k: v for k, v in ban_ghi.items() if k != "khoa_agent"}
        ra["dau_van"] = ban_ghi.get("dau_van", "")
        return ra

    # ── thao tác ──────────────────────────────────────────────────────────────
    def dang_ky(self, ten: str, nhan: str, duong_dan: list, *,
                co_quyen_ghi: bool = False, co_quyen_nguon: bool = False,
                co_quyen_ghi_man_hinh: bool = False) -> tuple[dict, str]:
        """Đăng ký máy MỚI. Trả (bản ghi công khai, khoá) — khoá chỉ trả đúng lần này.

        Ném ValueError với câu lỗi an toàn khi tên/khuôn sai hoặc đã tồn tại.
        """
        ten = str(ten or "").strip()
        if not MAU_TEN.fullmatch(ten):
            raise ValueError("ten_may chỉ gồm a-z 0-9 _ - (2–31 ký tự), ví dụ «may-van-phong-1»")
        ds_dd = [str(p).strip() for p in (duong_dan or []) if str(p).strip()]
        if not ds_dd:
            raise ValueError("phải khai ít nhất một thư mục trong «duong_dan» — "
                             "thiết bị không có thư mục nào bị từ chối (fail-closed)")
        khoa = secrets.token_hex(32)  # 256 bit
        with self._khoa:
            tat_ca = self._doc()
            if ten in tat_ca:
                raise ValueError(f"máy «{ten}» đã tồn tại — xoá trước rồi đăng ký lại, "
                                 "hoặc dùng «xoay_khoa»")
            ban_ghi = {
                "ten": ten,
                "nhan": str(nhan or ten)[:100],
                "duong_dan": ds_dd,
                "co_quyen_ghi": bool(co_quyen_ghi),
                "co_quyen_nguon": bool(co_quyen_nguon),
                "co_quyen_ghi_man_hinh": bool(co_quyen_ghi_man_hinh),
                "bat": True,
                "da_thu_hoi": False,
                "khoa_agent": khoa,
                "dau_van": dau_van(khoa),
                "tao_luc": _bay_gio_iso(),
                "sua_luc": _bay_gio_iso(),
            }
            tat_ca[ten] = ban_ghi
            self._ghi(tat_ca)
        return self._cong_khai(ban_ghi), khoa

    def lay(self, ten: str) -> dict | None:
        """Bản ghi ĐẦY ĐỦ (có khoá) — CHỈ dùng nội bộ để bắt tay/điều khiển phiên."""
        return self._doc().get(str(ten or "").strip())

    def lay_cong_khai(self, ten: str) -> dict | None:
        ban_ghi = self.lay(ten)
        return self._cong_khai(ban_ghi) if ban_ghi is not None else None

    def danh_sach(self) -> list[dict]:
        """Danh sách bản ghi CÔNG KHAI (không khoá)."""
        return [self._cong_khai(b) for b in self._doc().values() if isinstance(b, dict)]

    def xoay_khoa(self, ten: str) -> str | None:
        """Khoá MỚI cho máy đã có; trả khoá (một lần) hoặc None khi không thấy máy."""
        ten = str(ten or "").strip()
        khoa = secrets.token_hex(32)
        with self._khoa:
            tat_ca = self._doc()
            ban_ghi = tat_ca.get(ten)
            if not isinstance(ban_ghi, dict):
                return None
            ban_ghi = dict(ban_ghi)
            ban_ghi["khoa_agent"] = khoa
            ban_ghi["dau_van"] = dau_van(khoa)
            ban_ghi["da_thu_hoi"] = False
            ban_ghi["bat"] = True
            ban_ghi["sua_luc"] = _bay_gio_iso()
            tat_ca[ten] = ban_ghi
            self._ghi(tat_ca)
        return khoa

    def thu_hoi(self, ten: str) -> bool:
        """Thu hồi: khoá chết, máy không bắt tay lại được. Máy khác không ảnh hưởng."""
        ten = str(ten or "").strip()
        with self._khoa:
            tat_ca = self._doc()
            ban_ghi = tat_ca.get(ten)
            if not isinstance(ban_ghi, dict):
                return False
            ban_ghi = dict(ban_ghi)
            ban_ghi["da_thu_hoi"] = True
            ban_ghi["sua_luc"] = _bay_gio_iso()
            tat_ca[ten] = ban_ghi
            self._ghi(tat_ca)
        return True

    def doi_quyen(self, ten: str, **thay_doi) -> dict | None:
        """Đổi nhãn/thư mục/quyền/bật-tắt; giữ nguyên khoá. Trả bản ghi công khai."""
        ten = str(ten or "").strip()
        with self._khoa:
            tat_ca = self._doc()
            ban_ghi = tat_ca.get(ten)
            if not isinstance(ban_ghi, dict):
                return None
            ban_ghi = dict(ban_ghi)
            if "nhan" in thay_doi:
                ban_ghi["nhan"] = str(thay_doi["nhan"] or ten)[:100]
            if "duong_dan" in thay_doi:
                ds_dd = [str(p).strip() for p in (thay_doi["duong_dan"] or [])
                         if str(p).strip()]
                if not ds_dd:
                    raise ValueError("phải khai ít nhất một thư mục trong «duong_dan»")
                ban_ghi["duong_dan"] = ds_dd
            for k in ("co_quyen_ghi", "co_quyen_nguon", "co_quyen_ghi_man_hinh", "bat"):
                if k in thay_doi:
                    ban_ghi[k] = bool(thay_doi[k])
            ban_ghi["sua_luc"] = _bay_gio_iso()
            tat_ca[ten] = ban_ghi
            self._ghi(tat_ca)
        return self._cong_khai(ban_ghi)

    def xoa(self, ten: str) -> bool:
        """Xoá hẳn máy khỏi sổ (khoá cũng mất theo)."""
        ten = str(ten or "").strip()
        with self._khoa:
            tat_ca = self._doc()
            if ten not in tat_ca:
                return False
            tat_ca.pop(ten)
            self._ghi(tat_ca)
        return True
