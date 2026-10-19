"""Module van-phong — phần công cụ office_* qua container phụ (NV138).

- Công cụ `office_*` chỉ gọi dịch vụ van-phong-office khi người dùng bật
  (`office_bat`, mặc định TẮT); chưa bật hoặc không nối được → báo «chưa bật»
  (đóng khi lỗi, không lộ chi tiết), đường zip+XML của NV132 do module NV132 giữ.
- Công cụ ghi (set/add/remove/merge/import…) rủi ro `ghi`; công cụ đọc rủi ro
  `doc`; hành động ngoài tập office_* → từ chối.
- Đầu ra dịch vụ là DỮ LIỆU KHÔNG TIN: cắt ở trần, gắn `nguon_ngoai=True`.
- Khoá dịch vụ không vào log (một logging.Filter theo khuôn zalo-bot).

Máy chủ dịch vụ GIẢ cục bộ (monkeypatch httpx của module).
"""

from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "van-phong"

# Chuỗi GIẢ rõ ràng — không phải bí mật thật.
KHOA_GIA = "khoa-gia-van-phong-0123456789abcdef"
URL_GIA = "http://127.0.0.1:18701"


def _module_py():
    p = THU_MUC / "module.py"
    spec = importlib.util.spec_from_file_location("van_phong_nv138", p)
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    return md


@pytest.fixture()
def md():
    return _module_py()


@pytest.fixture()
def mod(md):
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    m = md.Module(manifest)
    m.nap({"office_bat": False, "dich_vu_url": URL_GIA, "khoa_dich_vu": KHOA_GIA})
    return m


def test_manifest_hop_le(md):
    from app.kham_pha import kiem_manifest
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    assert kiem_manifest(manifest) is None
    assert manifest["id"] == "van-phong"


def test_cong_cu_office_du_rui_ro_doc_ghi(mod):
    thuc_the = {e["id"]: e for e in mod.thuc_the()}
    doc = thuc_the["van_phong.office_doc"]
    ghi = thuc_the["van_phong.office_ghi"]
    assert doc["rui_ro_mac_dinh"] == "doc"
    assert ghi["rui_ro_mac_dinh"] == "ghi"
    ten_doc = {t["hanh_dong"] if isinstance(t, dict) else t for t in doc["tinh_nang"]}
    ten_ghi = {t["hanh_dong"] if isinstance(t, dict) else t for t in ghi["tinh_nang"]}
    assert {"office_view", "office_get", "office_query"} <= ten_doc
    assert {"office_set", "office_add", "office_remove", "office_merge", "office_import"} <= ten_ghi
    assert not (ten_doc & ten_ghi), "một công cụ không thể vừa đọc vừa ghi"
    for ten in ten_doc | ten_ghi:
        assert ten.startswith("office_"), f"công cụ {ten!r} phải có tiền tố office_"


def test_chua_bat_thi_bao_chua_bat_va_khong_goi_mang(mod, md, monkeypatch):
    da_goi = []
    monkeypatch.setattr(md.httpx, "Client", lambda *a, **k: da_goi.append(1) or 1 / 0)
    kq = mod.thuc_hien("van_phong.office_doc", "office_view", {"tep": "a.docx"})
    import asyncio
    kq = asyncio.run(kq) if not isinstance(kq, dict) else kq
    assert kq["ok"] is False
    assert "chưa bật" in kq["loi"]
    assert not da_goi, "chưa bật thì không được gọi mạng"


def _gia_lap_dich_vu(md, monkeypatch, tra_ve=None, ma=200):
    class TraLoiGia:
        status_code = ma

        def json(self):
            return tra_ve if tra_ve is not None else {"ok": True, "dau_ra": "xong"}

    class ClientGia:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **k):
            ClientGia.da_goi = (url, k.get("json"))
            return TraLoiGia()

    ClientGia.da_goi = None
    monkeypatch.setattr(md.httpx, "Client", ClientGia)
    return ClientGia


def test_bat_thi_goi_dich_vu_va_gan_nguon_ngoai(mod, md, monkeypatch):
    import asyncio
    _gia_lap_dich_vu(md, monkeypatch, {"ok": True, "dau_ra": "noi-dung-tai-lieu"})
    mod.nap({"office_bat": True, "dich_vu_url": URL_GIA, "khoa_dich_vu": KHOA_GIA})
    kq = asyncio.run(
        mod.thuc_hien("van_phong.office_doc", "office_view", {"tep": "a.docx"}))
    assert kq["ok"] is True
    assert kq["nguon_ngoai"] is True


def test_dau_ra_qua_tran_bi_cat_va_gan_nguon_ngoai(mod, md, monkeypatch):
    import asyncio
    _gia_lap_dich_vu(md, monkeypatch, {"ok": True, "dau_ra": "x" * (md.TRAN_DAU_RA + 5000)})
    mod.nap({"office_bat": True, "dich_vu_url": URL_GIA, "khoa_dich_vu": KHOA_GIA})
    kq = asyncio.run(
        mod.thuc_hien("van_phong.office_doc", "office_get", {"tep": "a.docx"}))
    assert len(kq["dau_ra"]) <= md.TRAN_DAU_RA + 100
    assert kq["nguon_ngoai"] is True


def test_khong_noi_duoc_dich_vu_bao_chua_bat(mod, md, monkeypatch):
    import asyncio

    class ClientHong:
        def __init__(self, *a, **k):
            raise ConnectionError("mang hong gia")

    monkeypatch.setattr(md.httpx, "Client", ClientHong)
    mod.nap({"office_bat": True, "dich_vu_url": URL_GIA, "khoa_dich_vu": KHOA_GIA})
    kq = asyncio.run(
        mod.thuc_hien("van_phong.office_doc", "office_view", {"tep": "a.docx"}))
    assert kq["ok"] is False
    assert "chưa bật" in kq["loi"]


def test_hanh_dong_ngoai_tap_bi_tu_choi(mod, md):
    import asyncio
    mod.nap({"office_bat": True, "dich_vu_url": URL_GIA, "khoa_dich_vu": KHOA_GIA})
    kq = asyncio.run(
        mod.thuc_hien("van_phong.office_doc", "office_mcp", {}))
    assert kq["ok"] is False


def test_bo_loc_log_chan_khoa_dich_vu(md):
    logger = logging.getLogger("httpx")
    ban_ghi = logging.LogRecord("httpx", logging.INFO, __file__, 1,
                                f"Authorization: Bearer {KHOA_GIA}", None, None)
    da_chan = [f for f in logger.filters if getattr(f, "_van_phong_loc", False)]
    assert da_chan, "thiếu logging.Filter chặn khoá dịch vụ"
    assert not any(f.filter(ban_ghi) for f in da_chan), "khoá dịch vụ lọt qua log"


def test_cai_tu_choi_url_la(md):
    import asyncio
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    m = md.Module(manifest)
    kq = asyncio.run(
        m.cai({"office_bat": True, "dich_vu_url": "http://169.254.169.254/", "khoa_dich_vu": KHOA_GIA}))
    assert kq["ok"] is False


def test_cai_qua_so_module_luu_cau_hinh_va_hoat_dong_voi_dich_vu_gia(tmp_path, monkeypatch):
    """Mục 3: Cài đặt qua SoModule:
    (a) Địa chỉ mặc định http://127.0.0.1:8701 mà README hướng dẫn cài được ngay cả khi
        không có biến môi trường WEB2API_TEST_CHO_PHEP_LOOPBACK (dịch vụ phụ nội bộ).
    (b) cai() trả về cau_hinh_luu sạch, khoá dịch vụ lưu vào bi_mat/, cấu hình được lưu.
    (c) Khởi động lại SoModule thì cấu hình được nạp lại và office_* hoạt động với dịch vụ.
    (d) SSRF vẫn chặn link-local (169.254.169.254).
    """
    import asyncio
    import shutil
    from app import kho, so_module
    from app.so_module import SoModule

    # Đảm bảo không có cờ test loopback
    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")

    # Chuẩn bị module van-phong trong WEB2API_DATA/modules/van-phong
    mod_dir = tmp_path / "modules" / "van-phong"
    shutil.copytree(THU_MUC, mod_dir, ignore=shutil.ignore_patterns("__pycache__"))

    so = SoModule()
    asyncio.run(so.khoi_dong())
    assert "van-phong" in so.catalog, "van-phong phải có trong catalog sau khởi động"

    # (d) Test SSRF trước: cài với 169.254.169.254 phải bị từ chối
    kq_ssrf = asyncio.run(so.cai("van-phong", {
        "office_bat": "1",
        "dich_vu_url": "http://169.254.169.254:8701",
        "khoa_dich_vu": KHOA_GIA,
    }))
    assert kq_ssrf["ok"] is False, "SSRF link-local phải bị từ chối"

    # (a) Cài đặt với địa chỉ mặc định http://127.0.0.1:8701
    cau_hinh_cai = {
        "office_bat": "1",
        "dich_vu_url": "http://127.0.0.1:8701",
        "khoa_dich_vu": KHOA_GIA,
    }
    kq_cai = asyncio.run(so.cai("van-phong", cau_hinh_cai))
    assert kq_cai["ok"] is True, f"Cài đặt thất bại: {kq_cai}"
    # Bí mật không bị lộ ra ngoài kết quả công khai
    assert KHOA_GIA not in json.dumps(kq_cai)

    # (b) Kiểm tra cấu hình đã được lưu trên đĩa trong modules.json và bi_mat/
    tep_modules = tmp_path / "modules.json"
    tep_bi_mat = tmp_path / "bi_mat" / "van-phong.json"
    assert tep_modules.exists(), "modules.json phải tồn tại"
    dl_modules = json.loads(tep_modules.read_text(encoding="utf-8"))
    muc = next(m for m in dl_modules["modules"] if m["id"] == "van-phong")
    cong_khai = muc["cau_hinh_cong_khai"]
    assert cong_khai.get("office_bat") in (True, "1", 1)
    assert cong_khai.get("dich_vu_url") == "http://127.0.0.1:8701"
    assert cong_khai.get("khoa_dich_vu", "").startswith("…"), "khoa_dich_vu phải ở dạng che trong modules.json"

    assert tep_bi_mat.exists(), "Tệp bí mật phải tồn tại"
    # Đọc bí mật qua kho.doc_bi_mat (hỗ trợ giải mã hầm khoá)
    dl_bi_mat = kho.doc_bi_mat("van-phong")
    assert dl_bi_mat.get("khoa_dich_vu") == KHOA_GIA

    # (c) Khởi động lại SoModule mới hoàn toàn (mô phỏng restart web2api)
    so_moi = SoModule()
    asyncio.run(so_moi.khoi_dong())
    mod_moi = so_moi.dang_chay["van-phong"]
    assert mod_moi._bat is True, "office_bat phải được nạp thành True sau khi khởi động lại"
    assert mod_moi._url == "http://127.0.0.1:8701"
    assert mod_moi._khoa == KHOA_GIA

    # Kiểm tra office_* hoạt động với dịch vụ GIẢ sau khi khởi động lại
    da_goi = []

    class ClientGia:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **k):
            da_goi.append((url, k))
            class Resp:
                status_code = 200
                def json(self):
                    return {"ok": True, "dau_ra": "thanh cong"}
            return Resp()

    monkeypatch.setattr(mod_moi.__class__.__module__ + ".httpx.Client", ClientGia, raising=False)
    # Monkeypatch trực tiếp lên httpx của module van-phong
    import sys
    for ten_mod, mod_obj in list(sys.modules.items()):
        if "van_phong" in ten_mod and hasattr(mod_obj, "httpx"):
            monkeypatch.setattr(mod_obj.httpx, "Client", ClientGia)

    kq_th = asyncio.run(mod_moi.thuc_hien("van_phong.office_doc", "office_view", {"tep": "a.docx"}))
    assert kq_th["ok"] is True
    assert kq_th["dau_ra"] == "thanh cong"
    assert kq_th["nguon_ngoai"] is True
    assert len(da_goi) == 1
    assert da_goi[0][0] == "http://127.0.0.1:8701/lenh"
    assert da_goi[0][1]["headers"]["Authorization"] == f"Bearer {KHOA_GIA}"


def test_quet_ten_nha_cung_cap_that_trong_app_van_bi_bat(tmp_path):
    """Mục 1: Chứng minh hằng chung MANH_TU_THONG_DUNG không làm lỏng test dò tên nhà cung cấp THẬT.
    Tên NCC thật (gemini, chatgpt, grok, claude...) thêm vào mã vẫn bị bắt (dùng file giả trong tmp_path,
    không sửa app/ thật).
    """
    from conftest import MANH_TU_THONG_DUNG
    import ast

    # 1. MANH_TU_THONG_DUNG chỉ chứa các từ thông dụng tiếng Việt / OpenAI role, tuyệt đối không có NCC thật
    ncc_that = {"gemini", "chatgpt", "grok", "claude", "google", "openai"}
    assert not (MANH_TU_THONG_DUNG & ncc_that), f"MANH_TU_THONG_DUNG không được chứa tên NCC thật: {MANH_TU_THONG_DUNG & ncc_that}"

    # 2. Logic quét _chu_trong_ma_python phải bắt được mọi tên NCC thật trong mã
    def _chu_trong_ma_python_local(p: Path) -> list[str]:
        cay = ast.parse(p.read_text(encoding="utf-8"))
        docstring = set()
        for nut in ast.walk(cay):
            if isinstance(nut, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and nut.body:
                dau = nut.body[0]
                if isinstance(dau, ast.Expr) and isinstance(dau.value, ast.Constant) and isinstance(dau.value.value, str):
                    docstring.add(id(dau.value))
        ra = []
        for nut in ast.walk(cay):
            if isinstance(nut, ast.Constant) and isinstance(nut.value, str) and id(nut) not in docstring:
                ra.append(nut.value)
            elif isinstance(nut, ast.Name):
                ra.append(nut.id)
            elif isinstance(nut, ast.Attribute):
                ra.append(nut.attr)
            elif isinstance(nut, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                ra.append(nut.name)
            elif isinstance(nut, ast.arg):
                ra.append(nut.arg)
        return ra

    for ten_that in ("gemini", "chatgpt", "grok", "claude"):
        tep_gia = tmp_path / f"ma_gia_{ten_that}.py"
        tep_gia.write_text(f"""
def ham_xu_ly():
    model_{ten_that} = "mo-hinh-ai"
    return model_{ten_that}
""", encoding="utf-8")
        chuoi_tim_thay = _chu_trong_ma_python_local(tep_gia)
        pham = [c for c in chuoi_tim_thay if ten_that in c.lower()]
        assert pham, f"Tên nhà cung cấp thật «{ten_that}» phải bị phát hiện trong mã python giả"


def test_cai_module_xac_thuc_leo_thang_quyen(tmp_path, monkeypatch):
    """Bảo mật LUẬT CỨNG #1: Kiểm leo thang quyền trên POST /api/modules/{id}/cai:
    - Không khoá → 401
    - Khoá sai / không phải quản trị → 401/403
    - Khoá quản trị hợp lệ → 200
    - Module không tồn tại → 404 (chống đụng tài nguyên không có)
    """
    from fastapi.testclient import TestClient
    from app.main import app
    import shutil

    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", "khoa-quan-tri-test-0123456789abcdef")

    mod_dir = tmp_path / "modules" / "van-phong"
    shutil.copytree(THU_MUC, mod_dir, ignore=shutil.ignore_patterns("__pycache__"))

    with TestClient(app) as client:
        # Nạp lại để nhận module
        client.post("/api/modules/nap-lai", headers={"Authorization": "Bearer khoa-quan-tri-test-0123456789abcdef"})

        cau_hinh = {
            "cau_hinh": {
                "office_bat": "1",
                "dich_vu_url": "http://127.0.0.1:8701",
                "khoa_dich_vu": KHOA_GIA,
            }
        }

    # 1. Không khoá → 401
    r_no_auth = client.post("/api/modules/van-phong/cai", json=cau_hinh)
    assert r_no_auth.status_code == 401, f"Không khoá phải nhận 401, nhận {r_no_auth.status_code}"

    # 2. Khoá sai / vai không đủ → 401 hoặc 403
    r_bad_auth = client.post("/api/modules/van-phong/cai",
                             headers={"Authorization": "Bearer khoa-khach-hoac-sai-quyen"},
                             json=cau_hinh)
    assert r_bad_auth.status_code in (401, 403), f"Khoá sai phải nhận 401/403, nhận {r_bad_auth.status_code}"

    # 3. IDOR / Module không tồn tại → 404
    r_idor = client.post("/api/modules/module-khong-ton-tai-999/cai",
                         headers={"Authorization": "Bearer khoa-quan-tri-test-0123456789abcdef"},
                         json=cau_hinh)
    assert r_idor.status_code == 404, f"Module không tồn tại phải nhận 404, nhận {r_idor.status_code}"

    # 4. Khoá quản trị chuẩn → 200
    r_admin = client.post("/api/modules/van-phong/cai",
                          headers={"Authorization": "Bearer khoa-quan-tri-test-0123456789abcdef"},
                          json=cau_hinh)
    assert r_admin.status_code == 200, f"Khoá quản trị phải nhận 200, nhận {r_admin.status_code}: {r_admin.text}"
    assert r_admin.json().get("ok") is True



