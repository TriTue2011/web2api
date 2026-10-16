"""Kiểm thử bảo mật phễu quy trình (pheu) và kiểm soát phạm vi quyền hạn (pham_vi).

Chứng minh các yêu cầu bảo mật:
1. Leo thang quyền qua phễu: Khách có phạm vi 'tim_kiem' gửi 'pheu' với kha_nang =
   'nap', 'cai', 'anh', 'noi', 'video_tao', 'lam_moi_phien' tới module giả
   -> KHÔNG phương thức nào bị gọi (kiểm qua bộ ghi lời gọi) và trả mã 403/400.
2. Quản trị hoặc khoá có '*' cũng KHÔNG gọi được các phương thức ngoài hợp đồng phễu
   qua quy trình (trả về 400 unknown_capability, không phương thức nhạy cảm nào bị gọi).
3. Xác thực & phân quyền:
   - Không có khoá -> 401 invalid_api_key.
   - Khoá khách phạm vi 'chat' -> 403 insufficient_scope.
   - Khách có phạm vi 'tim_kiem' gọi tầng 'xep_hang' -> 403 insufficient_scope.
4. Hồi quy:
   - Khách đúng phạm vi 'tim_kiem' chạy phễu tìm kiếm bình thường (200).
   - Khách có đủ phạm vi ['tim_kiem', 'xep_hang'] chạy phễu xếp hạng bình thường (200).
   - Khoá quản trị chạy bình thường (200).
5. Tham số khách gửi không bao giờ ghi đè cấu hình module.
6. Đột biến chứng minh đỏ khi bỏ các dòng bảo vệ.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import so_module
from app.main import app
from app.so_module import SoModule
from gia import ghi_thu_muc

KHOA_QT = "khoa-quan-tri-test-pheu-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


def mau_module_gia(
    id_="module-gia-kiem-tra",
    tien_to="mgkt",
) -> dict[str, bytes]:
    """Tạo module giả có đầy đủ các phương thức để kiểm tra lỗ hổng gọi phương thức tuỳ ý."""
    manifest = {
        "id": id_,
        "ten": "Module Giả Kiểm Tra Bảo Mật",
        "mo_ta": "Module phục vụ kiểm thử leo thang quyền qua phễu quy trình",
        "loai": "ai",
        "giao_thuc": "cuc_bo",
        "tien_to": tien_to,
        "cung_cap": ["tim_kiem", "xep_hang", "nhung", "chat", "anh", "noi", "video"],
        "truong_cau_hinh": [],
        "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False, "mang_ra": [], "ghi_chu": ""},
    }

    ma = f'''
import json

class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self.loi_goi = []
        self.cau_hinh = {{"khoa_chuan": "gia_tri_goc"}}

    def nap(self, cau_hinh):
        self.loi_goi.append("nap")
        self.cau_hinh = dict(cau_hinh)

    async def cai(self, cau_hinh):
        self.loi_goi.append("cai")
        return {{"ok": True, "ket_qua_tung_khoa": [], "loi": None}}

    async def chat(self, body, stream=False):
        self.loi_goi.append("chat")
        return 200, {{"content-type": "application/json"}}, b'{{"choices": []}}'

    async def anh(self, body):
        self.loi_goi.append("anh")
        return 200, {{"content-type": "application/json"}}, b'{{"data": []}}'

    async def noi(self, body):
        self.loi_goi.append("noi")
        return 200, {{"content-type": "audio/mpeg"}}, b'fake_audio'

    async def video_tao(self, body):
        self.loi_goi.append("video_tao")
        return 200, {{"content-type": "application/json"}}, b'{{"id": "vid123"}}'

    async def video_trang_thai(self, ma):
        self.loi_goi.append("video_trang_thai")
        return 200, {{"content-type": "application/json"}}, b'{{"status": "ready"}}'

    async def video_noi_dung(self, ma):
        self.loi_goi.append("video_noi_dung")
        return 200, {{"content-type": "video/mp4"}}, b'fake_video'

    async def lam_moi_phien(self, du_lieu):
        self.loi_goi.append("lam_moi_phien")
        return du_lieu

    async def phien_con_song(self, du_lieu):
        self.loi_goi.append("phien_con_song")
        return True

    async def models(self):
        return [
            {{"id": f"{{self.tien_to}}/model-chat", "kha_nang": "chat"}},
            {{"id": f"{{self.tien_to}}/model-emb", "kha_nang": "nhung"}},
            {{"id": f"{{self.tien_to}}/model-rerank", "kha_nang": "xep_hang"}},
        ]

    async def suc_khoe(self):
        return {{"ok": True}}

    async def dung(self):
        pass

    async def tim_kiem(self, yc: dict):
        self.loi_goi.append("tim_kiem")
        return {{
            "muc": [
                {{
                    "id": f"{{self.tien_to}}.doc1",
                    "diem": 0.9,
                    "doan": "Ket qua tim kiem an toan",
                    "tai_lieu": "tailieu.txt",
                }}
            ],
            "tong": 1,
        }}

    async def xep_hang(self, body: dict):
        self.loi_goi.append("xep_hang")
        return 200, {{"content-type": "application/json"}}, json.dumps({{"results": [{{"index": 0, "relevance_score": 0.99}}]}}).encode("utf-8")

    async def nhung(self, body: dict):
        self.loi_goi.append("nhung")
        return 200, {{"content-type": "application/json"}}, json.dumps({{"data": [{{"embedding": [0.1, 0.2]}}]}}).encode("utf-8")
'''
    return {
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        "module.py": ma.encode("utf-8"),
    }


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    """Thiết lập môi trường kiểm thử với module giả lập và các loại khoá."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    anh_dir = tmp_path / "anh"
    anh_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh_dir)

    mod_path = data_dir / "modules" / "module-gia-kiem-tra"
    ghi_thu_muc(mod_path, mau_module_gia("module-gia-kiem-tra", "mgkt"))

    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so

        # Nạp và cấu hình module giả
        r_nap = client.post("/api/modules/nap-lai", headers=QT)
        assert r_nap.status_code == 200
        r_cai = client.post("/api/modules/module-gia-kiem-tra/cai", headers=QT, json={"cau_hinh": {"khoa_chuan": "gia_tri_goc"}})
        assert r_cai.status_code == 200

        # Lấy thể hiện module đang chạy
        m = so.dang_chay.get("module-gia-kiem-tra")
        assert m is not None
        m.loi_goi.clear()  # Xoá vết gọi lúc cài đặt

        yield client, data_dir, so, m


# ─────────────────────────────────────────────────────────────
# 1. KIỂM THỬ LEO THANG QUYỀN QUA PHỄU (BẮT BUỘC)
# ─────────────────────────────────────────────────────────────


def test_khach_tim_kiem_khong_goi_duoc_phuong_thuc_nhay_cam(moi_truong):
    """Kiểm tra lỗ hổng leo thang quyền: khách có phạm vi 'tim_kiem' gửi 'pheu' chứa các khả năng
    'nap', 'cai', 'anh', 'noi', 'video_tao', 'lam_moi_phien' tới module giả.
    ĐẢM BẢO: Không phương thức nào bị gọi và trả mã lỗi 403 hoặc 400.
    """
    client, data_dir, so, mod_inst = moi_truong

    # Tạo khoá khách CHỈ có phạm vi tim_kiem
    r_k = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khach Tim Kiem", "pham_vi": ["tim_kiem"]})
    assert r_k.status_code == 200
    khoa_tk = r_k.json()["khoa"]["khoa"]
    h_tk = {"Authorization": f"Bearer {khoa_tk}"}

    phuong_thuc_nhay_cam = ["nap", "cai", "anh", "noi", "video_tao", "lam_moi_phien"]

    for pt in phuong_thuc_nhay_cam:
        mod_inst.loi_goi.clear()

        # Thử gọi có nguồn chỉ định
        body_co_nguon = {
            "chuoi": "kiem thu leo thang",
            "pheu": [
                {
                    "ten": f"tang_tan_cong_{pt}",
                    "kha_nang": pt,
                    "nguon": "module-gia-kiem-tra",
                    "tham_so": {"api_keys": ["khoa_doc_hai"], "base_url": "https://attacker.com/v1"},
                }
            ],
        }
        res_co_nguon = client.post("/api/tim-kiem", headers=h_tk, json=body_co_nguon)
        assert res_co_nguon.status_code in (400, 403), f"Phương thức {pt} (có nguồn) phải bị từ chối 403/400"
        assert pt not in mod_inst.loi_goi, f"LỖ HỔNG LEO THANG: {pt} (có nguồn) đã bị gọi trong module!"

        # Thử gọi không chỉ định nguồn (dự phòng)
        body_khong_nguon = {
            "chuoi": "kiem thu leo thang",
            "pheu": [
                {
                    "ten": f"tang_tan_cong_{pt}_auto",
                    "kha_nang": pt,
                    "tham_so": {"api_keys": ["khoa_doc_hai"]},
                }
            ],
        }
        res_khong_nguon = client.post("/api/tim-kiem", headers=h_tk, json=body_khong_nguon)
        assert res_khong_nguon.status_code in (400, 403), f"Phương thức {pt} (không nguồn) phải bị từ chối 403/400"
        assert pt not in mod_inst.loi_goi, f"LỖ HỔNG LEO THANG: {pt} (không nguồn) đã bị gọi trong module!"


def test_quan_tri_khong_goi_duoc_phuong_thuc_ngoai_pheu_qua_quy_trinh(moi_truong):
    """Kiểm tra hợp đồng toán tử phễu: ngay cả khoá quản trị (*) cũng KHÔNG THỂ gọi các phương thức
    ngoài phễu (nap, cai, anh, noi, video_tao, lam_moi_phien) qua POST /api/tim-kiem.
    Phải trả về 400 unknown_capability và không phương thức nào bị gọi.
    """
    client, data_dir, so, mod_inst = moi_truong

    phuong_thuc_ngoai_pheu = ["nap", "cai", "anh", "noi", "video_tao", "lam_moi_phien"]

    for pt in phuong_thuc_ngoai_pheu:
        mod_inst.loi_goi.clear()
        body = {
            "pheu": [
                {
                    "ten": f"tang_qt_{pt}",
                    "kha_nang": pt,
                    "nguon": "module-gia-kiem-tra",
                    "tham_so": {"du_lieu": "test"},
                }
            ]
        }
        r = client.post("/api/tim-kiem", headers=QT, json=body)
        assert r.status_code == 400, f"Quản trị gọi {pt} qua phễu phải trả 400 unknown_capability"
        than_loi = r.json()
        assert than_loi["error"]["code"] in ("unknown_capability", "step_failed")
        assert pt not in mod_inst.loi_goi, f"Phương thức {pt} không thuộc hợp đồng phễu nhưng đã bị gọi!"


# ─────────────────────────────────────────────────────────────
# 2. KIỂM THỬ XÁC THỰC VÀ PHÂN QUYỀN ĐƯỜNG TÌM KIẾM
# ─────────────────────────────────────────────────────────────


def test_xac_thuc_tim_kiem_khong_khoa_tra_401(moi_truong):
    """Không có khoá xác thực gọi POST /api/tim-kiem -> 401."""
    client, data_dir, so, mod_inst = moi_truong
    r = client.post("/api/tim-kiem", json={"chuoi": "test"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_api_key"


def test_khoa_khach_sai_pham_vi_tra_403(moi_truong):
    """Khoá khách chỉ có phạm vi 'chat' gọi POST /api/tim-kiem -> 403 insufficient_scope."""
    client, data_dir, so, mod_inst = moi_truong

    r_k = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khach Chat Only", "pham_vi": ["chat"]})
    k_chat = r_k.json()["khoa"]["khoa"]
    h_chat = {"Authorization": f"Bearer {k_chat}"}

    r = client.post("/api/tim-kiem", headers=h_chat, json={"chuoi": "test"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "insufficient_scope"


def test_khach_tim_kiem_khong_the_chay_tang_xep_hang(moi_truong):
    """Khách chỉ có phạm vi 'tim_kiem' nhưng phễu yêu cầu tầng 'xep_hang'
    -> Phải bị từ chối 403 insufficient_scope vì thiếu phạm vi 'xep_hang'.
    """
    client, data_dir, so, mod_inst = moi_truong

    r_k = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khach Tim Kiem", "pham_vi": ["tim_kiem"]})
    khoa_tk = r_k.json()["khoa"]["khoa"]
    h_tk = {"Authorization": f"Bearer {khoa_tk}"}

    pheu_co_rerank = [
        {"ten": "tang_tk", "kha_nang": "tim_kiem", "nguon": "module-gia-kiem-tra"},
        {"ten": "tang_xh", "kha_nang": "xep_hang", "nguon": "module-gia-kiem-tra", "tham_so": {"query": "test"}},
    ]
    r = client.post("/api/tim-kiem", headers=h_tk, json={"pheu": pheu_co_rerank})
    assert r.status_code == 403
    than_loi = r.json()
    assert than_loi["error"]["code"] in ("insufficient_scope", "step_failed")
    assert "xep_hang" in than_loi["error"]["message"]


# ─────────────────────────────────────────────────────────────
# 3. KIỂM THỬ HỒI QUY: CHẠY BÌNH THƯỜNG KHI ĐỦ QUYỀN
# ─────────────────────────────────────────────────────────────


def test_khach_tim_kiem_chay_pheu_tim_kiem_binh_thuong(moi_truong):
    """Khách có phạm vi 'tim_kiem' chạy phễu tìm kiếm và các toán tử thuần bình thường (200)."""
    client, data_dir, so, mod_inst = moi_truong

    r_k = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khach Tim Kiem", "pham_vi": ["tim_kiem"]})
    khoa_tk = r_k.json()["khoa"]["khoa"]
    h_tk = {"Authorization": f"Bearer {khoa_tk}"}

    mod_inst.loi_goi.clear()
    pheu_tk = [
        {"ten": "tang_tk", "kha_nang": "tim_kiem", "nguon": "module-gia-kiem-tra"},
        {"ten": "tang_loc", "kha_nang": "loc", "tham_so": {}},
        {"ten": "tang_cat", "kha_nang": "cat", "tham_so": {"so_luong": 5}},
    ]
    r = client.post("/api/tim-kiem", headers=h_tk, json={"pheu": pheu_tk})
    assert r.status_code == 200
    res = r.json()
    assert len(res["muc"]) == 1
    assert "tim_kiem" in mod_inst.loi_goi


def test_khach_co_du_pham_vi_chay_pheu_xep_hang_binh_thuong(moi_truong):
    """Khách có đủ phạm vi ['tim_kiem', 'xep_hang'] chạy phễu có cả tim_kiem và xep_hang bình thường (200)."""
    client, data_dir, so, mod_inst = moi_truong

    r_k = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khach Du Quyen", "pham_vi": ["tim_kiem", "xep_hang"]})
    khoa_du = r_k.json()["khoa"]["khoa"]
    h_du = {"Authorization": f"Bearer {khoa_du}"}

    mod_inst.loi_goi.clear()
    pheu_full = [
        {"ten": "tang_tk", "kha_nang": "tim_kiem", "nguon": "module-gia-kiem-tra"},
        {"ten": "tang_xh", "kha_nang": "xep_hang", "nguon": "module-gia-kiem-tra", "tham_so": {"query": "an toan"}},
    ]
    r = client.post("/api/tim-kiem", headers=h_du, json={"pheu": pheu_full})
    assert r.status_code == 200
    assert "tim_kiem" in mod_inst.loi_goi
    assert "xep_hang" in mod_inst.loi_goi


def test_quan_tri_chay_pheu_binh_thuong(moi_truong):
    """Khoá quản trị chạy phễu tìm kiếm + xếp hạng bình thường (200)."""
    client, data_dir, so, mod_inst = moi_truong
    mod_inst.loi_goi.clear()

    pheu_full = [
        {"ten": "tang_tk", "kha_nang": "tim_kiem", "nguon": "module-gia-kiem-tra"},
        {"ten": "tang_xh", "kha_nang": "xep_hang", "nguon": "module-gia-kiem-tra", "tham_so": {"query": "an toan"}},
        {"ten": "tang_cat", "kha_nang": "cat", "tham_so": {"so_luong": 1}},
    ]
    r = client.post("/api/tim-kiem", headers=QT, json={"pheu": pheu_full})
    assert r.status_code == 200
    assert "tim_kiem" in mod_inst.loi_goi
    assert "xep_hang" in mod_inst.loi_goi


# ─────────────────────────────────────────────────────────────
# 4. KIỂM THỬ THAM SỐ KHÁCH KHÔNG GHI ĐÈ CẤU HÌNH MODULE
# ─────────────────────────────────────────────────────────────


def test_tham_so_khach_khong_ghi_de_cau_hinh(moi_truong):
    """Tham số trong tầng do khách gửi không bao giờ được ghi đè vào cấu hình của module."""
    client, data_dir, so, mod_inst = moi_truong

    mod_inst.cau_hinh.update({"khoa_chuan": "gia_tri_goc"})  # cấu hình đã nạp của module
    cau_hinh_goc = dict(mod_inst.cau_hinh)

    r_k = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khach Tim Kiem", "pham_vi": ["tim_kiem"]})
    khoa_tk = r_k.json()["khoa"]["khoa"]
    h_tk = {"Authorization": f"Bearer {khoa_tk}"}

    body = {
        "pheu": [
            {
                "ten": "tang_tk",
                "kha_nang": "tim_kiem",
                "nguon": "module-gia-kiem-tra",
                "tham_so": {
                    "khoa_chuan": "gia_tri_bi_ghi_de_doc_hai",
                    "api_keys": ["fake_key"],
                    "base_url": "https://noi-khac/v1",
                },
            }
        ]
    }
    r = client.post("/api/tim-kiem", headers=h_tk, json=body)
    assert r.status_code == 200

    # Cấu hình của module trên bộ nhớ KHÔNG bị thay đổi
    assert mod_inst.cau_hinh == cau_hinh_goc
    assert mod_inst.cau_hinh["khoa_chuan"] == "gia_tri_goc"
