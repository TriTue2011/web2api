"""Test NV109 — soát bảo mật ĐỘC LẬP phần NV102 (tự lấy model) + NV103/NV105.

KHÔNG sửa mã sản phẩm: chỉ test + báo cáo.

Phần A — RED-TEAM (đóng vai kẻ tấn công, TestClient, không Internet):
 A.1 leo thang quyền trên các đường mới của NV102
 A.2 IDOR / đường dẫn (traversal, symlink, id lạ)
 A.3 từ chối dịch vụ (trần thân, JSON độc, dồn dập, single-flight, khoá)
 A.4 dữ liệu độc từ nhà cung cấp (id/nhan chứa HTML, __proto__, rất dài, trùng)
 A.5 logic sổ (tắt/bật, vắng 2 chu kỳ, đồng thời)
 A.6 rò rỉ (khoá vào log / phản hồi / sổ / nhật ký)

Quy ước: test khẳng định hành vi ĐÚNG. Ca nào ĐỎ hôm nay là lỗi thật →
đánh dấu @pytest.mark.xfail(strict=True, reason="NV109-xx: ...").
Ca xanh là test bảo vệ.

Chuỗi khoá trong test là chuỗi GIẢ, đã có trong allowlist .gitleaks.toml.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import kho, khoa_khach, nhat_ky, so_module
from app.main import app

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


def _tao_module_gia(goc: Path, id_: str, models: list[dict] | None = None,
                    co_lam_moi: bool = False) -> Path:
    """Module giả tối thiểu: nap/cai/chat/models/suc_khoe/dung (+lam_moi_model tuỳ chọn)."""
    thu_muc = goc / id_
    thu_muc.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": id_, "ten": f"Module {id_}", "mo_ta": "Module gia NV109",
        "phien_ban": "1.0.0", "hang": "A", "loai": "ai", "giao_thuc": "openai",
        "tien_to": id_, "cung_cap": ["chat"], "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []},
    }
    (thu_muc / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    lam_moi_code = ""
    if co_lam_moi:
        lam_moi_code = (
            "\n    async def lam_moi_model(self):\n"
            "        self.da_lam_moi = True\n"
        )
    (thu_muc / "module.py").write_text(
        "class Module:\n"
        "    def __init__(self, m):\n"
        "        self.manifest = m\n"
        "        self.tien_to = m['tien_to']\n"
        f"        self.raw_models = {models!r}\n"
        "        self.da_lam_moi = False\n"
        "    def nap(self, c): pass\n"
        "    async def cai(self, c): return {'ok': True, 'cau_hinh_luu': c}\n"
        "    async def chat(self, b, s): return 200, {}, b'{}'\n"
        "    async def models(self): return [dict(x) for x in self.raw_models]\n"
        "    async def suc_khoe(self): return {'ok': True}\n"
        "    async def dung(self): pass\n"
        f"{lam_moi_code}",
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


def _khoa_khach(pham_vi: list[str]) -> dict:
    _k, hd = khoa_khach.tao_khoa(nhan="khach-nv109", pham_vi=pham_vi)
    return {"Authorization": f"Bearer {hd}"}


def _dung_module(moi_truong: Path, id_: str, models=None, co_lam_moi=False):
    """Tạo tệp module giả TRƯỚC khi TestClient khởi động (catalog quét lúc startup)."""
    _tao_module_gia(moi_truong / "modules", id_, models=models or [], co_lam_moi=co_lam_moi)


def _cai_module(c, id_: str):
    r = c.post(f"/api/modules/{id_}/cai", headers=QT, json={"cau_hinh": {}})
    assert r.status_code == 200, r.text
    return r


# ── A.1: leo thang quyền ─────────────────────────────────────────────────────


def test_a1_khong_khoa_401(moi_truong):
    """Không khoá → 401 trên cả 4 đường mới của NV102."""
    with TestClient(app) as c:
        assert c.get("/api/modules/mod-x/model-so").status_code == 401
        assert c.post("/api/modules/mod-x/lam-moi-model").status_code == 401
        assert c.get("/api/modules/mod-x/model-bo-sung").status_code == 401
        assert c.put("/api/modules/mod-x/model-bo-sung", json={"models": []}).status_code == 401


def test_a1_khoa_khach_hep_403(moi_truong):
    """Khoá khách phạm vi hẹp → 403 trên cả 4 đường mới."""
    with TestClient(app) as c:
        auth = _khoa_khach(["chat"])
        assert c.get("/api/modules/mod-x/model-so", headers=auth).status_code == 403
        assert c.post("/api/modules/mod-x/lam-moi-model", headers=auth).status_code == 403
        assert c.get("/api/modules/mod-x/model-bo-sung", headers=auth).status_code == 403
        r = c.put("/api/modules/mod-x/model-bo-sung", headers=auth, json={"models": []})
        assert r.status_code == 403


def test_a1_khoa_khach_sao_403(moi_truong):
    """Khoá khách phạm vi `*` NHƯNG không phải quản trị → vẫn 403."""
    with TestClient(app) as c:
        auth = _khoa_khach(["*"])
        assert c.get("/api/modules/mod-x/model-so", headers=auth).status_code == 403
        assert c.post("/api/modules/mod-x/lam-moi-model", headers=auth).status_code == 403
        assert c.get("/api/modules/mod-x/model-bo-sung", headers=auth).status_code == 403
        r = c.put("/api/modules/mod-x/model-bo-sung", headers=auth, json={"models": []})
        assert r.status_code == 403


def test_a1_router_moi_dung_can_quan_tri():
    """Tĩnh: cả 2 router mới (lam_moi_model, model_bo_sung) dùng can_quan_tri,
    không đường nào dùng xac_thuc_v1 (khách lọt)."""
    import re
    for tep in ("app/lam_moi_model.py", "app/model_bo_sung.py"):
        src = Path(tep).read_text(encoding="utf-8")
        assert "can_quan_tri" in src, tep
        assert "xac_thuc_v1" not in src, f"{tep} dùng xac_thuc_v1!"
    # mọi @router.get/post/put trong 2 file đều nằm dưới router đã gắn can_quan_tri
    for tep in ("app/lam_moi_model.py", "app/model_bo_sung.py"):
        src = Path(tep).read_text(encoding="utf-8")
        routes = re.findall(r"@router\.(get|post|put)\(", src)
        assert routes, tep
        assert "dependencies=[Depends(can_quan_tri)]" in src, tep


def test_a1_quan_tri_duoc_200(moi_truong):
    """Quản trị gọi được (kênh mở đúng người)."""
    _dung_module(moi_truong, "mod-qt")
    with TestClient(app) as c:
        _cai_module(c, "mod-qt")
        r = c.get("/api/modules/mod-qt/model-so", headers=QT)
        assert r.status_code == 200, r.text
        r = c.get("/api/modules/mod-qt/model-bo-sung", headers=QT)
        assert r.status_code == 200, r.text


# ── A.2: IDOR / đường dẫn ────────────────────────────────────────────────────


@pytest.mark.parametrize("duong", [
    "/api/modules/../x/model-so",
    "/api/modules/..%2f..%2fx/model-so",
    "/api/modules/%2e%2e%2fetc/model-so",
    "/api/modules/" + "a" * 100 + "/model-so",
    "/api/modules/mödül/model-so",
    "/api/modules/MOD-X/model-so",
    "/api/modules//model-so",
])
def test_a2_id_module_la_404(moi_truong, duong):
    """Id module dạng traversal / quá dài / unicode / hoa → 404, không 500."""
    with TestClient(app) as c:
        r = c.get(duong, headers=QT)
        assert r.status_code in (404, 307), (duong, r.status_code)


def test_a2_duong_tep_chan_traversal(moi_truong):
    """duong_tep() từ chối mọi dạng traversal trực tiếp."""
    from app import model_bo_sung
    for xau in ("../x", "..%2f", "a/b", "", ".", "A" * 50, "möd"):
        with pytest.raises(ValueError):
            model_bo_sung.duong_tep(xau)


def test_a2_symlink_tro_ra_ngoai_bi_chan(moi_truong, tmp_path):
    """Symlink đặt sẵn model_bo_sung/<id>.json → trỏ ra ngoài: doc/ghi từ chối."""
    from app import model_bo_sung
    ngoai = tmp_path / "ngoai.json"
    ngoai.write_text('{"models": []}', encoding="utf-8")
    thu_muc = moi_truong / "model_bo_sung"
    thu_muc.mkdir(parents=True, exist_ok=True)
    (thu_muc / "mod-sym.json").symlink_to(ngoai)
    # doc() nuốt ngoại lệ → [], nhưng tệp ngoài KHÔNG được đọc/ghi qua symlink
    assert model_bo_sung.doc("mod-sym") == []
    with pytest.raises(ValueError):
        model_bo_sung.ghi("mod-sym", [])
    assert json.loads(ngoai.read_text(encoding="utf-8")) == {"models": []}


def test_a2_khong_ghi_de_tep_module_khac(moi_truong):
    """ghi('mod-a') không bao giờ chạm tệp của mod-b."""
    from app import model_bo_sung
    model_bo_sung.ghi("mod-a", [{"id": "m1"}])
    model_bo_sung.ghi("mod-b", [{"id": "m2"}])
    pa = model_bo_sung.duong_tep("mod-a")
    pb = model_bo_sung.duong_tep("mod-b")
    assert pa != pb
    assert [m["id"] for m in model_bo_sung.doc("mod-a")] == ["m1"]
    assert [m["id"] for m in model_bo_sung.doc("mod-b")] == ["m2"]


def test_a2_api_module_chua_cai_404(moi_truong):
    """Đọc/ghi bảng bổ sung của module CHƯA CÀI qua API → 404 (không tạo tệp)."""
    with TestClient(app) as c:
        assert c.get("/api/modules/chua-cai/model-bo-sung", headers=QT).status_code == 404
        r = c.put("/api/modules/chua-cai/model-bo-sung", headers=QT, json={"models": []})
        assert r.status_code == 404
        assert not (moi_truong / "model_bo_sung" / "chua-cai.json").exists()


def test_a2_proto_id_an_toan(moi_truong):
    """id '__proto__' qua kiem(): hợp lệ nhưng lưu trong dict Python thuần — không
    prototype-pollution; JSON round-trip nguyên vẹn."""
    from app import model_bo_sung
    ds = model_bo_sung.ghi("mod-p", [{"id": "__proto__", "nhan": "P"}])
    assert ds[0]["id"] == "__proto__"
    doc_lai = model_bo_sung.doc("mod-p")
    assert doc_lai[0]["id"] == "__proto__"
    assert {}.get("__proto__", "khong-nhiem") == "khong-nhiem"


# ── A.3: từ chối dịch vụ ─────────────────────────────────────────────────────


def test_a3_than_64kb_bien(moi_truong):
    """PUT model-bo-sung: thân ≤ 64KB qua, > 64KB → 413."""
    from app import model_bo_sung
    _dung_module(moi_truong, "mod-dos")
    with TestClient(app) as c:
        _cai_module(c, "mod-dos")
        # dựng thân vừa dưới trần
        nhan = "n" * 40
        ds = [{"id": f"m{i:03d}", "nhan": nhan} for i in range(60)]
        than = json.dumps({"models": ds}).encode("utf-8")
        assert len(than) <= model_bo_sung.TOI_DA_THAN_BYTE
        r = c.put("/api/modules/mod-dos/model-bo-sung",
                  content=than, headers={**QT, "Content-Type": "application/json"})
        assert r.status_code == 200, r.text[:200]
        # vượt trần → 413 (không đọc thân)
        lon = b'{"models": [' + b'{"id":"x"},' * 20000 + b'{"id":"y"}]}'
        assert len(lon) > model_bo_sung.TOI_DA_THAN_BYTE
        r = c.put("/api/modules/mod-dos/model-bo-sung",
                  content=lon, headers={**QT, "Content-Type": "application/json"})
        assert r.status_code == 413, r.status_code


def test_a3_json_long_sau_bi_chan(moi_truong):
    """JSON lồng 10000 tầng → 400 (không RecursionError 500, không treo)."""
    _dung_module(moi_truong, "mod-dos2")
    with TestClient(app) as c:
        _cai_module(c, "mod-dos2")
        sau = '{"models": ' + '{"a":' * 10000 + '1' + '}' * 10000 + '}'
        r = c.put("/api/modules/mod-dos2/model-bo-sung",
                  content=sau.encode(), headers={**QT, "Content-Type": "application/json"})
        assert r.status_code in (400, 413), r.status_code


def test_a3_nan_infinity_bi_chan(moi_truong):
    """NaN / Infinity trong thân → 400 (parse_constant từ chối)."""
    _dung_module(moi_truong, "mod-dos3")
    with TestClient(app) as c:
        _cai_module(c, "mod-dos3")
        for xau in ('{"models": [{"id": "m1", "nhan": NaN}]}',
                    '{"models": [{"id": "m1", "nhan": Infinity}]}'):
            r = c.put("/api/modules/mod-dos3/model-bo-sung",
                      content=xau.encode(), headers={**QT, "Content-Type": "application/json"})
            assert r.status_code == 400, (xau, r.status_code)


def test_a3_khoa_trung_so_muc_vuot_tran(moi_truong):
    """Khoá JSON trùng → json lấy cái cuối, vẫn qua kiem(); >100 mục → 400."""
    _dung_module(moi_truong, "mod-dos4")
    with TestClient(app) as c:
        _cai_module(c, "mod-dos4")
        r = c.put("/api/modules/mod-dos4/model-bo-sung", headers=QT,
                  json={"models": [{"id": "m1"}, {"id": "m1", "nhan": "hai"}]})
        assert r.status_code == 400  # id trùng trong danh sách
        ds = [{"id": f"m{i}"} for i in range(101)]
        r = c.put("/api/modules/mod-dos4/model-bo-sung", headers=QT, json={"models": ds})
        assert r.status_code == 400
        ds = [{"id": f"m{i}"} for i in range(100)]
        r = c.put("/api/modules/mod-dos4/model-bo-sung", headers=QT, json={"models": ds})
        assert r.status_code == 200


def test_a3_single_flight_dong_thoi(moi_truong):
    """Hai lượt lam_moi_mot đồng thời trên cùng vòng: một 'dang_chay', sổ không hỏng."""
    from app import lam_moi_model as lmm

    async def _chay():
        goi = {"dem": 0}

        class ModGia:
            async def lam_moi_model(self):
                goi["dem"] += 1
                await asyncio.sleep(0.2)

            async def models(self):
                return [{"id": "x1", "nhan": "X"}]

        vong = lmm.VongLamMoiModel(lambda: {"mod-dt": ModGia()})
        kq = await asyncio.gather(vong.lam_moi_mot("mod-dt"), vong.lam_moi_mot("mod-dt"))
        return kq, goi["dem"]

    (kq1, kq2), dem = asyncio.run(_chay())
    ket_qua = {kq1["ket_qua"], kq2["ket_qua"]}
    assert ket_qua == {"ok", "dang_chay"}, (kq1, kq2)
    assert dem == 1  # upstream chỉ bị gọi 1 lần


def test_a3_tac_vu_nen_khong_phinh(moi_truong):
    """sau_khi_cai tạo task nền có done_callback loại khỏi _tac_vu_nen → không phình."""
    import asyncio as _aio
    from app import lam_moi_model as lmm

    async def _chay(c):
        truoc = len(lmm._tac_vu_nen)
        for _ in range(5):
            lmm.sau_khi_cai(c.app, "mod-nen", {"ok": True})
        await _aio.sleep(0.3)
        return truoc, len(lmm._tac_vu_nen)

    _dung_module(moi_truong, "mod-nen", co_lam_moi=True)
    with TestClient(app) as c:
        _cai_module(c, "mod-nen")
        truoc, sau = asyncio.run(_chay(c))
        assert sau <= truoc + 1, (truoc, sau)


def test_a3_khoa_kho_khong_betac_giua_coroutine(moi_truong):
    """cap_nhat_so: toàn bộ đọc-sửa-ghi đồng bộ trong `with giu_khoa_kho()`
    (không await ở giữa) → 2 coroutine đồng thời không làm mất cập nhật."""
    from app import lam_moi_model as lmm

    async def _chay():
        await asyncio.gather(
            asyncio.to_thread(lmm.cap_nhat_so, "mod-k", [{"id": "a1", "nhan": "A"}]),
            asyncio.to_thread(lmm.cap_nhat_so, "mod-k", [{"id": "b1", "nhan": "B"}]),
        )
        return lmm._doc_so()["modules"]["mod-k"]

    mod_dict = asyncio.run(_chay())
    assert set(mod_dict) == {"a1", "b1"}, set(mod_dict)


def test_a3_model_mac_dinh_khong_tran_so_muc(moi_truong):
    """PUT model: trường `tat` bị lọc theo model hợp lệ (hữu hạn), nhưng
    `mac_dinh` {nhóm: id} KHÔNG có trần số mục — 200 nghìn nhóm lạ → 200 và
    được ghi nguyên vào model.json (phình đĩa, đọc lại nặng). model_bo_sung
    có trần 100 mục; đường này phải có trần tương đương."""
    _dung_module(moi_truong, "mod-tran", models=[{"id": "m1"}])
    with TestClient(app) as c:
        _cai_module(c, "mod-tran")
        md = {f"nhom-gia-{i}": "m1" for i in range(200_000)}
        r = c.put("/api/modules/mod-tran/model", headers=QT,
                  json={"tat": [], "mac_dinh": md})
        assert r.status_code == 400, r.status_code


# ── A.4: dữ liệu độc từ nhà cung cấp ─────────────────────────────────────────


def test_a4_id_chua_html_duoc_chap_nhan_nhung_khong_vot_qua_khuon():
    """id chứa '<img onerror>': chuan_hoa_ma_model KHÔNG loại (không phải ký tự
    điều khiển) → đi vào sổ NGUYÊN VẸN (không bị cắt/xuyên). XSS là việc của tầng
    hiển thị (dc-lite thoát HTML — xem test_a4_dc_lite_thoat_html)."""
    from app import lam_moi_model as lmm
    doc = '<img src=x onerror=alert(1)>'
    ds = lmm.loc_danh_sach_model([{"id": doc, "nhan": "X"}])
    assert ds and ds[0]["id"] == doc  # giữ nguyên, không biến dạng


def test_a4_nhan_chua_html_giu_nguyen_sau_loc():
    from app import lam_moi_model as lmm
    nhan = '"><script>alert(1)</script>'
    ds = lmm.loc_danh_sach_model([{"id": "m1", "nhan": nhan}])
    assert ds[0]["nhan"] == nhan


def test_a4_dc_lite_thoat_html():
    """Tĩnh: dc-lite (web/khung.js) thoát ký tự HTML ở mọi {{ }}; không có
    innerHTML trong khung.js; model chỉ vào thuộc tính/text đã thoát."""
    import re
    js = Path("web/khung.js").read_text(encoding="utf-8")
    assert "{{ }} của dc-lite thoát ký tự HTML" in js
    # không GÁN innerHTML ở đâu (chữ "innerHTML" chỉ còn trong chú thích giải thích)
    assert not re.search(r"\.innerHTML\s*=", js), "có gán innerHTML trong khung.js"
    for tep in ("web/He-thong-AI.dc.html", "web/Studio.dc.html"):
        html = Path(tep).read_text(encoding="utf-8")
        assert not re.search(r"\.innerHTML\s*=", html), f"gán innerHTML trong {tep}"


def test_a4_id_dai_129_bi_loai_128_duoc():
    from app import lam_moi_model as lmm
    assert lmm.chuan_hoa_ma_model("a" * 129) is None
    assert lmm.chuan_hoa_ma_model("a" * 128) == "a" * 128
    assert lmm.chuan_hoa_ma_model("") is None
    assert lmm.chuan_hoa_ma_model("  ") is None


def test_a4_nhan_dai_bi_cat_80():
    from app import lam_moi_model as lmm
    assert lmm.chuan_hoa_nhan("n" * 200) == "n" * 80
    assert lmm.chuan_hoa_nhan("   ") is None


def test_a4_tien_to_gia_mao_bi_chan():
    """Upstream của module mod-a trả id 'mod-b/m1' (tiền tố của module khác):
    sổ của mod-a PHẢI từ chối hoặc gắn không gian tên, không lưu nguyên."""
    from app import lam_moi_model as lmm
    ds = lmm.loc_danh_sach_model([{"id": "mod-b/m1", "nhan": "Gia"}])
    assert ds == [] or all(not m["id"].startswith("mod-b/") for m in ds)


def test_a4_trung_id_trong_cung_dot_bi_loai():
    from app import lam_moi_model as lmm
    ds = lmm.loc_danh_sach_model([
        {"id": "m1", "nhan": "Một"}, {"id": "m1", "nhan": "Hai"},
    ])
    assert [m["id"] for m in ds] == ["m1"]
    assert ds[0]["nhan"] == "Một"


def test_a4_ky_tu_dieu_khien_bi_loai():
    from app import lam_moi_model as lmm
    assert lmm.chuan_hoa_ma_model("a\nb") is None
    assert lmm.chuan_hoa_ma_model("a\x00b") is None
    assert lmm.chuan_hoa_ma_model("a\u200bb") is None  # zero-width space (Cf)
    assert lmm.chuan_hoa_ma_model("m1") == "m1"


# ── A.5: logic sổ ────────────────────────────────────────────────────────────


def test_a5_model_tat_khong_tu_bat_sau_lam_moi(moi_truong):
    """Model đã TẮT (cài đặt tat) không tự bật lại sau lượt làm mới thành công."""
    from app import lam_moi_model as lmm
    lmm.cap_nhat_so("mod-lg", [{"id": "m1", "nhan": "M"}])
    # giả lập cài đặt tắt m1
    from app import kho as _kho
    with _kho.giu_khoa_kho():
        p = _kho.thu_muc_du_lieu() / "cai_dat_model.json"
        cu = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        cu.setdefault("mod-lg", {})["tat"] = ["m1"]
        _kho.ghi_nguyen_tu(p, json.dumps(cu, ensure_ascii=False), quyen=0o600)
    # làm mới lại với cùng danh sách
    lmm.cap_nhat_so("mod-lg", [{"id": "m1", "nhan": "M"}])
    with _kho.giu_khoa_kho():
        cu2 = json.loads(p.read_text(encoding="utf-8"))
    assert cu2["mod-lg"]["tat"] == ["m1"]


def test_a5_ds_rong_khong_tang_vang(moi_truong):
    """Nguồn lỗi nuốt ngoại lệ trả rỗng: ds_sach rỗng → sổ giữ nguyên, vang không tăng."""
    from app import lam_moi_model as lmm
    lmm.cap_nhat_so("mod-v", [{"id": "m1", "nhan": "M"}])
    truoc = lmm._doc_so()["modules"]["mod-v"]["m1"]["vang"]
    kq = lmm.cap_nhat_so("mod-v", [])
    assert kq == {"moi": [], "khong_con": [], "tro_lai": []}
    sau = lmm._doc_so()["modules"]["mod-v"]["m1"]
    assert sau["vang"] == truoc and sau["con"] is True


def test_a5_vang_2_chu_ky_thi_mat_1_chu_ky_thi_con(moi_truong):
    """SO_CHU_KY_VANG=2: vắng 1 chu kỳ vẫn 'con', vắng 2 chu kỳ → 'khong_con'."""
    from app import lam_moi_model as lmm
    lmm.cap_nhat_so("mod-v2", [{"id": "m1", "nhan": "M"}])
    kq1 = lmm.cap_nhat_so("mod-v2", [{"id": "m2", "nhan": "M2"}])
    assert kq1["khong_con"] == []
    assert lmm._doc_so()["modules"]["mod-v2"]["m1"]["con"] is True
    kq2 = lmm.cap_nhat_so("mod-v2", [{"id": "m2", "nhan": "M2"}])
    assert kq2["khong_con"] == ["m1"]
    # trở lại → tro_lai
    kq3 = lmm.cap_nhat_so("mod-v2", [{"id": "m1"}, {"id": "m2"}])
    assert kq3["tro_lai"] == ["m1"]


def test_a5_hai_luot_dong_thoi_so_nguyen_ven(moi_truong):
    """Hai chu kỳ đồng thời (không single-flight liên-vòng): sổ vẫn nhất quán,
    không mất model, không trùng lặp."""
    from app import lam_moi_model as lmm

    async def _chay():
        def lay():
            class M:
                async def lam_moi_model(self):
                    await asyncio.sleep(0.05)

                async def models(self):
                    return [{"id": "m1", "nhan": "M"}]
            return {"mod-2v": M()}
        vong1 = lmm.VongLamMoiModel(lay)
        vong2 = lmm.VongLamMoiModel(lay)
        r = await asyncio.gather(vong1.lam_moi_mot("mod-2v"), vong2.lam_moi_mot("mod-2v"))
        return r

    rs = asyncio.run(_chay())
    assert all(r["ket_qua"] == "ok" for r in rs)
    mod_dict = lmm._doc_so()["modules"]["mod-2v"]
    assert set(mod_dict) == {"m1"}
    assert mod_dict["m1"]["vang"] == 0


def test_a5_lam_moi_khach_khong_kich_hoat_duoc(moi_truong):
    """Kẻ có khoá khách (quyền hạn chế) không kích hoạt được lam-moi-model
    để làm model 'biến mất' qua vắng 2 chu kỳ → 403."""
    _dung_module(moi_truong, "mod-l5", co_lam_moi=True)
    with TestClient(app) as c:
        _cai_module(c, "mod-l5")
        auth = _khoa_khach(["chat"])
        r = c.post("/api/modules/mod-l5/lam-moi-model", headers=auth)
        assert r.status_code == 403


# ── A.6: rò rỉ bí mật ────────────────────────────────────────────────────────


def test_a6_khoa_khong_vao_log_lam_moi(moi_truong, caplog):
    """Module lỗi với thông điệp chứa chuỗi giống khoá: log chỉ ghi TÊN loại
    ngoại lệ, không ghi nội dung (không lộ khoá)."""
    from app import lam_moi_model as lmm

    async def _chay():
        class ModLoi:
            async def lam_moi_model(self):
                raise RuntimeError("khoa-that-AIzaSy000-RAT-DAI-9999999999")

            async def models(self):
                return []

        vong = lmm.VongLamMoiModel(lambda: {"mod-loi": ModLoi()})
        with caplog.at_level(logging.WARNING, logger="web2api.lam_moi_model"):
            await vong.lam_moi_mot("mod-loi")

    asyncio.run(_chay())
    ban_ghi = "\n".join(r.getMessage() for r in caplog.records)
    assert "AIzaSy000" not in ban_ghi
    assert "RuntimeError" in ban_ghi


def test_a6_loi_api_khong_lo_noi_dung_ngoai_le(moi_truong):
    """POST lam-moi-model khi upstream lỗi: phản hồi chỉ có tên loại ngoại lệ."""
    _dung_module(moi_truong, "mod-loi-api", co_lam_moi=True)
    with TestClient(app) as c:
        _cai_module(c, "mod-loi-api")
        m = c.app.state.so.dang_chay["mod-loi-api"]

        async def lam_moi_hong():
            raise ConnectionError("bi-mat-noi-bo-xyz")

        m.lam_moi_model = lam_moi_hong
        r = c.post("/api/modules/mod-loi-api/lam-moi-model", headers=QT)
        assert r.status_code in (200, 502)
        assert "bi-mat-noi-bo-xyz" not in r.text
        assert "ConnectionError" in r.text or r.status_code == 200


def test_a6_so_model_khong_chua_khoa(moi_truong):
    """model_so.json sau các lượt làm mới: không chứa chuỗi khoá/giá trị bí mật."""
    from app import lam_moi_model as lmm
    lmm.cap_nhat_so("mod-s", [{"id": "m1", "nhan": "M"}])
    p = kho.thu_muc_du_lieu() / "model_so.json"
    noi_dung = p.read_text(encoding="utf-8")
    assert "khoa-quan-tri" not in noi_dung
    assert "AIza" not in noi_dung
    assert oct(p.stat().st_mode & 0o777) == "0o600"


def test_a6_nhat_ky_chi_ghi_so_muc(moi_truong):
    """PUT model-bo-sung: nhật ký quản trị ghi SỐ mục, không ghi nội dung id/nhan."""
    _dung_module(moi_truong, "mod-nk")
    with TestClient(app) as c:
        _cai_module(c, "mod-nk")
        r = c.put("/api/modules/mod-nk/model-bo-sung", headers=QT,
                  json={"models": [{"id": "id-bi-mat-xyz", "nhan": "Nhan XYZ"}]})
        assert r.status_code == 200
        muc = nhat_ky.doc(kenh="quan_tri", gioi_han=5)
        gan_nhat = [m for m in muc if "model bổ sung" in str(m.get("tom_tat", ""))]
        assert gan_nhat, muc
        noi_dung = json.dumps(gan_nhat[0], ensure_ascii=False)
        assert "id-bi-mat-xyz" not in noi_dung
        assert "1 mục" in noi_dung
