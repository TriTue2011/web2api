"""Giao thức Tuya LAN (cổng 6668) — TỰ VIẾT bằng thư viện chuẩn + cryptography.

Nguồn ý tưởng (port từ c2a, services/tuya_local.py, commit d879b03, CHỈ ĐỌC):
lớp local của c2a dùng ``tinytuya`` (thư viện Tuya bên thứ ba). Bản này KHÔNG
thêm thư viện đó (luật kho: không thêm thư viện Tuya bên thứ ba) — giao thức
khung 55AA tự viết: tiền tố 0x000055AA, payload JSON mã hoá AES-128-ECB bằng
local_key (bản 3.3 thêm đầu «3.3» + 12 byte 0 trước bản mã), CRC32 IEEE che
toàn bộ khung trước nó, hậu tố 0x0000AA55. Đây là giao thức được tài liệu hoá
công khai của Tuya cho thiết bị cắm nguồn (bản 3.1/3.3); bản 3.4/3.5 (khoá
phiên HMAC) CHƯA hỗ trợ.

Bài học giữ nguyên từ c2a (đo thật 10/09/2026):
- Lỗi GIẢI MÃ nghĩa là local_key ĐÃ ĐỔI (ghép nối lại thiết bị là Tuya đổi
  khoá) — không phải mạng hỏng; báo rõ cho người dùng, đừng thử lại vô hạn.
- Thiết bị pin (khoá cửa, gateway Zigbee) KHÔNG mở cổng 6668 — dò ra rỗng là
  trạng thái bình thường của nhà, không phải lỗi.
- Thiết bị chỉ nhớ ~5 client gần nhất → mỗi lần hỏi mở đúng MỘT kết nối rồi
  đóng ngay, không giữ kết nối thường trực.

An toàn:
- Chỉ nối tới IP người dùng khai TƯỜNG MINH trong cấu hình, và IP đó phải là
  địa chỉ riêng (private) — kiểm bằng ipaddress lúc cài và lúc nối (không
  DNS, không tên máy: thiết bị LAN khai bằng số). Loopback chỉ mở khi test
  bật cờ WEB2API_TEST_CHO_PHEP_LOOPBACK của lõi (app/tu_xa).
- local_key chỉ giữ trong RAM của tiến trình, không ghi log, không trả ra
  ngoài, không nằm trong thông báo lỗi.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import socket
import struct
import zlib
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.tu_xa import cho_phep_loopback_cho_test

TIEN_TO = 0x000055AA
HAU_TO = 0x0000AA55

#: Lệnh của giao thức (tài liệu công khai Tuya local protocol).
LENH_DIEU_KHIEN = 7
LENH_TRANG_THAI = 8
LENH_HOI_DP = 10

CONG_MAC_DINH = 6668
CONG_PHAT_31 = 6667   # quảng bá bản 3.1: payload JSON bản rõ trong khung
CONG_PHAT_33 = 6666   # quảng bá bản 3.3: payload mã hoá AES bằng khoá quảng bá

#: Khoá giải mã gói quảng bá bản 3.3: MD5 của chuỗi cố định của giao thức.
#: MD5 ở đây là MÃ GIAO THỨC Tuya định sẵn (không phải lựa chọn bảo mật).
_KHOA_QUANG_BA = hashlib.md5(b"yGAdlopoPVldABfn", usedforsecurity=False).digest()

HET_GIO_NOI_GIAY = 5.0
HET_GIO_DOC_GIAY = 5.0
#: Trần một khung nhận về — trạng thái thiết bị không bao giờ lớn tới mức này;
#: vượt là dữ liệu hỏng/không phải thiết bị Tuya.
TRAN_KHUNG = 64 * 1024


class LoiLan(Exception):
    """Lỗi LAN có lời giải thích sẵn cho người đọc; không chứa bí mật."""


# ── AES-128-ECB + PKCS7 (giao thức dùng ECB; khoá là local_key 16 byte) ────

def _khoa_bytes(local_key: str) -> bytes:
    khoa = str(local_key or "").encode("utf-8")
    if len(khoa) != 16:
        raise LoiLan("local_key phải đúng 16 ký tự — khoá hiện tại không dùng được.")
    return khoa


def aes_ecb_tho(khoa: bytes, ban_ro: bytes) -> bytes:
    """AES-ECB thô: bội 16 byte vào/ra, không đệm (đo vector NIST SP 800-38A)."""
    if len(khoa) not in (16, 24, 32):
        raise LoiLan("khoá AES phải dài 16/24/32 byte.")
    if not ban_ro or len(ban_ro) % 16:
        raise LoiLan("bản rõ AES-ECB thô phải là bội của 16 byte.")
    # nosec B305: giao thức LAN của Tuya BẮT BUỘC AES-128-ECB — không phải
    # lựa chọn của module (payload ngắn, khoá riêng từng thiết bị).
    enc = Cipher(algorithms.AES(khoa), modes.ECB()).encryptor()  # nosec B305
    return enc.update(ban_ro) + enc.finalize()


def aes_ecb_giai_tho(khoa: bytes, ban_ma: bytes) -> bytes:
    """AES-ECB thô ngược: bội 16 byte vào/ra, không bỏ đệm."""
    if len(khoa) not in (16, 24, 32):
        raise LoiLan("khoá AES phải dài 16/24/32 byte.")
    if not ban_ma or len(ban_ma) % 16:
        raise LoiLan("bản mã AES-ECB thô phải là bội của 16 byte.")
    dec = Cipher(algorithms.AES(khoa), modes.ECB()).decryptor()  # nosec B305
    return dec.update(ban_ma) + dec.finalize()


def aes_ecb_ma_hoa(khoa: bytes, ban_ro: bytes) -> bytes:
    """AES-ECB + đệm PKCS7 (giao thức Tuya: khối vào bội 16 thì thêm trọn một khối đệm)."""
    dem = 16 - (len(ban_ro) % 16)
    return aes_ecb_tho(khoa, ban_ro + bytes([dem]) * dem)


def aes_ecb_giai_ma(khoa: bytes, ban_ma: bytes) -> bytes:
    """AES-ECB + bỏ đệm PKCS7. Đệm sai → LoiLan «giải mã» (khoá đã đổi)."""
    if not ban_ma or len(ban_ma) % 16:
        raise LoiLan("Giải mã thất bại: dữ liệu không phải khối AES — "
                     "thường là local_key đã đổi, hãy lấy khoá mới.")
    try:
        du_lieu = aes_ecb_giai_tho(khoa, ban_ma)
    except Exception as exc:  # cryptography ném ValueError khi khối hỏng
        raise LoiLan("Giải mã thất bại — thường là local_key đã đổi, "
                     "hãy lấy khoá mới.") from exc
    dem = du_lieu[-1]
    if dem < 1 or dem > 16 or du_lieu[-dem:] != bytes([dem]) * dem:
        raise LoiLan("Giải mã thất bại: đệm PKCS7 sai — thường là local_key "
                     "đã đổi, hãy lấy khoá mới.")
    return du_lieu[:-dem]


# ── Khung 55AA ──────────────────────────────────────────────────────────────

def dong_khung(seq: int, lenh: int, payload_json: bytes, phien_ban: str,
               local_key: str) -> bytes:
    """Đóng một khung YÊU CẦU: header + payload (3.3: đầu «3.3» + bản mã) + CRC + hậu tố."""
    khoa = _khoa_bytes(local_key)
    if str(phien_ban).startswith("3.3"):
        than = b"3.3" + b"\x00" * 12 + aes_ecb_ma_hoa(khoa, payload_json)
    else:  # 3.1: payload JSON bản rõ
        than = payload_json
    dau = struct.pack(">IIII", TIEN_TO, int(seq), int(lenh), len(than) + 8)
    crc = struct.pack(">I", zlib.crc32(dau + than) & 0xFFFFFFFF)
    return dau + than + crc + struct.pack(">I", HAU_TO)


def _cat_khung(du_lieu: bytes) -> tuple[int, int, bytes, bytes]:
    """Cắt một khung khỏi đầu `du_lieu`: (seq, lệnh, payload thô, phần còn lại).

    Khung PHẢN HỒI có thêm 4 byte mã trả (retcode) đứng trước payload — tách
    ở đây để nơi gọi chỉ thấy payload.
    """
    if len(du_lieu) < 16:
        raise LoiLan("Khung quá ngắn — không phải thiết bị Tuya.")
    tien_to, seq, lenh, dai = struct.unpack(">IIII", du_lieu[:16])
    if tien_to != TIEN_TO:
        raise LoiLan("Sai tiền tố khung — không phải thiết bị Tuya.")
    if dai < 8 or 16 + dai > TRAN_KHUNG + 16:
        raise LoiLan("Độ dài khung lạ — bỏ qua an toàn.")
    if len(du_lieu) < 16 + dai:
        raise LoiLan("Khung chưa đủ byte.")
    than = du_lieu[16:16 + dai]
    if struct.unpack(">I", than[-4:])[0] != HAU_TO:
        raise LoiLan("Sai hậu tố khung — dữ liệu hỏng.")
    crc_ky_vong = struct.unpack(">I", than[-8:-4])[0]
    if zlib.crc32(du_lieu[:16 + dai - 8]) & 0xFFFFFFFF != crc_ky_vong:
        raise LoiLan("CRC khung sai — dữ liệu hỏng trên đường truyền.")
    payload = than[:-8]
    return seq, lenh, payload, du_lieu[16 + dai:]


def _giai_payload(payload: bytes, phien_ban: str, local_key: str) -> bytes:
    """Giải mã payload của một khung: đầu «3.3» + bản mã / JSON rõ (3.1) /
    bản mã không đầu (3.3 một số thiết bị)."""
    if payload.startswith(b"3.3"):
        return aes_ecb_giai_ma(_khoa_bytes(local_key), payload[15:])
    if payload.startswith(b"{"):
        return payload
    if str(phien_ban).startswith("3.3"):
        return aes_ecb_giai_ma(_khoa_bytes(local_key), payload)
    return payload


def mo_khung(du_lieu: bytes, phien_ban: str, local_key: str) -> tuple[int, int, bytes]:
    """Mở một khung: trả (seq, lệnh, payload JSON đã giải mã dạng bytes).

    Khung PHẢN HỒI có 4 byte mã trả đứng đầu payload; khung YÊU CẦU thì
    không — thử dạng có mã trả trước, sai thì thử dạng trần.
    Ném LoiLan khi khung sai hoặc giải mã hỏng (khoá đã đổi).
    """
    seq, lenh, payload, _con = _cat_khung(du_lieu)
    loi_cuoi: LoiLan | None = None
    for ung_vien in ((payload[4:], payload) if len(payload) >= 4 else (payload,)):
        try:
            return seq, lenh, _giai_payload(ung_vien, phien_ban, local_key)
        except LoiLan as e:
            loi_cuoi = e
    raise loi_cuoi or LoiLan("Payload rỗng — không phải thiết bị Tuya.")


# ── Kiểm địa chỉ LAN ────────────────────────────────────────────────────────

def kiem_ip_lan(ip: Any) -> str:
    """IP thiết bị LAN hợp lệ: số IP riêng khai tường minh. ValueError nếu sai.

    Chặn theo CẤU TRÚC ipaddress: loopback/link-local/metadata/multicast/
    không định tuyến đều bị từ chối; chỉ dải riêng (10/8, 172.16/12,
    192.168/16 và tương đương) mới qua. Loopback chỉ qua khi test bật cờ của
    lõi (máy chủ giả chạy trên 127.0.0.1).
    """
    s = str(ip or "").strip()
    try:
        dia = ipaddress.ip_address(s)
    except ValueError:
        raise ValueError(f"«{s}» không phải địa chỉ IP — thiết bị LAN phải "
                         "khai bằng số IP trong mạng nhà.") from None
    if dia.is_loopback:
        if cho_phep_loopback_cho_test():
            return s
        raise ValueError("địa chỉ loopback không phải thiết bị LAN.")
    if dia.is_link_local or dia.is_multicast or dia.is_unspecified or dia.is_reserved:
        raise ValueError(f"địa chỉ {s} không dùng được cho thiết bị LAN.")
    if not dia.is_private:
        raise ValueError(f"địa chỉ {s} không phải địa chỉ riêng trong mạng nhà — "
                         "module chỉ nối tới IP riêng được khai tường minh.")
    return s


# ── Hỏi/đặt trạng thái qua TCP 6668 ─────────────────────────────────────────

async def _giao_dich(ip: str, cong: int, local_key: str, phien_ban: str,
                     lenh: int, noi_dung: dict) -> dict:
    """Một lượt hỏi-đáp TCP: mở nối, gửi một khung, đọc một khung, đóng ngay."""
    kiem_ip_lan(ip)
    try:
        doc, ghi = await asyncio.wait_for(
            asyncio.open_connection(ip, int(cong)), timeout=HET_GIO_NOI_GIAY)
    except (OSError, TimeoutError, asyncio.TimeoutError) as exc:
        raise LoiLan(f"Không nối được thiết bị ở {ip}:{cong} "
                     f"({type(exc).__name__}).") from exc
    try:
        khung = dong_khung(1, lenh, json.dumps(noi_dung).encode("utf-8"),
                           phien_ban, local_key)
        ghi.write(khung)
        await ghi.drain()
        du_lieu = b""
        try:
            while len(du_lieu) < TRAN_KHUNG:  # gom cho đủ một khung (TCP cắt đoạn)
                manh = await asyncio.wait_for(doc.read(4096),
                                              timeout=HET_GIO_DOC_GIAY)
                if not manh:
                    break
                du_lieu += manh
                try:
                    _cat_khung(du_lieu)
                    break
                except LoiLan as e:
                    if "chưa đủ" not in str(e):
                        raise
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise LoiLan("Thiết bị không trả lời trong thời gian cho phép.") from exc
        if not du_lieu:
            raise LoiLan("Thiết bị đóng kết nối mà không trả lời.")
        _seq, _lenh_tra, payload = mo_khung(du_lieu, phien_ban, local_key)
    finally:
        ghi.close()
        try:
            await ghi.wait_closed()
        except OSError:
            pass
    try:
        kq = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise LoiLan("Giải mã được nhưng nội dung không phải JSON — thường là "
                     "local_key đã đổi, hãy lấy khoá mới.") from exc
    if not isinstance(kq, dict):
        raise LoiLan("Thiết bị trả dữ liệu lạ — bỏ qua an toàn.")
    return kq


async def hoi_dps(ip: str, cong: int, local_key: str, phien_ban: str,
                  ma_thiet_bi: str) -> dict:
    """Đọc toàn bộ dps hiện tại của thiết bị. Trả dict {số_dp: giá trị}."""
    kq = await _giao_dich(ip, cong, local_key, phien_ban, LENH_HOI_DP,
                          {"gwId": ma_thiet_bi, "devId": ma_thiet_bi})
    dps = kq.get("dps")
    if not isinstance(dps, dict):
        raise LoiLan("Thiết bị không trả dps — bỏ qua an toàn.")
    return {str(k): v for k, v in dps.items()}


async def dat_dps(ip: str, cong: int, local_key: str, phien_ban: str,
                  ma_thiet_bi: str, dps: dict) -> dict:
    """Đặt dps (điều khiển). Trả dps sau khi thiết bị xác nhận."""
    import time as _time
    kq = await _giao_dich(ip, cong, local_key, phien_ban, LENH_DIEU_KHIEN,
                          {"gwId": ma_thiet_bi, "devId": ma_thiet_bi,
                           "t": int(_time.time()),
                           "dps": dps})
    tra = kq.get("dps")
    return {str(k): v for k, v in tra.items()} if isinstance(tra, dict) else dps


# ── Dò thiết bị bằng gói quảng bá UDP 6666/6667 ─────────────────────────────

def phan_tich_phat_quang(du_lieu: bytes, cong: int) -> dict | None:
    """Một gói quảng bá → {id, ip, version} hoặc None nếu không hợp lệ.

    Cổng 6667 (bản 3.1): payload JSON bản rõ. Cổng 6666 (bản 3.3): payload mã
    hoá AES bằng khoá quảng bá của giao thức. Cả hai đều nằm trong khung 55AA.
    """
    try:
        _seq, _lenh, payload, _con = _cat_khung(du_lieu)
    except LoiLan:
        return None
    def _thu(ban: bytes) -> dict | None:
        try:
            van = json.loads(ban.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            van = None
        if van is None:
            try:
                if ban.startswith(b"3.3"):
                    ban = ban[15:]
                van = json.loads(aes_ecb_giai_ma(_KHOA_QUANG_BA, ban).decode("utf-8"))
            except (LoiLan, ValueError, UnicodeDecodeError):
                return None
        return van if isinstance(van, dict) else None
    # Khung quảng bá của thiết bị THẬT có 4 byte mã trả sau header; gói thử
    # trong kiểm có thể không có — thử cả hai dạng.
    van_ban = _thu(payload[4:] if len(payload) >= 4 else payload)
    if van_ban is None:
        van_ban = _thu(payload)
    if van_ban is None:
        return None
    ma = str(van_ban.get("gwId") or "").strip()
    ip = str(van_ban.get("ip") or "").strip()
    if not ma or not ip:
        return None
    try:
        kiem_ip_lan(ip)
    except ValueError:
        return None
    return {"id": ma, "ip": ip, "version": str(van_ban.get("version") or "3.3")}


def do_thiet_bi(giay: float = 3.0, chi_loopback: bool = False) -> list[dict]:
    """Nghe quảng bá UDP trong `giay` giây. Rỗng là BÌNH THƯỜNG (nhà chưa có
    thiết bị cắm nguồn nào) — không bao giờ ném lỗi.

    `chi_loopback` chỉ dùng trong test: nghe thêm trên 127.0.0.1.
    """
    thay: dict[str, dict] = {}
    cac_sock: list[socket.socket] = []
    for cong in (CONG_PHAT_31, CONG_PHAT_33):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1" if chi_loopback else "", cong))
            s.settimeout(0.25)
            cac_sock.append(s)
        except OSError:
            continue
    import time as _time
    han = _time.monotonic() + max(0.2, float(giay))
    try:
        while _time.monotonic() < han:
            for s in cac_sock:
                try:
                    du_lieu, _nguon = s.recvfrom(2048)
                except (socket.timeout, TimeoutError):
                    continue
                except OSError:
                    continue
                kq = phan_tich_phat_quang(du_lieu, s.getsockname()[1])
                if kq:
                    thay[kq["id"]] = kq
    finally:
        for s in cac_sock:
            try:
                s.close()
            except OSError:
                pass
    return list(thay.values())
