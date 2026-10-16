"""Kiểm thử app/tep_an_toan.py: bộ kiểm tệp tải lên (ảnh và zip)."""

import io
import stat
import struct
import zipfile

import pytest

from app.tep_an_toan import LoiTep, kiem_anh, kiem_zip


# ───────────────────────────── Tạo dữ liệu mẫu ─────────────────────────────

def tao_png(w: int, h: int) -> bytes:
    """Tạo tệp PNG tối thiểu hợp lệ với kích thước w x h."""
    chu_ky = b"\x89PNG\r\n\x1a\n"
    # Khối IHDR: 13 bytes
    ihdr_data = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    import zlib
    crc = struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF)
    ihdr = struct.pack(">I", len(ihdr_data)) + b"IHDR" + ihdr_data + crc
    # Khối IEND rỗng
    iend_crc = struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    iend = struct.pack(">I", 0) + b"IEND" + iend_crc
    return chu_ky + ihdr + iend


def tao_gif(w: int, h: int) -> bytes:
    """Tạo tệp GIF tối thiểu với kích thước w x h."""
    header = b"GIF89a"
    lsd = struct.pack("<HHBBB", w, h, 0x80, 0, 0)
    palette = b"\x00\x00\x00\xff\xff\xff"
    trailer = b"\x3b"
    return header + lsd + palette + trailer


def tao_jpeg(w: int, h: int) -> bytes:
    """Tạo tệp JPEG tối thiểu với segment SOF0 kích thước w x h."""
    soi = b"\xff\xd8"
    # Segment SOF0 (0xc0): length (2) + precision (1) + h (2) + w (2) + components (1) + comp spec (3)
    sof0_payload = struct.pack(">BHHB", 8, h, w, 1) + b"\x01\x11\x00"
    sof0 = b"\xff\xc0" + struct.pack(">H", len(sof0_payload) + 2) + sof0_payload
    eoi = b"\xff\xd9"
    return soi + sof0 + eoi


def tao_webp_vp8(w: int, h: int) -> bytes:
    """Tạo WebP lossy VP8 tối thiểu."""
    # tag 3 bytes + start code 3 bytes (\x9d\x01\x2a) + w 2 bytes + h 2 bytes
    payload = b"\x00\x00\x00\x9d\x01\x2a" + struct.pack("<HH", w & 0x3FFF, h & 0x3FFF)
    chunk = b"VP8 " + struct.pack("<I", len(payload)) + payload
    riff_header = b"RIFF" + struct.pack("<I", len(chunk) + 4) + b"WEBP"
    return riff_header + chunk


def tao_webp_vp8x(w: int, h: int) -> bytes:
    """Tạo WebP extended VP8X tối thiểu."""
    flags = b"\x00\x00\x00\x00"
    w_bytes = (w - 1).to_bytes(3, "little")
    h_bytes = (h - 1).to_bytes(3, "little")
    payload = flags + w_bytes + h_bytes
    chunk = b"VP8X" + struct.pack("<I", len(payload)) + payload
    riff_header = b"RIFF" + struct.pack("<I", len(chunk) + 4) + b"WEBP"
    return riff_header + chunk


# ───────────────────────────── Test kiểm ảnh ─────────────────────────────

def test_kiem_anh_png_hop_le():
    data = tao_png(800, 600)
    kq = kiem_anh(data)
    assert kq["loai"] == "png"
    assert kq["mime"] == "image/png"
    assert kq["rong"] == 800
    assert kq["cao"] == 600
    assert kq["diem_anh"] == 480_000


def test_kiem_anh_jpeg_hop_le():
    data = tao_jpeg(1024, 768)
    kq = kiem_anh(data)
    assert kq["loai"] == "jpeg"
    assert kq["mime"] == "image/jpeg"
    assert kq["rong"] == 1024
    assert kq["cao"] == 768
    assert kq["diem_anh"] == 786_432


def test_kiem_anh_gif_hop_le():
    data = tao_gif(320, 240)
    kq = kiem_anh(data)
    assert kq["loai"] == "gif"
    assert kq["mime"] == "image/gif"
    assert kq["rong"] == 320
    assert kq["cao"] == 240
    assert kq["diem_anh"] == 76_800


def test_kiem_anh_webp_hop_le():
    data_vp8 = tao_webp_vp8(640, 480)
    kq1 = kiem_anh(data_vp8)
    assert kq1["loai"] == "webp"
    assert kq1["rong"] == 640
    assert kq1["cao"] == 480

    data_vp8x = tao_webp_vp8x(1920, 1080)
    kq2 = kiem_anh(data_vp8x)
    assert kq2["loai"] == "webp"
    assert kq2["rong"] == 1920
    assert kq2["cao"] == 1080


def test_tu_choi_png_khai_50000x50000_dung_luong_nho():
    # PNG khai báo 50.000 x 50.000 (2.5 tỷ điểm ảnh) nhưng chỉ vài byte
    data = tao_png(50000, 50000)
    assert len(data) < 1000  # Rất nhẹ

    with pytest.raises(LoiTep) as exc:
        kiem_anh(data, canh_toi_da=8192, diem_anh_toi_da=40_000_000)
    # Phải bị từ chối vì vượt trần cạnh
    assert exc.value.ma == "canh_qua_lon"


def test_tu_choi_anh_canh_vuot_tran():
    data = tao_jpeg(9000, 100)
    with pytest.raises(LoiTep) as exc:
        kiem_anh(data, canh_toi_da=8192)
    assert exc.value.ma == "canh_qua_lon"


def test_tu_choi_anh_diem_anh_vuot_tran():
    data = tao_png(7000, 7000)  # 49.000.000 điểm ảnh (> 40.000.000)
    with pytest.raises(LoiTep) as exc:
        kiem_anh(data, canh_toi_da=8192, diem_anh_toi_da=40_000_000)
    assert exc.value.ma == "diem_anh_qua_lon"


def test_tu_choi_tep_doi_lot_anh():
    # Tệp nội dung là HTML hoặc văn bản nhưng đặt tên giả là ảnh
    noi_dung_gia = b"<html><script>alert(1)</script></html>"
    with pytest.raises(LoiTep) as exc:
        kiem_anh(noi_dung_gia)
    assert exc.value.ma == "khong_phai_anh"


def test_tu_choi_anh_hong_cat_ngan():
    # Header PNG bị cắt cụt
    data = b"\x89PNG\r\n\x1a\n\x00\x00\x00"
    with pytest.raises(LoiTep) as exc:
        kiem_anh(data)
    assert exc.value.ma == "khong_phai_anh"


# ───────────────────────────── Test kiểm zip ─────────────────────────────

def tao_zip_thuan(muc_luc: dict[str, bytes]) -> bytes:
    """Tạo tệp zip trong bộ nhớ từ từ điển {tên_tệp: nội_dung}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for ten, nd in muc_luc.items():
            zf.writestr(ten, nd)
    return buf.getvalue()


def test_kiem_zip_hop_le():
    data = tao_zip_thuan({
        "tai_lieu.txt": b"Hello world 12345",
        "anh/logo.txt": b"Logo dummy data",
    })
    kq = kiem_zip(data)
    assert kq["so_muc"] == 2
    assert kq["tong_giai_nen"] == len(b"Hello world 12345") + len(b"Logo dummy data")


def test_kiem_zip_chan_zip_slip_cham_cham():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../etc/passwd", b"root:x:0:0::/root:/bin/bash")
    data = buf.getvalue()

    with pytest.raises(LoiTep) as exc:
        kiem_zip(data)
    assert exc.value.ma == "zip_slip"
    assert ".." in exc.value.ly_do


def test_kiem_zip_chan_duong_dan_tuyet_doi():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("/etc/shadow", b"secret")
    data = buf.getvalue()

    with pytest.raises(LoiTep) as exc:
        kiem_zip(data)
    assert exc.value.ma == "zip_slip"


def test_kiem_zip_chan_symlink():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("lien_ket_mem")
        # Đặt cờ symlink trong external_attr
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    data = buf.getvalue()

    with pytest.raises(LoiTep) as exc:
        kiem_zip(data)
    assert exc.value.ma == "symlink"


def test_kiem_zip_chan_bom_nen_ti_le_cao():
    # Tạo chuỗi lặp số 0 lớn để nén cực chặt (tỉ lệ > 200:1)
    du_lieu_lon = b"\x00" * (1024 * 1024 * 2)  # 2 MB zeros
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bom.bin", du_lieu_lon)
    data = buf.getvalue()

    # Kích thước nén của 2MB zeros chỉ khoảng vài KB -> tỉ lệ > 500:1
    with pytest.raises(LoiTep) as exc:
        kiem_zip(data, ti_le_nen_toi_da=150.0)
    assert exc.value.ma == "ti_le_nen_qua_lon"


def test_kiem_zip_chan_tong_giai_nen_qua_lon():
    # Tổng dung lượng giải nén vượt trần
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("f1.bin", b"A" * 5000)
        zf.writestr("f2.bin", b"B" * 6000)
    data = buf.getvalue()

    with pytest.raises(LoiTep) as exc:
        kiem_zip(data, tong_giai_nen_toi_da=10000)  # Trần 10.000 bytes (tổng 11.000)
    assert exc.value.ma == "tong_giai_nen_qua_lon"


def test_kiem_zip_chan_so_muc_qua_lon():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(15):
            zf.writestr(f"tep_{i}.txt", b"x")
    data = buf.getvalue()

    with pytest.raises(LoiTep) as exc:
        kiem_zip(data, so_muc_toi_da=10)
    assert exc.value.ma == "so_muc_qua_lon"


def test_kiem_zip_tu_choi_zip_hong():
    with pytest.raises(LoiTep) as exc:
        kiem_zip(b"day khong phai la file zip")
    assert exc.value.ma == "zip_hong"


# ───────────────────────────── Test bổ sung B2, B3, B6 ─────────────────────────────

def tao_gif_co_khung(w_logic: int, h_logic: int, danh_sach_khung: list[tuple[int, int]]) -> bytes:
    """Tạo GIF với màn hình logic và các khối Image Descriptor xác định."""
    header = b"GIF89a"
    lsd = struct.pack("<HHBBB", w_logic, h_logic, 0x80, 0, 0)
    palette = b"\x00\x00\x00\xff\xff\xff"
    body = bytearray(header + lsd + palette)
    for fw, fh in danh_sach_khung:
        img_desc = b"\x2c" + struct.pack("<HHHHB", 0, 0, fw, fh, 0)
        img_data = b"\x02\x01\x00\x00"  # LZW min code size 2, 1 sub-block rỗng
        body.extend(img_desc + img_data)
    body.append(0x3B)
    return bytes(body)


def test_b3_gif_man_hinh_logic_nho_nhung_khung_anh_lon():
    # GIF màn hình logic 10x10 nhưng Image Descriptor 13000x13000
    data = tao_gif_co_khung(10, 10, [(13000, 13000)])
    with pytest.raises(LoiTep) as exc:
        kiem_anh(data, canh_toi_da=8192)
    assert exc.value.ma == "canh_qua_lon"


def test_b3_gif_nhieu_khung_tong_diem_anh_vuot_tran():
    # 200 khung 500x500 = 50.000.000 điểm ảnh (> trần 40.000.000)
    data = tao_gif_co_khung(500, 500, [(500, 500)] * 200)
    with pytest.raises(LoiTep) as exc:
        kiem_anh(data, canh_toi_da=8192, diem_anh_toi_da=40_000_000)
    assert exc.value.ma == "diem_anh_qua_lon"


def test_b6_jpeg_quet_marker_chua_nhieu_byte_khong():
    # JPEG có nhiều byte 0x00 trước marker SOF0 vẫn phân tích nhanh và thành công
    soi = b"\xff\xd8"
    dem_zeros = b"\x00" * (100 * 1024)  # 100 KB zeros
    sof0_payload = struct.pack(">BHHB", 8, 400, 600, 1) + b"\x01\x11\x00"
    sof0 = b"\xff\xc0" + struct.pack(">H", len(sof0_payload) + 2) + sof0_payload
    eoi = b"\xff\xd9"
    data = soi + dem_zeros + sof0 + eoi

    kq = kiem_anh(data)
    assert kq["loai"] == "jpeg"
    assert kq["rong"] == 600
    assert kq["cao"] == 400


def test_ta4_kiem_zip_khai_gian_kich_thuoc():
    """TA4: Lệch file_size so với số byte giải nén thật (so_byte_muc != info.file_size); đột biến phải đỏ."""
    # Tạo tệp zip với DEFLATE: dữ liệu thật 100 bytes
    that = b"B" * 100
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("gian_lan.txt", that)
    zip_bytes = bytearray(buf.getvalue())

    # Sửa uncompressed_size (100 -> 200) trong Central Directory và Local Header
    len_that = struct.pack("<I", 100)
    len_gia = struct.pack("<I", 200)

    idx = 0
    while True:
        pos = zip_bytes.find(len_that, idx)
        if pos == -1:
            break
        zip_bytes[pos:pos + 4] = len_gia
        idx = pos + 4

    with pytest.raises(LoiTep) as exc:
        kiem_zip(bytes(zip_bytes))
    assert exc.value.ma == "khai_gian_kich_thuoc"


test_b2_kiem_zip_khai_gian_kich_thuoc = test_ta4_kiem_zip_khai_gian_kich_thuoc


def test_b2_kiem_zip_chan_zip_long():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("tep_ngoai.txt", b"ngoai")
        zf.writestr("chua_ben_trong/con.zip", b"PK\x03\x04noi_dung_con")
    data = buf.getvalue()

    with pytest.raises(LoiTep) as exc:
        kiem_zip(data)
    assert exc.value.ma == "zip_long"


def test_b2_kiem_zip_chan_reparse_point_windows():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("reparse_link")
        # FILE_ATTRIBUTE_REPARSE_POINT (0x0400)
        info.external_attr = 0x0400
        zf.writestr(info, "target_path")
    data = buf.getvalue()

    with pytest.raises(LoiTep) as exc:
        kiem_zip(data)
    assert exc.value.ma == "symlink"


def test_b2_kiem_zip_chan_tran_byte_vao():
    du_lieu = b"PK\x03\x04" + b"\x00" * 2000
    with pytest.raises(LoiTep) as exc:
        kiem_zip(du_lieu, tran_byte_vao=1000)
    assert exc.value.ma == "kich_thuoc_vao_qua_lon"


def test_b2_kiem_zip_nhieu_muc_60000():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(2005):
            zf.writestr(f"f_{i}.txt", b"a")
    data = buf.getvalue()

    with pytest.raises(LoiTep) as exc:
        kiem_zip(data, so_muc_toi_da=2000)
    assert exc.value.ma == "so_muc_qua_lon"
