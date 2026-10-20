"""Test module youtube-phat (NV153 đợt B) — khuôn module gọi dịch vụ phụ.

Viết TRƯỚC mã (test-first): lần chạy đầu ĐỎ vì chưa có modules/youtube-phat/.

- `suc_khoe` async: mock httpx trả 200 → ok True; mock ném ConnectError →
  {"ok": False, "loi" bắt đầu "chưa kết nối được"}, KHÔNG ném.
- `cai()`: URL loopback (cần WEB2API_TEST_CHO_PHEP_LOOPBACK=1) → ok;
  169.254.169.254 (SSRF) → ok False; "ftp://x" → ok False.
- Khoá không lọt log: `nap` khoá giả rồi ghi log qua logger httpx chứa khoá → bị lọc.
- Độc lập lõi↔module: module chỉ được import app.tu_xa trong app.*;
  "youtube" KHÔNG xuất hiện trong app/, web/ (doc_lap).
- manifest hợp lệ: json load, id/tien_to/hang/truong_cau_hinh đúng.

Hợp đồng lõi liên quan (đọc từ mã, không đoán):
- app/kham_pha.py: lớp phải tên đúng `Module`; PHUONG_THUC = nap/cai/suc_khoe/dung.
- app/so_module.py::_nap: `nap` hợp đồng ĐỒNG BỘ — async def nap mà thật sự
  await thì lõi ném RuntimeError. Vì vậy nap là `def` thường (khuôn van-phong),
  chỉ cai/suc_khoe/dung là async (test_web2api_hop_dong_async_module.py bắt
  suc_khoe/dung async; lõi `await` cai ở app/so_module.py:530).

Kho chưa có pytest-asyncio nên test async chạy qua asyncio.run (khuôn
test_mikrotik_routeros.py).
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import inspect
import json
import logging
import pathlib
import sys

import pytest

from app.kham_pha import kiem_manifest

GOC = pathlib.Path(__file__).resolve().parent.parent
THU_MUC_MODULE = GOC / "modules" / "youtube-phat"
MANIFEST_PATH = THU_MUC_MODULE / "manifest.json"
MODULE_PY = THU_MUC_MODULE / "module.py"

KHOA_GIA = "khoa-gia-test-youtube-phat-0123456789abcdef"
URL_MAC_DINH = "http://127.0.0.1:8713"


def tai_module():
    """Nạp module như lõi làm (importlib từ tệp), trả về đối tượng mô-đun."""
    assert MODULE_PY.is_file(), (
        "chưa có modules/youtube-phat/module.py "
        "(test-first: lần chạy đầu đỏ là đúng)"
    )
    ten = "mod_youtube_phat_test"
    spec = importlib.util.spec_from_file_location(ten, MODULE_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(ten, None)
        raise
    return mod


@pytest.fixture()
def mod():
    return tai_module()


@pytest.fixture()
def manifest():
    assert MANIFEST_PATH.is_file(), "chưa có modules/youtube-phat/manifest.json"
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def the(mod, manifest):
    return mod.Module(manifest)


# ── manifest ─────────────────────────────────────────────────────────────


def test_manifest_hop_le(manifest):
    assert manifest["id"] == "youtube-phat"
    assert manifest["tien_to"] == "youtube_phat"
    assert manifest["hang"] == "B"
    assert manifest["loai"] == "tien_ich"
    assert manifest["giao_thuc"] == "http-noi-bo"
    assert manifest["yeu_cau_goi"] == ["httpx"]
    truong = {t["khoa"]: t for t in manifest["truong_cau_hinh"]}
    assert truong["dich_vu_url"]["mac_dinh"] == URL_MAC_DINH
    assert truong["khoa_dich_vu"]["kieu"] == "bi_mat"
    assert truong["khoa_dich_vu"]["bat_buoc"] is True
    mang = manifest["tai_nguyen"]["mang_ra"]
    for mien in ("youtube.com", "googlevideo.com", "ytimg.com", "zingmp3.vn"):
        assert mien in mang
    # kiem_manifest của lõi: None = hợp lệ.
    assert kiem_manifest(dict(manifest)) is None, kiem_manifest(dict(manifest))


def test_loai_thu_tu_la_so_nguyen_khong_trung(manifest):
    assert type(manifest["loai_thu_tu"]) is int
    for p in (GOC / "modules").glob("*/manifest.json"):
        if p.parent.name == "youtube-phat":
            continue
        khac = json.loads(p.read_text(encoding="utf-8"))
        assert khac.get("loai_thu_tu") != manifest["loai_thu_tu"], (
            f"trùng loai_thu_tu với module {p.parent.name}"
        )


# ── hợp đồng async / tên lớp ─────────────────────────────────────────────


def test_lop_ten_module_va_ham_async(mod):
    # Lõi (app/kham_pha.py) bắt lớp tên đúng «Module», không tên khác.
    assert isinstance(mod.Module, type)
    assert inspect.iscoroutinefunction(mod.Module.suc_khoe), "suc_khoe phải async def"
    assert inspect.iscoroutinefunction(mod.Module.dung), "dung phải async def"
    assert inspect.iscoroutinefunction(mod.Module.cai), "cai phải async def (lõi await)"
    # nap: hợp đồng lõi là ĐỒNG BỘ (app/so_module.py::_nap ném RuntimeError nếu
    # nap async mà thật sự await) — giữ def thường như khuôn van-phong.
    assert not inspect.iscoroutinefunction(mod.Module.nap), (
        "nap phải đồng bộ theo hợp đồng lõi (app/so_module.py::_nap)"
    )


# ── cai(): kiểm địa chỉ qua app/tu_xa.kiem_url_lan ────────────────────────


def test_cai_url_loopback_ok_khi_test_cho_phep(monkeypatch, the):
    async def _chay():
        return await the.cai({"dich_vu_url": URL_MAC_DINH, "khoa_dich_vu": KHOA_GIA})

    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    kq = asyncio.run(_chay())
    assert kq["ok"] is True
    assert kq["loi"] is None
    assert kq["cau_hinh_luu"]["dich_vu_url"] == URL_MAC_DINH
    assert kq["cau_hinh_luu"]["khoa_dich_vu"] == KHOA_GIA
    # Khoá che dạng …abcd trong ket_qua_tung_khoa; khoá đầy đủ KHÔNG lọt ra
    # phần chủ máy thấy (ket_qua_tung_khoa/loi). cau_hinh_luu chứa khoá đầy
    # đủ là đúng theo spec (lõi lưu riêng 0600 theo kieu bi_mat, không trả
    # lại qua API — xem app/so_module.py: cong_khai chỉ gồm ok/ket_qua/loi).
    che = kq["ket_qua_tung_khoa"][0]["khoa"]
    assert che == "…" + KHOA_GIA[-4:]
    hien = {"ket_qua_tung_khoa": kq["ket_qua_tung_khoa"], "loi": kq["loi"]}
    assert KHOA_GIA not in json.dumps(hien, ensure_ascii=False)


def test_cai_chan_ssrf_link_local(the):
    async def _chay():
        return await the.cai(
            {"dich_vu_url": "http://169.254.169.254/", "khoa_dich_vu": KHOA_GIA}
        )

    kq = asyncio.run(_chay())
    assert kq["ok"] is False
    assert "dia_chi_dich_vu_khong_hop_le" in kq["loi"]


def test_cai_chan_scheme_la(the):
    async def _chay():
        return await the.cai({"dich_vu_url": "ftp://x", "khoa_dich_vu": KHOA_GIA})

    kq = asyncio.run(_chay())
    assert kq["ok"] is False
    assert "dia_chi_dich_vu_khong_hop_le" in kq["loi"]


# ── suc_khoe(): hỏi /api/integration/health, lỗi không ném ────────────────


class _PhanHoiGia:
    def __init__(self, ma=200, than=None):
        self.status_code = ma
        self._than = {"success": True, "status": "ok"} if than is None else than

    def json(self):
        return self._than


def _client_gia(monkeypatch, mod, phan_hoi=None, loi=None):
    """Vá httpx.AsyncClient bằng máy khách giả ghi lại lời gọi."""
    goi = {}

    class _Client:
        def __init__(self, *a, **kw):
            goi["timeout"] = kw.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            goi["url"] = url
            goi["headers"] = headers
            if loi is not None:
                raise loi
            return phan_hoi

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Client)
    return goi


def test_suc_khoe_200_ok(monkeypatch, mod, the):
    goi = _client_gia(monkeypatch, mod, phan_hoi=_PhanHoiGia(200))
    the.nap({"dich_vu_url": URL_MAC_DINH, "khoa_dich_vu": KHOA_GIA})
    kq = asyncio.run(the.suc_khoe())
    assert kq["ok"] is True
    assert goi["url"] == URL_MAC_DINH + "/api/integration/health"
    assert goi["headers"]["Authorization"] == f"Bearer {KHOA_GIA}"
    assert float(goi["timeout"]) == 10.0
    assert KHOA_GIA not in json.dumps(kq, ensure_ascii=False)


def test_suc_khoe_loi_mang_khong_nem(monkeypatch, mod, the):
    import httpx

    _client_gia(monkeypatch, mod, loi=httpx.ConnectError("khong noi duoc"))
    the.nap({"dich_vu_url": URL_MAC_DINH, "khoa_dich_vu": KHOA_GIA})
    kq = asyncio.run(the.suc_khoe())  # phải trả dict, KHÔNG ném
    assert kq["ok"] is False
    assert kq["loi"].startswith("chưa kết nối được")
    assert KHOA_GIA not in kq["loi"]


def test_suc_khoe_401_khoa_sai(monkeypatch, mod, the):
    _client_gia(monkeypatch, mod, phan_hoi=_PhanHoiGia(401, {"success": False}))
    the.nap({"dich_vu_url": URL_MAC_DINH, "khoa_dich_vu": "khoa-sai"})
    kq = asyncio.run(the.suc_khoe())
    assert kq["ok"] is False
    assert kq["loi"].startswith("chưa kết nối được")


def test_dung_tra_none(the):
    assert asyncio.run(the.dung()) is None


# ── khoá không lọt log ────────────────────────────────────────────────────


def test_bo_loc_chan_dong_mang_bearer_va_khoa_da_nap(mod):
    loc = mod._ChanKhoaDichVu()

    def ban_ghi(noi_dung):
        return logging.LogRecord(
            "httpx", logging.WARNING, __file__, 1, noi_dung, (), None
        )

    # Chặn theo CẤU TRÚC: dòng mang «Authorization … Bearer» bị chặn ngay cả
    # khi khoá trong dòng chưa từng nạp.
    assert (
        loc.filter(ban_ghi("GET http://x Authorization: Bearer khoa-la-chua-nap"))
        is False
    )
    # Chặn theo giá trị: dòng chứa khoá đã nạp qua nap().
    mod._KHOA_DANG_DUNG.add(KHOA_GIA)
    try:
        assert loc.filter(ban_ghi(f"url chứa khoá trong query: ?k={KHOA_GIA}")) is False
        # Dòng sạch vẫn qua.
        assert (
            loc.filter(
                ban_ghi(
                    "HTTP Request: GET http://127.0.0.1:8713/api/integration/health"
                )
            )
            is True
        )
    finally:
        mod._KHOA_DANG_DUNG.discard(KHOA_GIA)


def test_khoa_khong_lot_log_qua_logger_httpx(mod, manifest):
    m = mod.Module(manifest)
    m.nap({"dich_vu_url": URL_MAC_DINH, "khoa_dich_vu": KHOA_GIA})
    thay = []

    class _Dem(logging.Handler):
        def emit(self, record):
            thay.append(record.getMessage())

    lg = logging.getLogger("httpx")
    h = _Dem()
    lg.addHandler(h)
    try:
        lg.warning("thử rò rỉ: Authorization: Bearer %s", KHOA_GIA)
        assert not any(KHOA_GIA in s for s in thay), "khoá lọt vào log httpx"
        lg.warning("dòng sạch không chứa bí mật")
        assert thay and "dòng sạch" in thay[-1]
    finally:
        lg.removeHandler(h)
        mod._KHOA_DANG_DUNG.discard(KHOA_GIA)


# ── độc lập lõi↔module ────────────────────────────────────────────────────


def test_module_chi_import_app_tu_xa():
    cay = ast.parse(MODULE_PY.read_text(encoding="utf-8"))
    app_imports = set()
    for nut in ast.walk(cay):
        if isinstance(nut, ast.Import):
            for ten in nut.names:
                if ten.name == "app" or ten.name.startswith("app."):
                    app_imports.add(ten.name)
        elif isinstance(nut, ast.ImportFrom):
            if (nut.module or "").startswith("app"):
                app_imports.add(nut.module)
    # Spec đợt B bắt dùng bộ kiểm chung app/tu_xa.kiem_url_lan, KHÔNG viết bộ
    # kiểm SSRF thứ hai — app.tu_xa là import app.* chính.
    # Vá bảo mật 2026-10-20 (siết lay_khoa): module được đọc thêm
    # app.ngu_canh — kênh ngữ cảnh gọi do lõi đóng dấu (SEC-002), CHỈ ĐỌC
    # danh tính người gọi, không gọi sâu vào lõi; thiếu ngữ cảnh thì đóng
    # (lay_khoa bị từ chối). Mọi import app.* khác ngoài 2 cái này vẫn bị cấm.
    assert app_imports <= {"app.tu_xa", "app.ngu_canh"}, (
        f"module import app.* ngoài app.tu_xa/app.ngu_canh: {app_imports}"
    )


def test_khong_ten_nha_cung_cap_trong_app_va_web():
    for goc, duoi in ((GOC / "app", (".py",)), (GOC / "web", (".py", ".js", ".html"))):
        for p in goc.rglob("*"):
            if not p.is_file() or p.suffix not in duoi:
                continue
            try:
                noi_dung = p.read_text(encoding="utf-8", errors="strict")
            except (UnicodeDecodeError, OSError):
                continue
            assert "youtube" not in noi_dung.lower(), (
                f"{p}: nhắc tới «youtube» — tên nhà cung cấp không được vào "
                "app/, web/ (doc_lap)"
            )


# ── thuc_the: mô tả, không liệt kê công cụ giả ────────────────────────────


def test_thuc_the_mo_ta_khong_cong_cu_gia(the):
    async def _chay():
        return await the.thuc_the(None)

    the.nap({"dich_vu_url": URL_MAC_DINH, "khoa_dich_vu": KHOA_GIA})
    ds = asyncio.run(_chay())
    assert isinstance(ds, list) and len(ds) == 1
    e = ds[0]
    assert e["id"] == "youtube_phat.dieu_khien"
    assert e["loai"] == "cong_cu"
    # Cho chủ máy thấy URL gốc để dán vào Home Assistant + khoá che …abcd.
    assert e["thuoc_tinh"]["dich_vu_url"] == URL_MAC_DINH
    assert e["thuoc_tinh"]["khoa"] == "…" + KHOA_GIA[-4:]
    assert KHOA_GIA not in json.dumps(ds, ensure_ascii=False)
    # KHÔNG liệt kê công cụ giả: tinh_nang rỗng, chỉ mô tả bằng chữ.
    # (Đợt C đổi có chủ ý: thực thể giờ khai 7 hành động THẬT gọi qua dịch vụ,
    #  không còn rỗng — cập nhật assert theo bài học NV98.)
    assert isinstance(e["tinh_nang"], list) and len(e["tinh_nang"]) == 7
    assert {t["hanh_dong"] for t in e["tinh_nang"]} == {
        "tim", "phat_len_loa", "dung_phien", "trang_thai",
        "danh_sach_playlist", "nhap_ma_chia_se", "lay_khoa"}
