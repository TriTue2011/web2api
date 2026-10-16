"""Kiểm thử chuẩn THIẾT BỊ / ỨNG DỤNG của lõi (thiet_bi) — Việc C1.

Chứng minh 9 điểm nghiệm thu:
1. Lõi chạy đúng khi KHÔNG có module nào: GET /api/thiet-bi trả rỗng, tab ẩn, không lỗi.
2. Thêm/gỡ module nạp nóng: thả module vào / gỡ ra không phải sửa mã lõi.
3. Không tên nhà cung cấp / dịch vụ cụ thể trong app/ và web/.
4. Mọi bảng/danh sách là mã MỞ: loại lạ hiện nguyên mã, lọc q tiếng Việt không dấu.
5. Phân quyền và hàng chờ phê duyệt: khách gọi nguy hiểm -> 202 + hàng chờ; quản trị duyệt -> thực thi; quản trị thiếu xác nhận -> 409; khách thiếu quyền -> 403.
6. Ghi đè rủi ro: PUT /api/thiet-bi/{id}/rui-ro chuyển nguy hiểm -> ghi, khách gọi chạy thẳng.
7. Mục hàng chờ quá hạn tự huỷ khi đọc (WEB2API_PHE_DUYET_PHUT).
8. Sổ kênh thiet_bi ghi nhận đầy đủ, làm sạch tham số nhạy cảm (không lộ khoá/mật khẩu).
9. Sự kiện SSE: POST /api/thiet-bi/su-kien phát nhận sự kiện, kiểm soát phạm vi thiet_bi:su_kien.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import kho, nhat_ky, so_module, thiet_bi
from app.main import app
from app.so_module import SoModule
from gia import ghi_thu_muc

KHOA_QT = "khoa-quan-tri-test-thiet-bi-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


def mau_module_thiet_bi(
    id_="thiet-bi-gia",
    tien_to="tbgia",
    loai="ung_dung",
    cung_cap=None,
) -> dict[str, bytes]:
    """Tạo tệp manifest.json và module.py cho module thiết bị mẫu."""
    if cung_cap is None:
        cung_cap = ["thiet_bi"]
    manifest = {
        "id": id_,
        "ten": "Thiết bị giả lập",
        "mo_ta": "Module thiết bị giả để kiểm thử",
        "loai": loai,
        "hang": "A",
        "giao_thuc": "cuc_bo",
        "tien_to": tien_to,
        "cung_cap": cung_cap,
        "truong_cau_hinh": [],
        "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False, "mang_ra": [], "ghi_chu": ""},
    }
    ma = f'''
import json

class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self.da_chay = []

    def nap(self, cau_hinh):
        self.cau_hinh = cau_hinh

    async def cai(self, cau_hinh):
        return {{"ok": True}}

    async def thuc_the(self, loc=None):
        return [
            {{
                "id": f"{{self.tien_to}}.den",
                "ten": "Đèn trần thông minh",
                "loai": "den",
                "khu": "phong_khach",
                "thuoc_tinh": {{"do_sang": 80}},
                "tinh_nang": ["bat", "tat", "do_sang"],
                "rui_ro_mac_dinh": "ghi",
            }},
            {{
                "id": f"{{self.tien_to}}.khoa-cua",
                "ten": "Khoá cửa chính",
                "loai": "khoa",
                "khu": "cong_vao",
                "thuoc_tinh": {{"dong": True}},
                "tinh_nang": ["mo_khoa", "khoa"],
                "rui_ro_mac_dinh": "nguy_hiem",
            }},
            {{
                "id": f"{{self.tien_to}}.thiet_bi_la",
                "ten": "Cảm biến môi trường lạ",
                "loai": "loai_la_chua_biet",
                "khu": "ngoai_troi",
                "thuoc_tinh": {{"gia_tri_tho": 42}},
                "tinh_nang": ["doc_so_lieu"],
                "rui_ro_mac_dinh": "doc",
            }},
            # Thực thể cố tình sai tiền tố để kiểm tra bỏ qua an toàn
            {{
                "id": "sai_tien_to.khong_nhan",
                "ten": "Thực thể lỗi tiền tố",
                "loai": "den",
                "tinh_nang": ["bat"],
                "rui_ro_mac_dinh": "doc",
            }},
            # Thực thể cố tình trùng id
            {{
                "id": f"{{self.tien_to}}.den",
                "ten": "Đèn trần trùng lặp",
                "loai": "den",
                "tinh_nang": ["bat"],
                "rui_ro_mac_dinh": "doc",
            }},
        ]

    async def trang_thai(self, ids):
        return {{
            f"{{self.tien_to}}.den": {{"gia_tri": "on", "hop_le": True}},
            f"{{self.tien_to}}.khoa-cua": {{"gia_tri": "locked", "hop_le": True}},
            f"{{self.tien_to}}.thiet_bi_la": {{"gia_tri": "ready", "hop_le": True}},
        }}

    async def thuc_hien(self, id, hanh_dong, tham_so):
        self.da_chay.append({{"id": id, "hanh_dong": hanh_dong, "tham_so": tham_so}})
        return {{"ok": True, "id": id, "hanh_dong": hanh_dong, "thuc_thi": "thanh_cong"}}

    async def chat(self, body, stream=False):
        return 501, {{}}, b"{{}}"

    async def models(self):
        return []

    async def suc_khoe(self):
        return {{"ok": True}}

    async def dung(self):
        pass
'''
    return {
        "manifest.json": json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
        "module.py": ma.encode("utf-8"),
    }


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    """Môi trường dữ liệu sạch cho kiểm thử thiết bị."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    anh_dir = tmp_path / "anh"
    anh_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh_dir)

    with TestClient(app) as client:
        # Ban đầu SoModule rỗng
        so = SoModule()
        client.app.state.so = so
        yield client, data_dir, so


def test_1_loi_chay_dung_khi_khong_co_module(moi_truong):
    """Điểm 1: Lõi không có module nào: GET /api/thiet-bi trả rỗng, không vỡ."""
    client, data_dir, so = moi_truong

    r = client.get("/api/thiet-bi", headers=QT)
    assert r.status_code == 200
    res = r.json()
    assert res["thuc_the"] == []
    assert res["loai"] == []
    assert res["khu"] == []
    assert res["module"] == []
    assert res["rui_ro"] == ["doc", "ghi", "nguy_hiem"]

    # /api/tinh-nang hoạt động bình thường
    r_tn = client.get("/api/tinh-nang", headers=QT)
    assert r_tn.status_code == 200


def test_2_them_go_module_nap_nong(moi_truong):
    """Điểm 2: Thêm/gỡ module nóng: thực thể tự hiện và biến mất không sửa lõi."""
    client, data_dir, so = moi_truong

    # Thả module vào thư mục dữ liệu
    mod_path = data_dir / "modules" / "thiet-bi-gia"
    ghi_thu_muc(mod_path, mau_module_thiet_bi("thiet-bi-gia", "tbgia"))

    # Nạp nóng qua API quản trị
    r_nap = client.post("/api/modules/nap-lai", headers=QT)
    assert r_nap.status_code == 200
    assert "thiet-bi-gia" in r_nap.json()["them"]

    # Liên kết module
    r_cai = client.post("/api/modules/thiet-bi-gia/cai", headers=QT, json={"cau_hinh": {}})
    assert r_cai.status_code == 200

    # GET /api/thiet-bi thấy thực thể
    r = client.get("/api/thiet-bi", headers=QT)
    assert r.status_code == 200
    ds = r.json()["thuc_the"]
    ids = [e["id"] for e in ds]
    assert "tbgia.den" in ids
    assert "tbgia.khoa-cua" in ids
    assert "tbgia.thiet_bi_la" in ids
    # Đã bỏ qua id sai tiền tố và trùng lặp
    assert "sai_tien_to.khong_nhan" not in ids
    assert ids.count("tbgia.den") == 1

    # Gỡ module
    r_go = client.delete("/api/modules/thiet-bi-gia", headers=QT)
    assert r_go.status_code == 200

    # GET /api/thiet-bi lại rỗng
    r_sau_go = client.get("/api/thiet-bi", headers=QT)
    assert r_sau_go.status_code == 200
    assert r_sau_go.json()["thuc_the"] == []


def test_3_khong_ten_nha_cung_cap_trong_ma_moi():
    """Điểm 3: app/thiet_bi.py và các tệp giao diện mới không chứa tên nhà cung cấp cụ thể."""
    goc = Path(__file__).resolve().parents[1]
    tep_kiem_tra = [
        goc / "app" / "thiet_bi.py",
        goc / "web" / "He-thong-Thiet-bi.dc.html",
        goc / "docs" / "CHUAN-MODULE.md",
    ]
    tu_cam = re.compile(r"\b(home.?assistant|mikrotik|ezviz|routeros)\b", re.IGNORECASE)
    for p in tep_kiem_tra:
        assert p.is_file(), f"Tệp {p} phải tồn tại"
        noi_dung = p.read_text(encoding="utf-8")
        khop = tu_cam.findall(noi_dung)
        assert not khop, f"Tệp {p.name} chứa tên riêng: {khop}"


def test_4_ma_mo_va_bo_loc(moi_truong):
    """Điểm 4: Mã mở (loại lạ hiện nguyên mã) và bộ lọc đa diện (q bỏ dấu, loai, khu)."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "thiet-bi-gia"
    ghi_thu_muc(mod_path, mau_module_thiet_bi("thiet-bi-gia", "tbgia"))
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/thiet-bi-gia/cai", headers=QT, json={"cau_hinh": {}})

    # Loại lạ hiện nguyên mã
    r = client.get("/api/thiet-bi", headers=QT)
    res = r.json()
    la = next((e for e in res["thuc_the"] if e["id"] == "tbgia.thiet_bi_la"), None)
    assert la is not None
    assert la["loai"] == "loai_la_chua_biet"
    assert "loai_la_chua_biet" in res["loai"]

    # Bộ lọc loại
    r_loai = client.get("/api/thiet-bi?loai=loai_la_chua_biet", headers=QT)
    ds_loai = r_loai.json()["thuc_the"]
    assert len(ds_loai) == 1
    assert ds_loai[0]["id"] == "tbgia.thiet_bi_la"

    # Bộ lọc khu
    r_khu = client.get("/api/thiet-bi?khu=cong_vao", headers=QT)
    ds_khu = r_khu.json()["thuc_the"]
    assert len(ds_khu) == 1
    assert ds_khu[0]["id"] == "tbgia.khoa-cua"

    # Tìm kiếm bỏ dấu tiếng Việt: q="den" và q="đèn" đều khớp "Đèn trần thông minh"
    r_q1 = client.get("/api/thiet-bi?q=den", headers=QT)
    assert any(e["id"] == "tbgia.den" for e in r_q1.json()["thuc_the"])

    r_q2 = client.get("/api/thiet-bi?q=đèn", headers=QT)
    assert any(e["id"] == "tbgia.den" for e in r_q2.json()["thuc_the"])

    # Chi tiết 1 thực thể
    r_ct = client.get("/api/thiet-bi/tbgia.den", headers=QT)
    assert r_ct.status_code == 200
    assert r_ct.json()["thuc_the"]["id"] == "tbgia.den"
    assert r_ct.json()["trang_thai"]["gia_tri"] == "on"

    # 404 thực thể không tồn tại
    assert client.get("/api/thiet-bi/tbgia.khong_co", headers=QT).status_code == 404


def test_5_phan_quyen_khoa_khach_va_hang_cho_phe_duyet(moi_truong):
    """Điểm 5: Khách gọi nguy hiểm -> 202 + hàng chờ; quản trị duyệt -> thực thi; thiếu quyền -> 403; thiếu xac_nhan -> 409."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "thiet-bi-gia"
    ghi_thu_muc(mod_path, mau_module_thiet_bi("thiet-bi-gia", "tbgia"))
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/thiet-bi-gia/cai", headers=QT, json={"cau_hinh": {}})

    # 1. Tạo khoá khách có quyền thiet_bi:ghi
    r_tao = client.post(
        "/api/khoa-khach",
        headers=QT,
        json={"nhan": "App Nhà Thông Minh", "pham_vi": ["thiet_bi:ghi", "thiet_bi:doc"]},
    )
    assert r_tao.status_code == 200
    khoa_ghi = r_tao.json()["khoa"]["khoa"]
    hd_ghi = {"Authorization": f"Bearer {khoa_ghi}"}

    # Tạo khoá khách chỉ có quyền thiet_bi:doc
    r_tao_doc = client.post(
        "/api/khoa-khach",
        headers=QT,
        json={"nhan": "App Chỉ Đọc", "pham_vi": ["thiet_bi:doc"]},
    )
    khoa_doc = r_tao_doc.json()["khoa"]["khoa"]
    hd_doc = {"Authorization": f"Bearer {khoa_doc}"}

    # Khách thiếu quyền gọi hành động -> 403 insufficient_scope
    r_cam = client.post("/api/thiet-bi/tbgia.den/hanh-dong", headers=hd_doc, json={"hanh_dong": "bat"})
    assert r_cam.status_code == 403
    assert r_cam.json()["error"]["code"] == "insufficient_scope"

    # Hành động không nằm trong tinh_nang -> 400
    r_ktt = client.post("/api/thiet-bi/tbgia.den/hanh-dong", headers=hd_ghi, json={"hanh_dong": "hanh_dong_la"})
    assert r_ktt.status_code == 400

    # Chưa xác nhận -> thực thể coi là nguy_hiem -> khách gọi vào hàng chờ 202
    r_chua_xn = client.post(
        "/api/thiet-bi/tbgia.den/hanh-dong",
        headers=hd_ghi,
        json={"hanh_dong": "bat", "tham_so": {"do_sang": 90}},
    )
    assert r_chua_xn.status_code == 202
    assert r_chua_xn.json()["cho_phe_duyet"] is True

    # Quản trị xác nhận thực thể tbgia.den
    r_xn = client.post("/api/thiet-bi/tbgia.den/xac-nhan", headers=QT)
    assert r_xn.status_code == 200

    # Khách có quyền gọi hành động bình thường (rui_ro="ghi") -> 200 thành công ngay
    r_chay = client.post(
        "/api/thiet-bi/tbgia.den/hanh-dong",
        headers=hd_ghi,
        json={"hanh_dong": "bat", "tham_so": {"do_sang": 90}},
    )
    assert r_chay.status_code == 200
    assert r_chay.json()["ok"] is True

    # Khách gọi hành động nguy hiểm (rui_ro="nguy_hiem") -> 202 accepted + đưa vào hàng chờ
    r_cho = client.post(
        "/api/thiet-bi/tbgia.khoa-cua/hanh-dong",
        headers=hd_ghi,
        json={"hanh_dong": "mo_khoa", "tham_so": {}, "ly_do": "Khách mở cửa"},
    )
    assert r_cho.status_code == 202
    res_cho = r_cho.json()
    assert res_cho["ok"] is True
    assert res_cho["cho_phe_duyet"] is True
    pd_id = res_cho["phe_duyet_id"]
    assert pd_id.startswith("pd_")

    # Khách không được gọi /api/phe-duyet (đường quản trị) -> 403
    assert client.get("/api/phe-duyet", headers=hd_ghi).status_code == 403

    # Quản trị xem hàng chờ
    r_ds_pd = client.get("/api/phe-duyet", headers=QT)
    assert r_ds_pd.status_code == 200
    muc_pd = next((m for m in r_ds_pd.json()["hang_cho"] if m["id"] == pd_id), None)
    assert muc_pd is not None
    assert muc_pd["thuc_the"] == "tbgia.khoa-cua"
    assert muc_pd["hanh_dong"] == "mo_khoa"

    # Quản trị duyệt -> thực thi hành động và xoá khỏi hàng chờ
    r_duyet = client.post(f"/api/phe-duyet/{pd_id}/duyet", headers=QT)
    assert r_duyet.status_code == 200
    assert r_duyet.json()["ok"] is True

    # Kiểm tra hàng chờ đã hết mục đó
    r_ds_pd2 = client.get("/api/phe-duyet", headers=QT)
    assert not any(m["id"] == pd_id for m in r_ds_pd2.json()["hang_cho"])

    # Quản trị gọi hành động nguy hiểm mà thiếu xac_nhan -> 409 can_xac_nhan
    r_qn = client.post("/api/thiet-bi/tbgia.khoa-cua/hanh-dong", headers=QT, json={"hanh_dong": "mo_khoa"})
    assert r_qn.status_code == 409
    assert r_qn.json()["error"]["code"] == "can_xac_nhan"

    # Quản trị gọi kèm xac_nhan: true -> thực hiện ngay
    r_qn_ok = client.post(
        "/api/thiet-bi/tbgia.khoa-cua/hanh-dong",
        headers=QT,
        json={"hanh_dong": "mo_khoa", "xac_nhan": True},
    )
    assert r_qn_ok.status_code == 200
    assert r_qn_ok.json()["ok"] is True


def test_6_ghi_de_rui_ro(moi_truong):
    """Điểm 6: Ghi đè rủi ro: PUT chuyển nguy_hiem -> ghi giúp khách gọi chạy thẳng."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "thiet-bi-gia"
    ghi_thu_muc(mod_path, mau_module_thiet_bi("thiet-bi-gia", "tbgia"))
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/thiet-bi-gia/cai", headers=QT, json={"cau_hinh": {}})

    r_tao = client.post(
        "/api/khoa-khach",
        headers=QT,
        json={"nhan": "App Tự Động Hoá", "pham_vi": ["thiet_bi:ghi"]},
    )
    hd_khach = {"Authorization": f"Bearer {r_tao.json()['khoa']['khoa']}"}

    # Ban đầu khoa-cua là nguy_hiem, khách gọi -> 202
    r1 = client.post("/api/thiet-bi/tbgia.khoa-cua/hanh-dong", headers=hd_khach, json={"hanh_dong": "mo_khoa"})
    assert r1.status_code == 202

    # Quản trị ghi đè rủi ro thành "ghi"
    r_gd = client.put("/api/thiet-bi/tbgia.khoa-cua/rui-ro", headers=QT, json={"rui_ro": "ghi"})
    assert r_gd.status_code == 200
    assert r_gd.json()["rui_ro"] == "ghi"

    # Kiểm tra tệp thiet_bi.json có quyền 0600
    p_tb = data_dir / "thiet_bi.json"
    assert p_tb.is_file()
    che_do = stat.S_IMODE(p_tb.stat().st_mode)
    assert che_do == 0o600

    # Khách gọi lại -> chạy thẳng 200 (không còn 202)
    r2 = client.post("/api/thiet-bi/tbgia.khoa-cua/hanh-dong", headers=hd_khach, json={"hanh_dong": "mo_khoa"})
    assert r2.status_code == 200
    assert r2.json()["ok"] is True


def test_7_tu_huy_muc_qua_han_khi_doc(moi_truong, monkeypatch):
    """Điểm 7: Mục hàng chờ quá hạn tự huỷ khi đọc."""
    client, data_dir, so = moi_truong

    p_pd = data_dir / "phe_duyet.json"
    qua_han = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    con_han = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()

    du_lieu = {
        "hang_cho": [
            {
                "id": "pd_qua_han_111",
                "ai_de_xuat": "khach",
                "thuc_the": "tbgia.khoa-cua",
                "hanh_dong": "mo_khoa",
                "tham_so": {},
                "ly_do": "Mục cũ",
                "luc": qua_han,
                "het_han": qua_han,
            },
            {
                "id": "pd_con_han_222",
                "ai_de_xuat": "khach",
                "thuc_the": "tbgia.khoa-cua",
                "hanh_dong": "khoa",
                "tham_so": {},
                "ly_do": "Mục mới",
                "luc": con_han,
                "het_han": con_han,
            },
        ]
    }
    kho.ghi_nguyen_tu(p_pd, json.dumps(du_lieu, ensure_ascii=False, indent=2), quyen=0o600)

    # Đọc danh sách phê duyệt -> mục quá hạn tự biến mất
    r = client.get("/api/phe-duyet", headers=QT)
    assert r.status_code == 200
    ds = r.json()["hang_cho"]
    ids = [m["id"] for m in ds]
    assert "pd_qua_han_111" not in ids
    assert "pd_con_han_222" in ids

    # Tệp đĩa cũng được cập nhật
    doc_lai = kho.doc_json(p_pd)
    ids_dia = [m["id"] for m in doc_lai["hang_cho"]]
    assert "pd_qua_han_111" not in ids_dia
    assert "pd_con_han_222" in ids_dia


def test_8_so_kenh_thiet_bi_va_che_bi_mat(moi_truong):
    """Điểm 8: Ghi sổ kênh thiet_bi, làm sạch tham số có vẻ nhạy cảm."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "thiet-bi-gia"
    ghi_thu_muc(mod_path, mau_module_thiet_bi("thiet-bi-gia", "tbgia"))
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/thiet-bi-gia/cai", headers=QT, json={"cau_hinh": {}})
    client.post("/api/thiet-bi/tbgia.den/xac-nhan", headers=QT)

    # Quản trị gọi hành động có tham số nhạy cảm: mat_khau
    r = client.post(
        "/api/thiet-bi/tbgia.den/hanh-dong",
        headers=QT,
        json={
            "hanh_dong": "bat",
            "tham_so": {"do_sang": 50, "mat_khau": "bi-mat-1234", "token_phu": "token-xyz"},
        },
    )
    assert r.status_code == 200

    # Xem nhật ký hệ thống kênh thiet_bi
    r_nk = client.get("/api/nhat-ky?kenh=thiet_bi", headers=QT)
    assert r_nk.status_code == 200
    dong_nk = r_nk.json()
    assert len(dong_nk) >= 1

    # Chuỗi bí mật thô không xuất hiện trong nhật ký
    toan_bo_chu = json.dumps(dong_nk, ensure_ascii=False)
    assert "bi-mat-1234" not in toan_bo_chu
    assert "token-xyz" not in toan_bo_chu
    assert "…che" in toan_bo_chu


def test_9_sse_su_kien_thiet_bi(moi_truong):
    """Điểm 9: Luồng SSE tiếp nhận và phát sự kiện thiết bị."""
    client, data_dir, so = moi_truong

    # NV29/SEC-003: sự kiện phải nhắm thực thể đang tồn tại trong danh mục lõi → nạp module mẫu trước.
    mod_path = data_dir / "modules" / "thiet-bi-gia"
    ghi_thu_muc(mod_path, mau_module_thiet_bi("thiet-bi-gia", "tbgia"))
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/thiet-bi-gia/cai", headers=QT, json={"cau_hinh": {}})

    # Tạo khoá khách có phạm vi thiet_bi:su_kien
    r_tao = client.post(
        "/api/khoa-khach",
        headers=QT,
        json={"nhan": "App Sự Kiện", "pham_vi": ["thiet_bi:su_kien"]},
    )
    hd_sk = {"Authorization": f"Bearer {r_tao.json()['khoa']['khoa']}"}

    # Tạo khoá khách thiếu phạm vi sự kiện
    r_tao_doc = client.post(
        "/api/khoa-khach",
        headers=QT,
        json={"nhan": "App Không Sự Kiện", "pham_vi": ["thiet_bi:doc"]},
    )
    hd_doc = {"Authorization": f"Bearer {r_tao_doc.json()['khoa']['khoa']}"}

    # Khách thiếu phạm vi -> 403
    r_cam = client.post("/api/thiet-bi/su-kien", headers=hd_doc, json={"thuc_the": "tbgia.den", "loai": "trang_thai"})
    assert r_cam.status_code == 403

    # Phát sự kiện qua POST /api/thiet-bi/su-kien
    r_post = client.post(
        "/api/thiet-bi/su-kien",
        headers=hd_sk,
        json={"thuc_the": "tbgia.den", "loai": "trang_thai", "gia_tri": "off"},
    )
    assert r_post.status_code == 200
    assert r_post.json()["ok"] is True
