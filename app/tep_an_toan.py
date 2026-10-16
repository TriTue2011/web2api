"""Bộ kiểm tra tệp tải lên dùng chung (CHUAN-BAO-MAT-MODULE.md mục 3.7).

- kiem_anh: Đọc HEADER ảnh PNG/JPEG/GIF/WebP thuần Python (không thêm gói mới).
  Nhận dạng định dạng theo NỘI DUNG nhị phân, không tin phần mở rộng tệp.
  Từ chối ngay khi kích thước khai báo vượt trần TRƯỚC khi giải mã (chống bom điểm ảnh / OOM).
- kiem_zip: Đọc mục lục lưu trữ bằng zipfile KHÔNG giải nén ra đĩa.
  Chặn tấn công zip-slip (.. hoặc đường dẫn tuyệt đối), symlink, và bom nén
  (theo tỉ lệ nén từng mục, tổng tỉ lệ nén, tổng dung lượng giải nén và số mục).
- Ngoại lệ: LoiTep(ma, ly_do).
"""

from __future__ import annotations

import io
import re
import stat
import struct
import zipfile


class LoiTep(Exception):
    """Ngoại lệ khi kiểm tra tệp không an toàn hoặc không hợp lệ."""

    def __init__(self, ma: str, ly_do: str):
        self.ma = ma
        self.ly_do = ly_do
        super().__init__(f"[{ma}] {ly_do}")


def kiem_anh(
    du_lieu: bytes,
    *,
    canh_toi_da: int = 8192,
    diem_anh_toi_da: int = 40_000_000,
) -> dict:
    """Kiểm tra tính an toàn và lấy kích thước ảnh từ tiêu đề nhị phân.

    Chỉ hỗ trợ PNG, JPEG, GIF, WebP.
    Trả về dict: {"loai": str, "mime": str, "rong": int, "cao": int, "diem_anh": int}.
    Ném LoiTep nếu định dạng lạ, tệp hỏng hoặc kích thước vượt trần.
    """
    if not isinstance(du_lieu, (bytes, bytearray)) or len(du_lieu) == 0:
        raise LoiTep("khong_phai_anh", "dữ liệu ảnh rỗng hoặc không đúng kiểu bytes")

    n = len(du_lieu)
    w = 0
    h = 0
    diem_anh = 0
    loai = ""
    mime = ""

    # 1. PNG / APNG: 8 byte chữ ký b"\x89PNG\r\n\x1a\n"
    if du_lieu.startswith(b"\x89PNG\r\n\x1a\n"):
        if n < 24:
            raise LoiTep("khong_phai_anh", "dữ liệu PNG quá ngắn")
        if du_lieu[12:16] != b"IHDR":
            raise LoiTep("khong_phai_anh", "PNG thiếu khối IHDR hợp lệ")
        w, h = struct.unpack(">II", du_lieu[16:24])
        loai = "png"
        mime = "image/png"
        max_w, max_h = w, h
        tong_diem_anh = w * h
        so_khung = 1

        # Duyệt các chunk tìm fcTL nếu là APNG
        pos = 8
        while pos + 8 <= n:
            ch_len = struct.unpack(">I", du_lieu[pos:pos + 4])[0]
            ch_type = du_lieu[pos + 4:pos + 8]
            data_start = pos + 8
            if ch_type == b"fcTL" and data_start + 12 <= n:
                fw, fh = struct.unpack(">II", du_lieu[data_start + 4:data_start + 12])
                max_w = max(max_w, fw)
                max_h = max(max_h, fh)
                tong_diem_anh += fw * fh
                so_khung += 1
                if max_w > canh_toi_da or max_h > canh_toi_da:
                    raise LoiTep("canh_qua_lon", f"cạnh ảnh {max(max_w, max_h)} vượt trần cho phép {canh_toi_da}px")
                if tong_diem_anh > diem_anh_toi_da:
                    raise LoiTep("diem_anh_qua_lon", f"tổng số điểm ảnh mọi khung ({tong_diem_anh}) vượt trần {diem_anh_toi_da}")
            pos = data_start + ch_len + 4  # +4 cho CRC

        w = max_w
        h = max_h
        diem_anh = tong_diem_anh

    # 2. GIF: b"GIF87a" hoặc b"GIF89a"
    elif du_lieu.startswith((b"GIF87a", b"GIF89a")):
        if n < 10:
            raise LoiTep("khong_phai_anh", "dữ liệu GIF quá ngắn")
        w_logical, h_logical = struct.unpack("<HH", du_lieu[6:10])
        max_w = w_logical
        max_h = h_logical
        tong_diem_anh = 0
        so_khung = 0

        # B3: Duyệt Image Descriptor tính max(rộng, cao) và tổng điểm ảnh mọi khung
        if n >= 13:
            packed = du_lieu[10]
            gct_size = 3 * (2 ** ((packed & 0x07) + 1)) if (packed & 0x80) else 0
            pos = 13 + gct_size
            while pos < n:
                b = du_lieu[pos]
                if b == 0x3B:  # Trailer
                    break
                elif b == 0x21:  # Extension block
                    pos += 2
                    while pos < n:
                        sub_len = du_lieu[pos]
                        pos += 1
                        if sub_len == 0:
                            break
                        pos += sub_len
                elif b == 0x2C:  # Image Descriptor
                    if pos + 10 > n:
                        raise LoiTep("anh_hong", "khối Image Descriptor GIF bị cắt ngắn")
                    fw, fh = struct.unpack("<HH", du_lieu[pos + 5:pos + 9])
                    packed_img = du_lieu[pos + 9]
                    max_w = max(max_w, fw)
                    max_h = max(max_h, fh)
                    so_khung += 1
                    tong_diem_anh += fw * fh
                    if max_w > canh_toi_da or max_h > canh_toi_da:
                        raise LoiTep("canh_qua_lon", f"cạnh ảnh {max(max_w, max_h)} vượt trần cho phép {canh_toi_da}px")
                    if tong_diem_anh > diem_anh_toi_da:
                        raise LoiTep("diem_anh_qua_lon", f"tổng số điểm ảnh mọi khung ({tong_diem_anh}) vượt trần {diem_anh_toi_da}")
                    pos += 10
                    if packed_img & 0x80:
                        lct_size = 3 * (2 ** ((packed_img & 0x07) + 1))
                        pos += lct_size
                    if pos >= n:
                        break
                    pos += 1  # LZW min code size
                    while pos < n:
                        sub_len = du_lieu[pos]
                        pos += 1
                        if sub_len == 0:
                            break
                        pos += sub_len
                else:
                    break

        if so_khung == 0:
            tong_diem_anh = w_logical * h_logical
        w = max_w
        h = max_h
        diem_anh = tong_diem_anh
        loai = "gif"
        mime = "image/gif"

    # 3. JPEG: bắt đầu bằng SOI b"\xff\xd8"
    elif du_lieu.startswith(b"\xff\xd8"):
        idx = 2
        sofs = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        # B6: Dùng bytes.find(b"\xff", idx, vung_quet) thay vì vòng lặp Python quét từng byte
        vung_quet = min(n, 1024 * 1024)
        so_marker = 0
        while idx < vung_quet:
            next_ff = du_lieu.find(b"\xff", idx, vung_quet)
            if next_ff == -1:
                break
            idx = next_ff
            while idx < vung_quet and du_lieu[idx] == 0xFF:
                idx += 1
            if idx >= vung_quet:
                break
            marker = du_lieu[idx]
            idx += 1
            so_marker += 1
            if so_marker > 2000:
                raise LoiTep("anh_hong", "quá nhiều marker JPEG trong header")
            if marker in (0xD8, 0xD9, 0x00) or (0xD0 <= marker <= 0xD7):
                continue
            if idx + 2 > n:
                break
            do_dai = struct.unpack(">H", du_lieu[idx:idx + 2])[0]
            if do_dai < 2:
                raise LoiTep("anh_hong", "độ dài segment JPEG không hợp lệ")
            if marker in sofs:
                if idx + 7 > n:
                    raise LoiTep("anh_hong", "segment SOF JPEG bị cắt ngắn")
                h, w = struct.unpack(">HH", du_lieu[idx + 3:idx + 7])
                break
            idx += do_dai

        if w == 0 or h == 0:
            raise LoiTep("anh_hong", "không tìm thấy thông tin kích thước trong ảnh JPEG")
        loai = "jpeg"
        mime = "image/jpeg"
        diem_anh = w * h

    # 4. WebP: RIFF....WEBP
    elif du_lieu.startswith(b"RIFF") and n >= 12 and du_lieu[8:12] == b"WEBP":
        if n < 16:
            raise LoiTep("anh_hong", "dữ liệu WebP quá ngắn")
        chunk_type = du_lieu[12:16]
        if chunk_type == b"VP8 ":
            # Lossy VP8
            if n < 30:
                raise LoiTep("anh_hong", "khối VP8 quá ngắn")
            if du_lieu[23:26] != b"\x9d\x01\x2a":
                raise LoiTep("anh_hong", "mã bắt đầu VP8 không hợp lệ")
            raw_w, raw_h = struct.unpack("<HH", du_lieu[26:30])
            w = raw_w & 0x3FFF
            h = raw_h & 0x3FFF
            diem_anh = w * h
        elif chunk_type == b"VP8L":
            # Lossless VP8L
            if n < 25:
                raise LoiTep("anh_hong", "khối VP8L quá ngắn")
            if du_lieu[20] != 0x2F:
                raise LoiTep("anh_hong", "chữ ký VP8L không hợp lệ")
            b0, b1, b2, b3 = du_lieu[21:25]
            w = 1 + (((b1 & 0x3F) << 8) | b0)
            h = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | (b1 >> 6))
            diem_anh = w * h
        elif chunk_type == b"VP8X":
            # Extended WebP
            if n < 30:
                raise LoiTep("anh_hong", "khối VP8X quá ngắn")
            flags = du_lieu[20]
            w = 1 + int.from_bytes(du_lieu[24:27], "little")
            h = 1 + int.from_bytes(du_lieu[27:30], "little")
            max_w, max_h = w, h
            tong_diem_anh = 0
            so_khung = 0
            # Nếu có animation (bit 1 của byte 20)
            if flags & 0x02:
                pos = 30
                while pos + 8 <= n:
                    ch_name = du_lieu[pos:pos + 4]
                    ch_size = struct.unpack("<I", du_lieu[pos + 4:pos + 8])[0]
                    data_start = pos + 8
                    if ch_name == b"ANMF" and data_start + 12 <= n:
                        fw = 1 + int.from_bytes(du_lieu[data_start + 6:data_start + 9], "little")
                        fh = 1 + int.from_bytes(du_lieu[data_start + 9:data_start + 12], "little")
                        max_w = max(max_w, fw)
                        max_h = max(max_h, fh)
                        tong_diem_anh += fw * fh
                        so_khung += 1
                        if max_w > canh_toi_da or max_h > canh_toi_da:
                            raise LoiTep("canh_qua_lon", f"cạnh ảnh {max(max_w, max_h)} vượt trần cho phép {canh_toi_da}px")
                        if tong_diem_anh > diem_anh_toi_da:
                            raise LoiTep("diem_anh_qua_lon", f"tổng số điểm ảnh mọi khung ({tong_diem_anh}) vượt trần {diem_anh_toi_da}")
                    pos = data_start + ch_size + (ch_size & 1)
            if so_khung == 0:
                tong_diem_anh = w * h
            w = max_w
            h = max_h
            diem_anh = tong_diem_anh
        else:
            raise LoiTep("anh_hong", f"khối WebP không được hỗ trợ: {chunk_type!r}")
        loai = "webp"
        mime = "image/webp"

    else:
        raise LoiTep("khong_phai_anh", "định dạng ảnh không được hỗ trợ (chỉ nhận PNG, JPEG, GIF, WebP)")

    if w <= 0 or h <= 0:
        raise LoiTep("anh_hong", f"kích thước ảnh không hợp lệ ({w}x{h})")

    if w > canh_toi_da or h > canh_toi_da:
        raise LoiTep("canh_qua_lon", f"cạnh ảnh {max(w, h)} vượt trần cho phép {canh_toi_da}px")

    if diem_anh > diem_anh_toi_da:
        raise LoiTep("diem_anh_qua_lon", f"số điểm ảnh {diem_anh} vượt trần cho phép {diem_anh_toi_da}")

    return {
        "loai": loai,
        "mime": mime,
        "rong": w,
        "cao": h,
        "diem_anh": diem_anh,
    }


def kiem_zip(
    du_lieu: bytes,
    *,
    tran_byte_vao: int = 50 * 1024 * 1024,
    so_muc_toi_da: int = 2000,
    tong_giai_nen_toi_da: int = 200 * 1024 * 1024,
    ti_le_nen_toi_da: float = 150.0,
) -> dict:
    """Kiểm tra mục lục tệp zip mà không giải nén ra đĩa.

    - B2: Kiểm tra trần byte đầu vào (tran_byte_vao) trước khi nạp ZipFile.
    - B2: Đọc thử từng mục qua luồng có trần byte tổng, kiểm tra CRC và phát hiện lệch kích thước khai báo.
    - B2: Từ chối zip lồng và coi FILE_ATTRIBUTE_REPARSE_POINT (0x0400) như symlink.
    - Chống zip-slip (.. hoặc đường dẫn tuyệt đối);
    - Chống bom nén theo tỉ lệ từng mục, tỉ lệ tổng, tổng dung lượng giải nén và số mục.

    Trả về dict: {"so_muc": int, "tong_giai_nen": int, "kich_thuoc_nen": int}.
    """
    if not isinstance(du_lieu, (bytes, bytearray)) or len(du_lieu) == 0:
        raise LoiTep("khong_phai_zip", "dữ liệu zip rỗng hoặc không đúng kiểu bytes")

    if len(du_lieu) > tran_byte_vao:
        raise LoiTep(
            "kich_thuoc_vao_qua_lon",
            f"kích thước tệp zip đầu vào ({len(du_lieu)} bytes) vượt trần {tran_byte_vao} bytes",
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(du_lieu))
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as e:
        raise LoiTep("zip_hong", f"tệp zip bị hỏng hoặc không đúng định dạng: {e}") from None

    with zf:
        danh_sach = zf.infolist()
        if len(danh_sach) > so_muc_toi_da:
            raise LoiTep("so_muc_qua_lon", f"số mục trong zip ({len(danh_sach)}) vượt trần {so_muc_toi_da}")

        tong_giai_nen = 0
        for info in danh_sach:
            ten = info.filename
            if not ten:
                continue

            # Kiểm tra zip-slip
            if ten.startswith(("/", "\\")) or re.match(r"^[a-zA-Z]:", ten):
                raise LoiTep("zip_slip", f"đường dẫn tuyệt đối trong zip: {ten!r}")

            cac_khuc = [k for k in re.split(r"[/\\]+", ten) if k]
            if ".." in cac_khuc:
                raise LoiTep("zip_slip", f"đường dẫn chứa '..' trong zip: {ten!r}")

            # Kiểm tra liên kết mềm (symlink POSIX hoặc FILE_ATTRIBUTE_REPARSE_POINT 0x0400 Windows)
            mode = (info.external_attr >> 16) & 0xFFFF
            is_symlink = stat.S_ISLNK(mode) or bool(info.external_attr & 0x0400)
            if is_symlink:
                raise LoiTep("symlink", f"mục zip là liên kết mềm (symlink/reparse_point) bị từ chối: {ten!r}")

            # B2: Từ chối zip lồng chứa trong file nén
            ten_lower = ten.lower()
            if ten_lower.endswith((".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar")):
                raise LoiTep("zip_long", f"từ chối tệp nén lồng bên trong: {ten!r}")

            # Kiểm tra tỉ lệ nén khai báo từng mục
            if info.compress_size > 0:
                ti_le = info.file_size / info.compress_size
                if info.file_size > 1024 and ti_le > ti_le_nen_toi_da:
                    raise LoiTep(
                        "ti_le_nen_qua_lon",
                        f"mục {ten!r} có tỉ lệ nén {ti_le:.1f}:1 vượt trần {ti_le_nen_toi_da:.0f}:1",
                    )
            elif info.compress_size == 0 and info.file_size > 1024:
                raise LoiTep(
                    "ti_le_nen_qua_lon",
                    f"mục {ten!r} có kích thước nén bằng 0 nhưng giải nén {info.file_size} bytes",
                )

            # B2: Đọc thử từng mục qua luồng có trần byte tổng và kiểm tra CRC thực tế
            if not info.is_dir() and not ten.endswith("/"):
                so_byte_muc = 0
                try:
                    with zf.open(info, "r") as f_muc:
                        while True:
                            chunk = f_muc.read(65536)
                            if not chunk:
                                break
                            so_byte_muc += len(chunk)
                            tong_giai_nen += len(chunk)
                            if tong_giai_nen > tong_giai_nen_toi_da:
                                raise LoiTep(
                                    "tong_giai_nen_qua_lon",
                                    f"tổng kích thước giải nén ({tong_giai_nen} bytes) vượt trần {tong_giai_nen_toi_da} bytes",
                                )
                except (zipfile.BadZipFile, zipfile.LargeZipFile) as e:
                    raise LoiTep("zip_hong", f"mục {ten!r} bị lỗi CRC hoặc hỏng dữ liệu nén: {e}") from None

                if so_byte_muc != info.file_size:
                    raise LoiTep(
                        "khai_gian_kich_thuoc",
                        f"mục {ten!r} khai báo kích thước {info.file_size} bytes nhưng thực tế giải nén ra {so_byte_muc} bytes",
                    )

        # Kiểm tra tỉ lệ nén toàn tệp
        kich_thuoc_nen = len(du_lieu)
        if kich_thuoc_nen > 0:
            ti_le_tong = tong_giai_nen / kich_thuoc_nen
            if tong_giai_nen > 1024 and ti_le_tong > ti_le_nen_toi_da:
                raise LoiTep(
                    "ti_le_nen_qua_lon",
                    f"tổng tỉ lệ nén của kho lưu trữ {ti_le_tong:.1f}:1 vượt trần {ti_le_nen_toi_da:.0f}:1",
                )

        return {
            "so_muc": len(danh_sach),
            "tong_giai_nen": tong_giai_nen,
            "kich_thuoc_nen": kich_thuoc_nen,
        }
