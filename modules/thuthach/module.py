"""Module thuthach — nhận diện thử thách chống bot và báo «cần người».

NV175: port PHẦN TƯƠNG THÍCH của captcha-solver (c2a) — chỉ phần KHÔNG
vượt thử thách. Kiểm kê toàn bộ captcha-solver nằm trong nv175-bao-cao.md;
tóm tắt:

- NHÓM (B) — giải/vượt CAPTCHA — KHÔNG chép một dòng nào: solvers/*
  (recaptcha, turnstile, twocaptcha…), auto_login.py (gõ phím giả người
  để né phát hiện bot), workspaces.click_checkbox (tự tích ô reCAPTCHA),
  các tệp *_login.py / *_onboard.py. Quy tắc kho: không giải CAPTCHA.
  Thay thế đúng đắn đã có sẵn: người dùng tự làm trên noVNC (vnc-xa /
  ho-so-google) — module này chỉ NÊU bước đó.
- NHÓM (A) đã có ở nơi khác nên không chép lại: két bí mật vault.py ↔
  kho NV144/NV164 (AES-256-GCM, khoá tách khỏi dữ liệu); hồ sơ/phiên
  trình duyệt ↔ ho-so-google + vnc-xa (NV92/NV94/NV142/NV155c); đăng
  nhập tự động ↔ NV145 (đang chờ soát, không làm lại).
- Phần (A) còn thiếu và được làm ở đây: NHẬN DIỆN thử thách bằng cấu
  trúc (xem phat_hien.py) + điểm nối «chờ người xử lý» (kết quả luôn
  `can_nguoi` + hướng dẫn mở noVNC).

Thiết kế an toàn:
- THUẦN PHÂN TÍCH: không gọi mạng, không mở trình duyệt, không có khoá
  hay cấu hình bí mật nào. HTML/URL do NGƯỜI GỌI đưa vào là DỮ LIỆU
  KHÔNG TIN (trần kích thước ở phat_hien.py); URL chỉ dùng đường dẫn —
  query chứa token bị bỏ trước khi phân loại và không vào log/kết quả.
- Kết quả KHÔNG chứa dữ liệu giúp giải thử thách (không sitekey/token).
- Thực thể rủi ro `doc`: việc duy nhất module làm được là ĐỌC và PHÂN
  LOẠI; không có hành động ghi/điều khiển nào để lạm dụng.
- Mở mặc định theo chuẩn kho: module không tự chạy gì; trợ lý/người dùng
  gọi công cụ `kiem_tra` khi một luồng khác (trình duyệt, đăng nhập) báo
  trang lạ.
"""

from __future__ import annotations

import logging

try:  # nạp như gói modules.thuthach và nạp trực tiếp trong test
    from . import phat_hien
except ImportError:  # pragma: no cover
    import phat_hien  # type: ignore

log = logging.getLogger("web2api.module.thuthach")

_HUONG_DAN_MAC_DINH = (
    "Mở hồ sơ trình duyệt qua noVNC (module vnc-xa / Hệ thống → Trình duyệt).",
    "Tự hoàn thành thử thách ngay trên cửa sổ noVNC đó.",
    "Đóng hồ sơ bằng «Đóng · giữ phiên» để lần chạy sau dùng lại phiên đã qua thử thách.",
)


class Module:
    """Lớp Module theo hợp đồng app/kham_pha.py (khả năng thiet_bi, C2)."""

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to", "thuthach")
        self._kenh_no_vnc = ""

    # ── Vòng đời ─────────────────────────────────────────────────────────

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM — không gọi mạng, không có bí mật."""
        cau_hinh = cau_hinh or {}
        kenh = cau_hinh.get("kenh_no_vnc")
        self._kenh_no_vnc = kenh.strip() if isinstance(kenh, str) else ""

    async def cai(self, cau_hinh: dict) -> dict:
        """Thẩm định cấu hình: chỉ một trường hiển thị tuỳ chọn."""
        cau_hinh = cau_hinh or {}
        kenh = cau_hinh.get("kenh_no_vnc", "")
        if kenh is None:
            kenh = ""
        if not isinstance(kenh, str):
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": "Trường «kenh_no_vnc» phải là chuỗi.",
                    "cau_hinh_luu": {}}
        kenh = kenh.strip()
        if len(kenh) > 300:
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": "Trường «kenh_no_vnc» quá dài (tối đa 300 ký tự).",
                    "cau_hinh_luu": {}}
        self.nap({"kenh_no_vnc": kenh})
        return {"ok": True, "ket_qua_tung_khoa": [], "loi": None,
                "cau_hinh_luu": {"kenh_no_vnc": kenh}}

    async def suc_khoe(self) -> dict:
        """Không gọi mạng; không có bí mật để lộ."""
        return {"ok": True, "che_do": "chi_nhan_dien",
                "so_loai_ho_tro": len(phat_hien.LOAI_HO_TRO)}

    async def dung(self) -> None:
        pass

    # ── Chuẩn thiet_bi ───────────────────────────────────────────────────

    async def thuc_the(self, loc: dict | None = None) -> list:
        return [{
            "id": f"{self.tien_to}.thu_thach",
            "ten": "Nhận diện thử thách (CAPTCHA) — chỉ báo cần người",
            "loai": "cong_cu",
            "rui_ro_mac_dinh": "doc",
            "tinh_nang": [
                {
                    "hanh_dong": "kiem_tra",
                    "mo_ta": ("Phân loại thử thách chống bot trên một trang "
                              "(HTML + URL do người gọi đưa vào, KHÔNG tải "
                              "trang, KHÔNG giải): trả loại thử thách và "
                              "«cần người» kèm hướng dẫn xử lý trên noVNC."),
                    "tham_so": {
                        "type": "object",
                        "required": [],
                        "additionalProperties": False,
                        "properties": {
                            "html": {"type": "string"},
                            "url": {"type": "string"},
                        },
                    },
                },
                {
                    "hanh_dong": "loai_ho_tro",
                    "mo_ta": ("Liệt kê các loại thử thách module nhận diện "
                              "được và cách xử lý (luôn: người dùng tự làm "
                              "trên noVNC)."),
                    "tham_so": {"type": "object", "required": [],
                                "additionalProperties": False,
                                "properties": {}},
                },
            ],
        }]

    async def trang_thai(self, ids: list) -> dict:
        muc_tieu = f"{self.tien_to}.thu_thach"
        if not ids or muc_tieu in ids:
            return {muc_tieu: {"gia_tri": "san_sang", "don_vi": None,
                               "luc": None, "hop_le": True}}
        return {}

    async def thuc_hien(self, id: str, hanh_dong: str,
                        tham_so: dict | None) -> dict:
        if id != f"{self.tien_to}.thu_thach":
            return {"ok": False, "loi": f"Không tìm thấy thực thể {id!r}"}
        tham_so = tham_so or {}
        if hanh_dong == "kiem_tra":
            return self._kiem_tra(tham_so)
        if hanh_dong == "loai_ho_tro":
            return {"ok": True,
                    "ket_qua": {"loai_ho_tro": list(phat_hien.LOAI_HO_TRO)}}
        return {"ok": False,
                "loi": f"Hành động «{hanh_dong}» không hỗ trợ — module chỉ "
                       f"nhận diện thử thách và báo cần người, không giải."}

    # ── Nội bộ ───────────────────────────────────────────────────────────

    def _huong_dan(self) -> list:
        hd = list(_HUONG_DAN_MAC_DINH)
        if self._kenh_no_vnc:
            hd[0] = f"Mở hồ sơ trình duyệt qua noVNC: {self._kenh_no_vnc}"
        return hd

    def _kiem_tra(self, tham_so: dict) -> dict:
        html = tham_so.get("html")
        url = tham_so.get("url")
        if html is None and url is None:
            return {"ok": False,
                    "loi": "thieu_tham_so: cần ít nhất một trong hai "
                           "tham số «html» hoặc «url»."}
        try:
            kq = phat_hien.phan_loai(html, url)
        except phat_hien.LoiThuThach as e:
            # Chỉ mã ngắn — KHÔNG chép đầu vào (có thể chứa bí mật) vào lỗi.
            return {"ok": False, "loi": f"khong_phan_loai_duoc: {e.ma}"}
        if kq["co_thu_thach"]:
            # Log CHỈ mã loại — không URL, không HTML (tránh lộ query/token).
            log.info("thuthach: phát hiện %s (tin cậy %s)",
                     kq["loai"], kq["tin_cay"])
            kq = {**kq, "trang_thai": "can_nguoi",
                  "huong_dan": self._huong_dan()}
        else:
            kq = {**kq, "trang_thai": "khong_thu_thach", "huong_dan": []}
        return {"ok": True, "ket_qua": kq}
