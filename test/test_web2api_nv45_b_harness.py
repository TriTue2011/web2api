"""NV45 (B): harness tự động — gửi giá trị độc vào TỪNG trường của các route
POST/PUT/PATCH/DELETE và assert không có 500/treo/timeout.

Phạm vi: 25 route (16 từ sweep gốc + 9 mở rộng: v1/chat, v1/embeddings,
v1/rerank, sao-luu, modules/model, modules/tu-xa, thiet-bi/hanh-dong,
phe-duyet, tim-kiem/muc-luc, gui, webhook, phien/kiem, thiet-lap) + thân thô
(NaN/Infinity/khoá trùng/lồng sâu 1000) + query độc (DELETE /api/nhat-ky).

Các ca ĐỎ đã biết KHÔNG chạy ở đây (tránh nhiễm độc kho dùng chung và nhiễu):
chúng nằm trong test/nv45_c_loi_that.py dưới dạng xfail(strict=True):
- rui_ro là list/dict/lồng sâu/khoá trùng -> 500 (app/tuyen.py:1679)
- nhan chứa lone surrogate (tao/sua khoa-khach) -> 500 + nhiễm độc kho

Infinity/NaN/lone-surrogate được gửi dạng bytes thô: client httpx từ chối
serialize chúng (giới hạn client, không phải lỗi server), nên chỉ có gửi thô
mới kiểm được server thật.

Nền: web2api-main-4dda468. Không sửa app/.
"""
import copy
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app

KHOA_QT = "khoa-quan-tri-nv45-harness-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
JSON = {"Content-Type": "application/json"}


def _long_nhau(n):
    d = {"x": 1}
    for _ in range(n):
        d = {"x": d}
    return d


# Giá trị độc; ba giá trị đầu nhóm THO được gửi dạng bytes thô.
GIA_TRI_DOC = [
    ("null", None, None),
    ("true", True, None),
    ("0", 0, None),
    ("-1", -1, None),
    ("1e999", 1e999, b"Infinity"),
    ("NaN", float("nan"), b"NaN"),
    ("[]", [], None),
    ("{}", {}, None),
    ("[[]]", [[]], None),
    ("10**400", 10 ** 400, None),
    ("unicode-la", "x\ud800\u202e\u200b\U0001f600y", b'"x\\ud800\\u202e\\u200b\360\237\230\200y"'),
    ("long-sau-300", _long_nhau(300), None),
    ("chuoi-5MB", "x" * 5_000_000, None),
]

# (method, path, body_hop_le, [fields])
ROUTES = [
    ("POST", "/api/dang-nhap", {"ten_dang_nhap": "khong-co-nguoi-nay", "mat_khau": "Matkhau123456"},
     ["ten_dang_nhap", "mat_khau"]),
    ("POST", "/api/nguoi-dung", {"ten_dang_nhap": "nv45u", "mat_khau": "Matkhau123456", "vai": "user"},
     ["ten_dang_nhap", "mat_khau", "vai", "pham_vi_toi_da"]),
    ("PATCH", "/api/nguoi-dung/{uid}", {"vai": "user", "bat": True},
     ["vai", "bat", "pham_vi_toi_da"]),
    ("POST", "/api/khoa-khach", {"nhan": "nv45k", "pham_vi": ["*"]},
     ["nhan", "pham_vi", "het_han"]),
    ("PATCH", "/api/khoa-khach/{kid}", {"bat": True, "nhan": "nv45k2"},
     ["bat", "nhan", "pham_vi", "het_han"]),
    ("POST", "/api/khoa-khach/{kid}/xoay", {}, ["cu_giay"]),
    ("PUT", "/api/dia-chi", {"url": None}, ["url"]),
    ("PUT", "/api/nguon-git", {"chu_ky_giay": 3600}, ["chu_ky_giay", "token", "repo"]),
    ("PUT", "/api/thiet-bi/khong-co-thuc-the/rui-ro", {"rui_ro": "nguy_hiem"}, ["rui_ro"]),
    ("POST", "/api/thiet-bi/su-kien", {"thuc_the": "khong-co", "loai": "trang_thai"},
     ["thuc_the", "loai", "du_lieu"]),
    ("POST", "/api/trinh-duyet/khong-co-module/mo", {"loai": "Chromium"},
     ["loai", "ho_so", "url"]),
    ("POST", "/api/tim-kiem", {"truy_van": "xin chao"}, ["truy_van", "gioi_han"]),
    ("PUT", "/api/combo/nv45-combo", {"mo_ta": "combo test"}, ["mo_ta", "du_lieu"]),
    ("POST", "/v1/chat/completions",
     {"model": "khong-co/model", "messages": [{"role": "user", "content": "hi"}]},
     ["model", "messages", "stream", "temperature", "max_tokens"]),
    ("POST", "/v1/embeddings", {"model": "khong-co/model", "input": "hi"}, ["model", "input"]),
    ("POST", "/v1/rerank", {"model": "khong-co/model", "query": "hi", "documents": ["a"]},
     ["model", "query", "documents"]),
    ("POST", "/api/sao-luu", {"kem_phien": False}, ["kem_phien", "mat_khau"]),
    ("PUT", "/api/modules/khong-co-module/model", {"tat": [], "mac_dinh": None},
     ["tat", "mac_dinh"]),
    ("POST", "/api/modules/tu-xa",
     {"ten": "nv45tx", "tien_to": "nv45tx", "base_url": "http://127.0.0.1:9/v1"},
     ["ten", "tien_to", "base_url", "api_key"]),
    ("POST", "/api/thiet-bi/khong-co-thuc-the/hanh-dong", {"hanh_dong": "bat"},
     ["hanh_dong", "tham_so", "xac_nhan", "ly_do"]),
    ("POST", "/api/phe-duyet/khong-co-pd/duyet", {}, []),
    ("POST", "/api/phe-duyet/khong-co-pd/tu-choi", {}, []),
    ("POST", "/api/tim-kiem/muc-luc", {"k": "v"}, ["k"]),
    ("POST", "/api/gui/khong-co-module", {"kenh_id": "x", "noi_dung": {"text": "hi"}},
     ["kenh_id", "noi_dung"]),
    ("POST", "/api/webhook/khong-co/ma-sai", {"su_kien": "x"}, ["su_kien"]),
    ("POST", "/api/phien/khong-co/khong-co/kiem", {}, []),
    ("POST", "/api/thiet-lap", {}, []),
]

# (method, path, label) các ca đã biết ĐỎ -> bỏ qua ở sweep, có test riêng xfail.
BO_QUA = {
    ("PUT", "/api/thiet-bi/khong-co-thuc-the/rui-ro", "rui_ro",
     frozenset({"[]", "{}", "[[]]", "long-sau-300"})),
    ("POST", "/api/khoa-khach", "nhan", frozenset({"unicode-la"})),
    ("PATCH", "/api/khoa-khach/{kid}", "nhan", frozenset({"unicode-la"})),
}

# Thân thô gửi thêm (không qua từng trường).
THAN_THO = [
    ("POST", "/api/dang-nhap", b'{"ten_dang_nhap": NaN, "mat_khau": "x"}', "NaN-tho"),
    ("POST", "/api/dang-nhap", b'{"a": Infinity}', "Infinity-tho"),
    ("POST", "/api/dang-nhap", b'{"ten_dang_nhap": "a", "ten_dang_nhap": ["x"]}', "khoa-trung"),
    ("POST", "/api/dang-nhap", ('{"a":' * 1000 + '1' + '}' * 1000).encode(), "long-sau-1000"),
    ("POST", "/v1/chat/completions", b'{"model": NaN, "messages": []}', "NaN-tho"),
    ("POST", "/v1/chat/completions",
     ('{"messages":' + '{"x":' * 1000 + '1' + '}' * 1000 + '}').encode(), "long-sau-1000"),
    ("POST", "/api/webhook/khong-co/ma-sai", b'{"su_kien": NaN}', "NaN-tho"),
    ("POST", "/api/webhook/khong-co/ma-sai",
     ('{"a":' * 1000 + '1' + '}' * 1000).encode(), "long-sau-1000"),
    ("POST", "/api/phien/khong-co/khong-co/kiem", b'{"a": NaN}', "NaN-tho"),
    ("POST", "/api/sao-luu", b'{"kem_phien": NaN}', "NaN-tho"),
    ("POST", "/api/sao-luu", b'{"kem_phien": false, "kem_phien": []}', "khoa-trung"),
]


def _body_tho(body_mau, truong, tho: bytes):
    parts = []
    for k, v in body_mau.items():
        gt = tho if k == truong else json.dumps(v, default=str).encode("utf-8")
        parts.append(json.dumps(k).encode("utf-8") + b": " + gt)
    return b"{" + b", ".join(parts) + b"}"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("CHATGPT2API_AUTH_KEY", "test-auth")
    from app import nguoi_dung, khoa_khach
    import asyncio
    # with để chạy lifespan: app.state.so được dựng lúc khởi động.
    with TestClient(app, raise_server_exceptions=False) as c:
        uid = asyncio.run(nguoi_dung.tao_nguoi_dung(
            vai="user", ten_dang_nhap="nv45-muc-tieu", mat_khau="Matkhau123456"))[0]["id"]
        ban_ghi, _ = khoa_khach.tao_khoa(nhan="nv45-muc-tieu", pham_vi=["*"])
        c.uid, c.kid = uid, ban_ghi["id"]
        yield c


def test_harness_khong_500_treo(client):
    xau = []
    tong = 0
    for method, path_tmpl, body_mau, fields in ROUTES:
        path = path_tmpl.format(uid=client.uid, kid=client.kid)
        for truong in fields:
            bo_qua_docs = next(
                (b[3] for b in BO_QUA if (b[0], b[1], b[2]) == (method, path_tmpl, truong)),
                frozenset(),
            )
            for ten_doc, gia_tri, tho in GIA_TRI_DOC:
                if ten_doc in bo_qua_docs:
                    continue
                tong += 1
                body = copy.deepcopy(body_mau)
                body[truong] = gia_tri
                t0 = time.monotonic()
                try:
                    if tho is None:
                        r = client.request(method, path, json=body, headers=QT)
                    else:
                        r = client.request(method, path, content=_body_tho(body, truong, tho),
                                           headers={**QT, **JSON})
                    ma = r.status_code
                except Exception as e:  # noqa: BLE001 - ghi nhận cả lỗi client
                    ma = f"LOI:{type(e).__name__}"
                dt = time.monotonic() - t0
                if ma == 500 or (isinstance(ma, str) and ma.startswith("LOI")) or dt > 10:
                    xau.append(f"{method} {path} truong={truong} doc={ten_doc} -> {ma} ({dt:.1f}s)")
        for ten_doc, than in [("list", [1, 2]), ("chuoi", "abc"), ("so", 5)]:
            tong += 1
            r = client.request(method, path, json=than, headers=QT)
            if r.status_code == 500:
                xau.append(f"{method} {path} than={ten_doc} -> 500")
    for method, path, raw, label in THAN_THO:
        tong += 1
        t0 = time.monotonic()
        r = client.request(method, path, content=raw, headers={**QT, **JSON})
        dt = time.monotonic() - t0
        ma = r.status_code
        if ma == 500 or dt > 10:
            xau.append(f"{method} {path} than-tho={label} -> {ma} ({dt:.1f}s)")
    # query độc
    for ten_doc, gt in [("chuoi", "abc"), ("am", "-5"), ("rat-lon", "99999999999999999999"),
                        ("float", "1.5"), ("NaN", "NaN")]:
        tong += 1
        r = client.delete("/api/nhat-ky", headers=QT, params={"truoc_ngay": gt})
        if r.status_code == 500:
            xau.append(f"DELETE /api/nhat-ky truoc_ngay={ten_doc} -> 500")
    assert tong > 700, f"sweep chạy thiếu: {tong}"
    assert not xau, f"{len(xau)} điểm xấu/{tong}:\n" + "\n".join(xau[:20])
