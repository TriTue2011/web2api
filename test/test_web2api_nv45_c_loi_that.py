"""NV45 (B): các test ĐỎ lộ lỗi thật (không sửa app/).

Mỗi test ĐỎ được đánh dấu pytest.mark.xfail(strict=True, reason=...):
- hiện tại test THẤT BẠI (lỗi còn đó) -> pytest báo xfail, bộ test chính xanh;
- khi lỗi được sửa, test thành công -> XPASS(strict) làm đỏ bộ test,
  báo hiệu đã đến lúc gỡ xfail.

Lỗi thật đã kiểm chứng (nền web2api-main-4dda468):

F1 — PUT /api/thiet-bi/{id}/rui-ro với rui_ro không băm được (list/dict/
lồng sâu/khoá JSON trùng) -> 500 TypeError: unhashable type tại
app/tuyen.py:1679 (`rui_ro not in RUI_RO_HOP_LE`, RUI_RO_HOP_LE là set).

F2 — nhan chứa lone surrogate (U+D800) qua POST/PATCH /api/khoa-khach ->
500 UnicodeEncodeError khi ghi kho (khoa_khach.py:795/907 -> _ghi_ra_dia_khoi_tao
-> kho.py:72 ghi_nguyen_tu). NGHIÊM TRỌNG HƠN: bản ghi độc đã vào bộ nhớ
trước khi ghi đĩa hỏng -> kho TRONG BỘ NHỚ bị nhiễm độc: mọi lần ghi khoá
khách sau đó (kể cả hợp lệ) đều 500 cho đến khi khởi động lại, trong khi
đĩa không có bản ghi đó (lệch bộ nhớ/đĩa).

Các test XANH khẳng định phòng thủ đã có (giữ nguyên khi sửa).
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

KHOA_QT = "khoa-quan-tri-nv45-loi-that-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
JSON = {"Content-Type": "application/json"}
RUI_RO = "/api/thiet-bi/khong-co-thuc-the/rui-ro"


def _long_nhau(n):
    d = {"x": 1}
    for _ in range(n):
        d = {"x": d}
    return d


def _tho(body, truong, tho: bytes):
    """Dựng thân JSON bytes, chèn giá trị thô (NaN/Infinity/\\ud800)."""
    parts = []
    for k, v in body.items():
        gt = tho if k == truong else json.dumps(v).encode("utf-8")
        parts.append(json.dumps(k).encode("utf-8") + b": " + gt)
    return b"{" + b", ".join(parts) + b"}"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("CHATGPT2API_AUTH_KEY", "test-auth")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def sach_khoa_khach(client):
    """Chụp/khôi phục trạng thái bộ nhớ của kho khoá khách (tránh nhiễm độc lan sang test khác)."""
    from app import khoa_khach
    truoc = (list(khoa_khach._KHOA_KHACH_CACHE), khoa_khach._CO_THAY_DOI,
             khoa_khach._CACHE_FILE_STAT)
    yield
    khoa_khach._KHOA_KHACH_CACHE[:] = truoc[0]
    khoa_khach._CO_THAY_DOI = truoc[1]
    khoa_khach._CACHE_FILE_STAT = truoc[2]


# ─────────────────────────── ĐỎ: F1 (rui_ro) ───────────────────────────

@pytest.mark.xfail(strict=True, reason="NV45 F1: rui_ro=[] -> 500 TypeError unhashable (app/tuyen.py:1679)")
def test_rui_ro_list_khong_500(client):
    r = client.put(RUI_RO, json={"rui_ro": []}, headers=QT)
    assert r.status_code != 500, r.text[:200]


@pytest.mark.xfail(strict=True, reason="NV45 F1: rui_ro={} -> 500 TypeError unhashable (app/tuyen.py:1679)")
def test_rui_ro_dict_khong_500(client):
    r = client.put(RUI_RO, json={"rui_ro": {}}, headers=QT)
    assert r.status_code != 500, r.text[:200]


@pytest.mark.xfail(strict=True, reason="NV45 F1: rui_ro lồng 300 tầng -> 500 (app/tuyen.py:1679)")
def test_rui_ro_long_sau_khong_500(client):
    r = client.put(RUI_RO, json={"rui_ro": _long_nhau(300)}, headers=QT)
    assert r.status_code != 500, r.text[:200]


@pytest.mark.xfail(strict=True, reason="NV45 F1: khoá JSON trùng -> rui_ro=[] -> 500 (app/tuyen.py:1679)")
def test_rui_ro_khoa_trung_khong_500(client):
    r = client.put(RUI_RO, content=b'{"rui_ro": "doc", "rui_ro": []}',
                   headers={**QT, **JSON})
    assert r.status_code != 500, r.text[:200]


@pytest.mark.xfail(strict=True, reason="NV45 F1: rui_ro lồng 1000 tầng (thô) -> 500 (app/tuyen.py:1679)")
def test_rui_ro_long_1000_tho_khong_500(client):
    raw = b'{"rui_ro": ' + ('{"x":' * 1000).encode() + b'1' + ('}' * 1000).encode() + b'}'
    r = client.put(RUI_RO, content=raw, headers={**QT, **JSON})
    assert r.status_code != 500, r.text[:200]


# ─────────────────────── ĐỎ: F2 (surrogate -> nhiễm độc kho) ───────────────────────

@pytest.mark.xfail(strict=True, reason="NV45 F2: tao khoa nhan lone-surrogate -> 500 UnicodeEncodeError (khoa_khach.py:795 -> kho.py:72)")
def test_khoa_khach_tao_nhan_surrogate_khong_500(client, sach_khoa_khach):
    r = client.post("/api/khoa-khach",
                    content=_tho({"nhan": "x", "pham_vi": ["*"]}, "nhan", b'"x\\ud800y"'),
                    headers={**QT, **JSON})
    assert r.status_code != 500, r.text[:200]


@pytest.mark.xfail(strict=True, reason="NV45 F2: sua khoa nhan lone-surrogate -> 500 UnicodeEncodeError (khoa_khach.py:907 -> kho.py:72)")
def test_khoa_khach_sua_nhan_surrogate_khong_500(client, sach_khoa_khach):
    from app import khoa_khach
    kid = khoa_khach.tao_khoa(nhan="sach", pham_vi=["*"])[0]["id"]
    r = client.patch(f"/api/khoa-khach/{kid}",
                     content=_tho({"nhan": "x"}, "nhan", b'"x\\ud800y"'),
                     headers={**QT, **JSON})
    assert r.status_code != 500, r.text[:200]


@pytest.mark.xfail(strict=True, reason="NV45 F2: sau khi nhan surrogate làm hỏng 1 lần ghi, mọi ghi khoá khách sau (kể cả hợp lệ) đều 500 — DoS kéo dài tới khi khởi động lại")
def test_khoa_khach_nhiem_doc_lan_sau(client, sach_khoa_khach):
    from app import khoa_khach
    kid = khoa_khach.tao_khoa(nhan="sach", pham_vi=["*"])[0]["id"]
    r1 = client.post("/api/khoa-khach",
                     content=_tho({"nhan": "x", "pham_vi": ["*"]}, "nhan", b'"x\\ud800y"'),
                     headers={**QT, **JSON})
    assert r1.status_code == 500  # bước nhiễm độc: đang 500
    # Thao tác HOÀN TOÀN HỢP LỆ sau đó vẫn 500 vì kho trong bộ nhớ đã nhiễm độc:
    r2 = client.patch(f"/api/khoa-khach/{kid}", json={"bat": True}, headers=QT)
    assert r2.status_code != 500, "khoá hợp lệ bị 500 sau khi kho nhiễm độc: " + r2.text[:200]


# ─────────────────────────── XANH: phòng thủ đã có ───────────────────────────

@pytest.mark.parametrize("tho", [b"Infinity", b"NaN", b'"x\\ud800y"'])
def test_dang_nhap_gia_tri_tho_khong_500(client, tho):
    body = {"ten_dang_nhap": "khong-co-nguoi-nay", "mat_khau": "Matkhau123456"}
    r = client.post("/api/dang-nhap", content=_tho(body, "ten_dang_nhap", tho),
                    headers={**QT, **JSON})
    assert r.status_code != 500


def test_dang_nhap_chuoi_5mb_khong_treo(client):
    r = client.post("/api/dang-nhap",
                    json={"ten_dang_nhap": "x" * 5_000_000, "mat_khau": "Matkhau123456"},
                    headers=QT)
    assert r.status_code != 500


@pytest.mark.parametrize("than,ma_cho", [([1, 2], 400), ("abc", 400), (5, 400)])
def test_than_khong_phai_dict_bi_tu_choi(client, than, ma_cho):
    r = client.post("/api/dang-nhap", json=than, headers=QT)
    assert r.status_code == ma_cho, r.text[:200]


def test_long_sau_1000_tho_khong_treo(client):
    raw = ('{"ten_dang_nhap":' + '{"x":' * 1000 + '1' + '}' * 1000 + '}').encode()
    r = client.post("/api/dang-nhap", content=raw, headers={**QT, **JSON})
    assert r.status_code != 500


@pytest.mark.parametrize("tho", [b"Infinity", b"NaN"])
def test_rui_ro_infinity_nan_tra_400_khong_500(client, tho):
    r = client.put(RUI_RO, content=_tho({"rui_ro": "doc"}, "rui_ro", tho),
                   headers={**QT, **JSON})
    assert r.status_code != 500
    assert r.status_code == 400, r.text[:200]
