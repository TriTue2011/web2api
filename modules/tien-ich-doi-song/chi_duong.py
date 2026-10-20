"""Chỉ đường / tra địa điểm — hoàn toàn MIỄN PHÍ, không API key.

Port từ kho CỦA CHÍNH CHỦ MÁY (LUẬT PORT 19/10, chỉ chép không viết lại):
  nguồn: /opt/claude-c2a/chatgpt2api/services/chi_duong.py
  commit: d879b03 (cây CHỈ ĐỌC, không sửa c2a)

Đường dùng:
  * geocode chính thức: Nominatim (OpenStreetMap) — `search` + `reverse`.
  * định tuyến chính thức: OSRM demo (router.project-osrm.org).
  * link mở được → Google Maps URL dựng từ TOẠ ĐỘ (Maps URLs, không cần key).

Chỉnh so với c2a (chỉ đổi lớp kết nối/khuôn module):
  - `requests` → `httpx` (gói khai ở yeu_cau_goi của manifest).
  - KHÔNG port đường bóc endpoint nội bộ Google Maps của c2a (`_gmaps_pb`,
    `_boc_pb`, `_boc_pb_nhieu`, `_doc_gmaps_pb`, `_gmaps_pb_nhieu`) — đó là
    CÀO DỮ LIỆU không chính thức (endpoint `pb` không công khai + curl_cffi giả
    mạo TLS). Kiểm «cần chủ máy quyết»; ở đây geocode chỉ dùng Nominatim.
  - `_giai_link_maps`: chỉ giải link rút gọn do NGƯỜI DÙNG DÁN khi đích cuối
    vẫn là tên máy Google Maps; validate host đích, tối đa 3 bước chuyển hướng.
  - KHÔNG log địa chỉ/vị trí của người dùng (dữ liệu nhạy cảm) — chỉ log sự cố
    mạng chung.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlencode, urlparse

import httpx

log = logging.getLogger("web2api.tien_ich_doi_song.chi_duong")

_UA = "web2api-tien-ich/1.0 (trợ lý cá nhân; Nominatim/OSRM công khai)"
_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"
_OSRM = "https://router.project-osrm.org/route/v1"
_TIMEOUT = 15

#: Tên phương tiện tiếng Việt (đã hạ chữ thường, bỏ dấu để so linh hoạt ở
#: `chuan_phuong_tien`) → (osrm_profile | "", gmaps_travelmode, có_route_text).
_PHUONG_TIEN = {
    "xe may": ("driving", "driving", True),
    "o to": ("driving", "driving", True),
    "oto": ("driving", "driving", True),
    "di bo": ("walking", "walking", True),
    "xe dap": ("cycling", "bicycling", True),
    "xe buyt": ("", "transit", False),
    "bus": ("", "transit", False),
}

#: Các phương tiện chào ra menu (thứ tự hiển thị).
MENU_PHUONG_TIEN = ["xe máy", "ô tô", "đi bộ", "xe buýt"]

_MANEUVER_VI = {
    "turn": "Rẽ", "new name": "Đi tiếp vào", "depart": "Xuất phát trên",
    "arrive": "Tới nơi", "merge": "Nhập vào", "on ramp": "Lên đường nhánh",
    "off ramp": "Xuống đường nhánh", "fork": "Rẽ nhánh", "end of road": "Cuối đường rẽ vào",
    "continue": "Đi thẳng theo", "roundabout": "Vào vòng xuyến", "rotary": "Vào vòng xuyến",
    "roundabout turn": "Ở vòng xuyến rẽ vào", "notification": "Tiếp tục trên",
}
_MODIFIER_VI = {
    "left": "trái", "right": "phải", "straight": "thẳng",
    "slight left": "chếch trái", "slight right": "chếch phải",
    "sharp left": "gấp trái", "sharp right": "gấp phải", "uturn": "quay đầu",
}


def _bo_dau(s: str) -> str:
    b = "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"
    k = "aaaaaaaaaaaaaaaaaeeeeeeeeeeeiiiiiooooooooooooooooouuuuuuuuuuuyyyyyd"
    return (s or "").lower().translate(str.maketrans(b, k))


#: Tiền tố đơn vị TỈNH/THÀNH ở đoạn cuối địa chỉ.
_TIEN_TO_TINH = ("thanh pho ", "tinh ", "tp ", "tp.", "t.p ", "t.p.")


def _bo_tinh_tp(q: str) -> str:
    """Bỏ đoạn TỈNH/THÀNH khỏi truy vấn (giữ quận/phường/landmark). Giữ nguyên
    logic của c2a (dùng cho đường tìm địa điểm/Nominatim)."""
    segs = [s.strip() for s in str(q or "").split(",")]
    giu = [s for s in segs
           if s and not any(_bo_dau(s).startswith(p) for p in _TIEN_TO_TINH)]
    return ", ".join(giu) if giu else str(q or "")


def _tinh_ngan(s: str) -> str:
    """"Thành phố Hà Nội" → "Hà Nội" (giữ dấu, bỏ tiền tố đơn vị)."""
    s = str(s or "").strip()
    fl = _bo_dau(s)
    for pre in ("thanh pho ", "tinh ", "tp ", "tp."):
        if fl.startswith(pre):
            return s[len(pre):].strip()
    return s


def chuan_phuong_tien(pt: str) -> tuple[str, str, bool]:
    """Tên phương tiện tự do → (osrm_profile, gmaps_travelmode, có_route_text).

    Không nhận ra → mặc định xe máy (phổ biến nhất ở VN)."""
    key = " ".join(_bo_dau(pt).split())
    return _PHUONG_TIEN.get(key, _PHUONG_TIEN["xe may"])


def maps_link(diem_di: str, diem_den: str, travelmode: str = "driving") -> str:
    """URL Google Maps mở sẵn tuyến đường — KHÔNG cần API key (Maps URLs)."""
    q = urlencode({
        "api": 1,
        "origin": diem_di,
        "destination": diem_den,
        "travelmode": travelmode,
    })
    return f"https://www.google.com/maps/dir/?{q}"


def maps_search_link(lat: float, lon: float) -> str:
    """URL ghim một điểm (Maps URLs, query=lat,lon) — giữ nguyên chính xác ghim."""
    def _toa(v: float) -> str:
        return f"{float(v):.7f}".rstrip("0").rstrip(".")

    q = urlencode({"api": 1, "query": f"{_toa(lat)},{_toa(lon)}"})
    return f"https://www.google.com/maps/search/?{q}"


#: Chữ ĐỆM đứng trước mã toà/số nhà, bỏ khi tra khái quát (viết KHÔNG dấu).
_DEM_DAU = {"toa", "nha", "so", "can", "ho", "phong", "chung", "cu", "khu"}


def _bo_ma_toa(dc: str) -> str:
    """Bỏ mã toà nhà + chữ đệm đứng ĐẦU địa chỉ, GIỮ dấu phẩy còn lại.

    Nominatim thường thiếu từng toà nhưng CÓ khu đô thị/phố, và nó KÉN dấu phẩy.
    Mã toà = token vừa có CHỮ vừa có SỐ. (Logic giữ nguyên của c2a.)"""
    def bo_qua(t: str) -> bool:
        f = _bo_dau(t)
        la_ma = any(c.isdigit() for c in t) and any(c.isalpha() for c in t)
        return f in _DEM_DAU or la_ma

    segs = [s.strip() for s in dc.split(",") if s.strip()]
    if not segs:
        return ""
    toks = segs[0].split()
    i = 0
    while i < len(toks) and bo_qua(toks[i]):
        i += 1
    segs[0] = " ".join(toks[i:]).strip()
    return ", ".join(s for s in segs if s)


#: URL Google Maps (link chia sẻ rút gọn hoặc link đầy đủ).
_RE_MAPS_URL = re.compile(
    r"https?://(maps\.app\.goo\.gl|(www\.)?google\.[a-z.]+/maps|goo\.gl/maps)/", re.I)
_RE_TOA_DO_MAPS = re.compile(
    r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*")

#: Host được phép khi giải link rút gọn Maps (đích cuối phải vẫn là Google).
_HOST_MAPS = ("google.com", "google.com.vn", "maps.app.goo.gl", "goo.gl")


def _toa_do_tu_url_maps(raw: str) -> tuple[float, float] | None:
    """Lấy tọa độ từ URL Maps canonical, không gọi mạng."""
    try:
        parsed = urlparse(raw)
        queries = parse_qs(parsed.query)
        for value in queries.get("query") or queries.get("q") or []:
            m = _RE_TOA_DO_MAPS.fullmatch(value)
            if m:
                return float(m.group(1)), float(m.group(2))
        m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", raw)
        if m:
            return float(m.group(1)), float(m.group(2))
    except (TypeError, ValueError):
        pass
    return None


def _truy_van_tu_link_maps(raw: str) -> str:
    """URL `/maps/search/?query=<chữ>` → chữ cần tìm, không coi là ghim."""
    try:
        parsed = urlparse(raw)
        if "/maps/search" not in parsed.path.lower():
            return raw
        queries = parse_qs(parsed.query)
        for value in queries.get("query") or queries.get("q") or []:
            if value.strip() and not _RE_TOA_DO_MAPS.fullmatch(value):
                return value.strip()
    except (TypeError, ValueError):
        pass
    return raw


def _host_maps_ok(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except (TypeError, ValueError):
        return False
    return any(host == h or host.endswith("." + h) for h in _HOST_MAPS)


def _giai_link_maps(s: str) -> tuple[float, float] | str | None:
    """Link Google Maps → toạ độ (lat, lon) hoặc chuỗi ĐỊA CHỈ; None nếu không phải link.

    Người dùng hay chỉ vị trí bằng cách CHIA SẺ ghim Google Maps (maps.app.goo.gl).
    Theo redirect tới link đầy đủ rồi lấy `@lat,lng` (chính xác nhất) hoặc tên
    trong `/place/<địa chỉ>`. An toàn: tối đa 3 bước, đích cuối PHẢI vẫn là tên
    máy Google Maps (host khác → bỏ, không moi nội dung lạ)."""
    raw = str(s or "").strip()
    if not _RE_MAPS_URL.search(raw):
        return None
    # URL đầy đủ có sẵn toạ độ/truy vấn → đọc ngay, không gọi mạng.
    toa_do = _toa_do_tu_url_maps(raw)
    if toa_do:
        return toa_do
    truy_van = _truy_van_tu_link_maps(raw)
    if truy_van != raw:
        return truy_van
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True,
                          max_redirects=3) as client:
            r = client.get(raw, headers={"User-Agent": _UA})
        url = str(r.url)
    except Exception:
        log.warning("chi_duong: giải link maps lỗi (mạng)")
        return None
    if not _host_maps_ok(url):
        return None
    m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"/place/([^/@?]+)", url)
    if m:
        return unquote_plus(m.group(1)).strip()
    return None


def la_ghim_maps(dia_chi: str) -> bool:
    """Có phải một ghim Maps do người dùng đã chỉ rõ hay không.

    Link chia sẻ ngắn / `place` và URL có tọa độ là một vị trí người dùng đã
    chọn. URL ``/maps/search/?query=<chữ>`` chỉ là truy vấn mơ hồ, vẫn phải ra
    menu; nếu không nó lại lách yêu cầu không tự chọn top-1."""
    raw = str(dia_chi or "").strip()
    if not _RE_MAPS_URL.search(raw):
        return False
    if _toa_do_tu_url_maps(raw):
        return True
    try:
        parsed = urlparse(raw)
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        return host in {"maps.app.goo.gl", "goo.gl"} or "/maps/place/" in path
    except (TypeError, ValueError):
        return False


def _nominatim_1(q: str) -> tuple[float, float, str, str] | None:
    """Một lượt Nominatim → (lat, lon, tên, TỈNH) hoặc None.

    `addressdetails=1` để lấy TỈNH/THÀNH: dùng đối chiếu hai đầu tuyến."""
    out = _nominatim_nhieu(q, so=1)
    return out[0] if out else None


def _nominatim_nhieu(q: str, so: int = 8) -> list[tuple[float, float, str, str]]:
    """Các kết quả Nominatim cho một truy vấn."""
    try:
        r = httpx.get(
            _NOMINATIM,
            params={"q": q, "format": "json", "limit": max(1, min(int(so), 8)),
                    "addressdetails": 1},
            headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception:
        log.warning("chi_duong.tim_dia_diem: Nominatim lỗi (mạng)")
        return []
    out: list[tuple[float, float, str, str]] = []
    for item in data if isinstance(data, list) else []:
        try:
            ad = item.get("address") or {}
            out.append((
                float(item["lat"]), float(item["lon"]),
                str(item.get("display_name") or q),
                str(ad.get("city") or ad.get("state") or ad.get("province") or "").strip(),
            ))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return out


def tim_dia_diem(dia_chi: str, so: int = 8) -> list[dict[str, Any]]:
    """Tìm địa điểm theo chuỗi tự do → danh sách lựa chọn {lat, lon, ten, tinh, link}.

    Không tự chọn kết quả đầu khi mơ hồ — người gọi bày danh sách cho người
    dùng chọn (ghim Maps do người dùng dán thì `la_ghim_maps` quyết riêng)."""
    dc = str(dia_chi or "").strip()
    if not dc:
        return []
    giai = _giai_link_maps(dc)
    if isinstance(giai, tuple):
        return [{"lat": giai[0], "lon": giai[1],
                 "ten": _ten_tu_toa_do(giai[0], giai[1]) or "Vị trí đã ghim trên Google Maps",
                 "tinh": "", "link": maps_search_link(giai[0], giai[1])}]
    if isinstance(giai, str) and giai:
        dc = giai
    kq = _nominatim_nhieu(dc, so=so)
    if not kq:
        ngan = _bo_ma_toa(dc)
        if ngan and ngan != dc:
            kq = _nominatim_nhieu(ngan, so=so)
    return [{"lat": lat, "lon": lon, "ten": ten, "tinh": tinh,
             "link": maps_search_link(lat, lon)} for lat, lon, ten, tinh in kq]


def _ten_tu_toa_do(lat: float, lon: float) -> str:
    """Toạ độ → địa chỉ chữ (Nominatim reverse), để người dùng XÁC NHẬN ghim."""
    try:
        r = httpx.get(
            _NOMINATIM_REVERSE,
            params={"format": "jsonv2", "lat": f"{lat:.7f}", "lon": f"{lon:.7f}",
                    "accept-language": "vi", "zoom": "18"},
            headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        if r.status_code != 200:
            return ""
        ten = str((r.json() or {}).get("display_name") or "").strip()
    except Exception:
        return ""
    phan = [x.strip() for x in ten.split(",") if x.strip()]
    while phan and (phan[-1].lower() in {"việt nam", "vietnam"} or phan[-1].isdigit()):
        phan.pop()
    return ", ".join(phan)


def geocode(dia_chi: str, tinh_goi_y: str = "") -> tuple[float, float, str, str, bool] | None:
    """Địa chỉ → (lat, lon, tên, tỉnh, chính_xác); None nếu không thấy.

    Đường chính thức: Nominatim (c2a dùng endpoint Google nội bộ làm đường
    chính — không port vì là cào dữ liệu; ở đây Nominatim là đường duy nhất).
    ``tinh_goi_y``: tỉnh/thành của ĐẦU KIA để bù khi câu thiếu thành phố.
    ``chính_xác`` = True khi khớp nguyên văn; False khi phải BỎ MÃ TOÀ mới ra."""
    dc = str(dia_chi or "").strip()
    if not dc:
        return None
    giai = _giai_link_maps(dc)
    if isinstance(giai, tuple):
        ten_ghim = _ten_tu_toa_do(giai[0], giai[1]) or "Vị trí đã ghim trên Google Maps"
        return giai[0], giai[1], ten_ghim, "", True
    if isinstance(giai, str) and giai:
        dc = giai
    fdc = _bo_dau(dc)
    hau_to = "" if ("viet nam" in fdc or "vietnam" in fdc) else ", Việt Nam"
    if tinh_goi_y and _bo_dau(tinh_goi_y) not in fdc:
        hau_to = f", {tinh_goi_y}" + hau_to
    bien_the: list[tuple[str, bool]] = [(dc, True)]
    ngan = _bo_ma_toa(dc)
    if ngan and ngan != dc and len(ngan.split()) >= 2:
        bien_the.append((ngan, False))
    for q, chinh_xac in bien_the:
        kq = _nominatim_1(q + hau_to)
        if kq:
            return (*kq, chinh_xac)
    return None


#: Chỉ các bước RẼ thật mới gắn hướng (trái/phải); depart/arrive/new name…
#: gắn hướng vào đọc ngược.
_CO_HUONG = {"turn", "end of road", "fork", "roundabout turn", "on ramp", "off ramp"}


def _mo_ta_buoc(step: dict) -> str:
    """Một bước OSRM → câu chỉ dẫn tiếng Việt ngắn."""
    m = step.get("maneuver") or {}
    loai = str(m.get("type") or "")
    kieu = _MANEUVER_VI.get(loai, "Đi tiếp")
    huong = _MODIFIER_VI.get(str(m.get("modifier") or ""), "") if loai in _CO_HUONG else ""
    ten = str(step.get("name") or "").strip()
    met = step.get("distance") or 0
    phan = kieu + (f" {huong}" if huong else "")
    if ten:
        phan += f" {ten}"
    if met and met >= 1:
        phan += f" (~{int(met)} m)" if met < 1000 else f" (~{met/1000:.1f} km)"
    return phan


def _chuan_tinh(s: str) -> str:
    """Chuẩn hoá tên tỉnh/thành để so khớp: bỏ dấu + bỏ tiền tố loại đơn vị."""
    f = _bo_dau(s).strip()
    for pre in ("thanh pho ", "tinh ", "tp. ", "tp.", "tp ", "t.p ", "t."):
        if f.startswith(pre):
            f = f[len(pre):].strip()
            break
    return f


def _dinh_vi_hai_dau(diem_di: str, diem_den: str):
    """Geocode hai đầu (bù tỉnh liên đầu) → (a, b, ly_do).

    ``ly_do`` None nếu ổn; ngược lại "khong_ra_diem_den"/"khong_ra_diem_di"/
    "tinh_khong_khop". Dùng chung cho `dinh_vi` và `chi_duong`."""
    b = geocode(diem_den)
    if not b:
        return None, None, "khong_ra_diem_den"
    a = geocode(diem_di, tinh_goi_y=b[3])
    if not a:
        return None, None, "khong_ra_diem_di"
    if a[3] and b[3] and _chuan_tinh(a[3]) != _chuan_tinh(b[3]):
        return a, b, "tinh_khong_khop"
    return a, b, None


def _link_tu(a, b, diem_di: str, diem_den: str, travelmode: str) -> str:
    """Deep-link Google Maps ưu tiên TOẠ ĐỘ đã geocode (mở chỉ đường chắc chắn)."""
    origin = f"{a[0]},{a[1]}" if a else diem_di
    dest = f"{b[0]},{b[1]}" if b else diem_den
    return maps_link(origin, dest, travelmode)


def dinh_vi(diem_di: str, diem_den: str, phuong_tien: str = "xe máy") -> dict:
    """Chỉ GEOCODE (không định tuyến) — cho bước XÁC NHẬN địa chỉ."""
    profile, travelmode, co_route = chuan_phuong_tien(phuong_tien)
    goc = {"phuong_tien": phuong_tien}
    if not co_route or not profile:
        a, b, _ = _dinh_vi_hai_dau(diem_di, diem_den)
        return {"ok": False, "ly_do": "khong_dinh_tuyen",
                "link": _link_tu(a, b, diem_di, diem_den, travelmode), **goc}
    a, b, ly = _dinh_vi_hai_dau(diem_di, diem_den)
    link = _link_tu(a, b, diem_di, diem_den, travelmode)
    if ly == "tinh_khong_khop":
        return {"ok": False, "ly_do": ly, "tinh_di": a[3], "tinh_den": b[3],
                "link": link, **goc}
    if ly:
        return {"ok": False, "ly_do": ly, "link": link, **goc}
    return {"ok": True, "tu": a[2], "den": b[2],
            "gan_dung": not (a[4] and b[4]), "link": link, **goc}


def chi_duong(diem_di: str, diem_den: str, phuong_tien: str = "xe máy") -> dict:
    """A→B → dict chỉ đường. KHÔNG raise; mọi lỗi thành `ok=False` + `ly_do`.

    ``ok=True``  → {km, phut, buoc:[str], link, tu, den, phuong_tien, gan_dung}
    ``ok=False`` → {ly_do, link, phuong_tien, [tinh_di, tinh_den]}"""
    profile, travelmode, co_route = chuan_phuong_tien(phuong_tien)
    goc = {"phuong_tien": phuong_tien}

    # Xe buýt: OSRM không làm transit → chỉ trả link, Google Maps lo tuyến bus.
    if not co_route or not profile:
        a, b, _ = _dinh_vi_hai_dau(diem_di, diem_den)
        return {"ok": False, "ly_do": "khong_dinh_tuyen",
                "link": _link_tu(a, b, diem_di, diem_den, travelmode), **goc}

    a, b, ly = _dinh_vi_hai_dau(diem_di, diem_den)
    link = _link_tu(a, b, diem_di, diem_den, travelmode)
    if ly == "tinh_khong_khop":
        return {"ok": False, "ly_do": ly, "tinh_di": a[3], "tinh_den": b[3],
                "link": link, **goc}
    if ly:
        return {"ok": False, "ly_do": ly, "link": link, **goc}

    # OSRM: kinh độ,vĩ độ;kinh độ,vĩ độ (LƯU Ý thứ tự lon,lat).
    toa_do = f"{a[1]},{a[0]};{b[1]},{b[0]}"
    try:
        r = httpx.get(
            f"{_OSRM}/{profile}/{toa_do}",
            params={"overview": "false", "steps": "true"},
            headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception:
        log.warning("chi_duong.osrm lỗi (mạng)")
        return {"ok": False, "ly_do": "khong_dinh_tuyen", "link": link, **goc}

    if data.get("code") != "Ok" or not data.get("routes"):
        return {"ok": False, "ly_do": "khong_dinh_tuyen", "link": link, **goc}

    route = data["routes"][0]
    steps = (route.get("legs") or [{}])[0].get("steps") or []
    buoc = [_mo_ta_buoc(s) for s in steps if s.get("maneuver")]
    return {
        "ok": True,
        "km": round(route.get("distance", 0) / 1000, 1),
        "phut": max(1, round(route.get("duration", 0) / 60)),
        "buoc": buoc,
        "link": link,
        "tu": a[2],
        "den": b[2],
        # Một đầu chỉ ra ở mức khu đô thị/phố (bỏ mã toà) → km GẦN ĐÚNG.
        "gan_dung": not (a[4] and b[4]),
        "phuong_tien": phuong_tien,
    }
