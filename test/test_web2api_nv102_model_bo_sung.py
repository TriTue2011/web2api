"""Test NV102-LÕI — bảng model do chủ máy khai bằng DỮ LIỆU (app/model_bo_sung.py).

Kiểm tra:
- Hàm kiem(), doc(), ghi(), duong_tep()
- Bảo mật đường dẫn (chống traversal, symlink trỏ ra ngoài, kiểm tra quyền 0600/0700)
- Xác thực API: 401 (không khoá), 403 (khoá khách), 404 (id lạ/sai khuôn), 413 (thân > 64KB), 400 (sai khuôn/JSON hỏng)
- Nhật ký quản trị ghi số mục, không lộ nội dung.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import kho, khoa_khach, nhat_ky, so_module
from app.main import app

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


def _tao_module_gia(goc: Path, id_: str) -> Path:
    thu_muc = goc / id_
    thu_muc.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": id_,
        "ten": f"Module {id_}",
        "mo_ta": "Module test model bo sung",
        "phien_ban": "1.0.0",
        "hang": "A",
        "loai": "ai",
        "giao_thuc": "openai",
        "tien_to": id_,
        "cung_cap": ["chat"],
        "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []},
    }
    (thu_muc / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    (thu_muc / "module.py").write_text(
        "class Module:\n"
        "    def __init__(self, m): pass\n"
        "    def nap(self, c): pass\n"
        "    async def cai(self, c): return {'ok': True}\n"
        "    async def models(self): return []\n"
        "    async def chat(self, b, s): return 200, {}, b'{}'\n"
        "    async def suc_khoe(self): return {'ok': True}\n"
        "    async def dung(self): pass\n",
        encoding="utf-8",
    )
    return thu_muc


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    monkeypatch.delenv("WEB2API_QT_IP_CHO_PHEP", raising=False)
    monkeypatch.delenv("WEB2API_TRUST_PROXY", raising=False)
    modules_dir = data_dir / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    khoa_khach.lam_moi_cache()
    return data_dir


# ── Kiểm tra khuôn kiem() ───────────────────────────────────────────────────


def test_kiem_model_bo_sung():
    from app import model_bo_sung

    # 1. Hợp lệ
    dl = {
        "models": [
            {"id": "grok-moi", "nhan": "Grok Mới"},
            {"id": "grok-khong-nhan"},
        ]
    }
    kq = model_bo_sung.kiem(dl)
    assert len(kq) == 2
    assert kq[0]["id"] == "grok-moi"
    assert kq[0]["nhan"] == "Grok Mới"
    assert kq[1]["id"] == "grok-khong-nhan"
    assert "nhan" not in kq[1] or kq[1]["nhan"] is None

    # 2. Gốc thừa khoá hoặc không phải dict
    with pytest.raises(ValueError):
        model_bo_sung.kiem("khong-phai-dict")
    with pytest.raises(ValueError):
        model_bo_sung.kiem({"models": [], "thua": 1})
    with pytest.raises(ValueError):
        model_bo_sung.kiem({})

    # 3. Models không phải list hoặc quá 100 mục
    with pytest.raises(ValueError):
        model_bo_sung.kiem({"models": "chuoi"})
    with pytest.raises(ValueError):
        model_bo_sung.kiem({"models": [{"id": f"m-{i}"} for i in range(101)]})

    # 4. Mục sai: thừa khoá, thiếu id, id rỗng, id chứa ký tự điều khiển, trùng id
    with pytest.raises(ValueError) as exc:
        model_bo_sung.kiem({"models": [{"id": "m1", "extra": 2}]})
    assert "0" in str(exc.value)

    with pytest.raises(ValueError):
        model_bo_sung.kiem({"models": [{}]})

    with pytest.raises(ValueError):
        model_bo_sung.kiem({"models": [{"id": "m1\nctrl"}]})

    with pytest.raises(ValueError):
        model_bo_sung.kiem({"models": [{"id": "m1"}, {"id": "m1"}]})

    with pytest.raises(ValueError):
        model_bo_sung.kiem({"models": [{"id": "m1", "nhan": 123}]})


# ── Kiểm tra đường dẫn duong_tep() và chống traversal ────────────────────────


def test_duong_tep_va_traversal(moi_truong):
    from app import model_bo_sung

    # Id hợp lệ
    p = model_bo_sung.duong_tep("mod-1")
    assert p.name == "mod-1.json"
    assert p.parent.name == "model_bo_sung"

    # Id sai khuôn
    for sai in ["..", "../etc", "a/b", "A_HOA", "m" * 45, ""]:
        with pytest.raises(ValueError):
            model_bo_sung.duong_tep(sai)

    # Symlink trỏ ra ngoài
    thu_muc_mb = moi_truong / "model_bo_sung"
    thu_muc_mb.mkdir(parents=True, exist_ok=True)
    link = thu_muc_mb / "mod-link.json"
    link.symlink_to(moi_truong / "ngoai.json")
    with pytest.raises(ValueError):
        model_bo_sung.duong_tep("mod-link")


# ── Kiểm tra doc() và ghi() ──────────────────────────────────────────────────


def test_doc_ghi_va_quyen_tep(moi_truong):
    from app import model_bo_sung

    # 1. Chưa có tệp -> doc trả []
    assert model_bo_sung.doc("chua-co") == []

    # 2. Ghi và kiểm tra quyền tệp 0600, thư mục 0700
    ds = [{"id": "model-1", "nhan": "Model 1"}]
    model_bo_sung.ghi("mod-test", ds)

    p = model_bo_sung.duong_tep("mod-test")
    assert p.is_file()
    che_do_tep = stat.S_IMODE(p.stat().st_mode)
    assert che_do_tep == 0o600, f"Quyền tệp phải là 0600, nhận: {oct(che_do_tep)}"

    che_do_tm = stat.S_IMODE(p.parent.stat().st_mode)
    assert che_do_tm == 0o700, f"Quyền thư mục phải là 0700, nhận: {oct(che_do_tm)}"

    # 3. Đọc lại
    doc_lai = model_bo_sung.doc("mod-test")
    assert len(doc_lai) == 1
    assert doc_lai[0]["id"] == "model-1"

    # 4. Tệp hỏng/JSON rác -> doc() trả [] không sập
    p.write_text("day la json loi {{{", encoding="utf-8")
    assert model_bo_sung.doc("mod-test") == []


# ── Kiểm tra API endpoints (GET / PUT /api/modules/{id}/model-bo-sung) ─────────


def test_api_model_bo_sung(moi_truong):
    _tao_module_gia(moi_truong / "modules", "mod-api")

    with TestClient(app) as c:
        # 1. Không khoá -> 401
        assert c.get("/api/modules/mod-api/model-bo-sung").status_code == 401
        assert c.put("/api/modules/mod-api/model-bo-sung", json={"models": []}).status_code == 401

        # 2. Khoá khách -> 403
        _, hd_khach = khoa_khach.tao_khoa(nhan="khach", pham_vi=["x"])
        auth_khach = {"Authorization": f"Bearer {hd_khach}"}
        assert c.get("/api/modules/mod-api/model-bo-sung", headers=auth_khach).status_code == 403
        assert c.put("/api/modules/mod-api/model-bo-sung", headers=auth_khach, json={"models": []}).status_code == 403

        # 3. Id lạ -> 404 (không phân biệt không tồn tại và sai khuôn)
        assert c.get("/api/modules/khong-co/model-bo-sung", headers=QT).status_code == 404
        assert c.get("/api/modules/..%2fmodel-bo-sung", headers=QT).status_code == 404
        assert c.get("/api/modules/..%2f..%2fetc/model-bo-sung", headers=QT).status_code == 404
        assert c.get("/api/modules/A_HOA/model-bo-sung", headers=QT).status_code == 404
        assert c.get(f"/api/modules/{'m'*45}/model-bo-sung", headers=QT).status_code == 404
        assert c.put("/api/modules/khong-co/model-bo-sung", headers=QT, json={"models": []}).status_code == 404
        assert c.put("/api/modules/..%2fmodel-bo-sung", headers=QT, json={"models": []}).status_code == 404
        assert c.put("/api/modules/A_HOA/model-bo-sung", headers=QT, json={"models": []}).status_code == 404
        assert c.put(f"/api/modules/{'m'*45}/model-bo-sung", headers=QT, json={"models": []}).status_code == 404

        # 4. Thân quá 64KB -> 413
        than_qua_lon = json.dumps({"models": [{"id": f"m{i}", "nhan": "x" * 70} for i in range(100)] * 10})
        assert len(than_qua_lon.encode("utf-8")) > 64 * 1024
        r_413 = c.put(
            "/api/modules/mod-api/model-bo-sung",
            headers={**QT, "Content-Type": "application/json"},
            content=than_qua_lon,
        )
        assert r_413.status_code == 413

        # 5. Thân sai khuôn -> 400
        r_400 = c.put("/api/modules/mod-api/model-bo-sung", headers=QT, json={"models": [{"id": ""}]})
        assert r_400.status_code == 400

        r_ko_json = c.put(
            "/api/modules/mod-api/model-bo-sung",
            headers={**QT, "Content-Type": "application/json"},
            content=b"khong phai json",
        )
        assert r_ko_json.status_code == 400

        # 6. PUT hợp lệ -> 200 và GET trả đúng
        du_lieu = {
            "models": [
                {"id": "mod-api/m1", "nhan": "Mô hình 1"},
                {"id": "mod-api/m2"},
            ]
        }
        r_put = c.put("/api/modules/mod-api/model-bo-sung", headers=QT, json=du_lieu)
        assert r_put.status_code == 200
        data_put = r_put.json()
        assert data_put["id"] == "mod-api"
        assert len(data_put["models"]) == 2
        assert data_put["toi_da"] == 100

        r_get = c.get("/api/modules/mod-api/model-bo-sung", headers=QT)
        assert r_get.status_code == 200
        data_get = r_get.json()
        assert data_get["id"] == "mod-api"
        assert len(data_get["models"]) == 2
        assert data_get["models"][0]["id"] == "mod-api/m1"

        # 7. Sau khi PUT bị từ chối (400 hoặc 413): tệp cũ và dữ liệu KHÔNG đổi
        from app import model_bo_sung

        p_tep = model_bo_sung.duong_tep("mod-api")
        noi_dung_truoc = p_tep.read_bytes()

        # 7a. PUT 400 (thân sai) -> tệp không đổi
        r_put_400 = c.put("/api/modules/mod-api/model-bo-sung", headers=QT, json={"models": [{"id": ""}]})
        assert r_put_400.status_code == 400
        assert p_tep.read_bytes() == noi_dung_truoc
        assert len(model_bo_sung.doc("mod-api")) == 2

        # 7b. PUT 413 (thân quá lớn) -> tệp không đổi
        r_put_413 = c.put(
            "/api/modules/mod-api/model-bo-sung",
            headers={**QT, "Content-Type": "application/json"},
            content=than_qua_lon,
        )
        assert r_put_413.status_code == 413
        assert p_tep.read_bytes() == noi_dung_truoc
        assert len(model_bo_sung.doc("mod-api")) == 2

        # 8. Nhật ký quản trị có ghi nhận
        dong_nk = nhat_ky.doc("quan_tri", gioi_han=10)
        assert any("mod-api" in (d.get("tom_tat") or "") and "2" in (d.get("tom_tat") or "") for d in dong_nk)
        # Không lộ chi tiết nội dung model trong tóm tắt nhật ký
        assert not any("Mô hình 1" in (d.get("tom_tat") or "") for d in dong_nk)
