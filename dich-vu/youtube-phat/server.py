"""Máy chủ FastAPI của dịch vụ youtube-phat — NV153 đợt A.

Nguồn port: ``c2a/api/youtube_phat.py`` (TriTue2011/chatgpt2api @ d879b03) —
CHỈ ĐỌC, không sửa c2a, không import từ c2a.

ĐỔI SO VỚI BẢN GỐC:
1. Tiền tố: phục vụ ở GỐC — ``/api/integration/*`` và ``/api/stream/{token}``
   (bản gốc nằm dưới ``/yt`` của c2a).
2. Rơ-le chịu lỗi ``ro_le.luong_chiu_loi`` thay ``_noi_cac_khuc``: chỉ nối lại khi
   NGUỒN cắt (403/404/410 giữa chừng → forget + giải lại link; lỗi đọc/mở →
   nối lại từ đúng byte đã gửi); client bỏ đi → dừng im lặng; hết lần nối →
   ngắt luồng, KHÔNG lấp rác. Thiết kế rơ-le xem PROGRESS.md.
3. Lifespan dừng-hẳn: ``dang_dung=True`` + phát event cho mọi luồng đang chạy +
   chờ tối đa 3 giây rồi thoát; task thống kê ro_le mỗi 5 phút bị huỷ khi tắt.
   Phiên phát chỉ nằm RAM, không lưu đĩa (đúng quy tắc «dừng là dừng hẳn» #1).
4. Cấu hình qua biến môi trường (``cau_hinh.CauHinh``) thay ``services.config``
   của c2a; ``url_lan`` lấy từ ``YT_PUBLISH_IP``, không thì theo Host
   của request (như ``url_goc`` của c2a nhưng KHÔNG gắn ``/yt``).
5. Giới hạn thân JSON của ``POST /api/integration/session`` là 4096
   (bản gốc 8192).

Hợp đồng với các module cùng thư mục (worker khác viết song song):
- ``cau_hinh``: ``doc_cau_hinh(thu_muc_du_lieu) -> CauHinh``; ``CauHinh`` có
  ``thu_muc_du_lieu`` (đã chuẩn hoá, None → mặc định), ``may_chu``, ``cong``,
  ``url_lan`` (từ ``YT_PUBLISH_IP``, None nếu không đặt).
- ``dich_vu``: ``tao_core(thu_muc_du_lieu) -> PlayerCore``; ``PlayerCore`` có
  ``integration_token`` và các phương thức như c2a (``get_session``/
  ``get_sessions``/``set_session_outputs``/``play``/``record_session``/``stop``/
  ``playlist_action``/``search``/``create_stream_url``/``prepare_stream``/
  ``resolve_stream``/``forget_stream``/``load_history``/``add_history``);
  ``API_VERSION``/``APP_VERSION`` ở mức module; ``normalize_target`` nếu có.
- ``xac_thuc``: ``kiem_bearer(authorization, token) -> None``,
  raise ``LoiXacThuc`` khi thiếu/sai (so sánh hằng thời gian).
- ``ro_le``: hợp đồng chi tiết xem ``_luong_cho_loa`` dưới đây.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

try:
    from .cau_hinh import doc_cau_hinh
except ImportError:  # chay truc tiep: python3 server.py
    from cau_hinh import doc_cau_hinh
try:
    from .dich_vu import API_VERSION, APP_VERSION, tao_core
except ImportError:  # chay truc tiep: python3 server.py
    from dich_vu import API_VERSION, APP_VERSION, tao_core

try:  # bản port tối thiểu của dich_vu có thể chưa có hàm này
    from .dich_vu import normalize_target as _normalize_target_dich_vu
except ImportError:
    _normalize_target_dich_vu = None
try:
    from .search import SearchUnavailableError
except ImportError:  # chay truc tiep: python3 server.py
    from search import SearchUnavailableError
try:
    from .streaming import (
        InvalidStreamTokenError,
        StreamUnavailableError,
        danh_sach_hls,
        doc_muc_luc_mp4,
        verify_stream_token,
    )
except ImportError:  # chay truc tiep: python3 server.py
    from streaming import (
        InvalidStreamTokenError,
        StreamUnavailableError,
        danh_sach_hls,
        doc_muc_luc_mp4,
        verify_stream_token,
    )
try:
    from .xac_thuc import LoiXacThuc, kiem_bearer
except ImportError:  # chay truc tiep: python3 server.py
    from xac_thuc import LoiXacThuc, kiem_bearer

try:  # ro_le.py do worker khác viết song song; thiếu thì chỉ đường luồng bị vô hiệu
    from . import ro_le
except ImportError:  # import trực tiếp (python3 server.py / test): thử đường tuyệt đối
    try:
        import ro_le
    except ImportError:  # pragma: no cover - ro_le.py chưa hạ cánh thật
        ro_le = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_KHA_NANG = ["history", "play", "search", "session", "sessions", "status", "stop",
             "youtube_stream", "zing_stream"]

#: Cỡ mỗi khúc proxy xin của nguồn. Đo 21/09/2026 trên một bài 82 MB (ghi trong
#: c2a): xin không giới hạn được 0,033 MB/giây, xin từng khúc 4 MB được
#: 15 MB/giây — nhanh hơn khoảng 450 lần.
KHUC_LUONG = getattr(ro_le, "KHUC", 4 * 1024 * 1024) if ro_le is not None else 4 * 1024 * 1024
#: Đủ cho mục lục sidx của một bài rất dài (mỗi khúc ~12 byte).
_DAU_MUC_LUC = 256 * 1024
_IOS = re.compile(r"iPhone|iPad|iPod")

#: Khoá không bao giờ được lọt vào log (token luồng, URL có ký, Authorization...).
_KHOA_CAM_LOG = {
    "token", "stream_token", "url", "stream_url", "direct_url", "authorization",
    "integration_token", "secret", "headers", "chu_ky", "signature",
}


def _ghi_log(su_kien: dict) -> None:
    """Log JSON một dòng ra stdout, đã lọc mọi khoá nhạy cảm."""
    an_toan = {k: v for k, v in dict(su_kien or {}).items()
               if str(k).lower() not in _KHOA_CAM_LOG}
    try:
        print(json.dumps(an_toan, ensure_ascii=False), flush=True)
    except Exception:
        pass


def _json(status: int, payload: dict) -> JSONResponse:
    return JSONResponse(payload, status_code=status,
                        headers={"Cache-Control": "no-store"})


def _xac_thuc(authorization: str | None, token: str) -> JSONResponse | None:
    """Bearer <integration_token>; sai/thiếu → 401 {"error": "invalid_auth"} (như c2a)."""
    try:
        kiem_bearer(authorization, token)
    except LoiXacThuc:
        return _json(401, {"error": "invalid_auth"})
    return None


async def _doc_json(request: Request, toi_da: int) -> dict:
    """Đọc thân JSON có giới hạn (port ``read_json_limited`` + ``_doc_json`` của c2a).

    Đọc theo chunk qua ``request.stream()`` nên chặn được cả chunked; vượt trần,
    rỗng, không phải dict, JSON hỏng → ``ValueError("invalid_request")`` — các
    route bắt ``ValueError`` và trả đúng mã lỗi như bản gốc.
    """
    total = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > toi_da:
            raise ValueError("invalid_request")
        chunks.append(chunk)
    raw = b"".join(chunks)
    try:
        payload = json.loads(raw) if raw else {}
    except (ValueError, UnicodeDecodeError):
        raise ValueError("invalid_request")
    # Tích hợp HA từ chối body rỗng bằng `invalid_request` (bản gốc: read_json_limited trả {}).
    if not isinstance(payload, dict) or not payload:
        raise ValueError("invalid_request")
    return payload


def _la_ios(request: Request) -> bool:
    return _IOS.search(request.headers.get("user-agent") or "") is not None


def _ios_can_danh_sach(token: str, request: Request) -> bool:
    """iPhone nhận danh sách khúc, trừ khi đang xin đúng một khoảng byte của khúc.

    Loa và Android không đi đây: chúng phát tốt tệp liền. Zing/Facebook cũng không
    — chỉ tiếng YouTube mới là MP4 cắt mảnh làm iPhone chờ theo độ dài cả bài.
    (Port nguyên của c2a.)
    """
    if request.query_params.get("khoi") == "1" or not _la_ios(request):
        return False
    try:
        source, _target = verify_stream_token(token, request.app.state.core.integration_token)
    except InvalidStreamTokenError:
        return False
    return source == "youtube"


def _tong_co_tep(up) -> int:
    """Cỡ THẬT của cả tệp, đọc từ «Content-Range: bytes a-b/TỔNG» của khúc đầu.

    Không dùng «Content-Length» được: với một khúc thì nó là cỡ của khúc ấy thôi.
    (Port nguyên của c2a.)
    """
    tong = str(up.headers.get("Content-Range") or "").rsplit("/", 1)[-1].strip()
    if tong.isdigit():
        return int(tong)
    con = str(up.headers.get("Content-Length") or "").strip()
    return int(con) if con.isdigit() else 0


def _mo_khuc_don(resolved: dict, tu: int, den: int | None):
    """Mở một khúc byte của nguồn qua urllib (ĐỒNG BỘ — luôn gọi qua to_thread).

    Range + Accept-Encoding: identity, timeout 30 (theo yêu cầu NV153 đợt A).
    """
    headers = {**resolved["headers"], "Accept-Encoding": "identity",
               "Range": f"bytes={tu}-{den}" if den is not None else f"bytes={tu}-"}
    return urlopen(UrlRequest(resolved["url"], headers=headers), timeout=30)


async def _mo_luong(token: str, request: Request):
    """(bản ghi luồng đã giải, phản hồi thượng nguồn) hoặc JSONResponse lỗi. Port c2a."""
    if not token or len(token) > 4096:
        return None, _json(403, {"error": "invalid_stream_token"})
    core = request.app.state.core
    try:
        source, target = verify_stream_token(token, core.integration_token)
        resolved = await asyncio.to_thread(core.resolve_stream, source, target)
    except InvalidStreamTokenError:
        return None, _json(403, {"error": "invalid_stream_token"})
    except (StreamUnavailableError, ValueError, OSError):
        return None, _json(502, {"error": "stream_unavailable"})
    return resolved, None


def _url_khuc(request: Request) -> str:
    """Địa chỉ trả một khoảng byte. Bỏ «.m3u8» nếu đang đứng ở danh sách. (Port c2a.)"""
    goc = str(request.url).split("?", 1)[0]
    if goc.endswith(".m3u8"):
        goc = goc[: -len(".m3u8")]
    return goc + "?khoi=1"


async def _danh_sach_khuc(token: str, request: Request):
    """Danh sách HLS của một bài YouTube — port ``_danh_sach_khuc`` của c2a.

    Hỏng mục lục thì 502, không trả tệp liền — trả tệp liền là đúng cái cách
    iPhone đang chờ lâu.
    """
    resolved, loi = await _mo_luong(token, request)
    if loi is not None:
        return loi

    def _doc_dau():
        headers = {**resolved["headers"], "Accept-Encoding": "identity",
                   "Range": f"bytes=0-{_DAU_MUC_LUC - 1}"}
        up = urlopen(UrlRequest(resolved["url"], headers=headers), timeout=20)
        try:
            return up.read(_DAU_MUC_LUC)
        finally:
            dong = getattr(up, "close", None)
            if dong:
                dong()

    try:
        buf = await asyncio.to_thread(_doc_dau)
    except (OSError, HTTPError) as exc:
        logger.warning("youtube_phat_muc_luc_loi: %s", str(exc)[:160])
        return _json(502, {"error": "stream_unavailable"})
    muc = doc_muc_luc_mp4(buf)
    if muc is None:
        logger.warning("youtube_phat_muc_luc_loi: khong_co_sidx")
        return _json(502, {"error": "stream_unavailable"})
    khoi, khuc = muc
    try:
        body = danh_sach_hls(khoi, khuc, _url_khuc(request))
    except ValueError:
        return _json(502, {"error": "stream_unavailable"})
    return Response(body, media_type="application/vnd.apple.mpegurl", headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })


def _luong_cho_loa(app: FastAPI, up, mo_khuc, giai_lai_link, dau: int, cuoi: int | None):
    """Bọc ``ro_le.luong_chiu_loi`` thành async generator cho ``StreamingResponse``.

    Hợp đồng ro_le.py (đã kiểm chứng trên mã thật)::

        async def luong_chiu_loi(mo_khuc, dau, cuoi, *, giai_lai_link,
                                doc_tong, ghi_log, kiem_dung=None, ngu=None)

    - ``mo_khuc(tu, den)``: mở khúc byte ``[tu, den]`` (đồng bộ hay async đều
      được — ro_le tự await qua ``_cho``); obj trả về cần ``.read(n)``,
      ``.headers``, ``.close()`` và ``.ma_trang_thai`` (mã HTTP).
    - ``giai_lai_link()``: forget_stream + resolve lại (đồng bộ hay async).
    - ``doc_tong(headers)``: tổng byte từ Content-Range.
    - ``ghi_log(su_kien: str, **truong)``: log JSON một dòng; TUYỆT ĐỐI không
      token luồng, URL có ký, header Authorization, integration_token.
    - ``kiem_dung``: async ``() -> bool`` — True = client đã ngắt HOẶC dịch vụ
      đang dừng → DỪNG NGAY, không nối lại, không thêm request thượng nguồn
      (dừng-hẳn #4).
    - Hết cách nối lại → raise ``LoiNguonKhongCuuDuoc``: ở đây bắt và ``return``
      (ngắt luồng, KHÔNG lấp rác — dừng-hẳn #5).

    ``up`` là khúc đầu đã mở để đọc header cho HTTP response; ở đây đóng lại
    và để ro_le tự mở lại từ ``dau`` (tốn thêm một khúc thượng nguồn cho mỗi
    luồng, đổi lấy mã đơn giản, một mối nối lại duy nhất).
    """

    def _ghi_log_ro_le(su_kien: str, **truong) -> None:
        _ghi_log({"su_kien": su_kien, **truong})

    def _doc_tong(headers) -> int | None:
        try:
            lay = headers.get("Content-Range") or headers.get("content-range") or ""
            tong = str(lay).rsplit("/", 1)[-1].strip()
            return int(tong) if tong.isdigit() else None
        except Exception:
            return None

    async def _tao():
        su_kien = asyncio.Event()
        dang_chay = getattr(app.state, "luong_dang_chay", None)
        if dang_chay is not None:
            dang_chay.add(su_kien)

        async def _kiem_dung() -> bool:
            if getattr(app.state, "dang_dung", False):
                return True
            return su_kien.is_set()

        try:
            up.close()
        except Exception:
            pass
        try:
            async for mieng in ro_le.luong_chiu_loi(
                mo_khuc,
                dau,
                cuoi,
                giai_lai_link=giai_lai_link,
                doc_tong=_doc_tong,
                ghi_log=_ghi_log_ro_le,
                kiem_dung=_kiem_dung,
            ):
                yield mieng
        except ro_le.LoiNguonKhongCuuDuoc:
            # Hết lần nối lại: ngắt luồng cho loa, không phát tiếp đường khác.
            return
        finally:
            if dang_chay is not None:
                dang_chay.discard(su_kien)

    return _tao()


_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}


def _normalize_target_cho_play(raw_target) -> dict:
    """Chuẩn hoá tối thiểu cho ``/play`` — DỰ PHÒNG khi ``dich_vu.normalize_target``
    chưa có (bản port tối thiểu). Chỉ phủ video YouTube như ``/play`` của c2a."""
    target = str(raw_target or "").strip()
    video_id = target if _VIDEO_ID.fullmatch(target) else None
    if video_id is None:
        parsed = urlsplit(target)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or host not in _YOUTUBE_HOSTS:
            raise ValueError("invalid_youtube_target")
        if host == "youtu.be":
            video_id = parsed.path.strip("/").split("/", 1)[0]
        elif parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith(("/shorts/", "/embed/")):
            video_id = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if not _VIDEO_ID.fullmatch(video_id or ""):
        raise ValueError("invalid_youtube_target")
    return {"kind": "video", "id": video_id}


def _dang_ky_routes(app: FastAPI) -> None:
    """Đăng ký toàn bộ route — port ``create_router`` của c2a, tiền tố ở GỐC
    (``/api/integration/*``, ``/api/stream/{token}``), mọi route integration
    đều cần ``Authorization: Bearer <integration_token>`` kể cả ``/health``."""

    @app.get("/api/integration/health")
    async def health(request: Request, authorization: str | None = Header(default=None)):
        core = request.app.state.core
        if (loi := _xac_thuc(authorization, core.integration_token)) is not None:
            return loi
        return _json(200, {
            "success": True, "status": "ok", "api_version": API_VERSION,
            "app_version": APP_VERSION, "capabilities": _KHA_NANG,
            "sources": ["youtube", "zing", "facebook"],
            "playback_sources": ["youtube", "zing", "facebook", "http"],
        })

    @app.get("/api/integration/search")
    async def search(request: Request, authorization: str | None = Header(default=None)):
        core = request.app.state.core
        if (loi := _xac_thuc(authorization, core.integration_token)) is not None:
            return loi
        query = str(request.query_params.get("q", "")).strip()
        source = str(request.query_params.get("source", "youtube")).strip().lower()
        try:
            limit = int(request.query_params.get("limit", "20"))
            # Độ dài chính xác do search.py kiểm (chữ 120 ký tự, link YouTube 2048).
            if not 1 <= len(query) <= 2048 or not 1 <= limit <= 30:
                raise ValueError
            items = await asyncio.to_thread(core.search, source, query, limit)
        except ValueError as error:
            ma = "invalid_search_source" if str(error) == "invalid_search_source" else "invalid_search_query"
            return _json(400, {"error": ma})
        except SearchUnavailableError:
            return _json(502, {"error": "search_unavailable"})
        return _json(200, {"success": True, "source": source, "items": items, "total": len(items)})

    @app.get("/api/integration/status")
    async def status(request: Request, authorization: str | None = Header(default=None)):
        core = request.app.state.core
        if (loi := _xac_thuc(authorization, core.integration_token)) is not None:
            return loi
        # Đọc trạng thái THẬT lúc hỏi, không cache (dừng-hẳn #3: tắt/kẹt thì trả
        # lỗi hoặc không trả lời, không bao giờ trả trạng thái cũ).
        session, sessions = core.get_sessions()
        return _json(200, {
            "success": True, "api_version": API_VERSION, "app_version": APP_VERSION,
            "state": session["state"], "item": session["item"], "session": session,
            "sessions": sessions, "history_count": len(core.load_history()),
        })

    @app.get("/api/integration/history")
    async def history(request: Request, authorization: str | None = Header(default=None)):
        core = request.app.state.core
        if (loi := _xac_thuc(authorization, core.integration_token)) is not None:
            return loi
        items = core.load_history()
        return _json(200, {"success": True, "items": items, "total": len(items)})

    @app.post("/api/integration/stop")
    async def stop(request: Request, authorization: str | None = Header(default=None)):
        core = request.app.state.core
        if (loi := _xac_thuc(authorization, core.integration_token)) is not None:
            return loi
        try:
            expected_revision = None
            session_id = None
            if int(request.headers.get("content-length") or "0"):
                payload = await _doc_json(request, 256)
                expected_revision = payload.get("expected_revision")
                session_id = payload.get("session_id") or None
                if expected_revision is not None and (
                        isinstance(expected_revision, bool) or not isinstance(expected_revision, int)
                        or expected_revision < 0):
                    raise ValueError("invalid_session_revision")
                if expected_revision is None and session_id is None:
                    raise ValueError("invalid_session_revision")
            result = core.stop(expected_revision, session_id)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return _json(400, {"error": "invalid_session_revision"})
        session = result["session"]
        return _json(200, {"success": True, "stopped": result["stopped"],
                           "state": session["state"], "session": session})

    @app.post("/api/integration/session")
    async def session(request: Request, authorization: str | None = Header(default=None)):
        core = request.app.state.core
        if (loi := _xac_thuc(authorization, core.integration_token)) is not None:
            return loi
        try:
            payload = await _doc_json(request, 4096)
            source = str(payload.get("source") or "").lower()
            ket_qua = await asyncio.to_thread(
                core.record_session, source, payload.get("target"),
                output_entity_ids=payload.get("output_entity_ids"),
                media_content_type=payload.get("media_content_type") or "",
                volume_level=payload.get("volume_level"),
                session_id=payload.get("session_id") or None,
                controller=payload.get("controller") or "",
                auto_advance=payload.get("auto_advance") is not False,
                playlist_id=payload.get("playlist_id") or None)
        except ValueError as error:
            ma = str(error)
            if ma == "playlist_not_found":
                return _json(404, {"error": ma})
            if ma == "unverified_zing_target":
                return _json(403, {"error": ma})
            if ma not in {"invalid_http_audio_target", "invalid_output_entity_ids", "invalid_session_id",
                          "invalid_volume_level", "invalid_youtube_target",
                          "unsupported_session_source"}:
                ma = "invalid_request"
            return _json(400, {"error": ma})
        except (AttributeError, json.JSONDecodeError, UnicodeDecodeError):
            return _json(400, {"error": "invalid_request"})
        return _json(200, {"success": True, "session": ket_qua})

    async def _playlist(payload: dict) -> tuple[int, dict]:
        core = app.state.core
        try:
            ket_qua = await asyncio.to_thread(core.playlist_action, payload)
        except (SearchUnavailableError, StreamUnavailableError):
            # Đọc playlist từ YouTube/Zing hỏng: link sai, playlist riêng tư hoặc nguồn lỗi.
            return 502, {"error": "playlist_unavailable"}
        except ValueError as error:
            return 400, {"error": str(error)}
        return 200, ket_qua

    # Playlist chung cả nhà: tích hợp HA (token) và tab YouTube (quản trị) cùng một kho.
    @app.get("/api/integration/playlists")
    async def playlists_integration(request: Request,
                                    authorization: str | None = Header(default=None)):
        core = request.app.state.core
        if (loi := _xac_thuc(authorization, core.integration_token)) is not None:
            return loi
        ma, body = await _playlist({"action": "list"})
        return _json(ma, {"success": ma == 200, **body})

    @app.post("/api/integration/playlists")
    async def playlist_integration(request: Request,
                                   authorization: str | None = Header(default=None)):
        core = request.app.state.core
        if (loi := _xac_thuc(authorization, core.integration_token)) is not None:
            return loi
        try:
            payload = await _doc_json(request, 400_000)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return _json(400, {"error": "invalid_request"})
        ma, body = await _playlist(payload)
        return _json(ma, {"success": ma == 200, **body})

    @app.post("/api/integration/session/outputs")
    async def session_outputs(request: Request, authorization: str | None = Header(default=None)):
        core = request.app.state.core
        if (loi := _xac_thuc(authorization, core.integration_token)) is not None:
            return loi
        try:
            payload = await _doc_json(request, 4096)
            ket_qua = core.set_session_outputs(payload.get("session_id"), payload.get("output_entity_ids"))
        except ValueError as error:
            ma = str(error) if str(error) in {"invalid_session_id", "invalid_output_entity_ids"} else "invalid_request"
            return _json(400, {"error": ma})
        return _json(200, {"success": True, **ket_qua})

    @app.post("/api/integration/stream")
    async def stream(request: Request, authorization: str | None = Header(default=None)):
        core = request.app.state.core
        if (loi := _xac_thuc(authorization, core.integration_token)) is not None:
            return loi
        try:
            payload = await _doc_json(request, 4096)
            source = payload.get("source")
            # Cửa chặn RIÊNG của tầng API, độc lập với STREAM_SOURCES bên streaming
            # và với các nhánh trong dich_vu (port chú thích của c2a).
            if source not in {"zing", "youtube", "youtube_video", "facebook", "facebook_video"}:
                raise ValueError("unsupported_stream_source")
            # youtube_video: chỉ hình, cho thẻ HA khi YouTube không cho nhúng.
            chieu_cao = [payload.get("max_height") or 720] if source in {"youtube_video", "facebook_video"} else []
            target, resolved = await asyncio.to_thread(core.prepare_stream, source, payload.get("target"), *chieu_cao)
            stream_url = core.create_stream_url(source, target, _url_goc(request))
        except StreamUnavailableError:
            return _json(502, {"error": "stream_unavailable"})
        except ValueError as error:
            ma = str(error)
            if ma == "public_base_url_required":
                return _json(409, {"error": ma})
            if ma == "unverified_zing_target":
                return _json(403, {"error": ma})
            # «invalid_facebook_target» PHẢI có trong danh sách giữ nguyên: thiếu nó thì
            # mã lỗi bị nghiền thành «invalid_request» và người dùng lại mất nguyên nhân.
            if ma not in {"invalid_request", "invalid_youtube_target", "invalid_zing_target",
                          "invalid_facebook_target",
                          "unsupported_stream_source", "youtube_audio_requires_video"}:
                ma = "invalid_request"
            return _json(400, {"error": ma})
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _json(400, {"error": "invalid_request"})
        return _json(200, {"success": True, "source": source, "stream_url": stream_url,
                           "media_content_type": resolved.get("content_type", "audio/mpeg"),
                           "expires_in": 3600,
                           **(_luong_hinh(resolved) if source in {"youtube_video", "facebook_video"} else {})})

    @app.post("/api/integration/play")
    async def play(request: Request, authorization: str | None = Header(default=None)):
        core = request.app.state.core
        if (loi := _xac_thuc(authorization, core.integration_token)) is not None:
            return loi
        chuan_hoa = _normalize_target_dich_vu or _normalize_target_cho_play
        try:
            payload = await _doc_json(request, 4096)
            raw_target = payload.get("target")
            target = chuan_hoa(raw_target)
        except ValueError as error:
            ma = str(error)
            return _json(400, {"error": ma if ma in {"invalid_request", "invalid_youtube_target"}
                               else "invalid_request"})
        except (AttributeError, json.JSONDecodeError, UnicodeDecodeError):
            return _json(400, {"error": "invalid_request"})
        ket_qua = await asyncio.to_thread(core.play, target, raw_target=raw_target)
        return _json(200, {"success": True, "item": ket_qua["item"],
                           "session_revision": ket_qua["revision"]})


def _luong_hinh(giai: dict) -> dict:
    """Thông tin luồng hình cho trình duyệt. `direct_url` là link googlevideo gắn IP
    mạng nhà: máy trong nhà (cùng IP ra Internet) tải thẳng, không qua máy chủ này;
    tải thẳng hỏng nghĩa là đang ở ngoài nhà — khi đó hình phải đi qua link đã ký
    (tốn băng thông tải lên của nhà), trang hỏi người xem trước. (Port c2a.)"""
    return {"height": giai.get("height"), "bitrate_kbps": giai.get("bitrate_kbps"),
            "direct_url": giai.get("url")}


def _url_goc(request: Request) -> str:
    """URL gốc loa dùng để tải luồng: ``YT_PUBLISH_IP`` nếu có, không thì theo chính
    địa chỉ HA đã gọi tới (HA gọi bằng IP LAN nên loa cũng tới được).

    Port ``url_goc`` của c2a nhưng KHÔNG gắn ``/yt`` — dịch vụ này phục vụ ở gốc.
    """
    base = (getattr(request.app.state.cau_hinh, "url_lan", "") or "").strip()
    if base:
        return base
    return f"{request.url.scheme}://{request.headers.get('host') or request.url.netloc}"


def _dang_ky_stream(app: FastAPI) -> None:
    """Route luồng cho loa — port ``proxy_stream``/``head_stream`` của c2a.

    Khác bản gốc ở đúng một điểm: các khúc sau khúc đầu đi qua rơ-le chịu lỗi
    ``ro_le.luong_chiu_loi`` thay vì ``_noi_cac_khuc`` (bản gốc nối im lặng mọi
    lỗi mở khúc kế và không giải lại link khi 403/410 giữa chừng).
    """

    @app.get("/api/stream/{token}")
    async def proxy_stream(token: str, request: Request):
        """Tiếp sóng luồng âm thanh cho loa — loa không gọi thẳng googlevideo.com
        (URL gắn IP máy giải, loa tải là 403).

        LẤY THEO TỪNG KHÚC CÓ GIỚI HẠN, đừng mở một cú không giới hạn (port chú
        thích đo đạc của c2a: khúc 4 MB nhanh hơn ~450 lần so với không giới hạn).
        """
        core = request.app.state.core
        if token.endswith(".m3u8"):
            return await _danh_sach_khuc(token[: -len(".m3u8")], request)
        if _ios_can_danh_sach(token, request):
            return RedirectResponse(str(request.url).split("?", 1)[0] + ".m3u8", status_code=302)
        range_header = request.headers.get("range")
        if range_header and not re.fullmatch(r"bytes=\d*-\d*", range_header):
            return _json(400, {"error": "invalid_range"})
        # «bytes=-500» (xin 500 byte CUỐI) không tính được đầu khúc nếu chưa biết cỡ
        # tệp, mà máy nghe gần như không dùng. Giữ nguyên đường cũ cho nó (như c2a).
        xin = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header or "bytes=0-")
        dau = int(xin.group(1)) if xin else 0
        cuoi_xin = int(xin.group(2)) if xin and xin.group(2) else None

        het_khuc_dau = None if not xin else dau + KHUC_LUONG - 1
        if cuoi_xin is not None:
            het_khuc_dau = min(het_khuc_dau, cuoi_xin) if het_khuc_dau is not None else cuoi_xin
        up = None
        for lan in (1, 2):
            resolved, loi = await _mo_luong(token, request)
            if loi is not None:
                return loi
            try:
                up = await asyncio.to_thread(_mo_khuc_don, resolved, dau, het_khuc_dau)
                break
            except HTTPError as exc:
                # Link được dùng lại hàng giờ: nguồn từ chối link cũ (403/404/410)
                # thì bỏ bản đệm, giải lại đúng một lần (như c2a).
                if lan == 1 and exc.code in {403, 404, 410}:
                    nguon, dich = verify_stream_token(token, core.integration_token)
                    core.forget_stream(nguon, dich)
                    continue
                logger.warning("youtube_phat_luong_loi: %s", str(exc)[:160])
                return _json(502, {"error": "stream_unavailable"})
            except OSError as exc:
                logger.warning("youtube_phat_luong_loi: %s", str(exc)[:160])
                return _json(502, {"error": "stream_unavailable"})

        tong = _tong_co_tep(up)
        cuoi = cuoi_xin if cuoi_xin is not None else (tong - 1 if tong else None)
        out = {"Content-Type": up.headers.get("Content-Type", resolved.get("content_type", "audio/mpeg")),
               "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
               "Accept-Ranges": "bytes"}
        if cuoi is not None:
            out["Content-Length"] = str(cuoi - dau + 1)
        if xin is not None and range_header and tong:
            # Máy nghe hỏi theo khúc thì trả đúng khúc NÓ hỏi — không phải khúc
            # proxy này đang lấy của nguồn. «bytes=-N» (xin None) đi đường cũ:
            # 200 từ đầu như c2a, vì không tính được đầu khúc từ hậu tố.
            ma = 206
            out["Content-Range"] = f"bytes {dau}-{cuoi}/{tong}"
        else:
            ma = 200

        if ro_le is None:  # ro_le.py chưa hạ cánh: không có rơ-le thì không phát luồng
            try:
                up.close()
            except Exception:
                pass
            return _json(502, {"error": "stream_unavailable"})

        hop = {"resolved": resolved}

        async def _mo_khuc(tu: int, den: int | None):
            """Mở khúc kế bằng bản ghi luồng HIỆN TẠI (async — không chặn loop).

            Mở và đọc đều qua ``to_thread``: đọc đồng bộ trong event loop sẽ
            chặn toàn bộ máy chủ, khiến dừng-hẳn không chạy đồng thời được
            (test_dung_han #2 bắt được). Gắn ``.ma_trang_thai`` cho obj trả về
            (ro_le dùng để kiểm bản); HTTPError khi mở được bọc thành
            ``ro_le.LoiMoKhuc`` mang mã để ro_le biết có cần giải lại link
            (403/404/410) không."""
            try:
                ban = await asyncio.to_thread(
                    _mo_khuc_don, hop["resolved"], tu, den)
            except HTTPError as exc:
                raise ro_le.LoiMoKhuc(
                    f"mở khúc [{tu}-{den}]: HTTP {exc.code}",
                    ma_trang_thai=exc.code)
            ban.ma_trang_thai = ban.status  # urllib: .status
            doc_goc = ban.read

            async def _doc(n: int) -> bytes:
                return await asyncio.to_thread(doc_goc, n)

            ban.read = _doc  # type: ignore[method-assign]
            return ban

        def _giai_lai_link() -> dict:
            """forget_stream + resolve_stream (đồng bộ — ro_le tự await).

            Lỗi không cứu được (token hết hạn giữa luồng, giải lại thất bại)
            → raise ``ro_le.LoiNguonKhongCuuDuoc``: ro_le để lọt ra ngoài,
            ``_luong_cho_loa`` bắt và ngắt luồng sạch (dừng-hẳn #5)."""
            try:
                nguon, dich = verify_stream_token(token, core.integration_token)
            except InvalidStreamTokenError as exc:
                raise ro_le.LoiNguonKhongCuuDuoc(f"token hết hạn giữa luồng: {exc}")
            core.forget_stream(nguon, dich)
            try:
                moi = core.resolve_stream(nguon, dich)
            except Exception as exc:
                raise ro_le.LoiNguonKhongCuuDuoc(
                    f"giải lại link thất bại: {type(exc).__name__}")
            hop["resolved"] = moi
            return moi

        return StreamingResponse(
            _luong_cho_loa(request.app, up, _mo_khuc, _giai_lai_link, dau, cuoi),
            status_code=ma, headers=out)

    @app.head("/api/stream/{token}")
    async def head_stream(token: str, request: Request):
        """Loa DLNA hỏi HEAD trước khi GET — trả kiểu, cỡ, ranges, không tải audio.

        LƯU Ý giữ nguyên bug của c2a: HEAD tới token có đuôi ``.m3u8`` KHÔNG strip
        đuôi (khác GET) nên chữ ký verify hỏng → 403 ``invalid_stream_token``
        kèm ``Content-Length: 0``. Sửa bug này sẽ đổi hợp đồng lỗi nên giữ lại
        và ghi chú ở đây.
        """
        if not token.endswith(".m3u8") and _ios_can_danh_sach(token, request):
            return RedirectResponse(str(request.url).split("?", 1)[0] + ".m3u8", status_code=302)
        resolved, loi = await _mo_luong(token, request)
        if loi is not None:
            return Response(status_code=loi.status_code, headers={"Content-Length": "0"})
        content_type = resolved.get("content_type", "audio/mpeg")
        total = None

        def _do() -> tuple[str, int | None]:
            ct, tong = content_type, None
            try:
                h = {**resolved["headers"], "Accept-Encoding": "identity", "Range": "bytes=0-0"}
                with urlopen(UrlRequest(resolved["url"], headers=h), timeout=15) as up:
                    ct = up.headers.get("Content-Type", ct)
                    cr = up.headers.get("Content-Range", "")
                    if "/" in cr and cr.rsplit("/", 1)[-1].strip().isdigit():
                        tong = int(cr.rsplit("/", 1)[-1].strip())
            except (OSError, ValueError):
                pass
            return ct, tong

        content_type, total = await asyncio.to_thread(_do)
        out = {"Content-Type": content_type, "Accept-Ranges": "bytes",
               "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
               "Content-Length": str(total) if total is not None else "0"}
        return Response(status_code=200, headers=out)


def tao_ung_dung(thu_muc_du_lieu: str | None = None) -> FastAPI:
    """App factory cho test và chạy thật.

    ``thu_muc_du_lieu``: thư mục chứa ``integration_token``/lịch sử/playlist.
    Test truyền thư mục tạm để cô lập; ``None`` → mặc định của ``CauHinh``.
    """
    cau_hinh = doc_cau_hinh(thu_muc_du_lieu)
    thu_muc = getattr(cau_hinh, "thu_muc_du_lieu", None) or thu_muc_du_lieu
    core = tao_core(thu_muc)
    app = FastAPI(lifespan=_vong_doi, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cau_hinh = cau_hinh
    app.state.core = core
    app.state.luong_dang_chay = set()
    app.state.dang_dung = False
    app.state.tac_vu_thong_ke = None
    _dang_ky_routes(app)
    _dang_ky_stream(app)
    return app


@asynccontextmanager
async def _vong_doi(app: FastAPI):
    """Lifespan: khởi động đặt app.state + chạy task thống kê; tắt thì dừng-hẳn."""
    app.state.luong_dang_chay = set(getattr(app.state, "luong_dang_chay", None) or ())
    app.state.dang_dung = False
    if getattr(app.state, "tac_vu_thong_ke", None) is None:
        app.state.tac_vu_thong_ke = asyncio.create_task(_thong_ke_dinh_ky(app))
    try:
        yield
    finally:
        await dong_may_chu(app)


async def _cho_luong_xong(app: FastAPI) -> None:
    while getattr(app.state, "luong_dang_chay", None):
        await asyncio.sleep(0.05)


async def dong_may_chu(app: FastAPI) -> None:
    """Dừng-hẳn — đúng các bước shutdown của lifespan, để test gọi trực tiếp.

    1. Đặt ``dang_dung=True``: rơ-le không nối lại nữa (dừng-hẳn #4).
    2. Phát event cho mọi luồng đang chạy: generator của chúng ngắt ở lần kiểm
       tiếp theo, đóng kết nối nguồn ngay (dừng-hẳn #2 — không đẩy nốt bộ đệm).
    3. Chờ các luồng dọn xong, tối đa 3 giây rồi thoát.
    4. Huỷ task thống kê nền.
    Gọi nhiều lần an toàn (idempotent).
    """
    app.state.dang_dung = True
    for su_kien in list(getattr(app.state, "luong_dang_chay", None) or ()):
        try:
            su_kien.set()
        except Exception:
            pass
    try:
        await asyncio.wait_for(_cho_luong_xong(app), timeout=3.0)
    except (asyncio.TimeoutError, TimeoutError):
        pass
    tac_vu = getattr(app.state, "tac_vu_thong_ke", None)
    if tac_vu is not None:
        app.state.tac_vu_thong_ke = None
        tac_vu.cancel()
        try:
            await tac_vu
        except (asyncio.CancelledError, Exception):
            pass


async def _thong_ke_dinh_ky(app: FastAPI) -> None:
    """Task nền: mỗi 5 phút log JSON một dòng thống kê rơ-le."""
    try:
        while True:
            await asyncio.sleep(300)
            if ro_le is None:
                continue
            try:
                tk = ro_le.lay_thong_ke() or {}
            except Exception:
                continue
            _ghi_log({
                "su_kien": "thong_ke_luong",
                "luong": tk.get("luong"),
                "lan_noi_lai": tk.get("lan_noi_lai"),
                "that_bai": tk.get("that_bai"),
                "byte": tk.get("byte"),
            })
    except asyncio.CancelledError:
        pass


def main() -> None:
    """Chạy máy chủ: host/port từ CauHinh (biến môi trường)."""
    app = tao_ung_dung()
    cau_hinh = app.state.cau_hinh
    uvicorn.run(app, host=cau_hinh.may_chu, port=cau_hinh.cong)


if __name__ == "__main__":
    main()
