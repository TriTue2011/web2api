"""Thư viện chụp ảnh camera cho dịch vụ phụ `dich-vu/camera/`.

NGUỒN (LUẬT PORT của chủ máy): chép hàm chạy thật từ
``/opt/claude-c2a/chatgpt2api/services/camera_nha.py``
(bản sao làm việc ``~/workspace/c2a-nv146``, commit ``d879b034fa2154810c0b6a3e33e79a14e7ff3696``,
756 dòng) — chỉ đổi lớp kết nối của web2api, KHÔNG viết lại từ đầu:

- ``_chup_go2rtc``: ``httpx`` → ``urllib`` (dịch vụ phụ chỉ thư viện chuẩn).
- Sổ camera (``services.config`` của c2a) không có ở đây: module ĐĂNG KÝ bảng
  camera một lần (``POST /dang-ky``, xem ``server.py``); các hàm ở đây nhận
  bản ghi đã qua :func:`kiem_ban_ghi` từ bảng của chính dịch vụ — lời gọi
  KHÔNG mang URL nữa (soát độc lập C2: C2).
- ``_chup_rtsp`` giữ NGUYÊN cách c2a (URL RTSP vào argv ffmpeg) — xem chú thích
  dài ở đầu tệp giải thích vì sao (container riêng, user non-root, /proc không
  chia sẻ; dùng env chỉ ẩn khỏi ps chứ không ẩn khỏi root). Log và phản hồi
  luôn dùng ``che_bi_mat``.

KHÔNG port ở đây (lý do trong báo cáo «Phần KHÔNG port và vì sao»):
``danh_sach/them/xoa/tim/_khop`` (sổ camera ở module web2api, không ở dịch vụ
phụ), ``_DocBenBi``/``_DocKhungKhoa`` (cv2 + luồng giữ mở — thuộc lớp nhận mặt/YOLO).
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
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


# ── Kiểm bản ghi + ghim IP (soát độc lập C2: C2 + C4) ───────────────────────
# Lớp lỗi của bản cũ: dịch vụ tin URL trong THÂN từng lời gọi — ai có khoá
# dịch vụ (hoặc chèn được cấu hình vào module) có một proxy SSRF đọc trọn
# thân tới loopback/metadata/LAN, và `ffmpeg -i` nhận scheme tuỳ ý
# (file:/concat:/http:). Lớp mới theo nguyên tắc «kiểm một lần ở biên, dùng
# địa chỉ đã kiểm»:
#   1. Bản ghi chỉ vào được bảng của dịch vụ qua kiem_ban_ghi (đăng ký):
#      kind đóng {go2rtc, rtsp}, scheme đóng (base http/https, url rtsp),
#      host phân giải ra địa chỉ RIÊNG (loopback của chính container cũng
#      chặn — nó không bao giờ là camera/go2rtc).
#   2. Đạt kiểm thì GHIM IP đã kiểm vào bản ghi (base → ``_base_ghim``,
#      url/url_ai viết lại theo IP) — lúc gọi không còn phụ thuộc DNS
#      (chống rebinding), và ffmpeg chỉ nhận URL từ bảng đã ghim này.
#   3. ffmpeg RTSP luôn có -protocol_whitelist rtsp,tcp,udp,rtp.

def _cho_loopback_de_test() -> bool:
    """Chỉ test bật (go2rtc giả chạy trên 127.0.0.1) — khuôn
    ``WEB2API_TEST_CHO_PHEP_LOOPBACK`` của app/tu_xa trong lõi."""
    return os.environ.get("CAM_TEST_CHO_PHEP_LOOPBACK") == "1"


def _dia_chi_dv_bi_chan(ip) -> str | None:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_link_local:
        return "link-local / metadata đám mây (169.254.0.0/16)"
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return "địa chỉ không định tuyến"
    if ip.is_loopback:
        return None if _cho_loopback_de_test() else "loopback (chính container dịch vụ)"
    if ip.is_private:
        return None
    return "địa chỉ công khai — camera/go2rtc chỉ ở mạng nhà riêng"


def _kiem_va_ghim(url: str, schemes: tuple[str, ...]) -> tuple[str | None, str | None]:
    """Kiểm một URL rồi trả ``(lỗi, url_đã_ghim_IP)`` — giữ nguyên
    userinfo/đường/cổng, chỉ thay host bằng IP đầu tiên đã kiểm."""
    try:
        p = urlsplit(url)
    except ValueError as exc:
        return f"không phân tích được ({exc})", None
    if p.scheme.lower() not in schemes:
        return f"scheme phải là {'/'.join(schemes)}", None
    if not p.hostname:
        return "thiếu địa chỉ máy chủ", None
    try:
        ds = [ipaddress.ip_address(p.hostname)]
    except ValueError:
        try:
            thong_tin = socket.getaddrinfo(p.hostname, None, type=socket.SOCK_STREAM)
        except OSError:
            return "không phân giải được tên máy", None
        ds = list({ipaddress.ip_address(x[4][0]) for x in thong_tin})
    if not ds:
        return "không phân giải được tên máy", None
    for ip in ds:
        ly_do = _dia_chi_dv_bi_chan(ip)
        if ly_do:
            return f"địa chỉ bị chặn ({ly_do})", None
    ip = ds[0]
    host_moi = f"[{ip}]" if ip.version == 6 else str(ip)
    netloc = p.netloc
    if "@" in netloc:
        tien, hostport = netloc.rsplit("@", 1)
        tien += "@"
    else:
        tien, hostport = "", netloc
    if hostport.startswith("["):
        phan_cong = hostport[hostport.index("]") + 1:]
    elif ":" in hostport:
        phan_cong = ":" + hostport.split(":", 1)[1]
    else:
        phan_cong = ""
    return None, urlunsplit((p.scheme, tien + host_moi + phan_cong,
                             p.path, p.query, p.fragment))


def kiem_ban_ghi(cam) -> str | None:
    """Kiểm bản ghi camera LÚC ĐĂNG KÝ. Đạt thì ghim IP vào chính bản ghi;
    trả câu lỗi (không chứa URL/mật khẩu) hoặc None."""
    if not isinstance(cam, dict):
        return "bản ghi camera phải là một đối tượng"
    ten = cam.get("name")
    if not isinstance(ten, str) or not ten.strip():
        return "thiếu name"
    kind = cam.get("kind")
    if kind not in KIEU_HOP_LE:
        return "kind phải là go2rtc hoặc rtsp"
    if kind == "go2rtc":
        base = str(cam.get("base") or "")
        if not base:
            return "thiếu base"
        try:
            if urlsplit(base).username or urlsplit(base).password:
                return "base không được chứa tài khoản/mật khẩu (khai ở trường username/password)"
        except ValueError as exc:
            return f"base không phân tích được ({exc})"
        loi, ghim = _kiem_va_ghim(base, ("http", "https"))
        if loi:
            return f"base: {loi}"
        cam["_base_ghim"] = ghim
        for khoa in ("src", "src_ai"):
            val = str(cam.get(khoa) or "").strip()
            if khoa == "src" and not val:
                return "thiếu src"
            if not val:
                continue
            if khoa == "src_ai" and val.lower().startswith("rtsp://"):
                loi, ghim2 = _kiem_va_ghim(val, ("rtsp",))
                if loi:
                    return f"src_ai: {loi}"
                cam["src_ai"] = ghim2
            elif "://" in val:
                return f"{khoa} là URL lạ — chỉ nhận tên luồng go2rtc hoặc rtsp:// (ở src_ai)"
            elif any(c.isspace() for c in val) or '"' in val or "'" in val:
                return f"{khoa} chứa ký tự lạ"
    else:
        for khoa in ("url", "url_ai"):
            val = str(cam.get(khoa) or "").strip()
            if khoa == "url" and not val:
                return "thiếu url"
            if val:
                loi, ghim = _kiem_va_ghim(val, ("rtsp",))
                if loi:
                    return f"{khoa}: {loi}"
                cam[khoa] = ghim
    for khoa in ("username", "password"):
        if cam.get(khoa) is not None and not isinstance(cam[khoa], str):
            return f"{khoa} phải là chuỗi"
    if cam.get("cho_loa") is not None and not isinstance(cam.get("cho_loa"), bool):
        return "cho_loa phải là true/false"
    return None


def _base_hieu_luc(cam: dict) -> str:
    """Địa chỉ go2rtc ĐÃ GHIM lúc đăng ký (dự phòng: base gốc cho lời gọi
    nội bộ cũ chưa qua đăng ký — vẫn là địa chỉ quản trị đã khai)."""
    return str(cam.get("_base_ghim") or cam.get("base") or "").rstrip("/")


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
    base = _base_hieu_luc(cam)
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
             # -protocol_whitelist đóng chặt giao thức nạp (C2): thiếu nó thì
             # scheme/đường lồng (file:, concat:, http:) vẫn lọt tới ffmpeg.
             "-protocol_whitelist", "rtsp,tcp,udp,rtp",
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
        u = urlsplit(_base_hieu_luc(cam))
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
    """Bóc một khung từ bản ghi đã chốt luồng.

    Kind ĐÓNG (C2): bản cũ rơi mọi kind ≠ go2rtc xuống đường RTSP — URL lạ
    hoá thành đầu vào ffmpeg. Kind lạ phải lỗi rõ, không gọi gì cả.
    """
    if cam.get("kind") == "go2rtc":
        return _chup_go2rtc(cam, timeout)
    if cam.get("kind") == "rtsp":
        return _chup_rtsp(cam, timeout)
    raise LoiCamera("kind camera không hợp lệ (chỉ go2rtc/rtsp).")


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
