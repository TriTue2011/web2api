"""Module zalo-ca-nhan — kênh Zalo CÁ NHÂN: GỬI RA (chuẩn `kenh_ra`) và NHẬN TIN
vào (chuẩn `hoi_vong` của CHUAN-MODULE mục 12) qua dịch vụ phụ
dich-vu/zalo-ca-nhan (Node + zca-js, container riêng, HTTP loopback).

NGUỒN PORT (LUẬT PORT): luồng nghiệp vụ Zalo (đăng nhập QR, gửi tin, nhận tin,
trích nội dung) được chép từ c2a (commit d879b03) — nhưng c2a nói thẳng với
Zalo bằng zca-js từ máy chủ web2api, còn ở đây zca-js chạy trong container phụ
(dich-vu/zalo-ca-nhan/zalo-client.js) và module chỉ gọi HTTP nội bộ. Mọi thứ
bên Node đều chỉ chạy khi quản trị dựng container và bật `bat=1`.

CẢNH BÁO: API KHÔNG CHÍNH THỨC của Zalo — tài khoản có thể bị khoá bất cứ lúc
nào, chủ máy tự chịu. Chứng chỉ đăng nhập (cookie/imei/userAgent) do DỊCH VỤ
giữ (cred_*.json 0600 trong /data của container); module KHÔNG BAO GIỜ thấy
chúng, không log, không đưa vào suc_khoe.

Thiết kế bảo mật:
- Mặc định TẮT: chưa bật → không gọi mạng, gui_tin báo «chua_bat».
- Địa chỉ dịch vụ CHỈ loopback (kiểm khi cài + khi nạp — chống SSRF).
- Khoá Bearer dịch vụ kiểu `bi_mat`: bắt buộc khi bật (≥24 ký tự); bộ lọc log
  chặn mọi dòng httpx/httpcore chứa khoá (khuôn zalo-bot/van-phong).
- Danh sách cho phép nhận tin: mặc định RỖNG = không nhận ai; tin của người
  ngoài bị bỏ lặng lẽ, đếm `bo_qua_nguoi_la` (không trả lời, không lưu).
- Tin nhận là DỮ LIỆU KHÔNG TIN: kiểm khuôn (nguoi_gui khớp MAU_NGUOI_GUI),
  cắt ở TRAN_VAN_BAN, bỏ ký tự điều khiển (lam_sach_*), không đưa vào log.
- GỬI tin là GHI ra ngoài: lõi đi qua cổng xác nhận quản trị; module không tự
  quyết việc nhạy cảm.
- URL ngoài: chỉ URL của dịch vụ (cấu hình). Dịch vụ nói tới Zalo bằng zca-js
  — mạng_ra của module để trống có chủ ý; xem tai_nguyen.
"""

from __future__ import annotations

import contextlib
import json
import logging
import urllib.parse

import httpx

from app.kenh_vao import MAU_NGUOI_GUI, TRAN_VAN_BAN, lam_sach_ten, lam_sach_van_ban

log = logging.getLogger(__name__)

#: Mã lỗi ngắn trả cho lõi/người gọi — không lộ thân thô của dịch vụ.
MA_LOI = (
    "chua_bat", "chua_cau_hinh", "dich_sai", "noi_dung_sai", "sai_khoa",
    "chua_dang_nhap", "het_han_muc", "khong_noi_duoc_dich_vu",
    "may_chu_loi", "phan_hoi_sai", "mang_loi", "loi_khong_ngu",
)

#: Khoá Bearer dịch vụ đang dùng (chỉ thêm) — bộ lọc log chặn mọi dòng của
#: thư viện HTTP chứa khoá (khuôn _ChanKhoaDichVu của module van-phong).
_KHOA_DANG_DUNG: set[str] = set()
_LOGGER_HTTP = ("httpx", "httpcore", "httpcore.connection", "httpcore.http11",
                "httpcore.http2", "httpcore.proxy")


class _ChanKhoaDichVu(logging.Filter):
    """Bỏ dòng log chứa khoá Bearer dịch vụ. Cài MỘT lần lên logger của thư
    viện HTTP — KHÔNG đổi mức log toàn cục."""

    _zalo_ca_nhan_loc = True

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            noi_dung = record.getMessage()
        except Exception:
            return False
        return not any(k in noi_dung for k in _KHOA_DANG_DUNG)


def _cai_bo_loc_log() -> None:
    for ten in _LOGGER_HTTP:
        lg = logging.getLogger(ten)
        for f in [f for f in lg.filters if getattr(f, "_zalo_ca_nhan_loc", False)]:
            lg.removeFilter(f)  # nạp lại module: bỏ bộ lọc của bản cũ
        lg.addFilter(_ChanKhoaDichVu())


_cai_bo_loc_log()


def _la_bat(gia_tri) -> bool:
    return str(gia_tri).strip().lower() in ("1", "bat", "true", "on")


def _kiem_url_dich_vu(url: str) -> str:
    """Chuẩn hoá + kiểm loopback; sai → ValueError (chống SSRF dịch vụ nội bộ)."""
    s = (url or "").strip()
    try:
        u = urllib.parse.urlparse(s)
    except Exception:
        raise ValueError(f"dich_vu_url không phân giải được: {url!r}")
    if u.scheme not in ("http", "https"):
        raise ValueError("dich_vu_url chỉ nhận http/https")
    if not u.hostname:
        raise ValueError("dich_vu_url thiếu máy chủ")
    may = u.hostname.lower()
    if not (may in ("localhost", "::1") or may == "127.0.0.1" or
            (may.startswith("127.") and may[4:].replace(".", "").isdigit())):
        raise ValueError("dich_vu_url chỉ nhận loopback (127.x.x.x, ::1, localhost)")
    return s.rstrip("/")


def _kiem_danh_sach_cho_phep(gia_tri) -> list[str]:
    """Chuẩn hoá chuỗi JSON mảng id người gửi; sai khuôn → ValueError."""
    s = (gia_tri or "").strip() or "[]"
    try:
        ds = json.loads(s)
    except Exception:
        raise ValueError("danh_sach_cho_phep phải là chuỗi JSON (ví dụ [\"123\"]).")
    if not isinstance(ds, list) or not all(isinstance(x, str) and x.strip() for x in ds):
        raise ValueError("danh_sach_cho_phep phải là mảng chuỗi id không rỗng")
    da_thay = [x.strip() for x in ds]
    if len(set(da_thay)) != len(da_thay):
        raise ValueError("danh_sach_cho_phep có id trùng")
    return da_thay


class Module:
    """Kênh Zalo cá nhân: gửi ra qua POST /gui-tin, nhận vào qua GET /tin-moi
    của dịch vụ phụ dich-vu/zalo-ca-nhan (xác thực Bearer)."""

    def __init__(self, manifest, _goc_kiem_thu: str | None = None):
        self.manifest = manifest
        self._bat = False
        self._dich_vu_url = "http://127.0.0.1:8703"
        self._khoa = ""
        self._cho_phep: list[str] = []
        self._seq = 0
        self._dich: list[dict] = []
        self._bo_qua_nguoi_la = 0
        self._loi_cuoi: str | None = None

    # ── hợp đồng module ──

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình; lỗi → module tắt + warning (không ném)."""
        try:
            ch = cau_hinh or {}
            self._bat = _la_bat(ch.get("bat"))
            self._dich_vu_url = _kiem_url_dich_vu(ch.get("dich_vu_url", ""))
            self._cho_phep = _kiem_danh_sach_cho_phep(ch.get("danh_sach_cho_phep"))
            khoa = (ch.get("khoa_dich_vu") or "").strip()
            if self._bat and (not khoa or len(khoa) < 24):
                raise ValueError("bat=1 cần khoa_dich_vu ≥ 24 ký tự")
            self._khoa = khoa
            _KHOA_DANG_DUNG.add(khoa) if khoa else None
            self._seq = 0
            self._dich = []
            self._loi_cuoi = None
        except Exception as e:
            log.warning("zalo-ca-nhan: cấu hình sai (%s) — module tắt", type(e).__name__)
            self._bat = False
            self._khoa = ""
            self._dich = []
            self._cho_phep = []

    async def cai(self, cau_hinh: dict) -> dict:
        """Cài đặt: lỗi → {"ok": False, ...}; ok → cau_hinh_luu đã chuẩn hoá."""
        try:
            ch = cau_hinh or {}
            bat = _la_bat(ch.get("bat"))
            url = _kiem_url_dich_vu(ch.get("dich_vu_url", ""))
            cho_phep = _kiem_danh_sach_cho_phep(ch.get("danh_sach_cho_phep"))
            khoa = (ch.get("khoa_dich_vu") or "").strip()
            if bat and (not khoa or len(khoa) < 24):
                return {"ok": False, "ma": "sai_khoa",
                        "loi": "bat=1 cần khoa_dich_vu ≥ 24 ký tự (tự đặt, ví dụ openssl rand -hex 32)"}
        except Exception as e:
            return {"ok": False, "ma": "cau_hinh_sai",
                    "loi": f"cấu hình sai: {type(e).__name__}"}
        self._bat, self._dich_vu_url, self._khoa, self._cho_phep = bat, url, khoa, cho_phep
        _KHOA_DANG_DUNG.add(khoa) if khoa else None
        self._seq = 0
        # Kiểm live khi bật: ping /suc-khoe (khuôn telegram-bot — kiểm sống thật).
        if bat:
            sk = await self._goi("GET", "/suc-khoe")
            if sk.get("ma") == "mang_loi":
                return {"ok": False, "ma": "khong_noi_duoc_dich_vu",
                        "loi": f"không nối được dịch vụ ở {url}"}
        return {"ok": True, "cau_hinh_luu": {
            "bat": "1" if bat else "0",
            "dich_vu_url": url,
            "khoa_dich_vu": khoa,
            "danh_sach_cho_phep": json.dumps(cho_phep, ensure_ascii=False),
        }}

    def kenh_ra(self) -> list[dict]:
        """Danh sách đích gửi ra — KHÔNG chứa khoá/cookie. Lấy từ dịch vụ khi bật."""
        if not self._bat:
            return []
        try:
            r = httpx.get(f"{self._dich_vu_url}/hop-thoai",
                          headers={"Authorization": f"Bearer {self._khoa}"}, timeout=10.0)
            than = r.json()
        except Exception as e:
            self._loi_cuoi = "mang_loi"
            log.warning("zalo-ca-nhan: kenh_ra mang_loi (%s)", type(e).__name__)
            return []
        if r.status_code == 409:
            self._loi_cuoi = "chua_dang_nhap"
            return []
        if not (isinstance(than, dict) and than.get("ok") is True
                and isinstance(than.get("hop_thoai"), list)):
            self._loi_cuoi = "phan_hoi_sai"
            return []
        self._dich = []
        for h in than["hop_thoai"]:
            if not isinstance(h, dict):
                continue
            tid = str(h.get("thread_id") or "").strip()
            if not tid or len(tid) > 64:
                continue
            self._dich.append({"id": tid, "ten": str(h.get("ten") or tid),
                               "loai": 1 if h.get("loai") == 1 else 0})
        return [{"id": d["id"], "ten": d["ten"]} for d in self._dich]

    async def suc_khoe(self) -> dict:
        # KHÔNG lộ khoá/chứng chỉ/nội dung tin: chỉ mã ngắn + bộ đếm.
        dang_nhap = None
        che_do = None
        if self._bat and self._khoa:
            sk = await self._goi("GET", "/suc-khoe")
            if "ma" in sk and "ok" not in sk:
                self._loi_cuoi = sk["ma"]
            else:
                dang_nhap = bool(sk.get("dang_nhap"))
                che_do = str(sk.get("che_do") or "")
        return {"ok": True, "bat": self._bat, "dang_nhap": dang_nhap,
                "che_do": che_do, "so_dich": len(self._dich),
                "bo_qua_nguoi_la": self._bo_qua_nguoi_la, "loi_cuoi": self._loi_cuoi}

    async def dung(self) -> None:
        return None

    # ── gọi dịch vụ phụ ──

    async def _goi(self, phuong_thuc: str, duong: str, than: dict | None = None) -> dict:
        """Gọi dịch vụ phụ; trả {"ma": ...} khi lỗi mạng/phản hồi."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=30.0)) as client:
                r = await client.request(phuong_thuc, f"{self._dich_vu_url}{duong}",
                                         headers={"Authorization": f"Bearer {self._khoa}"},
                                         json=than)
        except httpx.HTTPError as e:
            log.warning("zalo-ca-nhan: mang_loi (%s)", type(e).__name__)
            return {"ma": "mang_loi"}
        except Exception as e:
            log.warning("zalo-ca-nhan: loi_khong_ngu (%s)", type(e).__name__)
            return {"ma": "loi_khong_ngu"}
        if r.status_code == 401:
            return {"ma": "sai_khoa"}
        if r.status_code == 409:
            try:
                return {"ma": str(r.json().get("ma", "chua_dang_nhap"))}
            except Exception:
                return {"ma": "chua_dang_nhap"}
        if r.status_code == 429:
            return {"ma": "het_han_muc"}
        if r.status_code >= 500:
            return {"ma": "may_chu_loi"}
        try:
            body = r.json()
        except Exception:
            return {"ma": "phan_hoi_sai"}
        if isinstance(body, dict) and body.get("ok") is True:
            return body
        ma = body.get("ma") if isinstance(body, dict) else None
        return {"ma": ma if ma in MA_LOI else "phan_hoi_sai"}

    # ── nhận tin qua hoi_vong (mục 12 CHUAN-MODULE) ──

    async def hoi_vong(self) -> list[dict]:
        """Hỏi dịch vụ /tin-moi, lọc theo danh sách cho phép → tin chuẩn.

        Một tin lỗi chỉ ghi log mã ngắn, không làm hỏng chu kỳ hỏi."""
        if not self._bat or not self._khoa:
            return []
        kq = await self._goi("GET", f"/tin-moi?tu={self._seq}")
        if "ma" in kq and "tu_moi" not in kq:
            self._loi_cuoi = kq["ma"]
            return []
        try:
            self._seq = max(self._seq, int(kq.get("tu_moi", self._seq)))
            tin_tho = kq.get("tin") or []
        except Exception:
            return []
        ra: list[dict] = []
        for t in tin_tho:
            try:
                tin = self._chuan_hoa_tin(t)
            except Exception as e:
                # Điểm nuốt lỗi có kiểm toán: bỏ qua lỗi theo hướng AN TOÀN.
                log.warning("zalo-ca-nhan: hoi_vong bo tin (%s)", type(e).__name__)
                continue
            if tin is not None:
                ra.append(tin)
        return ra

    def _chuan_hoa_tin(self, t: dict) -> dict | None:
        """Chuẩn hoá một tin dịch vụ → tin chuẩn của lõi. None = bỏ tin."""
        if not isinstance(t, dict):
            return None
        nguoi_gui = str(t.get("nguoi_gui") or "").strip()
        if not MAU_NGUOI_GUI.match(nguoi_gui):
            self._bo_qua_nguoi_la += 1
            return None
        if nguoi_gui not in self._cho_phep:
            # Danh sách rỗng mặc định → không nhận ai.
            self._bo_qua_nguoi_la += 1
            return None
        thread_id = str(t.get("thread_id") or "").strip()
        if not thread_id or len(thread_id) > 64:
            return None
        van_ban = lam_sach_van_ban(t.get("van_ban") or "").strip()[:TRAN_VAN_BAN]
        if not van_ban:
            return None
        ten_tu_khai = lam_sach_ten(t.get("ten_tu_khai"))
        return {
            "id_tin": f"{thread_id}:{t.get('seq')}",
            "nguoi_gui": nguoi_gui,
            "noi_gui": thread_id,
            "van_ban": van_ban,
            "ten_tu_khai": ten_tu_khai,
            "la_nhom": t.get("loai") == 1,
            "luc": t.get("luc"),
        }

    # ── gửi tin (chuẩn kenh_ra; là GHI ra ngoài → qua cổng xác nhận quản trị) ──

    async def gui_tin(self, dich: dict, noi_dung: dict) -> dict:
        """Gửi văn bản (và tệp qua `_tep`) tới đích đã biết."""
        if not self._bat:
            return {"ok": False, "ma": "chua_bat"}
        kenh_id = str((dich or {}).get("kenh_id") or (dich or {}).get("id") or "").strip()
        d = next((x for x in self._dich if x["id"] == kenh_id), None)
        if d is None:
            # Tự làm mới danh sách đích một lần rồi kiểm lại (đích thêm sau khi nạp).
            if self.kenh_ra():
                d = next((x for x in self._dich if x["id"] == kenh_id), None)
            if d is None:
                if self._loi_cuoi == "chua_dang_nhap":
                    return {"ok": False, "ma": "chua_dang_nhap"}
                return {"ok": False, "ma": "dich_sai"}
        van_ban = (noi_dung or {}).get("van_ban")
        teps = (noi_dung or {}).get("_tep")
        tep = teps[0] if isinstance(teps, list) and teps else None
        if not isinstance(van_ban, str):
            van_ban = ""
        if not van_ban.strip() and tep is None:
            return {"ok": False, "ma": "noi_dung_sai"}
        than = {"thread_id": d["id"], "loai": d.get("loai", 0),
                "van_ban": van_ban[:TRAN_VAN_BAN]}
        if isinstance(tep, dict):
            du_lieu = tep.get("du_lieu")
            if not isinstance(du_lieu, str) or not du_lieu.strip():
                return {"ok": False, "ma": "noi_dung_sai"}
            than["tep"] = {"ten": str(tep.get("ten") or "tep")[:100],
                           "kieu": str(tep.get("kieu") or "application/octet-stream"),
                           "du_lieu_base64": du_lieu}
        # KHÔNG retry: lõi đã thử lại theo chu kỳ của nó.
        try:
            r = await self._goi("POST", "/gui-tin", than)
        except Exception as e:
            log.warning("zalo-ca-nhan: đích %s loi_khong_ngu (%s)", d["id"], type(e).__name__)
            return {"ok": False, "ma": "loi_khong_ngu"}
        if "ma" in r and "ok" not in r:
            return {"ok": False, "ma": r["ma"]}
        return {"ok": True}
