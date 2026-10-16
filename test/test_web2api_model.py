"""Kiểm thử tính năng quản lý model (VIỆC B2):
- Bật/tắt model
- Đặt mặc định model theo nhóm (chat, anh, video, nhóm lạ)
- Chọn tất cả / bỏ chọn tất cả
- /v1/models không liệt kê model tắt, model mặc định có mac_dinh: true
- Gọi model tắt trả 404 model_not_found kèm «đã tắt trong Hệ thống → AI»
- Model mới mặc định bật
- Module giả với nhóm lạ hoạt động đúng
"""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app import kho, so_module
from app.main import app

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


def _tao_module_gia(goc: Path, id_: str, tien_to: str, models: list[dict], cung_cap: list[str] | None = None) -> Path:
    thu_muc = goc / id_
    thu_muc.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": id_,
        "ten": f"Module {id_}",
        "mo_ta": "Module test model",
        "phien_ban": "1.0.0",
        "hang": "A",
        "loai": "ai",
        "giao_thuc": "openai",
        "tien_to": tien_to,
        "cung_cap": cung_cap or ["chat", "anh"],
        "truong_cau_hinh": [
            {
                "khoa": "api_key",
                "nhan": "Khoá API",
                "kieu": "bi_mat",
                "bat_buoc": True,
            }
        ],
        "tai_nguyen": {"mang_ra": []},
    }
    (thu_muc / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    ma_python = f"""class Module:
    def __init__(self, m):
        self.manifest = m
        self.tien_to = m["tien_to"]
        self.raw_models = {models!r}
    def nap(self, cfg):
        pass
    async def cai(self, cfg):
        return {{"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": cfg}}
    async def chat(self, body, stream):
        return 200, {{"content-type": "application/json"}}, b'{{"choices": [{{"message": {{"content": "ok"}}}}]}}'
    async def anh(self, body):
        return 200, {{"content-type": "application/json"}}, b'{{"data": [{{"url": "https://example.com/a.png"}}]}}'
    async def models(self):
        return [dict(x) for x in self.raw_models]
    async def suc_khoe(self):
        return {{"ok": True}}
    async def dung(self):
        pass
"""
    (thu_muc / "module.py").write_text(ma_python, encoding="utf-8")
    return thu_muc


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    modules_dir = data_dir / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return data_dir


def test_kho_model_json_nguyen_tu(moi_truong):
    # Chưa có tệp
    assert kho.doc_cai_dat_model() == {}
    assert kho.doc_cai_dat_model_module("mod-1") == {"tat": [], "mac_dinh": {}}

    # Lưu cấu hình
    muc = kho.luu_cai_dat_model_module("mod-1", ["m1", "m2", "m1"], {"chat": "m3"})
    assert muc["tat"] == ["m1", "m2"]
    assert muc["mac_dinh"] == {"chat": "m3"}

    # Đọc lại
    doc = kho.doc_cai_dat_model_module("mod-1")
    assert doc["tat"] == ["m1", "m2"]
    assert doc["mac_dinh"] == {"chat": "m3"}

    # Xoá
    assert kho.xoa_cai_dat_model_module("mod-1") is True
    assert kho.doc_cai_dat_model_module("mod-1") == {"tat": [], "mac_dinh": {}}
    assert kho.xoa_cai_dat_model_module("mod-1") is False


def test_api_model_quyen_va_module_chua_lien_ket(moi_truong):
    with TestClient(app) as client:
        # 401 khi không có khoá quản trị
        assert client.get("/api/modules/mod-1/model").status_code == 401
        assert client.put("/api/modules/mod-1/model", json={}).status_code == 401

        # 404 khi module chưa tồn tại
        r_get = client.get("/api/modules/khong-ton-tai/model", headers=QT)
        assert r_get.status_code == 404

        # Cài module nhưng chưa liên kết (chưa nhập khoá)
        _tao_module_gia(
            moi_truong / "modules",
            "mod-chua-lk",
            "mclk",
            [{"id": "mclk/chat-1", "object": "model"}],
        )
        client.post("/api/modules/nap-lai", headers=QT)

        # GET và PUT trả 404 module_not_configured
        r_get_clk = client.get("/api/modules/mod-chua-lk/model", headers=QT)
        assert r_get_clk.status_code == 404
        assert r_get_clk.json()["error"]["code"] == "module_not_configured"

        r_put_clk = client.put("/api/modules/mod-chua-lk/model", json={"tat": []}, headers=QT)
        assert r_put_clk.status_code == 404
        assert r_put_clk.json()["error"]["code"] == "module_not_configured"


def test_api_model_get_va_put_co_ban(moi_truong):
    raw_models = [
        {"id": "m1/chat-flash", "object": "model"},
        {"id": "m1/chat-pro", "object": "model"},
        {"id": "m1/anh-gen", "object": "model", "kha_nang": ["anh"]},
        {"id": "m1/video-gen", "object": "model", "kha_nang": ["video"]},
    ]
    _tao_module_gia(moi_truong / "modules", "mod-1", "m1", raw_models, cung_cap=["chat", "anh", "video"])

    with TestClient(app) as client:
        client.post("/api/modules/nap-lai", headers=QT)
        # Liên kết module
        r_cai = client.post("/api/modules/mod-1/cai", json={"cau_hinh": {"api_key": "khoa-test-1"}}, headers=QT)
        assert r_cai.status_code == 200

        # Ban đầu: mọi model đều BẬT, chưa có mặc định
        r_get = client.get("/api/modules/mod-1/model", headers=QT)
        assert r_get.status_code == 200
        j_get = r_get.json()
        assert j_get["id"] == "mod-1"
        assert j_get["tong_so"] == 4
        assert j_get["dang_bat"] == 4
        assert j_get["tat"] == []
        assert j_get["mac_dinh"] == {}
        assert len(j_get["models"]) == 4
        assert all(m["bat"] is True for m in j_get["models"])
        assert all(m["mac_dinh"] is False for m in j_get["models"])

        # Kiểm tra nhóm
        nhom_map = {m["id"]: m["nhom"] for m in j_get["models"]}
        assert nhom_map["m1/chat-flash"] == "chat"
        assert nhom_map["m1/chat-pro"] == "chat"
        assert nhom_map["m1/anh-gen"] == "anh"
        assert nhom_map["m1/video-gen"] == "video"

        # PUT: tắt m1/chat-flash, đặt m1/chat-pro làm mặc định chat, m1/anh-gen làm mặc định anh
        r_put = client.put(
            "/api/modules/mod-1/model",
            json={
                "tat": ["m1/chat-flash", "model-la-khong-ton-tai"],
                "mac_dinh": {
                    "chat": "m1/chat-pro",
                    "anh": "m1/anh-gen",
                    "video": "model-la-video",
                },
            },
            headers=QT,
        )
        assert r_put.status_code == 200
        j_put = r_put.json()
        assert j_put["ok"] is True
        # Model lạ được bỏ qua và liệt kê trong bo_qua
        assert "model-la-khong-ton-tai" in j_put["bo_qua"]
        assert "model-la-video" in j_put["bo_qua"]
        assert j_put["tat"] == ["m1/chat-flash"]
        assert j_put["mac_dinh"] == {"chat": "m1/chat-pro", "anh": "m1/anh-gen"}
        assert j_put["tong_so"] == 4
        assert j_put["dang_bat"] == 3

        # GET lại để kiểm tra tính bền vững
        r_get2 = client.get("/api/modules/mod-1/model", headers=QT)
        j2 = r_get2.json()
        assert j2["tat"] == ["m1/chat-flash"]
        assert j2["mac_dinh"] == {"chat": "m1/chat-pro", "anh": "m1/anh-gen"}
        m_chat_pro = next(m for m in j2["models"] if m["id"] == "m1/chat-pro")
        assert m_chat_pro["bat"] is True
        assert m_chat_pro["mac_dinh"] is True
        m_chat_flash = next(m for m in j2["models"] if m["id"] == "m1/chat-flash")
        assert m_chat_flash["bat"] is False
        assert m_chat_flash["mac_dinh"] is False


def test_put_dat_mac_dinh_cho_model_tat_tra_400(moi_truong):
    raw_models = [
        {"id": "m1/chat-flash", "object": "model"},
        {"id": "m1/chat-pro", "object": "model"},
    ]
    _tao_module_gia(moi_truong / "modules", "mod-1", "m1", raw_models)

    with TestClient(app) as client:
        client.post("/api/modules/nap-lai", headers=QT)
        client.post("/api/modules/mod-1/cai", json={"cau_hinh": {"api_key": "khoa-test-1"}}, headers=QT)

        # Cố đặt mặc định cho model đang bị tắt trong cùng lượt gọi -> 400
        r = client.put(
            "/api/modules/mod-1/model",
            json={
                "tat": ["m1/chat-flash"],
                "mac_dinh": {"chat": "m1/chat-flash"},
            },
            headers=QT,
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "model_disabled"

        # Tắt m1/chat-flash trước
        client.put("/api/modules/mod-1/model", json={"tat": ["m1/chat-flash"]}, headers=QT)
        # Giờ cố đặt nó làm mặc định mà không bật nó lên -> 400
        r2 = client.put(
            "/api/modules/mod-1/model",
            json={"mac_dinh": {"chat": "m1/chat-flash"}},
            headers=QT,
        )
        assert r2.status_code == 400
        assert r2.json()["error"]["code"] == "model_disabled"


def test_tat_model_mac_dinh_tu_bo_mac_dinh(moi_truong):
    raw_models = [
        {"id": "m1/chat-flash", "object": "model"},
        {"id": "m1/chat-pro", "object": "model"},
    ]
    _tao_module_gia(moi_truong / "modules", "mod-1", "m1", raw_models)

    with TestClient(app) as client:
        client.post("/api/modules/nap-lai", headers=QT)
        client.post("/api/modules/mod-1/cai", json={"cau_hinh": {"api_key": "khoa-test-1"}}, headers=QT)

        # Đặt m1/chat-flash làm mặc định
        client.put("/api/modules/mod-1/model", json={"tat": [], "mac_dinh": {"chat": "m1/chat-flash"}}, headers=QT)

        # Giờ tắt m1/chat-flash (không truyền mac_dinh mới)
        r = client.put("/api/modules/mod-1/model", json={"tat": ["m1/chat-flash"]}, headers=QT)
        assert r.status_code == 200
        j = r.json()
        assert j["tat"] == ["m1/chat-flash"]
        # Mặc định đã tự bị gỡ bỏ
        assert j["mac_dinh"] == {}


def test_v1_models_khong_liet_ke_model_tat_va_them_mac_dinh(moi_truong):
    raw_models = [
        {"id": "m1/chat-flash", "object": "model"},
        {"id": "m1/chat-pro", "object": "model"},
        {"id": "m1/anh-gen", "object": "model", "kha_nang": ["anh"]},
    ]
    _tao_module_gia(moi_truong / "modules", "mod-1", "m1", raw_models)

    with TestClient(app) as client:
        client.post("/api/modules/nap-lai", headers=QT)
        client.post("/api/modules/mod-1/cai", json={"cau_hinh": {"api_key": "khoa-test-1"}}, headers=QT)

        # Đặt m1/chat-pro làm mặc định, tắt m1/chat-flash
        client.put(
            "/api/modules/mod-1/model",
            json={
                "tat": ["m1/chat-flash"],
                "mac_dinh": {"chat": "m1/chat-pro"},
            },
            headers=QT,
        )

        # /v1/models
        r = client.get("/v1/models", headers=QT)
        assert r.status_code == 200
        data = r.json()["data"]

        # m1/chat-flash không có trong danh sách
        ids = [m["id"] for m in data]
        assert "m1/chat-flash" not in ids
        assert "m1/chat-pro" in ids
        assert "m1/anh-gen" in ids

        # m1/chat-pro có mac_dinh: True
        m_pro = next(m for m in data if m["id"] == "m1/chat-pro")
        assert m_pro.get("mac_dinh") is True

        # m1/anh-gen không có mac_dinh (hoặc không là True)
        m_anh = next(m for m in data if m["id"] == "m1/anh-gen")
        assert m_anh.get("mac_dinh") is not True


def test_goi_model_tat_tra_404_model_not_found_kem_loi_nhan(moi_truong):
    raw_models = [
        {"id": "m1/chat-flash", "object": "model"},
        {"id": "m1/chat-pro", "object": "model"},
    ]
    _tao_module_gia(moi_truong / "modules", "mod-1", "m1", raw_models)

    with TestClient(app) as client:
        client.post("/api/modules/nap-lai", headers=QT)
        client.post("/api/modules/mod-1/cai", json={"cau_hinh": {"api_key": "khoa-test-1"}}, headers=QT)

        # Tắt m1/chat-flash
        client.put("/api/modules/mod-1/model", json={"tat": ["m1/chat-flash"]}, headers=QT)

        # Gọi model bật m1/chat-pro -> thành công 200
        r_on = client.post(
            "/v1/chat/completions",
            json={"model": "m1/chat-pro", "messages": [{"role": "user", "content": "hi"}]},
            headers=QT,
        )
        assert r_on.status_code == 200

        # Gọi model tắt m1/chat-flash -> 404 model_not_found kèm «đã tắt trong Hệ thống → AI»
        r_off = client.post(
            "/v1/chat/completions",
            json={"model": "m1/chat-flash", "messages": [{"role": "user", "content": "hi"}]},
            headers=QT,
        )
        assert r_off.status_code == 404
        j_off = r_off.json()
        assert j_off["error"]["code"] == "model_not_found"
        assert "đã tắt trong Hệ thống → AI" in j_off["error"]["message"]


def test_model_moi_mac_dinh_bat(moi_truong):
    # Khởi đầu với 2 model
    raw_models = [
        {"id": "m1/chat-1", "object": "model"},
        {"id": "m1/chat-2", "object": "model"},
    ]
    mod_dir = _tao_module_gia(moi_truong / "modules", "mod-1", "m1", raw_models)

    with TestClient(app) as client:
        client.post("/api/modules/nap-lai", headers=QT)
        client.post("/api/modules/mod-1/cai", json={"cau_hinh": {"api_key": "khoa-test-1"}}, headers=QT)

        # Tắt chat-1
        client.put("/api/modules/mod-1/model", json={"tat": ["m1/chat-1"]}, headers=QT)

        # Giờ module cập nhật danh sách model, thêm model mới m1/chat-3
        raw_models_moi = raw_models + [{"id": "m1/chat-3", "object": "model"}]
        (mod_dir / "module.py").write_text(
            f"""class Module:
    def __init__(self, m):
        self.manifest = m
        self.tien_to = m["tien_to"]
        self.raw_models = {raw_models_moi!r}
    def nap(self, cfg): pass
    async def cai(self, cfg): return {{"ok": True, "cau_hinh_luu": cfg}}
    async def chat(self, b, s): return 200, {{}}, b"{{}}"
    async def models(self): return [dict(x) for x in self.raw_models]
    async def suc_khoe(self): return {{"ok": True}}
    async def dung(self): pass
""",
            encoding="utf-8",
        )
        client.post("/api/modules/nap-lai", headers=QT)

        # GET /api/modules/mod-1/model: m1/chat-3 phải mặc định là BẬT
        r = client.get("/api/modules/mod-1/model", headers=QT)
        assert r.status_code == 200
        models_map = {m["id"]: m["bat"] for m in r.json()["models"]}
        assert models_map["m1/chat-1"] is False  # vẫn tắt
        assert models_map["m1/chat-2"] is True   # bật
        assert models_map["m1/chat-3"] is True   # model MỚI mặc định BẬT


def test_module_gia_nhom_la_hoat_dong_dung(moi_truong):
    # Module giả với mã nhóm lạ: "suy_luan_dac_biet" và "tao_nhac_3d"
    raw_models = [
        {"id": "mla/suy-luan-1", "object": "model", "kha_nang": ["suy_luan_dac_biet"]},
        {"id": "mla/suy-luan-2", "object": "model", "kha_nang": ["suy_luan_dac_biet"]},
        {"id": "mla/nhac-1", "object": "model", "kha_nang": ["tao_nhac_3d"]},
    ]
    _tao_module_gia(
        moi_truong / "modules",
        "mod-la",
        "mla",
        raw_models,
        cung_cap=["suy_luan_dac_biet", "tao_nhac_3d"],
    )

    with TestClient(app) as client:
        client.post("/api/modules/nap-lai", headers=QT)
        client.post("/api/modules/mod-la/cai", json={"cau_hinh": {"api_key": "khoa-test-1"}}, headers=QT)

        # GET /api/modules/mod-la/model
        r_get = client.get("/api/modules/mod-la/model", headers=QT)
        assert r_get.status_code == 200
        j_get = r_get.json()
        nhom_set = {m["nhom"] for m in j_get["models"]}
        assert "suy_luan_dac_biet" in nhom_set
        assert "tao_nhac_3d" in nhom_set

        # PUT: tắt suy-luan-1, đặt suy-luan-2 làm mặc định của nhóm lạ suy_luan_dac_biet
        r_put = client.put(
            "/api/modules/mod-la/model",
            json={
                "tat": ["mla/suy-luan-1"],
                "mac_dinh": {
                    "suy_luan_dac_biet": "mla/suy-luan-2",
                    "tao_nhac_3d": "mla/nhac-1",
                },
            },
            headers=QT,
        )
        assert r_put.status_code == 200
        j_put = r_put.json()
        assert j_put["tat"] == ["mla/suy-luan-1"]
        assert j_put["mac_dinh"] == {
            "suy_luan_dac_biet": "mla/suy-luan-2",
            "tao_nhac_3d": "mla/nhac-1",
        }

        # /v1/models không có suy-luan-1, và suy-luan-2 + nhac-1 có mac_dinh: True
        r_v1 = client.get("/v1/models", headers=QT)
        v1_data = r_v1.json()["data"]
        v1_ids = [m["id"] for m in v1_data]
        assert "mla/suy-luan-1" not in v1_ids
        assert "mla/suy-luan-2" in v1_ids
        assert "mla/nhac-1" in v1_ids
        m_sl2 = next(m for m in v1_data if m["id"] == "mla/suy-luan-2")
        assert m_sl2.get("mac_dinh") is True
        m_nhac = next(m for m in v1_data if m["id"] == "mla/nhac-1")
        assert m_nhac.get("mac_dinh") is True
