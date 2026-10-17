"""Ma trận leo thang / IDOR cho dich-vu/trinh-duyet/server.py (NV96 — chỉ dịch vụ trình duyệt).

Phạm vi: mọi đường nhận id hồ sơ. Ba trục kiểm:

1. Xác thực Bearer (lớp ASGI XacThucBearer, chạy TRƯỚC định tuyến):
   thiếu/sai/khác kiểu → 401, CÙNG một thông điệp (không oracle phân biệt đúng/sai).
2. Khuôn id (MAU_HO_SO ^[a-f0-9]{16,64}$ + kiem_tra_duong_dan_ho_so chuẩn hoá bằng
   resolve() rồi so đúng <gốc>/<id>):
   sai khuôn → 400; "/" trong id → 404 (không khớp route); symlink thoát gốc hoặc
   trỏ sang hồ sơ KHÁC → 403; đường dẫn thực luôn nằm trong thư mục gốc.
3. Tồn tại: id đúng khuôn nhưng hồ sơ không tồn tại → 404 ở đường đọc/điều khiển;
   /dong và /dieu-khien/nha LUỸ ĐẲNG → 200 theo thiết kế (không đổi trạng thái,
   không lộ gì); /ho-so/mo với id mới → 200 (tạo/mở hồ sơ).

Test-first: viết ĐỎ trước rồi chạy. Nếu test đỏ vì mã thật có lỗ hổng
(ghép đường dẫn không chuẩn hoá, oracle Bearer, IDOR qua symlink…): KHÔNG sửa mã,
đánh dấu @pytest.mark.xfail(strict=True, reason="...") và mô tả lỗ hổng.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

GOC_DICH_VU = Path(__file__).resolve().parent
if str(GOC_DICH_VU) not in sys.path:
    sys.path.insert(0, str(GOC_DICH_VU))

import server
from server import (
    BoChayTienTrinh,
    BoKiemTraKetNoi,
    QuanLyHoSo,
    TienTrinhHandle,
    app,
    kiem_tra_duong_dan_ho_so,
)

# Chuỗi khoá giả rõ ràng (không phải bí mật thật).
KHOA_TEST = "sk-lan-0000000000000000000000000000xyzw"
KHOA_SAI = KHOA_TEST[::-1]
HEADER_AUTH = {"Authorization": f"Bearer {KHOA_TEST}"}

#: ID hợp lệ khuôn MAU_HO_SO, dùng xuyên suốt ma trận.
ID = "0123456789abcdef0123456789abcdef"

#: Các đường nhận id qua PATH — (phương thức, hậu tố, thân mẫu).
DUONG_PATH = [
    ("GET", "/cookie", None),  # query ?mien= thêm lúc gọi
    ("POST", "/dong", {"giu_phien": True}),
    ("POST", "/dieu-khien/gianh", None),
    ("POST", "/dieu-khien/nha", None),
    ("POST", "/dieu-huong", {"url": "https://example.com/"}),
    ("POST", "/dien", {"bo_chon": "#a", "van_ban": "x"}),
    ("POST", "/bam", {"bo_chon": "#a"}),
    ("POST", "/cho", {"bo_chon": "#a", "giay": 1}),
    ("POST", "/tin-hieu", {"quy_tac": []}),
    ("POST", "/cookie-truc-tiep", {"mien": "example.com"}),
    ("POST", "/chup-trang-thai", None),
]


class TienTrinhGia(TienTrinhHandle):
    def __init__(self) -> None:
        self.da_dung = False

    def dang_chay(self) -> bool:
        return not self.da_dung

    async def dung(self) -> None:
        self.da_dung = True


class BoChayGia(BoChayTienTrinh):
    def __init__(self) -> None:
        self.tien_trinh_tao: list[TienTrinhGia] = []

    async def chay(self, lenh: list[str], env: dict[str, str],
                  cwd: str | None = None) -> TienTrinhHandle:
        p = TienTrinhGia()
        self.tien_trinh_tao.append(p)
        return p


class BoKiemTraGia(BoKiemTraKetNoi):
    def dem_ket_noi(self, ho_so: Any) -> int:
        return 0


@pytest.fixture
def mt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    goc = tmp_path / "hoso"
    goc.mkdir()
    (tmp_path / "tam").mkdir()
    monkeypatch.setenv("TRINH_DUYET_KHOA", KHOA_TEST)
    monkeypatch.setenv("TRINH_DUYET_THU_MUC_GOC", str(goc))
    # Thư mục tạm riêng: lifespan dọn w2a-cookie-* chỉ quét ở đây, không quét /tmp thật.
    monkeypatch.setattr(__import__("tempfile"), "tempdir", str(tmp_path / "tam"))
    monkeypatch.setattr(server, "_khoa_dich_vu", KHOA_TEST, raising=False)
    cau_hinh = server.lay_cau_hinh()
    cau_hinh["cho_khoi_dong_giay"] = 0
    quan_ly = QuanLyHoSo(cau_hinh=cau_hinh, bo_chay=BoChayGia(),
                         bo_kiem_tra=BoKiemTraGia())
    monkeypatch.setattr(server, "quan_ly_toan_cuc", quan_ly)
    return {"goc": goc, "tmp": tmp_path, "ql": quan_ly, "monkeypatch": monkeypatch}


def _url(duong: str) -> str:
    url = f"/ho-so/{ID}{duong}"
    if duong == "/cookie":
        url += "?mien=example.com"
    return url


# ══ 1. Xác thực Bearer: 401 đồng nhất, không oracle ═══════════════════════════


@pytest.mark.parametrize("method,duong,than", DUONG_PATH)
def test_thieu_va_sai_bearer_deu_401_cung_thong_diep(mt, method, duong, than):
    """Thiếu/khác kiểu/sai khoá → 401 với CÙNG thân JSON: không phân biệt được
    Bearer đúng/sai qua mã hay thông điệp."""
    url = _url(duong)
    with TestClient(app) as client:
        r_thieu = client.request(method, url, json=than)
        r_khac_kieu = client.request(
            method, url, json=than,
            headers={"Authorization": f"Basic {KHOA_TEST}"})
        r_sai = client.request(
            method, url, json=than,
            headers={"Authorization": f"Bearer {KHOA_SAI}"})
        r_dung = client.request(method, url, json=than, headers=HEADER_AUTH)
    for r in (r_thieu, r_khac_kieu, r_sai):
        assert r.status_code == 401, (method, duong, r.status_code, r.text)
        assert r.headers.get("WWW-Authenticate") == "Bearer"
    assert r_thieu.json() == r_khac_kieu.json() == r_sai.json()
    assert r_thieu.json()["detail"] == r_sai.json()["detail"]
    # Có khoá đúng thì đi tiếp vào trong (không còn 401 — trạng thái tuỳ đường).
    assert r_dung.status_code != 401


def test_mo_thieu_va_sai_bearer_deu_401_cung_thong_diep(mt):
    than = {"ho_so": ID, "loai": "chromium"}
    with TestClient(app) as client:
        r_thieu = client.post("/ho-so/mo", json=than)
        r_sai = client.post("/ho-so/mo", json=than,
                            headers={"Authorization": f"Bearer {KHOA_SAI}"})
        r_dung = client.post("/ho-so/mo", json=than, headers=HEADER_AUTH)
    assert r_thieu.status_code == 401
    assert r_sai.status_code == 401
    assert r_thieu.json() == r_sai.json()
    assert r_thieu.headers.get("WWW-Authenticate") == "Bearer"
    assert r_dung.status_code == 200  # khoá đúng → mở được


def test_xac_thuc_chay_truoc_dinh_tuyen(mt):
    """Không khoá + id chứa '/..' → 401, không phải 404: middleware chặn trước route."""
    with TestClient(app) as client:
        r = client.get("/ho-so/../../../etc/passwd/cookie")
        assert r.status_code == 401


# ══ 2a. ID sai khuôn → 400 ════════════════════════════════════════════════════

ID_SAI_KHUON = [
    "ngan",  # quá ngắn
    "a" * 65,  # quá dài
    "ABCDEF0123456789ABCDEF0123456789",  # hex chữ HOA
    "user@example.com-12345678abcd",  # suy từ email
    "1234567890abcdef-12345678",  # chứa gạch nối
    "..\\..\\" + "ab" * 12,  # backslash (Linux: ký tự tên tệp thường)
    # LƯU Ý: id chứa "/" thật hoặc byte null THÔ không vào danh sách này:
    # "/" làm Starlette không khớp route (→ 404, có test riêng),
    # byte null thô bị httpx chặn ngay phía client (InvalidURL).
    # Cả hai dạng mã hoá (%2e%2e%2f, %00) có test riêng bên dưới.
]

#: ID mã hoá URL — máy chủ giải mã rồi kiểm khuôn như thường.
ID_MA_HOA_XAU = [
    # (dạng mã hoá, mã trạng thái kỳ vọng — đều an toàn)
    ("%2e%2e%2f" + "ab" * 14, 404),  # "../" sau giải mã → không khớp route
    ("%2E%2E%5C" + "ab" * 14, 400),  # "..\" sau giải mã → regex chặn
    ("..%c0%af" + "ab" * 14, 400),  # overlong UTF-8 → regex chặn
    ("%00cdef" + "ef" * 13, 400),  # byte null sau giải mã → regex chặn
]


@pytest.mark.parametrize("method,duong,than", DUONG_PATH)
@pytest.mark.parametrize("id_sai", ID_SAI_KHUON)
def test_id_sai_khuon_tren_duong_path_tra_400(mt, method, duong, than, id_sai):
    """Mọi đường path-based đều qua kiem_tra_duong_dan_ho_so → 400 trước mọi logic."""
    url = f"/ho-so/{id_sai}{duong}"
    if duong == "/cookie":
        url += "?mien=example.com"
    with TestClient(app) as client:
        r = client.request(method, url, json=than, headers=HEADER_AUTH)
    assert r.status_code == 400, (method, duong, repr(id_sai), r.status_code, r.text)


@pytest.mark.parametrize("id_sai", ID_SAI_KHUON)
def test_id_sai_khuon_trong_than_mo_tra_400(mt, id_sai):
    with TestClient(app) as client:
        r = client.post("/ho-so/mo", headers=HEADER_AUTH,
                        json={"ho_so": id_sai, "loai": "chromium"})
    assert r.status_code == 400, (repr(id_sai), r.status_code, r.text)


def test_id_chua_byte_null_trong_than_mo_tra_400(mt):
    """Thân JSON mang được byte null thô (\\u0000) — regex vẫn chặn → 400."""
    with TestClient(app) as client:
        r = client.post("/ho-so/mo", headers=HEADER_AUTH,
                        json={"ho_so": "ab\x00cd" + "ef" * 13, "loai": "chromium"})
    assert r.status_code == 400, (r.status_code, r.text)
    assert not (mt["goc"] / "ab").exists()


def test_id_ma_hoa_xau_khong_thoat(mt):
    """Các dạng mã hoá traversal/null-byte: máy chủ giải mã rồi hoặc 400 (regex)
    hoặc 404 (không khớp route) — tuyệt đối không tới handler, không tạo gì ngoài gốc."""
    truoc = {p.name for p in mt["tmp"].iterdir()}
    with TestClient(app) as client:
        for ma_hoa, ma_ky_vong in ID_MA_HOA_XAU:
            r = client.post(f"/ho-so/{ma_hoa}/dong", headers=HEADER_AUTH,
                            json={"giu_phien": False})
            assert r.status_code == ma_ky_vong, (ma_hoa[:16], r.status_code, r.text)
    assert {p.name for p in mt["tmp"].iterdir()} == truoc


# ══ 2b. "/" trong id → 404 (không khớp route), không chạm handler ════════════


@pytest.mark.parametrize("method,duong,than", DUONG_PATH)
def test_id_chua_gach_cheo_khong_khop_route(mt, method, duong, than):
    """'../x' chứa '/' nên Starlette không khớp /ho-so/{id}/… → 404, handler
    (và kiểm id) không bao giờ chạy — không thể lách bằng phân đoạn đường dẫn."""
    id_xau = "../" + "ab" * 14
    url = f"/ho-so/{id_xau}{duong}"
    with TestClient(app) as client:
        r = client.request(method, url, json=than, headers=HEADER_AUTH)
    assert r.status_code == 404, (method, duong, r.status_code, r.text)


def test_loat_id_xau_khong_de_lai_dau_vet_ngoai_goc(mt):
    """Sau cả loạt id xấu, cây tmp_path không đổi: không tệp/thư mục nào mọc
    ngoài thư mục gốc hồ sơ."""
    from urllib.parse import quote

    def cay() -> set[str]:
        return {str(p.relative_to(mt["tmp"])) for p in mt["tmp"].rglob("*")}

    truoc = cay()
    with TestClient(app) as client:
        for id_sai in ID_SAI_KHUON + ["../" + "ab" * 14,
                                      "..%2f.." + "ab" * 12,
                                      "%00cdef" + "ef" * 13]:
            client.post("/ho-so/mo", headers=HEADER_AUTH,
                        json={"ho_so": id_sai, "loai": "chromium"})
            client.post(f"/ho-so/{quote(id_sai, safe='')}/dong",
                        headers=HEADER_AUTH, json={"giu_phien": False})
    assert cay() == truoc


# ══ 2c. Symlink: thoát gốc → 403; trỏ sang hồ sơ khác (IDOR) → 403 ════════════


def test_symlink_thoat_goc_tra_403(mt):
    ngoai = mt["tmp"] / "ngoai"
    ngoai.mkdir()
    (ngoai / "bay.dat").write_text("du-lieu-nhay-cam", encoding="utf-8")
    (mt["goc"] / ID).symlink_to(ngoai)
    with TestClient(app) as client:
        r = client.post("/ho-so/mo", headers=HEADER_AUTH,
                        json={"ho_so": ID, "loai": "chromium"})
    assert r.status_code == 403, (r.status_code, r.text)
    # Không ghi gì vào thư mục ngoài gốc
    assert [p.name for p in ngoai.iterdir()] == ["bay.dat"]


def test_symlink_sang_ho_so_khac_tra_403(mt):
    """Symlink <gốc>/<idA> → <gốc>/<idB>: resolve() ra <gốc>/<idB> ≠ <gốc>/<idA>
    → 403. Chặn IDOR đọc/ghi xuyên hồ sơ qua liên kết."""
    id_b = "b" * 32
    (mt["goc"] / id_b).mkdir(mode=0o700)
    (mt["goc"] / id_b / "rieng.dat").write_text("cua-B", encoding="utf-8")
    (mt["goc"] / ID).symlink_to(mt["goc"] / id_b)
    with TestClient(app) as client:
        r = client.post("/ho-so/mo", headers=HEADER_AUTH,
                        json={"ho_so": ID, "loai": "chromium"})
    assert r.status_code == 403, (r.status_code, r.text)


def test_duong_dan_thuc_luon_nam_trong_goc(mt):
    """Đơn vị: id đúng khuôn → đường dẫn trả về resolve() đúng <gốc>/<id>,
    là con trực tiếp của gốc, không qua symlink."""
    dd = kiem_tra_duong_dan_ho_so(ID, mt["goc"])
    assert dd.resolve() == mt["goc"].resolve() / ID
    assert dd.resolve().parent == mt["goc"].resolve()
    assert not dd.is_symlink()


# ══ 3. ID đúng khuôn nhưng hồ sơ không tồn tại ═══════════════════════════════


@pytest.mark.parametrize("method,duong,than", [
    ("GET", "/cookie", None),
    ("POST", "/dieu-khien/gianh", None),
    ("POST", "/dieu-huong", {"url": "https://example.com/"}),
    ("POST", "/dien", {"bo_chon": "#a", "van_ban": "x"}),
    ("POST", "/bam", {"bo_chon": "#a"}),
    ("POST", "/cho", {"bo_chon": "#a", "giay": 1}),
    ("POST", "/tin-hieu", {"quy_tac": []}),
    ("POST", "/cookie-truc-tiep", {"mien": "example.com"}),
    ("POST", "/chup-trang-thai", None),
])
def test_id_khong_ton_tai_duong_doc_dieu_khien_tra_404(mt, method, duong, than):
    url = _url(duong)
    with TestClient(app) as client:
        r = client.request(method, url, json=than, headers=HEADER_AUTH)
    assert r.status_code == 404, (method, duong, r.status_code, r.text)


def test_dong_id_khong_ton_tai_luy_dang_200(mt):
    """Đóng hồ sơ chưa từng mở: luỹ đẳng, không đổi trạng thái → 200 theo thiết kế."""
    with TestClient(app) as client:
        r = client.post(f"/ho-so/{ID}/dong", headers=HEADER_AUTH,
                        json={"giu_phien": True})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "session_preserved": True}
    assert not (mt["goc"] / ID).exists()  # không tạo thư mục


def test_nha_dieu_khien_id_khong_ton_tai_luy_dang_200(mt):
    """Nhả quyền khi chưa giành/chưa mở: luỹ đẳng → 200 theo thiết kế."""
    with TestClient(app) as client:
        r = client.post(f"/ho-so/{ID}/dieu-khien/nha", headers=HEADER_AUTH)
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_mo_id_moi_tao_ho_so_trong_goc(mt):
    """/ho-so/mo với id mới đúng khuôn → 200 và thư mục chỉ mọc TRONG gốc, 0700."""
    with TestClient(app) as client:
        r = client.post("/ho-so/mo", headers=HEADER_AUTH,
                        json={"ho_so": ID, "loai": "chromium"})
    assert r.status_code == 200
    dd = mt["goc"] / ID
    assert dd.is_dir() and not dd.is_symlink()
    assert (dd.stat().st_mode & 0o777) == 0o700


# ══ 4. Giành điều khiển: xác minh phiên đúng hồ sơ (chống nối nhầm) ══════════


class MarionetteGiaSaiHoSo:
    """Máy khách giả trỏ moz_profile sang thư mục KHÁC hồ sơ yêu cầu."""

    def __init__(self, moz_profile: str):
        self.moz_profile = moz_profile

    def ket_noi(self) -> None:
        pass

    def phien_moi(self) -> None:
        pass

    def dong(self) -> None:
        pass

    def xoa_phien(self) -> None:
        pass


def test_gianh_noi_nham_ho_so_khac_tra_502_va_khong_ban(mt):
    """NV94-P3: phiên Marionette trỏ sang thư mục khác → 502 'sai_ho_so_phien',
    KHÔNG đánh dấu đang-bận; giành lại với hồ sơ đúng vẫn được."""
    (mt["goc"] / ID).mkdir(mode=0o700)
    (mt["goc"] / ID / ".thong_tin.json").write_text('{"loai": "firefox"}',
                                                   encoding="utf-8")
    khac = mt["tmp"] / "ho-so-khac"
    khac.mkdir()
    mt["monkeypatch"].setattr(
        server, "_tao_marionette", lambda cong: MarionetteGiaSaiHoSo(str(khac)))
    with TestClient(app) as client:
        r = client.post(f"/ho-so/{ID}/dieu-khien/gianh", headers=HEADER_AUTH)
        assert r.status_code == 502, (r.status_code, r.text)
        assert r.json()["detail"] == "sai_ho_so_phien"
        # Không kẹt cờ bận: giành với hồ sơ đúng → 200
        mt["monkeypatch"].setattr(
            server, "_tao_marionette",
            lambda cong: MarionetteGiaSaiHoSo(str(mt["goc"] / ID)))
        r2 = client.post(f"/ho-so/{ID}/dieu-khien/gianh", headers=HEADER_AUTH)
        assert r2.status_code == 200, (r2.status_code, r2.text)


# ══ 5. Chi tiết lỗi 400 không phản chiếu nội dung nguy hiểm ═══════════════════


def test_detail_400_id_sai_khuon_khong_chua_bearer(mt):
    """Thông điệp 400 có nhắc lại id (định dạng JSON) nhưng không bao giờ lẫn khoá."""
    with TestClient(app) as client:
        r = client.post("/ho-so/mo", headers=HEADER_AUTH,
                        json={"ho_so": "id-sai-@@@", "loai": "chromium"})
    assert r.status_code == 400
    assert KHOA_TEST not in r.text
    assert "application/json" in r.headers.get("content-type", "")
