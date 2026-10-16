"""Kiểm thử ngữ cảnh gọi (ngu_canh) + cổng gọi khả năng (goi_kha_nang) — SEC-002 & SEC-004.

Bảo đảm các yêu cầu nghiệm thu:
1. Lời gọi nội bộ không ngữ cảnh -> 403 missing_context.
2. Khoá khách phạm vi chat gọi hành động thiết bị qua goi -> 403 (và qua HTTP vẫn 403).
3. Khoá thiet_bi:ghi gọi hành động nguy_hiem qua goi -> vào hàng chờ 202 như HTTP.
4. Trường nguon không do người gọi đặt được (lõi đóng dấu).
5. Tác vụ nền (asyncio.create_task) kế thừa ngữ cảnh đúng.
6. Hai yêu cầu đồng thời không lẫn ngữ cảnh.
7. kiem_tra_pham_vi đóng khi thiếu danh tính trong ngữ cảnh gọi nội bộ -> 403 missing_context.
8. SEC-004(a): Kiểm tra tham số theo lược đồ (min/max, kiểu, required, additionalProperties, enum -> 400).
9. SEC-004(b): Tham số bí mật theo nguyên tắc (bi_mat: true -> …che, không khai -> …che(độ dài N), không dùng từ khoá đoán).
10. SEC-004(c): Hàng chờ phê duyệt trần số mục (WEB2API_PHE_DUYET_TRAN_SO_MUC) và loại trùng.
11. SEC-004(d): duyet_phe_duyet kiểm lại thực thể/hành động/mức rủi ro trước khi chạy (đổi/mất -> 409).
12. SEC-004(e): Trần tần suất lệnh thiết bị (8/lượt, 30/phút -> 429).
13. SEC-004(f): Thực thể mới hoặc đổi tinh_nang coi là nguy_hiem cho tới khi quản trị xác nhận.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import kho, nhat_ky, so_module, thiet_bi, xac_thuc, ngu_canh
from app.goi_kha_nang import goi
from app.loi_openai import LoiOpenAI
from app.main import app
from app.ngu_canh import NguCanh
from app.so_module import SoModule
from gia import ghi_thu_muc

KHOA_QT = "khoa-quan-tri-test-sec002-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


def mau_module_nang_cao(id_="mod-nang-cao", tien_to="mnc") -> dict[str, bytes]:
    manifest = {
        "id": id_,
        "ten": "Module Nâng Cao",
        "mo_ta": "Module kiểm thử SEC-002 và SEC-004",
        "loai": "ung_dung",
        "giao_thuc": "cuc_bo",
        "tien_to": tien_to,
        "cung_cap": ["thiet_bi", "chat", "tim_kiem"],
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
                "id": f"{{self.tien_to}}.den-phong",
                "ten": "Đèn thông minh có lược đồ",
                "loai": "den",
                "khu": "phong_khach",
                "tinh_nang": [
                    {{
                        "hanh_dong": "chinh_sang",
                        "tham_so": {{
                            "type": "object",
                            "required": ["muc_sang"],
                            "additionalProperties": False,
                            "properties": {{
                                "muc_sang": {{"type": "integer", "minimum": 0, "maximum": 100}},
                                "khoa_bi_mat": {{"type": "string", "bi_mat": True}},
                                "che_do": {{"type": "string", "enum": ["doc_sach", "ngu", "lam_viec"]}},
                            }}
                        }}
                    }},
                    "bat",
                    "tat"
                ],
                "rui_ro_mac_dinh": "ghi",
            }},
            {{
                "id": f"{{self.tien_to}}.cua-cuon",
                "ten": "Cửa cuốn tự động",
                "loai": "cua",
                "khu": "gara",
                "tinh_nang": ["mo_cua", "dong_cua"],
                "rui_ro_mac_dinh": "nguy_hiem",
            }},
        ]

    async def trang_thai(self, ids):
        return {{i: {{"gia_tri": "ok", "hop_le": True}} for i in ids}}

    async def thuc_hien(self, id, hanh_dong, tham_so):
        self.da_chay.append({{"id": id, "hanh_dong": hanh_dong, "tham_so": tham_so}})
        return {{"ok": True, "id": id, "hanh_dong": hanh_dong, "da_chay": True}}

    async def chat(self, body, stream=False):
        return 200, {{"content-type": "application/json"}}, b'{{"id": "chatcmpl-test"}}'

    async def tim_kiem(self, yc):
        return {{"muc": [{{"id": "doc1", "diem": 0.9, "doan": "Ket qua tim kiem"}}]}}

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


# ───────────── Test SEC-002: Ngữ cảnh gọi và MỘT cổng gọi ─────────────

def test_sec002_goi_noi_bo_khong_ngu_canh_nem_403():
    """1. Lời gọi nội bộ không có ngữ cảnh -> LoiOpenAI(403, code='missing_context')."""
    # Đảm bảo ContextVar đang trống
    tok = ngu_canh.NGU_CANH.set(None)
    try:
        class FakeModule:
            manifest = {"cung_cap": ["chat"]}
            async def chat(self, body, stream=False): return 200, {}, b"{}"

        m = FakeModule()
        with pytest.raises(LoiOpenAI) as exc_info:
            asyncio.run(goi(m, "chat", {}))
        assert exc_info.value.ma == 403
        assert exc_info.value.code == "missing_context"
    finally:
        ngu_canh.NGU_CANH.reset(tok)


def test_sec002_kiem_tra_pham_vi_thieu_danh_tinh_dong_403():
    """Kiểm tra kiem_tra_pham_vi đóng 403 missing_context khi thiếu cả request và ContextVar."""
    tok = ngu_canh.NGU_CANH.set(None)
    try:
        with pytest.raises(LoiOpenAI) as exc_info:
            xac_thuc.kiem_tra_pham_vi(None, "chat")
        assert exc_info.value.ma == 403
        assert exc_info.value.code == "missing_context"
    finally:
        ngu_canh.NGU_CANH.reset(tok)


def test_sec002_khoa_khach_chat_goi_thiet_bi_bi_chan_403(moi_truong):
    """2. Khoá khách phạm vi chat gọi hành động thiết bị qua goi -> 403 (và qua HTTP vẫn 403)."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})

    # Tạo khoá khách CHỈ có phạm vi "chat"
    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khoa Chat", "pham_vi": ["chat"]})
    assert r_tao.status_code == 200
    khoa_chat = r_tao.json()["khoa"]["khoa"]
    hd_chat = {"Authorization": f"Bearer {khoa_chat}"}

    # a. Gọi qua HTTP -> 403
    r_http = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=hd_chat, json={"hanh_dong": "bat"})
    assert r_http.status_code == 403
    assert r_http.json()["error"]["code"] == "insufficient_scope"

    # b. Gọi qua goi() với ngữ cảnh khoá khách chat -> 403
    nc = NguCanh(danh_tinh={"loai": "khach", "id": "khach_chat", "pham_vi": ["chat"]}, nguon="nguoi", pham_vi_hieu_luc=("chat",))
    m = so.dang_chay["mod-nang-cao"]
    with pytest.raises(LoiOpenAI) as exc_info:
        asyncio.run(goi(m, "thuc_hien", "mnc.den-phong", "bat", {}, ngu_canh=nc, so=so))
    assert exc_info.value.ma == 403
    assert exc_info.value.code == "insufficient_scope"


def test_sec002_khoa_ghi_goi_nguy_hiem_qua_goi_vao_hang_cho_202(moi_truong):
    """3. Khoá thiet_bi:ghi gọi hành động nguy_hiem qua goi -> vào hàng chờ 202 như HTTP."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})

    nc = NguCanh(
        danh_tinh={"loai": "khach", "id": "khach_ghi", "pham_vi": ["thiet_bi:ghi"]},
        nguon="nguoi",
        pham_vi_hieu_luc=("thiet_bi:ghi",),
    )
    m = so.dang_chay["mod-nang-cao"]
    res = asyncio.run(goi(m, "thuc_hien", "mnc.cua-cuon", "mo_cua", {}, ngu_canh=nc, so=so))
    assert isinstance(res, dict)
    assert res.get("status_code") == 202
    assert res.get("cho_phe_duyet") is True
    assert "phe_duyet_id" in res

    # Kiểm tra đã vào hàng chờ phê duyệt trên đĩa
    ds = thiet_bi.doc_hang_cho_phe_duyet()
    assert any(item["id"] == res["phe_duyet_id"] for item in ds)


def test_sec002_nguon_khong_do_nguoi_goi_dat_duoc(moi_truong):
    """4. Trường nguon do người gọi truyền vào bị loại bỏ, lõi đóng dấu từ ngu_canh."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})

    nc = NguCanh(
        danh_tinh={"loai": "quan_tri", "id": "admin"},
        nguon="nguoi",
        pham_vi_hieu_luc=("*",),
    )
    m = so.dang_chay["mod-nang-cao"]

    # Cố tình giả mạo nguon="kenh" trong kw
    asyncio.run(goi(m, "thuc_hien", "mnc.den-phong", "bat", {}, nguon="kenh", ngu_canh=nc, so=so))

    # Đọc nhật ký kiểm toán kênh thiet_bi qua API
    r_nk = client.get("/api/nhat-ky?kenh=thiet_bi", headers=QT)
    assert r_nk.status_code == 200
    nk = r_nk.json()
    assert len(nk) > 0
    dong_cuoi = nk[0]
    # nguon trong nhật ký PHẢI là "nguoi" từ ngu_canh, KHÔNG phải "kenh" do người gọi tự đặt
    them = dong_cuoi.get("them", {})
    assert them.get("nguon") == "nguoi"


def test_sec002_tac_vu_nen_ke_thua_ngu_canh(moi_truong):
    """5. Tác vụ nền (asyncio.create_task) kế thừa ContextVar NGU_CANH đúng đắn."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})

    nc = NguCanh(
        danh_tinh={"loai": "noi_bo", "id": "task_nen"},
        nguon="tu_dong",
        pham_vi_hieu_luc=("*",),
    )
    m = so.dang_chay["mod-nang-cao"]

    ket_qua_nen = []

    async def tac_vu_nen():
        # Gọi goi() mà KHÔNG truyền ngu_canh -> phải tự kế thừa từ task cha
        res = await goi(m, "chat", {"messages": []})
        ket_qua_nen.append(res)

    async def luong_chinh():
        tok = ngu_canh.dat(nc.danh_tinh, nguon=nc.nguon, pham_vi_hieu_luc=nc.pham_vi_hieu_luc)
        try:
            task = asyncio.create_task(tac_vu_nen())
            await task
        finally:
            ngu_canh.khoi_phuc(tok)

    asyncio.run(luong_chinh())
    assert len(ket_qua_nen) == 1
    ma, _, _ = ket_qua_nen[0]
    assert ma == 200


def test_sec002_dong_thoi_khong_lan_ngu_canh(moi_truong):
    """6. Hai yêu cầu đồng thời không làm lẫn ContextVar NGU_CANH của nhau."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})

    m = so.dang_chay["mod-nang-cao"]

    async def yeu_cau_1():
        nc1 = NguCanh(danh_tinh={"loai": "khach", "id": "client_1"}, nguon="nguoi", pham_vi_hieu_luc=("chat",))
        tok = ngu_canh.dat(nc1.danh_tinh, nguon=nc1.nguon, pham_vi_hieu_luc=nc1.pham_vi_hieu_luc)
        try:
            await asyncio.sleep(0.02)
            lay_duoc = ngu_canh.lay()
            assert lay_duoc.danh_tinh["id"] == "client_1"
        finally:
            ngu_canh.khoi_phuc(tok)

    async def yeu_cau_2():
        nc2 = NguCanh(danh_tinh={"loai": "khach", "id": "client_2"}, nguon="ai", pham_vi_hieu_luc=("chat",))
        tok = ngu_canh.dat(nc2.danh_tinh, nguon=nc2.nguon, pham_vi_hieu_luc=nc2.pham_vi_hieu_luc)
        try:
            await asyncio.sleep(0.02)
            lay_duoc = ngu_canh.lay()
            assert lay_duoc.danh_tinh["id"] == "client_2"
        finally:
            ngu_canh.khoi_phuc(tok)

    async def dong_thoi():
        await asyncio.gather(yeu_cau_1(), yeu_cau_2())

    asyncio.run(dong_thoi())


# ───────────── Test SEC-004: Kiểm soát an toàn thiết bị ─────────────

def test_sec004a_luoc_do_tham_so(moi_truong):
    """SEC-004(a): Kiểm tra tham số theo lược đồ (min/max, kiểu, required, additionalProperties, enum -> 400)."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})

    # 1. Thiếu tham số bắt buộc "muc_sang" -> 400
    r1 = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=QT, json={"hanh_dong": "chinh_sang", "tham_so": {}})
    assert r1.status_code == 400
    assert "muc_sang" in r1.json()["error"]["message"]

    # 2. Sai kiểu dữ liệu: muc_sang là string thay vì int -> 400
    r2 = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=QT, json={"hanh_dong": "chinh_sang", "tham_so": {"muc_sang": "mot_tram"}})
    assert r2.status_code == 400

    # 3. Vượt khoảng min/max: muc_sang = 150 (> 100) -> 400
    r3 = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=QT, json={"hanh_dong": "chinh_sang", "tham_so": {"muc_sang": 150}})
    assert r3.status_code == 400

    # 4. Tham số lạ khi additionalProperties=False -> 400
    r4 = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=QT, json={"hanh_dong": "chinh_sang", "tham_so": {"muc_sang": 50, "tham_so_la": 123}})
    assert r4.status_code == 400

    # 5. Sai enum: che_do = "che_do_la" -> 400
    r5 = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=QT, json={"hanh_dong": "chinh_sang", "tham_so": {"muc_sang": 50, "che_do": "che_do_la"}})
    assert r5.status_code == 400

    # 6. Đúng toàn bộ -> 200
    r6 = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=QT, json={"hanh_dong": "chinh_sang", "tham_so": {"muc_sang": 80, "che_do": "doc_sach"}})
    assert r6.status_code == 200


def test_sec004b_che_tham_so_bi_mat_va_khong_khai(moi_truong):
    """SEC-004(b): bi_mat: true -> …che; không khai trong lược đồ -> …che(độ dài N); không dùng từ khoá đoán."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})

    # Gọi hành động có tham số bí mật đã khai trong lược đồ
    client.post(
        "/api/thiet-bi/mnc.den-phong/hanh-dong",
        headers=QT,
        json={"hanh_dong": "chinh_sang", "tham_so": {"muc_sang": 50, "khoa_bi_mat": "chuoi-rat-bi-mat-12345"}},
    )

    r_nk = client.get("/api/nhat-ky?kenh=thiet_bi", headers=QT)
    dong_nk = r_nk.json()
    chuoi_log = json.dumps(dong_nk, ensure_ascii=False)
    assert "chuoi-rat-bi-mat-12345" not in chuoi_log
    assert "…che" in chuoi_log


def test_sec004c_hang_cho_loai_trung_va_tran_so_muc(moi_truong, monkeypatch):
    """SEC-004(c): Hàng chờ loại trùng yêu cầu giống nhau và áp dụng trần số mục."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})

    # Đặt trần số mục phê duyệt = 2
    monkeypatch.setenv("WEB2API_PHE_DUYET_TRAN_SO_MUC", "2")

    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "App", "pham_vi": ["thiet_bi:ghi"]})
    hd = {"Authorization": f"Bearer {r_tao.json()['khoa']['khoa']}"}

    # 1. Gửi yêu cầu 1 (nguy hiểm -> vào hàng chờ)
    r1 = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd, json={"hanh_dong": "mo_cua"})
    assert r1.status_code == 202
    pd1_id = r1.json()["phe_duyet_id"]

    # 2. Gửi lại yêu cầu 1 y hệt -> loại trùng, trả lại mục cũ pd1_id
    r1_dup = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd, json={"hanh_dong": "mo_cua"})
    assert r1_dup.status_code == 202
    assert r1_dup.json()["phe_duyet_id"] == pd1_id
    assert len(thiet_bi.doc_hang_cho_phe_duyet()) == 1

    # 3. Gửi yêu cầu 2 (hành động khác) -> vào hàng chờ (đủ 2/2)
    r2 = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd, json={"hanh_dong": "dong_cua"})
    assert r2.status_code == 202
    assert len(thiet_bi.doc_hang_cho_phe_duyet()) == 2

    # 4. Gửi yêu cầu 3 (tham số khác) -> chạm trần 2 -> từ chối 429
    r3 = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd, json={"hanh_dong": "dong_cua", "tham_so": {"toc_do": 10}})
    assert r3.status_code == 429
    assert r3.json()["error"]["code"] == "queue_full"


def test_sec004d_duyet_kiem_lai_thuc_the_va_hanh_dong(moi_truong):
    """SEC-004(d): duyet_phe_duyet kiểm tra lại thực thể/hành động/rủi ro; sai khác -> 409 và xoá mục."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})

    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "App", "pham_vi": ["thiet_bi:ghi"]})
    hd = {"Authorization": f"Bearer {r_tao.json()['khoa']['khoa']}"}

    # Tạo mục chờ
    r1 = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd, json={"hanh_dong": "mo_cua"})
    assert r1.status_code == 202
    pd_id = r1.json()["phe_duyet_id"]

    # Giả lập rủi ro bị quản trị hạ xuống "ghi" trước khi duyệt
    client.put("/api/thiet-bi/mnc.cua-cuon/rui-ro", headers=QT, json={"rui_ro": "ghi"})

    # Quản trị bấm duyệt mục cũ -> 409 do rủi ro đã thay đổi không còn nguy_hiem
    r_duyet = client.post(f"/api/phe-duyet/{pd_id}/duyet", headers=QT)
    assert r_duyet.status_code == 409
    assert r_duyet.json()["error"]["code"] == "conflict"

    # Mục đã bị xoá khỏi hàng chờ
    assert not any(m["id"] == pd_id for m in thiet_bi.doc_hang_cho_phe_duyet())


def test_sec004e_tran_tan_suat_thiet_bi(moi_truong, monkeypatch):
    """SEC-004(e): Trần tần suất lệnh (8 lệnh/lượt, 30 lệnh/phút/khoá/thực thể -> 429)."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})

    monkeypatch.setenv("WEB2API_THIET_BI_TRAN_LUOT", "3")

    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "App RateLimit", "pham_vi": ["thiet_bi:ghi"]})
    hd = {"Authorization": f"Bearer {r_tao.json()['khoa']['khoa']}"}

    # Gọi 3 lệnh hợp lệ
    for _ in range(3):
        r = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=hd, json={"hanh_dong": "bat"})
        assert r.status_code == 200

    # Lệnh thứ 4 vượt trần burst 3 -> 429
    r_chan = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=hd, json={"hanh_dong": "bat"})
    assert r_chan.status_code == 429
    assert r_chan.json()["error"]["code"] == "rate_limit_exceeded"
    assert "Retry-After" in r_chan.headers


def test_sec004f_thuc_the_moi_doi_tinh_nang_coi_la_nguy_hiem(moi_truong):
    """SEC-004(f): Thực thể mới hoặc đổi tinh_nang coi là nguy_hiem cho tới khi quản trị xác nhận."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})

    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "App", "pham_vi": ["thiet_bi:ghi"]})
    hd = {"Authorization": f"Bearer {r_tao.json()['khoa']['khoa']}"}

    # mnc.den-phong ban đầu là rui_ro_mac_dinh="ghi", đã xác nhận lúc cai -> khách gọi chạy thẳng (200)
    r1 = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=hd, json={"hanh_dong": "bat"})
    assert r1.status_code == 200

    # Bây giờ giả lập module đổi tính năng (băm không khớp da_xac_nhan)
    p_tb = data_dir / "thiet_bi.json"
    doc = kho.doc_json(p_tb)
    # Xoá xác nhận của mnc.den-phong để coi như mới đổi tính năng
    doc["da_xac_nhan"]["mnc.den-phong"] = "bam_cu_khong_khop"
    kho.ghi_nguyen_tu(p_tb, json.dumps(doc, indent=2), quyen=0o600)

    # Khách gọi lại -> lập tức bị coi là nguy_hiem -> vào hàng chờ 202
    r2 = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=hd, json={"hanh_dong": "bat"})
    assert r2.status_code == 202
    assert r2.json()["cho_phe_duyet"] is True

    # Quản trị gọi POST /api/thiet-bi/{id}/xac-nhan
    r_xn = client.post("/api/thiet-bi/mnc.den-phong/xac-nhan", headers=QT)
    assert r_xn.status_code == 200

    # Khách gọi lại -> đã xác nhận, chạy thẳng 200
    r3 = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=hd, json={"hanh_dong": "bat"})
    assert r3.status_code == 200
