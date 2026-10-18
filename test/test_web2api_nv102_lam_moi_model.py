"""Test NV102-LÕI — làm mới danh sách model tự động (app/lam_moi_model.py và móc nối).

Máy chủ giả, không Internet. Chuỗi khoá dùng từ allowlist .gitleaks.toml.
Kiểm tra các ca 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 theo đặc tả NV102.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import kho, khoa_khach, so_module
from app.main import app

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


def _tao_module_gia(
    goc: Path,
    id_: str,
    tien_to: str,
    models: list[dict],
    co_lam_moi: bool = False,
    cham_lam_moi: float = 0.0,
    loi_lam_moi: Exception | None = None,
    cai_ok: bool = True,
) -> Path:
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
        "cung_cap": ["chat"],
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
    (thu_muc / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    lam_moi_code = ""
    if co_lam_moi:
        lam_moi_code = f"""
    async def lam_moi_model(self):
        self.da_lam_moi = True
        if {cham_lam_moi} > 0:
            import asyncio
            await asyncio.sleep({cham_lam_moi})
        if self._loi_lam_moi:
            raise self._loi_lam_moi
"""
    ma_python = f"""class Module:
    def __init__(self, m):
        self.manifest = m
        self.tien_to = m["tien_to"]
        self.raw_models = {models!r}
        self.da_lam_moi = False
        self._loi_lam_moi = None

    def nap(self, cfg):
        pass

    async def cai(self, cfg):
        if not {cai_ok}:
            return {{"ok": False, "ket_qua_tung_khoa": [], "loi": "Cấu hình sai", "cau_hinh_luu": {{}}}}
        return {{"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": cfg}}

    async def chat(self, body, stream):
        return 200, {{"content-type": "application/json"}}, b'{{"choices": [{{"message": {{"content": "ok"}}}}]}}'

    async def models(self):
        return [dict(x) for x in self.raw_models]

    async def suc_khoe(self):
        return {{"ok": True}}

    async def dung(self):
        pass
{lam_moi_code}
"""
    (thu_muc / "module.py").write_text(ma_python, encoding="utf-8")
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


# ── Ca 1: chu_ky_gio ──────────────────────────────────────────────────────────


def test_1_chu_ky_gio(monkeypatch, caplog):
    from app import lam_moi_model

    monkeypatch.delenv("WEB2API_CHU_KY_MODEL_GIO", raising=False)
    assert lam_moi_model.chu_ky_gio() == 3.0

    monkeypatch.setenv("WEB2API_CHU_KY_MODEL_GIO", "")
    assert lam_moi_model.chu_ky_gio() == 3.0

    monkeypatch.setenv("WEB2API_CHU_KY_MODEL_GIO", "2.5")
    assert lam_moi_model.chu_ky_gio() == 2.5

    monkeypatch.setenv("WEB2API_CHU_KY_MODEL_GIO", "0")
    assert lam_moi_model.chu_ky_gio() == 1.0

    monkeypatch.setenv("WEB2API_CHU_KY_MODEL_GIO", "-5")
    assert lam_moi_model.chu_ky_gio() == 1.0

    monkeypatch.setenv("WEB2API_CHU_KY_MODEL_GIO", "100")
    assert lam_moi_model.chu_ky_gio() == 24.0

    with caplog.at_level(logging.WARNING):
        for val in ["abc", "nan", "inf", "1e999", "true"]:
            caplog.clear()
            monkeypatch.setenv("WEB2API_CHU_KY_MODEL_GIO", val)
            assert lam_moi_model.chu_ky_gio() == 3.0
            assert any(
                "WEB2API_CHU_KY_MODEL_GIO" in r.getMessage()
                for r in caplog.records
            )


# ── Ca 2: Vòng làm mới định kỳ và tiêm hàm ngủ ───────────────────────────────


def test_2_vong_lam_moi_tiem_ngu(monkeypatch):
    from app import lam_moi_model

    cac_lan_ngu = []

    async def ngu_gia(giay):
        cac_lan_ngu.append(giay)
        await asyncio.sleep(0.001)

    monkeypatch.delenv("WEB2API_CHU_KY_MODEL_GIO", raising=False)
    so_lan_chu_ky = 0

    class Mod:
        async def lam_moi_model(self):
            pass

        async def models(self):
            return [{"id": "test/m1"}]

    vong = lam_moi_model.VongLamMoiModel(lambda: {"m": Mod()}, ngu=ngu_gia)

    goc_chay = vong.chay_mot_chu_ky

    async def chay_dem():
        nonlocal so_lan_chu_ky
        so_lan_chu_ky += 1
        if so_lan_chu_ky == 2:
            monkeypatch.setenv("WEB2API_CHU_KY_MODEL_GIO", "1")
        elif so_lan_chu_ky >= 3:
            await vong.dung()
        return await goc_chay()

    vong.chay_mot_chu_ky = chay_dem

    async def _kich_ban():
        vong.bat_dau()
        for _ in range(100):
            if vong._dung_co:
                break
            await asyncio.sleep(0.01)
        await vong.dung()

    asyncio.run(_kich_ban())

    assert len(cac_lan_ngu) >= 3
    assert cac_lan_ngu[0] == lam_moi_model.TRI_HOAN_DAU_GIAY
    assert cac_lan_ngu[1] == 3.0 * 3600
    assert cac_lan_ngu[2] == 1.0 * 3600


# ── Ca 3: Lõi chạy khi không có module nào / không có hàm ─────────────────────


def test_3_khong_co_module_hoac_khong_co_ham(moi_truong):
    from app import lam_moi_model

    vong = lam_moi_model.VongLamMoiModel(lambda: {})
    kq = asyncio.run(vong.chay_mot_chu_ky())
    assert kq == {}

    p_so = kho.thu_muc_du_lieu() / "model_so.json"
    assert not p_so.exists()

    class ModKhongHam:
        async def models(self):
            return [{"id": "test/m1"}]

    vong2 = lam_moi_model.VongLamMoiModel(lambda: {"mod": ModKhongHam()})
    kq2 = asyncio.run(vong2.chay_mot_chu_ky())
    assert kq2 == {}
    assert not p_so.exists()


# ── Ca 4: Single-flight, timeout và bảo mật log ngoại lệ ──────────────────────


def test_4_single_flight_timeout_va_che_log(moi_truong, monkeypatch, caplog):
    from app import lam_moi_model

    monkeypatch.setattr(lam_moi_model, "HET_GIO_MODULE_GIAY", 0.05)

    class ModTreo:
        async def lam_moi_model(self):
            await asyncio.sleep(10.0)

        async def models(self):
            return [{"id": "treo/m1"}]

    class ModLoi:
        async def lam_moi_model(self):
            raise RuntimeError("sk-khoa-tu-xa-RIENG-7777-khong-duoc-lo")

        async def models(self):
            return []

    class ModOk:
        async def lam_moi_model(self):
            pass

        async def models(self):
            return [{"id": "ok/m1"}]

    mods = {
        "mod_treo": ModTreo(),
        "mod_loi": ModLoi(),
        "mod_ok": ModOk(),
    }
    vong = lam_moi_model.VongLamMoiModel(lambda: mods)

    with caplog.at_level(logging.WARNING, logger="web2api"):
        kq = asyncio.run(vong.chay_mot_chu_ky())

    assert kq.get("mod_treo") == "het_gio"
    assert kq.get("mod_loi") == "loi"
    assert kq.get("mod_ok") == "ok"

    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert "sk-khoa-tu-xa-RIENG-7777" not in log_text
    assert "RuntimeError" in log_text


# ── Ca 5: Quy tắc cập nhật sổ ────────────────────────────────────────────────


def test_5_quy_tac_cap_nhat_so(moi_truong):
    from app import lam_moi_model

    # Chu kỳ 1: [a, b] -> cả 2 còn, vang=0
    kq1 = lam_moi_model.cap_nhat_so("mod1", [{"id": "mod1/a"}, {"id": "mod1/b"}])
    assert set(kq1["moi"]) == {"mod1/a", "mod1/b"}
    assert kq1["khong_con"] == []

    p_so = kho.thu_muc_du_lieu() / "model_so.json"
    so = json.loads(p_so.read_text(encoding="utf-8"))
    assert so["modules"]["mod1"]["mod1/a"]["con"] is True
    assert so["modules"]["mod1"]["mod1/a"]["vang"] == 0
    assert so["modules"]["mod1"]["mod1/b"]["con"] is True
    assert so["modules"]["mod1"]["mod1/b"]["vang"] == 0

    # Chu kỳ 2: [a] -> b vắng 1 chu kỳ, vang=1, con vẫn True
    kq2 = lam_moi_model.cap_nhat_so("mod1", [{"id": "mod1/a"}])
    assert kq2["moi"] == []
    assert kq2["khong_con"] == []
    so = json.loads(p_so.read_text(encoding="utf-8"))
    assert so["modules"]["mod1"]["mod1/b"]["con"] is True
    assert so["modules"]["mod1"]["mod1/b"]["vang"] == 1

    # Chu kỳ rỗng: ds_sach = [] nghĩa là không có thông tin -> không đổi gì
    kq_rong = lam_moi_model.cap_nhat_so("mod1", [])
    assert kq_rong["moi"] == []
    assert kq_rong["khong_con"] == []
    so = json.loads(p_so.read_text(encoding="utf-8"))
    assert so["modules"]["mod1"]["mod1/b"]["con"] is True
    assert so["modules"]["mod1"]["mod1/b"]["vang"] == 1

    # Chu kỳ 3: [a] -> b vắng chu kỳ thứ 2, vang=2 -> con=False
    kq3 = lam_moi_model.cap_nhat_so("mod1", [{"id": "mod1/a"}])
    assert kq3["khong_con"] == ["mod1/b"]
    so = json.loads(p_so.read_text(encoding="utf-8"))
    assert so["modules"]["mod1"]["mod1/b"]["con"] is False
    assert so["modules"]["mod1"]["mod1/b"]["vang"] == 2

    # Chu kỳ 4: [a, b] -> b quay lại -> con=True, vang=0
    kq4 = lam_moi_model.cap_nhat_so("mod1", [{"id": "mod1/a"}, {"id": "mod1/b"}])
    assert kq4["tro_lai"] == ["mod1/b"]
    so = json.loads(p_so.read_text(encoding="utf-8"))
    assert so["modules"]["mod1"]["mod1/b"]["con"] is True
    assert so["modules"]["mod1"]["mod1/b"]["vang"] == 0


# ── Ca 6: Giữ lựa chọn của chủ máy (model.json không đổi một byte) ────────────


def test_6_giu_lua_chon_chu_may_model_json(moi_truong):
    from app import lam_moi_model

    # Đặt trước cấu hình model.json
    kho.luu_cai_dat_model_module("mod_x", ["mod_x/b"], {"chat": "mod_x/a"})
    p_model = kho.thu_muc_du_lieu() / "model.json"
    noi_dung_goc = p_model.read_bytes()

    class ModX:
        def __init__(self):
            self.ds = [{"id": "mod_x/a"}, {"id": "mod_x/c"}]  # b vắng, c mới

        async def lam_moi_model(self):
            pass

        async def models(self):
            return list(self.ds)

    mod_x = ModX()
    vong = lam_moi_model.VongLamMoiModel(lambda: {"mod_x": mod_x})

    async def _chay():
        # Chạy 3 chu kỳ
        for _ in range(3):
            await vong.chay_mot_chu_ky()

        # mod_x/b quay lại
        mod_x.ds.append({"id": "mod_x/b"})
        await vong.chay_mot_chu_ky()

    asyncio.run(_chay())

    # model.json KHÔNG đổi 1 byte
    assert p_model.read_bytes() == noi_dung_goc


# ── Ca 7: PUT /api/modules/{id}/model giữ tat của model vắng ──────────────────


def test_7_put_giu_tat_model_vang(moi_truong):
    # Khởi tạo module giả và cài đặt ban đầu
    _tao_module_gia(
        moi_truong / "modules",
        "gia-mod",
        "gia",
        [{"id": "gia/a"}, {"id": "gia/b"}],
    )
    with TestClient(app) as c:
        # Cài module
        r_cai = c.post(
            "/api/modules/gia-mod/cai",
            headers=QT,
            json={"cau_hinh": {"api_key": "khoa-gia"}},
        )
        assert r_cai.status_code == 200

        # Tắt model gia/b
        r_put = c.put(
            "/api/modules/gia-mod/model",
            headers=QT,
            json={"tat": ["gia/b"], "mac_dinh": {"chat": "gia/a"}},
        )
        assert r_put.status_code == 200
        assert "gia/b" in kho.doc_cai_dat_model_module("gia-mod")["tat"]

        # Cho module giả tạm thời KHÔNG trả gia/b (chỉ trả gia/a)
        m = c.app.state.so.dang_chay["gia-mod"]
        m.raw_models = [{"id": "gia/a"}]

        # PUT cập nhật tat: []
        r_put2 = c.put(
            "/api/modules/gia-mod/model",
            headers=QT,
            json={"tat": []},
        )
        assert r_put2.status_code == 200
        # Kiểm tra kho: gia/b vẫn phải còn trong tat!
        cai_dat = kho.doc_cai_dat_model_module("gia-mod")
        assert "gia/b" in cai_dat["tat"]

        # Cho module trả lại gia/b
        m.raw_models = [{"id": "gia/a"}, {"id": "gia/b"}]
        r_v1 = c.get("/v1/models", headers=QT)
        assert r_v1.status_code == 200
        ids_v1 = [it["id"] for it in r_v1.json()["data"]]
        assert "gia/b" not in ids_v1
        assert "gia/a" in ids_v1


# ── Ca 8: Chuẩn hoá mã lạ, nhãn và bảo mật XSS ───────────────────────────────


def test_8_chuan_hoa_ma_la_va_nhan(caplog):
    from app import lam_moi_model

    # Chuẩn hoá mã model
    assert lam_moi_model.chuan_hoa_ma_model("  gpt-4o  ") == "gpt-4o"
    assert lam_moi_model.chuan_hoa_ma_model("a/b:v1.0") == "a/b:v1.0"
    assert lam_moi_model.chuan_hoa_ma_model("mô hình tiếng việt") == "mô hình tiếng việt"
    assert lam_moi_model.chuan_hoa_ma_model("a/<script>alert(1)</script>") == "a/<script>alert(1)</script>"

    # Ký tự điều khiển, xuống dòng, quá dài, không phải chuỗi
    assert lam_moi_model.chuan_hoa_ma_model("line1\nline2") is None
    assert lam_moi_model.chuan_hoa_ma_model("null\x00byte") is None
    assert lam_moi_model.chuan_hoa_ma_model("a" * 129) is None
    assert lam_moi_model.chuan_hoa_ma_model("") is None
    assert lam_moi_model.chuan_hoa_ma_model(123) is None

    # Chuẩn hoá nhãn
    assert lam_moi_model.chuan_hoa_nhan("  Nhãn hiển thị  ") == "Nhãn hiển thị"
    assert lam_moi_model.chuan_hoa_nhan("Nhãn\x00có\x01ctrl") == "Nhãncóctrl"
    assert len(lam_moi_model.chuan_hoa_nhan("x" * 200)) == 80
    assert lam_moi_model.chuan_hoa_nhan(None) is None
    assert lam_moi_model.chuan_hoa_nhan(123) is None

    # Lọc danh sách model
    raw = [
        {"id": "m1\nloi"},
        {"id": "m2", "nhan": "n" * 100},
        {"id": "m2", "nhan": "trung"},
        {"id": "m3/<script>test</script>"},
        {"id": None},
    ]
    ds = lam_moi_model.loc_danh_sach_model(raw)
    # NV110: không có tien_to để đối chiếu → id mang '/' (tuyên bố không gian tên
    # không kiểm chứng được) bị loại; ý nghĩa chuẩn hoá của test giữ nguyên.
    assert len(ds) == 1
    assert ds[0]["id"] == "m2"
    assert len(ds[0]["nhan"]) == 80
    # Có tien_to khớp tuyên bố: id tuyên bố đúng không gian tên được giữ.
    ds2 = lam_moi_model.loc_danh_sach_model(raw, tien_to="m3")
    assert [m["id"] for m in ds2] == ["m2", "m3/<script>test</script>"]

    # Quá 500 model -> cắt và log warning (NV110: cần tien_to khớp để id có '/'
    # không bị loại trước khi cắt)
    nhieu = [{"id": f"m/{i}"} for i in range(505)]
    with caplog.at_level(logging.WARNING, logger="web2api"):
        ds_cat = lam_moi_model.loc_danh_sach_model(nhieu, tien_to="m")
    assert len(ds_cat) == 500
    assert any("500" in r.getMessage() and "5" in r.getMessage() for r in caplog.records)


# ── Ca 9: sau_khi_cai chạy nền và không làm chậm ─────────────────────────────


def test_9_sau_khi_cai_chay_nen(moi_truong):
    # 1. Module có lam_moi_model: cài thành công -> phản hồi nhanh, có model_dang_lay,
    # và hook lam_moi_model thật sự được gọi nền (thăm dò thấy cờ da_lam_moi)
    _tao_module_gia(
        moi_truong / "modules",
        "mod-cham",
        "cham",
        [{"id": "cham/m1"}],
        co_lam_moi=True,
        cham_lam_moi=10.0,
    )
    # 2. Module KHÔNG có lam_moi_model
    _tao_module_gia(
        moi_truong / "modules",
        "mod-ko-lm",
        "kolm",
        [{"id": "kolm/m1"}],
        co_lam_moi=False,
    )
    # 3. Module có lam_moi_model nhưng cài thất bại (ok: False)
    _tao_module_gia(
        moi_truong / "modules",
        "mod-that-bai",
        "thatbai",
        [{"id": "thatbai/m1"}],
        co_lam_moi=True,
        cai_ok=False,
    )

    with TestClient(app) as c:
        # Ca 1: có lam_moi_model -> phản hồi có model_dang_lay: true và cờ nền được bật
        t0 = time.monotonic()
        r = c.post(
            "/api/modules/mod-cham/cai",
            headers=QT,
            json={"cau_hinh": {"api_key": "khoa-gia"}},
        )
        t_ket_thuc = time.monotonic() - t0
        assert t_ket_thuc < 3.0, f"Phản hồi cài đặt quá chậm: {t_ket_thuc}s"
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True
        assert data.get("model_dang_lay") is True

        # Thăm dò ngắn (≤ 5 giây, bước ≤ 0.05 giây) thấy cờ da_lam_moi được bật
        m_cham = c.app.state.so.dang_chay["mod-cham"]
        t_bat_dau = time.monotonic()
        co_chay = False
        while time.monotonic() - t_bat_dau < 5.0:
            if getattr(m_cham, "da_lam_moi", False):
                co_chay = True
                break
            time.sleep(0.02)
        assert co_chay is True, "Hook lam_moi_model phải được gọi trong tác vụ nền"

        # Ca 2: Module không có lam_moi_model -> phản hồi không có khoá model_dang_lay
        r_ko = c.post(
            "/api/modules/mod-ko-lm/cai",
            headers=QT,
            json={"cau_hinh": {"api_key": "khoa-gia"}},
        )
        assert r_ko.status_code == 200
        data_ko = r_ko.json()
        assert data_ko.get("ok") is True
        assert "model_dang_lay" not in data_ko

        # Ca 3: Module cài thất bại (cai trả ok: false) -> hook KHÔNG được gọi
        r_tb = c.post(
            "/api/modules/mod-that-bai/cai",
            headers=QT,
            json={"cau_hinh": {"api_key": "khoa-gia"}},
        )
        assert r_tb.status_code == 400
        data_tb = r_tb.json()
        assert data_tb.get("ok") is False
        assert "model_dang_lay" not in data_tb
        time.sleep(0.1)
        m_tb = c.app.state.so.dang_chay.get("mod-that-bai")
        if m_tb is not None:
            assert getattr(m_tb, "da_lam_moi", False) is False


# ── Ca 10: Kiểm tra xác thực các đường API mới ───────────────────────────────


def test_10_xac_thuc_duong_moi(moi_truong):
    _tao_module_gia(
        moi_truong / "modules",
        "mod-auth",
        "auth",
        [{"id": "auth/m1"}],
        co_lam_moi=True,
    )
    with TestClient(app) as c:
        # 1. Không khoá -> 401
        assert c.post("/api/modules/mod-auth/lam-moi-model").status_code == 401
        assert c.get("/api/modules/mod-auth/model-so").status_code == 401

        # 2. Khoá khách -> 403
        _, hd_khach = khoa_khach.tao_khoa(nhan="khach", pham_vi=["x"])
        auth_khach = {"Authorization": f"Bearer {hd_khach}"}
        assert c.post("/api/modules/mod-auth/lam-moi-model", headers=auth_khach).status_code == 403
        assert c.get("/api/modules/mod-auth/model-so", headers=auth_khach).status_code == 403

        # 3. Id lạ -> 404
        assert c.post("/api/modules/khong-co-gi/lam-moi-model", headers=QT).status_code == 404
        assert c.get("/api/modules/khong-co-gi/model-so", headers=QT).status_code == 404
        assert c.post("/api/modules/A/lam-moi-model", headers=QT).status_code == 404
        assert c.get("/api/modules/A/model-so", headers=QT).status_code == 404
        assert c.post(f"/api/modules/{'a'*42}/lam-moi-model", headers=QT).status_code == 404
        assert c.get("/api/modules/..%2f..%2fetc/model-so", headers=QT).status_code == 404

        # 4. Module có trong catalog nhưng chưa cấu hình chạy -> 404 module_not_configured
        r_chua_chay = c.post("/api/modules/mod-auth/lam-moi-model", headers=QT)
        assert r_chua_chay.status_code == 404
        assert r_chua_chay.json().get("error", {}).get("code") == "module_not_configured"
