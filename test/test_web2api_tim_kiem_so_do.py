"""Kiểm thử bốn sơ đồ tìm kiếm của lõi: CÔNG TẮC, LỌC, CÂY, PHỄU.

Chứng minh các yêu cầu:
1. Công tắc bat_tat.nguon: tắt nguồn -> kết quả chỉ còn nguồn kia; nguồn không tồn tại ghi vào bao_cao là bỏ qua, không lỗi.
2. Danh sách nguon: chọn đúng nguồn; nguồn lạ ghi vào bao_cao là bỏ qua, không lỗi.
3. Công tắc bat_tat.so_do: chuyển vào module; manifest khai so_do_tim_kiem -> GET /api/tim-kiem/nguon liệt kê; sơ đồ lạ -> so_do_bo_qua theo nguồn.
4. Lọc nhiều mặt (loc): giá trị đơn, danh sách, khoảng {tu, den} (số và ngày ISO), loại trừ {khac: ...}, trường lồng đường chấm, kiểu không khớp -> không qua lọc, không lỗi.
5. Đếm theo mặt (mat): khớp số mục, tính trên kết quả trước khi cắt so_luong.
6. Cây phân cấp (cay): tiền tố, độ sâu; cay.dem: true -> cay_nut [{duong, so_muc}] các nút con trực tiếp dưới gốc, tính trước khi cắt.
7. Phễu mặc định đủ 5 tầng trong bao_cao (truy_hoi -> gop -> loc -> cay -> cat), mỗi tầng có ten, vao, ra, ms; kết quả có nguon_ngoai: true.
8. Khoá khách thiếu phạm vi tim_kiem -> 403 insufficient_scope.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import so_module
from app.main import app
from app.so_module import SoModule
from gia import ghi_thu_muc

KHOA_QT = "khoa-quan-tri-test-so-do-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


def mau_module_tim_kiem(
    id_: str,
    tien_to: str,
    so_do_tim_kiem: list[str] | None = None,
    du_lieu: list[dict] | None = None,
) -> dict[str, bytes]:
    """Tạo module giả lập có khả năng tim_kiem kèm danh mục sơ đồ hỗ trợ."""
    manifest = {
        "id": id_,
        "ten": f"Module {id_}",
        "mo_ta": "Module tim kiem gia",
        "loai": "tri_thuc",
        "hang": "A",
        "giao_thuc": "cuc_bo",
        "tien_to": tien_to,
        "cung_cap": ["tim_kiem"],
        "so_do_tim_kiem": so_do_tim_kiem or [],
        "truong_cau_hinh": [],
        "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False, "mang_ra": [], "ghi_chu": ""},
    }

    du_lieu_json = json.dumps(du_lieu or [], ensure_ascii=False)

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
        # Module lọc sơ đồ lạ nếu có bat_tat.so_do
        so_do_ho_tro = set(self.manifest.get("so_do_tim_kiem", []))
        bat_tat = yc.get("bat_tat") or {{}}
        bat_tat_so_do = bat_tat.get("so_do") or {{}}
        so_do_bo_qua = [sd for sd in bat_tat_so_do if sd not in so_do_ho_tro]

        return {{
            "muc": self.du_lieu,
            "tong": len(self.du_lieu),
            "so_do_bo_qua": so_do_bo_qua,
        }}
'''
    return {
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        "module.py": ma.encode("utf-8"),
    }


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    """Môi trường dữ liệu sạch có 2 module tri thức giả lập."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    anh_dir = tmp_path / "anh"
    anh_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh_dir)

    # Dữ liệu nguồn 1: 3 mục
    dl1 = [
        {
            "id": "tt1.doc1",
            "diem": 0.95,
            "doan": "Tri tue nhan tao va hoc may",
            "tai_lieu": "tailieu1.txt",
            "vi_tri": 1,
            "truong": {
                "loai": "khoa_hoc",
                "nam": 2026,
                "duong_dan": "khoa_hoc/cntt/ai",
                "kenh": {"ten": "cong_nghe", "cap": 1},
                "ngay": "2026-05-01T10:00:00Z",
            },
        },
        {
            "id": "tt1.doc2",
            "diem": 0.80,
            "doan": "Tho ca va van hoc hien dai",
            "tai_lieu": "tailieu2.txt",
            "vi_tri": 2,
            "truong": {
                "loai": "van_hoc",
                "nam": 2024,
                "duong_dan": "van_hoc/tho",
                "kenh": {"ten": "nghe_thuat", "cap": 2},
                "ngay": "2024-02-15T08:00:00Z",
            },
        },
        {
            "id": "tt1.doc3",
            "diem": 0.70,
            "doan": "Mang may tinh va an ninh mang",
            "tai_lieu": "tailieu3.txt",
            "vi_tri": 3,
            "truong": {
                "loai": "khoa_hoc",
                "nam": 2025,
                "duong_dan": "khoa_hoc/cntt/an_ninh",
                "kenh": {"ten": "cong_nghe", "cap": 1},
                "ngay": "2025-11-20T14:30:00Z",
            },
        },
    ]

    # Dữ liệu nguồn 2: 2 mục
    dl2 = [
        {
            "id": "tt2.doc1",
            "diem": 0.90,
            "doan": "He dieu hanh phan tan",
            "tai_lieu": "tailieu4.txt",
            "vi_tri": 1,
            "truong": {
                "loai": "khoa_hoc",
                "nam": 2026,
                "duong_dan": "khoa_hoc/cntt/he_dieu_hanh",
                "kenh": {"ten": "cong_nghe", "cap": 1},
                "ngay": "2026-06-10T12:00:00Z",
            },
        },
        {
            "id": "tt2.doc2",
            "diem": 0.65,
            "doan": "Lich su van minh the gioi",
            "tai_lieu": "tailieu5.txt",
            "vi_tri": 2,
            "truong": {
                "loai": "lich_su",
                "nam": 2023,
                "duong_dan": "xa_hoi/lich_su",
                "kenh": {"ten": "xa_hoi", "cap": 3},
                "ngay": "2023-01-01T00:00:00Z",
            },
        },
    ]

    ghi_thu_muc(data_dir / "modules" / "tri-thuc-1", mau_module_tim_kiem("tri-thuc-1", "tt1", ["toan_van", "ngu_nghia"], dl1))
    ghi_thu_muc(data_dir / "modules" / "tri-thuc-2", mau_module_tim_kiem("tri-thuc-2", "tt2", ["toan_van"], dl2))

    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        client.post("/api/modules/nap-lai", headers=QT)
        client.post("/api/modules/tri-thuc-1/cai", headers=QT, json={"cau_hinh": {}})
        client.post("/api/modules/tri-thuc-2/cai", headers=QT, json={"cau_hinh": {}})
        yield client, data_dir, so


def test_1_cong_tac_tat_nguon_va_bao_cao(moi_truong):
    """Điểm 1: bat_tat.nguon = {id: false} loại nguồn, nguồn không tồn tại ghi vào bao_cao là bỏ qua, không lỗi."""
    client, _, _ = moi_truong

    # Tắt nguồn tri-thuc-2 -> kết quả chỉ còn các mục từ tri-thuc-1
    r = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={
            "chuoi": "test",
            "bat_tat": {"nguon": {"tri-thuc-2": False}},
        },
    )
    assert r.status_code == 200
    res = r.json()
    nguon_ds = {m["nguon"] for m in res["muc"]}
    assert "tri-thuc-1" in nguon_ds
    assert "tri-thuc-2" not in nguon_ds

    # bao_cao tầng truy_hoi ghi nhận đã bỏ qua tri-thuc-2
    bc_truy_hoi = res["bao_cao"][0]
    assert bc_truy_hoi["ten"] == "truy_hoi"
    assert "tri-thuc-2" in bc_truy_hoi.get("bo_qua", [])

    # Tắt nguồn không tồn tại -> không lỗi, ghi nhận vào bao_cao
    r2 = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={
            "chuoi": "test",
            "bat_tat": {"nguon": {"nguon_ma_khong_co": False}},
        },
    )
    assert r2.status_code == 200
    bc2 = r2.json()["bao_cao"][0]
    assert "nguon_ma_khong_co" in bc2.get("bo_qua", [])


def test_2_danh_sach_nguon_chon_nguon(moi_truong):
    """Điểm 2: nguon: [id] chọn đúng nguồn; nguồn lạ ghi vào bao_cao là bỏ qua, không lỗi."""
    client, _, _ = moi_truong

    # Chỉ chọn tri-thuc-2
    r = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={
            "chuoi": "test",
            "nguon": ["tri-thuc-2"],
        },
    )
    assert r.status_code == 200
    res = r.json()
    assert all(m["nguon"] == "tri-thuc-2" for m in res["muc"])
    assert len(res["muc"]) == 2

    # Chọn cả nguồn hợp lệ và nguồn lạ không tồn tại -> nguồn lạ vào bo_qua, không lỗi
    r2 = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={
            "chuoi": "test",
            "nguon": ["tri-thuc-1", "nguon_khong_ton_tai"],
        },
    )
    assert r2.status_code == 200
    res2 = r2.json()
    assert all(m["nguon"] == "tri-thuc-1" for m in res2["muc"])
    assert "nguon_khong_ton_tai" in res2["bao_cao"][0].get("bo_qua", [])


def test_3_so_do_tim_kiem_manifest_va_so_do_bo_qua(moi_truong):
    """Điểm 3: GET /api/tim-kiem/nguon liệt kê so_do_tim_kiem; sơ đồ lạ -> so_do_bo_qua theo nguồn."""
    client, _, _ = moi_truong

    # Kiểm tra GET /api/tim-kiem/nguon
    r_nguon = client.get("/api/tim-kiem/nguon", headers=QT)
    assert r_nguon.status_code == 200
    ds_nguon = {item["id"]: item for item in r_nguon.json()["nguon"]}
    assert ds_nguon["tri-thuc-1"]["so_do_tim_kiem"] == ["toan_van", "ngu_nghia"]
    assert ds_nguon["tri-thuc-2"]["so_do_tim_kiem"] == ["toan_van"]

    # Gửi yêu cầu với sơ đồ lạ và sơ đồ mà tri-thuc-2 không hỗ trợ
    r_tk = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={
            "chuoi": "test",
            "bat_tat": {
                "so_do": {
                    "toan_van": True,
                    "ngu_nghia": True,
                    "so_do_la_khong_ai_biet": True,
                }
            },
        },
    )
    assert r_tk.status_code == 200
    res = r_tk.json()
    assert "so_do_bo_qua" in res
    so_do_bo_qua = res["so_do_bo_qua"]

    # tri-thuc-1 bỏ qua sơ đồ lạ
    assert "so_do_la_khong_ai_biet" in so_do_bo_qua.get("tri-thuc-1", [])
    assert "toan_van" not in so_do_bo_qua.get("tri-thuc-1", [])
    assert "ngu_nghia" not in so_do_bo_qua.get("tri-thuc-1", [])

    # tri-thuc-2 chỉ hỗ trợ toan_van, nên bỏ qua cả ngu_nghia và so_do_la_khong_ai_biet
    assert "so_do_la_khong_ai_biet" in so_do_bo_qua.get("tri-thuc-2", [])
    assert "ngu_nghia" in so_do_bo_qua.get("tri-thuc-2", [])
    assert "toan_van" not in so_do_bo_qua.get("tri-thuc-2", [])


def test_4_loc_nhieu_mat_day_du(moi_truong):
    """Điểm 4: Toán tử loc hỗ trợ giá trị đơn, danh sách, khoảng số/ngày ISO, loại trừ, trường lồng, kiểu sai."""
    client, _, _ = moi_truong

    # 1. Giá trị đơn (bằng)
    r1 = client.post("/api/tim-kiem", headers=QT, json={"loc": {"loai": "van_hoc"}})
    assert r1.status_code == 200
    assert len(r1.json()["muc"]) == 1
    assert r1.json()["muc"][0]["id"] == "tt1.doc2"

    # 2. Danh sách (thuộc một trong)
    r2 = client.post("/api/tim-kiem", headers=QT, json={"loc": {"loai": ["van_hoc", "lich_su"]}})
    assert r2.status_code == 200
    ids2 = {m["id"] for m in r2.json()["muc"]}
    assert ids2 == {"tt1.doc2", "tt2.doc2"}

    # 3. Khoảng số {tu, den}
    r3 = client.post("/api/tim-kiem", headers=QT, json={"loc": {"nam": {"tu": 2025, "den": 2026}}})
    assert r3.status_code == 200
    ids3 = {m["id"] for m in r3.json()["muc"]}
    assert ids3 == {"tt1.doc1", "tt1.doc3", "tt2.doc1"}

    # 4. Khoảng chuỗi ngày ISO {tu, den}
    r4 = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={"loc": {"ngay": {"tu": "2025-01-01T00:00:00Z", "den": "2026-05-15T00:00:00Z"}}},
    )
    assert r4.status_code == 200
    ids4 = {m["id"] for m in r4.json()["muc"]}
    assert ids4 == {"tt1.doc1", "tt1.doc3"}

    # 5. Loại trừ {khac: ...}
    r5 = client.post("/api/tim-kiem", headers=QT, json={"loc": {"loai": {"khac": "khoa_hoc"}}})
    assert r5.status_code == 200
    ids5 = {m["id"] for m in r5.json()["muc"]}
    assert ids5 == {"tt1.doc2", "tt2.doc2"}

    # 6. Loại trừ danh sách {khac: [...]}
    r6 = client.post("/api/tim-kiem", headers=QT, json={"loc": {"loai": {"khac": ["khoa_hoc", "van_hoc"]}}})
    assert r6.status_code == 200
    ids6 = {m["id"] for m in r6.json()["muc"]}
    assert ids6 == {"tt2.doc2"}

    # 7. Trường lồng bằng đường chấm ('kenh.ten' và 'truong.kenh.cap')
    r7 = client.post("/api/tim-kiem", headers=QT, json={"loc": {"kenh.ten": "cong_nghe"}})
    assert r7.status_code == 200
    assert len(r7.json()["muc"]) == 3

    r7b = client.post("/api/tim-kiem", headers=QT, json={"loc": {"truong.kenh.cap": 2}})
    assert r7b.status_code == 200
    assert len(r7b.json()["muc"]) == 1
    assert r7b.json()["muc"][0]["id"] == "tt1.doc2"

    # 8. Kiểu sai / không khớp -> mục không qua lọc, không lỗi
    r8 = client.post("/api/tim-kiem", headers=QT, json={"loc": {"nam": {"tu": "khong_phai_so"}}})
    assert r8.status_code == 200
    assert len(r8.json()["muc"]) == 0


def test_5_dem_theo_mat_facet(moi_truong):
    """Điểm 5: Đếm theo mặt (mat) tính trên kết quả trước khi cắt so_luong."""
    client, _, _ = moi_truong

    # Cắt so_luong: 1 nhưng yêu cầu mat đếm loai và kenh.ten
    r = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={
            "chuoi": "test",
            "so_luong": 1,
            "mat": ["loai", "kenh.ten"],
        },
    )
    assert r.status_code == 200
    res = r.json()

    # Danh sách mục chỉ còn 1 do so_luong=1
    assert len(res["muc"]) == 1

    # Nhưng số đếm theo mặt phải tính trên toàn bộ 5 mục trước khi cắt
    assert "mat" in res
    mat = res["mat"]
    assert mat["loai"] == {"khoa_hoc": 3, "van_hoc": 1, "lich_su": 1}
    assert mat["kenh.ten"] == {"cong_nghe": 3, "nghe_thuat": 1, "xa_hoi": 1}


def test_6_cay_phan_cap_va_cay_nut(moi_truong):
    """Điểm 6: Cây phân cấp lọc tiền tố, độ sâu; cay.dem: true sinh cay_nut tính trước khi cắt."""
    client, _, _ = moi_truong

    # 1. Lọc theo tiền tố gốc "khoa_hoc/cntt"
    r1 = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={"cay": {"truong": "duong_dan", "goc": "khoa_hoc/cntt"}},
    )
    assert r1.status_code == 200
    ids1 = {m["id"] for m in r1.json()["muc"]}
    assert ids1 == {"tt1.doc1", "tt1.doc3", "tt2.doc1"}

    # 2. Lọc theo độ sâu từ gốc "" (do_sau=2) -> loại các mục có độ sâu 3
    r2 = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={"cay": {"truong": "duong_dan", "goc": "", "do_sau": 2}},
    )
    assert r2.status_code == 200
    ids2 = {m["id"] for m in r2.json()["muc"]}
    # van_hoc/tho (độ sâu 2), xa_hoi/lich_su (độ sâu 2)
    assert ids2 == {"tt1.doc2", "tt2.doc2"}

    # 3. cay.dem: true -> sinh cay_nut các nút con trực tiếp dưới gốc
    r3 = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={
            "so_luong": 1,  # Cắt còn 1 nhưng cay_nut tính trước khi cắt
            "cay": {"truong": "duong_dan", "goc": "khoa_hoc", "dem": True},
        },
    )
    assert r3.status_code == 200
    res3 = r3.json()
    assert len(res3["muc"]) == 1
    assert "cay_nut" in res3
    # Nút con trực tiếp dưới khoa_hoc là khoa_hoc/cntt với 3 mục
    assert res3["cay_nut"] == [{"duong": "khoa_hoc/cntt", "so_muc": 3}]

    # Gốc rỗng đếm các nút cấp 1
    r4 = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={"cay": {"truong": "duong_dan", "goc": "", "dem": True}},
    )
    assert r4.status_code == 200
    res4 = r4.json()
    assert res4["cay_nut"] == [
        {"duong": "khoa_hoc", "so_muc": 3},
        {"duong": "van_hoc", "so_muc": 1},
        {"duong": "xa_hoi", "so_muc": 1},
    ]


def test_7_pheu_mac_dinh_du_5_tang_va_nguon_ngoai(moi_truong):
    """Điểm 7: Phễu mặc định đủ 5 tầng trong bao_cao (truy_hoi -> gop -> loc -> cay -> cat); mục có nguon_ngoai: true."""
    client, _, _ = moi_truong

    r = client.post("/api/tim-kiem", headers=QT, json={"chuoi": "test"})
    assert r.status_code == 200
    res = r.json()

    # Kiểm tra bao_cao đủ 5 tầng
    bc = res["bao_cao"]
    assert len(bc) == 5
    ten_cac_tang = [b["ten"] for b in bc]
    assert ten_cac_tang == ["truy_hoi", "gop", "loc", "cay", "cat"]
    assert all("vao" in b and "ra" in b and "ms" in b for b in bc)

    # Mục kết quả có nguon_ngoai: true
    for m in res["muc"]:
        assert m.get("nguon_ngoai") is True
        assert m.get("nguon") in ("tri-thuc-1", "tri-thuc-2")


def test_8_khoa_khach_thieu_pham_vi_tim_kiem_403(moi_truong):
    """Điểm 8: Khách thiếu phạm vi tim_kiem bị 403 insufficient_scope; có phạm vi tim_kiem gọi thành công."""
    client, _, _ = moi_truong

    # Tạo khoá chỉ có quyền chat
    r_k = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khach Chat", "pham_vi": ["chat"]})
    k_chat = r_k.json()["khoa"]["khoa"]
    h_chat = {"Authorization": f"Bearer {k_chat}"}

    # Gọi /api/tim-kiem -> 403 insufficient_scope
    r_cam = client.post("/api/tim-kiem", headers=h_chat, json={"chuoi": "test"})
    assert r_cam.status_code == 403
    assert r_cam.json()["error"]["code"] == "insufficient_scope"

    # Tạo khoá có quyền tim_kiem
    r_k_ok = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khach Tim Kiem", "pham_vi": ["tim_kiem"]})
    k_tk = r_k_ok.json()["khoa"]["khoa"]
    h_tk = {"Authorization": f"Bearer {k_tk}"}

    # Gọi thành công 200
    r_ok = client.post("/api/tim-kiem", headers=h_tk, json={"chuoi": "test"})
    assert r_ok.status_code == 200
    assert len(r_ok.json()["muc"]) > 0
