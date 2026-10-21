# -*- coding: utf-8 -*-
"""NV192 — test module anh-ai: port tạo/sửa ảnh AI từ c2a (commit d879b03).

Mọi HTTP đều GIẢ (conftest chặn Internet): Module(..., _http_post=gia).
Khóa trong test là chuỗi giả, không vào log (bộ lọc _ChanKhoaAnhAi).
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import logging
import sys
import time
from pathlib import Path

import pytest

THU_MUC_REPO = Path(__file__).resolve().parent.parent
THU_MUC_MODULE = THU_MUC_REPO / "modules" / "anh-ai"

PNG_1X1_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwAD"
    "hgGAWjR9awAAAABJRU5ErkJggg=="
)
PNG_1X1 = base64.b64decode(PNG_1X1_B64)
KHOA_GIA = "sk-test-anh-ai-gia-0123456789abcdef"


def _nap_module(_http_post=None, _goc_kiem_thu=None):
    """Nạp modules/anh-ai/module.py như lõi (gói duy nhất, import tương đối được)."""
    ten = "anh_ai_nv192_test"
    if ten in sys.modules:
        del sys.modules[ten]
    spec = importlib.util.spec_from_file_location(
        ten, THU_MUC_MODULE / "module.py",
        submodule_search_locations=[str(THU_MUC_MODULE)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten] = mod
    exec(compile((THU_MUC_MODULE / "module.py").read_bytes(),
                 str(THU_MUC_MODULE / "module.py"), "exec"), mod.__dict__)
    return mod


class PhanHoiGia:
    def __init__(self, status_code=200, json_data=None, text="", content=b""):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.content = content

    def json(self):
        if self._json is None:
            raise ValueError("không có JSON")
        return self._json

    def close(self):
        pass


def _phan_hoi_anh_png():
    return PhanHoiGia(200, json_data={"data": [{"b64_json": PNG_1X1_B64}]})


@pytest.fixture
def mod():
    return _nap_module()


@pytest.fixture
def thu_muc(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def cau_hinh_gemini():
    return {"nha_cung_cap": "gemini", "api_key": KHOA_GIA}


@pytest.fixture
def module_da_nap(mod, thu_muc, cau_hinh_gemini):
    m = mod.Module(_manifest(), _http_post=lambda *a, **k: _phan_hoi_anh_png(),
                   _goc_kiem_thu=str(thu_muc))
    m.nap(cau_hinh_gemini)
    yield m
    try:
        asyncio.get_event_loop().run_until_complete(m.dung())
    except RuntimeError:
        asyncio.run(m.dung())


def _manifest():
    return json.loads((THU_MUC_MODULE / "manifest.json").read_text("utf-8"))


def _chay(coro):
    return asyncio.run(coro)


# ── 1. Hợp đồng nạp / manifest ────────────────────────────────────────────

def test_nap_phai_dong_bo(mod):
    assert not asyncio.iscoroutinefunction(mod.Module.nap), \
        "nap phải ĐỒNG BỘ (lõi await-nap sẽ RuntimeError)"


def test_manifest_hop_le():
    mf = _manifest()
    assert mf["id"] == "anh-ai"
    assert mf["tien_to"] == "anh_ai"
    assert "anh" in mf.get("cung_cap", []) and "anh_sua" in mf.get("cung_cap", [])
    # 10 adapter (custom tuỳ khai)
    from anh_ai_nv192_test.bo_chuyen_doi import IMAGE_ADAPTERS
    assert len(IMAGE_ADAPTERS) == 10, f"phải có 10 adapter, có {len(IMAGE_ADAPTERS)}"


def test_danh_sach_model_co_gemini(module_da_nap):
    models = _chay(module_da_nap.models())
    ids = [m["id"] for m in models]
    assert any(i.startswith("anh_ai/gemini/") for i in ids)


# ── 2. cai(): kiểm cấu hình (SSRF, khoá, nhà cung cấp) ────────────────────

def test_cai_nha_cung_cap_la(mod, thu_muc):
    m = mod.Module(_manifest(), _goc_kiem_thu=str(thu_muc))
    kq = _chay(m.cai({"nha_cung_cap": "khong-co"}))
    assert kq["ok"] is False


def test_cai_gemini_thieu_khoa(mod, thu_muc):
    m = mod.Module(_manifest(), _goc_kiem_thu=str(thu_muc))
    kq = _chay(m.cai({"nha_cung_cap": "gemini"}))
    assert kq["ok"] is False and "api_key" in kq["loi"]


def test_cai_gemini_ok_khoa_duoc_che(mod, thu_muc):
    m = mod.Module(_manifest(), _goc_kiem_thu=str(thu_muc))
    kq = _chay(m.cai({"nha_cung_cap": "gemini", "api_key": KHOA_GIA}))
    assert kq["ok"] is True
    assert kq["cau_hinh_luu"]["nha_cung_cap"] == "gemini"
    che = " ".join(k["khoa"] for k in kq["ket_qua_tung_khoa"])
    assert KHOA_GIA not in che, "khóa nguyên văn không được lọt vào kết quả cai"


def test_cai_custom_bat_buoc_base_url(mod, thu_muc):
    m = mod.Module(_manifest(), _goc_kiem_thu=str(thu_muc))
    kq = _chay(m.cai({"nha_cung_cap": "custom:may-toi", "api_key": KHOA_GIA}))
    assert kq["ok"] is False and "base_url" in kq["loi"]


def test_cai_base_url_ssrf(mod, thu_muc):
    m = mod.Module(_manifest(), _goc_kiem_thu=str(thu_muc))
    kq = _chay(m.cai({"nha_cung_cap": "custom:may-toi", "api_key": KHOA_GIA,
                      "base_url": "http://169.254.169.254/latest/meta-data/"}))
    assert kq["ok"] is False and "SSRF" in kq["loi"], f"phải chặn IP metadata: {kq}"


def test_cai_sdwebui_duoc_thieu_khoa(mod, thu_muc):
    m = mod.Module(_manifest(), _goc_kiem_thu=str(thu_muc))
    kq = _chay(m.cai({"nha_cung_cap": "sdwebui"}))
    assert kq["ok"] is True  # local có thể không cần khoá


# ── 3. anh(): kiểm đầu vào ───────────────────────────────────────────────

def test_anh_thieu_prompt(module_da_nap):
    ma, _, noi_dung = _chay(module_da_nap.anh({"model": "anh_ai/gemini/x", "prompt": "  "}))
    assert ma == 400 and json.loads(noi_dung)["loi"] == "missing_prompt"


def test_anh_prompt_qua_dai(module_da_nap, mod):
    ma, _, noi_dung = _chay(
        module_da_nap.anh({"model": "anh_ai/gemini/x", "prompt": "a" * (mod.PROMPT_TOI_DA + 1)}))
    assert ma == 400


def test_anh_model_sai_dang(module_da_nap):
    for model in ["gemini", "anh_ai/", "anh_ai//x", "../etc/passwd", "anh_ai/gemini/a b"]:
        ma, _, _ = _chay(module_da_nap.anh({"model": model, "prompt": "mèo"}))
        assert ma == 400, f"model {model!r} phải bị từ chối"


def test_anh_model_idor(module_da_nap):
    # IDOR/traversal: tên model không được thoát ra khỏi không gian anh_ai/
    ma, _, noi_dung = _chay(
        module_da_nap.anh({"model": "anh_ai/gemini/../../admin", "prompt": "mèo"}))
    assert ma == 400, json.loads(noi_dung)


def test_anh_thanh_cong(module_da_nap):
    ma, headers, noi_dung = _chay(module_da_nap.anh(
        {"model": "anh_ai/gemini/imagen-3.0-generate-001", "prompt": "con mèo", "n": 1}))
    assert ma == 200, noi_dung[:200]
    kq = json.loads(noi_dung)
    assert kq["data"] and base64.b64decode(kq["data"][0]["b64_json"]) == PNG_1X1
    # tên tệp do module sinh (uuid), không từ nhà cung cấp
    tep = kq["data"][0]["tep"]
    assert tep.endswith(".png") and len(tep) < 45
    assert (module_da_nap._thu_muc_anh / tep).read_bytes() == PNG_1X1


def test_anh_xss_prompt_duoc_truyen_nguyen(module_da_nap):
    # prompt không phải JS để thực thi — chỉ là chuỗi gửi nhà cung cấp; kiểm nó
    # không làm vỡ JSON phản hồi
    ma, _, noi_dung = _chay(module_da_nap.anh(
        {"model": "anh_ai/gemini/x", "prompt": '<script>alert(1)</script>"\'&'}))
    assert ma == 200
    json.loads(noi_dung)


# ── 4. Xoay khoá (401/403/400/429) ───────────────────────────────────────

def test_xoay_khoa_401(mod, thu_muc):
    goi = []

    def gia(url, *, headers, json_body, timeout):
        goi.append(dict(headers))
        if len(goi) == 1:
            return PhanHoiGia(401, text="Unauthorized")
        return _phan_hoi_anh_png()

    m = mod.Module(_manifest(), _http_post=gia, _goc_kiem_thu=str(thu_muc))
    m.nap({"nha_cung_cap": "gemini", "api_keys": ["khoa-mot-gia", "khoa-hai-gia"]})
    ma, _, noi_dung = _chay(m.anh({"model": "anh_ai/gemini/x", "prompt": "mèo"}))
    assert ma == 200, noi_dung[:200]
    assert len(goi) == 2, "401 phải xoay sang khoá thứ hai"


def test_het_khoa_403(module_da_nap, mod):
    goi = []

    def gia(url, *, headers, json_body, timeout):
        goi.append(1)
        return PhanHoiGia(403, text="Forbidden")

    module_da_nap._http_post = gia
    ma, _, noi_dung = _chay(module_da_nap.anh({"model": "anh_ai/gemini/x", "prompt": "mèo"}))
    assert ma == 502, "403 mọi khoá phải trả 502 upstream"
    assert goi, "phải thử gọi"


def test_nha_loi_429(module_da_nap):
    module_da_nap._http_post = lambda *a, **k: PhanHoiGia(429, text="rate limit")
    ma, _, noi_dung = _chay(module_da_nap.anh({"model": "anh_ai/gemini/x", "prompt": "mèo"}))
    assert ma == 502 and "429" in json.loads(noi_dung)["thong_bao"]


# ── 5. Khoá không vào log ─────────────────────────────────────────────────

def test_khoa_khong_vao_log(mod):
    lg = logging.getLogger("httpx")
    rec = logging.LogRecord("httpx", logging.INFO, __file__, 1,
                            "Authorization: Bearer " + KHOA_GIA, (), None)
    for f in lg.filters:
        if getattr(f, "_anh_ai_loc", False):
            assert not f.filter(rec), "dòng chứa khoá phải bị chặn"
            return
    pytest.fail("bộ lọc log ảnh-ai chưa cài trên logger httpx")


def test_suc_khoe_che_khoa(module_da_nap):
    sk = _chay(module_da_nap.suc_khoe())
    assert KHOA_GIA not in json.dumps(sk), "sức khoẻ không được lộ khoá"


# ── 6. Hàng rào ảnh (port ca thật từ test_image_guard của c2a) ─────────────

def _kiem_anh():
    from anh_ai_nv192_test.kiem_anh import kiem_anh, ImageRejected
    return kiem_anh, ImageRejected


def test_hang_rao_anh_png_hop_le():
    kiem_anh, _ = _kiem_anh()
    assert kiem_anh(PNG_1X1) == "image/png"


def test_hang_rao_bom_giai_nen():
    kiem_anh, ImageRejected = _kiem_anh()
    with pytest.raises(ImageRejected):
        kiem_anh(b"\x89PNG\r\n\x1a\n" + b"\x00" * (21 * 1024 * 1024), ten="bom")


def test_hang_rao_magic_sai():
    kiem_anh, ImageRejected = _kiem_anh()
    with pytest.raises(ImageRejected):
        kiem_anh(b"GIF89a" + b"\x00" * 100 + b"<!doctype html>script")
    with pytest.raises(ImageRejected):
        kiem_anh(b"\xff\xd8\xff" + b"\x00" * 10 + b"<html>")  # JPEG nhưng rỗng nội dung?


def test_hang_rao_rong():
    kiem_anh, ImageRejected = _kiem_anh()
    with pytest.raises(ImageRejected):
        kiem_anh(b"")


def test_anh_nha_tra_anh_xau_bi_loai(module_da_nap):
    module_da_nap._http_post = lambda *a, **k: PhanHoiGia(
        200, json_data={"data": [{"b64_json": base64.b64encode(b"khong-phai-anh").decode()}]})
    ma, _, noi_dung = _chay(module_da_nap.anh({"model": "anh_ai/gemini/x", "prompt": "mèo"}))
    assert ma == 502, "ảnh trả về không qua hàng rào phải bị loại hết"


# ── 7. anh_sua(): đường trực tiếp ─────────────────────────────────────────

class _Tep:
    def __init__(self, noi_dung, ten_tep="image.png", content_type="image/png"):
        self.noi_dung = noi_dung
        self.ten_tep = ten_tep
        self.content_type = content_type


class _Yc:
    def __init__(self, truong, tep=None):
        self.truong = truong
        self._tep = tep or {}

    def tep_ten(self, ten):
        return self._tep.get(ten, [])


def test_anh_sua_nha_khong_ho_tro(mod, thu_muc):
    m = mod.Module(_manifest(), _http_post=lambda *a, **k: _phan_hoi_anh_png(),
                   _goc_kiem_thu=str(thu_muc))
    m.nap({"nha_cung_cap": "cloudflare_ai", "api_key": KHOA_GIA})
    yc = _Yc({"model": "anh_ai/cloudflare_ai/x", "prompt": "sửa"},
             {"image": [_Tep(PNG_1X1)]})
    ma, _, noi_dung = _chay(m.anh_sua(yc))
    assert ma == 400 and json.loads(noi_dung)["loi"] == "capability_not_supported"


def test_anh_sua_thieu_tep(module_da_nap):
    yc = _Yc({"model": "anh_ai/gemini/x", "prompt": "sửa"})
    ma, _, noi_dung = _chay(module_da_nap.anh_sua(yc))
    assert ma == 400 and json.loads(noi_dung)["loi"] == "missing_file"


def test_anh_sua_tep_xau(module_da_nap):
    yc = _Yc({"model": "anh_ai/gemini/x", "prompt": "sửa"},
             {"image": [_Tep(b"khong-phai-anh")]})
    ma, _, noi_dung = _chay(module_da_nap.anh_sua(yc))
    assert ma == 400 and json.loads(noi_dung)["loi"] == "invalid_image"


def test_anh_sua_thanh_cong(module_da_nap):
    ghi = {}

    def gia(url, *, headers, json_body, timeout):
        ghi["body"] = json_body
        return _phan_hoi_anh_png()

    module_da_nap._http_post = gia
    yc = _Yc({"model": "anh_ai/gemini/x", "prompt": "đổi nền"},
             {"image": [_Tep(PNG_1X1)]})
    ma, _, noi_dung = _chay(module_da_nap.anh_sua(yc))
    assert ma == 200, noi_dung[:200]
    # ảnh đầu vào phải tới được adapter (bug NV192: dict bị adapter bỏ qua)
    parts = ghi["body"]["contents"][0]["parts"]
    assert any(p.get("inlineData", {}).get("data") == PNG_1X1_B64 for p in parts), \
        "ảnh đầu vào phải được đưa vào thân gọi nhà cung cấp"


# ── 8. Hàng đợi qua thuc_the/thuc_hien (chuẩn C2) ──────────────────────────

def _doi_tac_vu(m, ma_tv, han=30):
    het = time.time() + han
    while time.time() < het:
        tv = _chay(m.thuc_hien(f"{m.tien_to}.hang_doi_anh", "lay",
                               {"ma": ma_tv, "danh_tinh": "kiem-thu"}))
        t = tv.get("tac_vu") or {}
        if t.get("status") in ("done", "failed"):
            return t
        time.sleep(0.1)
    raise AssertionError(f"tác vụ {ma_tv} không xong trong {han}s")


def test_thuc_the_hang_doi(module_da_nap):
    tt = _chay(module_da_nap.thuc_the())
    assert len(tt) == 1 and tt[0]["id"] == "anh_ai.hang_doi_anh"
    hd = {t["hanh_dong"] for t in tt[0]["tinh_nang"]}
    assert {"gui_tao", "gui_sua", "lay", "liet_ke", "huy", "don_dep"} <= hd


def test_trang_thai_san_sang(module_da_nap):
    tt = _chay(module_da_nap.trang_thai(["anh_ai.hang_doi_anh"]))
    assert tt["anh_ai.hang_doi_anh"]["gia_tri"] == "san_sang"


def test_hang_doi_gui_tao_xong(module_da_nap):
    kq = _chay(module_da_nap.thuc_hien("anh_ai.hang_doi_anh", "gui_tao",
                                      {"ma": "tv-tao-1", "prompt": "mèo",
                                       "model": "anh_ai/gemini/x",
                                       "danh_tinh": "kiem-thu"}))
    assert kq["ok"] is True
    t = _doi_tac_vu(module_da_nap, "tv-tao-1")
    assert t["status"] == "done" and t["data"], t.get("error")
    assert base64.b64decode(t["data"][0]["b64_json"]) == PNG_1X1


def test_hang_doi_gui_sua_giu_anh_vao(module_da_nap):
    ghi = {}

    def gia(url, *, headers, json_body, timeout):
        ghi["body"] = json_body
        return _phan_hoi_anh_png()

    module_da_nap._http_post = gia
    kq = _chay(module_da_nap.thuc_hien("anh_ai.hang_doi_anh", "gui_sua",
                                      {"ma": "tv-sua-1", "prompt": "đổi nền",
                                       "model": "anh_ai/gemini/x",
                                       "anh_b64": [PNG_1X1_B64],
                                       "danh_tinh": "kiem-thu"}))
    assert kq["ok"] is True, kq
    t = _doi_tac_vu(module_da_nap, "tv-sua-1")
    assert t["status"] == "done", t.get("error")
    parts = ghi["body"]["contents"][0]["parts"]
    assert any(p.get("inlineData", {}).get("data") == PNG_1X1_B64 for p in parts), \
        "tác vụ sửa qua hàng đợi phải giữ ảnh đầu vào (bug NV192)"


def test_hang_doi_id_pham_vi_chu(module_da_nap):
    # mã tác vụ chỉ là khoá trong bộ nhớ, phân tách theo chủ — không thoát phạm vi
    _chay(module_da_nap.thuc_hien("anh_ai.hang_doi_anh", "gui_tao",
                                 {"ma": "../../x", "prompt": "mèo",
                                  "model": "anh_ai/gemini/x", "danh_tinh": "nguoi-a"}))
    kq = _chay(module_da_nap.thuc_hien("anh_ai.hang_doi_anh", "lay",
                                      {"ma": "../../x", "danh_tinh": "nguoi-b"}))
    assert kq["ok"] is False and kq["tac_vu"] is None, \
        "tác vụ của người A không được thấy bởi người B (phân tách chủ)"


def test_hang_doi_huy_va_liet_ke(module_da_nap):
    _chay(module_da_nap.thuc_hien("anh_ai.hang_doi_anh", "gui_tao",
                                 {"ma": "tv-huy-1", "prompt": "mèo",
                                  "model": "anh_ai/gemini/x", "danh_tinh": "kiem-thu"}))
    ds = _chay(module_da_nap.thuc_hien("anh_ai.hang_doi_anh", "liet_ke",
                                      {"danh_tinh": "kiem-thu"}))
    assert ds["ok"] and any(t["id"] == "tv-huy-1" for t in ds["tac_vu"])


def test_hang_doi_hanh_dong_la(module_da_nap):
    kq = _chay(module_da_nap.thuc_hien("anh_ai.hang_doi_anh", "xoa_het", {}))
    assert kq["ok"] is False


# ── 9. QUA LÕI THẬT: TestClient POST /v1/images/generations ────────────────

KHOA_QT = "khoa-quan-tri-test-nv192-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


@pytest.fixture
def app_that(tmp_path, monkeypatch):
    from app import kho, so_module
    from app.main import app
    from app.so_module import SoModule
    from gia import ghi_thu_muc

    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh")

    tep = {}
    for p in THU_MUC_MODULE.iterdir():
        if p.name in ("__pycache__",):
            continue
        if p.is_file():
            tep[p.name] = p.read_bytes()
    for sub in ("bo_chuyen_doi",):
        for p in (THU_MUC_MODULE / sub).iterdir():
            if p.is_file() and p.suffix == ".py":
                tep[f"{sub}/{p.name}"] = p.read_bytes()
    ghi_thu_muc(data_dir / "modules" / "anh-ai", tep)

    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        r = client.post("/api/modules/nap-lai", headers=QT)
        assert r.status_code == 200, r.text
        r = client.post("/api/modules/anh-ai/cai", headers=QT,
                        json={"cau_hinh": {"nha_cung_cap": "gemini",
                                          "api_key": KHOA_GIA}})
        assert r.status_code == 200, r.text
        mod_chay = so.dang_chay["anh-ai"]
        mod_chay._http_post = lambda *a, **k: _phan_hoi_anh_png()
        yield client, mod_chay


def test_qua_loi_that_tao_anh(app_that):
    client, _ = app_that
    r = client.post("/v1/images/generations", headers=QT, json={
        "model": "anh_ai/gemini/imagen-3.0-generate-001",
        "prompt": "con mèo qua lõi thật", "n": 1})
    assert r.status_code == 200, r.text
    kq = r.json()
    assert base64.b64decode(kq["data"][0]["b64_json"]) == PNG_1X1


def test_qua_loi_that_model_module_khac_khong_lan(app_that):
    client, _ = app_that
    r = client.post("/v1/images/generations", headers=QT, json={
        "model": "module_khac/gemini/x", "prompt": "mèo"})
    assert r.status_code in (400, 404), "model không thuộc anh_ai không được đi qua module này"


def test_qua_loi_that_thieu_prompt_400(app_that):
    client, _ = app_that
    r = client.post("/v1/images/generations", headers=QT, json={
        "model": "anh_ai/gemini/x"})
    assert r.status_code == 400
