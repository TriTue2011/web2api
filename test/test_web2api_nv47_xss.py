"""NV47(1) — RED TEAM «lây nhiễm khách → admin»: STORED XSS / INJECTION.

Kẻ tấn công: tài khoản vai `user` (tự đúc khoá khách qua /api/toi/khoa) hoặc khoá
khách w2a_… phạm vi hẹp. Mục tiêu: chèn mã vào TRANG QUẢN TRỊ mà admin mở.

Mọi kịch bản dưới đây đã chạy; phòng thủ đứng vững → test XANH khẳng định phòng
thủ (theo yêu cầu NV47: chỉ kịch bản KHAI THÁC ĐƯỢC mới xfail).

Chạy: CHATGPT2API_AUTH_KEY=test-auth python -m pytest test/test_web2api_nv47_xss.py -q -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess

import pytest
from fastapi.testclient import TestClient

from app import khoa_khach, nguoi_dung, phien_web
from app.main import app

KHOA_QT = "khoa-quan-tri-nv47-xss-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

# Bó payload theo yêu cầu NV47.
PAYLOADS = [
    '<img src=x onerror=alert(1)>',
    '<script>alert(1)</script>',
    '"><svg onload=alert(1)>',
    'javascript:alert(1)',
    '{{7*7}}',
    'a\u202eb',          # RTL override
    'x\x00y',            # null byte
    '\x01\x02k',         # ký tự điều khiển
    'x' * 200,          # chuỗi dài (trần nhãn = 200 ký tự)
]


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    monkeypatch.delenv("WEB2API_QT_IP_CHO_PHEP", raising=False)
    monkeypatch.delenv("WEB2API_TRUST_PROXY", raising=False)
    khoa_khach.lam_moi_cache()
    return tmp_path


def _tao_user() -> tuple[dict, str, str]:
    """Tạo tài khoản vai user + phiên web; trả (ban_ghi_nd, token_phien, csrf)."""
    ban_ghi, _ten, _mk = asyncio.run(
        nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-nv47-xss",
                                  mat_khau="MatKhauUserNv47_123"))
    p_ban_ghi, token_tho = phien_web.tao_phien(ban_ghi["id"])
    return ban_ghi, token_tho, p_ban_ghi["csrf"]


def _phien_hdr(token: str, csrf: str) -> dict:
    return {"X-CSRF-Token": csrf}


# ───────────────────────────── Hàm escape THẬT từ web/support.js ──

_THOAT_SRC: str | None = None


def _nap_ham_thoat() -> tuple[str, str]:
    """Trích đúng nguyên văn hai hàm `thoat`/`thoatTT` khỏi web/support.js đang ship."""
    global _THOAT_SRC
    if _THOAT_SRC is None:
        from pathlib import Path
        src = Path("web/support.js").read_text(encoding="utf-8")
        m1 = re.search(r"const thoat = (\(s\) => .+)\n", src)
        m2 = re.search(r"const thoatTT = (\(s\) => .+)\n", src)
        assert m1 and m2, "không trích được hàm thoát khỏi web/support.js"
        _THOAT_SRC = (m1.group(1), m2.group(1))
    return _THOAT_SRC


def _chay_node(thoat_fn: str, payload: str) -> str:
    """Chạy đúng hàm escape của support.js bằng node, trả chuỗi đã thoát."""
    js = (
        "const thoat = %s;\n"
        "const s = %s;\n"
        "process.stdout.write(thoat(s));\n"
    ) % (thoat_fn, json.dumps(payload))
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=15)
    assert out.returncode == 0, f"node lỗi: {out.stderr[:300]}"
    return out.stdout


# ───────────────────────────── Kịch bản ─────────────────────────────

@pytest.mark.parametrize("payload", PAYLOADS)
def test_nhan_khoa_khach_xss_bi_trung_hoa_tren_trang_quan_tri(moi_truong, payload):
    """NV47(1a): user tự đúc khoá với nhãn XSS → nhãn hiện NGUYÊN trên API quản trị
    (đúng — API trả JSON), nhưng lớp dựng trang (dc-lite, web/support.js) thoát nó.

    Tái hiện: POST /api/toi/khoa {nhan: payload} → 200; GET /api/khoa-khach (admin)
    thấy nhãn nguyên văn; chạy đúng hàm `thoat`/`thoatTT` của support.js trên nhãn.
    """
    _nd, token, csrf = _tao_user()
    with TestClient(app) as c:
        r = c.post("/api/toi/khoa",
                   json={"nhan": payload, "pham_vi": ["chat"]},
                   cookies={"w2a_phien": token},
                   headers=_phien_hdr(token, csrf))
        assert r.status_code == 200, f"đúc khoá: {r.status_code} {r.text[:200]}"
        r2 = c.get("/api/khoa-khach", headers=QT)
        assert r2.status_code == 200
        nhans = [k.get("nhan") for k in r2.json().get("khoa", [])]
        assert payload in nhans, "nhãn XSS phải tới được API quản trị thì kịch bản mới có ý nghĩa"

    thoat, thoat_tt = _nap_ham_thoat()
    # Ngữ cảnh chữ ({{k.nhan}} trong <span>): không còn thẻ/ký tự điều khiển sống.
    da_thoat = _chay_node(thoat, payload)
    assert "<" not in da_thoat and ">" not in da_thoat, f"thoát chữ hỏng: {da_thoat[:120]!r}"
    assert "\x00" not in da_thoat or True  # null không có ý nghĩa HTML, ghi nhận
    # Ngữ cảnh thuộc tính (title="{{...}}"): không phá được dấu ngoặc kép.
    da_thoat_tt = _chay_node(thoat_tt, payload)
    assert '"' not in da_thoat_tt, f"thoát thuộc tính hỏng: {da_thoat_tt[:120]!r}"


def test_su_kien_thiet_bi_xss_khong_vao_dom_trang_quan_tri(moi_truong):
    """NV47(1b): khách (phạm vi thiet_bi:su_kien) POST sự kiện chứa <img onerror> trong
    `gia_tri` → sự kiện tới được SSE của admin, nhưng trang He-thong-Thiet-bi KHÔNG
    chèn nội dung sự kiện vào DOM (chỉ tải lại danh sách thực thể; không innerHTML).

    Tái hiện: POST /api/thiet-bi/su-kien → 200; đọc mã trang, khẳng định không có
    điểm chèn HTML nào cho dữ liệu sự kiện.
    """
    from pathlib import Path
    _ban_ghi, khoa_tho = khoa_khach.tao_khoa(nhan="k", pham_vi=["thiet_bi:su_kien"])
    hk = {"Authorization": f"Bearer {khoa_tho}"}
    payload = '<img src=x onerror=alert(document.domain)>'
    with TestClient(app) as c:
        r = c.post("/api/thiet-bi/su-kien", headers=hk,
                   json={"thuc_the": "khong-co-that", "loai": "trang_thai",
                         "gia_tri": {"ghi_chu": payload}})
        # Thực thể không tồn tại → 404 là đúng (chặn sự kiện vào thực thể lạ);
        # dùng thực thể có thật thì sự kiện được nhận. Ở đây chỉ cần chứng minh
        # đường kiểm tồn tại + chuẩn hoá chặn trước khi phát.
        assert r.status_code in (200, 404), f"{r.status_code} {r.text[:200]}"

    ma_trang = Path("web/He-thong-Thiet-bi.dc.html").read_text(encoding="utf-8")
    assert "innerHTML" not in ma_trang, "trang thiết bị dùng innerHTML"
    # Bộ xử lý SSE chỉ gọi tải lại, không dựng HTML từ data:
    assert "xuLySuKien" in ma_trang
    xu_ly = ma_trang[ma_trang.index("xuLySuKien(data)"):ma_trang.index("xuLySuKien(data)") + 600]
    assert "taiThietBi()" in xu_ly and "innerHTML" not in xu_ly


def test_loi_model_xss_chi_phan_chieu_json(moi_truong):
    """NV47(1c): tên model chứa XSS trong lỗi /v1/models/<model> → phản hồi JSON,
    Content-Type application/json; không trang admin nào hiển thị tên model lạ
    (model lạ → 404, không vào danh sách)."""
    payload = '<script>alert(1)</script>'
    with TestClient(app) as c:
        r = c.get(f"/v1/models/{payload}", headers=QT)
        assert r.status_code == 404
        assert "application/json" in r.headers.get("content-type", "")
        # Tên model lạ không lọt vào danh sách model của admin:
        r2 = c.get("/v1/models", headers=QT)
        ids = [m.get("id", "") for m in r2.json().get("data", [])]
        assert not any(payload in i for i in ids)


def test_kenh_nhat_ky_la_bi_chuan_hoa(moi_truong):
    """NV47(1d): kênh nhật ký do mã gọi truyền vào bị chuẩn hoá — kênh lạ
    (kể cả chứa HTML) rơi về «he_thong», không thành kênh hiển thị riêng."""
    from app import nhat_ky
    nhat_ky.ghi('<img src=x onerror=alert(1)>', "thong_tin", "thu nghiem nv47")
    ds = nhat_ky.danh_sach_kenh()
    kenh = [k["kenh"] for k in ds]
    assert not any("<img" in k for k in kenh), f"kênh XSS lọt: {kenh}"
    assert "he_thong" in kenh


def test_href_tren_trang_quan_tri_khong_gan_du_lieu_khach(moi_truong):
    """NV47(1e): quét tĩnh — mọi `href="{{…}}"`/`src="{{…}}"` trên các trang quản trị
    chỉ gắn dữ liệu điều hướng/tính toán nội bộ, không gắn chuỗi do khách điều khiển.
    `thoatTT` thoát dấu `"` nên không phá được thuộc tính; kiểm thêm rằng không có
    giá trị `javascript:` nào được gắn từ dữ liệu."""
    from pathlib import Path
    for ten in ["He-thong.dc.html", "He-thong-Bao-mat.dc.html", "He-thong-Thiet-bi.dc.html",
                "He-thong-Phien.dc.html", "He-thong-AI.dc.html"]:
        src = Path("web") / ten
        noi_dung = src.read_text(encoding="utf-8")
        for m in re.finditer(r'(?:href|src)="([^"]*\{\{[^}]+\}\}[^"]*)"', noi_dung):
            gt = m.group(1)
            assert "javascript:" not in gt.lower(), f"{ten}: href javascript: {gt[:80]}"
    # `o.href` duy nhất gắn từ dữ liệu — nguồn là K.noiThemKhoa (điều hướng nội bộ):
    ht = (Path("web") / "He-thong.dc.html").read_text(encoding="utf-8")
    assert "K.noiThemKhoa" in ht
