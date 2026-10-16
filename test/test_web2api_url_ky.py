"""Kiểm thử app/url_ky.py: URL ký cho media và đường dẫn tệp an toàn."""

import os
import stat
import time
import urllib.parse
from pathlib import Path

import pytest

from app.url_ky import _lay_khoa_ky, chuan_hoa_duong, duong_tep_an_toan, kiem_chu_ky, ky_url


def test_khoa_ky_duoc_luu_quyen_0600(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    khoa = _lay_khoa_ky()
    assert len(khoa) == 32

    tep_khoa = tmp_path / "bi_mat" / "khoa_url_ky"
    assert tep_khoa.is_file()
    # Kiểm tra quyền 0600 (chỉ user đọc/ghi)
    quyen = stat.S_IMODE(tep_khoa.stat().st_mode)
    assert quyen == 0o600


def test_ky_va_kiem_chu_ky_thanh_cong():
    duong = "/media/anh_chup_001.png"
    url_da_ky = ky_url(duong, het_han_giay=300, phuong_thuc="GET")

    assert "_het_han=" in url_da_ky
    assert "_ky=" in url_da_ky

    # Kiểm tra bằng URL đầy đủ
    assert kiem_chu_ky("GET", url_da_ky)

    # Kiểm tra bằng đường dẫn + dict tham số
    parsed = urllib.parse.urlsplit(url_da_ky)
    qs = urllib.parse.parse_qs(parsed.query)
    assert kiem_chu_ky("GET", duong, qs)


def test_kiem_chu_ky_sai_chu_ky_tra_ve_false():
    duong = "/media/anh_chup_001.png"
    url_da_ky = ky_url(duong, het_han_giay=300, phuong_thuc="GET")

    # Sửa 1 ký tự trong chữ ký _ky
    idx = url_da_ky.find("_ky=")
    char_cu = url_da_ky[idx + 4]
    char_moi = "0" if char_cu != "0" else "1"
    url_gia = url_da_ky[:idx + 4] + char_moi + url_da_ky[idx + 5:]

    assert not kiem_chu_ky("GET", url_gia)


def test_kiem_chu_ky_het_han_tra_ve_false(monkeypatch):
    duong = "/media/video_001.mp4"
    # Giả lập ký với hạn 10 giây
    url_da_ky = ky_url(duong, het_han_giay=10, phuong_thuc="GET")

    # Tua thời gian tiến thêm 20 giây
    tuong_lai = time.time() + 20
    monkeypatch.setattr(time, "time", lambda: tuong_lai)

    assert not kiem_chu_ky("GET", url_da_ky)


def test_kiem_chu_ky_sai_duong_dan_tra_ve_false():
    duong1 = "/media/anh_1.png"
    duong2 = "/media/anh_2.png"
    url_da_ky1 = ky_url(duong1, het_han_giay=300, phuong_thuc="GET")

    # Lấy tham số của duong1 đem áp vào duong2
    parsed = urllib.parse.urlsplit(url_da_ky1)
    url_trang_trao = f"{duong2}?{parsed.query}"

    assert not kiem_chu_ky("GET", url_trang_trao)


def test_kiem_chu_ky_sai_phuong_thuc_tra_ve_false():
    duong = "/media/tep.bin"
    url_da_ky = ky_url(duong, het_han_giay=300, phuong_thuc="GET")

    # Ký bằng GET nhưng gọi bằng POST hoặc DELETE
    assert not kiem_chu_ky("POST", url_da_ky)
    assert not kiem_chu_ky("DELETE", url_da_ky)


def test_kiem_chu_ky_cat_ngan_hoac_thieu_tham_so():
    duong = "/media/tep.bin"
    url_da_ky = ky_url(duong, het_han_giay=300, phuong_thuc="GET")

    # Bỏ _ky
    parsed = urllib.parse.urlsplit(url_da_ky)
    qs = urllib.parse.parse_qs(parsed.query)
    qs_thieu_ky = {"_het_han": qs["_het_han"]}
    assert not kiem_chu_ky("GET", duong, qs_thieu_ky)

    # Bỏ _het_han
    qs_thieu_han = {"_ky": qs["_ky"]}
    assert not kiem_chu_ky("GET", duong, qs_thieu_han)

    # Không có tham số nào
    assert not kiem_chu_ky("GET", duong, {})
    assert not kiem_chu_ky("GET", duong)


def test_kiem_chu_ky_dao_tham_so_va_tham_so_phu():
    duong = "/media/anh.jpg?chieu_rong=200"
    url_da_ky = ky_url(duong, het_han_giay=300, phuong_thuc="GET")

    # B4: Thêm, đổi hoặc bớt tham số query PHẢI trả về False
    parsed = urllib.parse.urlsplit(url_da_ky)
    qs = urllib.parse.parse_qs(parsed.query)

    # 1. Thêm tham số ngoài
    qs_them = dict(qs)
    qs_them["tag"] = ["evil"]
    assert not kiem_chu_ky("GET", "/media/anh.jpg", qs_them)

    # 2. Đổi giá trị tham số đã ký (200 -> 9999)
    qs_doi = dict(qs)
    qs_doi["chieu_rong"] = ["9999"]
    assert not kiem_chu_ky("GET", "/media/anh.jpg", qs_doi)

    # 3. Bớt tham số đã ký
    qs_bot = {k: v for k, v in qs.items() if k != "chieu_rong"}
    assert not kiem_chu_ky("GET", "/media/anh.jpg", qs_bot)


def test_b5_so_sanh_chu_ky_non_ascii_khong_nem_typeerror():
    # B5: Khách gửi _ky chứa ký tự non-ASCII không được gây lỗi 500 (TypeError)
    assert not kiem_chu_ky("GET", "/media/anh.png", {"_het_han": "1792131981", "_ky": "chữ_ký_sai_unicode"})
    assert not kiem_chu_ky("GET", "/media/anh.png", {"_het_han": "1792131981", "_ky": "⚡🔥"})


def test_b10_fragment_va_tran_han_dung():
    # B10: URL có fragment #frag -> không để tham số ký sau #
    url_ky = ky_url("/media/anh.png#frag", het_han_giay=300)
    assert "#frag" not in url_ky
    assert url_ky.startswith("/media/anh.png?")
    assert kiem_chu_ky("GET", url_ky)

    # het_han_giay không hợp lệ
    import math
    for sai in (0, -10, math.nan, math.inf, "300", None):
        with pytest.raises(ValueError):
            ky_url("/media/anh.png", het_han_giay=sai)  # type: ignore

    # Vượt trần 7 ngày
    with pytest.raises(ValueError, match="vượt trần tối đa"):
        ky_url("/media/anh.png", het_han_giay=8 * 86400)


def test_b11_chuan_hoa_nghiem_ngat_va_het_han_format():
    # Đường dẫn hợp lệ
    assert chuan_hoa_duong("/media/anh.png") == "/media/anh.png"
    assert chuan_hoa_duong("media/anh.png") == "/media/anh.png"
    assert chuan_hoa_duong("/media/anh.png?param=1#frag") == "/media/anh.png"

    # B11: Từ chối //, .., ., và byte không hợp lệ UTF-8
    with pytest.raises(ValueError, match="chứa '//'"):
        chuan_hoa_duong("/media//anh.png")

    with pytest.raises(ValueError, match="chứa '.' hoặc '..'"):
        chuan_hoa_duong("/media/sub/../anh.png")

    with pytest.raises(ValueError, match="chứa '.' hoặc '..'"):
        chuan_hoa_duong("/media/%2e%2e/anh.png")

    with pytest.raises(ValueError, match="chứa '.' hoặc '..'"):
        chuan_hoa_duong("/media/anh.png/.")

    with pytest.raises(ValueError, match="byte UTF-8 không hợp lệ"):
        chuan_hoa_duong("/media/%ff/anh.png")

    # kiem_chu_ky với đường dẫn vi phạm trả về False
    assert not kiem_chu_ky("GET", "/media//anh.png?_het_han=1792131981&_ky=" + "0" * 64)
    assert not kiem_chu_ky("GET", "/media/sub/../anh.png?_het_han=1792131981&_ky=" + "0" * 64)

    # _het_han format lạ (khoảng trắng, dấu +, gạch dưới, Unicode digits) => False
    assert not kiem_chu_ky("GET", "/media/anh.png", {"_het_han": " 1792131981", "_ky": "0" * 64})
    assert not kiem_chu_ky("GET", "/media/anh.png", {"_het_han": "+1792131981", "_ky": "0" * 64})
    assert not kiem_chu_ky("GET", "/media/anh.png", {"_het_han": "179_2131981", "_ky": "0" * 64})
    assert not kiem_chu_ky("GET", "/media/anh.png", {"_het_han": "١٢٣٤٥٦", "_ky": "0" * 64})


def test_b12_tep_khoa_loi_do_dai(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    from app.url_ky import _KHOA_URL_KY_CACHE
    import app.url_ky
    app.url_ky._KHOA_URL_KY_CACHE = None

    tep_khoa = tmp_path / "bi_mat" / "khoa_url_ky"
    tep_khoa.parent.mkdir(parents=True, exist_ok=True)
    # Ghi tệp khoá sai độ dài (chỉ 16 bytes thay vì 32)
    tep_khoa.write_bytes(b"0123456789abcdef")

    with pytest.raises(ValueError, match="lỗi độ dài"):
        _lay_khoa_ky()


def test_duong_tep_an_toan(tmp_path):
    goc = tmp_path / "media_storage"
    goc.mkdir()

    # Tệp hợp lệ
    tep1 = duong_tep_an_toan(goc, "anh.png")
    assert tep1 == goc / "anh.png"

    tep2 = duong_tep_an_toan(goc, "sub/dir/hinh.jpg")
    assert tep2 == goc / "sub" / "dir" / "hinh.jpg"

    # Thử thoát ra ngoài thư mục gốc bằng ..
    with pytest.raises(ValueError, match="đường dẫn không an toàn"):
        duong_tep_an_toan(goc, "../bi_mat/khoa_url_ky")

    with pytest.raises(ValueError, match="đường dẫn không an toàn"):
        duong_tep_an_toan(goc, "../../etc/passwd")

    with pytest.raises(ValueError, match="đường dẫn không an toàn"):
        duong_tep_an_toan(goc, "/etc/passwd")

    # Chính thư mục gốc cũng bị từ chối (phải là tệp dưới thư mục gốc)
    with pytest.raises(ValueError, match="đường dẫn không an toàn"):
        duong_tep_an_toan(goc, "")


def test_khong_lo_khoa_quan_tri_trong_url():
    duong = "/media/tai_lieu.pdf"
    url_da_ky = ky_url(duong, het_han_giay=300, phuong_thuc="GET")
    assert "admin_key" not in url_da_ky
    assert "w2a_" not in url_da_ky
    assert "Authorization" not in url_da_ky


def test_uk4_kiem_chu_ky_dung_thoi_gian_hang(monkeypatch):
    """UK4: kiem_chu_ky so sánh chữ ký bằng hmac.compare_digest; đột biến == phải đỏ."""
    import hmac
    import app.url_ky as url_ky_mod
    that_compare = hmac.compare_digest
    so_lan_goi = 0

    def dem_compare(a, b):
        nonlocal so_lan_goi
        so_lan_goi += 1
        return that_compare(a, b)

    monkeypatch.setattr(url_ky_mod.hmac, "compare_digest", dem_compare)

    duong = "/media/anh.png"
    url_dung = ky_url(duong, het_han_giay=300, phuong_thuc="GET")

    # 1. Gọi với chữ ký ĐÚNG
    so_lan_goi = 0
    assert kiem_chu_ky("GET", url_dung) is True
    assert so_lan_goi >= 1, "hmac.compare_digest phải được gọi khi chữ ký đúng"

    # 2. Gọi với chữ ký SAI
    so_lan_goi = 0
    idx = url_dung.find("_ky=")
    char_cu = url_dung[idx + 4]
    char_moi = "0" if char_cu != "0" else "1"
    url_sai = url_dung[:idx + 4] + char_moi + url_dung[idx + 5:]
    assert kiem_chu_ky("GET", url_sai) is False
    assert so_lan_goi >= 1, "hmac.compare_digest phải được gọi khi chữ ký sai"


def test_uk6_chuan_hoa_query_chuan_tac(monkeypatch):
    """UK6: Tham số query đảo thứ tự, lặp, rỗng, %20 vs + phải cho chữ ký giống nhau và kiểm hợp lệ."""
    # Cố định thời gian để hai lần ký sinh cùng _het_han
    moc_tg = 1700000000.0
    monkeypatch.setattr(time, "time", lambda: moc_tg)

    # 1. ?a=1&b=2 và ?b=2&a=1 phải cho chữ ký GIỐNG nhau và cả hai kiem_chu_ky = True
    u_ab = ky_url("/media/anh.png?a=1&b=2", het_han_giay=300)
    u_ba = ky_url("/media/anh.png?b=2&a=1", het_han_giay=300)

    parsed_ab = urllib.parse.urlsplit(u_ab)
    parsed_ba = urllib.parse.urlsplit(u_ba)
    qs_ab = urllib.parse.parse_qs(parsed_ab.query)
    qs_ba = urllib.parse.parse_qs(parsed_ba.query)

    assert qs_ab["_ky"][0] == qs_ba["_ky"][0], "Chữ ký phải giống nhau khi tham số khác thứ tự"
    assert kiem_chu_ky("GET", u_ab) is True
    assert kiem_chu_ky("GET", u_ba) is True

    # Tráo query string nhưng giữ chữ ký: URL gọi bằng ?b=2&a=1 với _ky của u_ab vẫn True
    u_trao = f"/media/anh.png?b=2&a=1&_het_han={qs_ab['_het_han'][0]}&_ky={qs_ab['_ky'][0]}"
    assert kiem_chu_ky("GET", u_trao) is True

    # 2. Tham số lặp: a=1&a=2 và a=2&a=1 khác thứ tự
    u_lap1 = ky_url("/media/anh.png?a=1&a=2", het_han_giay=300)
    u_lap2 = ky_url("/media/anh.png?a=2&a=1", het_han_giay=300)
    qs_lap1 = urllib.parse.parse_qs(urllib.parse.urlsplit(u_lap1).query)
    qs_lap2 = urllib.parse.parse_qs(urllib.parse.urlsplit(u_lap2).query)
    assert qs_lap1["_ky"][0] == qs_lap2["_ky"][0], "Chữ ký phải giống nhau với tham số lặp khác thứ tự"
    assert kiem_chu_ky("GET", u_lap1) is True
    assert kiem_chu_ky("GET", u_lap2) is True

    # 3. Giá trị rỗng: a=&b=2 và b=2&a=
    u_rong1 = ky_url("/media/anh.png?a=&b=2", het_han_giay=300)
    u_rong2 = ky_url("/media/anh.png?b=2&a=", het_han_giay=300)
    qs_rong1 = urllib.parse.parse_qs(urllib.parse.urlsplit(u_rong1).query)
    qs_rong2 = urllib.parse.parse_qs(urllib.parse.urlsplit(u_rong2).query)
    assert qs_rong1["_ky"][0] == qs_rong2["_ky"][0], "Chữ ký phải giống nhau với giá trị rỗng"
    assert kiem_chu_ky("GET", u_rong1) is True
    assert kiem_chu_ky("GET", u_rong2) is True

    # 4. Mã hoá %20 vs +: parse_qsl đều chuẩn hoá thành dấu cách và urlencode thành +
    u_space_pct = ky_url("/media/anh.png?q=hello%20world", het_han_giay=300)
    u_space_plus = ky_url("/media/anh.png?q=hello+world", het_han_giay=300)
    qs_pct = urllib.parse.parse_qs(urllib.parse.urlsplit(u_space_pct).query)
    qs_plus = urllib.parse.parse_qs(urllib.parse.urlsplit(u_space_plus).query)
    assert qs_pct["_ky"][0] == qs_plus["_ky"][0], "Chữ ký phải giống nhau giữa %20 và +"
    assert kiem_chu_ky("GET", u_space_pct) is True
    assert kiem_chu_ky("GET", u_space_plus) is True

