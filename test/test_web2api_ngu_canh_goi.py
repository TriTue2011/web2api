"""Kiểm thử ngữ cảnh gọi (ngu_canh) + cổng gọi khả năng (goi_kha_nang) — SEC-002 & SEC-004.

Bảo đảm các yêu cầu nghiệm thu:
1. Lời gọi nội bộ không ngữ cảnh -> 403 missing_context.
2. Khoá khách phạm vi chat gọi hành động thiết bị qua goi -> 403 (và qua HTTP vẫn 403).
3. Khoá thiet_bi:ghi gọi hành động nguy_hiem qua goi -> vào hàng chờ 202 như HTTP (không chứa status_code trong thân).
4. Trường nguon không do người gọi đặt được (lõi đóng dấu). Thực thể chưa xác nhận bị từ chối 409 cho tới khi quản trị xác nhận.
5. Tác vụ nền (asyncio.create_task) kế thừa ngữ cảnh đúng; khách bị từ chối quyền trong tác vụ nền.
6. Hai yêu cầu HTTP đồng thời không lẫn quyền (khách không nhận quyền admin).
7. Kiểm tra leo thang quyền: khách gọi đường quản trị -> 403, không khoá -> 401.
8. SEC-004(a): Kiểm tra tham số theo lược đồ (min/max, kiểu, required, additionalProperties, enum -> 400). Chặn NaN/Infinity, hỗ trợ exclusiveMinimum/Maximum, type danh sách, mảng/đối tượng lồng nhau, trần 8 KB.
9. SEC-004(b): Tham số bí mật che theo nguyên tắc. GET /api/phe-duyet và SSE che tham số bí mật. Sự kiện phe_duyet_moi phát đúng 1 lần.
10. SEC-004(c): Hàng chờ phê duyệt trần số mục toàn cục và trần THEO KHOÁ (WEB2API_PHE_DUYET_TOI_DA_MOI_KHOA). Loại trùng theo (khoá, thực thể, hành động), không trả pd_id của khoá A cho khoá B.
11. SEC-004(d): duyet_phe_duyet kiểm lại thực thể/hành động/mức rủi ro/băm tính năng/tham số trước khi chạy (đổi -> 409).
12. Duyệt đồng thời (race condition / TOCTOU) chỉ chạy hành động đúng MỘT lần, lần kia 404.
13. SEC-004(e): Trần tần suất lệnh thiết bị (8/lượt, 30/phút -> 429).
14. SEC-004(f): Thực thể mới hoặc đổi tinh_nang coi là nguy_hiem cho tới khi quản trị xác nhận.
15. Sổ kiểm toán kênh riêng goi_kha_nang: chỉ ghi từ chối hoặc hành động ghi/nguy hiểm, không ngập khi chat thường.
16. Hồi quy quy_trinh và goi: xep_hang chặn len(docs)=0, kiểm tra top_n bằng so_nguyen_chat, quy_trinh đóng khi thiếu ngữ cảnh (403), goi đòi cả phương thức lẫn cung_cap.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
import secrets
import stat
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import kho, nhat_ky, so_module, thiet_bi, xac_thuc, ngu_canh, quy_trinh
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
        "hang": "A",
        "giao_thuc": "cuc_bo",
        "tien_to": tien_to,
        "cung_cap": ["thiet_bi", "chat", "tim_kiem", "xep_hang"],
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
                    {{
                        "hanh_dong": "nang_cao",
                        "tham_so": {{
                            "type": "object",
                            "properties": {{
                                "nhiet_do": {{"type": "number", "minimum": 16.0, "maximum": 32.0, "exclusiveMinimum": 16.0, "exclusiveMaximum": 32.0}},
                                "ghi_chu": {{"type": ["string", "null"]}},
                                "danh_sach_kenh": {{
                                    "type": "array",
                                    "minItems": 1,
                                    "maxItems": 3,
                                    "items": {{"type": "string", "maxLength": 10}}
                                }},
                                "cau_hinh_con": {{
                                    "type": "object",
                                    "required": ["kich_hoat"],
                                    "properties": {{
                                        "kich_hoat": {{"type": "boolean"}}
                                    }}
                                }}
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
                "tinh_nang": [
                    {{
                        "hanh_dong": "mo_cua",
                        "tham_so": {{
                            "type": "object",
                            "properties": {{
                                "ma_pin": {{"type": "string", "bi_mat": True}},
                                "ma_the_tu": {{"type": "array", "items": {{"type": "string", "bi_mat": True}}}},
                                "cau_hinh_con": {{
                                    "type": "object",
                                    "properties": {{
                                        "khoa_bi_mat": {{"type": "string", "bi_mat": True}},
                                        "ghi_chu": {{"type": "string"}}
                                    }}
                                }},
                                "toc_do": {{"type": "integer", "maximum": 100}}
                            }}
                        }}
                    }},
                    "dong_cua"
                ],
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

    async def xep_hang(self, body):
        docs = body.get("documents", [])
        top_n = body.get("top_n", len(docs))
        results = [{{"index": i, "relevance_score": 0.9 - i * 0.1}} for i in range(min(top_n, len(docs)))]
        return 200, {{"content-type": "application/json"}}, json.dumps({{"results": results}}).encode("utf-8")

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
    """3. Khoá thiet_bi:ghi gọi hành động nguy_hiem qua goi -> vào hàng chờ 202 như HTTP (không chứa status_code)."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})
    client.post("/api/thiet-bi/mnc.cua-cuon/xac-nhan", headers=QT)

    nc = NguCanh(
        danh_tinh={"loai": "khach", "id": "khach_ghi", "pham_vi": ["thiet_bi:ghi"]},
        nguon="nguoi",
        pham_vi_hieu_luc=("thiet_bi:ghi",),
    )
    m = so.dang_chay["mod-nang-cao"]
    res = asyncio.run(goi(m, "thuc_hien", "mnc.cua-cuon", "mo_cua", {}, ngu_canh=nc, so=so))
    assert isinstance(res, dict)
    assert res.get("cho_phe_duyet") is True
    assert "phe_duyet_id" in res
    assert "status_code" not in res  # SEC-004b: không nhồi status_code vào thân phản hồi

    ds = thiet_bi.doc_hang_cho_phe_duyet()
    assert any(item["id"] == res["phe_duyet_id"] for item in ds)


def test_sec002_nguon_khong_do_nguoi_goi_dat_duoc(moi_truong):
    """4. Thực thể chưa xác nhận bị coi là nguy_hiem; sau khi quản trị xác nhận mới chạy; nguon lõi đóng dấu."""
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

    # 1. Chưa xác nhận -> quản trị gọi không có xac_nhan: true -> ném 409 can_xac_nhan
    with pytest.raises(LoiOpenAI) as exc:
        asyncio.run(goi(m, "thuc_hien", "mnc.den-phong", "bat", {}, nguon="kenh", ngu_canh=nc, so=so))
    assert exc.value.ma == 409

    # 2. Quản trị xác nhận thực thể
    r_xn = client.post("/api/thiet-bi/mnc.den-phong/xac-nhan", headers=QT)
    assert r_xn.status_code == 200

    # Bọc m.chat nhận **kwargs để khẳng định 'nguon' do người gọi truyền vào bị loại bỏ
    kw_nhan_duoc = {}
    goc_chat = m.chat
    async def chat_kiem_tra(*args, **kwargs):
        kw_nhan_duoc.update(kwargs)
        assert "nguon" not in kwargs, "Trường 'nguon' do người gọi truyền vào KHÔNG được lọt vào module!"
        return await goc_chat(*args, **kwargs)
    m.chat = chat_kiem_tra

    # Người gọi cố tình truyền nguon="kenh" qua goi()
    asyncio.run(goi(m, "chat", {"messages": []}, nguon="kenh", ngu_canh=nc, stream=False))
    assert "stream" in kw_nhan_duoc
    assert "nguon" not in kw_nhan_duoc

    # 3. Sau khi xác nhận -> gọi thuc_hien thành công, nguon trong nhật ký PHẢI là "nguoi" từ ngu_canh
    asyncio.run(goi(m, "thuc_hien", "mnc.den-phong", "bat", {}, nguon="kenh", ngu_canh=nc, so=so))

    r_nk = client.get("/api/nhat-ky?kenh=thiet_bi", headers=QT)
    assert r_nk.status_code == 200
    nk = r_nk.json()
    assert len(nk) > 0
    dong_cuoi = nk[0]
    them = dong_cuoi.get("them", {})
    assert them.get("nguon") == "nguoi"


def test_sec002_tac_vu_nen_ke_thua_ngu_canh(moi_truong):
    """5. Tác vụ nền kế thừa ContextVar đúng đắn; khách bị từ chối quyền trong tác vụ nền."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})

    m = so.dang_chay["mod-nang-cao"]

    # Ca 1: Nội bộ hợp lệ kế thừa
    nc_noi_bo = NguCanh(danh_tinh={"loai": "noi_bo", "id": "task_nen"}, nguon="tu_dong", pham_vi_hieu_luc=("*",))
    ket_qua_nen = []

    async def tac_vu_nen():
        res = await goi(m, "chat", {"messages": []})
        ket_qua_nen.append(res)

    async def luong_chinh():
        tok = ngu_canh.dat(nc_noi_bo.danh_tinh, nguon=nc_noi_bo.nguon, pham_vi_hieu_luc=nc_noi_bo.pham_vi_hieu_luc)
        try:
            task = asyncio.create_task(tac_vu_nen())
            await task
        finally:
            ngu_canh.khoi_phuc(tok)

    asyncio.run(luong_chinh())
    assert len(ket_qua_nen) == 1
    ma, _, _ = ket_qua_nen[0]
    assert ma == 200

    # Ca 2: Khách chỉ có quyền chat chạy tác vụ nền gọi thiet_bi -> BỊ TỪ CHỐI 403
    nc_khach = NguCanh(danh_tinh={"loai": "khach", "id": "khach_chat"}, nguon="nguoi", pham_vi_hieu_luc=("chat",))
    loi_nen = []

    async def tac_vu_khach():
        try:
            await goi(m, "thuc_hien", "mnc.den-phong", "bat", {})
        except LoiOpenAI as e:
            loi_nen.append(e)

    async def luong_khach():
        tok = ngu_canh.dat(nc_khach.danh_tinh, nguon=nc_khach.nguon, pham_vi_hieu_luc=nc_khach.pham_vi_hieu_luc)
        try:
            task = asyncio.create_task(tac_vu_khach())
            await task
        finally:
            ngu_canh.khoi_phuc(tok)

    asyncio.run(luong_khach())
    assert len(loi_nen) == 1
    assert loi_nen[0].ma == 403
    assert loi_nen[0].code == "insufficient_scope"


def test_sec002_dong_thoi_khong_lan_ngu_canh(moi_truong):
    """6. Hai coroutine đồng thời (khách chỉ chat và admin) đan xen qua asyncio.sleep: không lẫn ngữ cảnh."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})
    client.post("/api/thiet-bi/mnc.den-phong/xac-nhan", headers=QT)

    m = so.dang_chay["mod-nang-cao"]

    async def chay_coroutine_dan_xen():
        danh_sach_vai_admin = []
        danh_sach_vai_khach = []
        ket_qua_goi_admin = []
        ket_qua_goi_khach = []

        async def task_admin():
            tok = ngu_canh.dat(
                {"loai": "quan_tri", "id": "admin_1", "vai": "quan_tri"},
                nguon="nguoi",
                pham_vi_hieu_luc=("*",),
            )
            try:
                await asyncio.sleep(0.03)
                nc = ngu_canh.lay()
                danh_sach_vai_admin.append(nc.danh_tinh.get("vai") if nc else None)
                res = await goi(m, "thuc_hien", "mnc.den-phong", "bat", {}, so=so)
                ket_qua_goi_admin.append(res.get("ok"))
            finally:
                ngu_canh.khoi_phuc(tok)

        async def task_khach():
            await asyncio.sleep(0.01)
            tok = ngu_canh.dat(
                {"loai": "khach", "id": "khach_1", "vai": "khach"},
                nguon="ai",
                pham_vi_hieu_luc=("chat",),
            )
            try:
                await asyncio.sleep(0.03)
                nc = ngu_canh.lay()
                danh_sach_vai_khach.append(nc.danh_tinh.get("vai") if nc else None)
                try:
                    await goi(m, "thuc_hien", "mnc.den-phong", "bat", {}, so=so)
                    ket_qua_goi_khach.append("thanh_cong")
                except LoiOpenAI as e:
                    ket_qua_goi_khach.append(e.ma)
            finally:
                ngu_canh.khoi_phuc(tok)

        await asyncio.gather(task_admin(), task_khach())
        return danh_sach_vai_admin, danh_sach_vai_khach, ket_qua_goi_admin, ket_qua_goi_khach

    vai_admin, vai_khach, kq_admin, kq_khach = asyncio.run(chay_coroutine_dan_xen())

    # Task admin không bị lẫn ngữ cảnh của task khách
    assert vai_admin == ["quan_tri"]
    assert kq_admin == [True]

    # Task khách không bị lẫn ngữ cảnh hay leo thang quyền từ admin
    assert vai_khach == ["khach"]
    assert kq_khach == [403]


def test_sec002_leo_thang_quyen_va_khong_khoa(moi_truong):
    """Kiểm leo thang quyền (Mục 6): khách gọi đường quản trị -> 403; không khoá -> 401."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})

    r_k = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khach Thuong", "pham_vi": ["chat", "thiet_bi:doc"]})
    hd_khach = {"Authorization": f"Bearer {r_k.json()['khoa']['khoa']}"}

    duong_quan_tri = [
        ("POST", "/api/thiet-bi/mnc.den-phong/xac-nhan", {}),
        ("GET", "/api/phe-duyet", None),
        ("POST", "/api/phe-duyet/pd_fake123/duyet", {}),
        ("POST", "/api/phe-duyet/pd_fake123/tu-choi", {}),
    ]

    for phuong_thuc, duong, payload in duong_quan_tri:
        # 1. Không khoá -> 401
        if phuong_thuc == "GET":
            r_no_auth = client.get(duong)
        else:
            r_no_auth = client.post(duong, json=payload or {})
        assert r_no_auth.status_code == 401, f"Đường {duong} không khoá phải trả 401, nhận {r_no_auth.status_code}"

        # 2. Khoá khách gọi đường quản trị -> 403
        if phuong_thuc == "GET":
            r_khach = client.get(duong, headers=hd_khach)
        else:
            r_khach = client.post(duong, headers=hd_khach, json=payload or {})
        assert r_khach.status_code == 403, f"Khoá khách gọi {duong} phải trả 403, nhận {r_khach.status_code}"


# ───────────── Test SEC-004: Kiểm soát an toàn thiết bị ─────────────

def test_sec004a_luoc_do_tham_so(moi_truong):
    """SEC-004(a): Kiểm tra tham số theo lược đồ. Chặn NaN/Infinity, hỗ trợ exclusive min/max, type danh sách, mảng/object lồng nhau, trần 8 KB."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})
    client.post("/api/thiet-bi/mnc.den-phong/xac-nhan", headers=QT)

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

    # 6. NaN / Infinity lọt qua min/max -> từ chối 400
    # 6a. Phía HTTP client gửi JSON thô chứa NaN/Infinity -> server parse_constant từ chối 400
    r_nan = client.post(
        "/api/thiet-bi/mnc.den-phong/hanh-dong",
        headers={"Authorization": QT["Authorization"], "Content-Type": "application/json"},
        content=b'{"hanh_dong": "chinh_sang", "tham_so": {"muc_sang": NaN}}',
    )
    assert r_nan.status_code == 400

    r_inf = client.post(
        "/api/thiet-bi/mnc.den-phong/hanh-dong",
        headers={"Authorization": QT["Authorization"], "Content-Type": "application/json"},
        content=b'{"hanh_dong": "chinh_sang", "tham_so": {"muc_sang": Infinity}}',
    )
    assert r_inf.status_code == 400

    # 6b. Gọi hàm kiem_tra_tham_so trực tiếp với float("nan") / float("inf") -> ném LoiOpenAI 400
    luoc_do_sang = {"type": "object", "properties": {"muc_sang": {"type": "integer", "minimum": 0, "maximum": 100}}}
    tinh_nang_sang = {"chinh_sang": {"tham_so": luoc_do_sang}}
    with pytest.raises(LoiOpenAI) as exc_nan:
        thiet_bi.kiem_tra_tham_so(tinh_nang_sang, "chinh_sang", {"muc_sang": float("nan")})
    assert exc_nan.value.ma == 400

    with pytest.raises(LoiOpenAI) as exc_inf:
        thiet_bi.kiem_tra_tham_so(tinh_nang_sang, "chinh_sang", {"muc_sang": float("inf")})
    assert exc_inf.value.ma == 400

    # 7. exclusiveMinimum / exclusiveMaximum: nhiet_do (16.0, 32.0)
    r_ex_min = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=QT, json={"hanh_dong": "nang_cao", "tham_so": {"nhiet_do": 16.0}})
    assert r_ex_min.status_code == 400

    r_ex_max = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=QT, json={"hanh_dong": "nang_cao", "tham_so": {"nhiet_do": 32.0}})
    assert r_ex_max.status_code == 400

    r_ex_ok = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=QT, json={"hanh_dong": "nang_cao", "tham_so": {"nhiet_do": 25.0}})
    assert r_ex_ok.status_code == 200

    # 8. type dạng danh sách: ghi_chu là ["string", "null"]
    r_type_str = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=QT, json={"hanh_dong": "nang_cao", "tham_so": {"ghi_chu": "ghi chu ok"}})
    assert r_type_str.status_code == 200
    r_type_null = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=QT, json={"hanh_dong": "nang_cao", "tham_so": {"ghi_chu": None}})
    assert r_type_null.status_code == 200
    r_type_sai = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=QT, json={"hanh_dong": "nang_cao", "tham_so": {"ghi_chu": 12345}})
    assert r_type_sai.status_code == 400

    # 9. Đệ quy array: danh_sach_kenh items string maxLength 10, minItems 1, maxItems 3
    r_arr_ok = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=QT, json={"hanh_dong": "nang_cao", "tham_so": {"danh_sach_kenh": ["k1", "k2"]}})
    assert r_arr_ok.status_code == 200
    r_arr_sai_item = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=QT, json={"hanh_dong": "nang_cao", "tham_so": {"danh_sach_kenh": ["ten_qua_dai_hon_10_ky_tu"]}})
    assert r_arr_sai_item.status_code == 400

    # 10. Đệ quy object: cau_hinh_con required kich_hoat (boolean)
    r_obj_ok = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=QT, json={"hanh_dong": "nang_cao", "tham_so": {"cau_hinh_con": {"kich_hoat": True}}})
    assert r_obj_ok.status_code == 200
    r_obj_thieu = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=QT, json={"hanh_dong": "nang_cao", "tham_so": {"cau_hinh_con": {}}})
    assert r_obj_thieu.status_code == 400

    # 11. Trần kích thước tham số 8 KB
    ts_khong_lo = {"k": "x" * 9000}
    r_lon = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=QT, json={"hanh_dong": "bat", "tham_so": ts_khong_lo})
    assert r_lon.status_code == 400
    assert "8 KB" in r_lon.json()["error"]["message"]

    # 12. Hợp lệ toàn bộ -> 200
    r6 = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=QT, json={"hanh_dong": "chinh_sang", "tham_so": {"muc_sang": 80, "che_do": "doc_sach"}})
    assert r6.status_code == 200


def test_sec004b_che_tham_so_bi_mat_va_khong_khai(moi_truong):
    """SEC-004(b): bi_mat: true -> …che; không khai -> …che(độ dài N); GET /api/phe-duyet che tham số; SSE không trùng."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})
    client.post("/api/thiet-bi/mnc.den-phong/xac-nhan", headers=QT)

    # Gọi hành động có tham số bí mật đã khai trong lược đồ
    r_hd = client.post(
        "/api/thiet-bi/mnc.den-phong/hanh-dong",
        headers=QT,
        json={"hanh_dong": "chinh_sang", "tham_so": {"muc_sang": 50, "khoa_bi_mat": "chuoi-rat-bi-mat-12345"}},
    )
    assert r_hd.status_code == 200
    assert "status_code" not in r_hd.json()  # Bỏ trường status_code trong thân phản hồi

    r_nk = client.get("/api/nhat-ky?kenh=thiet_bi", headers=QT)
    dong_nk = r_nk.json()
    chuoi_log = json.dumps(dong_nk, ensure_ascii=False)
    assert "chuoi-rat-bi-mat-12345" not in chuoi_log
    assert "…che" in chuoi_log

    # Tạo yêu cầu phê duyệt có tham số bí mật (chuỗi, mảng và object con) và tham số ngoài lược đồ
    r_tao_k = client.post("/api/khoa-khach", headers=QT, json={"nhan": "App Test Che", "pham_vi": ["thiet_bi:ghi"]})
    hd_k = {"Authorization": f"Bearer {r_tao_k.json()['khoa']['khoa']}"}
    client.post("/api/thiet-bi/mnc.cua-cuon/xac-nhan", headers=QT)

    # Đăng ký nhận sự kiện SSE trước khi gửi yêu cầu (H.4)
    q_sse = thiet_bi.BO_PHAT.dang_ky("admin_sse", la_quan_tri=True)
    try:
        ts_gui = {
            "ma_pin": "pin-bi-mat-9999",
            "ma_the_tu": ["card-secret-1", "card-secret-2"],
            "cau_hinh_con": {
                "khoa_bi_mat": "token-sub-secret-8888",
                "ghi_chu": "cong_phu",
            },
            "khong_khai": "gia_tri_ngoai_999",
        }
        r_pd = client.post(
            "/api/thiet-bi/mnc.cua-cuon/hanh-dong",
            headers=hd_k,
            json={"hanh_dong": "mo_cua", "tham_so": ts_gui},
        )
        assert r_pd.status_code == 202
        assert "status_code" not in r_pd.json()

        # Kiểm tra sự kiện SSE phát ra: tham số PHẢI ĐƯỢC CHE ĐỆ QUY (H.4 & I)
        # phat_dong_bo() đưa sự kiện vào hàng đợi qua loop.create_task khi có loop
        # đang chạy -> đến nơi BẤT ĐỒNG BỘ; get_nowait() đọc trước khi ghi nên đỏ
        # chập chờn. Chờ có điều kiện thay vì get_nowait/ngủ cố định.
        evt = None
        han_cho = time.monotonic() + 5.0
        while time.monotonic() < han_cho:
            try:
                ung_vien = q_sse.get_nowait()
            except asyncio.QueueEmpty:
                time.sleep(0.02)
                continue
            if ung_vien.get("loai") == "phe_duyet_moi":
                evt = ung_vien
                break
        assert evt is not None, "không nhận được sự kiện phe_duyet_moi trong 5 giây"
        evt_ts = evt["muc"]["tham_so"]
        assert evt_ts["ma_pin"] == "…che"
        assert evt_ts["ma_the_tu"] == ["…che", "…che"]
        assert evt_ts["cau_hinh_con"]["khoa_bi_mat"] == "…che"
        assert evt_ts["cau_hinh_con"]["ghi_chu"] == "cong_phu"
        assert evt_ts["khong_khai"] == f"…che(độ dài {len('gia_tri_ngoai_999')})"

        evt_str = json.dumps(evt, ensure_ascii=False)
        assert "pin-bi-mat-9999" not in evt_str
        assert "card-secret-1" not in evt_str
        assert "token-sub-secret-8888" not in evt_str
        assert "gia_tri_ngoai_999" not in evt_str

        # GET /api/phe-duyet PHẢI CHE tham số bí mật / chưa khai báo đệ quy
        r_ds_pd = client.get("/api/phe-duyet", headers=QT)
        assert r_ds_pd.status_code == 200
        hang_cho = r_ds_pd.json().get("hang_cho", [])
        hang_cho_json = json.dumps(hang_cho, ensure_ascii=False)
        assert "pin-bi-mat-9999" not in hang_cho_json
        assert "card-secret-1" not in hang_cho_json
        assert "token-sub-secret-8888" not in hang_cho_json
        assert "gia_tri_ngoai_999" not in hang_cho_json

        muc_cuon = next((m for m in hang_cho if m.get("thuc_the") == "mnc.cua-cuon"), None)
        assert muc_cuon is not None
        ts_hien_thi = muc_cuon["tham_so"]
        assert ts_hien_thi["ma_pin"] == "…che"
        assert ts_hien_thi["ma_the_tu"] == ["…che", "…che"]
        assert ts_hien_thi["cau_hinh_con"]["khoa_bi_mat"] == "…che"
        assert ts_hien_thi["cau_hinh_con"]["ghi_chu"] == "cong_phu"
        assert ts_hien_thi["khong_khai"] == f"…che(độ dài {len('gia_tri_ngoai_999')})"
    finally:
        thiet_bi.BO_PHAT.huy(q_sse)


def test_sec004c_hang_cho_loai_trung_va_tran_so_muc(moi_truong, monkeypatch):
    """SEC-004(c): Hàng chờ loại trùng theo (khoá, thực thể, hành động), không trả pd_id của A cho B, trần theo khoá."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})
    client.post("/api/thiet-bi/mnc.cua-cuon/xac-nhan", headers=QT)

    # Đặt trần theo khoá = 2 và trần toàn cục = 5
    monkeypatch.setenv("WEB2API_PHE_DUYET_TOI_DA_MOI_KHOA", "2")
    monkeypatch.setenv("WEB2API_PHE_DUYET_TRAN_SO_MUC", "5")

    # Tạo khoá A và khoá B
    r_a = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khoa A", "pham_vi": ["thiet_bi:ghi"]})
    hd_a = {"Authorization": f"Bearer {r_a.json()['khoa']['khoa']}"}

    r_b = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khoa B", "pham_vi": ["thiet_bi:ghi"]})
    hd_b = {"Authorization": f"Bearer {r_b.json()['khoa']['khoa']}"}

    # 1. Khoá A gửi yêu cầu 1 (mnc.cua-cuon, mo_cua)
    r1_a = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd_a, json={"hanh_dong": "mo_cua"})
    assert r1_a.status_code == 202
    pd_a_id = r1_a.json()["phe_duyet_id"]

    # 2a. Khoá A gửi lại yêu cầu 1 với nội dung Y HỆT -> trả về cùng pd_a_id (idempotent)
    r1_a_same = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd_a, json={"hanh_dong": "mo_cua"})
    assert r1_a_same.status_code == 202
    assert r1_a_same.json()["phe_duyet_id"] == pd_a_id

    # 2b. Khoá A gửi lại với tham số KHÁC -> huỷ mục cũ, cấp id MỚI (chống TOCTOU ghi đè tham số)
    r1_a_diff = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd_a, json={"hanh_dong": "mo_cua", "tham_so": {"toc_do": 5}})
    assert r1_a_diff.status_code == 202
    pd_a_moi_id = r1_a_diff.json()["phe_duyet_id"]
    assert pd_a_moi_id != pd_a_id
    ds_pd = thiet_bi.doc_hang_cho_phe_duyet()
    assert len(ds_pd) == 1
    assert ds_pd[0]["id"] == pd_a_moi_id
    assert ds_pd[0]["tham_so"] == {"toc_do": 5}

    # ID cũ đã bị huỷ vĩnh viễn, duyệt id cũ -> 404 (chứng minh id cũ không thể mang nội dung mới)
    r_duyet_cu = client.post(f"/api/phe-duyet/{pd_a_id}/duyet", headers=QT)
    assert r_duyet_cu.status_code == 404

    # 3. Khoá B gửi cùng yêu cầu (mnc.cua-cuon, mo_cua) -> KHÔNG ĐƯỢC trả ID của khoá A
    r1_b = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd_b, json={"hanh_dong": "mo_cua"})
    assert r1_b.status_code == 202
    pd_b_id = r1_b.json()["phe_duyet_id"]
    assert pd_b_id != pd_a_moi_id  # Mục luôn thuộc đúng người đề xuất
    assert len(thiet_bi.doc_hang_cho_phe_duyet()) == 2

    # 4. Khoá A gửi yêu cầu 2 (hành động khác: dong_cua) -> thành công (đủ 2/2 mục của khoá A)
    r2_a = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd_a, json={"hanh_dong": "dong_cua"})
    assert r2_a.status_code == 202

    # 5. Khoá A gửi thêm yêu cầu thứ 3 trên thực thể chưa xác nhận -> chạm trần theo khoá (2 mục) -> 429
    r3_a = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=hd_a, json={"hanh_dong": "bat"})
    assert r3_a.status_code == 429
    assert r3_a.json()["error"]["code"] == "queue_full_per_key"


def test_sec004d_duyet_kiem_lai_thuc_the_va_hanh_dong(moi_truong):
    """SEC-004(d): duyet_phe_duyet kiểm lại thực thể/hành động/rủi ro; sai khác -> 409 và xoá mục."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})
    client.post("/api/thiet-bi/mnc.cua-cuon/xac-nhan", headers=QT)

    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "App", "pham_vi": ["thiet_bi:ghi"]})
    hd = {"Authorization": f"Bearer {r_tao.json()['khoa']['khoa']}"}

    r_yc = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd, json={"hanh_dong": "mo_cua"})
    pd_id = r_yc.json()["phe_duyet_id"]

    # Quản trị hạ mức rủi ro xuống "ghi" (không còn "nguy_hiem")
    p_tb = data_dir / "thiet_bi.json"
    doc = kho.doc_json(p_tb)
    doc["ghi_de_rui_ro"]["mnc.cua-cuon"] = "ghi"
    kho.ghi_nguyen_tu(p_tb, json.dumps(doc, indent=2), quyen=0o600)

    # Quản trị duyệt -> phát hiện mức rủi ro không còn nguy_hiem -> 409
    r_duyet = client.post(f"/api/phe-duyet/{pd_id}/duyet", headers=QT)
    assert r_duyet.status_code == 409
    assert "nguy_hiem" in r_duyet.json()["error"]["message"]


def test_sec004d_doi_tinh_nang_giua_luc_tao_va_duyet_409(moi_truong):
    """SEC-004(d): Đổi tính năng của thực thể giữa lúc tạo và duyệt -> 409 conflict, module KHÔNG bị gọi."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})
    client.post("/api/thiet-bi/mnc.cua-cuon/xac-nhan", headers=QT)

    r_k = client.post("/api/khoa-khach", headers=QT, json={"nhan": "App Tam So", "pham_vi": ["thiet_bi:ghi"]})
    hd_k = {"Authorization": f"Bearer {r_k.json()['khoa']['khoa']}"}

    # Tạo mục phê duyệt
    r_yc = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd_k, json={"hanh_dong": "mo_cua"})
    pd_id = r_yc.json()["phe_duyet_id"]

    m = so.dang_chay["mod-nang-cao"]
    # Đổi tính năng thực thể động trên module sau khi tạo mục phê duyệt
    thuc_the_goc = m.thuc_the
    async def thuc_the_moi(loc=None):
        ents = await thuc_the_goc(loc)
        for e in ents:
            if e["id"] == "mnc.cua-cuon":
                # Thêm hành động mới vào tính năng -> băm tính năng thay đổi so với lúc tạo
                e["tinh_nang"] = ["mo_cua", "dong_cua", "dung_khan_cap"]
        return ents
    m.thuc_the = thuc_the_moi

    so_lan_goi_truoc = len(m.da_chay)

    # Quản trị duyệt -> phát hiện băm tính năng thay đổi -> 409
    r_duyet = client.post(f"/api/phe-duyet/{pd_id}/duyet", headers=QT)
    assert r_duyet.status_code == 409
    assert "thay đổi" in r_duyet.json()["error"]["message"]

    # Module KHÔNG được gọi
    assert len(m.da_chay) == so_lan_goi_truoc


def test_sec004_duyet_dong_thoi_chi_chay_mot_lan(moi_truong):
    """Mục 1 (CAO): Duyệt đồng thời (race condition) chỉ chạy hành động đúng MỘT lần, lần kia 404/409."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})
    client.post("/api/thiet-bi/mnc.cua-cuon/xac-nhan", headers=QT)

    r_k = client.post("/api/khoa-khach", headers=QT, json={"nhan": "App Race", "pham_vi": ["thiet_bi:ghi"]})
    hd_k = {"Authorization": f"Bearer {r_k.json()['khoa']['khoa']}"}

    r_yc = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd_k, json={"hanh_dong": "mo_cua"})
    pd_id = r_yc.json()["phe_duyet_id"]

    m = so.dang_chay["mod-nang-cao"]
    m.da_chay.clear()

    # Giả lập module thực hiện bất đồng bộ có độ trễ I/O (chờ thiết bị thật)
    goc_thuc_hien = m.thuc_hien
    async def thuc_hien_cham(*args, **kwargs):
        await asyncio.sleep(0.05)
        return await goc_thuc_hien(*args, **kwargs)
    m.thuc_hien = thuc_hien_cham

    # Gọi duyệt đồng thời qua duyet_phe_duyet
    async def chay_dong_thoi():
        task1 = thiet_bi.duyet_phe_duyet(pd_id, so, nguoi_duyet="admin_1")
        task2 = thiet_bi.duyet_phe_duyet(pd_id, so, nguoi_duyet="admin_2")
        return await asyncio.gather(task1, task2, return_exceptions=True)

    ket_qua = asyncio.run(chay_dong_thoi())

    # Đúng MỘT lần thành công, một lần gặp ngoại lệ 404
    thanh_cong = [r for r in ket_qua if isinstance(r, dict) and r.get("ok")]
    loi = [r for r in ket_qua if isinstance(r, LoiOpenAI)]

    assert len(thanh_cong) == 1
    assert len(loi) == 1
    assert loi[0].ma in (404, 409)

    # Module thuc_hien chỉ được gọi đúng 1 lần duy nhất
    assert len(m.da_chay) == 1


def test_sec004e_tran_tan_suat_thiet_bi(moi_truong, monkeypatch):
    """SEC-004(e): Trần tần suất lệnh (8 lệnh/lượt, 30 lệnh/phút/khoá/thực thể -> 429)."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})
    client.post("/api/thiet-bi/mnc.den-phong/xac-nhan", headers=QT)

    monkeypatch.setenv("WEB2API_THIET_BI_TRAN_LUOT", "3")

    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "App RateLimit", "pham_vi": ["thiet_bi:ghi"]})
    hd = {"Authorization": f"Bearer {r_tao.json()['khoa']['khoa']}"}

    # Gọi 3 lệnh hợp lệ
    for _ in range(3):
        r = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=hd, json={"hanh_dong": "bat"})
        assert r.status_code == 200

    # Lệnh thứ 4 vượt trần lượt 3 -> 429 kèm Retry-After
    r_chan = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=hd, json={"hanh_dong": "bat"})
    assert r_chan.status_code == 429
    assert r_chan.json()["error"]["code"] == "rate_limit_exceeded"
    assert "5 giây" in r_chan.json()["error"]["message"]
    assert "Retry-After" in r_chan.headers

    # 2. Kiểm tra trần theo phút (khoá, thực thể): WEB2API_THIET_BI_TRAN_PHUT (H.7)
    r_tao2 = client.post("/api/khoa-khach", headers=QT, json={"nhan": "App RateLimit Minute", "pham_vi": ["thiet_bi:ghi"]})
    hd2 = {"Authorization": f"Bearer {r_tao2.json()['khoa']['khoa']}"}

    monkeypatch.setenv("WEB2API_THIET_BI_TRAN_LUOT", "20")
    monkeypatch.setenv("WEB2API_THIET_BI_TRAN_PHUT", "4")

    for _ in range(4):
        r = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=hd2, json={"hanh_dong": "bat"})
        assert r.status_code == 200

    r_chan_phut = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=hd2, json={"hanh_dong": "bat"})
    assert r_chan_phut.status_code == 429
    assert r_chan_phut.json()["error"]["code"] == "rate_limit_exceeded"
    assert "lệnh/phút" in r_chan_phut.json()["error"]["message"]
    assert "Retry-After" in r_chan_phut.headers


def test_sec004f_thuc_the_moi_doi_tinh_nang_coi_la_nguy_hiem(moi_truong):
    """SEC-004(f): Thực thể mới cài hoặc đổi tinh_nang coi là nguy_hiem cho tới khi quản trị xác nhận."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})

    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "App", "pham_vi": ["thiet_bi:ghi"]})
    hd = {"Authorization": f"Bearer {r_tao.json()['khoa']['khoa']}"}

    # 1. Mới cài, chưa xác nhận -> coi là nguy_hiem -> khách gọi vào hàng chờ 202
    r_chua = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=hd, json={"hanh_dong": "bat"})
    assert r_chua.status_code == 202
    assert r_chua.json()["cho_phe_duyet"] is True

    # 2. Quản trị xác nhận
    r_xn = client.post("/api/thiet-bi/mnc.den-phong/xac-nhan", headers=QT)
    assert r_xn.status_code == 200

    # 3. Khách gọi lại -> đã xác nhận, chạy thẳng 200
    r_xn_roi = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=hd, json={"hanh_dong": "bat"})
    assert r_xn_roi.status_code == 200

    # 4. Giả lập module đổi tính năng (băm không khớp da_xac_nhan)
    p_tb = data_dir / "thiet_bi.json"
    doc = kho.doc_json(p_tb)
    doc["da_xac_nhan"]["mnc.den-phong"] = "bam_cu_khong_khop"
    kho.ghi_nguyen_tu(p_tb, json.dumps(doc, indent=2), quyen=0o600)

    # Khách gọi lại -> lập tức bị coi là nguy_hiem -> vào hàng chờ 202
    r2 = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=hd, json={"hanh_dong": "bat"})
    assert r2.status_code == 202
    assert r2.json()["cho_phe_duyet"] is True

    # Quản trị xác nhận lại
    r_xn2 = client.post("/api/thiet-bi/mnc.den-phong/xac-nhan", headers=QT)
    assert r_xn2.status_code == 200

    # Khách gọi lại -> chạy thẳng 200
    r3 = client.post("/api/thiet-bi/mnc.den-phong/hanh-dong", headers=hd, json={"hanh_dong": "bat"})
    assert r3.status_code == 200

    # 5. Kịch bản Mục D: Admin xác nhận cửa cuốn (mặc định nguy_hiem)
    r_xn_cc = client.post("/api/thiet-bi/mnc.cua-cuon/xac-nhan", headers=QT)
    assert r_xn_cc.status_code == 200

    # Module cập nhật: GIỮ NGUYÊN tinh_nang nhưng đổi rui_ro_mac_dinh từ nguy_hiem thành doc
    m = so.dang_chay["mod-nang-cao"]
    thuc_the_goc = m.thuc_the
    async def thuc_the_doi_rui_ro(loc=None):
        ents = await thuc_the_goc(loc)
        for e in ents:
            if e.get("id") == "mnc.cua-cuon":
                e["rui_ro_mac_dinh"] = "doc"
        return ents
    m.thuc_the = thuc_the_doi_rui_ro

    # Vì rui_ro_mac_dinh thay đổi, băm không khớp -> mất xác nhận -> rủi ro hiệu lực trở thành nguy_hiem!
    # Khách gọi dong_cua KHÔNG được chạy thẳng 200 mà PHẢI vào hàng chờ 202
    r_dong_cua = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd, json={"hanh_dong": "dong_cua"})
    assert r_dong_cua.status_code == 202
    assert r_dong_cua.json()["cho_phe_duyet"] is True


def test_sec002_kenh_nhat_ky_goi_kha_nang(moi_truong):
    """Mục 8: Kênh riêng goi_kha_nang: chỉ ghi từ chối hoặc hành động ghi/nguy hiểm, không ngập kênh quan_tri."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})

    r_k = client.post("/api/khoa-khach", headers=QT, json={"nhan": "App Chat", "pham_vi": ["chat"]})
    hd = {"Authorization": f"Bearer {r_k.json()['khoa']['khoa']}"}

    # 1. Khách gọi chat bình thường -> KHÔNG ghi từng lời gọi vào kênh goi_kha_nang (chống spam log)
    for _ in range(5):
        r_chat = client.post("/v1/chat/completions", headers=hd, json={"model": "mnc/model", "messages": [{"role": "user", "content": "hi"}]})
        assert r_chat.status_code == 200

    r_gk_chat = client.get("/api/nhat-ky?kenh=goi_kha_nang", headers=QT)
    assert r_gk_chat.status_code == 200
    dong_gk_chat = r_gk_chat.json()
    assert not any("chat" in d.get("tom_tat", "").lower() for d in dong_gk_chat)

    # 2. Khách bị từ chối phạm vi (chỉ có chat, gọi thuc_hien) -> ghi nhận cảnh báo vào kênh goi_kha_nang
    nc_khach = NguCanh(danh_tinh={"loai": "khach", "id": "khach1"}, nguon="nguoi", pham_vi_hieu_luc=("chat",))
    m = so.dang_chay["mod-nang-cao"]
    with pytest.raises(LoiOpenAI) as exc_pv:
        asyncio.run(goi(m, "thuc_hien", "mnc.den-phong", "bat", {}, ngu_canh=nc_khach, so=so))
    assert exc_pv.value.ma == 403

    r_gk = client.get("/api/nhat-ky?kenh=goi_kha_nang", headers=QT)
    dong_gk = r_gk.json()
    assert any("Từ chối lời gọi khả năng" in d.get("tom_tat", "") for d in dong_gk)


def test_sec002_hoi_quy_quy_trinh_va_goi(moi_truong):
    """Mục 7: Hồi quy quy_trinh (xep_hang chặn len(docs)=0, top_n) và goi đòi cả phương thức lẫn cung_cap."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})

    m = so.dang_chay["mod-nang-cao"]

    # 1. xep_hang khi docs rỗng -> trả về du_lieu_vao nguyên vẹn không gọi module
    nc = NguCanh(danh_tinh={"loai": "quan_tri", "id": "admin"}, nguon="nguoi", pham_vi_hieu_luc=("*",))
    tok = ngu_canh.dat(nc.danh_tinh, nguon=nc.nguon, pham_vi_hieu_luc=nc.pham_vi_hieu_luc)
    try:
        kq_rong = asyncio.run(quy_trinh._thuc_hien_mot_tang(
            kha_nang="xep_hang",
            nguon="mod-nang-cao",
            tham_so={"top_n": 5},
            du_lieu_vao={"chuoi": "hoi", "muc": []},
            so=so,
        ))
        assert kq_rong == {"chuoi": "hoi", "muc": []}

        # 2. xep_hang với top_n không hợp lệ (<= 0 hoặc sai kiểu) -> bị so_nguyen_chat từ chối 400
        with pytest.raises(LoiOpenAI) as exc_topn:
            asyncio.run(quy_trinh._thuc_hien_mot_tang(
                kha_nang="xep_hang",
                nguon="mod-nang-cao",
                tham_so={"top_n": 0},
                du_lieu_vao={"chuoi": "hoi", "muc": [{"doan": "doc1"}]},
                so=so,
            ))
        assert exc_topn.value.ma == 400

        # top_n > len(docs) được kẹp về len(docs) mà không báo lỗi 400
        kq_kep = asyncio.run(quy_trinh._thuc_hien_mot_tang(
            kha_nang="xep_hang",
            nguon="mod-nang-cao",
            tham_so={"top_n": 10},
            du_lieu_vao={"chuoi": "hoi", "muc": [{"doan": "doc1"}]},
            so=so,
        ))
        assert isinstance(kq_kep, dict)
    finally:
        ngu_canh.khoi_phuc(tok)

    # 3. quy_trinh khi thiếu ngữ cảnh (ngu_canh.lay() is None) -> đóng 403 missing_context
    tok_none = ngu_canh.NGU_CANH.set(None)
    try:
        with pytest.raises(LoiOpenAI) as exc_mc:
            asyncio.run(quy_trinh._thuc_hien_mot_tang(
                kha_nang="xep_hang",
                nguon="mod-nang-cao",
                tham_so={"top_n": 1},
                du_lieu_vao={"chuoi": "hoi", "muc": [{"doan": "doc1"}]},
                so=so,
            ))
        assert exc_mc.value.ma == 403
        assert exc_mc.value.code == "missing_context"
    finally:
        ngu_canh.NGU_CANH.reset(tok_none)

    # 4. goi đòi cả phương thức lẫn cung_cap: module có phương thức nhưng manifest không khai -> 501
    class ModuleKhongKhaiCungCap:
        manifest = {"id": "mod-khong-khai", "cung_cap": ["chat"]}
        async def anh(self, body): return 200, {}, b"{}"

    m_khong_khai = ModuleKhongKhaiCungCap()
    tok_nb = ngu_canh.dat({"loai": "noi_bo", "id": "test"}, nguon="tu_dong", pham_vi_hieu_luc=("*",))
    try:
        with pytest.raises(LoiOpenAI) as exc_501:
            asyncio.run(goi(m_khong_khai, "anh", {}))
        assert exc_501.value.ma == 501
        assert exc_501.value.code == "capability_not_supported"

        # 5. Module chỉ có _manifest (như muse): nhận diện đúng cung_cap từ _manifest
        class ModuleCoUnderscoreManifest:
            def __init__(self):
                self._manifest = {"id": "mod-underscore", "cung_cap": ["anh"]}
            async def anh(self, body): return 200, {}, b'{"anh": true}'
            async def noi(self, body): return 200, {}, b'{"noi": true}'

        m_under = ModuleCoUnderscoreManifest()
        # "anh" có trong _manifest -> thành công
        ma_anh, _, noi_dung_anh = asyncio.run(goi(m_under, "anh", {}))
        assert ma_anh == 200
        assert b"anh" in noi_dung_anh

        # "noi" không có trong _manifest -> 501
        with pytest.raises(LoiOpenAI) as exc_under:
            asyncio.run(goi(m_under, "noi", {}))
        assert exc_under.value.ma == 501
        assert exc_under.value.code == "capability_not_supported"

        # 6. Module hoàn toàn không có manifest -> ĐÓNG (fail closed) với các khả năng chuyên biệt
        class ModuleKhongCoManifestNao:
            async def anh(self, body): return 200, {}, b"{}"
            async def chat(self, body, stream=False): return 200, {}, b"{}"

        m_no_manifest = ModuleKhongCoManifestNao()
        with pytest.raises(LoiOpenAI) as exc_no_mf:
            asyncio.run(goi(m_no_manifest, "anh", {}))
        assert exc_no_mf.value.ma == 501
        assert exc_no_mf.value.code == "capability_not_supported"

        # Chat cốt lõi vẫn gọi được
        ma_chat, _, _ = asyncio.run(goi(m_no_manifest, "chat", {}))
        assert ma_chat == 200
    finally:
        ngu_canh.khoi_phuc(tok_nb)


def test_sec004a_tham_so_long_sau_bi_chan_400():
    """Mục E: Tham số lồng sâu ~990 tầng bị từ chối 400 (chống RecursionError 500)."""
    # Tạo cấu trúc lồng sâu 990 tầng
    cur = {"val": 1}
    for i in range(990):
        cur = {"sub": cur}

    # kiem_tra_tham_so từ chối 400, không bị RecursionError 500
    with pytest.raises(LoiOpenAI) as exc:
        thiet_bi.kiem_tra_tham_so([], "test", cur)
    assert exc.value.ma == 400
    assert "Độ sâu" in str(exc.value)

    # them_hang_cho_phe_duyet cũng từ chối 400
    with pytest.raises(LoiOpenAI) as exc_hc:
        thiet_bi.them_hang_cho_phe_duyet("admin", "test.dev", "test", cur)
    assert exc_hc.value.ma == 400
    assert "Độ sâu" in str(exc_hc.value)


def test_sec004_ly_do_va_chuoi_tran_do_dai(moi_truong):
    """Mục F: ly_do vượt 500 ký tự hoặc chuỗi vào hàng chờ vượt trần -> 400 invalid_request."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})
    client.post("/api/thiet-bi/mnc.cua-cuon/xac-nhan", headers=QT)

    r_k = client.post("/api/khoa-khach", headers=QT, json={"nhan": "App F", "pham_vi": ["thiet_bi:ghi"]})
    hd = {"Authorization": f"Bearer {r_k.json()['khoa']['khoa']}"}

    # 1. ly_do dài 501 ký tự qua HTTP -> 400 invalid_request
    ly_do_dai = "a" * 501
    r = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd, json={"hanh_dong": "mo_cua", "ly_do": ly_do_dai})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"
    assert "500 ký tự" in r.json()["error"]["message"]

    # 2. ly_do đúng 500 ký tự -> hợp lệ 202
    ly_do_hop_le = "a" * 500
    r_ok = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd, json={"hanh_dong": "mo_cua", "ly_do": ly_do_hop_le})
    assert r_ok.status_code == 202

    # 3. them_hang_cho_phe_duyet với ai_de_xuat > 128 hoặc thuc_the > 256 -> 400
    with pytest.raises(LoiOpenAI) as exc_ai:
        thiet_bi.them_hang_cho_phe_duyet("x" * 129, "mnc.cua-cuon", "mo_cua", {})
    assert exc_ai.value.ma == 400

    with pytest.raises(LoiOpenAI) as exc_tt:
        thiet_bi.them_hang_cho_phe_duyet("admin", "x" * 257, "mo_cua", {})
    assert exc_tt.value.ma == 400


def test_sec004_kiem_lai_luoc_do_khi_duyet_va_dedup(moi_truong):
    """H.6: Bắt bỏ kiểm lại lược đồ khi duyệt (đổi lược đồ -> 409); test dedup huỷ mục cũ cấp id mới."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "mod-nang-cao"
    ghi_thu_muc(mod_path, mau_module_nang_cao())
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/mod-nang-cao/cai", headers=QT, json={"cau_hinh": {}})
    client.post("/api/thiet-bi/mnc.cua-cuon/xac-nhan", headers=QT)

    r_k = client.post("/api/khoa-khach", headers=QT, json={"nhan": "App H6", "pham_vi": ["thiet_bi:ghi"]})
    hd = {"Authorization": f"Bearer {r_k.json()['khoa']['khoa']}"}

    m = so.dang_chay["mod-nang-cao"]
    m.da_chay.clear()

    # 1. Kiểm lại lược đồ khi duyệt:
    # Khách gửi mo_cua với toc_do: 80 (lúc này hợp lệ vì maximum: 100)
    r_yc = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd, json={"hanh_dong": "mo_cua", "tham_so": {"toc_do": 80}})
    assert r_yc.status_code == 202
    pd_id = r_yc.json()["phe_duyet_id"]

    # Giả lập module đổi lược đồ: hạ maximum xuống 50
    thuc_the_goc = m.thuc_the
    async def thuc_the_luoc_do_moi(loc=None):
        ents = await thuc_the_goc(loc)
        for e in ents:
            if e.get("id") == "mnc.cua-cuon":
                for tn in e.get("tinh_nang", []):
                    if isinstance(tn, dict) and tn.get("hanh_dong") == "mo_cua":
                        tn["tham_so"]["properties"]["toc_do"]["maximum"] = 50
        return ents
    m.thuc_the = thuc_the_luoc_do_moi

    # Quản trị duyệt -> phát hiện tính năng/lược đồ đã thay đổi -> trả 409 conflict
    r_duyet = client.post(f"/api/phe-duyet/{pd_id}/duyet", headers=QT)
    assert r_duyet.status_code == 409
    assert ("thay đổi" in r_duyet.json()["error"]["message"] or "lược đồ" in r_duyet.json()["error"]["message"])
    assert len(m.da_chay) == 0

    # Khôi phục thuc_the
    m.thuc_the = thuc_the_goc

    # Kiểm tra trực tiếp bước kiểm lại lược đồ (khi mục không có băm cũ):
    r_yc2 = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd, json={"hanh_dong": "mo_cua", "tham_so": {"toc_do": 80}})
    pd_id_2 = r_yc2.json()["phe_duyet_id"]
    p_pd = data_dir / "phe_duyet.json"
    doc_pd = kho.doc_json(p_pd)
    for it in doc_pd.get("hang_cho", []):
        if it.get("id") == pd_id_2:
            it["bam"] = None
    kho.ghi_nguyen_tu(p_pd, json.dumps(doc_pd, indent=2), quyen=0o600)

    m.thuc_the = thuc_the_luoc_do_moi
    r_duyet_ld = client.post(f"/api/phe-duyet/{pd_id_2}/duyet", headers=QT)
    assert r_duyet_ld.status_code == 409
    assert "lược đồ" in r_duyet_ld.json()["error"]["message"]
    assert len(m.da_chay) == 0

    m.thuc_the = thuc_the_goc

    # 2. Test dedup: nếu nội dung thay đổi, huỷ mục cũ và cấp id mới
    r_lan1 = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd, json={"hanh_dong": "mo_cua", "tham_so": {"toc_do": 10}})
    assert r_lan1.status_code == 202
    pd_id_1 = r_lan1.json()["phe_duyet_id"]

    r_lan2 = client.post("/api/thiet-bi/mnc.cua-cuon/hanh-dong", headers=hd, json={"hanh_dong": "mo_cua", "tham_so": {"toc_do": 20}})
    assert r_lan2.status_code == 202
    pd_id_2 = r_lan2.json()["phe_duyet_id"]

    # ID mới phải khác ID cũ
    assert pd_id_1 != pd_id_2

    # Duyệt ID cũ bị 404 (vì đã bị huỷ để tránh TOCTOU)
    r_duyet_cu = client.post(f"/api/phe-duyet/{pd_id_1}/duyet", headers=QT)
    assert r_duyet_cu.status_code == 404

    # Duyệt ID mới thành công 200
    r_duyet_moi = client.post(f"/api/phe-duyet/{pd_id_2}/duyet", headers=QT)
    assert r_duyet_moi.status_code == 200
    assert len(m.da_chay) == 1
    assert m.da_chay[0]["tham_so"]["toc_do"] == 20


