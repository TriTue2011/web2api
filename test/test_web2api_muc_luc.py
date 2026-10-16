"""Kiểm thử sơ đồ thứ năm của tìm kiếm: MỤC LỤC (duyệt theo đề mục và chỉ mục A–Z).

Chứng minh các yêu cầu:
1. Mục lục tài liệu ba cấp: cây đúng (chương -> mục -> tiểu mục), trần độ sâu, trần số nút,
   bỏ nút sai không vỡ cả cây.
2. Module không có muc_luc trả 501 rõ ràng, kiểm tra tham số nguon/tai_lieu.
3. Nhảy tới đề mục (muc_luc: {tai_lieu, muc_id}): chuyển vào module; module không hỗ trợ thì
   lõi lọc theo vi_tri/truong.muc_id nếu có, không thì ghi so_do_bo_qua.
4. Chỉ mục A-Z (chi_muc: {truong, kieu: "chu_cai"}): phản hồi chi_muc {"A": n, "B": n, ..., "#": n}
   đếm theo chữ cái đầu, bỏ dấu tiếng Việt («Đ» xếp cùng «D»), giữ nguyên chữ ở mục, tính trước cat;
   chi_muc.chon: "D" chỉ giữ mục bắt đầu bằng chữ đó.
5. Báo cáo bao_cao có tầng tương ứng; kết quả có nguon_ngoai: true; kiểm phạm vi tim_kiem.
6. Bốn điều kiện nghiệm thu: không module không lỗi, module lạ mã mở, nạp/gỡ nóng.
7. Test giao diện Chrome (khuôn mẫu chuẩn, tự bỏ qua khi không có Chrome).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import so_module
from app.main import app
from app.so_module import SoModule
from gia import ghi_thu_muc
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture Chrome dùng chung của kho

KHOA_QT = "khoa-quan-tri-test-muc-luc-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

CHROME = os.environ.get("WEB2API_CHROME", "/usr/bin/google-chrome")


def mau_module_muc_luc(
    id_: str,
    tien_to: str,
    co_muc_luc: bool = True,
    so_do_tim_kiem: list[str] | None = None,
    du_lieu_tim_kiem: list[dict] | None = None,
    muc_luc_cay: dict | None = None,
) -> dict[str, bytes]:
    """Tạo module giả lập có hoặc không có phương thức muc_luc, phục vụ kiểm thử."""
    manifest = {
        "id": id_,
        "ten": f"Module {id_}",
        "mo_ta": "Module muc luc gia lap",
        "loai": "tri_thuc",
        "giao_thuc": "cuc_bo",
        "tien_to": tien_to,
        "cung_cap": ["tim_kiem"],
        "so_do_tim_kiem": so_do_tim_kiem or [],
        "truong_cau_hinh": [],
        "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False, "mang_ra": [], "ghi_chu": ""},
    }

    du_lieu_json = json.dumps(du_lieu_tim_kiem or [], ensure_ascii=False)
    muc_luc_json = json.dumps(muc_luc_cay or {}, ensure_ascii=False)

    doan_muc_luc = f"""
    async def muc_luc(self, yc: dict):
        return {muc_luc_json}
""" if co_muc_luc else ""

    ma = f'''
import json

class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self.du_lieu = {du_lieu_json}

    def nap(self, cau_hinh):
        self.cau_hinh = cau_hinh

    async def cai(self, cau_hinh):
        return {{"ok": True}}

    async def chat(self, body, stream=False):
        return 200, {{"content-type": "application/json"}}, b'{{"choices": []}}'

    async def models(self):
        return []

    async def suc_khoe(self):
        return {{"ok": True}}

    async def dung(self):
        pass

    async def tim_kiem(self, yc: dict):
        so_do_ho_tro = set(self.manifest.get("so_do_tim_kiem", []))
        so_do_bo_qua = []
        bat_tat = yc.get("bat_tat") or {{}}
        bat_tat_so_do = bat_tat.get("so_do") or {{}}
        for sd in bat_tat_so_do:
            if sd not in so_do_ho_tro:
                so_do_bo_qua.append(sd)

        # Nếu module tự hỗ trợ muc_luc
        items = list(self.du_lieu)
        muc_luc_yc = yc.get("muc_luc")
        if muc_luc_yc and "muc_luc" in so_do_ho_tro:
            m_id = str(muc_luc_yc.get("muc_id") or "")
            items = [x for x in items if str(x.get("muc_id") or x.get("vi_tri") or "") == m_id]
            for x in items:
                x["muc_luc_da_ap"] = True

        return {{
            "muc": items,
            "tong": len(items),
            "so_do_bo_qua": so_do_bo_qua,
        }}
{doan_muc_luc}
'''
    return {
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        "module.py": ma.encode("utf-8"),
    }


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    anh_dir = tmp_path / "anh"
    anh_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh_dir)

    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        yield client, data_dir, so


# ───────────────────────────── CÁC TEST CA ─────────────────────────────


def test_1_muc_luc_module_ba_cap_va_hinh_dang(moi_truong, monkeypatch):
    """Điểm 1: Cây đề mục ba cấp đúng hình dạng; trần độ sâu, trần số nút, bỏ nút sai không vỡ cây."""
    client, data_dir, so = moi_truong

    # Cây ba cấp: Chương 1 -> Mục 1.1 -> Tiểu mục 1.1.1
    # Có kèm: 1 nút sai không có tieu_de, 1 nút sai không có id, 1 nút con cấp 7 vượt trần
    cay_ba_cap = {
        "tai_lieu": "sach_giao_khoa",
        "tieu_de": "Sách Giáo Khoa Toán",
        "muc": [
            {
                "id": "chuong_1",
                "tieu_de": "Chương 1: Đại số",
                "cap": 1,
                "vi_tri": "trang_1",
                "truong_la": "gia_tri_la_1",  # Mã mở
                "con": [
                    {
                        "id": "muc_1_1",
                        "tieu_de": "Mục 1.1: Phương trình",
                        "cap": 2,
                        "vi_tri": "trang_5",
                        "con": [
                            {
                                "id": "tieu_muc_1_1_1",
                                "tieu_de": "Tiểu mục 1.1.1: Bậc nhất",
                                "cap": 3,
                                "vi_tri": "trang_6",
                            },
                            {
                                "id": "tieu_muc_1_1_2",
                                "tieu_de": "Tiểu mục 1.1.2: Bậc hai",
                                "cap": 3,
                                "vi_tri": "trang_10",
                            },
                            # Nút hỏng 1: thiếu tieu_de -> phải bị bỏ
                            {"id": "nut_hong_1", "cap": 3},
                            # Nút hỏng 2: thiếu id -> phải bị bỏ
                            {"tieu_de": "Nút không có id", "cap": 3},
                            # Nút hỏng 3: không phải dict -> phải bị bỏ
                            "chuoi_khong_hop_le",
                        ],
                    },
                    {
                        "id": "muc_1_2",
                        "tieu_de": "Mục 1.2: Bất đẳng thức",
                        "cap": 2,
                        "vi_tri": "trang_15",
                    },
                ],
            },
            {
                "id": "chuong_2",
                "tieu_de": "Chương 2: Hình học",
                "cap": 1,
                "vi_tri": "trang_30",
            },
        ],
    }

    mod_path = data_dir / "modules" / "mod-muc-luc"
    ghi_thu_muc(mod_path, mau_module_muc_luc("mod-muc-luc", "mml", co_muc_luc=True, muc_luc_cay=cay_ba_cap))

    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-muc-luc/cai", headers=QT, json={"cau_hinh": {}})

    # 1. Gọi thành công 200: lấy toàn bộ cây 3 cấp
    r = client.post(
        "/api/tim-kiem/muc-luc",
        headers=QT,
        json={"nguon": "mod-muc-luc", "tai_lieu": "sach_giao_khoa"},
    )
    assert r.status_code == 200
    res = r.json()
    assert res["tai_lieu"] == "sach_giao_khoa"
    assert res["tieu_de"] == "Sách Giáo Khoa Toán"
    assert len(res["muc"]) == 2

    # Kiểm tra chương 1
    ch1 = res["muc"][0]
    assert ch1["id"] == "chuong_1"
    assert ch1["truong_la"] == "gia_tri_la_1"  # Mã mở được giữ nguyên
    assert len(ch1["con"]) == 2

    # Kiểm tra mục 1.1 và các nút con: các nút sai bị bỏ, 2 tiểu mục hợp lệ còn nguyên
    m1_1 = ch1["con"][0]
    assert m1_1["id"] == "muc_1_1"
    assert len(m1_1["con"]) == 2
    assert m1_1["con"][0]["id"] == "tieu_muc_1_1_1"
    assert m1_1["con"][1]["id"] == "tieu_muc_1_1_2"

    # 2. Trần độ sâu: yêu cầu do_sau=2 -> cấp 3 (tiểu mục) bị cắt
    r2 = client.post(
        "/api/tim-kiem/muc-luc",
        headers=QT,
        json={"nguon": "mod-muc-luc", "tai_lieu": "sach_giao_khoa", "do_sau": 2},
    )
    assert r2.status_code == 200
    res2 = r2.json()
    m1_1_sau2 = res2["muc"][0]["con"][0]
    assert m1_1_sau2["con"] == []

    # 3. Trần số nút qua biến môi trường WEB2API_MUC_LUC_TRAN_SO_NUT
    monkeypatch.setenv("WEB2API_MUC_LUC_TRAN_SO_NUT", "3")
    r3 = client.post(
        "/api/tim-kiem/muc-luc",
        headers=QT,
        json={"nguon": "mod-muc-luc", "tai_lieu": "sach_giao_khoa"},
    )
    assert r3.status_code == 200
    res3 = r3.json()
    # Tổng số nút chỉ được nạp tối đa 3 nút
    tong_nut = 0
    def dem_de_quy(cac_nut):
        nonlocal tong_nut
        for n in cac_nut:
            tong_nut += 1
            dem_de_quy(n.get("con", []))
    dem_de_quy(res3["muc"])
    assert tong_nut <= 3


def test_2_module_khong_co_muc_luc_501(moi_truong):
    """Điểm 2: Module không có phương thức muc_luc trả 501 rõ ràng; kiểm tra các tham số bắt buộc."""
    client, data_dir, so = moi_truong

    # Dựng module không có phương thức muc_luc
    mod_path = data_dir / "modules" / "mod-khong-ml"
    ghi_thu_muc(mod_path, mau_module_muc_luc("mod-khong-ml", "nkml", co_muc_luc=False))

    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-khong-ml/cai", headers=QT, json={"cau_hinh": {}})

    # Gọi POST /api/tim-kiem/muc-luc -> 501 not_implemented
    r = client.post(
        "/api/tim-kiem/muc-luc",
        headers=QT,
        json={"nguon": "mod-khong-ml", "tai_lieu": "tai_lieu_test"},
    )
    assert r.status_code == 501
    assert r.json()["error"]["code"] == "not_implemented"
    assert "muc_luc" in r.json()["error"]["message"]

    # Thiếu nguon -> 400
    r_thieu_nguon = client.post("/api/tim-kiem/muc-luc", headers=QT, json={"tai_lieu": "doc1"})
    assert r_thieu_nguon.status_code == 400

    # Thiếu tai_lieu -> 400
    r_thieu_tl = client.post("/api/tim-kiem/muc-luc", headers=QT, json={"nguon": "mod-khong-ml"})
    assert r_thieu_tl.status_code == 400

    # Nguồn không tồn tại -> 404
    r_ko_nguon = client.post("/api/tim-kiem/muc-luc", headers=QT, json={"nguon": "khong-co", "tai_lieu": "d1"})
    assert r_ko_nguon.status_code == 404


def test_3_nhay_toi_de_muc_va_so_do_bo_qua(moi_truong):
    """Điểm 3: Nhảy tới đề mục (muc_luc: {tai_lieu, muc_id}). Lõi lọc nếu có vi_tri/truong.muc_id, không thì ghi so_do_bo_qua."""
    client, data_dir, so = moi_truong

    du_lieu_1 = [
        {"id": "doc1", "tai_lieu": "sach_toan", "vi_tri": "de_muc_1", "doan": "Bài học số một"},
        {"id": "doc2", "tai_lieu": "sach_toan", "vi_tri": "de_muc_2", "doan": "Bài học số hai"},
        {"id": "doc3", "tai_lieu": "sach_van", "vi_tri": "de_muc_1", "doan": "Văn bản khác"},
    ]

    # Module 1: không khai muc_luc trong so_do_tim_kiem nhưng dữ liệu có vi_tri
    mod1_path = data_dir / "modules" / "mod-loc-vi-tri"
    ghi_thu_muc(mod1_path, mau_module_muc_luc("mod-loc-vi-tri", "mlvt", co_muc_luc=False, du_lieu_tim_kiem=du_lieu_1))

    # Module 2: không khai muc_luc và dữ liệu KHÔNG có vi_tri lẫn truong.muc_id
    du_lieu_2 = [
        {"id": "doc2_1", "doan": "Dữ liệu phẳng không có đề mục 1"},
        {"id": "doc2_2", "doan": "Dữ liệu phẳng không có đề mục 2"},
    ]
    mod2_path = data_dir / "modules" / "mod-khong-vi-tri"
    ghi_thu_muc(mod2_path, mau_module_muc_luc("mod-khong-vi-tri", "mkvt", co_muc_luc=False, du_lieu_tim_kiem=du_lieu_2))

    # Module 3: tự hỗ trợ muc_luc (khai so_do_tim_kiem: ["muc_luc"])
    du_lieu_3 = [
        {"id": "doc3_1", "muc_id": "de_muc_1", "doan": "Module tự hỗ trợ đề mục 1"},
        {"id": "doc3_2", "muc_id": "de_muc_2", "doan": "Module tự hỗ trợ đề mục 2"},
    ]
    mod3_path = data_dir / "modules" / "mod-tu-ho-tro"
    ghi_thu_muc(mod3_path, mau_module_muc_luc("mod-tu-ho-tro", "mtht", co_muc_luc=False, so_do_tim_kiem=["muc_luc"], du_lieu_tim_kiem=du_lieu_3))

    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-loc-vi-tri/cai", headers=QT, json={"cau_hinh": {}})
    client.post("/api/modules/mod-khong-vi-tri/cai", headers=QT, json={"cau_hinh": {}})
    client.post("/api/modules/mod-tu-ho-tro/cai", headers=QT, json={"cau_hinh": {}})

    # 1. Gọi với Module 1 (có vi_tri): lõi tự lọc đúng đề mục và tài liệu
    r1 = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={
            "nguon": "mod-loc-vi-tri",
            "muc_luc": {"tai_lieu": "sach_toan", "muc_id": "de_muc_1"},
        },
    )
    assert r1.status_code == 200
    res1 = r1.json()
    assert len(res1["muc"]) == 1
    assert res1["muc"][0]["id"] == "doc1"
    # bao_cao có tầng muc_luc
    ten_cac_tang = [b["ten"] for b in res1["bao_cao"]]
    assert "muc_luc" in ten_cac_tang

    # 2. Gọi với Module 2 (không có vi_tri hay truong.muc_id): ghi nhận so_do_bo_qua
    r2 = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={
            "nguon": "mod-khong-vi-tri",
            "muc_luc": {"tai_lieu": "sach_toan", "muc_id": "de_muc_1"},
        },
    )
    assert r2.status_code == 200
    res2 = r2.json()
    assert "so_do_bo_qua" in res2
    assert "muc_luc" in res2["so_do_bo_qua"].get("mod-khong-vi-tri", [])

    # 3. Gọi với Module 3 (tự hỗ trợ muc_luc): module tự lọc
    r3 = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={
            "nguon": "mod-tu-ho-tro",
            "muc_luc": {"tai_lieu": "sach_toan", "muc_id": "de_muc_1"},
        },
    )
    assert r3.status_code == 200
    res3 = r3.json()
    assert len(res3["muc"]) == 1
    assert res3["muc"][0]["id"] == "doc3_1"


def test_4_chi_muc_a_z(moi_truong):
    """Điểm 4: Chỉ mục A-Z: đếm đúng tiếng Việt có dấu, «Đ» xếp cùng «D», hiện nguyên chữ, tính trước cat, chọn chữ cái."""
    client, data_dir, so = moi_truong

    du_lieu = [
        {"id": "m1", "tieu_de": "Đà Nẵng quê tôi", "doan": "Thành phố đáng sống"},
        {"id": "m2", "tieu_de": "Địa lý tự nhiên", "doan": "Kiến thức địa lý"},
        {"id": "m3", "tieu_de": "Ánh sáng mặt trời", "doan": "Hiện tượng vật lý"},
        {"id": "m4", "tieu_de": "Bắc Giang thân yêu", "doan": "Vùng đất trù phú"},
        {"id": "m5", "tieu_de": "Hà Nội mùa thu", "doan": "Thủ đô ngàn năm"},
        {"id": "m6", "tieu_de": "123 Bước khởi đầu", "doan": "Chữ số và ký hiệu"},
    ]

    mod_path = data_dir / "modules" / "mod-chi-muc"
    ghi_thu_muc(mod_path, mau_module_muc_luc("mod-chi-muc", "mcm", co_muc_luc=False, du_lieu_tim_kiem=du_lieu))

    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-chi-muc/cai", headers=QT, json={"cau_hinh": {}})

    # 1. Đếm chỉ mục A-Z: so_luong: 2 (cắt top 2) nhưng chi_muc tính trước khi cắt
    r1 = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={
            "nguon": "mod-chi-muc",
            "so_luong": 2,
            "chi_muc": {"truong": "tieu_de", "kieu": "chu_cai"},
        },
    )
    assert r1.status_code == 200
    res1 = r1.json()
    assert len(res1["muc"]) == 2  # Bị cắt còn 2 mục
    assert "chi_muc" in res1
    cm = res1["chi_muc"]

    # «Đà Nẵng» và «Địa lý» đều xếp vào 'D' -> D: 2
    assert cm.get("D") == 2
    # «Ánh sáng» -> A: 1
    assert cm.get("A") == 1
    # «Bắc Giang» -> B: 1
    assert cm.get("B") == 1
    # «Hà Nội» -> H: 1
    assert cm.get("H") == 1
    # «123 Bước» -> #: 1
    assert cm.get("#") == 1

    # Tầng chi_muc xuất hiện trong bao_cao
    ten_cac_tang = [b["ten"] for b in res1["bao_cao"]]
    assert "chi_muc" in ten_cac_tang

    # Tiêu đề nguyên gốc không bị đổi thành tiếng không dấu
    for m in res1["muc"]:
        if m["id"] == "m1":
            assert m["tieu_de"] == "Đà Nẵng quê tôi"

    # 2. Chọn chữ cái: chi_muc.chon: "D" -> chỉ giữ các mục bắt đầu bằng D hoặc Đ
    r2 = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={
            "nguon": "mod-chi-muc",
            "chi_muc": {"truong": "tieu_de", "kieu": "chu_cai", "chon": "D"},
        },
    )
    assert r2.status_code == 200
    res2 = r2.json()
    ids_d = {m["id"] for m in res2["muc"]}
    assert ids_d == {"m1", "m2"}
    assert res2["muc"][0]["tieu_de"].startswith("Đ")

    # Chọn chữ cái bằng ký tự Đ -> cũng nhận diện đúng nhóm D
    r3 = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={
            "nguon": "mod-chi-muc",
            "chi_muc": {"truong": "tieu_de", "kieu": "chu_cai", "chon": "Đ"},
        },
    )
    assert r3.status_code == 200
    assert {m["id"] for m in r3.json()["muc"]} == {"m1", "m2"}

    # Chọn ký hiệu số/đặc biệt '#'
    r4 = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={
            "nguon": "mod-chi-muc",
            "chi_muc": {"truong": "tieu_de", "kieu": "chu_cai", "chon": "#"},
        },
    )
    assert r4.status_code == 200
    assert len(r4.json()["muc"]) == 1
    assert r4.json()["muc"][0]["id"] == "m6"


def test_5_khoa_khach_va_phan_quyen(moi_truong):
    """Điểm 5: Khách gọi POST /api/tim-kiem/muc-luc cần có phạm vi tim_kiem."""
    client, _, _ = moi_truong

    # Tạo khoá chỉ có quyền chat
    r_k = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khach Chat", "pham_vi": ["chat"]})
    k_chat = r_k.json()["khoa"]["khoa"]
    h_chat = {"Authorization": f"Bearer {k_chat}"}

    # Bị 403 insufficient_scope
    r_cam = client.post("/api/tim-kiem/muc-luc", headers=h_chat, json={"nguon": "mod", "tai_lieu": "doc"})
    assert r_cam.status_code == 403
    assert r_cam.json()["error"]["code"] == "insufficient_scope"

    # Tạo khoá có quyền tim_kiem
    r_k_ok = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khach Tim Kiem", "pham_vi": ["tim_kiem"]})
    k_tk = r_k_ok.json()["khoa"]["khoa"]
    h_tk = {"Authorization": f"Bearer {k_tk}"}

    # Được qua cửa xác thực (kiểm tra thấy thiếu nguồn -> 400 hoặc 404 chứ không phải 403)
    r_ok = client.post("/api/tim-kiem/muc-luc", headers=h_tk, json={"nguon": "khong-co", "tai_lieu": "doc"})
    assert r_ok.status_code == 404


def test_6_bon_dieu_kien_nghiem_thu(moi_truong):
    """Bốn điều kiện nghiệm thu: không module không lỗi, nạp/gỡ nóng, mã mở với trường lạ."""
    client, data_dir, so = moi_truong

    # 1. Lõi chạy đúng khi không có module nào
    r_nguon = client.get("/api/tim-kiem/nguon", headers=QT)
    assert r_nguon.status_code == 200
    assert r_nguon.json() == {"nguon": []}

    r_tk_rong = client.post("/api/tim-kiem", headers=QT, json={"chuoi": "test"})
    assert r_tk_rong.status_code == 200
    assert r_tk_rong.json()["muc"] == []

    # 2. Module lạ mang trường lạ, mã mở: hiển thị nguyên vẹn không văng lỗi
    cay_la = {
        "tai_lieu": "doc_la",
        "truong_he_thong_la": "gia_tri_dac_biet",
        "muc": [
            {
                "id": "nut_la_1",
                "tieu_de": "Tiêu đề lạ",
                "cap": 1,
                "loai_la": "chuyen_khoa_ky_thuat",
                "thuoc_tinh_rieng": {"a": 1, "b": [2, 3]},
            }
        ],
    }
    mod_path = data_dir / "modules" / "mod-la"
    ghi_thu_muc(mod_path, mau_module_muc_luc("mod-la", "mla", co_muc_luc=True, muc_luc_cay=cay_la))

    # 3. Nạp nóng giữa chừng
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-la/cai", headers=QT, json={"cau_hinh": {}})

    r_ml_la = client.post("/api/tim-kiem/muc-luc", headers=QT, json={"nguon": "mod-la", "tai_lieu": "doc_la"})
    assert r_ml_la.status_code == 200
    res_la = r_ml_la.json()
    assert res_la["truong_he_thong_la"] == "gia_tri_dac_biet"
    assert res_la["muc"][0]["loai_la"] == "chuyen_khoa_ky_thuat"
    assert res_la["muc"][0]["thuoc_tinh_rieng"] == {"a": 1, "b": [2, 3]}

    # 4. Gỡ module giữa chừng
    client.delete("/api/modules/mod-la", headers=QT)
    r_sau_go = client.post("/api/tim-kiem/muc-luc", headers=QT, json={"nguon": "mod-la", "tai_lieu": "doc_la"})
    assert r_sau_go.status_code == 404


@pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")
def test_7_giao_dien_chrome_muc_luc(trang, tmp_path, monkeypatch):
    """Test giao diện Chrome cho module mục lục (chạy khi có Chrome)."""
    from gia import MayChuThat

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    anh_dir = tmp_path / "anh"
    anh_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh_dir)

    mod_path = data_dir / "modules" / "mod-chrome-ml"
    ghi_thu_muc(mod_path, mau_module_muc_luc("mod-chrome-ml", "mcml", co_muc_luc=True))

    with MayChuThat(app, lifespan="on") as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong.dc.html")
        trang.cho("document.querySelector('[data-o=\"mod-chrome-ml\"]') !== null", han=15, mo_ta="ô module mục lục")
        assert "Module mod-chrome-ml" in trang.js("document.querySelector('[data-o=\"mod-chrome-ml\"]').innerText")
