"""NV21 — kiểm kiểu chặt cho trường số nguyên từ thân JSON của khách.

Lớp lỗi: mã đọc trường từ thân JSON rồi dùng thẳng như int mà không kiểm kiểu thật.
Bẫy: True là int trong Python; int("5") nhận chuỗi; int(1.5) cắt thập phân;
int(float("inf")) ném OverflowError (không phải ValueError) → 500 thay vì 400.

Mỗi test dưới đây ĐỎ trên mã cũ (500 hoặc nhận sai kiểu), XANH trên mã mới (400).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import quy_trinh
from app.kieu_chat import so_nguyen_chat
from app.loi_openai import LoiOpenAI
from app.main import app
from gia import ghi_thu_muc

KHOA_QT = "khoa-quan-tri-test-nv21-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}", "Content-Type": "application/json"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, data_dir


def _module_muc_luc():
    manifest = {
        "id": "ml-nv21", "ten": "ML NV21", "mo_ta": "test", "loai": "tri_thuc",
        "giao_thuc": "cuc_bo", "tien_to": "mlnv21", "cung_cap": ["tim_kiem"],
        "truong_cau_hinh": [],
        "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False,
                       "mang_ra": [], "ghi_chu": ""},
    }
    ma = '''
import json
class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
    def nap(self, cau_hinh):
        self.cau_hinh = cau_hinh
    async def cai(self, cau_hinh):
        return {"ok": True}
    async def suc_khoe(self):
        return {"ok": True}
    async def dung(self):
        pass
    async def muc_luc(self, yc):
        return {"cay": [], "do_sau": yc.get("do_sau")}
    async def tim_kiem(self, yc):
        return {"muc": [], "tong": 0}
'''
    return {"manifest.json": json.dumps(manifest).encode(), "module.py": ma.encode()}


def _nap_module_muc_luc(client, data_dir):
    ghi_thu_muc(Path(data_dir) / "modules" / "ml-nv21", _module_muc_luc())
    assert client.post("/api/modules/nap-lai", headers=QT).status_code == 200
    r = client.post("/api/modules/ml-nv21/cai", headers=QT, json={"cau_hinh": {}})
    assert r.status_code == 200


# ───────────────────────── đơn vị: so_nguyen_chat ─────────────────────────

@pytest.mark.parametrize("gia_tri", [True, False, 1.5, float("inf"), float("nan"), "5", "", None, [1], {"a": 1}])
def test_so_nguyen_chat_loai_kieu_long(gia_tri):
    """bool/float/inf/nan/chuỗi/None/list/dict → 400 (mã cũ: True→nhận, 1.5→cắt, inf→500)."""
    with pytest.raises(LoiOpenAI) as exc:
        so_nguyen_chat(gia_tri, "truong")
    assert exc.value.ma == 400


@pytest.mark.parametrize("gia_tri,toi_thieu,toi_da", [(0, 1, None), (-3, 0, None), (101, None, 100)])
def test_so_nguyen_chat_tran(gia_tri, toi_thieu, toi_da):
    """Ngoài trần → 400."""
    with pytest.raises(LoiOpenAI) as exc:
        so_nguyen_chat(gia_tri, "truong", toi_thieu=toi_thieu, toi_da=toi_da)
    assert exc.value.ma == 400


def test_so_nguyen_chat_nhan_dung():
    assert so_nguyen_chat(5, "truong") == 5
    assert so_nguyen_chat(0, "truong", toi_thieu=0) == 0
    assert so_nguyen_chat(10**18, "truong") == 10**18  # số rất lớn nhưng hữu hạn: nhận


# ───────────────────────── TestClient: tri_thuc do_sau ─────────────────────────

def test_muc_luc_do_sau_infinity_400(client):
    """do_sau=Infinity: mã cũ OverflowError → 500; mã mới → 400."""
    c, data_dir = client
    _nap_module_muc_luc(c, data_dir)
    r = c.post("/api/tim-kiem/muc-luc", headers=QT,
               content='{"nguon": "ml-nv21", "tai_lieu": "t.txt", "do_sau": Infinity}')
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("do_sau", ["abc", True, 1.5, 0, -2])
def test_muc_luc_do_sau_sai_kieu_400(client, do_sau):
    """do_sau chuỗi-lạ/bool/float/âm/0 → 400 (mã cũ: true→200 nhận là 1, 1.5→200 cắt)."""
    c, data_dir = client
    _nap_module_muc_luc(c, data_dir)
    r = c.post("/api/tim-kiem/muc-luc", headers=QT,
               json={"nguon": "ml-nv21", "tai_lieu": "t.txt", "do_sau": do_sau})
    assert r.status_code == 400


def test_muc_luc_do_sau_hop_le_200(client):
    c, data_dir = client
    _nap_module_muc_luc(c, data_dir)
    r = c.post("/api/tim-kiem/muc-luc", headers=QT,
               json={"nguon": "ml-nv21", "tai_lieu": "t.txt", "do_sau": 2})
    assert r.status_code == 200
    assert r.json()["do_sau"] == 2


# ───────────────────────── TestClient: quy_trinh qua /api/tim-kiem ─────────────────────────

def _pheu_cat(so_luong):
    return [{"ten": "t1", "kha_nang": "cat", "tham_so": {"so_luong": so_luong}}]


def test_pheu_cat_so_luong_chuoi_400(client):
    """pheu cat so_luong='abc': mã cũ ValueError → LoiQuyTrinh 500; mã mới → 400."""
    c, _ = client
    r = c.post("/api/tim-kiem", headers=QT, json={"chuoi": "x", "pheu": _pheu_cat("abc")})
    assert r.status_code == 400


def test_pheu_cat_so_luong_infinity_400(client):
    """pheu cat so_luong=Infinity: mã cũ OverflowError → 500; mã mới → 400."""
    c, _ = client
    r = c.post("/api/tim-kiem", headers=QT,
               content='{"chuoi": "x", "pheu": [{"ten": "t1", "kha_nang": "cat", "tham_so": {"so_luong": Infinity}}]}')
    assert r.status_code == 400


def test_pheu_cat_so_luong_bool_400(client):
    """pheu cat so_luong=true: mã cũ int(True)=1 lặng lẽ nhận; mã mới → 400."""
    c, _ = client
    r = c.post("/api/tim-kiem", headers=QT, json={"chuoi": "x", "pheu": _pheu_cat(True)})
    assert r.status_code == 400


def test_pheu_cat_so_luong_hop_le_200(client):
    c, _ = client
    r = c.post("/api/tim-kiem", headers=QT, json={"chuoi": "x", "pheu": _pheu_cat(5)})
    assert r.status_code == 200


def test_pheu_gop_k_sai_kieu_400(client):
    """pheu gop k='abc': mã cũ ValueError → 500; mã mới → 400."""
    c, _ = client
    pheu = [{"ten": "t1", "kha_nang": "gop", "tham_so": {"k": "abc"}}]
    r = c.post("/api/tim-kiem", headers=QT, json={"chuoi": "x", "pheu": pheu})
    assert r.status_code == 400


def test_pheu_gop_k_infinity_400(client):
    c, _ = client
    body = '{"chuoi": "x", "pheu": [{"ten": "t1", "kha_nang": "gop", "tham_so": {"k": Infinity}}]}'
    r = c.post("/api/tim-kiem", headers=QT, content=body)
    assert r.status_code == 400


def test_pheu_gop_k_so_rat_lon_tra_400(client):
    """L4: k = 10**400 không có toi_da sẽ gây OverflowError (500) ở float(k) — phải bị chặn thành 400."""
    c, _ = client
    pheu = [{"ten": "t1", "kha_nang": "gop", "tham_so": {"k": 10**400}}]
    r = c.post("/api/tim-kiem", headers=QT, json={"chuoi": "x", "pheu": pheu})
    assert r.status_code == 400
    assert "k" in r.text and "≤" in r.text


def test_toan_tu_gop_k_qua_lon_nem_loi_400():
    """L4: _thuc_hien_mot_tang với k = 10**400 ném LoiOpenAI 400 thay vì OverflowError 500."""
    import asyncio
    du_lieu = {"danh_sach_nguon": [[{"id": "a", "diem": 1.0}], [{"id": "b", "diem": 0.5}]]}
    with pytest.raises(LoiOpenAI) as exc_info:
        asyncio.run(quy_trinh._thuc_hien_mot_tang("gop", None, {"k": 10**400}, du_lieu, None))
    assert exc_info.value.ma == 400
    assert "k" in str(exc_info.value)


def test_cac_tham_so_so_nguyen_chat_khac_qua_lon_tra_400(client):
    """L4: mọi nơi gọi so_nguyen_chat với số 10**400 đều bị chặn thành 400."""
    c, data_dir = client
    _nap_module_muc_luc(c, data_dir)
    # 1. do_sau trong mục lục
    r = c.post("/api/tim-kiem/muc-luc", headers=QT, json={"nguon": "ml-nv21", "tai_lieu": "doc1", "do_sau": 10**400})
    assert r.status_code == 400
    assert "do_sau" in r.text

    # 2. so_luong trong toán tử cat
    pheu_cat = [{"ten": "t_cat", "kha_nang": "cat", "tham_so": {"n": 10**400}}]
    r = c.post("/api/tim-kiem", headers=QT, json={"chuoi": "x", "pheu": pheu_cat})
    assert r.status_code == 400
    assert "so_luong" in r.text

    # 3. tran_ky_tu trong gop_ngu_canh
    pheu_gop_nc = [{"ten": "t_nc", "kha_nang": "gop_ngu_canh", "tham_so": {"tran_ky_tu": 10**400}}]
    r = c.post("/api/tim-kiem", headers=QT, json={"chuoi": "x", "pheu": pheu_gop_nc})
    assert r.status_code == 400
    assert "tran_ky_tu" in r.text



# ───────────────────────── đơn vị: gemini-api durationSeconds ─────────────────────────

def _tai_module_gemini():
    import importlib.util
    goc = Path(__file__).resolve().parents[1] / "modules" / "gemini-api" / "module.py"
    ten = "gemini_api_nv21"
    spec = importlib.util.spec_from_file_location(ten, goc)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_gemini_duration_seconds_infinity_400():
    """durationSeconds=Infinity: mã cũ OverflowError → 500; mã mới → ValueError (400)."""
    mod = _tai_module_gemini()
    m = mod.Module({"tien_to": "gemini"})
    with pytest.raises(ValueError):
        m._than_veo({"model": "gemini/veo-3.1-fast", "prompt": "x", "durationSeconds": float("inf")})


def test_gemini_duration_seconds_chuoi_so_duoc_chap_nhan():
    """Chuỗi '8' được chấp nhận có chủ đích (OpenAI gửi chuỗi) — không đổi hành vi."""
    mod = _tai_module_gemini()
    m = mod.Module({"tien_to": "gemini"})
    _, than = m._than_veo({"model": "gemini/veo-3.1-fast", "prompt": "x", "durationSeconds": "8"})
    assert than["parameters"]["durationSeconds"] == 8


@pytest.mark.parametrize("gia_tri", [True, False, 4.5])
def test_gemini_duration_seconds_bool_float_loai(gia_tri):
    """bool (True→1) và float không nguyên (4.5→4): int() ép kiểu lặng lẽ là sai → 400."""
    mod = _tai_module_gemini()
    m = mod.Module({"tien_to": "gemini"})
    with pytest.raises(ValueError):
        m._than_veo({"model": "gemini/veo-3.1-fast", "prompt": "x", "durationSeconds": gia_tri})
