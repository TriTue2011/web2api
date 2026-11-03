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
- NV301 mảnh lõi: phiên mang siêu dữ liệu đầy đủ (title/channel/
  thumbnail/duration/id/source/media_content_type) và hàng đợi thật
  (queue/index theo phiên và theo playlist_id, next/prev thật, 409 ở
  biên hàng đợi); status trả đủ hình dạng dịch vụ cũ (state/item/
  session/sessions/history_count); search trả cả `results` lẫn `items`
  và ghi nhớ hàng đợi tìm; seek nhận cả `giay` lẫn `position`, kẹp
  theo thời lượng. Lệch còn lại so với dịch vụ cũ (ghi rõ, không bịa):
  giải luồng Zing/Facebook chưa chuyển (trả 502 rõ); seek_url trả None
  (rơ-le vé không có sidx như dịch vụ cũ — loa tua bằng Range); stop
  body rỗng vẫn dừng hết (nới hơn cũ, giữ hành vi NV277); session_id
  bên gọi đưa không ép khuôn [a-z0-9_-]{1,40} của cũ (module tự sinh
  token_urlsafe có chữ hoa).
- NV301 mảnh playlist: import từ mã chia sẻ TTPL1.
  (base64url(zlib(JSON)) — đúng định dạng dịch vụ cũ, mở chéo được)
  và từ link playlist YouTube (/playlist hoặc link xem có list=, qua
  yt-dlp flat); export ra mã TTPL1.; create/add chuẩn hoá + khử trùng
  + trần 500 bài / 100 playlist; remove/move theo chỉ số kiểu cũ.
  Link album/playlist Zing → 502 playlist_unavailable (dịch vụ cũ lấy
  qua API web có khoá ký riêng, module không có khoá đó).
- CHƯA CÓ / CHƯA KIỂM (ghi thẳng, không bịa): giải luồng Zing/Facebook
  chưa chuyển (trả 502 rõ); chưa thử với YouTube thật, loa thật, HA
  thật (môi trường làm việc không có Internet ra ngoài). yt-dlp/deno
  đã nằm trong requirements.txt ở main 8ef3ca9 (đã duyệt) nên ghi chú
  cũ «chưa có trong ảnh lõi» ở đây đã bỏ.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import secrets
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from app import duong_module as duong_loi
from app.tu_xa import kiem_url_lan

from .trich_xuat import (
    PLAYLIST_ID,
    TrichXuat,
    TrichXuatUnavailable,
    YOUTUBE_URL_HOSTS,
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
#: Trần phiên cùng lúc (dịch vụ cũ: 32; vượt thì đuổi phiên cũ nhất).
PHIEN_TOI_DA = 32
#: Trần hàng đợi ghi nhớ từ lần tìm gần nhất (mỗi nguồn).
HANG_DOI_TIM_TOI_DA = 30
#: Trần playlist (dịch vụ cũ: playlists.py).
PLAYLIST_TOI_DA = 100
BAI_TOI_DA = 500
TEN_PLAYLIST_TOI_DA = 80
MA_CHIA_SE_TOI_DA = 300_000
JSON_CHIA_SE_TOI_DA = 2_000_000

_MA_CHIA_SE = re.compile(r"TTPL1\.[A-Za-z0-9_-]+")
#: Mã video Facebook là chuỗi số (dịch vụ cũ: 5–25 chữ số).
_FACEBOOK_ID = re.compile(r"[0-9]{5,25}")
#: Suy loại nội dung HTTP trực tiếp từ hậu tố (dịch vụ cũ: DIRECT_AUDIO_TYPES).
_KIEU_HTTP = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "ogg": "audio/ogg",
              "oga": "audio/ogg", "opus": "audio/opus", "flac": "audio/flac",
              "wav": "audio/wav"}


def _ten_playlist(ten) -> str:
    """Tên playlist chuẩn hoá (dịch vụ cũ: gộp khoảng trắng, tối đa 80)."""
    return " ".join(str(ten or "").split())[:TEN_PLAYLIST_TOI_DA]


def _ma_chia_se(playlist: dict) -> str:
    """Mã chia sẻ TTPL1. — port share_code của playlists.py (dịch vụ cũ).

    Định dạng: «TTPL1.» + base64url(zlib(JSON {n: tên, i: [[source, id,
    url, title, channel, duration, thumbnail], ...]})) — mã tạo ở đây
    mở được ở dịch vụ cũ và ngược lại."""
    compact = {
        "n": playlist["name"],
        "i": [[it["source"], it["id"], it["url"], it["title"],
               it["channel"], it["duration"], it["thumbnail"]]
              for it in playlist["items"]],
    }
    raw = zlib.compress(json.dumps(compact, ensure_ascii=False,
                                   separators=(",", ":")).encode(), 9)
    return "TTPL1." + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _doc_ma_chia_se(code: str):
    """(tên, các bài) từ mã chia sẻ — port read_share_code (dịch vụ cũ).

    Sai định dạng / quá trần / JSON không đúng hình → ValueError
    «invalid_share_code». Bài không chuẩn hoá được thì bỏ, không vỡ."""
    code = "".join(str(code or "").split())
    if not code.startswith("TTPL1.") or len(code) > MA_CHIA_SE_TOI_DA:
        raise ValueError("invalid_share_code")
    body = code[len("TTPL1."):]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        decompressor = zlib.decompressobj()
        text = decompressor.decompress(raw, JSON_CHIA_SE_TOI_DA)
        if decompressor.unconsumed_tail:
            raise ValueError("invalid_share_code")
        data = json.loads(text)
    except (ValueError, zlib.error) as e:
        raise ValueError("invalid_share_code") from e
    if not isinstance(data, dict) or not isinstance(data.get("i"), list):
        raise ValueError("invalid_share_code")
    keys = ("source", "id", "url", "title", "channel", "duration", "thumbnail")
    items = []
    for row in data["i"][:BAI_TOI_DA]:
        if not isinstance(row, list):
            continue
        muc = Module._chuan_hoa_muc(dict(zip(keys, row)))
        if muc:
            items.append(muc)
    return _ten_playlist(data.get("n")), items


def _id_playlist_youtube(text: str):
    """ID playlist trong link YouTube (/playlist hoặc link xem có list=).

    Port youtube_playlist_id của dịch vụ cũ (mọi link YouTube có tham số
    list= đều được, không riêng trang /playlist)."""
    raw = str(text or "").strip()
    if not raw or len(raw) > 2048 or any(c.isspace() for c in raw):
        return None
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme not in ("http", "https"):
        return None
    if (parsed.hostname or "").lower() not in YOUTUBE_URL_HOSTS:
        return None
    pid = parse_qs(parsed.query).get("list", [""])[0]
    return pid if pid and PLAYLIST_ID.fullmatch(pid) else None


def _la_link_playlist_zing(text: str) -> bool:
    """Link album/playlist Zing MP3 (/album/…/ID.html, /playlist/…/ID.html).

    Port zing_playlist_id của dịch vụ cũ (chỉ nhận dạng, không lấy dữ
    liệu — lấy dữ liệu cần khoá API web của Zing mà module không có)."""
    parsed = urlsplit(str(text or "").strip())
    host = (parsed.hostname or "").lower()
    return (parsed.scheme in ("http", "https")
            and (host == "zingmp3.vn" or host.endswith(".zingmp3.vn"))
            and parsed.path.startswith(("/album/", "/playlist/")))


class LoiNguonKhongCuuDuoc(Exception):
    """Thượng nguồn đứt sau 5 lần nối lại — luồng ngắt, không lấp rác."""


def _iso_utc() -> str:
    """Mốc giờ ISO UTC theo giây — HA đọc `session.updated_at` để nội suy vị trí."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
        # NV301: hàng đợi ghi nhớ từ lần tìm gần nhất (mỗi nguồn) — phát
        # một bài trong kết quả tìm thì bài kế/bài trước đi theo hàng đợi đó,
        # như remember_search của dịch vụ cũ.
        self._hang_doi_tim_kiem: dict[str, list[dict]] = {}
        # Bản sửa phiên (revision): tăng mỗi lần phiên đổi, để stop theo
        # expected_revision của dịch vụ cũ có cái mà đối chiếu.
        self._ban_sua = 0
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

    # ── thực thể cho trang Đời sống + tab riêng (NV301 mảnh giao diện) ───
    # Hai thực thể theo rủi ro (khuôn NV181 của youtube-phat): `xem`
    # (doc: đọc trạng thái/tìm/playlist) và `dieu_khien` (ghi: phát/dừng/
    # chuyển bài/tua/nhập playlist). Trang tab KHÔNG gọi thẳng đường
    # integration (đường đó cần khoá khách tich_hop@youtube, trình duyệt
    # không có) — mọi lệnh đi qua hành động thực thể của lõi, phiên quản trị.

    def _tien_to_thuc_the(self) -> str:
        return str((self.manifest or {}).get("tien_to") or "youtube")

    def _phien_trang_thai(self):
        """(gia_tri, phien) cho ô trạng thái: phiên hiện tại, nếu không còn
        thì phiên dừng gần nhất, nếu chưa từng phát thì chua_phat."""
        phien = None
        if self._phien_hien_tai:
            phien = self._phien.get(self._phien_hien_tai)
        if phien is None and self._phien:
            phien = next(reversed(list(self._phien.values())))
        if phien is None:
            return "chua_phat", None
        if phien.get("status") == "playing":
            return "dang_phat", phien
        return "dung", phien

    def _tinh_nang_xem(self) -> list[dict]:
        return [
            {"hanh_dong": "trang_thai",
             "mo_ta": "Đọc bài đang phát và trạng thái lúc hỏi",
             "tham_so": {"type": "object", "additionalProperties": False,
                         "properties": {}}},
            {"hanh_dong": "tim",
             "mo_ta": "Tìm bài hát trên YouTube, Zing MP3 (Facebook chỉ nhận link)",
             "tham_so": {
                 "type": "object", "required": ["tu_khoa"],
                 "additionalProperties": False,
                 "properties": {
                     "tu_khoa": {"type": "string", "minLength": 1,
                                 "maxLength": 2048,
                                 "description": "Từ khoá, hoặc link YouTube/playlist"},
                     "nguon": {"type": "string",
                               "enum": ["youtube", "zing", "facebook"],
                               "description": "Nguồn tìm (mặc định youtube)"},
                     "gioi_han": {"type": "integer", "minimum": 1, "maximum": 30,
                                  "description": "Số kết quả (mặc định 10)"}}}},
            {"hanh_dong": "danh_sach_playlist",
             "mo_ta": "Liệt kê playlist trong kho chung",
             "tham_so": {"type": "object", "additionalProperties": False,
                         "properties": {}}},
            {"hanh_dong": "cac_phien",
             "mo_ta": "Liệt kê các nhóm loa đang phát (mỗi nhóm một phiên)",
             "tham_so": {"type": "object", "additionalProperties": False,
                         "properties": {}}},
        ]

    def _tinh_nang_dieu_khien(self) -> list[dict]:
        phien = {"type": "string", "minLength": 1, "maxLength": 64,
                 "description": "Mã phiên (bỏ trống: dùng phiên đang chạy)"}
        return [
            {"hanh_dong": "phat",
             "mo_ta": "Phát một bài (hoặc tiếp tục phiên đang dừng)",
             "tham_so": {
                 "type": "object", "additionalProperties": False,
                 "properties": {
                     "muc_tieu": {"type": "string", "minLength": 1,
                                  "maxLength": 2048,
                                  "description": "Mã video, link bài hoặc URL trực tiếp"},
                     "nguon": {"type": "string",
                               "enum": ["youtube", "zing", "facebook", "http"],
                               "description": "Nguồn của mục tiêu (mặc định youtube)"},
                     "phien": phien,
                     "playlist_id": {"type": "string", "minLength": 1,
                                     "maxLength": 64,
                                     "description": "Phát theo playlist trong kho"},
                     "thiet_bi": {"type": "array", "minItems": 1, "maxItems": 32,
                                  "items": {"type": "string", "minLength": 1,
                                            "maxLength": 64},
                                  "description": "Loa/tivi nhận phát (entity_id)"},
                     "tieu_de": {"type": "string", "maxLength": 512,
                                 "description": "Tên bài gợi ý (tuỳ chọn)"},
                     "thoi_luong": {"type": "number", "minimum": 0,
                                    "description": "Thời lượng gợi ý, giây (tuỳ chọn)"}}}},
            {"hanh_dong": "dung",
             "mo_ta": "Dừng hẳn phiên phát (dừng là dừng hẳn, không phát lại)",
             "tham_so": {"type": "object", "additionalProperties": False,
                         "properties": {"phien": phien}}},
            {"hanh_dong": "tam_dung",
             "mo_ta": "Tạm dừng phiên phát, giữ nguyên bài (bấm Phát để tiếp tục)",
             "tham_so": {"type": "object", "additionalProperties": False,
                         "properties": {"phien": phien}}},
            {"hanh_dong": "bai_ke",
             "mo_ta": "Chuyển bài kế trong hàng đợi của phiên",
             "tham_so": {"type": "object", "additionalProperties": False,
                         "properties": {"phien": phien}}},
            {"hanh_dong": "bai_truoc",
             "mo_ta": "Chuyển bài trước trong hàng đợi của phiên",
             "tham_so": {"type": "object", "additionalProperties": False,
                         "properties": {"phien": phien}}},
            {"hanh_dong": "tua",
             "mo_ta": "Tua phiên phát tới vị trí giây",
             "tham_so": {
                 "type": "object", "required": ["giay"],
                 "additionalProperties": False,
                 "properties": {
                     "phien": phien,
                     "giay": {"type": "number", "minimum": 0,
                              "description": "Vị trí muốn tua tới (giây, >= 0)"}}}},
            {"hanh_dong": "doi_loa",
             "mo_ta": "Đổi loa/tivi của phiên đang phát (không phát lại từ đầu)",
             "tham_so": {
                 "type": "object", "required": ["thiet_bi"],
                 "additionalProperties": False,
                 "properties": {
                     "phien": phien,
                     "thiet_bi": {"type": "array", "minItems": 1, "maxItems": 32,
                                  "items": {"type": "string", "minLength": 1,
                                            "maxLength": 64},
                                  "description": "Loa/tivi mới nhận phát (entity_id)"}}}},
            {"hanh_dong": "nhap_playlist",
             "mo_ta": "Nhập playlist từ link playlist YouTube hoặc mã chia sẻ TTPL1.",
             "tham_so": {
                 "type": "object", "required": ["text"],
                 "additionalProperties": False,
                 "properties": {
                     "text": {"type": "string", "minLength": 1,
                              "maxLength": 400000,
                              "description": "Link playlist YouTube hoặc mã TTPL1."},
                     "ten": {"type": "string", "minLength": 1, "maxLength": 120,
                             "description": "Tên đặt cho playlist (tuỳ chọn)"}}}},
            {"hanh_dong": "phat_tu_hang_doi",
             "mo_ta": "Phát hàng đợi của phiên từ bài ở vị trí chọn (giữ nguyên hàng đợi)",
             "tham_so": {
                 "type": "object", "required": ["chi_so"],
                 "additionalProperties": False,
                 "properties": {
                     "phien": phien,
                     "chi_so": {"type": "integer", "minimum": 0,
                                "description": "Vị trí bài trong hàng đợi"}}}},
            {"hanh_dong": "tao_playlist",
             "mo_ta": "Tạo playlist mới trong kho",
             "tham_so": {
                 "type": "object", "required": ["ten"],
                 "additionalProperties": False,
                 "properties": {
                     "ten": {"type": "string", "minLength": 1, "maxLength": 120,
                             "description": "Tên playlist mới"}}}},
            {"hanh_dong": "xoa_playlist",
             "mo_ta": "Xoá một playlist khỏi kho",
             "tham_so": {
                 "type": "object", "required": ["playlist_id"],
                 "additionalProperties": False,
                 "properties": {
                     "playlist_id": {"type": "string", "minLength": 1,
                                     "maxLength": 64,
                                     "description": "Mã playlist trong kho"}}}},
            {"hanh_dong": "xoa_khoi_hang_doi",
             "mo_ta": "Xoá một bài khỏi hàng đợi của phiên đang phát",
             "tham_so": {
                 "type": "object", "required": ["chi_so"],
                 "additionalProperties": False,
                 "properties": {
                     "phien": phien,
                     "chi_so": {"type": "integer", "minimum": 0,
                                "description": "Vị trí bài trong hàng đợi"}}}},
            {"hanh_dong": "phat_playlist",
             "mo_ta": "Phát một playlist trong kho, từ bài ở vị trí chọn",
             "tham_so": {
                 "type": "object", "required": ["playlist_id"],
                 "additionalProperties": False,
                 "properties": {
                     "playlist_id": {"type": "string", "minLength": 1,
                                     "maxLength": 64,
                                     "description": "Mã playlist trong kho"},
                     "chi_so": {"type": "integer", "minimum": 0,
                                "description": "Vị trí bài bắt đầu (mặc định 0)"}}}},
            {"hanh_dong": "them_vao_playlist",
             "mo_ta": "Thêm một bài vào playlist trong kho",
             "tham_so": {
                 "type": "object", "required": ["playlist_id", "muc_tieu"],
                 "additionalProperties": False,
                 "properties": {
                     "playlist_id": {"type": "string", "minLength": 1,
                                     "maxLength": 64,
                                     "description": "Mã playlist trong kho"},
                     "muc_tieu": {"type": "string", "minLength": 1,
                                  "maxLength": 2048,
                                  "description": "Mã video, link bài hoặc URL trực tiếp"},
                     "nguon": {"type": "string",
                               "enum": ["youtube", "zing", "facebook", "http"],
                               "description": "Nguồn của bài (mặc định youtube)"},
                     "tieu_de": {"type": "string", "maxLength": 512,
                                 "description": "Tên bài gợi ý (tuỳ chọn)"}}}},
            {"hanh_dong": "them_vao_hang_doi",
             "mo_ta": "Thêm một bài vào cuối hàng đợi của phiên đang phát",
             "tham_so": {
                 "type": "object", "required": ["muc_tieu"],
                 "additionalProperties": False,
                 "properties": {
                     "phien": phien,
                     "muc_tieu": {"type": "string", "minLength": 1,
                                  "maxLength": 2048,
                                  "description": "Mã video, link bài hoặc URL trực tiếp"},
                     "nguon": {"type": "string",
                               "enum": ["youtube", "zing", "facebook", "http"],
                               "description": "Nguồn của bài (mặc định youtube)"},
                     "tieu_de": {"type": "string", "maxLength": 512,
                                 "description": "Tên bài gợi ý (tuỳ chọn)"}}}},
        ]

    async def thuc_the(self, loc=None) -> list[dict]:
        """Hai thực thể: xem (doc) + điều khiển (ghi).

        Không mang khoá, URL luồng hay thông tin kết nối nào: trạng thái
        phát là dữ liệu hiển thị, không phải bí mật."""
        tien_to = self._tien_to_thuc_the()
        return [
            {"id": f"{tien_to}.xem",
             "ten": "YouTube — trạng thái phát",
             "loai": "cong_cu",
             "thuoc_tinh": {"nguon_phat": "YouTube, Zing MP3, HTTP trực tiếp"},
             "mo_ta": ("Trạng thái phát của máy phát YouTube/Zing chạy thẳng "
                        "trong web2api: bài đang phát, đã dừng hay chưa phát. "
                        "Điều khiển đầy đủ ở tab YouTube."),
             "tinh_nang": self._tinh_nang_xem(),
             "rui_ro_mac_dinh": "doc"},
            {"id": f"{tien_to}.dieu_khien",
             "ten": "YouTube — điều khiển phát",
             "loai": "cong_cu",
             "thuoc_tinh": {"nguon_phat": "YouTube, Zing MP3, HTTP trực tiếp"},
             "mo_ta": ("Phát, dừng, tạm dừng, chuyển bài, tua và quản lý "
                        "playlist/hàng đợi của máy phát YouTube/Zing."),
             "tinh_nang": self._tinh_nang_dieu_khien(),
             "rui_ro_mac_dinh": "ghi"},
        ]

    async def trang_thai(self, ids) -> dict:
        """Trạng thái THẬT lúc hỏi (hợp đồng C1 — lõi `await m.trang_thai(ids)`):
        chỉ đọc RAM, không gọi mạng, không bao giờ ném."""
        luc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        gia_tri, _ = self._phien_trang_thai()
        return {str(i): {"gia_tri": gia_tri, "luc": luc} for i in (ids or [])}

    def _loi_hanh_dong(self, ma: int, nd: dict) -> dict:
        """(mã, nội dung) của tich_hop → dict lỗi cho tab (tiếng thường)."""
        code = (nd or {}).get("error") or "module_error"
        chu = {
            "session_not_found": "Không có phiên nào đang chạy.",
            "queue_end": "Đã hết hàng đợi — không còn bài kế.",
            "queue_start": "Đang ở đầu hàng đợi — không còn bài trước.",
            "playlist_not_found": "Không tìm thấy playlist trong kho.",
            "playlist_empty": "Playlist nhập về không có bài nào.",
            "playlist_unavailable": "Không lấy được playlist lúc này (nguồn lỗi hoặc chưa hỗ trợ).",
            "invalid_playlist_link": "Link chưa đúng: cần link playlist YouTube hoặc mã chia sẻ TTPL1.",
            "invalid_share_code": "Mã chia sẻ không đúng định dạng.",
            "invalid_target": "Mục tiêu chưa đúng (mã video, link bài hoặc URL trực tiếp).",
            "invalid_queue_item": "Bài thêm vào hàng đợi chưa đúng định dạng.",
            "invalid_item": "Bài thêm vào playlist chưa đúng định dạng.",
            "invalid_volume_level": "Âm lượng phải từ 0 tới 1.",
        }.get(code, f"Không thực hiện được ({code}).")
        return {"ok": False, "loi": chu, "ma_loi": code}

    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict | None) -> dict:
        """Điều phối hành động thực thể qua tich_hop nội bộ (không qua HTTP).
        Lỗi → dict {"ok": False, "loi": ...}, KHÔNG ném."""
        tham_so = dict(tham_so or {})
        tien_to = self._tien_to_thuc_the()
        xem_id, dk_id = f"{tien_to}.xem", f"{tien_to}.dieu_khien"
        xem_hd = {t["hanh_dong"] for t in self._tinh_nang_xem()}
        dk_hd = {t["hanh_dong"] for t in self._tinh_nang_dieu_khien()}
        try:
            if id == xem_id and hanh_dong in xem_hd:
                if hanh_dong == "trang_thai":
                    gia_tri, phien = self._phien_trang_thai()
                    return {"ok": True, "trang_thai": gia_tri,
                            "phien": self._anh_xa_phien(phien) if phien else None}
                if hanh_dong == "tim":
                    ma, _, nd = await self.tich_hop(
                        "integration/search", "GET",
                        {"q": tham_so.get("tu_khoa", ""),
                         "source": tham_so.get("nguon") or "youtube",
                         "limit": tham_so.get("gioi_han") or 10}, None)
                    if ma != 200:
                        return self._loi_hanh_dong(ma, nd)
                    return {"ok": True, "ket_qua": nd.get("results") or [],
                            "tong": nd.get("total", 0)}
                if hanh_dong == "danh_sach_playlist":
                    return {"ok": True, "danh_sach": self._playlists}
                if hanh_dong == "cac_phien":
                    song = [p for p in self._phien.values()
                            if p.get("status") != "stopped"]
                    song.sort(key=lambda p: p.get("revision", 0), reverse=True)
                    return {"ok": True,
                            "cac_phien": [self._anh_xa_phien(p) for p in song]}
            if id == dk_id and hanh_dong in dk_hd:
                if hanh_dong == "phat":
                    than = {}
                    if tham_so.get("phien"):
                        than["session_id"] = tham_so["phien"]
                    if tham_so.get("muc_tieu"):
                        than["target"] = tham_so["muc_tieu"]
                    if tham_so.get("nguon"):
                        than["source"] = tham_so["nguon"]
                    if tham_so.get("playlist_id"):
                        than["playlist_id"] = tham_so["playlist_id"]
                    if tham_so.get("thiet_bi"):
                        than["outputs"] = tham_so["thiet_bi"]
                    if tham_so.get("tieu_de"):
                        than["title"] = tham_so["tieu_de"]
                    if tham_so.get("thoi_luong") is not None:
                        than["duration"] = tham_so["thoi_luong"]
                    ma, _, nd = await self.tich_hop(
                        "integration/play", "POST", {}, than)
                    if ma != 200:
                        return self._loi_hanh_dong(ma, nd)
                    return {"ok": True, "phien": nd.get("session"),
                            "bai": nd.get("item")}
                if hanh_dong in ("dung", "tam_dung", "bai_ke", "bai_truoc"):
                    duong = {"dung": "integration/stop",
                             "tam_dung": "integration/pause",
                             "bai_ke": "integration/next",
                             "bai_truoc": "integration/prev"}[hanh_dong]
                    than = {}
                    if tham_so.get("phien"):
                        than["session_id"] = tham_so["phien"]
                    ma, _, nd = await self.tich_hop(duong, "POST", {}, than)
                    if ma != 200:
                        return self._loi_hanh_dong(ma, nd)
                    if hanh_dong == "dung":
                        return {"ok": True, "da_dung": bool(nd.get("stopped", True)),
                                "phien": nd.get("session")}
                    return {"ok": True, "phien": nd.get("session"),
                            "bai": nd.get("item")}
                if hanh_dong == "tua":
                    than = {"giay": tham_so.get("giay")}
                    if tham_so.get("phien"):
                        than["session_id"] = tham_so["phien"]
                    ma, _, nd = await self.tich_hop(
                        "integration/seek", "POST", {}, than)
                    if ma != 200:
                        return self._loi_hanh_dong(ma, nd)
                    return {"ok": True, "phien": nd.get("session"),
                            "vi_tri": nd.get("position")}
                if hanh_dong == "doi_loa":
                    than = {"output_entity_ids": tham_so.get("thiet_bi") or []}
                    if tham_so.get("phien"):
                        than["session_id"] = tham_so["phien"]
                    ma, _, nd = await self.tich_hop(
                        "integration/session/outputs", "POST", {}, than)
                    if ma != 200:
                        return self._loi_hanh_dong(ma, nd)
                    return {"ok": True, "phien": nd.get("session"),
                            "da_dung": bool(nd.get("stopped", False))}
                if hanh_dong == "nhap_playlist":
                    ma, _, nd = await self._playlists_hanh_dong(
                        {"action": "import", "text": tham_so.get("text", ""),
                         "name": tham_so.get("ten") or ""})
                    if ma != 200:
                        return self._loi_hanh_dong(ma, nd)
                    self._ghi_tep("playlists.json", "playlists", self._playlists)
                    return {"ok": True, "playlist": nd.get("playlist")}
                if hanh_dong == "phat_tu_hang_doi":
                    sid = tham_so.get("phien") or self._phien_hien_tai
                    phien = self._phien_song(sid)
                    if phien is None:
                        return {"ok": False, "loi": "Không có phiên nào đang chạy.",
                                "ma_loi": "session_not_found"}
                    hang = phien.get("queue") or {"index": 0, "items": []}
                    items = hang.get("items") or []
                    try:
                        chi_so = int(tham_so.get("chi_so"))
                    except (TypeError, ValueError):
                        return {"ok": False,
                                "loi": "Vị trí bài trong hàng đợi chưa đúng.",
                                "ma_loi": "invalid_queue_index"}
                    if not 0 <= chi_so < len(items):
                        return {"ok": False,
                                "loi": "Hàng đợi không có bài ở vị trí đó.",
                                "ma_loi": "invalid_queue_index"}
                    bai = items[chi_so]
                    try:
                        phien_moi = await self._tao_phien(
                            {"source": bai.get("source") or "youtube",
                             "target": bai.get("id"),
                             "queue": [dict(m) for m in items],
                             "queue_index": chi_so,
                             "outputs": list(phien.get("outputs") or [])})
                    except ValueError as e:
                        return self._loi_hanh_dong(400, {"error": str(e)})
                    self._ghi_lich_su({"source": phien_moi["source"],
                                       "target": phien_moi["target"],
                                       "title": phien_moi.get("title")})
                    anh_xa = self._anh_xa_phien(phien_moi)
                    return {"ok": True, "phien": anh_xa, "bai": anh_xa["item"]}
                if hanh_dong == "tao_playlist":
                    ma, _, nd = await self._playlists_hanh_dong(
                        {"action": "create", "name": tham_so.get("ten") or ""})
                    if ma != 200:
                        return self._loi_hanh_dong(ma, nd)
                    self._ghi_tep("playlists.json", "playlists", self._playlists)
                    return {"ok": True, "playlist": nd.get("playlist")}
                if hanh_dong == "xoa_playlist":
                    ma, _, nd = await self._playlists_hanh_dong(
                        {"action": "delete",
                         "playlist_id": tham_so.get("playlist_id")})
                    if ma != 200:
                        return self._loi_hanh_dong(ma, nd)
                    self._ghi_tep("playlists.json", "playlists", self._playlists)
                    return {"ok": True, "da_xoa": True}
                if hanh_dong == "xoa_khoi_hang_doi":
                    than = {"action": "remove", "index": tham_so.get("chi_so")}
                    if tham_so.get("phien"):
                        than["session_id"] = tham_so["phien"]
                    ma, _, nd = await self.tich_hop(
                        "integration/queue", "POST", {}, than)
                    if ma != 200:
                        return self._loi_hanh_dong(ma, nd)
                    return {"ok": True, "phien": nd.get("session"),
                            "hang_doi": nd.get("queue")}
                if hanh_dong == "phat_playlist":
                    muc = next((p for p in self._playlists
                                if p.get("id") == tham_so.get("playlist_id")), None)
                    if muc is None:
                        return {"ok": False, "loi": "Không tìm thấy playlist trong kho.",
                                "ma_loi": "playlist_not_found"}
                    items = muc.get("items") or []
                    try:
                        chi_so = int(tham_so.get("chi_so") or 0)
                    except (TypeError, ValueError):
                        chi_so = 0
                    if not items or not 0 <= chi_so < len(items):
                        return {"ok": False, "loi": "Playlist không có bài ở vị trí đó.",
                                "ma_loi": "invalid_playlist_index"}
                    bai = items[chi_so]
                    ma, _, nd = await self.tich_hop(
                        "integration/play", "POST", {},
                        {"source": bai.get("source") or "youtube",
                         "target": bai.get("id"),
                         "playlist_id": muc["id"]})
                    if ma != 200:
                        return self._loi_hanh_dong(ma, nd)
                    return {"ok": True, "phien": nd.get("session"),
                            "bai": nd.get("item")}
                if hanh_dong == "them_vao_playlist":
                    muc = self._chuan_hoa_muc(
                        {"source": tham_so.get("nguon") or "youtube",
                         "target": tham_so.get("muc_tieu"),
                         "title": tham_so.get("tieu_de")})
                    if muc is None:
                        return {"ok": False,
                                "loi": "Bài thêm vào playlist chưa đúng định dạng.",
                                "ma_loi": "invalid_item"}
                    ma, _, nd = await self._playlists_hanh_dong(
                        {"action": "add", "playlist_id": tham_so.get("playlist_id"),
                         "item": muc})
                    if ma != 200:
                        return self._loi_hanh_dong(ma, nd)
                    self._ghi_tep("playlists.json", "playlists", self._playlists)
                    return {"ok": True, "playlist": nd.get("playlist"),
                            "added": nd.get("added", 0)}
                if hanh_dong == "them_vao_hang_doi":
                    sid = tham_so.get("phien") or self._phien_hien_tai
                    phien = self._phien_song(sid)
                    if phien is None:
                        return {"ok": False, "loi": "Không có phiên nào đang chạy.",
                                "ma_loi": "session_not_found"}
                    muc = self._chuan_hoa_muc(
                        {"source": tham_so.get("nguon") or "youtube",
                         "target": tham_so.get("muc_tieu"),
                         "title": tham_so.get("tieu_de")})
                    if muc is None:
                        return {"ok": False,
                                "loi": "Bài thêm vào hàng đợi chưa đúng định dạng.",
                                "ma_loi": "invalid_queue_item"}
                    hang = phien.setdefault("queue", {"index": 0, "items": []})
                    if len(hang.get("items", [])) >= BAI_TOI_DA:
                        return {"ok": False, "loi": "Hàng đợi đã đầy.",
                                "ma_loi": "queue_full"}
                    hang.setdefault("items", []).append(muc)
                    phien["revision"] = self._tang_ban_sua()
                    phien["updated_at"] = _iso_utc()
                    return {"ok": True, "hang_doi": hang,
                            "so_bai": len(hang["items"])}
            return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}
        except Exception as e:
            log.warning("thuc_hien youtube lỗi: %s", type(e).__name__)
            return {"ok": False, "loi": "Không thực hiện được."}

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

    # ── hình dạng phiên ngang dịch vụ cũ (NV301 mảnh lõi) ──────────────
    # Dịch vụ cũ trả phiên dạng {session_id, state, position, duration,
    # item, queue: {index, items}, output_entity_ids, revision, ...} và
    # status dạng {success, api_version, app_version, state, item, session,
    # sessions, history_count}. Module này giữ thêm khoá `status` (giá trị
    # thô playing/paused/stopped, test NV277 dùng) và `outputs` (bí danh
    # của output_entity_ids) để không vỡ bên gọi cũ của chính module.

    @staticmethod
    def _trang_thai_phien(trang_thai_tho: str) -> str:
        """Trạng thái thô → state kiểu dịch vụ cũ (stopped → idle)."""
        return {"playing": "playing", "paused": "paused"}.get(
            trang_thai_tho, "idle")

    def _tang_ban_sua(self) -> int:
        self._ban_sua += 1
        return self._ban_sua

    def _phien_rong(self, session_id: str = "") -> dict:
        """Phiên «idle» kiểu dịch vụ cũ khi không có phiên nào đang chạy."""
        return {
            "session_id": session_id, "revision": self._ban_sua,
            "state": "idle", "status": "stopped", "position": 0,
            "duration": 0, "item": None, "current_item": None,
            "queue": {"index": -1, "items": []}, "queue_index": -1,
            "playlist_id": None, "outputs": [], "output_entity_ids": [],
            "volume_level": None, "auto_advance": False,
            "updated_at": _iso_utc(), "supported_actions": [], "controller": "",
        }

    def _anh_xa_phien(self, phien: dict) -> dict:
        hang = phien.get("queue") or {"index": 0, "items": []}
        muc = phien.get("item") or {}
        return {
            "session_id": phien["id"],
            "revision": phien.get("revision", 0),
            "state": self._trang_thai_phien(phien.get("status", "stopped")),
            "status": phien.get("status", "stopped"),
            "source": phien.get("source"),
            "title": muc.get("title") or phien.get("title"),
            "duration": muc.get("duration", phien.get("duration")),
            "position": phien.get("position", 0),
            "item": dict(muc) if muc else None,
            "current_item": dict(muc) if muc else None,
            "queue": {"index": hang.get("index", 0),
                      "items": [dict(i) for i in hang.get("items") or []]},
            "queue_index": hang.get("index", 0),
            "playlist_id": phien.get("playlist_id"),
            "outputs": list(phien.get("outputs") or []),
            "output_entity_ids": list(phien.get("outputs") or []),
            "volume_level": phien.get("volume_level"),
            "auto_advance": phien.get("auto_advance", True),
            # Hình dạng dịch vụ cũ mà tích hợp HA đọc (nội suy vị trí, hành động hỗ trợ).
            "updated_at": phien.get("updated_at") or _iso_utc(),
            "supported_actions": ["stop"] if phien.get("status") != "stopped" else [],
            "controller": phien.get("controller", ""),
        }

    def _phien_song(self, sid) -> dict | None:
        """Phiên còn sống theo id: có trong RAM và chưa dừng hẳn.

        «Dừng là dừng hẳn»: phiên đã stop vẫn nằm trong _phien để tab
        hiện «dung», nhưng next/prev/seek/pause/play-theo-id đều coi như
        không còn (404 session_not_found), không hồi sinh."""
        phien = self._phien.get(sid) if sid else None
        if phien is None or phien.get("status") == "stopped":
            return None
        return phien

    def _phien_hien_tai_song(self) -> dict | None:
        return self._phien_song(self._phien_hien_tai)

    def _chuyen_loa(self, phien: dict, outputs: list):
        """Một loa chỉ thuộc một phiên (dịch vụ cũ: start/set_outputs).

        Loa trong `outputs` bị gỡ khỏi các phiên sống khác; phiên nào hết
        loa thì kết thúc (xoá khỏi _phien, trừ phiên hiện tại đang xét)."""
        for khac_id in list(self._phien):
            if khac_id == phien["id"]:
                continue
            khac = self._phien[khac_id]
            if khac.get("status") == "stopped" or not khac.get("outputs"):
                continue
            con = [o for o in khac["outputs"] if o not in outputs]
            if not con:
                del self._phien[khac_id]
                if self._phien_hien_tai == khac_id:
                    self._phien_hien_tai = None
            elif len(con) != len(khac["outputs"]):
                khac["outputs"] = con
                khac["revision"] = self._tang_ban_sua()
                khac["updated_at"] = _iso_utc()
        phien["outputs"] = list(outputs)

    def _gioi_han_phien(self):
        """Vượt 32 phiên thì đuổi phiên cũ nhất (không đuổi phiên hiện tại)."""
        while len(self._phien) > PHIEN_TOI_DA:
            cu = min((p for p in self._phien.values()
                      if p["id"] != self._phien_hien_tai),
                     key=lambda p: p.get("revision", 0), default=None)
            if cu is None:
                return
            del self._phien[cu["id"]]

    @staticmethod
    def _chuan_hoa_muc(tho: dict, nguon_mac_dinh: str | None = None) -> dict | None:
        """Mục thô (kết quả tìm / mục playlist / mục hàng đợi / gợi ý bên
        gọi) → mục chuẩn phát được, hoặc None khi không phát được.

        Quy tắc theo normalize_item của dịch vụ cũ (id YouTube 11 ký tự,
        id Zing 8–16 ký tự trong link /bai-hat/, id Facebook chuỗi số,
        http phải là URL http(s)); nới hơn ở chỗ: chấp nhận cả dạng
        kết quả tìm (artists) lẫn dạng playlist (channel), và chấp nhận
        mục chỉ có `target` thay cho `id`/`url`."""
        if not isinstance(tho, dict):
            return None
        source = str(tho.get("source") or nguon_mac_dinh or "").strip().lower()
        muc_id = str(tho.get("id") or "").strip()
        url = str(tho.get("url") or tho.get("target") or "").strip()
        if not muc_id and url and "://" not in url:
            # Bên gọi đưa id trần qua `target` (vd mã video/mã bài): đó là
            # id, không phải URL — đừng để nhánh zing/facebook hiểu nhầm.
            muc_id, url = url, ""
        if not source:
            # Kho NV277 lưu mục không có `source`: suy từ hình id/link —
            # id 11 ký tự hoặc link YouTube thì chắc chắn là YouTube.
            # Không suy được thì các nhánh dưới trả None như cũ.
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", muc_id):
                source = "youtube"
            elif url:
                kq = youtube_url_query(url)
                if kq and kq[0] == "video":
                    source = "youtube"
        if source == "youtube":
            if not muc_id and url:
                kq = youtube_url_query(url)
                if kq and kq[0] == "video":
                    muc_id = kq[1]
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", muc_id):
                return None
            url = f"https://www.youtube.com/watch?v={muc_id}"
            kind = "video"
        elif source == "zing":
            if url:
                parsed = urlsplit(url)
                song_id = parsed.path.rsplit("/", 1)[-1].removesuffix(".html")
                if (parsed.scheme != "https"
                        or not (parsed.hostname == "zingmp3.vn"
                                or (parsed.hostname or "").endswith(".zingmp3.vn"))
                        or not parsed.path.startswith("/bai-hat/")
                        or not re.fullmatch(r"[A-Za-z0-9]{8,16}", song_id)):
                    return None
                muc_id = song_id
                url = f"https://zingmp3.vn{parsed.path}"
            elif not re.fullmatch(r"[A-Za-z0-9]{8,16}", muc_id):
                return None
            kind = "song"
        elif source == "facebook":
            if not muc_id and url:
                m = re.search(r"[?&]v=([0-9]{5,25})", url) or \
                    re.search(r"/videos/([0-9]{5,25})", url)
                if m:
                    muc_id = m.group(1)
            if not _FACEBOOK_ID.fullmatch(muc_id):
                return None
            url = f"https://www.facebook.com/watch/?v={muc_id}"
            kind = "video"
        elif source == "http":
            url = url or muc_id
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                return None
            muc_id = url
            kind = "audio"
        else:
            return None
        try:
            duration = max(0, int(float(tho.get("duration") or 0)))
        except (TypeError, ValueError, OverflowError):
            duration = 0
        nghe_si = tho.get("artists") if isinstance(tho.get("artists"), list) else []
        kenh = str(tho.get("channel") or tho.get("artist") or
                   (nghe_si[0] if nghe_si else "") or "").strip()
        anh = str(tho.get("thumbnail") or "").strip()
        if source == "youtube" and not anh:
            anh = f"https://i.ytimg.com/vi/{muc_id}/hqdefault.jpg"
        muc = {
            "source": source, "kind": kind, "id": muc_id, "url": url,
            "title": " ".join(str(tho.get("title") or muc_id).split())[:512] or muc_id,
            "artist": kenh, "channel": kenh, "album": "",
            "thumbnail": anh, "duration": duration,
        }
        if tho.get("media_content_type"):
            muc["media_content_type"] = str(tho["media_content_type"])[:128]
        if tho.get("playlist_id"):
            muc["playlist_id"] = str(tho["playlist_id"])[:256]
        return muc

    async def _sieu_du_lieu(self, source: str, target: str, goi_y: dict) -> dict:
        """Mục chuẩn cho một mục tiêu: gợi ý bên gọi trước, thiếu thì hỏi
        nguồn (YouTube: một lượt giải âm thanh qua semaphore, như _tao_luong).

        Hỏi hỏng thì giữ gợi ý, KHÔNG bịa tên/ảnh: title rơi về id, ảnh
        YouTube rơi về i.ytimg.com (quy ước của dịch vụ cũ)."""
        muc = self._chuan_hoa_muc(
            {"source": source, "id": goi_y.get("id"), "url": goi_y.get("url"),
             "target": target, "title": goi_y.get("title"),
             "channel": goi_y.get("channel") or goi_y.get("artist"),
             "artist": goi_y.get("artist"),
             "artists": goi_y.get("artists"),
             "thumbnail": goi_y.get("thumbnail"),
             "duration": goi_y.get("duration"),
             "media_content_type": goi_y.get("media_content_type")},
            source)
        if muc is None:
            # Không chuẩn hoá được (vd target http chưa qua kiểm LAN ở đây
            # thì bên gọi đã kiểm trước): trả mục tối thiểu từ gợi ý.
            muc = {"source": source, "kind": "audio", "id": target, "url": target,
                   "title": str(goi_y.get("title") or target),
                   "artist": "", "channel": "", "album": "", "thumbnail": "",
                   "duration": 0}
            try:
                muc["duration"] = max(0, int(float(goi_y.get("duration") or 0)))
            except (TypeError, ValueError, OverflowError):
                pass
            return muc
        if source == "youtube" and (not goi_y.get("title") or goi_y.get("duration") is None):
            try:
                sem = await self._chiem_cho_trich_xuat()
                try:
                    giai = await self.trich_xuat.giai_am_thanh("youtube", muc["id"])
                finally:
                    sem.release()
            except Exception as e:
                log.warning("Lấy siêu dữ liệu YouTube hỏng: %s", type(e).__name__)
                giai = None
            if giai:
                if giai.get("title"):
                    muc["title"] = str(giai["title"])
                if giai.get("duration") is not None:
                    try:
                        muc["duration"] = max(0, int(float(giai["duration"])))
                    except (TypeError, ValueError, OverflowError):
                        pass
                if giai.get("channel"):
                    muc["channel"] = muc["artist"] = str(giai["channel"])
                if giai.get("thumbnail"):
                    muc["thumbnail"] = str(giai["thumbnail"])
        return muc

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
        if source == "facebook":
            # Dịch vụ cũ nhận mục tiêu Facebook (mã số) khi mở phiên; giải
            # luồng Facebook thì module này vẫn 502 (xem trich_xuat).
            if _FACEBOOK_ID.fullmatch(target):
                return "facebook", target, None, None
            parsed = urlsplit(target)
            if (parsed.hostname or "").endswith("facebook.com"):
                m = re.search(r"[?&]v=([0-9]{5,25})", target) or \
                    re.search(r"/videos/([0-9]{5,25})", target)
                if m:
                    return "facebook", m.group(1), None, None
            raise ValueError("invalid_target")
        raise ValueError("invalid_source")

    async def _tao_phien(self, than: dict) -> dict:
        """Tạo (hoặc tạo lại) một phiên từ body session/play — trạng thái
        playing. Trả phiên nội bộ; bên gọi tự ghi lịch sử. Lỗi → ValueError
        mang mã lỗi (đường integration dịch thành mã HTTP).

        Thứ tự hàng đợi (theo dịch vụ cũ): hàng đợi bên gọi đưa →
        playlist theo playlist_id → hàng đợi của phiên cũ cùng id → hàng
        đợi tìm gần nhất chứa mục tiêu → một bài duy nhất. Mục tiêu không
        nằm trong hàng đợi đã chọn thì hàng đợi đó bị bỏ, rơi xuống tầng
        sau; không có mục tiêu thì phát bài đầu của hàng đợi đã chọn
        (phát playlist). Link playlist YouTube làm `target` được hiểu là
        playlist_id; link xem có `list=` thì giữ bài đó làm bài hiện tại
        và ghi playlist_id vào phiên."""
        outputs = than.get("outputs")
        if outputs is None:
            outputs = than.get("output_entity_ids")
        if outputs is None:
            outputs = []
        if not isinstance(outputs, list) or not all(isinstance(x, str) for x in outputs):
            raise ValueError("invalid_outputs")
        volume = than.get("volume_level")
        if volume is not None:
            try:
                volume = float(volume)
            except (TypeError, ValueError, OverflowError) as e:
                raise ValueError("invalid_volume_level") from e
            if not 0 <= volume <= 1:
                raise ValueError("invalid_volume_level")
        sid = str(than.get("session_id") or "").strip() or secrets.token_urlsafe(12)
        source = str(than.get("source") or "youtube").strip().lower()
        raw_target = than.get("target")
        playlist_id = str(than.get("playlist_id") or "").strip() or None
        list_tu_link = None
        if source == "youtube" and raw_target:
            kq = youtube_url_query(str(raw_target))
            if kq and kq[0] == "playlist":
                playlist_id = kq[1]
                raw_target = None
            elif kq and kq[0] == "video":
                vals = parse_qs(urlsplit(str(raw_target) if "://" in str(raw_target)
                                        else f"https://{raw_target}").query)
                list_tu_link = (vals.get("list", [""])[0] or None)
        target = None
        if raw_target:
            try:
                source, target, _, _ = await self._xac_thuc_muc_tieu(source, raw_target)
            except ValueError as e:
                raise ValueError(str(e) or "invalid_target") from e
        hang_goi = None
        if isinstance(than.get("queue"), list):
            hang_goi = []
            for tho in than["queue"]:
                muc = self._chuan_hoa_muc(tho)
                if muc is None:
                    raise ValueError("invalid_queue_item")
                hang_goi.append(muc)
        muc_playlist = None
        if playlist_id:
            muc_playlist = next((p for p in self._playlists
                                 if p.get("id") == playlist_id), None)
            if muc_playlist is None:
                raise ValueError("playlist_not_found")
            muc_playlist = [self._chuan_hoa_muc(i) for i in muc_playlist.get("items") or []]
            muc_playlist = [i for i in muc_playlist if i]
        if not target and hang_goi is None and muc_playlist is None:
            raise ValueError("invalid_target")
        # Gợi ý siêu dữ liệu từ bên gọi (title/duration/...) cho bài hiện tại.
        goi_y = {"title": than.get("title"), "duration": than.get("duration"),
                 "thumbnail": than.get("thumbnail"),
                 "channel": than.get("channel"), "artist": than.get("artist"),
                 "artists": than.get("artists"),
                 "media_content_type": than.get("media_content_type")}
        if target and source == "http" and not goi_y.get("title"):
            ten_tep = unquote(urlsplit(target).path.rsplit("/", 1)[-1]) or \
                (urlsplit(target).hostname or target)
            goi_y["title"] = ten_tep
            goi_y["artist"] = "HTTP Audio"
            if not goi_y.get("media_content_type"):
                hau_to = urlsplit(target).path.rsplit(".", 1)[-1].lower() \
                    if "." in urlsplit(target).path.rsplit("/", 1)[-1] else ""
                goi_y["media_content_type"] = _KIEU_HTTP.get(hau_to, "audio/mpeg")
        muc_hien_tai = None
        if target:
            muc_hien_tai = await self._sieu_du_lieu(source, target, goi_y)
        # Giải hàng đợi.
        hang, chi_so, playlist_cua_hang = None, 0, None
        # Bên gọi chỉ rõ vị trí trong hàng đợi đưa (phát từ bài thứ i):
        # dùng thẳng vị trí đó, khỏi tìm theo mục tiêu (tránh nhầm khi
        # hàng đợi có bài trùng nhau).
        if hang_goi is not None and than.get("queue_index") is not None:
            try:
                chi_so_goi = int(than["queue_index"])
            except (TypeError, ValueError):
                chi_so_goi = -1
            if 0 <= chi_so_goi < len(hang_goi):
                hang, chi_so = hang_goi, chi_so_goi
                muc_hien_tai = dict(hang_goi[chi_so_goi])
        ung_vien = []
        if hang is None and hang_goi is not None:
            ung_vien.append((hang_goi, None))
        if muc_playlist is not None:
            ung_vien.append((muc_playlist, playlist_id))
        phien_cu = self._phien.get(sid)
        if phien_cu is not None:
            ung_vien.append(((phien_cu.get("queue") or {}).get("items") or [], None))
        if target:
            ung_vien.append((self._hang_doi_tim_kiem.get(source) or [], None))
        if hang is None:
            for ds, pid in ung_vien:
                if not ds:
                    continue
                if target:
                    tim = next((i for i, m in enumerate(ds)
                                if m.get("source") == source
                                and target in {m.get("id"), m.get("url")}), -1)
                    if tim < 0:
                        continue
                    hang, chi_so, playlist_cua_hang = ds, tim, pid
                    muc_hien_tai = dict(ds[tim])
                    if pid:
                        muc_hien_tai["playlist_id"] = pid
                    break
                hang, chi_so, playlist_cua_hang = ds, 0, pid
                muc_hien_tai = dict(ds[0])
                if pid:
                    muc_hien_tai["playlist_id"] = pid
                break
        if hang is None:
            if muc_hien_tai is None:
                raise ValueError("invalid_target")
            hang, chi_so = [muc_hien_tai], 0
        if list_tu_link and playlist_cua_hang is None:
            playlist_cua_hang = list_tu_link
            muc_hien_tai.setdefault("playlist_id", list_tu_link)
        phien = {
            "id": sid, "source": muc_hien_tai["source"],
            "target": muc_hien_tai["id"],
            "title": muc_hien_tai.get("title"),
            "duration": muc_hien_tai.get("duration"),
            "status": "playing", "outputs": [],
            "position": 0, "item": muc_hien_tai,
            "queue": {"index": chi_so, "items": [dict(m) for m in hang]},
            "playlist_id": playlist_cua_hang,
            "volume_level": volume,
            "auto_advance": than.get("auto_advance") is not False,
            "controller": str(than.get("controller") or "")[:64],
            "revision": self._tang_ban_sua(),
            "updated_at": _iso_utc(),
        }
        self._chuyen_loa(phien, outputs)
        self._phien[sid] = phien
        self._phien_hien_tai = sid
        self._gioi_han_phien()
        return phien

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
                        "app_version": str((self.manifest or {}).get("phien_ban")
                                           or "0.1.0"),
                        "capabilities": ["search", "stream", "playlists", "history"],
                        "sources": ["youtube", "zing"],
                        "playback_sources": ["youtube", "zing", "http"]})
        if duong == "integration/search" and phuong_thuc == "GET":
            source = str(query.get("source") or "youtube").strip().lower()
            if source not in ("youtube", "zing", "facebook"):
                return _loi(400, "invalid_search_source")
            try:
                q, limit = _validated_query_and_limit(
                    str(query.get("q") or ""), query.get("limit", 20))
            except ValueError as e:
                return _loi(400, str(e) or "invalid_search_query")
            sem = await self._chiem_cho_trich_xuat()
            try:
                if source == "facebook":
                    # Dịch vụ cũ chỉ tra cứu Facebook bằng LINK dán vào,
                    # không tìm theo từ khoá (không có đường tìm công khai).
                    muc = self._chuan_hoa_muc({"source": "facebook",
                                               "target": q})
                    if muc is None:
                        return _loi(400, "invalid_search_query")
                    ket_qua = [muc]
                elif source == "youtube":
                    link = youtube_url_query(q)
                    if link and link[0] == "video":
                        giai = await self.trich_xuat.giai_am_thanh(
                            "youtube", link[1])
                        ket_qua = [self._chuan_hoa_muc(
                            {"source": "youtube", "id": link[1],
                             "title": giai.get("title"),
                             "channel": giai.get("channel"),
                             "thumbnail": giai.get("thumbnail"),
                             "duration": giai.get("duration")})]
                        ket_qua = [k for k in ket_qua if k]
                    elif link and link[0] == "playlist":
                        _, muc_pl = await self.trich_xuat.lay_danh_sach_phat(
                            "youtube", link[1], limit)
                        ket_qua = [self._chuan_hoa_muc(m) for m in muc_pl]
                        ket_qua = [k for k in ket_qua if k]
                    else:
                        ket_qua = await self.trich_xuat.tim_kiem(source, q, limit)
                        ket_qua = [self._chuan_hoa_muc(m, source)
                                   for m in ket_qua]
                        ket_qua = [k for k in ket_qua if k]
                else:
                    ket_qua = await self.trich_xuat.tim_kiem(source, q, limit)
                    ket_qua = [self._chuan_hoa_muc(m, source)
                               for m in ket_qua]
                    ket_qua = [k for k in ket_qua if k]
            finally:
                sem.release()
            # Ghi nhớ hàng đợi tìm (mỗi nguồn, tối đa 30) để phát một bài
            # trong kết quả thì bài kế/bài trước đi theo hàng đợi đó.
            if ket_qua:
                self._hang_doi_tim_kiem[source] = \
                    [dict(m) for m in ket_qua[:HANG_DOI_TIM_TOI_DA]]
            # `results` (hình dạng module NV277) và `items` (hình dạng dịch
            # vụ cũ) cùng trỏ tới một danh sách đã chuẩn hoá.
            return _ok({"results": ket_qua, "items": ket_qua,
                        "source": source, "total": len(ket_qua)})
        if duong == "integration/status" and phuong_thuc == "GET":
            phien = self._phien_hien_tai_song()
            # sessions: chỉ phiên còn sống, mới đổi nhất trước (như
            # snapshots của dịch vụ cũ); phiên đã dừng không nằm đây.
            song = [p for p in self._phien.values()
                    if p.get("status") != "stopped"]
            song.sort(key=lambda p: p.get("revision", 0), reverse=True)
            if phien is not None:
                anh_xa = self._anh_xa_phien(phien)
                trang_thai, muc = anh_xa["state"], anh_xa["item"]
            else:
                anh_xa, trang_thai, muc = self._phien_rong(), "idle", None
            return _ok({
                "api_version": "1",
                "app_version": str((self.manifest or {}).get("phien_ban")
                                   or "0.1.0"),
                "state": trang_thai, "item": muc,
                "session": anh_xa,
                "sessions": [self._anh_xa_phien(p) for p in song],
                "history_count": len(self._lich_su),
                "playlists_count": len(self._playlists),
            })
        if duong == "integration/history" and phuong_thuc == "GET":
            lich_su = list(reversed(self._lich_su))
            return _ok({"history": lich_su, "items": lich_su,
                        "total": len(lich_su)})
        if duong == "integration/stop" and phuong_thuc == "POST":
            # Ngang dịch vụ cũ: stop theo session_id hoặc expected_revision;
            # không khớp → 200 {stopped: False} (không 404), phiên idle.
            # Nới hơn cũ: body rỗng thì dừng hết (hành vi NV277 giữ lại).
            sid = than.get("session_id")
            ban_sua = than.get("expected_revision")
            if sid is not None:
                muc_tieu = self._phien.get(sid)
                if muc_tieu is None:
                    return _ok({"stopped": False, "state": "idle",
                                "session": self._phien_rong(str(sid))})
                if ban_sua is not None:
                    try:
                        ban_sua = int(ban_sua)
                    except (TypeError, ValueError):
                        return _loi(400, "invalid_session_revision")
                    if ban_sua != muc_tieu.get("revision"):
                        hien = self._phien_hien_tai_song()
                        return _ok({"stopped": False,
                                    "state": self._trang_thai_phien(
                                        hien["status"]) if hien else "idle",
                                    "session": self._anh_xa_phien(hien)
                                    if hien else self._phien_rong()})
                muc_tieu["status"] = "stopped"
                if self._phien_hien_tai == sid:
                    self._phien_hien_tai = None
            elif ban_sua is not None:
                try:
                    ban_sua = int(ban_sua)
                except (TypeError, ValueError):
                    return _loi(400, "invalid_session_revision")
                khop = next((p for p in self._phien.values()
                             if p.get("revision") == ban_sua
                             and p.get("status") != "stopped"), None)
                if khop is None:
                    hien = self._phien_hien_tai_song()
                    return _ok({"stopped": False,
                                "state": self._trang_thai_phien(
                                    hien["status"]) if hien else "idle",
                                "session": self._anh_xa_phien(hien)
                                if hien else self._phien_rong()})
                khop["status"] = "stopped"
                if self._phien_hien_tai == khop["id"]:
                    self._phien_hien_tai = None
            else:
                for p in self._phien.values():
                    p["status"] = "stopped"
                self._phien_hien_tai = None
            self._luong.clear()  # dừng là dừng hẳn: vé luồng cũ vô hiệu
            hien = self._phien_hien_tai_song()
            return _ok({"stopped": True,
                        "state": self._trang_thai_phien(hien["status"])
                        if hien else "idle",
                        "session": self._anh_xa_phien(hien)
                        if hien else self._phien_rong(
                            str(sid) if sid is not None else "")})
        if duong == "integration/session" and phuong_thuc == "POST":
            # Dịch vụ cũ trả 200 (không phải 201) cho record_session.
            try:
                phien = await self._tao_phien(than)
            except ValueError as e:
                ma_loi = str(e) or "invalid_target"
                if ma_loi == "playlist_not_found":
                    return _loi(404, ma_loi)
                return _loi(400, ma_loi)
            self._ghi_lich_su({"source": phien["source"],
                               "target": phien["target"],
                               "title": phien.get("title")})
            anh_xa = self._anh_xa_phien(phien)
            return _ok({"session": anh_xa, "item": anh_xa["item"]})
        if duong == "integration/playlists" and phuong_thuc == "GET":
            return _ok({"playlists": self._playlists})
        if duong == "integration/playlists" and phuong_thuc == "POST":
            ket_qua = await self._playlists_hanh_dong(than)
            if (than.get("action") or "list") not in ("list", "export"):
                self._ghi_tep("playlists.json", "playlists", self._playlists)
            return ket_qua
        if duong == "integration/session/outputs" and phuong_thuc == "POST":
            sid = than.get("session_id") or self._phien_hien_tai
            phien = self._phien_song(sid)
            if phien is None:
                return _ok({"stopped": False,
                            "session": self._phien_rong(str(sid or ""))})
            outputs = than.get("outputs")
            if outputs is None:
                outputs = than.get("output_entity_ids")
            if not isinstance(outputs, list) or not all(isinstance(x, str) for x in outputs):
                return _loi(400, "invalid_outputs")
            if not outputs:
                # Hết loa thì phiên kết thúc (dịch vụ cũ: set_outputs rỗng
                # → stop); vẫn giữ bản ghi dừng cho tab hiện «dung».
                phien["status"] = "stopped"
                if self._phien_hien_tai == phien["id"]:
                    self._phien_hien_tai = None
                return _ok({"stopped": True,
                            "session": self._phien_rong(phien["id"])})
            self._chuyen_loa(phien, outputs)
            phien["revision"] = self._tang_ban_sua()
            phien["updated_at"] = _iso_utc()
            return _ok({"stopped": False,
                        "session": self._anh_xa_phien(phien)})
        if duong == "integration/stream" and phuong_thuc == "POST":
            return await self._tao_luong(than, goc)
        if duong == "integration/play" and phuong_thuc == "POST":
            return await self._phat(than)
        if duong == "integration/pause" and phuong_thuc == "POST":
            # MỚI so với dịch vụ cũ (dịch vụ cũ không có endpoint pause —
            # HA tạm dừng qua media_player): giữ phiên, chỉ đổi trạng thái.
            sid = than.get("session_id") or self._phien_hien_tai
            phien = self._phien_song(sid)
            if phien is None:
                return _loi(404, "session_not_found")
            async with self._khoa:
                phien["status"] = "paused"
                phien["revision"] = self._tang_ban_sua()
                phien["updated_at"] = _iso_utc()
            anh_xa = self._anh_xa_phien(phien)
            return _ok({"session": anh_xa, "item": anh_xa["item"]})
        if duong in ("integration/next", "integration/prev") and phuong_thuc == "POST":
            sid = than.get("session_id") or self._phien_hien_tai
            phien = self._phien_song(sid)
            if phien is None:
                return _loi(404, "session_not_found")
            async with self._khoa:
                hang = phien.get("queue") or {"index": 0, "items": []}
                cac_bai = hang.get("items") or []
                moi = hang.get("index", 0) + (1 if duong.endswith("/next") else -1)
                if moi < 0:
                    return _loi(409, "queue_start")
                if moi >= len(cac_bai):
                    return _loi(409, "queue_end")
                bai = dict(cac_bai[moi])
                phien["item"] = bai
                phien["source"] = bai.get("source", phien.get("source"))
                phien["target"] = bai.get("id", phien.get("target"))
                phien["title"] = bai.get("title")
                phien["duration"] = bai.get("duration", 0)
                hang["index"] = moi
                phien["position"] = 0
                phien["status"] = "playing"
                phien["revision"] = self._tang_ban_sua()
                phien["updated_at"] = _iso_utc()
            self._ghi_lich_su({"source": phien["source"],
                               "target": phien["target"],
                               "title": phien.get("title")})
            anh_xa = self._anh_xa_phien(phien)
            return _ok({"session": anh_xa, "item": anh_xa["item"]})
        if duong == "integration/seek" and phuong_thuc == "POST":
            sid = than.get("session_id") or self._phien_hien_tai
            phien = self._phien_song(sid)
            if phien is None:
                return _loi(404, "session_not_found")
            # Dịch vụ cũ nhận `giay`; module NV277 nhận `position` — nhận cả hai.
            gia_tri = than.get("giay")
            if gia_tri is None:
                gia_tri = than.get("position")
            try:
                vi_tri = float(gia_tri)
            except (TypeError, ValueError):
                return _loi(400, "invalid_seek_position")
            if vi_tri != vi_tri or vi_tri < 0:
                return _loi(400, "invalid_seek_position")
            thoi_luong = phien.get("duration") or 0
            if thoi_luong > 0:
                vi_tri = min(vi_tri, float(thoi_luong))
            async with self._khoa:
                phien["position"] = vi_tri
                phien["revision"] = self._tang_ban_sua()
                phien["updated_at"] = _iso_utc()
            anh_xa = self._anh_xa_phien(phien)
            # seek_url: dịch vụ cũ tự tính từ mục lục sidx (?t=) để loa
            # nhảy tới; rơ-le vé của module này không có sidx và vé không
            # mang vị trí bắt đầu, nên KHÔNG bịa URL — loa tua bằng Range
            # trên URL luồng đã có. Trả None rõ ràng thay vì URL sai.
            return _ok({"session": anh_xa, "item": anh_xa["item"],
                        "position": vi_tri, "seek_url": None})
        if duong == "integration/queue" and phuong_thuc == "GET":
            # NV301 mảnh giao diện: hàng đợi của phiên (tab hiện «Queue
            # (a/b)», nút «Thêm vào Queue» dùng POST bên dưới).
            sid = query.get("session_id") or self._phien_hien_tai
            phien = self._phien_song(sid)
            if phien is None:
                return _loi(404, "session_not_found")
            hang = phien.get("queue") or {"index": 0, "items": []}
            return _ok({"session_id": phien["id"], "queue": hang,
                        "queue_index": hang.get("index", 0)})
        if duong == "integration/queue" and phuong_thuc == "POST":
            sid = than.get("session_id") or self._phien_hien_tai
            phien = self._phien_song(sid)
            if phien is None:
                return _loi(404, "session_not_found")
            hanh_dong = than.get("action") or "add"
            if hanh_dong == "add":
                muc = self._chuan_hoa_muc(than.get("item") or {})
                if muc is None:
                    return _loi(400, "invalid_queue_item")
                hang = phien.setdefault("queue", {"index": 0, "items": []})
                if len(hang.get("items", [])) >= BAI_TOI_DA:
                    return _loi(400, "queue_full")
                hang.setdefault("items", []).append(muc)
                phien["revision"] = self._tang_ban_sua()
                phien["updated_at"] = _iso_utc()
            elif hanh_dong == "remove":
                hang = phien.get("queue") or {"index": 0, "items": []}
                try:
                    chi_so = int(than.get("index"))
                except (TypeError, ValueError):
                    return _loi(400, "invalid_queue_index")
                items = hang.get("items", [])
                if not 0 <= chi_so < len(items):
                    return _loi(400, "invalid_queue_index")
                del items[chi_so]
                if chi_so < hang.get("index", 0):
                    hang["index"] -= 1
                elif chi_so == hang.get("index", 0):
                    # Xoá đúng bài đang phát: bài kế (nếu còn) thành bài
                    # hiện tại, vị trí về 0; hết hàng thì giữ chỉ số cuối.
                    phien["position"] = 0
                    if hang["index"] >= len(items):
                        hang["index"] = max(0, len(items) - 1)
                    if items:
                        bai_moi = items[hang["index"]]
                        phien["item"] = dict(bai_moi)
                        phien["title"] = bai_moi.get("title")
                        phien["duration"] = bai_moi.get("duration", 0)
                elif hang.get("index", 0) >= len(items):
                    hang["index"] = max(0, len(items) - 1)
                phien["revision"] = self._tang_ban_sua()
                phien["updated_at"] = _iso_utc()
            else:
                return _loi(400, "invalid_queue_action")
            anh_xa = self._anh_xa_phien(phien)
            return _ok({"session": anh_xa, "queue": anh_xa["queue"],
                        "queue_index": anh_xa["queue_index"]})
        return _loi(404, "not_found")

    def _tao_playlist_moi(self, ten, items):
        """Tạo playlist đã chuẩn hoá (chuẩn hoá + khử trùng + trần 500),
        như PlaylistStore.create của dịch vụ cũ."""
        if len(self._playlists) >= PLAYLIST_TOI_DA:
            raise ValueError("too_many_playlists")
        sach, thay = [], set()
        for tho in items or []:
            muc = self._chuan_hoa_muc(tho)
            if muc is None:
                continue
            khoa = (muc["source"], muc["id"])
            if khoa in thay or len(sach) >= BAI_TOI_DA:
                continue
            thay.add(khoa)
            sach.append(muc)
        muc = {"id": secrets.token_urlsafe(8), "name": _ten_playlist(ten),
               "items": sach}
        self._playlists.append(muc)
        return muc

    async def _nhap_nguon_playlist(self, text):
        """(tên, các bài, url_nguồn) từ mã chia sẻ hoặc link — port
        _read_playlist_source của dịch vụ cũ.

        Mã TTPL1. giải ngay trong RAM; link playlist YouTube lấy qua
        yt-dlp (không cần khoá). Link album/playlist Zing → 502 rõ
        (dịch vụ cũ lấy qua API web có khoá ký riêng, module này không
        có khoá đó nên không bịa dữ liệu). Link khác → 400."""
        text = str(text or "").strip()
        if text.startswith("TTPL1."):
            try:
                ten, muc = _doc_ma_chia_se(text)
            except ValueError as e:
                raise ValueError(str(e) or "invalid_share_code") from e
            return ten or "Playlist chia sẻ", muc, ""
        pid = _id_playlist_youtube(text)
        if pid:
            sem = await self._chiem_cho_trich_xuat()
            try:
                ten, muc = await self.trich_xuat.lay_danh_sach_phat(
                    "youtube", pid, BAI_TOI_DA)
            except TrichXuatUnavailable as e:
                raise ValueError("playlist_unavailable") from e
            finally:
                sem.release()
            muc = [m for m in (self._chuan_hoa_muc(i) for i in muc) if m]
            return ten or "Playlist YouTube", muc, text
        if _la_link_playlist_zing(text):
            raise ValueError("playlist_unavailable")
        raise ValueError("invalid_playlist_link")

    async def _playlists_hanh_dong(self, than):
        """Một lệnh playlist — hợp đồng playlist_action của dịch vụ cũ
        (list/create/rename/delete/add/remove/move/export/import), trả
        {playlists, ...} và mã 200 cho mọi lệnh thành công.

        Khác cũ (ghi rõ): import còn nhận dạng cũ `playlists` (thay toàn
        bộ kho, hành vi NV277) khi không có `text`; remove/move nhận cả
        khoá NV277 (`item_id`/`from`) lẫn khoá cũ (`index`)."""
        hanh_dong = than.get("action") or "list"
        if hanh_dong == "list":
            return _ok({"playlists": self._playlists})
        if hanh_dong == "export":
            pid = than.get("playlist_id") or than.get("id")
            muc = next((p for p in self._playlists if p.get("id") == pid), None)
            if muc is None:
                return _loi(404, "playlist_not_found")
            items = [m for m in (self._chuan_hoa_muc(i) for i in muc.get("items") or [])
                     if m]
            return _ok({"playlists": self._playlists,
                        "code": _ma_chia_se({**muc, "items": items})})
        if hanh_dong == "create":
            ten = _ten_playlist(than.get("name"))
            if not ten:
                return _loi(400, "invalid_playlist_name")
            try:
                muc = self._tao_playlist_moi(
                    ten, than.get("items") if isinstance(than.get("items"), list) else [])
            except ValueError as e:
                return _loi(400, str(e) or "invalid_playlist_name")
            # Dịch vụ cũ trả 200 cho mọi lệnh playlist (kể cả create).
            return _ok({"playlists": self._playlists, "playlist": muc})
        if hanh_dong == "import":
            if than.get("text") is not None:
                try:
                    ten, muc, _url = await self._nhap_nguon_playlist(than.get("text"))
                except ValueError as e:
                    ma_loi = str(e) or "invalid_playlist_link"
                    if ma_loi == "playlist_unavailable":
                        return _loi(502, ma_loi)
                    return _loi(400, ma_loi)
                if not muc:
                    return _loi(400, "playlist_empty")
                ten_ghi = _ten_playlist(than.get("name")) or ten
                try:
                    muc_moi = self._tao_playlist_moi(ten_ghi, muc)
                except ValueError as e:
                    return _loi(400, str(e) or "invalid_playlist_name")
                return _ok({"playlists": self._playlists, "playlist": muc_moi})
            ds = than.get("playlists")
            if not isinstance(ds, list):
                return _loi(400, "invalid_playlists")
            self._playlists = [p for p in ds if isinstance(p, dict)]
            return _ok({"playlists": self._playlists})
        pid = than.get("playlist_id") or than.get("id")
        for muc in self._playlists:
            if muc.get("id") != pid:
                continue
            if hanh_dong == "rename":
                ten = _ten_playlist(than.get("name"))
                if not ten:
                    return _loi(400, "invalid_playlist_name")
                muc["name"] = ten
                return _ok({"playlists": self._playlists, "playlist": muc})
            if hanh_dong == "delete":
                self._playlists.remove(muc)
                return _ok({"playlists": self._playlists, "deleted": True})
            if hanh_dong == "add":
                if isinstance(than.get("items"), list):
                    them, da_them = than["items"], 0
                    for tho in them:
                        item = self._chuan_hoa_muc(tho)
                        if item is None:
                            continue
                        if any(i.get("source") == item["source"]
                               and i.get("id") == item["id"]
                               for i in muc.get("items", [])):
                            continue
                        if len(muc.get("items", [])) >= BAI_TOI_DA:
                            return _loi(400, "playlist_full")
                        muc.setdefault("items", []).append(item)
                        da_them += 1
                    return _ok({"playlists": self._playlists,
                                "playlist": muc, "added": da_them})
                item = than.get("item")
                if not isinstance(item, dict):
                    return _loi(400, "invalid_item")
                chuan = self._chuan_hoa_muc(item)
                if chuan is None:
                    return _loi(400, "invalid_item")
                if any(i.get("source") == chuan["source"]
                       and i.get("id") == chuan["id"]
                       for i in muc.get("items", [])):
                    return _ok({"playlists": self._playlists,
                                "playlist": muc, "added": 0})
                if len(muc.get("items", [])) >= BAI_TOI_DA:
                    return _loi(400, "playlist_full")
                muc.setdefault("items", []).append(chuan)
                return _ok({"playlists": self._playlists,
                            "playlist": muc, "added": 1})
            if hanh_dong == "remove":
                if than.get("index") is not None:
                    try:
                        chi_so = int(than["index"])
                    except (TypeError, ValueError):
                        return _loi(400, "invalid_playlist_index")
                    items = muc.get("items", [])
                    if not 0 <= chi_so < len(items):
                        return _loi(400, "invalid_playlist_index")
                    del items[chi_so]
                    return _ok({"playlists": self._playlists, "playlist": muc})
                muc["items"] = [i for i in muc.get("items", [])
                                if i.get("id") != than.get("item_id")]
                return _ok({"playlists": self._playlists, "playlist": muc})
            if hanh_dong == "move":
                items = muc.get("items", [])
                tu = than.get("from", than.get("index"))
                den = than.get("to")
                try:
                    tu, den = int(tu), int(den)
                except (TypeError, ValueError):
                    return _loi(400, "invalid_playlist_index")
                if not (0 <= tu < len(items) and 0 <= den < len(items)):
                    return _loi(400, "invalid_playlist_index")
                items.insert(den, items.pop(tu))
                return _ok({"playlists": self._playlists, "playlist": muc})
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
        """Phát: tiếp tục phiên sống (resume, không ghi lại lịch sử) hoặc
        tạo phiên mới (ghi lịch sử). Phiên đã dừng thì 404, không hồi sinh."""
        sid_yeu_cau = than.get("session_id")
        if sid_yeu_cau is not None:
            phien = self._phien_song(sid_yeu_cau)
            if phien is None:
                return _loi(404, "session_not_found")
            async with self._khoa:
                phien["status"] = "playing"
                phien["revision"] = self._tang_ban_sua()
                phien["updated_at"] = _iso_utc()
                outputs = than.get("outputs")
                if outputs is None:
                    outputs = than.get("output_entity_ids")
                if outputs is not None:
                    if not isinstance(outputs, list) or \
                            not all(isinstance(x, str) for x in outputs):
                        return _loi(400, "invalid_outputs")
                    self._chuyen_loa(phien, outputs)
            anh_xa = self._anh_xa_phien(phien)
            return _ok({"session": anh_xa, "item": anh_xa["item"],
                        "session_revision": anh_xa["revision"]})
        if than.get("target") or than.get("queue") or than.get("playlist_id"):
            try:
                phien = await self._tao_phien(than)
            except ValueError as e:
                ma_loi = str(e) or "invalid_target"
                if ma_loi == "playlist_not_found":
                    return _loi(404, ma_loi)
                return _loi(400, ma_loi)
            self._ghi_lich_su({"source": phien["source"],
                               "target": phien["target"],
                               "title": phien.get("title")})
            anh_xa = self._anh_xa_phien(phien)
            return _ok({"session": anh_xa, "item": anh_xa["item"],
                        "session_revision": anh_xa["revision"]})
        # Không mục tiêu mới: tiếp tục phiên hiện tại nếu còn sống.
        phien = self._phien_hien_tai_song()
        if phien is None:
            return _loi(400, "invalid_target")
        async with self._khoa:
            phien["status"] = "playing"
            phien["revision"] = self._tang_ban_sua()
            phien["updated_at"] = _iso_utc()
        anh_xa = self._anh_xa_phien(phien)
        return _ok({"session": anh_xa, "item": anh_xa["item"],
                    "session_revision": anh_xa["revision"]})

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

