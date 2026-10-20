"""Thư viện chụp ảnh camera cho dịch vụ phụ `dich-vu/camera/`.

NGUỒN (LUẬT PORT của chủ máy): chép hàm chạy thật từ
``/opt/claude-c2a/chatgpt2api/services/camera_nha.py``
(bản sao làm việc ``~/workspace/c2a-nv146``, commit ``d879b034fa2154810c0b6a3e33e79a14e7ff3696``,
756 dòng) — chỉ đổi lớp kết nối của web2api, KHÔNG viết lại từ đầu:

- ``_chup_go2rtc``: ``httpx`` → ``urllib`` (dịch vụ phụ chỉ thư viện chuẩn).
- Sổ camera (``services.config`` của c2a) không có ở đây: các hàm nhận bản ghi
  ``cam`` trực tiếp — dịch vụ phụ không giữ cấu hình, module gửi từng lượt.
- ``_chup_rtsp`` giữ NGUYÊN cách c2a (URL RTSP vào argv ffmpeg) — xem chú thích
  dài ở đầu tệp giải thích vì sao (container riêng, user non-root, /proc không
  chia sẻ; dùng env chỉ ẩn khỏi ps chứ không ẩn khỏi root). Log và phản hồi
  luôn dùng ``che_bi_mat``.

KHÔNG port ở đây (lý do trong báo cáo «Phần KHÔNG port và vì sao»):
``danh_sach/them/xoa/tim/_khop`` (sổ camera ở module web2api, không ở dịch vụ
phụ), ``_DocBenBi``/``_DocKhungKhoa`` (cv2 + luồng giữ mở — thuộc lớp nhận mặt/YOLO).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from urllib.parse import quote, urlsplit, urlunsplit

from kenh import logger

#: Khung gửi cho người xem. Ảnh camera 4K gửi qua Zalo/Telegram là thừa, mà
#: nhiều đầu ghi trả JPEG vài MB.
CANH_GUI = 1600
#: Khung đưa cho AI đọc. Đo trên máy thật (xem README blueprint cảnh báo camera):
#: ba ảnh luồng phụ tốn 978 token ~1,0 giây, ba ảnh luồng chính tốn 8 425 token
#: ~20 giây, cùng kết quả nhận dạng. Ảnh nét chỉ để mắt người.
CANH_AI = 768
#: Chốt an toàn cho khung tải về, trước khi thu nhỏ.
TOI_DA_BYTE = 12_000_000
#: Cổng RTSP mặc định của go2rtc (cổng HTTP là 1984).
CONG_RTSP_GO2RTC = 8554

KIEU_HOP_LE = ("go2rtc", "rtsp")


class LoiCamera(RuntimeError):
    """Chụp không được — thông điệp đã sẵn sàng đọc cho người dùng."""


def che_bi_mat(url: str) -> str:
    """Bỏ mật khẩu khỏi URL trước khi cho ai đó nhìn thấy.

    ``rtsp://admin:MatKhau@10.0.0.5/stream`` → ``rtsp://admin:***@10.0.0.5/stream``
    """
    if not url:
        return ""
    try:
        p = urlsplit(url)
    except ValueError:
        return re.sub(r"//[^/@]*@", "//***@", url)
    if not p.password:
        return url
    chu = f"{p.username or ''}:***@{p.hostname or ''}"
    if p.port:
        chu += f":{p.port}"
    return urlunsplit((p.scheme, chu, p.path, p.query, p.fragment))


def kich_thuoc_jpeg(jpeg: bytes) -> tuple[int, int] | None:
    """Đọc rộng×cao từ header JPEG. Không nhận ra thì trả ``None``.

    Đọc bằng tay thay vì gọi ffprobe: chỉ cần lướt vài chục byte đầu, mà mỗi lần
    tránh được một tiến trình con là mỗi lần bớt vài chục mili-giây.
    """
    i, n = 2, len(jpeg)
    if jpeg[:2] != b"\xff\xd8":
        return None
    while i + 9 < n:
        if jpeg[i] != 0xFF:
            i += 1
            continue
        dau = jpeg[i + 1]
        # SOF0…SOF15, trừ DHT(C4), JPG(C8), DAC(CC) — chúng không mang kích thước.
        if 0xC0 <= dau <= 0xCF and dau not in (0xC4, 0xC8, 0xCC):
            cao = int.from_bytes(jpeg[i + 5:i + 7], "big")
            rong = int.from_bytes(jpeg[i + 7:i + 9], "big")
            return (rong, cao) if rong and cao else None
        if dau in (0xD8, 0x01) or 0xD0 <= dau <= 0xD7:
            i += 2
            continue
        i += 2 + int.from_bytes(jpeg[i + 2:i + 4], "big")
    return None


def _thu_nho(jpeg: bytes, canh: int) -> bytes:
    """Thu nhỏ cạnh dài nhất về ``canh`` px. Lỗi thì trả nguyên bản.

    Thu nhỏ là bước làm-đẹp, không phải bước bắt buộc: ffmpeg trục trặc thì thà
    gửi ảnh to còn hơn không có ảnh nào.

    Ảnh đã nhỏ hơn đích thì trả nguyên bản, KHÔNG mã hoá lại. Đo trên camera
    thật: khung luồng phụ 640×480 nặng 27 KB, cho qua ffmpeg với đích 1600 px
    thì ffmpeg không phóng to (đã chặn bằng ``decrease``) nhưng vẫn nén lại và
    ra 69 KB — to gấp hai rưỡi ảnh gốc, mà chất lượng thì kém đi.
    """
    kt = kich_thuoc_jpeg(jpeg)
    if kt and max(kt) <= canh:
        return jpeg
    try:
        p = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-frames:v", "1",
             "-vf", f"scale={canh}:{canh}:force_original_aspect_ratio=decrease",
             "-q:v", "5", "-f", "image2", "pipe:1"],
            input=jpeg, capture_output=True, timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("thu nhỏ ảnh không được: %s", exc)
        return jpeg
    if p.returncode or not p.stdout:
        return jpeg
    return p.stdout


def _chup_go2rtc(cam: dict, timeout: float) -> bytes:
    """Bóc một khung từ máy chủ go2rtc (``GET base/api/frame.jpeg?src=…``)."""
    base = str(cam.get("base") or "").rstrip("/")
    src = quote(str(cam.get("src") or ""), safe="")
    req = urllib.request.Request(f"{base}/api/frame.jpeg?src={src}")
    if cam.get("username"):
        import base64 as _b64
        mkhau = f"{cam['username']}:{cam.get('password') or ''}"
        req.add_header("Authorization", "Basic " + _b64.b64encode(mkhau.encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — base do quản trị khai, kiểm tu_xa ở module
            ma, noi_dung = r.status, r.read(TOI_DA_BYTE + 1)
    except Exception as exc:
        raise LoiCamera(f"không nối được máy chủ go2rtc ({str(exc)[:120]})") from exc
    if ma == 404:
        raise LoiCamera(f"go2rtc không có luồng tên '{src}'")
    if ma in (401, 403):
        raise LoiCamera("go2rtc từ chối — sai tài khoản hoặc mật khẩu")
    if ma != 200 or not noi_dung:
        raise LoiCamera(f"go2rtc trả về mã {ma}")
    if len(noi_dung) > TOI_DA_BYTE:
        raise LoiCamera("khung ảnh quá lớn")
    return noi_dung


def _chup_rtsp(cam: dict, timeout: float) -> bytes:
    """Bóc một khung bằng ffmpeg, cùng cách ``video_vision.trich_khung`` đang làm.

    Ép ``-rtsp_transport tcp`` vì UDP qua wifi hay mất gói giữa chừng, ra khung
    vỡ. Không dùng cờ timeout của ffmpeg — tên cờ đổi giữa các đời (``-stimeout``
    thành ``-timeout``) nên chốt bằng timeout của tiến trình, đời nào cũng đúng.
    """
    url = str(cam.get("url") or "")
    try:
        p = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-rtsp_transport", "tcp", "-i", url, "-frames:v", "1",
             "-q:v", "4", "-f", "image2", "pipe:1"],
            capture_output=True, timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise LoiCamera("máy chủ thiếu ffmpeg nên chưa bóc được ảnh RTSP") from exc
    except subprocess.TimeoutExpired as exc:
        raise LoiCamera(f"camera không trả lời trong {timeout:.0f} giây") from exc
    if p.returncode or not p.stdout:
        # Không bao giờ chép stderr vào thông điệp: dòng lỗi ffmpeg mang nguyên URL có mật khẩu.
        raise LoiCamera("ffmpeg không lấy được khung ảnh từ camera (đã che URL)")
    if len(p.stdout) > TOI_DA_BYTE:
        raise LoiCamera("khung ảnh quá lớn")
    return p.stdout


def url_luong(cam: dict, luong: str = "chinh") -> str:
    """URL RTSP của luồng ``"chinh"`` hoặc ``"phu"`` (go2rtc phát lại theo tên luồng,
    hoặc URL RTSP khai thẳng). Luồng phụ phải KHAI (``src_ai`` / ``url_ai``) — không
    bao giờ đoán bằng cách ghép ``"-sub"``. Ném ``LoiCamera`` khi không có luồng đọc được.
    """
    phu = luong == "phu"
    if cam.get("kind") == "go2rtc":
        src = str(cam.get("src_ai" if phu else "src") or "").strip()
        if src.lower().startswith("rtsp://"):
            return src
        u = urlsplit(str(cam.get("base") or ""))
        if not src or src.lower().startswith(("http://", "https://")) or not u.hostname:
            raise LoiCamera(f"camera không có luồng {'phụ' if phu else 'go2rtc'} đọc video được")
        tk = ""
        if cam.get("username"):
            # Mật khẩu có ký tự như "@" phải mã hoá mới nằm được trong URL.
            tk = f"{quote(str(cam['username']), safe='')}:{quote(str(cam.get('password') or ''), safe='')}@"
        return f"rtsp://{tk}{u.hostname}:{CONG_RTSP_GO2RTC}/{src}"
    url = str(cam.get("url_ai" if phu else "url") or "").strip()
    if not url.lower().startswith("rtsp://"):
        raise LoiCamera(f"camera không có URL RTSP {'luồng phụ' if phu else ''}".strip())
    return url


def _bocc(cam: dict, timeout: float) -> bytes:
    """Bóc một khung từ bản ghi đã chốt luồng."""
    return (_chup_go2rtc(cam, timeout) if cam.get("kind") == "go2rtc"
            else _chup_rtsp(cam, timeout))


def _ban_ghi_luong(cam: dict, phu: bool) -> dict:
    """Bản ghi trỏ vào một luồng cụ thể của camera.

    ``phu=True`` lấy luồng phụ nếu có khai, không có thì rơi về luồng chính —
    khai thiếu luồng phụ chỉ tốn thêm token chứ không được im lặng không có ảnh.
    """
    if not phu:
        return cam
    if cam.get("kind") == "go2rtc":
        src = str(cam.get("src_ai") or "").strip()
        return dict(cam, src=src) if src else cam
    url = str(cam.get("url_ai") or "").strip()
    return dict(cam, url=url) if url else cam


def co_luong_phu(cam: dict) -> bool:
    """Camera này có khai luồng phụ riêng cho AI đọc không."""
    khoa = "src_ai" if cam.get("kind") == "go2rtc" else "url_ai"
    return bool(str(cam.get(khoa) or "").strip())


def chup_tho(ten: str, cam: dict, *, phu: bool = False, timeout: float = 20.0) -> bytes:
    """Bóc một khung nguyên cỡ. Ném ``LoiCamera`` với câu đã sẵn sàng đọc cho người dùng."""
    return _bocc(_ban_ghi_luong(cam, phu), timeout)


def chup(ten: str, cam: dict, *, cho_ai: bool = False, timeout: float = 20.0) -> bytes:
    """Chụp một khung đã thu nhỏ sẵn.

    ``cho_ai=True`` đọc luồng phụ (nếu có khai) và thu nhỏ mạnh hơn, vì ảnh chỉ
    để model đọc chứ không để người xem.
    """
    jpeg = chup_tho(ten, cam, phu=cho_ai, timeout=timeout)
    return _thu_nho(jpeg, CANH_AI if cho_ai else CANH_GUI)


def chup_hai_co(ten: str, cam: dict, *, timeout: float = 20.0) -> tuple[bytes, bytes]:
    """Ảnh gửi người và ảnh cho AI đọc: ``(ảnh gửi, ảnh cho AI)``.

    Không khai luồng phụ thì bóc **một** khung rồi thu nhỏ hai kiểu — hai tấm
    chắc chắn cùng một khoảnh khắc, và chỉ tốn một phiên với camera.

    Khai luồng phụ thì bấm hai luồng:
    - **go2rtc**: bấm song song (go2rtc giữ sẵn một kết nối tới camera rồi phục
      vụ nhiều khách, nên hai lời gọi HTTP cùng lúc không phiền camera).
    - **RTSP thẳng**: bấm nối đuôi. Đo trên camera Dahua thật: hai phiên RTSP
      song song **hỏng cả hai** (hết 25 giây chờ, thử lại vẫn hỏng), trong khi
      nối đuôi xong trong 8,6 giây. Camera loại này chỉ có vài khe phiên, mở
      cùng lúc là nghẽn.
    """
    if not co_luong_phu(cam):
        jpeg = _bocc(cam, timeout)
        return _thu_nho(jpeg, CANH_GUI), _thu_nho(jpeg, CANH_AI)

    ban_chinh = _ban_ghi_luong(cam, False)
    ban_phu = _ban_ghi_luong(cam, True)

    def _phu_hoac_chinh(lay_phu, chinh: bytes) -> bytes:
        """Luồng phụ hỏng KHÔNG được làm hỏng cả lượt — rơi về luồng chính."""
        try:
            return lay_phu()
        except LoiCamera as exc:
            logger.warning({"event": "camera_luong_phu_hong", "loi": str(exc)[:150]})
            return chinh

    if cam.get("kind") == "go2rtc":
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_chinh = pool.submit(_bocc, ban_chinh, timeout)
            f_phu = pool.submit(_bocc, ban_phu, timeout)
            chinh = f_chinh.result()
            phu = _phu_hoac_chinh(f_phu.result, chinh)
    else:
        chinh = _bocc(ban_chinh, timeout)
        phu = _phu_hoac_chinh(lambda: _bocc(ban_phu, timeout), chinh)
    return _thu_nho(chinh, CANH_GUI), _thu_nho(phu, CANH_AI)
