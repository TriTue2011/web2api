"""Kiểm thử nhật ký hệ thống theo kênh (Đợt C lõi web2api):
- app/nhat_ky.py: ghi, đọc, lọc, phân trang con trỏ, xoay vòng, xoá cũ, quyền 0600, trần MB.
- Che bí mật: khoá quản trị, khoá API không bao giờ vào nhật ký kể cả khi lỗi hay stream đứt.
- Kênh cuoc_goi: mỗi lượt /v1 đúng một dòng (kể cả 401, 404, 500, stream đứt), tiêu đề x-request-id và X-Web2api-Module.
- Kênh he_thong: khởi động, nạp/đổi/gỡ module, lỗi nạp, cảnh báo TZ hỏng.
- Kênh lạ từ module giả hiện nguyên văn trên /api/nhat-ky/kenh.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import kho, nhat_ky, xac_thuc
from app.main import app

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
KHOA_OPENAI_GIA = "sk-lan-0000000000000000000000000000xyzw"
KHOA_GOOGLE_GIA = "AIzaSyUI-TOT-0000000000000000000000tot1"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    # Khởi động app với lifespan để sinh khoá và khởi tạo sổ module
    with TestClient(app) as c:
        yield c


def test_ghi_doc_loc_va_quyen_tep_0600(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    # Ghi thử các bản ghi vào các kênh khác nhau
    nhat_ky.ghi("he_thong", "thong_tin", "Khởi động hệ thống lần 1", ms=12.5, phien_ban="1.0.0")
    nhat_ky.ghi("cuoc_goi", "thong_tin", "GET /v1/models 200", ms=5.2, ma_http=200, duong="/v1/models")
    nhat_ky.ghi("kenh_la_x9", "canh_bao", "Cảnh báo từ module lạ", ms=None, chi_tiet="thử nghiệm")

    # Kiểm tra quyền thư mục và tệp nhật ký: quyền 0600 cho tệp
    thu_muc = nhat_ky.thu_muc_nhat_ky()
    assert thu_muc.is_dir()
    tep_nhat_ky = thu_muc / "nhat_ky.jsonl"
    assert tep_nhat_ky.is_file()
    che_do = stat.S_IMODE(tep_nhat_ky.stat().st_mode)
    assert che_do == 0o600

    # Đọc không lọc: mới nhất trước
    tat_ca = nhat_ky.doc(gioi_han=10)
    assert len(tat_ca) == 3
    assert tat_ca[0]["kenh"] == "kenh_la_x9"
    assert tat_ca[1]["kenh"] == "cuoc_goi"
    assert tat_ca[2]["kenh"] == "he_thong"

    # Lọc theo kênh
    chi_cuoc_goi = nhat_ky.doc(kenh="cuoc_goi")
    assert len(chi_cuoc_goi) == 1
    assert chi_cuoc_goi[0]["tom_tat"] == "GET /v1/models 200"
    assert chi_cuoc_goi[0]["them"]["ma_http"] == 200

    # Lọc theo mức
    chi_canh_bao = nhat_ky.doc(muc="canh_bao")
    assert len(chi_canh_bao) == 1
    assert chi_canh_bao[0]["kenh"] == "kenh_la_x9"

    # Lọc tìm kiếm q không phân biệt hoa thường
    tim_kiem = nhat_ky.doc(q="khởi ĐỘNG")
    assert len(tim_kiem) == 1
    assert tim_kiem[0]["kenh"] == "he_thong"


def test_phan_trang_bang_con_tro_truoc(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    # Ghi 5 bản ghi
    for i in range(5):
        nhat_ky.ghi("he_thong", "thong_tin", f"Bản ghi số {i}", ms=i)

    # Lấy trang 1: 2 bản ghi
    trang_1 = nhat_ky.doc(gioi_han=2)
    assert len(trang_1) == 2
    assert trang_1[0]["tom_tat"] == "Bản ghi số 4"
    assert trang_1[1]["tom_tat"] == "Bản ghi số 3"

    con_tro_1 = trang_1[-1]["luc"]

    # Lấy trang 2 với con trỏ truoc
    trang_2 = nhat_ky.doc(truoc=con_tro_1, gioi_han=2)
    assert len(trang_2) == 2
    assert trang_2[0]["tom_tat"] == "Bản ghi số 2"
    assert trang_2[1]["tom_tat"] == "Bản ghi số 1"

    con_tro_2 = trang_2[-1]["luc"]

    # Lấy trang 3
    trang_3 = nhat_ky.doc(truoc=con_tro_2, gioi_han=2)
    assert len(trang_3) == 1
    assert trang_3[0]["tom_tat"] == "Bản ghi số 0"

    # Hết dữ liệu
    trang_4 = nhat_ky.doc(truoc=trang_3[-1]["luc"], gioi_han=2)
    assert trang_4 == []


def test_che_bi_mat_va_khoa_trong_nhat_ky(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    # Ghi nội dung có chứa khoá quản trị và khoá API giả trong allowlist
    nhat_ky.ghi("he_thong", "loi", f"Lỗi với khoá {KHOA_QT} và {KHOA_OPENAI_GIA}",
                bi_mat_1=KHOA_GOOGLE_GIA, ghi_chu=f"Khoá là {KHOA_QT}")

    ban_ghi = nhat_ky.doc(gioi_han=1)[0]
    noi_dung_dong = json.dumps(ban_ghi)

    # Tuyệt đối không chứa nguyên vẹn chuỗi bí mật
    assert KHOA_QT not in noi_dung_dong
    assert KHOA_OPENAI_GIA not in noi_dung_dong
    assert KHOA_GOOGLE_GIA not in noi_dung_dong

    # Phải có dạng che ...
    assert "…" in ban_ghi["tom_tat"]
    assert "…" in ban_ghi["them"]["bi_mat_1"]


def test_xoay_vong_va_tran_dung_luong(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    # Đặt trần rất nhỏ: 0.005 MB (~5 KB) để kích hoạt xoay vòng nhanh
    monkeypatch.setenv("WEB2API_NHAT_KY_TOI_DA_MB", "0.005")

    # Ghi liên tục 100 bản ghi
    for i in range(100):
        nhat_ky.ghi("cuoc_goi", "thong_tin", f"Dòng ghi số {i} với dữ liệu đệm dài để kích hoạt xoay vòng dung lượng nhật ký",
                    so=i, chuoi="x" * 100)

    thu_muc = nhat_ky.thu_muc_nhat_ky()
    cac_tep = list(thu_muc.glob("nhat_ky*.jsonl"))
    assert len(cac_tep) >= 1

    # Kiểm tra quyền 0600 cho toàn bộ các tệp xoay vòng
    for p in cac_tep:
        assert stat.S_IMODE(p.stat().st_mode) == 0o600

    # Tổng dung lượng không được vượt quá trần cấu hình (cho phép dung sai nhỏ 1 dòng cuối)
    tong_byte = sum(p.stat().st_size for p in cac_tep)
    tran_byte = int(0.005 * 1024 * 1024)
    assert tong_byte <= tran_byte + 1024


def test_ghi_khong_nem_loi_khi_thu_muc_khong_ghi_duoc(tmp_path, monkeypatch):
    """Chạy bằng root thì chmod 0400 không chặn gì — dựng lỗi thật: đường thư mục nhật ký là một TỆP thường."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    nhat_ky.thu_muc_nhat_ky().write_text("không phải thư mục")
    nhat_ky.ghi("he_thong", "canh_bao", "Thử nghiệm thư mục không ghi được")  # không ném
    assert nhat_ky.doc() == []


def test_ghi_khong_nem_loi_khi_dia_day(tmp_path, monkeypatch):
    import errno
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    def day(*a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(nhat_ky.os, "write", day)
    monkeypatch.setattr("builtins.open", day)
    nhat_ky.ghi("he_thong", "canh_bao", "Đĩa đầy")  # không ném


def test_danh_sach_kenh_va_kenh_la(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    nhat_ky.ghi("kenh_mot", "thong_tin", "Bản ghi 1")
    nhat_ky.ghi("kenh_mot", "thong_tin", "Bản ghi 2")
    nhat_ky.ghi("kenh_la_bat_ky", "loi", "Bản ghi từ kênh lạ")

    ds = nhat_ky.danh_sach_kenh()
    map_kenh = {k["kenh"]: k for k in ds}

    assert "kenh_mot" in map_kenh
    assert map_kenh["kenh_mot"]["so_dong"] == 2
    assert "lan_cuoi" in map_kenh["kenh_mot"]

    # Kênh lạ xuất hiện nguyên văn
    assert "kenh_la_bat_ky" in map_kenh
    assert map_kenh["kenh_la_bat_ky"]["so_dong"] == 1


def test_xoa_nhat_ky_cu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    # Ghi bản ghi bình thường
    nhat_ky.ghi("he_thong", "thong_tin", "Bản ghi mới hôm nay")

    # Giả lập bản ghi cũ 10 ngày trước bằng cách sửa trường luc
    thu_muc = nhat_ky.thu_muc_nhat_ky()
    tep = thu_muc / "nhat_ky.jsonl"
    ngay_cu = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    dong_cu = json.dumps({"luc": ngay_cu, "kenh": "he_thong", "muc": "thong_tin",
                          "tom_tat": "Bản ghi 10 ngày trước", "ms": 1, "them": {}}, ensure_ascii=False) + "\n"
    with open(tep, "a", encoding="utf-8") as f:
        f.write(dong_cu)

    assert len(nhat_ky.doc()) == 2

    # Xoá bản ghi cũ hơn 5 ngày
    da_xoa = nhat_ky.xoa_cu(truoc_ngay=5)
    assert da_xoa == 1

    con_lai = nhat_ky.doc()
    assert len(con_lai) == 1
    assert con_lai[0]["tom_tat"] == "Bản ghi mới hôm nay"

    # Xoá với truoc_ngay=0 xoá toàn bộ
    da_xoa_het = nhat_ky.xoa_cu(truoc_ngay=0)
    assert da_xoa_het == 1
    assert len(nhat_ky.doc()) == 0


def test_api_nhat_ky_xac_thuc_va_thao_tac(client):
    # Chưa có khoá -> 401
    assert client.get("/api/nhat-ky").status_code == 401
    assert client.get("/api/nhat-ky/kenh").status_code == 401
    assert client.delete("/api/nhat-ky").status_code == 401

    # Tạo dữ liệu
    nhat_ky.ghi("he_thong", "thong_tin", "Tin nhắn từ API", ms=10)
    nhat_ky.ghi("kenh_mo", "canh_bao", "Cảnh báo API", ms=20)

    # GET /api/nhat-ky
    r = client.get("/api/nhat-ky", headers=QT)
    assert r.status_code == 200
    du_lieu = r.json()
    assert isinstance(du_lieu, list)
    assert len(du_lieu) >= 2

    # GET /api/nhat-ky/kenh
    r_kenh = client.get("/api/nhat-ky/kenh", headers=QT)
    assert r_kenh.status_code == 200
    ds_kenh = r_kenh.json()
    assert any(k["kenh"] == "kenh_mo" for k in ds_kenh)

    # DELETE /api/nhat-ky thiếu tham số -> 400
    r_xoa_thieu = client.delete("/api/nhat-ky", headers=QT)
    assert r_xoa_thieu.status_code == 400

    # DELETE /api/nhat-ky tham số âm -> 400
    r_xoa_am = client.delete("/api/nhat-ky?truoc_ngay=-1", headers=QT)
    assert r_xoa_am.status_code == 400

    # DELETE hợp lệ
    r_xoa = client.delete("/api/nhat-ky?truoc_ngay=30", headers=QT)
    assert r_xoa.status_code == 200
    assert "da_xoa" in r_xoa.json()


def test_kenh_cuoc_goi_moi_luot_v1_dung_mot_dong_va_tieu_de(client):
    # Trước khi gọi /v1, xoá nhật ký để kiểm tra
    nhat_ky.xoa_cu(truoc_ngay=0)

    # 1. Gọi /health và /api/he-thong KHÔNG sinh dòng cuoc_goi
    client.get("/health")
    client.get("/api/he-thong")
    assert len(nhat_ky.doc(kenh="cuoc_goi")) == 0

    # 2. Gọi /v1/models không khoá -> 401
    r_401 = client.get("/v1/models")
    assert r_401.status_code == 401
    assert "x-request-id" in r_401.headers
    cg_401 = nhat_ky.doc(kenh="cuoc_goi")
    assert len(cg_401) == 1
    assert cg_401[0]["them"]["ma_http"] == 401
    assert cg_401[0]["them"]["duong"] == "/v1/models"
    # 401: người gọi CHƯA xác thực — ghi «quan_tri» là sai sự thật
    assert cg_401[0]["them"]["nguoi_goi"] == "chua_xac_thuc"

    # 3. Gọi /v1/duong-la-khong-co -> 404
    r_404 = client.get("/v1/duong-la-khong-co", headers=QT)
    assert r_404.status_code == 404
    assert "x-request-id" in r_404.headers
    cg_404 = nhat_ky.doc(kenh="cuoc_goi")
    assert len(cg_404) == 2
    assert cg_404[0]["them"]["ma_http"] == 404

    # 4. Gọi /v1/models có khoá -> 200
    # Truyền sẵn x-request-id hợp lệ từ khách
    req_id_khach = "req-khach-gui-12345"
    r_200 = client.get("/v1/models", headers=QT | {"x-request-id": req_id_khach})
    assert r_200.status_code == 200
    assert r_200.headers.get("x-request-id") == req_id_khach
    cg_200 = nhat_ky.doc(kenh="cuoc_goi")
    assert len(cg_200) == 3
    assert cg_200[0]["them"]["ma_http"] == 200
    assert cg_200[0]["ms"] is not None

    # 5. Gọi /v1/chat/completions với model sai -> 404 model_not_found
    r_chat_sai = client.post("/v1/chat/completions", headers=QT,
                             json={"model": "khong-co/model-1", "messages": [{"role": "user", "content": "alo"}]})
    assert r_chat_sai.status_code == 404
    cg_chat = nhat_ky.doc(kenh="cuoc_goi")
    assert len(cg_chat) == 4
    assert cg_chat[0]["them"]["model"] == "khong-co/model-1"
    # Tuyệt đối không ghi nội dung lời nhắn trong bất kỳ trường nào
    dong_json = json.dumps(cg_chat[0])
    assert "alo" not in dong_json
    assert "messages" not in dong_json


def test_tieu_de_x_web2api_module_khi_co_module(client, tmp_path):
    # Cài module giả
    thu_muc_mod = tmp_path / "modules" / "mod-gia"
    thu_muc_mod.mkdir(parents=True, exist_ok=True)
    (thu_muc_mod / "manifest.json").write_text(json.dumps({
        "id": "mod-gia",
        "ten": "Module Giả",
        "mo_ta": "Module giả để kiểm tra",
        "loai": "ai",
        "giao_thuc": "openai",
        "tien_to": "modgia",
        "truong_cau_hinh": [],
        "tai_nguyen": {}
    }))
    (thu_muc_mod / "module.py").write_text("""
class Module:
    def __init__(self, manifest): self.manifest = manifest
    def nap(self, cfg): pass
    async def cai(self, cfg): return {"ok": True, "cau_hinh_luu": {}}
    async def models(self): return [{"id": "modgia/test-model"}]
    async def chat(self, body, stream):
        import json
        res = {"id": "chatcmpl-1", "object": "chat.completion", "choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33}}
        return 200, {"content-type": "application/json"}, json.dumps(res).encode()
    async def suc_khoe(self): return {"ok": True}
    async def dung(self): pass
""")
    # Nạp lại module và liên kết cấu hình
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-gia/cai", headers=QT, json={"cau_hinh": {}})

    # Gọi chat
    r = client.post("/v1/chat/completions", headers=QT,
                    json={"model": "modgia/test-model", "messages": [{"role": "user", "content": "chao"}]})
    assert r.status_code == 200
    assert r.headers.get("x-web2api-module") == "modgia" or r.headers.get("X-Web2api-Module") == "modgia"

    # Kiểm tra dòng cuoc_goi ghi nhận module và usage
    cg = nhat_ky.doc(kenh="cuoc_goi")[0]
    assert cg["them"]["module"] == "mod-gia"
    assert cg["them"]["tien_to"] == "modgia"
    assert cg["them"]["model"] == "modgia/test-model"
    assert cg["them"]["request_id"] == r.headers["x-request-id"]
    assert cg["them"]["usage"]["total_tokens"] == 33
    # Không có "chao"
    assert "chao" not in json.dumps(cg)


def test_he_thong_ghi_loi_nap_va_canh_bao_tz(client, monkeypatch):
    # Kiểm tra cảnh báo múi giờ TZ sai
    monkeypatch.setenv("TZ", "Posix/SaiKhongPhaiIANA")
    from app import he_thong
    he_thong.mui_gio()

    logs = nhat_ky.doc(kenh="he_thong", muc="canh_bao")
    assert any("TZ" in x["tom_tat"] for x in logs)


# ───────────── Soát độc lập: các lỗi tìm thấy (viết trước bản sửa) ─────────────

def _rchar() -> int:
    for dong in Path("/proc/self/io").read_text().splitlines():
        if dong.startswith("rchar:"):
            return int(dong.split()[1])
    raise RuntimeError("không có rchar")


@pytest.mark.skipif(not Path("/proc/self/io").is_file(), reason="cần /proc/self/io (Linux)")
def test_ghi_khong_doc_lai_ca_tep_moi_luot(tmp_path, monkeypatch):
    """`ghi` nằm trên đường nóng của MỌI lượt /v1: một dòng phải tốn I/O cỡ độ dài dòng, không cỡ tệp.
    Đo byte ĐỌC thật của tiến trình (rchar) — không phụ thuộc cách cài đặt; bản cũ đọc trọn tệp 3 MB mỗi lượt."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    d = nhat_ky.thu_muc_nhat_ky()
    d.mkdir(parents=True)
    dong = json.dumps({"luc": "2026-10-12T00:00:00.000000Z", "kenh": "cuoc_goi", "muc": "thong_tin",
                       "tom_tat": "x" * 200, "ms": 1, "them": {}}) + "\n"
    (d / "nhat_ky.jsonl").write_text(dong * (3 * 1024 * 1024 // len(dong)))
    nhat_ky.ghi("cuoc_goi", "thong_tin", "lượt đầu (được phép đếm tệp một lần)")
    truoc = _rchar()
    for i in range(50):
        nhat_ky.ghi("cuoc_goi", "thong_tin", f"lượt {i}", ms=1, duong="/v1/models")
    assert _rchar() - truoc < 1024 * 1024  # bản cũ: ~150 MB


def test_ghi_nhan_kenh_muc_lam_khoa_them_khong_nem(tmp_path, monkeypatch):
    """«Không bao giờ ném»: module truyền khoá phụ trùng tên tham số (kenh=, muc=, tom_tat=) không được TypeError."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    nhat_ky.ghi("kenh_mod", "thong_tin", "tóm tắt", kenh="phu", muc="phu", tom_tat="phu")
    assert nhat_ky.doc()[0]["them"] == {"kenh": "phu", "muc": "phu", "tom_tat": "phu"}


def test_ghi_khong_nuot_huy_tac_vu(tmp_path, monkeypatch):
    """CancelledError/KeyboardInterrupt là tín hiệu điều khiển, không phải lỗi ghi — không được nuốt."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    def huy(*a, **k):
        raise asyncio.CancelledError()
    monkeypatch.setattr(nhat_ky, "che_chuoi", huy)
    with pytest.raises(asyncio.CancelledError):
        nhat_ky.ghi("he_thong", "thong_tin", "x")


def test_so_khong_huu_han_khong_lam_hong_api(client):
    """Một dòng có NaN/inf (vd module tính ms hỏng) không được làm GET /api/nhat-ky trả 500 mãi mãi."""
    nhat_ky.ghi("kenh_mod", "loi", "đo hỏng", ms=float("nan"), ty_le=float("inf"), con={"a": float("-inf")})
    r = client.get("/api/nhat-ky", headers=QT)
    assert r.status_code == 200
    ban_ghi = [x for x in r.json() if x["kenh"] == "kenh_mod"][0]
    assert ban_ghi["ms"] is None


def test_api_con_tro_va_ngay_khong_hop_le_tra_400_khong_500(client):
    nhat_ky.ghi("he_thong", "thong_tin", "một dòng")
    # Con trỏ không có múi giờ: trước đây so datetime naive với aware → TypeError → 500
    r = client.get("/api/nhat-ky", headers=QT, params={"truoc": "2999-01-01T00:00:00"})
    assert r.status_code == 200 and len(r.json()) >= 1
    # Con trỏ rác: trước đây lặng lẽ bỏ qua → trả lại TRANG ĐẦU, khách phân trang lặp vô tận
    assert client.get("/api/nhat-ky", headers=QT, params={"truoc": "khong-phai-ngay"}).status_code == 400
    for n in ("nan", "inf", "1e10"):
        r = client.delete("/api/nhat-ky", headers=QT, params={"truoc_ngay": n})
        assert r.status_code == 400, (n, r.status_code)
    assert len(nhat_ky.doc()) >= 1  # không xoá gì


def test_model_lay_theo_cau_truc_khong_do_chu_trong_than(client):
    """`model` ghi vào nhật ký phải là trường «model» Ở GỐC thân mà lõi đã đọc — không dò regex khắp thân
    (bản cũ bắt «model» lồng trong metadata/tools — chuỗi do khách viết)."""
    nhat_ky.xoa_cu(truoc_ngay=0)
    r = client.post("/v1/chat/completions", headers=QT,
                    json={"model": 123, "metadata": {"model": "noi-dung-rieng-tu"},
                          "messages": [{"role": "user", "content": "alo"}]})
    assert r.status_code == 400
    dong = nhat_ky.doc(kenh="cuoc_goi")
    assert len(dong) == 1
    assert "noi-dung-rieng-tu" not in json.dumps(dong[0], ensure_ascii=False)


def test_x_request_id_khong_hop_le_bi_thay_va_co_trong_dong(client):
    nhat_ky.xoa_cu(truoc_ngay=0)
    for xau in ("a" * 300, "có dấu", "x y", "<script>"):
        r = client.get("/v1/models", headers=QT | {"x-request-id": xau.encode()})
        assert r.status_code == 200
        assert r.headers["x-request-id"].startswith("req.")  # bị thay bằng mã tự sinh, không phản chiếu
        assert nhat_ky.doc(kenh="cuoc_goi")[0]["them"]["request_id"] == r.headers["x-request-id"]


# ───── middleware chạy trực tiếp với ứng dụng ASGI giả: luồng đứt / bị huỷ / lỗi ─────

def _chay_mw(ung_dung, send=None):
    from app.nhat_ky import MiddlewareNhatKyCuocGoi
    mw = MiddlewareNhatKyCuocGoi(ung_dung)
    scope = {"type": "http", "method": "POST", "path": "/v1/chat/completions", "headers": [], "state": {}}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def gui_mac_dinh(msg):
        pass
    return mw(scope, receive, send or gui_mac_dinh)


async def _stream_nua_chung(scope, receive, send):
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/event-stream")]})
    await send({"type": "http.response.body", "body": b"data: {}\n\n", "more_body": True})
    await asyncio.sleep(10)


def test_mw_khach_cat_giua_stream_dung_mot_dong_danh_dau_dut(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    so_lan = {"n": 0}

    async def send(msg):
        so_lan["n"] += 1
        if so_lan["n"] == 2:
            raise OSError("khách đã ngắt")
    with pytest.raises(OSError):
        asyncio.run(_chay_mw(_stream_nua_chung, send))
    dong = nhat_ky.doc(kenh="cuoc_goi")
    assert len(dong) == 1
    assert dong[0]["them"].get("dut") is True and dong[0]["muc"] != "thong_tin"


def test_mw_tac_vu_bi_huy_giua_stream_dung_mot_dong(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    async def chinh():
        t = asyncio.create_task(_chay_mw(_stream_nua_chung))
        await asyncio.sleep(0.05)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    asyncio.run(chinh())
    dong = nhat_ky.doc(kenh="cuoc_goi")
    assert len(dong) == 1 and dong[0]["them"].get("dut") is True


def test_mw_ung_dung_ket_thuc_khong_gui_khuc_cuoi_bi_danh_dau_dut(tmp_path, monkeypatch):
    """BatLoiChuaBat (bên trong) nuốt lỗi giữa stream rồi trả về bình thường — middleware vẫn phải thấy luồng đứt."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    async def app_dut(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"x", "more_body": True})
    asyncio.run(_chay_mw(app_dut))
    dong = nhat_ky.doc(kenh="cuoc_goi")
    assert len(dong) == 1 and dong[0]["them"].get("dut") is True


def test_mw_ung_dung_nem_loi_truoc_dau_phan_hoi_mot_dong_500(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    async def app_loi(scope, receive, send):
        raise RuntimeError("hỏng")
    with pytest.raises(RuntimeError):
        asyncio.run(_chay_mw(app_loi))
    dong = nhat_ky.doc(kenh="cuoc_goi")
    assert len(dong) == 1 and dong[0]["them"]["ma_http"] == 500 and dong[0]["muc"] == "loi"


def test_xoay_vong_moi_tep_khong_vuot_tran_tep(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_NHAT_KY_TOI_DA_MB", "0.01")
    tran = int(0.01 * 1024 * 1024)
    for i in range(300):
        nhat_ky.ghi("cuoc_goi", "thong_tin", f"dòng {i}", chuoi="y" * 120)
    cac_tep = list(nhat_ky.thu_muc_nhat_ky().glob("nhat_ky*.jsonl"))
    assert len(cac_tep) == nhat_ky.SO_TEP_XOAY
    assert sum(p.stat().st_size for p in cac_tep) <= tran
    # dòng mới nhất còn đọc được, theo đúng thứ tự
    moi = nhat_ky.doc(gioi_han=3)
    assert [x["tom_tat"] for x in moi] == ["dòng 299", "dòng 298", "dòng 297"]


def test_module_gia_ghi_kenh_la_qua_hop_dong_import(client, tmp_path):
    """Hợp đồng module: `from app import nhat_ky; nhat_ky.ghi(...)` từ mã module ghi kênh riêng không phải sửa lõi;
    kênh lạ hiện nguyên văn ở /api/nhat-ky/kenh."""
    thu_muc_mod = tmp_path / "modules" / "mod-kenh"
    thu_muc_mod.mkdir(parents=True)
    (thu_muc_mod / "manifest.json").write_text(json.dumps({
        "id": "mod-kenh", "ten": "Module kênh", "mo_ta": "giả", "loai": "loai_la_x", "giao_thuc": "openai",
        "tien_to": "mkenh", "truong_cau_hinh": [], "tai_nguyen": {}}))
    (thu_muc_mod / "module.py").write_text("""
import json
from app import nhat_ky
class Module:
    def __init__(self, manifest): self.manifest = manifest
    def nap(self, cfg): pass
    async def cai(self, cfg): return {"ok": True, "cau_hinh_luu": {}}
    async def models(self): return [{"id": "mkenh/m"}]
    async def chat(self, body, stream):
        nhat_ky.ghi("kenh_mod_la_q7", "canh_bao", "module tự ghi", lan=1)
        return 200, {"content-type": "application/json"}, json.dumps({"choices": []}).encode()
    async def suc_khoe(self): return {"ok": True}
    async def dung(self): pass
""")
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-kenh/cai", headers=QT, json={"cau_hinh": {}})
    r = client.post("/v1/chat/completions", headers=QT, json={"model": "mkenh/m", "messages": []})
    assert r.status_code == 200
    kenh = {k["kenh"]: k for k in client.get("/api/nhat-ky/kenh", headers=QT).json()}
    assert kenh["kenh_mod_la_q7"]["so_dong"] == 1
    assert "cuoc_goi" in kenh and "he_thong" in kenh
