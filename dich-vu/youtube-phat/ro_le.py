"""Rơ-le luồng chịu lỗi cho dịch vụ youtube-phat (NV153 đợt A).

VIẾT LẠI phần ``_noi_cac_khuc`` trong c2a ``api/youtube_phat.py`` (dòng
478-498, commit d879b03) để sửa bug cắt luồng im lặng.

Bug gốc
-------
Rơ-le đọc từng khúc 4 MiB từ thượng nguồn (googlevideo). Trong bản c2a::

    try:
        while True:
            try:
                while mieng := mo.read(64 * 1024):
                    vi_tri += len(mieng)
                    yield mieng
            finally:
                mo.close()
            if cuoi is None or vi_tri > cuoi:
                return
            ke = min(vi_tri + KHUC_LUONG - 1, cuoi)
            mo = _mo_khuc(resolved, vi_tri, ke)   # <-- NẰM TRONG try
    except (OSError, HTTPError):
        return                                    # <-- im lặng: loa thấy đứt

Khi lỗi xảy ra giữa khúc HOẶC khi mở khúc kế (lưu ý ``mo = _mo_khuc(...)``
nằm trong ``try``), c2a thoát im lặng → loa thấy luồng đứt. Đo thật: đọc
chậm 17 KB/s thì cắt ở 467s/486s, đúng 131.072 hoặc 458.752 byte sau ranh
giới khúc 4 MiB. Ngoài ra c2a chỉ giải lại link đúng một lần cho khúc ĐẦU;
khúc sau gặp 403/404/410 thì chết im.

Khác biệt của bản viết lại
--------------------------
1. Mọi lỗi mở/đọc đều được nối lại tối đa 5 lần với backoff ``CHO_NOI_LAI``
   thay vì ``return`` im lặng.
2. Mở lại từ ĐÚNG byte đã gửi (``da_gui``) — không mất, không trùng byte.
3. 403/404/410 khi mở (khúc đầu hay khúc sau) → gọi ``giai_lai_link()``
   (forget_stream + resolve lại) trước khi thử lại.
4. Kiểm bản mỗi lần mở: mã 206 (hoặc 200 khi xin từ đầu và thượng nguồn trả
   200), ``Content-Range`` bắt đầu ĐÚNG offset đã xin, tổng KHỚP tổng đã
   biết. Bản sai → tính 1 lần thất bại, KHÔNG yield byte nào của nó.
5. Học tổng byte từ ``Content-Range`` khúc đầu → biết điểm kết thúc thật;
   đọc rỗng sớm khi chưa đủ tổng → coi như đứt, nối lại (chống cắt im).
   416 (Range Not Satisfiable) → coi như hết luồng, kết thúc sạch.
6. Yield từng miếng 64 KiB ngay khi đọc (áp lực ngược tự nhiên qua
   ``yield``); KHÔNG tích cả khúc 4 MiB vào bộ nhớ.
7. Client ngắt (``GeneratorExit`` tại ``yield`` hoặc khi đang chờ I/O) →
   return IM LẶNG: không log, không nối lại, không gọi thượng nguồn nữa.
   ``kiem_dung()`` True → dừng hẳn trước mỗi lần nối lại (quy tắc
   «dừng là dừng hẳn» #4: không ngủ, không mở lại, không request thêm).
8. Thống kê tích luỹ ở mức module (``_DEM`` / ``lay_thong_ke()`` /
   ``dat_lai_thong_ke()``) và log sự kiện qua ``ghi_log`` (JSON một dòng):
   ``luong_dut_noi_lai`` / ``luong_that_bai`` / ``luong_hoan_tat``.
   KHÔNG truyền token/URL ký/Authorization vào ``ghi_log``.
9. KHÔNG phụ thuộc FastAPI/Starlette — chỉ ``asyncio``, ``time``,
   ``logging`` (log nội bộ ở mức debug, không chứa bí mật); test được độc
   lập bằng fake cho ``mo_khuc``.

Hợp đồng ``mo_khuc`` (do ``server.py`` cung cấp)
-----------------------------------------------
``mo_khuc`` là ``async (tu: int, den: int) -> obj``; ``obj`` có::

    .read(n) -> bytes          (được phép là coroutine — fake sync/async đều được)
    .headers                  (dict-like, có Content-Range khi 206)
    .close()                  (đồng bộ theo hợp đồng)
    .ma_trang_thai -> int      (200 / 206 / 416 / ...)
    .loai_loi_403_410 -> bool (True nếu HTTPError 403/404/410 khi mở)

Khi mở lỗi, ``mo_khuc`` NÉM ``OSError`` (mất mạng/timeout) hoặc một đối
tượng lỗi có ``.ma_trang_thai`` / ``.loai_loi_403_410``.

Ghi chú về ``CHO_NOI_LAI``: lần thất bại thứ 5 thì raise ngay, không ngủ —
nên phần tử cuối của tuple hiện là dự phòng, chưa dùng tới.
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

__all__ = [
    "KHUC",
    "MIENG_DOC",
    "CHO_NOI_LAI",
    "LoiNguonKhongCuuDuoc",
    "LoiMoKhuc",
    "luong_chiu_loi",
    "lay_thong_ke",
    "dat_lai_thong_ke",
]

KHUC = 4 * 1024 * 1024
MIENG_DOC = 64 * 1024
CHO_NOI_LAI = (0.5, 1.0, 2.0, 2.0, 2.0)  # giây; tối đa 5 lần nối lại/luồng

_MA_CAN_GIAI_LAI_LINK = frozenset({403, 404, 410})


class LoiNguonKhongCuuDuoc(Exception):
    """Hết 5 lần nối lại — server.py bắt và NGẮT luồng."""


class LoiMoKhuc(Exception):
    """Lỗi logic khi mở/nhận khúc: bản không hợp lệ, hết sớm, obj lỗi.

    Đặt ``ma_trang_thai`` trong {403, 404, 410} (hoặc
    ``loai_loi_403_410=True``) khi nguyên nhân là link hết hạn để vòng
    nối lại biết phải gọi ``giai_lai_link()`` trước khi thử.
    """

    def __init__(self, ly_do, *, ma_trang_thai=None, loai_loi_403_410=False):
        super().__init__(ly_do)
        self.ma_trang_thai = ma_trang_thai
        self.loai_loi_403_410 = bool(loai_loi_403_410) or (
            ma_trang_thai in _MA_CAN_GIAI_LAI_LINK
        )


_DEM = {"luong": 0, "lan_noi_lai": 0, "that_bai": 0, "byte": 0}


def lay_thong_ke():
    """Trả bản sao thống kê tích luỹ ở mức module."""
    return dict(_DEM)


def dat_lai_thong_ke():
    """Đặt lại thống kê về 0 (cho test)."""
    for khoa in _DEM:
        _DEM[khoa] = 0


async def _cho(ket_qua):
    """Await kết quả nếu nó là awaitable (fake sync lẫn async đều chạy)."""
    if hasattr(ket_qua, "__await__"):
        return await ket_qua
    return ket_qua


def _can_giai_lai_link(exc):
    """True nếu lỗi thuộc nhóm 403/404/410 → phải giải lại link."""
    if getattr(exc, "loai_loi_403_410", False):
        return True
    return getattr(exc, "ma_trang_thai", None) in _MA_CAN_GIAI_LAI_LINK


def _dau_content_range(headers):
    """Offset bắt đầu từ header Content-Range "bytes a-b/T"; None nếu thiếu/sai."""
    try:
        cr = headers.get("Content-Range") or headers.get("content-range")
    except AttributeError:
        return None
    if not cr or not cr.startswith("bytes "):
        return None
    try:
        pham_vi = cr[len("bytes "):].split("/", 1)[0]
        return int(pham_vi.split("-", 1)[0])
    except (ValueError, IndexError):
        return None


def _ban_hop_le(ma, headers, tu):
    """Kiểm bản vừa mở. Trả (hop_le, ly_do); bản sai thì không yield."""
    if ma == 200 and tu == 0:
        # Xin từ đầu, thượng nguồn trả cả tệp: đọc tới hết phản hồi.
        return True, ""
    if ma != 206:
        return False, f"ma_trang_thai={ma}"
    dau_cr = _dau_content_range(headers)
    if dau_cr is None:
        return False, "thiếu Content-Range"
    if dau_cr != tu:
        return False, f"Content-Range bắt đầu ở {dau_cr}, đã xin {tu}"
    return True, ""


async def _dong(ban):
    """Đóng bản thượng nguồn; lỗi close không được giết luồng."""
    if ban is None:
        return
    try:
        await _cho(ban.close())
    except Exception:
        logger.debug("đóng bản thượng nguồn lỗi (bỏ qua)", exc_info=True)


async def luong_chiu_loi(mo_khuc, dau, cuoi, *, giai_lai_link, doc_tong, ghi_log,
                         kiem_dung=None, ngu=None):
    """Yield bytes từ ``dau`` đến ``cuoi`` (None = tới hết), tự nối lại khi đứt.

    - ``mo_khuc``: async ``(tu, den) -> obj`` (xem hợp đồng ở docstring module).
    - ``giai_lai_link``: async ``() -> None`` (forget_stream + resolve lại).
    - ``doc_tong``: ``(headers) -> int | None`` — tổng byte từ Content-Range
      "bytes a-b/TỔNG".
    - ``ghi_log``: ``(su_kien: str, **truong) -> None`` — ghi JSON một dòng;
      KHÔNG truyền token/URL ký/Authorization vào đây.
    - ``kiem_dung``: async ``() -> bool | None`` — True = client đã ngắt HOẶC
      dịch vụ đang dừng → DỪNG NGAY, không nối lại, không thêm request
      thượng nguồn.
    - ``ngu``: async ``(giay) -> None`` (mặc định ``asyncio.sleep``; test
      truyền bản giả).

    Raise ``LoiNguonKhongCuuDuoc`` khi hết 5 lần nối lại. Client ngắt
    (``GeneratorExit``) → return im lặng.
    """
    bat_dau = time.monotonic()
    _ngu = asyncio.sleep if ngu is None else ngu
    da_gui = dau
    tong = None   # tổng byte đã biết từ Content-Range (None = chưa biết)
    dich = cuoi   # điểm kết thúc hiệu dụng; thắt lại khi biết tổng
    so_lan_noi_lai = 0
    ban = None
    _DEM["luong"] += 1

    async def _ghi_nhan_loi(exc):
        """Xử lý một lần thất bại.

        Raise ``LoiNguonKhongCuuDuoc`` khi hết 5 lần. Trả False khi
        ``kiem_dung()`` báo dừng (gọi hàm trả False → return im lặng).
        Ngược lại ngủ backoff rồi trả True để mở lại từ ``da_gui``.
        """
        nonlocal so_lan_noi_lai
        so_lan_noi_lai += 1
        ten_loi = type(exc).__name__
        het_luot = so_lan_noi_lai >= len(CHO_NOI_LAI)
        if not het_luot and _can_giai_lai_link(exc):
            # Link hết hạn: quên bản đệm, giải lại rồi mới tính lần thử.
            logger.debug("giải lại link sau lỗi %s ở offset %d", ten_loi, da_gui)
            await _cho(giai_lai_link())
        _DEM["lan_noi_lai"] += 1
        if het_luot:
            ghi_log("luong_that_bai", da_gui=da_gui,
                    lan_thu=so_lan_noi_lai, loai_loi=ten_loi)
            _DEM["that_bai"] += 1
            raise LoiNguonKhongCuuDuoc(
                f"thượng nguồn đứt ở offset {da_gui} sau "
                f"{so_lan_noi_lai} lần nối lại (lỗi cuối: {ten_loi})"
            )
        ghi_log("luong_dut_noi_lai", offset=da_gui, lan_thu=so_lan_noi_lai,
                loai_loi=ten_loi, da_gui=da_gui)
        logger.debug("nối lại lần %d ở offset %d (%s)",
                     so_lan_noi_lai, da_gui, ten_loi)
        if kiem_dung is not None and await _cho(kiem_dung()):
            # «dừng là dừng hẳn»: không ngủ, không mở lại, không request thêm.
            return False
        await _cho(_ngu(CHO_NOI_LAI[so_lan_noi_lai - 1]))
        return True

    try:
        if dich is not None and dich < da_gui:
            # Khoảng rỗng (vd cuoi < dau): xong ngay, không chạm thượng nguồn.
            ghi_log("luong_hoan_tat", byte=da_gui, so_lan_noi_lai=0,
                    giay=round(time.monotonic() - bat_dau, 3))
            return
        while True:
            if kiem_dung is not None and await _cho(kiem_dung()):
                return
            tu = da_gui
            den = tu + KHUC - 1 if dich is None else min(tu + KHUC - 1, dich)
            logger.debug("mở khúc thượng nguồn [%d-%d]", tu, den)
            try:
                ban = await _cho(mo_khuc(tu, den))
            except Exception as exc:  # OSError mất mạng/timeout, lỗi HTTP khi mở
                ban = None
                if not await _ghi_nhan_loi(exc):
                    return
                continue

            ma = getattr(ban, "ma_trang_thai", None)
            headers = getattr(ban, "headers", None)
            if ma == 416:
                # Thượng nguồn báo không còn gì từ da_gui → hết luồng, xong sạch.
                await _dong(ban)
                ban = None
                break
            if ma in _MA_CAN_GIAI_LAI_LINK or getattr(
                    ban, "loai_loi_403_410", False):
                # mo_khuc trả obj lỗi thay vì ném — xử như lỗi mở 403/404/410.
                await _dong(ban)
                ban = None
                if not await _ghi_nhan_loi(LoiMoKhuc(
                        f"thượng nguồn trả {ma} khi mở khúc [{tu}-{den}]",
                        ma_trang_thai=ma)):
                    return
                continue
            hop_le, ly_do = _ban_hop_le(ma, headers, tu)
            if not hop_le:
                await _dong(ban)
                ban = None
                if not await _ghi_nhan_loi(LoiMoKhuc(
                        f"bản [{tu}-{den}] không hợp lệ: {ly_do}")):
                    return
                continue

            # Học tổng từ Content-Range; thắt điểm kết thúc nếu biết.
            try:
                tong_moi = await _cho(doc_tong(headers))
            except Exception:
                logger.debug("doc_tong lỗi (bỏ qua)", exc_info=True)
                tong_moi = None
            if tong_moi is not None:
                if tong is not None and tong_moi != tong:
                    await _dong(ban)
                    ban = None
                    if not await _ghi_nhan_loi(LoiMoKhuc(
                            f"tổng đổi {tong} -> {tong_moi} ở khúc [{tu}-{den}]")):
                        return
                    continue
                tong = tong_moi
                dich = tong - 1 if dich is None else min(dich, tong - 1)
            if dich is not None and da_gui > dich:
                await _dong(ban)
                ban = None
                break

            # Đọc khúc này, yield từng miếng 64 KiB ngay (áp lực ngược tự nhiên).
            nhan_duoc = 0
            try:
                while True:
                    mieng = await _cho(ban.read(MIENG_DOC))
                    if not mieng:
                        break
                    if dich is not None and da_gui + len(mieng) > dich + 1:
                        mieng = mieng[:dich + 1 - da_gui]
                    try:
                        yield mieng
                    except GeneratorExit:
                        # Client ngắt giữa chừng: im lặng tuyệt đối.
                        return
                    da_gui += len(mieng)
                    _DEM["byte"] += len(mieng)
                    nhan_duoc += len(mieng)
                    if dich is not None and da_gui > dich:
                        break
            except Exception as exc:  # lỗi giữa khúc (đọc đứt)
                await _dong(ban)
                ban = None
                if not await _ghi_nhan_loi(exc):
                    return
                continue
            await _dong(ban)
            ban = None

            if dich is not None and da_gui > dich:
                break  # đã đủ byte
            if nhan_duoc >= den - tu + 1:
                continue  # đủ (hoặc thừa) cả khúc → mở khúc kế
            # Tới đây: phản hồi ngắn hơn đã xin.
            if dich is None:
                # Tổng chưa biết: vừa đọc hết một phản hồi.
                if nhan_duoc == 0:
                    if da_gui == dau and ma == 200:
                        break  # 200 rỗng = tệp rỗng
                    if da_gui > dau:
                        break  # khúc sau rỗng → thượng nguồn hết thật
                    # Khúc đầu 206 rỗng: bất thường → nối lại cho chắc.
                    if not await _ghi_nhan_loi(LoiMoKhuc("khúc đầu 206 rỗng")):
                        return
                    continue
                # Phản hồi ngắn mà chưa biết tổng: nối lại để chắc chắn
                # (hết thật thì lần mở sau 416/rỗng → xong sạch).
                if not await _ghi_nhan_loi(LoiMoKhuc(
                        f"đọc {nhan_duoc}/{den - tu + 1} byte, tổng chưa biết")):
                    return
                continue
            # dich đã biết mà phản hồi ngắn → hết sớm → nối lại từ da_gui.
            if not await _ghi_nhan_loi(LoiMoKhuc(
                    f"đọc {nhan_duoc}/{den - tu + 1} byte, còn thiếu tới {dich}")):
                return
            continue
    except GeneratorExit:
        # Ngắt khi đang chờ I/O (ngủ/mở/đọc): im lặng tuyệt đối.
        return
    finally:
        if ban is not None:
            try:
                ban.close()  # hợp đồng: .close() đồng bộ; đang dọn GeneratorExit
            except Exception:
                pass

    ghi_log("luong_hoan_tat", byte=da_gui, so_lan_noi_lai=so_lan_noi_lai,
            giay=round(time.monotonic() - bat_dau, 3))
    logger.debug("luồng hoàn tất: %d byte, %d lần nối lại",
                 da_gui, so_lan_noi_lai)
