"""Module youtube (NV277) — máy phát YouTube/Zing chạy THẲNG trong web2api.

HOÀN TOÀN RIÊNG với modules/youtube-phat: module đó chỉ là MÁY KHÁCH
của dịch vụ phụ dich-vu/youtube-phat (nó gọi HTTP sang dịch vụ đó);
module này TỰ làm việc: tìm kiếm + giải luồng + rơ-le + phiên + danh
sách phát + lịch sử, phục vụ Home Assistant qua đường riêng của lõi
(app/duong_module.py: /m/youtube/api/...).

Hợp đồng với lõi (xem app/kham_pha.py):
- manifest khai "duong_module": {"lan_chi": true}; lõi kiểm khoá khách
  phạm vi «tich_hop@youtube» (do lõi tự sinh, NV289 — module KHÔNG có ô
  khoá nhập tay và không đọc bí mật nào) + nguồn LAN.
- tich_hop(duong, phuong_thuc, query, than, goc) -> (mã, headers, nội dung);
  `goc` là origin bên gọi đã dùng để tới lõi (URL luồng cho loa phải tuyệt đối).
- luong(ve, khoang, phuong_thuc) -> (mã, headers, bytes | iterator).
- nap(cau_hinh) đồng bộ; cai/suc_khoe/dung async; không có trường cấu hình
  nào nên module chạy ngay khi cài.

BẢO MẬT:
- Module không giữ bí mật nào; URL luồng dùng vé ký riêng của lõi (app/url_ky),
  không bao giờ nhúng khoá.
- Mọi URL do khách đưa (mục tiêu http) đều qua app.tu_xa.kiem_url_lan
  (chống SSRF, bộ kiểm CHUNG của kho — không viết bộ kiểm thứ hai).
  Chỉ cho ra mạng ngoài các nguồn đã khai trong manifest.tai_nguyen.
- Handler KHÔNG nuốt lỗi âm thầm (test nv45): except Exception nào cũng
  ghi một dòng log chỉ nêu kiểu lỗi, rồi trả mã lỗi rõ.

«DỪNG LÀ DỪNG HẲN» (giữ nguyên luật của dịch vụ cũ):
- Phiên chỉ ở RAM; dung()/stop xoá phiên + vé luồng, đặt cờ dừng; các
  iterator luồng đang chạy thấy cờ thì dừng ngay, KHÔNG mở thêm request
  lên nguồn, KHÔNG tự phát lại sau restart (restart = thể hiện mới, RAM
  trống).
- Rơ-le: nguồn đứt giữa chừng thì nối lại tối đa 5 lần (chờ 0,5/1/2/2/2
  giây) từ ĐÚNG byte đã gửi; 403/404/410 thì quên link cũ, giải lại rồi
  mới tính lần thử; kiểm Content-Range (phải cùng tổng cỡ, bắt đầu đúng
  offset — sai thì tính 1 lần thất bại, không gửi byte nào); hết lần thì
  NGẮT (không lấp rác, không chuyển bài — chuyển bài là việc của HA).
- Trần: tối đa 2 lần trích xuất/tìm kiếm chạy cùng lúc (semaphore, chờ
  tối đa 30 giây rồi 502 busy); bộ nhớ đệm vé luồng tối đa 256 mục.
- CHƯA CÓ / CHƯA KIỂM (ghi thẳng, không bịa): giải luồng Zing/Facebook
  chưa chuyển (trả 502 rõ); chưa thử với YouTube thật, loa thật, HA
  thật (môi trường làm việc không có Internet ra ngoài); yt-dlp/deno
  chưa có trong ảnh lõi — xem đề xuất Dockerfile/requirements trong
  báo cáo NV277 (chỉ ở nhánh, chủ kho duyệt mới dựng).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from app import duong_module as duong_loi
from app.tu_xa import kiem_url_lan

from .trich_xuat import (
    TrichXuat,
    TrichXuatUnavailable,
    _validated_query_and_limit,
    youtube_url_query,
    ZING_ID,
)

log = logging.getLogger("web2api.youtube")

#: Chờ nối lại (giây), tối đa 5 lần — giống dịch vụ cũ.
CHO_NOI_LAI = (0.5, 1, 2, 2, 2)
#: Kích thước một khúc thượng nguồn (byte) — 4 MiB như dịch vụ cũ.
KHUC = 4 * 1024 * 1024
#: Trần bộ nhớ đệm vé luồng (mục).
BO_NHO_DEM_TOI_DA = 256
#: Trần chờ chiếm chỗ trích xuất (giây).
CHO_TRICH_XUAT_GIAY = 30.0
#: Trần lịch sử giữ lại (mục).
LICH_SU_TOI_DA = 200

_MA_CHIA_SE = re.compile(r"TTPL1\.[A-Za-z0-9_-]+")


class LoiNguonKhongCuuDuoc(Exception):
    """Thượng nguồn đứt sau 5 lần nối lại — luồng ngắt, không lấp rác."""


def _loi(ma: int, code: str) -> tuple:
    return ma, {}, {"error": code, "success": False}


def _ok(noi_dung: dict, ma: int = 200) -> tuple:
    noi_dung = dict(noi_dung)
    noi_dung.setdefault("success", True)
    return ma, {}, noi_dung


def _parse_range(khoang, tong):
    """Trả (dau, cuoi) hoặc None khi Range hỏng (để gọi 416)."""
    if not khoang:
        return (0, None)
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", str(khoang).strip())
    if not m or (not m.group(1) and not m.group(2)):
        return None
    if m.group(2) and not m.group(1):
        # Dạng hậu tố bytes=-N: N byte cuối.
        n = int(m.group(2))
        if tong is None or n <= 0:
            return None
        return (max(0, tong - n), tong - 1)
    dau = int(m.group(1)) if m.group(1) else 0
    cuoi = int(m.group(2)) if m.group(2) else None
    if cuoi is not None and cuoi < dau:
        return None
    if tong is not None and dau >= tong:
        return None
    return (dau, cuoi)


def _parse_content_range(headers):
    """Trả (dau, cuoi, tong) từ Content-Range «bytes a-b/T», hoặc None."""
    if not headers:
        return None
    cr = headers.get("content-range") or headers.get("Content-Range")
    if not cr:
        return None
    m = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", str(cr).strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), (None if m.group(3) == "*" else int(m.group(3)))


class Module:
    """Module youtube: xem docstring đầu tệp."""

    def __init__(self, manifest=None, _thu_muc_du_lieu=None):
        self.manifest = manifest or {}
        self._goc_kiem_thu = _thu_muc_du_lieu
        self.trich_xuat = TrichXuat()
        self.mo_nguon = None  # test chích: async (url, headers) -> ban (xem luong)
        self._phien: dict[str, dict] = {}
        self._phien_hien_tai: str | None = None
        self._playlists: list[dict] = self._doc_tep("playlists.json", "playlists")
        self._lich_su: list[dict] = self._doc_tep("history.json", "history")[-LICH_SU_TOI_DA:]
        self._luong: dict[str, dict] = {}  # ve -> {url|bytes, het_han, ...}
        self._dung = False
        self._sem: asyncio.Semaphore | None = None
        self._khoa = asyncio.Lock()

    # ── vòng đời theo hợp đồng lõi ──────────────────────────────────────

    def nap(self, cau_hinh: dict):
        return None

    async def cai(self, cau_hinh: dict) -> dict:
        return {"ok": True, "loi": None, "ket_qua_tung_khoa": [], "cau_hinh_luu": {}}

    async def suc_khoe(self) -> dict:
        return {"ok": True, "phien": len(self._phien),
                "luong_dang_giu": len(self._luong)}

    # ── dữ liệu bền: danh sách phát + lịch sử (phiên/vé luồng KHÔNG bền) ───

    def _thu_muc(self) -> Path:
        """<WEB2API_DATA>/du_lieu/youtube (0700); tệp 0600. Không ghi vào ảnh."""
        if self._goc_kiem_thu is not None:
            goc = Path(self._goc_kiem_thu)
        else:
            from app.kho import thu_muc_du_lieu
            goc = thu_muc_du_lieu() / "du_lieu" / "youtube"
        return goc

    def _doc_tep(self, ten_tep: str, khoa: str) -> list[dict]:
        """Đọc {khoa: [...]} từ tệp bền; thiếu/hỏng → rỗng (ghi một dòng log, không vỡ)."""
        try:
            du_lieu = json.loads((self._thu_muc() / ten_tep).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as e:
            log.warning("Không đọc được %s: %s", ten_tep, type(e).__name__)
            return []
        ds = du_lieu.get(khoa) if isinstance(du_lieu, dict) else None
        return [x for x in ds if isinstance(x, dict)] if isinstance(ds, list) else []

    def _ghi_tep(self, ten_tep: str, khoa: str, ds: list[dict]) -> None:
        try:
            goc = self._thu_muc()
            goc.mkdir(parents=True, exist_ok=True, mode=0o700)
            tep = goc / ten_tep
            tam = tep.with_suffix(".tmp")
            fd = os.open(tam, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({khoa: ds}, f, ensure_ascii=False)
            tam.replace(tep)
        except OSError as e:
            log.warning("Không ghi được %s: %s", ten_tep, type(e).__name__)

    async def dung(self):
        """Dừng hẳn: xoá phiên + vé, đặt cờ để iterator đang chạy dừng."""
        self._dung = True
        self._phien.clear()
        self._phien_hien_tai = None
        self._luong.clear()

    # ── tiện ích nội bộ ─────────────────────────────────────────────────

    def _sem_trich_xuat(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(2)
        return self._sem

    async def _chiem_cho_trich_xuat(self):
        try:
            await asyncio.wait_for(self._sem_trich_xuat().acquire(),
                                   timeout=CHO_TRICH_XUAT_GIAY)
        except asyncio.TimeoutError as e:
            raise TrichXuatUnavailable("busy") from e
        return self._sem_trich_xuat()

    def _ghi_lich_su(self, muc: dict):
        self._lich_su.append(dict(muc, luc=time.time()))
        del self._lich_su[:max(0, len(self._lich_su) - LICH_SU_TOI_DA)]
        self._ghi_tep("history.json", "history", self._lich_su)

    def _anh_xa_phien(self, phien: dict) -> dict:
        return {
            "session_id": phien["id"],
            "source": phien.get("source"),
            "title": phien.get("title"),
            "duration": phien.get("duration"),
            "status": phien.get("status", "stopped"),
            "outputs": list(phien.get("outputs") or []),
            "position": phien.get("position", 0),
            "current_item": phien.get("muc"),
        }

    def _cat_bo_luong(self):
        if len(self._luong) <= BO_NHO_DEM_TOI_DA:
            return
        giu = sorted(self._luong.items(), key=lambda kv: kv[1].get("het_han", 0),
                     reverse=True)[:BO_NHO_DEM_TOI_DA]
        self._luong = dict(giu)

    def _dang_ky_luong(self, nguon: dict, goc: str = "") -> dict:
        ve = secrets.token_urlsafe(24)
        self._luong[ve] = dict(nguon, het_han=time.time() + 3600)
        self._cat_bo_luong()
        return {"stream_url": duong_loi.tao_duong_luong("youtube", ve, goc),
                "token": ve, "expires_in": 3600}

    def _xoa_luong_het_han(self):
        gio = time.time()
        for ve in [v for v, g in self._luong.items() if g.get("het_han", 0) <= gio]:
            del self._luong[ve]

    async def _xac_thuc_muc_tieu(self, source, target):
        """Trả (source, target_chuan, tieu_de, thoi_luong) hoặc raise ValueError."""
        target = str(target or "").strip()
        if not target:
            raise ValueError("invalid_target")
        if source == "youtube":
            kq = youtube_url_query(target)
            if kq and kq[0] == "video":
                return "youtube", kq[1], None, None
            if len(target) <= 11 and re.fullmatch(r"[A-Za-z0-9_-]{11}", target):
                return "youtube", target, None, None
            raise ValueError("invalid_target")
        if source == "zing":
            parsed = urlsplit(target)
            if parsed.scheme == "https" and (parsed.hostname == "zingmp3.vn"
                                            or (parsed.hostname or "").endswith(".zingmp3.vn")) \
                    and parsed.path.startswith("/bai-hat/"):
                return "zing", target, None, None
            if ZING_ID.fullmatch(target):
                return "zing", target, None, None
            raise ValueError("invalid_target")
        if source == "http":
            try:
                chuan = await kiem_url_lan(target)
            except ValueError as e:
                raise ValueError("invalid_target") from e
            return "http", chuan, None, None
        raise ValueError("invalid_source")

    # ── đường integration ───────────────────────────────────────────────

    async def tich_hop(self, duong, phuong_thuc, query, than, goc=""):
        """Dispatch /m/youtube/api/<duong>. Trả (mã, headers, nội dung)."""
        try:
            return await self._tich_hop(duong, phuong_thuc, query, than, goc)
        except TrichXuatUnavailable as e:
            log.warning("Trích xuất không khả dụng: %s", type(e).__name__)
            return _loi(502, str(e) or "extraction_unavailable")
        except Exception as e:  # không nuốt im: log kiểu lỗi, trả 502 chung
            log.warning("Lỗi integration youtube: %s", type(e).__name__)
            return _loi(502, "module_error")

    async def _tich_hop(self, duong, phuong_thuc, query, than, goc=""):
        query = query or {}
        than = than or {}
        if duong == "integration/health" and phuong_thuc == "GET":
            return _ok({"status": "ok", "api_version": "1",
                        "capabilities": ["search", "stream", "playlists", "history"],
                        "sources": ["youtube", "zing"],
                        "playback_sources": ["youtube", "zing", "http"]})
        if duong == "integration/search" and phuong_thuc == "GET":
            source = str(query.get("source") or "youtube")
            if source not in ("youtube", "zing", "facebook"):
                return _loi(400, "invalid_search_source")
            try:
                q, limit = _validated_query_and_limit(
                    str(query.get("q") or ""), query.get("limit", 20))
            except ValueError as e:
                return _loi(400, str(e) or "invalid_search_query")
            sem = await self._chiem_cho_trich_xuat()
            try:
                ket_qua = await self.trich_xuat.tim_kiem(source, q, limit)
            finally:
                sem.release()
            return _ok({"results": ket_qua, "source": source})
        if duong == "integration/status" and phuong_thuc == "GET":
            phien = self._phien.get(self._phien_hien_tai) if self._phien_hien_tai else None
            return _ok({
                "session": self._anh_xa_phien(phien) if phien else None,
                "sessions": [self._anh_xa_phien(p) for p in self._phien.values()],
                "history_count": len(self._lich_su),
                "playlists_count": len(self._playlists),
            })
        if duong == "integration/history" and phuong_thuc == "GET":
            return _ok({"history": list(reversed(self._lich_su))})
        if duong == "integration/stop" and phuong_thuc == "POST":
            sid = than.get("session_id")
            if sid is not None and sid not in self._phien:
                return _loi(404, "session_not_found")
            if sid is not None:
                self._phien[sid]["status"] = "stopped"
                if self._phien_hien_tai == sid:
                    self._phien_hien_tai = None
            else:
                for p in self._phien.values():
                    p["status"] = "stopped"
                self._phien_hien_tai = None
            self._luong.clear()  # dừng là dừng hẳn: vé luồng cũ vô hiệu
            return _ok({"stopped": True})
        if duong == "integration/session" and phuong_thuc == "POST":
            try:
                source, target, _, _ = await self._xac_thuc_muc_tieu(
                    than.get("source") or "youtube", than.get("target"))
            except ValueError as e:
                return _loi(400, str(e) or "invalid_target")
            sid = str(than.get("session_id") or secrets.token_urlsafe(12))
            phien = {
                "id": sid, "source": source, "target": target,
                "title": than.get("title"), "duration": than.get("duration"),
                "status": "stopped", "outputs": list(than.get("outputs") or []),
                "position": 0, "muc": {"source": source, "target": target},
            }
            self._phien[sid] = phien
            self._phien_hien_tai = sid
            return _ok({"session": self._anh_xa_phien(phien)}, 201)
        if duong == "integration/playlists" and phuong_thuc == "GET":
            return _ok({"playlists": self._playlists})
        if duong == "integration/playlists" and phuong_thuc == "POST":
            ket_qua = self._playlists_hanh_dong(than)
            if (than.get("action") or "list") not in ("list", "export"):
                self._ghi_tep("playlists.json", "playlists", self._playlists)
            return ket_qua
        if duong == "integration/session/outputs" and phuong_thuc == "POST":
            sid = than.get("session_id") or self._phien_hien_tai
            if sid not in self._phien:
                return _loi(404, "session_not_found")
            outputs = than.get("outputs")
            if not isinstance(outputs, list) or not all(isinstance(x, str) for x in outputs):
                return _loi(400, "invalid_outputs")
            self._phien[sid]["outputs"] = outputs
            return _ok({"session": self._anh_xa_phien(self._phien[sid])})
        if duong == "integration/stream" and phuong_thuc == "POST":
            return await self._tao_luong(than, goc)
        if duong == "integration/play" and phuong_thuc == "POST":
            return await self._phat(than)
        if duong in ("integration/next", "integration/prev") and phuong_thuc == "POST":
            phien = self._phien.get(self._phien_hien_tai) if self._phien_hien_tai else None
            if phien is None:
                return _loi(404, "session_not_found")
            phien["status"] = "playing"
            return _ok({"session": self._anh_xa_phien(phien)})
        if duong == "integration/seek" and phuong_thuc == "POST":
            phien = self._phien.get(self._phien_hien_tai) if self._phien_hien_tai else None
            if phien is None:
                return _loi(404, "session_not_found")
            try:
                vi_tri = float(than.get("position"))
            except (TypeError, ValueError):
                return _loi(400, "invalid_position")
            if vi_tri < 0:
                return _loi(400, "invalid_position")
            phien["position"] = vi_tri
            return _ok({"session": self._anh_xa_phien(phien)})
        return _loi(404, "not_found")

    def _playlists_hanh_dong(self, than):
        hanh_dong = than.get("action") or "list"
        if hanh_dong == "list":
            return _ok({"playlists": self._playlists})
        if hanh_dong == "export":
            return _ok({"playlists": self._playlists})
        if hanh_dong == "create":
            ten = str(than.get("name") or "").strip()
            if not ten:
                return _loi(400, "invalid_playlist_name")
            muc = {"id": secrets.token_urlsafe(8), "name": ten, "items": []}
            self._playlists.append(muc)
            return _ok({"playlist": muc}, 201)
        if hanh_dong == "import":
            ds = than.get("playlists")
            if not isinstance(ds, list):
                return _loi(400, "invalid_playlists")
            self._playlists = [p for p in ds if isinstance(p, dict)]
            return _ok({"playlists": self._playlists})
        for muc in self._playlists:
            if muc.get("id") != than.get("playlist_id"):
                continue
            if hanh_dong == "rename":
                ten = str(than.get("name") or "").strip()
                if not ten:
                    return _loi(400, "invalid_playlist_name")
                muc["name"] = ten
                return _ok({"playlist": muc})
            if hanh_dong == "delete":
                self._playlists.remove(muc)
                return _ok({"deleted": True})
            if hanh_dong == "add":
                item = than.get("item")
                if not isinstance(item, dict):
                    return _loi(400, "invalid_item")
                muc.setdefault("items", []).append(item)
                return _ok({"playlist": muc})
            if hanh_dong == "remove":
                muc["items"] = [i for i in muc.get("items", [])
                                if i.get("id") != than.get("item_id")]
                return _ok({"playlist": muc})
            if hanh_dong == "move":
                items = muc.get("items", [])
                try:
                    tu, den = int(than.get("from")), int(than.get("to"))
                    items.insert(den, items.pop(tu))
                except (TypeError, ValueError, IndexError):
                    return _loi(400, "invalid_move")
                return _ok({"playlist": muc})
        return _loi(404, "playlist_not_found")

    async def _tao_luong(self, than, goc=""):
        try:
            source, target, _, _ = await self._xac_thuc_muc_tieu(
                than.get("source") or "youtube", than.get("target"))
        except ValueError as e:
            return _loi(400, str(e) or "invalid_target")
        self._xoa_luong_het_han()
        if source == "http":
            muc = {"url": target, "source": "http", "total": None}
            return _ok(self._dang_ky_luong(muc, goc))
        sem = await self._chiem_cho_trich_xuat()
        try:
            giai = await self.trich_xuat.giai_am_thanh(source, target)
        finally:
            sem.release()
        muc = {"url": giai["url"], "source": source, "title": giai.get("title"),
               "total": None, "target": target,
               "video_id": target if source == "youtube" else None}
        return _ok(self._dang_ky_luong(muc, goc))

    async def _phat(self, than):
        sid_yeu_cau = than.get("session_id")
        if sid_yeu_cau is not None and sid_yeu_cau not in self._phien:
            return _loi(404, "session_not_found")
        phien = self._phien.get(sid_yeu_cau or self._phien_hien_tai) \
            if (sid_yeu_cau or self._phien_hien_tai) else None
        if phien is None:
            # play trực tiếp không qua session: tạo phiên rồi phát.
            try:
                source, target, _, _ = await self._xac_thuc_muc_tieu(
                    than.get("source") or "youtube", than.get("target"))
            except ValueError as e:
                return _loi(400, str(e) or "invalid_target")
            sid = secrets.token_urlsafe(12)
            phien = {"id": sid, "source": source, "target": target,
                     "title": than.get("title"), "duration": None,
                     "status": "playing", "outputs": list(than.get("outputs") or []),
                     "position": 0, "muc": {"source": source, "target": target}}
            self._phien[sid] = phien
            self._phien_hien_tai = sid
        else:
            phien["status"] = "playing"
            if than.get("outputs") is not None:
                phien["outputs"] = list(than.get("outputs") or [])
        self._ghi_lich_su({"source": phien["source"], "target": phien["target"],
                           "title": phien.get("title")})
        return _ok({"session": self._anh_xa_phien(phien)})

    # ── đường luồng ─────────────────────────────────────────────────────

    async def luong(self, ve, khoang, phuong_thuc):
        """Trả (mã, headers, bytes | iterator) cho vé luồng đã đăng ký."""
        self._xoa_luong_het_han()
        muc = self._luong.get(ve)
        if muc is None or self._dung:
            return 404, {}, b""
        if "bytes" in muc:
            du_lieu = muc["bytes"]
            tong = len(du_lieu)
            pham_vi = _parse_range(khoang, tong)
            if pham_vi is None:
                return 416, {"Content-Range": f"bytes */{tong}"}, b""
            dau, cuoi = pham_vi
            cuoi = tong - 1 if cuoi is None else min(cuoi, tong - 1)
            if dau > cuoi:
                return 416, {"Content-Range": f"bytes */{tong}"}, b""
            cat = du_lieu[dau:cuoi + 1]
            if phuong_thuc == "HEAD":
                return 200, {"Content-Length": str(len(cat)),
                             "Accept-Ranges": "bytes"}, b""
            headers = {"Content-Length": str(len(cat)), "Accept-Ranges": "bytes",
                       "Content-Type": "audio/mpeg"}
            if khoang:
                headers["Content-Range"] = f"bytes {dau}-{cuoi}/{tong}"
                return 206, headers, cat
            return 200, headers, cat
        url = muc["url"]
        tong = muc.get("total")
        pham_vi = _parse_range(khoang, tong)
        if pham_vi is None:
            return 416, {"Content-Range": f"bytes */{tong}" if tong else "bytes */*"}, b""
        dau, cuoi = pham_vi
        if cuoi is not None and tong is not None:
            cuoi = min(cuoi, tong - 1)

        async def _mo_khuc(tu, den):
            headers = {"Range": f"bytes={tu}-{den}"}
            if self.mo_nguon is not None:
                return await self.mo_nguon(url, headers)
            import httpx

            client = httpx.AsyncClient(follow_redirects=True, timeout=30.0)
            req = client.build_request("GET", url, headers=headers)
            resp = await client.send(req, stream=True)
            # Gắn client để đóng sau khi đọc xong (ban.httpx_client).
            resp.httpx_client = client  # type: ignore[attr-defined]
            return resp

        async def _dong(ban):
            try:
                await ban.aclose()
            except Exception as e:
                log.warning("Đóng luồng thượng nguồn lỗi: %s", type(e).__name__)
            client = getattr(ban, "httpx_client", None)
            if client is not None:
                try:
                    await client.aclose()
                except Exception as e:
                    log.warning("Đóng client thượng nguồn lỗi: %s", type(e).__name__)

        async def _giai_lai_link():
            # Quên link cũ, giải lại từ mục tiêu (chỉ youtube/http; zing
            # chưa hỗ trợ giải lại → giữ link cũ và để lần thử tính lỗi).
            if muc["source"] == "youtube":
                sem = await self._chiem_cho_trich_xuat()
                try:
                    giai = await self.trich_xuat.giai_am_thanh(
                        "youtube", muc.get("video_id") or muc.get("target") or "")
                    muc["url"] = giai["url"]
                finally:
                    sem.release()

        async def _kiem_dung():
            return self._dung

        iterator = self._luong_chiu_loi(_mo_khuc, _dong, _giai_lai_link,
                                        _kiem_dung, dau, cuoi, tong)
        headers = {"Accept-Ranges": "bytes", "Content-Type": "audio/mpeg"}
        if khoang:
            headers["Content-Range"] = f"bytes {dau}-{cuoi}/{tong}" if tong \
                else f"bytes {dau}-"
            return 206, headers, iterator
        if tong is not None:
            headers["Content-Length"] = str(tong - dau)
        return 200, headers, iterator

    async def _luong_chiu_loi(self, mo_khuc, dong, giai_lai_link, kiem_dung,
                              dau, cuoi, tong, ngu=None):
        """Yield bytes từ dau đến cuoi (None = tới hết), nối lại tối đa 5 lần.

        Giống ro_le.py của dịch vụ cũ: mỗi lần thất bại tính 1 lần, kiểm
        Content-Range trước khi gửi byte nào của khúc mới; hết lần thì
        raise LoiNguonKhongCuuDuoc (luồng ngắt); client ngắt
        (GeneratorExit) thì đóng và return im lặng, không request thêm.
        """
        _ngu = asyncio.sleep if ngu is None else ngu
        da_gui = dau
        tong_biet = tong
        dich = cuoi
        so_lan = 0
        ban = None
        try:
            while True:
                if await kiem_dung():
                    return
                if dich is not None and dich < da_gui:
                    return
                tu = da_gui
                den = tu + KHUC - 1 if dich is None else min(tu + KHUC - 1, dich)
                try:
                    ban = await mo_khuc(tu, den)
                except Exception as exc:
                    ban = None
                    if await kiem_dung():
                        return
                    so_lan += 1
                    if so_lan >= len(CHO_NOI_LAI):
                        raise LoiNguonKhongCuuDuoc(
                            f"thượng nguồn đứt ở offset {da_gui} sau {so_lan} lần "
                            f"nối lại (lỗi cuối: {type(exc).__name__})") from exc
                    await _ngu(CHO_NOI_LAI[so_lan - 1])
                    continue
                ma = getattr(ban, "status_code", 200)
                headers = getattr(ban, "headers", {}) or {}
                if ma == 416:
                    await dong(ban)
                    ban = None
                    return
                if ma in (403, 404, 410):
                    await dong(ban)
                    ban = None
                    # Quên link cũ, giải lại, rồi tính là 1 lần thất bại.
                    try:
                        await giai_lai_link()
                    except Exception as e:
                        log.warning("Giải lại link lỗi: %s", type(e).__name__)
                    so_lan += 1
                    if so_lan >= len(CHO_NOI_LAI):
                        raise LoiNguonKhongCuuDuoc(
                            f"thượng nguồn trả {ma} ở offset {da_gui} sau {so_lan} "
                            "lần nối lại") from None
                    await _ngu(CHO_NOI_LAI[so_lan - 1])
                    if await kiem_dung():
                        return
                    continue
                if ma not in (200, 206):
                    await dong(ban)
                    ban = None
                    so_lan += 1
                    if so_lan >= len(CHO_NOI_LAI):
                        raise LoiNguonKhongCuuDuoc(
                            f"thượng nguồn trả {ma} ở offset {da_gui} sau {so_lan} "
                            "lần nối lại") from None
                    await _ngu(CHO_NOI_LAI[so_lan - 1])
                    continue
                if ma == 200 and tu > 0:
                    # Thượng nguồn phớt lờ Range: không tự cắt byte (sợ sai
                    # nguồn đã đổi) → tính thất bại, giải lại rồi thử lại.
                    await dong(ban)
                    ban = None
                    try:
                        await giai_lai_link()
                    except Exception as e:
                        log.warning("Giải lại link lỗi: %s", type(e).__name__)
                    so_lan += 1
                    if so_lan >= len(CHO_NOI_LAI):
                        raise LoiNguonKhongCuuDuoc(
                            f"thượng nguồn phớt Range ở offset {da_gui}") from None
                    await _ngu(CHO_NOI_LAI[so_lan - 1])
                    continue
                cr = _parse_content_range(headers)
                if cr is not None:
                    cr_dau, _cr_cuoi, cr_tong = cr
                    if cr_dau != da_gui or (tong_biet is not None and cr_tong is not None
                                            and cr_tong != tong_biet):
                        await dong(ban)
                        ban = None
                        so_lan += 1
                        if so_lan >= len(CHO_NOI_LAI):
                            raise LoiNguonKhongCuuDuoc(
                                f"Content-Range lệch ở offset {da_gui}") from None
                        await _ngu(CHO_NOI_LAI[so_lan - 1])
                        continue
                    if cr_tong is not None:
                        tong_biet = cr_tong
                        if dich is None:
                            dich = tong_biet - 1
                try:
                    async for khuc in ban.aiter_bytes():
                        if await kiem_dung():
                            return
                        if khuc:
                            yield khuc
                            da_gui += len(khuc)
                    # Hết khúc mà chưa tới đích và tổng đã biết → đứt sớm.
                    if tong_biet is not None and da_gui < tong_biet:
                        raise ConnectionError("thượng nguồn ngắt sớm")
                except GeneratorExit:
                    return
                except Exception as exc:
                    if isinstance(exc, LoiNguonKhongCuuDuoc):
                        raise
                    await dong(ban)
                    ban = None
                    so_lan += 1
                    if so_lan >= len(CHO_NOI_LAI):
                        raise LoiNguonKhongCuuDuoc(
                            f"thượng nguồn đứt ở offset {da_gui} sau {so_lan} lần "
                            f"nối lại (lỗi cuối: {type(exc).__name__})") from exc
                    await _ngu(CHO_NOI_LAI[so_lan - 1])
                    if await kiem_dung():
                        return
                    continue
                await dong(ban)
                ban = None
                if dich is not None and da_gui > dich:
                    return
                if dich is None and tong_biet is not None and da_gui >= tong_biet:
                    return
                if dich is None and tong_biet is None:
                    return  # thượng nguồn tự hết (không biết tổng)
        finally:
            if ban is not None:
                await dong(ban)

