"""Module youtube-phat — điều khiển máy phát YouTube/Zing qua dịch vụ phụ.

Module chỉ giữ cấu hình (địa chỉ dịch vụ + khoá Integration API) và hỏi sức
khoẻ dịch vụ `dich-vu/youtube-phat` (HTTP nội bộ/LAN). Bản thân module KHÔNG
phát nhạc và KHÔNG gọi ra youtube.com — dịch vụ mới là nơi gọi ra ngoài
(youtube.com/googlevideo.com/ytimg.com/zingmp3.vn).

`thuc_the()` mô tả một thực thể `cong_cu` (URL gốc để dán vào Home Assistant
+ khoá che dạng `…abcd`) và khai 7 hành động gọi qua dịch vụ (đợt C):
`tim`, `phat_len_loa`, `dung_phien`, `trang_thai`, `danh_sach_playlist`,
`nhap_ma_chia_se`, `lay_khoa`. Giao diện web của module
(`web/index.html`, khai `tab_web` trong manifest) gọi các hành động này qua
đường lõi `POST /api/thiet-bi/{id}/hanh-dong` — lõi đã có cơ chế, không sửa lõi.

Thiết kế bảo mật (khuôn modules/van-phong, NV138):
- Khoá dịch vụ (Bearer) chỉ thêm vào header ở đúng lời gọi mạng; một
  `logging.Filter` chặn mọi dòng log của thư viện HTTP chứa khoá — KHÔNG đổi
  mức log toàn cục.
- Địa chỉ dịch vụ chỉ được đặt qua cấu hình; lúc `cai` kiểm bằng đúng
  `app.tu_xa.kiem_url_lan` của lõi (chống SSRF; cho phép loopback khi test qua
  `WEB2API_TEST_CHO_PHEP_LOOPBACK`, và LAN nội bộ như home-assistant/vnc-xa).
  Không có địa chỉ cố định nào khác trong mã; KHÔNG viết bộ kiểm SSRF thứ hai.
- `suc_khoe()` lỗi → «chưa kết nối được: <lý do>», không ném; lý do không
  chứa khoá.

Chỉ dùng httpx (đã khai `yeu_cau_goi` trong manifest) + thư viện chuẩn.

Hợp đồng lõi (đọc từ app/kham_pha.py, app/so_module.py — không đoán):
- Lớp phải tên đúng `Module` (lõi `getattr(mod, "Module")`, sai tên → LoiNap).
- `nap` hợp đồng ĐỒNG BỘ: async def nap mà thật sự await thì lõi ném
  RuntimeError (app/so_module.py::_nap). Vì vậy nap là `def` thường.
- `cai`/`suc_khoe`/`dung` là `async def` (lõi await).
- `thuc_the` là `async def` (lõi `await m.thuc_the(None)` ở app/thiet_bi.py).
"""

from __future__ import annotations

import logging

import httpx

from app.tu_xa import kiem_url_lan

log = logging.getLogger(__name__)

#: Hết giờ hỏi sức khoẻ dịch vụ (giây).
HET_GIO_SUC_KHOE = 10.0

#: Địa chỉ dịch vụ mặc định (trùng manifest).
URL_MAC_DINH = "http://127.0.0.1:8713"

#: Khoá đang dùng — bộ lọc log chặn mọi dòng của thư viện HTTP chứa một trong các chuỗi này.
_KHOA_DANG_DUNG: set[str] = set()
_LOGGER_HTTP = ("httpx", "httpcore", "httpcore.connection", "httpcore.http11",
                "httpcore.http2", "httpcore.proxy")


class _ChanKhoaDichVu(logging.Filter):
    """Bỏ dòng log chứa khoá dịch vụ. Cài MỘT lần lên logger của thư viện HTTP —
    KHÔNG đổi mức log toàn cục (khuôn modules/zalo-bot, modules/van-phong).

    Chặn theo CẤU TRÚC, không theo giá trị khoá cụ thể: mọi dòng log mang mẫu
    «Authorization … Bearer» đều là dòng mang bí mật xác thực — chặn ngay cả khi
    khoá trong dòng chưa từng được nạp qua `nap`. Ngoài ra chặn mọi dòng chứa
    một khoá đã nạp (phòng khi khoá lọt vào URL hay thân thông báo khác)."""

    _youtube_phat_loc = True

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            noi_dung = record.getMessage()
        except Exception:
            return False
        thap = noi_dung.lower()
        if "authorization" in thap and "bearer" in thap:
            return False
        return not any(k in noi_dung for k in _KHOA_DANG_DUNG)


def _cai_bo_loc_log() -> None:
    for ten in _LOGGER_HTTP:
        lg = logging.getLogger(ten)
        for f in [f for f in lg.filters if getattr(f, "_youtube_phat_loc", False)]:
            lg.removeFilter(f)  # nạp lại module: bỏ bộ lọc của bản cũ
        lg.addFilter(_ChanKhoaDichVu())


_cai_bo_loc_log()


def _che_khoa(khoa: str) -> str:
    """Che khoá dạng …abcd (khoá ngắn hơn 4 ký tự → «…»)."""
    khoa = str(khoa or "")
    return f"…{khoa[-4:]}" if len(khoa) >= 4 else "…"


#: Hết giờ gọi dịch vụ cho một hành động (giây).
HET_GIO_HANH_DONG = 30.0

#: Tiền tố mã chia sẻ playlist (xem dich-vu/youtube-phat/playlists.py).
TIEN_TO_CHIA_SE = "TTPL1."


class LoiDichVu(Exception):
    """Lỗi gọi dịch vụ youtube-phat — thông điệp sạch, không chứa khoá."""


def _chuoi_sach(gia_tri, dai_toi_da=2048) -> str:
    return str(gia_tri or "").strip()[:dai_toi_da]


#: Lược đồ 7 hành động mở qua chuẩn C1 `thiet_bi` (đợt C). Lõi kiểm tham số theo
#: lược đồ này (SEC-004a); module tự áp giá trị mặc định cho trường vắng.
_TINH_NANG = [
    {
        "hanh_dong": "tim",
        "mo_ta": "Tìm bài hát trên YouTube hoặc Zing MP3",
        "tham_so": {
            "type": "object",
            "required": ["tu_khoa"],
            "additionalProperties": False,
            "properties": {
                "tu_khoa": {"type": "string", "minLength": 1, "maxLength": 120,
                            "description": "Từ khoá tìm kiếm"},
                "nguon": {"type": "string", "enum": ["youtube", "zing"],
                          "description": "Nguồn tìm (mặc định youtube)"},
                "gioi_han": {"type": "integer", "minimum": 1, "maximum": 20,
                             "description": "Số kết quả (mặc định 10)"},
            },
        },
    },
    {
        "hanh_dong": "phat_len_loa",
        "mo_ta": "Phát một bài lên một hay nhiều loa/tivi",
        "tham_so": {
            "type": "object",
            "required": ["nguon", "muc_tieu", "thiet_bi"],
            "additionalProperties": False,
            "properties": {
                "nguon": {"type": "string", "enum": ["youtube", "zing", "http"],
                          "description": "Nguồn của mục tiêu"},
                "muc_tieu": {"type": "string", "minLength": 1, "maxLength": 2048,
                             "description": "Mã video YouTube, link hoặc URL Zing"},
                "thiet_bi": {"type": "array", "minItems": 1, "maxItems": 32,
                             "items": {"type": "string", "minLength": 1, "maxLength": 64},
                             "description": "Danh sách entity_id loa/tivi nhận phát"},
                "am_luong": {"type": "integer", "minimum": 0, "maximum": 100,
                             "description": "Âm lượng 0–100 (tuỳ chọn)"},
            },
        },
    },
    {
        "hanh_dong": "dung_phien",
        "mo_ta": "Dừng hẳn phiên phát của dịch vụ (dừng-hẳn: không phát lại)",
        "tham_so": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "phien": {"type": "string", "minLength": 1, "maxLength": 64,
                          "description": "Mã phiên (bỏ trống: tự lấy phiên đang chạy)"},
            },
        },
    },
    {
        "hanh_dong": "trang_thai",
        "mo_ta": "Đọc trạng thái phát THẬT lúc hỏi của dịch vụ",
        "tham_so": {"type": "object", "additionalProperties": False, "properties": {}},
    },
    {
        "hanh_dong": "danh_sach_playlist",
        "mo_ta": "Liệt kê playlist trong kho chung của dịch vụ",
        "tham_so": {"type": "object", "additionalProperties": False, "properties": {}},
    },
    {
        "hanh_dong": "nhap_ma_chia_se",
        "mo_ta": "Nhập playlist từ mã chia sẻ TTPL1. vào kho",
        "tham_so": {
            "type": "object",
            "required": ["ma"],
            "additionalProperties": False,
            "properties": {
                "ma": {"type": "string", "minLength": 7, "maxLength": 400000,
                       "description": "Mã chia sẻ, phải bắt đầu bằng «TTPL1.»"},
                "ten": {"type": "string", "minLength": 1, "maxLength": 120,
                        "description": "Tên đặt cho playlist (tuỳ chọn)"},
            },
        },
    },
    {
        "hanh_dong": "lay_khoa",
        "mo_ta": ("Lấy khoá Integration API ĐẦY ĐỦ để dán vào Home Assistant — "
                  "chỉ gọi khi quản trị bấm «Chép token» có chủ đích"),
        "tham_so": {"type": "object", "additionalProperties": False, "properties": {}},
    },
]


class Module:
    """Điều khiển máy phát YouTube/Zing qua dịch vụ youtube-phat."""

    def __init__(self, manifest, _goc_kiem_thu: str | None = None):
        self.manifest = manifest
        self.tien_to = str(manifest.get("tien_to") or "youtube_phat")
        self._url = URL_MAC_DINH
        self._khoa = ""

    # ── vòng đời ──────────────────────────────────────────────────────────

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM (khởi động/cài xong). HỢP ĐỒNG LÕI: ĐỒNG BỘ —
        không gọi mạng, không await (xem app/so_module.py::_nap)."""
        cau_hinh = cau_hinh or {}
        self._url = str(cau_hinh.get("dich_vu_url") or URL_MAC_DINH).strip().rstrip("/")
        khoa = str(cau_hinh.get("khoa_dich_vu") or "")
        self._khoa = khoa
        if khoa:
            _KHOA_DANG_DUNG.add(khoa)

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm cấu hình trước khi lưu: địa chỉ qua đúng bộ kiểm chung
        `app.tu_xa.kiem_url_lan` (chống SSRF; cho phép loopback + LAN nội bộ).
        Khoá đầy đủ chỉ vào `cau_hinh_luu` (lõi lưu riêng 0600 theo kieu bi_mat),
        ra ngoài chỉ dạng che …abcd."""
        cau_hinh = cau_hinh or {}
        url = str(cau_hinh.get("dich_vu_url") or URL_MAC_DINH)
        try:
            url_sach = await kiem_url_lan(url)
        except ValueError as e:
            return {"ok": False, "loi": f"dia_chi_dich_vu_khong_hop_le: {e}",
                    "ket_qua_tung_khoa": []}
        khoa = str(cau_hinh.get("khoa_dich_vu") or "").strip()
        ket_qua_tung_khoa = [{"khoa": _che_khoa(khoa), "ok": True}] if khoa else []
        return {
            "ok": True,
            "loi": None,
            "ket_qua_tung_khoa": ket_qua_tung_khoa,
            "cau_hinh_luu": {
                "dich_vu_url": url_sach,
                "khoa_dich_vu": khoa,
            },
        }

    async def suc_khoe(self) -> dict:
        """Hỏi GET <url>/api/integration/health của dịch vụ. Lỗi → dict
        «chưa kết nối được: <lý do>», KHÔNG ném; lý do không chứa khoá."""
        url = f"{self._url}/api/integration/health"
        try:
            async with httpx.AsyncClient(timeout=HET_GIO_SUC_KHOE) as khach:
                r = await khach.get(url, headers={"Authorization": f"Bearer {self._khoa}"})
        except Exception as e:
            # Chỉ nêu TÊN loại lỗi: str(e) của vài ngoại lệ có thể lặp lại
            # header/request (nơi có khoá) — không để khoá lọt vào «loi».
            log.warning("khong_noi_duoc_dich_vu_youtube_phat: %s", type(e).__name__)
            return {"ok": False, "loi": f"chưa kết nối được: {type(e).__name__}",
                    "dich_vu_url": self._url}
        if r.status_code == 401:
            return {"ok": False, "loi": "chưa kết nối được: khoá bị từ chối (401)",
                    "dich_vu_url": self._url}
        if r.status_code != 200:
            return {"ok": False,
                    "loi": f"chưa kết nối được: dịch vụ trả HTTP {r.status_code}",
                    "dich_vu_url": self._url}
        try:
            than = r.json()
        except Exception:
            return {"ok": False, "loi": "chưa kết nối được: phản hồi không phải JSON",
                    "dich_vu_url": self._url}
        if not isinstance(than, dict) or than.get("success") is not True:
            return {"ok": False, "loi": "chưa kết nối được: phản hồi sức khoẻ lạ",
                    "dich_vu_url": self._url}
        return {"ok": True, "dich_vu_url": self._url,
                "trang_thai": than.get("status"), "khoa": _che_khoa(self._khoa)}

    async def dung(self) -> None:
        return None

    # ── thực thể: mô tả + 7 hành động qua dịch vụ ─────────────────────────

    async def thuc_the(self, loc=None) -> list[dict]:
        """Một thực thể cong_cu: URL gốc để dán vào Home Assistant + khoá che
        …abcd, kèm 7 hành động gọi qua dịch vụ (khoá Bearer chỉ đi ở phía
        server, không ra trình duyệt — trừ `lay_khoa` khi quản trị bấm
        «Chép token» có chủ đích)."""
        return [{
            "id": f"{self.tien_to}.dieu_khien",
            "ten": "Máy phát YouTube/Zing (qua dịch vụ youtube-phat)",
            "loai": "cong_cu",
            "thuoc_tinh": {
                "dich_vu": "youtube-phat",
                "dich_vu_url": self._url,
                "khoa": _che_khoa(self._khoa),
                "ghi_chu": ("URL gốc để dán vào tích hợp Home Assistant; "
                            "khoá đầy đủ bấm «Chép token» trên tab YouTube phát, "
                            "hoặc đọc tệp integration_token trong "
                            "/data/youtube_phat của dịch vụ."),
            },
            "mo_ta": ("Điều khiển máy phát YouTube/Zing qua dịch vụ "
                      "dich-vu/youtube-phat (HTTP nội bộ/LAN). Bản thân module "
                      "không phát nhạc; tìm/phát/dừng do dịch vụ đảm nhiệm, "
                      "tạm dừng/tiếp tục loa do tích hợp Home Assistant đảm nhiệm."),
            "tinh_nang": _TINH_NANG,
            "rui_ro_mac_dinh": "ghi",
        }]

    async def _goi_dich_vu(self, method: str, path: str,
                           params: dict | None = None,
                           json_body: dict | None = None):
        """Gọi API integration của dịch vụ. Trả (mã, thân dict/None).

        Ném LoiDichVu với thông điệp sạch (không khoá, không thân lạ) khi
        không nối được hoặc khoá bị từ chối."""
        if not self._khoa:
            raise LoiDichVu("chưa cấu hình «Khoá Integration API» của dịch vụ")
        url = f"{self._url}{path}"
        try:
            async with httpx.AsyncClient(timeout=HET_GIO_HANH_DONG) as khach:
                r = await khach.request(method, url, params=params,
                                        json=json_body,
                                        headers={"Authorization": f"Bearer {self._khoa}"})
        except Exception as e:
            log.warning("khong_noi_duoc_dich_vu_youtube_phat: %s", type(e).__name__)
            raise LoiDichVu(f"không nối được dịch vụ: {type(e).__name__}")
        if r.status_code == 401:
            raise LoiDichVu("dịch vụ từ chối khoá (401) — kiểm tra lại "
                            "«Khoá Integration API»")
        try:
            than = r.json()
        except Exception:
            than = None
        return r.status_code, (than if isinstance(than, dict) else None)

    @staticmethod
    def _loi_dich_vu(ma: int, than: dict | None, mac_dinh: str) -> str:
        if isinstance(than, dict):
            ma_loi = than.get("error")
            if isinstance(ma_loi, str) and ma_loi:
                return ma_loi
        return f"{mac_dinh} (HTTP {ma})"

    async def _hd_tim(self, tham_so: dict) -> dict:
        tu_khoa = _chuoi_sach(tham_so.get("tu_khoa"), 120)
        nguon = tham_so.get("nguon") or "youtube"
        try:
            gioi_han = int(tham_so.get("gioi_han") or 10)
        except (TypeError, ValueError):
            gioi_han = 10
        gioi_han = min(max(gioi_han, 1), 20)
        ma, than = await self._goi_dich_vu(
            "GET", "/api/integration/search",
            params={"q": tu_khoa, "source": nguon, "limit": gioi_han})
        if ma != 200 or not (than or {}).get("success"):
            return {"ok": False,
                    "loi": self._loi_dich_vu(ma, than, "tìm kiếm thất bại")}
        ket_qua = []
        for muc in (than.get("items") or []):
            if not isinstance(muc, dict):
                continue
            ket_qua.append({
                "ma_bai": str(muc.get("id") or ""),
                "tieu_de": str(muc.get("title") or muc.get("id") or ""),
                "kenh": str(muc.get("channel") or ""),
                "thoi_luong": muc.get("duration"),
                "nguon": str(muc.get("source") or nguon),
                "url": str(muc.get("url") or ""),
            })
        return {"ok": True, "ket_qua": ket_qua, "tong": len(ket_qua)}

    async def _hd_phat_len_loa(self, tham_so: dict) -> dict:
        thiet_bi = [t for t in (tham_so.get("thiet_bi") or []) if str(t).strip()]
        if not thiet_bi:
            return {"ok": False,
                    "loi": "chưa chọn loa/tivi nào — chọn ít nhất một thiết bị"}
        than_gui: dict = {
            "source": tham_so.get("nguon") or "youtube",
            "target": _chuoi_sach(tham_so.get("muc_tieu")),
            "output_entity_ids": thiet_bi,
        }
        am_luong = tham_so.get("am_luong")
        if am_luong is not None:
            try:
                than_gui["volume_level"] = min(max(int(am_luong), 0), 100) / 100
            except (TypeError, ValueError):
                pass
        ma, than = await self._goi_dich_vu(
            "POST", "/api/integration/session", json_body=than_gui)
        if ma != 200 or not (than or {}).get("success"):
            return {"ok": False,
                    "loi": self._loi_dich_vu(ma, than, "phát thất bại")}
        return {"ok": True, "phien": than.get("session")}

    async def _hd_dung_phien(self, tham_so: dict) -> dict:
        phien = _chuoi_sach(tham_so.get("phien"), 64)
        if not phien:
            # Không đưa mã phiên: tự lấy phiên đang chạy rồi dừng đúng nó
            # (dừng-hẳn: dừng đúng phiên, không đoán).
            ma, than = await self._goi_dich_vu("GET", "/api/integration/status")
            if ma == 200 and isinstance(than, dict):
                ss = than.get("session") or {}
                phien = str(ss.get("session_id") or "")
            if not phien:
                return {"ok": True, "da_dung": False,
                        "loi": "không có phiên nào đang chạy"}
        ma, than = await self._goi_dich_vu(
            "POST", "/api/integration/stop", json_body={"session_id": phien})
        if ma != 200 or not (than or {}).get("success"):
            return {"ok": False,
                    "loi": self._loi_dich_vu(ma, than, "dừng thất bại")}
        return {"ok": True, "da_dung": bool((than or {}).get("stopped", True)),
                "phien": (than or {}).get("session")}

    async def _hd_trang_thai(self) -> dict:
        ma, than = await self._goi_dich_vu("GET", "/api/integration/status")
        if ma != 200 or not (than or {}).get("success"):
            return {"ok": False,
                    "loi": self._loi_dich_vu(ma, than, "không đọc được trạng thái")}
        return {"ok": True, "trang_thai": than.get("state"),
                "phien": than.get("session"), "bai": than.get("item")}

    async def _hd_danh_sach_playlist(self) -> dict:
        ma, than = await self._goi_dich_vu("GET", "/api/integration/playlists")
        if ma != 200 or not (than or {}).get("success"):
            return {"ok": False,
                    "loi": self._loi_dich_vu(ma, than, "không đọc được playlist")}
        ds = []
        for pl in (than.get("playlists") or []):
            if not isinstance(pl, dict):
                continue
            ds.append({"id": str(pl.get("id") or ""),
                       "ten": str(pl.get("name") or pl.get("id") or ""),
                       "so_bai": len(pl.get("items") or [])})
        return {"ok": True, "danh_sach": ds}

    async def _hd_nhap_ma_chia_se(self, tham_so: dict) -> dict:
        ma_chia_se = _chuoi_sach(tham_so.get("ma"), 400000)
        if not ma_chia_se.startswith(TIEN_TO_CHIA_SE):
            return {"ok": False,
                    "loi": f"mã chia sẻ phải bắt đầu bằng «{TIEN_TO_CHIA_SE}»"}
        than_gui: dict = {"action": "import", "text": ma_chia_se}
        ten = _chuoi_sach(tham_so.get("ten"), 120)
        if ten:
            than_gui["name"] = ten
        ma, than = await self._goi_dich_vu(
            "POST", "/api/integration/playlists", json_body=than_gui)
        if ma != 200 or not (than or {}).get("success"):
            return {"ok": False,
                    "loi": self._loi_dich_vu(ma, than, "nhập mã chia sẻ thất bại")}
        return {"ok": True, "playlist": (than or {}).get("playlist")}

    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict | None) -> dict:
        """Điều phối 7 hành động đợt C qua dịch vụ. Lỗi dịch vụ → dict
        {"ok": False, "loi": <sạch>}, KHÔNG ném, KHÔNG lộ khoá."""
        tham_so = dict(tham_so or {})
        try:
            if hanh_dong == "tim":
                return await self._hd_tim(tham_so)
            if hanh_dong == "phat_len_loa":
                return await self._hd_phat_len_loa(tham_so)
            if hanh_dong == "dung_phien":
                return await self._hd_dung_phien(tham_so)
            if hanh_dong == "trang_thai":
                return await self._hd_trang_thai()
            if hanh_dong == "danh_sach_playlist":
                return await self._hd_danh_sach_playlist()
            if hanh_dong == "nhap_ma_chia_se":
                return await self._hd_nhap_ma_chia_se(tham_so)
            if hanh_dong == "lay_khoa":
                if not self._khoa:
                    return {"ok": False, "loi": "chưa cấu hình «Khoá Integration API»"}
                return {"ok": True, "khoa": self._khoa}
            return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}
        except LoiDichVu as e:
            return {"ok": False, "loi": str(e)}
